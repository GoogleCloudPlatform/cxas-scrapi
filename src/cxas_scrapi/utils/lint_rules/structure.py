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

"""Structure validation rules (S002-S019).

Validates app structure and cross-references using the existing
``cxas_scrapi.utils.validator.Validator`` for structural checks, plus
custom rules for cross-referencing (e.g., instruction references a tool
not in the agent's tool list, agent hierarchy cycles and orphans, or
configs reference missing/unused guardrails, toolsets, or code/schema
files).
"""

import re
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from cxas_scrapi.utils.linter import (
    APP_CONFIG_NAMES,
    CONFIG_EXTENSIONS,
    FIELD_CHILD_AGENTS,
    AgentGraph,
    LintContext,
    LintResult,
    LocalResource,
    Rule,
    Severity,
    ToolsetValidationBehavior,
    _build_agent_ref_resolver,
    _extract_dict_agent_refs,
    _find_dir_config,
    _load_config_dict,
    collect_attached_guardrails,
    discover_local_resources,
    extract_resource_ref_candidates,
    get_field,
    get_toolset_tools,
    match_local_resources,
    parse_config_content,
    rel_to_project,
    resolve_agent_graph,
    resolve_app_root,
    resolve_safe_app_file,
    rule,
)


@rule("structure")
class AgentToolReferences(Rule):
    """Instruction references tools not in the agent's tool list.

    When an instruction tells the LLM to call a tool that isn't in the
    agent's tool list, the LLM can't call it — it silently improvises.
    """

    id = "S002"
    name = "agent-tool-references"
    description = (
        "Instruction references tools that exist in the agent's tool list"
    )
    default_severity = Severity.ERROR
    target = "instruction"

    TOOL_REF_PATTERN = re.compile(r"\{@TOOL[:\s]+([^}]+)\}", re.IGNORECASE)

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        agent_dir = file_path.parent
        agent_json = _find_dir_config(agent_dir)
        if agent_json is None:
            return []

        agent_config = _load_config_dict(agent_json)
        if agent_config is None:
            return []

        app_root = file_path.parent.parent.parent

        raw_tools = agent_config.get("tools")
        known_tools = (
            {t for t in raw_tools if isinstance(t, str)}
            if isinstance(raw_tools, list)
            else set()
        ) | context.platform_tools

        bypass_prefixes = set()

        # Resolve toolsets and add their tools to known_tools
        raw_toolsets = agent_config.get("toolsets")
        for ts_entry in raw_toolsets if isinstance(raw_toolsets, list) else []:
            if isinstance(ts_entry, dict):
                toolset_name = ts_entry.get("toolset")
                allowed_tool_ids = ts_entry.get("toolIds") or ts_entry.get(
                    "tool_ids"
                )
                if toolset_name:
                    res = get_toolset_tools(
                        app_root, toolset_name, allowed_tool_ids
                    )
                    if res.behavior == ToolsetValidationBehavior.BYPASS:
                        # Skip operation-level checks for MCP/Connector toolsets
                        bypass_prefixes.add(f"{toolset_name}_")
                    else:
                        known_tools.update(res.tools)

        known_tools_lower = {t.lower() for t in known_tools}

        referenced = {
            match.group(1).strip()
            for match in self.TOOL_REF_PATTERN.finditer(content)
        }

        # Filter out referenced tools matching bypass prefixes
        if bypass_prefixes:
            referenced = {
                ref
                for ref in referenced
                if not any(ref.startswith(pfx) for pfx in bypass_prefixes)
            }

        missing = {t for t in referenced if t.lower() not in known_tools_lower}
        if not missing:
            return []

        # Build line index once for all missing tools
        lines = content.splitlines()
        results = []
        for tool_name in missing:
            line_num = next(
                (i for i, line in enumerate(lines, 1) if tool_name in line),
                None,
            )
            results.append(
                self.make_result(
                    str(file_path),
                    (
                        "Instruction references"
                        f" tool '{tool_name}'"
                        " but it's not in the"
                        " agent's tool list."
                    ),
                    line=line_num,
                    fix=(
                        f"Add '{tool_name}' to"
                        " the tools list in"
                        f" {agent_json.name},"
                        " or remove the"
                        " reference from the"
                        " instruction."
                    ),
                )
            )
        return results


def _resolve_path(app_dir: Path, relative_path: str) -> Path:
    """Resolve a relative path against app_dir, with subdirectory fallback."""
    candidate = app_dir / relative_path
    if candidate.exists():
        return candidate
    for child in app_dir.iterdir():
        if child.is_dir() and (child / relative_path).exists():
            return child / relative_path
    return candidate


_CALLBACK_TYPES = [
    "beforeAgentCallbacks",
    "afterAgentCallbacks",
    "beforeModelCallbacks",
    "afterModelCallbacks",
    "beforeToolCallbacks",
    "afterToolCallbacks",
]


