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

"""Deterministic snapshot serialization and management for CES callback chain
tests.
"""

from __future__ import annotations

import base64
import contextlib
import contextvars
import copy
import datetime
import difflib
import hashlib
import inspect
import json
import math
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from cxas_scrapi.evals.callback_chain.hydration import compute_state_delta
from cxas_scrapi.evals.callback_chain.traces import (
    CallbackTrace,
    LlmStepTrace,
    ModeDiffReport,
    ToolStepTrace,
    TurnTrace,
)
from cxas_scrapi.evals.callback_chain.turn import SessionSimulator
from cxas_scrapi.utils.callback_libs import (
    Content,
    LlmRequest,
    LlmResponse,
)

_VOLATILE_TIMING_KEYS: frozenset[str] = frozenset(
    {
        "duration_ms",
        "llm_duration_ms",
        "tool_duration_ms",
        "callback_duration_ms",
        "total_duration_ms",
        "_extra_callback_duration_ms",
    }
)

_SCRUBBED_DATE_PLACEHOLDER = "<SCRUBBED_CURRENT_DATE>"
_SCRUBBED_THOUGHT_SIG_PLACEHOLDER = "<THOUGHT_SIGNATURE>"

_STATE_UPDATE_BLOCK_RE = re.compile(
    r"<state_update>\s*(.*?)\s*</state_update>",
    re.DOTALL,
)
_CURRENT_STATE_BLOCK_RE = re.compile(
    r"<current_state>\s*(.*?)\s*</current_state>",
    re.DOTALL,
)
_DOUBLE_BRACE_PLACEHOLDER_RE = re.compile(
    r"\{\{\s*(?:(?:app|user|temp):)?[a-zA-Z_][a-zA-Z0-9_.-]*\??\s*\}\}"
)
_SINGLE_BRACE_PLACEHOLDER_RE = re.compile(
    r"(?<!\{)\{(?:(?:app|user|temp):)?[a-zA-Z_][a-zA-Z0-9_.-]*\??\}(?!\})"
)

# Active pytest context populated by pytest_plugin.py during test execution
_ACTIVE_SNAPSHOT_CONTEXT: contextvars.ContextVar[dict[str, Any] | None] = (
    contextvars.ContextVar("_ACTIVE_SNAPSHOT_CONTEXT", default=None)
)

# Per-test counter for unnamed snapshots so multiple to_match_snapshot() calls
# in a single test function get distinct keys (test_name, test_name::2, ...)
_UNNAMED_SNAPSHOT_COUNTS: dict[tuple[str, str], int] = {}


def _reset_unnamed_snapshot_counts() -> None:
    """Resets the unnamed snapshot call counter (used at test setup)."""
    _UNNAMED_SNAPSHOT_COUNTS.clear()


def _canonicalize_value(val: Any, *, key_name: str | None = None) -> Any:
    """Recursively scrubs volatile fields and normalizes arbitrary Python
    objects for JSON.
    """
    if key_name == "current_date":
        return _SCRUBBED_DATE_PLACEHOLDER
    if key_name == "thought_signature" and val is not None:
        return _SCRUBBED_THOUGHT_SIG_PLACEHOLDER

    if val is None or isinstance(val, (bool, str, int)):
        return val

    if isinstance(val, float):
        if math.isnan(val):
            return "<NaN>"
        if math.isinf(val):
            return "<Infinity>" if val > 0 else "<-Infinity>"
        return val

    if isinstance(val, bytes):
        return f"<bytes:base64:{base64.b64encode(val).decode('ascii')}>"

    if isinstance(val, (datetime.datetime, datetime.date, datetime.time)):
        return val.isoformat()

    if isinstance(val, Path):
        return str(val)

    if hasattr(val, "model_dump") and callable(val.model_dump):
        try:
            dumped = val.model_dump(mode="python", exclude_none=True)
            return _canonicalize_value(dumped)
        except Exception:
            pass

    if isinstance(val, Mapping):
        out: dict[str, Any] = {}
        for k in sorted(val.keys(), key=lambda x: str(x)):
            k_str = str(k)
            if k_str in _VOLATILE_TIMING_KEYS:
                continue
            out[k_str] = _canonicalize_value(val[k], key_name=k_str)
        return out

    if isinstance(val, (set, frozenset)):
        canon_items = [_canonicalize_value(item) for item in val]
        return sorted(
            canon_items,
            key=lambda x: json.dumps(x, sort_keys=True, default=repr),
        )

    if isinstance(val, Sequence) and not isinstance(val, (str, bytes)):
        return [_canonicalize_value(item) for item in val]

    return repr(val)


