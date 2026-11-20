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

r"""Copies the bundled starter agent into a new CXAS app directory.

By default, scaffolds a 2-level agent (`select_domain` + `load_workflow`).
Pass `--single-level` when the agent has `<= 12` total workflows to scaffold
a 1-level agent (`load_workflow` only, saving an outer routing hop on Turn 1).

Example:
  python3 scaffold_from_sample.py --target-dir cxas_app/my-agent \
    --app-id 0a1b2c3d-... --project-id my-gcp-project \
    --agent-name Booking_Agent
  python3 scaffold_from_sample.py --target-dir cxas_app/my-agent \
    --app-id 0a1b2c3d-... --project-id my-gcp-project \
    --agent-name Booking_Agent --single-level
"""

import argparse
import ast
import json
import pathlib
import re
import shutil
import stat
from typing import Any

SAMPLE_AGENT = "Support_Agent"

SINGLE_LEVEL_LOAD_WORKFLOW_CODE = "\n".join(
    [
        "# pylint: disable=missing-module-docstring,"
        "undefined-variable,unused-argument",
        "from typing import Any",
        "",
        "VALID_WORKFLOWS = (",
        '    "live_agent_escalation",',
        ")",
        "",
        "",
        "def load_workflow(workflow_name: str, rationale: str)"
        " -> dict[str, Any]:",
        '  """Loads instructions for the workflow matching the request.',
        "",
        "  Args:",
        "    workflow_name (str): Exact name of the workflow to load, from",
        "      the workflow list.",
        "    rationale (str): Why this workflow is being loaded. Kept in the",
        "      conversation history so later turns know what prompted it. One",
        "      short sentence (max 15 words) stating the customer's request",
        '      or tool result that led here, e.g. "Connect the customer with',
        '      a live representative." Do not restate the workflow name or',
        "      include personal data.",
        "",
        "  Returns:",
        "    dict[str, Any]: Status and loaded workflow, or valid_workflows",
        "      on an unknown workflow name.",
        '  """',
        "  name = workflow_name.strip()",
        "  if name not in VALID_WORKFLOWS:",
        "    return {",
        '        "status": "error",',
        '        "error": "invalid_workflow",',
        '        "valid_workflows": list(VALID_WORKFLOWS),',
        "    }",
        "",
        '  context.state["current_workflow"] = name',
        "  return {",
        '      "status": "ok",',
        '      "current_workflow": name,',
        "  }",
        "",
    ]
)