@rule("structure")
class CallbackFileReferences(Rule):
    """Agent JSON references callback files that don't exist."""

    id = "S003"
    name = "callback-file-references"
    description = "Callback code files referenced in agent JSON exist on disk"
    default_severity = Severity.ERROR
    target = "agent_config"

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        agent_config = parse_config_content(file_path, content)
        if agent_config is None:
            return []

        results = []
        code_paths = [
            cb.get("pythonCode", "")
            for cb_type in _CALLBACK_TYPES
            for cb in (agent_config.get(cb_type) or [])
            if isinstance(cb, dict)
        ]
        for code_path in filter(None, code_paths):
            resolved = _resolve_path(context.app_dir, code_path)
            if not resolved.exists():
                results.append(
                    self.make_result(
                        str(file_path),
                        f"Callback references '{code_path}' but file not found",
                        fix=(
                            "Create the callback"
                            " file or fix the path"
                            " in the agent JSON"
                        ),
                    )
                )
        return results


def _extract_child_agent_candidates(
    child_entry: object,
) -> tuple[set[str], str]:
    """Return ``(lookup_candidates, display_label)`` for a childAgents entry."""
    if isinstance(child_entry, dict):
        raws = _extract_dict_agent_refs(child_entry)
        cands = {
            c
            for raw in raws
            for c in extract_resource_ref_candidates(raw, "agents")
        }
        return cands, (raws[0] if raws else str(child_entry))
    if isinstance(child_entry, str):
        return (
            extract_resource_ref_candidates(child_entry, "agents"),
            child_entry,
        )
    return set(), str(child_entry)


@rule("structure")
class ChildAgentReferences(Rule):
    """Agent config references child agents that don't exist."""

    id = "S004"
    name = "child-agent-references"
    description = (
        "Child agent references in agent JSON point to existing agents"
    )
    default_severity = Severity.ERROR
    target = "agent_config"

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        agent_config = parse_config_content(file_path, content)
        if agent_config is None:
            return []

        raw_children = get_field(agent_config, FIELD_CHILD_AGENTS)
        if not isinstance(raw_children, list):
            return []

        app_root = resolve_app_root(file_path, context)
        resolve_ref = context.get_agent_ref_resolver(app_root)
        valid_agents = context.all_agent_names | context.all_agent_display_names
        rel = rel_to_project(file_path, context)

        results = []
        for child_entry in raw_children:
            if resolve_ref(child_entry) is not None:
                continue
            candidates, child_label = _extract_child_agent_candidates(
                child_entry
            )
            if candidates & valid_agents:
                continue
            results.append(
                self.make_result(
                    rel,
                    (
                        "References child agent"
                        f" '{child_label}' but no"
                        " agent directory found."
                        " Available agents:"
                        f" {sorted(valid_agents)}"
                    ),
                    fix="Create the agent directory or fix the reference",
                )
            )
        return results


@rule("structure")
class StrictAgentPathLayout(Rule):
    id = "S005"
    name = "strict-agent-path-layout"
    description = (
        "Agent config relative paths must be app-relative and "
        "prefixed with agents/{agent_name}/"
    )
    default_severity = Severity.ERROR
    target = "agent_config"

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        agent_name = file_path.stem
        agent_config = parse_config_content(file_path, content)
        if agent_config is None:
            return []

        results = []

        # 1. Check instruction path
        instruction_path = agent_config.get("instruction")
        if instruction_path:
            expected_prefix = f"agents/{agent_name}/"
            if not instruction_path.startswith(expected_prefix):
                results.append(
                    self.make_result(
                        str(file_path),
                        (
                            f"Agent instruction path '{instruction_path}' "
                            "must be relative to the app root and start "
                            f"with '{expected_prefix}'"
                        ),
                        fix=(
                            f"Change '{instruction_path}' to "
                            f"'{expected_prefix}instruction.txt'"
                        ),
                    )
                )

        # 2. Check callbacks paths
        for cb_type in _CALLBACK_TYPES:
            callbacks = agent_config.get(cb_type, [])
            for i, cb in enumerate(callbacks):
                code_path = cb.get("pythonCode")
                if code_path:
                    expected_prefix = f"agents/{agent_name}/"
                    if not code_path.startswith(expected_prefix):
                        results.append(
                            self.make_result(
                                str(file_path),
                                (
                                    "Agent callback pythonCode path "
                                    f"'{code_path}' in {cb_type}[{i}] "
                                    "must be relative to the app root and "
                                    f"start with '{expected_prefix}'"
                                ),
                                fix=(
                                    f"Change '{code_path}' to start "
                                    f"with '{expected_prefix}'"
                                ),
                            )
                        )

        return results


