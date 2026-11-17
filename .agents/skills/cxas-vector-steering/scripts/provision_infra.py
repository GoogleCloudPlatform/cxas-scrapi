# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Turnkey GCP Infrastructure Provisioner for Vector Steering."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from config_loader import (  # noqa: E402
    RESOURCES_DIR,
    EnvironmentSpec,
    advise_vector_configuration,
    load_intents_catalog,
    load_vector_steering_config,
)
from embed_and_sync import (  # noqa: E402
    generate_embeddings_jsonl,
    get_access_token,
    get_credentials,
    upload_gcs_resumable,
    validate_embeddings_jsonl,
)

REQUIRED_APIS = [
    "aiplatform.googleapis.com",
    "cloudfunctions.googleapis.com",
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "storage.googleapis.com",
]


def run_cmd(cmd: list[str], *, dry_run: bool = False) -> None:
    """Executes a validated CLI command array safely with shell=False."""
    printable = " ".join(cmd)
    if dry_run:
        print(f"  [DRY-RUN] {printable}")
        return
    print(f"  [EXEC] {printable}")
    subprocess.run(cmd, check=True)


def ensure_bucket(env: EnvironmentSpec, *, dry_run: bool = False) -> None:
    bucket_uri = f"gs://{env.bucket_name}"
    if dry_run:
        print(
            f"  [DRY-RUN] Ensure hardened GCS bucket {bucket_uri} in {env.location}"
        )
        return

    probe = subprocess.run(
        [
            "gcloud",
            "storage",
            "buckets",
            "describe",
            bucket_uri,
            "--project",
            env.project_id,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if probe.returncode == 0:
        print(f"  GCS bucket {bucket_uri} already exists.")
        return

    run_cmd(
        [
            "gcloud",
            "storage",
            "buckets",
            "create",
            bucket_uri,
            f"--location={env.location}",
            f"--project={env.project_id}",
            "--uniform-bucket-level-access",
            "--public-access-prevention",
        ],
        dry_run=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Provision end-to-end Vector Steering infrastructure on Google Cloud."
    )
    parser.add_argument(
        "--config", type=str, required=True, help="Path to vector_steering.yaml"
    )
    parser.add_argument(
        "--env",
        type=str,
        default="dev",
        help="Environment key in vector_steering.yaml (default: dev)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print provisioning steps without creating resources",
    )
    parser.add_argument(
        "--skip-function",
        action="store_true",
        help="Only provision GCS and Vertex AI Vector Search",
    )
    args = parser.parse_args()

    cfg = load_vector_steering_config(args.config)
    if args.env not in cfg.environments:
        raise ValueError(
            f"Environment '{args.env}' not found in {args.config}."
        )
    env = cfg.environments[args.env]
    env.validate_for_cloud_ops(require_index=False)

    print(
        f"=== Vector Steering Provisioning Plan: {env.env_name} "
        f"({env.project_id} / {env.location}) ==="
    )

    corpus_rows = None
    if cfg.corpus_csv and os.path.exists(cfg.corpus_csv):
        with open(cfg.corpus_csv, encoding="utf-8") as csv_f:
            corpus_rows = max(0, sum(1 for _ in csv_f) - 1)
    nudges = advise_vector_configuration(cfg, args.env, corpus_size=corpus_rows)
    if nudges:
        print("\nProactive Vector DB & Embedding Optimization Nudges:")
        for n in nudges:
            print(f"  * {n}")

    print("\nStep 1: Enabling required Google Cloud APIs...")
    run_cmd(
        [
            "gcloud",
            "services",
            "enable",
            *REQUIRED_APIS,
            f"--project={env.project_id}",
        ],
        dry_run=args.dry_run,
    )

    print("\nStep 2: Provisioning GCS storage bucket...")
    ensure_bucket(env, dry_run=args.dry_run)

    active_folder = f"{env.gcs_prefix}/embeddings"
    gcs_delta_uri = f"gs://{env.bucket_name}/{active_folder}/"
    active_obj = f"{active_folder}/{os.path.basename(cfg.embeddings_jsonl)}"

    print("\nStep 3: Preparing and uploading vector embeddings...")
    if args.dry_run:
        print(
            f"  [DRY-RUN] Embed {cfg.corpus_csv} -> {cfg.embeddings_jsonl} -> "
            f"gs://{env.bucket_name}/{active_obj}"
        )
    else:
        creds = get_credentials(env.quota_project_id or env.project_id)
        catalog = (
            load_intents_catalog(cfg.intents_catalog_path)
            if os.path.exists(cfg.intents_catalog_path)
            else None
        )
        if not os.path.exists(cfg.embeddings_jsonl):
            generate_embeddings_jsonl(
                csv_path=cfg.corpus_csv,
                out_jsonl_path=cfg.embeddings_jsonl,
                project_id=env.project_id,
                location=env.location,
                creds=creds,
                model_name=cfg.embedding_model,
                task_type=cfg.embedding_task_type,
                output_dimensionality=cfg.embedding_dimensions,
                normalize_l2=cfg.embedding_normalize_l2,
                batch_size=cfg.embedding_batch_size,
                catalog=catalog,
            )
        validate_embeddings_jsonl(cfg.embeddings_jsonl)
        token = get_access_token(creds)
        upload_gcs_resumable(
            env.bucket_name, active_obj, cfg.embeddings_jsonl, token
        )

    print("\nStep 4: Provisioning Vertex AI Vector Search Index & Endpoint...")
    if args.dry_run:
        print(
            f"  [DRY-RUN] Create {env.index_algorithm} Index "
            f"'{env.index_display_name}' from {gcs_delta_uri}"
        )
        print(
            f"  [DRY-RUN] Create IndexEndpoint '{env.endpoint_display_name}' "
            f"and deploy '{env.deployed_index_id}'"
        )
        endpoint_resource_name = "<GENERATED_INDEX_ENDPOINT_RESOURCE_NAME>"
        index_resource_name = "<GENERATED_INDEX_RESOURCE_NAME>"
    else:
        from google.cloud import aiplatform  # noqa: PLC0415

        creds = get_credentials(env.quota_project_id or env.project_id)
        aiplatform.init(
            project=env.project_id, location=env.location, credentials=creds
        )

        if env.resolved_index_name:
            index = aiplatform.MatchingEngineIndex(
                index_name=env.resolved_index_name
            )
        elif env.index_algorithm == "BRUTE_FORCE":
            index = aiplatform.MatchingEngineIndex.create_brute_force_index(
                display_name=env.index_display_name,
                contents_delta_uri=gcs_delta_uri,
                dimensions=cfg.embedding_dimensions,
                shard_size=env.shard_size,
                distance_measure_type=env.distance_measure_type,
                index_update_method=env.index_update_method,
                description=f"Vector Steering Exact Intent Index ({env.env_name})",
            )
        else:
            index = aiplatform.MatchingEngineIndex.create_tree_ah_index(
                display_name=env.index_display_name,
                contents_delta_uri=gcs_delta_uri,
                dimensions=cfg.embedding_dimensions,
                approximate_neighbors_count=env.approximate_neighbors_count,
                leaf_node_embedding_count=env.leaf_node_embedding_count,
                leaf_nodes_to_search_percent=env.leaf_nodes_to_search_percent,
                shard_size=env.shard_size,
                distance_measure_type=env.distance_measure_type,
                index_update_method=env.index_update_method,
                description=f"Vector Steering Tree-AH Intent Index ({env.env_name})",
            )
        index_resource_name = index.resource_name

        if env.index_endpoint_id:
            endpoint = aiplatform.MatchingEngineIndexEndpoint(
                env.index_endpoint_id
            )
        else:
            endpoint = aiplatform.MatchingEngineIndexEndpoint.create(
                display_name=env.endpoint_display_name,
                public_endpoint_enabled=True,
            )

        already_deployed = any(
            d.id == env.deployed_index_id
            for d in (endpoint.deployed_indexes or [])
        )
        if not already_deployed:
            endpoint.deploy_index(
                index=index,
                deployed_index_id=env.deployed_index_id,
                display_name=env.deployed_index_id,
                machine_type=env.machine_type,
                min_replica_count=env.min_replicas,
                max_replica_count=env.max_replicas,
            )
        endpoint_resource_name = endpoint.resource_name

    if not args.skip_function:
        print(
            "\nStep 5: Deploying authenticated Gen2 Cloud Function middleware..."
        )
        cf_source = str(RESOURCES_DIR / "cloud_function")
        env_vars = (
            f"PROJECT_ID={env.project_id},"
            f"LOCATION={env.location},"
            f"INDEX_ENDPOINT_ID={endpoint_resource_name},"
            f"DEPLOYED_INDEX_ID={env.deployed_index_id},"
            f"EMBEDDING_MODEL={cfg.embedding_model},"
            f"EMBEDDING_TASK_TYPE={cfg.embedding_task_type},"
            f"OUTPUT_DIMENSIONALITY={cfg.embedding_dimensions},"
            f"NORMALIZE_L2={str(cfg.embedding_normalize_l2).lower()},"
            f"DISTANCE_MEASURE_TYPE={env.distance_measure_type},"
            f"SIMILARITY_THRESHOLD={cfg.similarity_threshold},"
            f"DISAMBIGUATION_DELTA={cfg.disambiguation_delta},"
            f"SECONDARY_MATCH_FLOOR_OFFSET={cfg.secondary_match_floor_offset},"
            f"NUM_NEIGHBORS={cfg.top_k_neighbors},"
            f"MAX_TOP_MATCHES={cfg.max_candidates}"
        )
        deploy_cmd = [
            "gcloud",
            "functions",
            "deploy",
            env.cloud_function_name,
            "--gen2",
            "--runtime=python311",
            "--trigger-http",
            f"--source={cf_source}",
            "--entry-point=classify_intent",
            f"--region={env.location}",
            f"--project={env.project_id}",
            f"--set-env-vars={env_vars}",
            f"--ingress-settings={env.ingress_settings}",
            "--no-allow-unauthenticated",
        ]
        if env.service_account:
            deploy_cmd.append(f"--service-account={env.service_account}")
        run_cmd(deploy_cmd, dry_run=args.dry_run)

        if env.agent_invoker_service_account:
            run_cmd(
                [
                    "gcloud",
                    "run",
                    "services",
                    "add-iam-policy-binding",
                    env.cloud_function_name,
                    f"--region={env.location}",
                    f"--project={env.project_id}",
                    f"--member=serviceAccount:{env.agent_invoker_service_account}",
                    "--role=roles/run.invoker",
                ],
                dry_run=args.dry_run,
            )

    print("\n=== Vector Steering Provisioning Summary ===")
    print(f"  Environment         : {env.env_name}")
    print(f"  Index Resource Name : {index_resource_name}")
    print(f"  Index Endpoint      : {endpoint_resource_name}")
    print(f"  Deployed Index ID   : {env.deployed_index_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