SINGLE_LEVEL_INSTRUCTION = (
    "\n".join(
        [
            "<role>",
            (
                "You are a virtual customer support assistant. You help"
                " customers with their requests and connect them with a live"
                " representative when needed."
            ),
            "</role>",
            "",
            "<constraints>",
            (
                "1. Reply only with what the customer should see or hear. Never"
                " include notes, reasoning, or tags."
            ),
            (
                "2. Never mention workflows, tools, or any other internal names"
                " to the customer."
            ),
            (
                "3. Only share facts returned by tools in this conversation."
                " Never guess or invent details, statuses, or policies."
            ),
            (
                "4. Keep replies short and conversational: one to three"
                " sentences, lead with the answer, and ask at most one question"
                " at a time."
            ),
            (
                "5. Write plain sentences without markdown, lists, or links,"
                " so replies read well in a chat window and sound natural on"
                " a phone call."
            ),
            "</constraints>",
            "",
            "<anytime_actions>",
            (
                "- When the customer says they are all set and do not need"
                " anything else, call {@TOOL: graceful_end_session} right away"
                " without replying first, and pass a short, warm goodbye in"
                " `goodbye_message`. You do not need to load a workflow to"
                " end the conversation."
            ),
            (
                "- When the customer asks to speak with a live representative,"
                " call {@TOOL: execute_live_agent_handover} right away"
                " without replying first, and pass"
                ' `reason="customer_requested_agent"` and a one-sentence'
                " `pre_transfer_message`."
            ),
            (
                "- When the customer asks for an unsupported request or change"
                " that no workflow covers, call"
                " {@TOOL: execute_live_agent_handover} right away without"
                ' replying first, and pass `reason="request_not_supported"`'
                " and a one-sentence `pre_transfer_message`."
            ),
            "</anytime_actions>",
            "",
            "<workflow_list>",
            "Available Workflows (call {@TOOL: load_workflow} to load one):",
            (
                "- `live_agent_escalation`: Connect the customer with a live"
                " representative."
            ),
            (
                "Your tool list only has the tools for the currently loaded"
                " workflow. Never call a tool that is not in your tool list,"
                " even if you called it earlier in the conversation. If you"
                " need a tool that is not in your list, first silently call"
                " {@TOOL: load_workflow} with the workflow that uses it, then"
                " call the tool."
            ),
            "</workflow_list>",
            "",
            "<status>",
            "Today's date is {{current_date}}.",
            "{{% if current_workflow %}}",
            (
                'Loaded Workflow: "{{current_workflow}}". To switch to a'
                " different workflow, silently call {@TOOL: load_workflow} with"
                " the exact workflow name first, before replying or calling"
                " any other tool."
            ),
            "{{% else %}}",
            (
                "No workflow is loaded yet. Silently call"
                " {@TOOL: load_workflow} with the matching workflow first when"
                " the customer states what they need."
            ),
            "{{% endif %}}",
            "</status>",
            "",
            "{{% if pending_handoff_reason %}}",
            "<pending_handoff>",
            (
                "A handoff to a live representative is required (reason:"
                ' "{{pending_handoff_reason}}").'
            ),
            (
                "Call {@TOOL: execute_live_agent_handover} right away without"
                " replying first, and pass a one-sentence"
                " `pre_transfer_message` letting the customer know you are"
                " connecting them."
            ),
            "</pending_handoff>",
            "{{% elif current_workflow == 'live_agent_escalation' %}}",
            '<workflow name="live_agent_escalation">',
            "<workflow_rules>",
            (
                "- Connect the customer with a live representative when they"
                " ask for one or when their request requires human help."
            ),
            "</workflow_rules>",
            "<current_step>",
            (
                "- Call {@TOOL: execute_live_agent_handover} right away without"
                " replying first. Pass a short `reason` and a one-sentence"
                " `pre_transfer_message` letting the customer know you are"
                " connecting them."
            ),
            "</current_step>",
            "</workflow>",
            "{{% endif %}}",
        ]
    )
    + "\n"
)


def _write_json(path: pathlib.Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _extract_docstring(source: str, func_name: str) -> str:
    tree = ast.parse(source)
    for node in tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == func_name
        ):
            return ast.get_docstring(node) or ""
    return ""


def _update_single_level_lint_config(lint_path: pathlib.Path) -> None:
    if not lint_path.exists():
        return
    text = lint_path.read_text(encoding="utf-8")
    text = text.replace("<domain_list>, and ", "")
    text = re.sub(
        r"(ignore:\s*\[)\s*current_workflow\s*,\s*",
        r"\1",
        text,
    )
    lint_path.write_text(text, encoding="utf-8")


