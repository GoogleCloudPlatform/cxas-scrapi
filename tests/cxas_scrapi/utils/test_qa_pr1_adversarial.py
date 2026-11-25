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
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from cxas_scrapi.utils.lint_rules.callbacks import (
    SharedPythonBundleIntegrity,
    VoiceBeforeModelToolMutation,
)
from cxas_scrapi.utils.lint_rules.config import VoiceLanguageTtsParity
from cxas_scrapi.utils.lint_rules.runtime_traps import VariableCountLimit
from cxas_scrapi.utils.lint_rules.structure import (
    AgentRefSelfOrOrphan,
    AgentTreeCycle,
    CrosscheckLocalesContentBlocks,
    DeploymentEntryAgentParity,
    GuardrailJinjaSyntax,
    OrphanedAgent,
    ProdUrlSafety,
    SubAgentSingleParent,
    UnusedGuardrail,
    UnusedToolset,
    _LocaleYamlLoader,
)
from cxas_scrapi.utils.lint_rules.tools import (
    DataStoreFilterParameterBehavior,
    DataStoreFilterTemplateSyntax,
    DataStoreVoiceModality,
    OpenApiSchemaValidity,
    ToolDisplayNameUnreferenced,
    ToolToolsetEnvVarParity,
)
from cxas_scrapi.utils.linter import (
    Discovery,
    LintConfig,
    LintContext,
    LintReport,
    Severity,
    _find_elementary_cycles,
    build_context,
    build_registry,
    is_voice_app,
    resolve_agent_graph,
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
    """CLI --list-rules shows >= 110 rules including PR 1 rules; lint exits 1 on errors, 0 on warnings; zero build imports."""
    registry = build_registry()
    assert len(registry.all_rules()) >= 110
    pr1_rules = ("T017", "T018", "T019", "G001", "G002", "A015", "C024")
    for rule_id in pr1_rules:
        assert registry.get(rule_id) is not None

    list_proc = _run_cli_lint("--list-rules")
    assert list_proc.returncode == 0
    for rule_id in pr1_rules:
        assert rule_id in list_proc.stdout

    # Verify zero cxas_scrapi.build imports in PR 1's standalone lint modules
    utils_dir = _SRC_ROOT / "cxas_scrapi" / "utils"
    for py_file in [
        utils_dir / "linter.py",
        utils_dir / "lint_rules" / "__init__.py",
        utils_dir / "lint_rules" / "tools.py",
        utils_dir / "lint_rules" / "callbacks.py",
        utils_dir / "lint_rules" / "runtime_traps.py",
        utils_dir / "lint_rules" / "structure.py",
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


# ── PR 1 Extension (Commit 731ca93c): S014-S017, T008, S007 & YAML ──────


def _write_ext_agent(
    app_root: Path,
    dir_name: str,
    *,
    ext: str = ".json",
    display_name: str | None = None,
    cfg_name: str | None = None,
    child_agents: Any = None,
    child_key: str = "childAgents",
    tools: Any = None,
    toolsets: Any = None,
    guardrails: Any = None,
    extra: dict[str, Any] | None = None,
    with_instruction: bool = True,
) -> Path:
    agent_dir = app_root / "agents" / dir_name
    agent_dir.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"displayName": display_name or dir_name}
    if with_instruction:
        (agent_dir / "instruction.txt").write_text(
            "<role>Agent. Today is {current_date}.</role>\n",
            encoding="utf-8",
        )
        data["instruction"] = f"agents/{dir_name}/instruction.txt"
    if cfg_name is not None:
        data["name"] = cfg_name
    if child_agents is not None:
        data[child_key] = child_agents
    if tools is not None:
        data["tools"] = tools
    if toolsets is not None:
        data["toolsets"] = toolsets
    if guardrails is not None:
        data["guardrails"] = guardrails
    if extra:
        data.update(extra)
    cfg_path = agent_dir / f"{dir_name}{ext}"
    if ext == ".json":
        cfg_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    else:
        cfg_path.write_text(
            yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
        )
    return cfg_path


def _write_ext_py_tool(
    app_root: Path,
    tool_name: str,
    *,
    ext: str = ".json",
    display_name: str | None = None,
    cfg_name: str | None = None,
    py_code: str | None = None,
) -> Path:
    tool_dir = app_root / "tools" / tool_name
    py_dir = tool_dir / "python_function"
    py_dir.mkdir(parents=True, exist_ok=True)
    py_path = py_dir / "python_code.py"
    py_path.write_text(
        py_code
        or f'def {tool_name}() -> dict:\n    """Doc."""\n    return {{}}\n',
        encoding="utf-8",
    )
    cfg: dict[str, Any] = {
        "displayName": display_name or tool_name,
        "pythonFunction": {
            "pythonCode": f"tools/{tool_name}/python_function/python_code.py"
        },
    }
    if cfg_name is not None:
        cfg["name"] = cfg_name
    cfg_path = tool_dir / f"{tool_name}{ext}"
    if ext == ".json":
        cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    else:
        cfg_path.write_text(
            yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8"
        )
    return py_path


def test_qa_pr1_ext_s014_dense_k6_cap_figure8_and_malformed_inputs(
    tmp_path: Path,
) -> None:
    """S014 handles K_6 cycle cap quickly, figure-8 overlapping cycles, snake_case, and malformed childAgents."""
    # K_6 complete directed graph (409 elementary cycles) must cap at 100 quickly
    k6 = {f"n{i}": [f"n{j}" for j in range(6) if j != i] for i in range(6)}
    t0 = time.perf_counter()
    cycles = _find_elementary_cycles(k6, root_agent_dir="n0")
    elapsed = time.perf_counter() - t0
    assert len(cycles) == 100
    assert len({tuple(c) for c in cycles}) == 100
    assert elapsed < 1.0

    # Overlapping figure-8 cycles: root -> a -> b -> root AND a -> c -> a
    fig8 = tmp_path / "fig8"
    fig8_app = fig8 / "app.json"
    fig8.mkdir()
    fig8_app.write_text(
        json.dumps({"displayName": "fig8", "rootAgent": "ROOT AGENT"}),
        encoding="utf-8",
    )
    _write_ext_agent(
        fig8,
        "root_agent",
        display_name="Root Agent",
        child_agents=["node_a", "NODE A"],
        child_key="child_agents",
    )
    _write_ext_agent(fig8, "node_a", child_agents=["node_b", "node_c"])
    _write_ext_agent(fig8, "node_b", child_agents=["root_agent"])
    _write_ext_agent(fig8, "node_c", child_agents=["node_a"])

    graph = resolve_agent_graph(fig8)
    assert graph.root_agent_dir == "root_agent"
    assert graph.cycles == [
        ["node_a", "node_c", "node_a"],
        ["root_agent", "node_a", "node_b", "root_agent"],
    ]
    res = AgentTreeCycle().check(fig8_app, "", _ctx(fig8))
    assert len(res) == 2
    assert all(r.rule_id == "S014" for r in res)

    # Malformed childAgents values must never crash or false-fire
    mal = tmp_path / "malformed"
    mal.mkdir()
    mal_app = mal / "app.json"
    mal_app.write_text(
        json.dumps({"displayName": "mal", "rootAgent": "root"}),
        encoding="utf-8",
    )
    _write_ext_agent(
        mal,
        "root",
        child_agents=[None, 123, "", "   ", {"bad": True}, "leaf", "leaf"],
    )
    _write_ext_agent(mal, "leaf", child_agents="not-a-list")
    assert AgentTreeCycle().check(mal_app, "", _ctx(mal)) == []
    assert OrphanedAgent().check(mal_app, "", _ctx(mal)) == []


def test_qa_pr1_ext_s015_depth60_no_cfg_dir_and_nested_layout(
    tmp_path: Path,
) -> None:
    """S015 handles 60-deep chains without recursion errors, config-less agent dirs, and nested app_root layouts."""
    chain = tmp_path / "chain"
    chain.mkdir()
    chain_app = chain / "app.json"
    chain_app.write_text(
        json.dumps({"displayName": "chain", "rootAgent": "a00"}),
        encoding="utf-8",
    )
    for i in range(60):
        _write_ext_agent(
            chain,
            f"a{i:02d}",
            child_agents=[f"a{i + 1:02d}"] if i < 59 else [],
        )
    _write_ext_agent(chain, "orphan_leaf")
    orphans = OrphanedAgent().check(chain_app, "", _ctx(chain))
    assert len(orphans) == 1
    assert orphans[0].file == "agents/orphan_leaf/orphan_leaf.json"

    # Config-less agent directories (only instruction.txt)
    nocfg = tmp_path / "nocfg"
    nocfg.mkdir()
    nocfg_app = nocfg / "app.json"
    nocfg_app.write_text(
        json.dumps({"displayName": "nocfg", "rootAgent": "root"}),
        encoding="utf-8",
    )
    _write_ext_agent(nocfg, "root", child_agents=["reached_nocfg"])
    (nocfg / "agents" / "reached_nocfg").mkdir(parents=True)
    (nocfg / "agents" / "reached_nocfg" / "instruction.txt").write_text(
        "<role>R</role>\n", encoding="utf-8"
    )
    (nocfg / "agents" / "orphan_nocfg").mkdir(parents=True)
    (nocfg / "agents" / "orphan_nocfg" / "instruction.txt").write_text(
        "<role>O</role>\n", encoding="utf-8"
    )
    nocfg_res = OrphanedAgent().check(nocfg_app, "", _ctx(nocfg))
    assert len(nocfg_res) == 1
    assert nocfg_res[0].file == "agents/orphan_nocfg"

    # Nested cxas_app/<AppName>/app.json layout
    proj = tmp_path / "nested_proj"
    nested_app = proj / "cxas_app" / "NestedApp"
    nested_app.mkdir(parents=True)
    (nested_app / "app.json").write_text(
        json.dumps({"displayName": "Nested", "rootAgent": "root"}),
        encoding="utf-8",
    )
    _write_ext_agent(nested_app, "root")
    _write_ext_agent(nested_app, "orphan")
    (nested_app / "toolsets" / "dead_ts").mkdir(parents=True)
    (nested_app / "toolsets" / "dead_ts" / "dead_ts.json").write_text(
        json.dumps({"displayName": "dead_ts"}), encoding="utf-8"
    )
    (nested_app / "guardrails" / "dead_gr").mkdir(parents=True)
    (nested_app / "guardrails" / "dead_gr" / "dead_gr.json").write_text(
        json.dumps({"displayName": "dead_gr"}), encoding="utf-8"
    )
    disc = Discovery(proj / "cxas_app", proj / "evals")
    ctx = build_context(proj, LintConfig(), disc)
    rep = LintReport()
    run_rules(
        build_registry(),
        LintConfig(),
        ctx,
        disc,
        rep,
        specific_rules={"S014", "S015", "S016", "S017"},
    )
    assert {(r.rule_id, r.file) for r in rep.results} == {
        ("S015", "cxas_app/NestedApp/agents/orphan/orphan.json"),
        ("S016", "cxas_app/NestedApp/toolsets/dead_ts/dead_ts.json"),
        ("S017", "cxas_app/NestedApp/guardrails/dead_gr/dead_gr.json"),
    }


def test_qa_pr1_ext_s016_s017_broken_root_app_and_orphan_guardrail_formats(
    tmp_path: Path,
) -> None:
    """S016/S017 handle broken rootAgent, app+orphan guardrail, dict/relative/flat .yml refs, and malformed entries."""
    # Broken rootAgent: unattached still warns, attached does NOT falsely claim unreachable
    broken = tmp_path / "broken"
    broken.mkdir()
    broken_app = broken / "app.json"
    broken_app.write_text(
        json.dumps({"displayName": "b", "rootAgent": "does_not_exist"}),
        encoding="utf-8",
    )
    _write_ext_agent(
        broken,
        "a1",
        toolsets=[{"toolset": "used_ts"}, None, {}, {"toolset": 99}],
        guardrails=["used_gr", None, 42, {"guardrail": None}],
    )
    for name in ("used_ts", "unused_ts"):
        d = broken / "toolsets" / name
        d.mkdir(parents=True)
        (d / f"{name}.json").write_text(
            json.dumps({"displayName": name}), encoding="utf-8"
        )
    for name in ("used_gr", "unused_gr"):
        d = broken / "guardrails" / name
        d.mkdir(parents=True)
        (d / f"{name}.json").write_text(
            json.dumps({"displayName": name}), encoding="utf-8"
        )
    assert [
        r.file for r in UnusedToolset().check(broken_app, "", _ctx(broken))
    ] == ["toolsets/unused_ts/unused_ts.json"]
    assert [
        r.file for r in UnusedGuardrail().check(broken_app, "", _ctx(broken))
    ] == ["guardrails/unused_gr/unused_gr.json"]

    # Guardrail attached to BOTH app.json AND orphan_agent + dict/rel/flat .yml refs -> zero S017 warnings
    fmt = tmp_path / "fmt"
    fmt.mkdir()
    fmt_app = fmt / "app.json"
    fmt_app.write_text(
        json.dumps(
            {
                "displayName": "fmt",
                "rootAgent": "root",
                "guardrails": [
                    "shared_gr",
                    {"guardrail": "Dict Guard"},
                    "guardrails/rel_guard",
                ],
            }
        ),
        encoding="utf-8",
    )
    _write_ext_agent(fmt, "root", guardrails=["flat_yml_gr"])
    _write_ext_agent(fmt, "orphan", guardrails=["shared_gr"])
    for dirname, disp, ext in [
        ("shared_gr", "shared_gr", ".json"),
        ("dict_guard", "Dict Guard", ".yaml"),
    ]:
        gd = fmt / "guardrails" / dirname
        gd.mkdir(parents=True)
        payload = {"displayName": disp}
        (gd / f"{dirname}{ext}").write_text(
            json.dumps(payload) if ext == ".json" else yaml.safe_dump(payload),
            encoding="utf-8",
        )
    (fmt / "guardrails" / "rel_guard.json").write_text(
        json.dumps({"displayName": "Rel Guard"}), encoding="utf-8"
    )
    (fmt / "guardrails" / "flat_yml_gr.yml").write_text(
        yaml.safe_dump({"displayName": "Flat YML"}), encoding="utf-8"
    )
    assert UnusedGuardrail().check(fmt_app, "", _ctx(fmt)) == []


def test_qa_pr1_ext_t008_s007_reachability_and_sample_support_agent_cli(
    tmp_path: Path,
) -> None:
    """T008 respects reachable vs orphan callbacks/guardrails/non-UTF-8; S007 ignores same-parent dupes and catches .yml/snake_case; Sample_Support_Agent CLI is 0E/0W."""
    # T008 callbacks (getattr + async_tools + non-UTF-8) + guardrails (reachable child vs orphan)
    app = tmp_path / "t008_reach"
    app.mkdir()
    (app / "app.json").write_text(
        json.dumps({"displayName": "t", "rootAgent": "root"}),
        encoding="utf-8",
    )
    _write_ext_agent(app, "root", child_agents=["child"])
    _write_ext_agent(app, "child", guardrails=["child_gr"])
    _write_ext_agent(app, "orphan", guardrails=["orphan_gr"])

    root_cb = (
        app
        / "agents"
        / "root"
        / "before_model_callbacks"
        / "cb1"
        / "python_code.py"
    )
    root_cb.parent.mkdir(parents=True)
    root_cb.write_text(
        "def cb(c, r):\n"
        "    getattr(tools, 'reach_getattr')()\n"
        "    async_tools.reach_async()\n",
        encoding="utf-8",
    )
    bad_utf8_cb = (
        app
        / "agents"
        / "root"
        / "before_model_callbacks"
        / "cb2"
        / "python_code.py"
    )
    bad_utf8_cb.parent.mkdir(parents=True)
    bad_utf8_cb.write_bytes(b"\xff\xfe def f(): tools.non_utf8_rescued()\n")

    orphan_cb = (
        app
        / "agents"
        / "orphan"
        / "before_model_callbacks"
        / "cb1"
        / "python_code.py"
    )
    orphan_cb.parent.mkdir(parents=True)
    orphan_cb.write_text(
        "def cb(c, r):\n"
        "    getattr(tools, 'orphan_getattr')()\n"
        "    async_tools.orphan_async()\n",
        encoding="utf-8",
    )

    for gr_name, t_name in [
        ("child_gr", "child_gr_tool"),
        ("orphan_gr", "orphan_gr_tool"),
    ]:
        gd = app / "guardrails" / gr_name
        gd.mkdir(parents=True)
        (gd / f"{gr_name}.yaml").write_text(
            yaml.safe_dump(
                {
                    "displayName": gr_name,
                    "action": {
                        "toolCall": {
                            "tool": f"projects/p/locations/us/apps/a/tools/{t_name}"
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    for tname in (
        "reach_getattr",
        "reach_async",
        "non_utf8_rescued",
        "child_gr_tool",
        "orphan_getattr",
        "orphan_async",
        "orphan_gr_tool",
    ):
        _write_ext_py_tool(app, tname)

    disc = Discovery(app, app / "evals")
    ctx = build_context(app, LintConfig(), disc)
    rep = LintReport()
    run_rules(
        build_registry(), LintConfig(), ctx, disc, rep, specific_rules={"T008"}
    )
    flagged = {r.file for r in rep.results}
    assert flagged == {
        "tools/orphan_getattr/orphan_getattr.json",
        "tools/orphan_async/orphan_async.json",
        "tools/orphan_gr_tool/orphan_gr_tool.json",
    }

    # S007: same parent listing child twice (dirName + displayName) must NOT fire;
    # snake_case child_agents + CES path + .yml multi-parent MUST fire
    dup = tmp_path / "s007_dup"
    dup.mkdir()
    (dup / "app.json").write_text(
        json.dumps({"displayName": "dup", "rootAgent": "root"}),
        encoding="utf-8",
    )
    _write_ext_agent(dup, "root", child_agents=["billing", "Billing Desk"])
    _write_ext_agent(dup, "billing", display_name="Billing Desk")
    disc_dup = Discovery(dup, dup / "evals")
    ctx_dup = build_context(dup, LintConfig(), disc_dup)
    rep_dup = LintReport()
    run_rules(
        build_registry(),
        LintConfig(),
        ctx_dup,
        disc_dup,
        rep_dup,
        specific_rules={"S007"},
    )
    assert rep_dup.results == []

    mp = tmp_path / "s007_yml"
    mp.mkdir()
    (mp / "app.yml").write_text(
        yaml.safe_dump({"displayName": "mp", "rootAgent": "root"}),
        encoding="utf-8",
    )
    _write_ext_agent(
        mp,
        "root",
        ext=".yml",
        child_agents=["pa", "pb"],
        child_key="child_agents",
    )
    _write_ext_agent(
        mp,
        "pa",
        ext=".yml",
        child_agents=["projects/p/locations/us/apps/a/agents/shared-id"],
        child_key="child_agents",
    )
    _write_ext_agent(mp, "pb", ext=".yml", child_agents=["Shared Display"])
    shared_yml = _write_ext_agent(
        mp,
        "shared_sub",
        ext=".yml",
        display_name="Shared Display",
        cfg_name="projects/p/locations/us/apps/a/agents/shared-id",
    )
    disc_mp = Discovery(mp, mp / "evals")
    ctx_mp = build_context(mp, LintConfig(), disc_mp)
    s007_res = SubAgentSingleParent().check(
        shared_yml, shared_yml.read_text(encoding="utf-8"), ctx_mp
    )
    assert len(s007_res) == 1
    assert s007_res[0].rule_id == "S007"

    # Real CLI on Sample_Support_Agent must exit 0 with 0 errors and 0 warnings
    sample_dir = (
        _WORKTREE_ROOT
        / ".agents"
        / "skills"
        / "cxas-agent-foundry"
        / "assets"
        / "project-template"
        / "cxas_app"
    )
    proc = _run_cli_lint("--app-dir", str(sample_dir))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "0 error(s), 0 warning(s)" in proc.stdout


def test_qa_pr1_ext_cli_list_rules_and_cycle_vs_orphan_exit_codes(
    tmp_path: Path,
) -> None:
    """Real CLI --list-rules exposes S014-S017; S014 cycle exits 1, S015 orphan-only exits 0."""
    registry = build_registry()
    assert len(registry.all_rules()) >= 114
    for rule_id in ("S014", "S015", "S016", "S017", "T008"):
        assert registry.get(rule_id) is not None

    list_proc = _run_cli_lint("--list-rules")
    assert list_proc.returncode == 0
    for rule_id in ("S014", "S015", "S016", "S017"):
        assert rule_id in list_proc.stdout

    # S014 cycle -> exit 1
    cycle_app = tmp_path / "cycle_app"
    cycle_app.mkdir()
    (cycle_app / "app.json").write_text(
        json.dumps({"name": "a", "displayName": "a", "rootAgent": "root"}),
        encoding="utf-8",
    )
    _write_ext_agent(
        cycle_app, "root", child_agents=["sub"], tools=["end_session"]
    )
    _write_ext_agent(cycle_app, "sub", child_agents=["root"])
    c_proc = _run_cli_lint(
        "--app-dir", str(cycle_app), "--rule", "S014,S015,S016,S017", "--json"
    )
    assert c_proc.returncode == 1
    c_payload = json.loads(c_proc.stdout)
    assert [item["rule_id"] for item in c_payload] == ["S014"]
    assert c_payload[0]["severity"] == "error"

    # S015 orphan only -> exit 0
    orphan_app = tmp_path / "orphan_app"
    orphan_app.mkdir()
    (orphan_app / "app.json").write_text(
        json.dumps({"name": "a", "displayName": "a", "rootAgent": "root"}),
        encoding="utf-8",
    )
    _write_ext_agent(orphan_app, "root", tools=["end_session"])
    _write_ext_agent(orphan_app, "orphan")
    o_proc = _run_cli_lint(
        "--app-dir", str(orphan_app), "--rule", "S014,S015,S016,S017", "--json"
    )
    assert o_proc.returncode == 0
    o_payload = json.loads(o_proc.stdout)
    assert [item["rule_id"] for item in o_payload] == ["S015"]
    assert o_payload[0]["severity"] == "warning"


# ── Open Defect Reproductions (Commit 731ca93c: QA-PR1-010..012) ────────


def test_qa_pr1_010_yaml_app_full_lint_must_not_emit_false_a001_a004(
    tmp_path: Path,
) -> None:
    """QA-PR1-010 [BLOCKER]: Valid YAML CES app must not fail full cxas lint with false A001/A004 errors."""
    yaml_app = tmp_path / "yaml_app"
    yaml_app.mkdir()
    (yaml_app / "app.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "yaml_app",
                "displayName": "YAML App",
                "rootAgent": "root_agent",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _write_ext_agent(
        yaml_app,
        "root_agent",
        ext=".yaml",
        child_agents=["guided_sub"],
        tools=["end_session"],
    )
    _write_ext_agent(
        yaml_app,
        "guided_sub",
        ext=".yaml",
        with_instruction=False,
        extra={"guidedAgent": {"config": "some_config"}},
    )

    disc = Discovery(yaml_app, yaml_app / "evals")
    ctx = build_context(yaml_app, LintConfig(), disc)
    rep = LintReport()
    run_rules(build_registry(), LintConfig(), ctx, disc, rep)

    false_errors = [r for r in rep.errors if r.rule_id in {"A001", "A004"}]
    assert false_errors == [], (
        f"Valid YAML app emitted false A001/A004 errors: {false_errors}"
    )

    proc = _run_cli_lint("--app-dir", str(yaml_app))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_qa_pr1_011_discover_tools_prefers_dir_config_and_skips_symlinks(
    tmp_path: Path,
) -> None:
    """QA-PR1-011 [MEDIUM]: discover_tools() must prefer <dir>.{json,yaml,yml} over sidecar JSON and skip symlinks/hidden dirs (same for discover_callbacks)."""
    app = tmp_path / "app"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.json").write_text(
        json.dumps({"displayName": "secret_tool"}), encoding="utf-8"
    )
    cb_out = outside / "before_model_callbacks" / "cb1"
    cb_out.mkdir(parents=True)
    (cb_out / "python_code.py").write_text("x = 1\n", encoding="utf-8")

    app.mkdir()
    (app / "app.json").write_text(
        json.dumps({"displayName": "app", "rootAgent": "root"}),
        encoding="utf-8",
    )
    _write_ext_agent(app, "root", tools=[])

    # Non-Python JSON tool with alphabetically earlier sidecar schema JSON
    zeta_dir = app / "tools" / "zeta_tool"
    zeta_dir.mkdir(parents=True)
    (zeta_dir / "zeta_tool.json").write_text(
        json.dumps({"displayName": "Zeta Tool", "openApiTool": {}}),
        encoding="utf-8",
    )
    (zeta_dir / "aaa_schema.json").write_text(
        json.dumps({"type": "object"}), encoding="utf-8"
    )

    # Non-Python YAML tool with sidecar JSON schema
    yaml_dir = app / "tools" / "yaml_tool"
    yaml_dir.mkdir(parents=True)
    (yaml_dir / "yaml_tool.yaml").write_text(
        yaml.safe_dump({"displayName": "YAML Tool", "openApiTool": {}}),
        encoding="utf-8",
    )
    (yaml_dir / "schema.json").write_text(
        json.dumps({"type": "object"}), encoding="utf-8"
    )

    # Hidden dir and symlinks under tools/ and agents/
    hidden_tool = app / "tools" / ".hidden_tool"
    hidden_tool.mkdir(parents=True)
    (hidden_tool / ".hidden_tool.json").write_text(
        json.dumps({"displayName": "hidden"}), encoding="utf-8"
    )
    os.symlink(outside, app / "tools" / "sym_tool")
    os.symlink(outside, app / "agents" / "sym_agent")

    disc = Discovery(app, app / "evals")
    discovered_tools = disc.discover_tools()
    assert discovered_tools.get("zeta_tool") == zeta_dir / "zeta_tool.json"
    assert discovered_tools.get("yaml_tool") == yaml_dir / "yaml_tool.yaml"
    assert "sym_tool" not in discovered_tools
    assert ".hidden_tool" not in discovered_tools
    assert all(agent != "sym_agent" for agent, *_ in disc.discover_callbacks())

    ctx = build_context(app, LintConfig(), disc)
    rep = LintReport()
    run_rules(
        build_registry(), LintConfig(), ctx, disc, rep, specific_rules={"T008"}
    )
    assert {r.file for r in rep.results} == {
        "tools/zeta_tool/zeta_tool.json",
        "tools/yaml_tool/yaml_tool.yaml",
    }


def test_qa_pr1_012_t008_orphan_tool_via_ces_name_must_not_rescue_dead_helper(
    tmp_path: Path,
) -> None:
    """QA-PR1-012 [LOW]: An orphan-only tool attached via its CES resource name must not rescue another unreferenced helper tool from T008."""
    (tmp_path / "app.json").write_text(
        json.dumps({"displayName": "app", "rootAgent": "root"}),
        encoding="utf-8",
    )
    _write_ext_agent(tmp_path, "root", tools=[])
    _write_ext_agent(
        tmp_path,
        "orphan",
        tools=["projects/p/locations/us/apps/a/tools/custom-tool-id-99"],
    )
    _write_ext_py_tool(
        tmp_path,
        "orphan_caller",
        display_name="Orphan Caller",
        cfg_name="projects/p/locations/us/apps/a/tools/custom-tool-id-99",
        py_code=(
            'def orphan_caller() -> dict:\n    """Doc."""\n'
            "    return tools.dead_helper()\n"
        ),
    )
    dead_py = _write_ext_py_tool(tmp_path, "dead_helper")

    res = ToolDisplayNameUnreferenced().check(
        dead_py, dead_py.read_text(encoding="utf-8"), _ctx(tmp_path)
    )
    assert len(res) == 1, (
        "dead_helper was falsely rescued by orphan_caller attached via CES name"
    )
    assert res[0].rule_id == "T008"


# ── PR 1 Post-Build Integrity (Commit e12fa23e): T020-T021, C025, A018, S018-S020 ─


def test_qa_pr1_post_build_dispatch_categories_severity_overrides_and_yml_deployments(
    tmp_path: Path,
) -> None:
    """Verify multi-category app_config dispatch, mixed severities, cxaslint.yaml overrides, and deployments/*.yml."""
    app = tmp_path / "dispatch_app"
    (app / "agents" / "root").mkdir(parents=True)
    (app / "agents" / "alt").mkdir(parents=True)
    (app / "deployments").mkdir(parents=True)
    (app / "tools" / "api" / "open_api_tool").mkdir(parents=True)
    (app / "manifests" / "locales").mkdir(parents=True)

    (app / "app.json").write_text(
        json.dumps(
            {
                "name": "dispatch_app",
                "displayName": "dispatch_app",
                "rootAgent": "root",
                "languageSettings": {
                    "defaultLanguageCode": "fr-FR",
                    "supportedLanguageCodes": ["en-US", "de-DE"],
                    "enableMultilingualSupport": False,
                },
            }
        ),
        encoding="utf-8",
    )
    _write_ext_agent(app, "root", child_agents=["alt"], tools=["end_session"])
    _write_ext_agent(app, "alt")
    cb = app / "agents" / "root" / "before_model_callbacks" / "cb1"
    cb.mkdir(parents=True)
    (cb / "python_code.py").write_text(
        "import python_lib.missing\n", encoding="utf-8"
    )
    (app / "tools" / "api" / "api.json").write_text(
        json.dumps({"displayName": "api", "openApiTool": {"url": "$env_var"}}),
        encoding="utf-8",
    )
    (
        app / "tools" / "api" / "open_api_tool" / "open_api_schema.yml"
    ).write_text("openapi: '3.0.0'\npaths: {}\n", encoding="utf-8")
    (app / "environment.json").write_text(
        json.dumps({"tools": {"dangling_tool": {"url": "https://a.example"}}}),
        encoding="utf-8",
    )
    (app / "deployments" / "prod_voice.yml").write_text(
        yaml.safe_dump(
            {
                "environment": "PRODUCTION",
                "channel": "VOICE_TELEPHONY",
                "brandId": "brand_alpha",
                "supportedCountries": ["DE"],
                "supportedLanguages": ["de"],
                "entryAgent": "alt",
            }
        ),
        encoding="utf-8",
    )
    (app / "manifests" / "locales" / "content_blocks.yaml").write_text(
        yaml.safe_dump({"blocks": {"brand_alpha": {"US": {"en": "hi"}}}}),
        encoding="utf-8",
    )

    disc = Discovery(app, app / "evals")
    reg = build_registry()
    default_cfg = LintConfig()
    ctx = build_context(app, default_cfg, disc)

    # Category-scoped runs execute app_config rules once (never skipped or duplicated)
    for cat, expected_ids in [
        ("tools", {"T020", "T021"}),
        ("callbacks", {"C025"}),
        ("config", {"A018"}),
    ]:
        rep = LintReport()
        run_rules(reg, default_cfg, ctx, disc, rep, categories=[cat])
        ids = [
            r.rule_id
            for r in rep.results
            if r.rule_id in {"T020", "T021", "C025", "A018"}
        ]
        assert set(ids) == expected_ids
        if cat == "callbacks":
            assert ids == ["C025"]

    # Default run preserves mixed ERROR/WARNING severities on T020 and A018
    rep_mixed = LintReport()
    run_rules(
        reg,
        default_cfg,
        ctx,
        disc,
        rep_mixed,
        specific_rules={"T020", "A018", "S018", "G002"},
    )
    t020_sevs = {r.severity for r in rep_mixed.results if r.rule_id == "T020"}
    assert t020_sevs == {Severity.ERROR, Severity.WARNING}
    a018_sevs = {r.severity for r in rep_mixed.results if r.rule_id == "A018"}
    assert a018_sevs == {Severity.ERROR, Severity.WARNING}
    assert any(r.rule_id == "G002" for r in rep_mixed.results)
    assert is_voice_app(ctx) is True

    # Real CLI: S018 divergent entryAgent defaults to WARNING (exit 0), override to error exits 1
    s18_def = _run_cli_lint("--app-dir", str(app), "--rule", "S018", "--json")
    assert s18_def.returncode == 0
    assert json.loads(s18_def.stdout)[0]["severity"] == "warning"

    (app / "cxaslint.yaml").write_text(
        yaml.safe_dump(
            {
                "rules": {
                    "S018": "error",
                    "T020": "warning",
                    "C025": "info",
                }
            }
        ),
        encoding="utf-8",
    )
    s18_err = _run_cli_lint("--app-dir", str(app), "--rule", "S018", "--json")
    assert s18_err.returncode == 1
    assert json.loads(s18_err.stdout)[0]["severity"] == "error"

    ovr_cfg = LintConfig.load(app)
    rep_ovr = LintReport()
    run_rules(
        reg,
        ovr_cfg,
        build_context(app, ovr_cfg, disc),
        disc,
        rep_ovr,
        specific_rules={"T020", "C025", "S018"},
    )
    assert all(
        r.severity == Severity.WARNING
        for r in rep_ovr.results
        if r.rule_id == "T020"
    )
    assert all(
        r.severity == Severity.INFO
        for r in rep_ovr.results
        if r.rule_id == "C025"
    )
    assert all(
        r.severity == Severity.ERROR
        for r in rep_ovr.results
        if r.rule_id == "S018"
    )


def test_qa_pr1_post_build_t020_t021_edge_cases_quoting_and_path_traversal(
    tmp_path: Path,
) -> None:
    """Verify T020 quoting/matching/non-dict/dangling cases and T021 schema validity + path-traversal blocking."""
    # T020: unquoted/single-quoted/double-quoted $env_var in .yml, headers, toolsets, displayName/CES ID matching
    t20 = tmp_path / "t20"
    (t20 / "tools" / "t_unquoted" / "open_api_tool").mkdir(parents=True)
    (t20 / "tools" / "t_single" / "open_api_tool").mkdir(parents=True)
    (t20 / "tools" / "t_double" / "open_api_tool").mkdir(parents=True)
    (t20 / "tools" / "t_resolved").mkdir(parents=True)
    (t20 / "toolsets" / "ts_crm").mkdir(parents=True)
    app20 = t20 / "app.json"
    app20.write_text(json.dumps({"displayName": "t20"}), encoding="utf-8")

    (
        t20 / "tools" / "t_unquoted" / "open_api_tool" / "open_api_schema.yml"
    ).write_text(
        "openapi: '3.0.0'\nservers:\n  - url: $env_var\npaths:\n  /a: {}\n",
        encoding="utf-8",
    )
    (
        t20 / "tools" / "t_single" / "open_api_tool" / "open_api_schema.yml"
    ).write_text(
        "openapi: '3.0.0'\nservers:\n  - url: '$env_var'\npaths:\n  /a: {}\n",
        encoding="utf-8",
    )
    (
        t20 / "tools" / "t_double" / "open_api_tool" / "open_api_schema.yml"
    ).write_text(
        'openapi: "3.0.0"\nservers:\n  - url: "$env_var"\npaths:\n  /a: {}\n',
        encoding="utf-8",
    )
    (t20 / "tools" / "t_resolved" / "t_resolved.json").write_text(
        json.dumps(
            {
                "name": "projects/p/locations/us/apps/a/tools/ces-tool-42",
                "displayName": "Resolved API",
                "openApiTool": {
                    "url": "$env_var",
                    "headers": {"X-Api-Key": "$env_var"},
                },
            }
        ),
        encoding="utf-8",
    )
    (t20 / "toolsets" / "ts_crm" / "ts_crm.yaml").write_text(
        yaml.safe_dump(
            {
                "displayName": "CRM Toolset",
                "openApiToolset": {"url": "$env_var"},
            }
        ),
        encoding="utf-8",
    )

    # Missing environment.json flags all placeholders as ERROR
    res_missing = ToolToolsetEnvVarParity().check(app20, "", _ctx(t20))
    assert len(res_missing) == 6
    assert all(r.severity == Severity.ERROR for r in res_missing)

    # Non-dict tools: [] does not crash and still flags unresolved placeholders
    (t20 / "environment.json").write_text(
        json.dumps(
            {
                "tools": [],
                "toolsets": {
                    "CRM Toolset": {
                        "openApiToolset": {"url": "https://crm.example.com"}
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    res_nondict = ToolToolsetEnvVarParity().check(app20, "", _ctx(t20))
    assert len(res_nondict) == 5

    # Resolve by CES short ID, displayName, and dir name; leave one dangling key
    (t20 / "environment.json").write_text(
        json.dumps(
            {
                "tools": {
                    "ces-tool-42": {
                        "openApiTool": {
                            "url": "https://api.example.com",
                            "headers": {"X-Api-Key": "secret-123"},
                        }
                    },
                    "t_unquoted": {
                        "openApiTool": {"url": "https://u1.example.com"}
                    },
                    "t_single": {
                        "openApiTool": {"url": "https://u2.example.com"}
                    },
                    "t_double": {
                        "openApiTool": {"url": "https://u3.example.com"}
                    },
                    "stale_ghost": {"url": "https://old.example.com"},
                },
                "toolsets": {
                    "CRM Toolset": {
                        "openApiToolset": {"url": "https://crm.example.com"}
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    res_resolved = ToolToolsetEnvVarParity().check(app20, "", _ctx(t20))
    assert len(res_resolved) == 1
    assert res_resolved[0].severity == Severity.WARNING
    assert "stale_ghost" in res_resolved[0].message

    # T021: corrupt/empty/list/non-dict paths/non-list servers/invalid URLs/valid templates + path traversal
    t21 = tmp_path / "t21"
    outside_schema = tmp_path / "outside.yaml"
    outside_schema.write_text("bad: [yaml\n", encoding="utf-8")
    app21 = t21 / "app.json"
    t21.mkdir()
    app21.write_text(json.dumps({"displayName": "t21"}), encoding="utf-8")

    # Path traversal must NOT read outside.yaml
    (t21 / "tools" / "trav").mkdir(parents=True)
    (t21 / "tools" / "trav" / "trav.json").write_text(
        json.dumps(
            {
                "displayName": "trav",
                "openApiTool": {"openApiSchema": "../../outside.yaml"},
            }
        ),
        encoding="utf-8",
    )
    # Valid schema with $env_var, OpenAPI template URL, and relative /v1
    (t21 / "tools" / "good" / "open_api_tool").mkdir(parents=True)
    (
        t21 / "tools" / "good" / "open_api_tool" / "open_api_schema.yaml"
    ).write_text(
        yaml.safe_dump(
            {
                "openapi": "3.0.0",
                "servers": [
                    {"url": "$env_var"},
                    {"url": "https://{env}.example.com/api"},
                    {"url": "/v1"},
                ],
                "paths": {"/health": {"get": {}}},
            }
        ),
        encoding="utf-8",
    )
    # Bad cases: empty, list, non-dict paths, non-list servers, non-dict server item, bad URL, non-UTF-8
    bad_schemas = {
        "b_corrupt": "openapi: [unclosed\n",
        "b_empty": "",
        "b_list": "- item1\n",
        "b_paths_list": "openapi: '3.0.0'\npaths: []\n",
        "b_servers_dict": "openapi: '3.0.0'\npaths:\n  /a: {}\nservers: {}\n",
        "b_server_str": "openapi: '3.0.0'\npaths:\n  /a: {}\nservers:\n  - https://x.com\n",
        "b_url_bad": "openapi: '3.0.0'\npaths:\n  /a: {}\nservers:\n  - url: example.com/v1\n  - url: '   '\n",
    }
    for name, body in bad_schemas.items():
        d = t21 / "tools" / name / "open_api_tool"
        d.mkdir(parents=True)
        (d / "open_api_schema.yaml").write_text(body, encoding="utf-8")
    bin_d = t21 / "tools" / "b_utf8" / "open_api_tool"
    bin_d.mkdir(parents=True)
    (bin_d / "open_api_schema.yaml").write_bytes(b"\xff\xfe\x00")

    res21 = OpenApiSchemaValidity().check(app21, "", _ctx(t21))
    flagged_files = {r.file for r in res21}
    assert "outside.yaml" not in " ".join(flagged_files)
    assert "tools/good/open_api_tool/open_api_schema.yaml" not in flagged_files
    for name in (*bad_schemas.keys(), "b_utf8"):
        assert (
            f"tools/{name}/open_api_tool/open_api_schema.yaml" in flagged_files
        )


def test_qa_pr1_post_build_c025_syntax_markers_type_checking_and_cli_exits(
    tmp_path: Path,
) -> None:
    """Verify C025 syntax/non-UTF-8, malformed markers, TYPE_CHECKING, native env, symlinks, and CLI exits."""
    # 1. Unbuilt local python_lib -> WARNING (CLI exit 0); TYPE_CHECKING imports ignored
    c_warn = tmp_path / "c_warn"
    (c_warn / "agents" / "root" / "before_model_callbacks" / "cb1").mkdir(
        parents=True
    )
    (c_warn / "python_lib").mkdir(parents=True)
    (c_warn / "app.json").write_text(
        json.dumps({"name": "c", "displayName": "c", "rootAgent": "root"}),
        encoding="utf-8",
    )
    _write_ext_agent(c_warn, "root")
    (c_warn / "python_lib" / "helpers.py").write_text(
        "def ok(): return 1\n", encoding="utf-8"
    )
    (
        c_warn
        / "agents"
        / "root"
        / "before_model_callbacks"
        / "cb1"
        / "python_code.py"
    ).write_text(
        "import typing\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import python_lib.type_only_a\n"
        "if typing.TYPE_CHECKING:\n"
        "    from python_lib.type_only_b import T\n"
        "import python_lib.helpers\n",
        encoding="utf-8",
    )
    proc_warn = _run_cli_lint(
        "--app-dir", str(c_warn), "--rule", "C025", "--json"
    )
    assert proc_warn.returncode == 0
    payload_w = json.loads(proc_warn.stdout)
    assert len(payload_w) == 1
    assert payload_w[0]["severity"] == "warning"
    assert payload_w[0]["line"] == 7

    # 2. Missing everywhere -> ERROR (CLI exit 1)
    c_err = tmp_path / "c_err"
    (c_err / "agents" / "root" / "before_model_callbacks" / "cb1").mkdir(
        parents=True
    )
    (c_err / "app.json").write_text(
        json.dumps({"name": "c", "displayName": "c", "rootAgent": "root"}),
        encoding="utf-8",
    )
    _write_ext_agent(c_err, "root")
    (
        c_err
        / "agents"
        / "root"
        / "before_model_callbacks"
        / "cb1"
        / "python_code.py"
    ).write_text("from python_lib.sub.mod import fn\n", encoding="utf-8")
    proc_err = _run_cli_lint(
        "--app-dir", str(c_err), "--rule", "C025", "--json"
    )
    assert proc_err.returncode == 1
    assert json.loads(proc_err.stdout)[0]["severity"] == "error"

    # 3. Native pythonEnvFiles + inline bundle + malformed markers + syntax/non-UTF-8 + symlinks
    c_nat = tmp_path / "c_nat"
    outside_py = tmp_path / "outside_bad.py"
    outside_py.write_text("def broken(:\n", encoding="utf-8")
    (c_nat / "pythonEnvFiles" / "content" / "python_lib" / "sub").mkdir(
        parents=True
    )
    (c_nat / "python_lib").mkdir(parents=True)
    app_nat = c_nat / "app.json"
    app_nat.write_text(
        json.dumps({"displayName": "c_nat", "rootAgent": "root"}),
        encoding="utf-8",
    )
    _write_ext_agent(c_nat, "root")
    (c_nat / "pythonEnvFiles" / "pythonEnvFiles.yaml").write_text(
        "files: []\n", encoding="utf-8"
    )
    (
        c_nat / "pythonEnvFiles" / "content" / "python_lib" / "sub" / "mod.py"
    ).write_text("def fn(): return 1\n", encoding="utf-8")
    (
        c_nat / "pythonEnvFiles" / "content" / "python_lib" / "bad_syntax.py"
    ).write_text("def broken(:\n", encoding="utf-8")
    (c_nat / "python_lib" / "non_utf8.py").write_bytes(b"\xff\xfe\x00")
    os.symlink(outside_py, c_nat / "python_lib" / "sym_bad.py")

    cbs = c_nat / "agents" / "root" / "before_model_callbacks"
    for name, code in [
        ("cb_ok_native", "from python_lib.sub.mod import fn\n"),
        ("cb_missing_native", "from python_lib.sub.absent import fn\n"),
        (
            "cb_inline_ok",
            "# === BEGIN CXAS_SHARED_BUNDLE ===\n"
            "# === END CXAS_SHARED_BUNDLE ===\n"
            "import python_lib.bundled_mod\n",
        ),
        ("cb_trunc", "# === BEGIN CXAS_SHARED_BUNDLE ===\nx = 1\n"),
        (
            "cb_reversed",
            "# === END CXAS_SHARED_BUNDLE ===\n# === BEGIN CXAS_SHARED_BUNDLE ===\n",
        ),
        (
            "cb_type_else",
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    import python_lib.ok_only\n"
            "else:\n"
            "    import python_lib.missing_else\n",
        ),
    ]:
        d = cbs / name
        d.mkdir(parents=True)
        (d / "python_code.py").write_text(code, encoding="utf-8")

    res_nat = SharedPythonBundleIntegrity().check(app_nat, "", _ctx(c_nat))
    files_nat = {r.file for r in res_nat}
    assert "python_lib/sym_bad.py" not in files_nat
    assert (
        "agents/root/before_model_callbacks/cb_ok_native/python_code.py"
        not in files_nat
    )
    assert (
        "agents/root/before_model_callbacks/cb_inline_ok/python_code.py"
        not in files_nat
    )
    assert {
        "pythonEnvFiles/content/python_lib/bad_syntax.py",
        "python_lib/non_utf8.py",
        "agents/root/before_model_callbacks/cb_missing_native/python_code.py",
        "agents/root/before_model_callbacks/cb_trunc/python_code.py",
        "agents/root/before_model_callbacks/cb_reversed/python_code.py",
        "agents/root/before_model_callbacks/cb_type_else/python_code.py",
    }.issubset(files_nat)


def test_qa_pr1_post_build_a018_s018_s019_s020_edge_cases_and_cli_hygiene(
    tmp_path: Path,
) -> None:
    """Verify A018, S018, S019, S020 edge cases, CLI 121+ rules, Sample_Support_Agent clean, and zero build imports."""
    # A018: app.yml snake_case + case-insensitive BCP-47 + non-voice WEB no FP + malformed no crash
    a18_web = tmp_path / "a18_web"
    (a18_web / "deployments").mkdir(parents=True)
    app_web = a18_web / "app.yml"
    app_web.write_text(
        yaml.safe_dump(
            {
                "displayName": "web",
                "language_settings": {
                    "default_language_code": "en-US",
                    "supported_language_codes": ["en-US", "de-DE"],
                    "enable_multilingual_support": True,
                },
                "audio_processing_config": {
                    "synthesize_speech_configs": {"en-us": {"voice": "v1"}}
                },
            }
        ),
        encoding="utf-8",
    )
    (a18_web / "deployments" / "web.yml").write_text(
        yaml.safe_dump({"channel": "WEB_UI"}), encoding="utf-8"
    )
    assert (
        VoiceLanguageTtsParity().check(
            app_web, app_web.read_text(encoding="utf-8"), _ctx(a18_web)
        )
        == []
    )

    a18_mal = tmp_path / "a18_mal"
    a18_mal.mkdir()
    app_mal = a18_mal / "app.yaml"
    app_mal.write_text(
        yaml.safe_dump(
            {
                "displayName": "mal",
                "languageSettings": {
                    "defaultLanguageCode": 123,
                    "supportedLanguageCodes": "en-US",
                },
                "audioProcessingConfig": "not-a-dict",
            }
        ),
        encoding="utf-8",
    )
    assert (
        VoiceLanguageTtsParity().check(
            app_mal, app_mal.read_text(encoding="utf-8"), _ctx(a18_mal)
        )
        == []
    )

    # S018: displayName & CES path match rootAgent cleanly; corrupt/non-string no crash
    s18 = tmp_path / "s18"
    (s18 / "deployments").mkdir(parents=True)
    app18 = s18 / "app.json"
    app18.write_text(
        json.dumps({"displayName": "s18", "rootAgent": "Main Concierge"}),
        encoding="utf-8",
    )
    _write_ext_agent(
        s18,
        "root_agent",
        display_name="Main Concierge",
        cfg_name="projects/p/locations/us/apps/a/agents/root-id",
    )
    (s18 / "deployments" / "ok_disp.yml").write_text(
        yaml.safe_dump({"entryAgent": "Main Concierge"}), encoding="utf-8"
    )
    (s18 / "deployments" / "ok_ces.yaml").write_text(
        yaml.safe_dump(
            {"entry_agent": "projects/p/locations/us/apps/a/agents/root-id"}
        ),
        encoding="utf-8",
    )
    (s18 / "deployments" / "bad_val.yaml").write_text(
        yaml.safe_dump({"entryAgent": 123}), encoding="utf-8"
    )
    (s18 / "deployments" / "corrupt.yaml").write_text(
        "entryAgent: [unclosed\n", encoding="utf-8"
    )
    assert DeploymentEntryAgentParity().check(app18, "", _ctx(s18)) == []

    # S019: silent on dev/staging; on prod catches http, loopback, .local, custom patterns; skips $env_var & $schema
    s19_dev = tmp_path / "s19_dev"
    (s19_dev / "deployments").mkdir(parents=True)
    app19d = s19_dev / "app.json"
    app19d.write_text(json.dumps({"displayName": "d"}), encoding="utf-8")
    (s19_dev / "deployments" / "dev.yaml").write_text(
        yaml.safe_dump({"environment": "staging"}), encoding="utf-8"
    )
    (s19_dev / "environment.json").write_text(
        json.dumps({"tools": {"a": {"url": "http://localhost:8000"}}}),
        encoding="utf-8",
    )
    assert ProdUrlSafety().check(app19d, "", _ctx(s19_dev)) == []

    s19_prod = tmp_path / "s19_prod"
    (s19_prod / "deployments").mkdir(parents=True)
    (s19_prod / "tools" / "api" / "open_api_tool").mkdir(parents=True)
    (s19_prod / "toolsets" / "ts1").mkdir(parents=True)
    app19p = s19_prod / "app.json"
    app19p.write_text(json.dumps({"displayName": "p"}), encoding="utf-8")
    (s19_prod / "deployments" / "prod.yml").write_text(
        yaml.safe_dump({"environment": "PRODUCTION"}), encoding="utf-8"
    )
    (s19_prod / "environment.json").write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "tools": {
                    "ok": {"url": "https://api.example.com/v1"},
                    "env_only": {"url": "$env_var"},
                    "bad_http": {"url": "http://api.example.com/v1"},
                    "bad_v6": {"url": "https://[::1]:8443/v1"},
                    "bad_zero": {"url": "https://0.0.0.0:9000/v1"},
                    "bad_local": {"url": "https://svc.local/v1"},
                    "bad_pat": {"url": "https://uat.example.com/v1"},
                },
            }
        ),
        encoding="utf-8",
    )
    (s19_prod / "tools" / "api" / "api.json").write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "displayName": "api",
                "openApiTool": {"url": "https://127.0.0.1:8080/v1"},
            }
        ),
        encoding="utf-8",
    )
    (
        s19_prod / "tools" / "api" / "open_api_tool" / "open_api_schema.yml"
    ).write_text(
        yaml.safe_dump(
            {
                "openapi": "3.0.0",
                "servers": [
                    {"url": "$env_var"},
                    {"url": "https://localhost:8443/v1"},
                ],
                "paths": {"/p": {"get": {}}},
            }
        ),
        encoding="utf-8",
    )
    (s19_prod / "toolsets" / "ts1" / "ts1.yaml").write_text(
        yaml.safe_dump(
            {
                "displayName": "ts1",
                "openApiToolset": {"url": "https://staging.example.com/v1"},
            }
        ),
        encoding="utf-8",
    )
    res19 = ProdUrlSafety().check(
        app19p,
        "",
        _ctx(
            s19_prod,
            options={
                "S019": {
                    "forbidden_url_patterns": [
                        "staging.example.com",
                        r"^https://uat\.",
                        "[unclosed-regex",
                        123,
                    ]
                }
            },
        ),
    )
    msgs19 = "\n".join(r.message for r in res19)
    assert "json-schema.org" not in msgs19
    assert "https://api.example.com/v1" not in msgs19
    for expected in (
        "http://api.example.com/v1",
        "[::1]:8443",
        "0.0.0.0:9000",
        "svc.local",
        "127.0.0.1:8080",
        "localhost:8443",
        "uat.example.com",
        "staging.example.com",
    ):
        assert expected in msgs19

    # S020: self (dir, displayName, case-insensitive, space-delimited), orphan, child->parent clean, path traversal blocked
    s20 = tmp_path / "s20"
    (tmp_path / "outside_inst.txt").write_text(
        "Self {@AGENT: root_agent}\n", encoding="utf-8"
    )
    app20 = s20 / "app.json"
    s20.mkdir()
    app20.write_text(
        json.dumps({"displayName": "s20", "rootAgent": "root_agent"}),
        encoding="utf-8",
    )
    _write_ext_agent(
        s20,
        "root_agent",
        display_name="Main Desk",
        child_agents=["child_agent"],
        with_instruction=False,
        extra={"instruction": "../../outside_inst.txt"},
    )
    _write_ext_agent(s20, "child_agent", display_name="Child Desk")
    (s20 / "agents" / "child_agent" / "instruction.txt").write_text(
        "Return to {@AGENT: Main Desk}\n"
        "Self space {@AGENT Child Desk}\n"
        "Self lower {@agent: child_agent}\n"
        "Orphan {@AGENT: orphan_agent}\n",
        encoding="utf-8",
    )
    _write_ext_agent(s20, "orphan_agent", display_name="Orphan")
    res20 = AgentRefSelfOrOrphan().check(app20, "", _ctx(s20))
    assert len(res20) == 3
    assert all(r.file == "agents/child_agent/instruction.txt" for r in res20)
    assert [r.line for r in res20] == [2, 3, 4]

    # Real CLI --list-rules (121+ rules) + Sample_Support_Agent clean + zero build imports
    reg = build_registry()
    assert len(reg.all_rules()) >= 121
    new_ids = ("T020", "T021", "C025", "A018", "S018", "S019", "S020")
    for rid in new_ids:
        assert reg.get(rid) is not None
    list_proc = _run_cli_lint("--list-rules")
    assert list_proc.returncode == 0
    for rid in new_ids:
        assert rid in list_proc.stdout

    sample_dir = (
        _WORKTREE_ROOT
        / ".agents"
        / "skills"
        / "cxas-agent-foundry"
        / "assets"
        / "project-template"
        / "cxas_app"
    )
    sample_proc = _run_cli_lint("--app-dir", str(sample_dir))
    assert sample_proc.returncode == 0
    assert "0 error(s), 0 warning(s)" in sample_proc.stdout

    utils_dir = _SRC_ROOT / "cxas_scrapi" / "utils"
    for py_file in [
        utils_dir / "linter.py",
        utils_dir / "lint_rules" / "config.py",
        utils_dir / "lint_rules" / "tools.py",
        utils_dir / "lint_rules" / "callbacks.py",
        utils_dir / "lint_rules" / "structure.py",
    ]:
        assert "cxas_scrapi.build" not in py_file.read_text(encoding="utf-8")


# ── Open Defect Reproductions (Commit e12fa23e: QA-PR1-013..016) ────────


def test_qa_pr1_013_t020_nested_empty_env_var_must_not_be_masked_by_sibling_key(
    tmp_path: Path,
) -> None:
    """QA-PR1-013 [HIGH]: T020 must NOT let sibling/top-level keys mask an explicitly empty or missing nested $env_var override."""
    app = tmp_path / "t020_mask"
    (app / "tools" / "api_tool").mkdir(parents=True)
    (app / "tools" / "ds_faq").mkdir(parents=True)
    app_json = app / "app.json"
    app_json.write_text(
        json.dumps({"displayName": "t020_mask", "rootAgent": "root"}),
        encoding="utf-8",
    )
    # Case A: openApiTool.auth.url explicitly set to "" must NOT be masked by openApiTool.url
    (app / "tools" / "api_tool" / "api_tool.json").write_text(
        json.dumps(
            {
                "displayName": "api_tool",
                "openApiTool": {
                    "url": "$env_var",
                    "auth": {"url": "$env_var"},
                },
            }
        ),
        encoding="utf-8",
    )
    # Case B: dataStoreSource.dataStore.name omitted/empty must NOT be masked by top-level "name"
    (app / "tools" / "ds_faq" / "ds_faq.json").write_text(
        json.dumps(
            {
                "displayName": "ds_faq",
                "dataStoreTool": {
                    "name": "ds_faq",
                    "dataStoreSource": {"dataStore": {"name": "$env_var"}},
                },
            }
        ),
        encoding="utf-8",
    )
    (app / "environment.json").write_text(
        json.dumps(
            {
                "tools": {
                    "api_tool": {
                        "openApiTool": {
                            "url": "https://api.example.com",
                            "auth": {"url": ""},
                        }
                    },
                    "ds_faq": {
                        "name": "ds_faq",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    res = ToolToolsetEnvVarParity().check(app_json, "", _ctx(app))
    error_files = {r.file for r in res if r.severity == Severity.ERROR}
    assert error_files == {
        "tools/api_tool/api_tool.json",
        "tools/ds_faq/ds_faq.json",
    }, f"Unresolved nested $env_var was masked by sibling/top-level key: {res}"


def test_qa_pr1_014_a018_voice_app_missing_audio_config_or_empty_tts_must_warn(
    tmp_path: Path,
) -> None:
    """QA-PR1-014 [MEDIUM]: A018 must warn when a voice deployment omits audioProcessingConfig or has null/empty synthesizeSpeechConfigs entries."""
    # Case A: voice deployment with NO audioProcessingConfig in app.json
    app_no_audio = tmp_path / "voice_no_audio"
    (app_no_audio / "deployments").mkdir(parents=True)
    cfg_a = app_no_audio / "app.json"
    cfg_a.write_text(
        json.dumps(
            {
                "displayName": "voice_no_audio",
                "languageSettings": {
                    "defaultLanguageCode": "en-US",
                    "supportedLanguageCodes": ["en-US", "de-DE"],
                    "enableMultilingualSupport": True,
                },
            }
        ),
        encoding="utf-8",
    )
    (app_no_audio / "deployments" / "voice.yaml").write_text(
        yaml.safe_dump({"channel": "VOICE_TELEPHONY"}), encoding="utf-8"
    )
    res_a = VoiceLanguageTtsParity().check(
        cfg_a, cfg_a.read_text(encoding="utf-8"), _ctx(app_no_audio)
    )
    assert len(res_a) == 2, (
        f"Expected 2 TTS warnings when voice app omits audioProcessingConfig, got {res_a}"
    )

    # Case B: voice deployment with null and empty {} TTS entries
    app_empty_tts = tmp_path / "voice_empty_tts"
    (app_empty_tts / "deployments").mkdir(parents=True)
    cfg_b = app_empty_tts / "app.json"
    cfg_b.write_text(
        json.dumps(
            {
                "displayName": "voice_empty_tts",
                "languageSettings": {
                    "defaultLanguageCode": "en-US",
                    "supportedLanguageCodes": ["en-US", "de-DE"],
                    "enableMultilingualSupport": True,
                },
                "audioProcessingConfig": {
                    "synthesizeSpeechConfigs": {"en-US": None, "de-DE": {}}
                },
            }
        ),
        encoding="utf-8",
    )
    (app_empty_tts / "deployments" / "voice.yml").write_text(
        yaml.safe_dump({"channel": "VOICE_TELEPHONY"}), encoding="utf-8"
    )
    res_b = VoiceLanguageTtsParity().check(
        cfg_b, cfg_b.read_text(encoding="utf-8"), _ctx(app_empty_tts)
    )
    assert len(res_b) == 2, (
        f"Expected 2 TTS warnings for null/empty synthesizeSpeechConfigs entries, got {res_b}"
    )


def test_qa_pr1_015_s019_prod_url_safety_must_catch_schemeless_loopback(
    tmp_path: Path,
) -> None:
    """QA-PR1-015 [MEDIUM]: S019 must catch scheme-less loopback hosts like localhost:8080/api and 127.0.0.1:5000 in prod builds."""
    app = tmp_path / "s019_schemeless"
    (app / "deployments").mkdir(parents=True)
    app_json = app / "app.json"
    app_json.write_text(json.dumps({"displayName": "p"}), encoding="utf-8")
    (app / "deployments" / "prod.yml").write_text(
        yaml.safe_dump({"environment": "PRODUCTION"}), encoding="utf-8"
    )
    (app / "environment.json").write_text(
        json.dumps(
            {
                "tools": {
                    "api": {
                        "url": "localhost:8080/api",
                        "endpoint": "127.0.0.1:5000/v1",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    res = ProdUrlSafety().check(app_json, "", _ctx(app))
    msgs = "\n".join(r.message for r in res)
    assert "localhost:8080/api" in msgs
    assert "127.0.0.1:5000/v1" in msgs


def test_qa_pr1_016_t021_uppercase_scheme_and_is_voice_app_symlink_safety(
    tmp_path: Path,
) -> None:
    """QA-PR1-016 [LOW]: T021 must accept case-insensitive HTTPS:// URLs (RFC 3986) and is_voice_app must skip symlinked deployments/."""
    # Case A: T021 uppercase HTTPS:// scheme
    app_t21 = tmp_path / "t21_upper"
    (app_t21 / "tools" / "api" / "open_api_tool").mkdir(parents=True)
    cfg_t21 = app_t21 / "app.json"
    cfg_t21.write_text(json.dumps({"displayName": "p"}), encoding="utf-8")
    (
        app_t21 / "tools" / "api" / "open_api_tool" / "open_api_schema.yaml"
    ).write_text(
        yaml.safe_dump(
            {
                "openapi": "3.0.0",
                "servers": [{"url": "HTTPS://api.example.com/v1"}],
                "paths": {"/ping": {"get": {}}},
            }
        ),
        encoding="utf-8",
    )
    assert OpenApiSchemaValidity().check(cfg_t21, "", _ctx(app_t21)) == []

    # Case B: is_voice_app must NOT follow symlinked deployments/ outside app_root
    app_sym = tmp_path / "sym_voice"
    outside_deps = tmp_path / "outside_deps"
    outside_deps.mkdir()
    (outside_deps / "voice.yaml").write_text(
        yaml.safe_dump({"channel": "VOICE_TELEPHONY"}), encoding="utf-8"
    )
    app_sym.mkdir()
    (app_sym / "app.json").write_text(
        json.dumps({"displayName": "web_only"}), encoding="utf-8"
    )
    os.symlink(outside_deps, app_sym / "deployments")
    assert is_voice_app(_ctx(app_sym)) is False


def test_qa_pr1_017_s019_malformed_ipv6_or_bracketed_url_must_not_crash(
    tmp_path: Path,
) -> None:
    """QA-PR1-017 [MEDIUM]: S019 must not crash with unhandled ValueError on malformed IPv6 or bracketed placeholder URLs."""
    app = tmp_path / "s019_ipv6_crash"
    (app / "deployments").mkdir(parents=True)
    app_json = app / "app.json"
    app_json.write_text(json.dumps({"displayName": "p"}), encoding="utf-8")
    (app / "deployments" / "prod.yaml").write_text(
        yaml.safe_dump({"environment": "prod"}), encoding="utf-8"
    )
    (app / "environment.json").write_text(
        json.dumps(
            {
                "tools": {
                    "bad_ipv6": {"url": "http://[::1/bad"},
                    "bracket_placeholder": {"url": "[your-host]:8080/v1"},
                }
            }
        ),
        encoding="utf-8",
    )

    # Must not raise ValueError: Invalid IPv6 URL; cleartext http:// must still be flagged
    res = ProdUrlSafety().check(app_json, "", _ctx(app))
    assert any("http://[::1/bad" in r.message for r in res)
