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

"""LLM-User Simulation eval runner using cxas-scrapi.

Extends SimulationEvals to support session variables, multi-step goals,
and proper handling of agent-terminated sessions.

Usage:
  python scripts/scrapi-sim-runner.py run [--priority P0] [--runs 3]
  python scripts/scrapi-sim-runner.py run --eval outage_voice_current --verbose
  python scripts/scrapi-sim-runner.py convert [--priority P0]
  python scripts/scrapi-sim-runner.py list
"""

import argparse
import json
import os
import sys
import time
import uuid
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
from google import genai

from cxas_scrapi.core.sessions import Sessions
from cxas_scrapi.core.apps import Apps
from cxas_scrapi.evals.simulation_evals import (
    LLMUserConversation,
    SimulationEvals,
    StepStatus,
)
from cxas_scrapi.prompts import llm_user_prompts


from config import load_app_name, get_project_path


EVALS_YAML = get_project_path("evals", "scenarios", "scenarios.yaml")
SIM_EVALS_YAML = get_project_path("evals", "simulations", "simulations.yaml")
REPORTS_DIR = get_project_path("eval-reports")

_DEFAULT_MODEL = "gemini-3.1-flash-lite-preview"


def load_yaml():
    if not os.path.exists(EVALS_YAML):
        return {"meta": {}, "evals": []}
    with open(EVALS_YAML, "r") as f:
        return yaml.safe_load(f) or {"meta": {}, "evals": []}


def load_sim_templates():
    """Load sim eval templates from simulations.yaml."""
    if not os.path.exists(SIM_EVALS_YAML):
        return {}
    with open(SIM_EVALS_YAML, "r") as f:
        data = yaml.safe_load(f)
    if isinstance(data, list):
        return {ev["name"]: ev for ev in data}
    return {ev["name"]: ev for ev in (data or {}).get("evals", [])}


def get_app_name():
    return load_app_name()