@rule("structure")
class StrictToolPathLayout(Rule):
    id = "S006"
    name = "strict-tool-path-layout"
    description = (
        "Tool config relative paths must be app-relative and "
        "prefixed with tools/{tool_name}/"
    )
    default_severity = Severity.ERROR
    target = "tool_config"

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        tool_name = file_path.name
        config_data, target_path = _load_resource_dir_config(file_path)
        if not config_data or target_path is None:
            return []

        results = []
        python_function = config_data.get("pythonFunction", {})
        if isinstance(python_function, dict):
            code_path = python_function.get("pythonCode")
            if code_path:
                expected_prefix = f"tools/{tool_name}/"
                if not code_path.startswith(expected_prefix):
                    results.append(
                        self.make_result(
                            str(target_path),
                            (
                                f"Tool pythonCode path '{code_path}' must be "
                                "relative to the app root and start with "
                                f"'{expected_prefix}'"
                            ),
                            fix=(
                                f"Change '{code_path}' to start with "
                                f"'{expected_prefix}'"
                            ),
                        )
                    )

        return results


@rule("structure")
class SubAgentSingleParent(Rule):
    """Sub-agents have at most one parent agent (strict tree constraint).

    The platform enforces a strict tree constraint where each sub-agent can have
    at most one parent. If a sub-agent is declared in the childAgents list of
    multiple parent agents, the deployment fails.

    Attributes:
        id: Unique identifier for the rule (S007).
        name: Human-readable name of the rule.
        description: Description of what the rule checks.
        default_severity: Default severity if not overridden (ERROR).
        target: Target file type (agent_config).
    """

    id = "S007"
    name = "sub-agent-single-parent"
    description = "Sub-agents have at most one parent agent"
    default_severity = Severity.ERROR
    target = "agent_config"

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        """Check if the agent has multiple parents in the context.

        Args:
            file_path: Path to the agent config file.
            content: Content of the agent config file (unused).
            context: Shared lint context containing the agent_to_parents map.

        Returns:
            A list of LintResult objects if violations are found.
        """
        agent_name = file_path.stem
        parents = context.agent_to_parents.get(agent_name, set())
        if len(parents) > 1:
            return [
                self.make_result(
                    str(file_path),
                    (
                        f"Agent '{agent_name}' is referenced as a child by "
                        f"multiple parents: {sorted(parents)}. "
                        "Each sub-agent must have at most one parent."
                    ),
                    fix=(
                        "Remove the agent from the childAgents list of "
                        "all but one parent."
                    ),
                )
            ]
        return []


# Guardrail types the platform allows at most one of per app, mapped to
# the config keys (camelCase and snake_case) that identify each type.
_SINGLETON_GUARDRAIL_TYPES = {
    "llmPromptSecurity": ("llmPromptSecurity", "llm_prompt_security"),
}


def _load_guardrail_config(guardrail_dir: Path) -> dict | None:
    """Load ``<name>.{json,yaml,yml}`` from a guardrail directory."""
    cfg_path = _find_dir_config(guardrail_dir)
    return _load_config_dict(cfg_path) if cfg_path is not None else None


@rule("structure")
class SingletonGuardrailTypes(Rule):
    """At most one guardrail of each singleton type exists per app.

    The platform allows only one guardrail of certain types (currently
    ``llmPromptSecurity``) per app. The console UI enforces the limit,
    but the App Import API used by ``cxas push`` does not — importing
    duplicates leaves the app un-editable in the console ("Too many llm
    prompt security guardrails, the limit is 1").
    """

    id = "S008"
    name = "singleton-guardrail-types"
    description = (
        "At most one guardrail of each singleton type (llmPromptSecurity)"
    )
    default_severity = Severity.ERROR
    target = "guardrail_config"

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        guardrail_dir = file_path if file_path.is_dir() else file_path.parent
        config = _load_guardrail_config(guardrail_dir)
        if config is None:
            return []

        results = []
        for type_name, keys in _SINGLETON_GUARDRAIL_TYPES.items():
            if not any(k in config for k in keys):
                continue
            duplicates = []
            for sibling in sorted(guardrail_dir.parent.iterdir()):
                if not sibling.is_dir() or sibling.name == guardrail_dir.name:
                    continue
                sibling_config = _load_guardrail_config(sibling)
                if sibling_config and any(k in sibling_config for k in keys):
                    duplicates.append(sibling.name)
            if duplicates:
                results.append(
                    self.make_result(
                        str(guardrail_dir),
                        (
                            f"Guardrail '{guardrail_dir.name}' has type "
                            f"'{type_name}', but so do: {duplicates}. "
                            "The platform allows at most one guardrail "
                            "of this type per app."
                        ),
                        fix=(
                            f"Keep a single '{type_name}' guardrail and "
                            "remove the others."
                        ),
                    )
                )
        return results


