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

"""Unit tests for the C024 voice before_model mutation lint rule."""

from pathlib import Path

from cxas_scrapi.utils.lint_rules.callbacks import VoiceBeforeModelToolMutation
from cxas_scrapi.utils.lint_rules.tools import DataStoreVoiceModality
from cxas_scrapi.utils.linter import (
    Discovery,
    LintConfig,
    LintContext,
    LintReport,
    build_registry,
    is_voice_app,
    run_rules,
)

_MUTATING = (
    "def before_model_callback(cb, req):\n"
    "    req.hide_tool('x')\n"
    "    req.config = 1\n"
)


def _context(app_root: Path) -> LintContext:
    return LintContext(
        project_root=app_root,
        app_dir=app_root,
        evals_dir=app_root / "evals",
        app_root=app_root,
    )


def _callback(
    tmp_path: Path,
    folder: str = "before_model_callbacks",
    code: str = _MUTATING,
) -> Path:
    cb_file = tmp_path / folder / "python_code.py"
    cb_file.parent.mkdir(parents=True, exist_ok=True)
    cb_file.write_text(code)
    return cb_file


def test_c024_flags_each_mutation_in_voice_app(tmp_path: Path) -> None:
    (tmp_path / "app.json").write_text('{"audioProcessingConfig": {"x": 1}}')
    cb_file = _callback(tmp_path)
    res = VoiceBeforeModelToolMutation().check(
        cb_file, _MUTATING, _context(tmp_path)
    )
    assert [r.rule_id for r in res] == ["C024", "C024"]
    assert [r.line for r in res] == [2, 3]


def test_c024_flags_async_and_collection_mutators(tmp_path: Path) -> None:
    (tmp_path / "app.yaml").write_text("audioProcessingConfig:\n  x: 1\n")
    code = (
        "async def before_model_callback(cb, llm_req):\n"
        "    llm_req.tools.extend(['a'])\n"
        "    del llm_req.extra['k']\n"
    )
    cb_file = _callback(tmp_path, code=code)
    res = VoiceBeforeModelToolMutation().check(
        cb_file, code, _context(tmp_path)
    )
    assert [r.rule_id for r in res] == ["C024", "C024"]
    assert [r.line for r in res] == [2, 3]


def test_c024_detects_voice_via_deployment_yaml(tmp_path: Path) -> None:
    (tmp_path / "app.json").write_text("{}")
    (tmp_path / "deployments").mkdir()
    (tmp_path / "deployments" / "empty.yaml").write_text("")
    (tmp_path / "deployments" / "phone.yaml").write_text(
        "channel: GOOGLE_TELEPHONY_PLATFORM\n"
    )
    cb_file = _callback(tmp_path)
    res = VoiceBeforeModelToolMutation().check(
        cb_file, _MUTATING, _context(tmp_path)
    )
    assert len(res) == 2


def test_c024_detects_voice_via_channel_profile_yaml(tmp_path: Path) -> None:
    (tmp_path / "app.json").write_text("{}")
    (tmp_path / "deployments").mkdir()
    (tmp_path / "deployments" / "ccaa.yaml").write_text(
        "channelProfile:\n  channelType: CONTACT_CENTER_AS_A_SERVICE\n"
    )
    cb_file = _callback(tmp_path)
    res = VoiceBeforeModelToolMutation().check(
        cb_file, _MUTATING, _context(tmp_path)
    )
    assert len(res) == 2


def test_c024_ignores_text_only_app(tmp_path: Path) -> None:
    (tmp_path / "app.json").write_text("{}")
    cb_file = _callback(tmp_path)
    assert (
        VoiceBeforeModelToolMutation().check(
            cb_file, _MUTATING, _context(tmp_path)
        )
        == []
    )


def test_c024_ignores_other_callback_types(tmp_path: Path) -> None:
    (tmp_path / "app.json").write_text('{"audioProcessingConfig": {"x": 1}}')
    cb_file = _callback(tmp_path, folder="before_tool_callbacks")
    assert (
        VoiceBeforeModelToolMutation().check(
            cb_file, _MUTATING, _context(tmp_path)
        )
        == []
    )


