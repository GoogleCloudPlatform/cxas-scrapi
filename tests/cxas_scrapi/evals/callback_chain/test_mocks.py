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

"""Unit tests for MockLlm, ToolSpy, and LlmResponse snapshot serialization."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cxas_scrapi.evals.callback_chain.mocks import (
    MockLlm,
    ToolSpy,
    deserialize_llm_response,
    serialize_llm_response,
)
from cxas_scrapi.utils.callback_libs import (
    Blob,
    Content,
    LlmRequest,
    LlmResponse,
    Part,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_mock_llm_fluent_queue_and_matching(tmp_path: Path) -> None:
    """Verifies MockLlm queue order, conditional predicates, tool calls, and snapshot save/load."""
    mock = (
        MockLlm()
        .when_prompt_contains("transfer")
        .transfer_to_agent("billing_agent")
        .call_tool("lookup_order", {"order_id": "123"})
        .reply_text("Your order #123 is shipped.")
        .default_reply("Fallback response.")
    )

    req_normal = LlmRequest(
        contents=[Content(role="user", parts=[Part.from_text("check order")])]
    )
    resp1 = mock(req_normal)
    assert resp1.content.parts[0].function_call.name == "lookup_order"
    assert resp1.content.parts[0].function_call.args == {"order_id": "123"}

    resp2 = mock(req_normal)
    assert resp2.content.parts[0].text == "Your order #123 is shipped."

    resp3 = mock(req_normal)
    assert resp3.content.parts[0].text == "Fallback response."

    req_transfer = LlmRequest(
        contents=[
            Content(role="user", parts=[Part.from_text("please transfer me")])
        ]
    )
    resp_transfer = mock(req_transfer)
    assert (
        resp_transfer.content.parts[0].function_call.name == "transfer_to_agent"
    )
    assert resp_transfer.content.parts[0].function_call.args == {
        "agent_name": "billing_agent"
    }
    assert mock.call_count == 4

    snap_file = tmp_path / "llm.snap.json"
    mock.save_snapshot(snap_file)
    loaded = MockLlm.from_snapshot(snap_file)
    assert (
        loaded(req_normal).content.parts[0].function_call.name == "lookup_order"
    )
    assert (
        loaded(req_normal).content.parts[0].text
        == "Your order #123 is shipped."
    )


def test_serialize_and_deserialize_llm_response_with_binary_blob() -> None:
    """Verifies lossless base64 round-trip for binary inline_data in LlmResponse snapshots."""
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\xff"
    resp = LlmResponse.from_parts(
        [
            Part.from_text("Here is the chart"),
            Part(inline_data=Blob(data=png_bytes, mime_type="image/png")),
        ]
    )
    data = serialize_llm_response(resp)
    restored = deserialize_llm_response(data)
    assert restored.content.parts[0].text == "Here is the chart"
    assert restored.content.parts[1].inline_data.data == png_bytes
    assert restored.content.parts[1].inline_data.mime_type == "image/png"


def test_tool_spy_recording_and_sequencing() -> None:
    """Verifies ToolSpy records calls, return values, exceptions, and reset."""
    spy = ToolSpy(
        side_effect=[{"temp": 72}, {"temp": 68}],
        name="get_weather",
    )
    assert spy(city="SF") == {"temp": 72}
    assert spy({"city": "NYC"}) == {"temp": 68}
    assert spy.call_count == 2
    spy.assert_called_with(city="NYC")
    assert spy.last_args == {"city": "NYC"}

    spy.reset()
    assert spy.call_count == 0

    err_spy = ToolSpy(side_effect=RuntimeError("db down"), name="fail_tool")
    with pytest.raises(RuntimeError, match="db down"):
        err_spy(x=1)
    assert err_spy.call_count == 1
    assert isinstance(err_spy.exceptions[-1], RuntimeError)
