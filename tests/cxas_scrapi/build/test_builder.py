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

"""Unit tests for Multi-Brand Build (`cxas_scrapi.build.builder`)."""

from __future__ import annotations

import ast
import filecmp
import hashlib
import json
import os
import zipfile
from typing import TYPE_CHECKING

import pytest
import yaml

from cxas_scrapi.build.builder import (
    BuildError,
    build_all,
    build_app,
    deep_merge,
)
from cxas_scrapi.build.manifests import ManifestError

if TYPE_CHECKING:
    from pathlib import Path


def _setup_sample_workspace(tmp_path: Path) -> tuple[Path, Path]:
    core_dir = tmp_path / "core"
    core_dir.mkdir()

    app_json = {
        "rootAgent": "default_agent",
        "variableDeclarations": [{"name": "var1"}],
    }
    (core_dir / "app.json").write_text(json.dumps(app_json), encoding="utf-8")
    (core_dir / "environment.json").write_text(
        json.dumps({"some_key": "val1", "nested": {"keep": 1, "replace": 0}}),
        encoding="utf-8",
    )
    (core_dir / "global_instruction.txt").write_text(
        "Instruction for @@brand.display_name@@ (@@build.channel@@/"
        "@@build.environment@@). URL: @@env.api:auth@@ "
        "Support: @@brand.urls.support@@ Routing: @@routing.core_api@@\n",
        encoding="utf-8",
    )

    agents_dir = core_dir / "agents"
    agents_dir.mkdir()
    a1_dir = agents_dir / "default_agent"
    a1_dir.mkdir()
    (a1_dir / "instruction.txt").write_text(
        "Call {@TOOL: secret_tool_disabled}.\n", encoding="utf-8"
    )
    (a1_dir / "default_agent.json").write_text(
        json.dumps(
            {
                "displayName": "default_agent",
                "childAgents": [
                    "pruned_agent_display",
                    {"displayName": "pruned_agent_display"},
                ],
                "tools": ["secret_tool_disabled", "active_tool"],
            }
        ),
        encoding="utf-8",
    )

    a2_dir = agents_dir / "pruned_agent_dir"
    a2_dir.mkdir()
    (a2_dir / "pruned_agent_dir.json").write_text(
        json.dumps({"displayName": "pruned_agent_display"}), encoding="utf-8"
    )

    a3_dir = agents_dir / "custom_entry"
    a3_dir.mkdir()
    (a3_dir / "custom_entry.json").write_text(
        json.dumps({"displayName": "custom_entry"}), encoding="utf-8"
    )

    tools_dir = core_dir / "tools"
    tools_dir.mkdir()
    t1_dir = tools_dir / "secret_tool_dir"
    t1_dir.mkdir()
    (t1_dir / "secret_tool_dir.json").write_text(
        json.dumps({"displayName": "secret_tool_disabled"}), encoding="utf-8"
    )

    t2_dir = tools_dir / "active_tool"
    (t2_dir / "python_function").mkdir(parents=True)
    (t2_dir / "active_tool.json").write_text(
        json.dumps({"displayName": "active_tool"}), encoding="utf-8"
    )
    (t2_dir / "python_function" / "python_code.py").write_text(
        'def active_tool() -> str:\n    return "Welcome to @@brand.display_name@@"\n',
        encoding="utf-8",
    )

    gr_dir = core_dir / "guardrails" / "brand_guardrail"
    gr_dir.mkdir(parents=True)
    (gr_dir / "brand_guardrail.json").write_text(
        json.dumps(
            {
                "llmPolicy": {
                    "prompt": "Guardrail for @@brand.display_name@@ "
                    "(@@brand.lexicon.account@@)."
                }
            }
        ),
        encoding="utf-8",
    )

    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()

    b1 = manifests_dir / "brands" / "brand_1"
    b1.mkdir(parents=True)
    (b1 / "identity.yaml").write_text(
        "brand_id: brand_1\n"
        "display_name: Brand One\n"
        "assistant_title: Brand One Assistant\n"
        "legal_entity: Brand One Corp\n"
        "tone_guidelines: Helpful and concise.\n"
        "lexicon:\n"
        "  account: Checking Account\n"
        "urls:\n"
        "  support: https://support.brand1.example.com\n"
        "tts_voices:\n"
        "  en:\n"
        "    voice: en-US-Standard-A\n"
        "    speaking_rate: 1.0\n"
        "  es:\n"
        "    voice: es-ES-Standard-A\n"
        "    speaking_rate: 1.1\n",
        encoding="utf-8",
    )
    (b1 / "channel_policy.yaml").write_text(
        "brand_id: brand_1\n"
        "supported_countries: ['US']\n"
        "api_routing:\n"
        "  core_api: https://core.brand1.example.com\n"
        "channels:\n"
        "  VOICE_TELEPHONY:\n"
        "    entry_agent: custom_entry\n"
        "    disabled_agents: [pruned_agent_display]\n"
        "    disabled_tools: [secret_tool_disabled]\n"
        "    supported_modalities: ['AUDIO']\n"
        "  DIGITAL_APP:\n"
        "    supported_modalities: ['TEXT']\n"
        "    deeplink_widgets: true\n",
        encoding="utf-8",
    )

    b2 = manifests_dir / "brands" / "brand_2"
    b2.mkdir(parents=True)
    (b2 / "identity.yaml").write_text(
        "brand_id: brand_2\n"
        "display_name: Brand Two\n"
        "lexicon:\n"
        "  account: Savings Account\n"
        "urls:\n"
        "  support: https://support.brand2.example.com\n",
        encoding="utf-8",
    )
    (b2 / "channel_policy.yaml").write_text(
        "brand_id: brand_2\n"
        "api_routing:\n"
        "  core_api: https://core.brand2.example.com\n"
        "channels:\n"
        "  DIGITAL_APP:\n"
        "    supported_modalities: ['TEXT']\n",
        encoding="utf-8",
    )

    envs_dir = manifests_dir / "envs"
    envs_dir.mkdir()
    (envs_dir / "prod.yaml").write_text(
        "environment: prod\n"
        "templates:\n"
        "  api: https://api.example.com/{brand_slug}/{env}/{service}\n"
        "overrides:\n"
        "  overridden_key: prod_val\n"
        "  nested:\n"
        "    replace: 2\n",
        encoding="utf-8",
    )

    locales_dir = manifests_dir / "locales"
    locales_dir.mkdir()
    (locales_dir / "content_blocks.yaml").write_text(
        "blocks:\n"
        "  brand_1:\n"
        "    US:\n"
        "      en:\n"
        "        disclosure: Regulated disclosure.\n",
        encoding="utf-8",
    )

    return core_dir, manifests_dir


