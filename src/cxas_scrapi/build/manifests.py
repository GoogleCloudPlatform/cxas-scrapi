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

"""Pydantic v2 models and loaders for ``cxas build`` manifests.

Layout under ``<manifests_root>/``::

    profiles/<profile_id>/identity.yaml        -> ProfileIdentity
    profiles/<profile_id>/channel_policy.yaml  -> ChannelPolicy
    profiles/<profile_id>/overrides.yaml       -> BrandOverrides (optional)
    envs/<environment>.yaml                    -> EnvManifest
    locales/content_blocks.yaml                -> ContentBlockCatalog (optional)

Profile *identity* (who the profile represents — e.g. a brand, subsidiary,
region, or business unit) and channel *policy* (what a given profile/channel
may do) are deliberately separate files so that business owners edit the former
and platform owners the latter.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, TypeAlias, TypeVar

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from cxas_scrapi.utils.yaml_utils import UniqueKeyYaml12SafeLoader

PROFILE_ID_PATTERN = r"^[a-z0-9_-]{2,64}$"
_PROFILE_ID_RE = re.compile(PROFILE_ID_PATTERN)
_T = TypeVar("_T")
_ModelT = TypeVar("_ModelT", bound=BaseModel)
ManifestValue: TypeAlias = Any

APP_CONFIG_FILENAMES: tuple[str, ...] = ("app.json", "app.yaml", "app.yml")
FIELD_ENV_APP: str = "app"
FORBIDDEN_APP_KEY_PAIRS: tuple[tuple[str, ...], ...] = (
    ("name",),
    ("locked",),
    ("scmSettings", "scm_settings"),
    ("createTime", "create_time"),
    ("updateTime", "update_time"),
    ("etag",),
    ("deploymentCount", "deployment_count"),
    ("predefinedVariableDeclarations", "predefined_variable_declarations"),
    ("validationErrors", "validation_errors"),
)
FORBIDDEN_APP_KEYS: frozenset[str] = frozenset(
    k for pair in FORBIDDEN_APP_KEY_PAIRS for k in pair
)

# Channel name (manifest key) -> CES deployment ChannelProfile type.
CHANNEL_PROFILE_TYPES: dict[str, str] = {
    "VOICE_TELEPHONY": "GOOGLE_TELEPHONY_PLATFORM",
    "CONTACT_CENTER": "CONTACT_CENTER_AS_A_SERVICE",
    "DIGITAL_APP": "WEB_UI",
    "WEB": "WEB_UI",
    "API": "API",
    "WHATSAPP": "WHATSAPP",
    "INSTAGRAM": "INSTAGRAM",
}

CompositionMode = Literal["canonical_overlay", "brand_native"]
COMPOSITION_MODES: frozenset[str] = frozenset(
    {"canonical_overlay", "brand_native"}
)


class ManifestError(ValueError):
    """Raised when a manifest file is missing or fails validation."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TtsVoice(_StrictModel):
    """One TTS voice binding for a language."""

    voice: str = Field(min_length=1)
    speaking_rate: float = Field(default=1.0, gt=0.0, le=4.0)


class ProfileIdentity(_StrictModel):
    """Business-owned description of a build profile.

    A profile can represent a brand, subsidiary, region, business unit, or
    specialized persona. Rendered by ``cxas build`` into the persona/lexicon
    block of ``global_instruction.txt``, into ``app.json`` / ``app.yaml`` /
    ``app.yml`` ``synthesizeSpeechConfigs`` and ``languageSettings``, and into
    ``@@profile.*@@`` placeholders across app files.
    """

    profile_id: str = Field(pattern=PROFILE_ID_PATTERN)
    display_name: str = Field(min_length=1)
    assistant_title: str | None = None
    legal_entity: str | None = None
    tone_guidelines: str | None = None
    lexicon: dict[str, str] = Field(default_factory=dict)
    urls: dict[str, str] = Field(default_factory=dict)
    tts_voices: dict[str, TtsVoice] = Field(default_factory=dict)
    languages: list[str] = Field(default_factory=list)
    forbidden_tokens: list[str] = Field(default_factory=list)
    composition_mode: CompositionMode = "canonical_overlay"

    @model_validator(mode="after")
    def _default_languages_from_tts(self) -> ProfileIdentity:
        if not self.languages and self.tts_voices:
            object.__setattr__(self, "languages", list(self.tts_voices))
        return self


