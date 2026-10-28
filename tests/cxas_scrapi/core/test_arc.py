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

"""Tests for the Arc REST client against recorded streaming fixtures."""

from __future__ import annotations

import itertools
import json
import pathlib
import time
from typing import Any

import pytest
import requests

from cxas_scrapi.core import arc as arc_module
from cxas_scrapi.core.arc import (
    Arc,
    ArcApiError,
    ArcStreamTimeout,
    iter_stream_json,
)
from cxas_scrapi.utils.arc_confirmation_policy import (
    ClickPolicy,
    ConfirmationAction,
    ConfirmationActionType,
    ConfirmationPolicy,
    make_policy,
)

_TESTDATA = pathlib.Path(__file__).resolve().parents[2] / "testdata" / "arc"
_APP = "projects/p/locations/us/apps/a"
_BASE = f"https://ces.googleapis.com/v1beta/{_APP}"
_SSE_HEADERS = {"content-type": "text/event-stream"}
_JSON_HEADERS = {"content-type": "application/json; charset=UTF-8"}
_OP_NAME = (
    "projects/example-project/locations/us-east1/operations/"
    "operation-1793151924013-65edcb775885a-94c65ca8-e18b72af"
)
_RUN_NAME = f"{_APP}/evaluationRuns/r1"


# ------------------------------------------------------------------- fakes


class FakeCreds:
    def __init__(self, token: str = "t") -> None:
        self.token = token
        self.expired = False
        self.refresh_calls = 0

    def refresh(self, request: Any) -> None:
        del request
        self.refresh_calls += 1


class FakeResponse:
    """Minimal stand-in for ``requests.Response`` (streaming + unary)."""

    def __init__(
        self,
        body: bytes | str = b"",
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        reason: str = "OK",
        chunk_size: int | None = None,
        raise_after: BaseException | None = None,
    ) -> None:
        self._body = body.encode("utf-8") if isinstance(body, str) else body
        self.status_code = status_code
        self.headers = dict(headers if headers is not None else _SSE_HEADERS)
        self.reason = reason
        self._chunk_size = chunk_size
        self._raise_after = raise_after
        self.closed = False

    @property
    def text(self) -> str:
        return self._body.decode("utf-8")

    def json(self) -> Any:
        return json.loads(self._body)

    def iter_lines(self, **kwargs: Any) -> Any:
        del kwargs
        yield from self._body.splitlines()
        if self._raise_after is not None:
            raise self._raise_after

    def iter_content(self, chunk_size: int = 8192, **kwargs: Any) -> Any:
        del kwargs
        size = self._chunk_size or chunk_size
        for start in range(0, len(self._body), size):
            yield self._body[start : start + size]
        if self._raise_after is not None:
            raise self._raise_after

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.closed = True


class FakeSession:
    """Records calls and replays queued responses."""

    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self._post_queue: list[FakeResponse] = []
        self._request_queue: list[FakeResponse] = []

    def queue_post(self, *responses: FakeResponse) -> None:
        self._post_queue.extend(responses)

    def queue_request(self, *responses: FakeResponse) -> None:
        self._request_queue.extend(responses)

    def post(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        stream: bool = False,
        timeout: Any = None,
    ) -> FakeResponse:
        self.posts.append(
            {
                "url": url,
                "params": params,
                "headers": headers,
                "json": json,
                "stream": stream,
                "timeout": timeout,
            }
        )
        if not self._post_queue:
            raise AssertionError(f"unexpected stream POST #{len(self.posts)}")
        return self._post_queue.pop(0)

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: Any = None,
    ) -> FakeResponse:
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "json": json,
                "params": params,
                "timeout": timeout,
            }
        )
        if self._request_queue:
            return self._request_queue.pop(0)
        return FakeResponse(b"{}", headers=_JSON_HEADERS)


class TypedPolicy(ConfirmationPolicy):
    """Types a composer reply for the first pending card only."""

    name = "typed-test"

    def decide(self, cards: Any) -> list[ConfirmationAction]:
        card = cards[0]
        return [
            ConfirmationAction(
                action=ConfirmationActionType.TYPE_TEXT,
                confirmation_id=card.confirmation_id,
                text="yes",
                tool=card.tool,
                reason="test",
            )
        ]


def sse_fixture(name: str) -> FakeResponse:
    return FakeResponse((_TESTDATA / f"{name}.sse").read_bytes())


def sse_body(events: list[dict[str, Any]]) -> bytes:
    return b"".join(
        b"data: " + json.dumps(e).encode("utf-8") + b"\r\n\r\n" for e in events
    )


def json_response(data: Any, status_code: int = 200) -> FakeResponse:
    return FakeResponse(
        json.dumps(data), status_code=status_code, headers=_JSON_HEADERS
    )


@pytest.fixture
def fake() -> FakeSession:
    return FakeSession()


@pytest.fixture
def arc(fake: FakeSession) -> Arc:
    return Arc(
        app_name=_APP,
        creds=FakeCreds(token="t"),
        http_session=fake,  # type: ignore[arg-type]
        api_endpoint="ces.googleapis.com",
    )


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    return sleeps


# ------------------------------------------------------------ construction


