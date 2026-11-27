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


"""CXAS Agent Linter — framework, configuration, discovery, and runner.

Rule-based linting engine for validating CXAS agent apps against best
practices and structural requirements.  Inspired by pylint/ruff: rules
are first-class objects with IDs, configurable severity, per-file
overrides, and decorator-based auto-registration.

Configuration lives in ``cxaslint.yaml``.
"""

import ast
import fnmatch
import json
import re
import typing
import warnings
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml

from cxas_scrapi.utils.env_vars import (
    _TOOL_NAME_CONTAINERS,
    ENV_VAR_PLACEHOLDER,  # noqa: F401
)

# ── Shared Schema Field Keys & Sentinels ─────────────────────────────────

CONFIG_EXTENSIONS: tuple[str, ...] = (".json", ".yaml", ".yml")
APP_CONFIG_NAMES: tuple[str, ...] = ("app.json", "app.yaml", "app.yml")
FIELD_DISPLAY_NAME: tuple[str, str] = ("displayName", "display_name")
FIELD_CHILD_AGENTS: tuple[str, str] = ("childAgents", "child_agents")
FIELD_ROOT_AGENT: tuple[str, str] = ("rootAgent", "root_agent")
FIELD_OPEN_API_TOOL: tuple[str, str] = ("openApiTool", "open_api_tool")
FIELD_OPEN_API_TOOLSET: tuple[str, str] = (
    "openApiToolset",
    "open_api_toolset",
)
FIELD_OPEN_API_SCHEMA: tuple[str, str] = ("openApiSchema", "open_api_schema")
FIELD_MODEL_SETTINGS: tuple[str, str] = ("modelSettings", "model_settings")
_FIELD_CHANNEL_PROFILE: tuple[str, str] = (
    "channelProfile",
    "channel_profile",
)
_FIELD_DEFAULT_CHANNEL_PROFILE: tuple[str, str] = (
    "defaultChannelProfile",
    "default_channel_profile",
)
_FIELD_CHANNEL_TYPE: tuple[str, str] = ("channelType", "channel_type")
_FIELD_WEB_WIDGET_CONFIG: tuple[str, str] = (
    "webWidgetConfig",
    "web_widget_config",
)
DEFAULT_ENV_FILENAME: str = "environment.json"


def get_field(
    mapping: dict[str, typing.Any],
    keys: tuple[str, ...],
    default: typing.Any = None,
) -> typing.Any:
    """Return the first non-None value in ``mapping`` for ``keys``."""
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


# ── Severity ─────────────────────────────────────────────────────────────


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    OFF = "off"

    @classmethod
    def from_str(cls, s: typing.Any) -> "Severity":
        # YAML parses bare ``off`` as boolean False
        if isinstance(s, bool):
            return cls.OFF if not s else cls.ERROR
        return cls(str(s).lower())


# ── Toolset Resolution ────────────────────────────────────────────────────


class ToolsetValidationBehavior(Enum):
    STRICT = "strict"  # Enforce strict operation-level checks (OpenAPI)
    BYPASS = "bypass"  # Bypass operation-level checks (MCP/Connector)


@dataclass
class ToolsetResolution:
    """Structured result of resolving a toolset offline."""

    behavior: ToolsetValidationBehavior
    tools: list[str] = field(default_factory=list)


# ── Lint Result ──────────────────────────────────────────────────────────


@dataclass
class LintResult:
    file: str
    rule_id: str
    severity: Severity
    message: str
    line: int | None = None
    fix_suggestion: str = ""

    def __str__(self) -> typing.Any:
        prefix = {"error": "E", "warning": "W", "info": "I"}[
            self.severity.value
        ]
        loc = self.file
        if self.line:
            loc += f":{self.line}"
        return f"  [{prefix}] {loc} [{self.rule_id}] {self.message}"

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "line": self.line,
            "severity": self.severity.value,
            "rule_id": self.rule_id,
            "message": self.message,
            "fix_suggestion": self.fix_suggestion,
        }


# ── Lint Report ──────────────────────────────────────────────────────────


@dataclass
class LintReport:
    results: list = field(default_factory=list)

    @property
    def errors(self) -> typing.Any:
        return [r for r in self.results if r.severity == Severity.ERROR]

    @property
    def warnings(self) -> typing.Any:
        return [r for r in self.results if r.severity == Severity.WARNING]

    def add(self, result: LintResult) -> None:
        self.results.append(result)

    def add_all(self, results: list) -> None:
        self.results.extend(results)

    def print_summary(self, show_fixes: typing.Any = False) -> None:
        if not self.results:
            print("\n  All checks passed.")
            return

        for r in sorted(self.results, key=lambda x: (x.severity.value, x.file)):
            print(str(r))
            if show_fixes and r.fix_suggestion:
                print(f"         Fix: {r.fix_suggestion}")

        info_count = len(self.results) - len(self.errors) - len(self.warnings)
        print(
            f"\n  {len(self.errors)} error(s), "
            f"{len(self.warnings)} warning(s), "
            f"{info_count} info"
        )

    def to_json(self) -> str:
        return json.dumps([r.to_dict() for r in self.results], indent=2)

    def print_and_exit(
        self, json_output: bool = False, show_fixes: bool = False
    ) -> None:
        """Print results and exit with code 1 if errors, 0 otherwise."""
        import sys  # noqa: PLC0415

        if json_output:
            print(self.to_json())
        else:
            print("\n" + "=" * 60)
            print("LINT RESULTS")
            print("=" * 60)
            self.print_summary(show_fixes=show_fixes)

            if self.errors:
                print(f"\nLint FAILED with {len(self.errors)} error(s).")
            else:
                print("\nLint PASSED (no errors).")

        sys.exit(1 if self.errors else 0)


# ── Rule Base Class ──────────────────────────────────────────────────────


class Rule(ABC):
    """Base class for all lint rules.

    Each rule has:
    - id: unique identifier (e.g., "I001")
    - name: human-readable name
    - description: what the rule checks
    - default_severity: severity when not overridden by config
    - category: set by the @rule decorator
    - target: which file type this rule operates on (used by the
      ``structure`` category to dispatch rules to the right files).
      Values: ``"app_config"``, ``"instruction"``, ``"agent_config"``.
      Rules that don't set ``target`` receive the default files for
      their category.
    - models: optional list of model names/patterns this rule applies to.
      If None or empty, the rule is model-agnostic and applies to all models.
    """

    id: str = ""
    name: str = ""
    description: str = ""
    default_severity: Severity = Severity.WARNING
    category: str = ""
    target: str = ""
    models: list[str] | None = None

    def is_applicable_for_model(self, model: str | None) -> bool:
        """Check if this rule is applicable for the given model.

        If `models` is None or empty, the rule is model-agnostic and
        applies to all models.
        If `models` is specified, it returns True only if `model` is
        provided and exactly matches one of the specified models
        (case-insensitive).
        """
        if not self.models:
            return True
        if not model:
            return False
        model_lower = model.strip().lower()
        return any(
            target.strip().lower() == model_lower for target in self.models
        )

    @abstractmethod
    def check(
        self, file_path: Path, content: str, context: "LintContext"
    ) -> list[LintResult]:
        """Run this rule against a file. Returns list of LintResults."""
        ...

    def make_result(
        self,
        file: str,
        message: str,
        severity: Severity | None = None,
        line: int | None = None,
        fix: str = "",
    ) -> LintResult:
        return LintResult(
            file=file,
            rule_id=self.id,
            severity=severity or self.default_severity,
            message=message,
            line=line,
            fix_suggestion=fix,
        )


# ── Decorator-Based Auto-Registration ───────────────────────────────────

_RULE_REGISTRY: dict[str, list[Rule]] = defaultdict(list)
# Dedup key is (rule_id, category) so the same id may register under
# multiple categories (e.g. V100 across callbacks/tools/evals) while
# repeated imports of the same rule still no-op.
_REGISTERED_IDS: set[tuple[str, str]] = set()


def rule(
    category: str,
    models: list[str] | str | None = None,
) -> typing.Any:
    """Class decorator that auto-registers a Rule into its category.

    Duplicate ``(rule_id, category)`` pairs are silently ignored so
    that repeated imports (or test-time ``@rule`` usage) never produce
    duplicates. The same ``rule_id`` MAY register under different
    categories — useful for cross-surface rules.

    Usage::

        @rule("config")
        class InvalidJson(Rule):
            id = "A001"
            ...

        @rule("instructions", models=["gemini-composite-v1"])
        class CompositeSpecificRule(Rule):
            id = "C001"
            ...
    """

    def decorator(cls) -> typing.Any:  # noqa: ANN001
        cls.category = category
        if models is not None:
            cls.models = [models] if isinstance(models, str) else list(models)
        elif isinstance(getattr(cls, "models", None), str):
            cls.models = [cls.models]
        instance = cls()
        key = (instance.id, category)
        if key not in _REGISTERED_IDS:
            _REGISTERED_IDS.add(key)
            _RULE_REGISTRY[category].append(instance)
        return cls

    return decorator


