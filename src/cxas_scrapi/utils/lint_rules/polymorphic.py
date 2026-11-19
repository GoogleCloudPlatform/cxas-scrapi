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

"""Polymorphic (multi-brand / multi-channel) build rules (P001-P005).

These run on an app compiled by ``cxas build --core-dir/--manifests-dir``.
P001 and P004 need Rung 1 brand context: brand and channel are read from the
bundle's ``deployments/*.yaml`` (written by ``cxas build``) unless given via
``cxas lint --brand/--channel``; manifests come from ``--manifests-dir``, or
``<app>/manifests`` / ``<project>/manifests`` when present. Without context
those two rules are skipped.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from cxas_scrapi.utils.linter import (
    LintContext,
    LintResult,
    Rule,
    Severity,
    rule,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from cxas_scrapi.build.manifests import ChannelCapabilities, Manifests

_TEXT_SUFFIXES = (".txt", ".json", ".yaml", ".yml", ".py")
# Build metadata written by `cxas build`; never pushed (not an archive root).
_NON_APP_DIRS = frozenset({"deployments", "evals", "manifests"})

_UNRESOLVED_PLACEHOLDER_RE = re.compile(r"@@[A-Za-z0-9_.:\-]+@@")
_UNRESOLVED_COND_TAG_RE = re.compile(
    r"\{@(?:start|end)(?:Channel|Brand|AgentEnabled|ToolEnabled)_[^}]+\}"
)
_INSTRUCTION_REF_RE = re.compile(r"\{@(AGENT|TOOL):\s*([^}\s]+)\s*\}")

# Model values CES does not treat as a pinned GA model.
MODEL_ALIASES: frozenset[str] = frozenset({"default", "latest", "auto"})
_DEPRECATED_MODEL_RE = re.compile(r"gemini-(?:1\.|2\.0|2\.5)")


def _iter_app_text_files(app_root: Path) -> Iterator[Path]:
    for path in sorted(app_root.rglob("*")):
        if not path.is_file() or path.suffix not in _TEXT_SUFFIXES:
            continue
        rel_parts = path.relative_to(app_root).parts
        if rel_parts and rel_parts[0] in _NON_APP_DIRS:
            continue
        if any(part.startswith(".") for part in rel_parts):
            continue
        yield path


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


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
            data = yaml.safe_load(dep.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(data, dict) and data.get("brandId"):
            return data.get("brandId"), data.get("channel")
    return None, None


def resolve_brand_context(
    context: LintContext,
) -> tuple[Manifests | None, str | None, str | None]:
    """Returns (manifests, brand_id, channel) or Nones when unavailable."""
    from cxas_scrapi.build.manifests import (  # noqa: PLC0415
        ManifestError,
        load_manifests,
    )

    app_root = context.app_root or context.project_root
    brand, channel = context.brand_id, context.channel
    if not brand or not channel:
        dep_brand, dep_channel = _deployment_metadata(app_root)
        brand = brand or dep_brand
        channel = channel or dep_channel

    candidates = [
        context.manifests_dir,
        app_root / "manifests",
        context.project_root / "manifests",
    ]
    manifests_dir = next(
        (c for c in candidates if c is not None and Path(c).is_dir()), None
    )
    if manifests_dir is None or not brand:
        return None, brand, channel
    try:
        manifests = load_manifests(manifests_dir)
    except ManifestError:
        return None, brand, channel
    if brand not in manifests.brands:
        return None, brand, channel
    return manifests, brand, channel


@rule("structure")
class BrandTokenLeak(Rule):
    """P001: no sister-brand or forbidden token in a compiled brand app."""

    id = "P001"
    target = "app_config"
    name = "brand-token-leak"
    description = (
        "Compiled app must not contain a sister brand's display_name or a "
        "token from this brand's forbidden_tokens (Rung 1 manifests)."
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        manifests, brand_id, _ = resolve_brand_context(context)
        if manifests is None or brand_id is None:
            return []
        brand = manifests.brand(brand_id)
        own_name = brand.display_name.casefold()
        tokens = {
            other.display_name
            for other_id, other in manifests.brands.items()
            if other_id != brand_id
        } | set(brand.forbidden_tokens)
        tokens = {
            t for t in tokens if t.strip() and t.casefold() not in own_name
        }
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
                                f"Brand '{brand_id}' app contains forbidden "
                                f"token '{tok}' (found '{m.group(0)}')."
                            ),
                            fix=(
                                "Template the text with @@brand.*@@ tokens "
                                "in core/ or move it into the right brand's "
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
        max_tools = int(
            (context.options.get(self.id) or {}).get("max_tools", 20)
        )
        app_root = context.app_root or file_path.parent
        results: list[LintResult] = []
        for agent_json in sorted(app_root.glob("agents/*/*.json")):
            if agent_json.stem != agent_json.parent.name:
                continue
            tools = _load_json(agent_json).get("tools") or []
            if isinstance(tools, list) and len(tools) > max_tools:
                results.append(
                    self.make_result(
                        file=_rel(agent_json, context),
                        message=(
                            f"Agent '{agent_json.stem}' references "
                            f"{len(tools)} tools (cap {max_tools})."
                        ),
                        fix="Split the agent or move tools into toolsets.",
                    )
                )
        return results


@rule("structure")
class UnresolvedBuildTags(Rule):
    """P003: no unresolved Rung 1 template tags in compiled output."""

    id = "P003"
    target = "app_config"
    name = "unresolved-build-tags"
    description = (
        "Compiled app must not contain unresolved @@...@@ placeholders or "
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
            for pat in (_UNRESOLVED_PLACEHOLDER_RE, _UNRESOLVED_COND_TAG_RE):
                for m in pat.finditer(text):
                    results.append(
                        self.make_result(
                            file=_rel(path, context),
                            line=_line_of(text, m.start()),
                            message=f"Unresolved build tag '{m.group(0)}'.",
                            fix=(
                                "Compile with `cxas build` and declare the "
                                "value in the Rung 1 manifests."
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
        "must not exist or be referenced in the compiled app."
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        manifests, brand_id, channel = resolve_brand_context(context)
        if manifests is None or brand_id is None or channel is None:
            return []
        policy = manifests.policies.get(brand_id)
        caps: ChannelCapabilities | None = (
            policy.channels.get(channel) if policy else None
        )
        if caps is None:
            return []
        disabled = {
            "agent": set(caps.disabled_agents),
            "tool": set(caps.disabled_tools),
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
            for res_dir in sorted(app_root.glob(f"{sub}/*")):
                if not res_dir.is_dir():
                    continue
                display = _load_json(res_dir / f"{res_dir.name}.json").get(
                    "displayName", ""
                )
                if res_dir.name in disabled[kind] or display in disabled[kind]:
                    _flag(
                        res_dir,
                        f"{kind.title()} '{display or res_dir.name}' is "
                        f"disabled for {brand_id}/{channel} but present.",
                    )

        for agent_json in sorted(app_root.glob("agents/*/*.json")):
            data = _load_json(agent_json)
            for ref in data.get("tools") or []:
                if isinstance(ref, str) and ref in disabled["tool"]:
                    _flag(agent_json, f"References disabled tool '{ref}'.")
            for ref in data.get("childAgents") or []:
                if isinstance(ref, str) and ref in disabled["agent"]:
                    _flag(agent_json, f"References disabled agent '{ref}'.")

        for path in _iter_app_text_files(app_root):
            if path.suffix != ".txt":
                continue
            text = _read(path)
            for m in _INSTRUCTION_REF_RE.finditer(text):
                kind = m.group(1).lower()
                if m.group(2) in disabled[kind]:
                    _flag(
                        path,
                        f"Instruction references disabled {kind} "
                        f"'{m.group(2)}'.",
                        _line_of(text, m.start()),
                    )
        return results


def _model_slots(app_root: Path) -> Iterator[tuple[Path, str, Any, bool]]:
    """Yields (file, slot, model_value, required) for CES-applied slots."""
    app = _load_json(app_root / "app.json")
    app_model = (app.get("modelSettings") or {}).get("model")
    if app_model is not None:
        yield app_root / "app.json", "app modelSettings", app_model, True
    for agent_json in sorted(app_root.glob("agents/*/*.json")):
        if agent_json.stem != agent_json.parent.name:
            continue
        model = (_load_json(agent_json).get("modelSettings") or {}).get("model")
        yield (
            agent_json,
            f"agent '{agent_json.stem}' modelSettings",
            model,
            app_model is None,
        )
    for gr_json in sorted(app_root.glob("guardrails/*/*.json")):
        data = _load_json(gr_json)
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
                gr_json,
                f"guardrail {label}",
                (holder.get("modelSettings") or {}).get("model"),
                True,
            )
    for tool_json in sorted(app_root.glob("tools/*/*.json")):
        if tool_json.stem != tool_json.parent.name:
            continue
        ds = _load_json(tool_json).get("dataStoreTool")
        if not isinstance(ds, dict):
            continue
        for cfg in ds.get("modalityConfigs") or []:
            for key in ("rewriterConfig", "summarizationConfig"):
                sub = cfg.get(key) if isinstance(cfg, dict) else None
                if not isinstance(sub, dict) or sub.get("disabled") is True:
                    continue
                yield (
                    tool_json,
                    f"data store {cfg.get('modalityType', '?')} {key}",
                    (sub.get("modelSettings") or {}).get("model"),
                    True,
                )


@rule("structure")
class PinnedGaModels(Rule):
    """P005: CES-applied model slots must be pinned to non-deprecated models."""

    id = "P005"
    target = "app_config"
    name = "pinned-ga-models"
    description = (
        "Explicit agent, LLM guardrail and data-store rewriter/summarizer "
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
                problem = f"{slot}: model must be a string, got {model!r}"
            elif model.strip().lower() in MODEL_ALIASES or "latest" in (
                model.lower()
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
