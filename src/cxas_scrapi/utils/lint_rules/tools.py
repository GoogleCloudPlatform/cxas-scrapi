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


"""Tool lint rules (T001-T008).

Validates agent tool Python files against CXAS conventions.
"""

import ast
import functools
import json
import re
import typing
import warnings
from pathlib import Path

import yaml

from cxas_scrapi.utils.linter import (
    AgentGraph,
    LintContext,
    LintResult,
    LocalResource,
    Rule,
    Severity,
    _load_config_dict,
    collect_attached_guardrails,
    discover_local_resources,
    discover_openapi_schema_paths,
    extract_resource_ref_candidates,
    is_voice_app,
    match_local_resources,
    resolve_agent_graph,
    rule,
)

FUNC_DEF_RE = re.compile(r"def\s+(\w+)\s*\(([^)]*)\)")


def _find_tool_function(
    content: str, expected_name: str | None = None
) -> re.Match[str] | None:
    """Find the entry tool function definition, skipping private helpers."""
    matches = list(FUNC_DEF_RE.finditer(content))
    if not matches:
        return None
    if expected_name:
        for m in matches:
            if m.group(1) == expected_name:
                return m
    for m in matches:
        if not m.group(1).startswith("_"):
            return m
    return matches[0]


def _find_tool_ast_function(
    content: str, expected_name: str | None = None
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Find the entry tool AST function node, skipping private helpers."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(content)
    except SyntaxError:
        return None
    fns = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if not fns:
        return None
    if expected_name:
        for fn in fns:
            if fn.name == expected_name:
                return fn
    for fn in fns:
        if not fn.name.startswith("_"):
            return fn
    return fns[0]


def _load_tool_config(file_path: Path) -> tuple[dict | None, Path | None]:
    """Load the tool JSON config from the tool directory.

    Tool layout: tools/<name>/python_function/python_code.py
    JSON config: tools/<name>/<name>.json
    """
    if file_path.suffix == ".json":
        json_path = file_path
    else:
        tool_dir = file_path.parent.parent
        json_path = tool_dir / f"{tool_dir.name}.json"

    if not json_path.exists():
        return None, json_path
    try:
        data = json.loads(json_path.read_text())
        return data if isinstance(data, dict) else None, json_path
    except (json.JSONDecodeError, OSError):
        return None, json_path


@rule("tools")
class MissingDocstring(Rule):
    id = "T002"
    name = "tool-docstring"
    description = "Tool missing docstring — CES uses this as tool description"
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.suffix != ".py":
            return []
        rel = str(file_path.relative_to(context.project_root))
        if '"""' not in content and "'''" not in content:
            return [
                self.make_result(
                    file=rel,
                    message=(
                        "Missing docstring — CES"
                        " uses tool docstrings"
                        " for invocation routing"
                    ),
                    fix=(
                        "Add a descriptive docstring"
                        " explaining when and how"
                        " the LLM should use"
                        " this tool"
                    ),
                )
            ]
        return []


@rule("tools")
class MissingTypeHints(Rule):
    id = "T003"
    name = "tool-type-hints"
    description = "Tool function arguments lack type hints"
    default_severity = Severity.INFO

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.suffix != ".py":
            return []
        rel = str(file_path.relative_to(context.project_root))
        tool_dir_name = file_path.parent.parent.name
        fn_node = _find_tool_ast_function(content, tool_dir_name)
        if fn_node is not None:
            all_args = (
                fn_node.args.posonlyargs
                + fn_node.args.args
                + fn_node.args.kwonlyargs
            )
            non_ctx_args = [
                a for a in all_args if a.arg not in ("context", "tool_context")
            ]
            if non_ctx_args and all(a.annotation is None for a in non_ctx_args):
                return [
                    self.make_result(
                        file=rel,
                        line=fn_node.lineno,
                        message="Function arguments lack type hints",
                        fix=(
                            "Add type hints: def"
                            " tool_name(arg: str,"
                            " count: int) -> dict:"
                        ),
                    )
                ]
            return []

        fn_match = _find_tool_function(content, tool_dir_name)
        if fn_match:
            args_str = fn_match.group(2)
            non_ctx_args_str = [
                p.strip()
                for p in args_str.split(",")
                if p.strip()
                and p.split(":")[0].split("=")[0].strip()
                not in ("context", "tool_context")
            ]
            if non_ctx_args_str and ":" not in args_str:
                return [
                    self.make_result(
                        file=rel,
                        line=1,
                        message="Function arguments lack type hints",
                        fix=(
                            "Add type hints: def"
                            " tool_name(arg: str,"
                            " count: int) -> dict:"
                        ),
                    )
                ]
        return []


@rule("tools")
class FunctionNameMismatch(Rule):
    id = "T004"
    name = "tool-fn-name"
    description = "Tool function name should match tool directory name"
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.suffix != ".py":
            return []
        rel = str(file_path.relative_to(context.project_root))
        tool_dir_name = file_path.parent.parent.name

        fn_match = _find_tool_function(content, tool_dir_name)
        if not fn_match:
            return [
                self.make_result(
                    file=rel,
                    line=1,
                    message="No function definition found in tool file",
                )
            ]

        actual_fn = fn_match.group(1)
        if actual_fn != tool_dir_name:
            return [
                self.make_result(
                    file=rel,
                    line=1,
                    message=(
                        f"Function named"
                        f" '{actual_fn}', expected"
                        f" '{tool_dir_name}'"
                        " (matching directory)"
                    ),
                    fix=f"Rename to: def {tool_dir_name}(...):",
                )
            ]
        return []


@rule("tools")
class HighCardinalityArgs(Rule):
    id = "T005"
    name = "tool-high-cardinality"
    description = (
        "High-cardinality input arguments reduce deterministic tool selection"
    )
    default_severity = Severity.INFO

    HIGH_CARDINALITY_PATTERNS = [
        (
            r"timestamp",
            "timestamp — hard for voice users to express",
        ),
        (
            r"latitude|longitude|coordinates",
            "coordinates — high cardinality",
        ),
        (
            r"session_id|request_id|trace_id",
            "internal ID — not voice-expressible",
        ),
    ]

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.suffix != ".py":
            return []
        tool_dir_name = file_path.parent.parent.name
        fn_match = _find_tool_function(content, tool_dir_name)
        if not fn_match:
            return []

        rel = str(file_path.relative_to(context.project_root))
        args_str = fn_match.group(2)
        return [
            self.make_result(
                file=rel,
                message=f"High-cardinality argument: {label}",
                fix=(
                    "Design args that a human"
                    " can express in voice mode"
                    " (e.g., region, category,"
                    " last_n_days)"
                ),
            )
            for pattern, label in self.HIGH_CARDINALITY_PATTERNS
            if re.search(pattern, args_str, re.IGNORECASE)
        ]


@rule("tools")
class ExcessiveReturnData(Rule):
    id = "T006"
    name = "tool-return-explosion"
    description = "Tool returning excessive data bloats LLM context"
    default_severity = Severity.INFO

    RETURN_PATTERNS = [
        (
            r"return\s+response\.json\(\)",
            "Returning raw API response"
            " — may include data the"
            " LLM doesn't need",
            "Filter the response to only"
            " include fields the LLM"
            " needs for decision-making",
        ),
        (
            r"return\s+json\.loads\(",
            "Returning parsed JSON"
            " directly — consider"
            " filtering to relevant"
            " fields only",
            "Only return data that the LLM needs to see",
        ),
    ]

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.suffix != ".py":
            return []
        rel = str(file_path.relative_to(context.project_root))
        return [
            self.make_result(file=rel, message=msg, fix=fix)
            for pattern, msg, fix in self.RETURN_PATTERNS
            if re.search(pattern, content)
        ]


_TOOL_ATTR_RE = re.compile(r"\b(?:async_)?tools\.(\w+)\b")


@functools.lru_cache(maxsize=512)
def _extract_py_referenced_tools_cached(
    py_path: Path,
    mtime_ns: int,
    file_size: int,
) -> frozenset[str]:
    """Return all tool names referenced via AST in ``py_path``."""
    try:
        text = py_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return frozenset()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(text)
    except SyntaxError:
        return frozenset(_TOOL_ATTR_RE.findall(text))

    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in ("tools", "async_tools")
        ):
            found.add(node.attr)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2  # noqa: PLR2004
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in ("tools", "async_tools")
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            found.add(node.args[1].value)
    return frozenset(found)


