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

"""Deterministic Profile-Based Build engine for CXAS."""

from __future__ import annotations

import ast
import contextlib
import copy
import functools
import io
import itertools
import json
import os
import re
import shutil
import tempfile
import tokenize
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeAlias, TypeVar

import yaml
from pydantic import BaseModel, Field

from cxas_scrapi.build.manifests import (
    APP_CONFIG_FILENAMES,
    CHANNEL_PROFILE_TYPES,
    FIELD_ENV_APP,
    ChannelCapabilities,
    ChannelPolicy,
    ContentBlockCatalog,
    EnvManifest,
    Manifests,
    ProfileIdentity,
    load_manifests,
    validate_app_overlay_mapping,
)
from cxas_scrapi.core.packaging import package_app_archive
from cxas_scrapi.utils.yaml_utils import (
    UniqueKeyYaml12SafeLoader,
    Yaml12SafeLoader,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_T = TypeVar("_T")
JsonValue: TypeAlias = Any
ConfigMap: TypeAlias = dict[str, JsonValue]
AppLayer: TypeAlias = tuple[str, Mapping[str, JsonValue]]

_KEY_ROOT_AGENT = "rootAgent"
_KEY_AUDIO_PROCESSING_CONFIG = "audioProcessingConfig"
_KEY_SYNTHESIZE_SPEECH_CONFIGS = "synthesizeSpeechConfigs"
_KEY_LANGUAGE_SETTINGS = "languageSettings"

_ALLOWED_ENV_TEMPLATE_VARS = frozenset(
    {"profile", "profile_slug", "env", "channel", "service"}
)
_ENV_TEMPLATE_BRACE_RE = re.compile(r"\{([^{}]*)\}")
_UNRESOLVED_TOKEN_RE = re.compile(r"@@[^@\s]+@@")
_DANGLING_TOKEN_RE = re.compile(
    r"@@(?:profile|env|routing|build)\.[A-Za-z0-9_.:-]+"
)
_COND_START_RE = re.compile(
    r"^\{@start(?!Modality_)([A-Za-z][A-Za-z0-9]*)_([^}]+)\}$"
)
_COND_END_RE = re.compile(
    r"^\{@end(?!Modality_)([A-Za-z][A-Za-z0-9]*)_([^}]+)\}$"
)
_COND_TOKEN_RE = re.compile(
    r"\{@(start|end)(?!Modality_)([A-Za-z][A-Za-z0-9]*)_([^}]+)\}"
)
_MODALITY_TAG_RE = re.compile(r"({@startModality_[^}]+}|{@endModality_[^}]+})")
_PY_STR_PREFIX_RE = re.compile(r"^([rRuUbBfF]*)('''|\"\"\"|'|\")")
_FSTRING_START = getattr(tokenize, "FSTRING_START", None)
_FSTRING_MIDDLE = getattr(tokenize, "FSTRING_MIDDLE", None)
_FSTRING_END = getattr(tokenize, "FSTRING_END", None)
_STAGE_IGNORE_PATTERNS = ("__pycache__", "*.pyc", ".DS_Store", "manifests")
_RESOURCE_CONFIG_SUFFIXES = (".json", ".yaml", ".yml")
_BUILD_MARKER_FILE = ".cxas-build-output"
_DYNAMIC_COND_KINDS = frozenset({"AgentEnabled", "ToolEnabled"})
_ILLEGAL_COND_ERROR_MARKERS = (
    "Conditional tag",
    "Unbalanced",
    "Unknown conditional",
)


class BuildError(Exception):
    """Raised when building a profile/channel app fails."""


class BuildReport(BaseModel):
    """Summary of one built profile/channel CES app."""

    profile_id: str
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


def _merge_app_entry(target: ConfigMap, key: str, val: JsonValue) -> None:
    if val is None:
        target.pop(key, None)
    elif isinstance(val, Mapping):
        existing = target.get(key)
        nested: ConfigMap = existing if isinstance(existing, dict) else {}
        target[key] = _merge_app_layer(nested, val)
    else:
        target[key] = copy.deepcopy(val)


def _merge_app_layer(
    target: ConfigMap, layer: Mapping[str, JsonValue]
) -> ConfigMap:
    for key, val in layer.items():
        _merge_app_entry(target, key, val)
    return target


def merge_app_config(
    base: Mapping[str, JsonValue],
    *layers: Mapping[str, JsonValue] | None,
) -> ConfigMap:
    """Deep-merge ordered ``app:`` overlay layers onto a deep copy of ``base``.

    Merge semantics (later layers win):
    - Nested mappings deep-merge recursively.
    - Lists replace wholesale (never concatenated).
    - Scalars replace.
    - Explicit ``None`` (YAML ``null``) deletes the key at any depth.
    """
    merged: ConfigMap = copy.deepcopy(dict(base))
    for layer in layers:
        if layer is not None:
            _merge_app_layer(merged, layer)
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


def _active_dynamic_kind(
    stack: list[tuple[str, str, bool]],
) -> tuple[bool, str | None]:
    static_active = True
    dyn_kind: str | None = None
    for kind, _, active in stack:
        if kind in _DYNAMIC_COND_KINDS:
            dyn_kind = kind
        elif not active:
            static_active = False
    return static_active, dyn_kind


def _raise_identity_cond_error(dyn_kind: str) -> None:
    raise BuildError(
        f"Conditional tag kind '{dyn_kind}' is not allowed inside "
        "resource identity fields (displayName/name); use Channel "
        "or Profile conditionals instead."
    )


def _extract_identity_pair(
    data: JsonValue,
) -> tuple[JsonValue, JsonValue]:
    if not isinstance(data, dict):
        return None, None
    return data.get("name"), data.get("displayName")


def _check_branch_duplicate_identity(branch_text: str, dyn_kind: str) -> None:
    try:
        yaml.load(branch_text, Loader=UniqueKeyYaml12SafeLoader)
    except yaml.constructor.ConstructorError as exc:
        msg = str(exc)
        if (
            "Duplicate mapping key 'displayName'" in msg
            or "Duplicate mapping key 'name'" in msg
        ):
            _raise_identity_cond_error(dyn_kind)
    except yaml.YAMLError:
        return


def _verify_single_dynamic_branch(
    base_ids: tuple[JsonValue, JsonValue],
    dyn_kind: str,
    branch_text: str,
) -> None:
    _check_branch_duplicate_identity(branch_text, dyn_kind)
    try:
        branch_data = yaml.load(branch_text, Loader=Yaml12SafeLoader)
    except yaml.YAMLError:
        _raise_identity_cond_error(dyn_kind)
    if not isinstance(branch_data, dict):
        _raise_identity_cond_error(dyn_kind)
    if _extract_identity_pair(branch_data) != base_ids:
        _raise_identity_cond_error(dyn_kind)


def _verify_all_on_dynamic_branch(
    base_ids: tuple[JsonValue, JsonValue],
    dyn_kind: str,
    all_text: str,
) -> None:
    _check_branch_duplicate_identity(all_text, dyn_kind)
    try:
        all_data = yaml.load(all_text, Loader=Yaml12SafeLoader)
    except yaml.YAMLError:
        return
    if (
        isinstance(all_data, dict)
        and _extract_identity_pair(all_data) != base_ids
    ):
        _raise_identity_cond_error(dyn_kind)


def _verify_dynamic_yaml_branches(
    base_data: JsonValue,
    branches: Sequence[tuple[str, str]],
    all_on: tuple[str, str] | None = None,
) -> None:
    base_ids = _extract_identity_pair(base_data)
    for dyn_kind, branch_text in branches:
        _verify_single_dynamic_branch(base_ids, dyn_kind, branch_text)
    if all_on is not None:
        _verify_all_on_dynamic_branch(base_ids, all_on[0], all_on[1])


def _build_dynamic_branches(
    collected: list[tuple[str, str | None, tuple[int, ...]]],
    block_kinds: list[str],
    block_ancestors: list[frozenset[int]],
    stripped: str,
) -> list[tuple[str, str]]:
    branches: list[tuple[str, str]] = []
    for bid, dyn_kind in enumerate(block_kinds):
        enabled = block_ancestors[bid]
        branch_text = "\n".join(
            line
            for line, _, dyn_ids in collected
            if all(d in enabled for d in dyn_ids)
        )
        if branch_text != stripped:
            branches.append((dyn_kind, branch_text))
    return branches


def _assert_cond_stack_empty(
    stack: list[tuple[str, str, bool]],
    *,
    inline_line_no: int | None = None,
) -> None:
    if not stack:
        return
    unclosed = [f"{{@start{k}_{a}}}" for k, a, _ in stack]
    if inline_line_no is not None:
        raise BuildError(
            f"Line {inline_line_no}: Unbalanced inline conditional tag(s): "
            f"{unclosed}"
        )
    raise BuildError(f"Unbalanced conditional tags remain: {unclosed}")


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

    profile_id: str
    channel: str
    disabled_agents: frozenset[str]
    disabled_tools: frozenset[str]
    replacements: dict[str, str]
    env_templates: tuple[tuple[re.Pattern[str], str], ...]
    cond_predicates: dict[str, Callable[[str], bool]]
    allow_resource_conditionals: bool = True

    @classmethod
    def from_context(
        cls,
        profile: ProfileIdentity,
        policy: ChannelPolicy,
        env_manifest: EnvManifest,
        channel: str,
        environment: str,
        disabled_agents: frozenset[str],
        disabled_tools: frozenset[str],
        *,
        allow_resource_conditionals: bool = True,
    ) -> _BuildRenderer:
        replacements: dict[str, str] = {
            "@@profile.profile_id@@": profile.profile_id,
            "@@profile.display_name@@": profile.display_name,
            "@@profile.assistant_title@@": profile.assistant_title or "",
            "@@profile.legal_entity@@": profile.legal_entity or "",
            "@@profile.tone_guidelines@@": profile.tone_guidelines or "",
            "@@build.channel@@": channel,
            "@@build.environment@@": environment,
            **{
                f"@@profile.lexicon.{k}@@": v
                for k, v in profile.lexicon.items()
            },
            **{f"@@profile.urls.{k}@@": v for k, v in profile.urls.items()},
            **{f"@@routing.{k}@@": v for k, v in policy.api_routing.items()},
        }
        template_values = {
            "profile": profile.profile_id,
            "profile_slug": profile.profile_id.replace("_", "-"),
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
            "Profile": lambda arg: arg == profile.profile_id,
            "AgentEnabled": lambda arg: arg not in disabled_agents,
            "ToolEnabled": lambda arg: arg not in disabled_tools,
        }
        return cls(
            profile_id=profile.profile_id,
            channel=channel,
            disabled_agents=disabled_agents,
            disabled_tools=disabled_tools,
            replacements=replacements,
            env_templates=tuple(env_templates),
            cond_predicates=predicates,
            allow_resource_conditionals=allow_resource_conditionals,
        )

    def _eval_cond(self, kind: str, arg: str) -> bool:
        if not self.allow_resource_conditionals and kind in (
            "AgentEnabled",
            "ToolEnabled",
        ):
            raise BuildError(
                f"Conditional tag kind '{kind}' is not allowed inside resource "
                "identity fields (displayName/name) or resource-config block "
                "conditionals; use Channel or Profile conditionals instead."
            )
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

        _assert_cond_stack_empty(inline_stack, inline_line_no=line_no)
        if not outer_active:
            return ""
        parts.append(line[pos:])
        return "".join(parts)

    def _handle_block_tag(
        self,
        stripped: str,
        line_no: int,
        stack: list[tuple[str, str, bool]],
        *,
        defer_dynamic: bool = False,
    ) -> bool:
        m_start = _COND_START_RE.match(stripped)
        if m_start:
            kind, arg = m_start.group(1), m_start.group(2)
            active = (
                False
                if defer_dynamic and kind in _DYNAMIC_COND_KINDS
                else self._eval_cond(kind, arg)
            )
            stack.append((kind, arg, active))
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

    def _track_dynamic_block_tag(
        self,
        stripped: str,
        line_no: int,
        stack: list[tuple[str, str, bool]],
        dyn_stack: list[int],
        block_kinds: list[str],
        block_ancestors: list[frozenset[int]],
    ) -> bool:
        prev_len = len(stack)
        prev_top_kind = stack[-1][0] if stack else ""
        if not self._handle_block_tag(
            stripped, line_no, stack, defer_dynamic=True
        ):
            return False
        if len(stack) > prev_len and stack[-1][0] in _DYNAMIC_COND_KINDS:
            bid = len(block_kinds)
            block_kinds.append(stack[-1][0])
            block_ancestors.append(frozenset(dyn_stack) | {bid})
            dyn_stack.append(bid)
        elif len(stack) < prev_len and prev_top_kind in _DYNAMIC_COND_KINDS:
            dyn_stack.pop()
        return True

    def preprocess_yaml_for_identifiers(
        self, content: str
    ) -> tuple[str, list[tuple[str, str]], tuple[str, str] | None]:
        """Evaluate static block conditionals and collect dynamic branches."""
        collected: list[tuple[str, str | None, tuple[int, ...]]] = []
        stack: list[tuple[str, str, bool]] = []
        dyn_stack: list[int] = []
        block_kinds: list[str] = []
        block_ancestors: list[frozenset[int]] = []
        for idx, line in enumerate(content.split("\n"), start=1):
            if self._track_dynamic_block_tag(
                line.strip(),
                idx,
                stack,
                dyn_stack,
                block_kinds,
                block_ancestors,
            ):
                continue
            static_active, dyn_kind = _active_dynamic_kind(stack)
            if static_active:
                collected.append((line, dyn_kind, tuple(dyn_stack)))
        _assert_cond_stack_empty(stack)
        stripped = "\n".join(
            line for line, dyn_kind, _ in collected if dyn_kind is None
        )
        branches = _build_dynamic_branches(
            collected, block_kinds, block_ancestors, stripped
        )
        all_on_text = "\n".join(line for line, _, _ in collected)
        all_on = (
            (block_kinds[0], all_on_text)
            if block_kinds and all_on_text != stripped
            else None
        )
        return stripped, branches, all_on

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

        _assert_cond_stack_empty(stack)
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

    def render_string(
        self, text: str, *, evaluate_conditionals: bool = True
    ) -> str:
        if evaluate_conditionals:
            text = self.process_conditional_blocks(text)
        return self.substitute_tokens(text)

    def _render_mapping(
        self,
        mapping: dict[JsonValue, JsonValue],
        *,
        evaluate_conditionals: bool,
    ) -> dict[JsonValue, JsonValue]:
        rendered: dict[JsonValue, JsonValue] = {}
        for k, v in mapping.items():
            rendered_k = (
                self.render_string(
                    k, evaluate_conditionals=evaluate_conditionals
                )
                if isinstance(k, str)
                else k
            )
            if rendered_k in rendered:
                raise BuildError(
                    f"Duplicate key '{rendered_k}' after placeholder "
                    "substitution."
                )
            rendered[rendered_k] = self.render_data(
                v, evaluate_conditionals=evaluate_conditionals
            )
        return rendered

    def render_data(
        self, val: JsonValue, *, evaluate_conditionals: bool = True
    ) -> JsonValue:
        if isinstance(val, dict):
            return self._render_mapping(
                val, evaluate_conditionals=evaluate_conditionals
            )
        if isinstance(val, list):
            return [
                self.render_data(
                    item, evaluate_conditionals=evaluate_conditionals
                )
                for item in val
            ]
        return (
            self.render_string(val, evaluate_conditionals=evaluate_conditionals)
            if isinstance(val, str)
            else val
        )


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
    profile_id: str,
    countries: Sequence[str],
    languages: Sequence[str],
) -> list[str]:
    if catalog is None:
        return []
    lines: list[str] = []
    for country in sorted(countries):
        for lang in sorted(languages):
            blocks = catalog.blocks_for(profile_id, country, lang)
            if blocks:
                lines.extend(
                    f"[{country}-{lang}] {bk}: {bv}"
                    for bk, bv in sorted(blocks.items())
                )
    return lines