def build_test_case(ev: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build a sim test case from sim template. Variables come from simulations.yaml."""
    name = ev["name"]
    templates = load_sim_templates()

    if name not in templates:
        return None

    template = templates[name]

    return {
        "name": name,
        "steps": template["steps"],
        "expectations": template.get("expectations", []),
        "session_parameters": template.get("session_parameters", {}),
        "metadata": {
            "prd_id": ev.get("prd_id", ""),
            "priority": ev.get("priority", ""),
            "severity": ev.get("severity", ""),
        },
    }


class EnhancedSimRunner(SimulationEvals):
    """Extended SimulationEvals that injects session variables."""

    def simulate_conversation(
        self,
        test_case: Dict[str, Any],
        initial_utterance: str = "Hi",
        model: str = _DEFAULT_MODEL,
        session_id: Optional[str] = None,
        console_logging: bool = True,
        modality: str = "text",
    ) -> LLMUserConversation:
        """Run a simulated conversation with variable injection."""
        if session_id is None:
            session_id = str(uuid.uuid4())

        eval_conv = LLMUserConversation(
            genai_client=self.genai_client,
            genai_model=model,
            test_case=test_case,
        )

        session_params = test_case.get("session_parameters", {})

        if console_logging:
            print("Starting simulated conversation...")
            if session_params:
                print(f"  Variables: {list(session_params.keys())}")

        # First turn: inject variables alongside the initial utterance
        user_utterance = initial_utterance
        eval_conv._add_user_utterance(user_utterance)
        eval_conv.current_turn += 1

        detailed_trace = [f"User: {user_utterance}"]

        first_turn = True
        while user_utterance:
            for attempt in range(self.max_retries):
                try:
                    kwargs = {
                        "session_id": session_id,
                        "text": user_utterance,
                        "modality": modality,
                    }
                    # Inject variables on first turn only
                    if first_turn and session_params:
                        kwargs["variables"] = session_params
                        first_turn = False
                    else:
                        first_turn = False

                    response = self.sessions_client.run(**kwargs)
                    break
                except Exception as e:
                    if attempt == self.max_retries - 1:
                        raise e
                    if console_logging:
                        print(f"  Retry {attempt+1}: {e}")
                    time.sleep(self.retry_delay_base ** attempt)

            if not response:
                break

            if console_logging:
                self.sessions_client.parse_result(response)

            agent_text, trace_chunks, session_ended = self._parse_agent_response(response)
            detailed_trace.append("\n".join(trace_chunks))

            if session_ended:
                if console_logging:
                    print("\nSession ended by agent (end_session).")
                # Mark current step as completed if the session ending
                # is a valid success (escalation evals)
                for prog in eval_conv.steps_progress:
                    criteria = prog.step.success_criteria.lower()
                    if prog.status != StepStatus.COMPLETED and (
                        "escalat" in criteria
                        or "transfer" in criteria
                        or "being transferred" in criteria
                    ):
                        prog.status = StepStatus.COMPLETED
                        prog.justification = "Agent ended session via escalation/transfer — matches success criteria."
                break

            result = eval_conv.next_user_utterance(agent_text)
            if isinstance(result, tuple):
                user_utterance, _ = result
            else:
                user_utterance = result
            if user_utterance:
                detailed_trace.append(f"User: {user_utterance}")

        if console_logging:
            print("\n--- Conversation Complete ---")
            for step_prog in eval_conv.steps_progress:
                status_icon = "✓" if step_prog.status == StepStatus.COMPLETED else "✗"
                print(f"  {status_icon} {step_prog.step.goal[:80]} → {step_prog.status.value}")

        # Evaluate expectations
        self._evaluate_expectations(eval_conv, detailed_trace, model, console_logging)

        # Attach extra data for reporting
        eval_conv._session_id = session_id
        eval_conv._detailed_trace = detailed_trace

        return eval_conv


def filter_evals(evals, priority=None, tag=None):
    if priority:
        filtered = []
        for e in evals:
            # Check both 'priority' field and 'tags' list for priority matching
            tags = e.get("tags", [])
            prio_field = e.get("priority", "")
            if prio_field and prio_field.upper() == priority.upper():
                filtered.append(e)
            elif tags and priority.upper() in [t.upper() for t in tags]:
                filtered.append(e)
            elif not prio_field and not tags:
                # No priority info at all — include with warning
                filtered.append(e)
        evals = filtered
    if tag:
        evals = [e for e in evals if tag in e.get("tags", [])]
    return evals


# --- Commands ---

def cmd_list(args):
    """List available sim test cases."""
    data = load_yaml()
    templates = load_sim_templates()
    evals = filter_evals(data.get("evals", []), args.priority, getattr(args, 'tag', None))

    print(f"{'Eval Name':45s} {'Has Template':14s} {'Priority':10s}")
    print("-" * 70)
    for ev in evals:
        has = "Yes" if ev["name"] in templates else "No"
        print(f"  {ev['name']:43s} {has:14s} {ev.get('priority', '-'):10s}")

    covered = sum(1 for e in evals if e["name"] in templates)
    print(f"\n{covered}/{len(evals)} evals have sim templates")


def cmd_convert(args):
    """Export sim test cases to JSON files."""
    data = load_yaml()

    output_dir = args.output or SIM_TESTS_DIR
    os.makedirs(output_dir, exist_ok=True)

    evals = filter_evals(data.get("evals", []), args.priority, getattr(args, 'tag', None))

    all_tests = []
    for ev in evals:
        tc = build_test_case(ev)
        if not tc:
            continue
        all_tests.append(tc)
        filepath = os.path.join(output_dir, f"{tc['name']}.json")
        with open(filepath, "w") as f:
            json.dump(tc, f, indent=2)

    combined = os.path.join(output_dir, "_all_tests.json")
    with open(combined, "w") as f:
        json.dump(all_tests, f, indent=2)

    print(f"Wrote {len(all_tests)} test cases to {output_dir}/")


def _escape(text):
    """HTML-escape a string."""
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
        return f"{seconds / 60:.1f}m"
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


def generate_html_report(results, output_path, modality, model, app_name="", wall_clock_s=None):
    """Generate an HTML report with transcripts, tool calls, and failure details."""
    total = len(results)
    passed = sum(1 for r in results if r.get("passed"))
    errors = sum(1 for r in results if "error" in r)
    pct = 100 * passed / total if total else 0

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
            from cxas_scrapi.core.tools import Tools
            tools_map = Tools(app_name=app_name).get_tools_map()
        except Exception:
            pass

    parts = app_name.split("/") if app_name else []
    project_id = parts[1] if len(parts) > 1 else ""
    location = parts[3] if len(parts) > 3 else ""
    app_id = parts[5] if len(parts) > 5 else ""
    ces_base = f"https://ces.cloud.google.com/projects/{project_id}/locations/{location}/apps/{app_id}" if app_id else ""

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Simulation Report - {ts}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 1100px; margin: 0 auto; padding: 20px; background: #f8f9fa; }}
  h1 {{ color: #1a1a2e; border-bottom: 3px solid #e94560; padding-bottom: 10px; }}
  h2 {{ color: #1a1a2e; margin-top: 30px; }}
  .summary {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 20px; }}
  .summary .big {{ font-size: 2em; font-weight: bold; }}
  .pass {{ color: #27ae60; }} .fail {{ color: #e74c3c; }} .error {{ color: #e67e22; }}
  table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
  th, td {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid #ddd; }}
  th {{ background: #2c3e50; color: white; }}
  tr:hover {{ background: #f5f5f5; }}
  .eval-card {{ background: white; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin: 15px 0; overflow: hidden; }}
  .eval-header {{ padding: 12px 16px; font-weight: bold; cursor: pointer; display: flex; justify-content: space-between; align-items: center; }}
  .eval-header.pass-bg {{ background: #d4edda; border-left: 4px solid #27ae60; }}
  .eval-header.fail-bg {{ background: #f8d7da; border-left: 4px solid #e74c3c; }}
  .eval-body {{ padding: 0 16px 16px; }}
  .transcript {{ background: #f8f9fa; border-radius: 6px; padding: 12px; margin: 8px 0; font-size: 0.9em; }}
  .transcript .user {{ color: #2980b9; margin: 6px 0; }}
  .transcript .agent {{ color: #27ae60; margin: 6px 0; }}
  .transcript .system {{ color: #e67e22; margin: 4px 0; font-size: 0.85em; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.8em; font-weight: bold; }}
  .badge.pass {{ background: #d4edda; color: #155724; }}
  .badge.fail {{ background: #f8d7da; color: #721c24; }}
  .badge.met {{ background: #d4edda; color: #155724; }}
  .badge.not-met {{ background: #f8d7da; color: #721c24; }}
  .expectation {{ margin: 6px 0; padding: 8px; background: #f0f0f0; border-radius: 4px; }}
  .step {{ margin: 6px 0; padding: 8px; border-left: 3px solid #3498db; background: #f0f8ff; }}
  .meta {{ color: #666; font-size: 0.85em; }}
  details {{ margin: 4px 0; }}
  summary {{ cursor: pointer; font-weight: bold; padding: 4px 0; }}
  .tool-details {{ margin: 4px 0; padding: 4px 8px; background: #f3e8ff; border-radius: 4px; border-left: 3px solid #8e44ad; }}
  .tool-summary {{ font-weight: normal; font-size: 0.9em; color: #6c3483; padding: 2px 0; }}
  .tool-data {{ margin: 4px 0; padding: 8px; background: #faf5ff; border-radius: 4px; font-size: 0.8em; white-space: pre-wrap; word-break: break-word; overflow-x: auto; }}
  .tool-section {{ font-size: 0.85em; color: #555; margin-top: 6px; }}
  .run-dot {{ display: inline-block; width: 12px; height: 12px; border-radius: 50%; margin-right: 3px; cursor: pointer; border: 2px solid transparent; transition: border-color 0.15s; }}
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
<h1>Simulation Eval Report</h1>
<div class="summary">
  <div class="big {('pass' if pct >= 90 else 'fail')}">{pct:.1f}%</div>
  <div>{passed}/{total} passed | {errors} errors | {modality} | model: {model}</div>
  <div class="meta">Generated {ts}{f' | Runtime: {_fmt_duration(wall_clock_s)}' if wall_clock_s else ''}</div>
</div>

<h2>Results by Eval</h2>
<table>
  <tr><th>Score</th><th>Eval</th><th>Runs</th></tr>
"""

    for name, s in sorted(eval_stats.items(), key=lambda x: x[1]["pass"] / max(x[1]["total"], 1)):
        score = f"{s['pass']}/{s['total']}"
        cls = "pass" if s["pass"] == s["total"] else "fail"
        dots = ""
        for i, r in enumerate(s["runs"]):
            dot_cls = "p" if r.get("passed") else ("e" if "error" in r else "f")
            safe_name = name.replace("'", "\\'")
            dots += f'<span class="run-dot {dot_cls}" title="Run {r["run"]}" onclick="jumpToRun(\'{safe_name}\', {i})"></span>'
        html += f'  <tr><td class="{cls}"><b>{score}</b></td><td>{_escape(name)}</td><td>{dots}</td></tr>\n'

    html += "</table>\n\n<h2>Eval Details</h2>\n"

    for name, s in sorted(eval_stats.items(), key=lambda x: x[1]["pass"] / max(x[1]["total"], 1)):
        score = f"{s['pass']}/{s['total']}"
        cls = "pass-bg" if s["pass"] == s["total"] else "fail-bg"
        html += f'<div class="eval-card" id="eval-{name}">\n'
        html += f'<div class="eval-header {cls}">{_escape(name)} <span>{score}</span></div>\n'
        html += f'<div class="eval-body">\n'

        for r in s["runs"]:
            run_cls = "pass" if r.get("passed") else "fail"
            session_id = r.get("session_id", "")
            html += f'<details class="run-detail"{"" if not r.get("passed") else ""}>\n'
            html += f'<summary>Run {r["run"]} — <span class="{run_cls}">{"PASS" if r.get("passed") else "FAIL"}</span>'
            html += f' | goals: {r.get("goals", "?")} | expectations: {r.get("expectations", "?")} | turns: {r.get("turns", "?")}</summary>\n'

            if session_id:
                if ces_base:
                    session_url = f"{ces_base}?panel=conversation_list&id={session_id}&source=EVAL"
                    html += f'<div class="session-link">Session: <a href="{session_url}" target="_blank"><code>{session_id}</code></a></div>\n'
                else:
                    html += f'<div class="session-link">Session: <code>{session_id}</code></div>\n'

            # Session parameters
            sparams = r.get("session_parameters", {})
            if sparams:
                html += f'<details class="tool-details"><summary class="tool-summary">&#9881; <b>Session Parameters</b></summary>'
                html += f'<pre class="tool-data">{_escape(json.dumps(sparams, indent=2))}</pre></details>\n'

            if "error" in r:
                html += f'<div class="expectation"><b>Error:</b> {_escape(r["error"])}</div>\n'
            else:
                for step in r.get("step_details", []):
                    step_cls = "pass" if step["status"] == "Completed" else "fail"
                    html += f'<div class="step"><b>Goal:</b> {_escape(step["goal"])}<br><b>Criteria:</b> {_escape(step["success_criteria"])}<br>'
                    html += f'<b>Status:</b> <span class="badge {step_cls.replace("pass","met").replace("fail","not-met")}">{_escape(step["status"])}</span><br>'
                    if step.get("justification"):
                        html += f'<b>Justification:</b> {_escape(step["justification"])}'
                    html += '</div>\n'

                for exp in r.get("expectation_details", []):
                    exp_cls = "met" if exp["status"] == "Met" else "not-met"
                    html += f'<div class="expectation"><span class="badge {exp_cls}">{_escape(exp["status"])}</span> {_escape(exp["expectation"])}'
                    if exp.get("justification"):
                        html += f'<br><span class="meta">{_escape(exp["justification"])}</span>'
                    html += '</div>\n'

                trace = r.get("detailed_trace", [])
                if trace:
                    html += f'<details open><summary>Conversation Trace ({r.get("turns", "?")} turns)</summary>\n<div class="transcript">\n'

                    parsed_lines = []
                    for entry in trace:
                        for line in entry.split("\n"):
                            line = line.strip()
                            if not line:
                                continue
                            line = _format_trace_line(line, tools_map)
                            if line.startswith("Agent Text (Diag):"):
                                continue
                            elif line.startswith("Agent Text:"):
                                parsed_lines.append(("agent", line[len("Agent Text:"):].strip()))
                            elif line.startswith("User:"):
                                parsed_lines.append(("user", line[5:].strip()))
                            elif line.startswith("Tool Call"):
                                parsed_lines.append(("tool_call", line))
                            elif line.startswith("Tool Response"):
                                parsed_lines.append(("tool_resp", line))
                            else:
                                parsed_lines.append(("system", line))

                    merged = []
                    for kind, text in parsed_lines:
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
                            call_text = item[1]
                            lbl, _, args = call_text.partition(" with args ")
                            lbl = lbl.replace("Tool Call: ", "").replace("Tool Call (Output): ", "")
                            lbl = lbl.split("/")[-1] if "/" in lbl else lbl
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
                            html += f'<details class="tool-details"><summary class="tool-summary">&#128228; <b>{_escape(lbl)}</b> response</summary>'
                            if result:
                                html += f'<pre class="tool-data">{_escape(result)}</pre>'
                            html += '</details>\n'
                        else:
                            html += f'<div class="system">{_escape(item[1])}</div>\n'

                    html += '</div>\n</details>\n'

            html += '</details>\n'
        html += '</div></div>\n'

    html += "</body></html>"
    with open(output_path, "w") as f:
        f.write(html)


def _run_single_eval(app_name, tc, run_idx, runs, model, modality, verbose):
    """Run a single eval iteration. Designed to be called from a thread pool."""
    name = tc["name"]
    label = f"{name} (run {run_idx + 1}/{runs})"

    try:
        # Each thread gets its own SimRunner instance (separate session client)
        import time as _time
        _start = _time.time()
        sim = EnhancedSimRunner(app_name=app_name)
        conv = sim.simulate_conversation(
            test_case=tc,
            model=model,
            console_logging=verbose,
            modality=modality,
        )
        duration_s = round(_time.time() - _start, 1)

        goals_completed = sum(
            1 for p in conv.steps_progress if p.status == StepStatus.COMPLETED
        )
        total_goals = len(conv.steps_progress)
        expectations_met = sum(
            1 for r in conv.expectation_results if r.status.value == "Met"
        )
        total_exp = len(conv.expectation_results)

        passed = (goals_completed == total_goals)
        if total_exp > 0:
            passed = passed and (expectations_met == total_exp)

        status = "PASS" if passed else "FAIL"
        print(f"  {status}  {label} | goals: {goals_completed}/{total_goals} | "
              f"expectations: {expectations_met}/{total_exp} | "
              f"turns: {conv.current_turn} | {duration_s}s")

        return {
            "name": name,
            "run": run_idx + 1,
            "passed": passed,
            "goals": f"{goals_completed}/{total_goals}",
            "expectations": f"{expectations_met}/{total_exp}",
            "turns": conv.current_turn,
            "duration_s": duration_s,
            "session_id": getattr(conv, "_session_id", ""),
            "session_parameters": tc.get("session_parameters", {}),
            "transcript": conv.get_transcript(),
            "detailed_trace": getattr(conv, "_detailed_trace", []),
            "step_details": [
                {
                    "goal": p.step.goal,
                    "success_criteria": p.step.success_criteria,
                    "status": p.status.value,
                    "justification": p.justification,
                }
                for p in conv.steps_progress
            ],
            "expectation_details": [
                {
                    "expectation": r.expectation,
                    "status": r.status.value,
                    "justification": r.justification,
                }
                for r in conv.expectation_results
            ],
        }

    except Exception as e:
        print(f"  ERROR  {label}: {e}")
        return {"name": name, "run": run_idx + 1, "passed": False, "error": str(e)}


def cmd_run(args):
    """Run sim evals against the live agent."""
    data = load_yaml()
    app_name = get_app_name()

    templates = load_sim_templates()

    if args.eval:
        # When specific evals are requested, source directly from simulations.yaml
        test_cases = []
        for name in args.eval:
            if name in templates:
                t = templates[name]
                test_cases.append({
                    "name": name,
                    "steps": t["steps"],
                    "expectations": t.get("expectations", []),
                    "session_parameters": t.get("session_parameters", {}),
                    "metadata": {},
                })
    else:
        # Otherwise, filter scenario evals that have sim templates
        evals = filter_evals(data.get("evals", []), args.priority, getattr(args, 'tag', None))
        test_cases = []
        for ev in evals:
            tc = build_test_case(ev)
            if tc:
                test_cases.append(tc)
        # Also include sim-only evals matching the filter
        for name, t in templates.items():
            if any(tc["name"] == name for tc in test_cases):
                continue
            tags = t.get("tags", [])
            if args.priority:
                if tags and args.priority.upper() not in [tg.upper() for tg in tags]:
                    continue
                if not tags:
                    print(f"  WARNING: sim '{name}' has no tags — including anyway (add tags for proper filtering)")
            tag_filter = getattr(args, 'tag', None)
            if tag_filter and tag_filter not in tags:
                continue
            test_cases.append({
                "name": name,
                "steps": t["steps"],
                "expectations": t.get("expectations", []),
                "session_parameters": t.get("session_parameters", {}),
                "metadata": {},
            })

    if not test_cases:
        print("No matching evals with sim templates found.")
        return

    model = args.model or _DEFAULT_MODEL
    modality = args.channel or "text"
    runs = args.runs or 1
    parallel = args.parallel or 1

    total_jobs = len(test_cases) * runs
    print(f"Running {len(test_cases)} evals x {runs} runs ({modality}, model: {model})")
    if parallel > 1:
        print(f"Parallelism: {parallel} concurrent sessions")
    print(f"App: {app_name}\n")

    # Build job list: (test_case, run_index)
    jobs = []
    for tc in test_cases:
        for run_idx in range(runs):
            jobs.append((tc, run_idx))

    all_results = []
    _batch_start = time.time()

    if parallel <= 1:
        # Sequential execution
        for tc, run_idx in jobs:
            result = _run_single_eval(app_name, tc, run_idx, runs, model, modality, args.verbose)
            all_results.append(result)
    else:
        # Parallel execution
        with ThreadPoolExecutor(max_workers=parallel) as executor:
            futures = {}
            for tc, run_idx in jobs:
                future = executor.submit(
                    _run_single_eval, app_name, tc, run_idx, runs,
                    model, modality, False  # disable verbose in parallel mode
                )
                futures[future] = (tc["name"], run_idx)

            for future in as_completed(futures):
                result = future.result()
                all_results.append(result)

    # Summary
    print(f"\n{'=' * 60}")
    total = len(all_results)
    passed = sum(1 for r in all_results if r.get("passed"))
    errors = sum(1 for r in all_results if "error" in r)
    pct = 100 * passed / total if total else 0
    print(f"Overall: {passed}/{total} ({pct:.1f}%) | Errors: {errors}\n")

    eval_stats = {}
    for r in all_results:
        n = r["name"]
        if n not in eval_stats:
            eval_stats[n] = {"pass": 0, "total": 0}
        eval_stats[n]["total"] += 1
        if r.get("passed"):
            eval_stats[n]["pass"] += 1

    for name, s in sorted(eval_stats.items(), key=lambda x: x[1]["pass"] / max(x[1]["total"], 1)):
        score = f"{s['pass']}/{s['total']}"
        marker = " <<<" if s["pass"] < s["total"] else ""
        print(f"  {score:>5}  {name}{marker}")

    # Capture wall clock time
    wall_clock_s = round(time.time() - _batch_start, 1)
    print(f"\nWall clock: {wall_clock_s}s")

    # Save results + generate report
    os.makedirs(REPORTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")

    # Wrap results with metadata
    output = {
        "wall_clock_s": wall_clock_s,
        "parallel": parallel,
        "modality": modality,
        "model": model,
        "results": all_results,
    }
    json_path = os.path.join(REPORTS_DIR, f"sim_results_{ts}.json")
    with open(json_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    html_path = os.path.join(REPORTS_DIR, f"sim_report_{ts}.html")
    generate_html_report(all_results, html_path, modality, model, app_name, wall_clock_s=wall_clock_s)
    print(f"\nResults: {json_path}")
    print(f"Report:  {html_path}")


def main():
    try:
        import cxas_scrapi  # noqa: F401
    except ImportError:
        print("Error: cxas-scrapi not installed. Activate venv (source .venv/bin/activate) and install cxas-scrapi first.")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="LLM-User Simulation eval runner (SCRAPI)")
    sub = parser.add_subparsers(dest="command")

    p_list = sub.add_parser("list", help="List available sim test cases")
    p_list.add_argument("--priority", default=None)
    p_list.add_argument("--tag", default=None, help="Filter by tag (e.g. outage, escalation)")

    p_convert = sub.add_parser("convert", help="Export sim test cases to JSON")
    p_convert.add_argument("--priority", default=None)
    p_convert.add_argument("--tag", default=None, help="Filter by tag")
    p_convert.add_argument("--output", default=None)

    p_run = sub.add_parser("run", help="Run sim evals against live agent")
    p_run.add_argument("--priority", default=None)
    p_run.add_argument("--tag", default=None, help="Filter by tag (e.g. outage, escalation)")
    p_run.add_argument("--eval", action="append", default=None, help="Eval name (can specify multiple)")
    p_run.add_argument("--channel", default="text", choices=["text", "audio"])
    p_run.add_argument("--model", default=None)
    p_run.add_argument("--runs", type=int, default=1)
    p_run.add_argument("--parallel", type=int, default=1, help="Number of concurrent sessions (default: 1)")
    p_run.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    commands = {"list": cmd_list, "convert": cmd_convert, "run": cmd_run}
    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
