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

"""Run the 6 build-verification gates against a deployed GECX app.

The gates encode the checks documented in references/build-verification.md.
Replaces the per-gate Python snippets the model used to re-derive on every build.

Usage:
  python scripts/gate-check.py
  python scripts/gate-check.py --skip-push       # Skip the lint+push round-trip in Gate 1
  python scripts/gate-check.py --multi-turn prompts.json  # Run Gate 6 with the given prompts
  python scripts/gate-check.py --json            # Print JSON result to stdout instead of pretty text
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import uuid

from config import load_config, get_project_path


# ---------- Gate runner helpers ----------

class GateResult:
    def __init__(self, gate, name):
        self.gate = gate
        self.name = name
        self.passed = None
        self.skipped = False
        self.findings = []
        self.warnings = []
        self.error = None

    def to_dict(self):
        status = "skipped" if self.skipped else ("pass" if self.passed else "fail")
        return {
            "gate": self.gate,
            "name": self.name,
            "status": status,
            "findings": self.findings,
            "warnings": self.warnings,
            "error": self.error,
        }


def _print_gate_header(gate, name):
    print(f"\n{'=' * 60}")
    print(f"  Gate {gate}: {name}")
    print(f"{'=' * 60}")


def _print_gate_footer(result: GateResult):
    if result.skipped:
        print("  → SKIPPED")
        return
    status = "PASS" if result.passed else "FAIL"
    print(f"  → {status}")
    for w in result.warnings:
        print(f"    WARN: {w}")
    if result.error:
        print(f"    ERROR: {result.error}")


# ---------- Gates ----------

def gate1_pull_lint_push(config, app_name, skip_push=False) -> GateResult:
    """Pull platform state, lint, optionally push, re-pull, re-lint."""
    r = GateResult(1, "Pull, Lint and Push")
    _print_gate_header(r.gate, r.name)

    project_dir = config["_project_dir"]
    app_dir = os.path.join(project_dir, config.get("app_dir", "cxas_app/"))
    project_id = config["gcp_project_id"]
    location = config["location"]

    env = {**os.environ, "GOOGLE_CLOUD_PROJECT": project_id}

    def _run(cmd, label):
        print(f"  $ {' '.join(cmd)}")
        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if result.returncode != 0:
            r.findings.append({
                "step": label,
                "stdout": result.stdout[-2000:],
                "stderr": result.stderr[-2000:],
            })
            return False
        return True

    # 1. Pull
    pull_cmd = ["cxas", "pull", app_name,
                "--project-id", project_id, "--location", location,
                "--target-dir", app_dir]
    if not _run(pull_cmd, "pull"):
        r.passed = False
        r.error = "Initial pull failed"
        _print_gate_footer(r)
        return r

    # 2. Lint
    lint_cmd = ["cxas", "lint", "--app-dir", app_dir]
    lint_clean = _run(lint_cmd, "lint")

    if not lint_clean:
        r.warnings.append("Initial lint found violations — fix locally then push")

    # 3. Push (only if lint clean and not skipped)
    if lint_clean and not skip_push:
        push_cmd = ["cxas", "push", "--app-dir", app_dir, "--to", app_name,
                    "--project-id", project_id, "--location", location]
        if not _run(push_cmd, "push"):
            r.passed = False
            r.error = "Push failed"
            _print_gate_footer(r)
            return r

        # 4. Re-pull
        if not _run(pull_cmd, "re-pull"):
            r.passed = False
            r.error = "Re-pull failed"
            _print_gate_footer(r)
            return r

        # 5. Re-lint
        if not _run(lint_cmd, "re-lint"):
            r.passed = False
            r.error = "Re-lint after push failed — drift between local and platform"
            _print_gate_footer(r)
            return r

    r.passed = lint_clean
    if not lint_clean:
        r.error = "Lint not clean — run cxas lint and fix violations before continuing"
    _print_gate_footer(r)
    return r


def gate2_agent_hierarchy(app_name) -> GateResult:
    """Verify root agent and all sub-agents exist."""
    r = GateResult(2, "Agent hierarchy")
    _print_gate_header(r.gate, r.name)

    from cxas_scrapi.core.agents import Agents
    from cxas_scrapi.core.apps import Apps

    parts = app_name.split("/")
    project_id, location = parts[1], parts[3]

    try:
        apps = Apps(project_id=project_id, location=location)
        app = apps.get_app(app_name)
        agents_client = Agents(app_name=app_name)
        agents_map = agents_client.get_agents_map(reverse=True)
    except Exception as e:
        r.passed = False
        r.error = f"Failed to fetch agents: {e}"
        _print_gate_footer(r)
        return r

    root_agent_name = app.root_agent.split("/")[-1] if app.root_agent else None
    print(f"  Root agent: {root_agent_name}")
    print(f"  Agents found: {len(agents_map)}")

    for name in agents_map:
        is_root = (name == root_agent_name)
        marker = " (ROOT)" if is_root else ""
        print(f"    - {name}{marker}")

    r.findings.append({
        "root_agent": root_agent_name,
        "agents": list(agents_map.keys()),
    })

    if not root_agent_name:
        r.passed = False
        r.error = "App has no root_agent set"
    elif root_agent_name not in agents_map:
        r.passed = False
        r.error = f"Root agent {root_agent_name} listed on app but not in agents list"
    else:
        r.passed = True

    _print_gate_footer(r)
    return r


def gate3_tool_associations(app_name) -> GateResult:
    """Verify tool associations; warn if root agent missing end_session."""
    r = GateResult(3, "Tool associations")
    _print_gate_header(r.gate, r.name)

    from cxas_scrapi.core.agents import Agents
    from cxas_scrapi.core.apps import Apps
    from cxas_scrapi.core.tools import Tools

    parts = app_name.split("/")
    project_id, location = parts[1], parts[3]

    try:
        apps = Apps(project_id=project_id, location=location)
        app = apps.get_app(app_name)
        agents_client = Agents(app_name=app_name)
        agents_map = agents_client.get_agents_map(reverse=True)
        tools_client = Tools(app_name=app_name)
        tools_map = {t.display_name: t.name for t in tools_client.list_tools()}
    except Exception as e:
        r.passed = False
        r.error = f"Failed to fetch tools/agents: {e}"
        _print_gate_footer(r)
        return r

    print(f"  Platform tools ({len(tools_map)}):")
    for name in tools_map:
        print(f"    - {name}")

    print(f"\n  Agent tool associations:")
    per_agent = {}
    end_session_warnings = []
    for agent_name, resource in agents_map.items():
        agent = agents_client.get_agent(resource)
        tool_ids = [t.split("/")[-1] for t in (agent.tools or [])]
        per_agent[agent_name] = tool_ids

        is_root = (resource == app.root_agent) or (
            agent_name == (app.root_agent.split("/")[-1] if app.root_agent else None)
        )
        has_end = any("end_session" in t for t in (agent.tools or []))
        flag = ""
        if is_root and not has_end:
            flag = "  WARNING: ROOT MISSING end_session"
            end_session_warnings.append(agent_name)
        if not has_end and not is_root:
            # Sub-agents should also have end_session per build-verification.md
            r.warnings.append(f"Sub-agent '{agent_name}' missing end_session in tools array")

        print(f"    {agent_name}: {tool_ids}{flag}")

    r.findings.append({
        "platform_tools": list(tools_map.keys()),
        "per_agent_tools": per_agent,
    })

    if end_session_warnings:
        r.passed = False
        r.error = f"Root agent(s) missing end_session: {end_session_warnings}"
    else:
        r.passed = True

    _print_gate_footer(r)
    return r


def gate4_callback_inventory(app_name) -> GateResult:
    """Inventory callbacks per agent, plus check that local callback tests are discoverable."""
    r = GateResult(4, "Callback inventory + test discovery")
    _print_gate_header(r.gate, r.name)

    from cxas_scrapi.core.agents import Agents
    from cxas_scrapi.core.callbacks import Callbacks

    try:
        agents_client = Agents(app_name=app_name)
        agents_map = agents_client.get_agents_map(reverse=True)
        cb_client = Callbacks(app_name=app_name)
    except Exception as e:
        r.passed = False
        r.error = f"Failed to fetch callbacks: {e}"
        _print_gate_footer(r)
        return r

    inventory = {}
    total_callbacks = 0
    for agent_name, resource in agents_map.items():
        try:
            cbs = cb_client.list_callbacks(resource)
        except Exception as e:
            r.warnings.append(f"Failed to list callbacks for {agent_name}: {e}")
            continue
        agent_inv = {}
        for cb_type, cb_list in cbs.items():
            if cb_list:
                agent_inv[cb_type] = len(cb_list)
                total_callbacks += len(cb_list)
                print(f"  {agent_name}/{cb_type}: {len(cb_list)}")
        inventory[agent_name] = agent_inv

    r.findings.append({"callbacks_per_agent": inventory, "total": total_callbacks})

    # Check whether local callback tests are wired up correctly. The runner globs
    # evals/callback_tests/agents/<agent>/*_callbacks/<base>/test.py and SILENTLY
    # skips any test whose python_code.py isn't in the same dir. If the platform
    # has callbacks AND the user has authored tests under tests/, we expect at
    # least one discoverable test — otherwise the symlink/copy step (run via
    # scripts/sync-callbacks.py) was missed and the tests are dead.
    cb_tests_dir = get_project_path("evals", "callback_tests")
    tests_root = os.path.join(cb_tests_dir, "tests")
    agents_root = os.path.join(cb_tests_dir, "agents")

    authored_count = 0
    if os.path.isdir(tests_root):
        for dirpath, _dirs, files in os.walk(tests_root):
            if "test.py" in files:
                authored_count += 1

    discoverable_count = 0
    if os.path.isdir(agents_root):
        try:
            from cxas_scrapi.evals.callback_evals import CallbackEvals
            cb_runner = CallbackEvals()
            # Pass empty pytest args so we just enumerate, but the runner does
            # execute tests. To avoid a slow test-run inside gate-check, just
            # glob the symlinks ourselves using the same pattern SCRAPI uses.
            import glob as _glob
            pattern = os.path.join(agents_root, "*", "*_callbacks", "*", "test.py")
            for tp in _glob.glob(pattern):
                td = os.path.dirname(tp)
                if os.path.isfile(os.path.join(td, "python_code.py")):
                    discoverable_count += 1
        except ImportError:
            r.warnings.append("cxas_scrapi.evals.callback_evals not importable — skipping discoverability check")

    r.findings.append({
        "tests_authored": authored_count,
        "tests_discoverable_by_runner": discoverable_count,
    })
    print(f"  Local callback tests: {authored_count} authored, {discoverable_count} discoverable")

    if total_callbacks > 0 and authored_count > 0 and discoverable_count == 0:
        r.passed = False
        r.error = (
            f"{authored_count} test.py files exist under {tests_root} but NONE are discoverable by "
            f"test_all_callbacks_in_app_dir — the agents/.../python_code.py copies and test.py symlinks "
            f"are missing. Run: python scripts/sync-callbacks.py "
            f"(post-push) or python scripts/sync-callbacks.py --from-local <app_dir> (pre-push)."
        )
    elif authored_count > discoverable_count:
        r.warnings.append(
            f"{authored_count - discoverable_count} authored test(s) are not discoverable — "
            f"likely missing python_code.py or symlink under agents/. Re-run sync-callbacks.py."
        )
        r.passed = True
    else:
        r.passed = True
    _print_gate_footer(r)
    return r


def gate5_single_turn_smoke(app_name) -> GateResult:
    """Send 'Hello' and verify the agent responds without crashing."""
    r = GateResult(5, "Single-turn smoke test")
    _print_gate_header(r.gate, r.name)

    from cxas_scrapi.core.sessions import Sessions

    try:
        sessions = Sessions(app_name=app_name)
        session_id = f"gate5-{uuid.uuid4().hex[:8]}"
        print(f"  session_id={session_id}, text='Hello'")
        result = sessions.run(session_id=session_id, text="Hello")
        sessions.parse_result(result)
    except Exception as e:
        r.passed = False
        r.error = f"Smoke test failed: {e}"
        _print_gate_footer(r)
        return r

    r.passed = True
    r.findings.append({"session_id": session_id, "status": "responded"})
    _print_gate_footer(r)
    return r


def gate6_multi_turn_smoke(app_name, prompts_file) -> GateResult:
    """Run a sequence of prompts in one session and check natural pacing."""
    r = GateResult(6, "Multi-turn smoke test")
    _print_gate_header(r.gate, r.name)

    if not prompts_file:
        r.skipped = True
        print("  Skipped — pass --multi-turn <prompts.json> to enable.")
        print("  Expected file format:")
        print('    [{"text": "I need help with my account"}, {"text": "July 12, 1948"}, ...]')
        _print_gate_footer(r)
        return r

    if not os.path.exists(prompts_file):
        r.passed = False
        r.error = f"Prompts file not found: {prompts_file}"
        _print_gate_footer(r)
        return r

    with open(prompts_file) as f:
        prompts = json.load(f)

    from cxas_scrapi.core.sessions import Sessions

    sessions = Sessions(app_name=app_name)
    session_id = f"gate6-{uuid.uuid4().hex[:8]}"
    turns = []

    try:
        for i, prompt in enumerate(prompts):
            text = prompt["text"] if isinstance(prompt, dict) else prompt
            print(f"  Turn {i + 1}: '{text}'")
            result = sessions.run(session_id=session_id, text=text)
            sessions.parse_result(result)
            turns.append({"turn": i + 1, "text": text, "status": "responded"})
    except Exception as e:
        r.passed = False
        r.error = f"Multi-turn smoke test failed at turn {len(turns) + 1}: {e}"
        r.findings.append({"session_id": session_id, "completed_turns": turns})
        _print_gate_footer(r)
        return r

    r.passed = True
    r.findings.append({"session_id": session_id, "turns": turns})
    print("  Note: pacing must be verified by reading the printed responses —")
    print("  agent should ask for ONE thing at a time, not dump all questions at once.")
    _print_gate_footer(r)
    return r


# ---------- Main ----------

def main():
    parser = argparse.ArgumentParser(
        description="Run the 6 build-verification gates against a deployed GECX app."
    )
    parser.add_argument("--skip-push", action="store_true",
                       help="Skip the lint+push round-trip in Gate 1 (use when you only want to verify, not modify the platform)")
    parser.add_argument("--multi-turn", default=None,
                       help="Path to a JSON file of prompts to run for Gate 6 (otherwise Gate 6 is skipped)")
    parser.add_argument("--json", action="store_true",
                       help="Print JSON result to stdout instead of pretty text")
    parser.add_argument("--save", default=None,
                       help="Save JSON result to a specific path (default: <project>/eval-reports/gate-check-<timestamp>.json)")
    args = parser.parse_args()

    try:
        import cxas_scrapi  # noqa: F401
    except ImportError:
        print("Error: cxas-scrapi is not installed. Activate venv (source .venv/bin/activate) and install cxas-scrapi first.")
        sys.exit(1)

    config = load_config()
    project_id = config["gcp_project_id"]
    location = config["location"]
    app_id = config["deployed_app_id"]
    app_name = f"projects/{project_id}/locations/{location}/apps/{app_id}"

    print(f"\nGate-check for: {app_name}")

    results = [
        gate1_pull_lint_push(config, app_name, skip_push=args.skip_push),
        gate2_agent_hierarchy(app_name),
        gate3_tool_associations(app_name),
        gate4_callback_inventory(app_name),
        gate5_single_turn_smoke(app_name),
        gate6_multi_turn_smoke(app_name, args.multi_turn),
    ]

    summary = {
        "app_name": app_name,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "gates": [r.to_dict() for r in results],
        "overall": {
            "passed": sum(1 for r in results if r.passed and not r.skipped),
            "failed": sum(1 for r in results if r.passed is False),
            "skipped": sum(1 for r in results if r.skipped),
            "all_pass": all((r.passed or r.skipped) for r in results),
        },
    }

    # Output JSON
    if args.save:
        out_path = args.save
    else:
        reports_dir = get_project_path("eval-reports")
        os.makedirs(reports_dir, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = os.path.join(reports_dir, f"gate-check-{ts}.json")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(f"\n{'=' * 60}")
        print(f"  Summary: {summary['overall']['passed']} passed, "
              f"{summary['overall']['failed']} failed, "
              f"{summary['overall']['skipped']} skipped")
        print(f"  Result: {'ALL PASS' if summary['overall']['all_pass'] else 'FAILURES — see above'}")
        print(f"  JSON saved to: {out_path}")
        print(f"{'=' * 60}\n")

    sys.exit(0 if summary["overall"]["all_pass"] else 1)


if __name__ == "__main__":
    main()
