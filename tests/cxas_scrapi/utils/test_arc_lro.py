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

"""Tests for client-managed LRO detection in assistant tool output."""

from __future__ import annotations

import json
from typing import Any

import pytest

from cxas_scrapi.utils import arc_lro

_OP = "projects/p/locations/us/operations/op-1"
_RUN = "projects/p/locations/us/apps/a/evaluationRuns/run-1"


def test_pending_top_level_payload() -> None:
    info = arc_lro.extract_from_tool_response(
        "run_evaluation",
        {"status": "PENDING", "operation": _OP, "run_id": _RUN},
    )

    assert info == arc_lro.LroOperationInfo(
        operation_name=_OP,
        target_resource_name=_RUN,
        operation_type="run_evaluation",
        status="PENDING",
    )


def test_recorded_wrapper_payload_uses_explicit_operation_type() -> None:
    # Shape streamed by the real API (see tests/testdata/arc/lro_pending.sse).
    info = arc_lro.extract_from_tool_response(
        "call_kestrel_tool",
        {
            "status": "PENDING",
            "run_id": _OP,
            "operation": _OP,
            "message": "Evaluation run initiated.",
            "operation_type": "run_evaluation",
        },
    )

    assert info is not None
    assert info.operation_name == _OP
    assert info.target_resource_name == _OP
    assert info.operation_type == "run_evaluation"


def test_nested_pending_lro() -> None:
    info = arc_lro.extract_from_tool_response(
        "call_kestrel_tool",
        {
            "status": "success",
            "pending_lro": {"status": "PENDING", "operation_name": _OP},
        },
    )

    assert info is not None
    assert info.operation_name == _OP
    assert info.target_resource_name == ""
    assert info.operation_type == "run_evaluation"


def test_output_json_string() -> None:
    info = arc_lro.extract_from_tool_response(
        "run_kestrel_python",
        {
            "output": json.dumps(
                {"status": "PENDING", "operationName": _OP, "runId": _RUN}
            )
        },
    )

    assert info is not None
    assert info.operation_name == _OP
    assert info.target_resource_name == _RUN
    assert info.operation_type == "run_evaluation"


def test_output_that_is_not_json_is_ignored() -> None:
    assert (
        arc_lro.extract_from_tool_response(
            "run_kestrel_python", {"output": "status PENDING but not json"}
        )
        is None
    )


def test_result_dict() -> None:
    info = arc_lro.extract_from_tool_response(
        "call_kestrel_tool",
        {"result": {"status": "PENDING", "operation": _OP}},
    )

    assert info is not None
    assert info.operation_name == _OP


def test_result_nested_values() -> None:
    info = arc_lro.extract_from_tool_response(
        "call_kestrel_tool",
        {
            "result": {
                "summary": {"status": "ok"},
                "eval": {"lro_status": "PENDING", "operation": _OP},
            }
        },
    )

    assert info is not None
    assert info.operation_name == _OP


def test_rpc_calls_last_pending_wins() -> None:
    info = arc_lro.extract_from_tool_response(
        "run_kestrel_python",
        {
            "rpc_calls": [
                {"tool": "get_app", "status": "success"},
                {
                    "tool": "run_evaluation",
                    "status": "PENDING",
                    "operation": "ops/first",
                },
                {
                    "tool": "run_evaluation",
                    "status": "PENDING",
                    "operation": "ops/last",
                },
                {"tool": "list_tools", "status": "success"},
            ]
        },
    )

    assert info is not None
    assert info.operation_name == "ops/last"
    assert info.operation_type == "run_evaluation"


def test_stdout_lines() -> None:
    stdout = "\n".join(
        [
            "starting evaluation",
            json.dumps({"status": "success", "step": 1}),
            json.dumps({"status": "PENDING", "operation": _OP}),
            "done",
        ]
    )
    info = arc_lro.extract_from_tool_response(
        "run_kestrel_python", {"stdout": stdout}
    )

    assert info is not None
    assert info.operation_name == _OP


def test_stdout_whole_blob() -> None:
    stdout = json.dumps({"status": "PENDING", "operation": _OP}, indent=2)
    info = arc_lro.extract_from_tool_response(
        "run_kestrel_python", {"stdout": stdout}
    )

    assert info is not None
    assert info.operation_name == _OP


def test_detached_assistant_job() -> None:
    info = arc_lro.extract_from_tool_response(
        "run_kestrel_python",
        {
            "status": "RUNNING",
            "job_id": "guddelon-42",
            "memo_path": "memo/report.md",
        },
    )

    assert info == arc_lro.LroOperationInfo(
        operation_name="guddelon-42",
        target_resource_name="memo/report.md",
        operation_type="detached_assistant",
        status="PENDING",
    )


def test_detached_job_in_stdout_without_pending_marker() -> None:
    stdout = json.dumps(
        {"status": "RUNNING", "job_id": "guddelon-7", "memo_path": "memo/x"}
    )
    info = arc_lro.extract_from_tool_response(
        "run_kestrel_python", {"stdout": stdout}
    )

    assert info is not None
    assert info.operation_type == "detached_assistant"
    assert info.operation_name == "guddelon-7"


def test_memo_target_without_job_id_is_detached() -> None:
    info = arc_lro.extract_from_tool_response(
        "run_kestrel_python",
        {"status": "PENDING", "memo_path": "memo/out.md"},
    )

    assert info is not None
    assert info.operation_type == "detached_assistant"
    assert info.target_resource_name == "memo/out.md"