def _py_file_references_tools(
    py_path: Path,
    candidate_names: set[str],
) -> bool:
    """Check via AST whether a Python file references any candidate tool."""
    try:
        stat = py_path.stat()
    except OSError:
        return False
    referenced = _extract_py_referenced_tools_cached(
        py_path, stat.st_mtime_ns, stat.st_size
    )
    return bool(candidate_names & referenced)


def _load_tool_config_any(
    file_path: Path,
) -> tuple[dict | None, Path | None, Path | None]:
    """Load tool JSON/YAML config and return ``(cfg, cfg_path, app_root)``."""
    if file_path.suffix.lower() in (".json", ".yaml", ".yml"):
        tool_dir = file_path.parent
        return _load_config_dict(file_path), file_path, tool_dir.parent.parent

    tool_dir = file_path.parent.parent
    app_root = tool_dir.parent.parent
    for ext in (".json", ".yaml", ".yml"):
        candidate = tool_dir / f"{tool_dir.name}{ext}"
        if candidate.is_file() and not candidate.is_symlink():
            return _load_config_dict(candidate), candidate, app_root
    return None, tool_dir / f"{tool_dir.name}.json", app_root


def _agent_tool_refs(cfg: dict) -> set[str]:
    """Extract raw and short-ID tool reference names from an agent config."""
    raw_tools = cfg.get("tools")
    if not isinstance(raw_tools, list):
        return set()
    refs: set[str] = set()
    for entry in raw_tools:
        if isinstance(entry, str) and entry.strip():
            refs.update(extract_resource_ref_candidates(entry, "tools"))
    return refs


def _guardrail_invokes_tool(gr_data: dict, candidate_names: set[str]) -> bool:
    """Return True if a guardrail ``action.toolCall`` references a tool."""
    action = gr_data.get("action")
    if not isinstance(action, dict):
        return False
    tool_call = action.get("toolCall") or action.get("tool_call")
    if not isinstance(tool_call, dict):
        return False
    tool_ref = str(tool_call.get("tool") or "").strip()
    if not tool_ref:
        return False
    short_ref = tool_ref.rsplit("/", 1)[-1]
    return tool_ref in candidate_names or short_ref in candidate_names


