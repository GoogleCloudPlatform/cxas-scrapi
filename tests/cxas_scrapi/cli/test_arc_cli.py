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

"""Tests for ``cxas arc`` (``cxas_scrapi.cli.arc_cli``)."""

from __future__ import annotations

import argparse
import io
import json
import os
import stat
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, create_autospec

import pytest

import cxas_scrapi.core.arc as arc_mod
from cxas_scrapi.cli import arc_cli, main
from cxas_scrapi.core.arc import Arc, ArcApiError, ArcStreamTimeout
from cxas_scrapi.core.common import DEFAULT_API_ENDPOINT
from cxas_scrapi.utils import arc_confirmation_policy as policy_mod
from cxas_scrapi.utils.arc_context import ArcTarget
from cxas_scrapi.utils.arc_events import (
    ConfirmationActionRecord,
    ConfirmationRequestRecord,
    OptionChoiceRecord,
    OptionQuestionRecord,
    StatusRecord,
    ToolCallRecord,
    TurnCompletedRecord,
    TurnStreamEvents,
)

APP = "projects/p/locations/us/apps/a"
ENDPOINT = "ces.googleapis.com"
KEY = f"{ENDPOINT}:{APP}"
SESSION = "sess-1"
TARGET = ArcTarget(app_name=APP, api_endpoint=ENDPOINT)
BASE_CMD = f"cxas arc --app-name={APP} --session-id={SESSION}"


# ----------------------------------------------------------------- helpers


def _gate_card(
    cid: str = "conf-1", tool: str = "update_app"
) -> ConfirmationRequestRecord:
    return ConfirmationRequestRecord(
        confirmation_id=cid,
        tool=tool,
        context="Publish the agent plan",
        positive_label="Publish",
        negative_label="Not yet",
        expire_time="2026-10-26T03:00:00Z",
    )


def _questions() -> list[OptionQuestionRecord]:
    return [
        OptionQuestionRecord(
            question="Which channel?",
            options=[
                OptionChoiceRecord(title="Voice", submit_text="Voice only"),
                OptionChoiceRecord(title="Chat"),
            ],
        ),
        OptionQuestionRecord(question="Escalate?", options=[]),
    ]


def _question_card(
    cid: str = "q-1", questions: list[OptionQuestionRecord] | None = None
) -> ConfirmationRequestRecord:
    return ConfirmationRequestRecord(
        confirmation_id=cid,
        tool="ask_user",
        questions=_questions() if questions is None else questions,
    )


def _events(**kwargs: object) -> TurnStreamEvents:
    return TurnStreamEvents(**kwargs)  # type: ignore[arg-type]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    arc_cli.register(sub)
    return parser


def _argv(store: Path, *rest: str) -> list[str]:
    return [
        "arc",
        *rest,
        f"--app-name={APP}",
        f"--api-endpoint={ENDPOINT}",
        f"--session-store={store}",
        f"--session-id={SESSION}",
    ]


def _run(store: Path, *rest: str) -> argparse.Namespace:
    args = _parser().parse_args(_argv(store, *rest))
    args.func(args)
    return args


def _seed_pending(store: Path, events: TurnStreamEvents) -> str:
    pending = arc_cli.pending_store_path(str(store))
    arc_cli.save_pending_ui_state(pending, KEY, SESSION, events)
    return pending


def _pending(store: Path) -> dict[str, object]:
    return arc_cli.load_pending_ui_state(
        arc_cli.pending_store_path(str(store)), KEY, SESSION
    )


@pytest.fixture
def store(tmp_path: Path) -> Path:
    return tmp_path / "s.json"


