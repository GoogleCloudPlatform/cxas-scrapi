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
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import requests
from google.genai.errors import ClientError, ServerError

from cxas_scrapi.utils.gemini import (
    GeminiEmbeddingError,
    GeminiGenerate,
    GeminiGenerationError,
)


@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_with_parts_text_response(mock_genai):
    mock_genai.types.Part.from_text = lambda text: f"text:{text}"
    mock_genai.types.GenerateContentConfig = MagicMock()
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    fake_client.models.generate_content.return_value = SimpleNamespace(
        text="reply"
    )

    gen = GeminiGenerate(project_id="p", credentials=None)
    out = gen.generate_with_parts(
        parts=["a prompt", SimpleNamespace(name="audio_part")],
        system_prompt="sys",
        temperature=0.5,
    )
    assert out == "reply"
    _args, kwargs = fake_client.models.generate_content.call_args
    contents = kwargs["contents"]
    assert contents[0] == "text:a prompt"
    assert hasattr(contents[1], "name")


@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_with_parts_returns_parsed_for_json_schema(mock_genai):
    mock_genai.types.Part.from_text = lambda text: f"t:{text}"
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    fake_client.models.generate_content.return_value = SimpleNamespace(
        parsed={"k": "v"}, text=None
    )

    gen = GeminiGenerate(project_id="p")
    out = gen.generate_with_parts(
        parts=["x"],
        response_mime_type="application/json",
        response_schema=object,
        model_name="custom-model",
    )
    assert out == {"k": "v"}


@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_with_parts_raises_on_failure(mock_genai):
    mock_genai.types.Part.from_text = lambda text: text
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    original_exc = RuntimeError("boom")
    fake_client.models.generate_content.side_effect = original_exc

    gen = GeminiGenerate(project_id="p")
    with pytest.raises(GeminiGenerationError) as exc_info:
        gen.generate_with_parts(parts=["x"])
    assert "Permanent error: boom" in str(exc_info.value)
    assert exc_info.value.original_exception is original_exc


@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_with_parts_no_config_when_no_args(mock_genai):
    mock_genai.types.Part.from_text = lambda text: text
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    fake_client.models.generate_content.return_value = SimpleNamespace(
        text="ok"
    )
    gen = GeminiGenerate(project_id="p")
    out = gen.generate_with_parts(parts=["q"], temperature=None)
    assert out == "ok"
    _, kwargs = fake_client.models.generate_content.call_args
    assert kwargs["config"] is None


@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_text_response(mock_genai):
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    fake_client.models.generate_content.return_value = SimpleNamespace(
        text="hi"
    )
    gen = GeminiGenerate(project_id="p")
    assert gen.generate(prompt="p", system_prompt="s") == "hi"


@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_returns_parsed_for_json_schema(mock_genai):
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    fake_client.models.generate_content.return_value = SimpleNamespace(
        parsed={"k": "v"}, text=None
    )
    gen = GeminiGenerate(project_id="p")
    out = gen.generate(
        prompt="p",
        response_mime_type="application/json",
        response_schema=object,
        model_name="custom",
    )
    assert out == {"k": "v"}


@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_raises_on_failure(mock_genai):
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    original_exc = RuntimeError("boom")
    fake_client.models.generate_content.side_effect = original_exc
    gen = GeminiGenerate(project_id="p")
    with pytest.raises(GeminiGenerationError) as exc_info:
        gen.generate(prompt="p")
    assert "Permanent error: boom" in str(exc_info.value)
    assert exc_info.value.original_exception is original_exc


@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_no_config_when_no_args(mock_genai):
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    fake_client.models.generate_content.return_value = SimpleNamespace(
        text="ok"
    )
    gen = GeminiGenerate(project_id="p")
    assert gen.generate(prompt="p", temperature=None) == "ok"
    _, kwargs = fake_client.models.generate_content.call_args
    assert kwargs["config"] is None


@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_passes_thinking_level(mock_genai):
    """thinking_level='low' wraps a ThinkingConfig and forwards it."""
    sentinel_thinking = MagicMock(name="ThinkingConfig")
    mock_genai.types.ThinkingConfig.return_value = sentinel_thinking
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    fake_client.models.generate_content.return_value = SimpleNamespace(
        text="ok"
    )
    gen = GeminiGenerate(project_id="p")
    gen.generate(prompt="p", thinking_level="low")
    mock_genai.types.ThinkingConfig.assert_called_once_with(
        thinking_level="low"
    )
    _, kwargs = mock_genai.types.GenerateContentConfig.call_args
    assert kwargs["thinking_config"] is sentinel_thinking


