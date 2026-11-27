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
from urllib.parse import urlparse

import yaml

from cxas_scrapi.utils.env_vars import (
    collect_tool_and_toolset_env_var_placeholders,
)
from cxas_scrapi.utils.linter import (
    CONFIG_EXTENSIONS,
    DEFAULT_ENV_FILENAME,
    ENV_VAR_PLACEHOLDER,
    FIELD_DISPLAY_NAME,
    AgentGraph,
    LintContext,
    LintResult,
    Rule,
    Severity,
    _load_config_dict,
    collect_attached_guardrails,
    discover_local_resources,
    discover_openapi_schema_paths,
    extract_resource_ref_candidates,
    get_field,
    is_voice_app,
    match_local_resources,
    parse_config_content,
    rel_to_project,
    resolve_agent_graph,
    resolve_app_root,
    rule,
    safe_ast_parse,
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
    tree = safe_ast_parse(text)
    if tree is None:
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
    if file_path.suffix.lower() in CONFIG_EXTENSIONS:
        tool_dir = file_path.parent
        return _load_config_dict(file_path), file_path, tool_dir.parent.parent

    tool_dir = file_path.parent.parent
    app_root = tool_dir.parent.parent
    for ext in CONFIG_EXTENSIONS:
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


def _any_py_in_dirs_references_tools(
    scan_dirs: list[Path], candidate_names: set[str]
) -> bool:
    """Return True if any non-symlink .py under ``scan_dirs`` calls a tool."""
    return any(
        not py_path.is_symlink()
        and _py_file_references_tools(py_path, candidate_names)
        for scan_dir in scan_dirs
        for py_path in scan_dir.rglob("*.py")
    )


def _tool_candidate_names_for_dir(tool_dir: Path) -> set[str]:
    """Collect directory name, displayName, and CES resource IDs for a tool."""
    cands = {tool_dir.name}
    cfg, _, _ = _load_tool_config_any(
        tool_dir / "python_function" / "python_code.py"
    )
    if not isinstance(cfg, dict):
        return cands
    disp = cfg.get("displayName") or cfg.get("display_name")
    if isinstance(disp, str) and disp.strip():
        cands.add(disp.strip())
    raw_name = cfg.get("name")
    if isinstance(raw_name, str) and raw_name.strip():
        clean = raw_name.strip()
        cands.update(extract_resource_ref_candidates(clean, "tools"))
        cands.add(clean.rsplit("/", 1)[-1])
    return cands


def _tool_referenced_in_agents(
    app_root: Path,
    candidate_names: set[str],
    graph: AgentGraph | None,
    filter_reachable: bool,
) -> bool:
    agents_dir = app_root / "agents"
    if agents_dir.is_symlink() or not agents_dir.is_dir():
        return False
    if filter_reachable and graph is not None:
        scan_dirs = [
            graph.agent_dirs[name]
            for name in sorted(graph.reachable)
            if name in graph.agent_dirs
        ]
    else:
        scan_dirs = [agents_dir]
    return _any_py_in_dirs_references_tools(scan_dirs, candidate_names)


def _tool_referenced_in_other_tools(
    app_root: Path,
    candidate_names: set[str],
    own_tool_dir: Path,
    graph: AgentGraph | None,
    filter_reachable: bool,
) -> bool:
    tools_dir = app_root / "tools"
    if tools_dir.is_symlink() or not tools_dir.is_dir():
        return False
    agent_tool_map = (
        {a: _agent_tool_refs(cfg) for a, cfg in graph.agent_configs.items()}
        if filter_reachable and graph is not None
        else {}
    )
    eligible_dirs: list[Path] = []
    for other_dir in sorted(tools_dir.iterdir()):
        if (
            other_dir.is_symlink()
            or not other_dir.is_dir()
            or other_dir == own_tool_dir
        ):
            continue
        if filter_reachable and graph is not None:
            other_cands = _tool_candidate_names_for_dir(other_dir)
            attaching = [
                a for a, refs in agent_tool_map.items() if other_cands & refs
            ]
            if attaching and not any(a in graph.reachable for a in attaching):
                continue
        eligible_dirs.append(other_dir)
    return _any_py_in_dirs_references_tools(eligible_dirs, candidate_names)


def _collect_guardrail_config_files(
    app_root: Path,
    guardrails_dir: Path,
    graph: AgentGraph | None,
    filter_reachable: bool,
) -> list[Path]:
    if not (filter_reachable and graph is not None):
        return [
            p
            for ext in CONFIG_EXTENSIONS
            for p in sorted(guardrails_dir.rglob(f"*{ext}"))
        ]
    gr_resources = discover_local_resources(app_root, "guardrails")
    app_grs, agent_grs = collect_attached_guardrails(graph, gr_resources)
    gr_files: list[Path] = []
    for res in gr_resources:
        if res not in app_grs and not any(
            a in graph.reachable for a in agent_grs.get(res, ())
        ):
            continue
        if res.report_path.is_file():
            gr_files.append(res.report_path)
        elif res.report_path.is_dir():
            for ext in CONFIG_EXTENSIONS:
                gr_files.extend(sorted(res.report_path.glob(f"*{ext}")))
    return gr_files


def _tool_referenced_in_guardrails(
    app_root: Path,
    candidate_names: set[str],
    graph: AgentGraph | None,
    filter_reachable: bool,
) -> bool:
    guardrails_dir = app_root / "guardrails"
    if guardrails_dir.is_symlink() or not guardrails_dir.is_dir():
        return False
    gr_files = _collect_guardrail_config_files(
        app_root, guardrails_dir, graph, filter_reachable
    )
    return any(
        (gr_data := _load_config_dict(gr_path)) is not None
        and _guardrail_invokes_tool(gr_data, candidate_names)
        for gr_path in gr_files
    )


def _is_tool_referenced_in_code_or_guardrails(
    app_root: Path,
    candidate_names: set[str],
    own_tool_dir: Path,
    graph: AgentGraph | None = None,
) -> bool:
    """Return True if candidate tool is referenced in code or guardrails."""
    filter_reachable = graph is not None and graph.root_agent_dir is not None
    return (
        _tool_referenced_in_agents(
            app_root, candidate_names, graph, filter_reachable
        )
        or _tool_referenced_in_other_tools(
            app_root, candidate_names, own_tool_dir, graph, filter_reachable
        )
        or _tool_referenced_in_guardrails(
            app_root, candidate_names, graph, filter_reachable
        )
    )


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

        rel = rel_to_project(cfg_path, context)

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


_FIELD_DATA_STORE_TOOL: tuple[str, str] = ("dataStoreTool", "data_store_tool")
_FIELD_DATA_STORE_SOURCE: tuple[str, str] = (
    "dataStoreSource",
    "data_store_source",
)
_FIELD_DATA_STORE_SOURCES: tuple[str, str] = (
    "dataStoreSources",
    "data_store_sources",
)
_FIELD_ENGINE_SOURCE: tuple[str, str] = ("engineSource", "engine_source")
_FIELD_DATA_STORE: tuple[str, str] = ("dataStore", "data_store")
_FIELD_CONNECTOR_CONFIG: tuple[str, str] = (
    "connectorConfig",
    "connector_config",
)
_FIELD_DATA_STORE_TYPE: tuple[str, str] = ("type", "type_")
_FIELD_FILTER_PARAM_BEHAVIOR: tuple[str, str] = (
    "filterParameterBehavior",
    "filter_parameter_behavior",
)
_FIELD_MODALITY_CONFIGS: tuple[str, str] = (
    "modalityConfigs",
    "modality_configs",
)
_FIELD_SNIPPETS_CONFIG: tuple[str, str] = ("snippetsConfig", "snippets_config")
_FIELD_REWRITER_CONFIG: tuple[str, str] = ("rewriterConfig", "rewriter_config")
_FIELD_SUMMARIZATION_CONFIG: tuple[str, str] = (
    "summarizationConfig",
    "summarization_config",
)
_FIELD_MODALITY_TYPE: tuple[str, str, str] = (
    "modalityType",
    "modality_type",
    "modality",
)

_FILTER_BEHAVIOR_ALWAYS_INCLUDE: tuple[typing.Any, ...] = ("ALWAYS_INCLUDE", 2)
_FILTER_BEHAVIOR_UNSPECIFIED: tuple[typing.Any, ...] = (
    None,
    "",
    "FILTER_PARAMETER_BEHAVIOR_UNSPECIFIED",
    0,
)
_MODALITY_AUDIO: tuple[typing.Any, ...] = ("AUDIO", 2)
_DATA_STORE_TYPE_CONNECTOR: tuple[typing.Any, ...] = ("CONNECTOR", 4)


def _load_data_store_tool_config(
    file_path: Path, content: str
) -> dict[str, typing.Any] | None:
    """Parse a JSON or YAML tool config file under tools/, else return None."""
    if (
        file_path.suffix.lower() not in CONFIG_EXTENSIONS
        or "tools" not in file_path.parts
    ):
        return None
    return parse_config_content(file_path, content)


def _append_source_filter(source: typing.Any, filters: list[str]) -> None:
    if isinstance(source, dict):
        s_filter = source.get("filter")
        if isinstance(s_filter, str) and s_filter:
            filters.append(s_filter)


def _iter_container_sources(
    container: dict[str, typing.Any],
) -> list[dict[str, typing.Any]]:
    """Collect dataStoreSource dicts from a tool or dataStoreTool mapping."""
    found: list[dict[str, typing.Any]] = []
    single = get_field(container, _FIELD_DATA_STORE_SOURCE)
    if isinstance(single, dict):
        found.append(single)
    for key in _FIELD_DATA_STORE_SOURCES:
        raw_list = container.get(key)
        if isinstance(raw_list, list):
            found.extend(item for item in raw_list if isinstance(item, dict))
    engine_source = get_field(container, _FIELD_ENGINE_SOURCE)
    if isinstance(engine_source, dict):
        for key in _FIELD_DATA_STORE_SOURCES:
            raw_list = engine_source.get(key)
            if isinstance(raw_list, list):
                found.extend(
                    item for item in raw_list if isinstance(item, dict)
                )
    return found


def _extract_data_store_filters(data: dict[str, typing.Any]) -> list[str]:
    """Collect non-empty filter strings from a dataStoreTool config dict."""
    filters: list[str] = []
    ds_tool = get_field(data, _FIELD_DATA_STORE_TOOL)
    containers: list[dict[str, typing.Any]] = [data]
    if isinstance(ds_tool, dict):
        containers.insert(0, ds_tool)
        _append_source_filter(ds_tool, filters)
    for container in containers:
        _append_source_filter(
            get_field(container, _FIELD_ENGINE_SOURCE), filters
        )
        for source in _iter_container_sources(container):
            _append_source_filter(source, filters)
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


def _is_connector_source(source: dict[str, typing.Any]) -> bool:
    """Return True if a dataStoreSource entry describes a connector store."""
    targets = [source]
    ds = get_field(source, _FIELD_DATA_STORE)
    if isinstance(ds, dict):
        targets.append(ds)
    for target in targets:
        if get_field(target, _FIELD_CONNECTOR_CONFIG):
            return True
        if (
            get_field(target, _FIELD_DATA_STORE_TYPE)
            in _DATA_STORE_TYPE_CONNECTOR
        ):
            return True
    return False


def _is_connector_data_store_tool(data: dict[str, typing.Any]) -> bool:
    """Return True if any configured dataStoreSource is a connector store."""
    containers: list[dict[str, typing.Any]] = [data]
    ds_tool = get_field(data, _FIELD_DATA_STORE_TOOL)
    if isinstance(ds_tool, dict):
        containers.insert(0, ds_tool)
    return any(
        _is_connector_source(src)
        for container in containers
        for src in _iter_container_sources(container)
    )


@rule("tools")
class DataStoreFilterParameterBehavior(Rule):
    id = "T018"
    name = "data-store-filter-parameter-behavior"
    description = (
        "Static-filtered dataStoreTool must not expose the filter parameter "
        "via ALWAYS_INCLUDE (or omit it on connector datastores)."
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        data = _load_data_store_tool_config(file_path, content)
        if data is None or not _extract_data_store_filters(data):
            return []

        raw_ds = get_field(data, _FIELD_DATA_STORE_TOOL)
        ds_tool = raw_ds if isinstance(raw_ds, dict) else {}
        behavior = get_field(ds_tool or data, _FIELD_FILTER_PARAM_BEHAVIOR)
        rel = str(file_path.relative_to(context.project_root))

        if behavior in _FILTER_BEHAVIOR_ALWAYS_INCLUDE:
            return [
                self.make_result(
                    file=rel,
                    severity=Severity.ERROR,
                    message=(
                        "dataStoreTool with a static filter sets "
                        "filterParameterBehavior to ALWAYS_INCLUDE (2), "
                        "exposing the filter parameter to the LLM."
                    ),
                    fix=(
                        "Set filterParameterBehavior to NEVER_INCLUDE "
                        "(or omit it for non-connector datastores)."
                    ),
                )
            ]

        if (
            behavior in _FILTER_BEHAVIOR_UNSPECIFIED
            and _is_connector_data_store_tool(data)
        ):
            return [
                self.make_result(
                    file=rel,
                    severity=Severity.WARNING,
                    message=(
                        "Connector dataStoreTool with a static filter leaves "
                        "filterParameterBehavior unspecified; CES defaults "
                        "to exposing the filter parameter for connector "
                        "datastores."
                    ),
                    fix="Set filterParameterBehavior to NEVER_INCLUDE.",
                )
            ]

        return []


def _find_audio_modality_config(
    mod_configs: typing.Any,
) -> dict[str, typing.Any] | None:
    """Locate the AUDIO (or enum 2) modality config block."""
    if isinstance(mod_configs, dict):
        candidate = get_field(mod_configs, _MODALITY_AUDIO)
        return candidate if isinstance(candidate, dict) else None
    if not isinstance(mod_configs, list):
        return None
    for mc in mod_configs:
        if (
            isinstance(mc, dict)
            and get_field(mc, _FIELD_MODALITY_TYPE) in _MODALITY_AUDIO
        ):
            return mc
    return None


@rule("tools")
class DataStoreVoiceModality(Rule):
    id = "T019"
    name = "data-store-voice-modality"
    description = (
        "Warns on voice deployments if dataStoreTool lacks AUDIO config."
    )
    default_severity = Severity.WARNING

    _DISABLED_VOICE_BLOCKS = (
        (_FIELD_REWRITER_CONFIG, "rewriter"),
        (_FIELD_SUMMARIZATION_CONFIG, "summarization"),
    )

    def _check_audio_gaps(
        self, rel: str, audio_conf: dict[str, typing.Any]
    ) -> list[LintResult]:
        results: list[LintResult] = []
        snippets = get_field(audio_conf, _FIELD_SNIPPETS_CONFIG)
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
        for field_keys, label in self._DISABLED_VOICE_BLOCKS:
            raw_block = get_field(audio_conf, field_keys)
            block = raw_block if isinstance(raw_block, dict) else {}
            if block.get("disabled") is not True:
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            f"{field_keys[0]}.disabled is not True in"
                            " AUDIO modalityConfig."
                        ),
                        fix=(
                            f"Disable {label} for Voice"
                            " deployments inside AUDIO."
                        ),
                    )
                )
        return results

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        data = _load_data_store_tool_config(file_path, content)
        if data is None or not is_voice_app(context):
            return []

        tool_name = file_path.parent.name
        raw_display = get_field(data, FIELD_DISPLAY_NAME, "")
        tool_display_name = raw_display if isinstance(raw_display, str) else ""
        if (
            context.all_tool_names
            and tool_name not in context.all_tool_names
            and tool_display_name not in context.all_tool_names
        ):
            return []

        ds_tool = get_field(data, _FIELD_DATA_STORE_TOOL)
        if not isinstance(ds_tool, dict):
            return []

        mod_configs = get_field(ds_tool, _FIELD_MODALITY_CONFIGS)
        audio_conf = _find_audio_modality_config(mod_configs)
        rel = str(file_path.relative_to(context.project_root))
        if audio_conf is None:
            return [
                self.make_result(
                    file=rel,
                    message=(
                        "dataStoreTool missing AUDIO entry in"
                        " modalityConfigs for a voice deployment."
                    ),
                    fix="Add an AUDIO configuration block to modalityConfigs.",
                )
            ]
        return self._check_audio_gaps(rel, audio_conf)


