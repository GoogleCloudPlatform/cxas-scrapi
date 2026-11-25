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
import copy
import io
import itertools
import json
import os
import re
import shutil
import tokenize
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeAlias, TypeVar

import yaml
from pydantic import BaseModel, Field

from cxas_scrapi.build.manifests import (
    CHANNEL_PROFILE_TYPES,
    BrandIdentity,
    ChannelCapabilities,
    ChannelPolicy,
    ContentBlockCatalog,
    EnvManifest,
    Manifests,
    load_manifests,
)
from cxas_scrapi.core.packaging import package_app_archive

if TYPE_CHECKING:
    from collections.abc import Sequence

_T = TypeVar("_T")
JsonValue: TypeAlias = Any
ConfigMap: TypeAlias = dict[str, JsonValue]

_ALLOWED_ENV_TEMPLATE_VARS = frozenset(
    {"brand", "brand_slug", "env", "channel", "service"}
)
_ENV_TEMPLATE_BRACE_RE = re.compile(r"\{([^{}]*)\}")
_UNRESOLVED_TOKEN_RE = re.compile(r"@@[^@\s]+@@")
_DANGLING_TOKEN_RE = re.compile(
    r"@@(?:brand|env|routing|build)\.[A-Za-z0-9_.:-]+"
)
_COND_START_RE = re.compile(
    r"^\{@start(Channel|Brand|AgentEnabled|ToolEnabled)_([^}]+)\}$"
)
_COND_END_RE = re.compile(
    r"^\{@end(Channel|Brand|AgentEnabled|ToolEnabled)_([^}]+)\}$"
)
_COND_TOKEN_RE = re.compile(
    r"\{@(start|end)(Channel|Brand|AgentEnabled|ToolEnabled)_([^}]+)\}"
)
_MODALITY_TAG_RE = re.compile(r"({@startModality_[^}]+}|{@endModality_[^}]+})")
_PY_STR_PREFIX_RE = re.compile(r"^([rRuUbBfF]*)('''|\"\"\"|'|\")")
_FSTRING_START = getattr(tokenize, "FSTRING_START", None)
_FSTRING_MIDDLE = getattr(tokenize, "FSTRING_MIDDLE", None)
_FSTRING_END = getattr(tokenize, "FSTRING_END", None)
_STAGE_IGNORE_PATTERNS = ("__pycache__", "*.pyc", ".DS_Store", "manifests")
_RESOURCE_CONFIG_SUFFIXES = (".json", ".yaml", ".yml")


# scbc ignore[empty-exception-subclass] public API exception
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
    target: Mapping[str, JsonValue], overrides: Mapping[str, JsonValue]
) -> ConfigMap:
    """Recursively merge ``overrides`` into a deep copy of ``target``."""
    merged: ConfigMap = copy.deepcopy(dict(target))
    merged.update(
        {
            k: deep_merge(merged[k], v)
            if isinstance(v, Mapping) and isinstance(merged.get(k), Mapping)
            else copy.deepcopy(v)
            for k, v in overrides.items()
        }
    )
    return merged


def _render_env_template(
    key: str, template: str, values: Mapping[str, str]
) -> str:
    """Safely render an env template allowing only known simple placeholders."""

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in _ALLOWED_ENV_TEMPLATE_VARS:
            raise BuildError(
                f"Invalid placeholder '{{{name}}}' in env template '{key}'; "
                f"allowed: {sorted(_ALLOWED_ENV_TEMPLATE_VARS)}"
            )
        return values[name]

    stripped = _ENV_TEMPLATE_BRACE_RE.sub("", template)
    if "{" in stripped or "}" in stripped:
        raise BuildError(
            f"Unmatched brace in env template '{key}': {template!r}"
        )
    return _ENV_TEMPLATE_BRACE_RE.sub(_sub, template)


def _pop_cond_stack(
    stack: list[tuple[str, str, bool]],
    kind: str,
    arg: str,
    error_message: str,
) -> None:
    if not stack or stack[-1][0] != kind or stack[-1][1] != arg:
        raise BuildError(error_message)
    stack.pop()


def _check_remaining_tokens(
    text: str, *, check_closed: bool = True, unclosed_label: str = "Unresolved"
) -> None:
    checks: list[tuple[re.Pattern[str], str]] = [
        (_DANGLING_TOKEN_RE, unclosed_label)
    ]
    if check_closed:
        checks.insert(0, (_UNRESOLVED_TOKEN_RE, "Unresolved"))
    for pattern, label in checks:
        found = pattern.findall(text)
        if found:
            raise BuildError(f"{label} placeholders remain in string: {found}")


