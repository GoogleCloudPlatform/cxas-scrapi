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

"""Regression tests for audit_agent_v2.py (Composite V2 auditor/migrator).

Run with:
    uv run pytest .agents/skills/cxas-composite-v2-voice-agent-optimizer/scripts/tests -q
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from typing import Any

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "audit_agent_v2.py"
_SPEC = importlib.util.spec_from_file_location("audit_agent_v2", _SCRIPT)
aud = importlib.util.module_from_spec(_SPEC)
sys.modules["audit_agent_v2"] = aud
_SPEC.loader.exec_module(aud)

GLOBAL_OK = (
    '<instruction id="audio tag format">\n'
    "Open empathetic sentences with <calm><empathetic>. Put CAPITAL letters "
    "on one key word per turn.\n"
    "</instruction>\n"
    '<instruction id="text normalization for speech">\n'
    "Scope: these rules apply only to spoken words, never to tool calls or "
    "tool arguments.\n"
    "</instruction>\n"
)


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_app(
    root: pathlib.Path,
    *,
    model: str = "gemini-composite-v2",
    speech: dict[str, Any] | None = None,
    supported: tuple[str, ...] = (),
    global_instruction: str | None = GLOBAL_OK,
    agent_instruction: str = "Help the caller.\n",
    extra_app: dict[str, Any] | None = None,
) -> pathlib.Path:
    app: dict[str, Any] = {
        "displayName": "Test app",
        "modelSettings": {"model": model},
        "languageSettings": {
            "defaultLanguageCode": "en-AU",
            "supportedLanguageCodes": list(supported),
        },
        "audioProcessingConfig": {
            "synthesizeSpeechConfigs": speech
            if speech is not None
            else {
                "en-AU": {
                    "voice": "en-AU-Chirp3-HD-Aoede",
                    "speakingRate": 1.0,
                    "instruction": "",
                }
            }
        },
    }
    if global_instruction is not None:
        app["globalInstruction"] = "global_instruction.txt"
        _write(root / "global_instruction.txt", global_instruction)
    app.update(extra_app or {})
    _write(root / "app.json", json.dumps(app, indent=2) + "\n")
    _write(
        root / "agents" / "root" / "root.json",
        json.dumps(
            {
                "displayName": "root",
                "instruction": "agents/root/instruction.txt",
            }
        ),
    )
    _write(root / "agents" / "root" / "instruction.txt", agent_instruction)
    return root


def _issues(app_dir: pathlib.Path) -> list[dict[str, Any]]:
    res = aud.VoiceAgentV2Auditor(app_dir).audit()
    return [i for p in res["passes"].values() for i in p["issues"]]


def _codes(app_dir: pathlib.Path) -> list[str]:
    return [i["code"] for i in _issues(app_dir)]


# --- migrate_text_to_v2 -----------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        'Say: "<prosody rate="70%"> Sure, <warm> let me check."',
        'Say: "[prosody rate="70%"] Sure, [warm] let me check."',
    ],
)
def test_prosody_utterance_is_not_corrupted(text: str) -> None:
    out, _ = aud.migrate_text_to_v2(text)
    assert out == 'Say: "<prosody rate="70%"><warm> Sure, let me check."'


def test_slow_tags_map_to_prosody_rate() -> None:
    out, _ = aud.migrate_text_to_v2("Read it with [slow], then <slower>.")
    assert out == (
        'Read it with <prosody rate="65%">, then <prosody rate="65%">.'
    )


def test_singular_spellings_are_canonicalized() -> None:
    out, _ = aud.migrate_text_to_v2("Use [whisper] here and [exhale] there.")
    assert out == "Use <whispers> here and <exhales> there."


def test_closing_prosody_becomes_reset_when_speech_continues() -> None:
    out, notes = aud.migrate_text_to_v2(
        '"[prosody rate="65%"] I understand. </prosody> Let\'s try again."'
    )
    assert out == (
        '"<prosody rate="65%"> I understand. <prosody rate="100%"> Let\'s try again."'
    )
    assert any("reset" in n for n in notes)


def test_closing_prosody_at_end_is_removed() -> None:
    out, _ = aud.migrate_text_to_v2(
        '"<prosody rate="65%"> All done.</prosody>"'
    )
    assert out == '"<prosody rate="65%"> All done."'


def test_prosody_moves_to_front_of_stack() -> None:
    out, _ = aud.migrate_text_to_v2(
        '"<empathetic><prosody rate="70%"> I\'m sorry."'
    )
    assert out == '"<prosody rate="70%"><empathetic> I\'m sorry."'


def test_lifted_tone_tag_stacks_onto_opening_tag() -> None:
    out, _ = aud.migrate_text_to_v2('"<calm> I am sorry, <empathetic> rough."')
    assert out == '"<calm><empathetic> I am sorry, rough."'


def test_mid_sentence_pause_gets_a_comma() -> None:
    out, _ = aud.migrate_text_to_v2('"I checked it now <long pause> it works."')
    assert out == '"I checked it now, <long pause> it works."'


def test_remap_deduplicates_tag_lists() -> None:
    out, _ = aud.migrate_text_to_v2("Use <empathetic>, <warm>, <reassuring>.")
    assert out == "Use <empathetic>, <warm>."


def test_links_and_placeholders_are_untouched() -> None:
    text = "See [warm](https://example.com) and read [BOOKING_ID] back."
    assert aud.migrate_text_to_v2(text)[0] == text


# --- audit findings ---------------------------------------------------------


def test_unevaluated_and_misspelled_tags_are_reported(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(
        tmp_path,
        global_instruction=GLOBAL_OK
        + 'Say "<excited> Great news." or open with [clam].\n'
        + "Never write tags in square brackets, like [sad].\n",
    )
    issues = _issues(app)
    unevaluated = [i for i in issues if i["code"] == "UNEVALUATED_V2_TAG"]
    brackets = [i for i in issues if i["code"] == "UNRECOGNIZED_BRACKET_TOKEN"]
    assert [i["message"].split("'")[1] for i in unevaluated] == ["<excited>"]
    assert "enthusiasm" in unevaluated[0]["recommended"]
    assert len(brackets) == 1
    assert "[clam]" in brackets[0]["message"]
    assert "<calm>" in brackets[0]["message"]


def test_sigh_on_transfer_and_goodbye_turns_is_reported(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(
        tmp_path,
        global_instruction=GLOBAL_OK
        + "Never use <sigh> or <exhales> in a turn that transfers the call.\n",
        agent_instruction=(
            "On a dispute, say \"<sigh> That is frustrating. I'll put you "
            'through now." and call {@TOOL: transfer_to_human}.\n'
            'On goodbye, say "<exhales> Have a great day." and call '
            "{@TOOL: end_session}.\n"
            'When the light is green, say "<exhales> You are online." and '
            "hand back to {@AGENT: concierge}.\n"
        ),
    )
    _write(
        app
        / "tools"
        / "transfer_to_human"
        / "python_function"
        / "python_code.py",
        "def transfer_to_human(reason: str) -> dict:\n"
        '  """Transfers the call.\n\n'
        "  Say something like '<sigh> I'll put you through now.'\n"
        '  """\n'
        "  return {}\n",
    )
    _write(
        app
        / "agents"
        / "root"
        / "after_model_callbacks"
        / "cb"
        / "python_code.py",
        'TRANSFER_LINES = {"en": "<sigh> I\'ll put you through now."}\n'
        'BRIDGE_LINES = {"en": "<exhales> One sec."}\n',
    )
    found = [
        (i["file"], i["line"])
        for i in _issues(app)
        if i["code"] == "SIGH_ON_ENDING_OR_TRANSFER_TURN"
    ]
    assert ("agents/root/instruction.txt", 1) in found
    assert ("agents/root/instruction.txt", 2) in found
    assert ("agents/root/instruction.txt", 3) not in found
    assert (
        "tools/transfer_to_human/python_function/python_code.py",
        4,
    ) in found
    assert ("agents/root/after_model_callbacks/cb/python_code.py", 1) in found
    assert not any(f == "global_instruction.txt" for f, _ in found)
    assert len(found) == 4


