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

import html
import json
import pathlib
from typing import TYPE_CHECKING

from cxas_scrapi.redaction.checker import FindingKind

if TYPE_CHECKING:
    from cxas_scrapi.redaction.checker import RedactionReport

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
    """Returns a self-contained HTML report."""
    e = html.escape
    rows = []
    for s in report.stats.values():
        rows.append(
            "<tr>"
            f"<td>{e(s.id)}</td><td>{e(s.action)}</td>"
            f"<td>{s.occurrences}</td><td>{s.redacted}</td>"
            f"<td>{s.preserved}</td>"
            f"<td class='{'bad' if s.leaked else ''}'>{s.leaked}</td>"
            f"<td class='{'warn' if s.partially_leaked else ''}'>"
            f"{s.partially_leaked}</td>"
            f"<td class='{'bad' if s.over_redacted else ''}'>"
            f"{s.over_redacted}</td>"
            f"<td>{_pct(s.redaction_recall)}</td>"
            f"<td>{_pct(s.preservation_rate)}</td>"
            "</tr>"
        )
    finding_rows = []
    for f in report.findings:
        where = e(f.source)
        if f.turn is not None:
            where += (
                f"<br><small>turn {f.turn + 1} {e(f.role or '')} "
                f"{e(f.surface or '')}</small>"
            )
        if f.path:
            where += f"<br><code>{e(f.path)}</code>"
        finding_rows.append(
            f"<tr class='k-{f.kind.value}'>"
            f"<td>{e(f.kind.value)}</td><td>{e(f.category or '-')}</td>"
            f"<td>{where}</td><td><code>{e(f.raw_excerpt or '')}</code></td>"
            f"<td><code>{e(f.stored_excerpt or f.message)}</code></td>"
            "</tr>"
        )
    counts = "".join(
        f"<div class='card'><b>{report.count(k)}</b><span>{e(label)}</span>"
        "</div>"
        for k, label in _KIND_LABELS.items()
    )
    markers = ", ".join(
        f"{e(k)}={v}" for k, v in report.info_type_counts.most_common()
    )
    status = "PASS" if report.passed else "FAIL"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Redaction report</title>
<style>
body{{font-family:system-ui,sans-serif;margin:24px;color:#202124}}
h1 span{{font-size:14px;padding:2px 8px;border-radius:4px;
background:{"#e6f4ea" if report.passed else "#fce8e6"}}}
table{{border-collapse:collapse;width:100%;margin:12px 0}}
td,th{{border:1px solid #dadce0;padding:6px 8px;text-align:left;
vertical-align:top;font-size:13px}}
th{{background:#f1f3f4}} code{{white-space:pre-wrap;word-break:break-word}}
.cards{{display:flex;gap:12px;flex-wrap:wrap}}
.card{{border:1px solid #dadce0;border-radius:8px;padding:8px 14px}}
.card b{{display:block;font-size:22px}} .bad{{background:#fce8e6}}
.warn{{background:#fef7e0}} .k-leak td:first-child,
.k-over_redaction td:first-child{{color:#c5221f;font-weight:600}}
</style></head><body>
<h1>Redaction report <span>{status}</span></h1>
<p>Mode: <b>{e(report.mode)}</b> &middot; sources: {len(report.sources)}
&middot; leaves checked: {report.leaves_checked}</p>
<div class="cards">{counts}</div>
<h2>Categories</h2>
<table><tr><th>category</th><th>action</th><th>seen</th><th>redacted</th>
<th>kept</th><th>leaked</th><th>partial</th><th>over-redacted</th>
<th>recall</th><th>preserved</th></tr>{"".join(rows)}</table>
<p>Redaction markers: {markers or "none"}</p>
<h2>Findings</h2>
<table><tr><th>kind</th><th>category</th><th>where</th><th>raw</th>
<th>persisted / replayed</th></tr>{"".join(finding_rows)}</table>
</body></html>"""


def write_html(report: RedactionReport, path: str | pathlib.Path) -> None:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_html(report), encoding="utf-8")
