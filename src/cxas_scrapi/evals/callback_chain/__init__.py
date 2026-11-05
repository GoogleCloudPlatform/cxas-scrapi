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

"""CES multi-callback chain evaluation package."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cxas_scrapi.evals.callback_chain.errors import AgentAssertionError
    from cxas_scrapi.evals.callback_chain.lint import (
        CallbackChainLintFinding,
        lint_app_dir,
        lint_stage_chain,
    )
    from cxas_scrapi.evals.callback_chain.runner import CallbackChainRunner
    from cxas_scrapi.evals.callback_chain.semantics import (
        STAGE_TO_AGENT_JSON_KEYS,
        STAGE_TO_FUNC_NAME,
        STAGE_TO_GLOBAL_JSON_KEYS,
        UNSUPPORTED_GLOBAL_JSON_KEYS,
        _synthesize_chained_callback_module,
        detect_state_aliasing,
        normalize_image_version,
        normalize_stage,
    )
    from cxas_scrapi.evals.callback_chain.traces import (
        CHAIN_LLM_ENV,
        CHAIN_MODE_ENV,
        VALID_CHANNELS,
        VALID_MODALITIES,
        BenchmarkComparisonReport,
        CallbackSpec,
        CallbackStepTrace,
        CallbackTrace,
        ErrorPhase,
        ExecutionMode,
        ImageVersion,
        LlmStepTrace,
        ModeDiffReport,
        SessionMetricsSummary,
        StageType,
        ToolStepTrace,
        TurnTrace,
        compare_execution_metrics,
        get_active_execution_context,
        get_active_tool_call_log,
        submit_in_context,
    )
    from cxas_scrapi.utils.callback_libs import UsageMetadata

_LAZY_IMPORTS: dict[str, str] = {
    "AgentAssertionError": "cxas_scrapi.evals.callback_chain.errors",
    "BenchmarkComparisonReport": "cxas_scrapi.evals.callback_chain.traces",
    "CHAIN_LLM_ENV": "cxas_scrapi.evals.callback_chain.traces",
    "CHAIN_MODE_ENV": "cxas_scrapi.evals.callback_chain.traces",
    "CallbackChainLintFinding": "cxas_scrapi.evals.callback_chain.lint",
    "CallbackChainRunner": "cxas_scrapi.evals.callback_chain.runner",
    "CallbackSpec": "cxas_scrapi.evals.callback_chain.traces",
    "CallbackStepTrace": "cxas_scrapi.evals.callback_chain.traces",
    "CallbackTrace": "cxas_scrapi.evals.callback_chain.traces",
    "ErrorPhase": "cxas_scrapi.evals.callback_chain.traces",
    "ExecutionMode": "cxas_scrapi.evals.callback_chain.traces",
    "ImageVersion": "cxas_scrapi.evals.callback_chain.traces",
    "LlmStepTrace": "cxas_scrapi.evals.callback_chain.traces",
    "ModeDiffReport": "cxas_scrapi.evals.callback_chain.traces",
    "STAGE_TO_AGENT_JSON_KEYS": "cxas_scrapi.evals.callback_chain.semantics",
    "STAGE_TO_FUNC_NAME": "cxas_scrapi.evals.callback_chain.semantics",
    "STAGE_TO_GLOBAL_JSON_KEYS": "cxas_scrapi.evals.callback_chain.semantics",
    "SessionMetricsSummary": "cxas_scrapi.evals.callback_chain.traces",
    "StageType": "cxas_scrapi.evals.callback_chain.traces",
    "ToolStepTrace": "cxas_scrapi.evals.callback_chain.traces",
    "TurnTrace": "cxas_scrapi.evals.callback_chain.traces",
    "UNSUPPORTED_GLOBAL_JSON_KEYS": (
        "cxas_scrapi.evals.callback_chain.semantics"
    ),
    "UsageMetadata": "cxas_scrapi.utils.callback_libs",
    "VALID_CHANNELS": "cxas_scrapi.evals.callback_chain.traces",
    "VALID_MODALITIES": "cxas_scrapi.evals.callback_chain.traces",
    "_synthesize_chained_callback_module": (
        "cxas_scrapi.evals.callback_chain.semantics"
    ),
    "compare_execution_metrics": "cxas_scrapi.evals.callback_chain.traces",
    "detect_state_aliasing": "cxas_scrapi.evals.callback_chain.semantics",
    "get_active_execution_context": "cxas_scrapi.evals.callback_chain.traces",
    "get_active_tool_call_log": "cxas_scrapi.evals.callback_chain.traces",
    "lint_app_dir": "cxas_scrapi.evals.callback_chain.lint",
    "lint_stage_chain": "cxas_scrapi.evals.callback_chain.lint",
    "normalize_image_version": "cxas_scrapi.evals.callback_chain.semantics",
    "normalize_stage": "cxas_scrapi.evals.callback_chain.semantics",
    "submit_in_context": "cxas_scrapi.evals.callback_chain.traces",
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_IMPORTS:
        mod = importlib.import_module(_LAZY_IMPORTS[name])
        val = getattr(mod, name)
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


__all__ = [
    "CHAIN_LLM_ENV",
    "CHAIN_MODE_ENV",
    "STAGE_TO_AGENT_JSON_KEYS",
    "STAGE_TO_FUNC_NAME",
    "STAGE_TO_GLOBAL_JSON_KEYS",
    "UNSUPPORTED_GLOBAL_JSON_KEYS",
    "VALID_CHANNELS",
    "VALID_MODALITIES",
    "AgentAssertionError",
    "BenchmarkComparisonReport",
    "CallbackChainLintFinding",
    "CallbackChainRunner",
    "CallbackSpec",
    "CallbackStepTrace",
    "CallbackTrace",
    "ErrorPhase",
    "ExecutionMode",
    "ImageVersion",
    "LlmStepTrace",
    "ModeDiffReport",
    "SessionMetricsSummary",
    "StageType",
    "ToolStepTrace",
    "TurnTrace",
    "UsageMetadata",
    "_synthesize_chained_callback_module",
    "compare_execution_metrics",
    "detect_state_aliasing",
    "get_active_execution_context",
    "get_active_tool_call_log",
    "lint_app_dir",
    "lint_stage_chain",
    "normalize_image_version",
    "normalize_stage",
    "submit_in_context",
]