def test_constructor_normalises_endpoint_and_profile() -> None:
    client = Arc(
        app_name=_APP,
        creds=FakeCreds(),
        http_session=FakeSession(),  # type: ignore[arg-type]
        api_endpoint="https://autopush-ces.sandbox.googleapis.com/",
        client_profile="legacy",
    )

    assert client.api_endpoint == "autopush-ces.sandbox.googleapis.com"
    assert client.client_profile == "legacy"
    assert client.project_id == "p"
    assert client.user_agent.endswith(":arc")


def test_constructor_rejects_unknown_profile() -> None:
    with pytest.raises(ValueError, match="client_profile must be"):
        Arc(
            app_name=_APP,
            creds=FakeCreds(),
            http_session=FakeSession(),  # type: ignore[arg-type]
            client_profile="bogus",
        )


@pytest.mark.parametrize(
    ("session_id", "expected"),
    [
        ("s-1", f"{_APP}/assistantSessions/s-1"),
        (f"{_APP}/assistantSessions/s-1", f"{_APP}/assistantSessions/s-1"),
        ("assistantSessions/s-2", f"{_APP}/assistantSessions/s-2"),
    ],
)
def test_session_name(arc: Arc, session_id: str, expected: str) -> None:
    assert arc.session_name(session_id) == expected


# --------------------------------------------------------- iter_stream_json


def test_iter_stream_json_sse_with_crlf() -> None:
    body = (
        b'data: {"textChunk":"a"}\r\n\r\n'
        b": keep-alive\r\n\r\n"
        b"data:\r\n\r\n"
        b'data:{"textChunk":"b"}\r\n'
        b'data: {"turnCompleted":{"reason":"COMPLETED"}}\r\n\r\n'
    )

    objs = list(iter_stream_json(FakeResponse(body)))  # type: ignore[arg-type]

    assert objs == [
        {"textChunk": "a"},
        {"textChunk": "b"},
        {"turnCompleted": {"reason": "COMPLETED"}},
    ]


def test_iter_stream_json_sse_fixture_matches_direct_parse() -> None:
    raw = (_TESTDATA / "chat_sse.sse").read_bytes()
    expected = [
        json.loads(line[5:])
        for line in raw.splitlines()
        if line.startswith(b"data:")
    ]

    objs = list(iter_stream_json(FakeResponse(raw)))  # type: ignore[arg-type]

    assert objs == expected
    assert len(objs) == 39


@pytest.mark.parametrize("chunk_size", [1, 7, 8192])
def test_iter_stream_json_array_framing_across_chunks(chunk_size: int) -> None:
    events = [
        {"textChunk": "hello 🛡️ wörld"},
        {"toolCall": {"tool": "get_app", "args": {"x": [1, 2]}}},
        {"turnCompleted": {"reason": "COMPLETED"}},
    ]
    body = json.dumps(events, ensure_ascii=False, indent=1).encode("utf-8")
    response = FakeResponse(body, headers=_JSON_HEADERS, chunk_size=chunk_size)

    objs = list(iter_stream_json(response))  # type: ignore[arg-type]

    assert objs == events


@pytest.mark.parametrize("body", [b"[]", b"[\n]\n", b"", b"   "])
def test_iter_stream_json_empty_array_yields_nothing(body: bytes) -> None:
    response = FakeResponse(body, headers=_JSON_HEADERS)
    assert list(iter_stream_json(response)) == []  # type: ignore[arg-type]


def test_iter_stream_json_array_without_brackets() -> None:
    response = FakeResponse(b'{"a":1}\n{"b":2}', headers=_JSON_HEADERS)
    assert list(iter_stream_json(response)) == [{"a": 1}, {"b": 2}]  # type: ignore[arg-type]


# --------------------------------------------------------------- stream_turn


def test_stream_turn_request_shape(arc: Arc, fake: FakeSession) -> None:
    fake.queue_post(sse_fixture("chat_sse"))
    seen: list[dict[str, Any]] = []

    events = arc.stream_turn(
        "s-1", "What does this app do?", on_event=seen.append
    )

    assert len(fake.posts) == 1
    post = fake.posts[0]
    assert post["url"] == f"{_BASE}/assistantSessions/s-1:streamChatAiAssistant"
    assert post["params"] == {"alt": "sse"}
    assert post["headers"]["Authorization"] == "Bearer t"
    assert post["headers"]["x-goog-user-project"] == "p"
    assert post["headers"]["Content-Type"].startswith("application/json")
    assert post["headers"]["User-Agent"].startswith("cxas-scrapi/")
    assert post["stream"] is True
    assert post["timeout"] == (
        30,
        arc_module.CONSOLE_STREAM_IDLE_TIMEOUT_SECONDS,
    )
    assert post["json"] == {
        "message": "What does this app do?",
        "clientCapabilities": ["CONFIRMATION_CARDS", "CLIENT_MANAGED_LRO"],
    }
    assert len(seen) == 39
    assert "Card" in events.text
    assert events.turn_completed is not None
    assert events.turn_completed.reason == "COMPLETED"


