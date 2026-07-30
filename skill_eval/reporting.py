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


"""Reporting logic for CXAS skill benchmarking."""

import dataclasses
import datetime
import json
import os
import pathlib
import re
import typing
import urllib.parse
from collections.abc import Sequence
from typing import Any

import markdown

from skill_eval import benchmark, scenario

_TRAJECTORY_URL_TEMPLATE = os.environ.get("TRAJECTORY_VIEWER_URL", "")

_INDEX_HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <title>Agent Evaluation Benchmark</title>
  <style>
    body {{ font-family: 'Google Sans', sans-serif; margin: 2rem; background: #f8f9fa; color: #3c4043; display: flex; flex-direction: column; min-height: 90vh; }}
    h1 {{ color: #1a73e8; margin-bottom: 2rem; }}
    .summary-card {{ background: white; border-radius: 8px; padding: 1.5rem; box-shadow: 0 1px 3px rgba(0,0,0,0.12); margin-bottom: 2rem; flex-grow: 1; }}
    table {{ width: 100%; border-collapse: collapse; background: white; }}
    th, td {{ padding: 12px 16px; text-align: left; border-bottom: 1px solid #e0e0e0; }}
    th {{ background-color: #f1f3f4; font-weight: 500; color: #5f6368; text-transform: uppercase; font-size: 0.85rem; letter-spacing: 0.5px; }}
    tr:hover {{ background-color: #f8f9fa; }}
    .scenario-cell {{ font-weight: 500; color: #202124; min-width: 200px; }}
    .status-badge {{ display: inline-block; padding: 4px 12px; border-radius: 16px; font-size: 0.8rem; font-weight: 500; min-width: 80px; text-align: center; }}
    .status-pending {{ background: #f1f3f4; color: #5f6368; }}
    .status-running {{ background: #e8f0fe; color: #1967d2; animation: pulse 2s infinite; }}
    .status-pass {{ background: #e6f4ea; color: #137333; }}
    .status-warning {{ background: #fef7e0; color: #b05e00; }}
    .status-fail {{ background: #fce8e6; color: #c5221f; }}
    .metric-line {{ font-size: 0.8rem; color: #5f6368; margin-top: 2px; }}
    .details-link {{ color: #1a73e8; text-decoration: none; font-size: 0.9rem; font-weight: 500; display: inline-block; margin-top: 8px; margin-right: 8px; }}
    .details-link:hover {{ text-decoration: underline; }}
    footer {{ margin-top: 3rem; padding: 1.5rem; border-top: 1px solid #e0e0e0; color: #70757a; font-size: 0.85rem; text-align: center; }}
    @keyframes pulse {{
      0% {{ opacity: 1; }}
      50% {{ opacity: 0.6; }}
      100% {{ opacity: 1; }}
    }}
  </style>
</head>
<body>
  <h1>Agent Evaluation Benchmark</h1>
  <div class="summary-card">
    <table>
      <thead>
        <tr>
          <th>Scenario</th>
          {head_headers}
        </tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>
  </div>
  <footer>
    Evaluation Period: <strong>{eval_start}</strong> &mdash; <strong>{eval_end}</strong>
    {suite_duration_html}
  </footer>
</body>
</html>
"""

_DETAIL_HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
  <title>Details: {scenario} - {head}</title>
  <style>
    body {{ font-family: 'Roboto', sans-serif; margin: 2rem; background: #f8f9fa; line-height: 1.6; color: #3c4043; }}
    h1 {{ color: #1a73e8; }}
    .summary-card {{ background: white; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.12); padding: 1.5rem; margin-bottom: 2rem; }}
    .turn-container {{ margin-bottom: 1.5rem; border-left: 4px solid #e0e0e0; padding-left: 1rem; }}
    .turn-header {{ font-size: 0.85rem; color: #70757a; margin-bottom: 0.25rem; display: flex; justify-content: space-between; }}
    .turn-title {{ margin: 0; font-weight: 500; color: #1a73e8; }}
    .turn {{ padding: 0.75rem; border-radius: 4px; position: relative; }}
    .user {{ background: #e8f0fe; border-left: 4px solid #1a73e8; }}
    .agent {{ background: #f1f3f4; border-left: 4px solid #70757a; margin-top: 0.5rem; }}
    .turn-meta {{ display: flex; gap: 1rem; font-size: 0.75rem; color: #70757a; margin-top: 0.25rem; }}
    .timing-pill {{ background: #e8eaed; padding: 2px 8px; border-radius: 10px; }}
    .tool-interactions {{ margin-top: 1rem; background: #fff; border: 1px solid #dadce0; border-radius: 6px; padding: 1rem; }}
    .tool-interactions details {{ margin-bottom: 0.5rem; }}
    .tool-interactions summary {{ cursor: pointer; color: #1a73e8; font-weight: 500; padding: 4px; }}
    .tool-details {{ padding: 1rem; background: #f8f9fa; border-top: 1px solid #eee; margin-top: 0.5rem; }}
    .markdown-body pre {{ background: #f8f9fa; padding: 1rem; border-radius: 4px; overflow-x: auto; }}
    .status-badge {{ padding: 4px 12px; border-radius: 12px; font-size: 0.85rem; font-weight: 500; display: inline-block; }}
    .status-pass {{ background: #e6f4ea; color: #137333; }}
    .status-fail {{ background: #fce8e6; color: #c5221f; }}
    .status-warning {{ background: #fef7e0; color: #b05e00; }}
    .status-running {{ background: #e8f0fe; color: #1a73e8; animation: pulse 2s infinite; }}
    .status-initializing {{ background: #e8f0fe; color: #1a73e8; animation: pulse 2s infinite; }}
    .status-conversing {{ background: #e8f0fe; color: #1a73e8; animation: pulse 2s infinite; }}
    .status-grading {{ background: #e6f4ea; color: #137333; animation: pulse 2s infinite; }}
    @keyframes pulse {{ 0% {{ opacity: 1; }} 50% {{ opacity: 0.5; }} 100% {{ opacity: 1; }} }}
    pre {{ background: #f8f9fa; padding: 0.5rem; overflow-x: auto; border: 1px solid #dee2e6; border-radius: 3px; max-height: 300px; font-size: 0.85rem; }}
    .scenario-intro {{ background: #e8f0fe; padding: 1.5rem; border-radius: 8px; margin-bottom: 2rem; border: 1px solid #d2e3fc; }}
    .termination-signal {{ color: #c5221f; background: #fce8e6; padding: 1rem; border-radius: 4px; border: 1px solid #f5c6cb; margin-top: 1rem; font-weight: bold; }}
    footer {{ margin-top: 3rem; padding: 1.5rem; border-top: 1px solid #e0e0e0; color: #70757a; font-size: 0.85rem; }}
    .time-stats {{ display: flex; gap: 2rem; color: #5f6368; font-size: 0.85rem; margin-top: 0.5rem; }}
  </style>
</head>
<body>
  <h1>Details: {scenario} <span style="color: #70757a; font-weight: normal;">&mdash; {head}</span></h1>
  <p><a href="index.html" style="color: #1a73e8; text-decoration: none; font-weight: 500;">&larr; Back to Summary</a></p>
{banners}
{intro}
{rubric}
<h3>Conversation History</h3>
{history}
{log_links}
{latency_table}

  <footer>
    <div class="time-stats">
      <span>Started: <strong>{start_time}</strong></span>
      <span>Ended: <strong>{end_time}</strong></span>
      <span>Total Duration: <strong>{total_duration}s</strong></span>
      {scoring_latency_html}
    </div>
  </footer>
</body>
</html>
"""


class _ReportEncoder(json.JSONEncoder):
    """Custom JSON encoder for benchmark results."""

    def default(self, o: Any) -> Any:
        if isinstance(o, benchmark.ExecutionStatus):
            return o.value
        if isinstance(o, datetime.timedelta):
            return o.total_seconds()
        return super().default(o)


def generate_json_report(result: benchmark.ConversationResult) -> str:
    """Generates a serialized JSON representation of the result."""
    return json.dumps(dataclasses.asdict(result), indent=2, cls=_ReportEncoder)


def _escape_html(text: Any) -> str:
    """Escapes text for safe HTML rendering."""
    if text is None:
        return ""
    s = str(text)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _format_ts(ts: float) -> str:
    """Formats a unix timestamp for display."""
    if ts <= 0:
        return "N/A"
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def generate_index_html(
    results: Sequence[benchmark.ConversationResult],
    suite_duration: datetime.timedelta = datetime.timedelta(),
) -> str:
    """Generates the main index.html summary page."""
    by_scenario = _group_by_scenario(results)
    all_heads = sorted(list(set(r.head_name for r in results)))

    eval_start_val = min(
        (r.start_time for r in results if r.start_time > 0), default=0
    )
    eval_end_val = max(
        (r.end_time for r in results if r.end_time > 0), default=0
    )

    eval_start = _format_ts(eval_start_val)
    eval_end = _format_ts(eval_end_val)

    head_headers = "".join(f"<th>{_escape_html(h)}</th>" for h in all_heads)

    rows_html = ""
    if not results:
        rows_html = (
            '<tr><td colspan="100%" style="text-align: center; padding: 2rem;'
            ' color: #70757a;">Initializing benchmark suite...</td></tr>'
        )

    for scenario_name in sorted(by_scenario.keys()):
        rows_html += (
            f'<tr><td class="scenario-cell">{_escape_html(scenario_name)}</td>'
        )
        head_map = {r.head_name: r for r in by_scenario[scenario_name]}

        for head_name in all_heads:
            res = head_map.get(head_name)
            if not res:
                rows_html += "<td>-</td>"
                continue

            status_class = f"status-{res.status.value.lower()}"
            status_text = res.status.value

            if res.status == benchmark.ExecutionStatus.FINISHED:
                earned, total = 0, 0
                if res.rubric_results:
                    earned = res.rubric_results.total_score
                    total = res.rubric_results.max_score
                    status_text = f"Score: {earned}/{total}"
                    pct = (earned / total) if total > 0 else 0
                    if pct >= 1.0:
                        status_class = "status-pass"
                    elif pct >= 0.5:
                        status_class = "status-warning"
                    else:
                        status_class = "status-fail"
                else:
                    status_class = "status-fail"
                    status_text = "FAILED"

            safe_scenario_name = scenario.get_safe_filename(res.scenario_name)
            link_html = (
                f'<a href="detail_{safe_scenario_name}_{_escape_html(res.head_name)}.html"'
                ' class="details-link">Details</a>'
            )
            if getattr(res, "trajectory_id", None) and _TRAJECTORY_URL_TEMPLATE:
                url = _TRAJECTORY_URL_TEMPLATE.replace(
                    "{id}", _escape_html(res.trajectory_id)
                )
                link_html += f'<a href="{url}" target="_blank" class="details-link">Trajectory</a>'

            rows_html += f"""
        <td>
          <div class="status-badge {status_class}">{status_text}</div>
          <div class="metric-line">Turns: {res.turns} | Time: {res.total_time_sec:.1f}s</div>
          {link_html}
        </td>
      """
        rows_html += "</tr>"

    suite_duration_html = ""
    if suite_duration.total_seconds() > 0:
        suite_duration_html = (
            " &mdash; Suite Duration:"
            f" <strong>{suite_duration.total_seconds():.1f}s</strong>"
        )

    return _INDEX_HTML_TEMPLATE.format(
        head_headers=head_headers,
        rows=rows_html,
        eval_start=eval_start,
        eval_end=eval_end,
        suite_duration_html=suite_duration_html,
    )


def generate_detail_html(
    result: benchmark.ConversationResult,
    report_dir: str | None = None,
) -> str:
    """Generates the detailed per-agent HTML report."""
    banners = ""
    if result.status in (
        benchmark.ExecutionStatus.RUNNING,
        benchmark.ExecutionStatus.INITIALIZING,
        benchmark.ExecutionStatus.CONVERSING,
        benchmark.ExecutionStatus.GRADING,
    ):
        banners = (
            f'<div class="status-badge status-{result.status.value.lower()}"'
            f' style="margin-bottom: 1rem;">{result.status.value}</div>'
        )

    intro_html = ""
    if result.scenario_text:
        match = re.split(
            r"##\s*Steps|#\s*Reference|##\s*Rubric", result.scenario_text
        )
        if match:
            intro_content = markdown.markdown(
                match[0].strip(), extensions=["fenced_code", "tables", "nl2br"]
            )
            intro_html = (
                '<div class="scenario-intro"><h3>Goal &amp;'
                f" Context</h3>{intro_content}</div>"
            )

    links = []
    if getattr(result, "trajectory_id", None) and _TRAJECTORY_URL_TEMPLATE:
        url = _TRAJECTORY_URL_TEMPLATE.replace(
            "{id}", _escape_html(result.trajectory_id)
        )
        links.append(
            f'<li><strong>Trajectory Viewer:</strong> <a href="{url}" '
            f'target="_blank">{_escape_html(result.trajectory_id)}</a></li>'
        )

    if result.log_files:
        for f in result.log_files:
            abs_path = os.path.abspath(f)
            file_uri = pathlib.Path(abs_path).as_uri()
            href = file_uri
            display_name = abs_path

            if report_dir:
                try:
                    # Standardize relative path resolved from report directory
                    rel_path = os.path.relpath(
                        abs_path, start=os.path.abspath(report_dir)
                    )
                    # Web-standard URL-encode the path using forward slashes
                    href = urllib.parse.quote(rel_path.replace(os.sep, "/"))
                    display_name = rel_path
                except Exception:
                    pass

            links.append(
                f'<li><a href="{href}" target="_blank">{_escape_html(display_name)}</a> '
                f'(<a href="{file_uri}" target="_blank">absolute fallback</a>)</li>'
            )

    log_links_html = ""
    if links:
        log_links_html = (
            '<div class="scenario-intro" style="margin-top: 2rem;"><h3>Raw'
            f" Diagnostic Logs &amp; Links</h3><ul>{''.join(links)}</ul></div>"
        )

    rubric_html = ""
    if result.rubric_results:
        res = result.rubric_results
        earned, total = res.total_score, res.max_score
        pct = (earned / total) if total > 0 else 0
        if pct >= 1.0:
            s_cls, s_txt = "status-pass", "PASSED"
        elif pct >= 0.5:
            s_cls, s_txt = "status-warning", "WARNING"
        else:
            s_cls, s_txt = "status-fail", "FAILED"

        rows = ""
        for s in res.scores:
            score_style = (
                "color: #137333; font-weight: bold;"
                if s.score == 2
                else ("color: #b05e00;" if s.score == 1 else "color: #c5221f;")
            )
            rows += (
                '<tr><td style="border: 1px solid #e0e0e0; padding:'
                f' 12px;">{_escape_html(s.criteria)}</td>'
            )
            rows += (
                '<td style="border: 1px solid #e0e0e0; padding: 12px; text-align:'
                f' center; {score_style}">{s.score}/2</td>'
            )
            rows += (
                '<td style="border: 1px solid #e0e0e0; padding: 12px; color:'
                f' #5f6368; font-size: 0.9rem;">{_escape_html(s.reasoning)}</td></tr>'
            )

        rubric_html = f"""
    <div class="summary-card">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
        <h3 style="margin: 0; color: #1a73e8;">Rubric Assessment</h3>
        <div class="status-badge {s_cls}" style="font-size: 1rem; padding: 8px 24px;">{s_txt} ({earned}/{total})</div>
      </div>
      <p style="color: #5f6368; margin-bottom: 1.5rem;"><strong>Overall:</strong> {_escape_html(res.summary)}</p>
      <table style="width: 100%; border-collapse: collapse; border: 1px solid #e0e0e0;">
        <thead><tr style="background: #f1f3f4;"><th style="padding: 12px;">Criterion</th><th style="padding: 12px; text-align: center;">Score</th><th style="padding: 12px;">Reasoning</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>"""

    history_html = ""
    num_turns = (len(result.messages) + 1) // 2
    for i in range(num_turns):
        m = result.turn_metrics[i] if i < len(result.turn_metrics) else None

        timing_html = ""
        if m:
            timing_html = (
                '<div class="turn-meta"><span class="timing-pill">Sim:'
                f" {m.simulator_latency_sec:.2f}s</span><span"
                f' class="timing-pill">Agent: {m.latency_sec:.2f}s</span></div>'
            )

        history_html += (
            '<div class="turn-container"><div class="turn-header"><h5'
            f' class="turn-title">Turn {i + 1}</h5></div>'
        )

        if (2 * i) < len(result.messages):
            history_html += (
                '<div class="turn user"><strong>User:</strong>'
                f" {_escape_html(result.messages[2 * i]['content'])}</div>"
            )
            history_html += timing_html

        if m and (getattr(m, "events", None) or m.tool_interactions):
            history_html += (
                '<details class="tool-interactions"><summary><strong>Execution'
                " Events</strong></summary>"
            )

            if getattr(m, "events", None):
                for ev in m.events:
                    if isinstance(ev, benchmark.ToolCallEvent):
                        summary_text = f"Tool Call: {_escape_html(ev.name)}"
                        base_text = f"Tool Call: {_escape_html(ev.name)}"
                        summary_text = base_text
                        if ev.name == "run_command":
                            command_line = ev.args.get(
                                "command_line"
                            ) or ev.args.get("CommandLine")
                            if command_line:
                                summary_text = (
                                    base_text
                                    + f" - <code>{_escape_html(command_line)}</code>"
                                )

                        escaped_args = _escape_html(
                            json.dumps(ev.args, indent=2)
                        )
                        history_html += (
                            f"<details><summary>{summary_text}</summary><div"
                            ' class="tool-details"><p><strong>Source:</strong>'
                            f" {ev.source}</p><p><strong>Call ID:</strong>"
                            f" {ev.call_id}</p><p><strong>Args:</strong></p>"
                            f"<pre>{escaped_args}</pre></div></details>"
                        )
                    elif isinstance(ev, benchmark.ToolResultEvent):
                        history_html += (
                            "<details><summary><code>Tool Result for"
                            f" {ev.call_id}</code></summary><div"
                            ' class="tool-details"><p><strong>Source:</strong>'
                            f" {ev.source}</p><p><strong>Status:</strong>"
                            f" {'Error' if ev.is_error else 'Success'}</p><p><strong>Output:</strong></p><pre>{_escape_html(ev.output)}</pre></div></details>"
                        )
                    elif (
                        isinstance(ev, benchmark.AgentMessageEvent)
                        and ev.is_thought
                    ):
                        history_html += (
                            "<details><summary><code>Thought"
                            f" ({ev.source})</code></summary><div"
                            f' class="tool-details"><pre>{_escape_html(ev.text)}</pre></div></details>'
                        )
                    elif isinstance(ev, benchmark.SubagentEvent):
                        history_html += (
                            "<details><summary><code>Subagent"
                            f" {ev.action}</code></summary><div"
                            ' class="tool-details"><p><strong>Subagent ID:</strong>'
                            f" {ev.subagent_id}</p><p><strong>Prompt:</strong>"
                            f" {_escape_html(ev.prompt)}</p></div></details>"
                        )
            elif m.tool_interactions:
                for ti in m.tool_interactions:
                    thought_html = ""
                    if ti.thought:
                        thought_html = (
                            '<p style="color: #666; font-style:'
                            ' italic;"><strong>Thought:</strong>'
                            f" {_escape_html(ti.thought)}</p>"
                        )
                    history_html += (
                        f"<details><summary><code>{_escape_html(ti.name)}</code></summary><div"
                        f' class="tool-details">{thought_html}<p><strong>Args:</strong></p><pre>{_escape_html(json.dumps(ti.args, indent=2))}</pre>'
                    )
                    if ti.output:
                        history_html += f"<p><strong>Output:</strong></p><pre>{_escape_html(ti.output)}</pre>"
                    history_html += "</div></details>"

            history_html += "</details>"

        if (2 * i + 1) < len(result.messages):
            rendered = markdown.markdown(
                result.messages[2 * i + 1]["content"],
                extensions=["fenced_code", "tables", "nl2br"],
            )
            history_html += (
                '<div class="turn agent"><strong>Agent:</strong><div'
                f' class="markdown-body">{rendered}</div></div>'
            )
        elif (
            i < num_turns - 1
            or result.status == benchmark.ExecutionStatus.FAILED
        ):
            history_html += (
                '<div class="turn agent" style="color: red; border-left-color:'
                ' red;"><strong>Agent:</strong> [FAILED]</div>'
            )

        history_html += "</div>"

    final_failure = result.failure_reason
    if (
        not final_failure
        and result.messages
        and "[[FAIL" in result.messages[-1]["content"]
    ):
        final_failure = result.messages[-1]["content"]
    if final_failure:
        history_html += (
            '<div class="termination-signal">Termination Signal:'
            f" {_escape_html(final_failure)}</div>"
        )

    scoring_latency_html = ""
    if result.scoring_latency.total_seconds() > 0:
        scoring_latency_html = (
            "<span>Scoring Duration:"
            f" <strong>{result.scoring_latency.total_seconds():.1f}s</strong></span>"
        )

    # Latency Profile Table HTML
    latency_table_html = f"""
  <div class="summary-card">
    <h3 style="margin-top: 0; color: #1a73e8;">Orchestration Latency Profile</h3>
    <table style="width: auto; min-width: 450px; border: 1px solid #dadce0; border-radius: 6px; border-collapse: collapse;">
      <thead>
        <tr style="background: #f1f3f4;">
          <th style="padding: 8px 16px; text-align: left; border-bottom: 1px solid #dee2e6;">Phase</th>
          <th style="padding: 8px 16px; text-align: right; border-bottom: 1px solid #dee2e6;">Duration</th>
        </tr>
      </thead>
      <tbody>
        <tr style="border-bottom: 1px solid #eee;">
          <td style="padding: 8px 16px;"><strong>Initialization Queue</strong> (waiting for slot)</td>
          <td style="padding: 8px 16px; text-align: right;">{result.init_queued_latency.total_seconds():.2f}s</td>
        </tr>
        <tr style="border-bottom: 1px solid #eee;">
          <td style="padding: 8px 16px;"><strong>Active Initialization</strong> (asset copy, pip/uv setup, LS bootstrap)</td>
          <td style="padding: 8px 16px; text-align: right;">{result.init_active_latency.total_seconds():.2f}s</td>
        </tr>
        <tr style="border-bottom: 1px solid #eee;">
          <td style="padding: 8px 16px;"><strong>Agent Execution</strong> (turns and tools)</td>
          <td style="padding: 8px 16px; text-align: right;">{result.conversing_latency.total_seconds():.2f}s</td>
        </tr>
        <tr style="border-bottom: 1px solid #eee;">
          <td style="padding: 8px 16px;"><strong>Rubric Grading</strong> (Gemini evaluation)</td>
          <td style="padding: 8px 16px; text-align: right;">{result.scoring_latency.total_seconds():.2f}s</td>
        </tr>
        <tr style="border-bottom: 1px solid #eee;">
          <td style="padding: 8px 16px;"><strong>Teardown &amp; Cleanup</strong> (sandbox application deletion)</td>
          <td style="padding: 8px 16px; text-align: right;">{result.cleanup_latency.total_seconds():.2f}s</td>
        </tr>
        <tr style="font-weight: bold; background: #e8f0fe; border-top: 2px solid #1a73e8;">
          <td style="padding: 8px 16px;">Total Orchestrated Duration</td>
          <td style="padding: 8px 16px; text-align: right; color: #1a73e8;">{result.total_time_sec:.2f}s</td>
        </tr>
      </tbody>
    </table>
  </div>
  """

    return _DETAIL_HTML_TEMPLATE.format(
        latency_table=latency_table_html,
        scenario=_escape_html(result.scenario_name),
        head=_escape_html(result.head_name),
        banners=banners,
        intro=intro_html,
        rubric=rubric_html,
        history=history_html,
        log_links=log_links_html,
        start_time=_format_ts(result.start_time),
        end_time=_format_ts(result.end_time),
        total_duration=f"{result.total_time_sec:.1f}",
        scoring_latency_html=scoring_latency_html,
    )


def _group_by_scenario(
    results: Sequence[benchmark.ConversationResult],
) -> typing.Any:
    grouped = {}
    for r in results:
        grouped.setdefault(r.scenario_name, []).append(r)
    return grouped


def save_report(content: str, path: str) -> None:
    """Saves report content to disk."""
    dir_path = os.path.dirname(path)
    if dir_path:
        os.makedirs(dir_path, mode=0o755, exist_ok=True)
        os.chmod(dir_path, 0o755)
    with open(path, "w") as f:
        f.write(content)
    os.chmod(path, 0o644)


def _parse_timedelta(val: Any) -> datetime.timedelta:
    """Helper to parse serialized float seconds back to datetime.timedelta."""
    if val is None:
        return datetime.timedelta()
    try:
        return datetime.timedelta(seconds=float(val))
    except (ValueError, TypeError):
        return datetime.timedelta()


def load_json_report(file_path: str) -> benchmark.ConversationResult:
    """Loads a JSON file from disk and reconstructs the ConversationResult dataclass tree."""
    with open(file_path) as f:
        data = json.load(f)

    # 1. Parse TurnMetrics list
    turn_metrics = []
    for tm_dict in data.get("turn_metrics", []):
        # Parse ToolInteractions
        tool_interactions = []
        for ti_dict in tm_dict.get("tool_interactions", []):
            tool_interactions.append(
                benchmark.ToolInteraction(
                    name=ti_dict.get("name", ""),
                    args=ti_dict.get("args", {}),
                    output=ti_dict.get("output"),
                    thought=ti_dict.get("thought"),
                )
            )

        # Parse TrajectoryEvents
        events = []
        for ev_dict in tm_dict.get("events", []):
            if "subagent_id" in ev_dict:
                events.append(
                    benchmark.SubagentEvent(
                        subagent_id=ev_dict.get("subagent_id", ""),
                        action=ev_dict.get("action", ""),
                        prompt=ev_dict.get("prompt"),
                        source=ev_dict.get("source", "main"),
                    )
                )
            elif "text" in ev_dict:
                events.append(
                    benchmark.AgentMessageEvent(
                        text=ev_dict.get("text", ""),
                        is_thought=ev_dict.get("is_thought", False),
                        source=ev_dict.get("source", "main"),
                    )
                )
            elif "output" in ev_dict:
                events.append(
                    benchmark.ToolResultEvent(
                        call_id=ev_dict.get("call_id", ""),
                        output=ev_dict.get("output", ""),
                        is_error=ev_dict.get("is_error", False),
                        source=ev_dict.get("source", "main"),
                    )
                )
            elif "call_id" in ev_dict:
                events.append(
                    benchmark.ToolCallEvent(
                        call_id=ev_dict.get("call_id", ""),
                        name=ev_dict.get("name", ""),
                        args=ev_dict.get("args", {}),
                        source=ev_dict.get("source", "main"),
                    )
                )

        turn_metrics.append(
            benchmark.TurnMetrics(
                latency_sec=tm_dict.get("latency_sec", 0.0),
                simulator_latency_sec=tm_dict.get("simulator_latency_sec", 0.0),
                tool_calls=tm_dict.get("tool_calls", 0),
                tool_interactions=tool_interactions,
                events=events,
            )
        )

    # 2. Parse RubricResult
    rubric_results = None
    rr_dict = data.get("rubric_results")
    if rr_dict:
        scores = [
            benchmark.RubricCriterion(
                criteria=rc.get("criteria", ""),
                score=rc.get("score", 0),
                reasoning=rc.get("reasoning", ""),
            )
            for rc in rr_dict.get("scores", [])
        ]
        rubric_results = benchmark.RubricResult(
            scores=scores,
            summary=rr_dict.get("summary", ""),
            total_score=rr_dict.get("total_score", 0),
            max_score=rr_dict.get("max_score", 0),
        )

    # 3. Parse execution status enum
    status_val = data.get("status", "FINISHED")
    try:
        status = benchmark.ExecutionStatus(status_val)
    except ValueError:
        status = benchmark.ExecutionStatus.FINISHED

    return benchmark.ConversationResult(
        scenario_name=data.get("scenario_name", ""),
        scenario_text=data.get("scenario_text", ""),
        head_name=data.get("head_name", ""),
        turns=data.get("turns", 0),
        total_time_sec=data.get("total_time_sec", 0.0),
        messages=data.get("messages", []),
        turn_metrics=turn_metrics,
        status=status,
        success=data.get("success"),
        failure_reason=data.get("failure_reason"),
        rubric_results=rubric_results,
        scoring_latency_sec=data.get("scoring_latency_sec", 0.0),
        start_time=data.get("start_time", 0.0),
        end_time=data.get("end_time", 0.0),
        log_files=data.get("log_files", []),
        trajectory_id=data.get("trajectory_id"),
        init_queued_latency=_parse_timedelta(data.get("init_queued_latency")),
        init_active_latency=_parse_timedelta(data.get("init_active_latency")),
        conversing_latency=_parse_timedelta(data.get("conversing_latency")),
        scoring_latency=_parse_timedelta(
            data.get("scoring_latency") or data.get("scoring_latency_sec")
        ),
        cleanup_latency=_parse_timedelta(data.get("cleanup_latency")),
    )