@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_with_parts_passes_thinking_level(mock_genai):
    sentinel_thinking = MagicMock(name="ThinkingConfig")
    mock_genai.types.ThinkingConfig.return_value = sentinel_thinking
    mock_genai.types.Part.from_text = lambda text: text
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    fake_client.models.generate_content.return_value = SimpleNamespace(
        text="ok"
    )
    gen = GeminiGenerate(project_id="p")
    gen.generate_with_parts(parts=["q"], thinking_level="medium")
    mock_genai.types.ThinkingConfig.assert_called_once_with(
        thinking_level="medium"
    )
    _, kwargs = mock_genai.types.GenerateContentConfig.call_args
    assert kwargs["thinking_config"] is sentinel_thinking


@patch("cxas_scrapi.utils.gemini.asyncio.sleep", new=AsyncMock())
@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_async_success_with_schema(mock_genai):
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    fake_client.aio.models.generate_content = AsyncMock(
        return_value=SimpleNamespace(parsed={"k": "v"}, text=None)
    )
    gen = GeminiGenerate(project_id="p", max_concurrent_requests=1)
    res = asyncio.run(
        gen.generate_async(
            prompt="x",
            system_prompt="s",
            response_mime_type="application/json",
            response_schema=object,
        )
    )
    assert res == {"k": "v"}


@patch("cxas_scrapi.utils.gemini.asyncio.sleep", new=AsyncMock())
@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_async_returns_text(mock_genai):
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    fake_client.aio.models.generate_content = AsyncMock(
        return_value=SimpleNamespace(text="resp")
    )
    gen = GeminiGenerate(project_id="p", max_concurrent_requests=1)
    res = asyncio.run(gen.generate_async(prompt="x", temperature=None))
    assert res == "resp"


@patch("cxas_scrapi.utils.gemini.asyncio.sleep", new=AsyncMock())
@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_async_quota_then_success(mock_genai):
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    quota = ClientError(429, {})
    fake_client.aio.models.generate_content = AsyncMock(
        side_effect=[quota, SimpleNamespace(text="ok")]
    )
    gen = GeminiGenerate(project_id="p", max_concurrent_requests=1)
    res = asyncio.run(
        gen.generate_async(prompt="x", max_retries=3, base_delay_seconds=0)
    )
    assert res == "ok"
    assert fake_client.aio.models.generate_content.call_count == 2


@patch("cxas_scrapi.utils.gemini.asyncio.sleep", new=AsyncMock())
@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_async_all_retries_fail(mock_genai):
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    quota = ClientError(429, {})
    fake_client.aio.models.generate_content = AsyncMock(side_effect=quota)
    gen = GeminiGenerate(project_id="p", max_concurrent_requests=1)
    with pytest.raises(GeminiGenerationError) as exc_info:
        asyncio.run(
            gen.generate_async(prompt="x", max_retries=2, base_delay_seconds=0)
        )
    assert "All 2 retry attempts failed." in str(exc_info.value)
    assert exc_info.value.original_exception is quota
    assert fake_client.aio.models.generate_content.call_count == 2


@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_async_zero_retries_returns_none(mock_genai):
    """`max_retries=0` skips the loop entirely — falls through to None."""
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    gen = GeminiGenerate(project_id="p", max_concurrent_requests=1)
    res = asyncio.run(gen.generate_async(prompt="x", max_retries=0))
    assert res is None


@patch("cxas_scrapi.utils.gemini.genai")
def test_is_transient_error(mock_genai, subtests):
    gen = GeminiGenerate(project_id="p")

    cases = [
        ("ServerError 500", ServerError(500, {}), True),
        ("ServerError 503", ServerError(503, {}), True),
        ("ClientError 429", ClientError(429, {}), True),
        ("ClientError 408", ClientError(408, {}), True),
        ("ClientError 400", ClientError(400, {}), False),
        ("ClientError 403", ClientError(403, {}), False),
        ("ClientError 404", ClientError(404, {}), False),
        ("httpx.HTTPError timeout", httpx.HTTPError("timeout"), True),
        (
            "requests.RequestException connection",
            requests.RequestException("connection error"),
            True,
        ),
        ("ValueError permanent", ValueError("bad arg"), False),
    ]

    for label, exc, expected in cases:
        with subtests.test(msg=label, exc=exc):
            assert gen._is_transient_error(exc) is expected


