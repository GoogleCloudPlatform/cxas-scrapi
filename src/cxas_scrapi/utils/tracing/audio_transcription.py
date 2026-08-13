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

"""Audio transcription and Word Error Rate (WER) utilities for CXAS traces.

Provides:
- Exact word-level Word Error Rate (WER) calculation with Levenshtein alignment.
- Multilingual and non-English character detection.
- Speech-to-text transcription powered by Gemini Flash / Flash-Lite models.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from google import genai

from cxas_scrapi.utils.gemini import GeminiGenerate

logger = logging.getLogger(__name__)

DEFAULT_TRANSCRIPTION_MODEL = "gemini-2.5-flash"

DEFAULT_TRANSCRIPTION_PROMPT = (
    "Transcribe the speech in this audio recording accurately and verbatim.\n"
    "- Output ONLY the exact spoken words.\n"
    "- Do NOT add any preamble, explanation, notes, timestamps, or markdown"
    " formatting.\n"
    "- If there is no speech or only silence/background noise, output an"
    " empty response."
)


def normalize_text(text: str) -> list[str]:
    """Normalizes text and tokenizes it into a list of word tokens.

    Applies Unicode NFKC normalization, lowercase folding, and splits into
    alphanumeric/word tokens while preserving international/unicode characters
    and apostrophes within words (e.g. "don't", "it's").

    Args:
        text: The raw input string.

    Returns:
        List of normalized word tokens.
    """
    if not text:
        return []
    # Normalize unicode representations
    normalized = unicodedata.normalize("NFKC", text).lower()
    # Find all words, including non-ASCII letters and numbers
    # Preserves unicode letters (e.g. accented Latin, Cyrillic, CJK, etc.)
    # and words containing internal apostrophes (e.g. don't, it's)
    words = re.findall(
        r"[\w\u00C0-\u024F\u1E00-\u1EFF\u0400-\u04FF\u4E00-\u9FFF]+(?:'[\w\u00C0-\u024F\u1E00-\u1EFF\u0400-\u04FF\u4E00-\u9FFF]+)?",
        normalized,
    )
    return words


def contains_non_english(text: str) -> bool:
    """Checks if text contains non-English / non-ASCII characters.

    Detects characters with code point > 127 (such as accented characters,
    emojis, or non-Latin alphabets) which indicate non-English speech or
    transcription anomalies.

    Args:
        text: The text to inspect.

    Returns:
        True if any character is non-ASCII (code point > 127).
    """
    if not text:
        return False
    return any(ord(char) > 127 for char in text)


def calculate_wer(
    reference: str,
    hypothesis: str,
    normalize: bool = True,
) -> dict[str, Any]:
    """Calculates Word Error Rate (WER) between reference and hypothesis text.

    Computes standard word-level Levenshtein edit distance:
        WER = (Substitutions + Deletions + Insertions) / Reference_Word_Count

    Args:
        reference: Ground truth / baseline transcript (e.g. CES transcription).
        hypothesis: Predicted / generated transcript (e.g. Gemini
          transcription).
        normalize: Whether to normalize case, punctuation, and whitespace
          before computing WER. Defaults to True.

    Returns:
        Dictionary containing:
          - wer: float (rounded to 4 decimal places)
          - substitutions: int
          - deletions: int
          - insertions: int
          - hits: int (matching words)
          - reference_words: int
          - hypothesis_words: int
          - reference_tokens: list[str]
          - hypothesis_tokens: list[str]
    """
    if normalize:
        ref_words = normalize_text(reference)
        hyp_words = normalize_text(hypothesis)
    else:
        ref_words = reference.split() if reference else []
        hyp_words = hypothesis.split() if hypothesis else []

    n = len(ref_words)
    m = len(hyp_words)

    if n == 0:
        if m == 0:
            return {
                "wer": 0.0,
                "substitutions": 0,
                "deletions": 0,
                "insertions": 0,
                "hits": 0,
                "reference_words": 0,
                "hypothesis_words": 0,
                "reference_tokens": ref_words,
                "hypothesis_tokens": hyp_words,
            }
        return {
            "wer": 1.0,
            "substitutions": 0,
            "deletions": 0,
            "insertions": m,
            "hits": 0,
            "reference_words": 0,
            "hypothesis_words": m,
            "reference_tokens": ref_words,
            "hypothesis_tokens": hyp_words,
        }

    # Dynamic Programming Matrix for Levenshtein Distance
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref_words[i - 1] == hyp_words[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = min(
                    dp[i - 1][j - 1] + 1,  # substitution
                    dp[i - 1][j] + 1,  # deletion
                    dp[i][j - 1] + 1,  # insertion
                )

    # Backtrack to count operations
    i, j = n, m
    substitutions = 0
    deletions = 0
    insertions = 0
    hits = 0

    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref_words[i - 1] == hyp_words[j - 1]:
            hits += 1
            i -= 1
            j -= 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            substitutions += 1
            i -= 1
            j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            deletions += 1
            i -= 1
        elif j > 0 and dp[i][j] == dp[i][j - 1] + 1:
            insertions += 1
            j -= 1
        elif i > 0 and j > 0:
            substitutions += 1
            i -= 1
            j -= 1
        elif i > 0:
            deletions += 1
            i -= 1
        else:
            insertions += 1
            j -= 1

    total_errors = substitutions + deletions + insertions
    wer = round(total_errors / n, 4)

    return {
        "wer": wer,
        "substitutions": substitutions,
        "deletions": deletions,
        "insertions": insertions,
        "hits": hits,
        "reference_words": n,
        "hypothesis_words": m,
        "reference_tokens": ref_words,
        "hypothesis_tokens": hyp_words,
    }


class AudioTranscriber:
    """Handles audio transcription using Gemini Flash / Flash-Lite models and

    computes transcription accuracy metrics.
    """

    def __init__(
        self,
        project_id: str,
        credentials: Any = None,
        model_name: str = DEFAULT_TRANSCRIPTION_MODEL,
        location: str = "global",
        **kwargs: Any,
    ) -> None:
        """Initializes the AudioTranscriber.

        Args:
            project_id: Google Cloud project ID.
            credentials: Optional credentials object.
            model_name: Gemini model name (default: gemini-2.5-flash).
            location: Vertex AI location.
            **kwargs: Additional keyword arguments.
        """
        self.project_id = project_id
        self.credentials = credentials
        self.model_name = model_name
        self.location = location
        self.gemini = GeminiGenerate(
            project_id=self.project_id,
            credentials=self.credentials,
            model_name=self.model_name,
            location=self.location,
            **kwargs,
        )

    def transcribe(
        self,
        audio_source: str | bytes,
        mime_type: str = "audio/wav",
        prompt: str | None = None,
    ) -> str:
        """Transcribes audio from a GCS URI, local file path, or raw bytes.

        Args:
            audio_source: GCS URI ('gs://...'), local file path, or raw audio
              bytes.
            mime_type: MIME type of audio (default: 'audio/wav').
            prompt: Optional custom prompt instruction for Gemini.

        Returns:
            Transcribed text string.
        """
        system_instruction = prompt or DEFAULT_TRANSCRIPTION_PROMPT

        if isinstance(audio_source, str) and audio_source.startswith("gs://"):
            part = genai.types.Part.from_uri(
                file_uri=audio_source, mime_type=mime_type
            )
            response = self.gemini.generate_with_parts(
                parts=[part, system_instruction],
                model_name=self.model_name,
                temperature=0.0,
            )
        elif isinstance(audio_source, bytes):
            part = genai.types.Part.from_bytes(
                data=audio_source, mime_type=mime_type
            )
            response = self.gemini.generate_with_parts(
                parts=[part, system_instruction],
                model_name=self.model_name,
                temperature=0.0,
            )
        elif isinstance(audio_source, str):
            with open(audio_source, "rb") as f:
                audio_bytes = f.read()
            part = genai.types.Part.from_bytes(
                data=audio_bytes, mime_type=mime_type
            )
            response = self.gemini.generate_with_parts(
                parts=[part, system_instruction],
                model_name=self.model_name,
                temperature=0.0,
            )
        else:
            raise TypeError(
                f"Unsupported audio source type: {type(audio_source)}"
            )

        if response is None:
            return ""

        text = response if isinstance(response, str) else str(response)
        # Strip potential wrapping markdown code fences or quotes if any
        cleaned = text.strip()
        if cleaned.startswith("```") and cleaned.endswith("```"):
            lines = cleaned.splitlines()
            if len(lines) >= 2:
                cleaned = "\n".join(lines[1:-1]).strip()
        if (
            (cleaned.startswith('"') and cleaned.endswith('"'))
            or (cleaned.startswith("'") and cleaned.endswith("'"))
        ) and len(cleaned) >= 2:
            cleaned = cleaned[1:-1].strip()

        return cleaned

    def evaluate_turn(
        self,
        reference_transcript: str,
        audio_source: str | bytes,
        turn_index: int | None = None,
        conversation_id: str | None = None,
        prompt: str | None = None,
    ) -> dict[str, Any]:
        """Transcribes audio for a turn and calculates WER against reference.

        Args:
            reference_transcript: The original CES transcript.
            audio_source: GCS URI or bytes of the audio.
            turn_index: Optional turn index.
            conversation_id: Optional conversation ID.
            prompt: Optional custom prompt.

        Returns:
            Dictionary with transcription and WER evaluation breakdown.
        """
        gemini_transcript = self.transcribe(audio_source, prompt=prompt)
        wer_metrics = calculate_wer(
            reference=reference_transcript or "",
            hypothesis=gemini_transcript,
            normalize=True,
        )

        return {
            "conversation_id": conversation_id,
            "turn_index": turn_index,
            "audio_source": (
                audio_source if isinstance(audio_source, str) else "<bytes>"
            ),
            "ces_transcript": reference_transcript or "",
            "gemini_transcript": gemini_transcript,
            "contains_non_english": contains_non_english(
                reference_transcript or ""
            ),
            **wer_metrics,
        }
