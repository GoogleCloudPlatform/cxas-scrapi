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

    brands/<brand_id>/identity.yaml        -> BrandIdentity
    brands/<brand_id>/channel_policy.yaml  -> ChannelPolicy
    envs/<environment>.yaml                -> EnvManifest
    locales/content_blocks.yaml            -> ContentBlockCatalog (optional)

Brand *identity* (who the brand is) and channel *policy* (what a given
brand/channel may do) are deliberately separate files so that business
owners edit the former and platform owners the latter.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

BRAND_ID_PATTERN = r"^[a-z0-9_-]{2,64}$"
_BRAND_ID_RE = re.compile(BRAND_ID_PATTERN)

# Channel name (manifest key) -> CES deployment ChannelProfile type.
CHANNEL_PROFILE_TYPES: dict[str, str] = {
    "VOICE_TELEPHONY": "GOOGLE_TELEPHONY_PLATFORM",
    "CONTACT_CENTER": "CONTACT_CENTER_AS_A_SERVICE",
    "DIGITAL_APP": "WEB_UI",
    "WEB": "WEB_UI",
    "API": "API",
    "WHATSAPP": "WHATSAPP",
}

# ChannelProfile types that are voice-first (used by lint rule T017).
VOICE_CHANNEL_PROFILE_TYPES: frozenset[str] = frozenset(
    {"GOOGLE_TELEPHONY_PLATFORM", "CONTACT_CENTER_AS_A_SERVICE"}
)


