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

"""Unit tests for cxas_scrapi.utils.tracing.trace_normalizer."""

from cxas_scrapi.utils.tracing import trace_normalizer

_ROOT_SPAN = {
    "name": "Turn",
    "childSpans": [
        {
            "name": "Callback",
            "attributes": {
                "agent": "order_status_cb",
                "type": "before_agent",
            },
            "childSpans": [
                {
                    "name": "Tool",
                    "attributes": {
                        "name": "lookup_orders",
                        "args": {"account_id": "1001"},
                        "response": {
                            "result": {
                                "status": "SUCCESS",
                                "orders": [{"sku": "Z9"}],
                            }
                        },
                    },
                    "childSpans": [
                        {
                            "name": "Tool",
                            "attributes": {"name": "nested_plumbing"},
                        }
                    ],
                }
            ],
        },
        # Fake mode: CES emits callback-invoked fakes as the callback's next
        # siblings.
        {
            "name": "Fake Tool",
            "attributes": {
                "name": "lookup_orders_secondary",
                "type": "ToolFake",
                "response": {"status": "SUCCESS", "orders": []},
            },
        },
        {"name": "Guardrail", "attributes": {"name": "g"}},
        # Also an LLM call this turn: not double counted.
        {"name": "Fake Tool", "attributes": {"name": "send_sms"}},
        {"name": "LLM", "attributes": {"name": "order_status_cb"}},
        {"name": "Fake Tool", "attributes": {"name": "after_llm_not_callback"}},
        {"name": "Tool", "attributes": {"name": "llm_tool_not_callback"}},
    ],
}

_RESULT = {
    "name": "synth_case__r0",
    "run": 1,
    "passed": True,
    "session_id": "sess-1",
    "detailed_trace": [
        "User: Hi",
        "Agent Text: Hello, what are you calling about today?",
        "User: Where is my order?",
        "\n".join([
            "Agent Transfer: Transferred to orders_router",
            "Agent Transfer: Transferred to order_status_cb",
            "Agent Text: Your Zentrix Z9 has shipped.",
            "It should arrive on November 4.",
            (
                "Tool Call: send_sms with args {'txnType': 'TRACK',"
                " 'order_id': '1001'}"
            ),
            (
                "Tool Response: send_sms with result {'result': {'statuscode':"
                " '200'}}"
            ),
        ]),
        "User: Can you cancel it? Yes please do.",
        "\n".join([
            "Agent Text (Diag): Connecting you to an order specialist.",
            "Tool Call: escalate_to_agent with args {'reason': 'cancel'}",
            (
                "Custom Payload: {'action': 'route', 'parameters':"
                " {'intent': 'order_cancel'}}"
            ),
        ]),
    ],
    "turn_traces": [
        {"turn_index": 1, "root_span": _ROOT_SPAN},
    ],
}


def test_parse_value() -> None:
    """Tests parsing Python repr, JSON, and plain string values."""
    assert trace_normalizer.parse_value("{'a': True}") == {"a": True}
    assert trace_normalizer.parse_value('{"a": true}') == {"a": True}
    assert trace_normalizer.parse_value("plain") == "plain"


def test_callback_tools_only_direct_children_and_siblings() -> None:
    """Tests extracting direct callback Tool and sibling Fake Tool spans."""
    out = trace_normalizer.callback_tools(_ROOT_SPAN)
    assert [(a, t, k) for a, t, _, _, k in out] == [
        ("order_status_cb", "lookup_orders", "Tool"),
        ("order_status_cb", "lookup_orders_secondary", "Fake Tool"),
        ("order_status_cb", "send_sms", "Fake Tool"),
    ]


def test_callback_tools_bigquery_shape_json_children() -> None:
    """Tests extracting callback tools from JSON-encoded child_spans."""
    span = {
        "name": "Turn",
        "child_spans": (
            '[{"name": "Callback", "attributes": {"agent": "x"},'
            ' "child_spans": [{"name": "Tool", "attributes": {"name": "t"}}]}]'
        ),
    }
    assert trace_normalizer.callback_tools(span) == [
        ("x", "t", None, None, "Tool")
    ]


