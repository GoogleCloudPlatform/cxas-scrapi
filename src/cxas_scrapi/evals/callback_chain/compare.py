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

"""Dual-mode (`per_callback` vs `combined`) comparison for callback chains."""

from __future__ import annotations

import contextvars
import copy
import os
import threading
from typing import TYPE_CHECKING, Any

from cxas_scrapi.evals.callback_chain.semantics import (
    format_current_date,
    json_normalize_for_diff,
    normalize_image_version,
    normalize_stage,
)
from cxas_scrapi.evals.callback_chain.traces import (
    ImageVersion,
    ModeDiffReport,
)

if TYPE_CHECKING:
    from cxas_scrapi.evals.callback_chain.runner import CallbackChainRunner
    from cxas_scrapi.utils.callback_libs import (
        CallbackContext,
        LlmRequest,
        LlmResponse,
        Tool,
        ToolContext,
    )

_ACTIVE_MODE_DIFF_COLLECTOR: contextvars.ContextVar[
    list[ModeDiffReport] | None
] = contextvars.ContextVar("_ACTIVE_MODE_DIFF_COLLECTOR", default=None)
_FALLBACK_MODE_DIFF_COLLECTOR_STACK: list[list[ModeDiffReport]] = []
_MODE_DIFF_COLLECTOR_LOCK = threading.Lock()


def _push_active_mode_diff_collector(
    collector: list[ModeDiffReport],
) -> contextvars.Token[list[ModeDiffReport] | None]:
    """Registers an active ModeDiffReport collector in ContextVar and stack."""
    with _MODE_DIFF_COLLECTOR_LOCK:
        _FALLBACK_MODE_DIFF_COLLECTOR_STACK.append(collector)
    return _ACTIVE_MODE_DIFF_COLLECTOR.set(collector)


def _pop_active_mode_diff_collector(
    token: contextvars.Token[list[ModeDiffReport] | None] | None,
    collector: list[ModeDiffReport] | None = None,
) -> None:
    """Restores the previous ModeDiffReport collector in ContextVar & stack."""
    if token is not None:
        _ACTIVE_MODE_DIFF_COLLECTOR.reset(token)
    else:
        _ACTIVE_MODE_DIFF_COLLECTOR.set(None)
    with _MODE_DIFF_COLLECTOR_LOCK:
        if collector is not None:
            for idx in range(
                len(_FALLBACK_MODE_DIFF_COLLECTOR_STACK) - 1, -1, -1
            ):
                if _FALLBACK_MODE_DIFF_COLLECTOR_STACK[idx] is collector:
                    _FALLBACK_MODE_DIFF_COLLECTOR_STACK.pop(idx)
                    break
        elif _FALLBACK_MODE_DIFF_COLLECTOR_STACK:
            _FALLBACK_MODE_DIFF_COLLECTOR_STACK.pop()


def _notify_active_mode_diff_collectors(report: ModeDiffReport) -> None:
    """Appends `report` to the active ContextVar or fallback collector."""
    collector = _ACTIVE_MODE_DIFF_COLLECTOR.get()
    if collector is None:
        with _MODE_DIFF_COLLECTOR_LOCK:
            if _FALLBACK_MODE_DIFF_COLLECTOR_STACK:
                collector = _FALLBACK_MODE_DIFF_COLLECTOR_STACK[-1]
    if collector is not None:
        with _MODE_DIFF_COLLECTOR_LOCK:
            collector.append(report)