def _build_identity_block(manifests: Manifests, profile_id: str) -> str:
    profile = manifests.profile(profile_id)
    policy = manifests.policy(profile_id)
    scalar_fields = (
        ("Title", profile.assistant_title),
        ("Legal Entity", profile.legal_entity),
        ("Tone", profile.tone_guidelines),
    )
    lines = [
        f"# Profile: {profile.display_name}",
        *(f"{label}: {val}" for label, val in scalar_fields if val),
        *_format_kv_section("Lexicon", profile.lexicon),
        *_format_kv_section("URLs", profile.urls),
        *_format_locale_blocks(
            manifests.content_blocks,
            profile_id,
            policy.supported_countries,
            profile.languages,
        ),
    ]
    return "\n".join(lines)


def _read_json_strict(path: Path) -> JsonValue:
    try:
        return json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise BuildError(f"{path}: invalid JSON: {exc}") from exc


def _read_yaml_with_renderer(path: Path, renderer: _BuildRenderer) -> JsonValue:
    try:
        text = path.read_text("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise BuildError(f"{path}: invalid YAML: {exc}") from exc
    has_cond = "{@start" in text or "{@end" in text
    if not renderer.allow_resource_conditionals and has_cond:
        try:
            stripped, branches, all_on = (
                renderer.preprocess_yaml_for_identifiers(text)
            )
            base_data = yaml.load(stripped, Loader=UniqueKeyYaml12SafeLoader)
            _verify_dynamic_yaml_branches(base_data, branches, all_on)
            return base_data
        except (yaml.YAMLError, BuildError) as exc:
            raise BuildError(f"{path}: {exc}") from exc
    return _parse_yaml_with_conditionals(
        path, text, renderer, has_tokens=has_cond
    )


def _render_optional_field(
    data: Mapping[str, JsonValue],
    key: str,
    cfg_path: Path,
    renderer: _BuildRenderer,
) -> str:
    raw = data.get(key)
    if not isinstance(raw, str) or not raw:
        return ""
    return _wrap_path_error(
        cfg_path, functools.partial(renderer.render_string, raw)
    )


def _add_config_identifiers(
    data: Mapping[str, JsonValue],
    cfg_path: Path,
    renderer: _BuildRenderer,
    ids: set[str],
) -> None:
    if display_name := _render_optional_field(
        data, "displayName", cfg_path, renderer
    ):
        ids.add(display_name)
    if resource_name := _render_optional_field(
        data, "name", cfg_path, renderer
    ):
        ids.add(resource_name)
        if "/" in resource_name and (tail := resource_name.rsplit("/", 1)[-1]):
            ids.add(tail)


def _read_resource_identifiers(
    resource_dir: Path, renderer: _BuildRenderer
) -> tuple[bool, set[str]]:
    """Read identifiers from ``<id>.{json,yaml,yml}`` if present."""
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
        return False, set()
    data = (
        _read_json_strict(cfg_path)
        if cfg_path.suffix == ".json"
        else _read_yaml_with_renderer(cfg_path, renderer)
    )
    ids: set[str] = {resource_dir.name}
    if isinstance(data, dict):
        _add_config_identifiers(data, cfg_path, renderer, ids)
    return True, ids


def _reject_symlinks(root: Path, label: str) -> None:
    if root.is_symlink():
        raise BuildError(f"Symlinks are not permitted in {label}: {root}")
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        for name in (*sorted(dirnames), *sorted(filenames)):
            entry = base / name
            if entry.is_symlink():
                raise BuildError(
                    f"Symlinks are not permitted in {label}: {entry}"
                )


def _validate_output_target(
    raw_out: Path, label: str, *, require_marker: bool = False
) -> Path:
    if raw_out.is_symlink():
        raise BuildError(f"Symlinks are not permitted for {label}: {raw_out}")
    resolved = raw_out.resolve()
    if resolved.exists() and not resolved.is_dir():
        raise BuildError(
            f"Output {label} '{resolved}' already exists and is not a "
            "directory."
        )
    if (
        require_marker
        and resolved.exists()
        and any(resolved.iterdir())
        and (
            (marker := resolved / _BUILD_MARKER_FILE).is_symlink()
            or not marker.is_file()
        )
    ):
        raise BuildError(
            f"Output directory '{resolved}' is non-empty and was not created "
            f"by 'cxas build' (missing '{_BUILD_MARKER_FILE}'). "
            "Refusing to overwrite unrelated files."
        )
    return resolved


def _resolve_input_dirs(
    core_dir: str | Path, manifests_dir: str | Path
) -> tuple[Path, Path]:
    raw_core = Path(core_dir)
    raw_manifests = Path(manifests_dir)
    _reject_symlinks(raw_core, "core directory")
    _reject_symlinks(raw_manifests, "manifests directory")
    return raw_core.resolve(), raw_manifests.resolve()


def _validate_build_paths(
    core_dir: Path, manifests_path: Path, out_dir: Path
) -> None:
    if not core_dir.is_dir():
        msg = (
            f"Core directory '{core_dir}' does not exist or is not a directory."
        )
        raise BuildError(msg)
    _validate_output_target(out_dir, "directory", require_marker=True)
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
    core_dir: Path, staging_dir: Path, *, has_env_overrides: bool
) -> None:
    shutil.copytree(
        core_dir,
        staging_dir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(*_STAGE_IGNORE_PATTERNS),
    )
    # Seed environment.json so env_manifest.overrides flows through the normal
    # merge, pruning, and token-substitution pass.
    if has_env_overrides and not (staging_dir / "environment.json").exists():
        (staging_dir / "environment.json").write_text("{}\n", "utf-8")