@rule("tools")
class ToolToolsetEnvVarParity(Rule):
    """Flag stale/dangling tool or toolset entries in ``environment.json``."""

    id = "T020"
    name = "tool-env-var-parity"
    description = (
        f"Complements A018 by warning when {DEFAULT_ENV_FILENAME} is missing "
        f"despite tool/toolset {ENV_VAR_PLACEHOLDER} placeholders, or contains "
        "stale/dangling tools and toolsets entries"
    )
    default_severity = Severity.WARNING
    target = "app_config"

    def _check_dangling_entries(
        self,
        app_root: Path,
        env_data: dict[str, typing.Any],
        env_rel: str,
    ) -> list[LintResult]:
        results: list[LintResult] = []
        for subdir, label in (("tools", "Tool"), ("toolsets", "Toolset")):
            raw_sec = env_data.get(subdir)
            if not isinstance(raw_sec, dict) or not raw_sec:
                continue
            resources = discover_local_resources(app_root, subdir)
            for env_key in sorted(k for k in raw_sec if isinstance(k, str)):
                if not match_local_resources(
                    env_key, subdir, resources, case_sensitive=True
                ):
                    results.append(
                        self.make_result(
                            file=env_rel,
                            message=(
                                f"{DEFAULT_ENV_FILENAME} '{subdir}' entry "
                                f"'{env_key}' does not match any "
                                f"{label.lower()} under {subdir}/."
                            ),
                            fix=(
                                f"Remove stale '{env_key}' from "
                                f"{DEFAULT_ENV_FILENAME} '{subdir}' or restore "
                                f"{subdir}/{env_key}."
                            ),
                        )
                    )
        return results

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        app_root = resolve_app_root(file_path, context)
        env_path = (
            context.env_file
            if context.env_file is not None
            else app_root / DEFAULT_ENV_FILENAME
        )
        env_rel = rel_to_project(env_path, context)
        if not env_path.exists():
            if context.env_file is not None:
                return []
            placeholders = collect_tool_and_toolset_env_var_placeholders(
                app_root
            )
            if not placeholders:
                return []
            return [
                self.make_result(
                    file=env_rel,
                    severity=Severity.WARNING,
                    message=(
                        f"Tools/toolsets reference '{ENV_VAR_PLACEHOLDER}' "
                        f"({len(placeholders)} placeholder(s)), but "
                        f"{DEFAULT_ENV_FILENAME} was not found."
                    ),
                    fix=(
                        f"Create {DEFAULT_ENV_FILENAME} or pass --env-file "
                        "before running cxas push."
                    ),
                )
            ]

        env_data = _load_config_dict(env_path)
        if env_data is None:
            return []
        return self._check_dangling_entries(app_root, env_data, env_rel)


