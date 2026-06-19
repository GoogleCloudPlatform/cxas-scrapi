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
import random
import threading
import wave

try:
    from pydub import AudioSegment
except ImportError:
    AudioSegment = None

from google.api_core import client_options
from google.cloud import texttospeech

ClientOptions = client_options.ClientOptions

# Standard audio formats for CXAS voice streaming (16kHz, 1ch, 16-bit PCM)
AUDIO_SAMPLE_RATE_HZ = 16000
AUDIO_CHANNELS = 1
AUDIO_SAMPLE_WIDTH = 2


class AudioTransformer:
    _client = None
    _lock = threading.Lock()

    def __init__(self):
        pass

    def text_to_speech_bytes(
        self,
        text: str,
        credentials,
        project_id: str,
        background_noise_file: str | None = None,
        bg_noise_snr: float = 15.0,
        burst_noise_files: list[str] | None = None,
        burst_noise_snr: float = 5.0,
    ) -> dict:
        """Converts text to speech and returns a dictionary with text and
        audio bytes without saving to disk. Background and burst noise can
        optionally be applied.
        """
        if AudioTransformer._client is None:
            with AudioTransformer._lock:
                if AudioTransformer._client is None:
                    client_options = ClientOptions(quota_project_id=project_id)
                    AudioTransformer._client = texttospeech.TextToSpeechClient(
                        credentials=credentials, client_options=client_options
                    )

        client = AudioTransformer._client
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

            if burst_noise_files and AudioSegment is None:
                logging.warning(
                    "burst_noise_files were provided, but pydub is not "
                    "installed or failed to import (e.g., under Python 3.13+). "
                    "Audio burst noise injection will be skipped."
                )

            # Apply burst noise if provided and pydub is available
            if AudioSegment and burst_noise_files:
                try:
                    # Load the generated speech
                    speech_audio = AudioSegment.from_file(
                        io.BytesIO(response.audio_content), format="wav"
                    )

                    # Apply burst noise
                    for burst_file in burst_noise_files:
                        burst_noise = AudioSegment.from_file(burst_file)
                        # Calculate dynamic volume to match target
                        # burst_noise_snr
                        if burst_noise.dBFS != float(
                            "-inf"
                        ) and speech_audio.dBFS != float("-inf"):
                            target_burst_dbfs = (
                                speech_audio.dBFS - burst_noise_snr
                            )
                            volume_change = target_burst_dbfs - burst_noise.dBFS
                            burst_noise = burst_noise + volume_change
                        else:
                            burst_noise = burst_noise - 10

                        # Overlay at randomized position
                        if len(speech_audio) > len(burst_noise):
                            position = random.randint(
                                0, len(speech_audio) - len(burst_noise)
                            )
                            speech_audio = speech_audio.overlay(
                                burst_noise, position=position
                            )
                        else:
                            speech_audio = speech_audio.overlay(
                                burst_noise, position=0
                            )

                    # Export the mixed audio to raw PCM bytes
                    mixed_io = io.BytesIO()
                    speech_audio = (
                        speech_audio.set_frame_rate(AUDIO_SAMPLE_RATE_HZ)
                        .set_channels(AUDIO_CHANNELS)
                        .set_sample_width(AUDIO_SAMPLE_WIDTH)
                    )
                    speech_audio.export(mixed_io, format="wav")
                    mixed_io.seek(0)

                    with wave.open(mixed_io, "rb") as wav_file:
                        audio_bytes = wav_file.readframes(wav_file.getnframes())
                        return {"text": text, "audio_bytes": audio_bytes}

                except Exception as ex:
                    logging.warning(
                        f"Failed to apply audio effects using pydub: {ex}"
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
