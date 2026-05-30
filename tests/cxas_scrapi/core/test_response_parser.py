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

from types import SimpleNamespace

from google.cloud.ces_v1beta import types

from cxas_scrapi.core.response_parser import ParsedSessionResponse


def test_parser_basic_text():
    """Test parsing basic top-level text responses."""
    response = types.RunSessionResponse(
        outputs=[
            types.SessionOutput(text="Hello there!"),
            types.SessionOutput(text="How can I help you today?"),
        ]
    )

    parsed = ParsedSessionResponse(response)
    assert (
        parsed.consolidated_agent_text
        == "Hello there! How can I help you today?"
    )
    assert parsed.session_ended is False
    assert len(parsed.tool_calls) == 0
    assert len(parsed.tool_responses) == 0


def test_parser_top_level_end_session():
    """Test parsing top-level end_session flag."""
    response = types.RunSessionResponse(
        outputs=[
            types.SessionOutput(text="Goodbye!"),
            types.SessionOutput(end_session={}),
        ]
    )

    parsed = ParsedSessionResponse(response)
    assert parsed.consolidated_agent_text == "Goodbye!"
    assert parsed.session_ended is True


def test_parser_top_level_tool_calls():
    """Test parsing top-level tool calls."""
    response = types.RunSessionResponse(
        outputs=[
            types.SessionOutput(
                tool_calls=types.ToolCalls(
                    tool_calls=[
                        types.ToolCall(tool="my_tool", args={"arg1": "val1"})
                    ]
                )
            )
        ]
    )

    parsed = ParsedSessionResponse(response)
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].name == "my_tool"
    assert parsed.tool_calls[0].args == {"arg1": "val1"}
    assert parsed.tool_calls[0].source == "top_level"
    assert parsed.session_ended is False


def test_parser_diagnostic_info_chunks():
    """Test parsing diagnostic_info messages and chunks."""
    response = types.RunSessionResponse(
        outputs=[
            types.SessionOutput(
                diagnostic_info={
                    "messages": [
                        {
                            "role": "user",
                            "chunks": [
                                {"text": "I want to pay my bill"},
                                {"transcript": "Spoken transcript"},
                            ],
                        },
                        {
                            "role": "agent",
                            "chunks": [
                                {"text": "Sure, I can help with that."},
                                {
                                    "tool_call": {
                                        "tool": "pay_bill_tool",
                                        "args": {"amount": 100},
                                    }
                                },
                                {
                                    "tool_response": {
                                        "tool": "pay_bill_tool",
                                        "response": {"status": "success"},
                                    }
                                },
                                {
                                    "agent_transfer": {
                                        "display_name": "Billing Specialist"
                                    }
                                },
                                {"payload": {"custom_key": "custom_value"}},
                            ],
                        },
                    ]
                }
            ),
        ]
    )

    parsed = ParsedSessionResponse(response)

    # Consolidated user text
    assert (
        parsed.consolidated_user_text
        == "I want to pay my bill Spoken transcript"
    )

    # Consolidated agent text
    assert parsed.consolidated_agent_text == "Sure, I can help with that."

    # Tool calls
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].name == "pay_bill_tool"
    assert parsed.tool_calls[0].args == {"amount": 100}
    assert parsed.tool_calls[0].source == "diagnostic_info"

    # Tool responses
    assert len(parsed.tool_responses) == 1
    assert parsed.tool_responses[0].name == "pay_bill_tool"
    assert parsed.tool_responses[0].response == {"status": "success"}

    # Agent transfer
    assert parsed.agent_transfer is not None
    assert parsed.agent_transfer.display_name == "Billing Specialist"

    # Custom payload
    assert len(parsed.custom_payloads) == 1
    assert parsed.custom_payloads[0] == {"custom_key": "custom_value"}


def test_parser_legacy_naming_compatibility():
    """Test parser handles legacy function_call / function_response naming."""
    # Mock chunk with function_call/function_response attribute values
    chunk1 = SimpleNamespace(
        function_call=SimpleNamespace(name="legacy_tool", args={"foo": "bar"})
    )
    chunk2 = SimpleNamespace(
        function_response=SimpleNamespace(
            name="legacy_tool", response={"ok": True}
        )
    )

    output = SimpleNamespace(
        diagnostic_info=SimpleNamespace(
            messages=[
                SimpleNamespace(role="agent", chunks=[chunk1, chunk2]),
            ],
            root_span=None,
        )
    )

    parsed = ParsedSessionResponse([output])

    # Legacy tool call
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].name == "legacy_tool"
    assert parsed.tool_calls[0].args == {"foo": "bar"}

    # Legacy tool response
    assert len(parsed.tool_responses) == 1
    assert parsed.tool_responses[0].name == "legacy_tool"
    assert parsed.tool_responses[0].response == {"ok": True}


def test_parser_guardrail_triggers():
    """Test parsing guardrail triggers from diagnostic_info.root_span."""
    response = types.RunSessionResponse(
        outputs=[
            types.SessionOutput(
                diagnostic_info={
                    "root_span": {
                        "name": "Root",
                        "child_spans": [
                            {
                                "name": "Agent Execution",
                                "child_spans": [
                                    {
                                        "name": "Safety Check",
                                        "attributes": {
                                            "name": (
                                                "Inappropriate Content "
                                                "Guardrail"
                                            ),
                                            "guardrail_type": "RAI_SAFETY",
                                            "reason": (
                                                "Triggered by policy violation."
                                            ),
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                }
            ),
        ]
    )

    parsed = ParsedSessionResponse(response)
    assert parsed.guardrail_trigger is not None
    assert parsed.guardrail_trigger.name == "Inappropriate Content Guardrail"
    assert parsed.guardrail_trigger.type == "RAI_SAFETY"
    assert parsed.guardrail_trigger.reason == "Triggered by policy violation."


def test_parser_detailed_trace_chunks():
    """Test that detailed_trace matches formatting requirements."""
    response = types.RunSessionResponse(
        outputs=[
            types.SessionOutput(text="Response text"),
            types.SessionOutput(
                tool_calls=types.ToolCalls(
                    tool_calls=[
                        types.ToolCall(tool="top_tool", args={"k": "v"})
                    ]
                )
            ),
            types.SessionOutput(
                diagnostic_info={
                    "messages": [
                        {
                            "role": "agent",
                            "chunks": [
                                {
                                    "tool_call": {
                                        "tool": "diag_tool",
                                        "args": {"a": 1},
                                    }
                                },
                                {
                                    "tool_response": {
                                        "tool": "diag_tool",
                                        "response": {"r": 2},
                                    }
                                },
                                {"payload": {"p": 3}},
                            ],
                        }
                    ]
                },
            ),
        ]
    )

    parsed = ParsedSessionResponse(response)
    assert "Agent Text: Response text" in parsed.detailed_trace
    assert (
        "Tool Call (Output): top_tool with args {'k': 'v'}"
        in parsed.detailed_trace
    )
    assert "Tool Call: diag_tool with args {'a': 1.0}" in parsed.detailed_trace
    assert (
        "Tool Response: diag_tool with result {'r': 2.0}"
        in parsed.detailed_trace
    )
    assert "Custom Payload: {'p': 3.0}" in parsed.detailed_trace
