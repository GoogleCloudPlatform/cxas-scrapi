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

import filecmp
import hashlib
import json
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

    # app.json checks: unpruned rootAgent preserved, TTS & multilingual set
    a_json = json.loads((b1_voice / "app.json").read_text("utf-8"))
    assert a_json["rootAgent"] == "default_agent"
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
