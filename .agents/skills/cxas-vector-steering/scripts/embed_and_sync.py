# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Multi-environment embedding generator, GCS archiver, & Vertex AI index updater."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from config_loader import (  # noqa: E402
    DEFAULT_SAMPLE_CSV,
    EnvironmentSpec,
    IntentSpec,
    load_corpus_dataframe,
    load_intents_catalog,
    load_vector_steering_config,
    resolve_safe_path,
    validate_gcp_location,
    validate_gcp_project_id,
    validate_gcs_bucket,
)


def get_credentials(quota_project_id: str | None = None) -> Any:
    """Acquires Google Cloud Application Default Credentials."""
    import google.auth  # noqa: PLC0415
    import google.oauth2.credentials  # noqa: PLC0415
    from google.auth.transport.requests import Request  # noqa: PLC0415

    try:
        creds, _ = google.auth.default(
            quota_project_id=quota_project_id or None
        )
        if not creds.valid:
            creds.refresh(Request())
        return creds
    except Exception as exc:  # noqa: BLE001
        print(
            f"ADC warning ({exc}); falling back to gcloud print-access-token."
        )
        token = (
            subprocess.check_output(
                ["gcloud", "auth", "application-default", "print-access-token"]
            )
            .decode("utf-8")
            .strip()
        )
        return google.oauth2.credentials.Credentials(
            token, quota_project_id=quota_project_id or None
        )


def get_access_token(creds: Any) -> str:
    from google.auth.transport.requests import Request  # noqa: PLC0415

    if not creds.valid:
        creds.refresh(Request())
    if getattr(creds, "token", None):
        return str(creds.token)
    return (
        subprocess.check_output(
            ["gcloud", "auth", "application-default", "print-access-token"]
        )
        .decode("utf-8")
        .strip()
    )