@dataclass(frozen=True)
class _BuildRenderer:
    """Evaluates build-time conditional blocks and ``@@...@@`` tokens."""

    brand_id: str
    channel: str
    disabled_agents: frozenset[str]
    disabled_tools: frozenset[str]
    replacements: dict[str, str]
    env_templates: tuple[tuple[re.Pattern[str], str], ...]
    cond_predicates: dict[str, Callable[[str], bool]]

    @classmethod
    def from_context(
        cls,
        brand: BrandIdentity,
        policy: ChannelPolicy,
        env_manifest: EnvManifest,
        channel: str,
        environment: str,
        disabled_agents: frozenset[str],
        disabled_tools: frozenset[str],
    ) -> _BuildRenderer:
        replacements: dict[str, str] = {
            "@@brand.brand_id@@": brand.brand_id,
            "@@brand.display_name@@": brand.display_name,
            "@@brand.assistant_title@@": brand.assistant_title or "",
            "@@brand.legal_entity@@": brand.legal_entity or "",
            "@@brand.tone_guidelines@@": brand.tone_guidelines or "",
            "@@build.channel@@": channel,
            "@@build.environment@@": environment,
            **{f"@@brand.lexicon.{k}@@": v for k, v in brand.lexicon.items()},
            **{f"@@brand.urls.{k}@@": v for k, v in brand.urls.items()},
            **{f"@@routing.{k}@@": v for k, v in policy.api_routing.items()},
        }
        template_values = {
            "brand": brand.brand_id,
            "brand_slug": brand.brand_id.replace("_", "-"),
            "env": environment,
            "channel": channel,
            "service": "{service}",
        }
        env_templates: list[tuple[re.Pattern[str], str]] = []
        for k, v in sorted(env_manifest.templates.items()):
            rendered = _render_env_template(k, v, template_values)
            replacements[f"@@env.{k}@@"] = rendered.replace("{service}", "")
            env_templates.append(
                (re.compile(rf"@@env\.{re.escape(k)}:([^@\s]+)@@"), rendered)
            )

        predicates: dict[str, Callable[[str], bool]] = {
            "Channel": lambda arg: arg == channel,
            "Brand": lambda arg: arg == brand.brand_id,
            "AgentEnabled": lambda arg: arg not in disabled_agents,
            "ToolEnabled": lambda arg: arg not in disabled_tools,
        }
        return cls(
            brand_id=brand.brand_id,
            channel=channel,
            disabled_agents=disabled_agents,
            disabled_tools=disabled_tools,
            replacements=replacements,
            env_templates=tuple(env_templates),
            cond_predicates=predicates,
        )

    def _eval_cond(self, kind: str, arg: str) -> bool:
        if kind not in self.cond_predicates:
            raise BuildError(f"Unknown conditional tag kind: {kind}")
        return self.cond_predicates[kind](arg)

    def _apply_inline_token(
        self,
        match: re.Match[str],
        line_no: int,
        inline_stack: list[tuple[str, str, bool]],
    ) -> None:
        action, kind, arg = match.group(1), match.group(2), match.group(3)
        if action == "start":
            inline_stack.append((kind, arg, self._eval_cond(kind, arg)))
            return
        _pop_cond_stack(
            inline_stack,
            kind,
            arg,
            f"Line {line_no}: Unbalanced or overlapping inline "
            f"conditional tag {match.group(0)}.",
        )

    def _process_inline_conditionals(
        self, line: str, line_no: int, outer_active: bool
    ) -> str:
        inline_stack: list[tuple[str, str, bool]] = []
        parts: list[str] = []
        pos = 0

        for match in _COND_TOKEN_RE.finditer(line):
            segment = line[pos : match.start()]
            if outer_active and all(item[2] for item in inline_stack):
                parts.append(segment)
            pos = match.end()
            self._apply_inline_token(match, line_no, inline_stack)

        if inline_stack:
            unclosed = [f"{{@start{k}_{a}}}" for k, a, _ in inline_stack]
            raise BuildError(
                f"Line {line_no}: Unbalanced inline conditional tag(s): "
                f"{unclosed}"
            )
        if not outer_active:
            return ""
        parts.append(line[pos:])
        return "".join(parts)

    def _handle_block_tag(
        self,
        stripped: str,
        line_no: int,
        stack: list[tuple[str, str, bool]],
    ) -> bool:
        m_start = _COND_START_RE.match(stripped)
        if m_start:
            kind, arg = m_start.group(1), m_start.group(2)
            stack.append((kind, arg, self._eval_cond(kind, arg)))
        elif m_end := _COND_END_RE.match(stripped):
            _pop_cond_stack(
                stack,
                m_end.group(1),
                m_end.group(2),
                f"Line {line_no}: Unbalanced or mismatched conditional "
                f"tag {stripped}.",
            )
        else:
            return False
        return True

    def process_conditional_blocks(self, content: str) -> str:
        if "{@start" not in content and "{@end" not in content:
            return content

        out_lines: list[str] = []
        stack: list[tuple[str, str, bool]] = []

        for idx, line in enumerate(content.split("\n"), start=1):
            if self._handle_block_tag(line.strip(), idx, stack):
                continue
            outer_active = all(item[2] for item in stack)
            rendered = self._process_inline_conditionals(
                line, idx, outer_active
            )
            if outer_active:
                out_lines.append(rendered)

        if stack:
            unclosed = [f"{{@start{k}_{a}}}" for k, a, _ in stack]
            raise BuildError(f"Unbalanced conditional tags remain: {unclosed}")

        return "\n".join(out_lines)

    def substitute_tokens(self, text: str) -> str:
        for pattern, base_rendered in self.env_templates:
            text = pattern.sub(
                lambda m, rendered=base_rendered: rendered.replace(
                    "{service}", m.group(1)
                ),
                text,
            )

        for token, value in self.replacements.items():
            if token in text:
                text = text.replace(token, value)

        _check_remaining_tokens(text, unclosed_label="Unresolved or unclosed")
        return text

    def process_string(
        self, text: str, *, evaluate_conditionals: bool = True
    ) -> str:
        if evaluate_conditionals:
            text = self.process_conditional_blocks(text)
        return self.substitute_tokens(text)

    def process_json(
        self, val: JsonValue, *, evaluate_conditionals: bool = True
    ) -> JsonValue:
        # scbc boundary: recursively traverses untyped JSON/YAML payloads
        if isinstance(val, dict):
            return {
                k: self.process_json(
                    v, evaluate_conditionals=evaluate_conditionals
                )
                for k, v in val.items()
            }
        if isinstance(val, list):
            return [
                self.process_json(
                    item, evaluate_conditionals=evaluate_conditionals
                )
                for item in val
            ]
        if isinstance(val, str):
            return self.process_string(
                val, evaluate_conditionals=evaluate_conditionals
            )
        return val