@pytest.mark.parametrize("wrapper", ["call_kestrel_tool", "run_kestrel_python"])
def test_wrapper_tool_names_are_not_operation_types(wrapper: str) -> None:
    info = arc_lro.extract_from_tool_response(
        wrapper,
        {"status": "PENDING", "operation": _OP, "tool": wrapper},
    )

    assert info is not None
    assert info.operation_type == "run_evaluation"

    info = arc_lro.extract_from_tool_response(
        wrapper,
        {"status": "PENDING", "operation": _OP, "operation_type": wrapper},
    )

    assert info is not None
    assert info.operation_type not in arc_lro._WRAPPER_TOOLS


def test_explicit_operation_type_is_preserved() -> None:
    info = arc_lro.extract_from_tool_response(
        "call_kestrel_tool",
        {"status": "PENDING", "operation": _OP, "operation_type": "deploy_app"},
    )

    assert info is not None
    assert info.operation_type == "deploy_app"


def test_evaluation_run_id_implies_run_evaluation() -> None:
    info = arc_lro.extract_from_tool_response(
        "some_custom_tool",
        {"status": "PENDING", "operation": _OP, "run_id": _RUN},
    )

    assert info is not None
    assert info.operation_type == "run_evaluation"


def test_unknown_tool_without_eval_hints_is_generic_operation() -> None:
    info = arc_lro.extract_from_tool_response(
        "some_custom_tool", {"status": "PENDING", "operation": _OP}
    )

    assert info is not None
    assert info.operation_type == "operation"


def test_operation_name_fallback_from_name_field() -> None:
    info = arc_lro.extract_from_tool_response(
        "run_evaluation", {"status": "PENDING", "name": _OP}
    )

    assert info is not None
    assert info.operation_name == _OP


def test_pending_without_identifiers_is_ignored() -> None:
    assert (
        arc_lro.extract_from_tool_response(
            "run_evaluation", {"status": "PENDING", "message": "soon"}
        )
        is None
    )


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"status": "success", "name": "projects/p/locations/us/apps/a"},
        {"status": "RUNNING", "job_id": "not-detached"},
        {"result": {"state": "COMPLETED"}},
        {"rpc_calls": "not a list"},
    ],
)
def test_non_pending_returns_none(response: dict[str, Any]) -> None:
    assert (
        arc_lro.extract_from_tool_response("run_evaluation", response) is None
    )


def test_extract_from_dict_rejects_non_dict() -> None:
    assert arc_lro.extract_from_dict("nope") is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("tool", "response", "expected"),
    [
        ("run_evaluation", {"state": "COMPLETED"}, True),
        ("run_evaluation", {"state": "ERROR"}, True),
        ("run_evaluation", {"done": True}, True),
        (
            "run_evaluation",
            {"output": json.dumps({"state": "SUCCEEDED"})},
            True,
        ),
        ("run_evaluation", {"result": {"state": "CANCELLED"}}, True),
        ("run_evaluation", {"state": "RUNNING"}, False),
        ("run_evaluation", {}, False),
        ("get_app", {"state": "COMPLETED"}, False),
        (
            "call_kestrel_tool",
            {"result": {"name": _RUN, "state": "COMPLETED"}},
            True,
        ),
        (
            "call_kestrel_tool",
            {
                "state": "COMPLETED",
                "rpc_calls": [{"tool": "run_evaluation", "status": "success"}],
            },
            True,
        ),
        (
            "call_kestrel_tool",
            {"result": {"name": "projects/p/apps/a", "state": "COMPLETED"}},
            False,
        ),
        ("run_evaluation", {"output": "not json", "state": "COMPLETED"}, True),
    ],
)
def test_is_sync_completed_evaluation(
    tool: str, response: dict[str, Any], expected: bool
) -> None:
    assert arc_lro.is_sync_completed_evaluation(tool, response) is expected


def test_extract_from_text_finds_embedded_json() -> None:
    payload = json.dumps(
        {
            "status": "PENDING",
            "operation": _OP,
            "operation_type": "run_evaluation",
        }
    )
    text = f"Evaluation started. Details: {payload} -- please wait."

    info = arc_lro.extract_from_text(text)

    assert info is not None
    assert info.operation_name == _OP
    assert info.operation_type == "run_evaluation"


def test_extract_from_text_whole_object() -> None:
    text = json.dumps({"status": "PENDING", "run_id": _RUN})

    info = arc_lro.extract_from_text(text)

    assert info is not None
    assert info.target_resource_name == _RUN
    assert info.operation_type == "run_evaluation"


def test_extract_from_text_nested_object() -> None:
    payload = json.dumps(
        {"result": {"status": "PENDING", "operation": _OP}, "ok": True}
    )

    info = arc_lro.extract_from_text(f"result: {payload}")

    assert info is not None
    assert info.operation_name == _OP


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Evaluation run initiated in the background. Monitoring operation op-1.",
        'The status is "PENDING" but there is no JSON here.',
        '{"status": "PENDING", not valid json}',
        json.dumps({"status": "success", "operation": _OP}),
    ],
)
def test_extract_from_text_returns_none_for_plain_text(text: str) -> None:
    assert arc_lro.extract_from_text(text) is None
