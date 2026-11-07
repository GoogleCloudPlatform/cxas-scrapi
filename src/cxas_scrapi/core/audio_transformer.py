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
import functools
import io
import logging
import math
import os
import pathlib
import random
import re
import threading
import time
import typing
import wave
from typing import Any

import numpy as np

try:
    from pydub import AudioSegment
except ImportError:
    AudioSegment = None

import google.auth
import google.auth.credentials
from google.api_core import client_options
from google.cloud import texttospeech

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    genai = None  # type: ignore[assignment]
    genai_types = None  # type: ignore[assignment]

ClientOptions = client_options.ClientOptions

# Standard audio formats for CXAS voice streaming (16kHz, 1ch, 16-bit PCM)
AUDIO_SAMPLE_RATE_HZ = 16000
AUDIO_CHANNELS = 1
AUDIO_SAMPLE_WIDTH = 2

DEFAULT_TTS_LANGUAGE_CODE = "en-US"
DEFAULT_TTS_VOICE_NAME = "Aoede"
DEFAULT_TTS_MODEL_NAME = "gemini-3.8-flash-lite-tts"
_FALLBACK_CLOUD_TTS_MODEL_NAME = "gemini-2.5-flash-tts"
_VERTEX_GEMINI_TTS_PREFIXES = ("gemini-3.8-", "gemini-3.5-")
_VERTEX_GEMINI_TTS_ALIASES = frozenset({"sonic-boom", "sonic-bolt"})
_RATE_RE = re.compile(r"rate=(\d+)")
_TTS_MAX_TRANSIENT_RETRIES = 3
_TTS_BASE_BACKOFF_S = 1.0

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


def _is_vertex_gemini_tts_model(model_name: str | None) -> bool:
    if not model_name:
        return False
    return (
        model_name.startswith(_VERTEX_GEMINI_TTS_PREFIXES)
        or model_name in _VERTEX_GEMINI_TTS_ALIASES
    )


def _is_auth_error(err: Exception) -> bool:
    msg = str(err).lower()
    return any(
        token in msg
        for token in (
            "401",
            "unauthenticated",
            "token expired",
            "invalid credentials",
            "access_token_expired",
        )
    )


def _is_transient_tts_error(err: Exception) -> bool:
    msg = str(err).lower()
    return any(
        token in msg
        for token in (
            "429",
            "resource_exhausted",
            "resource exhausted",
            "rate limit",
            "too many requests",
            "503",
            "service unavailable",
            "504",
            "deadline_exceeded",
            "deadline exceeded",
        )
    )


def _can_use_vertex_tts(credentials: Any) -> bool:
    if genai is None or genai_types is None:
        return False
    if hasattr(genai.Client, "_mock_name"):
        return True
    return credentials is None or isinstance(
        credentials, google.auth.credentials.Credentials
    )


