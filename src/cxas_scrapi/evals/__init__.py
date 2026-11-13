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
    from cxas_scrapi.evals import tool_expectations
    from cxas_scrapi.evals.callback_chain import (
        BenchmarkComparisonReport,
        CallbackChainRunner,
        CallbackTrace,
        LlmStepTrace,
        ModeDiffReport,
        SessionMetricsSummary,
        TurnTrace,
        UsageMetadata,
        compare_execution_metrics,
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
    "ModeDiffReport": ("cxas_scrapi.evals.callback_chain", "ModeDiffReport"),
    "SessionMetricsSummary": (
        "cxas_scrapi.evals.callback_chain",
        "SessionMetricsSummary",
    ),
    "TurnTrace": ("cxas_scrapi.evals.callback_chain", "TurnTrace"),
    "UsageMetadata": ("cxas_scrapi.evals.callback_chain", "UsageMetadata"),
    "compare_execution_metrics": (
        "cxas_scrapi.evals.callback_chain",
        "compare_execution_metrics",
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
    "tool_expectations": (
        "cxas_scrapi.evals.tool_expectations",
        "",
    ),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        mod = importlib.import_module(module_path)
        val = getattr(mod, attr_name) if attr_name else mod
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_IMPORTS) | set(__all__))


__all__ = [
    "BenchmarkComparisonReport",
    "CallbackChainRunner",
    "CallbackEvals",
    "CallbackTrace",
    "GuardrailEvals",
    "LlmStepTrace",
    "ModeDiffReport",
    "SessionMetricsSummary",
    "ShadowEvals",
    "ShadowReport",
    "ShadowTestCase",
    "ShadowUserConversation",
    "SimulationEvals",
    "ToolEvals",
    "TurnEvals",
    "TurnTrace",
    "UsageMetadata",
    "compare_execution_metrics",
    "tool_expectations",
]
