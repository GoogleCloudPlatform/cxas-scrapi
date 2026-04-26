# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.Agent.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cxas_scrapi.migration.data_models import MigrationConfig
from cxas_scrapi.migration.service import MigrationService


@pytest.mark.asyncio
async def test_run_migration_success():
    # Mock external clients
    mock_ps_apps = MagicMock()
    mock_ps_agents = MagicMock()
    mock_ps_tools = MagicMock()
    mock_ps_toolsets = MagicMock()
    mock_secret_manager = MagicMock()
    mock_cx_api = MagicMock()

    service = MigrationService(
        project_id="test-project",
        ps_apps_client=mock_ps_apps,
        ps_agents_client=mock_ps_agents,
        ps_tools_client=mock_ps_tools,
        ps_toolsets_client=mock_ps_toolsets,
        secret_manager_client=mock_secret_manager,
        cx_api_client=mock_cx_api,
    )

    # Mock internal components
    service.exporter = MagicMock()
    service.exporter.fetch_full_agent_details.return_value = {
        "displayName": "Test Agent",
        "playbooks": [],
        "flows": [],
    }

    service.ai_augment = MagicMock()
    service.ai_augment.generate_agent_description = AsyncMock(
        return_value="Desc"
    )

    # Mock deploy methods
    service._deploy_base_resources = AsyncMock()
    service._deploy_pending_agents = AsyncMock()

    # Mock flow processing
    service._process_single_flow = AsyncMock()

    # Mock topology linker
    service.topology_linker = MagicMock()

    # Mock reporter to avoid creating report file during test
    service.reporter = MagicMock()

    with patch(
        "cxas_scrapi.migration.service.DFCXParameterExtractor.migrate_parameters"
    ) as mock_migrate:
        mock_migrate.return_value = ([], {})
        config = MigrationConfig(
            project_id="dummy-project",
            target_name="cxas-app",
            model="gemini-2.5-flash-001",
        )
        await service.run_migration(
            source_cx_agent_id="dfcx-123", config=config
        )

    # Verify sequence
    service.exporter.fetch_full_agent_details.assert_called_once_with(
        "dfcx-123", use_export=True
    )
    mock_migrate.assert_called_once()
    service._deploy_base_resources.assert_called_once()
    service._deploy_pending_agents.assert_called_once()
    service.topology_linker.link_and_finalize_topology.assert_called_once()
