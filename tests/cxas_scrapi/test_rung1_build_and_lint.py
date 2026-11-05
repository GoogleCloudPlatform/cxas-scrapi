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

import argparse
import filecmp
import json
import zipfile
from pathlib import Path

import pytest

from cxas_scrapi.build.compiler import BuildError, build_all, build_app
from cxas_scrapi.build.manifests import load_manifests
from cxas_scrapi.cli.build_cli import app_build, app_package
from cxas_scrapi.core.packaging import package_app_archive
from cxas_scrapi.utils.linter import (
    Discovery,
    LintConfig,
    LintContext,
    LintReport,
    build_registry,
    run_rules,
)


def test_manifest_validation(tmp_path: Path) -> None:
    brands_dir = tmp_path / "brands" / "brand_alpha"
    brands_dir.mkdir(parents=True)
    with open(brands_dir / "identity.yaml", "w") as f:
        f.write("brand_id: brand_alpha\ndisplay_name: Cymbal Alpha\n")
    with open(brands_dir / "channel_policy.yaml", "w") as f:
        f.write(
            "brand_id: brand_alpha\n"
            "channels:\n"
            "  WEB:\n"
            "    supported_modalities: ['TEXT']\n"
        )
    envs_dir = tmp_path / "envs"
    envs_dir.mkdir(parents=True)
    with open(envs_dir / "prod.yaml", "w") as f:
        f.write(
            "environment: prod\n"
            "templates:\n"
            "  gcp_project: cymbal-prod\n"
            "gcp_project_template: cymbal-prod-from-root\n"
        )
    manifests = load_manifests(tmp_path)
    assert manifests.brand("brand_alpha").display_name == "Cymbal Alpha"
    assert (
        manifests.env("prod").templates.get("gcp_project")
        == "cymbal-prod-from-root"
    )


def test_inline_modality_rejection(tmp_path: Path) -> None:
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    brands_dir = manifests_dir / "brands" / "brand_alpha"
    brands_dir.mkdir(parents=True)
    with open(brands_dir / "identity.yaml", "w") as f:
        f.write("brand_id: brand_alpha\ndisplay_name: Cymbal Alpha\n")
    with open(brands_dir / "channel_policy.yaml", "w") as f:
        f.write(
            "brand_id: brand_alpha\n"
            "channels:\n"
            "  WEB:\n"
            "    supported_modalities: ['TEXT']\n"
        )
    envs_dir = manifests_dir / "envs"
    envs_dir.mkdir(parents=True)
    with open(envs_dir / "prod.yaml", "w") as f:
        f.write("environment: prod\n")
    with open(core_dir / "global_instruction.txt", "w") as f:
        f.write(
            "Some text {@startModality_Voice}inline{@endModality_Voice} bad.\n"
        )
    out_dir = tmp_path / "out"
    with pytest.raises(BuildError, match="must be on its own line"):
        build_app(
            core_dir, manifests_dir, "brand_alpha", "WEB", "prod", out_dir
        )


def test_package_app_archive_and_env(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "app.json").write_text("{}")
    python_env_dir = app_dir / "pythonEnvFiles"
    python_env_dir.mkdir()
    (python_env_dir / "requirements.txt").write_text("pytest")
    env_file = tmp_path / "my_env.json"
    env_file.write_text("{}")
    zip_path = tmp_path / "out.zip"
    package_app_archive(app_dir, zip_path, env_file)
    with zipfile.ZipFile(zip_path, "r") as z:
        names = z.namelist()
        assert "app.json" in names
        assert "pythonEnvFiles/requirements.txt" in names
        assert "environment.json" in names


