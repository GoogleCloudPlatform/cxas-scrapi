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
comments kept, non-bare {{ ... }} output expressions left literal) and applies
the same word-boundary match as the dynamic tool-hiding handler. It also checks
for template and tool anti-patterns that `cxas lint` misses.
Needs jinja2 (installed with cxas-scrapi).

Example:
  python3 check_tool_visibility.py cxas_app/my-agent
  python3 check_tool_visibility.py cxas_app/my-agent --print \
    --state \
    '{"current_playbook": "order_support", "current_task": "cancel_order"}'

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
SINGLE_COMMENT = re.compile(r"(?<!\{)\{#.*?#\}(?!\})", re.S)
DOUBLE_BLOCK = re.compile(r"\{\{%(-?)(.*?)(-?)%\}\}", re.S)
SINGLE_BLOCK = re.compile(r"(?<!\{)\{%.*?%\}(?!\})", re.S)
OUTPUT_TAG = re.compile(r"(?<!\{)\{\{(?![%#])(.*?)\}\}(?!\})", re.S)
VALID_OUTPUT_EXPR = re.compile(
    r"^\s*[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\s*$"
    r"|^\s*(?:[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\s*\|\s*)?default\s*\([^)]*\)\s*$",
    re.S,
)
SINGLE_VAR = re.compile(r"(?<!\{)\{([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\}(?!\})")
TAG = re.compile(r"\{\{%.*?%\}\}|(?<!\{)\{%.*?%\}(?!\})|\{\{.*?\}\}", re.S)
PROSE_STATE_CHECK = re.compile(
    r"\b(?:if|when|once|whether|unless)\s+`?([A-Za-z_]\w*)`?\s*"
    r"(?:==|!=|\bis\s+(?:not\s+)?(?:empty|true|false|set|none|null)\b)"
    r"|`([A-Za-z_]\w*)`\s*(?:==|!=)",
    re.I,
)
# Variables CES provides without a declaration.
BUILTIN_VARIABLES = frozenset({"current_date", "_session"})
# Tools the model must never see: built-in end_session and the queue helper.
HIDDEN_TOOLS = ("end_session", "set_next_agent_actions")
PROSE_RETURN_KEYS = frozenset(
    {
        "message",
        "phrase",
        "script",
        "text_to_say",
        "instruction",
        "instructions",
        "next_step_guidance",
        "wrap_up_guidance",
        "anti_herding_rule",
        "pre_transfer_message",
        "prompt",
        "guidance",
    }
)
JINJA_WORDS = frozenset(
    {"in", "not", "and", "or", "is", "if", "elif", "else", "for"}
)
# Asks the model to repeat tool output word for word.
VERBATIM_RESULT = re.compile(
    r"\bexactly as (?:it's |it is )?(?:returned|the tool returns)\b", re.I
)
TOOL_REF = re.compile(r"\{@TOOL:\s*\w+\s*\}")
# State the router tools may write.
ROUTER_STATE_KEYS = frozenset({"current_playbook", "current_task"})
# Static blocks where an always-available tool must not be named.
NO_TOOL_BLOCKS = ("role", "context", "constraints")
OPEN_PLAYBOOK_CODE = "tools/open_playbook/python_function/python_code.py"
LOAD_TASK_CODE = "tools/load_task/python_function/python_code.py"
MAX_LABEL_WIDTH = 48


@dataclasses.dataclass(frozen=True)
class _App:
    """The parts of an app directory that the checks read."""

    path: pathlib.Path
    config: dict[str, Any]
    agent: dict[str, Any]
    tools: list[str]
    instruction: str
    without_comments: str


@dataclasses.dataclass(frozen=True)
class _Render:
    """The instruction rendered for one state, or the error rendering it."""

    label: str
    state: dict[str, Any]
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


