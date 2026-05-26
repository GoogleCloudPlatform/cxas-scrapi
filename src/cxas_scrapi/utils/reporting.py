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

"""Utility functions for generating reports."""

import glob
import json
import os
from datetime import datetime
from typing import Any, Dict, List

import pandas as pd
import yaml
from jinja2 import Template

from cxas_scrapi.core.tools import Tools
from cxas_scrapi.evals import runner as evals_runner
from cxas_scrapi.utils.eval_utils import EvalUtils
from cxas_scrapi.utils.gcs_utils import GCSUtils
from cxas_scrapi.utils.report_components import load_component


def _escape(text):
    """HTML-escape a string."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _fmt_duration(seconds):
    """Format duration: seconds if < 60, minutes otherwise."""
    seconds_per_minute = 60
    if seconds is None:
        return ""
    if seconds >= seconds_per_minute:
        return f"{seconds / seconds_per_minute:.1f}m"
    return f"{seconds:.1f}s"


def _resolve_tool_name(raw_name, tools_map):
    """Resolve a full resource path to a display name."""
    if not raw_name:
        return raw_name
    # Check reverse map (resource path → display name)
    if raw_name in tools_map:
        return tools_map[raw_name]
    # Try matching by tool ID (last segment)
    tool_id = raw_name.split("/")[-1] if "/" in raw_name else raw_name
    for path, display in tools_map.items():
        if path.endswith(f"/{tool_id}"):
            return display
    # Fallback: just use last segment
    return tool_id if "/" in raw_name else raw_name


def _format_trace_line(line, tools_map):
    """Format a trace line, resolving tool IDs to display names."""
    if "Tool Call:" in line or "Tool Response:" in line:
        # Replace resource paths with display names
        for path, display in tools_map.items():
            line = line.replace(path, display)
    return line


def _get_html_head(ts):
    """Return the HTML head with CSS and JS."""
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Simulation Report - {ts}</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    max-width: 1100px;
    margin: 0 auto;
    padding: 20px;
    background: #f8f9fa;
  }}
  h1 {{
    color: #1a1a2e;
    border-bottom: 3px solid #e94560;
    padding-bottom: 10px;
  }}
  h2 {{ color: #1a1a2e; margin-top: 30px; }}
  .summary {{
    background: white;
    padding: 20px;
    border-radius: 8px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    margin-bottom: 20px;
  }}
  .summary .big {{ font-size: 2em; font-weight: bold; }}
  .pass {{ color: #27ae60; }}
  .fail {{ color: #e74c3c; }}
  .error {{ color: #e67e22; }}
  table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
  th,
  td {{
    text-align: left;
    padding: 8px 12px;
    border-bottom: 1px solid #ddd;
  }}
  th {{ background: #2c3e50; color: white; }}
  tr:hover {{ background: #f5f5f5; }}
  .eval-card {{
    background: white;
    border-radius: 8px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    margin: 15px 0;
    overflow: hidden;
  }}
  .eval-header {{
    padding: 12px 16px;
    font-weight: bold;
    cursor: pointer;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}
  .eval-header.pass-bg {{
    background: #d4edda;
    border-left: 4px solid #27ae60;
  }}
  .eval-header.fail-bg {{
    background: #f8d7da;
    border-left: 4px solid #e74c3c;
  }}
  .eval-body {{ padding: 0 16px 16px; }}
  .transcript {{
    background: #f8f9fa;
    border-radius: 6px;
    padding: 12px;
    margin: 8px 0;
    font-size: 0.9em;
  }}
  .transcript .user {{ color: #2980b9; margin: 6px 0; }}
  .transcript .agent {{ color: #27ae60; margin: 6px 0; }}
  .transcript .system {{ color: #e67e22; margin: 4px 0; font-size: 0.85em; }}
  .badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 0.8em;
    font-weight: bold;
  }}
  .badge.pass {{ background: #d4edda; color: #155724; }}
  .badge.fail {{ background: #f8d7da; color: #721c24; }}
  .badge.met {{ background: #d4edda; color: #155724; }}
  .badge.not-met {{ background: #f8d7da; color: #721c24; }}
  .expectation {{
    margin: 6px 0;
    padding: 8px;
    background: #f0f0f0;
    border-radius: 4px;
  }}
  .step {{
    margin: 6px 0;
    padding: 8px;
    border-left: 3px solid #3498db;
    background: #f0f8ff;
  }}
  .meta {{ color: #666; font-size: 0.85em; }}
  details {{ margin: 4px 0; }}
  summary {{ cursor: pointer; font-weight: bold; padding: 4px 0; }}
  .tool-details {{
    margin: 4px 0;
    padding: 4px 8px;
    background: #f3e8ff;
    border-radius: 4px;
    border-left: 3px solid #8e44ad;
  }}
  .tool-summary {{
    font-weight: normal;
    font-size: 0.9em;
    color: #6c3483;
    padding: 2px 0;
  }}
  .tool-data {{
    margin: 4px 0;
    padding: 8px;
    background: #faf5ff;
    border-radius: 4px;
    font-size: 0.8em;
    white-space: pre-wrap;
    word-break: break-word;
    overflow-x: auto;
  }}
  .tool-section {{ font-size: 0.85em; color: #555; margin-top: 6px; }}
  .run-dot {{
    display: inline-block;
    width: 12px;
    height: 12px;
    border-radius: 50%;
    margin-right: 3px;
    cursor: pointer;
    border: 2px solid transparent;
    transition: border-color 0.15s;
  }}
  .run-dot:hover {{ border-color: #333; }}
  .run-dot.p {{ background: #27ae60; }}
  .run-dot.f {{ background: #e74c3c; }}
  .run-dot.e {{ background: #e67e22; }}
  .session-link {{ font-size: 0.85em; color: #3498db; margin: 4px 0; }}
  .session-link a {{ color: #3498db; text-decoration: none; }}
  .session-link a:hover {{ text-decoration: underline; }}
</style>
<script>
function jumpToRun(evalName, runIdx) {{
  var card = document.getElementById('eval-' + evalName);
  if (!card) return;
  var details = card.querySelectorAll('details.run-detail');
  details.forEach(function(d) {{ d.removeAttribute('open'); }});
  if (details[runIdx]) {{
    details[runIdx].setAttribute('open', '');
  }}
  setTimeout(function() {{
    card.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
  }}, 50);
}}
</script>
</head><body>
"""


