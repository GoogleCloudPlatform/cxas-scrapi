# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Bootstrap Vector Steering configuration and starter corpus from a CXAS SCRAPI workspace.

Inspects `gecx-config.json`, pulled local CXAS agent directories (`app.json`,
`agents/*/*.json`), and evaluation files (`evals/`, `evaluations/`) to generate
`vector_steering/vector_steering.yaml`, `vector_steering/intents_catalog.yaml`, and a PII-scrubbed
`vector_steering/training_phrases.csv` starter dataset.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from config_loader import (  # noqa: E402
    DEFAULT_EXAMPLE_CATALOG,
    DEFAULT_SAMPLE_CSV,
    discover_gecx_workspace_config,
    resolve_safe_path,
)

EMAIL_SCRUB_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)
SSN_SCRUB_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
LONG_ID_SCRUB_RE = re.compile(r"\b(?:\d[ -]?){7,19}\b")
NON_IDENT_RE = re.compile(r"[^a-z0-9_]+")


def sanitize_intent_key(raw_name: str) -> str:
    cleaned = NON_IDENT_RE.sub("_", raw_name.strip().lower()).strip("_")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"intent_{cleaned}" if cleaned else "general_inquiry"
    return cleaned[:64]


def scrub_utterance_pii(text: str) -> str:
    cleaned = SSN_SCRUB_RE.sub("[ID]", text)
    cleaned = EMAIL_SCRUB_RE.sub("[EMAIL]", cleaned)
    cleaned = LONG_ID_SCRUB_RE.sub("[NUMBER]", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def harvest_scrapi_app(
    app_dir: Path,
) -> tuple[dict[str, dict[str, Any]], list[tuple[str, str]]]:
    """Extracts specialist sub-agent intents from a pulled CXAS app directory."""
    intents: dict[str, dict[str, Any]] = {}
    phrases: list[tuple[str, str]] = []

    app_json_path = app_dir / "app.json"
    root_agent_name = ""
    if app_json_path.is_file():
        app_data = json.loads(app_json_path.read_text(encoding="utf-8"))
        root_agent_name = str(app_data.get("rootAgent", "")).strip()

    agents_dir = app_dir / "agents"
    if agents_dir.is_dir():
        for agent_folder in sorted(agents_dir.iterdir()):
            if not agent_folder.is_dir():
                continue
            json_file = agent_folder / f"{agent_folder.name}.json"
            if not json_file.is_file():
                continue
            agent_meta = json.loads(json_file.read_text(encoding="utf-8"))
            display_name = str(
                agent_meta.get("displayName") or agent_folder.name
            ).strip()
            if display_name == root_agent_name:
                continue
            intent_id = sanitize_intent_key(display_name)
            desc = str(agent_meta.get("description") or "").strip()
            if not desc:
                desc = (
                    f"Handles {display_name.replace('_', ' ')} customer "
                    "requests and specialist fulfillment."
                )
            intents[intent_id] = {"description": desc}

            # Generate 5 clean starter phrases from description/display name
            readable = display_name.replace("_", " ").lower()
            seed_templates = [
                f"I need help with {readable} right now",
                f"Can you assist me with my {readable} request",
                f"Check the current status of {readable} on my account",
                f"Please connect me to handle {readable} today",
                f"I have a question regarding {readable}",
            ]
            for seed in seed_templates:
                phrases.append((intent_id, scrub_utterance_pii(seed)))

    return intents, phrases


def bootstrap_workspace(
    *,
    workspace_root: str = ".",
    app_dir: str | None = None,
    output_dir: str = "vector_steering",
    vector_location: str = "us-central1",
) -> dict[str, str]:
    """Creates `vector_steering.yaml`, `intents_catalog.yaml`, and `training_phrases.csv`."""
    ws_cfg = discover_gecx_workspace_config(workspace_root)
    resolved_out = Path(resolve_safe_path(output_dir, base_dir=workspace_root))
    resolved_out.mkdir(parents=True, exist_ok=True)

    gcp_project = str(ws_cfg.get("gcp_project_id") or "cymbal-gcp-dev").strip()
    if not re.match(r"^[a-z][a-z0-9-]{4,29}$", gcp_project):
        gcp_project = "cymbal-gcp-dev"

    app_name_raw = str(ws_cfg.get("app_name") or "cymbal-assistant")
    project_slug = (
        re.sub(r"[^a-z0-9-]+", "-", app_name_raw.lower()).strip("-")
        or "cymbal-assistant"
    )

    candidate_app_dir: Path | None = None
    if app_dir:
        candidate_app_dir = Path(
            resolve_safe_path(app_dir, base_dir=workspace_root)
        )
    elif ws_cfg.get("app_dir"):
        candidate_app_dir = Path(
            resolve_safe_path(str(ws_cfg["app_dir"]), base_dir=workspace_root)
        )

    catalog_path = resolved_out / "intents_catalog.yaml"
    csv_path = resolved_out / "training_phrases.csv"
    config_path = resolved_out / "vector_steering.yaml"

    harvested_intents: dict[str, dict[str, Any]] = {}
    harvested_phrases: list[tuple[str, str]] = []
    if candidate_app_dir and candidate_app_dir.is_dir():
        harvested_intents, harvested_phrases = harvest_scrapi_app(
            candidate_app_dir
        )

    if harvested_intents:
        catalog_obj = {"version": "1.0", "intents": harvested_intents}
        catalog_path.write_text(
            yaml.safe_dump(catalog_obj, sort_keys=False), encoding="utf-8"
        )
        with open(csv_path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["intent", "phrase"])
            for intent_id, phrase in harvested_phrases:
                writer.writerow([intent_id, phrase])
    else:
        catalog_path.write_text(
            Path(DEFAULT_EXAMPLE_CATALOG).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        csv_path.write_text(
            Path(DEFAULT_SAMPLE_CSV).read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    cfg_obj: dict[str, Any] = {
        "version": "1.0",
        "project": {
            "name": project_slug,
            "description": "Vector Steering Two-Tower Vector Intent Router for CXAS SCRAPI",
        },
        "embedding": {
            "model": "text-embedding-005",
            "dimensions": 768,
            "task_type": "SEMANTIC_SIMILARITY",
            "normalize_l2": True,
            "batch_size": 200,
        },
        "thresholds": {
            "similarity_threshold": 0.75,
            "disambiguation_delta": 0.08,
            "secondary_match_floor_offset": 0.15,
            "single_mode_min_confidence": 0.60,
            "top_k_neighbors": 5,
            "max_candidates": 3,
        },
        "dataset": {
            "corpus_csv": str(csv_path),
            "intents_catalog": str(catalog_path),
            "embeddings_jsonl": str(resolved_out / "symmetric_embeddings.json"),
            "min_word_count": 2,
            "min_phrases_per_intent": 5,
            "entity_mask_patterns": [],
        },
        "agent_integration": {
            "intent_variable": "Intent",
            "tenant_variable": "tenant_id",
            "toolset_name": "vector_intent_router",
            "operation_id": "classify_intent",
            "classifier_tool_name": "intent_classifier",
            "update_intent_tool_name": "update_intent",
            "agent_transfer_flag_variable": "isAgentTransfer",
            "agent_transfer_reason_variable": "agentTransferReason",
        },
        "environments": {
            "dev": {
                "env_name": "DEV",
                "project_id": gcp_project,
                "quota_project_id": gcp_project,
                "location": vector_location,
                "bucket_name": f"{gcp_project}-vector-steering",
                "gcs_prefix": "vector_steering",
                "index_display_name": f"{project_slug}-index-dev",
                "endpoint_display_name": f"{project_slug}-endpoint-dev",
                "deployed_index_id": "vector_steering_index_deployed",
                "index_algorithm": "BRUTE_FORCE",
                "distance_measure_type": "COSINE_DISTANCE",
                "index_update_method": "BATCH_UPDATE",
                "shard_size": "SHARD_SIZE_SMALL",
                "machine_type": "e2-standard-2",
                "min_replicas": 1,
                "max_replicas": 1,
                "cloud_function_name": "vector-steering-classifier",
                "cloud_function_url": "$env_var",
                "ingress_settings": "all",
            }
        },
    }
    config_path.write_text(
        yaml.safe_dump(cfg_obj, sort_keys=False), encoding="utf-8"
    )

    return {
        "config": str(config_path),
        "catalog": str(catalog_path),
        "corpus_csv": str(csv_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap Vector Steering config and corpus from a CXAS SCRAPI workspace."
    )
    parser.add_argument(
        "--workspace-root",
        type=str,
        default=".",
        help="Path to SCRAPI workspace root containing gecx-config.json",
    )
    parser.add_argument(
        "--app-dir",
        type=str,
        default=None,
        help="Optional pulled CXAS app directory to harvest sub-agents from",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="vector_steering",
        help="Directory to write vector_steering.yaml, catalog, and CSV",
    )
    parser.add_argument(
        "--location",
        type=str,
        default="us-central1",
        help="GCP region for Vertex AI Vector Search (default: us-central1)",
    )
    args = parser.parse_args()

    outputs = bootstrap_workspace(
        workspace_root=args.workspace_root,
        app_dir=args.app_dir,
        output_dir=args.output_dir,
        vector_location=args.location,
    )
    print("Bootstrapped Vector Steering SCRAPI configuration:")
    for key, val in outputs.items():
        print(f"  - {key:12s}: {val}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
