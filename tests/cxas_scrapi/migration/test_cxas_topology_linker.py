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

from unittest.mock import MagicMock

from cxas_scrapi.migration.cxas_topology_linker import CXASTopologyLinker
from cxas_scrapi.migration.data_models import (
    IRAgent,
    IRMetadata,
    MigrationIR,
    MigrationStatus,
)


def test_link_and_finalize_topology():
    mock_ps_agents = MagicMock()
    mock_ps_apps = MagicMock()
    mock_reporter = MagicMock()

    linker = CXASTopologyLinker(mock_ps_agents, mock_ps_apps, mock_reporter)

    ir = MigrationIR(
        metadata=IRMetadata(
            app_name="test-app", app_resource_name="projects/123/apps/456"
        ),
        agents={
            "Agent1": IRAgent(
                type="PLAYBOOK",
                display_name="Agent1",
                instruction="Reference {@AGENT: Agent2}",
                status=MigrationStatus.DEPLOYED,
                resource_name="projects/123/apps/456/agents/agent1",
            ),
            "Agent2": IRAgent(
                type="PLAYBOOK",
                display_name="Agent2",
                instruction="Reference {@AGENT: Agent1}",
                status=MigrationStatus.DEPLOYED,
                resource_name="projects/123/apps/456/agents/agent2",
            ),
        },
    )

    source_agent_data = {
        "startPlaybook": "projects/123/playbooks/agent1",
        "playbooks": [
            {"name": "projects/123/playbooks/agent1", "displayName": "Agent1"}
        ],
    }

    linker.link_and_finalize_topology(ir, source_agent_data)

    # Verify that update_agent was called for Agent1 to link Agent2
    mock_ps_agents.update_agent.assert_called_once_with(
        agent_id="projects/123/apps/456/agents/agent1",
        child_agents=["projects/123/apps/456/agents/agent2"],
    )
    # And update_app was called to set root agent
    mock_ps_apps.update_app.assert_called_once_with(
        app_id="projects/123/apps/456",
        rootAgent="projects/123/apps/456/agents/agent1",
    )