def _swap_staging_dir(staging_dir: Path, out_dir: Path) -> None:
    backup_dir: Path | None = None
    if out_dir.exists():
        backup_dir = Path(
            tempfile.mkdtemp(prefix=f".{out_dir.name}.bak-", dir=out_dir.parent)
        )
        backup_dir.rmdir()
        out_dir.rename(backup_dir)
    try:
        staging_dir.rename(out_dir)
    except BaseException:
        if backup_dir and backup_dir.exists() and not out_dir.exists():
            backup_dir.rename(out_dir)
        raise
    if backup_dir and backup_dir.exists():
        shutil.rmtree(backup_dir)


def _matches_resource_ref(
    ref: JsonValue, names: frozenset[str], collection: str
) -> bool:
    if not isinstance(ref, str):
        return False
    if ref in names:
        return True
    prefix = f"{collection}/"
    if ref.startswith(prefix) and ref[len(prefix) :] in names:
        return True
    marker = f"/{collection}/"
    return marker in ref and ref.rsplit("/", 1)[-1] in names


def _entry_matches_disabled(
    item: JsonValue, disabled: frozenset[str], collection: str
) -> bool:
    if isinstance(item, dict):
        return _matches_resource_ref(
            item.get("displayName"), disabled, collection
        ) or _matches_resource_ref(item.get("name"), disabled, collection)
    return _matches_resource_ref(item, disabled, collection)


