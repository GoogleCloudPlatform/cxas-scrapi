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

"""Console UI -> Multi-Brand Build back-port (`--target brand|core`)."""

from __future__ import annotations

import ast
import contextlib
import copy
import difflib
import io
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tokenize
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

import yaml

import cxas_scrapi.utils.linter as _linter_mod
from cxas_scrapi.build.builder import (
    BuildError,
    _build_identity_block,
    _paths_overlap,
    _reject_symlinks,
    _render_env_template,
    _render_python_source,
    build_all,
    build_app,
)
from cxas_scrapi.build.bundle_io import (
    BuildGateError,
    DriftGateError,
    NormalizedResource,
    assert_safe_subpath,
    load_json_dict_strict,
    load_mapping_strict,
    read_utf8_strict,
)
from cxas_scrapi.build.drift import (
    _OPENAPI_SERVER_URL_RE,
    _canonical_openapi_schema_lines,
    _find_config_file,
    _has_app_marker,
    _load_environment_tool_urls,
    _normalize_app_obj,
    _resolve_app_bundle_dir,
    _resolve_resource_selectors,
    check_drift,
    extract_normalized_resources,
    normalize_json_obj,
    write_lockfile,
)
from cxas_scrapi.build.manifests import (
    LineReplacement,
    ManifestError,
    Manifests,
    load_manifests,
)
from cxas_scrapi.build.overrides import (
    CES_ENV_VAR_PLACEHOLDER,
    apply_append_rules_lines,
    apply_line_replacements_strict,
)
from cxas_scrapi.core.python_bundler import (
    PythonBundlerError,
    strip_shared_bundle_text,
)
from cxas_scrapi.utils.yaml_utils import Yaml12SafeLoader

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

BackportTarget = Literal["brand", "core"]
BackportScope = Literal["channel", "brand"]

_GIT_BRANCH_RE = re.compile(r"^[a-zA-Z0-9._/-]+$")
_PLACEHOLDER_TOKEN_RE = re.compile(r"(@@[^@\s]+@@)")
_BRAND_SCOPED_TOKEN_RE = re.compile(
    r"^@@(profile\.lexicon|profile\.urls|routing)\.([^@]+)@@$"
)
_COND_START_RE = re.compile(
    r"^\{@start(Channel|Profile|AgentEnabled|ToolEnabled)_([^}]+)\}$"
)
_COND_END_RE = re.compile(
    r"^\{@end(Channel|Profile|AgentEnabled|ToolEnabled)_([^}]+)\}$"
)
_COND_INLINE_RE = re.compile(
    r"\{@start(Channel|Profile|AgentEnabled|ToolEnabled)_([^}]+)\}"
    r"([^\n]*?)"
    r"\{@end\1_\2\}"
)
_TOOL_PAYLOAD_KEYS = ("openApiTool", "dataStoreTool", "pythonFunction")
_PY_STR_PREFIX_RE = re.compile(r"^([rRuUbBfF]*)('''|\"\"\"|'|\")")
_FSTRING_MIDDLE = getattr(tokenize, "FSTRING_MIDDLE", None)


class _CleanYamlDumper(yaml.SafeDumper):
    """YAML dumper using literal block scalars (`|`) for multi-line text."""


def _str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_CleanYamlDumper.add_representer(str, _str_representer)


@dataclass
class BackportChange:
    """Single logical resource delta back-ported into brand manifests."""

    resource_id: str
    target_file: str
    summary: str

    def to_dict(self) -> dict[str, str]:
        return {
            "resource_id": self.resource_id,
            "summary": self.summary,
            "target_file": self.target_file,
        }


@dataclass
class BackportResult:
    """Summary, lint report, round-trip proof, and PR body for back-porting."""

    app_id: str
    brand_id: str
    channel: str
    environment: str
    target: BackportTarget
    scope: BackportScope
    dry_run: bool = False
    modified_files: list[Path] = field(default_factory=list)
    changes: list[BackportChange] = field(default_factory=list)
    verified_bundles: list[str] = field(default_factory=list)
    unchanged_sibling_bundles: list[str] = field(default_factory=list)
    lint_errors: int = 0
    lint_warnings: int = 0
    lint_ran: bool = True
    git_branch: str | None = None
    lockfile_path: Path | None = None
    pr_body_markdown: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "app_id": self.app_id,
            "brand_id": self.brand_id,
            "changes": [c.to_dict() for c in self.changes],
            "channel": self.channel,
            "dry_run": self.dry_run,
            "environment": self.environment,
            "git_branch": self.git_branch,
            "lint_errors": self.lint_errors,
            "lint_ran": self.lint_ran,
            "lint_warnings": self.lint_warnings,
            "lockfile_path": (
                str(self.lockfile_path) if self.lockfile_path else None
            ),
            "modified_files": [str(p) for p in self.modified_files],
            "pr_body_markdown": self.pr_body_markdown,
            "scope": self.scope,
            "target": self.target,
            "unchanged_sibling_bundles": list(self.unchanged_sibling_bundles),
            "verified_bundles": list(self.verified_bundles),
        }


