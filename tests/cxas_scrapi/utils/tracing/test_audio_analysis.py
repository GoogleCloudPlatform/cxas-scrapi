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


import json
import typing

import yaml

from cxas_scrapi.utils import scorecard_template_manager
from cxas_scrapi.utils.tracing import audio_analysis as aa

SAMPLE_FILES = [
    "gs://b/p/conv-1/METADATA.json",
    "gs://b/p/conv-1/full-session.wav",
    "gs://b/p/conv-1/agent-turn-1.wav",
    "gs://b/p/conv-1/agent-turn-2.wav",
    "gs://b/p/conv-1/user-turn-1.wav",
]


def test_registry_contains_five_named_analyses() -> None:
    expected = {
        "agent_voice_consistency",
        "no_long_pauses",
        "agent_having_trouble",
        "agent_looping",
        "agent_cutoff",
    }
    assert set(aa.ANALYSIS_REGISTRY.keys()) == expected


def test_analysis_type_enum_values_match_registry() -> None:
    assert {t.value for t in aa.AnalysisType} == set(
        aa.ANALYSIS_REGISTRY.keys()
    )


def test_voice_consistency_filters_to_agent_turns_only() -> None:
    a = aa.ANALYSIS_REGISTRY["agent_voice_consistency"]
    out = a.filter_files(SAMPLE_FILES)
    assert all("agent-turn" in f for f in out)
    assert len(out) == 2


def test_no_long_pauses_filters_to_full_session_only() -> None:
    a = aa.ANALYSIS_REGISTRY["no_long_pauses"]
    out = a.filter_files(SAMPLE_FILES)
    assert out == [f for f in SAMPLE_FILES if "full-session" in f]


def test_agent_having_trouble_filters_to_agent_turns() -> None:
    a = aa.ANALYSIS_REGISTRY["agent_having_trouble"]
    assert all("agent-turn" in f for f in a.filter_files(SAMPLE_FILES))


def test_agent_looping_filters_to_agent_turns() -> None:
    a = aa.ANALYSIS_REGISTRY["agent_looping"]
    assert all("agent-turn" in f for f in a.filter_files(SAMPLE_FILES))


def test_agent_cutoff_filters_to_agent_turns() -> None:
    a = aa.ANALYSIS_REGISTRY["agent_cutoff"]
    assert all("agent-turn" in f for f in a.filter_files(SAMPLE_FILES))


def test_each_analysis_has_pass_fail_in_prompt() -> None:
    """Every prompt must direct Gemini to report PASS or FAIL."""
    for name, analysis in aa.ANALYSIS_REGISTRY.items():
        text = analysis.prompt.upper()
        assert "PASS" in text, f"{name} missing PASS instruction"
        assert "FAIL" in text, f"{name} missing FAIL instruction"


def test_filter_files_handles_empty_list() -> None:
    for analysis in aa.ANALYSIS_REGISTRY.values():
        assert analysis.filter_files([]) == []


def test_name_property_returns_enum_member() -> None:
    for key, analysis in aa.ANALYSIS_REGISTRY.items():
        assert isinstance(analysis.name, aa.AnalysisType)
        assert str(analysis.name) == key


def test_default_json_is_valid_scorecard_template() -> None:
    scorecard, questions = scorecard_template_manager.load_scorecard_template(
        aa.DEFAULT_QUESTIONS_PATH
    )
    assert scorecard.get("displayName")
    assert len(questions) == 5
    abbrevs = [q["abbreviation"] for q in questions]
    assert abbrevs == [
        "agent_voice_consistency",
        "no_long_pauses",
        "agent_having_trouble",
        "agent_looping",
        "agent_cutoff",
    ]


def test_custom_question_from_scorecard_dict() -> None:
    custom_q = {
        "abbreviation": "background_music_check",
        "questionBody": "Determine if loud background music is audible during user turns.",
        "answerInstructions": "Listen carefully to all user turn recordings.",
        "answerChoices": [
            {
                "key": "PASS",
                "strValue": "PASS",
                "body": "no loud background music is audible",
                "score": 1.0,
            },
            {
                "key": "FAIL",
                "strValue": "FAIL",
                "body": "loud background music is audible",
                "score": 0.0,
            },
        ],
        "fileFilter": "user-turn",
    }
    analysis = aa.AudioAnalysis.from_question(custom_q)
    assert analysis.name == "background_music_check"
    assert not isinstance(analysis.name, aa.AnalysisType)
    assert "Determine if loud background music is audible" in analysis.prompt
    assert "Listen carefully to all user turn recordings." in analysis.prompt
    assert (
        "Report PASS if no loud background music is audible" in analysis.prompt
    )
    assert "Report FAIL if loud background music is audible" in analysis.prompt
    assert analysis.filter_files(SAMPLE_FILES) == [
        "gs://b/p/conv-1/user-turn-1.wav"
    ]


def test_custom_question_default_choices_and_wildcard_filter() -> None:
    custom_q = {
        "name": "projects/p/locations/l/qaScorecards/s/revisions/r/qaQuestions/politeness_check",
        "questionBody": "Was the agent polite throughout the call?",
        "fileFilter": "*",
    }
    analysis = aa.ScorecardAudioAnalysis.from_question(custom_q)
    assert analysis.name == "politeness_check"
    assert "PASS" in analysis.prompt
    assert "FAIL" in analysis.prompt
    wavs = analysis.filter_files(SAMPLE_FILES)
    assert len(wavs) == 4
    assert all(f.endswith(".wav") for f in wavs)


def test_custom_question_list_file_filter() -> None:
    custom_q = {
        "abbreviation": "both_turns",
        "questionBody": "Check turn boundary overlap.",
        "fileFilter": ["agent-turn", "user-turn"],
    }
    analysis = aa.ScorecardAudioAnalysis.from_question(custom_q)
    matched = analysis.filter_files(SAMPLE_FILES)
    assert matched == [
        "gs://b/p/conv-1/agent-turn-1.wav",
        "gs://b/p/conv-1/agent-turn-2.wav",
        "gs://b/p/conv-1/user-turn-1.wav",
    ]


def test_load_audio_questions_from_custom_json_and_yaml(
    tmp_path: typing.Any,
) -> None:
    json_file = tmp_path / "custom_scorecard.json"
    payload = {
        "qaScorecard": {"displayName": "Custom Audio Scorecard"},
        "qaQuestions": [
            {
                "abbreviation": "custom_q1",
                "questionBody": "Did the agent speak clearly?",
                "fileFilter": "agent-turn",
                "order": 2,
            },
            {
                "abbreviation": "custom_q2",
                "questionBody": "Was there background static on the session?",
                "fileFilter": "full-session",
                "order": 1,
            },
        ],
    }
    json_file.write_text(json.dumps(payload))
    loaded = aa.load_audio_questions(str(json_file))
    assert list(loaded.keys()) == ["custom_q2", "custom_q1"]
    assert loaded["custom_q2"].filter_files(SAMPLE_FILES) == [
        "gs://b/p/conv-1/full-session.wav"
    ]

    yaml_file = tmp_path / "bare_list.yaml"
    yaml_file.write_text(
        yaml.safe_dump(
            [
                {
                    "abbreviation": "yaml_q",
                    "questionBody": "Is the greeting warm?",
                }
            ]
        )
    )
    loaded_yaml = aa.load_audio_questions(str(yaml_file))
    assert "yaml_q" in loaded_yaml