def _extract_system_instruction_text(req: LlmRequest | None) -> str:
    """Extracts the plain text of `req.config.system_instruction`."""
    if req is None or req.config is None:
        return ""
    sys_inst = req.config.system_instruction
    if sys_inst is None:
        return ""
    if isinstance(sys_inst, str):
        return sys_inst
    if isinstance(sys_inst, Content) and sys_inst.parts:
        texts = [p.text for p in sys_inst.parts if p.text is not None]
        return "\n".join(texts)
    return str(sys_inst)


def _find_unresolved_placeholders(text: str) -> list[str]:
    """Finds unresolved `{var}` and `{{var}}` placeholders in instruction/prompt
    text.
    """
    if not text:
        return []
    # Strip <state_update> and <current_state> blocks before scanning for
    # placeholders
    cleaned = _STATE_UPDATE_BLOCK_RE.sub("", text)
    cleaned = _CURRENT_STATE_BLOCK_RE.sub("", cleaned)
    found: set[str] = set()
    for m in _DOUBLE_BRACE_PLACEHOLDER_RE.finditer(cleaned):
        found.add(m.group(0))
    for m in _SINGLE_BRACE_PLACEHOLDER_RE.finditer(cleaned):
        found.add(m.group(0))
    return sorted(found)


def _parse_state_update_inner(raw_inner: str, merged: dict[str, Any]) -> None:
    """Parses JSON or `key: value` lines inside a `<state_update>` /
    `<current_state>` block.
    """
    raw_stripped = raw_inner.strip()
    if not raw_stripped:
        return
    try:
        parsed = json.loads(raw_stripped)
        if isinstance(parsed, dict):
            for k, v in parsed.items():
                merged[str(k)] = _canonicalize_value(v, key_name=str(k))
            return
    except Exception:
        pass

    parsed_any_kv = False
    for line in raw_stripped.splitlines():
        s_line = line.strip()
        if not s_line or ":" not in s_line:
            continue
        k_part, v_part = s_line.split(":", 1)
        k_str = k_part.strip()
        if not k_str or " " in k_str:
            continue
        v_str = v_part.strip()
        try:
            v_val = json.loads(v_str)
        except Exception:
            v_val = v_str
        merged[k_str] = _canonicalize_value(v_val, key_name=k_str)
        parsed_any_kv = True

    if not parsed_any_kv:
        merged["_raw_text"] = raw_stripped


def _extract_injected_state_updates(
    req: LlmRequest | None,
    *,
    source: str = "state_update",
) -> dict[str, Any]:
    """Extracts `<state_update>` and/or `<current_state>` payloads from `req`.

    Args:
        req: The `LlmRequest` to inspect.
        source: `"state_update"` (default, extracts only `<state_update>`
        deltas),
            `"current_state"` (extracts only `<current_state>` blocks), or
            `"any"` (merges `<current_state>` followed by `<state_update>` so
            `<state_update>` takes precedence).
    """
    if req is None:
        return {}
    if source not in ("state_update", "current_state", "any"):
        raise ValueError(
            "source must be 'state_update', 'current_state', or 'any'; got "
            f"{source!r}."
        )
    merged: dict[str, Any] = {}
    text_blobs: list[str] = []
    if req.config is not None and req.config.system_instruction is not None:
        sys_inst = req.config.system_instruction
        if isinstance(sys_inst, str):
            text_blobs.append(sys_inst)
        elif isinstance(sys_inst, Content) and sys_inst.parts:
            text_blobs.append(
                "\n".join(p.text for p in sys_inst.parts if p.text)
            )
    for content in req.contents or []:
        if content.parts:
            joined = "\n".join(p.text for p in content.parts if p.text)
            if joined:
                text_blobs.append(joined)
    if source in ("current_state", "any"):
        for blob in text_blobs:
            for match in _CURRENT_STATE_BLOCK_RE.finditer(blob):
                _parse_state_update_inner(match.group(1), merged)
    if source in ("state_update", "any"):
        for blob in text_blobs:
            for match in _STATE_UPDATE_BLOCK_RE.finditer(blob):
                _parse_state_update_inner(match.group(1), merged)
    return _canonicalize_value(merged)


def _extract_active_tools(req: LlmRequest | None) -> list[str]:
    """Extracts sorted tool declaration names from `req.config.tools`."""
    if req is None or req.config is None or not req.config.tools:
        return []
    names: list[str] = []
    for tool_decl in req.config.tools:
        for fn_decl in getattr(tool_decl, "function_declarations", None) or []:
            fn_name = getattr(fn_decl, "name", None)
            if fn_name and fn_name not in names:
                names.append(str(fn_name))
    return sorted(names)


def _extract_excluded_tools(req: LlmRequest | None) -> list[str]:
    """Extracts sorted excluded tool names from `req.config.excluded_tools`."""
    if req is None:
        return []
    names: set[str] = set()
    if req.config is not None and req.config.excluded_tools:
        for t in req.config.excluded_tools:
            if t:
                names.add(str(t))
    for t in getattr(req, "_excluded_tools_applied", None) or []:
        if t:
            names.add(str(t))
    return sorted(names)