def _declared_variables(app: _App) -> set[str]:
    return {v["name"] for v in app.config.get("variableDeclarations", [])}


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
    without_comments = SINGLE_COMMENT.sub("", COMMENT.sub("", instruction))
    return _App(
        path=app_dir,
        config=config,
        agent=agent,
        tools=agent.get("tools", []),
        instruction=instruction,
        without_comments=without_comments,
    )


def _all_tasks(playbook_tasks: dict[str, tuple[str, ...]]) -> set[str]:
    tasks = set()
    for names in playbook_tasks.values():
        tasks.update(names)
    return tasks


def _comment_problems(app: _App) -> list[str]:
    """Finds Jinja comments (unstripped by CES) and tool names inside them."""
    problems = []
    comments = COMMENT.findall(app.instruction) + SINGLE_COMMENT.findall(
        app.instruction
    )
    for comment in comments:
        snippet = " ".join(comment.split())[:60]
        tools_in_comment = _visible(comment, app.tools)
        if tools_in_comment:
            for tool in tools_in_comment:
                problems.append(
                    f"tool name {tool!r} in a Jinja comment (comments are not"
                    f" stripped by CES and reach the model and callback):"
                    f" {snippet!r}"
                )
        else:
            problems.append(
                "Jinja comment is not stripped by CES and reaches the model and"
                f" callback; remove it: {snippet!r}"
            )
    return problems


def _syntax_problems(app: _App) -> list[str]:
    """Finds template syntax and instruction anti-patterns CES mishandles."""
    problems = []
    declared = _declared_variables(app) | BUILTIN_VARIABLES
    for tag in SINGLE_BLOCK.findall(app.without_comments):
        snippet = " ".join(tag.split())
        problems.append(
            f"single-brace block {snippet!r}: write double-brace control blocks"
            " so CES does not add <state_update> to the history"
        )
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
    for match in OUTPUT_TAG.finditer(app.without_comments):
        expr = match.group(1)
        if VALID_OUTPUT_EXPR.match(expr):
            continue
        if any(
            m.group(1) not in JINJA_WORDS
            for m in re.finditer(r"\b([A-Za-z_]\w*)\s*\[", expr)
        ):
            continue
        snippet = " ".join(match.group(0).split())
        problems.append(
            f"output expression {snippet!r}: CES only substitutes bare"
            " variables"
            " (`{{var}}`) in output tags and leaves expressions literal; branch"
            " with `{{% if ... %}}` instead"
        )
    prose = TAG.sub(" ", app.without_comments)
    for match in SINGLE_VAR.finditer(prose):
        expr = match.group(1)
        if expr.split(".")[0] in declared:
            problems.append(
                f"single-brace '{{{expr}}}' is not substituted by CES; write"
                f" '{{{{{{{expr}}}}}}}'"
            )
    if "<session_snapshot" in app.without_comments:
        problems.append(
            "<session_snapshot> state dump in instruction.txt; list only"
            " confirmed"
            " task facts inside the task and branch with `{{% if ... %}}`"
        )
    for match in PROSE_STATE_CHECK.finditer(prose):
        var = match.group(1) or match.group(2)
        if var in declared:
            snippet = " ".join(match.group(0).split())
            problems.append(
                f"prose state check on variable {var!r} ({snippet!r}): branch"
                " with"
                f" `{{{{% if {var} ... %}}}}` instead of asking the model to"
                " evaluate state variables"
            )
    return problems


def _block(text: str, tag: str) -> str:
    """Returns the contents of every <tag>...</tag> block in `text`."""
    return "\n".join(
        re.findall(rf"<{tag}(?:\s[^>]*)?>(.*?)</{tag}>", text, re.S)
    )


