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

"""Live redaction checks (end-to-end, through the real platform pathway).

A session is run through the Sessions API. Each turn's live response carries
the turn's messages unredacted (``diagnosticInfo``), while the conversation
persisted for the same session has logging redaction applied. Comparing the
two field by field shows exactly what the platform redacted, including
audio transcripts and structured tool/variable values.
"""

from __future__ import annotations

import dataclasses
import pathlib
import time
import uuid
from typing import TYPE_CHECKING, Any

import pydantic
import yaml

from cxas_scrapi.redaction.checker import RedactionReport, check_pairs
from cxas_scrapi.redaction.extract import (
    align,
    conversation_to_leaves,
    messages_to_leaves,
    session_outputs_to_messages,
)
from cxas_scrapi.redaction.policy import RedactionExpectations

if TYPE_CHECKING:
    from collections.abc import Iterable

    from cxas_scrapi.redaction.policy import RedactionPolicy


def response_to_outputs(response: Any) -> list[dict[str, Any]]:
    """Converts a session response into a list of output dicts."""
    if response is None:
        return []
    if isinstance(response, dict):
        return list(response.get("outputs") or [])
    from google.protobuf.json_format import MessageToDict  # noqa: PLC0415

    original = getattr(response, "original_response", response)
    pb = getattr(original, "_pb", original)
    try:
        return list(MessageToDict(pb).get("outputs") or [])
    except Exception:
        return []


def response_to_turn_messages(response: Any) -> list[dict[str, Any]]:
    """Returns the raw (unredacted) messages for the response's turn."""
    return session_outputs_to_messages(response_to_outputs(response))


class LiveScriptConversation(pydantic.BaseModel):
    """A scripted conversation for ``cxas redaction live``."""

    model_config = pydantic.ConfigDict(extra="forbid")

    name: str
    turns: list[str]
    variables: dict[str, Any] = pydantic.Field(default_factory=dict)
    redaction_expectations: Any = None

    @pydantic.field_validator("turns", mode="before")
    @classmethod
    def _flatten_turns(cls, v: Any) -> Any:
        if isinstance(v, list):
            out = []
            for t in v:
                text = (
                    (t.get("user") or t.get("text") or "")
                    if isinstance(t, dict)
                    else t
                )
                out.append(str(text))
            return out
        return v


def load_live_script(path: str | pathlib.Path) -> list[LiveScriptConversation]:
    """Loads a YAML script: ``conversations: [{name, turns, variables}]``."""
    with pathlib.Path(path).open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    items = data.get("conversations") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError(f"{path}: expected a 'conversations' list")
    common = (
        data.get("common_redaction_expectations")
        if isinstance(data, dict)
        else None
    )
    convs = []
    for item in items:
        conv = LiveScriptConversation.model_validate(item)
        if conv.redaction_expectations is None and common is not None:
            conv.redaction_expectations = common
        convs.append(conv)
    return convs


@dataclasses.dataclass
class LiveSessionCapture:
    """Raw per-turn messages captured while running a session."""

    session_id: str
    turn_messages: list[list[dict[str, Any]]] = dataclasses.field(
        default_factory=list
    )