def _load_resource_dir_config(
    resource_dir: Path,
) -> tuple[dict | None, Path | None]:
    """Load ``<name>.{json,yaml,yml}`` from a resource directory."""
    cfg_path = _find_dir_config(resource_dir)
    if cfg_path is None:
        return None, None
    return _load_config_dict(cfg_path), cfg_path


def _add_config_identifiers(cfg: dict | None, known: set[str]) -> None:
    """Add ``displayName`` and ``name`` (plus last segment) from ``cfg``."""
    if not isinstance(cfg, dict):
        return
    disp = cfg.get("displayName") or cfg.get("display_name")
    if isinstance(disp, str) and disp:
        known.add(disp)
    cfg_name = cfg.get("name")
    if isinstance(cfg_name, str) and cfg_name:
        known.add(cfg_name)
        known.add(cfg_name.rsplit("/", 1)[-1])


def _collect_known_resource_names(app_root: Path, subdir: str) -> set[str]:
    """Collect valid resource identifiers (dir, stem, displayName, name)."""
    parent = app_root / subdir
    if not parent.exists() or not parent.is_dir():
        return set()
    known: set[str] = set()
    for entry in parent.iterdir():
        if entry.is_symlink() or entry.name.startswith("."):
            continue
        if entry.is_dir():
            known.add(entry.name)
            known.add(entry.name.replace("_", " "))
            cfg, _ = _load_resource_dir_config(entry)
            _add_config_identifiers(cfg, known)
        elif entry.is_file() and entry.suffix.lower() in CONFIG_EXTENSIONS:
            known.add(entry.stem)
            known.add(entry.stem.replace("_", " "))
            _add_config_identifiers(_load_config_dict(entry), known)
    return known


def _is_missing_local_resource(ref: str, subdir: str, known: set[str]) -> bool:
    """Return True if ``ref`` is a local reference missing from ``known``."""
    if ref in known:
        return False
    m = re.match(
        rf"^projects/[^/]+/locations/[^/]+/apps/[^/]+/{re.escape(subdir)}/([^/]+)$",
        ref,
    )
    if m:
        return m.group(1) not in known
    return "/" not in ref


def _is_file_path_ref(val: object) -> bool:
    """Return True if val looks like a relative file path reference."""
    return (
        isinstance(val, str)
        and bool(val.strip())
        and "://" not in val
        and "\n" not in val
        and not any(c.isspace() for c in val)
        and val.endswith((".txt", ".py", ".yaml", ".yml", ".json"))
    )


def _referenced_file_exists(
    rel_path: str,
    resource_dir: Path,
    app_root: Path,
    context: LintContext,
) -> bool:
    """Check if an app-relative file path exists on disk under ``app_root``."""
    del resource_dir, context
    p = Path(rel_path)
    if p.is_absolute() or ".." in p.parts:
        return False
    return (app_root / p).is_file()


@rule("structure")
class AppResourceReferences(Rule):
    """App config references missing guardrails or globalInstruction."""

    id = "S010"
    name = "app-resource-references"
    description = (
        "Guardrails and globalInstruction file referenced in app config exist"
    )
    default_severity = Severity.ERROR
    target = "app_config"

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        app_config = parse_config_content(file_path, content)
        if app_config is None:
            return []

        app_root = resolve_app_root(file_path, context)
        known_guardrails = _collect_known_resource_names(app_root, "guardrails")
        results: list[LintResult] = []

        for gr in app_config.get("guardrails") or []:
            if isinstance(gr, str) and _is_missing_local_resource(
                gr, "guardrails", known_guardrails
            ):
                results.append(
                    self.make_result(
                        str(file_path),
                        (
                            f"App config references guardrail '{gr}' "
                            "but no matching guardrail was found in "
                            "guardrails/."
                        ),
                        fix=(
                            f"Create guardrails/{gr}/ or remove '{gr}' "
                            f"from {file_path.name}."
                        ),
                    )
                )

        global_inst = app_config.get("globalInstruction") or app_config.get(
            "global_instruction"
        )
        if _is_file_path_ref(global_inst) and not _referenced_file_exists(
            global_inst, file_path.parent, app_root, context
        ):
            results.append(
                self.make_result(
                    str(file_path),
                    (
                        "App config globalInstruction references "
                        f"'{global_inst}' but file not found"
                    ),
                    fix=(
                        f"Create '{global_inst}' or fix the path in "
                        f"{file_path.name}."
                    ),
                )
            )

        return results


AppGuardrailReferences = AppResourceReferences


