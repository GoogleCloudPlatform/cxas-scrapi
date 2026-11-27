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

"""Unit tests for post-build integrity lint rules (T020-T021, A019, S018)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from cxas_scrapi.utils.env_vars import (
    collect_tool_and_toolset_env_var_placeholders,
    resolve_tool_and_toolset_env_vars_in_dir,
)
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


# ── T020: Stale / dangling environment.json tools & toolsets entries ────


def test_t020_dangling_env_entries_and_valid_resource_matches(
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
                "url": "$env_var",
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

    # 1. Missing default environment.json while $env_var placeholders exist -> 1 WARNING
    missing_env_res = ToolToolsetEnvVarParity().check(
        app_json, "", _ctx(tmp_path)
    )
    assert len(missing_env_res) == 1
    assert missing_env_res[0].severity == Severity.WARNING
    assert missing_env_res[0].file == "environment.json"
    assert "2 placeholder(s)" in missing_env_res[0].message

    # 2. Dangling tool & toolset keys emit WARNINGs on environment.json
    _write_json(
        tmp_path / "environment.json",
        {
            "tools": {
                "Billing API": {"url": "https://api.example.com"},
                "stale_tool": {"url": "https://old.example.com"},
            },
            "toolsets": {
                "crm_ts": {"url": "https://crm.example.com"},
                "stale_ts": {"url": "https://old-ts.example.com"},
            },
        },
    )
    rep = _run_specific(tmp_path, {"T020"})
    assert len(rep.results) == 2
    assert all(r.severity == Severity.WARNING for r in rep.results)
    assert {r.file for r in rep.results} == {"environment.json"}
    assert any("stale_tool" in r.message for r in rep.results)
    assert any("stale_ts" in r.message for r in rep.results)

    # 3. Valid matches by dir name, displayName, and CES resource path -> 0 findings
    _write_json(
        tmp_path / "environment.json",
        {
            "tools": {
                "billing_api": {"url": "https://api.example.com"},
                "Billing API": {"url": "https://api.example.com"},
                "projects/p/locations/l/apps/a/tools/billing_api": {
                    "url": "https://api.example.com"
                },
            },
            "toolsets": {
                "crm_ts": {"url": "https://crm.example.com"},
                "CRM Toolset": {"url": "https://crm.example.com"},
                "projects/p/locations/l/apps/a/toolsets/crm_ts": {
                    "url": "https://crm.example.com"
                },
            },
        },
    )
    assert _run_specific(tmp_path, {"T020"}).results == []


def test_t020_env_file_override_and_corrupt_env_ignored_cleanly(
    tmp_path: Path,
) -> None:
    app_json = _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    _write_json(
        tmp_path / "tools" / "api_tool" / "api_tool.json",
        {"displayName": "api_tool", "openApiTool": {"url": "$env_var"}},
    )
    custom_env = _write_json(
        tmp_path / "custom_env.json",
        {
            "tools": {
                "api_tool": {"url": "https://api.example.com"},
                "ghost_tool": {"url": "https://ghost.example.com"},
            }
        },
    )
    res = ToolToolsetEnvVarParity().check(
        app_json, "", _ctx(tmp_path, env_file=custom_env)
    )
    assert len(res) == 1
    assert res[0].severity == Severity.WARNING
    assert res[0].file == "custom_env.json"
    assert "ghost_tool" in res[0].message

    # Corrupt JSON and non-dict environment.json are ignored cleanly
    (tmp_path / "environment.json").write_text("{bad json", encoding="utf-8")
    assert ToolToolsetEnvVarParity().check(app_json, "", _ctx(tmp_path)) == []
    (tmp_path / "list_env.json").write_text("[]", encoding="utf-8")
    assert (
        ToolToolsetEnvVarParity().check(
            app_json, "", _ctx(tmp_path, env_file=tmp_path / "list_env.json")
        )
        == []
    )


def test_a018_and_t020_complement_without_duplicate_errors(
    tmp_path: Path,
) -> None:
    """A018 flags missing $env_var keys (ERROR); T020 flags stale keys (WARNING)."""
    _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    _write_json(
        tmp_path / "tools" / "active_api" / "active_api.json",
        {
            "displayName": "active_api",
            "openApiTool": {
                "openApiSchema": "openapi: 3.0.0",
                "url": "$env_var",
            },
        },
    )
    _write_json(
        tmp_path / "environment.json",
        {
            "tools": {
                "stale_tool": {"url": "https://stale.example.com"},
            }
        },
    )
    rep = _run_specific(tmp_path, {"A018", "T020"})
    a018 = [r for r in rep.results if r.rule_id == "A018"]
    t020 = [r for r in rep.results if r.rule_id == "T020"]

    assert len(a018) == 1
    assert a018[0].severity == Severity.ERROR
    assert "active_api" in a018[0].message

    assert len(t020) == 1
    assert t020[0].severity == Severity.WARNING
    assert t020[0].file == "environment.json"
    assert "stale_tool" in t020[0].message


def test_t020_yaml_env_file_mixed_non_string_keys_no_crash(
    tmp_path: Path,
) -> None:
    """T020 must not raise TypeError sorting mixed YAML env keys."""
    _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    env_yaml = tmp_path / "env.yaml"
    env_yaml.write_text(
        "tools:\n  123: {}\n  stale_tool: {}\n", encoding="utf-8"
    )
    disc = Discovery(tmp_path, tmp_path / "evals")
    cfg = LintConfig()
    ctx = build_context(tmp_path, cfg, disc, env_file=env_yaml)
    rep = LintReport()
    run_rules(build_registry(), cfg, ctx, disc, rep, specific_rules={"T020"})
    assert len(rep.results) == 1
    assert rep.results[0].rule_id == "T020"
    assert "stale_tool" in rep.results[0].message


def test_t020_accepts_tool_subobject_name_keys_like_a018(
    tmp_path: Path,
) -> None:
    """T020 must match tool sub-object .name keys accepted by A018."""
    _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    _write_json(
        tmp_path / "tools" / "api_v1" / "api_v1.json",
        {
            "openApiTool": {
                "name": "Billing Sub Name",
                "openApiSchema": "openapi: 3.0.0",
                "url": "$env_var",
            },
        },
    )
    _write_json(
        tmp_path / "environment.json",
        {
            "tools": {
                "Billing Sub Name": {
                    "openApiTool": {"url": "https://api.example.com"}
                },
            }
        },
    )
    rep = _run_specific(tmp_path, {"A018", "T020"})
    assert rep.results == []


def test_a018_discovers_default_location_openapi_schema_placeholder(
    tmp_path: Path,
) -> None:
    """A018 detects $env_var in default open_api_tool/open_api_schema.yaml."""
    _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    _write_json(
        tmp_path / "tools" / "a" / "a.json",
        {"displayName": "a", "openApiTool": {}},
    )
    _write_yaml(
        tmp_path / "tools" / "a" / "open_api_tool" / "open_api_schema.yaml",
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
    _write_json(tmp_path / "environment.json", {})
    rep = _run_specific(tmp_path, {"A018"})
    assert len(rep.results) == 1
    assert rep.results[0].rule_id == "A018"
    assert rep.results[0].severity == Severity.ERROR


def test_env_vars_keeps_and_resolves_both_manifest_url_and_schema_placeholders(
    tmp_path: Path,
) -> None:
    """When both a.json openApiTool.url and open_api_schema.yaml use $env_var,
    both placeholders are kept and rewritten without leaving $env_var behind.
    """
    _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    tool_json = _write_json(
        tmp_path / "tools" / "a" / "a.json",
        {
            "displayName": "a",
            "openApiTool": {
                "openApiSchema": "tools/a/open_api_tool/open_api_schema.yaml",
                "url": "$env_var",
            },
        },
    )
    schema_yaml = _write_yaml(
        tmp_path / "tools" / "a" / "open_api_tool" / "open_api_schema.yaml",
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
    _write_json(
        tmp_path / "environment.json",
        {"tools": {"a": {"openApiTool": {"url": "https://prod.example.com"}}}},
    )

    placeholders = collect_tool_and_toolset_env_var_placeholders(tmp_path)
    assert len(placeholders) == 2
    assert any(p.openapi_schema_path is None for p in placeholders)
    assert any(p.openapi_schema_path == schema_yaml for p in placeholders)

    resolved = resolve_tool_and_toolset_env_vars_in_dir(tmp_path)
    assert resolved == ["a.openApiTool.url"]
    assert "$env_var" not in tool_json.read_text(encoding="utf-8")
    assert "$env_var" not in schema_yaml.read_text(encoding="utf-8")
    assert "https://prod.example.com" in tool_json.read_text(encoding="utf-8")
    assert "https://prod.example.com" in schema_yaml.read_text(encoding="utf-8")


def test_env_vars_explicit_schema_blocks_stale_default_schema_fallback(
    tmp_path: Path,
) -> None:
    """When openApiSchema is explicitly set without $env_var, stale default
    open_api_schema.yaml with $env_var must be ignored and left untouched.
    """
    _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    _write_json(
        tmp_path / "tools" / "a" / "a.json",
        {
            "displayName": "a",
            "openApiTool": {
                "openApiSchema": "tools/a/open_api_tool/current.yaml",
            },
        },
    )
    _write_yaml(
        tmp_path / "tools" / "a" / "open_api_tool" / "current.yaml",
        {
            "openapi": "3.0.0",
            "servers": [{"url": "https://fixed.example.com"}],
            "paths": {
                "/health": {
                    "get": {"responses": {"200": {"description": "ok"}}}
                }
            },
        },
    )
    stale_schema = _write_yaml(
        tmp_path / "tools" / "a" / "open_api_tool" / "open_api_schema.yaml",
        {
            "openapi": "3.0.0",
            "servers": [{"url": "$env_var"}],
            "paths": {
                "/old": {"get": {"responses": {"200": {"description": "ok"}}}}
            },
        },
    )
    stale_before = stale_schema.read_text(encoding="utf-8")
    _write_json(tmp_path / "environment.json", {})

    assert collect_tool_and_toolset_env_var_placeholders(tmp_path) == []
    assert _run_specific(tmp_path, {"A018"}).results == []
    assert resolve_tool_and_toolset_env_vars_in_dir(tmp_path) == []
    assert stale_schema.read_text(encoding="utf-8") == stale_before


def test_shared_openapi_schema_conflicting_urls_rejected_before_writes(
    tmp_path: Path,
) -> None:
    """Conflicting URLs on a shared OpenAPI schema fail A018 and abort writes."""
    _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    shared = _write_yaml(
        tmp_path / "schemas" / "shared.yaml",
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
    tool_a = _write_json(
        tmp_path / "tools" / "tool_a" / "tool_a.json",
        {
            "displayName": "tool_a",
            "openApiTool": {
                "openApiSchema": "schemas/shared.yaml",
                "url": "$env_var",
            },
        },
    )
    tool_b = _write_json(
        tmp_path / "tools" / "tool_b" / "tool_b.json",
        {
            "displayName": "tool_b",
            "openApiTool": {
                "openApiSchema": "schemas/shared.yaml",
                "url": "$env_var",
            },
        },
    )
    _write_json(
        tmp_path / "environment.json",
        {
            "tools": {
                "tool_a": {"openApiTool": {"url": "https://a.example.com"}},
                "tool_b": {"openApiTool": {"url": "https://b.example.com"}},
            }
        },
    )
    before = {
        p: p.read_text(encoding="utf-8") for p in (tool_a, tool_b, shared)
    }

    rep = _run_specific(tmp_path, {"A018"})
    assert len(rep.results) == 1
    assert rep.results[0].rule_id == "A018"
    assert rep.results[0].severity == Severity.ERROR
    assert "tool_a" in rep.results[0].message
    assert "tool_b" in rep.results[0].message

    with pytest.raises(ValueError, match=r"tool_a.*tool_b"):
        resolve_tool_and_toolset_env_vars_in_dir(tmp_path)

    for path, content in before.items():
        assert path.read_text(encoding="utf-8") == content


def test_shared_openapi_schema_identical_urls_resolved_cleanly(
    tmp_path: Path,
) -> None:
    """Identical URLs on a shared OpenAPI schema pass A018 and resolve cleanly."""
    _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    shared = _write_yaml(
        tmp_path / "schemas" / "shared.yaml",
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
    tool_a = _write_json(
        tmp_path / "tools" / "tool_a" / "tool_a.json",
        {
            "displayName": "tool_a",
            "openApiTool": {
                "openApiSchema": "schemas/shared.yaml",
                "url": "$env_var",
            },
        },
    )
    tool_b = _write_json(
        tmp_path / "tools" / "tool_b" / "tool_b.json",
        {
            "displayName": "tool_b",
            "openApiTool": {
                "openApiSchema": "schemas/shared.yaml",
                "url": "$env_var",
            },
        },
    )
    _write_json(
        tmp_path / "environment.json",
        {
            "tools": {
                "tool_a": {"openApiTool": {"url": "https://same.example.com"}},
                "tool_b": {"openApiTool": {"url": "https://same.example.com"}},
            }
        },
    )

    assert _run_specific(tmp_path, {"A018"}).results == []
    resolve_tool_and_toolset_env_vars_in_dir(tmp_path)
    for path in (shared, tool_a, tool_b):
        text = path.read_text(encoding="utf-8")
        assert "$env_var" not in text
        assert "https://same.example.com" in text


def test_a018_snake_case_manifest_and_default_schema_emits_single_error(
    tmp_path: Path,
) -> None:
    """Snake-case open_api_tool.url + default open_api_schema.yaml emits 1 A018 error."""
    _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    _write_yaml(
        tmp_path / "tools" / "a" / "a.yaml",
        {
            "display_name": "a",
            "open_api_tool": {"url": "$env_var"},
        },
    )
    _write_yaml(
        tmp_path / "tools" / "a" / "open_api_tool" / "open_api_schema.yaml",
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
    _write_json(tmp_path / "environment.json", {})

    rep = _run_specific(tmp_path, {"A018"})
    assert len(rep.results) == 1
    assert rep.results[0].rule_id == "A018"
    assert rep.results[0].severity == Severity.ERROR


def test_t020_missing_default_env_warns_once_and_missing_env_file_no_duplicate(
    tmp_path: Path,
) -> None:
    """Missing environment.json warns once; missing --env-file is A018-only."""
    _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    _write_json(
        tmp_path / "tools" / "t1" / "t1.json",
        {"displayName": "t1", "openApiTool": {"url": "$env_var"}},
    )
    _write_json(
        tmp_path / "tools" / "t2" / "t2.json",
        {"displayName": "t2", "openApiTool": {"url": "$env_var"}},
    )
    _write_json(
        tmp_path / "toolsets" / "ts1" / "ts1.json",
        {"displayName": "ts1", "openApiToolset": {"url": "$env_var"}},
    )

    # 3 placeholders + no default environment.json -> 1 T020 WARNING
    rep_default = _run_specific(tmp_path, {"A018", "T020"})
    assert len(rep_default.results) == 1
    assert rep_default.results[0].rule_id == "T020"
    assert rep_default.results[0].severity == Severity.WARNING
    assert "3 placeholder(s)" in rep_default.results[0].message

    # Explicit --env-file missing.json -> A018 ERROR only, zero T020 duplicates
    disc = Discovery(tmp_path, tmp_path / "evals")
    cfg = LintConfig()
    ctx = build_context(tmp_path, cfg, disc, env_file=tmp_path / "missing.json")
    rep_explicit = LintReport()
    run_rules(
        build_registry(),
        cfg,
        ctx,
        disc,
        rep_explicit,
        specific_rules={"A018", "T020"},
    )
    assert [r.rule_id for r in rep_explicit.results] == ["A018"]
    assert rep_explicit.results[0].severity == Severity.ERROR


def test_t020_case_sensitive_matching_aligned_with_a018(
    tmp_path: Path,
) -> None:
    """Mismatched casing triggers A018 ERROR (missing) and T020 WARNING (unused)."""
    _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    _write_json(
        tmp_path / "tools" / "GetStock" / "GetStock.json",
        {
            "displayName": "GetStock",
            "openApiTool": {
                "openApiSchema": "openapi: 3.0.0",
                "url": "$env_var",
            },
        },
    )
    _write_json(
        tmp_path / "environment.json",
        {
            "tools": {
                "getstock": {
                    "openApiTool": {"url": "https://stock.example.com"}
                },
            }
        },
    )
    rep = _run_specific(tmp_path, {"A018", "T020"})
    a018 = [r for r in rep.results if r.rule_id == "A018"]
    t020 = [r for r in rep.results if r.rule_id == "T020"]

    assert len(a018) == 1
    assert a018[0].severity == Severity.ERROR
    assert "GetStock" in a018[0].message

    assert len(t020) == 1
    assert t020[0].severity == Severity.WARNING
    assert "getstock" in t020[0].message


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


# ── A019: Standard Voice / Language / TTS Parity ────────────────────────


def test_a019_language_settings_and_voice_tts_parity(tmp_path: Path) -> None:
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
    rep = _run_specific(tmp_path, {"A019"})
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
    assert len(registry.all_rules()) == 120  # noqa: PLR2004
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
        "A019",
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
        tmp_path / "environment.json",
        {"tools": {"stale_api": {"url": "https://old.example.com"}}},
    )
    (tmp_path / "cxaslint.yaml").write_text(
        "rules:\n  T020: error\n  S018: off\n", encoding="utf-8"
    )
    cfg = LintConfig.load(tmp_path)
    disc = Discovery(tmp_path, tmp_path / "evals")
    ctx = build_context(tmp_path, cfg, disc)
    reg = build_registry()

    for category, expected_id in [
        ("tools", "T020"),
        ("config", "A019"),
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
            assert all(r.severity == Severity.ERROR for r in t020)


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


def test_a019_voice_missing_audio_empty_tts_and_digital_override(
    tmp_path: Path,
) -> None:
    # Voice deployment omitting audioProcessingConfig uses platform defaults (0 warnings)
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
        VoiceLanguageTtsParity().check(cfg_a, cfg_a.read_text(), _ctx(app_a))
        == []
    )

    # Null / empty {} TTS entries when audioProcessingConfig is present still warn
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


def test_ignore_and_per_file_override_on_t020_and_t021(tmp_path: Path) -> None:
    """ignore/per_file work cleanly on environment.json (T020) and tools/** (T021)."""
    _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    _write_json(
        tmp_path / "agents" / "root" / "root.json", {"displayName": "root"}
    )
    _write_json(
        tmp_path / "environment.json",
        {"tools": {"stale_api": {"url": "https://old.example.com"}}},
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

    # Without ignore -> both T020 (on environment.json) and T021 (on tools/**) fire
    rep_unfiltered = _run_specific(tmp_path, {"T020", "T021"})
    assert {r.rule_id for r in rep_unfiltered.results} == {"T020", "T021"}

    # With ignore: ["./environment.json", "./tools/**"] -> both suppressed
    (tmp_path / "cxaslint.yaml").write_text(
        "ignore:\n  - './environment.json'\n  - './tools/**'\n",
        encoding="utf-8",
    )
    cfg = LintConfig.load(tmp_path)
    rep_ignored = _run_specific(tmp_path, {"T020", "T021"}, config=cfg)
    assert rep_ignored.results == []

    # per_file on app.json must NOT leak onto environment.json (T020) or tools/** (T021)
    cfg_no_leak = LintConfig(
        per_file={"app.json": {"T020": "error", "T021": "warning"}}
    )
    rep_no_leak = _run_specific(tmp_path, {"T020", "T021"}, config=cfg_no_leak)
    by_id = {r.rule_id: r.severity for r in rep_no_leak.results}
    assert by_id == {"T020": Severity.WARNING, "T021": Severity.ERROR}

    # per_file on emitted paths overrides global rules cleanly
    cfg_override = LintConfig(
        rules={"T020": Severity.WARNING, "T021": Severity.ERROR},
        per_file={
            "environment.json": {"T020": "error"},
            "tools/**": {"T021": "warning"},
        },
    )
    rep_override = _run_specific(
        tmp_path, {"T020", "T021"}, config=cfg_override
    )
    by_id_override = {r.rule_id: r.severity for r in rep_override.results}
    assert by_id_override == {"T020": Severity.ERROR, "T021": Severity.WARNING}


def test_a019_default_language_case_insensitive_bcp47(tmp_path: Path) -> None:
    """defaultLanguageCode matching in supportedLanguageCodes is case-insensitive."""
    app_json = _write_json(
        tmp_path / "app.json",
        {
            "displayName": "app",
            "languageSettings": {
                "defaultLanguageCode": "en-us",
                "supportedLanguageCodes": ["en-US"],
            },
        },
    )
    assert (
        VoiceLanguageTtsParity().check(
            app_json, app_json.read_text(encoding="utf-8"), _ctx(tmp_path)
        )
        == []
    )


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
        "A019",
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


def test_t021_shared_openapi_schema_across_multiple_tools(
    tmp_path: Path,
) -> None:
    """Shared OpenAPI schema reports T021 once even when referenced by 2 tools."""
    app_json = _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    shared_schema = _write_yaml(
        tmp_path / "schemas" / "shared.yml",
        {
            "openapi": "3.0.0",
            "servers": [{"url": "$env_var"}],
            "paths": {},
        },
    )
    for name in ("tool_a", "tool_b"):
        _write_json(
            tmp_path / "tools" / name / f"{name}.json",
            {
                "displayName": name,
                "openApiTool": {
                    "openApiSchema": str(shared_schema.relative_to(tmp_path))
                },
            },
        )

    t021_res = OpenApiSchemaValidity().check(app_json, "", _ctx(tmp_path))
    assert len(t021_res) == 1
    assert t021_res[0].file == "schemas/shared.yml"


def test_per_file_override_reenables_globally_off_cross_file_rule(
    tmp_path: Path,
) -> None:
    """per_file can re-enable an app_config rule globally disabled via rules."""
    _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    _write_json(
        tmp_path / "agents" / "root" / "root.json", {"displayName": "root"}
    )
    for subdir in ("critical", "other"):
        _write_yaml(
            tmp_path
            / "tools"
            / subdir
            / "open_api_tool"
            / "open_api_schema.yaml",
            {"openapi": "3.0.0", "paths": {}},
        )

    cfg = LintConfig(
        rules={"T021": Severity.OFF},
        per_file={"tools/critical/**": {"T021": "error"}},
    )
    rep = _run_specific(tmp_path, {"T021"}, config=cfg)
    assert [r.file for r in rep.results] == [
        "tools/critical/open_api_tool/open_api_schema.yaml"
    ]
    assert rep.results[0].severity == Severity.ERROR

    # An unrelated per_file entry must NOT leak a rule turned off on app.json
    cfg_no_leak = LintConfig(
        per_file={
            "app.json": {"T021": "off"},
            "never-matches.yml": {"T021": "error"},
        }
    )
    assert _run_specific(tmp_path, {"T021"}, config=cfg_no_leak).results == []


def test_per_file_override_reenables_globally_off_non_target_cross_file_rule(
    tmp_path: Path,
) -> None:
    """per_file can re-enable non-target cross-file rules like I012."""
    _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "foo"}
    )
    for name in ("foo", "bar"):
        _write_json(
            tmp_path / "agents" / name / f"{name}.json",
            {
                "displayName": name,
                "tools": ["unused_tool"],
            },
        )
        (tmp_path / "agents" / name / "instruction.txt").write_text(
            "No tools referenced here.\n", encoding="utf-8"
        )

    # Match the emitted agent JSON path specifically (not instruction.txt)
    cfg_exact = LintConfig(
        rules={"I012": Severity.OFF},
        per_file={"agents/foo/foo.json": {"I012": "error"}},
    )
    rep_exact = _run_specific(tmp_path, {"I012"}, config=cfg_exact)
    assert [r.file for r in rep_exact.results] == ["agents/foo/foo.json"]
    assert rep_exact.results[0].severity == Severity.ERROR

    # Glob pattern covering agents/foo/** also scopes cleanly to foo
    cfg_glob = LintConfig(
        rules={"I012": Severity.OFF},
        per_file={"agents/foo/**": {"I012": "error"}},
    )
    rep_glob = _run_specific(tmp_path, {"I012"}, config=cfg_glob)
    assert [r.file for r in rep_glob.results] == ["agents/foo/foo.json"]
    assert rep_glob.results[0].severity == Severity.ERROR
