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

"""Tests for dependency analyzer."""

import pytest

from cxas_scrapi.migration.dfcx_dep_analyzer import DependencyAnalyzer


@pytest.fixture
def sample_agent_data():
    return {
        "playbooks": [
            {
                "name": "projects/p1/locations/l1/agents/a1/playbooks/pb1",
                "displayName": "Playbook 1",
                "referencedPlaybooks": [
                    "projects/p1/locations/l1/agents/a1/playbooks/pb2"
                ],
                "instruction": {"steps": [{"text": "Go to ${FLOW:Flow 1}"}]},
            },
            {
                "name": "projects/p1/locations/l1/agents/a1/playbooks/pb2",
                "displayName": "Playbook 2",
            },
        ],
        "flows": [
            {
                "flow_data": {
                    "name": "projects/p1/locations/l1/agents/a1/flows/f1",
                    "displayName": "Flow 1",
                    "transitionRoutes": [
                        {
                            "targetFlow": (
                                "projects/p1/locations/l1/agents/a1/flows/f2"
                            )
                        }
                    ],
                }
            },
            {
                "flow_data": {
                    "name": "projects/p1/locations/l1/agents/a1/flows/f2",
                    "displayName": "Flow 2",
                }
            },
        ],
    }


def test_analyzer_init(sample_agent_data):
    analyzer = DependencyAnalyzer(sample_agent_data)
    assert "Playbook 1" in analyzer.id_map
    assert (
        "projects/p1/locations/l1/agents/a1/playbooks/pb1" in analyzer.name_map
    )


def test_analyzer_graph(sample_agent_data):
    analyzer = DependencyAnalyzer(sample_agent_data)
    pb1_id = "projects/p1/locations/l1/agents/a1/playbooks/pb1"
    pb2_id = "projects/p1/locations/l1/agents/a1/playbooks/pb2"
    f1_id = "projects/p1/locations/l1/agents/a1/flows/f1"
    f2_id = "projects/p1/locations/l1/agents/a1/flows/f2"

    assert pb2_id in analyzer.graph[pb1_id]
    assert f1_id in analyzer.graph[pb1_id]  # from text scan
    assert f2_id in analyzer.graph[f1_id]


def test_get_impact(sample_agent_data):
    analyzer = DependencyAnalyzer(sample_agent_data)
    pb1_id = "projects/p1/locations/l1/agents/a1/playbooks/pb1"
    pb2_id = "projects/p1/locations/l1/agents/a1/playbooks/pb2"
    f1_id = "projects/p1/locations/l1/agents/a1/flows/f1"

    outgoing, incoming = analyzer.get_impact([pb1_id])
    assert pb2_id in outgoing
    assert f1_id in outgoing

    outgoing, incoming = analyzer.get_impact([pb2_id])
    assert pb1_id in incoming