def _setup_complex_compilation(tmp_path: Path) -> tuple[Path, Path]:
    core_dir = tmp_path / "core"
    core_dir.mkdir()

    app_json = {
        "rootAgent": "default_agent",
        "variableDeclarations": [{"name": "var1"}],
    }
    (core_dir / "app.json").write_text(json.dumps(app_json))

    (core_dir / "environment.json").write_text(json.dumps({"some_key": "val1"}))
    (core_dir / "global_instruction.txt").write_text(
        "Instruction for @@brand.display_name@@. URL: @@env.api:auth@@"
    )

    agents_dir = core_dir / "agents"
    agents_dir.mkdir()
    a1_dir = agents_dir / "default_agent"
    a1_dir.mkdir()
    (a1_dir / "instruction.txt").write_text(
        "Call {@TOOL: secret_tool_disabled}."
    )
    (a1_dir / "default_agent.json").write_text(
        json.dumps(
            {
                "displayName": "default_agent",
                "childAgents": ["pruned_agent_display"],
                "tools": ["secret_tool_disabled"],
            }
        )
    )
    a2_dir = agents_dir / "pruned_agent_dir"
    a2_dir.mkdir()
    (a2_dir / "pruned_agent_dir.json").write_text(
        json.dumps({"displayName": "pruned_agent_display"})
    )

    tools_dir = core_dir / "tools"
    tools_dir.mkdir()
    t1_dir = tools_dir / "secret_tool_dir"
    t1_dir.mkdir()
    (t1_dir / "secret_tool_dir.json").write_text(
        json.dumps({"displayName": "secret_tool_disabled"})
    )

    gr_dir = core_dir / "guardrails" / "my_guardrail"
    gr_dir.mkdir(parents=True)
    (gr_dir / "my_guardrail.json").write_text(
        json.dumps(
            {"llmPolicy": {"prompt": "Guardrail for @@brand.display_name@@."}}
        )
    )

    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()

    b_alpha = manifests_dir / "brands" / "brand_alpha"
    b_alpha.mkdir(parents=True)
    (b_alpha / "identity.yaml").write_text(
        "brand_id: brand_alpha\n"
        "display_name: Cymbal Alpha\n"
        "tts_voices:\n"
        "  en:\n"
        "    voice: en-US-Standard-A\n"
        "    speaking_rate: 1.0\n"
    )
    (b_alpha / "channel_policy.yaml").write_text(
        "brand_id: brand_alpha\n"
        "channels:\n"
        "  VOICE_TELEPHONY:\n"
        "    entry_agent: custom_entry\n"
        "    disabled_agents: [pruned_agent_display]\n"
        "    disabled_tools: [secret_tool_disabled]\n"
        "    supported_modalities: ['AUDIO']\n"
        "  DIGITAL_APP:\n"
        "    supported_modalities: ['TEXT']\n"
    )

    b_beta = manifests_dir / "brands" / "brand_beta"
    b_beta.mkdir(parents=True)
    (b_beta / "identity.yaml").write_text(
        "brand_id: brand_beta\ndisplay_name: Cymbal Beta\n"
    )
    (b_beta / "channel_policy.yaml").write_text(
        "brand_id: brand_beta\n"
        "channels:\n"
        "  DIGITAL_APP:\n"
        "    supported_modalities: ['TEXT']\n"
    )

    envs_dir = manifests_dir / "envs"
    envs_dir.mkdir(parents=True)
    (envs_dir / "prod.yaml").write_text(
        "environment: prod\n"
        "templates:\n"
        "  api: https://api.cymbal.com/{service}\n"
        "overrides:\n"
        "  overridden_key: prod_val\n"
    )

    return core_dir, manifests_dir


