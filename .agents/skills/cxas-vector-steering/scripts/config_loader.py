# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Declarative YAML configuration loader, SCRAPI workspace locator, and validator."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

SKILL_ROOT = Path(__file__).resolve().parent.parent
RESOURCES_DIR = SKILL_ROOT / "resources"
DEFAULT_SAMPLE_CSV = str(
    RESOURCES_DIR / "sample_data" / "sample_training_phrases.csv"
)
DEFAULT_EXAMPLE_CATALOG = str(
    RESOURCES_DIR / "config" / "intents_catalog.example.yaml"
)
DEFAULT_EXAMPLE_CONFIG = str(
    RESOURCES_DIR / "config" / "vector_steering.example.yaml"
)

GCP_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,29}$")
GCP_LOCATION_RE = re.compile(r"^[a-z]+-[a-z]+[0-9]+$")
GCS_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$")
RESOURCE_ID_RE = re.compile(r"^[a-zA-Z0-9_./:-]{1,256}$")
SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]{0,127}$")
SERVICE_ACCOUNT_RE = re.compile(
    r"^[a-zA-Z0-9._-]+@[a-zA-Z0-9.-]+\.iam\.gserviceaccount\.com$"
)


class ConfigValidationError(ValueError):
    """Raised when a Vector Steering configuration violates schema or security rules."""


def validate_gcp_project_id(
    project_id: str, field_name: str = "project_id"
) -> str:
    value = (project_id or "").strip()
    if not GCP_PROJECT_RE.match(value):
        raise ConfigValidationError(
            f"Invalid {field_name} '{value}'. Expected lowercase GCP project ID "
            "(6-30 chars, letters/digits/hyphens)."
        )
    return value


def validate_gcp_location(location: str, field_name: str = "location") -> str:
    value = (location or "").strip()
    if not GCP_LOCATION_RE.match(value):
        raise ConfigValidationError(
            f"Invalid {field_name} '{value}'. Expected GCP region such as "
            "'us-central1' or 'europe-west1'."
        )
    return value


def validate_gcs_bucket(
    bucket_name: str, field_name: str = "bucket_name"
) -> str:
    value = (bucket_name or "").strip()
    if value.startswith("gs://"):
        value = value[5:].rstrip("/")
    if not GCS_BUCKET_RE.match(value) or ".." in value:
        raise ConfigValidationError(f"Invalid {field_name} '{bucket_name}'.")
    return value


def validate_safe_identifier(
    identifier: str, field_name: str = "identifier"
) -> str:
    value = (identifier or "").strip()
    if not SAFE_IDENTIFIER_RE.match(value):
        raise ConfigValidationError(
            f"Invalid {field_name} '{value}'. Only alphanumeric characters, "
            "underscores, and hyphens are permitted."
        )
    return value


def validate_service_account(
    sa_email: str, field_name: str = "service_account"
) -> str:
    value = (sa_email or "").strip()
    if not value:
        return ""
    if not SERVICE_ACCOUNT_RE.match(value):
        raise ConfigValidationError(
            f"Invalid {field_name} '{value}'. Expected "
            "'<name>@<project>.iam.gserviceaccount.com'."
        )
    return value


def resolve_safe_path(path_str: str, base_dir: str | None = None) -> str:
    """Resolves a local file path safely without null bytes."""
    raw = str(path_str) if path_str is not None else ""
    if not raw or "\x00" in raw:
        raise ConfigValidationError("Invalid or empty file path.")
    expanded = os.path.expanduser(raw.strip())
    if os.path.isabs(expanded):
        return os.path.realpath(expanded)
    root = os.path.realpath(base_dir or os.getcwd())
    return os.path.realpath(os.path.join(root, expanded))