def _serialize_response_summary(
    resp: LlmResponse | None,
) -> dict[str, Any] | None:
    """Serializes an `LlmResponse` into a deterministic summary dict."""
    if resp is None:
        return None
    texts: list[str] = []
    thoughts: list[str] = []
    function_calls: list[dict[str, Any]] = []
    has_thought_signature = False

    if resp.content is not None and resp.content.parts:
        for part in resp.content.parts:
            if getattr(part, "thought_signature", None) is not None:
                has_thought_signature = True
            if getattr(part, "thought", False):
                if part.text is not None:
                    thoughts.append(part.text)
            elif part.text is not None:
                texts.append(part.text)
            if part.function_call is not None:
                fc = part.function_call
                function_calls.append(
                    {
                        "name": fc.name or "",
                        "args": _canonicalize_value(dict(fc.args or {})),
                    }
                )

    summary: dict[str, Any] = {
        "text": "\n".join(texts) if texts else "",
        "function_calls": function_calls,
    }
    if thoughts:
        summary["thoughts"] = "\n".join(thoughts)
    if has_thought_signature:
        summary["thought_signature"] = _SCRUBBED_THOUGHT_SIG_PLACEHOLDER
    if resp.finish_reason:
        summary["finish_reason"] = resp.finish_reason
    if resp.block_reason:
        summary["block_reason"] = resp.block_reason
    return summary


def serialize_stage_snapshot(
    trace: CallbackTrace | tuple[Any, CallbackTrace] | ModeDiffReport,
) -> dict[str, Any]:
    """Serializes a `CallbackTrace` or `ModeDiffReport` into a deterministic
    snapshot dict.
    """
    if (
        isinstance(trace, tuple)
        and len(trace) == 2
        and isinstance(trace[1], CallbackTrace)
    ):
        trace = trace[1]

    if isinstance(trace, ModeDiffReport):
        return {
            "type": "mode_diff_report",
            "stage": trace.stage,
            "image_version": trace.image_version,
            "has_diff": trace.has_diff,
            "has_state_diff": trace.has_state_diff,
            "has_response_diff": trace.has_response_diff,
            "has_execution_order_diff": trace.has_execution_order_diff,
            "has_error_diff": trace.has_error_diff,
            "state_diffs": _canonicalize_value(trace.state_diffs),
            "response_diff": _canonicalize_value(trace.response_diff),
            "per_callback": serialize_stage_snapshot(trace.per_callback_trace),
            "combined": serialize_stage_snapshot(trace.combined_trace),
        }

    if not isinstance(trace, CallbackTrace):
        raise TypeError(
            "serialize_stage_snapshot expected CallbackTrace or "
            "ModeDiffReport, "
            f"got {type(trace).__name__}"
        )

    state_delta = compute_state_delta(trace.initial_state, trace.final_state)
    steps_summary = []
    prev_state = copy.deepcopy(trace.initial_state)
    for step in trace.steps:
        step_delta = compute_state_delta(prev_state, step.state_after)
        prev_state = copy.deepcopy(step.state_after)
        steps_summary.append(
            {
                "name": step.name,
                "status": step.status,
                "is_global": step.is_global,
                "returned_override": step.returned_override,
                "state_delta": _canonicalize_value(step_delta),
                "error": step.error.splitlines()[0] if step.error else None,
                "warnings": list(step.warnings),
            }
        )

    out: dict[str, Any] = {
        "stage": trace.stage,
        "mode": trace.mode,
        "image_version": trace.image_version,
        "executed_callbacks": list(trace.executed_callbacks),
        "disabled_callbacks": list(trace.disabled_callbacks),
        "skipped_callbacks": list(trace.skipped_callbacks),
        "short_circuited": trace.short_circuited,
        "short_circuited_by": trace.short_circuited_by,
        "failed_callback": trace.failed_callback,
        "culprit_callback": trace.culprit_callback,
        "error_phase": trace.error_phase,
        "error_outcome": trace.error_outcome,
        "error": trace.error.splitlines()[0] if trace.error else None,
        "state_delta": _canonicalize_value(state_delta),
        "final_state": _canonicalize_value(trace.final_state),
        "override_response": _canonicalize_value(trace.override_response),
        "final_tool_input": _canonicalize_value(trace.final_tool_input),
        "tool_calls": _canonicalize_value(trace.tool_calls),
        "request_mutations": list(trace.request_mutations),
        "response_mutations": list(trace.response_mutations),
        "warnings": list(trace.warnings),
        "steps": steps_summary,
    }
    return out


