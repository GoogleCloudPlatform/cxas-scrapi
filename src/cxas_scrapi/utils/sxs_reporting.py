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

"""HTML report generation for SxS evaluations."""

import os
from datetime import datetime

from cxas_scrapi.utils.report_components import load_component


def _e(text: str) -> str:
    """HTML-escape a string."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _ces_base(app_name: str) -> str:
    parts = app_name.split("/") if app_name else []
    project_id = parts[1] if len(parts) > 1 else ""
    location = parts[3] if len(parts) > 3 else ""
    app_id = parts[5] if len(parts) > 5 else ""
    if not app_id:
        return ""
    return (
        f"https://ces.cloud.google.com/projects/{project_id}"
        f"/locations/{location}/apps/{app_id}"
    )


def _session_link(session_id: str, ces_base: str, label: str) -> str:
    if not session_id:
        return ""
    if ces_base:
        url = f"{ces_base}?panel=conversation_list&id={session_id}&source=EVAL"
        return (
            f'<a class="session-link-a" href="{url}" target="_blank"'
            f' title="Open {_e(label)} session in CES">{_e(session_id)}</a>'
        )
    return f'<code>{_e(session_id)}</code>'


def _render_checks(checks: list) -> str:
    if not checks:
        return ""
    html = ""
    for c in checks:
        cls = "pass-bg" if c["status"] == "SUCCESS" else "fail-bg"
        html += f'<div class="check-item {cls}">'
        if c["expected"]:
            html += (
                f'<div class="check-label">expected</div>'
                f'<div class="comp-text">{_e(c["expected"])}</div>'
            )
        if c["actual"]:
            html += (
                f'<div class="check-label">actual</div>'
                f'<div class="comp-text">{_e(c["actual"])}</div>'
            )
        if c["errors"]:
            html += (
                f'<div class="check-label" style="color:#c0392b">error</div>'
                f'<div class="comp-text" style="color:#c0392b">'
                f'{_e(c["errors"])}</div>'
            )
        if c["llm_results"] and not c["expected"] and not c["actual"]:
            html += (
                f'<div class="comp-text meta">{_e(c["llm_results"])}</div>'
            )
        html += "</div>"
    return html


def _render_turn_rows(turns: list, label_a: str, label_b: str) -> str:
    html = ""
    for t in turns:
        sa = t["status_a"]
        sb = t["status_b"]
        a_cls = "pass" if sa == "SUCCESS" else "fail"
        b_cls = "pass" if sb == "SUCCESS" else "fail"

        # Highlight row if the two apps diverge
        if sa != sb:
            row_style = ' style="background:#fffde7"'
        else:
            row_style = ""

        user_html = (
            f'<span class="user-bubble">{_e(t["user"])}</span>'
            if t["user"]
            else '<span class="meta">—</span>'
        )

        checks_a = _render_checks(t["checks_a"])
        checks_b = _render_checks(t["checks_b"])

        html += (
            f"<tr{row_style}>"
            f"<td><code>{_e(t['turn'])}</code></td>"
            f"<td>{user_html}</td>"
            f'<td><span class="badge {a_cls}">{sa}</span>{checks_a}</td>'
            f'<td><span class="badge {b_cls}">{sb}</span>{checks_b}</td>'
            f"</tr>\n"
        )
    return html


def _card_header(
    test: dict,
    label_a: str,
    label_b: str,
    card_id: str,
) -> tuple:
    """Return (outcome, header_cls, delta, delta_cls, a_badge, b_badge, body_display)."""
    pa, pb = test["pass_a"], test["pass_b"]
    if pa and pb:
        outcome, header_cls = "both-pass", "pass-bg"
        delta, delta_cls = "&#10003; both pass", "pass"
    elif pa and not pb:
        outcome, header_cls = "regression", "regression-bg"
        delta, delta_cls = "&#8595; regression", "regression"
    elif not pa and pb:
        outcome, header_cls = "improvement", "improvement-bg"
        delta, delta_cls = "&#8593; improvement", "improvement"
    else:
        outcome, header_cls = "both-fail", "fail-bg"
        delta, delta_cls = "&#10007; both fail", "fail"
    a_badge = "pass" if pa else "fail"
    b_badge = "pass" if pb else "fail"
    body_display = (
        "block" if outcome in ("regression", "improvement") else "none"
    )
    return outcome, header_cls, delta, delta_cls, a_badge, b_badge, body_display


def _session_row(test, ces_base_a, ces_base_b, label_a, label_b):
    link_a = _session_link(test["session_a"], ces_base_a, label_a)
    link_b = _session_link(test["session_b"], ces_base_b, label_b)
    if not link_a and not link_b:
        return ""
    return (
        '<div class="sxs-sessions meta">'
        f"<span>Session {_e(label_a)}: {link_a or '—'}</span>"
        f"<span>Session {_e(label_b)}: {link_b or '—'}</span>"
        "</div>"
    )


def _render_sim_test_card(
    test: dict,
    label_a: str,
    label_b: str,
    ces_base_a: str,
    ces_base_b: str,
    idx: int,
) -> str:
    name = test["name"]
    outcome, header_cls, delta, delta_cls, a_badge, b_badge, body_display = (
        _card_header(test, label_a, label_b, f"sxs-{idx}")
    )
    a_status = "PASS" if test["pass_a"] else "FAIL"
    b_status = "PASS" if test["pass_b"] else "FAIL"
    card_id = f"sxs-{idx}"

    # Stats bar
    stats_html = (
        '<div class="sim-stats meta">'
        f"<span><b>{_e(label_a)}:</b> goals {_e(test['goals_a'])} | "
        f"expectations {_e(test['expectations_a'])} | {test['turns_a']} turns</span>"
        f"<span><b>{_e(label_b)}:</b> goals {_e(test['goals_b'])} | "
        f"expectations {_e(test['expectations_b'])} | {test['turns_b']} turns</span>"
        "</div>"
    )

    session_html = _session_row(test, ces_base_a, ces_base_b, label_a, label_b)

    # Steps table
    step_rows = ""
    for i, step in enumerate(test.get("steps", []), 1):
        sa, sb = step["status_a"], step["status_b"]
        a_cls = "pass" if sa == "Completed" else "fail"
        b_cls = "pass" if sb == "Completed" else "fail"
        row_style = ' style="background:#fffde7"' if sa != sb else ""
        just_a = (
            f'<div class="check-label">justification</div>'
            f'<div class="comp-text meta">{_e(step["justification_a"])}</div>'
            if step["justification_a"]
            else ""
        )
        just_b = (
            f'<div class="check-label">justification</div>'
            f'<div class="comp-text meta">{_e(step["justification_b"])}</div>'
            if step["justification_b"]
            else ""
        )
        step_rows += (
            f"<tr{row_style}>"
            f"<td style='width:5%;text-align:center'><b>{i}</b></td>"
            f"<td style='width:35%'>{_e(step['goal'])}</td>"
            f"<td><span class='badge {a_cls}'>{_e(sa)}</span>{just_a}</td>"
            f"<td><span class='badge {b_cls}'>{_e(sb)}</span>{just_b}</td>"
            f"</tr>\n"
        )

    steps_table = (
        "<h4 style='margin:12px 0 4px'>Steps</h4>"
        '<table class="sxs-table">'
        f"<tr><th>#</th><th>Goal</th>"
        f"<th>{_e(label_a)}</th><th>{_e(label_b)}</th></tr>"
        f"{step_rows}</table>"
        if step_rows
        else ""
    )

    # Expectations table
    exp_rows = ""
    for i, exp in enumerate(test.get("expectations", []), 1):
        sa, sb = exp["status_a"], exp["status_b"]
        a_cls = "pass" if sa == "Met" else "fail"
        b_cls = "pass" if sb == "Met" else "fail"
        row_style = ' style="background:#fffde7"' if sa != sb else ""
        just_a = (
            f'<div class="comp-text meta">{_e(exp["justification_a"])}</div>'
            if exp["justification_a"]
            else ""
        )
        just_b = (
            f'<div class="comp-text meta">{_e(exp["justification_b"])}</div>'
            if exp["justification_b"]
            else ""
        )
        exp_rows += (
            f"<tr{row_style}>"
            f"<td style='width:5%;text-align:center'>{i}</td>"
            f"<td style='width:35%'>{_e(exp['expectation'])}</td>"
            f"<td><span class='badge {a_cls}'>{_e(sa)}</span>{just_a}</td>"
            f"<td><span class='badge {b_cls}'>{_e(sb)}</span>{just_b}</td>"
            f"</tr>\n"
        )

    exps_table = (
        "<h4 style='margin:12px 0 4px'>Expectations</h4>"
        '<table class="sxs-table">'
        f"<tr><th>#</th><th>Expectation</th>"
        f"<th>{_e(label_a)}</th><th>{_e(label_b)}</th></tr>"
        f"{exp_rows}</table>"
        if exp_rows
        else ""
    )

    # Traces (collapsible) — use the same pipeline as the single-sided report
    from cxas_scrapi.utils.reporting import (  # noqa: PLC0415
        _parse_trace,
        _merge_trace_lines,
        _render_merged_items,
    )

    def _render_sxs_trace(trace, turns, label):
        if not trace:
            return ""
        parsed = _parse_trace(trace, {})
        merged = _merge_trace_lines(parsed)
        body = _render_merged_items(merged)
        return (
            f'<details><summary>&#128172; Conversation — {_e(label)}'
            f" ({turns} turns)</summary>"
            f'<div class="transcript">{body}</div></details>'
        )

    transcripts_html = ""
    trace_a_html = _render_sxs_trace(
        test.get("trace_a", []), test.get("turns_a", "?"), label_a
    )
    trace_b_html = _render_sxs_trace(
        test.get("trace_b", []), test.get("turns_b", "?"), label_b
    )
    if trace_a_html or trace_b_html:
        transcripts_html = (
            f'<div class="sxs-transcripts">{trace_a_html}{trace_b_html}</div>'
        )

    return (
        f'<div class="eval-card" id="{card_id}" data-outcome="{outcome}">\n'
        f'  <div class="eval-header {header_cls} sxs-header"'
        f'    onclick="toggleCard(\'{card_id}\')" style="cursor:pointer">\n'
        f"    <span>{_e(name)} "
        f'<span class="badge sim" style="font-weight:normal">sim</span></span>\n'
        f"    <span>\n"
        f'      <span class="badge {a_badge}">{_e(label_a)}: {a_status}</span>\n'
        f'      <span class="badge {b_badge}">{_e(label_b)}: {b_status}</span>\n'
        f'      <span class="badge {delta_cls}">{delta}</span>\n'
        f"    </span>\n"
        f"  </div>\n"
        f'  <div class="eval-body" id="body-{card_id}" style="display:{body_display}">\n'
        f"    {stats_html}\n"
        f"    {session_html}\n"
        f"    {steps_table}\n"
        f"    {exps_table}\n"
        f"    {transcripts_html}\n"
        f"  </div>\n"
        f"</div>\n"
    )


def _render_test_card(
    test: dict,
    label_a: str,
    label_b: str,
    ces_base_a: str,
    ces_base_b: str,
    idx: int,
) -> str:
    if test.get("type") == "sim":
        return _render_sim_test_card(
            test, label_a, label_b, ces_base_a, ces_base_b, idx
        )

    # Turn-eval card (original logic)
    name = test["name"]
    outcome, header_cls, delta, delta_cls, a_badge, b_badge, body_display = (
        _card_header(test, label_a, label_b, f"sxs-{idx}")
    )
    a_status = "PASS" if test["pass_a"] else "FAIL"
    b_status = "PASS" if test["pass_b"] else "FAIL"
    card_id = f"sxs-{idx}"

    session_html = _session_row(test, ces_base_a, ces_base_b, label_a, label_b)

    turn_rows = _render_turn_rows(test["turns"], label_a, label_b)
    turn_table = (
        f'<table class="sxs-table">'
        f"<tr>"
        f"<th class='col-turn'>Turn</th>"
        f"<th class='col-user'>User Input</th>"
        f"<th class='col-app'>{_e(label_a)}</th>"
        f"<th class='col-app'>{_e(label_b)}</th>"
        f"</tr>"
        f"{turn_rows}"
        f"</table>"
    )

    return (
        f'<div class="eval-card" id="{card_id}" data-outcome="{outcome}">\n'
        f'  <div class="eval-header {header_cls} sxs-header"'
        f'    onclick="toggleCard(\'{card_id}\')" style="cursor:pointer">\n'
        f"    <span>{_e(name)}</span>\n"
        f"    <span>\n"
        f'      <span class="badge {a_badge}">{_e(label_a)}: {a_status}</span>\n'
        f'      <span class="badge {b_badge}">{_e(label_b)}: {b_status}</span>\n'
        f'      <span class="badge {delta_cls}">{delta}</span>\n'
        f"    </span>\n"
        f"  </div>\n"
        f'  <div class="eval-body" id="body-{card_id}"'
        f'    style="display:{body_display}">\n'
        f"    {session_html}\n"
        f"    {turn_table}\n"
        f"  </div>\n"
        f"</div>\n"
    )


_SXS_CSS = """
/* SxS-specific overrides */
body { max-width: 1400px; }

