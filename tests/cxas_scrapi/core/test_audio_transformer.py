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

import io
import typing
import wave
from unittest.mock import MagicMock, patch

import pytest

from cxas_scrapi.core.audio_transformer import (
    BACKGROUND_NOISE_LEVELS,
    BUILTIN_BACKGROUND_NOISE_PRESETS,
    AudioTransformer,
    load_background_noise_segment,
    parse_noise_snr,
    resolve_noise_config,
    snr_to_noise_level,
    synthesize_tts_competing_speech,
)


class TestAudioTransformer:
    def setup_method(self) -> None:
        AudioTransformer._client = None
        self.transformer = AudioTransformer()

    @patch("cxas_scrapi.core.audio_transformer.texttospeech")
    def test_text_to_speech_bytes_success(self, mock_tts: typing.Any) -> None:
        # Mock dependencies
        mock_client = MagicMock()
        mock_tts.TextToSpeechClient.return_value = mock_client

        # Create a valid WAV file in memory to return as mock response
        with io.BytesIO() as wav_io:
            with wave.open(wav_io, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(b"audio_data")
            wav_bytes = wav_io.getvalue()

        # Configure mock response
        mock_response = MagicMock()
        mock_response.audio_content = wav_bytes
        mock_client.synthesize_speech.return_value = mock_response

        # Execute
        result = self.transformer.text_to_speech_bytes(
            text="hello", credentials=MagicMock(), project_id="test-project"
        )

        # Verify
        assert result["text"] == "hello"
        assert result["audio_bytes"] == b"audio_data"
        mock_client.synthesize_speech.assert_called_once()
        mock_tts.VoiceSelectionParams.assert_called_once_with(
            language_code="en-US",
            name="Aoede",
            model_name="gemini-3.8-flash-lite-tts",
        )

    @patch("cxas_scrapi.core.audio_transformer.texttospeech")
    def test_text_to_speech_bytes_api_error(self, mock_tts: typing.Any) -> None:
        # Mock dependencies
        mock_client = MagicMock()
        mock_tts.TextToSpeechClient.return_value = mock_client

        # Configure mock to raise exception
        mock_client.synthesize_speech.side_effect = Exception("API Error")

        # Execute
        result = self.transformer.text_to_speech_bytes(
            text="hello", credentials=MagicMock(), project_id="test-project"
        )

        # Verify
        assert result["text"] == "hello"
        assert result["audio_bytes"] is None

    @patch("cxas_scrapi.core.audio_transformer.texttospeech")
    def test_text_to_speech_bytes_invalid_wav(
        self, mock_tts: typing.Any
    ) -> None:
        # Mock dependencies
        mock_client = MagicMock()
        mock_tts.TextToSpeechClient.return_value = mock_client

        # Return invalid bytes usually wouldn't pass wave.open
        mock_response = MagicMock()
        mock_response.audio_content = b"invalid_wav_data"
        mock_client.synthesize_speech.return_value = mock_response

        # Execute
        result = self.transformer.text_to_speech_bytes(
            text="hello", credentials=MagicMock(), project_id="test-project"
        )

        # Verify failure handled gracefully
        assert result["text"] == "hello"
        assert result["audio_bytes"] is None

    @patch("cxas_scrapi.core.audio_transformer.texttospeech")
    def test_text_to_speech_bytes_custom_voice(
        self, mock_tts: typing.Any
    ) -> None:
        # Mock dependencies
        mock_client = MagicMock()
        mock_tts.TextToSpeechClient.return_value = mock_client

        # Create a valid WAV file in memory to return as mock response
        with io.BytesIO() as wav_io:
            with wave.open(wav_io, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(b"audio_data")
            wav_bytes = wav_io.getvalue()

        # Configure mock response
        mock_response = MagicMock()
        mock_response.audio_content = wav_bytes
        mock_client.synthesize_speech.return_value = mock_response

        # Execute with custom voice_config
        custom_voice = {
            "language_code": "fr-FR",
            "voice_name": "fr-FR-Standard-G",
            "model_name": "custom-tts-model",
        }
        result = self.transformer.text_to_speech_bytes(
            text="Bonjour",
            credentials=MagicMock(),
            project_id="test-project",
            voice_config=custom_voice,
        )

        # Verify
        assert result["text"] == "Bonjour"
        assert result["audio_bytes"] == b"audio_data"
        mock_client.synthesize_speech.assert_called_once()

        # Verify custom params were used
        mock_tts.VoiceSelectionParams.assert_called_once_with(
            language_code="fr-FR",
            name="fr-FR-Standard-G",
            model_name="custom-tts-model",
        )

    @patch("cxas_scrapi.core.audio_transformer.texttospeech")
    def test_text_to_speech_bytes_custom_language_code_default_voice_and_model(
        self, mock_tts: typing.Any
    ) -> None:
        mock_client = MagicMock()
        mock_tts.TextToSpeechClient.return_value = mock_client

        with io.BytesIO() as wav_io:
            with wave.open(wav_io, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(b"audio_data")
            wav_bytes = wav_io.getvalue()

        mock_response = MagicMock()
        mock_response.audio_content = wav_bytes
        mock_client.synthesize_speech.return_value = mock_response

        result = self.transformer.text_to_speech_bytes(
            text="Hola",
            credentials=MagicMock(),
            project_id="test-project",
            voice_config={"language_code": "es-US"},
        )

        assert result["text"] == "Hola"
        assert result["audio_bytes"] == b"audio_data"
        mock_tts.VoiceSelectionParams.assert_called_once_with(
            language_code="es-US",
            name="Aoede",
            model_name="gemini-3.8-flash-lite-tts",
        )

    @patch("cxas_scrapi.core.audio_transformer.texttospeech")
    def test_text_to_speech_bytes_model_name_none(
        self, mock_tts: typing.Any
    ) -> None:
        mock_client = MagicMock()
        mock_tts.TextToSpeechClient.return_value = mock_client

        with io.BytesIO() as wav_io:
            with wave.open(wav_io, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(b"audio_data")
            wav_bytes = wav_io.getvalue()

        mock_response = MagicMock()
        mock_response.audio_content = wav_bytes
        mock_client.synthesize_speech.return_value = mock_response

        result = self.transformer.text_to_speech_bytes(
            text="Hello",
            credentials=MagicMock(),
            project_id="test-project",
            voice_config={"model_name": None},
        )

        assert result["text"] == "Hello"
        assert result["audio_bytes"] == b"audio_data"
        mock_tts.VoiceSelectionParams.assert_called_once_with(
            language_code="en-US", name="Aoede"
        )

    @patch("cxas_scrapi.core.audio_transformer.texttospeech")
    def test_text_to_speech_bytes_with_prompt(
        self, mock_tts: typing.Any
    ) -> None:
        mock_client = MagicMock()
        mock_tts.TextToSpeechClient.return_value = mock_client

        with io.BytesIO() as wav_io:
            with wave.open(wav_io, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(b"audio_data")
            wav_bytes = wav_io.getvalue()

        mock_response = MagicMock()
        mock_response.audio_content = wav_bytes
        mock_client.synthesize_speech.return_value = mock_response

        result = self.transformer.text_to_speech_bytes(
            text="Hello world",
            credentials=MagicMock(),
            project_id="test-project",
            voice_config={"prompt": " Speak cheerfully "},
        )

        assert result["text"] == "Hello world"
        assert result["audio_bytes"] == b"audio_data"
        mock_tts.SynthesisInput.assert_called_once_with(
            text="Hello world", prompt="Speak cheerfully"
        )

    @patch("cxas_scrapi.core.audio_transformer.texttospeech")
    def test_text_to_speech_bytes_fallback_on_unsupported_model(
        self, mock_tts: typing.Any
    ) -> None:
        mock_client = MagicMock()
        mock_tts.TextToSpeechClient.return_value = mock_client

        with io.BytesIO() as wav_io:
            with wave.open(wav_io, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(b"audio_data")
            wav_bytes = wav_io.getvalue()

        mock_response = MagicMock()
        mock_response.audio_content = wav_bytes

        mock_client.synthesize_speech.side_effect = [
            Exception("Model gemini-3.8-flash-lite-tts is not supported"),
            mock_response,
        ]

        result = self.transformer.text_to_speech_bytes(
            text="Testing fallback",
            credentials=MagicMock(),
            project_id="test-project",
        )

        assert result["text"] == "Testing fallback"
        assert result["audio_bytes"] == b"audio_data"
        assert mock_client.synthesize_speech.call_count == 2
        mock_tts.VoiceSelectionParams.assert_any_call(
            language_code="en-US",
            name="Aoede",
            model_name="gemini-3.8-flash-lite-tts",
        )
        mock_tts.VoiceSelectionParams.assert_any_call(
            language_code="en-US",
            name="Aoede",
            model_name="gemini-2.5-flash-tts",
        )

    @patch("cxas_scrapi.core.audio_transformer.AudioSegment")
    @patch("cxas_scrapi.core.audio_transformer.texttospeech")
    def test_text_to_speech_bytes_with_burst_noise_success(
        self, mock_tts: typing.Any, mock_audio_segment: typing.Any
    ) -> None:
        # Setup TTS mock
        mock_client = MagicMock()
        mock_tts.TextToSpeechClient.return_value = mock_client

        # Create raw WAV mock
        with io.BytesIO() as wav_io:
            with wave.open(wav_io, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(b"audio_data")
            wav_bytes = wav_io.getvalue()

        # Configure mock response
        mock_response = MagicMock()
        mock_response.audio_content = wav_bytes
        mock_client.synthesize_speech.return_value = mock_response

        # Mock AudioSegment behaviour
        mock_speech = MagicMock()
        mock_speech.dBFS = -10.0
        mock_speech.__len__.return_value = 2000  # 2000 ms

        mock_burst = MagicMock()
        mock_burst.dBFS = -15.0
        mock_burst.__len__.return_value = 500  # 500 ms

        # Configure mock_audio_segment.from_file to return speech then burst
        mock_audio_segment.from_file.side_effect = [mock_speech, mock_burst]

        # Mock overlay and chaining calls
        mock_speech.overlay.return_value = mock_speech
        mock_speech.set_frame_rate.return_value = mock_speech
        mock_speech.set_channels.return_value = mock_speech
        mock_speech.set_sample_width.return_value = mock_speech

        # Mock export to return mixed WAV bytes
        mock_export_io = io.BytesIO()
        with wave.open(mock_export_io, "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(16000)
            f.writeframes(b"mixed_data_burst")
        mock_export_bytes = mock_export_io.getvalue()

        def fake_export(out_f: typing.Any, format: typing.Any = "wav") -> None:
            out_f.write(mock_export_bytes)

        mock_speech.export.side_effect = fake_export

        # Execute
        result = self.transformer.text_to_speech_bytes(
            text="hello",
            credentials=MagicMock(),
            project_id="test-project",
            burst_noise_files=["burst.wav"],
            burst_noise_snr=5.0,
        )

        # Verify
        assert result["text"] == "hello"
        assert result["audio_bytes"] == b"mixed_data_burst"
        mock_audio_segment.from_file.assert_any_call(
            mock_audio_segment.from_file.call_args_list[0][0][0], format="wav"
        )
        mock_audio_segment.from_file.assert_any_call("burst.wav")

    @patch("cxas_scrapi.core.audio_transformer.AudioSegment")
    @patch("cxas_scrapi.core.audio_transformer.texttospeech")
    def test_text_to_speech_bytes_noise_failure_fallback(
        self, mock_tts: typing.Any, mock_audio_segment: typing.Any
    ) -> None:
        # Setup TTS mock
        mock_client = MagicMock()
        mock_tts.TextToSpeechClient.return_value = mock_client

        # Create raw WAV mock
        with io.BytesIO() as wav_io:
            with wave.open(wav_io, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(b"clean_audio_")
            wav_bytes = wav_io.getvalue()

        mock_response = MagicMock()
        mock_response.audio_content = wav_bytes
        mock_client.synthesize_speech.return_value = mock_response

        # Force pydub to fail on from_file to simulate file loading error
        mock_audio_segment.from_file.side_effect = Exception("Corrupt file")

        # Execute
        result = self.transformer.text_to_speech_bytes(
            text="hello",
            credentials=MagicMock(),
            project_id="test-project",
            burst_noise_files=["corrupt.wav"],
        )

        # Verify graceful fallback to clean audio
        assert result["text"] == "hello"
        assert result["audio_bytes"] == b"clean_audio_"

    @patch("google.auth.transport.requests.Request")
    @patch("cxas_scrapi.core.audio_transformer.texttospeech")
    def test_text_to_speech_bytes_refreshes_expired_credentials_and_retries(
        self, mock_tts: typing.Any, mock_auth_request: typing.Any
    ) -> None:
        mock_client_1 = MagicMock()
        mock_client_2 = MagicMock()
        mock_tts.TextToSpeechClient.side_effect = [mock_client_1, mock_client_2]

        with io.BytesIO() as wav_io:
            with wave.open(wav_io, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(b"recovered_audio_")
            wav_bytes = wav_io.getvalue()

        mock_response = MagicMock()
        mock_response.audio_content = wav_bytes
        mock_client_1.synthesize_speech.side_effect = RuntimeError(
            "401 Token expired"
        )
        mock_client_2.synthesize_speech.return_value = mock_response

        creds = MagicMock()
        result = self.transformer.text_to_speech_bytes(
            text="retry turn",
            credentials=creds,
            project_id="test-project",
        )

        assert result["audio_bytes"] == b"recovered_audio_"
        creds.refresh.assert_called_once_with(mock_auth_request.return_value)
        assert mock_tts.TextToSpeechClient.call_count == 2

    def test_load_background_noise_presets(self) -> None:
        assert len(BUILTIN_BACKGROUND_NOISE_PRESETS) == 13
        for preset in BUILTIN_BACKGROUND_NOISE_PRESETS:
            seg = load_background_noise_segment(preset, duration_ms=500)
            assert len(seg) == 500
            assert seg.frame_rate == 16000
            assert seg.channels == 1
            assert seg.sample_width == 2

    def test_load_background_noise_aliases_and_prefix(self) -> None:
        aliases = [
            "white_noise",
            "pink_noise",
            "brown_noise",
            "coffee_shop",
            "office",
            "road",
            "car",
            "other_people_speaking",
            "people_speaking",
            "background_speech",
            "competing_speaker",
            "chatter",
            "baby",
            "crying_baby",
            "infant_crying",
            "kids",
            "children",
            "playground",
            "dog",
            "barking",
            "tv",
            "television",
            "radio",
            "household",
            "home",
            "appliances",
            "wind_noise",
            "outdoor",
            "outdoors",
            "preset:cafe",
            "preset:call_center",
            "preset:cross_talk",
            "preset:baby_crying",
            "preset:wind",
        ]
        for alias in aliases:
            seg = load_background_noise_segment(alias, duration_ms=250)
            assert len(seg) == 250
            assert seg.frame_rate == 16000

    def test_load_background_noise_deterministic(self) -> None:
        seg1 = load_background_noise_segment("cafe", duration_ms=500)
        seg2 = load_background_noise_segment("cafe", duration_ms=500)
        assert seg1.raw_data == seg2.raw_data

    @patch("cxas_scrapi.core.audio_transformer.AudioSegment")
    def test_load_background_noise_custom_file(
        self, mock_audio_segment: MagicMock
    ) -> None:
        fake_segment = MagicMock()
        mock_audio_segment.from_file.return_value = fake_segment
        fake_segment.set_frame_rate.return_value = fake_segment
        fake_segment.set_channels.return_value = fake_segment
        fake_segment.set_sample_width.return_value = fake_segment

        res = load_background_noise_segment("/path/to/custom_noise.wav")
        mock_audio_segment.from_file.assert_called_once_with(
            "/path/to/custom_noise.wav"
        )
        fake_segment.set_frame_rate.assert_called_once_with(16000)
        assert res == fake_segment

    def test_load_background_noise_invalid_preset(self) -> None:
        with pytest.raises(ValueError, match="Unknown background noise preset"):
            load_background_noise_segment("non_existent_preset_xyz")

    def test_parse_noise_snr(self) -> None:
        assert BACKGROUND_NOISE_LEVELS["low"] == 20.0
        assert BACKGROUND_NOISE_LEVELS["medium"] == 12.0
        assert BACKGROUND_NOISE_LEVELS["high"] == 5.0
        assert parse_noise_snr(None) == 15.0
        assert parse_noise_snr(None, default=10.0) == 10.0
        assert parse_noise_snr(20.0) == 20.0
        assert parse_noise_snr(12) == 12.0
        assert parse_noise_snr("18.5") == 18.5
        assert parse_noise_snr("LOW") == 20.0
        assert parse_noise_snr("low") == 20.0
        assert parse_noise_snr("MEDIUM") == 12.0
        assert parse_noise_snr("med") == 12.0
        assert parse_noise_snr("moderate") == 12.0
        assert parse_noise_snr("HIGH") == 5.0
        assert parse_noise_snr("heavy") == 5.0
        assert parse_noise_snr("loud") == 5.0

        with pytest.raises(ValueError, match="Invalid noise level or SNR"):
            parse_noise_snr("invalid_level_xyz")

        with pytest.raises(TypeError, match="Cannot parse noise SNR from type"):
            parse_noise_snr(["invalid", "type"])  # type: ignore[arg-type]

    def test_snr_to_noise_level(self) -> None:
        assert snr_to_noise_level(None) == ""
        assert snr_to_noise_level(25.0) == "LOW"
        assert snr_to_noise_level(20.0) == "LOW"
        assert snr_to_noise_level(16.0) == "LOW"
        assert snr_to_noise_level(15.9) == "MEDIUM"
        assert snr_to_noise_level(12.0) == "MEDIUM"
        assert snr_to_noise_level(8.0) == "MEDIUM"
        assert snr_to_noise_level(7.9) == "HIGH"
        assert snr_to_noise_level(5.0) == "HIGH"
        assert snr_to_noise_level(0.0) == "HIGH"

    def test_resolve_noise_config(self) -> None:
        # Defaults
        res = resolve_noise_config()
        assert res == {
            "background_noise_file": None,
            "bg_noise_snr": 15.0,
            "bg_noise_level": "MEDIUM",
            "burst_noise_files": None,
            "burst_noise_snr": 5.0,
            "burst_noise_level": "HIGH",
        }

        # String preset
        res = resolve_noise_config({"background_noise": "cafe"})
        assert res["background_noise_file"] == "cafe"
        assert res["bg_noise_snr"] == 15.0
        assert res["bg_noise_level"] == "MEDIUM"

        # Shorthand string preset:LEVEL
        res = resolve_noise_config({"background_noise": "cafe:HIGH"})
        assert res["background_noise_file"] == "cafe"
        assert res["bg_noise_snr"] == 5.0
        assert res["bg_noise_level"] == "HIGH"

        # Shorthand string preset:SNR
        res = resolve_noise_config({"background_noise": "traffic:20.0"})
        assert res["background_noise_file"] == "traffic"
        assert res["bg_noise_snr"] == 20.0
        assert res["bg_noise_level"] == "LOW"

        # Dict config with snr and burst
        res = resolve_noise_config(
            {
                "background_noise": {
                    "preset": "traffic",
                    "snr": 10.0,
                    "burst_files": ["b1.wav"],
                    "burst_snr": 3.0,
                }
            }
        )
        assert res["background_noise_file"] == "traffic"
        assert res["bg_noise_snr"] == 10.0
        assert res["bg_noise_level"] == "MEDIUM"
        assert res["burst_noise_files"] == ["b1.wav"]
        assert res["burst_noise_snr"] == 3.0
        assert res["burst_noise_level"] == "HIGH"

        # Dict config with level keywords
        res = resolve_noise_config(
            {
                "background_noise": {
                    "preset": "call_center",
                    "level": "LOW",
                    "burst_files": ["b1.wav"],
                    "burst_level": "MEDIUM",
                }
            }
        )
        assert res["background_noise_file"] == "call_center"
        assert res["bg_noise_snr"] == 20.0
        assert res["bg_noise_level"] == "LOW"
        assert res["burst_noise_files"] == ["b1.wav"]
        assert res["burst_noise_snr"] == 12.0
        assert res["burst_noise_level"] == "MEDIUM"

        # Boolean False opt-out
        res = resolve_noise_config({"background_noise": False})
        assert res["background_noise_file"] is None

        # Nested config dict
        res = resolve_noise_config(
            {
                "config": {
                    "background_noise": "call_center",
                    "bg_noise_snr": 12.0,
                }
            }
        )
        assert res["background_noise_file"] == "call_center"
        assert res["bg_noise_snr"] == 12.0
        assert res["bg_noise_level"] == "MEDIUM"

        # Function argument override with categorical level
        res = resolve_noise_config(
            {"background_noise": "cafe"},
            background_noise_file="white",
            bg_noise_snr="LOW",
            burst_noise_files=["b2.wav"],
            burst_noise_snr="HIGH",
        )
        assert res["background_noise_file"] == "white"
        assert res["bg_noise_snr"] == 20.0
        assert res["bg_noise_level"] == "LOW"
        assert res["burst_noise_files"] == ["b2.wav"]
        assert res["burst_noise_snr"] == 5.0
        assert res["burst_noise_level"] == "HIGH"

    @patch("cxas_scrapi.core.audio_transformer.texttospeech")
    def test_synthesize_tts_competing_speech(
        self, mock_tts: typing.Any
    ) -> None:
        mock_client = MagicMock()
        mock_tts.TextToSpeechClient.return_value = mock_client

        with io.BytesIO() as wav_io:
            with wave.open(wav_io, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(b"\x00\x00" * 8000)  # 0.5s silence
            wav_bytes = wav_io.getvalue()

        mock_resp = MagicMock()
        mock_resp.audio_content = wav_bytes
        mock_client.synthesize_speech.return_value = mock_resp

        seg = synthesize_tts_competing_speech(
            phrases=["Phrase 1", "Phrase 2"],
            voices=("Puck", "Charon"),
            credentials=MagicMock(),
            project_id="test-proj",
        )
        assert seg is not None
        assert len(seg) > 0
        assert mock_client.synthesize_speech.call_count == 2