def _serialize_llm_step(
    ls: LlmStepTrace,
    *,
    include_prompt: bool = True,
    include_full_prompt: bool = False,
) -> dict[str, Any]:
    """Serializes a single `LlmStepTrace` using the hybrid prompt hash +
    state_update strategy.
    """
    sys_text = _extract_system_instruction_text(ls.request)
    sys_hash = (
        hashlib.sha256(sys_text.encode("utf-8")).hexdigest()[:12]
        if sys_text
        else ""
    )
    unresolved = _find_unresolved_placeholders(sys_text)
    for content in (ls.request.contents or []) if ls.request else []:
        for part in content.parts or []:
            if part.text:
                for ph in _find_unresolved_placeholders(part.text):
                    if ph not in unresolved:
                        unresolved.append(ph)
    unresolved.sort()

    step_dict: dict[str, Any] = {
        "hop": ls.step_index + 1,
        "llm_invoked": ls.llm_invoked,
        "short_circuited": ls.short_circuited,
        "after_model_skipped": ls.after_model_skipped,
        "before_model_callbacks": list(
            ls.before_model_trace.executed_callbacks
        ),
        "after_model_callbacks": (
            list(ls.after_model_trace.executed_callbacks)
            if ls.after_model_trace is not None
            else []
        ),
        "active_tools": _extract_active_tools(ls.request),
        "excluded_tools": _extract_excluded_tools(ls.request),
        "request_mutations": list(ls.before_model_trace.request_mutations),
        "response_mutations": (
            list(ls.after_model_trace.response_mutations)
            if ls.after_model_trace is not None
            else []
        ),
        "raw_response": _serialize_response_summary(ls.raw_response),
        "final_response": _serialize_response_summary(ls.final_response),
    }

    if include_prompt:
        step_dict["system_instruction_sha256"] = sys_hash
        step_dict["unresolved_placeholders"] = unresolved
        step_dict["injected_state_update"] = _extract_injected_state_updates(
            ls.request, source="state_update"
        )
        curr_state_payload = _extract_injected_state_updates(
            ls.request, source="current_state"
        )
        if curr_state_payload:
            step_dict["injected_current_state"] = curr_state_payload
        if include_full_prompt:
            step_dict["system_instruction"] = sys_text

    return step_dict


def _serialize_tool_step(ts: ToolStepTrace, index: int) -> dict[str, Any]:
    """Serializes a single `ToolStepTrace` with normalized deterministic call
    ID.
    """
    return {
        "call_index": index,
        "tool_name": ts.tool_name,
        "function_call_id": f"call_{index}",
        "initial_tool_input": _canonicalize_value(ts.initial_tool_input),
        "final_tool_input": _canonicalize_value(ts.final_tool_input),
        "short_circuited_by_before_tool": ts.short_circuited_by_before_tool,
        "tool_executed": ts.tool_executed,
        "missing_impl": ts.missing_impl,
        "raw_tool_response": _canonicalize_value(ts.raw_tool_response),
        "final_tool_response": _canonicalize_value(ts.final_tool_response),
        "tool_error": (
            ts.tool_error.splitlines()[0] if ts.tool_error else None
        ),
        "before_tool_callbacks": list(ts.before_tool_trace.executed_callbacks),
        "after_tool_callbacks": (
            list(ts.after_tool_trace.executed_callbacks)
            if ts.after_tool_trace is not None
            else []
        ),
    }


