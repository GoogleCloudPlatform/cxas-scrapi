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

import contextlib
import io
import logging
import math
import os
import pathlib
import random
import threading
import typing
import wave
from typing import Any

import numpy as np

try:
    from pydub import AudioSegment
except ImportError:
    AudioSegment = None

import google.auth
from google.api_core import client_options
from google.cloud import texttospeech

ClientOptions = client_options.ClientOptions

# Standard audio formats for CXAS voice streaming (16kHz, 1ch, 16-bit PCM)
AUDIO_SAMPLE_RATE_HZ = 16000
AUDIO_CHANNELS = 1
AUDIO_SAMPLE_WIDTH = 2

DEFAULT_TTS_LANGUAGE_CODE = "en-US"
DEFAULT_TTS_VOICE_NAME = "Aoede"
DEFAULT_TTS_MODEL_NAME = "gemini-3.8-flash-lite-tts"

BUILTIN_BACKGROUND_NOISE_PRESETS = (
    "white",
    "pink",
    "brown",
    "cafe",
    "call_center",
    "traffic",
    "cross_talk",
    "baby_crying",
    "kids_noise",
    "dog_barking",
    "tv_radio",
    "kitchen",
    "wind",
)

_NOISE_PRESET_ALIASES: dict[str, str] = {
    "white": "white",
    "white_noise": "white",
    "pink": "pink",
    "pink_noise": "pink",
    "brown": "brown",
    "brown_noise": "brown",
    "brownian": "brown",
    "red": "brown",
    "cafe": "cafe",
    "coffee_shop": "cafe",
    "restaurant": "cafe",
    "call_center": "call_center",
    "office": "call_center",
    "traffic": "traffic",
    "street": "traffic",
    "road": "traffic",
    "car": "traffic",
    "cross_talk": "cross_talk",
    "other_people_speaking": "cross_talk",
    "people_speaking": "cross_talk",
    "background_speech": "cross_talk",
    "competing_speaker": "cross_talk",
    "chatter": "cross_talk",
    "baby_crying": "baby_crying",
    "baby": "baby_crying",
    "crying_baby": "baby_crying",
    "infant_crying": "baby_crying",
    "kids_noise": "kids_noise",
    "kids": "kids_noise",
    "children": "kids_noise",
    "playground": "kids_noise",
    "dog_barking": "dog_barking",
    "dog": "dog_barking",
    "barking": "dog_barking",
    "tv_radio": "tv_radio",
    "tv": "tv_radio",
    "television": "tv_radio",
    "radio": "tv_radio",
    "kitchen": "kitchen",
    "household": "kitchen",
    "home": "kitchen",
    "appliances": "kitchen",
    "wind": "wind",
    "wind_noise": "wind",
    "outdoor": "wind",
    "outdoors": "wind",
}

BACKGROUND_NOISE_LEVELS: dict[str, float] = {
    "low": 20.0,
    "medium": 12.0,
    "med": 12.0,
    "moderate": 12.0,
    "high": 5.0,
    "heavy": 5.0,
    "loud": 5.0,
}


def parse_noise_snr(value: Any, default: float = 15.0) -> float:
    """Parses a noise level string or numeric value into an SNR in dB.

    Args:
        value: Numeric SNR in dB, or categorical noise level string ('LOW',
            'MEDIUM', 'HIGH', 'med', 'moderate', 'heavy', 'loud').
        default: Default SNR in dB if value is None.

    Returns:
        Float SNR in dB.
    """
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip().lower()
        if stripped in BACKGROUND_NOISE_LEVELS:
            return BACKGROUND_NOISE_LEVELS[stripped]
        try:
            return float(stripped)
        except ValueError:
            raise ValueError(
                f"Invalid noise level or SNR: '{value}'. "
                f"Expected float dB or one of: LOW, MEDIUM, HIGH"
            ) from None
    raise TypeError(f"Cannot parse noise SNR from type {type(value).__name__}")