def _escape_for_python_quote(
    value: str, quote: str, *, escape_braces: bool = False
) -> str:
    escaped = value.replace("\\", "\\\\")
    if len(quote) == 1:
        escaped = (
            escaped.replace(quote, f"\\{quote}")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
        )
    else:
        escaped = escaped.replace(quote[0], f"\\{quote[0]}").replace(
            "\r", "\\r"
        )
    if escape_braces:
        escaped = escaped.replace("{", "{{").replace("}", "}}")
    return escaped


def _escape_for_raw_python_quote(
    value: str, quote: str, *, escape_braces: bool = False
) -> str:
    """Validate and prepare a value for substitution inside a raw string."""
    trailing_bs = len(value) - len(value.rstrip("\\"))
    violations = (
        (
            "\r" in value or (len(quote) == 1 and "\n" in value),
            "Cannot safely substitute multiline value into a raw Python "
            "string literal.",
        ),
        (
            quote in value,
            "Cannot safely substitute value containing quote delimiter into "
            "a raw Python string literal.",
        ),
        (
            len(quote) == 3 and value.endswith(quote[0]),
            "Cannot safely substitute value ending in quote delimiter into a "
            "raw triple-quoted Python string literal.",
        ),
        (
            trailing_bs % 2 == 1,
            "Cannot safely substitute value ending in an odd number of "
            "backslashes into a raw Python string literal.",
        ),
    )
    for failed, message in violations:
        if failed:
            raise BuildError(message)
    return (
        value.replace("{", "{{").replace("}", "}}") if escape_braces else value
    )


def _parse_python_or_raise(source: str, path: Path, stage: str) -> None:
    try:
        ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise BuildError(
            f"{path}: invalid Python syntax {stage}: {exc}"
        ) from exc


def _substitute_python_literal(
    body: str,
    quote: str,
    substitute: Callable[[str], str],
    *,
    is_raw: bool = False,
    escape_braces: bool = False,
) -> str:
    escaper = (
        _escape_for_raw_python_quote if is_raw else _escape_for_python_quote
    )
    replaced = _UNRESOLVED_TOKEN_RE.sub(
        lambda match: escaper(
            substitute(match.group(0)), quote, escape_braces=escape_braces
        ),
        body,
    )
    _check_remaining_tokens(replaced, check_closed=False)
    return replaced


