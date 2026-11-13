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

"""Static dependency graph and reverse blast-radius analyzer for CXAS apps."""

from __future__ import annotations

import ast
import json
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

RESOURCE_TYPES = ("variable", "tool", "toolset", "guardrail", "agent")
TYPE_ALIASES = {
    "var": "variable",
    "variables": "variable",
    "variable": "variable",
    "tool": "tool",
    "tools": "tool",
    "toolset": "toolset",
    "toolsets": "toolset",
    "guardrail": "guardrail",
    "guardrails": "guardrail",
    "agent": "agent",
    "agents": "agent",
}

INSTRUCTION_BUILTINS = frozenset({"current_date"})

# Matches {var}, {{var}}, {$session.params.var}, {session.params.var}
TEMPLATE_VAR_RE = re.compile(
    r"\{{1,2}(?!@)\$?(?:session\.params\.)?([a-zA-Z_][\w.]*)\}{1,2}"
)
SESSION_PARAMS_RE = re.compile(r"\$?session\.params\.([a-zA-Z_][\w.]*)")
TOOL_DIRECTIVE_RE = re.compile(r"\{@TOOL[:\s]+([^}]+)\}", re.IGNORECASE)
TOOLSET_DIRECTIVE_RE = re.compile(r"\{@TOOLSET[:\s]+([^}]+)\}", re.IGNORECASE)
AGENT_DIRECTIVE_RE = re.compile(r"\{@AGENT[:\s]+([^}]+)\}", re.IGNORECASE)
IDENTIFIER_RE = re.compile(r"\b([a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*)\b")

EVAL_VAR_MAP_KEYS = frozenset(
    {
        "inject_variables",
        "injectVariables",
        "updatedVariables",
        "updated_variables",
        "variableOverrides",
        "variable_overrides",
        "session_params",
        "sessionParams",
        "session_parameters",
        "sessionParameters",
        "common_session_parameters",
        "commonSessionParameters",
        "variables",
        "initial_variables",
        "initialVariables",
        "expected_variables",
        "expectedVariables",
    }
)

EVAL_TOOL_SINGLE_KEYS = frozenset(
    {
        "toolCall",
        "tool_call",
        "toolExpectation",
        "tool_expectation",
        "tool_called",
        "toolCalled",
        "tool_not_called",
        "toolNotCalled",
        "mockToolResponse",
        "mock_tool_response",
    }
)

EVAL_TOOL_LIST_KEYS = frozenset(
    {
        "toolCalls",
        "tool_calls",
        "expected_tool_calls",
        "expectedToolCalls",
        "toolExpectations",
        "tool_expectations",
        "mockToolResponses",
        "mock_tool_responses",
    }
)

EVAL_AGENT_KEYS = frozenset(
    {
        "agentTransfer",
        "agent_transfer",
        "agentResponse",
        "agent_response",
        "start_agent",
        "startAgent",
        "target_agent",
        "targetAgent",
        "entry_agent",
        "entryAgent",
        "agent_name",
        "agentName",
        "childAgent",
        "child_agent",
    }
)

CALLBACK_DIR_NAMES = (
    "before_model_callbacks",
    "after_model_callbacks",
    "before_agent_callbacks",
    "after_agent_callbacks",
    "before_tool_callbacks",
    "after_tool_callbacks",
)


@dataclass
class ReferenceEdge:
    """Represents a single dependency reference to a target resource."""

    target: str
    source_kind: str
    source_id: str
    file: str
    line: int | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "file": self.file,
            "line": self.line,
            "detail": self.detail,
        }


class _DictAccessibleMixin:
    """Mixin allowing both attribute and dictionary-style access."""

    def to_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __contains__(self, key: str) -> bool:
        return key in self.to_dict()

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def keys(self) -> Any:
        return self.to_dict().keys()


@dataclass
class ImpactAnalysisResult(_DictAccessibleMixin):
    """Blast-radius analysis result for a single target resource."""

    target: str
    resource_type: str
    resource_name: str
    defined_in: list[str] = field(default_factory=list)
    affected_agents: list[str] = field(default_factory=list)
    affected_callbacks: list[str] = field(default_factory=list)
    affected_tools: list[str] = field(default_factory=list)
    affected_instructions: list[str] = field(default_factory=list)
    affected_evaluations: list[str] = field(default_factory=list)
    references: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_dependents(self) -> int:
        return (
            len(self.affected_agents)
            + len(self.affected_callbacks)
            + len(self.affected_tools)
            + len(self.affected_instructions)
            + len(self.affected_evaluations)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "resource_type": self.resource_type,
            "resource_name": self.resource_name,
            "defined_in": self.defined_in,
            "affected_agents": self.affected_agents,
            "affected_callbacks": self.affected_callbacks,
            "affected_tools": self.affected_tools,
            "affected_instructions": self.affected_instructions,
            "affected_evaluations": self.affected_evaluations,
            "references": self.references,
            "total_dependents": self.total_dependents,
        }


@dataclass
class DiffImpactResult(_DictAccessibleMixin):
    """Blast-radius analysis result for a git diff."""

    git_ref: str
    changed_files: list[str] = field(default_factory=list)
    changed_resources: list[str] = field(default_factory=list)
    affected_agents: list[str] = field(default_factory=list)
    affected_callbacks: list[str] = field(default_factory=list)
    affected_tools: list[str] = field(default_factory=list)
    affected_instructions: list[str] = field(default_factory=list)
    affected_evaluations: list[str] = field(default_factory=list)
    per_resource_impact: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "git_ref": self.git_ref,
            "changed_files": self.changed_files,
            "changed_resources": self.changed_resources,
            "affected_agents": self.affected_agents,
            "affected_callbacks": self.affected_callbacks,
            "affected_tools": self.affected_tools,
            "affected_instructions": self.affected_instructions,
            "affected_evaluations": self.affected_evaluations,
            "per_resource_impact": self.per_resource_impact,
        }


@dataclass
class AppImpactSummary(_DictAccessibleMixin):
    """Complete dependency summary for an exported CXAS app."""

    resource_counts: dict[str, int] = field(default_factory=dict)
    resources: dict[str, list[str]] = field(default_factory=dict)
    high_impact_resources: list[dict[str, Any]] = field(default_factory=list)
    orphan_resources: list[str] = field(default_factory=list)

    @property
    def unreferenced_resources(self) -> list[str]:
        return self.orphan_resources

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_counts": self.resource_counts,
            "resources": self.resources,
            "high_impact_resources": self.high_impact_resources,
            "orphan_resources": self.orphan_resources,
            "unreferenced_resources": self.orphan_resources,
        }


