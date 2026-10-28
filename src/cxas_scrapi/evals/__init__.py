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

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cxas_scrapi.evals.callback_chain import (
        AgentAssertionError,
        BenchmarkComparisonReport,
        CallbackChainRunner,
        CallbackTrace,
        LlmStepTrace,
        MockLlm,
        ModeDiffReport,
        SessionExpectation,
        SessionMetricsSummary,
        SessionSimulator,
        SnapshotManager,
        StageExpectation,
        ToolSpy,
        TurnExpectation,
        TurnTrace,
        UsageMetadata,
        build_agent_tool_declarations,
        compare_execution_metrics,
        expect_session,
        expect_stage,
        expect_turn,
        load_local_python_tools,
        render_agent_instruction,
        serialize_session_snapshot,
        serialize_stage_snapshot,
        serialize_turn_snapshot,
    )
    from cxas_scrapi.evals.callback_evals import CallbackEvals
    from cxas_scrapi.evals.guardrail_evals import GuardrailEvals
    from cxas_scrapi.evals.shadow_evals import (
        ShadowEvals,
        ShadowReport,
        ShadowTestCase,
        ShadowUserConversation,
    )
    from cxas_scrapi.evals.simulation_evals import SimulationEvals
    from cxas_scrapi.evals.tool_evals import ToolEvals
    from cxas_scrapi.evals.turn_evals import TurnEvals

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "AgentAssertionError": (
        "cxas_scrapi.evals.callback_chain",
        "AgentAssertionError",
    ),
    "BenchmarkComparisonReport": (
        "cxas_scrapi.evals.callback_chain",
        "BenchmarkComparisonReport",
    ),
    "CallbackChainRunner": (
        "cxas_scrapi.evals.callback_chain",
        "CallbackChainRunner",
    ),
    "CallbackTrace": ("cxas_scrapi.evals.callback_chain", "CallbackTrace"),
    "LlmStepTrace": ("cxas_scrapi.evals.callback_chain", "LlmStepTrace"),
    "MockLlm": ("cxas_scrapi.evals.callback_chain", "MockLlm"),
    "ModeDiffReport": ("cxas_scrapi.evals.callback_chain", "ModeDiffReport"),
    "SessionExpectation": (
        "cxas_scrapi.evals.callback_chain",
        "SessionExpectation",
    ),
    "SessionMetricsSummary": (
        "cxas_scrapi.evals.callback_chain",
        "SessionMetricsSummary",
    ),
    "SessionSimulator": (
        "cxas_scrapi.evals.callback_chain",
        "SessionSimulator",
    ),
    "SnapshotManager": (
        "cxas_scrapi.evals.callback_chain",
        "SnapshotManager",
    ),
    "StageExpectation": (
        "cxas_scrapi.evals.callback_chain",
        "StageExpectation",
    ),
    "ToolSpy": ("cxas_scrapi.evals.callback_chain", "ToolSpy"),
    "TurnExpectation": (
        "cxas_scrapi.evals.callback_chain",
        "TurnExpectation",
    ),
    "TurnTrace": ("cxas_scrapi.evals.callback_chain", "TurnTrace"),
    "UsageMetadata": ("cxas_scrapi.evals.callback_chain", "UsageMetadata"),
    "build_agent_tool_declarations": (
        "cxas_scrapi.evals.callback_chain",
        "build_agent_tool_declarations",
    ),
    "compare_execution_metrics": (
        "cxas_scrapi.evals.callback_chain",
        "compare_execution_metrics",
    ),
    "expect_session": (
        "cxas_scrapi.evals.callback_chain",
        "expect_session",
    ),
    "expect_stage": ("cxas_scrapi.evals.callback_chain", "expect_stage"),
    "expect_turn": ("cxas_scrapi.evals.callback_chain", "expect_turn"),
    "load_local_python_tools": (
        "cxas_scrapi.evals.callback_chain",
        "load_local_python_tools",
    ),
    "render_agent_instruction": (
        "cxas_scrapi.evals.callback_chain",
        "render_agent_instruction",
    ),
    "serialize_session_snapshot": (
        "cxas_scrapi.evals.callback_chain",
        "serialize_session_snapshot",
    ),
    "serialize_stage_snapshot": (
        "cxas_scrapi.evals.callback_chain",
        "serialize_stage_snapshot",
    ),
    "serialize_turn_snapshot": (
        "cxas_scrapi.evals.callback_chain",
        "serialize_turn_snapshot",
    ),
    "CallbackEvals": ("cxas_scrapi.evals.callback_evals", "CallbackEvals"),
    "GuardrailEvals": ("cxas_scrapi.evals.guardrail_evals", "GuardrailEvals"),
    "ShadowEvals": ("cxas_scrapi.evals.shadow_evals", "ShadowEvals"),
    "ShadowReport": ("cxas_scrapi.evals.shadow_evals", "ShadowReport"),
    "ShadowTestCase": ("cxas_scrapi.evals.shadow_evals", "ShadowTestCase"),
    "ShadowUserConversation": (
        "cxas_scrapi.evals.shadow_evals",
        "ShadowUserConversation",
    ),
    "SimulationEvals": (
        "cxas_scrapi.evals.simulation_evals",
        "SimulationEvals",
    ),
    "ToolEvals": ("cxas_scrapi.evals.tool_evals", "ToolEvals"),
    "TurnEvals": ("cxas_scrapi.evals.turn_evals", "TurnEvals"),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        mod = importlib.import_module(module_path)
        val = getattr(mod, attr_name)
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AgentAssertionError",
    "BenchmarkComparisonReport",
    "CallbackChainRunner",
    "CallbackEvals",
    "CallbackTrace",
    "GuardrailEvals",
    "LlmStepTrace",
    "MockLlm",
    "ModeDiffReport",
    "SessionExpectation",
    "SessionMetricsSummary",
    "SessionSimulator",
    "ShadowEvals",
    "ShadowReport",
    "ShadowTestCase",
    "ShadowUserConversation",
    "SimulationEvals",
    "SnapshotManager",
    "StageExpectation",
    "ToolEvals",
    "ToolSpy",
    "TurnEvals",
    "TurnExpectation",
    "TurnTrace",
    "UsageMetadata",
    "build_agent_tool_declarations",
    "compare_execution_metrics",
    "expect_session",
    "expect_stage",
    "expect_turn",
    "load_local_python_tools",
    "render_agent_instruction",
    "serialize_session_snapshot",
    "serialize_stage_snapshot",
    "serialize_turn_snapshot",
]