def get_registered_rules() -> dict[str, list[Rule]]:
    """Return all auto-registered rules, keyed by category."""
    return dict(_RULE_REGISTRY)


def reset_registry() -> None:
    """Clear all registered rules.  Use in tests to get a clean state."""
    _RULE_REGISTRY.clear()
    _REGISTERED_IDS.clear()


# ── Lint Context ─────────────────────────────────────────────────────────


@dataclass
class LintContext:
    """Shared context passed to rules for cross-referencing."""

    project_root: Path
    app_dir: Path
    evals_dir: Path
    model: str | None = None
    agent_models: dict[str, str] = field(default_factory=dict)
    all_agent_names: set = field(default_factory=set)
    all_agent_display_names: set = field(default_factory=set)
    all_tool_names: set = field(default_factory=set)
    all_tool_dirs: dict = field(default_factory=dict)
    platform_tools: set = field(
        default_factory=lambda: {"end_session", "customize_response"}
    )
    options: dict = field(default_factory=dict)
    bypass_tool_prefixes: set = field(default_factory=set)
    app_root: Path | None = None
    env_file: Path | None = None
    agent_to_parents: dict[str, set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )

    @property
    def all_known_tools(self) -> set:
        return self.all_tool_names | self.platform_tools

    def get_model_for_agent(self, agent_name: str) -> str | None:
        """Get agent-configured model (or fallback to app-level model)."""
        return self.agent_models.get(agent_name, self.model)

    def get_model_for_file(self, file_path: Path) -> str | None:
        """Resolve the effective model for a specific file."""
        if self.app_root:
            try:
                rel = file_path.resolve().relative_to(self.app_root.resolve())
                parts = rel.parts
                if len(parts) >= 2 and parts[0] == "agents":
                    agent_name = parts[1]
                    return self.get_model_for_agent(agent_name)
            except ValueError:
                pass
        return self.model


COMPOSITE_V2_MODELS: list[str] = ["gemini-composite-v2"]
LIVE_AUDIO_MODELS: list[str] = [
    "gemini-3.1-flash-live",
    "gemini-3-flash-native-audio",
    "gemini-3.0-flash-native-audio-preview",
    "gemini-2.5-flash-native-audio-preview",
]
VOICE_MODELS: frozenset[str] = frozenset(
    {"gemini-composite-v1", *COMPOSITE_V2_MODELS, *LIVE_AUDIO_MODELS}
)
_VOICE_MODELS_LOWER: frozenset[str] = frozenset(m.lower() for m in VOICE_MODELS)

VOICE_CHANNEL_TYPES = frozenset(
    {
        # CES / SDK ChannelProfile.ChannelType telephony values
        "TWILIO",
        "GOOGLE_TELEPHONY_PLATFORM",
        "CONTACT_CENTER_AS_A_SERVICE",
        "FIVE9",
        "CONTACT_CENTER_INTEGRATION",
        "AUDIOCODES",
        # Legacy telephony aliases used in deployment manifests
        "VOICE_TELEPHONY",
        "TELEPHONY",
        "VOICE",
        "AUDIO",
        "CONTACT_CENTER",
    }
)

VOICE_MODALITIES = frozenset(
    {"VOICE_ONLY", "CHAT_AND_VOICE", "CHAT_VOICE_AND_VIDEO"}
)
_VOICE_MODALITY_INTS = frozenset({1, 2, 4})
NON_VOICE_MODALITIES = frozenset({"CHAT_ONLY"})
_NON_VOICE_MODALITY_INTS = frozenset({3})


def _classify_modality(raw: typing.Any) -> bool | None:
    """Return True for voice modality, False for CHAT_ONLY, else None."""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        if raw in _VOICE_MODALITY_INTS:
            return True
        if raw in _NON_VOICE_MODALITY_INTS:
            return False
        return None
    if isinstance(raw, str):
        norm = raw.strip().upper()
        if norm in VOICE_MODALITIES:
            return True
        if norm in NON_VOICE_MODALITIES:
            return False
    return None


def _channel_profile_modality(cp: dict[str, typing.Any]) -> bool | None:
    """Classify modality from ``channelProfile`` or its ``webWidgetConfig``."""
    direct = _classify_modality(cp.get("modality"))
    if direct is not None:
        return direct
    for ww_key in _FIELD_WEB_WIDGET_CONFIG:
        ww = cp.get(ww_key)
        if isinstance(ww, dict):
            nested = _classify_modality(ww.get("modality"))
            if nested is not None:
                return nested
    return None


def _deployment_modality(data: dict[str, typing.Any]) -> bool | None:
    """Classify explicit modality declared in a deployment manifest dict."""
    top = _classify_modality(data.get("modality"))
    if top is not None:
        return top
    for cp_key in _FIELD_CHANNEL_PROFILE:
        cp = data.get(cp_key)
        if isinstance(cp, dict):
            cp_mod = _channel_profile_modality(cp)
            if cp_mod is not None:
                return cp_mod
    return None


def _deployment_channel_candidates(
    data: dict[str, typing.Any],
) -> list[str]:
    """Extract non-empty channel strings from a deployment manifest dict."""
    raw_values: list[object] = [data.get("channel")]
    for cp_key in _FIELD_CHANNEL_PROFILE:
        cp = data.get(cp_key)
        if isinstance(cp, dict):
            raw_values.extend(cp.get(k) for k in _FIELD_CHANNEL_TYPE)
    return [v.strip() for v in raw_values if isinstance(v, str) and v.strip()]


def _check_deployment_voice_channels(context: LintContext) -> bool | None:
    """Return True/False if any deployment declares modality/channel."""
    saw_signal = False
    for dep_file in discover_deployment_files(context.app_root, context):
        data = _load_config_dict(dep_file)
        if data is None:
            continue
        modality_voice = _deployment_modality(data)
        if modality_voice is not None:
            saw_signal = True
            if modality_voice:
                return True
            continue
        for ch in _deployment_channel_candidates(data):
            saw_signal = True
            if ch.upper() in VOICE_CHANNEL_TYPES:
                return True
    return False if saw_signal else None


def _app_channel_is_voice(app_cfg: dict[str, typing.Any] | None) -> bool | None:
    """Return True/False if ``defaultChannelProfile`` declares voice/text."""
    if not isinstance(app_cfg, dict):
        return None
    cp = get_field(app_cfg, _FIELD_DEFAULT_CHANNEL_PROFILE)
    if not isinstance(cp, dict):
        return None
    modality_voice = _channel_profile_modality(cp)
    if modality_voice is not None:
        return modality_voice
    ch = get_field(cp, _FIELD_CHANNEL_TYPE)
    if isinstance(ch, str) and ch.strip():
        return ch.strip().upper() in VOICE_CHANNEL_TYPES
    return None