@rule("structure")
class AgentResourceReferences(Rule):
    """Agent config references missing guardrails, toolsets, or instruction."""

    id = "S011"
    name = "agent-resource-references"
    description = (
        "Guardrails, toolsets, and instruction file referenced in agent "
        "JSON exist on disk"
    )
    default_severity = Severity.ERROR
    target = "agent_config"

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        agent_config = parse_config_content(file_path, content)
        if agent_config is None:
            return []

        app_root = resolve_app_root(file_path, context)
        known_guardrails = _collect_known_resource_names(app_root, "guardrails")
        known_toolsets = _collect_known_resource_names(app_root, "toolsets")
        results: list[LintResult] = []

        for gr in agent_config.get("guardrails") or []:
            if isinstance(gr, str) and _is_missing_local_resource(
                gr, "guardrails", known_guardrails
            ):
                results.append(
                    self.make_result(
                        str(file_path),
                        (
                            f"Agent config references guardrail '{gr}' "
                            "but no matching guardrail was found in "
                            "guardrails/."
                        ),
                        fix=(
                            f"Create guardrails/{gr}/ or remove '{gr}' "
                            f"from {file_path.name}."
                        ),
                    )
                )

        for ts_entry in agent_config.get("toolsets") or []:
            ts_name = (
                ts_entry.get("toolset")
                if isinstance(ts_entry, dict)
                else (ts_entry if isinstance(ts_entry, str) else None)
            )
            if (
                isinstance(ts_name, str)
                and ts_name
                and _is_missing_local_resource(
                    ts_name, "toolsets", known_toolsets
                )
            ):
                results.append(
                    self.make_result(
                        str(file_path),
                        (
                            f"Agent config references toolset '{ts_name}' "
                            "but no matching toolset was found in "
                            "toolsets/."
                        ),
                        fix=(
                            f"Create toolsets/{ts_name}/ or remove "
                            f"'{ts_name}' from {file_path.name}."
                        ),
                    )
                )

        instruction_path = agent_config.get("instruction")
        if _is_file_path_ref(instruction_path) and not _referenced_file_exists(
            instruction_path, file_path.parent, app_root, context
        ):
            results.append(
                self.make_result(
                    str(file_path),
                    (
                        f"Agent instruction references '{instruction_path}' "
                        "but file not found"
                    ),
                    fix=(
                        "Create the instruction file or fix the path in "
                        f"{file_path.name}."
                    ),
                )
            )

        return results


@rule("structure")
class ToolFileReferences(Rule):
    """Tool config references missing pythonCode or openApiSchema files."""

    id = "S012"
    name = "tool-file-references"
    description = (
        "Tool pythonCode and openApiSchema files referenced in tool "
        "config exist on disk"
    )
    default_severity = Severity.ERROR
    target = "tool_config"

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        tool_dir = file_path if file_path.is_dir() else file_path.parent
        config_data, target_path = _load_resource_dir_config(tool_dir)
        if not config_data or not target_path:
            return []

        app_root = resolve_app_root(tool_dir, context)
        results: list[LintResult] = []

        py_fn = config_data.get("pythonFunction") or config_data.get(
            "python_function"
        )
        if isinstance(py_fn, dict):
            code_path = py_fn.get("pythonCode") or py_fn.get("python_code")
            if _is_file_path_ref(code_path) and not _referenced_file_exists(
                code_path, tool_dir, app_root, context
            ):
                results.append(
                    self.make_result(
                        str(target_path),
                        (
                            f"Tool pythonCode references '{code_path}' "
                            "but file not found"
                        ),
                        fix=(
                            "Create the Python file or fix the path in "
                            f"{target_path.name}."
                        ),
                    )
                )

        oa_tool = config_data.get("openApiTool") or config_data.get(
            "open_api_tool"
        )
        if isinstance(oa_tool, dict):
            schema_path = oa_tool.get("openApiSchema") or oa_tool.get(
                "open_api_schema"
            )
            if _is_file_path_ref(schema_path) and not _referenced_file_exists(
                schema_path, tool_dir, app_root, context
            ):
                results.append(
                    self.make_result(
                        str(target_path),
                        (
                            f"Tool openApiSchema references '{schema_path}' "
                            "but file not found"
                        ),
                        fix=(
                            "Create the OpenAPI schema file or fix the path "
                            f"in {target_path.name}."
                        ),
                    )
                )

        return results


