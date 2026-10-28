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

from cxas_scrapi.evals.callback_chain.expect import expect
from cxas_scrapi.evals.callback_chain.hydration import (
    STATE_REMOVED_SENTINEL,
    HydratedAgentConfig,
    StateUpdateMode,
    build_agent_tool_declarations,
    build_contents_with_state_updates,
    compute_state_delta,
    extract_in_scope_params,
    get_active_execution_context,
    get_active_tool_call_log,
    get_agent_in_scope_params,
    load_agent_from_app_dir,
    load_local_python_tools,
    render_agent_instruction,
    sanitize_state_map,
)
from cxas_scrapi.evals.callback_chain.lint import (
    CallbackChainLintFinding,
    lint_stage_chain,
)
from cxas_scrapi.evals.callback_chain.matchers import (
    AgentAssertionError,
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
)
from cxas_scrapi.evals.callback_chain.turn import (
    SessionSimulator,
    register_llm_resolver,
    resolve_llm,
)
from cxas_scrapi.utils.callback_libs import UsageMetadata

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
    "lint_stage_chain",
    "load_agent_from_app_dir",
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
]
