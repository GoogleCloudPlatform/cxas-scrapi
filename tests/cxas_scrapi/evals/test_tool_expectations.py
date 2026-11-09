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

"""Unit tests for deterministic tool_expectations scoring."""

from cxas_scrapi.core.response_parser import (
    ParsedToolCall,
    ParsedToolResponse,
    ToolSource,
)
from cxas_scrapi.evals import tool_expectations


def test_parse_trace_deduplicates_top_level_output_calls() -> None:
    trace = tool_expectations.parse_trace(
        [
            "User: event: welcome",
            (
                "Tool Call: projects/p/locations/us/apps/a/tools/lookup_account"
                " with args {'account_id': '10001'}\n"
                "Tool Response: lookup_account with result {'result':"
                " {'status': 'found', 'account_id': '10001'}}\n"
                "Tool Call (Output): lookup_account with args {'account_id':"
                " '10001'}\n"
                "Tool Call (Output): end_session with args {'params':"
                " {'status': 'completed', 'intent': 'account_lookup'}}"
            ),
        ]
    )

    assert not trace.unparsed_lines
    assert len(trace.calls("lookup_account")) == 1
    assert len(trace.responses("lookup_account")) == 1
    assert trace.all_calls()[-1].tool == "end_session"


def test_parse_trace_records_unparseable_tool_lines() -> None:
    trace = tool_expectations.parse_trace(
        [
            "Tool Call: create_order with args <unparseable object>",
        ]
    )

    assert len(trace.unparsed_lines) == 1
    results = tool_expectations.check(
        [{"type": "tool_not_called", "tool": "cancel_order"}], trace
    )
    assert results[0].met
    assert not results[-1].met
    assert results[-1].operator == "trace_parsed"


def test_parse_trace_from_parsed_tool_objects() -> None:
    call = ParsedToolCall(
        name="lookup_account",
        args={"account_id": "10001"},
        source=ToolSource.TOP_LEVEL,
    )
    resp = ParsedToolResponse(
        name="lookup_account",
        response={"result": {"status": "active"}},
    )
    trace = tool_expectations.parse_trace([call, resp])
    assert len(trace.calls("lookup_account")) == 1
    assert len(trace.responses("lookup_account")) == 1
    assert trace.calls("lookup_account")[0].payload == {"account_id": "10001"}


def test_operators_end_to_end() -> None:
    trace = tool_expectations.parse_trace(
        [
            (
                "Tool Call: check_availability with args"
                " {'account_id': '10001', 'item_count': 2.0}\n"
                "Tool Response: check_availability with result {'result':"
                " {'status': 'available'}}\n"
                "Tool Call: create_order with args {'account_id':"
                " '10001', 'confirmed': True, 'payload': '{\"method\":"
                ' "express"}\'}\n'
                "Tool Response: create_order with result {'result':"
                " {'is_scheduled': True, 'order_id': 'ORD-9871'}}\n"
                "Tool Call: update_status with args {'disposition':"
                " 'completed'}\n"
                "Tool Call (Output): end_session with args {'reason': 'finished'}"
            ),
        ]
    )
    expectations = [
        {"type": "tool_called", "tool": "create_order", "min": 1},
        {
            "type": "tool_not_called",
            "tool": "create_order",
            "before": {
                "tool": "check_availability",
                "result": {"status": "available"},
            },
        },
        {
            "type": "tool_input",
            "tool": "check_availability",
            "args": {"item_count": 2},
        },
        {
            "type": "tool_input",
            "tool": "create_order",
            "args": {"confirmed": True, "payload": {"method": "express"}},
        },
        {
            "type": "tool_output",
            "tool": "create_order",
            "result": {"is_scheduled": True, "order_id": "*"},
        },
        {
            "type": "every_call",
            "tool": "create_order",
            "args": {"account_id": "10001"},
        },
        {
            "type": "followed_by",
            "tool": "check_availability",
            "next": {"tool": "create_order"},
        },
        {"type": "terminal_tool", "tool": "end_session"},
        {"type": "last_tool", "tool": "end_session"},
        {"type": "end_session", "args": {"reason": "finished"}},
    ]

    results = tool_expectations.check(expectations, trace)
    assert all(r.met for r in results), str(results)


