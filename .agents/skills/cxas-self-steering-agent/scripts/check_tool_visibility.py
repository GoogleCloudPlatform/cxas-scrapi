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

r"""Lists tools the model sees for each domain/playbook and workflow/task.

Renders agents/<root>/instruction.txt the way CES does (Jinja rendered, {{# #}}
comments kept, non-bare {{ ... }} output expressions left literal) and applies
the same word-boundary match as the dynamic tool-hiding handler. Automatically
detects and validates all three self-steering topologies:
  1. 2-level `select_domain` + `load_workflow` (`<domain_list>`,
     `<workflow_list>`)
  2. 1-level `load_workflow` (`--single-level`, `<workflow_list>` always shown)
  3. Legacy 2-level `open_playbook` + `load_task` (`<playbook_list>`,
     `<task_list>`)

It also checks for template, instruction, and tool anti-patterns that `cxas
lint` misses, and automatically synthesizes branch states from
`{{% if ... %}} / {{% elif ... %}}` conditions so multi-step `<current_step>`
and `<pending_handoff>` branches are verified without manual `--state` flags.
Needs jinja2 (installed with cxas-scrapi).

Example:
  python3 check_tool_visibility.py cxas_app/my-agent
  python3 check_tool_visibility.py cxas_app/my-agent --print \
    --state '{"current_domain": "live_agent", "current_workflow": "live_agent"}'

Exits with 1 when it finds a problem.
"""

import argparse
import ast
import copy
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
    r"^\s*[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\s*$",
    re.S,
)
SINGLE_VAR = re.compile(r"(?<!\{)\{([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\}(?!\})")
TAG = re.compile(r"\{\{%.*?%\}\}|(?<!\{)\{%.*?%\}(?!\})|\{\{.*?\}\}", re.S)
IDENT_PATTERN = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*"
PROSE_STATE_CHECK = re.compile(
    r"\b(?:if|when|once|whether|unless)\s+`?([A-Za-z_]\w*(?:\.\w+)*)`?\s*"
    r"(?:==|!=|\bis\s+(?:not\s+)?(?:empty|true|false|set|none|null)\b)"
    r"|`([A-Za-z_]\w*(?:\.\w+)*)`\s*(?:==|!=)",
    re.I,
)
PROSE_PRONOUNS = frozenset(
    {
        "it",
        "that",
        "this",
        "there",
        "nothing",
        "anything",
        "something",
        "one",
        "none",
    }
)
SAMPLE_CODE_PATTERN = re.compile(
    r"\b(?:such\s+as|for\s+example|e\.g\.?|example\s*:)[,:]?\s*"
    r"[`'\"]?([A-Z]{2,6}-?\d{3,10}|\d{3,6}-[A-Z]{2,6})[`'\"]?"
)
TOOL_REF_PATTERN = re.compile(r"\{@TOOL:\s*([A-Za-z_]\w*)\s*\}")
# Variables CES provides without a declaration.
BUILTIN_VARIABLES = frozenset({"current_date", "_session"})
# Tools the model must never see: built-in end_session and the queue helper.
HIDDEN_TOOLS = ("end_session", "set_next_agent_actions")
ROUTING_VARS = (
    "current_domain",
    "current_playbook",
    "current_workflow",
    "current_task",
)
JINJA_WORDS = frozenset(
    {
        "in",
        "not",
        "and",
        "or",
        "is",
        "if",
        "elif",
        "else",
        "for",
        "true",
        "false",
        "none",
        "True",
        "False",
        "None",
    }
)
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
# Asks the model to repeat tool output word for word.
VERBATIM_RESULT = re.compile(
    r"\bexactly as (?:it's |it is )?(?:returned|the tool returns)\b", re.I
)
QUOTED = re.compile(r"['\"]([^'\"]+)['\"]")
# Static blocks where an always-available tool must not be named.
NO_TOOL_BLOCKS = ("role", "context", "constraints")
SELECT_DOMAIN_CODE = "tools/select_domain/python_function/python_code.py"
LOAD_WORKFLOW_CODE = "tools/load_workflow/python_function/python_code.py"
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
class _Topology:
    """Describes the routing topology of the self-steering agent."""

    mode: str  # "domain_workflow", "single_workflow", or "playbook_task"
    outer_tool: str | None
    inner_tool: str
    outer_var: str | None
    inner_var: str
    outer_tag: str | None
    inner_tag: str
    outer_noun: str | None
    inner_noun: str
    outer_code: str | None
    inner_code: str
    outer_const: str | None
    inner_const: str
    router_state_keys: frozenset[str]


@dataclasses.dataclass(frozen=True)
class _Render:
    """The instruction rendered for one state, or the error rendering it."""

    label: str
    state: dict[str, Any]
    text: str | None = None
    error: str | None = None


DOMAIN_WORKFLOW_TOPOLOGY = _Topology(
    mode="domain_workflow",
    outer_tool="select_domain",
    inner_tool="load_workflow",
    outer_var="current_domain",
    inner_var="current_workflow",
    outer_tag="<domain_list>",
    inner_tag="<workflow_list>",
    outer_noun="domain",
    inner_noun="workflow",
    outer_code=SELECT_DOMAIN_CODE,
    inner_code=LOAD_WORKFLOW_CODE,
    outer_const="VALID_DOMAINS",
    inner_const="DOMAIN_WORKFLOWS",
    router_state_keys=frozenset({"current_domain", "current_workflow"}),
)

SINGLE_WORKFLOW_TOPOLOGY = _Topology(
    mode="single_workflow",
    outer_tool=None,
    inner_tool="load_workflow",
    outer_var=None,
    inner_var="current_workflow",
    outer_tag=None,
    inner_tag="<workflow_list>",
    outer_noun=None,
    inner_noun="workflow",
    outer_code=None,
    inner_code=LOAD_WORKFLOW_CODE,
    outer_const=None,
    inner_const="VALID_WORKFLOWS",
    router_state_keys=frozenset({"current_workflow"}),
)

