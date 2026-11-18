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

"""Polymorphic `(Brand x Modality)` CES blueprint compiler and zip packager."""

from __future__ import annotations

import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import yaml

from cxas_scrapi.build.blueprint_schemas import (
    SINGLE_BRACE_PLACEHOLDER_RE,
    BlueprintCompilerError,
    BlueprintSpec,
    BrandSpec,
    FirewallError,
    FirewallViolation,
    GuardrailSpec,
    ModalitySpec,
    RuntimePolicySpec,
    SpecialistSpec,
    ToolSpec,
    TuningOverlaySpec,
    VariablePropertySpec,
    VariableSpec,
    build_pruned_identifier_regex,
    load_json_dict_strict,
    load_spec,
    read_utf8_strict,
    write_canonical_json,
    write_text,
)
from cxas_scrapi.build.drift import write_lockfile
from cxas_scrapi.build.firewall import run_firewall
from cxas_scrapi.core.packaging import package_app_archive

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

MARKDOWN_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
MARKDOWN_HEADING_RE = re.compile(r"^\s*#")


@dataclass
class BuildBundleResult:
    """Compilation summary for a single `(brand, modality)` CES app bundle."""

    app_id: str
    brand_id: str
    modality_id: str
    out_dir: Path
    active_agents: list[str] = field(default_factory=list)
    pruned_agents: list[str] = field(default_factory=list)
    active_tools: list[str] = field(default_factory=list)
    pruned_tools: list[str] = field(default_factory=list)
    active_guardrails: list[str] = field(default_factory=list)
    pruned_guardrails: list[str] = field(default_factory=list)
    runtime_rules_count: int = 0
    violations: list[FirewallViolation] = field(default_factory=list)
    lockfile_path: Path | None = None
    zip_path: Path | None = None

    @property
    def transfer_rules_count(self) -> int:
        return self.runtime_rules_count

    @property
    def app_dir(self) -> Path:
        return self.out_dir

    @property
    def bundle_dir(self) -> Path:
        return self.out_dir

    @property
    def modality(self) -> str:
        return self.modality_id

    @property
    def surviving_agents(self) -> list[str]:
        return self.active_agents

    @property
    def surviving_tools(self) -> list[str]:
        return self.active_tools

    @property
    def surviving_guardrails(self) -> list[str]:
        return self.active_guardrails

    @property
    def firewall_violations(self) -> list[FirewallViolation]:
        return self.violations

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_agents": self.active_agents,
            "active_guardrails": self.active_guardrails,
            "active_tools": self.active_tools,
            "app_id": self.app_id,
            "brand_id": self.brand_id,
            "lockfile_path": (
                str(self.lockfile_path) if self.lockfile_path else None
            ),
            "modality_id": self.modality_id,
            "out_dir": str(self.out_dir),
            "pruned_agents": self.pruned_agents,
            "pruned_guardrails": self.pruned_guardrails,
            "pruned_tools": self.pruned_tools,
            "runtime_rules_count": self.runtime_rules_count,
            "violations": [v.to_dict() for v in self.violations],
            "zip_path": str(self.zip_path) if self.zip_path else None,
        }


def _build_placeholder_map(
    brand: BrandSpec, modality: ModalitySpec
) -> dict[str, str]:
    mapping: dict[str, str] = {
        "brand_id": brand.brand_id,
        "brand_name": brand.brand_name,
        "brand_slug": brand.brand_slug,
        "tenant_id": str(brand.tenant_id),
        "web_host": brand.web_host,
        "ai_disclosure": brand.ai_disclosure,
        "modality_id": modality.modality_id,
        "modality_label": modality.modality_label,
    }
    for k, v in brand.placeholders.items():
        mapping[k] = str(v)

    for _ in range(5):
        changed = False
        for k, v in list(mapping.items()):
            resolved_v = SINGLE_BRACE_PLACEHOLDER_RE.sub(
                lambda m: mapping.get(m.group(1), m.group(0)), v
            )
            if resolved_v != v:
                mapping[k] = resolved_v
                changed = True
        if not changed:
            break
    return mapping


def _render_placeholders(text: str, placeholders: dict[str, str]) -> str:
    """Replaces known single-brace `{key}` tokens while preserving `{{var}}`."""
    return SINGLE_BRACE_PLACEHOLDER_RE.sub(
        lambda m: placeholders.get(m.group(1), m.group(0)),
        text,
    )


def _strip_dangling_reference_lines(
    text: str, pruned_re: re.Pattern[str] | None
) -> str:
    if pruned_re is None:
        return re.sub(r"\n{3,}", "\n\n", text)

    lines = text.splitlines()
    kept_lines: list[str] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if MARKDOWN_BULLET_RE.match(line):
            block = [line]
            idx += 1
            while idx < len(lines):
                nxt = lines[idx]
                if (
                    nxt.strip() != ""
                    and (nxt.startswith("  ") or nxt.startswith("\t"))
                    and not MARKDOWN_HEADING_RE.match(nxt)
                    and not MARKDOWN_BULLET_RE.match(nxt)
                ):
                    block.append(nxt)
                    idx += 1
                else:
                    break
            if not pruned_re.search("\n".join(block)):
                kept_lines.extend(block)
        else:
            if not pruned_re.search(line):
                kept_lines.append(line)
            idx += 1

    joined = "\n".join(kept_lines)
    return re.sub(r"\n{3,}", "\n\n", joined)


