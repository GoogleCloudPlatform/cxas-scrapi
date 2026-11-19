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

"""Static architecture firewall (`FW01`..`FW10`) for CES app export bundles."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from cxas_scrapi.build.blueprint_schemas import (
    SINGLE_BRACE_PLACEHOLDER_RE,
    BlueprintCompilerError,
    BrandSpec,
    FirewallError,
    FirewallViolation,
    ModalitySpec,
    RuntimePolicySpec,
    build_pruned_identifier_regex,
    load_spec,
)
from cxas_scrapi.utils.linter import CES_PLATFORM_SYSTEM_TOOLS

if TYPE_CHECKING:
    from collections.abc import Sequence

REQUIRED_MODEL_POOLS: tuple[str, ...] = (
    "voice_talker_thinker",
    "agent_planner",
    "guardrail_judge",
    "datastore_rewriter_summarizer",
    "eval_judge",
)

UNPINNED_MODEL_TOKENS: tuple[str, ...] = ("default", "latest", "auto")

ROOT_INSTRUCTION_MAX_BYTES = 15_360
SPECIALIST_INSTRUCTION_MAX_BYTES = 18_432
MAX_SPECIALIST_TOOLS = 6
MAX_ROOT_OBJECT_VARIABLES = 16
MAX_TOTAL_VARIABLES = 100
MAX_PYTHON_FILE_BYTES = 65_536

FirewallProfile = Literal["strict", "standard"]

# CES built-in system tools (e.g. `end_session`) are referenced by name with no
# `tools/<name>/` directory; CES's own export represents them the same way.
# Single source of truth shared with lint (`LintContext.platform_tools`, A005).
CES_SYSTEM_TOOLS: frozenset[str] = CES_PLATFORM_SYSTEM_TOOLS


def _is_known_tool(snap: _BundleSnapshot, tool_name: str) -> bool:
    return tool_name in snap.tool_jsons or tool_name in CES_SYSTEM_TOOLS


CALLBACK_JSON_KEYS: tuple[str, ...] = (
    "beforeAgentCallbacks",
    "afterAgentCallbacks",
    "beforeModelCallbacks",
    "afterModelCallbacks",
    "beforeToolCallbacks",
    "afterToolCallbacks",
    "before_agent_callbacks",
    "after_agent_callbacks",
    "before_model_callbacks",
    "after_model_callbacks",
    "before_tool_callbacks",
    "after_tool_callbacks",
    "callbacks",
)

PYTHON_TOOL_JSON_KEYS: tuple[str, ...] = (
    "pythonFunction",
    "pythonFunctions",
    "python_function",
    "python_functions",
)

GUARDRAIL_CALLBACK_JSON_KEYS: tuple[str, ...] = (
    "codeCallback",
    "code_callback",
)

INSTRUCTION_REF_TAG_RE = re.compile(
    r"\{\s*@(TOOL|AGENT):\s*([a-zA-Z0-9_-]+)\s*\}"
)
COMPILE_COND_TAG_RE = re.compile(
    r"\{@(?:start|end)(?:Channel|Brand|AgentEnabled|ToolEnabled)_[^}]+\}"
)
MODALITY_TAG_RE = re.compile(
    r"(\{@startModality_[^}]+\}|\{@endModality_[^}]+\})"
)

PCRE_LOOKAROUND_RE = re.compile(r"(?<!\\)(?:\\\\)*\(\?(?:=|!|<=|<!)")
PCRE_BACKREF_RE = re.compile(
    r"(?<!\\)(?:\\\\)*(?:\\[1-9]|\\k<[a-zA-Z_][a-zA-Z0-9_]*>)"
)
PCRE_UNSUPPORTED_GROUP_RE = re.compile(
    r"(?<!\\)(?:\\\\)*\(\?(?:>|P=|\(|#|[aiLmsux-]*[aLu])"
)
PYTHON_ONLY_ESCAPE_RE = re.compile(r"(?<!\\)(?:\\\\)*\\[ZuU]")
PCRE_POSSESSIVE_RE = re.compile(
    r"(?<!\\)(?:\\\\)*(?:[*+?]|(?:\{[0-9]+(?:,[0-9]*)?\}))\+"
)

SURFACE_EXPR_RE = re.compile(
    r"(?:^|(?<![a-zA-Z0-9_.]))(?:session_ctx\.)?surface_id\s*==\s*'([^']*)'"
)
SEGMENT_EXPR_RE = re.compile(
    r"(?:^|(?<![a-zA-Z0-9_.]))(?:session_ctx\.)?segment\s*==\s*'([^']*)'"
)
KILL_SWITCH_EXPR_RE = re.compile(
    r"(?:session_ctx\.disabled_agents\.([a-zA-Z0-9_]+)"
    r"|(?:^|(?<![a-zA-Z0-9_.]))disabled_agent_([a-zA-Z0-9_]+)\b)"
)

JsonRecord = tuple[str, str, dict[str, Any]]


@dataclass
class _BundleSnapshot:
    bundle_dir: Path
    profile: FirewallProfile
    eff_bp_dir: Path | None
    requires_bp_context: bool
    root_agent_name: str
    app_json_path: Path
    app_json: dict[str, Any]
    metadata: dict[str, Any]
    brand_id: str
    bundle_modality: str
    brand_spec: BrandSpec | None
    modality_spec: ModalitySpec | None
    eff_forbidden_tokens: list[str]
    eff_runtime_policy: RuntimePolicySpec | None
    agent_dirs: list[Path]
    agent_jsons: dict[str, dict[str, Any]]
    agent_rel_paths: dict[str, str]
    all_agent_records: list[JsonRecord]
    tool_dirs: list[Path]
    tool_jsons: dict[str, dict[str, Any]]
    tool_rel_paths: dict[str, str]
    all_tool_records: list[JsonRecord]
    guardrail_dirs: list[Path]
    guardrail_jsons: dict[str, dict[str, Any]]
    guardrail_rel_paths: dict[str, str]
    all_guardrail_records: list[JsonRecord]
    all_toolset_records: list[JsonRecord]
    surviving_agents: set[str]
    root_children: set[str]
    extra_pruned_agents: frozenset[str] = frozenset()

    @property
    def root_rel_json(self) -> str:
        return self.agent_rel_paths.get(
            self.root_agent_name,
            f"agents/{self.root_agent_name}/{self.root_agent_name}.json",
        )


def _is_unpinned_model(model_value: Any) -> bool:
    if not isinstance(model_value, str) or not model_value.strip():
        return True
    lowered = model_value.strip().lower()
    return any(tok in lowered for tok in UNPINNED_MODEL_TOKENS)


def _mask_regex_char_classes(pattern: str) -> str:
    """Masks characters inside `[...]` so outer constructs can be inspected."""
    out: list[str] = []
    in_class = False
    class_start = False
    seen_caret = False
    escaped = False
    for ch in pattern:
        if escaped:
            if in_class:
                class_start = False
            else:
                out.append("\\")
                out.append(ch)
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if not in_class:
            if ch == "[":
                in_class = True
                class_start = True
                seen_caret = False
                out.append("[x")
            else:
                out.append(ch)
        elif ch == "^" and class_start and not seen_caret:
            seen_caret = True
        elif ch == "]" and not class_start:
            in_class = False
            out.append("]")
        else:
            class_start = False
    return "".join(out)


def _discover_blueprint_dir(
    app_dir: Path, explicit_blueprint_dir: Path | str | None
) -> Path | None:
    if explicit_blueprint_dir is not None:
        p = Path(explicit_blueprint_dir).resolve()
        return p if (p / "blueprint.yaml").is_file() else None
    for candidate in (
        app_dir.parent.parent / "blueprint",
        app_dir.parent / "blueprint",
    ):
        if (candidate / "blueprint.yaml").is_file():
            return candidate.resolve()
    return None


def _read_json_safe(
    path: Path, violations: list[FirewallViolation], rule_id: str = "FW08"
) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        violations.append(
            FirewallViolation(
                rule_id=rule_id,
                message=f"Invalid JSON file: {exc}",
                path=str(path),
            )
        )
        return {}
    if not isinstance(data, dict):
        violations.append(
            FirewallViolation(
                rule_id=rule_id,
                message=(
                    f"Expected JSON object at root, got {type(data).__name__}."
                ),
                path=str(path),
            )
        )
        return {}
    return data


def _discover_category_jsons(
    bundle_dir: Path,
    category: str,
    violations: list[FirewallViolation],
) -> tuple[
    list[Path],
    dict[str, dict[str, Any]],
    dict[str, str],
    list[JsonRecord],
]:
    cat_root = bundle_dir / category
    item_dirs: list[Path] = []
    primary_jsons: dict[str, dict[str, Any]] = {}
    primary_rel_paths: dict[str, str] = {}
    all_records: list[JsonRecord] = []

    if not cat_root.is_dir():
        return item_dirs, primary_jsons, primary_rel_paths, all_records

    for entry in sorted(cat_root.iterdir()):
        if entry.is_dir():
            item_dirs.append(entry)
            item_name = entry.name
            json_files = sorted(entry.glob("*.json"))
            canonical_jf = entry / f"{item_name}.json"
            primary_jf: Path | None = (
                canonical_jf
                if canonical_jf.is_file()
                else (json_files[0] if json_files else None)
            )
            if primary_jf is None:
                rel_missing = f"{category}/{item_name}/{item_name}.json"
                violations.append(
                    FirewallViolation(
                        rule_id="FW10",
                        message=(
                            f"Resource directory {category}/{item_name} is "
                            "missing its JSON manifest."
                        ),
                        path=rel_missing,
                    )
                )
                primary_jsons[item_name] = {}
                primary_rel_paths[item_name] = rel_missing
            else:
                parsed_by_path: dict[Path, dict[str, Any]] = {}
                for jf in json_files:
                    rel_jf = jf.relative_to(bundle_dir).as_posix()
                    parsed_obj = _read_json_safe(jf, violations)
                    parsed_by_path[jf] = parsed_obj
                    all_records.append((item_name, rel_jf, parsed_obj))
                primary_jsons[item_name] = parsed_by_path[primary_jf]
                primary_rel_paths[item_name] = primary_jf.relative_to(
                    bundle_dir
                ).as_posix()
        elif entry.is_file() and entry.suffix == ".json":
            item_name = entry.stem
            rel_jf = entry.relative_to(bundle_dir).as_posix()
            parsed_obj = _read_json_safe(entry, violations)
            all_records.append((item_name, rel_jf, parsed_obj))
            if item_name not in primary_jsons:
                primary_jsons[item_name] = parsed_obj
                primary_rel_paths[item_name] = rel_jf

    return item_dirs, primary_jsons, primary_rel_paths, all_records


def _has_invalid_modality_tags(text: str) -> bool:
    stack: list[str] = []
    for line in text.split("\n"):
        matches = MODALITY_TAG_RE.findall(line)
        if not matches:
            continue
        for match in matches:
            if match != line.strip():
                return True
            if match.startswith("{@startModality_"):
                stack.append(match[16:-1])
            elif match.startswith("{@endModality_"):
                modality = match[14:-1]
                if not stack or stack[-1] != modality:
                    return True
                stack.pop()
    if stack:
        return True
    stripped = MODALITY_TAG_RE.sub("", text)
    return "{@start" in stripped or "{@end" in stripped


def _check_fw01_fw02(
    snap: _BundleSnapshot, violations: list[FirewallViolation]
) -> None:
    for a_name, rel_agent_json, a_json in snap.all_agent_records:
        if a_name != snap.root_agent_name:
            if snap.profile == "strict":
                for cb_key in CALLBACK_JSON_KEYS:
                    if a_json.get(cb_key):
                        violations.append(
                            FirewallViolation(
                                rule_id="FW01",
                                message=(
                                    f"Specialist {a_name!r} attaches forbidden "
                                    f"callback field {cb_key!r}."
                                ),
                                path=rel_agent_json,
                            )
                        )
            attached_tools = a_json.get("tools", [])
            if (
                isinstance(attached_tools, list)
                and len(attached_tools) > MAX_SPECIALIST_TOOLS
            ):
                violations.append(
                    FirewallViolation(
                        rule_id="FW02",
                        message=(
                            f"Specialist {a_name!r} attaches "
                            f"{len(attached_tools)} tools "
                            f"(max {MAX_SPECIALIST_TOOLS})."
                        ),
                        path=rel_agent_json,
                    )
                )

        if snap.profile == "strict" and any(
            k in a_json and a_json[k] not in (None, [], {})
            for k in PYTHON_TOOL_JSON_KEYS
        ):
            violations.append(
                FirewallViolation(
                    rule_id="FW01",
                    message=(
                        f"Agent {a_name!r} defines inline pythonFunction tool."
                    ),
                    path=rel_agent_json,
                )
            )

    if snap.profile == "strict":
        for t_name, rel_tool_json, t_json in [
            *snap.all_tool_records,
            *snap.all_toolset_records,
        ]:
            if (
                any(
                    k in t_json and t_json[k] is not None
                    for k in PYTHON_TOOL_JSON_KEYS
                )
                or t_json.get("kind") == "python_function"
            ):
                violations.append(
                    FirewallViolation(
                        rule_id="FW01",
                        message=(
                            f"Tool {t_name!r} uses forbidden pythonFunction "
                            "tool type."
                        ),
                        path=rel_tool_json,
                    )
                )

        for gr_name, rel_gr_json, gr_json in snap.all_guardrail_records:
            for cb_key in GUARDRAIL_CALLBACK_JSON_KEYS:
                if cb_key in gr_json and gr_json[cb_key] is not None:
                    violations.append(
                        FirewallViolation(
                            rule_id="FW01",
                            message=(
                                f"Guardrail {gr_name!r} uses forbidden "
                                f"{cb_key!r} Python callback."
                            ),
                            path=rel_gr_json,
                        )
                    )

        for py_file in sorted(snap.bundle_dir.rglob("*.py")):
            rel_py = py_file.relative_to(snap.bundle_dir).as_posix()
            violations.append(
                FirewallViolation(
                    rule_id="FW01",
                    message=(
                        f"Bundle contains forbidden Python code file "
                        f"{py_file.name!r}."
                    ),
                    path=rel_py,
                )
            )
        return

    for cat in ("agents", "tools", "guardrails", "toolsets"):
        cat_root = snap.bundle_dir / cat
        if not cat_root.is_dir():
            continue
        for entry in sorted(cat_root.iterdir()):
            if not entry.is_dir():
                continue
            expected_name = f"{entry.name}.json"
            for jf in sorted(entry.glob("*.json")):
                if jf.name != expected_name:
                    rel_jf = jf.relative_to(snap.bundle_dir).as_posix()
                    violations.append(
                        FirewallViolation(
                            rule_id="FW01",
                            message=(
                                f"JSON manifest {jf.name!r} must match "
                                f"directory name {expected_name!r}."
                            ),
                            path=rel_jf,
                        )
                    )

    for py_file in sorted(snap.bundle_dir.rglob("*.py")):
        rel_py = py_file.relative_to(snap.bundle_dir).as_posix()
        try:
            py_bytes = py_file.read_bytes()
        except OSError as exc:
            violations.append(
                FirewallViolation(
                    rule_id="FW01",
                    message=f"Unable to read Python file {rel_py!r}: {exc}.",
                    path=rel_py,
                )
            )
            continue
        if len(py_bytes) > MAX_PYTHON_FILE_BYTES:
            violations.append(
                FirewallViolation(
                    rule_id="FW01",
                    message=(
                        f"Python file {rel_py!r} is {len(py_bytes)} B "
                        f"(max {MAX_PYTHON_FILE_BYTES} B)."
                    ),
                    path=rel_py,
                )
            )
        try:
            py_text = py_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            violations.append(
                FirewallViolation(
                    rule_id="FW01",
                    message=(
                        f"Python file {rel_py!r} is not valid UTF-8: {exc}."
                    ),
                    path=rel_py,
                )
            )
            continue
        try:
            ast.parse(py_text, filename=rel_py)
        except SyntaxError as exc:
            violations.append(
                FirewallViolation(
                    rule_id="FW01",
                    message=(
                        f"Python file {rel_py!r} has invalid Python syntax: "
                        f"{exc}."
                    ),
                    path=rel_py,
                )
            )


def _check_fw03(
    snap: _BundleSnapshot, violations: list[FirewallViolation]
) -> None:
    var_decls = snap.app_json.get("variableDeclarations", [])
    if not isinstance(var_decls, list):
        return
    root_objects = [
        decl
        for decl in var_decls
        if isinstance(decl, dict)
        and str(
            (
                decl.get("schema", decl)
                if isinstance(decl.get("schema", decl), dict)
                else {}
            ).get("type", "")
        ).upper()
        == "OBJECT"
    ]
    if len(root_objects) > MAX_ROOT_OBJECT_VARIABLES:
        violations.append(
            FirewallViolation(
                rule_id="FW03",
                message=(
                    f"App declares {len(root_objects)} root OBJECT variables "
                    f"(max {MAX_ROOT_OBJECT_VARIABLES})."
                ),
                path="app.json",
            )
        )
    if len(var_decls) > MAX_TOTAL_VARIABLES:
        violations.append(
            FirewallViolation(
                rule_id="FW03",
                message=(
                    f"App declares {len(var_decls)} total variables "
                    f"(max {MAX_TOTAL_VARIABLES})."
                ),
                path="app.json",
            )
        )


def _check_fw04(
    snap: _BundleSnapshot, violations: list[FirewallViolation]
) -> None:
    global_ref = snap.app_json.get("globalInstruction")
    global_instr_path = (
        snap.bundle_dir / global_ref
        if isinstance(global_ref, str) and global_ref.strip()
        else snap.bundle_dir / "global_instruction.txt"
    )
    global_bytes = (
        len(global_instr_path.read_bytes())
        if global_instr_path.is_file()
        else 0
    )

    root_a_json = snap.agent_jsons.get(snap.root_agent_name, {})
    root_instr_ref = root_a_json.get("instruction")
    default_root_instr = (
        snap.bundle_dir / "agents" / snap.root_agent_name / "instruction.txt"
    )
    root_instr_path = (
        snap.bundle_dir / root_instr_ref
        if isinstance(root_instr_ref, str)
        and root_instr_ref.strip()
        and (snap.bundle_dir / root_instr_ref).is_file()
        else default_root_instr
    )
    if root_instr_path.is_file():
        root_bytes = len(root_instr_path.read_bytes())
        if (
            root_bytes > ROOT_INSTRUCTION_MAX_BYTES
            or (root_bytes + global_bytes) > ROOT_INSTRUCTION_MAX_BYTES
        ):
            rel_root_instr = root_instr_path.relative_to(
                snap.bundle_dir
            ).as_posix()
            violations.append(
                FirewallViolation(
                    rule_id="FW04",
                    message=(
                        f"{snap.root_agent_name} instruction exceeds "
                        f"{ROOT_INSTRUCTION_MAX_BYTES} B "
                        f"(root={root_bytes} B, global={global_bytes} B)."
                    ),
                    path=rel_root_instr,
                )
            )
    elif global_bytes > ROOT_INSTRUCTION_MAX_BYTES:
        rel_global = global_instr_path.relative_to(snap.bundle_dir).as_posix()
        violations.append(
            FirewallViolation(
                rule_id="FW04",
                message=(
                    f"{rel_global} exceeds "
                    f"{ROOT_INSTRUCTION_MAX_BYTES} B (global={global_bytes} B)."
                ),
                path=rel_global,
            )
        )

    for a_dir in snap.agent_dirs:
        if a_dir.name == snap.root_agent_name:
            continue
        spec_instr_path = a_dir / "instruction.txt"
        if spec_instr_path.is_file():
            spec_bytes = len(spec_instr_path.read_bytes())
            if spec_bytes > SPECIALIST_INSTRUCTION_MAX_BYTES:
                violations.append(
                    FirewallViolation(
                        rule_id="FW04",
                        message=(
                            f"Specialist {a_dir.name!r} instruction is "
                            f"{spec_bytes} B "
                            f"(max {SPECIALIST_INSTRUCTION_MAX_BYTES} B)."
                        ),
                        path=f"agents/{a_dir.name}/instruction.txt",
                    )
                )


def _check_fw05_fw07_fw09(
    snap: _BundleSnapshot, violations: list[FirewallViolation]
) -> None:
    present_agents = snap.surviving_agents | {d.name for d in snap.agent_dirs}
    present_tools = set(snap.tool_jsons.keys()) | {
        d.name for d in snap.tool_dirs
    }
    present_grs = set(snap.guardrail_jsons.keys()) | {
        d.name for d in snap.guardrail_dirs
    }

    referenced_tools: set[str] = set()
    if isinstance(snap.app_json.get("tools"), list):
        referenced_tools.update(
            str(t).split("/")[-1] for t in snap.app_json["tools"]
        )
    for a_json in snap.agent_jsons.values():
        if isinstance(a_json.get("tools"), list):
            referenced_tools.update(
                str(t).split("/")[-1] for t in a_json["tools"]
            )

    referenced_grs: set[str] = set()
    if isinstance(snap.app_json.get("guardrails"), list):
        referenced_grs.update(
            str(g).split("/")[-1] for g in snap.app_json["guardrails"]
        )
    for a_json in snap.agent_jsons.values():
        if isinstance(a_json.get("guardrails"), list):
            referenced_grs.update(
                str(g).split("/")[-1] for g in a_json["guardrails"]
            )

    brand_pruned_re: re.Pattern[str] | None = None
    if snap.brand_spec is not None:
        b_agents = set(snap.brand_spec.disabled_agents)
        b_tools = set(snap.brand_spec.disabled_tools)
        b_grs = set(snap.brand_spec.disabled_guardrails)
        brand_pruned_re = build_pruned_identifier_regex(
            b_agents, b_tools | b_grs
        )
        for bad_a in sorted((present_agents | snap.root_children) & b_agents):
            violations.append(
                FirewallViolation(
                    rule_id="FW05",
                    message=(
                        f"Brand-disabled agent {bad_a!r} is present or "
                        f"attached in {snap.brand_spec.brand_id!r} bundle."
                    ),
                    path=snap.agent_rel_paths.get(bad_a, snap.root_rel_json),
                )
            )
        for bad_t in sorted((present_tools | referenced_tools) & b_tools):
            violations.append(
                FirewallViolation(
                    rule_id="FW05",
                    message=(
                        f"Brand-disabled tool {bad_t!r} is present or "
                        f"referenced in {snap.brand_spec.brand_id!r} bundle."
                    ),
                    path=snap.tool_rel_paths.get(bad_t, "app.json"),
                )
            )
        for bad_g in sorted((present_grs | referenced_grs) & b_grs):
            violations.append(
                FirewallViolation(
                    rule_id="FW05",
                    message=(
                        f"Brand-disabled guardrail {bad_g!r} is present or "
                        f"referenced in {snap.brand_spec.brand_id!r} bundle."
                    ),
                    path=snap.guardrail_rel_paths.get(bad_g, "app.json"),
                )
            )

    mod_pruned_re: re.Pattern[str] | None = None
    if snap.modality_spec is not None:
        m_agents = set(snap.modality_spec.disabled_agents)
        m_tools = set(snap.modality_spec.disabled_tools)
        m_grs = set(snap.modality_spec.disabled_guardrails)
        mod_pruned_re = build_pruned_identifier_regex(m_agents, m_tools | m_grs)
        for bad_a in sorted((present_agents | snap.root_children) & m_agents):
            violations.append(
                FirewallViolation(
                    rule_id="FW07",
                    message=(
                        f"Modality-disabled agent {bad_a!r} is present or "
                        f"attached in {snap.modality_spec.modality_id!r}."
                    ),
                    path=snap.agent_rel_paths.get(bad_a, snap.root_rel_json),
                )
            )
        for bad_t in sorted((present_tools | referenced_tools) & m_tools):
            violations.append(
                FirewallViolation(
                    rule_id="FW07",
                    message=(
                        f"Modality-disabled tool {bad_t!r} is present or "
                        f"referenced in {snap.modality_spec.modality_id!r}."
                    ),
                    path=snap.tool_rel_paths.get(bad_t, "app.json"),
                )
            )
        for bad_g in sorted((present_grs | referenced_grs) & m_grs):
            violations.append(
                FirewallViolation(
                    rule_id="FW07",
                    message=(
                        f"Modality-disabled guardrail {bad_g!r} is present or "
                        f"referenced in {snap.modality_spec.modality_id!r}."
                    ),
                    path=snap.guardrail_rel_paths.get(bad_g, "app.json"),
                )
            )

    if snap.app_json_path.is_file():
        if snap.app_json.get("locked") is not True:
            violations.append(
                FirewallViolation(
                    rule_id="FW09",
                    message=(
                        "app.json must set 'locked': true for GitOps "
                        "control-plane governance (declared lock policy: "
                        "`cxas push` imports unlocked and applies the lock "
                        "via UpdateApp after import)."
                    ),
                    path="app.json",
                )
            )
        if snap.app_json.get("toolExecutionMode") != "SEQUENTIAL":
            mode = snap.app_json.get("toolExecutionMode")
            violations.append(
                FirewallViolation(
                    rule_id="FW09",
                    message=(
                        "app.json toolExecutionMode must be 'SEQUENTIAL', "
                        f"got {mode!r}."
                    ),
                    path="app.json",
                )
            )

    if any(str(k).lower() == "deployments" for k in snap.app_json):
        violations.append(
            FirewallViolation(
                rule_id="FW09",
                message="Forbidden 'deployments' field present in app.json.",
                path="app.json",
            )
        )

    for file_path in sorted(snap.bundle_dir.rglob("*")):
        rel_obj = file_path.relative_to(snap.bundle_dir)
        rel_path = rel_obj.as_posix()
        if snap.profile == "strict" and file_path.name.lower() == "deployments":
            violations.append(
                FirewallViolation(
                    rule_id="FW09",
                    message=(
                        "Forbidden build-time deployments artifact "
                        f"present: {rel_path}."
                    ),
                    path=rel_path,
                )
            )
        if not file_path.is_file():
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        lowered_text = text.lower()
        for tok in snap.eff_forbidden_tokens:
            if tok and tok.lower() in lowered_text:
                violations.append(
                    FirewallViolation(
                        rule_id="FW05",
                        message=(
                            f"Forbidden cross-brand or pruned token {tok!r} "
                            f"found in {rel_path}."
                        ),
                        path=rel_path,
                    )
                )

        if brand_pruned_re is not None:
            m_brand = brand_pruned_re.search(text)
            if m_brand is not None:
                violations.append(
                    FirewallViolation(
                        rule_id="FW05",
                        message=(
                            f"Brand-pruned identifier {m_brand.group(0)!r} "
                            f"found in {rel_path}."
                        ),
                        path=rel_path,
                    )
                )

        if mod_pruned_re is not None:
            m_mod = mod_pruned_re.search(text)
            if m_mod is not None:
                violations.append(
                    FirewallViolation(
                        rule_id="FW07",
                        message=(
                            f"Modality-pruned identifier {m_mod.group(0)!r} "
                            f"found in {rel_path}."
                        ),
                        path=rel_path,
                    )
                )

        in_deployments = bool(rel_obj.parts) and (
            rel_obj.parts[0].lower() == "deployments"
        )
        check_single_brace = snap.profile == "strict" or (
            file_path.suffix != ".py" and not in_deployments
        )
        if check_single_brace:
            unresolved = SINGLE_BRACE_PLACEHOLDER_RE.findall(text)
            if unresolved:
                violations.append(
                    FirewallViolation(
                        rule_id="FW05",
                        message=(
                            "Unresolved single-brace placeholder(s) "
                            f"{sorted(set(unresolved))} in {rel_path}."
                        ),
                        path=rel_path,
                    )
                )

        if "{%" in text or "%}" in text:
            violations.append(
                FirewallViolation(
                    rule_id="FW09",
                    message=(
                        f"Forbidden Jinja control block found in {rel_path}."
                    ),
                    path=rel_path,
                )
            )
        if snap.profile == "strict":
            if "{@start" in text or "{@end" in text:
                violations.append(
                    FirewallViolation(
                        rule_id="FW09",
                        message=(
                            "Forbidden inline conditional modality/brand tag "
                            f"found in {rel_path}."
                        ),
                        path=rel_path,
                    )
                )
        elif COMPILE_COND_TAG_RE.search(text) or (
            ("{@start" in text or "{@end" in text)
            and _has_invalid_modality_tags(text)
        ):
            violations.append(
                FirewallViolation(
                    rule_id="FW09",
                    message=(
                        "Forbidden inline or unstripped conditional tag "
                        f"found in {rel_path}."
                    ),
                    path=rel_path,
                )
            )


def _check_fw06(
    snap: _BundleSnapshot, violations: list[FirewallViolation]
) -> None:
    for gr_name, rel_gr, gr_json in snap.all_guardrail_records:
        cf = gr_json.get("contentFilter")
        if not isinstance(cf, dict):
            continue
        for field_key in (
            "bannedContents",
            "bannedContentsInUserInput",
            "bannedContentsInAgentResponse",
        ):
            patterns = cf.get(field_key, [])
            if not isinstance(patterns, list):
                continue
            for pat in patterns:
                if not isinstance(pat, str):
                    continue
                masked_pat = _mask_regex_char_classes(pat)
                if PCRE_LOOKAROUND_RE.search(masked_pat):
                    violations.append(
                        FirewallViolation(
                            rule_id="FW06",
                            message=(
                                f"Guardrail {gr_name!r} uses non-RE2 "
                                f"lookaround in {field_key}: {pat!r}."
                            ),
                            path=rel_gr,
                        )
                    )
                if PCRE_BACKREF_RE.search(masked_pat):
                    violations.append(
                        FirewallViolation(
                            rule_id="FW06",
                            message=(
                                f"Guardrail {gr_name!r} uses non-RE2 "
                                f"backreference in {field_key}: {pat!r}."
                            ),
                            path=rel_gr,
                        )
                    )
                if PCRE_UNSUPPORTED_GROUP_RE.search(masked_pat):
                    violations.append(
                        FirewallViolation(
                            rule_id="FW06",
                            message=(
                                f"Guardrail {gr_name!r} uses non-RE2 "
                                "group/comment/inline-flag construct in "
                                f"{field_key}: {pat!r}."
                            ),
                            path=rel_gr,
                        )
                    )
                if PYTHON_ONLY_ESCAPE_RE.search(masked_pat):
                    violations.append(
                        FirewallViolation(
                            rule_id="FW06",
                            message=(
                                f"Guardrail {gr_name!r} uses non-RE2 "
                                f"Python-only escape in {field_key}: {pat!r}."
                            ),
                            path=rel_gr,
                        )
                    )
                if PCRE_POSSESSIVE_RE.search(masked_pat):
                    violations.append(
                        FirewallViolation(
                            rule_id="FW06",
                            message=(
                                f"Guardrail {gr_name!r} uses non-RE2 "
                                f"possessive quantifier in {field_key}: "
                                f"{pat!r}."
                            ),
                            path=rel_gr,
                        )
                    )
                try:
                    re.compile(pat)
                except re.error as exc:
                    violations.append(
                        FirewallViolation(
                            rule_id="FW06",
                            message=(
                                f"Guardrail {gr_name!r} has invalid regex "
                                f"{pat!r}: {exc}."
                            ),
                            path=rel_gr,
                        )
                    )


def _check_fw07_supervisors(
    snap: _BundleSnapshot, violations: list[FirewallViolation]
) -> None:
    supervisors_forbidden = snap.bundle_modality == "digital" or (
        snap.modality_spec is not None
        and not snap.modality_spec.keep_supervisor_guardrails
    )
    attached_grs = [
        str(g).split("/")[-1]
        for g in (
            snap.app_json.get("guardrails", [])
            if isinstance(snap.app_json.get("guardrails"), list)
            else []
        )
    ]
    if supervisors_forbidden:
        for g_ref in attached_grs:
            if g_ref.startswith("Supervisor_"):
                mod_label = snap.bundle_modality or "non-voice"
                violations.append(
                    FirewallViolation(
                        rule_id="FW07",
                        message=(
                            f"Supervisor guardrail {g_ref!r} is forbidden on "
                            f"{mod_label} modality."
                        ),
                        path="app.json",
                    )
                )

    for gr_name, rel_gr, gr_json in snap.all_guardrail_records:
        is_supervisor = gr_name.startswith("Supervisor_") or (
            "supervisor" in gr_json and gr_json["supervisor"] is not None
        )
        if gr_json.get("enabled") is not True:
            violations.append(
                FirewallViolation(
                    rule_id="FW07" if is_supervisor else "FW08",
                    message=(
                        f"Guardrail {gr_name!r} must set 'enabled': true, "
                        f"got {gr_json.get('enabled')!r}."
                    ),
                    path=rel_gr,
                )
            )
        if not is_supervisor:
            continue
        if supervisors_forbidden:
            mod_label = snap.bundle_modality or "non-voice"
            violations.append(
                FirewallViolation(
                    rule_id="FW07",
                    message=(
                        f"Supervisor guardrail {gr_name!r} present in "
                        f"{mod_label} bundle."
                    ),
                    path=rel_gr,
                )
            )
        if "action" in gr_json and gr_json["action"] is not None:
            violations.append(
                FirewallViolation(
                    rule_id="FW07",
                    message=(
                        f"Supervisor guardrail {gr_name!r} must not define an "
                        "'action' block."
                    ),
                    path=rel_gr,
                )
            )
        sup_block = gr_json.get("supervisor")
        det_mode = (
            sup_block.get("detectionMode")
            if isinstance(sup_block, dict)
            else None
        )
        if det_mode != "NON_BLOCKING":
            violations.append(
                FirewallViolation(
                    rule_id="FW07",
                    message=(
                        f"Supervisor guardrail {gr_name!r} must set "
                        "supervisor.detectionMode == 'NON_BLOCKING', got "
                        f"{det_mode!r}."
                    ),
                    path=rel_gr,
                )
            )


def _check_fw08(
    snap: _BundleSnapshot, violations: list[FirewallViolation]
) -> None:
    has_metadata_pools = any(
        str(k).startswith("model_pool.") for k in snap.metadata
    )
    if "modelPools" in snap.app_json:
        legacy_pools = snap.app_json.get("modelPools")
        if not isinstance(legacy_pools, dict):
            violations.append(
                FirewallViolation(
                    rule_id="FW08",
                    message=(
                        "app.json 'modelPools' must be a mapping when present."
                    ),
                    path="app.json",
                )
            )
        else:
            for pool_name in REQUIRED_MODEL_POOLS:
                pool_val = legacy_pools.get(
                    pool_name, snap.metadata.get(f"model_pool.{pool_name}")
                )
                if _is_unpinned_model(pool_val):
                    violations.append(
                        FirewallViolation(
                            rule_id="FW08",
                            message=(
                                f"Model pool {pool_name!r} has missing or "
                                f"unpinned model identifier: {pool_val!r}."
                            ),
                            path="app.json",
                        )
                    )
    elif snap.requires_bp_context or has_metadata_pools:
        for pool_name in REQUIRED_MODEL_POOLS:
            pool_val = snap.metadata.get(f"model_pool.{pool_name}")
            if _is_unpinned_model(pool_val):
                violations.append(
                    FirewallViolation(
                        rule_id="FW08",
                        message=(
                            f"Model pool 'model_pool.{pool_name}' in "
                            "app.json metadata is missing or unpinned: "
                            f"{pool_val!r}."
                        ),
                        path="app.json",
                    )
                )

    app_has_ms = (
        "modelSettings" in snap.app_json
        and snap.app_json.get("modelSettings") is not None
    )
    app_ms = snap.app_json.get("modelSettings")
    app_model = app_ms.get("model") if isinstance(app_ms, dict) else None
    app_has_pinned_model = not _is_unpinned_model(app_model)
    if (
        snap.requires_bp_context or app_has_ms or not snap.all_agent_records
    ) and not app_has_pinned_model:
        violations.append(
            FirewallViolation(
                rule_id="FW08",
                message=(
                    "app.json modelSettings.model is missing or unpinned: "
                    f"{app_model!r}."
                ),
                path="app.json",
            )
        )

    for a_name, rel_agent_json, a_json in snap.all_agent_records:
        agent_has_ms = (
            "modelSettings" in a_json
            and a_json.get("modelSettings") is not None
        )
        ms = a_json.get("modelSettings")
        m_val = ms.get("model") if isinstance(ms, dict) else None
        if snap.requires_bp_context or agent_has_ms:
            if _is_unpinned_model(m_val):
                violations.append(
                    FirewallViolation(
                        rule_id="FW08",
                        message=(
                            f"Agent {a_name!r} modelSettings.model is missing "
                            f"or unpinned: {m_val!r}."
                        ),
                        path=rel_agent_json,
                    )
                )
        elif not app_has_pinned_model:
            violations.append(
                FirewallViolation(
                    rule_id="FW08",
                    message=(
                        f"Agent {a_name!r} omits modelSettings.model and "
                        "app.json has no pinned modelSettings.model."
                    ),
                    path=rel_agent_json,
                )
            )

    for gr_name, rel_gr, gr_json in snap.all_guardrail_records:
        if "llmPolicy" in gr_json and gr_json["llmPolicy"] is not None:
            lp_block = gr_json["llmPolicy"]
            ms = (
                lp_block.get("modelSettings")
                if isinstance(lp_block, dict)
                else None
            )
            m_val = ms.get("model") if isinstance(ms, dict) else None
            if _is_unpinned_model(m_val):
                violations.append(
                    FirewallViolation(
                        rule_id="FW08",
                        message=(
                            f"Guardrail {gr_name!r} llmPolicy model is "
                            f"missing or unpinned: {m_val!r}."
                        ),
                        path=rel_gr,
                    )
                )
        if (
            "llmPromptSecurity" in gr_json
            and gr_json["llmPromptSecurity"] is not None
        ):
            lps = gr_json["llmPromptSecurity"]
            cp = lps.get("customPolicy") if isinstance(lps, dict) else None
            if (
                not isinstance(lps, dict)
                or "defaultSettings" in lps
                or not isinstance(cp, dict)
            ):
                violations.append(
                    FirewallViolation(
                        rule_id="FW08",
                        message=(
                            f"Guardrail {gr_name!r} llmPromptSecurity must "
                            "configure customPolicy with a pinned model "
                            "(defaultSettings is forbidden)."
                        ),
                        path=rel_gr,
                    )
                )
            else:
                ms = cp.get("modelSettings")
                m_val = ms.get("model") if isinstance(ms, dict) else None
                if _is_unpinned_model(m_val):
                    violations.append(
                        FirewallViolation(
                            rule_id="FW08",
                            message=(
                                f"Guardrail {gr_name!r} llmPromptSecurity "
                                f"model is missing or unpinned: {m_val!r}."
                            ),
                            path=rel_gr,
                        )
                    )
        if isinstance(gr_json.get("supervisor"), dict):
            sup = gr_json["supervisor"]
            if "modelSettings" in sup:
                ms = sup.get("modelSettings")
                m_val = ms.get("model") if isinstance(ms, dict) else None
                if _is_unpinned_model(m_val):
                    violations.append(
                        FirewallViolation(
                            rule_id="FW08",
                            message=(
                                f"Guardrail {gr_name!r} supervisor model is "
                                f"unpinned: {m_val!r}."
                            ),
                            path=rel_gr,
                        )
                    )

    for t_name, rel_tool, t_json in snap.all_tool_records:
        ds = t_json.get("dataStoreTool")
        if not isinstance(ds, dict):
            continue
        if ds.get("filterParameterBehavior") != "NEVER_INCLUDE":
            fpb = ds.get("filterParameterBehavior")
            violations.append(
                FirewallViolation(
                    rule_id="FW08",
                    message=(
                        f"DataStoreTool {t_name!r} filterParameterBehavior "
                        f"must be 'NEVER_INCLUDE', got {fpb!r}."
                    ),
                    path=rel_tool,
                )
            )
        mod_configs = ds.get("modalityConfigs", [])
        if not isinstance(mod_configs, list):
            mod_configs = []
        for mc in mod_configs:
            if not isinstance(mc, dict):
                continue
            for cfg_key in ("rewriterConfig", "summarizationConfig"):
                cfg = (
                    mc.get("rewriterConfig")
                    if cfg_key == "rewriterConfig"
                    else mc.get(
                        "summarizationConfig", mc.get("summarizerConfig")
                    )
                )
                is_disabled = (
                    isinstance(cfg, dict) and cfg.get("disabled") is True
                )
                if not is_disabled:
                    ms = (
                        cfg.get("modelSettings")
                        if isinstance(cfg, dict)
                        else None
                    )
                    m_val = ms.get("model") if isinstance(ms, dict) else None
                    if _is_unpinned_model(m_val):
                        violations.append(
                            FirewallViolation(
                                rule_id="FW08",
                                message=(
                                    f"DataStoreTool {t_name!r} active "
                                    f"{cfg_key} must specify a pinned "
                                    f"modelSettings.model, got {m_val!r}."
                                ),
                                path=rel_tool,
                            )
                        )
                elif isinstance(cfg, dict) and "modelSettings" in cfg:
                    ms = cfg.get("modelSettings")
                    m_val = ms.get("model") if isinstance(ms, dict) else None
                    if _is_unpinned_model(m_val):
                        violations.append(
                            FirewallViolation(
                                rule_id="FW08",
                                message=(
                                    f"DataStoreTool {t_name!r} {cfg_key} model "
                                    f"is unpinned: {m_val!r}."
                                ),
                                path=rel_tool,
                            )
                        )
        if snap.bundle_modality == "voice":
            audio_cfgs = [
                mc
                for mc in mod_configs
                if isinstance(mc, dict) and mc.get("modalityType") == "AUDIO"
            ]
            if not audio_cfgs:
                violations.append(
                    FirewallViolation(
                        rule_id="FW08",
                        message=(
                            f"DataStoreTool {t_name!r} on voice modality is "
                            "missing AUDIO modalityConfig."
                        ),
                        path=rel_tool,
                    )
                )
            for ac in audio_cfgs:
                rw_off = (
                    isinstance(ac.get("rewriterConfig"), dict)
                    and ac["rewriterConfig"].get("disabled") is True
                )
                sum_cfg = ac.get(
                    "summarizationConfig", ac.get("summarizerConfig")
                )
                sum_off = (
                    isinstance(sum_cfg, dict)
                    and sum_cfg.get("disabled") is True
                )
                if not (rw_off and sum_off):
                    violations.append(
                        FirewallViolation(
                            rule_id="FW08",
                            message=(
                                f"DataStoreTool {t_name!r} on voice modality "
                                "must disable AUDIO rewriterConfig and "
                                "summarizationConfig."
                            ),
                            path=rel_tool,
                        )
                    )


def _check_fw10_bundle_integrity(
    snap: _BundleSnapshot, violations: list[FirewallViolation]
) -> None:
    global_instr_ref = snap.app_json.get("globalInstruction")
    if (
        isinstance(global_instr_ref, str)
        and global_instr_ref.strip()
        and not (snap.bundle_dir / global_instr_ref).is_file()
    ):
        violations.append(
            FirewallViolation(
                rule_id="FW10",
                message=(
                    f"app.json globalInstruction file {global_instr_ref!r} "
                    "does not exist on disk."
                ),
                path="app.json",
            )
        )

    raw_root_field = snap.app_json.get("rootAgent")
    has_invalid_root_field = "rootAgent" in snap.app_json and (
        not isinstance(raw_root_field, str)
        or not raw_root_field.strip().strip("/")
    )
    if has_invalid_root_field:
        violations.append(
            FirewallViolation(
                rule_id="FW10",
                message=(
                    f"app.json rootAgent must be a non-empty agent reference, "
                    f"got {raw_root_field!r}."
                ),
                path="app.json",
            )
        )
    elif snap.root_agent_name not in snap.surviving_agents:
        violations.append(
            FirewallViolation(
                rule_id="FW10",
                message=(
                    f"app.json rootAgent {snap.root_agent_name!r} does not "
                    "exist in agents/."
                ),
                path="app.json",
            )
        )

    for g_ref in (
        snap.app_json.get("guardrails", [])
        if isinstance(snap.app_json.get("guardrails"), list)
        else []
    ):
        g_short = str(g_ref).split("/")[-1]
        if g_short not in snap.guardrail_jsons:
            violations.append(
                FirewallViolation(
                    rule_id="FW10",
                    message=(
                        "app.json guardrails references missing guardrail "
                        f"{g_short!r}."
                    ),
                    path="app.json",
                )
            )

    for t_ref in (
        snap.app_json.get("tools", [])
        if isinstance(snap.app_json.get("tools"), list)
        else []
    ):
        t_short = str(t_ref).split("/")[-1]
        if not _is_known_tool(snap, t_short):
            violations.append(
                FirewallViolation(
                    rule_id="FW10",
                    message=(
                        f"app.json tools references missing tool {t_short!r}."
                    ),
                    path="app.json",
                )
            )

    for child in sorted(snap.root_children):
        if child not in snap.surviving_agents or child == snap.root_agent_name:
            violations.append(
                FirewallViolation(
                    rule_id="FW10",
                    message=(
                        f"{snap.root_agent_name} childAgents references "
                        f"missing or invalid agent {child!r}."
                    ),
                    path=snap.root_rel_json,
                )
            )

    for spec_name in sorted(snap.surviving_agents - {snap.root_agent_name}):
        if spec_name not in snap.root_children:
            violations.append(
                FirewallViolation(
                    rule_id="FW10",
                    message=(
                        f"Orphan specialist agent {spec_name!r} in agents/ is "
                        f"not attached in {snap.root_agent_name}.childAgents."
                    ),
                    path=snap.agent_rel_paths.get(
                        spec_name, f"agents/{spec_name}/{spec_name}.json"
                    ),
                )
            )

    for a_name, a_json in sorted(snap.agent_jsons.items()):
        rel_agent = snap.agent_rel_paths.get(
            a_name, f"agents/{a_name}/{a_name}.json"
        )
        expected_instr = snap.bundle_dir / "agents" / a_name / "instruction.txt"
        instr_ref = a_json.get("instruction")
        resolved_instr = (
            snap.bundle_dir / instr_ref
            if isinstance(instr_ref, str) and instr_ref.strip()
            else expected_instr
        )
        if not expected_instr.is_file() or not resolved_instr.is_file():
            violations.append(
                FirewallViolation(
                    rule_id="FW10",
                    message=(
                        f"Agent {a_name!r} is missing its instruction.txt file "
                        "on disk."
                    ),
                    path=rel_agent,
                )
            )

        for t_ref in (
            a_json.get("tools", [])
            if isinstance(a_json.get("tools"), list)
            else []
        ):
            t_short = str(t_ref).split("/")[-1]
            if not _is_known_tool(snap, t_short):
                violations.append(
                    FirewallViolation(
                        rule_id="FW10",
                        message=(
                            f"Agent {a_name!r} references missing tool "
                            f"{t_short!r}."
                        ),
                        path=rel_agent,
                    )
                )

        for g_ref in (
            a_json.get("guardrails", [])
            if isinstance(a_json.get("guardrails"), list)
            else []
        ):
            g_short = str(g_ref).split("/")[-1]
            if g_short not in snap.guardrail_jsons:
                violations.append(
                    FirewallViolation(
                        rule_id="FW10",
                        message=(
                            f"Agent {a_name!r} references missing guardrail "
                            f"{g_short!r}."
                        ),
                        path=rel_agent,
                    )
                )

    for t_name, rel_tool, t_json in snap.all_tool_records:
        oa = t_json.get("openApiTool")
        if isinstance(oa, dict):
            schema_ref = oa.get("openApiSchema")
            if not isinstance(schema_ref, str) or not schema_ref.strip():
                violations.append(
                    FirewallViolation(
                        rule_id="FW10",
                        message=(
                            f"OpenApiTool {t_name!r} is missing openApiSchema "
                            "path."
                        ),
                        path=rel_tool,
                    )
                )
            elif not (
                (snap.bundle_dir / schema_ref).is_file()
                or (snap.bundle_dir / "tools" / t_name / schema_ref).is_file()
            ):
                violations.append(
                    FirewallViolation(
                        rule_id="FW10",
                        message=(
                            f"OpenApiTool {t_name!r} openApiSchema file "
                            f"{schema_ref!r} does not exist on disk."
                        ),
                        path=rel_tool,
                    )
                )

    for txt_file in sorted(snap.bundle_dir.rglob("*.txt")):
        rel_txt = txt_file.relative_to(snap.bundle_dir).as_posix()
        try:
            txt_content = txt_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for tag_kind, ref_name in INSTRUCTION_REF_TAG_RE.findall(txt_content):
            if tag_kind == "TOOL" and not _is_known_tool(snap, ref_name):
                violations.append(
                    FirewallViolation(
                        rule_id="FW10",
                        message=(
                            f"Instruction {rel_txt} references missing "
                            f"{{@TOOL: {ref_name}}}."
                        ),
                        path=rel_txt,
                    )
                )
            elif tag_kind == "AGENT" and ref_name not in snap.surviving_agents:
                violations.append(
                    FirewallViolation(
                        rule_id="FW10",
                        message=(
                            f"Instruction {rel_txt} references missing "
                            f"{{@AGENT: {ref_name}}}."
                        ),
                        path=rel_txt,
                    )
                )


def _check_fw10_runtime_policy(
    snap: _BundleSnapshot, violations: list[FirewallViolation]
) -> None:
    known_pruned_agents: set[str] = set(snap.extra_pruned_agents)
    if snap.brand_spec is not None:
        known_pruned_agents.update(snap.brand_spec.disabled_agents)
    if snap.modality_spec is not None:
        known_pruned_agents.update(snap.modality_spec.disabled_agents)

    known_modalities: set[str] = {"voice", "digital"}
    if (
        snap.eff_bp_dir is not None
        and (snap.eff_bp_dir / "modalities").is_dir()
    ):
        discovered_mods = {
            p.stem for p in (snap.eff_bp_dir / "modalities").glob("*.yaml")
        }
        if discovered_mods:
            known_modalities = discovered_mods

    for a_name, rel_agent, a_json in snap.all_agent_records:
        t_rules = a_json.get("transferRules", [])
        if not isinstance(t_rules, list):
            continue
        for rule in t_rules:
            if not isinstance(rule, dict):
                continue
            target = str(rule.get("childAgent", "")).split("/")[-1]
            if (
                target not in snap.surviving_agents
                or target not in snap.root_children
            ):
                violations.append(
                    FirewallViolation(
                        rule_id="FW10",
                        message=(
                            f"TransferRule in {a_name!r} references dangling "
                            f"or unattached childAgent {target!r}."
                        ),
                        path=rel_agent,
                    )
                )

            dpt = rule.get("disablePlannerTransfer")
            dt = rule.get("deterministicTransfer")
            expr = ""
            for block in (dpt, dt):
                if isinstance(block, dict):
                    ec = block.get("expressionCondition")
                    if isinstance(ec, dict) and isinstance(
                        ec.get("expression"), str
                    ):
                        expr = ec["expression"]
                        break
                    if isinstance(block.get("expression"), str):
                        expr = block["expression"]
                        break
                    if isinstance(block.get("condition"), str):
                        expr = block["condition"]
                        break
            if not expr.strip():
                violations.append(
                    FirewallViolation(
                        rule_id="FW10",
                        message=(
                            f"TransferRule for {target!r} in {a_name!r} has "
                            "empty condition expression."
                        ),
                        path=rel_agent,
                    )
                )
                continue

            if snap.eff_runtime_policy is not None:
                for surf_ref in SURFACE_EXPR_RE.findall(expr):
                    surf_spec = snap.eff_runtime_policy.surfaces.get(surf_ref)
                    if (
                        surf_spec is None
                        or surf_spec.modality != snap.bundle_modality
                    ):
                        violations.append(
                            FirewallViolation(
                                rule_id="FW10",
                                message=(
                                    "TransferRule references undeclared or "
                                    f"wrong-modality surface {surf_ref!r}."
                                ),
                                path=rel_agent,
                            )
                        )
                for seg_ref in SEGMENT_EXPR_RE.findall(expr):
                    if seg_ref not in snap.eff_runtime_policy.segments:
                        violations.append(
                            FirewallViolation(
                                rule_id="FW10",
                                message=(
                                    "TransferRule references undeclared "
                                    f"segment {seg_ref!r}."
                                ),
                                path=rel_agent,
                            )
                        )
                for ks_grouped, ks_scalar in KILL_SWITCH_EXPR_RE.findall(expr):
                    ks_agent = ks_grouped or ks_scalar
                    if ks_agent and ks_agent not in snap.surviving_agents:
                        violations.append(
                            FirewallViolation(
                                rule_id="FW10",
                                message=(
                                    "Kill-switch TransferRule references "
                                    f"missing specialist {ks_agent!r}."
                                ),
                                path=rel_agent,
                            )
                        )

    if snap.eff_runtime_policy is None:
        return

    for surf_id, surf in sorted(snap.eff_runtime_policy.surfaces.items()):
        if surf.modality not in known_modalities:
            violations.append(
                FirewallViolation(
                    rule_id="FW10",
                    message=(
                        f"Surface {surf_id!r} maps to unknown modality "
                        f"{surf.modality!r}."
                    ),
                    path="runtime_policy.yaml",
                )
            )
        if surf.modality != snap.bundle_modality:
            continue
        eff_entry = (
            surf.entry_agent
            if "entry_agent" in surf.model_fields_set
            else snap.root_agent_name
        )
        if eff_entry not in snap.surviving_agents:
            violations.append(
                FirewallViolation(
                    rule_id="FW10",
                    message=(
                        f"Surface {surf_id!r} entry_agent "
                        f"{eff_entry!r} does not exist in surviving "
                        f"agents of {snap.bundle_dir.name}."
                    ),
                    path="runtime_policy.yaml",
                )
            )
        for blocked in surf.blocked_agents:
            if (
                blocked not in snap.surviving_agents
                and blocked not in known_pruned_agents
            ):
                violations.append(
                    FirewallViolation(
                        rule_id="FW10",
                        message=(
                            f"Surface {surf_id!r} blocked_agents references "
                            f"unknown specialist {blocked!r}."
                        ),
                        path="runtime_policy.yaml",
                    )
                )

    for seg_id, seg in sorted(snap.eff_runtime_policy.segments.items()):
        for blocked in seg.blocked_agents:
            if (
                blocked not in snap.surviving_agents
                and blocked not in known_pruned_agents
            ):
                violations.append(
                    FirewallViolation(
                        rule_id="FW10",
                        message=(
                            f"Segment {seg_id!r} blocked_agents references "
                            f"unknown specialist {blocked!r}."
                        ),
                        path="runtime_policy.yaml",
                    )
                )


def run_firewall(
    app_dir: Path | str,
    blueprint_dir: Path | str | None = None,
    *,
    runtime_policy: RuntimePolicySpec | None = None,
    forbidden_tokens: Sequence[str] | None = None,
    profile: FirewallProfile = "strict",
    modality: str | None = None,
    pruned_agents: Sequence[str] = (),
) -> list[FirewallViolation]:
    """Validates a compiled CES app bundle on disk against `FW01`..`FW10`."""
    if profile not in ("strict", "standard"):
        raise BlueprintCompilerError(
            f"Unsupported firewall profile {profile!r}; "
            "expected 'strict' or 'standard'."
        )
    bundle_dir = Path(app_dir).resolve()
    violations: list[FirewallViolation] = []

    if not bundle_dir.is_dir():
        violations.append(
            FirewallViolation(
                rule_id="FW10",
                message=f"Bundle directory does not exist: {bundle_dir}",
                path=str(bundle_dir),
            )
        )
        return violations

    if blueprint_dir is not None:
        explicit_bp = Path(blueprint_dir).resolve()
        if not (explicit_bp / "blueprint.yaml").is_file():
            violations.append(
                FirewallViolation(
                    rule_id="FW10",
                    message=(
                        "Explicit blueprint_dir is invalid or missing "
                        f"blueprint.yaml: {blueprint_dir}"
                    ),
                    path=str(blueprint_dir),
                )
            )

    eff_bp_dir = _discover_blueprint_dir(bundle_dir, blueprint_dir)
    app_json_path = bundle_dir / "app.json"
    if not app_json_path.is_file():
        violations.append(
            FirewallViolation(
                rule_id="FW10",
                message="Bundle is missing required app.json.",
                path="app.json",
            )
        )
        app_json: dict[str, Any] = {}
    else:
        app_json = _read_json_safe(app_json_path, violations)

    raw_meta = app_json.get("metadata", {})
    metadata = raw_meta if isinstance(raw_meta, dict) else {}
    brand_id = str(metadata.get("brandId", "")).strip()
    bundle_modality = (
        str(modality).strip()
        if modality is not None
        else str(metadata.get("modality", "")).strip()
    )
    if not bundle_modality and "-" in bundle_dir.name:
        bundle_modality = bundle_dir.name.rsplit("-", 1)[-1]
    if not bundle_modality:
        lowered_dir = bundle_dir.name.lower()
        if "voice" in lowered_dir:
            bundle_modality = "voice"
        elif "digital" in lowered_dir:
            bundle_modality = "digital"
    if not brand_id and "-" in bundle_dir.name:
        brand_id = bundle_dir.name.rsplit("-", 1)[0]

    requires_bp_context = (
        blueprint_dir is not None
        or eff_bp_dir is not None
        or bool(metadata.get("blueprintId"))
    )

    brand_spec: BrandSpec | None = None
    modality_spec: ModalitySpec | None = None
    if eff_bp_dir is not None:
        brand_file = eff_bp_dir / "brands" / f"{brand_id}.yaml"
        if brand_id and brand_file.is_file():
            try:
                brand_spec = load_spec(BrandSpec, brand_file)
            except BlueprintCompilerError as exc:
                violations.append(
                    FirewallViolation(
                        rule_id="FW05",
                        message=f"Invalid brand manifest {brand_file}: {exc}",
                        path=str(brand_file),
                    )
                )
        mod_file = eff_bp_dir / "modalities" / f"{bundle_modality}.yaml"
        if bundle_modality and mod_file.is_file():
            try:
                modality_spec = load_spec(ModalitySpec, mod_file)
            except BlueprintCompilerError as exc:
                violations.append(
                    FirewallViolation(
                        rule_id="FW07",
                        message=f"Invalid modality manifest {mod_file}: {exc}",
                        path=str(mod_file),
                    )
                )

    if forbidden_tokens is None and brand_spec is None and requires_bp_context:
        violations.append(
            FirewallViolation(
                rule_id="FW05",
                message=(
                    f"Cannot verify brand forbidden_tokens for brand "
                    f"{brand_id!r} (missing brand manifest)."
                ),
                path="app.json",
            )
        )

    eff_forbidden_tokens: list[str] = (
        list(forbidden_tokens)
        if forbidden_tokens is not None
        else (list(brand_spec.forbidden_tokens) if brand_spec else [])
    )

    eff_runtime_policy = runtime_policy
    if eff_runtime_policy is None and eff_bp_dir is not None:
        rp_path = eff_bp_dir / "runtime_policy.yaml"
        if rp_path.is_file():
            try:
                eff_runtime_policy = load_spec(RuntimePolicySpec, rp_path)
            except BlueprintCompilerError as exc:
                violations.append(
                    FirewallViolation(
                        rule_id="FW10",
                        message=f"Invalid runtime_policy.yaml: {exc}",
                        path=str(rp_path),
                    )
                )

    if (
        runtime_policy is None
        and eff_runtime_policy is None
        and requires_bp_context
    ):
        violations.append(
            FirewallViolation(
                rule_id="FW10",
                message=(
                    "Cannot verify runtime_policy referential integrity "
                    "(missing runtime_policy.yaml)."
                ),
                path="app.json",
            )
        )

    (
        agent_dirs,
        agent_jsons,
        agent_rel_paths,
        all_agent_records,
    ) = _discover_category_jsons(bundle_dir, "agents", violations)
    (
        tool_dirs,
        tool_jsons,
        tool_rel_paths,
        all_tool_records,
    ) = _discover_category_jsons(bundle_dir, "tools", violations)
    (
        guardrail_dirs,
        guardrail_jsons,
        guardrail_rel_paths,
        all_guardrail_records,
    ) = _discover_category_jsons(bundle_dir, "guardrails", violations)
    (
        _toolset_dirs,
        _toolset_jsons,
        _toolset_rel_paths,
        all_toolset_records,
    ) = _discover_category_jsons(bundle_dir, "toolsets", violations)

    surviving_agents = set(agent_jsons.keys())
    root_agent_name = (
        str(app_json.get("rootAgent") or "root_agent")
        .strip()
        .rsplit("/", maxsplit=1)[-1]
        or "root_agent"
    )
    root_json = agent_jsons.get(root_agent_name, {})
    raw_root_children = root_json.get("childAgents", [])
    root_children = {
        str(c).split("/")[-1]
        for c in (
            raw_root_children if isinstance(raw_root_children, list) else []
        )
    }

    snap = _BundleSnapshot(
        bundle_dir=bundle_dir,
        profile=profile,
        eff_bp_dir=eff_bp_dir,
        requires_bp_context=requires_bp_context,
        root_agent_name=root_agent_name,
        app_json_path=app_json_path,
        app_json=app_json,
        metadata=metadata,
        brand_id=brand_id,
        bundle_modality=bundle_modality,
        brand_spec=brand_spec,
        modality_spec=modality_spec,
        eff_forbidden_tokens=eff_forbidden_tokens,
        eff_runtime_policy=eff_runtime_policy,
        agent_dirs=agent_dirs,
        agent_jsons=agent_jsons,
        agent_rel_paths=agent_rel_paths,
        all_agent_records=all_agent_records,
        tool_dirs=tool_dirs,
        tool_jsons=tool_jsons,
        tool_rel_paths=tool_rel_paths,
        all_tool_records=all_tool_records,
        guardrail_dirs=guardrail_dirs,
        guardrail_jsons=guardrail_jsons,
        guardrail_rel_paths=guardrail_rel_paths,
        all_guardrail_records=all_guardrail_records,
        all_toolset_records=all_toolset_records,
        surviving_agents=surviving_agents,
        root_children=root_children,
        extra_pruned_agents=frozenset(pruned_agents),
    )

    _check_fw01_fw02(snap, violations)
    _check_fw03(snap, violations)
    _check_fw04(snap, violations)
    _check_fw05_fw07_fw09(snap, violations)
    _check_fw06(snap, violations)
    _check_fw07_supervisors(snap, violations)
    _check_fw08(snap, violations)
    _check_fw10_bundle_integrity(snap, violations)
    _check_fw10_runtime_policy(snap, violations)

    return violations


__all__ = [
    "FirewallError",
    "FirewallProfile",
    "FirewallViolation",
    "run_firewall",
]