def scaffold(
    target_dir: pathlib.Path | str,
    app_id: str = "self-steering-agent-sample",
    project_id: str = "$env_var",
    display_name: str | None = None,
    agent_name: str = SAMPLE_AGENT,
    overwrite: bool = False,
    single_level: bool = False,
) -> pathlib.Path:
    """Copies the bundled starter agent into `target_dir` and configures it."""
    # Support positional `scaffold(dest, app_id, agent_name, single_level=...)`
    # where the 3rd positional argument is an agent name like `Direct_Agent`.
    if (
        agent_name == SAMPLE_AGENT
        and display_name is None
        and "_" in project_id
        and not project_id.startswith("$")
    ):
        agent_name = project_id
        project_id = "$env_var"

    sample = (
        pathlib.Path(__file__).resolve().parent.parent
        / "resources"
        / "sample-agent"
    )
    target = pathlib.Path(target_dir).resolve()
    if target.exists():
        if not overwrite:
            raise SystemExit(
                f"{target} already exists. Pass --overwrite to replace it."
            )
        shutil.rmtree(target)
    shutil.copytree(
        sample,
        target,
        ignore=shutil.ignore_patterns("__init__.py", "__pycache__"),
    )
    for item in (target, *target.rglob("*")):
        mode = item.stat().st_mode
        if item.is_dir():
            item.chmod(mode | stat.S_IRWXU)
        else:
            item.chmod(mode | stat.S_IRUSR | stat.S_IWUSR)

    app_path = target / "app.json"
    app = json.loads(app_path.read_text(encoding="utf-8"))
    app["name"] = app_id
    app["displayName"] = display_name or target.name
    app["rootAgent"] = agent_name
    if single_level:
        app["variableDeclarations"] = [
            v
            for v in app.get("variableDeclarations", [])
            if v.get("name") != "current_domain"
        ]
    _write_json(app_path, app)

    env_path = target / "environment.json"
    env = json.loads(env_path.read_text(encoding="utf-8"))
    env["app"]["loggingSettings"]["bigqueryExportSettings"]["project"] = (
        project_id
    )
    _write_json(env_path, env)

    agent_dir = target / "agents" / agent_name
    if agent_name != SAMPLE_AGENT:
        (target / "agents" / SAMPLE_AGENT).rename(agent_dir)
        old_json = agent_dir / f"{SAMPLE_AGENT}.json"
        raw = old_json.read_text(encoding="utf-8").replace(
            f"agents/{SAMPLE_AGENT}/", f"agents/{agent_name}/"
        )
        old_json.unlink()
        agent = json.loads(raw)
        agent["name"] = agent_name
        agent["displayName"] = agent_name
    else:
        agent = json.loads(
            (agent_dir / f"{agent_name}.json").read_text(encoding="utf-8")
        )

    if single_level:
        shutil.rmtree(target / "tools" / "select_domain", ignore_errors=True)
        agent["tools"] = [
            t for t in agent.get("tools", []) if t != "select_domain"
        ]
        agent["description"] = (
            "Generic starter support agent for chat or voice. Uses workflows"
            " in a Jinja instruction, dynamic tool hiding, and deterministic"
            " actions."
        )
        lw_dir = target / "tools" / "load_workflow"
        (lw_dir / "python_function" / "python_code.py").write_text(
            SINGLE_LEVEL_LOAD_WORKFLOW_CODE, encoding="utf-8"
        )
        lw_json_path = lw_dir / "load_workflow.json"
        lw_json = json.loads(lw_json_path.read_text(encoding="utf-8"))
        lw_json.setdefault("pythonFunction", {})["description"] = (
            _extract_docstring(SINGLE_LEVEL_LOAD_WORKFLOW_CODE, "load_workflow")
        )
        _write_json(lw_json_path, lw_json)
        (agent_dir / "instruction.txt").write_text(
            SINGLE_LEVEL_INSTRUCTION, encoding="utf-8"
        )
        _update_single_level_lint_config(target / "cxaslint.yaml")

    _write_json(agent_dir / f"{agent_name}.json", agent)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--target-dir",
        required=True,
        help="New app directory, e.g. cxas_app/my-agent.",
    )
    parser.add_argument(
        "--app-id",
        required=True,
        help="CES app ID to push to (from `cxas create` or the console).",
    )
    parser.add_argument(
        "--project-id",
        required=True,
        help="GCP project for the BigQuery export setting in environment.json.",
    )
    parser.add_argument(
        "--display-name",
        default=None,
        help="App display name (defaults to the target directory name).",
    )
    parser.add_argument(
        "--agent-name",
        default=SAMPLE_AGENT,
        help=f"Root agent name (defaults to {SAMPLE_AGENT}).",
    )
    parser.add_argument(
        "--single-level",
        action="store_true",
        help=(
            "Scaffold a 1-level workflow agent (`load_workflow` only, no"
            " `select_domain`), recommended for agents with <= 12 workflows."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace the target directory if it already exists.",
    )
    args = parser.parse_args()

    target = scaffold(
        target_dir=args.target_dir,
        app_id=args.app_id,
        project_id=args.project_id,
        display_name=args.display_name,
        agent_name=args.agent_name,
        overwrite=args.overwrite,
        single_level=args.single_level,
    )
    app = json.loads((target / "app.json").read_text(encoding="utf-8"))
    mode_label = (
        "1-level (load_workflow)"
        if args.single_level
        else "2-level (select_domain + load_workflow)"
    )
    router_hint = (
        "VALID_WORKFLOWS in load_workflow"
        if args.single_level
        else "VALID_DOMAINS and DOMAIN_WORKFLOWS"
    )
    print(f"Created {target}")
    print(f"  app id:       {args.app_id}")
    print(f"  display name: {app['displayName']}")
    print(f"  root agent:   {args.agent_name}")
    print(f"  mode:         {mode_label}")
    print(
        f"Next: rewrite the instruction, GREETING and agent description,"
        f" update {router_hint}, add your domain tools and variables, then"
        " run cxas lint, check_tool_visibility.py and run_locally.py before"
        " pushing."
    )


if __name__ == "__main__":
    main()
