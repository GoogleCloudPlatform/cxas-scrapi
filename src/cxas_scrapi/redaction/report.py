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

"""Renders ``RedactionReport`` objects for terminals, JSON and HTML."""

from __future__ import annotations

import json
import pathlib
from typing import TYPE_CHECKING

import jinja2

from cxas_scrapi.redaction.checker import FindingKind

if TYPE_CHECKING:
    from cxas_scrapi.redaction.checker import RedactionReport

_TEMPLATE_DIR = pathlib.Path(__file__).parent
_TEMPLATE_NAME = "report_template.html"

_KIND_LABELS = {
    FindingKind.LEAK: "Leaks",
    FindingKind.PARTIAL_LEAK: "Partial leaks",
    FindingKind.OVER_REDACTION: "Over-redactions",
    FindingKind.UNEXPECTED_REDACTION: "Unexpected redactions",
    FindingKind.TYPE_CHANGED: "Type changes",
    FindingKind.NOT_EXERCISED: "Not exercised",
}


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{v:.0%}"


def print_report(report: RedactionReport, max_findings: int = 50) -> None:
    """Prints a summary table and the most important findings."""
    from rich.console import Console  # noqa: PLC0415
    from rich.table import Table  # noqa: PLC0415

    console = Console()
    status = "[green]PASS[/green]" if report.passed else "[red]FAIL[/red]"
    console.print(
        f"\n[bold]Redaction check ({report.mode})[/bold]  {status}  "
        f"sources={len(report.sources)}  leaves={report.leaves_checked}"
    )

    cats = Table(title="Categories", show_lines=False)
    for col in (
        "category",
        "action",
        "seen",
        "redacted",
        "kept",
        "leaked",
        "partial",
        "over-redacted",
        "recall",
        "preserved",
    ):
        cats.add_column(col)
    for s in report.stats.values():
        cats.add_row(
            s.id,
            s.action,
            str(s.occurrences),
            str(s.redacted),
            str(s.preserved),
            f"[red]{s.leaked}[/red]" if s.leaked else "0",
            f"[yellow]{s.partially_leaked}[/yellow]"
            if s.partially_leaked
            else "0",
            f"[red]{s.over_redacted}[/red]" if s.over_redacted else "0",
            _pct(s.redaction_recall),
            _pct(s.preservation_rate),
        )
    console.print(cats)

    summary = ", ".join(
        f"{label}: {report.count(kind)}" for kind, label in _KIND_LABELS.items()
    )
    console.print(summary)
    if report.info_type_counts:
        console.print(
            "Redaction markers: "
            + ", ".join(
                f"{k}={v}" for k, v in report.info_type_counts.most_common()
            )
        )

    order = list(_KIND_LABELS)
    findings = sorted(report.findings, key=lambda f: order.index(f.kind))
    if not findings:
        return
    table = Table(title="Findings", show_lines=True)
    for col in ("kind", "category", "where", "raw", "persisted / replayed"):
        table.add_column(col, overflow="fold")
    for f in findings[:max_findings]:
        where = f.source
        if f.turn is not None:
            where += f"\nturn {f.turn + 1} {f.role or ''} {f.surface or ''}"
        if f.path:
            where += f"\n{f.path}"
        table.add_row(
            f.kind.value,
            f.category or "-",
            where,
            f.raw_excerpt or "",
            f.stored_excerpt or f.message,
        )
    console.print(table)
    if len(findings) > max_findings:
        console.print(
            f"... {len(findings) - max_findings} more findings in the JSON "
            "report"
        )


def write_json(report: RedactionReport, path: str | pathlib.Path) -> None:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")


def render_html(report: RedactionReport) -> str:
    """Returns a self-contained HTML report (rendered from a template)."""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=jinja2.select_autoescape(["html"]),
    )
    template = env.get_template(_TEMPLATE_NAME)
    categories = [
        {
            **s.to_dict(),
            "recall": _pct(s.redaction_recall),
            "preservation": _pct(s.preservation_rate),
        }
        for s in report.stats.values()
    ]
    return template.render(
        passed=report.passed,
        mode=report.mode,
        source_count=len(report.sources),
        leaves_checked=report.leaves_checked,
        cards=[
            {"count": report.count(k), "label": label}
            for k, label in _KIND_LABELS.items()
        ],
        categories=categories,
        markers=report.info_type_counts.most_common(),
        findings=[f.to_dict() for f in report.findings],
    )


def write_html(report: RedactionReport, path: str | pathlib.Path) -> None:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_html(report), encoding="utf-8")
