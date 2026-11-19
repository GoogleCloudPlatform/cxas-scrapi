# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Vector Steering Serverless Vector Intent Classifier Middleware (Gen2 Cloud Function).

Performs low-latency query vectorization via Vertex AI Embeddings and nearest-neighbor
retrieval against Vertex AI Vector Search, applying calibrated cosine similarity
thresholds and mathematical ambiguity gating.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

try:
    import functions_framework
except ImportError:  # pragma: no cover

    class _FunctionsFrameworkStub:
        @staticmethod
        def http(fn: Any) -> Any:
            return fn

    functions_framework = _FunctionsFrameworkStub()  # type: ignore[assignment]

try:
    import vertexai
    from google.cloud import aiplatform
    from google.cloud.aiplatform.matching_engine.matching_engine_index_endpoint import (
        Namespace,
    )
    from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel
except ImportError:  # pragma: no cover
    vertexai = None  # type: ignore[assignment]
    aiplatform = None  # type: ignore[assignment]
    Namespace = None  # type: ignore[assignment]
    TextEmbeddingInput = None  # type: ignore[assignment]
    TextEmbeddingModel = None  # type: ignore[assignment]

MAX_PAYLOAD_BYTES = int(os.getenv("MAX_PAYLOAD_BYTES", "65536"))
MAX_QUERY_LENGTH = int(os.getenv("MAX_QUERY_LENGTH", "2000"))
ALLOWED_MODES = {"", "multi", "single"}
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SAFE_TOKEN_RE = re.compile(r"^[a-zA-Z0-9_.:-]{1,128}$")

SECURITY_HEADERS: dict[str, str] = {
    "Content-Type": "application/json; charset=utf-8",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cache-Control": "no-store",
}

_RUNTIME_INITIALIZED = False
_INDEX_ENDPOINT: Any = None
_EMBEDDING_MODEL: Any = None


def _sanitize_log_token(value: str | None) -> str | None:
    if not value or not isinstance(value, str):
        return None
    cleaned = CONTROL_CHARS_RE.sub("", value.strip())[:128]
    return cleaned if SAFE_TOKEN_RE.match(cleaned) else "redacted"


def log(
    message: str,
    severity: str = "INFO",
    session_id: str | None = None,
    **extra: Any,
) -> None:
    """Emits structured JSON logs compatible with Google Cloud Logging."""
    entry: dict[str, Any] = {"message": message, "severity": severity}
    safe_session = _sanitize_log_token(session_id)
    if safe_session:
        entry["session_id"] = safe_session
    for key, val in extra.items():
        if val is not None:
            entry[key] = val
    print(json.dumps(entry))


def _build_response_headers(
    request_origin: str | None = None,
) -> dict[str, str]:
    """Builds hardened HTTP headers with strict allow-listed CORS."""
    headers = dict(SECURITY_HEADERS)
    raw_origins = os.getenv("ALLOWED_ORIGINS", "").strip()
    if raw_origins and request_origin:
        allowed = {
            origin.strip()
            for origin in raw_origins.split(",")
            if origin.strip() and origin.strip() != "*"
        }
        if request_origin in allowed:
            headers["Access-Control-Allow-Origin"] = request_origin
            headers["Vary"] = "Origin"
    return headers


def _ensure_runtime_initialized() -> tuple[Any, Any]:
    """Initializes Vertex AI SDK clients lazily on warm container startup."""
    global _RUNTIME_INITIALIZED, _INDEX_ENDPOINT, _EMBEDDING_MODEL  # noqa: PLW0603
    if (
        _RUNTIME_INITIALIZED
        and _INDEX_ENDPOINT is not None
        and _EMBEDDING_MODEL is not None
    ):
        return _EMBEDDING_MODEL, _INDEX_ENDPOINT

    project_id = (
        os.getenv("PROJECT_ID")
        or os.getenv("GCP_PROJECT")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or ""
    )
    location = (
        os.getenv("LOCATION") or os.getenv("FUNCTION_REGION") or "us-central1"
    )
    endpoint_id = os.getenv("INDEX_ENDPOINT_ID", "").strip()
    model_name = os.getenv("EMBEDDING_MODEL", "text-embedding-005").strip()

    if not endpoint_id or vertexai is None or aiplatform is None:
        return _EMBEDDING_MODEL, _INDEX_ENDPOINT

    vertexai.init(project=project_id or None, location=location)
    aiplatform.init(project=project_id or None, location=location)
    _INDEX_ENDPOINT = aiplatform.MatchingEngineIndexEndpoint(endpoint_id)
    _EMBEDDING_MODEL = TextEmbeddingModel.from_pretrained(model_name)
    _RUNTIME_INITIALIZED = True
    return _EMBEDDING_MODEL, _INDEX_ENDPOINT


