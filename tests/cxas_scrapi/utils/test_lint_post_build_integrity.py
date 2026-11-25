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

"""Unit tests for post-build integrity lint rules (T020-T021, A018, S018)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from cxas_scrapi.utils.lint_rules.config import VoiceLanguageTtsParity
from cxas_scrapi.utils.lint_rules.structure import AgentRefSelfOrOrphan
from cxas_scrapi.utils.lint_rules.tools import (
    OpenApiSchemaValidity,
    ToolToolsetEnvVarParity,
)
from cxas_scrapi.utils.linter import (
    Discovery,
    LintConfig,
    LintContext,
    LintReport,
    Severity,
    build_context,
    build_registry,
    discover_deployment_files,
    is_voice_app,
    run_rules,
)


def _write_json(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _write_yaml(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _ctx(
    app_root: Path,
    *,
    options: dict[str, Any] | None = None,
    env_file: Path | None = None,
) -> LintContext:
    return LintContext(
        project_root=app_root,
        app_dir=app_root,
        evals_dir=app_root / "evals",
        app_root=app_root,
        options=options or {},
        env_file=env_file,
    )


def _run_specific(
    app_root: Path,
    rule_ids: set[str],
    *,
    config: LintConfig | None = None,
) -> LintReport:
    cfg = config or LintConfig()
    disc = Discovery(app_root, app_root / "evals")
    ctx = build_context(app_root, cfg, disc)
    report = LintReport()
    run_rules(build_registry(), cfg, ctx, disc, report, specific_rules=rule_ids)
    return report


# ── Deployment YAML (.yml) discovery ───────────────────────────────────


def test_discover_deployment_files_finds_yml_and_skips_symlinks(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.yml"
    outside.write_text("channel: VOICE_TELEPHONY\n", encoding="utf-8")
    app = tmp_path / "app"
    _write_json(app / "app.json", {"displayName": "app", "rootAgent": "root"})
    _write_yaml(
        app / "deployments" / "web.yml",
        {"channel": "WEB"},
    )
    os.symlink(outside, app / "deployments" / "escape.yml")

    ctx = _ctx(app)
    found = discover_deployment_files(app, ctx)
    assert found == [app / "deployments" / "web.yml"]
    assert is_voice_app(ctx) is False


# ── T020: Tool / Toolset $env_var <-> environment.json parity ───────────


def test_t020_missing_env_var_error_and_dangling_key_warning(
    tmp_path: Path,
) -> None:
    app_json = _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    _write_json(
        tmp_path / "tools" / "billing_api" / "billing_api.json",
        {
            "displayName": "Billing API",
            "openApiTool": {
                "openApiSchema": "tools/billing_api/open_api_tool/open_api_schema.yaml",
                "headers": {"X-Api-Key": "$env_var"},
            },
        },
    )
    _write_yaml(
        tmp_path
        / "tools"
        / "billing_api"
        / "open_api_tool"
        / "open_api_schema.yaml",
        {
            "openapi": "3.0.0",
            "servers": [{"url": "$env_var"}],
            "paths": {
                "/health": {
                    "get": {"responses": {"200": {"description": "ok"}}}
                }
            },
        },
    )
    _write_yaml(
        tmp_path / "toolsets" / "crm_ts" / "crm_ts.yaml",
        {
            "displayName": "CRM Toolset",
            "openApiToolset": {"url": "$env_var"},
        },
    )

    # 1. Missing environment.json -> ERROR for every $env_var placeholder
    res_missing = ToolToolsetEnvVarParity().check(app_json, "", _ctx(tmp_path))
    assert len(res_missing) == 3
    assert all(r.severity == Severity.ERROR for r in res_missing)

    # 2. Partial environment.json + dangling tool & toolset keys via run_rules
    _write_json(
        tmp_path / "environment.json",
        {
            "tools": {
                "Billing API": {
                    "url": "https://api.example.com",
                    "headers": {"X-Api-Key": "secret-val"},
                },
                "stale_tool": {"url": "https://old.example.com"},
            },
            "toolsets": {
                "crm_ts": {"url": ""},  # empty -> ERROR
                "stale_ts": {"url": "https://old-ts.example.com"},
            },
        },
    )
    rep = _run_specific(tmp_path, {"T020"})
    errors = [r for r in rep.results if r.severity == Severity.ERROR]
    warnings = [r for r in rep.results if r.severity == Severity.WARNING]
    assert len(errors) == 1
    assert "crm_ts" in errors[0].message
    assert len(warnings) == 2
    assert {w.file for w in warnings} == {"environment.json"}

    # 3. Clean environment.json -> 0 findings
    _write_json(
        tmp_path / "environment.json",
        {
            "tools": {
                "billing_api": {
                    "url": "https://api.example.com",
                    "headers": {"X-Api-Key": "secret-val"},
                }
            },
            "toolsets": {"crm_ts": {"url": "https://crm.example.com"}},
        },
    )
    assert _run_specific(tmp_path, {"T020"}).results == []


def test_t020_ignores_corrupt_environment_json_cleanly(tmp_path: Path) -> None:
    app_json = _write_json(tmp_path / "app.json", {"displayName": "app"})
    (tmp_path / "environment.json").write_text("{bad json", encoding="utf-8")
    assert ToolToolsetEnvVarParity().check(app_json, "", _ctx(tmp_path)) == []


# ── T021: OpenAPI Schema Validity ───────────────────────────────────────


def test_t021_openapi_schema_valid_and_invalid_cases(tmp_path: Path) -> None:
    app_json = _write_json(tmp_path / "app.json", {"displayName": "app"})

    # Valid tool schema with $env_var and template URL
    _write_yaml(
        tmp_path
        / "tools"
        / "good_api"
        / "open_api_tool"
        / "open_api_schema.yaml",
        {
            "openapi": "3.0.0",
            "servers": [
                {"url": "$env_var"},
                {"url": "https://{env}.example.com/v1"},
                {"url": "/api/v1"},
            ],
            "paths": {
                "/items": {"get": {"responses": {"200": {"description": "ok"}}}}
            },
        },
    )
    assert OpenApiSchemaValidity().check(app_json, "", _ctx(tmp_path)) == []

    # Invalid YAML syntax
    bad_yaml = (
        tmp_path
        / "tools"
        / "bad_yaml"
        / "open_api_tool"
        / "open_api_schema.yaml"
    )
    bad_yaml.parent.mkdir(parents=True)
    bad_yaml.write_text("paths: [\n  unclosed", encoding="utf-8")

    # Empty paths + invalid server URLs in toolset schema
    _write_yaml(
        tmp_path
        / "toolsets"
        / "bad_ts"
        / "open_api_toolset"
        / "open_api_schema.yml",
        {
            "openapi": "3.0.0",
            "servers": [
                {"url": "   "},
                {"url": "not-a-url"},
                {"url": "https://"},
            ],
            "paths": {},
        },
    )

    # Non-UTF-8 schema
    bad_utf8 = (
        tmp_path
        / "tools"
        / "bad_utf8"
        / "open_api_tool"
        / "open_api_schema.yaml"
    )
    bad_utf8.parent.mkdir(parents=True)
    bad_utf8.write_bytes(b"\xff\xfe\x00invalid")

    # Symlink escape should be skipped safely
    outside = tmp_path / "outside.yaml"
    outside.write_text("not_openapi: true\n", encoding="utf-8")
    sym_dir = tmp_path / "tools" / "sym_api" / "open_api_tool"
    sym_dir.mkdir(parents=True)
    os.symlink(outside, sym_dir / "open_api_schema.yaml")

    results = OpenApiSchemaValidity().check(app_json, "", _ctx(tmp_path))
    files = {r.file for r in results}
    assert "tools/bad_yaml/open_api_tool/open_api_schema.yaml" in files
    assert "tools/bad_utf8/open_api_tool/open_api_schema.yaml" in files
    assert "toolsets/bad_ts/open_api_toolset/open_api_schema.yml" in files
    assert all("sym_api" not in f for f in files)
    assert all(r.severity == Severity.ERROR for r in results)
    ts_msgs = [
        r.message
        for r in results
        if r.file == "toolsets/bad_ts/open_api_toolset/open_api_schema.yml"
    ]
    assert len(ts_msgs) == 4  # empty paths + 3 invalid server URLs


# ── A018: Standard Voice / Language / TTS Parity ────────────────────────


def test_a018_language_settings_and_voice_tts_parity(tmp_path: Path) -> None:
    # 1. defaultLanguageCode not in supportedLanguageCodes -> ERROR
    # 2. multiple supported languages with enableMultilingualSupport: false -> WARNING
    # 3. voice app missing TTS entry for de-DE (case-insensitive en-us passes) -> WARNING
    app_yaml = _write_yaml(
        tmp_path / "app.yaml",
        {
            "name": "voice_app",
            "displayName": "Voice App",
            "modelSettings": {"model": "gemini-composite-v1"},
            "languageSettings": {
                "defaultLanguageCode": "fr-FR",
                "supportedLanguageCodes": ["en-US", "de-DE"],
                "enableMultilingualSupport": False,
            },
            "audioProcessingConfig": {
                "synthesizeSpeechConfigs": {
                    "en-us": {"voice": "en-US-Chirp3-HD-Aoede"}
                }
            },
        },
    )
    rep = _run_specific(tmp_path, {"A018"})
    errors = [r for r in rep.results if r.severity == Severity.ERROR]
    warnings = [r for r in rep.results if r.severity == Severity.WARNING]
    assert len(errors) == 1
    assert "fr-FR" in errors[0].message
    assert len(warnings) == 2
    assert any("enableMultilingualSupport" in w.message for w in warnings)
    assert any("Voice app language 'de-DE'" in w.message for w in warnings)
    assert not any("Voice app language 'en-US'" in w.message for w in warnings)

    # Clean voice YAML app -> 0 findings
    _write_yaml(
        app_yaml,
        {
            "name": "voice_app",
            "displayName": "Voice App",
            "modelSettings": {"model": "gemini-composite-v1"},
            "languageSettings": {
                "defaultLanguageCode": "en-US",
                "supportedLanguageCodes": ["en-US", "de-DE"],
                "enableMultilingualSupport": True,
            },
            "audioProcessingConfig": {
                "synthesizeSpeechConfigs": {
                    "en-US": {"voice": "en-US-Chirp3-HD-Aoede"},
                    "de-DE": {"voice": "de-DE-Chirp3-HD-Aoede"},
                }
            },
        },
    )
    assert (
        VoiceLanguageTtsParity().check(
            app_yaml, app_yaml.read_text("utf-8"), _ctx(tmp_path)
        )
        == []
    )


# ── S018: {@AGENT:} Self-Transfer or Orphaned-Agent Target ──────────────


def test_s018_self_transfer_and_orphaned_agent_target(tmp_path: Path) -> None:
    app_json = _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root_agent"}
    )
    _write_json(
        tmp_path / "agents" / "root_agent" / "root_agent.json",
        {
            "displayName": "Root Desk",
            "instruction": "agents/root_agent/custom_prompt.txt",
            "childAgents": ["billing"],
        },
    )
    (tmp_path / "agents" / "root_agent" / "custom_prompt.txt").write_text(
        "Transfer to {@AGENT: billing} for invoices.\n"
        "Never loop to {@AGENT: Root Desk}.\n"
        "Legacy fallback: {@AGENT: Orphan Helper}.\n",
        encoding="utf-8",
    )
    _write_json(
        tmp_path / "agents" / "billing" / "billing.json",
        {"displayName": "Billing"},
    )
    (tmp_path / "agents" / "billing" / "instruction.txt").write_text(
        "Return to {@AGENT: root_agent}.\n", encoding="utf-8"
    )
    _write_json(
        tmp_path / "agents" / "orphan_agent" / "orphan_agent.json",
        {"displayName": "Orphan Helper"},
    )

    results = AgentRefSelfOrOrphan().check(app_json, "", _ctx(tmp_path))
    assert len(results) == 2
    assert all(
        r.rule_id == "S018" and r.severity == Severity.WARNING for r in results
    )
    by_line = {r.line: r.message for r in results}
    assert "self-transfer" in by_line[2]
    assert "orphaned/unreachable agent 'orphan_agent'" in by_line[3]
    assert all(r.file == "agents/root_agent/custom_prompt.txt" for r in results)


# ── Example App Regression Guards ───────────────────────────────────────


def test_sample_support_agent_clean_with_all_rules() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    template_root = (
        repo_root
        / ".agents"
        / "skills"
        / "cxas-agent-foundry"
        / "assets"
        / "project-template"
    )
    cfg = LintConfig()
    discovery = Discovery(template_root / "cxas_app", template_root / "evals")
    ctx = build_context(template_root, cfg, discovery)
    registry = build_registry()
    assert len(registry.all_rules()) == 117  # noqa: PLR2004
    report = LintReport()
    run_rules(registry, cfg, ctx, discovery, report)
    assert report.errors == []
    assert report.warnings == []


def test_self_steering_sample_agent_zero_new_rule_findings() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    sample_root = (
        repo_root
        / ".agents"
        / "skills"
        / "cxas-self-steering-agent"
        / "resources"
        / "sample-agent"
    )
    cfg = LintConfig()
    discovery = Discovery(sample_root, sample_root / "evals")
    ctx = build_context(sample_root, cfg, discovery)
    report = LintReport()
    new_rules = {
        "T017",
        "T018",
        "T019",
        "T020",
        "T021",
        "A018",
        "C024",
        "S014",
        "S015",
        "S016",
        "S017",
        "S018",
        "S019",
    }
    run_rules(
        build_registry(), cfg, ctx, discovery, report, specific_rules=new_rules
    )
    assert report.results == [], [str(r) for r in report.results]


# ── Edge-case & CLI integration coverage ────────────────────────────────


def test_category_dispatch_and_cxaslint_severity_overrides(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "app.json",
        {
            "displayName": "app",
            "rootAgent": "root",
            "languageSettings": {
                "defaultLanguageCode": "fr-FR",
                "supportedLanguageCodes": ["en-US"],
            },
        },
    )
    _write_json(
        tmp_path / "agents" / "root" / "root.json", {"displayName": "root"}
    )
    (tmp_path / "agents" / "root" / "instruction.txt").write_text(
        "Loop {@AGENT: root}.\n", encoding="utf-8"
    )
    _write_json(
        tmp_path / "tools" / "api" / "api.json",
        {"displayName": "api", "openApiTool": {"url": "$env_var"}},
    )
    (tmp_path / "cxaslint.yaml").write_text(
        "rules:\n  T020: warning\n  S018: off\n", encoding="utf-8"
    )
    cfg = LintConfig.load(tmp_path)
    disc = Discovery(tmp_path, tmp_path / "evals")
    ctx = build_context(tmp_path, cfg, disc)
    reg = build_registry()

    for category, expected_id in [
        ("tools", "T020"),
        ("config", "A018"),
        ("structure", None),
    ]:
        rep = LintReport()
        run_rules(reg, cfg, ctx, disc, rep, categories=[category])
        ids = {r.rule_id for r in rep.results}
        if expected_id:
            assert expected_id in ids
        else:
            assert "S018" not in ids
        if category == "tools":
            t020 = [r for r in rep.results if r.rule_id == "T020"]
            assert all(r.severity == Severity.WARNING for r in t020)


def test_t020_nested_empty_env_var_not_masked_and_env_file_override(
    tmp_path: Path,
) -> None:
    app_json = _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    _write_json(
        tmp_path / "tools" / "api_tool" / "api_tool.json",
        {
            "displayName": "api_tool",
            "openApiTool": {"url": "$env_var", "auth": {"url": "$env_var"}},
        },
    )
    _write_json(
        tmp_path / "tools" / "ds_faq" / "ds_faq.json",
        {
            "displayName": "ds_faq",
            "dataStoreTool": {
                "name": "ds_faq",
                "dataStoreSource": {"dataStore": {"name": "$env_var"}},
            },
        },
    )
    custom_env = _write_json(
        tmp_path / "custom_env.json",
        {
            "tools": {
                "api_tool": {
                    "openApiTool": {
                        "url": "https://api.example.com",
                        "auth": {"url": ""},
                    }
                },
                "ds_faq": {"name": "ds_faq"},
            }
        },
    )
    res = ToolToolsetEnvVarParity().check(
        app_json, "", _ctx(tmp_path, env_file=custom_env)
    )
    assert {r.file for r in res if r.severity == Severity.ERROR} == {
        "tools/api_tool/api_tool.json",
        "tools/ds_faq/ds_faq.json",
    }

    # Non-dict environment.json is ignored without crashing
    (tmp_path / "list_env.json").write_text("[]", encoding="utf-8")
    assert (
        ToolToolsetEnvVarParity().check(
            app_json, "", _ctx(tmp_path, env_file=tmp_path / "list_env.json")
        )
        == []
    )


def test_t021_uppercase_scheme_path_traversal_and_malformed_shapes(
    tmp_path: Path,
) -> None:
    outside = _write_yaml(tmp_path / "outside.yaml", {"bad": True})
    app = tmp_path / "app"
    app_json = _write_json(app / "app.json", {"displayName": "app"})

    # Uppercase HTTPS:// accepted (RFC 3986)
    _write_yaml(
        app / "tools" / "upper" / "open_api_tool" / "open_api_schema.yaml",
        {
            "openapi": "3.0.0",
            "servers": [{"url": "HTTPS://API.EXAMPLE.COM/v1"}],
            "paths": {"/ping": {"get": {}}},
        },
    )
    # Path traversal openApiSchema ref must be ignored safely
    _write_json(
        app / "tools" / "escape" / "escape.json",
        {"openApiTool": {"openApiSchema": f"../{outside.name}"}},
    )
    assert OpenApiSchemaValidity().check(app_json, "", _ctx(app)) == []

    # Non-mapping YAML, non-list servers, non-dict server item, malformed IPv6
    (app / "tools" / "list_doc" / "open_api_tool").mkdir(parents=True)
    (
        app / "tools" / "list_doc" / "open_api_tool" / "open_api_schema.yaml"
    ).write_text("- not-a-mapping\n", encoding="utf-8")
    _write_yaml(
        app / "tools" / "bad_srv" / "open_api_tool" / "open_api_schema.yaml",
        {"servers": "not-a-list", "paths": {"/x": {"get": {}}}},
    )
    _write_yaml(
        app / "tools" / "bad_item" / "open_api_tool" / "open_api_schema.yaml",
        {
            "servers": ["str-item", {"url": "https://[::1"}],
            "paths": {"/x": {"get": {}}},
        },
    )
    res = OpenApiSchemaValidity().check(app_json, "", _ctx(app))
    assert len(res) == 4


def test_a018_voice_missing_audio_empty_tts_and_digital_override(
    tmp_path: Path,
) -> None:
    # Voice deployment omitting audioProcessingConfig warns per language
    app_a = tmp_path / "no_audio"
    cfg_a = _write_json(
        app_a / "app.json",
        {
            "displayName": "a",
            "languageSettings": {
                "defaultLanguageCode": "en-US",
                "supportedLanguageCodes": ["en-US", "de-DE"],
                "enableMultilingualSupport": True,
            },
        },
    )
    _write_yaml(
        app_a / "deployments" / "voice.yaml", {"channel": "VOICE_TELEPHONY"}
    )
    assert (
        len(
            VoiceLanguageTtsParity().check(
                cfg_a, cfg_a.read_text(), _ctx(app_a)
            )
        )
        == 2
    )

    # Null / empty {} TTS entries still warn
    app_b = tmp_path / "empty_tts"
    cfg_b = _write_json(
        app_b / "app.json",
        {
            "displayName": "b",
            "languageSettings": {
                "defaultLanguageCode": "en-US",
                "supportedLanguageCodes": ["en-US", "de-DE"],
                "enableMultilingualSupport": True,
            },
            "audioProcessingConfig": {
                "synthesizeSpeechConfigs": {"en-US": None, "de-DE": {}}
            },
        },
    )
    _write_yaml(
        app_b / "deployments" / "voice.yml", {"channel": "VOICE_TELEPHONY"}
    )
    assert (
        len(
            VoiceLanguageTtsParity().check(
                cfg_b, cfg_b.read_text(), _ctx(app_b)
            )
        )
        == 2
    )

    # Digital deployment overrides retained audioProcessingConfig -> 0 warnings
    _write_yaml(app_b / "deployments" / "voice.yml", {"channel": "WEB"})
    assert (
        VoiceLanguageTtsParity().check(cfg_b, cfg_b.read_text(), _ctx(app_b))
        == []
    )


def test_s018_inline_instruction_and_traversal_fallback(tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_text("{@AGENT: root}\n", encoding="utf-8")
    app = tmp_path / "app"
    app_json = _write_json(
        app / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    _write_json(
        app / "agents" / "root" / "root.json",
        {
            "displayName": "root",
            "instruction": "Inline self ref {@AGENT: root}.",
            "childAgents": ["sub"],
        },
    )
    _write_json(
        app / "agents" / "sub" / "sub.json",
        {"displayName": "sub", "instruction": "../secret.txt"},
    )
    res = AgentRefSelfOrOrphan().check(app_json, "", _ctx(app))
    assert len(res) == 1
    assert res[0].rule_id == "S018"
    assert res[0].file == "agents/root/root.json"
    assert "self-transfer" in res[0].message


def test_ignore_tools_glob_suppresses_t020_and_t021(tmp_path: Path) -> None:
    """cxaslint.yaml ignore: ['tools/**'] suppresses T020/T021 findings."""
    _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    _write_json(
        tmp_path / "agents" / "root" / "root.json", {"displayName": "root"}
    )
    _write_json(
        tmp_path / "tools" / "bad_api" / "bad_api.json",
        {"displayName": "bad_api", "openApiTool": {"url": "$env_var"}},
    )
    bad_schema = (
        tmp_path
        / "tools"
        / "bad_api"
        / "open_api_tool"
        / "open_api_schema.yaml"
    )
    bad_schema.parent.mkdir(parents=True)
    bad_schema.write_text("openapi: 3.0.0\npaths: {}\n", encoding="utf-8")

    # Without ignore -> both T020 and T021 fire
    rep_unfiltered = _run_specific(tmp_path, {"T020", "T021"})
    assert {r.rule_id for r in rep_unfiltered.results} == {"T020", "T021"}

    # With ignore: ["tools/**"] -> both T020 and T021 suppressed
    (tmp_path / "cxaslint.yaml").write_text(
        "ignore:\n  - 'tools/**'\n", encoding="utf-8"
    )
    cfg = LintConfig.load(tmp_path)
    rep_ignored = _run_specific(tmp_path, {"T020", "T021"}, config=cfg)
    assert rep_ignored.results == []


def _run_cli_lint(*args: str) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[3]
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(repo_root / "src")
    return subprocess.run(
        [sys.executable, "-m", "cxas_scrapi.cli.main", "lint", *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_cli_list_rules_exit_codes_and_sample_template(tmp_path: Path) -> None:
    listed = _run_cli_lint("--list-rules")
    assert listed.returncode == 0, listed.stderr
    for rule_id in (
        "T017",
        "T018",
        "T019",
        "T020",
        "T021",
        "A015",
        "A018",
        "C024",
        "S014",
        "S015",
        "S016",
        "S017",
        "S018",
        "S019",
    ):
        assert rule_id in listed.stdout

    # Cycle ERROR exits 1
    cyc = tmp_path / "cyc"
    _write_json(
        cyc / "app.json",
        {"name": "cyc", "displayName": "cyc", "rootAgent": "a"},
    )
    _write_json(
        cyc / "agents" / "a" / "a.json",
        {
            "displayName": "a",
            "instruction": "agents/a/instruction.txt",
            "tools": ["end_session"],
            "childAgents": ["a"],
        },
    )
    (cyc / "agents" / "a" / "instruction.txt").write_text("<role>A</role>\n")
    assert (
        _run_cli_lint("--app-dir", str(cyc), "--rule", "S014").returncode == 1
    )

    # Orphan WARNING-only exits 0
    orph = tmp_path / "orph"
    _write_json(
        orph / "app.json",
        {"name": "orph", "displayName": "orph", "rootAgent": "root"},
    )
    for name in ("root", "orphan"):
        _write_json(
            orph / "agents" / name / f"{name}.json",
            {
                "displayName": name,
                "instruction": f"agents/{name}/instruction.txt",
                "tools": ["end_session"],
            },
        )
        (orph / "agents" / name / "instruction.txt").write_text(
            "<role>Hi</role>\n"
        )
    res_orph = _run_cli_lint("--app-dir", str(orph), "--rule", "S015")
    assert res_orph.returncode == 0
    assert "S015" in res_orph.stdout

    # Sample_Support_Agent CLI exits 0 cleanly
    repo_root = Path(__file__).resolve().parents[3]
    sample_dir = (
        repo_root
        / ".agents"
        / "skills"
        / "cxas-agent-foundry"
        / "assets"
        / "project-template"
        / "cxas_app"
    )
    res_sample = _run_cli_lint("--app-dir", str(sample_dir))
    assert res_sample.returncode == 0, res_sample.stdout + res_sample.stderr