def _is_resource_config_file(rel_path: Path, collection: str) -> bool:
    return (
        len(rel_path.parts) == 3
        and rel_path.parts[0] == collection
        and rel_path.stem == rel_path.parts[1]
        and rel_path.suffix in _RESOURCE_CONFIG_SUFFIXES
    )


def _assert_rendered_resource_not_disabled(
    data: JsonValue, rel_path: Path, renderer: _BuildRenderer
) -> None:
    if not isinstance(data, Mapping):
        return
    if _is_resource_config_file(rel_path, "agents"):
        collection, disabled = "agents", renderer.disabled_agents
    elif _is_resource_config_file(rel_path, "tools"):
        collection, disabled = "tools", renderer.disabled_tools
    else:
        return
    if not disabled:
        return
    for ident in _extract_raw_identifiers(data):
        if _matches_resource_ref(ident, disabled, collection):
            raise BuildError(
                f"{rel_path}: rendered resource identity '{ident}' matches a "
                f"disabled {collection[:-1]} for channel '{renderer.channel}'."
            )


def _extract_raw_identifiers(data: Mapping[str, JsonValue]) -> set[str]:
    ids: set[str] = set()
    display_name = data.get("displayName")
    if isinstance(display_name, str) and display_name:
        ids.add(display_name)
    resource_name = data.get("name")
    if isinstance(resource_name, str) and resource_name:
        ids.add(resource_name)
        if "/" in resource_name and (tail := resource_name.rsplit("/", 1)[-1]):
            ids.add(tail)
    return ids


def _read_raw_config_identifiers(cfg_path: Path) -> set[str]:
    try:
        text = cfg_path.read_text("utf-8")
        data = (
            json.loads(text)
            if cfg_path.suffix == ".json"
            else yaml.load(text, Loader=UniqueKeyYaml12SafeLoader)
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError):
        return set()
    return _extract_raw_identifiers(data) if isinstance(data, dict) else set()


def _matches_any_disabled(
    entry_ids: frozenset[str],
    disabled_set: frozenset[str],
    collection: str,
) -> bool:
    if any(
        _matches_resource_ref(ident, disabled_set, collection)
        for ident in entry_ids
    ):
        return True
    return any(
        _matches_resource_ref(ref, entry_ids, collection)
        for ref in disabled_set
    )


def _inspect_prunable_entry(
    entry: Path,
    disabled_set: frozenset[str],
    collection: str,
    renderer: _BuildRenderer,
) -> tuple[bool, set[str]]:
    raw_ids: set[str] = {entry.name}
    for suffix in _RESOURCE_CONFIG_SUFFIXES:
        candidate = entry / f"{entry.name}{suffix}"
        if candidate.is_file():
            raw_ids.update(_read_raw_config_identifiers(candidate))
            break
    if _matches_any_disabled(frozenset(raw_ids), disabled_set, collection):
        try:
            _, rendered_ids = _read_resource_identifiers(entry, renderer)
        except BuildError as exc:
            if any(m in str(exc) for m in _ILLEGAL_COND_ERROR_MARKERS):
                raise
            rendered_ids = set()
        return True, raw_ids | rendered_ids

    _, rendered_ids = _read_resource_identifiers(entry, renderer)
    all_ids = raw_ids | rendered_ids
    return (
        _matches_any_disabled(frozenset(all_ids), disabled_set, collection),
        all_ids,
    )


def _register_unique_identifiers(
    owner_by_id: dict[str, str],
    entry_name: str,
    entry_ids: set[str],
    collection: str,
) -> None:
    for ident in sorted(entry_ids):
        if "@@" in ident or "{@" in ident:
            continue
        prev_owner = owner_by_id.get(ident)
        if prev_owner is not None and prev_owner != entry_name:
            raise BuildError(
                f"Ambiguous {collection} identifier '{ident}' is shared by "
                f"'{prev_owner}' and '{entry_name}'; resource folder names, "
                "names, and displayNames must be unique within "
                f"'{collection}/'."
            )
        owner_by_id[ident] = entry_name


def _prune_resource_dir(
    resource_dir: Path,
    disabled: set[str],
    collection: str,
    renderer: _BuildRenderer,
) -> frozenset[str]:
    pruned: set[str] = set(disabled)
    if not resource_dir.exists():
        return frozenset(pruned)
    disabled_set = frozenset(disabled)
    owner_by_id: dict[str, str] = {}
    to_prune: list[tuple[Path, set[str]]] = []
    for entry in sorted(p for p in resource_dir.iterdir() if p.is_dir()):
        matched, entry_ids = _inspect_prunable_entry(
            entry, disabled_set, collection, renderer
        )
        _register_unique_identifiers(
            owner_by_id, entry.name, entry_ids, collection
        )
        if matched:
            to_prune.append((entry, entry_ids))
    for entry, entry_ids in to_prune:
        pruned.update(entry_ids)
        shutil.rmtree(entry)
    return frozenset(pruned)


