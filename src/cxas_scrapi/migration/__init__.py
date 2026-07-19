from __future__ import annotations

"""Migration package for porting DFCX to CXAS."""

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
from typing import Any

_LAZY_EXPORTS = {
    "AIAugment": "cxas_scrapi.migration.ai_augment",
    "ConversationTrace": "cxas_scrapi.migration.dfcx_conversation_runner",
    "ConversationTurn": "cxas_scrapi.migration.dfcx_conversation_runner",
    "DFCXConversationRunner": "cxas_scrapi.migration.dfcx_conversation_runner",
    "BaseDFCXClient": "cxas_scrapi.migration.dfcx_exporter",
    "ConversationalAgentsAPI": "cxas_scrapi.migration.dfcx_exporter",
    "DFCXAgentExporter": "cxas_scrapi.migration.dfcx_exporter",
    "DFCXAgents": "cxas_scrapi.migration.dfcx_exporter",
    "DFCXGenerativeSettings": "cxas_scrapi.migration.dfcx_exporter",
    "DFCXPlaybooks": "cxas_scrapi.migration.dfcx_exporter",
    "DFCXTools": "cxas_scrapi.migration.dfcx_exporter",
    "DFCXMigrationReporter": "cxas_scrapi.migration.dfcx_migration_reporter",
    "FlowDependencyResolver": "cxas_scrapi.migration.flow_visualizer",
    "FlowTreeVisualizer": "cxas_scrapi.migration.flow_visualizer",
    "HighLevelGraphVisualizer": "cxas_scrapi.migration.graph_visualizer",
    "MainVisualizer": "cxas_scrapi.migration.main_visualizer",
    "PlaybookTreeVisualizer": "cxas_scrapi.migration.playbook_visualizer",
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        mod = importlib.import_module(_LAZY_EXPORTS[name])
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = list(_LAZY_EXPORTS.keys())
