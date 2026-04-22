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
