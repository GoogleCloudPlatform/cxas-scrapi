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

import ast
import io
import json
import os
import re
import shutil
import tokenize
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

_ALLOWED_ENV_TEMPLATE_VARS = frozenset(
    {"brand", "brand_slug", "env", "channel", "service"}
)
_ENV_TEMPLATE_BRACE_RE = re.compile(r"\{([^{}]*)\}")
_DANGLING_TOKEN_RE = re.compile(
    r"@@(?:brand|env|routing|build)\.[A-Za-z0-9_.:-]+"
)
_PY_STR_PREFIX_RE = re.compile(r"^([rRuUbBfF]*)('''|\"\"\"|'|\")")
_FSTRING_MIDDLE = getattr(tokenize, "FSTRING_MIDDLE", None)


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


def _paths_overlap(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def _render_env_template(
    key: str, template: str, values: dict[str, str]
) -> str:
    """Safely render an env template allowing only known simple placeholders."""
    if not isinstance(template, str):
        raise BuildError(f"Env template '{key}' must be a string.")

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in _ALLOWED_ENV_TEMPLATE_VARS:
            raise BuildError(
                f"Invalid placeholder '{{{name}}}' in env template '{key}'; "
                f"allowed: {sorted(_ALLOWED_ENV_TEMPLATE_VARS)}"
            )
        return values[name]

    # Check for unmatched braces before substitution.
    stripped = _ENV_TEMPLATE_BRACE_RE.sub("", template)
    if "{" in stripped or "}" in stripped:
        raise BuildError(
            f"Unmatched brace in env template '{key}': {template!r}"
        )
    return _ENV_TEMPLATE_BRACE_RE.sub(_sub, template)


def _escape_for_python_quote(value: str, quote: str) -> str:
    escaped = value.replace("\\", "\\\\")
    if len(quote) == 1:
        return (
            escaped.replace(quote, f"\\{quote}")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
        )
    escaped = escaped.replace(quote, f"\\{quote[0]}" * 3)
    if escaped.endswith(quote[0]):
        escaped = escaped[:-1] + f"\\{quote[0]}"
    return escaped


def _render_python_source(
    source: str,
    path: Path,
    process_conditionals: Any,
    process_string: Any,
) -> str:
    """Process conditionals and tokens in Python code with safe string escaping.

    Escapes substituted values inside Python string literals so quotes,
    backslashes, and newlines do not corrupt syntax, then validates the result
    with ``ast.parse``.
    """
    text = process_conditionals(source)
    if "@@" not in text:
        try:
            ast.parse(text, filename=str(path))
        except SyntaxError as exc:
            raise BuildError(
                f"{path}: invalid Python syntax after build: {exc}"
            ) from exc
        return text

    lines = text.splitlines(keepends=True)
    line_offsets: list[int] = [0]
    for ln in lines:
        line_offsets.append(line_offsets[-1] + len(ln))

    def _to_offset(pos: tuple[int, int]) -> int:
        row, col = pos
        return line_offsets[row - 1] + col

    edits: list[tuple[int, int, str]] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError) as exc:
        raise BuildError(f"{path}: invalid Python syntax: {exc}") from exc

    for tok in tokens:
        if "@@" not in tok.string:
            continue
        start_off = _to_offset(tok.start)
        end_off = _to_offset(tok.end)

        if tok.type == tokenize.STRING:
            m = _PY_STR_PREFIX_RE.match(tok.string)
            if not m:
                raise BuildError(
                    f"{path}:{tok.start[0]}: unsupported string literal"
                )
            prefix, quote = m.group(1), m.group(2)
            body = tok.string[len(prefix) + len(quote) : -len(quote)]
            if "r" in prefix.lower():
                rendered_val = process_string(body)
                new_prefix = "".join(c for c in prefix if c not in "rR")
                new_tok = (
                    f"{new_prefix}{quote}"
                    f"{_escape_for_python_quote(rendered_val, quote)}{quote}"
                )
            else:
                # Substitute only inside placeholder spans while keeping
                # existing escapes in the literal intact.
                def _sub_literal(s: str, q: str = quote) -> str:
                    def _esc_token(
                        match: re.Match[str],
                    ) -> str:
                        resolved = process_string(match.group(0))
                        return _escape_for_python_quote(resolved, q)

                    # Replace closed @@...@@ tokens first, then verify no
                    # dangling tokens remain.
                    replaced = re.sub(r"@@[^@\s]+@@", _esc_token, s)
                    dangling = _DANGLING_TOKEN_RE.findall(replaced)
                    if dangling:
                        raise BuildError(
                            f"Unresolved placeholders remain in string: "
                            f"{dangling}"
                        )
                    return replaced

                new_tok = f"{prefix}{quote}{_sub_literal(body)}{quote}"
            edits.append((start_off, end_off, new_tok))
        elif _FSTRING_MIDDLE is not None and tok.type == _FSTRING_MIDDLE:
            rendered = process_string(tok.string)
            safe = (
                rendered.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("'", "\\'")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
            )
            edits.append((start_off, end_off, safe))
        elif tok.type == tokenize.COMMENT:
            rendered = process_string(tok.string)
            safe = rendered.replace("\r", " ").replace("\n", " ")
            edits.append((start_off, end_off, safe))
        else:
            edits.append((start_off, end_off, process_string(tok.string)))

    result = text
    for start_off, end_off, replacement in reversed(edits):
        result = result[:start_off] + replacement + result[end_off:]

    try:
        ast.parse(result, filename=str(path))
    except SyntaxError as exc:
        raise BuildError(
            f"{path}: invalid Python syntax after substitution: {exc}"
        ) from exc
    return result


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


