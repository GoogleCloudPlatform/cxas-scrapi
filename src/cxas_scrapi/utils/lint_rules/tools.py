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

from cxas_scrapi.utils.linter import (
    LintContext,
    LintResult,
    Rule,
    Severity,
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


def _is_tool_referenced_in_code_or_guardrails(
    app_root: Path,
    candidate_names: set[str],
    own_tool_dir: Path,
) -> bool:
    """Return True if candidate tool is referenced in code or guardrails."""
    agents_dir = app_root / "agents"
    if agents_dir.exists():
        for py_path in agents_dir.rglob("*.py"):
            if _py_file_references_tools(py_path, candidate_names):
                return True

    tools_dir = app_root / "tools"
    if tools_dir.exists():
        for py_path in tools_dir.rglob("*.py"):
            if own_tool_dir in py_path.parents:
                continue
            if _py_file_references_tools(py_path, candidate_names):
                return True

    guardrails_dir = app_root / "guardrails"
    if guardrails_dir.exists():
        for gr_json in guardrails_dir.rglob("*.json"):
            try:
                gr_data = json.loads(
                    gr_json.read_text(encoding="utf-8", errors="replace")
                )
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(gr_data, dict):
                continue
            action = gr_data.get("action")
            if not isinstance(action, dict):
                continue
            tool_call = action.get("toolCall") or action.get("tool_call")
            if isinstance(tool_call, dict):
                tool_ref = str(tool_call.get("tool") or "")
                short_ref = tool_ref.rsplit("/", 1)[-1]
                if tool_ref in candidate_names or short_ref in candidate_names:
                    return True

    return False


@rule("tools")
class ToolDisplayNameUnreferenced(Rule):
    id = "T008"
    name = "tool-displayname-unreferenced"
    description = "Tool displayName not referenced by any agent's tools array"
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        tool_config, json_path = _load_tool_config(file_path)
        if not tool_config or json_path is None:
            return []

        display_name = tool_config.get("displayName", "")
        if not display_name:
            return []

        # tools/<name>/python_function/python_code.py → 4 levels to app root
        if file_path.suffix == ".json":
            app_root = file_path.parent.parent.parent
        else:
            app_root = file_path.parent.parent.parent.parent
        agents_dir = app_root / "agents"
        if not agents_dir.exists():
            return []

        candidate_names = {display_name, json_path.parent.name}
        referenced = any(
            bool(
                candidate_names
                & set(json.loads(agent_json.read_text()).get("tools", []))
            )
            for agent_dir in agents_dir.iterdir()
            if agent_dir.is_dir()
            for agent_json in [agent_dir / f"{agent_dir.name}.json"]
            if agent_json.exists()
        )
        if not referenced:
            referenced = _is_tool_referenced_in_code_or_guardrails(
                app_root, candidate_names, json_path.parent
            )

        if not referenced:
            rel = str(json_path.relative_to(context.project_root))
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
                        " relevant agent's JSON"
                        " config, or remove the"
                        " tool if unused"
                    ),
                )
            ]
        return []


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


@rule("tools")
class DataStoreFilterTemplateSyntax(Rule):
    id = "T017"
    name = "data-store-filter-template"
    description = "Detects template syntax in data store tools/sources filters."
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        if (
            not file_path.name.endswith(".json")
            or "tools" not in file_path.parts
        ):
            return []

        try:
            data = json.loads(content)
        except (ValueError, OSError):
            return []

        def _check_filter(f_str: str) -> bool:
            return f_str and ("{" in f_str or "}" in f_str)

        filters = []
        ds_tool = data.get("dataStoreTool") or {}
        if ds_tool:
            if "filter" in ds_tool:
                filters.append(ds_tool["filter"])
            for s in ds_tool.get("dataStoreSources", []):
                if s.get("filter"):
                    filters.append(s["filter"])
            if ds_tool.get("engineSource", {}).get("filter"):
                filters.append(ds_tool["engineSource"]["filter"])

        # Top-level fallback
        for s in data.get("dataStoreSources", []):
            if s.get("filter"):
                filters.append(s["filter"])
        if data.get("engineSource", {}).get("filter"):
            filters.append(data["engineSource"]["filter"])

        if any(_check_filter(f) for f in filters):
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
        rel = str(file_path.relative_to(context.project_root))
        if (
            not file_path.name.endswith(".json")
            or "tools" not in file_path.parts
        ):
            return []

        try:
            data = json.loads(content)
        except (ValueError, OSError):
            return []

        ds_tool = data.get("dataStoreTool") or {}

        has_filter = False
        if ds_tool.get("filter"):
            has_filter = True
        for s in ds_tool.get("dataStoreSources", []) + data.get(
            "dataStoreSources", []
        ):
            if s.get("filter"):
                has_filter = True
        if ds_tool.get("engineSource", {}).get("filter") or data.get(
            "engineSource", {}
        ).get("filter"):
            has_filter = True

        if not ds_tool and not has_filter:
            return []

        tool_dir_name = file_path.parent.name
        tool_display_name = data.get("displayName", "")

        is_brand_scoped = (
            has_filter
            or tool_dir_name.startswith("ds_")
            or tool_display_name.startswith("ds_")
        )

        if is_brand_scoped:
            behavior = ds_tool.get("filterParameterBehavior") or ds_tool.get(
                "filter_parameter_behavior"
            )
            if behavior not in ("NEVER_INCLUDE", 3):
                return [
                    self.make_result(
                        file=rel,
                        message=(
                            "Brand-scoped dataStoreTool (with static filter)"
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
        rel = str(file_path.relative_to(context.project_root))
        if (
            not file_path.name.endswith(".json")
            or "tools" not in file_path.parts
        ):
            return []

        tool_name = file_path.parent.name
        tool_display_name = ""

        import yaml  # noqa: PLC0415

        try:
            data = json.loads(content)
            tool_display_name = data.get("displayName", "")
        except (ValueError, OSError):
            return []

        if (
            context.all_tool_names
            and tool_name not in context.all_tool_names
            and tool_display_name not in context.all_tool_names
        ):
            return []

        has_voice = False
        if context.app_root:
            for dep_file in context.app_root.glob("deployments/*.yaml"):
                try:
                    dep = yaml.safe_load(dep_file.read_text("utf-8"))
                except Exception:
                    continue
                ct = dep.get("channelProfile", {}).get("channelType", "")
                if ct in (
                    "GOOGLE_TELEPHONY_PLATFORM",
                    "CONTACT_CENTER_AS_A_SERVICE",
                ):
                    has_voice = True
                    break

        if not has_voice:
            return []

        ds_tool = data.get("dataStoreTool")
        if not ds_tool:
            return []

        results = []
        mod_configs = (
            ds_tool.get("modalityConfigs")
            or ds_tool.get("modality_configs")
            or []
        )

        audio_conf = {}
        if isinstance(mod_configs, dict):
            audio_conf = mod_configs.get("AUDIO")
        elif isinstance(mod_configs, list):
            for mc in mod_configs:
                mt = (
                    mc.get("modalityType")
                    or mc.get("modality_type")
                    or mc.get("modality")
                )
                if mt == "AUDIO":
                    audio_conf = mc
                    break

        if not audio_conf:
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
            snippets = (
                audio_conf.get("snippetsConfig")
                or audio_conf.get("snippets_config")
                or {}
            )
            if not snippets:
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

            rewriter = (
                audio_conf.get("rewriterConfig")
                or audio_conf.get("rewriter_config")
                or {}
            )
            summarizer = (
                audio_conf.get("summarizationConfig")
                or audio_conf.get("summarization_config")
                or {}
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
