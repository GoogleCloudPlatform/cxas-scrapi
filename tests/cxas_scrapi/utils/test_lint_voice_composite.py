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

"""Unit tests for Gemini Composite v2 & Live Audio lint rules (A013, I017-I019)."""

import json
from pathlib import Path

from cxas_scrapi.migration import config as migration_config
from cxas_scrapi.utils.linter import (
    Discovery,
    LintConfig,
    LintReport,
    build_context,
    build_registry,
    run_rules,
)


def _make_minimal_app(
    tmp_path: Path,
    model: str = "gemini-composite-v2",
    speech_instruction: str = "",
    custom_voice_samples: list | None = None,
    instruction_text: str = "<role>Today is {{current_date}}.</role>",
    global_instruction_text: str | None = None,
    callback_code: str | None = None,
) -> LintReport:
    app_dir = tmp_path / "app"
    agent_dir = app_dir / "agents" / "root_agent"
    agent_dir.mkdir(parents=True)

    audio_cfg: dict = {
        "synthesizeSpeechConfigs": {
            "en-US": {
                "voice": "en-US-Chirp3-HD-Aoede",
                "instruction": speech_instruction,
            }
        }
    }
    if custom_voice_samples is not None:
        audio_cfg["customVoiceSamples"] = custom_voice_samples

    app_json: dict = {
        "name": "test_app",
        "displayName": "Test App",
        "rootAgent": "root_agent",
        "modelSettings": {"model": model},
        "audioProcessingConfig": audio_cfg,
    }
    if global_instruction_text is not None:
        app_json["globalInstruction"] = "global_instruction.txt"
        (app_dir / "global_instruction.txt").write_text(global_instruction_text)

    (app_dir / "app.json").write_text(json.dumps(app_json))

    agent_json: dict = {
        "name": "root_agent",
        "displayName": "root_agent",
        "instruction": "agents/root_agent/instruction.txt",
        "tools": ["end_session"],
    }
    if callback_code is not None:
        cb_dir = agent_dir / "before_model_callbacks" / "cb_01"
        cb_dir.mkdir(parents=True)
        (cb_dir / "python_code.py").write_text(callback_code)
        agent_json["beforeModelCallbacks"] = [
            {
                "pythonCode": "agents/root_agent/before_model_callbacks/cb_01/python_code.py"
            }
        ]

    (agent_dir / "root_agent.json").write_text(json.dumps(agent_json))
    (agent_dir / "instruction.txt").write_text(instruction_text)

    cfg = LintConfig(app_dir="app")
    discovery = Discovery(app_dir, tmp_path / "evals")
    ctx = build_context(tmp_path, cfg, discovery)
    registry = build_registry()
    report = LintReport()
    run_rules(registry, cfg, ctx, discovery, report)
    return report


def test_default_migration_model_is_composite_v2() -> None:
    assert migration_config.DEFAULT_MODEL == "gemini-composite-v2"
    assert "gemini-composite-v2" in migration_config.AGENT_MODELS


def test_a013_flags_directors_notes_and_custom_voice_mode(
    tmp_path: Path,
) -> None:
    report = _make_minimal_app(
        tmp_path,
        model="gemini-composite-v2",
        speech_instruction="# Director's Notes\nSpeak warmly like a radio host.",
        custom_voice_samples=[
            {"name": "brand_voice", "voiceInstructionMode": "DEFAULT"}
        ],
    )
    a013 = [r for r in report.results if r.rule_id == "A013"]
    assert len(a013) == 2


def test_a013_passes_empty_instruction_on_composite_v2(tmp_path: Path) -> None:
    report = _make_minimal_app(
        tmp_path,
        model="gemini-composite-v2",
        speech_instruction="",
        custom_voice_samples=[
            {"name": "brand_voice", "voiceInstructionMode": "NO_INSTRUCTION"}
        ],
    )
    assert not [r for r in report.results if r.rule_id == "A013"]


def test_i017_catches_invalid_composite_v2_tags_in_instructions_and_callbacks(
    tmp_path: Path,
) -> None:
    inst = (
        "<role>Today is {{current_date}}.</role>\n"
        'Say: "<slow>Your code is 1234.</prosody> [sigh] <pausa corta> Done."\n'
        "Never write [laugh] or </prosody> in responses.\n"
        "If user sentiment is [calm], proceed.\n"
    )
    cb = (
        "from typing import Optional\n"
        "def before_model_callback(callback_context: CallbackContext, llm_request: LlmRequest) -> Optional[LlmResponse]:\n"
        '    return LlmResponse.from_parts(parts=[Part.from_text(text="[warm] One moment.</prosody>")])\n'
    )
    report = _make_minimal_app(
        tmp_path,
        model="gemini-composite-v2",
        instruction_text=inst,
        callback_code=cb,
    )
    i017 = [r for r in report.results if r.rule_id == "I017"]
    # Instruction hits: <slow>, </prosody>, [sigh], <pausa corta> (4)
    # Negated line & label context [calm] must NOT trigger!
    # Callback hits: [warm], </prosody> (2)
    assert len(i017) == 6


def test_i018_flags_normalization_without_scope_guard_and_passes_with_guard(
    tmp_path: Path,
) -> None:
    unguarded = (
        '<instruction id="text normalization for speech">\n'
        "Spell out all phone numbers digit by digit.\n"
        "</instruction>\n"
    )
    report_bad = _make_minimal_app(
        tmp_path / "bad",
        model="gemini-composite-v2",
        global_instruction_text=unguarded,
    )
    assert [r for r in report_bad.results if r.rule_id == "I018"]

    guarded = (
        '<instruction id="text normalization for speech">\n'
        "Scope: Apply ONLY to spoken words. NEVER apply to tool call arguments or JSON payloads.\n"
        "Spell out phone numbers clearly.\n"
        "</instruction>\n"
    )
    report_good = _make_minimal_app(
        tmp_path / "good",
        model="gemini-composite-v2",
        global_instruction_text=guarded,
    )
    assert not [r for r in report_good.results if r.rule_id == "I018"]


def test_i019_flags_inline_tags_on_live_audio_model(tmp_path: Path) -> None:
    inst = (
        "<role>Today is {{current_date}}.</role>\n"
        'Say: "<warm> Hello there! <short pause> How can I help?"\n'
    )
    cb = (
        "from typing import Optional\n"
        "def before_model_callback(callback_context: CallbackContext, llm_request: LlmRequest) -> Optional[LlmResponse]:\n"
        '    return LlmResponse.from_parts(parts=[Part.from_customized_response(content="<prosody rate=\\"80%\\">Hi!")])\n'
    )
    report = _make_minimal_app(
        tmp_path,
        model="gemini-3.1-flash-live",
        instruction_text=inst,
        callback_code=cb,
    )
    i019 = [r for r in report.results if r.rule_id == "I019"]
    assert len(i019) == 3


def test_sample_support_agent_template_lints_clean_on_composite_v2() -> None:
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
    assert ctx.model == "gemini-composite-v2"
    registry = build_registry()
    report = LintReport()
    run_rules(registry, cfg, ctx, discovery, report)
    assert report.errors == []
    assert report.warnings == []