class ManifestError(ValueError):
    """Raised when a manifest file is missing or fails validation."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TtsVoice(_StrictModel):
    """One TTS voice binding for a language."""

    voice: str = Field(min_length=1)
    speaking_rate: float = Field(default=1.0, gt=0.0, le=4.0)


class BrandIdentity(_StrictModel):
    """Business-owned description of who a brand is.

    Rendered by ``cxas build`` into the persona/lexicon block of
    ``global_instruction.txt``, into ``app.json``
    ``synthesizeSpeechConfigs``, and into ``@@brand.*@@`` placeholders in
    guardrail prompts and tool descriptions.
    """

    brand_id: str = Field(pattern=BRAND_ID_PATTERN)
    display_name: str = Field(min_length=1)
    assistant_title: str | None = None
    legal_entity: str | None = None
    tone_guidelines: str | None = None
    lexicon: dict[str, str] = Field(default_factory=dict)
    urls: dict[str, str] = Field(default_factory=dict)
    tts_voices: dict[str, TtsVoice] = Field(default_factory=dict)

    @property
    def languages(self) -> list[str]:
        """Languages this brand speaks, in manifest order."""
        return list(self.tts_voices.keys())


class ChannelCapabilities(_StrictModel):
    """What a single channel of a brand may do."""

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
    """Platform-owned policy: channels, routing and countries for a brand."""

    brand_id: str = Field(pattern=BRAND_ID_PATTERN)
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
            raise ValueError(
                f"unknown channel(s) {unknown}; expected one of "
                f"{sorted(CHANNEL_PROFILE_TYPES)}"
            )
        return value

    @field_validator("supported_countries")
    @classmethod
    def _iso_countries(cls, value: list[str]) -> list[str]:
        bad = [c for c in value if not re.fullmatch(r"[A-Z]{2}", c)]
        if bad:
            raise ValueError(
                f"supported_countries must be ISO-3166 alpha-2 codes, got {bad}"
            )
        return value


class EnvManifest(_StrictModel):
    """Environment template manifest (``envs/<environment>.yaml``).

    ``templates`` values may contain ``{brand}``, ``{brand_slug}``,
    ``{env}``, ``{channel}`` and ``{service}`` placeholders. They are
    exposed to app files as ``@@env.<key>@@`` (or
    ``@@env.<key>:<service>@@`` when the template uses ``{service}``).
    """

    environment: str = Field(pattern=r"^[a-z0-9_-]{1,32}$")
    templates: dict[str, str] = Field(default_factory=dict)
    overrides: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _parse_top_level_templates(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        # We need a new dict because data could be immutable
        out = dict(data)
        templates = dict(out.get("templates") or {})

        for k, v in list(out.items()):
            if k.endswith("_template"):
                key_name = k[:-9]  # len("_template") == 9
                templates[key_name] = v
                del out[k]

        if templates:
            out["templates"] = templates

        return out


class ContentBlockCatalog(_StrictModel):
    """Verbatim regulated copy keyed ``brand -> country -> lang -> key``."""

    blocks: dict[str, dict[str, dict[str, dict[str, str]]]] = Field(
        default_factory=dict
    )

    def blocks_for(
        self, brand_id: str, country: str, lang: str
    ) -> dict[str, str] | None:
        """Return the block map for one tuple, or ``None`` if undeclared."""
        return self.blocks.get(brand_id, {}).get(country, {}).get(lang)

    def missing_tuples(
        self, brand_id: str, countries: list[str], langs: list[str]
    ) -> list[tuple[str, str, str]]:
        """List ``(brand, country, lang)`` tuples without any block."""
        return [
            (brand_id, country, lang)
            for country in countries
            for lang in langs
            if not self.blocks_for(brand_id, country, lang)
        ]


class Manifests(BaseModel):
    """All manifests loaded from one ``manifests/`` root."""

    model_config = ConfigDict(frozen=True)

    root: Path
    brands: dict[str, BrandIdentity]
    policies: dict[str, ChannelPolicy]
    envs: dict[str, EnvManifest]
    content_blocks: ContentBlockCatalog | None = None

    def brand(self, brand_id: str) -> BrandIdentity:
        try:
            return self.brands[brand_id]
        except KeyError as exc:
            raise ManifestError(
                f"Unknown brand '{brand_id}'. Known brands: "
                f"{sorted(self.brands)}"
            ) from exc

    def policy(self, brand_id: str) -> ChannelPolicy:
        try:
            return self.policies[brand_id]
        except KeyError as exc:
            raise ManifestError(
                f"Brand '{brand_id}' has no channel_policy.yaml."
            ) from exc

    def env(self, environment: str) -> EnvManifest:
        try:
            return self.envs[environment]
        except KeyError as exc:
            raise ManifestError(
                f"Unknown environment '{environment}'. Known: "
                f"{sorted(self.envs)}"
            ) from exc


# ── Loading ─────────────────────────────────────────────────────────────


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ManifestError(f"{path}: invalid YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ManifestError(f"{path}: expected a mapping at the top level")
    return data


def _load_model(model: type[BaseModel], path: Path) -> Any:
    try:
        return model.model_validate(_read_yaml(path))
    except ValueError as exc:  # pydantic.ValidationError is a ValueError
        raise ManifestError(f"{path}: {exc}") from exc


def load_manifests(root: str | Path) -> Manifests:
    """Load and validate every manifest under ``root``.

    Raises:
        ManifestError: when the directory layout is wrong, a YAML file is
            malformed, a model fails validation, or a brand directory name
            disagrees with the ``brand_id`` declared inside its files.
    """
    root = Path(root)
    if not root.is_dir():
        raise ManifestError(f"Manifests root '{root}' is not a directory")

    brands_dir = root / "brands"
    if not brands_dir.is_dir():
        raise ManifestError(f"Missing '{brands_dir}' directory")

    brands: dict[str, BrandIdentity] = {}
    policies: dict[str, ChannelPolicy] = {}
    for brand_dir in sorted(p for p in brands_dir.iterdir() if p.is_dir()):
        if not _BRAND_ID_RE.fullmatch(brand_dir.name):
            raise ManifestError(
                f"Brand directory '{brand_dir.name}' must match "
                f"{BRAND_ID_PATTERN}"
            )
        identity_path = brand_dir / "identity.yaml"
        if not identity_path.is_file():
            raise ManifestError(f"Missing '{identity_path}'")
        identity: BrandIdentity = _load_model(BrandIdentity, identity_path)
        if identity.brand_id != brand_dir.name:
            raise ManifestError(
                f"{identity_path}: brand_id '{identity.brand_id}' does not "
                f"match directory name '{brand_dir.name}'"
            )
        brands[identity.brand_id] = identity

        policy_path = brand_dir / "channel_policy.yaml"
        if policy_path.is_file():
            policy: ChannelPolicy = _load_model(ChannelPolicy, policy_path)
            if policy.brand_id != brand_dir.name:
                raise ManifestError(
                    f"{policy_path}: brand_id '{policy.brand_id}' does not "
                    f"match directory name '{brand_dir.name}'"
                )
            policies[policy.brand_id] = policy

    envs: dict[str, EnvManifest] = {}
    envs_dir = root / "envs"
    if envs_dir.is_dir():
        for env_path in sorted(envs_dir.glob("*.yaml")):
            env: EnvManifest = _load_model(EnvManifest, env_path)
            if env.environment != env_path.stem:
                raise ManifestError(
                    f"{env_path}: environment '{env.environment}' does not "
                    f"match file name '{env_path.stem}'"
                )
            envs[env.environment] = env

    content_blocks: ContentBlockCatalog | None = None
    blocks_path = root / "locales" / "content_blocks.yaml"
    if blocks_path.is_file():
        content_blocks = _load_model(ContentBlockCatalog, blocks_path)

    return Manifests(
        root=root,
        brands=brands,
        policies=policies,
        envs=envs,
        content_blocks=content_blocks,
    )