PLAYBOOK_TASK_TOPOLOGY = _Topology(
    mode="playbook_task",
    outer_tool="open_playbook",
    inner_tool="load_task",
    outer_var="current_playbook",
    inner_var="current_task",
    outer_tag="<playbook_list>",
    inner_tag="<task_list>",
    outer_noun="playbook",
    inner_noun="task",
    outer_code=OPEN_PLAYBOOK_CODE,
    inner_code=LOAD_TASK_CODE,
    outer_const="VALID_PLAYBOOKS",
    inner_const="PLAYBOOK_TASKS",
    router_state_keys=frozenset({"current_playbook", "current_task"}),
)


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


def _empty(schema: dict[str, Any]) -> Any:
    """Returns a variable's declared default, or an empty value of its type."""
    if "default" in schema:
        return copy.deepcopy(schema["default"])
    kind = schema.get("type")
    if kind == "OBJECT":
        return {
            k: _empty(v) for k, v in (schema.get("properties") or {}).items()
        }
    if kind == "ARRAY":
        return []
    return {"STRING": "", "BOOLEAN": False, "NUMBER": 0, "INTEGER": 0}.get(kind)


def _defaults(app: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for var in app.get("variableDeclarations", []):
        out[var["name"]] = _empty(var.get("schema", {}))
    tz = app.get("timeZoneSettings", {}).get("timeZone", "UTC")
    out.setdefault("current_date", f"{datetime.date.today().isoformat()}[{tz}]")
    return out


def _var_schemas(app: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        var["name"]: var.get("schema", {})
        for var in app.get("variableDeclarations", [])
    }


def _declared_variables(app: _App) -> set[str]:
    return {v["name"] for v in app.config.get("variableDeclarations", [])}


def _object_schemas(app: _App) -> dict[str, dict[str, Any]]:
    """Returns the schema of each declared OBJECT variable."""
    return {
        v["name"]: v.get("schema", {})
        for v in app.config.get("variableDeclarations", [])
        if v.get("schema", {}).get("type") == "OBJECT"
    }


def _root_var(path: str) -> str:
    return path.split(".", maxsplit=1)[0]


def _mentions(text: str, name: str) -> bool:
    return re.search(rf"\b{re.escape(name)}\b", text) is not None


def _visible(text: str, tools: list[str]) -> list[str]:
    return sorted(t for t in tools if _mentions(text, t))


def _block(text: str, tag: str) -> str:
    """Returns the contents of every <tag>...</tag> block in `text`."""
    return "\n".join(
        re.findall(rf"<{tag}(?:\s[^>]*)?>(.*?)</{tag}>", text, re.S)
    )


def _compared_names(source: str, var: str) -> set[str]:
    """Names the template compares `var` with (== 'x', or in ['x', 'y'])."""
    names = set(re.findall(rf"\b{var}\s*==\s*['\"]([^'\"]+)['\"]", source))
    for group in re.findall(rf"\b{var}\s+in\s+[\[(]([^\])]*)[\])]", source):
        names.update(re.findall(r"['\"]([^'\"]+)['\"]", group))
    return names


def _format_override_value(val: Any) -> str:
    if isinstance(val, dict):
        non_empty = {
            k: _format_override_value(sv)
            for k, sv in val.items()
            if sv not in ("", False, 0, None, [], {})
        }
        if non_empty:
            inner = ", ".join(f"{k}={sv}" for k, sv in non_empty.items())
            return f"{{{inner}}}"
        return "{}"
    return str(val)


def _state_label(overrides: dict[str, Any]) -> str:
    """Returns a label like "live_agent / live_agent_escalation (...)"."""
    label = (
        " / ".join(str(overrides[k]) for k in ROUTING_VARS if overrides.get(k))
        or "start"
    )
    rest = ", ".join(
        f"{k}={_format_override_value(v)}"
        for k, v in overrides.items()
        if k not in ROUTING_VARS
    )
    return f"{label} ({rest})" if rest else label


def _detect_topology(app: _App) -> _Topology:
    """Detects 2-level domain/workflow, 1-level workflow, or playbook/task."""
    declared = _declared_variables(app)
    tools_set = set(app.tools)
    has_workflow_markers = (
        "select_domain" in tools_set
        or "load_workflow" in tools_set
        or "current_domain" in declared
        or "current_workflow" in declared
        or "<domain_list>" in app.without_comments
        or "<workflow_list>" in app.without_comments
        or (app.path / SELECT_DOMAIN_CODE).exists()
        or (app.path / LOAD_WORKFLOW_CODE).exists()
    )
    if has_workflow_markers:
        is_two_level = (
            "select_domain" in tools_set
            or (app.path / SELECT_DOMAIN_CODE).exists()
            or "current_domain" in declared
            or "<domain_list>" in app.without_comments
            or _mentions(app.without_comments, "current_domain")
            or _literal(app.path / LOAD_WORKFLOW_CODE, "DOMAIN_WORKFLOWS")
            is not None
        )
        return (
            DOMAIN_WORKFLOW_TOPOLOGY
            if is_two_level
            else SINGLE_WORKFLOW_TOPOLOGY
        )
    has_playbook_markers = (
        "open_playbook" in tools_set
        or "load_task" in tools_set
        or "current_playbook" in declared
        or "current_task" in declared
        or "<playbook_list>" in app.without_comments
        or "<task_list>" in app.without_comments
        or (app.path / OPEN_PLAYBOOK_CODE).exists()
        or (app.path / LOAD_TASK_CODE).exists()
    )
    if has_playbook_markers:
        return PLAYBOOK_TASK_TOPOLOGY
    return DOMAIN_WORKFLOW_TOPOLOGY


def _load_routing_map(app: _App, topo: _Topology) -> dict[str, tuple[str, ...]]:
    """Loads outer->inner routing map (or {"": workflows} in 1-level)."""
    raw = _literal(app.path / topo.inner_code, topo.inner_const)
    if topo.mode == "single_workflow":
        if isinstance(raw, (list, tuple)):
            return {"": tuple(str(x) for x in raw)}
        return {}
    if isinstance(raw, dict):
        return {str(k): tuple(str(x) for x in v) for k, v in raw.items()}
    return {}


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
            "Extra state to render, as a JSON object. Include current_domain"
            " and current_workflow (or current_playbook and current_task)."
            " Repeatable."
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


def _all_inners(mapping: dict[str, tuple[str, ...]]) -> set[str]:
    inners: set[str] = set()
    for names in mapping.values():
        inners.update(names)
    return inners


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


def _instruction_problems(app: _App) -> list[str]:
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
            " variables (`{{var}}`) in output tags and leaves expressions"
            " literal; branch with `{{% if ... %}}` instead"
        )
    prose = TAG.sub(" ", app.without_comments)
    for match in SINGLE_VAR.finditer(prose):
        expr = match.group(1)
        if _root_var(expr) in declared or "_" in expr:
            problems.append(
                f"single-brace '{{{expr}}}' is not substituted by CES; write"
                f" '{{{{{{{expr}}}}}}}'"
            )
    if "<session_snapshot" in app.without_comments:
        problems.append(
            "<session_snapshot> state dump in instruction.txt; list only"
            " confirmed facts inside the workflow/task and branch with"
            " `{{% if ... %}}`"
        )
    for match in PROSE_STATE_CHECK.finditer(prose):
        var = match.group(1) or match.group(2)
        if (
            var
            and var.lower() not in PROSE_PRONOUNS
            and (_root_var(var) in declared or var.islower())
        ):
            snippet = " ".join(match.group(0).split())
            problems.append(
                f"prose state check on variable {var!r} ({snippet!r}): branch"
                f" with `{{{{% if {var} ... %}}}}` instead of asking the model"
                " to evaluate state variables"
            )
    for match in SAMPLE_CODE_PATTERN.finditer(prose):
        code = match.group(1)
        snippet = " ".join(match.group(0).split())
        problems.append(
            f"hardcoded sample confirmation code {code!r} in instruction.txt"
            f" ({snippet!r}); describe the format in words so the model cannot"
            " read a sample code aloud as real data"
        )
    for match in TOOL_REF_PATTERN.finditer(app.without_comments):
        tool_name = match.group(1)
        if tool_name not in app.tools:
            problems.append(
                f"instruction references {{@TOOL: {tool_name}}}, which is not"
                " in the root agent's tools list"
            )
    if _mentions(app.without_comments, "execute_live_agent_handover") and not (
        _mentions(app.without_comments, "reason")
        or _mentions(app.without_comments, "pending_handoff_reason")
    ):
        problems.append(
            "instruction.txt calls execute_live_agent_handover without"
            " specifying a handoff reason or pending_handoff_reason"
        )
    return problems