def _is_tool_referenced_in_code_or_guardrails(
    app_root: Path,
    candidate_names: set[str],
    own_tool_dir: Path,
    graph: AgentGraph | None = None,
) -> bool:
    """Return True if candidate tool is referenced in code or guardrails."""
    filter_reachable = graph is not None and graph.root_agent_dir is not None

    agents_dir = app_root / "agents"
    if agents_dir.is_dir() and not agents_dir.is_symlink():
        if filter_reachable and graph is not None:
            agent_scan_dirs = [
                graph.agent_dirs[name]
                for name in sorted(graph.reachable)
                if name in graph.agent_dirs
            ]
        else:
            agent_scan_dirs = [agents_dir]
        for scan_dir in agent_scan_dirs:
            for py_path in scan_dir.rglob("*.py"):
                if not py_path.is_symlink() and _py_file_references_tools(
                    py_path, candidate_names
                ):
                    return True

    tools_dir = app_root / "tools"
    if tools_dir.is_dir() and not tools_dir.is_symlink():
        agent_tool_map: dict[str, set[str]] = {}
        if filter_reachable and graph is not None:
            agent_tool_map = {
                a: _agent_tool_refs(cfg)
                for a, cfg in graph.agent_configs.items()
            }
        for other_dir in sorted(tools_dir.iterdir()):
            if (
                other_dir.is_symlink()
                or not other_dir.is_dir()
                or other_dir == own_tool_dir
            ):
                continue
            if filter_reachable and graph is not None:
                other_cands = {other_dir.name}
                other_cfg, _, _ = _load_tool_config_any(
                    other_dir / "python_function" / "python_code.py"
                )
                if isinstance(other_cfg, dict):
                    disp = other_cfg.get("displayName") or other_cfg.get(
                        "display_name"
                    )
                    if isinstance(disp, str) and disp.strip():
                        other_cands.add(disp.strip())
                    raw_other_name = other_cfg.get("name")
                    if (
                        isinstance(raw_other_name, str)
                        and raw_other_name.strip()
                    ):
                        clean_other = raw_other_name.strip()
                        other_cands.update(
                            extract_resource_ref_candidates(
                                clean_other, "tools"
                            )
                        )
                        other_cands.add(clean_other.rsplit("/", 1)[-1])
                attaching = [
                    a
                    for a, refs in agent_tool_map.items()
                    if other_cands & refs
                ]
                if attaching and not any(
                    a in graph.reachable for a in attaching
                ):
                    continue
            for py_path in other_dir.rglob("*.py"):
                if not py_path.is_symlink() and _py_file_references_tools(
                    py_path, candidate_names
                ):
                    return True

    guardrails_dir = app_root / "guardrails"
    if guardrails_dir.is_dir() and not guardrails_dir.is_symlink():
        if filter_reachable and graph is not None:
            gr_resources = discover_local_resources(app_root, "guardrails")
            app_grs, agent_grs = collect_attached_guardrails(
                graph, gr_resources
            )
            gr_files: list[Path] = []
            for res in gr_resources:
                is_attached = res in app_grs or any(
                    a in graph.reachable for a in agent_grs.get(res, ())
                )
                if not is_attached:
                    continue
                if res.report_path.is_file():
                    gr_files.append(res.report_path)
                elif res.report_path.is_dir():
                    for ext in ("*.json", "*.yaml", "*.yml"):
                        gr_files.extend(sorted(res.report_path.glob(ext)))
        else:
            gr_files = []
            for ext in ("*.json", "*.yaml", "*.yml"):
                gr_files.extend(sorted(guardrails_dir.rglob(ext)))

        for gr_path in gr_files:
            gr_data = _load_config_dict(gr_path)
            if gr_data is not None and _guardrail_invokes_tool(
                gr_data, candidate_names
            ):
                return True

    return False


