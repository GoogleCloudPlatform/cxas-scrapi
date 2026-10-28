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

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cxas_scrapi.core.agents import Agents
    from cxas_scrapi.core.apps import Apps
    from cxas_scrapi.core.callbacks import Callbacks
    from cxas_scrapi.core.changelogs import Changelogs
    from cxas_scrapi.core.common import Common
    from cxas_scrapi.core.conversation_history import ConversationHistory
    from cxas_scrapi.core.cxas_insights import CXASInsights
    from cxas_scrapi.core.deployments import Deployments
    from cxas_scrapi.core.evaluations import Evaluations
    from cxas_scrapi.core.guardrails import Guardrails
    from cxas_scrapi.core.sessions import Sessions
    from cxas_scrapi.core.tools import Tools
    from cxas_scrapi.core.variables import Variables
    from cxas_scrapi.core.versions import Versions
    from cxas_scrapi.evals.callback_evals import (
        AgentAssertionError,
        BenchmarkComparisonReport,
        CallbackChainRunner,
        CallbackEvals,
        CallbackTrace,
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
    from cxas_scrapi.evals.guardrail_evals import GuardrailEvals
    from cxas_scrapi.evals.simulation_evals import SimulationEvals
    from cxas_scrapi.evals.tool_evals import ToolEvals
    from cxas_scrapi.evals.turn_evals import TurnEvals

    # Migration / Visualization
    from cxas_scrapi.migration.dfcx_exporter import (
        BaseDFCXClient,
        ConversationalAgentsAPI,
        DFCXAgentExporter,
        DFCXAgents,
        DFCXGenerativeSettings,
        DFCXPlaybooks,
        DFCXTools,
    )
    from cxas_scrapi.migration.flow_visualizer import (
        FlowDependencyResolver,
        FlowTreeVisualizer,
    )
    from cxas_scrapi.migration.graph_visualizer import HighLevelGraphVisualizer
    from cxas_scrapi.migration.main_visualizer import MainVisualizer
    from cxas_scrapi.migration.playbook_visualizer import PlaybookTreeVisualizer
    from cxas_scrapi.utils.changelog_utils import ChangelogUtils

    # Utilities
    from cxas_scrapi.utils.eval_utils import EvalUtils
    from cxas_scrapi.utils.google_sheets_utils import GoogleSheetsUtils
    from cxas_scrapi.utils.insights_reconciler import InsightsReconciler
    from cxas_scrapi.utils.metrics_extractor import MetricsExtractor
    from cxas_scrapi.utils.scorecard_eval_runner import ScorecardEvalRunner
    from cxas_scrapi.utils.secret_manager_utils import SecretManagerUtils
else:
    _LAZY_IMPORTS = {
        "AgentAssertionError": "cxas_scrapi.evals.callback_evals",
        "Agents": "cxas_scrapi.core.agents",
        "Apps": "cxas_scrapi.core.apps",
        "BaseDFCXClient": "cxas_scrapi.migration.dfcx_exporter",
        "BenchmarkComparisonReport": "cxas_scrapi.evals.callback_evals",
        "CXASInsights": "cxas_scrapi.core.cxas_insights",
        "CallbackChainRunner": "cxas_scrapi.evals.callback_evals",
        "CallbackEvals": "cxas_scrapi.evals.callback_evals",
        "CallbackTrace": "cxas_scrapi.evals.callback_evals",
        "Callbacks": "cxas_scrapi.core.callbacks",
        "ChangelogUtils": "cxas_scrapi.utils.changelog_utils",
        "Changelogs": "cxas_scrapi.core.changelogs",
        "Common": "cxas_scrapi.core.common",
        "ConversationHistory": "cxas_scrapi.core.conversation_history",
        "ConversationalAgentsAPI": "cxas_scrapi.migration.dfcx_exporter",
        "DFCXAgentExporter": "cxas_scrapi.migration.dfcx_exporter",
        "DFCXAgents": "cxas_scrapi.migration.dfcx_exporter",
        "DFCXGenerativeSettings": "cxas_scrapi.migration.dfcx_exporter",
        "DFCXPlaybooks": "cxas_scrapi.migration.dfcx_exporter",
        "DFCXTools": "cxas_scrapi.migration.dfcx_exporter",
        "Deployments": "cxas_scrapi.core.deployments",
        "EvalUtils": "cxas_scrapi.utils.eval_utils",
        "Evaluations": "cxas_scrapi.core.evaluations",
        "FlowDependencyResolver": "cxas_scrapi.migration.flow_visualizer",
        "FlowTreeVisualizer": "cxas_scrapi.migration.flow_visualizer",
        "GoogleSheetsUtils": "cxas_scrapi.utils.google_sheets_utils",
        "GuardrailEvals": "cxas_scrapi.evals.guardrail_evals",
        "Guardrails": "cxas_scrapi.core.guardrails",
        "HighLevelGraphVisualizer": "cxas_scrapi.migration.graph_visualizer",
        "InsightsReconciler": "cxas_scrapi.utils.insights_reconciler",
        "MainVisualizer": "cxas_scrapi.migration.main_visualizer",
        "MetricsExtractor": "cxas_scrapi.utils.metrics_extractor",
        "MockLlm": "cxas_scrapi.evals.callback_evals",
        "ModeDiffReport": "cxas_scrapi.evals.callback_evals",
        "PlaybookTreeVisualizer": "cxas_scrapi.migration.playbook_visualizer",
        "ScorecardEvalRunner": "cxas_scrapi.utils.scorecard_eval_runner",
        "SecretManagerUtils": "cxas_scrapi.utils.secret_manager_utils",
        "SessionExpectation": "cxas_scrapi.evals.callback_evals",
        "SessionMetricsSummary": "cxas_scrapi.evals.callback_evals",
        "SessionSimulator": "cxas_scrapi.evals.callback_evals",
        "Sessions": "cxas_scrapi.core.sessions",
        "SimulationEvals": "cxas_scrapi.evals.simulation_evals",
        "SnapshotManager": "cxas_scrapi.evals.callback_evals",
        "StageExpectation": "cxas_scrapi.evals.callback_evals",
        "ToolEvals": "cxas_scrapi.evals.tool_evals",
        "ToolSpy": "cxas_scrapi.evals.callback_evals",
        "Tools": "cxas_scrapi.core.tools",
        "TurnEvals": "cxas_scrapi.evals.turn_evals",
        "TurnExpectation": "cxas_scrapi.evals.callback_evals",
        "TurnTrace": "cxas_scrapi.evals.callback_evals",
        "UsageMetadata": "cxas_scrapi.evals.callback_evals",
        "Variables": "cxas_scrapi.core.variables",
        "Versions": "cxas_scrapi.core.versions",
        "build_agent_tool_declarations": "cxas_scrapi.evals.callback_evals",
        "compare_execution_metrics": "cxas_scrapi.evals.callback_evals",
        "expect_session": "cxas_scrapi.evals.callback_evals",
        "expect_stage": "cxas_scrapi.evals.callback_evals",
        "expect_turn": "cxas_scrapi.evals.callback_evals",
        "load_local_python_tools": "cxas_scrapi.evals.callback_evals",
        "render_agent_instruction": "cxas_scrapi.evals.callback_evals",
        "serialize_session_snapshot": "cxas_scrapi.evals.callback_evals",
        "serialize_stage_snapshot": "cxas_scrapi.evals.callback_evals",
        "serialize_turn_snapshot": "cxas_scrapi.evals.callback_evals",
    }

    def __getattr__(name: str) -> Any:
        if name in _LAZY_IMPORTS:
            module_path = _LAZY_IMPORTS[name]
            module = importlib.import_module(module_path)
            return getattr(module, name)
        raise AttributeError(f"module {__name__} has no attribute {name}")


__all__ = [
    "AgentAssertionError",
    "Agents",
    "Apps",
    "BaseDFCXClient",
    "BenchmarkComparisonReport",
    "CXASInsights",
    "CallbackChainRunner",
    "CallbackEvals",
    "CallbackTrace",
    "Callbacks",
    "ChangelogUtils",
    "Changelogs",
    "Common",
    "ConversationHistory",
    "ConversationalAgentsAPI",
    "DFCXAgentExporter",
    "DFCXAgents",
    "DFCXGenerativeSettings",
    "DFCXPlaybooks",
    "DFCXTools",
    "Deployments",
    "EvalUtils",
    "Evaluations",
    "FlowDependencyResolver",
    "FlowTreeVisualizer",
    "GoogleSheetsUtils",
    "GuardrailEvals",
    "Guardrails",
    "HighLevelGraphVisualizer",
    "InsightsReconciler",
    "MainVisualizer",
    "MetricsExtractor",
    "MockLlm",
    "ModeDiffReport",
    "PlaybookTreeVisualizer",
    "ScorecardEvalRunner",
    "SecretManagerUtils",
    "SessionExpectation",
    "SessionMetricsSummary",
    "SessionSimulator",
    "Sessions",
    "SimulationEvals",
    "SnapshotManager",
    "StageExpectation",
    "ToolEvals",
    "ToolSpy",
    "Tools",
    "TurnEvals",
    "TurnExpectation",
    "TurnTrace",
    "UsageMetadata",
    "Variables",
    "Versions",
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
