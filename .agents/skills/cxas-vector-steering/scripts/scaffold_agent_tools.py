# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Vector Steering Agent Scaffolder for CXAS SCRAPI (GECX / CES) and Google ADK.

Compiles `vector_steering.yaml` + `intents_catalog.yaml` into CES-schema-compliant:
  1. OpenAPI Toolset (`toolsets/<toolset_name>/`) with `$env_var` + `environment.json`
  2. Python Tool Wrapper (`tools/<classifier_tool_name>/`) with tenant isolation
  3. Anti-Hallucination Intent Setter (`tools/<update_intent_tool_name>/`)
  4. Steering Sub-Agent XML instruction (`steering_instruction_<modality>.txt`)
  5. `variable_declarations_snippet.json` (plus optional merge into `app.json`)
  6. Tool test suite YAML (`vector_steering_tool_tests.yaml`) for `cxas test-tools`
"""

from __future__ import annotations

import argparse
import json
import os
import pprint
import sys
import uuid
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from config_loader import (  # noqa: E402
    DEFAULT_EXAMPLE_CONFIG,
    RESOURCES_DIR,
    IntentSpec,
    VectorSteeringConfig,
    discover_gecx_workspace_config,
    load_corpus_dataframe,
    load_intents_catalog,
    load_vector_steering_config,
    resolve_safe_path,
)


def _deterministic_uuid(namespace_key: str) -> str:
    return str(
        uuid.uuid5(uuid.NAMESPACE_DNS, f"vector_steering.ces.{namespace_key}")
    )


def build_openapi_schema_yaml(
    cfg: VectorSteeringConfig, server_url: str
) -> str:
    op_id = cfg.agent_integration["operation_id"]
    schema_obj: dict[str, Any] = {
        "openapi": "3.0.3",
        "info": {
            "title": "Vector Steering Vector Intent Classifier API",
            "version": "1.0.0",
            "description": (
                "Two-Tower Hybrid Vector Search intent classification "
                "microservice backed by Vertex AI Vector Search."
            ),
        },
        "servers": [
            {
                "url": server_url,
                "description": "Vector Steering Cloud Run / Gen2 Cloud Function Endpoint",
            }
        ],
        "paths": {
            "/classify": {
                "post": {
                    "summary": "Classify User Utterance via Vector Search",
                    "description": (
                        "Embeds user query and retrieves calibrated cosine "
                        "nearest-neighbor intents."
                    ),
                    "operationId": op_id,
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "query": {
                                            "type": "string",
                                            "description": "Clean user utterance.",
                                        },
                                        "mode": {
                                            "type": "string",
                                            "description": "Mode ('multi' or 'single').",
                                        },
                                        "tenant_id": {
                                            "type": "string",
                                            "description": "Optional tenant ID.",
                                        },
                                        "session_id": {
                                            "type": "string",
                                            "description": "Optional session ID.",
                                        },
                                    },
                                    "required": ["query"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Structured classification response",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "intent": {"type": "string"},
                                            "confidence_score": {
                                                "type": "number",
                                                "format": "float",
                                            },
                                            "is_match_found": {
                                                "type": "boolean"
                                            },
                                            "requires_disambiguation": {
                                                "type": "boolean"
                                            },
                                            "disambiguation_delta": {
                                                "type": "number",
                                                "format": "float",
                                            },
                                            "top_matches": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "intent": {
                                                            "type": "string"
                                                        },
                                                        "confidence": {
                                                            "type": "number",
                                                            "format": "float",
                                                        },
                                                    },
                                                },
                                            },
                                        },
                                    }
                                }
                            },
                        },
                        "500": {"description": "Internal Server Error"},
                    },
                }
            }
        },
    }
    return yaml.safe_dump(schema_obj, sort_keys=False)


def build_intent_classifier_code(
    cfg: VectorSteeringConfig, catalog: dict[str, IntentSpec]
) -> str:
    fn_name = cfg.agent_integration["classifier_tool_name"]
    toolset_call = (
        f"{cfg.agent_integration['toolset_name']}_"
        f"{cfg.agent_integration['operation_id']}"
    )
    intent_var = cfg.agent_integration["intent_variable"]
    tenant_var = cfg.agent_integration["tenant_variable"]
    transfer_flag_var = cfg.agent_integration["agent_transfer_flag_variable"]
    transfer_reason_var = cfg.agent_integration[
        "agent_transfer_reason_variable"
    ]

    descriptions = {k: v.description for k, v in sorted(catalog.items())}
    allowed_tenants = {
        k: v.allowed_tenants
        for k, v in sorted(catalog.items())
        if v.allowed_tenants
    }
    excluded_tenants = {
        k: v.excluded_tenants
        for k, v in sorted(catalog.items())
        if v.excluded_tenants
    }
    suppressed_by_agent: dict[str, list[str]] = {}
    for intent_id, spec in sorted(catalog.items()):
        for agent_name in spec.suppress_in_agents:
            suppressed_by_agent.setdefault(agent_name, []).append(intent_id)

    return f'''from typing import Any

INTENT_DESCRIPTIONS: dict[str, str] = {pprint.pformat(descriptions, width=88, sort_dicts=True)}

INTENT_ALLOWED_TENANTS: dict[str, list[str]] = {pprint.pformat(allowed_tenants, width=88, sort_dicts=True)}

INTENT_EXCLUDED_TENANTS: dict[str, list[str]] = {pprint.pformat(excluded_tenants, width=88, sort_dicts=True)}

SUPPRESSED_INTENTS_BY_AGENT: dict[str, list[str]] = {pprint.pformat(suppressed_by_agent, width=88, sort_dicts=True)}

SIMILARITY_THRESHOLD: float = {cfg.similarity_threshold}
SINGLE_MODE_MIN_CONFIDENCE: float = {cfg.single_mode_min_confidence}


def _is_intent_allowed_for_tenant(intent_id: str, active_tenant: str) -> bool:
    if not intent_id:
        return False
    allowed = INTENT_ALLOWED_TENANTS.get(intent_id)
    if allowed and (not active_tenant or active_tenant not in allowed):
        return False
    excluded = INTENT_EXCLUDED_TENANTS.get(intent_id)
    if excluded and active_tenant and active_tenant in excluded:
        return False
    return True


def {fn_name}(query: str = "") -> dict[str, Any]:
    """Classifies customer utterances via Vector Steering Vertex AI Vector Search.

    Args:
        query (str): Clean user utterance or rephrased single-sentence query.

    Returns:
        dict[str, Any]: Classification result containing `intent`, `top_matches`
        with `intent_description`, `processed_action`, and `agent_action`.
    """
    clean_query = str(query or "").strip()
    if not clean_query:
        return {{
            "intent": "unknown",
            "confidence_score": 0.0,
            "is_match_found": False,
            "requires_disambiguation": False,
            "top_matches": [],
            "processed_action": "NO_MATCH",
            "agent_action": "EMPTY_QUERY: Ask the customer how you can assist today.",
        }}

    try:
        zd_state = context.variables.get("_vector_steering_state")
        if not isinstance(zd_state, dict):
            zd_state = {{}}

        mode = str(zd_state.get("mode") or "")
        active_tenant = str(context.variables.get("{tenant_var}", "") or "").strip().lower()

        payload: dict[str, Any] = {{"query": clean_query}}
        if active_tenant:
            payload["tenant_id"] = active_tenant

        zd_state["last_query"] = clean_query
        res = tools.{toolset_call}(payload)
        try:
            data = res.json()
        except AttributeError:
            data = res

        if not isinstance(data, dict):
            data = {{}}

        if data.get("status") == "error" or "error" in data or data.get("errorCode") == "INTERNAL":
            err_detail = str(data.get("error_code") or data.get("error") or "UPSTREAM_API_ERROR")
            zd_state["mode"] = ""
            context.variables["_vector_steering_state"] = zd_state
            return {{
                "intent": "unknown",
                "confidence_score": 0.0,
                "is_match_found": False,
                "requires_disambiguation": False,
                "top_matches": [],
                "processed_action": "ERROR_FALLBACK",
                "agent_action": f"INTENT_CLASSIFICATION_API_ERROR ({{err_detail}}): Ask user to clarify.",
            }}

        calling_agent = str(getattr(context, "agent_name", "") or "")
        suppressed_for_agent = set(SUPPRESSED_INTENTS_BY_AGENT.get(calling_agent, []))

        raw_matches = data.get("top_matches") or []
        filtered_matches: list[dict[str, Any]] = []
        for match in raw_matches:
            if not isinstance(match, dict):
                continue
            m_intent = str(match.get("intent") or "").strip()
            if not m_intent or m_intent in suppressed_for_agent:
                continue
            if not _is_intent_allowed_for_tenant(m_intent, active_tenant):
                continue
            enriched = dict(match)
            enriched["intent_description"] = INTENT_DESCRIPTIONS.get(m_intent, "")
            filtered_matches.append(enriched)

        data["top_matches"] = filtered_matches

        resolved_intent = str(data.get("intent") or "unknown").strip()
        if resolved_intent in suppressed_for_agent or not _is_intent_allowed_for_tenant(resolved_intent, active_tenant):
            resolved_intent = "unknown"
            data["intent"] = "unknown"

        if len(filtered_matches) == 1 and float(filtered_matches[0].get("confidence", 0.0)) >= SIMILARITY_THRESHOLD:
            resolved_intent = str(filtered_matches[0]["intent"])
            data["intent"] = resolved_intent
            data["confidence_score"] = float(filtered_matches[0]["confidence"])
            data["is_match_found"] = True
            data["requires_disambiguation"] = False
        elif not filtered_matches:
            resolved_intent = "unknown"
            data["intent"] = "unknown"
            data["confidence_score"] = 0.0
            data["is_match_found"] = False
            data["requires_disambiguation"] = False

        if mode == "single":
            prior_candidates = set(zd_state.get("previous_top_intents") or [])
            chosen_match = None
            for m in filtered_matches:
                if m["intent"] in prior_candidates and float(m.get("confidence", 0.0)) >= SINGLE_MODE_MIN_CONFIDENCE:
                    chosen_match = m
                    break
            if chosen_match is None and filtered_matches:
                if float(filtered_matches[0].get("confidence", 0.0)) >= SINGLE_MODE_MIN_CONFIDENCE:
                    chosen_match = filtered_matches[0]

            if chosen_match is not None:
                resolved_intent = str(chosen_match["intent"])
                context.variables["{intent_var}"] = resolved_intent
                zd_state["no_match_count"] = 0
                data["intent"] = resolved_intent
                data["confidence_score"] = float(chosen_match.get("confidence", 0.0))
                data["is_match_found"] = True
                data["requires_disambiguation"] = False
                data["processed_action"] = "INTENT_SET"
                data["agent_action"] = f"INTENT_SET: Call update_intent with '{{resolved_intent}}'."
            else:
                context.variables["{intent_var}"] = "unknown"
                zd_state["no_match_count"] = int(zd_state.get("no_match_count", 0)) + 1
                data["intent"] = "unknown"
                data["is_match_found"] = False
                data["requires_disambiguation"] = False
                data["processed_action"] = "NO_MATCH"
                data["agent_action"] = "NO_MATCH: Ask the customer to clarify their request."

            zd_state["mode"] = ""
            zd_state["previous_top_intents"] = []
        else:
            is_match = bool(data.get("is_match_found", False))
            needs_disambig = bool(data.get("requires_disambiguation", False))

            if is_match and not needs_disambig and resolved_intent and resolved_intent != "unknown":
                context.variables["{intent_var}"] = resolved_intent
                zd_state["no_match_count"] = 0
                zd_state["mode"] = ""
                zd_state["previous_top_intents"] = []
                data["processed_action"] = "INTENT_SET"
                data["agent_action"] = f"INTENT_SET: Call update_intent with '{{resolved_intent}}'."
            elif needs_disambig and filtered_matches:
                zd_state["mode"] = "single"
                zd_state["previous_top_intents"] = [str(m["intent"]) for m in filtered_matches]
                data["processed_action"] = "DISAMBIGUATION_REQUIRED"
                data["agent_action"] = (
                    "DISAMBIGUATION_REQUIRED: Inspect candidate intent_description values "
                    "in top_matches and select via update_intent or ask one concise question."
                )
            else:
                zd_state["mode"] = ""
                zd_state["previous_top_intents"] = []
                zd_state["no_match_count"] = int(zd_state.get("no_match_count", 0)) + 1
                data["processed_action"] = "NO_MATCH"
                data["agent_action"] = "NO_MATCH: Ask the customer to rephrase their request."

        final_intent = str(data.get("intent") or "unknown")
        data["intent_description"] = INTENT_DESCRIPTIONS.get(final_intent, "") if final_intent != "unknown" else ""
        context.variables["_vector_steering_state"] = zd_state
        return data

    except Exception as exc:
        print(f"Vector Steering {fn_name} unexpected error: {{exc}}")
        context.variables["{transfer_flag_var}"] = True
        context.variables["{transfer_reason_var}"] = "Intent Classifier Runtime Error"
        return {{
            "status": "error",
            "intent": "unknown",
            "confidence_score": 0.0,
            "is_match_found": False,
            "requires_disambiguation": False,
            "top_matches": [],
            "processed_action": "ERROR_FALLBACK",
            "agent_action": "ERROR_FALLBACK: Apologize briefly and ask the customer to repeat.",
        }}
'''


def build_update_intent_code(
    cfg: VectorSteeringConfig, valid_intents: list[str]
) -> str:
    fn_name = cfg.agent_integration["update_intent_tool_name"]
    intent_var = cfg.agent_integration["intent_variable"]
    sorted_intents = sorted(set(valid_intents))

    return f'''from typing import Any

VALID_INTENTS: set[str] = {pprint.pformat(set(sorted_intents), width=88)}


def {fn_name}({intent_var}: str = "") -> dict[str, Any]:
    """Updates `{intent_var}` with strict allowlist anti-hallucination validation.

    Args:
        {intent_var} (str): Canonical intent identifier from `top_matches`.

    Returns:
        dict[str, Any]: Structured status dictionary containing `agent_action`.
    """
    try:
        cleaned = str({intent_var} or "").strip()
        zd_state = context.variables.get("_vector_steering_state")
        if not isinstance(zd_state, dict):
            zd_state = {{}}

        if not cleaned or cleaned.lower() in ("null", "none"):
            context.variables["{intent_var}"] = ""
            zd_state["mode"] = ""
            zd_state["previous_top_intents"] = []
            context.variables["_vector_steering_state"] = zd_state
            return {{
                "status": "success",
                "message": "{intent_var} reset.",
                "agent_action": "INTENT_RESET_COMPLETE",
            }}

        if cleaned not in VALID_INTENTS:
            return {{
                "status": "rejected",
                "message": f"Intent '{{cleaned}}' is not in VALID_INTENTS.",
                "agent_action": (
                    f"INVALID_INTENT_REJECTED: '{{cleaned}}' is not a valid canonical intent. "
                    "Only pass exact intent strings returned in top_matches."
                ),
            }}

        context.variables["{intent_var}"] = cleaned
        zd_state["mode"] = ""
        zd_state["previous_top_intents"] = []
        zd_state["no_match_count"] = 0
        context.variables["_vector_steering_state"] = zd_state

        return {{
            "status": "success",
            "intent": cleaned,
            "message": f"{intent_var} set to '{{cleaned}}'.",
            "agent_action": f"INTENT_UPDATED: {intent_var} is now '{{cleaned}}'.",
        }}
    except Exception as exc:
        print(f"Error in {fn_name}: {{exc}}")
        return {{
            "status": "error",
            "agent_action": "UPDATE_INTENT_ERROR: Retry or ask the user to confirm.",
        }}
'''


def build_variable_declarations(
    cfg: VectorSteeringConfig,
) -> list[dict[str, Any]]:
    return [
        {
            "name": cfg.agent_integration["intent_variable"],
            "description": "Canonical customer intent resolved via Vector Steering vector router",
            "schema": {"type": "STRING", "default": ""},
        },
        {
            "name": cfg.agent_integration["tenant_variable"],
            "description": "Active tenant, brand, or customer tier identifier",
            "schema": {"type": "STRING", "default": ""},
        },
        {
            "name": cfg.agent_integration["agent_transfer_flag_variable"],
            "description": "Indicates whether live agent escalation has been triggered",
            "schema": {"type": "BOOLEAN", "default": False},
        },
        {
            "name": cfg.agent_integration["agent_transfer_reason_variable"],
            "description": "Diagnostic reason for live agent transfer",
            "schema": {"type": "STRING", "default": ""},
        },
        {
            "name": "_vector_steering_state",
            "description": "Structured Vector Steering disambiguation and retry tracking state",
            "schema": {
                "type": "OBJECT",
                "properties": {
                    "mode": {"type": "STRING"},
                    "last_query": {"type": "STRING"},
                    "previous_top_intents": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                    },
                    "no_match_count": {"type": "NUMBER"},
                },
            },
        },
    ]


def build_tool_tests_yaml(cfg: VectorSteeringConfig, sample_intent: str) -> str:
    update_fn = cfg.agent_integration["update_intent_tool_name"]
    intent_var = cfg.agent_integration["intent_variable"]
    test_suite = {
        "tests": [
            {
                "name": "update_intent_accepts_valid_canonical_intent",
                "tool": update_fn,
                "args": {intent_var: sample_intent},
                "expected_output": {
                    "status": "success",
                    "intent": sample_intent,
                },
            },
            {
                "name": "update_intent_rejects_hallucinated_intent",
                "tool": update_fn,
                "args": {intent_var: "invented_hallucinated_intent_xyz"},
                "expected_output": {"status": "rejected"},
            },
        ]
    }
    return yaml.safe_dump(test_suite, sort_keys=False)


def render_steering_instruction(
    cfg: VectorSteeringConfig, modality: str = "voice"
) -> str:
    templates_dir = RESOURCES_DIR / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=False,
        keep_trailing_newline=True,
    )
    template_name = (
        "steering_agent_voice.xml.j2"
        if modality == "voice"
        else "steering_agent_chat.xml.j2"
    )
    template = env.get_template(template_name)
    return template.render(
        project_name=cfg.project_name,
        classifier_tool_name=cfg.agent_integration["classifier_tool_name"],
        update_intent_tool_name=cfg.agent_integration[
            "update_intent_tool_name"
        ],
        intent_variable=cfg.agent_integration["intent_variable"],
    )


def build_adk_tools_module(
    cfg: VectorSteeringConfig,
    catalog: dict[str, IntentSpec],
    valid_intents: list[str],
    server_url: str,
    modality: str = "voice",
) -> str:
    """Generates a standalone Google ADK Python tools module."""
    cls_fn = cfg.agent_integration["classifier_tool_name"]
    upd_fn = cfg.agent_integration["update_intent_tool_name"]
    intent_var = cfg.agent_integration["intent_variable"]
    tenant_var = cfg.agent_integration["tenant_variable"]
    descriptions = {k: v.description for k, v in sorted(catalog.items())}
    allowed_tenants = {
        k: v.allowed_tenants
        for k, v in sorted(catalog.items())
        if v.allowed_tenants
    }
    excluded_tenants = {
        k: v.excluded_tenants
        for k, v in sorted(catalog.items())
        if v.excluded_tenants
    }
    sorted_valid = sorted(set(valid_intents))
    steering_xml = render_steering_instruction(cfg, modality=modality)

    return f'''"""Auto-generated Vector Steering Google ADK Tools & Steering Instruction Module."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import requests