def execute_compare_modes(
    runner: CallbackChainRunner,
    ctx: CallbackContext | ToolContext,
    stage: str,
    *,
    llm_request: LlmRequest | None = None,
    llm_response: LlmResponse | None = None,
    tool: Tool | None = None,
    tool_input: dict[str, Any] | None = None,
    tool_response: dict[str, Any] | None = None,
    image_version: ImageVersion | None = None,
    new_session: bool | None = None,
    allow_content_update: bool | None = None,
    simulate_struct_numbers: bool | None = None,
    time_zone: str | None = None,
    mock_tools: dict[str, Any] | None = None,
    consecutive_fallback_count: int | None = None,
) -> ModeDiffReport:
    """Runs `stage` under `per_callback` and `combined` modes and diffs results.

    Preserves exact `per_callback_trace.final_state` and
    `combined_trace.final_state` without mutating them in-place, storing
    canonicalized states in `report.normalized_states`.
    """
    norm_stage = normalize_stage(stage)
    eff_image_version: ImageVersion = (
        runner.image_version
        if image_version is None
        else normalize_image_version(image_version)
    )
    eff_tz = runner._effective_time_zone(time_zone)
    frozen_date = format_current_date(eff_tz)

    if (
        os.environ.get("CXAS_RECORD_FIXTURES") == "1"
        and not getattr(runner, "_suppress_fixture_recording", False)
        and (
            runner.agent_dir is not None
            or os.environ.get("CXAS_FIXTURES_DIR")
            or getattr(runner, "fixtures_dir", None)
        )
    ):
        runner.record_fixture(
            ctx,
            norm_stage,
            llm_request=llm_request,
            llm_response=llm_response,
            tool=tool,
            tool_input=tool_input,
            tool_response=tool_response,
        )

    prev_suppress = getattr(runner, "_suppress_fixture_recording", False)
    runner._suppress_fixture_recording = True
    try:
        ctx_per = copy.deepcopy(ctx)
        req_per = copy.deepcopy(llm_request)
        resp_per = copy.deepcopy(llm_response)
        tool_per = copy.deepcopy(tool)
        tin_per = copy.deepcopy(tool_input)
        tresp_per = copy.deepcopy(tool_response)

        per_out, per_trace = runner.run_stage(
            norm_stage,
            ctx_per,
            llm_request=req_per,
            llm_response=resp_per,
            tool=tool_per,
            tool_input=tin_per,
            tool_response=tresp_per,
            mode="per_callback",
            image_version=eff_image_version,
            new_session=new_session,
            allow_content_update=allow_content_update,
            simulate_struct_numbers=simulate_struct_numbers,
            time_zone=time_zone,
            mock_tools=mock_tools,
            consecutive_fallback_count=consecutive_fallback_count,
            fixed_current_date=frozen_date,
        )

        ctx_comb = copy.deepcopy(ctx)
        req_comb = copy.deepcopy(llm_request)
        resp_comb = copy.deepcopy(llm_response)
        tool_comb = copy.deepcopy(tool)
        tin_comb = copy.deepcopy(tool_input)
        tresp_comb = copy.deepcopy(tool_response)

        comb_out, comb_trace = runner.run_stage(
            norm_stage,
            ctx_comb,
            llm_request=req_comb,
            llm_response=resp_comb,
            tool=tool_comb,
            tool_input=tin_comb,
            tool_response=tresp_comb,
            mode="combined",
            image_version=eff_image_version,
            new_session=new_session,
            allow_content_update=allow_content_update,
            simulate_struct_numbers=simulate_struct_numbers,
            time_zone=time_zone,
            mock_tools=mock_tools,
            consecutive_fallback_count=consecutive_fallback_count,
            fixed_current_date=frozen_date,
        )
    finally:
        runner._suppress_fixture_recording = prev_suppress

    # Update caller ctx with per_callback final state & session initialized flag
    ctx.state = copy.deepcopy(per_trace.final_state)
    if getattr(ctx_per, "_ces_session_initialized", False):
        ctx._ces_session_initialized = True

    # Do NOT overwrite per_trace.final_state / comb_trace.final_state in-place!
    norm_per_state = json_normalize_for_diff(
        per_trace.final_state, image_version=eff_image_version
    )
    norm_comb_state = json_normalize_for_diff(
        comb_trace.final_state, image_version=eff_image_version
    )

    state_diffs: dict[str, dict[str, Any]] = {}
    all_keys = sorted(set(norm_per_state.keys()) | set(norm_comb_state.keys()))
    for k in all_keys:
        in_per = k in norm_per_state
        in_comb = k in norm_comb_state
        v_per = norm_per_state.get(k)
        v_comb = norm_comb_state.get(k)
        if not in_per or not in_comb or v_per != v_comb:
            state_diffs[k] = {
                "per_callback": v_per if in_per else "<ABSENT>",
                "combined": v_comb if in_comb else "<ABSENT>",
            }

    norm_per_resp = json_normalize_for_diff(
        per_trace.final_response, image_version=eff_image_version
    )
    norm_comb_resp = json_normalize_for_diff(
        comb_trace.final_response, image_version=eff_image_version
    )
    has_resp_diff = norm_per_resp != norm_comb_resp
    resp_diff = (
        {"per_callback": norm_per_resp, "combined": norm_comb_resp}
        if has_resp_diff
        else None
    )

    has_exec_diff = (
        per_trace.executed_callbacks != comb_trace.executed_callbacks
        or per_trace.short_circuited != comb_trace.short_circuited
        or per_trace.short_circuited_by != comb_trace.short_circuited_by
    )
    has_err_diff = (
        per_trace.failed_callback != comb_trace.failed_callback
        or per_trace.error_phase != comb_trace.error_phase
        or per_trace.error_outcome != comb_trace.error_outcome
    )

    report = ModeDiffReport(
        stage=norm_stage,
        per_callback_trace=per_trace,
        combined_trace=comb_trace,
        per_callback_response=per_out,
        combined_response=comb_out,
        has_state_diff=bool(state_diffs),
        has_response_diff=has_resp_diff,
        has_execution_order_diff=has_exec_diff,
        has_error_diff=has_err_diff,
        state_diffs=state_diffs,
        response_diff=resp_diff,
        normalized_states={
            "per_callback": norm_per_state,
            "combined": norm_comb_state,
        },
        image_version=eff_image_version,
    )
    object.__setattr__(report, "_runner", runner)
    _notify_active_mode_diff_collectors(report)
    return report
