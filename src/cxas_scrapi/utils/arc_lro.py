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

"""Detection of client-managed long-running operations in assistant output.

When the client declares the ``CLIENT_MANAGED_LRO`` capability, tools such as
``run_evaluation`` return a ``{"status": "PENDING", "operation": ...}`` payload
and the assistant detaches the turn. The client polls the operation and sends
an ``operationCompletedEvent`` to resume. These helpers find such payloads in
tool responses and in JSON fragments embedded in text.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import re
from typing import Any

__all__ = [
    "DETACHED_JOB_PREFIX",
    "LroOperationInfo",
    "extract_from_dict",
    "extract_from_text",
    "extract_from_tool_response",
]


@dataclasses.dataclass
class LroOperationInfo:
    """A long-running operation initiated by the assistant during a turn."""

    operation_name: str
    target_resource_name: str
    operation_type: str = "run_evaluation"
    status: str = "PENDING"
    metadata: dict[str, Any] | None = None


# Server-side wrapper tools whose payloads carry the real operation type.
_WRAPPER_TOOLS = ("run_kestrel_python", "call_kestrel_tool")
DETACHED_JOB_PREFIX = "guddelon-"
_DETACHED_JOB_PREFIX = DETACHED_JOB_PREFIX
_JSON_OBJECT_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", re.DOTALL)


def _is_pending(d: Any) -> bool:
    return isinstance(d, dict) and (
        d.get("status") == "PENDING"
        or d.get("lro_status") == "PENDING"
        or (
            d.get("status") == "RUNNING"
            and str(d.get("job_id") or "").startswith(DETACHED_JOB_PREFIX)
        )
    )


def extract_from_dict(
    data: dict[str, Any], default_tool_name: str = ""
) -> LroOperationInfo | None:
    """Extracts pending LRO metadata from a tool response or JSON dict."""
    if not isinstance(data, dict):
        return None

    candidates: list[dict[str, Any]] = []

    def _add(d: Any) -> bool:
        if not isinstance(d, dict):
            return False
        added = False
        if _is_pending(d):
            candidates.append(d)
            added = True
        nested = d.get("pending_lro")
        if isinstance(nested, dict) and nested.get("status") == "PENDING":
            candidates.append(nested)
            added = True
        return added

    _add(data)

    output_val = data.get("output")
    if isinstance(output_val, str):
        try:
            parsed = json.loads(output_val)
            output_val = parsed if isinstance(parsed, dict) else None
        except ValueError:
            output_val = None
    _add(output_val)

    result_val = data.get("result")
    if isinstance(result_val, dict) and not _add(result_val):
        for nested in result_val.values():
            _add(nested)

    rpc_calls = data.get("rpc_calls")
    if isinstance(rpc_calls, list):
        for entry in reversed(rpc_calls):
            _add(entry)

    stdout_val = data.get("stdout")
    if isinstance(stdout_val, str) and (
        '"PENDING"' in stdout_val or f'"{_DETACHED_JOB_PREFIX}' in stdout_val
    ):
        for line in reversed(stdout_val.splitlines()):
            stripped = line.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    if _add(json.loads(stripped)):
                        break
                except ValueError:
                    pass
        else:
            with contextlib.suppress(ValueError):
                _add(json.loads(stdout_val.strip()))

    for cand in candidates:
        job_id = str(cand.get("job_id") or "")
        if (
            job_id.startswith(DETACHED_JOB_PREFIX)
            and cand.get("status") == "RUNNING"
        ):
            return LroOperationInfo(
                operation_name=job_id,
                target_resource_name=str(cand.get("memo_path") or ""),
                operation_type="detached_assistant",
                status="PENDING",
            )

        name_fallback = str(cand.get("name") or "")
        op_name = str(
            cand.get("operation")
            or cand.get("operation_name")
            or cand.get("operationName")
            or (name_fallback if "operations/" in name_fallback else "")
            or ""
        )
        run_id = str(
            cand.get("run_id")
            or cand.get("runId")
            or cand.get("target_resource_name")
            or cand.get("targetResourceName")
            or cand.get("memo_path")
            or ""
        )
        if not (op_name or run_id):
            continue

        explicit_type = str(
            cand.get("operation_type")
            or cand.get("operationType")
            or cand.get("tool")
            or data.get("operation_type")
            or data.get("operationType")
            or ""
        )
        if explicit_type in _WRAPPER_TOOLS:
            explicit_type = ""

        is_eval = (
            explicit_type == "run_evaluation"
            or "run_evaluation" in default_tool_name
            or "/evaluationRuns/" in run_id
            or (
                isinstance(rpc_calls, list)
                and any(
                    isinstance(c, dict)
                    and (
                        c.get("tool") == "run_evaluation"
                        or c.get("operation_type") == "run_evaluation"
                    )
                    for c in rpc_calls
                )
            )
        )
        if explicit_type:
            operation_type = explicit_type
        elif op_name.startswith(DETACHED_JOB_PREFIX) or run_id.startswith(
            "memo/"
        ):
            operation_type = "detached_assistant"
        elif is_eval or default_tool_name in ("", *_WRAPPER_TOOLS):
            operation_type = "run_evaluation"
        else:
            operation_type = "operation"

        return LroOperationInfo(
            operation_name=op_name,
            target_resource_name=run_id,
            operation_type=operation_type,
            status="PENDING",
        )

    return None


def extract_from_tool_response(
    tool_name: str, response: dict[str, Any]
) -> LroOperationInfo | None:
    """Inspects a tool response payload for a pending LRO."""
    if not response:
        return None
    return extract_from_dict(response, default_tool_name=tool_name or "")


def extract_from_text(text: str) -> LroOperationInfo | None:
    """Finds a pending LRO payload inside a text chunk, if any."""
    if not text or (
        '"PENDING"' not in text and DETACHED_JOB_PREFIX not in text
    ):
        return None
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            parsed = json.loads(stripped)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            found = extract_from_dict(parsed)
            if found is not None:
                return found
    for match in _JSON_OBJECT_RE.finditer(text):
        try:
            parsed = json.loads(match.group(0))
        except ValueError:
            continue
        if isinstance(parsed, dict):
            found = extract_from_dict(parsed)
            if found is not None:
                return found
    return None