def serialize_turn_snapshot(
    turn: TurnTrace | tuple[LlmResponse | None, TurnTrace],
    *,
    include_prompt: bool = True,
    include_full_prompt: bool = False,
) -> dict[str, Any]:
    """Serializes a `TurnTrace` into a deterministic, human-readable snapshot
    dictionary.

    Scrubs volatile timing fields (`duration_ms`, `llm_duration_ms`,
    `tool_duration_ms`,
    `callback_duration_ms`), replaces `current_date` with
    `<SCRUBBED_CURRENT_DATE>`,
    and replaces binary `thought_signature` values with `<THOUGHT_SIGNATURE>`.
    """
    if (
        isinstance(turn, tuple)
        and len(turn) == 2
        and isinstance(turn[1], TurnTrace)
    ):
        turn = turn[1]

    if not isinstance(turn, TurnTrace):
        raise TypeError(
            "serialize_turn_snapshot expected TurnTrace or (LlmResponse, "
            "TurnTrace), "
            f"got {type(turn).__name__}"
        )

    stages_summary: dict[str, Any] = {}
    if turn.before_agent_trace is not None:
        stages_summary["before_agent"] = {
            "executed_callbacks": list(
                turn.before_agent_trace.executed_callbacks
            ),
            "short_circuited_by": turn.before_agent_trace.short_circuited_by,
            "state_delta": _canonicalize_value(
                compute_state_delta(
                    turn.before_agent_trace.initial_state,
                    turn.before_agent_trace.final_state,
                )
            ),
            "error": (
                turn.before_agent_trace.error.splitlines()[0]
                if turn.before_agent_trace.error
                else None
            ),
        }
    if turn.before_model_trace is not None:
        stages_summary["before_model"] = {
            "executed_callbacks": list(
                turn.before_model_trace.executed_callbacks
            ),
            "short_circuited_by": turn.before_model_trace.short_circuited_by,
            "state_delta": _canonicalize_value(
                compute_state_delta(
                    turn.before_model_trace.initial_state,
                    turn.before_model_trace.final_state,
                )
            ),
            "error": (
                turn.before_model_trace.error.splitlines()[0]
                if turn.before_model_trace.error
                else None
            ),
        }
    if turn.after_model_trace is not None:
        stages_summary["after_model"] = {
            "executed_callbacks": list(
                turn.after_model_trace.executed_callbacks
            ),
            "short_circuited_by": turn.after_model_trace.short_circuited_by,
            "state_delta": _canonicalize_value(
                compute_state_delta(
                    turn.after_model_trace.initial_state,
                    turn.after_model_trace.final_state,
                )
            ),
            "error": (
                turn.after_model_trace.error.splitlines()[0]
                if turn.after_model_trace.error
                else None
            ),
        }
    if turn.after_agent_trace is not None:
        stages_summary["after_agent"] = {
            "executed_callbacks": list(
                turn.after_agent_trace.executed_callbacks
            ),
            "short_circuited_by": turn.after_agent_trace.short_circuited_by,
            "state_delta": _canonicalize_value(
                compute_state_delta(
                    turn.after_agent_trace.initial_state,
                    turn.after_agent_trace.final_state,
                )
            ),
            "error": (
                turn.after_agent_trace.error.splitlines()[0]
                if turn.after_agent_trace.error
                else None
            ),
        }

    serialized_llm_steps = [
        _serialize_llm_step(
            ls,
            include_prompt=include_prompt,
            include_full_prompt=include_full_prompt,
        )
        for ls in turn.llm_steps
    ]
    serialized_tool_steps = [
        _serialize_tool_step(ts, idx)
        for idx, ts in enumerate(turn.tool_steps, 1)
    ]

    snapshot: dict[str, Any] = {
        "agent_name": turn.agent_name,
        "mode": turn.mode,
        "image_version": turn.image_version,
        "user_input": turn.user_input,
        "routed_agents": list(turn.routed_agents),
        "executed_callbacks": list(turn.executed_callbacks),
        "short_circuited_by": turn.short_circuited_by,
        "llm_invoked": turn.llm_invoked,
        "after_model_skipped": turn.after_model_skipped,
        "stages": stages_summary,
        "llm_steps": serialized_llm_steps,
        "tool_steps": serialized_tool_steps,
        "final_response": _serialize_response_summary(turn.final_response),
        "final_state": _canonicalize_value(turn.final_state),
        "transfer_to": turn.transfer_to,
        "end_session": _canonicalize_value(turn.end_session),
        "error": turn.error.splitlines()[0] if turn.error else None,
        "failed_stage": turn.failed_stage,
        "failed_tool": turn.failed_tool,
        "warnings": list(turn.warnings),
    }
    return snapshot


def serialize_session_snapshot(
    session: SessionSimulator | Sequence[TurnTrace],
    *,
    include_prompt: bool = True,
    include_full_prompt: bool = False,
) -> dict[str, Any]:
    """Serializes a multi-turn `SessionSimulator` or sequence of `TurnTrace`s
    into a snapshot dict.
    """
    if isinstance(session, SessionSimulator):
        turns = list(session.turns)
        agent_history = list(session.agent_history)
        final_state = _canonicalize_value(session.state)
        warnings = list(session.warnings)
    elif isinstance(session, Sequence) and not isinstance(
        session, (str, bytes)
    ):
        turns = []
        for item in session:
            if (
                isinstance(item, tuple)
                and len(item) == 2
                and isinstance(item[1], TurnTrace)
            ):
                turns.append(item[1])
            elif isinstance(item, TurnTrace):
                turns.append(item)
            else:
                raise TypeError(
                    "serialize_session_snapshot sequence items must be "
                    "TurnTrace, "
                    f"got {type(item).__name__}"
                )
        agent_history = []
        for t in turns:
            for ag in t.routed_agents:
                if not agent_history or agent_history[-1] != ag:
                    agent_history.append(ag)
        final_state = (
            _canonicalize_value(turns[-1].final_state) if turns else {}
        )
        warnings = []
    else:
        raise TypeError(
            "serialize_session_snapshot expected SessionSimulator or "
            "Sequence[TurnTrace], "
            f"got {type(session).__name__}"
        )

    return {
        "turn_count": len(turns),
        "agent_history": agent_history,
        "turns": [
            serialize_turn_snapshot(
                t,
                include_prompt=include_prompt,
                include_full_prompt=include_full_prompt,
            )
            for t in turns
        ],
        "final_state": final_state,
        "warnings": warnings,
    }