def test_build_all_pruning_tokens_deployments_and_determinism(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)

    out_root1 = tmp_path / "out1"
    reports1 = build_all(
        core_dir, manifests_dir, out_root1, "prod", package_zip=True
    )
    assert len(reports1) == 3

    b1_voice = out_root1 / "brand_1" / "voice_telephony"
    assert b1_voice.exists()
    assert not (b1_voice / "agents" / "pruned_agent_dir").exists()
    assert not (b1_voice / "tools" / "secret_tool_dir").exists()
    assert (b1_voice / "tools" / "active_tool").exists()

    # app.json checks: configured entry_agent aligns rootAgent with entryAgent
    a_json = json.loads((b1_voice / "app.json").read_text("utf-8"))
    assert a_json["rootAgent"] == "custom_entry"
    b1_digital = out_root1 / "brand_1" / "digital_app"
    digital_app = json.loads((b1_digital / "app.json").read_text("utf-8"))
    assert digital_app["rootAgent"] == "default_agent"
    assert (
        a_json["audioProcessingConfig"]["synthesizeSpeechConfigs"]["en"][
            "voice"
        ]
        == "en-US-Standard-A"
    )
    assert a_json["languageSettings"]["defaultLanguageCode"] == "en"
    assert a_json["languageSettings"]["supportedLanguageCodes"] == ["en", "es"]
    assert a_json["languageSettings"]["enableMultilingualSupport"] is True

    # Surviving agent JSON references pruned cleanly
    d_json = json.loads(
        (
            b1_voice / "agents" / "default_agent" / "default_agent.json"
        ).read_text("utf-8")
    )
    assert d_json["childAgents"] == []
    assert d_json["tools"] == ["active_tool"]

    # environment.json deep merged
    e_json = json.loads((b1_voice / "environment.json").read_text("utf-8"))
    assert e_json["some_key"] == "val1"
    assert e_json["overridden_key"] == "prod_val"
    assert e_json["nested"] == {"keep": 1, "replace": 2}

    # Guardrail JSON token replacement
    g_json = json.loads(
        (
            b1_voice / "guardrails" / "brand_guardrail" / "brand_guardrail.json"
        ).read_text("utf-8")
    )
    assert (
        g_json["llmPolicy"]["prompt"]
        == "Guardrail for Brand One (Checking Account)."
    )

    # Global instruction identity block + tokens + locale content block
    gi = (b1_voice / "global_instruction.txt").read_text("utf-8")
    assert "# Brand: Brand One" in gi
    assert "[US-en] disclosure: Regulated disclosure." in gi
    assert "URL: https://api.example.com/brand-1/prod/auth" in gi
    assert "Support: https://support.brand1.example.com" in gi
    assert "Routing: https://core.brand1.example.com" in gi

    # Deployment YAML emitted
    dep_yaml = yaml.safe_load(
        (b1_voice / "deployments" / "voice-telephony.yaml").read_text("utf-8")
    )
    assert dep_yaml["brandId"] == "brand_1"
    assert (
        dep_yaml["channelProfile"]["channelType"] == "GOOGLE_TELEPHONY_PLATFORM"
    )
    assert dep_yaml["entryAgent"] == "custom_entry"

    # Stale tool warning recorded
    r_voice = next(r for r in reports1 if r.channel == "VOICE_TELEPHONY")
    assert any(
        "mentions pruned tool secret_tool_disabled" in w
        for w in r_voice.warnings
    )
    assert r_voice.zip_path is not None
    assert r_voice.zip_path.is_file()
    with zipfile.ZipFile(r_voice.zip_path, "r") as zf:
        assert "app.json" in zf.namelist()
        assert "deployments/voice-telephony.yaml" not in zf.namelist()

    # Determinism across two independent builds (files and ZIP hashes)
    out_root2 = tmp_path / "out2"
    reports2 = build_all(
        core_dir, manifests_dir, out_root2, "prod", package_zip=True
    )

    def compare_dirs(d1: Path, d2: Path) -> None:
        dcmp = filecmp.dircmp(d1, d2)
        assert not dcmp.left_only
        assert not dcmp.right_only
        assert not dcmp.diff_files
        for sub in dcmp.common_dirs:
            compare_dirs(d1 / sub, d2 / sub)

    compare_dirs(out_root1, out_root2)
    for r1, r2 in zip(reports1, reports2, strict=True):
        assert r1.zip_path is not None
        assert r2.zip_path is not None
        assert hashlib.sha256(r1.zip_path.read_bytes()).hexdigest() == (
            hashlib.sha256(r2.zip_path.read_bytes()).hexdigest()
        )