def _nested_property_problems(app: _App) -> list[str]:
    """Finds instruction refs to properties an OBJECT variable doesn't declare.

    An undeclared property always renders empty, which silently flips the
    branch it's in.
    """
    problems = []
    objects = _object_schemas(app)
    for tag in TAG.findall(app.without_comments):
        body = QUOTED.sub(" ", tag)
        for head, child in re.findall(r"(?<![\w.])(\w+)\.(\w+)", body):
            props = objects.get(head, {}).get("properties")
            if props and child not in props:
                problems.append(
                    f"{head}.{child} isn't a declared property of {head!r}"
                    f" (declared: {', '.join(sorted(props))}); it always"
                    " renders empty"
                )
    return problems


def _tool_name_problems(app: _App) -> list[str]:
    """Finds tools named outside {@TOOL: ...} or in the wrong static block."""
    problems = []
    prose = TOOL_REF_PATTERN.sub(" ", TAG.sub(" ", app.without_comments))
    for tool in _visible(prose, app.tools):
        if tool in HIDDEN_TOOLS:
            continue
        problems.append(
            f"tool {tool!r} is named outside {{@TOOL: ...}}, which shows it to"
            " the model in that branch; refer to an earlier result by what it"
            " holds ('the record you found'), and name a tool only where the"
            " model may call it"
        )
    for tag in NO_TOOL_BLOCKS:
        for tool in _visible(_block(app.without_comments, tag), app.tools):
            problems.append(
                f"tool {tool!r} is named in <{tag}>; put rules for"
                " always-available tools in <anytime_actions>, so each"
                " trigger has one rule"
            )
    for match in VERBATIM_RESULT.finditer(app.without_comments):
        problems.append(
            "instruction asks the model to repeat tool output"
            f" ({match.group(0)!r}); queue exact text with"
            " next_agent_actions from the tool instead"
        )
    return problems