def _read_json_strict(path: Path) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise BuildError(f"{path}: invalid JSON: {exc}") from exc


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
    manifests_path = Path(manifests_dir).resolve()
    out_dir = Path(out_dir).resolve()

    if not core_dir.is_dir():
        raise BuildError(
            f"Core directory '{core_dir}' does not exist or is not a directory."
        )
    if _paths_overlap(out_dir, core_dir):
        raise BuildError(
            f"Output directory '{out_dir}' must not overlap with core "
            f"directory '{core_dir}'."
        )
    if _paths_overlap(out_dir, manifests_path):
        raise BuildError(
            f"Output directory '{out_dir}' must not overlap with manifests "
            f"directory '{manifests_path}'."
        )

    manifests = load_manifests(manifests_path)
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

    # Ensure environment.json exists when env_manifest defines overrides so it
    # flows through the merge, pruning, and token-substitution pipeline.
    if env_manifest.overrides and not (out_dir / "environment.json").exists():
        (out_dir / "environment.json").write_text("{}\n", "utf-8")

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
                    d = _read_json_strict(a_json)
                    if isinstance(d, dict):
                        display_name = d.get("displayName", "")
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
                    d = _read_json_strict(t_json)
                    if isinstance(d, dict):
                        display_name = d.get("displayName", "")
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

    if (
        channel_caps.entry_agent
        and channel_caps.entry_agent in all_disabled_agents
    ):
        raise BuildError(
            f"Channel '{channel}' entry_agent '{channel_caps.entry_agent}' "
            "cannot also be in disabled_agents."
        )

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

    template_values = {
        "brand": brand.brand_id,
        "brand_slug": brand.brand_id.replace("_", "-"),
        "env": environment,
        "channel": channel,
        "service": "{service}",
    }
    rendered_env_templates: list[tuple[str, re.Pattern[str], str]] = []
    for k, v in sorted(env_manifest.templates.items()):
        v_rendered = _render_env_template(k, v, template_values)
        replacements[f"@@env.{k}@@"] = v_rendered.replace("{service}", "")
        rendered_env_templates.append(
            (k, re.compile(rf"@@env\.{re.escape(k)}:([^@\s]+)@@"), v_rendered)
        )

    cond_start_re = re.compile(
        r"^\{@start(Channel|Brand|AgentEnabled|ToolEnabled)_([^}]+)\}$"
    )
    cond_end_re = re.compile(
        r"^\{@end(Channel|Brand|AgentEnabled|ToolEnabled)_([^}]+)\}$"
    )
    cond_token_re = re.compile(
        r"\{@(start|end)(Channel|Brand|AgentEnabled|ToolEnabled)_([^}]+)\}"
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

            outer_active = all(item[2] for item in stack)
            inline_stack: list[tuple[str, str, bool]] = []
            line_parts: list[str] = []
            pos = 0

            for match in cond_token_re.finditer(line):
                if outer_active and all(item[2] for item in inline_stack):
                    line_parts.append(line[pos : match.start()])
                pos = match.end()

                action, kind, arg = (
                    match.group(1),
                    match.group(2),
                    match.group(3),
                )
                if action == "start":
                    inline_stack.append((kind, arg, _eval_cond(kind, arg)))
                else:
                    if (
                        not inline_stack
                        or inline_stack[-1][0] != kind
                        or inline_stack[-1][1] != arg
                    ):
                        raise BuildError(
                            f"Line {i + 1}: Unbalanced or overlapping inline "
                            f"conditional tag {match.group(0)}."
                        )
                    inline_stack.pop()

            if inline_stack:
                unclosed_inline = [
                    f"{{@start{k}_{a}}}" for k, a, _ in inline_stack
                ]
                raise BuildError(
                    f"Line {i + 1}: Unbalanced inline conditional tag(s): "
                    f"{unclosed_inline}"
                )

            if outer_active:
                line_parts.append(line[pos:])
                out_lines.append("".join(line_parts))

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

        unresolved = re.findall(r"@@[^@\s]+@@", s)
        if unresolved:
            raise BuildError(
                f"Unresolved placeholders remain in string: {unresolved}"
            )
        dangling = _DANGLING_TOKEN_RE.findall(s)
        if dangling:
            raise BuildError(
                f"Unresolved or unclosed placeholders remain in string: "
                f"{dangling}"
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
                data = _read_json_strict(path)

                if isinstance(data, dict):
                    # Merge environment overrides BEFORE pruning so overrides
                    # cannot re-inject disabled tools.
                    if file == "environment.json" and env_manifest.overrides:
                        data = deep_merge(data, env_manifest.overrides)

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
                        if root_pruned:
                            if not channel_caps.entry_agent:
                                raise BuildError(
                                    f"rootAgent '{current_root}' is disabled "
                                    f"for channel '{channel}' and no "
                                    "replacement entry_agent is configured."
                                )
                            data["rootAgent"] = channel_caps.entry_agent
                        elif not current_root and channel_caps.entry_agent:
                            data["rootAgent"] = channel_caps.entry_agent

                        if brand.tts_voices:
                            if not isinstance(
                                data.get("audioProcessingConfig"), dict
                            ):
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
                            if not isinstance(
                                data.get("languageSettings"), dict
                            ):
                                data["languageSettings"] = {}
                            data["languageSettings"]["defaultLanguageCode"] = (
                                brand.languages[0]
                            )
                            data["languageSettings"][
                                "supportedLanguageCodes"
                            ] = brand.languages
                            data["languageSettings"][
                                "enableMultilingualSupport"
                            ] = len(brand.languages) > 1

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
                    if re.search(rf"\{{@AGENT:\s*{re.escape(a)}\s*\}}", text):
                        warnings.append(
                            f"{path.relative_to(out_dir)} mentions pruned "
                            f"agent {a}"
                        )
                for t in sorted(all_disabled_tools):
                    if re.search(rf"\{{@TOOL:\s*{re.escape(t)}\s*\}}", text):
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
                        text = _render_python_source(
                            text,
                            path,
                            process_conditional_blocks,
                            process_string,
                        )
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

    # 6. Optional deterministic ZIP package (with env var & boolean resolution)
    zip_path = None
    if package_zip:
        zip_path = out_dir / f"{brand_id}_{slug}.zip"
        try:
            package_app_archive(out_dir, zip_path, resolved_env_vars=[])
        except (OSError, ValueError) as exc:
            raise BuildError(f"Failed to package {zip_path}: {exc}") from exc

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
    core_path = Path(core_dir).resolve()
    if not core_path.is_dir():
        raise BuildError(
            f"Core directory '{core_path}' does not exist or is not a "
            "directory."
        )

    manifests = load_manifests(manifests_dir)
    manifests.env(environment)

    reports: list[BuildReport] = []
    out_root = Path(out_root).resolve()

    for brand_id, policy in sorted(manifests.policies.items()):
        for channel in sorted(policy.channels):
            out_dir = out_root / brand_id / channel.lower()
            rep = build_app(
                core_dir=core_path,
                manifests_dir=manifests_dir,
                brand_id=brand_id,
                channel=channel,
                environment=environment,
                out_dir=out_dir,
                package_zip=package_zip,
            )
            reports.append(rep)

    return reports