.eval-header.regression-bg {
  background: #fff3e0;
  border-left: 4px solid #f57c00;
}
.eval-header.improvement-bg {
  background: #e0f7f4;
  border-left: 4px solid #00897b;
}

.badge.regression { background: #fff3e0; color: #e65100; }
.badge.improvement { background: #e0f7f4; color: #00695c; }

.sxs-header { cursor: pointer; }
.sxs-sessions {
  display: flex;
  gap: 24px;
  margin: 8px 0 12px;
  flex-wrap: wrap;
}
.session-link-a {
  color: #3498db;
  text-decoration: none;
}
.session-link-a:hover { text-decoration: underline; }

.sxs-table {
  table-layout: fixed;
  font-size: 0.88em;
}
.sxs-table .col-turn { width: 12%; }
.sxs-table .col-user { width: 20%; }
.sxs-table .col-app  { width: 34%; vertical-align: top; }

.check-item {
  margin: 4px 0;
  padding: 5px 8px;
  border-radius: 4px;
  font-size: 0.85em;
}
.check-label {
  font-size: 0.7em;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #888;
  margin-top: 4px;
}
.comp-text { margin-top: 2px; word-break: break-word; }

.sxs-score { font-size: 1.5em; font-weight: bold; }

.sim-stats {
  display: flex;
  gap: 24px;
  margin: 8px 0 12px;
  flex-wrap: wrap;
}
.sxs-transcripts { margin-top: 12px; }
.sxs-transcripts details { margin: 6px 0; }
.sxs-transcripts summary {
  cursor: pointer;
  font-size: 0.85em;
  color: #3498db;
  padding: 4px 0;
}
.sxs-transcripts .transcript {
  max-height: 400px;
  overflow-y: auto;
}

/* Dark mode additions */
@media (prefers-color-scheme: dark) {
  .eval-header.regression-bg {
    background: #2e1f00;
    border-left-color: #f57c00;
    color: #e0e0e0;
  }
  .eval-header.improvement-bg {
    background: #00221d;
    border-left-color: #00897b;
    color: #e0e0e0;
  }
  .badge.regression { background: #2e1f00; color: #ffb74d; }
  .badge.improvement { background: #00221d; color: #80cbc4; }
  .check-label { color: #aaa; }
  .sxs-table { color: #e0e0e0; }
}
"""

_SXS_JS = """
function toggleCard(id) {
  var body = document.getElementById('body-' + id);
  if (body) body.style.display = body.style.display === 'none' ? 'block' : 'none';
}

function filterTests(type) {
  document.querySelectorAll('.eval-card').forEach(function(card) {
    var ok = type === 'all' || card.dataset.outcome === type;
    card.classList.toggle('hidden-card', !ok);
  });
  document.querySelectorAll('.controls button').forEach(function(btn) {
    btn.classList.remove('active');
  });
  var btn = document.getElementById('btn-' + type);
  if (btn) btn.classList.add('active');
}
"""


def generate_sxs_html_report(
    sxs_results: dict,
    output_path: str,
    app_name_a: str = "",
    app_name_b: str = "",
) -> str:
    """Generate a side-by-side HTML comparison report.

    Args:
        sxs_results: The dict returned by :meth:`SxSEvals.run_sxs`.
        output_path: Where to write the HTML file.
        app_name_a: Full resource name of App A (used to build CES session
            links). Optional — links are omitted when blank.
        app_name_b: Full resource name of App B (same as above for App B).

    Returns:
        The path to the written report.
    """
    label_a = sxs_results["label_a"]
    label_b = sxs_results["label_b"]
    s = sxs_results["summary"]
    tests = sxs_results["tests"]
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    total = s["total"]
    pass_a = s["pass_a"]
    pass_b = s["pass_b"]
    pct_a = 100 * pass_a / total if total else 0
    pct_b = 100 * pass_b / total if total else 0
    both_pass = s["both_pass"]
    both_fail = s["both_fail"]
    regressions = s["regressions"]
    improvements = s["improvements"]

    ces_a = _ces_base(app_name_a)
    ces_b = _ces_base(app_name_b)

    base_css = load_component("base/base.css")
    base_js = load_component("base/interaction.js")

    # Summary section
    a_cls = "pass" if pct_a >= 90 else ("mixed" if pct_a >= 60 else "fail")
    b_cls = "pass" if pct_b >= 90 else ("mixed" if pct_b >= 60 else "fail")

    summary_html = f"""
<div class="summary">
  <div class="summary-top">
    <div>
      <div class="sxs-score {a_cls}">{_e(label_a)}: {pass_a}/{total}
        <span style="font-size:0.6em;font-weight:normal">({pct_a:.0f}%)</span>
      </div>
      <div class="sxs-score {b_cls}">{_e(label_b)}: {pass_b}/{total}
        <span style="font-size:0.6em;font-weight:normal">({pct_b:.0f}%)</span>
      </div>
    </div>
  </div>
  <div class="summary-grid">
    <div class="summary-card" onclick="filterTests('both-pass')">
      <div class="label">Both Pass</div>
      <div class="value pass">{both_pass}</div>
    </div>
    <div class="summary-card" onclick="filterTests('improvement')">
      <div class="label">Improvements &#8593;</div>
      <div class="value pass">{improvements}</div>
    </div>
    <div class="summary-card" onclick="filterTests('regression')">
      <div class="label">Regressions &#8595;</div>
      <div class="value {'mixed' if regressions else 'pass'}">{regressions}</div>
    </div>
    <div class="summary-card" onclick="filterTests('both-fail')">
      <div class="label">Both Fail</div>
      <div class="value fail">{both_fail}</div>
    </div>
    <div class="summary-card" onclick="filterTests('all')">
      <div class="label">Total</div>
      <div class="value">{total}</div>
    </div>
  </div>
  <div class="meta">Generated {ts}
    | {_e(label_a)} runtime: {sxs_results['duration_a']:.1f}s
    | {_e(label_b)} runtime: {sxs_results['duration_b']:.1f}s
  </div>
</div>
"""

    controls_html = f"""
<div class="controls">
  <button id="btn-all" class="active" onclick="filterTests('all')">
    All ({total})
  </button>
  <button id="btn-regression" onclick="filterTests('regression')">
    &#8595; Regressions ({regressions})
  </button>
  <button id="btn-improvement" onclick="filterTests('improvement')">
    &#8593; Improvements ({improvements})
  </button>
  <button id="btn-both-fail" onclick="filterTests('both-fail')">
    Both Fail ({both_fail})
  </button>
  <button id="btn-both-pass" onclick="filterTests('both-pass')">
    Both Pass ({both_pass})
  </button>
</div>
"""

    cards_html = ""
    for idx, test in enumerate(tests):
        cards_html += _render_test_card(
            test, label_a, label_b, ces_a, ces_b, idx
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SxS Report — {_e(label_a)} vs {_e(label_b)}</title>
<style>
{base_css}
{_SXS_CSS}
</style>
<script>
{base_js}
{_SXS_JS}
</script>
</head>
<body>
<h1>SxS Evaluation Report</h1>
<p class="meta">{_e(label_a)} vs {_e(label_b)}</p>
{summary_html}
{controls_html}
<h2>Test Results</h2>
{cards_html}
</body>
</html>"""

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"SxS report written to {output_path}")
    return output_path
