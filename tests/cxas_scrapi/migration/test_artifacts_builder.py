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

"""Tests for artifacts_builder.py."""

from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from cxas_scrapi.migration.artifacts_builder import CXASAsyncArtifactBuilder


@pytest.fixture
def mock_gemini_client():
    client = MagicMock()
    client.generate_async = AsyncMock()
    return client


@pytest.fixture
def builder(mock_gemini_client):
    return CXASAsyncArtifactBuilder(gemini_client=mock_gemini_client)


@pytest.mark.asyncio
async def test_run_step_1a_success(builder, mock_gemini_client):
    """Test successful generation of inventory."""
    mock_gemini_client.generate_async.return_value = "Inventory Report"

    inventory = await builder._run_step_1a_inventory(
        "Test Flow", "Dummy Tree View", {}
    )

    assert inventory == "Inventory Report"
    mock_gemini_client.generate_async.assert_called_once()


@pytest.mark.asyncio
async def test_run_step_1b_success(builder, mock_gemini_client):
    """Test successful generation of business logic."""
    mock_gemini_client.generate_async.return_value = "Business Logic"

    business_logic = await builder._run_step_1b_business_logic(
        "Test Flow", "Inventory Report", "Dummy Tree View", "Telemetry Summary"
    )

    assert business_logic == "Business Logic"
    mock_gemini_client.generate_async.assert_called_once()


@pytest.mark.asyncio
async def test_run_step_1c_success(builder, mock_gemini_client):
    """Test successful generation of requirements."""
    mock_gemini_client.generate_async.return_value = (
        "Requirement_ID,Priority,Category,Description,Expected_Behavior\n"
        "REQ1,P0,Auth,User must be authenticated,Redirect to auth page"
    )

    df_reqs = await builder._run_step_1c_requirements(
        "Test Flow", "Business Logic", "Dummy Tree View"
    )

    assert isinstance(df_reqs, pd.DataFrame)
    assert len(df_reqs) == 1
    assert df_reqs.iloc[0]["Requirement_ID"] == "REQ1"
    mock_gemini_client.generate_async.assert_called_once()


@pytest.mark.asyncio
async def test_run_step_1d_success(builder, mock_gemini_client):
    """Test successful generation of tests."""
    mock_gemini_client.generate_async.return_value = (
        '```json\n[{"name": "Scenario 1"}]\n```'
    )

    tests = await builder._run_step_1d_tests(
        "Test Flow",
        "Inventory Report",
        "Dummy Tree View",
        "Business Logic",
        pd.DataFrame(),
    )

    assert tests == [{"name": "Scenario 1"}]
    mock_gemini_client.generate_async.assert_called_once()


@pytest.mark.asyncio
async def test_run_step_1_success(builder, mock_gemini_client):
    """Test full step 1 analysis."""
    # We need to set return values for multiple calls!
    mock_gemini_client.generate_async.side_effect = [
        "Inventory Report",
        "Business Logic",
        (
            "Requirement_ID,Priority,Category,Description,Expected_Behavior\n"
            "REQ1,P0,Auth,User must be authenticated,Redirect to auth page"
        ),
        '```json\n[{"name": "Scenario 1"}]\n```',
    ]

    artifacts = await builder.run_step_1(
        "Test Flow", "Dummy Tree View", {}, "Telemetry Summary"
    )

    assert artifacts["flow_name"] == "Test Flow"
    assert artifacts["inventory"] == "Inventory Report"
    assert artifacts["business_logic"] == "Business Logic"
    assert isinstance(artifacts["requirements"], pd.DataFrame)
    assert artifacts["test_cases"] == [{"name": "Scenario 1"}]
    assert mock_gemini_client.generate_async.call_count == 4
