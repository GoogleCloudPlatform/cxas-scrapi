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

"""Pydantic v2 schemas, exceptions, and shared I/O helpers for blueprints."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

# Single-brace `{placeholder}` token (including hyphenated or dotted typos
# such as `{brand-name}` or `{session_ctx.surface_id}`), strictly excluding
# runtime `{{session_var}}` double braces and `{@TAG: ...}` references.
SINGLE_BRACE_PLACEHOLDER_RE = re.compile(
    r"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_.-]*)\}(?!\})"
)


@dataclass(frozen=True)
class FirewallViolation:
    """Single static architecture firewall violation (`FW01`..`FW10`)."""

    rule_id: str
    message: str
    path: str = ""

    @property
    def code(self) -> str:
        return self.rule_id

    @property
    def rule(self) -> str:
        return self.rule_id

    def to_dict(self) -> dict[str, str]:
        return {
            "message": self.message,
            "path": self.path,
            "rule_id": self.rule_id,
        }

    def __str__(self) -> str:
        loc = f" ({self.path})" if self.path else ""
        return f"[{self.rule_id}]{loc}: {self.message}"


@dataclass(frozen=True)
class NormalizedResource:
    """Canonical volatile-stripped representation of a single CES resource."""

    resource_id: str
    sha256: str
    normalized: str
    accepted_from_live: bool = False

    @property
    def res_id(self) -> str:
        return self.resource_id


@dataclass
class ResourceDrift:
    """3-way comparison outcome for a single logical CES resource."""

    resource_id: str
    status: Literal["CLEAN", "DRIFT", "ORPHAN", "CONFLICT"]
    base_sha256: str | None = None
    ours_sha256: str | None = None
    live_sha256: str | None = None
    resolved_by: Literal["accept-live", "force-ours"] | None = None
    conflict_path: Path | None = None

    @property
    def res_id(self) -> str:
        return self.resource_id

    @property
    def diff_path(self) -> Path | None:
        return self.conflict_path

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_sha256": self.base_sha256,
            "conflict_path": (
                str(self.conflict_path) if self.conflict_path else None
            ),
            "live_sha256": self.live_sha256,
            "ours_sha256": self.ours_sha256,
            "resolved_by": self.resolved_by,
            "resource_id": self.resource_id,
            "status": self.status,
        }


@dataclass
class DriftReport:
    """Aggregate 3-way drift, conflict, and orphan report for a compiled app."""

    app_id: str
    resources: list[ResourceDrift] = field(default_factory=list)
    git_mirror_commit: str | None = None

    @property
    def is_clean(self) -> bool:
        return all(r.status == "CLEAN" for r in self.resources)

    @property
    def has_unresolved(self) -> bool:
        return any(
            r.status != "CLEAN" and r.resolved_by is None
            for r in self.resources
        )

    @property
    def by_id(self) -> dict[str, ResourceDrift]:
        return {r.resource_id: r for r in self.resources}

    def to_dict(self) -> dict[str, Any]:
        return {
            "app_id": self.app_id,
            "git_mirror_commit": self.git_mirror_commit,
            "has_unresolved": self.has_unresolved,
            "is_clean": self.is_clean,
            "resources": [r.to_dict() for r in self.resources],
        }


class BlueprintCompilerError(ValueError):
    """Base error raised on invalid blueprint schemas, builds, or inputs."""


CompilerError = BlueprintCompilerError


class FirewallError(BlueprintCompilerError):
    """Raised when one or more static architecture firewall invariants fail."""

    def __init__(
        self,
        message: str,
        violations: Sequence[FirewallViolation] = (),
    ) -> None:
        super().__init__(message)
        self.violations: list[FirewallViolation] = list(violations)


class DriftGateError(BlueprintCompilerError):
    """Raised when the pre-push gate refuses promotion on firewall or drift."""

    def __init__(
        self,
        message: str,
        *,
        violations: Sequence[FirewallViolation] = (),
        drift_report: DriftReport | None = None,
    ) -> None:
        super().__init__(message)
        self.violations: list[FirewallViolation] = list(violations)
        self.drift_report: DriftReport | None = drift_report


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelPoolsSpec(_StrictModel):
    voice_talker_thinker: str
    agent_planner: str
    guardrail_judge: str
    datastore_rewriter_summarizer: str
    eval_judge: str


class VariablePropertySpec(_StrictModel):
    type: str
    description: str = ""
    properties: dict[str, VariablePropertySpec] = Field(default_factory=dict)


VariablePropertySpec.model_rebuild()


class VariableSpec(_StrictModel):
    name: str
    type: str
    description: str = ""
    properties: dict[str, VariablePropertySpec] = Field(default_factory=dict)


class ToolSpec(_StrictModel):
    kind: Literal["open_api", "data_store", "python_function"]
    description: str
    execution_type: str = "SYNCHRONOUS"
    url_template: str | None = None
    method: str | None = None
    operation_id: str | None = None
    data_store_id_template: str | None = None
    filter_parameter_behavior: str | None = None
    python_code: str | None = None


class GuardrailSpec(_StrictModel):
    kind: Literal[
        "content_filter", "llm_policy", "llm_prompt_security", "supervisor"
    ]
    description: str
    enabled: bool = True
    match_type: str | None = None
    patterns: list[str] = Field(default_factory=list)
    policy_scope: str | None = None
    max_conversation_messages: int | None = None
    prompt: str | None = None
    detection_mode: str | None = None
    supervisor_type: str | None = None
    action_text: str | None = None


class RootAgentSpec(_StrictModel):
    description: str
    instruction: str
    sub_agents: list[str]
    tools: list[str] = Field(default_factory=list)
    callbacks: list[str] = Field(default_factory=list)


class SpecialistSpec(_StrictModel):
    description: str
    instruction: str
    tools: list[str] = Field(default_factory=list)
    callbacks: list[str] = Field(default_factory=list)


class BlueprintSpec(_StrictModel):
    schema_version: str
    blueprint_id: str
    display_name_template: str
    default_language_code: str
    supported_language_codes: list[str]
    time_zone: str
    tool_execution_mode: str
    locked: bool
    model_pools: ModelPoolsSpec
    variables: list[VariableSpec] = Field(default_factory=list)
    global_instruction: str
    tools: dict[str, ToolSpec] = Field(default_factory=dict)
    guardrails: dict[str, GuardrailSpec] = Field(default_factory=dict)
    root_agent: RootAgentSpec
    specialists: dict[str, SpecialistSpec] = Field(default_factory=dict)


class VoiceSpec(_StrictModel):
    voice_name: str
    speaking_rate: float = 1.0


class BrandSpec(_StrictModel):
    brand_id: str
    brand_name: str
    brand_slug: str
    tenant_id: int = 0
    branches_available: bool = True
    web_host: str
    voice: VoiceSpec
    ai_disclosure: str
    placeholders: dict[str, str] = Field(default_factory=dict)
    disabled_agents: list[str] = Field(default_factory=list)
    disabled_tools: list[str] = Field(default_factory=list)
    disabled_guardrails: list[str] = Field(default_factory=list)
    forbidden_tokens: list[str] = Field(default_factory=list)


class ModalitySpec(_StrictModel):
    modality_id: str
    modality_label: str
    planner_model_override: str | None = None
    keep_supervisor_guardrails: bool
    audio_rewriter_disabled: bool
    barge_in_enabled: bool = False
    disabled_agents: list[str] = Field(default_factory=list)
    disabled_tools: list[str] = Field(default_factory=list)
    disabled_guardrails: list[str] = Field(default_factory=list)


class SurfacePolicySpec(_StrictModel):
    modality: str
    auth_state: str = "anonymous"
    entry_agent: str = "root_agent"
    blocked_agents: list[str] = Field(default_factory=list)
    description: str = ""


class SegmentPolicySpec(_StrictModel):
    description: str = ""
    blocked_agents: list[str] = Field(default_factory=list)


class KillSwitchSpec(_StrictModel):
    enabled: bool = True
    expression_template: str = "session_ctx.disabled_agents.{agent} == true"


class RuntimePolicySpec(_StrictModel):
    schema_version: str = "1.0"
    surfaces: dict[str, SurfacePolicySpec] = Field(default_factory=dict)
    segments: dict[str, SegmentPolicySpec] = Field(default_factory=dict)
    kill_switch: KillSwitchSpec = Field(default_factory=KillSwitchSpec)


class AgentOverlaySpec(_StrictModel):
    append_rules: list[str] = Field(default_factory=list)


class TuningOverlaySpec(_StrictModel):
    schema_version: str = "1.0"
    overlays: dict[str, dict[str, AgentOverlaySpec]] = Field(
        default_factory=dict
    )


def build_pruned_identifier_regex(
    pruned_agents: set[str], pruned_tools: set[str]
) -> re.Pattern[str] | None:
    """Compiles a regex matching pruned `{@AGENT:...}`/`{@TOOL:...}` or IDs."""
    tokens = sorted(
        {tok for tok in (pruned_agents | pruned_tools) if tok},
        key=len,
        reverse=True,
    )
    if not tokens:
        return None
    escaped = "|".join(re.escape(tok) for tok in tokens)
    pattern = (
        rf"(?:\{{\s*@(?:AGENT|TOOL):\s*(?:{escaped})\s*\}}|\b(?:{escaped})\b)"
    )
    return re.compile(pattern)


def load_yaml_dict(path: Path) -> dict[str, Any]:
    """Reads a YAML file into a top-level dictionary or raises CompilerError."""
    if not path.is_file():
        raise BlueprintCompilerError(f"Missing required YAML file: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
        raise BlueprintCompilerError(
            f"{path}: failed to read YAML: {exc}"
        ) from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise BlueprintCompilerError(
            f"{path}: expected a YAML mapping at top level"
        )
    return raw


def load_spec(model_cls: type[BaseModel], path: Path) -> Any:
    """Validates a YAML file against a strict Pydantic v2 schema."""
    data = load_yaml_dict(path)
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise BlueprintCompilerError(
            f"{path}: schema validation failed: {exc}"
        ) from exc


def read_utf8_strict(path: Path, *, context: str = "file") -> str:
    """Reads UTF-8 text from disk or raises BlueprintCompilerError."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise BlueprintCompilerError(
            f"Failed to read {context} {path}: {exc}"
        ) from exc


