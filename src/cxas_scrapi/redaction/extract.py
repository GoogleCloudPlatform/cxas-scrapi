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

"""Flattens conversations into checkable leaves and aligns them.

A *leaf* is one scalar value in a conversation: a user utterance, an agent
response or transcript, a tool argument, a field of a tool response, or a
session variable. Persisted conversations (``Conversation`` resources) and
live session responses (``diagnosticInfo.messages``) share the same
message/chunk layout, which lets a raw leaf be paired with its persisted,
redacted counterpart by position.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from cxas_scrapi.redaction.policy import Surface

_USER_ROLES = {"user", "USER", "end_user", "END_USER"}


@dataclasses.dataclass(frozen=True)
class Leaf:
    """One scalar value inside a conversation."""

    turn: int
    message: int
    chunk: int
    role: str
    surface: Surface
    path: str
    """Location inside the chunk, e.g. ``args.card_number``."""
    key: str
    """Innermost key for structured values (``""`` for text)."""
    value: Any

    @property
    def text(self) -> str:
        return _scalar_text(self.value)

    @property
    def is_number(self) -> bool:
        return isinstance(self.value, (int, float)) and not isinstance(
            self.value, bool
        )

    @property
    def location(self) -> tuple[int, int, int, str]:
        return (self.turn, self.message, self.chunk, self.path)

    def describe(self) -> str:
        where = f"turn {self.turn + 1} {self.role} {self.surface.value}"
        return f"{where} [{self.path}]" if self.path else where


def _scalar_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _walk(value: Any, path: str, key: str) -> list[tuple[str, str, Any]]:
    """Yields (path, key, scalar) for every scalar under ``value``."""
    if isinstance(value, dict):
        out = []
        for k, v in value.items():
            out.extend(_walk(v, f"{path}.{k}" if path else str(k), str(k)))
        return out
    if isinstance(value, list):
        out = []
        for i, v in enumerate(value):
            out.extend(_walk(v, f"{path}[{i}]", key))
        return out
    return [(path, key, value)]


def _chunk_leaves(
    chunk: dict[str, Any],
    turn: int,
    m_idx: int,
    c_idx: int,
    role: str,
) -> list[Leaf]:
    is_user = role in _USER_ROLES
    text_surface = Surface.USER_TEXT if is_user else Surface.AGENT_TEXT
    leaves: list[Leaf] = []

    def add(surface: Surface, items: list[tuple[str, str, Any]]) -> None:
        for path, key, value in items:
            if value is None or value == "":
                continue
            leaves.append(
                Leaf(turn, m_idx, c_idx, role, surface, path, key, value)
            )

    # Text and audio transcripts share one path so live/persisted copies of
    # the same utterance align regardless of channel.
    for field in ("text", "transcript"):
        if isinstance(chunk.get(field), str):
            add(text_surface, [("text", "", chunk[field])])

    call = chunk.get("toolCall") or chunk.get("tool_call")
    if isinstance(call, dict):
        add(Surface.TOOL_ARGS, _walk(call.get("args") or {}, "args", ""))

    resp = chunk.get("toolResponse") or chunk.get("tool_response")
    if isinstance(resp, dict):
        add(
            Surface.TOOL_RESPONSE,
            _walk(resp.get("response") or {}, "response", ""),
        )

    for field in ("updatedVariables", "updated_variables"):
        if isinstance(chunk.get(field), dict):
            add(Surface.VARIABLES, _walk(chunk[field], "variables", ""))

    payload = chunk.get("payload")
    if isinstance(payload, dict):
        add(text_surface, _walk(payload, "payload", ""))
    return leaves


def messages_to_leaves(messages: list[dict[str, Any]], turn: int) -> list[Leaf]:
    """Flattens one turn's messages (``[{role, chunks}]``) into leaves."""
    leaves: list[Leaf] = []
    for m_idx, message in enumerate(messages or []):
        role = str(message.get("role", ""))
        for c_idx, chunk in enumerate(message.get("chunks") or []):
            if isinstance(chunk, dict):
                leaves.extend(_chunk_leaves(chunk, turn, m_idx, c_idx, role))
    return leaves


def conversation_to_leaves(conversation: dict[str, Any]) -> list[Leaf]:
    """Flattens a ``Conversation`` resource (as a JSON dict) into leaves."""
    leaves: list[Leaf] = []
    for t_idx, turn in enumerate(conversation.get("turns") or []):
        leaves.extend(messages_to_leaves(turn.get("messages") or [], t_idx))
    return leaves


def session_outputs_to_messages(
    outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extracts the turn's raw messages from live session outputs.

    The output that completes a turn carries ``diagnosticInfo.messages``,
    which mirrors the persisted turn layout with unredacted values.
    """
    messages: list[dict[str, Any]] = []
    for output in outputs or []:
        diag = output.get("diagnosticInfo") or output.get("diagnostic_info")
        if isinstance(diag, dict) and diag.get("messages"):
            messages = diag["messages"]
    return messages


@dataclasses.dataclass(frozen=True)
class AlignedLeaf:
    """A raw leaf and its persisted counterpart (``None`` if missing)."""

    raw: Leaf
    stored: Leaf | None


def align(raw: list[Leaf], stored: list[Leaf]) -> list[AlignedLeaf]:
    """Pairs raw leaves with persisted leaves by location.

    Falls back to (turn, role, surface, path) order when message/chunk
    indexes differ, e.g. because a variables-only message was dropped.
    """
    by_loc = {leaf.location: leaf for leaf in stored}
    fallback: dict[tuple[int, str, Surface, str], list[Leaf]] = {}
    for leaf in stored:
        fallback.setdefault(
            (leaf.turn, leaf.role, leaf.surface, leaf.path), []
        ).append(leaf)

    used: set[int] = set()
    out: list[AlignedLeaf] = []
    for leaf in raw:
        match = by_loc.get(leaf.location)
        if match is not None and match.surface == leaf.surface:
            used.add(id(match))
            out.append(AlignedLeaf(leaf, match))
            continue
        candidates = fallback.get(
            (leaf.turn, leaf.role, leaf.surface, leaf.path), []
        )
        match = next((c for c in candidates if id(c) not in used), None)
        if match is not None:
            used.add(id(match))
        out.append(AlignedLeaf(leaf, match))
    return out
