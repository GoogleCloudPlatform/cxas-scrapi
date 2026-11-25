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

"""Unit tests for post-build integrity lint rules (T020-T021, A018, S018-S020)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

from cxas_scrapi.utils.lint_rules.config import VoiceLanguageTtsParity
from cxas_scrapi.utils.lint_rules.structure import (
    AgentRefSelfOrOrphan,
    DeploymentEntryAgentParity,
    ProdUrlSafety,
)
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


# ── Item 0: deployments/*.yml discovery ─────────────────────────────────


def test_run_rules_discovers_deployments_yml_and_skips_symlinks(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.yml"
    outside.write_text(
        "brandId: brand_a\nsupportedCountries: [XX]\nsupportedLanguages: [xx]\n",
        encoding="utf-8",
    )
    app = tmp_path / "app"
    _write_json(app / "app.json", {"displayName": "app", "rootAgent": "root"})
    _write_json(
        app / "agents" / "root" / "root.json",
        {"displayName": "root", "tools": ["end_session"]},
    )
    (app / "agents" / "root" / "instruction.txt").write_text(
        "<role>Hi.</role>\n", encoding="utf-8"
    )
    _write_yaml(
        app / "manifests" / "locales" / "content_blocks.yaml",
        {"blocks": {"brand_a": {"DE": {"de": {"hi": "Hallo"}}}}},
    )
    _write_yaml(
        app / "deployments" / "web.yml",
        {
            "brandId": "brand_a",
            "supportedCountries": ["DE"],
            "supportedLanguages": ["fr"],
        },
    )
    os.symlink(outside, app / "deployments" / "escape.yml")

    rep = _run_specific(app, {"G002"})
    assert len(rep.results) == 1
    assert rep.results[0].file == "deployments/web.yml"
    assert "lang=fr" in rep.results[0].message


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


# ── S018: deployments/*.{yaml,yml} entryAgent existence & parity ────────


def test_s018_deployment_entry_agent_missing_and_divergent(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root_agent"}
    )
    _write_json(
        tmp_path / "agents" / "root_agent" / "root_agent.json",
        {"displayName": "Root Agent", "childAgents": ["alt_entry"]},
    )
    _write_json(
        tmp_path / "agents" / "alt_entry" / "alt_entry.json",
        {"displayName": "Alt Entry"},
    )
    _write_yaml(
        tmp_path / "deployments" / "ok.yaml", {"entryAgent": "Root Agent"}
    )
    _write_yaml(
        tmp_path / "deployments" / "diverge.yml", {"entry_agent": "Alt Entry"}
    )
    _write_yaml(
        tmp_path / "deployments" / "broken.yaml", {"entryAgent": "ghost_agent"}
    )

    rep = _run_specific(tmp_path, {"S018"})
    errors = [r for r in rep.results if r.severity == Severity.ERROR]
    warnings = [r for r in rep.results if r.severity == Severity.WARNING]
    assert len(errors) == 1
    assert errors[0].file == "deployments/broken.yaml"
    assert "ghost_agent" in errors[0].message
    assert len(warnings) == 1
    assert warnings[0].file == "deployments/diverge.yml"
    assert "diverges from app rootAgent" in warnings[0].message
    assert DeploymentEntryAgentParity().check(
        tmp_path / "app.json", "", _ctx(tmp_path)
    )


# ── S019: Non-Prod / Cleartext URLs in prod Builds ──────────────────────


def test_s019_prod_url_safety_cleartext_loopback_and_forbidden_patterns(
    tmp_path: Path,
) -> None:
    app_json = _write_json(tmp_path / "app.json", {"displayName": "app"})
    _write_yaml(tmp_path / "deployments" / "dev.yaml", {"environment": "dev"})
    _write_json(
        tmp_path / "environment.json",
        {
            "tools": {
                "api_clear": {"url": "http://api.example.com/v1"},
                "api_local": {"url": "https://localhost:8443/v1"},
                "api_ipv6": {"url": "https://[::1]/v1"},
                "api_dotlocal": {"url": "https://svc.internal.local/v1"},
                "api_staging": {"url": "https://staging.example.com/v1"},
                "api_device_ok": {"url": "https://device.example.com/v1"},
            }
        },
    )
    _write_yaml(
        tmp_path / "tools" / "t1" / "open_api_tool" / "open_api_schema.yaml",
        {
            "openapi": "3.0.0",
            "servers": [
                {"url": "$env_var"},
                {"url": "http://127.0.0.1:9000/api"},
            ],
            "paths": {
                "/x": {"get": {"responses": {"200": {"description": "ok"}}}}
            },
        },
    )

    opts = {"S019": {"forbidden_url_patterns": ["staging.", "dev.", "uat."]}}

    # Inactive when only dev deployment exists
    assert (
        ProdUrlSafety().check(app_json, "", _ctx(tmp_path, options=opts)) == []
    )

    # Active when prod deployment exists
    _write_yaml(tmp_path / "deployments" / "prod.yml", {"environment": "PROD"})
    results = ProdUrlSafety().check(app_json, "", _ctx(tmp_path, options=opts))
    assert all(r.severity == Severity.ERROR for r in results)
    msgs = "\n".join(r.message for r in results)
    assert "http://api.example.com/v1" in msgs
    assert "https://localhost:8443/v1" in msgs
    assert "https://[::1]/v1" in msgs
    assert "https://svc.internal.local/v1" in msgs
    assert "https://staging.example.com/v1" in msgs
    assert "http://127.0.0.1:9000/api" in msgs
    # "dev." literal pattern must NOT false-positive on "device.example.com"!
    assert "device.example.com" not in msgs
    assert "$env_var" not in msgs


# ── S020: {@AGENT:} Self-Transfer or Orphaned-Agent Target ──────────────


def test_s020_self_transfer_and_orphaned_agent_target(tmp_path: Path) -> None:
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
        r.rule_id == "S020" and r.severity == Severity.WARNING for r in results
    )
    by_line = {r.line: r.message for r in results}
    assert "self-transfer" in by_line[2]
    assert "orphaned/unreachable agent 'orphan_agent'" in by_line[3]
    assert all(r.file == "agents/root_agent/custom_prompt.txt" for r in results)


# ── Sample_Support_Agent Regression Guard ───────────────────────────────


def test_sample_support_agent_clean_with_all_120_rules() -> None:
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
    assert len(registry.all_rules()) >= 120  # noqa: PLR2004
    report = LintReport()
    run_rules(registry, cfg, ctx, discovery, report)
    assert report.errors == []
    assert report.warnings == []
