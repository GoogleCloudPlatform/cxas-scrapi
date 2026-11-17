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

"""Replay redaction checks (offline, deterministic).

Evaluation runs persist their conversations without logging redaction, so
they are a ready-made corpus of *unredacted* transcripts. ``RedactionReplay``
pushes those transcripts through the app's own DLP inspect/deidentify
templates and checks the result against the redaction policy, without
running the agent again.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
from typing import TYPE_CHECKING, Any

from cxas_scrapi.redaction.checker import RedactionReport, check_pairs
from cxas_scrapi.redaction.dlp import DlpClient, ReplayMode
from cxas_scrapi.redaction.extract import (
    AlignedLeaf,
    Leaf,
    conversation_to_leaves,
)
from cxas_scrapi.redaction.policy import Surface

if TYPE_CHECKING:
    from collections.abc import Iterable

    from cxas_scrapi.redaction.policy import RedactionPolicy

_TEXT_SURFACES = {Surface.USER_TEXT, Surface.AGENT_TEXT}


def _to_dict(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        return message
    from google.protobuf.json_format import MessageToDict  # noqa: PLC0415

    pb = getattr(message, "_pb", message)
    return MessageToDict(pb)


def _wire_text(leaf: Leaf) -> str:
    """Text as the logging pipeline sees it (numbers are doubles)."""
    if leaf.is_number:
        return repr(float(leaf.value))
    return leaf.text


def collect_conversation_names(obj: Any) -> list[str]:
    """Finds ``.../conversations/...`` resource names anywhere in ``obj``."""
    names: list[str] = []

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                if (
                    k == "conversation"
                    and isinstance(v, str)
                    and "/conversations/" in v
                ):
                    names.append(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return list(dict.fromkeys(names))


def dedupe_leaves(leaves: Iterable[Leaf]) -> list[Leaf]:
    """Drops repeated values (eval turns replay earlier history)."""
    seen: set[tuple[str, str, str, str]] = set()
    out = []
    for leaf in leaves:
        key = (leaf.role, leaf.surface.value, leaf.path, leaf.text)
        if key not in seen:
            seen.add(key)
            out.append(leaf)
    return out


class RedactionReplay:
    """Replays raw transcripts through an app's DLP templates."""

    def __init__(
        self,
        policy: RedactionPolicy,
        app_name: str | None = None,
        inspect_template: str | None = None,
        deidentify_template: str | None = None,
        mode: ReplayMode | str = ReplayMode.PLATFORM,
        dlp_client: DlpClient | None = None,
        creds: Any = None,
    ) -> None:
        self.policy = policy
        self.app_name = app_name
        self.mode = ReplayMode(mode)
        self.creds = creds
        if dlp_client is None:
            if not (inspect_template or deidentify_template):
                inspect_template, deidentify_template = (
                    self._templates_from_app()
                )
            dlp_client = DlpClient(
                inspect_template=inspect_template or "",
                deidentify_template=deidentify_template or "",
                credentials=creds,
            )
        self.dlp = dlp_client

    def _templates_from_app(self) -> tuple[str, str]:
        if not self.app_name:
            raise ValueError(
                "pass app_name or explicit inspect/deidentify templates"
            )
        from cxas_scrapi.core.apps import Apps  # noqa: PLC0415

        parts = self.app_name.split("/")
        apps = Apps(project_id=parts[1], location=parts[3], creds=self.creds)
        cfg = apps.get_app(self.app_name).logging_settings.redaction_config
        if not (cfg.inspect_template or cfg.deidentify_template):
            raise ValueError(
                f"{self.app_name} has no redaction templates configured "
                "(loggingSettings.redactionConfig)"
            )
        return cfg.inspect_template, cfg.deidentify_template

    def _redact(self, leaves: list[Leaf]) -> list[str]:
        if self.mode == ReplayMode.PLATFORM:
            return self.dlp.deidentify_values([_wire_text(x) for x in leaves])

        text_idx = [
            i for i, x in enumerate(leaves) if x.surface in _TEXT_SURFACES
        ]
        struct_idx = [i for i in range(len(leaves)) if i not in set(text_idx)]
        out = [""] * len(leaves)

        if self.mode == ReplayMode.CONVERSATION:
            redacted_text = self.dlp.deidentify_conversation(
                [(leaves[i].role, leaves[i].text) for i in text_idx]
            )
        else:
            redacted_text = self.dlp.deidentify_values(
                [leaves[i].text for i in text_idx]
            )
        for i, v in zip(text_idx, redacted_text, strict=True):
            out[i] = v

        headers = (
            [leaves[i].key or leaves[i].path for i in struct_idx]
            if self.mode == ReplayMode.KEY_AWARE
            else None
        )
        redacted_struct = self.dlp.deidentify_values(
            [_wire_text(leaves[i]) for i in struct_idx], headers=headers
        )
        for i, v in zip(struct_idx, redacted_struct, strict=True):
            out[i] = v
        return out

    def replay_leaves(
        self,
        leaves: list[Leaf],
        source: str,
        require: Iterable[str] = (),
    ) -> RedactionReport:
        """Redacts ``leaves`` with DLP and checks them against the policy."""
        surfaces = set(self.policy.surfaces)
        leaves = [x for x in leaves if x.surface in surfaces]
        redacted = self._redact(leaves)
        pairs = [
            AlignedLeaf(x, dataclasses.replace(x, value=r))
            for x, r in zip(leaves, redacted, strict=True)
        ]
        report = check_pairs(
            pairs,
            self.policy,
            source=source,
            mode=f"replay:{self.mode.value}",
            require=require,
        )
        report.metadata.update(
            {
                "inspect_template": self.dlp.inspect_template,
                "deidentify_template": self.dlp.deidentify_template,
            }
        )
        return report

    def replay_conversation(
        self,
        conversation: dict[str, Any] | Any,
        source: str | None = None,
        require: Iterable[str] = (),
    ) -> RedactionReport:
        conv = _to_dict(conversation)
        return self.replay_leaves(
            conversation_to_leaves(conv),
            source=source or conv.get("name", "conversation"),
            require=require,
        )

    def replay_conversation_ids(
        self,
        conversation_ids: Iterable[str],
        require: Iterable[str] = (),
        dedupe: bool = False,
        source: str | None = None,
    ) -> RedactionReport:
        """Fetches conversations and replays them.

        With ``dedupe`` the conversations are treated as views of one
        dialog (as produced by golden turn replays) and checked together.
        """
        if not self.app_name:
            raise ValueError("app_name is required to fetch conversations")
        from cxas_scrapi.core.conversation_history import (  # noqa: PLC0415
            ConversationHistory,
        )

        history = ConversationHistory(app_name=self.app_name, creds=self.creds)
        convs = [
            _to_dict(history.get_conversation(c)) for c in conversation_ids
        ]
        if dedupe:
            leaves = dedupe_leaves(
                leaf for c in convs for leaf in conversation_to_leaves(c)
            )
            return self.replay_leaves(
                leaves,
                source=source or (convs[0].get("name", "") if convs else ""),
                require=require,
            )
        report: RedactionReport | None = None
        for conv in convs:
            r = self.replay_conversation(conv, require=require)
            report = r if report is None else report.merge(r)
        return report or RedactionReport(
            mode=f"replay:{self.mode.value}", policy=self.policy
        )

    def replay_evaluation_results(
        self,
        results: Iterable[dict[str, Any] | Any],
        require: Iterable[str] = (),
    ) -> RedactionReport:
        """Replays the conversations behind evaluation results."""
        report = RedactionReport(
            mode=f"replay:{self.mode.value}", policy=self.policy
        )
        report.sources.clear()
        for result in results:
            res = _to_dict(result)
            names = collect_conversation_names(res)
            if not names:
                continue
            label = res.get("displayName") or res.get("name") or names[0]
            report.merge(
                self.replay_conversation_ids(
                    names, require=require, dedupe=True, source=label
                )
            )
        return report

    def replay_evaluation_run(
        self, evaluation_run: str, require: Iterable[str] = ()
    ) -> RedactionReport:
        """Replays every result of an evaluation run."""
        if not self.app_name:
            raise ValueError("app_name is required to read evaluation runs")
        from cxas_scrapi.core.evaluations import Evaluations  # noqa: PLC0415

        if "/evaluationRuns/" not in evaluation_run:
            evaluation_run = f"{self.app_name}/evaluationRuns/{evaluation_run}"
        ev = Evaluations(app_name=self.app_name, creds=self.creds)
        results = ev.list_evaluation_results_by_run(evaluation_run)
        report = self.replay_evaluation_results(results, require=require)
        report.metadata["evaluation_run"] = evaluation_run
        return report


def load_conversation_file(path: str | pathlib.Path) -> dict[str, Any]:
    """Loads an exported conversation JSON (``Conversation`` resource)."""
    with pathlib.Path(path).open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or "turns" not in data:
        raise ValueError(f"{path}: not a conversation export (no 'turns')")
    return data
