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

from cxas_scrapi.migration.data_models import IRMetadata, MigrationIR
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
