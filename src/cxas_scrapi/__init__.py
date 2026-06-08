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

# To keep the CLI startup time under 130ms, we lazy-load packages at runtime.
# Using `if TYPE_CHECKING:` allows IDEs, type checkers, and static analyzers
# to see the eager imports statically for full autocompletion and type-safety.
# At runtime, TYPE_CHECKING is False, triggering the dynamic lazy loader in
# __getattr__.
if TYPE_CHECKING:
    from cxas_scrapi.core.agents import Agents
    from cxas_scrapi.core.apps import Apps
    from cxas_scrapi.core.callbacks import Callbacks
    from cxas_scrapi.core.changelogs import Changelogs
    from cxas_scrapi.core.common import Common
    from cxas_scrapi.core.conversation_history import ConversationHistory
    from cxas_scrapi.core.deployments import Deployments
    from cxas_scrapi.core.evaluations import Evaluations
    from cxas_scrapi.core.guardrails import Guardrails
    from cxas_scrapi.core.sessions import Sessions
    from cxas_scrapi.core.tools import Tools
    from cxas_scrapi.core.variables import Variables
    from cxas_scrapi.core.versions import Versions
    from cxas_scrapi.evals.callback_evals import CallbackEvals
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
    from cxas_scrapi.utils.secret_manager_utils import SecretManagerUtils
else:
    _LAZY_IMPORTS = {
        "Agents": "cxas_scrapi.core.agents",
        "Apps": "cxas_scrapi.core.apps",
        "BaseDFCXClient": "cxas_scrapi.migration.dfcx_exporter",
        "CallbackEvals": "cxas_scrapi.evals.callback_evals",
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
        "MainVisualizer": "cxas_scrapi.migration.main_visualizer",
        "PlaybookTreeVisualizer": "cxas_scrapi.migration.playbook_visualizer",
        "SecretManagerUtils": "cxas_scrapi.utils.secret_manager_utils",
        "Sessions": "cxas_scrapi.core.sessions",
        "SimulationEvals": "cxas_scrapi.evals.simulation_evals",
        "ToolEvals": "cxas_scrapi.evals.tool_evals",
        "Tools": "cxas_scrapi.core.tools",
        "TurnEvals": "cxas_scrapi.evals.turn_evals",
        "Variables": "cxas_scrapi.core.variables",
        "Versions": "cxas_scrapi.core.versions",
    }

    def __getattr__(name: str) -> Any:
        if name in _LAZY_IMPORTS:
            module_path = _LAZY_IMPORTS[name]
            module = importlib.import_module(module_path)
            return getattr(module, name)
        raise AttributeError(f"module {__name__} has no attribute {name}")


__all__ = [
    "Agents",
    "Apps",
    "BaseDFCXClient",
    "CallbackEvals",
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
    "MainVisualizer",
    "PlaybookTreeVisualizer",
    "SecretManagerUtils",
    "Sessions",
    "SimulationEvals",
    "ToolEvals",
    "Tools",
    "TurnEvals",
    "Variables",
    "Versions",
]


__all__ = [
    "Agents",
    "Apps",
    "BaseDFCXClient",
    "CallbackEvals",
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
    "MainVisualizer",
    "PlaybookTreeVisualizer",
    "SecretManagerUtils",
    "Sessions",
    "SimulationEvals",
    "ToolEvals",
    "Tools",
    "TurnEvals",
    "Variables",
    "Versions",
]