class _PythonAstAnalyzer(ast.NodeVisitor):
    """AST visitor extracting state/variable, tool, and agent references."""

    _NON_CTX_HOLDERS = frozenset({"self", "cls", "runner", "engine"})

    def __init__(self) -> None:
        self.variables: list[tuple[str, str, int]] = []
        self.tools: list[tuple[str, int]] = []
        self.agents: list[tuple[str, int]] = []
        self._skip_subscripts: set[int] = set()
        self._scope_stack: list[tuple[set[str], set[str]]] = []
        self._module_shadows_state = False

    @classmethod
    def _is_state_or_vars_attr(cls, node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Attribute)
            and node.attr in ("state", "variables")
            and not (
                isinstance(node.value, ast.Name)
                and node.value.id in cls._NON_CTX_HOLDERS
            )
        )

    def _is_state_target(self, node: ast.AST) -> bool:
        if self._is_state_or_vars_attr(node):
            return True
        if isinstance(node, ast.Name):
            name = node.id
            for aliases, shadowed in reversed(self._scope_stack):
                if name in shadowed:
                    return False
                if name in aliases:
                    return True
            if (
                name in ("state", "variables")
                and not self._module_shadows_state
            ):
                return True
        return False

    @staticmethod
    def _is_tools_holder(node: ast.AST) -> bool:
        if isinstance(node, ast.Name) and node.id == "tools":
            return True
        return isinstance(node, ast.Attribute) and node.attr == "tools"

    @staticmethod
    def _is_excluded_tools_attr(node: ast.AST) -> bool:
        if isinstance(node, ast.Name) and node.id == "excluded_tools":
            return True
        return isinstance(node, ast.Attribute) and node.attr == "excluded_tools"

    @staticmethod
    def _const_str(node: ast.AST | None) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    def _extract_str_constants(self, node: ast.AST) -> list[str]:
        s = self._const_str(node)
        if s is not None:
            return [s]
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            out: list[str] = []
            for elt in node.elts:
                val = self._const_str(elt)
                if val is not None:
                    out.append(val)
            return out
        return []

    def visit_Module(self, node: ast.Module) -> None:
        stack: list[ast.AST] = [
            stmt
            for stmt in node.body
            if not isinstance(
                stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            )
        ]
        while stack:
            sub = stack.pop()
            if isinstance(sub, ast.Assign) and not self._is_state_or_vars_attr(
                sub.value
            ):
                for target in sub.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id in ("state", "variables")
                    ) or (
                        isinstance(target, ast.Tuple)
                        and any(
                            isinstance(elt, ast.Name)
                            and elt.id in ("state", "variables")
                            for elt in target.elts
                        )
                    ):
                        self._module_shadows_state = True
            for child in ast.iter_child_nodes(sub):
                if not isinstance(
                    child,
                    (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
                ):
                    stack.append(child)
        self.generic_visit(node)

    def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._scope_stack.append((set(), set()))
        try:
            self.generic_visit(node)
        finally:
            self._scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_func(node)

    def _record_target_binding(self, target: ast.AST, is_ctx: bool) -> None:
        if not self._scope_stack:
            return
        aliases, shadowed = self._scope_stack[-1]
        if isinstance(target, ast.Name):
            if is_ctx:
                aliases.add(target.id)
                shadowed.discard(target.id)
            else:
                shadowed.add(target.id)
                aliases.discard(target.id)
        elif isinstance(target, ast.Tuple):
            for elt in target.elts:
                if isinstance(elt, ast.Name):
                    shadowed.add(elt.id)
                    aliases.discard(elt.id)

    def _resolve_subscript_chain(
        self, node: ast.Subscript
    ) -> tuple[ast.AST, list[str]] | None:
        keys: list[str] = []
        curr: ast.AST = node
        while isinstance(curr, ast.Subscript):
            k = self._const_str(curr.slice)
            if k is None:
                return None
            keys.append(k)
            curr = curr.value
        keys.reverse()
        return curr, keys

    def _record_var_keys(self, kind: str, keys: list[str], lineno: int) -> None:
        if not keys:
            return
        top = keys[0]
        self.variables.append((kind, top, lineno))
        if len(keys) > 1:
            dotted = ".".join(keys)
            self.variables.append((kind, dotted, lineno))

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Subscript):
                chain = self._resolve_subscript_chain(target)
                if chain is not None and self._is_state_target(chain[0]):
                    self._record_var_keys("write", chain[1], target.lineno)
                    curr: ast.AST = target
                    while isinstance(curr, ast.Subscript):
                        self._skip_subscripts.add(id(curr))
                        curr = curr.value
            elif self._is_excluded_tools_attr(target):
                for tname in self._extract_str_constants(node.value):
                    self.tools.append((tname, node.lineno))
        self.generic_visit(node)
        is_ctx = self._is_state_or_vars_attr(node.value)
        for target in node.targets:
            self._record_target_binding(target, is_ctx)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        target = node.target
        if isinstance(target, ast.Subscript):
            chain = self._resolve_subscript_chain(target)
            if chain is not None and self._is_state_target(chain[0]):
                self._record_var_keys("write", chain[1], target.lineno)
                curr: ast.AST = target
                while isinstance(curr, ast.Subscript):
                    self._skip_subscripts.add(id(curr))
                    curr = curr.value
        elif self._is_excluded_tools_attr(target) and node.value is not None:
            for tname in self._extract_str_constants(node.value):
                self.tools.append((tname, node.lineno))
        self.generic_visit(node)
        if node.value is not None:
            self._record_target_binding(
                target, self._is_state_or_vars_attr(node.value)
            )

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        target = node.target
        if isinstance(target, ast.Subscript):
            chain = self._resolve_subscript_chain(target)
            if chain is not None and self._is_state_target(chain[0]):
                self._record_var_keys("write", chain[1], target.lineno)
                curr: ast.AST = target
                while isinstance(curr, ast.Subscript):
                    self._skip_subscripts.add(id(curr))
                    curr = curr.value
        elif self._is_excluded_tools_attr(target):
            for tname in self._extract_str_constants(node.value):
                self.tools.append((tname, node.lineno))
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if id(node) not in self._skip_subscripts:
            chain = self._resolve_subscript_chain(node)
            if chain is not None and self._is_state_target(chain[0]):
                self._record_var_keys("read", chain[1], node.lineno)
                curr: ast.AST = node
                while isinstance(curr, ast.Subscript):
                    self._skip_subscripts.add(id(curr))
                    curr = curr.value
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        operands = [node.left, *node.comparators]
        for left_op, op, right_op in zip(
            operands, node.ops, node.comparators, strict=False
        ):
            if isinstance(op, (ast.In, ast.NotIn)) and self._is_state_target(
                right_op
            ):
                key = self._const_str(left_op)
                if key is not None:
                    self.variables.append(("read", key, node.lineno))
            if self._is_tool_name_expr(left_op):
                for tname in self._extract_str_constants(right_op):
                    self.tools.append((tname, node.lineno))
            elif self._is_tool_name_expr(right_op):
                for tname in self._extract_str_constants(left_op):
                    self.tools.append((tname, node.lineno))
        self.generic_visit(node)

    @staticmethod
    def _is_tool_name_expr(node: ast.AST) -> bool:
        if isinstance(node, ast.Name) and node.id in ("tool", "tool_name"):
            return True
        return (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "tool"
            and node.attr == "name"
        )

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute):
            method = func.attr
            if self._is_state_target(func.value):
                if method in ("get", "pop") and node.args:
                    key = self._const_str(node.args[0])
                    if key is not None:
                        self.variables.append(("read", key, node.lineno))
                elif method == "setdefault" and node.args:
                    key = self._const_str(node.args[0])
                    if key is not None:
                        self.variables.append(("write", key, node.lineno))
                elif method == "update":
                    if node.args and isinstance(node.args[0], ast.Dict):
                        arg = node.args[0]
                        for k_node in arg.keys:
                            key = self._const_str(k_node)
                            if key is not None:
                                self.variables.append(
                                    ("write", key, node.lineno)
                                )
                    for kw in node.keywords:
                        if kw.arg is not None:
                            self.variables.append(
                                ("write", kw.arg, node.lineno)
                            )
            elif self._is_tools_holder(func.value):
                self.tools.append((method, node.lineno))
            elif (
                self._is_excluded_tools_attr(func.value)
                and method in ("append", "extend", "add", "update")
            ) or method in ("hide_tool", "unhide_tool"):
                for arg in node.args:
                    for tname in self._extract_str_constants(arg):
                        self.tools.append((tname, node.lineno))
            elif method == "transfer_to_agent":
                self._extract_agent_call_args(node)
        elif isinstance(func, ast.Name):
            if func.id in ("hide_tool", "unhide_tool"):
                for arg in node.args:
                    for tname in self._extract_str_constants(arg):
                        self.tools.append((tname, node.lineno))
            elif func.id == "transfer_to_agent":
                self._extract_agent_call_args(node)

        self.generic_visit(node)

    def _extract_agent_call_args(self, node: ast.Call) -> None:
        for arg in node.args:
            for aname in self._extract_str_constants(arg):
                self.agents.append((aname, node.lineno))
            if isinstance(arg, ast.Dict):
                for v_node in arg.values:
                    for aname in self._extract_str_constants(v_node):
                        self.agents.append((aname, node.lineno))
        for kw in node.keywords:
            for aname in self._extract_str_constants(kw.value):
                self.agents.append((aname, node.lineno))


def _load_structured_file(path: Path) -> Any:
    """Safely load a JSON or YAML file."""
    try:
        content = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            return json.loads(content)
        if path.suffix in (".yaml", ".yml"):
            return yaml.safe_load(content)
    except Exception:
        return None
    return None


def _find_app_root(app_dir: Path) -> Path:
    """Locate the CXAS app root directory."""
    resolved = app_dir.resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(
            f"App directory does not exist or is not a directory: {app_dir}"
        )
    for marker in ("app.json", "app.yaml", "agents"):
        if (resolved / marker).exists():
            return resolved
    for child in sorted(resolved.iterdir()):
        if child.is_dir() and not child.name.startswith("."):  # noqa: SIM102
            if (
                (child / "app.json").exists()
                or (child / "app.yaml").exists()
                or (child / "agents").exists()
            ):
                return child
    raise ValueError(
        f"No CXAS app configuration (app.json/app.yaml/agents/) found under "
        f"{app_dir}"
    )


def _rel_path(path: Path, base: Path) -> str:
    """Return POSIX relative path from base (or fallback to parent)."""
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        try:
            return path.resolve().relative_to(base.parent.resolve()).as_posix()
        except ValueError:
            return path.as_posix()


