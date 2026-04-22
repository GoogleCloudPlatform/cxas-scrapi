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

"""Tests for eval_generator.py."""

import pytest

from cxas_scrapi.evals.turn_evals import TurnOperator
from cxas_scrapi.migration.data_models import IRAgent, IRMetadata, MigrationIR
from cxas_scrapi.migration.eval_generator import DeterministicEvalGenerator


@pytest.fixture
def sample_ir():
    metadata = IRMetadata(app_name="Test App")
    agent = IRAgent(
        type="PLAYBOOK",
        display_name="Test Agent",
        instruction=(
            "Sample instruction with {@TOOL: test_tool} and "
            "{@AGENT: other_agent}"
        ),
    )
    return MigrationIR(metadata=metadata, agents={"Test Agent": agent})


def test_generate_tests_for_agent(sample_ir):
    """Test successful generation of tests."""
    generator = DeterministicEvalGenerator(ir_state=sample_ir)

    tests = generator.generate_tests_for_agent("Test Agent")

    assert len(tests) == 3

    # Verify Ping Test
    assert tests[0].name == "[Test Agent] Basic Ping"
    assert len(tests[0].turns) == 1
    assert tests[0].turns[0].user == "hi"
    assert tests[0].turns[0].expectations[0].type == TurnOperator.CONTAINS

    # Verify Tool Test
    assert tests[1].name == "[Test Agent] Tool Binding: test_tool"
    assert len(tests[1].turns) == 1
    assert tests[1].turns[0].expectations[0].type == TurnOperator.TOOL_CALLED
    assert tests[1].turns[0].expectations[0].value == "test_tool"

    # Verify Routing Test
    assert tests[2].name == "[Test Agent] Routing: other_agent"
    assert len(tests[2].turns) == 1
    assert tests[2].turns[0].expectations[0].type == TurnOperator.AGENT_TRANSFER
    assert tests[2].turns[0].expectations[0].value == "other_agent"


def test_generate_tests_for_agent_missing(sample_ir):
    """Test handling of missing agent."""
    generator = DeterministicEvalGenerator(ir_state=sample_ir)

    tests = generator.generate_tests_for_agent("Non Existent Agent")

    assert tests == []
