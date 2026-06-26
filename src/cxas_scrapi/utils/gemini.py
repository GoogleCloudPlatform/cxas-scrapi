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
import time
from collections.abc import Callable
from typing import Any

import httpx
import requests
from google import genai
from google.genai import errors

logger = logging.getLogger(__name__)


class GeminiError(Exception):
    """Base exception for all Gemini wrapper operations."""

    def __init__(
        self, message: str, original_exception: Exception | None = None
    ):
        super().__init__(message)
        self.original_exception = original_exception


class GeminiGenerationError(GeminiError):
    """Raised when text or multimodal generation fails."""

    pass


class GeminiEmbeddingError(GeminiError):
    """Raised when generating embeddings fails."""

    pass


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

    def _is_transient_error(self, e: Exception) -> bool:
        """Determines if an API or network error is transient.

        Args:
            e: The exception to classify.

        Returns:
            True if transient and retriable, False otherwise.
        """
        match e:
            case errors.ServerError():
                return True
            case errors.ClientError(code=429 | 408):
                return True
            case httpx.HTTPError() | requests.RequestException():
                # Direct transport exceptions (timeouts, connection drops)
                # are transient.
                return True
            case _:
                return False

    def _execute_with_retry(
        self,
        func: Callable[[], Any],
        error_wrapper_class: type[GeminiError],
        max_retries: int = 5,
        base_delay_seconds: float = 2.0,
    ) -> Any:
        """Executes a synchronous operation with exponential backoff retries.

        Args:
            func: The function to execute.
            error_wrapper_class: The custom exception type to raise on failure.
            max_retries: Maximum number of retries.
            base_delay_seconds: Base delay for backoff.

        Returns:
            The return value of func().

        Raises:
            error_wrapper_class: If all retries fail or a permanent error
              occurs.
        """
        for attempt in range(max_retries):
            try:
                return func()
            except Exception as e:
                if not self._is_transient_error(e):
                    raise error_wrapper_class(
                        f"Permanent error: {e}", original_exception=e
                    ) from e

                logger.warning(f"Transient error on attempt {attempt + 1}: {e}")
                if attempt == max_retries - 1:
                    raise error_wrapper_class(
                        f"All {max_retries} retry attempts failed.",
                        original_exception=e,
                    ) from e

            # Jittered exponential backoff
            sleep_time = (base_delay_seconds * (1.5**attempt)) + random.uniform(
                0, 1
            )
            logger.info(f"Sleeping for {sleep_time:.1f}s before retry...")
            time.sleep(sleep_time)

    def _build_generation_config(
        self,
        system_prompt: str | None = None,
        response_mime_type: str | None = None,
        response_schema: Any | None = None,
        temperature: float | None = 1.0,
        thinking_level: str | None = None,
    ) -> genai.types.GenerateContentConfig | None:
        """Helper to construct GenerateContentConfig for the GenAI SDK."""
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

        if config_args:
            return genai.types.GenerateContentConfig(**config_args)
        return None

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model_name: str | None = None,
        response_mime_type: str | None = None,
        response_schema: Any | None = None,
        temperature: float | None = 1.0,
        thinking_level: str | None = None,
        max_retries: int = 5,
        base_delay_seconds: float = 2.0,
    ) -> Any:
        """Generates content using the Gemini model.

        Args:
            prompt: The user prompt.
            system_prompt: Optional system prompt/instruction.
            model_name: Optional override for the model name.
            response_mime_type: Optional MIME type (e.g. 'application/json').
            response_schema: Optional Pydantic model or schema.
            temperature: Optional temperature setting. Defaults to 1.0.
            thinking_level: Optional budget: "low"/"medium"/"high".
            max_retries: Maximum number of retries for transient errors.
            base_delay_seconds: Base delay for backoff.

        Returns:
            The generated text response or parsed object.

        Raises:
            GeminiGenerationError: If the generation fails.
        """
        target_model = model_name or self.model_name

        config = self._build_generation_config(
            system_prompt=system_prompt,
            response_mime_type=response_mime_type,
            response_schema=response_schema,
            temperature=temperature,
            thinking_level=thinking_level,
        )

        def _call():
            response = self.client.models.generate_content(
                model=target_model, contents=prompt, config=config
            )
            if response_mime_type == "application/json" and response_schema:
                return response.parsed
            return response.text

        return self._execute_with_retry(
            _call,
            GeminiGenerationError,
            max_retries=max_retries,
            base_delay_seconds=base_delay_seconds,
        )

    def generate_with_parts(
        self,
        parts: list[Any],
        system_prompt: str | None = None,
        model_name: str | None = None,
        response_mime_type: str | None = None,
        response_schema: Any | None = None,
        temperature: float | None = 1.0,
        thinking_level: str | None = None,
        max_retries: int = 5,
        base_delay_seconds: float = 2.0,
    ) -> Any:
        """Generates content from a list of multimodal Parts.

        Useful for audio analysis where one part is a `genai.types.Part`
        constructed via `from_uri` or `from_bytes`, and another part is a text
        prompt.

        Args:
            parts: List of `genai.types.Part` or strings.
            system_prompt: Optional system instruction.
            model_name: Optional override for the model name.
            response_mime_type: Optional MIME type (e.g. 'application/json').
            response_schema: Optional schema for structured output.
            temperature: Sampling temperature.
            thinking_level: Optional budget: "low"/"medium"/"high".
            max_retries: Maximum number of retries for transient errors.
            base_delay_seconds: Base delay for backoff.

        Returns:
            The generated text response or parsed object.

        Raises:
            GeminiGenerationError: If the generation fails.
        """
        target_model = model_name or self.model_name

        contents = []
        for part in parts:
            if isinstance(part, str):
                contents.append(genai.types.Part.from_text(text=part))
            else:
                contents.append(part)

        config = self._build_generation_config(
            system_prompt=system_prompt,
            response_mime_type=response_mime_type,
            response_schema=response_schema,
            temperature=temperature,
            thinking_level=thinking_level,
        )

        def _call():
            response = self.client.models.generate_content(
                model=target_model, contents=contents, config=config
            )
            if response_mime_type == "application/json" and response_schema:
                return response.parsed
            return response.text

        return self._execute_with_retry(
            _call,
            GeminiGenerationError,
            max_retries=max_retries,
            base_delay_seconds=base_delay_seconds,
        )

    async def create_cache(
        self,
        system_prompt: str,
        shared_content: str,
        ttl_seconds: int = 300,
    ) -> str | None:
        """Creates a Gemini context cache for shared prompt content.

        Returns the cache resource name on success, or None if the API call
        fails (e.g. content below the minimum token threshold). Callers should
        treat None as a signal to fall back to uncached generation.
        """
        try:
            cache = await self.client.aio.caches.create(
                model=self.model_name,
                config={
                    "system_instruction": system_prompt,
                    "contents": [
                        genai.types.Content(
                            role="user",
                            parts=[
                                genai.types.Part.from_text(text=shared_content)
                            ],
                        )
                    ],
                    "ttl": f"{ttl_seconds}s",
                },
            )
            logger.info("Created Gemini context cache: %s", cache.name)
            return cache.name
        except Exception as exc:
            logger.warning(
                "Cache creation failed (will proceed uncached): %s", exc
            )
            return None

    async def delete_cache(self, cache_name: str) -> None:
        """Deletes a Gemini context cache by resource name."""
        try:
            await self.client.aio.caches.delete(name=cache_name)
            logger.info("Deleted Gemini context cache: %s", cache_name)
        except Exception as exc:
            logger.warning("Cache deletion failed for %s: %s", cache_name, exc)

    async def generate_async(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model_name: str | None = None,
        response_mime_type: str | None = None,
        response_schema: Any | None = None,
        max_retries: int = 5,
        base_delay_seconds: int = 10,
        temperature: float | None = 1.0,
        cached_content_name: str | None = None,
    ) -> Any:
        """Generates content asynchronously using the Gemini model.

        Args:
            prompt: The user prompt (per-call content only when using a cache).
            system_prompt: Optional system prompt/instruction. Ignored when
              cached_content_name is provided (system prompt lives in cache).
            model_name: Optional override for the model name.
            response_mime_type: Optional MIME type (e.g. 'application/json').
            response_schema: Optional Pydantic model or schema.
            max_retries: Maximum number of retries for transient errors.
            base_delay_seconds: Base delay for exponential backoff.
            temperature: Optional temperature setting. Defaults to 1.0.
            cached_content_name: Optional resource name returned by
              create_cache().

        Returns:
            The generated text response or parsed object.

        Raises:
            GeminiGenerationError: If the generation fails.
        """
        target_model = model_name or self.model_name

        if cached_content_name:
            config_args: dict = {"cached_content": cached_content_name}
            if temperature is not None:
                config_args["temperature"] = temperature
            config = genai.types.GenerateContentConfig(**config_args)
        else:
            config = self._build_generation_config(
                system_prompt=system_prompt,
                response_mime_type=response_mime_type,
                response_schema=response_schema,
                temperature=temperature,
            )

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
                if not self._is_transient_error(e):
                    raise GeminiGenerationError(
                        f"Permanent error: {e}", original_exception=e
                    ) from e

                logger.warning(
                    f"  Attempt {attempt + 1} failed: {type(e).__name__}: {e}"
                )

                if attempt == max_retries - 1:
                    logger.error("  ❌ All retry attempts failed.")
                    raise GeminiGenerationError(
                        f"All {max_retries} retry attempts failed.",
                        original_exception=e,
                    ) from e

            # EXPONENTIAL BACKOFF WITH JITTER
            sleep_time = (base_delay_seconds * (1.5**attempt)) + random.uniform(
                0, 3
            )
            logger.info(
                f"    ⏳ Sleeping for {sleep_time:.1f}s before retry..."
            )
            await asyncio.sleep(sleep_time)

    def generate_embeddings(
        self,
        contents: list[str],
        model_name: str = "gemini-embedding-001",
        max_retries: int = 5,
        base_delay_seconds: float = 2.0,
    ) -> list[list[float]]:
        """Generates embeddings using the Gemini model.

        Args:
            contents: The list of texts to be embedded.
            model_name: Optional override for the model name.
            max_retries: Maximum number of retries for transient errors.
            base_delay_seconds: Base delay for backoff.

        Returns:
            List of the generated embeddings.

        Raises:
            GeminiEmbeddingError: If the embedding generation fails.
        """
        target_model = model_name

        def _call():
            response = self.client.models.embed_content(
                model=target_model, contents=contents
            )
            if response.embeddings is not None:
                return [embedding.values for embedding in response.embeddings]
            return []

        return self._execute_with_retry(
            _call,
            GeminiEmbeddingError,
            max_retries=max_retries,
            base_delay_seconds=base_delay_seconds,
        )
