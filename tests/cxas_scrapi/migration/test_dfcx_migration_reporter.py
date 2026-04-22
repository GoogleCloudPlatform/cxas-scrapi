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

from unittest.mock import AsyncMock, MagicMock

import pytest

from cxas_scrapi.migration.dfcx_migration_reporter import DFCXMigrationReporter
from cxas_scrapi.utils.gemini import GeminiGenerate


@pytest.fixture
def mock_gemini():

    client = MagicMock(spec=GeminiGenerate)

    def mock_generate(prompt, system_prompt=None):
        if "customer user journeys" in prompt:
            return "Mocked User Journeys:\n1. Journey A\n2. Journey B"
        elif "detailed description" in prompt:
            return "Mocked detailed description."
        return "Mocked response"

    client.generate.side_effect = mock_generate
    client.generate_async = AsyncMock(side_effect=mock_generate)
    return client


def test_initialization(mock_gemini):
    reporter = DFCXMigrationReporter(gemini_client=mock_gemini)
    assert reporter.gemini_client == mock_gemini
    assert reporter.app_info == {}
    assert reporter.variables == []
    assert reporter.tools == []
    assert reporter.agents == []


def test_set_app_info(mock_gemini):
    reporter = DFCXMigrationReporter(gemini_client=mock_gemini)
    reporter.set_app_info("dfcx-123", "cxas-app", "cxas-456")
    assert reporter.app_info == {
        "source": "dfcx-123",
        "target_name": "cxas-app",
        "target_id": "cxas-456",
    }


def test_log_variable(mock_gemini):
    reporter = DFCXMigrationReporter(gemini_client=mock_gemini)
    reporter.log_variable("orig_var", "new_var", "STRING")
    assert len(reporter.variables) == 1
    assert reporter.variables[0] == {
        "original": "orig_var",
        "sanitized": "new_var",
        "type": "STRING",
    }


@pytest.mark.asyncio
async def test_generate_cxas_augmented_details(mock_gemini):
    reporter = DFCXMigrationReporter(gemini_client=mock_gemini)
    mock_config = {
        "display_name": "Test Agent",
        "instruction": "Test Instruction",
        "tools": [],
        "callbacks": [],
    }
    await reporter.generate_cxas_augmented_details(agent_config=mock_config)

    assert reporter.generated_description == "Mocked detailed description."
    assert (
        reporter.generated_features
        == "Mocked User Journeys:\n1. Journey A\n2. Journey B"
    )
    assert mock_gemini.generate_async.call_count == 2


@pytest.mark.asyncio
async def test_generate_markdown(mock_gemini):
    reporter = DFCXMigrationReporter(gemini_client=mock_gemini)
    reporter.set_app_info("dfcx-123", "cxas-app", "cxas-456")

    mock_config = {
        "display_name": "Test Agent",
        "instruction": "Test Instruction",
    }
    await reporter.generate_cxas_augmented_details(agent_config=mock_config)

    markdown = reporter.generate_markdown()

    assert "# Polysynth Migration Audit Report" in markdown
    assert "dfcx-123" in markdown
    assert "cxas-app" in markdown
    assert "Mocked detailed description." in markdown
    assert "Mocked User Journeys" in markdown