def test_to_conversation_header_and_sequence() -> None:
    """Tests full conversation.v1 normalization and event ordering."""
    conv = trace_normalizer.to_conversation(_RESULT)
    events = conv["events"]
    assert conv["format"] == "conversation.v1"
    assert conv["source"] == "fde_eval_runner"
    assert conv["conversation_id"] == "synth_case__r0#sess-1"
    assert bool(conv["signals"]["session_escalated"])
    assert [(e["turn"], e["type"]) for e in events] == [
        (0, "user_message"),
        (0, "agent_message"),
        (1, "user_message"),
        (1, "tool_call"),
        (1, "tool_result"),
        (1, "tool_call"),
        (1, "tool_result"),
        (1, "handoff"),
        (1, "handoff"),
        (1, "tool_call"),
        (1, "tool_result"),
        (1, "agent_message"),
        (2, "user_message"),
        (2, "agent_message"),
        (2, "tool_call"),
        (2, "payload"),
    ]
    callback_calls = [e["tool"] for e in events if e.get("via")]
    assert callback_calls == ["lookup_orders", "lookup_orders_secondary"]
    assert (
        events[4]["response"]["session_changes"]["orders[0].sku"] == "Z9"
    )
    assert events[11]["actor"] == "order_status_cb"
    assert (
        events[11]["text"]
        == "Your Zentrix Z9 has shipped.\nIt should arrive on November 4."
    )


def test_tool_failed() -> None:
    """Tests case-insensitive error status detection on bare and result dicts."""
    assert trace_normalizer.tool_failed({"error": "x"})
    assert trace_normalizer.tool_failed({"status": "ERROR"})
    assert trace_normalizer.tool_failed({"status": "error"})
    assert trace_normalizer.tool_failed({"result": {"status": "error"}})
    assert trace_normalizer.tool_failed({"result": {"status": "ERROR"}})
    assert not trace_normalizer.tool_failed({"status": "SUCCESS"})
    assert not trace_normalizer.tool_failed({"result": {"ok": 1}})
    assert not trace_normalizer.tool_failed("text")


def test_nested_callback_tool_preserved_when_llm_calls_same_tool() -> None:
    """Tests preserving nested Callback -> Tool calls when LLM calls same tool."""
    conv = trace_normalizer.to_conversation({
        "name": "c__r0",
        "detailed_trace": [
            "User: check order",
            (
                "Tool Call: lookup_orders with args {'account_id': '1002'}\n"
                "Tool Response: lookup_orders with result {'status': 'SUCCESS'}"
            ),
        ],
        "turn_traces": [{"turn_index": 0, "root_span": _ROOT_SPAN}],
    })
    lookup_calls = [
        (e.get("via", "llm"), e["args"])
        for e in conv["events"]
        if e["type"] == "tool_call" and e["tool"] == "lookup_orders"
    ]
    assert lookup_calls == [
        ("callback", {"account_id": "1001"}),
        ("llm", {"account_id": "1002"}),
    ]


def test_callback_tools_display_name_input_output_and_tools_map() -> None:
    """Tests display_name/input/output keys and tools_map resolution in spans."""
    span = {
        "name": "Turn",
        "childSpans": [
            {
                "name": "Callback",
                "attributes": {"agent": "order_status_cb"},
                "childSpans": [
                    {
                        "name": "Tool",
                        "attributes": {
                            "display_name": "lookup_orders",
                            "input": {"account_id": "1001"},
                            "output": {"status": "SUCCESS"},
                        },
                    },
                    {
                        "name": "Tool",
                        "attributes": {
                            "tool": "projects/p/locations/l/apps/a/tools/tid_1",
                            "parameters": {"order_id": "99"},
                            "result": {"status": "SHIPPED"},
                        },
                    },
                ],
            },
            {
                "name": "Fake Tool",
                "attributes": {
                    "displayName": "send_sms",
                    "input": {"to": "555"},
                    "output": {"ok": True},
                },
            },
        ],
    }
    tools_map = {
        "projects/p/locations/l/apps/a/tools/tid_1": "lookup_orders_secondary"
    }
    out = trace_normalizer.callback_tools(span, tools_map=tools_map)
    assert out == [
        (
            "order_status_cb",
            "lookup_orders",
            {"account_id": "1001"},
            {"status": "SUCCESS"},
            "Tool",
        ),
        (
            "order_status_cb",
            "lookup_orders_secondary",
            {"order_id": "99"},
            {"status": "SHIPPED"},
            "Tool",
        ),
        (
            "order_status_cb",
            "send_sms",
            {"to": "555"},
            {"ok": True},
            "Fake Tool",
        ),
    ]
    conv = trace_normalizer.to_conversation(
        {
            "name": "c__r0",
            "detailed_trace": [
                "User: hi",
                "Tool Call: send_sms with args {'to': '555'}",
            ],
            "turn_traces": [{"turn_index": 0, "root_span": span}],
        },
        tools_map=tools_map,
    )
    tool_events = [
        (e["tool"], e.get("via", "llm"))
        for e in conv["events"]
        if e["type"] == "tool_call"
    ]
    assert tool_events == [
        ("lookup_orders", "callback"),
        ("lookup_orders_secondary", "callback"),
        ("send_sms", "llm"),
    ]
