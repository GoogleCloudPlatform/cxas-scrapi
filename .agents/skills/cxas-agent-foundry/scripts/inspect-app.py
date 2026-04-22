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

"""Inspect a GECX app and dump its architecture, tools, variables, callbacks, and existing evals.

Usage:
  python scripts/inspect-app.py                  # Summary view
  python scripts/inspect-app.py --verbose         # Include instruction text and callback code
  python scripts/inspect-app.py --json            # Output as JSON
  python scripts/inspect-app.py --save report.md  # Save to file
"""

import argparse
import json
import os
import sys
import textwrap

from config import load_app_name


def inspect(app_name, verbose=False):
    """Inspect app and return structured data."""
    from cxas_scrapi.core.apps import Apps
    from cxas_scrapi.core.agents import Agents
    from cxas_scrapi.core.tools import Tools
    from cxas_scrapi.core.callbacks import Callbacks
    from cxas_scrapi.core.evaluations import Evaluations

    parts = app_name.split("/")
    project = parts[1]
    location = parts[3]

    # App
    apps = Apps(project_id=project, location=location)
    try:
        app = apps.get_app(app_name=app_name)
    except Exception as e:
        print(f"Error: Failed to get app '{app_name}': {e}")
        sys.exit(1)

    result = {
        "app_name": app_name,
        "display_name": app.display_name,
        "root_agent": app.root_agent,
        "agents": [],
        "tools": [],
        "evals": {"goldens": [], "scenarios": []},
    }

    # Thresholds
    t = app.evaluation_metrics_thresholds
    if t and t.golden_evaluation_metrics_thresholds:
        gt = t.golden_evaluation_metrics_thresholds
        turn = gt.turn_level_metrics_thresholds
        result["thresholds"] = {
            "semantic_similarity": turn.semantic_similarity_success_threshold,
            "tool_invocation": turn.overall_tool_invocation_correctness_threshold,
            "extra_tool_behavior": getattr(gt.tool_matching_settings, "extra_tool_call_behavior", 0),
            "hallucination": t.golden_hallucination_metric_behavior,
        }

    # Agents
    agents_client = Agents(app_name=app_name)
    try:
        agent_list = agents_client.list_agents()
    except Exception as e:
        print(f"Error: Failed to list agents: {e}")
        agent_list = []

    callbacks_client = Callbacks(app_name=app_name)

    for agent in agent_list:
        agent_info = {
            "name": agent.name,
            "display_name": agent.display_name,
            "tools": [t.split("/")[-1] for t in agent.tools],
            "callbacks": {},
        }

        if verbose and agent.instruction:
            agent_info["instruction"] = agent.instruction

        # Callbacks
        try:
            cb_map = callbacks_client.list_callbacks(agent.name)
        except Exception as e:
            print(f"  Warning: Failed to list callbacks for '{agent.display_name}': {e}")
            cb_map = {}
        for cb_type, cb_list in cb_map.items():
            if cb_list:
                cbs = []
                for i, cb in enumerate(cb_list):
                    cb_info = {"index": i, "disabled": cb.disabled}
                    if cb.description:
                        cb_info["description"] = cb.description
                    if verbose and cb.python_code:
                        cb_info["code_preview"] = cb.python_code[:500]
                    elif cb.python_code:
                        # Show first line of function def
                        first_line = cb.python_code.strip().split("\n")[0]
                        cb_info["signature"] = first_line
                    cbs.append(cb_info)
                agent_info["callbacks"][cb_type] = cbs

        result["agents"].append(agent_info)

    # Tools
    tools_client = Tools(app_name=app_name)
    try:
        tools_map = tools_client.get_tools_map()
        for display_name, tool in tools_map.items():
            tool_info = {
                "display_name": display_name,
                "name": tool.name,
                "id": tool.name.split("/")[-1],
            }
            result["tools"].append(tool_info)
    except Exception as e:
        result["tools_error"] = str(e)

    # Existing evals
    evals_client = Evaluations(app_name=app_name)
    try:
        evals_list = evals_client.list_evaluations()
        for ev in evals_list:
            d = type(ev).to_dict(ev)
            eval_info = {
                "display_name": ev.display_name,
                "id": ev.name.split("/")[-1],
                "tags": list(ev.tags) if ev.tags else [],
            }
            if "golden" in d and d["golden"]:
                eval_info["type"] = "golden"
                turns = d["golden"].get("turns", [])
                eval_info["turns"] = len(turns)
                result["evals"]["goldens"].append(eval_info)
            else:
                eval_info["type"] = "scenario"
                result["evals"]["scenarios"].append(eval_info)
    except Exception as e:
        result["evals_error"] = str(e)

    return result


