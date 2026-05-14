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

from cxas_scrapi.utils.gemini import GeminiGenerate


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
    args, kwargs = fake_client.models.generate_content.call_args
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
def test_generate_with_parts_returns_none_on_failure(mock_genai):
    mock_genai.types.Part.from_text = lambda text: text
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    fake_client.models.generate_content.side_effect = RuntimeError("boom")

    gen = GeminiGenerate(project_id="p")
    assert gen.generate_with_parts(parts=["x"]) is None


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
def test_generate_returns_none_on_failure(mock_genai):
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    fake_client.models.generate_content.side_effect = RuntimeError("boom")
    gen = GeminiGenerate(project_id="p")
    assert gen.generate(prompt="p") is None


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
    quota = RuntimeError("RESOURCE_EXHAUSTED 429")
    fake_client.aio.models.generate_content = AsyncMock(
        side_effect=[quota, SimpleNamespace(text="ok")]
    )
    gen = GeminiGenerate(project_id="p", max_concurrent_requests=1)
    res = asyncio.run(
        gen.generate_async(prompt="x", max_retries=3, base_delay_seconds=0)
    )
    assert res == "ok"


@patch("cxas_scrapi.utils.gemini.asyncio.sleep", new=AsyncMock())
@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_async_all_retries_fail(mock_genai):
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    fake_client.aio.models.generate_content = AsyncMock(
        side_effect=RuntimeError("boom")
    )
    gen = GeminiGenerate(project_id="p", max_concurrent_requests=1)
    res = asyncio.run(
        gen.generate_async(prompt="x", max_retries=2, base_delay_seconds=0)
    )
    assert res is None


@patch("cxas_scrapi.utils.gemini.genai")
def test_generate_async_zero_retries_returns_none(mock_genai):
    """`max_retries=0` skips the loop entirely — falls through to None."""
    fake_client = MagicMock()
    mock_genai.Client.return_value = fake_client
    gen = GeminiGenerate(project_id="p", max_concurrent_requests=1)
    res = asyncio.run(gen.generate_async(prompt="x", max_retries=0))
    assert res is None
