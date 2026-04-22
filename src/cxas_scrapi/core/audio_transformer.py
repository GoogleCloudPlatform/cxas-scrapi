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
import logging
import wave

from google.api_core import client_options
from google.cloud import texttospeech

ClientOptions = client_options.ClientOptions


class AudioTransformer:
    def __init__(self):
        pass

    def text_to_speech_bytes(
        self, text: str, credentials, project_id: str
    ) -> dict:
        """Converts text to speech and returns a dictionary with text and
        audio bytes without saving to disk.
        """
        client_options = ClientOptions(quota_project_id=project_id)
        client = texttospeech.TextToSpeechClient(
            credentials=credentials, client_options=client_options
        )
        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice = texttospeech.VoiceSelectionParams(
            language_code="en-US", name="en-US-Standard-A"
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
        )
        try:
            response = client.synthesize_speech(
                input=synthesis_input, voice=voice, audio_config=audio_config
            )

            # The response.audio_content is a WAV file (RIFF header + data)
            # We need to strip the header to get raw PCM bytes.
            with io.BytesIO(response.audio_content) as wav_io:
                with wave.open(wav_io, "rb") as wav_file:
                    # Verify format if needed, but for now just read all frames
                    audio_bytes = wav_file.readframes(wav_file.getnframes())
                    return {"text": text, "audio_bytes": audio_bytes}
        except Exception as e:
            logging.debug(f"Error processing audio content: {e}")
            return {"text": text, "audio_bytes": None}
