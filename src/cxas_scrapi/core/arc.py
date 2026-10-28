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

"""Client for the CX Agent Studio AI assistant (Arc).

Arc is the conversational builder assistant embedded in the CX Agent Studio
console. This module drives it the way the console does, over the public
``v1beta`` REST API:

* ``SessionService.StreamChatAiAssistant`` (server streaming) for turns,
  confirmation-card answers and client-managed LRO resumption;
* ``SessionService.CancelAssistantTurn`` / ``GenerateOnboardingSuggestions``;
* ``AssistantService`` session CRUD (``assistantSessions``);
* ``google.longrunning.Operations`` and ``EvaluationRuns`` for LRO polling.

Example::

    from cxas_scrapi.core.arc import Arc

    arc = Arc(app_name="projects/my-proj/locations/us/apps/my-app")
    events = arc.chat("arc-cli-demo", "What does this app do?")
    print(events.format_markdown())
"""

from __future__ import annotations

import codecs
import http
import json
import logging
import time
import typing
from typing import Any

import requests
from google.auth.transport.requests import Request as GoogleAuthRequest
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from cxas_scrapi.core.common import DEFAULT_API_ENDPOINT, Common
from cxas_scrapi.utils.arc_context import session_short_id
from cxas_scrapi.utils.arc_events import (
    ConfirmationActionRecord,
    LroOperationInfo,
    TurnStreamEvents,
    TurnStreamReducer,
)

if typing.TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence

    from cxas_scrapi.utils.arc_confirmation_policy import ConfirmationPolicy
    from cxas_scrapi.utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

API_VERSION = "v1beta"
CLIENT_PROFILE_CONSOLE = "console"
CLIENT_PROFILE_LEGACY = "legacy"
CAPABILITY_CONFIRMATION_CARDS = "CONFIRMATION_CARDS"
CAPABILITY_CLIENT_MANAGED_LRO = "CLIENT_MANAGED_LRO"
# The console keeps a stream open without data for up to ~3 minutes while
# the assistant runs tools; mirror that idle tolerance.
CONSOLE_STREAM_IDLE_TIMEOUT_SECONDS = 180.0
_ALREADY_PROCESSED_MARKER = "has already been processed for this session"
_DETACHED_JOB_PREFIX = "guddelon-"
_TERMINAL_RUN_STATES = ("COMPLETED", "ERROR", "CANCELLED")


class ArcError(RuntimeError):
    """Base error for Arc client failures."""


class ArcApiError(ArcError):
    """An HTTP error returned by the API, with the decoded status if any."""

    def __init__(self, status_code: int, message: str, body: Any = None):
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.body = body


class ArcStreamTimeout(ArcError):
    """The stream went idle or the turn exceeded its deadline."""

    def __init__(self, message: str, partial: TurnStreamEvents):
        super().__init__(message)
        self.partial = partial


def iter_stream_json(response: requests.Response) -> Iterator[dict[str, Any]]:
    """Yields JSON objects from a streaming REST response.

    Handles both ``?alt=sse`` (``data: {...}`` lines) and the default
    One Platform JSON-array framing (``[{...},{...}]``).
    """
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for raw in response.iter_lines(decode_unicode=False):
            if not raw or not raw.startswith(b"data:"):
                continue
            payload = raw[5:].strip()
            if not payload:
                continue
            yield json.loads(payload.decode("utf-8"))
        return

    decoder = json.JSONDecoder()
    # Chunks may split a multi-byte UTF-8 sequence; decode incrementally.
    utf8 = codecs.getincrementaldecoder("utf-8")()
    buffer = ""
    started = False
    for chunk in response.iter_content(chunk_size=8192, decode_unicode=False):
        if not chunk:
            continue
        buffer += utf8.decode(chunk)
        while True:
            buffer = buffer.lstrip()
            if not started:
                if not buffer:
                    break
                if buffer[0] == "[":
                    buffer = buffer[1:]
                started = True
                continue
            if buffer[:1] in (",", "]"):
                buffer = buffer[1:]
                continue
            if not buffer:
                break
            try:
                obj, end = decoder.raw_decode(buffer)
            except ValueError:
                break  # need more bytes
            buffer = buffer[end:]
            if isinstance(obj, dict):
                yield obj
    rest = buffer.strip().strip("],").strip()
    if rest:
        obj = json.loads(rest)
        if isinstance(obj, dict):
            yield obj