def test_stream_turn_legacy_profile_capabilities(fake: FakeSession) -> None:
    client = Arc(
        app_name=_APP,
        creds=FakeCreds(),
        http_session=fake,  # type: ignore[arg-type]
        api_endpoint="ces.googleapis.com",
        client_profile="legacy",
    )
    fake.queue_post(sse_fixture("chat_sse"), sse_fixture("chat_sse"))

    client.stream_turn("s-1", "hi")
    client.stream_turn("s-1", "hi", enable_confirmation_cards=True)

    assert fake.posts[0]["json"]["clientCapabilities"] == ["CLIENT_MANAGED_LRO"]
    assert fake.posts[1]["json"]["clientCapabilities"] == [
        "CONFIRMATION_CARDS",
        "CLIENT_MANAGED_LRO",
    ]


def test_stream_turn_console_profile_can_disable_cards(
    arc: Arc, fake: FakeSession
) -> None:
    fake.queue_post(sse_fixture("chat_sse"))

    arc.stream_turn("s-1", "hi", enable_confirmation_cards=False)

    assert fake.posts[0]["json"]["clientCapabilities"] == ["CLIENT_MANAGED_LRO"]


def test_stream_turn_serialises_optional_fields(
    arc: Arc, fake: FakeSession
) -> None:
    fake.queue_post(sse_fixture("card_confirm"))
    completed = {
        "operationName": "ops/1",
        "operationType": "run_evaluation",
        "status": "SUCCEEDED",
    }

    arc.stream_turn(
        f"{_APP}/assistantSessions/s-1",
        "",
        attached_gcs_uris=["gs://b/one.pdf", "gs://b/two.pdf"],
        confirmation_response={
            "confirmationId": "step-idx-12",
            "confirmed": True,
        },
        operation_completed_event=completed,
        resume_from_sequence_number=5,
    )

    body = fake.posts[0]["json"]
    assert fake.posts[0]["url"].endswith(
        "/assistantSessions/s-1:streamChatAiAssistant"
    )
    assert body["message"] == ""
    assert body["attachedGcsUris"] == ["gs://b/one.pdf", "gs://b/two.pdf"]
    assert body["confirmationResponse"] == {
        "confirmationId": "step-idx-12",
        "confirmed": True,
    }
    assert body["operationCompletedEvent"] == completed
    assert body["resumeFromSequenceNumber"] == "5"


def test_stream_turn_merges_into_existing_events(
    arc: Arc, fake: FakeSession
) -> None:
    fake.queue_post(sse_fixture("card_gate"), sse_fixture("card_confirm"))

    first = arc.stream_turn("s-1", "add a variable")
    merged = arc.stream_turn("s-1", "", into=first)

    assert merged is first
    assert "spike_marker" in first.text
    assert len(first.confirmation_requests) == 1
    assert first.last_sequence_number == 13


def test_stream_turn_4xx_raises_api_error(arc: Arc, fake: FakeSession) -> None:
    fake.queue_post(
        json_response(
            {
                "error": {
                    "code": 403,
                    "message": "Permission denied on resource",
                    "status": "PERMISSION_DENIED",
                }
            },
            status_code=403,
        )
    )

    with pytest.raises(
        ArcApiError, match="HTTP 403: PERMISSION_DENIED: Permission denied"
    ) as excinfo:
        arc.stream_turn("s-1", "hi")

    assert excinfo.value.status_code == 403
    assert (
        excinfo.value.message
        == "PERMISSION_DENIED: Permission denied on resource"
    )
    assert excinfo.value.body["error"]["code"] == 403


def test_stream_turn_4xx_non_json_body(arc: Arc, fake: FakeSession) -> None:
    fake.queue_post(
        FakeResponse(
            b"<html>Bad Gateway</html>",
            status_code=502,
            headers={"content-type": "text/html"},
            reason="Bad Gateway",
        )
    )

    with pytest.raises(ArcApiError, match="HTTP 502: <html>Bad Gateway</html>"):
        arc.stream_turn("s-1", "hi")


def test_stream_turn_read_timeout_keeps_partial(
    arc: Arc, fake: FakeSession
) -> None:
    partial_body = sse_body(
        [
            {"thoughtChunk": "thinking", "sequenceNumber": "1"},
            {
                "toolCall": {"id": "c1", "tool": "get_app"},
                "sequenceNumber": "2",
            },
            {"textChunk": "partial", "sequenceNumber": "3"},
        ]
    )
    fake.queue_post(
        FakeResponse(
            partial_body, raise_after=requests.exceptions.ReadTimeout("idle")
        )
    )

    with pytest.raises(
        ArcStreamTimeout, match="idle for 180s after 3 events"
    ) as excinfo:
        arc.stream_turn("s-1", "hi")

    partial = excinfo.value.partial
    assert partial.text == "partial"
    assert [tc.tool for tc in partial.tool_calls] == ["get_app"]
    assert partial.thoughts == ["thinking"]
    assert partial.last_sequence_number == 3
    assert partial.turn_completed is None


def test_stream_turn_chunked_encoding_error(
    arc: Arc, fake: FakeSession
) -> None:
    fake.queue_post(
        FakeResponse(
            sse_body([{"textChunk": "x"}]),
            raise_after=requests.exceptions.ChunkedEncodingError("broken"),
        )
    )

    with pytest.raises(arc_module.ArcError, match="interrupted after 1 events"):
        arc.stream_turn("s-1", "hi")