def _analyze_returned_dicts(func: ast.AST) -> tuple[set[str], bool]:
    """Returns `(returned_keys, has_returned_spread)` for `func`."""
    var_keys: dict[str, set[str]] = {}
    var_spreads: set[str] = set()

    def _keys_from_expr(expr: ast.AST | None) -> set[str]:
        if expr is None:
            return set()
        if isinstance(expr, ast.Dict):
            return {
                k.value
                for k in expr.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
        if (
            isinstance(expr, ast.Call)
            and isinstance(expr.func, ast.Name)
            and expr.func.id == "dict"
        ):
            return {kw.arg for kw in expr.keywords if kw.arg is not None}
        if isinstance(expr, ast.IfExp):
            return _keys_from_expr(expr.body) | _keys_from_expr(expr.orelse)
        if isinstance(expr, ast.BoolOp):
            out: set[str] = set()
            for val in expr.values:
                out |= _keys_from_expr(val)
            return out
        if isinstance(expr, ast.Name):
            return set(var_keys.get(expr.id, set()))
        return set()

    def _has_spread(expr: ast.AST | None) -> bool:
        if expr is None:
            return False
        if isinstance(expr, ast.Dict):
            return None in expr.keys
        if isinstance(expr, ast.IfExp):
            return _has_spread(expr.body) or _has_spread(expr.orelse)
        if isinstance(expr, ast.BoolOp):
            return any(_has_spread(val) for val in expr.values)
        if isinstance(expr, ast.Name):
            return expr.id in var_spreads
        return False

    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            expr_keys = _keys_from_expr(node.value)
            expr_spread = _has_spread(node.value)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if expr_keys:
                        var_keys.setdefault(target.id, set()).update(expr_keys)
                    if expr_spread:
                        var_spreads.add(target.id)
                elif (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)
                ):
                    var_keys.setdefault(target.value.id, set()).add(
                        target.slice.value
                    )
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            expr_keys = _keys_from_expr(node.value)
            if expr_keys:
                var_keys.setdefault(node.target.id, set()).update(expr_keys)
            if _has_spread(node.value):
                var_spreads.add(node.target.id)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in var_keys
        ):
            target_id = node.func.value.id
            if node.func.attr == "setdefault" and node.args:
                arg0 = node.args[0]
                if isinstance(arg0, ast.Constant) and isinstance(
                    arg0.value, str
                ):
                    var_keys[target_id].add(arg0.value)
            elif node.func.attr == "update" and node.args:
                arg0 = node.args[0]
                var_keys[target_id].update(_keys_from_expr(arg0))
                if _has_spread(arg0):
                    var_spreads.add(target_id)

    returned_keys: set[str] = set()
    has_returned_spread = False
    for node in ast.walk(func):
        if isinstance(node, ast.Return):
            returned_keys |= _keys_from_expr(node.value)
            if _has_spread(node.value):
                has_returned_spread = True
    return returned_keys, has_returned_spread


def _returned_dict_keys(func: ast.AST) -> set[str]:
    """Returns string keys present in dictionaries returned by `func`."""
    returned_keys, _ = _analyze_returned_dicts(func)
    return returned_keys


def _code_problems(app: _App) -> list[str]:
    """Checks callbacks, tool docstrings, and tool return dictionaries."""
    problems = []
    if app.agent.get("afterModelCallbacks"):
        problems.append(
            "root agent has afterModelCallbacks; use a single"
            " before_model_callback and never inspect or rewrite model replies"
            " in after_model_callback"
        )
    for code_path in sorted(
        app.path.glob("tools/*/python_function/python_code.py")
    ):
        tool_name = code_path.parent.parent.name
        source = code_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        funcs = [
            n
            for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        found_spread = False
        for func in funcs:
            doc = ast.get_docstring(func) or ""
            for match in SAMPLE_CODE_PATTERN.finditer(doc):
                code = match.group(1)
                snippet = " ".join(match.group(0).split())
                problems.append(
                    f"tool {tool_name!r} docstring has hardcoded sample"
                    f" confirmation code {code!r} ({snippet!r}); describe the"
                    " format in words"
                )
            returned_keys, has_spread = _analyze_returned_dicts(func)
            if has_spread and not found_spread:
                found_spread = True
                problems.append(
                    f"tool {tool_name!r} spreads a dict into its result"
                    " (`**x`); build results from named fields so raw backend"
                    " or on-file data doesn't reach the model"
                )
            for key in sorted(returned_keys & PROSE_RETURN_KEYS):
                problems.append(
                    f"tool {tool_name!r} builds a dict with prose key"
                    f" {key!r}; return status codes and data fields instead,"
                    " and keep behavior rules in instruction.txt"
                )
        if tool_name == "execute_live_agent_handover" and funcs:
            entry = next((f for f in funcs if f.name == tool_name), funcs[0])
            arg_names = {a.arg for a in entry.args.args + entry.args.kwonlyargs}
            has_reason_arg = "reason" in arg_names
            uses_reason = (
                "reason" in source or "pending_handoff_reason" in source
            )
            has_end_session_reason = bool(
                re.search(r"['\"]reason['\"]\s*:", source)
            )
            if not (has_reason_arg and uses_reason and has_end_session_reason):
                problems.append(
                    "execute_live_agent_handover is missing a handoff reason;"
                    " accept `reason` (and check `pending_handoff_reason`) and"
                    ' pass `"reason"` in `end_session`'
                )
    return problems


def _router_problems(
    app: _App,
    topo: _Topology,
    mapping: dict[str, tuple[str, ...]],
) -> list[str]:
    """Checks the router tools' lists against each other and the instruction."""
    problems = []
    if topo.mode == "single_workflow":
        if topo.inner_tool not in app.tools:
            problems.append(
                f"{topo.inner_tool} isn't in the root agent's tools; every"
                f" single-level agent needs {topo.inner_tool}"
            )
        if not mapping:
            problems.append(f"no {topo.inner_const} found in {topo.inner_code}")
        inners = _all_inners(mapping)
        instr_inners = _compared_names(app.without_comments, topo.inner_var)
        if instr_inners != inners:
            problems.append(
                f"{topo.inner_noun}s in instruction {sorted(instr_inners)} !="
                f" {topo.inner_const} {sorted(inners)}"
            )
        for name in sorted(inners & set(app.tools)):
            problems.append(
                f"{name!r} is both a {topo.inner_noun} and a tool, so the tool"
                " shows whenever that name is listed"
            )
        return problems

    assert topo.outer_code is not None
    assert topo.outer_const is not None
    assert topo.outer_tool is not None
    assert topo.outer_var is not None
    assert topo.outer_noun is not None
    valid_outers = _literal(app.path / topo.outer_code, topo.outer_const)
    if valid_outers is None:
        problems.append(f"no {topo.outer_const} found in {topo.outer_code}")
    if topo.outer_tool not in app.tools:
        problems.append(
            f"{topo.outer_tool} isn't in the root agent's tools; every agent"
            f" needs {topo.outer_tool} and {topo.inner_tool}"
        )
    if topo.inner_tool not in app.tools:
        problems.append(
            f"{topo.inner_tool} isn't in the root agent's tools; every agent"
            f" needs {topo.outer_tool} and {topo.inner_tool}"
        )
    if not mapping:
        problems.append(f"no {topo.inner_const} found in {topo.inner_code}")
    outers = set(mapping)
    inners = _all_inners(mapping)
    instr_outers = _compared_names(app.without_comments, topo.outer_var)
    instr_inners = _compared_names(app.without_comments, topo.inner_var)
    if valid_outers is not None and set(valid_outers) != outers:
        problems.append(
            f"{topo.outer_const} {sorted(valid_outers)} != {topo.inner_const}"
            f" keys {sorted(outers)}"
        )
    if instr_outers != outers:
        problems.append(
            f"{topo.outer_noun}s in instruction {sorted(instr_outers)} !="
            f" {topo.inner_const} keys {sorted(outers)}"
        )
    if instr_inners != inners:
        problems.append(
            f"{topo.inner_noun}s in instruction {sorted(instr_inners)} !="
            f" {topo.inner_const} {topo.inner_noun}s {sorted(inners)}"
        )
    for name in sorted((outers | inners) & set(app.tools)):
        problems.append(
            f"{name!r} is both a {topo.outer_noun} or {topo.inner_noun} and a"
            " tool, so the tool shows whenever that name is listed"
        )
    return problems


def _router_state_problems(app: _App, topo: _Topology) -> list[str]:
    """Finds router tools that write domain state or read `rationale`."""
    problems = []
    router_codes = [c for c in (topo.outer_code, topo.inner_code) if c]
    allowed_desc = (
        f"{topo.outer_var} and {topo.inner_var}"
        if topo.outer_var
        else topo.inner_var
    )
    for code_rel in router_codes:
        path = app.path / code_rel
        if not path.exists():
            continue
        tool_name = path.parent.parent.name
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            key = None
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.ctx, (ast.Store, ast.Del))
                and "state" in ast.unparse(node.value)
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
                    problems.append(
                        f"router tool {tool_name!r} calls"
                        f" state.{node.func.attr}(); routers set only"
                        f" {allowed_desc}"
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
                        " only a note the model leaves in the history"
                    )
                continue
            if (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and key.value not in topo.router_state_keys
            ):
                problems.append(
                    f"router tool {tool_name!r} writes state {key.value!r};"
                    f" routers set only {allowed_desc}, and the tool that"
                    " changes a record clears what depended on it"
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
    topo: _Topology,
    mapping: dict[str, tuple[str, ...]],
) -> list[tuple[str, dict[str, Any]]]:
    """Returns the start state, then each domain/playbook and workflow/task."""
    states: list[tuple[str, dict[str, Any]]] = [("start", {})]
    if topo.mode == "single_workflow":
        for wf in mapping.get("", ()):
            states.append((wf, {topo.inner_var: wf}))
        return states
    assert topo.outer_var is not None
    for outer, inners in mapping.items():
        states.append((outer, {topo.outer_var: outer}))
        states += [
            (
                f"{outer} / {inner}",
                {topo.outer_var: outer, topo.inner_var: inner},
            )
            for inner in inners
        ]
    return states


