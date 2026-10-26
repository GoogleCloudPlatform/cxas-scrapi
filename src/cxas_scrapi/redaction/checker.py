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

"""Evaluates redaction outcomes against a ``RedactionPolicy``.

Given pairs of (raw, persisted) leaves, every occurrence of a policy
category in the raw value is checked against the persisted value:

* ``must_redact`` values that survive are **leaks** (or **partial leaks**
  when only a fragment survives);
* ``must_not_redact`` values that disappear are **over-redactions**;
* redaction markers on values that match no category are reported as
  **unexpected redactions** so policy gaps become visible;
* numbers that turned into strings are reported as **type changes**.
"""

from __future__ import annotations

import collections
import dataclasses
import difflib
import enum
import re
from typing import TYPE_CHECKING, Any

from cxas_scrapi.redaction import matching
from cxas_scrapi.redaction.policy import Action, MatchMode

if TYPE_CHECKING:
    from collections.abc import Iterable

    from cxas_scrapi.redaction.extract import AlignedLeaf, Leaf
    from cxas_scrapi.redaction.policy import Category, RedactionPolicy

_EXCERPT_RADIUS = 40


class FindingKind(str, enum.Enum):
    LEAK = "leak"
    PARTIAL_LEAK = "partial_leak"
    OVER_REDACTION = "over_redaction"
    UNEXPECTED_REDACTION = "unexpected_redaction"
    TYPE_CHANGED = "type_changed"
    NOT_EXERCISED = "not_exercised"


# Kinds that count against thresholds.
FAILING_KINDS = {
    FindingKind.LEAK,
    FindingKind.PARTIAL_LEAK,
    FindingKind.OVER_REDACTION,
}


@dataclasses.dataclass
class Finding:
    kind: FindingKind
    source: str
    message: str
    category: str | None = None
    surface: str | None = None
    turn: int | None = None
    role: str | None = None
    path: str | None = None
    raw_excerpt: str | None = None
    stored_excerpt: str | None = None
    info_types: list[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["kind"] = self.kind.value
        if self.turn is not None:
            d["turn"] = self.turn + 1
        return d


@dataclasses.dataclass
class CategoryStats:
    id: str
    action: str
    occurrences: int = 0
    redacted: int = 0
    preserved: int = 0
    leaked: int = 0
    partially_leaked: int = 0
    over_redacted: int = 0

    @property
    def redaction_recall(self) -> float | None:
        """Share of must-redact occurrences fully redacted."""
        if self.action != Action.MUST_REDACT.value or not self.occurrences:
            return None
        return self.redacted / self.occurrences

    @property
    def preservation_rate(self) -> float | None:
        """Share of must-not-redact occurrences left intact."""
        if self.action != Action.MUST_NOT_REDACT.value or not self.occurrences:
            return None
        return self.preserved / self.occurrences

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["redaction_recall"] = self.redaction_recall
        d["preservation_rate"] = self.preservation_rate
        return d


@dataclasses.dataclass
class RedactionReport:
    """Aggregated outcome of one or more redaction checks."""

    mode: str
    policy: RedactionPolicy
    sources: list[str] = dataclasses.field(default_factory=list)
    findings: list[Finding] = dataclasses.field(default_factory=list)
    stats: dict[str, CategoryStats] = dataclasses.field(default_factory=dict)
    info_type_counts: collections.Counter = dataclasses.field(
        default_factory=collections.Counter
    )
    leaves_checked: int = 0
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        for c in self.policy.categories:
            self.stats.setdefault(c.id, CategoryStats(c.id, c.action.value))

    def count(self, kind: FindingKind) -> int:
        return sum(1 for f in self.findings if f.kind == kind)

    @property
    def passed(self) -> bool:
        t = self.policy.thresholds
        return (
            self.count(FindingKind.LEAK) <= t.max_leaks
            and self.count(FindingKind.PARTIAL_LEAK) <= t.max_partial_leaks
            and self.count(FindingKind.OVER_REDACTION) <= t.max_over_redactions
        )

    def merge(self, other: RedactionReport) -> RedactionReport:
        self.sources.extend(other.sources)
        self.findings.extend(other.findings)
        self.leaves_checked += other.leaves_checked
        self.info_type_counts.update(other.info_type_counts)
        for cid, s in other.stats.items():
            mine = self.stats.setdefault(cid, CategoryStats(cid, s.action))
            for f in (
                "occurrences",
                "redacted",
                "preserved",
                "leaked",
                "partially_leaked",
                "over_redacted",
            ):
                setattr(mine, f, getattr(mine, f) + getattr(s, f))
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "passed": self.passed,
            "summary": {
                "sources": len(self.sources),
                "leaves_checked": self.leaves_checked,
                **{k.value: self.count(k) for k in FindingKind},
            },
            "thresholds": self.policy.thresholds.model_dump(),
            "categories": {k: v.to_dict() for k, v in self.stats.items()},
            "info_types": dict(self.info_type_counts.most_common()),
            "findings": [f.to_dict() for f in self.findings],
            "sources": self.sources,
            "metadata": self.metadata,
        }