@rule("tools")
class ToolDisplayNameUnreferenced(Rule):
    id = "T008"
    name = "tool-displayname-unreferenced"
    description = (
        "Tool displayName not referenced by any reachable agent's tools array"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        tool_config, cfg_path, app_root = _load_tool_config_any(file_path)
        if not tool_config or cfg_path is None or app_root is None:
            return []

        raw_display = tool_config.get("displayName") or tool_config.get(
            "display_name"
        )
        if not isinstance(raw_display, str) or not raw_display.strip():
            return []
        display_name = raw_display.strip()

        agents_dir = app_root / "agents"
        if not agents_dir.exists():
            return []

        candidate_names: set[str] = {display_name, cfg_path.parent.name}
        raw_name = tool_config.get("name")
        if isinstance(raw_name, str) and raw_name.strip():
            clean_name = raw_name.strip()
            candidate_names.add(clean_name)
            candidate_names.add(clean_name.rsplit("/", 1)[-1])

        graph = resolve_agent_graph(app_root)
        attaching_agents = [
            agent_name
            for agent_name, agent_cfg in sorted(graph.agent_configs.items())
            if candidate_names & _agent_tool_refs(agent_cfg)
        ]

        if graph.root_agent_dir is not None:
            reachable_attaching = [
                a for a in attaching_agents if a in graph.reachable
            ]
            unreachable_attaching = [
                a for a in attaching_agents if a not in graph.reachable
            ]
        else:
            reachable_attaching = attaching_agents
            unreachable_attaching = []

        if reachable_attaching:
            return []

        if _is_tool_referenced_in_code_or_guardrails(
            app_root, candidate_names, cfg_path.parent, graph=graph
        ):
            return []

        try:
            rel = str(cfg_path.relative_to(context.project_root))
        except ValueError:
            rel = str(cfg_path)

        if unreachable_attaching:
            unreachable_str = ", ".join(unreachable_attaching)
            return [
                self.make_result(
                    file=rel,
                    message=(
                        f"Tool '{display_name}' is only attached to "
                        f"unreachable agent(s): {unreachable_str}."
                    ),
                    fix=(
                        f"Attach '{display_name}' to a reachable agent, "
                        f"connect {unreachable_str} to rootAgent, or remove "
                        "the unused tool."
                    ),
                )
            ]

        return [
            self.make_result(
                file=rel,
                message=(
                    f"Tool displayName"
                    f" '{display_name}' not"
                    " found in any agent's"
                    " tools array"
                ),
                fix=(
                    "Add this tool to the"
                    " relevant agent's"
                    " config, or remove the"
                    " tool if unused"
                ),
            )
        ]


@rule("tools")
class KwargsInSignature(Rule):
    id = "T009"
    name = "tool-kwargs-signature"
    description = (
        "Tool function uses **kwargs"
        " — platform requires explicit"
        " named parameters"
    )
    default_severity = Severity.ERROR

    def check(
        self,
        file_path: Path,
        content: str,
        context: LintContext,
    ) -> list[LintResult]:
        if file_path.suffix != ".py":
            return []
        rel = str(file_path.relative_to(context.project_root))
        tool_dir_name = file_path.parent.parent.name
        fn_node = _find_tool_ast_function(content, tool_dir_name)
        if fn_node is not None:
            if fn_node.args.kwarg is not None:
                return [
                    self.make_result(
                        file=rel,
                        line=fn_node.lineno,
                        message=(
                            "Tool function uses"
                            " **kwargs — platform"
                            " silently drops tools"
                            " with **kwargs during"
                            " import"
                        ),
                        fix=(
                            "Replace **kwargs with"
                            " explicit parameters:"
                            " def my_tool(param1:"
                            " str = '', param2:"
                            " str = '') -> dict:"
                        ),
                    )
                ]
            return []

        fn_match = _find_tool_function(content, tool_dir_name)
        if fn_match and "**" in fn_match.group(2):
            line = content[: fn_match.start()].count("\n") + 1
            return [
                self.make_result(
                    file=rel,
                    line=line,
                    message=(
                        "Tool function uses"
                        " **kwargs — platform"
                        " silently drops tools"
                        " with **kwargs during"
                        " import"
                    ),
                    fix=(
                        "Replace **kwargs with"
                        " explicit parameters:"
                        " def my_tool(param1:"
                        " str = '', param2:"
                        " str = '') -> dict:"
                    ),
                )
            ]
        return []


@rule("tools")
class ToolInvalidPythonSyntax(Rule):
    id = "T010"
    name = "tool-python-syntax"
    description = "Tool Python file must have valid syntax"
    default_severity = Severity.ERROR

    def check(
        self,
        file_path: Path,
        content: str,
        context: LintContext,
    ) -> list[LintResult]:
        if file_path.suffix != ".py":
            return []
        rel = str(file_path.relative_to(context.project_root))
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                compile(content, rel, "exec")
        except SyntaxError as e:
            return [
                self.make_result(
                    file=rel,
                    line=e.lineno,
                    message=(f"Invalid Python syntax: {e.msg}"),
                    fix=(
                        "Fix the syntax error"
                        " — invalid Python causes"
                        " tools to be silently"
                        " dropped during import"
                    ),
                )
            ]
        return []


@rule("tools")
class NoneDefaultValue(Rule):
    id = "T011"
    name = "tool-none-default"
    description = (
        "Tool parameter uses None as default"
        " — platform silently drops"
        " these tools"
    )
    default_severity = Severity.ERROR

    def check(
        self,
        file_path: Path,
        content: str,
        context: LintContext,
    ) -> list[LintResult]:
        if file_path.suffix != ".py":
            return []
        rel = str(file_path.relative_to(context.project_root))
        tool_dir_name = file_path.parent.parent.name
        fn_node = _find_tool_ast_function(content, tool_dir_name)
        if fn_node is not None:
            pos_args = fn_node.args.posonlyargs + fn_node.args.args
            defaults = fn_node.args.defaults
            pairs: list[tuple[ast.arg, ast.expr | None]] = []
            if defaults:
                pairs.extend(
                    zip(pos_args[-len(defaults) :], defaults, strict=False)
                )
            pairs.extend(
                zip(
                    fn_node.args.kwonlyargs,
                    fn_node.args.kw_defaults,
                    strict=False,
                )
            )
            return [
                self.make_result(
                    file=rel,
                    line=arg.lineno,
                    message=(
                        f"Parameter"
                        f" '{arg.arg}'"
                        " uses None as default"
                        " — platform silently"
                        " drops tools with None"
                        " defaults during import"
                    ),
                    fix=(
                        "Use type-matching defaults: str = '', int = 0,"
                        " list = []"
                    ),
                )
                for arg, default in pairs
                if arg.arg not in ("context", "tool_context")
                and isinstance(default, ast.Constant)
                and default.value is None
            ]

        fn_match = _find_tool_function(content, tool_dir_name)
        if not fn_match:
            return []

        args_str = fn_match.group(2)
        line = content[: fn_match.start()].count("\n") + 1

        def _param_name(p: typing.Any) -> typing.Any:
            return p.split(":")[0].split("=")[0].strip()

        return [
            self.make_result(
                file=rel,
                line=line,
                message=(
                    f"Parameter"
                    f" '{_param_name(p)}'"
                    " uses None as default"
                    " — platform silently"
                    " drops tools with None"
                    " defaults during import"
                ),
                fix=(
                    "Use type-matching defaults: str = '', int = 0, list = []"
                ),
            )
            for p in args_str.split(",")
            if "=" in p
            and _param_name(p) not in ("context", "tool_context")
            and re.search(r"=\s*None\s*$", p.strip())
        ]


@rule("tools")
class MissingToolDescriptionInJSON(Rule):
    id = "T012"
    name = "tool-json-missing-description"
    description = (
        "Tool JSON configuration must include top-level description or "
        "nested tool type description."
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        tool_config, json_path = _load_tool_config(file_path)
        if not tool_config:
            return []

        tool_types = [
            "clientFunction",
            "openApiTool",
            "googleSearchTool",
            "connectorTool",
            "dataStoreTool",
            "pythonFunction",
            "mcpTool",
            "fileSearchTool",
            "systemTool",
            "agentTool",
            "remoteAgentTool",
            "widgetTool",
        ]

        description = tool_config.get("description")
        field_name = "description"
        fix_msg = "Add a 'description' key to the tool's JSON file."

        active_tool_type = None
        for t in tool_types:
            if tool_config.get(t) is not None:
                active_tool_type = t
                break

        if not description or not str(description).strip():  # noqa: SIM102
            if active_tool_type is not None:
                tool_obj = tool_config.get(active_tool_type)
                if isinstance(tool_obj, dict):
                    description = tool_obj.get("description")
                field_name = f"{active_tool_type}.description"
                if active_tool_type == "pythonFunction":
                    fix_msg = (
                        "Add a docstring to the Python tool function or a "
                        "'description' key to the 'pythonFunction' object in "
                        "the tool's JSON file."
                    )
                else:
                    fix_msg = (
                        f"Add a 'description' key to the '{active_tool_type}' "
                        f"object in the tool's JSON file."
                    )

        if (
            (not description or not str(description).strip())
            and active_tool_type == "pythonFunction"
            and json_path is not None
        ):
            app_root = json_path.parent.parent.parent
            py_func_cfg = tool_config.get("pythonFunction")
            code_ref = (
                py_func_cfg.get("pythonCode")
                if isinstance(py_func_cfg, dict)
                else None
            )
            py_path = (
                (app_root / code_ref)
                if isinstance(code_ref, str) and code_ref.strip()
                else (json_path.parent / "python_function" / "python_code.py")
            )
            if not py_path.is_file():
                py_path = (
                    json_path.parent / "python_function" / "python_code.py"
                )
            if py_path.is_file():
                try:
                    py_text = py_path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    fn_node = _find_tool_ast_function(
                        py_text, json_path.parent.name
                    )
                    if (
                        fn_node is not None
                        and (ast.get_docstring(fn_node) or "").strip()
                    ):
                        return []
                except OSError:
                    pass

        if not description or not str(description).strip():
            rel = str(json_path.relative_to(context.project_root))
            return [
                self.make_result(
                    file=rel,
                    message=(
                        f"Tool JSON configuration is missing "
                        f"{field_name} field. "
                        f"The LLM relies on this description to know when to "
                        f"call the tool."
                    ),
                    fix=fix_msg,
                )
            ]
        return []


@rule("tools")
class ToolConfigInvalid(Rule):
    id = "T013"
    name = "tool-config-invalid"
    description = (
        "Tool JSON configuration must be a valid JSON object/dictionary"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if file_path.suffix == ".json":
            json_path = file_path
        else:
            tool_dir = file_path.parent.parent
            json_path = tool_dir / f"{tool_dir.name}.json"

        if not json_path.exists():
            return []

        rel = str(json_path.relative_to(context.project_root))

        try:
            data = json.loads(json_path.read_text())
        except json.JSONDecodeError as e:
            return [
                self.make_result(
                    file=rel,
                    message=f"Tool JSON configuration has invalid syntax: {e}",
                )
            ]
        except OSError as e:
            return [
                self.make_result(
                    file=rel,
                    message=f"Failed to read tool JSON configuration: {e}",
                )
            ]

        if not isinstance(data, dict):
            return [
                self.make_result(
                    file=rel,
                    message=(
                        "Tool JSON configuration must be a JSON "
                        f"dictionary/object, got {type(data).__name__}"
                    ),
                    fix=(
                        "Ensure the configuration file starts with '{' "
                        "and ends with '}'"
                    ),
                )
            ]

        return []


# --- T014: Conversational tool pacing for composite models ---------------


@rule("tools", models=["gemini-composite-v1"])
class CompositeToolConversationalPacing(Rule):
    id = "T014"
    name = "composite-tool-pacing"
    description = (
        "Tool docstring or description must include a conversational pacing "
        "directive in Gemini Composite V1 to prevent dead air"
    )
    default_severity = Severity.WARNING

    PACING_PATTERN = re.compile(
        r"(?i)\b(?:conversational\s+)?pacing\s+phrase\b|"
        r"\bbefore\s+calling\s+this\s+tool,\s+speak\b|"
        r"\bspeak\s+a\s+brief\b|"
        r"\bspoken\s+pacing\b"
    )

    TERMINAL_PATTERNS = (
        r"(?:^|_)end(?:_session)?(?:$|_)",
        r"(?:^|_)exit(?:$|_)",
        r"(?:^|_)wrap_up(?:$|_)",
        r"(?:^|_)mock(?:$|_)",
    )

    TOOL_TYPES = (
        "clientFunction",
        "openApiTool",
        "googleSearchTool",
        "connectorTool",
        "dataStoreTool",
        "pythonFunction",
        "mcpTool",
        "fileSearchTool",
        "systemTool",
        "agentTool",
        "remoteAgentTool",
        "widgetTool",
    )

    def _is_terminal(self, tool_name: str) -> bool:
        name_lower = tool_name.lower()
        return any(re.search(pat, name_lower) for pat in self.TERMINAL_PATTERNS)

    def _get_docstring_and_line(
        self, file_path: Path, content: str
    ) -> tuple[str | None, int]:
        if file_path.suffix == ".py":
            # Check triple double-quoted or single-quoted docstrings
            m = re.search(r'("""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\')', content)
            if m:
                line = content[: m.start()].count("\n") + 1
                return m.group(1), line
            return None, 1

        if file_path.suffix == ".json":
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                return None, 1
            if not isinstance(data, dict):
                return None, 1

            desc = data.get("description")
            if isinstance(desc, str) and desc.strip():
                return desc, 1

            for t in self.TOOL_TYPES:
                tool_obj = data.get(t)
                if isinstance(tool_obj, dict):
                    nested_desc = tool_obj.get("description")
                    if isinstance(nested_desc, str) and nested_desc.strip():
                        return nested_desc, 1

            return None, 1

        return None, 1

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        # Determine tool name from directory layout (Python or JSON tool)
        if file_path.suffix == ".py":
            tool_name = file_path.parent.parent.name
        elif file_path.suffix == ".json":
            tool_name = file_path.parent.name
        else:
            return []

        if self._is_terminal(tool_name):
            return []

        doc, line_no = self._get_docstring_and_line(file_path, content)
        if not doc or not doc.strip():
            # Missing docstring is handled by T002 / T012
            return []

        if not self.PACING_PATTERN.search(doc):
            rel = str(file_path.relative_to(context.project_root))
            return [
                self.make_result(
                    file=rel,
                    line=line_no,
                    message=(
                        f"Tool '{tool_name}' lacks a conversational pacing "
                        "directive. In Gemini Composite V1, speak a brief "
                        "natural bridge phrase before calling to prevent "
                        "caller dead air during execution."
                    ),
                    fix=(
                        "Add a conversational pacing directive to the tool "
                        "docstring/description (e.g. 'Before calling this "
                        "tool, speak a brief conversational pacing phrase: "
                        "Let me check that for you...')."
                    ),
                )
            ]

        return []


def _load_data_store_tool_config(
    file_path: Path, content: str
) -> dict[str, typing.Any] | None:
    """Parse a JSON or YAML tool config file under tools/, else return None."""
    suffix = file_path.suffix.lower()
    if (
        suffix not in {".json", ".yaml", ".yml"}
        or "tools" not in file_path.parts
    ):
        return None
    try:
        data = (
            json.loads(content)
            if suffix == ".json"
            else yaml.safe_load(content)
        )
    except (ValueError, OSError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def _extract_data_store_filters(data: dict[str, typing.Any]) -> list[str]:
    """Collect non-empty filter strings from a dataStoreTool config dict."""
    filters: list[str] = []
    ds_tool = data.get("dataStoreTool") or data.get("data_store_tool")
    containers: list[dict[str, typing.Any]] = [data]
    if isinstance(ds_tool, dict):
        containers.insert(0, ds_tool)
        top_filter = ds_tool.get("filter")
        if isinstance(top_filter, str) and top_filter:
            filters.append(top_filter)

    def _collect_source(source: typing.Any) -> None:
        if isinstance(source, dict):
            s_filter = source.get("filter")
            if isinstance(s_filter, str) and s_filter:
                filters.append(s_filter)

    for container in containers:
        _collect_source(
            container.get("dataStoreSource")
            or container.get("data_store_source")
        )
        sources = (
            container.get("dataStoreSources")
            or container.get("data_store_sources")
            or []
        )
        if isinstance(sources, list):
            for source in sources:
                _collect_source(source)
        engine_source = container.get("engineSource") or container.get(
            "engine_source"
        )
        if isinstance(engine_source, dict):
            _collect_source(engine_source)
            engine_sources = (
                engine_source.get("dataStoreSources")
                or engine_source.get("data_store_sources")
                or []
            )
            if isinstance(engine_sources, list):
                for source in engine_sources:
                    _collect_source(source)

    return filters


@rule("tools")
class DataStoreFilterTemplateSyntax(Rule):
    id = "T017"
    name = "data-store-filter-template"
    description = "Detects template syntax in data store tools/sources filters."
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        data = _load_data_store_tool_config(file_path, content)
        if data is None:
            return []

        rel = str(file_path.relative_to(context.project_root))
        filters = _extract_data_store_filters(data)
        if any("{" in f_str or "}" in f_str for f_str in filters):
            return [
                self.make_result(
                    file=rel,
                    message=(
                        "Template syntax {...} or {{...}} found in"
                        " static filter of dataStoreTool."
                    ),
                    fix=(
                        "Data store static filters do not support"
                        " variable interpolation."
                    ),
                )
            ]
        return []


@rule("tools")
class DataStoreFilterParameterBehavior(Rule):
    id = "T018"
    name = "data-store-filter-parameter-behavior"
    description = (
        "Brand-scoped dataStoreTool without NEVER_INCLUDE parameter behavior."
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        data = _load_data_store_tool_config(file_path, content)
        if data is None:
            return []

        raw_ds = data.get("dataStoreTool") or data.get("data_store_tool")
        ds_tool = raw_ds if isinstance(raw_ds, dict) else {}
        has_filter = bool(_extract_data_store_filters(data))

        if not ds_tool and not has_filter:
            return []

        tool_dir_name = file_path.parent.name
        raw_display = data.get("displayName") or data.get("display_name") or ""
        tool_display_name = raw_display if isinstance(raw_display, str) else ""

        is_brand_scoped = (
            has_filter
            or tool_dir_name.startswith("ds_")
            or tool_display_name.startswith("ds_")
        )

        if is_brand_scoped:
            behavior = ds_tool.get("filterParameterBehavior")
            if behavior is None:
                behavior = ds_tool.get("filter_parameter_behavior")
            if behavior not in ("NEVER_INCLUDE", 3):
                rel = str(file_path.relative_to(context.project_root))
                reason = (
                    "with static filter" if has_filter else "with ds_ prefix"
                )
                return [
                    self.make_result(
                        file=rel,
                        message=(
                            f"Brand-scoped dataStoreTool ({reason})"
                            " must use NEVER_INCLUDE (or 3)."
                        ),
                        fix=(
                            "Set"
                            " dataStoreTool.filterParameterBehavior to "
                            "NEVER_INCLUDE."
                        ),
                    )
                ]
        return []


@rule("tools")
class DataStoreVoiceModality(Rule):
    id = "T019"
    name = "data-store-voice-modality"
    description = (
        "Warns on voice deployments if dataStoreTool lacks AUDIO config."
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        data = _load_data_store_tool_config(file_path, content)
        if data is None:
            return []

        tool_name = file_path.parent.name
        raw_display = data.get("displayName") or data.get("display_name") or ""
        tool_display_name = raw_display if isinstance(raw_display, str) else ""

        if (
            context.all_tool_names
            and tool_name not in context.all_tool_names
            and tool_display_name not in context.all_tool_names
        ):
            return []

        if not is_voice_app(context):
            return []

        ds_tool = data.get("dataStoreTool")
        if ds_tool is None:
            ds_tool = data.get("data_store_tool")
        if not isinstance(ds_tool, dict):
            return []

        rel = str(file_path.relative_to(context.project_root))
        results: list[LintResult] = []
        mod_configs = ds_tool.get("modalityConfigs")
        if mod_configs is None:
            mod_configs = ds_tool.get("modality_configs")
        if mod_configs is None:
            mod_configs = []

        audio_conf: dict[str, typing.Any] | None = None
        if isinstance(mod_configs, dict):
            candidate = mod_configs.get("AUDIO")
            if candidate is None:
                candidate = mod_configs.get(2)
            if isinstance(candidate, dict):
                audio_conf = candidate
        elif isinstance(mod_configs, list):
            for mc in mod_configs:
                if not isinstance(mc, dict):
                    continue
                mt = mc.get("modalityType")
                if mt is None:
                    mt = mc.get("modality_type")
                if mt is None:
                    mt = mc.get("modality")
                if mt in ("AUDIO", 2):
                    audio_conf = mc
                    break

        if audio_conf is None:
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        "dataStoreTool missing AUDIO entry in"
                        " modalityConfigs for a voice deployment."
                    ),
                    fix="Add an AUDIO configuration block to modalityConfigs.",
                )
            )
        else:
            snippets = audio_conf.get("snippetsConfig") or audio_conf.get(
                "snippets_config"
            )
            if not isinstance(snippets, dict) or not snippets:
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            "dataStoreTool AUDIO modalityConfigs missing"
                            " snippetsConfig/snippets_config."
                        ),
                        fix=(
                            "Provide snippetsConfig inside the"
                            " AUDIO modality config."
                        ),
                    )
                )

            raw_rewriter = audio_conf.get("rewriterConfig") or audio_conf.get(
                "rewriter_config"
            )
            rewriter = raw_rewriter if isinstance(raw_rewriter, dict) else {}
            raw_summarizer = audio_conf.get(
                "summarizationConfig"
            ) or audio_conf.get("summarization_config")
            summarizer = (
                raw_summarizer if isinstance(raw_summarizer, dict) else {}
            )

            if rewriter.get("disabled") is not True:
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            "rewriterConfig.disabled is not True in"
                            " AUDIO modalityConfig."
                        ),
                        fix=(
                            "Disable rewriter for Voice"
                            " deployments inside AUDIO."
                        ),
                    )
                )
            if summarizer.get("disabled") is not True:
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            "summarizationConfig.disabled is not True"
                            " in AUDIO modalityConfig."
                        ),
                        fix=(
                            "Disable summarization for Voice"
                            " deployments inside AUDIO."
                        ),
                    )
                )
        return results


