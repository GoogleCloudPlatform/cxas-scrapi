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

r"""Lists the tools the model can see for each playbook and task of a CXAS app.

Renders agents/<root>/instruction.txt the way CES does (Jinja rendered, {{# #}}
comments kept) and applies the same word-boundary match as the dynamic
tool-hiding handler. It also checks for mistakes that `cxas lint` misses.
Needs jinja2 (installed with cxas-scrapi).

Example:
  python3 check_tool_visibility.py cxas_app/my-agent
  python3 check_tool_visibility.py cxas_app/my-agent --print \
    --state '{"current_playbook": "order_support", "current_task": "cancel"}'

Exits with 1 when it finds a problem.
"""

import argparse
import ast
import dataclasses
import datetime
import json
import pathlib
import re
import sys
from typing import Any

import jinja2
from jinja2 import meta

COMMENT = re.compile(r"\{\{#.*?#\}\}", re.S)
TAG = re.compile(r"\{%.*?%\}|\{\{.*?\}\}", re.S)
# Variables CES provides without a declaration.
BUILTIN_VARIABLES = frozenset({"current_date", "_session"})
# Tools the model must never see: built-in end_session and the queue helper.
HIDDEN_TOOLS = ("end_session", "set_next_agent_actions")
JINJA_WORDS = frozenset(
    {"in", "not", "and", "or", "is", "if", "elif", "else", "for"}
)
OPEN_PLAYBOOK_CODE = "tools/open_playbook/python_function/python_code.py"
LOAD_TASK_CODE = "tools/load_task/python_function/python_code.py"
MAX_LABEL_WIDTH = 48


@dataclasses.dataclass(frozen=True)
class _App:
    """The parts of an app directory that the checks read."""

    path: pathlib.Path
    config: dict[str, Any]
    tools: list[str]
    instruction: str
    without_comments: str


@dataclasses.dataclass(frozen=True)
class _Render:
    """The instruction rendered for one state, or the error rendering it."""

    label: str
    text: str | None = None
    error: str | None = None


def _literal(path: pathlib.Path, name: str) -> Any:
    """Returns the literal value assigned to `name` at module level."""
    if not path.exists():
        return None
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return ast.literal_eval(node.value)
    return None


def _defaults(app: dict[str, Any]) -> dict[str, Any]:
    empty = {"STRING": "", "BOOLEAN": False, "NUMBER": 0, "INTEGER": 0}
    out = {}
    for var in app.get("variableDeclarations", []):
        schema = var.get("schema", {})
        out[var["name"]] = schema.get("default", empty.get(schema.get("type")))
    tz = app.get("timeZoneSettings", {}).get("timeZone", "UTC")
    out.setdefault("current_date", f"{datetime.date.today().isoformat()}[{tz}]")
    return out


def _mentions(text: str, name: str) -> bool:
    return re.search(rf"\b{re.escape(name)}\b", text) is not None


def _visible(text: str, tools: list[str]) -> list[str]:
    return sorted(t for t in tools if _mentions(text, t))


def _compared_names(source: str, var: str) -> set[str]:
    """Names the template compares `var` with (== 'x', or in ['x', 'y'])."""
    names = set(re.findall(rf"\b{var}\s*==\s*['\"]([^'\"]+)['\"]", source))
    for group in re.findall(rf"\b{var}\s+in\s+[\[(]([^\])]*)[\])]", source):
        names.update(re.findall(r"['\"]([^'\"]+)['\"]", group))
    return names


def _state_label(overrides: dict[str, Any]) -> str:
    """Returns a label like "order_support / cancel_order (order_id=1001)"."""
    label = (
        " / ".join(
            str(overrides[k])
            for k in ("current_playbook", "current_task")
            if overrides.get(k)
        )
        or "start"
    )
    rest = ", ".join(
        f"{k}={v}"
        for k, v in overrides.items()
        if k not in ("current_playbook", "current_task")
    )
    return f"{label} ({rest})" if rest else label


def _json_object(text: str) -> dict[str, Any]:
    """Parses a --state value, which has to be a JSON object."""
    try:
        value = json.loads(text)
    except json.JSONDecodeError as e:
        raise argparse.ArgumentTypeError(f"not valid JSON ({e}): {text}") from e
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError(f"not a JSON object: {text}")
    return value


def _parse_args() -> argparse.Namespace:
    """Parses the command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("app_dir", type=pathlib.Path)
    parser.add_argument(
        "--state",
        action="append",
        default=[],
        type=_json_object,
        help=(
            "Extra state to render, as a JSON object. Include current_playbook"
            " and current_task. Repeatable."
        ),
    )
    parser.add_argument(
        "--print",
        dest="print_rendered",
        action="store_true",
        help=(
            "Print the rendered instruction for each --state, or for every"
            " state when no --state is given."
        ),
    )
    return parser.parse_args()


def _load_app(app_dir: pathlib.Path) -> _App:
    """Reads app.json and the root agent's tool list and instruction."""
    config = json.loads((app_dir / "app.json").read_text(encoding="utf-8"))
    agent_dir = app_dir / "agents" / config["rootAgent"]
    agent_file = agent_dir / f"{config['rootAgent']}.json"
    agent = json.loads(agent_file.read_text(encoding="utf-8"))
    instruction = (agent_dir / "instruction.txt").read_text(encoding="utf-8")
    return _App(
        path=app_dir,
        config=config,
        tools=agent.get("tools", []),
        instruction=instruction,
        without_comments=COMMENT.sub("", instruction),
    )