@dataclasses.dataclass(frozen=True)
class _Hit:
    category: Category
    needle: str
    mode: MatchMode
    start: int
    end: int


def _excerpt(text: str, start: int, end: int) -> str:
    lo = max(0, start - _EXCERPT_RADIUS)
    hi = min(len(text), end + _EXCERPT_RADIUS)
    prefix = "…" if lo > 0 else ""
    suffix = "…" if hi < len(text) else ""
    return f"{prefix}{text[lo:hi]}{suffix}"


def _short(text: str, limit: int = 2 * _EXCERPT_RADIUS) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _stored_excerpt(stored: str, hit: _Hit, raw_len: int) -> str:
    """Excerpt of the persisted text near where the raw value was."""
    found = matching.find_value(stored, hit.needle, hit.mode)
    if found:
        return _excerpt(stored, found[0].start, found[0].end)
    if not stored:
        return ""
    # Redaction changes lengths; map the raw position proportionally.
    ratio = len(stored) / raw_len if raw_len else 1.0
    pos = min(len(stored) - 1, int(hit.start * ratio))
    return _excerpt(stored, pos, min(len(stored), pos + 1))


def _key_matches(leaf: Leaf, category: Category) -> bool:
    if not leaf.key or not category.keys:
        return False
    low = leaf.key.lower()
    return any(low == k.lower() for k in category.keys)


def _hits(leaf: Leaf, policy: RedactionPolicy) -> list[_Hit]:
    text = leaf.text
    hits: list[_Hit] = []
    seen: set[tuple[str, int, int]] = set()

    def add(c: Category, needle: str, mode: MatchMode, s: int, e: int) -> None:
        key = (c.id, s, e)
        if key not in seen:
            seen.add(key)
            hits.append(_Hit(c, needle, mode, s, e))

    for c in policy.categories:
        for value in c.values:
            for m in matching.find_value(text, value, c.match):
                add(c, value, c.match, m.start, m.end)
        for pattern in c.patterns:
            for m in re.finditer(pattern, text):
                add(c, m.group(0), MatchMode.EXACT, m.start(), m.end())
        if _key_matches(leaf, c) and not any(h.category is c for h in hits):
            add(c, text, c.match, 0, len(text))
    return hits


def _info_types(markers: list[str]) -> list[str]:
    out = []
    for m in markers:
        inner = m.strip("[]")
        out.append(inner if inner and inner != m else m)
    return out


def _present(stored_text: str, hit: _Hit) -> bool:
    return bool(matching.find_value(stored_text, hit.needle, hit.mode))


_TOKEN_RE = re.compile(r"\S+")


def _unexplained_redactions(
    raw: str,
    stored: str,
    hits: list[_Hit],
    markers_re: re.Pattern[str],
) -> list[tuple[int, int]]:
    """Raw spans that were redacted but overlap no policy category hit.

    Raw and stored texts are diffed token by token; every replaced or
    deleted run whose stored side carries a redaction marker is a redacted
    span, and it is unexplained when it does not overlap any hit.
    """
    raw_toks = [(m.start(), m.end()) for m in _TOKEN_RE.finditer(raw)]
    stored_toks = [m.group(0) for m in _TOKEN_RE.finditer(stored)]
    matcher = difflib.SequenceMatcher(
        a=[raw[s:e] for s, e in raw_toks], b=stored_toks, autojunk=False
    )
    spans: list[tuple[int, int]] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op not in ("replace", "delete") or i1 == i2:
            continue
        if not markers_re.search(" ".join(stored_toks[j1:j2])):
            continue
        start, end = raw_toks[i1][0], raw_toks[i2 - 1][1]
        if not any(h.start < end and start < h.end for h in hits):
            spans.append((start, end))
    return spans


