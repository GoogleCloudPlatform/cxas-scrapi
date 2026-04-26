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

import json
from datetime import datetime
from typing import Any, Dict, List

from cxas_scrapi.core.tools import Tools


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
  th, td {{
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


def generate_html_report(
    results: List[Dict[str, Any]],
    output_path: str,
    modality: str,
    model: str,
    app_name: str = "",
    wall_clock_s: float = None,
):
    """Generate an HTML report with transcripts, tool calls,

    and failure details.
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
            tools_map = Tools(app_name=app_name).get_tools_map()
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
    with open(output_path, "w") as f:
        f.write(html)