def _prune_all_resources(
    staging_dir: Path,
    profile: ProfileIdentity,
    policy: ChannelPolicy,
    env_manifest: EnvManifest,
    channel: str,
    environment: str,
    channel_caps: ChannelCapabilities,
) -> tuple[frozenset[str], frozenset[str]]:
    prelim_renderer = _BuildRenderer.from_context(
        profile,
        policy,
        env_manifest,
        channel,
        environment,
        frozenset(),
        frozenset(),
        allow_resource_conditionals=False,
    )
    disabled_tools = _prune_resource_dir(
        staging_dir / "tools",
        set(channel_caps.disabled_tools),
        "tools",
        prelim_renderer,
    )
    disabled_agents = _prune_resource_dir(
        staging_dir / "agents",
        set(channel_caps.disabled_agents),
        "agents",
        prelim_renderer,
    )
    return disabled_agents, disabled_tools


def _collect_surviving_agent_names(
    agents_dir: Path, renderer: _BuildRenderer
) -> frozenset[str]:
    if not agents_dir.is_dir():
        return frozenset()
    names: set[str] = set()
    for entry in sorted(p for p in agents_dir.iterdir() if p.is_dir()):
        has_cfg, ids = _read_resource_identifiers(entry, renderer)
        if has_cfg:
            names.update(ids)
    return frozenset(names)


def _validate_entry_agent(
    entry_agent: str | None,
    channel: str,
    disabled_agents: frozenset[str],
    surviving_agents: frozenset[str],
) -> None:
    if not entry_agent:
        return
    if _matches_resource_ref(entry_agent, disabled_agents, "agents"):
        reason = "cannot also be in disabled_agents."
    elif not _matches_resource_ref(entry_agent, surviving_agents, "agents"):
        reason = "does not match any surviving agent under 'agents/'."
    else:
        return
    raise BuildError(
        f"Channel '{channel}' entry_agent '{entry_agent}' {reason}"
    )


def _prune_mapping_key(
    data: ConfigMap,
    key: str,
    disabled: frozenset[str],
    collection: str,
    *,
    allow_dict: bool = False,
) -> bool:
    value = data.get(key)
    if isinstance(value, list):
        filtered = [
            item
            for item in value
            if not _entry_matches_disabled(item, disabled, collection)
        ]
    elif allow_dict and isinstance(value, dict):
        filtered = {
            k: v
            for k, v in value.items()
            if not _matches_resource_ref(k, disabled, collection)
        }
    else:
        return False

    if len(filtered) == len(value):
        return False
    data[key] = filtered
    return True


def _prune_agent_config_refs(
    data: JsonValue,
    disabled_agents: frozenset[str],
    disabled_tools: frozenset[str],
) -> bool:
    if not isinstance(data, dict):
        return False
    pruned_agents = _prune_mapping_key(
        data, "childAgents", disabled_agents, "agents"
    )
    pruned_tools = _prune_mapping_key(
        data, "tools", disabled_tools, "tools", allow_dict=True
    )
    return pruned_agents or pruned_tools


def _ensure_dict_section(data: ConfigMap, key: str) -> ConfigMap:
    section = data.get(key)
    if section is None:
        section = {}
        data[key] = section
        return section
    if not isinstance(section, dict):
        raise BuildError(
            f"Expected '{key}' to be an object/mapping, got "
            f"{type(section).__name__}."
        )
    return section


def _validate_existing_root_agent(
    current_root: JsonValue,
    owner_label: str,
    channel: str,
    disabled_agents: frozenset[str],
    surviving_agents: frozenset[str],
) -> None:
    if not isinstance(current_root, str) or not current_root.strip():
        raise BuildError(
            f"{owner_label}: rootAgent cannot be empty or null for channel "
            f"'{channel}' when no replacement entry_agent is configured."
        )
    if _matches_resource_ref(current_root, disabled_agents, "agents"):
        raise BuildError(
            f"{owner_label}: rootAgent '{current_root}' is disabled for "
            f"channel '{channel}' and no replacement entry_agent is configured."
        )
    if not _matches_resource_ref(current_root, surviving_agents, "agents"):
        raise BuildError(
            f"{owner_label}: rootAgent '{current_root}' does not match any "
            f"surviving agent for channel '{channel}'."
        )


def _validate_final_root_agent(
    data: Mapping[str, JsonValue],
    core_app_path: Path,
    channel: str,
    entry_agent: str | None,
    disabled_agents: frozenset[str],
    surviving_agents: frozenset[str],
    root_agent_source: tuple[str, bool] | None,
) -> None:
    if entry_agent:
        return
    if root_agent_source is not None:
        layer_source, was_nulled = root_agent_source
        owner_label = f"{layer_source} (merged into {core_app_path.name})"
    else:
        owner_label = str(core_app_path)
        was_nulled = False
    if was_nulled:
        raise BuildError(
            f"{owner_label}: rootAgent cannot be null for channel "
            f"'{channel}' when no replacement entry_agent is configured."
        )
    if _KEY_ROOT_AGENT in data:
        _validate_existing_root_agent(
            data[_KEY_ROOT_AGENT],
            owner_label,
            channel,
            disabled_agents,
            surviving_agents,
        )
    elif surviving_agents:
        raise BuildError(
            f"{owner_label}: App config is missing 'rootAgent' for "
            f"channel '{channel}'."
        )


def _update_root_agent(data: ConfigMap, entry_agent: str | None) -> bool:
    if not entry_agent:
        return False
    changed = data.get(_KEY_ROOT_AGENT) != entry_agent
    data[_KEY_ROOT_AGENT] = entry_agent
    return changed


def _apply_profile_sections(data: ConfigMap, profile: ProfileIdentity) -> bool:
    updated = False
    if profile.tts_voices:
        audio_cfg = _ensure_dict_section(data, _KEY_AUDIO_PROCESSING_CONFIG)
        audio_cfg[_KEY_SYNTHESIZE_SPEECH_CONFIGS] = {
            lang: {"voice": tts.voice, "speakingRate": tts.speaking_rate}
            for lang, tts in profile.tts_voices.items()
        }
        updated = True

    if profile.languages:
        lang_cfg = _ensure_dict_section(data, _KEY_LANGUAGE_SETTINGS)
        lang_cfg.update(
            {
                "defaultLanguageCode": profile.languages[0],
                "supportedLanguageCodes": profile.languages,
                "enableMultilingualSupport": len(profile.languages) > 1,
            }
        )
        updated = True
    return updated


def _apply_app_manifest_settings(
    data: ConfigMap,
    profile: ProfileIdentity,
    channel_caps: ChannelCapabilities,
) -> bool:
    updated = _update_root_agent(data, channel_caps.entry_agent)
    return _apply_profile_sections(data, profile) or updated