@rule("structure")
class ToolsetFileReferences(Rule):
    """Toolset config openApiSchema path is app-relative and exists on disk."""

    id = "S013"
    name = "toolset-file-references"
    description = (
        "Toolset openApiSchema file path must be prefixed with "
        "toolsets/{toolset_name}/ and exist on disk"
    )
    default_severity = Severity.ERROR
    target = "toolset_config"

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        toolset_dir = file_path if file_path.is_dir() else file_path.parent
        toolset_name = toolset_dir.name
        config_data, target_path = _load_resource_dir_config(toolset_dir)
        if not config_data or not target_path:
            return []

        oa_toolset = config_data.get("openApiToolset") or config_data.get(
            "open_api_toolset"
        )
        if not isinstance(oa_toolset, dict):
            return []

        schema_path = oa_toolset.get("openApiSchema") or oa_toolset.get(
            "open_api_schema"
        )
        if not _is_file_path_ref(schema_path):
            return []

        app_root = resolve_app_root(toolset_dir, context)
        results: list[LintResult] = []

        expected_prefix = f"toolsets/{toolset_name}/"
        if not schema_path.startswith(expected_prefix):
            results.append(
                self.make_result(
                    str(target_path),
                    (
                        f"Toolset openApiSchema path '{schema_path}' must be "
                        "relative to the app root and start with "
                        f"'{expected_prefix}'"
                    ),
                    fix=(
                        f"Change '{schema_path}' to start with "
                        f"'{expected_prefix}'"
                    ),
                )
            )

        if not _referenced_file_exists(
            schema_path, toolset_dir, app_root, context
        ):
            results.append(
                self.make_result(
                    str(target_path),
                    (
                        f"Toolset openApiSchema references '{schema_path}' "
                        "but file not found"
                    ),
                    fix=(
                        "Create the OpenAPI schema file or fix the path in "
                        f"{target_path.name}."
                    ),
                )
            )

        return results


@rule("structure")
class AgentTreeCycle(Rule):
    """Agent ``childAgents`` hierarchy must not contain cycles."""

    id = "S014"
    name = "agent-tree-cycle"
    description = "Agent childAgents hierarchy must form an acyclic tree"
    default_severity = Severity.ERROR
    target = "app_config"

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        app_root = resolve_app_root(file_path, context)
        graph = resolve_agent_graph(app_root)
        if not graph.cycles:
            return []

        rel = rel_to_project(file_path, context)
        return [
            self.make_result(
                file=rel,
                message=f"Agent tree contains a cycle: {' -> '.join(cycle)}.",
                fix=(
                    "Remove the cyclic reference from childAgents so agents "
                    "form a tree."
                ),
            )
            for cycle in graph.cycles
        ]


@rule("structure")
class OrphanedAgent(Rule):
    """Every agent under ``agents/`` should be reachable from ``rootAgent``."""

    id = "S015"
    name = "orphaned-agent"
    description = "Every agent under agents/ is reachable from rootAgent"
    default_severity = Severity.WARNING
    target = "app_config"

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        app_root = resolve_app_root(file_path, context)
        graph = resolve_agent_graph(app_root)
        if graph.root_agent_dir is None:
            return []

        root_label = graph.root_agent_ref or graph.root_agent_dir
        results: list[LintResult] = []
        for agent_name in sorted(graph.agent_dirs):
            if agent_name in graph.reachable:
                continue
            target_path = graph.agent_config_paths.get(
                agent_name, graph.agent_dirs[agent_name]
            )
            disp = graph.agent_display_names.get(agent_name)
            label = (
                f"'{agent_name}' (displayName '{disp}')"
                if disp and disp != agent_name
                else f"'{agent_name}'"
            )
            results.append(
                self.make_result(
                    file=rel_to_project(target_path, context),
                    message=(
                        f"Agent {label} is unreachable from rootAgent "
                        f"'{root_label}'."
                    ),
                    fix=(
                        f"Add '{agent_name}' to a reachable parent's "
                        "childAgents list, or remove the unused agent "
                        "directory."
                    ),
                )
            )
        return results


def _report_unused_resources(
    rule_obj: Rule,
    resources: list[LocalResource],
    agent_refs: dict[LocalResource, set[str]],
    graph: AgentGraph,
    context: LintContext,
    *,
    label: str,
    subdir: str,
    app_referenced: set[LocalResource] | frozenset[LocalResource] = frozenset(),
) -> list[LintResult]:
    """Emit warnings for resources unattached or attached only to orphans."""
    results: list[LintResult] = []
    app_scope = "the app or " if subdir == "guardrails" else ""
    for res in resources:
        if res in app_referenced:
            continue
        referencing = sorted(agent_refs.get(res, set()))
        if not referencing:
            results.append(
                rule_obj.make_result(
                    file=rel_to_project(res.report_path, context),
                    message=(
                        f"{label} '{res.key}' is not attached to "
                        f"{app_scope}any agent."
                    ),
                    fix=(
                        f"Attach '{res.key}' in {app_scope}an agent's "
                        f"{subdir} list, or remove {subdir}/{res.key}."
                    ),
                )
            )
        elif graph.root_agent_dir is not None and not any(
            a in graph.reachable for a in referencing
        ):
            unreachable_str = ", ".join(referencing)
            results.append(
                rule_obj.make_result(
                    file=rel_to_project(res.report_path, context),
                    message=(
                        f"{label} '{res.key}' is only attached to "
                        f"unreachable agent(s): {unreachable_str}."
                    ),
                    fix=(
                        f"Attach '{res.key}' to {app_scope}a reachable agent, "
                        f"connect {unreachable_str} to rootAgent, or remove "
                        f"the unused {label.lower()}."
                    ),
                )
            )
    return results


