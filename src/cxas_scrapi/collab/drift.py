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

"""Stateless CES-side drift detection for ``cxas push``.

Stores the last-pushed baseline (``cxas.last_push``) on the target CES App's
``App.metadata`` rather than a local file so stateless CI runners and multiple
developers share one authoritative baseline with zero Git merge conflicts.
"""

from __future__ import annotations

import dataclasses
import datetime
import re
import subprocess
from typing import Any

from cxas_scrapi.collab.metadata import AppMetadataStore, current_user

_NON_CONFIG_SEGMENTS = (
    "/evaluations/",
    "/evaluationRuns/",
    "/evaluationDatasets/",
    "/evaluationExpectations/",
    "/scheduledEvaluationRuns/",
    "/versions/",
    "/deployments/",
    "/conversations/",
    "/changelogs/",
)
_NON_CONFIG_WORDS = ("evaluation", "appversion", "deployment", "conversation")
_BOOKKEEPING_FIELDS = frozenset({"metadata", "locked"})


class DriftDetectedError(RuntimeError):
    """Raised when ``--on-drift=fail`` detects out-of-band CES edits."""

    def __init__(self, report: DriftReport, message: str | None = None):
        self.report = report
        super().__init__(message or report.format_summary())


@dataclasses.dataclass
class DriftEntry:
    """One out-of-band changelog entry detected since ``cxas.last_push``."""

    seq: int
    author: str
    action: str
    resource_type: str
    resource: str
    display_name: str
    description: str = ""
    time: str | None = None


@dataclasses.dataclass
class DriftReport:
    """Result of comparing CES changelogs against ``cxas.last_push``."""

    app_name: str
    has_baseline: bool
    baseline_seq: int | None = None
    baseline_git_sha: str | None = None
    baseline_by: str | None = None
    baseline_at: str | None = None
    latest_seq: int = 0
    entries: list[DriftEntry] = dataclasses.field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        """True if any config-changing entry occurred after baseline."""
        return self.has_baseline and bool(self.entries)

    def format_summary(self) -> str:
        """Render a concise human-readable drift report."""
        if not self.has_baseline:
            return f"No cxas.last_push baseline recorded on {self.app_name}."
        if not self.entries:
            return (
                f"No drift detected on {self.app_name} "
                f"(baseline seq={self.baseline_seq})."
            )
        lines = [
            f"Drift detected on {self.app_name}: {len(self.entries)} "
            f"out-of-band change(s) since last push "
            f"(seq {self.baseline_seq}, by {self.baseline_by or 'unknown'}, "
            f"at {self.baseline_at or 'unknown'}):"
        ]
        for entry in self.entries[:10]:
            label = entry.display_name or entry.resource or entry.resource_type
            desc = f" — {entry.description}" if entry.description else ""
            lines.append(
                f"  - [seq {entry.seq}] {entry.action} {label} "
                f"by {entry.author or 'unknown'}{desc}"
            )
        if len(self.entries) > 10:
            lines.append(f"  ... and {len(self.entries) - 10} more change(s).")
        lines.append(
            "Run `cxas pull --branch <name>` to capture remote tweaks or "
            "re-run with `--on-drift=overwrite` to overwrite them."
        )
        return "\n".join(lines)


def _enum_name(value: Any) -> str:
    name = getattr(value, "name", None)
    return str(name if name is not None else value or "")


def _format_time(ts: Any) -> str | None:
    if ts is None:
        return None
    if isinstance(ts, datetime.datetime):
        dt = ts
    elif hasattr(ts, "ToDatetime"):
        dt = ts.ToDatetime()
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    if dt.year <= 1970:
        return None
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_collab_only(entry: Any) -> bool:
    """True for app updates that only touched ``metadata`` / ``locked``."""
    description = str(getattr(entry, "description", "") or "").strip()
    match = re.match(r"(?i)^update app fields:\s*(.+)$", description)
    if match:
        fields = {
            f.strip().lower() for f in match.group(1).split(",") if f.strip()
        }
        if fields and fields <= _BOOKKEEPING_FIELDS:
            return True
    resource_type = _enum_name(getattr(entry, "resource_type", "")).lower()
    action = _enum_name(getattr(entry, "action", "")).lower()
    if (
        resource_type == "app" or "app" in action
    ) and "fields:" in description.lower():
        raw_fields = description.lower().split("fields:", 1)[1]
        fields = {f.strip() for f in raw_fields.split(",") if f.strip()}
        if fields and fields <= _BOOKKEEPING_FIELDS:
            return True
    return False


