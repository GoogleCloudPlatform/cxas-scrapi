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

"""Unit tests for Profile-Based Build (`cxas_scrapi.build.builder`)."""

from __future__ import annotations

import ast
import filecmp
import hashlib
import json
import os
import shutil
import zipfile
from typing import TYPE_CHECKING

import pytest
import yaml

from cxas_scrapi.build.builder import (
    BuildError,
    build_all,
    build_app,
    deep_merge,
    merge_app_config,
)
from cxas_scrapi.build.manifests import ManifestError

if TYPE_CHECKING:
    from pathlib import Path


def _setup_sample_workspace(tmp_path: Path) -> tuple[Path, Path]:
    core_dir = tmp_path / "core"
    core_dir.mkdir()

    app_json = {
        "displayName": "Sample App",
        "rootAgent": "default_agent",
        "variableDeclarations": [
            {"name": "var1", "schema": {"type": "STRING"}}
        ],
    }
    (core_dir / "app.json").write_text(json.dumps(app_json), encoding="utf-8")
    (core_dir / "environment.json").write_text(
        json.dumps({"some_key": "val1", "nested": {"keep": 1, "replace": 0}}),
        encoding="utf-8",
    )
    (core_dir / "global_instruction.txt").write_text(
        "Instruction for @@profile.display_name@@ (@@build.channel@@/"
        "@@build.environment@@). URL: @@env.api:auth@@ "
        "Support: @@profile.urls.support@@ Routing: @@routing.core_api@@\n",
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
        'def active_tool() -> str:\n    return "Welcome to @@profile.display_name@@"\n',
        encoding="utf-8",
    )

    gr_dir = core_dir / "guardrails" / "profile_guardrail"
    gr_dir.mkdir(parents=True)
    (gr_dir / "profile_guardrail.json").write_text(
        json.dumps(
            {
                "llmPolicy": {
                    "prompt": "Guardrail for @@profile.display_name@@ "
                    "(@@profile.lexicon.account@@)."
                }
            }
        ),
        encoding="utf-8",
    )

    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()

    p1 = manifests_dir / "profiles" / "profile_1"
    p1.mkdir(parents=True)
    (p1 / "identity.yaml").write_text(
        "profile_id: profile_1\n"
        "display_name: Profile One\n"
        "assistant_title: Profile One Assistant\n"
        "legal_entity: Profile One Corp\n"
        "tone_guidelines: Helpful and concise.\n"
        "lexicon:\n"
        "  account: Checking Account\n"
        "urls:\n"
        "  support: https://support.profile1.example.com\n"
        "tts_voices:\n"
        "  en:\n"
        "    voice: en-US-Standard-A\n"
        "    speaking_rate: 1.0\n"
        "  es:\n"
        "    voice: es-ES-Standard-A\n"
        "    speaking_rate: 1.1\n",
        encoding="utf-8",
    )
    (p1 / "channel_policy.yaml").write_text(
        "profile_id: profile_1\n"
        "supported_countries: ['US']\n"
        "api_routing:\n"
        "  core_api: https://core.profile1.example.com\n"
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

    p2 = manifests_dir / "profiles" / "profile_2"
    p2.mkdir(parents=True)
    (p2 / "identity.yaml").write_text(
        "profile_id: profile_2\n"
        "display_name: Profile Two\n"
        "lexicon:\n"
        "  account: Savings Account\n"
        "urls:\n"
        "  support: https://support.profile2.example.com\n",
        encoding="utf-8",
    )
    (p2 / "channel_policy.yaml").write_text(
        "profile_id: profile_2\n"
        "api_routing:\n"
        "  core_api: https://core.profile2.example.com\n"
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
        "  api: https://api.example.com/{profile_slug}/{env}/{service}\n"
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
        "  profile_1:\n"
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

    p1_voice = out_root1 / "profile_1" / "voice_telephony"
    assert p1_voice.exists()
    assert not (p1_voice / "agents" / "pruned_agent_dir").exists()
    assert not (p1_voice / "tools" / "secret_tool_dir").exists()
    assert (p1_voice / "tools" / "active_tool").exists()

    # app.json checks: configured entry_agent aligns rootAgent with entryAgent
    a_json = json.loads((p1_voice / "app.json").read_text("utf-8"))
    assert a_json["rootAgent"] == "custom_entry"
    p1_digital = out_root1 / "profile_1" / "digital_app"
    digital_app = json.loads((p1_digital / "app.json").read_text("utf-8"))
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
            p1_voice / "agents" / "default_agent" / "default_agent.json"
        ).read_text("utf-8")
    )
    assert d_json["childAgents"] == []
    assert d_json["tools"] == ["active_tool"]

    # environment.json deep merged
    e_json = json.loads((p1_voice / "environment.json").read_text("utf-8"))
    assert e_json["some_key"] == "val1"
    assert e_json["overridden_key"] == "prod_val"
    assert e_json["nested"] == {"keep": 1, "replace": 2}

    # Guardrail JSON token replacement
    g_json = json.loads(
        (
            p1_voice
            / "guardrails"
            / "profile_guardrail"
            / "profile_guardrail.json"
        ).read_text("utf-8")
    )
    assert (
        g_json["llmPolicy"]["prompt"]
        == "Guardrail for Profile One (Checking Account)."
    )

    # Global instruction identity block + tokens + locale content block
    gi = (p1_voice / "global_instruction.txt").read_text("utf-8")
    assert "# Profile: Profile One" in gi
    assert "[US-en] disclosure: Regulated disclosure." in gi
    assert "URL: https://api.example.com/profile-1/prod/auth" in gi
    assert "Support: https://support.profile1.example.com" in gi
    assert "Routing: https://core.profile1.example.com" in gi

    # Deployment YAML emitted
    dep_yaml = yaml.safe_load(
        (p1_voice / "deployments" / "voice-telephony.yaml").read_text("utf-8")
    )
    assert dep_yaml["profileId"] == "profile_1"
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
        "Voice mode for @@profile.display_name@@.\n"
        "{@startAgentEnabled_pruned_agent_display}\n"
        "Route to {@AGENT: pruned_agent_display}.\n"
        "{@endAgentEnabled_pruned_agent_display}\n"
        "{@startProfile_profile_1}\n"
        "Profile One voice fallback.\n"
        "{@endProfile_profile_1}\n"
        "{@endChannel_VOICE_TELEPHONY}\n"
        "{@startChannel_DIGITAL_APP}\n"
        "Digital mode for @@profile.display_name@@.\n"
        "{@startToolEnabled_secret_tool_disabled}\n"
        "Call {@TOOL: secret_tool_disabled}.\n"
        "{@endToolEnabled_secret_tool_disabled}\n"
        "{@endChannel_DIGITAL_APP}\n"
        "Inline: {@startProfile_profile_1}P1{@endProfile_profile_1}"
        "{@startProfile_profile_2}P2{@endProfile_profile_2}\n",
        encoding="utf-8",
    )

    out_voice = tmp_path / "out_voice"
    rep_voice = build_app(
        core_dir,
        manifests_dir,
        "profile_1",
        "VOICE_TELEPHONY",
        "prod",
        out_voice,
    )
    assert not rep_voice.warnings
    built_voice = (
        out_voice / "agents" / "default_agent" / "instruction.txt"
    ).read_text("utf-8")
    assert "Voice mode for Profile One." in built_voice
    assert "Profile One voice fallback." in built_voice
    assert "Inline: P1" in built_voice
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
    assert 'return "Welcome to Profile One"' in built_py

    out_digital = tmp_path / "out_digital"
    rep_digital = build_app(
        core_dir, manifests_dir, "profile_2", "DIGITAL_APP", "prod", out_digital
    )
    assert not rep_digital.warnings
    built_digital = (
        out_digital / "agents" / "default_agent" / "instruction.txt"
    ).read_text("utf-8")
    assert "Digital mode for Profile Two." in built_digital
    assert "Call {@TOOL: secret_tool_disabled}." in built_digital
    assert "Inline: P2" in built_digital
    assert "Voice mode" not in built_digital


