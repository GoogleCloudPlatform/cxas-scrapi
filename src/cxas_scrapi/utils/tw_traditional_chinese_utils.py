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

"""Utility class for Simplified Chinese detection and Taiwan Traditional Chinese conversion."""

import functools
import logging
from typing import Any

import opencc

from cxas_scrapi.utils.callback_libs import CallbackContext, LlmResponse

logger = logging.getLogger(__name__)


class TWTraditionalChineseUtils:
    """Utility class providing low-latency Simplified to Taiwan Traditional Chinese conversion."""

    CONFIG_S2TWP = "s2twp.json"

    def __init__(self, config: str = CONFIG_S2TWP) -> None:
        """Initializes the TWTraditionalChineseUtils with an OpenCC configuration.

        Args:
            config: The OpenCC configuration file name. Defaults to 's2twp.json'
                (Simplified Chinese to Taiwan Traditional with phrase/idiom conversion).
        """
        self.config = config
        self._converter = self._get_cached_converter(config)

    @staticmethod
    @functools.lru_cache(maxsize=4)
    def _get_cached_converter(config: str) -> opencc.OpenCC:
        """Returns a cached OpenCC instance to avoid dictionary re-loading latency."""
        return opencc.OpenCC(config)

    @property
    def converter(self) -> opencc.OpenCC:
        """Returns the underlying OpenCC converter instance."""
        return self._converter

    @staticmethod
    def contains_cjk(text: str) -> bool:
        """Fast check to determine if the string contains any CJK ideographs.

        Allows instant early-exit for non-Chinese text (ASCII, numbers, Latin, etc.)
        with near-zero latency overhead.

        Args:
            text: The input text to inspect.

        Returns:
            True if any CJK characters are found, False otherwise.
        """
        if not text:
            return False
        return any(
            "\u4e00" <= ch <= "\u9fff"
            or "\u3400" <= ch <= "\u4dbf"
            or "\uf900" <= ch <= "\ufaff"
            for ch in text
        )

    def convert(self, text: str) -> tuple[str, bool]:
        """Converts Simplified Chinese text to Taiwan Traditional Chinese (s2twp).

        Executes a single-pass conversion. If the converted text differs from the
        original string, Simplified Chinese / non-Taiwanese phrasing was detected.

        Args:
            text: Input string to convert.

        Returns:
            A tuple of (converted_text, has_simplified).
        """
        if not text or not self.contains_cjk(text):
            return text, False

        converted = self._converter.convert(text)
        has_simplified = converted != text
        return converted, has_simplified

    def convert_text(self, text: str) -> str:
        """Converts input text to Taiwan Traditional Chinese, returning only the text.

        Args:
            text: Input string to convert.

        Returns:
            The converted Taiwan Traditional Chinese text.
        """
        converted, _ = self.convert(text)
        return converted

    def has_simplified_chinese(self, text: str) -> bool:
        """Checks if the input text contains Simplified Chinese characters or vocabulary.

        Args:
            text: Input string to inspect.

        Returns:
            True if Simplified Chinese was detected, False otherwise.
        """
        _, has_simplified = self.convert(text)
        return has_simplified

    def process_llm_response(
        self,
        llm_response: LlmResponse,
        context: CallbackContext | None = None,
        state_key: str = "simplified_chinese_detected",
    ) -> LlmResponse:
        """Post-LLM callback handler that converts LLM output text parts to Taiwan Traditional Chinese.

        Iterates over all parts in the LLM response, converting text parts in-place
        while preserving all tool calls, inline data, and response metadata.

        Args:
            llm_response: The LlmResponse object produced by the model.
            context: Optional CallbackContext to record state variables.
            state_key: The state variable name to flag if Simplified Chinese was found.

        Returns:
            The updated LlmResponse with Taiwan Traditional Chinese text.
        """
        if not llm_response.content or not llm_response.content.parts:
            return llm_response

        simplified_detected = False

        for part in llm_response.content.parts:
            if part.text:
                converted_text, was_modified = self.convert(part.text)
                if was_modified:
                    simplified_detected = True
                    part.text = converted_text

        if simplified_detected and context is not None:
            context.set_variable(state_key, True)

        return llm_response


# Global singleton instance for immediate low-latency reuse
default_tw_converter = TWTraditionalChineseUtils()


def after_model_chinese_convert(
    context: CallbackContext, llm_response: LlmResponse
) -> LlmResponse:
    """Standard post-LLM callback function compatible with CES after_model_callbacks.

    Args:
        context: The session callback context.
        llm_response: The model output response.

    Returns:
        The transformed LlmResponse in Taiwan Traditional Chinese.
    """
    return default_tw_converter.process_llm_response(
        llm_response=llm_response, context=context
    )