class LiveRedactionCheck:
    """Compares live (raw) session turns with the persisted conversation."""

    def __init__(
        self,
        app_name: str,
        policy: RedactionPolicy,
        creds: Any = None,
        deployment_id: str | None = None,
        persist_timeout_s: float = 90.0,
        poll_interval_s: float = 3.0,
    ) -> None:
        self.app_name = app_name
        self.policy = policy
        self.creds = creds
        self.deployment_id = deployment_id
        self.persist_timeout_s = persist_timeout_s
        self.poll_interval_s = poll_interval_s
        self._history = None
        self._sessions = None

    @property
    def history(self) -> Any:
        if self._history is None:
            from cxas_scrapi.core.conversation_history import (  # noqa: PLC0415
                ConversationHistory,
            )

            self._history = ConversationHistory(
                app_name=self.app_name, creds=self.creds
            )
        return self._history

    @property
    def sessions(self) -> Any:
        if self._sessions is None:
            from cxas_scrapi.core.sessions import Sessions  # noqa: PLC0415

            self._sessions = Sessions(
                app_name=self.app_name,
                deployment_id=self.deployment_id,
                creds=self.creds,
            )
        return self._sessions

    def fetch_persisted(
        self, session_id: str, min_turns: int = 1
    ) -> dict[str, Any]:
        """Waits for the persisted conversation to contain ``min_turns``."""
        from google.protobuf.json_format import MessageToDict  # noqa: PLC0415

        deadline = time.monotonic() + self.persist_timeout_s
        last_error: Exception | None = None
        conv: dict[str, Any] = {}
        while True:
            try:
                conv = MessageToDict(
                    self.history.get_conversation(session_id)._pb
                )
                if len(conv.get("turns") or []) >= min_turns:
                    return conv
            except Exception as e:  # not persisted yet
                last_error = e
            if time.monotonic() >= deadline:
                break
            time.sleep(self.poll_interval_s)
        if conv:
            return conv
        raise TimeoutError(
            f"conversation {session_id} was not persisted within "
            f"{self.persist_timeout_s:.0f}s: {last_error}"
        )

    def check_capture(
        self,
        capture: LiveSessionCapture,
        require: Iterable[str] = (),
        source: str | None = None,
        policy: RedactionPolicy | None = None,
    ) -> RedactionReport:
        """Checks captured raw turns against the persisted conversation."""
        policy = policy or self.policy
        raw = [
            leaf
            for t_idx, msgs in enumerate(capture.turn_messages)
            for leaf in messages_to_leaves(msgs, t_idx)
        ]
        persisted = self.fetch_persisted(
            capture.session_id, min_turns=len(capture.turn_messages)
        )
        stored = conversation_to_leaves(persisted)
        pairs = align(raw, stored)
        report = check_pairs(
            pairs,
            policy,
            source=source or capture.session_id,
            mode="live",
            require=require,
        )
        unmatched = sum(1 for p in pairs if p.stored is None)
        report.metadata.update(
            {
                "session_id": capture.session_id,
                "turns": len(capture.turn_messages),
                "unmatched_leaves": unmatched,
            }
        )
        return report

    def run_conversation(
        self,
        turns: list[str],
        variables: dict[str, Any] | None = None,
        modality: str = "text",
        session_id: str | None = None,
    ) -> LiveSessionCapture:
        """Runs scripted user turns and captures the raw turn messages."""
        session_id = session_id or f"redaction-{uuid.uuid4().hex[:12]}"
        capture = LiveSessionCapture(session_id=session_id)
        for idx, text in enumerate(turns):
            kwargs: dict[str, Any] = {"modality": modality}
            if idx == 0 and variables:
                kwargs["variables"] = variables
            response = self.sessions.run(
                session_id=session_id, text=text, **kwargs
            )
            capture.turn_messages.append(response_to_turn_messages(response))
        return capture

    def run_script(
        self,
        conversations: Iterable[LiveScriptConversation],
        modality: str = "text",
        base_dir: str | pathlib.Path | None = None,
    ) -> RedactionReport:
        """Runs scripted conversations and checks each one."""
        report = RedactionReport(mode="live", policy=self.policy)
        report.sources.clear()
        for conv in conversations:
            expectations = RedactionExpectations.parse(
                conv.redaction_expectations
            )
            policy = self.policy
            require: list[str] = []
            if expectations is not None:
                policy = (
                    expectations.resolve_policy(base_dir)
                    if expectations.policy
                    else self.policy.with_values(expectations.values)
                )
                require = expectations.require
            capture = self.run_conversation(
                conv.turns, conv.variables, modality=modality
            )
            report.merge(
                self.check_capture(
                    capture,
                    require=require,
                    source=f"{conv.name} ({capture.session_id})",
                    policy=policy,
                )
            )
        return report