def _sanitize_query(raw_query: Any) -> tuple[str | None, str | None]:
    if not isinstance(raw_query, str):
        return None, "Field 'query' must be a string."
    cleaned = CONTROL_CHARS_RE.sub(" ", raw_query).strip()
    if not cleaned:
        return None, "Field 'query' must not be empty."
    if len(cleaned) > MAX_QUERY_LENGTH:
        return (
            None,
            f"Field 'query' exceeds maximum length of {MAX_QUERY_LENGTH} characters.",
        )
    return cleaned, None


def _normalize_l2(values: list[float]) -> list[float]:
    norm_sq = sum(float(v) * float(v) for v in values)
    if norm_sq <= 0.0:
        return [float(v) for v in values]
    inv_norm = 1.0 / (norm_sq**0.5)
    return [float(v) * inv_norm for v in values]


def _distance_to_similarity(
    distance: float,
    distance_measure_type: str = "COSINE_DISTANCE",
) -> float:
    measure = (distance_measure_type or "COSINE_DISTANCE").strip().upper()
    if measure == "DOT_PRODUCT_DISTANCE":
        score = float(distance)
    elif measure == "SQUARED_L2_DISTANCE":
        score = 1.0 - (float(distance) / 2.0)
    else:
        score = 1.0 - float(distance)
    return round(max(0.0, min(1.0, score)), 4)