@rule("structure")
class UnusedToolset(Rule):
    """Every toolset under ``toolsets/`` should attach to a reachable agent."""

    id = "S016"
    name = "unused-toolset"
    description = (
        "Every toolset under toolsets/ is attached to at least one reachable "
        "agent"
    )
    default_severity = Severity.WARNING
    target = "app_config"

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        app_root = resolve_app_root(file_path, context)
        resources = discover_local_resources(app_root, "toolsets")
        if not resources:
            return []

        graph = resolve_agent_graph(app_root)
        refs_by_resource: dict[LocalResource, set[str]] = defaultdict(set)
        for agent_name, cfg in sorted(graph.agent_configs.items()):
            raw_toolsets = cfg.get("toolsets")
            if not isinstance(raw_toolsets, list):
                continue
            for entry in raw_toolsets:
                ts_ref = (
                    entry.get("toolset")
                    if isinstance(entry, dict)
                    else (entry if isinstance(entry, str) else None)
                )
                if isinstance(ts_ref, str) and ts_ref.strip():
                    for matched in match_local_resources(
                        ts_ref, "toolsets", resources
                    ):
                        refs_by_resource[matched].add(agent_name)

        return _report_unused_resources(
            self,
            resources,
            refs_by_resource,
            graph,
            context,
            label="Toolset",
            subdir="toolsets",
        )


@rule("structure")
class UnusedGuardrail(Rule):
    """Guardrails should attach to the app or at least one reachable agent."""

    id = "S017"
    name = "unused-guardrail"
    description = (
        "Every guardrail under guardrails/ is attached to the app or at least "
        "one reachable agent"
    )
    default_severity = Severity.WARNING
    target = "app_config"

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        app_root = resolve_app_root(file_path, context)
        resources = discover_local_resources(app_root, "guardrails")
        if not resources:
            return []

        graph = resolve_agent_graph(app_root)
        app_referenced, agent_refs = collect_attached_guardrails(
            graph, resources
        )
        return _report_unused_resources(
            self,
            resources,
            agent_refs,
            graph,
            context,
            label="Guardrail",
            subdir="guardrails",
            app_referenced=app_referenced,
        )


_AGENT_REF_PATTERN = re.compile(r"\{@AGENT[:\s]+([^}]+)\}", re.IGNORECASE)


def _read_agent_instruction(
    app_root: Path,
    agent_name: str,
    agent_dir: Path,
    graph: AgentGraph,
    context: LintContext,
) -> tuple[str, str] | None:
    """Return ``(instruction_text, report_rel)`` for an agent, or None."""
    cfg = graph.agent_configs.get(agent_name)
    raw_inst = cfg.get("instruction") if isinstance(cfg, dict) else None
    inst_path = (
        resolve_safe_app_file(app_root, str(raw_inst))
        if _is_file_path_ref(raw_inst)
        else None
    )
    if inst_path is None:
        default_inst = agent_dir / "instruction.txt"
        if default_inst.is_file() and not default_inst.is_symlink():
            inst_path = default_inst

    if inst_path is not None:
        try:
            return (
                inst_path.read_text(encoding="utf-8"),
                rel_to_project(inst_path, context),
            )
        except (OSError, UnicodeDecodeError):
            return None
    if isinstance(raw_inst, str) and raw_inst.strip():
        cfg_path = graph.agent_config_paths.get(agent_name, agent_dir)
        return raw_inst, rel_to_project(cfg_path, context)
    return None


