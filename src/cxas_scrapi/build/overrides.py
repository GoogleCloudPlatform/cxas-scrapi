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

"""Per-brand and per-channel ``overrides.yaml`` layering engine."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import yaml

from cxas_scrapi.build.errors import BuildError
from cxas_scrapi.utils.yaml_utils import Yaml12SafeLoader

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from cxas_scrapi.build.manifests import (
        BrandOverrides,
        InstructionOverride,
        LineReplacement,
    )

# CES replaces OpenAPI `servers[].url` with this placeholder on export and
# moves the concrete URL into environment.json (`tools.<name>.openApiTool.url`).
CES_ENV_VAR_PLACEHOLDER = "$env_var"
_OPENAPI_SERVERS_HEADER_RE = re.compile(r"^servers:\s*(?:#.*)?$")
_OPENAPI_SERVER_URL_RE = re.compile(r"^(\s*(?:-\s+)?url:\s*)(.+?)\s*$")
_OPENAPI_LIST_ITEM_RE = re.compile(r"^(\s*)-(?:\s+.*)?$")
_OPENAPI_KEY_RE = re.compile(r"^(\s*(?:-\s+)?)([A-Za-z0-9_.-]+):")
_CONFIG_SUFFIXES = (".json", ".yaml", ".yml")


def _assert_within_dir(path: Path, root: Path, *, label: str) -> Path:
    """Reject symlinks and paths resolving outside ``root``."""
    resolved_root = root.resolve()
    try:
        rel = path.relative_to(root)
    except ValueError:
        try:
            rel = path.relative_to(resolved_root)
        except ValueError as exc:
            raise BuildError(
                f"{label}: path '{path}' escapes output directory '{root}'."
            ) from exc

    curr = root
    for part in rel.parts:
        curr = curr / part
        if curr.is_symlink():
            raise BuildError(
                f"{label}: symlinks are not permitted in build output: {curr}"
            )

    resolved = path.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise BuildError(
            f"{label}: path '{path}' resolves outside '{resolved_root}'."
        )
    return resolved


def _find_resource_config(resource_dir: Path, resource_id: str) -> Path | None:
    """Find ``<id>.json``, ``<id>.yaml``, or ``<id>.yml`` inside a resource."""
    for suffix in _CONFIG_SUFFIXES:
        candidate = resource_dir / f"{resource_id}{suffix}"
        if candidate.is_file() or candidate.is_symlink():
            return candidate
    return None


def _read_config_dict(
    path: Path, out_dir: Path, *, label: str
) -> dict[str, Any]:
    _assert_within_dir(path, out_dir, label=label)
    try:
        raw = path.read_text("utf-8")
        if path.suffix.lower() in (".yaml", ".yml"):
            data = yaml.load(raw, Loader=Yaml12SafeLoader)
        else:
            data = json.loads(raw)
    except (
        json.JSONDecodeError,
        yaml.YAMLError,
        OSError,
        UnicodeDecodeError,
    ) as exc:
        raise BuildError(f"{label}: invalid config in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise BuildError(f"{label}: expected a mapping at top level of {path}.")
    return data


def _write_config_dict(
    path: Path, out_dir: Path, data: dict[str, Any], *, label: str
) -> None:
    _assert_within_dir(path, out_dir, label=label)
    if path.suffix.lower() in (".yaml", ".yml"):
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    else:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _set_model(holder: dict[str, Any], model: str) -> None:
    settings = holder.get("modelSettings")
    if not isinstance(settings, dict):
        settings = {}
    settings["model"] = model
    holder["modelSettings"] = settings


def _contains_line_sequence(lines: list[str], seq: list[str]) -> bool:
    """Return True if ``seq`` appears as a contiguous slice of ``lines``."""
    span = len(seq)
    if span == 0 or span > len(lines):
        return False
    return any(
        lines[idx : idx + span] == seq for idx in range(len(lines) - span + 1)
    )


def apply_line_replacements_strict(
    current_text: str,
    replacements: Sequence[LineReplacement],
    *,
    render_fn: Callable[[str], str],
    context_label: str,
) -> str:
    """Apply deterministic, strict line or unique-substring replacements."""
    if not replacements:
        return current_text

    norm = current_text.replace("\r\n", "\n")
    trailing_nl = norm.endswith("\n") or norm == ""
    body = norm.rstrip("\n")
    lines = body.split("\n") if body else []

    for rep in replacements:
        m_rendered = render_fn(rep.match.replace("\r\n", "\n")).rstrip("\n")
        w_rendered = render_fn(rep.replace_with.replace("\r\n", "\n"))
        if not m_rendered.strip():
            raise BuildError(
                f"{context_label}: empty or whitespace-only replace_lines "
                "match pattern."
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
        replaced_joined = joined.replace(m_rendered, w_rendered.rstrip("\n"), 1)
        lines = replaced_joined.split("\n") if replaced_joined else []

    rebuilt = "\n".join(lines)
    if trailing_nl and rebuilt:
        return rebuilt + "\n"
    return rebuilt


def apply_append_rules_lines(
    current_text: str,
    rules: Sequence[str],
    *,
    render_fn: Callable[[str], str],
) -> str:
    """Append rendered rule lines idempotently (skip existing rules)."""
    if not rules:
        return current_text

    base = current_text.replace("\r\n", "\n").rstrip("\n")
    lines = base.split("\n") if base else []
    changed = False

    for rule in rules:
        rendered = render_fn(rule.replace("\r\n", "\n")).rstrip("\n")
        if not rendered:
            continue
        rule_lines = rendered.split("\n")
        if _contains_line_sequence(lines, rule_lines):
            continue
        lines.extend(rule_lines)
        changed = True

    if not changed:
        return current_text

    return "\n".join(lines) + "\n"


def apply_instruction_override(
    current_text: str,
    brand_ov: InstructionOverride | None,
    ch_ov: InstructionOverride | None,
    *,
    render_fn: Callable[[str], str],
    context_label: str,
    identity_prefix: str | None = None,
) -> str:
    """Layer brand-wide and channel-scoped instruction overrides."""
    if ch_ov is not None and ch_ov.instruction is not None:
        rendered_ch = render_fn(ch_ov.instruction.replace("\r\n", "\n"))
        if identity_prefix and identity_prefix not in rendered_ch:
            text = identity_prefix + "\n\n" + rendered_ch.lstrip("\n")
        else:
            text = rendered_ch
        if text and not text.endswith("\n"):
            text += "\n"
        text = apply_line_replacements_strict(
            text,
            ch_ov.replace_lines,
            render_fn=render_fn,
            context_label=context_label,
        )
        return apply_append_rules_lines(
            text, ch_ov.append_rules, render_fn=render_fn
        )

    if brand_ov is not None and brand_ov.instruction is not None:
        rendered_br = render_fn(brand_ov.instruction.replace("\r\n", "\n"))
        if identity_prefix and identity_prefix not in rendered_br:
            text = identity_prefix + "\n\n" + rendered_br.lstrip("\n")
        else:
            text = rendered_br
        if text and not text.endswith("\n"):
            text += "\n"
    else:
        text = current_text

    if brand_ov is not None:
        text = apply_line_replacements_strict(
            text,
            brand_ov.replace_lines,
            render_fn=render_fn,
            context_label=context_label,
        )
        text = apply_append_rules_lines(
            text, brand_ov.append_rules, render_fn=render_fn
        )

    if ch_ov is not None:
        text = apply_line_replacements_strict(
            text,
            ch_ov.replace_lines,
            render_fn=render_fn,
            context_label=context_label,
        )
        text = apply_append_rules_lines(
            text, ch_ov.append_rules, render_fn=render_fn
        )

    return text


def _is_env_var_yaml_scalar(raw_value: str) -> bool:
    """Return True if a YAML scalar represents ``$env_var`` (quoted or not)."""
    try:
        parsed = yaml.load(raw_value, Loader=Yaml12SafeLoader)
    except yaml.YAMLError:
        return raw_value.strip() == CES_ENV_VAR_PLACEHOLDER
    return parsed == CES_ENV_VAR_PLACEHOLDER


def _rewrite_openapi_servers_urls(
    oa_raw: str, srv_val: str
) -> tuple[str, bool]:
    """Update ``url:`` entries strictly inside the top-level ``servers:`` block.

    Supports both single-line (``- url: ...``) and multi-line server items
    (e.g. ``- description: ...`` followed by ``  url: ...``), preserves quoted
    or unquoted ``$env_var`` placeholders, skips nested ``variables:`` blocks,
    and never modifies ``url:`` lines outside top-level ``servers:``.
    """
    in_servers = False
    in_variables = False
    vars_indent = 0
    new_lines: list[str] = []
    changed = False

    for line in oa_raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue

        indent = len(line) - len(line.lstrip(" "))
        is_indent0_seq = indent == 0 and (
            stripped == "-" or stripped.startswith("- ")
        )
        if indent == 0 and not (in_servers and is_indent0_seq):
            in_servers = bool(_OPENAPI_SERVERS_HEADER_RE.match(line))
            in_variables = False
            new_lines.append(line)
            continue

        if not in_servers:
            new_lines.append(line)
            continue

        if _OPENAPI_LIST_ITEM_RE.match(line):
            in_variables = False

        if in_variables and indent <= vars_indent:
            in_variables = False

        m_key = _OPENAPI_KEY_RE.match(line)
        if m_key and m_key.group(2) == "variables" and not in_variables:
            in_variables = True
            vars_indent = len(m_key.group(1))
            new_lines.append(line)
            continue

        if not in_variables:
            m_srv = _OPENAPI_SERVER_URL_RE.match(line)
            if m_srv:
                prefix, raw_val = m_srv.group(1), m_srv.group(2)
                if not _is_env_var_yaml_scalar(raw_val):
                    new_line = f"{prefix}{srv_val}"
                    if new_line != line:
                        changed = True
                    new_lines.append(new_line)
                    continue

        new_lines.append(line)

    rebuilt = "\n".join(new_lines) + ("\n" if oa_raw.endswith("\n") else "")
    return rebuilt, changed


def _resolve_openapi_schema_path(
    out_dir: Path, t_dir: Path, oa_cfg: dict[str, Any], *, label: str
) -> Path | None:
    """Resolve the OpenAPI schema YAML path strictly inside ``out_dir``."""
    raw_ref = oa_cfg.get("openApiSchema")
    if isinstance(raw_ref, str) and raw_ref.strip():
        candidate = out_dir / raw_ref.strip()
        _assert_within_dir(candidate, out_dir, label=label)
        if candidate.is_file():
            return candidate
    default_path = t_dir / "open_api_tool" / "open_api_schema.yaml"
    if default_path.exists() or default_path.is_symlink():
        _assert_within_dir(default_path, out_dir, label=label)
        if default_path.is_file():
            return default_path
    return None


def apply_brand_overrides(
    *,
    out_dir: Path,
    brand_id: str,
    channel: str,
    permitted_channels: set[str],
    ov: BrandOverrides,
    render_fn: Callable[[str], str],
    global_identity_prefix: str | None = None,
    global_identity_block: str | None = None,
    known_agents: set[str] | None = None,
    known_tools: set[str] | None = None,
    core_dir: Path | None = None,
    validate_modalities_fn: Callable[[str, Path], None] | None = None,
) -> None:
    """Apply 4-stage ``overrides.yaml`` layering onto a staged build tree.

    Stages:
        1. ``global_instruction`` (brand-wide + channel)
        2. ``agents`` (config description/temperature/model + instruction)
        3. ``tools`` (description across tool types; OpenAPI ``url`` +
           ``environment.json`` + OpenAPI schema ``servers[].url``)
        4. Per-channel overrides (``channels.<CHANNEL>``) layered on top at
           each stage.
    """
    if known_agents is None:
        if core_dir is not None and (core_dir / "agents").is_dir():
            known_agents = {
                p.name for p in (core_dir / "agents").iterdir() if p.is_dir()
            }
        elif (out_dir / "agents").is_dir():
            known_agents = {
                p.name for p in (out_dir / "agents").iterdir() if p.is_dir()
            }
        else:
            known_agents = set()

    if known_tools is None:
        if core_dir is not None and (core_dir / "tools").is_dir():
            known_tools = {
                p.name for p in (core_dir / "tools").iterdir() if p.is_dir()
            }
        elif (out_dir / "tools").is_dir():
            known_tools = {
                p.name for p in (out_dir / "tools").iterdir() if p.is_dir()
            }
        else:
            known_tools = set()

    # Validate targets and channels before mutating any file on disk.
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
        if agent_id not in known_agents:
            raise BuildError(
                f"Brand '{brand_id}' agent override references unknown agent "
                f"'{agent_id}' (missing from staged agents/)."
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
        if tool_id not in known_tools:
            raise BuildError(
                f"Brand '{brand_id}' tool override references unknown tool "
                f"'{tool_id}' (missing from staged tools/)."
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

    # Stage 1: global_instruction.txt
    if ov.global_instruction is not None:
        gi_label = f"{brand_id}/{channel} global_instruction.txt"
        gi_path = out_dir / "global_instruction.txt"
        _assert_within_dir(gi_path, out_dir, label=gi_label)
        cur_gi = gi_path.read_text("utf-8") if gi_path.is_file() else ""
        g_ch_ov = ov.global_instruction.channels.get(channel)

        id_block_rendered = (
            global_identity_block
            if global_identity_block is not None
            else global_identity_prefix
        )

        def _global_render(text: str) -> str:
            if (
                id_block_rendered is not None
                and "@@profile.identity_block@@" in text
            ):
                text = text.replace(
                    "@@profile.identity_block@@", id_block_rendered
                )
            return render_fn(text)

        new_gi = apply_instruction_override(
            cur_gi,
            ov.global_instruction,
            g_ch_ov,
            render_fn=_global_render,
            context_label=gi_label,
            identity_prefix=global_identity_prefix,
        )
        if (
            new_gi
            and not cur_gi
            and global_identity_prefix
            and global_identity_prefix not in new_gi
        ):
            new_gi = global_identity_prefix + "\n\n" + new_gi.lstrip("\n")
        if new_gi != cur_gi:
            gi_path.write_text(new_gi, "utf-8")
            if validate_modalities_fn is not None:
                validate_modalities_fn(new_gi, gi_path.relative_to(out_dir))

    # Stage 2: agents/<agent_id>/
    for agent_id, ag_ov in sorted(ov.agents.items()):
        ag_dir = out_dir / "agents" / agent_id
        if not ag_dir.is_dir():
            continue
        ag_label = f"{brand_id}/{channel} agents/{agent_id}"
        _assert_within_dir(ag_dir, out_dir, label=ag_label)
        a_ch_ov = ag_ov.channels.get(channel)

        ag_data: dict[str, Any] | None = None
        ag_cfg_path = _find_resource_config(ag_dir, agent_id)
        if ag_cfg_path is not None:
            ag_data = _read_config_dict(ag_cfg_path, out_dir, label=ag_label)
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
                _write_config_dict(
                    ag_cfg_path, out_dir, ag_data, label=ag_label
                )

        instr_path = ag_dir / "instruction.txt"
        raw_instr = (
            ag_data.get("instruction") if isinstance(ag_data, dict) else None
        )
        if isinstance(raw_instr, str) and raw_instr.strip():
            rel_instr = raw_instr.strip()
            candidate = out_dir / rel_instr
            if not candidate.is_file() and (
                ("/" not in rel_instr and "\\" not in rel_instr)
                or (ag_dir / rel_instr).is_file()
            ):
                candidate = ag_dir / rel_instr
            instr_path = candidate
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
            instr_label = f"{ag_label}/{instr_path.name}"
            _assert_within_dir(instr_path, out_dir, label=instr_label)
            cur_in = (
                instr_path.read_text("utf-8") if instr_path.is_file() else ""
            )
            new_in = apply_instruction_override(
                cur_in,
                ag_ov,
                a_ch_ov,
                render_fn=render_fn,
                context_label=instr_label,
            )
            instr_path.parent.mkdir(parents=True, exist_ok=True)
            instr_path.write_text(new_in, "utf-8")
            if validate_modalities_fn is not None:
                validate_modalities_fn(new_in, instr_path.relative_to(out_dir))

    # Stage 3: tools/<tool_id>/
    for tool_id, t_ov in sorted(ov.tools.items()):
        t_dir = out_dir / "tools" / tool_id
        if not t_dir.is_dir():
            continue
        t_label = f"{brand_id}/{channel} tools/{tool_id}"
        _assert_within_dir(t_dir, out_dir, label=t_label)
        t_ch_ov = t_ov.channels.get(channel)

        t_cfg_path = _find_resource_config(t_dir, tool_id)
        if t_cfg_path is None:
            continue
        t_data = _read_config_dict(t_cfg_path, out_dir, label=t_label)
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
            oa_cfg = t_data.get("openApiTool")
            if not isinstance(oa_cfg, dict):
                raise BuildError(
                    f"Brand '{brand_id}' tool '{tool_id}' override sets 'url' "
                    "but the tool is not an openApiTool; 'url' overrides are "
                    "only supported for OpenAPI tools."
                )
            r_url = render_fn(eff_t_url)
            display_name = str(t_data.get("displayName") or tool_id)

            env_path = out_dir / "environment.json"
            env_updated = False
            if env_path.is_file() or env_path.is_symlink():
                env_data = _read_config_dict(
                    env_path, out_dir, label=f"{t_label} environment.json"
                )
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
                        _write_config_dict(
                            env_path,
                            out_dir,
                            env_data,
                            label=f"{t_label} environment.json",
                        )

            if (not env_updated) or (
                "url" in oa_cfg and oa_cfg.get("url") != CES_ENV_VAR_PLACEHOLDER
            ):
                oa_cfg["url"] = r_url
                t_mod = True

            oa_yaml_path = _resolve_openapi_schema_path(
                out_dir, t_dir, oa_cfg, label=t_label
            )
            if oa_yaml_path is not None:
                oa_raw = oa_yaml_path.read_text("utf-8")
                try:
                    oa_doc = yaml.load(oa_raw, Loader=Yaml12SafeLoader)
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
                rebuilt_oa, oa_changed = _rewrite_openapi_servers_urls(
                    oa_raw, srv_val
                )
                if oa_changed:
                    oa_yaml_path.write_text(rebuilt_oa, "utf-8")

        if t_mod:
            _write_config_dict(t_cfg_path, out_dir, t_data, label=t_label)


def pruned_reference_warnings(
    out_dir: Path, disabled_agents: set[str], disabled_tools: set[str]
) -> list[str]:
    """List ``{@AGENT: x}`` / ``{@TOOL: x}`` mentions of pruned resources."""
    if not disabled_agents and not disabled_tools:
        return []
    found: list[str] = []
    for path in sorted(out_dir.rglob("*.txt")):
        if not path.is_file() or path.is_symlink():
            continue
        text = path.read_text("utf-8")
        rel = path.relative_to(out_dir).as_posix()
        for a in sorted(disabled_agents):
            if re.search(rf"\{{@AGENT:\s*{re.escape(a)}\s*\}}", text):
                found.append(f"{rel} mentions pruned agent {a}")
        for t in sorted(disabled_tools):
            if re.search(rf"\{{@TOOL:\s*{re.escape(t)}\s*\}}", text):
                found.append(f"{rel} mentions pruned tool {t}")
    return found