def discover_gecx_workspace_config(
    workspace_root: str | None = None,
) -> dict[str, Any]:
    """Discovers SCRAPI `gecx-config.json` (and `.active-project`) if present."""
    root = Path(resolve_safe_path(workspace_root or "."))
    candidates: list[Path] = []

    active_file = root / ".active-project"
    if active_file.is_file():
        active_rel = active_file.read_text(encoding="utf-8").strip()
        if active_rel:
            candidates.append(root / active_rel / "gecx-config.json")

    candidates.append(root / "gecx-config.json")
    for candidate in candidates:
        if candidate.is_file():
            with open(candidate, encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                loaded["_config_path"] = str(candidate)
                loaded["_project_dir"] = str(candidate.parent)
                return loaded
    return {}


@dataclass
class IntentSpec:
    intent_id: str
    description: str
    allowed_tenants: list[str] = field(default_factory=list)
    excluded_tenants: list[str] = field(default_factory=list)
    suppress_in_agents: list[str] = field(default_factory=list)
    vector_indexed: bool = True


ALLOWED_INDEX_ALGORITHMS = {"TREE_AH", "BRUTE_FORCE"}
ALLOWED_DISTANCE_MEASURES = {
    "COSINE_DISTANCE",
    "DOT_PRODUCT_DISTANCE",
    "SQUARED_L2_DISTANCE",
}
ALLOWED_UPDATE_METHODS = {"BATCH_UPDATE", "STREAM_UPDATE"}


@dataclass
class EnvironmentSpec:
    key: str
    env_name: str
    project_id: str
    quota_project_id: str
    location: str
    bucket_name: str
    gcs_prefix: str = "vector_steering"
    index_display_name: str = "vector-steering-intent-index"
    index_id: str = ""
    index_resource_name: str = ""
    endpoint_display_name: str = "vector-steering-intent-endpoint"
    index_endpoint_id: str = ""
    deployed_index_id: str = "vector_steering_index_deployed"
    index_algorithm: str = "BRUTE_FORCE"
    distance_measure_type: str = "COSINE_DISTANCE"
    index_update_method: str = "BATCH_UPDATE"
    approximate_neighbors_count: int = 150
    leaf_node_embedding_count: int = 500
    leaf_nodes_to_search_percent: int = 15
    shard_size: str = "SHARD_SIZE_SMALL"
    machine_type: str = "e2-standard-2"
    min_replicas: int = 1
    max_replicas: int = 1
    cloud_function_name: str = "vector-steering-classifier"
    cloud_function_url: str = "$env_var"
    ingress_settings: str = "all"
    service_account: str = ""
    agent_invoker_service_account: str = ""

    def validate_for_cloud_ops(self, require_index: bool = False) -> None:
        validate_gcp_project_id(
            self.project_id, f"environments.{self.key}.project_id"
        )
        if self.quota_project_id:
            validate_gcp_project_id(
                self.quota_project_id,
                f"environments.{self.key}.quota_project_id",
            )
        validate_gcp_location(
            self.location, f"environments.{self.key}.location"
        )
        validate_gcs_bucket(
            self.bucket_name, f"environments.{self.key}.bucket_name"
        )
        if self.index_algorithm not in ALLOWED_INDEX_ALGORITHMS:
            raise ConfigValidationError(
                f"Invalid index_algorithm '{self.index_algorithm}'. "
                f"Expected one of {sorted(ALLOWED_INDEX_ALGORITHMS)}."
            )
        if self.distance_measure_type not in ALLOWED_DISTANCE_MEASURES:
            raise ConfigValidationError(
                f"Invalid distance_measure_type '{self.distance_measure_type}'. "
                f"Expected one of {sorted(ALLOWED_DISTANCE_MEASURES)}."
            )
        if self.index_update_method not in ALLOWED_UPDATE_METHODS:
            raise ConfigValidationError(
                f"Invalid index_update_method '{self.index_update_method}'. "
                f"Expected one of {sorted(ALLOWED_UPDATE_METHODS)}."
            )
        if self.service_account:
            validate_service_account(
                self.service_account, f"environments.{self.key}.service_account"
            )
        if self.agent_invoker_service_account:
            validate_service_account(
                self.agent_invoker_service_account,
                f"environments.{self.key}.agent_invoker_service_account",
            )
        if require_index and not (self.index_resource_name or self.index_id):
            raise ConfigValidationError(
                f"Environment '{self.key}' requires 'index_resource_name' "
                "or 'index_id' for index updates."
            )

    @property
    def resolved_index_name(self) -> str:
        if self.index_resource_name:
            if not RESOURCE_ID_RE.match(self.index_resource_name):
                raise ConfigValidationError(
                    f"Invalid index_resource_name: {self.index_resource_name}"
                )
            return self.index_resource_name
        if self.index_id:
            if not RESOURCE_ID_RE.match(self.index_id):
                raise ConfigValidationError(
                    f"Invalid index_id: {self.index_id}"
                )
            if self.index_id.startswith("projects/"):
                return self.index_id
            return (
                f"projects/{self.project_id}/locations/"
                f"{self.location}/indexes/{self.index_id}"
            )
        return ""


@dataclass
class VectorSteeringConfig:
    project_name: str
    embedding_model: str
    embedding_dimensions: int
    embedding_task_type: str
    embedding_normalize_l2: bool
    embedding_batch_size: int
    similarity_threshold: float
    disambiguation_delta: float
    secondary_match_floor_offset: float
    single_mode_min_confidence: float
    top_k_neighbors: int
    max_candidates: int
    corpus_csv: str
    intents_catalog_path: str
    embeddings_jsonl: str
    min_word_count: int
    min_phrases_per_intent: int
    entity_mask_patterns: list[str]
    agent_integration: dict[str, str]
    environments: dict[str, EnvironmentSpec]


def advise_vector_configuration(
    cfg: VectorSteeringConfig, env_key: str, corpus_size: int | None = None
) -> list[str]:
    """Generates proactive ML/Vector DB optimization nudges."""
    nudges: list[str] = []

    if cfg.embedding_task_type.upper() != "SEMANTIC_SIMILARITY":
        nudges.append(
            f"[NUDGE - Task Type] task_type is '{cfg.embedding_task_type}'. "
            "Use symmetric 'SEMANTIC_SIMILARITY' on both index and query to "
            "avoid 8-15% asymmetric retrieval subspace loss."
        )

    if env_key in cfg.environments:
        env = cfg.environments[env_key]
        if (
            env.distance_measure_type == "DOT_PRODUCT_DISTANCE"
            and not cfg.embedding_normalize_l2
        ):
            nudges.append(
                "[CRITICAL NUDGE - Distance] 'DOT_PRODUCT_DISTANCE' requires "
                "unit-length vectors (||v||=1). Enable 'embedding.normalize_l2: true' "
                "or switch to 'COSINE_DISTANCE'."
            )
        if corpus_size is not None:
            if corpus_size < 30000 and env.index_algorithm == "TREE_AH":
                nudges.append(
                    f"[NUDGE - Algorithm] Corpus has {corpus_size} phrases (<30,000). "
                    "'BRUTE_FORCE' delivers 100.0% exact recall in ~5-15ms without "
                    "Tree-AH leaf-boundary misses."
                )
                if (
                    env.leaf_node_embedding_count >= 1000
                    and env.leaf_nodes_to_search_percent <= 10
                ):
                    nudges.append(
                        f"[WARNING - Tree-AH Recall] leaf_node_embedding_count="
                        f"{env.leaf_node_embedding_count} with "
                        f"leaf_nodes_to_search_percent={env.leaf_nodes_to_search_percent}% "
                        f"on {corpus_size} vectors risks missing intent clusters."
                    )
            elif corpus_size >= 50000 and env.index_algorithm == "BRUTE_FORCE":
                nudges.append(
                    f"[NUDGE - Scaling] Corpus has {corpus_size} phrases (>=50,000). "
                    "Switch index_algorithm to 'TREE_AH' for sub-linear latency."
                )
    return nudges


def load_yaml_file(file_path: str) -> dict[str, Any]:
    resolved = resolve_safe_path(file_path)
    if not os.path.exists(resolved):
        raise ConfigValidationError(f"Configuration file not found: {resolved}")
    with open(resolved, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ConfigValidationError(
            f"Expected top-level YAML mapping in {resolved}."
        )
    return data


def load_vector_steering_config(
    config_path: str, base_dir: str | None = None
) -> VectorSteeringConfig:
    raw = load_yaml_file(resolve_safe_path(config_path, base_dir=base_dir))

    proj = raw.get("project") or {}
    emb = raw.get("embedding") or {}
    thresh = raw.get("thresholds") or {}
    ds = raw.get("dataset") or {}
    agent_cfg = raw.get("agent_integration") or {}
    envs_raw = raw.get("environments") or {}

    similarity_threshold = float(thresh.get("similarity_threshold", 0.75))
    disambiguation_delta = float(thresh.get("disambiguation_delta", 0.08))
    if not (0.0 < similarity_threshold <= 1.0):
        raise ConfigValidationError(
            "thresholds.similarity_threshold must be in (0.0, 1.0]."
        )
    if not (0.0 <= disambiguation_delta < 1.0):
        raise ConfigValidationError(
            "thresholds.disambiguation_delta must be in [0.0, 1.0)."
        )

    normalize_l2 = bool(emb.get("normalize_l2", True))

    environments: dict[str, EnvironmentSpec] = {}
    for env_key, env_dict in envs_raw.items():
        if not isinstance(env_dict, dict):
            continue
        project_id = str(env_dict.get("project_id", "")).strip()
        dist_measure = (
            str(env_dict.get("distance_measure_type", "COSINE_DISTANCE"))
            .strip()
            .upper()
        )
        if dist_measure == "DOT_PRODUCT_DISTANCE" and not normalize_l2:
            raise ConfigValidationError(
                f"Environment '{env_key}' uses DOT_PRODUCT_DISTANCE, which "
                "requires embedding.normalize_l2: true."
            )
        environments[str(env_key)] = EnvironmentSpec(
            key=str(env_key),
            env_name=str(env_dict.get("env_name", str(env_key).upper())),
            project_id=project_id,
            quota_project_id=str(
                env_dict.get("quota_project_id") or project_id
            ).strip(),
            location=str(env_dict.get("location", "us-central1")).strip(),
            bucket_name=str(env_dict.get("bucket_name", "")).strip(),
            gcs_prefix=str(env_dict.get("gcs_prefix", "vector_steering"))
            .strip()
            .strip("/"),
            index_display_name=str(
                env_dict.get(
                    "index_display_name", f"vector-steering-index-{env_key}"
                )
            ).strip(),
            index_id=str(env_dict.get("index_id", "")).strip(),
            index_resource_name=str(
                env_dict.get("index_resource_name", "")
            ).strip(),
            endpoint_display_name=str(
                env_dict.get(
                    "endpoint_display_name",
                    f"vector-steering-endpoint-{env_key}",
                )
            ).strip(),
            index_endpoint_id=str(
                env_dict.get("index_endpoint_id", "")
            ).strip(),
            deployed_index_id=str(
                env_dict.get(
                    "deployed_index_id", "vector_steering_index_deployed"
                )
            ).strip(),
            index_algorithm=str(env_dict.get("index_algorithm", "BRUTE_FORCE"))
            .strip()
            .upper(),
            distance_measure_type=dist_measure,
            index_update_method=str(
                env_dict.get("index_update_method", "BATCH_UPDATE")
            )
            .strip()
            .upper(),
            approximate_neighbors_count=max(
                1, int(env_dict.get("approximate_neighbors_count", 150))
            ),
            leaf_node_embedding_count=max(
                10, int(env_dict.get("leaf_node_embedding_count", 500))
            ),
            leaf_nodes_to_search_percent=max(
                1,
                min(100, int(env_dict.get("leaf_nodes_to_search_percent", 15))),
            ),
            shard_size=str(
                env_dict.get("shard_size", "SHARD_SIZE_SMALL")
            ).strip(),
            machine_type=str(
                env_dict.get("machine_type", "e2-standard-2")
            ).strip(),
            min_replicas=int(env_dict.get("min_replicas", 1)),
            max_replicas=int(env_dict.get("max_replicas", 1)),
            cloud_function_name=str(
                env_dict.get(
                    "cloud_function_name", "vector-steering-classifier"
                )
            ).strip(),
            cloud_function_url=str(
                env_dict.get("cloud_function_url", "$env_var")
            ).strip(),
            ingress_settings=str(
                env_dict.get("ingress_settings", "all")
            ).strip(),
            service_account=str(env_dict.get("service_account", "")).strip(),
            agent_invoker_service_account=str(
                env_dict.get("agent_invoker_service_account", "")
            ).strip(),
        )

    default_agent_integration = {
        "intent_variable": str(agent_cfg.get("intent_variable", "Intent")),
        "tenant_variable": str(agent_cfg.get("tenant_variable", "tenant_id")),
        "toolset_name": validate_safe_identifier(
            str(agent_cfg.get("toolset_name", "vector_intent_router")),
            "agent_integration.toolset_name",
        ),
        "operation_id": validate_safe_identifier(
            str(agent_cfg.get("operation_id", "classify_intent")),
            "agent_integration.operation_id",
        ),
        "classifier_tool_name": validate_safe_identifier(
            str(agent_cfg.get("classifier_tool_name", "intent_classifier")),
            "agent_integration.classifier_tool_name",
        ),
        "update_intent_tool_name": validate_safe_identifier(
            str(agent_cfg.get("update_intent_tool_name", "update_intent")),
            "agent_integration.update_intent_tool_name",
        ),
        "agent_transfer_flag_variable": str(
            agent_cfg.get("agent_transfer_flag_variable", "isAgentTransfer")
        ),
        "agent_transfer_reason_variable": str(
            agent_cfg.get(
                "agent_transfer_reason_variable", "agentTransferReason"
            )
        ),
    }

    return VectorSteeringConfig(
        project_name=str(proj.get("name", "cymbal-assistant")),
        embedding_model=str(emb.get("model", "text-embedding-005")),
        embedding_dimensions=int(emb.get("dimensions", 768)),
        embedding_task_type=str(emb.get("task_type", "SEMANTIC_SIMILARITY")),
        embedding_normalize_l2=normalize_l2,
        embedding_batch_size=max(1, min(250, int(emb.get("batch_size", 200)))),
        similarity_threshold=similarity_threshold,
        disambiguation_delta=disambiguation_delta,
        secondary_match_floor_offset=float(
            thresh.get("secondary_match_floor_offset", 0.15)
        ),
        single_mode_min_confidence=float(
            thresh.get("single_mode_min_confidence", 0.60)
        ),
        top_k_neighbors=max(1, min(50, int(thresh.get("top_k_neighbors", 5)))),
        max_candidates=max(1, min(10, int(thresh.get("max_candidates", 3)))),
        corpus_csv=str(ds.get("corpus_csv", DEFAULT_SAMPLE_CSV)),
        intents_catalog_path=str(
            ds.get("intents_catalog", DEFAULT_EXAMPLE_CATALOG)
        ),
        embeddings_jsonl=str(
            ds.get(
                "embeddings_jsonl", "vector_steering/symmetric_embeddings.json"
            )
        ),
        min_word_count=max(1, int(ds.get("min_word_count", 2))),
        min_phrases_per_intent=max(1, int(ds.get("min_phrases_per_intent", 5))),
        entity_mask_patterns=[
            str(p) for p in (ds.get("entity_mask_patterns") or [])
        ],
        agent_integration=default_agent_integration,
        environments=environments,
    )


def load_intents_catalog(
    catalog_path: str, base_dir: str | None = None
) -> dict[str, IntentSpec]:
    raw = load_yaml_file(resolve_safe_path(catalog_path, base_dir=base_dir))
    intents_raw = raw.get("intents") or {}
    if not isinstance(intents_raw, dict):
        raise ConfigValidationError(
            "Expected 'intents' mapping in intents catalog YAML."
        )

    catalog: dict[str, IntentSpec] = {}
    for raw_id, meta in intents_raw.items():
        intent_id = validate_safe_identifier(str(raw_id), f"intents.{raw_id}")
        if isinstance(meta, str):
            catalog[intent_id] = IntentSpec(
                intent_id=intent_id, description=meta.strip()
            )
            continue
        if not isinstance(meta, dict):
            raise ConfigValidationError(
                f"Invalid metadata for intent '{intent_id}'."
            )
        catalog[intent_id] = IntentSpec(
            intent_id=intent_id,
            description=str(meta.get("description", "")).strip(),
            allowed_tenants=[
                str(t).strip().lower()
                for t in (meta.get("allowed_tenants") or [])
                if str(t).strip()
            ],
            excluded_tenants=[
                str(t).strip().lower()
                for t in (meta.get("excluded_tenants") or [])
                if str(t).strip()
            ],
            suppress_in_agents=[
                str(a).strip()
                for a in (meta.get("suppress_in_agents") or [])
                if str(a).strip()
            ],
            vector_indexed=bool(meta.get("vector_indexed", True)),
        )
    return catalog


INTENT_COLUMN_ALIASES = (
    "intent",
    "label",
    "category",
    "intent_name",
    "intent_id",
)
PHRASE_COLUMN_ALIASES = ("phrase", "utterance", "text", "query", "example")


def load_corpus_dataframe(corpus_path: str) -> pd.DataFrame:
    """Loads training corpora from CSV (including BOM), TSV, JSON, or JSONL."""
    resolved = resolve_safe_path(corpus_path)
    if not os.path.exists(resolved):
        raise FileNotFoundError(f"Training corpus file not found: {resolved}")

    lower = resolved.lower()
    if lower.endswith(".tsv"):
        df = pd.read_csv(resolved, sep="\t", encoding="utf-8-sig")
    elif lower.endswith(".jsonl"):
        df = pd.read_json(resolved, lines=True)
    elif lower.endswith(".json"):
        df = pd.read_json(resolved)
    else:
        df = pd.read_csv(resolved, encoding="utf-8-sig")

    rename_map: dict[str, str] = {}
    cleaned_cols = {col: str(col).strip() for col in df.columns}
    df = df.rename(columns=cleaned_cols)

    cols_lower = {c.lower(): c for c in df.columns}
    if "intent" not in df.columns:
        for alias in INTENT_COLUMN_ALIASES:
            if alias in cols_lower:
                rename_map[cols_lower[alias]] = "intent"
                break
    if "phrase" not in df.columns:
        for alias in PHRASE_COLUMN_ALIASES:
            if alias in cols_lower:
                rename_map[cols_lower[alias]] = "phrase"
                break

    if rename_map:
        df = df.rename(columns=rename_map)

    return df