def validate_embeddings_jsonl(jsonl_path: str) -> int:
    """Validates a Vertex AI Vector Search JSONL file and returns record count."""
    resolved = resolve_safe_path(jsonl_path)
    if not os.path.exists(resolved):
        raise FileNotFoundError(f"Embeddings JSONL file not found: {resolved}")

    count = 0
    expected_dim: int | None = None
    with open(resolved, encoding="utf-8") as handle:
        for line_num, raw_line in enumerate(handle, start=1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            record = json.loads(stripped)
            if (
                not isinstance(record, dict)
                or "id" not in record
                or "embedding" not in record
            ):
                raise ValueError(
                    f"Invalid vector record at {resolved}:{line_num}."
                )
            emb = record["embedding"]
            if not isinstance(emb, list) or not emb:
                raise ValueError(
                    f"Invalid 'embedding' array at {resolved}:{line_num}."
                )
            if expected_dim is None:
                expected_dim = len(emb)
            elif len(emb) != expected_dim:
                raise ValueError(
                    f"Dimension mismatch at {resolved}:{line_num}: "
                    f"expected {expected_dim}, got {len(emb)}."
                )
            count += 1

    if count == 0:
        raise ValueError(f"JSONL file {resolved} contains 0 vector records.")
    return count


def build_vector_record(
    *,
    row_index: int,
    intent: str,
    phrase: str,
    embedding_values: list[float],
    row_tenant: str | None = None,
    catalog_spec: IntentSpec | None = None,
) -> dict[str, Any]:
    """Constructs a Vertex AI Vector Search JSONL record with restricts."""
    digest = hashlib.sha256(
        f"{row_index}::{intent}::{phrase}".encode()
    ).hexdigest()[:16]
    item_id = f"{intent}:::{digest}"

    restricts: list[dict[str, Any]] = [
        {"namespace": "intent", "allow": [intent]}
    ]

    allowed_tenants: list[str] = []
    if row_tenant and str(row_tenant).strip():
        allowed_tenants = [
            t.strip().lower() for t in str(row_tenant).split("|") if t.strip()
        ]
    elif catalog_spec and catalog_spec.allowed_tenants:
        allowed_tenants = list(catalog_spec.allowed_tenants)

    if allowed_tenants:
        restricts.append({"namespace": "tenant", "allow": allowed_tenants})
    else:
        restricts.append({"namespace": "tenant", "allow": ["all"]})

    return {
        "id": item_id,
        "embedding": embedding_values,
        "restricts": restricts,
    }


def normalize_l2_vector(values: list[float]) -> list[float]:
    """L2-normalizes an embedding vector (||v||=1)."""
    norm_sq = sum(float(v) * float(v) for v in values)
    if norm_sq <= 0.0:
        return [float(v) for v in values]
    inv_norm = 1.0 / (norm_sq**0.5)
    return [float(v) * inv_norm for v in values]


def generate_embeddings_jsonl(
    *,
    csv_path: str,
    out_jsonl_path: str,
    project_id: str,
    location: str,
    creds: Any,
    model_name: str = "text-embedding-005",
    task_type: str = "SEMANTIC_SIMILARITY",
    output_dimensionality: int | None = None,
    normalize_l2: bool = True,
    batch_size: int = 200,
    catalog: dict[str, IntentSpec] | None = None,
) -> int:
    """Embeds all training phrases in batches via Vertex AI Embeddings."""
    import vertexai  # noqa: PLC0415
    from vertexai.language_models import (  # noqa: PLC0415
        TextEmbeddingInput,
        TextEmbeddingModel,
    )

    resolved_csv = resolve_safe_path(csv_path)
    resolved_out = resolve_safe_path(out_jsonl_path)
    os.makedirs(os.path.dirname(resolved_out), exist_ok=True)

    df = (
        load_corpus_dataframe(resolved_csv)
        .dropna(subset=["intent", "phrase"])
        .reset_index(drop=True)
    )
    phrases = df["phrase"].astype(str).tolist()
    intents = df["intent"].astype(str).str.strip().tolist()
    tenants = (
        df["tenant"].tolist()
        if "tenant" in df.columns
        else [None] * len(phrases)
    )

    vertexai.init(project=project_id, location=location, credentials=creds)
    model = TextEmbeddingModel.from_pretrained(model_name)

    print(
        f"Generating '{model_name}' ({task_type}) vectors for {len(phrases)} phrases..."
    )
    written = 0
    with open(resolved_out, "w", encoding="utf-8") as out_f:
        for start in range(0, len(phrases), batch_size):
            batch_phrases = phrases[start : start + batch_size]
            batch_intents = intents[start : start + batch_size]
            batch_tenants = tenants[start : start + batch_size]

            inputs = [
                TextEmbeddingInput(text, task_type) for text in batch_phrases
            ]
            embeddings = None
            for attempt in range(3):
                try:
                    if output_dimensionality:
                        try:
                            embeddings = model.get_embeddings(
                                inputs,
                                output_dimensionality=output_dimensionality,
                            )
                        except TypeError:
                            embeddings = model.get_embeddings(inputs)
                    else:
                        embeddings = model.get_embeddings(inputs)
                    break
                except Exception as exc:
                    if attempt < 2:
                        time.sleep(2 * (attempt + 1))
                    else:
                        raise RuntimeError(
                            f"Failed embedding generation at batch {start}: {exc}"
                        ) from exc

            for offset, emb in enumerate(embeddings or []):
                intent_id = batch_intents[offset]
                raw_tenant = batch_tenants[offset]
                tenant_str = (
                    None
                    if (raw_tenant is None or pd.isna(raw_tenant))
                    else str(raw_tenant)
                )
                vec_vals = list(emb.values)
                if (
                    output_dimensionality
                    and len(vec_vals) > output_dimensionality
                ):
                    vec_vals = vec_vals[:output_dimensionality]
                if normalize_l2:
                    vec_vals = normalize_l2_vector(vec_vals)
                record = build_vector_record(
                    row_index=start + offset,
                    intent=intent_id,
                    phrase=batch_phrases[offset],
                    embedding_values=vec_vals,
                    row_tenant=tenant_str,
                    catalog_spec=catalog.get(intent_id) if catalog else None,
                )
                out_f.write(json.dumps(record) + "\n")
                written += 1

    print(f"Successfully wrote {written} vector records to {resolved_out}")
    return written


def upload_gcs_resumable(
    bucket_name: str,
    object_name: str,
    local_file_path: str,
    token: str,
    content_type: str = "application/json",
) -> None:
    """Uploads a file to GCS via authenticated REST Resumable Upload."""
    file_size = os.path.getsize(local_file_path)
    init_url = (
        f"https://storage.googleapis.com/upload/storage/v1/b/{bucket_name}/o"
    )
    init_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": content_type,
        "X-Upload-Content-Length": str(file_size),
    }
    resp = requests.post(
        init_url,
        params={"uploadType": "resumable", "name": object_name},
        headers=init_headers,
        timeout=60,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"GCS resumable init failed ({resp.status_code}): {resp.text}"
        )

    upload_url = resp.headers.get("Location")
    if not upload_url:
        raise RuntimeError("GCS resumable response missing Location header.")

    with open(local_file_path, "rb") as handle:
        put_resp = requests.put(
            upload_url,
            headers={
                "Content-Length": str(file_size),
                "Content-Type": content_type,
            },
            data=handle,
            timeout=600,
        )
    if put_resp.status_code not in (200, 201):
        raise RuntimeError(
            f"GCS upload failed ({put_resp.status_code}): {put_resp.text}"
        )