def _get_summary_block(
    passed, total, errors, modality, model, ts, wall_clock_s
):
    """Return the HTML summary block."""
    pct = 100 * passed / total if total else 0
    pass_threshold = 90
    cls = "pass" if pct >= pass_threshold else "fail"
    return f"""<h1>Simulation Eval Report</h1>
<div class="summary">
  <div class="big {cls}">{pct:.1f}%</div>
  <div>{passed}/{total} passed | {errors} errors |
    {modality} | model: {model}</div>
  <div class="meta">Generated {ts}
    {f" | Runtime: {_fmt_duration(wall_clock_s)}" if wall_clock_s else ""}</div>
</div>
"""


def _get_results_table(eval_stats):
    """Return the HTML results table."""
    html = """
<h2>Results by Eval</h2>
<table>
  <tr><th>Score</th><th>Eval</th><th>Runs</th></tr>
"""
    for name, s in sorted(
        eval_stats.items(), key=lambda x: x[1]["pass"] / max(x[1]["total"], 1)
    ):
        score = f"{s['pass']}/{s['total']}"
        cls = "pass" if s["pass"] == s["total"] else "fail"
        dots = ""
        for i, r in enumerate(s["runs"]):
            dot_cls = "p" if r.get("passed") else ("e" if "error" in r else "f")
            safe_name = name.replace("'", "\\'")
            dots += (
                f'<span class="run-dot {dot_cls}" title="Run {r["run"]}" '
                f"onclick=\"jumpToRun('{safe_name}', {i})\"></span>"
            )
        html += (
            f'  <tr><td class="{cls}"><b>{score}</b></td>'
            f"<td>{_escape(name)}</td><td>{dots}</td></tr>\n"
        )
    html += "</table>\n"
    return html


def _render_session_link(session_id, ces_base):
    """Render the session link."""
    if not session_id:
        return ""
    if ces_base:
        session_url = (
            f"{ces_base}?panel=conversation_list&id={session_id}&source=EVAL"
        )
        return (
            f'<div class="session-link">Session: '
            f'<a href="{session_url}" target="_blank">'
            f"<code>{session_id}</code></a></div>\n"
        )
    return (
        f'<div class="session-link">Session: <code>{session_id}</code></div>\n'
    )


def _render_session_parameters(sparams):
    """Render session parameters."""
    if not sparams:
        return ""
    html = (
        '<details class="tool-details"><summary class="tool-summary">'
        "&#9881; <b>Session Parameters</b></summary>"
    )
    html += (
        f'<pre class="tool-data">'
        f"{_escape(json.dumps(sparams, indent=2))}</pre></details>\n"
    )
    return html


def _render_step_details(step_details):
    """Render step details."""
    if not step_details:
        return ""
    html = ""
    for step in step_details:
        step_cls = "pass" if step["status"] == "Completed" else "fail"
        html += (
            f'<div class="step"><b>Goal:</b> {_escape(step["goal"])}<br>'
            f"<b>Criteria:</b> {_escape(step['success_criteria'])}<br>"
        )
        badge_cls = step_cls.replace("pass", "met").replace("fail", "not-met")
        html += (
            f'<b>Status:</b> <span class="badge {badge_cls}">'
            f"{_escape(step['status'])}</span><br>"
        )
        if step.get("justification"):
            html += f"<b>Justification:</b> {_escape(step['justification'])}"
        html += "</div>\n"
    return html


def _render_expectation_details(expectation_details):
    """Render expectation details."""
    if not expectation_details:
        return ""
    html = ""
    for exp in expectation_details:
        exp_cls = "met" if exp["status"] == "Met" else "not-met"
        html += f'<div class="expectation"><span class="badge {exp_cls}">'
        html += f"{_escape(exp['status'])}</span> {_escape(exp['expectation'])}"
        if exp.get("justification"):
            html += (
                f'<br><span class="meta">{_escape(exp["justification"])}</span>'
            )
        html += "</div>\n"
    return html


def _parse_trace(trace, tools_map):
    """Parse trace lines into typed entries."""
    parsed_lines = []
    for entry in trace:
        for line in entry.split("\n"):
            stripped_line = line.strip()
            if not stripped_line:
                continue
            formatted_line = _format_trace_line(stripped_line, tools_map)
            if formatted_line.startswith("Agent Text (Diag):"):
                continue
            elif formatted_line.startswith("Agent Text:"):
                parsed_lines.append(
                    ("agent", formatted_line[len("Agent Text:") :].strip())
                )
            elif formatted_line.startswith("User:"):
                parsed_lines.append(("user", formatted_line[5:].strip()))
            elif formatted_line.startswith("Tool Call"):
                parsed_lines.append(("tool_call", formatted_line))
            elif formatted_line.startswith("Tool Response"):
                parsed_lines.append(("tool_resp", formatted_line))
            elif formatted_line.startswith("Agent Transfer:"):
                parsed_lines.append(
                    (
                        "agent_transfer",
                        formatted_line[len("Agent Transfer:") :].strip(),
                    )
                )
            elif formatted_line.startswith("Custom Payload:"):
                parsed_lines.append(
                    (
                        "custom_payload",
                        formatted_line[len("Custom Payload:") :].strip(),
                    )
                )
            else:
                parsed_lines.append(("system", formatted_line))
    return parsed_lines


