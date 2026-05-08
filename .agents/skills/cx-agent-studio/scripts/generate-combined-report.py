#!/usr/bin/env python3
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

"""Generate a combined HTML report for golden + simulation eval results.

Usage:
  python scripts/generate-combined-report.py --golden-run <RUN_ID> --sim-results <JSON_PATH>
  python scripts/generate-combined-report.py --golden-run <RUN_ID>
  python scripts/generate-combined-report.py --sim-results <JSON_PATH>
"""

import argparse
import json
import os
import sys
import yaml
import pandas as pd
from datetime import datetime
from typing import Any, Dict, List, Optional

from config import get_project_path

REPORTS_DIR = get_project_path("eval-reports")
SIM_EVALS_YAML = get_project_path("evals", "simulations", "simulations.yaml")


def _escape(text):
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _fmt_duration(seconds):
    """Format duration: seconds if < 60, minutes otherwise."""
    if seconds is None:
        return ""
    if seconds >= 60:
        mins = seconds / 60
        return f"{mins:.1f}m"
    return f"{seconds:.1f}s"


def _outcome_str(val):
    if isinstance(val, int):
        return {0: "UNSPECIFIED", 1: "PASS", 2: "FAIL"}.get(val, f"?{val}")
    return str(val) if val else "?"


def load_golden_results(run_id, app_name):
    """Fetch golden results from platform and parse into report-friendly format."""
    from cxas_scrapi.utils.eval_utils import EvalUtils

    utils = EvalUtils(app_name=app_name)
    full_run_id = run_id if run_id.startswith("projects/") else f"{app_name}/evaluationRuns/{run_id}"

    raw_results = utils.list_evaluation_results_by_run(full_run_id)

    # Get eval display names
    evals_map = utils.get_evaluations_map(app_name, reverse=False)
    name_lookup = {}
    for cat in ["goldens", "scenarios"]:
        for resource, display in evals_map.get(cat, {}).items():
            name_lookup[resource] = display

    # Get tools map for display name resolution
    tools_map = {}
    try:
        from cxas_scrapi.core.tools import Tools
        tools_map = Tools(app_name=app_name).get_tools_map()
    except Exception:
        pass

    def _resolve_tool(name):
        if name in tools_map:
            return tools_map[name]
        return name.split("/")[-1] if "/" in name else name

    results = []
    for r in raw_results:
        rd = type(r).to_dict(r)
        result_name = rd.get("name", "")
        eval_resource = "/".join(result_name.split("/")[:-2])
        display_name = name_lookup.get(eval_resource, eval_resource.split("/")[-1])

        status_raw = rd.get("evaluation_status", 0)
        passed = (status_raw == 1) if isinstance(status_raw, int) else str(status_raw).upper() == "PASS"

        golden = rd.get("golden_result", {})

        # Parse turns
        turns = []
        for i, turn in enumerate(golden.get("turn_replay_results", [])):
            sem = turn.get("semantic_similarity_result", {})
            turn_data = {
                "index": i + 1,
                "semantic_score": sem.get("score"),
                "comparisons": [],
            }
            for o in turn.get("expectation_outcome", []):
                exp = o.get("expectation", {})
                outcome = _outcome_str(o.get("outcome"))
                comp = {"outcome": outcome}

                if "agent_response" in exp:
                    chunks = exp["agent_response"].get("chunks", [])
                    comp["type"] = "text"
                    comp["expected"] = chunks[0].get("text", "") if chunks else ""
                    obs = o.get("observed_agent_response", {})
                    comp["actual"] = obs.get("chunks", [{}])[0].get("text", "") if obs else "(missed)"
                elif "tool_call" in exp:
                    tc = exp["tool_call"]
                    comp["type"] = "tool_call"
                    comp["expected"] = tc.get("display_name") or tc.get("tool", "").split("/")[-1]
                    comp["expected_args"] = tc.get("args", {})
                    obs = o.get("observed_tool_call", {})
                    comp["actual"] = (obs.get("display_name") or obs.get("tool", "").split("/")[-1]) if obs else "(missed)"
                    comp["actual_args"] = obs.get("args", {}) if obs else {}
                elif "tool_response" in exp:
                    continue  # skip tool responses
                elif "agent_transfer" in exp:
                    at = exp["agent_transfer"]
                    comp["type"] = "transfer"
                    comp["expected"] = at.get("display_name", at.get("target_agent", "").split("/")[-1])
                    obs = o.get("observed_agent_transfer", {})
                    comp["actual"] = obs.get("display_name", obs.get("target_agent", "").split("/")[-1]) if obs else "(missed)"
                else:
                    continue

                turn_data["comparisons"].append(comp)
            turns.append(turn_data)

        # Custom expectations
        expectations = []
        for ee in golden.get("evaluation_expectation_results", []):
            result_val = ee.get("result")
            exp_text = ee.get("prompt", ee.get("evaluation_expectation", ""))
            explanation = ee.get("explanation", "")
            met = result_val == 1 if isinstance(result_val, int) else str(result_val).upper() == "PASS"
            expectations.append({
                "expectation": exp_text,
                "status": "Met" if met else "Not Met",
                "justification": explanation,
            })

        # Extract session_id from conversation field
        session_id = ""
        if golden.get("turn_replay_results"):
            conv_path = golden["turn_replay_results"][0].get("conversation", "")
            if conv_path:
                # Extract the conversation ID (e.g. "evaluation-xxxx")
                session_id = conv_path.split("/")[-1]

        # Get session parameters and user inputs from eval definition
        session_params = {}
        turn_inputs = []  # One entry per golden turn: ("text", "...") or ("event", "...")
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

        # Attach user input to each turn for inline display
        for i, turn in enumerate(turns):
            if i < len(turn_inputs):
                kind, text = turn_inputs[i]
                turn["user_input"] = text if kind == "text" else None
            else:
                turn["user_input"] = None

        # Calculate total runtime from turn latencies
        total_latency_s = 0
        for turn_result in golden.get("turn_replay_results", []):
            lat = turn_result.get("turn_latency", "")
            if isinstance(lat, str) and lat.endswith("s"):
                try:
                    total_latency_s += float(lat.replace("s", ""))
                except ValueError:
                    pass
            elif isinstance(lat, dict):
                total_latency_s += lat.get("seconds", 0) + lat.get("nanos", 0) / 1e9

        results.append({
            "name": display_name,
            "passed": passed,
            "turns": turns,
            "expectations": expectations,
            "session_id": session_id,
            "session_parameters": session_params,
            "duration_s": round(total_latency_s, 1) if total_latency_s > 0 else None,
        })

    return results