def snr_to_noise_level(snr_db: float | None) -> str:
    """Maps an SNR in dB to a categorical noise level ('LOW', 'MEDIUM', 'HIGH').

    Args:
        snr_db: Signal-to-noise ratio in decibels.

    Returns:
        Categorical level string: 'LOW' (>= 16 dB), 'MEDIUM' (8 to 16 dB), or
        'HIGH' (< 8 dB). Returns empty string if snr_db is None.
    """
    if snr_db is None:
        return ""
    snr = float(snr_db)
    if snr >= 16.0:
        return "LOW"
    if snr >= 8.0:
        return "MEDIUM"
    return "HIGH"


def load_background_noise_segment(
    noise_source: str,
    duration_ms: int = 10000,
    sample_rate: int = AUDIO_SAMPLE_RATE_HZ,
) -> Any:
    """Loads or synthesizes a background noise AudioSegment.

    Args:
        noise_source: Path to an audio file or name of a built-in preset
            ('white', 'pink', 'brown', 'cafe', 'call_center', 'traffic').
        duration_ms: Desired duration in milliseconds for synthesized presets.
        sample_rate: Audio sampling rate in Hertz (default 16000).

    Returns:
        AudioSegment formatted to mono 16-bit PCM at sample_rate.
    """
    if AudioSegment is None:
        raise ImportError(
            "background_noise_file was provided, but pydub is not installed "
            "or failed to import. Please install pydub (and audioop-lts on "
            "Python 3.13+) to enable background noise."
        )

    norm_key = noise_source.strip().lower()
    is_explicit_preset = norm_key.startswith("preset:")
    if is_explicit_preset:
        norm_key = norm_key.removeprefix("preset:").strip()
    if not os.path.isfile(noise_source) and ":" in norm_key:
        prefix, _, suffix = norm_key.rpartition(":")
        if prefix in _NOISE_PRESET_ALIASES:
            with contextlib.suppress(ValueError, TypeError):
                parse_noise_snr(suffix)
                norm_key = prefix

    preset = _NOISE_PRESET_ALIASES.get(norm_key)
    if not preset and not os.path.isfile(noise_source):
        audio_exts = (
            ".wav",
            ".mp3",
            ".ogg",
            ".flac",
            ".raw",
            ".pcm",
            ".aiff",
            ".m4a",
        )
        if is_explicit_preset or not any(
            noise_source.lower().endswith(ext) for ext in audio_exts
        ):
            raise ValueError(
                f"Unknown background noise preset: '{noise_source}'. "
                f"Available presets: "
                f"{', '.join(BUILTIN_BACKGROUND_NOISE_PRESETS)}"
            )

    if preset and not os.path.isfile(noise_source):
        # 1. Mathematical synthesis is reserved ONLY for white, pink, and brown.
        if preset in ("white", "pink", "brown"):
            rng = np.random.default_rng(42)
            num_samples = int(sample_rate * (duration_ms / 1000.0))
            if preset == "white":
                signal = rng.standard_normal(num_samples)
            else:
                white_fft = np.fft.rfft(rng.standard_normal(num_samples))
                freqs = np.fft.rfftfreq(num_samples, d=1.0 / sample_rate)
                f_safe = np.maximum(freqs, 1.0)
                if preset == "pink":
                    weights = 1.0 / np.sqrt(f_safe)
                    weights *= np.clip(freqs / 20.0, 0.0, 1.0)
                    signal = np.fft.irfft(white_fft * weights, n=num_samples)
                else:  # brown
                    weights = 1.0 / f_safe
                    weights *= np.clip(freqs / 25.0, 0.0, 1.0)
                    signal = np.fft.irfft(white_fft * weights, n=num_samples)

            rms = np.sqrt(np.mean(signal**2))
            if rms > 1e-9:
                signal = signal * (3276.7 / rms)

            signal = np.clip(signal, -32768.0, 32767.0).astype(np.int16)
            return AudioSegment(
                data=signal.tobytes(),
                sample_width=AUDIO_SAMPLE_WIDTH,
                frame_rate=sample_rate,
                channels=AUDIO_CHANNELS,
            )

        # 2. For the 10 real-world presets, load the verified bundled recording
        bundled_path = (
            pathlib.Path(__file__).resolve().parent.parent
            / "resources"
            / "noise"
            / f"{preset}.wav"
        )
        if bundled_path.is_file():
            segment = AudioSegment.from_file(str(bundled_path))
            segment = (
                segment.set_frame_rate(sample_rate)
                .set_channels(AUDIO_CHANNELS)
                .set_sample_width(AUDIO_SAMPLE_WIDTH)
            )
            if len(segment) < duration_ms:
                repeats = math.ceil(duration_ms / len(segment))
                segment = segment * repeats
            return segment[:duration_ms]

    segment = AudioSegment.from_file(noise_source)
    return (
        segment.set_frame_rate(sample_rate)
        .set_channels(AUDIO_CHANNELS)
        .set_sample_width(AUDIO_SAMPLE_WIDTH)
    )