def _merge_trace_lines(parsed_lines):
    """Merge consecutive agent lines and pair tool calls with responses."""
    merged = []
    for kind, text in parsed_lines:
        if kind == "agent" and merged and merged[-1][0] == "agent":
            merged[-1] = ("agent", merged[-1][1] + " " + text)
        elif kind == "tool_resp" and merged and merged[-1][0] == "tool_call":
            merged[-1] = ("tool_pair", merged[-1][1], text)
        else:
            merged.append((kind, text))
    return merged


def _render_merged_items(merged):
    """Render merged trace items to HTML."""
    html = ""
    for item in merged:
        kind = item[0]
        if kind == "user":
            html += f'<div class="user"><b>User:</b> {_escape(item[1])}</div>\n'
        elif kind == "agent":
            html += (
                f'<div class="agent"><b>Agent:</b> {_escape(item[1])}</div>\n'
            )
        elif kind in ("tool_call", "tool_pair"):
            call_text = item[1]
            lbl, _, args = call_text.partition(" with args ")
            lbl = lbl.replace("Tool Call: ", "").replace(
                "Tool Call (Output): ", ""
            )
            lbl = lbl.split("/")[-1] if "/" in lbl else lbl
            html += (
                f'<details class="tool-details"><summary class="tool-summary">'
                f"&#128295; <b>{_escape(lbl)}</b></summary>"
            )
            if args:
                html += (
                    f'<div class="tool-section"><b>Input:</b></div>'
                    f'<pre class="tool-data">{_escape(args)}</pre>'
                )
            if kind == "tool_pair":
                _, _, result = item[2].partition(" with result ")
                if result:
                    html += (
                        f'<div class="tool-section"><b>Output:</b></div>'
                        f'<pre class="tool-data">{_escape(result)}</pre>'
                    )
            html += "</details>\n"
        elif kind == "tool_resp":
            lbl, _, result = item[1].partition(" with result ")
            lbl = lbl.replace("Tool Response: ", "").split("/")[-1]
            html += (
                f'<details class="tool-details"><summary class="tool-summary">'
                f"&#128228; <b>{_escape(lbl)}</b> response</summary>"
            )
            if result:
                html += f'<pre class="tool-data">{_escape(result)}</pre>'
            html += "</details>\n"
        elif kind == "agent_transfer":
            html += (
                f'<div class="system">&#128256; <b>Agent Transfer:</b>'
                f" {_escape(item[1])}</div>\n"
            )
        elif kind == "custom_payload":
            html += (
                f'<details class="tool-details">'
                f'<summary class="tool-summary">'
                f"&#128230; <b>Custom Payload</b></summary>"
                f'<pre class="tool-data">{_escape(item[1])}</pre>'
                f"</details>\n"
            )
        else:
            html += f'<div class="system">{_escape(item[1])}</div>\n'
    return html


def _render_trace(trace, tools_map, turns):
    """Render the conversation trace."""
    if not trace:
        return ""
    html = (
        f"<details open><summary>Conversation Trace "
        f"({turns} turns)</summary>\n"
        f'<div class="transcript">\n'
    )

    parsed_lines = _parse_trace(trace, tools_map)

    merged = _merge_trace_lines(parsed_lines)

    html += _render_merged_items(merged)

    html += "</div>\n</details>\n"
    return html


def _get_run_detail(r, ces_base, tools_map):
    """Return the HTML for a single run detail."""
    html = ""
    run_cls = "pass" if r.get("passed") else "fail"
    session_id = r.get("session_id", "")
    html += f'<details class="run-detail"{"" if not r.get("passed") else ""}>\n'
    html += (
        f"<summary>Run {r['run']} — "
        f'<span class="{run_cls}">'
        f"{'PASS' if r.get('passed') else 'FAIL'}</span>"
    )
    html += (
        f" | goals: {r.get('goals', '?')} | "
        f"expectations: {r.get('expectations', '?')} | "
        f"turns: {r.get('turns', '?')}</summary>\n"
    )

    html += _render_session_link(session_id, ces_base)

    sparams = r.get("session_parameters", {})
    html += _render_session_parameters(sparams)

    if "error" in r:
        html += (
            f'<div class="expectation"><b>Error:</b> '
            f"{_escape(r['error'])}</div>\n"
        )
    else:
        html += _render_step_details(r.get("step_details", []))

        html += _render_expectation_details(r.get("expectation_details", []))

        html += _render_trace(
            r.get("detailed_trace", []), tools_map, r.get("turns", "?")
        )

    html += "</details>\n"
    return html


def _upload_to_gcs(output_path: str, html: str) -> str | None:
    """Uploads the report to GCS and returns the mTLS URL or None on failure."""
    try:
        gcs = GCSUtils()
        mtls_url = gcs.upload_string(output_path, html)
        print(f"Report uploaded to GCS: {output_path}")
        print(f"Authenticated URL: {mtls_url}")
        return mtls_url
    except Exception as e:
        print(f"WARNING: GCS upload failed ({e}). Falling back to local file.")
        return None


