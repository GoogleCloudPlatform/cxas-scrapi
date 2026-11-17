#!/usr/bin/env python3
"""Automated CXAS Tool Data Hygiene, Python Code & Instruction Alignment Analyzer.

Reads centralized variables from `.agents/skills/cxas-tool-data-hygiene/config.py`
(with optional CLI overrides) and executes the 6-Step Tool Data Hygiene SOP:
  - Step 0: Pre-Triage (Exempt Tool Categories & Local Active Usage Verification)
  - Step 1: BigQuery Tool Log Extraction (`ccai_insights_agent_tool`)
  - Step 2: Filtering & Isolating `distinct_tool_response` (Ignoring `'NA'` toolCall rows)
  - Step 3: 6-Pillar Tool Response Data Hygiene Analysis (Token Bloat, Nesting, Empty/Nulls,
            Duplicates/UI Noise, Double Serialization, Casing/Typing & Hallucination Risks)
  - Step 4: Agent Instruction (`instruction.txt`) vs. Tool Response Alignment Check
  - Step 5: Python Tool Code (`python_code.py`) & Session Context Memory Bloat Audit
  - Step 6: Tool Return Statement (`return` Contract) Audit & HITL Filtering Summary
"""

import argparse
import ast
import csv
import datetime
import json
import os
import re
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Ensure we can import `config.py` from the parent skill directory
SKILL_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = SKILL_DIR.parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

import config as skill_config


# ==============================================================================
# STEP 1: DYNAMIC BIGQUERY EXTRACTION QUERY (MULTI-SCHEMA & AUTO-DISCOVERY)
# ==============================================================================