def resolve_noise_config(
    config_or_case: dict[str, Any] | None = None,
    background_noise_file: str | None = None,
    bg_noise_snr: float | str | None = None,
    burst_noise_files: list[str] | None = None,
    burst_noise_snr: float | str | None = None,
    bg_noise_level: str | None = None,
    burst_noise_level: str | None = None,
) -> dict[str, Any]:
    """Resolves background and burst noise parameters from config or case."""
    resolved_bg_file = None
    resolved_bg_snr = None
    resolved_burst_files = None
    resolved_burst_snr = None
    bg_opted_out = False
    burst_opted_out = False

    if isinstance(config_or_case, dict):
        cfg = (
            config_or_case.get("config")
            if isinstance(config_or_case.get("config"), dict)
            else {}
        )

        bg_val = config_or_case.get("background_noise")
        if bg_val is None:
            bg_val = cfg.get("background_noise")

        if bg_val is False:
            bg_opted_out = True
            resolved_bg_file = None
        elif isinstance(bg_val, str):
            if not os.path.isfile(bg_val) and ":" in bg_val:
                prefix, _, suffix = bg_val.rpartition(":")
                prefix_clean = prefix.strip()
                norm_prefix = prefix_clean.lower().removeprefix("preset:")
                if norm_prefix in _NOISE_PRESET_ALIASES or os.path.isfile(
                    prefix_clean
                ):
                    with contextlib.suppress(ValueError, TypeError):
                        resolved_bg_snr = parse_noise_snr(suffix)
                        resolved_bg_file = prefix_clean
            if resolved_bg_file is None:
                resolved_bg_file = bg_val
        elif isinstance(bg_val, dict):
            resolved_bg_file = (
                bg_val.get("file")
                or bg_val.get("preset")
                or bg_val.get("source")
                or bg_val.get("name")
            )
            if (
                isinstance(resolved_bg_file, str)
                and not os.path.isfile(resolved_bg_file)
                and ":" in resolved_bg_file
            ):
                prefix, _, suffix = resolved_bg_file.rpartition(":")
                prefix_clean = prefix.strip()
                norm_prefix = prefix_clean.lower().removeprefix("preset:")
                if norm_prefix in _NOISE_PRESET_ALIASES or os.path.isfile(
                    prefix_clean
                ):
                    with contextlib.suppress(ValueError, TypeError):
                        resolved_bg_snr = parse_noise_snr(suffix)
                        resolved_bg_file = prefix_clean

            if "snr" in bg_val:
                resolved_bg_snr = bg_val.get("snr")
            elif "bg_noise_snr" in bg_val:
                resolved_bg_snr = bg_val.get("bg_noise_snr")
            elif "level" in bg_val:
                resolved_bg_snr = bg_val.get("level")
            elif "bg_noise_level" in bg_val:
                resolved_bg_snr = bg_val.get("bg_noise_level")
            elif "noise_level" in bg_val:
                resolved_bg_snr = bg_val.get("noise_level")

            if "burst_files" in bg_val:
                resolved_burst_files = bg_val.get("burst_files")
            elif "burst_noise_files" in bg_val:
                resolved_burst_files = bg_val.get("burst_noise_files")

            if "burst_snr" in bg_val:
                resolved_burst_snr = bg_val.get("burst_snr")
            elif "burst_noise_snr" in bg_val:
                resolved_burst_snr = bg_val.get("burst_noise_snr")
            elif "burst_level" in bg_val:
                resolved_burst_snr = bg_val.get("burst_level")
            elif "burst_noise_level" in bg_val:
                resolved_burst_snr = bg_val.get("burst_noise_level")

        bg_file_val = config_or_case.get("background_noise_file")
        if bg_file_val is None:
            bg_file_val = cfg.get("background_noise_file")
        if bg_file_val is False:
            bg_opted_out = True
            resolved_bg_file = None
        elif isinstance(bg_file_val, str) and not bg_opted_out:
            if not os.path.isfile(bg_file_val) and ":" in bg_file_val:
                prefix, _, suffix = bg_file_val.rpartition(":")
                prefix_clean = prefix.strip()
                norm_prefix = prefix_clean.lower().removeprefix("preset:")
                if norm_prefix in _NOISE_PRESET_ALIASES or os.path.isfile(
                    prefix_clean
                ):
                    with contextlib.suppress(ValueError, TypeError):
                        resolved_bg_snr = parse_noise_snr(suffix)
                        resolved_bg_file = prefix_clean
            if resolved_bg_file is None or not bg_val:
                resolved_bg_file = bg_file_val

        level_val = (
            config_or_case.get("bg_noise_level")
            or config_or_case.get("noise_level")
            or cfg.get("bg_noise_level")
            or cfg.get("noise_level")
            or config_or_case.get("level")
            or cfg.get("level")
        )
        if (
            level_val is not None
            and resolved_bg_snr is None
            and not bg_opted_out
        ):
            with contextlib.suppress(ValueError, TypeError):
                resolved_bg_snr = parse_noise_snr(level_val)

        snr_val = config_or_case.get("bg_noise_snr")
        if snr_val is None:
            snr_val = cfg.get("bg_noise_snr")
        if snr_val is not None and not bg_opted_out:
            with contextlib.suppress(ValueError, TypeError):
                resolved_bg_snr = parse_noise_snr(snr_val)

        bf_val = config_or_case.get("burst_noise_files")
        if bf_val is None:
            bf_val = cfg.get("burst_noise_files")
        if bf_val is False:
            burst_opted_out = True
            resolved_burst_files = None
        elif (
            bf_val is not None
            and resolved_burst_files is None
            and not burst_opted_out
        ):
            resolved_burst_files = bf_val

        burst_level_val = (
            config_or_case.get("burst_noise_level")
            or config_or_case.get("burst_level")
            or cfg.get("burst_noise_level")
            or cfg.get("burst_level")
        )
        if (
            burst_level_val is not None
            and resolved_burst_snr is None
            and not burst_opted_out
        ):
            with contextlib.suppress(ValueError, TypeError):
                resolved_burst_snr = parse_noise_snr(burst_level_val)

        bsnr_val = config_or_case.get("burst_noise_snr")
        if bsnr_val is None:
            bsnr_val = cfg.get("burst_noise_snr")
        if bsnr_val is not None and not burst_opted_out:
            with contextlib.suppress(ValueError, TypeError):
                resolved_burst_snr = parse_noise_snr(bsnr_val)

    if not bg_opted_out and background_noise_file is not None:
        if (
            isinstance(background_noise_file, str)
            and not os.path.isfile(background_noise_file)
            and ":" in background_noise_file
        ):
            prefix, _, suffix = background_noise_file.rpartition(":")
            prefix_clean = prefix.strip()
            norm_prefix = prefix_clean.lower().removeprefix("preset:")
            if norm_prefix in _NOISE_PRESET_ALIASES or os.path.isfile(
                prefix_clean
            ):
                with contextlib.suppress(ValueError, TypeError):
                    resolved_bg_snr = parse_noise_snr(suffix)
                    resolved_bg_file = prefix_clean
        if resolved_bg_file is None or ":" not in str(background_noise_file):
            resolved_bg_file = background_noise_file or None

    if not bg_opted_out and bg_noise_level is not None:
        with contextlib.suppress(ValueError, TypeError):
            resolved_bg_snr = parse_noise_snr(bg_noise_level)

    if not bg_opted_out and bg_noise_snr is not None:
        with contextlib.suppress(ValueError, TypeError):
            resolved_bg_snr = parse_noise_snr(bg_noise_snr)

    if not burst_opted_out and burst_noise_files is not None:
        resolved_burst_files = burst_noise_files

    if not burst_opted_out and burst_noise_level is not None:
        with contextlib.suppress(ValueError, TypeError):
            resolved_burst_snr = parse_noise_snr(burst_noise_level)

    if not burst_opted_out and burst_noise_snr is not None:
        with contextlib.suppress(ValueError, TypeError):
            resolved_burst_snr = parse_noise_snr(burst_noise_snr)

    final_bg_snr = parse_noise_snr(resolved_bg_snr, default=15.0)
    final_burst_snr = parse_noise_snr(resolved_burst_snr, default=5.0)

    return {
        "background_noise_file": resolved_bg_file,
        "bg_noise_snr": final_bg_snr,
        "bg_noise_level": snr_to_noise_level(final_bg_snr),
        "burst_noise_files": resolved_burst_files,
        "burst_noise_snr": final_burst_snr,
        "burst_noise_level": snr_to_noise_level(final_burst_snr),
    }


