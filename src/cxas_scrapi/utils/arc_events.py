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

"""Structured events streamed by the CXAS AI assistant (Arc).

``StreamChatAiAssistant`` streams a sequence of JSON
``StreamChatAiAssistantResponse`` objects. :class:`TurnStreamReducer` folds
them into a :class:`TurnStreamEvents` aggregate that the CLI renders as
markdown or JSON.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from cxas_scrapi.utils import arc_lro

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from cxas_scrapi.utils.arc_lro import LroOperationInfo

__all__ = [
    "ArtifactRecord",
    "ConfirmationActionRecord",
    "ConfirmationRequestRecord",
    "ContractMetadataRecord",
    "OptionChoiceRecord",
    "OptionQuestionRecord",
    "StatusRecord",
    "ToolCallRecord",
    "ToolResponseRecord",
    "TurnCompletedRecord",
    "TurnHandoffRecord",
    "TurnStreamEvents",
    "TurnStreamReducer",
    "parse_confirmation_request",
    "parse_option_questions",
]


@dataclasses.dataclass
class OptionChoiceRecord:
    """A selectable option in a clarification question."""

    title: str
    details: str = ""
    submit_text: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> OptionChoiceRecord:
        """Builds an option record from camelCase or snake_case JSON."""
        return cls(
            title=str(data.get("title", "")),
            details=str(data.get("details", "")),
            submit_text=str(
                data.get("submitText") or data.get("submit_text") or ""
            ),
        )


@dataclasses.dataclass
class OptionQuestionRecord:
    """A clarification question with selectable options."""

    question: str
    options: list[OptionChoiceRecord] = dataclasses.field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> OptionQuestionRecord:
        """Builds a question record from camelCase or snake_case JSON."""
        return cls(
            question=str(data.get("question", "")),
            options=[
                OptionChoiceRecord.from_dict(o)
                for o in data.get("options") or []
                if isinstance(o, dict)
            ],
        )


@dataclasses.dataclass
class ConfirmationRequestRecord:
    """A streamed confirmation card (``AssistantConfirmationRequest``)."""

    confirmation_id: str
    context: str = ""
    positive_label: str = ""
    negative_label: str = ""
    questions: list[OptionQuestionRecord] = dataclasses.field(
        default_factory=list
    )
    tool: str = ""
    agent_name: str = ""
    # RFC 3339; empty when the card does not expire.
    expire_time: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ConfirmationRequestRecord:
        """Builds a card record from camelCase wire JSON or saved snake_case."""
        return cls(
            confirmation_id=str(
                data.get("confirmationId") or data.get("confirmation_id") or ""
            ),
            context=str(data.get("context", "")),
            positive_label=str(
                data.get("positiveLabel") or data.get("positive_label") or ""
            ),
            negative_label=str(
                data.get("negativeLabel") or data.get("negative_label") or ""
            ),
            questions=parse_option_questions(data.get("questions") or []),
            tool=str(data.get("tool", "")),
            agent_name=str(
                data.get("agentName") or data.get("agent_name") or ""
            ),
            expire_time=str(
                data.get("expireTime") or data.get("expire_time") or ""
            ),
        )


@dataclasses.dataclass
class ConfirmationActionRecord:
    """How the client answered a confirmation card.

    Attributes:
        confirmation_id: The card that was answered.
        tool: The gated tool, when known.
        action: A ``ConfirmationActionType`` value (``CLICK_CONFIRM``,
            ``CLICK_DENY``, ``CLICK_ANSWER`` or ``TYPE_TEXT``).
        text: Answer text (``CLICK_ANSWER``) or the typed message
            (``TYPE_TEXT``).
        typed: True when sent as a plain composer message instead of a click.
        policy: The policy that chose the action (``manual`` for explicit
            calls).
        round: 1-based confirmation round within the turn (0 for explicit
            calls).
        reason: Why the policy chose this action.
        followup_chars: Characters of assistant text streamed after this
            answer (-1 when unknown).
        followup_tool_calls: Tool calls streamed after this answer (-1 when
            unknown).
    """

    confirmation_id: str
    tool: str = ""
    action: str = ""
    text: str = ""
    typed: bool = False
    policy: str = ""
    round: int = 0
    reason: str = ""
    followup_chars: int = -1
    followup_tool_calls: int = -1


@dataclasses.dataclass
class ArtifactRecord:
    """A streamed document artifact (for example an agent contract)."""

    artifact_id: str
    display_name: str = ""
    mime_type: str = ""
    content: str = ""
    gcs_uri: str = ""
    state: str = ""


@dataclasses.dataclass
class ToolCallRecord:
    """A tool call event streamed by the assistant."""

    id: str = ""
    tool: str = ""
    display_name: str = ""
    args: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class ToolResponseRecord:
    """A tool response event streamed by the assistant."""

    id: str = ""
    tool: str = ""
    display_name: str = ""
    response: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class ContractMetadataRecord:
    """Contract drafting flags carried in ``TurnMetadata``."""

    contract_related: bool = False
    contract_finalized: bool = False
    contract_draft_complete: bool = False


@dataclasses.dataclass
class StatusRecord:
    """A streamed ``google.rpc.Status`` frame (``code != 0`` is an error)."""

    code: int = 0
    message: str = ""
    details: list[dict[str, Any]] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class TurnHandoffRecord:
    """A ``handoff`` frame advising the client to reconnect cleanly."""

    turn_id: str = ""
    reason: str = ""


@dataclasses.dataclass
class TurnCompletedRecord:
    """The terminal ``turnCompleted`` event of a stream."""

    turn_id: str = ""
    reason: str = ""
    final_sequence_number: int | None = None


@dataclasses.dataclass
class TurnStreamEvents:
    """Aggregates all structured events emitted during an assistant turn."""

    text: str = ""
    thoughts: list[str] = dataclasses.field(default_factory=list)
    statuses: list[str] = dataclasses.field(default_factory=list)
    errors: list[StatusRecord] = dataclasses.field(default_factory=list)
    tool_calls: list[ToolCallRecord] = dataclasses.field(default_factory=list)
    tool_responses: list[ToolResponseRecord] = dataclasses.field(
        default_factory=list
    )
    artifacts: list[ArtifactRecord] = dataclasses.field(default_factory=list)
    option_questions: list[OptionQuestionRecord] = dataclasses.field(
        default_factory=list
    )
    confirmation_requests: list[ConfirmationRequestRecord] = dataclasses.field(
        default_factory=list
    )
    confirmation_actions: list[ConfirmationActionRecord] = dataclasses.field(
        default_factory=list
    )
    contract_metadata: ContractMetadataRecord | None = None
    initiated_lros: list[LroOperationInfo] = dataclasses.field(
        default_factory=list
    )
    handoff: TurnHandoffRecord | None = None
    turn_completed: TurnCompletedRecord | None = None
    last_sequence_number: int | None = None

    @property
    def pending_confirmation_requests(
        self,
    ) -> list[ConfirmationRequestRecord]:
        """Cards raised in this turn that the client has not answered yet."""
        answered = {a.confirmation_id for a in self.confirmation_actions}
        pending: list[ConfirmationRequestRecord] = []
        seen: set[str] = set()
        for cr in self.confirmation_requests:
            cid = cr.confirmation_id
            if not cid or cid in answered or cid in seen:
                continue
            seen.add(cid)
            pending.append(cr)
        return pending

    @property
    def pending_option_questions(self) -> list[OptionQuestionRecord]:
        """Standalone option questions not owned by a confirmation card."""
        card_questions: set[tuple[str, tuple[str, ...]]] = set()
        for cr in self.confirmation_requests:
            for q in cr.questions:
                card_questions.add(
                    (q.question, tuple(o.title for o in q.options))
                )
        return [
            q
            for q in self.option_questions
            if (q.question, tuple(o.title for o in q.options))
            not in card_questions
        ]

    def merge(self, other: TurnStreamEvents) -> None:
        """Merges events from a subsequent stream (e.g. LRO resumption)."""
        if other.text:
            self.text = (
                f"{self.text}\n\n{other.text}" if self.text else other.text
            )
        self.thoughts.extend(other.thoughts)
        self.statuses.extend(other.statuses)
        self.errors.extend(other.errors)
        self.tool_calls.extend(other.tool_calls)
        self.tool_responses.extend(other.tool_responses)
        for art in other.artifacts:
            existing = next(
                (
                    a
                    for a in self.artifacts
                    if a.artifact_id and a.artifact_id == art.artifact_id
                ),
                None,
            )
            if existing is None:
                self.artifacts.append(dataclasses.replace(art))
                continue
            for field in (
                "display_name",
                "mime_type",
                "content",
                "gcs_uri",
                "state",
            ):
                value = getattr(art, field)
                if value:
                    setattr(existing, field, value)
        self.option_questions.extend(other.option_questions)
        self.confirmation_requests.extend(other.confirmation_requests)
        self.confirmation_actions.extend(other.confirmation_actions)
        if other.contract_metadata is not None:
            self.contract_metadata = other.contract_metadata
        for lro in other.initiated_lros:
            if lro not in self.initiated_lros:
                self.initiated_lros.append(lro)
        if other.handoff is not None:
            self.handoff = other.handoff
        if other.turn_completed is not None:
            self.turn_completed = other.turn_completed
        if other.last_sequence_number is not None:
            self.last_sequence_number = other.last_sequence_number

    def to_dict(self) -> dict[str, Any]:
        """Serializes the turn events to a JSON-compatible dictionary."""
        return dataclasses.asdict(self)

    def format_markdown(
        self, include_thoughts: bool = False, include_tools: bool = True
    ) -> str:
        """Renders the turn as markdown sections, mirroring the console."""
        sections: list[str] = []
        if include_thoughts and self.thoughts:
            body = "\n\n".join(t.strip() for t in self.thoughts if t.strip())
            if body:
                sections.append(f"### [Thoughts]\n{body}")
        if include_tools and (self.tool_calls or self.tool_responses):
            lines = ["### [Tool Activity]"]
            for tc in self.tool_calls:
                name = tc.display_name or tc.tool or tc.id or "tool"
                lines.append(f"- **Call** `{name}`: {_compact_json(tc.args)}")
            for tr in self.tool_responses:
                name = tr.display_name or tr.tool or tr.id or "tool"
                lines.append(
                    f"- **Response** `{name}`: {_compact_json(tr.response)}"
                )
            sections.append("\n".join(lines))
        if self.text.strip():
            sections.append(self.text.strip())
        if self.errors:
            lines = ["### [Errors]"]
            for err in self.errors:
                msg = err.message or f"code={err.code}"
                lines.append(f"- `code={err.code}`: {msg}")
            sections.append("\n".join(lines))
        for art in self.artifacts:
            meta = [f"state={art.state or 'UNKNOWN'}"]
            if art.mime_type:
                meta.append(f"mime={art.mime_type}")
            if art.gcs_uri:
                meta.append(f"gcs_uri={art.gcs_uri}")
            title = art.display_name or art.artifact_id or "artifact"
            header = f"### [Artifact] {title} ({', '.join(meta)})"
            sections.append(
                f"{header}\n{art.content.rstrip()}" if art.content else header
            )
        if self.contract_metadata is not None and (
            self.contract_metadata.contract_related
            or self.contract_metadata.contract_finalized
            or self.contract_metadata.contract_draft_complete
        ):
            cm = self.contract_metadata
            sections.append(
                "### [Contract Progress]\n"
                f"- Contract related: {cm.contract_related}\n"
                f"- Draft complete: {cm.contract_draft_complete}\n"
                f"- Finalized: {cm.contract_finalized}"
            )
        if self.confirmation_actions:
            lines = ["### [Confirmation Actions]"]
            for a in self.confirmation_actions:
                how = "typed" if a.typed else "clicked"
                detail = f' "{a.text}"' if a.text else ""
                tool = f" ({a.tool})" if a.tool else ""
                lines.append(
                    f"- `{a.confirmation_id}`{tool}: {a.action}{detail} "
                    f"[{how}, policy={a.policy or 'manual'}, round={a.round}]"
                )
            sections.append("\n".join(lines))
        pending_cards = self.pending_confirmation_requests
        if pending_cards:
            lines = ["### [Confirmation Requests] ⚠️ awaiting your answer"]
            for cr in pending_cards:
                lines.append(f"- **Card** `{cr.confirmation_id}`")
                if cr.tool:
                    lines.append(f"  - Tool: `{cr.tool}`")
                if cr.agent_name:
                    lines.append(f"  - Agent: `{cr.agent_name}`")
                if cr.context:
                    lines.append(f"  - Context: {cr.context.strip()}")
                if cr.questions:
                    for qi, q in enumerate(cr.questions, start=1):
                        lines.append(f"  - Q{qi}: {q.question}")
                        lines.extend(_format_options(q.options, indent="    "))
                labels = []
                if cr.positive_label:
                    labels.append(f'confirm="{cr.positive_label}"')
                if cr.negative_label:
                    labels.append(f'decline="{cr.negative_label}"')
                if labels:
                    lines.append(f"  - Buttons: {', '.join(labels)}")
                if cr.expire_time:
                    lines.append(f"  - Expires: {cr.expire_time}")
            sections.append("\n".join(lines))
        pending_options = self.pending_option_questions
        if pending_options:
            lines = ["### [Clarification Options]"]
            for qi, q in enumerate(pending_options, start=1):
                lines.append(f"{qi}. {q.question}")
                lines.extend(_format_options(q.options, indent="   "))
            sections.append("\n".join(lines))
        if self.turn_completed is not None:
            tc = self.turn_completed
            notes: list[str] = []
            if tc.reason and tc.reason != "COMPLETED":
                notes.append(f"> Note: turn ended with reason `{tc.reason}`.")
            if (
                tc.final_sequence_number is not None
                and self.last_sequence_number is not None
                and tc.final_sequence_number > self.last_sequence_number
            ):
                notes.append(
                    "> Note: stream missed events up to sequence "
                    f"{tc.final_sequence_number} "
                    f"(last seen: {self.last_sequence_number})."
                )
            if notes:
                sections.append("\n".join(notes))
        return "\n\n".join(sections)


def _format_options(
    options: Iterable[OptionChoiceRecord], indent: str
) -> list[str]:
    lines = []
    for oi, opt in enumerate(options):
        letter = chr(ord("A") + oi) if oi < 26 else str(oi + 1)
        line = f"{indent}[{letter}] {opt.title}"
        if opt.details:
            line += f" — {opt.details}"
        if opt.submit_text:
            line += f' *(submit: "{opt.submit_text}")*'
        lines.append(line)
    return lines


def _compact_json(value: Any, limit: int = 400) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = str(value)
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return f"`{text}`"


def parse_option_questions(
    raw: Iterable[Mapping[str, Any]],
) -> list[OptionQuestionRecord]:
    """Converts ``OptionQuestionsChunk.questions`` JSON into records."""
    return [
        OptionQuestionRecord.from_dict(q)
        for q in raw or []
        if isinstance(q, Mapping)
    ]


def parse_confirmation_request(
    cr: Mapping[str, Any],
) -> ConfirmationRequestRecord:
    """Converts an ``AssistantConfirmationRequest`` JSON into a record."""
    return ConfirmationRequestRecord.from_dict(cr)


class TurnStreamReducer:
    """Folds ``StreamChatAiAssistantResponse`` JSON objects into events.

    Args:
        on_progress: Optional callback receiving short human-readable progress
            strings (tool names, statuses) while the stream is consumed.
    """

    def __init__(self, on_progress: Callable[[str], None] | None = None):
        self.events = TurnStreamEvents()
        self._artifacts: dict[str, ArtifactRecord] = {}
        self._on_progress = on_progress
        self._in_snapshot = False

    def _progress(self, message: str) -> None:
        if self._on_progress is not None:
            self._on_progress(message)

    def _add_lro(self, lro: LroOperationInfo | None) -> None:
        if lro is not None and lro not in self.events.initiated_lros:
            self.events.initiated_lros.append(lro)

    def feed(self, resp: dict[str, Any]) -> None:
        """Consumes one response object (camelCase JSON)."""
        ev = self.events
        seq: int | None = None
        if "sequenceNumber" in resp:
            with contextlib.suppress(TypeError, ValueError):
                seq = int(resp["sequenceNumber"])
        if (
            not self._in_snapshot
            and seq is not None
            and ev.last_sequence_number is not None
            and seq <= ev.last_sequence_number
            and "turnCompleted" not in resp
            and "resumeSnapshot" not in resp
            and "handoff" not in resp
        ):
            return
        if seq is not None:
            ev.last_sequence_number = seq
        tm = resp.get("turnMetadata")
        if isinstance(tm, dict) and tm:
            ev.contract_metadata = ContractMetadataRecord(
                contract_related=bool(tm.get("contractRelated")),
                contract_finalized=bool(tm.get("contractFinalized")),
                contract_draft_complete=bool(tm.get("contractDraftComplete")),
            )

        if "textChunk" in resp:
            chunk = str(resp["textChunk"])
            ev.text += chunk
            self._add_lro(arc_lro.extract_from_text(chunk))
        elif "thoughtChunk" in resp:
            ev.thoughts.append(str(resp["thoughtChunk"]))
        elif "status" in resp:
            status = resp.get("status") or {}
            raw_code = status.get("code", 0)
            try:
                code = int(raw_code or 0)
            except (TypeError, ValueError):
                code = 1
            msg = str(status.get("message") or f"code={code}")
            ev.statuses.append(msg)
            if code != 0:
                raw_details = status.get("details") or []
                details = [dict(d) for d in raw_details if isinstance(d, dict)]
                ev.errors.append(
                    StatusRecord(code=code, message=msg, details=details)
                )
            self._progress(f"status: {msg}")
        elif "toolCall" in resp:
            tc = resp["toolCall"] or {}
            record = ToolCallRecord(
                id=str(tc.get("id", "")),
                tool=str(tc.get("tool", "")),
                display_name=str(tc.get("displayName", "")),
                args=dict(tc.get("args") or {}),
            )
            ev.tool_calls.append(record)
            self._progress(f"tool: {record.display_name or record.tool}")
        elif "toolResponse" in resp:
            tr = resp["toolResponse"] or {}
            record = ToolResponseRecord(
                id=str(tr.get("id", "")),
                tool=str(tr.get("tool", "")),
                display_name=str(tr.get("displayName", "")),
                response=dict(tr.get("response") or {}),
            )
            ev.tool_responses.append(record)
            self._add_lro(
                arc_lro.extract_from_tool_response(record.tool, record.response)
            )
        elif "artifactChunk" in resp:
            self._merge_artifact(resp["artifactChunk"] or {})
        elif "optionQuestionsChunk" in resp:
            oq = resp["optionQuestionsChunk"] or {}
            ev.option_questions.extend(
                parse_option_questions(oq.get("questions", []))
            )
        elif "confirmationRequest" in resp:
            record = parse_confirmation_request(
                resp["confirmationRequest"] or {}
            )
            ev.confirmation_requests.append(record)
            ev.option_questions.extend(record.questions)
            self._progress(
                f"confirmation card: {record.tool or record.confirmation_id}"
            )
        elif "resumeSnapshot" in resp:
            snap = resp.get("resumeSnapshot") or {}
            self.events = TurnStreamEvents()
            self._artifacts = {}
            prev_in_snapshot = self._in_snapshot
            self._in_snapshot = True
            try:
                for inner in snap.get("events", []) or []:
                    self.feed(inner)
            finally:
                self._in_snapshot = prev_in_snapshot
            resolved_seq = snap.get("resolvedSequenceNumber")
            if resolved_seq is not None:
                with contextlib.suppress(TypeError, ValueError):
                    self.events.last_sequence_number = int(resolved_seq)
            elif seq is not None and (
                self.events.last_sequence_number is None
                or seq > self.events.last_sequence_number
            ):
                self.events.last_sequence_number = seq
        elif "handoff" in resp:
            ho = resp.get("handoff") or {}
            ev.handoff = TurnHandoffRecord(
                turn_id=str(ho.get("turnId", "")),
                reason=str(ho.get("reason", "")),
            )
            self._progress("stream handoff; reconnecting")
        elif "turnCompleted" in resp:
            tc = resp["turnCompleted"] or {}
            final_seq = tc.get("finalSequenceNumber")
            ev.turn_completed = TurnCompletedRecord(
                turn_id=str(tc.get("turnId", "")),
                reason=str(tc.get("reason", "")),
                final_sequence_number=(
                    int(final_seq) if final_seq is not None else None
                ),
            )

    def _merge_artifact(self, ac: dict[str, Any]) -> None:
        key = str(ac.get("artifactId") or "default")
        record = self._artifacts.get(key)
        if record is None:
            record = ArtifactRecord(artifact_id=str(ac.get("artifactId", "")))
            self._artifacts[key] = record
            self.events.artifacts.append(record)
        if ac.get("displayName"):
            record.display_name = str(ac["displayName"])
        if ac.get("mimeType"):
            record.mime_type = str(ac["mimeType"])
        if ac.get("contentDelta"):
            record.content += str(ac["contentDelta"])
        if ac.get("gcsUri"):
            record.gcs_uri = str(ac["gcsUri"])
        if ac.get("state"):
            record.state = str(ac["state"])