def _render_string_token(
    tok: tokenize.TokenInfo,
    path: Path,
    substitute: Callable[[str], str],
) -> str:
    match = _PY_STR_PREFIX_RE.match(tok.string)
    if not match:
        raise BuildError(f"{path}:{tok.start[0]}: unsupported string literal")
    prefix, quote = match.group(1), match.group(2)
    body = tok.string[len(prefix) + len(quote) : -len(quote)]
    is_f = "f" in prefix.lower()
    is_raw = "r" in prefix.lower()
    if is_raw and not is_f:
        rendered_val = substitute(body)
        new_prefix = "".join(c for c in prefix if c not in "rR")
        escaped = _escape_for_python_quote(rendered_val, quote)
        return f"{new_prefix}{quote}{escaped}{quote}"
    new_body = _substitute_python_literal(
        body, quote, substitute, is_raw=is_raw, escape_braces=is_f
    )
    return f"{prefix}{quote}{new_body}{quote}"


def _render_non_string_token(
    tok: tokenize.TokenInfo,
    path: Path,
    fstring_stack: list[tuple[str, str]],
    substitute: Callable[[str], str],
) -> str:
    if _FSTRING_MIDDLE is not None and tok.type == _FSTRING_MIDDLE:
        if not fstring_stack:
            msg = (
                f"{path}:{tok.start[0]}: unexpected f-string segment outside "
                "f-string literal"
            )
            raise BuildError(msg)
        prefix, quote = fstring_stack[-1]
        return _substitute_python_literal(
            tok.string,
            quote,
            substitute,
            is_raw="r" in prefix.lower(),
            escape_braces=True,
        )
    if tok.type == tokenize.COMMENT:
        return substitute(tok.string).replace("\r", " ").replace("\n", " ")
    return substitute(tok.string)


def _advance_fstring_stack(
    tok: tokenize.TokenInfo, fstring_stack: list[tuple[str, str]]
) -> bool:
    if tok.type == _FSTRING_START:
        m_fs = _PY_STR_PREFIX_RE.match(tok.string)
        if m_fs:
            fstring_stack.append((m_fs.group(1), m_fs.group(2)))
    elif tok.type == _FSTRING_END:
        if fstring_stack:
            fstring_stack.pop()
    else:
        return False
    return True


def _collect_python_edits(
    text: str, path: Path, substitute: Callable[[str], str]
) -> list[tuple[int, int, str]]:
    line_offsets = list(
        itertools.accumulate(
            (len(ln) for ln in text.splitlines(keepends=True)), initial=0
        )
    )
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError) as exc:
        raise BuildError(f"{path}: invalid Python syntax: {exc}") from exc

    edits: list[tuple[int, int, str]] = []
    fstring_stack: list[tuple[str, str]] = []
    for tok in tokens:
        if _advance_fstring_stack(tok, fstring_stack) or "@@" not in tok.string:
            continue
        start_off = line_offsets[tok.start[0] - 1] + tok.start[1]
        end_off = line_offsets[tok.end[0] - 1] + tok.end[1]
        replacement = (
            _render_string_token(tok, path, substitute)
            if tok.type == tokenize.STRING
            else _render_non_string_token(tok, path, fstring_stack, substitute)
        )
        edits.append((start_off, end_off, replacement))
    return edits


def _render_python_source(
    source: str, path: Path, renderer: _BuildRenderer
) -> str:
    """Process conditionals and tokens in Python code with safe string escaping.

    Escapes substituted values inside Python string literals (and f-strings,
    including doubling ``{``/``}``) so quotes, backslashes, newlines, or braces
    cannot corrupt syntax or inject code, then validates with ``ast.parse``.
    """
    text = renderer.process_conditional_blocks(source)
    if "@@" not in text:
        _parse_python_or_raise(text, path, "after build")
        return text

    edits = _collect_python_edits(text, path, renderer.substitute_tokens)
    result = text
    for start_off, end_off, replacement in reversed(edits):
        result = result[:start_off] + replacement + result[end_off:]
    _parse_python_or_raise(result, path, "after substitution")
    return result


def _apply_modality_tag(
    tag: str, line: str, path: Path, line_no: int, stack: list[str]
) -> None:
    if tag != line.strip():
        raise BuildError(
            f"{path}:{line_no}: Modality tag {tag} must be on its own line "
            "(found inline)."
        )
    if tag.startswith("{@startModality_"):
        stack.append(tag[16:-1])
        return
    modality = tag[14:-1]
    if not stack or stack[-1] != modality:
        raise BuildError(
            f"{path}:{line_no}: Unbalanced or mismatched modality tag {tag}."
        )
    stack.pop()