def test_conditional_blocks_and_py_token_substitution(tmp_path: Path) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    instr_path = core_dir / "agents" / "default_agent" / "instruction.txt"
    instr_path.write_text(
        "{@startChannel_VOICE_TELEPHONY}\n"
        "Voice mode for @@brand.display_name@@.\n"
        "{@startAgentEnabled_pruned_agent_display}\n"
        "Route to {@AGENT: pruned_agent_display}.\n"
        "{@endAgentEnabled_pruned_agent_display}\n"
        "{@startBrand_brand_1}\n"
        "Brand One voice fallback.\n"
        "{@endBrand_brand_1}\n"
        "{@endChannel_VOICE_TELEPHONY}\n"
        "{@startChannel_DIGITAL_APP}\n"
        "Digital mode for @@brand.display_name@@.\n"
        "{@startToolEnabled_secret_tool_disabled}\n"
        "Call {@TOOL: secret_tool_disabled}.\n"
        "{@endToolEnabled_secret_tool_disabled}\n"
        "{@endChannel_DIGITAL_APP}\n"
        "Inline: {@startBrand_brand_1}B1{@endBrand_brand_1}"
        "{@startBrand_brand_2}B2{@endBrand_brand_2}\n",
        encoding="utf-8",
    )

    out_voice = tmp_path / "out_voice"
    rep_voice = build_app(
        core_dir, manifests_dir, "brand_1", "VOICE_TELEPHONY", "prod", out_voice
    )
    assert not rep_voice.warnings
    built_voice = (
        out_voice / "agents" / "default_agent" / "instruction.txt"
    ).read_text("utf-8")
    assert "Voice mode for Brand One." in built_voice
    assert "Brand One voice fallback." in built_voice
    assert "Inline: B1" in built_voice
    assert "Digital mode" not in built_voice
    assert "pruned_agent_display" not in built_voice
    assert "{@start" not in built_voice

    built_py = (
        out_voice
        / "tools"
        / "active_tool"
        / "python_function"
        / "python_code.py"
    ).read_text("utf-8")
    assert 'return "Welcome to Brand One"' in built_py

    out_digital = tmp_path / "out_digital"
    rep_digital = build_app(
        core_dir, manifests_dir, "brand_2", "DIGITAL_APP", "prod", out_digital
    )
    assert not rep_digital.warnings
    built_digital = (
        out_digital / "agents" / "default_agent" / "instruction.txt"
    ).read_text("utf-8")
    assert "Digital mode for Brand Two." in built_digital
    assert "Call {@TOOL: secret_tool_disabled}." in built_digital
    assert "Inline: B2" in built_digital
    assert "Voice mode" not in built_digital


def test_root_agent_fallback_when_pruned_and_explicit_identity_block(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    (core_dir / "app.json").write_text(
        json.dumps({"rootAgent": "pruned_agent_display"}), encoding="utf-8"
    )
    (core_dir / "global_instruction.txt").write_text(
        "HEADER\n@@brand.identity_block@@\nFOOTER\n", encoding="utf-8"
    )

    out_dir = tmp_path / "out_root_fallback"
    build_app(
        core_dir, manifests_dir, "brand_1", "VOICE_TELEPHONY", "prod", out_dir
    )
    a_json = json.loads((out_dir / "app.json").read_text("utf-8"))
    assert a_json["rootAgent"] == "custom_entry"

    gi = (out_dir / "global_instruction.txt").read_text("utf-8")
    assert gi.startswith("HEADER\n# Brand: Brand One")
    assert gi.endswith("FOOTER\n")


def test_build_app_error_cases(tmp_path: Path) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)

    # Disallowed channel for brand
    with pytest.raises(BuildError, match="not permitted"):
        build_app(
            core_dir,
            manifests_dir,
            "brand_2",
            "VOICE_TELEPHONY",
            "prod",
            tmp_path / "out_bad_ch",
        )

    # Missing core dir & overlapping output dir
    with pytest.raises(BuildError, match="does not exist"):
        build_app(
            tmp_path / "no_core",
            manifests_dir,
            "brand_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_no_core",
        )
    with pytest.raises(BuildError, match="must not overlap"):
        build_app(
            core_dir,
            manifests_dir,
            "brand_1",
            "DIGITAL_APP",
            "prod",
            core_dir / "nested_out",
        )

    # Inline modality tag rejection
    (core_dir / "global_instruction.txt").write_text(
        "Inline {@startModality_Voice}bad{@endModality_Voice}.\n",
        encoding="utf-8",
    )
    with pytest.raises(BuildError, match="must be on its own line"):
        build_app(
            core_dir,
            manifests_dir,
            "brand_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_mod",
        )

    # Unresolved placeholder
    (core_dir / "global_instruction.txt").write_text(
        "Missing @@brand.nonexistent@@.\n", encoding="utf-8"
    )
    with pytest.raises(BuildError, match="Unresolved placeholders"):
        build_app(
            core_dir,
            manifests_dir,
            "brand_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_unres",
        )

    # Unbalanced conditional block
    (core_dir / "global_instruction.txt").write_text(
        "{@startBrand_brand_1}\nUnclosed block.\n", encoding="utf-8"
    )
    with pytest.raises(BuildError, match="Unbalanced conditional tags"):
        build_app(
            core_dir,
            manifests_dir,
            "brand_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_cond",
        )


def test_deep_merge_no_mutable_aliasing() -> None:
    target = {
        "keep_list": [1, 2],
        "nested": {"keep": {"a": [10]}, "replace_list": ["old"]},
    }
    overrides = {
        "added_dict": {"x": [100]},
        "nested": {"added": {"b": [20]}, "replace_list": ["new"]},
    }

    merged = deep_merge(target, overrides)
    assert merged == {
        "keep_list": [1, 2],
        "added_dict": {"x": [100]},
        "nested": {
            "keep": {"a": [10]},
            "added": {"b": [20]},
            "replace_list": ["new"],
        },
    }

    # Mutate every mutable container in the returned dict.
    merged["keep_list"].append(999)
    merged["added_dict"]["x"].append(999)
    merged["nested"]["keep"]["a"].append(999)
    merged["nested"]["added"]["b"].append(999)
    merged["nested"]["replace_list"].append("mutated")
    merged["nested"]["extra"] = True

    assert target == {
        "keep_list": [1, 2],
        "nested": {"keep": {"a": [10]}, "replace_list": ["old"]},
    }
    assert overrides == {
        "added_dict": {"x": [100]},
        "nested": {"added": {"b": [20]}, "replace_list": ["new"]},
    }


