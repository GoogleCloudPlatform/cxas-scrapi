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

"""Unit tests for data store tool lint rules T017-T019."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

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
    build_registry,
    run_rules,
)

_CLEAN_AUDIO_CONFIG = {
    "modalityType": "AUDIO",
    "snippetsConfig": {"enableSnippets": True},
    "rewriterConfig": {"disabled": True},
    "summarizationConfig": {"disabled": True},
}


def _context(app_root: Path) -> LintContext:
    return LintContext(
        project_root=app_root,
        app_dir=app_root,
        evals_dir=app_root / "evals",
        app_root=app_root,
    )


def _write_tool(
    app_root: Path, name: str, data: dict[str, Any], ext: str = ".json"
) -> Path:
    tool_dir = app_root / "tools" / name
    tool_dir.mkdir(parents=True, exist_ok=True)
    path = tool_dir / f"{name}{ext}"
    text = (
        json.dumps(data)
        if ext == ".json"
        else yaml.safe_dump(data, sort_keys=False)
    )
    path.write_text(text, encoding="utf-8")
    return path


def _write_voice_deployment(
    app_root: Path,
    channel_type: str = "GOOGLE_TELEPHONY_PLATFORM",
) -> None:
    deps = app_root / "deployments"
    deps.mkdir(parents=True, exist_ok=True)
    (deps / "voice.yaml").write_text(
        f"channelProfile:\n  channelType: {channel_type}\n"
    )


# ── T017 ────────────────────────────────────────────────────────────────


def test_t017_static_filter_passes(tmp_path: Path) -> None:
    path = _write_tool(
        tmp_path, "ds_search", {"dataStoreTool": {"filter": "user_id = 1"}}
    )
    rule = DataStoreFilterTemplateSyntax()
    assert rule.check(path, path.read_text(), _context(tmp_path)) == []


def test_t017_template_in_tool_filter_flagged(tmp_path: Path) -> None:
    path = _write_tool(
        tmp_path, "ds_search", {"dataStoreTool": {"filter": "user = {user}"}}
    )
    rule = DataStoreFilterTemplateSyntax()
    results = rule.check(path, path.read_text(), _context(tmp_path))
    assert [r.rule_id for r in results] == ["T017"]


def test_t017_template_in_source_filter_flagged(tmp_path: Path) -> None:
    path = _write_tool(
        tmp_path,
        "ds_search",
        {
            "dataStoreTool": {
                "dataStoreSources": [{"filter": "brand = {{ brand }}"}]
            }
        },
    )
    rule = DataStoreFilterTemplateSyntax()
    results = rule.check(path, path.read_text(), _context(tmp_path))
    assert [r.rule_id for r in results] == ["T017"]


def test_t017_template_in_engine_source_flagged(tmp_path: Path) -> None:
    path = _write_tool(
        tmp_path,
        "ds_search",
        {"dataStoreTool": {"engineSource": {"filter": "region = {region}"}}},
    )
    rule = DataStoreFilterTemplateSyntax()
    results = rule.check(path, path.read_text(), _context(tmp_path))
    assert [r.rule_id for r in results] == ["T017"]


def test_t017_malformed_or_non_dict_json_does_not_crash(tmp_path: Path) -> None:
    tool_dir = tmp_path / "tools" / "ds_search"
    tool_dir.mkdir(parents=True)
    path = tool_dir / "ds_search.json"
    rule = DataStoreFilterTemplateSyntax()
    for raw in ("not-json", "[]", '{"dataStoreTool": null}'):
        path.write_text(raw)
        assert rule.check(path, raw, _context(tmp_path)) == []


# ── T018 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("behavior", ["NEVER_INCLUDE", 3])
def test_t018_never_include_passes(tmp_path: Path, behavior: str | int) -> None:
    path = _write_tool(
        tmp_path,
        "ds_search",
        {
            "dataStoreTool": {
                "filter": "brand = 'brand_a'",
                "filterParameterBehavior": behavior,
            }
        },
    )
    rule = DataStoreFilterParameterBehavior()
    assert rule.check(path, path.read_text(), _context(tmp_path)) == []


def test_t018_filtered_tool_without_never_include_flagged(
    tmp_path: Path,
) -> None:
    path = _write_tool(
        tmp_path,
        "faq_search",
        {
            "dataStoreTool": {
                "filter": "brand = 'brand_a'",
                "filterParameterBehavior": "INCLUDE",
            }
        },
    )
    rule = DataStoreFilterParameterBehavior()
    results = rule.check(path, path.read_text(), _context(tmp_path))
    assert [r.rule_id for r in results] == ["T018"]


def test_t018_ds_prefixed_tool_without_behavior_flagged(tmp_path: Path) -> None:
    path = _write_tool(
        tmp_path,
        "ds_catalog",
        {"dataStoreTool": {"dataStoreSources": [{"dataStore": "catalog"}]}},
    )
    rule = DataStoreFilterParameterBehavior()
    results = rule.check(path, path.read_text(), _context(tmp_path))
    assert [r.rule_id for r in results] == ["T018"]


def test_t018_unfiltered_non_prefixed_tool_ignored(tmp_path: Path) -> None:
    path = _write_tool(tmp_path, "faq_search", {"dataStoreTool": {}})
    rule = DataStoreFilterParameterBehavior()
    assert rule.check(path, path.read_text(), _context(tmp_path)) == []


# ── T019 ────────────────────────────────────────────────────────────────


def test_t019_no_voice_deployment_skips(tmp_path: Path) -> None:
    path = _write_tool(tmp_path, "ds_search", {"dataStoreTool": {}})
    rule = DataStoreVoiceModality()
    assert rule.check(path, path.read_text(), _context(tmp_path)) == []


@pytest.mark.parametrize(
    "channel_type",
    ["GOOGLE_TELEPHONY_PLATFORM", "CONTACT_CENTER_AS_A_SERVICE"],
)
def test_t019_voice_deployment_with_clean_audio_config_passes(
    tmp_path: Path, channel_type: str
) -> None:
    _write_voice_deployment(tmp_path, channel_type=channel_type)
    path = _write_tool(
        tmp_path,
        "ds_search",
        {"dataStoreTool": {"modalityConfigs": [_CLEAN_AUDIO_CONFIG]}},
    )
    rule = DataStoreVoiceModality()
    assert rule.check(path, path.read_text(), _context(tmp_path)) == []


def test_t019_dict_modality_configs_passes(tmp_path: Path) -> None:
    _write_voice_deployment(tmp_path)
    path = _write_tool(
        tmp_path,
        "ds_search",
        {"dataStoreTool": {"modalityConfigs": {"AUDIO": _CLEAN_AUDIO_CONFIG}}},
    )
    rule = DataStoreVoiceModality()
    assert rule.check(path, path.read_text(), _context(tmp_path)) == []


def test_t019_empty_deployment_yaml_does_not_crash(tmp_path: Path) -> None:
    deps = tmp_path / "deployments"
    deps.mkdir()
    (deps / "empty.yaml").write_text("")
    (deps / "list.yaml").write_text("- item\n")
    _write_voice_deployment(tmp_path)
    path = _write_tool(
        tmp_path, "ds_search", {"dataStoreTool": {"filter": "brand = 'a'"}}
    )
    rule = DataStoreVoiceModality()
    results = rule.check(path, path.read_text(), _context(tmp_path))
    assert len(results) == 1
    assert "missing AUDIO" in results[0].message


def test_t019_voice_deployment_missing_audio_config_flagged(
    tmp_path: Path,
) -> None:
    _write_voice_deployment(tmp_path)
    path = _write_tool(
        tmp_path, "ds_search", {"dataStoreTool": {"filter": "brand = 'a'"}}
    )
    rule = DataStoreVoiceModality()
    results = rule.check(path, path.read_text(), _context(tmp_path))
    assert len(results) == 1
    assert "missing AUDIO" in results[0].message


def test_t019_incomplete_audio_config_flags_each_gap(tmp_path: Path) -> None:
    _write_voice_deployment(tmp_path)
    path = _write_tool(
        tmp_path,
        "ds_search",
        {"dataStoreTool": {"modalityConfigs": [{"modalityType": "AUDIO"}]}},
    )
    rule = DataStoreVoiceModality()
    messages = [
        r.message
        for r in rule.check(path, path.read_text(), _context(tmp_path))
    ]
    assert len(messages) == 3
    assert any("snippetsConfig" in m for m in messages)
    assert any("rewriterConfig" in m for m in messages)
    assert any("summarizationConfig" in m for m in messages)


# ── registry integration ───────────────────────────────────────────────


def test_rules_registered_and_dispatched(tmp_path: Path) -> None:
    (tmp_path / "app.json").write_text(json.dumps({"displayName": "app"}))
    _write_voice_deployment(tmp_path)
    _write_tool(
        tmp_path,
        "ds_search",
        {
            "displayName": "ds_search",
            "dataStoreTool": {
                "filter": "user_id = {user}",
                "filterParameterBehavior": "INCLUDE",
                "modalityConfigs": [{"modalityType": "AUDIO"}],
            },
        },
    )
    context = _context(tmp_path)
    context.all_tool_names = {"ds_search"}
    report = LintReport()
    run_rules(
        build_registry(),
        LintConfig(),
        context,
        Discovery(tmp_path, tmp_path / "evals"),
        report,
        specific_rules={"T017", "T018", "T019"},
    )
    assert {r.rule_id for r in report.results} == {"T017", "T018", "T019"}


# ── CES schema paths, YAML configs, voice channel variants ──────────────


@pytest.mark.parametrize(
    "payload",
    [
        {"dataStoreTool": {"dataStoreSource": {"filter": "b = {{brand}}"}}},
        {"data_store_tool": {"data_store_source": {"filter": "b = {brand}"}}},
        {
            "dataStoreTool": {
                "engineSource": {
                    "dataStoreSources": [{"filter": "{% if x %}a{% endif %}"}]
                }
            }
        },
        {
            "data_store_tool": {
                "engine_source": {
                    "data_store_sources": [{"filter": "region = {r}"}]
                }
            }
        },
    ],
)
@pytest.mark.parametrize("ext", [".json", ".yaml", ".yml"])
def test_t017_ces_source_paths_and_yaml_configs(
    tmp_path: Path, payload: dict[str, Any], ext: str
) -> None:
    path = _write_tool(tmp_path, "ds_search", payload, ext=ext)
    res = DataStoreFilterTemplateSyntax().check(
        path, path.read_text(), _context(tmp_path)
    )
    assert [r.rule_id for r in res] == ["T017"]


@pytest.mark.parametrize("behavior", ["NEVER_INCLUDE", 3])
def test_t018_snake_case_and_yaml_never_include_pass(
    tmp_path: Path, behavior: str | int
) -> None:
    path = _write_tool(
        tmp_path,
        "ds_kb",
        {
            "data_store_tool": {
                "data_store_source": {"filter": "brand = 'a'"},
                "filter_parameter_behavior": behavior,
            }
        },
        ext=".yaml",
    )
    assert (
        DataStoreFilterParameterBehavior().check(
            path, path.read_text(), _context(tmp_path)
        )
        == []
    )


@pytest.mark.parametrize(
    "behavior",
    [None, "UNSPECIFIED", "INCLUDE", "never_include", 0, 1, 2],
)
def test_t018_rejects_unsafe_or_non_canonical_behaviors(
    tmp_path: Path, behavior: Any
) -> None:
    ds_cfg: dict[str, Any] = {"dataStoreSource": {"filter": "brand = 'a'"}}
    if behavior is not None:
        ds_cfg["filterParameterBehavior"] = behavior
    path = _write_tool(tmp_path, "kb_search", {"dataStoreTool": ds_cfg})
    res = DataStoreFilterParameterBehavior().check(
        path, path.read_text(), _context(tmp_path)
    )
    assert [r.rule_id for r in res] == ["T018"]
    assert "with static filter" in res[0].message


def test_t018_ces_engine_source_and_ds_prefix_reason(tmp_path: Path) -> None:
    p_eng = _write_tool(
        tmp_path,
        "kb_eng",
        {
            "dataStoreTool": {
                "engineSource": {
                    "dataStoreSources": [{"filter": "brand = 'a'"}]
                }
            }
        },
    )
    p_ds = _write_tool(
        tmp_path,
        "ds_only",
        {"dataStoreTool": {"dataStoreSources": [{"dataStore": "cat"}]}},
        ext=".yaml",
    )
    rule = DataStoreFilterParameterBehavior()
    res_eng = rule.check(p_eng, p_eng.read_text(), _context(tmp_path))
    res_ds = rule.check(p_ds, p_ds.read_text(), _context(tmp_path))
    assert [r.rule_id for r in res_eng] == ["T018"]
    assert "with static filter" in res_eng[0].message
    assert [r.rule_id for r in res_ds] == ["T018"]
    assert "with ds_ prefix" in res_ds[0].message


def test_t019_snake_case_int_enum_yaml_and_empty_audio_dict(
    tmp_path: Path,
) -> None:
    _write_voice_deployment(tmp_path)
    rule = DataStoreVoiceModality()
    ctx = _context(tmp_path)

    # Snake-case + integer enum modalityType=2 in list and dict forms pass
    snake_ok = _write_tool(
        tmp_path,
        "ds_snake",
        {
            "data_store_tool": {
                "modality_configs": [
                    {
                        "modality_type": 2,
                        "snippets_config": {"enable_snippets": True},
                        "rewriter_config": {"disabled": True},
                        "summarization_config": {"disabled": True},
                    }
                ]
            }
        },
        ext=".yaml",
    )
    assert rule.check(snake_ok, snake_ok.read_text(), ctx) == []

    dict_int_ok = _write_tool(
        tmp_path,
        "ds_dict_int",
        {"dataStoreTool": {"modalityConfigs": {2: _CLEAN_AUDIO_CONFIG}}},
        ext=".yaml",
    )
    assert rule.check(dict_int_ok, dict_int_ok.read_text(), ctx) == []

    # TEXT-only warns missing AUDIO; empty {"AUDIO": {}} warns all 3 sub-gaps
    text_only = _write_tool(
        tmp_path,
        "ds_text",
        {"dataStoreTool": {"modalityConfigs": [{"modalityType": "TEXT"}]}},
    )
    res_text = rule.check(text_only, text_only.read_text(), ctx)
    assert len(res_text) == 1
    assert "missing AUDIO" in res_text[0].message

    empty_audio = _write_tool(
        tmp_path,
        "ds_empty_audio",
        {"dataStoreTool": {"modalityConfigs": {"AUDIO": {}}}},
    )
    assert len(rule.check(empty_audio, empty_audio.read_text(), ctx)) == 3


@pytest.mark.parametrize(
    "deployment_yaml",
    [
        "channel: VOICE\n",
        "channel: TELEPHONY\n",
        "channel: TWILIO\n",
        "channel: FIVE9\n",
        "channelProfile:\n  channelType: AUDIO\n",
    ],
)
def test_t019_detects_voice_channels_and_app_audio_config(
    tmp_path: Path, deployment_yaml: str
) -> None:
    deps = tmp_path / "deployments"
    deps.mkdir()
    (deps / "voice.yaml").write_text(deployment_yaml, encoding="utf-8")
    tool = _write_tool(tmp_path, "ds_kb", {"dataStoreTool": {}})
    res = DataStoreVoiceModality().check(
        tool, tool.read_text(), _context(tmp_path)
    )
    assert [r.rule_id for r in res] == ["T019"]


def test_t019_ignores_bare_audio_config_and_detects_voice_model(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.json").write_text(
        json.dumps(
            {
                "modelSettings": {"model": "gemini-2.5-flash"},
                "audioProcessingConfig": {"inactivityTimeout": "20s"},
            }
        ),
        encoding="utf-8",
    )
    tool = _write_tool(tmp_path, "ds_kb", {"dataStoreTool": {}})
    assert (
        DataStoreVoiceModality().check(
            tool, tool.read_text(), _context(tmp_path)
        )
        == []
    )

    (tmp_path / "app.json").write_text(
        json.dumps({"modelSettings": {"model": "gemini-composite-v1"}}),
        encoding="utf-8",
    )
    res = DataStoreVoiceModality().check(
        tool, tool.read_text(), _context(tmp_path)
    )
    assert [r.rule_id for r in res] == ["T019"]


def test_v003_valid_ces_data_store_tool_catches_t017_t018_without_schema_error(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.json").write_text(
        json.dumps({"name": "demo", "displayName": "Demo"}), encoding="utf-8"
    )
    _write_tool(
        tmp_path,
        "kb_ces",
        {
            "displayName": "kb_ces",
            "dataStoreTool": {
                "name": "kb_ces",
                "description": "Search knowledge base",
                "engineSource": {
                    "engine": "projects/p/locations/global/collections/c/engines/e",
                    "dataStoreSources": [
                        {
                            "filter": "brand = '{{brand}}'",
                            "dataStore": {
                                "name": "projects/p/locations/global/collections/c/dataStores/d"
                            },
                        }
                    ],
                },
            },
        },
    )
    disc = Discovery(tmp_path, tmp_path / "evals")
    ctx = _context(tmp_path)
    ctx.all_tool_names = {"kb_ces"}
    report = LintReport()
    run_rules(
        build_registry(),
        LintConfig(),
        ctx,
        disc,
        report,
        specific_rules={"V003", "T017", "T018"},
    )
    ids = {r.rule_id for r in report.results}
    assert "V003" not in ids
    assert {"T017", "T018"} <= ids


def test_qa_009_t018_top_level_filter_parameter_behavior(
    tmp_path: Path,
) -> None:
    """QA-009: T018 must honor top-level filterParameterBehavior when dataStoreSource is top-level."""
    path = _write_tool(
        tmp_path,
        "ds_top",
        {
            "displayName": "ds_top",
            "filterParameterBehavior": "NEVER_INCLUDE",
            "dataStoreSource": {"filter": "brand: ANY('acme')"},
        },
        ext=".yaml",
    )
    results = DataStoreFilterParameterBehavior().check(
        path, path.read_text(encoding="utf-8"), _context(tmp_path)
    )
    assert results == [], [str(r) for r in results]
