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

from cxas_scrapi.utils.tracing import audio_analysis as aa

SAMPLE_FILES = [
    "gs://b/p/conv-1/METADATA.json",
    "gs://b/p/conv-1/full-session.wav",
    "gs://b/p/conv-1/agent-turn-1.wav",
    "gs://b/p/conv-1/agent-turn-2.wav",
    "gs://b/p/conv-1/user-turn-1.wav",
]


def test_registry_contains_five_named_analyses():
    expected = {
        "agent_voice_consistency",
        "no_long_pauses",
        "agent_having_trouble",
        "agent_looping",
        "agent_cutoff",
    }
    assert set(aa.ANALYSIS_REGISTRY.keys()) == expected


def test_analysis_type_enum_values_match_registry():
    assert {t.value for t in aa.AnalysisType} == set(
        aa.ANALYSIS_REGISTRY.keys()
    )


def test_voice_consistency_filters_to_agent_turns_only():
    a = aa.ANALYSIS_REGISTRY["agent_voice_consistency"]
    out = a.filter_files(SAMPLE_FILES)
    assert all("agent-turn" in f for f in out)
    assert len(out) == 2


def test_no_long_pauses_filters_to_full_session_only():
    a = aa.ANALYSIS_REGISTRY["no_long_pauses"]
    out = a.filter_files(SAMPLE_FILES)
    assert out == [f for f in SAMPLE_FILES if "full-session" in f]


def test_agent_having_trouble_filters_to_agent_turns():
    a = aa.ANALYSIS_REGISTRY["agent_having_trouble"]
    assert all("agent-turn" in f for f in a.filter_files(SAMPLE_FILES))


def test_agent_looping_filters_to_agent_turns():
    a = aa.ANALYSIS_REGISTRY["agent_looping"]
    assert all("agent-turn" in f for f in a.filter_files(SAMPLE_FILES))


def test_agent_cutoff_filters_to_agent_turns():
    a = aa.ANALYSIS_REGISTRY["agent_cutoff"]
    assert all("agent-turn" in f for f in a.filter_files(SAMPLE_FILES))


def test_each_analysis_has_pass_fail_in_prompt():
    """Every prompt must direct Gemini to report PASS or FAIL."""
    for name, analysis in aa.ANALYSIS_REGISTRY.items():
        text = analysis.prompt.upper()
        assert "PASS" in text, f"{name} missing PASS instruction"
        assert "FAIL" in text, f"{name} missing FAIL instruction"


def test_filter_files_handles_empty_list():
    for analysis in aa.ANALYSIS_REGISTRY.values():
        assert analysis.filter_files([]) == []


def test_name_property_returns_enum_member():
    for key, analysis in aa.ANALYSIS_REGISTRY.items():
        assert isinstance(analysis.name, aa.AnalysisType)
        assert str(analysis.name) == key