INTENT_DESCRIPTIONS: dict[str, str] = {pprint.pformat(descriptions, width=88, sort_dicts=True)}
INTENT_ALLOWED_TENANTS: dict[str, list[str]] = {pprint.pformat(allowed_tenants, width=88, sort_dicts=True)}
INTENT_EXCLUDED_TENANTS: dict[str, list[str]] = {pprint.pformat(excluded_tenants, width=88, sort_dicts=True)}
VALID_INTENTS: set[str] = {pprint.pformat(set(sorted_valid), width=88)}

SIMILARITY_THRESHOLD: float = {cfg.similarity_threshold}
SINGLE_MODE_MIN_CONFIDENCE: float = {cfg.single_mode_min_confidence}
DEFAULT_CLASSIFIER_URL: str = {server_url!r}

STEERING_INSTRUCTION: str = {steering_xml!r}

_CUSTOM_POST_TRANSPORT: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None


def _is_intent_allowed_for_tenant(intent_id: str, active_tenant: str) -> bool:
    if not intent_id:
        return False
    allowed = INTENT_ALLOWED_TENANTS.get(intent_id)
    if allowed and (not active_tenant or active_tenant not in allowed):
        return False
    excluded = INTENT_EXCLUDED_TENANTS.get(intent_id)
    if excluded and active_tenant and active_tenant in excluded:
        return False
    return True


