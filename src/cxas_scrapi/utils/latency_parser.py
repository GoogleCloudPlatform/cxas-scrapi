"""Utility for parsing Conversational core span telemetry into DataFrames."""

# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import contextlib
import glob
import json
import logging
import math
import os
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

import pandas as pd
from rich.progress import track

logger = logging.getLogger(__name__)


class LatencyParser:
    """A generic utility for parsing Conversational core span telemetry into
    DataFrames."""

    @staticmethod
    def fetch_conversation_traces(
        conv_ids: list[str], get_conversation_func: Callable
    ) -> dict[str, Any]:
        """Fetches detailed conversation traces concurrently with rate
        limiting."""
        traces = {}
        # Rate Limiting: 600 requests per minute API quota = 10 req/s.
        # We will chunk into batches of 5 and sleep 1 second.
        chunk_size = 5
        conv_list = list(set(conv_ids))

        print(
            f"Fetching {len(conv_list)} conversation traces for detailed "
            f"latency metrics..."
        )

        for i in track(
            range(0, len(conv_list), chunk_size),
            description="Fetching Traces",
        ):
            chunk = conv_list[i : i + chunk_size]
            with ThreadPoolExecutor(max_workers=chunk_size) as executor:
                future_to_id = {
                    executor.submit(get_conversation_func, cid): cid
                    for cid in chunk
                }
                for future in as_completed(future_to_id):
                    cid = future_to_id[future]
                    try:
                        conv = future.result()
                        traces[cid] = (
                            type(conv).to_dict(conv)
                            if not isinstance(conv, dict)
                            else conv
                        )
                    except Exception as e:
                        logger.error(f"Failed to fetch conversation {cid}: {e}")
            if i + chunk_size < len(conv_list):
                time.sleep(1)  # Rate limit padding

        return traces

    @staticmethod
    def _parse_duration_ms(duration_str: Any) -> float:
        """Convert string durations ('1.450s') or dicts to floats in ms."""
        if not duration_str:
            return 0.0
        if isinstance(duration_str, (int, float)):
            return float(duration_str) * 1000.0
        if isinstance(duration_str, dict):
            secs = float(duration_str.get("seconds", 0) or 0)
            nanos = float(duration_str.get("nanos", 0) or 0)
            return (secs + nanos / 1e9) * 1000.0
        if isinstance(duration_str, str):
            return float(duration_str.replace("s", "")) * 1000.0
        return 0.0

    @staticmethod
    def _parse_timestamp_s(ts_val: Any) -> float | None:
        """Convert ISO-8601 timestamp string or Timestamp dict to epoch s."""
        if not ts_val:
            return None
        if isinstance(ts_val, (int, float)):
            return float(ts_val)
        if isinstance(ts_val, dict):
            secs = float(ts_val.get("seconds", 0) or 0)
            nanos = float(ts_val.get("nanos", 0) or 0)
            return secs + nanos / 1e9 if (secs or nanos) else None
        if isinstance(ts_val, str):
            try:
                s = ts_val.strip()
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                # Truncate nanoseconds to microseconds for fromisoformat
                s = re.sub(r"\.(\d{6})\d+", r".\1", s)
                return datetime.fromisoformat(s).timestamp()
            except Exception:
                return None
        return None

    @staticmethod
    def _unwrap_attr_val(val: Any) -> Any:
        """Unwrap protobuf Value wrappers or return raw value."""
        if isinstance(val, dict):
            for k in (
                "string_value",
                "stringValue",
                "number_value",
                "numberValue",
                "bool_value",
                "boolValue",
            ):
                if k in val and val[k] is not None:
                    return val[k]
            if "struct_value" in val or "structValue" in val:
                sv = val.get("struct_value") or val.get("structValue") or {}
                fields = sv.get("fields", sv) if isinstance(sv, dict) else {}
                if isinstance(fields, dict):
                    return {
                        fk: LatencyParser._unwrap_attr_val(fv)
                        for fk, fv in fields.items()
                    }
            if "list_value" in val or "listValue" in val:
                lv = val.get("list_value") or val.get("listValue") or {}
                values = lv.get("values", []) if isinstance(lv, dict) else []
                return [LatencyParser._unwrap_attr_val(v) for v in values]
            if "kind" in val and len(val) == 1:
                return None
        return val

    @staticmethod
    def _unwrap_attrs(attrs: Any) -> dict[str, Any]:
        """Normalizes a span attributes dictionary."""
        if not isinstance(attrs, dict):
            return {}
        if "fields" in attrs and isinstance(attrs["fields"], dict):
            attrs = attrs["fields"]
        return {k: LatencyParser._unwrap_attr_val(v) for k, v in attrs.items()}

    @staticmethod
    def _percentile(vals: list[float], q: float) -> float:
        """Computes percentile q (0..100) using linear interpolation."""
        if not vals:
            return 0.0
        s = sorted(vals)
        if len(s) == 1:
            return float(s[0])
        pos = (len(s) - 1) * (q / 100.0)
        lo = math.floor(pos)
        hi = math.ceil(pos)
        if lo == hi:
            return float(s[lo])
        return float(s[lo] + (s[hi] - s[lo]) * (pos - lo))

    @staticmethod
    def _process_spans(
        spans: list[dict],
        context_id: str,
        t_idx: int,
        tool_rows: list,
        callback_rows: list,
        guardrail_rows: list,
        llm_rows: list,
        context_key: str = "conversation_id",
    ) -> dict[str, float]:
        """Recursively walks span trees and accumulates granular component
        attributes."""
        sums = {"LLM": 0.0, "Guardrail": 0.0, "Callback": 0.0}
        for s in spans:
            if not isinstance(s, dict):
                continue
            name: str | None = s.get("name")
            duration = LatencyParser._parse_duration_ms(s.get("duration", "0s"))
            attrs = LatencyParser._unwrap_attrs(s.get("attributes", {}))

            if name and name in sums:
                sums[name] += duration

            if name == "Tool":
                tool_rows.append(
                    {
                        "tool_name": attrs.get(
                            "name",
                            attrs.get("tool", attrs.get("display_name", "")),
                        ),
                        "agent": attrs.get("agent", ""),
                        context_key: context_id,
                        "turn_index": t_idx,
                        "duration_ms": duration,
                    }
                )
            elif name == "Callback":
                callback_rows.append(
                    {
                        "agent": attrs.get("agent", ""),
                        "stage": attrs.get("stage", ""),
                        "description": attrs.get("description", ""),
                        context_key: context_id,
                        "turn_index": t_idx,
                        "duration_ms": duration,
                    }
                )
            elif name == "Guardrail":
                guardrail_rows.append(
                    {
                        "agent": attrs.get("agent", ""),
                        "name": attrs.get("name", attrs.get("description", "")),
                        context_key: context_id,
                        "turn_index": t_idx,
                        "duration_ms": duration,
                    }
                )
            elif name == "LLM":
                llm_rows.append(
                    {
                        "agent": attrs.get("agent", ""),
                        "model": attrs.get("model", ""),
                        "input_tokens": attrs.get("input token count", 0),
                        "output_tokens": attrs.get("output token count", 0),
                        "time_to_first_token_ms": attrs.get(
                            "time to first chunk (ms)", 0
                        ),
                        "time_to_first_audio_ms": attrs.get(
                            "time to first audio (ms)", 0
                        ),
                        "audio_duration_ms": attrs.get(
                            "audio duration (ms)", 0
                        ),
                        context_key: context_id,
                        "turn_index": t_idx,
                        "duration_ms": duration,
                    }
                )

            children = s.get("child_spans", s.get("childSpans", []))
            if children:
                child_sums = LatencyParser._process_spans(
                    children,
                    context_id,
                    t_idx,
                    tool_rows,
                    callback_rows,
                    guardrail_rows,
                    llm_rows,
                    context_key,
                )
                for k in sums:
                    sums[k] += child_sums[k]
        return sums

    @staticmethod
    def build_summary_df(
        df_d: pd.DataFrame, group_cols: list[str]
    ) -> pd.DataFrame:
        """Aggregates a detailed DataFrame into counts and latency
        percentiles."""
        if df_d.empty:
            return pd.DataFrame(
                columns=[
                    *group_cols,
                    "count",
                    "Average (ms)",
                    "p50 (ms)",
                    "p90 (ms)",
                    "p99 (ms)",
                ]
            )

        agg_df = (
            df_d.groupby(group_cols)
            .agg(
                count=("duration_ms", "count"),
                Average=("duration_ms", "mean"),
                p50=("duration_ms", lambda x: x.quantile(0.50)),
                p90=("duration_ms", lambda x: x.quantile(0.90)),
                p99=("duration_ms", lambda x: x.quantile(0.99)),
            )
            .reset_index()
        )

        for col in ["Average", "p50", "p90", "p99"]:
            agg_df[col] = agg_df[col].fillna(0).astype(int)

        agg_df.rename(
            columns={
                "Average": "Average (ms)",
                "p50": "p50 (ms)",
                "p90": "p90 (ms)",
                "p99": "p99 (ms)",
            },
            inplace=True,
        )

        agg_df = agg_df.sort_values(by="count", ascending=False).reset_index(
            drop=True
        )
        return agg_df

    @staticmethod
    def _build_llm_summary_df(
        df_d: pd.DataFrame, group_cols: list[str]
    ) -> pd.DataFrame:
        """Aggregates a detailed LLM DataFrame into counts, percentiles, and
        average tokens."""
        if df_d.empty:
            return pd.DataFrame(
                columns=[
                    *group_cols,
                    "count",
                    "Average Input Tokens",
                    "Average (ms)",
                    "p50 (ms)",
                    "p90 (ms)",
                    "p99 (ms)",
                ]
            )

        agg_df = (
            df_d.groupby(group_cols)
            .agg(
                count=("duration_ms", "count"),
                Average_Input_Tokens=("input_tokens", "mean"),
                Average=("duration_ms", "mean"),
                p50=("duration_ms", lambda x: x.quantile(0.50)),
                p90=("duration_ms", lambda x: x.quantile(0.90)),
                p99=("duration_ms", lambda x: x.quantile(0.99)),
            )
            .reset_index()
        )

        for col in ["Average_Input_Tokens", "Average", "p50", "p90", "p99"]:
            agg_df[col] = agg_df[col].fillna(0).astype(int)

        agg_df.rename(
            columns={
                "Average_Input_Tokens": "Average Input Tokens",
                "Average": "Average (ms)",
                "p50": "p50 (ms)",
                "p90": "p90 (ms)",
                "p99": "p99 (ms)",
            },
            inplace=True,
        )

        agg_df = agg_df.sort_values(by="count", ascending=False).reset_index(
            drop=True
        )
        return agg_df

    @staticmethod
    def extract_trace_metrics(
        traces: dict[str, Any], context_type: str = "conversation"
    ) -> dict[str, pd.DataFrame]:
        """
        Extracts execution traces from Conversation history objects.

        Args:
            traces: A dictionary mapping `{conversation_id: Conversation
                object dict}`.
            context_type: Whether the `context_id` should represent the
                native `conversation_id` or an `eval_result_id`. Currently
                defaults to mapping conversation_ids natively, as eval routing
                expects EvalUtils to coordinate the span tree walk directly for
                synchronized sequence generation.

        Returns:
            Dictionary mapped to generic 6 Pandas DataFrames.
        """
        tool_details_rows = []
        callback_details_rows = []
        guardrail_details_rows = []
        llm_details_rows = []

        for cid, conv in traces.items():
            conv_dict = (
                type(conv).to_dict(conv) if not isinstance(conv, dict) else conv
            )
            conv_turns = conv_dict.get("turns", [])
            for turn_idx, t in enumerate(conv_turns):
                root = t.get("root_span", t.get("rootSpan", {}))
                if root:
                    _ = LatencyParser._process_spans(
                        [root],
                        cid,
                        turn_idx + 1,
                        tool_details_rows,
                        callback_details_rows,
                        guardrail_details_rows,
                        llm_details_rows,
                    )

        tool_details = pd.DataFrame(tool_details_rows)
        callback_details = pd.DataFrame(callback_details_rows)
        guardrail_details = pd.DataFrame(guardrail_details_rows)
        llm_details = pd.DataFrame(llm_details_rows)

        tool_summary = LatencyParser.build_summary_df(
            tool_details, ["tool_name"]
        )
        callback_summary = LatencyParser.build_summary_df(
            callback_details, ["agent", "stage", "description"]
        )
        guardrail_summary = LatencyParser.build_summary_df(
            guardrail_details, ["agent", "name"]
        )
        llm_summary = LatencyParser._build_llm_summary_df(
            llm_details, ["agent", "model"]
        )

        return {
            "tool_summary": tool_summary,
            "tool_details": tool_details,
            "callback_summary": callback_summary,
            "callback_details": callback_details,
            "guardrail_summary": guardrail_summary,
            "guardrail_details": guardrail_details,
            "llm_summary": llm_summary,
            "llm_details": llm_details,
        }

    @staticmethod
    def _extract_turn_texts(turn_dict: dict[str, Any]) -> tuple[str, str]:
        """Extracts user utterance and agent response text from a turn dict."""
        user_parts: list[str] = []
        agent_parts: list[str] = []

        if turn_dict.get("user_utterance"):
            user_parts.append(str(turn_dict["user_utterance"]))
        if turn_dict.get("agent_text"):
            agent_parts.append(str(turn_dict["agent_text"]))

        messages = turn_dict.get("messages", [])
        if isinstance(messages, list):
            for m in messages:
                if not isinstance(m, dict):
                    continue
                role = str(m.get("role", "")).lower()
                chunks = m.get("chunks", []) or []
                for c in chunks:
                    if not isinstance(c, dict):
                        continue
                    txt = c.get("text") or c.get("transcript")
                    if txt and isinstance(txt, str):
                        if role == "user" and not user_parts:
                            user_parts.append(txt)
                        elif role != "user" and not turn_dict.get("agent_text"):
                            agent_parts.append(txt)
                    ev = c.get("event")
                    if isinstance(ev, dict) and not user_parts:
                        ev_name = ev.get("event", ev.get("name", "event"))
                        user_parts.append(f"event: {ev_name}")

        return " ".join(user_parts).strip(), " ".join(agent_parts).strip()

    @staticmethod
    def discover_callback_catalog(
        base_dirs: list[str] | None = None,
    ) -> dict[tuple[str, str], list[dict[str, str]]]:
        """Discovers callback folder names (without python_code.py) from
        local <Agent>.json files if available."""
        catalog: dict[tuple[str, str], list[dict[str, str]]] = {}
        roots: list[str] = [os.getcwd()]
        if base_dirs:
            for bd in base_dirs:
                if bd and not bd.startswith("gs://"):
                    abs_bd = os.path.abspath(bd)
                    cur = (
                        abs_bd
                        if os.path.isdir(abs_bd)
                        else os.path.dirname(abs_bd)
                    )
                    for _ in range(4):
                        if cur and cur not in roots:
                            roots.append(cur)
                        parent = os.path.dirname(cur)
                        if parent == cur:
                            break
                        cur = parent

        stage_map = {
            "beforeAgentCallbacks": "BeforeAgent",
            "beforeModelCallbacks": "BeforeModel",
            "afterModelCallbacks": "AfterModel",
            "beforeToolCallbacks": "BeforeTool",
            "afterToolCallbacks": "AfterTool",
            "afterAgentCallbacks": "AfterAgent",
        }
        rel_patterns = [
            "build/voice/merged/agents/*/*.json",
            "build/chat/merged/agents/*/*.json",
            "voice/agents/*/*.json",
            "chat/agents/*/*.json",
            "common/agents/*/*.json",
            "agents/*/*.json",
        ]
        for root in roots:
            for pat in rel_patterns:
                for fp in sorted(glob.glob(os.path.join(root, pat))):
                    with contextlib.suppress(Exception):
                        with open(fp, encoding="utf-8") as f:
                            data = json.load(f)
                        if not isinstance(data, dict):
                            continue
                        disp_name = str(data.get("displayName", "") or "")
                        dir_name = os.path.basename(os.path.dirname(fp))
                        for json_key, stage_name in stage_map.items():
                            cb_list = data.get(json_key)
                            if not isinstance(cb_list, list) or not cb_list:
                                continue
                            parsed_cbs: list[dict[str, str]] = []
                            for idx, cb in enumerate(cb_list, start=1):
                                if not isinstance(cb, dict):
                                    continue
                                raw_path = str(cb.get("pythonCode", "") or "")
                                clean_path = re.sub(
                                    r"/?python_code\.py$", "", raw_path.strip()
                                )
                                folder = (
                                    clean_path.split("/")[-1]
                                    if clean_path
                                    else f"callback_{idx:02d}"
                                )
                                desc = str(cb.get("description", "") or "")
                                parsed_cbs.append(
                                    {
                                        "cb_name": folder,
                                        "description": desc,
                                    }
                                )
                            if parsed_cbs:
                                if disp_name:
                                    catalog.setdefault(
                                        (disp_name, stage_name), parsed_cbs
                                    )
                                if dir_name:
                                    catalog.setdefault(
                                        (dir_name, stage_name), parsed_cbs
                                    )
        return catalog

    @staticmethod
    def _resolve_callback_info(
        agent_name: str,
        stage_label: str,
        seq_idx: int,
        desc: str,
        inline_label: str,
        callback_catalog: dict[tuple[str, str], list[dict[str, str]]]
        | None = None,
    ) -> tuple[str, str]:
        """Resolves concise callback folder name (excluding python_code.py)
        and optional description."""
        seq = max(1, int(seq_idx or 1))
        stage_norm_map = {
            "beforeagent": "BeforeAgent",
            "beforeagentcallback": "BeforeAgent",
            "beforeagentcallbacks": "BeforeAgent",
            "beforemodel": "BeforeModel",
            "beforemodelcallback": "BeforeModel",
            "beforemodelcallbacks": "BeforeModel",
            "aftermodel": "AfterModel",
            "aftermodelcallback": "AfterModel",
            "aftermodelcallbacks": "AfterModel",
            "beforetool": "BeforeTool",
            "beforetoolcallback": "BeforeTool",
            "beforetoolcallbacks": "BeforeTool",
            "aftertool": "AfterTool",
            "aftertoolcallback": "AfterTool",
            "aftertoolcallbacks": "AfterTool",
            "afteragent": "AfterAgent",
            "afteragentcallback": "AfterAgent",
            "afteragentcallbacks": "AfterAgent",
        }
        norm_stage = stage_norm_map.get(
            re.sub(r"[^a-z0-9]", "", stage_label.lower()), stage_label
        )
        if callback_catalog:
            cat_list = (
                callback_catalog.get((agent_name, norm_stage))
                or callback_catalog.get(
                    (agent_name.replace(" ", "_"), norm_stage)
                )
                or callback_catalog.get((agent_name, stage_label))
            )
            if cat_list:
                if desc:
                    for item in cat_list:
                        if item.get("description") == desc:
                            return item["cb_name"], desc
                if 1 <= seq <= len(cat_list):
                    item = cat_list[seq - 1]
                    return (
                        item["cb_name"],
                        desc or item.get("description", ""),
                    )

        if (
            inline_label
            and re.sub(r"[^a-z0-9]", "", inline_label.lower())
            not in stage_norm_map
        ):
            clean_inline = re.sub(
                r"/?python_code\.py$", "", inline_label.strip()
            ).split("/")[-1]
            if clean_inline:
                return clean_inline, desc

        s_clean = norm_stage.replace(" ", "")
        stage_snake = re.sub(r"(?<!^)(?=[A-Z])", "_", s_clean).lower()
        stage_snake = re.sub(r"_calls?$", "", stage_snake)
        stage_snake = re.sub(r"_callbacks?$", "", stage_snake)
        if not stage_snake:
            stage_snake = "callback"
        return f"{stage_snake}_callbacks_{seq:02d}", desc

    @staticmethod
    def _flatten_turn_spans(
        root_span: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Flattens child spans under root_span into normalized span dicts."""
        flat: list[dict[str, Any]] = []
        pass_counter = 0

        def _collect_child_tools(node: dict[str, Any]) -> list[dict[str, Any]]:
            tools_found: list[dict[str, Any]] = []
            for ch in node.get("child_spans", node.get("childSpans", [])) or []:
                if not isinstance(ch, dict):
                    continue
                ch_name = str(ch.get("name", "") or "")
                if ch_name == "Tool" or ch_name.upper().startswith("TOOL:"):
                    ch_attrs = LatencyParser._unwrap_attrs(
                        ch.get("attributes", {})
                    )
                    ch_dur = LatencyParser._parse_duration_ms(
                        ch.get("duration")
                    )
                    inline_t = (
                        ch_name.split(":", 1)[1].strip()
                        if ":" in ch_name
                        else ""
                    )
                    t_lbl = str(
                        ch_attrs.get(
                            "name",
                            ch_attrs.get(
                                "tool",
                                ch_attrs.get(
                                    "display_name",
                                    ch_attrs.get(
                                        "toolDisplayName", inline_t or "tool"
                                    ),
                                ),
                            ),
                        )
                        or "tool"
                    )
                    tools_found.append(
                        {"name": t_lbl, "dur_ms": round(ch_dur, 1)}
                    )
                tools_found.extend(_collect_child_tools(ch))
            return tools_found

        def _walk(
            node: dict[str, Any],
            depth: int = 0,
            parent_agent: str = "",
            cb_pass_id: int = 0,
            cb_seq: int = 0,
        ) -> None:
            nonlocal pass_counter
            if not isinstance(node, dict):
                return
            name = str(node.get("name", "") or "")
            start_ts = LatencyParser._parse_timestamp_s(
                node.get("start_time", node.get("startTime"))
            )
            end_ts = LatencyParser._parse_timestamp_s(
                node.get("end_time", node.get("endTime"))
            )
            dur_ms = LatencyParser._parse_duration_ms(node.get("duration"))
            if dur_ms <= 0 and start_ts is not None and end_ts is not None:
                dur_ms = max(0.0, (end_ts - start_ts) * 1000.0)
            if end_ts is None and start_ts is not None and dur_ms > 0:
                end_ts = start_ts + dur_ms / 1000.0

            attrs = LatencyParser._unwrap_attrs(node.get("attributes", {}))
            node_agent = str(attrs.get("agent", "") or "")
            if not node_agent and name.upper().startswith("AGENT:"):
                node_agent = name.split(":", 1)[1].strip()
            if not node_agent and parent_agent:
                node_agent = parent_agent
                attrs = {**attrs, "agent": node_agent}

            is_cb = name == "Callback" or name.upper().startswith("CALLBACK:")
            child_tools = _collect_child_tools(node) if is_cb else []

            if depth > 0 or name != "Turn":
                flat.append(
                    {
                        "name": name,
                        "depth": depth,
                        "start_ts": start_ts,
                        "end_ts": end_ts,
                        "dur_ms": dur_ms,
                        "attrs": attrs,
                        "cb_pass_id": cb_pass_id,
                        "cb_seq": cb_seq,
                        "child_tools": child_tools,
                    }
                )

            children = node.get("child_spans", node.get("childSpans", [])) or []
            cur_cb_key: tuple[str, str] | None = None
            cur_pass_id = 0
            cur_seq = 0
            for ch in children:
                if not isinstance(ch, dict):
                    continue
                ch_name = str(ch.get("name", "") or "")
                ch_upper = ch_name.upper()
                ch_attrs = LatencyParser._unwrap_attrs(ch.get("attributes", {}))
                ch_agent = str(ch_attrs.get("agent", "") or node_agent or "")
                if ch_name == "Callback" or ch_upper.startswith("CALLBACK:"):
                    inl = (
                        ch_name.split(":", 1)[1].strip()
                        if ":" in ch_name
                        else ""
                    )
                    ch_stage = str(
                        ch_attrs.get(
                            "stage",
                            ch_attrs.get("description", inl or "Callback"),
                        )
                        or "Callback"
                    )
                    cb_key = (ch_agent, ch_stage)
                    if cb_key == cur_cb_key:
                        cur_seq += 1
                    else:
                        pass_counter += 1
                        cur_pass_id = pass_counter
                        cur_cb_key = cb_key
                        cur_seq = 1
                    _walk(ch, depth + 1, ch_agent, cur_pass_id, cur_seq)
                else:
                    if (
                        ch_name in ("LLM", "Tool")
                        or ch_upper.startswith("TOOL:")
                        or ch_upper.startswith("AGENT:")
                    ):
                        cur_cb_key = None
                        cur_seq = 0
                    _walk(ch, depth + 1, ch_agent, 0, 0)

        _walk(root_span, 0)
        flat.sort(
            key=lambda x: (
                x["start_ts"] if x["start_ts"] is not None else float("inf"),
                x["depth"],
            )
        )
        return flat

    @staticmethod
    def parse_turn_perceived_latency(
        turn_dict: dict[str, Any],
        turn_idx: int = 0,
        callback_catalog: dict[tuple[str, str], list[dict[str, str]]]
        | None = None,
    ) -> dict[str, Any] | None:
        """Computes Perceived Latency (PL), critical-path breakdown, filler
        masking, and waterfall spans for a single turn.

        Perceived Latency is defined as:
            T_first_audio - T_start
        where T_start is VAD.endTime (when the customer stops speaking) for
        customer audio turns, or earliest span start for event/poll turns, and
        T_first_audio is FirstAudioProducingSpan.startTime + TTFA.
        """
        if not isinstance(turn_dict, dict):
            return None
        root_span = turn_dict.get("root_span", turn_dict.get("rootSpan"))
        if not isinstance(root_span, dict) or not root_span:
            return None

        user_text, agent_text = LatencyParser._extract_turn_texts(turn_dict)
        spans = LatencyParser._flatten_turn_spans(root_span)
        root_start_ts = LatencyParser._parse_timestamp_s(
            root_span.get("start_time", root_span.get("startTime"))
        )
        root_end_ts = LatencyParser._parse_timestamp_s(
            root_span.get("end_time", root_span.get("endTime"))
        )
        root_dur_ms = LatencyParser._parse_duration_ms(
            root_span.get("duration")
        )

        valid_starts = [
            s["start_ts"] for s in spans if s["start_ts"] is not None
        ]
        valid_ends = [s["end_ts"] for s in spans if s["end_ts"] is not None]
        earliest_ts = min(valid_starts) if valid_starts else root_start_ts
        latest_ts = max(valid_ends) if valid_ends else root_end_ts
        if earliest_ts is None:
            return None

        vad_spans = [s for s in spans if s["name"] == "VAD"]
        vad_span = vad_spans[-1] if vad_spans else None

        # Determine turn category & T_start
        if vad_span and vad_span.get("end_ts") is not None:
            category = "customer_audio"
            category_label = "Customer Audio"
            t_start = vad_span["end_ts"]
            t_origin = (
                vad_span["start_ts"]
                if vad_span.get("start_ts") is not None
                else t_start
            )
            vad_dur_ms = vad_span["dur_ms"]
        else:
            t_start = earliest_ts
            t_origin = earliest_ts
            vad_dur_ms = 0.0
            u_low = user_text.lower()
            if turn_idx == 0 or "welcome" in u_low:
                category = "session_start"
                category_label = "Session Start"
            elif (
                "inactivity" in u_low
                or u_low.startswith("event:")
                or not user_text
            ):
                category = "inactivity_poll"
                category_label = "Inactivity / Hold Poll"
            else:
                category = "customer_audio"
                category_label = "Customer Turn (Text)"

        # Identify audio-producing spans
        audio_spans: list[dict[str, Any]] = []
        for s in spans:
            attrs = s["attrs"]
            ttfa_ms = float(attrs.get("time to first audio (ms)", 0) or 0)
            audio_dur_ms = float(attrs.get("audio duration (ms)", 0) or 0)
            if (ttfa_ms > 0 or audio_dur_ms > 0) and s["start_ts"] is not None:
                offset_s = (
                    ttfa_ms / 1000.0 if ttfa_ms > 0 else (s["dur_ms"] / 1000.0)
                )
                first_audio_ts = s["start_ts"] + offset_s
                audio_spans.append(
                    {
                        **s,
                        "ttfa_ms": ttfa_ms,
                        "audio_dur_ms": audio_dur_ms,
                        "first_audio_ts": first_audio_ts,
                    }
                )
        audio_spans.sort(key=lambda x: x["first_audio_ts"])

        # Determine T_first_audio & Perceived Latency
        is_silent = False
        pl_ms: float | None = None
        unmasked_pl_ms: float | None = None
        filler_masked = False
        filler_saved_ms = 0.0
        pl_source = "None (Silent)"
        first_audio_ts: float | None = None
        first_audio_span: dict[str, Any] | None = None

        if audio_spans:
            first_audio_span = audio_spans[0]
            first_audio_ts = first_audio_span["first_audio_ts"]
            pl_ms = max(0.0, (first_audio_ts - t_start) * 1000.0)
            fa_name = str(first_audio_span["name"])
            if (
                fa_name == "Callback"
                or fa_name.upper().startswith("CALLBACK")
                or fa_name == "Tool"
                or fa_name.upper().startswith("TOOL")
            ):
                stage = first_audio_span["attrs"].get(
                    "stage",
                    first_audio_span["attrs"].get(
                        "toolDisplayName", "Callback"
                    ),
                )
                pl_source = f"Filler ({stage})"
                filler_masked = True
                # Look for subsequent LLM audio span or end of tool+LLM chain
                later_llm_audio = [
                    a for a in audio_spans[1:] if a["name"] == "LLM"
                ]
                if later_llm_audio:
                    unmasked_ts = later_llm_audio[0]["first_audio_ts"]
                    unmasked_pl_ms = max(
                        pl_ms, (unmasked_ts - t_start) * 1000.0
                    )
                else:
                    later_spans = [
                        s
                        for s in spans
                        if (
                            s["name"] in ("Tool", "LLM")
                            or str(s["name"]).upper().startswith("TOOL")
                        )
                        and s.get("end_ts") is not None
                        and s["end_ts"] > first_audio_ts
                    ]
                    if later_spans:
                        unmasked_ts = max(s["end_ts"] for s in later_spans)
                        unmasked_pl_ms = max(
                            pl_ms, (unmasked_ts - t_start) * 1000.0
                        )
                    else:
                        unmasked_pl_ms = pl_ms
                filler_saved_ms = max(0.0, (unmasked_pl_ms or pl_ms) - pl_ms)
            else:
                model_name = first_audio_span["attrs"].get("model", "LLM")
                pl_source = f"LLM ({model_name})"
                unmasked_pl_ms = pl_ms
        else:
            # Check if text modality turn with non-empty agent response
            llm_spans = [
                s
                for s in spans
                if s["name"] == "LLM" and s.get("start_ts") is not None
            ]
            if agent_text and llm_spans:
                target_llm = llm_spans[-1]
                ttfc_ms = float(
                    target_llm["attrs"].get(
                        "time to first chunk (ms)",
                        target_llm["attrs"].get("time to first token (ms)", 0),
                    )
                    or 0
                )
                offset_s = (
                    ttfc_ms / 1000.0
                    if ttfc_ms > 0
                    else (target_llm["dur_ms"] / 1000.0)
                )
                first_audio_ts = target_llm["start_ts"] + offset_s
                first_audio_span = target_llm
                pl_ms = max(0.0, (first_audio_ts - t_start) * 1000.0)
                unmasked_pl_ms = pl_ms
                model_name = target_llm["attrs"].get("model", "LLM")
                pl_source = f"LLM ({model_name} - Text)"
            else:
                is_silent = True

        # Compute Pre-Speech Critical-Path Breakdown & Component Invocations
        llm_ttfc_ms = 0.0
        tts_ms = 0.0
        callback_pre_ms = 0.0
        tool_pre_ms = 0.0
        guardrail_pre_ms = 0.0
        component_records: list[dict[str, Any]] = []
        tool_records: list[dict[str, Any]] = []
        waterfall_spans: list[dict[str, Any]] = []

        total_timeline_end = latest_ts or (
            t_origin + max(root_dur_ms, pl_ms or 0.0, 100.0) / 1000.0
        )
        if first_audio_ts is not None:
            total_timeline_end = max(total_timeline_end, first_audio_ts)
        total_timeline_ms = max(100.0, (total_timeline_end - t_origin) * 1000.0)
        first_audio_rel_ms = (
            max(0.0, (first_audio_ts - t_origin) * 1000.0)
            if first_audio_ts is not None
            else None
        )

        cb_pass_to_wf: dict[tuple[int, bool], dict[str, Any]] = {}

        for s in spans:
            raw_s_name = str(s["name"] or "")
            s_upper = raw_s_name.upper()
            if raw_s_name in ("VAD", "Callback", "LLM", "Tool", "Guardrail"):
                s_name = raw_s_name
                inline_label = ""
            elif s_upper.startswith("CALLBACK:"):
                s_name = "Callback"
                inline_label = raw_s_name.split(":", 1)[1].strip()
            elif s_upper.startswith("TOOL:"):
                s_name = "Tool"
                inline_label = raw_s_name.split(":", 1)[1].strip()
            elif s_upper.startswith("GUARDRAIL:"):
                s_name = "Guardrail"
                inline_label = raw_s_name.split(":", 1)[1].strip()
            else:
                continue

            s_start = s["start_ts"]
            s_end = s["end_ts"]
            if s_start is None or s_end is None:
                continue

            attrs = s["attrs"]
            agent_name = str(attrs.get("agent", "") or "Root Agent")
            dur_ms = s["dur_ms"]

            # Overlap with [t_start, first_audio_ts]
            pre_ms = 0.0
            is_pre_speech = False
            if first_audio_ts is not None and s_name != "VAD":
                ov_start = max(t_start, s_start)
                ov_end = min(first_audio_ts, s_end)
                if ov_end > ov_start:
                    pre_ms = (ov_end - ov_start) * 1000.0
                    is_pre_speech = True

            ttfc_val = float(
                attrs.get(
                    "time to first chunk (ms)",
                    attrs.get("time to first token (ms)", 0),
                )
                or 0
            )
            ttfa_val = float(attrs.get("time to first audio (ms)", 0) or 0)
            in_tok = int(float(attrs.get("input token count", 0) or 0))
            out_tok = int(float(attrs.get("output token count", 0) or 0))

            cb_seq = int(s.get("cb_seq", 0) or 1)
            cb_pass_id = int(s.get("cb_pass_id", 0) or 0)
            cb_name = ""
            cb_desc = ""
            code_ms = 0.0
            ext_wait_ms = 0.0
            sandbox_init_ms = 0.0
            sandbox_overhead_ms = 0.0
            has_detailed_latency = False
            child_tools = s.get("child_tools", []) or []

            if s_name == "LLM":
                model_label = str(attrs.get("model", "gemini") or "gemini")
                comp_label = f"LLM: {model_label}"
                comp_type = "LLM"
                wf_kind = "llm"
                detail_parts = []
                if ttfc_val > 0:
                    detail_parts.append(f"TTFC {round(ttfc_val)}ms")
                if ttfa_val > 0:
                    detail_parts.append(f"TTFA {round(ttfa_val)}ms")
                if in_tok > 0 or out_tok > 0:
                    detail_parts.append(f"{in_tok}->{out_tok} tok")
                wf_detail = ", ".join(detail_parts) or f"{round(dur_ms)}ms"

                if is_pre_speech:
                    if ttfa_val > 0 and ttfc_val > 0 and ttfa_val >= ttfc_val:
                        tts_part = min(pre_ms, max(0.0, ttfa_val - ttfc_val))
                        llm_part = max(0.0, pre_ms - tts_part)
                        llm_ttfc_ms += llm_part
                        tts_ms += tts_part
                    else:
                        llm_ttfc_ms += pre_ms
            elif s_name == "Callback":
                stage_label = str(
                    attrs.get(
                        "stage",
                        attrs.get("description", inline_label or "Callback"),
                    )
                    or "Callback"
                )
                comp_label = f"Callback: {stage_label}"
                comp_type = "Callback"
                wf_kind = "cb"
                raw_desc = str(attrs.get("description", "") or "")
                cb_name, cb_desc = LatencyParser._resolve_callback_info(
                    agent_name=agent_name,
                    stage_label=stage_label,
                    seq_idx=cb_seq,
                    desc=raw_desc,
                    inline_label=inline_label,
                    callback_catalog=callback_catalog,
                )
                det_lat = attrs.get("[debug] detailed latency", {})
                if isinstance(det_lat, dict) and det_lat:
                    has_detailed_latency = True
                    raw_code_ms = float(
                        det_lat.get("total code execution latency (ms)", 0) or 0
                    )
                    ext_wait_ms = float(
                        det_lat.get("waiting for external calls (ms)", 0) or 0
                    )
                    sandbox_init_ms = float(
                        det_lat.get("sandbox init latency (ms)", 0) or 0
                    )
                    code_ms = max(0.0, raw_code_ms - ext_wait_ms)
                    sandbox_overhead_ms = max(
                        0.0, dur_ms - code_ms - ext_wait_ms
                    )
                if ttfa_val > 0:
                    wf_detail = (
                        f"#{cb_seq} {cb_name} · "
                        f"Filler TTFA {round(ttfa_val)}ms ({round(dur_ms)}ms)"
                    )
                else:
                    wf_detail = f"1 callback: {cb_name} · {round(dur_ms)}ms"
                if is_pre_speech:
                    callback_pre_ms += pre_ms
            elif s_name == "Tool":
                t_label = str(
                    attrs.get(
                        "name",
                        attrs.get(
                            "tool",
                            attrs.get(
                                "display_name",
                                attrs.get(
                                    "toolDisplayName", inline_label or "tool"
                                ),
                            ),
                        ),
                    )
                    or "tool"
                )
                comp_label = f"Tool: {t_label}"
                comp_type = "Tool / API"
                wf_kind = "tool"
                wf_detail = f"{round(dur_ms)}ms"
                if is_pre_speech:
                    tool_pre_ms += pre_ms
                tool_records.append(
                    {
                        "tool_name": t_label,
                        "agent": agent_name,
                        "dur_ms": dur_ms,
                        "pre_speech_ms": pre_ms,
                        "is_pre_speech": is_pre_speech,
                        "masked_by_filler": filler_masked and not is_pre_speech,
                        "turn_pl_ms": pl_ms,
                        "turn_unmasked_pl_ms": unmasked_pl_ms,
                        "turn_category": category,
                    }
                )
            elif s_name == "Guardrail":
                g_label = str(
                    attrs.get(
                        "name",
                        attrs.get("description", inline_label or "Guardrail"),
                    )
                    or "Guardrail"
                )
                comp_label = f"Guardrail: {g_label}"
                comp_type = "Guardrail"
                wf_kind = "guardrail"
                wf_detail = f"{round(dur_ms)}ms"
                if is_pre_speech:
                    guardrail_pre_ms += pre_ms
            else:  # VAD
                comp_label = "Customer Speech (VAD)"
                comp_type = "VAD"
                wf_kind = "vad"
                wf_detail = f"{round(dur_ms)}ms"

            if s_name != "VAD":
                component_records.append(
                    {
                        "component": comp_label,
                        "type": comp_type,
                        "agent": agent_name,
                        "dur_ms": dur_ms,
                        "pre_speech_ms": pre_ms,
                        "is_pre_speech": is_pre_speech,
                        "masked_by_filler": filler_masked and not is_pre_speech,
                        "turn_category": category,
                        "cb_seq": cb_seq,
                        "cb_name": cb_name,
                        "cb_desc": cb_desc,
                        "code_ms": code_ms,
                        "ext_wait_ms": ext_wait_ms,
                        "sandbox_init_ms": sandbox_init_ms,
                        "sandbox_overhead_ms": sandbox_overhead_ms,
                        "has_detailed_latency": has_detailed_latency,
                        "child_tools": child_tools,
                        "ttfc_ms": ttfc_val,
                        "ttfa_ms": ttfa_val,
                        "in_tok": in_tok,
                        "out_tok": out_tok,
                    }
                )

            rel_start_ms = max(0.0, (s_start - t_origin) * 1000.0)
            rel_end_ms = max(rel_start_ms, (s_end - t_origin) * 1000.0)
            left_pct = min(
                98.0, max(0.0, (rel_start_ms / total_timeline_ms) * 100.0)
            )
            width_pct = max(
                1.2,
                min(
                    100.0 - left_pct,
                    ((rel_end_ms - rel_start_ms) / total_timeline_ms) * 100.0,
                ),
            )
            is_fa_span = (
                first_audio_span is not None
                and s_start == first_audio_span.get("start_ts")
                and raw_s_name == first_audio_span.get("name")
            )

            cb_item = (
                {
                    "seq": cb_seq,
                    "cb_name": cb_name,
                    "description": cb_desc,
                    "dur_ms": round(dur_ms, 1),
                    "pre_speech_ms": round(pre_ms, 1),
                    "code_ms": round(code_ms, 1),
                    "ext_wait_ms": round(ext_wait_ms, 1),
                    "sandbox_init_ms": round(sandbox_init_ms, 1),
                    "sandbox_overhead_ms": round(sandbox_overhead_ms, 1),
                    "has_detailed_latency": has_detailed_latency,
                    "ttfa_ms": round(ttfa_val, 1),
                    "child_tools": child_tools,
                    "left_pct": round(left_pct, 2),
                    "width_pct": round(width_pct, 2),
                }
                if wf_kind == "cb"
                else None
            )

            pass_key = (cb_pass_id, is_pre_speech)
            existing_cb_wf = (
                cb_pass_to_wf.get(pass_key)
                if (wf_kind == "cb" and not is_fa_span and cb_pass_id > 0)
                else None
            )
            if existing_cb_wf is not None and cb_item is not None:
                existing_cb_wf["callbacks"].append(cb_item)
                existing_cb_wf["count"] = len(existing_cb_wf["callbacks"])
                existing_cb_wf["dur_ms"] = round(
                    existing_cb_wf["dur_ms"] + dur_ms, 1
                )
                existing_cb_wf["pre_speech_ms"] = round(
                    existing_cb_wf["pre_speech_ms"] + pre_ms, 1
                )
                existing_cb_wf["rel_end_ms"] = round(
                    max(existing_cb_wf["rel_end_ms"], rel_end_ms), 1
                )
                existing_cb_wf["width_pct"] = round(
                    max(
                        1.2,
                        min(
                            100.0 - existing_cb_wf["left_pct"],
                            (
                                (
                                    existing_cb_wf["rel_end_ms"]
                                    - existing_cb_wf["rel_start_ms"]
                                )
                                / total_timeline_ms
                            )
                            * 100.0,
                        ),
                    ),
                    2,
                )
                cnt = existing_cb_wf["count"]
                existing_cb_wf["label"] = comp_label
                existing_cb_wf["detail"] = (
                    f"{cnt} callbacks · {round(existing_cb_wf['dur_ms'])}ms"
                )
            else:
                new_wf: dict[str, Any] = {
                    "kind": wf_kind,
                    "base_label": comp_label,
                    "label": comp_label,
                    "count": 1,
                    "agent": agent_name,
                    "detail": wf_detail,
                    "rel_start_ms": round(rel_start_ms, 1),
                    "rel_end_ms": round(rel_end_ms, 1),
                    "dur_ms": round(dur_ms, 1),
                    "pre_speech_ms": round(pre_ms, 1),
                    "is_pre_speech": is_pre_speech,
                    "left_pct": round(left_pct, 2),
                    "width_pct": round(width_pct, 2),
                    "is_first_audio": is_fa_span,
                    "cb_pass_id": cb_pass_id,
                    "callbacks": [cb_item] if cb_item is not None else [],
                }
                waterfall_spans.append(new_wf)
                if wf_kind == "cb" and not is_fa_span and cb_pass_id > 0:
                    cb_pass_to_wf[pass_key] = new_wf

        # Normalize Pre-Speech breakdown against pl_ms
        raw_sum = (
            llm_ttfc_ms
            + tts_ms
            + callback_pre_ms
            + tool_pre_ms
            + guardrail_pre_ms
        )
        overhead_ms = 0.0
        if pl_ms is not None and pl_ms > 0:
            if raw_sum > pl_ms and raw_sum > 0:
                scale = pl_ms / raw_sum
                llm_ttfc_ms *= scale
                tts_ms *= scale
                callback_pre_ms *= scale
                tool_pre_ms *= scale
                guardrail_pre_ms *= scale
            else:
                overhead_ms = max(0.0, pl_ms - raw_sum)

        # Determine primary bottleneck label for this turn
        if is_silent:
            bottleneck_label = "Silent Turn (No Audio)"
        elif filler_masked:
            bottleneck_label = (
                f"Filler Masked (Saved {round(filler_saved_ms)}ms)"
            )
        else:
            pre_candidates = [
                c for c in component_records if c["pre_speech_ms"] > 0
            ]
            if pre_candidates:
                top_c = max(pre_candidates, key=lambda x: x["pre_speech_ms"])
                c_lbl = top_c["component"]
                if top_c.get("cb_name") and top_c["type"] == "Callback":
                    c_lbl = f"{c_lbl} ({top_c['cb_name']})"
                bottleneck_label = (
                    f"{c_lbl} ({round(top_c['pre_speech_ms'])}ms)"
                )
            else:
                bottleneck_label = "Platform / Handoff"

        # Compact pre-speech summary string
        summary_parts = []
        if callback_pre_ms >= 5:
            summary_parts.append(f"CB {round(callback_pre_ms)}ms")
        if tool_pre_ms >= 5:
            summary_parts.append(f"Tool {round(tool_pre_ms)}ms")
        if llm_ttfc_ms >= 5:
            summary_parts.append(f"LLM TTFC {round(llm_ttfc_ms)}ms")
        if tts_ms >= 5:
            summary_parts.append(f"TTS {round(tts_ms)}ms")
        if guardrail_pre_ms + overhead_ms >= 5:
            summary_parts.append(
                f"Overhead {round(guardrail_pre_ms + overhead_ms)}ms"
            )
        pre_speech_summary = (
            " + ".join(summary_parts) if summary_parts else "N/A"
        )

        first_audio_pct = (
            min(
                99.0,
                max(0.0, (first_audio_rel_ms / total_timeline_ms) * 100.0),
            )
            if first_audio_rel_ms is not None
            else None
        )
        vad_end_rel_ms = max(0.0, (t_start - t_origin) * 1000.0)
        vad_end_pct = min(
            99.0, max(0.0, (vad_end_rel_ms / total_timeline_ms) * 100.0)
        )

        return {
            "turn_index": turn_idx,
            "turn_num": turn_idx + 1,
            "category": category,
            "category_label": category_label,
            "user_text": user_text,
            "agent_text": agent_text,
            "is_silent": is_silent,
            "pl_ms": round(pl_ms, 1) if pl_ms is not None else None,
            "unmasked_pl_ms": (
                round(unmasked_pl_ms, 1) if unmasked_pl_ms is not None else None
            ),
            "filler_masked": filler_masked,
            "filler_saved_ms": round(filler_saved_ms, 1),
            "pl_source": pl_source,
            "vad_dur_ms": round(vad_dur_ms, 1),
            "total_turn_ms": round(
                root_dur_ms if root_dur_ms > 0 else total_timeline_ms, 1
            ),
            "total_timeline_ms": round(total_timeline_ms, 1),
            "first_audio_rel_ms": (
                round(first_audio_rel_ms, 1)
                if first_audio_rel_ms is not None
                else None
            ),
            "first_audio_pct": (
                round(first_audio_pct, 2)
                if first_audio_pct is not None
                else None
            ),
            "vad_end_rel_ms": round(vad_end_rel_ms, 1),
            "vad_end_pct": round(vad_end_pct, 2),
            "breakdown_ms": {
                "llm_ttfc_ms": round(llm_ttfc_ms, 1),
                "tts_ms": round(tts_ms, 1),
                "callback_ms": round(callback_pre_ms, 1),
                "tool_ms": round(tool_pre_ms, 1),
                "overhead_ms": round(guardrail_pre_ms + overhead_ms, 1),
            },
            "pre_speech_summary": pre_speech_summary,
            "bottleneck_label": bottleneck_label,
            "component_records": component_records,
            "tool_records": tool_records,
            "waterfall_spans": waterfall_spans,
        }

    @staticmethod
    def analyze_conversation_perceived_latency(
        conv_dict: dict[str, Any],
        session_id: str = "",
        conv_name: str = "",
        callback_catalog: dict[tuple[str, str], list[dict[str, str]]]
        | None = None,
    ) -> dict[str, Any] | None:
        """Analyze Perceived Latency across all turns of a conversation."""
        if not isinstance(conv_dict, dict):
            return None
        turns_raw = conv_dict.get("turns", conv_dict.get("turn_traces", []))
        if not isinstance(turns_raw, list) or not turns_raw:
            return None

        parsed_turns: list[dict[str, Any]] = []
        for idx, t in enumerate(turns_raw):
            pt = LatencyParser.parse_turn_perceived_latency(
                t, idx, callback_catalog=callback_catalog
            )
            if pt is not None:
                parsed_turns.append(pt)

        if not parsed_turns:
            return None

        audio_turns = [
            t
            for t in parsed_turns
            if t["category"] == "customer_audio" and t["pl_ms"] is not None
        ]
        spoken_turns = [t for t in parsed_turns if t["pl_ms"] is not None]
        primary_turns = audio_turns if audio_turns else spoken_turns
        pl_vals = [float(t["pl_ms"]) for t in primary_turns]
        unmasked_vals = [
            float(t["unmasked_pl_ms"] or t["pl_ms"]) for t in primary_turns
        ]
        filler_turns = [t for t in parsed_turns if t["filler_masked"]]

        avg_ms = sum(pl_vals) / len(pl_vals) if pl_vals else 0.0
        p50_ms = LatencyParser._percentile(pl_vals, 50)
        p90_ms = LatencyParser._percentile(pl_vals, 90)
        max_ms = max(pl_vals) if pl_vals else 0.0
        unmasked_avg_ms = (
            sum(unmasked_vals) / len(unmasked_vals) if unmasked_vals else 0.0
        )
        filler_saved_avg_ms = (
            sum(t["filler_saved_ms"] for t in filler_turns) / len(filler_turns)
            if filler_turns
            else 0.0
        )

        return {
            "session_id": session_id or str(conv_dict.get("session_id", "")),
            "conv_name": conv_name,
            "total_turns": len(parsed_turns),
            "spoken_turns_count": len(spoken_turns),
            "audio_turns_count": len(audio_turns),
            "primary_slice_label": (
                "Customer Audio" if audio_turns else "All Spoken"
            ),
            "avg_ms": round(avg_ms, 1),
            "p50_ms": round(p50_ms, 1),
            "p90_ms": round(p90_ms, 1),
            "max_ms": round(max_ms, 1),
            "unmasked_avg_ms": round(unmasked_avg_ms, 1),
            "filler_masked_count": len(filler_turns),
            "filler_saved_avg_ms": round(filler_saved_avg_ms, 1),
            "turns": parsed_turns,
        }

    @staticmethod
    def _build_slice_metrics(
        turns: list[dict[str, Any]], label: str
    ) -> dict[str, Any]:
        """Build KPI and stacked pre-speech breakdown metrics for a slice."""
        spoken = [t for t in turns if t["pl_ms"] is not None]
        silent_count = len(turns) - len(spoken)
        pl_vals = [float(t["pl_ms"]) for t in spoken]
        unmasked_vals = [
            float(t["unmasked_pl_ms"] or t["pl_ms"]) for t in spoken
        ]
        filler_turns = [t for t in spoken if t["filler_masked"]]

        n = len(pl_vals)
        avg_ms = sum(pl_vals) / n if n else 0.0
        p50_ms = LatencyParser._percentile(pl_vals, 50)
        p90_ms = LatencyParser._percentile(pl_vals, 90)
        p95_ms = LatencyParser._percentile(pl_vals, 95)
        max_ms = max(pl_vals) if n else 0.0
        unmasked_avg_ms = sum(unmasked_vals) / n if n else 0.0
        unmasked_p90_ms = LatencyParser._percentile(unmasked_vals, 90)
        filler_saved_avg_ms = (
            sum(t["filler_saved_ms"] for t in filler_turns) / len(filler_turns)
            if filler_turns
            else 0.0
        )

        under_1s_pct = (
            (sum(1 for v in pl_vals if v < 1000.0) / n * 100.0) if n else 0.0
        )
        over_2s_pct = (
            (sum(1 for v in pl_vals if v > 2000.0) / n * 100.0) if n else 0.0
        )
        over_3s_pct = (
            (sum(1 for v in pl_vals if v > 3000.0) / n * 100.0) if n else 0.0
        )

        # Average pre-speech breakdown across spoken turns
        if n > 0:
            llm_ms = sum(t["breakdown_ms"]["llm_ttfc_ms"] for t in spoken) / n
            tts_ms = sum(t["breakdown_ms"]["tts_ms"] for t in spoken) / n
            cb_ms = sum(t["breakdown_ms"]["callback_ms"] for t in spoken) / n
            tool_ms = sum(t["breakdown_ms"]["tool_ms"] for t in spoken) / n
            ov_ms = sum(t["breakdown_ms"]["overhead_ms"] for t in spoken) / n
        else:
            llm_ms = tts_ms = cb_ms = tool_ms = ov_ms = 0.0

        tot_stack = max(1.0, llm_ms + tts_ms + cb_ms + tool_ms + ov_ms)
        return {
            "label": label,
            "total_turns": len(turns),
            "spoken_count": n,
            "silent_count": silent_count,
            "avg_ms": round(avg_ms, 1),
            "p50_ms": round(p50_ms, 1),
            "p90_ms": round(p90_ms, 1),
            "p95_ms": round(p95_ms, 1),
            "max_ms": round(max_ms, 1),
            "unmasked_avg_ms": round(unmasked_avg_ms, 1),
            "unmasked_p90_ms": round(unmasked_p90_ms, 1),
            "filler_masked_count": len(filler_turns),
            "filler_saved_avg_ms": round(filler_saved_avg_ms, 1),
            "under_1s_pct": round(under_1s_pct, 1),
            "over_2s_pct": round(over_2s_pct, 1),
            "over_3s_pct": round(over_3s_pct, 1),
            "stack": {
                "llm_ttfc_ms": round(llm_ms, 1),
                "llm_ttfc_pct": round(llm_ms / tot_stack * 100.0, 1),
                "tts_ms": round(tts_ms, 1),
                "tts_pct": round(tts_ms / tot_stack * 100.0, 1),
                "callback_ms": round(cb_ms, 1),
                "callback_pct": round(cb_ms / tot_stack * 100.0, 1),
                "tool_ms": round(tool_ms, 1),
                "tool_pct": round(tool_ms / tot_stack * 100.0, 1),
                "overhead_ms": round(ov_ms, 1),
                "overhead_pct": round(ov_ms / tot_stack * 100.0, 1),
            },
        }

    @staticmethod
    def analyze_suite_perceived_latency(
        conv_analyses: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Aggregates Perceived Latency across a suite of conversations."""
        valid_convs = [
            c for c in conv_analyses if isinstance(c, dict) and c.get("turns")
        ]
        if not valid_convs:
            return None

        all_turns: list[dict[str, Any]] = []
        for c in valid_convs:
            all_turns.extend(c["turns"])

        if not all_turns:
            return None

        audio_turns = [
            t for t in all_turns if t["category"] == "customer_audio"
        ]
        session_start_turns = [
            t for t in all_turns if t["category"] == "session_start"
        ]
        poll_turns = [
            t for t in all_turns if t["category"] == "inactivity_poll"
        ]

        slices = {
            "customer_audio": LatencyParser._build_slice_metrics(
                audio_turns if audio_turns else all_turns,
                "Customer Audio Turns" if audio_turns else "All Spoken Turns",
            ),
            "session_start": LatencyParser._build_slice_metrics(
                session_start_turns, "Session Start (Welcome)"
            ),
            "inactivity_poll": LatencyParser._build_slice_metrics(
                poll_turns, "Inactivity / Hold Polls"
            ),
            "all_turns": LatencyParser._build_slice_metrics(
                all_turns, "All Spoken Turns"
            ),
        }

        primary_key = (
            "customer_audio"
            if slices["customer_audio"]["spoken_count"] > 0
            else "all_turns"
        )
        primary = slices[primary_key]

        # Build Table A: Pre-Speech Component Breakdown & Signals
        comp_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for t in all_turns:
            for rec in t.get("component_records", []):
                key = (rec["component"], rec["type"], rec["agent"])
                comp_groups.setdefault(key, []).append(rec)

        spoken_turn_count = max(1, slices["all_turns"]["spoken_count"])
        suite_avg_pl = max(1.0, float(slices["all_turns"].get("avg_ms") or 1.0))
        optimization_targets: list[dict[str, Any]] = []
        for (comp_name, comp_type, agent_name), recs in comp_groups.items():
            durs = [float(r["dur_ms"]) for r in recs]
            pre_durs = [float(r["pre_speech_ms"]) for r in recs]
            pre_calls = sum(1 for r in recs if r["is_pre_speech"])
            masked_calls = sum(1 for r in recs if r.get("masked_by_filler"))
            calls = len(recs)
            calls_per_turn = calls / spoken_turn_count
            pre_pct = (pre_calls / calls * 100.0) if calls else 0.0
            avg_dur = sum(durs) / calls if calls else 0.0
            p50_dur = LatencyParser._percentile(durs, 50)
            p90_dur = LatencyParser._percentile(durs, 90)
            max_dur = max(durs) if durs else 0.0
            total_pre_ms = sum(pre_durs)
            avg_pl_impact = total_pre_ms / spoken_turn_count
            share_of_pl_pct = min(100.0, (avg_pl_impact / suite_avg_pl) * 100.0)
            avg_pre_when_blocking = (
                total_pre_ms / pre_calls if pre_calls else 0.0
            )

            sub_callbacks: list[dict[str, Any]] = []
            if comp_type == "Callback":
                cb_by_seq: dict[tuple[int, str], list[dict[str, Any]]] = {}
                for r in recs:
                    c_seq = int(r.get("cb_seq", 1) or 1)
                    c_nm = str(r.get("cb_name", "") or "callback_01")
                    cb_by_seq.setdefault((c_seq, c_nm), []).append(r)
                for (c_seq, c_nm), c_recs in sorted(
                    cb_by_seq.items(), key=lambda x: (x[0][0], x[0][1])
                ):
                    c_durs = [float(cr["dur_ms"]) for cr in c_recs]
                    c_pre = [float(cr["pre_speech_ms"]) for cr in c_recs]
                    c_codes = [float(cr.get("code_ms", 0)) for cr in c_recs]
                    c_waits = [float(cr.get("ext_wait_ms", 0)) for cr in c_recs]
                    c_sandboxes = [
                        float(cr.get("sandbox_overhead_ms", 0)) for cr in c_recs
                    ]
                    c_has_detail = any(
                        bool(cr.get("has_detailed_latency")) for cr in c_recs
                    )
                    c_cnt = len(c_recs)
                    c_desc = next(
                        (
                            str(cr.get("cb_desc", ""))
                            for cr in c_recs
                            if cr.get("cb_desc")
                        ),
                        "",
                    )
                    tool_durs_map: dict[str, list[float]] = {}
                    for cr in c_recs:
                        for ct in cr.get("child_tools", []) or []:
                            tn = str(ct.get("name", ""))
                            if tn:
                                tool_durs_map.setdefault(tn, []).append(
                                    float(ct.get("dur_ms", 0))
                                )
                    child_tool_summaries = [
                        {
                            "name": tn,
                            "calls": len(td),
                            "avg_ms": round(sum(td) / len(td), 1),
                        }
                        for tn, td in tool_durs_map.items()
                    ]
                    sub_callbacks.append(
                        {
                            "seq": c_seq,
                            "cb_name": c_nm,
                            "description": c_desc,
                            "calls": c_cnt,
                            "avg_ms": round(sum(c_durs) / c_cnt, 1),
                            "p50_ms": round(
                                LatencyParser._percentile(c_durs, 50), 1
                            ),
                            "p90_ms": round(
                                LatencyParser._percentile(c_durs, 90), 1
                            ),
                            "total_pre_ms": round(sum(c_pre), 1),
                            "avg_code_ms": round(sum(c_codes) / c_cnt, 1),
                            "avg_ext_wait_ms": round(sum(c_waits) / c_cnt, 1),
                            "avg_sandbox_ms": round(
                                sum(c_sandboxes) / c_cnt, 1
                            ),
                            "has_detailed_latency": c_has_detail,
                            "child_tools": child_tool_summaries,
                        }
                    )

            # Build objective, raw telemetry signal observation
            obs_parts = [
                f"pre_speech={pre_calls}/{calls} ({pre_pct:.0f}%)",
                f"freq={calls_per_turn:.2g}x/turn",
            ]
            if comp_type == "LLM":
                ttfcs = [
                    float(r.get("ttfc_ms", 0))
                    for r in recs
                    if float(r.get("ttfc_ms", 0)) > 0
                ]
                ttfas = [
                    float(r.get("ttfa_ms", 0))
                    for r in recs
                    if float(r.get("ttfa_ms", 0)) > 0
                ]
                in_toks = [
                    int(r.get("in_tok", 0))
                    for r in recs
                    if int(r.get("in_tok", 0)) > 0
                ]
                out_toks = [
                    int(r.get("out_tok", 0))
                    for r in recs
                    if int(r.get("out_tok", 0)) > 0
                ]
                if ttfcs:
                    obs_parts.append(
                        f"avg_ttfc={round(sum(ttfcs) / len(ttfcs))}ms"
                    )
                if ttfas:
                    obs_parts.append(
                        f"avg_ttfa={round(sum(ttfas) / len(ttfas))}ms"
                    )
                if in_toks or out_toks:
                    avg_in = (
                        round(sum(in_toks) / len(in_toks)) if in_toks else 0
                    )
                    avg_out = (
                        round(sum(out_toks) / len(out_toks)) if out_toks else 0
                    )
                    obs_parts.append(f"avg_tokens={avg_in}->{avg_out}")
                obs_parts.append(f"max={round(max_dur):,}ms")
            elif comp_type == "Tool / API":
                unm_pre = sum(
                    1
                    for r in recs
                    if r["is_pre_speech"] and not r.get("masked_by_filler")
                )
                obs_parts.append(f"unmasked={unm_pre}/{calls}")
                obs_parts.append(f"filler_masked={masked_calls}/{calls}")
                if pre_calls > 0:
                    obs_parts.append(
                        f"avg_blocking={round(avg_pre_when_blocking):,}ms"
                    )
            elif comp_type == "Callback" and sub_callbacks:
                top_cb = max(sub_callbacks, key=lambda x: x["avg_ms"])
                obs_parts.append(
                    f"top={top_cb['cb_name']} "
                    f"(#{top_cb['seq']}, avg {round(top_cb['avg_ms'])}ms)"
                )
                cb_detailed = [
                    r for r in recs if r.get("has_detailed_latency")
                ]
                if cb_detailed and calls > 0:
                    avg_c_ms = round(
                        sum(float(r.get("code_ms", 0)) for r in recs) / calls
                    )
                    avg_w_ms = round(
                        sum(float(r.get("ext_wait_ms", 0)) for r in recs)
                        / calls
                    )
                    avg_s_ms = round(
                        sum(
                            float(r.get("sandbox_overhead_ms", 0)) for r in recs
                        )
                        / calls
                    )
                    bd_items = [f"code={avg_c_ms}ms"]
                    if avg_w_ms > 0:
                        bd_items.append(f"ext_wait={avg_w_ms}ms")
                    bd_items.append(f"sandbox={avg_s_ms}ms")
                    obs_parts.append(f"breakdown=[{', '.join(bd_items)}]")
                all_ct = [
                    ct["name"]
                    for sc in sub_callbacks
                    for ct in sc.get("child_tools", [])
                ]
                if all_ct:
                    shown = ", ".join(all_ct[:3])
                    more = (
                        f" +{len(all_ct) - 3} more" if len(all_ct) > 3 else ""
                    )
                    obs_parts.append(f"child_tools=[{shown}{more}]")
            else:
                if pre_calls > 0:
                    obs_parts.append(
                        f"avg_blocking={round(avg_pre_when_blocking):,}ms"
                    )
                obs_parts.append(f"max={round(max_dur):,}ms")

            obs_text = " · ".join(obs_parts)

            optimization_targets.append(
                {
                    "component": comp_name,
                    "type": comp_type,
                    "agent": agent_name,
                    "calls": calls,
                    "calls_per_turn": round(calls_per_turn, 2),
                    "pre_speech_calls": pre_calls,
                    "pre_speech_pct": round(pre_pct, 1),
                    "avg_ms": round(avg_dur, 1),
                    "p50_ms": round(p50_dur, 1),
                    "p90_ms": round(p90_dur, 1),
                    "max_ms": round(max_dur, 1),
                    "avg_pre_when_blocking_ms": round(avg_pre_when_blocking, 1),
                    "avg_pl_impact_ms": round(avg_pl_impact, 1),
                    "share_of_pl_pct": round(share_of_pl_pct, 1),
                    "total_pre_ms": round(total_pre_ms, 1),
                    "sub_callbacks": sub_callbacks,
                    "observation": obs_text,
                    "recommendation": obs_text,
                }
            )

        optimization_targets.sort(
            key=lambda x: (
                x["total_pre_ms"],
                x["avg_ms"] * x["calls"],
                x["p90_ms"],
            ),
            reverse=True,
        )

        # Build Table B: Pre-Speech Tool Masking & Dead-Air Signals
        tool_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for t in all_turns:
            for tr in t.get("tool_records", []):
                tkey = (tr["tool_name"], tr["agent"])
                tool_groups.setdefault(tkey, []).append(tr)

        all_filler_rows: list[dict[str, Any]] = []
        for (t_name, agent_name), trecs in tool_groups.items():
            calls = len(trecs)
            durs = [float(r["dur_ms"]) for r in trecs]
            masked = [r for r in trecs if r["masked_by_filler"]]
            unmasked_pre = [
                r
                for r in trecs
                if r["is_pre_speech"] and not r["masked_by_filler"]
            ]
            mask_rate = (len(masked) / calls * 100.0) if calls else 0.0
            unmasked_rate = (
                (len(unmasked_pre) / calls * 100.0) if calls else 0.0
            )
            avg_dur = sum(durs) / calls if calls else 0.0
            p90_dur = LatencyParser._percentile(durs, 90)

            masked_pls = [
                float(r["turn_pl_ms"])
                for r in masked
                if r["turn_pl_ms"] is not None
            ]
            unmasked_pls = [
                float(r["turn_pl_ms"])
                for r in unmasked_pre
                if r["turn_pl_ms"] is not None
            ]
            raw_unmasked_pls = [
                float(r["turn_unmasked_pl_ms"])
                for r in masked
                if r["turn_unmasked_pl_ms"] is not None
            ]

            pl_masked_avg = (
                sum(masked_pls) / len(masked_pls) if masked_pls else None
            )
            pl_unmasked_avg = (
                sum(unmasked_pls) / len(unmasked_pls)
                if unmasked_pls
                else (
                    sum(raw_unmasked_pls) / len(raw_unmasked_pls)
                    if raw_unmasked_pls
                    else None
                )
            )
            if pl_masked_avg is not None and pl_unmasked_avg is not None:
                net_saved = max(0.0, pl_unmasked_avg - pl_masked_avg)
            elif unmasked_pls:
                net_saved = avg_dur
            else:
                net_saved = 0.0

            if len(masked) > 0 and len(unmasked_pre) == 0:
                status = "0% Unmasked"
                status_cls = "pass"
                badge_label = f"0% ({len(masked)}/{calls} Masked)"
                observation = (
                    f"masked={len(masked)}/{calls} (100%) · "
                    f"tool_avg={round(avg_dur):,}ms · "
                    f"pl_delta=-{round(net_saved):,}ms"
                )
            elif len(masked) > 0 and len(unmasked_pre) > 0:
                status = f"{round(unmasked_rate)}% Unmasked"
                status_cls = "warn"
                badge_label = (
                    f"{round(unmasked_rate)}% "
                    f"({len(unmasked_pre)}/{calls} Unmasked)"
                )
                observation = (
                    f"unmasked={len(unmasked_pre)}/{calls} "
                    f"({round(unmasked_rate)}%), "
                    f"masked={len(masked)}/{calls} · "
                    f"tool_avg={round(avg_dur):,}ms (p90={round(p90_dur):,}ms)"
                )
            elif len(unmasked_pre) > 0:
                status = "100% Unmasked"
                status_cls = "fail" if avg_dur >= 300 else "warn"
                badge_label = f"100% ({len(unmasked_pre)}/{calls} Unmasked)"
                observation = (
                    f"unmasked={len(unmasked_pre)}/{calls} (100% pre-speech) · "
                    f"pre_speech_impact={round(len(unmasked_pre) * avg_dur):,}"
                    f"ms total ({round(avg_dur):,}ms/call)"
                )
            else:
                status = "Post-Speech / Silent Poll"
                status_cls = "neutral"
                badge_label = f"0% ({calls} Post-Speech)"
                observation = (
                    f"post_speech={calls}/{calls} (0ms pre-speech overlap) · "
                    f"tool_avg={round(avg_dur):,}ms"
                )

            all_filler_rows.append(
                {
                    "tool_name": t_name,
                    "agent": agent_name,
                    "calls": calls,
                    "masked_calls": len(masked),
                    "unmasked_calls": len(unmasked_pre),
                    "mask_rate_pct": round(mask_rate, 1),
                    "unmasked_rate_pct": round(unmasked_rate, 1),
                    "badge_label": badge_label,
                    "avg_ms": round(avg_dur, 1),
                    "p90_ms": round(p90_dur, 1),
                    "pl_masked_ms": (
                        round(pl_masked_avg, 1)
                        if pl_masked_avg is not None
                        else None
                    ),
                    "pl_unmasked_ms": (
                        round(pl_unmasked_avg, 1)
                        if pl_unmasked_avg is not None
                        else None
                    ),
                    "net_saved_ms": round(net_saved, 1),
                    "status": status,
                    "status_cls": status_cls,
                    "observation": observation,
                    "action": observation,
                }
            )

        # Sort descending by highest unmasked pre-speech impact first,
        # then total pre-speech impact, then total execution duration.
        all_filler_rows.sort(
            key=lambda x: (
                x["unmasked_calls"] * x["avg_ms"],
                (x["unmasked_calls"] + x["masked_calls"]) * x["avg_ms"],
                x["avg_ms"] * x["calls"],
            ),
            reverse=True,
        )

        # Filter out trivial (<100ms) or post-speech 0ms tools
        filler_table = [
            r
            for r in all_filler_rows
            if r["masked_calls"] > 0
            or (
                r["unmasked_calls"] > 0
                and (r["avg_ms"] >= 100.0 or r["p90_ms"] >= 150.0)
            )
            or r["avg_ms"] >= 250.0
        ]
        if not filler_table and all_filler_rows:
            filler_table = all_filler_rows[:5]
        else:
            filler_table = filler_table[:10]

        all_cb_recs = [
            rec
            for t in all_turns
            for rec in t.get("component_records", [])
            if rec.get("type") == "Callback"
        ]
        detailed_cb_recs = [
            r for r in all_cb_recs if r.get("has_detailed_latency")
        ]
        callback_decomposition: dict[str, Any] | None = None
        if detailed_cb_recs and all_cb_recs:
            cb_total_calls = len(all_cb_recs)
            cb_calls_per_turn = round(cb_total_calls / spoken_turn_count, 2)
            tot_cb_dur = sum(float(r.get("dur_ms", 0)) for r in all_cb_recs)
            tot_cb_code = sum(float(r.get("code_ms", 0)) for r in all_cb_recs)
            tot_cb_wait = sum(
                float(r.get("ext_wait_ms", 0)) for r in all_cb_recs
            )
            tot_cb_init = sum(
                float(r.get("sandbox_init_ms", 0)) for r in all_cb_recs
            )
            tot_cb_sandbox = max(0.0, tot_cb_dur - tot_cb_code - tot_cb_wait)
            denom_cb = max(1.0, tot_cb_dur)
            code_pct = round(tot_cb_code / denom_cb * 100.0, 1)
            ext_wait_pct = round(tot_cb_wait / denom_cb * 100.0, 1)
            sandbox_pct = round(max(0.0, 100.0 - code_pct - ext_wait_pct), 1)
            callback_decomposition = {
                "total_calls": cb_total_calls,
                "calls_per_turn": cb_calls_per_turn,
                "avg_total_per_turn_ms": round(
                    tot_cb_dur / spoken_turn_count, 1
                ),
                "avg_code_per_turn_ms": round(
                    tot_cb_code / spoken_turn_count, 1
                ),
                "code_pct": code_pct,
                "avg_ext_wait_per_turn_ms": round(
                    tot_cb_wait / spoken_turn_count, 1
                ),
                "ext_wait_pct": ext_wait_pct,
                "avg_sandbox_per_turn_ms": round(
                    tot_cb_sandbox / spoken_turn_count, 1
                ),
                "sandbox_pct": sandbox_pct,
                "avg_sandbox_per_call_ms": round(
                    tot_cb_sandbox / cb_total_calls, 1
                ),
                "avg_init_per_turn_ms": round(
                    tot_cb_init / spoken_turn_count, 1
                ),
            }

        return {
            "conversations_count": len(valid_convs),
            "total_turns": len(all_turns),
            "primary_slice_key": primary_key,
            "primary": primary,
            "slices": slices,
            "optimization_targets": optimization_targets,
            "callback_decomposition": callback_decomposition,
            "filler_table": filler_table,
        }