def auto_detect_bq_schema(table_name: str) -> Dict[str, Optional[str]]:
    """
    Inspects the BigQuery table schema via `bq show --schema --format=json`
    and automatically maps column names and schema mode ('ccai_chunks_json' vs 'flat_columns').
    """
    # Convert `project.dataset.table` -> `project:dataset.table` for `bq show`
    bq_table_ref = table_name.strip("`")
    parts = bq_table_ref.split(".")
    if len(parts) == 3:
        bq_table_ref = f"{parts[0]}:{parts[1]}.{parts[2]}"

    env = os.environ.copy()
    existing_metrics = env.get("CLOUDSDK_METRICS_ENVIRONMENT", "")
    env["CLOUDSDK_METRICS_ENVIRONMENT"] = f"{existing_metrics} datacloud.antigravity".strip()

    mapping: Dict[str, Optional[str]] = {
        "mode": "ccai_chunks_json",
        "col_conv": getattr(skill_config, "BQ_COL_CONVERSATION_ID", "conversationName"),
        "col_ts": getattr(skill_config, "BQ_COL_TIMESTAMP", "startTimestamp"),
        "col_role": getattr(skill_config, "BQ_COL_AGENT_ROLE", "role"),
        "col_tool": getattr(skill_config, "BQ_COL_TOOL_NAME", "tool_call_display_name"),
        "col_payload": getattr(skill_config, "BQ_COL_PAYLOAD", "chunks"),
        "payload_type": "JSON",
    }

    try:
        proc = subprocess.run(
            ["bq", "show", "--schema", "--format=json", bq_table_ref],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            print(f"[WARN] Auto-schema detection fallback (bq show returned {proc.returncode}). Using config defaults.")
            return mapping

        schema_cols = json.loads(proc.stdout)
        col_lookup = {c["name"].lower(): c for c in schema_cols if isinstance(c, dict) and "name" in c}

        def match_col(candidates: List[str], default_val: Optional[str]) -> Optional[str]:
            for cand in candidates:
                if cand.lower() in col_lookup:
                    return col_lookup[cand.lower()]["name"]
            return default_val

        mapping["col_conv"] = match_col(
            ["conversationName", "conversation_id", "session_id", "interaction_id", "call_id", "dialog_id", "id"],
            mapping["col_conv"],
        )
        mapping["col_ts"] = match_col(
            ["startTimestamp", "timestamp", "event_timestamp", "created_at", "turn_timestamp", "request_time", "date"],
            mapping["col_ts"],
        )
        mapping["col_role"] = match_col(
            ["role", "agent_role", "sub_agent", "agent_name", "flow_name", "playbook_name"],
            None,
        )
        mapping["col_tool"] = match_col(
            ["tool_call_display_name", "tool_name", "function_name", "action_name", "tool_id", "tool"],
            mapping["col_tool"],
        )

        # Determine if nested CCAI chunks column exists vs flat tool response column
        nested_col = match_col(["chunks", "turn_payload", "event_payload"], None)
        flat_col = match_col(
            ["distinct_tool_response", "tool_response", "response_payload", "tool_output", "output_json", "response", "payload", "result"],
            None,
        )
        if nested_col and not flat_col:
            mapping["mode"] = "ccai_chunks_json"
            mapping["col_payload"] = nested_col
            mapping["payload_type"] = col_lookup[nested_col.lower()].get("type", "JSON").upper()
        elif flat_col:
            mapping["mode"] = "flat_columns"
            mapping["col_payload"] = flat_col
            mapping["payload_type"] = col_lookup[flat_col.lower()].get("type", "STRING").upper()

        print(
            f"[INFO] Auto-detected BigQuery schema mode='{mapping['mode']}' | "
            f"conv='{mapping['col_conv']}', ts='{mapping['col_ts']}', role='{mapping['col_role']}', "
            f"tool='{mapping['col_tool']}', payload='{mapping['col_payload']}' ({mapping['payload_type']})"
        )
    except Exception as exc:
        print(f"[WARN] Auto-schema detection encountered error ({exc}); using config defaults.")

    return mapping


def build_sop_bigquery_sql(
    table_name: str,
    start_date: str,
    agent_role: str,
    tool_name: str,
    row_limit: int,
    schema_mode: Optional[str] = None,
) -> str:
    """
    Builds the BigQuery SQL query dynamically based on `BQ_SCHEMA_MODE` and column mappings in `config.py`.
    Supports:
      1. 'ccai_chunks_json' : Standard CXAS / CCAI Insights nested JSON (`chunks`) schema (default)
      2. 'flat_columns'     : Any flat BigQuery table with direct tool name and tool response columns
      3. 'auto'             : Automatically inspects table schema via `bq show --schema` and builds SQL
      4. 'custom_sql'       : Uses user-supplied `CUSTOM_BQ_SQL_QUERY` template from `config.py`
    """
    mode = (schema_mode or getattr(skill_config, "BQ_SCHEMA_MODE", "ccai_chunks_json")).strip().lower()

    # Mode 4: Custom SQL Query Override
    custom_sql = getattr(skill_config, "CUSTOM_BQ_SQL_QUERY", None)
    if mode == "custom_sql" and custom_sql:
        return custom_sql.format(
            table=table_name,
            agent_role=agent_role,
            tool_name=tool_name,
            start_date=start_date,
            row_limit=row_limit,
        )

    # Resolve column mappings (either via 'auto' discovery or config.py settings)
    if mode == "auto":
        detected = auto_detect_bq_schema(table_name)
        mode = str(detected.get("mode") or "ccai_chunks_json")
        col_conv = detected.get("col_conv") or "conversationName"
        col_ts = detected.get("col_ts") or "startTimestamp"
        col_role = detected.get("col_role")
        col_tool = detected.get("col_tool") or "tool_call_display_name"
        col_payload = detected.get("col_payload") or "chunks"
        payload_type = detected.get("payload_type") or "STRING"
    else:
        col_conv = getattr(skill_config, "BQ_COL_CONVERSATION_ID", "conversationName")
        col_ts = getattr(skill_config, "BQ_COL_TIMESTAMP", "startTimestamp")
        col_role = getattr(skill_config, "BQ_COL_AGENT_ROLE", "role")
        col_tool = getattr(skill_config, "BQ_COL_TOOL_NAME", "tool_call_display_name")
        col_payload = getattr(skill_config, "BQ_COL_PAYLOAD", "chunks")
        payload_type = "JSON"

    role_select = f"{col_role} AS role" if col_role else f"'{agent_role}' AS role"
    role_filter = f"  AND {col_role} = '{agent_role}'\n" if (col_role and agent_role) else ""

    # Mode 2: Flat Columns Table (Direct Tool Name + Tool Response Payload Column)
    if mode == "flat_columns":
        payload_expr = (
            f"TO_JSON_STRING({col_payload})"
            if payload_type in ("JSON", "STRUCT", "RECORD")
            else f"CAST({col_payload} AS STRING)"
        )
        return f"""SELECT
  {col_conv} AS conversationName,
  {col_ts} AS startTimestamp,
  {role_select},
  {col_tool} AS tool_call_display_name,
  {payload_expr} AS distinct_tool_response
FROM
  `{table_name}`
WHERE
  {col_payload} IS NOT NULL
  AND DATE({col_ts}) >= '{start_date}'
{role_filter}  AND {col_tool} IN ('{tool_name}')
LIMIT {row_limit};"""

    # Mode 1 (Default): Standard CXAS / CCAI Insights Nested JSON (`chunks`) Schema
    jp_call = getattr(skill_config, "BQ_JSON_PATH_TOOL_CALL", "$.toolCall")
    jp_resp = getattr(skill_config, "BQ_JSON_PATH_TOOL_RESPONSE", "$.toolResponse")
    jp_resp_name = getattr(skill_config, "BQ_JSON_PATH_TOOL_RESP_NAME", "$.toolResponse.displayName")
    jp_resp_data = getattr(skill_config, "BQ_JSON_PATH_TOOL_RESP_DATA", "$.toolResponse.response")

    conv_select = col_conv if col_conv == "conversationName" else f"{col_conv} AS conversationName"
    ts_select = col_ts if col_ts == "startTimestamp" else f"{col_ts} AS startTimestamp"
    role_select_std = "role" if col_role == "role" else role_select
    tool_select = col_tool if col_tool == "tool_call_display_name" else f"{col_tool} AS tool_call_display_name"
    chunks_select = col_payload if col_payload == "chunks" else f"{col_payload} AS chunks"

    return f"""SELECT
  {conv_select},
  {ts_select},
  {role_select_std},
  {tool_select},
  {chunks_select},
  CASE
    WHEN {col_tool} IS NULL THEN TO_JSON_STRING(JSON_QUERY({col_payload}, '{jp_resp_data}'))
    ELSE 'NA'
  END AS distinct_tool_response
FROM
  `{table_name}`
WHERE
  TO_JSON_STRING({col_payload}) != '{{}}'
  AND DATE({col_ts}) >= '{start_date}'
{role_filter}  AND (
    -- Condition: Handle toolCall
    JSON_QUERY({col_payload}, '{jp_call}') IS NOT NULL
    OR (
      -- Condition: toolResponse must match ToolName list
      JSON_QUERY({col_payload}, '{jp_resp}') IS NOT NULL
      AND JSON_VALUE({col_payload}, '{jp_resp_name}')
        IN ('{tool_name}')))
  AND (
    -- Criteria 1: tool_call_display_name matches ToolName list
    {col_tool} IN ('{tool_name}')
    OR (
      -- Criteria 2: tool_call_display_name is NULL, check nested toolResponse displayName
      {col_tool} IS NULL
      AND JSON_VALUE({col_payload}, '{jp_resp_name}')
        IN ('{tool_name}')))
LIMIT {row_limit};"""


def normalize_bq_rows(
    rows: List[Dict[str, Any]],
    default_role: str = "",
    default_tool: str = "",
) -> List[Dict[str, Any]]:
    """
    Normalizes rows from any custom BigQuery table, custom SQL query, or offline file
    so downstream hygiene analysis always has standard keys (`distinct_tool_response`, etc.).
    """
    normalized: List[Dict[str, Any]] = []
    col_conv_cfg = getattr(skill_config, "BQ_COL_CONVERSATION_ID", "conversationName")
    col_ts_cfg = getattr(skill_config, "BQ_COL_TIMESTAMP", "startTimestamp")
    col_role_cfg = getattr(skill_config, "BQ_COL_AGENT_ROLE", "role")
    col_tool_cfg = getattr(skill_config, "BQ_COL_TOOL_NAME", "tool_call_display_name")
    col_payload_cfg = getattr(skill_config, "BQ_COL_PAYLOAD", "chunks")

    for r in rows:
        if not isinstance(r, dict):
            continue
        row_copy = dict(r)

        if "conversationName" not in row_copy:
            for k in (col_conv_cfg, "conversation_id", "session_id", "interaction_id", "call_id", "id"):
                if k and k in row_copy:
                    row_copy["conversationName"] = row_copy[k]
                    break

        if "startTimestamp" not in row_copy:
            for k in (col_ts_cfg, "timestamp", "event_timestamp", "created_at", "turn_timestamp", "date"):
                if k and k in row_copy:
                    row_copy["startTimestamp"] = row_copy[k]
                    break

        if "role" not in row_copy:
            for k in (col_role_cfg, "agent_role", "sub_agent", "agent_name", "flow_name"):
                if k and k in row_copy:
                    row_copy["role"] = row_copy[k]
                    break
            else:
                row_copy["role"] = default_role

        if "tool_call_display_name" not in row_copy:
            for k in (col_tool_cfg, "tool_name", "function_name", "action_name", "tool"):
                if k and k in row_copy:
                    row_copy["tool_call_display_name"] = row_copy[k]
                    break
            else:
                row_copy["tool_call_display_name"] = default_tool

        if "distinct_tool_response" not in row_copy:
            for k in (
                "distinct_tool_response",
                col_payload_cfg,
                "tool_response",
                "response_payload",
                "tool_output",
                "output_json",
                "response",
                "payload",
                "result",
            ):
                if k and k in row_copy and row_copy[k] is not None:
                    val = row_copy[k]
                    if isinstance(val, (dict, list)):
                        # If it's a nested CCAI chunks object, extract $.toolResponse.response if present
                        if isinstance(val, dict) and "toolResponse" in val and isinstance(val["toolResponse"], dict):
                            inner = val["toolResponse"].get("response", val["toolResponse"])
                            row_copy["distinct_tool_response"] = json.dumps(inner, ensure_ascii=False)
                        else:
                            row_copy["distinct_tool_response"] = json.dumps(val, ensure_ascii=False)
                    else:
                        row_copy["distinct_tool_response"] = str(val)
                    break

        normalized.append(row_copy)
    return normalized



def run_bigquery_query(sql: str, max_rows: int = 10000) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Executes the read-only BigQuery SQL query via `bq` CLI with mandatory resource attribution."""
    cmd = [
        "bq",
        "query",
        "--use_legacy_sql=false",
        "--label",
        "datacloud:antigravity",
        "--format=json",
        f"--max_rows={max_rows}",
        sql,
    ]
    env = os.environ.copy()
    existing_metrics = env.get("CLOUDSDK_METRICS_ENVIRONMENT", "")
    env["CLOUDSDK_METRICS_ENVIRONMENT"] = (
        f"{existing_metrics} datacloud.antigravity".strip()
    )

    print("[INFO] Running BigQuery query via bq CLI...")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    except FileNotFoundError:
        print("[ERROR] `bq` CLI executable not found in PATH.", file=sys.stderr)
        sys.exit(1)

    if proc.returncode != 0:
        err_msg = (proc.stderr or proc.stdout or "Unknown bq CLI error").strip()
        print(f"[ERROR] BigQuery execution failed:\n{err_msg}", file=sys.stderr)
        sys.exit(1)

    stdout = proc.stdout.strip()
    if not stdout or stdout == "[]":
        return [], None

    # bq query --format=json may emit status lines before the JSON array
    json_start = stdout.find("[")
    if json_start == -1:
        return [], None
    try:
        rows = json.loads(stdout[json_start:])
        print(f"[INFO] Fetched {len(rows)} total rows from BigQuery.")
        return rows, None
    except json.JSONDecodeError as exc:
        print(f"[ERROR] Failed to parse bq JSON output: {exc}", file=sys.stderr)
        sys.exit(1)


def load_offline_records(filepath: str) -> List[Dict[str, Any]]:
    """Loads pre-exported BigQuery records from a local JSON or CSV file."""
    path = Path(filepath)
    if not path.is_absolute():
        path = WORKSPACE_ROOT / path
    if not path.exists():
        raise FileNotFoundError(f"Offline BigQuery file not found: {path}")

    if path.suffix.lower() == ".json":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else [data]
    elif path.suffix.lower() == ".csv":
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return list(csv.DictReader(f))
    else:
        raise ValueError(f"Unsupported offline file format (use .json or .csv): {path}")


# ==============================================================================
# STEP 0: PRE-TRIAGE & LOCAL AGENT FILE RESOLUTION
# ==============================================================================

def check_exempt_tool_category(tool_name: str) -> Optional[Tuple[str, str]]:
    """Checks if the tool belongs to one of the SOP Step 0 fast-track exempt categories."""
    t_lower = tool_name.lower()
    if tool_name in skill_config.EXEMPT_COT_TOOLS or t_lower == "setcurrentactivities":
        return (
            "EXEMPT_COT_PLATFORM_TOOL",
            "After discussion with VZ team, this tool was developed by the Platform team solely to set Chain-Of-Thought (CoT) reasoning steps (`current_activity`) and returns `{\"result\": \"None\"}` by design. No recommendation needed from tool data hygiene point of view.",
        )
    if (
        tool_name in skill_config.EXEMPT_WEB_SEARCH_DATASTORE_TOOLS
        or "datastore" in t_lower
        or t_lower == "check_destination_coverage"
    ):
        return (
            "EXEMPT_WEB_SEARCH_DATASTORE_TOOL",
            "This tool is a Web Search / Knowledge Base datastore tool. Response length and content depend dynamically on the user query and matched search snippets rather than a fixed backend API contract. No recommendation needed from tool data hygiene point of view.",
        )
    if tool_name in skill_config.EXEMPT_UI_WIDGET_AND_TEMPLATE_TOOLS:
        return (
            "EXEMPT_UI_WIDGET_OR_TEMPLATE_TOOL",
            "This tool is a client-side UI widget or local NGA template setter (`NGARESP_TEMPLATE` / `NGARESP_TEMPLATE_DATAMAP`) and does not call an external backend API. No data hygiene recommendation needed.",
        )
    if tool_name in skill_config.EXEMPT_STATIC_PROMPT_AND_TELEMETRY_TOOLS:
        return (
            "EXEMPT_STATIC_PROMPT_OR_TELEMETRY_TOOL",
            "This tool returns static prompt guardrails or sets session telemetry/utterance variables (`milestone_details`, `user_utterance`, sentiment) without external API payload bloat. No data hygiene recommendation needed.",
        )
    return None


def resolve_local_agent_and_tool_paths(
    app_dir: Path,
    agent_role: str,
    tool_name: str,
    sub_agent_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolves local paths for the sub-agent JSON, instruction.txt, callbacks, and tool code."""
    agents_root = app_dir / "agents"
    tools_root = app_dir / "tools"

    target_folder_name = sub_agent_override or agent_role
    sub_agent_dir: Optional[Path] = None

    if agents_root.exists():
        # Exact match first
        candidate = agents_root / target_folder_name
        if candidate.exists():
            sub_agent_dir = candidate
        else:
            # Case-insensitive or normalized match (e.g. "Adaptive Authentication" -> "Adaptive_Authentication")
            norm_target = re.sub(r"[\s_-]+", "_", target_folder_name.strip()).lower()
            for child in agents_root.iterdir():
                if child.is_dir():
                    norm_child = re.sub(r"[\s_-]+", "_", child.name).lower()
                    if norm_child == norm_target:
                        sub_agent_dir = child
                        break

    tool_dir: Optional[Path] = None
    if tools_root.exists():
        candidate_tool = tools_root / tool_name
        if candidate_tool.exists():
            tool_dir = candidate_tool
        else:
            for child in tools_root.iterdir():
                if child.is_dir() and child.name.lower() == tool_name.lower():
                    tool_dir = child
                    break

    agent_json_path: Optional[Path] = None
    instruction_path: Optional[Path] = None
    callback_files: List[Path] = []

    if sub_agent_dir and sub_agent_dir.exists():
        for f in sub_agent_dir.glob("*.json"):
            agent_json_path = f
            break
        inst_candidate = sub_agent_dir / "instruction.txt"
        if inst_candidate.exists():
            instruction_path = inst_candidate
        for cb_py in sub_agent_dir.rglob("*.py"):
            callback_files.append(cb_py)

    tool_py_path: Optional[Path] = None
    tool_json_path: Optional[Path] = None
    if tool_dir and tool_dir.exists():
        py_candidates = list(tool_dir.rglob("*.py"))
        if py_candidates:
            tool_py_path = py_candidates[0]
        json_candidates = list(tool_dir.glob("*.json"))
        if json_candidates:
            tool_json_path = json_candidates[0]

    # Check if tool is listed in sub-agent's JSON config
    registered_in_agent_json = False
    if agent_json_path and agent_json_path.exists():
        try:
            agent_cfg_text = agent_json_path.read_text(encoding="utf-8", errors="replace")
            if tool_name.lower() in agent_cfg_text.lower():
                registered_in_agent_json = True
        except Exception:
            pass

    return {
        "sub_agent_dir": str(sub_agent_dir) if sub_agent_dir else None,
        "agent_json_path": str(agent_json_path) if agent_json_path else None,
        "instruction_path": str(instruction_path) if instruction_path else None,
        "callback_files": [str(p) for p in callback_files],
        "tool_dir": str(tool_dir) if tool_dir else None,
        "tool_py_path": str(tool_py_path) if tool_py_path else None,
        "tool_json_path": str(tool_json_path) if tool_json_path else None,
        "registered_in_agent_json": registered_in_agent_json,
    }


# ==============================================================================
# STEP 2 & STEP 3: FILTER `distinct_tool_response` & 6-PILLAR HYGIENE ANALYSIS
# ==============================================================================

UI_NOISE_PATTERNS = re.compile(
    r"(height|width|tooltip|image|img|icon|thumbnail|color|css|style|font|template|datamap)",
    re.IGNORECASE,
)
MDN_MTN_PATTERNS = re.compile(r"(mtn|mdn|phone|phonenumber|subscriberid)", re.IGNORECASE)
INSTRUCTION_LEAK_PATTERNS = re.compile(
    r"(rewrite the user response|inform the customer|ask the customer|do not proceed|<template|dynamic_instructions)",
    re.IGNORECASE,
)
TRACEBACK_PATTERNS = re.compile(
    r"(traceback \(most recent call last\)|unboundlocalerror|attributeerror:|typeerror:|keyerror:|valueerror:|nullpointerexception|<html>)",
    re.IGNORECASE,
)


def classify_key_casing(key: str) -> str:
    """Classifies a JSON key into a casing convention."""
    if not key or not re.search(r"[a-zA-Z]", key):
        return "other"
    if "_" in key:
        if key.isupper():
            return "UPPER_SNAKE_CASE"
        if key.islower():
            return "snake_case"
        parts = key.split("_")
        if all(p[:1].isupper() for p in parts if p):
            return "Pascal_Snake_Case"
        return "mixed_snake_case"
    if key.islower():
        return "lowercase"
    if key.isupper():
        return "UPPERCASE"
    if key[0].islower() and any(c.isupper() for c in key[1:]):
        return "camelCase"
    if key[0].isupper() and any(c.islower() for c in key[1:]):
        return "PascalCase"
    return "other"


def inspect_json_tree(
    node: Any,
    depth: int = 1,
    path: str = "$",
) -> Dict[str, Any]:
    """Recursively inspects a parsed JSON object for all 6 hygiene pillars."""
    stats: Dict[str, Any] = {
        "max_depth": depth,
        "total_keys": 0,
        "empty_keys": [],
        "string_booleans": [],
        "currency_strings": [],
        "double_serialized_fields": [],
        "ui_noise_keys": [],
        "mdn_mtn_keys": [],
        "instruction_leak_fields": [],
        "all_keys": [],
    }

    if isinstance(node, dict):
        stats["total_keys"] += len(node)
        for k, v in node.items():
            k_str = str(k)
            current_path = f"{path}.{k_str}"
            stats["all_keys"].append(k_str)

            if UI_NOISE_PATTERNS.search(k_str):
                stats["ui_noise_keys"].append(current_path)
            if MDN_MTN_PATTERNS.search(k_str):
                stats["mdn_mtn_keys"].append(current_path)

            # Check empty / null / NaN values
            if v is None or v == "" or v == [] or v == {}:
                stats["empty_keys"].append(current_path)
            elif isinstance(v, str):
                v_strip = v.strip()
                if v_strip.lower() in ("null", "none", "nan", "n/a"):
                    stats["empty_keys"].append(current_path)
                if v_strip.lower() in ("true", "false", "y", "n"):
                    stats["string_booleans"].append(f"{current_path}={repr(v_strip)}")
                if re.match(r"^-?\$[0-9,]+(\.[0-9]{1,2})?$", v_strip):
                    stats["currency_strings"].append(f"{current_path}={repr(v_strip)}")
                if (
                    (v_strip.startswith("{") and v_strip.endswith("}"))
                    or (v_strip.startswith("[") and v_strip.endswith("]"))
                ) and ('\\"' in v_strip or len(v_strip) > 20):
                    try:
                        parsed_inner = json.loads(v_strip)
                        if isinstance(parsed_inner, (dict, list)):
                            stats["double_serialized_fields"].append(current_path)
                    except Exception:
                        pass
                if INSTRUCTION_LEAK_PATTERNS.search(v_strip):
                    stats["instruction_leak_fields"].append(current_path)

            if isinstance(v, (dict, list)):
                child = inspect_json_tree(v, depth + 1, current_path)
                stats["max_depth"] = max(stats["max_depth"], child["max_depth"])
                stats["total_keys"] += child["total_keys"]
                stats["empty_keys"].extend(child["empty_keys"])
                stats["string_booleans"].extend(child["string_booleans"])
                stats["currency_strings"].extend(child["currency_strings"])
                stats["double_serialized_fields"].extend(child["double_serialized_fields"])
                stats["ui_noise_keys"].extend(child["ui_noise_keys"])
                stats["mdn_mtn_keys"].extend(child["mdn_mtn_keys"])
                stats["instruction_leak_fields"].extend(child["instruction_leak_fields"])
                stats["all_keys"].extend(child["all_keys"])

    elif isinstance(node, list):
        for idx, item in enumerate(node):
            if isinstance(item, (dict, list)):
                child = inspect_json_tree(item, depth + 1, f"{path}[{idx}]")
                stats["max_depth"] = max(stats["max_depth"], child["max_depth"])
                stats["total_keys"] += child["total_keys"]
                stats["empty_keys"].extend(child["empty_keys"])
                stats["string_booleans"].extend(child["string_booleans"])
                stats["currency_strings"].extend(child["currency_strings"])
                stats["double_serialized_fields"].extend(child["double_serialized_fields"])
                stats["ui_noise_keys"].extend(child["ui_noise_keys"])
                stats["mdn_mtn_keys"].extend(child["mdn_mtn_keys"])
                stats["instruction_leak_fields"].extend(child["instruction_leak_fields"])
                stats["all_keys"].extend(child["all_keys"])

    return stats


def analyze_distinct_tool_responses(
    raw_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Filters out `'NA'` rows (Step 2) and runs 6-pillar hygiene analysis (Step 3)."""
    na_tool_call_rows: List[Dict[str, Any]] = []
    valid_response_rows: List[Dict[str, Any]] = []

    for row in raw_rows:
        resp_val = row.get("distinct_tool_response")
        if resp_val is None or str(resp_val).strip() in ("NA", ""):
            na_tool_call_rows.append(row)
        else:
            valid_response_rows.append(row)

    if not valid_response_rows:
        return {
            "total_bq_rows": len(raw_rows),
            "na_tool_call_rows_ignored": len(na_tool_call_rows),
            "valid_tool_response_rows": 0,
            "hygiene_verdict": "NO_RESPONSE_RECORDS_FOUND",
            "verdict_summary": "0 non-'NA' toolResponse rows were returned from BigQuery for the configured role, tool, and date range.",
        }

    char_lengths: List[int] = []
    est_tokens_list: List[float] = []
    bpe_tokens_list: List[int] = []
    key_counts: List[int] = []
    empty_key_counts: List[int] = []
    nesting_depths: List[int] = []

    pattern_clusters: Dict[str, Dict[str, Any]] = {}
    all_observed_keys: Set[str] = set()
    all_Double_serialized: Set[str] = set()
    all_ui_noise: Set[str] = set()
    all_mdn_keys: Set[str] = set()
    all_string_booleans: Set[str] = set()
    all_currency_strings: Set[str] = set()
    all_instruction_leaks: Set[str] = set()
    traceback_samples: List[Dict[str, str]] = []
    empty_result_count = 0

    for row in valid_response_rows:
        raw_str = str(row.get("distinct_tool_response", "")).strip()
        convo_id = str(row.get("conversationName", ""))
        char_len = len(raw_str)
        char_lengths.append(char_len)
        est_tokens_list.append(round(char_len / 4.0, 1))
        bpe_tokens_list.append(len(re.findall(r"\w+|[^\w\s]", raw_str)))

        if TRACEBACK_PATTERNS.search(raw_str):
            if len(traceback_samples) < 5:
                traceback_samples.append({"conversationName": convo_id, "sample": raw_str[:400]})

        try:
            parsed = json.loads(raw_str)
        except Exception:
            parsed = None

        if isinstance(parsed, dict):
            if parsed == {"result": ""} or parsed == {}:
                empty_result_count += 1
            tree_stats = inspect_json_tree(parsed)
            key_counts.append(tree_stats["total_keys"])
            empty_key_counts.append(len(tree_stats["empty_keys"]))
            nesting_depths.append(tree_stats["max_depth"])
            all_observed_keys.update(tree_stats["all_keys"])
            all_Double_serialized.update(tree_stats["double_serialized_fields"])
            all_ui_noise.update(tree_stats["ui_noise_keys"])
            all_mdn_keys.update(tree_stats["mdn_mtn_keys"])
            all_string_booleans.update(tree_stats["string_booleans"][:20])
            all_currency_strings.update(tree_stats["currency_strings"][:20])
            all_instruction_leaks.update(tree_stats["instruction_leak_fields"])

            # Signature pattern key for clustering
            res_val = parsed.get("result")
            if isinstance(res_val, str) and len(res_val) < 80:
                sig = f"result={repr(res_val)}"
            elif isinstance(res_val, dict):
                top_res_keys = sorted(list(res_val.keys()))[:8]
                sig = f"result_dict_keys={top_res_keys}"
            else:
                sig = f"top_keys={sorted(list(parsed.keys()))[:8]}"
        else:
            key_counts.append(0)
            empty_key_counts.append(0)
            nesting_depths.append(1)
            sig = f"raw_non_dict:{raw_str[:60]}"

        if sig not in pattern_clusters:
            pattern_clusters[sig] = {
                "count": 0,
                "sample_conversationName": convo_id,
                "sample_response": raw_str[:600],
            }
        pattern_clusters[sig]["count"] += 1

    # Casing distribution across observed keys
    casing_breakdown: Dict[str, List[str]] = {}
    for k in sorted(all_observed_keys):
        c_style = classify_key_casing(k)
        casing_breakdown.setdefault(c_style, []).append(k)

    mean_chars = round(statistics.mean(char_lengths), 1)
    max_chars = max(char_lengths)
    min_chars = min(char_lengths)
    median_chars = round(statistics.median(char_lengths), 1)
    mean_tokens = round(statistics.mean(est_tokens_list), 1)
    max_tokens = round(max(est_tokens_list), 1)
    mean_keys = round(statistics.mean(key_counts), 1) if key_counts else 0.0
    mean_empty_keys = round(statistics.mean(empty_key_counts), 1) if empty_key_counts else 0.0
    empty_ratio = round(mean_empty_keys / mean_keys, 3) if mean_keys > 0 else 0.0
    max_depth = max(nesting_depths) if nesting_depths else 1

    # Evaluate Hygiene Issues & Apply HITL Filter #3 & #4
    issues_found: List[str] = []
    if max_chars > skill_config.MAX_RECOMMENDED_CHARS:
        issues_found.append(
            f"TOKEN_BLOAT: Max payload size is {max_chars:,} chars (~{max_tokens:,.0f} tokens), exceeding the {skill_config.MAX_RECOMMENDED_CHARS:,}-char guideline."
        )
    if max_depth > skill_config.MAX_RECOMMENDED_NESTING_DEPTH:
        issues_found.append(
            f"DEEP_JSON_NESTING: Maximum nesting depth is {max_depth} levels (recommended <= {skill_config.MAX_RECOMMENDED_NESTING_DEPTH} levels)."
        )
    if all_Double_serialized:
        issues_found.append(
            f"DOUBLE_JSON_SERIALIZATION: Escaped stringified JSON detected in fields: {sorted(list(all_Double_serialized))[:5]}."
        )
    if traceback_samples:
        issues_found.append(
            f"EXCEPTION_TRACEBACK_LEAK: {len(traceback_samples)} sample(s) contain raw exception/error tracebacks."
        )
    if all_instruction_leaks:
        issues_found.append(
            f"INSTRUCTION_LEAKAGE_IN_PAYLOAD: Dynamic prompt/rewriting instructions detected inside data fields: {sorted(list(all_instruction_leaks))}."
        )
    if empty_result_count == len(valid_response_rows):
        issues_found.append(
            "SILENT_EMPTY_STRING_RESPONSE: 100% of responses returned empty `{\"result\": \"\"}`."
        )
    elif empty_ratio >= skill_config.HIGH_EMPTY_FIELD_RATIO_THRESHOLD and mean_keys >= 8:
        issues_found.append(
            f"HIGH_EMPTY_NULL_FIELD_RATIO: {empty_ratio * 100:.1f}% of keys ({mean_empty_keys:.1f}/{mean_keys:.1f} keys) are empty strings, null, or empty arrays."
        )
    if len(all_mdn_keys) >= 3:
        issues_found.append(
            f"REDUNDANT_ENTITY_KEYS: Multiple MDN/MTN key variations detected ({sorted(list(all_mdn_keys))})."
        )
    if len(all_ui_noise) >= 3 and max_chars > skill_config.COMPACT_CLEAN_CHAR_LIMIT:
        issues_found.append(
            f"UI_LAYOUT_POLLUTION: Non-conversational UI/template fields detected ({sorted(list(all_ui_noise))[:6]})."
        )

    # HITL Filter #3 & #4: Compact, well-formed JSON responses require NO data hygiene recommendation
    if (
        not issues_found
        and max_chars <= skill_config.MAX_RECOMMENDED_CHARS
        and max_depth <= skill_config.MAX_RECOMMENDED_NESTING_DEPTH
    ):
        hygiene_verdict = "CLEAN_NO_RECOMMENDATION_NEEDED"
        verdict_summary = (
            "All good from tool response data hygiene point of view. The tool is returning a compact, "
            f"clean JSON format (avg {mean_chars} chars / ~{mean_tokens} tokens, max nesting depth {max_depth}). "
            "No tool response data hygiene recommendation needed."
        )
    else:
        hygiene_verdict = "ISSUES_IDENTIFIED"
        verdict_summary = (
            f"Identified {len(issues_found)} tool response data hygiene issue(s) across {len(valid_response_rows)} valid response records."
        )

    sorted_clusters = sorted(
        [{"pattern": k, **v} for k, v in pattern_clusters.items()],
        key=lambda x: x["count"],
        reverse=True,
    )

    return {
        "total_bq_rows": len(raw_rows),
        "na_tool_call_rows_ignored": len(na_tool_call_rows),
        "valid_tool_response_rows": len(valid_response_rows),
        "hygiene_verdict": hygiene_verdict,
        "verdict_summary": verdict_summary,
        "metrics": {
            "mean_chars": mean_chars,
            "median_chars": median_chars,
            "min_chars": min_chars,
            "max_chars": max_chars,
            "mean_est_tokens": mean_tokens,
            "max_est_tokens": max_tokens,
            "mean_bpe_tokens": round(statistics.mean(bpe_tokens_list), 1),
            "mean_total_keys": mean_keys,
            "mean_empty_keys": mean_empty_keys,
            "empty_key_percentage": round(empty_ratio * 100, 1),
            "max_nesting_depth": max_depth,
            "empty_result_records_count": empty_result_count,
        },
        "issues_found": issues_found,
        "double_serialized_fields": sorted(list(all_Double_serialized)),
        "ui_noise_keys": sorted(list(all_ui_noise)),
        "mdn_mtn_keys": sorted(list(all_mdn_keys)),
        "string_booleans_sample": sorted(list(all_string_booleans))[:15],
        "currency_strings_sample": sorted(list(all_currency_strings))[:15],
        "instruction_leak_fields": sorted(list(all_instruction_leaks)),
        "traceback_samples": traceback_samples,
        "casing_styles_detected": {k: v[:10] for k, v in casing_breakdown.items()},
        "observed_response_keys": sorted(list(all_observed_keys)),
        "response_pattern_clusters": sorted_clusters[:10],
    }


# ==============================================================================
# STEP 4: AGENT INSTRUCTION (`instruction.txt`) ALIGNMENT CHECK
# ==============================================================================

_INSTRUCTION_STOPWORDS = {
    "if", "else", "then", "and", "or", "not", "true", "false", "null", "none",
    "yes", "no", "y", "n", "pass", "fail", "success", "failure", "error",
    "step", "subtask", "task", "guideline", "guidelines", "note", "example",
    "user", "agent", "customer", "tool", "response", "result", "value", "values",
    "item", "items", "list", "following", "variables", "variable", "only",
    "display", "extract", "match", "from", "the", "in", "to", "for", "with",
    "byod", "imei", "esim", "psim", "sim", "VZ", "vz", "chat", "line",
    "user_utterance", "feedback_type", "positive", "negative",
}


def _find_closest_key_suggestions(target_var: str, candidate_keys: List[str]) -> List[str]:
    """Finds semantically similar keys in `candidate_keys` for a missing instruction variable."""
    if not candidate_keys:
        return []
    t_low = target_var.lower()
    suggestions: List[str] = []

    # Domain-specific synonym hints common in VZ CXAS tools
    synonym_groups = [
        ({"devicemodel", "model", "modelname", "device_model"}, {"productname", "modelname", "devicename", "make", "model"}),
        ({"eligiblelines", "eligible_lines", "eligiblelinesfound"}, {"eligible_lines_found", "eligiblelines", "line_info_list"}),
        ({"gurueligible", "guru_eligible"}, {"gurueligible", "guru_eligible", "assisted", "nsaeligible"}),
        ({"mdn", "mtn", "phonenumber"}, {"mtn", "mdn", "formatedmtn", "formattedmtn", "mdn_to_activate"}),
        ({"inactivedeviceid", "deviceid"}, {"inactivedeviceid", "deviceid", "imei"}),
    ]
    t_norm = re.sub(r"[^a-z0-9]", "", t_low)
    for triggers, targets in synonym_groups:
        if t_norm in triggers:
            for ck in candidate_keys:
                ck_norm = re.sub(r"[^a-z0-9]", "", ck.lower())
                if ck_norm in targets and ck not in suggestions:
                    suggestions.append(ck)

    # Substring / token overlap fallback
    t_tokens = set(re.findall(r"[a-z]+", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", target_var).lower()))
    t_tokens -= {"get", "list", "id", "byod", "the", "to", "in"}
    for ck in candidate_keys:
        ck_tokens = set(re.findall(r"[a-z]+", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", ck).lower()))
        if t_tokens and (t_tokens & ck_tokens) and ck not in suggestions:
            suggestions.append(ck)

    return suggestions[:4]


def _extract_instruction_variables_from_block(block_lines: List[Tuple[int, str]], tool_name: str) -> List[Dict[str, Any]]:
    """Extracts all variable/field names referenced in an instruction block around a tool invocation."""
    extracted: Dict[str, Dict[str, Any]] = {}

    def _record_var(var_name: str, line_no: int, ref_type: str, raw_context: str) -> None:
        clean = var_name.strip().strip("`'\".,:;()[]{}")
        if not clean or len(clean) < 2:
            return
        if clean.startswith("@") or clean.lower() == tool_name.lower():
            return
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_.]*$", clean):
            return
        base_clean = clean.split(".")[-1]
        if base_clean.lower() in _INSTRUCTION_STOPWORDS:
            return
        # Ignore ALL_CAPS flow/subtask constants unless they look like schema keys
        if base_clean.isupper() and len(base_clean) > 3 and "_" not in base_clean:
            return

        if base_clean not in extracted:
            extracted[base_clean] = {
                "variable": base_clean,
                "full_reference": clean,
                "lines": [line_no],
                "reference_types": [ref_type],
                "contexts": [raw_context.strip()],
            }
        else:
            if line_no not in extracted[base_clean]["lines"]:
                extracted[base_clean]["lines"].append(line_no)
            if ref_type not in extracted[base_clean]["reference_types"]:
                extracted[base_clean]["reference_types"].append(ref_type)

    in_variable_bullet_list = False
    for ln_no, raw_line in block_lines:
        stripped = raw_line.strip()
        low = stripped.lower()

        # Skip lines that explicitly invoke or reference a DIFFERENT tool's response
        other_tools_in_line = [
            m.group(1).strip()
            for m in re.finditer(r"\{@TOOL:\s*([^}]+)\}", stripped, re.IGNORECASE)
            if m.group(1).strip().lower() != tool_name.lower()
        ]
        if other_tools_in_line and tool_name.lower() not in low:
            in_variable_bullet_list = False
            continue
        if ("response from " in low or "tool response from " in low) and tool_name.lower() not in low:
            in_variable_bullet_list = False
            continue

        # Detect start/end of a bulleted variable list (e.g., "display only the following variables:")
        if any(phrase in low for phrase in ("following variables", "following fields", "these variables", "below variables")):
            in_variable_bullet_list = True

        if in_variable_bullet_list and re.match(r"^[-*•]\s*([a-zA-Z_][a-zA-Z0-9_]*)(?:\s*$|\s*\()", stripped):
            m_bul = re.match(r"^[-*•]\s*([a-zA-Z_][a-zA-Z0-9_]*)(?:\s*$|\s*\()", stripped)
            if m_bul:
                _record_var(m_bul.group(1), ln_no, "bullet_variable_list", stripped)
                continue
        elif in_variable_bullet_list and stripped and not stripped.startswith(("-", "*", "•")):
            # End bullet list once indentation/bullets stop
            if not any(phrase in low for phrase in ("following variables", "following fields")):
                in_variable_bullet_list = False

        # 1. Curly brace placeholders `{var_name}` (excluding `{@TOOL:...}` and `{@AGENT:...}`)
        for m_curly in re.finditer(r"\{([a-zA-Z_][a-zA-Z0-9_.]*)\}", stripped):
            _record_var(m_curly.group(1), ln_no, "curly_brace_placeholder `{var}`", stripped)

        # 2. Square bracket template placeholders `[var_name]` (e.g., `[deviceModel] - [inactiveDeviceId]`)
        for m_sq in re.finditer(r"\[([a-zA-Z_][a-zA-Z0-9_.]*)\]", stripped):
            cand = m_sq.group(1)
            # Filter out markdown links or generic words like `[Wait]`
            if cand[0].islower() or "_" in cand or any(c.isupper() for c in cand[1:]):
                _record_var(cand, ln_no, "template_placeholder `[var]`", stripped)

        # 3. Backticked variable/key names `` `var_name` ``
        for m_bt in re.finditer(r"`([a-zA-Z_][a-zA-Z0-9_.]*)`", stripped):
            cand = m_bt.group(1)
            if cand.lower() != tool_name.lower():
                _record_var(cand, ln_no, "backtick_reference `` `var` ``", stripped)

        # 4. Single/double-quoted field names next to keywords like extract/field/key/value/variable
        for m_q in re.finditer(
            r"(?:extract|field|key|variable|check|item\s+in|value\s+of)\s+(?:the\s+)?['\"]([a-zA-Z_][a-zA-Z0-9_.]*)['\"]",
            stripped,
            re.IGNORECASE,
        ):
            _record_var(m_q.group(1), ln_no, "quoted_field_extraction", stripped)
        for m_q2 in re.finditer(
            r"['\"]([a-zA-Z_][a-zA-Z0-9_.]*)['\"]\s+(?:value|field|key|list|variable)",
            stripped,
            re.IGNORECASE,
        ):
            _record_var(m_q2.group(1), ln_no, "quoted_field_extraction", stripped)

        # 5. Conditional equality/inequality checks `var == "val"` or `var != "val"`
        for m_cond in re.finditer(
            r"\b([a-zA-Z_][a-zA-Z0-9_.]*)\s*(?:==|!=)\s*['\"]([^'\"]+)['\"]",
            stripped,
        ):
            _record_var(m_cond.group(1), ln_no, f"condition_check (`== \"{m_cond.group(2)}\"`)", stripped)

    return list(extracted.values())


def audit_agent_instructions(
    instruction_path: Optional[str],
    tool_name: str,
    observed_response_keys: List[str],
    code_context_vars_set: List[str],
    code_returned_keys: Optional[List[str]] = None,
    code_nested_context_keys: Optional[List[str]] = None,
    code_unreachable_context_vars: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Audits `instruction.txt` for tool invocation syntax, typos, and variable alignment against Tool Response & Context Session."""
    if not instruction_path or not Path(instruction_path).exists():
        return {
            "instruction_file_found": False,
            "status": "INSTRUCTION_FILE_NOT_FOUND",
            "findings": ["Could not locate `instruction.txt` for the specified sub-agent."],
            "matched_blocks": [],
            "variable_alignment_matrix": [],
            "missing_from_both_response_and_context": [],
            "resolved_via_context_only": [],
        }

    text = Path(instruction_path).read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    # 1. Exact `{@TOOL: tool_name}` syntax matches
    exact_tag_pattern = re.compile(rf"\{{@TOOL:\s*{re.escape(tool_name)}\s*\}}", re.IGNORECASE)
    # 2. Any mention of tool_name
    any_mention_pattern = re.compile(re.escape(tool_name), re.IGNORECASE)
    # 3. Prefix/fuzzy check to catch typos like `get_case` instead of `get_case_activations`
    prefix_parts = tool_name.split("_")
    fuzzy_prefix = "_".join(prefix_parts[:2]) if len(prefix_parts) >= 2 else tool_name

    matched_blocks: List[Dict[str, Any]] = []
    block_line_tuples: List[Tuple[int, str]] = []
    findings: List[str] = []

    for idx, line in enumerate(lines):
        if any_mention_pattern.search(line):
            # Walk backward to find the start of the enclosing <step> (up to 8 lines back)
            start_l = max(0, idx - 6)
            for b in range(idx, max(-1, idx - 10), -1):
                if "<step>" in lines[b].lower() or "<subtask>" in lines[b].lower():
                    start_l = b
                    break

            # Walk forward to include the current <step> AND any immediately following <step> in the same
            # <subtask> that consumes the output before another `{@TOOL:` is called (up to 28 lines forward)
            max_end = min(len(lines), idx + 28)
            stop_l = min(len(lines), idx + 16)
            steps_closed = 0
            for j in range(idx + 1, max_end):
                lj_low = lines[j].lower()
                if "</subtask>" in lj_low or "</task>" in lj_low:
                    stop_l = j + 1
                    break
                # If a subsequent step invokes a DIFFERENT tool, stop before that line
                other_tool_match = re.search(r"\{@TOOL:\s*([^}]+)\}", lines[j], re.IGNORECASE)
                if steps_closed >= 1 and other_tool_match and other_tool_match.group(1).strip().lower() != tool_name.lower():
                    stop_l = j
                    break
                if "</step>" in lj_low:
                    steps_closed += 1
                    stop_l = j + 1
                    if steps_closed >= 2:
                        break

            snippet = "\n".join(f"L{i+1}: {lines[i]}" for i in range(start_l, stop_l))
            # Extract post-tool variables starting from the tool invocation line `idx` through `stop_l`
            for i in range(idx, stop_l):
                block_line_tuples.append((i + 1, lines[i]))

            has_proper_tag = bool(exact_tag_pattern.search(line))
            matched_blocks.append(
                {
                    "line_number": idx + 1,
                    "line_content": line.strip(),
                    "uses_proper_tool_tag_syntax": has_proper_tag,
                    "surrounding_block": snippet,
                }
            )
            line_lower = line.lower()
            is_response_ref = "tool response" in line_lower or "response from" in line_lower
            is_invocation_attempt = any(
                kw in line_lower for kw in ("invoke", "call ", "execute ", "from the ")
            )
            if not has_proper_tag and is_invocation_attempt and not is_response_ref:
                findings.append(
                    f"SYNTAX_WARNING (Line {idx+1}): Tool `{tool_name}` appears to be invoked without standard `{{@TOOL: {tool_name}}}` syntax: `{line.strip()}`"
                )

    if not matched_blocks:
        # Check for prefix typo (e.g., `get_case` when tool is `get_case_activations`)
        typo_matches = []
        if len(fuzzy_prefix) >= 6:
            typo_regex = re.compile(
                rf"(\{{@TOOL:\s*{re.escape(fuzzy_prefix)}[a-zA-Z0-9_]*|`{re.escape(fuzzy_prefix)}[a-zA-Z0-9_]*`)",
                re.IGNORECASE,
            )
            for idx, line in enumerate(lines):
                m = typo_regex.search(line)
                if m:
                    typo_matches.append((idx + 1, m.group(0), line.strip()))
        if typo_matches:
            for ln_no, matched_tok, full_ln in typo_matches:
                findings.append(
                    f"POTENTIAL_TOOL_NAME_TYPO (Line {ln_no}): Tool `{tool_name}` is not found verbatim, but found `{matched_tok}` in `{full_ln}`. Recommend using `{{@TOOL: {tool_name}}}`."
                )
        else:
            findings.append(
                f"NOT_INVOKED_IN_INSTRUCTIONS: Tool `{tool_name}` is not referenced anywhere in `{Path(instruction_path).name}`. Check if it is invoked via a callback or if it is deprecated/unused."
            )

    # Build lookup maps for Tool Response Keys and Session Context Variables
    returned_keys = code_returned_keys or []
    nested_ctx_keys = code_nested_context_keys or []
    unreachable_ctx = code_unreachable_context_vars or {}

    all_response_keys = list(dict.fromkeys(list(observed_response_keys) + list(returned_keys)))
    response_exact_set = set(all_response_keys)
    response_lower_map: Dict[str, str] = {k.lower(): k for k in all_response_keys}

    all_context_keys = list(dict.fromkeys(list(code_context_vars_set) + list(nested_ctx_keys)))
    context_exact_set = set(all_context_keys)
    context_lower_map: Dict[str, str] = {k.lower(): k for k in all_context_keys}

    # Extract all variables referenced in the instruction block(s) for this tool
    extracted_vars = _extract_instruction_variables_from_block(block_line_tuples, tool_name)

    variable_alignment_matrix: List[Dict[str, Any]] = []
    missing_from_both: List[Dict[str, Any]] = []
    resolved_via_context_only: List[Dict[str, Any]] = []

    for vinfo in extracted_vars:
        var_name = vinfo["variable"]
        v_low = var_name.lower()
        line_refs = ", ".join(f"L{ln}" for ln in vinfo["lines"])
        ref_types_str = ", ".join(vinfo["reference_types"])

        in_resp_exact = var_name in response_exact_set
        in_resp_ci = v_low in response_lower_map
        matched_resp_key = var_name if in_resp_exact else response_lower_map.get(v_low)

        in_ctx_exact = var_name in context_exact_set
        in_ctx_ci = v_low in context_lower_map
        matched_ctx_key = var_name if in_ctx_exact else context_lower_map.get(v_low)

        # Determine alignment status, explanation, and recommendation
        if in_resp_exact and in_ctx_exact:
            status = "ALIGNED_IN_BOTH"
            explanation = (
                f"`{var_name}` is returned in `distinct_tool_response` AND saved in session context (`context.state[\"{matched_ctx_key}\"]`)."
            )
            recommendation = "No change needed — variable is aligned in both tool response and context session."
        elif in_resp_exact and not in_ctx_ci:
            status = "ALIGNED_IN_TOOL_RESPONSE"
            explanation = (
                f"`{var_name}` is returned directly in `distinct_tool_response` (not separately saved as a top-level `context.state` key, or nested inside a list in context)."
            )
            recommendation = "No change needed unless cross-turn persistence is required in `context.state`."
        elif not in_resp_ci and in_ctx_exact:
            if var_name in unreachable_ctx:
                status = "CONTEXT_ONLY_BUT_UNREACHABLE_AT_RUNTIME"
                reason = unreachable_ctx[var_name]
                explanation = (
                    f"`{var_name}` is NOT in `distinct_tool_response`. It is assigned to `context.state[\"{var_name}\"]` in `python_code.py`, "
                    f"BUT that assignment is skipped/unreachable at runtime when {reason}."
                )
                recommendation = (
                    f"Fix the runtime crash in `python_code.py` ({reason}) so `context.state[\"{var_name}\"]` is reliably populated, "
                    f"and also include `\"{var_name}\"` in the tool's `return` dictionary."
                )
                findings.append(
                    f"UNREACHABLE_CONTEXT_VARIABLE (`{var_name}` at {line_refs}): Instruction references `{var_name}`, which is NOT in `distinct_tool_response` and its `context.state[\"{var_name}\"]` assignment in `python_code.py` fails to execute when {reason}."
                )
            else:
                status = "RESOLVED_VIA_CONTEXT_SESSION_ONLY"
                explanation = (
                    f"`{var_name}` is NOT returned in `distinct_tool_response`, but IS saved in the session context window (`context.state[\"{matched_ctx_key}\"]` / `context.set_variable`)."
                )
                recommendation = (
                    f"Keep `context.state[\"{matched_ctx_key}\"]` or also return `\"{var_name}\"` in the tool's `return` dictionary so the LLM can read it directly from the tool response without relying solely on session state injection."
                )
                resolved_via_context_only.append(
                    {
                        "variable": var_name,
                        "lines": vinfo["lines"],
                        "matched_context_key": matched_ctx_key,
                        "explanation": explanation,
                        "recommendation": recommendation,
                    }
                )
        elif in_resp_ci or in_ctx_ci:
            # Casing mismatch (e.g., `guru_eligible` vs `guruEligible`)
            status = "CASING_MISMATCH"
            explanation = (
                f"Casing mismatch: Instruction references `{var_name}`, whereas Tool Response has `{matched_resp_key or 'None'}` and Context Session has `{matched_ctx_key or 'None'}`."
            )
            recommendation = (
                f"Align variable naming casing between `instruction.txt` (`{var_name}`), `distinct_tool_response` (`{matched_resp_key or 'N/A'}`), and `context.state` (`{matched_ctx_key or 'N/A'}`)."
            )
            findings.append(
                f"VARIABLE_CASING_MISMATCH (`{var_name}` at {line_refs}): Instruction uses `{var_name}`, while tool response key is `{matched_resp_key or 'None'}` and context session key is `{matched_ctx_key or 'None'}`."
            )
        else:
            # MISSING FROM BOTH TOOL RESPONSE AND CONTEXT SESSION!
            status = "MISSING_FROM_BOTH_RESPONSE_AND_CONTEXT"
            suggestions = _find_closest_key_suggestions(var_name, all_response_keys + all_context_keys)
            sugg_text = (
                f" Closest available key(s) in tool response/context: `{suggestions}`."
                if suggestions
                else f" Available tool response keys: `{all_response_keys[:12]}`; Context keys: `{all_context_keys}`."
            )
            explanation = (
                f"`{var_name}` (referenced at {line_refs} via {ref_types_str}) is **NOT returned by `distinct_tool_response`** AND **NOT saved in `context.state` / session context** in `python_code.py`.{sugg_text}"
            )
            if suggestions:
                recommendation = (
                    f"Either update `instruction.txt` ({line_refs}) to reference `{suggestions[0]}` instead of `{var_name}`, "
                    f"OR update `python_code.py` to map/include `\"{var_name}\"` in the returned dictionary and `context.state`."
                )
            else:
                recommendation = (
                    f"Update `python_code.py` to populate `\"{var_name}\"` in the tool response / `context.state`, or remove/update `{var_name}` in `instruction.txt` ({line_refs})."
                )
            missing_entry = {
                "variable": var_name,
                "lines": vinfo["lines"],
                "reference_types": vinfo["reference_types"],
                "closest_available_keys": suggestions,
                "explanation": explanation,
                "recommendation": recommendation,
            }
            missing_from_both.append(missing_entry)
            findings.append(
                f"MISSING_VARIABLE_NOT_IN_RESPONSE_OR_CONTEXT (`{var_name}` at {line_refs}): Referenced in `instruction.txt` ({ref_types_str}), but `{var_name}` is NOT returned in `distinct_tool_response` and NOT saved in `context.state`.{sugg_text}"
            )

        variable_alignment_matrix.append(
            {
                "variable": var_name,
                "lines": vinfo["lines"],
                "line_refs": line_refs,
                "reference_types": vinfo["reference_types"],
                "in_tool_response": bool(in_resp_exact or in_resp_ci),
                "matched_response_key": matched_resp_key,
                "in_context_session": bool(in_ctx_exact or in_ctx_ci),
                "matched_context_key": matched_ctx_key,
                "status": status,
                "explanation": explanation,
                "recommendation": recommendation,
            }
        )

    return {
        "instruction_file_found": True,
        "instruction_path": instruction_path,
        "invocation_count": len(matched_blocks),
        "findings": findings,
        "matched_blocks": matched_blocks,
        "variable_alignment_matrix": variable_alignment_matrix,
        "missing_from_both_response_and_context": missing_from_both,
        "resolved_via_context_only": resolved_via_context_only,
    }


# ==============================================================================
# STEP 5 & STEP 6: PYTHON TOOL CODE & RETURN STATEMENT STATIC AUDIT
# ==============================================================================

def audit_python_tool_code(tool_py_path: Optional[str]) -> Dict[str, Any]:
    """Audits `python_code.py` for context memory dumps, return anti-patterns, and code bugs."""
    if not tool_py_path or not Path(tool_py_path).exists():
        return {
            "python_file_found": False,
            "findings": ["No `python_code.py` found for this tool (may be a pure OpenAPI/Datastore/Widget tool)."],
            "recommendations": [],
            "context_vars_set": [],
            "nested_context_keys": [],
            "unreachable_context_vars": {},
            "returned_dict_keys": [],
            "return_statements": [],
        }

    source = Path(tool_py_path).read_text(encoding="utf-8", errors="replace")
    lines = source.splitlines()
    findings: List[str] = []
    recommendations: List[str] = []
    context_vars_set: List[str] = []
    nested_context_keys: List[str] = []
    unreachable_context_vars: Dict[str, str] = {}
    context_vars_get: List[Tuple[int, str]] = []
    context_vars_set_lines: List[Tuple[int, str, str]] = []
    unsafe_bracket_reads: List[Tuple[int, str]] = []
    returned_dict_keys: List[str] = []
    return_statements: List[Dict[str, Any]] = []

    def _add_issue(finding_msg: str, rec_msg: str) -> None:
        if finding_msg not in findings:
            findings.append(finding_msg)
        if rec_msg and rec_msg not in recommendations:
            recommendations.append(rec_msg)

    # 1. Regex & Line-level checks
    for idx, line in enumerate(lines):
        ln_no = idx + 1
        stripped = line.strip()
        if stripped.startswith("#"):
            continue

        # Check `return context.state` (Critical P0 Anti-Pattern from `addsim_byod`)
        if re.search(r"\breturn\s+context\.state\b", stripped):
            _add_issue(
                f"CRITICAL_P0_RETURN_CONTEXT_STATE (Line {ln_no}): `return context.state` dumps the entire session state dictionary back to the agent as the tool response (`{stripped}`).",
                f"Replace `return context.state` on line {ln_no} of `python_code.py` with a minimal dictionary containing only the specific fields required by `instruction.txt` (e.g., `return {{\"status\": \"Success\", ...}}`).",
            )

        # Check `return json.dumps(...)` (Double Serialization Anti-Pattern from `set_flow_milestone`)
        if re.search(r"\breturn\s+json\.dumps\s*\(", stripped):
            _add_issue(
                f"DOUBLE_SERIALIZATION_IN_RETURN (Line {ln_no}): `return json.dumps(...)` causes double JSON serialization (`\\\"`).",
                f"Remove `json.dumps(...)` on line {ln_no} of `python_code.py` and return the native Python `dict` directly so the CES runtime serializes it cleanly once.",
            )

        # Check unsafe chained `.get("...", "").get(` (AttributeError bug from `modify_paper_free_billing`)
        if re.search(r"\.get\([^)]*,\s*['\"']['\"]\s*\)\.get\(", stripped):
            _add_issue(
                f"UNSAFE_DICT_GET_CHAIN (Line {ln_no}): Defaulting `.get(..., \"\")` to an empty string before chaining `.get(...)` raises `AttributeError: 'str' object has no attribute 'get'` when the key is missing.",
                f"Change `.get(..., \"\").get(...)` on line {ln_no} of `python_code.py` to `.get(..., {{}}).get(...)` (defaulting to an empty dict `{{}}` or using `(obj.get(...) or {{}}).get(...)`).",
            )

        # Check unsafe `.get("key").strip()` / `.isdigit()` / `.lower()` without default `""`
        m_none_method = re.search(
            r"\.get\(\s*['\"]([^'\"]+)['\"]\s*\)\.(strip|lower|upper|split|replace|isdigit)\s*\(",
            stripped,
        )
        if m_none_method:
            fld_name, meth_name = m_none_method.group(1), m_none_method.group(2)
            _add_issue(
                f"UNSAFE_NONE_METHOD_CALL (Line {ln_no}): `.get(\"{fld_name}\").{meth_name}()` without a fallback default string raises `AttributeError: 'NoneType' object has no attribute '{meth_name}'` if `\"{fld_name}\"` is missing or `null` in the API payload.",
                f"Update line {ln_no} in `python_code.py` from `.get(\"{fld_name}\").{meth_name}()` to `str(obj.get(\"{fld_name}\") or \"\").{meth_name}()`.",
            )

        # Check hardcoded API key strings
        if re.search(
            r"(['\"](?:apikey|x-soe-apikey)['\"]\s*:\s*['\"][a-zA-Z0-9]{16,}['\"]|headers\.get\([^)]*,\s*['\"][a-zA-Z0-9]{16,}['\"]\))",
            stripped,
            re.IGNORECASE,
        ):
            _add_issue(
                f"HARDCODED_API_CREDENTIAL (Line {ln_no}): Hardcoded API key literal detected (`{stripped[:80]}...`).",
                f"Remove the hardcoded API key fallback on line {ln_no} of `python_code.py` and read credentials strictly from secure session/environment headers.",
            )

        # Check invalid default `"NOT FOUND"` for `soedc`
        if re.search(r"headers\.get\(\s*['\"]soedc['\"]\s*,\s*['\"]NOT FOUND['\"]\s*\)", stripped):
            _add_issue(
                f"INVALID_HEADER_DEFAULT (Line {ln_no}): `soedc` defaults to `\"NOT FOUND\"` instead of `\"\"`, which can break data center routing.",
                f"Change `headers.get(\"soedc\", \"NOT FOUND\")` on line {ln_no} of `python_code.py` to `headers.get(\"soedc\", \"\")`.",
            )

        # Check sensitive `print(request)` or `print(headers)` logging
        if re.search(r"\bprint\s*\(\s*(?:request|headers)\s*\)", stripped):
            _add_issue(
                f"SENSITIVE_REQUEST_LOGGING (Line {ln_no}): `{stripped}` logs raw request/header objects (including auth tokens and customer PII/MTNs) to stdout.",
                f"Remove or redact `{stripped}` on line {ln_no} of `python_code.py` before production deployment.",
            )

        # Track `context.set_variable("var", expr)` or `context.variables["var"] = expr`
        m_set = re.search(
            r"(?:context|callback_context)\.set_variable\(\s*['\"]([^'\"]+)['\"]\s*,\s*(.+)\)",
            stripped,
        )
        if m_set:
            var_name, val_expr = m_set.group(1), m_set.group(2).strip()
            context_vars_set.append(var_name)
            context_vars_set_lines.append((ln_no, var_name, val_expr))
            if val_expr == "{}":
                _add_issue(
                    f"CONTEXT_MEMORY_WIPE_CHECK (Line {ln_no}): `context.set_variable(\"{var_name}\", {{}})` wipes `{var_name}`.",
                    f"Verify line {ln_no} (`context.set_variable(\"{var_name}\", {{}})`) does not overwrite session keys needed by downstream tools/turns.",
                )

        m_var_assign = re.search(
            r"context\.(?:variables|state)\[\s*['\"]([^'\"]+)['\"]\s*\]\s*=\s*(.+)",
            stripped,
        )
        if m_var_assign:
            var_name, val_expr = m_var_assign.group(1), m_var_assign.group(2).strip()
            context_vars_set.append(var_name)
            context_vars_set_lines.append((ln_no, var_name, val_expr))
        else:
            # Check for unsafe direct bracket reads `context.state["key"]` on the RHS (not assignment target)
            for m_br in re.finditer(r"context\.(?:variables|state)\[\s*['\"]([^'\"]+)['\"]\s*\]", stripped):
                read_key = m_br.group(1)
                # Ensure this bracket access is not on the LHS of an assignment
                lhs_part = stripped.split("=")[0] if "=" in stripped and "==" not in stripped else ""
                if f'"{read_key}"' not in lhs_part and f"'{read_key}'" not in lhs_part:
                    unsafe_bracket_reads.append((ln_no, read_key))
                    _add_issue(
                        f"UNSAFE_CONTEXT_STATE_BRACKET_READ (Line {ln_no}): Direct bracket indexing `context.state[\"{read_key}\"]` raises `KeyError: '{read_key}'` whenever `{read_key}` is not yet set in session state, aborting execution of the remaining lines in the `try` block.",
                        f"Replace `context.state[\"{read_key}\"]` on line {ln_no} of `python_code.py` with `context.state.get(\"{read_key}\", \"\")` so missing state keys do not raise `KeyError`.",
                    )

        m_get = re.search(
            r"(?:context|callback_context)\.(?:get_variable|state\.get)\(\s*['\"]([^'\"]+)['\"]",
            stripped,
        )
        if m_get:
            context_vars_get.append((ln_no, m_get.group(1)))

    # If there is an unsafe bracket read inside a try block before a context.state assignment, flag unreachable context vars
    if unsafe_bracket_reads and context_vars_set_lines:
        first_crash_ln, crash_key = unsafe_bracket_reads[0]
        for set_ln, var_name, _ in context_vars_set_lines:
            if set_ln > first_crash_ln:
                unreachable_context_vars[var_name] = (
                    f"`context.state[\"{crash_key}\"]` on line {first_crash_ln} raises `KeyError: '{crash_key}'` before line {set_ln} (`context.state[\"{var_name}\"]`) is reached"
                )

    # Map local variables assigned from `<api>.get("field")` so we can detect duplicate `context.state` assignments
    local_var_origins: Dict[str, str] = {}
    for idx, line in enumerate(lines):
        m_loc = re.match(
            r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*([a-zA-Z_][a-zA-Z0-9_]*\.get\(\s*['\"]([^'\"]+)['\"])",
            line,
        )
        if m_loc:
            local_var_origins[m_loc.group(1)] = f"{m_loc.group(2).split('.get')[0]}.get(\"{m_loc.group(3)}\")"

    # Check if the same expression/list is saved under multiple duplicate keys in `context.state`
    expr_to_ctx_vars: Dict[str, List[Tuple[int, str]]] = defaultdict(list)
    for set_ln, var_name, val_expr in context_vars_set_lines:
        clean_expr = val_expr.strip()
        if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", clean_expr):
            canonical_source = local_var_origins.get(clean_expr, clean_expr)
            expr_to_ctx_vars[canonical_source].append((set_ln, var_name))
    for expr_name, var_entries in expr_to_ctx_vars.items():
        distinct_var_names = sorted({vn for _, vn in var_entries})
        if len(distinct_var_names) > 1:
            ln_list_str = ", ".join(f"L{ln} (`{vn}`)" for ln, vn in var_entries)
            _add_issue(
                f"DUPLICATE_CONTEXT_STATE_ASSIGNMENT ({ln_list_str}): `{expr_name}` is stored redundantly under multiple `context.state` keys ({distinct_var_names}), duplicating session memory footprint.",
                f"Consolidate duplicate `context.state` assignments ({ln_list_str}) in `python_code.py` so only the single key referenced by `instruction.txt` / downstream tools is stored.",
            )

    # Check if `return` reads from `context.state.get("key")` while an `else:` branch doesn't reset `context.state["key"]`
    for idx, line in enumerate(lines):
        m_ret_ctx = re.search(
            r"context\.state\.get\(\s*['\"]([^'\"]+)['\"]",
            line,
        )
        if m_ret_ctx and idx >= max(0, len(lines) - 12):
            ret_ctx_key = m_ret_ctx.group(1)
            set_occurrences = [ln for ln, vn, _ in context_vars_set_lines if vn == ret_ctx_key]
            if len(set_occurrences) == 1 and "else:" in source:
                _add_issue(
                    f"STALE_CONTEXT_STATE_IN_RETURN (Line {idx+1}): `return` statement reads `context.state.get(\"{ret_ctx_key}\")`, which is only assigned in the `if` branch (line {set_occurrences[0]}) and not reset in the `else` branch. When the API returns an empty list/false branch, `return` leaks stale `{ret_ctx_key}` data from a prior turn.",
                    f"Initialize `{ret_ctx_key} = []` (or reset `context.state[\"{ret_ctx_key}\"] = []` in the `else:` branch) in `python_code.py` so stale session data is never returned.",
                )

    # Check if raw API `.json()` variable is dumped directly into `context.set_variable` or `return`
    raw_api_vars: Dict[str, int] = {}
    for idx, line in enumerate(lines):
        m_api = re.search(
            r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(?:tools\.|[a-zA-Z_][a-zA-Z0-9_]*\.json\()",
            line,
        )
        if m_api:
            raw_api_vars[m_api.group(1)] = idx + 1

    for set_ln, var_name, val_expr in context_vars_set_lines:
        clean_expr = val_expr.rstrip(")")
        if clean_expr in raw_api_vars:
            _add_issue(
                f"RAW_API_RESPONSE_CONTEXT_DUMP (Line {set_ln}): Variable `{clean_expr}` (raw API response from line {raw_api_vars[clean_expr]}) is saved directly into session memory via `context.set_variable(\"{var_name}\", {clean_expr})`.",
                f"In `python_code.py` (line {set_ln}), do not save the unpruned API payload `{clean_expr}` into `context.state`/`context.set_variable(\"{var_name}\", ...)`. Filter it down to only the fields required by downstream tools.",
            )

    # Check redundant `set_variable` followed immediately by `get_variable` for the same key
    for set_ln, var_name, _ in context_vars_set_lines:
        for get_ln, get_var in context_vars_get:
            if get_var == var_name and 0 < (get_ln - set_ln) <= 15:
                _add_issue(
                    f"REDUNDANT_CONTEXT_REFETCH (Lines {set_ln} -> {get_ln}): `{var_name}` is saved to context on line {set_ln} and re-fetched via `context.get_variable(\"{var_name}\")` on line {get_ln} while already in local Python scope.",
                    f"Reuse the local Python variable directly on line {get_ln} instead of calling `context.get_variable(\"{var_name}\")` right after setting it on line {set_ln}.",
                )

    # 2. AST Structural Checks (Docstring, Type Hints, Return Type Mismatch, Bare Return, Raw API Return, Dict Return Keys)
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                doc = ast.get_docstring(node)
                if not doc:
                    _add_issue(
                        f"MISSING_DOCSTRING_[T002] (Line {node.lineno}): Function `{node.name}` is missing a Google-style docstring (required by CXAS linter `[T002]` for LLM tool routing).",
                        f"Add a concise Google-style docstring (`\"\"\"...\"\"\"`) to `def {node.name}(...)` on line {node.lineno} of `python_code.py` describing its purpose, args, and returned dict schema.",
                    )
                ret_annotation_str = None
                if node.returns is None:
                    _add_issue(
                        f"MISSING_RETURN_TYPE_ANNOTATION (Line {node.lineno}): Function `{node.name}` is missing an explicit return type annotation.",
                        f"Add `-> dict[str, Any]:` (or `-> dict:`) return type annotation to `def {node.name}(...)` on line {node.lineno} of `python_code.py`.",
                    )
                else:
                    ret_annotation_str = ast.get_source_segment(source, node.returns)

                returns_dict_literal = False
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Return):
                        ret_src = ast.get_source_segment(source, sub) or "return"
                        return_statements.append({"line": sub.lineno, "statement": ret_src.strip()})
                        if sub.value is None:
                            _add_issue(
                                f"BARE_NONE_RETURN (Line {sub.lineno}): Function `{node.name}` executes a bare `return` (returning `None`), which produces `{{\"result\": null}}` or inconsistent return types.",
                                f"Replace bare `return` on line {sub.lineno} of `python_code.py` with a structured dictionary return (e.g., `return {{\"status\": \"Error\", \"message\": \"...\"}}`).",
                            )
                        elif isinstance(sub.value, ast.Name) and sub.value.id in raw_api_vars:
                            _add_issue(
                                f"RAW_API_RESPONSE_RETURNED_DIRECTLY (Line {sub.lineno}): `return {sub.value.id}` returns the unpruned raw API response from line {raw_api_vars[sub.value.id]} directly to the agent.",
                                f"Prune `{sub.value.id}` before `return` on line {sub.lineno} of `python_code.py` so only the keys required by `instruction.txt` are returned.",
                            )
                        elif isinstance(sub.value, ast.Dict):
                            returns_dict_literal = True
                            for k_node in sub.value.keys:
                                if isinstance(k_node, ast.Constant) and isinstance(k_node.value, str):
                                    returned_dict_keys.append(k_node.value)
                    elif isinstance(sub, ast.ExceptHandler):
                        if sub.type is None:
                            _add_issue(
                                f"BARE_EXCEPT_HANDLER (Line {sub.lineno}): Bare `except:` clause detected; catch explicit exception types and return a structured error dictionary.",
                                f"Change bare `except:` on line {sub.lineno} of `python_code.py` to `except Exception as e:` and return a structured error dict.",
                            )

                if ret_annotation_str and ret_annotation_str.strip() in ("str", "int", "bool", "list") and returns_dict_literal:
                    _add_issue(
                        f"RETURN_TYPE_ANNOTATION_MISMATCH (Line {node.lineno}): Function `{node.name}` is annotated with `-> {ret_annotation_str.strip()}` on line {node.lineno}, but its `return` statements return a `dict` (`dict[str, Any]`).",
                        f"Update the return type annotation on line {node.lineno} of `python_code.py` from `-> {ret_annotation_str.strip()}` to `-> dict[str, Any]`.",
                    )
    except SyntaxError as exc:
        _add_issue(
            f"PYTHON_SYNTAX_ERROR: Failed to parse `{tool_py_path}`: {exc}",
            f"Fix the Python syntax error in `{tool_py_path}`: {exc}",
        )

    return {
        "python_file_found": True,
        "tool_py_path": tool_py_path,
        "total_lines": len(lines),
        "context_vars_set": sorted(list(set(context_vars_set))),
        "nested_context_keys": sorted(list(set(nested_context_keys))),
        "unreachable_context_vars": unreachable_context_vars,
        "returned_dict_keys": sorted(list(set(returned_dict_keys))),
        "return_statements": return_statements,
        "findings": findings,
        "recommendations": recommendations,
    }


# ==============================================================================
# STEP 7 & 8: BUILD RECOMMENDATIONS, MARKDOWN REPORT & TRACKER SUMMARY
# ==============================================================================

def build_all_recommendations(
    hygiene_analysis: Dict[str, Any],
    instruction_audit: Dict[str, Any],
    code_audit: Dict[str, Any],
) -> Dict[str, List[str]]:
    """Synthesizes concrete, actionable recommendations for Tool Response Hygiene, Instruction Alignment, and Python Code."""
    hygiene_recs: List[str] = []
    instruction_recs: List[str] = []
    code_recs: List[str] = list(code_audit.get("recommendations", []))

    metrics = hygiene_analysis.get("metrics", {})
    observed_keys = hygiene_analysis.get("observed_response_keys", [])

    # 1. Build Tool Response Data Hygiene Recommendations from detected issues
    for iss in hygiene_analysis.get("issues_found", []):
        if iss.startswith("TOKEN_BLOAT"):
            # Recommend keeping only keys referenced in instruction.txt
            referenced_in_instr = [
                row["matched_response_key"] or row["variable"]
                for row in instruction_audit.get("variable_alignment_matrix", [])
                if row.get("in_tool_response") or row.get("closest_available_keys")
            ]
            # Also include closest available keys for missing variables (e.g., productName for deviceModel)
            for m_var in instruction_audit.get("missing_from_both_response_and_context", []):
                for ck in m_var.get("closest_available_keys", []):
                    if ck not in referenced_in_instr:
                        referenced_in_instr.append(ck)
            keep_keys_hint = (
                f" (specifically keep only the fields needed by `instruction.txt`: `{referenced_in_instr}`)"
                if referenced_in_instr
                else ""
            )
            hygiene_recs.append(
                f"**Prune Unnecessary Keys from Tool Response (`TOKEN_BLOAT`):** Filter each item in `python_code.py` before returning and storing in `context.state`{keep_keys_hint}. Strip unreferenced backend metadata keys to reduce payload size from `{metrics.get('mean_chars', 'N/A')}` chars (`~{metrics.get('mean_est_tokens', 'N/A')}` tokens) to `< 300` chars."
            )
        elif iss.startswith("EMPTY_FIELD_BLOAT"):
            hygiene_recs.append(
                f"**Strip Empty/Null Fields (`EMPTY_FIELD_BLOAT`):** Drop keys with `\"\"`, `null`, `[]`, or `{{}}` values in `python_code.py` (currently `{metrics.get('empty_key_percentage', 'N/A')}%` of keys per response are empty) before returning the payload to the LLM."
            )
        elif iss.startswith("UI_TEMPLATE_NOISE"):
            ui_keys = hygiene_analysis.get("ui_noise_keys", [])
            hygiene_recs.append(
                f"**Remove UI/Presentation Metadata (`UI_TEMPLATE_NOISE`):** Strip frontend rendering fields `{ui_keys}` in `python_code.py` so the LLM only receives conversational data fields."
            )
        elif iss.startswith("DEEP_NESTING"):
            hygiene_recs.append(
                f"**Flatten Deeply Nested JSON (`DEEP_NESTING`):** Flatten the returned dictionary in `python_code.py` from `{metrics.get('max_nesting_depth', 'N/A')}` levels down to `<= {skill_config.MAX_RECOMMENDED_NESTING_DEPTH}` levels containing only top-level conversational fields."
            )
        elif iss.startswith("DOUBLE_SERIALIZED_JSON"):
            ds_fields = hygiene_analysis.get("double_serialized_fields", [])
            hygiene_recs.append(
                f"**Fix Double-Serialized JSON Strings (`DOUBLE_SERIALIZED_JSON`):** Parse stringified JSON in `{ds_fields}` via `json.loads()` or remove `json.dumps()` in `python_code.py` so native JSON objects are returned."
            )
        elif iss.startswith("SILENT_EMPTY_RESULT"):
            hygiene_recs.append(
                "**Replace Silent Empty Strings with Explicit Status (`SILENT_EMPTY_RESULT`):** Never return `{\"result\": \"\"}` on API failure or empty branches; return an explicit diagnostic status such as `{\"status\": \"ERROR\", \"reason\": \"...\"}` so the agent does not hallucinate or loop."
            )
        elif iss.startswith("DUPLICATE_MDN_MTN_KEYS"):
            hygiene_recs.append(
                f"**Consolidate Duplicate Phone Number Keys (`DUPLICATE_MDN_MTN_KEYS`):** Standardize on a single key (e.g., `mtn`) instead of returning redundant `{hygiene_analysis.get('mdn_mtn_keys', [])}` fields."
            )

    # 2. Build Instruction Alignment Recommendations
    for m_var in instruction_audit.get("missing_from_both_response_and_context", []):
        if m_var.get("recommendation") and m_var["recommendation"] not in instruction_recs:
            instruction_recs.append(
                f"**Resolve Missing Instruction Variable `{m_var['variable']}` (Lines {', '.join('L'+str(x) for x in m_var['lines'])}):** {m_var['recommendation']}"
            )

    for row in instruction_audit.get("variable_alignment_matrix", []):
        if row["status"] in ("CASING_MISMATCH", "CONTEXT_ONLY_BUT_UNREACHABLE_AT_RUNTIME", "RESOLVED_VIA_CONTEXT_SESSION_ONLY"):
            rec_text = f"**Align `{row['variable']}` ({row['line_refs']} — `{row['status']}`):** {row['recommendation']}"
            if rec_text not in instruction_recs:
                instruction_recs.append(rec_text)

    for f in instruction_audit.get("findings", []):
        if f.startswith("SYNTAX_WARNING") or f.startswith("POTENTIAL_TOOL_NAME_TYPO") or f.startswith("NOT_INVOKED_IN_INSTRUCTIONS"):
            instruction_recs.append(f"**Fix Tool Invocation in `instruction.txt`:** {f}")

    return {
        "hygiene_recommendations": hygiene_recs,
        "instruction_recommendations": instruction_recs,
        "code_recommendations": code_recs,
    }


def generate_markdown_report(
    agent_role: str,
    tool_name: str,
    start_date: str,
    row_limit: int,
    sql_query: str,
    exempt_info: Optional[Tuple[str, str]],
    local_paths: Dict[str, Any],
    bq_error: Optional[str],
    hygiene_analysis: Dict[str, Any],
    instruction_audit: Dict[str, Any],
    code_audit: Dict[str, Any],
) -> str:
    """Compiles the comprehensive Tool Data Hygiene Markdown report and Tracker copy-paste block."""
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    recs = build_all_recommendations(hygiene_analysis, instruction_audit, code_audit)

    # Build Tracker Column C & Column D ready-to-paste summaries
    all_issues: List[str] = []
    if exempt_info:
        col_c = "NA"
        col_d = f"1. {exempt_info[1]}\nHence, from tool data hygiene point of view no recommendation needed."
    else:
        if hygiene_analysis.get("hygiene_verdict") == "ISSUES_IDENTIFIED":
            all_issues.append("1. Tool Response Data Hygiene Issue")
        if instruction_audit.get("findings") or instruction_audit.get("missing_from_both_response_and_context"):
            all_issues.append(f"{len(all_issues)+1}. Agent Instruction vs. Tool Response / Context Variable Mismatch")
        if code_audit.get("findings"):
            all_issues.append(f"{len(all_issues)+1}. Python Tool Code / Context Memory Issue")

        col_c = "\n".join(all_issues) if all_issues else "NA"

        sample_str = ""
        clusters = hygiene_analysis.get("response_pattern_clusters", [])
        if clusters:
            sample_str = clusters[0].get("sample_response", "")[:280]

        if not all_issues and hygiene_analysis.get("valid_tool_response_rows", 0) > 0:
            col_d = (
                "1. All good from data hygiene point of view. This tool is returning the data in correct JSON format.\n"
                f"Sharing one sample example below:\n`{sample_str}`"
            )
        else:
            d_lines = []
            sec_no = 1
            if hygiene_analysis.get("hygiene_verdict") == "CLEAN_NO_RECOMMENDATION_NEEDED":
                d_lines.append(
                    f"{sec_no}. **Tool Response Hygiene:** No issue seen on tool response data hygiene as it returns clean/compact JSON: `{sample_str}`"
                )
                sec_no += 1
            elif hygiene_analysis.get("issues_found"):
                d_lines.append(f"{sec_no}. **Tool Response Hygiene Analysis & Recommendation:**")
                for iss in hygiene_analysis["issues_found"]:
                    d_lines.append(f"   - **Issue:** {iss}")
                for r in recs["hygiene_recommendations"]:
                    d_lines.append(f"   - **Recommendation:** {r}")
                sec_no += 1

            missing_vars = instruction_audit.get("missing_from_both_response_and_context", [])
            if missing_vars or instruction_audit.get("findings") or recs["instruction_recommendations"]:
                d_lines.append(f"{sec_no}. **Instruction Alignment vs. Tool Response & Context Session:**")
                if missing_vars:
                    missing_names = [m["variable"] for m in missing_vars]
                    d_lines.append(
                        f"   - **Variables in `instruction.txt` Missing from BOTH Tool Response & Context Session:** `{missing_names}`"
                    )
                for f in instruction_audit.get("findings", []):
                    d_lines.append(f"   - **Finding:** {f}")
                for r in recs["instruction_recommendations"]:
                    d_lines.append(f"   - **Recommendation:** {r}")
                sec_no += 1

            if code_audit.get("findings") or recs["code_recommendations"]:
                d_lines.append(f"{sec_no}. **Python Tool Code & Session Context (`context.state`) Analysis & Recommendation:**")
                for f in code_audit.get("findings", []):
                    d_lines.append(f"   - **Issue:** {f}")
                for r in recs["code_recommendations"]:
                    d_lines.append(f"   - **Recommendation:** {r}")
                sec_no += 1

            col_d = "\n".join(d_lines) if d_lines else "See detailed sections below."

    metrics = hygiene_analysis.get("metrics", {})

    md = f"""# Tool Data Hygiene, Code Audit & Instruction Alignment Report

- **Generated At:** `{now_utc}`
- **Sub-Agent (`AGENT_ROLE`):** `{agent_role}`
- **Tool Name (`TOOL_NAME`):** `{tool_name}`
- **BigQuery Start Date (`START_DATE`):** `{start_date}` (Limit: `{row_limit}`)
- **BigQuery Table:** `{skill_config.BQ_PROJECT_DATASET_TABLE}`
- **Local Sub-Agent Path:** `{local_paths.get('sub_agent_dir') or 'Not Found'}`
- **Local Tool Code Path:** `{local_paths.get('tool_py_path') or 'Not Found'}`

---

## 1. Ready-to-Paste Tracker Summary (`VZ_Chat Hillclimbing Tracker.xlsx` -> Tab: `Tool Hygiene`)

| Tracker Column | Value to Paste |
| :--- | :--- |
| **Column A (`Sub-Agent`)** | `{agent_role}` |
| **Column B (`Tool Name/API Name/Callbacks`)** | `{tool_name}` |
| **Column C (`Issue`)** | {col_c.replace(chr(10), '<br>')} |
| **Column D (`Detail Analysis and Recommendation`)** | {col_d.replace(chr(10), '<br>')} |

---

## 2. Step 0: Pre-Triage & Active Usage Verification

- **Exempt Category Check:** `{exempt_info[0] if exempt_info else 'None (Active Tool Requiring Full 6-Step Hygiene Audit)'}`
- **Registered in `<SubAgent>.json`:** `{local_paths.get('registered_in_agent_json')}`
- **Invocations Found in `instruction.txt`:** `{instruction_audit.get('invocation_count', 0)}`
- **Local `python_code.py` Present:** `{code_audit.get('python_file_found', False)}`
"""

    if exempt_info:
        md += f"\n> [!NOTE]\n> **Fast-Track Exemption Applied:** {exempt_info[1]}\n"

    md += f"""
---

## 3. Step 1 & Step 2: BigQuery Extraction & `distinct_tool_response` Filtering

- **Total BigQuery Rows Fetched:** `{hygiene_analysis.get('total_bq_rows', 0)}`
- **`'NA'` Rows Ignored (`toolCall` events):** `{hygiene_analysis.get('na_tool_call_rows_ignored', 0)}`
- **Valid `distinct_tool_response` Rows Analyzed:** `{hygiene_analysis.get('valid_tool_response_rows', 0)}`
"""
    if bq_error:
        md += f"\n> [!WARNING]\n> **BigQuery Execution Note:** `{bq_error}`\n"

    md += f"""
<details>
<summary><b>Executed BigQuery SQL Query (Click to Expand)</b></summary>

```sql
{sql_query}
```
</details>

---

## 4. Step 3: Tool Response Data Hygiene Analysis (`distinct_tool_response`)

- **Data Hygiene Verdict:** **`{hygiene_analysis.get('hygiene_verdict')}`**
- **Summary:** {hygiene_analysis.get('verdict_summary')}

### 4.1 Quantitative Payload & Token Metrics

| Metric | Measured Value | SOP Target / Threshold |
| :--- | :---: | :---: |
| **Mean Character Length** | `{metrics.get('mean_chars', 'N/A')}` chars | `<= {skill_config.MAX_RECOMMENDED_CHARS:,}` chars |
| **Median / Max Character Length** | `{metrics.get('median_chars', 'N/A')}` / `{metrics.get('max_chars', 'N/A')}` chars | `<= {skill_config.MAX_RECOMMENDED_CHARS:,}` chars |
| **Mean / Max Estimated Tokens (`chars / 4`)** | `{metrics.get('mean_est_tokens', 'N/A')}` / `{metrics.get('max_est_tokens', 'N/A')}` tokens | `<= {skill_config.MAX_RECOMMENDED_TOKENS}` tokens |
| **Mean Regex BPE Tokens** | `{metrics.get('mean_bpe_tokens', 'N/A')}` tokens | — |
| **Mean Total Keys per Response** | `{metrics.get('mean_total_keys', 'N/A')}` keys | Minimal required keys |
| **Mean Empty / `null` / `[]` Keys** | `{metrics.get('mean_empty_keys', 'N/A')}` keys (`{metrics.get('empty_key_percentage', 'N/A')}%`) | `< 25%` empty keys |
| **Maximum JSON Nesting Depth** | `{metrics.get('max_nesting_depth', 'N/A')}` levels | `<= {skill_config.MAX_RECOMMENDED_NESTING_DEPTH}` levels |
| **Silent Empty (`{{"result": ""}}`) Responses** | `{metrics.get('empty_result_records_count', 0)}` records | `0` records |

### 4.2 Observed Response Pattern Clusters (Top Samples)
"""
    for idx, cl in enumerate(hygiene_analysis.get("response_pattern_clusters", []), 1):
        md += f"""
#### Pattern {idx}: `{cl['pattern']}` — `{cl['count']}` occurrence(s) (Sample Convo: `{cl['sample_conversationName']}`)
```json
{cl['sample_response']}
```
"""

    md += "\n### 4.3 Detailed 6-Pillar Hygiene Findings\n"
    if hygiene_analysis.get("issues_found"):
        for iss in hygiene_analysis["issues_found"]:
            md += f"- 🔴 {iss}\n"
    else:
        md += "- ✅ **No Tool Response Data Hygiene issues detected.** Payload is compact, properly serialized, and within token/nesting budgets.\n"

    md += f"""
- **Observed Keys Across Responses:** `{hygiene_analysis.get('observed_response_keys', [])[:30]}`
- **Key Casing Breakdown:** `{json.dumps(hygiene_analysis.get('casing_styles_detected', {}))}`
- **String Booleans Detected (Sample):** `{hygiene_analysis.get('string_booleans_sample', [])[:8]}`
- **Double-Serialized Fields:** `{hygiene_analysis.get('double_serialized_fields', [])}`
- **UI / Template Noise Keys:** `{hygiene_analysis.get('ui_noise_keys', [])}`
- **Duplicate MDN/MTN Keys:** `{hygiene_analysis.get('mdn_mtn_keys', [])}`

---

## 5. Step 4: Agent Instruction (`instruction.txt`) vs. Tool Response & Context Session Alignment

- **Instruction File:** `{instruction_audit.get('instruction_path') or 'Not Found'}`
- **Tool Invocations Found:** `{instruction_audit.get('invocation_count', 0)}`

### 5.1 Variables Referenced in `instruction.txt` Missing from BOTH Tool Response AND Context Session
"""
    missing_both = instruction_audit.get("missing_from_both_response_and_context", [])
    if missing_both:
        md += "\n> [!CAUTION]\n> **The following variable(s) are referenced in `instruction.txt` for this tool, but are NEITHER returned in `distinct_tool_response` NOR saved in the session context (`context.state` / `context.set_variable`) in `python_code.py`:**\n\n"
        for m in missing_both:
            ln_str = ", ".join(f"L{x}" for x in m["lines"])
            md += f"- ❌ **`{m['variable']}`** (Referenced at `{ln_str}` via `{', '.join(m['reference_types'])}`)\n"
            md += f"  - **Why it fails:** {m['explanation']}\n"
            md += f"  - **Recommendation:** {m['recommendation']}\n"
    else:
        md += "- ✅ **None.** Every variable referenced in `instruction.txt` for this tool is either returned in `distinct_tool_response` or saved in the session context (`context.state`).\n"

    resolved_ctx = instruction_audit.get("resolved_via_context_only", [])
    if resolved_ctx:
        md += "\n### 5.2 Variables Referenced in `instruction.txt` That Are NOT in Tool Response, But ARE Saved in Context Session (`context.state`)\n"
        for rc in resolved_ctx:
            ln_str = ", ".join(f"L{x}" for x in rc["lines"])
            md += f"- 🟡 **`{rc['variable']}`** (Referenced at `{ln_str}`) -> Not in `distinct_tool_response`, but saved in `context.state[\"{rc['matched_context_key']}\"]`.\n"
            md += f"  - **Recommendation:** {rc['recommendation']}\n"

    md += "\n### 5.3 Complete Instruction Variable Cross-Reference Matrix (Instruction vs. Tool Response vs. Context Session)\n\n"
    matrix = instruction_audit.get("variable_alignment_matrix", [])
    if matrix:
        md += "| Variable Referenced in `instruction.txt` | Line(s) | Reference Type | In Tool Response (`distinct_tool_response`)? | Saved in Context Session (`context.state`)? | Alignment Status | Analysis & Recommendation |\n"
        md += "| :--- | :---: | :--- | :---: | :---: | :--- | :--- |\n"
        for row in matrix:
            resp_badge = f"✅ Yes (`{row['matched_response_key']}`)" if row["in_tool_response"] else "❌ **No**"
            ctx_badge = f"✅ Yes (`{row['matched_context_key']}`)" if row["in_context_session"] else "❌ **No**"
            md += (
                f"| **`{row['variable']}`** | `{row['line_refs']}` | {', '.join(row['reference_types'])} | "
                f"{resp_badge} | {ctx_badge} | `{row['status']}` | {row['explanation']} **Rec:** {row['recommendation']} |\n"
            )
    else:
        md += "_No explicit post-tool variables extracted from the invocation block in `instruction.txt`._\n"

    md += "\n### 5.4 Instruction Alignment Findings\n"
    if instruction_audit.get("findings"):
        for f in instruction_audit["findings"]:
            md += f"- 🟠 {f}\n"
    else:
        md += "- ✅ **All tool invocations in `instruction.txt` use valid `{@TOOL: ...}` syntax and align with observed tool outputs/variables.**\n"

    for idx, blk in enumerate(instruction_audit.get("matched_blocks", []), 1):
        md += f"""
#### Invocation {idx} (Line {blk['line_number']} — Proper `{{@TOOL: ...}}` Syntax: `{blk['uses_proper_tool_tag_syntax']}`)
```xml
{blk['surrounding_block']}
```
"""

    md += f"""
---

## 6. Step 5 & Step 6: Python Tool Code (`python_code.py`) & Return Statement Audit

- **Python Code File:** `{code_audit.get('tool_py_path') or 'Not Found'}`
- **Top-Level Context Variables Written (`context.state` / `context.set_variable`):** `{code_audit.get('context_vars_set', [])}`
- **Return Statements Found:** `{json.dumps(code_audit.get('return_statements', []))}`

### 6.1 Static Code & Context Memory Findings
"""
    if code_audit.get("findings"):
        for f in code_audit["findings"]:
            md += f"- 🔴 {f}\n"
    else:
        md += "- ✅ **No context memory dumps, return anti-patterns, or critical code defects detected.**\n"

    md += """
---

## 7. Actionable Recommendations & Remediation Plan

"""
    has_any_rec = any([
        recs["hygiene_recommendations"],
        recs["instruction_recommendations"],
        recs["code_recommendations"],
    ])
    if not has_any_rec:
        md += "- ✅ **No recommendations needed.** Tool response payload, `instruction.txt` references, and `python_code.py` implementation are clean and aligned.\n"
    else:
        md += "### 7.1 Tool Response Data Hygiene Recommendations\n"
        if recs["hygiene_recommendations"]:
            for r in recs["hygiene_recommendations"]:
                md += f"- 🛠️ {r}\n"
        else:
            md += "- ✅ No changes needed to tool response payload structure.\n"

        md += "\n### 7.2 Agent Instruction (`instruction.txt`) & Variable Alignment Recommendations\n"
        if recs["instruction_recommendations"]:
            for r in recs["instruction_recommendations"]:
                md += f"- 🛠️ {r}\n"
        else:
            md += "- ✅ No changes needed in `instruction.txt`.\n"

        md += "\n### 7.3 Python Tool Code (`python_code.py`) & Context Session Recommendations\n"
        if recs["code_recommendations"]:
            for r in recs["code_recommendations"]:
                md += f"- 🛠️ {r}\n"
        else:
            md += "- ✅ No changes needed in `python_code.py`.\n"

    md += """
---

## 8. Mandatory Human-in-the-Loop (HITL) Review Checklist (Before Finalizing Tracker)

> [!IMPORTANT]
> **Human Reviewer Action Required:** Before copying these findings into `VZ_Chat Hillclimbing Tracker.xlsx` or `VZ_ToolData_DetailAnalysis&Recommendation.docx`, apply the 5 HITL Governance Filters:
> 1. **[ ] Re-Scope Upstream API Changes:** Do NOT ask the VZ agent team to rewrite shared upstream SOE backend microservice schemas; instead, recommend filtering/transforming inside `python_code.py` (DTO wrapper pattern).
> 2. **[ ] Verify Empty Field Dependencies:** Before recommending removal of empty (`""`/`null`/`[]`) fields, confirm with the dev team whether client-side UI templates (`NGARESP_TEMPLATE_DATAMAP`) or stateful webhooks (`mobile_data`) require those keys to exist.
> 3. **[ ] Separate Clean Tool Responses from Code/Instruction Bugs:** If `distinct_tool_response` is already compact (e.g., `{"result": "Pass"}` or a 2-key JSON), explicitly state *"1. Tool Response Hygiene: All good"* and list Python code bugs or instruction gaps as separate points.
> 4. **[ ] Drop Pedantic Nitpicks on Small Payloads:** If the payload is < 200 chars and works with current `instruction.txt` string comparisons (e.g., `"cardCreated": "true"`), do NOT recommend changing `"true"` to native boolean `true` as it would break `instruction.txt` conditions for zero token savings.
> 5. **[ ] Verify Dev vs. Prod Sync:** If BigQuery shows recent calls for a tool that is no longer in local `instruction.txt`, check with the VZ POC whether a Dev change is awaiting Prod deployment.
"""
    return md


# ==============================================================================
# MAIN CLI ENTRYPOINT
# ==============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="CXAS Tool Data Hygiene, Python Code & Instruction Alignment Analyzer"
    )
    parser.add_argument(
        "--role",
        default=skill_config.AGENT_ROLE,
        help=f"BigQuery sub-agent role (default from config.py: {skill_config.AGENT_ROLE})",
    )
    parser.add_argument(
        "--tool",
        default=skill_config.TOOL_NAME,
        help=f"Tool display name to analyze (default from config.py: {skill_config.TOOL_NAME})",
    )
    parser.add_argument(
        "--start-date",
        default=skill_config.START_DATE,
        help=f"BigQuery start date YYYY-MM-DD (default from config.py: {skill_config.START_DATE})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=skill_config.ROW_LIMIT,
        help=f"BigQuery row limit (default from config.py: {skill_config.ROW_LIMIT})",
    )
    parser.add_argument(
        "--sub-agent-folder",
        default=skill_config.LOCAL_SUB_AGENT_FOLDER,
        help="Optional local sub-agent folder name override under agents/",
    )
    parser.add_argument(
        "--offline-file",
        default=skill_config.OFFLINE_BQ_FILE,
        help="Optional path to pre-exported BigQuery JSON/CSV file to skip live bq query",
    )
    parser.add_argument(
        "--app-dir",
        default=skill_config.LOCAL_AGENT_APP_DIR,
        help=f"Local CXAS app directory (default: {skill_config.LOCAL_AGENT_APP_DIR})",
    )
    parser.add_argument(
        "--output-dir",
        default=skill_config.OUTPUT_DIR,
        help=f"Output directory for reports (default: {skill_config.OUTPUT_DIR})",
    )
    parser.add_argument(
        "--table",
        default=skill_config.BQ_PROJECT_DATASET_TABLE,
        help=f"BigQuery project.dataset.table override (default: {skill_config.BQ_PROJECT_DATASET_TABLE})",
    )
    parser.add_argument(
        "--schema-mode",
        default=getattr(skill_config, "BQ_SCHEMA_MODE", "ccai_chunks_json"),
        choices=["ccai_chunks_json", "flat_columns", "auto", "custom_sql"],
        help="BigQuery schema extraction mode (ccai_chunks_json, flat_columns, auto, custom_sql)",
    )
    args = parser.parse_args()

    app_dir_path = WORKSPACE_ROOT / args.app_dir
    out_root_path = WORKSPACE_ROOT / args.output_dir
    safe_folder_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", f"{args.role}__{args.tool}")
    tool_out_dir = out_root_path / safe_folder_name
    tool_out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 80)
    print(f"CXAS TOOL DATA HYGIENE ANALYZER (`cxas-tool-data-hygiene`)")
    print(f"  BigQuery Table : {args.table} (Mode: {args.schema_mode})")
    print(f"  Sub-Agent Role : {args.role}")
    print(f"  Tool Name      : {args.tool}")
    print(f"  Start Date     : {args.start_date} (Limit: {args.limit})")
    print(f"  Output Folder  : {tool_out_dir}")
    print("=" * 80)

    # Step 0: Pre-Triage & Local Path Resolution
    exempt_info = check_exempt_tool_category(args.tool)
    local_paths = resolve_local_agent_and_tool_paths(
        app_dir=app_dir_path,
        agent_role=args.role,
        tool_name=args.tool,
        sub_agent_override=args.sub_agent_folder,
    )

    # Step 1: Build & Run BigQuery SQL (or load offline file)
    sql_query = build_sop_bigquery_sql(
        table_name=args.table,
        start_date=args.start_date,
        agent_role=args.role,
        tool_name=args.tool,
        row_limit=args.limit,
        schema_mode=args.schema_mode,
    )

    raw_rows: List[Dict[str, Any]] = []
    bq_error: Optional[str] = None

    if args.offline_file:
        print(f"[INFO] Loading BigQuery records from offline file: {args.offline_file}")
        raw_rows = load_offline_records(args.offline_file)
    else:
        raw_rows, bq_error = run_bigquery_query(sql_query, max_rows=max(args.limit, 10000))
        if bq_error:
            print(f"[WARN] BigQuery query returned warning/error: {bq_error}")

    # Normalize rows so any custom column names map cleanly to standard keys
    raw_rows = normalize_bq_rows(raw_rows, default_role=args.role, default_tool=args.tool)

    # Step 2 & Step 3: Filter `distinct_tool_response` & Analyze Hygiene
    hygiene_analysis = analyze_distinct_tool_responses(raw_rows)

    # Step 5 & Step 6: Audit Python Tool Code (`python_code.py`)
    code_audit = audit_python_tool_code(local_paths.get("tool_py_path"))

    # Step 4: Audit Agent Instructions (`instruction.txt`) against BOTH Tool Response keys & Context Session keys
    instruction_audit = audit_agent_instructions(
        instruction_path=local_paths.get("instruction_path"),
        tool_name=args.tool,
        observed_response_keys=hygiene_analysis.get("observed_response_keys", []),
        code_context_vars_set=code_audit.get("context_vars_set", []),
        code_returned_keys=code_audit.get("returned_dict_keys", []),
        code_nested_context_keys=code_audit.get("nested_context_keys", []),
        code_unreachable_context_vars=code_audit.get("unreachable_context_vars", {}),
    )

    # Save cleaned non-NA `distinct_tool_response` records (or all raw_rows if all are 'NA') to JSON and CSV
    valid_rows = [
        r for r in raw_rows
        if r.get("distinct_tool_response") is not None
        and str(r.get("distinct_tool_response")).strip() not in ("NA", "")
    ]
    rows_to_save = valid_rows if valid_rows else raw_rows

    responses_json_path = tool_out_dir / "bq_distinct_tool_responses_latest.json"
    responses_csv_path = tool_out_dir / "bq_distinct_tool_responses_latest.csv"
    audit_json_path = tool_out_dir / "hygiene_metrics_and_ast_audit_latest.json"
    report_md_path = tool_out_dir / "tool_data_hygiene_report_latest.md"

    with open(responses_json_path, "w", encoding="utf-8") as f:
        json.dump(rows_to_save, f, indent=2, ensure_ascii=False)

    with open(responses_csv_path, "w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "conversationName",
            "startTimestamp",
            "role",
            "tool_call_display_name",
            "chunks",
            "distinct_tool_response",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in rows_to_save:
            writer.writerow(r)

    combined_audit = {
        "config": {
            "agent_role": args.role,
            "tool_name": args.tool,
            "start_date": args.start_date,
            "row_limit": args.limit,
            "bq_table": skill_config.BQ_PROJECT_DATASET_TABLE,
        },
        "step0_exempt_check": exempt_info,
        "local_paths": local_paths,
        "step1_sql_query": sql_query,
        "step2_and_step3_response_hygiene": hygiene_analysis,
        "step4_instruction_alignment": instruction_audit,
        "step5_and_step6_python_code_audit": code_audit,
        "step7_recommendations": build_all_recommendations(hygiene_analysis, instruction_audit, code_audit),
    }
    with open(audit_json_path, "w", encoding="utf-8") as f:
        json.dump(combined_audit, f, indent=2, ensure_ascii=False)

    report_md = generate_markdown_report(
        agent_role=args.role,
        tool_name=args.tool,
        start_date=args.start_date,
        row_limit=args.limit,
        sql_query=sql_query,
        exempt_info=exempt_info,
        local_paths=local_paths,
        bq_error=bq_error,
        hygiene_analysis=hygiene_analysis,
        instruction_audit=instruction_audit,
        code_audit=code_audit,
    )
    with open(report_md_path, "w", encoding="utf-8") as f:
        f.write(report_md)

    print(f"[SUCCESS] Saved cleaned tool responses JSON : {responses_json_path}")
    print(f"[SUCCESS] Saved cleaned tool responses CSV  : {responses_csv_path}")
    print(f"[SUCCESS] Saved structured audit JSON       : {audit_json_path}")
    print(f"[SUCCESS] Saved Markdown Analysis Report    : {report_md_path}")


if __name__ == "__main__":
    main()