def test_c024_flags_flat_ces_before_model_callback_dir(tmp_path: Path) -> None:
    (tmp_path / "app.json").write_text('{"audioProcessingConfig": {"x": 1}}')
    cb_file = (
        tmp_path
        / "agents"
        / "root_agent"
        / "before_model_callbacks_01"
        / "python_code.py"
    )
    cb_file.parent.mkdir(parents=True)
    cb_file.write_text(_MUTATING)
    res = VoiceBeforeModelToolMutation().check(
        cb_file, _MUTATING, _context(tmp_path)
    )
    assert [r.rule_id for r in res] == ["C024", "C024"]


def test_c024_dispatched_via_registry(tmp_path: Path) -> None:
    (tmp_path / "app.json").write_text(
        '{"displayName": "app", "audioProcessingConfig": {"x": 1}}'
    )
    cb_file = (
        tmp_path
        / "agents"
        / "root_agent"
        / "before_model_callbacks"
        / "cb_01"
        / "python_code.py"
    )
    cb_file.parent.mkdir(parents=True)
    cb_file.write_text(_MUTATING)
    report = LintReport()
    run_rules(
        build_registry(),
        LintConfig(),
        _context(tmp_path),
        Discovery(tmp_path, tmp_path / "evals"),
        report,
        specific_rules={"C024"},
    )
    assert [r.rule_id for r in report.results] == ["C024", "C024"]


def test_is_voice_app_voice_telephony_deployment_fires_t019_and_c024(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.json").write_text('{"displayName": "voice_app"}')
    deps = tmp_path / "deployments"
    deps.mkdir()
    (deps / "voice-telephony.yaml").write_text(
        "channel: VOICE_TELEPHONY\n"
        "channelProfile:\n"
        "  channelType: GOOGLE_TELEPHONY_PLATFORM\n",
        encoding="utf-8",
    )
    ctx = _context(tmp_path)
    assert is_voice_app(ctx) is True

    cb_file = _callback(tmp_path)
    assert [
        r.rule_id
        for r in VoiceBeforeModelToolMutation().check(cb_file, _MUTATING, ctx)
    ] == ["C024", "C024"]

    tool_file = tmp_path / "tools" / "ds_kb" / "ds_kb.json"
    tool_file.parent.mkdir(parents=True)
    tool_json = '{"dataStoreTool": {"filter": "brand = \'a\'"}}'
    tool_file.write_text(tool_json, encoding="utf-8")
    assert [
        r.rule_id
        for r in DataStoreVoiceModality().check(tool_file, tool_json, ctx)
    ] == ["T019"]


def test_is_voice_app_checks_channel_profile_even_when_channel_is_non_voice(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.json").write_text("{}")
    deps = tmp_path / "deployments"
    deps.mkdir()
    (deps / "custom.yml").write_text(
        "channel: CUSTOM_IVR\n"
        "channelProfile:\n"
        "  channelType: GOOGLE_TELEPHONY_PLATFORM\n",
        encoding="utf-8",
    )
    assert is_voice_app(_context(tmp_path)) is True


def test_is_voice_app_web_deployment_overrides_app_audio_processing_config(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.json").write_text(
        '{"displayName": "web_app", "audioProcessingConfig": '
        '{"synthesizeSpeechConfigs": {"en-US": {"voice": "v1"}}}}'
    )
    deps = tmp_path / "deployments"
    deps.mkdir()
    (deps / "web.yaml").write_text(
        "channel: WEB\nchannelProfile:\n  channelType: WEB_UI\n",
        encoding="utf-8",
    )
    ctx = _context(tmp_path)
    assert is_voice_app(ctx) is False

    cb_file = _callback(tmp_path)
    assert VoiceBeforeModelToolMutation().check(cb_file, _MUTATING, ctx) == []

    tool_file = tmp_path / "tools" / "ds_kb" / "ds_kb.json"
    tool_file.parent.mkdir(parents=True)
    tool_json = '{"dataStoreTool": {"filter": "brand = \'a\'"}}'
    tool_file.write_text(tool_json, encoding="utf-8")
    assert DataStoreVoiceModality().check(tool_file, tool_json, ctx) == []


def test_is_voice_app_falls_back_to_app_audio_config_without_deployments(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.json").write_text(
        '{"audioProcessingConfig": {"inactivityTimeout": "20s"}}'
    )
    assert is_voice_app(_context(tmp_path)) is True

    yml_app = tmp_path / "yml_app"
    yml_app.mkdir()
    (yml_app / "app.yml").write_text(
        "audioProcessingConfig:\n  inactivityTimeout: 20s\n",
        encoding="utf-8",
    )
    assert is_voice_app(_context(yml_app)) is True
