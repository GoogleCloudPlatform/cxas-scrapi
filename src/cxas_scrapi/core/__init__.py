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

"""Core module for CXAS Scrapi."""

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cxas_scrapi.core import (
        agents,
        apps,
        callbacks,
        changelogs,
        common,
        conversation_history,
        deployments,
        evaluations,
        guardrails,
        sessions,
        tools,
        variables,
        versions,
    )
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

_DYNAMIC_IMPORTS: dict[str, str] = {
    "Agents": "cxas_scrapi.core.agents",
    "Apps": "cxas_scrapi.core.apps",
    "Callbacks": "cxas_scrapi.core.callbacks",
    "Changelogs": "cxas_scrapi.core.changelogs",
    "Common": "cxas_scrapi.core.common",
    "ConversationHistory": "cxas_scrapi.core.conversation_history",
    "Deployments": "cxas_scrapi.core.deployments",
    "Evaluations": "cxas_scrapi.core.evaluations",
    "Guardrails": "cxas_scrapi.core.guardrails",
    "Sessions": "cxas_scrapi.core.sessions",
    "Tools": "cxas_scrapi.core.tools",
    "Variables": "cxas_scrapi.core.variables",
    "Versions": "cxas_scrapi.core.versions",
    "agents": "cxas_scrapi.core.agents",
    "apps": "cxas_scrapi.core.apps",
    "callbacks": "cxas_scrapi.core.callbacks",
    "changelogs": "cxas_scrapi.core.changelogs",
    "common": "cxas_scrapi.core.common",
    "conversation_history": "cxas_scrapi.core.conversation_history",
    "deployments": "cxas_scrapi.core.deployments",
    "evaluations": "cxas_scrapi.core.evaluations",
    "guardrails": "cxas_scrapi.core.guardrails",
    "sessions": "cxas_scrapi.core.sessions",
    "tools": "cxas_scrapi.core.tools",
    "variables": "cxas_scrapi.core.variables",
    "versions": "cxas_scrapi.core.versions",
}


def __getattr__(name: str) -> Any:
    if name in _DYNAMIC_IMPORTS:
        module_path = _DYNAMIC_IMPORTS[name]
        module = importlib.import_module(module_path)
        value = (
            module
            if module_path.endswith(f".{name}")
            else getattr(module, name)
        )
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals().keys()) | set(_DYNAMIC_IMPORTS.keys()))


__all__ = [
    "Agents",
    "Apps",
    "Callbacks",
    "Changelogs",
    "Common",
    "ConversationHistory",
    "Deployments",
    "Evaluations",
    "Guardrails",
    "Sessions",
    "Tools",
    "Variables",
    "Versions",
    "agents",
    "apps",
    "callbacks",
    "changelogs",
    "common",
    "conversation_history",
    "deployments",
    "evaluations",
    "guardrails",
    "sessions",
    "tools",
    "variables",
    "versions",
]
