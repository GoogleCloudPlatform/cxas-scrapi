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

from unittest.mock import MagicMock, patch

import pytest

from cxas_scrapi.utils.tracing.audio_transcription import (
    AudioTranscriber,
    calculate_wer,
    contains_non_english,
    normalize_text,
)


def test_normalize_text_basic() -> None:
    assert normalize_text("Hello, World!") == ["hello", "world"]
    assert normalize_text("Don't count your chickens.") == [
        "don't",
        "count",
        "your",
        "chickens",
    ]
    assert normalize_text("") == []


def test_normalize_text_multilingual() -> None:
    assert normalize_text("Café con leche") == ["café", "con", "leche"]
    assert normalize_text("Ciao bella, ché mi sento!") == [
        "ciao",
        "bella",
        "ché",
        "mi",
        "sento",
    ]
    assert normalize_text("你好 世界") == ["你好", "世界"]


def test_contains_non_english() -> None:
    assert not contains_non_english("Hello world")
    assert not contains_non_english("No.")
    assert not contains_non_english("12345!@#$%^&*()")
    assert not contains_non_english("")

    # Non-ASCII / accented / multilingual / emojis
    assert contains_non_english("Café")
    assert contains_non_english("ché mi sento")
    assert contains_non_english("👂")
    assert contains_non_english("¿Cómo estás?")
    assert contains_non_english("你好")
    assert contains_non_english("Привет")


def test_calculate_wer_exact_match() -> None:
    res = calculate_wer("Hello world", "hello world")
    assert res["wer"] == 0.0
    assert res["substitutions"] == 0
    assert res["deletions"] == 0
    assert res["insertions"] == 0
    assert res["hits"] == 2
    assert res["reference_words"] == 2
    assert res["hypothesis_words"] == 2


def test_calculate_wer_empty_strings() -> None:
    res_both_empty = calculate_wer("", "")
    assert res_both_empty["wer"] == 0.0
    assert res_both_empty["reference_words"] == 0
    assert res_both_empty["hypothesis_words"] == 0

    res_ref_empty = calculate_wer("", "some spoken text")
    assert res_ref_empty["wer"] == 1.0
    assert res_ref_empty["insertions"] == 3

    res_hyp_empty = calculate_wer("some spoken text", "")
    assert res_hyp_empty["wer"] == 1.0
    assert res_hyp_empty["deletions"] == 3


def test_calculate_wer_substitutions() -> None:
    res = calculate_wer("I want pizza", "I want pasta")
    assert res["wer"] == pytest.approx(1 / 3, rel=1e-3)
    assert res["substitutions"] == 1
    assert res["deletions"] == 0
    assert res["insertions"] == 0
    assert res["hits"] == 2


def test_calculate_wer_insertions_and_deletions() -> None:
    res_ins = calculate_wer("hello world", "hello beautiful world")
    assert res_ins["wer"] == 0.5
    assert res_ins["insertions"] == 1
    assert res_ins["hits"] == 2

    res_del = calculate_wer("hello beautiful world", "hello world")
    assert res_del["wer"] == pytest.approx(1 / 3, rel=1e-3)
    assert res_del["deletions"] == 1
    assert res_del["hits"] == 2


def test_calculate_wer_without_normalization() -> None:
    res = calculate_wer("Hello World", "hello world", normalize=False)
    # Case mismatch treated as substitution
    assert res["wer"] == 1.0
    assert res["substitutions"] == 2


@patch("cxas_scrapi.utils.tracing.audio_transcription.GeminiGenerate")
def test_audio_transcriber_from_gcs_uri(mock_gemini_cls: MagicMock) -> None:
    mock_gemini = MagicMock()
    mock_gemini.generate_with_parts.return_value = "Yes I would like help"
    mock_gemini_cls.return_value = mock_gemini

    transcriber = AudioTranscriber(
        project_id="test-proj",
        model_name="gemini-2.5-flash",
    )

    text = transcriber.transcribe("gs://my-bucket/audio/user-turn-1.wav")
    assert text == "Yes I would like help"
    mock_gemini.generate_with_parts.assert_called_once()


@patch("cxas_scrapi.utils.tracing.audio_transcription.GeminiGenerate")
def test_audio_transcriber_from_bytes(mock_gemini_cls: MagicMock) -> None:
    mock_gemini = MagicMock()
    mock_gemini.generate_with_parts.return_value = "```\nClean speech text\n```"
    mock_gemini_cls.return_value = mock_gemini

    transcriber = AudioTranscriber(project_id="test-proj")
    text = transcriber.transcribe(b"RIFF....WAVEfmt ")
    assert text == "Clean speech text"


@patch("cxas_scrapi.utils.tracing.audio_transcription.GeminiGenerate")
def test_audio_transcriber_from_local_file(
    mock_gemini_cls: MagicMock, tmp_path: pytest.TempPathFactory
) -> None:
    mock_gemini = MagicMock()
    mock_gemini.generate_with_parts.return_value = '"Quoted transcription"'
    mock_gemini_cls.return_value = mock_gemini

    audio_file = tmp_path / "test.wav"  # type: ignore[operator]
    audio_file.write_bytes(b"dummy audio data")

    transcriber = AudioTranscriber(project_id="test-proj")
    text = transcriber.transcribe(str(audio_file))
    assert text == "Quoted transcription"


@patch("cxas_scrapi.utils.tracing.audio_transcription.GeminiGenerate")
def test_audio_transcriber_evaluate_turn(mock_gemini_cls: MagicMock) -> None:
    mock_gemini = MagicMock()
    mock_gemini.generate_with_parts.return_value = "I want a flight to Chicago"
    mock_gemini_cls.return_value = mock_gemini

    transcriber = AudioTranscriber(project_id="test-proj")
    eval_res = transcriber.evaluate_turn(
        reference_transcript="I want a flight to Boston",
        audio_source="gs://bucket/turn-2.wav",
        turn_index=2,
        conversation_id="conv-123",
    )

    assert eval_res["conversation_id"] == "conv-123"
    assert eval_res["turn_index"] == 2
    assert eval_res["ces_transcript"] == "I want a flight to Boston"
    assert eval_res["gemini_transcript"] == "I want a flight to Chicago"
    assert eval_res["substitutions"] == 1
    assert eval_res["wer"] == pytest.approx(1 / 6, rel=1e-3)
    assert not eval_res["contains_non_english"]