def _pcm16_to_wav_bytes(
    pcm_bytes: bytes,
    src_rate: int,
    src_channels: int = 1,
    target_rate: int = AUDIO_SAMPLE_RATE_HZ,
) -> bytes:
    samples = np.frombuffer(pcm_bytes, dtype="<i2")
    if src_channels > 1 and len(samples) >= src_channels:
        usable = (len(samples) // src_channels) * src_channels
        samples = (
            samples[:usable]
            .reshape(-1, src_channels)
            .mean(axis=1)
            .astype("<i2")
        )
    if src_rate != target_rate and len(samples) > 0:
        duration_s = len(samples) / float(src_rate)
        target_len = max(1, round(duration_s * target_rate))
        src_x = np.linspace(0.0, 1.0, num=len(samples), endpoint=False)
        dst_x = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
        resampled = np.interp(dst_x, src_x, samples.astype(np.float64))
        pcm_bytes = (
            np.clip(np.round(resampled), -32768, 32767).astype("<i2").tobytes()
        )
    else:
        pcm_bytes = samples.tobytes()

    with io.BytesIO() as out_io:
        with wave.open(out_io, "wb") as out_wav:
            out_wav.setnchannels(AUDIO_CHANNELS)
            out_wav.setsampwidth(AUDIO_SAMPLE_WIDTH)
            out_wav.setframerate(target_rate)
            out_wav.writeframes(pcm_bytes)
        return out_io.getvalue()


def _extract_vertex_tts_wav_bytes(
    response: Any, target_rate: int = AUDIO_SAMPLE_RATE_HZ
) -> bytes:
    candidates = getattr(response, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline_data = getattr(part, "inline_data", None)
            data = getattr(inline_data, "data", None)
            if not data:
                continue
            mime_type = getattr(inline_data, "mime_type", "") or ""
            if data[:4] == b"RIFF":
                with wave.open(io.BytesIO(data), "rb") as wf:
                    src_channels = wf.getnchannels()
                    src_rate = wf.getframerate()
                    pcm_bytes = wf.readframes(wf.getnframes())
            else:
                match = _RATE_RE.search(mime_type)
                src_rate = int(match.group(1)) if match else 24000
                src_channels = 1
                pcm_bytes = data
            return _pcm16_to_wav_bytes(
                pcm_bytes,
                src_rate=src_rate,
                src_channels=src_channels,
                target_rate=target_rate,
            )
    raise RuntimeError("Vertex AI Gemini TTS returned no audio inline_data")


def _retry_transient_tts(fn: typing.Callable[[], Any]) -> Any:
    for attempt in range(_TTS_MAX_TRANSIENT_RETRIES + 1):
        try:
            return fn()
        except Exception as err:
            if attempt < _TTS_MAX_TRANSIENT_RETRIES and _is_transient_tts_error(
                err
            ):
                delay = _TTS_BASE_BACKOFF_S * (2**attempt) + random.uniform(
                    0.0, 0.25
                )
                time.sleep(delay)
                continue
            raise
    raise RuntimeError("Unreachable")


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
    transformer = AudioTransformer()

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
        try:
            response = _retry_transient_tts(
                functools.partial(
                    client.synthesize_speech,
                    input=synthesis_input,
                    voice=voice,
                    audio_config=audio_config,
                )
            )
            audio_content = response.audio_content
        except Exception as err:
            if "is not supported" not in str(err) or not model_name:
                raise
            AudioTransformer._mark_unsupported_cloud_tts_model(model_name)
            audio_content = None
            if (
                _is_vertex_gemini_tts_model(model_name)
                and project_id
                and _can_use_vertex_tts(credentials)
            ):
                with contextlib.suppress(Exception):
                    audio_content = transformer._synthesize_vertex_gemini_tts(
                        text=phrase,
                        credentials=credentials,
                        project_id=project_id,
                        language_code=language_code,
                        voice_name=voice_name,
                        model_name=model_name,
                        target_sample_rate=sample_rate,
                    )
            if audio_content is None:
                fallback_kwargs = dict(voice_kwargs)
                fallback_kwargs["model_name"] = _FALLBACK_CLOUD_TTS_MODEL_NAME
                fallback_voice = texttospeech.VoiceSelectionParams(
                    **fallback_kwargs
                )
                response = _retry_transient_tts(
                    functools.partial(
                        client.synthesize_speech,
                        input=synthesis_input,
                        voice=fallback_voice,
                        audio_config=audio_config,
                    )
                )
                audio_content = response.audio_content
        phrase_seg = (
            AudioSegment.from_wav(io.BytesIO(audio_content))
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
    _unsupported_cloud_tts_models: set[str] = set()
    _genai_local = threading.local()

    def __init__(self) -> None:
        pass

    @classmethod
    def _mark_unsupported_cloud_tts_model(cls, model_name: str) -> None:
        with cls._lock:
            if model_name not in cls._unsupported_cloud_tts_models:
                cls._unsupported_cloud_tts_models.add(model_name)
                logging.info(
                    "Model '%s' is not supported by Cloud Text-to-Speech API; "
                    "routing synthesis via Vertex AI.",
                    model_name,
                )

    @classmethod
    def _get_genai_client(cls, project_id: str, credentials: Any) -> typing.Any:
        cache_key = (project_id, id(credentials))
        cached = getattr(cls._genai_local, "clients", None)
        if cached is None:
            cached = {}
            cls._genai_local.clients = cached
        if cache_key not in cached:
            cached[cache_key] = genai.Client(
                vertexai=True,
                project=project_id,
                location="global",
                credentials=credentials,
            )
        return cached[cache_key]

    def _synthesize_vertex_gemini_tts(
        self,
        text: str,
        credentials: Any,
        project_id: str,
        language_code: str,
        voice_name: str,
        model_name: str,
        target_sample_rate: int = AUDIO_SAMPLE_RATE_HZ,
    ) -> bytes:
        genai_client = self._get_genai_client(project_id, credentials)
        config = genai_types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=genai_types.SpeechConfig(
                language_code=language_code,
                voice_config=genai_types.VoiceConfig(
                    prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(
                        voice_name=voice_name
                    )
                ),
            ),
        )
        response = _retry_transient_tts(
            lambda: genai_client.models.generate_content(
                model=model_name,
                contents=text,
                config=config,
            )
        )
        return _extract_vertex_tts_wav_bytes(
            response, target_rate=target_sample_rate
        )

    @classmethod
    def _get_or_create_tts_client(
        cls, credentials: Any, project_id: str
    ) -> Any:
        if cls._client is None:
            with cls._lock:
                if cls._client is None:
                    client_options = ClientOptions(quota_project_id=project_id)
                    cls._client = texttospeech.TextToSpeechClient(
                        credentials=credentials, client_options=client_options
                    )
        return cls._client

    def _synthesize_cloud_tts(
        self,
        synthesis_input: Any,
        voice_kwargs: dict[str, Any],
        audio_config: Any,
        credentials: Any,
        project_id: str,
    ) -> bytes:
        client = self._get_or_create_tts_client(credentials, project_id)
        voice = texttospeech.VoiceSelectionParams(**voice_kwargs)
        try:
            response = _retry_transient_tts(
                lambda: client.synthesize_speech(
                    input=synthesis_input,
                    voice=voice,
                    audio_config=audio_config,
                )
            )
            return response.audio_content
        except Exception as e:
            if not _is_auth_error(e):
                raise
            with AudioTransformer._lock:
                if credentials is not None and hasattr(credentials, "refresh"):
                    try:
                        from google.auth.transport.requests import (  # noqa: PLC0415
                            Request as AuthRequest,
                        )

                        credentials.refresh(AuthRequest())
                    except Exception as refresh_err:
                        logging.debug(
                            f"Failed to refresh TTS credentials: {refresh_err}"
                        )
                client_opts = ClientOptions(quota_project_id=project_id)
                AudioTransformer._client = texttospeech.TextToSpeechClient(
                    credentials=credentials, client_options=client_opts
                )
                client = AudioTransformer._client
            response = _retry_transient_tts(
                lambda: client.synthesize_speech(
                    input=synthesis_input,
                    voice=voice,
                    audio_config=audio_config,
                )
            )
            return response.audio_content

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
        voice_config = voice_config or {}
        prompt = voice_config.get("prompt")
        has_prompt = bool(prompt and isinstance(prompt, str) and prompt.strip())

        language_code = (
            voice_config.get("language_code") or DEFAULT_TTS_LANGUAGE_CODE
        )
        voice_name = voice_config.get("voice_name") or DEFAULT_TTS_VOICE_NAME
        model_name = voice_config.get("model_name", DEFAULT_TTS_MODEL_NAME)

        can_route_vertex = (
            not has_prompt
            and bool(project_id)
            and _is_vertex_gemini_tts_model(model_name)
            and _can_use_vertex_tts(credentials)
        )

        try:
            audio_content: bytes | None = None
            if (
                can_route_vertex
                and model_name in AudioTransformer._unsupported_cloud_tts_models
            ):
                try:
                    audio_content = self._synthesize_vertex_gemini_tts(
                        text=text,
                        credentials=credentials,
                        project_id=project_id,
                        language_code=language_code,
                        voice_name=voice_name,
                        model_name=model_name,
                    )
                except Exception as vertex_err:
                    logging.debug(
                        "Vertex AI TTS failed for %s (%s); falling back to "
                        "Cloud TTS %s.",
                        model_name,
                        vertex_err,
                        _FALLBACK_CLOUD_TTS_MODEL_NAME,
                    )

            if audio_content is None:
                if has_prompt:
                    synthesis_input = texttospeech.SynthesisInput(
                        text=text, prompt=prompt.strip()
                    )
                else:
                    synthesis_input = texttospeech.SynthesisInput(text=text)

                effective_model = (
                    _FALLBACK_CLOUD_TTS_MODEL_NAME
                    if model_name
                    in AudioTransformer._unsupported_cloud_tts_models
                    else model_name
                )
                voice_kwargs: dict[str, Any] = {
                    "language_code": language_code,
                    "name": voice_name,
                }
                if effective_model:
                    voice_kwargs["model_name"] = effective_model

                audio_config = texttospeech.AudioConfig(
                    audio_encoding=texttospeech.AudioEncoding.LINEAR16,
                    sample_rate_hertz=16000,
                )
                try:
                    audio_content = self._synthesize_cloud_tts(
                        synthesis_input=synthesis_input,
                        voice_kwargs=voice_kwargs,
                        audio_config=audio_config,
                        credentials=credentials,
                        project_id=project_id,
                    )
                except Exception as e:
                    if "is not supported" not in str(e) or not effective_model:
                        raise
                    self._mark_unsupported_cloud_tts_model(effective_model)
                    if can_route_vertex:
                        try:
                            audio_content = self._synthesize_vertex_gemini_tts(
                                text=text,
                                credentials=credentials,
                                project_id=project_id,
                                language_code=language_code,
                                voice_name=voice_name,
                                model_name=model_name,
                            )
                        except Exception as vertex_err:
                            logging.debug(
                                "Vertex AI TTS fallback failed for %s: %s",
                                model_name,
                                vertex_err,
                            )
                    if audio_content is None:
                        fallback_kwargs = dict(voice_kwargs)
                        fallback_kwargs["model_name"] = (
                            _FALLBACK_CLOUD_TTS_MODEL_NAME
                        )
                        audio_content = self._synthesize_cloud_tts(
                            synthesis_input=synthesis_input,
                            voice_kwargs=fallback_kwargs,
                            audio_config=audio_config,
                            credentials=credentials,
                            project_id=project_id,
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
                        io.BytesIO(audio_content), format="wav"
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

            # The audio_content is a WAV file (RIFF header + data)
            # We need to strip the header to get raw PCM bytes.
            with io.BytesIO(audio_content) as wav_io:  # noqa: SIM117
                with wave.open(wav_io, "rb") as wav_file:
                    # Verify format if needed, but for now just read all frames
                    audio_bytes = wav_file.readframes(wav_file.getnframes())
                    return {"text": text, "audio_bytes": audio_bytes}
        except Exception as e:
            logging.debug(f"Error processing audio content: {e}")
            return {"text": text, "audio_bytes": None}