def generate_html_report(
    results: List[Dict[str, Any]],
    output_path: str,
    modality: str,
    model: str,
    app_name: str = "",
    wall_clock_s: float = None,
    user_agent_extension: str = None,
):
    """Generate an HTML report and save it locally or upload to GCS.

    If output_path starts with 'gs://', the report is uploaded to GCS.
    If the upload fails, it falls back to saving a local file.
    """
    total = len(results)
    passed = sum(1 for r in results if r.get("passed"))
    errors = sum(1 for r in results if "error" in r)

    eval_stats = {}
    for r in results:
        n = r["name"]
        if n not in eval_stats:
            eval_stats[n] = {"pass": 0, "total": 0, "runs": []}
        eval_stats[n]["total"] += 1
        if r.get("passed"):
            eval_stats[n]["pass"] += 1
        eval_stats[n]["runs"].append(r)

    tools_map = {}
    if app_name:
        try:
            tools_map = Tools(
                app_name=app_name, user_agent_extension=user_agent_extension
            ).get_tools_map()
        except Exception:
            pass

    parts = app_name.split("/") if app_name else []
    project_id_idx = 1
    location_idx = 3
    app_id_idx = 5
    project_id = parts[project_id_idx] if len(parts) > project_id_idx else ""
    location = parts[location_idx] if len(parts) > location_idx else ""
    app_id = parts[app_id_idx] if len(parts) > app_id_idx else ""
    ces_base = (
        f"https://ces.cloud.google.com/projects/{project_id}/locations/{location}/apps/{app_id}"
        if app_id
        else ""
    )

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = _get_html_head(ts)
    html += _get_summary_block(
        passed, total, errors, modality, model, ts, wall_clock_s
    )

    html += _get_results_table(eval_stats)
    html += "\n<h2>Eval Details</h2>\n"

    for name, s in sorted(
        eval_stats.items(), key=lambda x: x[1]["pass"] / max(x[1]["total"], 1)
    ):
        score = f"{s['pass']}/{s['total']}"
        cls = "pass-bg" if s["pass"] == s["total"] else "fail-bg"
        html += f'<div class="eval-card" id="eval-{name}">\n'
        html += (
            f'<div class="eval-header {cls}">{_escape(name)} '
            f"<span>{score}</span></div>\n"
        )
        html += '<div class="eval-body">\n'

        for r in s["runs"]:
            html += _get_run_detail(r, ces_base, tools_map)
        html += "</div></div>\n"

    html += "</body></html>"

    if output_path.startswith("gs://"):
        mtls_url = _upload_to_gcs(output_path, html)
        if mtls_url:
            return

        # Fallback to local file if upload failed
        filename = output_path.rsplit("/", maxsplit=1)[-1]
        if not filename.endswith(".html"):
            filename = "report_fallback.html"
        output_path = filename

    with open(output_path, "w") as f:
        f.write(html)
    print(f"Report saved locally to: {output_path}")