def synthesize_tts_competing_speech(
    phrases: list[str],
    voices: tuple[str, ...] = ("Puck", "Charon", "Kore"),
    sample_rate: int = AUDIO_SAMPLE_RATE_HZ,
    credentials: Any = None,
    project_id: str | None = None,
    language_code: str = DEFAULT_TTS_LANGUAGE_CODE,
    model_name: str = DEFAULT_TTS_MODEL_NAME,
) -> Any:
    """Synthesizes human competing speech phrases using Google Cloud TTS.

    Args:
        phrases: List of text phrases spoken by competing speakers.
        voices: Tuple of voice names cycled across phrases.
        sample_rate: Audio sampling rate in Hertz (default 16000).
        credentials: Optional Google Cloud credentials.
        project_id: Optional GCP quota project ID.
        language_code: BCP-47 language code (default 'en-US').
        model_name: TTS model name (default 'gemini-3.8-flash-lite-tts').

    Returns:
        AudioSegment containing the sequenced speech phrases.
    """
    if AudioSegment is None:
        raise ImportError(
            "pydub is not installed or failed to import. "
            "Please install pydub to synthesize competing speech."
        )

    if not phrases:
        return AudioSegment.silent(duration=0, frame_rate=sample_rate)

    if credentials is None:
        with contextlib.suppress(Exception):
            credentials, def_project = google.auth.default()
            if project_id is None:
                project_id = def_project

    client_options = (
        ClientOptions(quota_project_id=project_id) if project_id else None
    )
    client = texttospeech.TextToSpeechClient(
        credentials=credentials, client_options=client_options
    )

    combined = AudioSegment.silent(duration=0, frame_rate=sample_rate)
    for i, phrase in enumerate(phrases):
        voice_name = voices[i % len(voices)]
        synthesis_input = texttospeech.SynthesisInput(text=phrase)
        voice_kwargs: dict[str, Any] = {
            "language_code": language_code,
            "name": voice_name,
        }
        if model_name:
            voice_kwargs["model_name"] = model_name
        voice = texttospeech.VoiceSelectionParams(**voice_kwargs)
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=sample_rate,
        )
        response = client.synthesize_speech(
            input=synthesis_input, voice=voice, audio_config=audio_config
        )
        phrase_seg = (
            AudioSegment.from_wav(io.BytesIO(response.audio_content))
            .set_frame_rate(sample_rate)
            .set_channels(AUDIO_CHANNELS)
            .set_sample_width(AUDIO_SAMPLE_WIDTH)
        )
        if len(combined) > 0:
            combined += AudioSegment.silent(
                duration=400, frame_rate=sample_rate
            )
        combined += phrase_seg

    return combined