@rule("structure")
class AgentRefSelfOrOrphan(Rule):
    """Instructions should not target self or orphans via {@AGENT:}."""

    id = "S018"
    name = "agent-ref-self-or-orphan"
    description = (
        "Instructions should not reference the current agent (self-transfer) "
        "or an orphaned/unreachable agent via {@AGENT:}"
    )
    default_severity = Severity.WARNING
    target = "app_config"

    def _scan_instruction_refs(
        self,
        agent_name: str,
        inst_text: str,
        report_rel: str,
        graph: AgentGraph,
        resolve_ref: Callable[[object], str | None],
    ) -> list[LintResult]:
        results: list[LintResult] = []
        for line_num, line in enumerate(inst_text.splitlines(), 1):
            for match in _AGENT_REF_PATTERN.finditer(line):
                raw_target = match.group(1).strip()
                resolved = resolve_ref(raw_target)
                if resolved is None:
                    continue
                if resolved == agent_name:
                    results.append(
                        self.make_result(
                            file=report_rel,
                            line=line_num,
                            message=(
                                f"Agent '{agent_name}' references itself "
                                f"via '{{@AGENT: {raw_target}}}' "
                                "(self-transfer)."
                            ),
                            fix=(
                                "Remove the self-transfer or target a "
                                "distinct child/parent agent."
                            ),
                        )
                    )
                elif (
                    graph.root_agent_dir is not None
                    and resolved not in graph.reachable
                ):
                    results.append(
                        self.make_result(
                            file=report_rel,
                            line=line_num,
                            message=(
                                f"Agent '{agent_name}' references "
                                f"orphaned/unreachable agent '{resolved}' "
                                f"via '{{@AGENT: {raw_target}}}'."
                            ),
                            fix=(
                                f"Add '{resolved}' to a reachable parent's "
                                "childAgents list, or remove the stale "
                                "{@AGENT:} reference."
                            ),
                        )
                    )
        return results

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        app_root = resolve_app_root(file_path, context)
        graph = resolve_agent_graph(app_root)
        if not graph.agent_dirs:
            return []

        resolve_ref = _build_agent_ref_resolver(
            set(graph.agent_dirs.keys()), graph.agent_configs
        )
        results: list[LintResult] = []
        for agent_name, agent_dir in sorted(graph.agent_dirs.items()):
            loaded = _read_agent_instruction(
                app_root, agent_name, agent_dir, graph, context
            )
            if loaded is None:
                continue
            inst_text, report_rel = loaded
            results.extend(
                self._scan_instruction_refs(
                    agent_name, inst_text, report_rel, graph, resolve_ref
                )
            )
        return results


_FIELD_LLM_POLICY: tuple[str, str] = ("llmPolicy", "llm_policy")
_FIELD_LLM_PROMPT_SECURITY: tuple[str, str] = (
    "llmPromptSecurity",
    "llm_prompt_security",
)
_FIELD_CUSTOM_POLICY: tuple[str, str] = ("customPolicy", "custom_policy")


def _extract_guardrail_prompts(data: dict) -> list[str]:
    """Return prompt strings from llmPolicy and llmPromptSecurity blocks."""
    prompts: list[str] = []
    for key in (*_FIELD_LLM_POLICY, *_FIELD_CUSTOM_POLICY):
        policy = data.get(key)
        if isinstance(policy, dict):
            prompt = policy.get("prompt")
            if isinstance(prompt, str):
                prompts.append(prompt)

    for sec_key in _FIELD_LLM_PROMPT_SECURITY:
        sec = data.get(sec_key)
        if not isinstance(sec, dict):
            continue
        for cp_key in _FIELD_CUSTOM_POLICY:
            custom_policy = sec.get(cp_key)
            if isinstance(custom_policy, dict):
                prompt = custom_policy.get("prompt")
                if isinstance(prompt, str):
                    prompts.append(prompt)

    return prompts


def _iter_guardrail_config_paths(
    file_path: Path, context: LintContext
) -> list[Path]:
    """Return guardrail config paths for an app_config or direct target."""
    if file_path.name in APP_CONFIG_NAMES:
        app_root = resolve_app_root(file_path, context)
        paths: list[Path] = []
        for res in discover_local_resources(app_root, "guardrails"):
            if res.report_path.is_dir():
                for ext in CONFIG_EXTENSIONS:
                    paths.extend(sorted(res.report_path.glob(f"*{ext}")))
            elif res.report_path.is_file():
                paths.append(res.report_path)
        return paths
    if file_path.is_dir():
        return [
            p
            for ext in CONFIG_EXTENSIONS
            for p in sorted(file_path.glob(f"*{ext}"))
        ]
    if file_path.suffix.lower() in CONFIG_EXTENSIONS:
        return [file_path]
    return []


@rule("structure")
class GuardrailJinjaSyntax(Rule):
    id = "S019"
    target = "app_config"
    name = "guardrail-jinja-syntax"
    description = "Detects Jinja tokens inside guardrail policy prompts."
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        results: list[LintResult] = []
        for cfg_path in _iter_guardrail_config_paths(file_path, context):
            data = _load_config_dict(cfg_path)
            if data is None:
                continue

            prompts = _extract_guardrail_prompts(data)
            if any("{{" in p or "{%" in p or "{#" in p for p in prompts):
                results.append(
                    self.make_result(
                        file=rel_to_project(cfg_path, context),
                        message=(
                            "Jinja tokens {{...}}, {% ... %}, or {# ... #}"
                            " found inside guardrail policy prompt."
                        ),
                        fix=(
                            "Use flat {variable} placeholders or pre-rendered"
                            " static text instead of Jinja templates."
                        ),
                    )
                )
        return results
