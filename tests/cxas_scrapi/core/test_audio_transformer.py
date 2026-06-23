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
import wave
from unittest.mock import MagicMock, patch

from cxas_scrapi.core.audio_transformer import AudioTransformer


class TestAudioTransformer:
    def setup_method(self):
        AudioTransformer._client = None
        self.transformer = AudioTransformer()

    @patch("cxas_scrapi.core.audio_transformer.texttospeech")
    def test_text_to_speech_bytes_success(self, mock_tts):
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

    @patch("cxas_scrapi.core.audio_transformer.texttospeech")
    def test_text_to_speech_bytes_api_error(self, mock_tts):
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
    def test_text_to_speech_bytes_invalid_wav(self, mock_tts):
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

    @patch("cxas_scrapi.core.audio_transformer.AudioSegment")
    @patch("cxas_scrapi.core.audio_transformer.texttospeech")
    def test_text_to_speech_bytes_with_burst_noise_success(
        self, mock_tts, mock_audio_segment
    ):
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

        def fake_export(out_f, format="wav"):
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
        self, mock_tts, mock_audio_segment
    ):
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
