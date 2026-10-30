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

"""Tests for the assistant stream reducer and markdown rendering."""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from cxas_scrapi.utils import arc_events, arc_lro
from cxas_scrapi.utils.arc_events import (
    ArtifactRecord,
    ConfirmationActionRecord,
    ConfirmationRequestRecord,
    ContractMetadataRecord,
    OptionQuestionRecord,
    StatusRecord,
    TurnHandoffRecord,
    TurnStreamEvents,
    TurnStreamReducer,
    parse_confirmation_request,
    parse_option_questions,
)

_TESTDATA = pathlib.Path(__file__).resolve().parents[2] / "testdata" / "arc"


def load_sse(name: str) -> list[dict[str, Any]]:
    """Parses a recorded ``data: {...}`` SSE body into JSON objects."""
    events = []
    for raw in (_TESTDATA / f"{name}.sse").read_bytes().splitlines():
        if raw.startswith(b"data:"):
            events.append(json.loads(raw[5:].strip().decode("utf-8")))
    return events


def reduce_fixture(name: str) -> TurnStreamEvents:
    reducer = TurnStreamReducer()
    for event in load_sse(name):
        reducer.feed(event)
    return reducer.events


def reduce_events(events: list[dict[str, Any]]) -> TurnStreamEvents:
    reducer = TurnStreamReducer()
    for event in events:
        reducer.feed(event)
    return reducer.events


# --------------------------------------------------------------- fixtures


def test_chat_sse_fixture() -> None:
    events = reduce_fixture("chat_sse")

    assert events.text
    assert "Card" in events.text
    tool_names = {tc.tool for tc in events.tool_calls}
    assert {"get_app", "list_agents", "list_tools"} <= tool_names
    assert len(events.tool_responses) == len(events.tool_calls)
    assert len(events.thoughts) > 0
    assert events.turn_completed is not None
    assert events.turn_completed.reason == "COMPLETED"
    assert events.turn_completed.turn_id == "0"
    assert events.turn_completed.final_sequence_number == 39
    assert events.last_sequence_number == 39
    assert events.pending_confirmation_requests == []
    assert events.initiated_lros == []

    md = events.format_markdown(include_tools=True)
    assert "### [Tool Activity]" in md
    assert "- **Call** `get_app`" in md
    assert "### [Confirmation Requests]" not in md
    assert "### [Thoughts]" not in md
    assert "### [Thoughts]" in events.format_markdown(include_thoughts=True)
    assert "### [Tool Activity]" not in events.format_markdown(
        include_tools=False
    )


def test_card_gate_fixture() -> None:
    events = reduce_fixture("card_gate")

    pending = events.pending_confirmation_requests
    assert len(pending) == 1
    assert pending[0].confirmation_id == "step-idx-12"
    assert pending[0].tool == "update_app"
    assert pending[0].agent_name == "ces_coding_agent"
    assert events.text == ""
    assert events.turn_completed is not None
    assert events.turn_completed.reason == "COMPLETED"

    md = events.format_markdown()
    assert "### [Confirmation Requests] ⚠️ awaiting your answer" in md
    assert "- **Card** `step-idx-12`" in md
    assert "  - Tool: `update_app`" in md


def test_card_confirm_fixture() -> None:
    events = reduce_fixture("card_confirm")

    assert "spike_marker" in events.text
    assert [tc.tool for tc in events.tool_calls] == [
        "call_kestrel_tool",
        "get_app",
    ]
    assert events.confirmation_requests == []
    assert events.initiated_lros == []
    assert events.last_sequence_number == 13


def test_lro_pending_fixture() -> None:
    events = reduce_fixture("lro_pending")

    assert len(events.initiated_lros) == 1
    lro = events.initiated_lros[0]
    assert isinstance(lro, arc_lro.LroOperationInfo)
    assert lro.operation_type == "run_evaluation"
    assert "/operations/" in lro.operation_name
    assert lro.target_resource_name
    assert lro.status == "PENDING"
    assert "Evaluation run initiated" in events.text


def test_lro_resume_fixture_tolerates_null_tool_response() -> None:
    events = reduce_fixture("lro_resume")

    assert "Evaluation Run Results" in events.text
    assert any(tr.response == {} for tr in events.tool_responses)
    assert events.initiated_lros == []
    assert events.last_sequence_number == 40