@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_embeddings_success(mock_genai):
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client

    mock_embedding = MagicMock()
    mock_embedding.values = [0.1, 0.2]
    fake_client.models.embed_content.return_value = SimpleNamespace(
        embeddings=[mock_embedding]
    )

    gen = GeminiGenerate(project_id="p")
    res = gen.generate_embeddings(contents=["hello"])
    assert res == [[0.1, 0.2]]


@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_embeddings_raises_on_failure(mock_genai):
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    original_exc = RuntimeError("boom")
    fake_client.models.embed_content.side_effect = original_exc

    gen = GeminiGenerate(project_id="p")
    with pytest.raises(GeminiEmbeddingError) as exc_info:
        gen.generate_embeddings(contents=["hello"], max_retries=1)
    assert "Permanent error: boom" in str(exc_info.value)
    assert exc_info.value.original_exception is original_exc


@patch("cxas_scrapi.utils.gemini.time.sleep", new=MagicMock())
@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_embeddings_retries_on_transient(mock_genai):
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client

    mock_embedding = MagicMock()
    mock_embedding.values = [0.1, 0.2]
    success_response = SimpleNamespace(embeddings=[mock_embedding])

    fake_client.models.embed_content.side_effect = [
        ClientError(429, {}),
        success_response,
    ]

    gen = GeminiGenerate(project_id="p")
    res = gen.generate_embeddings(
        contents=["hello"], max_retries=3, base_delay_seconds=0
    )
    assert res == [[0.1, 0.2]]
    assert fake_client.models.embed_content.call_count == 2


@patch("cxas_scrapi.utils.gemini.time.sleep", new=MagicMock())
@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_retries_on_transient(mock_genai):
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client

    fake_client.models.generate_content.side_effect = [
        ClientError(429, {}),
        SimpleNamespace(text="ok"),
    ]

    gen = GeminiGenerate(project_id="p")
    res = gen.generate(prompt="hello", max_retries=3, base_delay_seconds=0)
    assert res == "ok"
    assert fake_client.models.generate_content.call_count == 2


@patch("cxas_scrapi.utils.gemini.time.sleep", new=MagicMock())
@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_with_parts_retries_on_transient(mock_genai):
    mock_genai.types.Part.from_text = lambda text: text
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client

    fake_client.models.generate_content.side_effect = [
        ServerError(503, {}),
        SimpleNamespace(text="ok"),
    ]

    gen = GeminiGenerate(project_id="p")
    res = gen.generate_with_parts(
        parts=["x"], max_retries=3, base_delay_seconds=0
    )
    assert res == "ok"
    assert fake_client.models.generate_content.call_count == 2


@patch("cxas_scrapi.utils.gemini.time.sleep", new=MagicMock())
@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_all_retries_fail_sync(mock_genai):
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    quota = ClientError(429, {})
    fake_client.models.generate_content.side_effect = quota

    gen = GeminiGenerate(project_id="p")
    with pytest.raises(GeminiGenerationError) as exc_info:
        gen.generate(prompt="hello", max_retries=3, base_delay_seconds=0)
    assert "All 3 retry attempts failed." in str(exc_info.value)
    assert exc_info.value.original_exception is quota
    assert fake_client.models.generate_content.call_count == 3


@patch("cxas_scrapi.utils.gemini.genai")
def test_create_cache_success(mock_genai):
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    mock_cache = MagicMock()
    mock_cache.name = "cachedContents/12345"
    fake_client.aio.caches.create = AsyncMock(return_value=mock_cache)

    gen = GeminiGenerate(project_id="p")
    res = asyncio.run(
        gen.create_cache(
            system_prompt="sys", shared_content="shared", ttl_seconds=100
        )
    )
    assert res == "cachedContents/12345"
    _, kwargs = fake_client.aio.caches.create.call_args
    assert kwargs["model"] == gen.model_name
    assert kwargs["config"]["system_instruction"] == "sys"
    assert kwargs["config"]["ttl"] == "100s"
    contents = kwargs["config"]["contents"]

    assert contents == [mock_genai.types.Content.return_value]
    mock_genai.types.Content.assert_called_once_with(
        role="user", parts=[mock_genai.types.Part.from_text.return_value]
    )
    mock_genai.types.Part.from_text.assert_called_once_with(text="shared")


