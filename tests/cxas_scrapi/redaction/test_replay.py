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

import json
import pathlib
import typing
from unittest.mock import MagicMock, patch

import pytest

from cxas_scrapi.redaction.checker import FindingKind
from cxas_scrapi.redaction.dlp import ReplayMode
from cxas_scrapi.redaction.extract import conversation_to_leaves
from cxas_scrapi.redaction.replay import (
    RedactionReplay,
    collect_conversation_names,
    dedupe_leaves,
    load_conversation_file,
)

from .conftest import fake_redact

APP = "projects/p/locations/l/apps/a"


class FakeDlp:
    inspect_template = "projects/p/locations/l/inspectTemplates/i"
    deidentify_template = "projects/p/locations/l/deidentifyTemplates/d"

    def __init__(self) -> None:
        self.value_calls: list[tuple[list[str], typing.Any]] = []
        self.conversation_calls: list[list[tuple[str, str]]] = []

    def deidentify_values(
        self, values: list[str], headers: list[str] | None = None
    ) -> list[str]:
        self.value_calls.append((values, headers))
        return [fake_redact(v) for v in values]

    def deidentify_conversation(
        self, messages: list[tuple[str, str]]
    ) -> list[str]:
        self.conversation_calls.append(messages)
        return [fake_redact(t) for _, t in messages]


def test_platform_replay_mirrors_logging(
    policy: typing.Any, raw_conversation: dict[str, typing.Any]
) -> None:
    dlp = FakeDlp()
    replay = RedactionReplay(policy, dlp_client=dlp)
    report = replay.replay_conversation(raw_conversation)
    assert report.mode == "replay:platform"
    assert len(dlp.value_calls) == 1
    # Numbers are sent the way the logging pipeline renders them.
    assert "200.0" in dlp.value_calls[0][0]
    assert report.count(FindingKind.OVER_REDACTION) == 3
    assert report.count(FindingKind.TYPE_CHANGED) == 1
    assert report.metadata["inspect_template"].endswith("/i")


def test_conversation_mode_sends_dialog(
    policy: typing.Any, raw_conversation: dict[str, typing.Any]
) -> None:
    dlp = FakeDlp()
    replay = RedactionReplay(
        policy, dlp_client=dlp, mode=ReplayMode.CONVERSATION
    )
    replay.replay_conversation(raw_conversation)
    (messages,) = dlp.conversation_calls
    assert messages[0] == ("user", "Pay with card 4111 1111 1111 1111.")
    # Structured values are still inspected one by one, without keys.
    (struct_call,) = dlp.value_calls
    assert struct_call[1] is None
    assert "4111111111111111" in struct_call[0]


def test_key_aware_mode_passes_headers(
    policy: typing.Any, raw_conversation: dict[str, typing.Any]
) -> None:
    dlp = FakeDlp()
    RedactionReplay(
        policy, dlp_client=dlp, mode="key_aware"
    ).replay_conversation(raw_conversation)
    _, headers = dlp.value_calls[-1]
    assert headers == ["card_number", "cvv", "status_code"]


def test_templates_resolved_from_app(policy: typing.Any) -> None:
    app = MagicMock()
    app.logging_settings.redaction_config.inspect_template = (
        "projects/p/locations/us/inspectTemplates/i"
    )
    app.logging_settings.redaction_config.deidentify_template = (
        "projects/p/locations/us/deidentifyTemplates/d"
    )
    with (
        patch("cxas_scrapi.core.apps.Apps") as apps_cls,
        patch("cxas_scrapi.redaction.replay.DlpClient") as dlp_cls,
    ):
        apps_cls.return_value.get_app.return_value = app
        RedactionReplay(policy, app_name=APP)
    dlp_cls.assert_called_once()
    assert dlp_cls.call_args.kwargs["deidentify_template"].endswith("/d")


def test_missing_templates_errors(policy: typing.Any) -> None:
    app = MagicMock()
    app.logging_settings.redaction_config.inspect_template = ""
    app.logging_settings.redaction_config.deidentify_template = ""
    with patch("cxas_scrapi.core.apps.Apps") as apps_cls:
        apps_cls.return_value.get_app.return_value = app
        with pytest.raises(ValueError, match="no redaction templates"):
            RedactionReplay(policy, app_name=APP)
    with pytest.raises(ValueError, match="pass app_name"):
        RedactionReplay(policy)


def test_replay_evaluation_run(
    policy: typing.Any, raw_conversation: dict[str, typing.Any]
) -> None:
    conv_name = raw_conversation["name"]
    result = {
        "name": f"{APP}/evaluations/e/results/r",
        "displayName": "golden one",
        "goldenResult": {
            "turnReplayResults": [
                {"conversation": conv_name},
                {"conversation": conv_name},
            ]
        },
    }
    history = MagicMock()
    history.get_conversation.return_value = raw_conversation
    evals = MagicMock()
    evals.list_evaluation_results_by_run.return_value = [result]
    with (
        patch(
            "cxas_scrapi.core.conversation_history.ConversationHistory",
            return_value=history,
        ),
        patch("cxas_scrapi.core.evaluations.Evaluations", return_value=evals),
    ):
        replay = RedactionReplay(policy, app_name=APP, dlp_client=FakeDlp())
        report = replay.replay_evaluation_run("run-1")
    evals.list_evaluation_results_by_run.assert_called_once_with(
        f"{APP}/evaluationRuns/run-1"
    )
    assert report.sources == ["golden one"]
    # Duplicate views of the same dialog are only checked once.
    assert report.stats["crisis_line"].occurrences == 2
    assert report.metadata["evaluation_run"].endswith("run-1")


def test_replay_conversation_ids_requires_app(policy: typing.Any) -> None:
    replay = RedactionReplay(policy, dlp_client=FakeDlp())
    with pytest.raises(ValueError, match="app_name is required"):
        replay.replay_conversation_ids(["c1"])
    with pytest.raises(ValueError, match="app_name is required"):
        replay.replay_evaluation_run("r")


def test_collect_and_dedupe(raw_conversation: dict[str, typing.Any]) -> None:
    obj = {
        "a": [{"conversation": f"{APP}/conversations/1"}],
        "b": {"conversation": f"{APP}/conversations/1"},
        "c": {"conversation": "not-a-resource"},
    }
    assert collect_conversation_names(obj) == [f"{APP}/conversations/1"]
    leaves = conversation_to_leaves(raw_conversation)
    assert len(dedupe_leaves(leaves + leaves)) == len(dedupe_leaves(leaves))


def test_load_conversation_file(tmp_path: pathlib.Path) -> None:
    good = tmp_path / "c.json"
    good.write_text(json.dumps({"turns": []}))
    assert load_conversation_file(good) == {"turns": []}
    bad = tmp_path / "b.json"
    bad.write_text("[]")
    with pytest.raises(ValueError, match="not a conversation export"):
        load_conversation_file(bad)