def _tool_name_problems(app: _App) -> list[str]:
    """Finds tools named outside {@TOOL: ...} or in the wrong static block."""
    problems = []
    prose = TOOL_REF.sub(" ", TAG.sub(" ", app.without_comments))
    for tool in _visible(prose, app.tools):
        if tool in HIDDEN_TOOLS:
            continue
        problems.append(
            f"tool {tool!r} is named outside {{@TOOL: ...}}, which shows it to"
            " the"
            " model in that branch; refer to an earlier result by what it holds"
            " ('the order you found'), and name a tool only where the model may"
            " call it"
        )
    for tag in NO_TOOL_BLOCKS:
        for tool in _visible(_block(app.without_comments, tag), app.tools):
            problems.append(
                f"tool {tool!r} is named in <{tag}>; put rules for"
                " always-available"
                " tools in <anytime_actions>, so each trigger has one rule"
            )
    for match in VERBATIM_RESULT.finditer(app.without_comments):
        problems.append(
            "instruction asks the model to repeat tool output"
            f" ({match.group(0)!r}); queue exact text with"
            " set_next_agent_actions"
            " from the tool instead"
        )
    return problems


def _static_tool_problems(results: list["_Render"], app: _App) -> list[str]:
    """Checks that only open_playbook and anytime actions show at the start."""
    start = next((r for r in results if r.label == "start" and r.text), None)
    if start is None:
        return []
    allowed = {"open_playbook"} | set(
        _visible(_block(app.without_comments, "anytime_actions"), app.tools)
    )
    return [
        f"tool {tool!r} is visible before any playbook is open, but"
        " <anytime_actions> doesn't name it; only open_playbook and the anytime"
        " actions may be named in static text"
        for tool in _visible(start.text, app.tools)
        if tool not in allowed
    ]


def _router_state_problems(app: _App) -> list[str]:
    """Finds router tools that write domain state or read `rationale`."""
    problems = []
    for code_rel in (OPEN_PLAYBOOK_CODE, LOAD_TASK_CODE):
        path = app.path / code_rel
        if not path.exists():
            continue
        tool_name = path.parent.parent.name
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            key = None
            if isinstance(node, ast.Subscript) and isinstance(
                node.ctx, ast.Store
            ):
                key = node.slice
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("pop", "update", "clear", "setdefault")
                and "state" in ast.unparse(node.func.value)
            ):
                key = node.args[0] if node.args else None
                if node.func.attr in ("update", "clear"):
                    key = None
                    problems.append(
                        f"router tool {tool_name!r} calls"
                        f" state.{node.func.attr}();"
                        " routers set only current_playbook and current_task"
                    )
                    continue
            else:
                if (
                    isinstance(node, ast.Name)
                    and node.id == "rationale"
                    and isinstance(node.ctx, ast.Load)
                ):
                    problems.append(
                        f"router tool {tool_name!r} reads `rationale`; it's"
                        " only a note"
                        " the model leaves in the history"
                    )
                continue
            if (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and key.value not in ROUTER_STATE_KEYS
            ):
                problems.append(
                    f"router tool {tool_name!r} writes state {key.value!r};"
                    " routers set"
                    " only current_playbook and current_task, and the tool that"
                    " changes a record clears what depended on it"
                )
    return problems