def _render_instruction_text(
    raw_instruction: str,
    *,
    placeholders: dict[str, str],
    pruned_re: re.Pattern[str] | None,
    modality_id: str,
    overlay_rules: Sequence[str] = (),
) -> str:
    cleaned = _strip_dangling_reference_lines(raw_instruction, pruned_re)
    rendered = _render_placeholders(cleaned, placeholders)
    rendered = _strip_dangling_reference_lines(rendered, pruned_re)

    if overlay_rules:
        rendered_rules: list[str] = []
        for rule in overlay_rules:
            if pruned_re is not None and pruned_re.search(rule):
                continue
            r_text = _render_placeholders(rule.strip(), placeholders)
            if pruned_re is not None and pruned_re.search(r_text):
                continue
            if r_text:
                rendered_rules.append(f"- {r_text}")
        if rendered_rules:
            rendered = (
                rendered.rstrip("\n")
                + f"\n\n## Modality Tuning Rules ({modality_id})\n"
                + "\n".join(rendered_rules)
            )

    return rendered.rstrip("\n") + "\n"


def _render_variable_property_schema(
    prop: VariablePropertySpec,
    *,
    placeholders: dict[str, str],
    pruned_agents: set[str],
    is_disabled_agents_map: bool = False,
) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": prop.type}
    if prop.description:
        schema["description"] = _render_placeholders(
            prop.description, placeholders
        )
    if prop.properties:
        rendered_props: dict[str, Any] = {}
        for child_name, child_prop in prop.properties.items():
            if is_disabled_agents_map and child_name in pruned_agents:
                continue
            rendered_props[child_name] = _render_variable_property_schema(
                child_prop,
                placeholders=placeholders,
                pruned_agents=pruned_agents,
                is_disabled_agents_map=(child_name == "disabled_agents"),
            )
        schema["properties"] = rendered_props
    if prop.type.upper() == "OBJECT":
        schema.setdefault("required", [])
    return schema


def _append_scalar_mirror_declarations(
    declarations: list[dict[str, Any]],
    surviving_specialists: Sequence[str],
) -> list[dict[str, Any]]:
    declared_names: set[str] = {
        str(d.get("name"))
        for d in declarations
        if isinstance(d, dict) and d.get("name")
    }
    scalar_mirrors: list[dict[str, Any]] = [
        {
            "description": (
                "Flat scalar mirror of session_ctx.surface_id for runtime "
                "CEL condition evaluation."
            ),
            "name": "surface_id",
            "schema": {"required": [], "type": "STRING"},
        },
        {
            "description": (
                "Flat scalar mirror of session_ctx.segment for runtime "
                "CEL condition evaluation."
            ),
            "name": "segment",
            "schema": {"required": [], "type": "STRING"},
        },
    ]
    for agent_name in sorted(surviving_specialists):
        scalar_mirrors.append(
            {
                "description": (
                    f"Flat scalar operational kill-switch mirror for "
                    f"{agent_name}."
                ),
                "name": f"disabled_agent_{agent_name}",
                "schema": {"required": [], "type": "BOOLEAN"},
            }
        )

    out = list(declarations)
    for mirror in scalar_mirrors:
        if mirror["name"] not in declared_names:
            out.append(mirror)
            declared_names.add(mirror["name"])
    return out


def _render_variable_declarations(
    variables: Sequence[VariableSpec],
    *,
    placeholders: dict[str, str],
    pruned_agents: set[str],
    surviving_specialists: Sequence[str] = (),
) -> list[dict[str, Any]]:
    declarations: list[dict[str, Any]] = []
    for var in variables:
        schema: dict[str, Any] = {"type": var.type}
        if var.properties:
            rendered_props: dict[str, Any] = {}
            for prop_name, prop_spec in var.properties.items():
                rendered_props[prop_name] = _render_variable_property_schema(
                    prop_spec,
                    placeholders=placeholders,
                    pruned_agents=pruned_agents,
                    is_disabled_agents_map=(
                        var.name == "session_ctx"
                        and prop_name == "disabled_agents"
                    ),
                )
            schema["properties"] = rendered_props
        schema.setdefault("required", [])
        decl: dict[str, Any] = {
            "name": var.name,
            "schema": schema,
        }
        if var.description:
            decl["description"] = _render_placeholders(
                var.description, placeholders
            )
        declarations.append(decl)

    return _append_scalar_mirror_declarations(
        declarations, surviving_specialists
    )