def backup_gcs_active_embeddings(
    bucket_name: str, active_object_name: str, gcs_prefix: str, token: str
) -> None:
    """Copies the currently active GCS embeddings file to a timestamped backup."""
    headers = {"Authorization": f"Bearer {token}"}
    encoded_src = requests.utils.quote(active_object_name, safe="")
    check_url = f"https://storage.googleapis.com/storage/v1/b/{bucket_name}/o/{encoded_src}"
    check_resp = requests.get(check_url, headers=headers, timeout=30)
    if check_resp.status_code != 200:
        return

    ts = datetime.now(timezone.utc).strftime("%Y_%m_%d_%H%M%S")
    dst_name = f"{gcs_prefix}/backups/embeddings_{ts}.json"
    encoded_dst = requests.utils.quote(dst_name, safe="")
    copy_url = (
        f"https://storage.googleapis.com/storage/v1/b/{bucket_name}/o/{encoded_src}"
        f"/copyTo/b/{bucket_name}/o/{encoded_dst}"
    )
    requests.post(copy_url, headers=headers, timeout=60)


def clean_gcs_directory_marker(
    bucket_name: str, folder_marker: str, token: str
) -> None:
    """Removes 0-byte directory placeholder objects before index scan."""
    headers = {"Authorization": f"Bearer {token}"}
    encoded = requests.utils.quote(folder_marker, safe="")
    url = (
        f"https://storage.googleapis.com/storage/v1/b/{bucket_name}/o/{encoded}"
    )
    requests.delete(url, headers=headers, timeout=30)


def trigger_matching_engine_overwrite(
    *,
    project_id: str,
    location: str,
    index_resource_name: str,
    gcs_delta_uri: str,
    creds: Any,
) -> Any:
    """Triggers an asynchronous overwrite update on Vertex AI Vector Search."""
    from google.cloud import aiplatform  # noqa: PLC0415
    from google.cloud.aiplatform.compat.types import (  # noqa: PLC0415
        matching_engine_index as gca_matching_engine_index,
    )
    from google.protobuf import field_mask_pb2  # noqa: PLC0415

    aiplatform.init(project=project_id, location=location, credentials=creds)
    index = aiplatform.MatchingEngineIndex(index_name=index_resource_name)

    gapic_index = gca_matching_engine_index.Index(
        name=index.resource_name,
        metadata={
            "contentsDeltaUri": gcs_delta_uri,
            "isCompleteOverwrite": True,
        },
    )
    update_mask = field_mask_pb2.FieldMask(paths=["metadata"])
    return index.api_client.update_index(
        index=gapic_index, update_mask=update_mask
    )