def _apply_app_layers(
    data: ConfigMap,
    app_layers: Sequence[AppLayer],
    renderer: _BuildRenderer,
) -> tuple[ConfigMap, tuple[str, bool] | None]:
    merged = data
    root_agent_source: tuple[str, bool] | None = None
    for source_label, layer in app_layers:
        rendered_layer = _wrap_path_error(
            Path(source_label),
            functools.partial(
                renderer.render_data, layer, evaluate_conditionals=True
            ),
        )
        try:
            validate_app_overlay_mapping(rendered_layer)
        except ValueError as exc:
            raise BuildError(f"{source_label}: {exc}") from exc
        if _KEY_ROOT_AGENT in rendered_layer:
            root_agent_source = (
                source_label,
                rendered_layer[_KEY_ROOT_AGENT] is None,
            )
        merged = merge_app_config(merged, rendered_layer)
    return merged, root_agent_source


def _finalize_app_config(
    data: ConfigMap,
    app_layers: Sequence[AppLayer],
    renderer: _BuildRenderer,
    profile: ProfileIdentity,
    channel_caps: ChannelCapabilities,
    *,
    rel_name: str = "",
    core_app_snapshots: dict[str, ConfigMap] | None = None,
    root_agent_sources: dict[str, tuple[str, bool]] | None = None,
) -> tuple[ConfigMap, bool]:
    if core_app_snapshots is not None and rel_name:
        core_app_snapshots[rel_name] = copy.deepcopy(data)
    layered = bool(app_layers)
    if app_layers:
        data, root_agent_source = _apply_app_layers(data, app_layers, renderer)
        if (
            root_agent_sources is not None
            and rel_name
            and root_agent_source is not None
        ):
            root_agent_sources[rel_name] = root_agent_source
    updated = _apply_app_manifest_settings(data, profile, channel_caps)
    return data, layered or updated


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


def _prune_env_json(data: ConfigMap, renderer: _BuildRenderer) -> None:
    _prune_mapping_key(
        data, "agents", renderer.disabled_agents, "agents", allow_dict=True
    )
    _prune_mapping_key(
        data, "tools", renderer.disabled_tools, "tools", allow_dict=True
    )


def _render_json_file(
    path: Path,
    out_dir: Path,
    renderer: _BuildRenderer,
    profile: ProfileIdentity,
    channel_caps: ChannelCapabilities,
    env_overrides: Mapping[str, JsonValue],
    app_layers: Sequence[AppLayer] = (),
    core_app_snapshots: dict[str, ConfigMap] | None = None,
    root_agent_sources: dict[str, tuple[str, bool]] | None = None,
) -> None:
    rel_path = path.relative_to(out_dir)
    is_env_json = rel_path == Path("environment.json")
    data = _read_json_strict(path)
    if is_env_json and isinstance(data, dict) and env_overrides:
        data = deep_merge(data, env_overrides)

    rendered = _wrap_path_error(path, lambda: renderer.render_data(data))
    if is_env_json and isinstance(rendered, dict):
        _prune_env_json(rendered, renderer)
    elif _is_resource_config_file(rel_path, "agents"):
        _prune_agent_config_refs(
            rendered, renderer.disabled_agents, renderer.disabled_tools
        )
    _assert_rendered_resource_not_disabled(rendered, rel_path, renderer)

    if rel_path == Path("app.json") and isinstance(rendered, dict):
        rendered, _ = _finalize_app_config(
            rendered,
            app_layers,
            renderer,
            profile,
            channel_caps,
            rel_name=rel_path.name,
            core_app_snapshots=core_app_snapshots,
            root_agent_sources=root_agent_sources,
        )
    path.write_text(json.dumps(rendered, indent=2) + "\n", "utf-8")


def _render_text_file(
    path: Path,
    out_dir: Path,
    manifests: Manifests,
    renderer: _BuildRenderer,
) -> list[str]:
    text = path.read_text("utf-8")
    if path.name == "global_instruction.txt":
        identity_block = _build_identity_block(manifests, renderer.profile_id)
        if "@@profile.identity_block@@" in text:
            text = text.replace("@@profile.identity_block@@", identity_block)
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
    try:
        if has_tokens and ("{@start" in text or "{@end" in text):
            text = renderer.process_conditional_blocks(text)
        return yaml.load(text, Loader=UniqueKeyYaml12SafeLoader)
    except (yaml.YAMLError, BuildError) as exc:
        raise BuildError(f"{path}: {exc}") from exc


def _yaml_render_flags(
    rel_path: Path, text: str, renderer: _BuildRenderer
) -> tuple[bool, bool, bool, bool]:
    has_tokens = "@@" in text or "{@start" in text or "{@end" in text
    is_agent_cfg = _is_resource_config_file(rel_path, "agents")
    has_disabled = bool(renderer.disabled_agents or renderer.disabled_tools)
    needs_prune = (
        is_agent_cfg
        and has_disabled
        and ("childAgents" in text or "tools" in text)
    )
    is_app_yaml = rel_path in (Path("app.yaml"), Path("app.yml"))
    return has_tokens, is_agent_cfg, needs_prune, is_app_yaml


def _apply_yaml_mutations(
    data: JsonValue,
    renderer: _BuildRenderer,
    profile: ProfileIdentity,
    channel_caps: ChannelCapabilities,
    app_layers: Sequence[AppLayer],
    *,
    is_agent_cfg: bool,
    is_app_yaml: bool,
    rel_name: str,
    core_app_snapshots: dict[str, ConfigMap] | None = None,
    root_agent_sources: dict[str, tuple[str, bool]] | None = None,
) -> tuple[JsonValue, bool]:
    pruned = is_agent_cfg and _prune_agent_config_refs(
        data, renderer.disabled_agents, renderer.disabled_tools
    )
    if is_app_yaml and data is None:
        data = {}
    if not (is_app_yaml and isinstance(data, dict)):
        return data, pruned
    data, app_updated = _finalize_app_config(
        data,
        app_layers,
        renderer,
        profile,
        channel_caps,
        rel_name=rel_name,
        core_app_snapshots=core_app_snapshots,
        root_agent_sources=root_agent_sources,
    )
    return data, pruned or app_updated