def load_sim_results(json_path):
    """Load sim results from JSON file. Handles both old (list) and new (envelope) formats."""
    with open(json_path) as f:
        data = json.load(f)

    # New envelope format: {"wall_clock_s": N, "results": [...]}
    # Old format: [...]
    wall_clock_s = None
    if isinstance(data, dict):
        wall_clock_s = data.get("wall_clock_s")
        results = data.get("results", [])
    else:
        results = data

    # Backfill session_parameters if missing
    try:
        with open(SIM_EVALS_YAML) as f:
            templates = {e["name"]: e for e in yaml.safe_load(f).get("evals", [])}
        for r in results:
            if "session_parameters" not in r and r.get("name") in templates:
                r["session_parameters"] = templates[r["name"]].get("session_parameters", {})
    except Exception:
        pass

    return results, wall_clock_s


def build_html(golden_results=None, sim_results=None, app_name="", golden_modality="text", sim_modality="text", sim_wall_clock_s=None, tool_results=None, callback_results=None):
    """Build combined HTML report."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    g_total = len(golden_results) if golden_results else 0
    g_passed = sum(1 for r in golden_results if r.get("passed")) if golden_results else 0
    s_total = len(sim_results) if sim_results else 0
    s_passed = sum(1 for r in sim_results if r.get("passed")) if sim_results else 0
    t_total = len(tool_results) if tool_results else 0
    t_passed = sum(1 for r in tool_results if r.get("passed")) if tool_results else 0
    c_total = len(callback_results) if callback_results else 0
    c_passed = sum(1 for r in callback_results if r.get("passed")) if callback_results else 0
    total = g_total + s_total + t_total + c_total
    passed = g_passed + s_passed + t_passed + c_passed
    pct = 100 * passed / total if total else 0

    # Build unified eval list for top summary
    # Goldens: 1 result per eval
    # Sims: aggregate runs per eval
    unified = []
    if golden_results:
        for r in golden_results:
            scores = [t["semantic_score"] for t in r.get("turns", []) if t.get("semantic_score") is not None]
            avg_sem = sum(scores) / len(scores) if scores else None
            unified.append({
                "name": r["name"],
                "type": "golden",
                "passed": r.get("passed", False),
                "score": "PASS" if r.get("passed") else "FAIL",
                "detail": f"sem {avg_sem:.1f}/4" if avg_sem is not None else "",
                "runs": 1,
            })
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
            unified.append({
                "name": name,
                "type": "sim",
                "passed": s["pass"] == s["total"],
                "score": f"{s['pass']}/{s['total']}",
                "detail": "",
                "runs": s["total"],
                "run_results": s["runs"],
            })

    # Add tool tests to unified list
    if tool_results:
        for r in tool_results:
            unified.append({
                "name": r["name"],
                "type": "tool",
                "passed": r.get("passed", False),
                "score": r.get("status", "?"),
                "detail": f'{r.get("latency_ms", 0):.0f}ms' if r.get("latency_ms") else "",
                "runs": 1,
            })

    # Add callback tests to unified list
    if callback_results:
        for r in callback_results:
            unified.append({
                "name": r["name"],
                "type": "callback",
                "passed": r.get("passed", False),
                "score": r.get("status", "?"),
                "detail": r.get("callback_type", ""),
                "runs": 1,
            })

    # Sort: failures first, then by name
    unified.sort(key=lambda x: (x["passed"], x["name"]))

    # CES base URL
    parts = app_name.split("/") if app_name else []
    project_id = parts[1] if len(parts) > 1 else ""
    location = parts[3] if len(parts) > 3 else ""
    app_id = parts[5] if len(parts) > 5 else ""
    ces_base = f"https://ces.cloud.google.com/projects/{project_id}/locations/{location}/apps/{app_id}" if app_id else ""

    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Combined Eval Report - {ts}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 1100px; margin: 0 auto; padding: 20px; background: #f8f9fa; }}
  h1 {{ color: #1a1a2e; border-bottom: 3px solid #e94560; padding-bottom: 10px; }}
  h2 {{ color: #1a1a2e; margin-top: 30px; }}
  .summary {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 20px; }}
  .summary-top {{ display: flex; gap: 24px; align-items: center; margin-bottom: 12px; }}
  .summary .big {{ font-size: 2em; font-weight: bold; }}
  .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; }}
  .summary-card {{ padding: 8px 12px; border-radius: 6px; background: #f8f9fa; text-align: center; }}
  .summary-card .label {{ font-size: 0.7em; text-transform: uppercase; letter-spacing: 1px; color: #888; }}
  .summary-card .value {{ font-size: 1.3em; font-weight: bold; margin-top: 2px; }}
  .mixed {{ color: #f57c00; }}
  .failure-group {{ background: white; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin: 15px 0; padding: 16px; }}
  .failure-group h3 {{ margin: 0 0 8px 0; color: #c0392b; font-size: 0.95em; }}
  .failure-group .affected {{ font-size: 0.85em; color: #555; margin: 4px 0; }}
  .failure-group .affected-item {{ display: inline-block; padding: 2px 8px; margin: 2px; border-radius: 4px; background: #fdecea; font-size: 0.8em; }}
  .pass {{ color: #27ae60; }} .fail {{ color: #e74c3c; }}
  table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
  th, td {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid #ddd; }}
  th {{ background: #2c3e50; color: white; }}
  tr.clickable {{ cursor: pointer; }}
  tr.clickable:hover {{ background: #eef; }}
  .eval-card {{ background: white; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin: 15px 0; overflow: hidden; }}
  .eval-header {{ padding: 12px 16px; font-weight: bold; display: flex; justify-content: space-between; align-items: center; }}
  .eval-header.pass-bg {{ background: #d4edda; border-left: 4px solid #27ae60; }}
  .eval-header.fail-bg {{ background: #f8d7da; border-left: 4px solid #e74c3c; }}
  .eval-body {{ padding: 0 16px 16px; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.8em; font-weight: bold; }}
  .badge.pass, .badge.met {{ background: #d4edda; color: #155724; }}
  .badge.fail, .badge.not-met {{ background: #f8d7da; color: #721c24; }}
  .badge.golden {{ background: #fff3e0; color: #e65100; }}
  .badge.sim {{ background: #e8eaf6; color: #283593; }}
  .badge.tool {{ background: #e0f2f1; color: #004d40; }}
  .badge.callback {{ background: #fce4ec; color: #880e4f; }}
  .meta {{ color: #666; font-size: 0.85em; }}
  details {{ margin: 4px 0; }}
  summary {{ cursor: pointer; font-weight: bold; padding: 4px 0; }}
  .run-dot {{ display: inline-block; width: 12px; height: 12px; border-radius: 50%; margin-right: 3px; cursor: pointer; border: 2px solid transparent; transition: border-color 0.15s; }}
  .run-dot:hover {{ border-color: #333; }}
  .run-dot.p {{ background: #27ae60; }} .run-dot.f {{ background: #e74c3c; }}
  .session-link {{ font-size: 0.85em; color: #3498db; margin: 4px 0; }}
  .session-link a {{ color: #3498db; text-decoration: none; }}
  .session-link a:hover {{ text-decoration: underline; }}

  /* Golden turn comparison */
  .turn-row {{ margin: 6px 0; padding: 10px; background: #fafafa; border-radius: 6px; border-left: 3px solid #bbb; font-size: 0.85em; }}
  .turn-row.match {{ border-left-color: #27ae60; }}
  .turn-row.mismatch {{ border-left-color: #e74c3c; background: #fef5f5; }}
  .turn-header {{ font-weight: bold; margin-bottom: 4px; }}
  .comparison {{ margin: 4px 0; padding: 6px 8px; border-radius: 4px; }}
  .comparison.pass-bg {{ background: #edf7ed; }}
  .comparison.fail-bg {{ background: #fdecea; }}
  .comp-label {{ font-size: 0.75em; text-transform: uppercase; letter-spacing: 0.5px; color: #888; }}
  .comp-text {{ margin-top: 2px; }}
  .sem-score {{ display: inline-block; padding: 1px 6px; border-radius: 8px; font-size: 0.75em; font-weight: bold; }}
  .sem-4 {{ background: #d4edda; color: #155724; }}
  .sem-3 {{ background: #d4edda; color: #155724; }}
  .sem-2 {{ background: #fff3cd; color: #856404; }}
  .sem-1 {{ background: #f8d7da; color: #721c24; }}
  .sem-0 {{ background: #f8d7da; color: #721c24; }}

  /* Sim styles */
  .transcript {{ background: #f8f9fa; border-radius: 6px; padding: 12px; margin: 8px 0; font-size: 0.9em; }}
  .transcript .user {{ color: #2980b9; margin: 6px 0; }}
  .transcript .agent {{ color: #27ae60; margin: 6px 0; }}
  .transcript .system {{ color: #e67e22; margin: 4px 0; font-size: 0.85em; }}
  .expectation {{ margin: 6px 0; padding: 8px; background: #f0f0f0; border-radius: 4px; }}
  .step {{ margin: 6px 0; padding: 8px; border-left: 3px solid #3498db; background: #f0f8ff; }}
  .tool-details {{ margin: 4px 0; padding: 4px 8px; background: #f3e8ff; border-radius: 4px; border-left: 3px solid #8e44ad; }}
  .tool-summary {{ font-weight: normal; font-size: 0.9em; color: #6c3483; padding: 2px 0; }}
  .tool-data {{ margin: 4px 0; padding: 8px; background: #faf5ff; border-radius: 4px; font-size: 0.8em; white-space: pre-wrap; word-break: break-word; }}
  .tool-section {{ font-size: 0.85em; color: #555; margin-top: 6px; }}
  .controls {{ display: flex; gap: 8px; margin: 16px 0; flex-wrap: wrap; }}
  .controls button {{ padding: 6px 14px; border: 1px solid #ccc; border-radius: 6px; background: white; cursor: pointer; font-size: 0.85em; transition: all 0.15s; }}
  .controls button:hover {{ background: #eee; }}
  .controls button.active {{ background: #2c3e50; color: white; border-color: #2c3e50; }}
  tr.hidden-row {{ display: none; }}
  .eval-card.hidden-card {{ display: none; }}
</style>
<script>
var failuresOnly = false;

function jumpTo(evalName) {{
  var card = document.getElementById('eval-' + evalName);
  if (!card) return;
  var details = card.querySelectorAll('details.run-detail');
  var opened = false;
  details.forEach(function(d) {{
    if (!opened && d.dataset.failed === 'true') {{
      d.setAttribute('open', '');
      opened = true;
    }} else {{
      d.removeAttribute('open');
    }}
  }});
  if (!opened && details.length > 0) details[0].setAttribute('open', '');
  setTimeout(function() {{ card.scrollIntoView({{ behavior: 'smooth', block: 'start' }}); }}, 50);
}}

function jumpToRun(evalName, runIdx) {{
  var card = document.getElementById('eval-' + evalName);
  if (!card) return;
  var details = card.querySelectorAll('details.run-detail');
  details.forEach(function(d) {{ d.removeAttribute('open'); }});
  if (details[runIdx]) {{ details[runIdx].setAttribute('open', ''); }}
  setTimeout(function() {{ card.scrollIntoView({{ behavior: 'smooth', block: 'start' }}); }}, 50);
}}

function toggleFailures() {{
  failuresOnly = !failuresOnly;
  var btn = document.getElementById('btn-failures');
  btn.classList.toggle('active', failuresOnly);
  // Table rows
  document.querySelectorAll('tr[data-passed]').forEach(function(row) {{
    if (failuresOnly && row.dataset.passed === 'true') {{
      row.classList.add('hidden-row');
    }} else {{
      row.classList.remove('hidden-row');
    }}
  }});
  // Detail cards
  document.querySelectorAll('.eval-card[data-passed]').forEach(function(card) {{
    if (failuresOnly && card.dataset.passed === 'true') {{
      card.classList.add('hidden-card');
    }} else {{
      card.classList.remove('hidden-card');
    }}
  }});
}}

function expandAll() {{
  document.querySelectorAll('details').forEach(function(d) {{ d.setAttribute('open', ''); }});
}}

function collapseAll() {{
  document.querySelectorAll('details').forEach(function(d) {{ d.removeAttribute('open'); }});
}}
</script>
</head><body>
<h1>Combined Eval Report</h1>
<div class="summary">
  <div class="summary-top">
    <div class="big {('pass' if pct >= 90 else 'fail')}">{pct:.0f}%</div>
    <div><b>{passed}/{total}</b> total passed<br><span class="meta">Generated {ts}</span></div>
  </div>
  <div class="summary-grid">"""

    # Build summary cards for each eval type — clickable to jump to section
    cards = []
    if golden_results:
        g_cls = "pass" if g_passed == g_total else "fail"
        g_duration = sum(r.get("duration_s", 0) or 0 for r in golden_results)
        g_time = f"<br><span class='meta'>{_fmt_duration(g_duration)}</span>" if g_duration > 0 else ""
        cards.append(f'<div class="summary-card" onclick="document.getElementById(\'section-goldens\').scrollIntoView({{behavior:\'smooth\'}})" style="cursor:pointer"><div class="label">Goldens ({golden_modality})</div><div class="value {g_cls}">{g_passed}/{g_total}</div>{g_time}</div>')
    if sim_results:
        s_pct = 100 * s_passed / s_total if s_total else 0
        s_cls = "pass" if s_passed == s_total else "fail"
        s_time = ""
        if sim_wall_clock_s:
            s_time = f"<br><span class='meta'>{_fmt_duration(sim_wall_clock_s)}</span>"
        elif sim_results:
            s_dur = sum(r.get("duration_s", 0) or 0 for r in sim_results)
            if s_dur > 0:
                s_time = f"<br><span class='meta'>~{_fmt_duration(s_dur)}</span>"
        s_cls = "pass" if s_passed == s_total else "fail"
        cards.append(f'<div class="summary-card" onclick="document.getElementById(\'section-sims\').scrollIntoView({{behavior:\'smooth\'}})" style="cursor:pointer"><div class="label">Sims ({sim_modality})</div><div class="value {s_cls}">{s_passed}/{s_total}</div>{s_time}</div>')
    if tool_results:
        t_cls = "pass" if t_passed == t_total else "fail"
        cards.append(f'<div class="summary-card" onclick="document.getElementById(\'section-tools\').scrollIntoView({{behavior:\'smooth\'}})" style="cursor:pointer"><div class="label">Tool Tests</div><div class="value {t_cls}">{t_passed}/{t_total}</div></div>')
    if callback_results:
        c_cls = "pass" if c_passed == c_total else "fail"
        cards.append(f'<div class="summary-card" onclick="document.getElementById(\'section-callbacks\').scrollIntoView({{behavior:\'smooth\'}})" style="cursor:pointer"><div class="label">Callback Tests</div><div class="value {c_cls}">{c_passed}/{c_total}</div></div>')

    html += "\n".join(cards)

    html += f"""
  </div>
</div>

<div class="controls">
  <button id="btn-failures" onclick="toggleFailures()">Show Failures Only</button>
  <button onclick="expandAll()">Expand All</button>
  <button onclick="collapseAll()">Collapse All</button>
</div>

<h2>All Evals</h2>
<table>
  <tr><th>Result</th><th>Type</th><th>Eval</th><th>Detail</th></tr>
"""

    # Unified top table — clickable rows
    for u in unified:
        type_cls = u["type"]
        safe = u["name"].replace("'", "\\'")

        # Determine status label
        score = u.get("score", "")
        if u["type"] == "sim" and "run_results" in u:
            p = sum(1 for r in u["run_results"] if r.get("passed"))
            t = len(u["run_results"])
            if p == t:
                status_html = '<span class="pass"><b>PASSED</b></span>'
                cls = "pass"
            elif p == 0:
                status_html = '<span class="fail"><b>FAILED</b></span>'
                cls = "fail"
            else:
                status_html = f'<span class="mixed"><b>MIXED</b></span> <span class="meta">{p}/{t}</span>'
                cls = "fail"
        elif u["passed"]:
            status_html = '<span class="pass"><b>PASSED</b></span>'
            cls = "pass"
        else:
            status_html = '<span class="fail"><b>FAILED</b></span>'
            cls = "fail"

        # Detail column — run dots for sims, latency for tools, etc.
        detail = u.get("detail", "")
        if u["type"] == "sim" and "run_results" in u:
            dots = ""
            for i, r in enumerate(u["run_results"]):
                dot_cls = "p" if r.get("passed") else "f"
                dots += f'<span class="run-dot {dot_cls}" title="Run {r.get("run","?")}" onclick="event.stopPropagation(); jumpToRun(\'{safe}\', {i})"></span>'
            detail = dots

        passed_str = "true" if u["passed"] else "false"
        html += f'  <tr class="clickable" data-passed="{passed_str}" onclick="jumpTo(\'{safe}\')">'
        html += f'<td>{status_html}</td>'
        html += f'<td><span class="badge {type_cls}">{u["type"]}</span></td>'
        html += f'<td>{_escape(u["name"])}</td>'
        html += f'<td>{detail}</td></tr>\n'

    html += "</table>\n"

    # --- FAILURE GROUPING ---
    failure_groups = {}  # reason → list of eval names

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
                                reason = f"Routing missed: expected transfer to {expected}"
                            else:
                                reason = f"Wrong routing: expected {expected}, got {actual}"
                        elif ctype == "tool_call" and actual == "(missed)":
                            if expected:
                                reason = f"Tool not called: {expected}"
                            else:
                                continue  # Empty tool expectation, skip
                        elif ctype == "tool_call" and expected != actual:
                            reason = f"Wrong tool: expected {expected}, got {actual}"
                        elif ctype == "text":
                            reason = "Semantic similarity too low"
                        else:
                            continue  # Skip non-failures
                        failure_groups.setdefault(reason, set()).add(r["name"])
            for exp in r.get("expectations", []):
                if exp.get("status") == "Not Met":
                    reason = str(exp.get("expectation", ""))[:80]
                    failure_groups.setdefault(f"Expectation not met: {reason}", set()).add(r["name"])

    # Collect sim failures
    if sim_results:
        for r in sim_results:
            if r.get("passed"):
                continue
            for step in r.get("step_details", []):
                if step.get("status") != "Completed":
                    reason = f"Goal not completed: {step.get('goal', '')[:60]}"
                    failure_groups.setdefault(reason, set()).add(r["name"])
            for exp in r.get("expectation_details", []):
                if exp.get("status") == "Not Met":
                    reason = f"Expectation not met: {exp.get('expectation', '')[:60]}"
                    failure_groups.setdefault(reason, set()).add(r["name"])

    # Collect tool test failures
    if tool_results:
        for r in tool_results:
            if r.get("passed"):
                continue
            errors = str(r.get("errors", ""))
            # Normalize common tool test errors
            if "operator='Operator.CONTAINS'" in errors and "expected='PASSED'" in errors:
                reason = "Default expectation: $.result contains PASSED (needs customization)"
            elif "operator='Operator" in errors:
                reason = errors.split(",")[0][:80] if "," in errors else errors[:80]
            else:
                reason = errors[:80]
            failure_groups.setdefault(reason, set()).add(r["name"])

    # Collect callback failures
    if callback_results:
        for r in callback_results:
            if r.get("passed"):
                continue
            reason = str(r.get("error", "Unknown error"))[:80]
            failure_groups.setdefault(f"Callback: {reason}", set()).add(r["name"])

    if failure_groups:
        html += '\n<h2>Failure Patterns</h2>\n'
        # Sort by number of affected evals (most common first)
        for reason, evals in sorted(failure_groups.items(), key=lambda x: -len(x[1])):
            items = " ".join(
                f'<span class="affected-item" style="cursor:pointer" onclick="jumpTo(\'{e.replace(chr(39), chr(92)+chr(39))}\')">{_escape(e)}</span>'
                for e in sorted(evals)
            )
            html += f'<div class="failure-group"><h3>{_escape(reason)}</h3><div class="affected">{len(evals)} eval(s): {items}</div></div>\n'

    # --- GOLDEN DETAIL CARDS ---
    if golden_results:
        g_pct_str = f"{100 * g_passed / g_total:.0f}%" if g_total else "0%"
        html += f'\n<h2 id="section-goldens">Goldens <span class="meta">({g_passed}/{g_total} — {g_pct_str})</span></h2>\n'
        for r in sorted(golden_results, key=lambda x: (x.get("passed", False), x.get("name", ""))):
            cls = "pass-bg" if r["passed"] else "fail-bg"
            status = "PASS" if r["passed"] else "FAIL"
            failed_str = "false" if r["passed"] else "true"
            passed_str = "true" if r["passed"] else "false"
            html += f'<div class="eval-card" id="eval-{r["name"]}" data-passed="{passed_str}"><div class="eval-header {cls}"><span>{_escape(r["name"])} <span class="badge golden">golden</span></span><span class="badge {"pass" if r["passed"] else "fail"}">{status}</span></div><div class="eval-body">\n'

            html += f'<details class="run-detail" data-failed="{failed_str}">\n'
            g_dur = r.get("duration_s")
            g_dur_str = f" | {_fmt_duration(g_dur)}" if g_dur else ""
            html += f'<summary>Golden Evaluation — <span class="{"pass" if r["passed"] else "fail"}">{"PASS" if r["passed"] else "FAIL"}</span> | {len(r.get("turns",[]))} turns{g_dur_str}</summary>\n'

            # Session ID
            session_id = r.get("session_id", "")
            if session_id and ces_base:
                url = f"{ces_base}?panel=conversation_list&id={session_id}&source=EVAL"
                html += f'<div class="session-link">Session: <a href="{url}" target="_blank"><code>{session_id}</code></a></div>\n'
            elif session_id:
                html += f'<div class="session-link">Session: <code>{session_id}</code></div>\n'

            # Session parameters
            sparams = r.get("session_parameters", {})
            if sparams:
                html += f'<details class="tool-details"><summary class="tool-summary">&#9881; <b>Session Parameters</b></summary>'
                html += f'<pre class="tool-data">{_escape(json.dumps(sparams, indent=2))}</pre></details>\n'

            # Turn-by-turn comparison with user messages inline
            html += '<details open><summary>Conversation &amp; Comparison</summary>\n'
            for turn in r.get("turns", []):
                sem = turn.get("semantic_score")
                sem_cls = f"sem-{sem}" if sem is not None else ""
                any_fail = any(c["outcome"] == "FAIL" for c in turn.get("comparisons", []))
                row_cls = "mismatch" if any_fail else "match"

                html += f'<div class="turn-row {row_cls}">'
                html += f'<div class="turn-header">Turn {turn["index"]}'
                if sem is not None:
                    html += f' <span class="sem-score {sem_cls}">{sem}/4</span>'
                html += '</div>\n'

                # Show user input at the top of the turn
                user_input = turn.get("user_input")
                if user_input:
                    html += f'<div style="color:#2980b9;margin:4px 0;padding:4px 8px;background:#eef5ff;border-radius:4px;"><b>User:</b> {_escape(user_input)}</div>\n'

                for comp in turn.get("comparisons", []):
                    c_cls = "pass-bg" if comp["outcome"] != "FAIL" else "fail-bg"
                    badge_cls = "pass" if comp["outcome"] != "FAIL" else "fail"
                    ctype = comp.get("type", "?")
                    icon = {"text": "&#128172;", "tool_call": "&#128295;", "transfer": "&#128256;"}.get(ctype, "")

                    html += f'<div class="comparison {c_cls}">'
                    html += f'<span class="badge {badge_cls}">{comp["outcome"]}</span> {icon} <b>{ctype}</b><br>'
                    html += f'<span class="comp-label">Expected:</span> <span class="comp-text">{_escape(str(comp.get("expected", ""))[:150])}</span><br>'
                    html += f'<span class="comp-label">Actual:</span> <span class="comp-text">{_escape(str(comp.get("actual", ""))[:150])}</span>'

                    # Show tool call args for tool_call comparisons
                    if ctype == "tool_call" and (comp.get("expected_args") or comp.get("actual_args")):
                        html += '<details style="margin-top:4px;"><summary style="cursor:pointer;font-size:0.85em;color:#666;">Tool call args</summary>'
                        html += '<div style="font-size:0.82em;background:#f8f9fa;padding:6px 10px;border-radius:4px;margin-top:4px;">'
                        if comp.get("expected_args"):
                            html += f'<div><span class="comp-label">Expected args:</span><pre style="margin:2px 0;white-space:pre-wrap;">{_escape(json.dumps(comp["expected_args"], indent=2, default=str))}</pre></div>'
                        if comp.get("actual_args"):
                            html += f'<div><span class="comp-label">Actual args:</span><pre style="margin:2px 0;white-space:pre-wrap;">{_escape(json.dumps(comp["actual_args"], indent=2, default=str))}</pre></div>'
                        html += '</div></details>'

                    html += '</div>\n'

                html += '</div>\n'
            html += '</details>\n'

            # Custom expectations
            for exp in r.get("expectations", []):
                e_cls = "met" if exp["status"] == "Met" else "not-met"
                html += f'<div class="expectation"><span class="badge {e_cls}">{_escape(exp["status"])}</span> {_escape(str(exp["expectation"])[:150])}'
                if exp.get("justification"):
                    html += f'<br><span class="meta">{_escape(str(exp["justification"])[:250])}</span>'
                html += '</div>\n'

            html += '</details>\n'
            html += '</div></div>\n'

    # --- SIMS SECTION ---
    if sim_results:
        s_pct_str = f"{100 * s_passed / s_total:.0f}%" if s_total else "0%"
        html += f'\n<h2 id="section-sims">Simulations <span class="meta">({s_passed}/{s_total} — {s_pct_str})</span></h2>\n'
        eval_stats = {}
        for r in sim_results:
            n = r["name"]
            if n not in eval_stats:
                eval_stats[n] = {"pass": 0, "total": 0, "runs": []}
            eval_stats[n]["total"] += 1
            if r.get("passed"):
                eval_stats[n]["pass"] += 1
            eval_stats[n]["runs"].append(r)

        # Load tools map for display name resolution
        tools_map = {}
        if app_name:
            try:
                from cxas_scrapi.core.tools import Tools
                tools_map = Tools(app_name=app_name).get_tools_map()
            except Exception:
                pass

        for name, s in sorted(eval_stats.items(), key=lambda x: (x[1]["pass"] / max(x[1]["total"], 1), x[0])):
            score = f"{s['pass']}/{s['total']}"
            cls = "pass-bg" if s["pass"] == s["total"] else "fail-bg"
            passed_str = "true" if s["pass"] == s["total"] else "false"
            html += f'<div class="eval-card" id="eval-{name}" data-passed="{passed_str}"><div class="eval-header {cls}"><span>{_escape(name)} <span class="badge sim">sim</span></span><span>{score}</span></div><div class="eval-body">\n'

            for r in s["runs"]:
                run_cls = "pass" if r.get("passed") else "fail"
                failed_str = "false" if r.get("passed") else "true"
                session_id = r.get("session_id", "")
                html += f'<details class="run-detail" data-failed="{failed_str}">\n'
                html += f'<summary>Run {r.get("run","?")} — <span class="{run_cls}">{"PASS" if r.get("passed") else "FAIL"}</span>'
                dur = r.get("duration_s")
                dur_str = f" | {_fmt_duration(dur)}" if dur else ""
                html += f' | goals: {r.get("goals","?")} | expectations: {r.get("expectations","?")} | turns: {r.get("turns","?")}{dur_str}</summary>\n'

                if session_id and ces_base:
                    url = f"{ces_base}?panel=conversation_list&id={session_id}&source=EVAL"
                    html += f'<div class="session-link">Session: <a href="{url}" target="_blank"><code>{session_id}</code></a></div>\n'

                sparams = r.get("session_parameters", {})
                if sparams:
                    html += f'<details class="tool-details"><summary class="tool-summary">&#9881; <b>Session Parameters</b></summary>'
                    html += f'<pre class="tool-data">{_escape(json.dumps(sparams, indent=2))}</pre></details>\n'

                if "error" in r:
                    html += f'<div class="expectation"><b>Error:</b> {_escape(r["error"])}</div>\n'
                else:
                    for step in r.get("step_details", []):
                        s_cls = "pass" if step["status"] == "Completed" else "fail"
                        html += f'<div class="step"><b>Goal:</b> {_escape(step["goal"])}<br><b>Criteria:</b> {_escape(step["success_criteria"])}<br>'
                        html += f'<b>Status:</b> <span class="badge {s_cls.replace("pass","met").replace("fail","not-met")}">{_escape(step["status"])}</span>'
                        if step.get("justification"):
                            html += f'<br><b>Justification:</b> {_escape(step["justification"])}'
                        html += '</div>\n'

                    for exp in r.get("expectation_details", []):
                        e_cls = "met" if exp["status"] == "Met" else "not-met"
                        html += f'<div class="expectation"><span class="badge {e_cls}">{_escape(exp["status"])}</span> {_escape(exp["expectation"])}'
                        if exp.get("justification"):
                            html += f'<br><span class="meta">{_escape(exp["justification"])}</span>'
                        html += '</div>\n'

                    trace = r.get("detailed_trace", [])
                    if trace:
                        html += f'<details open><summary>Conversation Trace ({r.get("turns","?")} turns)</summary>\n<div class="transcript">\n'
                        parsed = []
                        for entry in trace:
                            for line in entry.split("\n"):
                                line = line.strip()
                                if not line or line.startswith("Agent Text (Diag):"):
                                    continue
                                for path, dname in tools_map.items():
                                    line = line.replace(path, dname)
                                if line.startswith("Agent Text:"):
                                    parsed.append(("agent", line[len("Agent Text:"):].strip()))
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
                            elif kind == "tool_resp" and merged and merged[-1][0] == "tool_call":
                                merged[-1] = ("tool_pair", merged[-1][1], text)
                            else:
                                merged.append((kind, text))

                        for item in merged:
                            kind = item[0]
                            if kind == "user":
                                html += f'<div class="user"><b>User:</b> {_escape(item[1])}</div>\n'
                            elif kind == "agent":
                                html += f'<div class="agent"><b>Agent:</b> {_escape(item[1])}</div>\n'
                            elif kind in ("tool_call", "tool_pair"):
                                lbl, _, args = item[1].partition(" with args ")
                                lbl = lbl.replace("Tool Call: ", "").replace("Tool Call (Output): ", "").split("/")[-1]
                                html += f'<details class="tool-details"><summary class="tool-summary">&#128295; <b>{_escape(lbl)}</b></summary>'
                                if args:
                                    html += f'<div class="tool-section"><b>Input:</b></div><pre class="tool-data">{_escape(args)}</pre>'
                                if kind == "tool_pair":
                                    _, _, result = item[2].partition(" with result ")
                                    if result:
                                        html += f'<div class="tool-section"><b>Output:</b></div><pre class="tool-data">{_escape(result)}</pre>'
                                html += '</details>\n'
                            elif kind == "tool_resp":
                                lbl, _, result = item[1].partition(" with result ")
                                lbl = lbl.replace("Tool Response: ", "").split("/")[-1]
                                html += f'<details class="tool-details"><summary class="tool-summary">&#128228; <b>{_escape(lbl)}</b></summary>'
                                if result:
                                    html += f'<pre class="tool-data">{_escape(result)}</pre>'
                                html += '</details>\n'
                            else:
                                html += f'<div class="system">{_escape(item[1])}</div>\n'
                        html += '</div>\n</details>\n'

                html += '</details>\n'
            html += '</div></div>\n'

    # Component test sections (tool + callback)
    html = render_component_tests(html, tool_results, callback_results)

    html += "</body></html>"
    return html