def test_yaml_token_rendering_special_chars_openapi_and_verbatim_untouched(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)

    tricky_name = 'Acme: "Prime" Bank\nLine 2'
    b1_id_path = manifests_dir / "brands" / "brand_1" / "identity.yaml"
    b1_id = yaml.safe_load(b1_id_path.read_text("utf-8"))
    b1_id["display_name"] = tricky_name
    b1_id_path.write_text(
        yaml.safe_dump(b1_id, sort_keys=False), encoding="utf-8"
    )

    b1_pol_path = manifests_dir / "brands" / "brand_1" / "channel_policy.yaml"
    b1_pol = yaml.safe_load(b1_pol_path.read_text("utf-8"))
    b1_pol["api_routing"]["api_base"] = "https://api.brand1.example.com/v2"
    b1_pol_path.write_text(
        yaml.safe_dump(b1_pol, sort_keys=False), encoding="utf-8"
    )

    # YAML file with token resolving to quotes, colons, and newlines
    (core_dir / "agents" / "default_agent" / "metadata.yaml").write_text(
        'title: "@@brand.display_name@@"\n', encoding="utf-8"
    )

    # OpenAPI schema YAML with quoted routing token
    openapi_dir = core_dir / "tools" / "active_tool" / "open_api_tool"
    openapi_dir.mkdir(parents=True)
    (openapi_dir / "open_api_schema.yaml").write_text(
        'openapi: "3.0.3"\n'
        "info:\n"
        '  title: "@@brand.display_name@@ API"\n'
        "servers:\n"
        '  - url: "@@routing.api_base@@"\n',
        encoding="utf-8",
    )

    # Untouched tokenless YAML with comments and custom spacing must be
    # preserved verbatim (not reformatted by yaml.safe_dump).
    verbatim_yaml = (
        "# Preserved comment\n"
        "openapi:   '3.0.3'\n"
        "info:\n"
        "  title:   Static   Schema\n"
        "  description: Mentions tools in prose only\n"
    )
    (openapi_dir / "static_schema.yaml").write_text(
        verbatim_yaml, encoding="utf-8"
    )

    out_dir = tmp_path / "out_yaml"
    build_app(
        core_dir, manifests_dir, "brand_1", "DIGITAL_APP", "prod", out_dir
    )

    rendered_meta = yaml.safe_load(
        (out_dir / "agents" / "default_agent" / "metadata.yaml").read_text(
            "utf-8"
        )
    )
    assert rendered_meta["title"] == tricky_name

    rendered_openapi = yaml.safe_load(
        (
            out_dir
            / "tools"
            / "active_tool"
            / "open_api_tool"
            / "open_api_schema.yaml"
        ).read_text("utf-8")
    )
    assert rendered_openapi["info"]["title"] == f"{tricky_name} API"
    assert rendered_openapi["servers"] == [
        {"url": "https://api.brand1.example.com/v2"}
    ]

    assert (
        out_dir
        / "tools"
        / "active_tool"
        / "open_api_tool"
        / "static_schema.yaml"
    ).read_text("utf-8") == verbatim_yaml

    # Unquoted @@...@@ in YAML fails loudly with BuildError.
    (openapi_dir / "bad_unquoted.yaml").write_text(
        "url: @@routing.api_base@@\n", encoding="utf-8"
    )
    with pytest.raises(BuildError, match=r"bad_unquoted\.yaml"):
        build_app(
            core_dir,
            manifests_dir,
            "brand_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_yaml_bad",
        )


def test_yaml_pruning_and_app_yaml_settings(tmp_path: Path) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)

    # Replace JSON pruned agent/tool with YAML/YML equivalents to test Step 2.
    (
        core_dir / "agents" / "pruned_agent_dir" / "pruned_agent_dir.json"
    ).unlink()
    (
        core_dir / "agents" / "pruned_agent_dir" / "pruned_agent_dir.yaml"
    ).write_text("displayName: pruned_agent_display\n", encoding="utf-8")

    (core_dir / "tools" / "secret_tool_dir" / "secret_tool_dir.json").unlink()
    (core_dir / "tools" / "secret_tool_dir" / "secret_tool_dir.yml").write_text(
        "displayName: secret_tool_disabled\n", encoding="utf-8"
    )

    # Surviving tokenless YAML agent referencing disabled childAgent & tool.
    yaml_agent_dir = core_dir / "agents" / "yaml_parent"
    yaml_agent_dir.mkdir()
    (yaml_agent_dir / "yaml_parent.yaml").write_text(
        "displayName: yaml_parent\n"
        "childAgents:\n"
        "  - pruned_agent_display\n"
        "  - displayName: pruned_agent_display\n"
        "  - default_agent\n"
        "tools:\n"
        "  - secret_tool_disabled\n"
        "  - active_tool\n",
        encoding="utf-8",
    )

    # Surviving tokenless YAML file with dict-shaped tools map.
    (core_dir / "agents" / "yaml_parent" / "tool_map.yml").write_text(
        "tools:\n"
        "  secret_tool_disabled:\n"
        "    enabled: true\n"
        "  active_tool:\n"
        "    enabled: true\n",
        encoding="utf-8",
    )

    # Replace app.json with tokenless app.yaml pointing to pruned rootAgent.
    (core_dir / "app.json").unlink()
    (core_dir / "app.yaml").write_text(
        "rootAgent: pruned_agent_display\n", encoding="utf-8"
    )

    out_dir = tmp_path / "out_yaml_prune"
    build_app(
        core_dir, manifests_dir, "brand_1", "VOICE_TELEPHONY", "prod", out_dir
    )

    assert not (out_dir / "agents" / "pruned_agent_dir").exists()
    assert not (out_dir / "tools" / "secret_tool_dir").exists()

    parent_cfg = yaml.safe_load(
        (out_dir / "agents" / "yaml_parent" / "yaml_parent.yaml").read_text(
            "utf-8"
        )
    )
    assert parent_cfg["childAgents"] == ["default_agent"]
    assert parent_cfg["tools"] == ["active_tool"]

    tool_map = yaml.safe_load(
        (out_dir / "agents" / "yaml_parent" / "tool_map.yml").read_text("utf-8")
    )
    assert tool_map["tools"] == {"active_tool": {"enabled": True}}

    app_cfg = yaml.safe_load((out_dir / "app.yaml").read_text("utf-8"))
    assert app_cfg["rootAgent"] == "custom_entry"
    assert (
        app_cfg["audioProcessingConfig"]["synthesizeSpeechConfigs"]["en"][
            "voice"
        ]
        == "en-US-Standard-A"
    )
    assert app_cfg["languageSettings"]["defaultLanguageCode"] == "en"
    assert app_cfg["languageSettings"]["supportedLanguageCodes"] == ["en", "es"]
    assert app_cfg["languageSettings"]["enableMultilingualSupport"] is True