def _render_yaml_file(
    path: Path,
    out_dir: Path,
    renderer: _BuildRenderer,
    profile: ProfileIdentity,
    channel_caps: ChannelCapabilities,
    app_layers: Sequence[AppLayer] = (),
    core_app_snapshots: dict[str, ConfigMap] | None = None,
    root_agent_sources: dict[str, tuple[str, bool]] | None = None,
) -> None:
    try:
        text = path.read_text("utf-8")
    except UnicodeDecodeError as exc:
        raise BuildError(f"{path}: file is not valid UTF-8: {exc}") from exc

    rel_path = path.relative_to(out_dir)
    has_tokens, is_agent_cfg, needs_prune, is_app_yaml = _yaml_render_flags(
        rel_path, text, renderer
    )
    if not (has_tokens or needs_prune or is_app_yaml):
        return

    data = _parse_yaml_with_conditionals(
        path, text, renderer, has_tokens=has_tokens
    )
    if has_tokens:
        data = _wrap_path_error(
            path,
            lambda: renderer.render_data(data, evaluate_conditionals=False),
        )
    _assert_rendered_resource_not_disabled(data, rel_path, renderer)
    data, modified = _apply_yaml_mutations(
        data,
        renderer,
        profile,
        channel_caps,
        app_layers,
        is_agent_cfg=is_agent_cfg,
        is_app_yaml=is_app_yaml,
        rel_name=rel_path.name,
        core_app_snapshots=core_app_snapshots,
        root_agent_sources=root_agent_sources,
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
    profile: ProfileIdentity,
    channel_caps: ChannelCapabilities,
    env_overrides: Mapping[str, JsonValue],
    app_layers: Sequence[AppLayer] = (),
    core_app_snapshots: dict[str, ConfigMap] | None = None,
    root_agent_sources: dict[str, tuple[str, bool]] | None = None,
) -> list[str]:
    suffix = path.suffix
    if suffix == ".json":
        _render_json_file(
            path,
            out_dir,
            renderer,
            profile,
            channel_caps,
            env_overrides,
            app_layers,
            core_app_snapshots,
            root_agent_sources,
        )
    elif suffix == ".txt":
        return _render_text_file(path, out_dir, manifests, renderer)
    elif suffix in (".yaml", ".yml"):
        _render_yaml_file(
            path,
            out_dir,
            renderer,
            profile,
            channel_caps,
            app_layers,
            core_app_snapshots,
            root_agent_sources,
        )
    elif suffix == ".py":
        _render_python_file(path, renderer)
    return []


def _render_staged_tree(
    out_dir: Path,
    manifests: Manifests,
    renderer: _BuildRenderer,
    profile: ProfileIdentity,
    channel_caps: ChannelCapabilities,
    env_overrides: Mapping[str, JsonValue],
    app_layers: Sequence[AppLayer] = (),
    core_app_snapshots: dict[str, ConfigMap] | None = None,
    root_agent_sources: dict[str, tuple[str, bool]] | None = None,
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
                    profile,
                    channel_caps,
                    env_overrides,
                    app_layers,
                    core_app_snapshots,
                    root_agent_sources,
                )
            )
    return warnings


def _read_app_config_for_validation(app_cfg_path: Path) -> JsonValue:
    try:
        return (
            _read_json_strict(app_cfg_path)
            if app_cfg_path.suffix == ".json"
            else yaml.load(
                app_cfg_path.read_text("utf-8"),
                Loader=UniqueKeyYaml12SafeLoader,
            )
        )
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise BuildError(f"{app_cfg_path}: invalid YAML: {exc}") from exc


def _compute_base_app_errors(
    core_snapshot: ConfigMap,
    staging_dir: Path,
    profile: ProfileIdentity,
    channel_caps: ChannelCapabilities,
) -> set[str]:
    base_data = copy.deepcopy(core_snapshot)
    if channel_caps.entry_agent:
        base_data[_KEY_ROOT_AGENT] = channel_caps.entry_agent
    with contextlib.suppress(BuildError):
        _apply_profile_sections(base_data, profile)
    from cxas_scrapi.utils.lint_rules.schema import (  # noqa: PLC0415
        validate_app_config_data,
    )

    return set(validate_app_config_data(base_data, staging_dir))


def _format_app_validation_error(
    errors: list[str],
    core_app_path: Path,
    source_label: str,
    base_errors: set[str] | None,
) -> str:
    if base_errors is None:
        return f"{core_app_path}: invalid App config: {'; '.join(errors)}"
    core_errs = [err for err in errors if err in base_errors]
    overlay_errs = [err for err in errors if err not in base_errors]
    parts: list[str] = []
    if core_errs:
        parts.append(
            f"{core_app_path}: invalid App config: {'; '.join(core_errs)}"
        )
    if overlay_errs:
        parts.append(
            f"{source_label} (merged into {core_app_path.name}): invalid "
            f"App config: {'; '.join(overlay_errs)}"
        )
    return "; ".join(parts)


def _validate_single_app_config(
    app_cfg_path: Path,
    core_dir: Path,
    staging_dir: Path,
    source_label: str,
    *,
    has_app_layers: bool,
    core_snapshot: ConfigMap | None,
    profile: ProfileIdentity,
    channel: str,
    channel_caps: ChannelCapabilities,
    disabled_agents: frozenset[str],
    surviving_agents: frozenset[str],
    root_agent_source: tuple[str, bool] | None,
) -> None:
    data = _read_app_config_for_validation(app_cfg_path)
    if isinstance(data, Mapping):
        from cxas_scrapi.utils.lint_rules.schema import (  # noqa: PLC0415
            validate_app_config_data,
        )

        errors = validate_app_config_data(data, staging_dir)
    else:
        errors = [f"expected a mapping, got {type(data).__name__}"]
    if errors:
        base_errors = (
            _compute_base_app_errors(
                core_snapshot, staging_dir, profile, channel_caps
            )
            if has_app_layers and core_snapshot is not None
            else None
        )
        raise BuildError(
            _format_app_validation_error(
                errors, core_dir / app_cfg_path.name, source_label, base_errors
            )
        )
    if isinstance(data, Mapping):
        _validate_final_root_agent(
            data,
            core_dir / app_cfg_path.name,
            channel,
            channel_caps.entry_agent,
            disabled_agents,
            surviving_agents,
            root_agent_source,
        )


def _validate_final_app_configs(
    core_dir: Path,
    staging_dir: Path,
    source_label: str,
    *,
    has_app_layers: bool,
    core_app_snapshots: Mapping[str, ConfigMap],
    profile: ProfileIdentity,
    channel: str,
    channel_caps: ChannelCapabilities,
    disabled_agents: frozenset[str],
    surviving_agents: frozenset[str],
    root_agent_sources: Mapping[str, tuple[str, bool]],
) -> None:
    app_cfg_paths = [
        candidate
        for fname in APP_CONFIG_FILENAMES
        if (candidate := staging_dir / fname).is_file()
    ]
    if not app_cfg_paths:
        if has_app_layers:
            names = ", ".join(APP_CONFIG_FILENAMES)
            raise BuildError(
                f"{source_label}: '{FIELD_ENV_APP}:' overrides configured, "
                f"but no app config ({names}) found in core directory."
            )
        return
    for app_cfg_path in app_cfg_paths:
        _validate_single_app_config(
            app_cfg_path,
            core_dir,
            staging_dir,
            source_label,
            has_app_layers=has_app_layers,
            core_snapshot=core_app_snapshots.get(app_cfg_path.name),
            profile=profile,
            channel=channel,
            channel_caps=channel_caps,
            disabled_agents=disabled_agents,
            surviving_agents=surviving_agents,
            root_agent_source=root_agent_sources.get(app_cfg_path.name),
        )