@pytest.fixture
def fake_arc(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake = create_autospec(Arc, instance=True)
    fake.client_profile = "console"
    fake.creds = object()
    fake.project_id = "p"
    fake.active_turn_events = None
    fake.last_stream_events = None
    fake.session_name.side_effect = lambda sid: f"{APP}/assistantSessions/{sid}"
    fake.chat.return_value = _events()
    fake.send_confirmation_response.return_value = _events()
    fake.list_sessions.return_value = []
    fake.get_session.return_value = {}
    fake.delete_session.return_value = None
    fake.cancel_turn.return_value = {"cancelled": True}
    fake.generate_onboarding_suggestions.return_value = []
    fake.session_artifacts.return_value = []
    monkeypatch.setattr(
        "cxas_scrapi.core.arc.Arc", MagicMock(return_value=fake)
    )
    return fake


# ---------------------------------------------------------------- register


@pytest.mark.parametrize(
    ("argv", "func"),
    [
        (["chat", "--message", "hi"], arc_cli.arc_chat),
        (["confirm", "--confirmed"], arc_cli.arc_confirm),
        (["answer", "--choices", "A"], arc_cli.arc_answer),
        (["sessions", "list"], arc_cli.arc_sessions_list),
        (["sessions", "get"], arc_cli.arc_sessions_get),
        (["sessions", "new"], arc_cli.arc_sessions_new),
        (["sessions", "delete"], arc_cli.arc_sessions_delete),
        (["cancel"], arc_cli.arc_cancel),
        (["suggestions"], arc_cli.arc_suggestions),
    ],
)
def test_register_routes_subcommands(argv: list[str], func: object) -> None:
    args = _parser().parse_args(["arc", *argv, "--app-name", "x"])
    assert args.func is func
    assert args.app_name == "x"
    assert args.format == "markdown"


def test_register_chat_defaults() -> None:
    args = _parser().parse_args(
        ["arc", "chat", "--app-name", "x", "--message", "hi"]
    )
    assert args.message == "hi"
    assert args.message_file is None
    assert args.attach == []
    assert args.attached_gcs_uri == []
    assert args.confirmation_policy == "manual"
    assert args.client_profile == "console"
    assert args.session_store == arc_cli.DEFAULT_SESSION_STORE


def test_register_requires_subcommand() -> None:
    with pytest.raises(SystemExit):
        _parser().parse_args(["arc"])


def test_main_parser_includes_arc() -> None:
    args = main.get_parser().parse_args(
        ["arc", "chat", "--app-name", "x", "--message", "hi"]
    )
    assert args.func is arc_cli.arc_chat


# ------------------------------------------------------------ sticky store


def test_store_key() -> None:
    assert arc_cli.store_key(TARGET) == KEY


def test_resolve_session_id_generates_and_reuses(store: Path) -> None:
    first = arc_cli.resolve_session_id(TARGET, session_store_path=str(store))
    assert first.startswith(arc_cli.SESSION_ID_PREFIX)
    assert json.loads(store.read_text()) == {KEY: first}

    again = arc_cli.resolve_session_id(TARGET, session_store_path=str(store))
    assert again == first


def test_resolve_session_id_new_session_rotates(store: Path) -> None:
    first = arc_cli.resolve_session_id(TARGET, session_store_path=str(store))
    second = arc_cli.resolve_session_id(
        TARGET, new_session=True, session_store_path=str(store)
    )
    assert second != first
    assert second.startswith(arc_cli.SESSION_ID_PREFIX)
    assert json.loads(store.read_text()) == {KEY: second}


def test_resolve_session_id_is_scoped_per_target(store: Path) -> None:
    first = arc_cli.resolve_session_id(TARGET, session_store_path=str(store))
    other = ArcTarget(
        app_name=APP, api_endpoint="sandbox-ces.sandbox.googleapis.com"
    )
    second = arc_cli.resolve_session_id(other, session_store_path=str(store))
    assert second != first
    assert json.loads(store.read_text()) == {
        KEY: first,
        arc_cli.store_key(other): second,
    }


@pytest.mark.parametrize(
    "explicit",
    ["explicit-1", f"{APP}/assistantSessions/explicit-1"],
)
def test_resolve_session_id_explicit_overrides_and_persists(
    store: Path, explicit: str
) -> None:
    arc_cli.resolve_session_id(TARGET, session_store_path=str(store))
    resolved = arc_cli.resolve_session_id(
        TARGET, explicit_session_id=explicit, session_store_path=str(store)
    )
    assert resolved == "explicit-1"
    assert json.loads(store.read_text()) == {KEY: "explicit-1"}
    # The explicit id is now the sticky one.
    assert (
        arc_cli.resolve_session_id(TARGET, session_store_path=str(store))
        == "explicit-1"
    )


def test_resolve_session_id_honours_url_session(store: Path) -> None:
    target = ArcTarget(
        app_name=APP, api_endpoint=ENDPOINT, session_id_from_url="from-url"
    )
    assert (
        arc_cli.resolve_session_id(target, session_store_path=str(store))
        == "from-url"
    )
    # An explicit id wins over the URL one.
    assert (
        arc_cli.resolve_session_id(
            target, explicit_session_id="x", session_store_path=str(store)
        )
        == "x"
    )


def test_resolve_session_id_expands_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    sid = arc_cli.resolve_session_id(
        TARGET, session_store_path=os.path.join("~", ".cxas", "s.json")
    )
    saved = json.loads((tmp_path / ".cxas" / "s.json").read_text())
    assert saved == {KEY: sid}


def test_resolve_session_id_tolerates_corrupt_store(store: Path) -> None:
    store.write_text("{not json")
    sid = arc_cli.resolve_session_id(TARGET, session_store_path=str(store))
    assert sid.startswith(arc_cli.SESSION_ID_PREFIX)
    assert json.loads(store.read_text()) == {KEY: sid}


# --------------------------------------------------------- pending sidecar


def test_pending_store_path() -> None:
    assert arc_cli.pending_store_path("/x/s.json") == "/x/s.pending.json"
    assert arc_cli.pending_store_path("/x/store") == "/x/store.pending.json"
    assert arc_cli.pending_store_path("") == ""


def test_pending_store_path_expands_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert arc_cli.pending_store_path("~/s.json") == str(
        tmp_path / "s.pending.json"
    )


def test_save_pending_writes_cards_and_drops_options(tmp_path: Path) -> None:
    path = str(tmp_path / "p.json")
    events = _events(
        confirmation_requests=[_gate_card()],
        option_questions=_questions(),
    )
    arc_cli.save_pending_ui_state(path, KEY, SESSION, events)
    state = arc_cli.load_pending_ui_state(path, KEY, SESSION)
    assert state["session_id"] == SESSION
    assert [c["confirmation_id"] for c in state["confirmations"]] == ["conf-1"]
    # Options are only persisted when there is no card.
    assert state["option_questions"] == []


def test_save_pending_writes_options_without_cards(tmp_path: Path) -> None:
    path = str(tmp_path / "p.json")
    arc_cli.save_pending_ui_state(
        path, KEY, SESSION, _events(option_questions=_questions())
    )
    state = arc_cli.load_pending_ui_state(path, KEY, SESSION)
    assert state["confirmations"] == []
    assert [q["question"] for q in state["option_questions"]] == [
        "Which channel?",
        "Escalate?",
    ]


def test_save_pending_removes_entry_when_nothing_pending(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "p.json")
    arc_cli.save_pending_ui_state(
        path, KEY, SESSION, _events(confirmation_requests=[_gate_card()])
    )
    arc_cli.save_pending_ui_state(path, "other-key", "s2", _events())
    assert arc_cli.load_pending_ui_state(path, KEY, SESSION)
    arc_cli.save_pending_ui_state(path, KEY, SESSION, _events())
    assert arc_cli.load_pending_ui_state(path, KEY, SESSION) == {}
    assert json.loads(Path(path).read_text()) == {}


def test_save_pending_clears_answered_cards(tmp_path: Path) -> None:
    path = str(tmp_path / "p.json")
    events = _events(
        confirmation_requests=[_question_card()],
        option_questions=_questions(),
        confirmation_actions=[ConfirmationActionRecord(confirmation_id="q-1")],
    )
    arc_cli.save_pending_ui_state(path, KEY, SESSION, events)
    assert arc_cli.load_pending_ui_state(path, KEY, SESSION) == {}


def test_load_pending_ignores_other_session(tmp_path: Path) -> None:
    path = str(tmp_path / "p.json")
    arc_cli.save_pending_ui_state(
        path, KEY, SESSION, _events(confirmation_requests=[_gate_card()])
    )
    assert arc_cli.load_pending_ui_state(path, KEY, "other") == {}
    assert arc_cli.load_pending_ui_state(path, "other-key", SESSION) == {}


def test_load_pending_tolerates_missing_or_corrupt(tmp_path: Path) -> None:
    missing = str(tmp_path / "missing.json")
    assert arc_cli.load_pending_ui_state(missing, KEY, SESSION) == {}
    corrupt = tmp_path / "bad.json"
    corrupt.write_text("{not-json")
    assert arc_cli.load_pending_ui_state(str(corrupt), KEY, SESSION) == {}
    assert arc_cli.load_pending_ui_state("", KEY, SESSION) == {}


def test_save_pending_empty_path_is_noop() -> None:
    arc_cli.save_pending_ui_state(
        "", KEY, SESSION, _events(confirmation_requests=[_gate_card()])
    )


def test_card_from_dict_round_trips_question_card(tmp_path: Path) -> None:
    path = str(tmp_path / "p.json")
    arc_cli.save_pending_ui_state(
        path, KEY, SESSION, _events(confirmation_requests=[_question_card()])
    )
    state = arc_cli.load_pending_ui_state(path, KEY, SESSION)
    assert arc_cli.card_from_dict(state["confirmations"][0]) == _question_card()


def test_card_from_dict_ignores_unknown_keys() -> None:
    card = arc_cli.card_from_dict(
        {"confirmation_id": "c", "tool": "t", "unexpected": 1}
    )
    assert card == ConfirmationRequestRecord(confirmation_id="c", tool="t")


def _session_with_cards(*statuses: tuple[str, str]) -> dict[str, object]:
    """Session resource with one confirmation_request chunk per status."""
    chunks = [
        {
            "typedPayload": {
                "type": "confirmation_request",
                "value": {
                    "status": status,
                    "request": {
                        "confirmationId": cid,
                        "tool": "update_app",
                        "context": "Publish?",
                        "positiveLabel": "Publish",
                        "negativeLabel": "Not yet",
                        "questions": [
                            {
                                "question": "Which channel?",
                                "options": [
                                    {
                                        "title": "Voice",
                                        "submitText": "Voice only",
                                    }
                                ],
                            }
                        ],
                    },
                },
            }
        }
        for cid, status in statuses
    ]
    return {
        "name": f"{APP}/assistantSessions/{SESSION}",
        "messages": [
            {"role": "user", "chunks": [{"text": "hi"}]},
            {"role": "model", "chunks": [{"typedPayload": {"type": "x"}}]},
            {"role": "model", "chunks": chunks},
        ],
    }


def test_pending_cards_from_session_keeps_only_pending() -> None:
    session = _session_with_cards(
        ("done", "ANSWERED"), ("open", "pending"), ("", "PENDING")
    )
    cards = arc_cli.pending_cards_from_session(session)
    assert [c.confirmation_id for c in cards] == ["open"]
    card = cards[0]
    assert card.tool == "update_app"
    assert card.positive_label == "Publish"
    assert card.questions[0].options[0].submit_text == "Voice only"
    assert arc_cli.pending_cards_from_session({}) == []


def test_pending_cards_from_session_ignores_cards_before_last_message() -> None:
    """The server never flips status, so a later message means it was handled."""
    session = _session_with_cards(("stale", "PENDING"))
    session["messages"].append(
        {"role": "user", "chunks": [{"text": "Confirmed."}]}
    )
    session["messages"].append({"role": "model", "chunks": [{"text": "Done."}]})
    assert arc_cli.pending_cards_from_session(session) == []


def test_describe_typed_payload() -> None:
    assert arc_cli._describe_typed_payload(
        {"type": "tool_call", "displayName": "Update app"}
    ) == ("[tool_call] Update app")
    assert arc_cli._describe_typed_payload(
        {"type": "tool_call", "value": {"tool": "update_app"}}
    ) == ("[tool_call] update_app")
    assert arc_cli._describe_typed_payload(
        {
            "type": "confirmation_request",
            "value": {
                "status": "PENDING",
                "request": {"confirmationId": "c-1", "tool": "update_app"},
            },
        }
    ) == ("[confirmation_request] c-1 tool=update_app status=PENDING")
    assert arc_cli._describe_typed_payload({"type": "plan", "x": 1}) == (
        '[plan] {"type": "plan", "x": 1}'
    )
    assert arc_cli._describe_typed_payload({}).startswith("[payload] ")


# -------------------------------------------------------- answer formatting


def test_format_choices_answer_letters_numbers_and_text() -> None:
    questions = _questions()
    assert arc_cli.format_choices_answer(
        questions, ["A", "Only after hours"]
    ) == ("> Which channel?\nVoice only\n\n> Escalate?\nOnly after hours")
    assert arc_cli.format_choices_answer(questions, ["2", ""]) == (
        f"> Which channel?\nChat\n\n> Escalate?\n{policy_mod.UNANSWERED_MARKER}"
    )
    assert arc_cli.format_choices_answer(questions, ["b"]) == (
        f"> Which channel?\nChat\n\n> Escalate?\n{policy_mod.UNANSWERED_MARKER}"
    )


def test_format_choices_answer_out_of_range_is_free_text() -> None:
    questions = _questions()
    out = arc_cli.format_choices_answer(questions, ["Z", "9"])
    assert out == "> Which channel?\nZ\n\n> Escalate?\n9"


def test_resolve_answer_variants() -> None:
    questions = _questions()
    assert arc_cli._resolve_answer(questions, "raw", ["A"], "all") == "raw"
    assert (
        arc_cli._resolve_answer(questions, None, None, "round")
        == policy_mod.SKIP_ROUND_ANSWER
    )
    assert (
        arc_cli._resolve_answer(questions, None, None, "all")
        == policy_mod.SKIP_ALL_ANSWER
    )
    assert arc_cli._resolve_answer(questions, None, ["A", "x"], None) == (
        "> Which channel?\nVoice only\n\n> Escalate?\nx"
    )
    assert arc_cli._resolve_answer(questions, None, None, None) is None


def test_resolve_answer_choices_without_question_raises() -> None:
    with pytest.raises(ValueError, match="needs a pending question"):
        arc_cli._resolve_answer([], None, ["A"], None)


# -------------------------------------------------------------- followups


def test_cli_base_command_omits_default_endpoint() -> None:
    target = ArcTarget(app_name=APP, api_endpoint=DEFAULT_API_ENDPOINT)
    assert (
        arc_cli.cli_base_command(target, SESSION)
        == f"cxas arc --app-name={APP} --session-id={SESSION}"
    )
    other = ArcTarget(app_name=APP, api_endpoint="other.example.com")
    assert arc_cli.cli_base_command(other, SESSION) == (
        f"cxas arc --app-name={APP} --session-id={SESSION}"
        " --api-endpoint=other.example.com"
    )


def test_suggested_followups_gate_card() -> None:
    events = _events(confirmation_requests=[_gate_card("conf-9")])
    confirm = f"cxas arc confirm --app-name={APP} --session-id={SESSION}"
    chat = f"cxas arc chat --app-name={APP} --session-id={SESSION}"
    assert arc_cli.suggested_followups(events, BASE_CMD) == [
        f"{confirm} --confirmation-id=conf-9 --confirmed",
        f"{confirm} --confirmation-id=conf-9 --declined",
        f'{chat} --message="yes"  # typed reply, as in the console',
    ]


def test_suggested_followups_question_card() -> None:
    events = _events(confirmation_requests=[_question_card("q-42")])
    confirm = f"cxas arc confirm --app-name={APP} --session-id={SESSION}"
    chat = f"cxas arc chat --app-name={APP} --session-id={SESSION}"
    assert arc_cli.suggested_followups(events, BASE_CMD) == [
        f"{confirm} --confirmation-id=q-42 --choices=A,A",
        f'{confirm} --confirmation-id=q-42 --answer-text="..."',
        f"{confirm} --confirmation-id=q-42 --skip=all",
        f'{chat} --message="..."  # typed reply, as in the console',
    ]


def test_suggested_followups_option_panel() -> None:
    events = _events(option_questions=_questions())
    answer = f"cxas arc answer --app-name={APP} --session-id={SESSION}"
    assert arc_cli.suggested_followups(events, BASE_CMD) == [
        f"{answer} --choices=A,A",
        f"{answer} --skip=all",
    ]


def test_suggested_followups_latest_card_wins() -> None:
    events = _events(
        confirmation_requests=[_gate_card("old"), _gate_card("latest")]
    )
    out = arc_cli.suggested_followups(events, BASE_CMD)
    assert all("--confirmation-id=latest" in c for c in out[:2])


def test_suggested_followups_nothing_pending() -> None:
    assert arc_cli.suggested_followups(_events(text="done"), BASE_CMD) == []
    answered = _events(
        confirmation_requests=[_question_card()],
        option_questions=_questions(),
        confirmation_actions=[ConfirmationActionRecord(confirmation_id="q-1")],
    )
    assert arc_cli.suggested_followups(answered, BASE_CMD) == []


# -------------------------------------------------------------- rendering


def test_render_turn_markdown(fake_arc: MagicMock) -> None:
    events = _events(text="Ready.", confirmation_requests=[_gate_card()])
    out = arc_cli.render_turn(
        "chat",
        events,
        TARGET,
        fake_arc,
        SESSION,
        include_thoughts=False,
        include_tools=True,
        response_format="markdown",
        notes=["> Note: hello"],
        base_cmd=BASE_CMD,
    )
    assert out.startswith(
        f"**Session**: `{APP}/assistantSessions/{SESSION}` | "
        f"**Endpoint**: `{ENDPOINT}` | **Profile**: `console`"
    )
    assert "> Note: hello" in out
    assert "Ready." in out
    assert "### [Confirmation Requests]" in out
    assert "### [Next Steps]\n```shell\n" in out
    assert "--confirmation-id=conf-1 --confirmed" in out
    assert out.rstrip().endswith("```")


def test_render_turn_markdown_without_base_cmd_has_no_followups(
    fake_arc: MagicMock,
) -> None:
    out = arc_cli.render_turn(
        "chat",
        _events(confirmation_requests=[_gate_card()]),
        TARGET,
        fake_arc,
        SESSION,
        include_thoughts=False,
        include_tools=True,
        response_format="markdown",
    )
    assert "### [Next Steps]" not in out


def test_render_turn_json(fake_arc: MagicMock) -> None:
    events = _events(text="Ready.", confirmation_requests=[_gate_card()])
    out = arc_cli.render_turn(
        "chat",
        events,
        TARGET,
        fake_arc,
        SESSION,
        include_thoughts=False,
        include_tools=True,
        response_format="json",
        notes=["n1"],
        base_cmd=BASE_CMD,
    )
    payload = json.loads(out)
    assert set(payload) == {
        "action",
        "session_id",
        "canonical_session_id",
        "app_name",
        "api_endpoint",
        "client_profile",
        "events",
        "pending_confirmations",
        "suggested_commands",
        "notes",
        "uploaded_attachments",
        "downloaded_artifacts",
    }
    assert payload["action"] == "chat"
    assert payload["session_id"] == SESSION
    assert payload["canonical_session_id"] == (
        f"{APP}/assistantSessions/{SESSION}"
    )
    assert payload["app_name"] == APP
    assert payload["api_endpoint"] == ENDPOINT
    assert payload["client_profile"] == "console"
    assert payload["events"]["text"] == "Ready."
    assert payload["pending_confirmations"][0]["confirmation_id"] == "conf-1"
    assert len(payload["suggested_commands"]) == 3
    assert payload["notes"] == ["n1"]
    assert payload["uploaded_attachments"] == []
    assert payload["downloaded_artifacts"] == []


def test_format_partial_turn() -> None:
    assert arc_cli.format_partial_turn(None) == ""
    assert arc_cli.format_partial_turn(_events()) == ""
    out = arc_cli.format_partial_turn(_events(text="half"))
    assert out.startswith("### [Turn failed] partial output\n\n")
    assert "half" in out


# ------------------------------------------------------------- arc chat


def test_arc_chat_renders_pending_card_and_persists(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_arc.chat.return_value = _events(
        text="Ready to publish.", confirmation_requests=[_gate_card()]
    )
    _run(store, "chat", "--message=build it")
    out = capsys.readouterr().out
    assert "### [Confirmation Requests]" in out
    assert "### [Next Steps]" in out
    assert "--confirmation-id=conf-1 --confirmed" in out
    assert f"--session-id={SESSION}" in out

    args, kwargs = fake_arc.chat.call_args
    assert args == (SESSION, "build it")
    assert kwargs["attached_gcs_uris"] == []
    assert kwargs["max_confirmation_rounds"] == 5
    assert kwargs["auto_resolve_lro"] is True
    assert kwargs["confirmation_policy"] is not None

    state = _pending(store)
    assert state["confirmations"][0]["confirmation_id"] == "conf-1"
    assert json.loads(store.read_text()) == {KEY: SESSION}


def test_arc_chat_json_output(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_arc.chat.return_value = _events(
        confirmation_requests=[_gate_card("conf-json")]
    )
    _run(store, "chat", "--message=hi", "--format=json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "chat"
    assert payload["pending_confirmations"][0]["confirmation_id"] == (
        "conf-json"
    )
    assert len(payload["suggested_commands"]) == 3


def test_arc_chat_notes_typed_reply_when_card_pending(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_pending(store, _events(confirmation_requests=[_gate_card()]))
    _run(store, "chat", "--message=yes")
    out = capsys.readouterr().out
    assert "> Note: a confirmation card is pending" in out
    assert "typed reply" in out
    # The turn produced no new card, so the sidecar entry is cleared.
    assert _pending(store) == {}


def test_arc_chat_message_file(
    store: Path,
    tmp_path: Path,
    fake_arc: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    msg = tmp_path / "msg.txt"
    msg.write_text("  from file \n")
    _run(store, "chat", f"--message-file={msg}")
    assert fake_arc.chat.call_args.args == (SESSION, "from file")
    assert "**Session**" in capsys.readouterr().out


def test_arc_chat_rejects_message_and_file(
    store: Path,
    tmp_path: Path,
    fake_arc: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    msg = tmp_path / "msg.txt"
    msg.write_text("x")
    with pytest.raises(SystemExit) as exc:
        _run(store, "chat", "--message=hi", f"--message-file={msg}")
    assert exc.value.code == 1
    assert "Error: Provide either --message or --message-file" in (
        capsys.readouterr().err
    )
    fake_arc.chat.assert_not_called()


def test_arc_chat_requires_message_or_attachment(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        _run(store, "chat")
    assert exc.value.code == 1
    assert "Error: --message, --message-file or --attach is required." in (
        capsys.readouterr().err
    )
    fake_arc.chat.assert_not_called()


def test_arc_chat_invalid_target_exits(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _parser().parse_args(
        ["arc", "chat", "--app-name=short", "--message=hi"]
    )
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    assert "Error: --project is required" in capsys.readouterr().err


def test_arc_chat_stream_timeout_prints_partial_and_persists(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    partial = _events(
        text="Partial progress.",
        confirmation_requests=[_gate_card("conf-partial")],
    )
    fake_arc.chat.side_effect = ArcStreamTimeout("stream idle", partial)
    with pytest.raises(SystemExit) as exc:
        _run(store, "chat", "--message=build it")
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "### [Turn failed] partial output" in captured.out
    assert "Partial progress." in captured.out
    assert "Error: stream idle" in captured.err
    assert _pending(store)["confirmations"][0]["confirmation_id"] == (
        "conf-partial"
    )


def test_arc_chat_generic_failure_uses_last_stream_events(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_arc.last_stream_events = _events(
        text="Before the crash.",
        confirmation_requests=[_gate_card("conf-crash")],
    )
    fake_arc.chat.side_effect = RuntimeError("STREAM_BROKEN")
    with pytest.raises(SystemExit) as exc:
        _run(store, "chat", "--message=build it")
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "Before the crash." in captured.out
    assert "Error: STREAM_BROKEN" in captured.err
    assert _pending(store)["confirmations"][0]["confirmation_id"] == (
        "conf-crash"
    )


def test_arc_chat_attach_dry_run(
    store: Path,
    tmp_path: Path,
    fake_arc: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    src = tmp_path / "bundle"
    src.mkdir()
    (src / "notes.md").write_text("# hi\n")
    (src / "agent.yaml").write_text("name: a\n")
    _run(store, "chat", f"--attach={src}", "--attach-dry-run")
    out = capsys.readouterr().out
    assert "### [Attachment Bundle]" in out
    assert "notes.md" in out
    assert "agent.yaml" in out
    fake_arc.chat.assert_not_called()


def test_arc_chat_attached_gcs_uri_passthrough(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(store, "chat", "--attached-gcs-uri=gs://b/o.zip", "--message=see")
    assert fake_arc.chat.call_args.kwargs["attached_gcs_uris"] == [
        "gs://b/o.zip"
    ]
    assert "**Session**" in capsys.readouterr().out


def test_arc_chat_download_artifacts(
    store: Path,
    tmp_path: Path,
    fake_arc: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dest = tmp_path / "out"
    fake_arc.session_artifacts.return_value = [
        {"gcsUri": "gs://b/report.html", "displayName": "report.html"},
        {"id": "inline-only"},
        {"gcsUri": "gs://b/broken.bin", "id": "broken"},
    ]

    def fake_download(
        uri: str,
        dest_dir: str,
        *,
        credentials: object,
        project: str | None = None,
    ) -> str:
        assert credentials is fake_arc.creds
        assert project == "p"
        if uri.endswith("broken.bin"):
            raise OSError("boom")
        return os.path.join(dest_dir, uri.rsplit("/", 1)[-1])

    monkeypatch.setattr(
        "cxas_scrapi.utils.arc_bundle.download_gcs_uri", fake_download
    )
    _run(store, "chat", "--message=hi", f"--download-artifacts={dest}")
    out = capsys.readouterr().out
    fake_arc.session_artifacts.assert_called_once_with(SESSION)
    assert "### [Downloaded Artifacts]" in out
    assert f"- `report.html` -> `{dest / 'report.html'}`" in out
    assert "- `broken` -> FAILED: boom" in out
    assert "inline-only" not in out


def test_download_session_artifacts_none(fake_arc: MagicMock) -> None:
    out = arc_cli.download_session_artifacts(fake_arc, SESSION, "/dest")
    assert out == "### [Downloaded Artifacts]\n(none)"


# ---------------------------------------------------------- arc confirm


def _confirm_kwargs(fake_arc: MagicMock) -> dict[str, object]:
    return dict(fake_arc.send_confirmation_response.call_args.kwargs)


def test_arc_confirm_confirmed_uses_latest_card(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_pending(
        store,
        _events(confirmation_requests=[_gate_card("old"), _gate_card("new")]),
    )
    fake_arc.send_confirmation_response.return_value = _events(
        text="Published."
    )
    _run(store, "confirm", "--confirmed")
    args = fake_arc.send_confirmation_response.call_args.args
    kwargs = _confirm_kwargs(fake_arc)
    assert args == (SESSION, "new")
    assert kwargs["confirmed"] is True
    assert kwargs["answer_text"] is None
    assert kwargs["tool"] == "update_app"
    assert "Published." in capsys.readouterr().out
    # Nothing new is pending, so the sidecar entry is gone.
    assert _pending(store) == {}


def test_arc_confirm_declined(store: Path, fake_arc: MagicMock) -> None:
    _seed_pending(store, _events(confirmation_requests=[_gate_card()]))
    _run(store, "confirm", "--declined")
    kwargs = _confirm_kwargs(fake_arc)
    assert kwargs["confirmed"] is False
    assert kwargs["answer_text"] is None
    assert kwargs["tool"] == "update_app"


def test_arc_confirm_choices_formats_like_console(
    store: Path, fake_arc: MagicMock
) -> None:
    card = _question_card(
        "q-1",
        [
            OptionQuestionRecord(
                question="q",
                options=[
                    OptionChoiceRecord(title="First"),
                    OptionChoiceRecord(title="Second"),
                ],
            )
        ],
    )
    _seed_pending(store, _events(confirmation_requests=[card]))
    _run(store, "confirm", "--choices=B")
    kwargs = _confirm_kwargs(fake_arc)
    assert fake_arc.send_confirmation_response.call_args.args == (
        SESSION,
        "q-1",
    )
    assert kwargs["answer_text"] == "> q\nSecond"
    assert kwargs["confirmed"] is None
    assert kwargs["tool"] == "ask_user"


def test_arc_confirm_multi_question_choices(
    store: Path, fake_arc: MagicMock
) -> None:
    _seed_pending(store, _events(confirmation_requests=[_question_card()]))
    _run(store, "confirm", "--choices=A,Only after hours")
    assert _confirm_kwargs(fake_arc)["answer_text"] == (
        "> Which channel?\nVoice only\n\n> Escalate?\nOnly after hours"
    )


def test_arc_confirm_answer_text(store: Path, fake_arc: MagicMock) -> None:
    _seed_pending(store, _events(confirmation_requests=[_question_card()]))
    _run(store, "confirm", "--answer-text=Custom answer")
    kwargs = _confirm_kwargs(fake_arc)
    assert kwargs["answer_text"] == "Custom answer"
    assert kwargs["confirmed"] is None


@pytest.mark.parametrize(
    ("skip", "expected"),
    [
        ("round", policy_mod.SKIP_ROUND_ANSWER),
        ("all", policy_mod.SKIP_ALL_ANSWER),
    ],
)
def test_arc_confirm_skip(
    store: Path, fake_arc: MagicMock, skip: str, expected: str
) -> None:
    _seed_pending(store, _events(confirmation_requests=[_question_card()]))
    _run(store, "confirm", f"--skip={skip}")
    assert _confirm_kwargs(fake_arc)["answer_text"] == expected


def test_arc_confirm_explicit_id_selects_matching_card(
    store: Path, fake_arc: MagicMock
) -> None:
    _seed_pending(
        store,
        _events(
            confirmation_requests=[
                _question_card("q-earlier"),
                _gate_card("latest"),
            ]
        ),
    )
    _run(store, "confirm", "--confirmation-id=q-earlier", "--choices=A,")
    kwargs = _confirm_kwargs(fake_arc)
    assert fake_arc.send_confirmation_response.call_args.args == (
        SESSION,
        "q-earlier",
    )
    assert kwargs["tool"] == "ask_user"
    assert kwargs["answer_text"] == (
        "> Which channel?\nVoice only\n\n> Escalate?\n"
        f"{policy_mod.UNANSWERED_MARKER}"
    )


def test_arc_confirm_unknown_id_still_sends(
    store: Path, fake_arc: MagicMock
) -> None:
    _run(store, "confirm", "--confirmation-id=unknown-id", "--skip=all")
    kwargs = _confirm_kwargs(fake_arc)
    assert fake_arc.send_confirmation_response.call_args.args == (
        SESSION,
        "unknown-id",
    )
    assert kwargs["tool"] == ""
    assert kwargs["answer_text"] == policy_mod.SKIP_ALL_ANSWER


def test_arc_confirm_unknown_id_confirmed(
    store: Path, fake_arc: MagicMock
) -> None:
    _run(store, "confirm", "--confirmation-id=unknown-id", "--confirmed")
    kwargs = _confirm_kwargs(fake_arc)
    assert kwargs["confirmed"] is True
    assert kwargs["answer_text"] is None
    assert kwargs["tool"] == ""


@pytest.mark.parametrize(
    "flags",
    [
        [],
        ["--confirmed", "--declined"],
        ["--confirmed", "--answer-text=x"],
        ["--choices=A", "--skip=all"],
    ],
)
def test_arc_confirm_requires_exactly_one_answer_flag(
    store: Path,
    fake_arc: MagicMock,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
) -> None:
    _seed_pending(store, _events(confirmation_requests=[_gate_card()]))
    with pytest.raises(SystemExit) as exc:
        _run(store, "confirm", *flags)
    assert exc.value.code == 1
    assert "Error: cxas arc confirm needs exactly one of" in (
        capsys.readouterr().err
    )
    fake_arc.send_confirmation_response.assert_not_called()
    # The pending card survives a validation error.
    assert _pending(store)["confirmations"][0]["confirmation_id"] == "conf-1"


def test_arc_confirm_without_pending_card_exits(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        _run(store, "confirm", "--confirmed")
    assert exc.value.code == 1
    assert "Error: No pending confirmation card is recorded" in (
        capsys.readouterr().err
    )
    # The session itself was consulted before giving up.
    fake_arc.get_session.assert_called_once_with(SESSION)
    fake_arc.send_confirmation_response.assert_not_called()


def test_arc_confirm_recovers_card_from_session(
    store: Path, fake_arc: MagicMock
) -> None:
    fake_arc.get_session.return_value = _session_with_cards(
        ("answered", "ANSWERED"), ("srv-1", "PENDING")
    )
    _run(store, "confirm", "--confirmed")
    fake_arc.get_session.assert_called_once_with(SESSION)
    assert fake_arc.send_confirmation_response.call_args.args == (
        SESSION,
        "srv-1",
    )
    assert _confirm_kwargs(fake_arc)["tool"] == "update_app"


def test_arc_confirm_does_not_consult_session_when_card_known(
    store: Path, fake_arc: MagicMock
) -> None:
    _seed_pending(store, _events(confirmation_requests=[_gate_card()]))
    _run(store, "confirm", "--confirmed")
    fake_arc.get_session.assert_not_called()
    _run(store, "confirm", "--confirmation-id=explicit", "--declined")
    fake_arc.get_session.assert_not_called()


def test_arc_confirm_session_lookup_failure_exits(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_arc.get_session.side_effect = ArcApiError(404, "gone")
    with pytest.raises(SystemExit) as exc:
        _run(store, "confirm", "--confirmed")
    assert exc.value.code == 1
    assert "Error: HTTP 404: gone" in capsys.readouterr().err
    fake_arc.send_confirmation_response.assert_not_called()


def test_arc_confirm_choices_on_gate_card_exits(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_pending(store, _events(confirmation_requests=[_gate_card()]))
    with pytest.raises(SystemExit) as exc:
        _run(store, "confirm", "--choices=A")
    assert exc.value.code == 1
    assert "Error: --choices needs a pending question" in (
        capsys.readouterr().err
    )
    fake_arc.send_confirmation_response.assert_not_called()


def test_arc_confirm_persists_new_card_from_response(
    store: Path, fake_arc: MagicMock
) -> None:
    _seed_pending(store, _events(confirmation_requests=[_gate_card("first")]))
    fake_arc.send_confirmation_response.return_value = _events(
        confirmation_requests=[_gate_card("second")]
    )
    _run(store, "confirm", "--confirmed")
    assert [c["confirmation_id"] for c in _pending(store)["confirmations"]] == [
        "second"
    ]


def test_arc_confirm_stream_failure_persists_partial(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_pending(store, _events(confirmation_requests=[_gate_card("first")]))
    partial = _events(confirmation_requests=[_gate_card("retry-me")])
    fake_arc.send_confirmation_response.side_effect = ArcStreamTimeout(
        "idle", partial
    )
    with pytest.raises(SystemExit) as exc:
        _run(store, "confirm", "--confirmed")
    assert exc.value.code == 1
    assert "Error: idle" in capsys.readouterr().err
    assert _pending(store)["confirmations"][0]["confirmation_id"] == (
        "retry-me"
    )


# ----------------------------------------------------------- arc answer


def test_arc_answer_sends_formatted_choices(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_pending(store, _events(option_questions=_questions()))
    _run(store, "answer", "--choices=2,")
    assert fake_arc.chat.call_args.args == (
        SESSION,
        f"> Which channel?\nChat\n\n> Escalate?\n{policy_mod.UNANSWERED_MARKER}",
    )
    assert "attached_gcs_uris" not in fake_arc.chat.call_args.kwargs
    assert "**Session**" in capsys.readouterr().out
    assert _pending(store) == {}


def test_arc_answer_skip_all(store: Path, fake_arc: MagicMock) -> None:
    _seed_pending(store, _events(option_questions=_questions()))
    _run(store, "answer", "--skip=all")
    assert fake_arc.chat.call_args.args == (
        SESSION,
        policy_mod.SKIP_ALL_ANSWER,
    )


def test_arc_answer_without_panel_exits(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        _run(store, "answer", "--choices=A")
    assert exc.value.code == 1
    assert "Error: No option panel is recorded" in capsys.readouterr().err
    fake_arc.chat.assert_not_called()


def test_arc_answer_card_is_not_a_panel(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    # A question card is answered with `confirm`, not `answer`.
    _seed_pending(store, _events(confirmation_requests=[_question_card()]))
    with pytest.raises(SystemExit):
        _run(store, "answer", "--choices=A")
    assert "Error: No option panel is recorded" in capsys.readouterr().err
    fake_arc.chat.assert_not_called()


@pytest.mark.parametrize("flags", [[], ["--choices=A", "--skip=all"]])
def test_arc_answer_requires_exactly_one_flag(
    store: Path,
    fake_arc: MagicMock,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
) -> None:
    _seed_pending(store, _events(option_questions=_questions()))
    with pytest.raises(SystemExit) as exc:
        _run(store, "answer", *flags)
    assert exc.value.code == 1
    assert "Error: cxas arc answer needs exactly one of" in (
        capsys.readouterr().err
    )
    fake_arc.chat.assert_not_called()


# --------------------------------------------------------------- sessions


def test_arc_sessions_new_rotates_and_prints(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    old = arc_cli.resolve_session_id(TARGET, session_store_path=str(store))
    _seed_pending(store, _events(confirmation_requests=[_gate_card()]))
    args = _parser().parse_args(
        [
            "arc",
            "sessions",
            "new",
            f"--app-name={APP}",
            f"--api-endpoint={ENDPOINT}",
            f"--session-store={store}",
        ]
    )
    args.func(args)
    out = capsys.readouterr().out
    new = json.loads(store.read_text())[KEY]
    assert new != old
    assert new.startswith(arc_cli.SESSION_ID_PREFIX)
    assert f"New sticky session: `{APP}/assistantSessions/{new}`" in out
    assert f"Next: cxas arc chat --app-name={APP} --session-id={new}" in out
    assert '--message="..."' in out


def test_arc_sessions_new_json(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(store, "sessions", "new", "--format=json")
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "action": "sessions.new",
        "session_id": SESSION,
        "canonical_session_id": f"{APP}/assistantSessions/{SESSION}",
        "app_name": APP,
        "api_endpoint": ENDPOINT,
    }


def test_arc_sessions_list_markdown_and_json(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    sessions = [
        {
            "name": f"{APP}/assistantSessions/s-a",
            "state": "ACTIVE",
            "messages": [{}, {}],
            "updateTime": "2026-10-27T00:00:00Z",
            "requestor": "me@example.com",
        },
        {"name": f"{APP}/assistantSessions/s-b"},
    ]
    fake_arc.list_sessions.return_value = sessions
    _run(store, "sessions", "list", "--page-size=5", "--filter=state=ACTIVE")
    out = capsys.readouterr().out
    fake_arc.list_sessions.assert_called_once_with(
        page_size=5, filter_="state=ACTIVE"
    )
    assert (
        "- `s-a` state=ACTIVE messages=2 updated=2026-10-27T00:00:00Z "
        "requestor=me@example.com"
    ) in out
    assert "- `s-b` state= messages=0 updated= requestor=" in out

    _run(store, "sessions", "list", "--format=json")
    assert json.loads(capsys.readouterr().out) == sessions


def test_arc_sessions_list_empty_and_error(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(store, "sessions", "list")
    assert capsys.readouterr().out.strip() == "(no assistant sessions)"

    fake_arc.list_sessions.side_effect = ArcApiError(403, "denied")
    with pytest.raises(SystemExit) as exc:
        _run(store, "sessions", "list")
    assert exc.value.code == 1
    assert "Error: HTTP 403: denied" in capsys.readouterr().err


def test_arc_sessions_get_renders_messages_and_artifacts(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    session = {
        "name": f"{APP}/assistantSessions/{SESSION}",
        "state": "ACTIVE",
        "updateTime": "2026-10-27T00:00:00Z",
        "activeTurn": {"turnId": "t-1"},
        "messages": [
            {"role": "user", "chunks": [{"text": "hello\nworld"}]},
            {
                "role": "assistant",
                "chunks": [{"typedPayload": {"type": "plan", "x": 1}}],
            },
        ],
        "artifacts": [
            {
                "id": "art-1",
                "displayName": "report.html",
                "mimeType": "text/html",
                "gcsUri": "gs://b/report.html",
            },
            {"id": "art-2"},
        ],
    }
    fake_arc.get_session.return_value = session
    _run(store, "sessions", "get")
    out = capsys.readouterr().out
    fake_arc.get_session.assert_called_once_with(SESSION)
    assert f"**Session**: `{APP}/assistantSessions/{SESSION}`" in out
    assert "state=ACTIVE messages=2 updated=2026-10-27T00:00:00Z" in out
    assert 'active turn: {"turnId": "t-1"}' in out
    assert "- **user**: hello\n  world" in out
    assert '- **assistant** [plan] {"type": "plan", "x": 1}' in out
    assert "### [Artifacts]" in out
    assert "- `report.html` text/html gs://b/report.html" in out
    assert "- `art-2`  " in out

    _run(store, "sessions", "get", "--format=json")
    assert json.loads(capsys.readouterr().out) == session


def test_arc_sessions_get_error_exits(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_arc.get_session.side_effect = ArcApiError(404, "no such session")
    with pytest.raises(SystemExit) as exc:
        _run(store, "sessions", "get")
    assert exc.value.code == 1
    assert "Error: HTTP 404: no such session" in capsys.readouterr().err


def test_arc_sessions_delete_forgets_local_state(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    arc_cli.resolve_session_id(
        TARGET, explicit_session_id=SESSION, session_store_path=str(store)
    )
    other = ArcTarget(app_name=APP, api_endpoint="other.example.com")
    other_sid = arc_cli.resolve_session_id(other, session_store_path=str(store))
    _seed_pending(store, _events(confirmation_requests=[_gate_card()]))
    _run(store, "sessions", "delete")
    out = capsys.readouterr().out
    fake_arc.delete_session.assert_called_once_with(SESSION)
    assert f"Deleted `{APP}/assistantSessions/{SESSION}`" in out
    # Only this target's entry is removed; the sidecar is cleared too.
    assert json.loads(store.read_text()) == {
        arc_cli.store_key(other): other_sid
    }
    assert _pending(store) == {}


def test_arc_sessions_delete_keeps_store_when_id_differs(
    store: Path, fake_arc: MagicMock
) -> None:
    sticky = arc_cli.resolve_session_id(TARGET, session_store_path=str(store))
    _seed_pending(store, _events(confirmation_requests=[_gate_card()]))
    # Seed pending state under `sticky` and verify deleting `someone-else`
    # keeps both the sticky pointer and `sticky`'s pending card intact.
    pending_path = arc_cli.pending_store_path(str(store))
    arc_cli.save_pending_ui_state(
        pending_path, KEY, sticky, _events(confirmation_requests=[_gate_card()])
    )
    args = _parser().parse_args(
        [
            "arc",
            "sessions",
            "delete",
            f"--app-name={APP}",
            f"--api-endpoint={ENDPOINT}",
            f"--session-store={store}",
            "--session-id=someone-else",
        ]
    )
    args.func(args)
    fake_arc.delete_session.assert_called_once_with("someone-else")
    assert json.loads(store.read_text()) == {KEY: sticky}
    assert (
        arc_cli.load_pending_ui_state(pending_path, KEY, sticky)[
            "confirmations"
        ][0]["confirmation_id"]
        == "conf-1"
    )


def test_arc_sessions_delete_error_exits(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    arc_cli.resolve_session_id(
        TARGET, explicit_session_id=SESSION, session_store_path=str(store)
    )
    fake_arc.delete_session.side_effect = ArcApiError(500, "boom")
    with pytest.raises(SystemExit) as exc:
        _run(store, "sessions", "delete")
    assert exc.value.code == 1
    assert "Error: HTTP 500: boom" in capsys.readouterr().err
    assert json.loads(store.read_text()) == {KEY: SESSION}


# ------------------------------------------------------ cancel/suggestions


def test_arc_cancel_markdown_and_json(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(store, "cancel", "--turn-id=t-7")
    fake_arc.cancel_turn.assert_called_once_with(SESSION, "t-7")
    assert capsys.readouterr().out.strip() == "cancelled: True"

    fake_arc.cancel_turn.return_value = {}
    _run(store, "cancel", "--format=json")
    assert json.loads(capsys.readouterr().out) == {
        "action": "cancel",
        "session_id": SESSION,
        "cancelled": False,
    }
    assert fake_arc.cancel_turn.call_args.args == (SESSION, "")


def test_arc_cancel_error_exits(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_arc.cancel_turn.side_effect = ArcApiError(409, "no active turn")
    with pytest.raises(SystemExit) as exc:
        _run(store, "cancel")
    assert exc.value.code == 1
    assert "Error: HTTP 409: no active turn" in capsys.readouterr().err


def test_arc_suggestions_renders(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    suggestions = [
        {
            "suggestionId": "s1",
            "label": "Build",
            "seedPrompt": "Build an agent",
            "rationale": "Quickstart",
        },
        {"label": "Eval", "sendMessage": "Run an evaluation"},
        {
            "label": "Resume",
            "loadSession": {"assistantSessionId": "prev-123"},
        },
        {"label": "Label only"},
        {"text": "Just text"},
        {"other": 1},
    ]
    fake_arc.generate_onboarding_suggestions.return_value = suggestions
    _run(store, "suggestions")
    out = capsys.readouterr().out
    assert "- Build: Build an agent (Quickstart)" in out
    assert "- Eval: Run an evaluation" in out
    assert "- Resume: session prev-123" in out
    assert "- Label only" in out
    assert "- Just text" in out
    assert '- {"other": 1}' in out

    _run(store, "suggestions", "--format=json")
    assert json.loads(capsys.readouterr().out) == suggestions


def test_arc_suggestions_empty(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(store, "suggestions")
    assert capsys.readouterr().out.strip() == "(no suggestions)"


def test_arc_suggestions_api_error_exits(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_arc.generate_onboarding_suggestions.side_effect = ArcApiError(
        403, "permission denied"
    )
    with pytest.raises(SystemExit) as exc:
        _run(store, "suggestions")
    assert exc.value.code == 1
    assert "Error: HTTP 403: permission denied" in capsys.readouterr().err


def test_build_client_forwards_profile_and_timeout(
    store: Path, fake_arc: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(
        store,
        "chat",
        "--message=hi",
        "--client-profile=legacy",
        "--stream-idle-timeout=5",
        "--quiet",
    )
    kwargs = arc_mod.Arc.call_args.kwargs  # type: ignore[attr-defined]
    assert kwargs["app_name"] == APP
    assert kwargs["api_endpoint"] == ENDPOINT
    assert kwargs["client_profile"] == "legacy"
    assert kwargs["stream_idle_timeout"] == 5.0
    assert kwargs["progress"] is None


def test_is_empty_turn() -> None:
    empty = TurnStreamEvents(
        turn_completed=TurnCompletedRecord(reason="COMPLETED")
    )
    assert arc_cli.is_empty_turn(empty)
    assert not arc_cli.is_empty_turn(TurnStreamEvents(text="hi"))
    assert not arc_cli.is_empty_turn(
        TurnStreamEvents(confirmation_requests=[_gate_card("c-1")])
    )


def test_arc_turn_empty_exits_3_across_chat_confirm_and_answer(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = TurnStreamEvents(turn_completed=TurnCompletedRecord(reason=""))
    fake_arc.chat.return_value = empty
    with pytest.raises(SystemExit) as excinfo:
        _run(store, "chat", "--message", "hi")
    assert excinfo.value.code == arc_cli.EXIT_EMPTY_TURN == 3
    err = capsys.readouterr().err
    assert "without any output" in err
    assert "cxas apps get" in err

    _seed_pending(store, _events(confirmation_requests=[_gate_card()]))
    fake_arc.send_confirmation_response.return_value = empty
    with pytest.raises(SystemExit) as excinfo2:
        _run(store, "confirm", "--confirmed")
    assert excinfo2.value.code == 3
    capsys.readouterr()

    _seed_pending(store, _events(option_questions=_questions()))
    with pytest.raises(SystemExit) as excinfo3:
        _run(store, "answer", "--skip=all")
    assert excinfo3.value.code == 3


def test_download_session_artifacts_skips_managed_uris(
    fake_arc: MagicMock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_arc.session_artifacts.return_value = [
        {"displayName": "notes.md", "gcsUri": "ces-artifact://projects/p/x"},
        {"displayName": "plan.md", "gcsUri": "gs://b/plan.md"},
    ]
    monkeypatch.setattr(
        "cxas_scrapi.utils.arc_bundle.download_gcs_uri",
        lambda uri, dest, **_: str(tmp_path / "plan.md"),
    )
    out = arc_cli.download_session_artifacts(fake_arc, "s-1", str(tmp_path))
    assert "`notes.md` -> not downloadable here (managed artifact" in out
    assert "`plan.md` -> `" in out


# ------------------------------------------- review regression & E2E tests


def test_arc_chat_attach_upload_directory_and_secret_exclusion(
    store: Path,
    tmp_path: Path,
    fake_arc: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    src = tmp_path / "workspace"
    src.mkdir()
    (src / "agent.yaml").write_text("name: demo\n")
    (src / ".env").write_text("API_KEY=secret\n")

    uploaded_archives: list[tuple[list[str], str, str | None]] = []

    def fake_resolve_project(project: str, *, credentials: object) -> str:
        assert project == "p"
        assert credentials is fake_arc.creds
        return "resolved-proj"

    def fake_upload(
        local_path: str,
        bucket: str,
        *,
        credentials: object,
        project: str | None = None,
    ) -> str:
        assert credentials is fake_arc.creds
        with zipfile.ZipFile(local_path) as zf:
            uploaded_archives.append((sorted(zf.namelist()), bucket, project))
        return f"gs://{bucket}/agent-assist-uploads/arc_bundle_test.zip"

    monkeypatch.setattr(
        "cxas_scrapi.utils.arc_bundle.resolve_project_id", fake_resolve_project
    )
    monkeypatch.setattr(
        "cxas_scrapi.utils.arc_bundle.upload_bundle", fake_upload
    )
    fake_arc.chat.return_value = _events(text="Reviewed.")

    # Default excludes `.env` and reports secret count on stderr before upload.
    _run(store, "chat", f"--attach={src}", "--format=json")
    captured = capsys.readouterr()
    assert "uploading 1 file(s)" in captured.err
    assert "1 secret file(s) skipped" in captured.err
    assert "### [Attachment Bundle]" in captured.err
    payload = json.loads(captured.out)
    expected_uri = (
        "gs://resolved-proj-agent-assist-artifacts/"
        "agent-assist-uploads/arc_bundle_test.zip"
    )
    assert payload["uploaded_attachments"] == [expected_uri]
    assert fake_arc.chat.call_args.args == (
        SESSION,
        "Attached files for you to review.",
    )
    assert fake_arc.chat.call_args.kwargs["attached_gcs_uris"] == [expected_uri]
    assert uploaded_archives[0] == (
        ["agent.yaml", "arc_bundle.json"],
        "resolved-proj-agent-assist-artifacts",
        "p",
    )

    # `--attach-allow-secrets` explicitly includes `.env`.
    uploaded_archives.clear()
    _run(
        store,
        "chat",
        f"--attach={src}",
        "--attach-allow-secrets",
        "--message=check env",
    )
    capsys.readouterr()
    assert uploaded_archives[0][0] == [".env", "agent.yaml", "arc_bundle.json"]


def test_arc_chat_attach_upload_prebuilt_zip_and_error_paths(
    store: Path,
    tmp_path: Path,
    fake_arc: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prebuilt = tmp_path / "export.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("flows/default.json", '{"start": true}')
    prebuilt.write_bytes(buf.getvalue())

    uploaded_bytes: list[bytes] = []

    def fake_upload(
        local_path: str,
        bucket: str,
        *,
        credentials: object,
        project: str | None = None,
    ) -> str:
        uploaded_bytes.append(Path(local_path).read_bytes())
        return "gs://custom-bucket/sub/arc_bundle_export.zip"

    monkeypatch.setattr(
        "cxas_scrapi.utils.arc_bundle.upload_bundle", fake_upload
    )
    fake_arc.chat.return_value = _events(text="Imported.")

    _run(
        store,
        "chat",
        f"--attach={prebuilt}",
        "--bucket=gs://custom-bucket/sub",
    )
    out = capsys.readouterr()
    assert "Imported." in out.out
    assert uploaded_bytes == [prebuilt.read_bytes()]
    assert fake_arc.chat.call_args.kwargs["attached_gcs_uris"] == [
        "gs://custom-bucket/sub/arc_bundle_export.zip"
    ]

    # Upload failure surfaces cleanly via _fail instead of a raw traceback.
    monkeypatch.setattr(
        "cxas_scrapi.utils.arc_bundle.upload_bundle",
        MagicMock(side_effect=OSError("bucket forbidden")),
    )
    with pytest.raises(SystemExit) as exc1:
        _run(store, "chat", f"--attach={prebuilt}", "--bucket=b")
    assert exc1.value.code == 1
    assert "Failed to upload attachment bundle: bucket forbidden" in (
        capsys.readouterr().err
    )

    # Project resolution failure without --bucket surfaces cleanly.
    monkeypatch.setattr(
        "cxas_scrapi.utils.arc_bundle.resolve_project_id",
        MagicMock(side_effect=RuntimeError("pass --bucket")),
    )
    with pytest.raises(SystemExit) as exc2:
        _run(store, "chat", f"--attach={prebuilt}")
    assert exc2.value.code == 1
    assert "Error: pass --bucket" in capsys.readouterr().err

    # Unsafe zip member is rejected as BundleError -> exit 1.
    unsafe = tmp_path / "unsafe.zip"
    bad_buf = io.BytesIO()
    with zipfile.ZipFile(bad_buf, "w") as zf:
        zf.writestr("../escape.txt", "nope")
    unsafe.write_bytes(bad_buf.getvalue())
    with pytest.raises(SystemExit) as exc3:
        _run(store, "chat", f"--attach={unsafe}")
    assert exc3.value.code == 1
    assert "Unsafe path inside zip archive" in capsys.readouterr().err


def test_arc_chat_attach_dry_run_json_does_not_mutate_store(
    store: Path,
    tmp_path: Path,
    fake_arc: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    src = tmp_path / "bundle_dir"
    src.mkdir()
    (src / "readme.md").write_text("hello\n")
    (src / ".env.local").write_text("SECRET=1\n")

    args = _parser().parse_args(
        [
            "arc",
            "chat",
            f"--app-name={APP}",
            f"--session-store={store}",
            f"--attach={src}",
            "--attach-dry-run",
            "--format=json",
        ]
    )
    args.func(args)

    assert not store.exists()
    fake_arc.chat.assert_not_called()
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "chat.attach_dry_run"
    assert payload["root"] == "bundle_dir"
    assert payload["file_count"] == 1
    assert payload["secret_excluded_count"] == 1
    assert payload["files"] == [{"path": "readme.md", "size": 6}]


def test_arc_turn_streamed_error_status_renders_and_exits_1(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    err_turn = _events(
        errors=[StatusRecord(code=7, message="PERMISSION_DENIED on tool")],
        turn_completed=TurnCompletedRecord(reason="ERROR"),
    )
    fake_arc.chat.return_value = err_turn
    with pytest.raises(SystemExit) as exc:
        _run(store, "chat", "--message=hi")
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "### [Errors]" in captured.out
    assert "- `code=7`: PERMISSION_DENIED on tool" in captured.out
    assert "error status 7: PERMISSION_DENIED on tool" in captured.err
    assert "cxas apps get" not in captured.err


def test_arc_turn_forwards_non_default_flags(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_arc.chat.return_value = _events(
        text="Done.",
        thoughts=["secret reasoning"],
        tool_calls=[ToolCallRecord(id="c1", tool="update_app", args={})],
    )
    _run(
        store,
        "chat",
        "--message=go",
        "--confirmation-policy=click",
        "--deny-tools=*delete*",
        "--no-auto-resolve-lro",
        "--timeout-seconds=3600",
        "--max-confirmation-rounds=2",
        "--include-thoughts",
        "--no-tools",
    )
    out = capsys.readouterr().out
    assert "### [Thoughts]" in out
    assert "secret reasoning" in out
    assert "### [Tool Activity]" not in out

    kwargs = fake_arc.chat.call_args.kwargs
    assert kwargs["timeout_seconds"] == 3600.0
    assert kwargs["lro_timeout_seconds"] == 3600.0
    assert kwargs["auto_resolve_lro"] is False
    assert kwargs["max_confirmation_rounds"] == 2
    policy = kwargs["confirmation_policy"]
    assert isinstance(policy, policy_mod.ClickPolicy)
    decisions = policy.decide([_gate_card("c-del", tool="delete_agent")])
    assert decisions[0].confirmed is False


def test_handle_turn_failure_preserves_pending_on_zero_events_and_renders_json(
    store: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_pending(store, _events(confirmation_requests=[_gate_card("keep-me")]))
    # Connect/auth failure with 0 streamed events must NOT erase pending state.
    fake_arc.chat.side_effect = ArcApiError(401, "unauthenticated")
    with pytest.raises(SystemExit) as exc:
        _run(store, "chat", "--message=hi", "--format=json")
    assert exc.value.code == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["action"] == "error"
    assert payload["error"] == "HTTP 401: unauthenticated"
    assert payload["partial_events"] is None
    assert _pending(store)["confirmations"][0]["confirmation_id"] == "keep-me"

    # Mid-stream JSONDecodeError (a ValueError subclass) in `confirm` must
    # route through `_handle_turn_failure` rather than bare `except ValueError`.
    fake_arc.active_turn_events = _events(
        text="partial before bad frame",
        confirmation_requests=[_gate_card("new-card")],
    )
    fake_arc.send_confirmation_response.side_effect = json.JSONDecodeError(
        "Unterminated string", "{", 1
    )
    with pytest.raises(SystemExit) as exc2:
        _run(store, "confirm", "--confirmed", "--format=json")
    assert exc2.value.code == 1
    err_payload = json.loads(capsys.readouterr().out)
    assert err_payload["action"] == "error"
    assert err_payload["partial_events"]["text"] == "partial before bad frame"
    assert _pending(store)["confirmations"][0]["confirmation_id"] == "new-card"

    # `sessions delete --format=json` emits structured JSON.
    arc_cli.resolve_session_id(
        TARGET, explicit_session_id=SESSION, session_store_path=str(store)
    )
    _run(store, "sessions", "delete", "--format=json")
    del_payload = json.loads(capsys.readouterr().out)
    assert del_payload == {
        "action": "sessions.delete",
        "canonical_session_id": f"{APP}/assistantSessions/{SESSION}",
        "deleted": True,
        "session_id": SESSION,
    }


def test_readonly_and_cancel_do_not_hijack_sticky_store_and_atomic_perms(
    tmp_path: Path, fake_arc: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    cxas_dir = tmp_path / ".cxas"
    store_file = cxas_dir / "arc_sessions.json"
    sticky = arc_cli.resolve_session_id(
        TARGET, session_store_path=str(store_file)
    )

    assert stat.S_IMODE(cxas_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(store_file.stat().st_mode) == 0o600

    for subcmd in (["sessions", "get"], ["cancel"]):
        args = _parser().parse_args(
            [
                "arc",
                *subcmd,
                f"--app-name={APP}",
                f"--api-endpoint={ENDPOINT}",
                f"--session-store={store_file}",
                "--session-id=other-session",
            ]
        )
        args.func(args)
        capsys.readouterr()
        assert json.loads(store_file.read_text()) == {KEY: sticky}

    # Corrupt JSON emits a warning to stderr instead of silently resetting.
    store_file.write_text("{corrupt")
    arc_cli.resolve_session_id(
        TARGET, session_store_path=str(store_file), persist=False
    )
    assert "Warning: ignoring corrupted JSON state file" in (
        capsys.readouterr().err
    )


def test_mutually_exclusive_flags_and_repeatable_choice_with_commas(
    store: Path, fake_arc: MagicMock
) -> None:
    with pytest.raises(SystemExit) as exc1:
        _parser().parse_args(
            [
                "arc",
                "chat",
                f"--app-name={APP}",
                "--url=https://ces.cloud.google.com/projects/p/locations/us/apps/a",
                "--message=hi",
            ]
        )
    assert exc1.value.code == 2

    with pytest.raises(SystemExit) as exc2:
        _parser().parse_args(
            [
                "arc",
                "chat",
                f"--app-name={APP}",
                "--session-id=s1",
                "--new-session",
                "--message=hi",
            ]
        )
    assert exc2.value.code == 2

    _seed_pending(store, _events(confirmation_requests=[_question_card()]))
    _run(
        store,
        "confirm",
        "--choice=A",
        "--choice=Yes, after 5pm, weekdays",
    )
    assert _confirm_kwargs(fake_arc)["answer_text"] == (
        "> Which channel?\nVoice only\n\n> Escalate?\nYes, after 5pm, weekdays"
    )

    _seed_pending(store, _events(option_questions=_questions()))
    _run(
        store,
        "answer",
        "--choice=B",
        "--choice=No, never",
    )
    assert fake_arc.chat.call_args.args == (
        SESSION,
        "> Which channel?\nChat\n\n> Escalate?\nNo, never",
    )


def test_arc_cli_reference_markdown_is_in_sync() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    ref_path = (
        repo_root
        / ".agents"
        / "skills"
        / "cxas-arc"
        / "references"
        / "arc_cli_reference.md"
    )
    assert ref_path.read_text(encoding="utf-8") == (
        arc_cli.render_cli_reference_markdown()
    )


def test_end_to_end_cli_with_real_arc_and_recorded_sse_fixtures(
    store: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixtures_dir = Path(__file__).resolve().parents[2] / "testdata" / "arc"
    gate_bytes = (fixtures_dir / "card_gate.sse").read_bytes()
    confirm_bytes = (fixtures_dir / "card_confirm.sse").read_bytes()

    class _FakeResp:
        status_code = 200
        headers = {"Content-Type": "text/event-stream"}

        def __init__(self, body: bytes) -> None:
            self._body = body

        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def iter_lines(self, decode_unicode: bool = False) -> object:
            return iter(self._body.splitlines())

        def iter_content(
            self, chunk_size: int = 256, decode_unicode: bool = False
        ) -> object:
            for i in range(0, len(self._body), chunk_size):
                yield self._body[i : i + chunk_size]

    posts: list[dict[str, object]] = []
    queued = [_FakeResp(gate_bytes), _FakeResp(confirm_bytes)]

    class _FakeHttp:
        def post(self, url: str, **kwargs: object) -> _FakeResp:
            posts.append({"url": url, **kwargs})
            return queued.pop(0)

    class _FakeCreds:
        token = "fake-bearer"
        expired = False

        def refresh(self, _request: object) -> None:
            return None

    real_http = _FakeHttp()

    def _build_real_client(args: argparse.Namespace, target: ArcTarget) -> Arc:
        return Arc(
            app_name=target.app_name,
            api_endpoint=target.api_endpoint,
            creds=_FakeCreds(),
            http_session=real_http,  # type: ignore[arg-type]
            client_profile=args.client_profile,
            progress=None,
        )

    monkeypatch.setattr(arc_cli, "_build_client", _build_real_client)

    _run(store, "chat", "--message=Lock the template")
    chat_out = capsys.readouterr().out
    assert "### [Confirmation Requests]" in chat_out
    assert "step-idx-12" in chat_out
    assert _pending(store)["confirmations"][0]["confirmation_id"] == (
        "step-idx-12"
    )

    _run(store, "confirm", "--confirmed")
    confirm_out = capsys.readouterr().out
    assert "Template locked" in confirm_out or "**Session**" in confirm_out
    assert _pending(store) == {}
    assert len(posts) == 2
    assert posts[1]["json"] == {  # type: ignore[index]
        "message": "",
        "confirmationResponse": {
            "confirmationId": "step-idx-12",
            "confirmed": True,
        },
        "clientCapabilities": ["CONFIRMATION_CARDS", "CLIENT_MANAGED_LRO"],
    }
