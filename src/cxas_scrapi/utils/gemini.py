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

import asyncio
import logging
import random
from typing import Any, Optional

from google import genai

logger = logging.getLogger(__name__)


class GeminiGenerate:
    """A wrapper for the Gemini client to generate content."""

    def __init__(
        self,
        project_id: str,
        location: str = "global",
        credentials=None,
        model_name: str = "gemini-3.1-flash-lite-preview",
        max_concurrent_requests: int = 2,
    ):
        """Initializes the GeminiGenerate client.

        Args:
            project_id: Google Cloud project ID.
            location: Vertex AI location. Defaults to 'global'.
            credentials: Optional Google Cloud credentials.
            model_name: The Gemini model name to use. Defaults to
              'gemini-3.1-flash-lite-preview'.
            max_concurrent_requests: Limits the maximum number of simultaneous
              API calls to avoid 429 Quota Exhaustion.
        """
        self.model_name = model_name
        logger.info(
            f"Initializing GeminiGenerate with model: {self.model_name} "
            f"(Max Concurrency: {max_concurrent_requests})"
        )
        self.client = genai.Client(
            vertexai=True,
            project=project_id,
            location=location,
            credentials=credentials,
        )
        self.semaphore = asyncio.Semaphore(max_concurrent_requests)

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model_name: Optional[str] = None,
        response_mime_type: Optional[str] = None,
        response_schema: Optional[Any] = None,
    ) -> Optional[Any]:
        """Generates content using the Gemini model.

        Args:
            prompt: The user prompt.
            system_prompt: Optional system prompt/instruction.
            model_name: Optional override for the model name.
            response_mime_type: Optional MIME type for the response (e.g.,
              'application/json').
            response_schema: Optional Pydantic model or schema for structured
              output.

        Returns:
            The generated text response or parsed object, or None on failure.
        """
        target_model = model_name or self.model_name

        config_args = {}
        if system_prompt:
            config_args["system_instruction"] = system_prompt
        if response_mime_type:
            config_args["response_mime_type"] = response_mime_type
        if response_schema:
            config_args["response_schema"] = response_schema

        config = None
        if config_args:
            config = genai.types.GenerateContentConfig(**config_args)

        try:
            response = self.client.models.generate_content(
                model=target_model, contents=prompt, config=config
            )

            if response_mime_type == "application/json" and response_schema:
                return response.parsed
            return response.text
        except Exception as e:
            logger.error(f"Gemini generation failed: {e}")
            return None

    async def generate_async(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model_name: Optional[str] = None,
        response_mime_type: Optional[str] = None,
        response_schema: Optional[Any] = None,
        max_retries: int = 5,
        base_delay_seconds: int = 10,
    ) -> Optional[Any]:
        """Generates content asynchronously using the Gemini model.

        Args:
            prompt: The user prompt.
            system_prompt: Optional system prompt/instruction.
            model_name: Optional override for the model name.
            response_mime_type: Optional MIME type for the response (e.g.,
              'application/json').
            response_schema: Optional Pydantic model or schema for structured
              output.
            max_retries: Maximum number of retries for transient errors.
            base_delay_seconds: Base delay for exponential backoff.

        Returns:
            The generated text response or parsed object, or None on failure.
        """
        target_model = model_name or self.model_name

        config_args = {}
        if system_prompt:
            config_args["system_instruction"] = system_prompt
        if response_mime_type:
            config_args["response_mime_type"] = response_mime_type
        if response_schema:
            config_args["response_schema"] = response_schema

        config = None
        if config_args:
            config = genai.types.GenerateContentConfig(**config_args)

        for attempt in range(max_retries):
            try:
                # ACQUIRE SEMAPHORE: Wait if too many requests are running
                async with self.semaphore:
                    response = await self.client.aio.models.generate_content(
                        model=target_model, contents=prompt, config=config
                    )

                if response_mime_type == "application/json" and response_schema:
                    return response.parsed
                return response.text

            except Exception as e:
                is_quota = "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)
                err_msg = (
                    "Quota/Rate Limit Exhausted"
                    if is_quota
                    else f"{type(e).__name__}: {e}"
                )

                logger.warning(f"  Attempt {attempt + 1} failed: {err_msg}")

                if attempt == max_retries - 1:
                    logger.error(
                        "  ❌ All retry attempts failed. Check GCP quota."
                    )
                    return None

            # EXPONENTIAL BACKOFF WITH JITTER
            sleep_time = (base_delay_seconds * (1.5**attempt)) + random.uniform(
                0, 3
            )
            logger.info(
                f"    ⏳ Sleeping for {sleep_time:.1f}s before retry..."
            )
            await asyncio.sleep(sleep_time)

        return None
