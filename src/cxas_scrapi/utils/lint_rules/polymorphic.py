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

"""Polymorphic (profile / channel) build lint rules (P001-P005).

These rules validate apps built by ``cxas build --core-dir/--manifests-dir``.
P001 and P004 use build profile context: profile and channel are read from the
bundle's ``deployments/*.yaml`` (emitted by ``cxas build``) unless given via
``cxas lint --profile/--channel``; manifests come from ``--manifests-dir``, or
``<app>/manifests`` / ``<project>/manifests`` when present. Without profile
context (and when no explicit ``--manifests-dir`` / ``--profile`` /
``--channel`` was supplied), those two rules are skipped cleanly.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from cxas_scrapi.utils.linter import (
    CES_PLATFORM_SYSTEM_TOOLS,
    LintContext,
    LintResult,
    Rule,
    Severity,
    rule,
)
from cxas_scrapi.utils.yaml_utils import Yaml12SafeLoader

if TYPE_CHECKING:
    from collections.abc import Iterator

_PROFILE_ID_RE = re.compile(r"^[a-z0-9_-]{2,64}$")
_TEXT_SUFFIXES = (".txt", ".json", ".yaml", ".yml", ".py")
_CONFIG_SUFFIXES = (".json", ".yaml", ".yml")
# Build metadata written by `cxas build`; never pushed (not an archive root).
_NON_APP_DIRS = frozenset({"deployments", "evals", "manifests"})

_UNRESOLVED_PLACEHOLDER_RE = re.compile(r"@@[A-Za-z0-9_.:\-]+@@")
_DANGLING_PLACEHOLDER_RE = re.compile(
    r"@@(?:profile|brand|env|routing|build)\.[A-Za-z0-9_.:\-]+"
)
_UNRESOLVED_COND_TAG_RE = re.compile(
    r"\{@(?:start|end)(?:Channel|Profile|Brand|AgentEnabled|ToolEnabled)_[^}]+\}"
)
_INSTRUCTION_REF_RE = re.compile(
    r"\{@(AGENT|TOOL)[:\s]+([^}]+?)\s*\}", re.IGNORECASE
)

# Model values CES does not treat as a pinned GA model.
MODEL_ALIASES: frozenset[str] = frozenset({"default", "latest", "auto"})
_DEPRECATED_MODEL_RE = re.compile(r"gemini-(?:1\.|2\.0|2\.5)", re.IGNORECASE)


@dataclass(frozen=True)
class _ProfileInfo:
    profile_id: str
    display_name: str
    forbidden_tokens: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ChannelCapsInfo:
    disabled_agents: tuple[str, ...] = ()
    disabled_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class _PolicyInfo:
    profile_id: str
    channels: dict[str, _ChannelCapsInfo] = field(default_factory=dict)


@dataclass(frozen=True)
class _LintManifests:
    profiles: dict[str, _ProfileInfo]
    policies: dict[str, _PolicyInfo]

    def profile(self, profile_id: str) -> _ProfileInfo:
        return self.profiles[profile_id]


def _load_lint_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.load(
            path.read_text(encoding="utf-8"), Loader=Yaml12SafeLoader
        )
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"{path}: invalid YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    return data


def _load_lint_manifests(root: Path) -> _LintManifests:
    """Lightweight leaf manifest loader avoiding ``utils -> build`` imports."""
    if not root.is_dir():
        raise ValueError(f"Manifests root '{root}' is not a directory")
    profiles_dir = root / "profiles"
    if not profiles_dir.is_dir():
        raise ValueError(f"Missing '{profiles_dir}' directory")

    profiles: dict[str, _ProfileInfo] = {}
    policies: dict[str, _PolicyInfo] = {}
    for p_dir in sorted(p for p in profiles_dir.iterdir() if p.is_dir()):
        if not _PROFILE_ID_RE.fullmatch(p_dir.name):
            raise ValueError(f"Invalid profile directory '{p_dir.name}'")
        id_path = p_dir / "identity.yaml"
        if not id_path.is_file():
            raise ValueError(f"Missing '{id_path}'")
        id_data = _load_lint_yaml(id_path)
        pid = id_data.get("profile_id")
        display = id_data.get("display_name")
        if not isinstance(pid, str) or pid != p_dir.name:
            raise ValueError(
                f"{id_path}: profile_id '{pid}' does not match '{p_dir.name}'"
            )
        if not isinstance(display, str) or not display:
            raise ValueError(f"{id_path}: display_name must be non-empty")
        raw_forbidden = id_data.get("forbidden_tokens") or []
        if not isinstance(raw_forbidden, list) or not all(
            isinstance(t, str) for t in raw_forbidden
        ):
            raise ValueError(
                f"{id_path}: forbidden_tokens must be a list of strings"
            )
        profiles[pid] = _ProfileInfo(
            profile_id=pid,
            display_name=display,
            forbidden_tokens=tuple(raw_forbidden),
        )

        pol_path = p_dir / "channel_policy.yaml"
        if pol_path.is_file():
            pol_data = _load_lint_yaml(pol_path)
            pol_pid = pol_data.get("profile_id")
            if not isinstance(pol_pid, str) or pol_pid != p_dir.name:
                raise ValueError(
                    f"{pol_path}: profile_id '{pol_pid}' does not match "
                    f"'{p_dir.name}'"
                )
            raw_channels = pol_data.get("channels")
            if not isinstance(raw_channels, dict) or not raw_channels:
                raise ValueError(
                    f"{pol_path}: channels must be a non-empty mapping"
                )
            channels: dict[str, _ChannelCapsInfo] = {}
            for ch_name, ch_cfg in raw_channels.items():
                if not isinstance(ch_name, str) or not isinstance(ch_cfg, dict):
                    raise ValueError(f"{pol_path}: invalid channel '{ch_name}'")
                da = ch_cfg.get("disabled_agents") or []
                dt = ch_cfg.get("disabled_tools") or []
                if not isinstance(da, list) or not isinstance(dt, list):
                    raise ValueError(
                        f"{pol_path}: disabled_agents/disabled_tools must be "
                        "lists"
                    )
                channels[ch_name] = _ChannelCapsInfo(
                    disabled_agents=tuple(str(x) for x in da),
                    disabled_tools=tuple(str(x) for x in dt),
                )
            policies[pid] = _PolicyInfo(profile_id=pid, channels=channels)

    return _LintManifests(profiles=profiles, policies=policies)


def _iter_app_text_files(app_root: Path) -> Iterator[Path]:
    for path in sorted(app_root.rglob("*")):
        if not path.is_file() or path.suffix not in _TEXT_SUFFIXES:
            continue
        rel_parts = path.relative_to(app_root).parts
        if rel_parts and rel_parts[0] in _NON_APP_DIRS:
            continue
        if any(
            part.startswith(".") or part == "__pycache__" for part in rel_parts
        ):
            continue
        yield path


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _load_config(path: Path) -> dict[str, Any]:
    """Load a JSON or YAML mapping file, returning ``{}`` on error/non-dict."""
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in (".yaml", ".yml"):
            data = yaml.load(text, Loader=Yaml12SafeLoader)
        else:
            data = json.loads(text)
    except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _find_app_config(app_root: Path) -> Path | None:
    for suffix in _CONFIG_SUFFIXES:
        candidate = app_root / f"app{suffix}"
        if candidate.is_file():
            return candidate
    return None


def _find_resource_config(res_dir: Path) -> Path | None:
    """Find ``<res_dir>/<res_dir.name>.{json,yaml,yml}`` (preferring JSON)."""
    for suffix in _CONFIG_SUFFIXES:
        candidate = res_dir / f"{res_dir.name}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def _iter_resource_configs(app_root: Path, subdir: str) -> Iterator[Path]:
    """Yield one config path per resource dir under ``app_root/subdir``."""
    base = app_root / subdir
    if not base.is_dir():
        return
    for res_dir in sorted(base.iterdir()):
        if not res_dir.is_dir():
            continue
        cfg = _find_resource_config(res_dir)
        if cfg is not None:
            yield cfg


def _iter_guardrail_configs(app_root: Path) -> Iterator[Path]:
    """Yield guardrail config files under ``app_root/guardrails/*/``."""
    base = app_root / "guardrails"
    if not base.is_dir():
        return
    for gr_dir in sorted(base.iterdir()):
        if not gr_dir.is_dir():
            continue
        preferred = _find_resource_config(gr_dir)
        if preferred is not None:
            yield preferred
            continue
        for path in sorted(gr_dir.iterdir()):
            if path.is_file() and path.suffix.lower() in _CONFIG_SUFFIXES:
                yield path


def _rel(path: Path, context: LintContext) -> str:
    try:
        return str(path.relative_to(context.project_root))
    except ValueError:
        return str(path)


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _deployment_metadata(app_root: Path) -> tuple[str | None, str | None]:
    for dep in sorted(app_root.glob("deployments/*.yaml")):
        try:
            data = (
                yaml.load(
                    dep.read_text(encoding="utf-8"), Loader=Yaml12SafeLoader
                )
                or {}
            )
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        raw_profile = data.get("profileId")
        raw_channel = data.get("channel")
        if isinstance(raw_profile, str) and raw_profile.strip():
            channel = (
                raw_channel.strip()
                if isinstance(raw_channel, str) and raw_channel.strip()
                else None
            )
            return raw_profile.strip(), channel
    return None, None


def _resolve_profile_context_checked(
    context: LintContext,
) -> tuple[_LintManifests | None, str | None, str | None, str | None]:
    """Resolve ``(manifests, profile_id, channel, error_message)``.

    Explicitly supplied ``--manifests-dir``, ``--profile``, or ``--channel``
    flags never silently fall back or skip when missing/invalid; they return a
    non-None ``error_message`` so callers fail loudly.
    """
    app_root = context.app_root or context.project_root
    dep_profile, dep_channel = _deployment_metadata(app_root)
    explicit_profile = (
        context.profile_id
        if isinstance(context.profile_id, str) and context.profile_id.strip()
        else None
    )
    explicit_channel = (
        context.channel
        if isinstance(context.channel, str) and context.channel.strip()
        else None
    )
    profile = explicit_profile or dep_profile
    channel = explicit_channel or dep_channel

    if context.manifests_dir is not None:
        manifests_dir = Path(context.manifests_dir)
        if not manifests_dir.is_dir():
            return (
                None,
                profile,
                channel,
                f"Manifests directory not found: {manifests_dir}",
            )
        try:
            manifests = _load_lint_manifests(manifests_dir)
        except ValueError as exc:
            return (
                None,
                profile,
                channel,
                f"Failed to load manifests from '{manifests_dir}': {exc}",
            )
    else:
        auto_candidates = [
            app_root / "manifests",
            context.project_root / "manifests",
        ]
        manifests_dir = next((c for c in auto_candidates if c.is_dir()), None)
        if manifests_dir is None:
            if explicit_profile or explicit_channel:
                return (
                    None,
                    profile,
                    channel,
                    "--profile/--channel specified but no manifests directory "
                    "was found (pass --manifests-dir).",
                )
            return None, profile, channel, None
        try:
            manifests = _load_lint_manifests(manifests_dir)
        except ValueError as exc:
            if explicit_profile or explicit_channel:
                return (
                    None,
                    profile,
                    channel,
                    f"Failed to load manifests from '{manifests_dir}': {exc}",
                )
            return None, profile, channel, None

    if not profile:
        if context.manifests_dir is not None or explicit_channel:
            return (
                None,
                None,
                channel,
                "Could not determine profile ID from deployments/*.yaml "
                "(pass --profile).",
            )
        return None, None, channel, None

    if profile not in manifests.profiles:
        if explicit_profile or context.manifests_dir is not None:
            return (
                None,
                profile,
                channel,
                f"Unknown profile '{profile}'. Known profiles: "
                f"{sorted(manifests.profiles)}",
            )
        return None, profile, channel, None

    return manifests, profile, channel, None


def resolve_profile_context(
    context: LintContext,
) -> tuple[_LintManifests | None, str | None, str | None]:
    """Returns (manifests, profile_id, channel) or Nones when unavailable."""
    manifests, profile, channel, _err = _resolve_profile_context_checked(
        context
    )
    return manifests, profile, channel


def _parse_positive_int(raw: Any, default: int) -> int:
    """Parse a strictly positive integer option, rejecting bools/infinities."""
    if isinstance(raw, bool):
        return default
    if isinstance(raw, float) and not math.isfinite(raw):
        return default
    try:
        val = int(raw)
    except (TypeError, ValueError, OverflowError):
        return default
    return val if val > 0 else default


@rule("structure")
class BrandTokenLeak(Rule):
    """P001: no sister-profile or forbidden token in a built profile app."""

    id = "P001"
    target = "app_config"
    name = "brand-token-leak"
    description = (
        "Built app must not contain a sister profile's display_name or a "
        "token from this profile's forbidden_tokens."
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        manifests, profile_id, _, ctx_err = _resolve_profile_context_checked(
            context
        )
        if ctx_err:
            return [
                self.make_result(
                    file=_rel(file_path, context),
                    message=ctx_err,
                    fix=(
                        "Pass a valid --manifests-dir and --profile matching "
                        "manifests/profiles/."
                    ),
                )
            ]
        if manifests is None or profile_id is None:
            return []
        profile = manifests.profile(profile_id)
        own_name = profile.display_name.casefold()
        sister_tokens = {
            other.display_name
            for other_id, other in manifests.profiles.items()
            if other_id != profile_id
            and other.display_name.strip()
            and other.display_name.casefold() not in own_name
        }
        explicit_tokens = {t for t in profile.forbidden_tokens if t.strip()}
        tokens = sister_tokens | explicit_tokens
        if not tokens:
            return []
        patterns = {
            tok: re.compile(
                rf"(?<![A-Za-z0-9]){re.escape(tok)}(?![A-Za-z0-9])",
                re.IGNORECASE,
            )
            for tok in sorted(tokens)
        }
        app_root = context.app_root or file_path.parent
        results: list[LintResult] = []
        for path in _iter_app_text_files(app_root):
            text = _read(path)
            for tok, pat in patterns.items():
                m = pat.search(text)
                if m:
                    results.append(
                        self.make_result(
                            file=_rel(path, context),
                            line=_line_of(text, m.start()),
                            message=(
                                f"Profile '{profile_id}' app contains "
                                f"forbidden token '{tok}' "
                                f"(found '{m.group(0)}')."
                            ),
                            fix=(
                                "Template the text with @@profile.*@@ tokens "
                                "in core/ or move it into the right profile's "
                                "manifests."
                            ),
                        )
                    )
        return results


@rule("structure")
class AgentToolCountCap(Rule):
    """P002: keep each agent's tool list under a configurable cap."""

    id = "P002"
    target = "app_config"
    name = "agent-tool-count-cap"
    description = (
        "An agent should not reference more than `max_tools` tools "
        "(default 20; configure via cxaslint.yaml options.P002.max_tools)."
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        raw_opts = context.options.get(self.id)
        raw_cap = (
            raw_opts.get("max_tools", 20) if isinstance(raw_opts, dict) else 20
        )
        max_tools = _parse_positive_int(raw_cap, default=20)
        app_root = context.app_root or file_path.parent
        results: list[LintResult] = []
        for agent_cfg in _iter_resource_configs(app_root, "agents"):
            tools = _load_config(agent_cfg).get("tools")
            if isinstance(tools, list) and len(tools) > max_tools:
                results.append(
                    self.make_result(
                        file=_rel(agent_cfg, context),
                        message=(
                            f"Agent '{agent_cfg.stem}' references "
                            f"{len(tools)} tools (cap {max_tools})."
                        ),
                        fix="Split the agent or move tools into toolsets.",
                    )
                )
        return results


@rule("structure")
class UnresolvedBuildTags(Rule):
    """P003: no unresolved multi-brand build template tags in built output."""

    id = "P003"
    target = "app_config"
    name = "unresolved-build-tags"
    description = (
        "Built app must not contain unresolved @@...@@ placeholders or "
        "{@start/end(Channel|Brand|AgentEnabled|ToolEnabled)_...} tags."
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        app_root = context.app_root or file_path.parent
        results: list[LintResult] = []
        for path in _iter_app_text_files(app_root):
            text = _read(path)
            matched_spans: list[tuple[int, int]] = []
            for pat in (_UNRESOLVED_PLACEHOLDER_RE, _UNRESOLVED_COND_TAG_RE):
                for m in pat.finditer(text):
                    matched_spans.append((m.start(), m.end()))
                    results.append(
                        self.make_result(
                            file=_rel(path, context),
                            line=_line_of(text, m.start()),
                            message=f"Unresolved build tag '{m.group(0)}'.",
                            fix=(
                                "Rebuild with `cxas build` and declare the "
                                "value in the build manifests."
                            ),
                        )
                    )
            for m in _DANGLING_PLACEHOLDER_RE.finditer(text):
                if any(s <= m.start() < e for s, e in matched_spans):
                    continue
                results.append(
                    self.make_result(
                        file=_rel(path, context),
                        line=_line_of(text, m.start()),
                        message=f"Unclosed build tag '{m.group(0)}'.",
                        fix=(
                            "Close the @@...@@ placeholder and rebuild with "
                            "`cxas build`."
                        ),
                    )
                )
        return results


@rule("structure")
class PrunedResourcesAbsent(Rule):
    """P004: channel-disabled agents/tools are fully absent."""

    id = "P004"
    target = "app_config"
    name = "pruned-resources-absent"
    description = (
        "Agents/tools disabled for the brand's channel (channel_policy.yaml) "
        "must not exist or be referenced in the built app."
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        manifests, profile_id, channel, ctx_err = (
            _resolve_profile_context_checked(context)
        )
        if ctx_err:
            return [
                self.make_result(
                    file=_rel(file_path, context),
                    message=ctx_err,
                    fix=(
                        "Pass a valid --manifests-dir, --profile, and "
                        "--channel matching manifests/profiles/."
                    ),
                )
            ]
        if manifests is None or profile_id is None:
            return []
        if channel is None:
            if context.channel is not None:
                return [
                    self.make_result(
                        file=_rel(file_path, context),
                        message="Channel must be a non-empty string.",
                        fix="Pass a valid --channel from channel_policy.yaml.",
                    )
                ]
            return []
        policy = manifests.policies.get(profile_id)
        caps: _ChannelCapsInfo | None = (
            policy.channels.get(channel) if policy else None
        )
        if caps is None:
            if context.channel is not None:
                known = sorted(policy.channels) if policy else []
                return [
                    self.make_result(
                        file=_rel(file_path, context),
                        message=(
                            f"Unknown channel '{channel}' for profile "
                            f"'{profile_id}'. Known channels: {known}"
                        ),
                        fix=(
                            "Pass a valid --channel declared in "
                            "channel_policy.yaml."
                        ),
                    )
                ]
            return []
        disabled = {
            "agent": set(caps.disabled_agents),
            "tool": set(caps.disabled_tools) - CES_PLATFORM_SYSTEM_TOOLS,
        }
        app_root = context.app_root or file_path.parent
        results: list[LintResult] = []

        def _flag(path: Path, msg: str, line: int | None = None) -> None:
            results.append(
                self.make_result(
                    file=_rel(path, context),
                    line=line,
                    message=msg,
                    fix=(
                        "Rebuild with `cxas build`; remove the reference "
                        "from core/ or guard it with "
                        "{@startAgentEnabled_x}/{@startToolEnabled_x}."
                    ),
                )
            )

        for kind, sub in (("agent", "agents"), ("tool", "tools")):
            sub_dir = app_root / sub
            if not sub_dir.is_dir():
                continue
            for res_dir in sorted(sub_dir.iterdir()):
                if not res_dir.is_dir():
                    continue
                cfg_path = _find_resource_config(res_dir)
                raw_display = (
                    _load_config(cfg_path).get("displayName")
                    if cfg_path is not None
                    else None
                )
                display = raw_display if isinstance(raw_display, str) else ""
                if res_dir.name in disabled[kind] or display in disabled[kind]:
                    _flag(
                        res_dir,
                        f"{kind.title()} '{display or res_dir.name}' is "
                        f"disabled for {profile_id}/{channel} but present.",
                    )

        app_cfg = _find_app_config(app_root)
        if app_cfg is not None:
            root_agent = _load_config(app_cfg).get("rootAgent")
            if isinstance(root_agent, str) and root_agent in disabled["agent"]:
                _flag(
                    app_cfg,
                    f"rootAgent references disabled agent '{root_agent}'.",
                )

        env_json = app_root / "environment.json"
        if env_json.is_file():
            env_tools = _load_config(env_json).get("tools")
            if isinstance(env_tools, dict):
                for tool_name in sorted(env_tools):
                    if (
                        isinstance(tool_name, str)
                        and tool_name in disabled["tool"]
                    ):
                        _flag(
                            env_json,
                            f"environment.json references disabled tool "
                            f"'{tool_name}'.",
                        )

        for agent_cfg in _iter_resource_configs(app_root, "agents"):
            data = _load_config(agent_cfg)
            tools = data.get("tools")
            if isinstance(tools, list):
                for ref in tools:
                    ref_name = (
                        ref.get("displayName") if isinstance(ref, dict) else ref
                    )
                    if (
                        isinstance(ref_name, str)
                        and ref_name in disabled["tool"]
                    ):
                        _flag(
                            agent_cfg, f"References disabled tool '{ref_name}'."
                        )
            child_agents = data.get("childAgents")
            if isinstance(child_agents, list):
                for ref in child_agents:
                    ref_name = (
                        ref.get("displayName") if isinstance(ref, dict) else ref
                    )
                    if (
                        isinstance(ref_name, str)
                        and ref_name in disabled["agent"]
                    ):
                        _flag(
                            agent_cfg,
                            f"References disabled agent '{ref_name}'.",
                        )

        for path in _iter_app_text_files(app_root):
            if path.suffix != ".txt":
                continue
            text = _read(path)
            for m in _INSTRUCTION_REF_RE.finditer(text):
                kind = m.group(1).lower()
                ref_name = m.group(2).strip()
                if ref_name in disabled[kind]:
                    _flag(
                        path,
                        f"Instruction references disabled {kind} '{ref_name}'.",
                        _line_of(text, m.start()),
                    )
        return results


def _extract_model_value(holder: Any) -> Any:
    if not isinstance(holder, dict):
        return None
    ms = holder.get("modelSettings")
    if not isinstance(ms, dict):
        return None
    return ms.get("model")


def _model_slots(app_root: Path) -> Iterator[tuple[Path, str, Any, bool]]:
    """Yields (file, slot, model_value, required) for CES-applied slots."""
    app_cfg = _find_app_config(app_root)
    app = _load_config(app_cfg) if app_cfg is not None else {}
    app_model = _extract_model_value(app)
    if app_cfg is not None and app_model is not None:
        yield app_cfg, "app modelSettings", app_model, True
    for agent_cfg in _iter_resource_configs(app_root, "agents"):
        model = _extract_model_value(_load_config(agent_cfg))
        yield (
            agent_cfg,
            f"agent '{agent_cfg.stem}' modelSettings",
            model,
            app_model is None,
        )
    for gr_cfg in _iter_guardrail_configs(app_root):
        data = _load_config(gr_cfg)
        holders = []
        if isinstance(data.get("llmPolicy"), dict):
            holders.append(("llmPolicy", data["llmPolicy"]))
        sec = data.get("llmPromptSecurity")
        if isinstance(sec, dict) and isinstance(sec.get("customPolicy"), dict):
            holders.append(
                ("llmPromptSecurity.customPolicy", sec["customPolicy"])
            )
        for label, holder in holders:
            yield (
                gr_cfg,
                f"guardrail {label}",
                _extract_model_value(holder),
                True,
            )
    for tool_cfg in _iter_resource_configs(app_root, "tools"):
        ds = _load_config(tool_cfg).get("dataStoreTool")
        if not isinstance(ds, dict):
            continue
        modality_configs = ds.get("modalityConfigs")
        if not isinstance(modality_configs, list):
            continue
        for cfg in modality_configs:
            if not isinstance(cfg, dict):
                continue
            for key in ("rewriterConfig", "summarizationConfig"):
                sub = cfg.get(key)
                if not isinstance(sub, dict) or sub.get("disabled") is True:
                    continue
                yield (
                    tool_cfg,
                    f"data store {cfg.get('modalityType', '?')} {key}",
                    _extract_model_value(sub),
                    True,
                )


@rule("structure")
class PinnedGaModels(Rule):
    """P005: CES-applied model slots must be pinned to non-deprecated models."""

    id = "P005"
    target = "app_config"
    name = "pinned-ga-models"
    description = (
        "Explicit agent, LLM guardrail, and data-store rewriter/summarizer "
        "models must not be an alias (default/latest/auto) or a deprecated "
        "gemini-1.x/2.0/2.5 model."
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        app_root = context.app_root or file_path.parent
        results: list[LintResult] = []
        for path, slot, model, _required in _model_slots(app_root):
            problem = None
            if model is None:
                # No explicit model: CES applies its own default. Not flagged
                # so plain apps keep linting clean; pin via env `models:`.
                continue
            if not isinstance(model, str) or not model.strip():
                problem = (
                    f"{slot}: model must be a non-empty string, got {model!r}"
                )
            elif (
                model.strip().lower() in MODEL_ALIASES
                or "latest" in model.lower()
            ):
                problem = f"{slot}: '{model}' is an alias, pin a GA model"
            elif _DEPRECATED_MODEL_RE.search(model):
                problem = f"{slot}: '{model}' is a deprecated model"
            if problem:
                results.append(
                    self.make_result(
                        file=_rel(path, context),
                        message=problem + ".",
                        fix=(
                            "Pin models in the env manifest `models:` block "
                            "(agent / guardrail / data_store)."
                        ),
                    )
                )
        return results
