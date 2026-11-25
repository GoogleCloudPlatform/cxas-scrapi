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
from typing import TYPE_CHECKING, Any, TypeAlias, TypeVar

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

BRAND_ID_PATTERN = r"^[a-z0-9_-]{2,64}$"
_BRAND_ID_RE = re.compile(BRAND_ID_PATTERN)
_T = TypeVar("_T")
_ModelT = TypeVar("_ModelT", bound=BaseModel)
ManifestValue: TypeAlias = Any

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


# scbc ignore[empty-exception-subclass] public API exception
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
    # scbc ignore[trivial-wrapper] public BrandIdentity API property
    def languages(self) -> list[str]:
        """Languages this brand speaks, in manifest order."""
        return list(self.tts_voices)


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


class EnvManifest(_StrictModel):
    """Environment template manifest (``envs/<environment>.yaml``).

    ``templates`` values may contain ``{brand}``, ``{brand_slug}``,
    ``{env}``, ``{channel}`` and ``{service}`` placeholders. They are
    exposed to app files as ``@@env.<key>@@`` (or
    ``@@env.<key>:<service>@@`` when the template uses ``{service}``).
    """

    environment: str = Field(pattern=r"^[a-z0-9_-]{1,32}$")
    templates: dict[str, str] = Field(default_factory=dict)
    overrides: dict[str, ManifestValue] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _parse_top_level_templates(cls, data: ManifestValue) -> ManifestValue:
        # scbc boundary: Pydantic before-validator normalizes raw YAML input
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
    """Verbatim regulated copy keyed ``brand -> country -> lang -> key``."""

    blocks: dict[str, dict[str, dict[str, dict[str, str]]]] = Field(
        default_factory=dict
    )

    def blocks_for(
        self, brand_id: str, country: str, lang: str
    ) -> dict[str, str] | None:
        """Return the block map for one tuple, or ``None`` if undeclared."""
        try:
            return self.blocks[brand_id][country][lang]
        except KeyError:
            return None

    # scbc ignore[trivial-wrapper] public ContentBlockCatalog query API
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
    brands: dict[str, BrandIdentity]
    policies: dict[str, ChannelPolicy]
    envs: dict[str, EnvManifest]
    content_blocks: ContentBlockCatalog | None = None

    # scbc ignore[trivial-wrapper] public Manifests lookup API
    def brand(self, brand_id: str) -> BrandIdentity:
        return _lookup_known(self.brands, brand_id, "brand", "Known brands")

    def policy(self, brand_id: str) -> ChannelPolicy:
        if brand_id not in self.policies:
            raise ManifestError(
                f"Brand '{brand_id}' has no channel_policy.yaml."
            )
        return self.policies[brand_id]

    # scbc ignore[trivial-wrapper] public Manifests lookup API
    def env(self, environment: str) -> EnvManifest:
        return _lookup_known(self.envs, environment, "environment", "Known")


def _read_yaml(path: Path) -> dict[str, ManifestValue]:
    # scbc boundary: reads and validates top-level YAML mapping from disk
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ManifestError(f"{path}: invalid YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ManifestError(f"{path}: expected a mapping at the top level")
    return data


def _load_model(model: type[_ModelT], path: Path) -> _ModelT:
    # scbc boundary: validates external YAML file against Pydantic schema
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


def _load_brand_dir(
    brand_dir: Path,
) -> tuple[BrandIdentity, ChannelPolicy | None]:
    if not _BRAND_ID_RE.fullmatch(brand_dir.name):
        raise ManifestError(
            f"Brand directory '{brand_dir.name}' must match {BRAND_ID_PATTERN}"
        )
    identity_path = brand_dir / "identity.yaml"
    if not identity_path.is_file():
        raise ManifestError(f"Missing '{identity_path}'")
    identity = _load_model(BrandIdentity, identity_path)
    _assert_matching_id(
        identity_path,
        "brand_id",
        identity.brand_id,
        brand_dir.name,
        "directory name",
    )

    policy_path = brand_dir / "channel_policy.yaml"
    if not policy_path.is_file():
        return identity, None
    policy = _load_model(ChannelPolicy, policy_path)
    _assert_matching_id(
        policy_path,
        "brand_id",
        policy.brand_id,
        brand_dir.name,
        "directory name",
    )
    return identity, policy


def _load_brand_manifests(
    brands_dir: Path,
) -> tuple[dict[str, BrandIdentity], dict[str, ChannelPolicy]]:
    brands: dict[str, BrandIdentity] = {}
    policies: dict[str, ChannelPolicy] = {}
    for brand_dir in sorted(p for p in brands_dir.iterdir() if p.is_dir()):
        identity, policy = _load_brand_dir(brand_dir)
        brands[identity.brand_id] = identity
        if policy is not None:
            policies[policy.brand_id] = policy
    return brands, policies


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
            malformed, a model fails validation, or a brand directory name
            disagrees with the ``brand_id`` declared inside its files.
    """
    root_path = Path(root)
    if not root_path.is_dir():
        raise ManifestError(f"Manifests root '{root_path}' is not a directory")

    brands_dir = root_path / "brands"
    if not brands_dir.is_dir():
        raise ManifestError(f"Missing '{brands_dir}' directory")

    brands, policies = _load_brand_manifests(brands_dir)
    envs = _load_env_manifests(root_path / "envs")

    blocks_path = root_path / "locales" / "content_blocks.yaml"
    content_blocks = (
        _load_model(ContentBlockCatalog, blocks_path)
        if blocks_path.is_file()
        else None
    )

    return Manifests(
        root=root_path,
        brands=brands,
        policies=policies,
        envs=envs,
        content_blocks=content_blocks,
    )
