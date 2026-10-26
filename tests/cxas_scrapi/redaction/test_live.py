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

import pathlib
import typing
from unittest.mock import MagicMock, patch

import pytest
import yaml

from cxas_scrapi.redaction.checker import FindingKind
from cxas_scrapi.redaction.live import (
    LiveRedactionCheck,
    LiveSessionCapture,
    load_live_script,
    response_to_outputs,
    response_to_turn_messages,
)

APP = "projects/p/locations/l/apps/a"


def _check(policy: typing.Any, conversation: dict) -> LiveRedactionCheck:
    check = LiveRedactionCheck(APP, policy, poll_interval_s=0)
    history = MagicMock()
    history.get_conversation.return_value = MagicMock(_pb=conversation)
    check._history = history
    return check


@pytest.fixture(autouse=True)
def _identity_message_to_dict() -> typing.Iterator[None]:
    with patch(
        "google.protobuf.json_format.MessageToDict", side_effect=lambda pb: pb
    ):
        yield


def test_check_capture_compares_live_and_persisted(
    policy: typing.Any,
    raw_turns: list,
    persisted_conversation: dict[str, typing.Any],
) -> None:
    check = _check(policy, persisted_conversation)
    capture = LiveSessionCapture("s1", turn_messages=raw_turns)
    report = check.check_capture(capture, require=["payment_card"])
    assert report.mode == "live"
    assert report.metadata == {
        "session_id": "s1",
        "turns": 2,
        "unmatched_leaves": 0,
    }
    assert report.count(FindingKind.OVER_REDACTION) == 3
    assert report.count(FindingKind.NOT_EXERCISED) == 0
    check.history.get_conversation.assert_called_with("s1")


def test_fetch_persisted_times_out(policy: typing.Any) -> None:
    check = LiveRedactionCheck(
        APP, policy, persist_timeout_s=0, poll_interval_s=0
    )
    history = MagicMock()
    history.get_conversation.side_effect = RuntimeError("not found")
    check._history = history
    with pytest.raises(TimeoutError, match="not persisted"):
        check.fetch_persisted("s1")


def test_fetch_persisted_returns_partial_after_deadline(
    policy: typing.Any, persisted_conversation: dict[str, typing.Any]
) -> None:
    check = LiveRedactionCheck(
        APP, policy, persist_timeout_s=0, poll_interval_s=0
    )
    check._history = MagicMock()
    check._history.get_conversation.return_value = MagicMock(
        _pb=persisted_conversation
    )
    conv = check.fetch_persisted("s1", min_turns=5)
    assert len(conv["turns"]) == 2


def test_run_script(
    tmp_path: pathlib.Path,
    policy: typing.Any,
    raw_turns: list,
    persisted_conversation: dict[str, typing.Any],
) -> None:
    script = tmp_path / "script.yaml"
    script.write_text(
        yaml.safe_dump(
            {
                "common_redaction_expectations": {"require": ["percent"]},
                "conversations": [
                    {
                        "name": "pay",
                        "variables": {"ani": "1"},
                        "turns": [{"user": "a"}, "b"],
                    }
                ],
            }
        )
    )
    convs = load_live_script(script)
    assert convs[0].turns == ["a", "b"]
    assert convs[0].redaction_expectations == {"require": ["percent"]}

    check = _check(policy, persisted_conversation)
    sessions = MagicMock()
    sessions.run.side_effect = [
        {"outputs": [{"diagnosticInfo": {"messages": t}}]} for t in raw_turns
    ]
    check._sessions = sessions
    report = check.run_script(convs, modality="text", base_dir=tmp_path)

    first, second = sessions.run.call_args_list
    assert first.kwargs["variables"] == {"ani": "1"}
    assert "variables" not in second.kwargs
    assert report.sources[0].startswith("pay (redaction-")
    assert report.count(FindingKind.NOT_EXERCISED) == 0


def test_load_live_script_errors(tmp_path: pathlib.Path) -> None:
    script = tmp_path / "s.yaml"
    script.write_text(yaml.safe_dump({"conversations": "nope"}))
    with pytest.raises(ValueError, match="conversations"):
        load_live_script(script)


def test_response_helpers() -> None:
    assert response_to_outputs(None) == []
    resp = {"outputs": [{"diagnosticInfo": {"messages": [{"role": "u"}]}}]}
    assert response_to_turn_messages(resp) == [{"role": "u"}]

    wrapped = MagicMock()
    wrapped.original_response._pb = {"outputs": [{"text": "hi"}]}
    assert response_to_outputs(wrapped) == [{"text": "hi"}]
