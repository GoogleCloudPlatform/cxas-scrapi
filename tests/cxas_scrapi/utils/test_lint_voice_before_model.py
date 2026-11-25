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

import os
from pathlib import Path

import pytest

from cxas_scrapi.utils.lint_rules.callbacks import VoiceBeforeModelToolMutation
from cxas_scrapi.utils.lint_rules.config import VoiceLanguageTtsParity
from cxas_scrapi.utils.lint_rules.tools import DataStoreVoiceModality
from cxas_scrapi.utils.linter import (
    LIVE_AUDIO_MODELS,
    Discovery,
    LintConfig,
    LintContext,
    LintReport,
    build_context,
    build_registry,
    is_voice_app,
    run_rules,
)

_MUTATING = (
    "def before_model_callback(cb, req):\n"
    "    req.hide_tool('x')\n"
    "    req.config = 1\n"
)


def _context(app_root: Path, *, model: str | None = None) -> LintContext:
    return LintContext(
        project_root=app_root,
        app_dir=app_root,
        evals_dir=app_root / "evals",
        app_root=app_root,
        model=model,
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


def test_c024_flags_each_mutation_in_before_model_callback(
    tmp_path: Path,
) -> None:
    rule = VoiceBeforeModelToolMutation()
    assert rule.models == LIVE_AUDIO_MODELS
    assert rule.is_applicable_for_model("gemini-2.5-flash-native-audio-preview")
    assert rule.is_applicable_for_model("gemini-3.1-flash-live")
    assert not rule.is_applicable_for_model("gemini-composite-v1")
    assert not rule.is_applicable_for_model("gemini-2.5-flash")

    cb_file = _callback(tmp_path)
    res = rule.check(cb_file, _MUTATING, _context(tmp_path))
    assert [r.rule_id for r in res] == ["C024", "C024"]
    assert [r.line for r in res] == [2, 3]


def test_c024_flags_async_and_collection_mutators(tmp_path: Path) -> None:
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


@pytest.mark.parametrize(
    ("model_name", "expected_ids"),
    [
        ("gemini-2.5-flash-native-audio-preview", ["C024", "C024"]),
        ("gemini-3.1-flash-live", ["C024", "C024"]),
        ("gemini-composite-v1", []),
        ("gemini-2.5-flash", []),
    ],
)
def test_c024_dispatched_only_for_live_audio_models(
    tmp_path: Path, model_name: str, expected_ids: list[str]
) -> None:
    """C024 fires on LIVE_AUDIO_MODELS and stays silent on composite-v1/text."""
    (tmp_path / "app.json").write_text(
        f'{{"displayName": "app", "modelSettings": {{"model": "{model_name}"}},'
        ' "audioProcessingConfig": {"inactivityTimeout": "20s"}}',
        encoding="utf-8",
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
    cb_file.write_text(_MUTATING, encoding="utf-8")
    disc = Discovery(tmp_path, tmp_path / "evals")
    cfg = LintConfig()
    ctx = build_context(tmp_path, cfg, disc)
    report = LintReport()
    run_rules(
        build_registry(),
        cfg,
        ctx,
        disc,
        report,
        specific_rules={"C024"},
    )
    assert [r.rule_id for r in report.results] == expected_ids


def test_c024_ignores_other_callback_types(tmp_path: Path) -> None:
    cb_file = _callback(tmp_path, folder="before_tool_callbacks")
    assert (
        VoiceBeforeModelToolMutation().check(
            cb_file, _MUTATING, _context(tmp_path)
        )
        == []
    )


def test_c024_flags_flat_ces_before_model_callback_dir(tmp_path: Path) -> None:
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


def test_is_voice_app_voice_telephony_deployment_fires_t019(
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


def test_is_voice_app_web_deployment_overrides_voice_model(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.json").write_text(
        '{"displayName": "web_app",'
        ' "modelSettings": {"model": "gemini-composite-v1"},'
        ' "audioProcessingConfig":'
        ' {"synthesizeSpeechConfigs": {"en-US": {"voice": "v1"}}}}'
    )
    deps = tmp_path / "deployments"
    deps.mkdir()
    (deps / "web.yaml").write_text(
        "channel: WEB\nchannelProfile:\n  channelType: WEB_UI\n",
        encoding="utf-8",
    )
    ctx = _context(tmp_path, model="gemini-composite-v1")
    assert is_voice_app(ctx) is False

    tool_file = tmp_path / "tools" / "ds_kb" / "ds_kb.json"
    tool_file.parent.mkdir(parents=True)
    tool_json = '{"dataStoreTool": {"filter": "brand = \'a\'"}}'
    tool_file.write_text(tool_json, encoding="utf-8")
    assert DataStoreVoiceModality().check(tool_file, tool_json, ctx) == []


def test_is_voice_app_bare_audio_config_on_text_model_is_false(
    tmp_path: Path,
) -> None:
    """Bare audioProcessingConfig + gemini-2.5-flash + empty channel is not voice."""
    app_json = tmp_path / "app.json"
    app_text = (
        '{"displayName": "sample",'
        ' "modelSettings": {"model": "gemini-2.5-flash"},'
        ' "defaultChannelProfile": {},'
        ' "languageSettings": {"defaultLanguageCode": "en-US",'
        ' "supportedLanguageCodes": ["en-US"]},'
        ' "audioProcessingConfig": {"inactivityTimeout": "20s"}}'
    )
    app_json.write_text(app_text, encoding="utf-8")
    tool_file = tmp_path / "tools" / "ds_kb" / "ds_kb.json"
    tool_file.parent.mkdir(parents=True)
    tool_text = '{"dataStoreTool": {"filter": "brand = \'a\'"}}'
    tool_file.write_text(tool_text, encoding="utf-8")
    cb_file = (
        tmp_path
        / "agents"
        / "root"
        / "before_model_callbacks"
        / "cb"
        / "python_code.py"
    )
    cb_file.parent.mkdir(parents=True)
    cb_file.write_text(_MUTATING, encoding="utf-8")

    disc = Discovery(tmp_path, tmp_path / "evals")
    ctx = build_context(tmp_path, LintConfig(), disc)
    assert is_voice_app(ctx) is False
    assert DataStoreVoiceModality().check(tool_file, tool_text, ctx) == []
    assert VoiceLanguageTtsParity().check(app_json, app_text, ctx) == []
    report = LintReport()
    run_rules(
        build_registry(),
        LintConfig(),
        ctx,
        disc,
        report,
        specific_rules={"T019", "A018", "C024"},
    )
    assert report.results == []


def test_is_voice_app_channel_profile_and_voice_model_precedence(
    tmp_path: Path,
) -> None:
    """defaultChannelProfile.channelType and VOICE_MODELS determine voice."""
    # Voice defaultChannelProfile overrides text model
    ch_voice = tmp_path / "ch_voice"
    ch_voice.mkdir()
    (ch_voice / "app.json").write_text(
        '{"modelSettings": {"model": "gemini-2.5-flash"},'
        ' "defaultChannelProfile": {"channelType": "TELEPHONY"}}',
        encoding="utf-8",
    )
    assert is_voice_app(_context(ch_voice, model="gemini-2.5-flash")) is True

    # Non-voice defaultChannelProfile overrides voice model
    ch_web = tmp_path / "ch_web"
    ch_web.mkdir()
    (ch_web / "app.json").write_text(
        '{"modelSettings": {"model": "gemini-composite-v1"},'
        ' "defaultChannelProfile": {"channelType": "WEB_UI"}}',
        encoding="utf-8",
    )
    assert is_voice_app(_context(ch_web, model="gemini-composite-v1")) is False

    # Voice model in app.yml triggers is_voice_app even without deployments
    yml_app = tmp_path / "yml_app"
    yml_app.mkdir()
    (yml_app / "app.yml").write_text(
        "modelSettings:\n  model: gemini-composite-v1\n",
        encoding="utf-8",
    )
    assert is_voice_app(_context(yml_app)) is True


def test_is_voice_app_ignores_non_utf8_deployment_and_app_files(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.json").write_text('{"displayName": "app"}')
    deps = tmp_path / "deployments"
    deps.mkdir()
    (deps / "00_corrupt.yaml").write_bytes(b"\xff\xfe\x00invalid")
    assert is_voice_app(_context(tmp_path)) is False

    # A valid voice deployment next to a corrupt non-UTF-8 file is still detected.
    (deps / "01_voice.yaml").write_text(
        "channel: VOICE_TELEPHONY\n", encoding="utf-8"
    )
    assert is_voice_app(_context(tmp_path)) is True

    # Non-UTF-8 app.json / app.yaml files do not crash is_voice_app.
    bad_json_app = tmp_path / "bad_json_app"
    bad_json_app.mkdir()
    (bad_json_app / "app.json").write_bytes(b"\xff\xfe\x00invalid")
    assert is_voice_app(_context(bad_json_app)) is False

    bad_yaml_app = tmp_path / "bad_yaml_app"
    bad_yaml_app.mkdir()
    (bad_yaml_app / "app.yaml").write_bytes(b"\xff\xfe\x00invalid")
    assert is_voice_app(_context(bad_yaml_app)) is False


def test_run_rules_ignores_non_utf8_deployment_file_without_crashing(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.json").write_text(
        '{"displayName": "app", "rootAgent": "root"}', encoding="utf-8"
    )
    ag_dir = tmp_path / "agents" / "root"
    ag_dir.mkdir(parents=True)
    (ag_dir / "root.json").write_text(
        '{"displayName": "root", "instruction": "agents/root/instruction.txt"}',
        encoding="utf-8",
    )
    (ag_dir / "instruction.txt").write_text("Hello.\n", encoding="utf-8")
    deps = tmp_path / "deployments"
    deps.mkdir()
    (deps / "00_corrupt.yaml").write_bytes(b"\xff\xfe\x00invalid")

    report = LintReport()
    run_rules(
        build_registry(),
        LintConfig(),
        _context(tmp_path),
        Discovery(tmp_path, tmp_path / "evals"),
        report,
    )
    assert isinstance(report.results, list)


@pytest.mark.parametrize(
    ("code", "expected_count"),
    [
        # AugAssign, subscript, and all collection mutators
        (
            "def before_model_callback(cb, req):\n"
            "    req.tools += ['a']\n"
            "    req.tools[0] = 'b'\n"
            "    req.tools.append('c')\n"
            "    req.tools.insert(0, 'd')\n"
            "    req.tools.pop()\n"
            "    req.tools.remove('a')\n"
            "    req.tools.clear()\n"
            "    req.config.update({'k': 1})\n"
            "    req.config.setdefault('m', 2)\n",
            9,
        ),
        # posonlyargs, tuple unpack, chained assign (1 finding), AnnAssign w/ & w/o value
        (
            "def before_model_callback(cb, req, /):\n"
            "    req.a, other = 1, 2\n"
            "    x = req.b = 3\n"
            "    req.c: int = 4\n"
            "    req.d: int\n",
            3,
        ),
        # Alias tracking & setattr/delattr; copies and rebinds must NOT flag
        (
            "def before_model_callback(cb, req):\n"
            "    t = req.tools\n"
            "    t.append('x')\n"
            "    setattr(req, 'x', 1)\n"
            "    delattr(req, 'y')\n"
            "    copy_t = list(req.tools)\n"
            "    copy_t.append('safe')\n"
            "    t = []\n"
            "    t.append('safe_after_rebind')\n"
            "    setattr(copy_t, 'z', 1)\n",
            3,
        ),
        # Read-only callback and invalid syntax must not flag or crash
        (
            "def before_model_callback(cb, req):\n"
            "    names = [t.name for t in req.tools]\n"
            "    return None\n",
            0,
        ),
        ("def before_model_callback(cb, req:\n    pass\n", 0),
    ],
)
def test_c024_ast_edge_cases_and_alias_safety(
    tmp_path: Path, code: str, expected_count: int
) -> None:
    (tmp_path / "app.json").write_text('{"audioProcessingConfig": {"x": 1}}')
    cb_file = _callback(tmp_path, code=code)
    res = VoiceBeforeModelToolMutation().check(
        cb_file, code, _context(tmp_path)
    )
    assert len(res) == expected_count
    assert all(r.rule_id == "C024" for r in res)


@pytest.mark.parametrize(
    "folder", ["before_agent_callbacks", "after_model_callbacks"]
)
def test_c024_ignores_non_before_model_callbacks(
    tmp_path: Path, folder: str
) -> None:
    (tmp_path / "app.json").write_text('{"audioProcessingConfig": {"x": 1}}')
    cb_file = _callback(tmp_path, folder=folder)
    assert (
        VoiceBeforeModelToolMutation().check(
            cb_file, _MUTATING, _context(tmp_path)
        )
        == []
    )


def test_is_voice_app_skips_symlinked_deployments_dir(tmp_path: Path) -> None:
    outside = tmp_path / "outside_deps"
    outside.mkdir()
    (outside / "voice.yaml").write_text(
        "channel: VOICE_TELEPHONY\n", encoding="utf-8"
    )
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.json").write_text('{"displayName": "web"}', encoding="utf-8")
    os.symlink(outside, app / "deployments")
    assert is_voice_app(_context(app)) is False


def test_is_voice_app_detects_agent_level_voice_model(
    tmp_path: Path,
) -> None:
    """is_voice_app recognizes voice models declared at agent level."""
    (tmp_path / "app.json").write_text(
        '{"name": "demo", "displayName": "Demo", "rootAgent": "root"}',
        encoding="utf-8",
    )
    ag_dir = tmp_path / "agents" / "root"
    ag_dir.mkdir(parents=True)
    (ag_dir / "root.json").write_text(
        '{"displayName": "Root", "modelSettings": {"model": "gemini-composite-v2"}}',
        encoding="utf-8",
    )
    disc = Discovery(tmp_path, tmp_path / "evals")
    ctx = build_context(tmp_path, LintConfig(), disc)
    assert is_voice_app(ctx) is True
