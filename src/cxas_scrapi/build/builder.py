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

"""Deterministic Multi-Brand Build engine for CXAS."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from cxas_scrapi.build.manifests import (
    CHANNEL_PROFILE_TYPES,
    Manifests,
    load_manifests,
)
from cxas_scrapi.core.packaging import package_app_archive


class BuildError(Exception):
    """Raised when building a brand/channel app fails."""


class BuildReport(BaseModel):
    """Summary of one built brand/channel CES app."""

    brand_id: str
    channel: str
    environment: str
    out_dir: Path
    zip_path: Path | None = None
    warnings: list[str] = Field(default_factory=list)


def deep_merge(
    target: dict[str, Any], overrides: dict[str, Any]
) -> dict[str, Any]:
    """Recursively merge ``overrides`` into a shallow copy of ``target``."""
    merged = dict(target)
    for k, v in overrides.items():
        if isinstance(v, dict) and k in merged and isinstance(merged[k], dict):
            merged[k] = deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


def validate_modalities(content: str, path: Path) -> None:
    """Validate that ``{@startModality_*}`` / ``{@endModality_*}`` balance."""
    lines = content.split("\n")
    stack: list[str] = []

    modality_re = re.compile(r"({@startModality_[^}]+}|{@endModality_[^}]+})")

    for i, line in enumerate(lines):
        matches = modality_re.findall(line)
        if not matches:
            continue

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


def _build_identity_block(manifests: Manifests, brand_id: str) -> str:
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
) -> BuildReport:
    """Build one CES app for ``(brand_id, channel, environment)``."""
    core_dir = Path(core_dir).resolve()
    out_dir = Path(out_dir).resolve()

    if not core_dir.is_dir():
        raise BuildError(
            f"Core directory '{core_dir}' does not exist or is not a directory."
        )
    if (
        out_dir == core_dir
        or core_dir in out_dir.parents
        or out_dir in core_dir.parents
    ):
        raise BuildError(
            f"Output directory '{out_dir}' must not overlap with core "
            f"directory '{core_dir}'."
        )

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

    # 2. Prune disabled agents & tools (track both dir names and displayNames)
    pruned_agent_names: set[str] = set()
    pruned_tool_names: set[str] = set()

    disabled_agents = set(channel_caps.disabled_agents)
    disabled_tools = set(channel_caps.disabled_tools)

    agents_dir = out_dir / "agents"
    tools_dir = out_dir / "tools"

    if agents_dir.exists():
        for agent_path in sorted(agents_dir.iterdir()):
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
                        pruned_agent_names.add(display_name)
                    pruned_agent_names.add(agent_path.name)
                    shutil.rmtree(agent_path)

    if tools_dir.exists():
        for tool_path in sorted(tools_dir.iterdir()):
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
                        pruned_tool_names.add(display_name)
                    pruned_tool_names.add(tool_path.name)
                    shutil.rmtree(tool_path)

    all_disabled_agents = disabled_agents | pruned_agent_names
    all_disabled_tools = disabled_tools | pruned_tool_names

    # 3. Pre-compute deterministic token replacements
    replacements: dict[str, str] = {
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

    rendered_env_templates: list[tuple[str, re.Pattern[str], str]] = []
    for k, v in sorted(env_manifest.templates.items()):
        try:
            v_rendered = v.format(
                brand=brand.brand_id,
                brand_slug=brand.brand_id.replace("_", "-"),
                env=environment,
                channel=channel,
                service="{service}",
            )
        except KeyError as exc:
            raise BuildError(
                f"Unknown template variable {exc} in env template '{k}'"
            ) from exc
        replacements[f"@@env.{k}@@"] = v_rendered.replace("{service}", "")
        rendered_env_templates.append(
            (k, re.compile(rf"@@env\.{re.escape(k)}:([^@]+)@@"), v_rendered)
        )

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
            return arg not in all_disabled_agents
        if kind == "ToolEnabled":
            return arg not in all_disabled_tools
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
        for ek, pattern, base_rendered in rendered_env_templates:

            def _repl(m: re.Match[str], rendered: str = base_rendered) -> str:
                return rendered.replace("{service}", m.group(1))

            s = pattern.sub(_repl, s)
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
        if isinstance(val, list):
            return [process_json(item) for item in val]
        if isinstance(val, str):
            return process_string(val)
        return val

    # 4. Prune references & render tokens across surviving files
    for root, dirs, files in os.walk(out_dir):
        dirs.sort()
        for file in sorted(files):
            path = Path(root) / file

            if file.endswith(".json"):
                try:
                    data = json.loads(path.read_text("utf-8"))
                except (ValueError, OSError):
                    continue

                if isinstance(data, dict):
                    if isinstance(data.get("childAgents"), list):
                        data["childAgents"] = [
                            c
                            for c in data["childAgents"]
                            if (
                                c.get("displayName")
                                if isinstance(c, dict)
                                else c
                            )
                            not in all_disabled_agents
                        ]

                    if isinstance(data.get("tools"), list):
                        data["tools"] = [
                            t
                            for t in data["tools"]
                            if (
                                t.get("displayName")
                                if isinstance(t, dict)
                                else t
                            )
                            not in all_disabled_tools
                        ]
                    elif isinstance(data.get("tools"), dict):
                        data["tools"] = {
                            t_key: t_val
                            for t_key, t_val in data["tools"].items()
                            if t_key not in all_disabled_tools
                        }

                    if file == "app.json":
                        # Do NOT unconditionally overwrite app.json["rootAgent"]
                        # Set only if missing or pointing to a pruned agent.
                        current_root = data.get("rootAgent")
                        root_pruned = current_root in all_disabled_agents
                        if channel_caps.entry_agent and (
                            not current_root or root_pruned
                        ):
                            data["rootAgent"] = channel_caps.entry_agent
                        elif root_pruned and not channel_caps.entry_agent:
                            del data["rootAgent"]

                        if brand.tts_voices:
                            if "audioProcessingConfig" not in data:
                                data["audioProcessingConfig"] = {}
                            synth_configs = {
                                lang: {
                                    "voice": tts.voice,
                                    "speakingRate": tts.speaking_rate,
                                }
                                for lang, tts in brand.tts_voices.items()
                            }
                            data["audioProcessingConfig"][
                                "synthesizeSpeechConfigs"
                            ] = synth_configs

                        if brand.languages:
                            if "languageSettings" not in data:
                                data["languageSettings"] = {}
                            data["languageSettings"]["defaultLanguageCode"] = (
                                brand.languages[0]
                            )
                            data["languageSettings"][
                                "supportedLanguageCodes"
                            ] = brand.languages
                            if len(brand.languages) > 1:
                                data["languageSettings"][
                                    "enableMultilingualSupport"
                                ] = True

                    if file == "environment.json" and env_manifest.overrides:
                        data = deep_merge(data, env_manifest.overrides)

                try:
                    data = process_json(data)
                except BuildError as e:
                    raise BuildError(f"{path}: {e}") from e

                path.write_text(json.dumps(data, indent=2) + "\n", "utf-8")

            elif file.endswith(".txt"):
                text = path.read_text("utf-8")

                if file == "global_instruction.txt":
                    identity_block = _build_identity_block(manifests, brand_id)
                    if "@@brand.identity_block@@" in text:
                        text = text.replace(
                            "@@brand.identity_block@@", identity_block
                        )
                    else:
                        text = identity_block + "\n\n" + text

                try:
                    text = process_conditional_blocks(text)
                except BuildError as e:
                    raise BuildError(f"{path}: {e}") from e

                for a in sorted(all_disabled_agents):
                    if f"{{@AGENT: {a}}}" in text:
                        warnings.append(
                            f"{path.relative_to(out_dir)} mentions pruned "
                            f"agent {a}"
                        )
                for t in sorted(all_disabled_tools):
                    if f"{{@TOOL: {t}}}" in text:
                        warnings.append(
                            f"{path.relative_to(out_dir)} mentions pruned "
                            f"tool {t}"
                        )

                try:
                    text = process_string(text)
                except BuildError as e:
                    raise BuildError(f"{path}: {e}") from e

                path.write_text(text, "utf-8")
                validate_modalities(text, path.relative_to(out_dir))

            elif file.endswith(".py"):
                text = path.read_text("utf-8")
                if "@@" in text or "{@start" in text or "{@end" in text:
                    try:
                        text = process_conditional_blocks(text)
                        text = process_string(text)
                    except BuildError as e:
                        raise BuildError(f"{path}: {e}") from e
                    path.write_text(text, "utf-8")

    # 5. Emit deployments/<channel>.yaml
    deployments_dir = out_dir / "deployments"
    deployments_dir.mkdir(exist_ok=True)
    slug = channel.lower().replace("_", "-")
    dep_path = deployments_dir / f"{slug}.yaml"

    dep_data: dict[str, Any] = {
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

    with open(dep_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(dep_data, f, sort_keys=False)

    # 6. Optional deterministic ZIP package
    zip_path = None
    if package_zip:
        zip_path = out_dir / f"{brand_id}_{slug}.zip"
        package_app_archive(out_dir, zip_path)

    return BuildReport(
        brand_id=brand_id,
        channel=channel,
        environment=environment,
        out_dir=out_dir,
        zip_path=zip_path,
        warnings=warnings,
    )


def build_all(
    core_dir: str | Path,
    manifests_dir: str | Path,
    out_root: str | Path,
    environment: str,
    *,
    package_zip: bool = False,
) -> list[BuildReport]:
    """Build every ``(brand_id, channel)`` pair declared in manifests."""
    manifests = load_manifests(manifests_dir)
    reports: list[BuildReport] = []

    out_root = Path(out_root).resolve()

    for brand_id, policy in sorted(manifests.policies.items()):
        for channel in sorted(policy.channels):
            out_dir = out_root / brand_id / channel.lower()
            rep = build_app(
                core_dir=core_dir,
                manifests_dir=manifests_dir,
                brand_id=brand_id,
                channel=channel,
                environment=environment,
                out_dir=out_dir,
                package_zip=package_zip,
            )
            reports.append(rep)

    return reports