# -------------------------------------------------------- synthetic events


def test_artifact_chunks_merge_by_id() -> None:
    events = reduce_events(
        [
            {
                "artifactChunk": {
                    "artifactId": "art-1",
                    "displayName": "Agent Contract",
                    "mimeType": "text/markdown",
                    "contentDelta": "# Contract\n",
                    "state": "STREAMING",
                }
            },
            {"artifactChunk": {"artifactId": "art-1", "contentDelta": "Body"}},
            {
                "artifactChunk": {
                    "artifactId": "art-1",
                    "state": "COMPLETE",
                    "gcsUri": "gs://bucket/contract.md",
                }
            },
            {"artifactChunk": {"artifactId": "art-2", "contentDelta": "x"}},
        ]
    )

    assert [a.artifact_id for a in events.artifacts] == ["art-1", "art-2"]
    art = events.artifacts[0]
    assert art.display_name == "Agent Contract"
    assert art.mime_type == "text/markdown"
    assert art.content == "# Contract\nBody"
    assert art.gcs_uri == "gs://bucket/contract.md"
    assert art.state == "COMPLETE"

    md = events.format_markdown()
    assert (
        "### [Artifact] Agent Contract (state=COMPLETE, mime=text/markdown, "
        "gcs_uri=gs://bucket/contract.md)\n# Contract\nBody"
    ) in md
    assert "### [Artifact] art-2 (state=UNKNOWN)\nx" in md


def test_option_questions_chunk_renders_clarification_options() -> None:
    events = reduce_events(
        [
            {
                "optionQuestionsChunk": {
                    "questions": [
                        {
                            "question": "Which channel?",
                            "options": [
                                {
                                    "title": "Voice",
                                    "details": "phone calls",
                                    "submitText": "Use voice",
                                },
                                {"title": "Chat"},
                            ],
                        }
                    ]
                }
            }
        ]
    )

    assert len(events.option_questions) == 1
    assert events.pending_option_questions == events.option_questions
    md = events.format_markdown()
    assert "### [Clarification Options]" in md
    assert "1. Which channel?" in md
    assert '   [A] Voice — phone calls *(submit: "Use voice")*' in md
    assert "   [B] Chat" in md


def test_card_questions_are_not_pending_option_questions() -> None:
    card = {
        "confirmationId": "card-q",
        "tool": "ask_user",
        "context": "Need details",
        "positiveLabel": "Submit",
        "negativeLabel": "Skip",
        "expireTime": "2026-10-28T02:00:00Z",
        "questions": [
            {
                "question": "Pick one",
                "options": [{"title": "A"}, {"title": "B"}],
            }
        ],
    }
    events = reduce_events(
        [
            {"confirmationRequest": card},
            {
                "optionQuestionsChunk": {
                    "questions": [
                        {"question": "Standalone?", "options": [{"title": "Y"}]}
                    ]
                }
            },
        ]
    )

    assert [q.question for q in events.option_questions] == [
        "Pick one",
        "Standalone?",
    ]
    assert [q.question for q in events.pending_option_questions] == [
        "Standalone?"
    ]
    md = events.format_markdown()
    assert "  - Q1: Pick one" in md
    assert "    [A] A" in md
    assert '  - Buttons: confirm="Submit", decline="Skip"' in md
    assert "  - Expires: 2026-10-28T02:00:00Z" in md
    assert "  - Context: Need details" in md
    assert "### [Clarification Options]\n1. Standalone?" in md


def test_answered_confirmation_is_not_pending() -> None:
    events = reduce_events(
        [
            {"confirmationRequest": {"confirmationId": "c1", "tool": "t1"}},
            {"confirmationRequest": {"confirmationId": "c2", "tool": "t2"}},
            # Duplicate card id is reported once.
            {"confirmationRequest": {"confirmationId": "c2", "tool": "t2"}},
        ]
    )
    events.confirmation_actions.append(
        ConfirmationActionRecord(
            confirmation_id="c1",
            tool="t1",
            action="CLICK_CONFIRM",
            policy="click",
            round=1,
        )
    )

    assert [
        c.confirmation_id for c in events.pending_confirmation_requests
    ] == ["c2"]
    md = events.format_markdown()
    assert "### [Confirmation Actions]" in md
    assert "- `c1` (t1): CLICK_CONFIRM [clicked, policy=click, round=1]" in md
    assert "- **Card** `c2`" in md
    assert "- **Card** `c1`" not in md