def test_root_agent_fallback_when_pruned_and_explicit_identity_block(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    (core_dir / "app.json").write_text(
        json.dumps(
            {"displayName": "Fallback App", "rootAgent": "pruned_agent_display"}
        ),
        encoding="utf-8",
    )
    (core_dir / "global_instruction.txt").write_text(
        "HEADER\n@@profile.identity_block@@\nFOOTER\n", encoding="utf-8"
    )

    out_dir = tmp_path / "out_root_fallback"
    build_app(
        core_dir, manifests_dir, "profile_1", "VOICE_TELEPHONY", "prod", out_dir
    )
    a_json = json.loads((out_dir / "app.json").read_text("utf-8"))
    assert a_json["rootAgent"] == "custom_entry"

    gi = (out_dir / "global_instruction.txt").read_text("utf-8")
    assert gi.startswith("HEADER\n# Profile: Profile One")
    assert gi.endswith("FOOTER\n")


def test_build_app_error_cases(tmp_path: Path) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)

    # Disallowed channel for profile
    with pytest.raises(BuildError, match="not permitted"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_2",
            "VOICE_TELEPHONY",
            "prod",
            tmp_path / "out_bad_ch",
        )

    # Missing core dir & overlapping output dir
    with pytest.raises(BuildError, match="does not exist"):
        build_app(
            tmp_path / "no_core",
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_no_core",
        )
    with pytest.raises(BuildError, match="must not overlap"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
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
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_mod",
        )

    # Unresolved placeholder
    (core_dir / "global_instruction.txt").write_text(
        "Missing @@profile.nonexistent@@.\n", encoding="utf-8"
    )
    with pytest.raises(BuildError, match="Unresolved placeholders"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_unres",
        )

    # Unbalanced conditional block
    (core_dir / "global_instruction.txt").write_text(
        "{@startProfile_profile_1}\nUnclosed block.\n", encoding="utf-8"
    )
    with pytest.raises(BuildError, match="Unbalanced conditional tags"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
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
    p1_id_path = manifests_dir / "profiles" / "profile_1" / "identity.yaml"
    p1_id = yaml.safe_load(p1_id_path.read_text("utf-8"))
    p1_id["display_name"] = tricky_name
    p1_id_path.write_text(
        yaml.safe_dump(p1_id, sort_keys=False), encoding="utf-8"
    )

    p1_pol_path = (
        manifests_dir / "profiles" / "profile_1" / "channel_policy.yaml"
    )
    p1_pol = yaml.safe_load(p1_pol_path.read_text("utf-8"))
    p1_pol["api_routing"]["api_base"] = "https://api.profile1.example.com/v2"
    p1_pol_path.write_text(
        yaml.safe_dump(p1_pol, sort_keys=False), encoding="utf-8"
    )

    # YAML file with token resolving to quotes, colons, and newlines
    (core_dir / "agents" / "default_agent" / "metadata.yaml").write_text(
        'title: "@@profile.display_name@@"\n', encoding="utf-8"
    )

    # OpenAPI schema YAML with quoted routing token
    openapi_dir = core_dir / "tools" / "active_tool" / "open_api_tool"
    openapi_dir.mkdir(parents=True)
    (openapi_dir / "open_api_schema.yaml").write_text(
        'openapi: "3.0.3"\n'
        "info:\n"
        '  title: "@@profile.display_name@@ API"\n'
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
        core_dir, manifests_dir, "profile_1", "DIGITAL_APP", "prod", out_dir
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
        {"url": "https://api.profile1.example.com/v2"}
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
            "profile_1",
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

    # Surviving tokenless YAML agent with dict-shaped tools map (.yml config).
    yaml_map_dir = core_dir / "agents" / "yaml_map_agent"
    yaml_map_dir.mkdir()
    (yaml_map_dir / "yaml_map_agent.yml").write_text(
        "displayName: yaml_map_agent\n"
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
        "displayName: YAML App\nrootAgent: pruned_agent_display\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_yaml_prune"
    build_app(
        core_dir, manifests_dir, "profile_1", "VOICE_TELEPHONY", "prod", out_dir
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
        (
            out_dir / "agents" / "yaml_map_agent" / "yaml_map_agent.yml"
        ).read_text("utf-8")
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
    pol_path = manifests_dir / "profiles" / "profile_1" / "channel_policy.yaml"

    # Missing entry_agent fails fast instead of emitting a broken rootAgent.
    pol = yaml.safe_load(pol_path.read_text("utf-8"))
    pol["channels"]["VOICE_TELEPHONY"]["entry_agent"] = "nonexistent_agent"
    pol_path.write_text(yaml.safe_dump(pol, sort_keys=False), encoding="utf-8")
    with pytest.raises(BuildError, match="does not match any surviving agent"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
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
            "profile_1",
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
        core_dir,
        manifests_dir,
        "profile_1",
        "VOICE_TELEPHONY",
        "prod",
        out_full,
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
                "profile_1",
                "VOICE_TELEPHONY",
                "prod",
                tmp_path / "out_pruned_root",
            )


def test_scoped_resource_path_pruning_and_openapi_non_mutation(
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
                "metadata": {
                    "tools": ["secret_tool_disabled", "active_tool"],
                    "childAgents": ["agents/pruned_agent_dir"],
                },
            }
        ),
        encoding="utf-8",
    )
    yaml_agent = core_dir / "agents" / "yaml_agent"
    yaml_agent.mkdir()
    (yaml_agent / "yaml_agent.yaml").write_text(
        "displayName: yaml_agent\n"
        "childAgents:\n"
        "  - projects/p/locations/l/apps/a/agents/pruned_agent_dir\n"
        "  - name: agents/pruned_agent_dir\n"
        "  - custom_entry\n"
        "tools:\n"
        "  - tools/secret_tool_dir\n"
        "  - active_tool\n",
        encoding="utf-8",
    )

    # OpenAPI schemas under tools/ must NEVER have `tools` or `childAgents`
    # mutated, even when tokens in the same file are substituted.
    openapi_dir = core_dir / "tools" / "active_tool" / "open_api_tool"
    openapi_dir.mkdir(parents=True)
    (openapi_dir / "open_api_schema.yaml").write_text(
        'openapi: "3.0.3"\n'
        "info:\n"
        '  title: "@@profile.display_name@@ API"\n'
        "tools:\n"
        "  - active_tool\n"
        "  - secret_tool_disabled\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_scoped_prune"
    build_app(
        core_dir, manifests_dir, "profile_1", "VOICE_TELEPHONY", "prod", out_dir
    )

    agent_json = json.loads(
        (out_dir / "agents" / "default_agent" / "default_agent.json").read_text(
            "utf-8"
        )
    )
    assert agent_json["childAgents"] == ["custom_entry"]
    assert agent_json["tools"] == ["active_tool"]
    assert agent_json["metadata"] == {
        "tools": ["secret_tool_disabled", "active_tool"],
        "childAgents": ["agents/pruned_agent_dir"],
    }

    rendered_yaml_agent = yaml.safe_load(
        (out_dir / "agents" / "yaml_agent" / "yaml_agent.yaml").read_text(
            "utf-8"
        )
    )
    assert rendered_yaml_agent["childAgents"] == ["custom_entry"]
    assert rendered_yaml_agent["tools"] == ["active_tool"]

    openapi = yaml.safe_load(
        (
            out_dir
            / "tools"
            / "active_tool"
            / "open_api_tool"
            / "open_api_schema.yaml"
        ).read_text("utf-8")
    )
    assert openapi["info"]["title"] == "Profile One API"
    assert openapi["tools"] == ["active_tool", "secret_tool_disabled"]


def test_dict_key_placeholder_substitution_and_fail_fast(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    (core_dir / "agents" / "default_agent" / "key_tokens.json").write_text(
        json.dumps({"@@profile.profile_id@@_key": "@@profile.display_name@@"}),
        encoding="utf-8",
    )
    (core_dir / "agents" / "default_agent" / "key_tokens.yaml").write_text(
        '"@@profile.profile_id@@_yaml": "@@profile.display_name@@"\n'
        "200:\n"
        "  ok: true\n",
        encoding="utf-8",
    )

    out_ok = tmp_path / "out_key_ok"
    build_app(
        core_dir, manifests_dir, "profile_1", "DIGITAL_APP", "prod", out_ok
    )
    rendered_json = json.loads(
        (out_ok / "agents" / "default_agent" / "key_tokens.json").read_text(
            "utf-8"
        )
    )
    assert rendered_json == {"profile_1_key": "Profile One"}
    rendered_yaml = yaml.safe_load(
        (out_ok / "agents" / "default_agent" / "key_tokens.yaml").read_text(
            "utf-8"
        )
    )
    assert rendered_yaml["profile_1_yaml"] == "Profile One"
    assert rendered_yaml[200] == {"ok": True}

    (core_dir / "agents" / "default_agent" / "key_tokens.json").write_text(
        json.dumps({"@@unknown.key@@": "x"}), encoding="utf-8"
    )
    with pytest.raises(BuildError, match="Unresolved placeholders"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
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
        "profile_1",
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
        "Broken @@profile.missing_field@@\n", encoding="utf-8"
    )
    with pytest.raises(BuildError, match="Unresolved placeholders"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
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
            "profile_1",
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
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_core_root_link",
        )

    # Symlink file inside manifests_dir
    man_link = manifests_dir / "profiles" / "profile_1" / "extra.yaml"
    man_link.symlink_to(outside)
    with pytest.raises(BuildError, match="Symlinks are not permitted"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
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
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_man_root_link",
        )


def test_app_yml_end_to_end_build_and_zip(tmp_path: Path) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    (core_dir / "app.json").unlink()
    (core_dir / "app.yml").write_text(
        'displayName: "@@profile.display_name@@"\nrootAgent: default_agent\n',
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_app_yml"
    report = build_app(
        core_dir,
        manifests_dir,
        "profile_1",
        "VOICE_TELEPHONY",
        "prod",
        out_dir,
        package_zip=True,
    )
    assert report.zip_path is not None
    with zipfile.ZipFile(report.zip_path, "r") as zf:
        assert "app.yml" in zf.namelist()
        staged_app = yaml.safe_load(zf.read("app.yml").decode("utf-8"))
    assert staged_app["displayName"] == "Profile One"
    assert staged_app["rootAgent"] == "custom_entry"
    assert staged_app["languageSettings"]["supportedLanguageCodes"] == [
        "en",
        "es",
    ]


def test_text_only_profile_explicit_languages_and_yaml12_country_no(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    p3 = manifests_dir / "profiles" / "profile_no"
    p3.mkdir(parents=True)
    (p3 / "identity.yaml").write_text(
        "profile_id: profile_no\n"
        "display_name: Nordic Text Profile\n"
        "languages: [en, no]\n"
        "lexicon:\n"
        "  account: Konto\n"
        "urls:\n"
        "  support: https://support.nordic.example.com\n",
        encoding="utf-8",
    )
    (p3 / "channel_policy.yaml").write_text(
        "profile_id: profile_no\n"
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
        "  profile_no:\n"
        "    NO:\n"
        "      no:\n"
        "        disclosure: Norsk informasjon.\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_nordic"
    build_app(
        core_dir, manifests_dir, "profile_no", "DIGITAL_APP", "prod", out_dir
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
    (manifests_dir / "profiles" / "profile_1" / "identity.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_id": "profile_1",
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
                "displayName": "@@profile.display_name@@",
                "rootAgent": "default_agent",
                "audioProcessingConfig": None,
                "languageSettings": None,
            }
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_unicode_bin"
    build_app(
        core_dir, manifests_dir, "profile_1", "DIGITAL_APP", "prod", out_dir
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
            "Endpoint: @@env.api:users\nWelcome to @@profile.display_name@@\n",
            "Unresolved or unclosed placeholders",
        ),
        ("{@endChannel_DIGITAL_APP}\n", "Unbalanced or mismatched conditional"),
        (
            "{@startChannel_DIGITAL_APP}\n{@endProfile_profile_1}\n",
            "Unbalanced or mismatched conditional",
        ),
        (
            "{@startChannel_DIGITAL_APP}A{@startProfile_profile_1}B"
            "{@endChannel_DIGITAL_APP}C{@endProfile_profile_1}\n",
            "Unbalanced or overlapping inline",
        ),
        (
            "{@startChannel_API}\n"
            "Broken {@startProfile_profile_1}unclosed inline\n"
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
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_malformed",
        )


def test_traversal_overlap_malformed_json_and_bad_env_templates(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)

    with pytest.raises(ManifestError, match="Unknown profile"):
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
            "profile_1",
            "DIGITAL_APP",
            "prod",
            manifests_dir,
        )
    assert (
        manifests_dir / "profiles" / "profile_1" / "identity.yaml"
    ).is_file()

    # Unclosed brace and format-string attribute traversal in env template
    for env_name, tpl in (
        ("bad_tpl", "https://ex/{profile"),
        ("gadget", "{profile.__class__.__name__}"),
    ):
        (manifests_dir / "envs" / f"{env_name}.yaml").write_text(
            f"environment: {env_name}\ntemplates:\n  api: '{tpl}'\n",
            encoding="utf-8",
        )
        with pytest.raises(BuildError):
            build_app(
                core_dir,
                manifests_dir,
                "profile_1",
                "DIGITAL_APP",
                env_name,
                tmp_path / f"out_{env_name}",
            )

    # Malformed JSON in core raises BuildError
    (core_dir / "app.json").write_text(
        '{"displayName": "@@profile.display_name@@",}', encoding="utf-8"
    )
    with pytest.raises(BuildError, match=r"(?i)json"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
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
    build_app(core_dir, manifests_dir, "profile_1", "DIGITAL_APP", "prod", out1)
    assert json.loads((out1 / "environment.json").read_text("utf-8")) == {
        "overridden_key": "prod_val",
        "nested": {"replace": 2},
    }

    (manifests_dir / "envs" / "prod.yaml").write_text(
        yaml.safe_dump(
            {
                "environment": "prod",
                "templates": {
                    "api": (
                        "https://api.example.com/{profile_slug}/{env}/{service}"
                    )
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
        core_dir, manifests_dir, "profile_1", "VOICE_TELEPHONY", "prod", out2
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
    id_path = manifests_dir / "profiles" / "profile_1" / "identity.yaml"

    # 1. Quotes, backslashes, and newlines inside standard string literal
    id_data = yaml.safe_load(id_path.read_text("utf-8"))
    id_data["display_name"] = 'Acme "Prime" \\ Bank\nLine2'
    id_path.write_text(yaml.safe_dump(id_data, sort_keys=False), "utf-8")
    out1 = tmp_path / "out_py_quotes"
    build_app(core_dir, manifests_dir, "profile_1", "DIGITAL_APP", "prod", out1)
    rendered1 = (
        out1 / "tools" / "active_tool" / "python_function" / "python_code.py"
    ).read_text("utf-8")
    ast.parse(rendered1)

    # Verify f-strings escape braces without injection and keep raw backslashes.
    id_data["display_name"] = "Acme {1 + 1} Bank"
    id_path.write_text(yaml.safe_dump(id_data, sort_keys=False), "utf-8")
    py_path.write_text(
        "def active_tool(user: str) -> tuple[str, str]:\n"
        '    msg = f"Line1\\nWelcome to @@profile.display_name@@, {user}"\n'
        '    raw_path = rf"C:\\dir\\@@profile.profile_id@@\\{user}"\n'
        "    return msg, raw_path\n",
        encoding="utf-8",
    )
    out2 = tmp_path / "out_fstring"
    build_app(core_dir, manifests_dir, "profile_1", "DIGITAL_APP", "prod", out2)
    rendered2 = (
        out2 / "tools" / "active_tool" / "python_function" / "python_code.py"
    ).read_text("utf-8")
    ns2: dict[str, object] = {}
    exec(compile(rendered2, str(py_path), "exec"), ns2)
    fn2 = ns2["active_tool"]
    assert callable(fn2)
    assert fn2("Alice") == (
        "Line1\nWelcome to Acme {1 + 1} Bank, Alice",
        r"C:\dir\profile_1\Alice",
    )

    # 3. Triple-quoted string ending in triple quotes stays valid
    profile_val = 'Acme """'
    id_data["display_name"] = profile_val
    id_path.write_text(yaml.safe_dump(id_data, sort_keys=False), "utf-8")
    py_path.write_text(
        "def active_tool() -> str:\n"
        '    return """@@profile.display_name@@"""\n',
        encoding="utf-8",
    )
    out3 = tmp_path / "out_triple_q"
    build_app(core_dir, manifests_dir, "profile_1", "DIGITAL_APP", "prod", out3)
    rendered3 = (
        out3 / "tools" / "active_tool" / "python_function" / "python_code.py"
    ).read_text("utf-8")
    ns3: dict[str, object] = {}
    exec(compile(rendered3, str(py_path), "exec"), ns3)
    fn3 = ns3["active_tool"]
    assert callable(fn3)
    assert fn3() == profile_val

    # 4. Package determinism across mtimes + junk exclusions
    os.utime(core_dir / "app.json", (1_700_000_000, 1_700_000_000))
    rep_a = build_app(
        core_dir,
        manifests_dir,
        "profile_1",
        "DIGITAL_APP",
        "prod",
        tmp_path / "det_a",
        package_zip=True,
    )
    os.utime(core_dir / "app.json", (1_750_000_000, 1_750_000_000))
    rep_b = build_app(
        core_dir,
        manifests_dir,
        "profile_1",
        "DIGITAL_APP",
        "prod",
        tmp_path / "det_b",
        package_zip=True,
    )
    assert rep_a.zip_path is not None
    assert rep_b.zip_path is not None
    assert rep_a.zip_path.read_bytes() == rep_b.zip_path.read_bytes()


# Reject symlinked or regular-file out_dir targets in build_app.
def test_build_rejects_symlink_or_regular_file_out_dir(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)

    # 1. Symlinked out_dir must be rejected without modifying the target dir.
    victim_dir = tmp_path / "victim_dir"
    victim_dir.mkdir()
    precious = victim_dir / "precious.txt"
    precious.write_text("keep me", encoding="utf-8")
    out_symlink = tmp_path / "out_symlink"
    out_symlink.symlink_to(victim_dir)
    with pytest.raises(BuildError, match="Symlinks are not permitted"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            out_symlink,
        )
    assert precious.read_text("utf-8") == "keep me"

    # 2. Existing regular file at out_dir must fail with BuildError cleanly.
    out_file = tmp_path / "existing_file"
    out_file.write_text("regular file", encoding="utf-8")
    with pytest.raises(BuildError, match=r"(?i)not a directory|file"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            out_file,
        )
    assert out_file.is_file()
    assert out_file.read_text("utf-8") == "regular file"
    leftovers = [
        p.name
        for p in out_file.parent.iterdir()
        if ".tmp-" in p.name or ".bak-" in p.name
    ]
    assert leftovers == []


def test_env_agents_pruning_tokenized_root_empty_app_yaml_and_unicode_dep(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)

    # Unicode profile display_name + env overrides attempting disabled-agent
    # reinjection.
    id_path = manifests_dir / "profiles" / "profile_1" / "identity.yaml"
    id_data = yaml.safe_load(id_path.read_text("utf-8"))
    id_data["display_name"] = "Nordic ÅÄÖ Bank"
    id_path.write_text(
        yaml.safe_dump(id_data, sort_keys=False, allow_unicode=True), "utf-8"
    )

    (core_dir / "environment.json").write_text(
        json.dumps(
            {
                "agents": {
                    "pruned_agent_dir": {"temp": 0.1},
                    "pruned_agent_display": {"temp": 0.2},
                    "default_agent": {"temp": 0.5},
                }
            }
        ),
        encoding="utf-8",
    )
    (manifests_dir / "envs" / "prod.yaml").write_text(
        yaml.safe_dump(
            {
                "environment": "prod",
                "templates": {
                    "api": (
                        "https://api.example.com/{profile_slug}/{env}/{service}"
                    )
                },
                "overrides": {
                    "agents": {
                        "pruned_agent_display": {"reinjected": True},
                        "default_agent": {"kept": True},
                    }
                },
                "app": {"displayName": "From Env App"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    # Comment-only app.yaml must still receive rootAgent & languageSettings.
    (core_dir / "app.json").unlink()
    (core_dir / "app.yaml").write_text("# empty app config\n", encoding="utf-8")

    out_ok = tmp_path / "out_ok"
    build_app(
        core_dir, manifests_dir, "profile_1", "VOICE_TELEPHONY", "prod", out_ok
    )

    app_cfg = yaml.safe_load((out_ok / "app.yaml").read_text("utf-8"))
    assert app_cfg["rootAgent"] == "custom_entry"
    assert app_cfg["languageSettings"]["supportedLanguageCodes"] == ["en", "es"]

    env_out = json.loads((out_ok / "environment.json").read_text("utf-8"))
    assert env_out["agents"] == {"default_agent": {"temp": 0.5, "kept": True}}

    dep_raw = (out_ok / "deployments" / "voice-telephony.yaml").read_text(
        "utf-8"
    )
    assert "Nordic ÅÄÖ Bank VOICE_TELEPHONY" in dep_raw

    # Tokenized rootAgent resolving to a disabled agent must fail fast.
    pol_path = manifests_dir / "profiles" / "profile_1" / "channel_policy.yaml"
    pol = yaml.safe_load(pol_path.read_text("utf-8"))
    pol["api_routing"]["root_ref"] = "pruned_agent_dir"
    pol["channels"]["VOICE_TELEPHONY"].pop("entry_agent")
    pol_path.write_text(yaml.safe_dump(pol, sort_keys=False), "utf-8")
    (core_dir / "app.yaml").write_text(
        'rootAgent: "@@routing.root_ref@@"\n', encoding="utf-8"
    )
    with pytest.raises(BuildError, match=r"rootAgent.*disabled"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "VOICE_TELEPHONY",
            "prod",
            tmp_path / "out_tok_pruned",
        )


def test_refuses_to_overwrite_unrelated_non_empty_out_dir(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)

    # 1. Non-empty directory without build marker is refused and untouched.
    unrelated = tmp_path / "unrelated_out"
    unrelated.mkdir()
    thesis = unrelated / "thesis.docx"
    thesis.write_text("important draft", encoding="utf-8")
    with pytest.raises(BuildError, match=r"\.cxas-build-output"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            unrelated,
        )
    assert thesis.read_text("utf-8") == "important draft"

    # 2. Existing empty directory succeeds, writes marker, and allows rebuilds.
    empty_out = tmp_path / "empty_out"
    empty_out.mkdir()
    rep1 = build_app(
        core_dir,
        manifests_dir,
        "profile_1",
        "DIGITAL_APP",
        "prod",
        empty_out,
        package_zip=True,
    )
    assert (empty_out / ".cxas-build-output").is_file()
    assert rep1.zip_path is not None
    with zipfile.ZipFile(rep1.zip_path, "r") as zf:
        assert ".cxas-build-output" not in zf.namelist()

    # Subsequent rebuild into the same directory succeeds.
    build_app(
        core_dir,
        manifests_dir,
        "profile_1",
        "DIGITAL_APP",
        "prod",
        empty_out,
    )
    assert (empty_out / ".cxas-build-output").is_file()


def test_duplicate_dict_keys_after_placeholder_substitution_fail_fast(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)

    # JSON duplicate key collision
    (core_dir / "environment.json").write_text(
        json.dumps({"Profile One": 1, "@@profile.display_name@@": 2}),
        encoding="utf-8",
    )
    with pytest.raises(BuildError, match="Duplicate key 'Profile One'"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_dup_json",
        )

    # YAML duplicate key collision
    (core_dir / "environment.json").write_text("{}\n", encoding="utf-8")
    (core_dir / "agents" / "default_agent" / "dup.yaml").write_text(
        'Profile One: 1\n"@@profile.display_name@@": 2\n', encoding="utf-8"
    )
    with pytest.raises(BuildError, match="Duplicate key 'Profile One'"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_dup_yaml",
        )


def test_tokenized_entry_agent_configless_agent_exported_name_and_post_render_pruning(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    pol_path = manifests_dir / "profiles" / "profile_1" / "channel_policy.yaml"

    # 1. Tokenized agent displayName matches entry_agent after rendering.
    support_dir = core_dir / "agents" / "support_agent"
    support_dir.mkdir()
    (support_dir / "support_agent.json").write_text(
        json.dumps({"displayName": "@@profile.display_name@@ Support"}),
        encoding="utf-8",
    )
    pol = yaml.safe_load(pol_path.read_text("utf-8"))
    pol["channels"]["VOICE_TELEPHONY"]["entry_agent"] = "Profile One Support"
    pol_path.write_text(yaml.safe_dump(pol, sort_keys=False), encoding="utf-8")

    out_tok_entry = tmp_path / "out_tok_entry"
    build_app(
        core_dir,
        manifests_dir,
        "profile_1",
        "VOICE_TELEPHONY",
        "prod",
        out_tok_entry,
    )
    app_cfg = json.loads((out_tok_entry / "app.json").read_text("utf-8"))
    assert app_cfg["rootAgent"] == "Profile One Support"

    # 2. Empty/configless folder under agents/ does not qualify as entry_agent.
    (core_dir / "agents" / "empty_agent").mkdir()
    pol["channels"]["VOICE_TELEPHONY"]["entry_agent"] = "empty_agent"
    pol_path.write_text(yaml.safe_dump(pol, sort_keys=False), encoding="utf-8")
    with pytest.raises(BuildError, match="does not match any surviving agent"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "VOICE_TELEPHONY",
            "prod",
            tmp_path / "out_empty_agent",
        )

    # 3. Exported `name` ID mismatch with folder name + post-render tool pruning.
    billing_dir = core_dir / "agents" / "billing_dir"
    billing_dir.mkdir()
    exported_ref = "projects/p/locations/l/apps/a/agents/exported_uuid_42"
    (billing_dir / "billing_dir.json").write_text(
        json.dumps({"displayName": "Billing", "name": exported_ref}),
        encoding="utf-8",
    )
    (core_dir / "agents" / "default_agent" / "default_agent.json").write_text(
        json.dumps(
            {
                "displayName": "default_agent",
                "childAgents": [exported_ref, "custom_entry"],
                "tools": ["@@routing.disabled_tool@@", "active_tool"],
            }
        ),
        encoding="utf-8",
    )
    yaml_agent = core_dir / "agents" / "yaml_tok_agent"
    yaml_agent.mkdir()
    (yaml_agent / "yaml_tok_agent.yaml").write_text(
        "displayName: yaml_tok_agent\n"
        "tools:\n"
        '  - "@@routing.disabled_tool@@"\n'
        "  - active_tool\n",
        encoding="utf-8",
    )
    pol["api_routing"]["disabled_tool"] = "secret_tool_disabled"
    pol["channels"]["VOICE_TELEPHONY"]["entry_agent"] = "custom_entry"
    pol["channels"]["VOICE_TELEPHONY"]["disabled_agents"] = [
        "pruned_agent_display",
        "exported_uuid_42",
    ]
    pol_path.write_text(yaml.safe_dump(pol, sort_keys=False), encoding="utf-8")

    out_exported = tmp_path / "out_exported"
    build_app(
        core_dir,
        manifests_dir,
        "profile_1",
        "VOICE_TELEPHONY",
        "prod",
        out_exported,
    )
    assert not (out_exported / "agents" / "billing_dir").exists()
    parent_json = json.loads(
        (
            out_exported / "agents" / "default_agent" / "default_agent.json"
        ).read_text("utf-8")
    )
    assert parent_json["childAgents"] == ["custom_entry"]
    assert parent_json["tools"] == ["active_tool"]

    yaml_tok_cfg = yaml.safe_load(
        (
            out_exported / "agents" / "yaml_tok_agent" / "yaml_tok_agent.yaml"
        ).read_text("utf-8")
    )
    assert yaml_tok_cfg["tools"] == ["active_tool"]


# Reject legacy {@startBrand_*} and unknown conditional tag kinds fail-fast.
@pytest.mark.parametrize(
    "content",
    [
        "{@startBrand_profile_1}\nLeaked block\n{@endBrand_profile_1}\n",
        "Prefix {@startBrand_profile_1}Leaked{@endBrand_profile_1}\n",
        "{@startRegion_EU}\nLeaked unknown kind\n{@endRegion_EU}\n",
    ],
)
def test_unknown_and_legacy_brand_conditional_tags_fail_fast(
    tmp_path: Path, content: str
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    (core_dir / "global_instruction.txt").write_text(content, encoding="utf-8")
    with pytest.raises(BuildError, match=r"(?i)unknown conditional|brand"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_unknown_cond",
        )


# Ensure a symlinked .cxas-build-output marker cannot bypass the unrelated dir guard.
def test_symlinked_build_marker_cannot_bypass_unrelated_out_dir_guard(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    unrelated = tmp_path / "unrelated_with_symlink_marker"
    unrelated.mkdir()
    thesis = unrelated / "thesis.docx"
    thesis.write_text("important document", encoding="utf-8")
    (unrelated / ".cxas-build-output").symlink_to(thesis)

    with pytest.raises(BuildError, match=r"(?i)marker|symlink|overwrite"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            unrelated,
        )
    assert thesis.read_text("utf-8") == "important document"


# Ensure agent <id>.yaml files with block conditionals build cleanly during pruning.
def test_agent_yaml_with_block_conditionals_builds_cleanly(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    cond_dir = core_dir / "agents" / "cond_agent"
    cond_dir.mkdir()
    (cond_dir / "cond_agent.yaml").write_text(
        "{@startChannel_VOICE_TELEPHONY}\n"
        "displayName: Voice Cond Agent\n"
        "{@endChannel_VOICE_TELEPHONY}\n"
        "{@startChannel_DIGITAL_APP}\n"
        "displayName: Digital Cond Agent\n"
        "{@endChannel_DIGITAL_APP}\n",
        encoding="utf-8",
    )
    pol_path = manifests_dir / "profiles" / "profile_1" / "channel_policy.yaml"
    pol = yaml.safe_load(pol_path.read_text("utf-8"))
    pol["channels"]["VOICE_TELEPHONY"]["entry_agent"] = "Voice Cond Agent"
    pol_path.write_text(yaml.safe_dump(pol, sort_keys=False), encoding="utf-8")

    out_dir = tmp_path / "out_agent_block_cond"
    build_app(
        core_dir,
        manifests_dir,
        "profile_1",
        "VOICE_TELEPHONY",
        "prod",
        out_dir,
    )
    app_cfg = json.loads((out_dir / "app.json").read_text("utf-8"))
    assert app_cfg["rootAgent"] == "Voice Cond Agent"
    agent_cfg = yaml.safe_load(
        (out_dir / "agents" / "cond_agent" / "cond_agent.yaml").read_text(
            "utf-8"
        )
    )
    assert agent_cfg == {"displayName": "Voice Cond Agent"}


def test_tokenized_display_name_prunes_disabled_agent_and_tool(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    tok_agent_dir = core_dir / "agents" / "tok_agent"
    tok_agent_dir.mkdir()
    (tok_agent_dir / "tok_agent.yaml").write_text(
        "{@startChannel_DIGITAL_APP}\n"
        'displayName: "@@profile.display_name@@ DisabledAgent"\n'
        "{@endChannel_DIGITAL_APP}\n"
        "{@startChannel_VOICE_TELEPHONY}\n"
        "displayName: VoiceAgent\n"
        "{@endChannel_VOICE_TELEPHONY}\n",
        encoding="utf-8",
    )
    tok_tool_dir = core_dir / "tools" / "tok_tool"
    tok_tool_dir.mkdir()
    (tok_tool_dir / "tok_tool.yaml").write_text(
        "{@startChannel_DIGITAL_APP}\n"
        'displayName: "@@profile.display_name@@ DisabledTool"\n'
        "{@endChannel_DIGITAL_APP}\n"
        "{@startChannel_VOICE_TELEPHONY}\n"
        "displayName: VoiceTool\n"
        "{@endChannel_VOICE_TELEPHONY}\n",
        encoding="utf-8",
    )
    pol_path = manifests_dir / "profiles" / "profile_1" / "channel_policy.yaml"
    pol = yaml.safe_load(pol_path.read_text("utf-8"))
    pol["channels"]["DIGITAL_APP"]["disabled_agents"] = [
        "Profile One DisabledAgent"
    ]
    pol["channels"]["DIGITAL_APP"]["disabled_tools"] = [
        "Profile One DisabledTool"
    ]
    pol_path.write_text(yaml.safe_dump(pol, sort_keys=False), encoding="utf-8")

    out_digital = tmp_path / "out_digital_tok"
    build_app(
        core_dir,
        manifests_dir,
        "profile_1",
        "DIGITAL_APP",
        "prod",
        out_digital,
    )
    assert not (out_digital / "agents" / "tok_agent").exists()
    assert not (out_digital / "tools" / "tok_tool").exists()

    out_voice = tmp_path / "out_voice_tok"
    build_app(
        core_dir,
        manifests_dir,
        "profile_1",
        "VOICE_TELEPHONY",
        "prod",
        out_voice,
    )
    assert yaml.safe_load(
        (out_voice / "agents" / "tok_agent" / "tok_agent.yaml").read_text(
            "utf-8"
        )
    ) == {"displayName": "VoiceAgent"}
    assert yaml.safe_load(
        (out_voice / "tools" / "tok_tool" / "tok_tool.yaml").read_text("utf-8")
    ) == {"displayName": "VoiceTool"}


@pytest.mark.parametrize(
    ("rel_path", "content", "expected_kind"),
    [
        (
            "agents/cond_ag/cond_ag.json",
            json.dumps(
                {
                    "displayName": (
                        "{@startToolEnabled_active_tool}Bad"
                        "{@endToolEnabled_active_tool}"
                    )
                }
            ),
            "ToolEnabled",
        ),
        (
            "agents/cond_ag/cond_ag.json",
            json.dumps(
                {
                    "displayName": "Valid",
                    "name": (
                        "{@startAgentEnabled_default_agent}bad"
                        "{@endAgentEnabled_default_agent}"
                    ),
                }
            ),
            "AgentEnabled",
        ),
        (
            "agents/cond_ag/cond_ag.yaml",
            "{@startToolEnabled_active_tool}\n"
            "displayName: Bad\n"
            "{@endToolEnabled_active_tool}\n",
            "ToolEnabled",
        ),
        (
            "tools/cond_tl/cond_tl.yaml",
            "{@startAgentEnabled_default_agent}\n"
            "displayName: Bad\n"
            "{@endAgentEnabled_default_agent}\n",
            "AgentEnabled",
        ),
    ],
)
def test_resource_identity_rejects_circular_enabled_conditionals(
    tmp_path: Path, rel_path: str, content: str, expected_kind: str
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    cfg = core_dir / rel_path
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(content, encoding="utf-8")

    with pytest.raises(
        BuildError,
        match=(
            rf"Conditional tag kind '{expected_kind}' is not allowed inside "
            r"resource identity fields"
        ),
    ):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "VOICE_TELEPHONY",
            "prod",
            tmp_path / "out_circular_cond",
        )


@pytest.mark.parametrize(
    ("yaml_body", "expected_key"),
    [
        (
            "foo: 1\n"
            "{@startChannel_DIGITAL_APP}\n"
            "foo: 2\n"
            "{@endChannel_DIGITAL_APP}\n",
            "foo",
        ),
        (
            "key: 1\n{@startProfile_profile_1}key{@endProfile_profile_1}: 2\n",
            "key",
        ),
    ],
)
def test_yaml_conditional_block_duplicate_keys_fail_fast(
    tmp_path: Path, yaml_body: str, expected_key: str
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    (core_dir / "agents" / "default_agent" / "cond_dup.yaml").write_text(
        yaml_body, encoding="utf-8"
    )
    with pytest.raises(
        BuildError, match=f"Duplicate mapping key '{expected_key}'"
    ):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_cond_dup",
        )


def test_disabled_resources_ignore_undefined_profile_tokens(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    (
        core_dir / "tools" / "secret_tool_dir" / "secret_tool_dir.json"
    ).write_text(
        json.dumps({"displayName": "@@routing.nonexistent_for_this_profile@@"}),
        encoding="utf-8",
    )
    (
        core_dir / "agents" / "pruned_agent_dir" / "pruned_agent_dir.json"
    ).write_text(
        json.dumps({"displayName": "@@routing.nonexistent_for_this_profile@@"}),
        encoding="utf-8",
    )

    pol_path = manifests_dir / "profiles" / "profile_1" / "channel_policy.yaml"
    pol = yaml.safe_load(pol_path.read_text("utf-8"))
    pol["channels"]["VOICE_TELEPHONY"]["disabled_agents"] = ["pruned_agent_dir"]
    pol["channels"]["VOICE_TELEPHONY"]["disabled_tools"] = ["secret_tool_dir"]
    pol_path.write_text(yaml.safe_dump(pol, sort_keys=False), encoding="utf-8")

    out_dir = tmp_path / "out_disabled_undef_tok"
    build_app(
        core_dir, manifests_dir, "profile_1", "VOICE_TELEPHONY", "prod", out_dir
    )
    assert not (out_dir / "tools" / "secret_tool_dir").exists()
    assert not (out_dir / "agents" / "pruned_agent_dir").exists()


def test_yaml_anchor_merge_key_override_allowed_with_unique_key_loader(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    openapi_path = (
        core_dir / "tools" / "active_tool" / "open_api_tool" / "schema.yaml"
    )
    openapi_path.parent.mkdir(parents=True, exist_ok=True)
    openapi_path.write_text(
        "x-defaults: &def\n"
        "  timeout: 30\n"
        "  retries: 3\n"
        "x-extra: &extra\n"
        "  timeout: 45\n"
        "  region: eu\n"
        "info:\n"
        '  title: "@@profile.display_name@@ API"\n'
        "  <<: [*def, *extra]\n"
        "  timeout: 60\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_yaml_merge"
    build_app(
        core_dir, manifests_dir, "profile_1", "DIGITAL_APP", "prod", out_dir
    )
    rendered = yaml.safe_load(
        (
            out_dir / "tools" / "active_tool" / "open_api_tool" / "schema.yaml"
        ).read_text("utf-8")
    )
    assert rendered["info"]["title"] == "Profile One API"
    assert rendered["info"]["timeout"] == 60
    assert rendered["info"]["retries"] == 3
    assert rendered["info"]["region"] == "eu"

    # Explicit duplicate key alongside a merge key must still fail fast.
    openapi_path.write_text(
        "x-defaults: &def\n"
        "  retries: 3\n"
        "info:\n"
        '  title: "@@profile.display_name@@ API"\n'
        "  <<: *def\n"
        "  timeout: 10\n"
        "  timeout: 20\n",
        encoding="utf-8",
    )
    with pytest.raises(BuildError, match="Duplicate mapping key 'timeout'"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_yaml_merge_dup",
        )

    # Unhashable YAML mapping key must raise BuildError (not uncaught TypeError).
    openapi_path.write_text(
        'title: "@@profile.display_name@@"\n? [1, 2]\n: x\n', encoding="utf-8"
    )
    with pytest.raises(BuildError, match=r"(?i)unhashable"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_yaml_unhashable",
        )


def test_post_pruning_enabled_conditionals_and_static_identity_conditionals(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)

    # Static Channel/Profile conditional in displayName prunes or survives in
    # a single deterministic pass.
    pol_path = manifests_dir / "profiles" / "profile_1" / "channel_policy.yaml"
    pol = yaml.safe_load(pol_path.read_text("utf-8"))
    pol["channels"]["VOICE_TELEPHONY"]["disabled_agents"].append(
        "static_voice_disabled"
    )
    pol_path.write_text(yaml.safe_dump(pol, sort_keys=False), encoding="utf-8")

    static_dir = core_dir / "agents" / "static_cond_agent"
    static_dir.mkdir()
    (static_dir / "static_cond_agent.yaml").write_text(
        'displayName: "{@startChannel_VOICE_TELEPHONY}static_voice_disabled'
        "{@endChannel_VOICE_TELEPHONY}"
        "{@startChannel_DIGITAL_APP}Digital Static{@endChannel_DIGITAL_APP}"
        '"\n',
        encoding="utf-8",
    )

    # Surviving prompt and non-identity JSON agent fields evaluate
    # AgentEnabled / ToolEnabled using all pruned aliases (folder + displayName).
    (core_dir / "agents" / "default_agent" / "default_agent.json").write_text(
        json.dumps(
            {
                "displayName": "default_agent",
                "description": (
                    "Base"
                    "{@startToolEnabled_secret_tool_dir} DIR_LEAK"
                    "{@endToolEnabled_secret_tool_dir}"
                    "{@startToolEnabled_secret_tool_disabled} DISP_LEAK"
                    "{@endToolEnabled_secret_tool_disabled}"
                    "{@startToolEnabled_active_tool} ACTIVE_OK"
                    "{@endToolEnabled_active_tool}"
                ),
                "childAgents": ["pruned_agent_display"],
                "tools": ["secret_tool_disabled", "active_tool"],
            }
        ),
        encoding="utf-8",
    )
    (core_dir / "agents" / "default_agent" / "instruction.txt").write_text(
        "{@startAgentEnabled_pruned_agent_dir}AG_DIR_LEAK"
        "{@endAgentEnabled_pruned_agent_dir}\n"
        "{@startAgentEnabled_pruned_agent_display}AG_DISP_LEAK"
        "{@endAgentEnabled_pruned_agent_display}\n"
        "{@startToolEnabled_secret_tool_dir}TL_DIR_LEAK"
        "{@endToolEnabled_secret_tool_dir}\n"
        "{@startToolEnabled_active_tool}KEEP_ACTIVE"
        "{@endToolEnabled_active_tool}\n",
        encoding="utf-8",
    )

    out_voice = tmp_path / "out_voice_single_pass"
    build_app(
        core_dir,
        manifests_dir,
        "profile_1",
        "VOICE_TELEPHONY",
        "prod",
        out_voice,
    )
    assert not (out_voice / "agents" / "static_cond_agent").exists()
    agent_cfg = json.loads(
        (
            out_voice / "agents" / "default_agent" / "default_agent.json"
        ).read_text("utf-8")
    )
    assert agent_cfg["description"] == "Base ACTIVE_OK"
    instr_text = (
        out_voice / "agents" / "default_agent" / "instruction.txt"
    ).read_text("utf-8")
    assert "LEAK" not in instr_text
    assert "KEEP_ACTIVE" in instr_text

    out_digital = tmp_path / "out_digital_single_pass"
    build_app(
        core_dir,
        manifests_dir,
        "profile_1",
        "DIGITAL_APP",
        "prod",
        out_digital,
    )
    assert (out_digital / "agents" / "static_cond_agent").is_dir()
    assert yaml.safe_load(
        (
            out_digital
            / "agents"
            / "static_cond_agent"
            / "static_cond_agent.yaml"
        ).read_text("utf-8")
    ) == {"displayName": "Digital Static"}


def test_yaml_resource_configs_allow_enabled_conditionals_outside_identity_fields(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)

    yaml_agent = core_dir / "agents" / "yaml_desc_agent"
    yaml_agent.mkdir()
    (yaml_agent / "yaml_desc_agent.yaml").write_text(
        "displayName: YamlDescAgent\n"
        'description: "Base'
        "{@startToolEnabled_active_tool} INLINE_ACTIVE"
        "{@endToolEnabled_active_tool}"
        "{@startToolEnabled_secret_tool_dir} INLINE_LEAK"
        '{@endToolEnabled_secret_tool_dir}"\n'
        "{@startToolEnabled_active_tool}\n"
        "activeBlock: kept\n"
        "{@endToolEnabled_active_tool}\n"
        "{@startToolEnabled_secret_tool_disabled}\n"
        "disabledBlock: leaked\n"
        "{@endToolEnabled_secret_tool_disabled}\n",
        encoding="utf-8",
    )

    yaml_tool = core_dir / "tools" / "yaml_desc_tool"
    yaml_tool.mkdir()
    (yaml_tool / "yaml_desc_tool.yaml").write_text(
        "displayName: YamlDescTool\n"
        'description: "Tool'
        "{@startAgentEnabled_custom_entry} AGENT_ACTIVE"
        "{@endAgentEnabled_custom_entry}"
        "{@startAgentEnabled_pruned_agent_display} AGENT_LEAK"
        '{@endAgentEnabled_pruned_agent_display}"\n'
        "{@startAgentEnabled_custom_entry}\n"
        "agentActiveBlock: kept\n"
        "{@endAgentEnabled_custom_entry}\n"
        "{@startAgentEnabled_pruned_agent_dir}\n"
        "agentDisabledBlock: leaked\n"
        "{@endAgentEnabled_pruned_agent_dir}\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_yaml_desc_cond"
    build_app(
        core_dir, manifests_dir, "profile_1", "VOICE_TELEPHONY", "prod", out_dir
    )

    built_agent = yaml.safe_load(
        (
            out_dir / "agents" / "yaml_desc_agent" / "yaml_desc_agent.yaml"
        ).read_text("utf-8")
    )
    assert built_agent == {
        "displayName": "YamlDescAgent",
        "description": "Base INLINE_ACTIVE",
        "activeBlock": "kept",
    }

    built_tool = yaml.safe_load(
        (
            out_dir / "tools" / "yaml_desc_tool" / "yaml_desc_tool.yaml"
        ).read_text("utf-8")
    )
    assert built_tool == {
        "displayName": "YamlDescTool",
        "description": "Tool AGENT_ACTIVE",
        "agentActiveBlock": "kept",
    }


def test_nested_yaml_anchor_overrides_and_inline_merge_duplicate_rejection(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    schema_path = (
        core_dir / "tools" / "active_tool" / "open_api_tool" / "nested.yaml"
    )
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path.write_text(
        "grand: &grand\n"
        "  timeout: 10\n"
        "  retries: 1\n"
        "parent: &parent\n"
        "  <<: *grand\n"
        "  timeout: 20\n"
        "  region: eu\n"
        "prod:\n"
        "  <<: *parent\n"
        '  title: "@@profile.display_name@@"\n'
        "  timeout: 30\n",
        encoding="utf-8",
    )

    out_ok = tmp_path / "out_nested_anchor"
    build_app(
        core_dir, manifests_dir, "profile_1", "DIGITAL_APP", "prod", out_ok
    )
    rendered = yaml.safe_load(
        (
            out_ok / "tools" / "active_tool" / "open_api_tool" / "nested.yaml"
        ).read_text("utf-8")
    )
    assert rendered["prod"] == {
        "timeout": 30,
        "retries": 1,
        "region": "eu",
        "title": "Profile One",
    }

    # Inline merge source with duplicate key must fail fast.
    schema_path.write_text(
        "info:\n"
        '  title: "@@profile.display_name@@"\n'
        "  <<: {x: 1, x: 2}\n"
        "  y: 3\n",
        encoding="utf-8",
    )
    with pytest.raises(BuildError, match="Duplicate mapping key 'x'"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_inline_merge_dup",
        )


def test_merge_app_config_semantics_and_immutability() -> None:
    base = {
        "displayName": "Base App",
        "description": "Will be deleted",
        "guardrails": ["g1", "g2"],
        "audioProcessingConfig": {
            "inactivityTimeout": "120s",
            "ambientSoundConfig": {"volumeGainDb": 1.0},
        },
        "timeZoneSettings": {"timeZone": "UTC"},
    }
    layer1 = {
        "description": None,
        "nonexistentKey": None,
        "guardrails": ["g3"],
        "audioProcessingConfig": {
            "inactivityTimeout": "300s",
            "ambientSoundConfig": None,
        },
        "freshSection": {"keep": True, "drop": None},
    }
    layer2 = {
        "guardrails": [],
        "timeZoneSettings": {"timeZone": "America/New_York"},
    }

    merged = merge_app_config(base, layer1, None, layer2)
    assert merged == {
        "displayName": "Base App",
        "guardrails": [],
        "audioProcessingConfig": {"inactivityTimeout": "300s"},
        "timeZoneSettings": {"timeZone": "America/New_York"},
        "freshSection": {"keep": True},
    }

    # Mutating merged output must not mutate inputs.
    merged["guardrails"].append("mutated")
    merged["audioProcessingConfig"]["inactivityTimeout"] = "999s"
    merged["freshSection"]["keep"] = False
    assert base["guardrails"] == ["g1", "g2"]
    assert base["audioProcessingConfig"]["inactivityTimeout"] == "120s"
    assert layer1["freshSection"] == {"keep": True, "drop": None}
    assert layer2["guardrails"] == []


def test_env_app_block_merge_order_tokens_and_profile_precedence(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    (core_dir / "app.json").write_text(
        json.dumps(
            {
                "displayName": "Base App",
                "description": "Remove me in prod",
                "rootAgent": "default_agent",
                "guardrails": ["core_guardrail"],
                "audioProcessingConfig": {"inactivityTimeout": "120s"},
                "timeZoneSettings": {"timeZone": "UTC"},
            }
        ),
        encoding="utf-8",
    )
    (manifests_dir / "envs" / "prod.yaml").write_text(
        "environment: prod\n"
        "templates:\n"
        "  api: https://api.example.com/{profile_slug}/{env}/{service}\n"
        "app:\n"
        '  displayName: "@@profile.display_name@@ @@build.environment@@"\n'
        "  description: null\n"
        "  rootAgent: default_agent\n"
        "  guardrails:\n"
        '    - "{@startChannel_VOICE_TELEPHONY}voice_guardrail'
        '{@endChannel_VOICE_TELEPHONY}"\n'
        "  timeZoneSettings:\n"
        "    timeZone: America/New_York\n"
        "  audioProcessingConfig:\n"
        "    inactivityTimeout: 300s\n"
        "    synthesizeSpeechConfigs:\n"
        "      en:\n"
        "        voice: env-override-should-lose\n"
        "  languageSettings:\n"
        "    defaultLanguageCode: de\n"
        "    supportedLanguageCodes: [de]\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_env_app_order"
    build_app(
        core_dir, manifests_dir, "profile_1", "VOICE_TELEPHONY", "prod", out_dir
    )

    app_cfg = json.loads((out_dir / "app.json").read_text("utf-8"))
    assert app_cfg["displayName"] == "Profile One prod"
    assert "description" not in app_cfg
    assert app_cfg["guardrails"] == ["voice_guardrail"]
    assert app_cfg["timeZoneSettings"] == {"timeZone": "America/New_York"}
    assert app_cfg["audioProcessingConfig"]["inactivityTimeout"] == "300s"
    # Profile-derived settings win over env app: where they overlap.
    assert app_cfg["rootAgent"] == "custom_entry"
    assert (
        app_cfg["audioProcessingConfig"]["synthesizeSpeechConfigs"]["en"][
            "voice"
        ]
        == "en-US-Standard-A"
    )
    assert app_cfg["languageSettings"] == {
        "defaultLanguageCode": "en",
        "supportedLanguageCodes": ["en", "es"],
        "enableMultilingualSupport": True,
    }

    # Unresolved token inside app: fails fast naming the env file.
    (manifests_dir / "envs" / "prod.yaml").write_text(
        "environment: prod\n"
        "templates:\n"
        "  api: https://api.example.com/{profile_slug}/{env}/{service}\n"
        "app:\n"
        '  displayName: "@@profile.does_not_exist@@"\n',
        encoding="utf-8",
    )
    with pytest.raises(
        BuildError, match=r"prod\.yaml:.*Unresolved placeholders"
    ):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_env_app_bad_tok",
        )

    # Tokenized forbidden key rendered from routing fails fast naming env file.
    pol_path = manifests_dir / "profiles" / "profile_1" / "channel_policy.yaml"
    pol = yaml.safe_load(pol_path.read_text("utf-8"))
    pol["api_routing"]["bad_key"] = "etag"
    pol_path.write_text(yaml.safe_dump(pol, sort_keys=False), encoding="utf-8")
    (manifests_dir / "envs" / "prod.yaml").write_text(
        "environment: prod\n"
        "templates:\n"
        "  api: https://api.example.com/{profile_slug}/{env}/{service}\n"
        "app:\n"
        '  "@@routing.bad_key@@": "abc"\n',
        encoding="utf-8",
    )
    with pytest.raises(
        BuildError, match=r"prod\.yaml:.*Forbidden key 'app\.etag'"
    ):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_env_app_tok_forbidden",
        )


@pytest.mark.parametrize(
    ("app_yaml_block", "expected_pattern"),
    [
        (
            "  unknownTopField: 123\n",
            r"prod\.yaml.*unknownTopField",
        ),
        (
            "  timeZoneSettings:\n    badKey: UTC\n",
            r"prod\.yaml.*badKey",
        ),
        (
            "  audioProcessingConfig:\n    inactivityTimeout: not-a-duration\n",
            r"prod\.yaml.*inactivityTimeout|duration",
        ),
        (
            "  displayName: null\n",
            r"prod\.yaml.*display_name",
        ),
    ],
)
def test_env_app_block_schema_validation_errors(
    tmp_path: Path, app_yaml_block: str, expected_pattern: str
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    (core_dir / "app.json").write_text(
        json.dumps({"displayName": "Valid App", "rootAgent": "default_agent"}),
        encoding="utf-8",
    )
    (manifests_dir / "envs" / "prod.yaml").write_text(
        "environment: prod\n"
        "templates:\n"
        "  api: https://api.example.com/{profile_slug}/{env}/{service}\n"
        f"app:\n{app_yaml_block}",
        encoding="utf-8",
    )

    with pytest.raises(BuildError, match=expected_pattern):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_schema_invalid",
        )


def test_env_app_block_missing_core_app_config_raises(tmp_path: Path) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    (core_dir / "app.json").unlink()
    (manifests_dir / "envs" / "prod.yaml").write_text(
        "environment: prod\n"
        "templates:\n"
        "  api: https://api.example.com/{profile_slug}/{env}/{service}\n"
        "app:\n"
        "  displayName: Orphan App\n",
        encoding="utf-8",
    )

    with pytest.raises(BuildError, match=r"prod\.yaml.*no app config"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_missing_app_cfg",
        )


@pytest.mark.parametrize("filename", ["app.json", "app.yaml", "app.yml"])
def test_env_app_block_parity_across_json_yaml_yml_and_anchors(
    tmp_path: Path, filename: str
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    (core_dir / "app.json").unlink()

    if filename == "app.json":
        (core_dir / filename).write_text(
            json.dumps(
                {"displayName": "Core App", "rootAgent": "default_agent"}
            ),
            encoding="utf-8",
        )
    else:
        (core_dir / filename).write_text(
            "displayName: Core App\nrootAgent: default_agent\n",
            encoding="utf-8",
        )

    # Profile 2 has no tts_voices, languages, or entry_agent — verifies tokenless
    # app.yaml/app.yml still writes back when env app: is applied.
    (manifests_dir / "envs" / "prod.yaml").write_text(
        "environment: prod\n"
        "templates:\n"
        "  api: https://api.example.com/{profile_slug}/{env}/{service}\n"
        "overrides:\n"
        "  anchor_src: &audio_base\n"
        "    inactivityTimeout: 120s\n"
        "app:\n"
        "  timeZoneSettings:\n"
        "    timeZone: Europe/Oslo\n"
        "  audioProcessingConfig:\n"
        "    <<: *audio_base\n"
        "    inactivityTimeout: 300s\n"
        "  variableDeclarations:\n"
        "    - name: country\n"
        "      schema:\n"
        "        type: STRING\n"
        "        default: NO\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / f"out_{filename.replace('.', '_')}"
    build_app(
        core_dir, manifests_dir, "profile_2", "DIGITAL_APP", "prod", out_dir
    )

    out_path = out_dir / filename
    merged = (
        json.loads(out_path.read_text("utf-8"))
        if filename.endswith(".json")
        else yaml.safe_load(out_path.read_text("utf-8"))
    )
    assert merged["displayName"] == "Core App"
    assert merged["timeZoneSettings"] == {"timeZone": "Europe/Oslo"}
    assert merged["audioProcessingConfig"] == {"inactivityTimeout": "300s"}
    assert merged["variableDeclarations"] == [
        {"name": "country", "schema": {"type": "STRING", "default": "NO"}}
    ]


def test_env_app_block_on_comment_only_app_yaml(tmp_path: Path) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    (core_dir / "app.json").unlink()
    (core_dir / "app.yaml").write_text("# comment only\n", encoding="utf-8")
    (manifests_dir / "envs" / "prod.yaml").write_text(
        "environment: prod\n"
        "templates:\n"
        "  api: https://api.example.com/{profile_slug}/{env}/{service}\n"
        "app:\n"
        "  displayName: From Env App\n"
        "  rootAgent: default_agent\n"
        "  timeZoneSettings:\n"
        "    timeZone: UTC\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_empty_app_yaml"
    build_app(
        core_dir, manifests_dir, "profile_2", "DIGITAL_APP", "prod", out_dir
    )
    merged = yaml.safe_load((out_dir / "app.yaml").read_text("utf-8"))
    assert merged == {
        "displayName": "From Env App",
        "rootAgent": "default_agent",
        "timeZoneSettings": {"timeZone": "UTC"},
    }


def test_no_env_app_block_preserves_byte_identical_minimal_output(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    # Without an env app: block, repeated builds remain byte-identical.
    out1 = tmp_path / "out_no_app_1"
    out2 = tmp_path / "out_no_app_2"
    r1 = build_app(
        core_dir,
        manifests_dir,
        "profile_1",
        "DIGITAL_APP",
        "prod",
        out1,
        package_zip=True,
    )
    r2 = build_app(
        core_dir,
        manifests_dir,
        "profile_1",
        "DIGITAL_APP",
        "prod",
        out2,
        package_zip=True,
    )
    assert r1.zip_path is not None
    assert r2.zip_path is not None
    assert (out1 / "app.json").read_bytes() == (out2 / "app.json").read_bytes()
    assert r1.zip_path.read_bytes() == r2.zip_path.read_bytes()


def test_docs_build_guide_env_app_example_passes_cxas_lint(
    tmp_path: Path,
) -> None:
    from cxas_scrapi.utils.linter import (  # noqa: PLC0415
        Discovery,
        LintConfig,
        LintReport,
        Severity,
        build_context,
        build_registry,
        run_rules,
    )

    core_dir = tmp_path / "core"
    (core_dir / "agents" / "root_agent").mkdir(parents=True)
    (core_dir / "tools" / "end_session").mkdir(parents=True)
    (core_dir / "guardrails" / "profile_safety_guardrail").mkdir(parents=True)

    (core_dir / "app.json").write_text(
        json.dumps(
            {
                "name": "projects/example-prod/locations/us/apps/profile-one",
                "displayName": "Profile One Prod App",
                "rootAgent": "root_agent",
                "globalInstruction": "global_instruction.txt",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (core_dir / "global_instruction.txt").write_text(
        "Follow @@profile.display_name@@ policies.\n", encoding="utf-8"
    )
    (core_dir / "agents" / "root_agent" / "root_agent.json").write_text(
        json.dumps(
            {
                "displayName": "root_agent",
                "instruction": "agents/root_agent/instruction.txt",
                "tools": ["end_session"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (core_dir / "tools" / "end_session" / "end_session.json").write_text(
        json.dumps(
            {
                "displayName": "end_session",
                "systemTool": {
                    "name": "end_session",
                    "description": "Ends the current conversation session.",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (core_dir / "agents" / "root_agent" / "instruction.txt").write_text(
        "<role>You are a helpful assistant.</role>\n"
        "<persona>Clear and concise.</persona>\n"
        "<constraints>Stay on topic. Call {@TOOL: end_session} when done."
        "</constraints>\n",
        encoding="utf-8",
    )
    (
        core_dir
        / "guardrails"
        / "profile_safety_guardrail"
        / "profile_safety_guardrail.json"
    ).write_text(
        json.dumps(
            {
                "displayName": "profile_safety_guardrail",
                "enabled": True,
                "contentFilter": {
                    "bannedContents": ["forbidden_phrase"],
                    "matchType": "SIMPLE_STRING_MATCH",
                },
                "action": {
                    "respondImmediately": {
                        "responses": [{"text": "I cannot help with that."}]
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    manifests_dir = tmp_path / "manifests"
    p1 = manifests_dir / "profiles" / "profile_1"
    p1.mkdir(parents=True)
    (p1 / "identity.yaml").write_text(
        "profile_id: profile_1\n"
        "display_name: Profile One\n"
        "languages: [en]\n"
        "tts_voices:\n"
        "  en:\n"
        "    voice: en-US-Standard-A\n"
        "    speaking_rate: 1.0\n",
        encoding="utf-8",
    )
    (p1 / "channel_policy.yaml").write_text(
        "profile_id: profile_1\n"
        "supported_countries: [US]\n"
        "channels:\n"
        "  WEB:\n"
        "    supported_modalities: [TEXT]\n",
        encoding="utf-8",
    )
    envs_dir = manifests_dir / "envs"
    envs_dir.mkdir(parents=True)
    # Exact app: snippet from docs/guides/build.md
    (envs_dir / "prod.yaml").write_text(
        "environment: prod\n"
        "templates:\n"
        "  gcp_project: example-prod-{profile_slug}-{env}\n"
        "  api: https://api.example.com/{env}/{service}\n"
        "overrides:\n"
        "  endpoint_region: us-central1\n"
        "app:\n"
        "  timeZoneSettings:\n"
        "    timeZone: America/New_York\n"
        "  guardrails:\n"
        "    - profile_safety_guardrail\n"
        "  audioProcessingConfig:\n"
        "    inactivityTimeout: 300s\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_docs_example"
    build_app(core_dir, manifests_dir, "profile_1", "WEB", "prod", out_dir)

    registry = build_registry()
    config = LintConfig()
    discovery = Discovery(out_dir, out_dir / "evals")
    context = build_context(out_dir, config, discovery)
    report = LintReport()
    run_rules(registry, config, context, discovery, report)
    errors = [r for r in report.results if r.severity == Severity.ERROR]
    assert errors == [], f"Unexpected lint errors: {errors}"


def test_env_app_snake_case_root_agent_pruned_raises(tmp_path: Path) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    pol_path = manifests_dir / "profiles" / "profile_1" / "channel_policy.yaml"
    pol = yaml.safe_load(pol_path.read_text("utf-8"))
    pol["channels"]["DIGITAL_APP"]["disabled_agents"] = ["pruned_agent_display"]
    pol_path.write_text(yaml.safe_dump(pol, sort_keys=False), encoding="utf-8")

    (core_dir / "app.json").write_text(
        json.dumps({"displayName": "Valid App"}), encoding="utf-8"
    )
    (manifests_dir / "envs" / "prod.yaml").write_text(
        "environment: prod\n"
        "templates:\n"
        "  api: https://api.example.com/{profile_slug}/{env}/{service}\n"
        "app:\n"
        "  root_agent: pruned_agent_dir\n",
        encoding="utf-8",
    )

    with pytest.raises(BuildError):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_pruned_snake_root",
        )


def test_env_app_snake_case_root_agent_cannot_override_entry_agent(
    tmp_path: Path,
) -> None:
    from google.cloud.ces_v1beta import types  # noqa: PLC0415
    from google.protobuf import json_format  # noqa: PLC0415

    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    (core_dir / "app.json").write_text(
        json.dumps({"displayName": "Valid App", "rootAgent": "default_agent"}),
        encoding="utf-8",
    )
    (manifests_dir / "envs" / "prod.yaml").write_text(
        "environment: prod\n"
        "templates:\n"
        "  api: https://api.example.com/{profile_slug}/{env}/{service}\n"
        "app:\n"
        "  root_agent: pruned_agent_dir\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_snake_root_with_entry"
    try:
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "VOICE_TELEPHONY",
            "prod",
            out_dir,
        )
    except BuildError:
        return

    app_cfg = json.loads((out_dir / "app.json").read_text("utf-8"))
    assert "root_agent" not in app_cfg
    assert app_cfg["rootAgent"] == "custom_entry"
    proto_app = types.App()
    json_format.ParseDict(app_cfg, proto_app._pb, ignore_unknown_fields=False)
    assert proto_app.root_agent == "custom_entry"


def test_env_app_snake_case_sections_cannot_clobber_profile_precedence(
    tmp_path: Path,
) -> None:
    from google.cloud.ces_v1beta import types  # noqa: PLC0415
    from google.protobuf import json_format  # noqa: PLC0415

    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    (core_dir / "app.json").write_text(
        json.dumps(
            {
                "displayName": "Valid App",
                "rootAgent": "default_agent",
                "audioProcessingConfig": {"inactivityTimeout": "120s"},
                "languageSettings": {
                    "defaultLanguageCode": "en",
                    "supportedLanguageCodes": ["en"],
                    "enableMultilingualSupport": False,
                },
            }
        ),
        encoding="utf-8",
    )
    (manifests_dir / "envs" / "prod.yaml").write_text(
        "environment: prod\n"
        "templates:\n"
        "  api: https://api.example.com/{profile_slug}/{env}/{service}\n"
        "app:\n"
        "  audio_processing_config:\n"
        "    inactivity_timeout: 300s\n"
        "    synthesize_speech_configs:\n"
        "      en:\n"
        "        voice: env-override-should-lose\n"
        "        speaking_rate: 2.0\n"
        "  language_settings:\n"
        "    default_language_code: fr\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_snake_sections"
    try:
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "VOICE_TELEPHONY",
            "prod",
            out_dir,
        )
    except BuildError:
        return

    app_cfg = json.loads((out_dir / "app.json").read_text("utf-8"))
    assert "audio_processing_config" not in app_cfg
    assert "language_settings" not in app_cfg
    proto_app = types.App()
    json_format.ParseDict(app_cfg, proto_app._pb, ignore_unknown_fields=False)
    assert proto_app.language_settings.default_language_code == "en"
    assert (
        proto_app.audio_processing_config.synthesize_speech_configs["en"].voice
        == "en-US-Standard-A"
    )


def test_env_app_nested_snake_case_rejected_and_struct_map_keys_allowed(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    (core_dir / "app.json").write_text(
        json.dumps({"displayName": "Valid App", "rootAgent": "default_agent"}),
        encoding="utf-8",
    )

    # User-defined snake_case keys in metadata, Schema.properties, and
    # google.protobuf.Value defaults must remain allowed.
    (manifests_dir / "envs" / "prod.yaml").write_text(
        "environment: prod\n"
        "templates:\n"
        "  api: https://api.example.com/{profile_slug}/{env}/{service}\n"
        "app:\n"
        "  metadata:\n"
        "    my_snake_key: ok\n"
        "  variableDeclarations:\n"
        "    - name: user_profile\n"
        "      description: User profile\n"
        "      schema:\n"
        "        type: OBJECT\n"
        "        properties:\n"
        "          first_name:\n"
        "            type: STRING\n"
        "        default:\n"
        "          my_snake_key: 1\n",
        encoding="utf-8",
    )
    out_ok = tmp_path / "out_allowed_maps"
    build_app(
        core_dir, manifests_dir, "profile_2", "DIGITAL_APP", "prod", out_ok
    )
    app_cfg = json.loads((out_ok / "app.json").read_text("utf-8"))
    assert app_cfg["metadata"] == {"my_snake_key": "ok"}
    assert app_cfg["variableDeclarations"][0]["schema"]["default"] == {
        "my_snake_key": 1
    }

    # Nested proto snake_case fields (including inside map values) must fail.
    for snippet, expected_camel in [
        (
            "  audioProcessingConfig:\n    inactivity_timeout: 300s\n",
            "inactivityTimeout",
        ),
        (
            "  audioProcessingConfig:\n"
            "    synthesizeSpeechConfigs:\n"
            "      en:\n"
            "        voice: en-US-Standard-A\n"
            "        speaking_rate: 1.0\n",
            "speakingRate",
        ),
    ]:
        (manifests_dir / "envs" / "prod.yaml").write_text(
            "environment: prod\n"
            "templates:\n"
            "  api: https://api.example.com/{profile_slug}/{env}/{service}\n"
            f"app:\n{snippet}",
            encoding="utf-8",
        )
        with pytest.raises(BuildError, match=expected_camel):
            build_app(
                core_dir,
                manifests_dir,
                "profile_2",
                "DIGITAL_APP",
                "prod",
                tmp_path / f"out_nested_{expected_camel}",
            )


@pytest.mark.parametrize(
    ("section_key", "bad_value"),
    [
        ("languageSettings", '"not_a_dict"'),
        ("audioProcessingConfig", "[1, 2]"),
    ],
)
def test_env_app_non_dict_profile_sections_raise(
    tmp_path: Path, section_key: str, bad_value: str
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    (core_dir / "app.json").write_text(
        json.dumps({"displayName": "Valid App", "rootAgent": "default_agent"}),
        encoding="utf-8",
    )
    (manifests_dir / "envs" / "prod.yaml").write_text(
        "environment: prod\n"
        "templates:\n"
        "  api: https://api.example.com/{profile_slug}/{env}/{service}\n"
        "app:\n"
        f"  {section_key}: {bad_value}\n",
        encoding="utf-8",
    )

    with pytest.raises(BuildError, match=rf"{section_key}.*mapping"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / f"out_bad_{section_key}",
        )


def test_env_app_path_escape_and_dual_app_configs_validated(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    (core_dir / "app.json").write_text(
        json.dumps({"displayName": "Valid App", "rootAgent": "default_agent"}),
        encoding="utf-8",
    )
    (manifests_dir / "envs" / "prod.yaml").write_text(
        "environment: prod\n"
        "templates:\n"
        "  api: https://api.example.com/{profile_slug}/{env}/{service}\n"
        "app:\n"
        '  globalInstruction: "../manifests/envs/prod.yaml"\n',
        encoding="utf-8",
    )
    with pytest.raises(BuildError, match="escapes app directory"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_path_escape",
        )

    # When both app.json and app.yaml exist in core, both must be validated.
    (core_dir / "app.yaml").write_text(
        "displayName: YAML App\n"
        "rootAgent: default_agent\n"
        "bogusYamlField: 123\n",
        encoding="utf-8",
    )
    (manifests_dir / "envs" / "prod.yaml").write_text(
        "environment: prod\n"
        "templates:\n"
        "  api: https://api.example.com/{profile_slug}/{env}/{service}\n"
        "app:\n"
        "  description: Merged\n",
        encoding="utf-8",
    )
    with pytest.raises(BuildError, match=r"app\.yaml.*bogusYamlField"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_dual_configs",
        )


def test_indented_block_scalar_and_flow_yaml_reject_dynamic_identity_conditionals(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)

    # 2-space indented YAML with dynamic conditional on description & nested
    # parameters.properties.name must build cleanly.
    ok_tool = core_dir / "tools" / "indented_ok_tool"
    ok_tool.mkdir()
    (ok_tool / "indented_ok_tool.yaml").write_text(
        "  displayName: Indented Ok Tool\n"
        "  {@startToolEnabled_active_tool}\n"
        "  description: Uses active tool\n"
        "  parameters:\n"
        "    properties:\n"
        "      name: string\n"
        "  {@endToolEnabled_active_tool}\n",
        encoding="utf-8",
    )
    out_ok = tmp_path / "out_indented_ok"
    build_app(
        core_dir, manifests_dir, "profile_1", "VOICE_TELEPHONY", "prod", out_ok
    )
    rendered_ok = yaml.safe_load(
        (
            out_ok / "tools" / "indented_ok_tool" / "indented_ok_tool.yaml"
        ).read_text("utf-8")
    )
    assert rendered_ok["displayName"] == "Indented Ok Tool"
    assert rendered_ok["description"] == "Uses active tool"
    assert rendered_ok["parameters"]["properties"]["name"] == "string"

    # Indented top-level displayName, block scalar displayName, and flow-mapping
    # root displayName wrapped in dynamic conditionals must all fail fast.
    bad_cases = [
        (
            "indented_ident",
            "  {@startToolEnabled_active_tool}\n"
            "  displayName: Indented Dynamic\n"
            "  {@endToolEnabled_active_tool}\n"
            "  description: test\n",
        ),
        (
            "block_scalar_ident",
            "displayName: |\n"
            "  {@startToolEnabled_active_tool}\n"
            "  Block Scalar Name\n"
            "  {@endToolEnabled_active_tool}\n",
        ),
        (
            "flow_root_ident",
            "{\n"
            "{@startToolEnabled_active_tool}\n"
            '  "displayName": "Flow Dynamic",\n'
            "{@endToolEnabled_active_tool}\n"
            '  "description": "test"\n'
            "}\n",
        ),
    ]
    for folder_name, yaml_content in bad_cases:
        bad_dir = core_dir / "tools" / folder_name
        bad_dir.mkdir()
        (bad_dir / f"{folder_name}.yaml").write_text(
            yaml_content, encoding="utf-8"
        )
        with pytest.raises(BuildError, match="not allowed inside resource"):
            build_app(
                core_dir,
                manifests_dir,
                "profile_1",
                "VOICE_TELEPHONY",
                "prod",
                tmp_path / f"out_{folder_name}",
            )
        shutil.rmtree(bad_dir)

    # Illegal dynamic conditional must not be swallowed even on a disabled tool.
    (core_dir / "tools" / "secret_tool_dir" / "secret_tool_dir.json").unlink()
    (
        core_dir / "tools" / "secret_tool_dir" / "secret_tool_dir.yaml"
    ).write_text(
        "  {@startToolEnabled_active_tool}\n"
        "  displayName: secret_tool_disabled\n"
        "  {@endToolEnabled_active_tool}\n",
        encoding="utf-8",
    )
    with pytest.raises(BuildError, match="not allowed inside resource"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "VOICE_TELEPHONY",
            "prod",
            tmp_path / "out_disabled_illegal_cond",
        )


def test_root_agent_must_exist_in_surviving_agents_and_cannot_be_null_or_missing(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)

    # Overlay setting rootAgent to non-existent agent fails without entry_agent.
    (manifests_dir / "envs" / "prod.yaml").write_text(
        "environment: prod\n"
        "templates:\n"
        "  api: https://api.example.com/{profile_slug}/{env}/{service}\n"
        "app:\n"
        "  rootAgent: ghost_agent\n",
        encoding="utf-8",
    )
    with pytest.raises(BuildError, match="does not match any surviving agent"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_ghost_root",
        )

    # Overlay deleting rootAgent via explicit null fails without entry_agent.
    (manifests_dir / "envs" / "prod.yaml").write_text(
        "environment: prod\n"
        "templates:\n"
        "  api: https://api.example.com/{profile_slug}/{env}/{service}\n"
        "app:\n"
        "  rootAgent: null\n",
        encoding="utf-8",
    )
    with pytest.raises(BuildError, match="rootAgent cannot be null"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_null_root",
        )

    # Missing rootAgent in core/app.json fails when agents exist and no entry_agent.
    (manifests_dir / "envs" / "prod.yaml").write_text(
        "environment: prod\n"
        "templates:\n"
        "  api: https://api.example.com/{profile_slug}/{env}/{service}\n",
        encoding="utf-8",
    )
    (core_dir / "app.json").write_text(
        json.dumps({"displayName": "No Root App"}), encoding="utf-8"
    )
    with pytest.raises(BuildError, match="missing 'rootAgent'"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_missing_root",
        )

    # Empty string rootAgent in core/app.json fails without entry_agent.
    (core_dir / "app.json").write_text(
        json.dumps({"displayName": "Blank Root App", "rootAgent": ""}),
        encoding="utf-8",
    )
    with pytest.raises(BuildError, match="rootAgent cannot be empty or null"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_blank_root",
        )


def test_always_validates_final_app_config_and_attributes_blame_accurately(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    (core_dir / "app.json").unlink()

    # 1. Invalid snake_case key in core/app.yaml fails BOTH without and with
    # an env app: overlay, blaming core/app.yaml in both cases.
    (core_dir / "app.yaml").write_text(
        "displayName: Core App\n"
        "rootAgent: default_agent\n"
        "root_agent: default_agent\n",
        encoding="utf-8",
    )
    with pytest.raises(BuildError) as exc_no_overlay:
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "VOICE_TELEPHONY",
            "prod",
            tmp_path / "out_core_snake_no_overlay",
        )
    assert "core/app.yaml" in str(exc_no_overlay.value)
    assert "envs/prod.yaml" not in str(exc_no_overlay.value)

    (manifests_dir / "envs" / "prod.yaml").write_text(
        "environment: prod\n"
        "templates:\n"
        "  api: https://api.example.com/{profile_slug}/{env}/{service}\n"
        "app:\n"
        "  description: Valid env description\n",
        encoding="utf-8",
    )
    with pytest.raises(BuildError) as exc_with_overlay:
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "VOICE_TELEPHONY",
            "prod",
            tmp_path / "out_core_snake_with_overlay",
        )
    assert "core/app.yaml" in str(exc_with_overlay.value)
    assert "envs/prod.yaml" not in str(exc_with_overlay.value)

    # 2. Missing displayName in core/app.yaml fails in both envs, blaming core.
    (core_dir / "app.yaml").write_text(
        "rootAgent: default_agent\n", encoding="utf-8"
    )
    with pytest.raises(BuildError) as exc_missing_dn:
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_missing_dn",
        )
    assert "core/app.yaml" in str(exc_missing_dn.value)
    assert "envs/prod.yaml" not in str(exc_missing_dn.value)

    # 3. Valid core/app.yaml + invalid envs/prod.yaml overlay blames envs/prod.yaml.
    (core_dir / "app.yaml").write_text(
        "displayName: Core App\nrootAgent: default_agent\n", encoding="utf-8"
    )
    for bad_overlay in (
        "  displayName: null\n",
        "  time_zone_settings:\n    timeZone: UTC\n",
    ):
        (manifests_dir / "envs" / "prod.yaml").write_text(
            "environment: prod\n"
            "templates:\n"
            "  api: https://api.example.com/{profile_slug}/{env}/{service}\n"
            f"app:\n{bad_overlay}",
            encoding="utf-8",
        )
        with pytest.raises(BuildError) as exc_overlay:
            build_app(
                core_dir,
                manifests_dir,
                "profile_1",
                "DIGITAL_APP",
                "prod",
                tmp_path / "out_bad_overlay",
            )
        assert "envs/prod.yaml" in str(exc_overlay.value)
        assert "core/app.yaml" not in str(exc_overlay.value)


def test_duplicate_sibling_resource_identifiers_fail_fast(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)

    # Tool folder tool_a vs sibling tool_b whose displayName is also "tool_a".
    tool_a = core_dir / "tools" / "tool_a"
    tool_a.mkdir()
    (tool_a / "tool_a.json").write_text(
        json.dumps({"displayName": "tool_a"}), encoding="utf-8"
    )
    tool_b = core_dir / "tools" / "tool_b"
    tool_b.mkdir()
    (tool_b / "tool_b.json").write_text(
        json.dumps({"displayName": "tool_a"}), encoding="utf-8"
    )
    pol_path = manifests_dir / "profiles" / "profile_1" / "channel_policy.yaml"
    pol = yaml.safe_load(pol_path.read_text("utf-8"))
    pol["channels"]["VOICE_TELEPHONY"]["disabled_tools"].append("tool_a")
    pol_path.write_text(yaml.safe_dump(pol, sort_keys=False), encoding="utf-8")

    with pytest.raises(BuildError, match="Ambiguous tools identifier 'tool_a'"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "VOICE_TELEPHONY",
            "prod",
            tmp_path / "out_dup_tool",
        )

    shutil.rmtree(tool_a)
    shutil.rmtree(tool_b)

    # Two sibling agents sharing the same displayName must also fail fast.
    ag_a = core_dir / "agents" / "agent_a"
    ag_a.mkdir()
    (ag_a / "agent_a.json").write_text(
        json.dumps({"displayName": "Shared Agent"}), encoding="utf-8"
    )
    ag_b = core_dir / "agents" / "agent_b"
    ag_b.mkdir()
    (ag_b / "agent_b.json").write_text(
        json.dumps({"displayName": "Shared Agent"}), encoding="utf-8"
    )
    with pytest.raises(
        BuildError, match="Ambiguous agents identifier 'Shared Agent'"
    ):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "VOICE_TELEPHONY",
            "prod",
            tmp_path / "out_dup_agent",
        )


def test_validate_app_config_data_does_not_read_cwd_or_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cxas_scrapi.utils.lint_rules.schema import (  # noqa: PLC0415
        validate_app_config_data,
    )

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    leaked_file = outside_dir / "cwd_leak.txt"
    leaked_file.write_text("SECRET FROM CWD\n", encoding="utf-8")

    app_root = tmp_path / "app"
    app_root.mkdir()
    monkeypatch.chdir(outside_dir)

    errors = validate_app_config_data(
        {"displayName": "Valid App", "globalInstruction": "cwd_leak.txt"},
        app_root,
    )
    assert errors, "Expected missing-file error instead of CWD fallback"
    assert any("Missing referenced file" in e for e in errors)

    sym_path = app_root / "sym_instruction.txt"
    sym_path.symlink_to(leaked_file)
    sym_errors = validate_app_config_data(
        {
            "displayName": "Valid App",
            "globalInstruction": "sym_instruction.txt",
        },
        app_root,
    )
    assert any("escapes app directory" in e for e in sym_errors)


@pytest.mark.parametrize(
    "tool_yaml",
    [
        (
            "_base: &base\n"
            "  other: 1\n"
            "  {@startToolEnabled_active_tool}\n"
            "  displayName: secret_tool_disabled\n"
            "  {@endToolEnabled_active_tool}\n"
            "<<: *base\n"
            "description: Ok\n"
        ),
        (
            "_base: &base\n"
            "  displayName: secret_tool_disabled\n"
            "{@startToolEnabled_active_tool}\n"
            "<<: *base\n"
            "{@endToolEnabled_active_tool}\n"
            "description: Ok\n"
        ),
        (
            "{@startToolEnabled_active_tool}\n"
            "? displayName\n"
            ": secret_tool_disabled\n"
            "{@endToolEnabled_active_tool}\n"
            "description: Ok\n"
        ),
    ],
)
def test_yaml_anchor_merge_and_explicit_key_identity_conditionals_rejected(
    tmp_path: Path, tool_yaml: str
) -> None:
    core_dir, manifests_dir = _setup_sample_workspace(tmp_path)
    (core_dir / "tools" / "active_tool" / "active_tool.json").unlink()
    (core_dir / "tools" / "active_tool" / "active_tool.yaml").write_text(
        tool_yaml, encoding="utf-8"
    )

    with pytest.raises(BuildError, match=r"Conditional tag|identity"):
        build_app(
            core_dir,
            manifests_dir,
            "profile_1",
            "VOICE_TELEPHONY",
            "prod",
            tmp_path / "out_anchor_identity_cond",
        )
