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

"""Deterministic Eval Generator for generating foundational unit tests."""

import logging
import re
from typing import Any, List

from cxas_scrapi.evals.turn_evals import (
    TurnExpectation,
    TurnOperator,
    TurnStep,
    TurnTestCase,
)
from cxas_scrapi.migration.data_models import MigrationIR

logger = logging.getLogger(__name__)


class DeterministicEvalGenerator:
    """Generates deterministic, foundational unit tests by directly parsing

    the compiled Intermediate Representation (IR) of the agent.
    """

    def __init__(self, ir_state: MigrationIR):
        self.ir = ir_state

    def _build_test_turn(
        self,
        turn_name: str,
        user_text: str,
        exp_type: TurnOperator,
        exp_value: Any,
    ) -> TurnStep:
        """Standardized schema for a unit test turn."""
        return TurnStep(
            turn=turn_name,
            user=user_text,
            expectations=[TurnExpectation(type=exp_type, value=exp_value)],
        )

    def generate_tests_for_agent(self, agent_name: str) -> List[TurnTestCase]:
        """Parse the IR to build an isolated test suite for the given agent."""
        agent_data = self.ir.agents.get(agent_name)
        if not agent_data:
            logger.warning(
                f"[EvalGenerator] Could not find agent '{agent_name}' in IR."
            )
            return []

        tests = []
        instructions = (
            agent_data.instruction if hasattr(agent_data, "instruction") else ""
        )

        # ---------------------------------------------------------
        # 1. The Ping / Initialization Test
        # ---------------------------------------------------------
        ping_step = self._build_test_turn(
            turn_name="Ping",
            user_text="hi",
            exp_type=TurnOperator.CONTAINS,
            exp_value="Hi",  # Fallback guess
        )
        tests.append(
            TurnTestCase(name=f"[{agent_name}] Basic Ping", turns=[ping_step])
        )

        # ---------------------------------------------------------
        # 2. Tool Invocation Tests
        # ---------------------------------------------------------
        # Look for {@TOOL: tool_name} in the compiled XML
        tools_found = set(re.findall(r"{@TOOL:\s*([^}]+)}", instructions))
        for tool in tools_found:
            tool_clean = tool.strip()
            tool_step = self._build_test_turn(
                turn_name=f"Tool_{tool_clean}",
                user_text=f"Please execute the {tool_clean} action.",
                exp_type=TurnOperator.TOOL_CALLED,
                exp_value=tool_clean,
            )
            tests.append(
                TurnTestCase(
                    name=f"[{agent_name}] Tool Binding: {tool_clean}",
                    turns=[tool_step],
                )
            )

        # ---------------------------------------------------------
        # 3. Agent Transfer / Routing Tests
        # ---------------------------------------------------------
        # Look for {@AGENT: target_agent} in the compiled XML
        agents_found = set(re.findall(r"{@AGENT:\s*([^}]+)}", instructions))
        for target_agent in agents_found:
            target_clean = target_agent.strip()
            if target_clean.upper() in ["END_SESSION", "END_FLOW"]:
                continue
            routing_step = self._build_test_turn(
                turn_name=f"Routing_{target_clean}",
                user_text=f"I want to talk to {target_clean}.",
                exp_type=TurnOperator.AGENT_TRANSFER,
                exp_value=target_clean,
            )
            tests.append(
                TurnTestCase(
                    name=f"[{agent_name}] Routing: {target_clean}",
                    turns=[routing_step],
                )
            )

        return tests