def test_typed_confirmation_action_rendering() -> None:
    events = TurnStreamEvents()
    events.confirmation_actions.append(
        ConfirmationActionRecord(
            confirmation_id="c1", action="TYPE_TEXT", text="yes", typed=True
        )
    )
    md = events.format_markdown()
    assert '- `c1`: TYPE_TEXT "yes" [typed, policy=manual, round=0]' in md


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"contractRelated": True}, True),
        ({"contractFinalized": True}, True),
        ({"contractDraftComplete": True}, True),
        ({"contractRelated": False, "contractFinalized": False}, False),
    ],
)
def test_turn_metadata_contract_progress(
    metadata: dict[str, Any], expected: bool
) -> None:
    events = reduce_events([{"turnMetadata": metadata, "sequenceNumber": "1"}])

    assert isinstance(events.contract_metadata, ContractMetadataRecord)
    assert events.last_sequence_number == 1
    assert ("### [Contract Progress]" in events.format_markdown()) is expected


def test_empty_turn_metadata_is_ignored() -> None:
    events = reduce_events([{"turnMetadata": {}, "turnInProgress": True}])
    assert events.contract_metadata is None


def test_status_event_is_recorded_and_reported() -> None:
    progress: list[str] = []
    reducer = TurnStreamReducer(on_progress=progress.append)
    reducer.feed({"status": {"message": "Thinking..."}})
    reducer.feed({"status": {"code": 2}})
    reducer.feed({"toolCall": {"tool": "get_app"}})

    assert reducer.events.statuses == ["Thinking...", "code=2"]
    assert progress == [
        "status: Thinking...",
        "status: code=2",
        "tool: get_app",
    ]


def test_resume_snapshot_events_are_recursed() -> None:
    events = reduce_events(
        [
            {
                "resumeSnapshot": {
                    "events": [
                        {"textChunk": "Hello ", "sequenceNumber": "4"},
                        {"textChunk": "world", "sequenceNumber": "5"},
                        {"toolCall": {"id": "c1", "tool": "list_tools"}},
                    ]
                }
            },
            {"textChunk": "!"},
        ]
    )

    assert events.text == "Hello world!"
    assert events.last_sequence_number == 5
    assert [tc.tool for tc in events.tool_calls] == ["list_tools"]


@pytest.mark.parametrize(
    "event",
    [
        {},
        {"eventId": "x", "eventTime": "2026-10-28T00:00:00Z"},
        {"uiEvent": {"mimeType": "application/a2ui+json"}},
        {"sessionCheckpoint": {"userIntent": "x"}},
        {"optionQuestionsChunk": None},
        {"resumeSnapshot": None},
        {"sequenceNumber": "not-a-number"},
    ],
)
def test_unknown_or_empty_events_are_ignored(event: dict[str, Any]) -> None:
    events = reduce_events([event])

    assert events == TurnStreamEvents()
    assert events.format_markdown() == ""


@pytest.mark.parametrize(
    "event",
    [
        {"toolCall": None},
        {"toolResponse": None},
        {"artifactChunk": None},
        {"confirmationRequest": None},
        {"turnCompleted": None},
        {"status": None},
    ],
)
def test_null_payloads_do_not_crash(event: dict[str, Any]) -> None:
    events = reduce_events([event])

    assert events.text == ""
    assert events.pending_confirmation_requests == []
    assert isinstance(events.format_markdown(), str)
    json.dumps(events.to_dict())


def test_text_chunk_with_embedded_pending_payload_registers_lro() -> None:
    payload = json.dumps(
        {
            "status": "PENDING",
            "operation": "projects/p/locations/us/operations/op-9",
            "operation_type": "run_evaluation",
        }
    )
    events = reduce_events(
        [{"textChunk": f"Started: {payload}"}, {"textChunk": f" {payload}"}]
    )

    # The same operation seen twice is recorded once.
    assert len(events.initiated_lros) == 1
    assert events.initiated_lros[0].operation_name.endswith("/operations/op-9")


