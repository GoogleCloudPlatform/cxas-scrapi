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

"""Tests for dfcx_exporter module."""

import io
import json
import zipfile

from cxas_scrapi.migration.dfcx_exporter import DFCXAgentExporter


def test_process_zip_content_minimal():
    extractor = DFCXAgentExporter()
    agent_id = "projects/test-project/locations/global/agents/test-agent"

    # Create a minimal zip in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(
        zip_buffer, "a", zipfile.ZIP_DEFLATED, False
    ) as zip_file:
        agent_json = {
            "name": agent_id,
            "displayName": "Test Agent",
            "defaultLanguageCode": "en",
        }
        zip_file.writestr("agent.json", json.dumps(agent_json))

    zip_content = zip_buffer.getvalue()

    result = extractor.process_zip_content(zip_content, agent_id)

    assert result is not None
    assert result.name == agent_id
    assert result.display_name == "Test Agent"
    assert hasattr(result, "intents")
    assert hasattr(result, "tools")
    assert hasattr(result, "entity_types")
    assert hasattr(result, "webhooks")
    assert hasattr(result, "flows")
    assert hasattr(result, "playbooks")
    assert hasattr(result, "test_cases")
    assert hasattr(result, "generators")
    assert hasattr(result, "agent_transition_route_groups")


def test_process_zip_content_complex():
    extractor = DFCXAgentExporter()
    agent_id = "projects/test-project/locations/global/agents/test-agent"

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(
        zip_buffer, "a", zipfile.ZIP_DEFLATED, False
    ) as zip_file:
        # 1. agent.json
        agent_json = {
            "name": agent_id,
            "displayName": "Test Agent",
            "defaultLanguageCode": "en",
        }
        zip_file.writestr("agent.json", json.dumps(agent_json))

        # 2. Flows & Pages
        flow_json = {"displayName": "Main Flow"}
        zip_file.writestr(
            "flows/main_flow/main_flow.json", json.dumps(flow_json)
        )

        page_json = {"displayName": "Page 1", "name": "page-1-id"}
        zip_file.writestr(
            "flows/main_flow/pages/page1.json", json.dumps(page_json)
        )

        # 3. Generators
        gen_json = {"displayName": "My Generator", "name": "gen-id"}
        zip_file.writestr("generators/my_gen/my_gen.json", json.dumps(gen_json))

        phrase_json = {"phrases": [{"text": "hello"}]}
        zip_file.writestr(
            "generators/my_gen/phrases/en.json", json.dumps(phrase_json)
        )

        # 4. Test Cases
        tc_json = {"displayName": "Test Case 1"}
        zip_file.writestr("testCases/test_case_1.json", json.dumps(tc_json))

        # 5. Agent TRGs
        trg_json = {"displayName": "My TRG", "name": "trg-id"}
        zip_file.writestr(
            "agentTransitionRouteGroups/my_trg.json", json.dumps(trg_json)
        )

        # 6. Generative Settings
        gs_json = {"fallbackSettings": {"enabled": True}}
        zip_file.writestr("generativeSettings/en.json", json.dumps(gs_json))

    zip_content = zip_buffer.getvalue()

    result = extractor.process_zip_content(zip_content, agent_id)

    assert result is not None
    assert result.name == agent_id

    # Verify Flows
    assert len(result.flows) == 1
    assert result.flows[0].flow_data["displayName"] == "Main Flow"
    assert len(result.flows[0].pages) == 1
    assert result.flows[0].pages[0].page_data["displayName"] == "Page 1"

    # Verify Generators
    assert len(result.generators) == 1
    assert result.generators[0]["name"] == f"{agent_id}/generators/gen-id"
    assert "phrases" in result.generators[0]
    assert "en" in result.generators[0]["phrases"]

    # Verify Test Cases
    assert len(result.test_cases) == 1
    assert result.test_cases[0]["displayName"] == "Test Case 1"

    # Verify Agent TRGs
    assert len(result.agent_transition_route_groups) == 1
    assert (
        result.agent_transition_route_groups[0]["name"]
        == f"{agent_id}/agentTransitionRouteGroups/trg-id"
    )

    # Verify Generative Settings
    assert "en" in result.generative_settings
    assert (
        result.generative_settings["en"]["fallbackSettings"]["enabled"] is True
    )