def test_comprehensive_compilation_and_determinism(tmp_path: Path) -> None:
    core_dir, manifests_dir = _setup_complex_compilation(tmp_path)

    out_root = tmp_path / "out1"
    reports = build_all(core_dir, manifests_dir, out_root, "prod")
    assert len(reports) == 3

    alpha_voice = out_root / "brand_alpha" / "voice_telephony"
    assert alpha_voice.exists()

    # Assert pruning by displayName
    assert not (alpha_voice / "agents" / "pruned_agent_dir").exists()
    assert not (alpha_voice / "tools" / "secret_tool_dir").exists()

    # Assert JSON edits
    a_json = json.loads((alpha_voice / "app.json").read_text("utf-8"))
    assert (
        a_json["rootAgent"] == "default_agent"
    )  # Because default_agent was NOT pruned, and we don't unconditionally override

    # Wait, in the setup default_agent was NOT disabled, but custom_entry is specified in channel policy!
    # The rule is: do NOT unconditionally overwrite rootAgent if it exists and is NOT pruned.
    # So it should be default_agent.
    assert "synthesizeSpeechConfigs" in a_json["audioProcessingConfig"]
    assert "en" in a_json["audioProcessingConfig"]["synthesizeSpeechConfigs"]
    assert a_json["languageSettings"]["defaultLanguageCode"] == "en"

    d_json = json.loads(
        (
            alpha_voice / "agents" / "default_agent" / "default_agent.json"
        ).read_text("utf-8")
    )
    assert not d_json.get("childAgents")
    assert not d_json.get("tools")

    e_json = json.loads((alpha_voice / "environment.json").read_text("utf-8"))
    assert e_json["some_key"] == "val1"
    assert e_json["overridden_key"] == "prod_val"

    g_json = json.loads(
        (
            alpha_voice / "guardrails" / "my_guardrail" / "my_guardrail.json"
        ).read_text("utf-8")
    )
    assert g_json["llmPolicy"]["prompt"] == "Guardrail for Cymbal Alpha."

    gi = (alpha_voice / "global_instruction.txt").read_text("utf-8")
    assert "Instruction for Cymbal Alpha" in gi
    assert "URL: https://api.cymbal.com/auth" in gi

    # Assert warning on stale @TOOL
    r_alpha_voice = next(r for r in reports if r.channel == "VOICE_TELEPHONY")
    assert any(
        "mentions pruned tool secret_tool_disabled" in w
        for w in r_alpha_voice.warnings
    )

    # Determinism check
    out_root2 = tmp_path / "out2"
    build_all(core_dir, manifests_dir, out_root2, "prod")

    def compare_dirs(d1: Path, d2: Path) -> None:
        dcmp = filecmp.dircmp(d1, d2)
        assert not dcmp.left_only
        assert not dcmp.right_only
        assert not dcmp.diff_files
        for sub in dcmp.common_dirs:
            compare_dirs(d1 / sub, d2 / sub)

    compare_dirs(out_root, out_root2)


def test_compiled_app_lint(tmp_path: Path) -> None:
    core_dir, manifests_dir = _setup_complex_compilation(tmp_path)
    out_root = tmp_path / "out_lint"
    build_app(
        core_dir,
        manifests_dir,
        "brand_alpha",
        "VOICE_TELEPHONY",
        "prod",
        out_root,
    )

    discovery = Discovery(out_root, out_root / "evals")
    config = LintConfig()
    context = LintContext(
        project_root=out_root,
        app_dir=out_root,
        evals_dir=out_root / "evals",
        app_root=out_root,
    )
    registry = build_registry()
    report = LintReport()

    run_rules(registry, config, context, discovery, report)

    # 0 errors on compiled app (should not crash A001 on deployments)
    errors = [
        e
        for e in report.errors
        if e.rule_id in ("T015", "T016", "T017", "G001", "G002", "V105", "A001")
    ]
    assert not errors