def test_merge_combines_text_and_dedups_artifacts_and_lros() -> None:
    lro = arc_lro.LroOperationInfo(
        operation_name="ops/1", target_resource_name="runs/1"
    )
    first = reduce_events(
        [
            {"textChunk": "first"},
            {"thoughtChunk": "t1"},
            {"artifactChunk": {"artifactId": "a", "contentDelta": "v1"}},
            {"turnCompleted": {"turnId": "0", "reason": "COMPLETED"}},
        ]
    )
    first.initiated_lros.append(lro)
    second = reduce_events(
        [
            {"textChunk": "second", "sequenceNumber": "7"},
            {"thoughtChunk": "t2"},
            {
                "artifactChunk": {
                    "artifactId": "a",
                    "state": "COMPLETE",
                    "contentDelta": "v2",
                }
            },
            {"artifactChunk": {"artifactId": "b", "contentDelta": "new"}},
            {"turnMetadata": {"contractRelated": True}},
            {"turnCompleted": {"turnId": "1", "reason": "CANCELLED"}},
        ]
    )
    second.initiated_lros.append(
        arc_lro.LroOperationInfo(
            operation_name="ops/1", target_resource_name="runs/1"
        )
    )
    second.initiated_lros.append(
        arc_lro.LroOperationInfo(
            operation_name="ops/2", target_resource_name="runs/2"
        )
    )

    first.merge(second)

    assert first.text == "first\n\nsecond"
    assert first.thoughts == ["t1", "t2"]
    assert [a.artifact_id for a in first.artifacts] == ["a", "b"]
    assert first.artifacts[0].content == "v2"
    assert first.artifacts[0].state == "COMPLETE"
    assert first.artifacts[1].content == "new"
    assert [lro.operation_name for lro in first.initiated_lros] == [
        "ops/1",
        "ops/2",
    ]
    assert first.contract_metadata is not None
    assert first.contract_metadata.contract_related is True
    assert first.turn_completed is not None
    assert first.turn_completed.reason == "CANCELLED"
    assert first.last_sequence_number == 7


def test_merge_into_empty_target_keeps_other_text() -> None:
    target = TurnStreamEvents()
    target.merge(reduce_events([{"textChunk": "only"}]))
    assert target.text == "only"


def test_merge_copies_artifacts() -> None:
    target = TurnStreamEvents()
    source = TurnStreamEvents(artifacts=[ArtifactRecord(artifact_id="a")])
    target.merge(source)
    target.artifacts[0].content = "mutated"
    assert source.artifacts[0].content == ""


def test_to_dict_is_json_serialisable() -> None:
    events = reduce_fixture("lro_pending")
    events.merge(reduce_fixture("card_gate"))

    data = events.to_dict()
    text = json.dumps(data)

    assert json.loads(text)["turn_completed"]["reason"] == "COMPLETED"
    assert data["initiated_lros"][0]["operation_type"] == "run_evaluation"
    assert data["confirmation_requests"][0]["confirmation_id"] == "step-idx-12"


# ------------------------------------------------------------------ parsers


def test_parse_option_questions_handles_missing_fields() -> None:
    records = parse_option_questions(
        [{"question": "Q?", "options": None}, {"options": [{}]}]
    )

    assert records[0].question == "Q?"
    assert records[0].options == []
    assert records[1].question == ""
    assert records[1].options[0].title == ""
    assert parse_option_questions([]) == []


def test_parse_confirmation_request_maps_camel_case() -> None:
    record = parse_confirmation_request(
        {
            "confirmationId": "c",
            "context": "ctx",
            "positiveLabel": "Yes",
            "negativeLabel": "No",
            "tool": "update_app",
            "agentName": "coder",
            "expireTime": "2026-01-01T00:00:00Z",
            "questions": [{"question": "q", "options": [{"title": "o"}]}],
        }
    )

    assert record.confirmation_id == "c"
    assert record.context == "ctx"
    assert record.positive_label == "Yes"
    assert record.negative_label == "No"
    assert record.tool == "update_app"
    assert record.agent_name == "coder"
    assert record.expire_time == "2026-01-01T00:00:00Z"
    assert record.questions[0].options[0].title == "o"
    assert parse_confirmation_request({}).confirmation_id == ""