def validate_modalities(content: str, path: Path) -> None:
    """Validate that ``{@startModality_*}`` / ``{@endModality_*}`` balance."""
    stack: list[str] = []
    for line_no, line in enumerate(content.split("\n"), start=1):
        for tag in _MODALITY_TAG_RE.findall(line):
            _apply_modality_tag(tag, line, path, line_no, stack)
    if stack:
        raise BuildError(f"{path}: Unbalanced modality tags remain: {stack}")


def _format_kv_section(title: str, mapping: Mapping[str, str]) -> list[str]:
    if not mapping:
        return []
    return [f"{title}:", *(f"  - {k}: {v}" for k, v in sorted(mapping.items()))]


def _format_locale_blocks(
    catalog: ContentBlockCatalog | None,
    brand_id: str,
    countries: Sequence[str],
    languages: Sequence[str],
) -> list[str]:
    if catalog is None:
        return []
    lines: list[str] = []
    for country in sorted(countries):
        for lang in sorted(languages):
            blocks = catalog.blocks_for(brand_id, country, lang)
            if blocks:
                lines.extend(
                    f"[{country}-{lang}] {bk}: {bv}"
                    for bk, bv in sorted(blocks.items())
                )
    return lines


def _build_identity_block(manifests: Manifests, brand_id: str) -> str:
    brand = manifests.brand(brand_id)
    policy = manifests.policy(brand_id)
    scalar_fields = (
        ("Title", brand.assistant_title),
        ("Legal Entity", brand.legal_entity),
        ("Tone", brand.tone_guidelines),
    )
    lines = [
        f"# Brand: {brand.display_name}",
        *(f"{label}: {val}" for label, val in scalar_fields if val),
        *_format_kv_section("Lexicon", brand.lexicon),
        *_format_kv_section("URLs", brand.urls),
        *_format_locale_blocks(
            manifests.content_blocks,
            brand_id,
            policy.supported_countries,
            brand.languages,
        ),
    ]
    return "\n".join(lines)


def _read_json_strict(path: Path) -> JsonValue:
    # scbc boundary: parses external JSON file from disk
    try:
        return json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise BuildError(f"{path}: invalid JSON: {exc}") from exc