def test_stream_turn_deadline_exceeded(
    arc: Arc, fake: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = itertools.count(0, 100)
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    fake.queue_post(sse_fixture("chat_sse"))

    with pytest.raises(
        ArcStreamTimeout, match="exceeded 10s deadline after 1 events"
    ):
        arc.stream_turn("s-1", "hi", timeout_seconds=10)


def test_headers_refresh_expired_credentials(
    arc: Arc, fake: FakeSession
) -> None:
    creds = arc.creds
    assert isinstance(creds, FakeCreds)
    creds.expired = True
    fake.queue_post(sse_fixture("chat_sse"))

    arc.stream_turn("s-1", "hi")

    # Once in Common.__init__ and once more because the token looked expired.
    assert creds.refresh_calls == 2


# --------------------------------------------------------------------- chat


def test_chat_click_policy_confirms_card(arc: Arc, fake: FakeSession) -> None:
    fake.queue_post(sse_fixture("card_gate"), sse_fixture("card_confirm"))

    events = arc.chat(
        "s-1",
        "Add a string variable spike_marker",
        confirmation_policy=make_policy("click"),
    )

    assert len(fake.posts) == 2
    assert (
        fake.posts[0]["json"]["message"] == "Add a string variable spike_marker"
    )
    assert "confirmationResponse" not in fake.posts[0]["json"]
    second = fake.posts[1]["json"]
    assert second["message"] == ""
    assert second["confirmationResponse"] == {
        "confirmationId": "step-idx-12",
        "confirmed": True,
    }

    assert len(events.confirmation_actions) == 1
    action = events.confirmation_actions[0]
    assert action.confirmation_id == "step-idx-12"
    assert action.tool == "update_app"
    assert action.action == "CLICK_CONFIRM"
    assert action.typed is False
    assert action.policy == "click"
    assert action.round == 1
    assert action.reason == "gate"
    assert action.followup_tool_calls >= 1
    assert action.followup_chars > 0
    assert events.pending_confirmation_requests == []
    assert len(events.confirmation_requests) == 1
    assert "spike_marker" in events.text
    assert "### [Confirmation Actions]" in events.format_markdown()
    assert "awaiting your answer" not in events.format_markdown()
    assert arc.last_stream_events is events
    assert arc.active_turn_events is None


def test_chat_click_policy_deny_tools(arc: Arc, fake: FakeSession) -> None:
    fake.queue_post(sse_fixture("card_gate"), sse_fixture("card_confirm"))
    policy = ClickPolicy(deny_tools=["update_*"])

    events = arc.chat("s-1", "go", confirmation_policy=policy)

    assert fake.posts[1]["json"]["confirmationResponse"] == {
        "confirmationId": "step-idx-12",
        "confirmed": False,
    }
    assert events.confirmation_actions[0].action == "CLICK_DENY"


@pytest.mark.parametrize("policy_name", ["manual", None])
def test_chat_manual_policy_leaves_card_pending(
    arc: Arc, fake: FakeSession, policy_name: str | None
) -> None:
    fake.queue_post(sse_fixture("card_gate"))

    events = arc.chat(
        "s-1",
        "Add a variable",
        confirmation_policy=make_policy(policy_name) if policy_name else None,
    )

    assert len(fake.posts) == 1
    pending = events.pending_confirmation_requests
    assert [c.confirmation_id for c in pending] == ["step-idx-12"]
    assert events.confirmation_actions == []
    assert events.text == ""
    assert "awaiting your answer" in events.format_markdown()


def test_chat_typed_reply_supersedes_other_cards(
    arc: Arc, fake: FakeSession
) -> None:
    two_cards = sse_body(
        [
            {"turnMetadata": {}, "sequenceNumber": "1"},
            {
                "confirmationRequest": {
                    "confirmationId": "card-1",
                    "tool": "update_app",
                },
                "sequenceNumber": "2",
            },
            {
                "confirmationRequest": {
                    "confirmationId": "card-2",
                    "tool": "create_tool",
                },
                "sequenceNumber": "3",
            },
            {
                "turnCompleted": {"turnId": "0", "reason": "COMPLETED"},
                "sequenceNumber": "4",
            },
        ]
    )
    fake.queue_post(FakeResponse(two_cards), sse_fixture("card_confirm"))

    events = arc.chat("s-1", "do both", confirmation_policy=TypedPolicy())

    assert len(fake.posts) == 2
    second = fake.posts[1]["json"]
    assert second["message"] == "yes"
    assert "confirmationResponse" not in second

    assert [a.confirmation_id for a in events.confirmation_actions] == [
        "card-1",
        "card-2",
    ]
    typed, superseded = events.confirmation_actions
    assert typed.action == "TYPE_TEXT"
    assert typed.typed is True
    assert typed.text == "yes"
    assert typed.policy == "typed-test"
    assert typed.followup_tool_calls == 2
    assert superseded.typed is True
    assert superseded.reason == "superseded by typed reply on card-1"
    assert superseded.tool == "create_tool"
    assert events.pending_confirmation_requests == []
    assert "spike_marker" in events.text


def test_chat_mixed_policy_always_typed(arc: Arc, fake: FakeSession) -> None:
    fake.queue_post(sse_fixture("card_gate"), sse_fixture("card_confirm"))
    policy = make_policy("mixed", typed_reply_probability=1.0, seed=7)

    events = arc.chat("s-1", "go", confirmation_policy=policy)

    second = fake.posts[1]["json"]
    assert second["message"]
    assert "confirmationResponse" not in second
    assert events.confirmation_actions[0].action == "TYPE_TEXT"
    assert events.confirmation_actions[0].policy == "mixed(p=1.0, seed=7)"


def test_chat_stops_at_max_confirmation_rounds(
    arc: Arc, fake: FakeSession
) -> None:
    # The server keeps raising a new card after every click.
    fake.queue_post(
        sse_fixture("card_gate"),
        FakeResponse(
            sse_body(
                [
                    {
                        "confirmationRequest": {
                            "confirmationId": "again",
                            "tool": "t",
                        }
                    },
                    {"turnCompleted": {"reason": "COMPLETED"}},
                ]
            )
        ),
    )

    events = arc.chat(
        "s-1",
        "go",
        confirmation_policy=make_policy("click"),
        max_confirmation_rounds=1,
    )

    assert len(fake.posts) == 2
    assert [
        c.confirmation_id for c in events.pending_confirmation_requests
    ] == ["again"]


def test_chat_partial_events_on_stream_timeout(
    arc: Arc, fake: FakeSession
) -> None:
    fake.queue_post(
        FakeResponse(
            sse_body([{"textChunk": "so far"}]),
            raise_after=requests.exceptions.ReadTimeout("idle"),
        )
    )

    with pytest.raises(ArcStreamTimeout) as excinfo:
        arc.chat("s-1", "hi")

    # The partial text travels on the exception; the client also keeps the
    # structured events of the failed turn for later rendering.
    assert excinfo.value.partial.text == "so far"
    assert arc.last_stream_events is not None
    assert arc.last_stream_events.last_sequence_number is None
    assert arc.active_turn_events is None


# ------------------------------------------------- send_confirmation_response


def test_send_confirmation_response_confirmed(
    arc: Arc, fake: FakeSession
) -> None:
    fake.queue_post(sse_fixture("card_confirm"))

    events = arc.send_confirmation_response(
        "s-1", "step-idx-12", confirmed=True, tool="update_app"
    )

    body = fake.posts[0]["json"]
    assert body["message"] == ""
    assert body["confirmationResponse"] == {
        "confirmationId": "step-idx-12",
        "confirmed": True,
    }
    assert body["clientCapabilities"] == [
        "CONFIRMATION_CARDS",
        "CLIENT_MANAGED_LRO",
    ]
    assert len(events.confirmation_actions) == 1
    action = events.confirmation_actions[0]
    assert action.action == "CLICK_CONFIRM"
    assert action.policy == "manual"
    assert action.round == 0
    assert action.tool == "update_app"
    assert action.reason == "explicit call"
    assert action.followup_chars == len(events.text)
    assert action.followup_tool_calls == 2
    assert "spike_marker" in events.text


def test_send_confirmation_response_deny_and_answer(
    arc: Arc, fake: FakeSession
) -> None:
    fake.queue_post(sse_fixture("card_confirm"), sse_fixture("card_confirm"))

    denied = arc.send_confirmation_response("s-1", "c1", confirmed=False)
    answered = arc.send_confirmation_response("s-1", "c2", answer_text="> Q\nB")

    assert fake.posts[0]["json"]["confirmationResponse"] == {
        "confirmationId": "c1",
        "confirmed": False,
    }
    assert denied.confirmation_actions[0].action == "CLICK_DENY"
    assert fake.posts[1]["json"]["confirmationResponse"] == {
        "confirmationId": "c2",
        "answerText": "> Q\nB",
    }
    assert answered.confirmation_actions[0].action == "CLICK_ANSWER"
    assert answered.confirmation_actions[0].text == "> Q\nB"


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"confirmed": True, "answer_text": "x"}],
)
def test_send_confirmation_response_requires_exactly_one(
    arc: Arc, kwargs: dict[str, Any]
) -> None:
    with pytest.raises(
        ValueError, match="exactly one of confirmed or answer_text"
    ):
        arc.send_confirmation_response("s-1", "c1", **kwargs)