def evaluate_neighbors(
    neighbors: list[Any],
    *,
    mode: str = "multi",
    distance_measure_type: str = "COSINE_DISTANCE",
    similarity_threshold: float = 0.75,
    disambiguation_delta: float = 0.08,
    secondary_floor_offset: float = 0.15,
    max_top_matches: int = 3,
    latency_telemetry: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Deterministic threshold and disambiguation evaluator over neighbors."""
    top_matches: list[dict[str, Any]] = []
    for neighbor in neighbors or []:
        raw_id = getattr(neighbor, "id", "") or ""
        intent_name = str(raw_id).split(":::")[0].strip()
        if not intent_name:
            continue
        distance = float(getattr(neighbor, "distance", 1.0))
        score = _distance_to_similarity(distance, distance_measure_type)

        if not any(m["intent"] == intent_name for m in top_matches):
            top_matches.append({"intent": intent_name, "confidence": score})
            if len(top_matches) >= max_top_matches:
                break

    telemetry = latency_telemetry or {}

    if not top_matches:
        if mode == "single":
            return {"intent": "unknown", "confidence_score": 0.0, **telemetry}
        return {
            "intent": "unknown",
            "confidence_score": 0.0,
            "is_match_found": False,
            "requires_disambiguation": False,
            "top_matches": [],
            **telemetry,
        }

    best_match = top_matches[0]

    if mode == "single":
        return {
            "intent": best_match["intent"],
            "confidence_score": best_match["confidence"],
            "top_matches": top_matches,
            **telemetry,
        }

    requires_disambiguation = False
    score_diff: float | None = None
    if len(top_matches) > 1:
        score_diff = round(
            best_match["confidence"] - top_matches[1]["confidence"], 4
        )
        second_floor = max(0.0, similarity_threshold - secondary_floor_offset)
        if (
            score_diff < disambiguation_delta
            and top_matches[1]["confidence"] >= second_floor
        ):
            requires_disambiguation = True

    is_match_found = best_match["confidence"] >= similarity_threshold

    if not is_match_found and not requires_disambiguation:
        return {
            "intent": "unknown",
            "confidence_score": best_match["confidence"],
            "is_match_found": False,
            "requires_disambiguation": False,
            "top_matches": top_matches,
            **telemetry,
        }

    result: dict[str, Any] = {
        "intent": (
            best_match["intent"]
            if (is_match_found and not requires_disambiguation)
            else "unknown"
        ),
        "confidence_score": best_match["confidence"],
        "is_match_found": is_match_found,
        "requires_disambiguation": requires_disambiguation,
        "top_matches": top_matches,
        **telemetry,
    }
    if score_diff is not None:
        result["disambiguation_delta"] = score_diff
    return result


@functions_framework.http
def classify_intent(request: Any) -> tuple[str, int, dict[str, str]]:
    """HTTP entrypoint for Vector Steering Vector Intent Classification."""
    total_start = time.perf_counter()
    origin = (
        request.headers.get("Origin") if hasattr(request, "headers") else None
    )
    headers = _build_response_headers(origin)

    method = getattr(request, "method", "POST").upper()
    if method == "OPTIONS":
        if "Access-Control-Allow-Origin" not in headers:
            return (
                json.dumps({"error": "CORS origin not permitted."}),
                403,
                headers,
            )
        preflight_headers = dict(headers)
        preflight_headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        preflight_headers["Access-Control-Allow-Headers"] = (
            "Content-Type, Authorization"
        )
        preflight_headers["Access-Control-Max-Age"] = "3600"
        return ("", 204, preflight_headers)

    if method != "POST":
        headers["Allow"] = "POST"
        return (
            json.dumps({"error": "Method not allowed. Use POST."}),
            405,
            headers,
        )

    content_length = getattr(request, "content_length", None)
    if content_length is not None and content_length > MAX_PAYLOAD_BYTES:
        return (
            json.dumps({"error": "Request payload too large."}),
            413,
            headers,
        )

    session_id: str | None = None
    try:
        request_json = request.get_json(silent=True)
        if not isinstance(request_json, dict):
            return (
                json.dumps({"error": "Invalid JSON object in request body."}),
                400,
                headers,
            )

        session_id = request_json.get("session_id")
        query, query_err = _sanitize_query(request_json.get("query"))
        if query_err or query is None:
            log(
                f"Validation error: {query_err}",
                severity="WARNING",
                session_id=session_id,
            )
            return (json.dumps({"error": query_err}), 400, headers)

        raw_mode = request_json.get("mode")
        if raw_mode is None and hasattr(request, "args"):
            raw_mode = request.args.get("mode")
        mode = str(raw_mode or "").strip().lower()
        if mode not in ALLOWED_MODES:
            return (
                json.dumps(
                    {"error": "Invalid 'mode'. Expected 'multi' or 'single'."}
                ),
                400,
                headers,
            )

        tenant_id: str | None = None
        raw_tenant = request_json.get("tenant_id")
        if raw_tenant is not None and str(raw_tenant).strip():
            candidate_tenant = str(raw_tenant).strip().lower()
            if not SAFE_TOKEN_RE.match(candidate_tenant):
                return (
                    json.dumps({"error": "Invalid 'tenant_id' format."}),
                    400,
                    headers,
                )
            tenant_id = candidate_tenant

        embedding_model, index_endpoint = _ensure_runtime_initialized()
        deployed_index_id = os.getenv(
            "DEPLOYED_INDEX_ID", "vector_steering_index_deployed"
        ).strip()

        if (
            index_endpoint is None
            or embedding_model is None
            or not deployed_index_id
        ):
            log(
                "Vector Steering endpoint or embedding client is not configured.",
                severity="ERROR",
                session_id=session_id,
            )
            fallback = {
                "intent": "unknown",
                "confidence_score": 0.0,
                "is_match_found": False,
                "requires_disambiguation": False,
                "top_matches": [],
                "error_code": "ENDPOINT_NOT_CONFIGURED",
                "status": "error",
            }
            return (json.dumps(fallback), 200, headers)

        task_type = os.getenv(
            "EMBEDDING_TASK_TYPE", "SEMANTIC_SIMILARITY"
        ).strip()
        output_dim_raw = os.getenv("OUTPUT_DIMENSIONALITY", "").strip()
        output_dim = int(output_dim_raw) if output_dim_raw.isdigit() else None
        normalize_l2 = (
            os.getenv("NORMALIZE_L2", "true").strip().lower() != "false"
        )
        max_retries = max(0, min(4, int(os.getenv("MAX_RETRIES", "2"))))

        emb_start = time.perf_counter()
        inputs = (
            [TextEmbeddingInput(query, task_type)]
            if TextEmbeddingInput is not None
            else [query]
        )
        embeddings = None
        for attempt in range(max_retries + 1):
            try:
                if output_dim:
                    try:
                        embeddings = embedding_model.get_embeddings(
                            inputs, output_dimensionality=output_dim
                        )
                    except TypeError:
                        embeddings = embedding_model.get_embeddings(inputs)
                else:
                    embeddings = embedding_model.get_embeddings(inputs)
                break
            except Exception as emb_err:
                if attempt < max_retries:
                    log(
                        f"Transient embedding error ({attempt + 1}/{max_retries + 1})",
                        severity="WARNING",
                        session_id=session_id,
                    )
                    time.sleep(0.15 * (attempt + 1))
                else:
                    raise emb_err
        emb_ms = round((time.perf_counter() - emb_start) * 1000.0, 2)
        query_vector = list(embeddings[0].values)
        if output_dim and len(query_vector) > output_dim:
            query_vector = query_vector[:output_dim]
        if normalize_l2:
            query_vector = _normalize_l2(query_vector)

        num_neighbors = max(1, min(50, int(os.getenv("NUM_NEIGHBORS", "5"))))
        max_top_matches = max(
            1, min(10, int(os.getenv("MAX_TOP_MATCHES", "3")))
        )
        enable_ns_filter = (
            os.getenv("ENABLE_VECTOR_NAMESPACE_FILTER", "false").strip().lower()
            == "true"
        )

        namespace_filters = []
        if enable_ns_filter and tenant_id and Namespace is not None:
            namespace_filters.append(
                Namespace(name="tenant", allow_tokens=[tenant_id, "all"])
            )

        vec_start = time.perf_counter()
        response = None
        for attempt in range(max_retries + 1):
            try:
                find_kwargs: dict[str, Any] = {
                    "deployed_index_id": deployed_index_id,
                    "queries": [query_vector],
                    "num_neighbors": num_neighbors,
                }
                if namespace_filters:
                    find_kwargs["filter"] = namespace_filters
                response = index_endpoint.find_neighbors(**find_kwargs)
                break
            except Exception as search_err:
                if attempt < max_retries:
                    log(
                        f"Transient vector search error ({attempt + 1}/{max_retries + 1})",
                        severity="WARNING",
                        session_id=session_id,
                    )
                    time.sleep(0.15 * (attempt + 1))
                else:
                    raise search_err
        vec_ms = round((time.perf_counter() - vec_start) * 1000.0, 2)
        total_ms = round((time.perf_counter() - total_start) * 1000.0, 2)

        neighbors = response[0] if (response and len(response) > 0) else []
        distance_measure_type = (
            os.getenv("DISTANCE_MEASURE_TYPE", "COSINE_DISTANCE")
            .strip()
            .upper()
        )
        similarity_threshold = float(os.getenv("SIMILARITY_THRESHOLD", "0.75"))
        disambiguation_delta = float(os.getenv("DISAMBIGUATION_DELTA", "0.08"))
        secondary_floor_offset = float(
            os.getenv("SECONDARY_MATCH_FLOOR_OFFSET", "0.15")
        )

        result = evaluate_neighbors(
            neighbors,
            mode=mode or "multi",
            distance_measure_type=distance_measure_type,
            similarity_threshold=similarity_threshold,
            disambiguation_delta=disambiguation_delta,
            secondary_floor_offset=secondary_floor_offset,
            max_top_matches=max_top_matches,
            latency_telemetry={
                "embedding_latency_ms": emb_ms,
                "vector_search_latency_ms": vec_ms,
                "total_latency_ms": total_ms,
            },
        )
        return (json.dumps(result), 200, headers)

    except Exception as exc:  # noqa: BLE001
        log(
            f"Unhandled exception in classify_intent: {type(exc).__name__}: {exc}",
            severity="ERROR",
            session_id=session_id,
        )
        safe_fallback = {
            "intent": "unknown",
            "confidence_score": 0.0,
            "is_match_found": False,
            "requires_disambiguation": False,
            "top_matches": [],
            "error_code": "CLASSIFICATION_RUNTIME_ERROR",
            "status": "error",
        }
        return (json.dumps(safe_fallback), 200, headers)
