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

"""Deterministic Build Compiler for CXAS."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field

from cxas_scrapi.build.drift import (
    _OPENAPI_SERVER_URL_RE,
    CES_ENV_VAR_PLACEHOLDER,
    write_lockfile,
)
from cxas_scrapi.build.manifests import (
    CHANNEL_PROFILE_TYPES,
    BrandOverrides,
    InstructionOverride,
    LineReplacement,
    Manifests,
    ModelPins,
    load_manifests,
)
from cxas_scrapi.core.packaging import package_app_archive

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


class BuildError(Exception):
    pass


class BuildReport(BaseModel):
    brand_id: str
    channel: str
    environment: str
    out_dir: Path
    zip_path: Path | None = None
    lockfile_path: Path | None = None
    warnings: list[str] = Field(default_factory=list)


def _set_model(holder: dict[str, Any], model: str) -> None:
    settings = holder.get("modelSettings")
    if not isinstance(settings, dict):
        settings = {}
    settings["model"] = model
    holder["modelSettings"] = settings


def _apply_model_pins(out_dir: Path, pins: ModelPins | None) -> None:
    """Pins only the model slots CES applies (agent, guardrail, data store)."""
    if pins is None:
        return

    def _rewrite(path: Path, fn: Any) -> None:
        data = json.loads(path.read_text("utf-8"))
        if fn(data):
            path.write_text(json.dumps(data, indent=2) + "\n", "utf-8")

    if pins.agent:
        app_json = out_dir / "app.json"
        if app_json.is_file():
            _rewrite(app_json, lambda d: _set_model(d, pins.agent) or True)
        for agent_json in sorted(out_dir.glob("agents/*/*.json")):
            if agent_json.stem == agent_json.parent.name:
                _rewrite(
                    agent_json, lambda d: _set_model(d, pins.agent) or True
                )

    if pins.guardrail:

        def _pin_guardrail(d: dict[str, Any]) -> bool:
            changed = False
            if isinstance(d.get("llmPolicy"), dict):
                _set_model(d["llmPolicy"], pins.guardrail)
                changed = True
            sec = d.get("llmPromptSecurity")
            if isinstance(sec, dict) and isinstance(
                sec.get("customPolicy"), dict
            ):
                _set_model(sec["customPolicy"], pins.guardrail)
                changed = True
            return changed

        for gr_json in sorted(out_dir.glob("guardrails/*/*.json")):
            _rewrite(gr_json, _pin_guardrail)

    if pins.data_store:

        def _pin_data_store(d: dict[str, Any]) -> bool:
            ds = d.get("dataStoreTool")
            if not isinstance(ds, dict):
                return False
            for cfg in ds.get("modalityConfigs") or []:
                for key in ("rewriterConfig", "summarizationConfig"):
                    if isinstance(cfg.get(key), dict):
                        _set_model(cfg[key], pins.data_store)
            return True

        for tool_json in sorted(out_dir.glob("tools/*/*.json")):
            if tool_json.stem == tool_json.parent.name:
                _rewrite(tool_json, _pin_data_store)


def _apply_lock_policy(out_dir: Path, locked: bool | None) -> None:
    """Declares the governed lock in app.json (applied by `cxas push`)."""
    app_json = out_dir / "app.json"
    if locked is None or not app_json.is_file():
        return
    data = json.loads(app_json.read_text("utf-8"))
    data["locked"] = bool(locked)
    app_json.write_text(json.dumps(data, indent=2) + "\n", "utf-8")


def deep_merge(
    target: dict[str, Any], overrides: dict[str, Any]
) -> dict[str, Any]:
    for k, v in overrides.items():
        if isinstance(v, dict) and k in target and isinstance(target[k], dict):
            target[k] = deep_merge(dict(target[k]), v)
        else:
            target[k] = v
    return target


def validate_modalities(content: str, path: Path) -> None:
    lines = content.split("\n")
    stack: list[str] = []

    # regex for modality tags anywhere in a line
    modality_re = re.compile(r"({@startModality_[^}]+}|{@endModality_[^}]+})")

    for i, line in enumerate(lines):
        matches = modality_re.findall(line)
        if not matches:
            continue

        # Guard: tag must be standalone on the line
        for match in matches:
            if match != line.strip():
                raise BuildError(
                    f"{path}:{i + 1}: Modality tag {match} must be on its "
                    "own line (found inline)."
                )

            if match.startswith("{@startModality_"):
                modality = match[16:-1]
                stack.append(modality)
            elif match.startswith("{@endModality_"):
                modality = match[14:-1]
                if not stack or stack[-1] != modality:
                    raise BuildError(
                        f"{path}:{i + 1}: Unbalanced or mismatched "
                        f"modality tag {match}."
                    )
                stack.pop()

    if stack:
        raise BuildError(f"{path}: Unbalanced modality tags remain: {stack}")


def _apply_line_replacements_strict(
    current_text: str,
    replacements: Sequence[LineReplacement],
    *,
    render_fn: Callable[[str], str],
    context_label: str,
) -> str:
    if not replacements:
        return current_text

    norm = current_text.replace("\r\n", "\n")
    trailing_nl = norm.endswith("\n") or norm == ""
    body = norm.rstrip("\n")
    lines = body.split("\n") if body else []

    for rep in replacements:
        m_rendered = render_fn(rep.match.replace("\r\n", "\n")).rstrip("\n")
        w_rendered = render_fn(rep.replace_with.replace("\r\n", "\n"))
        if not m_rendered:
            raise BuildError(
                f"{context_label}: empty replace_lines match pattern."
            )
        m_lines = m_rendered.split("\n")
        w_lines = (
            [] if w_rendered == "" else w_rendered.rstrip("\n").split("\n")
        )
        span = len(m_lines)

        exact_hits = [
            idx
            for idx in range(len(lines) - span + 1)
            if lines[idx : idx + span] == m_lines
        ]
        if len(exact_hits) == 1:
            hit = exact_hits[0]
            lines = [*lines[:hit], *w_lines, *lines[hit + span :]]
            continue
        if len(exact_hits) > 1:
            raise BuildError(
                f"{context_label}: ambiguous replace_lines match "
                f"({len(exact_hits)} occurrences): {m_rendered!r}"
            )

        joined = "\n".join(lines)
        sub_count = joined.count(m_rendered)
        if sub_count == 0:
            raise BuildError(
                f"{context_label}: replace_lines match not found: "
                f"{m_rendered!r}"
            )
        if sub_count > 1:
            raise BuildError(
                f"{context_label}: ambiguous replace_lines substring match "
                f"({sub_count} occurrences): {m_rendered!r}"
            )
        # Unique-substring match (single- or multi-line): replace only the
        # matched span and keep the surrounding text on the affected lines.
        replaced_joined = joined.replace(m_rendered, w_rendered.rstrip("\n"), 1)
        lines = replaced_joined.split("\n") if replaced_joined else []

    rebuilt = "\n".join(lines)
    if trailing_nl and rebuilt:
        return rebuilt + "\n"
    return rebuilt


def _apply_append_rules_lines(
    current_text: str,
    rules: Sequence[str],
    *,
    render_fn: Callable[[str], str],
) -> str:
    if not rules:
        return current_text
    rendered_rules = [
        render_fn(r.replace("\r\n", "\n")).rstrip("\n") for r in rules
    ]
    rendered_rules = [r for r in rendered_rules if r]
    if not rendered_rules:
        return current_text
    base = current_text.replace("\r\n", "\n").rstrip("\n")
    joined = (
        (base + "\n" + "\n".join(rendered_rules))
        if base
        else "\n".join(rendered_rules)
    )
    return joined + "\n"


def _apply_instruction_override(
    current_text: str,
    brand_ov: InstructionOverride | None,
    ch_ov: InstructionOverride | None,
    *,
    render_fn: Callable[[str], str],
    context_label: str,
    identity_prefix: str | None = None,
) -> str:
    if ch_ov is not None and ch_ov.instruction is not None:
        rendered_ch = render_fn(ch_ov.instruction.replace("\r\n", "\n"))
        if identity_prefix and not rendered_ch.startswith(identity_prefix):
            text = identity_prefix + "\n\n" + rendered_ch.lstrip("\n")
        else:
            text = rendered_ch
        if text and not text.endswith("\n"):
            text += "\n"
        text = _apply_line_replacements_strict(
            text,
            ch_ov.replace_lines,
            render_fn=render_fn,
            context_label=context_label,
        )
        return _apply_append_rules_lines(
            text, ch_ov.append_rules, render_fn=render_fn
        )

    if brand_ov is not None and brand_ov.instruction is not None:
        rendered_br = render_fn(brand_ov.instruction.replace("\r\n", "\n"))
        if identity_prefix and not rendered_br.startswith(identity_prefix):
            text = identity_prefix + "\n\n" + rendered_br.lstrip("\n")
        else:
            text = rendered_br
        if text and not text.endswith("\n"):
            text += "\n"
    else:
        text = current_text

    if brand_ov is not None:
        text = _apply_line_replacements_strict(
            text,
            brand_ov.replace_lines,
            render_fn=render_fn,
            context_label=context_label,
        )
        text = _apply_append_rules_lines(
            text, brand_ov.append_rules, render_fn=render_fn
        )

    if ch_ov is not None:
        text = _apply_line_replacements_strict(
            text,
            ch_ov.replace_lines,
            render_fn=render_fn,
            context_label=context_label,
        )
        text = _apply_append_rules_lines(
            text, ch_ov.append_rules, render_fn=render_fn
        )

    return text


def _apply_brand_overrides(
    *,
    core_dir: Path,
    out_dir: Path,
    brand_id: str,
    channel: str,
    permitted_channels: set[str],
    ov: BrandOverrides,
    render_fn: Callable[[str], str],
    global_identity_prefix: str | None,
) -> None:
    if ov.global_instruction is not None:
        unknown_g_ch = sorted(
            set(ov.global_instruction.channels) - permitted_channels
        )
        if unknown_g_ch:
            raise BuildError(
                f"Brand '{brand_id}' global_instruction override references "
                f"channel(s) {unknown_g_ch} not declared in "
                f"channel_policy.yaml ({sorted(permitted_channels)})."
            )

    for agent_id, ag_ov in sorted(ov.agents.items()):
        if not (core_dir / "agents" / agent_id).is_dir():
            raise BuildError(
                f"Brand '{brand_id}' agent override references unknown agent "
                f"'{agent_id}' (missing from core/agents/)."
            )
        unknown_a_ch = sorted(set(ag_ov.channels) - permitted_channels)
        if unknown_a_ch:
            raise BuildError(
                f"Brand '{brand_id}' agent '{agent_id}' override references "
                f"channel(s) {unknown_a_ch} not declared in "
                f"channel_policy.yaml ({sorted(permitted_channels)})."
            )
        if not (out_dir / "agents" / agent_id).is_dir() and (
            channel in ag_ov.channels
        ):
            raise BuildError(
                f"Brand '{brand_id}' channel '{channel}' override targets "
                f"disabled agent '{agent_id}'."
            )

    for tool_id, t_ov in sorted(ov.tools.items()):
        if not (core_dir / "tools" / tool_id).is_dir():
            raise BuildError(
                f"Brand '{brand_id}' tool override references unknown tool "
                f"'{tool_id}' (missing from core/tools/)."
            )
        unknown_t_ch = sorted(set(t_ov.channels) - permitted_channels)
        if unknown_t_ch:
            raise BuildError(
                f"Brand '{brand_id}' tool '{tool_id}' override references "
                f"channel(s) {unknown_t_ch} not declared in "
                f"channel_policy.yaml ({sorted(permitted_channels)})."
            )
        if not (out_dir / "tools" / tool_id).is_dir() and (
            channel in t_ov.channels
        ):
            raise BuildError(
                f"Brand '{brand_id}' channel '{channel}' override targets "
                f"disabled tool '{tool_id}'."
            )

    if ov.global_instruction is not None:
        gi_path = out_dir / "global_instruction.txt"
        cur_gi = gi_path.read_text("utf-8") if gi_path.is_file() else ""
        g_ch_ov = ov.global_instruction.channels.get(channel)
        new_gi = _apply_instruction_override(
            cur_gi,
            ov.global_instruction,
            g_ch_ov,
            render_fn=render_fn,
            context_label=f"{brand_id}/{channel} global_instruction.txt",
            identity_prefix=global_identity_prefix,
        )
        if (
            new_gi
            and not cur_gi
            and global_identity_prefix
            and not new_gi.startswith(global_identity_prefix)
        ):
            # Mirror how a core global_instruction.txt is compiled: the brand
            # identity block always prefixes the global instruction.
            new_gi = global_identity_prefix + "\n\n" + new_gi.lstrip("\n")
        if new_gi != cur_gi:
            gi_path.write_text(new_gi, "utf-8")
            validate_modalities(new_gi, gi_path.relative_to(out_dir))

    for agent_id, ag_ov in sorted(ov.agents.items()):
        ag_dir = out_dir / "agents" / agent_id
        if not ag_dir.is_dir():
            continue
        a_ch_ov = ag_ov.channels.get(channel)
        ag_json_path = ag_dir / f"{agent_id}.json"
        if ag_json_path.is_file():
            ag_data = json.loads(ag_json_path.read_text("utf-8"))
            ag_mod = False
            eff_desc = (
                a_ch_ov.description
                if a_ch_ov is not None and a_ch_ov.description is not None
                else ag_ov.description
            )
            if eff_desc is not None:
                ag_data["description"] = render_fn(eff_desc)
                ag_mod = True
            eff_temp = (
                a_ch_ov.temperature
                if a_ch_ov is not None and a_ch_ov.temperature is not None
                else ag_ov.temperature
            )
            if eff_temp is not None:
                ms = ag_data.get("modelSettings")
                if not isinstance(ms, dict):
                    ms = {}
                ms["temperature"] = float(eff_temp)
                ag_data["modelSettings"] = ms
                ag_mod = True
            eff_model = (
                a_ch_ov.model
                if a_ch_ov is not None and a_ch_ov.model is not None
                else ag_ov.model
            )
            if eff_model is not None:
                _set_model(ag_data, render_fn(eff_model))
                ag_mod = True
            if ag_mod:
                ag_json_path.write_text(
                    json.dumps(ag_data, indent=2) + "\n", "utf-8"
                )

        instr_path = ag_dir / "instruction.txt"
        has_instr_ov = (
            ag_ov.instruction is not None
            or bool(ag_ov.replace_lines)
            or bool(ag_ov.append_rules)
            or (
                a_ch_ov is not None
                and (
                    a_ch_ov.instruction is not None
                    or bool(a_ch_ov.replace_lines)
                    or bool(a_ch_ov.append_rules)
                )
            )
        )
        if has_instr_ov:
            cur_in = (
                instr_path.read_text("utf-8") if instr_path.is_file() else ""
            )
            new_in = _apply_instruction_override(
                cur_in,
                ag_ov,
                a_ch_ov,
                render_fn=render_fn,
                context_label=(
                    f"{brand_id}/{channel} agents/{agent_id}/instruction.txt"
                ),
            )
            instr_path.write_text(new_in, "utf-8")
            validate_modalities(new_in, instr_path.relative_to(out_dir))

    for tool_id, t_ov in sorted(ov.tools.items()):
        t_dir = out_dir / "tools" / tool_id
        if not t_dir.is_dir():
            continue
        t_ch_ov = t_ov.channels.get(channel)
        t_json_path = t_dir / f"{tool_id}.json"
        if not t_json_path.is_file():
            continue
        t_data = json.loads(t_json_path.read_text("utf-8"))
        t_mod = False

        eff_t_desc = (
            t_ch_ov.description
            if t_ch_ov is not None and t_ch_ov.description is not None
            else t_ov.description
        )
        if eff_t_desc is not None:
            r_desc = render_fn(eff_t_desc)
            updated_sub = False
            for sub_k in ("openApiTool", "dataStoreTool", "pythonFunction"):
                if isinstance(t_data.get(sub_k), dict):
                    t_data[sub_k]["description"] = r_desc
                    updated_sub = True
                    break
            if not updated_sub:
                t_data["description"] = r_desc
            t_mod = True

        eff_t_url = (
            t_ch_ov.url
            if t_ch_ov is not None and t_ch_ov.url is not None
            else t_ov.url
        )
        if eff_t_url is not None:
            if not isinstance(t_data.get("openApiTool"), dict):
                raise BuildError(
                    f"Brand '{brand_id}' tool '{tool_id}' override sets 'url' "
                    "but the tool is not an openApiTool; 'url' overrides are "
                    "only supported for OpenAPI tools."
                )
            r_url = render_fn(eff_t_url)
            display_name = str(t_data.get("displayName") or tool_id)
            env_path = out_dir / "environment.json"
            env_updated = False
            if env_path.is_file():
                env_data = json.loads(env_path.read_text("utf-8"))
                env_tools = env_data.get("tools")
                if isinstance(env_tools, dict):
                    for k_cand in dict.fromkeys((display_name, tool_id)):
                        entry = env_tools.get(k_cand)
                        if isinstance(entry, dict) and isinstance(
                            entry.get("openApiTool"), dict
                        ):
                            entry["openApiTool"]["url"] = r_url
                            env_updated = True
                    if env_updated:
                        env_path.write_text(
                            json.dumps(env_data, indent=2) + "\n", "utf-8"
                        )

            oa_cfg = t_data.get("openApiTool")
            if isinstance(oa_cfg, dict) and (
                "url" in oa_cfg or not env_updated
            ):
                oa_cfg["url"] = r_url
                t_mod = True

            oa_yaml_path = t_dir / "open_api_tool" / "open_api_schema.yaml"
            if oa_yaml_path.is_file():
                oa_raw = oa_yaml_path.read_text("utf-8")
                try:
                    oa_doc = yaml.safe_load(oa_raw)
                except yaml.YAMLError as exc:
                    raise BuildError(
                        f"Brand '{brand_id}' tool '{tool_id}': invalid OpenAPI "
                        f"schema YAML in {oa_yaml_path.relative_to(out_dir)}: "
                        f"{exc}"
                    ) from exc
                oa_paths = (
                    oa_doc.get("paths") if isinstance(oa_doc, dict) else None
                )
                parsed_u = urlsplit(r_url)
                srv_val = (
                    f"{parsed_u.scheme}://{parsed_u.netloc}"
                    if (
                        parsed_u.scheme
                        and parsed_u.netloc
                        and isinstance(oa_paths, dict)
                        and parsed_u.path in oa_paths
                    )
                    else r_url
                )
                new_oa_lines: list[str] = []
                oa_changed = False
                for line in oa_raw.splitlines():
                    m_srv = _OPENAPI_SERVER_URL_RE.match(line)
                    if m_srv:
                        cur_srv = line[len(m_srv.group(1)) :].strip()
                        if cur_srv != CES_ENV_VAR_PLACEHOLDER:
                            new_line = f"{m_srv.group(1)}{srv_val}"
                            if new_line != line:
                                oa_changed = True
                            new_oa_lines.append(new_line)
                            continue
                    new_oa_lines.append(line)
                if oa_changed:
                    oa_yaml_path.write_text(
                        "\n".join(new_oa_lines)
                        + ("\n" if oa_raw.endswith("\n") else ""),
                        "utf-8",
                    )

        if t_mod:
            t_json_path.write_text(json.dumps(t_data, indent=2) + "\n", "utf-8")


def _pruned_reference_warnings(
    out_dir: Path, disabled_agents: set[str], disabled_tools: set[str]
) -> list[str]:
    """Lists `{@AGENT: x}` / `{@TOOL: x}` mentions of pruned resources."""
    if not disabled_agents and not disabled_tools:
        return []
    found: list[str] = []
    for path in sorted(out_dir.rglob("*.txt")):
        if not path.is_file():
            continue
        text = path.read_text("utf-8")
        rel = path.relative_to(out_dir).as_posix()
        for a in sorted(disabled_agents):
            if f"{{@AGENT: {a}}}" in text:
                found.append(f"{rel} mentions pruned agent {a}")
        for t in sorted(disabled_tools):
            if f"{{@TOOL: {t}}}" in text:
                found.append(f"{rel} mentions pruned tool {t}")
    return found


def _build_identity_block(
    manifests: Manifests, brand_id: str, channel: str
) -> str:
    brand = manifests.brand(brand_id)
    policy = manifests.policy(brand_id)

    lines = [f"# Brand: {brand.display_name}"]
    if brand.assistant_title:
        lines.append(f"Title: {brand.assistant_title}")
    if brand.legal_entity:
        lines.append(f"Legal Entity: {brand.legal_entity}")
    if brand.tone_guidelines:
        lines.append(f"Tone: {brand.tone_guidelines}")
    if brand.lexicon:
        lines.append("Lexicon:")
        for k, v in sorted(brand.lexicon.items()):
            lines.append(f"  - {k}: {v}")
    if brand.urls:
        lines.append("URLs:")
        for k, v in sorted(brand.urls.items()):
            lines.append(f"  - {k}: {v}")

    # Locales content blocks
    if manifests.content_blocks:
        countries = policy.supported_countries
        languages = brand.languages
        for country in sorted(countries):
            for lang in sorted(languages):
                blocks = (
                    manifests.content_blocks.blocks.get(brand_id, {})
                    .get(country, {})
                    .get(lang, {})
                )
                for bk, bv in sorted(blocks.items()):
                    lines.append(f"[{country}-{lang}] {bk}: {bv}")

    return "\n".join(lines)


def build_app(
    core_dir: str | Path,
    manifests_dir: str | Path,
    brand_id: str,
    channel: str,
    environment: str,
    out_dir: str | Path,
    *,
    package_zip: bool = False,
    native_python_env: bool = False,
    strip_bridge: str | None = None,
    write_lock: bool = False,
) -> BuildReport:
    core_dir = Path(core_dir).resolve()
    out_dir = Path(out_dir).resolve()

    manifests = load_manifests(manifests_dir)
    brand = manifests.brand(brand_id)
    policy = manifests.policy(brand_id)
    env_manifest = manifests.env(environment)

    if channel not in policy.channels:
        raise BuildError(
            f"Channel {channel} not permitted for brand {brand_id}."
        )

    channel_caps = policy.channels[channel]

    # 1. Clean staging
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(
        core_dir,
        out_dir,
        ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", ".DS_Store", "manifests"
        ),
    )

    warnings: list[str] = []

    # Read pruned names to track displayNames
    pruned_agent_display_names = set()
    pruned_tool_display_names = set()

    disabled_agents = set(channel_caps.disabled_agents)
    disabled_tools = set(channel_caps.disabled_tools)

    agents_dir = out_dir / "agents"
    tools_dir = out_dir / "tools"

    if agents_dir.exists():
        for agent_path in list(agents_dir.iterdir()):
            if agent_path.is_dir():
                a_json = agent_path / f"{agent_path.name}.json"
                display_name = ""
                if a_json.exists():
                    try:
                        d = json.loads(a_json.read_text("utf-8"))
                        display_name = d.get("displayName", "")
                    except (ValueError, OSError):
                        pass
                if (
                    agent_path.name in disabled_agents
                    or display_name in disabled_agents
                ):
                    if display_name:
                        pruned_agent_display_names.add(display_name)
                    pruned_agent_display_names.add(agent_path.name)
                    shutil.rmtree(agent_path)

    if tools_dir.exists():
        for tool_path in list(tools_dir.iterdir()):
            if tool_path.is_dir():
                t_json = tool_path / f"{tool_path.name}.json"
                display_name = ""
                if t_json.exists():
                    try:
                        d = json.loads(t_json.read_text("utf-8"))
                        display_name = d.get("displayName", "")
                    except (ValueError, OSError):
                        pass
                if (
                    tool_path.name in disabled_tools
                    or display_name in disabled_tools
                ):
                    if display_name:
                        pruned_tool_display_names.add(display_name)
                    pruned_tool_display_names.add(tool_path.name)
                    shutil.rmtree(tool_path)

    # Substitutions mapping
    replacements = {
        "@@brand.brand_id@@": brand.brand_id,
        "@@brand.display_name@@": brand.display_name,
        "@@brand.assistant_title@@": brand.assistant_title or "",
        "@@brand.legal_entity@@": brand.legal_entity or "",
        "@@brand.tone_guidelines@@": brand.tone_guidelines or "",
        "@@build.channel@@": channel,
        "@@build.environment@@": environment,
    }

    for k, v in brand.lexicon.items():
        replacements[f"@@brand.lexicon.{k}@@"] = v
    for k, v in brand.urls.items():
        replacements[f"@@brand.urls.{k}@@"] = v

    for k, v in policy.api_routing.items():
        replacements[f"@@routing.{k}@@"] = v

    for k, v in env_manifest.templates.items():
        v_rendered = v.format(
            brand=brand.brand_id,
            brand_slug=brand.brand_id.replace("_", "-"),
            env=environment,
            channel=channel,
            service="{service}",
        )
        replacements[f"@@env.{k}@@"] = v_rendered.replace("{service}", "")

    cond_inline_re = re.compile(
        r"\{@start(Channel|Brand|AgentEnabled|ToolEnabled)_([^}]+)\}"
        r"([^\n]*?)"
        r"\{@end\1_\2\}"
    )
    cond_start_re = re.compile(
        r"^\{@start(Channel|Brand|AgentEnabled|ToolEnabled)_([^}]+)\}$"
    )
    cond_end_re = re.compile(
        r"^\{@end(Channel|Brand|AgentEnabled|ToolEnabled)_([^}]+)\}$"
    )
    cond_any_re = re.compile(
        r"\{@(?:start|end)(?:Channel|Brand|AgentEnabled|ToolEnabled)_[^}]+\}"
    )

    def _eval_cond(kind: str, arg: str) -> bool:
        if kind == "Channel":
            return arg == channel
        if kind == "Brand":
            return arg == brand_id
        if kind == "AgentEnabled":
            return (
                arg not in disabled_agents
                and arg not in pruned_agent_display_names
            )
        if kind == "ToolEnabled":
            return (
                arg not in disabled_tools
                and arg not in pruned_tool_display_names
            )
        raise BuildError(f"Unknown conditional tag kind: {kind}")

    def process_conditional_blocks(content: str) -> str:
        if "{@start" not in content and "{@end" not in content:
            return content

        lines = content.split("\n")
        out_lines: list[str] = []
        stack: list[tuple[str, str, bool]] = []

        for i, line in enumerate(lines):
            stripped = line.strip()
            m_start = cond_start_re.match(stripped)
            if m_start:
                kind, arg = m_start.group(1), m_start.group(2)
                stack.append((kind, arg, _eval_cond(kind, arg)))
                continue

            m_end = cond_end_re.match(stripped)
            if m_end:
                kind, arg = m_end.group(1), m_end.group(2)
                if not stack or stack[-1][0] != kind or stack[-1][1] != arg:
                    raise BuildError(
                        f"Line {i + 1}: Unbalanced or mismatched conditional "
                        f"tag {stripped}."
                    )
                stack.pop()
                continue

            if all(item[2] for item in stack):
                cur_line = line
                while True:
                    cur_line, count = cond_inline_re.subn(
                        lambda m: (
                            m.group(3)
                            if _eval_cond(m.group(1), m.group(2))
                            else ""
                        ),
                        cur_line,
                    )
                    if count == 0:
                        break
                leftover = cond_any_re.findall(cur_line)
                if leftover:
                    raise BuildError(
                        f"Line {i + 1}: Unbalanced inline conditional "
                        f"tag(s): {leftover}"
                    )
                out_lines.append(cur_line)

        if stack:
            unclosed = [f"{{@start{k}_{a}}}" for k, a, _ in stack]
            raise BuildError(f"Unbalanced conditional tags remain: {unclosed}")

        return "\n".join(out_lines)

    def process_string(s: str) -> str:
        s = process_conditional_blocks(s)
        for ek, ev in env_manifest.templates.items():
            base_rendered = ev.format(
                brand=brand.brand_id,
                brand_slug=brand.brand_id.replace("_", "-"),
                env=environment,
                channel=channel,
                service="{service}",
            )
            pattern = rf"@@env\.{re.escape(ek)}:([^@]+)@@"

            def _repl(m: re.Match[str], rendered: str = base_rendered) -> str:
                return rendered.replace("{service}", m.group(1))

            s = re.sub(pattern, _repl, s)
            s = s.replace(
                f"@@env.{ek}@@", base_rendered.replace("{service}", "")
            )

        for rep_k, rep_v in replacements.items():
            if rep_k in s:
                s = s.replace(rep_k, rep_v)

        unresolved = re.findall(r"@@[^@]+@@", s)
        if unresolved:
            raise BuildError(
                f"Unresolved placeholders remain in string: {unresolved}"
            )
        return s

    def process_json(val: Any) -> Any:
        if isinstance(val, dict):
            return {k: process_json(v) for k, v in val.items()}
        elif isinstance(val, list):
            return [process_json(item) for item in val]
        elif isinstance(val, str):
            return process_string(val)
        return val

    global_identity_prefix: str | None = None

    # Pruning from surviving agent/tool jsons and processing strings
    for root, dirs, files in os.walk(out_dir):
        dirs.sort()
        for file in sorted(files):
            path = Path(root) / file

            if file.endswith(".json"):
                try:
                    data = json.loads(path.read_text("utf-8"))
                except (ValueError, OSError):
                    continue

                modified = False

                if "childAgents" in data:
                    new_children = []
                    for c in data["childAgents"]:
                        c_name = (
                            c.get("displayName") if isinstance(c, dict) else c
                        )
                        if (
                            c_name not in disabled_agents
                            and c_name not in pruned_agent_display_names
                        ):
                            new_children.append(c)
                        else:
                            modified = True
                    data["childAgents"] = new_children

                if isinstance(data.get("tools"), list):
                    new_tools = []
                    for t in data["tools"]:
                        t_name = (
                            t.get("displayName") if isinstance(t, dict) else t
                        )
                        if (
                            t_name not in disabled_tools
                            and t_name not in pruned_tool_display_names
                        ):
                            new_tools.append(t)
                        else:
                            modified = True
                    data["tools"] = new_tools
                elif isinstance(data.get("tools"), dict):
                    new_tools_dict = {}
                    for t_key, t_val in data["tools"].items():
                        if (
                            t_key not in disabled_tools
                            and t_key not in pruned_tool_display_names
                        ):
                            new_tools_dict[t_key] = t_val
                        else:
                            modified = True
                    data["tools"] = new_tools_dict

                if file == "app.json":
                    # Do NOT unconditionally overwrite app.json["rootAgent"]
                    # Set only if missing or in disabled_agents
                    current_root = data.get("rootAgent")
                    if channel_caps.entry_agent and (
                        not current_root or current_root in disabled_agents
                    ):
                        data["rootAgent"] = channel_caps.entry_agent
                        modified = True
                    elif (
                        current_root in disabled_agents
                        and not channel_caps.entry_agent
                    ):
                        del data["rootAgent"]
                        modified = True

                    if brand.tts_voices:
                        if "audioProcessingConfig" not in data:
                            data["audioProcessingConfig"] = {}
                        synth_configs = {}
                        for lang, tts in brand.tts_voices.items():
                            synth_configs[lang] = {
                                "voice": tts.voice,
                                "speakingRate": tts.speaking_rate,
                            }
                        data["audioProcessingConfig"][
                            "synthesizeSpeechConfigs"
                        ] = synth_configs
                        modified = True

                    if brand.languages:
                        if "languageSettings" not in data:
                            data["languageSettings"] = {}
                        data["languageSettings"]["defaultLanguageCode"] = (
                            brand.languages[0]
                        )
                        data["languageSettings"]["supportedLanguageCodes"] = (
                            brand.languages
                        )
                        if len(brand.languages) > 1:
                            data["languageSettings"][
                                "enableMultilingualSupport"
                            ] = True
                        modified = True

                if file == "environment.json" and env_manifest.overrides:
                    data = deep_merge(data, env_manifest.overrides)
                    modified = True

                try:
                    data = process_json(data)
                    modified = True
                except BuildError as e:
                    raise BuildError(f"{path}: {e}") from e

                if modified:
                    path.write_text(json.dumps(data, indent=2) + "\n", "utf-8")

            elif file.endswith(".txt"):
                text = path.read_text("utf-8")

                if file == "global_instruction.txt":
                    identity_block = _build_identity_block(
                        manifests, brand_id, channel
                    )
                    if "@@brand.identity_block@@" in text:
                        text = text.replace(
                            "@@brand.identity_block@@", identity_block
                        )
                    else:
                        global_identity_prefix = process_string(identity_block)
                        text = identity_block + "\n\n" + text

                try:
                    text = process_conditional_blocks(text)
                except BuildError as e:
                    raise BuildError(f"{path}: {e}") from e

                try:
                    text = process_string(text)
                except BuildError as e:
                    raise BuildError(f"{path}: {e}") from e

                path.write_text(text, "utf-8")
                validate_modalities(text, path.relative_to(out_dir))

            elif file.endswith((".yaml", ".yml")):
                # OpenAPI schemas carry brand-specific server URLs and
                # descriptions; render the same tokens as JSON/text files.
                text = path.read_text("utf-8")
                if "@@" in text or "{@start" in text or "{@end" in text:
                    try:
                        text = process_conditional_blocks(text)
                        text = process_string(text)
                    except BuildError as e:
                        raise BuildError(f"{path}: {e}") from e
                    path.write_text(text, "utf-8")

            elif file.endswith(".py"):
                text = path.read_text("utf-8")
                if "@@" in text or "{@start" in text or "{@end" in text:
                    try:
                        text = process_conditional_blocks(text)
                        text = process_string(text)
                    except BuildError as e:
                        raise BuildError(f"{path}: {e}") from e
                    path.write_text(text, "utf-8")

    _apply_model_pins(out_dir, env_manifest.models)
    _apply_lock_policy(out_dir, env_manifest.locked)

    brand_ov = manifests.brand_overrides(brand_id)
    if brand_ov is not None:
        if (
            global_identity_prefix is None
            and not (core_dir / "global_instruction.txt").is_file()
        ):
            global_identity_prefix = process_string(
                _build_identity_block(manifests, brand_id, channel)
            )
        _apply_brand_overrides(
            core_dir=core_dir,
            out_dir=out_dir,
            brand_id=brand_id,
            channel=channel,
            permitted_channels=set(policy.channels.keys()),
            ov=brand_ov,
            render_fn=process_string,
            global_identity_prefix=global_identity_prefix,
        )

    # Pruned-reference warnings are computed once, deterministically, on the
    # final (post-override) compiled instructions.
    warnings.extend(
        _pruned_reference_warnings(
            out_dir,
            disabled_agents | pruned_agent_display_names,
            disabled_tools | pruned_tool_display_names,
        )
    )

    deployments_dir = out_dir / "deployments"
    deployments_dir.mkdir(exist_ok=True)
    slug = channel.lower().replace("_", "-")
    dep_path = deployments_dir / f"{slug}.yaml"

    dep_data = {
        "displayName": f"{brand.display_name} {channel}",
        "brandId": brand.brand_id,
        "channel": channel,
        "environment": environment,
        "channelProfile": {"channelType": CHANNEL_PROFILE_TYPES[channel]},
        "supportedCountries": policy.supported_countries,
        "supportedLanguages": brand.languages,
        "supportedModalities": channel_caps.supported_modalities,
        "deeplinkWidgets": channel_caps.deeplink_widgets,
    }
    if channel_caps.entry_agent:
        dep_data["entryAgent"] = channel_caps.entry_agent

    with open(dep_path, "w") as f:
        yaml.safe_dump(dep_data, f, sort_keys=False)

    zip_path = None
    if package_zip:
        zip_path = out_dir / f"{brand_id}_{slug}.zip"
        package_app_archive(
            out_dir, zip_path, native_python_env=native_python_env
        )

    lockfile_path = None
    if write_lock:
        lockfile_path = write_lockfile(out_dir)

    return BuildReport(
        brand_id=brand_id,
        channel=channel,
        environment=environment,
        out_dir=out_dir,
        zip_path=zip_path,
        lockfile_path=lockfile_path,
        warnings=warnings,
    )


def build_all(
    core_dir: str | Path,
    manifests_dir: str | Path,
    out_root: str | Path,
    environment: str,
    *,
    package_zip: bool = False,
    native_python_env: bool = False,
    strip_bridge: str | None = None,
    write_locks: bool = False,
) -> list[BuildReport]:
    manifests = load_manifests(manifests_dir)
    reports = []

    out_root = Path(out_root).resolve()

    for brand_id, policy in manifests.policies.items():
        for channel in policy.channels:
            out_dir = out_root / brand_id / channel.lower()
            rep = build_app(
                core_dir=core_dir,
                manifests_dir=manifests_dir,
                brand_id=brand_id,
                channel=channel,
                environment=environment,
                out_dir=out_dir,
                package_zip=package_zip,
                native_python_env=native_python_env,
                strip_bridge=strip_bridge,
                write_lock=write_locks,
            )
            reports.append(rep)

    return reports