def test_polymorphic_lint_rules(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()

    # T015, T016, T017 - Negative (clean)
    tools_dir = app_dir / "tools" / "ds_search"
    tools_dir.mkdir(parents=True)
    with open(tools_dir / "ds_search.json", "w") as f:
        json.dump(
            {
                "displayName": "ds_search",
                "dataStoreTool": {
                    "filter": "user_id = 1",  # No template -> T015 clean
                    "filterParameterBehavior": "NEVER_INCLUDE",  # T016 clean
                    "modalityConfigs": [
                        {
                            "modalityType": "AUDIO",
                            "snippetsConfig": {"enableSnippets": True},
                            "rewriterConfig": {"disabled": True},
                            "summarizationConfig": {"disabled": True},
                        }
                    ],  # T017 clean
                },
            },
            f,
        )

    # G001 - Negative
    guardrails_dir = app_dir / "guardrails" / "my_guardrail"
    guardrails_dir.mkdir(parents=True)
    with open(guardrails_dir / "my_guardrail.json", "w") as f:
        json.dump({"llmPolicy": {"prompt": "Hello @@brand.name@@!"}}, f)

    # G002 - Negative
    deps_dir = app_dir / "deployments"
    deps_dir.mkdir(parents=True)
    with open(deps_dir / "web.yaml", "w") as f:
        f.write(
            "channelProfile:\n  channelType: GOOGLE_TELEPHONY_PLATFORM\nbrandId: brand_alpha\nsupportedCountries: ['US']\nsupportedLanguages: ['en']\n"
        )

    manifests_locales = app_dir / "manifests" / "locales"
    manifests_locales.mkdir(parents=True)
    with open(manifests_locales / "content_blocks.yaml", "w") as f:
        f.write(
            "blocks:\n  brand_alpha:\n    US:\n      en: { greeting: Hello }\n"
        )

    # V105 - Negative
    app_json = app_dir / "app.json"
    with open(app_json, "w") as f:
        json.dump(
            {"variableDeclarations": [{"name": f"var{i}"} for i in range(10)]},
            f,
        )

    discovery = Discovery(app_dir, app_dir / "evals")
    config = LintConfig()
    context = LintContext(
        project_root=app_dir,
        app_dir=app_dir,
        evals_dir=app_dir / "evals",
        app_root=app_dir,
        options={"max_variables_per_app": 100},
    )
    context.all_tool_names = {"ds_search"}
    registry = build_registry()
    report = LintReport()

    run_rules(registry, config, context, discovery, report)
    rule_ids = {issue.rule_id for issue in report.results}
    assert "T015" not in rule_ids
    assert "T016" not in rule_ids
    assert "T017" not in rule_ids
    assert "G001" not in rule_ids
    assert "G002" not in rule_ids
    assert "V105" not in rule_ids

    # Now make them positive (fail)
    with open(tools_dir / "ds_search.json", "w") as f:
        json.dump(
            {
                "displayName": "ds_search",
                "dataStoreTool": {
                    "filter": "user_id = {user}",  # T015 fail
                    "filterParameterBehavior": "INCLUDE",  # T016 fail
                    "modalityConfigs": [
                        {"modalityType": "AUDIO"}
                    ],  # T017 fail (missing snippets)
                },
            },
            f,
        )
    with open(guardrails_dir / "my_guardrail.json", "w") as f:
        json.dump(
            {"llmPolicy": {"prompt": "Hello {{ name }}!"}}, f
        )  # G001 fail
    with open(deps_dir / "web.yaml", "w") as f:
        f.write(
            "channelProfile:\n  channelType: GOOGLE_TELEPHONY_PLATFORM\nbrandId: brand_alpha\nsupportedCountries: ['US']\nsupportedLanguages: ['fr']\n"
        )  # G002 fail
    with open(app_json, "w") as f:
        json.dump(
            {"variableDeclarations": [{"name": f"var{i}"} for i in range(85)]},
            f,
        )  # V105 fail

    report2 = LintReport()
    run_rules(registry, config, context, discovery, report2)
    rule_ids2 = {issue.rule_id for issue in report2.results}
    assert "T015" in rule_ids2
    assert "T016" in rule_ids2
    assert "T017" in rule_ids2
    assert "G001" in rule_ids2
    assert "G002" in rule_ids2
    assert "V105" in rule_ids2


def test_cli_app_build_and_package(tmp_path: Path) -> None:
    core_dir, manifests_dir = _setup_complex_compilation(tmp_path)
    out_dir = tmp_path / "cli_out"
    args = argparse.Namespace(
        core_dir=str(core_dir),
        manifests_dir=str(manifests_dir),
        brand="brand_alpha",
        channel="VOICE_TELEPHONY",
        env="prod",
        out=str(out_dir),
        all=False,
        zip=False,
        json=False,
    )
    app_build(args)
    assert out_dir.exists()

    zip_path = tmp_path / "cli_package.zip"
    args_pkg = argparse.Namespace(
        app_dir=str(out_dir), zip=str(zip_path), env_file=None
    )
    app_package(args_pkg)
    assert zip_path.exists()
