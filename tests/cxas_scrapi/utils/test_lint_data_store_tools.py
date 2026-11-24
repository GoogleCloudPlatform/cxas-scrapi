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


def _write_tool(app_root: Path, name: str, data: dict[str, Any]) -> Path:
    tool_dir = app_root / "tools" / name
    tool_dir.mkdir(parents=True, exist_ok=True)
    path = tool_dir / f"{name}.json"
    path.write_text(json.dumps(data))
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