def _all_tasks(playbook_tasks: dict[str, tuple[str, ...]]) -> set[str]:
    tasks = set()
    for names in playbook_tasks.values():
        tasks.update(names)
    return tasks


def _comment_problems(app: _App) -> list[str]:
    """Finds tool names in {{# #}} comments, which reach the hiding callback."""
    problems = []
    for comment in COMMENT.findall(app.instruction):
        for tool in _visible(comment, app.tools):
            problems.append(
                f"tool name {tool!r} in a Jinja comment (comments reach the"
                f" callback): {comment[:60]!r}"
            )
    return problems


def _syntax_problems(app: _App) -> list[str]:
    """Finds template syntax that jinja2 accepts but a CES push rejects."""
    problems = []
    for tag in TAG.findall(app.without_comments):
        snippet = " ".join(tag.split())
        if re.search(r"\bnot\s+in\b", tag):
            problems.append(
                f"`not in` in {snippet!r}: CES rejects it, write"
                " `not x in [...]`"
            )
        if any(
            m.group(1) not in JINJA_WORDS
            for m in re.finditer(r"\b([A-Za-z_]\w*)\s*\[", tag)
        ):
            problems.append(
                f"subscript in {snippet!r}: CES rejects `x[...]`, use dot"
                " access (`x.key`)"
            )
    return problems


def _router_problems(
    app: _App, playbook_tasks: dict[str, tuple[str, ...]]
) -> list[str]:
    """Checks the router tools' lists against each other and the instruction."""
    problems = []
    valid_playbooks = _literal(app.path / OPEN_PLAYBOOK_CODE, "VALID_PLAYBOOKS")
    if valid_playbooks is None:
        problems.append(f"no VALID_PLAYBOOKS found in {OPEN_PLAYBOOK_CODE}")
    if "load_task" not in app.tools:
        problems.append(
            "load_task isn't in the root agent's tools; every agent needs"
            " open_playbook and load_task"
        )
    if not playbook_tasks:
        problems.append(f"no PLAYBOOK_TASKS found in {LOAD_TASK_CODE}")
    playbooks = set(playbook_tasks)
    tasks = _all_tasks(playbook_tasks)
    instr_playbooks = _compared_names(app.without_comments, "current_playbook")
    instr_tasks = _compared_names(app.without_comments, "current_task")
    if valid_playbooks is not None and set(valid_playbooks) != playbooks:
        problems.append(
            f"VALID_PLAYBOOKS {sorted(valid_playbooks)} != PLAYBOOK_TASKS keys"
            f" {sorted(playbooks)}"
        )
    if instr_playbooks != playbooks:
        problems.append(
            f"playbooks in instruction {sorted(instr_playbooks)} !="
            f" PLAYBOOK_TASKS keys {sorted(playbooks)}"
        )
    if instr_tasks != tasks:
        problems.append(
            f"tasks in instruction {sorted(instr_tasks)} != PLAYBOOK_TASKS"
            f" tasks {sorted(tasks)}"
        )
    for name in sorted((playbooks | tasks) & set(app.tools)):
        problems.append(
            f"{name!r} is both a playbook or task and a tool, so the tool shows"
            " whenever that name is listed"
        )
    return problems


def _compile(app: _App) -> tuple[jinja2.Template, list[str]]:
    """Compiles the instruction with {{# #}} comments kept as text, like CES.

    Args:
      app: The app whose instruction to compile.

    Returns:
      The template, and a problem for each variable app.json doesn't declare,
      since those always render empty.

    Raises:
      jinja2.TemplateSyntaxError: The instruction isn't a valid template.
    """
    env = jinja2.Environment()
    source = COMMENT.sub(
        lambda m: "{% raw %}" + m.group(0) + "{% endraw %}", app.instruction
    )
    declared = {v["name"] for v in app.config.get("variableDeclarations", [])}
    undeclared = (
        meta.find_undeclared_variables(env.parse(source))
        - declared
        - BUILTIN_VARIABLES
    )
    problems = [
        f"variable {name!r} is used in the instruction but not declared in"
        " app.json, so it always renders empty"
        for name in sorted(undeclared)
    ]
    return env.from_string(source), problems


def _default_states(
    playbook_tasks: dict[str, tuple[str, ...]],
) -> list[tuple[str, dict[str, Any]]]:
    """Returns the start state, then each playbook followed by its tasks."""
    states = [("start", {})]
    for playbook, tasks in playbook_tasks.items():
        states.append((playbook, {"current_playbook": playbook}))
        states += [
            (
                f"{playbook} / {task}",
                {"current_playbook": playbook, "current_task": task},
            )
            for task in tasks
        ]
    return states