def format_text(data):
    """Format inspection data as readable text."""
    lines = []
    lines.append(f"App: {data['display_name']}")
    lines.append(f"Resource: {data['app_name']}")
    lines.append(f"Root agent: {data['root_agent'].split('/')[-1]}")
    lines.append("")

    # Thresholds
    if "thresholds" in data:
        t = data["thresholds"]
        lines.append("Scoring Thresholds:")
        lines.append(f"  Semantic similarity: {t['semantic_similarity']}")
        lines.append(f"  Tool invocation: {t['tool_invocation']}")
        lines.append(f"  Extra tool behavior: {t['extra_tool_behavior']}")
        lines.append(f"  Hallucination: {t['hallucination']}")
        lines.append("")

    # Agents
    lines.append(f"Agents ({len(data['agents'])}):")
    for agent in data["agents"]:
        is_root = agent["name"] == data.get("root_agent", "")
        root_marker = " (ROOT)" if is_root else ""
        lines.append(f"  {agent['display_name']}{root_marker}")
        lines.append(f"    Tools: {len(agent['tools'])}")
        for cb_type, cbs in agent.get("callbacks", {}).items():
            for cb in cbs:
                sig = cb.get("signature", "")
                disabled = " [DISABLED]" if cb.get("disabled") else ""
                lines.append(f"    {cb_type}[{cb['index']}]{disabled}: {sig}")

        if "instruction" in agent:
            lines.append(f"    Instruction ({len(agent['instruction'])} chars):")
            preview = agent["instruction"][:200].replace("\n", " ")
            lines.append(f"      {preview}...")
        lines.append("")

    # Tools
    lines.append(f"Tools ({len(data['tools'])}):")
    for tool in data["tools"]:
        lines.append(f"  {tool['display_name']} ({tool['id'][:8]})")
    lines.append("")

    # Evals
    goldens = data["evals"]["goldens"]
    scenarios = data["evals"]["scenarios"]
    lines.append(f"Existing Evals ({len(goldens)} goldens, {len(scenarios)} scenarios):")
    for ev in goldens:
        tags = ", ".join(ev.get("tags", [])[:4])
        lines.append(f"  [golden] {ev['display_name']} ({ev.get('turns', '?')} turns) [{tags}]")
    for ev in scenarios:
        tags = ", ".join(ev.get("tags", [])[:4])
        lines.append(f"  [scenario] {ev['display_name']} [{tags}]")

    if "tools_error" in data:
        lines.append(f"\nTools error: {data['tools_error']}")
    if "evals_error" in data:
        lines.append(f"\nEvals error: {data['evals_error']}")

    return "\n".join(lines)


def main():
    try:
        import cxas_scrapi
    except ImportError:
        print("Error: cxas-scrapi not installed. Activate venv (source .venv/bin/activate) and install cxas-scrapi first.")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Inspect a GECX app")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Include instruction text and callback code")
    parser.add_argument("--json", action="store_true",
                       help="Output as JSON")
    parser.add_argument("--save", default=None,
                       help="Save output to file")
    args = parser.parse_args()

    app_name = load_app_name()
    data = inspect(app_name, verbose=args.verbose)

    if args.json:
        output = json.dumps(data, indent=2, default=str)
    else:
        output = format_text(data)

    if args.save:
        with open(args.save, "w") as f:
            f.write(output)
        print(f"Saved to {args.save}")
    else:
        print(output)


if __name__ == "__main__":
    main()