def _write_deployment_manifest(
    out_dir: Path,
    profile: ProfileIdentity,
    policy: ChannelPolicy,
    channel: str,
    environment: str,
    channel_caps: ChannelCapabilities,
) -> str:
    deployments_dir = out_dir / "deployments"
    deployments_dir.mkdir(exist_ok=True)
    slug = channel.lower().replace("_", "-")
    dep_data: ConfigMap = {
        "displayName": f"{profile.display_name} {channel}",
        "profileId": profile.profile_id,
        "channel": channel,
        "environment": environment,
        "channelProfile": {"channelType": CHANNEL_PROFILE_TYPES[channel]},
        "supportedCountries": policy.supported_countries,
        "supportedLanguages": profile.languages,
        "supportedModalities": channel_caps.supported_modalities,
        "deeplinkWidgets": channel_caps.deeplink_widgets,
    }
    if channel_caps.entry_agent:
        dep_data["entryAgent"] = channel_caps.entry_agent
    with open(deployments_dir / f"{slug}.yaml", "w", encoding="utf-8") as fh:
        yaml.safe_dump(dep_data, fh, sort_keys=False, allow_unicode=True)
    return slug


def _build_in_staging(
    core_dir: Path,
    staging_dir: Path,
    manifests: Manifests,
    profile: ProfileIdentity,
    policy: ChannelPolicy,
    env_manifest: EnvManifest,
    channel: str,
    environment: str,
    channel_caps: ChannelCapabilities,
    *,
    package_zip: bool,
) -> tuple[list[str], str]:
    _stage_core_dir(
        core_dir, staging_dir, has_env_overrides=bool(env_manifest.overrides)
    )
    all_disabled_agents, all_disabled_tools = _prune_all_resources(
        staging_dir,
        profile,
        policy,
        env_manifest,
        channel,
        environment,
        channel_caps,
    )
    renderer = _BuildRenderer.from_context(
        profile,
        policy,
        env_manifest,
        channel,
        environment,
        all_disabled_agents,
        all_disabled_tools,
    )
    surviving_agents = _collect_surviving_agent_names(
        staging_dir / "agents", renderer
    )
    _validate_entry_agent(
        channel_caps.entry_agent, channel, all_disabled_agents, surviving_agents
    )

    env_source_label = str(manifests.root / "envs" / f"{environment}.yaml")
    app_layers: tuple[AppLayer, ...] = (
        ((env_source_label, env_manifest.app),)
        if env_manifest.app is not None
        else ()
    )
    core_app_snapshots: dict[str, ConfigMap] = {}
    root_agent_sources: dict[str, tuple[str, bool]] = {}
    warnings = _render_staged_tree(
        staging_dir,
        manifests,
        renderer,
        profile,
        channel_caps,
        env_manifest.overrides,
        app_layers,
        core_app_snapshots,
        root_agent_sources,
    )
    _validate_final_app_configs(
        core_dir,
        staging_dir,
        env_source_label,
        has_app_layers=bool(app_layers),
        core_app_snapshots=core_app_snapshots,
        profile=profile,
        channel=channel,
        channel_caps=channel_caps,
        disabled_agents=all_disabled_agents,
        surviving_agents=surviving_agents,
        root_agent_sources=root_agent_sources,
    )
    slug = _write_deployment_manifest(
        staging_dir, profile, policy, channel, environment, channel_caps
    )
    (staging_dir / _BUILD_MARKER_FILE).write_text(
        "cxas-build\n", encoding="utf-8"
    )
    if package_zip:
        staged_zip = staging_dir / f"{profile.profile_id}_{slug}.zip"
        try:
            package_app_archive(
                staging_dir, staged_zip, source_app_dir=core_dir
            )
        except (OSError, ValueError) as exc:
            raise BuildError(f"Failed to package {staged_zip}: {exc}") from exc
    return warnings, slug


def _build_app_from_manifests(
    core_dir: Path,
    manifests_path: Path,
    manifests: Manifests,
    profile_id: str,
    channel: str,
    environment: str,
    out_dir: Path,
    *,
    package_zip: bool = False,
) -> BuildReport:
    _validate_build_paths(core_dir, manifests_path, out_dir)
    profile = manifests.profile(profile_id)
    policy = manifests.policy(profile_id)
    env_manifest = manifests.env(environment)

    try:
        channel_caps = policy.channels[channel]
    except KeyError as exc:
        raise BuildError(
            f"Channel {channel} not permitted for profile {profile_id}."
        ) from exc

    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(prefix=f".{out_dir.name}.tmp-", dir=out_dir.parent)
    )
    try:
        warnings, slug = _build_in_staging(
            core_dir,
            staging_dir,
            manifests,
            profile,
            policy,
            env_manifest,
            channel,
            environment,
            channel_caps,
            package_zip=package_zip,
        )
        _swap_staging_dir(staging_dir, out_dir)
    except BaseException:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    zip_path = out_dir / f"{profile_id}_{slug}.zip" if package_zip else None
    return BuildReport(
        profile_id=profile_id,
        channel=channel,
        environment=environment,
        out_dir=out_dir,
        zip_path=zip_path,
        warnings=warnings,
    )


def build_app(
    core_dir: str | Path,
    manifests_dir: str | Path,
    profile_id: str,
    channel: str,
    environment: str,
    out_dir: str | Path,
    *,
    package_zip: bool = False,
) -> BuildReport:
    """Build one CES app for ``(profile_id, channel, environment)``."""
    core_path, manifests_path = _resolve_input_dirs(core_dir, manifests_dir)
    out_path = _validate_output_target(
        Path(out_dir), "output directory", require_marker=True
    )
    _validate_build_paths(core_path, manifests_path, out_path)
    manifests = load_manifests(manifests_path)
    return _build_app_from_manifests(
        core_path,
        manifests_path,
        manifests,
        profile_id,
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
    """Build every ``(profile_id, channel)`` pair declared in manifests."""
    core_path, manifests_path = _resolve_input_dirs(core_dir, manifests_dir)
    if not core_path.is_dir():
        raise BuildError(
            f"Core directory '{core_path}' does not exist or is not a "
            "directory."
        )

    out_root_path = _validate_output_target(Path(out_root), "output root")
    manifests = load_manifests(manifests_path)
    manifests.env(environment)

    reports: list[BuildReport] = []
    for profile_id, policy in sorted(manifests.policies.items()):
        profile_out_dir = _validate_output_target(
            out_root_path / profile_id, "profile output directory"
        )
        for channel in sorted(policy.channels):
            channel_out_dir = _validate_output_target(
                profile_out_dir / channel.lower(),
                "output directory",
                require_marker=True,
            )
            reports.append(
                _build_app_from_manifests(
                    core_path,
                    manifests_path,
                    manifests,
                    profile_id,
                    channel,
                    environment,
                    channel_out_dir,
                    package_zip=package_zip,
                )
            )
    return reports
