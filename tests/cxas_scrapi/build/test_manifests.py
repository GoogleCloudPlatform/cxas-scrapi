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

"""Unit tests for Profile-Based Build manifest models and loader."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cxas_scrapi.build.manifests import (
    CHANNEL_PROFILE_TYPES,
    FORBIDDEN_APP_KEYS,
    ContentBlockCatalog,
    ManifestError,
    load_manifests,
)
from cxas_scrapi.core.deployments import Deployments

if TYPE_CHECKING:
    from pathlib import Path


def _write_minimal_profile(root: Path, profile_id: str = "profile_1") -> Path:
    profile_dir = root / "profiles" / profile_id
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "identity.yaml").write_text(
        f"profile_id: {profile_id}\n"
        "display_name: Profile One\n"
        "tts_voices:\n"
        "  en:\n"
        "    voice: en-US-Standard-A\n"
        "    speaking_rate: 1.0\n"
        "  fr:\n"
        "    voice: fr-FR-Standard-A\n",
        encoding="utf-8",
    )
    (profile_dir / "channel_policy.yaml").write_text(
        f"profile_id: {profile_id}\n"
        "supported_countries: ['US', 'CA']\n"
        "channels:\n"
        "  WEB:\n"
        "    supported_modalities: ['TEXT']\n"
        "  WHATSAPP:\n"
        "    supported_modalities: ['TEXT']\n"
        "  INSTAGRAM:\n"
        "    supported_modalities: ['TEXT']\n",
        encoding="utf-8",
    )
    return profile_dir


def test_load_manifests_happy_path_and_template_shorthand(
    tmp_path: Path,
) -> None:
    _write_minimal_profile(tmp_path)
    envs_dir = tmp_path / "envs"
    envs_dir.mkdir()
    (envs_dir / "prod.yaml").write_text(
        "environment: prod\n"
        "templates:\n"
        "  gcp_project: example-prod\n"
        "gcp_project_template: example-prod-from-root\n"
        "overrides:\n"
        "  region: us-central1\n",
        encoding="utf-8",
    )
    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()
    (locales_dir / "content_blocks.yaml").write_text(
        "blocks:\n  profile_1:\n    US:\n      en:\n        greeting: Hello\n",
        encoding="utf-8",
    )

    manifests = load_manifests(tmp_path)
    profile = manifests.profile("profile_1")
    assert profile.display_name == "Profile One"
    assert profile.languages == ["en", "fr"]
    assert (
        manifests.env("prod").templates["gcp_project"]
        == "example-prod-from-root"
    )
    assert manifests.content_blocks is not None
    assert manifests.content_blocks.blocks_for("profile_1", "US", "en") == {
        "greeting": "Hello"
    }
    assert manifests.content_blocks.missing_tuples(
        "profile_1", ["US", "CA"], ["en", "fr"]
    ) == [
        ("profile_1", "US", "fr"),
        ("profile_1", "CA", "en"),
        ("profile_1", "CA", "fr"),
    ]


def test_deployments_channel_types_include_whatsapp_and_instagram() -> None:
    assert Deployments.ChannelType.WHATSAPP.value == "WHATSAPP"
    assert Deployments.ChannelType.INSTAGRAM.value == "INSTAGRAM"
    assert CHANNEL_PROFILE_TYPES["WHATSAPP"] == "WHATSAPP"
    assert CHANNEL_PROFILE_TYPES["INSTAGRAM"] == "INSTAGRAM"


def test_load_manifests_validation_errors(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="not a directory"):
        load_manifests(tmp_path / "missing")

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ManifestError, match="Missing"):
        load_manifests(empty)

    # Profile ID directory mismatch
    bad_dir = tmp_path / "bad_mismatch"
    profile_dir = bad_dir / "profiles" / "profile_1"
    profile_dir.mkdir(parents=True)
    (profile_dir / "identity.yaml").write_text(
        "profile_id: profile_2\ndisplay_name: Profile Two\n", encoding="utf-8"
    )
    with pytest.raises(ManifestError, match="does not match directory name"):
        load_manifests(bad_dir)

    # Unknown channel
    bad_ch = tmp_path / "bad_channel"
    pdir = _write_minimal_profile(bad_ch)
    (pdir / "channel_policy.yaml").write_text(
        "profile_id: profile_1\nchannels:\n  PIGEON: {}\n", encoding="utf-8"
    )
    with pytest.raises(ManifestError, match="unknown channel"):
        load_manifests(bad_ch)

    # Bad ISO country code
    bad_iso = tmp_path / "bad_iso"
    pdir = _write_minimal_profile(bad_iso)
    (pdir / "channel_policy.yaml").write_text(
        "profile_id: profile_1\n"
        "supported_countries: ['USA']\n"
        "channels:\n"
        "  WEB: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="ISO-3166 alpha-2"):
        load_manifests(bad_iso)

    # Empty supported_modalities
    bad_mod = tmp_path / "bad_mod"
    pdir = _write_minimal_profile(bad_mod)
    (pdir / "channel_policy.yaml").write_text(
        "profile_id: profile_1\n"
        "channels:\n"
        "  WEB:\n"
        "    supported_modalities: []\n",
        encoding="utf-8",
    )
    with pytest.raises(
        ManifestError, match="supported_modalities must not be empty"
    ):
        load_manifests(bad_mod)

    # Extra unknown key rejected (extra="forbid")
    bad_extra = tmp_path / "bad_extra"
    pdir = _write_minimal_profile(bad_extra)
    (pdir / "identity.yaml").write_text(
        "profile_id: profile_1\ndisplay_name: Profile One\nextra_field: nope\n",
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="Extra inputs are not permitted"):
        load_manifests(bad_extra)

    # Environment filename mismatch
    bad_env = tmp_path / "bad_env"
    _write_minimal_profile(bad_env)
    (bad_env / "envs").mkdir()
    (bad_env / "envs" / "prod.yaml").write_text(
        "environment: staging\n", encoding="utf-8"
    )
    with pytest.raises(ManifestError, match="does not match file name"):
        load_manifests(bad_env)

    # Invalid YAML
    bad_yaml = tmp_path / "bad_yaml"
    pdir = _write_minimal_profile(bad_yaml)
    (pdir / "identity.yaml").write_text(":\n  - [unclosed", encoding="utf-8")
    with pytest.raises(ManifestError, match="invalid YAML"):
        load_manifests(bad_yaml)

    # Non-dict templates in env YAML must raise ManifestError (not TypeError)
    bad_tpl = tmp_path / "bad_tpl"
    _write_minimal_profile(bad_tpl)
    (bad_tpl / "envs").mkdir()
    (bad_tpl / "envs" / "prod.yaml").write_text(
        "environment: prod\ntemplates: 123\n", encoding="utf-8"
    )
    with pytest.raises(ManifestError):
        load_manifests(bad_tpl)


def test_manifests_lookup_errors(tmp_path: Path) -> None:
    _write_minimal_profile(tmp_path)
    manifests = load_manifests(tmp_path)
    with pytest.raises(ManifestError, match="Unknown profile"):
        manifests.profile("missing_profile")
    with pytest.raises(ManifestError, match="Unknown environment"):
        manifests.env("prod")

    catalog = ContentBlockCatalog()
    assert catalog.blocks_for("profile_1", "US", "en") is None


def test_yaml12_preserves_unquoted_country_no_and_booleans(
    tmp_path: Path,
) -> None:
    profile_dir = tmp_path / "profiles" / "nordic_profile"
    profile_dir.mkdir(parents=True)
    (profile_dir / "identity.yaml").write_text(
        "profile_id: nordic_profile\n"
        "display_name: Nordic Profile\n"
        "languages: [en, no]\n",
        encoding="utf-8",
    )
    (profile_dir / "channel_policy.yaml").write_text(
        "profile_id: nordic_profile\n"
        "supported_countries: [NO, US]\n"
        "channels:\n"
        "  WEB:\n"
        "    deeplink_widgets: true\n"
        "  API:\n"
        "    deeplink_widgets: false\n",
        encoding="utf-8",
    )
    envs_dir = tmp_path / "envs"
    envs_dir.mkdir()
    (envs_dir / "prod.yaml").write_text(
        "environment: prod\n"
        "overrides:\n"
        "  enabled: true\n"
        "  disabled: false\n"
        "  country_code: NO\n",
        encoding="utf-8",
    )

    manifests = load_manifests(tmp_path)
    profile = manifests.profile("nordic_profile")
    assert profile.languages == ["en", "no"]
    assert profile.tts_voices == {}

    policy = manifests.policy("nordic_profile")
    assert policy.supported_countries == ["NO", "US"]
    assert policy.channels["WEB"].deeplink_widgets is True
    assert policy.channels["API"].deeplink_widgets is False

    env = manifests.env("prod")
    assert env.overrides == {
        "enabled": True,
        "disabled": False,
        "country_code": "NO",
    }
    assert env.app is None


def test_env_manifest_app_block_and_yaml_anchor_merge(tmp_path: Path) -> None:
    _write_minimal_profile(tmp_path)
    envs_dir = tmp_path / "envs"
    envs_dir.mkdir()
    (envs_dir / "prod.yaml").write_text(
        "environment: prod\n"
        "app:\n"
        "  timeZoneSettings: &tz\n"
        "    timeZone: Europe/Oslo\n"
        "  guardrails:\n"
        "    - safety_guardrail\n"
        "  description: null\n"
        "  audioProcessingConfig:\n"
        "    inactivityTimeout: 300s\n"
        "  variableDeclarations:\n"
        "    - name: country\n"
        "      schema:\n"
        "        type: STRING\n"
        "        default: NO\n"
        "overrides:\n"
        "  tz_copy:\n"
        "    <<: *tz\n"
        "    timeZone: America/New_York\n",
        encoding="utf-8",
    )

    manifests = load_manifests(tmp_path)
    env = manifests.env("prod")
    assert env.app == {
        "timeZoneSettings": {"timeZone": "Europe/Oslo"},
        "guardrails": ["safety_guardrail"],
        "description": None,
        "audioProcessingConfig": {"inactivityTimeout": "300s"},
        "variableDeclarations": [
            {
                "name": "country",
                "schema": {"type": "STRING", "default": "NO"},
            }
        ],
    }
    assert env.overrides == {"tz_copy": {"timeZone": "America/New_York"}}


@pytest.mark.parametrize("forbidden_key", sorted(FORBIDDEN_APP_KEYS))
def test_env_manifest_app_rejects_forbidden_keys(
    tmp_path: Path, forbidden_key: str
) -> None:
    _write_minimal_profile(tmp_path)
    envs_dir = tmp_path / "envs"
    envs_dir.mkdir()
    (envs_dir / "prod.yaml").write_text(
        f"environment: prod\napp:\n  {forbidden_key}: value\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ManifestError,
        match=rf"(?s)prod\.yaml:.*Forbidden key 'app\.{forbidden_key}'",
    ):
        load_manifests(tmp_path)


@pytest.mark.parametrize(
    ("yaml_snippet", "expected_match"),
    [
        ("app:\n  200: bad\n", r"(?s)prod\.yaml:.*Non-string key 'app\.200'"),
        ("app:\n  true: bad\n", r"(?s)prod\.yaml:.*Non-string key 'app\.True'"),
        (
            "app:\n  audioProcessingConfig:\n    123: bad\n",
            r"(?s)prod\.yaml:.*Non-string key 'app\.audioProcessingConfig\.123'",
        ),
        (
            "app:\n  variableDeclarations:\n    - 123: bad\n",
            r"(?s)prod\.yaml:.*Non-string key 'app\.variableDeclarations\[0\]\.123'",
        ),
        ("app:\n  - not_a_dict\n", r"(?s)prod\.yaml:.*'app' must be a mapping"),
    ],
)
def test_env_manifest_app_rejects_non_string_keys_and_non_mapping(
    tmp_path: Path, yaml_snippet: str, expected_match: str
) -> None:
    _write_minimal_profile(tmp_path)
    envs_dir = tmp_path / "envs"
    envs_dir.mkdir()
    (envs_dir / "prod.yaml").write_text(
        f"environment: prod\n{yaml_snippet}", encoding="utf-8"
    )

    with pytest.raises(ManifestError, match=expected_match):
        load_manifests(tmp_path)


def test_env_manifest_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    _write_minimal_profile(tmp_path)
    envs_dir = tmp_path / "envs"
    envs_dir.mkdir()
    (envs_dir / "prod.yaml").write_text(
        "environment: prod\n"
        "app:\n"
        "  description: first\n"
        "  description: second\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ManifestError, match=r"prod\.yaml:.*Duplicate mapping key 'description'"
    ):
        load_manifests(tmp_path)