def _read_yaml_strict(path: Path) -> JsonValue:
    # scbc boundary: parses external YAML file from disk
    try:
        return yaml.safe_load(path.read_text("utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
        raise BuildError(f"{path}: invalid YAML: {exc}") from exc


def _read_resource_display_name(resource_dir: Path) -> str:
    """Read ``displayName`` from ``<id>.{json,yaml,yml}`` if present."""
    # scbc boundary: reads untyped JSON/YAML resource descriptor from disk
    cfg_path = next(
        (
            candidate
            for suffix in _RESOURCE_CONFIG_SUFFIXES
            if (
                candidate := resource_dir / f"{resource_dir.name}{suffix}"
            ).is_file()
        ),
        None,
    )
    if cfg_path is None:
        return ""
    data = (
        _read_json_strict(cfg_path)
        if cfg_path.suffix == ".json"
        else _read_yaml_strict(cfg_path)
    )
    if isinstance(data, dict) and isinstance(data.get("displayName"), str):
        return data["displayName"]
    return ""


def _validate_build_paths(
    core_dir: Path, manifests_path: Path, out_dir: Path
) -> None:
    if not core_dir.is_dir():
        msg = (
            f"Core directory '{core_dir}' does not exist or is not a directory."
        )
        raise BuildError(msg)
    for label, source in (("core", core_dir), ("manifests", manifests_path)):
        if (
            out_dir == source
            or out_dir in source.parents
            or source in out_dir.parents
        ):
            raise BuildError(
                f"Output directory '{out_dir}' must not overlap with {label} "
                f"directory '{source}'."
            )


def _stage_core_dir(
    core_dir: Path, out_dir: Path, *, has_env_overrides: bool
) -> None:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(
        core_dir,
        out_dir,
        ignore=shutil.ignore_patterns(*_STAGE_IGNORE_PATTERNS),
    )
    # Seed environment.json so env_manifest.overrides flows through the normal
    # merge, pruning, and token-substitution pass.
    if has_env_overrides and not (out_dir / "environment.json").exists():
        (out_dir / "environment.json").write_text("{}\n", "utf-8")


def _prune_resource_dir(
    resource_dir: Path, disabled: set[str]
) -> frozenset[str]:
    pruned: set[str] = set(disabled)
    if not resource_dir.exists():
        return frozenset(pruned)
    for entry in sorted(p for p in resource_dir.iterdir() if p.is_dir()):
        display_name = _read_resource_display_name(entry)
        if entry.name not in disabled and display_name not in disabled:
            continue
        if display_name:
            pruned.add(display_name)
        pruned.add(entry.name)
        shutil.rmtree(entry)
    return frozenset(pruned)


def _prune_mapping_key(
    data: ConfigMap,
    key: str,
    disabled: frozenset[str],
    *,
    allow_dict: bool = False,
) -> bool:
    # scbc boundary: filters resource refs inside untyped JSON/YAML mappings
    value = data.get(key)
    if isinstance(value, list):
        filtered = [
            item
            for item in value
            if (item.get("displayName") if isinstance(item, dict) else item)
            not in disabled
        ]
    elif allow_dict and isinstance(value, dict):
        filtered = {k: v for k, v in value.items() if k not in disabled}
    else:
        return False

    if len(filtered) == len(value):
        return False
    data[key] = filtered
    return True


def _prune_resource_refs(
    data: ConfigMap,
    disabled_agents: frozenset[str],
    disabled_tools: frozenset[str],
) -> bool:
    pruned_agents = _prune_mapping_key(data, "childAgents", disabled_agents)
    pruned_tools = _prune_mapping_key(
        data, "tools", disabled_tools, allow_dict=True
    )
    return pruned_agents or pruned_tools


def _ensure_dict_section(data: ConfigMap, key: str) -> ConfigMap:
    # scbc boundary: normalizes optional dict subsection in untyped app config
    section = data.get(key)
    if not isinstance(section, dict):
        section = {}
        data[key] = section
    return section


def _update_root_agent(
    data: ConfigMap,
    channel: str,
    entry_agent: str | None,
    disabled_agents: frozenset[str],
) -> bool:
    # Set rootAgent only when missing or pointing to a pruned agent so valid
    # custom roots stay intact.
    current_root = data.get("rootAgent")
    root_pruned = current_root in disabled_agents
    if root_pruned and not entry_agent:
        raise BuildError(
            f"rootAgent '{current_root}' is disabled for channel "
            f"'{channel}' and no replacement entry_agent is configured."
        )
    should_set = (root_pruned or not current_root) and bool(entry_agent)
    if should_set:
        data["rootAgent"] = entry_agent
    return should_set


def _apply_app_manifest_settings(
    data: ConfigMap,
    brand: BrandIdentity,
    channel: str,
    channel_caps: ChannelCapabilities,
    disabled_agents: frozenset[str],
) -> bool:
    updated = _update_root_agent(
        data, channel, channel_caps.entry_agent, disabled_agents
    )
    if brand.tts_voices:
        audio_cfg = _ensure_dict_section(data, "audioProcessingConfig")
        audio_cfg["synthesizeSpeechConfigs"] = {
            lang: {"voice": tts.voice, "speakingRate": tts.speaking_rate}
            for lang, tts in brand.tts_voices.items()
        }
        updated = True

    if brand.languages:
        lang_cfg = _ensure_dict_section(data, "languageSettings")
        lang_cfg.update(
            {
                "defaultLanguageCode": brand.languages[0],
                "supportedLanguageCodes": brand.languages,
                "enableMultilingualSupport": len(brand.languages) > 1,
            }
        )
        updated = True
    return updated


def _wrap_path_error(path: Path, fn: Callable[[], _T]) -> _T:
    try:
        return fn()
    except BuildError as exc:
        raise BuildError(f"{path}: {exc}") from exc


def _collect_pruned_mentions(
    text: str, rel_path: Path, renderer: _BuildRenderer
) -> list[str]:
    checks = (
        ("AGENT", "agent", renderer.disabled_agents),
        ("TOOL", "tool", renderer.disabled_tools),
    )
    warnings: list[str] = []
    for tag_name, resource_label, disabled in checks:
        warnings.extend(
            f"{rel_path} mentions pruned {resource_label} {name}"
            for name in sorted(disabled)
            if re.search(rf"\{{@{tag_name}:\s*{re.escape(name)}\s*\}}", text)
        )
    return warnings


def _render_json_file(
    path: Path,
    renderer: _BuildRenderer,
    brand: BrandIdentity,
    channel_caps: ChannelCapabilities,
    env_overrides: Mapping[str, JsonValue],
) -> None:
    data = _read_json_strict(path)
    if isinstance(data, dict):
        # Merge environment overrides before pruning so overrides cannot
        # re-introduce disabled tools.
        if path.name == "environment.json" and env_overrides:
            data = deep_merge(data, env_overrides)
        _prune_resource_refs(
            data, renderer.disabled_agents, renderer.disabled_tools
        )
        if path.name == "app.json":
            _apply_app_manifest_settings(
                data,
                brand,
                renderer.channel,
                channel_caps,
                renderer.disabled_agents,
            )

    rendered = _wrap_path_error(path, lambda: renderer.process_json(data))
    path.write_text(json.dumps(rendered, indent=2) + "\n", "utf-8")


def _render_text_file(
    path: Path,
    out_dir: Path,
    manifests: Manifests,
    renderer: _BuildRenderer,
) -> list[str]:
    text = path.read_text("utf-8")
    if path.name == "global_instruction.txt":
        identity_block = _build_identity_block(manifests, renderer.brand_id)
        if "@@brand.identity_block@@" in text:
            text = text.replace("@@brand.identity_block@@", identity_block)
        else:
            text = identity_block + "\n\n" + text

    text = _wrap_path_error(
        path, lambda: renderer.process_conditional_blocks(text)
    )
    rel_path = path.relative_to(out_dir)
    file_warnings = _collect_pruned_mentions(text, rel_path, renderer)
    text = _wrap_path_error(path, lambda: renderer.substitute_tokens(text))
    path.write_text(text, "utf-8")
    validate_modalities(text, rel_path)
    return file_warnings


def _parse_yaml_with_conditionals(
    path: Path, text: str, renderer: _BuildRenderer, *, has_tokens: bool
) -> JsonValue:
    # scbc boundary: evaluates conditionals then parses external YAML file
    try:
        if has_tokens and ("{@start" in text or "{@end" in text):
            text = renderer.process_conditional_blocks(text)
        return yaml.safe_load(text)
    except (yaml.YAMLError, BuildError) as exc:
        raise BuildError(f"{path}: {exc}") from exc


def _yaml_render_flags(
    path: Path, text: str, renderer: _BuildRenderer
) -> tuple[bool, bool, bool]:
    has_tokens = "@@" in text or "{@start" in text or "{@end" in text
    has_disabled = bool(renderer.disabled_agents or renderer.disabled_tools)
    needs_prune = has_disabled and ("childAgents" in text or "tools" in text)
    is_app_yaml = path.name in ("app.yaml", "app.yml")
    return has_tokens, needs_prune, is_app_yaml


def _apply_yaml_mutations(
    data: ConfigMap,
    renderer: _BuildRenderer,
    brand: BrandIdentity,
    channel_caps: ChannelCapabilities,
    *,
    is_app_yaml: bool,
) -> bool:
    pruned = _prune_resource_refs(
        data, renderer.disabled_agents, renderer.disabled_tools
    )
    app_updated = is_app_yaml and _apply_app_manifest_settings(
        data, brand, renderer.channel, channel_caps, renderer.disabled_agents
    )
    return pruned or app_updated


def _render_yaml_file(
    path: Path,
    renderer: _BuildRenderer,
    brand: BrandIdentity,
    channel_caps: ChannelCapabilities,
) -> None:
    try:
        text = path.read_text("utf-8")
    except UnicodeDecodeError as exc:
        raise BuildError(f"{path}: file is not valid UTF-8: {exc}") from exc

    has_tokens, needs_prune, is_app_yaml = _yaml_render_flags(
        path, text, renderer
    )
    if not (has_tokens or needs_prune or is_app_yaml):
        return

    data = _parse_yaml_with_conditionals(
        path, text, renderer, has_tokens=has_tokens
    )
    modified = isinstance(data, dict) and _apply_yaml_mutations(
        data, renderer, brand, channel_caps, is_app_yaml=is_app_yaml
    )
    if has_tokens:
        data = _wrap_path_error(
            path,
            lambda: renderer.process_json(data, evaluate_conditionals=False),
        )
    if has_tokens or modified:
        path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True), "utf-8"
        )