def _invoke_classifier_endpoint(payload: dict[str, Any]) -> dict[str, Any]:
    url = os.getenv("VECTOR_STEERING_CLASSIFIER_URL", DEFAULT_CLASSIFIER_URL).strip()
    if _CUSTOM_POST_TRANSPORT is not None:
        return _CUSTOM_POST_TRANSPORT(url, payload)

    from google.auth.transport.requests import Request  # noqa: PLC0415
    from google.oauth2 import id_token  # noqa: PLC0415

    token = id_token.fetch_id_token(Request(), url)
    resp = requests.post(
        f"{{url.rstrip('/')}}/classify",
        json=payload,
        headers={{"Authorization": f"Bearer {{token}}"}},
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()


def {cls_fn}(query: str = "", tool_context: Any = None) -> dict[str, Any]:
    """Classifies user utterance via Vector Steering Vector Search for Google ADK."""
    clean_query = str(query or "").strip()
    if not clean_query:
        return {{
            "intent": "unknown",
            "processed_action": "NO_MATCH",
            "agent_action": "EMPTY_QUERY: Ask the customer how you can assist today.",
        }}

    state = getattr(tool_context, "state", {{}}) if tool_context is not None else {{}}
    zd_state = dict(state.get("_vector_steering_state") or {{}})
    active_tenant = str(state.get("{tenant_var}", "") or "").strip().lower()

    try:
        payload: dict[str, Any] = {{"query": clean_query}}
        if active_tenant:
            payload["tenant_id"] = active_tenant
        data = dict(_invoke_classifier_endpoint(payload) or {{}})
    except Exception as exc:
        return {{
            "status": "error",
            "intent": "unknown",
            "processed_action": "ERROR_FALLBACK",
            "agent_action": f"CLASSIFIER_UNAVAILABLE ({{type(exc).__name__}}): Ask user to clarify.",
        }}

    filtered: list[dict[str, Any]] = []
    for match in data.get("top_matches") or []:
        m_intent = str(match.get("intent") or "").strip()
        if m_intent and _is_intent_allowed_for_tenant(m_intent, active_tenant):
            enriched = dict(match)
            enriched["intent_description"] = INTENT_DESCRIPTIONS.get(m_intent, "")
            filtered.append(enriched)

    data["top_matches"] = filtered
    resolved = str(data.get("intent") or "unknown").strip()
    if not _is_intent_allowed_for_tenant(resolved, active_tenant):
        resolved = "unknown"
        data["intent"] = "unknown"

    if len(filtered) == 1 and float(filtered[0].get("confidence", 0.0)) >= SIMILARITY_THRESHOLD:
        resolved = str(filtered[0]["intent"])
        data["intent"] = resolved
        data["is_match_found"] = True
        data["requires_disambiguation"] = False

    if data.get("is_match_found") and not data.get("requires_disambiguation") and resolved != "unknown":
        state["{intent_var}"] = resolved
        zd_state["mode"] = ""
        zd_state["previous_top_intents"] = []
        data["processed_action"] = "INTENT_SET"
        data["agent_action"] = f"INTENT_SET: Call {upd_fn} with '{{resolved}}'."
    elif data.get("requires_disambiguation") and filtered:
        zd_state["mode"] = "single"
        zd_state["previous_top_intents"] = [str(m["intent"]) for m in filtered]
        data["processed_action"] = "DISAMBIGUATION_REQUIRED"
        data["agent_action"] = "DISAMBIGUATION_REQUIRED: Resolve via descriptions or ask 1 question."
    else:
        data["processed_action"] = "NO_MATCH"
        data["agent_action"] = "NO_MATCH: Ask the user to rephrase their request."

    state["_vector_steering_state"] = zd_state
    return data


def {upd_fn}({intent_var}: str = "", tool_context: Any = None) -> dict[str, Any]:
    """Allowlist-enforced intent state updater for Google ADK agents."""
    state = getattr(tool_context, "state", {{}}) if tool_context is not None else {{}}
    cleaned = str({intent_var} or "").strip()
    if not cleaned:
        state["{intent_var}"] = ""
        return {{"status": "success", "agent_action": "INTENT_RESET_COMPLETE"}}
    if cleaned not in VALID_INTENTS:
        return {{
            "status": "rejected",
            "agent_action": f"INVALID_INTENT_REJECTED: '{{cleaned}}' is not in VALID_INTENTS.",
        }}
    state["{intent_var}"] = cleaned
    zd_state = dict(state.get("_vector_steering_state") or {{}})
    zd_state["mode"] = ""
    zd_state["previous_top_intents"] = []
    state["_vector_steering_state"] = zd_state
    return {{"status": "success", "intent": cleaned, "agent_action": f"INTENT_UPDATED: {{cleaned}}"}}
'''


def _sync_environment_json(
    out_root: Path, toolset_name: str, resolved_url: str
) -> str:
    """Syncs toolset endpoint URL into CXAS SCRAPI `environment.json`."""
    env_json_path = out_root / "environment.json"
    env_data: dict[str, Any] = {}
    if env_json_path.is_file():
        env_data = json.loads(env_json_path.read_text(encoding="utf-8"))

    toolsets_section = env_data.setdefault("toolsets", {})
    placeholder_url = (
        resolved_url
        if resolved_url and not resolved_url.startswith("$")
        else "https://vector-steering-classifier-placeholder.a.run.app"
    )
    toolsets_section[toolset_name] = {
        "openApiToolset": {"url": placeholder_url}
    }
    env_json_path.write_text(
        json.dumps(env_data, indent=2) + "\n", encoding="utf-8"
    )
    return str(env_json_path)


def _wire_steering_agent_json(
    out_root: Path,
    agent_name: str,
    cls_tool_name: str,
    upd_tool_name: str,
    toolset_name: str,
    op_id: str,
) -> str | None:
    """Registers Vector Steering tools and toolset on an existing CXAS sub-agent JSON."""
    agent_json_path = out_root / "agents" / agent_name / f"{agent_name}.json"
    if not agent_json_path.is_file():
        return None
    agent_data = json.loads(agent_json_path.read_text(encoding="utf-8"))
    tools_list: list[str] = list(agent_data.get("tools") or [])
    for t_name in (cls_tool_name, upd_tool_name):
        if t_name not in tools_list:
            tools_list.append(t_name)
    agent_data["tools"] = tools_list

    toolsets_list: list[dict[str, Any]] = list(agent_data.get("toolsets") or [])
    if not any(ts.get("toolset") == toolset_name for ts in toolsets_list):
        toolsets_list.append({"toolset": toolset_name, "toolIds": [op_id]})
    agent_data["toolsets"] = toolsets_list

    agent_json_path.write_text(
        json.dumps(agent_data, indent=2) + "\n", encoding="utf-8"
    )
    return str(agent_json_path)


def scaffold_bundle(
    *,
    cfg: VectorSteeringConfig,
    catalog: dict[str, IntentSpec],
    output_dir: str,
    env_key: str = "dev",
    modality: str = "voice",
    target_platform: str = "gecx",
    merge_app_vars: bool = False,
    sync_env_json: bool = False,
    wire_steering_agent: str | None = None,
) -> dict[str, str]:
    """Generates GECX/CES or Google ADK integration files into `output_dir`."""
    out_root = Path(resolve_safe_path(output_dir))

    valid_intents = set(catalog.keys())
    if cfg.corpus_csv and os.path.exists(cfg.corpus_csv):
        df = load_corpus_dataframe(cfg.corpus_csv).dropna(subset=["intent"])
        valid_intents.update(df["intent"].astype(str).str.strip().tolist())

    configured_url = "$env_var"
    if (
        env_key in cfg.environments
        and cfg.environments[env_key].cloud_function_url
    ):
        configured_url = cfg.environments[env_key].cloud_function_url

    if target_platform == "adk":
        out_root.mkdir(parents=True, exist_ok=True)
        adk_url = (
            configured_url
            if not configured_url.startswith("$")
            else "https://example-vector-steering-classifier.a.run.app"
        )
        adk_module_path = out_root / "vector_steering_adk_tools.py"
        adk_module_path.write_text(
            build_adk_tools_module(
                cfg, catalog, sorted(valid_intents), adk_url, modality=modality
            ),
            encoding="utf-8",
        )
        instr_path = out_root / f"steering_instruction_{modality}.txt"
        instr_path.write_text(
            render_steering_instruction(cfg, modality=modality),
            encoding="utf-8",
        )
        return {
            "adk_tools_module": str(adk_module_path),
            "steering_instruction": str(instr_path),
        }

    toolset_name = cfg.agent_integration["toolset_name"]
    cls_tool_name = cfg.agent_integration["classifier_tool_name"]
    upd_tool_name = cfg.agent_integration["update_intent_tool_name"]
    op_id = cfg.agent_integration["operation_id"]

    schema_server_url = "$env_var" if sync_env_json else configured_url

    toolset_dir = out_root / "toolsets" / toolset_name
    openapi_dir = toolset_dir / "open_api_toolset"
    openapi_dir.mkdir(parents=True, exist_ok=True)

    schema_path = openapi_dir / "open_api_schema.yaml"
    schema_path.write_text(
        build_openapi_schema_yaml(cfg, schema_server_url), encoding="utf-8"
    )

    toolset_json_path = toolset_dir / f"{toolset_name}.json"
    toolset_json = {
        "name": _deterministic_uuid(f"toolset.{toolset_name}"),
        "displayName": toolset_name,
        "openApiToolset": {
            "openApiSchema": (
                f"toolsets/{toolset_name}/open_api_toolset/open_api_schema.yaml"
            ),
            "apiAuthentication": {"serviceAgentIdTokenAuthConfig": {}},
        },
    }
    toolset_json_path.write_text(
        json.dumps(toolset_json, indent=2) + "\n", encoding="utf-8"
    )

    cls_dir = out_root / "tools" / cls_tool_name
    cls_py_dir = cls_dir / "python_function"
    cls_py_dir.mkdir(parents=True, exist_ok=True)

    cls_code_path = cls_py_dir / "python_code.py"
    cls_code_path.write_text(
        build_intent_classifier_code(cfg, catalog), encoding="utf-8"
    )

    cls_json_path = cls_dir / f"{cls_tool_name}.json"
    cls_json = {
        "name": _deterministic_uuid(f"tool.{cls_tool_name}"),
        "displayName": cls_tool_name,
        "executionType": "SYNCHRONOUS",
        "pythonFunction": {
            "name": cls_tool_name,
            "pythonCode": f"tools/{cls_tool_name}/python_function/python_code.py",
            "description": (
                "Classifies customer utterances via Vector Steering Vertex AI Vector "
                "Search, applies tenant isolation, and returns top candidate "
                "intents with descriptions and disambiguation flags."
            ),
        },
    }
    cls_json_path.write_text(
        json.dumps(cls_json, indent=2) + "\n", encoding="utf-8"
    )

    upd_dir = out_root / "tools" / upd_tool_name
    upd_py_dir = upd_dir / "python_function"
    upd_py_dir.mkdir(parents=True, exist_ok=True)

    upd_code_path = upd_py_dir / "python_code.py"
    upd_code_path.write_text(
        build_update_intent_code(cfg, sorted(valid_intents)), encoding="utf-8"
    )

    upd_json_path = upd_dir / f"{upd_tool_name}.json"
    upd_json = {
        "name": _deterministic_uuid(f"tool.{upd_tool_name}"),
        "displayName": upd_tool_name,
        "executionType": "SYNCHRONOUS",
        "pythonFunction": {
            "name": upd_tool_name,
            "pythonCode": f"tools/{upd_tool_name}/python_function/python_code.py",
            "description": (
                f"Updates `{cfg.agent_integration['intent_variable']}` with "
                "strict allowlist validation."
            ),
        },
    }
    upd_json_path.write_text(
        json.dumps(upd_json, indent=2) + "\n", encoding="utf-8"
    )

    # Write generated snippets outside `out_root` if `app.json` is present so we
    # never pollute a strict CXAS app root directory with unexported folders;
    # otherwise write under `out_root / "vector_steering_generated"`.
    app_json_path = out_root / "app.json"
    meta_dir = (
        out_root.parent / f".vector_steering_{out_root.name}"
        if app_json_path.is_file()
        else out_root / "vector_steering_generated"
    )
    meta_dir.mkdir(parents=True, exist_ok=True)

    instr_path = meta_dir / f"steering_instruction_{modality}.txt"
    instr_path.write_text(
        render_steering_instruction(cfg, modality=modality), encoding="utf-8"
    )

    vars_decls = build_variable_declarations(cfg)
    vars_path = meta_dir / "variable_declarations_snippet.json"
    vars_path.write_text(
        json.dumps(vars_decls, indent=2) + "\n", encoding="utf-8"
    )

    sample_intent = (
        sorted(valid_intents)[0] if valid_intents else "order_status"
    )
    tests_path = meta_dir / "vector_steering_tool_tests.yaml"
    tests_path.write_text(
        build_tool_tests_yaml(cfg, sample_intent), encoding="utf-8"
    )

    outputs: dict[str, str] = {
        "toolset_json": str(toolset_json_path),
        "openapi_schema": str(schema_path),
        "classifier_tool_json": str(cls_json_path),
        "classifier_tool_code": str(cls_code_path),
        "update_intent_tool_json": str(upd_json_path),
        "update_intent_tool_code": str(upd_code_path),
        "steering_instruction": str(instr_path),
        "variable_declarations": str(vars_path),
        "tool_tests": str(tests_path),
    }

    if merge_app_vars and app_json_path.is_file():
        app_data = json.loads(app_json_path.read_text(encoding="utf-8"))
        existing_decls = app_data.get("variableDeclarations") or []
        existing_names = {
            d.get("name") for d in existing_decls if isinstance(d, dict)
        }
        added = 0
        for decl in vars_decls:
            if decl["name"] not in existing_names:
                existing_decls.append(decl)
                added += 1
        if added > 0:
            app_data["variableDeclarations"] = existing_decls
            app_json_path.write_text(
                json.dumps(app_data, indent=2) + "\n", encoding="utf-8"
            )

    if sync_env_json or (out_root / "environment.json").is_file():
        outputs["environment_json"] = _sync_environment_json(
            out_root, toolset_name, configured_url
        )

    if wire_steering_agent:
        wired = _wire_steering_agent_json(
            out_root,
            wire_steering_agent,
            cls_tool_name,
            upd_tool_name,
            toolset_name,
            op_id,
        )
        if wired:
            outputs["wired_agent_json"] = wired

    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scaffold Vector Steering CXAS SCRAPI / ADK tools, toolsets, and prompt."
    )
    parser.add_argument(
        "--config",
        type=str,
        default=DEFAULT_EXAMPLE_CONFIG,
        help="Path to vector_steering.yaml",
    )
    parser.add_argument(
        "--catalog",
        type=str,
        default=None,
        help="Optional override path to intents_catalog.yaml",
    )
    parser.add_argument(
        "--app-dir",
        type=str,
        default=None,
        help="Target CXAS app directory (defaults to gecx-config.json app_dir)",
    )
    parser.add_argument(
        "--env",
        type=str,
        default="dev",
        help="Environment key for Cloud Function URL lookup",
    )
    parser.add_argument(
        "--modality",
        choices=["voice", "chat"],
        default=None,
        help="Target steering modality (auto-detected from gecx-config.json if omitted)",
    )
    parser.add_argument(
        "--target",
        choices=["gecx", "adk"],
        default="gecx",
        help="Target platform: 'gecx' (CXAS SCRAPI) or 'adk' (Google ADK)",
    )
    parser.add_argument(
        "--merge-app-vars",
        action="store_true",
        help="Merge missing variableDeclarations into <app-dir>/app.json",
    )
    parser.add_argument(
        "--sync-env-json",
        action="store_true",
        help="Sync $env_var toolset URL into <app-dir>/environment.json",
    )
    parser.add_argument(
        "--wire-steering-agent",
        type=str,
        default=None,
        help="Optional CXAS sub-agent name to register tools and toolsets in",
    )
    args = parser.parse_args()

    ws_cfg = discover_gecx_workspace_config(".")
    app_dir = args.app_dir or str(ws_cfg.get("app_dir") or "").strip()
    if not app_dir:
        parser.error(
            "--app-dir is required when gecx-config.json does not specify app_dir."
        )

    modality = args.modality
    if not modality:
        raw_mod = str(ws_cfg.get("modality") or "voice").lower()
        modality = "voice" if raw_mod in ("audio", "voice") else "chat"

    cfg = load_vector_steering_config(args.config)
    catalog_path = args.catalog or cfg.intents_catalog_path
    catalog = load_intents_catalog(catalog_path)

    outputs = scaffold_bundle(
        cfg=cfg,
        catalog=catalog,
        output_dir=app_dir,
        env_key=args.env,
        modality=modality,
        target_platform=args.target,
        merge_app_vars=args.merge_app_vars,
        sync_env_json=args.sync_env_json,
        wire_steering_agent=args.wire_steering_agent,
    )

    print(
        f"Vector Steering {args.target.upper()} Agent Integration Bundle Generated:"
    )
    for label, path in outputs.items():
        print(f"  - {label:24s}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
