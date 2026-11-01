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

"""Unit and regression tests for audit_agent.py (Composite v2 optimizer)."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from typing import Any

import pytest

from cxas_scrapi.utils import lint_rules as _sdk_lint_rules  # noqa: F401
from cxas_scrapi.utils import linter as _linter

_PRE_IDS = set(_linter._REGISTERED_IDS)
_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "audit_agent.py"
_SPEC = importlib.util.spec_from_file_location("audit_agent", _SCRIPT)
assert _SPEC is not None
assert _SPEC.loader is not None
aud = importlib.util.module_from_spec(_SPEC)
sys.modules["audit_agent"] = aud
_SPEC.loader.exec_module(aud)

_ADDED_IDS = set(_linter._REGISTERED_IDS) - _PRE_IDS
_ADDED_RULES = [
    (cat, r)
    for cat, rules in _linter._RULE_REGISTRY.items()
    for r in list(rules)
    if (r.id, cat) in _ADDED_IDS
]


def _set_cv2_rules_registered(active: bool) -> None:
    for cat, rule_obj in _ADDED_RULES:
        key = (rule_obj.id, cat)
        if active and key not in _linter._REGISTERED_IDS:
            _linter._REGISTERED_IDS.add(key)
            _linter._RULE_REGISTRY[cat].append(rule_obj)
        elif not active and key in _linter._REGISTERED_IDS:
            _linter._REGISTERED_IDS.discard(key)
            _linter._RULE_REGISTRY[cat] = [
                r for r in _linter._RULE_REGISTRY[cat] if r.id != rule_obj.id
            ]


_set_cv2_rules_registered(False)


@pytest.fixture(scope="module", autouse=True)
def _register_cv2_rules_for_module() -> Any:
    _set_cv2_rules_registered(True)
    yield
    _set_cv2_rules_registered(False)


GLOBAL_OK = (
    '<instruction id="Affective_Delivery_and_Voice_Guidelines">\n'
    "Use angle brackets for audio tags such as <calm><empathetic> and "
    "<short pause>.\n"
    "</instruction>\n"
)

DIRECTORS_NOTE = (
    "# AUDIO PROFILE: Maya, a friendly support agent in her 30s.\n"
    "# THE SCENE: Quiet support center.\n"
    "# DIRECTOR'S NOTES: Style: warm. Accent: Australian English.\n"
    "## Transcript:\n"
)

OFFICIAL_SPEAKING_STYLES = (
    (
        "Friendly American woman in her early 30s, warm mid-pitch voice,"
        " relaxed conversational pace"
    ),
    (
        "Upbeat woman in her late 20s, bright mid-range voice, quick,"
        " friendly cadence"
    ),
    (
        "Calm man in his 40s, steady low baritone, measured and unhurried"
        " delivery"
    ),
    (
        "Friendly Southern US woman in her 50s, warm alto, relaxed, patient"
        " drawl"
    ),
    (
        "Young British man, crisp diction, clear tenor voice, brisk yet"
        " polite pace"
    ),
    (
        "Friendly Australian woman in her 30s, clear warm voice, relaxed"
        " conversational pace"
    ),
)


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_app(
    root: pathlib.Path,
    *,
    model: str = "gemini-composite-v2",
    temperature: float | None = None,
    speech: dict[str, Any] | None = None,
    supported: tuple[str, ...] = (),
    global_instruction: str | None = GLOBAL_OK,
    agent_instruction: str = "Help the caller.\n",
    extra_app: dict[str, Any] | None = None,
) -> pathlib.Path:
    model_settings: dict[str, Any] = {"model": model}
    if temperature is not None:
        model_settings["temperature"] = temperature
    app: dict[str, Any] = {
        "displayName": "Cymbal Voice Concierge",
        "rootAgent": "root",
        "modelSettings": model_settings,
        "languageSettings": {
            "defaultLanguageCode": "en-AU",
            "supportedLanguageCodes": list(supported),
            "enableMultilingualSupport": bool(supported),
        },
        "audioProcessingConfig": {
            "synthesizeSpeechConfigs": (
                speech
                if speech is not None
                else {
                    "en-AU": {
                        "voice": "en-AU-Chirp3-HD-Aoede",
                        "speakingRate": 1.0,
                        "instruction": "",
                    }
                }
            )
        },
    }
    if global_instruction is not None:
        app["globalInstruction"] = "global_instruction.txt"
        _write(root / "global_instruction.txt", global_instruction)
    if extra_app:
        app.update(extra_app)
    _write(root / "app.json", json.dumps(app, indent=2) + "\n")
    _write(
        root / "agents" / "root" / "root.json",
        json.dumps(
            {
                "displayName": "root",
                "instruction": "agents/root/instruction.txt",
            },
            indent=2,
        )
        + "\n",
    )
    _write(root / "agents" / "root" / "instruction.txt", agent_instruction)
    return root


def _issues(app_dir: pathlib.Path) -> list[dict[str, Any]]:
    res = aud.VoiceAgentAuditor(app_dir).audit()
    return [item for group in res["issues"].values() for item in group]


def _codes(app_dir: pathlib.Path) -> list[str]:
    return [item["code"] for item in _issues(app_dir)]


# ── Temperature dropped completely (no CV202, no TEMPERATURE_ADVISORY) ──


@pytest.mark.parametrize("temp", [None, 0.0, 0.3, 0.7, 1.0])
def test_temperature_is_never_flagged_or_mutated(
    tmp_path: pathlib.Path, temp: float | None
) -> None:
    app = _make_app(
        tmp_path,
        model="gemini-composite-v1",
        temperature=temp,
    )
    assert not hasattr(aud, "CompositeV2Temperature")
    assert not hasattr(aud, "TARGET_TEMPERATURE")
    assert "CV202" not in aud.RULE_PRIORITY

    codes_before = _codes(app)
    assert "CV201" in codes_before
    assert "CV202" not in codes_before
    assert "TEMPERATURE_ADVISORY" not in codes_before

    aud.VoiceAgentAuditor(app).remediate()
    out = json.loads((app / "app.json").read_text(encoding="utf-8"))
    assert out["modelSettings"]["model"] == "gemini-composite-v2"
    if temp is None:
        assert "temperature" not in out["modelSettings"]
    else:
        assert out["modelSettings"]["temperature"] == temp
    assert aud.VoiceAgentAuditor(app).audit()["overall_status"] == "PASSED"


# ── CV201: Model check & upgrade ─────────────────────────────────────────


def test_cv201_upgrades_app_and_composite_agent_overrides(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(tmp_path, model="gemini-composite-v1")
    agent_json = app / "agents" / "root" / "root.json"
    _write(
        agent_json,
        json.dumps(
            {
                "displayName": "root",
                "instruction": "agents/root/instruction.txt",
                "modelSettings": {"model": "gemini-composite-v1"},
            },
            indent=2,
        )
        + "\n",
    )
    assert _codes(app).count("CV201") == 2

    aud.VoiceAgentAuditor(app).remediate()
    assert _codes(app) == []
    out_agent = json.loads(agent_json.read_text(encoding="utf-8"))
    assert out_agent["modelSettings"]["model"] == "gemini-composite-v2"


def test_cv201_leaves_non_composite_models_for_user(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(tmp_path, model="gemini-2.5-flash")
    assert "CV201" in _codes(app)
    aud.VoiceAgentAuditor(app).remediate()
    out = json.loads((app / "app.json").read_text(encoding="utf-8"))
    assert out["modelSettings"]["model"] == "gemini-2.5-flash"


# ── CV203: Speaking style ceiling (<=25w), Director's Notes & feelings ──


@pytest.mark.parametrize("style", OFFICIAL_SPEAKING_STYLES)
def test_cv203_accepts_official_twelve_word_speaker_profiles(
    tmp_path: pathlib.Path, style: str
) -> None:
    app = _make_app(
        tmp_path,
        speech={
            "en-AU": {
                "voice": "en-AU-Chirp3-HD-Aoede",
                "speakingRate": 1.0,
                "instruction": style,
            }
        },
    )
    assert "CV203" not in _codes(app)
    res = aud.VoiceAgentAuditor(app).remediate()
    assert res["status"] == "NO_CHANGES_NEEDED"
    out = json.loads((app / "app.json").read_text(encoding="utf-8"))
    assert (
        out["audioProcessingConfig"]["synthesizeSpeechConfigs"]["en-AU"][
            "instruction"
        ]
        == style
    )


def test_cv203_flags_and_clears_long_style_above_25_words(
    tmp_path: pathlib.Path,
) -> None:
    long_style = " ".join(["steady"] * 26) + " baritone speaker"
    app = _make_app(
        tmp_path,
        speech={
            "en-AU": {
                "voice": "en-AU-Chirp3-HD-Aoede",
                "instruction": long_style,
            }
        },
    )
    cv203 = [i for i in _issues(app) if i["code"] == "CV203"]
    assert len(cv203) == 1
    assert cv203[0]["priority"] == "P1"

    res = aud.VoiceAgentAuditor(app).remediate()
    assert any("steady" in item for item in res["removed_styles"])
    out = json.loads((app / "app.json").read_text(encoding="utf-8"))
    assert (
        out["audioProcessingConfig"]["synthesizeSpeechConfigs"]["en-AU"][
            "instruction"
        ]
        == ""
    )


def test_cv203_flags_directors_notes_agent_voice_and_feeling_words(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(
        tmp_path,
        supported=("es-US",),
        speech={
            "en-AU": {
                "voice": "en-AU-Chirp3-HD-Aoede",
                "instruction": DIRECTORS_NOTE,
            },
            "es-US": {
                "voice": "es-US-Chirp3-HD-Aoede",
                "instruction": "warm, friendly, natural, professional",
            },
        },
    )
    cv203 = [i for i in _issues(app) if i["code"] == "CV203"]
    assert len(cv203) == 2
    priorities = {i["priority"] for i in cv203}
    assert priorities == {"P0", "P1"}

    aud.VoiceAgentAuditor(app).remediate()
    assert "CV203" not in _codes(app)


def test_minimal_json_edit_preserves_formatting(tmp_path: pathlib.Path) -> None:
    app = _make_app(
        tmp_path,
        model="gemini-composite-v1",
        speech={
            "en-AU": {
                "voice": "en-AU-Chirp3-HD-Aoede",
                "speakingRate": 0.95,
                "instruction": DIRECTORS_NOTE,
            }
        },
    )
    raw = (
        "{\n"
        '    "displayName": "Cymbal Voice Concierge",\n'
        '    "rootAgent": "root",\n'
        '    "globalInstruction": "global_instruction.txt",\n'
        '    "modelSettings": { "model": "gemini-composite-v1" },\n'
        '    "languageSettings": { "defaultLanguageCode": "en-AU" },\n'
        '    "audioProcessingConfig": {\n'
        '        "synthesizeSpeechConfigs": {\n'
        '            "en-AU": {\n'
        '                "voice": "en-AU-Chirp3-HD-Aoede",\n'
        '                "speakingRate": 0.95,\n'
        '                "instruction": "# AUDIO PROFILE: Maya"\n'
        "            }\n"
        "        }\n"
        "    }\n"
        "}\n"
    )
    _write(app / "app.json", raw)
    aud.VoiceAgentAuditor(app).remediate()
    patched = (app / "app.json").read_text(encoding="utf-8")
    assert '    "displayName": "Cymbal Voice Concierge",' in patched
    assert '"model": "gemini-composite-v2"' in patched
    assert '"speakingRate": 0.95' in patched
    assert '"instruction": ""' in patched


# ── CV204 & CV205: Prebuilt Aoede default & Custom cloned voices ─────────


@pytest.mark.parametrize(
    ("locale", "expected_voice"),
    [
        ("en-US", "en-US-Chirp3-HD-Aoede"),
        ("en-AU", "en-AU-Chirp3-HD-Aoede"),
        ("en-GB", "en-GB-Chirp3-HD-Aoede"),
        ("es-MX", "es-US-Chirp3-HD-Aoede"),
        ("fr-CA", "fr-CA-Chirp3-HD-Aoede"),
        ("de-DE", "de-DE-Chirp3-HD-Aoede"),
        ("zh-CN", "cmn-CN-Chirp3-HD-Aoede"),
        ("hi-IN", "hi-IN-Chirp3-HD-Aoede"),
        ("ja-JP", "ja-JP-Chirp3-HD-Aoede"),
        ("ko-KR", "ko-KR-Chirp3-HD-Aoede"),
        ("nl-NL", "nl-NL-Chirp3-HD-Aoede"),
        ("ar-XA", "ar-XA-Chirp3-HD-Aoede"),
    ],
)
def test_default_prebuilt_voice_is_aoede(
    locale: str, expected_voice: str
) -> None:
    assert aud.DEFAULT_PREBUILT_PERSONA == "Aoede"
    assert aud.get_default_voice(locale) == expected_voice
    assert aud.derive_locale_voice(locale, None) == expected_voice


def test_cv204_scaffolds_aoede_and_multilingual_support(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(
        tmp_path,
        supported=("es-US",),
        speech={},
        extra_app={
            "languageSettings": {
                "defaultLanguageCode": "en-AU",
                "supportedLanguageCodes": ["es-US"],
                "enableMultilingualSupport": False,
            }
        },
    )
    assert "CV204" in _codes(app)
    aud.VoiceAgentAuditor(app).remediate()
    out = json.loads((app / "app.json").read_text(encoding="utf-8"))
    assert out["languageSettings"]["enableMultilingualSupport"] is True
    cfgs = out["audioProcessingConfig"]["synthesizeSpeechConfigs"]
    assert cfgs["en-AU"]["voice"] == "en-AU-Chirp3-HD-Aoede"
    assert cfgs["es-US"]["voice"] == "es-US-Chirp3-HD-Aoede"


def test_cv204_inherits_existing_non_aoede_chirp3_persona(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(
        tmp_path,
        supported=("es-US",),
        speech={
            "en-AU": {
                "voice": "en-AU-Chirp3-HD-Puck",
                "speakingRate": 0.95,
                "instruction": "",
            }
        },
    )
    aud.VoiceAgentAuditor(app).remediate()
    cfgs = json.loads((app / "app.json").read_text(encoding="utf-8"))[
        "audioProcessingConfig"
    ]["synthesizeSpeechConfigs"]
    assert cfgs["en-AU"]["voice"] == "en-AU-Chirp3-HD-Puck"
    assert cfgs["es-US"]["voice"] == "es-US-Chirp3-HD-Puck"
    assert cfgs["es-US"]["speakingRate"] == 0.95


def test_cv204_prebuilt_voice_flag_switches_legacy_voices(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(
        tmp_path,
        supported=("es-US",),
        speech={
            "en-AU": {"voice": "en-AU-Neural2-B", "instruction": ""},
            "es-US": {"voice": "es-US-Neural2-A", "instruction": ""},
        },
    )
    assert "CV204" in _codes(app)
    aud.VoiceAgentAuditor(app, prebuilt_voice="Aoede").remediate()
    cfgs = json.loads((app / "app.json").read_text(encoding="utf-8"))[
        "audioProcessingConfig"
    ]["synthesizeSpeechConfigs"]
    assert cfgs["en-AU"]["voice"] == "en-AU-Chirp3-HD-Aoede"
    assert cfgs["es-US"]["voice"] == "es-US-Chirp3-HD-Aoede"
    assert "CV204" not in _codes(app)


def test_cv205_custom_voice_audit_and_remediation(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(
        tmp_path,
        supported=("es-US",),
        extra_app={
            "audioProcessingConfig": {
                "synthesizeSpeechConfigs": {
                    "en-AU": {
                        "voice": "CymbalClonedVoice",
                        "speakingRate": 1.0,
                        "instruction": "Friendly advisor in her 30s",
                        "voiceSampleGcsUri": "gs://legacy-bucket/inline.wav",
                    }
                },
                "customVoiceSamples": [
                    {
                        "name": "CymbalClonedVoice",
                        "voiceSampleGcsUri": "gs://bucket/sample.wav",
                        "consentAudioGcsUri": "",
                        "voiceInstruction": (
                            "<agent_voice>Warm mid-pitch voice</agent_voice>"
                        ),
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
    assert "CV203" in codes
    assert "CV204" in codes
    assert "CV205" in codes

    res = aud.VoiceAgentAuditor(app).remediate()
    out = json.loads((app / "app.json").read_text(encoding="utf-8"))
    samples = out["audioProcessingConfig"]["customVoiceSamples"]
    assert samples[0]["voiceInstruction"] == ""
    assert samples[0]["voiceInstructionMode"] == "NO_INSTRUCTION"
    assert "previewAudioContent" not in samples[0]
    assert samples[1]["voiceInstruction"] == ""
    assert any("agent_voice" in item for item in res["removed_styles"])

    cfgs = out["audioProcessingConfig"]["synthesizeSpeechConfigs"]
    assert cfgs["en-AU"]["instruction"] == ""
    assert cfgs["es-US"]["voice"] == "CymbalClonedVoice"
    assert cfgs["es-US"]["instruction"] == ""


def test_cv205_bind_custom_voice_auto_and_mutual_exclusion(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(
        tmp_path,
        supported=("es-US",),
        extra_app={
            "audioProcessingConfig": {
                "synthesizeSpeechConfigs": {
                    "en-AU": {
                        "voice": "en-AU-Chirp3-HD-Aoede",
                        "instruction": "warm, friendly, natural",
                    },
                    "es-US": {
                        "voice": "es-US-Chirp3-HD-Aoede",
                        "instruction": "",
                    },
                },
                "customVoiceSamples": [
                    {
                        "name": "CymbalAdvisor",
                        "voiceSampleGcsUri": "gs://bucket/advisor.wav",
                        "consentAudioGcsUri": "gs://bucket/consent.wav",
                        "voiceInstruction": "<agent_voice>Bright</agent_voice>",
                        "voiceInstructionMode": "GENERATE_INSTRUCTION",
                        "previewAudioContent": "UklGRiQAAABXQVZF",
                    }
                ],
            }
        },
    )
    res = aud.VoiceAgentAuditor(app, bind_custom_voice="auto").remediate()
    assert res["post_remediation_audit"]["overall_status"] == "PASSED"
    cfgs = json.loads((app / "app.json").read_text(encoding="utf-8"))[
        "audioProcessingConfig"
    ]["synthesizeSpeechConfigs"]
    assert cfgs["en-AU"]["voice"] == "CymbalAdvisor"
    assert cfgs["es-US"]["voice"] == "CymbalAdvisor"

    with pytest.raises(ValueError, match="Cannot specify both"):
        aud.VoiceAgentAuditor(
            app, prebuilt_voice="Aoede", bind_custom_voice="CymbalAdvisor"
        )


# ── CV210, CV211, CV212: Bracket tags, translated tags, remaps & breath ──


@pytest.mark.parametrize(
    "supported_tag",
    [
        "<breath>",
        "<laugh>",
        "<chuckle>",
        "<chuckles>",
        "<gasp>",
        "<throat-clearing>",
        "<exhales>",
        "<sigh>",
        "<short pause>",
        "<long pause>",
        "<calm>",
        "<empathetic>",
        "<warm>",
        "<positive>",
    ],
)
def test_cv212_allows_evaluated_and_ces_supported_tags(
    tmp_path: pathlib.Path, supported_tag: str
) -> None:
    app = _make_app(
        tmp_path,
        agent_instruction=f'Say "{supported_tag} Right away."\n',
    )
    assert "CV212" not in _codes(app)
    assert "CV211" not in _codes(app)


def test_cv211_remaps_tags_and_deduplicates_lists() -> None:
    raw = (
        "Use [reassuring], [curiosity], [professional], [medium pause], "
        "[clears throat], [exhale], [whisper], [slow], and [neutral] here."
    )
    migrated, _ = aud.migrate_tags(raw)
    assert migrated == (
        "Use <empathetic>, <interest>, <clear>, <short pause>, "
        "<throat-clearing>, <exhales>, <whispers>, "
        '<prosody rate="65%">, and here.'
    )

    deduped, _ = aud.migrate_tags(
        "Open with <empathetic>, <warm>, <reassuring>."
    )
    assert deduped == "Open with <empathetic>, <warm>."

    quoted_list, _ = aud.migrate_tags(
        """Use '[prosody rate="..."]' with ('[sigh]', '[neutral]', '[positive]')."""
    )
    assert quoted_list == (
        """Use '<prosody rate="...">' with ('<sigh>', '<positive>')."""
    )


def test_cv210_migrates_translated_non_english_tags(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(
        tmp_path,
        global_instruction=(
            GLOBAL_OK
            + "Keep tags in English (<short pause>, never <pausa corta>).\n"
        ),
        agent_instruction=(
            'Say "<empático> Lo siento, <pausa corta> déjeme revisar."\n'
            'In Mandarin, say "<短暂停顿> 稍等一下。"\n'
            'In Hindi, say "[छोटा विराम] जी, एक सेकंड रुकिए।"\n'
        ),
    )
    cv210 = [i for i in _issues(app) if i["code"] == "CV210"]
    assert len(cv210) == 4
    assert not any(i["file"] == "global_instruction.txt" for i in cv210)

    aud.VoiceAgentAuditor(app).remediate()
    migrated = (app / "agents" / "root" / "instruction.txt").read_text(
        encoding="utf-8"
    )
    assert migrated == (
        'Say "<empathetic> Lo siento, <short pause> déjeme revisar."\n'
        'In Mandarin, say "<short pause> 稍等一下。"\n'
        'In Hindi, say "<short pause> जी, एक सेकंड रुकिए।"\n'
    )
    assert "CV210" not in _codes(app)


def test_cv212_reports_unevaluated_tags_and_bracket_typos(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(
        tmp_path,
        global_instruction=(
            GLOBAL_OK
            + 'Say "<excited> Great news." or open with [clam].\n'
            + "Read [BOOKING_ID] and [warm](https://example.com) and x[0].\n"
        ),
    )
    cv212 = [i for i in _issues(app) if i["code"] == "CV212"]
    assert len(cv212) == 2
    messages = " | ".join(i["message"] for i in cv212)
    assert "<excited>" in messages
    assert "[clam]" in messages


# ── CV213 & CV214: Closing tags, prosody stacks & mid-sentence placement ─


def test_cv213_rewrites_closing_prosody_and_removes_standalone_closing() -> (
    None
):
    text = (
        '"[prosody rate="65%"] I understand. </prosody> Let\'s try again."\n'
        '"<warm> All done.</warm>"\n'
    )
    migrated, _ = aud.migrate_tags(text)
    assert migrated == (
        '"<prosody rate="65%"> I understand. <prosody rate="100%"> '
        "Let's try again.\"\n"
        '"<warm> All done."\n'
    )


def test_cv213_escapes_quotes_inside_json_strings() -> None:
    raw = '{"example": "[prosody rate=70%] Slow. </prosody> Normal."}'
    migrated, _ = aud.migrate_tags(raw, json_escaped=True)
    parsed = json.loads(migrated)
    assert parsed["example"] == (
        '<prosody rate="70%"> Slow. <prosody rate="100%"> Normal.'
    )


def test_cv214_reorders_prosody_stack_and_relocates_utterance_tags() -> None:
    stack, _ = aud.migrate_tags('"<empathetic><prosody rate="70%"> Sorry."')
    assert stack == '"<prosody rate="70%"><empathetic> Sorry."'

    relocated, _ = aud.migrate_tags(
        '"<calm> I checked it now <short pause> and <empathetic> all set."'
    )
    assert relocated == (
        '"<calm><empathetic> I checked it now, <short pause> and all set."'
    )

    cjk = '"<calm><empathetic> 很抱歉。<positive> 好消息是已经改签了。"'
    assert aud.migrate_tags(cjk)[0] == cjk


# ── CV215: Prompt & locale hygiene checks ────────────────────────────────


def test_cv215_flags_ending_sigh_collisions_accent_and_devanagari(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(
        tmp_path,
        supported=("es-US",),
        speech={
            "en-AU": {
                "voice": "en-AU-Chirp3-HD-Aoede",
                "instruction": "Australian woman in her 30s, warm voice",
            },
            "es-US": {
                "voice": "es-US-Chirp3-HD-Aoede",
                "instruction": "Australian woman in her 30s, warm voice",
            },
        },
        agent_instruction=(
            "Wrap tags in square brackets.\n"
            'On transfer, say "<sigh> Putting you through." and call '
            "{@TOOL: transfer_to_human}.\n"
            "Wait... <short pause> let me check.\n"
            'Say "<exhales> SOLID green means online."\n'
            'Hindi: "नमस्ते! मैं आपके WiFi राउटर में मदद कर सकती हूँ।"\n'
        ),
    )
    all_issues = _issues(app)
    cv203 = [i for i in all_issues if i["code"] == "CV203"]
    assert any("es-US" in i["message"] for i in cv203)

    cv215 = [i for i in all_issues if i["code"] == "CV215"]
    joined = "\n".join(i["message"] for i in cv215)
    assert "square brackets" in joined.lower() or "old" in joined.lower()
    assert "session-ending or transfer" in joined
    assert "ellipsis" in joined.lower()
    assert "ALL-CAPS" in joined
    assert "Devanagari" in joined


# ── CV216 & CV217: Global guidelines & conditional text normalization ────


def test_cv216_flags_missing_global_guidelines(tmp_path: pathlib.Path) -> None:
    app = _make_app(tmp_path, global_instruction="Be helpful.\n")
    assert "CV216" in _codes(app)


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        ("en", True),
        ("en-US", True),
        ("en-AU", True),
        ("en-GB", True),
        ("fr", True),
        ("fr-CA", True),
        ("es", True),
        ("es-US", True),
        ("es-ES", False),
        ("es-MX", False),
        ("fr-FR", False),
        ("de-DE", False),
        ("cmn-CN", False),
        ("hi-IN", False),
        ("ja-JP", False),
        ("ko-KR", False),
    ],
)
def test_platform_tn_locale_matrix(locale: str, expected: bool) -> None:
    assert aud.is_platform_tn_supported_locale(locale) is expected


def test_cv217_silent_for_supported_locales_without_tn_block(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(
        tmp_path,
        supported=("es-US", "fr-CA"),
        speech={
            "en-AU": {"voice": "en-AU-Chirp3-HD-Aoede", "instruction": ""},
            "es-US": {"voice": "es-US-Chirp3-HD-Aoede", "instruction": ""},
            "fr-CA": {"voice": "fr-CA-Chirp3-HD-Aoede", "instruction": ""},
        },
    )
    assert "CV217" not in _codes(app)
    assert aud.VoiceAgentAuditor(app).audit()["overall_status"] == "PASSED"


def test_cv217_warns_p1_when_unsupported_locale_lacks_tn_block(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(
        tmp_path,
        supported=("es-MX", "hi-IN"),
        speech={
            "en-AU": {"voice": "en-AU-Chirp3-HD-Aoede", "instruction": ""},
            "es-MX": {"voice": "es-US-Chirp3-HD-Aoede", "instruction": ""},
            "hi-IN": {"voice": "hi-IN-Chirp3-HD-Aoede", "instruction": ""},
        },
    )
    cv217 = [i for i in _issues(app) if i["code"] == "CV217"]
    assert len(cv217) == 1
    assert cv217[0]["priority"] == "P1"
    assert "es-MX" in cv217[0]["message"]
    assert "hi-IN" in cv217[0]["message"]


def test_cv217_errors_p0_on_unscoped_tn_and_passes_when_scoped(
    tmp_path: pathlib.Path,
) -> None:
    unscoped_dir = tmp_path / "unscoped"
    _make_app(
        unscoped_dir,
        global_instruction=(
            GLOBAL_OK
            + '<instruction id="text normalization for speech">\n'
            + "Spell out $89 as eighty-nine dollars.\n"
            + "</instruction>\n"
        ),
    )
    cv217 = [i for i in _issues(unscoped_dir) if i["code"] == "CV217"]
    assert len(cv217) == 1
    assert cv217[0]["priority"] == "P0"

    scoped_dir = tmp_path / "scoped"
    _make_app(
        scoped_dir,
        supported=("hi-IN",),
        speech={
            "en-AU": {"voice": "en-AU-Chirp3-HD-Aoede", "instruction": ""},
            "hi-IN": {"voice": "hi-IN-Chirp3-HD-Aoede", "instruction": ""},
        },
        global_instruction=(
            GLOBAL_OK
            + '<instruction id="text normalization for speech">\n'
            + "Scope: applies ONLY to words spoken aloud, never to tool calls"
            + " or tool arguments.\n"
            + "</instruction>\n"
        ),
    )
    assert "CV217" not in _codes(scoped_dir)


# ── CV218 & Hand-offs: Callbacks & cross-app transfers ───────────────────


def test_cv218_callback_inline_tag_coverage_and_handoffs(
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
        'INLINE_TAG = re.compile(r"(<(?:breath|empathetic)>)", '
        "re.IGNORECASE)\n",
    )
    _write(
        app / "tools" / "handoff" / "handoff.json",
        json.dumps(
            {
                "transferToNga": {
                    "app": "projects/p/locations/us/apps/" + "0" * 36
                }
            }
        ),
    )

    res = aud.VoiceAgentAuditor(app).audit()
    cv218 = [i for i in _issues(app) if i["code"] == "CV218"]
    assert len(cv218) == 1
    assert "<calm>" in cv218[0]["message"]
    assert len(res["handoffs"]) >= 1
    assert aud.HANDOFF_TARGETS["transferToNga"] == "another CX Agent Studio app"

    _write(
        app / "agents" / "root" / "instruction.txt",
        "Never write banned tags like <warm> here.\n",
    )
    _write(
        cb,
        "import re\n"
        'INLINE_TAG = re.compile(r"(<(?:calm|empathetic|short pause)>)", '
        "re.IGNORECASE)\n",
    )
    assert "CV218" not in _codes(app)


# ── CLI flags: --dry-run, --backup, --output, --lint-report ──────────────


def test_dry_run_backup_and_cli_outputs(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _make_app(tmp_path, agent_instruction='Say "[sigh] Okay."\n')
    inst = app / "agents" / "root" / "instruction.txt"

    dry = aud.VoiceAgentAuditor(app).remediate(dry_run=True)
    assert dry["status"] == "DRY_RUN"
    assert inst.read_text(encoding="utf-8") == 'Say "[sigh] Okay."\n'

    aud.VoiceAgentAuditor(app, backup=True).remediate()
    assert inst.read_text(encoding="utf-8") == 'Say "<sigh> Okay."\n'
    assert (
        pathlib.Path(f"{inst}.bak").read_text(encoding="utf-8")
        == 'Say "[sigh] Okay."\n'
    )
    pathlib.Path(f"{inst}.bak").unlink()

    out_file = tmp_path / "report.md"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit_agent.py",
            "--workspace",
            str(app),
            "--report",
            "--output",
            str(out_file),
        ],
    )
    with pytest.raises(SystemExit) as exc:
        aud.main()
    assert exc.value.code == 0
    assert "No issues detected" in out_file.read_text(encoding="utf-8")

    lint_json = tmp_path / "lint.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit_agent.py",
            "--workspace",
            str(app),
            "--lint-report",
            "--json",
            "--output",
            str(lint_json),
        ],
    )
    with pytest.raises(SystemExit) as exc_lint:
        aud.main()
    assert exc_lint.value.code == 0
    assert json.loads(lint_json.read_text(encoding="utf-8")) == []