def _field_type(path: str, var_schemas: dict[str, dict[str, Any]]) -> str:
    parts = path.split(".")
    schema = var_schemas.get(parts[0], {})
    for part in parts[1:]:
        schema = (schema.get("properties") or {}).get(part, {})
    return str(schema.get("type", "STRING"))


def _truthy_value(path: str, var_schemas: dict[str, dict[str, Any]]) -> Any:
    vtype = _field_type(path, var_schemas)
    if vtype == "BOOLEAN":
        return True
    if vtype in ("NUMBER", "INTEGER"):
        return 1
    if vtype == "ARRAY":
        return ["1"]
    if vtype == "OBJECT":
        schema = var_schemas.get(_root_var(path), {})
        obj = _empty(schema)
        if isinstance(obj, dict) and obj:
            return obj
        return {"_set": True}
    return "1"


def _falsy_value(path: str, var_schemas: dict[str, dict[str, Any]]) -> Any:
    vtype = _field_type(path, var_schemas)
    if vtype == "BOOLEAN":
        return False
    if vtype in ("NUMBER", "INTEGER"):
        return 0
    if vtype == "ARRAY":
        return []
    if vtype == "OBJECT":
        return {}
    return ""


def _set_nested(
    out: dict[str, Any],
    path: str,
    value: Any,
    var_schemas: dict[str, dict[str, Any]],
) -> None:
    """Writes `value` at a flat or dotted path, initializing OBJECT schemas."""
    parts = path.split(".")
    if len(parts) == 1:
        out[parts[0]] = value
        return
    root = parts[0]
    if root not in out or not isinstance(out[root], dict):
        schema = var_schemas.get(root, {})
        default_obj = _empty(schema) if schema.get("type") == "OBJECT" else {}
        out[root] = (
            copy.deepcopy(default_obj) if isinstance(default_obj, dict) else {}
        )
    cur: dict[str, Any] = out[root]
    for part in parts[1:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _get_nested(out: dict[str, Any], path: str) -> tuple[bool, Any]:
    parts = path.split(".")
    cur: Any = out
    for part in parts:
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur


def _merge_states(*dicts: dict[str, Any]) -> dict[str, Any]:
    """Deep-merges state dictionaries so OBJECT subkeys are preserved."""
    merged: dict[str, Any] = {}
    for d in dicts:
        for k, v in d.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k] = _merge_states(merged[k], v)
            else:
                merged[k] = copy.deepcopy(v)
    return merged


