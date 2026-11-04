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
    from cxas_scrapi.evals.callback_chain.expect import expect
    from cxas_scrapi.evals.callback_chain.hydration import (
        STATE_REMOVED_SENTINEL,
        HydratedAgentConfig,
        StateUpdateMode,
        build_agent_tool_declarations,
        build_contents_with_state_updates,
        compute_state_delta,
        extract_in_scope_params,
        get_agent_in_scope_params,
        load_agent_from_app_dir,
        load_instruction_sources,
        load_local_python_tools,
        render_agent_instruction,
        sanitize_state_map,
    )
    from cxas_scrapi.evals.callback_chain.lint import (
        CallbackChainLintFinding,
        lint_app_dir,
        lint_stage_chain,
    )
    from cxas_scrapi.evals.callback_chain.matchers import (
        SessionExpectation,
        StageExpectation,
        TurnExpectation,
        expect_session,
        expect_stage,
        expect_turn,
    )
    from cxas_scrapi.evals.callback_chain.mocks import (
        MockLlm,
        ToolSpy,
        deserialize_llm_response,
        serialize_llm_response,
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
    from cxas_scrapi.evals.callback_chain.snapshots import (
        SnapshotManager,
        serialize_session_snapshot,
        serialize_stage_snapshot,
        serialize_turn_snapshot,
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
    from cxas_scrapi.evals.callback_chain.turn import (
        SessionSimulator,
        register_llm_resolver,
        resolve_llm,
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
    "HydratedAgentConfig": "cxas_scrapi.evals.callback_chain.hydration",
    "ImageVersion": "cxas_scrapi.evals.callback_chain.traces",
    "LlmStepTrace": "cxas_scrapi.evals.callback_chain.traces",
    "MockLlm": "cxas_scrapi.evals.callback_chain.mocks",
    "ModeDiffReport": "cxas_scrapi.evals.callback_chain.traces",
    "STAGE_TO_AGENT_JSON_KEYS": "cxas_scrapi.evals.callback_chain.semantics",
    "STAGE_TO_FUNC_NAME": "cxas_scrapi.evals.callback_chain.semantics",
    "STAGE_TO_GLOBAL_JSON_KEYS": "cxas_scrapi.evals.callback_chain.semantics",
    "STATE_REMOVED_SENTINEL": "cxas_scrapi.evals.callback_chain.hydration",
    "SessionExpectation": "cxas_scrapi.evals.callback_chain.matchers",
    "SessionMetricsSummary": "cxas_scrapi.evals.callback_chain.traces",
    "SessionSimulator": "cxas_scrapi.evals.callback_chain.turn",
    "SnapshotManager": "cxas_scrapi.evals.callback_chain.snapshots",
    "StageExpectation": "cxas_scrapi.evals.callback_chain.matchers",
    "StageType": "cxas_scrapi.evals.callback_chain.traces",
    "StateUpdateMode": "cxas_scrapi.evals.callback_chain.hydration",
    "ToolSpy": "cxas_scrapi.evals.callback_chain.mocks",
    "ToolStepTrace": "cxas_scrapi.evals.callback_chain.traces",
    "TurnExpectation": "cxas_scrapi.evals.callback_chain.matchers",
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
    "build_agent_tool_declarations": (
        "cxas_scrapi.evals.callback_chain.hydration"
    ),
    "build_contents_with_state_updates": (
        "cxas_scrapi.evals.callback_chain.hydration"
    ),
    "compare_execution_metrics": "cxas_scrapi.evals.callback_chain.traces",
    "compute_state_delta": "cxas_scrapi.evals.callback_chain.hydration",
    "deserialize_llm_response": "cxas_scrapi.evals.callback_chain.mocks",
    "detect_state_aliasing": "cxas_scrapi.evals.callback_chain.semantics",
    "expect": "cxas_scrapi.evals.callback_chain.expect",
    "expect_session": "cxas_scrapi.evals.callback_chain.matchers",
    "expect_stage": "cxas_scrapi.evals.callback_chain.matchers",
    "expect_turn": "cxas_scrapi.evals.callback_chain.matchers",
    "extract_in_scope_params": "cxas_scrapi.evals.callback_chain.hydration",
    "get_active_execution_context": "cxas_scrapi.evals.callback_chain.traces",
    "get_active_tool_call_log": "cxas_scrapi.evals.callback_chain.traces",
    "get_agent_in_scope_params": "cxas_scrapi.evals.callback_chain.hydration",
    "lint_app_dir": "cxas_scrapi.evals.callback_chain.lint",
    "lint_stage_chain": "cxas_scrapi.evals.callback_chain.lint",
    "load_agent_from_app_dir": "cxas_scrapi.evals.callback_chain.hydration",
    "load_instruction_sources": "cxas_scrapi.evals.callback_chain.hydration",
    "load_local_python_tools": "cxas_scrapi.evals.callback_chain.hydration",
    "normalize_image_version": "cxas_scrapi.evals.callback_chain.semantics",
    "normalize_stage": "cxas_scrapi.evals.callback_chain.semantics",
    "register_llm_resolver": "cxas_scrapi.evals.callback_chain.turn",
    "render_agent_instruction": "cxas_scrapi.evals.callback_chain.hydration",
    "resolve_llm": "cxas_scrapi.evals.callback_chain.turn",
    "sanitize_state_map": "cxas_scrapi.evals.callback_chain.hydration",
    "serialize_llm_response": "cxas_scrapi.evals.callback_chain.mocks",
    "serialize_session_snapshot": "cxas_scrapi.evals.callback_chain.snapshots",
    "serialize_stage_snapshot": "cxas_scrapi.evals.callback_chain.snapshots",
    "serialize_turn_snapshot": "cxas_scrapi.evals.callback_chain.snapshots",
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
    "STATE_REMOVED_SENTINEL",
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
    "HydratedAgentConfig",
    "ImageVersion",
    "LlmStepTrace",
    "MockLlm",
    "ModeDiffReport",
    "SessionExpectation",
    "SessionMetricsSummary",
    "SessionSimulator",
    "SnapshotManager",
    "StageExpectation",
    "StageType",
    "StateUpdateMode",
    "ToolSpy",
    "ToolStepTrace",
    "TurnExpectation",
    "TurnTrace",
    "UsageMetadata",
    "_synthesize_chained_callback_module",
    "build_agent_tool_declarations",
    "build_contents_with_state_updates",
    "compare_execution_metrics",
    "compute_state_delta",
    "deserialize_llm_response",
    "detect_state_aliasing",
    "expect",
    "expect_session",
    "expect_stage",
    "expect_turn",
    "extract_in_scope_params",
    "get_active_execution_context",
    "get_active_tool_call_log",
    "get_agent_in_scope_params",
    "lint_app_dir",
    "lint_stage_chain",
    "load_agent_from_app_dir",
    "load_instruction_sources",
    "load_local_python_tools",
    "normalize_image_version",
    "normalize_stage",
    "register_llm_resolver",
    "render_agent_instruction",
    "resolve_llm",
    "sanitize_state_map",
    "serialize_llm_response",
    "serialize_session_snapshot",
    "serialize_stage_snapshot",
    "serialize_turn_snapshot",
    "submit_in_context",
]