def load_json_dict_strict(
    path: Path, *, context: str = "JSON file"
) -> dict[str, Any]:
    """Reads a JSON object (dict) from disk or raises BlueprintCompilerError."""
    raw_text = read_utf8_strict(path, context=context)
    try:
        data = json.loads(raw_text)
    except ValueError as exc:
        raise BlueprintCompilerError(
            f"Corrupt JSON in {context} {path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise BlueprintCompilerError(
            f"Expected JSON object at root of {context} {path}, "
            f"got {type(data).__name__}."
        )
    return data


def write_canonical_json(path: Path, payload: dict[str, Any]) -> None:
    """Writes sorted, deterministic UTF-8 JSON with trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )


def write_text(path: Path, text: str) -> None:
    """Writes LF-normalized UTF-8 text ending with a single newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = text.replace("\r\n", "\n").rstrip("\n") + "\n"
    path.write_text(normalized, encoding="utf-8")


__all__ = [
    "SINGLE_BRACE_PLACEHOLDER_RE",
    "AgentOverlaySpec",
    "BlueprintCompilerError",
    "BlueprintSpec",
    "BrandSpec",
    "CompilerError",
    "DriftGateError",
    "DriftReport",
    "FirewallError",
    "FirewallViolation",
    "GuardrailSpec",
    "KillSwitchSpec",
    "ModalitySpec",
    "ModelPoolsSpec",
    "NormalizedResource",
    "ResourceDrift",
    "RootAgentSpec",
    "RuntimePolicySpec",
    "SegmentPolicySpec",
    "SpecialistSpec",
    "SurfacePolicySpec",
    "ToolSpec",
    "TuningOverlaySpec",
    "VariablePropertySpec",
    "VariableSpec",
    "VoiceSpec",
    "build_pruned_identifier_regex",
    "load_json_dict_strict",
    "load_spec",
    "load_yaml_dict",
    "read_utf8_strict",
    "write_canonical_json",
    "write_text",
]