def _detect_caller_test_context() -> tuple[Path, str]:
    """Walks the call stack to find the calling test file and class/function
    scope.
    """
    active_ctx = _ACTIVE_SNAPSHOT_CONTEXT.get()
    if active_ctx is not None:
        f_path = active_ctx.get("test_file")
        t_name = active_ctx.get("test_name")
        if f_path and t_name:
            return Path(f_path), str(t_name)

    this_dir = Path(__file__).resolve().parent
    orig_env_file = os.environ.get("CXAS_ORIGINAL_TEST_FILE")
    override_file_path = (
        Path(orig_env_file).resolve() if orig_env_file else None
    )
    fallback_file: Path | None = None
    fallback_func: str = "snapshot"

    for frame_info in inspect.stack()[1:]:
        filename = frame_info.filename
        if not filename or filename.startswith("<"):
            continue
        p = Path(filename).resolve()
        if p.parent == this_dir:
            continue
        func_name = frame_info.function
        self_obj = frame_info.frame.f_locals.get("self")
        if self_obj is not None:
            cls_name = type(self_obj).__name__
            if cls_name.startswith("Test"):
                func_name = f"{cls_name}::{func_name}"
        if fallback_file is None:
            fallback_file = override_file_path or p
            fallback_func = func_name
        if (
            p.name.startswith("test_")
            or p.name.endswith("_test.py")
            or frame_info.function.startswith("test_")
        ):
            return (override_file_path or p), func_name

    if fallback_file is not None:
        return fallback_file, fallback_func
    return (
        override_file_path or (Path.cwd() / "test_snapshots.py"),
        fallback_func,
    )


def _compute_structured_json_diff(
    expected: Any,
    actual: Any,
    path: str = "$",
) -> list[str]:
    """Computes a structured `+Added / ~Modified / -Removed` diff across JSON
    values.
    """
    lines: list[str] = []
    if type(expected) is not type(actual):
        lines.append(f"~Modified {path}: {expected!r} -> {actual!r}")
        return lines
    if isinstance(expected, dict):
        all_keys = sorted(set(expected.keys()) | set(actual.keys()))
        for k in all_keys:
            sub_path = f"{path}.{k}"
            if k not in expected:
                lines.append(f"+Added    {sub_path}: {actual[k]!r}")
            elif k not in actual:
                lines.append(f"-Removed  {sub_path}: {expected[k]!r}")
            else:
                lines.extend(
                    _compute_structured_json_diff(
                        expected[k], actual[k], sub_path
                    )
                )
        return lines
    if isinstance(expected, list):
        min_len = min(len(expected), len(actual))
        for idx in range(min_len):
            lines.extend(
                _compute_structured_json_diff(
                    expected[idx], actual[idx], f"{path}[{idx}]"
                )
            )
        for idx in range(min_len, len(actual)):
            lines.append(f"+Added    {path}[{idx}]: {actual[idx]!r}")
        for idx in range(min_len, len(expected)):
            lines.append(f"-Removed  {path}[{idx}]: {expected[idx]!r}")
        return lines
    if expected != actual:
        lines.append(f"~Modified {path}: {expected!r} -> {actual!r}")
    return lines


def _truncate_snapshot_preview(payload: Any, max_chars: int = 400) -> Any:
    """Truncates large snapshot payloads for concise `Expected`/`Actual`
    summaries.
    """
    try:
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except Exception:
        rendered = repr(payload)
    if len(rendered) <= max_chars:
        return payload
    return f"{rendered[:max_chars]}... (truncated, {len(rendered)} chars total)"