def _is_valid_openapi_server_url(url: typing.Any) -> bool:
    """Validate an OpenAPI server URL string."""
    if not isinstance(url, str):
        return False
    stripped = url.strip()
    if not stripped:
        return False
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in stripped):
        return False
    if stripped == ENV_VAR_PLACEHOLDER or stripped.startswith("/"):
        return True
    if not stripped.lower().startswith(("http://", "https://")):
        return False
    try:
        parsed = urlparse(stripped)
        host = parsed.hostname
    except ValueError:
        return False
    return (
        parsed.scheme.lower() in ("http", "https")
        and bool(host)
        and not host.startswith(".")
    )


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

    def _validate_servers(
        self, rel: str, servers: typing.Any
    ) -> list[LintResult]:
        if not isinstance(servers, list):
            return [
                self.make_result(
                    file=rel,
                    message="OpenAPI schema 'servers' must be a list.",
                    fix="Define 'servers' as a list of {url: ...} items.",
                )
            ]
        results: list[LintResult] = []
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
                            f"relative '/' path, or '{ENV_VAR_PLACEHOLDER}'."
                        ),
                    )
                )
        return results

    def _check_schema_file(
        self, schema_path: Path, rel: str
    ) -> list[LintResult]:
        try:
            raw = schema_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return [
                self.make_result(
                    file=rel,
                    message=f"OpenAPI schema could not be read as UTF-8: {e}",
                    fix="Ensure the OpenAPI schema file is valid UTF-8.",
                )
            ]
        try:
            doc = yaml.safe_load(raw)
        except yaml.YAMLError as e:
            return [
                self.make_result(
                    file=rel,
                    message=f"OpenAPI schema is not valid YAML: {e}",
                    fix="Fix the YAML syntax error in the OpenAPI schema.",
                )
            ]
        if not isinstance(doc, dict):
            return [
                self.make_result(
                    file=rel,
                    message="OpenAPI schema must be a YAML mapping.",
                    fix="Provide a valid OpenAPI 3.0 YAML object.",
                )
            ]

        results: list[LintResult] = []
        paths = doc.get("paths")
        if not isinstance(paths, dict) or not paths:
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
            results.extend(self._validate_servers(rel, doc.get("servers")))
        return results

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        app_root = resolve_app_root(file_path, context)
        results: list[LintResult] = []
        seen_schemas: set[Path] = set()
        for _kind, _key, schema_path in discover_openapi_schema_paths(app_root):
            if schema_path in seen_schemas:
                continue
            seen_schemas.add(schema_path)
            results.extend(
                self._check_schema_file(
                    schema_path, rel_to_project(schema_path, context)
                )
            )
        return results
