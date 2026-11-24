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

"""Adversarial QA & E2E tests for PR 1 polymorphic lint rules.

Covers T017, T018, T019, G001, G002, configurable A015, C024, deployment_config
discovery, and real CLI subprocess execution. Includes both passing edge-case
checks and failing reproduction tests labeled with QA-PR1-00N for open defects.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from cxas_scrapi.utils.lint_rules.callbacks import VoiceBeforeModelToolMutation
from cxas_scrapi.utils.lint_rules.runtime_traps import VariableCountLimit
from cxas_scrapi.utils.lint_rules.structure import (
    CrosscheckLocalesContentBlocks,
    GuardrailJinjaSyntax,
    _LocaleYamlLoader,
)
from cxas_scrapi.utils.lint_rules.tools import (
    DataStoreFilterParameterBehavior,
    DataStoreFilterTemplateSyntax,
    DataStoreVoiceModality,
)
from cxas_scrapi.utils.linter import (
    Discovery,
    LintConfig,
    LintContext,
    LintReport,
    build_context,
    build_registry,
    run_rules,
)

_WORKTREE_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _WORKTREE_ROOT / "src"

_CLEAN_AUDIO_MODALITY = {
    "modalityType": "AUDIO",
    "snippetsConfig": {"enableSnippets": True},
    "rewriterConfig": {"disabled": True},
    "summarizationConfig": {"disabled": True},
}


def _ctx(
    project_root: Path,
    *,
    app_root: Path | None = None,
    options: dict[str, Any] | None = None,
) -> LintContext:
    resolved_app_root = app_root if app_root is not None else project_root
    return LintContext(
        project_root=project_root,
        app_dir=project_root,
        evals_dir=project_root / "evals",
        app_root=resolved_app_root,
        options=options or {},
    )


def _write_tool_json(
    app_root: Path, name: str, payload: dict[str, Any]
) -> Path:
    tool_dir = app_root / "tools" / name
    tool_dir.mkdir(parents=True, exist_ok=True)
    path = tool_dir / f"{name}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run_cli_lint(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(_SRC_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "cxas_scrapi.cli.main", "lint", *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


# ── T017: data-store-filter-template ────────────────────────────────────


def test_qa_pr1_t017_plain_static_filter_has_no_false_positive(
    tmp_path: Path,
) -> None:
    """Plain Discovery Engine static filter must not trigger T017."""
    path = _write_tool_json(
        tmp_path,
        "ds_brand",
        {"dataStoreTool": {"filter": 'brand: ANY("brand_a")'}},
    )
    results = DataStoreFilterTemplateSyntax().check(
        path, path.read_text(), _ctx(tmp_path)
    )
    assert results == []


@pytest.mark.parametrize(
    "template_filter",
    [
        'brand: ANY("{{brand}}")',
        "{% if vip %}segment: ANY('vip'){% endif %}",
        'country: ANY("{country}")',
    ],
)
@pytest.mark.parametrize(
    "shape",
    [
        "top_camel",
        "sources_camel",
        "sources_snake",
        "engine_camel",
        "engine_snake",
    ],
)
def test_qa_pr1_t017_templates_in_existing_supported_paths_flagged(
    tmp_path: Path, template_filter: str, shape: str
) -> None:
    """T017 detects {{var}}, {% if %}, and {var} across supported JSON paths."""
    payloads: dict[str, dict[str, Any]] = {
        "top_camel": {"dataStoreTool": {"filter": template_filter}},
        "sources_camel": {
            "dataStoreTool": {"dataStoreSources": [{"filter": template_filter}]}
        },
        "sources_snake": {
            "data_store_tool": {
                "data_store_sources": [{"filter": template_filter}]
            }
        },
        "engine_camel": {
            "dataStoreTool": {"engineSource": {"filter": template_filter}}
        },
        "engine_snake": {
            "data_store_tool": {"engine_source": {"filter": template_filter}}
        },
    }
    path = _write_tool_json(tmp_path, "faq_search", payloads[shape])
    results = DataStoreFilterTemplateSyntax().check(
        path, path.read_text(), _ctx(tmp_path)
    )
    assert [r.rule_id for r in results] == ["T017"]


@pytest.mark.parametrize("key", ["dataStoreSource", "data_store_source"])
@pytest.mark.parametrize(
    "template_filter",
    [
        'brand: ANY("{{brand}}")',
        "{% if is_eu %}region: ANY('EU'){% endif %}",
        'country: ANY("{country}")',
    ],
)
def test_qa_pr1_001_t017_detects_template_in_ces_data_store_source_filter(
    tmp_path: Path, key: str, template_filter: str
) -> None:
    """Repro QA-PR1-001 (BLOCKER): T017 misses real CES dataStoreSource.filter."""
    tool_key = (
        "dataStoreTool" if key == "dataStoreSource" else "data_store_tool"
    )
    path = _write_tool_json(
        tmp_path,
        "kb_search",
        {
            "displayName": "kb_search",
            tool_key: {
                "name": "kb_search",
                key: {
                    "dataStore": {
                        "name": "projects/p/locations/l/collections/c/dataStores/d"
                    },
                    "filter": template_filter,
                },
            },
        },
    )
    results = DataStoreFilterTemplateSyntax().check(
        path, path.read_text(), _ctx(tmp_path)
    )
    assert [r.rule_id for r in results] == ["T017"]


@pytest.mark.parametrize(
    ("tool_key", "engine_key", "sources_key"),
    [
        ("dataStoreTool", "engineSource", "dataStoreSources"),
        ("data_store_tool", "engine_source", "data_store_sources"),
    ],
)
def test_qa_pr1_001_t017_detects_template_in_engine_source_data_store_sources_filter(
    tmp_path: Path, tool_key: str, engine_key: str, sources_key: str
) -> None:
    """Repro QA-PR1-001 (BLOCKER): T017 misses engineSource.dataStoreSources[].filter."""
    path = _write_tool_json(
        tmp_path,
        "engine_kb",
        {
            "displayName": "engine_kb",
            tool_key: {
                "name": "engine_kb",
                engine_key: {
                    "engine": "projects/p/locations/l/collections/c/engines/e",
                    sources_key: [
                        {
                            "dataStore": {
                                "name": "projects/p/locations/l/collections/c/dataStores/d"
                            },
                            "filter": 'segment: ANY("{{segment}}")',
                        }
                    ],
                },
            },
        },
    )
    results = DataStoreFilterTemplateSyntax().check(
        path, path.read_text(), _ctx(tmp_path)
    )
    assert [r.rule_id for r in results] == ["T017"]


# ── T018: data-store-filter-parameter-behavior ──────────────────────────


@pytest.mark.parametrize("behavior", ["NEVER_INCLUDE", 3])
@pytest.mark.parametrize(
    "behavior_key", ["filterParameterBehavior", "filter_parameter_behavior"]
)
def test_qa_pr1_t018_never_include_camel_and_snake_pass(
    tmp_path: Path, behavior: str | int, behavior_key: str
) -> None:
    """T018 accepts NEVER_INCLUDE and integer enum 3 in camelCase and snake_case."""
    path = _write_tool_json(
        tmp_path,
        "ds_brand",
        {
            "dataStoreTool": {
                "filter": 'brand: ANY("brand_a")',
                behavior_key: behavior,
            }
        },
    )
    results = DataStoreFilterParameterBehavior().check(
        path, path.read_text(), _ctx(tmp_path)
    )
    assert results == []


@pytest.mark.parametrize(
    "bad_behavior",
    [
        None,
        "INCLUDE",
        "ALWAYS_INCLUDE",
        "DEFAULT",
        "FILTER_PARAMETER_BEHAVIOR_UNSPECIFIED",
        "never_include",
        0,
        2,
    ],
)
def test_qa_pr1_t018_unsafe_or_non_canonical_behavior_flagged(
    tmp_path: Path, bad_behavior: str | int | None
) -> None:
    """T018 rejects missing, unsafe, and invalid-case filterParameterBehavior."""
    ds_cfg: dict[str, Any] = {"filter": 'brand: ANY("brand_a")'}
    if bad_behavior is not None:
        ds_cfg["filterParameterBehavior"] = bad_behavior
    path = _write_tool_json(tmp_path, "faq_search", {"dataStoreTool": ds_cfg})
    results = DataStoreFilterParameterBehavior().check(
        path, path.read_text(), _ctx(tmp_path)
    )
    assert [r.rule_id for r in results] == ["T018"]


def test_qa_pr1_t018_unfiltered_non_ds_tool_has_no_false_positive(
    tmp_path: Path,
) -> None:
    """Unfiltered non-ds_ dataStoreTool should not trigger T018."""
    path = _write_tool_json(
        tmp_path,
        "public_docs",
        {
            "displayName": "public_docs",
            "dataStoreTool": {
                "name": "public_docs",
                "dataStoreSource": {
                    "dataStore": {"name": "projects/p/locations/l/dataStores/d"}
                },
            },
        },
    )
    results = DataStoreFilterParameterBehavior().check(
        path, path.read_text(), _ctx(tmp_path)
    )
    assert results == []


@pytest.mark.parametrize(
    "payload",
    [
        {
            "displayName": "kb_search",
            "dataStoreTool": {
                "name": "kb_search",
                "dataStoreSource": {
                    "dataStore": {
                        "name": "projects/p/locations/l/collections/c/dataStores/d"
                    },
                    "filter": 'brand: ANY("brand_a")',
                },
            },
        },
        {
            "displayName": "engine_kb",
            "dataStoreTool": {
                "name": "engine_kb",
                "engineSource": {
                    "engine": "projects/p/locations/l/collections/c/engines/e",
                    "dataStoreSources": [
                        {
                            "dataStore": {
                                "name": "projects/p/locations/l/collections/c/dataStores/d"
                            },
                            "filter": 'country: ANY("US")',
                        }
                    ],
                },
            },
        },
    ],
)
def test_qa_pr1_001_t018_flags_brand_filter_in_ces_data_store_source_and_engine_sources(
    tmp_path: Path, payload: dict[str, Any]
) -> None:
    """Repro QA-PR1-001 (BLOCKER): T018 misses real CES dataStoreSource & engineSource filters."""
    path = _write_tool_json(tmp_path, payload["displayName"], payload)
    results = DataStoreFilterParameterBehavior().check(
        path, path.read_text(), _ctx(tmp_path)
    )
    assert [r.rule_id for r in results] == ["T018"]


def test_qa_pr1_001_cli_e2e_schema_valid_ces_data_store_tool_catches_t017_t018(
    tmp_path: Path,
) -> None:
    """Repro QA-PR1-001 (BLOCKER): Real CLI lint on V003-valid CES DataStoreTool catches T017 & T018."""
    (tmp_path / "app.json").write_text(
        json.dumps({"displayName": "real_app", "rootAgent": "root_agent"}),
        encoding="utf-8",
    )
    ag = tmp_path / "agents" / "root_agent"
    ag.mkdir(parents=True)
    (ag / "root_agent.json").write_text(
        json.dumps(
            {"displayName": "root_agent", "tools": ["end_session", "kb"]}
        ),
        encoding="utf-8",
    )
    (ag / "instruction.txt").write_text(
        "Use {@TOOL: kb} and {@TOOL: end_session}.", encoding="utf-8"
    )
    _write_tool_json(
        tmp_path,
        "kb",
        {
            "displayName": "kb",
            "dataStoreTool": {
                "name": "kb",
                "description": "Brand KB search",
                "dataStoreSource": {
                    "dataStore": {
                        "name": "projects/p/locations/l/collections/c/dataStores/d"
                    },
                    "filter": 'brand: ANY("{{brand}}")',
                },
            },
        },
    )
    proc = _run_cli_lint(
        "--app-dir", str(tmp_path), "--rule", "V003,T017,T018", "--json"
    )
    items = json.loads(proc.stdout)
    rule_ids = {item["rule_id"] for item in items}
    assert "V003" not in rule_ids
    assert {"T017", "T018"}.issubset(rule_ids)
    assert proc.returncode == 1


# ── T019: data-store-voice-modality ─────────────────────────────────────


def test_qa_pr1_t019_snake_case_modality_configs_passes(tmp_path: Path) -> None:
    """T019 accepts valid snake_case modality_configs AUDIO entry."""
    deps = tmp_path / "deployments"
    deps.mkdir()
    (deps / "voice.yaml").write_text(
        "channel_profile:\n  channel_type: GOOGLE_TELEPHONY_PLATFORM\n",
        encoding="utf-8",
    )
    path = _write_tool_json(
        tmp_path,
        "ds_kb",
        {
            "data_store_tool": {
                "modality_configs": [
                    {
                        "modality_type": "AUDIO",
                        "snippets_config": {"enable_snippets": True},
                        "rewriter_config": {"disabled": True},
                        "summarization_config": {"disabled": True},
                    }
                ]
            }
        },
    )
    results = DataStoreVoiceModality().check(
        path, path.read_text(), _ctx(tmp_path)
    )
    assert results == []


def test_qa_pr1_t019_text_only_modality_on_voice_deployment_warns(
    tmp_path: Path,
) -> None:
    """T019 warns when a voice deployment has TEXT-only modalityConfigs."""
    deps = tmp_path / "deployments"
    deps.mkdir()
    (deps / "voice.yaml").write_text(
        "channelProfile:\n  channelType: CONTACT_CENTER_AS_A_SERVICE\n",
        encoding="utf-8",
    )
    path = _write_tool_json(
        tmp_path,
        "ds_kb",
        {"dataStoreTool": {"modalityConfigs": [{"modalityType": "TEXT"}]}},
    )
    results = DataStoreVoiceModality().check(
        path, path.read_text(), _ctx(tmp_path)
    )
    assert [r.rule_id for r in results] == ["T019"]


def test_qa_pr1_002_t019_detects_voice_via_app_audio_processing_config(
    tmp_path: Path,
) -> None:
    """Repro QA-PR1-002 (HIGH): T019 ignores app.json audioProcessingConfig voice apps."""
    (tmp_path / "app.json").write_text(
        json.dumps(
            {
                "displayName": "voice_app",
                "audioProcessingConfig": {"inactivityTimeout": "20s"},
            }
        ),
        encoding="utf-8",
    )
    path = _write_tool_json(
        tmp_path,
        "ds_kb",
        {"dataStoreTool": {"modalityConfigs": [{"modalityType": "TEXT"}]}},
    )
    results = DataStoreVoiceModality().check(
        path, path.read_text(), _ctx(tmp_path)
    )
    assert [r.rule_id for r in results] == ["T019"]


@pytest.mark.parametrize(
    "deployment_yaml",
    [
        "channelProfile:\n  channelType: TELEPHONY\n",
        "channelProfile:\n  channelType: VOICE\n",
        "channelProfile:\n  channelType: TWILIO\n",
        "channel: GOOGLE_TELEPHONY_PLATFORM\n",
    ],
)
def test_qa_pr1_002_t019_detects_voice_via_telephony_and_voice_deployments(
    tmp_path: Path, deployment_yaml: str
) -> None:
    """Repro QA-PR1-002 (HIGH): T019 misses TELEPHONY/VOICE/TWILIO and top-level channel."""
    deps = tmp_path / "deployments"
    deps.mkdir()
    (deps / "voice.yaml").write_text(deployment_yaml, encoding="utf-8")
    path = _write_tool_json(tmp_path, "ds_kb", {"dataStoreTool": {}})
    results = DataStoreVoiceModality().check(
        path, path.read_text(), _ctx(tmp_path)
    )
    assert [r.rule_id for r in results] == ["T019"]


def test_qa_pr1_006_t017_t018_t019_support_yaml_tools_and_int_audio_modality(
    tmp_path: Path,
) -> None:
    """Repro QA-PR1-006 (MEDIUM): YAML dataStoreTool files & integer AUDIO enum=2."""
    deps = tmp_path / "deployments"
    deps.mkdir()
    (deps / "voice.yaml").write_text(
        "channelProfile:\n  channelType: GOOGLE_TELEPHONY_PLATFORM\n",
        encoding="utf-8",
    )
    tool_dir = tmp_path / "tools" / "ds_yaml"
    tool_dir.mkdir(parents=True)
    yaml_tool = tool_dir / "ds_yaml.yaml"
    yaml_tool.write_text(
        "displayName: ds_yaml\n"
        "dataStoreTool:\n"
        "  filter: 'brand = \"{{brand}}\"'\n"
        "  filterParameterBehavior: INCLUDE\n",
        encoding="utf-8",
    )
    assert [
        r.rule_id
        for r in DataStoreFilterTemplateSyntax().check(
            yaml_tool, yaml_tool.read_text(), _ctx(tmp_path)
        )
    ] == ["T017"]

    int_audio_tool = _write_tool_json(
        tmp_path,
        "ds_int_audio",
        {
            "dataStoreTool": {
                "modalityConfigs": [
                    {
                        "modalityType": 2,
                        "snippetsConfig": {"enableSnippets": True},
                        "rewriterConfig": {"disabled": True},
                        "summarizationConfig": {"disabled": True},
                    }
                ]
            }
        },
    )
    assert (
        DataStoreVoiceModality().check(
            int_audio_tool, int_audio_tool.read_text(), _ctx(tmp_path)
        )
        == []
    )


# ── G001: guardrail-jinja-syntax ────────────────────────────────────────


@pytest.mark.parametrize(
    "prompt",
    [
        "{% for item in items %}{{ item }}{% endfor %}",
        "{% if brand == 'a' %}Block{% endif %}",
        "Policy for {{ tenant_id }}",
    ],
)
def test_qa_pr1_g001_jinja_constructs_in_llm_policy_flagged(
    tmp_path: Path, prompt: str
) -> None:
    """G001 flags {% if %}, {% for %}, and {{var}} inside llmPolicy.prompt."""
    gr_dir = tmp_path / "guardrails" / "brand_guard"
    gr_dir.mkdir(parents=True)
    (gr_dir / "brand_guard.json").write_text(
        json.dumps(
            {"displayName": "brand_guard", "llmPolicy": {"prompt": prompt}}
        ),
        encoding="utf-8",
    )
    results = GuardrailJinjaSyntax().check(gr_dir, "", _ctx(tmp_path))
    assert [r.rule_id for r in results] == ["G001"]


def test_qa_pr1_g001_flat_session_var_placeholder_does_not_false_positive(
    tmp_path: Path,
) -> None:
    """G001 must not false-positive on valid flat {session_var} placeholders."""
    gr_dir = tmp_path / "guardrails" / "brand_guard"
    gr_dir.mkdir(parents=True)
    (gr_dir / "brand_guard.json").write_text(
        json.dumps(
            {
                "displayName": "brand_guard",
                "llmPolicy": {
                    "prompt": "Enforce rules for {brand_name} in {session_locale}."
                },
            }
        ),
        encoding="utf-8",
    )
    assert GuardrailJinjaSyntax().check(gr_dir, "", _ctx(tmp_path)) == []


@pytest.mark.parametrize(
    "payload",
    [
        {
            "displayName": "sec",
            "llmPromptSecurity": {
                "customPolicy": {"prompt": "{% if vip %}Allow{% endif %}"}
            },
        },
        {
            "display_name": "sec",
            "llm_prompt_security": {
                "custom_policy": {"prompt": "Deny {{ forbidden_topic }}"}
            },
        },
    ],
)
def test_qa_pr1_003_g001_detects_jinja_in_custom_policy_prompt(
    tmp_path: Path, payload: dict[str, Any]
) -> None:
    """Repro QA-PR1-003 (HIGH): G001 misses llmPromptSecurity.customPolicy.prompt."""
    gr_dir = tmp_path / "guardrails" / "sec"
    gr_dir.mkdir(parents=True)
    (gr_dir / "sec.json").write_text(json.dumps(payload), encoding="utf-8")
    results = GuardrailJinjaSyntax().check(gr_dir, "", _ctx(tmp_path))
    assert [r.rule_id for r in results] == ["G001"]


def test_qa_pr1_003_g001_detects_jinja_in_yaml_guardrail(
    tmp_path: Path,
) -> None:
    """Repro QA-PR1-003 (HIGH): G001 ignores YAML guardrail configs."""
    gr_dir = tmp_path / "guardrails" / "yaml_guard"
    gr_dir.mkdir(parents=True)
    (gr_dir / "yaml_guard.yaml").write_text(
        "displayName: yaml_guard\nllmPolicy:\n  prompt: 'Block {{ topic }}'\n",
        encoding="utf-8",
    )
    results = GuardrailJinjaSyntax().check(gr_dir, "", _ctx(tmp_path))
    assert [r.rule_id for r in results] == ["G001"]


# ── G002: crosscheck-locales-content-blocks ─────────────────────────────


def test_qa_pr1_g002_malformed_yaml_does_not_crash(tmp_path: Path) -> None:
    """G002 must handle invalid/malformed YAML in both files without crashing."""
    locales = tmp_path / "manifests" / "locales"
    locales.mkdir(parents=True)
    (locales / "content_blocks.yaml").write_text(
        "blocks: [unclosed", encoding="utf-8"
    )
    deps = tmp_path / "deployments"
    deps.mkdir()
    dep = deps / "web.yaml"
    dep.write_text(
        "brandId: brand_a\nsupportedCountries: ['US']\n", encoding="utf-8"
    )
    assert (
        CrosscheckLocalesContentBlocks().check(
            dep, dep.read_text(), _ctx(tmp_path)
        )
        == []
    )

    (locales / "content_blocks.yaml").write_text(
        "blocks:\n  brand_a:\n    US:\n      en: {ok: true}\n", encoding="utf-8"
    )
    dep.write_text("brandId: [broken", encoding="utf-8")
    assert (
        CrosscheckLocalesContentBlocks().check(
            dep, dep.read_text(), _ctx(tmp_path)
        )
        == []
    )


def test_qa_pr1_004_g002_discovers_deployments_in_nested_app_layout(
    tmp_path: Path,
) -> None:
    """Repro QA-PR1-004 (HIGH): deployment_config discovery ignores project_root/deployments/ in nested apps."""
    nested_app = tmp_path / "my_app"
    nested_app.mkdir()
    (nested_app / "app.json").write_text(
        json.dumps({"displayName": "my_app"}), encoding="utf-8"
    )
    (tmp_path / "deployments").mkdir()
    (tmp_path / "deployments" / "web.yaml").write_text(
        "brandId: brand_a\nsupportedCountries: ['US']\nsupportedLanguages: ['fr']\n",
        encoding="utf-8",
    )
    (tmp_path / "manifests" / "locales").mkdir(parents=True)
    (tmp_path / "manifests" / "locales" / "content_blocks.yaml").write_text(
        "blocks:\n  brand_a:\n    US:\n      en: {greeting: Hi}\n",
        encoding="utf-8",
    )
    discovery = Discovery(tmp_path, tmp_path / "evals")
    cfg = LintConfig()
    ctx = build_context(tmp_path, cfg, discovery)
    report = LintReport()
    run_rules(
        build_registry(), cfg, ctx, discovery, report, specific_rules={"G002"}
    )
    assert [r.rule_id for r in report.results] == ["G002"]


def test_qa_pr1_007_g002_supports_snake_case_deployment_keys(
    tmp_path: Path,
) -> None:
    """Repro QA-PR1-007 (MEDIUM): G002 ignores snake_case brand_id/supported_countries/supported_languages."""
    (tmp_path / "manifests" / "locales").mkdir(parents=True)
    (tmp_path / "manifests" / "locales" / "content_blocks.yaml").write_text(
        "blocks:\n  brand_a:\n    US:\n      en: {greeting: Hi}\n",
        encoding="utf-8",
    )
    deps = tmp_path / "deployments"
    deps.mkdir()
    dep = deps / "snake.yaml"
    dep.write_text(
        "brand_id: brand_a\n"
        "supported_countries: ['US']\n"
        "supported_languages: ['es']\n",
        encoding="utf-8",
    )
    results = CrosscheckLocalesContentBlocks().check(
        dep, dep.read_text(), _ctx(tmp_path)
    )
    assert [r.rule_id for r in results] == ["G002"]


# ── A015: variable-count-limit ──────────────────────────────────────────


def _write_app_vars(
    tmp_path: Path, count: int, *, as_yaml: bool = False, snake: bool = False
) -> Path:
    key = "variable_declarations" if snake else "variableDeclarations"
    decls = [{"name": f"v{i}"} for i in range(count)]
    if as_yaml:
        lines = ["displayName: demo", f"{key}:"]
        lines.extend(f"  - name: v{i}" for i in range(count))
        path = tmp_path / "app.yaml"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path
    path = tmp_path / "app.json"
    path.write_text(
        json.dumps({"displayName": "demo", key: decls}), encoding="utf-8"
    )
    return path


@pytest.mark.parametrize(
    ("count", "options", "expect_warn"),
    [
        (95, {}, False),
        (96, {}, True),
        (190, {"max_variables_per_app": 200}, False),
        (191, {"max_variables_per_app": 200}, True),
        (
            150,
            {
                "max_variables_per_app": 200,
                "A015": {"max_variables_per_app": 100},
            },
            True,
        ),
        (
            150,
            {
                "max_variables_per_app": 100,
                "A015": {"max_variables_per_app": 200},
            },
            False,
        ),
    ],
)
def test_qa_pr1_a015_exact_boundaries_and_option_precedence(
    tmp_path: Path,
    count: int,
    options: dict[str, Any],
    expect_warn: bool,
) -> None:
    """A015 enforces exact 95/96 & 190/191 boundaries and A015 > top-level precedence."""
    app_file = _write_app_vars(tmp_path, count)
    results = VariableCountLimit().check(
        app_file, app_file.read_text(), _ctx(tmp_path, options=options)
    )
    assert bool(results) is expect_warn


@pytest.mark.parametrize(
    "invalid_val", [0, -1, True, False, "bad", None, math.nan]
)
def test_qa_pr1_a015_invalid_option_values_fall_back_safely(
    tmp_path: Path, invalid_val: Any
) -> None:
    """A015 falls back to default ceiling 95 on invalid non-infinite option values."""
    app_file = _write_app_vars(tmp_path, 95, as_yaml=True, snake=True)
    results = VariableCountLimit().check(
        app_file,
        app_file.read_text(),
        _ctx(
            tmp_path, options={"A015": {"max_variables_per_app": invalid_val}}
        ),
    )
    assert results == []


@pytest.mark.parametrize("bad_float", [math.inf, 0.5])
def test_qa_pr1_005_a015_does_not_crash_on_infinite_or_fractional_limit(
    tmp_path: Path, bad_float: float
) -> None:
    """Repro QA-PR1-005 (MEDIUM): A015 crashes with OverflowError on .inf and warns on 0.5."""
    app_file = _write_app_vars(tmp_path, 10)
    results = VariableCountLimit().check(
        app_file,
        app_file.read_text(),
        _ctx(tmp_path, options={"A015": {"max_variables_per_app": bad_float}}),
    )
    assert results == []


# ── C024: voice-before-model-request-mutation ───────────────────────────


@pytest.mark.parametrize(
    "mutation_stmt",
    [
        "llm_request.config = {}",
        "llm_request.temperature += 0.1",
        "llm_request.tools.append('x')",
        "llm_request.tools.pop()",
        "llm_request.tools.clear()",
        "llm_request.headers.update({'a': 'b'})",
        "llm_request.headers.setdefault('a', 'b')",
        "llm_request.hide_tool('secret')",
    ],
)
@pytest.mark.parametrize("async_prefix", ["", "async "])
def test_qa_pr1_c024_all_mutator_forms_sync_and_async_flagged(
    tmp_path: Path, mutation_stmt: str, async_prefix: str
) -> None:
    """C024 catches sync/async assignments, AugAssign, collection mutators, and hide_tool."""
    (tmp_path / "app.json").write_text(
        json.dumps({"audioProcessingConfig": {"inactivityTimeout": "20s"}}),
        encoding="utf-8",
    )
    cb = (
        tmp_path
        / "agents"
        / "root_agent"
        / "before_model_callbacks"
        / "cb_01"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)
    code = (
        f"{async_prefix}def before_model_callback(callback_context, llm_request):\n"
        f"    {mutation_stmt}\n"
        "    return None\n"
    )
    cb.write_text(code, encoding="utf-8")
    results = VoiceBeforeModelToolMutation().check(cb, code, _ctx(tmp_path))
    assert [r.rule_id for r in results] == ["C024"]


@pytest.mark.parametrize(
    "cb_folder",
    ["before_agent_callbacks_01", "after_model_callbacks_01"],
)
def test_qa_pr1_c024_no_false_positive_on_other_callbacks_or_readonly(
    tmp_path: Path, cb_folder: str
) -> None:
    """C024 must not flag before_agent/after_model, read-only before_model, or invalid syntax."""
    (tmp_path / "app.json").write_text(
        json.dumps({"audioProcessingConfig": {"inactivityTimeout": "20s"}}),
        encoding="utf-8",
    )
    other_cb = tmp_path / "agents" / "root" / cb_folder / "python_code.py"
    other_cb.parent.mkdir(parents=True)
    mutating = "def before_model_callback(cb, llm_request):\n    llm_request.hide_tool('x')\n"
    other_cb.write_text(mutating, encoding="utf-8")
    assert (
        VoiceBeforeModelToolMutation().check(other_cb, mutating, _ctx(tmp_path))
        == []
    )

    readonly_cb = (
        tmp_path
        / "agents"
        / "root"
        / "before_model_callbacks_01"
        / "python_code.py"
    )
    readonly_cb.parent.mkdir(parents=True)
    readonly_code = (
        "def before_model_callback(cb, llm_request):\n"
        "    model = llm_request.model\n"
        "    _ = llm_request.headers.get('x')\n"
        "    return None\n"
    )
    readonly_cb.write_text(readonly_code, encoding="utf-8")
    assert (
        VoiceBeforeModelToolMutation().check(
            readonly_cb, readonly_code, _ctx(tmp_path)
        )
        == []
    )

    broken = "def before_model_callback(cb, llm_request):\n    llm_request.hide_tool(\n"
    assert (
        VoiceBeforeModelToolMutation().check(
            readonly_cb, broken, _ctx(tmp_path)
        )
        == []
    )


def test_qa_pr1_008_c024_edge_cases_tuple_unpack_posonly_chained_and_annassign(
    tmp_path: Path,
) -> None:
    """Repro QA-PR1-008 (MEDIUM): C024 misses tuple unpack & posonlyargs, duplicates chained assign, flags bare AnnAssign."""
    (tmp_path / "app.json").write_text(
        json.dumps({"audioProcessingConfig": {"inactivityTimeout": "20s"}}),
        encoding="utf-8",
    )
    cb = (
        tmp_path
        / "agents"
        / "root"
        / "before_model_callbacks_01"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)

    tuple_unpack = (
        "def before_model_callback(cb, req):\n"
        "    req.model, req.config = 'm', {}\n"
    )
    assert [
        r.rule_id
        for r in VoiceBeforeModelToolMutation().check(
            cb, tuple_unpack, _ctx(tmp_path)
        )
    ] == ["C024"]

    posonly = "def before_model_callback(cb, req, /):\n    req.hide_tool('x')\n"
    assert [
        r.rule_id
        for r in VoiceBeforeModelToolMutation().check(
            cb, posonly, _ctx(tmp_path)
        )
    ] == ["C024"]

    chained = "def before_model_callback(cb, req):\n    req.a = req.b = 1\n"
    assert (
        len(VoiceBeforeModelToolMutation().check(cb, chained, _ctx(tmp_path)))
        == 1
    )

    bare_ann = "def before_model_callback(cb, req):\n    req.config: dict\n"
    assert (
        VoiceBeforeModelToolMutation().check(cb, bare_ann, _ctx(tmp_path)) == []
    )


# ── Real CLI E2E, Registry & Standalone Isolation ───────────────────────


def test_qa_pr1_cli_list_rules_and_exit_codes_and_no_build_imports(
    tmp_path: Path,
) -> None:
    """CLI --list-rules shows 110 rules; lint exits 1 on errors, 0 on warnings; zero build imports."""
    registry = build_registry()
    assert len(registry.all_rules()) == 110

    list_proc = _run_cli_lint("--list-rules")
    assert list_proc.returncode == 0
    for rule_id in ("T017", "T018", "T019", "G001", "G002", "A015", "C024"):
        assert rule_id in list_proc.stdout

    # Verify zero cxas_scrapi.build imports in linter & lint_rules
    utils_dir = _SRC_ROOT / "cxas_scrapi" / "utils"
    for py_file in [
        utils_dir / "linter.py",
        *(utils_dir / "lint_rules").glob("*.py"),
    ]:
        text = py_file.read_text(encoding="utf-8")
        assert "cxas_scrapi.build" not in text, (
            f"Unexpected build import in {py_file}"
        )

    # Full CLI E2E app exercising all 7 PR1 rules
    (tmp_path / "app.json").write_text(
        json.dumps(
            {
                "displayName": "e2e_app",
                "rootAgent": "root_agent",
                "audioProcessingConfig": {"inactivityTimeout": "20s"},
                "variableDeclarations": [{"name": f"v{i}"} for i in range(96)],
            }
        ),
        encoding="utf-8",
    )
    ag = tmp_path / "agents" / "root_agent"
    ag.mkdir(parents=True)
    (ag / "root_agent.json").write_text(
        json.dumps(
            {"displayName": "root_agent", "tools": ["end_session", "ds_faq"]}
        ),
        encoding="utf-8",
    )
    (ag / "instruction.txt").write_text(
        "Use {@TOOL: ds_faq} and {@TOOL: end_session}.", encoding="utf-8"
    )
    cb = ag / "before_model_callbacks_01"
    cb.mkdir()
    (cb / "python_code.py").write_text(
        "def before_model_callback(cb, llm_request):\n"
        "    llm_request.hide_tool('ds_faq')\n",
        encoding="utf-8",
    )
    _write_tool_json(
        tmp_path,
        "ds_faq",
        {
            "displayName": "ds_faq",
            "dataStoreTool": {
                "filter": "brand = '{{brand}}'",
                "filterParameterBehavior": "INCLUDE",
                "modalityConfigs": [{"modalityType": "TEXT"}],
            },
        },
    )
    gr = tmp_path / "guardrails" / "g1"
    gr.mkdir(parents=True)
    (gr / "g1.json").write_text(
        json.dumps({"llmPolicy": {"prompt": "{% if x %}no{% endif %}"}}),
        encoding="utf-8",
    )
    (tmp_path / "deployments").mkdir()
    (tmp_path / "deployments" / "voice.yaml").write_text(
        "brandId: brand_a\n"
        "supportedCountries: ['US']\n"
        "supportedLanguages: ['en', 'de']\n"
        "channelProfile:\n"
        "  channelType: GOOGLE_TELEPHONY_PLATFORM\n",
        encoding="utf-8",
    )
    (tmp_path / "manifests" / "locales").mkdir(parents=True)
    (tmp_path / "manifests" / "locales" / "content_blocks.yaml").write_text(
        "blocks:\n  brand_a:\n    US:\n      en: {greeting: Hello}\n",
        encoding="utf-8",
    )

    err_proc = _run_cli_lint(
        "--app-dir",
        str(tmp_path),
        "--rule",
        "T017,T018,T019,G001,G002,A015,C024",
        "--json",
    )
    assert err_proc.returncode == 1
    assert {item["rule_id"] for item in json.loads(err_proc.stdout)} == {
        "T017",
        "T018",
        "T019",
        "G001",
        "G002",
        "A015",
        "C024",
    }

    warn_proc = _run_cli_lint(
        "--app-dir", str(tmp_path), "--rule", "T019,A015,C024", "--json"
    )
    assert warn_proc.returncode == 0
    assert {item["rule_id"] for item in json.loads(warn_proc.stdout)} == {
        "T019",
        "A015",
        "C024",
    }


# ── Round 1 Fix Regression Tests (QA-PR1-007 / 008 / 009) ───────────────


def test_qa_pr1_007_locale_yaml_loader_safety_and_norway_codes(
    tmp_path: Path,
) -> None:
    """Verify QA-PR1-007: _LocaleYamlLoader keeps NO/no as strings, preserves SafeLoader, and rejects unsafe tags."""
    assert yaml.safe_load("flag: NO\nflag2: true\n") == {
        "flag": False,
        "flag2": True,
    }
    with pytest.raises(yaml.YAMLError):
        yaml.load(
            "!!python/object/apply:os.system ['echo nope']",
            Loader=_LocaleYamlLoader,
        )

    (tmp_path / "manifests" / "locales").mkdir(parents=True)
    (tmp_path / "manifests" / "locales" / "content_blocks.yaml").write_text(
        "defaults: &d\n"
        "  greeting: Hei\n"
        "blocks:\n"
        "  brand_a:\n"
        "    NO:\n"
        "      no: *d\n",
        encoding="utf-8",
    )
    deps = tmp_path / "deployments"
    deps.mkdir()
    dep = deps / "nordics.yaml"
    dep.write_text(
        "brandId: brand_a\n"
        "supportedCountries: [NO]\n"
        "supportedLanguages: [no]\n",
        encoding="utf-8",
    )
    assert (
        CrosscheckLocalesContentBlocks().check(
            dep, dep.read_text(), _ctx(tmp_path)
        )
        == []
    )


def test_qa_pr1_008_c024_alias_tracking_and_setattr_safety(
    tmp_path: Path,
) -> None:
    """Verify QA-PR1-008: C024 catches alias & setattr mutations without false positives on copies or rebinds."""
    (tmp_path / "app.json").write_text(
        json.dumps({"audioProcessingConfig": {"inactivityTimeout": "20s"}}),
        encoding="utf-8",
    )
    cb = (
        tmp_path
        / "agents"
        / "root"
        / "before_model_callbacks_01"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)

    alias_mutate = (
        "def before_model_callback(cb, req):\n"
        "    tools = req.tools\n"
        "    tools.append('x')\n"
    )
    assert [
        (r.rule_id, r.line)
        for r in VoiceBeforeModelToolMutation().check(
            cb, alias_mutate, _ctx(tmp_path)
        )
    ] == [("C024", 3)]

    setattr_mutate = (
        "def before_model_callback(cb, req):\n"
        "    setattr(req, 'config', None)\n"
    )
    assert [
        (r.rule_id, r.line)
        for r in VoiceBeforeModelToolMutation().check(
            cb, setattr_mutate, _ctx(tmp_path)
        )
    ] == [("C024", 2)]

    safe_copy = (
        "def before_model_callback(cb, req):\n"
        "    tools = list(req.tools)\n"
        "    tools.append('local_only')\n"
    )
    assert (
        VoiceBeforeModelToolMutation().check(cb, safe_copy, _ctx(tmp_path))
        == []
    )

    safe_rebind = (
        "def before_model_callback(cb, req):\n"
        "    cfg = req.config\n"
        "    cfg = {}\n"
        "    cfg['k'] = 1\n"
    )
    assert (
        VoiceBeforeModelToolMutation().check(cb, safe_rebind, _ctx(tmp_path))
        == []
    )


def test_qa_pr1_009_diagnostic_polish_and_yaml_tool_discovery(
    tmp_path: Path,
) -> None:
    """Verify QA-PR1-009: T018 ds_ reason, T019 empty AUDIO dict sub-gaps, G001 {# comment #}, and YAML tool discovery."""
    (tmp_path / "app.json").write_text(
        json.dumps(
            {
                "displayName": "app",
                "audioProcessingConfig": {"inactivityTimeout": "20s"},
            }
        ),
        encoding="utf-8",
    )

    ds_plain = _write_tool_json(
        tmp_path,
        "ds_plain",
        {"displayName": "ds_plain", "dataStoreTool": {"name": "ds_plain"}},
    )
    t018_res = DataStoreFilterParameterBehavior().check(
        ds_plain, ds_plain.read_text(), _ctx(tmp_path)
    )
    assert len(t018_res) == 1
    assert "with ds_ prefix" in t018_res[0].message

    empty_audio = _write_tool_json(
        tmp_path,
        "kb_empty_audio",
        {
            "displayName": "kb_empty_audio",
            "dataStoreTool": {"modalityConfigs": {"AUDIO": {}}},
        },
    )
    t019_res = DataStoreVoiceModality().check(
        empty_audio, empty_audio.read_text(), _ctx(tmp_path)
    )
    assert len(t019_res) == 3
    assert any("snippetsConfig" in r.message for r in t019_res)
    assert any("rewriterConfig" in r.message for r in t019_res)
    assert any("summarizationConfig" in r.message for r in t019_res)

    gr_dir = tmp_path / "guardrails" / "comment_guard"
    gr_dir.mkdir(parents=True)
    (gr_dir / "comment_guard.json").write_text(
        json.dumps({"llmPolicy": {"prompt": "Block {# todo #} items"}}),
        encoding="utf-8",
    )
    assert [
        r.rule_id
        for r in GuardrailJinjaSyntax().check(gr_dir, "", _ctx(tmp_path))
    ] == ["G001"]

    yaml_dir = tmp_path / "tools" / "kb_yaml"
    yaml_dir.mkdir(parents=True)
    (yaml_dir / "kb_yaml.yaml").write_text(
        "displayName: kb_yaml\n"
        "dataStoreTool:\n"
        "  name: kb_yaml\n"
        "  dataStoreSource:\n"
        "    filter: 'brand: ANY(\"{{brand}}\")'\n",
        encoding="utf-8",
    )
    discovery = Discovery(tmp_path, tmp_path / "evals")
    assert "kb_yaml" in discovery.discover_tools()
    cfg = LintConfig()
    ctx = build_context(tmp_path, cfg, discovery)
    report = LintReport()
    run_rules(
        build_registry(),
        cfg,
        ctx,
        discovery,
        report,
        specific_rules={"T017", "T018", "T019"},
    )
    kb_yaml_rules = {
        r.rule_id for r in report.results if "kb_yaml.yaml" in r.file
    }
    assert kb_yaml_rules == {"T017", "T018", "T019"}
