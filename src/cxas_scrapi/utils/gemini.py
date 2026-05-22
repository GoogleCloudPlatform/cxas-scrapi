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
import threading
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
        model_name: str = "gemini-3.1-pro-preview",
        max_concurrent_requests: int = 3,
    ):
        """Initializes the GeminiGenerate client.

        Args:
            project_id: Google Cloud project ID.
            location: Vertex AI location. Defaults to 'global'.
            credentials: Optional Google Cloud credentials.
            model_name: The Gemini model name to use. Defaults to
              'gemini-3.1-pro-preview'.
            max_concurrent_requests: Limits the maximum number of simultaneous
              API calls to avoid 429 Quota Exhaustion.
        """
        self.model_name = model_name
        logger.info(
            f"Initializing GeminiGenerate with model: {self.model_name} "
            f"(Max Concurrency: {max_concurrent_requests})"
        )
        self.project_id = project_id
        self.location = location
        self.credentials = credentials
        self._thread_local = threading.local()
        self.semaphore = asyncio.Semaphore(max_concurrent_requests)

    @property
    def client(self) -> genai.Client:
        """Get or create a thread-local genai.Client instance."""
        if not hasattr(self._thread_local, "client"):
            self._thread_local.client = genai.Client(
                vertexai=True,
                project=self.project_id,
                location=self.location,
                credentials=self.credentials,
            )
        return self._thread_local.client

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model_name: Optional[str] = None,
        response_mime_type: Optional[str] = None,
        response_schema: Optional[Any] = None,
        temperature: Optional[float] = 1.0,
        thinking_level: Optional[str] = None,
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
            temperature: Optional temperature setting. Defaults to 1.0.
            thinking_level: Optional Vertex `ThinkingConfig` budget; one of
              "low" / "medium" / "high". `None` disables thinking entirely.

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
        if temperature is not None:
            config_args["temperature"] = temperature
        if thinking_level:
            config_args["thinking_config"] = genai.types.ThinkingConfig(
                thinking_level=thinking_level
            )

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

    def generate_with_parts(
        self,
        parts: list[Any],
        system_prompt: Optional[str] = None,
        model_name: Optional[str] = None,
        response_mime_type: Optional[str] = None,
        response_schema: Optional[Any] = None,
        temperature: Optional[float] = 1.0,
        thinking_level: Optional[str] = None,
    ) -> Optional[Any]:
        """Generates content from a list of multimodal Parts.

        Useful for audio analysis where one part is a `genai.types.Part`
        constructed via `from_uri(gs://..., mime_type='audio/wav')` or
        `from_bytes(data=..., mime_type=...)`, and another part is a text
        prompt.

        Args:
            parts: List of `genai.types.Part` (or strings — converted to text
              parts automatically).
            system_prompt: Optional system instruction.
            model_name: Optional override for the model name.
            response_mime_type: Optional MIME type (e.g. 'application/json').
            response_schema: Optional schema for structured output.
            temperature: Sampling temperature.
            thinking_level: Optional Vertex `ThinkingConfig` budget; one of
              "low" / "medium" / "high". `None` disables thinking entirely.
        """
        target_model = model_name or self.model_name

        contents = []
        for part in parts:
            if isinstance(part, str):
                contents.append(genai.types.Part.from_text(text=part))
            else:
                contents.append(part)

        config_args = {}
        if system_prompt:
            config_args["system_instruction"] = system_prompt
        if response_mime_type:
            config_args["response_mime_type"] = response_mime_type
        if response_schema:
            config_args["response_schema"] = response_schema
        if temperature is not None:
            config_args["temperature"] = temperature
        if thinking_level:
            config_args["thinking_config"] = genai.types.ThinkingConfig(
                thinking_level=thinking_level
            )

        config = None
        if config_args:
            config = genai.types.GenerateContentConfig(**config_args)

        try:
            response = self.client.models.generate_content(
                model=target_model, contents=contents, config=config
            )
            if response_mime_type == "application/json" and response_schema:
                return response.parsed
            return response.text
        except Exception as e:
            logger.error(f"Gemini multimodal generation failed: {e}")
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
        temperature: Optional[float] = 1.0,
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
            temperature: Optional temperature setting. Defaults to 1.0.

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
        if temperature is not None:
            config_args["temperature"] = temperature

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

    def generate_embeddings(
        self, contents: list[str], model_name: str = "gemini-embedding-001"
    ) -> list[list[float] | None]:
        """Generates embeddings using the Gemini model.

        Args:
            contents: The list of texts to be embedded.
            model_name: Optional override for the model name.

        Returns:
            List of the generated embeddings.
        """
        target_model = model_name

        try:
            response = self.client.models.embed_content(
                model=target_model, contents=contents
            )
            if response.embeddings is not None:
                return [embedding.values for embedding in response.embeddings]
        except Exception as e:
            logger.error(f"Gemini embedding generation failed: {e}")
        return []