def _render_python_file(path: Path, renderer: _BuildRenderer) -> None:
    text = path.read_text("utf-8")
    if "@@" not in text and "{@start" not in text and "{@end" not in text:
        return
    rendered = _wrap_path_error(
        path, lambda: _render_python_source(text, path, renderer)
    )
    path.write_text(rendered, "utf-8")


def _render_single_file(
    path: Path,
    out_dir: Path,
    manifests: Manifests,
    renderer: _BuildRenderer,
    brand: BrandIdentity,
    channel_caps: ChannelCapabilities,
    env_overrides: Mapping[str, JsonValue],
) -> list[str]:
    suffix = path.suffix
    if suffix == ".json":
        _render_json_file(path, renderer, brand, channel_caps, env_overrides)
    elif suffix == ".txt":
        return _render_text_file(path, out_dir, manifests, renderer)
    elif suffix in (".yaml", ".yml"):
        _render_yaml_file(path, renderer, brand, channel_caps)
    elif suffix == ".py":
        _render_python_file(path, renderer)
    return []


def _render_staged_tree(
    out_dir: Path,
    manifests: Manifests,
    renderer: _BuildRenderer,
    brand: BrandIdentity,
    channel_caps: ChannelCapabilities,
    env_overrides: Mapping[str, JsonValue],
) -> list[str]:
    warnings: list[str] = []
    for root, dirs, files in os.walk(out_dir):
        dirs.sort()
        root_path = Path(root)
        for fname in sorted(files):
            warnings.extend(
                _render_single_file(
                    root_path / fname,
                    out_dir,
                    manifests,
                    renderer,
                    brand,
                    channel_caps,
                    env_overrides,
                )
            )
    return warnings