def load_tool_test_results(csv_or_json_path):
    """Load tool test results from a CSV or JSON file."""
    if csv_or_json_path.endswith(".csv"):
        df = pd.read_csv(csv_or_json_path)
    else:
        df = pd.read_json(csv_or_json_path)
    results = []
    for _, row in df.iterrows():
        results.append({
            "name": row.get("test_name", row.get("test", "?")),
            "tool": row.get("tool", "?"),
            "passed": row.get("status", "").upper() == "PASSED",
            "status": row.get("status", "?"),
            "latency_ms": row.get("latency (ms)", 0),
            "errors": row.get("errors", ""),
        })
    return results


def load_callback_test_results(csv_or_json_path):
    """Load callback test results from a CSV or JSON file."""
    if csv_or_json_path.endswith(".csv"):
        df = pd.read_csv(csv_or_json_path)
    else:
        df = pd.read_json(csv_or_json_path)
    results = []
    for _, row in df.iterrows():
        results.append({
            "name": row.get("test_name", "?"),
            "agent": row.get("agent_name", "?"),
            "callback_type": row.get("callback_type", "?"),
            "passed": row.get("status", "").upper() == "PASSED",
            "status": row.get("status", "?"),
            "error": row.get("error_message", ""),
        })
    return results


def render_component_tests(html, tool_results=None, callback_results=None):
    """Render tool and callback test sections into the HTML."""
    if not tool_results and not callback_results:
        return html

    section = ""

    if tool_results:
        t_total = len(tool_results)
        t_passed = sum(1 for r in tool_results if r["passed"])
        t_pct = 100 * t_passed / t_total if t_total else 0

        section += f'<h2 id="section-tools">Tool Tests <span class="meta">({t_passed}/{t_total} — {t_pct:.0f}%)</span></h2>\n'
        section += '<table><tr><th>Result</th><th>Tool</th><th>Test</th><th>Latency</th><th>Errors</th></tr>\n'
        for r in sorted(tool_results, key=lambda x: x["passed"]):
            cls = "pass" if r["passed"] else "fail"
            passed_str = "true" if r["passed"] else "false"
            lat = f'{r["latency_ms"]:.0f}ms' if r.get("latency_ms") else "-"
            errors = _escape(str(r.get("errors", ""))[:100])
            section += f'<tr data-passed="{passed_str}"><td class="{cls}"><b>{r["status"]}</b></td><td>{_escape(r["tool"])}</td><td>{_escape(r["name"])}</td><td>{lat}</td><td>{errors}</td></tr>\n'
        section += '</table>\n'

    if callback_results:
        c_total = len(callback_results)
        c_passed = sum(1 for r in callback_results if r["passed"])
        c_pct = 100 * c_passed / c_total if c_total else 0

        section += f'<h2 id="section-callbacks">Callback Tests <span class="meta">({c_passed}/{c_total} — {c_pct:.0f}%)</span></h2>\n'
        section += '<table><tr><th>Result</th><th>Agent</th><th>Callback</th><th>Test</th><th>Error</th></tr>\n'
        for r in sorted(callback_results, key=lambda x: x["passed"]):
            cls = "pass" if r["passed"] else "fail"
            passed_str = "true" if r["passed"] else "false"
            error = _escape(str(r.get("error", ""))[:100])
            section += f'<tr data-passed="{passed_str}"><td class="{cls}"><b>{r["status"]}</b></td><td>{_escape(r["agent"])}</td><td>{_escape(r["callback_type"])}</td><td>{_escape(r["name"])}</td><td>{error}</td></tr>\n'
        section += '</table>\n'

    return html + section