def _build_transfer_rules(
    *,
    surviving_child_agents: Sequence[str],
    disabled_agents: set[str],
    modality_id: str,
    runtime_policy: RuntimePolicySpec,
) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def _append_rule(target_agent: str, expr: str) -> None:
        key = (target_agent, "PARENT_TO_CHILD", expr)
        if key in seen:
            return
        seen.add(key)
        rules.append(
            {
                "childAgent": target_agent,
                "direction": "PARENT_TO_CHILD",
                "disablePlannerTransfer": {
                    "expressionCondition": {
                        "expression": expr,
                    }
                },
            }
        )

    if runtime_policy.kill_switch.enabled:
        for agent_name in surviving_child_agents:
            expr = runtime_policy.kill_switch.expression_template.format(
                agent=agent_name
            )
            _append_rule(agent_name, expr)
            _append_rule(agent_name, f"disabled_agent_{agent_name} == true")

    for surface_id, surf in sorted(runtime_policy.surfaces.items()):
        if surf.modality != modality_id:
            continue
        for blocked in surf.blocked_agents:
            if blocked in disabled_agents:
                continue
            _append_rule(blocked, f"session_ctx.surface_id == '{surface_id}'")
            _append_rule(blocked, f"surface_id == '{surface_id}'")

    for seg_id, seg in sorted(runtime_policy.segments.items()):
        for blocked in seg.blocked_agents:
            if blocked in disabled_agents:
                continue
            _append_rule(blocked, f"session_ctx.segment == '{seg_id}'")
            _append_rule(blocked, f"segment == '{seg_id}'")

    return rules