def _strip_parens(text: str) -> str:
    s = text.strip()
    while s.startswith("(") and s.endswith(")"):
        depth = 0
        balanced = True
        for i, ch in enumerate(s):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and i < len(s) - 1:
                    balanced = False
                    break
        if not balanced or depth != 0:
            break
        s = s[1:-1].strip()
    return s


def _satisfy_condition(
    cond: str,
    target_true: bool,
    var_schemas: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Synthesizes variable assignments to satisfy/falsify a Jinja condition."""
    out: dict[str, Any] = {}
    parts = re.split(r"\b(?:and|or)\b", cond)
    for raw_part in parts:
        part = _strip_parens(raw_part)
        if not part:
            continue
        eq_match = re.match(
            rf"^(not\s+)?({IDENT_PATTERN})\s*(==|!=)\s*['\"]([^'\"]*)['\"]$",
            part,
        )
        if eq_match:
            neg, var, op, val = eq_match.groups()
            if _root_var(var) in ROUTING_VARS:
                continue
            want_equal = (op == "==") ^ bool(neg) ^ (not target_true)
            assigned = val if want_equal else (f"not_{val}" if val else "1")
            _set_nested(out, var, assigned, var_schemas)
            continue
        num_match = re.match(
            rf"^(not\s+)?({IDENT_PATTERN})\s*(==|!=)\s*(-?\d+)$",
            part,
        )
        if num_match:
            neg, var, op, raw_num = num_match.groups()
            if _root_var(var) in ROUTING_VARS:
                continue
            want_equal = (op == "==") ^ bool(neg) ^ (not target_true)
            num_val = int(raw_num)
            _set_nested(
                out,
                var,
                num_val if want_equal else num_val + 1,
                var_schemas,
            )
            continue
        var_eq_match = re.match(
            rf"^(not\s+)?({IDENT_PATTERN})\s*(==|!=)\s*({IDENT_PATTERN})$",
            part,
        )
        if var_eq_match:
            neg, var, op, rhs = var_eq_match.groups()
            if _root_var(var) in ROUTING_VARS:
                continue
            want_equal = (op == "==") ^ bool(neg) ^ (not target_true)
            if rhs in ("true", "True", "false", "False"):
                bool_val = rhs.lower() == "true"
                _set_nested(
                    out,
                    var,
                    bool_val if want_equal else not bool_val,
                    var_schemas,
                )
                continue
            if rhs in JINJA_WORDS or _root_var(rhs) in ROUTING_VARS:
                continue
            found_rhs, existing_rhs = _get_nested(out, rhs)
            if not found_rhs:
                rhs_val: Any = "1"
                _set_nested(out, rhs, rhs_val, var_schemas)
            else:
                rhs_val = existing_rhs
            if want_equal:
                _set_nested(out, var, rhs_val, var_schemas)
            else:
                neq_val = "2" if rhs_val == "1" else f"not_{rhs_val}"
                _set_nested(out, var, neq_val, var_schemas)
            continue
        in_match = re.match(
            rf"^(not\s+)?({IDENT_PATTERN})\s+in\s+[\[(]([^\])]*)[\])]$",
            part,
        )
        if in_match:
            neg, var, items_str = in_match.groups()
            if _root_var(var) in ROUTING_VARS:
                continue
            vals = re.findall(r"['\"]([^'\"]+)['\"]", items_str)
            if vals:
                want_in = bool(target_true) ^ bool(neg)
                _set_nested(
                    out,
                    var,
                    vals[0] if want_in else "__other__",
                    var_schemas,
                )
            continue
        bool_match = re.match(rf"^(not\s+)?({IDENT_PATTERN})$", part)
        if bool_match:
            neg, var = bool_match.groups()
            if var in JINJA_WORDS or _root_var(var) in ROUTING_VARS:
                continue
            want_truthy = bool(target_true) ^ bool(neg)
            _set_nested(
                out,
                var,
                _truthy_value(var, var_schemas)
                if want_truthy
                else _falsy_value(var, var_schemas),
                var_schemas,
            )
    return out


def _synthesize_branch_states(
    app: _App,
    topo: _Topology,
    mapping: dict[str, tuple[str, ...]],
) -> list[tuple[str, dict[str, Any]]]:
    """Synthesizes states for `if/elif/else` branches in instruction.txt."""
    var_schemas = _var_schemas(app.config)
    base_defaults = _defaults(app.config)
    inner_to_outer = {
        inner: outer for outer, inners in mapping.items() for inner in inners
    }
    synthesized: list[tuple[str, dict[str, Any]]] = []
    seen_states: set[str] = set()

    def _add_state(state: dict[str, Any]) -> None:
        if not state:
            return
        key = json.dumps(state, sort_keys=True)
        if key not in seen_states:
            seen_states.add(key)
            synthesized.append((_state_label(state), state))

    if "pending_handoff_reason" in var_schemas or _mentions(
        app.without_comments, "pending_handoff_reason"
    ):
        _add_state({"pending_handoff_reason": "verification_failed"})

    # Split instruction into workflow/task sections by `<inner_var> == '...'`.
    inner_re = re.compile(
        rf"\{{\{{%-?\s*(if|elif)\s+{re.escape(topo.inner_var)}"
        r"\s*==\s*['\"]([^'\"]+)['\"]\s*-?%\}\}"
    )
    sections: list[tuple[str | None, str, str]] = []
    pos_idx = 0
    prev_kw = "if"
    prev_inner: str | None = None
    for match in inner_re.finditer(app.without_comments):
        sections.append(
            (prev_inner, prev_kw, app.without_comments[pos_idx : match.start()])
        )
        prev_kw, prev_inner = match.group(1), match.group(2)
        pos_idx = match.end()
    sections.append((prev_inner, prev_kw, app.without_comments[pos_idx:]))

    block_re = re.compile(r"\{\{%-?\s*(if|elif|else|endif)\b(.*?)-?%\}\}", re.S)
    outer_neg: dict[str, Any] = {}
    for inner_name, open_kw, body in sections:
        base_ctx: dict[str, Any] = {}
        if inner_name and inner_name in inner_to_outer:
            if topo.mode == "single_workflow" or topo.outer_var is None:
                base_ctx = {topo.inner_var: inner_name}
            else:
                base_ctx = {
                    topo.outer_var: inner_to_outer[inner_name],
                    topo.inner_var: inner_name,
                }
            if open_kw == "elif" and outer_neg:
                base_ctx = _merge_states(outer_neg, base_ctx)
                _add_state(base_ctx)
        elif inner_name:
            continue

        stack: list[dict[str, Any]] = []
        for match in block_re.finditer(body):
            kw, cond = match.group(1), match.group(2).strip()
            if kw == "if":
                pos = _satisfy_condition(cond, True, var_schemas)
                neg = _satisfy_condition(cond, False, var_schemas)
                parent_neg = copy.deepcopy(stack[-1]) if stack else {}
                outer_ctxs = (
                    [
                        {topo.outer_var: out}
                        for out in _compared_names(cond, topo.outer_var)
                        if out in mapping
                    ]
                    if not base_ctx and topo.outer_var
                    else []
                )
                if pos:
                    _add_state(_merge_states(base_ctx, parent_neg, pos))
                    if base_ctx:
                        _add_state(_merge_states(base_ctx, pos))
                    else:
                        for octx in outer_ctxs:
                            _add_state(_merge_states(octx, parent_neg, pos))
                stack.append(_merge_states(parent_neg, neg))
            elif kw == "elif":
                neg_so_far = copy.deepcopy(stack[-1]) if stack else {}
                pos = _satisfy_condition(cond, True, var_schemas)
                neg = _satisfy_condition(cond, False, var_schemas)
                outer_ctxs = (
                    [
                        {topo.outer_var: out}
                        for out in _compared_names(cond, topo.outer_var)
                        if out in mapping
                    ]
                    if not base_ctx and topo.outer_var
                    else []
                )
                if pos or neg_so_far:
                    _add_state(_merge_states(base_ctx, neg_so_far, pos))
                    if base_ctx and pos:
                        _add_state(_merge_states(base_ctx, pos))
                    elif pos:
                        for octx in outer_ctxs:
                            _add_state(_merge_states(octx, neg_so_far, pos))
                if stack:
                    stack[-1] = _merge_states(stack[-1], neg)
                else:
                    stack.append(copy.deepcopy(neg))
            elif kw == "else":
                neg_so_far = copy.deepcopy(stack[-1]) if stack else {}
                if neg_so_far:
                    _add_state(_merge_states(base_ctx, neg_so_far))
            elif kw == "endif" and stack:
                stack.pop()

        if inner_name is None and stack:
            outer_neg = {
                k: v for k, v in stack[-1].items() if v != base_defaults.get(k)
            }

    # If instruction has conditions but no inner split matched, also test
    # conditions against every routing entry.
    if len(sections) == 1 and mapping:
        for outer, inners in mapping.items():
            for inner in inners:
                if topo.mode == "single_workflow" or topo.outer_var is None:
                    base_ctx = {topo.inner_var: inner}
                else:
                    base_ctx = {
                        topo.outer_var: outer,
                        topo.inner_var: inner,
                    }
                for match in block_re.finditer(app.without_comments):
                    kw, cond = match.group(1), match.group(2).strip()
                    if kw in ("if", "elif"):
                        pos = _satisfy_condition(cond, True, var_schemas)
                        if pos:
                            _add_state(_merge_states(base_ctx, pos))
    return synthesized


def _render(
    template: jinja2.Template,
    base: dict[str, Any],
    states: list[tuple[str, dict[str, Any]]],
) -> list[_Render]:
    """Renders the instruction for each state on top of the default state."""
    results = []
    for label, overrides in states:
        full_state = _merge_states(base, overrides)
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
    topo: _Topology,
    mapping: dict[str, tuple[str, ...]],
) -> list[str]:
    """Checks render errors, routing lists, and hidden tools."""
    problems = []
    hidden_tool_states: dict[str, list[str]] = {t: [] for t in HIDDEN_TOOLS}
    for r in results:
        if r.text is None:
            problems.append(f"rendering {r.label!r} failed: {r.error}")
            continue
        visible = _visible(r.text, tools)
        for tool in HIDDEN_TOOLS:
            if tool in visible:
                hidden_tool_states[tool].append(r.label)

        if topo.mode == "single_workflow":
            if topo.inner_tag not in r.text:
                problems.append(
                    f"{topo.inner_tag} isn't rendered in {r.label!r}; keep"
                    f" {topo.inner_tag} visible in every state"
                )
            if topo.inner_tool in tools and topo.inner_tool not in visible:
                problems.append(
                    f"{topo.inner_tool} isn't visible in {r.label!r}; keep"
                    f" {topo.inner_tag} visible in every state so the agent"
                    f" can switch {topo.inner_noun}s"
                )
            for wf in mapping.get("", ()):
                if not _mentions(r.text, wf):
                    if r.label == "start":
                        problems.append(
                            f"{topo.inner_noun} {wf!r} isn't in the"
                            f" {topo.inner_noun} list the model sees at the"
                            " start"
                        )
                    else:
                        problems.append(
                            f"{topo.inner_noun} {wf!r} isn't in the"
                            f" {topo.inner_noun} list the model sees in"
                            f" {r.label!r}; keep {topo.inner_tag} visible in"
                            " every state"
                        )
            continue

        assert topo.outer_tag is not None
        assert topo.outer_tool is not None
        assert topo.outer_noun is not None
        assert topo.outer_var is not None
        open_verb = "selected" if topo.mode == "domain_workflow" else "open"
        if topo.outer_tag not in r.text:
            problems.append(
                f"{topo.outer_tag} isn't rendered in {r.label!r}; keep"
                f" {topo.outer_tag} visible in every state"
            )
        if topo.outer_tool not in visible:
            problems.append(
                f"{topo.outer_tool} isn't visible in {r.label!r}; keep"
                f" {topo.outer_tag} visible in every state so the agent can"
                f" switch {topo.outer_noun}s"
            )
        for outer in mapping:
            if not _mentions(r.text, outer):
                if r.label == "start":
                    problems.append(
                        f"{topo.outer_noun} {outer!r} isn't in the"
                        f" {topo.outer_noun} list the model sees at the start"
                    )
                else:
                    problems.append(
                        f"{topo.outer_noun} {outer!r} isn't in the"
                        f" {topo.outer_noun} list the model sees in"
                        f" {r.label!r}; keep {topo.outer_tag} visible in every"
                        " state"
                    )
        active_outer = r.state.get(topo.outer_var) or ""
        if active_outer in mapping:
            if topo.inner_tag not in r.text:
                problems.append(
                    f"{topo.inner_tag} isn't rendered in {r.label!r}; keep"
                    f" {topo.inner_tag} visible whenever a {topo.outer_noun}"
                    f" is {open_verb}"
                )
            if topo.inner_tool in tools and topo.inner_tool not in visible:
                problems.append(
                    f"{topo.inner_tool} isn't visible in {r.label!r}; keep"
                    f" {topo.inner_tag} visible whenever a {topo.outer_noun}"
                    f" is {open_verb}"
                )
            for inner in mapping[active_outer]:
                if not _mentions(r.text, inner):
                    if r.label == active_outer:
                        problems.append(
                            f"{topo.inner_noun} {inner!r} isn't in the"
                            f" {topo.inner_noun} list the model sees when"
                            f" {r.label!r} is {open_verb}"
                        )
                    else:
                        problems.append(
                            f"{topo.inner_noun} {inner!r} isn't in the"
                            f" {topo.inner_noun} list the model sees in"
                            f" {r.label!r}; keep {topo.inner_tag} visible"
                            f" while a {topo.inner_noun} is loaded"
                        )
    for tool, labels in hidden_tool_states.items():
        if labels:
            problems.append(
                f"{tool} is visible in: {', '.join(labels)}. The model must"
                " never call it directly"
            )
    return problems


def _static_tool_problems(
    results: list[_Render], app: _App, topo: _Topology
) -> list[str]:
    """Checks that only start routers and anytime actions show at start."""
    start = next((r for r in results if r.label == "start" and r.text), None)
    if start is None or start.text is None:
        return []
    start_router = topo.outer_tool or topo.inner_tool
    allowed = {start_router} | set(
        _visible(_block(app.without_comments, "anytime_actions"), app.tools)
    )
    if topo.mode == "single_workflow":
        gate_desc = "before any workflow is loaded"
    elif topo.mode == "domain_workflow":
        gate_desc = "before any domain is selected"
    else:
        gate_desc = "before any playbook is open"
    return [
        f"tool {tool!r} is visible {gate_desc}, but <anytime_actions>"
        f" doesn't name it; only {start_router} and the anytime actions may"
        " be named in static text"
        for tool in _visible(start.text, app.tools)
        if tool not in allowed
    ]


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


def _static_problems(
    app: _App,
    topo: _Topology,
    mapping: dict[str, tuple[str, ...]],
) -> list[str]:
    return (
        _comment_problems(app)
        + _instruction_problems(app)
        + _tool_name_problems(app)
        + _code_problems(app)
        + _router_problems(app, topo, mapping)
        + _router_state_problems(app, topo)
        + _nested_property_problems(app)
    )


def check(
    app_dir: pathlib.Path | str,
    extra_states: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Runs static, template, branch-synthesis, and code checks on `app_dir`."""
    app = _load_app(pathlib.Path(app_dir))
    topo = _detect_topology(app)
    mapping = _load_routing_map(app, topo)
    problems = _static_problems(app, topo, mapping)
    try:
        template, undeclared = _compile(app)
    except jinja2.TemplateSyntaxError as e:
        return list(
            dict.fromkeys(
                [
                    *problems,
                    f"Jinja syntax error on line {e.lineno}: {e.message}",
                ]
            )
        )

    extra = [(_state_label(s), s) for s in extra_states or []]
    synth = _synthesize_branch_states(app, topo, mapping)
    states = _default_states(topo, mapping) + synth + extra
    results = _render(template, _defaults(app.config), states)
    problems += (
        undeclared
        + _rendered_problems(results, app.tools, topo, mapping)
        + _static_tool_problems(results, app, topo)
        + _leftover_problems(app)
    )
    return list(dict.fromkeys(problems))


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
    topo = _detect_topology(app)
    mapping = _load_routing_map(app, topo)
    problems = _static_problems(app, topo, mapping)
    try:
        template, undeclared = _compile(app)
    except jinja2.TemplateSyntaxError as e:
        print(f"Jinja syntax error on line {e.lineno}: {e.message}")
        sys.exit(1)

    extra = [(_state_label(s), s) for s in args.state]
    default_states = _default_states(topo, mapping)
    synth_states = _synthesize_branch_states(app, topo, mapping)
    base_defaults = _defaults(app.config)
    display_results = _render(template, base_defaults, default_states + extra)
    all_results = _render(
        template, base_defaults, default_states + synth_states + extra
    )
    problems += (
        undeclared
        + _rendered_problems(all_results, app.tools, topo, mapping)
        + _static_tool_problems(all_results, app, topo)
        + _leftover_problems(app)
    )

    _print_table(display_results, app.tools)
    _print_note(_state_dependent_tools(app, all_results))
    if args.print_rendered:
        _print_rendered(
            display_results, {label for label, _ in extra or default_states}
        )
    _report(problems)


if __name__ == "__main__":
    main()
