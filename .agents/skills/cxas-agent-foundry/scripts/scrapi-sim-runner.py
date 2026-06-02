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
from cxas_scrapi.utils.reporting import generate_html_report


from config import load_app_name, get_project_path

USER_AGENT_EXTENSION = "skill/cxas-agent-foundry/scrapi-sim-runner"


EVALS_YAML = get_project_path("evals", "scenarios", "scenarios.yaml")
SIM_EVALS_YAML = get_project_path("evals", "simulations", "simulations.yaml")
REPORTS_DIR = get_project_path("eval-reports")

_DEFAULT_MODEL = "gemini-3.1-flash-lite"


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


def _parse_priorities(priority):
    """Parse a priority arg like 'P0' or 'P0,P1,P2' into an upper-cased set."""
    if not priority:
        return None
    return {p.strip().upper() for p in priority.split(",") if p.strip()}


def filter_evals(evals, priority=None, tag=None):
    prios = _parse_priorities(priority)
    if prios:
        filtered = []
        for e in evals:
            # Check both 'priority' field and 'tags' list for priority matching
            tags = e.get("tags", [])
            prio_field = e.get("priority", "")
            if prio_field and prio_field.upper() in prios:
                filtered.append(e)
            elif tags and prios.intersection({t.upper() for t in tags}):
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
                prios = _parse_priorities(args.priority)
                if tags and not prios.intersection({tg.upper() for tg in tags}):
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

    print(f"Running {len(test_cases)} evals x {runs} runs ({modality}, model: {model})")
    if parallel > 1:
        print(f"Parallelism: {parallel} concurrent sessions")
    print(f"App: {app_name}\n")

    _batch_start = time.time()
    sim = EnhancedSimRunner(
        app_name=app_name,
        user_agent_extension=USER_AGENT_EXTENSION,
    )
    all_results = sim.run_simulations(
        test_cases=test_cases,
        runs=runs,
        parallel=parallel,
        model=model,
        modality=modality,
        verbose=args.verbose,
    )

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

    report_path = getattr(args, "gcs_report_path", None) or os.path.join(
        REPORTS_DIR, f"sim_report_{ts}.html"
    )
    generate_html_report(
        all_results,
        report_path,
        modality,
        model,
        app_name,
        wall_clock_s=wall_clock_s,
    )
    print(f"\nResults: {json_path}")
    print(f"Report:  {report_path}")


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
    p_run.add_argument(
        "--gcs-report-path",
        type=str,
        default=None,
        help="GCS URI to upload report to (e.g. gs://bucket/report.html)",
    )

    args = parser.parse_args()
    commands = {"list": cmd_list, "convert": cmd_convert, "run": cmd_run}
    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