def _emit_tool_files(
    tool_dir: Path,
    tool_name: str,
    tool: ToolSpec,
    *,
    placeholders: dict[str, str],
    blueprint: BlueprintSpec,
    modality: ModalitySpec,
) -> None:
    rendered_desc = _render_placeholders(tool.description, placeholders)
    tool_json: dict[str, Any] = {
        "displayName": tool_name,
        "executionType": tool.execution_type,
    }

    if tool.kind == "open_api":
        rendered_url = _render_placeholders(
            tool.url_template or "", placeholders
        )
        parsed = urlsplit(rendered_url)
        server_url = (
            f"{parsed.scheme}://{parsed.netloc}"
            if parsed.scheme and parsed.netloc
            else rendered_url
        )
        endpoint_path = parsed.path or "/"
        http_method = (tool.method or "POST").lower()
        op_id = tool.operation_id or tool_name

        operation_obj: dict[str, Any] = {
            "operationId": op_id,
            "summary": rendered_desc,
            "description": rendered_desc,
            "responses": {
                "200": {
                    "description": "Successful backend API response.",
                    "content": {
                        "application/json": {"schema": {"type": "object"}}
                    },
                }
            },
        }
        if http_method == "post":
            operation_obj["requestBody"] = {
                "required": False,
                "content": {"application/json": {"schema": {"type": "object"}}},
            }

        openapi_doc = {
            "openapi": "3.0.0",
            "info": {
                "title": tool_name,
                "version": "1.0.0",
                "description": rendered_desc,
            },
            "servers": [{"url": server_url}],
            "paths": {
                endpoint_path: {
                    http_method: operation_obj,
                }
            },
        }
        schema_rel = f"tools/{tool_name}/open_api_tool/open_api_schema.yaml"
        schema_path = tool_dir / "open_api_tool" / "open_api_schema.yaml"
        schema_path.parent.mkdir(parents=True, exist_ok=True)
        schema_path.write_text(
            yaml.safe_dump(openapi_doc, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        tool_json["openApiTool"] = {
            "apiAuthentication": {"serviceAgentIdTokenAuthConfig": {}},
            "description": rendered_desc,
            "name": tool_name,
            "openApiSchema": schema_rel,
            "url": rendered_url,
        }

    elif tool.kind == "data_store":
        rendered_ds = _render_placeholders(
            tool.data_store_id_template or "", placeholders
        )
        ds_model = blueprint.model_pools.datastore_rewriter_summarizer
        audio_disabled = bool(modality.audio_rewriter_disabled)
        rendered_engine = rendered_ds.replace("/dataStores/", "/engines/")
        tool_json["dataStoreTool"] = {
            "description": rendered_desc,
            "engineSource": {
                "dataStoreSources": [
                    {"dataStore": {"name": rendered_ds}},
                ],
                "engine": rendered_engine,
            },
            "filterParameterBehavior": (
                tool.filter_parameter_behavior or "NEVER_INCLUDE"
            ),
            "modalityConfigs": [
                {
                    "modalityType": "TEXT",
                    "rewriterConfig": {
                        "disabled": False,
                        "modelSettings": {
                            "model": ds_model,
                            "temperature": 1.0,
                        },
                    },
                    "summarizationConfig": {
                        "disabled": False,
                        "modelSettings": {
                            "model": ds_model,
                            "temperature": 1.0,
                        },
                    },
                },
                {
                    "modalityType": "AUDIO",
                    "rewriterConfig": {
                        "disabled": audio_disabled,
                        "modelSettings": {
                            "model": ds_model,
                            "temperature": 1.0,
                        },
                    },
                    "summarizationConfig": {
                        "disabled": audio_disabled,
                        "modelSettings": {
                            "model": ds_model,
                            "temperature": 1.0,
                        },
                    },
                },
            ],
            "name": tool_name,
        }

    elif tool.kind == "python_function":
        code_rel = "python_function/python_code.py"
        code_text = _render_placeholders(
            tool.python_code or f"def {tool_name}(args):\n    return {{}}\n",
            placeholders,
        )
        write_text(tool_dir / code_rel, code_text)
        tool_json["pythonFunction"] = {
            "description": rendered_desc,
            "name": tool_name,
            "pythonCode": code_rel,
        }

    write_canonical_json(tool_dir / f"{tool_name}.json", tool_json)


def _emit_guardrail_file(
    guardrail_dir: Path,
    gr_name: str,
    gr: GuardrailSpec,
    *,
    placeholders: dict[str, str],
    blueprint: BlueprintSpec,
) -> None:
    rendered_desc = _render_placeholders(gr.description, placeholders)
    judge_model = blueprint.model_pools.guardrail_judge
    gr_json: dict[str, Any] = {
        "displayName": gr_name,
        "description": rendered_desc,
        "enabled": gr.enabled,
    }

    if gr.kind == "content_filter":
        action_txt = _render_placeholders(gr.action_text or "", placeholders)
        gr_json["action"] = {
            "respondImmediately": {
                "responses": [{"disabled": False, "text": action_txt}]
            }
        }
        gr_json["contentFilter"] = {
            "bannedContents": [
                _render_placeholders(pat, placeholders) for pat in gr.patterns
            ],
            "bannedContentsInAgentResponse": [],
            "bannedContentsInUserInput": [],
            "disregardDiacritics": False,
            "matchType": gr.match_type or "REGEXP_MATCH",
        }

    elif gr.kind == "llm_policy":
        action_txt = _render_placeholders(gr.action_text or "", placeholders)
        gr_json["action"] = {
            "respondImmediately": {
                "responses": [{"disabled": False, "text": action_txt}]
            }
        }
        gr_json["llmPolicy"] = {
            "allowShortUtterance": True,
            "failOpen": True,
            "maxConversationMessages": (
                gr.max_conversation_messages
                if gr.max_conversation_messages is not None
                else 6
            ),
            "modelSettings": {
                "model": judge_model,
                "temperature": 1.0,
            },
            "policyScope": gr.policy_scope or "USER_QUERY_AND_AGENT_RESPONSE",
            "prompt": _render_placeholders(gr.prompt or "", placeholders),
        }

    elif gr.kind == "llm_prompt_security":
        action_txt = _render_placeholders(gr.action_text or "", placeholders)
        gr_json["action"] = {
            "respondImmediately": {
                "responses": [{"disabled": False, "text": action_txt}]
            }
        }
        gr_json["llmPromptSecurity"] = {
            "customPolicy": {
                "allowShortUtterance": True,
                "failOpen": True,
                "maxConversationMessages": (
                    gr.max_conversation_messages
                    if gr.max_conversation_messages is not None
                    else 6
                ),
                "modelSettings": {
                    "model": judge_model,
                    "temperature": 1.0,
                },
                "policyScope": gr.policy_scope or "USER_QUERY",
                "prompt": _render_placeholders(gr.prompt or "", placeholders),
            }
        }

    elif gr.kind == "supervisor":
        if gr.action_text is not None:
            action_txt = _render_placeholders(gr.action_text, placeholders)
            gr_json["action"] = {
                "respondImmediately": {
                    "responses": [{"disabled": False, "text": action_txt}]
                }
            }
        sup_type = gr.supervisor_type
        if not sup_type:
            stem = gr_name.removeprefix("Supervisor_")
            sup_type = stem.upper()
        gr_json["supervisor"] = {
            "detectionMode": gr.detection_mode or "NON_BLOCKING",
            "type": sup_type,
        }

    write_canonical_json(guardrail_dir / f"{gr_name}.json", gr_json)


def apply_runtime_policy_to_bundle(
    bundle_dir: Path | str,
    runtime_policy: RuntimePolicySpec,
    *,
    modality_id: str,
    disabled_agents: set[str] | None = None,
) -> int:
    """Synthesizes CEL TransferRules and flat scalar mirrors into a bundle."""
    b_dir = Path(bundle_dir).resolve()
    app_json_path = b_dir / "app.json"
    app_json = load_json_dict_strict(app_json_path, context="app.json")

    root_agent_name = (
        str(app_json.get("rootAgent") or "root_agent")
        .strip()
        .rsplit("/", maxsplit=1)[-1]
        or "root_agent"
    )
    root_dir = b_dir / "agents" / root_agent_name
    root_json_path = root_dir / f"{root_agent_name}.json"
    if not root_json_path.is_file() and root_dir.is_dir():
        candidates = sorted(root_dir.glob("*.json"))
        if candidates:
            root_json_path = candidates[0]
    if not root_json_path.is_file():
        raise BlueprintCompilerError(
            f"Root agent JSON for {root_agent_name!r} not found in {b_dir}"
        )
    root_json = load_json_dict_strict(
        root_json_path, context=f"{root_agent_name}.json"
    )

    raw_children = root_json.get("childAgents", [])
    surviving_child_agents: list[str] = []
    if isinstance(raw_children, list):
        for child in raw_children:
            c_name = (
                str(child.get("displayName", ""))
                if isinstance(child, dict)
                else str(child)
            ).split("/")[-1]
            if c_name:
                surviving_child_agents.append(c_name)
    surviving_set = set(surviving_child_agents)

    if disabled_agents is not None:
        eff_disabled = set(disabled_agents)
    else:
        referenced_blocked = {
            blocked
            for surf in runtime_policy.surfaces.values()
            for blocked in surf.blocked_agents
        } | {
            blocked
            for seg in runtime_policy.segments.values()
            for blocked in seg.blocked_agents
        }
        eff_disabled = referenced_blocked - surviving_set

    transfer_rules = _build_transfer_rules(
        surviving_child_agents=surviving_child_agents,
        disabled_agents=eff_disabled,
        modality_id=modality_id,
        runtime_policy=runtime_policy,
    )
    root_json["transferRules"] = transfer_rules
    write_canonical_json(root_json_path, root_json)

    raw_vars = app_json.get("variableDeclarations", [])
    var_decls: list[dict[str, Any]] = (
        [dict(d) for d in raw_vars if isinstance(d, dict)]
        if isinstance(raw_vars, list)
        else []
    )
    app_json["variableDeclarations"] = _append_scalar_mirror_declarations(
        var_decls, surviving_child_agents
    )
    write_canonical_json(app_json_path, app_json)
    return len(transfer_rules)


def apply_tuning_overlay_to_bundle(
    bundle_dir: Path | str,
    tuning_overlay: TuningOverlaySpec,
    *,
    modality_id: str,
    placeholders: Mapping[str, str],
    pruned_re: re.Pattern[str] | None = None,
) -> None:
    """Appends modality-scoped tuning overlay rules to agent instructions."""
    modality_overlays = tuning_overlay.overlays.get(modality_id, {})
    if not modality_overlays:
        return

    b_dir = Path(bundle_dir).resolve()
    app_json_path = b_dir / "app.json"
    root_agent_name = "root_agent"
    if app_json_path.is_file():
        app_json = load_json_dict_strict(app_json_path, context="app.json")
        root_agent_name = (
            str(app_json.get("rootAgent") or "root_agent")
            .strip()
            .rsplit("/", maxsplit=1)[-1]
            or "root_agent"
        )

    ph_dict = {str(k): str(v) for k, v in placeholders.items()}
    for agent_key, overlay_spec in sorted(modality_overlays.items()):
        if not overlay_spec.append_rules:
            continue
        target_dir = b_dir / "agents" / agent_key
        if (
            not target_dir.is_dir()
            and agent_key == "root_agent"
            and (b_dir / "agents" / root_agent_name).is_dir()
        ):
            target_dir = b_dir / "agents" / root_agent_name
        instr_path = target_dir / "instruction.txt"
        if not instr_path.is_file():
            continue

        rendered_rules: list[str] = []
        for rule in overlay_spec.append_rules:
            if pruned_re is not None and pruned_re.search(rule):
                continue
            r_text = _render_placeholders(rule.strip(), ph_dict)
            if pruned_re is not None and pruned_re.search(r_text):
                continue
            if r_text:
                rendered_rules.append(f"- {r_text}")
        if not rendered_rules:
            continue

        existing = read_utf8_strict(
            instr_path, context=f"{target_dir.name} instruction.txt"
        )
        updated = (
            existing.rstrip("\n")
            + f"\n\n## Modality Tuning Rules ({modality_id})\n"
            + "\n".join(rendered_rules)
            + "\n"
        )
        write_text(instr_path, updated)


def package_bundle_zip(
    app_dir: Path | str,
    zip_path: Path | str | None = None,
) -> Path:
    """Writes a bit-for-bit deterministic CES `ImportApp` `.zip` archive."""
    bundle_dir = Path(app_dir).resolve()
    if not bundle_dir.is_dir():
        raise BlueprintCompilerError(
            f"Bundle directory does not exist: {bundle_dir}"
        )
    target_zip = (
        Path(zip_path).resolve()
        if zip_path is not None
        else bundle_dir.parent / f"{bundle_dir.name}.zip"
    )
    return package_app_archive(
        bundle_dir, target_zip, wrapper_dir=bundle_dir.name
    )


def _compile_single_bundle(
    *,
    blueprint_dir: Path,
    out_root: Path,
    blueprint: BlueprintSpec,
    brand: BrandSpec,
    modality: ModalitySpec,
    runtime_policy: RuntimePolicySpec,
    tuning_overlay: TuningOverlaySpec,
    write_locks: bool,
    package_zips: bool,
    fail_on_firewall: bool,
) -> BuildBundleResult:
    app_id = f"{brand.brand_id}-{modality.modality_id}"
    bundle_dir = out_root / app_id
    stale_zip = out_root / f"{app_id}.zip"
    if stale_zip.is_file():
        stale_zip.unlink()
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    disabled_agents = set(brand.disabled_agents) | set(modality.disabled_agents)
    disabled_tools = set(brand.disabled_tools) | set(modality.disabled_tools)
    disabled_guardrails = set(brand.disabled_guardrails) | set(
        modality.disabled_guardrails
    )

    if not modality.keep_supervisor_guardrails:
        for gr_name, gr_spec in blueprint.guardrails.items():
            if gr_name.startswith("Supervisor_") or (
                gr_spec.kind == "supervisor"
            ):
                disabled_guardrails.add(gr_name)

    surviving_specialists: dict[str, SpecialistSpec] = {
        name: spec
        for name, spec in blueprint.specialists.items()
        if name not in disabled_agents
    }
    pruned_agents_set = (
        set(blueprint.specialists.keys()) - set(surviving_specialists.keys())
    ) | disabled_agents

    surviving_child_agents = [
        a
        for a in blueprint.root_agent.sub_agents
        if a not in pruned_agents_set and a in surviving_specialists
    ]

    root_tools = [
        t for t in blueprint.root_agent.tools if t not in disabled_tools
    ]
    spec_tools_map: dict[str, list[str]] = {
        name: [t for t in spec.tools if t not in disabled_tools]
        for name, spec in surviving_specialists.items()
    }
    referenced_tools: set[str] = set(root_tools) | {
        t for t_list in spec_tools_map.values() for t in t_list
    }

    surviving_tools: dict[str, ToolSpec] = {
        t_name: t_spec
        for t_name, t_spec in blueprint.tools.items()
        if t_name not in disabled_tools and t_name in referenced_tools
    }
    pruned_tools_set = (
        set(blueprint.tools.keys()) - set(surviving_tools.keys())
    ) | disabled_tools

    surviving_guardrails: dict[str, GuardrailSpec] = {
        g_name: g_spec
        for g_name, g_spec in blueprint.guardrails.items()
        if g_name not in disabled_guardrails
    }
    pruned_guardrails_set = (
        set(blueprint.guardrails.keys()) - set(surviving_guardrails.keys())
    ) | disabled_guardrails

    placeholders = _build_placeholder_map(brand, modality)
    pruned_re = build_pruned_identifier_regex(
        pruned_agents_set, pruned_tools_set
    )

    effective_planner_model = (
        modality.planner_model_override or blueprint.model_pools.agent_planner
    )

    global_instr_override = blueprint_dir / "global_instruction.txt"
    raw_global_instr = (
        global_instr_override.read_text(encoding="utf-8")
        if global_instr_override.is_file()
        else blueprint.global_instruction
    )
    rendered_global_instr = _render_instruction_text(
        raw_global_instr,
        placeholders=placeholders,
        pruned_re=pruned_re,
        modality_id=modality.modality_id,
    )
    write_text(bundle_dir / "global_instruction.txt", rendered_global_instr)

    root_instr_override = (
        blueprint_dir / "agents" / "root_agent" / "instruction.txt"
    )
    raw_root_instr = (
        root_instr_override.read_text(encoding="utf-8")
        if root_instr_override.is_file()
        else blueprint.root_agent.instruction
    )
    rendered_root_instr = _render_instruction_text(
        raw_root_instr,
        placeholders=placeholders,
        pruned_re=pruned_re,
        modality_id=modality.modality_id,
    )
    root_dir = bundle_dir / "agents" / "root_agent"
    write_text(root_dir / "instruction.txt", rendered_root_instr)

    root_json: dict[str, Any] = {
        "childAgents": surviving_child_agents,
        "description": _render_placeholders(
            blueprint.root_agent.description, placeholders
        ),
        "displayName": "root_agent",
        "instruction": "agents/root_agent/instruction.txt",
        "modelSettings": {
            "model": effective_planner_model,
            "temperature": 1.0,
        },
        "tools": root_tools,
        "transferRules": [],
    }
    if blueprint.root_agent.callbacks:
        root_json["callbacks"] = list(blueprint.root_agent.callbacks)
    write_canonical_json(root_dir / "root_agent.json", root_json)

    for spec_name in sorted(surviving_specialists):
        spec = surviving_specialists[spec_name]
        spec_dir = bundle_dir / "agents" / spec_name
        spec_instr_override = (
            blueprint_dir / "agents" / spec_name / "instruction.txt"
        )
        raw_spec_instr = (
            spec_instr_override.read_text(encoding="utf-8")
            if spec_instr_override.is_file()
            else spec.instruction
        )
        rendered_spec_instr = _render_instruction_text(
            raw_spec_instr,
            placeholders=placeholders,
            pruned_re=pruned_re,
            modality_id=modality.modality_id,
        )
        write_text(spec_dir / "instruction.txt", rendered_spec_instr)

        spec_json: dict[str, Any] = {
            "description": _render_placeholders(spec.description, placeholders),
            "displayName": spec_name,
            "instruction": f"agents/{spec_name}/instruction.txt",
            "modelSettings": {
                "model": effective_planner_model,
                "temperature": 1.0,
            },
            "tools": spec_tools_map[spec_name],
        }
        if spec.callbacks:
            spec_json["callbacks"] = list(spec.callbacks)
        write_canonical_json(spec_dir / f"{spec_name}.json", spec_json)

    for tool_name in sorted(surviving_tools):
        _emit_tool_files(
            bundle_dir / "tools" / tool_name,
            tool_name,
            surviving_tools[tool_name],
            placeholders=placeholders,
            blueprint=blueprint,
            modality=modality,
        )

    for gr_name in sorted(surviving_guardrails):
        _emit_guardrail_file(
            bundle_dir / "guardrails" / gr_name,
            gr_name,
            surviving_guardrails[gr_name],
            placeholders=placeholders,
            blueprint=blueprint,
        )

    var_declarations = _render_variable_declarations(
        blueprint.variables,
        placeholders=placeholders,
        pruned_agents=pruned_agents_set,
        surviving_specialists=sorted(surviving_specialists.keys()),
    )
    tts_lang = blueprint.default_language_code or "en-US"
    app_uuid = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"https://schemas.cxas.example/blueprint/{app_id}",
    )
    app_json: dict[str, Any] = {
        "audioProcessingConfig": {
            "bargeInConfig": {
                "bargeInAwareness": modality.barge_in_enabled,
            },
            "inactivityTimeout": "20s",
            "synthesizeSpeechConfigs": {
                tts_lang: {
                    "speakingRate": brand.voice.speaking_rate,
                    "voice": brand.voice.voice_name,
                }
            },
        },
        "displayName": _render_placeholders(
            blueprint.display_name_template, placeholders
        ),
        "globalInstruction": "global_instruction.txt",
        "guardrails": sorted(surviving_guardrails.keys()),
        "languageSettings": {
            "defaultLanguageCode": blueprint.default_language_code,
            "enableMultilingualSupport": (
                len(blueprint.supported_language_codes) > 1
            ),
            "supportedLanguageCodes": list(blueprint.supported_language_codes),
        },
        "locked": blueprint.locked,
        "metadata": {
            "audioRewriterDisabled": (
                "true" if modality.audio_rewriter_disabled else "false"
            ),
            "blueprintId": blueprint.blueprint_id,
            "brandId": brand.brand_id,
            "modality": modality.modality_id,
            "model_pool.agent_planner": effective_planner_model,
            "model_pool.datastore_rewriter_summarizer": (
                blueprint.model_pools.datastore_rewriter_summarizer
            ),
            "model_pool.eval_judge": blueprint.model_pools.eval_judge,
            "model_pool.guardrail_judge": blueprint.model_pools.guardrail_judge,
            "model_pool.voice_talker_thinker": (
                blueprint.model_pools.voice_talker_thinker
            ),
            "tenantId": str(brand.tenant_id),
        },
        "modelSettings": {
            "model": effective_planner_model,
            "temperature": 1.0,
        },
        "name": f"projects/example-project/locations/global/apps/{app_uuid}",
        "rootAgent": "root_agent",
        "timeZoneSettings": {
            "timeZone": blueprint.time_zone,
        },
        "toolExecutionMode": blueprint.tool_execution_mode,
        "variableDeclarations": var_declarations,
    }
    write_canonical_json(bundle_dir / "app.json", app_json)

    apply_tuning_overlay_to_bundle(
        bundle_dir,
        tuning_overlay,
        modality_id=modality.modality_id,
        placeholders=placeholders,
        pruned_re=pruned_re,
    )
    runtime_rules_count = apply_runtime_policy_to_bundle(
        bundle_dir,
        runtime_policy,
        modality_id=modality.modality_id,
        disabled_agents=pruned_agents_set,
    )

    violations = run_firewall(
        bundle_dir,
        blueprint_dir=blueprint_dir,
        runtime_policy=runtime_policy,
        forbidden_tokens=brand.forbidden_tokens,
    )
    if violations and fail_on_firewall:
        summary = "; ".join(str(v) for v in violations)
        raise FirewallError(
            f"Firewall validation failed for {app_id} "
            f"({len(violations)} violation(s)): {summary}",
            violations=violations,
        )

    lock_path: Path | None = None
    if write_locks and not violations:
        lock_path = write_lockfile(bundle_dir)

    archive_path: Path | None = None
    if package_zips and not violations:
        archive_path = package_bundle_zip(bundle_dir)

    return BuildBundleResult(
        app_id=app_id,
        brand_id=brand.brand_id,
        modality_id=modality.modality_id,
        out_dir=bundle_dir,
        active_agents=["root_agent", *sorted(surviving_specialists.keys())],
        pruned_agents=sorted(pruned_agents_set),
        active_tools=sorted(surviving_tools.keys()),
        pruned_tools=sorted(pruned_tools_set),
        active_guardrails=sorted(surviving_guardrails.keys()),
        pruned_guardrails=sorted(pruned_guardrails_set),
        runtime_rules_count=runtime_rules_count,
        violations=violations,
        lockfile_path=lock_path,
        zip_path=archive_path,
    )


def compile_blueprint(
    blueprint_dir: Path | str,
    out_dir: Path | str,
    *,
    brand: str | None = None,
    brand_filter: str | None = None,
    modality: str | None = None,
    modality_filter: str | None = None,
    write_locks: bool = False,
    package_zips: bool = False,
    fail_on_firewall: bool = True,
) -> list[BuildBundleResult]:
    """Compiles `(brand, modality)` bundles from `blueprint_dir`."""
    bp_dir = Path(blueprint_dir).resolve()
    out_root = Path(out_dir).resolve()

    if not bp_dir.is_dir():
        raise BlueprintCompilerError(
            f"Blueprint directory does not exist: {bp_dir}"
        )

    blueprint: BlueprintSpec = load_spec(
        BlueprintSpec, bp_dir / "blueprint.yaml"
    )
    runtime_policy: RuntimePolicySpec = load_spec(
        RuntimePolicySpec, bp_dir / "runtime_policy.yaml"
    )

    overlay_path = bp_dir / "tuning_overlay.yaml"
    tuning_overlay: TuningOverlaySpec = (
        load_spec(TuningOverlaySpec, overlay_path)
        if overlay_path.is_file()
        else TuningOverlaySpec()
    )

    brands_dir = bp_dir / "brands"
    if not brands_dir.is_dir():
        raise BlueprintCompilerError(f"Missing brands directory: {brands_dir}")
    brands: dict[str, BrandSpec] = {}
    for brand_path in sorted(brands_dir.glob("*.yaml")):
        b_spec: BrandSpec = load_spec(BrandSpec, brand_path)
        if b_spec.brand_id != brand_path.stem:
            raise BlueprintCompilerError(
                f"{brand_path}: brand_id {b_spec.brand_id!r} does not match "
                f"filename stem {brand_path.stem!r}"
            )
        brands[b_spec.brand_id] = b_spec
    if not brands:
        raise BlueprintCompilerError(
            f"No brand manifests found in {brands_dir}"
        )

    modalities_dir = bp_dir / "modalities"
    if not modalities_dir.is_dir():
        raise BlueprintCompilerError(
            f"Missing modalities directory: {modalities_dir}"
        )
    modalities: dict[str, ModalitySpec] = {}
    for mod_path in sorted(modalities_dir.glob("*.yaml")):
        m_spec: ModalitySpec = load_spec(ModalitySpec, mod_path)
        if m_spec.modality_id != mod_path.stem:
            raise BlueprintCompilerError(
                f"{mod_path}: modality_id {m_spec.modality_id!r} does not "
                f"match filename stem {mod_path.stem!r}"
            )
        modalities[m_spec.modality_id] = m_spec
    if not modalities:
        raise BlueprintCompilerError(
            f"No modality manifests found in {modalities_dir}"
        )

    valid_overlay_agents = {"root_agent"} | set(blueprint.specialists.keys())
    for mod_key, agent_map in sorted(tuning_overlay.overlays.items()):
        if mod_key not in modalities:
            raise BlueprintCompilerError(
                f"tuning_overlay.yaml references unknown modality {mod_key!r}. "
                f"Available: {sorted(modalities)}"
            )
        for agent_key in sorted(agent_map.keys()):
            if agent_key not in valid_overlay_agents:
                raise BlueprintCompilerError(
                    f"tuning_overlay.yaml modality {mod_key!r} references "
                    f"unknown agent {agent_key!r}. "
                    f"Available: {sorted(valid_overlay_agents)}"
                )

    selected_brand = brand if brand is not None else brand_filter
    selected_modality = modality if modality is not None else modality_filter

    if selected_brand is not None and selected_brand not in brands:
        raise BlueprintCompilerError(
            f"Unknown brand {selected_brand!r}. Available: {sorted(brands)}"
        )
    if selected_modality is not None and selected_modality not in modalities:
        raise BlueprintCompilerError(
            f"Unknown modality {selected_modality!r}. "
            f"Available: {sorted(modalities)}"
        )

    target_brands = (
        [brands[selected_brand]]
        if selected_brand is not None
        else [brands[k] for k in sorted(brands)]
    )
    target_modalities = (
        [modalities[selected_modality]]
        if selected_modality is not None
        else [modalities[k] for k in sorted(modalities)]
    )

    out_root.mkdir(parents=True, exist_ok=True)
    results: list[BuildBundleResult] = []
    for b_spec in target_brands:
        for m_spec in target_modalities:
            results.append(
                _compile_single_bundle(
                    blueprint_dir=bp_dir,
                    out_root=out_root,
                    blueprint=blueprint,
                    brand=b_spec,
                    modality=m_spec,
                    runtime_policy=runtime_policy,
                    tuning_overlay=tuning_overlay,
                    write_locks=write_locks,
                    package_zips=package_zips,
                    fail_on_firewall=fail_on_firewall,
                )
            )
    return results


def format_build_table(results: Sequence[BuildBundleResult]) -> str:
    """Formats a human-readable summary table for compiled bundles."""
    total_violations = sum(len(r.violations) for r in results)
    header = (
        f"{'APP_ID':<20} {'ACTIVE (A/T/G)':<16} "
        f"{'RUNTIME_RULES':<17} {'FIREWALL':<24} PRUNED_ITEMS"
    )
    lines = [header, "-" * 112]
    for res in results:
        v_cnt = len(res.violations)
        fw_str = (
            f"PASS ({v_cnt} violations)"
            if v_cnt == 0
            else f"FAIL ({v_cnt} violations)"
        )
        active_str = (
            f"{len(res.active_agents)}/"
            f"{len(res.active_tools)}/"
            f"{len(res.active_guardrails)}"
        )
        pruned_str = (
            f"agents={res.pruned_agents}, "
            f"tools={res.pruned_tools}, "
            f"guardrails={res.pruned_guardrails}"
        )
        lines.append(
            f"{res.app_id:<20} {active_str:<16} "
            f"runtime_rules={res.runtime_rules_count:<3} "
            f"firewall={fw_str:<15} {pruned_str}"
        )
    lines.append(
        f"BUILD SUMMARY: {len(results)} apps compiled, "
        f"{total_violations} violations"
    )
    return "\n".join(lines)


__all__ = [
    "BuildBundleResult",
    "apply_runtime_policy_to_bundle",
    "apply_tuning_overlay_to_bundle",
    "compile_blueprint",
    "format_build_table",
    "package_bundle_zip",
]