def _render(
    template: jinja2.Template,
    base: dict[str, Any],
    states: list[tuple[str, dict[str, Any]]],
) -> list[_Render]:
    """Renders the instruction for each state on top of the default state."""
    results = []
    for label, overrides in states:
        try:
            text = template.render(**{**base, **overrides})
        except jinja2.TemplateError as e:
            results.append(_Render(label, error=str(e)))
        else:
            results.append(_Render(label, text=text))
    return results


def _rendered_problems(
    results: list[_Render],
    tools: list[str],
    playbook_tasks: dict[str, tuple[str, ...]],
) -> list[str]:
    """Checks render errors, the playbook and task lists, and hidden tools."""
    problems = []
    hidden_tool_states = {t: [] for t in HIDDEN_TOOLS}
    for r in results:
        if r.text is None:
            problems.append(f"rendering {r.label!r} failed: {r.error}")
            continue
        visible = _visible(r.text, tools)
        for tool in HIDDEN_TOOLS:
            if tool in visible:
                hidden_tool_states[tool].append(r.label)
        if r.label == "start":
            problems += [
                f"playbook {playbook!r} isn't in the playbook list the model"
                " sees at the start"
                for playbook in playbook_tasks
                if not _mentions(r.text, playbook)
            ]
        elif r.label in playbook_tasks:
            problems += [
                f"task {task!r} isn't in the task list the model sees when"
                f" {r.label!r} is open"
                for task in playbook_tasks[r.label]
                if not _mentions(r.text, task)
            ]
    for tool, labels in hidden_tool_states.items():
        if labels:
            problems.append(
                f"{tool} is visible in: {', '.join(labels)}. The model must"
                " never call it directly"
            )
    return problems


def _leftover_problems(app: _App) -> list[str]:
    """Finds registered tools the instruction never names and no code calls."""
    called = set()
    for code in app.path.glob("**/python_code.py"):
        called.update(
            re.findall(r"\btools\.(\w+)\s*\(", code.read_text(encoding="utf-8"))
        )
    return [
        f"tool {tool!r} is registered, but the instruction never names it and"
        " no code calls it"
        for tool in sorted(set(app.tools) - set(HIDDEN_TOOLS) - called)
        if not _mentions(app.without_comments, tool)
    ]


def _state_dependent_tools(app: _App, results: list[_Render]) -> list[str]:
    """Returns tools the instruction names but no rendered state showed."""
    seen = set()
    for r in results:
        if r.text is not None:
            seen.update(_visible(r.text, app.tools))
    return sorted(
        t
        for t in set(app.tools) - seen - set(HIDDEN_TOOLS)
        if _mentions(app.without_comments, t)
    )


def _print_table(results: list[_Render], tools: list[str]) -> None:
    """Prints the word count and the visible tools for each rendered state."""
    width = min(max(len(r.label) for r in results), MAX_LABEL_WIDTH)
    for r in results:
        if r.text is not None:
            print(
                f"{r.label:<{width}}  {len(r.text.split()):4d} words "
                f" {', '.join(_visible(r.text, tools))}"
            )


def _print_note(state_dependent: list[str]) -> None:
    if state_dependent:
        print(
            "\nnote: only visible in branches that depend on other state:"
            f" {', '.join(state_dependent)}. Check them with --state."
        )


def _print_rendered(results: list[_Render], labels: set[str]) -> None:
    """Prints the rendered instruction for each state in `labels`."""
    texts = {
        r.label: r.text
        for r in results
        if r.text is not None and r.label in labels
    }
    for label, text in texts.items():
        squeezed = re.sub(r"(?:[ \t]*\n){2,}", "\n\n", text).strip()
        print(f"\n===== {label}\n{squeezed}")


def _report(problems: list[str]) -> None:
    """Prints the problems and exits with 1, or says there are none."""
    if problems:
        print("\nproblems:")
        for p in dict.fromkeys(problems):
            print(f"  - {p}")
        sys.exit(1)
    print("\nno problems found")


def main() -> None:
    args = _parse_args()
    app = _load_app(args.app_dir)
    playbook_tasks = _literal(app.path / LOAD_TASK_CODE, "PLAYBOOK_TASKS") or {}
    problems = (
        _comment_problems(app)
        + _syntax_problems(app)
        + _router_problems(app, playbook_tasks)
    )
    try:
        template, undeclared = _compile(app)
    except jinja2.TemplateSyntaxError as e:
        print(f"Jinja syntax error on line {e.lineno}: {e.message}")
        sys.exit(1)

    extra = [(_state_label(s), s) for s in args.state]
    states = _default_states(playbook_tasks) + extra
    results = _render(template, _defaults(app.config), states)
    problems += (
        undeclared
        + _rendered_problems(results, app.tools, playbook_tasks)
        + _leftover_problems(app)
    )

    _print_table(results, app.tools)
    _print_note(_state_dependent_tools(app, results))
    if args.print_rendered:
        _print_rendered(results, {label for label, _ in extra or states})
    _report(problems)


if __name__ == "__main__":
    main()