def generate_combined_html_report(
    golden_results=None,
    sim_results=None,
    tool_results=None,
    callback_results=None,
    output_path="",
    app_name="",
    golden_modality="text",
    sim_modality="text",
    sim_wall_clock_s=None,
    user_agent_extension=None,
):
    """Generate combined HTML report based on results from multiple sources."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    g_total = len(golden_results) if golden_results else 0
    g_passed = (
        sum(1 for r in golden_results if r.get("passed"))
        if golden_results
        else 0
    )
    s_total = len(sim_results) if sim_results else 0
    s_passed = (
        sum(1 for r in sim_results if r.get("passed")) if sim_results else 0
    )
    t_total = len(tool_results) if tool_results else 0
    t_passed = (
        sum(1 for r in tool_results if r.get("passed")) if tool_results else 0
    )
    c_total = len(callback_results) if callback_results else 0
    c_passed = (
        sum(1 for r in callback_results if r.get("passed"))
        if callback_results
        else 0
    )
    total = g_total + s_total + t_total + c_total
    passed = g_passed + s_passed + t_passed + c_passed
    pct = 100 * passed / total if total else 0

    unified = []
    if golden_results:
        for r in golden_results:
            scores = [
                t["semantic_score"]
                for t in r.get("turns", [])
                if t.get("semantic_score") is not None
            ]
            avg_sem = sum(scores) / len(scores) if scores else None
            unified.append(
                {
                    "name": r["name"],
                    "type": "golden",
                    "passed": r.get("passed", False),
                    "score": "PASS" if r.get("passed") else "FAIL",
                    "detail": f"sem {avg_sem:.1f}/4"
                    if avg_sem is not None
                    else "",
                    "runs": 1,
                }
            )
    if sim_results:
        sim_stats = {}
        for r in sim_results:
            n = r["name"]
            if n not in sim_stats:
                sim_stats[n] = {"pass": 0, "total": 0, "runs": []}
            sim_stats[n]["total"] += 1
            if r.get("passed"):
                sim_stats[n]["pass"] += 1
            sim_stats[n]["runs"].append(r)
        for name, s in sim_stats.items():
            unified.append(
                {
                    "name": name,
                    "type": "sim",
                    "passed": s["pass"] == s["total"],
                    "score": f"{s['pass']}/{s['total']}",
                    "detail": "",
                    "runs": s["total"],
                    "run_results": s["runs"],
                }
            )

    if tool_results:
        for r in tool_results:
            unified.append(
                {
                    "name": r["name"],
                    "type": "tool",
                    "passed": r.get("passed", False),
                    "score": r.get("status", "?"),
                    "detail": (
                        f"{r.get('latency_ms', 0):.0f}ms"
                        if r.get("latency_ms")
                        else ""
                    ),
                    "runs": 1,
                }
            )

    if callback_results:
        for r in callback_results:
            unified.append(
                {
                    "name": r["name"],
                    "type": "callback",
                    "passed": r.get("passed", False),
                    "score": r.get("status", "?"),
                    "detail": r.get("callback_type", ""),
                    "runs": 1,
                }
            )

    unified.sort(key=lambda x: (x["passed"], x["name"]))

    parts = app_name.split("/") if app_name else []
    project_id = parts[1] if len(parts) > 1 else ""
    location = parts[3] if len(parts) > 3 else ""
    app_id = parts[5] if len(parts) > 5 else ""
    ces_base = (
        f"https://ces.cloud.google.com/projects/{project_id}/locations/{location}/apps/{app_id}"
        if app_id
        else ""
    )

    # Compute summary cards data
    g_duration = (
        sum(r.get("duration_s", 0) or 0 for r in golden_results)
        if golden_results
        else 0
    )
    s_dur = (
        sum(r.get("duration_s", 0) or 0 for r in sim_results)
        if sim_results
        else 0
    )

    # --- FAILURE GROUPING ---
    failure_groups = {}

    # Collect golden failures
    if golden_results:
        for r in golden_results:
            if r.get("passed"):
                continue
            for turn in r.get("turns", []):
                for comp in turn.get("comparisons", []):
                    if comp.get("outcome") == "FAIL":
                        ctype = comp.get("type", "?")
                        expected = str(comp.get("expected", ""))[:60]
                        actual = str(comp.get("actual", ""))[:60]
                        if ctype == "transfer":
                            if actual == "(missed)":
                                reason = (
                                    f"Routing missed: expected transfer to "
                                    f"{expected}"
                                )
                            else:
                                reason = (
                                    f"Wrong routing: expected {expected}, "
                                    f"got {actual}"
                                )
                        elif ctype == "tool_call" and actual == "(missed)":
                            if expected:
                                reason = f"Tool not called: {expected}"
                            else:
                                continue
                        elif ctype == "tool_call" and expected != actual:
                            reason = (
                                f"Wrong tool: expected {expected}, got {actual}"
                            )
                        elif ctype == "text":
                            reason = "Semantic similarity too low"
                        else:
                            continue
                        failure_groups.setdefault(reason, set()).add(
                            ("golden", r["name"])
                        )
            for exp in r.get("expectations", []):
                if exp.get("status") == "Not Met":
                    reason = str(exp.get("expectation", ""))[:80]
                    failure_groups.setdefault(
                        f"Expectation not met: {reason}", set()
                    ).add(("golden", r["name"]))

    # Collect sim failures
    if sim_results:
        for r in sim_results:
            if r.get("passed"):
                continue
            for step in r.get("step_details", []):
                if step.get("status") != "Completed":
                    reason = f"Goal not completed: {step.get('goal', '')[:60]}"
                    failure_groups.setdefault(reason, set()).add(
                        ("sim", r["name"])
                    )
            for exp in r.get("expectation_details", []):
                if exp.get("status") == "Not Met":
                    reason = (
                        f"Expectation not met: "
                        f"{exp.get('expectation', '')[:60]}"
                    )
                    failure_groups.setdefault(reason, set()).add(
                        ("sim", r["name"])
                    )

    # Collect tool test failures
    if tool_results:
        for r in tool_results:
            if r.get("passed"):
                continue
            errors = str(r.get("errors", ""))
            if (
                "operator='Operator.CONTAINS'" in errors
                and "expected='PASSED'" in errors
            ):
                reason = (
                    "Default expectation: $.result contains PASSED "
                    "(needs customization)"
                )
            elif "operator='Operator" in errors:
                reason = (
                    errors.split(",", maxsplit=1)[0][:80]
                    if "," in errors
                    else errors[:80]
                )
            else:
                reason = errors[:80]
            failure_groups.setdefault(reason, set()).add(("tool", r["name"]))

    # Collect callback failures
    if callback_results:
        for r in callback_results:
            if r.get("passed"):
                continue
            reason = str(r.get("error", "Unknown error"))[:80]
            failure_groups.setdefault(f"Callback: {reason}", set()).add(
                ("callback", r["name"])
            )

    # Prepare tools map for template if needed
    tools_map = {}
    if app_name:
        try:
            tools_map = Tools(
                app_name=app_name, user_agent_extension=user_agent_extension
            ).get_tools_map()
        except Exception:
            pass

    # Process traces for simulation results to simplify template
    if sim_results:
        for r in sim_results:
            trace = r.get("detailed_trace", [])
            if trace:
                parsed = []
                for entry in trace:
                    for raw_line in entry.split("\n"):
                        line = raw_line.strip()
                        if not line or line.startswith("Agent Text (Diag):"):
                            continue
                        for path, dname in tools_map.items():
                            line = line.replace(path, dname)
                        if line.startswith("Agent Text:"):
                            parsed.append(
                                ("agent", line[len("Agent Text:") :].strip())
                            )
                        elif line.startswith("User:"):
                            parsed.append(("user", line[5:].strip()))
                        elif line.startswith("Tool Call"):
                            parsed.append(("tool_call", line))
                        elif line.startswith("Tool Response"):
                            parsed.append(("tool_resp", line))
                        else:
                            parsed.append(("system", line))

                merged = []
                for kind, text in parsed:
                    if kind == "agent" and merged and merged[-1][0] == "agent":
                        merged[-1] = ("agent", merged[-1][1] + " " + text)
                    elif (
                        kind == "tool_resp"
                        and merged
                        and merged[-1][0] == "tool_call"
                    ):
                        merged[-1] = ("tool_pair", merged[-1][1], text)
                    else:
                        merged.append((kind, text))
                r["_processed_trace"] = merged

    template_path = os.path.join(
        os.path.dirname(__file__), "combined_report_template.html"
    )
    with open(template_path, "r") as f:
        template_content = f.read()
    template = Template(template_content)
    html = template.render(
        ts=ts,
        pct=pct,
        passed=passed,
        total=total,
        unified=unified,
        golden_results=golden_results or [],
        sim_results=sim_results or [],
        tool_results=tool_results or [],
        callback_results=callback_results or [],
        ces_base=ces_base,
        golden_modality=golden_modality,
        sim_modality=sim_modality,
        sim_wall_clock_s=sim_wall_clock_s,
        failure_groups=failure_groups,
        g_passed=g_passed,
        g_total=g_total,
        g_duration=g_duration,
        s_passed=s_passed,
        s_total=s_total,
        s_dur=s_dur,
        t_passed=t_passed,
        t_total=t_total,
        c_passed=c_passed,
        c_total=c_total,
        _escape=_escape,
        _fmt_duration=_fmt_duration,
        json=json,
        css_content=load_component("base/base.css"),
        js_interaction=load_component("base/interaction.js"),
    )

    if output_path:
        if output_path.startswith("gs://"):
            mtls_url = _upload_to_gcs(output_path, html)
            if not mtls_url:
                # Fallback to local file if upload failed
                filename = output_path.rsplit("/", maxsplit=1)[-1]
                if not filename.endswith(".html"):
                    filename = "report_fallback.html"
                output_path = filename
                with open(output_path, "w") as f:
                    f.write(html)
        else:
            with open(output_path, "w") as f:
                f.write(html)

    return html


def _outcome_str(val):
    if isinstance(val, int):
        return {0: "UNSPECIFIED", 1: "PASS", 2: "FAIL"}.get(val, f"?{val}")
    return str(val) if val else "?"


def load_golden_results(
    run_id, app_name, include=None, user_agent_extension=None
):
    """Fetch golden results and parse into report-friendly format."""
    if include is None:
        include = ["goldens", "scenarios"]

    utils = EvalUtils(app_name=app_name)
    full_run_id = (
        run_id
        if run_id.startswith("projects/")
        else f"{app_name}/evaluationRuns/{run_id}"
    )

    raw_results = utils.list_evaluation_results_by_run(full_run_id)

    evals_map = utils.get_evaluations_map(app_name, reverse=False)
    name_lookup = {}
    for cat in ["goldens", "scenarios"]:
        for resource, display in evals_map.get(cat, {}).items():
            name_lookup[resource] = display

    results = []
    for r in raw_results:
        rd = type(r).to_dict(r)
        result_name = rd.get("name", "")
        eval_resource = "/".join(result_name.split("/")[:-2])

        is_golden = eval_resource in evals_map.get("goldens", {})
        is_scenario = eval_resource in evals_map.get("scenarios", {})

        if is_golden and "goldens" not in include:
            continue
        if is_scenario and "scenarios" not in include:
            continue

        display_name = name_lookup.get(
            eval_resource, eval_resource.split("/")[-1]
        )

        status_raw = rd.get("evaluation_status", 0)
        passed = (
            (status_raw == 1)
            if isinstance(status_raw, int)
            else str(status_raw).upper() == "PASS"
        )

        golden = rd.get("golden_result", {})

        turns = []
        for i, turn in enumerate(golden.get("turn_replay_results", [])):
            sem = turn.get("semantic_similarity_result", {})
            turn_data = {
                "index": i + 1,
                "semantic_score": sem.get("score"),
                "semantic_explanation": sem.get("explanation"),
                "comparisons": [],
            }
            for o in turn.get("expectation_outcome", []):
                exp = o.get("expectation", {})
                outcome = _outcome_str(o.get("outcome"))
                comp = {"outcome": outcome}

                if "agent_response" in exp:
                    chunks = exp["agent_response"].get("chunks", [])
                    comp["type"] = "text"
                    comp["expected"] = (
                        chunks[0].get("text", "") if chunks else ""
                    )
                    obs = o.get("observed_agent_response", {})
                    comp["actual"] = (
                        obs.get("chunks", [{}])[0].get("text", "")
                        if obs
                        else "(missed)"
                    )
                elif "tool_call" in exp:
                    tc = exp["tool_call"]
                    comp["type"] = "tool_call"
                    comp["expected"] = (
                        tc.get("display_name")
                        or tc.get("tool", "").split("/")[-1]
                    )
                    comp["expected_args"] = tc.get("args", {})
                    obs = o.get("observed_tool_call", {})
                    comp["actual"] = (
                        (
                            obs.get("display_name")
                            or obs.get("tool", "").split("/")[-1]
                        )
                        if obs
                        else "(missed)"
                    )
                    comp["actual_args"] = obs.get("args", {}) if obs else {}
                    tir = o.get("toolInvocationResult", {})
                    comp["tool_invocation_score"] = tir.get(
                        "parameterCorrectnessScore"
                    )
                    comp["tool_invocation_explanation"] = tir.get("explanation")
                elif "tool_response" in exp:
                    continue
                elif "agent_transfer" in exp:
                    at = exp["agent_transfer"]
                    comp["type"] = "transfer"
                    comp["expected"] = at.get(
                        "display_name",
                        at.get("target_agent", "").split("/")[-1],
                    )
                    obs = o.get("observed_agent_transfer", {})
                    comp["actual"] = (
                        obs.get(
                            "display_name",
                            obs.get("target_agent", "").split("/")[-1],
                        )
                        if obs
                        else "(missed)"
                    )
                else:
                    continue

                turn_data["comparisons"].append(comp)
            turns.append(turn_data)

        expectations = []
        for ee in golden.get("evaluation_expectation_results", []):
            result_val = ee.get("outcome", ee.get("result"))
            exp_text = ee.get("prompt", ee.get("evaluation_expectation", ""))
            explanation = ee.get("explanation", "")
            met = (
                result_val == 1
                if isinstance(result_val, int)
                else str(result_val).upper() == "PASS"
            )
            expectations.append(
                {
                    "expectation": exp_text,
                    "status": "Met" if met else "Not Met",
                    "justification": explanation,
                }
            )

        session_id = ""
        if golden.get("turn_replay_results"):
            conv_path = golden["turn_replay_results"][0].get("conversation", "")
            if conv_path:
                # Extract the conversation ID (e.g. "evaluation-xxxx")
                session_id = conv_path.split("/")[-1]

        session_params = {}
        # One entry per golden turn: ("text", "...") or ("event", "...")
        turn_inputs = []
        try:
            ev_obj = utils.get_evaluation(eval_resource)
            evd = type(ev_obj).to_dict(ev_obj)
            golden_def = evd.get("golden", {})
            for turn_def in golden_def.get("turns", []):
                turn_input = None
                for step in turn_def.get("steps", []):
                    ui = step.get("user_input", {})
                    if "variables" in ui:
                        session_params.update(ui["variables"])
                    if "text" in ui:
                        turn_input = ("text", ui["text"])
                    elif "event" in ui:
                        turn_input = ("event", str(ui["event"]))
                if turn_input:
                    turn_inputs.append(turn_input)
        except Exception:
            pass

        for i, turn in enumerate(turns):
            if i < len(turn_inputs):
                kind, text = turn_inputs[i]
                turn["user_input"] = text if kind == "text" else None
            else:
                turn["user_input"] = None

        total_latency_s = 0
        for turn_result in golden.get("turn_replay_results", []):
            lat = turn_result.get("turn_latency", "")
            if isinstance(lat, str) and lat.endswith("s"):
                try:
                    total_latency_s += float(lat.replace("s", ""))
                except ValueError:
                    pass
            elif isinstance(lat, dict):
                total_latency_s += lat.get("seconds", 0) + (
                    lat.get("nanos", 0) / 1e9
                )

        results.append(
            {
                "name": display_name,
                "passed": passed,
                "turns": turns,
                "expectations": expectations,
                "session_id": session_id,
                "session_parameters": session_params,
                "duration_s": (
                    round(total_latency_s, 1) if total_latency_s > 0 else None
                ),
            }
        )

    return results


def _load_sim_test_cases(yaml_path: str) -> list[dict]:
    """Loads sim files and merges common params and expectations."""
    with open(yaml_path) as f:
        data = yaml.safe_load(f) or {}
    if isinstance(data, list):
        return data

    common_params = data.get("common_session_parameters", {}) or {}
    common_expectations = data.get("common_expectations", []) or []
    cases = data.get("evals", [])
    if not isinstance(cases, list):
        return []

    merged_cases = []
    for c in cases:
        if isinstance(c, dict):
            case_copy = c.copy()
            # Merge session parameters
            case_params = case_copy.get("session_parameters", {}) or {}
            merged = common_params.copy()
            merged.update(case_params)
            case_copy["session_parameters"] = merged

            # Merge expectations
            case_expectations = case_copy.get("expectations", []) or []
            case_copy["expectations"] = common_expectations + case_expectations

            merged_cases.append(case_copy)
    return merged_cases


def load_sim_results(json_path, sim_evals_yaml=None):
    """Load sim results from JSON file.

    Handles both old (list) and new (envelope) formats.
    """
    with open(json_path) as f:
        data = json.load(f)

    wall_clock_s = None
    # New envelope format: {"wall_clock_s": N, "results": [...]}
    # Old format: [...]
    if isinstance(data, dict):
        wall_clock_s = data.get("wall_clock_s")
        results = data.get("results", [])
    else:
        results = data

    # Backfill session_parameters if missing
    if sim_evals_yaml:
        try:
            eval_list = _load_sim_test_cases(sim_evals_yaml)
            templates = {
                e["name"]: e
                for e in eval_list
                if isinstance(e, dict) and "name" in e
            }
            for r in results:
                if "session_parameters" not in r and r.get("name") in templates:
                    r["session_parameters"] = templates[r["name"]].get(
                        "session_parameters", {}
                    )
        except Exception:
            pass

    return results, wall_clock_s


def load_tool_test_results(csv_or_json_path):
    """Load tool test results from a CSV or JSON file."""
    if csv_or_json_path.endswith(".csv"):
        df = pd.read_csv(csv_or_json_path)
    else:
        df = pd.read_json(csv_or_json_path)
    results = []
    for _, row in df.iterrows():
        results.append(
            {
                "name": row.get("test_name", row.get("test", "?")),
                "tool": row.get("tool", "?"),
                "passed": row.get("status", "").upper() == "PASSED",
                "status": row.get("status", "?"),
                "latency_ms": row.get("latency (ms)", 0),
                "errors": row.get("errors", ""),
            }
        )
    return results


def load_callback_test_results(csv_or_json_path):
    """Load callback test results from a CSV or JSON file."""
    if csv_or_json_path.endswith(".csv"):
        df = pd.read_csv(csv_or_json_path)
    else:
        df = pd.read_json(csv_or_json_path)
    results = []
    for _, row in df.iterrows():
        results.append(
            {
                "name": row.get("test_name", "?"),
                "agent": row.get("agent_name", "?"),
                "callback_type": row.get("callback_type", "?"),
                "passed": row.get("status", "").upper() == "PASSED",
                "status": row.get("status", "?"),
                "error": row.get("error_message", ""),
            }
        )
    return results


def generate_combined_report_from_dir(
    output_dir,
    golden_run=None,
    app_name=None,
    output_path=None,
    run=False,
    app_dir=None,
    tool_test_file=None,
    goldens_dir=None,
    simulation_dir=None,
    format="html",
    include=None,
    modality="text",
    runs=1,
    filter_files=None,
    filter_tags=None,
    parallel=1,
    golden_timeout=600,
):
    """Load results from directory and generate combined HTML report."""
    if not os.path.isdir(output_dir):
        raise ValueError(f"{output_dir} is not a directory.")

    if include is None or "all" in include:
        include = ["sims", "goldens", "tools", "callbacks"]

    sim_results = []
    tool_results = []
    callback_results = []
    golden_results = []

    if run:
        run_results = run_all_evals(
            app_name=app_name,
            app_dir=app_dir,
            tool_test_file=tool_test_file,
            goldens_dir=goldens_dir,
            simulation_dir=simulation_dir,
            output_dir=output_dir,
            modality=modality,
            runs=runs,
            filter_files=filter_files,
            filter_tags=filter_tags,
            parallel=parallel,
            golden_timeout=golden_timeout,
            include=include,
        )
        sim_results = run_results["simulation"] if "sims" in include else []
        # Map tool results to expected format if needed
        if "tools" in include:
            for r in run_results["tool"]:
                tool_results.append(
                    {
                        "name": r.get("test_name", r.get("test", "?")),
                        "tool": r.get("tool", "?"),
                        "passed": r.get("status", "").upper()
                        in ("PASSED", "PASS"),
                        "status": r.get("status", "?"),
                        "latency_ms": r.get("latency (ms)", 0),
                        "errors": r.get("errors", ""),
                    }
                )
        # Map callback results
        if "callbacks" in include:
            for r in run_results["callback"]:
                callback_results.append(
                    {
                        "name": r.get("test_name", "?"),
                        "agent": r.get("agent_name", "?"),
                        "callback_type": r.get("callback_type", "?"),
                        "passed": r.get("status", "").upper()
                        in ("PASSED", "PASS"),
                        "status": r.get("status", "?"),
                        "error": r.get("error_message", ""),
                    }
                )
        golden_results = run_results["golden"] if "goldens" in include else []
    else:
        sim_files = []
        if "sims" in include:
            sim_files = glob.glob(os.path.join(output_dir, "sim_results*.json"))

        tool_files = []
        callback_files = []
        if "tools" in include:
            tool_files = glob.glob(
                os.path.join(output_dir, "tool_results*.csv")
            )
            tool_files.extend(
                glob.glob(os.path.join(output_dir, "tool_results*.json"))
            )
        if "callbacks" in include:
            callback_files = glob.glob(
                os.path.join(output_dir, "callback_results*.csv")
            )
            callback_files.extend(
                glob.glob(os.path.join(output_dir, "callback_results*.json"))
            )

        if sim_files:
            with open(sim_files[0]) as f:
                data = json.load(f)
                # New envelope format: {"wall_clock_s": N, "results": [...]}
                # Old format: [...]
                if isinstance(data, dict):
                    sim_results = data.get("results", [])
                else:
                    sim_results = data
            print(f"Loaded {len(sim_results)} sim results from {sim_files[0]}")

        if tool_files:
            tf = tool_files[0]
            if tf.endswith(".csv"):
                df = pd.read_csv(tf)
            else:
                df = pd.read_json(tf)
            for _, row in df.iterrows():
                tool_results.append(
                    {
                        "name": row.get("test_name", row.get("test", "?")),
                        "tool": row.get("tool", "?"),
                        "passed": row.get("status", "").upper()
                        in ("PASSED", "PASS"),
                        "status": row.get("status", "?"),
                        "latency_ms": row.get("latency (ms)", 0),
                        "errors": row.get("errors", ""),
                    }
                )
            print(f"Loaded {len(tool_results)} tool results from {tf}")

        if callback_files:
            cf = callback_files[0]
            if cf.endswith(".csv"):
                df = pd.read_csv(cf)
            else:
                df = pd.read_json(cf)
            for _, row in df.iterrows():
                callback_results.append(
                    {
                        "name": row.get("test_name", "?"),
                        "agent": row.get("agent_name", "?"),
                        "callback_type": row.get("callback_type", "?"),
                        "passed": row.get("status", "").upper()
                        in ("PASSED", "PASS"),
                        "status": row.get("status", "?"),
                        "error": row.get("error_message", ""),
                    }
                )
            print(f"Loaded {len(callback_results)} callback results from {cf}")

        if golden_run:
            if not app_name:
                raise ValueError(
                    "--app-name is required when golden_run is specified."
                )
            golden_results = load_golden_results(
                golden_run, app_name, include=include
            )

    if not output_path:
        output_path = os.path.join(output_dir, "combined_report.html")

    return generate_combined_html_report(
        golden_results=golden_results,
        sim_results=sim_results,
        tool_results=tool_results,
        callback_results=callback_results,
        output_path=output_path,
        app_name=app_name or "",
        golden_modality=modality,
        sim_modality=modality,
    )


def run_all_evals(
    app_name,
    app_dir=None,
    tool_test_file=None,
    goldens_dir=None,
    simulation_dir=None,
    output_dir=None,
    modality="text",
    runs=1,
    filter_files=None,
    filter_tags=None,
    parallel=1,
    golden_timeout=600,
    include=None,
):
    """Runs all 4 types of evaluations and returns aggregated results.

    Deprecated legacy wrapper. Use
    `cxas_scrapi.evals.runner.run_all_evals` directly.
    """
    return evals_runner.run_all_evals(
        app_name=app_name,
        modality=modality,
        runs=runs,
        goldens_dir=goldens_dir,
        tool_test_file=tool_test_file,
        simulation_dir=simulation_dir,
        app_dir=app_dir,
        output_dir=output_dir,
        filter_files=filter_files,
        filter_tags=filter_tags,
        parallel=parallel,
        golden_timeout=golden_timeout,
        include=include,
    )