def test_entry_agent_validation_and_root_agent_alignment(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    pol_path = manifests_dir / "brands" / "brand_1" / "channel_policy.yaml"

    # Missing entry_agent fails fast instead of emitting a broken rootAgent.
    pol = yaml.safe_load(pol_path.read_text("utf-8"))
    pol["channels"]["VOICE_TELEPHONY"]["entry_agent"] = "nonexistent_agent"
    pol_path.write_text(yaml.safe_dump(pol, sort_keys=False), encoding="utf-8")
    with pytest.raises(BuildError, match="does not match any surviving agent"):
        build_app(
            core_dir,
            manifests_dir,
            "brand_1",
            "VOICE_TELEPHONY",
            "prod",
            tmp_path / "out_missing_entry",
        )

    # Disabled entry_agent fails fast.
    pol["channels"]["VOICE_TELEPHONY"]["entry_agent"] = "pruned_agent_display"
    pol_path.write_text(yaml.safe_dump(pol, sort_keys=False), encoding="utf-8")
    with pytest.raises(BuildError, match="cannot also be in disabled_agents"):
        build_app(
            core_dir,
            manifests_dir,
            "brand_1",
            "VOICE_TELEPHONY",
            "prod",
            tmp_path / "out_disabled_entry",
        )

    # Full CES resource-path entry_agent matching a surviving agent succeeds
    # and syncs both app.json rootAgent and deployments/<channel>.yaml.
    full_entry = "projects/p/locations/l/apps/a/agents/custom_entry"
    pol["channels"]["VOICE_TELEPHONY"]["entry_agent"] = full_entry
    pol_path.write_text(yaml.safe_dump(pol, sort_keys=False), encoding="utf-8")
    out_full = tmp_path / "out_full_entry"
    build_app(
        core_dir, manifests_dir, "brand_1", "VOICE_TELEPHONY", "prod", out_full
    )
    app_cfg = json.loads((out_full / "app.json").read_text("utf-8"))
    dep_cfg = yaml.safe_load(
        (out_full / "deployments" / "voice-telephony.yaml").read_text("utf-8")
    )
    assert app_cfg["rootAgent"] == full_entry
    assert dep_cfg["entryAgent"] == full_entry

    # Pruned rootAgent (including resource-path form) without entry_agent fails.
    pol["channels"]["VOICE_TELEPHONY"].pop("entry_agent")
    pol_path.write_text(yaml.safe_dump(pol, sort_keys=False), encoding="utf-8")
    for pruned_ref in (
        "pruned_agent_dir",
        "agents/pruned_agent_dir",
        "projects/p/locations/l/apps/a/agents/pruned_agent_dir",
    ):
        (core_dir / "app.json").write_text(
            json.dumps({"rootAgent": pruned_ref}), encoding="utf-8"
        )
        with pytest.raises(BuildError, match=r"(?i)rootAgent|entry_agent"):
            build_app(
                core_dir,
                manifests_dir,
                "brand_1",
                "VOICE_TELEPHONY",
                "prod",
                tmp_path / "out_pruned_root",
            )


def test_nested_and_full_resource_path_pruning_across_json_and_yaml(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)

    (core_dir / "agents" / "default_agent" / "default_agent.json").write_text(
        json.dumps(
            {
                "displayName": "default_agent",
                "childAgents": [
                    "agents/pruned_agent_dir",
                    "projects/p/locations/l/apps/a/agents/pruned_agent_dir",
                    {
                        "name": "projects/p/locations/l/apps/a/agents/pruned_agent_dir"
                    },
                    {"displayName": "pruned_agent_display"},
                    "custom_entry",
                ],
                "tools": [
                    "tools/secret_tool_dir",
                    "projects/p/locations/l/apps/a/tools/secret_tool_dir",
                    {"name": "tools/secret_tool_dir"},
                    "active_tool",
                ],
                "nested": {
                    "items": [
                        {
                            "childAgents": [
                                "agents/pruned_agent_dir",
                                "custom_entry",
                            ],
                            "tools": {
                                "projects/p/locations/l/apps/a/tools/secret_tool_dir": {
                                    "enabled": True
                                },
                                "active_tool": {"enabled": True},
                            },
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (core_dir / "agents" / "default_agent" / "nested_refs.yaml").write_text(
        "wrapper:\n"
        "  childAgents:\n"
        "    - projects/p/locations/l/apps/a/agents/pruned_agent_dir\n"
        "    - name: agents/pruned_agent_dir\n"
        "    - custom_entry\n"
        "  tools:\n"
        "    - tools/secret_tool_dir\n"
        "    - active_tool\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_nested_prune"
    build_app(
        core_dir, manifests_dir, "brand_1", "VOICE_TELEPHONY", "prod", out_dir
    )

    agent_json = json.loads(
        (out_dir / "agents" / "default_agent" / "default_agent.json").read_text(
            "utf-8"
        )
    )
    assert agent_json["childAgents"] == ["custom_entry"]
    assert agent_json["tools"] == ["active_tool"]
    nested_item = agent_json["nested"]["items"][0]
    assert nested_item["childAgents"] == ["custom_entry"]
    assert nested_item["tools"] == {"active_tool": {"enabled": True}}

    nested_yaml = yaml.safe_load(
        (out_dir / "agents" / "default_agent" / "nested_refs.yaml").read_text(
            "utf-8"
        )
    )
    assert nested_yaml["wrapper"]["childAgents"] == ["custom_entry"]
    assert nested_yaml["wrapper"]["tools"] == ["active_tool"]


def test_dict_key_placeholder_substitution_and_fail_fast(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    (core_dir / "agents" / "default_agent" / "key_tokens.json").write_text(
        json.dumps({"@@brand.brand_id@@_key": "@@brand.display_name@@"}),
        encoding="utf-8",
    )
    (core_dir / "agents" / "default_agent" / "key_tokens.yaml").write_text(
        '"@@brand.brand_id@@_yaml": "@@brand.display_name@@"\n200:\n  ok: true\n',
        encoding="utf-8",
    )

    out_ok = tmp_path / "out_key_ok"
    build_app(core_dir, manifests_dir, "brand_1", "DIGITAL_APP", "prod", out_ok)
    rendered_json = json.loads(
        (out_ok / "agents" / "default_agent" / "key_tokens.json").read_text(
            "utf-8"
        )
    )
    assert rendered_json == {"brand_1_key": "Brand One"}
    rendered_yaml = yaml.safe_load(
        (out_ok / "agents" / "default_agent" / "key_tokens.yaml").read_text(
            "utf-8"
        )
    )
    assert rendered_yaml["brand_1_yaml"] == "Brand One"
    assert rendered_yaml[200] == {"ok": True}

    (core_dir / "agents" / "default_agent" / "key_tokens.json").write_text(
        json.dumps({"@@unknown.key@@": "x"}), encoding="utf-8"
    )
    with pytest.raises(BuildError, match="Unresolved placeholders"):
        build_app(
            core_dir,
            manifests_dir,
            "brand_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_key_bad",
        )


def test_atomic_build_failure_preserves_previous_out_dir_and_zip(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    out_dir = tmp_path / "atomic_out"

    first_report = build_app(
        core_dir,
        manifests_dir,
        "brand_1",
        "DIGITAL_APP",
        "prod",
        out_dir,
        package_zip=True,
    )
    assert first_report.zip_path is not None
    good_zip_bytes = first_report.zip_path.read_bytes()
    good_gi = (out_dir / "global_instruction.txt").read_text("utf-8")

    # Introduce a build error and rebuild into the same out_dir.
    (core_dir / "global_instruction.txt").write_text(
        "Broken @@brand.missing_field@@\n", encoding="utf-8"
    )
    with pytest.raises(BuildError, match="Unresolved placeholders"):
        build_app(
            core_dir,
            manifests_dir,
            "brand_1",
            "DIGITAL_APP",
            "prod",
            out_dir,
            package_zip=True,
        )

    assert (out_dir / "global_instruction.txt").read_text("utf-8") == good_gi
    assert first_report.zip_path.read_bytes() == good_zip_bytes
    leftover_tmps = [
        p.name for p in out_dir.parent.iterdir() if ".tmp-" in p.name
    ]
    assert leftover_tmps == []


def test_symlinks_rejected_in_core_and_manifests(tmp_path: Path) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    # Symlink file inside core_dir
    core_link = core_dir / "leak.txt"
    core_link.symlink_to(outside)
    with pytest.raises(BuildError, match="Symlinks are not permitted"):
        build_app(
            core_dir,
            manifests_dir,
            "brand_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_core_link",
        )
    core_link.unlink()

    # Symlinked core_dir itself
    core_root_link = tmp_path / "core_link"
    core_root_link.symlink_to(core_dir)
    with pytest.raises(BuildError, match="Symlinks are not permitted"):
        build_app(
            core_root_link,
            manifests_dir,
            "brand_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_core_root_link",
        )

    # Symlink file inside manifests_dir
    man_link = manifests_dir / "brands" / "brand_1" / "extra.yaml"
    man_link.symlink_to(outside)
    with pytest.raises(BuildError, match="Symlinks are not permitted"):
        build_app(
            core_dir,
            manifests_dir,
            "brand_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_man_link",
        )
    man_link.unlink()

    # Symlinked manifests_dir itself
    man_root_link = tmp_path / "manifests_link"
    man_root_link.symlink_to(manifests_dir)
    with pytest.raises(BuildError, match="Symlinks are not permitted"):
        build_app(
            core_dir,
            man_root_link,
            "brand_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_man_root_link",
        )


def test_app_yml_end_to_end_build_and_zip(tmp_path: Path) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    (core_dir / "app.json").unlink()
    (core_dir / "app.yml").write_text(
        'displayName: "@@brand.display_name@@"\nrootAgent: default_agent\n',
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_app_yml"
    report = build_app(
        core_dir,
        manifests_dir,
        "brand_1",
        "VOICE_TELEPHONY",
        "prod",
        out_dir,
        package_zip=True,
    )
    assert report.zip_path is not None
    with zipfile.ZipFile(report.zip_path, "r") as zf:
        assert "app.yml" in zf.namelist()
        staged_app = yaml.safe_load(zf.read("app.yml").decode("utf-8"))
    assert staged_app["displayName"] == "Brand One"
    assert staged_app["rootAgent"] == "custom_entry"
    assert staged_app["languageSettings"]["supportedLanguageCodes"] == [
        "en",
        "es",
    ]


def test_text_only_brand_explicit_languages_and_yaml12_country_no(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    b3 = manifests_dir / "brands" / "brand_no"
    b3.mkdir(parents=True)
    (b3 / "identity.yaml").write_text(
        "brand_id: brand_no\n"
        "display_name: Nordic Text Brand\n"
        "languages: [en, no]\n"
        "lexicon:\n"
        "  account: Konto\n"
        "urls:\n"
        "  support: https://support.nordic.example.com\n",
        encoding="utf-8",
    )
    (b3 / "channel_policy.yaml").write_text(
        "brand_id: brand_no\n"
        "supported_countries: [NO]\n"
        "api_routing:\n"
        "  core_api: https://core.nordic.example.com\n"
        "channels:\n"
        "  DIGITAL_APP:\n"
        "    supported_modalities: [TEXT]\n",
        encoding="utf-8",
    )
    (manifests_dir / "locales" / "content_blocks.yaml").write_text(
        "blocks:\n"
        "  brand_no:\n"
        "    NO:\n"
        "      no:\n"
        "        disclosure: Norsk informasjon.\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_nordic"
    build_app(
        core_dir, manifests_dir, "brand_no", "DIGITAL_APP", "prod", out_dir
    )

    app_cfg = json.loads((out_dir / "app.json").read_text("utf-8"))
    assert app_cfg["languageSettings"] == {
        "defaultLanguageCode": "en",
        "supportedLanguageCodes": ["en", "no"],
        "enableMultilingualSupport": True,
    }
    assert "audioProcessingConfig" not in app_cfg

    dep_cfg = yaml.safe_load(
        (out_dir / "deployments" / "digital-app.yaml").read_text("utf-8")
    )
    assert dep_cfg["supportedCountries"] == ["NO"]
    assert dep_cfg["supportedLanguages"] == ["en", "no"]

    gi = (out_dir / "global_instruction.txt").read_text("utf-8")
    assert "[NO-no] disclosure: Norsk informasjon." in gi


def test_json_unicode_escaping_and_binary_assets_preserved(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    (
        core_dir
        / "tools"
        / "active_tool"
        / "python_function"
        / "python_code.py"
    ).write_text("def active_tool() -> str:\n    return 'ok'\n", "utf-8")

    tricky_display = 'Acme "Prime" \\ Bank äöüß€\nLine 2'
    tricky_legal = 'Acme "Legal" GmbH & Co. KG \\ €'
    tricky_lex = 'Giro "Konto" \\ äöüß€'
    (manifests_dir / "brands" / "brand_1" / "identity.yaml").write_text(
        yaml.safe_dump(
            {
                "brand_id": "brand_1",
                "display_name": tricky_display,
                "legal_entity": tricky_legal,
                "lexicon": {"account": tricky_lex},
                "urls": {"support": "https://example.de/hilfe?q=äöü&a=1"},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe\x80\x81"
    (core_dir / "logo.png").write_bytes(png_bytes)
    (core_dir / "app.json").write_text(
        json.dumps(
            {
                "displayName": "@@brand.display_name@@",
                "rootAgent": "default_agent",
                "audioProcessingConfig": None,
                "languageSettings": None,
            }
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_unicode_bin"
    build_app(
        core_dir, manifests_dir, "brand_1", "DIGITAL_APP", "prod", out_dir
    )

    app_data = json.loads((out_dir / "app.json").read_text("utf-8"))
    assert app_data["displayName"] == tricky_display
    assert (out_dir / "logo.png").read_bytes() == png_bytes


@pytest.mark.parametrize(
    ("content", "expected_err"),
    [
        ("Broken @@env.missing@@\n", "Unresolved placeholders"),
        ("Broken @@routing.bad@@\n", "Unresolved placeholders"),
        (
            "Endpoint: @@env.api:users\nWelcome to @@brand.display_name@@\n",
            "Unresolved or unclosed placeholders",
        ),
        ("{@endChannel_DIGITAL_APP}\n", "Unbalanced or mismatched conditional"),
        (
            "{@startChannel_DIGITAL_APP}\n{@endBrand_brand_1}\n",
            "Unbalanced or mismatched conditional",
        ),
        (
            "{@startChannel_DIGITAL_APP}A{@startBrand_brand_1}B"
            "{@endChannel_DIGITAL_APP}C{@endBrand_brand_1}\n",
            "Unbalanced or overlapping inline",
        ),
        (
            "{@startChannel_API}\n"
            "Broken {@startBrand_brand_1}unclosed inline\n"
            "{@endChannel_API}\n",
            "Unbalanced inline conditional",
        ),
        (
            "{@startModality_Voice}\nUnclosed modality\n",
            "Unbalanced modality tags",
        ),
        (
            "{@startModality_Voice}\n{@endModality_Text}\n",
            "Unbalanced or mismatched modality",
        ),
    ],
)
def test_malformed_tokens_conditionals_and_modalities_fail_fast(
    tmp_path: Path, content: str, expected_err: str
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    (core_dir / "global_instruction.txt").write_text(content, encoding="utf-8")
    with pytest.raises(BuildError, match=expected_err):
        build_app(
            core_dir,
            manifests_dir,
            "brand_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_malformed",
        )


def test_traversal_overlap_malformed_json_and_bad_env_templates(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)

    with pytest.raises(ManifestError, match="Unknown brand"):
        build_app(
            core_dir,
            manifests_dir,
            "../../escape",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_escape",
        )

    # out_dir overlapping manifests_dir must not wipe manifests_dir
    with pytest.raises(BuildError, match="overlap"):
        build_app(
            core_dir,
            manifests_dir,
            "brand_1",
            "DIGITAL_APP",
            "prod",
            manifests_dir,
        )
    assert (manifests_dir / "brands" / "brand_1" / "identity.yaml").is_file()

    # Unclosed brace and format-string attribute traversal in env template
    for env_name, tpl in (
        ("bad_tpl", "https://ex/{brand"),
        ("gadget", "{brand.__class__.__name__}"),
    ):
        (manifests_dir / "envs" / f"{env_name}.yaml").write_text(
            f"environment: {env_name}\ntemplates:\n  api: '{tpl}'\n",
            encoding="utf-8",
        )
        with pytest.raises(BuildError):
            build_app(
                core_dir,
                manifests_dir,
                "brand_1",
                "DIGITAL_APP",
                env_name,
                tmp_path / f"out_{env_name}",
            )

    # Malformed JSON in core raises BuildError
    (core_dir / "app.json").write_text(
        '{"displayName": "@@brand.display_name@@",}', encoding="utf-8"
    )
    with pytest.raises(BuildError, match=r"(?i)json"):
        build_app(
            core_dir,
            manifests_dir,
            "brand_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_bad_json",
        )


def test_env_overrides_without_base_and_no_disabled_tool_reinjection(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    (core_dir / "environment.json").unlink()
    out1 = tmp_path / "out_no_base_env"
    build_app(core_dir, manifests_dir, "brand_1", "DIGITAL_APP", "prod", out1)
    assert json.loads((out1 / "environment.json").read_text("utf-8")) == {
        "overridden_key": "prod_val",
        "nested": {"replace": 2},
    }

    (manifests_dir / "envs" / "prod.yaml").write_text(
        yaml.safe_dump(
            {
                "environment": "prod",
                "templates": {
                    "api": "https://api.example.com/{brand_slug}/{env}/{service}"
                },
                "overrides": {
                    "tools": {
                        "secret_tool_disabled": {
                            "endpoint": "https://leaked.local"
                        },
                        "active_tool": {"endpoint": "https://active.local"},
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    out2 = tmp_path / "out_no_reinject"
    build_app(
        core_dir, manifests_dir, "brand_1", "VOICE_TELEPHONY", "prod", out2
    )
    env_out = json.loads((out2 / "environment.json").read_text("utf-8"))
    assert "secret_tool_disabled" not in env_out.get("tools", {})
    assert "active_tool" in env_out.get("tools", {})


def test_python_token_escaping_fstrings_and_triple_quotes(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    py_path = (
        core_dir
        / "tools"
        / "active_tool"
        / "python_function"
        / "python_code.py"
    )
    id_path = manifests_dir / "brands" / "brand_1" / "identity.yaml"

    # 1. Quotes, backslashes, and newlines inside standard string literal
    id_data = yaml.safe_load(id_path.read_text("utf-8"))
    id_data["display_name"] = 'Acme "Prime" \\ Bank\nLine2'
    id_path.write_text(yaml.safe_dump(id_data, sort_keys=False), "utf-8")
    out1 = tmp_path / "out_py_quotes"
    build_app(core_dir, manifests_dir, "brand_1", "DIGITAL_APP", "prod", out1)
    rendered1 = (
        out1 / "tools" / "active_tool" / "python_function" / "python_code.py"
    ).read_text("utf-8")
    ast.parse(rendered1)

    # Verify f-strings escape braces without injection and keep raw backslashes.
    id_data["display_name"] = "Acme {1 + 1} Bank"
    id_path.write_text(yaml.safe_dump(id_data, sort_keys=False), "utf-8")
    py_path.write_text(
        "def active_tool(user: str) -> tuple[str, str]:\n"
        '    msg = f"Line1\\nWelcome to @@brand.display_name@@, {user}"\n'
        '    raw_path = rf"C:\\dir\\@@brand.brand_id@@\\{user}"\n'
        "    return msg, raw_path\n",
        encoding="utf-8",
    )
    out2 = tmp_path / "out_fstring"
    build_app(core_dir, manifests_dir, "brand_1", "DIGITAL_APP", "prod", out2)
    rendered2 = (
        out2 / "tools" / "active_tool" / "python_function" / "python_code.py"
    ).read_text("utf-8")
    ns2: dict[str, object] = {}
    exec(compile(rendered2, str(py_path), "exec"), ns2)
    fn2 = ns2["active_tool"]
    assert callable(fn2)
    assert fn2("Alice") == (
        "Line1\nWelcome to Acme {1 + 1} Bank, Alice",
        r"C:\dir\brand_1\Alice",
    )

    # 3. Triple-quoted string ending in triple quotes stays valid
    brand_val = 'Acme """'
    id_data["display_name"] = brand_val
    id_path.write_text(yaml.safe_dump(id_data, sort_keys=False), "utf-8")
    py_path.write_text(
        'def active_tool() -> str:\n    return """@@brand.display_name@@"""\n',
        encoding="utf-8",
    )
    out3 = tmp_path / "out_triple_q"
    build_app(core_dir, manifests_dir, "brand_1", "DIGITAL_APP", "prod", out3)
    rendered3 = (
        out3 / "tools" / "active_tool" / "python_function" / "python_code.py"
    ).read_text("utf-8")
    ns3: dict[str, object] = {}
    exec(compile(rendered3, str(py_path), "exec"), ns3)
    fn3 = ns3["active_tool"]
    assert callable(fn3)
    assert fn3() == brand_val

    # 4. Package determinism across mtimes + junk exclusions
    os.utime(core_dir / "app.json", (1_700_000_000, 1_700_000_000))
    rep_a = build_app(
        core_dir,
        manifests_dir,
        "brand_1",
        "DIGITAL_APP",
        "prod",
        tmp_path / "det_a",
        package_zip=True,
    )
    os.utime(core_dir / "app.json", (1_750_000_000, 1_750_000_000))
    rep_b = build_app(
        core_dir,
        manifests_dir,
        "brand_1",
        "DIGITAL_APP",
        "prod",
        tmp_path / "det_b",
        package_zip=True,
    )
    assert rep_a.zip_path is not None
    assert rep_b.zip_path is not None
    assert rep_a.zip_path.read_bytes() == rep_b.zip_path.read_bytes()
