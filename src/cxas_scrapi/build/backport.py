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

"""Console UI -> Rung 1 source/manifest back-port with round-trip proof."""

from __future__ import annotations

import ast
import copy
import difflib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

import yaml

from cxas_scrapi.build.builder import (
    BuildError,
    _apply_append_rules_lines,
    _apply_line_replacements_strict,
    _build_identity_block,
    build_all,
    build_app,
)
from cxas_scrapi.build.bundle_io import (
    BuildGateError,
    DriftGateError,
    NormalizedResource,
    load_json_dict_strict,
    read_utf8_strict,
)
from cxas_scrapi.build.drift import (
    _OPENAPI_SERVER_URL_RE,
    CES_ENV_VAR_PLACEHOLDER,
    _canonical_openapi_schema_lines,
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
from cxas_scrapi.core.python_bundler import (
    PythonBundlerError,
    strip_shared_bundle_text,
)
from cxas_scrapi.utils.linter import collect_lint_report

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

BackportTarget = Literal["brand", "core"]
BackportScope = Literal["channel", "brand"]

_GIT_BRANCH_RE = re.compile(r"^[a-zA-Z0-9._/-]+$")
_COND_START_RE = re.compile(
    r"^\{@start(Channel|Brand|AgentEnabled|ToolEnabled)_([^}]+)\}$"
)
_COND_END_RE = re.compile(
    r"^\{@end(Channel|Brand|AgentEnabled|ToolEnabled)_([^}]+)\}$"
)
_COND_INLINE_RE = re.compile(
    r"\{@start(Channel|Brand|AgentEnabled|ToolEnabled)_([^}]+)\}"
    r"([^\n]*?)"
    r"\{@end\1_\2\}"
)


class _CleanYamlDumper(yaml.SafeDumper):
    """YAML dumper using literal block scalars (`|`) for multi-line text."""


def _str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_CleanYamlDumper.add_representer(str, _str_representer)


@dataclass
class BackportChange:
    """Single logical resource delta back-ported into Rung 1 manifests/core."""

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
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise BuildGateError(f"Failed to parse YAML {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise BuildGateError(f"Expected mapping at root of {path}")
    return data


def _safe_load_yaml_text(text: str, *, context: str) -> Any:
    """Parses YAML text, converting parser errors into `BuildGateError`."""
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise BuildGateError(f"Invalid YAML in {context}: {exc}") from exc


def _read_optional_text(path: Path, *, context: str = "file") -> str:
    """Reads an optional UTF-8 file (e.g. `instruction.txt`); '' if absent."""
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


def _make_token_renderer(
    manifests: Manifests,
    brand_id: str,
    channel: str,
    environment: str,
) -> Callable[[str], str]:
    brand = manifests.brand(brand_id)
    policy = manifests.policy(brand_id)
    env_manifest = manifests.env(environment)

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
    for k, v in env_manifest.templates.items():
        v_rendered = v.format(
            brand=brand.brand_id,
            brand_slug=brand.brand_id.replace("_", "-"),
            env=environment,
            channel=channel,
            service="{service}",
        )
        replacements[f"@@env.{k}@@"] = v_rendered.replace("{service}", "")

    def _render(s: str) -> str:
        for ek, ev in env_manifest.templates.items():
            base_rendered = ev.format(
                brand=brand.brand_id,
                brand_slug=brand.brand_id.replace("_", "-"),
                env=environment,
                channel=channel,
                service="{service}",
            )
            pat = rf"@@env\.{re.escape(ek)}:([^@]+)@@"

            def _repl(m: re.Match[str], rendered: str = base_rendered) -> str:
                return rendered.replace("{service}", m.group(1))

            s = re.sub(pat, _repl, s)
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
    brand = manifests.brand(brand_id)
    policy = manifests.policy(brand_id)
    env_manifest = manifests.env(environment)
    render_fn = _make_token_renderer(manifests, brand_id, channel, environment)

    raw_candidates: list[tuple[str, str, int]] = []
    if brand.tone_guidelines:
        raw_candidates.append(
            (brand.tone_guidelines, "@@brand.tone_guidelines@@", 0)
        )
    for k, v in sorted(brand.lexicon.items()):
        raw_candidates.append((v, f"@@brand.lexicon.{k}@@", 0))
    if brand.legal_entity:
        raw_candidates.append((brand.legal_entity, "@@brand.legal_entity@@", 1))
    if brand.assistant_title:
        raw_candidates.append(
            (brand.assistant_title, "@@brand.assistant_title@@", 2)
        )
    for k, v in sorted(brand.urls.items()):
        raw_candidates.append((v, f"@@brand.urls.{k}@@", 2))
    for k, v in sorted(policy.api_routing.items()):
        raw_candidates.append((v, f"@@routing.{k}@@", 2))

    svc_patterns: list[tuple[re.Pattern[str], str]] = []
    for ek, ev in sorted(env_manifest.templates.items()):
        base_rendered = ev.format(
            brand=brand.brand_id,
            brand_slug=brand.brand_id.replace("_", "-"),
            env=environment,
            channel=channel,
            service="{service}",
        )
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

    raw_candidates.append((brand.display_name, "@@brand.display_name@@", 3))

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


_PLACEHOLDER_TOKEN_RE = re.compile(r"(@@[^@\s]+@@)")


def _replace_outside_placeholders(text: str, val: str, tok: str) -> str:
    """Replaces `val` with `tok` only outside already-emitted `@@...@@`."""
    parts = _PLACEHOLDER_TOKEN_RE.split(text)
    # re.split with one capture group alternates [plain, token, plain, ...].
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
    manifests: Manifests,
    brand_id: str,
    rev_pairs: Sequence[tuple[str, str]],
    svc_patterns: Sequence[tuple[re.Pattern[str], str]],
    render_fn: Callable[[str], str],
) -> str:
    extra: list[tuple[str, str]] = []
    if len(brand_id) >= 4 and not brand_id.isdigit():
        extra.append((brand_id, "@@brand.brand_id@@"))
    return _reverse_tokens(
        url_text, rev_pairs, svc_patterns, render_fn, extra_pairs=extra
    )


_BRAND_SCOPED_TOKEN_RE = re.compile(
    r"^@@(brand\.lexicon|brand\.urls|routing)\.([^@]+)@@$"
)


def _token_defined_for_all_brands(manifests: Manifests, tok: str) -> bool:
    """True when every compiled brand can resolve `tok` (shared `core/`)."""
    m_tok = _BRAND_SCOPED_TOKEN_RE.match(tok)
    if m_tok is None:
        return True
    kind, key = m_tok.group(1), m_tok.group(2)
    for b_id in sorted(manifests.policies):
        if kind == "brand.lexicon":
            defined = key in manifests.brand(b_id).lexicon
        elif kind == "brand.urls":
            defined = key in manifests.brand(b_id).urls
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
    # A blank-only match (deleting/replacing blank lines, or anchoring an
    # insertion on a blank line) cannot be expressed as a `LineReplacement`
    # (`match` must be non-empty); fall back to a full `instruction`.
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
        sim_app = _apply_append_rules_lines(
            base_norm, cand_rules, render_fn=render_fn
        )
        if sim_app == live_norm:
            return "append_rules", cand_rules

    # 2. Unique line replacements
    raw_pairs = _extract_line_replacement_pairs(base_lines, live_lines)
    if raw_pairs:
        rev_specs = [
            LineReplacement(match=_rev(m_str), replace_with=_rev(w_str))
            for m_str, w_str in raw_pairs
        ]
        try:
            sim_rep = _apply_line_replacements_strict(
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
                        _apply_line_replacements_strict(
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
            pref_specs = [
                LineReplacement(match=_rev(m_str), replace_with=_rev(w_str))
                for m_str, w_str in prefix_pairs
            ]
            try:
                step1 = _apply_line_replacements_strict(
                    base_norm,
                    pref_specs,
                    render_fn=render_fn,
                    context_label="backport-combo-rep",
                )
                sim_combo = _apply_append_rules_lines(
                    step1, tail_rules, render_fn=render_fn
                )
            except BuildError:
                sim_combo = ""

            if sim_combo == live_norm:
                combo_ok = True
                if scope == "brand":
                    for sib_base, sib_render in sibling_baselines:
                        try:
                            s1 = _apply_line_replacements_strict(
                                sib_base,
                                pref_specs,
                                render_fn=sib_render,
                                context_label="backport-combo-sib",
                            )
                            _apply_append_rules_lines(
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
        load_json_dict_strict(
            bundle_dir / "tools" / tool_name / f"{tool_name}.json",
            context=f"{res_id} JSON",
        ),
        is_root=True,
    )
    env_tool_urls = _load_environment_tool_urls(bundle_dir)
    oa_path = (
        bundle_dir
        / "tools"
        / tool_name
        / "open_api_tool"
        / "open_api_schema.yaml"
    )
    oa_raw = (
        read_utf8_strict(oa_path, context=f"{res_id} OpenAPI schema").replace(
            "\r\n", "\n"
        )
        if oa_path.is_file()
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
                    elif len(paths) == 0:
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


_TOOL_PAYLOAD_KEYS = ("openApiTool", "dataStoreTool", "pythonFunction")


def _tool_payload(tj: Mapping[str, Any]) -> Mapping[str, Any]:
    """Returns the tool sub-object carrying `description`/`url` fields."""
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
                f"into brands/{brand_id}/overrides.yaml; re-run with "
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

    cand_py = _reverse_tokens(clean_py, rev_pairs, svc_patterns, render_fn)
    for b_id, pol in manifests.policies.items():
        for ch in pol.channels:
            r_fn = _make_token_renderer(manifests, b_id, ch, environment)
            try:
                ast.parse(r_fn(cand_py))
            except SyntaxError:
                return clean_py
    return cand_py


def _strip_brand_overrides_for_baseline(
    ov_yaml: dict[str, Any],
    *,
    channel_to_clear: str | None,
) -> dict[str, Any]:
    stripped = copy.deepcopy(ov_yaml)
    if channel_to_clear is None:
        return {"brand_id": stripped["brand_id"]}

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


def _compile_clean_brand_baselines(
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
    ov_path = tmp_man / "brands" / brand_id / "overrides.yaml"
    if clear_all_brand:
        if ov_path.is_file():
            ov_path.unlink()
    elif ov_path.is_file():
        raw_ov = _load_yaml_dict_optional(ov_path)
        cleaned_ov = _strip_brand_overrides_for_baseline(
            raw_ov, channel_to_clear=channel_to_clear
        )
        if set(cleaned_ov.keys()) <= {"brand_id"}:
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
            brand_id=brand_id,
            channel=ch,
            environment=environment,
            out_dir=ch_dir,
        )
        out_map[ch] = ch_dir
    return out_map


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


def _patch_core_instruction_preserving_conditionals(
    raw_core_text: str,
    base_compiled_text: str,
    live_compiled_text: str,
    *,
    brand_id: str,
    channel: str,
    disabled_agents: set[str],
    disabled_tools: set[str],
    rev_fn: Callable[[str], str],
) -> str:
    """Patches a `core/` prompt while keeping inactive `{@start...}` blocks."""

    def _eval_cond(kind: str, arg: str) -> bool:
        if kind == "Channel":
            return arg == channel
        if kind == "Brand":
            return arg == brand_id
        if kind == "AgentEnabled":
            return arg not in disabled_agents
        if kind == "ToolEnabled":
            return arg not in disabled_tools
        return True

    raw_norm = raw_core_text.replace("\r\n", "\n")
    raw_lines = raw_norm.splitlines()
    base_split = base_compiled_text.replace("\r\n", "\n").splitlines()
    live_split = live_compiled_text.replace("\r\n", "\n").splitlines()

    # Map each emitted line index in base_split to raw line index and collect
    # standalone conditional structural lines positioned before each emitted
    # line (plus trailing structural lines after the last emitted line).
    pre_interstitial: dict[int, list[str]] = {}
    emit_to_raw_line: list[str] = []
    pending_struct: list[str] = []
    stack: list[tuple[str, str, bool]] = []

    for r_line in raw_lines:
        stripped = r_line.strip()
        m_s = _COND_START_RE.match(stripped)
        if m_s:
            k, a = m_s.group(1), m_s.group(2)
            active = _eval_cond(k, a)
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

    if len(emit_to_raw_line) == len(base_split):
        out_raw: list[str] = []
        opcodes = difflib.SequenceMatcher(
            None, base_split, live_split, autojunk=False
        ).get_opcodes()
        for tag, i1, i2, j1, j2 in opcodes:
            if tag == "equal":
                for idx in range(i1, i2):
                    out_raw.extend(pre_interstitial.get(idx, ()))
                    out_raw.append(emit_to_raw_line[idx])
            elif tag in ("replace", "delete"):
                for idx in range(i1, i2):
                    out_raw.extend(pre_interstitial.get(idx, ()))
                    r_ln = emit_to_raw_line[idx]
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
                if tag == "replace":
                    for l_ln in live_split[j1:j2]:
                        out_raw.append(rev_fn(l_ln))
            elif tag == "insert":
                if i1 < len(emit_to_raw_line) and i1 in pre_interstitial:
                    out_raw.extend(pre_interstitial.pop(i1))
                for l_ln in live_split[j1:j2]:
                    out_raw.append(rev_fn(l_ln))
        out_raw.extend(trailing_struct)
        return "\n".join(out_raw) + ("\n" if out_raw else "")

    # Fallback: preserve standalone inactive conditional blocks verbatim,
    # expand purely-inactive inline conditional lines into 3-line blocks, and
    # fail closed on mixed active + inactive inline conditional lines.
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
    ov_path = manifests_dir / "brands" / brand_id / "overrides.yaml"
    orig_ov_exists = ov_path.is_file()
    ov_yaml = copy.deepcopy(_load_yaml_dict_optional(ov_path))
    ov_yaml["brand_id"] = brand_id
    rel_target = f"brands/{brand_id}/overrides.yaml"
    changes: list[BackportChange] = []

    policy = manifests.policy(brand_id)
    sibling_channels = [ch for ch in policy.channels if ch != channel]

    core_gi_path = core_dir / "global_instruction.txt"
    has_inline_id_tok = (
        core_gi_path.is_file()
        and "@@brand.identity_block@@"
        in core_gi_path.read_text(encoding="utf-8")
    )
    rendered_id_block = render_fn(
        _build_identity_block(manifests, brand_id, channel)
    )
    rendered_id_prefix = None if has_inline_id_tok else rendered_id_block

    for res_id in changed_res_ids:
        if res_id == "app":
            ours_app = _normalize_app_obj(
                load_json_dict_strict(
                    ours_bundle / "app.json", context="ours app.json"
                )
            )
            live_app = _normalize_app_obj(
                load_json_dict_strict(
                    live_bundle / "app.json", context="live app.json"
                )
            )
            if ours_app != live_app:
                diff_keys = sorted(
                    k
                    for k in set(ours_app) | set(live_app)
                    if ours_app.get(k) != live_app.get(k)
                )
                raise BuildGateError(
                    f"Cannot backport app.json change on keys {diff_keys} "
                    f"into {rel_target}; edit brands/{brand_id}/identity.yaml "
                    "or core/app.json."
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
                    and not live_gi_norm.startswith(rendered_id_prefix)
                )
            )
            if id_missing:
                raise BuildGateError(
                    f"Cannot backport global_instruction.txt: live modified "
                    f"the generated '# Brand:' identity block; update "
                    f"brands/{brand_id}/identity.yaml directly."
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
            ours_aj = normalize_json_obj(
                load_json_dict_strict(
                    ours_bundle / "agents" / agent_id / f"{agent_id}.json",
                    context=f"ours {res_id}",
                ),
                is_root=True,
            )
            base_aj = normalize_json_obj(
                load_json_dict_strict(
                    base_bundle / "agents" / agent_id / f"{agent_id}.json",
                    context=f"baseline {res_id}",
                ),
                is_root=True,
            )
            live_aj = normalize_json_obj(
                load_json_dict_strict(
                    live_bundle / "agents" / agent_id / f"{agent_id}.json",
                    context=f"live {res_id}",
                ),
                is_root=True,
            )
            for cmp_aj in (ours_aj, base_aj):
                changed_top = {
                    k
                    for k in set(cmp_aj) | set(live_aj)
                    if cmp_aj.get(k) != live_aj.get(k)
                }
                bad_top = sorted(changed_top - {"description", "modelSettings"})
                if bad_top:
                    raise BuildGateError(
                        f"Cannot backport structural JSON change on {res_id} "
                        f"keys {bad_top} into {rel_target}; edit "
                        f"core/agents/{agent_id}/{agent_id}.json or "
                        "channel_policy.yaml."
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
                        "set values, not delete them; re-run with --target "
                        f"core or edit core/agents/{agent_id}/{agent_id}.json."
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
                        f"edit core/agents/{agent_id}/{agent_id}.json or "
                        "envs/*.yaml model pins directly."
                    )
                if base_ms.get("model") != live_ms.get("model"):
                    target_holder["model"] = str(live_ms["model"])
                if ours_ms.get("model") != live_ms.get("model") or (
                    scope == "brand"
                    and base_ms.get("model") != live_ms.get("model")
                ):
                    summary_parts.append(f"model={live_ms.get('model')}")

                if base_ms.get("temperature") != live_ms.get("temperature"):
                    target_holder["temperature"] = float(live_ms["temperature"])
                if ours_ms.get("temperature") != live_ms.get("temperature") or (
                    scope == "brand"
                    and base_ms.get("temperature") != live_ms.get("temperature")
                ):
                    summary_parts.append(
                        f"temperature={live_ms.get('temperature')}"
                    )

            ours_in = _read_optional_text(
                ours_bundle / "agents" / agent_id / "instruction.txt",
                context=f"ours {res_id} instruction.txt",
            )
            base_in = _read_optional_text(
                base_bundle / "agents" / agent_id / "instruction.txt",
                context=f"baseline {res_id} instruction.txt",
            )
            live_in = _read_optional_text(
                live_bundle / "agents" / agent_id / "instruction.txt",
                context=f"live {res_id} instruction.txt",
            )
            ours_in_l = ours_in.replace("\r\n", "\n").splitlines()
            base_in_l = base_in.replace("\r\n", "\n").splitlines()
            live_in_l = live_in.replace("\r\n", "\n").splitlines()

            if base_in_l != live_in_l:
                sib_ag: list[tuple[str, Callable[[str], str]]] = []
                for s_ch in sibling_channels:
                    sib_in_p = (
                        base_bundles_by_channel[s_ch]
                        / "agents"
                        / agent_id
                        / "instruction.txt"
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
            err_sfx = " with --target brand; re-run with --target core."
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
                    "delete them; re-run with --target core."
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
                    manifests,
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
            "re-run with --target core."
        )

    cleaned_ov_root = _prune_empty_dict(ov_yaml)
    if set(cleaned_ov_root.keys()) <= {"brand_id"}:
        if orig_ov_exists:
            ov_path.unlink()
            return [ov_path], changes
        return [], changes

    _dump_yaml_clean(ov_path, cleaned_ov_root)
    return [ov_path], changes


def _write_core_json_formatted(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


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
    # Shared core/ is compiled for every brand: never lift literals into
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
        for ag_p in (core_dir / "agents").iterdir():
            aj_p = ag_p / f"{ag_p.name}.json"
            if aj_p.is_file():
                d_name = load_json_dict_strict(aj_p).get("displayName", "")
                if ag_p.name in disabled_agents or d_name in disabled_agents:
                    disabled_agents.add(ag_p.name)
                    if d_name:
                        disabled_agents.add(str(d_name))
    if (core_dir / "tools").is_dir():
        for tl_p in (core_dir / "tools").iterdir():
            tj_p = tl_p / f"{tl_p.name}.json"
            if tj_p.is_file():
                d_name = load_json_dict_strict(tj_p).get("displayName", "")
                if tl_p.name in disabled_tools or d_name in disabled_tools:
                    disabled_tools.add(tl_p.name)
                    if d_name:
                        disabled_tools.add(str(d_name))

    ov_path = manifests_dir / "brands" / brand_id / "overrides.yaml"
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
                load_json_dict_strict(
                    ours_bundle / "app.json", context="ours app.json"
                )
            )
            live_app = _normalize_app_obj(
                load_json_dict_strict(
                    live_bundle / "app.json", context="live app.json"
                )
            )
            if ours_app != live_app:
                diff_keys = sorted(
                    k
                    for k in set(ours_app) | set(live_app)
                    if ours_app.get(k) != live_app.get(k)
                )
                raise BuildGateError(
                    f"Cannot backport structural app.json change on keys "
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
                raw_core_gi = (
                    core_gi_path.read_text(encoding="utf-8")
                    if core_gi_path.is_file()
                    else ""
                )
                id_block = render_fn(
                    _build_identity_block(manifests, brand_id, channel)
                )
                live_gi_norm = live_gi.replace("\r\n", "\n")
                base_gi_norm = base_gi.replace("\r\n", "\n")
                if "@@brand.identity_block@@" in raw_core_gi:
                    if id_block and id_block not in live_gi_norm:
                        raise BuildGateError(
                            f"Cannot backport global_instruction.txt: live "
                            f"modified the generated '# Brand:' identity "
                            f"block; edit brands/{brand_id}/identity.yaml."
                        )
                    occ_cnt = max(
                        1, raw_core_gi.count("@@brand.identity_block@@")
                    )
                    b_tok = (
                        base_gi_norm.replace(
                            id_block, "@@brand.identity_block@@", occ_cnt
                        )
                        if id_block
                        else base_gi_norm
                    )
                    l_tok = (
                        live_gi_norm.replace(
                            id_block, "@@brand.identity_block@@", occ_cnt
                        )
                        if id_block
                        else live_gi_norm
                    )

                    def _gi_rev(ln: str) -> str:
                        if "@@brand.identity_block@@" not in ln:
                            return _rev(ln)
                        return "@@brand.identity_block@@".join(
                            _rev(part)
                            for part in ln.split("@@brand.identity_block@@")
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
                        )
                    )
                else:
                    if id_block and not live_gi_norm.startswith(id_block):
                        raise BuildGateError(
                            f"Cannot backport global_instruction.txt: live "
                            f"modified the generated '# Brand:' identity "
                            f"block; edit brands/{brand_id}/identity.yaml."
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
            ours_aj = normalize_json_obj(
                load_json_dict_strict(
                    ours_bundle / "agents" / agent_id / f"{agent_id}.json",
                    context=f"ours {res_id}",
                ),
                is_root=True,
            )
            base_aj = normalize_json_obj(
                load_json_dict_strict(
                    unoverridden_bundle
                    / "agents"
                    / agent_id
                    / f"{agent_id}.json",
                    context=f"baseline {res_id}",
                ),
                is_root=True,
            )
            live_aj = normalize_json_obj(
                load_json_dict_strict(
                    live_bundle / "agents" / agent_id / f"{agent_id}.json",
                    context=f"live {res_id}",
                ),
                is_root=True,
            )
            if ours_aj != live_aj:
                changed_top = {
                    k
                    for k in set(ours_aj) | set(live_aj)
                    if ours_aj.get(k) != live_aj.get(k)
                }
                if not changed_top.issubset({"description"}):
                    raise BuildGateError(
                        f"Cannot backport JSON keys {sorted(changed_top)} on "
                        f"{res_id} with --target core; use --target brand for "
                        "model/temperature or edit core/manifests."
                    )

            ag_root = dict(ov_yaml.get("agents") or {})
            ag_ov_d = copy.deepcopy(dict(ag_root.get(agent_id) or {}))
            ag_ch_d = dict(ag_ov_d.get("channels") or {})
            ag_ch_entry = dict(ag_ch_d.get(channel) or {})
            summary_parts = []

            if ours_aj.get("description") != live_aj.get("description") or (
                is_promotion_only
                and base_aj.get("description") != live_aj.get("description")
            ):
                core_aj_path = (
                    core_dir / "agents" / agent_id / f"{agent_id}.json"
                )
                core_aj = load_json_dict_strict(core_aj_path)
                if "description" in live_aj:
                    core_aj["description"] = _rev(
                        str(live_aj.get("description", ""))
                    )
                else:
                    # Console cleared the optional field: drop it from core/
                    # instead of writing "" so the round-trip SHA matches.
                    core_aj.pop("description", None)
                _write_core_json_formatted(core_aj_path, core_aj)
                _mark_modified(core_aj_path)
                ag_ov_d.pop("description", None)
                ag_ch_entry.pop("description", None)
                summary_parts.append("description")

            base_in = _read_optional_text(
                unoverridden_bundle / "agents" / agent_id / "instruction.txt",
                context=f"baseline {res_id} instruction.txt",
            )
            ours_in = _read_optional_text(
                ours_bundle / "agents" / agent_id / "instruction.txt",
                context=f"ours {res_id} instruction.txt",
            )
            live_in = _read_optional_text(
                live_bundle / "agents" / agent_id / "instruction.txt",
                context=f"live {res_id} instruction.txt",
            )
            if ours_in.replace("\r\n", "\n").splitlines() != live_in.replace(
                "\r\n", "\n"
            ).splitlines() or (
                is_promotion_only
                and base_in.replace("\r\n", "\n").splitlines()
                != live_in.replace("\r\n", "\n").splitlines()
            ):
                core_in_path = (
                    core_dir / "agents" / agent_id / "instruction.txt"
                )
                raw_core_in = (
                    core_in_path.read_text(encoding="utf-8")
                    if core_in_path.is_file()
                    else ""
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
                )
                core_in_path.write_text(patched_in, encoding="utf-8")
                _mark_modified(core_in_path)
                for k_i in ("instruction", "replace_lines", "append_rules"):
                    ag_ov_d.pop(k_i, None)
                    ag_ch_entry.pop(k_i, None)
                summary_parts.append("instruction")

            for rel_py in changed_py_files.get(res_id, ()):
                live_py_p = live_bundle / "agents" / agent_id / rel_py
                core_py_p = core_dir / "agents" / agent_id / rel_py
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

            core_tj_path = core_dir / "tools" / tool_id / f"{tool_id}.json"
            core_tj = load_json_dict_strict(core_tj_path)
            mutated_tj = False
            t_parts = []

            to_root = dict(ov_yaml.get("tools") or {})
            to_ov_d = copy.deepcopy(dict(to_root.get(tool_id) or {}))
            to_ch_d = dict(to_ov_d.get("channels") or {})
            to_ch_entry = dict(to_ch_d.get(channel) or {})

            # Live reverted a brand/channel override back to the core/
            # baseline (`live == base`, `ours != live`): core/ already holds
            # the live value, so only the override must be dropped.
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
                # Console cleared the optional field: drop it from core/
                # instead of writing "" so the round-trip SHA matches.
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
                for sub_k in ("openApiTool", "dataStoreTool", "pythonFunction"):
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
                    manifests,
                    brand_id,
                    rev_pairs,
                    svc_patterns,
                    render_fn,
                )
                core_env_p = core_dir / "environment.json"
                env_has_url = False
                if core_env_p.is_file():
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
                # Mirror builder.py: keep an explicit tool-JSON url in sync
                # even when core/environment.json also declares one.
                if isinstance(oa_cfg, dict) and (
                    "url" in oa_cfg or not env_has_url
                ):
                    oa_cfg["url"] = rev_url
                    mutated_tj = True

                to_ov_d.pop("url", None)
                to_ch_entry.pop("url", None)
                t_parts.append("url")

            core_oa_p = (
                core_dir
                / "tools"
                / tool_id
                / "open_api_tool"
                / "open_api_schema.yaml"
            )
            if core_oa_p.is_file():
                orig_core_oa = core_oa_p.read_text(encoding="utf-8")
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
                    # Raw core/ schemas may carry unquoted `@@...@@` tokens
                    # (`@` is a reserved YAML indicator): render before parse.
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
                        manifests,
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
                            t_parts.append("open_api_schema.yaml")
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
                _write_core_json_formatted(core_tj_path, core_tj)
                _mark_modified(core_tj_path)

            for rel_py in changed_py_files.get(res_id, ()):
                live_py_p = live_bundle / "tools" / tool_id / rel_py
                core_py_p = core_dir / "tools" / tool_id / rel_py
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
                load_json_dict_strict(
                    ours_bundle / "guardrails" / gr_id / f"{gr_id}.json",
                    context=f"ours {res_id}",
                ),
                is_root=True,
            )
            live_gj = normalize_json_obj(
                load_json_dict_strict(
                    live_bundle / "guardrails" / gr_id / f"{gr_id}.json",
                    context=f"live {res_id}",
                ),
                is_root=True,
            )
            core_gj_path = core_dir / "guardrails" / gr_id / f"{gr_id}.json"
            core_gj = load_json_dict_strict(core_gj_path)
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
            _write_core_json_formatted(core_gj_path, core_gj)
            _mark_modified(core_gj_path)
            changes.append(
                BackportChange(
                    resource_id=res_id,
                    target_file=f"core/guardrails/{gr_id}/{gr_id}.json",
                    summary=f"Backported core guardrail {', '.join(gr_parts)}",
                )
            )
            continue

        raise BuildGateError(
            f"Unsupported resource {res_id!r} for --target core."
        )

    cleaned_ov = _prune_empty_dict(ov_yaml)
    if cleaned_ov != _prune_empty_dict(orig_ov_yaml):
        if set(cleaned_ov.keys()) <= {"brand_id"}:
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
        u_aj = normalize_json_obj(
            load_json_dict_strict(
                unoverridden_bundle / "agents" / ag_id / f"{ag_id}.json"
            ),
            is_root=True,
        )
        l_aj = normalize_json_obj(
            load_json_dict_strict(
                live_bundle / "agents" / ag_id / f"{ag_id}.json"
            ),
            is_root=True,
        )
        if u_aj.get("description") != l_aj.get("description"):
            return True
        u_in = _read_optional_text(
            unoverridden_bundle / "agents" / ag_id / "instruction.txt"
        )
        l_in = _read_optional_text(
            live_bundle / "agents" / ag_id / "instruction.txt"
        )
        return (
            u_in.replace("\r\n", "\n").splitlines()
            != l_in.replace("\r\n", "\n").splitlines()
        )
    return res_id.startswith(("tools/", "guardrails/"))


def _snapshot_directory_bytes(root: Path) -> dict[Path, bytes]:
    return {
        p: p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


def _restore_directory_bytes(
    root: Path, snapshot: Mapping[Path, bytes]
) -> None:
    current_files = {
        p
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }
    for extra in sorted(current_files - set(snapshot.keys())):
        extra.unlink()
    for path, data in sorted(snapshot.items()):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _validate_git_branch_precheck(
    repo_dir: Path, git_branch: str
) -> tuple[str, str]:
    branch = git_branch.strip()
    if (
        not branch
        or branch.startswith("-")
        or branch.endswith((".lock", "/"))
        or ".." in branch
        or "//" in branch
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
    """Renders a review-ready Git PR body with lint and SHA-256 proof."""
    lint_line = (
        f"- **cxas lint (P001-P005)**: `PASS ({result.lint_errors} errors, "
        f"{result.lint_warnings} warnings)`"
        if result.lint_ran
        else "- **cxas lint (P001-P005)**: `SKIPPED (--no-lint)`"
    )
    lines = [
        f"## CES Console -> Rung 1 Backport (`{result.app_id}`)",
        "",
        f"- **App ID**: `{result.app_id}` (`brand={result.brand_id}`, "
        f"`channel={result.channel}`, `environment={result.environment}`)",
        f"- **Target & Scope**: `target={result.target}`, "
        f"`scope={result.scope}`",
        lint_line,
        "- **Round-Trip SHA-256 Proof**: `VERIFIED` "
        "(`compile(backport(live)) == live`)",
    ]
    if result.unchanged_sibling_bundles:
        sib_str = ", ".join(f"`{b}`" for b in result.unchanged_sibling_bundles)
        lines.append(
            f"- **Unchanged Sibling Bundles (0 Blast Radius)**: {sib_str}"
        )
    else:
        lines.append(
            "- **Unchanged Sibling Bundles**: *(shared core update across "
            "applicable bundles)*"
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
    lines.append("### Verified Compiled Bundles")
    for vb in result.verified_bundles:
        lines.append(f"- `{vb}`")

    return "\n".join(lines) + "\n"


def _read_deployment_triplet(
    bundle_dir: Path | None,
) -> tuple[str | None, str | None, str | None]:
    if bundle_dir is None or not bundle_dir.is_dir():
        return None, None, None
    for dep in sorted(bundle_dir.glob("deployments/*.yaml")):
        try:
            data = yaml.safe_load(dep.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(data, dict) and data.get("brandId"):
            return (
                str(data["brandId"]) if data.get("brandId") else None,
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
        if cand.is_dir() and (cand / "app.json").is_file():
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
    """Infers `(brand, channel)` from `dist/` only when provably unique.

    Fails closed (returns `(None, None, candidates)`) unless:
      * every declared candidate `(brand, channel)` matching the hints has a
        compiled `dist/` bundle (a partial `dist/` cannot rule out unbuilt
        channels);
      * exactly one candidate has the same resource topology (normalized
        resource IDs) as the live export (SHA-match counts alone are unsafe:
        reverting a channel override can make a sibling score higher); and
      * that candidate shares at least one identical resource SHA-256.
    """
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
        if not (cand / "app.json").is_file():
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
    """Recompiles `dist_root` and re-locks ONLY the reconciled target app.

    Sibling `(brand, channel)` bundles are recompiled so `dist/` reflects the
    new source, but their `.lock.json` baselines are left untouched: their
    live CES state was not verified by this backport, so re-locking them
    would make `cxas push --check-drift` see unpushed changes as drift.

    `lock_source` (the verified live export) supplies the target lockfile
    content so resources Git advanced but Console never saw keep their
    deployed baseline.
    """
    ch_low = channel.lower()
    dash_id = f"{brand_id}-{ch_low}"

    def _lock(target_dir: Path, lock_path: Path | str) -> Path:
        if lock_source is None:
            return write_lockfile(target_dir, lock_path)
        return write_lockfile(lock_source, lock_path, app_id=target_dir.name)

    # 1. Single-bundle directory
    if (dist_root / "app.json").is_file():
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
                brand_id=brand_id,
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
        (dist_root / f"{b_id}-{ch.lower()}" / "app.json").is_file()
        or (dist_root / f"{b_id}-{ch.lower()}.lock.json").is_file()
        for b_id, pol in manifests.policies.items()
        for ch in pol.channels
    ) and not any(
        (dist_root / b_id / ch.lower() / "app.json").is_file()
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
                    brand_id=b_id,
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

    # 3. Standard Rung 1 2-level `dist/<brand_id>/<channel_low>` layout
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


def _enforce_lint_clean(
    bundle_dir: Path,
    *,
    manifests_dir: Path,
    brand_id: str,
    channel: str,
    stage_label: str,
) -> tuple[int, int]:
    report = collect_lint_report(
        bundle_dir,
        manifests_dir=manifests_dir,
        brand=brand_id,
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
    pr_body_out: Path | str | None = None,
    git_branch: str | None = None,
) -> BackportResult:
    """Back-ports Console UI edits into Rung 1 `manifests/` or `core/`."""
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

    c_dir = Path(core_dir).resolve()
    m_dir = Path(manifests_dir).resolve()
    if not c_dir.is_dir():
        raise BuildGateError(f"Core directory does not exist: {c_dir}")
    if not m_dir.is_dir():
        raise BuildGateError(f"Manifests directory does not exist: {m_dir}")

    repo_root = (
        c_dir.parent
        if c_dir.parent == m_dir.parent
        else (
            m_dir.parent if (m_dir.parent / ".git").exists() else c_dir.parent
        )
    )
    git_precheck: tuple[str, str] | None = None
    if git_branch is not None:
        git_precheck = _validate_git_branch_precheck(repo_root, git_branch)

    try:
        manifests = load_manifests(m_dir)
    except ManifestError as exc:
        raise BuildGateError(str(exc)) from exc

    live_root = Path(live_dir).resolve()
    dist_root = Path(dist_dir).resolve() if dist_dir is not None else None

    prelim_live = live_root if (live_root / "app.json").is_file() else None
    prelim_dist = (
        dist_root
        if (dist_root is not None and (dist_root / "app.json").is_file())
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
        # Real CES exports carry no deployments/*.yaml: infer the app from
        # the multi-brand dist/ root when exactly one bundle best matches.
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
        manifests.brand(eff_brand)
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
    if not live_bundle.is_dir() or not (live_bundle / "app.json").is_file():
        raise BuildGateError(
            f"Live bundle directory is missing or has no app.json: "
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
            f"{rep.brand_id}/{rep.channel.lower()}": (
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
                    if (dist_root / "app.json").is_file()
                    else dist_root
                )
                eff_conflicts = c_base / "conflicts" / dash_app_id
            else:
                eff_conflicts = repo_root / "conflicts" / dash_app_id

            # Without an explicit --conflicts-dir, stage `.diff` markers in
            # the scratch dir and only persist them when conflicts remain
            # unresolved (so --force-live leaves no side-effect files).
            drift_rep = check_drift(
                canonical_app_id,
                ours_bundle,
                live_bundle,
                lockfile_path=eff_lock,
                conflicts_dir=(
                    eff_conflicts
                    if conflicts_dir is not None
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
                if conflicts_dir is None:
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
            # Git advanced these resources after the last lock while Console
            # left them untouched (`live == base`, `ours != base`): they are
            # NOT live edits and must keep Git's (ours) state. Only `CLEAN`
            # entries qualify: an `--accept-live` hotfix (`accepted_from_live`)
            # reports `DRIFT` with `live == base` and MUST be backported.
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
            # Promotion (--scope brand / --target core) always lifts existing
            # narrower overrides too, even when live also has fresh edits, so
            # a second run is a no-op (idempotent).
            return sorted(
                (set(direct_changed_ids) | set(promoted)) - git_ahead_ids
            )

        try:
            if target == "brand" and scope == "channel":
                changed_res_ids = direct_changed_ids
                if changed_res_ids:
                    brand_only_bundles = _compile_clean_brand_baselines(
                        c_dir,
                        m_dir,
                        eff_brand,
                        eff_env,
                        clear_all_brand=False,
                        channel_to_clear=eff_channel,
                        work_dir=work_root / "brand_only_base",
                    )
            elif target == "brand" and scope == "brand":
                unoverridden_bundles = _compile_clean_brand_baselines(
                    c_dir,
                    m_dir,
                    eff_brand,
                    eff_env,
                    clear_all_brand=True,
                    channel_to_clear=None,
                    work_dir=work_root / "unoverridden_base",
                )
                brand_only_bundles = _compile_clean_brand_baselines(
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
                unoverridden_bundles = _compile_clean_brand_baselines(
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
            if git_precheck is not None:
                checked_out_branch = _checkout_git_branch(
                    repo_root, git_precheck[0], git_precheck[1]
                )
            unchanged_siblings = sorted(
                b_id for b_id in pre_hashes if b_id != canonical_app_id
            )
            out_lock_path: Path | None = eff_lock
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
                    live_bundle, lockfile_path, app_id=ours_bundle.name
                )

            noop_res = BackportResult(
                app_id=canonical_app_id,
                brand_id=eff_brand,
                channel=eff_channel,
                environment=eff_env,
                target=target,
                scope=scope,
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
            if pr_body_out is not None:
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

        core_snapshot = _snapshot_directory_bytes(c_dir)
        man_snapshot = _snapshot_directory_bytes(m_dir)
        try:
            if target == "brand":
                base_map = (
                    brand_only_bundles
                    if scope == "channel" and brand_only_bundles is not None
                    else (unoverridden_bundles or {})
                )
                modified_files, changes = _apply_brand_target_backport(
                    core_dir=c_dir,
                    manifests_dir=m_dir,
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
                modified_files, changes = _apply_core_target_backport(
                    core_dir=c_dir,
                    manifests_dir=m_dir,
                    manifests=manifests,
                    brand_id=eff_brand,
                    channel=eff_channel,
                    environment=eff_env,
                    ours_bundle=ours_bundle,
                    live_bundle=live_bundle,
                    unoverridden_bundle=unoverridden_bundles[eff_channel],
                    changed_res_ids=changed_res_ids,
                    changed_py_files=changed_py_files,
                    # Always reconcile against the unoverridden baseline so
                    # existing brand/channel overrides are promoted alongside
                    # fresh live edits.
                    is_promotion_only=True,
                )

            post_dist_root = work_root / "post_dist"
            try:
                post_reports = build_all(c_dir, m_dir, post_dist_root, eff_env)
            except (BuildError, ManifestError) as exc:
                raise BuildGateError(str(exc)) from exc

            post_target_bundle = (
                post_dist_root / eff_brand / eff_channel.lower()
            )
            post_norm_by_app: dict[str, dict[str, NormalizedResource]] = {
                f"{rep.brand_id}/{rep.channel.lower()}": (
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
                    app_key = f"{rep.brand_id}/{rep.channel.lower()}"
                    if app_key == canonical_app_id or post_hashes[
                        app_key
                    ] != pre_hashes.get(app_key):
                        e_cnt, w_cnt = _enforce_lint_clean(
                            rep.out_dir,
                            manifests_dir=m_dir,
                            brand_id=rep.brand_id,
                            channel=rep.channel,
                            stage_label="recompiled bundle",
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
                # Git-ahead resources must keep Git's state; every other
                # resource must reproduce the live export byte-for-byte.
                want_sha = (
                    ours_norm[r_id].sha256
                    if r_id in git_ahead_ids
                    else l_res.sha256
                )
                if p_res.sha256 != want_sha:
                    raise BuildGateError(
                        f"Round-trip verification failed on {r_id}: "
                        "recompiled Rung 1 bundle did not match "
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

            if dist_root is None and lockfile_path is not None:
                # The verified live export is the new deployed baseline.
                final_lock_from_post: Path | None = write_lockfile(
                    live_bundle,
                    lockfile_path,
                    app_id=post_target_bundle.name,
                )
            else:
                final_lock_from_post = eff_lock

            if git_precheck is not None:
                checked_out_branch = _checkout_git_branch(
                    repo_root, git_precheck[0], git_precheck[1]
                )
        except Exception:
            _restore_directory_bytes(c_dir, core_snapshot)
            _restore_directory_bytes(m_dir, man_snapshot)
            raise

    final_lock_path: Path | None = final_lock_from_post
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

    result = BackportResult(
        app_id=canonical_app_id,
        brand_id=eff_brand,
        channel=eff_channel,
        environment=eff_env,
        target=target,
        scope=scope,
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
    if pr_body_out is not None:
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