# ---------------------------------------------------------------------- LRO


def _patch_eval_lro(
    monkeypatch: pytest.MonkeyPatch,
    arc: Arc,
    progress: dict[str, Any],
) -> dict[str, list[str]]:
    calls: dict[str, list[str]] = {"operation": [], "run": []}

    def get_operation(name: str) -> dict[str, Any]:
        calls["operation"].append(name)
        return {
            "name": name,
            "done": True,
            "metadata": {"evaluationRun": _RUN_NAME},
        }

    def get_evaluation_run(name: str) -> dict[str, Any]:
        calls["run"].append(name)
        return {"name": name, "state": "COMPLETED", "progress": progress}

    monkeypatch.setattr(arc, "get_operation", get_operation)
    monkeypatch.setattr(arc, "get_evaluation_run", get_evaluation_run)
    return calls


def test_chat_resolves_evaluation_lro(
    arc: Arc,
    fake: FakeSession,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: list[float],
) -> None:
    fake.queue_post(sse_fixture("lro_pending"), sse_fixture("lro_resume"))
    calls = _patch_eval_lro(
        monkeypatch,
        arc,
        {"totalCount": 5, "failedCount": 5, "completedCount": 5},
    )
    progress: list[str] = []
    arc._progress_cb = progress.append

    events = arc.chat("s-1", "run the evaluations")

    assert len(fake.posts) == 2
    assert calls["operation"] == [_OP_NAME]
    assert calls["run"] == [_RUN_NAME]
    second = fake.posts[1]["json"]
    assert second["message"] == ""
    assert second["operationCompletedEvent"] == {
        "operationName": _OP_NAME,
        "targetResourceName": _RUN_NAME,
        "operationType": "run_evaluation",
        "status": "SUCCEEDED",
        "metadata": {
            "pass_rate": 0.0,
            "passed_count": 0,
            "failed_count": 5,
            "total_count": 5,
        },
    }
    assert "Evaluation run initiated" in events.text
    assert "Evaluation Run Results" in events.text
    assert events.text.index("initiated") < events.text.index("Results")
    assert len(events.initiated_lros) == 1
    assert events.initiated_lros[0].status == "PROCESSED"
    assert events.turn_completed is not None
    assert events.last_sequence_number == 40
    assert any("waiting for run_evaluation" in p for p in progress)
    assert any("run_evaluation finished (SUCCEEDED)" in p for p in progress)
    assert no_sleep == []


