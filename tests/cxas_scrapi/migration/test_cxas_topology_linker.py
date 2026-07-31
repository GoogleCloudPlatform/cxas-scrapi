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
    DFCXAgentIR,
    DFCXFlowModel,
    IRAgent,
    IRBundle,
    IRMetadata,
    MigrationConfig,
    MigrationIR,
    MigrationStatus,
)
from cxas_scrapi.migration.topology_wirer import (
    compute_group_children_preserve_hierarchy,
)


def test_link_and_finalize_topology() -> None:
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
        "name": "projects/123/apps/456",
        "display_name": "Test Agent",
        "default_language_code": "en",
        "start_playbook": "projects/123/playbooks/agent1",
        "playbooks": [
            {"name": "projects/123/playbooks/agent1", "displayName": "Agent1"}
        ],
    }

    linker.link_and_finalize_topology(ir, DFCXAgentIR(**source_agent_data))

    # Verify that update_agent was called for Agent1 to link Agent2
    mock_ps_agents.update_agent.assert_called_once_with(
        agent_name="projects/123/apps/456/agents/agent1",
        child_agents=["projects/123/apps/456/agents/agent2"],
    )
    # And update_app was called to set root agent
    mock_ps_apps.update_app.assert_called_once_with(
        app_name="projects/123/apps/456",
        root_agent="projects/123/apps/456/agents/agent1",
    )


def test_compute_group_children_enforces_strict_tree() -> None:
    config = MigrationConfig(
        project_id="test-proj",
        target_name="test-app",
        model="gemini-2.5-pro",
    )
    source_agent_data = {
        "name": "projects/123/apps/456",
        "display_name": "Test Agent",
        "default_language_code": "en",
        "start_playbook": "projects/123/playbooks/root",
        "playbooks": [
            {"name": "projects/123/playbooks/root", "displayName": "RootFlow"},
            {"name": "projects/123/playbooks/a", "displayName": "FlowA"},
            {"name": "projects/123/playbooks/b", "displayName": "FlowB"},
            {"name": "projects/123/playbooks/c", "displayName": "FlowC"},
        ],
        "flows": [
            DFCXFlowModel(
                flow_id="projects/123/playbooks/root",
                flow_data={
                    "name": "projects/123/playbooks/root",
                    "displayName": "RootFlow",
                    "transitionRoutes": [
                        {"targetFlow": "projects/123/playbooks/a"},
                        {"targetFlow": "projects/123/playbooks/b"},
                    ],
                },
            ),
            DFCXFlowModel(
                flow_id="projects/123/playbooks/a",
                flow_data={
                    "name": "projects/123/playbooks/a",
                    "displayName": "FlowA",
                    "transitionRoutes": [
                        {"targetFlow": "projects/123/playbooks/c"},
                    ],
                },
            ),
            DFCXFlowModel(
                flow_id="projects/123/playbooks/b",
                flow_data={
                    "name": "projects/123/playbooks/b",
                    "displayName": "FlowB",
                    "transitionRoutes": [
                        {"targetFlow": "projects/123/playbooks/c"},
                    ],
                },
            ),
            DFCXFlowModel(
                flow_id="projects/123/playbooks/c",
                flow_data={
                    "name": "projects/123/playbooks/c",
                    "displayName": "FlowC",
                    "transitionRoutes": [],
                },
            ),
        ],
    }

    ir = MigrationIR(
        metadata=IRMetadata(
            app_name="test-app", app_resource_name="projects/123/apps/456"
        ),
        agents={
            "RootFlow": IRAgent(
                type="PLAYBOOK", display_name="RootFlow", instruction=""
            ),
            "FlowA": IRAgent(
                type="PLAYBOOK", display_name="FlowA", instruction=""
            ),
            "FlowB": IRAgent(
                type="PLAYBOOK", display_name="FlowB", instruction=""
            ),
            "FlowC": IRAgent(
                type="PLAYBOOK", display_name="FlowC", instruction=""
            ),
        },
    )

    grouping = {
        "RootGroup": {"agents": ["RootFlow"], "is_root": True},
        "GroupA": {"agents": ["FlowA"]},
        "GroupB": {"agents": ["FlowB"]},
        "GroupC": {"agents": ["FlowC"]},
    }

    bundle = IRBundle(
        config=config,
        source_agent_data=source_agent_data,
        ir=ir,
        grouping=grouping,
    )

    tree = compute_group_children_preserve_hierarchy(bundle)

    # Without tree enforcement, both GroupA and GroupB would claim GroupC.
    # With strict tree topology enforcement, GroupC can have at most ONE parent.
    parents_of_c = [
        parent for parent, children in tree.items() if "GroupC" in children
    ]
    assert len(parents_of_c) <= 1, (
        f"GroupC has multiple parents: {parents_of_c}"
    )