def _resolve_app_root(file_path: Path, context: LintContext) -> Path:
    if file_path.name in ("app.json", "app.yaml", "app.yml"):
        return file_path.parent
    if context.app_root and context.app_root.exists():
        return context.app_root
    return context.app_dir


def _rel_or_str(path: Path, project_root: Path) -> str:
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


def _camel_snake_variants(key: str) -> set[str]:
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower()
    parts = snake.split("_")
    camel = parts[0] + "".join(p.title() for p in parts[1:]) if parts else key
    return {key, snake, camel}


def _find_env_var_placeholders(
    obj: typing.Any, prefix: tuple[str, ...] = ()
) -> list[tuple[str, ...]]:
    """Return key-path tuples whose string value equals ``$env_var``."""
    found: list[tuple[str, ...]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.extend(_find_env_var_placeholders(v, (*prefix, str(k))))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            found.extend(_find_env_var_placeholders(item, (*prefix, str(idx))))
    elif isinstance(obj, str) and obj.strip() == "$env_var":
        found.append(prefix)
    return found


def _is_resolved_env_value(val: typing.Any) -> bool:
    """Return True if ``val`` is a concrete non-empty non-placeholder value."""
    if val is None:
        return False
    if isinstance(val, str):
        stripped = val.strip()
        return bool(stripped) and stripped != "$env_var"
    if isinstance(val, (dict, list)):
        return bool(val)
    return True


def _dict_lookup_variants(
    mapping: dict[str, typing.Any], key: str
) -> tuple[bool, typing.Any]:
    variants = _camel_snake_variants(key)
    for k, v in mapping.items():
        if k in variants:
            return True, v
    return False, None


def _contains_resolved_leaf(obj: typing.Any, leaf_variants: set[str]) -> bool:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in leaf_variants and _is_resolved_env_value(v):
                return True
            if _contains_resolved_leaf(v, leaf_variants):
                return True
    elif isinstance(obj, list):
        return any(_contains_resolved_leaf(i, leaf_variants) for i in obj)
    return False


def _env_entry_resolves_placeholder(
    entry: typing.Any, path: tuple[str, ...]
) -> bool:
    """Return True if an ``environment.json`` entry resolves ``path``."""
    if not isinstance(entry, dict) or not entry:
        return False

    # 1. Exact nested path traversal (allowing camelCase/snake_case keys)
    cur: typing.Any = entry
    matched_exact = True
    for seg in path:
        if isinstance(cur, dict):
            ok, cur = _dict_lookup_variants(cur, seg)
            if not ok:
                matched_exact = False
                break
        elif isinstance(cur, list) and seg.isdigit():
            idx = int(seg)
            if 0 <= idx < len(cur):
                cur = cur[idx]
            else:
                matched_exact = False
                break
        else:
            matched_exact = False
            break
    if matched_exact and _is_resolved_env_value(cur):
        return True

    # 2. Leaf key lookup anywhere inside entry (handles flat {"url": ...}, etc.)
    non_index_segs = [s for s in path if not s.isdigit()]
    if non_index_segs:
        leaf = non_index_segs[-1]
        if _contains_resolved_leaf(entry, _camel_snake_variants(leaf)):
            return True

    return False


def _find_matching_env_entry(
    res: LocalResource,
    subdir: str,
    env_section: dict[str, typing.Any],
) -> tuple[str | None, typing.Any]:
    for env_key, env_val in env_section.items():
        if not isinstance(env_key, str):
            continue
        if match_local_resources(env_key, subdir, [res]):
            return env_key, env_val
    return None, None


@rule("tools")
class ToolToolsetEnvVarParity(Rule):
    """Tool/toolset ``$env_var`` values must resolve in ``environment.json``."""

    id = "T020"
    name = "tool-env-var-parity"
    description = (
        "Tool and toolset $env_var placeholders resolve in environment.json "
        "without dangling entries"
    )
    default_severity = Severity.ERROR
    target = "app_config"

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        app_root = _resolve_app_root(file_path, context)
        env_path = (
            context.env_file
            if context.env_file is not None
            else app_root / "environment.json"
        )

        env_data: dict[str, typing.Any] | None = None
        if env_path.exists():
            if env_path.is_symlink() or not env_path.is_file():
                return []
            try:
                raw_env = json.loads(env_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                return []
            if not isinstance(raw_env, dict):
                return []
            env_data = raw_env

        env_tools = (
            env_data.get("tools")
            if env_data and isinstance(env_data.get("tools"), dict)
            else {}
        )
        env_toolsets = (
            env_data.get("toolsets")
            if env_data and isinstance(env_data.get("toolsets"), dict)
            else {}
        )

        tool_resources = discover_local_resources(app_root, "tools")
        toolset_resources = discover_local_resources(app_root, "toolsets")
        schema_paths = discover_openapi_schema_paths(app_root)
        schemas_by_key: dict[tuple[str, str], list[Path]] = {}
        for kind, key, sp in schema_paths:
            schemas_by_key.setdefault((kind, key), []).append(sp)

        results: list[LintResult] = []
        sections = (
            ("tools", "Tool", "openApiTool", tool_resources, env_tools),
            (
                "toolsets",
                "Toolset",
                "openApiToolset",
                toolset_resources,
                env_toolsets,
            ),
        )

        for subdir, label, oa_key, resources, env_section in sections:
            for res in resources:
                placeholders: list[tuple[Path, tuple[str, ...]]] = []
                if res.report_path.is_file():
                    cfg = _load_config_dict(res.report_path)
                    if cfg is not None:
                        for p in _find_env_var_placeholders(cfg):
                            placeholders.append((res.report_path, p))

                for sp in schemas_by_key.get((subdir, res.key), ()):
                    schema_doc = _load_config_dict(sp)
                    if schema_doc is None:
                        continue
                    servers = schema_doc.get("servers")
                    if isinstance(servers, list):
                        for srv in servers:
                            if (
                                isinstance(srv, dict)
                                and isinstance(srv.get("url"), str)
                                and srv["url"].strip() == "$env_var"
                            ):
                                placeholders.append((sp, (oa_key, "url")))
                                break

                if not placeholders:
                    continue

                _, env_entry = _find_matching_env_entry(
                    res, subdir, env_section
                )
                seen_paths: set[tuple[Path, tuple[str, ...]]] = set()
                for src_path, field_path in placeholders:
                    if (src_path, field_path) in seen_paths:
                        continue
                    seen_paths.add((src_path, field_path))
                    field_desc = ".".join(field_path) or "$env_var"
                    src_rel = _rel_or_str(src_path, context.project_root)
                    if env_data is None:
                        results.append(
                            self.make_result(
                                file=src_rel,
                                severity=Severity.ERROR,
                                message=(
                                    f"{label} '{res.key}' uses '$env_var' at "
                                    f"'{field_desc}' but environment.json is "
                                    "missing."
                                ),
                                fix=(
                                    f"Add a '{subdir}.{res.key}' entry to "
                                    "environment.json resolving this value."
                                ),
                            )
                        )
                    elif not _env_entry_resolves_placeholder(
                        env_entry, field_path
                    ):
                        results.append(
                            self.make_result(
                                file=src_rel,
                                severity=Severity.ERROR,
                                message=(
                                    f"{label} '{res.key}' uses '$env_var' at "
                                    f"'{field_desc}' but environment.json "
                                    f"'{subdir}' has no non-empty value for it."
                                ),
                                fix=(
                                    f"Set '{subdir}.{res.key}' in "
                                    "environment.json to a non-empty value."
                                ),
                            )
                        )

            # Dangling keys in environment.json
            if env_data is not None and env_section:
                env_rel = _rel_or_str(env_path, context.project_root)
                for env_key in sorted(env_section.keys()):
                    if not isinstance(env_key, str):
                        continue
                    if not match_local_resources(env_key, subdir, resources):
                        results.append(
                            self.make_result(
                                file=env_rel,
                                severity=Severity.WARNING,
                                message=(
                                    f"environment.json '{subdir}' entry "
                                    f"'{env_key}' does not match any "
                                    f"{label.lower()} under {subdir}/."
                                ),
                                fix=(
                                    f"Remove stale '{env_key}' from "
                                    f"environment.json '{subdir}' or restore "
                                    f"{subdir}/{env_key}."
                                ),
                            )
                        )

        return results


def _is_valid_openapi_server_url(url: typing.Any) -> bool:
    """Validate an OpenAPI server URL string."""
    if not isinstance(url, str):
        return False
    stripped = url.strip()
    if not stripped:
        return False
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in stripped):
        return False
    if stripped == "$env_var":
        return True
    if stripped.startswith("/"):
        return True
    if stripped.startswith(("http://", "https://")):
        rest = stripped.split("://", 1)[1]
        authority = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        return bool(authority) and not authority.startswith((".", ":"))
    return False


@rule("tools")
class OpenApiSchemaValidity(Rule):
    """OpenAPI schema files must be valid YAML with paths and valid servers."""

    id = "T021"
    name = "openapi-schema-valid"
    description = (
        "OpenAPI schema files must be valid YAML with non-empty paths and "
        "valid server URLs"
    )
    default_severity = Severity.ERROR
    target = "app_config"

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        app_root = _resolve_app_root(file_path, context)
        results: list[LintResult] = []

        for _kind, _key, schema_path in discover_openapi_schema_paths(app_root):
            rel = _rel_or_str(schema_path, context.project_root)
            try:
                raw = schema_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            f"OpenAPI schema could not be read as UTF-8: {e}"
                        ),
                        fix="Ensure the OpenAPI schema file is valid UTF-8.",
                    )
                )
                continue

            try:
                doc = yaml.safe_load(raw)
            except yaml.YAMLError as e:
                results.append(
                    self.make_result(
                        file=rel,
                        message=f"OpenAPI schema is not valid YAML: {e}",
                        fix="Fix the YAML syntax error in the OpenAPI schema.",
                    )
                )
                continue

            if not isinstance(doc, dict):
                results.append(
                    self.make_result(
                        file=rel,
                        message="OpenAPI schema must be a YAML mapping.",
                        fix="Provide a valid OpenAPI 3.0 YAML object.",
                    )
                )
                continue

            paths = doc.get("paths")
            if not isinstance(paths, dict) or len(paths) == 0:
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            "OpenAPI schema must define a non-empty 'paths' "
                            "mapping."
                        ),
                        fix="Add at least one endpoint under 'paths:'.",
                    )
                )

            if "servers" in doc:
                servers = doc.get("servers")
                if not isinstance(servers, list):
                    results.append(
                        self.make_result(
                            file=rel,
                            message="OpenAPI schema 'servers' must be a list.",
                            fix=(
                                "Define 'servers' as a list of {url: ...} "
                                "items."
                            ),
                        )
                    )
                else:
                    for idx, srv in enumerate(servers):
                        if not isinstance(srv, dict):
                            results.append(
                                self.make_result(
                                    file=rel,
                                    message=(
                                        f"OpenAPI schema servers[{idx}] must "
                                        "be a mapping with a 'url' field."
                                    ),
                                    fix=(
                                        "Use '- url: https://...' for each "
                                        "entry in 'servers'."
                                    ),
                                )
                            )
                            continue
                        url = srv.get("url")
                        if not _is_valid_openapi_server_url(url):
                            results.append(
                                self.make_result(
                                    file=rel,
                                    message=(
                                        f"OpenAPI schema servers[{idx}].url "
                                        f"has invalid value {url!r}."
                                    ),
                                    fix=(
                                        "Set 'url' to an http(s):// URL, "
                                        "relative '/' path, or '$env_var'."
                                    ),
                                )
                            )

        return results