def test_resume_snapshot_replaces_prior_turn_state_and_dedups_sequences() -> (
    None
):
    events = reduce_events(
        [
            {"thoughtChunk": "partial thought", "sequenceNumber": "1"},
            {"textChunk": "Hello ", "sequenceNumber": "2"},
            {
                "toolCall": {"id": "c1", "tool": "get_app"},
                "sequenceNumber": "3",
            },
            {
                "resumeSnapshot": {
                    "turnId": "t-1",
                    "resolvedSequenceNumber": "5",
                    "events": [
                        {"thoughtChunk": "compacted thought"},
                        {"textChunk": "Hello world"},
                        {"toolCall": {"id": "c1", "tool": "get_app"}},
                    ],
                },
                "sequenceNumber": "5",
            },
            # Replayed frame at <= resolvedSequenceNumber must be ignored.
            {"textChunk": " DUPLICATE", "sequenceNumber": "5"},
            {"textChunk": "!", "sequenceNumber": "6"},
            {
                "turnCompleted": {
                    "turnId": "t-1",
                    "reason": "COMPLETED",
                    "finalSequenceNumber": "6",
                },
                "sequenceNumber": "6",
            },
        ]
    )

    assert events.text == "Hello world!"
    assert events.thoughts == ["compacted thought"]
    assert [tc.tool for tc in events.tool_calls] == ["get_app"]
    assert events.last_sequence_number == 6
    assert events.turn_completed is not None
    assert events.turn_completed.reason == "COMPLETED"


def test_handoff_and_error_status_and_terminal_notes() -> None:
    events = reduce_events(
        [
            {"textChunk": "Working...", "sequenceNumber": "1"},
            {
                "status": {
                    "code": 7,
                    "message": "Permission denied calling backend",
                    "details": [{"@type": "type.googleapis.com/err"}],
                },
                "sequenceNumber": "2",
            },
            {"status": {"code": 0, "message": "ok"}, "sequenceNumber": "3"},
            {
                "handoff": {"turnId": "t-9", "reason": "TASK_DRAINING"},
                "sequenceNumber": "4",
            },
            {
                "turnCompleted": {
                    "turnId": "t-9",
                    "reason": "RESUME_UNAVAILABLE",
                    "finalSequenceNumber": "9",
                },
                "sequenceNumber": "5",
            },
        ]
    )

    assert events.errors == [
        StatusRecord(
            code=7,
            message="Permission denied calling backend",
            details=[{"@type": "type.googleapis.com/err"}],
        )
    ]
    assert events.handoff == TurnHandoffRecord(
        turn_id="t-9", reason="TASK_DRAINING"
    )
    md = events.format_markdown()
    assert "### [Errors]" in md
    assert "- `code=7`: Permission denied calling backend" in md
    assert "reason `RESUME_UNAVAILABLE`" in md
    assert "missed events up to sequence 9 (last seen: 5)" in md

    merged = TurnStreamEvents()
    merged.merge(events)
    assert merged.errors == events.errors
    assert merged.handoff == events.handoff


def test_codec_round_trip_snake_and_camel_and_public_exports() -> None:
    assert "LroOperationInfo" not in arc_events.__all__
    q_wire = {
        "question": "Channel?",
        "options": [{"title": "Voice", "submitText": "Voice only"}],
    }
    q_rec = OptionQuestionRecord.from_dict(q_wire)
    assert (
        OptionQuestionRecord.from_dict(
            {
                "question": "Channel?",
                "options": [{"title": "Voice", "submit_text": "Voice only"}],
            }
        )
        == q_rec
    )

    card_snake = {
        "confirmation_id": "c-1",
        "positive_label": "Go",
        "negative_label": "Stop",
        "agent_name": "builder",
        "expire_time": "2026-10-30T00:00:00Z",
        "questions": [
            {
                "question": "Channel?",
                "options": [{"title": "Voice", "submit_text": "Voice only"}],
            }
        ],
    }
    rebuilt = ConfirmationRequestRecord.from_dict(card_snake)
    assert rebuilt.confirmation_id == "c-1"
    assert rebuilt.positive_label == "Go"
    assert rebuilt.questions == [q_rec]
