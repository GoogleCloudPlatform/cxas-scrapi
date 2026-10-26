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

import dataclasses
import typing

from cxas_scrapi.redaction.extract import (
    align,
    conversation_to_leaves,
    messages_to_leaves,
    session_outputs_to_messages,
)
from cxas_scrapi.redaction.policy import Surface


def test_conversation_to_leaves_surfaces(
    raw_conversation: dict[str, typing.Any],
) -> None:
    leaves = conversation_to_leaves(raw_conversation)
    by_surface = {(x.surface, x.path) for x in leaves}
    assert (Surface.USER_TEXT, "text") in by_surface
    assert (Surface.AGENT_TEXT, "text") in by_surface
    assert (Surface.TOOL_ARGS, "args.card_number") in by_surface
    assert (Surface.TOOL_RESPONSE, "response.result.status_code") in by_surface
    status = next(x for x in leaves if x.key == "status_code")
    assert status.is_number
    assert status.text == "200"
    assert status.describe() == (
        "turn 1 Billing tool_response [response.result.status_code]"
    )


def test_transcripts_variables_and_payloads() -> None:
    leaves = messages_to_leaves(
        [
            {
                "role": "user",
                "chunks": [
                    {"transcript": "hello"},
                    {"updatedVariables": {"ani": "555", "nested": {"x": [1]}}},
                ],
            },
            {"role": "Agent", "chunks": [{"payload": {"code": "A1"}}, "skip"]},
        ],
        turn=3,
    )
    got = {(x.surface, x.path, x.text) for x in leaves}
    assert (Surface.USER_TEXT, "text", "hello") in got
    assert (Surface.VARIABLES, "variables.ani", "555") in got
    assert (Surface.VARIABLES, "variables.nested.x[0]", "1") in got
    assert (Surface.AGENT_TEXT, "payload.code", "A1") in got
    assert all(x.turn == 3 for x in leaves)


def test_empty_values_are_skipped() -> None:
    leaves = messages_to_leaves(
        [{"role": "user", "chunks": [{"text": ""}, {"toolCall": {}}]}], 0
    )
    assert leaves == []


def test_session_outputs_to_messages_uses_turn_completion() -> None:
    outputs = [
        {"text": "partial"},
        {
            "turnCompleted": True,
            "diagnosticInfo": {"messages": [{"role": "u"}]},
        },
    ]
    assert session_outputs_to_messages(outputs) == [{"role": "u"}]
    assert session_outputs_to_messages([{"text": "x"}]) == []


def test_align_by_location_and_fallback(
    raw_conversation: dict[str, typing.Any],
    persisted_conversation: dict[str, typing.Any],
) -> None:
    raw = conversation_to_leaves(raw_conversation)
    stored = conversation_to_leaves(persisted_conversation)
    pairs = align(raw, stored)
    assert all(p.stored is not None for p in pairs)
    assert all(p.raw.location == p.stored.location for p in pairs)

    # Shift message indexes: fallback still pairs on (turn, role, path).
    shifted = [dataclasses.replace(x, message=x.message + 1) for x in stored]
    pairs = align(raw, shifted)
    assert all(p.stored is not None for p in pairs)

    pairs = align(raw, [])
    assert all(p.stored is None for p in pairs)