def resolve_target_environments(
    args: argparse.Namespace, cfg: Any
) -> list[EnvironmentSpec]:
    if args.project or args.bucket or args.index_name:
        if not args.project or not args.bucket:
            raise ValueError(
                "When using CLI overrides, both --project and --bucket are required."
            )
        project_id = validate_gcp_project_id(args.project, "--project")
        location = validate_gcp_location(args.location, "--location")
        bucket = validate_gcs_bucket(args.bucket, "--bucket")
        quota_proj = (
            validate_gcp_project_id(args.quota_project, "--quota-project")
            if args.quota_project
            else project_id
        )
        return [
            EnvironmentSpec(
                key="custom",
                env_name="CUSTOM",
                project_id=project_id,
                quota_project_id=quota_proj,
                location=location,
                bucket_name=bucket,
                gcs_prefix=args.gcs_prefix.strip("/"),
                index_resource_name=args.index_name or "",
            )
        ]

    if cfg is None or not cfg.environments:
        raise ValueError(
            "Provide either --config <vector_steering.yaml> or --project / --bucket."
        )

    if args.env == "all":
        return list(cfg.environments.values())
    if args.env not in cfg.environments:
        raise ValueError(
            f"Environment '{args.env}' not found. "
            f"Available: {list(cfg.environments.keys())}"
        )
    return [cfg.environments[args.env]]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Vector Steering Vector Pipeline: Embed, backup, upload, and sync index."
    )
    parser.add_argument(
        "--config", type=str, default=None, help="Path to vector_steering.yaml"
    )
    parser.add_argument(
        "--env", type=str, default="dev", help="Target environment key or 'all'"
    )
    parser.add_argument(
        "--csv", type=str, default=None, help="Override path to training CSV"
    )
    parser.add_argument(
        "--catalog",
        type=str,
        default=None,
        help="Override path to intents_catalog.yaml",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Override path to embeddings JSONL",
    )
    parser.add_argument("--project", type=str, default=None)
    parser.add_argument("--quota-project", type=str, default=None)
    parser.add_argument("--location", type=str, default="us-central1")
    parser.add_argument("--bucket", type=str, default=None)
    parser.add_argument("--gcs-prefix", type=str, default="vector_steering")
    parser.add_argument("--index-name", type=str, default=None)
    parser.add_argument("--skip-upload", action="store_true")
    parser.add_argument("--upload-only", action="store_true")
    parser.add_argument("--upload-and-trigger", action="store_true")
    parser.add_argument("--trigger-only", action="store_true")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    cfg = load_vector_steering_config(args.config) if args.config else None

    csv_path = args.csv or (cfg.corpus_csv if cfg else DEFAULT_SAMPLE_CSV)
    jsonl_path = args.out or (
        cfg.embeddings_jsonl
        if cfg
        else "vector_steering/symmetric_embeddings.json"
    )
    catalog_path = args.catalog or (cfg.intents_catalog_path if cfg else None)
    catalog = (
        load_intents_catalog(catalog_path)
        if (catalog_path and os.path.exists(catalog_path))
        else None
    )

    skip_embedding = (
        args.upload_only or args.upload_and_trigger or args.trigger_only
    )
    require_index = not (args.skip_upload or args.upload_only)

    targets: list[EnvironmentSpec] = []
    if not args.skip_upload:
        targets = resolve_target_environments(args, cfg)
        for t in targets:
            t.validate_for_cloud_ops(require_index=require_index)

    if args.dry_run:
        print("[DRY-RUN] Vector Steering Execution Plan:")
        print(f"  Skip Embedding Generation : {skip_embedding}")
        print(f"  Corpus CSV                : {csv_path}")
        print(f"  Embeddings JSONL          : {jsonl_path}")
        for t in targets:
            print(
                f"  Target [{t.env_name}] -> project={t.project_id}, "
                f"bucket=gs://{t.bucket_name}/{t.gcs_prefix}/embeddings/"
            )
        return 0

    quota_project = (
        targets[0].quota_project_id
        if targets
        else (args.quota_project or args.project)
    )
    creds = get_credentials(quota_project)

    if not skip_embedding:
        emb_project = (
            targets[0].project_id
            if targets
            else validate_gcp_project_id(args.project or "", "--project")
        )
        emb_location = (
            targets[0].location
            if targets
            else validate_gcp_location(args.location, "--location")
        )
        generate_embeddings_jsonl(
            csv_path=csv_path,
            out_jsonl_path=jsonl_path,
            project_id=emb_project,
            location=emb_location,
            creds=creds,
            model_name=cfg.embedding_model if cfg else "text-embedding-005",
            task_type=cfg.embedding_task_type if cfg else "SEMANTIC_SIMILARITY",
            output_dimensionality=cfg.embedding_dimensions if cfg else None,
            normalize_l2=cfg.embedding_normalize_l2 if cfg else True,
            batch_size=cfg.embedding_batch_size if cfg else 200,
            catalog=catalog,
        )

    if not args.trigger_only:
        rec_count = validate_embeddings_jsonl(jsonl_path)
        print(f"Validated {rec_count} vector records in {jsonl_path}.")

    if args.skip_upload:
        return 0

    token = get_access_token(creds)
    operations: list[dict[str, Any]] = []

    for target in targets:
        active_folder = f"{target.gcs_prefix}/embeddings"
        active_obj = f"{active_folder}/{os.path.basename(jsonl_path)}"
        gcs_delta_uri = f"gs://{target.bucket_name}/{active_folder}/"

        if not args.trigger_only:
            backup_gcs_active_embeddings(
                target.bucket_name, active_obj, target.gcs_prefix, token
            )
            if os.path.exists(csv_path):
                ts = datetime.now(timezone.utc).strftime("%Y_%m_%d_%H%M%S")
                corpus_backup_obj = (
                    f"{target.gcs_prefix}/backups/corpus_{ts}.csv"
                )
                upload_gcs_resumable(
                    target.bucket_name,
                    corpus_backup_obj,
                    csv_path,
                    token,
                    content_type="text/csv",
                )

            clean_gcs_directory_marker(
                target.bucket_name, f"{active_folder}/", token
            )
            upload_gcs_resumable(
                target.bucket_name, active_obj, jsonl_path, token
            )

        if args.upload_only:
            operations.append(
                {"env": target.env_name, "status": "UPLOADED_ONLY", "lro": None}
            )
            continue

        lro = trigger_matching_engine_overwrite(
            project_id=target.project_id,
            location=target.location,
            index_resource_name=target.resolved_index_name,
            gcs_delta_uri=gcs_delta_uri,
            creds=creds,
        )
        operations.append(
            {"env": target.env_name, "status": lro.operation.name, "lro": lro}
        )

    if args.wait:
        for item in operations:
            if item["lro"] is not None:
                item["lro"].result()

    return 0


if __name__ == "__main__":
    sys.exit(main())