def test_chat_without_auto_resolve_leaves_lro_pending(
    arc: Arc, fake: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake.queue_post(sse_fixture("lro_pending"))
    calls = _patch_eval_lro(monkeypatch, arc, {})

    events = arc.chat("s-1", "run the evaluations", auto_resolve_lro=False)

    assert len(fake.posts) == 1
    assert calls == {"operation": [], "run": []}
    assert len(events.initiated_lros) == 1
    assert events.initiated_lros[0].status == "PENDING"
    assert events.initiated_lros[0].operation_name == _OP_NAME


def test_poll_operation_delegates_to_evaluation_run_pass_rate(
    arc: Arc, monkeypatch: pytest.MonkeyPatch, no_sleep: list[float]
) -> None:
    monkeypatch.setattr(
        arc,
        "get_operation",
        lambda name: {
            "name": name,
            "done": False,
            "metadata": {"evaluationRun": {"name": _RUN_NAME}},
        },
    )
    monkeypatch.setattr(
        arc,
        "get_evaluation_run",
        lambda name: {
            "name": name,
            "state": "COMPLETED",
            "progress": {"totalCount": 4, "passedCount": 3, "failedCount": 1},
        },
    )

    result = arc.poll_operation("ops/1")

    assert result == {
        "status": "SUCCEEDED",
        "state": "COMPLETED",
        "evaluation_run": _RUN_NAME,
        "pass_rate": 75.0,
        "passed_count": 3,
        "failed_count": 1,
        "total_count": 4,
    }
    assert no_sleep == []


def test_poll_evaluation_run_error_state(
    arc: Arc, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        arc,
        "get_evaluation_run",
        lambda name: {"name": name, "state": "ERROR"},
    )

    result = arc.poll_evaluation_run(_RUN_NAME)

    assert result["status"] == "ERROR"
    assert result["pass_rate"] == 0.0
    assert result["total_count"] == 0


def test_poll_evaluation_run_polls_until_terminal(
    arc: Arc, monkeypatch: pytest.MonkeyPatch, no_sleep: list[float]
) -> None:
    states = iter(["RUNNING", "RUNNING", "COMPLETED"])
    monkeypatch.setattr(
        arc,
        "get_evaluation_run",
        lambda name: {
            "name": name,
            "state": next(states),
            "progress": {"totalCount": 2, "completedCount": 2},
        },
    )

    result = arc.poll_evaluation_run(_RUN_NAME, poll_interval_seconds=3)

    assert result["status"] == "SUCCEEDED"
    assert result["passed_count"] == 2
    assert result["pass_rate"] == 100.0
    assert no_sleep == [3, 3]


def test_poll_evaluation_run_timeout(
    arc: Arc, monkeypatch: pytest.MonkeyPatch, no_sleep: list[float]
) -> None:
    clock = itertools.count(0, 1000)
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(
        arc,
        "get_evaluation_run",
        lambda name: {"name": name, "state": "RUNNING"},
    )

    result = arc.poll_evaluation_run(_RUN_NAME, timeout_seconds=10)

    assert result["status"] == "TIMEOUT"
    assert result["state"] == "RUNNING"
    assert result["evaluation_run"] == _RUN_NAME
    assert "not finished after 10s" in result["error"]
    assert no_sleep == []


def test_poll_operation_done_with_error(
    arc: Arc, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        arc,
        "get_operation",
        lambda name: {
            "name": name,
            "done": True,
            "error": {"code": 3, "message": "Invalid evaluation"},
        },
    )

    result = arc.poll_operation("ops/1")

    assert result == {"status": "FAILED", "error": "Invalid evaluation"}


def test_poll_operation_done_with_response(
    arc: Arc, monkeypatch: pytest.MonkeyPatch, no_sleep: list[float]
) -> None:
    ops = iter(
        [
            {"name": "ops/1", "done": False},
            {"name": "ops/1", "done": True, "response": {"ok": True}},
        ]
    )
    monkeypatch.setattr(arc, "get_operation", lambda name: next(ops))

    result = arc.poll_operation("ops/1", poll_interval_seconds=2)

    assert result == {"status": "SUCCEEDED", "response": {"ok": True}}
    assert no_sleep == [2]


def test_poll_operation_timeout(
    arc: Arc, monkeypatch: pytest.MonkeyPatch, no_sleep: list[float]
) -> None:
    clock = itertools.count(0, 1000)
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(arc, "get_operation", lambda name: {"done": False})

    result = arc.poll_operation("ops/1", timeout_seconds=10)

    assert result["status"] == "TIMEOUT"
    assert no_sleep == []


def test_poll_detached_job(
    arc: Arc, monkeypatch: pytest.MonkeyPatch, no_sleep: list[float]
) -> None:
    def session_with(status: str) -> dict[str, Any]:
        return {
            "messages": [
                {
                    "chunks": [
                        {"text": "ignored"},
                        {
                            "typedPayload": {
                                "type": "detached_assistant_status",
                                "payload": json.dumps(
                                    {
                                        "job_id": "guddelon-1",
                                        "status": status,
                                        "memo_path": "memo/out.md",
                                    }
                                ),
                            }
                        },
                        {
                            "typedPayload": {
                                "type": "detached_assistant_status",
                                "data": {
                                    "job_id": "guddelon-other",
                                    "status": "failed",
                                },
                            }
                        },
                    ]
                }
            ]
        }

    sessions = iter([session_with("running"), session_with("completed")])
    monkeypatch.setattr(arc, "get_session", lambda session_id: next(sessions))

    result = arc.poll_detached_job("s-1", "guddelon-1", poll_interval_seconds=1)

    assert result == {"status": "SUCCEEDED", "memo_path": "memo/out.md"}
    assert no_sleep == [1]


def test_chat_detached_lro_resumes_with_memo(
    arc: Arc, fake: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    pending = sse_body(
        [
            {
                "toolResponse": {
                    "id": "c1",
                    "tool": "run_kestrel_python",
                    "response": {
                        "status": "RUNNING",
                        "job_id": "guddelon-9",
                        "memo_path": "memo/report.md",
                    },
                }
            },
            {"turnCompleted": {"reason": "COMPLETED"}},
        ]
    )
    fake.queue_post(
        FakeResponse(pending), FakeResponse(sse_body([{"textChunk": "done"}]))
    )
    monkeypatch.setattr(
        arc,
        "poll_detached_job",
        lambda session_id, job_id, **kw: {
            "status": "SUCCEEDED",
            "memo_path": "memo/report.md",
        },
    )

    events = arc.chat("s-1", "go")

    assert len(fake.posts) == 2
    assert fake.posts[1]["json"]["operationCompletedEvent"] == {
        "operationName": "guddelon-9",
        "targetResourceName": "memo/report.md",
        "operationType": "detached_assistant",
        "status": "SUCCEEDED",
    }
    assert events.text.strip() == "done"
    assert events.initiated_lros[0].operation_type == "detached_assistant"


def test_chat_lro_already_processed_fallback(
    arc: Arc,
    fake: FakeSession,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: list[float],
) -> None:
    already = sse_body(
        [
            {
                "textChunk": (
                    "The operation has already been processed for this session."
                )
            },
            {"turnCompleted": {"reason": "COMPLETED"}},
        ]
    )
    fake.queue_post(
        sse_fixture("lro_pending"),
        FakeResponse(already),
        sse_fixture("lro_resume"),
    )
    _patch_eval_lro(
        monkeypatch,
        arc,
        {"totalCount": 5, "failedCount": 5, "completedCount": 5},
    )

    events = arc.chat("s-1", "run the evaluations")

    assert len(fake.posts) == 3
    assert "operationCompletedEvent" in fake.posts[1]["json"]
    third = fake.posts[2]["json"]
    assert "operationCompletedEvent" not in third
    assert third["message"].startswith(f"Operation {_OP_NAME} (run_evaluation)")
    assert "finished with status SUCCEEDED" in third["message"]
    assert f"on resource {_RUN_NAME}" in third["message"]
    assert "(pass rate: 0.0%)" in third["message"]
    assert "already been processed" not in events.text
    assert "Evaluation Run Results" in events.text
    assert no_sleep == []


def test_chat_lro_resume_retries_once_on_api_error(
    arc: Arc,
    fake: FakeSession,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: list[float],
) -> None:
    fake.queue_post(
        sse_fixture("lro_pending"),
        json_response(
            {"error": {"message": "flaky", "status": "UNAVAILABLE"}}, 503
        ),
        sse_fixture("lro_resume"),
    )
    _patch_eval_lro(monkeypatch, arc, {"totalCount": 1, "completedCount": 1})

    events = arc.chat("s-1", "run the evaluations")

    assert len(fake.posts) == 3
    assert (
        fake.posts[2]["json"]["operationCompletedEvent"]["status"]
        == "SUCCEEDED"
    )
    assert "Evaluation Run Results" in events.text
    assert no_sleep == [5]


# -------------------------------------------------------------------- unary


def test_list_sessions(arc: Arc, fake: FakeSession) -> None:
    fake.queue_request(
        json_response({"assistantSessions": [{"name": "s1"}, {"name": "s2"}]})
    )

    sessions = arc.list_sessions(page_size=5, filter_='displayName="x"')

    assert [s["name"] for s in sessions] == ["s1", "s2"]
    req = fake.requests[0]
    assert req["method"] == "GET"
    assert req["url"] == f"{_BASE}/assistantSessions"
    assert req["params"] == {"pageSize": 5, "filter": 'displayName="x"'}
    assert req["json"] is None
    assert req["headers"]["Authorization"] == "Bearer t"
    assert req["headers"]["x-goog-user-project"] == "p"


def test_list_sessions_without_filter(arc: Arc, fake: FakeSession) -> None:
    assert arc.list_sessions() == []
    assert fake.requests[0]["params"] == {"pageSize": 20}


def test_get_session_and_artifacts(arc: Arc, fake: FakeSession) -> None:
    fake.queue_request(
        json_response({"name": "x", "artifacts": [{"gcsUri": "gs://b/a"}]}),
        json_response({"name": "x", "artifacts": [{"gcsUri": "gs://b/a"}]}),
    )

    session = arc.get_session("s-1")
    artifacts = arc.session_artifacts(f"{_APP}/assistantSessions/s-1")

    assert session["name"] == "x"
    assert artifacts == [{"gcsUri": "gs://b/a"}]
    assert fake.requests[0]["method"] == "GET"
    assert fake.requests[0]["url"] == f"{_BASE}/assistantSessions/s-1"
    assert fake.requests[1]["url"] == f"{_BASE}/assistantSessions/s-1"


@pytest.mark.parametrize(
    ("turn_id", "expected_body"),
    [("0", {"turnId": "0"}), ("", {})],
)
def test_cancel_turn(
    arc: Arc, fake: FakeSession, turn_id: str, expected_body: dict[str, Any]
) -> None:
    fake.queue_request(json_response({"cancelled": True}))

    result = arc.cancel_turn("s-1", turn_id)

    assert result == {"cancelled": True}
    req = fake.requests[0]
    assert req["method"] == "POST"
    assert req["url"] == f"{_BASE}/assistantSessions/s-1:cancelAssistantTurn"
    assert req["json"] == expected_body


def test_delete_session(arc: Arc, fake: FakeSession) -> None:
    fake.queue_request(
        FakeResponse(b"", status_code=204, headers=_JSON_HEADERS)
    )

    assert arc.delete_session("s-1") is None

    req = fake.requests[0]
    assert req["method"] == "DELETE"
    assert req["url"] == f"{_BASE}/assistantSessions/s-1"


def test_create_session(arc: Arc, fake: FakeSession) -> None:
    fake.queue_request(json_response({"name": f"{_APP}/assistantSessions/s-1"}))

    created = arc.create_session(f"{_APP}/assistantSessions/s-1")

    assert created["name"].endswith("/assistantSessions/s-1")
    req = fake.requests[0]
    assert req["method"] == "POST"
    assert req["url"] == f"{_BASE}/assistantSessions"
    assert req["params"] == {"assistantSessionId": "s-1"}
    assert req["json"] == {}


def test_generate_onboarding_suggestions(arc: Arc, fake: FakeSession) -> None:
    fake.queue_request(
        json_response({"suggestions": [{"title": "Explain my app"}]})
    )

    suggestions = arc.generate_onboarding_suggestions()

    assert suggestions == [{"title": "Explain my app"}]
    req = fake.requests[0]
    assert req["method"] == "POST"
    assert req["url"] == f"{_BASE}:generateOnboardingSuggestions"
    assert req["json"] == {}


def test_list_evaluation_runs_and_getters(arc: Arc, fake: FakeSession) -> None:
    fake.queue_request(
        json_response({"evaluationRuns": [{"name": _RUN_NAME}]}),
        json_response({"name": "ops/1", "done": False}),
        json_response({"name": _RUN_NAME, "state": "RUNNING"}),
    )

    runs = arc.list_evaluation_runs(page_size=3)
    op = arc.get_operation("projects/p/locations/us/operations/op-1")
    run = arc.get_evaluation_run(_RUN_NAME)

    assert runs == [{"name": _RUN_NAME}]
    assert op == {"name": "ops/1", "done": False}
    assert run["state"] == "RUNNING"
    assert fake.requests[0]["url"] == f"{_BASE}/evaluationRuns"
    assert fake.requests[0]["params"] == {"pageSize": 3}
    assert fake.requests[1]["url"] == (
        "https://ces.googleapis.com/v1beta/projects/p/locations/us/operations/op-1"
    )
    assert (
        fake.requests[2]["url"]
        == f"https://ces.googleapis.com/v1beta/{_RUN_NAME}"
    )


def test_unary_error_raises_api_error(arc: Arc, fake: FakeSession) -> None:
    fake.queue_request(
        json_response(
            {
                "error": {
                    "code": 404,
                    "message": "Session not found",
                    "status": "NOT_FOUND",
                }
            },
            status_code=404,
        )
    )

    with pytest.raises(
        ArcApiError, match="HTTP 404: NOT_FOUND: Session not found"
    ):
        arc.get_session("missing")