def _write_deployment_manifest(
    out_dir: Path,
    brand: BrandIdentity,
    policy: ChannelPolicy,
    channel: str,
    environment: str,
    channel_caps: ChannelCapabilities,
) -> str:
    deployments_dir = out_dir / "deployments"
    deployments_dir.mkdir(exist_ok=True)
    slug = channel.lower().replace("_", "-")
    dep_data: ConfigMap = {
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
    with open(deployments_dir / f"{slug}.yaml", "w", encoding="utf-8") as fh:
        yaml.safe_dump(dep_data, fh, sort_keys=False)
    return slug


def _build_app_from_manifests(
    core_dir: Path,
    manifests_path: Path,
    manifests: Manifests,
    brand_id: str,
    channel: str,
    environment: str,
    out_dir: Path,
    *,
    package_zip: bool = False,
) -> BuildReport:
    _validate_build_paths(core_dir, manifests_path, out_dir)
    brand = manifests.brand(brand_id)
    policy = manifests.policy(brand_id)
    env_manifest = manifests.env(environment)

    try:
        channel_caps = policy.channels[channel]
    except KeyError as exc:
        raise BuildError(
            f"Channel {channel} not permitted for brand {brand_id}."
        ) from exc

    _stage_core_dir(
        core_dir, out_dir, has_env_overrides=bool(env_manifest.overrides)
    )
    all_disabled_agents = _prune_resource_dir(
        out_dir / "agents", set(channel_caps.disabled_agents)
    )
    all_disabled_tools = _prune_resource_dir(
        out_dir / "tools", set(channel_caps.disabled_tools)
    )
    if (
        channel_caps.entry_agent
        and channel_caps.entry_agent in all_disabled_agents
    ):
        raise BuildError(
            f"Channel '{channel}' entry_agent '{channel_caps.entry_agent}' "
            "cannot also be in disabled_agents."
        )

    renderer = _BuildRenderer.from_context(
        brand,
        policy,
        env_manifest,
        channel,
        environment,
        all_disabled_agents,
        all_disabled_tools,
    )
    warnings = _render_staged_tree(
        out_dir,
        manifests,
        renderer,
        brand,
        channel_caps,
        env_manifest.overrides,
    )
    slug = _write_deployment_manifest(
        out_dir, brand, policy, channel, environment, channel_caps
    )

    zip_path: Path | None = None
    if package_zip:
        zip_path = out_dir / f"{brand_id}_{slug}.zip"
        try:
            package_app_archive(out_dir, zip_path)
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
    core_path = Path(core_dir).resolve()
    manifests_path = Path(manifests_dir).resolve()
    out_path = Path(out_dir).resolve()
    _validate_build_paths(core_path, manifests_path, out_path)
    manifests = load_manifests(manifests_path)
    return _build_app_from_manifests(
        core_path,
        manifests_path,
        manifests,
        brand_id,
        channel,
        environment,
        out_path,
        package_zip=package_zip,
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

    manifests_path = Path(manifests_dir).resolve()
    manifests = load_manifests(manifests_path)
    manifests.env(environment)

    out_root_path = Path(out_root).resolve()
    return [
        _build_app_from_manifests(
            core_path,
            manifests_path,
            manifests,
            brand_id,
            channel,
            environment,
            out_root_path / brand_id / channel.lower(),
            package_zip=package_zip,
        )
        for brand_id, policy in sorted(manifests.policies.items())
        for channel in sorted(policy.channels)
    ]