def _retrying_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[
            http.HTTPStatus.TOO_MANY_REQUESTS,
            http.HTTPStatus.INTERNAL_SERVER_ERROR,
            http.HTTPStatus.BAD_GATEWAY,
            http.HTTPStatus.SERVICE_UNAVAILABLE,
            http.HTTPStatus.GATEWAY_TIMEOUT,
        ],
        allowed_methods=frozenset(["GET", "DELETE"]),
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


class Arc(Common):
    """Drives an assistant session on a CX Agent Studio app.

    Args:
        app_name: Full app resource name
            (``projects/{p}/locations/{l}/apps/{a}``).
        client_profile: ``console`` (default) sends the capabilities the
            console sends, so gated tools pause on confirmation cards;
            ``legacy`` omits ``CONFIRMATION_CARDS`` and the server auto-denies
            gated tools.
        api_endpoint: API host; defaults to ``CES_API_ENDPOINT`` or prod.
        stream_idle_timeout: Seconds without any streamed event before the
            turn is abandoned client-side.
        rate_limiter: Optional :class:`RateLimiter` applied before each
            streaming request.
        http_session: Optional ``requests.Session`` (tests inject fakes).
        progress: Optional callback for short progress messages.
        **kwargs: Forwarded to :class:`Common` (credentials, scopes, ...).
    """

    def __init__(
        self,
        app_name: str,
        *,
        client_profile: str = CLIENT_PROFILE_CONSOLE,
        api_endpoint: str | None = None,
        stream_idle_timeout: float = CONSOLE_STREAM_IDLE_TIMEOUT_SECONDS,
        rate_limiter: RateLimiter | None = None,
        http_session: requests.Session | None = None,
        progress: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("user_agent_extension", "arc")
        super().__init__(app_name=app_name, **kwargs)
        if client_profile not in (
            CLIENT_PROFILE_CONSOLE,
            CLIENT_PROFILE_LEGACY,
        ):
            raise ValueError(
                f"client_profile must be '{CLIENT_PROFILE_CONSOLE}' or "
                f"'{CLIENT_PROFILE_LEGACY}', got {client_profile!r}"
            )
        self.client_profile = client_profile
        endpoint = (api_endpoint or DEFAULT_API_ENDPOINT).strip()
        endpoint = endpoint.removeprefix("https://").removeprefix("http://")
        self.api_endpoint = endpoint.rstrip("/")
        self._base_url = f"https://{self.api_endpoint}/{API_VERSION}"
        self.stream_idle_timeout = stream_idle_timeout
        self.rate_limiter = rate_limiter
        self._progress_cb = progress
        self.session = http_session or _retrying_session()
        # Streams must not be retried by the adapter; use a plain session.
        self._stream_session = http_session or requests.Session()
        self.last_stream_events: TurnStreamEvents | None = None
        self._active_turn_events: TurnStreamEvents | None = None

    # ------------------------------------------------------------------ utils

    @property
    def active_turn_events(self) -> TurnStreamEvents | None:
        """Events of the turn currently in flight (for partial rendering)."""
        return self._active_turn_events

    def _progress(self, message: str) -> None:
        if self._progress_cb is not None:
            self._progress_cb(message)

    def _headers(self) -> dict[str, str]:
        if (
            getattr(self.creds, "expired", False)
            or getattr(self.creds, "token", None) is None
        ) and hasattr(self.creds, "refresh"):
            self.creds.refresh(GoogleAuthRequest())
        headers = {
            "Authorization": f"Bearer {self.creds.token}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": self.user_agent,
        }
        if self.project_id:
            headers["x-goog-user-project"] = self.project_id
        return headers

    @staticmethod
    def _error_from_response(response: requests.Response) -> ArcApiError:
        body: Any = None
        message = response.reason or ""
        try:
            body = response.json()
            err = body.get("error", {}) if isinstance(body, dict) else {}
            message = err.get("message") or message
            status = err.get("status")
            if status:
                message = f"{status}: {message}"
        except ValueError:
            text = (response.text or "").strip()
            if text:
                message = text[:500]
        return ArcApiError(response.status_code, message, body)

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float = 60.0,
    ) -> Any:
        url = f"{self._base_url}/{path}"
        response = self.session.request(
            method=method,
            url=url,
            headers=self._headers(),
            json=data,
            params=params,
            timeout=timeout,
        )
        if response.status_code >= 400:
            raise self._error_from_response(response)
        if response.status_code == 204 or not response.text:
            return {}
        return response.json()

    def session_name(self, session_id: str) -> str:
        """Returns the full ``assistantSessions`` resource name."""
        if "/assistantSessions/" in session_id:
            return session_id
        return (
            f"{self.app_name}/assistantSessions/{session_short_id(session_id)}"
        )

    def _capabilities(
        self, enable_confirmation_cards: bool | None
    ) -> list[str]:
        caps = [CAPABILITY_CLIENT_MANAGED_LRO]
        cards = (
            self.client_profile == CLIENT_PROFILE_CONSOLE
            if enable_confirmation_cards is None
            else enable_confirmation_cards
        )
        if cards:
            caps.insert(0, CAPABILITY_CONFIRMATION_CARDS)
        return caps

    # ---------------------------------------------------------------- sessions

    def list_sessions(
        self, page_size: int = 20, filter_: str = ""
    ) -> list[dict[str, Any]]:
        """Lists assistant sessions on the app (first ``page_size`` only)."""
        params: dict[str, Any] = {"pageSize": page_size}
        if filter_:
            params["filter"] = filter_
        data = self._request(
            "GET", f"{self.app_name}/assistantSessions", params=params
        )
        return list(data.get("assistantSessions", []))

    def get_session(self, session_id: str) -> dict[str, Any]:
        """Gets an assistant session (messages, artifacts, active turn)."""
        return self._request("GET", self.session_name(session_id))

    def create_session(self, session_id: str) -> dict[str, Any]:
        """Explicitly creates an assistant session (streams create lazily)."""
        return self._request(
            "POST",
            f"{self.app_name}/assistantSessions",
            params={"assistantSessionId": session_short_id(session_id)},
            data={},
        )

    def delete_session(self, session_id: str) -> None:
        """Deletes an assistant session."""
        self._request("DELETE", self.session_name(session_id))

    def cancel_turn(self, session_id: str, turn_id: str = "") -> dict[str, Any]:
        """Cancels the in-flight turn of a session."""
        body: dict[str, Any] = {}
        if turn_id:
            body["turnId"] = turn_id
        return self._request(
            "POST",
            f"{self.session_name(session_id)}:cancelAssistantTurn",
            data=body,
        )

    def generate_onboarding_suggestions(self) -> list[dict[str, Any]]:
        """Returns starter suggestions the console shows on an empty session."""
        data = self._request(
            "POST", f"{self.app_name}:generateOnboardingSuggestions", data={}
        )
        return list(data.get("suggestions", []))

    def session_artifacts(self, session_id: str) -> list[dict[str, Any]]:
        """Artifacts (``gcsUri`` pointers) recorded on the session."""
        return list(self.get_session(session_id).get("artifacts", []))

    # -------------------------------------------------------------- operations

    def get_operation(self, name: str) -> dict[str, Any]:
        """Gets a long-running operation by resource name."""
        return self._request("GET", name)

    def get_evaluation_run(self, name: str) -> dict[str, Any]:
        """Gets an evaluation run by resource name."""
        return self._request("GET", name)

    def list_evaluation_runs(self, page_size: int = 10) -> list[dict[str, Any]]:
        """Lists the most recent evaluation runs on the app."""
        data = self._request(
            "GET",
            f"{self.app_name}/evaluationRuns",
            params={"pageSize": page_size},
        )
        return list(data.get("evaluationRuns", []))

    # --------------------------------------------------------------- streaming

    def stream_turn(
        self,
        session_id: str,
        message: str = "",
        *,
        attached_gcs_uris: Sequence[str] = (),
        confirmation_response: dict[str, Any] | None = None,
        operation_completed_event: dict[str, Any] | None = None,
        resume_from_sequence_number: int | None = None,
        enable_confirmation_cards: bool | None = None,
        timeout_seconds: float = 1800,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        into: TurnStreamEvents | None = None,
    ) -> TurnStreamEvents:
        """Sends one ``StreamChatAiAssistant`` request and consumes the stream.

        This is the low-level primitive: no confirmation policy and no LRO
        resumption. See :meth:`chat` for the console-equivalent turn loop.

        Args:
            session_id: Short id or full session name.
            message: User text (may be empty for a pure card click).
            attached_gcs_uris: ``gs://`` URIs attached to the turn.
            confirmation_response: ``{"confirmationId": ..., "confirmed":
                bool}`` or ``{"confirmationId": ..., "answerText": str}``.
            operation_completed_event: Client-managed LRO completion event.
            resume_from_sequence_number: Replay from this sequence number.
            enable_confirmation_cards: Override the profile's card setting.
            timeout_seconds: Overall deadline for this stream.
            on_event: Callback for every raw JSON event.
            into: Merge the sub-stream into this aggregate instead of a new one.

        Returns:
            The events of this stream (``into`` when provided).
        """
        body: dict[str, Any] = {
            "message": message or "",
            "clientCapabilities": self._capabilities(enable_confirmation_cards),
        }
        if attached_gcs_uris:
            body["attachedGcsUris"] = list(attached_gcs_uris)
        if confirmation_response:
            body["confirmationResponse"] = confirmation_response
        if operation_completed_event:
            body["operationCompletedEvent"] = operation_completed_event
        if resume_from_sequence_number is not None:
            body["resumeFromSequenceNumber"] = str(resume_from_sequence_number)

        if self.rate_limiter is not None:
            self.rate_limiter.wait_and_consume()

        url = (
            f"{self._base_url}/{self.session_name(session_id)}"
            ":streamChatAiAssistant"
        )
        reducer = TurnStreamReducer(on_progress=self._progress)
        target = into if into is not None else TurnStreamEvents()
        deadline = time.monotonic() + timeout_seconds
        count = 0
        try:
            with self._stream_session.post(
                url,
                params={"alt": "sse"},
                headers=self._headers(),
                json=body,
                stream=True,
                timeout=(30, self.stream_idle_timeout),
            ) as response:
                if response.status_code >= 400:
                    raise self._error_from_response(response)
                for obj in iter_stream_json(response):
                    count += 1
                    reducer.feed(obj)
                    if on_event is not None:
                        on_event(obj)
                    if time.monotonic() > deadline:
                        target.merge(reducer.events)
                        raise ArcStreamTimeout(
                            f"Turn exceeded {timeout_seconds:.0f}s deadline "
                            f"after {count} events.",
                            target,
                        )
        except requests.exceptions.ReadTimeout as e:
            target.merge(reducer.events)
            raise ArcStreamTimeout(
                f"Stream idle for {self.stream_idle_timeout:.0f}s after "
                f"{count} events.",
                target,
            ) from e
        except requests.exceptions.ChunkedEncodingError as e:
            target.merge(reducer.events)
            raise ArcError(
                f"Stream interrupted after {count} events: {e}"
            ) from e
        target.merge(reducer.events)
        return target

    # --------------------------------------------------------------- turn loop

    def _send_turn_request(
        self,
        session_id: str,
        turn_events: TurnStreamEvents,
        *,
        message: str = "",
        attached_gcs_uris: Sequence[str] = (),
        confirmation_response: dict[str, Any] | None = None,
        operation_completed_event: dict[str, Any] | None = None,
        enable_confirmation_cards: bool | None,
        timeout_seconds: float,
        auto_resolve_lro: bool,
        lro_timeout_seconds: float,
        lro_poll_interval_seconds: float,
        max_lro_iterations: int,
    ) -> str:
        """Streams one request into ``turn_events`` and resolves its LROs.

        Returns the text streamed by this request (and its LRO follow-ups).
        """
        sub = TurnStreamEvents()
        try:
            self.stream_turn(
                session_id,
                message,
                attached_gcs_uris=attached_gcs_uris,
                confirmation_response=confirmation_response,
                operation_completed_event=operation_completed_event,
                enable_confirmation_cards=enable_confirmation_cards,
                timeout_seconds=timeout_seconds,
                into=sub,
            )
        finally:
            text_before = turn_events.text
            turn_events.merge(sub)
            # The caller re-assembles text on success; keep the running text
            # here so a failed turn still renders what streamed so far.
            turn_events.text = "\n\n".join(
                part for part in (text_before, sub.text) if part
            )
        text = sub.text
        pending = [lro for lro in sub.initiated_lros if lro.status == "PENDING"]
        if auto_resolve_lro and pending:
            resumed = self._resolve_lro_loop(
                session_id,
                turn_events,
                pending,
                enable_confirmation_cards=enable_confirmation_cards,
                timeout_seconds=timeout_seconds,
                lro_timeout_seconds=lro_timeout_seconds,
                lro_poll_interval_seconds=lro_poll_interval_seconds,
                max_lro_iterations=max_lro_iterations,
            )
            text = "\n\n".join(part for part in (text, resumed) if part)
        return text

    def _run_turn(
        self,
        session_id: str,
        *,
        message: str = "",
        attached_gcs_uris: Sequence[str] = (),
        confirmation_response: dict[str, Any] | None = None,
        explicit_action: ConfirmationActionRecord | None = None,
        enable_confirmation_cards: bool | None,
        confirmation_policy: ConfirmationPolicy | None,
        max_confirmation_rounds: int,
        timeout_seconds: float,
        auto_resolve_lro: bool,
        lro_timeout_seconds: float,
        lro_poll_interval_seconds: float,
        max_lro_iterations: int,
    ) -> TurnStreamEvents:
        send_kwargs: dict[str, Any] = {
            "enable_confirmation_cards": enable_confirmation_cards,
            "timeout_seconds": timeout_seconds,
            "auto_resolve_lro": auto_resolve_lro,
            "lro_timeout_seconds": lro_timeout_seconds,
            "lro_poll_interval_seconds": lro_poll_interval_seconds,
            "max_lro_iterations": max_lro_iterations,
        }
        turn_events = TurnStreamEvents()
        if explicit_action is not None:
            turn_events.confirmation_actions.append(explicit_action)
        self._active_turn_events = turn_events
        chunks: list[str] = []
        try:
            chunks.append(
                self._send_turn_request(
                    session_id,
                    turn_events,
                    message=message,
                    attached_gcs_uris=attached_gcs_uris,
                    confirmation_response=confirmation_response,
                    **send_kwargs,
                )
            )
            if explicit_action is not None:
                explicit_action.followup_chars = len(chunks[0])
                explicit_action.followup_tool_calls = len(
                    turn_events.tool_calls
                )
            if (
                confirmation_policy is not None
                and confirmation_policy.answers_cards
            ):
                self._resolve_confirmation_loop(
                    session_id,
                    turn_events,
                    chunks,
                    confirmation_policy,
                    max_confirmation_rounds,
                    **send_kwargs,
                )
            turn_events.text = "".join(chunks)
            return turn_events
        finally:
            if not turn_events.text and chunks:
                turn_events.text = "".join(chunks)
            self.last_stream_events = turn_events
            self._active_turn_events = None

    def _resolve_confirmation_loop(
        self,
        session_id: str,
        turn_events: TurnStreamEvents,
        chunks: list[str],
        policy: ConfirmationPolicy,
        max_confirmation_rounds: int,
        **send_kwargs: Any,
    ) -> None:
        """Answers pending cards with ``policy`` until none remain."""
        policy_desc = policy.describe()
        for round_idx in range(1, max_confirmation_rounds + 1):
            pending = turn_events.pending_confirmation_requests
            if not pending:
                return
            actions = policy.decide(pending)
            if not actions:
                return
            for action in actions:
                record = ConfirmationActionRecord(
                    confirmation_id=action.confirmation_id,
                    tool=action.tool,
                    action=action.action.value,
                    text=action.text,
                    typed=action.is_typed,
                    policy=policy_desc,
                    round=round_idx,
                    reason=action.reason,
                )
                turn_events.confirmation_actions.append(record)
                calls_before = len(turn_events.tool_calls)
                how = "typing" if action.is_typed else "clicking"
                self._progress(
                    f"confirmation round {round_idx}: {how} "
                    f"{action.action.value} on "
                    f"{action.tool or action.confirmation_id}"
                )
                if action.is_typed:
                    followup = self._send_turn_request(
                        session_id,
                        turn_events,
                        message=action.text,
                        **send_kwargs,
                    )
                else:
                    response: dict[str, Any] = {
                        "confirmationId": action.confirmation_id
                    }
                    if action.confirmed is not None:
                        response["confirmed"] = bool(action.confirmed)
                    else:
                        response["answerText"] = action.text
                    followup = self._send_turn_request(
                        session_id,
                        turn_events,
                        confirmation_response=response,
                        **send_kwargs,
                    )
                record.followup_chars = len(followup)
                record.followup_tool_calls = (
                    len(turn_events.tool_calls) - calls_before
                )
                if followup:
                    chunks.append(("\n\n" if any(chunks) else "") + followup)
                if action.is_typed:
                    # A typed reply resumes every pending card with the same
                    # message, so mark the others as superseded and stop.
                    answered = {
                        a.confirmation_id
                        for a in turn_events.confirmation_actions
                    }
                    for other in pending:
                        if (
                            other.confirmation_id
                            and other.confirmation_id not in answered
                        ):
                            turn_events.confirmation_actions.append(
                                ConfirmationActionRecord(
                                    confirmation_id=other.confirmation_id,
                                    tool=other.tool,
                                    action=action.action.value,
                                    text=action.text,
                                    typed=True,
                                    policy=policy_desc,
                                    round=round_idx,
                                    reason=(
                                        "superseded by typed reply on "
                                        f"{action.confirmation_id}"
                                    ),
                                )
                            )
                    break
        if turn_events.pending_confirmation_requests:
            logger.warning(
                "Reached max_confirmation_rounds (%d); "
                "%d confirmation(s) pending.",
                max_confirmation_rounds,
                len(turn_events.pending_confirmation_requests),
            )

    # ------------------------------------------------------------- public API

    def chat(
        self,
        session_id: str,
        message: str,
        *,
        attached_gcs_uris: Sequence[str] = (),
        confirmation_policy: ConfirmationPolicy | None = None,
        max_confirmation_rounds: int = 5,
        enable_confirmation_cards: bool | None = None,
        timeout_seconds: float = 1800,
        auto_resolve_lro: bool = True,
        lro_timeout_seconds: float = 1800,
        lro_poll_interval_seconds: float = 5,
        max_lro_iterations: int = 10,
    ) -> TurnStreamEvents:
        """Sends a user message and runs the console-equivalent turn loop.

        Pending LROs are polled and resumed (``auto_resolve_lro``) and
        confirmation cards are answered by ``confirmation_policy`` (none or a
        ``ManualPolicy`` leaves them pending for the caller).
        """
        return self._run_turn(
            session_id,
            message=message,
            attached_gcs_uris=attached_gcs_uris,
            enable_confirmation_cards=enable_confirmation_cards,
            confirmation_policy=confirmation_policy,
            max_confirmation_rounds=max_confirmation_rounds,
            timeout_seconds=timeout_seconds,
            auto_resolve_lro=auto_resolve_lro,
            lro_timeout_seconds=lro_timeout_seconds,
            lro_poll_interval_seconds=lro_poll_interval_seconds,
            max_lro_iterations=max_lro_iterations,
        )

    def send_confirmation_response(
        self,
        session_id: str,
        confirmation_id: str,
        *,
        confirmed: bool | None = None,
        answer_text: str | None = None,
        tool: str = "",
        confirmation_policy: ConfirmationPolicy | None = None,
        max_confirmation_rounds: int = 5,
        timeout_seconds: float = 1800,
        auto_resolve_lro: bool = True,
        lro_timeout_seconds: float = 1800,
        lro_poll_interval_seconds: float = 5,
        max_lro_iterations: int = 10,
    ) -> TurnStreamEvents:
        """Answers a confirmation card the way a console click does."""
        if (confirmed is None) == (answer_text is None):
            raise ValueError("Pass exactly one of confirmed or answer_text.")
        response: dict[str, Any] = {"confirmationId": confirmation_id}
        if confirmed is not None:
            response["confirmed"] = bool(confirmed)
            action = "CLICK_CONFIRM" if confirmed else "CLICK_DENY"
            text = ""
        else:
            response["answerText"] = answer_text or ""
            action = "CLICK_ANSWER"
            text = answer_text or ""
        explicit = ConfirmationActionRecord(
            confirmation_id=confirmation_id,
            tool=tool,
            action=action,
            text=text,
            typed=False,
            policy="manual",
            round=0,
            reason="explicit call",
        )
        return self._run_turn(
            session_id,
            confirmation_response=response,
            explicit_action=explicit,
            enable_confirmation_cards=True,
            confirmation_policy=confirmation_policy,
            max_confirmation_rounds=max_confirmation_rounds,
            timeout_seconds=timeout_seconds,
            auto_resolve_lro=auto_resolve_lro,
            lro_timeout_seconds=lro_timeout_seconds,
            lro_poll_interval_seconds=lro_poll_interval_seconds,
            max_lro_iterations=max_lro_iterations,
        )

    # --------------------------------------------------------------- LRO loop

    def poll_evaluation_run(
        self,
        run_name: str,
        *,
        timeout_seconds: float = 1800,
        poll_interval_seconds: float = 5,
    ) -> dict[str, Any]:
        """Polls an evaluation run until it reaches a terminal state."""
        deadline = time.monotonic() + timeout_seconds
        last: dict[str, Any] = {}
        while True:
            last = self.get_evaluation_run(run_name)
            state = str(last.get("state", ""))
            progress = last.get("progress") or {}
            total = int(progress.get("totalCount", 0) or 0)
            failed = int(progress.get("failedCount", 0) or 0)
            passed = int(progress.get("passedCount", 0) or 0)
            completed = int(progress.get("completedCount", 0) or 0)
            if not passed and completed:
                passed = max(completed - failed, 0)
            self._progress(
                f"evaluation run {state or 'RUNNING'}: {completed}/{total} "
                f"(failed {failed})"
            )
            if state in _TERMINAL_RUN_STATES:
                pass_rate = round(100.0 * passed / total, 1) if total else 0.0
                return {
                    "status": "SUCCEEDED" if state == "COMPLETED" else state,
                    "state": state,
                    "evaluation_run": last.get("name", run_name),
                    "pass_rate": pass_rate,
                    "passed_count": passed,
                    "failed_count": failed,
                    "total_count": total,
                }
            if time.monotonic() > deadline:
                return {
                    "status": "TIMEOUT",
                    "state": state,
                    "evaluation_run": run_name,
                    "error": (
                        f"evaluation run not finished after {timeout_seconds}s"
                    ),
                }
            time.sleep(poll_interval_seconds)

    def poll_operation(
        self,
        operation_name: str,
        *,
        timeout_seconds: float = 1800,
        poll_interval_seconds: float = 5,
    ) -> dict[str, Any]:
        """Polls an operation; delegates to the evaluation run when present."""
        deadline = time.monotonic() + timeout_seconds
        while True:
            op = self.get_operation(operation_name)
            metadata = op.get("metadata") or {}
            response = op.get("response") or {}
            run_ref = metadata.get("evaluationRun") or response.get(
                "evaluationRun"
            )
            run_name = (
                run_ref.get("name") if isinstance(run_ref, dict) else run_ref
            )
            if run_name:
                remaining = max(
                    deadline - time.monotonic(), poll_interval_seconds
                )
                return self.poll_evaluation_run(
                    str(run_name),
                    timeout_seconds=remaining,
                    poll_interval_seconds=poll_interval_seconds,
                )
            if op.get("done"):
                error = op.get("error")
                if error:
                    return {
                        "status": "FAILED",
                        "error": error.get("message", str(error)),
                    }
                return {"status": "SUCCEEDED", "response": response}
            if time.monotonic() > deadline:
                return {
                    "status": "TIMEOUT",
                    "error": f"operation not done after {timeout_seconds}s",
                }
            self._progress(
                f"operation running: {operation_name.rsplit('/', 1)[-1]}"
            )
            time.sleep(poll_interval_seconds)

    def poll_detached_job(
        self,
        session_id: str,
        job_id: str,
        *,
        timeout_seconds: float = 1800,
        poll_interval_seconds: float = 5,
    ) -> dict[str, Any]:
        """Polls the session for a detached assistant job's terminal status."""
        deadline = time.monotonic() + timeout_seconds
        while True:
            session = self.get_session(session_id)
            for status in _detached_job_statuses(session):
                if status.get("job_id") != job_id:
                    continue
                state = str(status.get("status", "")).upper()
                if state in (
                    "COMPLETED",
                    "SUCCEEDED",
                    "FAILED",
                    "ERROR",
                    "CANCELLED",
                ):
                    return {
                        "status": "SUCCEEDED"
                        if state in ("COMPLETED", "SUCCEEDED")
                        else state,
                        "memo_path": status.get("memo_path", ""),
                    }
            if time.monotonic() > deadline:
                return {
                    "status": "TIMEOUT",
                    "error": "detached job not finished",
                }
            self._progress(f"detached job running: {job_id}")
            time.sleep(poll_interval_seconds)

    def _resolve_lro_loop(
        self,
        session_id: str,
        turn_events: TurnStreamEvents,
        pending: list[LroOperationInfo],
        *,
        enable_confirmation_cards: bool | None,
        timeout_seconds: float,
        lro_timeout_seconds: float,
        lro_poll_interval_seconds: float,
        max_lro_iterations: int,
    ) -> str:
        """Polls and resumes initiated LROs; returns the resumed text."""
        collected: list[str] = []
        iteration = 0
        while pending and iteration < max_lro_iterations:
            iteration += 1
            current, pending = list(pending), []
            for lro in current:
                lro.status = "PROCESSED"
                self._progress(
                    f"waiting for {lro.operation_type}: "
                    f"{lro.target_resource_name or lro.operation_name}"
                )
                detached = (
                    lro.operation_type == "detached_assistant"
                    or _DETACHED_JOB_PREFIX in lro.operation_name
                )
                if detached:
                    result = self.poll_detached_job(
                        session_id,
                        lro.operation_name,
                        timeout_seconds=lro_timeout_seconds,
                        poll_interval_seconds=lro_poll_interval_seconds,
                    )
                elif "evaluationRuns" in (lro.target_resource_name or ""):
                    result = self.poll_evaluation_run(
                        lro.target_resource_name,
                        timeout_seconds=lro_timeout_seconds,
                        poll_interval_seconds=lro_poll_interval_seconds,
                    )
                elif lro.operation_name:
                    result = self.poll_operation(
                        lro.operation_name,
                        timeout_seconds=lro_timeout_seconds,
                        poll_interval_seconds=lro_poll_interval_seconds,
                    )
                else:
                    result = {"status": "SUCCEEDED"}

                status = str(result.get("status", "SUCCEEDED"))
                self._progress(
                    f"{lro.operation_type} finished ({status}); resuming"
                )
                metadata = {
                    k: result[k]
                    for k in (
                        "pass_rate",
                        "passed_count",
                        "failed_count",
                        "total_count",
                    )
                    if k in result
                }
                target = (
                    result.get("evaluation_run")
                    or result.get("memo_path")
                    or lro.target_resource_name
                )
                event: dict[str, Any] = {
                    "operationName": lro.operation_name,
                    "targetResourceName": target,
                    "operationType": lro.operation_type,
                    "status": status,
                }
                if metadata:
                    event["metadata"] = metadata
                if result.get("error"):
                    event["error"] = {"message": str(result["error"])}

                sub = TurnStreamEvents()
                had_error = False
                for attempt in range(2):
                    try:
                        self.stream_turn(
                            session_id,
                            "",
                            operation_completed_event=event,
                            enable_confirmation_cards=enable_confirmation_cards,
                            timeout_seconds=(
                                max(timeout_seconds, lro_timeout_seconds)
                                if detached
                                else timeout_seconds
                            ),
                            into=sub,
                        )
                        break
                    except ArcError as e:
                        had_error = True
                        logger.warning(
                            "operationCompletedEvent delivery failed "
                            "(attempt %d): %s",
                            attempt + 1,
                            e,
                        )
                        if attempt == 0:
                            time.sleep(5)
                else:
                    continue

                text_before = turn_events.text
                turn_events.merge(sub)
                turn_events.text = text_before
                resumed_text = sub.text
                next_lros = [
                    x for x in sub.initiated_lros if x.status == "PENDING"
                ]

                if _ALREADY_PROCESSED_MARKER in resumed_text:
                    pass_rate = (
                        f" (pass rate: {metadata['pass_rate']}%)"
                        if "pass_rate" in metadata
                        else ""
                    )
                    memo = ""
                    if detached and str(target).startswith("memo/"):
                        memo = (
                            " The detached assistant's output was persisted to "
                            f"memory file '{target}'. Call read_memory_file "
                            "with "
                            f"path='{target}' to inspect it and continue."
                        )
                    fallback = (
                        f"Operation {lro.operation_name} "
                        f"({lro.operation_type}) "
                        f"finished with status {status} on resource {target}"
                        f"{pass_rate}.{memo}"
                    )
                    logger.warning(
                        "Server already processed %s; "
                        "sending fallback message.",
                        lro.operation_name,
                    )
                    fb = TurnStreamEvents()
                    try:
                        self.stream_turn(
                            session_id,
                            fallback,
                            enable_confirmation_cards=enable_confirmation_cards,
                            timeout_seconds=(
                                min(timeout_seconds, 300)
                                if had_error
                                else timeout_seconds
                            ),
                            into=fb,
                        )
                        text_before = turn_events.text
                        turn_events.merge(fb)
                        turn_events.text = text_before
                        resumed_text = fb.text
                        next_lros = [
                            x
                            for x in fb.initiated_lros
                            if x.status == "PENDING"
                        ]
                    except ArcError as e:
                        logger.warning("Fallback resume failed: %s", e)
                        resumed_text = ""
                        next_lros = []

                if resumed_text:
                    collected.append(resumed_text)
                pending.extend(next_lros)

        if pending:
            logger.warning(
                "Reached max_lro_iterations (%d); %d LRO(s) remain unpolled.",
                max_lro_iterations,
                len(pending),
            )
        return "\n\n".join(collected)


def _detached_job_statuses(session: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Yields ``detached_assistant_status`` payloads from session messages."""
    for message in session.get("messages", []) or []:
        for chunk in message.get("chunks", []) or []:
            payload = chunk.get("typedPayload") or {}
            if payload.get("type") != "detached_assistant_status":
                continue
            data = payload.get("payload") or payload.get("data") or {}
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except ValueError:
                    continue
            if isinstance(data, dict):
                yield data