def is_drift_entry(entry: Any) -> bool:
    """True if a changelog entry represents a real out-of-band config change."""
    resource = str(getattr(entry, "resource", "") or "")
    if any(seg in resource for seg in _NON_CONFIG_SEGMENTS):
        return False
    kind = (
        (
            f"{_enum_name(getattr(entry, 'resource_type', ''))} "
            f"{_enum_name(getattr(entry, 'action', ''))}"
        )
        .lower()
        .replace(" ", "")
    )
    if any(word in kind for word in _NON_CONFIG_WORDS):
        return False
    return not _is_collab_only(entry)


def _short_git_sha(cwd: str | None = None) -> str | None:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res.returncode == 0:
            sha = res.stdout.strip()
            return sha or None
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _get_changelogs_client(app_name: str, changelogs: Any = None) -> Any:
    if changelogs is not None:
        return changelogs
    from cxas_scrapi.core.changelogs import Changelogs  # noqa: PLC0415

    return Changelogs(app_name=app_name)


def check_drift(
    app_name: str,
    *,
    store: AppMetadataStore | None = None,
    changelogs: Any = None,
) -> DriftReport:
    """Compare live CES changelogs against ``cxas.last_push``."""
    meta_store = store or AppMetadataStore(app_name)
    baseline = meta_store.get_last_push()
    if not baseline or "seq" not in baseline:
        return DriftReport(app_name=app_name, has_baseline=False)

    try:
        baseline_seq = int(baseline["seq"])
    except (TypeError, ValueError):
        return DriftReport(app_name=app_name, has_baseline=False)

    cl_client = _get_changelogs_client(app_name, changelogs)
    all_entries = list(cl_client.list_changelogs())
    latest_seq = max(
        (
            int(c.sequence_number)
            for c in all_entries
            if isinstance(getattr(c, "sequence_number", None), int)
        ),
        default=baseline_seq,
    )

    drift_entries: list[DriftEntry] = []
    for entry in all_entries:
        seq = getattr(entry, "sequence_number", None)
        if not isinstance(seq, int) or seq <= baseline_seq:
            continue
        if not is_drift_entry(entry):
            continue
        drift_entries.append(
            DriftEntry(
                seq=seq,
                author=str(getattr(entry, "author", "") or ""),
                action=_enum_name(getattr(entry, "action", "")),
                resource_type=_enum_name(getattr(entry, "resource_type", "")),
                resource=str(getattr(entry, "resource", "") or ""),
                display_name=str(getattr(entry, "display_name", "") or ""),
                description=str(getattr(entry, "description", "") or ""),
                time=_format_time(getattr(entry, "create_time", None)),
            )
        )

    drift_entries.sort(key=lambda e: e.seq)
    return DriftReport(
        app_name=app_name,
        has_baseline=True,
        baseline_seq=baseline_seq,
        baseline_git_sha=baseline.get("git_sha"),
        baseline_by=baseline.get("by"),
        baseline_at=baseline.get("at"),
        latest_seq=latest_seq,
        entries=drift_entries,
    )


def record_last_push(
    app_name: str,
    *,
    store: AppMetadataStore | None = None,
    changelogs: Any = None,
    git_sha: str | None = None,
    user: str | None = None,
    app_dir: str | None = None,
) -> dict[str, Any]:
    """Record the current CES changelog sequence as ``cxas.last_push``."""
    meta_store = store or AppMetadataStore(app_name)
    cl_client = _get_changelogs_client(app_name, changelogs)
    all_entries = list(cl_client.list_changelogs())
    latest_seq = max(
        (
            int(c.sequence_number)
            for c in all_entries
            if isinstance(getattr(c, "sequence_number", None), int)
        ),
        default=0,
    )
    resolved_sha = git_sha or _short_git_sha(cwd=app_dir)
    return meta_store.set_last_push(
        seq=latest_seq,
        git_sha=resolved_sha,
        by=user or current_user(),
    )
