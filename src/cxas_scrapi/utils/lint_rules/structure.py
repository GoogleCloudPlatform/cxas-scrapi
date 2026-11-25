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

"""Structure validation rules (S002-S021).

Validates app structure and cross-references using the existing
``cxas_scrapi.utils.validator.Validator`` for structural checks, plus
custom rules for cross-referencing (e.g., instruction references a tool
not in the agent's tool list, agent hierarchy cycles and orphans, or
configs reference missing/unused guardrails, toolsets, or code/schema
files).
"""

import json
import re
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import yaml

from cxas_scrapi.utils.linter import (
    CONFIG_EXTENSIONS,
    AgentGraph,
    LintContext,
    LintResult,
    LocalResource,
    Rule,
    Severity,
    ToolsetValidationBehavior,
    _build_agent_ref_resolver,
    _find_dir_config,
    _load_config_dict,
    collect_attached_guardrails,
    discover_deployment_files,
    discover_local_resources,
    discover_openapi_schema_paths,
    get_toolset_tools,
    iter_openapi_server_urls,
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

        known_tools = (
            set(agent_config.get("tools", [])) | context.platform_tools
        )

        bypass_prefixes = set()

        # Resolve toolsets and add their tools to known_tools
        for ts_entry in agent_config.get("toolsets", []):
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


@rule("structure")
class ChildAgentReferences(Rule):
    """Agent JSON references child agents that don't exist."""

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

        results = []
        child_agents = agent_config.get("childAgents") or []
        if not isinstance(child_agents, list):
            return []
        valid_agents = context.all_agent_names | context.all_agent_display_names
        for child_name in child_agents:
            if child_name not in valid_agents:
                results.append(
                    self.make_result(
                        str(file_path),
                        (
                            "References child agent"
                            f" '{child_name}' but no"
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

        yaml_path = file_path / f"{tool_name}.yaml"
        json_path = file_path / f"{tool_name}.json"

        config_data = None
        target_path = None

        if yaml_path.exists():
            try:
                config_data = yaml.safe_load(yaml_path.read_text())
                target_path = yaml_path
            except Exception:
                return []
        elif json_path.exists():
            try:
                config_data = json.loads(json_path.read_text())
                target_path = json_path
            except Exception:
                return []
        else:
            return []

        if not config_data:
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
    """Load ``<name>.yaml`` or ``<name>.json`` from a guardrail directory."""
    for file_name, loader in (
        (f"{guardrail_dir.name}.yaml", yaml.safe_load),
        (f"{guardrail_dir.name}.json", json.loads),
    ):
        config_path = guardrail_dir / file_name
        if config_path.exists():
            try:
                data = loader(config_path.read_text())
            except Exception:
                return None
            return data if isinstance(data, dict) else None
    return None


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
    """Load ``<name>.yaml`` or ``<name>.json`` from a resource directory."""
    name = resource_dir.name
    yaml_path = resource_dir / f"{name}.yaml"
    json_path = resource_dir / f"{name}.json"
    if yaml_path.exists():
        try:
            data = yaml.safe_load(yaml_path.read_text())
            return (data if isinstance(data, dict) else None), yaml_path
        except Exception:
            return None, yaml_path
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text())
            return (data if isinstance(data, dict) else None), json_path
        except Exception:
            return None, json_path
    return None, None


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
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            known.add(entry.name)
            known.add(entry.name.replace("_", " "))
            cfg, _ = _load_resource_dir_config(entry)
            _add_config_identifiers(cfg, known)
        elif entry.is_file() and entry.suffix in (".json", ".yaml", ".yml"):
            known.add(entry.stem)
            known.add(entry.stem.replace("_", " "))
            try:
                cfg = (
                    json.loads(entry.read_text())
                    if entry.suffix == ".json"
                    else yaml.safe_load(entry.read_text())
                )
            except Exception:
                cfg = None
            _add_config_identifiers(cfg, known)
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


@rule("structure")
class DeploymentEntryAgentParity(Rule):
    """Deployment ``entryAgent`` must exist under ``agents/`` and match root."""

    id = "S018"
    name = "deployment-entry-agent"
    description = (
        "Deployment entryAgent must exist under agents/ and match app rootAgent"
    )
    default_severity = Severity.ERROR
    target = "app_config"

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        app_root = resolve_app_root(file_path, context)
        dep_files = discover_deployment_files(app_root, context)
        if not dep_files:
            return []

        graph = resolve_agent_graph(app_root)
        resolve_ref = _build_agent_ref_resolver(
            set(graph.agent_dirs.keys()), graph.agent_configs
        )

        results: list[LintResult] = []
        for dep_path in dep_files:
            dep = _load_config_dict(dep_path)
            if dep is None:
                continue
            raw_entry = (
                dep.get("entryAgent")
                if "entryAgent" in dep
                else dep.get("entry_agent")
            )
            if not isinstance(raw_entry, str) or not raw_entry.strip():
                continue

            entry_ref = raw_entry.strip()
            resolved = resolve_ref(entry_ref)
            dep_rel = rel_to_project(dep_path, context)

            if resolved is None:
                results.append(
                    self.make_result(
                        file=dep_rel,
                        severity=Severity.ERROR,
                        message=(
                            f"Deployment entryAgent '{entry_ref}' does not "
                            "resolve to any agent under agents/."
                        ),
                        fix=(
                            "Set entryAgent to an existing agent under "
                            "agents/ or create the missing agent."
                        ),
                    )
                )
            elif (
                graph.root_agent_dir is not None
                and resolved != graph.root_agent_dir
            ):
                root_label = graph.root_agent_ref or graph.root_agent_dir
                results.append(
                    self.make_result(
                        file=dep_rel,
                        severity=Severity.WARNING,
                        message=(
                            f"Deployment entryAgent '{entry_ref}' (resolved "
                            f"to '{resolved}') diverges from app rootAgent "
                            f"'{root_label}'."
                        ),
                        fix=(
                            "Align deployment entryAgent with app rootAgent, "
                            "or confirm the alternate entry agent is "
                            "intentional."
                        ),
                    )
                )
        return results


_PROD_ENV_VALUES: frozenset[str] = frozenset({"prod", "production"})
_LOOPBACK_HOSTS: frozenset[str] = frozenset(
    {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
)
_URL_FIELD_KEYS_LOWER: frozenset[str] = frozenset(
    {
        "url",
        "serverurl",
        "server_url",
        "baseurl",
        "base_url",
        "endpoint",
        "endpointurl",
        "endpoint_url",
    }
)
_REGEX_META_RE = re.compile(r"[\\^$*+?{}\[\]|()]")


def _extract_matching_url(
    val: object, *, key_lower: str | None, include_all_http: bool
) -> str | None:
    """Return stripped URL if ``val`` matches URL field or http(s) criteria."""
    if not isinstance(val, str):
        return None
    stripped = val.strip()
    if not stripped or stripped == "$env_var":
        return None
    if key_lower is not None and key_lower in _URL_FIELD_KEYS_LOWER:
        return stripped
    if include_all_http and stripped.lower().startswith(
        ("http://", "https://")
    ):
        return stripped
    return None


def _extract_url_fields(
    obj: object, *, include_all_http: bool = False
) -> list[str]:
    """Collect endpoint URL strings from nested JSON/YAML structures."""
    found: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key_str = str(k)
            if key_str == "$schema" or key_str.startswith("_comment"):
                continue
            url = _extract_matching_url(
                v, key_lower=key_str.lower(), include_all_http=include_all_http
            )
            if url is not None:
                found.append(url)
            elif not isinstance(v, str):
                found.extend(
                    _extract_url_fields(v, include_all_http=include_all_http)
                )
    elif isinstance(obj, list):
        for item in obj:
            url = _extract_matching_url(
                item, key_lower=None, include_all_http=include_all_http
            )
            if url is not None:
                found.append(url)
            elif not isinstance(item, str):
                found.extend(
                    _extract_url_fields(item, include_all_http=include_all_http)
                )
    return found


def _compile_forbidden_url_matchers(
    raw_patterns: object,
) -> list[tuple[str, re.Pattern[str] | None]]:
    """Compile forbidden URL patterns (literal substring or regex)."""
    if not isinstance(raw_patterns, list):
        return []
    matchers: list[tuple[str, re.Pattern[str] | None]] = []
    for pat in raw_patterns:
        if not isinstance(pat, str) or not pat.strip():
            continue
        clean = pat.strip()
        if _REGEX_META_RE.search(clean):
            try:
                matchers.append((clean, re.compile(clean, re.IGNORECASE)))
            except re.error:
                matchers.append((clean, None))
        else:
            matchers.append((clean, None))
    return matchers


def _has_prod_deployment(app_root: Path, context: LintContext) -> bool:
    """Return True if any deployment manifest targets prod/production."""
    for dep_path in discover_deployment_files(app_root, context):
        dep = _load_config_dict(dep_path)
        env_val = dep.get("environment") if dep is not None else None
        if (
            isinstance(env_val, str)
            and env_val.strip().lower() in _PROD_ENV_VALUES
        ):
            return True
    return False


def _collect_prod_url_sources(
    app_root: Path, context: LintContext
) -> list[tuple[Path, str]]:
    """Collect ``(source_path, url)`` pairs across env, tools, and OpenAPI."""
    url_sources: list[tuple[Path, str]] = []

    env_path = (
        context.env_file
        if context.env_file is not None
        else app_root / "environment.json"
    )
    env_doc = _load_config_dict(env_path)
    if env_doc is not None:
        url_sources.extend(
            (env_path, u)
            for u in _extract_url_fields(env_doc, include_all_http=True)
        )

    for subdir in ("tools", "toolsets"):
        for res in discover_local_resources(app_root, subdir):
            cfg = (
                _load_config_dict(res.report_path)
                if res.report_path.is_file()
                else None
            )
            if cfg is not None:
                url_sources.extend(
                    (res.report_path, u) for u in _extract_url_fields(cfg)
                )

    for _kind, _key, schema_path in discover_openapi_schema_paths(app_root):
        doc = _load_config_dict(schema_path)
        if doc is None:
            continue
        url_sources.extend(
            (schema_path, u)
            for u in iter_openapi_server_urls(doc)
            if u != "$env_var"
        )
    return url_sources


def _parse_url_scheme_and_host(url: str) -> tuple[str, str]:
    """Extract lowercase ``(scheme, host)`` safely on malformed IPv6."""
    try:
        if "://" in url:
            parsed = urlparse(url)
            return (parsed.scheme or "").lower(), (
                parsed.hostname or ""
            ).lower()
        if not url.startswith("/"):
            parsed = urlparse(f"//{url}")
            return "", (parsed.hostname or "").lower()
    except ValueError:
        scheme = url.split("://", 1)[0].lower() if "://" in url else ""
        return scheme, ""
    return "", ""


def _is_loopback_host(host: str) -> bool:
    return (
        host in _LOOPBACK_HOSTS
        or host.endswith(".local")
        or host.endswith(".localhost")
    )


def _match_forbidden_patterns(
    url: str,
    matchers: list[tuple[str, re.Pattern[str] | None]],
) -> list[str]:
    """Return all forbidden pattern strings matching ``url``."""
    url_lower = url.lower()
    return [
        pat_str
        for pat_str, pat_re in matchers
        if (
            bool(pat_re.search(url))
            if pat_re is not None
            else pat_str.lower() in url_lower
        )
    ]


@rule("structure")
class ProdUrlSafety(Rule):
    """Prod builds must not contain cleartext, loopback, or non-prod URLs."""

    id = "S019"
    name = "prod-url-safety"
    description = (
        "Prod deployments must not reference cleartext http://, loopback, "
        "or forbidden non-prod URLs"
    )
    default_severity = Severity.ERROR
    target = "app_config"

    def _check_url_violations(
        self,
        rel: str,
        url: str,
        matchers: list[tuple[str, re.Pattern[str] | None]],
        seen: set[tuple[str, str, str]],
    ) -> list[LintResult]:
        scheme, host = _parse_url_scheme_and_host(url)
        findings: list[tuple[str, str, str]] = []
        if scheme == "http":
            findings.append(
                (
                    "cleartext",
                    (
                        f"Cleartext http:// URL '{url}' is not allowed in "
                        "prod builds."
                    ),
                    "Use an https:// production endpoint URL.",
                )
            )
        if _is_loopback_host(host):
            findings.append(
                (
                    "loopback",
                    (
                        f"Loopback or local-only host in URL '{url}' is not "
                        "allowed in prod builds."
                    ),
                    "Replace with a production endpoint host.",
                )
            )
        for pat_str in _match_forbidden_patterns(url, matchers):
            findings.append(
                (
                    f"pattern:{pat_str}",
                    (
                        f"URL '{url}' matches forbidden non-prod pattern "
                        f"'{pat_str}' in prod build."
                    ),
                    "Replace with the production URL before deploying to prod.",
                )
            )

        results: list[LintResult] = []
        for tag, msg, fix in findings:
            key = (rel, url, tag)
            if key not in seen:
                seen.add(key)
                results.append(self.make_result(file=rel, message=msg, fix=fix))
        return results

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        app_root = resolve_app_root(file_path, context)
        if not _has_prod_deployment(app_root, context):
            return []

        s019_opts = context.options.get("S019")
        raw_patterns = (
            s019_opts.get("forbidden_url_patterns")
            if isinstance(s019_opts, dict)
            else context.options.get("forbidden_url_patterns")
        )
        forbidden_matchers = _compile_forbidden_url_matchers(raw_patterns)
        seen: set[tuple[str, str, str]] = set()
        results: list[LintResult] = []
        for src_path, url in _collect_prod_url_sources(app_root, context):
            results.extend(
                self._check_url_violations(
                    rel_to_project(src_path, context),
                    url,
                    forbidden_matchers,
                    seen,
                )
            )
        return results


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

    id = "S020"
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


def _extract_guardrail_prompts(data: dict) -> list[str]:
    """Return prompt strings from llmPolicy and llmPromptSecurity blocks."""
    prompts: list[str] = []
    for key in ("llm_policy", "llmPolicy", "custom_policy", "customPolicy"):
        policy = data.get(key)
        if isinstance(policy, dict):
            prompt = policy.get("prompt")
            if isinstance(prompt, str):
                prompts.append(prompt)

    for sec_key in ("llm_prompt_security", "llmPromptSecurity"):
        sec = data.get(sec_key)
        if not isinstance(sec, dict):
            continue
        for cp_key in ("custom_policy", "customPolicy"):
            custom_policy = sec.get(cp_key)
            if isinstance(custom_policy, dict):
                prompt = custom_policy.get("prompt")
                if isinstance(prompt, str):
                    prompts.append(prompt)

    return prompts


@rule("structure")
class GuardrailJinjaSyntax(Rule):
    id = "S021"
    target = "guardrail_config"
    name = "guardrail-jinja-syntax"
    description = "Detects Jinja tokens inside guardrail policy prompts."
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        config_paths: list[Path] = []
        if file_path.is_dir():
            for ext in CONFIG_EXTENSIONS:
                config_paths.extend(sorted(file_path.glob(f"*{ext}")))
        elif file_path.suffix.lower() in CONFIG_EXTENSIONS:
            config_paths.append(file_path)

        results: list[LintResult] = []
        for cfg_path in config_paths:
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