class SnapshotManager:
    """Manages reading, comparing, and updating `.snap.json` files for CES agent
    tests.

    Note on concurrency (`pytest-xdist`):
    Snapshot file updates use atomic temporary-file replacement (`os.replace`),
    which prevents partial/corrupt JSON writes. However, because all tests in a
    single test module share `<module>.snap.json`, running
    `--cxas-update-snapshots`
    across parallel `pytest-xdist` workers that split tests from the same module
    can still race on read-modify-write; use `--dist=loadfile` (or
    single-worker)
    when updating snapshots.
    """

    def __init__(
        self,
        snapshot_dir: str | Path | None = None,
        *,
        update_snapshots: bool | None = None,
        ci_mode: bool | None = None,
    ) -> None:
        self.snapshot_dir = (
            Path(snapshot_dir) if snapshot_dir is not None else None
        )
        active_ctx = _ACTIVE_SNAPSHOT_CONTEXT.get()
        if update_snapshots is not None:
            self.update_snapshots = bool(update_snapshots)
        else:
            ctx_update = (
                bool(active_ctx.get("update_snapshots", False))
                if active_ctx is not None
                else False
            )
            env_update = os.environ.get(
                "CXAS_UPDATE_SNAPSHOTS", ""
            ).strip().lower() in ("1", "true", "yes")
            self.update_snapshots = ctx_update or env_update

        if ci_mode is not None:
            self.ci_mode = bool(ci_mode)
        else:
            ctx_ci = (
                bool(active_ctx.get("ci_mode", False))
                if active_ctx is not None
                else False
            )
            env_ci = os.environ.get("CXAS_SNAPSHOT_CI", "").strip().lower() in (
                "1",
                "true",
                "yes",
            )
            self.ci_mode = ctx_ci or env_ci

    def resolve_snapshot_path(
        self,
        test_file: str | Path | None = None,
    ) -> tuple[Path, str]:
        """Resolves the `.snap.json` file path and default snapshot key
        prefix.
        """
        detected_file, detected_func = _detect_caller_test_context()
        eff_test_file = (
            Path(test_file).resolve()
            if test_file is not None
            else detected_file
        )
        module_stem = eff_test_file.stem

        if self.snapshot_dir is not None:
            if self.snapshot_dir.suffix == ".json":
                snap_file = self.snapshot_dir
            else:
                snap_file = self.snapshot_dir / f"{module_stem}.snap.json"
        else:
            active_ctx = _ACTIVE_SNAPSHOT_CONTEXT.get()
            ctx_snap_dir = (
                active_ctx.get("snapshot_dir")
                if active_ctx is not None
                else None
            )
            if ctx_snap_dir is not None:
                snap_file = Path(ctx_snap_dir) / f"{module_stem}.snap.json"
            else:
                snap_file = (
                    eff_test_file.parent
                    / "__snapshots__"
                    / f"{module_stem}.snap.json"
                )

        return snap_file, detected_func

    def _resolve_key(
        self,
        name: str | None,
        snap_file: Path,
        default_func: str,
        *,
        increment: bool = True,
    ) -> str:
        if name:
            if "::" in name:
                return name
            return f"{default_func}::{name}"
        active_ctx = _ACTIVE_SNAPSHOT_CONTEXT.get()
        counts_map = (
            active_ctx.setdefault("unnamed_counts", {})
            if isinstance(active_ctx, dict)
            else _UNNAMED_SNAPSHOT_COUNTS
        )
        counter_key = (str(snap_file), default_func)
        count = counts_map.get(counter_key, 0) + 1
        if increment:
            counts_map[counter_key] = count
        if count == 1:
            return default_func
        return f"{default_func}::{count}"

    def check_matches_readonly(
        self,
        actual_payload: dict[str, Any],
        name: str | None = None,
        *,
        test_file: str | Path | None = None,
    ) -> tuple[bool, bool, str, Path]:
        """Checks whether `actual_payload` matches an existing snapshot without
        writing to disk.

        Returns `(exists_on_disk, matches_expected, snap_key, snap_file)`.
        """
        snap_file, default_func = self.resolve_snapshot_path(
            test_file=test_file
        )
        snap_key = self._resolve_key(
            name, snap_file, default_func, increment=False
        )

        if not snap_file.is_file():
            return False, False, snap_key, snap_file

        try:
            loaded = json.loads(snap_file.read_text(encoding="utf-8"))
        except Exception:
            return False, False, snap_key, snap_file

        if not isinstance(loaded, dict):
            return False, False, snap_key, snap_file

        if snap_key not in loaded and name and name in loaded:
            snap_key = name

        if snap_key not in loaded:
            return False, False, snap_key, snap_file

        canonical_actual = json.loads(
            json.dumps(
                _canonicalize_value(actual_payload),
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
        )
        return True, (loaded[snap_key] == canonical_actual), snap_key, snap_file

    def assert_matches(
        self,
        actual_payload: dict[str, Any],
        name: str | None = None,
        *,
        test_file: str | Path | None = None,
        update_snapshots: bool | None = None,
    ) -> dict[str, Any]:
        """Compares `actual_payload` against the stored snapshot or
        writes/updates it.

        Raises `AgentAssertionError` with a structured `+Added / ~Modified /
        -Removed`
        diff and unified diff if the snapshot drifts and `update_snapshots` is
        False.
        """
        from cxas_scrapi.evals.callback_chain.matchers import (  # noqa: PLC0415
            AgentAssertionError,
        )

        snap_file, default_func = self.resolve_snapshot_path(
            test_file=test_file
        )
        snap_key = self._resolve_key(name, snap_file, default_func)
        eff_update = (
            self.update_snapshots
            if update_snapshots is None
            else bool(update_snapshots)
        )

        canonical_actual = json.loads(
            json.dumps(
                _canonicalize_value(actual_payload),
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
        )

        all_snapshots: dict[str, Any] = {}
        if snap_file.is_file():
            try:
                loaded = json.loads(snap_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    all_snapshots = loaded
            except Exception as exc:
                raise AgentAssertionError(
                    f"Failed to parse existing snapshot file '{snap_file}': "
                    f"{exc}"
                ) from exc

        if snap_key not in all_snapshots and name and name in all_snapshots:
            snap_key = name

        if snap_key not in all_snapshots and not eff_update and self.ci_mode:
            raise AgentAssertionError(
                f"Snapshot '{snap_key}' is missing in '{snap_file}' "
                "(CI mode enabled; run pytest with `--cxas-update-snapshots` "
                "to record it)."
            )

        if snap_key not in all_snapshots or eff_update:
            all_snapshots[snap_key] = canonical_actual
            snap_file.parent.mkdir(parents=True, exist_ok=True)
            serialized = (
                json.dumps(
                    all_snapshots,
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=False,
                )
                + "\n"
            )
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=str(snap_file.parent),
                prefix=f".{snap_file.name}.",
                suffix=".tmp",
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp_file:
                    tmp_file.write(serialized)
                os.replace(tmp_path, snap_file)
            except Exception:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
                raise
            return canonical_actual

        expected_payload = all_snapshots[snap_key]
        if expected_payload != canonical_actual:
            structured_lines = _compute_structured_json_diff(
                expected_payload, canonical_actual
            )
            structured_block = "\n  ".join(structured_lines[:40])
            if len(structured_lines) > 40:
                structured_block += (
                    f"\n  ... ({len(structured_lines) - 40} more diff lines)"
                )

            expected_lines = json.dumps(
                expected_payload,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            ).splitlines(keepends=True)
            actual_lines = json.dumps(
                canonical_actual,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            ).splitlines(keepends=True)
            unified_lines = list(
                difflib.unified_diff(
                    expected_lines,
                    actual_lines,
                    fromfile=f"snapshot ({snap_key})",
                    tofile=f"actual ({snap_key})",
                )
            )
            diff_text = "".join(unified_lines[:80])
            if len(unified_lines) > 80:
                diff_text += (
                    f"... ({len(unified_lines) - 80} more unified diff lines "
                    f"truncated)\n"
                )
            raise AgentAssertionError(
                f"Snapshot '{snap_key}' in '{snap_file}' did not match actual "
                "trace output.\n\n"
                "--- Snapshot Drift (+Added / ~Modified / -Removed) ---\n  "
                f"{structured_block}\n\n"
                f"--- Unified Diff ---\n{diff_text}\n"
                "To update snapshots, run pytest with "
                "`--cxas-update-snapshots` "
                f"(or set `CXAS_UPDATE_SNAPSHOTS=1`).",
                expected=_truncate_snapshot_preview(expected_payload),
                actual=_truncate_snapshot_preview(canonical_actual),
            )

        return canonical_actual

    def match_turn(
        self,
        turn: TurnTrace | tuple[LlmResponse | None, TurnTrace],
        name: str | None = None,
        *,
        include_prompt: bool = True,
        include_full_prompt: bool = False,
        update_snapshots: bool | None = None,
    ) -> dict[str, Any]:
        """Serializes `turn` and asserts that it matches the named snapshot."""
        payload = serialize_turn_snapshot(
            turn,
            include_prompt=include_prompt,
            include_full_prompt=include_full_prompt,
        )
        return self.assert_matches(
            payload, name=name, update_snapshots=update_snapshots
        )

    def match_stage(
        self,
        trace: CallbackTrace | tuple[Any, CallbackTrace] | ModeDiffReport,
        name: str | None = None,
        *,
        update_snapshots: bool | None = None,
    ) -> dict[str, Any]:
        """Serializes `trace` and asserts that it matches the named snapshot."""
        payload = serialize_stage_snapshot(trace)
        return self.assert_matches(
            payload, name=name, update_snapshots=update_snapshots
        )

    def match_session(
        self,
        session: SessionSimulator | Sequence[TurnTrace],
        name: str | None = None,
        *,
        include_prompt: bool = True,
        include_full_prompt: bool = False,
        update_snapshots: bool | None = None,
    ) -> dict[str, Any]:
        """Serializes `session` and asserts that it matches the named
        snapshot.
        """
        payload = serialize_session_snapshot(
            session,
            include_prompt=include_prompt,
            include_full_prompt=include_full_prompt,
        )
        return self.assert_matches(
            payload, name=name, update_snapshots=update_snapshots
        )