class ChannelCapabilities(_StrictModel):
    """What a single channel of a profile may do."""

    entry_agent: str | None = None
    disabled_agents: list[str] = Field(default_factory=list)
    disabled_tools: list[str] = Field(default_factory=list)
    supported_modalities: list[str] = Field(default_factory=lambda: ["TEXT"])
    deeplink_widgets: bool = False

    @field_validator("supported_modalities")
    @classmethod
    def _non_empty_modalities(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("supported_modalities must not be empty")
        return value


class ChannelPolicy(_StrictModel):
    """Platform-owned policy: channels, routing and countries for a profile."""

    profile_id: str = Field(pattern=PROFILE_ID_PATTERN)
    channels: dict[str, ChannelCapabilities]
    api_routing: dict[str, str] = Field(default_factory=dict)
    supported_countries: list[str] = Field(default_factory=list)

    @field_validator("channels")
    @classmethod
    def _known_channels(
        cls, value: dict[str, ChannelCapabilities]
    ) -> dict[str, ChannelCapabilities]:
        if not value:
            raise ValueError("channels must declare at least one channel")
        unknown = sorted(set(value) - set(CHANNEL_PROFILE_TYPES))
        if unknown:
            valid = sorted(CHANNEL_PROFILE_TYPES)
            raise ValueError(
                f"unknown channel(s) {unknown}; expected one of {valid}"
            )
        return value

    @field_validator("supported_countries")
    @classmethod
    def _iso_countries(cls, value: list[str]) -> list[str]:
        bad = [c for c in value if not re.fullmatch(r"[A-Z]{2}", c)]
        if bad:
            msg = (
                f"supported_countries must be ISO-3166 alpha-2 codes, got {bad}"
            )
            raise ValueError(msg)
        return value


def _validate_string_keys(value: ManifestValue, path: str) -> None:
    if isinstance(value, list):
        for idx, item in enumerate(value):
            _validate_string_keys(item, f"{path}[{idx}]")
        return
    if not isinstance(value, Mapping):
        return
    for key, nested in value.items():
        if not isinstance(key, str):
            raise ValueError(
                f"Non-string key '{path}.{key}' ({type(key).__name__}) "
                f"is not permitted in '{FIELD_ENV_APP}:' overrides; "
                "all keys must be strings."
            )
        _validate_string_keys(nested, f"{path}.{key}")


def validate_app_overlay_mapping(
    mapping: ManifestValue, *, prefix: str = FIELD_ENV_APP
) -> None:
    """Validate ``app:`` overlay key types and reject forbidden App fields."""
    if not isinstance(mapping, Mapping):
        raise ValueError(
            f"'{prefix}' must be a mapping, got {type(mapping).__name__}"
        )
    _validate_string_keys(mapping, prefix)
    for key in mapping:
        if key in FORBIDDEN_APP_KEYS:
            raise ValueError(
                f"Forbidden key '{prefix}.{key}' in '{FIELD_ENV_APP}:' "
                "overrides: server-managed, read-only, or lock-controlled "
                "CES App fields cannot be set via env manifests."
            )


class ModelPins(_StrictModel):
    """Model slots CES applies, pinned per environment.

    ``agent`` -> ``app.json`` and every agent ``modelSettings.model``;
    ``guardrail`` -> LLM policy / prompt-security guardrail judge model;
    ``data_store`` -> data-store tool rewriter and summarizer models.
    """

    agent: str | None = None
    guardrail: str | None = None
    data_store: str | None = None

    @field_validator("agent", "guardrail", "data_store")
    @classmethod
    def _non_blank_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("model pin must not be blank")
        return stripped


class EnvManifest(_StrictModel):
    """Environment template manifest (``envs/<environment>.yaml``).

    ``templates`` values may contain ``{profile}``, ``{profile_slug}``,
    ``{env}``, ``{channel}`` and ``{service}`` placeholders. They are
    exposed to app files as ``@@env.<key>@@`` (or
    ``@@env.<key>:<service>@@`` when the template uses ``{service}``).

    ``overrides`` deep-merges into ``environment.json``, while the optional
    ``app`` mapping deep-merges into ``app.{json,yaml,yml}`` before
    profile-derived settings are applied and is validated against the CES
    ``App`` schema.
    """

    environment: str = Field(pattern=r"^[a-z0-9_-]{1,32}$")
    templates: dict[str, str] = Field(default_factory=dict)
    overrides: dict[str, ManifestValue] = Field(default_factory=dict)
    app: dict[str, ManifestValue] | None = None
    locked: bool | None = None
    models: ModelPins | None = None

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_app_block(cls, data: ManifestValue) -> ManifestValue:
        if isinstance(data, dict) and data.get(FIELD_ENV_APP) is not None:
            validate_app_overlay_mapping(data[FIELD_ENV_APP])
        return data

    @model_validator(mode="before")
    @classmethod
    def _parse_top_level_templates(cls, data: ManifestValue) -> ManifestValue:
        if not isinstance(data, dict):
            return data

        out = dict(data)
        raw_templates = out.get("templates")
        if raw_templates is not None and not isinstance(raw_templates, dict):
            return out

        templates = dict(raw_templates or {})
        for k, v in list(out.items()):
            if isinstance(k, str) and k.endswith("_template"):
                templates[k.removesuffix("_template")] = v
                del out[k]

        if templates:
            out["templates"] = templates
        return out


class ContentBlockCatalog(_StrictModel):
    """Verbatim regulated copy keyed ``profile -> country -> lang -> key``."""

    blocks: dict[str, dict[str, dict[str, dict[str, str]]]] = Field(
        default_factory=dict
    )

    def blocks_for(
        self, profile_id: str, country: str, lang: str
    ) -> dict[str, str] | None:
        """Return the block map for one tuple, or ``None`` if undeclared."""
        try:
            return self.blocks[profile_id][country][lang]
        except KeyError:
            return None

    def missing_tuples(
        self, profile_id: str, countries: list[str], langs: list[str]
    ) -> list[tuple[str, str, str]]:
        """List ``(profile, country, lang)`` tuples without any block."""
        return [
            (profile_id, country, lang)
            for country in countries
            for lang in langs
            if not self.blocks_for(profile_id, country, lang)
        ]


def _validate_override_channel_keys(value: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(value) - set(CHANNEL_PROFILE_TYPES))
    if unknown:
        raise ValueError(
            f"unknown channel(s) {unknown}; expected one of "
            f"{sorted(CHANNEL_PROFILE_TYPES)}"
        )
    return value


def _validate_override_resource_ids(value: dict[str, Any]) -> dict[str, Any]:
    """Rejects override keys that could escape ``core/`` or ``out_dir``."""
    bad = sorted(
        str(k)
        for k in value
        if not isinstance(k, str)
        or not k
        or k != k.strip()
        or k in {".", ".."}
        or any(sep in k for sep in ("/", "\\", "\x00", ":"))
    )
    if bad:
        raise ValueError(
            f"invalid resource id(s) {bad}; override keys must be a single "
            "directory name (no '.', '..', whitespace padding, or path "
            "separators)"
        )
    return value


class LineReplacement(_StrictModel):
    """Deterministic line or unique-substring replacement inside text."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True
    )

    match: str = Field(min_length=1)
    replace_with: str = Field(alias="with")

    @field_validator("match")
    @classmethod
    def _non_whitespace_match(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("match must not be empty or whitespace")
        return value


class InstructionOverride(_StrictModel):
    """Structured prompt override (`instruction`, `replace_lines`, rules)."""

    instruction: str | None = None
    replace_lines: list[LineReplacement] = Field(default_factory=list)
    append_rules: list[str] = Field(default_factory=list)


class GlobalInstructionOverride(InstructionOverride):
    """Profile-wide and per-channel overrides for ``global_instruction.txt``."""

    channels: dict[str, InstructionOverride] = Field(default_factory=dict)

    @field_validator("channels")
    @classmethod
    def _known_channels(
        cls, value: dict[str, InstructionOverride]
    ) -> dict[str, InstructionOverride]:
        return _validate_override_channel_keys(value)


class AgentChannelOverride(InstructionOverride):
    """Channel-scoped overrides for a single subagent or root agent."""

    description: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    model: str | None = None

    @field_validator("temperature", mode="before")
    @classmethod
    def _reject_bool_temperature(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("temperature must be a number, not a boolean")
        return value

    @field_validator("model")
    @classmethod
    def _non_empty_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("model must not be empty or whitespace")
        return stripped


class AgentOverride(AgentChannelOverride):
    """Profile-wide and channel-scoped overrides for a single agent."""

    channels: dict[str, AgentChannelOverride] = Field(default_factory=dict)

    @field_validator("channels")
    @classmethod
    def _known_channels(
        cls, value: dict[str, AgentChannelOverride]
    ) -> dict[str, AgentChannelOverride]:
        return _validate_override_channel_keys(value)


class ToolChannelOverride(_StrictModel):
    """Channel-scoped overrides for a single tool."""

    description: str | None = None
    url: str | None = None


class ToolOverride(ToolChannelOverride):
    """Profile-wide and channel-scoped overrides for a single tool."""

    channels: dict[str, ToolChannelOverride] = Field(default_factory=dict)

    @field_validator("channels")
    @classmethod
    def _known_channels(
        cls, value: dict[str, ToolChannelOverride]
    ) -> dict[str, ToolChannelOverride]:
        return _validate_override_channel_keys(value)


class BrandOverrides(_StrictModel):
    """Optional profile/channel overrides (`profiles/<id>/overrides.yaml`)."""

    profile_id: str = Field(pattern=PROFILE_ID_PATTERN)
    global_instruction: GlobalInstructionOverride | None = None
    agents: dict[str, AgentOverride] = Field(default_factory=dict)
    tools: dict[str, ToolOverride] = Field(default_factory=dict)

    @field_validator("agents", "tools")
    @classmethod
    def _safe_resource_ids(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_override_resource_ids(value)


def _lookup_known(
    mapping: Mapping[str, _T], key: str, kind: str, known_label: str
) -> _T:
    try:
        return mapping[key]
    except KeyError as exc:
        known = sorted(mapping)
        raise ManifestError(
            f"Unknown {kind} '{key}'. {known_label}: {known}"
        ) from exc


class Manifests(BaseModel):
    """All manifests loaded from one ``manifests/`` root."""

    model_config = ConfigDict(frozen=True)

    root: Path
    profiles: dict[str, ProfileIdentity]
    policies: dict[str, ChannelPolicy]
    envs: dict[str, EnvManifest]
    content_blocks: ContentBlockCatalog | None = None
    overrides: dict[str, BrandOverrides] = Field(default_factory=dict)

    def profile(self, profile_id: str) -> ProfileIdentity:
        return _lookup_known(
            self.profiles, profile_id, "profile", "Known profiles"
        )

    def policy(self, profile_id: str) -> ChannelPolicy:
        if profile_id not in self.policies:
            raise ManifestError(
                f"Profile '{profile_id}' has no channel_policy.yaml."
            )
        return self.policies[profile_id]

    def env(self, environment: str) -> EnvManifest:
        return _lookup_known(self.envs, environment, "environment", "Known")

    def profile_overrides(self, profile_id: str) -> BrandOverrides | None:
        return self.overrides.get(profile_id)


def _read_yaml(path: Path) -> dict[str, ManifestValue]:
    try:
        data = yaml.load(
            path.read_text(encoding="utf-8"), Loader=UniqueKeyYaml12SafeLoader
        )
    except yaml.YAMLError as exc:
        raise ManifestError(f"{path}: invalid YAML: {exc}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ManifestError(f"{path}: unreadable manifest: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ManifestError(f"{path}: expected a mapping at the top level")
    return data


def _load_model(model: type[_ModelT], path: Path) -> _ModelT:
    try:
        return model.model_validate(_read_yaml(path))
    except (ValueError, TypeError) as exc:
        raise ManifestError(f"{path}: {exc}") from exc


def _assert_matching_id(
    path: Path,
    field_name: str,
    actual: str,
    expected: str,
    source_kind: str,
) -> None:
    if actual != expected:
        raise ManifestError(
            f"{path}: {field_name} '{actual}' does not match "
            f"{source_kind} '{expected}'"
        )


def _load_profile_dir(
    profile_dir: Path,
) -> tuple[ProfileIdentity, ChannelPolicy | None, BrandOverrides | None]:
    if not _PROFILE_ID_RE.fullmatch(profile_dir.name):
        raise ManifestError(
            f"Profile directory '{profile_dir.name}' must match "
            f"{PROFILE_ID_PATTERN}"
        )
    identity_path = profile_dir / "identity.yaml"
    if not identity_path.is_file():
        raise ManifestError(f"Missing '{identity_path}'")
    identity = _load_model(ProfileIdentity, identity_path)
    _assert_matching_id(
        identity_path,
        "profile_id",
        identity.profile_id,
        profile_dir.name,
        "directory name",
    )

    policy: ChannelPolicy | None = None
    policy_path = profile_dir / "channel_policy.yaml"
    if policy_path.is_file():
        policy = _load_model(ChannelPolicy, policy_path)
        _assert_matching_id(
            policy_path,
            "profile_id",
            policy.profile_id,
            profile_dir.name,
            "directory name",
        )

    ov: BrandOverrides | None = None
    overrides_path = profile_dir / "overrides.yaml"
    if overrides_path.is_file():
        ov = _load_model(BrandOverrides, overrides_path)
        _assert_matching_id(
            overrides_path,
            "profile_id",
            ov.profile_id,
            profile_dir.name,
            "directory name",
        )

    return identity, policy, ov


def _load_profile_manifests(
    profiles_dir: Path,
) -> tuple[
    dict[str, ProfileIdentity],
    dict[str, ChannelPolicy],
    dict[str, BrandOverrides],
]:
    profiles: dict[str, ProfileIdentity] = {}
    policies: dict[str, ChannelPolicy] = {}
    overrides: dict[str, BrandOverrides] = {}
    for profile_dir in sorted(p for p in profiles_dir.iterdir() if p.is_dir()):
        identity, policy, ov = _load_profile_dir(profile_dir)
        profiles[identity.profile_id] = identity
        if policy is not None:
            policies[policy.profile_id] = policy
        if ov is not None:
            overrides[ov.profile_id] = ov
    return profiles, policies, overrides


def _load_env_manifests(envs_dir: Path) -> dict[str, EnvManifest]:
    if not envs_dir.is_dir():
        return {}
    envs: dict[str, EnvManifest] = {}
    for env_path in sorted(envs_dir.glob("*.yaml")):
        env = _load_model(EnvManifest, env_path)
        _assert_matching_id(
            env_path, "environment", env.environment, env_path.stem, "file name"
        )
        envs[env.environment] = env
    return envs


def load_manifests(root: str | Path) -> Manifests:
    """Load and validate every manifest under ``root``.

    Raises:
        ManifestError: when the directory layout is wrong, a YAML file is
            malformed, a model fails validation, or a profile directory name
            disagrees with the ``profile_id`` declared inside its files.
    """
    root_path = Path(root)
    if not root_path.is_dir():
        raise ManifestError(f"Manifests root '{root_path}' is not a directory")

    profiles_dir = root_path / "profiles"
    if not profiles_dir.is_dir():
        raise ManifestError(f"Missing '{profiles_dir}' directory")

    profiles, policies, overrides = _load_profile_manifests(profiles_dir)
    envs = _load_env_manifests(root_path / "envs")

    blocks_path = root_path / "locales" / "content_blocks.yaml"
    content_blocks = (
        _load_model(ContentBlockCatalog, blocks_path)
        if blocks_path.is_file()
        else None
    )

    return Manifests(
        root=root_path,
        profiles=profiles,
        policies=policies,
        envs=envs,
        content_blocks=content_blocks,
        overrides=overrides,
    )
