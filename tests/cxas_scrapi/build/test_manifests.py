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

"""Unit tests for Multi-Brand Build manifest models and loader."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cxas_scrapi.build.manifests import (
    CHANNEL_PROFILE_TYPES,
    ContentBlockCatalog,
    ManifestError,
    load_manifests,
)
from cxas_scrapi.core.deployments import Deployments

if TYPE_CHECKING:
    from pathlib import Path


def _write_minimal_brand(root: Path, brand_id: str = "brand_1") -> Path:
    brand_dir = root / "brands" / brand_id
    brand_dir.mkdir(parents=True, exist_ok=True)
    (brand_dir / "identity.yaml").write_text(
        f"brand_id: {brand_id}\n"
        "display_name: Brand One\n"
        "tts_voices:\n"
        "  en:\n"
        "    voice: en-US-Standard-A\n"
        "    speaking_rate: 1.0\n"
        "  fr:\n"
        "    voice: fr-FR-Standard-A\n",
        encoding="utf-8",
    )
    (brand_dir / "channel_policy.yaml").write_text(
        f"brand_id: {brand_id}\n"
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
    return brand_dir


def test_load_manifests_happy_path_and_template_shorthand(
    tmp_path: Path,
) -> None:
    _write_minimal_brand(tmp_path)
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
        "blocks:\n  brand_1:\n    US:\n      en:\n        greeting: Hello\n",
        encoding="utf-8",
    )

    manifests = load_manifests(tmp_path)
    brand = manifests.brand("brand_1")
    assert brand.display_name == "Brand One"
    assert brand.languages == ["en", "fr"]
    assert (
        manifests.env("prod").templates["gcp_project"]
        == "example-prod-from-root"
    )
    assert manifests.content_blocks is not None
    assert manifests.content_blocks.blocks_for("brand_1", "US", "en") == {
        "greeting": "Hello"
    }
    assert manifests.content_blocks.missing_tuples(
        "brand_1", ["US", "CA"], ["en", "fr"]
    ) == [
        ("brand_1", "US", "fr"),
        ("brand_1", "CA", "en"),
        ("brand_1", "CA", "fr"),
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

    # Brand ID directory mismatch
    bad_dir = tmp_path / "bad_mismatch"
    brand_dir = bad_dir / "brands" / "brand_1"
    brand_dir.mkdir(parents=True)
    (brand_dir / "identity.yaml").write_text(
        "brand_id: brand_2\ndisplay_name: Brand Two\n", encoding="utf-8"
    )
    with pytest.raises(ManifestError, match="does not match directory name"):
        load_manifests(bad_dir)

    # Unknown channel
    bad_ch = tmp_path / "bad_channel"
    bdir = _write_minimal_brand(bad_ch)
    (bdir / "channel_policy.yaml").write_text(
        "brand_id: brand_1\nchannels:\n  PIGEON: {}\n", encoding="utf-8"
    )
    with pytest.raises(ManifestError, match="unknown channel"):
        load_manifests(bad_ch)

    # Bad ISO country code
    bad_iso = tmp_path / "bad_iso"
    bdir = _write_minimal_brand(bad_iso)
    (bdir / "channel_policy.yaml").write_text(
        "brand_id: brand_1\n"
        "supported_countries: ['USA']\n"
        "channels:\n"
        "  WEB: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="ISO-3166 alpha-2"):
        load_manifests(bad_iso)

    # Empty supported_modalities
    bad_mod = tmp_path / "bad_mod"
    bdir = _write_minimal_brand(bad_mod)
    (bdir / "channel_policy.yaml").write_text(
        "brand_id: brand_1\nchannels:\n  WEB:\n    supported_modalities: []\n",
        encoding="utf-8",
    )
    with pytest.raises(
        ManifestError, match="supported_modalities must not be empty"
    ):
        load_manifests(bad_mod)

    # Extra unknown key rejected (extra="forbid")
    bad_extra = tmp_path / "bad_extra"
    bdir = _write_minimal_brand(bad_extra)
    (bdir / "identity.yaml").write_text(
        "brand_id: brand_1\ndisplay_name: Brand One\nextra_field: nope\n",
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="Extra inputs are not permitted"):
        load_manifests(bad_extra)

    # Environment filename mismatch
    bad_env = tmp_path / "bad_env"
    _write_minimal_brand(bad_env)
    (bad_env / "envs").mkdir()
    (bad_env / "envs" / "prod.yaml").write_text(
        "environment: staging\n", encoding="utf-8"
    )
    with pytest.raises(ManifestError, match="does not match file name"):
        load_manifests(bad_env)

    # Invalid YAML
    bad_yaml = tmp_path / "bad_yaml"
    bdir = _write_minimal_brand(bad_yaml)
    (bdir / "identity.yaml").write_text(":\n  - [unclosed", encoding="utf-8")
    with pytest.raises(ManifestError, match="invalid YAML"):
        load_manifests(bad_yaml)


def test_manifests_lookup_errors(tmp_path: Path) -> None:
    _write_minimal_brand(tmp_path)
    manifests = load_manifests(tmp_path)
    with pytest.raises(ManifestError, match="Unknown brand"):
        manifests.brand("missing_brand")
    with pytest.raises(ManifestError, match="Unknown environment"):
        manifests.env("prod")

    catalog = ContentBlockCatalog()
    assert catalog.blocks_for("brand_1", "US", "en") is None