def _extract_var_decls_from_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return mapping of declared variable names and nested props to schema."""
    decls = (
        cfg.get("variableDeclarations")
        or cfg.get("variable_declarations")
        or cfg.get("predefinedVariableDeclarations")
        or cfg.get("predefined_variable_declarations")
        or []
    )
    out: dict[str, Any] = {}
    if isinstance(decls, list):
        for item in decls:
            if isinstance(item, dict) and item.get("name"):
                vname = str(item["name"])
                schema = item.get("schema") or {}
                out[vname] = item
                _collect_nested_schema_vars(vname, schema, out)
    elif isinstance(decls, dict):
        for vname, val in decls.items():
            out[str(vname)] = val

    env_vars = cfg.get("variables")
    if isinstance(env_vars, dict):
        for vname, val in env_vars.items():
            out.setdefault(str(vname), val)
    return out


def _collect_nested_schema_vars(
    prefix: str, schema: Any, out: dict[str, Any]
) -> None:
    if not isinstance(schema, dict):
        return
    props = schema.get("properties")
    if isinstance(props, dict):
        for prop_name, prop_schema in props.items():
            full_name = f"{prefix}.{prop_name}"
            out[full_name] = prop_schema
            _collect_nested_schema_vars(full_name, prop_schema, out)


class ImpactGraph:
    """Static dependency graph over an exported CXAS application directory."""

    def __init__(
        self,
        app_dir: str | Path,
        extra_resources: list[tuple[str, str, str | None, list[str]]]
        | None = None,
        extra_toolset_ops: dict[str, set[str]] | None = None,
    ) -> None:
        self.input_dir = Path(app_dir).resolve()
        self.app_root = _find_app_root(self.input_dir)
        self._extra_resources = extra_resources or []
        self._extra_toolset_ops = extra_toolset_ops or {}

        # Canonical resource sets per type
        self.resources: dict[str, set[str]] = {t: set() for t in RESOURCE_TYPES}
        # Declared/defined resources (used for orphan detection)
        self.declared_resources: dict[str, set[str]] = {
            t: set() for t in RESOURCE_TYPES
        }
        # Map canonical "type:name" -> list of relative file paths where defined
        self.defined_in: dict[str, list[str]] = defaultdict(list)
        # Alias lookup: (resource_type, alias_str) -> canonical_name
        self.aliases: dict[tuple[str, str], str] = {}
        self.aliases_lower: dict[tuple[str, str], str] = {}

        # Agent hierarchy and metadata
        self.root_agent: str | None = None
        # parent_agent -> set of child_agents
        self.agent_children: dict[str, set[str]] = defaultdict(set)
        # child_agent -> set of parent_agents
        self.agent_parents: dict[str, set[str]] = defaultdict(set)
        # agent -> list of callback relative paths
        self.agent_callbacks: dict[str, list[str]] = defaultdict(list)
        # callback relative path -> owning agent
        self.callback_to_agent: dict[str, str] = {}
        # agent -> instruction relative path
        self.agent_instruction: dict[str, str] = {}
        # instruction relative path -> owning agent (or None for global)
        self.instruction_to_agent: dict[str, str | None] = {}
        # agent -> set of bound canonical tool names
        self.agent_tools: dict[str, set[str]] = defaultdict(set)
        # tool -> set of agents binding it
        self.tool_to_agents: dict[str, set[str]] = defaultdict(set)
        # toolset -> set of resolved tool names (e.g. OpenAPI operations)
        self.toolset_tools: dict[str, set[str]] = defaultdict(set)
        # tool -> set of other tools it calls (DAG tool dependencies)
        self.tool_calls_tool: dict[str, set[str]] = defaultdict(set)
        # tool -> set of tools that call it (reverse DAG tool dependencies)
        self.tool_called_by_tool: dict[str, set[str]] = defaultdict(set)
        # callback -> set of tools it calls
        self.callback_calls_tools: dict[str, set[str]] = defaultdict(set)

        # Discovered files
        self.all_callbacks: list[str] = []
        self.all_instructions: list[str] = []
        self.all_evaluations: list[str] = []
        # eval relative path -> set of referenced canonical agents
        self.eval_agents: dict[str, set[str]] = defaultdict(set)
        # eval relative path -> set of referenced canonical tools
        self.eval_tools: dict[str, set[str]] = defaultdict(set)

        # Reverse reference edges: canonical "type:name" -> list[ReferenceEdge]
        self.edges_by_target: dict[str, list[ReferenceEdge]] = defaultdict(list)

        self._build()

    def _register_resource(
        self,
        rtype: str,
        canonical_name: str,
        defined_file: str | None = None,
        aliases: list[str] | None = None,
        declared: bool = True,
    ) -> str:
        self.resources[rtype].add(canonical_name)
        if declared:
            self.declared_resources[rtype].add(canonical_name)
        target_id = f"{rtype}:{canonical_name}"
        if defined_file and defined_file not in self.defined_in[target_id]:
            self.defined_in[target_id].append(defined_file)
        all_aliases = [canonical_name, *(aliases or [])]
        for alias in all_aliases:
            if not alias:
                continue
            cleaned = alias.strip()
            self.aliases[(rtype, cleaned)] = canonical_name
            self.aliases_lower[(rtype, cleaned.lower())] = canonical_name
            if "/" in cleaned:
                tail = cleaned.rsplit("/", 1)[-1]
                self.aliases.setdefault((rtype, tail), canonical_name)
                self.aliases_lower.setdefault(
                    (rtype, tail.lower()), canonical_name
                )
        return target_id

    def _add_edge(
        self,
        target: str,
        source_kind: str,
        source_id: str,
        file: str,
        line: int | None = None,
        detail: str = "",
    ) -> None:
        edge = ReferenceEdge(
            target=target,
            source_kind=source_kind,
            source_id=source_id,
            file=file,
            line=line,
            detail=detail,
        )
        self.edges_by_target[target].append(edge)

    def resolve_name(self, rtype: str, raw_name: str) -> str | None:
        """Resolve a raw name or display name to a canonical resource name."""
        if not raw_name:
            return None
        cleaned = raw_name.strip()
        if (rtype, cleaned) in self.aliases:
            return self.aliases[(rtype, cleaned)]
        if "/" in cleaned:
            tail = cleaned.rsplit("/", 1)[-1]
            if (rtype, tail) in self.aliases:
                return self.aliases[(rtype, tail)]
        if (rtype, cleaned.lower()) in self.aliases_lower:
            return self.aliases_lower[(rtype, cleaned.lower())]
        if "/" in cleaned:
            tail = cleaned.rsplit("/", 1)[-1].lower()
            if (rtype, tail) in self.aliases_lower:
                return self.aliases_lower[(rtype, tail)]
        return None

    def resolve_target(self, target_spec: str) -> tuple[str, str]:
        """Resolve '<type>:<name>' or bare '<name>' to (rtype, canonical)."""
        spec = target_spec.strip()
        if ":" in spec:
            raw_type, raw_name = spec.split(":", 1)
            rtype = TYPE_ALIASES.get(raw_type.strip().lower())
            if not rtype:
                raise ValueError(
                    f"Unknown resource type '{raw_type}' in target "
                    f"'{target_spec}'. Expected one of: "
                    f"{', '.join(RESOURCE_TYPES)}"
                )
            resolved = self.resolve_name(rtype, raw_name)
            if resolved is not None:
                return rtype, resolved
            # Check prefix match for dotted variable names
            cleaned_name = raw_name.strip()
            if rtype == "variable" and "." in cleaned_name:
                top_var = cleaned_name.split(".", 1)[0]
                if self.resolve_name("variable", top_var) is not None:
                    return "variable", cleaned_name
            raise ValueError(
                f"Target resource '{target_spec}' not found in app"
            )

        # Bare name auto-resolution across resource types
        for rtype in RESOURCE_TYPES:
            if (rtype, spec) in self.aliases:
                return rtype, self.aliases[(rtype, spec)]
        for rtype in RESOURCE_TYPES:
            if (rtype, spec.lower()) in self.aliases_lower:
                return rtype, self.aliases_lower[(rtype, spec.lower())]
        raise ValueError(f"Target resource '{target_spec}' not found in app")

    def _build(self) -> None:
        self._discover_definitions()
        self._extract_app_and_agent_references()
        self._extract_instruction_references()
        self._extract_python_references()
        self._extract_guardrail_references()
        self._extract_evaluation_references()

    def _discover_definitions(self) -> None:
        # 1. App config & environment config variable declarations
        for cfg_name in ("app.json", "app.yaml"):
            cfg_path = self.app_root / cfg_name
            if cfg_path.exists():
                rel = _rel_path(cfg_path, self.app_root)
                data = _load_structured_file(cfg_path)
                if isinstance(data, dict):
                    var_map = _extract_var_decls_from_config(data)
                    for vname in var_map:
                        self._register_resource(
                            "variable", vname, defined_file=rel, declared=True
                        )

        env_path = self.app_root / "environment.json"
        if env_path.exists():
            rel = _rel_path(env_path, self.app_root)
            data = _load_structured_file(env_path)
            if isinstance(data, dict):
                var_map = _extract_var_decls_from_config(data)
                for vname in var_map:
                    self._register_resource(
                        "variable", vname, defined_file=rel, declared=True
                    )

        # 2. Tools
        tools_dir = self.app_root / "tools"
        if tools_dir.is_dir():
            for d in sorted(tools_dir.iterdir()):
                if not d.is_dir() or d.name.startswith("."):
                    continue
                t_name = d.name
                t_aliases = [t_name, t_name.replace("_", " ")]
                json_path = d / f"{t_name}.json"
                if not json_path.exists():
                    candidates = list(d.glob("*.json"))
                    if candidates:
                        json_path = candidates[0]
                def_file = _rel_path(d, self.app_root)
                if json_path.exists():
                    def_file = _rel_path(json_path, self.app_root)
                    tdata = _load_structured_file(json_path)
                    if isinstance(tdata, dict):
                        for k in ("displayName", "display_name", "name"):
                            if isinstance(tdata.get(k), str):
                                t_aliases.append(tdata[k])
                        py_fn = (
                            tdata.get("pythonFunction")
                            or tdata.get("python_function")
                            or {}
                        )
                        if isinstance(py_fn, dict) and isinstance(
                            py_fn.get("name"), str
                        ):
                            t_aliases.append(py_fn["name"])
                py_code = d / "python_function" / "python_code.py"
                if py_code.exists() and not json_path.exists():
                    def_file = _rel_path(py_code, self.app_root)
                self._register_resource(
                    "tool",
                    t_name,
                    defined_file=def_file,
                    aliases=t_aliases,
                    declared=True,
                )

        # 3. Toolsets
        toolsets_dir = self.app_root / "toolsets"
        if toolsets_dir.is_dir():
            for d in sorted(toolsets_dir.iterdir()):
                if not d.is_dir() or d.name.startswith("."):
                    continue
                ts_name = d.name
                ts_aliases = [ts_name, ts_name.replace("_", " ")]
                json_path = d / f"{ts_name}.json"
                if not json_path.exists():
                    candidates = list(d.glob("*.json"))
                    if candidates:
                        json_path = candidates[0]
                def_file = _rel_path(d, self.app_root)
                if json_path.exists():
                    def_file = _rel_path(json_path, self.app_root)
                    tsdata = _load_structured_file(json_path)
                    if isinstance(tsdata, dict):
                        for k in ("displayName", "display_name", "name"):
                            if isinstance(tsdata.get(k), str):
                                ts_aliases.append(tsdata[k])
                self._register_resource(
                    "toolset",
                    ts_name,
                    defined_file=def_file,
                    aliases=ts_aliases,
                    declared=True,
                )
                # Discover OpenAPI operations if present
                schema_file = d / "open_api_toolset" / "open_api_schema.yaml"
                if schema_file.exists():
                    sdata = _load_structured_file(schema_file)
                    if isinstance(sdata, dict):
                        for _p, methods in (sdata.get("paths") or {}).items():
                            if not isinstance(methods, dict):
                                continue
                            for _m, details in methods.items():
                                if isinstance(details, dict) and details.get(
                                    "operationId"
                                ):
                                    op_tool = (
                                        f"{ts_name}_{details['operationId']}"
                                    )
                                    self.toolset_tools[ts_name].add(op_tool)

        # 4. Guardrails
        guardrails_dir = self.app_root / "guardrails"
        if guardrails_dir.is_dir():
            for d in sorted(guardrails_dir.iterdir()):
                if not d.is_dir() or d.name.startswith("."):
                    continue
                g_name = d.name
                g_aliases = [g_name, g_name.replace("_", " ")]
                json_path = d / f"{g_name}.json"
                if not json_path.exists():
                    candidates = list(d.glob("*.json"))
                    if candidates:
                        json_path = candidates[0]
                def_file = _rel_path(d, self.app_root)
                if json_path.exists():
                    def_file = _rel_path(json_path, self.app_root)
                    gdata = _load_structured_file(json_path)
                    if isinstance(gdata, dict):
                        for k in ("displayName", "display_name", "name"):
                            if isinstance(gdata.get(k), str):
                                g_aliases.append(gdata[k])
                self._register_resource(
                    "guardrail",
                    g_name,
                    defined_file=def_file,
                    aliases=g_aliases,
                    declared=True,
                )

        # 5. Agents
        agents_dir = self.app_root / "agents"
        if agents_dir.is_dir():
            for d in sorted(agents_dir.iterdir()):
                if not d.is_dir() or d.name.startswith("."):
                    continue
                a_name = d.name
                a_aliases = [a_name, a_name.replace("_", " ")]
                json_path = d / f"{a_name}.json"
                if not json_path.exists():
                    candidates = list(d.glob("*.json"))
                    if candidates:
                        json_path = candidates[0]
                def_file = _rel_path(d, self.app_root)
                if json_path.exists():
                    def_file = _rel_path(json_path, self.app_root)
                    adata = _load_structured_file(json_path)
                    if isinstance(adata, dict):
                        for k in ("displayName", "display_name", "name"):
                            if isinstance(adata.get(k), str):
                                a_aliases.append(adata[k])
                self._register_resource(
                    "agent",
                    a_name,
                    defined_file=def_file,
                    aliases=a_aliases,
                    declared=True,
                )
                inst_path = d / "instruction.txt"
                if inst_path.exists():
                    inst_rel = _rel_path(inst_path, self.app_root)
                    self.agent_instruction[a_name] = inst_rel
                    self.instruction_to_agent[inst_rel] = a_name
                    self.all_instructions.append(inst_rel)

                # Discover callbacks in agent directory
                seen_cb: set[str] = set()
                for cb_type in CALLBACK_DIR_NAMES:
                    cb_dir = d / cb_type
                    if cb_dir.is_dir():
                        for cb_sub in sorted(cb_dir.iterdir()):
                            py_p = cb_sub / "python_code.py"
                            if py_p.exists():
                                rel_cb = _rel_path(py_p, self.app_root)
                                if rel_cb not in seen_cb:
                                    seen_cb.add(rel_cb)
                                    self.agent_callbacks[a_name].append(rel_cb)
                                    self.callback_to_agent[rel_cb] = a_name
                                    self.all_callbacks.append(rel_cb)
                for py_p in sorted(d.rglob("*.py")):
                    rel_cb = _rel_path(py_p, self.app_root)
                    if rel_cb not in seen_cb:
                        seen_cb.add(rel_cb)
                        self.agent_callbacks[a_name].append(rel_cb)
                        self.callback_to_agent[rel_cb] = a_name
                        self.all_callbacks.append(rel_cb)

        global_inst = self.app_root / "global_instruction.txt"
        if global_inst.exists():
            g_rel = _rel_path(global_inst, self.app_root)
            self.instruction_to_agent[g_rel] = None
            self.all_instructions.append(g_rel)

        for rtype, rname, def_file, aliases in self._extra_resources:
            self._register_resource(
                rtype,
                rname,
                defined_file=def_file,
                aliases=aliases,
                declared=False,
            )
        for ts_name, ops in self._extra_toolset_ops.items():
            self.toolset_tools[ts_name].update(ops)

    def _extract_app_and_agent_references(self) -> None:
        # App-level references (rootAgent, guardrails)
        for cfg_name in ("app.json", "app.yaml"):
            cfg_path = self.app_root / cfg_name
            if not cfg_path.exists():
                continue
            rel = _rel_path(cfg_path, self.app_root)
            data = _load_structured_file(cfg_path)
            if not isinstance(data, dict):
                continue
            root_ref = data.get("rootAgent") or data.get("root_agent")
            if isinstance(root_ref, str):
                resolved_root = self.resolve_name("agent", root_ref)
                if resolved_root:
                    self.root_agent = resolved_root
                    self._add_edge(
                        f"agent:{resolved_root}",
                        "app_config",
                        "app",
                        rel,
                        detail="rootAgent",
                    )
            for g_ref in data.get("guardrails") or []:
                if isinstance(g_ref, str):
                    g_name = self.resolve_name("guardrail", g_ref) or g_ref
                    self._register_resource("guardrail", g_name, declared=False)
                    self._add_edge(
                        f"guardrail:{g_name}",
                        "app_config",
                        "app",
                        rel,
                        detail="app.guardrails",
                    )

        # Agent-level references
        agents_dir = self.app_root / "agents"
        if not agents_dir.is_dir():
            return
        for d in sorted(agents_dir.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            a_name = d.name
            json_path = d / f"{a_name}.json"
            if not json_path.exists():
                candidates = list(d.glob("*.json"))
                if not candidates:
                    continue
                json_path = candidates[0]
            rel = _rel_path(json_path, self.app_root)
            adata = _load_structured_file(json_path)
            if not isinstance(adata, dict):
                continue

            # Bound tools
            for t_ref in adata.get("tools") or []:
                if isinstance(t_ref, str):
                    t_name = self.resolve_name("tool", t_ref) or t_ref
                    self._register_resource("tool", t_name, declared=False)
                    self.agent_tools[a_name].add(t_name)
                    self.tool_to_agents[t_name].add(a_name)
                    self._add_edge(
                        f"tool:{t_name}",
                        "agent_config",
                        a_name,
                        rel,
                        detail="tools",
                    )

            # Bound toolsets
            for ts_entry in adata.get("toolsets") or []:
                ts_ref = (
                    ts_entry.get("toolset")
                    if isinstance(ts_entry, dict)
                    else ts_entry
                    if isinstance(ts_entry, str)
                    else None
                )
                if isinstance(ts_ref, str):
                    ts_name = self.resolve_name("toolset", ts_ref) or ts_ref
                    self._register_resource("toolset", ts_name, declared=False)
                    self._add_edge(
                        f"toolset:{ts_name}",
                        "agent_config",
                        a_name,
                        rel,
                        detail="toolsets",
                    )

            # Attached guardrails
            for g_ref in adata.get("guardrails") or []:
                if isinstance(g_ref, str):
                    g_name = self.resolve_name("guardrail", g_ref) or g_ref
                    self._register_resource("guardrail", g_name, declared=False)
                    self._add_edge(
                        f"guardrail:{g_name}",
                        "agent_config",
                        a_name,
                        rel,
                        detail="guardrails",
                    )

            # Child agents
            child_list = (
                adata.get("childAgents") or adata.get("child_agents") or []
            )
            for c_ref in child_list:
                if isinstance(c_ref, str):
                    c_name = self.resolve_name("agent", c_ref) or c_ref
                    self.agent_children[a_name].add(c_name)
                    self.agent_parents[c_name].add(a_name)
                    self._add_edge(
                        f"agent:{c_name}",
                        "agent_config",
                        a_name,
                        rel,
                        detail="childAgents",
                    )

            # Transfer rules (childAgent + condition variables)
            transfer_rules = (
                adata.get("transferRules") or adata.get("transfer_rules") or []
            )
            for tr in transfer_rules:
                if not isinstance(tr, dict):
                    continue
                target_agent_ref = (
                    tr.get("childAgent")
                    or tr.get("child_agent")
                    or tr.get("targetAgent")
                    or tr.get("target_agent")
                    or tr.get("agent")
                )
                if isinstance(target_agent_ref, str):
                    c_name = (
                        self.resolve_name("agent", target_agent_ref)
                        or target_agent_ref
                    )
                    self.agent_children[a_name].add(c_name)
                    self.agent_parents[c_name].add(a_name)
                    self._add_edge(
                        f"agent:{c_name}",
                        "transfer_rule",
                        a_name,
                        rel,
                        detail="transferRules",
                    )
                self._extract_transfer_rule_vars(tr, a_name, rel)

    def _extract_transfer_rule_vars(
        self, tr: dict[str, Any], agent_name: str, rel_file: str
    ) -> None:
        strings_to_scan: list[str] = []
        for k in ("condition", "expression"):
            if isinstance(tr.get(k), str):
                strings_to_scan.append(tr[k])
        det = tr.get("deterministicTransfer") or tr.get(
            "deterministic_transfer"
        )
        if isinstance(det, dict):
            for k in ("condition", "expression", "pythonCode", "python_code"):
                if isinstance(det.get(k), str):
                    strings_to_scan.append(det[k])

        for expr in strings_to_scan:
            for m in SESSION_PARAMS_RE.finditer(expr):
                self._record_var_reference(
                    m.group(1),
                    "transfer_rule",
                    agent_name,
                    rel_file,
                    detail="transferRules.condition",
                )
            for m in TEMPLATE_VAR_RE.finditer(expr):
                self._record_var_reference(
                    m.group(1),
                    "transfer_rule",
                    agent_name,
                    rel_file,
                    detail="transferRules.condition",
                )
            for m in IDENTIFIER_RE.finditer(expr):
                ident = m.group(1)
                top = ident.split(".", 1)[0]
                if top in self.resources["variable"]:
                    self._record_var_reference(
                        ident,
                        "transfer_rule",
                        agent_name,
                        rel_file,
                        detail="transferRules.condition",
                    )

    def _record_var_reference(
        self,
        var_path: str,
        source_kind: str,
        source_id: str,
        file: str,
        line: int | None = None,
        detail: str = "",
    ) -> None:
        cleaned = var_path.strip()
        if not cleaned or cleaned in INSTRUCTION_BUILTINS:
            return
        if cleaned.startswith("session.params."):
            cleaned = cleaned[len("session.params.") :]
        top = cleaned.split(".", 1)[0]
        self._register_resource("variable", top, declared=False)
        self._add_edge(
            f"variable:{top}",
            source_kind,
            source_id,
            file,
            line=line,
            detail=detail,
        )
        if "." in cleaned:
            self._register_resource("variable", cleaned, declared=False)
            self._add_edge(
                f"variable:{cleaned}",
                source_kind,
                source_id,
                file,
                line=line,
                detail=detail,
            )

    def _extract_instruction_references(self) -> None:
        for inst_rel, owner_agent in self.instruction_to_agent.items():
            inst_path = self.app_root / inst_rel
            if not inst_path.exists():
                continue
            content = inst_path.read_text(encoding="utf-8")
            source_id = owner_agent or "global_instruction"

            # Variable references: {var}, {{var}}, {$session.params.var}, etc.
            for m in TEMPLATE_VAR_RE.finditer(content):
                var_ref = m.group(1).strip()
                line = content[: m.start()].count("\n") + 1
                self._record_var_reference(
                    var_ref,
                    "instruction",
                    source_id,
                    inst_rel,
                    line=line,
                    detail="instruction_template",
                )
            for m in SESSION_PARAMS_RE.finditer(content):
                var_ref = m.group(1).strip()
                line = content[: m.start()].count("\n") + 1
                self._record_var_reference(
                    var_ref,
                    "instruction",
                    source_id,
                    inst_rel,
                    line=line,
                    detail="session.params",
                )

            # Tool references: {@TOOL: ...}
            for m in TOOL_DIRECTIVE_RE.finditer(content):
                raw_tool = m.group(1).strip()
                line = content[: m.start()].count("\n") + 1
                resolved_tool = self.resolve_name("tool", raw_tool)
                if resolved_tool:
                    self._add_edge(
                        f"tool:{resolved_tool}",
                        "instruction",
                        source_id,
                        inst_rel,
                        line=line,
                        detail="{@TOOL}",
                    )
                else:
                    # Check if it's a toolset or toolset operation prefix
                    resolved_ts = self.resolve_name("toolset", raw_tool)
                    if resolved_ts:
                        self._add_edge(
                            f"toolset:{resolved_ts}",
                            "instruction",
                            source_id,
                            inst_rel,
                            line=line,
                            detail="{@TOOL}",
                        )
                    else:
                        matched_ts = self._match_toolset_prefix(raw_tool)
                        if matched_ts:
                            self._add_edge(
                                f"toolset:{matched_ts}",
                                "instruction",
                                source_id,
                                inst_rel,
                                line=line,
                                detail="{@TOOL}",
                            )
                        else:
                            self._register_resource(
                                "tool", raw_tool, declared=False
                            )
                            self._add_edge(
                                f"tool:{raw_tool}",
                                "instruction",
                                source_id,
                                inst_rel,
                                line=line,
                                detail="{@TOOL}",
                            )

            # Toolset references: {@TOOLSET: ...}
            for m in TOOLSET_DIRECTIVE_RE.finditer(content):
                raw_ts = m.group(1).strip()
                line = content[: m.start()].count("\n") + 1
                resolved_ts = self.resolve_name("toolset", raw_ts) or raw_ts
                self._register_resource("toolset", resolved_ts, declared=False)
                self._add_edge(
                    f"toolset:{resolved_ts}",
                    "instruction",
                    source_id,
                    inst_rel,
                    line=line,
                    detail="{@TOOLSET}",
                )

            # Agent references: {@AGENT: ...}
            for m in AGENT_DIRECTIVE_RE.finditer(content):
                raw_agent = m.group(1).strip()
                line = content[: m.start()].count("\n") + 1
                resolved_agent = (
                    self.resolve_name("agent", raw_agent) or raw_agent
                )
                self._register_resource("agent", resolved_agent, declared=False)
                if owner_agent and resolved_agent != owner_agent:
                    self.agent_children[owner_agent].add(resolved_agent)
                    self.agent_parents[resolved_agent].add(owner_agent)
                self._add_edge(
                    f"agent:{resolved_agent}",
                    "instruction",
                    source_id,
                    inst_rel,
                    line=line,
                    detail="{@AGENT}",
                )

    def _match_toolset_prefix(self, raw_tool_name: str) -> str | None:
        for ts_name in self.resources["toolset"]:
            if raw_tool_name.startswith(f"{ts_name}_"):
                return ts_name
        return None

    def _extract_python_references(self) -> None:
        # 1. Agent callbacks
        for cb_rel in self.all_callbacks:
            cb_path = self.app_root / cb_rel
            owner_agent = self.callback_to_agent.get(cb_rel, "")
            self._analyze_python_file(
                cb_path, cb_rel, source_kind="callback", source_id=owner_agent
            )

        # 2. Python tools
        tools_dir = self.app_root / "tools"
        if tools_dir.is_dir():
            for d in sorted(tools_dir.iterdir()):
                if not d.is_dir() or d.name.startswith("."):
                    continue
                t_name = d.name
                for py_path in sorted(d.rglob("*.py")):
                    rel_py = _rel_path(py_path, self.app_root)
                    self._analyze_python_file(
                        py_path, rel_py, source_kind="tool", source_id=t_name
                    )

    def _analyze_python_file(
        self,
        py_path: Path,
        rel_path: str,
        source_kind: str,
        source_id: str,
    ) -> None:
        try:
            content = py_path.read_text(encoding="utf-8")
            tree = ast.parse(content)
        except (OSError, SyntaxError):
            return

        visitor = _PythonAstAnalyzer()
        visitor.visit(tree)

        for rw_kind, var_key, lineno in visitor.variables:
            self._record_var_reference(
                var_key,
                source_kind,
                source_id,
                rel_path,
                line=lineno,
                detail=f"state_{rw_kind}",
            )

        for raw_tool, lineno in visitor.tools:
            resolved_tool = self.resolve_name("tool", raw_tool)
            if resolved_tool:
                if source_kind == "tool" and source_id != resolved_tool:
                    self.tool_calls_tool[source_id].add(resolved_tool)
                    self.tool_called_by_tool[resolved_tool].add(source_id)
                elif source_kind == "callback":
                    self.callback_calls_tools[rel_path].add(resolved_tool)
                self._add_edge(
                    f"tool:{resolved_tool}",
                    source_kind,
                    source_id,
                    rel_path,
                    line=lineno,
                    detail="python_tool_ref",
                )
            else:
                resolved_ts = self.resolve_name(
                    "toolset", raw_tool
                ) or self._match_toolset_prefix(raw_tool)
                if resolved_ts:
                    self._add_edge(
                        f"toolset:{resolved_ts}",
                        source_kind,
                        source_id,
                        rel_path,
                        line=lineno,
                        detail="python_toolset_ref",
                    )
                else:
                    self._register_resource("tool", raw_tool, declared=False)
                    if source_kind == "callback":
                        self.callback_calls_tools[rel_path].add(raw_tool)
                    self._add_edge(
                        f"tool:{raw_tool}",
                        source_kind,
                        source_id,
                        rel_path,
                        line=lineno,
                        detail="python_tool_ref",
                    )

        for raw_agent, lineno in visitor.agents:
            resolved_agent = self.resolve_name("agent", raw_agent) or raw_agent
            self._register_resource("agent", resolved_agent, declared=False)
            if source_kind == "callback" and source_id:
                self.agent_children[source_id].add(resolved_agent)
                self.agent_parents[resolved_agent].add(source_id)
            self._add_edge(
                f"agent:{resolved_agent}",
                source_kind,
                source_id,
                rel_path,
                line=lineno,
                detail="transfer_to_agent",
            )

    def _extract_guardrail_references(self) -> None:
        guardrails_dir = self.app_root / "guardrails"
        if not guardrails_dir.is_dir():
            return
        for d in sorted(guardrails_dir.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            g_name = d.name
            json_path = d / f"{g_name}.json"
            if not json_path.exists():
                candidates = list(d.glob("*.json"))
                if not candidates:
                    continue
                json_path = candidates[0]
            rel = _rel_path(json_path, self.app_root)
            gdata = _load_structured_file(json_path)
            if not isinstance(gdata, dict):
                continue
            action = gdata.get("action")
            if isinstance(action, dict):
                transfer = action.get("transferAgent") or action.get(
                    "transfer_agent"
                )
                if isinstance(transfer, dict):
                    a_ref = (
                        transfer.get("agent")
                        or transfer.get("targetAgent")
                        or transfer.get("target_agent")
                    )
                    if isinstance(a_ref, str):
                        a_name = self.resolve_name("agent", a_ref) or a_ref
                        self._add_edge(
                            f"agent:{a_name}",
                            "guardrail",
                            g_name,
                            rel,
                            detail="action.transferAgent",
                        )
                t_call = action.get("toolCall") or action.get("tool_call")
                if isinstance(t_call, dict):
                    t_ref = (
                        t_call.get("tool")
                        or t_call.get("name")
                        or t_call.get("displayName")
                    )
                    if isinstance(t_ref, str):
                        t_name = self.resolve_name("tool", t_ref) or t_ref
                        self._add_edge(
                            f"tool:{t_name}",
                            "guardrail",
                            g_name,
                            rel,
                            detail="action.toolCall",
                        )

    def _discover_evaluation_files(self) -> list[tuple[Path, str]]:
        eval_dirs: list[Path] = []
        for sub in (
            "evaluations",
            "evaluationDatasets",
            "evaluationExpectations",
            "evaluation_expectations",
            "evals",
            "tests",
        ):
            p1 = self.app_root / sub
            if p1.is_dir():
                eval_dirs.append(p1)
            if self.input_dir != self.app_root:
                p2 = self.input_dir / sub
                if p2.is_dir() and p2 not in eval_dirs:
                    eval_dirs.append(p2)

        seen: set[Path] = set()
        out: list[tuple[Path, str]] = []
        for ed in eval_dirs:
            for fpath in sorted(ed.rglob("*")):
                if (
                    fpath.is_file()
                    and fpath.suffix in (".json", ".yaml", ".yml")
                    and fpath.resolve() not in seen
                ):
                    seen.add(fpath.resolve())
                    rel = _rel_path(fpath, self.app_root)
                    out.append((fpath, rel))
        return out

    def _extract_evaluation_references(self) -> None:
        for fpath, rel in self._discover_evaluation_files():
            self.all_evaluations.append(rel)
            data = _load_structured_file(fpath)
            if data is None:
                continue
            self._walk_eval_node(data, rel)

    def _walk_eval_node(self, node: Any, rel_file: str) -> None:
        if isinstance(node, dict):
            # Check if top-level or nested keys match tool test structure
            for k, v in node.items():
                k_str = str(k)
                if k_str in EVAL_VAR_MAP_KEYS:
                    self._extract_eval_vars(v, rel_file)
                elif k_str in EVAL_TOOL_SINGLE_KEYS:
                    self._extract_eval_tool_item(v, rel_file)
                elif k_str in EVAL_TOOL_LIST_KEYS:
                    if isinstance(v, list):
                        for item in v:
                            self._extract_eval_tool_item(item, rel_file)
                    elif isinstance(v, dict):
                        self._extract_eval_tool_item(v, rel_file)
                elif k_str in EVAL_AGENT_KEYS:
                    self._extract_eval_agent_item(v, rel_file)
                elif k_str in ("tool", "tool_name", "toolName", "function"):
                    if isinstance(v, str):
                        self._record_eval_tool_ref(v, rel_file)
                elif k_str == "agent" and isinstance(v, str):
                    resolved_agent = self.resolve_name("agent", v)
                    if resolved_agent:
                        self._record_eval_agent_ref(resolved_agent, rel_file)
                elif k_str in ("guardrail", "guardrails", "triggeredGuardrail"):
                    self._extract_eval_guardrail_item(v, rel_file)
                elif (
                    "tool_test" in rel_file
                    and isinstance(v, (dict, list))
                    and self.resolve_name("tool", k_str)
                ):
                    self._record_eval_tool_ref(k_str, rel_file)

                self._walk_eval_node(v, rel_file)
        elif isinstance(node, list):
            for item in node:
                self._walk_eval_node(item, rel_file)
        elif isinstance(node, str):
            for m in SESSION_PARAMS_RE.finditer(node):
                self._record_var_reference(
                    m.group(1),
                    "evaluation",
                    rel_file,
                    rel_file,
                    detail="eval_expression",
                )

    def _extract_eval_vars(self, val: Any, rel_file: str) -> None:
        if isinstance(val, dict):
            self._emit_eval_var_dict(val, rel_file, prefix="")
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    self._record_var_reference(
                        item,
                        "evaluation",
                        rel_file,
                        rel_file,
                        detail="eval_variable",
                    )
                elif isinstance(item, dict):
                    vname = (
                        item.get("name")
                        or item.get("key")
                        or item.get("variable")
                    )
                    if isinstance(vname, str):
                        self._record_var_reference(
                            vname,
                            "evaluation",
                            rel_file,
                            rel_file,
                            detail="eval_variable",
                        )
                    else:
                        self._emit_eval_var_dict(item, rel_file, prefix="")

    def _emit_eval_var_dict(
        self, d: dict[str, Any], rel_file: str, prefix: str
    ) -> None:
        for k, v in d.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            self._record_var_reference(
                path,
                "evaluation",
                rel_file,
                rel_file,
                detail="eval_variable",
            )
            if isinstance(v, dict):
                self._emit_eval_var_dict(v, rel_file, prefix=path)

    def _extract_eval_tool_item(self, item: Any, rel_file: str) -> None:
        if isinstance(item, str):
            self._record_eval_tool_ref(item, rel_file)
            return
        if isinstance(item, list):
            for sub in item:
                self._extract_eval_tool_item(sub, rel_file)
            return
        if not isinstance(item, dict):
            return

        # Check if item is a mapping of {tool_name: mock_response}
        found_explicit = False
        for k in (
            "action",
            "name",
            "tool",
            "toolName",
            "tool_name",
            "displayName",
        ):
            val = item.get(k)
            if isinstance(val, str):
                found_explicit = True
                if val == "transfer_to_agent":
                    for arg_key in ("args", "input", "parameters", "arguments"):
                        arg_val = item.get(arg_key)
                        if isinstance(arg_val, dict):
                            for ak in (
                                "agent_name",
                                "agentName",
                                "agent",
                                "target_agent",
                                "targetAgent",
                            ):
                                if isinstance(arg_val.get(ak), str):
                                    self._record_eval_agent_ref(
                                        arg_val[ak], rel_file
                                    )
                else:
                    self._record_eval_tool_ref(val, rel_file)

        for nested_k in (
            "toolCall",
            "tool_call",
            "tool_called",
            "tool_not_called",
        ):
            if nested_k in item:
                found_explicit = True
                self._extract_eval_tool_item(item[nested_k], rel_file)

        ts_ref = item.get("toolset") or item.get("toolsetTool")
        if isinstance(ts_ref, str):
            found_explicit = True
            resolved_ts = self.resolve_name("toolset", ts_ref) or ts_ref
            self._add_edge(
                f"toolset:{resolved_ts}",
                "evaluation",
                rel_file,
                rel_file,
                detail="eval_toolset",
            )
        elif isinstance(ts_ref, dict):
            found_explicit = True
            ts_id = ts_ref.get("toolset") or ts_ref.get("name")
            if isinstance(ts_id, str):
                resolved_ts = self.resolve_name("toolset", ts_id) or ts_id
                self._add_edge(
                    f"toolset:{resolved_ts}",
                    "evaluation",
                    rel_file,
                    rel_file,
                    detail="eval_toolset",
                )

        if not found_explicit:
            # Might be a dict keyed by tool name (e.g. mock_tool_responses)
            for k in item:
                resolved = self.resolve_name("tool", str(k))
                if resolved:
                    self._record_eval_tool_ref(resolved, rel_file)

    def _record_eval_tool_ref(self, raw_tool: str, rel_file: str) -> None:
        cleaned = raw_tool.strip()
        if not cleaned or cleaned in ("end_session", "customize_response"):
            return
        resolved_tool = self.resolve_name("tool", cleaned)
        if resolved_tool:
            self.eval_tools[rel_file].add(resolved_tool)
            self._add_edge(
                f"tool:{resolved_tool}",
                "evaluation",
                rel_file,
                rel_file,
                detail="eval_tool",
            )
            return
        resolved_ts = self.resolve_name(
            "toolset", cleaned
        ) or self._match_toolset_prefix(cleaned)
        if resolved_ts:
            self._add_edge(
                f"toolset:{resolved_ts}",
                "evaluation",
                rel_file,
                rel_file,
                detail="eval_toolset",
            )
            return
        self._register_resource("tool", cleaned, declared=False)
        self.eval_tools[rel_file].add(cleaned)
        self._add_edge(
            f"tool:{cleaned}",
            "evaluation",
            rel_file,
            rel_file,
            detail="eval_tool",
        )

    def _extract_eval_agent_item(self, item: Any, rel_file: str) -> None:
        if isinstance(item, str):
            self._record_eval_agent_ref(item, rel_file)
        elif isinstance(item, dict):
            for k in (
                "agent",
                "targetAgent",
                "target_agent",
                "childAgent",
                "child_agent",
                "name",
                "displayName",
                "display_name",
                "agentName",
                "agent_name",
            ):
                val = item.get(k)
                if isinstance(val, str):
                    self._record_eval_agent_ref(val, rel_file)
        elif isinstance(item, list):
            for sub in item:
                self._extract_eval_agent_item(sub, rel_file)

    def _record_eval_agent_ref(self, raw_agent: str, rel_file: str) -> None:
        cleaned = raw_agent.strip()
        if not cleaned:
            return
        resolved_agent = self.resolve_name("agent", cleaned) or cleaned
        self._register_resource("agent", resolved_agent, declared=False)
        self.eval_agents[rel_file].add(resolved_agent)
        self._add_edge(
            f"agent:{resolved_agent}",
            "evaluation",
            rel_file,
            rel_file,
            detail="eval_agent",
        )

    def _extract_eval_guardrail_item(self, item: Any, rel_file: str) -> None:
        if isinstance(item, str):
            resolved = self.resolve_name("guardrail", item) or item
            self._add_edge(
                f"guardrail:{resolved}",
                "evaluation",
                rel_file,
                rel_file,
                detail="eval_guardrail",
            )
        elif isinstance(item, list):
            for sub in item:
                self._extract_eval_guardrail_item(sub, rel_file)
        elif isinstance(item, dict):
            for k in ("guardrail", "name", "displayName", "display_name"):
                if isinstance(item.get(k), str):
                    resolved = (
                        self.resolve_name("guardrail", item[k]) or item[k]
                    )
                    self._add_edge(
                        f"guardrail:{resolved}",
                        "evaluation",
                        rel_file,
                        rel_file,
                        detail="eval_guardrail",
                    )

    def _get_agent_ancestors(self, agent_name: str) -> set[str]:
        visited: set[str] = set()
        queue = list(self.agent_parents.get(agent_name, ()))
        while queue:
            curr = queue.pop()
            if curr not in visited:
                visited.add(curr)
                queue.extend(self.agent_parents.get(curr, ()))
        return visited

    def _get_agent_descendants(self, agent_name: str) -> set[str]:
        visited: set[str] = set()
        queue = list(self.agent_children.get(agent_name, ()))
        while queue:
            curr = queue.pop()
            if curr not in visited:
                visited.add(curr)
                queue.extend(self.agent_children.get(curr, ()))
        return visited

    def _get_calling_tools_transitive(self, tool_names: set[str]) -> set[str]:
        visited = set(tool_names)
        queue = list(tool_names)
        while queue:
            curr = queue.pop()
            for caller in self.tool_called_by_tool.get(curr, ()):
                if caller not in visited:
                    visited.add(caller)
                    queue.append(caller)
        return visited

    def analyze_target(self, target_spec: str) -> ImpactAnalysisResult:
        """Compute direct and transitive blast radius for a resource target."""
        rtype, rname = self.resolve_target(target_spec)
        canonical_target = f"{rtype}:{rname}"

        # Collect edges matching target (plus nested children if variable)
        direct_edges = list(self.edges_by_target.get(canonical_target, []))
        if rtype == "variable":
            prefix = f"variable:{rname}."
            for edge_target, edges in self.edges_by_target.items():
                if edge_target.startswith(prefix):
                    direct_edges.extend(edges)
            if "." in rname:
                top_var = rname.split(".", 1)[0]
                for edge in self.edges_by_target.get(f"variable:{top_var}", []):
                    if edge not in direct_edges:
                        direct_edges.append(edge)

        affected_tools: set[str] = set()
        affected_callbacks: set[str] = set()
        affected_instructions: set[str] = set()
        direct_agents: set[str] = set()
        caller_agents: set[str] = set()
        affected_evaluations: set[str] = set()

        if rtype == "tool":
            affected_tools.add(rname)
        elif rtype == "toolset":
            affected_tools.update(self.toolset_tools.get(rname, set()))
        elif rtype == "agent":
            direct_agents.add(rname)
            if rname in self.agent_instruction:
                affected_instructions.add(self.agent_instruction[rname])
            affected_callbacks.update(self.agent_callbacks.get(rname, ()))
            affected_tools.update(self.agent_tools.get(rname, ()))

        for edge in direct_edges:
            if edge.source_kind == "instruction":
                affected_instructions.add(edge.file)
                owner = self.instruction_to_agent.get(edge.file)
                if owner:
                    if rtype == "agent":
                        caller_agents.add(owner)
                    else:
                        direct_agents.add(owner)
                else:
                    # global_instruction.txt affects all agents
                    direct_agents.update(self.resources["agent"])
            elif edge.source_kind == "callback":
                affected_callbacks.add(edge.file)
                if edge.source_id:
                    if rtype == "agent":
                        caller_agents.add(edge.source_id)
                    else:
                        direct_agents.add(edge.source_id)
            elif edge.source_kind == "tool":
                affected_tools.add(edge.source_id)
            elif edge.source_kind in ("agent_config", "transfer_rule"):
                if rtype == "agent":
                    caller_agents.add(edge.source_id)
                else:
                    direct_agents.add(edge.source_id)
            elif edge.source_kind == "app_config":
                if rtype == "guardrail":
                    direct_agents.update(self.resources["agent"])
                elif rtype == "agent":
                    direct_agents.add(rname)
            elif edge.source_kind == "guardrail":
                # Guardrail references this tool or agent
                g_edges = self.edges_by_target.get(
                    f"guardrail:{edge.source_id}", []
                )
                target_set = (
                    caller_agents if rtype == "agent" else direct_agents
                )
                for ge in g_edges:
                    if ge.source_kind == "app_config":
                        target_set.update(self.resources["agent"])
                    elif ge.source_kind == "agent_config":
                        target_set.add(ge.source_id)
            elif edge.source_kind == "evaluation":
                affected_evaluations.add(edge.file)

        # If target is a guardrail, also include instructions/callbacks of
        # directly attached agents and any tools/agents the guardrail invokes
        if rtype == "guardrail":
            for a_name in list(direct_agents):
                if a_name in self.agent_instruction:
                    affected_instructions.add(self.agent_instruction[a_name])
                affected_callbacks.update(self.agent_callbacks.get(a_name, ()))
            for t_name in self.resources["tool"]:
                for te in self.edges_by_target.get(f"tool:{t_name}", []):
                    if te.source_kind == "guardrail" and te.source_id == rname:
                        affected_tools.add(t_name)
            for a_name in self.resources["agent"]:
                for ae in self.edges_by_target.get(f"agent:{a_name}", []):
                    if ae.source_kind == "guardrail" and ae.source_id == rname:
                        direct_agents.add(a_name)

        # Expand DAG tool callers transitively
        if affected_tools:
            affected_tools = self._get_calling_tools_transitive(affected_tools)
            if rtype != "agent":
                for t_name in affected_tools:
                    direct_agents.update(self.tool_to_agents.get(t_name, ()))
                    for cb_rel, cb_tools in self.callback_calls_tools.items():
                        if t_name in cb_tools:
                            affected_callbacks.add(cb_rel)
                            cb_owner = self.callback_to_agent.get(cb_rel)
                            if cb_owner:
                                direct_agents.add(cb_owner)
                    if rtype in ("tool", "toolset"):
                        t_edges = self.edges_by_target.get(f"tool:{t_name}", [])
                        for te in t_edges:
                            if te.source_kind == "instruction":
                                affected_instructions.add(te.file)

        # Compute transitive agent impact:
        # - Descendants of directly affected agents (parent -> child)
        # - Ancestors of directly affected agents (child -> parent)
        descendant_agents: set[str] = set()
        ancestor_agents: set[str] = set(caller_agents)
        for a_name in direct_agents:
            descendant_agents.update(self._get_agent_descendants(a_name))
            ancestor_agents.update(self._get_agent_ancestors(a_name))
        for c_name in list(caller_agents):
            ancestor_agents.update(self._get_agent_ancestors(c_name))

        all_affected_agents = (
            direct_agents | descendant_agents | ancestor_agents
        )

        # Determine affected evaluations:
        # 1. Evals referencing any affected tool
        for eval_rel, ev_tools in self.eval_tools.items():
            if ev_tools & affected_tools:
                affected_evaluations.add(eval_rel)

        # 2. Evals referencing affected agents (direct, descendant, or
        #    ancestor when the eval does not target an unrelated sibling)
        lineage_agents = direct_agents | descendant_agents
        for eval_rel in self.all_evaluations:
            ev_agents = set(self.eval_agents.get(eval_rel, ()))
            for ev_tool in self.eval_tools.get(eval_rel, ()):
                ev_agents.update(self.tool_to_agents.get(ev_tool, ()))

            if not ev_agents and self.root_agent:
                ev_agents.add(self.root_agent)

            if ev_agents & lineage_agents:
                affected_evaluations.add(eval_rel)
            elif ev_agents & ancestor_agents:
                # Only include ancestor eval if it doesn't target an unrelated
                # sibling sub-agent outside lineage_agents | ancestor_agents
                non_ancestor_refs = ev_agents - ancestor_agents
                if not non_ancestor_refs or (
                    non_ancestor_refs & lineage_agents
                ):
                    affected_evaluations.add(eval_rel)

        defined_files = list(self.defined_in.get(canonical_target, []))
        if not defined_files and rtype == "variable" and "." in rname:
            top_var = rname.split(".", 1)[0]
            defined_files = list(self.defined_in.get(f"variable:{top_var}", []))

        return ImpactAnalysisResult(
            target=canonical_target,
            resource_type=rtype,
            resource_name=rname,
            defined_in=sorted(defined_files),
            affected_agents=sorted(all_affected_agents),
            affected_callbacks=sorted(affected_callbacks),
            affected_tools=sorted(affected_tools),
            affected_instructions=sorted(affected_instructions),
            affected_evaluations=sorted(affected_evaluations),
            references=[e.to_dict() for e in direct_edges],
        )


def analyze_target(app_dir: str | Path, target: str) -> ImpactAnalysisResult:
    """Analyze the direct and transitive blast radius of a single target."""
    graph = ImpactGraph(app_dir)
    return graph.analyze_target(target)


def _run_git(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def _load_git_file_structured(
    repo_root: Path, git_ref: str, repo_rel_path: str
) -> dict[str, Any] | None:
    res = _run_git(repo_root, ["show", f"{git_ref}:{repo_rel_path}"])
    if res.returncode != 0 or not res.stdout:
        return None
    try:
        if repo_rel_path.endswith(".json"):
            data = json.loads(res.stdout)
        else:
            data = yaml.safe_load(res.stdout)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _diff_config_variables(
    old_cfg: dict[str, Any] | None, new_cfg: dict[str, Any] | None
) -> list[str]:
    old_vars = _extract_var_decls_from_config(old_cfg or {})
    new_vars = _extract_var_decls_from_config(new_cfg or {})
    changed: list[str] = []
    all_keys = sorted(set(old_vars.keys()) | set(new_vars.keys()))
    for k in all_keys:
        if k not in old_vars or k not in new_vars or old_vars[k] != new_vars[k]:
            changed.append(f"variable:{k}")
    return changed


def _extract_git_resource_aliases(
    repo_root: Path,
    git_ref: str,
    repo_rel: str,
    app_rel: str,
    rtype: str,
    res_name: str,
) -> tuple[str, str, str, list[str]] | None:
    """Extract aliases for a resource JSON file from git_ref."""
    old_data = _load_git_file_structured(repo_root, git_ref, repo_rel)
    if not isinstance(old_data, dict):
        return None
    aliases = [res_name, res_name.replace("_", " ")]
    for k in ("displayName", "display_name", "name"):
        if isinstance(old_data.get(k), str):
            aliases.append(old_data[k])
    if rtype == "tool":
        py_fn = (
            old_data.get("pythonFunction")
            or old_data.get("python_function")
            or {}
        )
        if isinstance(py_fn, dict) and isinstance(py_fn.get("name"), str):
            aliases.append(py_fn["name"])
    return rtype, res_name, app_rel, aliases


def analyze_git_diff(
    app_dir: str | Path, git_ref: str = "HEAD"
) -> DiffImpactResult:
    """Compute combined blast radius for files changed against a git ref."""
    input_dir = Path(app_dir).resolve()
    app_root = _find_app_root(input_dir)

    top_res = _run_git(app_root, ["rev-parse", "--show-toplevel"])
    if top_res.returncode != 0:
        raise ValueError(
            f"Directory '{app_root}' is not inside a git repository: "
            f"{top_res.stderr.strip()}"
        )
    repo_root = Path(top_res.stdout.strip()).resolve()

    diff_res = _run_git(repo_root, ["diff", "--name-only", git_ref])
    if diff_res.returncode != 0:
        raise ValueError(
            f"Failed to run 'git diff --name-only {git_ref}': "
            f"{diff_res.stderr.strip()}"
        )

    untracked_res = _run_git(
        repo_root, ["ls-files", "--others", "--exclude-standard"]
    )
    raw_repo_paths: set[str] = set()
    for line in diff_res.stdout.splitlines():
        if line.strip():
            raw_repo_paths.add(line.strip())
    if untracked_res.returncode == 0:
        for line in untracked_res.stdout.splitlines():
            if line.strip():
                raw_repo_paths.add(line.strip())

    extra_resources: list[tuple[str, str, str | None, list[str]]] = []
    extra_toolset_ops: dict[str, set[str]] = defaultdict(set)
    dir_to_rtype = {
        "agents": "agent",
        "tools": "tool",
        "toolsets": "toolset",
        "guardrails": "guardrail",
    }
    for repo_rel in sorted(raw_repo_paths):
        abs_file = (repo_root / repo_rel).resolve()
        try:
            app_rel = abs_file.relative_to(app_root).as_posix()
        except ValueError:
            try:
                app_rel = abs_file.relative_to(input_dir).as_posix()
            except ValueError:
                continue
        parts = Path(app_rel).parts
        if len(parts) >= 2 and parts[0] in dir_to_rtype:
            rtype = dir_to_rtype[parts[0]]
            res_name = parts[1]
            if app_rel.endswith(".json"):
                extracted = _extract_git_resource_aliases(
                    repo_root, git_ref, repo_rel, app_rel, rtype, res_name
                )
                if extracted is not None:
                    extra_resources.append(extracted)
            elif rtype == "toolset" and app_rel.endswith(
                ("open_api_schema.yaml", "open_api_schema.yml")
            ):
                sdata = _load_git_file_structured(repo_root, git_ref, repo_rel)
                if isinstance(sdata, dict):
                    for _p, methods in (sdata.get("paths") or {}).items():
                        if not isinstance(methods, dict):
                            continue
                        for _m, details in methods.items():
                            if isinstance(details, dict) and details.get(
                                "operationId"
                            ):
                                op_tool = f"{res_name}_{details['operationId']}"
                                extra_toolset_ops[res_name].add(op_tool)

    graph = ImpactGraph(
        app_dir,
        extra_resources=extra_resources,
        extra_toolset_ops=extra_toolset_ops,
    )

    changed_files: list[str] = []
    changed_resources: list[str] = []
    affected_agents: set[str] = set()
    affected_callbacks: set[str] = set()
    affected_tools: set[str] = set()
    affected_instructions: set[str] = set()
    affected_evaluations: set[str] = set()

    def _add_changed_res(res_id: str) -> None:
        if res_id not in changed_resources:
            changed_resources.append(res_id)

    for repo_rel in sorted(raw_repo_paths):
        abs_file = (repo_root / repo_rel).resolve()
        try:
            app_rel = abs_file.relative_to(app_root).as_posix()
        except ValueError:
            try:
                app_rel = abs_file.relative_to(graph.input_dir).as_posix()
            except ValueError:
                continue

        changed_files.append(app_rel)
        parts = Path(app_rel).parts
        if not parts:
            continue

        top = parts[0]
        if top in ("app.json", "app.yaml"):
            old_cfg = _load_git_file_structured(repo_root, git_ref, repo_rel)
            new_cfg = (
                _load_structured_file(abs_file) if abs_file.exists() else {}
            )
            for v_id in _diff_config_variables(
                old_cfg, new_cfg if isinstance(new_cfg, dict) else {}
            ):
                _add_changed_res(v_id)
                _, v_name = v_id.split(":", 1)
                graph._register_resource("variable", v_name, declared=False)

            old_guards = set((old_cfg or {}).get("guardrails") or [])
            new_guards = set(
                (new_cfg if isinstance(new_cfg, dict) else {}).get("guardrails")
                or []
            )
            for g_ref in sorted(old_guards ^ new_guards):
                if isinstance(g_ref, str):
                    g_name = graph.resolve_name("guardrail", g_ref) or g_ref
                    graph._register_resource(
                        "guardrail", g_name, declared=False
                    )
                    _add_changed_res(f"guardrail:{g_name}")

        elif top == "environment.json":
            old_env = _load_git_file_structured(repo_root, git_ref, repo_rel)
            new_env = (
                _load_structured_file(abs_file) if abs_file.exists() else {}
            )
            for v_id in _diff_config_variables(
                old_env, new_env if isinstance(new_env, dict) else {}
            ):
                _add_changed_res(v_id)
                _, v_name = v_id.split(":", 1)
                graph._register_resource("variable", v_name, declared=False)

        elif top == "global_instruction.txt":
            affected_instructions.add("global_instruction.txt")
            for a_name in graph.resources["agent"]:
                _add_changed_res(f"agent:{a_name}")

        elif top == "agents" and len(parts) >= 2:
            a_name = parts[1]
            graph._register_resource("agent", a_name, declared=False)
            _add_changed_res(f"agent:{a_name}")
            if app_rel.endswith("instruction.txt"):
                affected_instructions.add(app_rel)
            elif app_rel.endswith(".py"):
                affected_callbacks.add(app_rel)

        elif top == "tools" and len(parts) >= 2:
            t_name = parts[1]
            graph._register_resource("tool", t_name, declared=False)
            _add_changed_res(f"tool:{t_name}")

        elif top == "toolsets" and len(parts) >= 2:
            ts_name = parts[1]
            graph._register_resource("toolset", ts_name, declared=False)
            _add_changed_res(f"toolset:{ts_name}")

        elif top == "guardrails" and len(parts) >= 2:
            g_name = parts[1]
            graph._register_resource("guardrail", g_name, declared=False)
            _add_changed_res(f"guardrail:{g_name}")

        elif top in (
            "evaluations",
            "evaluationDatasets",
            "evaluationExpectations",
            "evaluation_expectations",
            "evals",
            "tests",
        ):
            affected_evaluations.add(app_rel)

    per_resource_impact: dict[str, dict[str, Any]] = {}
    for res_id in changed_resources:
        res_impact = graph.analyze_target(res_id)
        per_resource_impact[res_id] = res_impact.to_dict()
        affected_agents.update(res_impact.affected_agents)
        affected_callbacks.update(res_impact.affected_callbacks)
        affected_tools.update(res_impact.affected_tools)
        affected_instructions.update(res_impact.affected_instructions)
        affected_evaluations.update(res_impact.affected_evaluations)

    return DiffImpactResult(
        git_ref=git_ref,
        changed_files=sorted(changed_files),
        changed_resources=changed_resources,
        affected_agents=sorted(affected_agents),
        affected_callbacks=sorted(affected_callbacks),
        affected_tools=sorted(affected_tools),
        affected_instructions=sorted(affected_instructions),
        affected_evaluations=sorted(affected_evaluations),
        per_resource_impact=per_resource_impact,
    )


def analyze_app_summary(app_dir: str | Path) -> AppImpactSummary:
    """Compute a complete dependency summary and orphan report for an app."""
    graph = ImpactGraph(app_dir)

    resources_by_type = {
        rtype: sorted(graph.resources[rtype]) for rtype in RESOURCE_TYPES
    }
    resource_counts = {
        "variables": len(resources_by_type["variable"]),
        "tools": len(resources_by_type["tool"]),
        "toolsets": len(resources_by_type["toolset"]),
        "guardrails": len(resources_by_type["guardrail"]),
        "agents": len(resources_by_type["agent"]),
        "callbacks": len(graph.all_callbacks),
        "instructions": len(graph.all_instructions),
        "evaluations": len(graph.all_evaluations),
    }

    high_impact: list[dict[str, Any]] = []
    orphan_resources: list[str] = []

    for rtype in RESOURCE_TYPES:
        for rname in sorted(graph.resources[rtype]):
            # Skip nested dotted variable sub-paths in top-level orphan check
            if rtype == "variable" and "." in rname:
                continue
            target_id = f"{rtype}:{rname}"
            res = graph.analyze_target(target_id)
            direct_refs = graph.edges_by_target.get(target_id, [])
            if rtype == "variable":
                prefix = f"variable:{rname}."
                for k, v in graph.edges_by_target.items():
                    if k.startswith(prefix):
                        direct_refs = [*direct_refs, *v]

            is_declared = rname in graph.declared_resources[rtype]
            if is_declared and not direct_refs:
                if rtype == "agent" and (
                    rname == graph.root_agent
                    or len(graph.resources["agent"]) == 1
                ):
                    pass
                else:
                    orphan_resources.append(target_id)

            high_impact.append(
                {
                    "target": target_id,
                    "resource_type": rtype,
                    "resource_name": rname,
                    "reference_count": len(direct_refs),
                    "dependent_count": res.total_dependents,
                    "affected_agents": res.affected_agents,
                    "affected_evaluations": res.affected_evaluations,
                }
            )

    high_impact.sort(
        key=lambda item: (
            -int(item["dependent_count"]),
            -int(item["reference_count"]),
            str(item["target"]),
        )
    )

    return AppImpactSummary(
        resource_counts=resource_counts,
        resources=resources_by_type,
        high_impact_resources=high_impact,
        orphan_resources=sorted(orphan_resources),
    )
