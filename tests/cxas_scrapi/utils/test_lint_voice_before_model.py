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
from cxas_scrapi.utils.linter import (
    Discovery,
    LintConfig,
    LintContext,
    LintReport,
    build_registry,
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