@patch("cxas_scrapi.utils.gemini.genai")
def test_create_cache_failure(mock_genai):
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    fake_client.aio.caches.create = AsyncMock(
        side_effect=RuntimeError("cache error")
    )

    gen = GeminiGenerate(project_id="p")
    res = asyncio.run(
        gen.create_cache(system_prompt="sys", shared_content="shared")
    )
    assert res is None


@patch("cxas_scrapi.utils.gemini.genai")
def test_delete_cache_success(mock_genai):
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    fake_client.aio.caches.delete = AsyncMock()

    gen = GeminiGenerate(project_id="p")
    asyncio.run(gen.delete_cache(cache_name="cachedContents/12345"))
    fake_client.aio.caches.delete.assert_called_once_with(
        name="cachedContents/12345"
    )


@patch("cxas_scrapi.utils.gemini.genai")
def test_delete_cache_failure_does_not_propagate(mock_genai):
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    fake_client.aio.caches.delete = AsyncMock(
        side_effect=RuntimeError("delete error")
    )

    gen = GeminiGenerate(project_id="p")
    # Should not raise exception
    asyncio.run(gen.delete_cache(cache_name="cachedContents/12345"))
    fake_client.aio.caches.delete.assert_called_once_with(
        name="cachedContents/12345"
    )


@patch("cxas_scrapi.utils.gemini.asyncio.sleep", new=AsyncMock())
@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_async_with_context_cache(mock_genai):
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    fake_client.aio.models.generate_content = AsyncMock(
        return_value=SimpleNamespace(text="templated response")
    )

    gen = GeminiGenerate(project_id="p")
    res = asyncio.run(
        gen.generate_async(
            prompt="next question",
            cached_content_name="cachedContents/12345",
            temperature=0.7,
        )
    )
    assert res == "templated response"
    mock_genai.types.GenerateContentConfig.assert_called_once_with(
        cached_content="cachedContents/12345", temperature=0.7
    )
    _, kwargs = fake_client.aio.models.generate_content.call_args
    assert (
        kwargs["config"] == mock_genai.types.GenerateContentConfig.return_value
    )


@patch("cxas_scrapi.utils.gemini.asyncio.sleep", new=AsyncMock())
@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_async_immediate_failure_on_permanent_error(mock_genai):
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    permanent_error = ClientError(403, {})
    fake_client.aio.models.generate_content = AsyncMock(
        side_effect=permanent_error
    )

    gen = GeminiGenerate(project_id="p")
    with pytest.raises(GeminiGenerationError) as exc_info:
        asyncio.run(gen.generate_async(prompt="x", max_retries=3))
    assert "Permanent error:" in str(exc_info.value)
    assert exc_info.value.original_exception is permanent_error
    assert fake_client.aio.models.generate_content.call_count == 1


@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_embeddings_fallback_when_none(mock_genai):
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    fake_client.models.embed_content.return_value = SimpleNamespace(
        embeddings=None
    )

    gen = GeminiGenerate(project_id="p")
    res = gen.generate_embeddings(contents=["hello"])
    assert res == []


@patch("cxas_scrapi.utils.gemini.time.sleep", new=MagicMock())
@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_embeddings_all_retries_fail_sync(mock_genai):
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    quota = ClientError(429, {})
    fake_client.models.embed_content.side_effect = quota

    gen = GeminiGenerate(project_id="p")
    with pytest.raises(GeminiEmbeddingError) as exc_info:
        gen.generate_embeddings(
            contents=["hello"], max_retries=3, base_delay_seconds=0
        )
    assert "All 3 retry attempts failed." in str(exc_info.value)
    assert exc_info.value.original_exception is quota
    assert fake_client.models.embed_content.call_count == 3


@patch("cxas_scrapi.utils.gemini.time.sleep", new=MagicMock())
@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_with_parts_all_retries_fail_sync(mock_genai):
    mock_genai.types.Part.from_text = lambda text: text
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    quota = ClientError(429, {})
    fake_client.models.generate_content.side_effect = quota

    gen = GeminiGenerate(project_id="p")
    with pytest.raises(GeminiGenerationError) as exc_info:
        gen.generate_with_parts(
            parts=["x"], max_retries=3, base_delay_seconds=0
        )
    assert "All 3 retry attempts failed." in str(exc_info.value)
    assert exc_info.value.original_exception is quota
    assert fake_client.models.generate_content.call_count == 3