class AudioTransformer:
    _client = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        pass

    def text_to_speech_bytes(
        self,
        text: str,
        credentials: typing.Any,
        project_id: str,
        background_noise_file: str | None = None,
        bg_noise_snr: float = 15.0,
        burst_noise_files: list[str] | None = None,
        burst_noise_snr: float = 5.0,
        voice_config: dict[str, Any] | None = None,
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
        voice_config = voice_config or {}
        prompt = voice_config.get("prompt")
        if prompt and isinstance(prompt, str) and prompt.strip():
            synthesis_input = texttospeech.SynthesisInput(
                text=text, prompt=prompt.strip()
            )
        else:
            synthesis_input = texttospeech.SynthesisInput(text=text)

        language_code = (
            voice_config.get("language_code") or DEFAULT_TTS_LANGUAGE_CODE
        )
        voice_name = voice_config.get("voice_name") or DEFAULT_TTS_VOICE_NAME
        model_name = voice_config.get("model_name", DEFAULT_TTS_MODEL_NAME)

        voice_kwargs: dict[str, Any] = {
            "language_code": language_code,
            "name": voice_name,
        }
        if model_name:
            voice_kwargs["model_name"] = model_name

        voice = texttospeech.VoiceSelectionParams(**voice_kwargs)
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
        )
        try:
            try:
                response = client.synthesize_speech(
                    input=synthesis_input,
                    voice=voice,
                    audio_config=audio_config,
                )
            except Exception as e:
                if (
                    "is not supported" in str(e)
                    and model_name == DEFAULT_TTS_MODEL_NAME
                ):
                    fallback_kwargs = dict(voice_kwargs)
                    fallback_kwargs["model_name"] = "gemini-2.5-flash-tts"
                    fallback_voice = texttospeech.VoiceSelectionParams(
                        **fallback_kwargs
                    )
                    response = client.synthesize_speech(
                        input=synthesis_input,
                        voice=fallback_voice,
                        audio_config=audio_config,
                    )
                else:
                    with AudioTransformer._lock:
                        if credentials is not None and hasattr(
                            credentials, "refresh"
                        ):
                            try:
                                from google.auth.transport.requests import (  # noqa: PLC0415
                                    Request as AuthRequest,
                                )

                                credentials.refresh(AuthRequest())
                            except Exception as refresh_err:
                                logging.debug(
                                    "Failed to refresh TTS credentials: "
                                    f"{refresh_err}"
                                )
                        client_opts = ClientOptions(quota_project_id=project_id)
                        tts_cls = texttospeech.TextToSpeechClient
                        AudioTransformer._client = tts_cls(
                            credentials=credentials, client_options=client_opts
                        )
                        client = AudioTransformer._client
                    try:
                        response = client.synthesize_speech(
                            input=synthesis_input,
                            voice=voice,
                            audio_config=audio_config,
                        )
                    except Exception as retry_e:
                        if (
                            "is not supported" in str(retry_e)
                            and model_name == DEFAULT_TTS_MODEL_NAME
                        ):
                            fallback_kwargs = dict(voice_kwargs)
                            fallback_kwargs["model_name"] = (
                                "gemini-2.5-flash-tts"
                            )
                            fallback_voice = texttospeech.VoiceSelectionParams(
                                **fallback_kwargs
                            )
                            response = client.synthesize_speech(
                                input=synthesis_input,
                                voice=fallback_voice,
                                audio_config=audio_config,
                            )
                        else:
                            raise

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
            with io.BytesIO(response.audio_content) as wav_io:  # noqa: SIM117
                with wave.open(wav_io, "rb") as wav_file:
                    # Verify format if needed, but for now just read all frames
                    audio_bytes = wav_file.readframes(wav_file.getnframes())
                    return {"text": text, "audio_bytes": audio_bytes}
        except Exception as e:
            logging.debug(f"Error processing audio content: {e}")
            return {"text": text, "audio_bytes": None}