def test_inline_global_instruction_is_audited(tmp_path: pathlib.Path) -> None:
    app = _make_app(
        tmp_path,
        global_instruction=None,
        extra_app={"globalInstruction": "Open with [reassuring] when needed."},
    )
    legacy = [
        i for i in _issues(app) if i["code"] == "LEGACY_V1_SQUARE_BRACKET_TAG"
    ]
    assert [i["file"] for i in legacy] == ["app.json:globalInstruction"]


def test_missing_instruction_pointer_is_reported(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(
        tmp_path,
        global_instruction=None,
        extra_app={"globalInstruction": "missing_global.txt"},
    )
    assert "MISSING_INSTRUCTION_FILE" in _codes(app)


def test_empty_app_is_not_reported_as_compliant(tmp_path: pathlib.Path) -> None:
    _write(tmp_path / "app.json", json.dumps({"displayName": "empty"}))
    res = aud.VoiceAgentV2Auditor(tmp_path).audit()
    codes = [i["code"] for p in res["passes"].values() for i in p["issues"]]
    assert res["overall_status"] == "FAILED"
    assert "NO_INSTRUCTIONS_FOUND" in codes


def test_temperature_is_advisory_only(tmp_path: pathlib.Path) -> None:
    app = _make_app(tmp_path, extra_app={})
    data = json.loads((app / "app.json").read_text())
    data["modelSettings"]["temperature"] = 0.3
    _write(app / "app.json", json.dumps(data))
    advisory = [i for i in _issues(app) if i["code"] == "TEMPERATURE_ADVISORY"]
    assert [i["priority"] for i in advisory] == ["P2"]


def test_callback_inline_tag_must_cover_used_tags(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(tmp_path)
    cb = (
        app
        / "agents"
        / "root"
        / "after_model_callbacks"
        / "cb"
        / "python_code.py"
    )
    _write(
        cb,
        "import re\n"
        'INLINE_TAG = re.compile(r"(<(?:breath|short pause|empathetic)>)", '
        "re.IGNORECASE)\n",
    )
    issues = [
        i for i in _issues(app) if i["code"] == "CALLBACK_MISSING_V2_TAG_REGEX"
    ]
    assert len(issues) == 1
    assert "<calm>" in issues[0]["message"]
    _write(
        cb,
        "import re\n"
        'INLINE_TAG = re.compile(r"(<(?:calm|empathetic)>)", re.IGNORECASE)\n',
    )
    assert "CALLBACK_MISSING_V2_TAG_REGEX" not in _codes(app)


def test_structural_pass_reports_skipped_without_scrapi(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(aud, "Discovery", None)
    res = aud.VoiceAgentV2Auditor(_make_app(tmp_path)).audit()
    assert res["passes"]["structural_rules"]["skipped"] is True
    assert "structural_rules" in res["skipped_passes"]


def test_handoff_wording_has_no_internal_terms() -> None:
    assert aud.HANDOFF_TARGETS["transferToNga"] == "another CX Agent Studio app"


def test_json_escaped_tags_are_audited_and_migrated(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(tmp_path)
    tool_json = app / "tools" / "transfer_to_human" / "transfer_to_human.json"
    _write(
        tool_json,
        '{"description": "Say \\u003csigh\\u003e I\'ll put you through, '
        'or open with \\u003creassuring\\u003e."}\n',
    )
    codes = _codes(app)
    assert "SIGH_ON_ENDING_OR_TRANSFER_TURN" in codes
    assert "STALE_V2_AUDIO_TAG" in codes
    aud.VoiceAgentV2Auditor(app).remediate()
    out = tool_json.read_text()
    assert "\\u003cempathetic\\u003e" in out
    assert "<" not in out
    assert json.loads(out)["description"].endswith("<empathetic>.")


# --- remediation ------------------------------------------------------------

DIRECTORS_NOTE = (
    "# AUDIO PROFILE: Maya, a friendly support agent in her 30s.\n"
    "# DIRECTOR'S NOTES: Style: warm. Accent: Australian English.\n"
    "## Transcript:\n"
)


def test_remediate_clears_styles_and_keeps_persona(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(
        tmp_path,
        model="gemini-composite-v1",
        supported=("es-US",),
        speech={
            "en-AU": {
                "voice": "en-AU-Chirp3-HD-Aoede",
                "speakingRate": 0.95,
                "instruction": DIRECTORS_NOTE,
            }
        },
        extra_app={
            "audioProcessingConfig": {
                "synthesizeSpeechConfigs": {
                    "en-AU": {
                        "voice": "en-AU-Chirp3-HD-Aoede",
                        "speakingRate": 0.95,
                        "instruction": DIRECTORS_NOTE,
                    }
                },
                "customVoiceSamples": [
                    {"name": "brand", "voiceInstruction": DIRECTORS_NOTE}
                ],
            }
        },
    )
    data = json.loads((app / "app.json").read_text())
    data["modelSettings"]["temperature"] = 0.3
    _write(app / "app.json", json.dumps(data, indent=2))

    res = aud.VoiceAgentV2Auditor(app).remediate()
    out = json.loads((app / "app.json").read_text())
    speech = out["audioProcessingConfig"]["synthesizeSpeechConfigs"]
    assert out["modelSettings"] == {
        "model": "gemini-composite-v2",
        "temperature": 0.3,
    }
    assert speech["en-AU"] == {
        "voice": "en-AU-Chirp3-HD-Aoede",
        "speakingRate": 0.95,
        "instruction": "",
    }
    assert speech["es-US"] == {
        "voice": "es-US-Chirp3-HD-Aoede",
        "speakingRate": 0.95,
        "instruction": "",
    }
    assert (
        out["audioProcessingConfig"]["customVoiceSamples"][0][
            "voiceInstruction"
        ]
        == ""
    )
    assert any("Maya" in s for s in res["removed_styles"])


def test_remediate_never_injects_a_style(tmp_path: pathlib.Path) -> None:
    app = _make_app(
        tmp_path,
        speech={"en-AU": {"voice": "en-AU-Chirp3-HD-Aoede"}},
    )
    aud.VoiceAgentV2Auditor(app).remediate()
    speech = json.loads((app / "app.json").read_text())[
        "audioProcessingConfig"
    ]["synthesizeSpeechConfigs"]
    assert "instruction" not in speech["en-AU"]
    assert "speakingRate" not in speech["en-AU"]


def test_remediate_leaves_other_models_and_voices(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(
        tmp_path,
        model="gemini-2.5-flash",
        speech={"en-AU": {"voice": "en-AU-Neural2-B", "instruction": ""}},
    )
    aud.VoiceAgentV2Auditor(app).remediate()
    out = json.loads((app / "app.json").read_text())
    assert out["modelSettings"]["model"] == "gemini-2.5-flash"
    assert (
        out["audioProcessingConfig"]["synthesizeSpeechConfigs"]["en-AU"][
            "voice"
        ]
        == "en-AU-Neural2-B"
    )
    codes = _codes(app)
    assert "MODEL_NOT_COMPOSITE_V2" in codes
    assert "NON_CHIRP3_HD_VOICE" in codes


def test_dry_run_writes_nothing(tmp_path: pathlib.Path) -> None:
    app = _make_app(tmp_path, agent_instruction='Say "[sigh] Okay."\n')
    before = (app / "agents" / "root" / "instruction.txt").read_text()
    res = aud.VoiceAgentV2Auditor(app, dry_run=True).remediate()
    assert res["status"] == "DRY_RUN"
    assert any("<sigh> Okay." in d for d in res["planned_diffs"])
    assert (app / "agents" / "root" / "instruction.txt").read_text() == before


def test_backup_keeps_a_copy(tmp_path: pathlib.Path) -> None:
    app = _make_app(tmp_path, agent_instruction='Say "[sigh] Okay."\n')
    aud.VoiceAgentV2Auditor(app, backup=True).remediate()
    inst = app / "agents" / "root" / "instruction.txt"
    assert inst.read_text() == 'Say "<sigh> Okay."\n'
    assert inst.with_name("instruction.txt.bak").read_text() == (
        'Say "[sigh] Okay."\n'
    )


def test_remediation_is_idempotent(tmp_path: pathlib.Path) -> None:
    app = _make_app(
        tmp_path,
        model="gemini-composite-v1",
        agent_instruction='Say "Sure, [warm] let me check [slow]."\n',
    )
    aud.VoiceAgentV2Auditor(app).remediate()
    second = aud.VoiceAgentV2Auditor(app).remediate()
    assert second["status"] == "NO_CHANGES_NEEDED"


def test_translated_tags_are_audited_and_migrated(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(
        tmp_path,
        global_instruction=GLOBAL_OK
        + "Always keep tags in English (<short pause>, never <pausa corta>).\n",
        agent_instruction=(
            'Say "<empático> Lo siento, <pausa corta> déjeme revisar."\n'
            'In Mandarin, say "<短暂停顿> 稍等一下。"\n'
            'In Hindi, say "[छोटा विराम] जी, एक सेकंड रुकिए।"\n'
        ),
    )
    issues = [i for i in _issues(app) if i["code"] == "TRANSLATED_AUDIO_TAG"]
    assert len(issues) == 4
    assert not any(i["file"] == "global_instruction.txt" for i in issues)

    aud.VoiceAgentV2Auditor(app).remediate()
    migrated = (app / "agents" / "root" / "instruction.txt").read_text()
    assert (
        migrated
        == 'Say "<empathetic> Lo siento, <short pause> déjeme revisar."\n'
        'In Mandarin, say "<short pause> 稍等一下。"\n'
        'In Hindi, say "<short pause> जी, एक सेकंड रुकिए।"\n'
    )
    assert "TRANSLATED_AUDIO_TAG" not in _codes(app)


def test_mixed_latin_in_devanagari_utterance_is_reported(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(
        tmp_path,
        global_instruction=GLOBAL_OK
        + "- Hindi: write acronyms in Devanagari (e.g., 'वाई-फाई' instead of 'WiFi', 'सिम' instead of 'SIM').\n",
        agent_instruction=(
            'Bad: "नमस्ते! <short pause> मैं आज आपके WiFi राउटर या SIM कार्ड में आपकी कैसे मदद कर सकता हूँ?"\n'
            'Good: "नमस्ते! <short pause> मैं आज आपके वाई-फाई राउटर या सिम कार्ड में कैसे मदद कर सकती हूँ?"\n'
        ),
    )
    issues = [
        i
        for i in _issues(app)
        if i["code"] == "MIXED_LATIN_IN_DEVANAGARI_UTTERANCE"
    ]
    assert len(issues) == 1
    assert issues[0]["file"] == "agents/root/instruction.txt"
    assert issues[0]["line"] == 1
    assert "SIM" in issues[0]["message"]
    assert "WiFi" in issues[0]["message"]


def test_multilingual_support_and_locale_accent_mismatch(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(
        tmp_path,
        supported=("es-US", "hi-IN"),
        speech={
            "en-AU": {
                "voice": "en-AU-Chirp3-HD-Aoede",
                "instruction": "Australian woman in her early 30s, warm mid-pitch voice",
            },
            "es-US": {
                "voice": "es-US-Chirp3-HD-Aoede",
                "instruction": "Australian woman in her early 30s, warm mid-pitch voice",
            },
            "hi-IN": {
                "voice": "hi-IN-Chirp3-HD-Aoede",
                "instruction": "",
            },
        },
    )
    codes = _codes(app)
    assert "MULTILINGUAL_SUPPORT_NOT_ENABLED" in codes
    assert "LOCALE_SPEAKER_PROFILE_ACCENT_MISMATCH" in codes

    mismatch = [
        i
        for i in _issues(app)
        if i["code"] == "LOCALE_SPEAKER_PROFILE_ACCENT_MISMATCH"
    ]
    assert len(mismatch) == 1
    assert "'es-US'" in mismatch[0]["message"]

    aud.VoiceAgentV2Auditor(app).remediate()
    out = json.loads((app / "app.json").read_text())
    assert out["languageSettings"]["enableMultilingualSupport"] is True
    assert "MULTILINGUAL_SUPPORT_NOT_ENABLED" not in _codes(app)


def test_multilingual_sentence_boundaries_and_pause_commas() -> None:
    # Multi-part tone transitions after CJK (。) and Devanagari (।) sentence
    # endings must stay at the start of sentence 2, not lift to sentence 1.
    cjk = '"<calm><empathetic> 很抱歉您的航班取消了。<positive> 好消息是我已经帮您改签了。"'
    assert aud.migrate_text_to_v2(cjk)[0] == cjk

    hindi = '"<calm><empathetic> मुझे खेद है। <positive> अच्छी खबर यह है।"'
    assert aud.migrate_text_to_v2(hindi)[0] == hindi

    # Mid-sentence pause after an accented Spanish letter gets a comma.
    es_pause = '"Ya revisé <short pause> todo está bien."'
    assert (
        aud.migrate_text_to_v2(es_pause)[0]
        == '"Ya revisé, <short pause> todo está bien."'
    )

    # All-caps placeholders ([PAUSA]) and label contexts (sentiment: [Positivo])
    # are untouched.
    keep = "Read [PAUSA] and check if sentiment: [Positivo]."
    assert aud.migrate_text_to_v2(keep)[0] == keep


def test_english_apostrophes_do_not_false_positive_devanagari_check(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(
        tmp_path,
        global_instruction=GLOBAL_OK
        + "When the caller's language is Hindi, say 'मैं आपकी मदद कर सकती हूँ' so it doesn't sound stiff.\n",
    )
    assert "MIXED_LATIN_IN_DEVANAGARI_UTTERANCE" not in _codes(app)


def test_custom_voice_audit_and_remediation(tmp_path: pathlib.Path) -> None:
    app = _make_app(
        tmp_path,
        supported=("es-US",),
        extra_app={
            "audioProcessingConfig": {
                "synthesizeSpeechConfigs": {
                    "en-AU": {
                        "voice": "OptusClonedVoice",
                        "speakingRate": 1.0,
                        "instruction": "Australian woman in her early 30s",
                        "voiceSampleGcsUri": "gs://legacy-bucket/inline.wav",
                    }
                },
                "customVoiceSamples": [
                    {
                        "name": "OptusClonedVoice",
                        "voiceSampleGcsUri": "gs://bucket/sample.wav",
                        "consentAudioGcsUri": "",
                        "voiceInstruction": "<agent_voice>Warm mid-pitch voice</agent_voice>",
                        "voiceInstructionMode": "GENERATE_INSTRUCTION",
                        "previewAudioContent": "UklGRiQAAABXQVZFZm10IBAAAA==",
                    },
                    {
                        "name": "UnusedBrandVoice",
                        "voiceSampleGcsUri": "gs://bucket/unused.wav",
                        "consentAudioGcsUri": "gs://bucket/consent.wav",
                        "voiceInstruction": "Friendly advisor",
                        "voiceInstructionMode": "NO_INSTRUCTION",
                    },
                ],
            }
        },
    )
    codes = _codes(app)
    assert "CUSTOM_VOICE_MISSING_GCS_URI" in codes
    assert "CUSTOM_VOICE_DIRECTORS_NOTE" in codes
    assert "CUSTOM_VOICE_INSTRUCTION_MODE_NOT_DISABLED" in codes
    assert "CUSTOM_VOICE_INSTRUCTION_TOO_LONG" in codes
    assert "UNBOUND_CUSTOM_VOICE_SAMPLE" in codes
    assert "CUSTOM_VOICE_STALE_PREVIEW_AUDIO_BLOB" in codes
    assert "DEPRECATED_INLINE_VOICE_SAMPLE_URI" in codes
    assert "CUSTOM_VOICE_LOCALE_STYLE_CONFLICT" in codes

    res = aud.VoiceAgentV2Auditor(app).remediate()
    out = json.loads((app / "app.json").read_text())
    samples = out["audioProcessingConfig"]["customVoiceSamples"]
    assert samples[0]["voiceInstruction"] == ""
    assert samples[0]["voiceInstructionMode"] == "NO_INSTRUCTION"
    assert "previewAudioContent" not in samples[0]
    assert samples[1]["voiceInstruction"] == ""
    assert samples[1]["voiceInstructionMode"] == "NO_INSTRUCTION"
    assert any("agent_voice" in s for s in res["removed_styles"])

    # Missing es-US locale inherits the custom voice key instead of Chirp3-HD-Zephyr,
    # and en-AU's conflicting short speaking style is cleared to ""
    speech = out["audioProcessingConfig"]["synthesizeSpeechConfigs"]
    assert speech["en-AU"]["instruction"] == ""
    assert speech["es-US"]["voice"] == "OptusClonedVoice"
    assert speech["es-US"]["instruction"] == ""


def test_bind_custom_voice_auto_one_pass(tmp_path: pathlib.Path) -> None:
    app = _make_app(
        tmp_path,
        supported=("es-US",),
        extra_app={
            "audioProcessingConfig": {
                "synthesizeSpeechConfigs": {
                    "en-AU": {
                        "voice": "en-AU-Chirp3-HD-Aoede",
                        "speakingRate": 1.0,
                        "instruction": "warm, friendly, natural, professional",
                    },
                    "es-US": {
                        "voice": "es-US-Chirp3-HD-Aoede",
                        "speakingRate": 1.0,
                        "instruction": "warm, friendly, natural, professional",
                    },
                },
                "customVoiceSamples": [
                    {
                        "name": "Claire",
                        "voiceSampleGcsUri": "gs://bucket/claire.wav",
                        "consentAudioGcsUri": "gs://bucket/claire_consent.wav",
                        "voiceInstruction": "<agent_voice>Bright voice</agent_voice>",
                        "voiceInstructionMode": "GENERATE_INSTRUCTION",
                        "previewAudioContent": "UklGRiQAAABXQVZFZm10IBAAAA==",
                    }
                ],
            }
        },
    )
    res = aud.VoiceAgentV2Auditor(app, bind_custom_voice="auto").remediate()
    assert res["post_remediation_audit"]["overall_status"] == "PASSED"
    out = json.loads((app / "app.json").read_text())
    speech = out["audioProcessingConfig"]["synthesizeSpeechConfigs"]
    assert speech["en-AU"]["voice"] == "Claire"
    assert speech["en-AU"]["instruction"] == ""
    assert speech["es-US"]["voice"] == "Claire"
    assert speech["es-US"]["instruction"] == ""
    sample = out["audioProcessingConfig"]["customVoiceSamples"][0]
    assert sample["voiceInstruction"] == ""
    assert sample["voiceInstructionMode"] == "NO_INSTRUCTION"
    assert "previewAudioContent" not in sample