def main():
    try:
        import cxas_scrapi  # noqa: F401
    except ImportError:
        print("Error: cxas-scrapi not installed. Activate venv (source .venv/bin/activate) and install cxas-scrapi first.")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Generate combined eval report")
    parser.add_argument("--golden-run", help="Golden eval run ID")
    parser.add_argument("--sim-results", help="Path to sim results JSON")
    parser.add_argument("--tool-results", help="Path to tool test results CSV/JSON")
    parser.add_argument("--callback-results", help="Path to callback test results CSV/JSON")
    parser.add_argument("--app-name", default=None, help="App resource name. If not provided, reads from gecx-config.json via config.py.")
    parser.add_argument("--golden-modality", default="text", help="Modality for golden run (text/audio)")
    parser.add_argument("--sim-modality", default="text", help="Modality for sim run (text/audio)")
    parser.add_argument("--output", help="Output HTML path")
    args = parser.parse_args()

    if not any([args.golden_run, args.sim_results, args.tool_results, args.callback_results]):
        parser.print_help()
        return

    # Resolve app_name from gecx-config.json if not provided
    if not args.app_name and args.golden_run:
        try:
            from config import load_app_name
            args.app_name = load_app_name()
        except Exception:
            print("Error: --app-name required when gecx-config.json not found")
            return

    golden_results = None
    sim_results = None
    tool_results = None
    callback_results = None

    if args.golden_run:
        print(f"Loading golden results for run {args.golden_run}...")
        golden_results = load_golden_results(args.golden_run, args.app_name)
        print(f"  {len(golden_results)} golden results")

    sim_wall_clock_s = None
    if args.sim_results:
        print(f"Loading sim results from {args.sim_results}...")
        sim_results, sim_wall_clock_s = load_sim_results(args.sim_results)
        wc_str = f" (wall clock: {sim_wall_clock_s:.0f}s)" if sim_wall_clock_s else ""
        print(f"  {len(sim_results)} sim results{wc_str}")

    if args.tool_results:
        print(f"Loading tool test results from {args.tool_results}...")
        tool_results = load_tool_test_results(args.tool_results)
        print(f"  {len(tool_results)} tool test results")

    if args.callback_results:
        print(f"Loading callback test results from {args.callback_results}...")
        callback_results = load_callback_test_results(args.callback_results)
        print(f"  {len(callback_results)} callback test results")

    html = build_html(golden_results, sim_results, args.app_name,
                       args.golden_modality, args.sim_modality, sim_wall_clock_s,
                       tool_results, callback_results)

    os.makedirs(REPORTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    output_path = args.output or os.path.join(REPORTS_DIR, f"combined_report_{ts}.html")

    with open(output_path, "w") as f:
        f.write(html)
    print(f"\nReport: {output_path}")


if __name__ == "__main__":
    main()