def _dump_yaml_clean(path: Path, payload: Mapping[str, Any]) -> None:
    if path.is_symlink():
        raise BuildGateError(f"Refusing to write through symlink: {path}")
    rendered = yaml.dump(
        dict(payload),
        Dumper=_CleanYamlDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=100,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")


def _load_yaml_dict_optional(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise BuildGateError(f"Symlinks are not permitted in manifests: {path}")
    if not path.is_file():
        return {}
    try:
        data = yaml.load(
            path.read_text(encoding="utf-8"), Loader=Yaml12SafeLoader
        )
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise BuildGateError(f"Failed to parse YAML {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise BuildGateError(f"Expected mapping at root of {path}")
    return data


def _safe_load_yaml_text(text: str, *, context: str) -> Any:
    """Parse YAML text, converting parser errors into `BuildGateError`."""
    try:
        return yaml.load(text, Loader=Yaml12SafeLoader)
    except yaml.YAMLError as exc:
        raise BuildGateError(f"Invalid YAML in {context}: {exc}") from exc


def _read_optional_text(path: Path, *, context: str = "file") -> str:
    """Read an optional UTF-8 file (e.g. `instruction.txt`); '' if absent."""
    if path.is_symlink():
        raise BuildGateError(f"Symlinks are not permitted in {context}: {path}")
    if not path.is_file():
        return ""
    return read_utf8_strict(path, context=context)


def _prune_empty_dict(d: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, dict):
            sub = _prune_empty_dict(v)
            if sub:
                cleaned[k] = sub
        elif isinstance(v, list):
            if v:
                cleaned[k] = v
        else:
            cleaned[k] = v
    return cleaned


def _validate_dir_no_symlinks_gate(root_dir: Path, *, label: str) -> None:
    try:
        _reject_symlinks(root_dir, label)
    except BuildError as exc:
        raise BuildGateError(str(exc)) from exc


def _resolve_agent_instruction_path(
    bundle_dir: Path, agent_id: str, agent_cfg: Mapping[str, Any]
) -> Path:
    """Resolve an agent's instruction path safely inside ``bundle_dir``."""
    ag_dir = bundle_dir / "agents" / agent_id
    raw_instr = agent_cfg.get("instruction")
    if isinstance(raw_instr, str) and raw_instr.strip():
        rel_instr = raw_instr.strip()
        cand = bundle_dir / rel_instr
        if not cand.is_file() and (
            ("/" not in rel_instr and "\\" not in rel_instr)
            or (ag_dir / rel_instr).is_file()
        ):
            cand = ag_dir / rel_instr
        assert_safe_subpath(
            cand, bundle_dir, label=f"agents/{agent_id} instruction"
        )
        return cand
    default_path = ag_dir / "instruction.txt"
    if default_path.exists() or default_path.is_symlink():
        assert_safe_subpath(
            default_path, bundle_dir, label=f"agents/{agent_id} instruction"
        )
    return default_path


def _load_bundle_app_config(bundle_dir: Path, *, label: str) -> dict[str, Any]:
    cfg_path = _find_config_file(bundle_dir, "app", bundle_dir, label=label)
    if cfg_path is None:
        raise BuildGateError(
            f"App directory is missing app.json/app.yaml: {bundle_dir}"
        )
    return load_mapping_strict(cfg_path, context=label)


def _load_bundle_resource_config(
    bundle_dir: Path, subdir: str, resource_id: str, *, label: str
) -> dict[str, Any]:
    res_dir = bundle_dir / subdir / resource_id
    cfg_path = _find_config_file(res_dir, resource_id, bundle_dir, label=label)
    if cfg_path is None:
        raise BuildGateError(
            f"Missing resource config under {res_dir} for {label}"
        )
    return load_mapping_strict(cfg_path, context=label)


def _make_token_renderer(
    manifests: Manifests,
    brand_id: str,
    channel: str,
    environment: str,
) -> Callable[[str], str]:
    brand = manifests.profile(brand_id)
    policy = manifests.policy(brand_id)
    env_manifest = manifests.env(environment)

    env_values = {
        "profile": brand.profile_id,
        "profile_slug": brand.profile_id.replace("_", "-"),
        "env": environment,
        "channel": channel,
        "service": "{service}",
    }

    replacements: dict[str, str] = {
        "@@profile.profile_id@@": brand.profile_id,
        "@@profile.display_name@@": brand.display_name,
        "@@profile.assistant_title@@": brand.assistant_title or "",
        "@@profile.legal_entity@@": brand.legal_entity or "",
        "@@profile.tone_guidelines@@": brand.tone_guidelines or "",
        "@@build.channel@@": channel,
        "@@build.environment@@": environment,
    }
    for k, v in brand.lexicon.items():
        replacements[f"@@profile.lexicon.{k}@@"] = v
    for k, v in brand.urls.items():
        replacements[f"@@profile.urls.{k}@@"] = v
    for k, v in policy.api_routing.items():
        replacements[f"@@routing.{k}@@"] = v

    rendered_env_templates: list[tuple[str, re.Pattern[str], str]] = []
    for k, v in env_manifest.templates.items():
        try:
            v_rendered = _render_env_template(k, v, env_values)
        except BuildError as exc:
            raise BuildGateError(str(exc)) from exc
        replacements[f"@@env.{k}@@"] = v_rendered.replace("{service}", "")
        rendered_env_templates.append(
            (k, re.compile(rf"@@env\.{re.escape(k)}:([^@]+)@@"), v_rendered)
        )

    def _render(s: str) -> str:
        for ek, pat, base_rendered in rendered_env_templates:

            def _repl(m: re.Match[str], rendered: str = base_rendered) -> str:
                return rendered.replace("{service}", m.group(1))

            s = pat.sub(_repl, s)
            s = s.replace(
                f"@@env.{ek}@@", base_rendered.replace("{service}", "")
            )
        for rep_k, rep_v in replacements.items():
            if rep_k in s:
                s = s.replace(rep_k, rep_v)
        return s

    return _render


def _build_reverse_token_map(
    manifests: Manifests,
    brand_id: str,
    channel: str,
    environment: str,
) -> tuple[
    list[tuple[str, str]],
    list[tuple[re.Pattern[str], str]],
    Callable[[str], str],
]:
    brand = manifests.profile(brand_id)
    policy = manifests.policy(brand_id)
    env_manifest = manifests.env(environment)
    render_fn = _make_token_renderer(manifests, brand_id, channel, environment)

    env_values = {
        "profile": brand.profile_id,
        "profile_slug": brand.profile_id.replace("_", "-"),
        "env": environment,
        "channel": channel,
        "service": "{service}",
    }

    raw_candidates: list[tuple[str, str, int]] = []
    if brand.tone_guidelines:
        raw_candidates.append(
            (brand.tone_guidelines, "@@profile.tone_guidelines@@", 0)
        )
    for k, v in sorted(brand.lexicon.items()):
        raw_candidates.append((v, f"@@profile.lexicon.{k}@@", 0))
    if brand.legal_entity:
        raw_candidates.append(
            (brand.legal_entity, "@@profile.legal_entity@@", 1)
        )
    if brand.assistant_title:
        raw_candidates.append(
            (brand.assistant_title, "@@profile.assistant_title@@", 2)
        )
    for k, v in sorted(brand.urls.items()):
        raw_candidates.append((v, f"@@profile.urls.{k}@@", 2))
    for k, v in sorted(policy.api_routing.items()):
        raw_candidates.append((v, f"@@routing.{k}@@", 2))

    svc_patterns: list[tuple[re.Pattern[str], str]] = []
    for ek, ev in sorted(env_manifest.templates.items()):
        try:
            base_rendered = _render_env_template(ek, ev, env_values)
        except BuildError as exc:
            raise BuildGateError(str(exc)) from exc
        if "{service}" in ev:
            parts = base_rendered.split("{service}")
            if len(parts) == 2 and (parts[0] or parts[1]):
                regex_str = (
                    f"{re.escape(parts[0])}"
                    r"(?P<svc>[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)"
                    f"{re.escape(parts[1])}"
                )
                svc_patterns.append((re.compile(regex_str), ek))
        else:
            raw_candidates.append((base_rendered, f"@@env.{ek}@@", 1))

    raw_candidates.append((brand.display_name, "@@profile.display_name@@", 3))

    filtered: list[tuple[str, str, int]] = []
    for val, tok, prio in raw_candidates:
        stripped = val.strip()
        if len(stripped) < 4 or stripped.isdigit():
            continue
        filtered.append((val, tok, prio))

    filtered.sort(key=lambda item: (-len(item[0]), item[2], item[1]))
    seen_vals: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for val, tok, _prio in filtered:
        if val not in seen_vals:
            seen_vals.add(val)
            deduped.append((val, tok))

    return deduped, svc_patterns, render_fn


def _replace_outside_placeholders(text: str, val: str, tok: str) -> str:
    """Replace `val` with `tok` only outside already-emitted `@@...@@`."""
    parts = _PLACEHOLDER_TOKEN_RE.split(text)
    return "".join(
        part if idx % 2 else part.replace(val, tok)
        for idx, part in enumerate(parts)
    )


def _reverse_tokens(
    text: str,
    rev_pairs: Sequence[tuple[str, str]],
    svc_patterns: Sequence[tuple[re.Pattern[str], str]],
    render_fn: Callable[[str], str],
    *,
    extra_pairs: Sequence[tuple[str, str]] = (),
) -> str:
    current = text
    for pat, ek in svc_patterns:
        if not pat.search(current):
            continue
        cand = pat.sub(
            lambda m, key=ek: f"@@env.{key}:{m.group('svc')}@@", current
        )
        if render_fn(cand) == text:
            current = cand

    for val, tok in [*rev_pairs, *extra_pairs]:
        if val not in current:
            continue
        cand = _replace_outside_placeholders(current, val, tok)
        if cand == current or render_fn(cand) != text:
            continue
        current = cand
    return current


def _reverse_url_tokens(
    url_text: str,
    brand_id: str,
    rev_pairs: Sequence[tuple[str, str]],
    svc_patterns: Sequence[tuple[re.Pattern[str], str]],
    render_fn: Callable[[str], str],
) -> str:
    extra: list[tuple[str, str]] = []
    if len(brand_id) >= 4 and not brand_id.isdigit():
        extra.append((brand_id, "@@profile.profile_id@@"))
    return _reverse_tokens(
        url_text, rev_pairs, svc_patterns, render_fn, extra_pairs=extra
    )


def _token_defined_for_all_brands(manifests: Manifests, tok: str) -> bool:
    """Return True when every built brand can resolve ``tok``."""
    m_tok = _BRAND_SCOPED_TOKEN_RE.match(tok)
    if m_tok is None:
        return True
    kind, key = m_tok.group(1), m_tok.group(2)
    for b_id in sorted(manifests.policies):
        if kind == "profile.lexicon":
            defined = key in manifests.profile(b_id).lexicon
        elif kind == "profile.urls":
            defined = key in manifests.profile(b_id).urls
        else:
            defined = key in manifests.policy(b_id).api_routing
        if not defined:
            return False
    return True


def _extract_line_replacement_pairs(
    base_lines: list[str],
    live_lines: list[str],
) -> list[tuple[str, str]] | None:
    opcodes = difflib.SequenceMatcher(
        None, base_lines, live_lines, autojunk=False
    ).get_opcodes()
    non_equal = [op for op in opcodes if op[0] != "equal"]
    if not non_equal or len(non_equal) > 8:
        return None

    used_base_indices: set[int] = set()
    pairs: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in non_equal:
        if tag == "replace":
            span_set = set(range(i1, i2))
            if span_set & used_base_indices:
                return None
            used_base_indices.update(span_set)
            pairs.append(
                ("\n".join(base_lines[i1:i2]), "\n".join(live_lines[j1:j2]))
            )
        elif tag == "delete":
            span_set = set(range(i1, i2))
            if span_set & used_base_indices:
                return None
            used_base_indices.update(span_set)
            pairs.append(("\n".join(base_lines[i1:i2]), ""))
        elif tag == "insert":
            if i1 > 0 and (i1 - 1) not in used_base_indices:
                anchor = base_lines[i1 - 1]
                used_base_indices.add(i1 - 1)
                pairs.append((anchor, "\n".join([anchor, *live_lines[j1:j2]])))
            elif i1 == 0 and base_lines and 0 not in used_base_indices:
                anchor = base_lines[0]
                used_base_indices.add(0)
                pairs.append((anchor, "\n".join([*live_lines[j1:j2], anchor])))
            else:
                return None
    if any(not m_str.strip() for m_str, _w_str in pairs):
        return None
    return pairs


def _compute_brand_instruction_delta(
    base_instr: str,
    live_instr: str,
    *,
    scope: BackportScope,
    sibling_baselines: Sequence[tuple[str, Callable[[str], str]]],
    rev_pairs: Sequence[tuple[str, str]],
    svc_patterns: Sequence[tuple[re.Pattern[str], str]],
    render_fn: Callable[[str], str],
    identity_prefix: str | None = None,
) -> tuple[str, Any]:
    live_norm = live_instr.replace("\r\n", "\n")
    if live_norm and not live_norm.endswith("\n"):
        live_norm += "\n"
    base_norm = base_instr.replace("\r\n", "\n")
    if base_norm and not base_norm.endswith("\n"):
        base_norm += "\n"

    base_body = base_norm.rstrip("\n")
    live_body = live_norm.rstrip("\n")
    base_lines = base_body.split("\n") if base_body else []
    live_lines = live_body.split("\n") if live_body else []

    def _rev(s: str) -> str:
        return _reverse_tokens(s, rev_pairs, svc_patterns, render_fn)

    # 1. Pure trailing append
    if (
        base_lines
        and len(live_lines) > len(base_lines)
        and live_lines[: len(base_lines)] == base_lines
    ):
        cand_rules = [_rev(ln) for ln in live_lines[len(base_lines) :]]
        sim_app = apply_append_rules_lines(
            base_norm, cand_rules, render_fn=render_fn
        )
        if sim_app == live_norm:
            return "append_rules", cand_rules

    # 2. Unique line replacements
    raw_pairs = _extract_line_replacement_pairs(base_lines, live_lines)
    if raw_pairs:
        try:
            rev_specs = [
                LineReplacement(match=_rev(m_str), replace_with=_rev(w_str))
                for m_str, w_str in raw_pairs
            ]
        except ValueError:
            rev_specs = []
        if rev_specs:
            try:
                sim_rep = apply_line_replacements_strict(
                    base_norm,
                    rev_specs,
                    render_fn=render_fn,
                    context_label="backport-replace-sim",
                )
            except BuildError:
                sim_rep = ""

            if sim_rep == live_norm:
                siblings_ok = True
                if scope == "brand":
                    for sib_base, sib_render in sibling_baselines:
                        try:
                            apply_line_replacements_strict(
                                sib_base,
                                rev_specs,
                                render_fn=sib_render,
                                context_label="backport-sibling-sim",
                            )
                        except BuildError:
                            siblings_ok = False
                            break
                if siblings_ok:
                    return (
                        "replace_lines",
                        [
                            {"match": s.match, "with": s.replace_with}
                            for s in rev_specs
                        ],
                    )

    # 2b. Combined line replacements + trailing append_rules
    opcodes = difflib.SequenceMatcher(
        None, base_lines, live_lines, autojunk=False
    ).get_opcodes()
    if (
        opcodes
        and opcodes[-1][0] == "insert"
        and opcodes[-1][1] == len(base_lines)
    ):
        j1_tail = opcodes[-1][3]
        prefix_pairs = _extract_line_replacement_pairs(
            base_lines, live_lines[:j1_tail]
        )
        tail_rules = [_rev(ln) for ln in live_lines[j1_tail:]]
        if prefix_pairs and tail_rules:
            try:
                pref_specs = [
                    LineReplacement(match=_rev(m_str), replace_with=_rev(w_str))
                    for m_str, w_str in prefix_pairs
                ]
            except ValueError:
                pref_specs = []
            if pref_specs:
                try:
                    step1 = apply_line_replacements_strict(
                        base_norm,
                        pref_specs,
                        render_fn=render_fn,
                        context_label="backport-combo-rep",
                    )
                    sim_combo = apply_append_rules_lines(
                        step1, tail_rules, render_fn=render_fn
                    )
                except BuildError:
                    sim_combo = ""

                if sim_combo == live_norm:
                    combo_ok = True
                    if scope == "brand":
                        for sib_base, sib_render in sibling_baselines:
                            try:
                                s1 = apply_line_replacements_strict(
                                    sib_base,
                                    pref_specs,
                                    render_fn=sib_render,
                                    context_label="backport-combo-sib",
                                )
                                apply_append_rules_lines(
                                    s1, tail_rules, render_fn=sib_render
                                )
                            except BuildError:
                                combo_ok = False
                                break
                    if combo_ok:
                        return (
                            "replace_and_append",
                            (
                                [
                                    {"match": s.match, "with": s.replace_with}
                                    for s in pref_specs
                                ],
                                tail_rules,
                            ),
                        )

    # 3. Full instruction fallback (preserves trailing blank lines verbatim so
    # the canonical `splitlines()` round-trip matches live exactly).
    fallback_body = live_body
    trailing_nl = live_norm[len(live_body) :] or "\n"
    if identity_prefix and fallback_body.startswith(identity_prefix):
        after = fallback_body[len(identity_prefix) :]
        if after.startswith("\n\n"):
            fallback_body = after[2:]
        else:
            fallback_body = after.lstrip("\n")
    return "instruction", _rev(fallback_body) + trailing_nl


def _load_normalized_tool(
    bundle_dir: Path, tool_name: str, res_id: str
) -> tuple[dict[str, Any], list[str] | None, str | None]:
    norm_tj = normalize_json_obj(
        _load_bundle_resource_config(
            bundle_dir, "tools", tool_name, label=f"{res_id} config"
        ),
        is_root=True,
    )
    env_tool_urls = _load_environment_tool_urls(bundle_dir)
    oa_path: Path | None = None
    oa_cfg_raw = norm_tj.get("openApiTool")
    if isinstance(oa_cfg_raw, dict):
        raw_schema = oa_cfg_raw.get("openApiSchema")
        if isinstance(raw_schema, str) and raw_schema.strip():
            cand = bundle_dir / raw_schema.strip()
            if cand.exists() or cand.is_symlink():
                assert_safe_subpath(
                    cand, bundle_dir, label=f"{res_id} OpenAPI schema"
                )
                if cand.is_file():
                    oa_path = cand
    if oa_path is None:
        for fname in ("open_api_schema.yaml", "open_api_schema.yml"):
            cand = bundle_dir / "tools" / tool_name / "open_api_tool" / fname
            if cand.exists() or cand.is_symlink():
                assert_safe_subpath(
                    cand, bundle_dir, label=f"{res_id} OpenAPI schema"
                )
                if cand.is_file():
                    oa_path = cand
                    break

    oa_raw = (
        read_utf8_strict(oa_path, context=f"{res_id} OpenAPI schema").replace(
            "\r\n", "\n"
        )
        if oa_path is not None
        else None
    )
    oa_canon = (
        _canonical_openapi_schema_lines(oa_raw) if oa_raw is not None else None
    )

    oa_cfg = norm_tj.get("openApiTool")
    if isinstance(oa_cfg, dict) and "url" not in oa_cfg:
        env_url = env_tool_urls.get(
            str(norm_tj.get("displayName", tool_name)),
            env_tool_urls.get(tool_name),
        )
        if env_url is not None:
            oa_cfg["url"] = env_url
        elif oa_raw is not None:
            oa_doc = _safe_load_yaml_text(
                oa_raw, context=f"{res_id} OpenAPI schema"
            )
            servers = (
                oa_doc.get("servers") if isinstance(oa_doc, dict) else None
            )
            paths = oa_doc.get("paths") if isinstance(oa_doc, dict) else None
            if (
                isinstance(servers, list)
                and servers
                and isinstance(servers[0], dict)
                and isinstance(paths, dict)
            ):
                srv_url = servers[0].get("url")
                if (
                    isinstance(srv_url, str)
                    and srv_url
                    and srv_url != CES_ENV_VAR_PLACEHOLDER
                ):
                    if len(paths) == 1:
                        ep_path = str(next(iter(paths.keys())))
                        oa_cfg["url"] = (
                            f"{srv_url.rstrip('/')}/{ep_path.lstrip('/')}"
                        )
                    else:
                        oa_cfg["url"] = srv_url
    return norm_tj, oa_canon, oa_raw


def _validate_openapi_schema_compat(
    res_id: str,
    ours_oa_canon: list[str] | None,
    live_oa_canon: list[str] | None,
    ours_oa_raw: str | None,
    live_oa_raw: str | None,
    *,
    new_url: str | None = None,
    new_desc: str | None = None,
    allow_schema_desc_update: bool = False,
    error_suffix: str,
) -> None:
    if ours_oa_canon == live_oa_canon:
        return
    if ours_oa_raw is None or live_oa_raw is None:
        raise BuildGateError(
            f"Cannot backport structural OpenAPI schema change on "
            f"{res_id}{error_suffix}"
        )
    ours_doc = _safe_load_yaml_text(
        ours_oa_raw, context=f"{res_id} OpenAPI schema (source)"
    )
    live_doc = _safe_load_yaml_text(
        live_oa_raw, context=f"{res_id} OpenAPI schema (live)"
    )
    if not isinstance(ours_doc, dict) or not isinstance(live_doc, dict):
        raise BuildGateError(
            f"Cannot backport structural OpenAPI schema change on "
            f"{res_id}{error_suffix}"
        )
    exp_doc = copy.deepcopy(ours_doc)
    live_cmp = copy.deepcopy(live_doc)
    exp_doc["servers"] = [{"url": CES_ENV_VAR_PLACEHOLDER}]
    live_cmp["servers"] = [{"url": CES_ENV_VAR_PLACEHOLDER}]

    if (
        new_url is not None
        and isinstance(exp_doc.get("paths"), dict)
        and len(exp_doc["paths"]) == 1
    ):
        new_ep = urlsplit(new_url).path or "/"
        old_ep = next(iter(exp_doc["paths"].keys()))
        exp_doc["paths"] = {new_ep: exp_doc["paths"][old_ep]}

    if exp_doc == live_cmp:
        return

    if allow_schema_desc_update and new_desc is not None:
        exp_desc_doc = copy.deepcopy(exp_doc)
        if isinstance(exp_desc_doc.get("info"), dict):
            exp_desc_doc["info"]["description"] = new_desc
        if (
            isinstance(exp_desc_doc.get("paths"), dict)
            and len(exp_desc_doc["paths"]) == 1
        ):
            ops = next(iter(exp_desc_doc["paths"].values()))
            if isinstance(ops, dict) and len(ops) == 1:
                op_obj = next(iter(ops.values()))
                if isinstance(op_obj, dict):
                    op_obj["summary"] = new_desc
                    op_obj["description"] = new_desc
        if exp_desc_doc == live_cmp:
            return

    raise BuildGateError(
        f"Cannot backport structural OpenAPI schema change on "
        f"{res_id}{error_suffix}"
    )


def _tool_payload(tj: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the tool sub-object carrying `description`/`url` fields."""
    for sub_k in _TOOL_PAYLOAD_KEYS:
        if isinstance(tj.get(sub_k), dict):
            return tj[sub_k]
    return tj


def _extract_tool_json_deltas(
    base_tj: dict[str, Any],
    live_tj: dict[str, Any],
    *,
    res_id: str,
    error_suffix: str,
) -> tuple[str | None, str | None]:
    changed_top = {
        k
        for k in set(base_tj) | set(live_tj)
        if base_tj.get(k) != live_tj.get(k)
    }
    if not changed_top:
        return None, None

    if changed_top == {"description"}:
        return str(live_tj.get("description", "")), None

    if changed_top == {"openApiTool"} and isinstance(
        live_tj.get("openApiTool"), dict
    ):
        o_b = dict(base_tj.get("openApiTool") or {})
        l_b = dict(live_tj["openApiTool"])
        sub_diff = {k for k in set(o_b) | set(l_b) if o_b.get(k) != l_b.get(k)}
        if sub_diff.issubset({"description", "url"}):
            new_desc = (
                str(l_b.get("description", ""))
                if "description" in sub_diff
                else None
            )
            new_url = str(l_b.get("url", "")) if "url" in sub_diff else None
            return new_desc, new_url

    elif changed_top == {"dataStoreTool"} and isinstance(
        live_tj.get("dataStoreTool"), dict
    ):
        o_b = dict(base_tj.get("dataStoreTool") or {})
        l_b = dict(live_tj["dataStoreTool"])
        sub_diff = {k for k in set(o_b) | set(l_b) if o_b.get(k) != l_b.get(k)}
        if sub_diff.issubset({"description"}):
            new_desc = (
                str(l_b.get("description", ""))
                if "description" in sub_diff
                else None
            )
            return new_desc, None

    elif changed_top == {"pythonFunction"} and isinstance(
        live_tj.get("pythonFunction"), dict
    ):
        o_b = dict(base_tj.get("pythonFunction") or {})
        l_b = dict(live_tj["pythonFunction"])
        sub_diff = {k for k in set(o_b) | set(l_b) if o_b.get(k) != l_b.get(k)}
        if sub_diff.issubset({"description"}):
            new_desc = (
                str(l_b.get("description", ""))
                if "description" in sub_diff
                else None
            )
            return new_desc, None

    raise BuildGateError(
        f"Cannot backport structural tool change on {res_id} "
        f"(changed keys: {sorted(changed_top)}){error_suffix}"
    )


def _check_python_bundle_and_extra_files(
    *,
    changed_res_ids: Sequence[str],
    ours_norm: Mapping[str, NormalizedResource],
    live_norm: Mapping[str, NormalizedResource],
    brand_id: str,
    target: BackportTarget,
) -> dict[str, list[str]]:
    changed_py_files: dict[str, list[str]] = {}
    for res_id in changed_res_ids:
        ours_obj = json.loads(ours_norm[res_id].normalized)
        live_obj = json.loads(live_norm[res_id].normalized)

        ours_digests = ours_obj.get("_sharedBundleDigests", {})
        live_digests = live_obj.get("_sharedBundleDigests", {})
        if ours_digests != live_digests:
            raise BuildGateError(
                f"Cannot backport {res_id}: CXAS_SHARED_BUNDLE (python_lib/) "
                "module digests diverged; update shared modules in "
                "core/python_lib/ directly."
            )

        ours_extra = ours_obj.get("_extraFiles", {})
        live_extra = live_obj.get("_extraFiles", {})
        if ours_extra == live_extra:
            continue

        diff_py = sorted(
            k
            for k in set(ours_extra) | set(live_extra)
            if ours_extra.get(k) != live_extra.get(k)
        )
        if target == "brand":
            raise BuildGateError(
                f"Cannot backport Python code edits ({diff_py}) on {res_id} "
                f"into profiles/{brand_id}/overrides.yaml; re-run with "
                "--target core."
            )
        missing_in_live = sorted(set(ours_extra) - set(live_extra))
        if missing_in_live:
            raise BuildGateError(
                f"Cannot backport deleted Python entrypoint(s) "
                f"{missing_in_live} on {res_id}."
            )
        changed_py_files[res_id] = diff_py
    return changed_py_files


def _reverse_tokens_in_python_strings(
    clean_py: str,
    rev_pairs: Sequence[tuple[str, str]],
    svc_patterns: Sequence[tuple[re.Pattern[str], str]],
    render_fn: Callable[[str], str],
) -> str:
    """Reverse-tokenize only Python string literals, never identifiers."""
    lines = clean_py.splitlines(keepends=True)
    line_offsets: list[int] = [0]
    for ln in lines:
        line_offsets.append(line_offsets[-1] + len(ln))

    def _to_offset(pos: tuple[int, int]) -> int:
        row, col = pos
        return line_offsets[row - 1] + col

    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(clean_py).readline))
    except (tokenize.TokenError, IndentationError):
        return clean_py

    edits: list[tuple[int, int, str]] = []
    for tok in tokens:
        if tok.type == tokenize.STRING:
            m = _PY_STR_PREFIX_RE.match(tok.string)
            if not m:
                continue
            prefix, quote = m.group(1), m.group(2)
            if "b" in prefix.lower():
                continue
            body = tok.string[len(prefix) + len(quote) : -len(quote)]
            new_body = _reverse_tokens(body, rev_pairs, svc_patterns, render_fn)
            if new_body != body:
                edits.append(
                    (
                        _to_offset(tok.start),
                        _to_offset(tok.end),
                        f"{prefix}{quote}{new_body}{quote}",
                    )
                )
        elif _FSTRING_MIDDLE is not None and tok.type == _FSTRING_MIDDLE:
            new_seg = _reverse_tokens(
                tok.string, rev_pairs, svc_patterns, render_fn
            )
            if new_seg != tok.string:
                edits.append(
                    (_to_offset(tok.start), _to_offset(tok.end), new_seg)
                )

    if not edits:
        return clean_py
    result = clean_py
    for start_off, end_off, replacement in reversed(edits):
        result = result[:start_off] + replacement + result[end_off:]
    return result


def _reverse_python_code_safely(
    raw_py: str,
    py_path: Path,
    *,
    manifests: Manifests,
    environment: str,
    rev_pairs: Sequence[tuple[str, str]],
    svc_patterns: Sequence[tuple[re.Pattern[str], str]],
    render_fn: Callable[[str], str],
) -> str:
    try:
        clean_py = strip_shared_bundle_text(raw_py, py_path).replace(
            "\r\n", "\n"
        )
    except PythonBundlerError as exc:
        raise BuildGateError(str(exc)) from exc

    try:
        ast.parse(clean_py, filename=str(py_path))
    except SyntaxError as exc:
        raise BuildGateError(
            f"Cannot backport invalid Python syntax in {py_path}: {exc}"
        ) from exc

    cand_py = _reverse_tokens_in_python_strings(
        clean_py, rev_pairs, svc_patterns, render_fn
    )
    if cand_py == clean_py:
        return clean_py

    try:
        rendered_source = _render_python_source(
            cand_py,
            py_path,
            types.SimpleNamespace(
                process_conditional_blocks=lambda s: s,
                substitute_tokens=render_fn,
            ),
        )
    except BuildError:
        return clean_py
    if rendered_source != clean_py:
        return clean_py

    for b_id, pol in manifests.policies.items():
        for ch in pol.channels:
            r_fn = _make_token_renderer(manifests, b_id, ch, environment)
            try:
                _render_python_source(
                    cand_py,
                    py_path,
                    types.SimpleNamespace(
                        process_conditional_blocks=lambda s: s,
                        substitute_tokens=r_fn,
                    ),
                )
            except BuildError:
                return clean_py
    return cand_py


def _has_reachable_inactive_inline(
    text: str, eval_fn: Callable[[str, str], bool]
) -> bool:
    for m in _COND_INLINE_RE.finditer(text):
        if not eval_fn(m.group(1), m.group(2)):
            return True
        if _has_reachable_inactive_inline(m.group(3), eval_fn):
            return True
    return False


def _analyze_inline_conditionals(
    r_line: str, eval_fn: Callable[[str, str], bool]
) -> tuple[str, list[str]]:
    if "{@start" not in r_line:
        return "none", []
    top_matches = list(_COND_INLINE_RE.finditer(r_line))
    if not top_matches:
        return "none", []
    outside = _COND_INLINE_RE.sub("", r_line)
    if outside.strip() == "" and all(
        not eval_fn(m.group(1), m.group(2)) for m in top_matches
    ):
        expanded: list[str] = []
        if len(top_matches) == 1:
            m0 = top_matches[0]
            lead_ws = r_line[: m0.start()]
            trail_ws = r_line[m0.end() :]
            expanded.extend(
                [
                    f"{{@start{m0.group(1)}_{m0.group(2)}}}",
                    f"{lead_ws}{m0.group(3)}{trail_ws}",
                    f"{{@end{m0.group(1)}_{m0.group(2)}}}",
                ]
            )
        else:
            for m in top_matches:
                expanded.extend(
                    [
                        f"{{@start{m.group(1)}_{m.group(2)}}}",
                        m.group(3),
                        f"{{@end{m.group(1)}_{m.group(2)}}}",
                    ]
                )
        return "pure_inactive", expanded
    if _has_reachable_inactive_inline(r_line, eval_fn):
        return "mixed_inactive", []
    return "none", []


def _eval_inline_conditionals_line(
    r_line: str, eval_fn: Callable[[str, str], bool]
) -> str:
    """Evaluate inline `{@start...}...{@end...}` blocks on one raw line."""
    cur = r_line
    while "{@start" in cur:
        nxt = _COND_INLINE_RE.sub(
            lambda m: m.group(3) if eval_fn(m.group(1), m.group(2)) else "",
            cur,
        )
        if nxt == cur:
            break
        cur = nxt
    return cur


def _align_raw_lines_to_base_spans(
    emit_to_raw_line: Sequence[str],
    base_split: Sequence[str],
    *,
    eval_fn: Callable[[str, str], bool],
    render_fn: Callable[[str], str] | None = None,
) -> list[tuple[int, int]] | None:
    """Map each emitted raw line to a contiguous `[start, end)` base span."""
    n_raw = len(emit_to_raw_line)
    n_base = len(base_split)
    if n_raw == n_base:
        return [(i, i + 1) for i in range(n_raw)]
    if n_raw == 0 or n_base == 0:
        return None

    # 1. Exact alignment using `render_fn` when available (handles any
    # combination of single-line and multi-line token expansions cleanly).
    if render_fn is not None:
        spans: list[tuple[int, int]] = []
        cursor = 0
        matched = True
        for r_line in emit_to_raw_line:
            rendered = render_fn(
                _eval_inline_conditionals_line(r_line, eval_fn)
            ).replace("\r\n", "\n")
            parts = rendered.split("\n")
            end = cursor + len(parts)
            if end > n_base or list(base_split[cursor:end]) != parts:
                matched = False
                break
            spans.append((cursor, end))
            cursor = end
        if matched and cursor == n_base:
            return spans

    # 2. Structural anchor alignment without `render_fn`: literal lines match
    # 1:1 while a single raw line containing a multi-line token maps to its
    # expanded `base_split` slice.
    eval_raw = [
        _eval_inline_conditionals_line(ln, eval_fn) for ln in emit_to_raw_line
    ]
    opcodes = difflib.SequenceMatcher(
        None, eval_raw, list(base_split), autojunk=False
    ).get_opcodes()
    spans_by_raw: list[tuple[int, int] | None] = [None] * n_raw
    for tag, ri1, ri2, bi1, bi2 in opcodes:
        if tag == "equal":
            for k in range(ri2 - ri1):
                spans_by_raw[ri1 + k] = (bi1 + k, bi1 + k + 1)
        elif tag == "replace":
            r_len = ri2 - ri1
            b_len = bi2 - bi1
            if r_len == 1 and b_len >= 1:
                spans_by_raw[ri1] = (bi1, bi2)
            elif r_len == b_len:
                for k in range(r_len):
                    spans_by_raw[ri1 + k] = (bi1 + k, bi1 + k + 1)
            else:
                return None
        else:
            return None

    if any(s is None for s in spans_by_raw):
        return None
    resolved = [s for s in spans_by_raw if s is not None]
    if (
        resolved[0][0] != 0
        or resolved[-1][1] != n_base
        or any(b_e <= b_s for b_s, b_e in resolved)
        or any(
            resolved[i][1] != resolved[i + 1][0]
            for i in range(len(resolved) - 1)
        )
    ):
        return None
    return resolved


def _patch_core_instruction_preserving_conditionals(
    raw_core_text: str,
    base_built_text: str,
    live_built_text: str,
    *,
    brand_id: str,
    channel: str,
    disabled_agents: set[str],
    disabled_tools: set[str],
    rev_fn: Callable[[str], str],
    render_fn: Callable[[str], str] | None = None,
) -> str:
    """Patch a `core/` prompt while keeping active and inactive conditionals."""

    def _eval_cond(kind: str, arg: str) -> bool:
        if kind == "Channel":
            return arg == channel
        if kind == "Profile":
            return arg == brand_id
        if kind == "AgentEnabled":
            return arg not in disabled_agents
        if kind == "ToolEnabled":
            return arg not in disabled_tools
        return True

    raw_norm = raw_core_text.replace("\r\n", "\n")
    raw_lines = raw_norm.splitlines()
    base_split = base_built_text.replace("\r\n", "\n").splitlines()
    live_split = live_built_text.replace("\r\n", "\n").splitlines()

    pre_interstitial: dict[int, list[str]] = {}
    emit_to_raw_line: list[str] = []
    pending_struct: list[str] = []
    stack: list[tuple[str, str, bool]] = []
    has_active_cond = False

    for r_line in raw_lines:
        stripped = r_line.strip()
        m_s = _COND_START_RE.match(stripped)
        if m_s:
            k, a = m_s.group(1), m_s.group(2)
            active = _eval_cond(k, a)
            if active and all(item[2] for item in stack):
                has_active_cond = True
            stack.append((k, a, active))
            pending_struct.append(r_line)
            continue
        m_e = _COND_END_RE.match(stripped)
        if m_e:
            if stack:
                stack.pop()
            pending_struct.append(r_line)
            continue
        if all(item[2] for item in stack):
            if "{@start" in r_line:
                for m_inl in _COND_INLINE_RE.finditer(r_line):
                    if _eval_cond(m_inl.group(1), m_inl.group(2)):
                        has_active_cond = True
            emit_idx = len(emit_to_raw_line)
            if pending_struct:
                pre_interstitial[emit_idx] = list(pending_struct)
                pending_struct.clear()
            emit_to_raw_line.append(r_line)
        else:
            pending_struct.append(r_line)

    trailing_struct = list(pending_struct)

    if (
        not raw_norm.endswith("\n")
        and len(emit_to_raw_line) == len(base_split) + 1
    ):
        tail_kind, tail_exp = _analyze_inline_conditionals(
            emit_to_raw_line[-1], _eval_cond
        )
        if tail_kind == "pure_inactive":
            last_idx = len(emit_to_raw_line) - 1
            emit_to_raw_line.pop()
            tail_pre = pre_interstitial.pop(last_idx, [])
            trailing_struct = [*tail_pre, *tail_exp, *trailing_struct]

    raw_spans = _align_raw_lines_to_base_spans(
        emit_to_raw_line,
        base_split,
        eval_fn=_eval_cond,
        render_fn=render_fn,
    )
    if raw_spans is not None:
        base_to_raw: list[int] = [-1] * len(base_split)
        raw_bounds: dict[int, int] = {len(base_split): len(raw_spans)}
        for r_idx, (b_s, b_e) in enumerate(raw_spans):
            raw_bounds[b_s] = r_idx
            for b_i in range(b_s, b_e):
                base_to_raw[b_i] = r_idx

        opcodes = difflib.SequenceMatcher(
            None, base_split, live_split, autojunk=False
        ).get_opcodes()
        base_to_live: dict[int, int] = {}
        dirty_raw = [False] * len(raw_spans)
        for tag, i1, i2, j1, _j2 in opcodes:
            if tag == "equal":
                for k in range(i2 - i1):
                    base_to_live[i1 + k] = j1 + k
            elif tag in ("replace", "delete"):
                for b_i in range(i1, i2):
                    dirty_raw[base_to_raw[b_i]] = True
            elif tag == "insert" and i1 not in raw_bounds:
                dirty_raw[base_to_raw[i1]] = True

        out_raw: list[str] = []
        live_cursor = 0
        r_idx = 0
        n_raw = len(raw_spans)
        while r_idx < n_raw:
            if not dirty_raw[r_idx]:
                b_s, b_e = raw_spans[r_idx]
                l_s = base_to_live[b_s]
                l_e = base_to_live[b_e - 1] + 1
                if live_cursor < l_s:
                    if r_idx in pre_interstitial:
                        out_raw.extend(pre_interstitial.pop(r_idx))
                    for l_ln in live_split[live_cursor:l_s]:
                        out_raw.append(rev_fn(l_ln))
                out_raw.extend(pre_interstitial.get(r_idx, ()))
                out_raw.append(emit_to_raw_line[r_idx])
                live_cursor = l_e
                r_idx += 1
            else:
                r_start = r_idx
                while r_idx < n_raw and dirty_raw[r_idx]:
                    r_idx += 1
                r_end = r_idx
                live_end = (
                    base_to_live[raw_spans[r_end][0]]
                    if r_end < n_raw
                    else len(live_split)
                )
                for d_idx in range(r_start, r_end):
                    out_raw.extend(pre_interstitial.get(d_idx, ()))
                    r_ln = emit_to_raw_line[d_idx]
                    inl_kind, inl_exp = _analyze_inline_conditionals(
                        r_ln, _eval_cond
                    )
                    if inl_kind == "pure_inactive":
                        out_raw.extend(inl_exp)
                    elif inl_kind == "mixed_inactive":
                        raise BuildGateError(
                            "Cannot backport modification/deletion of line "
                            "mixing active text with an inactive inline "
                            f"conditional ({r_ln.strip()!r}); edit this line "
                            "in core/ directly."
                        )
                for l_ln in live_split[live_cursor:live_end]:
                    out_raw.append(rev_fn(l_ln))
                live_cursor = live_end

        if live_cursor < len(live_split):
            for l_ln in live_split[live_cursor:]:
                out_raw.append(rev_fn(l_ln))
        out_raw.extend(trailing_struct)
        return "\n".join(out_raw) + ("\n" if out_raw else "")

    if has_active_cond:
        raise BuildGateError(
            "Cannot safely preserve active conditional block(s) during "
            "--target core backport because line alignment failed; edit "
            "this file in core/ directly."
        )

    preserved_inactive: list[str] = []
    inact_stack: list[bool] = []
    cur_block: list[str] = []
    for r_line in raw_lines:
        st = r_line.strip()
        ms = _COND_START_RE.match(st)
        if ms:
            act = _eval_cond(ms.group(1), ms.group(2))
            inact_stack.append(act)
            if not all(inact_stack):
                cur_block.append(r_line)
            continue
        me = _COND_END_RE.match(st)
        if me and inact_stack:
            was_inact = not all(inact_stack)
            inact_stack.pop()
            if was_inact:
                cur_block.append(r_line)
                if all(inact_stack):
                    preserved_inactive.extend(cur_block)
                    cur_block.clear()
            continue
        if inact_stack and not all(inact_stack):
            cur_block.append(r_line)
            continue
        inl_kind, inl_exp = _analyze_inline_conditionals(r_line, _eval_cond)
        if inl_kind == "pure_inactive":
            preserved_inactive.extend(inl_exp)
        elif inl_kind == "mixed_inactive":
            raise BuildGateError(
                "Cannot backport modification/deletion of line mixing active "
                "text with an inactive inline conditional "
                f"({r_line.strip()!r}); edit this line in core/ directly."
            )

    rebuilt = [*preserved_inactive, *[rev_fn(ln) for ln in live_split]]
    return "\n".join(rebuilt) + ("\n" if rebuilt else "")


def _strip_brand_overrides_for_baseline(
    ov_yaml: dict[str, Any],
    *,
    channel_to_clear: str | None,
) -> dict[str, Any]:
    stripped = copy.deepcopy(ov_yaml)
    if channel_to_clear is None:
        return {"profile_id": stripped["profile_id"]}

    gi = stripped.get("global_instruction")
    if isinstance(gi, dict) and isinstance(gi.get("channels"), dict):
        gi["channels"].pop(channel_to_clear, None)

    agents = stripped.get("agents")
    if isinstance(agents, dict):
        for ag_ov in agents.values():
            if isinstance(ag_ov, dict) and isinstance(
                ag_ov.get("channels"), dict
            ):
                ag_ov["channels"].pop(channel_to_clear, None)

    tools = stripped.get("tools")
    if isinstance(tools, dict):
        for t_ov in tools.values():
            if isinstance(t_ov, dict) and isinstance(
                t_ov.get("channels"), dict
            ):
                t_ov["channels"].pop(channel_to_clear, None)

    return _prune_empty_dict(stripped)


def _build_clean_brand_baselines(
    core_dir: Path,
    manifests_dir: Path,
    brand_id: str,
    environment: str,
    *,
    clear_all_brand: bool,
    channel_to_clear: str | None,
    work_dir: Path,
) -> dict[str, Path]:
    tmp_man = work_dir / "clean_manifests"
    tmp_out = work_dir / "clean_dist"
    shutil.copytree(
        manifests_dir,
        tmp_man,
        ignore=shutil.ignore_patterns(".git", "__pycache__"),
    )
    _make_tree_writable(tmp_man)
    ov_path = tmp_man / "profiles" / brand_id / "overrides.yaml"
    if clear_all_brand:
        if ov_path.is_file():
            ov_path.unlink()
    elif ov_path.is_file():
        raw_ov = _load_yaml_dict_optional(ov_path)
        cleaned_ov = _strip_brand_overrides_for_baseline(
            raw_ov, channel_to_clear=channel_to_clear
        )
        if set(cleaned_ov.keys()) <= {"profile_id"}:
            ov_path.unlink()
        else:
            _dump_yaml_clean(ov_path, cleaned_ov)

    clean_manifests = load_manifests(tmp_man)
    pol = clean_manifests.policy(brand_id)
    out_map: dict[str, Path] = {}
    for ch in pol.channels:
        ch_dir = tmp_out / brand_id / ch.lower()
        build_app(
            core_dir=core_dir,
            manifests_dir=tmp_man,
            profile_id=brand_id,
            channel=ch,
            environment=environment,
            out_dir=ch_dir,
        )
        out_map[ch] = ch_dir
    return out_map


def _assign_instruction_delta(
    holder: dict[str, Any], strat: str, payload: Any
) -> None:
    holder.pop("instruction", None)
    holder.pop("replace_lines", None)
    holder.pop("append_rules", None)
    if strat == "append_rules":
        holder["append_rules"] = payload
    elif strat == "replace_lines":
        holder["replace_lines"] = payload
    elif strat == "replace_and_append":
        holder["replace_lines"] = payload[0]
        holder["append_rules"] = payload[1]
    else:
        holder["instruction"] = payload


def _apply_brand_target_backport(
    *,
    core_dir: Path,
    manifests_dir: Path,
    manifests: Manifests,
    brand_id: str,
    channel: str,
    environment: str,
    scope: BackportScope,
    ours_bundle: Path,
    live_bundle: Path,
    base_bundles_by_channel: Mapping[str, Path],
    changed_res_ids: Sequence[str],
) -> tuple[list[Path], list[BackportChange]]:
    rev_pairs, svc_patterns, render_fn = _build_reverse_token_map(
        manifests, brand_id, channel, environment
    )
    base_bundle = base_bundles_by_channel[channel]
    ov_path = manifests_dir / "profiles" / brand_id / "overrides.yaml"
    assert_safe_subpath(ov_path, manifests_dir, label="overrides.yaml")
    orig_ov_exists = ov_path.is_file()
    ov_yaml = copy.deepcopy(_load_yaml_dict_optional(ov_path))
    ov_yaml["profile_id"] = brand_id
    rel_target = f"profiles/{brand_id}/overrides.yaml"
    changes: list[BackportChange] = []

    policy = manifests.policy(brand_id)
    sibling_channels = [ch for ch in policy.channels if ch != channel]

    core_gi_path = core_dir / "global_instruction.txt"
    has_inline_id_tok = (
        core_gi_path.is_file()
        and "@@profile.identity_block@@"
        in read_utf8_strict(core_gi_path, context="core global_instruction.txt")
    )
    rendered_id_block = render_fn(_build_identity_block(manifests, brand_id))
    rendered_id_prefix = rendered_id_block

    for res_id in changed_res_ids:
        if res_id == "app":
            ours_app = _normalize_app_obj(
                _load_bundle_app_config(ours_bundle, label="ours app config")
            )
            live_app = _normalize_app_obj(
                _load_bundle_app_config(live_bundle, label="live app config")
            )
            if ours_app != live_app:
                diff_keys = sorted(
                    k
                    for k in set(ours_app) | set(live_app)
                    if ours_app.get(k) != live_app.get(k)
                )
                raise BuildGateError(
                    f"Cannot backport app config change on keys {diff_keys} "
                    f"into {rel_target}; edit "
                    f"profiles/{brand_id}/identity.yaml or core/app.json."
                )

            ours_gi = _read_optional_text(
                ours_bundle / "global_instruction.txt",
                context="ours global_instruction.txt",
            )
            base_gi = _read_optional_text(
                base_bundle / "global_instruction.txt",
                context="baseline global_instruction.txt",
            )
            live_gi = _read_optional_text(
                live_bundle / "global_instruction.txt",
                context="live global_instruction.txt",
            )
            live_gi_norm = live_gi.replace("\r\n", "\n")
            id_missing = (
                (rendered_id_block and rendered_id_block not in live_gi_norm)
                if has_inline_id_tok
                else (
                    rendered_id_prefix
                    and rendered_id_prefix not in live_gi_norm
                )
            )
            if id_missing:
                raise BuildGateError(
                    f"Cannot backport global_instruction.txt: live modified "
                    f"the generated '# Profile:' identity block; update "
                    f"profiles/{brand_id}/identity.yaml directly."
                )

            ours_gi_lines = ours_gi.replace("\r\n", "\n").splitlines()
            base_gi_lines = base_gi.replace("\r\n", "\n").splitlines()
            live_gi_lines = live_gi_norm.splitlines()
            if ours_gi_lines != live_gi_lines or (
                scope == "brand" and base_gi_lines != live_gi_lines
            ):
                gi_ov = copy.deepcopy(
                    dict(ov_yaml.get("global_instruction") or {})
                )
                gi_channels = dict(gi_ov.get("channels") or {})
                gi_channels.pop(channel, None)
                if scope == "brand":
                    gi_ov.pop("instruction", None)
                    gi_ov.pop("replace_lines", None)
                    gi_ov.pop("append_rules", None)

                if base_gi_lines == live_gi_lines:
                    summary = "Cleared reverted global_instruction override"
                else:
                    sib_gi = [
                        (
                            _read_optional_text(
                                base_bundles_by_channel[s_ch]
                                / "global_instruction.txt"
                            ),
                            _make_token_renderer(
                                manifests, brand_id, s_ch, environment
                            ),
                        )
                        for s_ch in sibling_channels
                    ]
                    strat, payload = _compute_brand_instruction_delta(
                        base_gi,
                        live_gi,
                        scope=scope,
                        sibling_baselines=sib_gi,
                        rev_pairs=rev_pairs,
                        svc_patterns=svc_patterns,
                        render_fn=render_fn,
                        identity_prefix=rendered_id_prefix,
                    )
                    if scope == "channel":
                        ch_holder: dict[str, Any] = {}
                        _assign_instruction_delta(ch_holder, strat, payload)
                        gi_channels[channel] = ch_holder
                    else:
                        _assign_instruction_delta(gi_ov, strat, payload)
                    summary = f"Backported global_instruction.txt ({strat})"

                if gi_channels:
                    gi_ov["channels"] = gi_channels
                else:
                    gi_ov.pop("channels", None)
                cleaned_gi = _prune_empty_dict(gi_ov)
                if cleaned_gi:
                    ov_yaml["global_instruction"] = cleaned_gi
                else:
                    ov_yaml.pop("global_instruction", None)
                changes.append(
                    BackportChange(
                        resource_id="app",
                        target_file=rel_target,
                        summary=summary,
                    )
                )
            continue

        if res_id.startswith("agents/"):
            agent_id = res_id.split("/", 1)[1]
            ours_aj_raw = _load_bundle_resource_config(
                ours_bundle, "agents", agent_id, label=f"ours {res_id}"
            )
            base_aj_raw = _load_bundle_resource_config(
                base_bundle, "agents", agent_id, label=f"baseline {res_id}"
            )
            live_aj_raw = _load_bundle_resource_config(
                live_bundle, "agents", agent_id, label=f"live {res_id}"
            )
            ours_aj = normalize_json_obj(ours_aj_raw, is_root=True)
            base_aj = normalize_json_obj(base_aj_raw, is_root=True)
            live_aj = normalize_json_obj(live_aj_raw, is_root=True)
            for cmp_aj in (ours_aj, base_aj):
                changed_top = {
                    k
                    for k in set(cmp_aj) | set(live_aj)
                    if cmp_aj.get(k) != live_aj.get(k)
                }
                bad_top = sorted(changed_top - {"description", "modelSettings"})
                if bad_top:
                    raise BuildGateError(
                        f"Cannot backport structural config change on {res_id} "
                        f"keys {bad_top} into {rel_target}; edit "
                        f"core/agents/{agent_id}/ or channel_policy.yaml."
                    )

            agents_root = dict(ov_yaml.get("agents") or {})
            ag_ov_dict = copy.deepcopy(dict(agents_root.get(agent_id) or {}))
            ag_channels = dict(ag_ov_dict.get("channels") or {})
            ag_channels.pop(channel, None)
            if scope == "brand":
                for b_k in (
                    "description",
                    "temperature",
                    "model",
                    "instruction",
                    "replace_lines",
                    "append_rules",
                ):
                    ag_ov_dict.pop(b_k, None)

            target_holder: dict[str, Any] = (
                {} if scope == "channel" else ag_ov_dict
            )
            summary_parts: list[str] = []

            if base_aj != live_aj or ours_aj != live_aj:
                if "description" in base_aj and "description" not in live_aj:
                    raise BuildGateError(
                        f"Cannot backport removal of 'description' on "
                        f"{res_id} into {rel_target}: brand overrides can only "
                        "set values, not delete them; edit "
                        f"core/agents/{agent_id}/ directly."
                    )
                if base_aj.get("description") != live_aj.get("description"):
                    target_holder["description"] = _reverse_tokens(
                        str(live_aj.get("description", "")),
                        rev_pairs,
                        svc_patterns,
                        render_fn,
                    )
                if ours_aj.get("description") != live_aj.get("description") or (
                    scope == "brand"
                    and base_aj.get("description") != live_aj.get("description")
                ):
                    summary_parts.append("description")

                base_ms = (
                    base_aj.get("modelSettings")
                    if isinstance(base_aj.get("modelSettings"), dict)
                    else {}
                )
                ours_ms = (
                    ours_aj.get("modelSettings")
                    if isinstance(ours_aj.get("modelSettings"), dict)
                    else {}
                )
                live_ms = (
                    live_aj.get("modelSettings")
                    if isinstance(live_aj.get("modelSettings"), dict)
                    else {}
                )
                changed_ms = {
                    k
                    for k in set(base_ms) | set(ours_ms) | set(live_ms)
                    if base_ms.get(k) != live_ms.get(k)
                    or ours_ms.get(k) != live_ms.get(k)
                }
                bad_ms = sorted(changed_ms - {"model", "temperature"})
                if bad_ms:
                    raise BuildGateError(
                        f"Cannot backport unsupported modelSettings keys "
                        f"{bad_ms} on {res_id}."
                    )
                removed_ms = sorted(
                    k
                    for k in ("model", "temperature")
                    if k in base_ms and k not in live_ms
                )
                if removed_ms:
                    raise BuildGateError(
                        f"Cannot backport removal of modelSettings "
                        f"{removed_ms} on {res_id} into {rel_target}: brand "
                        "overrides can only set values, not delete them; "
                        f"edit core/agents/{agent_id}/ or envs/*.yaml directly."
                    )
                if base_ms.get("model") != live_ms.get("model"):
                    target_holder["model"] = str(live_ms["model"])
                if ours_ms.get("model") != live_ms.get("model") or (
                    scope == "brand"
                    and base_ms.get("model") != live_ms.get("model")
                ):
                    summary_parts.append(f"model={live_ms.get('model')}")

                live_temp = live_ms.get("temperature")
                base_temp = base_ms.get("temperature")
                if live_temp is not None and (
                    isinstance(live_temp, bool)
                    or not isinstance(live_temp, (int, float))
                    or not math.isfinite(float(live_temp))
                    or not (0.0 <= float(live_temp) <= 2.0)
                ):
                    raise BuildGateError(
                        f"Invalid modelSettings.temperature {live_temp!r} "
                        f"on {res_id}: expected a finite number in "
                        "[0.0, 2.0]."
                    )
                if base_temp != live_temp or (
                    isinstance(base_temp, bool) != isinstance(live_temp, bool)
                ):
                    if live_temp is None:
                        raise BuildGateError(
                            f"Cannot backport null modelSettings.temperature "
                            f"on {res_id} into {rel_target}: brand overrides "
                            "can only set numeric values, not clear them; "
                            f"edit core/agents/{agent_id}/ or envs/*.yaml "
                            "directly."
                        )
                    target_holder["temperature"] = float(live_temp)
                if ours_ms.get("temperature") != live_temp or (
                    scope == "brand" and base_temp != live_temp
                ):
                    summary_parts.append(f"temperature={live_temp}")

            ours_in_path = _resolve_agent_instruction_path(
                ours_bundle, agent_id, ours_aj_raw
            )
            base_in_path = _resolve_agent_instruction_path(
                base_bundle, agent_id, base_aj_raw
            )
            live_in_path = _resolve_agent_instruction_path(
                live_bundle, agent_id, live_aj_raw
            )
            ours_in = _read_optional_text(
                ours_in_path, context=f"ours {res_id} instruction"
            )
            base_in = _read_optional_text(
                base_in_path, context=f"baseline {res_id} instruction"
            )
            live_in = _read_optional_text(
                live_in_path, context=f"live {res_id} instruction"
            )
            ours_in_l = ours_in.replace("\r\n", "\n").splitlines()
            base_in_l = base_in.replace("\r\n", "\n").splitlines()
            live_in_l = live_in.replace("\r\n", "\n").splitlines()

            if base_in_l != live_in_l:
                sib_ag: list[tuple[str, Callable[[str], str]]] = []
                for s_ch in sibling_channels:
                    sib_b = base_bundles_by_channel[s_ch]
                    sib_ag_dir = sib_b / "agents" / agent_id
                    if not sib_ag_dir.is_dir():
                        continue
                    sib_cfg_p = _find_config_file(
                        sib_ag_dir, agent_id, sib_b, label=f"sibling {res_id}"
                    )
                    sib_cfg = (
                        load_mapping_strict(
                            sib_cfg_p, context=f"sibling {res_id}"
                        )
                        if sib_cfg_p is not None
                        else {}
                    )
                    sib_in_p = _resolve_agent_instruction_path(
                        sib_b, agent_id, sib_cfg
                    )
                    if not sib_in_p.is_file():
                        continue
                    sib_ag.append(
                        (
                            read_utf8_strict(sib_in_p),
                            _make_token_renderer(
                                manifests, brand_id, s_ch, environment
                            ),
                        )
                    )
                strat, payload = _compute_brand_instruction_delta(
                    base_in,
                    live_in,
                    scope=scope,
                    sibling_baselines=sib_ag,
                    rev_pairs=rev_pairs,
                    svc_patterns=svc_patterns,
                    render_fn=render_fn,
                )
                _assign_instruction_delta(target_holder, strat, payload)
                if ours_in_l != live_in_l or scope == "brand":
                    summary_parts.append(f"instruction ({strat})")
            elif ours_in_l != live_in_l:
                summary_parts.append("instruction (reverted override)")

            if scope == "channel":
                cleaned_ch = _prune_empty_dict(target_holder)
                if cleaned_ch:
                    ag_channels[channel] = cleaned_ch

            if ag_channels:
                ag_ov_dict["channels"] = ag_channels
            else:
                ag_ov_dict.pop("channels", None)

            cleaned_ag = _prune_empty_dict(ag_ov_dict)
            if cleaned_ag:
                agents_root[agent_id] = cleaned_ag
            else:
                agents_root.pop(agent_id, None)
            if agents_root:
                ov_yaml["agents"] = agents_root
            else:
                ov_yaml.pop("agents", None)

            if not summary_parts:
                summary_parts.append("reverted override")
            changes.append(
                BackportChange(
                    resource_id=res_id,
                    target_file=rel_target,
                    summary=f"Backported {', '.join(summary_parts)}",
                )
            )
            continue

        if res_id.startswith("tools/"):
            tool_id = res_id.split("/", 1)[1]
            err_sfx = " with --target brand; edit core/ directly."
            ours_tj, _o_oa_c, _o_oa_r = _load_normalized_tool(
                ours_bundle, tool_id, res_id
            )
            base_tj, base_oa_c, _b_oa_r = _load_normalized_tool(
                base_bundle, tool_id, res_id
            )
            live_tj, live_oa_c, _l_oa_r = _load_normalized_tool(
                live_bundle, tool_id, res_id
            )
            _extract_tool_json_deltas(
                ours_tj, live_tj, res_id=res_id, error_suffix=err_sfx
            )
            new_desc, new_url = _extract_tool_json_deltas(
                base_tj, live_tj, res_id=res_id, error_suffix=err_sfx
            )
            base_sub = _tool_payload(base_tj)
            live_sub = _tool_payload(live_tj)
            removed_t = sorted(
                k
                for k in ("description", "url")
                if k in base_sub and k not in live_sub
            )
            if removed_t:
                raise BuildGateError(
                    f"Cannot backport removal of {removed_t} on {res_id} into "
                    f"{rel_target}: brand overrides can only set values, not "
                    "delete them; edit core/ directly."
                )
            if base_oa_c != live_oa_c:
                raise BuildGateError(
                    f"Cannot backport structural OpenAPI schema change on "
                    f"{res_id}{err_sfx}"
                )

            tools_root = dict(ov_yaml.get("tools") or {})
            t_ov_dict = copy.deepcopy(dict(tools_root.get(tool_id) or {}))
            t_channels = dict(t_ov_dict.get("channels") or {})
            t_channels.pop(channel, None)
            if scope == "brand":
                t_ov_dict.pop("description", None)
                t_ov_dict.pop("url", None)

            t_holder: dict[str, Any] = {} if scope == "channel" else t_ov_dict
            t_parts: list[str] = []
            if new_desc is not None:
                t_holder["description"] = _reverse_tokens(
                    new_desc, rev_pairs, svc_patterns, render_fn
                )
                t_parts.append("description")
            if new_url is not None:
                t_holder["url"] = _reverse_url_tokens(
                    new_url,
                    brand_id,
                    rev_pairs,
                    svc_patterns,
                    render_fn,
                )
                t_parts.append("url")

            if not t_parts and ours_tj == live_tj:
                raise BuildGateError(
                    f"Cannot backport structural tool change on "
                    f"{res_id}{err_sfx}"
                )
            if not t_parts:
                t_parts.append("reverted override")

            if scope == "channel":
                cleaned_tch = _prune_empty_dict(t_holder)
                if cleaned_tch:
                    t_channels[channel] = cleaned_tch
            if t_channels:
                t_ov_dict["channels"] = t_channels
            else:
                t_ov_dict.pop("channels", None)

            cleaned_t = _prune_empty_dict(t_ov_dict)
            if cleaned_t:
                tools_root[tool_id] = cleaned_t
            else:
                tools_root.pop(tool_id, None)
            if tools_root:
                ov_yaml["tools"] = tools_root
            else:
                ov_yaml.pop("tools", None)

            changes.append(
                BackportChange(
                    resource_id=res_id,
                    target_file=rel_target,
                    summary=f"Backported tool {', '.join(t_parts)}",
                )
            )
            continue

        raise BuildGateError(
            f"Cannot backport resource {res_id!r} with --target brand; "
            "edit core/ directly."
        )

    cleaned_ov_root = _prune_empty_dict(ov_yaml)
    if set(cleaned_ov_root.keys()) <= {"profile_id"}:
        if orig_ov_exists:
            ov_path.unlink()
            return [ov_path], changes
        return [], changes

    _dump_yaml_clean(ov_path, cleaned_ov_root)
    return [ov_path], changes


def _write_core_json_formatted(path: Path, payload: dict[str, Any]) -> None:
    if path.is_symlink():
        raise BuildGateError(f"Refusing to write through symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_core_mapping_formatted(path: Path, payload: dict[str, Any]) -> None:
    if path.suffix.lower() in {".yaml", ".yml"}:
        _dump_yaml_clean(path, payload)
    else:
        _write_core_json_formatted(path, payload)


def _apply_core_target_backport(
    *,
    core_dir: Path,
    manifests_dir: Path,
    manifests: Manifests,
    brand_id: str,
    channel: str,
    environment: str,
    ours_bundle: Path,
    live_bundle: Path,
    unoverridden_bundle: Path,
    changed_res_ids: Sequence[str],
    changed_py_files: Mapping[str, list[str]],
    is_promotion_only: bool,
) -> tuple[list[Path], list[BackportChange]]:
    rev_pairs, svc_patterns, render_fn = _build_reverse_token_map(
        manifests, brand_id, channel, environment
    )
    # Shared core/ is built for every brand: never lift literals into
    # brand-scoped tokens that some sister brand cannot resolve.
    rev_pairs = [
        (val, tok)
        for val, tok in rev_pairs
        if _token_defined_for_all_brands(manifests, tok)
    ]

    def _rev(s: str) -> str:
        return _reverse_tokens(s, rev_pairs, svc_patterns, render_fn)

    policy = manifests.policy(brand_id)
    ch_caps = policy.channels[channel]
    disabled_agents = set(ch_caps.disabled_agents)
    disabled_tools = set(ch_caps.disabled_tools)

    # Include displayNames of pruned core agents/tools so {@startAgentEnabled_*}
    # matching displayName is evaluated identically to builder.py.
    if (core_dir / "agents").is_dir():
        for ag_p in sorted((core_dir / "agents").iterdir()):
            if not ag_p.is_dir():
                continue
            aj_p = _find_config_file(
                ag_p, ag_p.name, core_dir, label=f"core agents/{ag_p.name}"
            )
            if aj_p is not None:
                d_name = load_mapping_strict(
                    aj_p, context=f"core agents/{ag_p.name}"
                ).get("displayName", "")
                if ag_p.name in disabled_agents or d_name in disabled_agents:
                    disabled_agents.add(ag_p.name)
                    if d_name:
                        disabled_agents.add(str(d_name))
    if (core_dir / "tools").is_dir():
        for tl_p in sorted((core_dir / "tools").iterdir()):
            if not tl_p.is_dir():
                continue
            tj_p = _find_config_file(
                tl_p, tl_p.name, core_dir, label=f"core tools/{tl_p.name}"
            )
            if tj_p is not None:
                d_name = load_mapping_strict(
                    tj_p, context=f"core tools/{tl_p.name}"
                ).get("displayName", "")
                if tl_p.name in disabled_tools or d_name in disabled_tools:
                    disabled_tools.add(tl_p.name)
                    if d_name:
                        disabled_tools.add(str(d_name))

    ov_path = manifests_dir / "profiles" / brand_id / "overrides.yaml"
    assert_safe_subpath(ov_path, manifests_dir, label="overrides.yaml")
    orig_ov_yaml = _load_yaml_dict_optional(ov_path)
    ov_yaml = copy.deepcopy(orig_ov_yaml)

    modified_paths: list[Path] = []
    changes: list[BackportChange] = []

    def _mark_modified(p: Path) -> None:
        if p not in modified_paths:
            modified_paths.append(p)

    for res_id in changed_res_ids:
        if res_id == "app":
            ours_app = _normalize_app_obj(
                _load_bundle_app_config(ours_bundle, label="ours app config")
            )
            live_app = _normalize_app_obj(
                _load_bundle_app_config(live_bundle, label="live app config")
            )
            if ours_app != live_app:
                diff_keys = sorted(
                    k
                    for k in set(ours_app) | set(live_app)
                    if ours_app.get(k) != live_app.get(k)
                )
                raise BuildGateError(
                    f"Cannot backport structural app config change on keys "
                    f"{diff_keys} with --target core."
                )

            base_gi = _read_optional_text(
                unoverridden_bundle / "global_instruction.txt",
                context="baseline global_instruction.txt",
            )
            ours_gi = _read_optional_text(
                ours_bundle / "global_instruction.txt",
                context="ours global_instruction.txt",
            )
            live_gi = _read_optional_text(
                live_bundle / "global_instruction.txt",
                context="live global_instruction.txt",
            )
            base_gi_l = base_gi.replace("\r\n", "\n").splitlines()
            ours_gi_l = ours_gi.replace("\r\n", "\n").splitlines()
            live_gi_l = live_gi.replace("\r\n", "\n").splitlines()

            if ours_gi_l != live_gi_l or (
                is_promotion_only and base_gi_l != live_gi_l
            ):
                core_gi_path = core_dir / "global_instruction.txt"
                assert_safe_subpath(
                    core_gi_path, core_dir, label="core global_instruction.txt"
                )
                raw_core_gi = _read_optional_text(
                    core_gi_path, context="core global_instruction.txt"
                )
                id_block = render_fn(_build_identity_block(manifests, brand_id))
                live_gi_norm = live_gi.replace("\r\n", "\n")
                base_gi_norm = base_gi.replace("\r\n", "\n")
                if "@@profile.identity_block@@" in raw_core_gi:
                    if id_block and id_block not in live_gi_norm:
                        raise BuildGateError(
                            f"Cannot backport global_instruction.txt: live "
                            f"modified the generated '# Profile:' identity "
                            f"block; edit profiles/{brand_id}/identity.yaml."
                        )
                    occ_cnt = max(
                        1, raw_core_gi.count("@@profile.identity_block@@")
                    )
                    b_tok = (
                        base_gi_norm.replace(
                            id_block, "@@profile.identity_block@@", occ_cnt
                        )
                        if id_block
                        else base_gi_norm
                    )
                    l_tok = (
                        live_gi_norm.replace(
                            id_block, "@@profile.identity_block@@", occ_cnt
                        )
                        if id_block
                        else live_gi_norm
                    )

                    def _gi_rev(ln: str) -> str:
                        if "@@profile.identity_block@@" not in ln:
                            return _rev(ln)
                        return "@@profile.identity_block@@".join(
                            _rev(part)
                            for part in ln.split("@@profile.identity_block@@")
                        )

                    def _gi_render(ln: str) -> str:
                        if "@@profile.identity_block@@" not in ln:
                            return render_fn(ln)
                        return "@@profile.identity_block@@".join(
                            render_fn(part)
                            for part in ln.split("@@profile.identity_block@@")
                        )

                    patched_core_gi = (
                        _patch_core_instruction_preserving_conditionals(
                            raw_core_gi,
                            b_tok,
                            l_tok,
                            brand_id=brand_id,
                            channel=channel,
                            disabled_agents=disabled_agents,
                            disabled_tools=disabled_tools,
                            rev_fn=_gi_rev,
                            render_fn=_gi_render,
                        )
                    )
                else:
                    if id_block and not live_gi_norm.startswith(id_block):
                        raise BuildGateError(
                            f"Cannot backport global_instruction.txt: live "
                            f"modified the generated '# Profile:' identity "
                            f"block; edit profiles/{brand_id}/identity.yaml."
                        )
                    b_after = base_gi_norm[len(id_block) :]
                    l_after = live_gi_norm[len(id_block) :]
                    b_tail = (
                        b_after[2:]
                        if b_after.startswith("\n\n")
                        else b_after.lstrip("\n")
                    )
                    l_tail = (
                        l_after[2:]
                        if l_after.startswith("\n\n")
                        else l_after.lstrip("\n")
                    )
                    patched_core_gi = (
                        _patch_core_instruction_preserving_conditionals(
                            raw_core_gi,
                            b_tail,
                            l_tail,
                            brand_id=brand_id,
                            channel=channel,
                            disabled_agents=disabled_agents,
                            disabled_tools=disabled_tools,
                            rev_fn=_rev,
                            render_fn=render_fn,
                        )
                    )
                core_gi_path.write_text(patched_core_gi, encoding="utf-8")
                _mark_modified(core_gi_path)

                if isinstance(ov_yaml.get("global_instruction"), dict):
                    gi_d = dict(ov_yaml["global_instruction"])
                    gi_d.pop("instruction", None)
                    gi_d.pop("replace_lines", None)
                    gi_d.pop("append_rules", None)
                    if isinstance(gi_d.get("channels"), dict):
                        gi_d["channels"].pop(channel, None)
                    cleaned_gi = _prune_empty_dict(gi_d)
                    if cleaned_gi:
                        ov_yaml["global_instruction"] = cleaned_gi
                    else:
                        ov_yaml.pop("global_instruction", None)

                changes.append(
                    BackportChange(
                        resource_id="app",
                        target_file="core/global_instruction.txt",
                        summary="Backported shared global_instruction.txt",
                    )
                )
            continue

        if res_id.startswith("agents/"):
            agent_id = res_id.split("/", 1)[1]
            ours_aj_raw = _load_bundle_resource_config(
                ours_bundle, "agents", agent_id, label=f"ours {res_id}"
            )
            base_aj_raw = _load_bundle_resource_config(
                unoverridden_bundle,
                "agents",
                agent_id,
                label=f"baseline {res_id}",
            )
            live_aj_raw = _load_bundle_resource_config(
                live_bundle, "agents", agent_id, label=f"live {res_id}"
            )
            ours_aj = normalize_json_obj(ours_aj_raw, is_root=True)
            base_aj = normalize_json_obj(base_aj_raw, is_root=True)
            live_aj = normalize_json_obj(live_aj_raw, is_root=True)
            if ours_aj != live_aj:
                changed_top = {
                    k
                    for k in set(ours_aj) | set(live_aj)
                    if ours_aj.get(k) != live_aj.get(k)
                }
                if not changed_top.issubset({"description"}):
                    raise BuildGateError(
                        f"Cannot backport config keys {sorted(changed_top)} on "
                        f"{res_id} with --target core; use --target brand for "
                        "model/temperature or edit core/manifests."
                    )

            core_ag_dir = core_dir / "agents" / agent_id
            core_aj_path = _find_config_file(
                core_ag_dir, agent_id, core_dir, label=f"core {res_id}"
            )
            if core_aj_path is None:
                raise BuildGateError(
                    f"Missing core agent config under {core_ag_dir} for "
                    f"{res_id}"
                )
            core_aj = load_mapping_strict(
                core_aj_path, context=f"core {res_id}"
            )

            ag_root = dict(ov_yaml.get("agents") or {})
            ag_ov_d = copy.deepcopy(dict(ag_root.get(agent_id) or {}))
            ag_ch_d = dict(ag_ov_d.get("channels") or {})
            ag_ch_entry = dict(ag_ch_d.get(channel) or {})
            summary_parts: list[str] = []

            if ours_aj.get("description") != live_aj.get("description") or (
                is_promotion_only
                and base_aj.get("description") != live_aj.get("description")
            ):
                if "description" in live_aj:
                    core_aj["description"] = _rev(
                        str(live_aj.get("description", ""))
                    )
                else:
                    core_aj.pop("description", None)
                _write_core_mapping_formatted(core_aj_path, core_aj)
                _mark_modified(core_aj_path)
                ag_ov_d.pop("description", None)
                ag_ch_entry.pop("description", None)
                summary_parts.append("description")

            base_in_path = _resolve_agent_instruction_path(
                unoverridden_bundle, agent_id, base_aj_raw
            )
            ours_in_path = _resolve_agent_instruction_path(
                ours_bundle, agent_id, ours_aj_raw
            )
            live_in_path = _resolve_agent_instruction_path(
                live_bundle, agent_id, live_aj_raw
            )
            base_in = _read_optional_text(
                base_in_path, context=f"baseline {res_id} instruction"
            )
            ours_in = _read_optional_text(
                ours_in_path, context=f"ours {res_id} instruction"
            )
            live_in = _read_optional_text(
                live_in_path, context=f"live {res_id} instruction"
            )
            if ours_in.replace("\r\n", "\n").splitlines() != live_in.replace(
                "\r\n", "\n"
            ).splitlines() or (
                is_promotion_only
                and base_in.replace("\r\n", "\n").splitlines()
                != live_in.replace("\r\n", "\n").splitlines()
            ):
                core_in_path = _resolve_agent_instruction_path(
                    core_dir, agent_id, core_aj
                )
                assert_safe_subpath(
                    core_in_path, core_dir, label=f"core {res_id} instruction"
                )
                raw_core_in = _read_optional_text(
                    core_in_path, context=f"core {res_id} instruction"
                )
                patched_in = _patch_core_instruction_preserving_conditionals(
                    raw_core_in,
                    base_in,
                    live_in,
                    brand_id=brand_id,
                    channel=channel,
                    disabled_agents=disabled_agents,
                    disabled_tools=disabled_tools,
                    rev_fn=_rev,
                    render_fn=render_fn,
                )
                core_in_path.parent.mkdir(parents=True, exist_ok=True)
                core_in_path.write_text(patched_in, encoding="utf-8")
                _mark_modified(core_in_path)
                for k_i in ("instruction", "replace_lines", "append_rules"):
                    ag_ov_d.pop(k_i, None)
                    ag_ch_entry.pop(k_i, None)
                summary_parts.append("instruction")

            for rel_py in changed_py_files.get(res_id, ()):
                live_py_p = live_bundle / "agents" / agent_id / rel_py
                core_py_p = core_dir / "agents" / agent_id / rel_py
                assert_safe_subpath(
                    live_py_p, live_bundle, label=f"live {res_id} {rel_py}"
                )
                assert_safe_subpath(
                    core_py_p, core_dir, label=f"core {res_id} {rel_py}"
                )
                raw_py = read_utf8_strict(
                    live_py_p, context=f"live {res_id} {rel_py}"
                )
                rev_py = _reverse_python_code_safely(
                    raw_py,
                    live_py_p,
                    manifests=manifests,
                    environment=environment,
                    rev_pairs=rev_pairs,
                    svc_patterns=svc_patterns,
                    render_fn=render_fn,
                )
                core_py_p.parent.mkdir(parents=True, exist_ok=True)
                core_py_p.write_text(rev_py, encoding="utf-8")
                _mark_modified(core_py_p)
                summary_parts.append(rel_py)

            cleaned_ch_e = _prune_empty_dict(ag_ch_entry)
            if cleaned_ch_e:
                ag_ch_d[channel] = cleaned_ch_e
            else:
                ag_ch_d.pop(channel, None)
            if ag_ch_d:
                ag_ov_d["channels"] = ag_ch_d
            else:
                ag_ov_d.pop("channels", None)
            cleaned_ag_d = _prune_empty_dict(ag_ov_d)
            if cleaned_ag_d:
                ag_root[agent_id] = cleaned_ag_d
            else:
                ag_root.pop(agent_id, None)
            if ag_root:
                ov_yaml["agents"] = ag_root
            else:
                ov_yaml.pop("agents", None)

            changes.append(
                BackportChange(
                    resource_id=res_id,
                    target_file=f"core/agents/{agent_id}",
                    summary=f"Backported core {', '.join(summary_parts)}",
                )
            )
            continue

        if res_id.startswith("tools/"):
            tool_id = res_id.split("/", 1)[1]
            err_sfx = " with --target core."
            ours_tj, _o_oa_c, _o_oa_r = _load_normalized_tool(
                ours_bundle, tool_id, res_id
            )
            base_tj, base_oa_c, base_oa_r = _load_normalized_tool(
                unoverridden_bundle, tool_id, res_id
            )
            live_tj, live_oa_c, live_oa_r = _load_normalized_tool(
                live_bundle, tool_id, res_id
            )
            ours_t_desc, ours_t_url = _extract_tool_json_deltas(
                ours_tj, live_tj, res_id=res_id, error_suffix=err_sfx
            )
            base_t_desc, base_t_url = _extract_tool_json_deltas(
                base_tj, live_tj, res_id=res_id, error_suffix=err_sfx
            )
            new_t_desc = base_t_desc if is_promotion_only else ours_t_desc
            new_t_url = base_t_url if is_promotion_only else ours_t_url

            _validate_openapi_schema_compat(
                res_id,
                base_oa_c,
                live_oa_c,
                base_oa_r,
                live_oa_r,
                new_url=base_t_url,
                new_desc=new_t_desc,
                allow_schema_desc_update=True,
                error_suffix=err_sfx,
            )

            core_tool_dir = core_dir / "tools" / tool_id
            core_tj_path = _find_config_file(
                core_tool_dir, tool_id, core_dir, label=f"core {res_id}"
            )
            if core_tj_path is None:
                raise BuildGateError(
                    f"Missing core tool config under {core_tool_dir} for "
                    f"{res_id}"
                )
            core_tj = load_mapping_strict(
                core_tj_path, context=f"core {res_id}"
            )
            mutated_tj = False
            t_parts: list[str] = []

            to_root = dict(ov_yaml.get("tools") or {})
            to_ov_d = copy.deepcopy(dict(to_root.get(tool_id) or {}))
            to_ch_d = dict(to_ov_d.get("channels") or {})
            to_ch_entry = dict(to_ch_d.get(channel) or {})

            for fld, base_delta, ours_delta in (
                ("description", base_t_desc, ours_t_desc),
                ("url", base_t_url, ours_t_url),
            ):
                if base_delta is None and ours_delta is not None:
                    to_ov_d.pop(fld, None)
                    to_ch_entry.pop(fld, None)
                    t_parts.append(f"{fld} (reverted override)")

            rev_desc: str | None = None
            if new_t_desc is not None and "description" not in _tool_payload(
                live_tj
            ):
                core_payload = _tool_payload(core_tj)
                if isinstance(core_payload, dict):
                    core_payload.pop("description", None)
                mutated_tj = True
                to_ov_d.pop("description", None)
                to_ch_entry.pop("description", None)
                t_parts.append("description (cleared)")
            elif new_t_desc is not None:
                rev_desc = _rev(new_t_desc)
                updated_sub = False
                for sub_k in _TOOL_PAYLOAD_KEYS:
                    if isinstance(core_tj.get(sub_k), dict):
                        core_tj[sub_k]["description"] = rev_desc
                        updated_sub = True
                        break
                if not updated_sub:
                    core_tj["description"] = rev_desc
                mutated_tj = True
                to_ov_d.pop("description", None)
                to_ch_entry.pop("description", None)
                t_parts.append("description")

            if new_t_url is not None:
                rev_url = _reverse_url_tokens(
                    new_t_url,
                    brand_id,
                    rev_pairs,
                    svc_patterns,
                    render_fn,
                )
                core_env_p = core_dir / "environment.json"
                env_has_url = False
                if core_env_p.is_file():
                    assert_safe_subpath(
                        core_env_p, core_dir, label="core environment.json"
                    )
                    env_d = load_json_dict_strict(core_env_p)
                    env_tools = env_d.get("tools")
                    d_name = str(core_tj.get("displayName") or tool_id)
                    if isinstance(env_tools, dict):
                        for kc in dict.fromkeys((d_name, tool_id)):
                            ent = env_tools.get(kc)
                            if isinstance(ent, dict) and isinstance(
                                ent.get("openApiTool"), dict
                            ):
                                ent["openApiTool"]["url"] = rev_url
                                env_has_url = True
                        if env_has_url:
                            _write_core_json_formatted(core_env_p, env_d)
                            _mark_modified(core_env_p)

                oa_cfg = core_tj.get("openApiTool")
                if isinstance(oa_cfg, dict) and (
                    "url" in oa_cfg or not env_has_url
                ):
                    oa_cfg["url"] = rev_url
                    mutated_tj = True

                to_ov_d.pop("url", None)
                to_ch_entry.pop("url", None)
                t_parts.append("url")

            core_oa_p: Path | None = None
            core_oa_cfg = core_tj.get("openApiTool")
            if isinstance(core_oa_cfg, dict):
                raw_schema = core_oa_cfg.get("openApiSchema")
                if isinstance(raw_schema, str) and raw_schema.strip():
                    cand_oa_p = core_dir / raw_schema.strip()
                    if cand_oa_p.exists() or cand_oa_p.is_symlink():
                        assert_safe_subpath(
                            cand_oa_p,
                            core_dir,
                            label=f"core {res_id} OpenAPI schema",
                        )
                        if cand_oa_p.is_file():
                            core_oa_p = cand_oa_p
            if core_oa_p is None:
                for oa_name in ("open_api_schema.yaml", "open_api_schema.yml"):
                    cand_oa_p = core_tool_dir / "open_api_tool" / oa_name
                    if cand_oa_p.exists() or cand_oa_p.is_symlink():
                        assert_safe_subpath(
                            cand_oa_p,
                            core_dir,
                            label=f"core {res_id} OpenAPI schema",
                        )
                        if cand_oa_p.is_file():
                            core_oa_p = cand_oa_p
                            break

            if core_oa_p is not None and core_oa_p.is_file():
                orig_core_oa = read_utf8_strict(
                    core_oa_p, context=f"core {res_id} OpenAPI schema"
                )
                orig_srv_tok: str | None = None
                for ln in orig_core_oa.splitlines():
                    m_orig = _OPENAPI_SERVER_URL_RE.match(ln)
                    if m_orig:
                        orig_srv_tok = ln[len(m_orig.group(1)) :].strip()
                        break

                desired_srv_tok = orig_srv_tok
                if (
                    new_t_url is not None
                    and orig_srv_tok != CES_ENV_VAR_PLACEHOLDER
                ):
                    ref_oa = (
                        live_oa_r
                        if (base_oa_c != live_oa_c and live_oa_r is not None)
                        else orig_core_oa
                    )
                    ref_doc = _safe_load_yaml_text(
                        render_fn(ref_oa),
                        context=f"{res_id} OpenAPI schema",
                    )
                    ref_paths = (
                        ref_doc.get("paths")
                        if isinstance(ref_doc, dict)
                        else None
                    )
                    parsed_raw = urlsplit(new_t_url)
                    srv_raw = (
                        f"{parsed_raw.scheme}://{parsed_raw.netloc}"
                        if (
                            parsed_raw.scheme
                            and parsed_raw.netloc
                            and isinstance(ref_paths, dict)
                            and parsed_raw.path in ref_paths
                        )
                        else new_t_url
                    )
                    desired_srv_tok = _reverse_url_tokens(
                        srv_raw,
                        brand_id,
                        rev_pairs,
                        svc_patterns,
                        render_fn,
                    )

                if base_oa_c != live_oa_c and live_oa_r is not None:
                    cand_oa = live_oa_r
                    if (
                        new_t_desc is not None
                        and rev_desc is not None
                        and new_t_desc in cand_oa
                    ):
                        maybe_tok = cand_oa.replace(new_t_desc, rev_desc)
                        if _canonical_openapi_schema_lines(
                            render_fn(maybe_tok)
                        ) == _canonical_openapi_schema_lines(live_oa_r):
                            cand_oa = maybe_tok
                    out_lines: list[str] = []
                    for line in cand_oa.splitlines():
                        m_srv = _OPENAPI_SERVER_URL_RE.match(line)
                        if m_srv and desired_srv_tok is not None:
                            out_lines.append(
                                f"{m_srv.group(1)}{desired_srv_tok}"
                            )
                        else:
                            out_lines.append(line)
                    rebuilt_oa = "\n".join(out_lines) + (
                        "\n" if cand_oa.endswith("\n") else ""
                    )
                    if rebuilt_oa != orig_core_oa:
                        core_oa_p.write_text(rebuilt_oa, encoding="utf-8")
                        _mark_modified(core_oa_p)
                        if not t_parts:
                            t_parts.append(core_oa_p.name)
                elif (
                    desired_srv_tok is not None
                    and desired_srv_tok != orig_srv_tok
                ):
                    out_lines = []
                    for line in orig_core_oa.splitlines():
                        m_srv = _OPENAPI_SERVER_URL_RE.match(line)
                        if m_srv:
                            out_lines.append(
                                f"{m_srv.group(1)}{desired_srv_tok}"
                            )
                        else:
                            out_lines.append(line)
                    core_oa_p.write_text(
                        "\n".join(out_lines)
                        + ("\n" if orig_core_oa.endswith("\n") else ""),
                        encoding="utf-8",
                    )
                    _mark_modified(core_oa_p)

            if mutated_tj:
                _write_core_mapping_formatted(core_tj_path, core_tj)
                _mark_modified(core_tj_path)

            for rel_py in changed_py_files.get(res_id, ()):
                live_py_p = live_bundle / "tools" / tool_id / rel_py
                core_py_p = core_dir / "tools" / tool_id / rel_py
                assert_safe_subpath(
                    live_py_p, live_bundle, label=f"live {res_id} {rel_py}"
                )
                assert_safe_subpath(
                    core_py_p, core_dir, label=f"core {res_id} {rel_py}"
                )
                raw_py = read_utf8_strict(
                    live_py_p, context=f"live {res_id} {rel_py}"
                )
                rev_py = _reverse_python_code_safely(
                    raw_py,
                    live_py_p,
                    manifests=manifests,
                    environment=environment,
                    rev_pairs=rev_pairs,
                    svc_patterns=svc_patterns,
                    render_fn=render_fn,
                )
                core_py_p.parent.mkdir(parents=True, exist_ok=True)
                core_py_p.write_text(rev_py, encoding="utf-8")
                _mark_modified(core_py_p)
                t_parts.append(rel_py)

            cleaned_tch = _prune_empty_dict(to_ch_entry)
            if cleaned_tch:
                to_ch_d[channel] = cleaned_tch
            else:
                to_ch_d.pop(channel, None)
            if to_ch_d:
                to_ov_d["channels"] = to_ch_d
            else:
                to_ov_d.pop("channels", None)
            cleaned_to = _prune_empty_dict(to_ov_d)
            if cleaned_to:
                to_root[tool_id] = cleaned_to
            else:
                to_root.pop(tool_id, None)
            if to_root:
                ov_yaml["tools"] = to_root
            else:
                ov_yaml.pop("tools", None)

            if not t_parts:
                raise BuildGateError(
                    f"Cannot backport structural tool change on "
                    f"{res_id}{err_sfx}"
                )
            changes.append(
                BackportChange(
                    resource_id=res_id,
                    target_file=f"core/tools/{tool_id}",
                    summary=f"Backported core tool {', '.join(t_parts)}",
                )
            )
            continue

        if res_id.startswith("guardrails/"):
            gr_id = res_id.split("/", 1)[1]
            ours_gj = normalize_json_obj(
                _load_bundle_resource_config(
                    ours_bundle, "guardrails", gr_id, label=f"ours {res_id}"
                ),
                is_root=True,
            )
            live_gj = normalize_json_obj(
                _load_bundle_resource_config(
                    live_bundle, "guardrails", gr_id, label=f"live {res_id}"
                ),
                is_root=True,
            )
            core_gr_dir = core_dir / "guardrails" / gr_id
            core_gj_path = _find_config_file(
                core_gr_dir, gr_id, core_dir, label=f"core {res_id}"
            )
            if core_gj_path is None:
                raise BuildGateError(
                    f"Missing core guardrail config under {core_gr_dir} for "
                    f"{res_id}"
                )
            core_gj = load_mapping_strict(
                core_gj_path, context=f"core {res_id}"
            )
            gr_parts: list[str] = []

            if ours_gj.get("description") != live_gj.get("description"):
                if "description" in live_gj:
                    core_gj["description"] = _rev(
                        str(live_gj.get("description", ""))
                    )
                else:
                    core_gj.pop("description", None)
                gr_parts.append("description")
            if (
                isinstance(ours_gj.get("llmPolicy"), dict)
                and isinstance(live_gj.get("llmPolicy"), dict)
                and isinstance(core_gj.get("llmPolicy"), dict)
                and ours_gj["llmPolicy"].get("prompt")
                != live_gj["llmPolicy"].get("prompt")
            ):
                core_gj["llmPolicy"]["prompt"] = _rev(
                    str(live_gj["llmPolicy"].get("prompt", ""))
                )
                gr_parts.append("llmPolicy.prompt")
            if (
                isinstance(ours_gj.get("llmPromptSecurity"), dict)
                and isinstance(live_gj.get("llmPromptSecurity"), dict)
                and isinstance(core_gj.get("llmPromptSecurity"), dict)
            ):
                o_cp = ours_gj["llmPromptSecurity"].get("customPolicy")
                l_cp = live_gj["llmPromptSecurity"].get("customPolicy")
                c_cp = core_gj["llmPromptSecurity"].get("customPolicy")
                if (
                    isinstance(o_cp, dict)
                    and isinstance(l_cp, dict)
                    and isinstance(c_cp, dict)
                    and o_cp.get("prompt") != l_cp.get("prompt")
                ):
                    c_cp["prompt"] = _rev(str(l_cp.get("prompt", "")))
                    gr_parts.append("llmPromptSecurity.customPolicy.prompt")

            if not gr_parts:
                raise BuildGateError(
                    f"Cannot backport structural guardrail change on {res_id}."
                )
            _write_core_mapping_formatted(core_gj_path, core_gj)
            _mark_modified(core_gj_path)
            changes.append(
                BackportChange(
                    resource_id=res_id,
                    target_file=f"core/guardrails/{gr_id}/{core_gj_path.name}",
                    summary=f"Backported core guardrail {', '.join(gr_parts)}",
                )
            )
            continue

        raise BuildGateError(
            f"Unsupported resource {res_id!r} for --target core."
        )

    cleaned_ov = _prune_empty_dict(ov_yaml)
    if cleaned_ov != _prune_empty_dict(orig_ov_yaml):
        if set(cleaned_ov.keys()) <= {"profile_id"}:
            if ov_path.is_file():
                ov_path.unlink()
                _mark_modified(ov_path)
        else:
            _dump_yaml_clean(ov_path, cleaned_ov)
            _mark_modified(ov_path)

    return modified_paths, changes


def _has_core_promotable_diff(
    res_id: str,
    unoverridden_bundle: Path,
    live_bundle: Path,
    *,
    unoverridden_norm: Mapping[str, NormalizedResource],
    live_norm: Mapping[str, NormalizedResource],
) -> bool:
    if unoverridden_norm[res_id].sha256 == live_norm[res_id].sha256:
        return False
    if res_id == "app":
        u_gi = _read_optional_text(
            unoverridden_bundle / "global_instruction.txt"
        )
        l_gi = _read_optional_text(live_bundle / "global_instruction.txt")
        return (
            u_gi.replace("\r\n", "\n").splitlines()
            != l_gi.replace("\r\n", "\n").splitlines()
        )
    if res_id.startswith("agents/"):
        ag_id = res_id.split("/", 1)[1]
        u_aj_raw = _load_bundle_resource_config(
            unoverridden_bundle, "agents", ag_id, label=f"baseline {res_id}"
        )
        l_aj_raw = _load_bundle_resource_config(
            live_bundle, "agents", ag_id, label=f"live {res_id}"
        )
        u_aj = normalize_json_obj(u_aj_raw, is_root=True)
        l_aj = normalize_json_obj(l_aj_raw, is_root=True)
        if u_aj.get("description") != l_aj.get("description"):
            return True
        u_in = _read_optional_text(
            _resolve_agent_instruction_path(
                unoverridden_bundle, ag_id, u_aj_raw
            )
        )
        l_in = _read_optional_text(
            _resolve_agent_instruction_path(live_bundle, ag_id, l_aj_raw)
        )
        return (
            u_in.replace("\r\n", "\n").splitlines()
            != l_in.replace("\r\n", "\n").splitlines()
        )
    return res_id.startswith(("tools/", "guardrails/"))


def _make_tree_writable(root: Path) -> None:
    """Ensure copied temporary directories and files are owner-writable."""
    for dirpath, _dirnames, filenames in os.walk(root):
        dp = Path(dirpath)
        with contextlib.suppress(OSError):
            os.chmod(
                dp,
                dp.stat().st_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR,
            )
        for fname in filenames:
            fp = dp / fname
            if not fp.is_symlink():
                with contextlib.suppress(OSError):
                    os.chmod(
                        fp, fp.stat().st_mode | stat.S_IRUSR | stat.S_IWUSR
                    )


def _assert_no_symlink_in_path(raw_path: Path, *, label: str) -> None:
    """Reject symlinks on ``raw_path`` or any existing ancestor component."""
    if raw_path.is_symlink():
        raise BuildGateError(
            f"Symlinks are not permitted for {label}: {raw_path}"
        )
    if raw_path.is_absolute():
        curr = Path(raw_path.anchor)
        parts = raw_path.parts[1:]
    else:
        cwd = Path.cwd()
        curr = Path(cwd.anchor)
        parts = (*cwd.parts[1:], *raw_path.parts)
    darwin_os_links = (
        {Path("/tmp"), Path("/var"), Path("/etc")}
        if sys.platform == "darwin"
        else set()
    )
    for part in parts:
        curr = curr / part
        if curr in darwin_os_links:
            continue
        if curr.is_symlink():
            raise BuildGateError(
                f"Symlinks are not permitted in {label} path {raw_path} "
                f"(symlink component: {curr})"
            )


def _collect_lock_snapshots(
    *,
    lockfile_path: Path | str | None,
    eff_lock: Path | None,
    dist_root: Path | None,
) -> dict[Path, bytes | None]:
    tracked: list[Path] = []
    for lp_raw in (lockfile_path, eff_lock):
        if lp_raw is not None:
            lp_res = Path(lp_raw).resolve()
            if lp_res not in tracked:
                tracked.append(lp_res)
    if dist_root is not None and _has_app_marker(dist_root):
        sib_lp = (dist_root.parent / f"{dist_root.name}.lock.json").resolve()
        if sib_lp not in tracked:
            tracked.append(sib_lp)
    return {lp: (lp.read_bytes() if lp.is_file() else None) for lp in tracked}


def _restore_lock_snapshots(snapshots: Mapping[Path, bytes | None]) -> None:
    for lp, data in snapshots.items():
        if data is None:
            lp.unlink(missing_ok=True)
        elif not lp.is_file() or lp.read_bytes() != data:
            lp.parent.mkdir(parents=True, exist_ok=True)
            lp.write_bytes(data)


def _snapshot_directory_bytes(
    root: Path,
) -> tuple[dict[Path, bytes], set[Path]]:
    files: dict[Path, bytes] = {}
    dirs: set[Path] = set()
    for p in sorted(root.rglob("*")):
        if ".git" in p.relative_to(root).parts:
            continue
        if p.is_symlink():
            raise BuildGateError(f"Symlinks are not permitted in {root}: {p}")
        if p.is_dir():
            dirs.add(p)
        elif p.is_file():
            files[p] = p.read_bytes()
    return files, dirs


def _restore_directory_bytes(
    root: Path, snapshot: tuple[dict[Path, bytes], set[Path]]
) -> None:
    snap_files, snap_dirs = snapshot
    current_files = {
        p
        for p in root.rglob("*")
        if p.is_file()
        and not p.is_symlink()
        and ".git" not in p.relative_to(root).parts
    }
    for extra in sorted(current_files - set(snap_files.keys())):
        extra.unlink()
    current_dirs = {
        p
        for p in root.rglob("*")
        if p.is_dir()
        and not p.is_symlink()
        and ".git" not in p.relative_to(root).parts
    }
    for extra_dir in sorted(
        current_dirs - snap_dirs, key=lambda d: len(d.parts), reverse=True
    ):
        with contextlib.suppress(OSError):
            extra_dir.rmdir()
    for path, data in sorted(snap_files.items()):
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or path.read_bytes() != data:
            path.write_bytes(data)


def _validate_git_branch_precheck(
    repo_dir: Path, git_branch: str
) -> tuple[str, str]:
    branch = git_branch.strip()
    if (
        not branch
        or branch != git_branch
        or branch.startswith("-")
        or branch.endswith((".lock", "/"))
        or ".." in branch
        or "//" in branch
        or "@{" in branch
        or not _GIT_BRANCH_RE.match(branch)
    ):
        raise BuildGateError(
            f"Invalid --git-branch name {git_branch!r}; expected safe "
            "alphanumeric/slash/dash ref."
        )
    env = dict(os.environ)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    inside = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "--is-inside-work-tree"],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if inside.returncode != 0 or inside.stdout.strip().lower() != "true":
        raise BuildGateError(
            f"Cannot checkout --git-branch {branch!r}: {repo_dir} is not "
            "inside a git worktree."
        )

    fmt_check = subprocess.run(
        [
            "git",
            "-C",
            str(repo_dir),
            "check-ref-format",
            "--branch",
            branch,
        ],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if fmt_check.returncode != 0:
        raise BuildGateError(
            f"Invalid --git-branch name {git_branch!r}: rejected by git "
            "check-ref-format."
        )

    dirty = subprocess.run(
        [
            "git",
            "-C",
            str(repo_dir),
            "status",
            "--porcelain",
            "--untracked-files=no",
        ],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if dirty.returncode != 0 or dirty.stdout.strip():
        raise BuildGateError(
            f"Refusing to checkout --git-branch {branch!r}: working tree at "
            f"{repo_dir} has uncommitted tracked changes."
        )

    sym = subprocess.run(
        [
            "git",
            "-C",
            str(repo_dir),
            "symbolic-ref",
            "--quiet",
            "--short",
            "HEAD",
        ],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if sym.returncode == 0:
        current_branch = sym.stdout.strip()
    else:
        rev = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "--abbrev-ref", "HEAD"],
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        current_branch = rev.stdout.strip() if rev.returncode == 0 else ""

    if branch != current_branch:
        exists = subprocess.run(
            [
                "git",
                "-C",
                str(repo_dir),
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/heads/{branch}",
            ],
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if exists.returncode == 0:
            raise BuildGateError(
                f"Refusing to clobber existing local git branch {branch!r}."
            )
    return branch, current_branch


def _checkout_git_branch(
    repo_dir: Path, branch: str, current_branch: str
) -> str:
    if branch == current_branch:
        return branch
    env = dict(os.environ)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    try:
        subprocess.run(
            ["git", "-C", str(repo_dir), "checkout", "-b", branch],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise BuildGateError(
            f"Failed to checkout git branch {branch!r}: {exc.stderr.strip()}"
        ) from exc
    return branch


def _relative_source_label(mf: Path, repo_root: Path | str | None) -> str:
    resolved = mf.resolve()
    if repo_root is not None:
        try:
            return resolved.relative_to(Path(repo_root).resolve()).as_posix()
        except ValueError:
            pass
    parts = resolved.parts
    for anchor in ("manifests", "core"):
        if anchor in parts:
            idx = len(parts) - 1 - parts[::-1].index(anchor)
            return "/".join(parts[idx:])
    return resolved.name


def format_backport_pr_body(
    result: BackportResult,
    *,
    repo_root: Path | str | None = None,
) -> str:
    """Render a review-ready Git PR body with lint and SHA-256 proof."""
    lint_line = (
        f"- **cxas lint**: `PASS ({result.lint_errors} errors, "
        f"{result.lint_warnings} warnings)`"
        if result.lint_ran
        else "- **cxas lint**: `SKIPPED (--no-lint)`"
    )
    title_suffix = " [DRY RUN]" if result.dry_run else ""
    heading = f"## CES Console -> Manifest Backport (`{result.app_id}`)"
    lines = [
        f"{heading}{title_suffix}",
        "",
        f"- **App ID**: `{result.app_id}` (`brand={result.brand_id}`, "
        f"`channel={result.channel}`, `environment={result.environment}`)",
        f"- **Target & Scope**: `target={result.target}`, "
        f"`scope={result.scope}`",
        lint_line,
        "- **Round-Trip SHA-256 Proof**: `VERIFIED` "
        "(`build(backport(live)) == live`)",
    ]
    if result.dry_run:
        lines.append("- **Mode**: `DRY RUN` *(no files modified on disk)*")
    if result.unchanged_sibling_bundles:
        sib_str = ", ".join(f"`{b}`" for b in result.unchanged_sibling_bundles)
        lines.append(
            f"- **Unchanged Sibling Bundles (0 Blast Radius)**: {sib_str}"
        )
    elif result.target == "core":
        lines.append(
            "- **Unchanged Sibling Bundles**: *(shared core update across "
            "applicable bundles)*"
        )
    else:
        lines.append(
            "- **Unchanged Sibling Bundles**: *(no sibling bundles declared)*"
        )
    if result.git_branch:
        lines.append(f"- **Git Branch**: `{result.git_branch}`")

    lines.append("")
    lines.append("### Modified Source & Manifest Files")
    if result.modified_files:
        for mf in result.modified_files:
            lines.append(f"- `{_relative_source_label(mf, repo_root)}`")
    else:
        lines.append("- *(No changes — live export already matches source)*")

    lines.append("")
    lines.append("### Backported Resource Deltas")
    if result.changes:
        for chg in result.changes:
            lines.append(
                f"- `{chg.resource_id}` -> `{chg.target_file}`: {chg.summary}"
            )
    else:
        lines.append("- *(0 resource deltas)*")

    lines.append("")
    lines.append("### Verified Built Bundles")
    for vb in result.verified_bundles:
        lines.append(f"- `{vb}`")

    return "\n".join(lines) + "\n"


def _read_deployment_triplet(
    bundle_dir: Path | None,
) -> tuple[str | None, str | None, str | None]:
    if bundle_dir is None or not bundle_dir.is_dir():
        return None, None, None
    for dep in sorted(bundle_dir.glob("deployments/*.yaml")):
        if dep.is_symlink():
            continue
        try:
            data = (
                yaml.load(
                    dep.read_text(encoding="utf-8"), Loader=Yaml12SafeLoader
                )
                or {}
            )
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
        if isinstance(data, dict) and data.get("profileId"):
            return (
                str(data["profileId"]) if data.get("profileId") else None,
                str(data["channel"]) if data.get("channel") else None,
                str(data["environment"]) if data.get("environment") else None,
            )
    return None, None, None


def _resolve_bundle_for_app(
    root_or_bundle: Path, brand_id: str, channel: str
) -> Path:
    ch_low = channel.lower()
    for cand_id in (
        f"{brand_id}/{ch_low}",
        f"{brand_id}-{ch_low}",
        f"{brand_id}/{channel}",
        f"{brand_id}-{channel}",
    ):
        cand = _resolve_app_bundle_dir(root_or_bundle, cand_id)
        if cand.is_dir() and _has_app_marker(cand):
            return cand
    return _resolve_app_bundle_dir(root_or_bundle, f"{brand_id}/{ch_low}")


def _infer_app_from_dist(
    dist_root: Path,
    live_bundle: Path,
    manifests: Manifests,
    *,
    brand_hint: str | None,
    channel_hint: str | None,
) -> tuple[str | None, str | None, list[tuple[str, str]]]:
    """Infer `(brand, channel)` from `dist/` only when provably unique."""
    declared = [
        (b_id, ch)
        for b_id, pol in sorted(manifests.policies.items())
        if not brand_hint or b_id == brand_hint
        for ch in sorted(pol.channels)
        if not channel_hint or ch == channel_hint
    ]
    if not declared:
        return None, None, []
    try:
        live_norm = extract_normalized_resources(live_bundle)
    except BuildGateError:
        return None, None, declared
    compatible: list[tuple[str, str, int]] = []
    for b_id, ch in declared:
        cand = _resolve_bundle_for_app(dist_root, b_id, ch)
        if not _has_app_marker(cand):
            return None, None, declared
        try:
            c_norm = extract_normalized_resources(cand)
        except BuildGateError:
            return None, None, declared
        if set(c_norm) != set(live_norm):
            continue
        score = sum(
            1
            for r_id, l_res in live_norm.items()
            if c_norm[r_id].sha256 == l_res.sha256
        )
        compatible.append((b_id, ch, score))
    if len(compatible) != 1 or compatible[0][2] == 0:
        return None, None, [(b, c) for b, c, _s in compatible] or declared
    return compatible[0][0], compatible[0][1], [compatible[0][:2]]


def _replace_dir_contents(src_dir: Path, dst_dir: Path) -> None:
    dst_dir.mkdir(parents=True, exist_ok=True)
    for existing in sorted(dst_dir.iterdir()):
        if existing.is_dir() and not existing.is_symlink():
            shutil.rmtree(existing)
        else:
            existing.unlink()
    for item in sorted(src_dir.iterdir()):
        target = dst_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)


def _refresh_dist_and_lock(
    *,
    core_dir: Path,
    manifests_dir: Path,
    manifests: Manifests,
    dist_root: Path,
    brand_id: str,
    channel: str,
    environment: str,
    lockfile_path: Path | str | None,
    eff_lock: Path | None,
    lock_source: Path | None = None,
) -> Path | None:
    """Rebuild `dist_root` and re-lock ONLY the reconciled target app."""
    ch_low = channel.lower()
    dash_id = f"{brand_id}-{ch_low}"

    def _lock(target_dir: Path, lock_path: Path | str) -> Path:
        if lock_source is None:
            return write_lockfile(target_dir, lock_path)
        return write_lockfile(lock_source, lock_path, app_id=target_dir.name)

    # 1. Single-bundle directory
    if _has_app_marker(dist_root):
        sibling_lock = dist_root.parent / f"{dist_root.name}.lock.json"
        want_locks = (
            lockfile_path is not None
            or eff_lock is not None
            or sibling_lock.is_file()
        )
        with tempfile.TemporaryDirectory() as tmp_s_str:
            tmp_out = Path(tmp_s_str) / "single_bundle"
            build_app(
                core_dir=core_dir,
                manifests_dir=manifests_dir,
                profile_id=brand_id,
                channel=channel,
                environment=environment,
                out_dir=tmp_out,
                write_lock=False,
            )
            _replace_dir_contents(tmp_out, dist_root)
        if lockfile_path is not None:
            return _lock(dist_root, lockfile_path)
        if want_locks:
            return _lock(dist_root, eff_lock or sibling_lock)
        return None

    # 2. 1-level `<brand_id>-<channel_low>` layout
    is_one_level = any(
        _has_app_marker(dist_root / f"{b_id}-{ch.lower()}")
        or (dist_root / f"{b_id}-{ch.lower()}.lock.json").is_file()
        for b_id, pol in manifests.policies.items()
        for ch in pol.channels
    ) and not any(
        _has_app_marker(dist_root / b_id / ch.lower())
        for b_id, pol in manifests.policies.items()
        for ch in pol.channels
    )
    if is_one_level:
        want_locks = (
            lockfile_path is not None
            or eff_lock is not None
            or any(dist_root.glob("*.lock.json"))
        )
        for b_id, pol in manifests.policies.items():
            for ch in pol.channels:
                build_app(
                    core_dir=core_dir,
                    manifests_dir=manifests_dir,
                    profile_id=b_id,
                    channel=ch,
                    environment=environment,
                    out_dir=dist_root / f"{b_id}-{ch.lower()}",
                    write_lock=False,
                )
        target_one = dist_root / dash_id
        if lockfile_path is not None:
            return _lock(target_one, lockfile_path)
        if want_locks:
            return _lock(
                target_one, eff_lock or (dist_root / f"{dash_id}.lock.json")
            )
        return None

    # 3. Standard 2-level `dist/<brand_id>/<channel_low>` layout
    want_locks = (
        lockfile_path is not None
        or eff_lock is not None
        or any(dist_root.rglob("*.lock.json"))
    )
    build_all(
        core_dir=core_dir,
        manifests_dir=manifests_dir,
        out_root=dist_root,
        environment=environment,
        write_locks=False,
    )
    target_two = dist_root / brand_id / ch_low
    if lockfile_path is not None:
        return _lock(target_two, lockfile_path)
    if want_locks:
        return _lock(
            target_two,
            eff_lock or (dist_root / brand_id / f"{ch_low}.lock.json"),
        )
    return eff_lock


def _run_lint_report(
    bundle_dir: Path,
    *,
    manifests_dir: Path,
    brand_id: str,
    channel: str,
) -> Any:
    """Run cxas lint (using polymorphic `collect_lint_report` if available)."""
    collect_fn = getattr(_linter_mod, "collect_lint_report", None)
    if callable(collect_fn):
        return collect_fn(
            bundle_dir,
            manifests_dir=manifests_dir,
            profile=brand_id,
            channel=channel,
        )
    registry = _linter_mod.build_registry()
    config = _linter_mod.LintConfig.load(bundle_dir)
    app_dir = bundle_dir / config.app_dir
    evals_dir = bundle_dir / config.evals_dir
    discovery = _linter_mod.Discovery(app_dir, evals_dir)
    context = _linter_mod.build_context(bundle_dir, config, discovery)
    report = _linter_mod.LintReport()
    _linter_mod.run_rules(registry, config, context, discovery, report)
    return report


def _enforce_lint_clean(
    bundle_dir: Path,
    *,
    manifests_dir: Path,
    brand_id: str,
    channel: str,
    stage_label: str,
) -> tuple[int, int]:
    report = _run_lint_report(
        bundle_dir,
        manifests_dir=manifests_dir,
        brand_id=brand_id,
        channel=channel,
    )
    err_cnt = len(report.errors)
    warn_cnt = len(report.warnings)
    if err_cnt > 0:
        formatted = "; ".join(
            f"[{r.rule_id}] {r.file}: {r.message}" for r in report.errors
        )
        raise BuildGateError(
            f"Refusing backport for {brand_id}/{channel.lower()}: "
            f"{stage_label} failed cxas lint ({err_cnt} error(s)): {formatted}"
        )
    return err_cnt, warn_cnt


def backport_live_to_source(
    core_dir: Path | str,
    manifests_dir: Path | str,
    live_dir: Path | str,
    *,
    brand_id: str | None = None,
    channel: str | None = None,
    environment: str | None = None,
    dist_dir: Path | str | None = None,
    lockfile_path: Path | str | None = None,
    target: BackportTarget = "brand",
    scope: BackportScope = "channel",
    conflicts_dir: Path | str | None = None,
    force_live: Sequence[str] = (),
    run_lint: bool = True,
    dry_run: bool = False,
    pr_body_out: Path | str | None = None,
    git_branch: str | None = None,
) -> BackportResult:
    """Back-port Console UI edits into `manifests/` or shared `core/`."""
    if target not in ("brand", "core"):
        raise BuildGateError(
            f"Unsupported backport target {target!r}; "
            "expected 'brand' or 'core'."
        )
    if scope not in ("channel", "brand"):
        raise BuildGateError(
            f"Unsupported backport scope {scope!r}; "
            "expected 'channel' or 'brand'."
        )

    c_raw = Path(core_dir)
    m_raw = Path(manifests_dir)
    l_raw = Path(live_dir)
    for raw_p, lbl in (
        (c_raw, "core directory"),
        (m_raw, "manifests directory"),
        (l_raw, "live directory"),
    ):
        if raw_p.is_symlink():
            raise BuildGateError(
                f"Symlinks are not permitted in {lbl}: {raw_p}"
            )

    c_dir = c_raw.resolve()
    m_dir = m_raw.resolve()
    if not c_dir.is_dir():
        raise BuildGateError(f"Core directory does not exist: {c_dir}")
    if not m_dir.is_dir():
        raise BuildGateError(f"Manifests directory does not exist: {m_dir}")

    _validate_dir_no_symlinks_gate(c_dir, label="core directory")
    _validate_dir_no_symlinks_gate(m_dir, label="manifests directory")

    repo_root = (
        c_dir.parent
        if c_dir.parent == m_dir.parent
        else (
            m_dir.parent if (m_dir.parent / ".git").exists() else c_dir.parent
        )
    )
    git_precheck: tuple[str, str] | None = None
    if git_branch is not None:
        if dry_run:
            raise BuildGateError(
                "Cannot combine --git-branch with --dry-run; remove "
                "--git-branch to preview changes without touching Git."
            )
        git_precheck = _validate_git_branch_precheck(repo_root, git_branch)

    try:
        manifests = load_manifests(m_dir)
    except ManifestError as exc:
        raise BuildGateError(str(exc)) from exc

    live_root = l_raw.resolve()
    if not live_root.is_dir():
        raise BuildGateError(f"Live directory does not exist: {live_root}")
    _validate_dir_no_symlinks_gate(live_root, label="live directory")

    protected_dirs = (
        (c_dir, "core"),
        (m_dir, "manifests"),
        (live_root, "live"),
    )
    for out_raw, out_label in (
        (dist_dir, "--dist"),
        (conflicts_dir, "--conflicts-dir"),
        (lockfile_path, "--lockfile"),
        (pr_body_out, "--pr-body-out"),
    ):
        if out_raw is None:
            continue
        raw_out_p = Path(out_raw)
        _assert_no_symlink_in_path(raw_out_p, label=out_label)
        res_out_p = raw_out_p.resolve()
        for protected, p_label in protected_dirs:
            if _paths_overlap(res_out_p, protected):
                raise BuildGateError(
                    f"{out_label} path '{res_out_p}' must not overlap with "
                    f"{p_label} directory '{protected}'."
                )

    dist_root = Path(dist_dir).resolve() if dist_dir is not None else None

    prelim_live = live_root if _has_app_marker(live_root) else None
    prelim_dist = (
        dist_root
        if (dist_root is not None and _has_app_marker(dist_root))
        else None
    )
    if prelim_dist is None and dist_root is not None and brand_id and channel:
        cand_d = _resolve_bundle_for_app(dist_root, brand_id, channel)
        if cand_d.is_dir():
            prelim_dist = cand_d
    if prelim_live is None and brand_id and channel:
        cand_l = _resolve_bundle_for_app(live_root, brand_id, channel)
        if cand_l.is_dir():
            prelim_live = cand_l

    d_brand, d_chan, d_env = _read_deployment_triplet(prelim_dist)
    l_brand, l_chan, l_env = _read_deployment_triplet(prelim_live)

    eff_brand = brand_id or d_brand or l_brand
    eff_channel = channel or d_chan or l_chan
    eff_env = environment or d_env or l_env
    if eff_env is None and len(manifests.envs) == 1:
        eff_env = next(iter(manifests.envs.keys()))

    infer_note = ""
    if (
        (not eff_brand or not eff_channel)
        and dist_root is not None
        and prelim_dist is None
        and prelim_live is not None
    ):
        inf_brand, inf_channel, top = _infer_app_from_dist(
            dist_root,
            prelim_live,
            manifests,
            brand_hint=eff_brand,
            channel_hint=eff_channel,
        )
        eff_brand = eff_brand or inf_brand
        eff_channel = eff_channel or inf_channel
        if inf_channel is None and top:
            infer_note = (
                " Ambiguous or unproven dist/ candidates: "
                + ", ".join(f"{b}/{c}" for b, c in top)
                + "."
            )

    if not eff_brand or not eff_channel or not eff_env:
        raise BuildGateError(
            "Could not resolve (brand_id, channel, environment) from "
            "arguments or deployments/*.yaml; pass --brand, --channel, and "
            "--env explicitly." + infer_note
        )

    try:
        manifests.profile(eff_brand)
        pol = manifests.policy(eff_brand)
        manifests.env(eff_env)
    except ManifestError as exc:
        raise BuildGateError(str(exc)) from exc

    if eff_channel not in pol.channels:
        raise BuildGateError(
            f"Channel {eff_channel!r} is not declared for brand "
            f"{eff_brand!r} in channel_policy.yaml "
            f"(available: {sorted(pol.channels)})."
        )

    canonical_app_id = f"{eff_brand}/{eff_channel.lower()}"
    dash_app_id = f"{eff_brand}-{eff_channel.lower()}"

    live_bundle = (
        prelim_live
        if prelim_live is not None
        else _resolve_bundle_for_app(live_root, eff_brand, eff_channel)
    )
    if not live_bundle.is_dir() or not _has_app_marker(live_bundle):
        raise BuildGateError(
            f"Live bundle directory is missing or has no app config: "
            f"{live_bundle}"
        )

    eff_lock: Path | None = None
    if lockfile_path is not None:
        eff_lock = Path(lockfile_path).resolve()
        if not eff_lock.is_file():
            raise BuildGateError(
                f"Explicit lockfile does not exist: {eff_lock}"
            )
    elif dist_root is not None:
        dist_bundle = _resolve_bundle_for_app(dist_root, eff_brand, eff_channel)
        lock_candidates = [
            dist_bundle.parent / f"{dist_bundle.name}.lock.json",
            dist_root / f"{dash_app_id}.lock.json",
            dist_root / eff_brand / f"{eff_channel.lower()}.lock.json",
            dist_bundle.parent / f"{dash_app_id}.lock.json",
        ]
        for cand_lock in lock_candidates:
            if cand_lock.is_file():
                eff_lock = cand_lock
                break

    with tempfile.TemporaryDirectory() as tmp_work_str:
        work_root = Path(tmp_work_str)
        pre_dist_root = work_root / "pre_dist"
        try:
            pre_reports = build_all(c_dir, m_dir, pre_dist_root, eff_env)
        except (BuildError, ManifestError) as exc:
            raise BuildGateError(str(exc)) from exc

        pre_norm_by_app: dict[str, dict[str, NormalizedResource]] = {
            f"{rep.profile_id}/{rep.channel.lower()}": (
                extract_normalized_resources(rep.out_dir)
            )
            for rep in pre_reports
        }
        pre_hashes: dict[str, dict[str, str]] = {
            b_id: {r_id: r.sha256 for r_id, r in r_map.items()}
            for b_id, r_map in pre_norm_by_app.items()
        }
        ours_bundle = pre_dist_root / eff_brand / eff_channel.lower()
        ours_norm = pre_norm_by_app[canonical_app_id]
        live_norm = extract_normalized_resources(live_bundle)

        all_known_ids = sorted(set(ours_norm) | set(live_norm))
        force_resolved = (
            _resolve_resource_selectors(
                force_live, all_known_ids, "--force-live"
            )
            if force_live
            else set()
        )

        git_ahead_ids: set[str] = set()
        if eff_lock is not None:
            if conflicts_dir is not None:
                eff_conflicts = Path(conflicts_dir).resolve()
            elif dist_root is not None:
                c_base = (
                    dist_root.parent
                    if _has_app_marker(dist_root)
                    else dist_root
                )
                eff_conflicts = c_base / "conflicts" / dash_app_id
            else:
                eff_conflicts = repo_root / "conflicts" / dash_app_id

            drift_rep = check_drift(
                canonical_app_id,
                ours_bundle,
                live_bundle,
                lockfile_path=eff_lock,
                conflicts_dir=(
                    eff_conflicts
                    if conflicts_dir is not None and not dry_run
                    else work_root / "staged_conflicts"
                ),
            )
            conflicted_ids = [
                r.resource_id
                for r in drift_rep.resources
                if r.status == "CONFLICT"
                and r.resource_id not in force_resolved
            ]
            if conflicted_ids:
                if conflicts_dir is None and not dry_run:
                    drift_rep = check_drift(
                        canonical_app_id,
                        ours_bundle,
                        live_bundle,
                        lockfile_path=eff_lock,
                        conflicts_dir=eff_conflicts,
                    )
                raise DriftGateError(
                    f"Backport refused for {canonical_app_id}: 3-way conflict "
                    f"between source and live on {conflicted_ids}",
                    drift_report=drift_rep,
                )
            git_ahead_ids = {
                r.resource_id
                for r in drift_rep.resources
                if r.status == "CLEAN"
                and r.base_sha256 is not None
                and r.live_sha256 == r.base_sha256
                and r.ours_sha256 is not None
                and r.ours_sha256 != r.base_sha256
            }

        orphans = sorted(set(live_norm) - set(ours_norm))
        if orphans:
            raise BuildGateError(
                f"Backport refused for {canonical_app_id}: live contains "
                f"undeclared orphan resource(s) {orphans}; declare new "
                "agents/tools in core/ first."
            )
        missing_in_live = sorted(set(ours_norm) - set(live_norm))
        if missing_in_live:
            raise BuildGateError(
                f"Backport refused for {canonical_app_id}: live is missing "
                f"declared resource(s) {missing_in_live}; update "
                "disabled_agents/disabled_tools in channel_policy.yaml."
            )

        lint_errors = 0
        lint_warnings = 0
        if run_lint:
            lint_errors, lint_warnings = _enforce_lint_clean(
                live_bundle,
                manifests_dir=m_dir,
                brand_id=eff_brand,
                channel=eff_channel,
                stage_label="live state",
            )

        direct_changed_ids = [
            r_id
            for r_id in sorted(live_norm)
            if live_norm[r_id].sha256 != ours_norm[r_id].sha256
            and r_id not in git_ahead_ids
        ]

        brand_only_bundles: dict[str, Path] | None = None
        unoverridden_bundles: dict[str, Path] | None = None

        def _merge_ids(promoted: Sequence[str]) -> list[str]:
            return sorted(
                (set(direct_changed_ids) | set(promoted)) - git_ahead_ids
            )

        try:
            if target == "brand" and scope == "channel":
                changed_res_ids = direct_changed_ids
                if changed_res_ids:
                    brand_only_bundles = _build_clean_brand_baselines(
                        c_dir,
                        m_dir,
                        eff_brand,
                        eff_env,
                        clear_all_brand=False,
                        channel_to_clear=eff_channel,
                        work_dir=work_root / "brand_only_base",
                    )
            elif target == "brand" and scope == "brand":
                unoverridden_bundles = _build_clean_brand_baselines(
                    c_dir,
                    m_dir,
                    eff_brand,
                    eff_env,
                    clear_all_brand=True,
                    channel_to_clear=None,
                    work_dir=work_root / "unoverridden_base",
                )
                brand_only_bundles = _build_clean_brand_baselines(
                    c_dir,
                    m_dir,
                    eff_brand,
                    eff_env,
                    clear_all_brand=False,
                    channel_to_clear=eff_channel,
                    work_dir=work_root / "brand_only_base",
                )
                b_only_norm = extract_normalized_resources(
                    brand_only_bundles[eff_channel]
                )
                changed_res_ids = _merge_ids(
                    [
                        r_id
                        for r_id in sorted(live_norm)
                        if live_norm[r_id].sha256 != b_only_norm[r_id].sha256
                    ]
                )
            else:
                unoverridden_bundles = _build_clean_brand_baselines(
                    c_dir,
                    m_dir,
                    eff_brand,
                    eff_env,
                    clear_all_brand=True,
                    channel_to_clear=None,
                    work_dir=work_root / "unoverridden_base",
                )
                u_bundle = unoverridden_bundles[eff_channel]
                u_norm = extract_normalized_resources(u_bundle)
                changed_res_ids = _merge_ids(
                    [
                        r_id
                        for r_id in sorted(live_norm)
                        if _has_core_promotable_diff(
                            r_id,
                            u_bundle,
                            live_bundle,
                            unoverridden_norm=u_norm,
                            live_norm=live_norm,
                        )
                    ]
                )
        except (BuildError, ManifestError) as exc:
            raise BuildGateError(str(exc)) from exc

        checked_out_branch: str | None = None

        if not changed_res_ids:
            unchanged_siblings = sorted(
                b_id for b_id in pre_hashes if b_id != canonical_app_id
            )
            out_lock_path: Path | None = eff_lock
            if not dry_run:
                noop_dist_snap = (
                    _snapshot_directory_bytes(dist_root)
                    if dist_root is not None and dist_root.is_dir()
                    else None
                )
                noop_lock_snaps = _collect_lock_snapshots(
                    lockfile_path=lockfile_path,
                    eff_lock=eff_lock,
                    dist_root=dist_root,
                )
                try:
                    if dist_root is not None:
                        out_lock_path = _refresh_dist_and_lock(
                            core_dir=c_dir,
                            manifests_dir=m_dir,
                            manifests=manifests,
                            dist_root=dist_root,
                            brand_id=eff_brand,
                            channel=eff_channel,
                            environment=eff_env,
                            lockfile_path=lockfile_path,
                            eff_lock=eff_lock,
                            lock_source=live_bundle,
                        )
                    elif lockfile_path is not None:
                        out_lock_path = write_lockfile(
                            live_bundle,
                            lockfile_path,
                            app_id=ours_bundle.name,
                        )
                    if git_precheck is not None:
                        checked_out_branch = _checkout_git_branch(
                            repo_root, git_precheck[0], git_precheck[1]
                        )
                except Exception:
                    if dist_root is not None:
                        if noop_dist_snap is not None:
                            _restore_directory_bytes(dist_root, noop_dist_snap)
                        elif dist_root.exists():
                            shutil.rmtree(dist_root)
                    _restore_lock_snapshots(noop_lock_snaps)
                    raise

            noop_res = BackportResult(
                app_id=canonical_app_id,
                brand_id=eff_brand,
                channel=eff_channel,
                environment=eff_env,
                target=target,
                scope=scope,
                dry_run=dry_run,
                modified_files=[],
                changes=[],
                verified_bundles=sorted(pre_hashes.keys()),
                unchanged_sibling_bundles=unchanged_siblings,
                lint_errors=lint_errors,
                lint_warnings=lint_warnings,
                lint_ran=run_lint,
                git_branch=checked_out_branch,
                lockfile_path=out_lock_path,
            )
            noop_res.pr_body_markdown = format_backport_pr_body(
                noop_res, repo_root=repo_root
            )
            if not dry_run and pr_body_out is not None:
                pr_out = Path(pr_body_out).resolve()
                pr_out.parent.mkdir(parents=True, exist_ok=True)
                pr_out.write_text(noop_res.pr_body_markdown, encoding="utf-8")
            return noop_res

        changed_py_files = _check_python_bundle_and_extra_files(
            changed_res_ids=changed_res_ids,
            ours_norm=ours_norm,
            live_norm=live_norm,
            brand_id=eff_brand,
            target=target,
        )

        if dry_run:
            work_m_dir = work_root / "dry_run_manifests"
            shutil.copytree(
                m_dir,
                work_m_dir,
                ignore=shutil.ignore_patterns(".git", "__pycache__"),
            )
            _make_tree_writable(work_m_dir)
            if target == "core":
                work_c_dir = work_root / "dry_run_core"
                shutil.copytree(
                    c_dir,
                    work_c_dir,
                    ignore=shutil.ignore_patterns(".git", "__pycache__"),
                )
                _make_tree_writable(work_c_dir)
            else:
                work_c_dir = c_dir
            core_snapshot: tuple[dict[Path, bytes], set[Path]] | None = None
            man_snapshot: tuple[dict[Path, bytes], set[Path]] | None = None
            dist_snapshot: tuple[dict[Path, bytes], set[Path]] | None = None
            lock_snapshots: dict[Path, bytes | None] = {}
        else:
            work_c_dir = c_dir
            work_m_dir = m_dir
            core_snapshot = _snapshot_directory_bytes(c_dir)
            man_snapshot = _snapshot_directory_bytes(m_dir)
            dist_snapshot = (
                _snapshot_directory_bytes(dist_root)
                if dist_root is not None and dist_root.is_dir()
                else None
            )
            lock_snapshots = _collect_lock_snapshots(
                lockfile_path=lockfile_path,
                eff_lock=eff_lock,
                dist_root=dist_root,
            )

        final_lock_path: Path | None = eff_lock
        try:
            if target == "brand":
                base_map = (
                    brand_only_bundles
                    if scope == "channel" and brand_only_bundles is not None
                    else (unoverridden_bundles or {})
                )
                raw_modified_files, changes = _apply_brand_target_backport(
                    core_dir=work_c_dir,
                    manifests_dir=work_m_dir,
                    manifests=manifests,
                    brand_id=eff_brand,
                    channel=eff_channel,
                    environment=eff_env,
                    scope=scope,
                    ours_bundle=ours_bundle,
                    live_bundle=live_bundle,
                    base_bundles_by_channel=base_map,
                    changed_res_ids=changed_res_ids,
                )
            else:
                assert unoverridden_bundles is not None
                raw_modified_files, changes = _apply_core_target_backport(
                    core_dir=work_c_dir,
                    manifests_dir=work_m_dir,
                    manifests=manifests,
                    brand_id=eff_brand,
                    channel=eff_channel,
                    environment=eff_env,
                    ours_bundle=ours_bundle,
                    live_bundle=live_bundle,
                    unoverridden_bundle=unoverridden_bundles[eff_channel],
                    changed_res_ids=changed_res_ids,
                    changed_py_files=changed_py_files,
                    is_promotion_only=True,
                )

            if dry_run:
                modified_files = []
                for mf in raw_modified_files:
                    if mf.is_relative_to(work_m_dir):
                        modified_files.append(
                            m_dir / mf.relative_to(work_m_dir)
                        )
                    elif target == "core" and mf.is_relative_to(work_c_dir):
                        modified_files.append(
                            c_dir / mf.relative_to(work_c_dir)
                        )
                    else:
                        modified_files.append(mf)
            else:
                modified_files = raw_modified_files

            post_dist_root = work_root / "post_dist"
            try:
                post_reports = build_all(
                    work_c_dir, work_m_dir, post_dist_root, eff_env
                )
            except (BuildError, ManifestError) as exc:
                raise BuildGateError(str(exc)) from exc

            post_target_bundle = (
                post_dist_root / eff_brand / eff_channel.lower()
            )
            post_norm_by_app: dict[str, dict[str, NormalizedResource]] = {
                f"{rep.profile_id}/{rep.channel.lower()}": (
                    extract_normalized_resources(rep.out_dir)
                )
                for rep in post_reports
            }
            post_hashes: dict[str, dict[str, str]] = {
                b_id: {r_id: r.sha256 for r_id, r in r_map.items()}
                for b_id, r_map in post_norm_by_app.items()
            }

            if run_lint:
                lint_errors = 0
                lint_warnings = 0
                for rep in post_reports:
                    app_key = f"{rep.profile_id}/{rep.channel.lower()}"
                    if app_key == canonical_app_id or post_hashes[
                        app_key
                    ] != pre_hashes.get(app_key):
                        e_cnt, w_cnt = _enforce_lint_clean(
                            rep.out_dir,
                            manifests_dir=work_m_dir,
                            brand_id=rep.profile_id,
                            channel=rep.channel,
                            stage_label="rebuilt bundle",
                        )
                        lint_errors += e_cnt
                        lint_warnings += w_cnt

            # Invariant 1: Round-Trip SHA-256 Proof
            target_post_norm = post_norm_by_app[canonical_app_id]
            if set(target_post_norm.keys()) != set(live_norm.keys()):
                raise BuildGateError(
                    f"Round-trip verification failed for {canonical_app_id}: "
                    f"resource key set mismatch "
                    f"({sorted(target_post_norm)} != {sorted(live_norm)})."
                )
            for r_id, l_res in sorted(live_norm.items()):
                p_res = target_post_norm[r_id]
                want_sha = (
                    ours_norm[r_id].sha256
                    if r_id in git_ahead_ids
                    else l_res.sha256
                )
                if p_res.sha256 != want_sha:
                    raise BuildGateError(
                        f"Round-trip verification failed on {r_id}: "
                        "rebuilt bundle did not match "
                        + (
                            "the Git-ahead source."
                            if r_id in git_ahead_ids
                            else "live export."
                        )
                    )

            # Invariant 2: Zero Sibling Blast-Radius Proof
            unchanged_siblings = sorted(
                b_id
                for b_id in post_hashes
                if b_id != canonical_app_id
                and post_hashes[b_id] == pre_hashes.get(b_id)
            )
            if target == "brand" and scope == "channel":
                for b_id in sorted(post_hashes):
                    if b_id != canonical_app_id and post_hashes[
                        b_id
                    ] != pre_hashes.get(b_id):
                        raise BuildGateError(
                            f"Zero-blast-radius check failed: backport on "
                            f"{canonical_app_id} unexpectedly mutated sibling "
                            f"bundle {b_id!r}."
                        )
            elif target == "brand" and scope == "brand":
                for b_id in sorted(post_hashes):
                    if not b_id.startswith(f"{eff_brand}/") and (
                        post_hashes[b_id] != pre_hashes.get(b_id)
                    ):
                        raise BuildGateError(
                            f"Zero-blast-radius check failed: brand-scoped "
                            f"backport on {eff_brand!r} unexpectedly mutated "
                            f"sister-brand bundle {b_id!r}."
                        )

            if not dry_run:
                if dist_root is not None:
                    refreshed_manifests = load_manifests(m_dir)
                    final_lock_path = _refresh_dist_and_lock(
                        core_dir=c_dir,
                        manifests_dir=m_dir,
                        manifests=refreshed_manifests,
                        dist_root=dist_root,
                        brand_id=eff_brand,
                        channel=eff_channel,
                        environment=eff_env,
                        lockfile_path=lockfile_path,
                        eff_lock=eff_lock,
                        lock_source=live_bundle,
                    )
                elif lockfile_path is not None:
                    final_lock_path = write_lockfile(
                        live_bundle,
                        lockfile_path,
                        app_id=post_target_bundle.name,
                    )

                if git_precheck is not None:
                    checked_out_branch = _checkout_git_branch(
                        repo_root, git_precheck[0], git_precheck[1]
                    )
        except Exception:
            if not dry_run:
                if core_snapshot is not None:
                    _restore_directory_bytes(c_dir, core_snapshot)
                if man_snapshot is not None:
                    _restore_directory_bytes(m_dir, man_snapshot)
                if dist_root is not None:
                    if dist_snapshot is not None:
                        _restore_directory_bytes(dist_root, dist_snapshot)
                    elif dist_root.exists():
                        shutil.rmtree(dist_root)
                _restore_lock_snapshots(lock_snapshots)
            raise

    result = BackportResult(
        app_id=canonical_app_id,
        brand_id=eff_brand,
        channel=eff_channel,
        environment=eff_env,
        target=target,
        scope=scope,
        dry_run=dry_run,
        modified_files=modified_files,
        changes=changes,
        verified_bundles=sorted(post_hashes.keys()),
        unchanged_sibling_bundles=unchanged_siblings,
        lint_errors=lint_errors,
        lint_warnings=lint_warnings,
        lint_ran=run_lint,
        git_branch=checked_out_branch,
        lockfile_path=final_lock_path,
    )
    result.pr_body_markdown = format_backport_pr_body(
        result, repo_root=repo_root
    )
    if not dry_run and pr_body_out is not None:
        pr_out = Path(pr_body_out).resolve()
        pr_out.parent.mkdir(parents=True, exist_ok=True)
        pr_out.write_text(result.pr_body_markdown, encoding="utf-8")

    return result


__all__ = [
    "BackportChange",
    "BackportResult",
    "BackportScope",
    "BackportTarget",
    "backport_live_to_source",
    "format_backport_pr_body",
]