def safe_ast_parse(content: str) -> ast.Module | None:
    """Parse Python source into an AST, returning None on syntax/null errors."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            return ast.parse(content)
    except (SyntaxError, ValueError):
        return None


def _normalize_glob_path(path: str) -> str:
    """Normalize slashes and strip leading ``./`` segments for glob matching."""
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _app_model_is_voice(
    context: LintContext, app_cfg: dict[str, typing.Any] | None
) -> bool:
    """Return True if the app or any agent configures a known voice model."""
    candidates: list[str] = []
    if isinstance(context.model, str) and context.model.strip():
        candidates.append(context.model)
    elif isinstance(app_cfg, dict):
        ms = get_field(app_cfg, FIELD_MODEL_SETTINGS)
        if isinstance(ms, dict) and isinstance(ms.get("model"), str):
            candidates.append(ms["model"])
    candidates.extend(
        m for m in context.agent_models.values() if isinstance(m, str)
    )
    return any(m.strip().lower() in _VOICE_MODELS_LOWER for m in candidates)


def is_voice_app(context: LintContext) -> bool:
    """Return True if deployment manifests, channel profile, or model is voice.

    Precedence:
    1. ``deployments/*.{yaml,yml}`` explicit ``modality`` (``VOICE_ONLY``,
       ``CHAT_AND_VOICE``, ``CHAT_VOICE_AND_VIDEO`` -> True; ``CHAT_ONLY`` ->
       False), falling back to ``channel``/``channelType`` when unset.
    2. ``app.{json,yaml,yml}`` ``defaultChannelProfile`` ``modality`` (direct
       or ``webWidgetConfig.modality``), falling back to ``channelType``.
    3. App or agent model (``context.model``, ``modelSettings.model``, or
       ``context.agent_models``) matching a known voice model
       (``VOICE_MODELS``).
    4. ``False`` otherwise (bare ``audioProcessingConfig`` is not treated as
       a voice signal because CES defaults include it on text/web apps).
    """
    dep_voice = _check_deployment_voice_channels(context)
    if dep_voice is not None:
        return dep_voice

    app_root = context.app_root or context.app_dir
    _, app_cfg = (
        _load_app_config(app_root) if app_root is not None else (None, None)
    )
    channel_voice = _app_channel_is_voice(app_cfg)
    if channel_voice is not None:
        return channel_voice

    return _app_model_is_voice(context, app_cfg)


# ── Shared Config Loading & Agent Ref Resolution ────────────────────────

_MAX_AGENT_CYCLES: int = 100
_CES_AGENT_REF_RE = re.compile(
    r"^(?:projects/[^/]+/locations/[^/]+/apps/[^/]+/)?agents/([^/]+)$"
)


def parse_config_content(
    file_path: Path, content: str
) -> dict[str, typing.Any] | None:
    """Parse JSON or YAML config content into a dict, or None on error."""
    suffix = file_path.suffix.lower()
    try:
        data = (
            yaml.safe_load(content)
            if suffix in (".yaml", ".yml")
            else json.loads(content)
        )
    except (ValueError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def _load_config_dict(path: Path) -> dict[str, typing.Any] | None:
    """Load a JSON or YAML mapping from ``path``, returning None on error."""
    if path.is_symlink() or not path.is_file():
        return None
    if path.suffix.lower() not in CONFIG_EXTENSIONS:
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return parse_config_content(path, raw)


def _find_dir_config(
    resource_dir: Path, stem: str | None = None
) -> Path | None:
    """Return ``<stem>.json``, ``<stem>.yaml``, or ``<stem>.yml`` if present."""
    name = stem if stem is not None else resource_dir.name
    for ext in CONFIG_EXTENSIONS:
        candidate = resource_dir / f"{name}{ext}"
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return None


def _load_app_config(
    app_root: Path,
) -> tuple[Path | None, dict[str, typing.Any] | None]:
    """Return ``(path, data)`` for the first non-symlink app config file."""
    for name in APP_CONFIG_NAMES:
        candidate = app_root / name
        if candidate.is_file() and not candidate.is_symlink():
            return candidate, _load_config_dict(candidate)
    return None, None


def resolve_app_root(file_path: Path, context: LintContext) -> Path:
    """Resolve the app root directory from ``file_path`` or ``context``."""
    if file_path.name in APP_CONFIG_NAMES:
        return file_path.parent
    if context.app_root and context.app_root.exists():
        return context.app_root
    if any((context.app_dir / name).exists() for name in APP_CONFIG_NAMES):
        return context.app_dir
    resource_dirs = ("agents", "tools", "toolsets", "guardrails")
    if file_path.parent.parent.name in resource_dirs:
        return file_path.parent.parent.parent
    if file_path.parent.name in resource_dirs:
        return file_path.parent.parent
    return context.app_dir


def rel_to_project(path: Path, context: LintContext) -> str:
    """Format ``path`` relative to ``context.project_root`` when possible."""
    try:
        return str(path.relative_to(context.project_root))
    except ValueError:
        try:
            return str(
                path.resolve().relative_to(context.project_root.resolve())
            )
        except (OSError, ValueError):
            return str(path)


def _iter_subdirs(parent: Path) -> list[Path]:
    """Return sorted non-hidden, non-symlink subdirectories under ``parent``."""
    if parent.is_symlink() or not parent.is_dir():
        return []
    return [
        entry
        for entry in sorted(parent.iterdir())
        if not entry.is_symlink()
        and entry.is_dir()
        and not entry.name.startswith(".")
        and entry.name != "__pycache__"
    ]


def extract_resource_ref_candidates(ref: str, subdir: str) -> set[str]:
    """Return raw and short-ID lookup candidates for a CES resource ref."""
    raw = ref.strip()
    if not raw:
        return set()
    cands = {raw}
    m = re.match(
        rf"^(?:projects/[^/]+/locations/[^/]+/apps/[^/]+/)?{re.escape(subdir)}/([^/]+)$",
        raw,
    )
    if m:
        cands.add(m.group(1))
    return cands


@dataclass
class _AgentRefIndex:
    """Deterministic lookup maps from agent ref variants to directory names."""

    name_set: set[str]
    exact_name_map: dict[str, str] = field(default_factory=dict)
    short_name_map: dict[str, str] = field(default_factory=dict)
    exact_display_map: dict[str, str] = field(default_factory=dict)
    norm_dir_map: dict[str, str] = field(default_factory=dict)
    norm_name_map: dict[str, str] = field(default_factory=dict)
    norm_display_map: dict[str, str] = field(default_factory=dict)


def _register_agent_config_refs(
    dir_name: str, cfg: dict[str, typing.Any], index: _AgentRefIndex
) -> None:
    """Populate exact and normalized name/displayName entries for one agent."""
    cfg_name = cfg.get("name")
    if isinstance(cfg_name, str) and cfg_name.strip():
        clean_name = cfg_name.strip()
        short_id = clean_name.rsplit("/", 1)[-1]
        index.exact_name_map.setdefault(clean_name, dir_name)
        index.short_name_map.setdefault(short_id, dir_name)
        index.norm_name_map.setdefault(
            clean_name.replace(" ", "_").lower(), dir_name
        )
        index.norm_name_map.setdefault(
            short_id.replace(" ", "_").lower(), dir_name
        )
    disp = get_field(cfg, FIELD_DISPLAY_NAME)
    if isinstance(disp, str) and disp.strip():
        clean_disp = disp.strip()
        index.exact_display_map.setdefault(clean_disp, dir_name)
        index.norm_display_map.setdefault(
            clean_disp.replace(" ", "_").lower(), dir_name
        )


def _match_exact_agent_ref(
    raw: str, index: _AgentRefIndex, *, use_short_map: bool = False
) -> str | None:
    """Match an agent ref against exact directory, name, and display maps."""
    if raw in index.name_set:
        return raw
    underscored = raw.replace(" ", "_")
    if underscored in index.name_set:
        return underscored
    name_map = index.short_name_map if use_short_map else index.exact_name_map
    return name_map.get(raw) or index.exact_display_map.get(raw)


def _match_normalized_agent_ref(
    candidates: list[str], index: _AgentRefIndex
) -> str | None:
    """Fallback case/space-insensitive match across normalized maps."""
    lookups = (index.norm_dir_map, index.norm_name_map, index.norm_display_map)
    for cand in candidates:
        norm = cand.replace(" ", "_").lower()
        for lookup in lookups:
            if norm in lookup:
                return lookup[norm]
    return None


def _extract_dict_agent_refs(ref: dict[str, typing.Any]) -> list[str]:
    """Extract candidate string references from a dict childAgents entry."""
    return [
        val.strip()
        for key in ("name", *FIELD_DISPLAY_NAME)
        if isinstance((val := ref.get(key)), str) and val.strip()
    ]


def _resolve_agent_ref_str(raw: str, index: _AgentRefIndex) -> str | None:
    """Resolve a stripped non-empty agent reference string to a dir name."""
    direct = _match_exact_agent_ref(raw, index)
    if direct is not None:
        return direct

    candidates = [raw]
    m = _CES_AGENT_REF_RE.match(raw)
    if m:
        short_ref = m.group(1)
        short_match = _match_exact_agent_ref(
            short_ref, index, use_short_map=True
        )
        if short_match is not None:
            return short_match
        candidates.append(short_ref)

    if raw in index.short_name_map:
        return index.short_name_map[raw]
    return _match_normalized_agent_ref(candidates, index)


def _build_agent_ref_resolver(
    agent_names: set[str],
    agent_configs: dict[str, dict[str, typing.Any]],
) -> typing.Callable[[typing.Any], str | None]:
    """Build a deterministic resolver from agent refs to directory names."""
    sorted_names = sorted(agent_names)
    index = _AgentRefIndex(name_set=set(sorted_names))
    for dir_name in sorted_names:
        index.norm_dir_map.setdefault(
            dir_name.replace(" ", "_").lower(), dir_name
        )
        cfg = agent_configs.get(dir_name)
        if isinstance(cfg, dict):
            _register_agent_config_refs(dir_name, cfg, index)

    def _resolve(ref: typing.Any) -> str | None:
        if isinstance(ref, dict):
            for cand in _extract_dict_agent_refs(ref):
                resolved = _resolve_agent_ref_str(cand, index)
                if resolved is not None:
                    return resolved
            return None
        if not isinstance(ref, str) or not ref.strip():
            return None
        return _resolve_agent_ref_str(ref.strip(), index)

    return _resolve


def resolve_safe_app_file(app_root: Path, rel_path: str) -> Path | None:
    """Resolve an app-relative file path strictly within ``app_root``.

    Returns ``None`` if ``rel_path`` is absolute, traverses ``..``, traverses
    any symlink inside ``app_root``, escapes ``app_root``, or is not a file.
    """
    if not isinstance(rel_path, str):
        return None
    stripped = rel_path.strip()
    if not stripped or "://" in stripped or "\n" in stripped:
        return None
    p = Path(stripped)
    if p.is_absolute() or ".." in p.parts:
        return None

    cur = app_root
    for part in p.parts:
        cur = cur / part
        if cur.is_symlink():
            return None

    if not cur.is_file():
        return None

    try:
        cur.resolve().relative_to(app_root.resolve())
    except (OSError, ValueError):
        return None
    return cur


@dataclass(frozen=True)
class LocalResource:
    """A locally defined resource under ``toolsets/`` or ``guardrails/``."""

    key: str
    report_path: Path
    identifiers: frozenset[str]
    normalized_identifiers: frozenset[str]


def _add_tool_subobject_names(
    cfg: dict[str, typing.Any], ids: set[str]
) -> None:
    """Add non-empty ``name`` values from CES tool sub-object containers."""
    for container_key in _TOOL_NAME_CONTAINERS:
        sub = cfg.get(container_key)
        if not isinstance(sub, dict):
            continue
        sub_name = sub.get("name")
        if isinstance(sub_name, str) and sub_name.strip():
            clean = sub_name.strip()
            ids.add(clean)
            ids.add(clean.rsplit("/", 1)[-1])


def _extract_resource_identifiers(
    key: str, cfg: dict[str, typing.Any] | None
) -> tuple[frozenset[str], frozenset[str]]:
    """Build exact and normalized lookup identifier sets for a resource."""
    ids: set[str] = {key}
    if isinstance(cfg, dict):
        disp = cfg.get("displayName") or cfg.get("display_name")
        if isinstance(disp, str) and disp.strip():
            ids.add(disp.strip())
        cfg_name = cfg.get("name")
        if isinstance(cfg_name, str) and cfg_name.strip():
            clean_name = cfg_name.strip()
            ids.add(clean_name)
            ids.add(clean_name.rsplit("/", 1)[-1])
        _add_tool_subobject_names(cfg, ids)
    norm_ids = {i.replace(" ", "_").lower() for i in ids}
    return frozenset(ids), frozenset(norm_ids)


def _load_resource_entry(
    entry: Path,
) -> tuple[str, Path, dict[str, typing.Any] | None] | None:
    """Return ``(key, report_path, cfg)`` for a directory or flat config."""
    if entry.is_dir():
        cfg_path = _find_dir_config(entry)
        cfg = _load_config_dict(cfg_path) if cfg_path is not None else None
        return entry.name, cfg_path or entry, cfg
    if entry.is_file() and entry.suffix.lower() in CONFIG_EXTENSIONS:
        return entry.stem, entry, _load_config_dict(entry)
    return None


def discover_local_resources(
    app_root: Path, subdir: str
) -> list[LocalResource]:
    """Discover directory and flat configs under ``app_root/<subdir>``."""
    parent = app_root / subdir
    if parent.is_symlink() or not parent.is_dir():
        return []

    resources: list[LocalResource] = []
    for entry in sorted(parent.iterdir()):
        if (
            entry.is_symlink()
            or entry.name.startswith(".")
            or entry.name == "__pycache__"
        ):
            continue
        loaded = _load_resource_entry(entry)
        if loaded is None:
            continue
        key, report_path, cfg = loaded
        ids, norm_ids = _extract_resource_identifiers(key, cfg)
        resources.append(
            LocalResource(
                key=key,
                report_path=report_path,
                identifiers=ids,
                normalized_identifiers=norm_ids,
            )
        )
    return resources


def match_local_resources(
    ref: str,
    subdir: str,
    resources: list[LocalResource],
    *,
    case_sensitive: bool = False,
) -> list[LocalResource]:
    """Match a reference string against discovered ``LocalResource`` entries."""
    cands = extract_resource_ref_candidates(ref, subdir)
    if not cands:
        return []
    if case_sensitive:
        return [res for res in resources if cands & res.identifiers]
    spaced_cands = {c.replace(" ", "_") for c in cands}
    exact = [
        res
        for res in resources
        if (cands & res.identifiers) or (spaced_cands & res.identifiers)
    ]
    if exact:
        return exact
    norm_cands = {c.replace(" ", "_").lower() for c in cands}
    return [res for res in resources if norm_cands & res.normalized_identifiers]


@dataclass
class AgentGraph:
    """Resolved agent hierarchy graph for a CES app."""

    app_config: dict[str, typing.Any] | None = None
    app_config_path: Path | None = None
    root_agent_ref: str | None = None
    root_agent_dir: str | None = None
    agent_dirs: dict[str, Path] = field(default_factory=dict)
    agent_configs: dict[str, dict[str, typing.Any]] = field(
        default_factory=dict
    )
    agent_config_paths: dict[str, Path] = field(default_factory=dict)
    agent_display_names: dict[str, str] = field(default_factory=dict)
    adjacency: dict[str, list[str]] = field(default_factory=dict)
    reachable: set[str] = field(default_factory=set)
    cycles: list[list[str]] = field(default_factory=list)


def _canonicalize_cycle(
    core: list[str], root_agent_dir: str | None
) -> list[str]:
    """Rotate a cycle to start at ``root_agent_dir`` or its min node."""
    pivot = (
        root_agent_dir
        if root_agent_dir and root_agent_dir in core
        else min(core)
    )
    rot = core.index(pivot)
    return [*core[rot:], *core[:rot], pivot]


def _dfs_cycles_from(
    start: str,
    adjacency: dict[str, list[str]],
    color: dict[str, int],
    seen: set[tuple[str, ...]],
    cycles: list[list[str]],
    root_agent_dir: str | None,
    max_cycles: int,
) -> None:
    """Run iterative 3-color DFS from ``start``, appending unique cycles."""
    color[start] = 1
    path: list[str] = [start]
    pos_in_path: dict[str, int] = {start: 0}
    stack: list[tuple[str, int]] = [(start, 0)]

    while stack and len(cycles) < max_cycles:
        node, idx = stack[-1]
        nbrs = adjacency.get(node, ())
        if idx >= len(nbrs):
            stack.pop()
            path.pop()
            pos_in_path.pop(node, None)
            color[node] = 2
            continue
        stack[-1] = (node, idx + 1)
        nxt = nbrs[idx]
        if nxt not in adjacency:
            continue
        nxt_color = color.get(nxt, 0)
        if nxt_color == 0:
            color[nxt] = 1
            pos_in_path[nxt] = len(path)
            path.append(nxt)
            stack.append((nxt, 0))
        elif nxt_color == 1:
            full = _canonicalize_cycle(path[pos_in_path[nxt] :], root_agent_dir)
            key = tuple(full)
            if key not in seen:
                seen.add(key)
                cycles.append(full)


def _find_cycles(
    adjacency: dict[str, list[str]],
    root_agent_dir: str | None = None,
    max_cycles: int = _MAX_AGENT_CYCLES,
) -> list[list[str]]:
    """Detect cycles via iterative 3-color DFS (WHITE=0, GRAY=1, BLACK=2)."""
    color: dict[str, int] = {}
    seen: set[tuple[str, ...]] = set()
    cycles: list[list[str]] = []

    order = sorted(adjacency)
    if root_agent_dir in adjacency:
        order = [root_agent_dir, *(n for n in order if n != root_agent_dir)]

    for start in order:
        if color.get(start, 0) == 0 and len(cycles) < max_cycles:
            _dfs_cycles_from(
                start,
                adjacency,
                color,
                seen,
                cycles,
                root_agent_dir,
                max_cycles,
            )

    cycles.sort()
    return cycles


def _discover_agent_entries(
    app_root: Path,
) -> tuple[
    dict[str, Path],
    dict[str, dict[str, typing.Any]],
    dict[str, Path],
    dict[str, str],
]:
    """Load agent dirs, config paths, parsed configs, and displayNames."""
    agent_dirs: dict[str, Path] = {}
    agent_configs: dict[str, dict[str, typing.Any]] = {}
    agent_config_paths: dict[str, Path] = {}
    agent_display_names: dict[str, str] = {}

    for entry in _iter_subdirs(app_root / "agents"):
        agent_dirs[entry.name] = entry
        cfg_path = _find_dir_config(entry)
        if cfg_path is None:
            continue
        agent_config_paths[entry.name] = cfg_path
        cfg = _load_config_dict(cfg_path)
        if cfg is None:
            continue
        agent_configs[entry.name] = cfg
        disp = cfg.get("displayName") or cfg.get("display_name")
        if isinstance(disp, str) and disp.strip():
            agent_display_names[entry.name] = disp.strip()
    return agent_dirs, agent_configs, agent_config_paths, agent_display_names


def _build_agent_adjacency(
    agent_dirs: dict[str, Path],
    agent_configs: dict[str, dict[str, typing.Any]],
    resolve_ref: typing.Callable[[typing.Any], str | None],
) -> dict[str, list[str]]:
    """Build deduplicated child-agent adjacency lists from agent configs."""
    adjacency: dict[str, list[str]] = {name: [] for name in sorted(agent_dirs)}
    for agent_name in sorted(agent_dirs):
        cfg = agent_configs.get(agent_name)
        if not isinstance(cfg, dict):
            continue
        raw_children = get_field(cfg, FIELD_CHILD_AGENTS)
        if not isinstance(raw_children, list):
            continue
        seen_children: set[str] = set()
        for child_ref in raw_children:
            resolved = resolve_ref(child_ref)
            if resolved is not None and resolved not in seen_children:
                seen_children.add(resolved)
                adjacency[agent_name].append(resolved)
    return adjacency


def _compute_reachable_agents(
    root_agent_dir: str | None, adjacency: dict[str, list[str]]
) -> set[str]:
    """Return all agent directory names reachable from ``root_agent_dir``."""
    if root_agent_dir is None:
        return set()
    reachable: set[str] = {root_agent_dir}
    queue: deque[str] = deque([root_agent_dir])
    while queue:
        curr = queue.popleft()
        for nxt in adjacency.get(curr, ()):
            if nxt not in reachable:
                reachable.add(nxt)
                queue.append(nxt)
    return reachable


def resolve_agent_graph(app_root: Path) -> AgentGraph:
    """Resolve app config, agent directories, reachability, and cycles."""
    app_config_path, app_config = _load_app_config(app_root)
    agent_dirs, agent_configs, agent_config_paths, agent_display_names = (
        _discover_agent_entries(app_root)
    )
    resolve_ref = _build_agent_ref_resolver(
        set(agent_dirs.keys()), agent_configs
    )

    root_agent_ref: str | None = None
    root_agent_dir: str | None = None
    if isinstance(app_config, dict):
        raw_root = get_field(app_config, FIELD_ROOT_AGENT)
        if isinstance(raw_root, str) and raw_root.strip():
            root_agent_ref = raw_root.strip()
            root_agent_dir = resolve_ref(root_agent_ref)

    adjacency = _build_agent_adjacency(agent_dirs, agent_configs, resolve_ref)
    reachable = _compute_reachable_agents(root_agent_dir, adjacency)
    cycles = _find_cycles(adjacency, root_agent_dir=root_agent_dir)

    return AgentGraph(
        app_config=app_config,
        app_config_path=app_config_path,
        root_agent_ref=root_agent_ref,
        root_agent_dir=root_agent_dir,
        agent_dirs=agent_dirs,
        agent_configs=agent_configs,
        agent_config_paths=agent_config_paths,
        agent_display_names=agent_display_names,
        adjacency=adjacency,
        reachable=reachable,
        cycles=cycles,
    )


def collect_attached_guardrails(
    graph: AgentGraph,
    resources: list[LocalResource],
) -> tuple[set[LocalResource], dict[LocalResource, set[str]]]:
    """Return ``(app_referenced, agent_refs_by_resource)`` for guardrails."""

    def _matched_guardrails(raw_grs: typing.Any) -> list[LocalResource]:
        if not isinstance(raw_grs, list):
            return []
        matched: list[LocalResource] = []
        for entry in raw_grs:
            gr_ref = (
                entry
                if isinstance(entry, str)
                else (
                    entry.get("guardrail") if isinstance(entry, dict) else None
                )
            )
            if isinstance(gr_ref, str) and gr_ref.strip():
                matched.extend(
                    match_local_resources(gr_ref, "guardrails", resources)
                )
        return matched

    app_referenced: set[LocalResource] = set()
    if isinstance(graph.app_config, dict):
        app_referenced.update(
            _matched_guardrails(graph.app_config.get("guardrails"))
        )

    agent_refs: dict[LocalResource, set[str]] = defaultdict(set)
    for agent_name, cfg in sorted(graph.agent_configs.items()):
        for res in _matched_guardrails(cfg.get("guardrails")):
            agent_refs[res].add(agent_name)

    return app_referenced, agent_refs


def discover_deployment_files(
    app_root: Path | None,
    context: LintContext | None = None,
) -> list[Path]:
    """Return sorted ``deployments/*.yaml`` and ``*.yml`` skipping symlinks.

    Only scans ``app_root / "deployments"`` (falling back to
    ``context.app_root`` or ``context.app_dir`` only when ``app_root`` is
    ``None``) so sibling apps in a multi-app repo cannot leak deployments.
    """
    resolved_root = app_root
    if resolved_root is None and context is not None:
        resolved_root = context.app_root or context.app_dir
    if resolved_root is None:
        return []
    deps_dir = resolved_root / "deployments"
    if deps_dir.is_symlink() or not deps_dir.is_dir():
        return []
    return sorted(
        {
            f
            for ext in ("*.yaml", "*.yml")
            for f in deps_dir.glob(ext)
            if f.is_file() and not f.is_symlink() and not f.name.startswith(".")
        }
    )


def _looks_like_schema_file_ref(val: typing.Any) -> bool:
    return (
        isinstance(val, str)
        and bool(val.strip())
        and "://" not in val
        and "\n" not in val
        and not any(c.isspace() for c in val)
        and val.strip().lower().endswith(CONFIG_EXTENSIONS)
    )


def _resource_openapi_schema_paths(
    app_root: Path,
    subdir: str,
    default_folder: str,
    block_keys: tuple[str, ...],
    res: LocalResource,
) -> list[Path]:
    """Collect safe OpenAPI schema paths referenced by a single resource."""
    candidates: list[Path] = []
    for ext in (".yaml", ".yml"):
        safe = resolve_safe_app_file(
            app_root,
            f"{subdir}/{res.key}/{default_folder}/open_api_schema{ext}",
        )
        if safe is not None:
            candidates.append(safe)

    cfg = (
        _load_config_dict(res.report_path)
        if res.report_path.is_file()
        else None
    )
    if not isinstance(cfg, dict):
        return candidates
    for bk in block_keys:
        block = cfg.get(bk)
        if not isinstance(block, dict):
            continue
        raw_schema = get_field(block, FIELD_OPEN_API_SCHEMA)
        if _looks_like_schema_file_ref(raw_schema):
            safe = resolve_safe_app_file(app_root, raw_schema)
            if safe is not None:
                candidates.append(safe)
    return candidates


def discover_openapi_schema_paths(
    app_root: Path,
) -> list[tuple[str, str, Path]]:
    """Discover OpenAPI schemas as ``(kind, resource_key, schema_path)``."""
    results: list[tuple[str, str, Path]] = []
    seen_entries: set[tuple[str, str, Path]] = set()
    specs = (
        ("tools", "open_api_tool", FIELD_OPEN_API_TOOL),
        ("toolsets", "open_api_toolset", FIELD_OPEN_API_TOOLSET),
    )
    for subdir, default_folder, block_keys in specs:
        for res in discover_local_resources(app_root, subdir):
            for sp in _resource_openapi_schema_paths(
                app_root, subdir, default_folder, block_keys, res
            ):
                entry = (subdir, res.key, sp)
                if entry not in seen_entries:
                    seen_entries.add(entry)
                    results.append(entry)
    return results


# ── Rule Registry ────────────────────────────────────────────────────────


class RuleRegistry:
    """Holds all registered rules and applies config overrides.

    Internally keyed by ``(rule_id, category)`` so the same id may
    appear under multiple categories. ``get(rule_id)`` returns the
    first match by id for back-compat with callers that expect
    unique ids (V001-V007 single-resource validation).
    """

    def __init__(self) -> None:
        self._rules: dict[tuple[str, str], Rule] = {}

    def register(self, rule_obj: Rule) -> None:
        self._rules[(rule_obj.id, rule_obj.category)] = rule_obj

    def register_all(self, rules: list[Rule]) -> None:
        for r in rules:
            self.register(r)

    def get(self, rule_id: str) -> Rule | None:
        for (rid, _cat), r in self._rules.items():
            if rid == rule_id:
                return r
        return None

    def all_rules(self) -> list[Rule]:
        return sorted(self._rules.values(), key=lambda r: (r.id, r.category))

    def rules_for_category(self, category: str) -> list[Rule]:
        return [r for r in self.all_rules() if r.category == category]

    def rules_for_model(
        self, model: str, model_only: bool = False
    ) -> list[Rule]:
        """Return rules applicable to a specific model.

        If `model_only` is True, returns only rules that are specific to
        that model (i.e. rules with non-empty `models` that match).
        If `model_only` is False, returns all rules applicable to that model
        (including model-agnostic rules).
        """
        if model_only:
            return [
                r
                for r in self.all_rules()
                if r.models and r.is_applicable_for_model(model)
            ]
        return [r for r in self.all_rules() if r.is_applicable_for_model(model)]

    def list_rules(
        self, model: str | None = None, model_only: bool = False
    ) -> None:
        """Print all registered rules, optionally filtered by model."""
        current_cat = ""
        if model:
            rules = self.rules_for_model(model, model_only=model_only)
        elif model_only:
            rules = [r for r in self.all_rules() if r.models]
        else:
            rules = self.all_rules()

        for r in rules:
            if r.category != current_cat:
                current_cat = r.category
                print(f"\n  {current_cat.upper()}")
            sev = r.default_severity.value.upper()
            model_info = f" [models: {', '.join(r.models)}]" if r.models else ""
            print(
                f"    {r.id}  [{sev:7s}]{model_info}  {r.name}: {r.description}"
            )


# ── Configuration ────────────────────────────────────────────────────────


@dataclass
class LintConfig:
    """Linter configuration loaded from ``cxaslint.yaml``."""

    app_dir: str = "."
    evals_dir: str = "evals/"
    model: str | None = None
    rules: dict[str, Severity] = field(default_factory=dict)
    options: dict[str, dict] = field(default_factory=dict)
    ignore: list[str] = field(default_factory=list)
    per_file: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def load(cls, project_root: Path) -> "LintConfig":
        config = cls()

        lint_config_path = project_root / "cxaslint.yaml"
        if lint_config_path.exists():
            with open(lint_config_path) as f:
                data = yaml.safe_load(f) or {}

            config.app_dir = data.get("app_dir", config.app_dir)
            config.evals_dir = data.get("evals_dir", config.evals_dir)
            config.model = data.get("model", config.model)

            for rule_id, severity_str in (data.get("rules") or {}).items():
                config.rules[rule_id] = Severity.from_str(severity_str)

            config.options = data.get("options") or {}
            config.ignore = data.get("ignore") or []
            config.per_file = data.get("per_file") or {}

        # Fall back to gecx-config.json for app_dir if not set by cxaslint.yaml
        if config.app_dir == ".":
            gecx_config_path = project_root / "gecx-config.json"
            if gecx_config_path.exists():
                with open(gecx_config_path) as f:
                    gecx = json.load(f)
                config.app_dir = gecx.get("app_dir", config.app_dir)

        return config

    @staticmethod
    def _lookup_rule_override(
        overrides: dict[str, typing.Any], rule_obj: Rule
    ) -> Severity | None:
        if rule_obj.id in overrides:
            return Severity.from_str(overrides[rule_obj.id])
        if rule_obj.name and rule_obj.name in overrides:
            return Severity.from_str(overrides[rule_obj.name])
        return None

    def get_per_file_severity_override(
        self, rule_obj: Rule, file_path: str = ""
    ) -> Severity | None:
        """Return a matching ``per_file`` severity override, or None."""
        norm_path = _normalize_glob_path(file_path)
        for pattern, overrides in self.per_file.items():
            if not isinstance(overrides, dict):
                continue
            if fnmatch.fnmatch(norm_path, _normalize_glob_path(pattern)):
                sev = self._lookup_rule_override(overrides, rule_obj)
                if sev is not None:
                    return sev
        return None

    def has_enabled_per_file_override(self, rule_obj: Rule) -> bool:
        """Return True if any ``per_file`` entry enables ``rule_obj``."""
        for overrides in self.per_file.values():
            if not isinstance(overrides, dict):
                continue
            sev = self._lookup_rule_override(overrides, rule_obj)
            if sev is not None and sev != Severity.OFF:
                return True
        return False

    def get_global_severity_override(self, rule_obj: Rule) -> Severity | None:
        """Return a rule-level severity override from ``rules:``, or None."""
        if rule_obj.id in self.rules:
            return self.rules[rule_obj.id]
        if rule_obj.name and rule_obj.name in self.rules:
            return self.rules[rule_obj.name]
        return None

    def get_severity_override(
        self, rule_obj: Rule, file_path: str = ""
    ) -> Severity | None:
        """Return an explicit configured severity override, or None."""
        per_file = self.get_per_file_severity_override(rule_obj, file_path)
        if per_file is not None:
            return per_file
        return self.get_global_severity_override(rule_obj)

    def get_severity(self, rule_obj: Rule, file_path: str = "") -> Severity:
        """Get the effective severity for a rule.

        Considers per-file overrides.
        """
        override = self.get_severity_override(rule_obj, file_path)
        if override is not None:
            return override

        return rule_obj.default_severity

    def is_ignored(self, file_path: str) -> bool:
        """Check if a file matches any ignore pattern."""
        norm_path = _normalize_glob_path(file_path)
        return any(
            fnmatch.fnmatch(norm_path, _normalize_glob_path(p))
            for p in self.ignore
        )

    def get_options(self, rule_id: str) -> dict:
        """Get rule-specific options."""
        return self.options.get(rule_id, {})


# ── Discovery ────────────────────────────────────────────────────────────


class Discovery:
    """Discovers agents, tools, callbacks, evals, and configs.

    Scans an app directory for all resources.
    """

    def __init__(
        self,
        app_dir: Path,
        evals_dir: Path,
        limit_agents: set[str] | None = None,
        limit_tools: set[str] | None = None,
    ) -> None:
        self.app_dir = app_dir
        self.evals_dir = evals_dir
        self.limit_agents = limit_agents
        self.limit_tools = limit_tools
        self.app_root = self._find_app_root()

    def _find_app_root(self) -> Path | None:
        """Find the actual app root directory.

        Handles two layouts:
        - Direct: ``app_dir`` itself contains ``app.{json,yaml,yml}``
        - Nested: ``app_dir/<name>/`` contains the app (gecx-skills convention)
        """
        if not self.app_dir.exists():
            return None
        if any((self.app_dir / name).exists() for name in APP_CONFIG_NAMES):
            return self.app_dir
        for d in sorted(self.app_dir.iterdir()):
            if (
                not d.is_symlink()
                and d.is_dir()
                and not d.name.startswith(".")
                and any((d / name).exists() for name in APP_CONFIG_NAMES)
            ):
                return d
        return None

    def discover_global_instruction(self) -> Path | None:
        """Return path to ``global_instruction.txt`` if it exists."""
        if not self.app_root:
            return None
        p = self.app_root / "global_instruction.txt"
        return p if p.exists() else None

    def _iter_agent_dirs(self) -> list[Path]:
        if not self.app_root:
            return []
        return [
            d
            for d in _iter_subdirs(self.app_root / "agents")
            if self.limit_agents is None or d.name in self.limit_agents
        ]

    def discover_agents(self) -> dict[str, Path]:
        """Return ``{dir_name: instruction_or_config_path}`` for all agents."""
        result = {}
        for d in self._iter_agent_dirs():
            inst = d / "instruction.txt"
            target = inst if inst.exists() else _find_dir_config(d)
            if target is not None:
                result[d.name] = target
        return result

    def discover_tools(self) -> dict[str, Path]:
        """Return ``{tool_name: code_path}`` for all tools."""
        if not self.app_root:
            return {}
        result = {}
        for d in _iter_subdirs(self.app_root / "tools"):
            if self.limit_tools is not None and d.name not in self.limit_tools:
                continue
            py_fn_dir = d / "python_function"
            code = py_fn_dir / "python_code.py"
            if (
                not py_fn_dir.is_symlink()
                and code.is_file()
                and not code.is_symlink()
            ):
                result[d.name] = code
                continue
            dir_cfg = _find_dir_config(d)
            if dir_cfg is not None:
                result[d.name] = dir_cfg
                continue
            for ext in CONFIG_EXTENSIONS:
                cfg_files = [
                    p
                    for p in sorted(d.glob(f"*{ext}"))
                    if p.is_file() and not p.is_symlink()
                ]
                if cfg_files:
                    result[d.name] = cfg_files[0]
                    break
        return result

    def discover_callbacks(self) -> list[tuple[str, str, str, Path]]:
        """Return ``[(agent_name, cb_type, cb_name, code_path), ...]``."""
        result: list[tuple[str, str, str, Path]] = []
        seen_paths: set[Path] = set()
        cb_types = (
            "before_model_callbacks",
            "after_model_callbacks",
            "before_agent_callbacks",
            "after_agent_callbacks",
            "before_tool_callbacks",
            "after_tool_callbacks",
        )

        def _add_cb(agent_name: str, cb_type: str, cb_dir: Path) -> None:
            code = cb_dir / "python_code.py"
            if (
                code.is_file()
                and not code.is_symlink()
                and code not in seen_paths
            ):
                seen_paths.add(code)
                result.append((agent_name, cb_type, cb_dir.name, code))

        for agent_dir in self._iter_agent_dirs():
            for cb_type in cb_types:
                for cb in _iter_subdirs(agent_dir / cb_type):
                    _add_cb(agent_dir.name, cb_type, cb)
            for d in _iter_subdirs(agent_dir):
                for cb_type in cb_types:
                    if d.name.startswith(f"{cb_type}_"):
                        _add_cb(agent_dir.name, cb_type, d)
                        break
        return result

    def discover_evals(self) -> dict[str, Path]:
        """Return ``{filename: path}`` for all eval YAMLs."""
        result = {}
        if not self.evals_dir.exists():
            return result
        for yaml_path in sorted(self.evals_dir.rglob("*.yaml")):
            rel = str(yaml_path.relative_to(self.evals_dir))
            result[rel] = yaml_path
        return result

    def discover_app_config(self) -> Path | None:
        """Return path to ``app.json``, ``app.yaml``, or ``app.yml``."""
        if not self.app_root:
            return None
        for name in APP_CONFIG_NAMES:
            p = self.app_root / name
            if p.is_file() and not p.is_symlink():
                return p
        return None

    def discover_app_model(self) -> str | None:
        """Return model specified in app config under ``modelSettings``."""
        app_cfg = self.discover_app_config()
        if not app_cfg:
            return None
        data = _load_config_dict(app_cfg)
        if not data:
            return None
        model_settings = get_field(data, FIELD_MODEL_SETTINGS)
        if isinstance(model_settings, dict):
            return model_settings.get("model")
        return None

    def discover_agent_configs(self) -> dict[str, Path]:
        """Return ``{agent_name: config_path}`` for all agent configs."""
        result = {}
        for d in self._iter_agent_dirs():
            cfg_file = _find_dir_config(d)
            if cfg_file is not None:
                result[d.name] = cfg_file
        return result

    def discover_agent_models(self) -> dict[str, str]:
        """Return ``{agent_name: model}`` for agents with ``modelSettings``."""
        configs = self.discover_agent_configs()
        result = {}
        for name, path in configs.items():
            data = _load_config_dict(path)
            if not data:
                continue
            model_settings = get_field(data, FIELD_MODEL_SETTINGS)
            if isinstance(model_settings, dict) and model_settings.get("model"):
                result[name] = model_settings["model"]
        return result

    def _discover_resource_dirs(self, subdir: str) -> dict[str, Path]:
        """Return ``{name: dir_path}`` for subdirs.

        Scans ``app_root/<subdir>/``.
        """
        if not self.app_root:
            return {}
        parent = self.app_root / subdir
        if not parent.exists():
            return {}
        return {d.name: d for d in sorted(parent.iterdir()) if d.is_dir()}

    def discover_toolsets(self) -> dict[str, Path]:
        return self._discover_resource_dirs("toolsets")

    def discover_guardrails(self) -> dict[str, Path]:
        return self._discover_resource_dirs("guardrails")

    def discover_evaluations(self) -> dict[str, Path]:
        return self._discover_resource_dirs("evaluations")

    def discover_evaluation_expectations(self) -> dict[str, Path]:
        return self._discover_resource_dirs("evaluation_expectations")

    def dir_name_to_display(self, dir_name: str) -> str:
        """Convert directory name to display name."""
        return dir_name.replace("_", " ")


# Maps CLI per-resource flags to their schema rule IDs.
SINGLE_RESOURCE_RULES = {
    "agent": "V002",
    "tool": "V003",
    "toolset": "V004",
    "guardrail": "V005",
    "evaluation": "V006",
    "evaluation_expectations": "V007",
}


# ── Runner ───────────────────────────────────────────────────────────────


def build_registry() -> RuleRegistry:
    """Build the rule registry by importing all rule modules.

    Importing ``cxas_scrapi.utils.lint_rules`` triggers the ``@rule``
    decorator on every rule class, populating ``_RULE_REGISTRY``.  We
    then copy those into a ``RuleRegistry`` for config-aware lookups.
    """
    import cxas_scrapi.utils.lint_rules  # noqa: F401,PLC0415

    registry = RuleRegistry()
    for _category, rules in get_registered_rules().items():
        registry.register_all(rules)
    return registry


def get_toolset_tools(
    app_root: Path,
    toolset_name: str,
    allowed_tool_ids: list[str] | None = None,
) -> ToolsetResolution:
    """Parses toolset config and resolves its validation behavior and tools.

    Returns a ToolsetResolution object.
    """
    # If allowed_tool_ids is explicitly empty, it means NO tools are assigned.
    # We enforce STRICT validation with an empty tools list (no tools allowed).
    if allowed_tool_ids is not None and not allowed_tool_ids:
        return ToolsetResolution(behavior=ToolsetValidationBehavior.STRICT)

    toolset_dir = app_root / "toolsets" / toolset_name
    json_file = toolset_dir / f"{toolset_name}.json"
    if not json_file.exists():
        return ToolsetResolution(behavior=ToolsetValidationBehavior.STRICT)

    try:
        config = json.loads(json_file.read_text())
    except Exception:
        return ToolsetResolution(behavior=ToolsetValidationBehavior.STRICT)

    # Handle OpenAPI toolset strictly
    if "openApiToolset" in config or "open_api_toolset" in config:
        schema_file = toolset_dir / "open_api_toolset" / "open_api_schema.yaml"
        if not schema_file.exists():
            return ToolsetResolution(behavior=ToolsetValidationBehavior.STRICT)

        http_methods = {
            "get",
            "post",
            "put",
            "delete",
            "patch",
            "options",
            "head",
            "trace",
        }
        tools = []
        try:
            schema = yaml.safe_load(schema_file.read_text()) or {}
            for path_str, methods in (schema.get("paths") or {}).items():
                if not isinstance(methods, dict):
                    continue
                for method_name, details in methods.items():
                    if not isinstance(details, dict):
                        continue
                    op_id = details.get("operationId")
                    if not op_id and str(method_name).lower() in http_methods:
                        sanitized = re.sub(
                            r"[^A-Za-z0-9_]",
                            "_",
                            str(path_str).strip("/"),
                        )
                        op_id = f"{str(method_name).upper()}_{sanitized}"
                    if op_id:
                        if allowed_tool_ids and op_id not in allowed_tool_ids:
                            continue
                        tools.append(f"{toolset_name}_{op_id}")
        except Exception:
            pass
        return ToolsetResolution(
            behavior=ToolsetValidationBehavior.STRICT, tools=tools
        )

    # Default for all other toolsets (MCP, Connector, etc.)
    # where tools cannot be resolved offline.
    # We return BYPASS to signal that this prefix should bypass
    # strict operation checks.
    return ToolsetResolution(behavior=ToolsetValidationBehavior.BYPASS)


def _build_agent_parent_and_display_maps(
    known_agent_names: set[str],
    agent_configs: dict[str, Path],
    discovery: Discovery,
) -> tuple[dict[str, set[str]], set[str]]:
    """Load agent configs and return ``(agent_to_parents, display_names)``."""
    loaded_configs: dict[str, dict[str, typing.Any]] = {}
    for agent_name, config_path in agent_configs.items():
        loaded = _load_config_dict(config_path)
        if loaded is not None:
            loaded_configs[agent_name] = loaded

    resolve_agent_ref = _build_agent_ref_resolver(
        known_agent_names, loaded_configs
    )
    agent_to_parents: dict[str, set[str]] = defaultdict(set)
    for parent_name, agent_config in loaded_configs.items():
        child_agents = get_field(agent_config, FIELD_CHILD_AGENTS)
        if not isinstance(child_agents, list):
            continue
        for child_ref in child_agents:
            child_name = resolve_agent_ref(child_ref)
            if child_name:
                agent_to_parents[child_name].add(parent_name)

    display_names = {
        discovery.dir_name_to_display(name) for name in known_agent_names
    }
    for agent_config in loaded_configs.values():
        disp = get_field(agent_config, FIELD_DISPLAY_NAME)
        if isinstance(disp, str) and disp.strip():
            display_names.add(disp.strip())

    return agent_to_parents, display_names


def build_context(
    project_root: Path,
    config: LintConfig,
    discovery: Discovery,
    model_override: str | None = None,
    env_file: Path | None = None,
) -> LintContext:
    """Build the shared lint context from discovered app resources."""
    agents = discovery.discover_agents()
    tools = discovery.discover_tools()
    toolsets = discovery.discover_toolsets()

    all_tool_names = set(tools.keys())
    bypass_tool_prefixes = set()
    app_root = discovery.app_root

    if app_root:
        for ts_name in toolsets:
            res = get_toolset_tools(app_root, ts_name)
            if res.behavior == ToolsetValidationBehavior.BYPASS:
                bypass_tool_prefixes.add(f"{ts_name}_")
            elif res.tools:
                all_tool_names.update(res.tools)

    agent_configs = discovery.discover_agent_configs()
    known_agent_names = set(agents.keys()) | set(agent_configs.keys())
    agent_to_parents, all_agent_display_names = (
        _build_agent_parent_and_display_maps(
            known_agent_names, agent_configs, discovery
        )
    )

    resolved_model = (
        model_override or config.model or discovery.discover_app_model()
    )
    agent_models = discovery.discover_agent_models()

    return LintContext(
        project_root=project_root,
        app_dir=discovery.app_dir,
        evals_dir=project_root / config.evals_dir,
        model=resolved_model,
        agent_models=agent_models,
        all_agent_names=known_agent_names,
        all_agent_display_names=all_agent_display_names,
        all_tool_names=all_tool_names,
        all_tool_dirs={name: path.parent for name, path in tools.items()},
        options=config.options,
        bypass_tool_prefixes=bypass_tool_prefixes,
        app_root=app_root,
        env_file=env_file,
        agent_to_parents=agent_to_parents,
    )


def _normalize_result_file(
    result_file: str, rel: str, context: LintContext
) -> str:
    """Return a project-relative normalized path for glob/override matching."""
    if not result_file:
        return _normalize_glob_path(rel)
    p = Path(result_file)
    if p.is_absolute():
        return _normalize_glob_path(rel_to_project(p, context))
    return _normalize_glob_path(result_file)


def _resolve_emitted_result(
    config: LintConfig,
    rule_obj: Rule,
    result: LintResult,
    rel: str,
    context: LintContext,
    *,
    require_per_file_match: bool = False,
) -> LintResult | None:
    """Apply ignore patterns and per-file severity overrides to a finding."""
    target_file = _normalize_result_file(result.file, rel, context)
    if config.is_ignored(target_file):
        return None
    per_file = config.get_per_file_severity_override(rule_obj, target_file)
    if require_per_file_match:
        if per_file is None or per_file == Severity.OFF:
            return None
        result.severity = per_file
        return result
    effective_override = (
        per_file
        if per_file is not None
        else config.get_global_severity_override(rule_obj)
    )
    if effective_override == Severity.OFF:
        return None
    if effective_override is not None:
        result.severity = effective_override
    return result


def _lint_files(
    rules: list[Rule],
    files: dict[str, Path],
    config: LintConfig,
    context: LintContext,
    report: LintReport,
) -> None:
    """Apply ``rules`` to a set of discovered files or directories."""
    for _name, file_path in files.items():
        rel = str(file_path.relative_to(context.project_root))
        if config.is_ignored(rel):
            continue
        file_model = context.get_model_for_file(file_path)
        if file_path.is_file():
            try:
                content = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        else:
            content = ""
        for rule_obj in rules:
            if not rule_obj.is_applicable_for_model(file_model):
                continue
            override = config.get_severity_override(rule_obj, rel)
            effective_sev = (
                override if override is not None else rule_obj.default_severity
            )
            ran_only_for_per_file = effective_sev == Severity.OFF
            if (
                ran_only_for_per_file
                and not config.has_enabled_per_file_override(rule_obj)
            ):
                continue
            for result in rule_obj.check(file_path, content, context):
                resolved = _resolve_emitted_result(
                    config,
                    rule_obj,
                    result,
                    rel,
                    context,
                    require_per_file_match=ran_only_for_per_file,
                )
                if resolved is not None:
                    report.add(resolved)


def _build_target_files(
    discovery: Discovery,
    instruction_files: dict[str, Path],
) -> dict[str, dict[str, Path]]:
    """Collect target-keyed file maps for structure and schema rules."""
    app_cfg = discovery.discover_app_config()
    return {
        "app_config": {"app": app_cfg} if app_cfg else {},
        "instruction": instruction_files,
        "agent_config": discovery.discover_agent_configs(),
        "tool_config": discovery._discover_resource_dirs("tools"),
        "toolset_config": discovery.discover_toolsets(),
        "guardrail_config": discovery.discover_guardrails(),
        "evaluation_config": discovery.discover_evaluations(),
        "eval_expectation_config": (
            discovery.discover_evaluation_expectations()
        ),
    }


def run_rules(
    registry: RuleRegistry,
    config: LintConfig,
    context: LintContext,
    discovery: Discovery,
    report: LintReport,
    categories: list[str] | None = None,
    specific_rules: set[str] | None = None,
    model_only: bool = False,
) -> None:
    """Run lint rules against discovered files."""

    def _get_rules(category: str) -> list[Rule]:
        if categories and category not in categories:
            return []
        return [
            r
            for r in registry.rules_for_category(category)
            if (not specific_rules or r.id in specific_rules)
            and (not model_only or bool(r.models))
        ]

    def _split_targeted(category: str) -> tuple[list[Rule], list[Rule]]:
        cat_rules = _get_rules(category)
        return (
            [r for r in cat_rules if not r.target],
            [r for r in cat_rules if r.target],
        )

    target_dispatched: list[Rule] = []

    # Instructions — instruction.txt files + global_instruction.txt
    instruction_files = {
        k: v
        for k, v in discovery.discover_agents().items()
        if v.name == "instruction.txt"
    }
    global_inst = discovery.discover_global_instruction()
    if global_inst:
        instruction_files["global_instruction"] = global_inst
    inst_rules, inst_targeted = _split_targeted("instructions")
    target_dispatched.extend(inst_targeted)
    _lint_files(inst_rules, instruction_files, config, context, report)

    # Callbacks
    cb_files = {
        f"{agent}_{cb_type}_{cb_name}": code_path
        for agent, cb_type, cb_name, code_path in discovery.discover_callbacks()
    }
    cb_rules, cb_targeted = _split_targeted("callbacks")
    target_dispatched.extend(cb_targeted)
    _lint_files(cb_rules, cb_files, config, context, report)

    # Tools
    tool_rules, tool_targeted = _split_targeted("tools")
    target_dispatched.extend(tool_targeted)
    _lint_files(tool_rules, discovery.discover_tools(), config, context, report)

    # Evals
    eval_rules, eval_targeted = _split_targeted("evals")
    target_dispatched.extend(eval_targeted)
    _lint_files(eval_rules, discovery.discover_evals(), config, context, report)

    # Config — app config + agent configs
    config_rules, config_targeted = _split_targeted("config")
    target_dispatched.extend(config_targeted)
    if config_rules:
        app_cfg = discovery.discover_app_config()
        config_files = {"app": app_cfg} if app_cfg else {}
        config_files.update(discovery.discover_agent_configs())
        _lint_files(config_rules, config_files, config, context, report)

    # Target-dispatched rules (structure, schema, and targeted rules from
    # any category, e.g. app_config-targeted tool/callback/config rules).
    target_dispatched.extend(_get_rules("structure"))
    target_dispatched.extend(_get_rules("schema"))
    if target_dispatched:
        target_files = _build_target_files(discovery, instruction_files)
        by_target: dict[str, list[Rule]] = {}
        for r in target_dispatched:
            by_target.setdefault(r.target, []).append(r)

        for target_name, rules in by_target.items():
            files = target_files.get(target_name, {})
            if files:
                _lint_files(rules, files, config, context, report)