def _code_problems(app: _App) -> list[str]:
    """Checks callbacks and tool return dictionaries for anti-patterns."""
    problems = []
    if app.agent.get("afterModelCallbacks"):
        problems.append(
            "root agent has afterModelCallbacks; use a single"
            " before_model_callback and never inspect or rewrite model replies"
            " in"
            " after_model_callback"
        )
    for code_path in sorted(
        app.path.glob("tools/*/python_function/python_code.py")
    ):
        tool_name = code_path.parent.parent.name
        tree = ast.parse(code_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Return)
                and isinstance(node.value, ast.Dict)
                and None in node.value.keys
            ):
                problems.append(
                    f"tool {tool_name!r} spreads a dict into its result"
                    " (`**x`); build"
                    " results from named fields so raw backend or on-file data"
                    " doesn't"
                    " reach the model"
                )
            if not isinstance(node, ast.Dict):
                continue
            for key in node.keys:
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and key.value in PROSE_RETURN_KEYS
                ):
                    problems.append(
                        f"tool {tool_name!r} builds a dict with prose key"
                        f" {key.value!r}; return status codes and data fields"
                        " instead,"
                        " and keep behavior rules in instruction.txt"
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


def _to_jinja(raw: str, preserve_non_bare_output: bool = False) -> str:
    """Converts CES double-brace blocks and preserves unstripped comments."""
    source = DOUBLE_BLOCK.sub(r"{%\1\2\3%}", raw)
    source = COMMENT.sub(
        lambda m: "{% raw %}" + m.group(0) + "{% endraw %}", source
    )
    source = SINGLE_COMMENT.sub(
        lambda m: "{% raw %}" + m.group(0) + "{% endraw %}", source
    )
    if preserve_non_bare_output:
        source = OUTPUT_TAG.sub(
            lambda m: (
                m.group(0)
                if VALID_OUTPUT_EXPR.match(m.group(1))
                else "{% raw %}" + m.group(0) + "{% endraw %}"
            ),
            source,
        )
    return source


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
    parse_source = _to_jinja(app.instruction, preserve_non_bare_output=False)
    declared = _declared_variables(app)
    undeclared = (
        meta.find_undeclared_variables(env.parse(parse_source))
        - declared
        - BUILTIN_VARIABLES
    )
    problems = [
        f"variable {name!r} is used in the instruction but not declared in"
        " app.json, so it always renders empty"
        for name in sorted(undeclared)
    ]
    render_source = _to_jinja(app.instruction, preserve_non_bare_output=True)
    return env.from_string(render_source), problems


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
        full_state = {**base, **overrides}
        try:
            text = template.render(**full_state)
        except jinja2.TemplateError as e:
            results.append(_Render(label, full_state, error=str(e)))
        else:
            results.append(_Render(label, full_state, text=text))
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
        if "open_playbook" not in visible:
            problems.append(
                f"open_playbook isn't visible in {r.label!r}; keep"
                " <playbook_list>"
                " visible in every state so the agent can switch playbooks"
            )
        for playbook in playbook_tasks:
            if not _mentions(r.text, playbook):
                if r.label == "start":
                    problems.append(
                        f"playbook {playbook!r} isn't in the playbook list the"
                        " model sees"
                        " at the start"
                    )
                else:
                    problems.append(
                        f"playbook {playbook!r} isn't in the playbook list the"
                        " model sees"
                        f" in {r.label!r}; keep <playbook_list> visible in"
                        " every state"
                    )
        active_playbook = r.state.get("current_playbook") or ""
        if active_playbook in playbook_tasks:
            if "load_task" in tools and "load_task" not in visible:
                problems.append(
                    f"load_task isn't visible in {r.label!r}; keep <task_list>"
                    " visible whenever a playbook is open"
                )
            for task in playbook_tasks[active_playbook]:
                if not _mentions(r.text, task):
                    if r.label == active_playbook:
                        problems.append(
                            f"task {task!r} isn't in the task list the model"
                            " sees when"
                            f" {r.label!r} is open"
                        )
                    else:
                        problems.append(
                            f"task {task!r} isn't in the task list the model"
                            " sees in"
                            f" {r.label!r}; keep <task_list> visible while a"
                            " task is loaded"
                        )
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
        text = code.read_text(encoding="utf-8")
        called.update(re.findall(r"\btools\.(\w+)\s*\(", text))
        called.update(
            re.findall(
                r"from_function_call\(\s*name\s*=\s*[\"'](\w+)[\"']", text
            )
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
        + _tool_name_problems(app)
        + _code_problems(app)
        + _router_problems(app, playbook_tasks)
        + _router_state_problems(app)
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
        + _static_tool_problems(results, app)
        + _leftover_problems(app)
    )

    _print_table(results, app.tools)
    _print_note(_state_dependent_tools(app, results))
    if args.print_rendered:
        _print_rendered(results, {label for label, _ in extra or states})
    _report(problems)


if __name__ == "__main__":
    main()