def check_pairs(
    pairs: Iterable[AlignedLeaf],
    policy: RedactionPolicy,
    source: str,
    mode: str,
    require: Iterable[str] = (),
) -> RedactionReport:
    """Checks raw/persisted leaf pairs against ``policy``."""
    report = RedactionReport(mode=mode, policy=policy, sources=[source])
    surfaces = set(policy.surfaces)
    markers_re = policy.marker_regex
    exercised: set[str] = set()

    for pair in pairs:
        raw, stored = pair.raw, pair.stored
        if raw.surface not in surfaces or stored is None:
            continue
        report.leaves_checked += 1
        raw_text, stored_text = raw.text, stored.text
        markers = markers_re.findall(stored_text)
        info_types = _info_types(markers)
        report.info_type_counts.update(info_types)

        common = {
            "source": source,
            "surface": raw.surface.value,
            "turn": raw.turn,
            "role": raw.role,
            "path": raw.path or None,
        }

        if raw.is_number and isinstance(stored.value, str) and markers:
            report.findings.append(
                Finding(
                    FindingKind.TYPE_CHANGED,
                    message=(
                        f"numeric value became the string "
                        f"'{_short(stored_text)}'"
                    ),
                    raw_excerpt=raw_text,
                    stored_excerpt=_short(stored_text),
                    info_types=info_types,
                    **common,
                )
            )

        hits = _hits(raw, policy)
        for hit in hits:
            c = hit.category
            exercised.add(c.id)
            stats = report.stats[c.id]
            stats.occurrences += 1
            raw_ex = _excerpt(raw_text, hit.start, hit.end)
            present = _present(stored_text, hit)

            if c.action == Action.MUST_REDACT:
                if present:
                    stats.leaked += 1
                    report.findings.append(
                        Finding(
                            FindingKind.LEAK,
                            category=c.id,
                            message=f"'{c.id}' value persisted unredacted",
                            raw_excerpt=raw_ex,
                            stored_excerpt=_stored_excerpt(
                                stored_text, hit, len(raw_text)
                            ),
                            info_types=info_types,
                            **common,
                        )
                    )
                    continue
                partial = (
                    matching.find_partial(
                        stored_text, hit.needle, allow_last=c.allow_last
                    )
                    if hit.mode == MatchMode.DIGITS
                    else []
                )
                if partial:
                    stats.partially_leaked += 1
                    frag = ", ".join(sorted({p.text for p in partial}))
                    report.findings.append(
                        Finding(
                            FindingKind.PARTIAL_LEAK,
                            category=c.id,
                            message=(
                                f"'{c.id}' value partially persisted: {frag}"
                            ),
                            raw_excerpt=raw_ex,
                            stored_excerpt=_stored_excerpt(
                                stored_text, hit, len(raw_text)
                            ),
                            info_types=info_types,
                            **common,
                        )
                    )
                else:
                    stats.redacted += 1
            elif c.action == Action.MUST_NOT_REDACT:
                if present:
                    stats.preserved += 1
                else:
                    stats.over_redacted += 1
                    report.findings.append(
                        Finding(
                            FindingKind.OVER_REDACTION,
                            category=c.id,
                            message=f"'{c.id}' value was redacted",
                            raw_excerpt=raw_ex,
                            stored_excerpt=_stored_excerpt(
                                stored_text, hit, len(raw_text)
                            ),
                            info_types=info_types,
                            **common,
                        )
                    )
            elif present:
                stats.preserved += 1
            else:
                stats.redacted += 1

        if markers:
            unexplained = _unexplained_redactions(
                raw_text, stored_text, hits, markers_re
            )
            if unexplained:
                frags = ", ".join(
                    f"'{_short(raw_text[s:e], 40)}'" for s, e in unexplained
                )
                s0, e0 = unexplained[0]
                report.findings.append(
                    Finding(
                        FindingKind.UNEXPECTED_REDACTION,
                        message=(
                            f"redacted {frags}, which matches no policy "
                            "category"
                        ),
                        raw_excerpt=_excerpt(raw_text, s0, e0),
                        stored_excerpt=_short(stored_text),
                        info_types=info_types,
                        **common,
                    )
                )

    for cid in require:
        if cid not in exercised:
            report.findings.append(
                Finding(
                    FindingKind.NOT_EXERCISED,
                    source=source,
                    category=cid,
                    message=f"required category '{cid}' never appeared",
                )
            )
    return report
