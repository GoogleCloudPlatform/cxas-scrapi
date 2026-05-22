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

"""Tests for designer.py."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from cxas_scrapi.migration.data_models import (
    IRMetadata,
    IRTool,
    MigrationIR,
)
from cxas_scrapi.migration.designer import AsyncAgentDesigner


@pytest.fixture
def mock_gemini_client():
    client = MagicMock()
    client.generate_async = AsyncMock()
    return client


@pytest.fixture
def designer(mock_gemini_client):
    return AsyncAgentDesigner(gemini_client=mock_gemini_client)


@pytest.fixture
def sample_ir():
    metadata = IRMetadata(app_name="Test App")
    return MigrationIR(metadata=metadata)


@pytest.mark.asyncio
async def test_run_step_2a_missing_tree_view(designer, sample_ir):
    """Test that run_step_2a raises ValueError when tree_view is missing."""
    with pytest.raises(ValueError) as exc_info:
        await designer.run_step_2a("Test Flow", "", sample_ir)
    assert "tree_view is required" in str(exc_info.value)


@pytest.mark.asyncio
async def test_run_step_2b_missing_tree_view(designer):
    """Test that run_step_2b_instructions raises ValueError when tree_view is
    missing."""
    with pytest.raises(ValueError) as exc_info:
        await designer.run_step_2b_instructions("Test Flow", {}, "")
    assert "tree_view is required" in str(exc_info.value)


@pytest.mark.asyncio
async def test_run_step_2c_missing_tree_view(designer, sample_ir):
    """Test that run_step_2c_tools_and_callbacks raises ValueError when
    tree_view is missing."""
    with pytest.raises(ValueError) as exc_info:
        await designer.run_step_2c_tools_and_callbacks(
            "Test Flow", {}, "", sample_ir
        )
    assert "tree_view is required" in str(exc_info.value)


@pytest.mark.asyncio
async def test_run_step_2a_success(designer, mock_gemini_client, sample_ir):
    """Test successful generation of blueprint."""
    mock_gemini_client.generate_async.return_value = (
        '```json\n{"agent_metadata": {"name": "Test Flow"}}\n```'
    )

    blueprint = await designer.run_step_2a(
        "Test Flow", "Dummy Tree View", sample_ir
    )

    assert blueprint == {"agent_metadata": {"name": "Test Flow"}}
    mock_gemini_client.generate_async.assert_called_once()


@pytest.mark.asyncio
async def test_run_step_2b_success(designer, mock_gemini_client):
    """Test successful generation of instructions."""
    mock_gemini_client.generate_async.return_value = (
        "```xml\n<Agent><Name>Test Flow</Name></Agent>\n```"
    )

    instructions = await designer.run_step_2b_instructions(
        "Test Flow", {}, "Dummy Tree View"
    )

    assert instructions == "<Agent><Name>Test Flow</Name></Agent>"
    mock_gemini_client.generate_async.assert_called_once()


@pytest.mark.asyncio
async def test_run_step_2c_success(designer, mock_gemini_client, sample_ir):
    """Test successful generation of tools and callbacks."""
    mock_gemini_client.generate_async.return_value = (
        '```json\n{"tools": [{"name": "test_tool"}]}\n```'
    )

    tools_callbacks = await designer.run_step_2c_tools_and_callbacks(
        "Test Flow", {}, "Dummy Tree View", sample_ir
    )

    assert tools_callbacks == {"tools": [{"name": "test_tool"}]}
    mock_gemini_client.generate_async.assert_called_once()


# ---------------------------------------------------------------------------
# _get_available_tools_context — covers all 3 tool types + sentinel
# ---------------------------------------------------------------------------


def test_get_available_tools_context_lists_all_types_with_sentinel():
    """The new helper emits TOOLSET + PYTHON + TOOL with exact IDs and
    the ``end_session`` sentinel — unlike the older toolsets-only
    helper which skipped Python tools entirely."""
    tools = {
        "auth_user": IRTool(
            id="auth_user",
            name="projects/p/locations/us/apps/X/tools/auth_user",
            type="PYTHON",
            payload={},
        ),
        "rate_plan": IRTool(
            id="rate_plan",
            name="projects/p/locations/us/apps/X/tools/rate_plan",
            type="TOOLSET",
            payload={},
        ),
        "raw_tool": IRTool(
            id="raw_tool",
            name="projects/p/locations/us/apps/X/tools/raw_tool",
            type="TOOL",
            payload={},
        ),
    }

    context = AsyncAgentDesigner._get_available_tools_context(tools)

    # All three tool IDs appear verbatim under their respective sections.
    assert "### TOOLSET tools" in context
    assert "- rate_plan" in context
    assert "### PYTHON tools" in context
    assert "- auth_user" in context
    assert "### TOOL tools" in context
    assert "- raw_tool" in context
    # The system sentinel is always advertised.
    assert "### SYSTEM tools" in context
    assert "- end_session" in context


def test_get_available_tools_context_handles_empty_registry():
    context = AsyncAgentDesigner._get_available_tools_context({})
    # Empty per-type sections are omitted, but the sentinel block remains.
    assert "### SYSTEM tools" in context
    assert "- end_session" in context


# ---------------------------------------------------------------------------
# Step 2A + 2B receive {available_tools} in their rendered prompts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_step_2a_renders_available_tools_into_prompt(
    designer, mock_gemini_client
):
    """The 2A prompt must include the exact tool ID list so the
    blueprint Gemini produces references real tools."""
    mock_gemini_client.generate_async.return_value = "{}"
    ir = MigrationIR(
        metadata=IRMetadata(app_name="t"),
        tools={
            "authenticate_user": IRTool(
                id="authenticate_user",
                name="x",
                type="PYTHON",
                payload={},
            ),
        },
    )

    await designer.run_step_2a("Test Flow", "Dummy Tree View", ir)

    sent_prompt = mock_gemini_client.generate_async.call_args.kwargs["prompt"]
    assert "### INPUT 4: Available Tools" in sent_prompt
    assert "authenticate_user" in sent_prompt


@pytest.mark.asyncio
async def test_run_step_2b_with_target_ir_renders_available_tools(
    designer, mock_gemini_client
):
    """Passing ``target_ir`` to 2B injects the AVAILABLE TOOLS block so
    Gemini can only reference real tool IDs in ``{@TOOL: …}``."""
    mock_gemini_client.generate_async.return_value = (
        "<Agent><Name>t</Name></Agent>"
    )
    ir = MigrationIR(
        metadata=IRMetadata(app_name="t"),
        tools={
            "verify_pin_api_wrapper": IRTool(
                id="verify_pin_api_wrapper",
                name="x",
                type="PYTHON",
                payload={},
            ),
        },
    )

    await designer.run_step_2b_instructions(
        "Test Flow", {}, "Dummy Tree View", target_ir=ir
    )

    sent_prompt = mock_gemini_client.generate_async.call_args.kwargs["prompt"]
    assert "### INPUT 3: AVAILABLE TOOLS" in sent_prompt
    assert "verify_pin_api_wrapper" in sent_prompt


@pytest.mark.asyncio
async def test_run_step_2b_without_target_ir_falls_back(
    designer, mock_gemini_client
):
    """Back-compat: callers that don't pass ``target_ir`` get a generic
    fallback string in place of the AVAILABLE TOOLS block (no crash)."""
    mock_gemini_client.generate_async.return_value = "<Agent/>"
    await designer.run_step_2b_instructions("Test Flow", {}, "Dummy Tree View")
    sent_prompt = mock_gemini_client.generate_async.call_args.kwargs["prompt"]
    assert "not provided" in sent_prompt
    assert "Architecture Blueprint" in sent_prompt