def test_end_session_operator_matches_args_and_nested_metadata() -> None:
    trace = tool_expectations.parse_trace(
        [
            (
                "Tool Call (Output): end_session with args {'params':"
                " {'queue': 'support', 'intent': 'order_inquiry'}}"
            ),
        ]
    )

    ok = tool_expectations.check(
        [
            {
                "type": "end_session",
                "args": {"queue": "support", "intent": "order_inquiry"},
            }
        ],
        trace,
    )
    bad = tool_expectations.check(
        [{"type": "end_session", "args": {"queue": "billing"}}],
        trace,
    )

    assert all(r.met for r in ok)
    assert not bad[0].met


def test_handoff_alias_checks_headers_and_args() -> None:
    trace = tool_expectations.parse_trace(
        [
            (
                "Tool Call (Output): end_session with args {'xHeaders':"
                " {'nextappcode': '0211', 'user_intent': 'General_Inquiry'}}"
            ),
        ]
    )

    ok = tool_expectations.check(
        [
            {
                "type": "handoff",
                "xheaders": {
                    "nextappcode": "0211",
                    "user_intent": "General_Inquiry",
                },
            }
        ],
        trace,
    )
    bad = tool_expectations.check(
        [{"type": "handoff", "xheaders": {"nextappcode": "0003"}}],
        trace,
    )

    assert all(r.met for r in ok)
    assert not bad[0].met


def test_schema_validation_valid_and_invalid_entries() -> None:
    valid = {"type": "tool_called", "tool": "lookup_account", "min": 2}
    assert not tool_expectations.validate(valid)

    assert tool_expectations.validate({}) == [
        "unknown type None (expected one of"
        f" {', '.join(tool_expectations.OPERATORS)})"
    ]
    assert (
        "missing 'tool'"
        in tool_expectations.validate({"type": "tool_called"})[0]
    )
    assert (
        "unexpected field(s)"
        in tool_expectations.validate(
            {
                "type": "tool_called",
                "tool": "lookup_account",
                "unknown": True,
            }
        )[0]
    )
    assert (
        "'min' must be an integer >= 1"
        in tool_expectations.validate(
            {
                "type": "tool_called",
                "tool": "lookup_account",
                "min": 0,
            }
        )[0]
    )

    all_errs = tool_expectations.validate_all([valid, {"type": "unknown_op"}])
    assert len(all_errs) == 1
    assert "entry [1]" in all_errs[0]


def test_value_matches_wildcard_bool_number_and_dict() -> None:
    assert tool_expectations.value_matches("*", "non-empty")
    assert not tool_expectations.value_matches("*", "")
    assert not tool_expectations.value_matches("*", None)

    assert tool_expectations.value_matches(True, "true")
    assert tool_expectations.value_matches(False, "FALSE")
    assert not tool_expectations.value_matches(True, False)

    assert tool_expectations.value_matches(10, 10.0)
    assert tool_expectations.value_matches("42", 42)
    assert not tool_expectations.value_matches(10, 11)

    assert tool_expectations.value_matches(
        {"k": "v"}, '{"k": "v", "extra": 123}'
    )


def test_apply_to_result_folds_and_is_idempotent() -> None:
    tc = {
        "name": "Order Sim",
        "tool_expectations": [
            {"type": "tool_called", "tool": "create_order"},
        ],
    }
    result = {
        "name": "Order Sim",
        "passed": True,
        "goals": "1/1",
        "expectations": "1/1",
        "detailed_trace": ["User: Hi", "Agent: Order created!"],
        "expectation_details": [
            {
                "expectation": "Agent is polite",
                "status": "Met",
                "justification": "Polite.",
            }
        ],
    }

    checks = tool_expectations.apply_to_result(tc, result)
    assert len(checks) == 2
    assert not result["passed"]
    assert result["judge_passed"] is True
    assert result["expectations"] == "2/3"

    tool_expectations.apply_to_result(tc, result)
    assert result["expectations"] == "2/3"
    assert len(result["expectation_details"]) == 3


def test_apply_to_result_skips_when_no_expectations_or_crashed() -> None:
    tc_no_exp = {"name": "No Exp"}
    res_no_exp = {"name": "No Exp", "passed": True, "detailed_trace": []}
    assert not tool_expectations.apply_to_result(tc_no_exp, res_no_exp)

    crashed = {"name": "Crashed", "passed": False, "error": "timeout"}
    tc_with_exp = {
        "tool_expectations": [{"type": "tool_called", "tool": "lookup"}]
    }
    assert not tool_expectations.apply_to_result(tc_with_exp, crashed)


def test_checker_error_result() -> None:
    err = tool_expectations.checker_error_result(ValueError("boom"))
    assert not err.met
    assert err.operator == "checker_error"
    assert "boom" in err.justification
