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

"""Exception handling and fallback/end-session resolution for CES runtime."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from cxas_scrapi.evals.callback_chain.traces import (
    CallbackTrace,
    ModeDiffReport,
    TurnTrace,
)
from cxas_scrapi.utils.callback_libs import (
    CallbackContext,
    Content,
    LlmResponse,
    Part,
)

if TYPE_CHECKING:
    from cxas_scrapi.evals.callback_chain.lint import CallbackChainLintFinding
    from cxas_scrapi.evals.callback_chain.runner import CallbackChainRunner
    from cxas_scrapi.evals.callback_chain.traces import ErrorOutcome, StageType

END_SESSION_REASON_ERRORS = "errors"
END_SESSION_REASON_ESCALATION = "escalation after errors"
DEFAULT_FALLBACK_MESSAGE = "Sorry, something went wrong."

_INT_TO_STRATEGY: dict[int, str] = {
    0: "ERROR_HANDLING_STRATEGY_UNSPECIFIED",
    1: "NONE",
    2: "FALLBACK_RESPONSE",
    3: "END_SESSION",
}


def resolve_error_outcome(
    stage: StageType,
    ctx: CallbackContext,
    *,
    error_handling_settings: dict[str, Any] | None = None,
    app_config: dict[str, Any] | None = None,
    consecutive_fallback_count: int = 0,
    enable_exception_handling: bool = True,
    force_enable_exception_handling: bool = False,
) -> tuple[ErrorOutcome, Any, int]:
    """Resolves `error_outcome`, response payload, and updated fallback counter.

    Aligns with the CES runtime error-handling semantics:
    - `enable_exception_handling` controls whether callback exception handling
      is active (default `True`).
    - `force_enable_exception_handling` (default `False`), when `True`,
      unconditionally enables fallback regardless of
      `enable_exception_handling` or `errorHandlingStrategy`.
    - In Protobuf, `app.errorHandlingSettings` is always present as a message
      whose default `errorHandlingStrategy` is
      `ERROR_HANDLING_STRATEGY_UNSPECIFIED` (treated identically to
      `FALLBACK_RESPONSE` with `maxFallbackAttempts = 0`).
    """
    ehs = error_handling_settings or {}
    raw_strategy = ehs.get("errorHandlingStrategy")
    if raw_strategy is None:
        raw_strategy = ehs.get("error_handling_strategy")
    if isinstance(raw_strategy, int) and not isinstance(raw_strategy, bool):
        strategy = _INT_TO_STRATEGY.get(
            raw_strategy, "ERROR_HANDLING_STRATEGY_UNSPECIFIED"
        )
    elif isinstance(raw_strategy, str) and raw_strategy:
        strategy = raw_strategy
    else:
        strategy = "ERROR_HANDLING_STRATEGY_UNSPECIFIED"
    fb_cfg = (
        ehs.get("fallbackResponseConfig")
        or ehs.get("fallback_response_config")
        or {}
    )
    raw_max_attempts = fb_cfg.get(
        "maxFallbackAttempts",
        fb_cfg.get("max_fallback_attempts", 0),
    )
    try:
        max_attempts = max(0, int(raw_max_attempts or 0))
    except (TypeError, ValueError):
        max_attempts = 0

    is_fallback_strategy = strategy in (
        "FALLBACK_RESPONSE",
        "ERROR_HANDLING_STRATEGY_UNSPECIFIED",
        "",
    )
    should_return_fallback = is_fallback_strategy and (
        max_attempts == 0 or consecutive_fallback_count < max_attempts
    )
    should_end_session = strategy == "END_SESSION" or (
        is_fallback_strategy
        and max_attempts != 0
        and consecutive_fallback_count >= max_attempts
    )

    is_fallback_enabled = force_enable_exception_handling or (
        enable_exception_handling and should_return_fallback
    )
    is_end_session_enabled = enable_exception_handling and should_end_session

    if is_fallback_enabled:
        next_count = consecutive_fallback_count + 1
        if stage in ("before_tool", "after_tool"):
            # Tool stages return {"error": True} as the fallback response.
            return "fallback", {"error": True}, next_count

        custom_msgs = (
            fb_cfg.get("customFallbackMessages")
            or fb_cfg.get("custom_fallback_messages")
            or {}
        )
        lang = (
            ctx.language_code
            or (
                (app_config or {}).get("languageSettings")
                or (app_config or {}).get("language_settings")
                or {}
            ).get("defaultLanguageCode")
            or "en-US"
        )
        fallback_text = (
            custom_msgs.get(lang)
            or custom_msgs.get("en-US")
            or DEFAULT_FALLBACK_MESSAGE
        )
        if stage in ("before_agent", "after_agent"):
            return (
                "fallback",
                Content(parts=[Part.from_text(fallback_text)], role="model"),
                next_count,
            )
        return (
            "fallback",
            LlmResponse.from_parts([Part.from_text(fallback_text)]),
            next_count,
        )

    if is_end_session_enabled:
        # Tool stages do not produce an end_session response payload.
        if stage in ("before_tool", "after_tool"):
            return "error", None, consecutive_fallback_count

        next_count = consecutive_fallback_count + 1
        end_cfg = (
            ehs.get("endSessionConfig") or ehs.get("end_session_config") or {}
        )
        escalated = bool(
            end_cfg.get(
                "escalateSession", end_cfg.get("escalate_session", False)
            )
        )
        # Resolve endSessionReason by whether fallback attempts were exhausted.
        if (
            is_fallback_strategy
            and max_attempts != 0
            and consecutive_fallback_count >= max_attempts
        ):
            reason = END_SESSION_REASON_ESCALATION
        else:
            reason = END_SESSION_REASON_ERRORS

        end_part = Part.from_end_session(reason=reason, escalated=escalated)
        if stage in ("before_agent", "after_agent"):
            return (
                "end_session",
                Content(parts=[end_part], role="model"),
                next_count,
            )
        return "end_session", LlmResponse.from_parts([end_part]), next_count

    return "error", None, consecutive_fallback_count


def _iter_stage_traces_from_target(
    target: Any,
) -> list[tuple[str, CallbackTrace]]:
    """Extracts ordered `(stage_label, CallbackTrace)` pairs from any trace
    target.
    """
    if target is None:
        return []
    if isinstance(target, CallbackTrace):
        return [(target.stage, target)]
    if isinstance(target, ModeDiffReport):
        return [
            (f"{target.stage} [per_callback]", target.per_callback_trace),
            (f"{target.stage} [combined]", target.combined_trace),
        ]
    if isinstance(target, TurnTrace):
        out: list[tuple[str, CallbackTrace]] = []
        if target.before_agent_trace is not None:
            out.append(("before_agent", target.before_agent_trace))
        if target.llm_steps:
            for ls in target.llm_steps:
                hop_lbl = f"hop#{ls.step_index + 1}"
                out.append((f"{hop_lbl}.before_model", ls.before_model_trace))
                if ls.after_model_trace is not None:
                    out.append((f"{hop_lbl}.after_model", ls.after_model_trace))
        else:
            out.append(("before_model", target.before_model_trace))
            if target.after_model_trace is not None:
                out.append(("after_model", target.after_model_trace))
        for idx, ts in enumerate(target.tool_steps, 1):
            t_lbl = f"tool#{idx}({ts.tool_name})"
            out.append((f"{t_lbl}.before_tool", ts.before_tool_trace))
            if ts.after_tool_trace is not None:
                out.append((f"{t_lbl}.after_tool", ts.after_tool_trace))
        if target.after_agent_trace is not None:
            out.append(("after_agent", target.after_agent_trace))
        return out
    turns = getattr(target, "turns", None)
    if isinstance(turns, Sequence):
        out = []
        for idx, tt in enumerate(turns, 1):
            for lbl, st in _iter_stage_traces_from_target(tt):
                out.append((f"turn#{idx}.{lbl}", st))
        return out
    return []


def _values_differ(prev_val: Any, curr_val: Any) -> bool:
    """Returns True if `prev_val` and `curr_val` differ semantically (ignoring
    CES int/float coercion).
    """
    if isinstance(prev_val, bool) != isinstance(curr_val, bool):
        return True
    if isinstance(prev_val, (int, float)) and isinstance(
        curr_val, (int, float)
    ):
        return prev_val != curr_val
    if type(prev_val) is not type(curr_val):
        return True
    if isinstance(prev_val, dict) and isinstance(curr_val, dict):
        if set(prev_val.keys()) != set(curr_val.keys()):
            return True
        return any(_values_differ(prev_val[k], curr_val[k]) for k in prev_val)
    if isinstance(prev_val, (list, tuple)) and isinstance(
        curr_val, (list, tuple)
    ):
        if len(prev_val) != len(curr_val):
            return True
        return any(
            _values_differ(a, b)
            for a, b in zip(prev_val, curr_val, strict=False)
        )
    return bool(prev_val != curr_val)


def _format_step_state_changes(
    prev_state: Mapping[str, Any],
    curr_state: Mapping[str, Any],
    focus_set: set[str],
) -> tuple[list[str], bool]:
    """Formats state mutations between `prev_state` and `curr_state`."""
    all_k = sorted(set(prev_state.keys()) | set(curr_state.keys()))
    step_changes: list[str] = []
    touched_focus = False
    for k in all_k:
        if k in ("_session", "current_date") and k not in focus_set:
            continue
        in_prev = k in prev_state
        in_curr = k in curr_state
        if not in_prev and in_curr:
            step_changes.append(f"added '{k}'={curr_state[k]!r}")
            if k in focus_set:
                touched_focus = True
        elif in_prev and not in_curr:
            step_changes.append(f"removed '{k}' (was {prev_state[k]!r})")
            if k in focus_set:
                touched_focus = True
        elif _values_differ(prev_state[k], curr_val=curr_state[k]):
            step_changes.append(
                f"modified '{k}': {prev_state[k]!r} -> {curr_state[k]!r}"
            )
            if k in focus_set:
                touched_focus = True
    return step_changes, touched_focus


def _trace_state_lineage(
    target: Any,
    *,
    focus_keys: Sequence[str] = (),
) -> tuple[list[str], str | None]:
    """Walks step and tool traces to build state mutation lineage and identify
    root cause.
    """
    stage_pairs = _iter_stage_traces_from_target(target)
    history: list[str] = []
    last_mutator_for_focus: str | None = None
    last_mutator_any: str | None = None
    focus_set = set(focus_keys)

    def _process_stage(stage_lbl: str, st: CallbackTrace) -> None:
        nonlocal last_mutator_any, last_mutator_for_focus
        prev_state = copy.deepcopy(st.initial_state)
        for step in st.steps:
            curr_state = step.state_after
            step_changes, touched_focus = _format_step_state_changes(
                prev_state, curr_state, focus_set
            )
            prev_state = copy.deepcopy(curr_state)
            if step_changes or step.error or step.returned_override:
                change_str = (
                    "; ".join(step_changes)
                    if step_changes
                    else "no state changes"
                )
                history.append(
                    f"[{stage_lbl}] {step.name} ({step.status}): {change_str}"
                )
                if step_changes:
                    last_mutator_any = step.name
                if touched_focus:
                    last_mutator_for_focus = step.name

    def _process_turn(tt: TurnTrace, prefix: str = "") -> None:
        nonlocal last_mutator_any, last_mutator_for_focus
        p = f"{prefix}." if prefix else ""
        if tt.before_agent_trace is not None:
            _process_stage(f"{p}before_agent", tt.before_agent_trace)
        if tt.llm_steps:
            for ls in tt.llm_steps:
                hop_lbl = f"{p}hop#{ls.step_index + 1}"
                _process_stage(f"{hop_lbl}.before_model", ls.before_model_trace)
                if ls.after_model_trace is not None:
                    _process_stage(
                        f"{hop_lbl}.after_model", ls.after_model_trace
                    )
        else:
            _process_stage(f"{p}before_model", tt.before_model_trace)
            if tt.after_model_trace is not None:
                _process_stage(f"{p}after_model", tt.after_model_trace)
        for idx, ts in enumerate(tt.tool_steps, 1):
            t_lbl = f"{p}tool#{idx}({ts.tool_name})"
            _process_stage(f"{t_lbl}.before_tool", ts.before_tool_trace)
            if (
                not ts.short_circuited_by_before_tool
                and ts.after_tool_trace is not None
            ):
                tool_changes, touched_focus = _format_step_state_changes(
                    ts.before_tool_trace.final_state,
                    ts.after_tool_trace.initial_state,
                    focus_set,
                )
                tool_actor = f"tool:{ts.tool_name}"
                if tool_changes or ts.tool_error:
                    change_str = (
                        "; ".join(tool_changes)
                        if tool_changes
                        else "no state changes"
                    )
                    status_lbl = "FAILED" if ts.tool_error else "EXECUTED"
                    history.append(
                        f"[{t_lbl}] {tool_actor} ({status_lbl}): {change_str}"
                    )
                    if tool_changes:
                        last_mutator_any = tool_actor
                    if touched_focus:
                        last_mutator_for_focus = tool_actor
            if ts.after_tool_trace is not None:
                _process_stage(f"{t_lbl}.after_tool", ts.after_tool_trace)
        if tt.after_agent_trace is not None:
            _process_stage(f"{p}after_agent", tt.after_agent_trace)

    if isinstance(target, TurnTrace):
        _process_turn(target)
    elif isinstance(getattr(target, "turns", None), Sequence):
        for idx, tt in enumerate(target.turns, 1):
            _process_turn(tt, prefix=f"turn#{idx}")
    else:
        for stage_lbl, st in stage_pairs:
            _process_stage(stage_lbl, st)

    root_cause = last_mutator_for_focus
    if root_cause is None:
        if isinstance(target, CallbackTrace):
            root_cause = (
                target.failed_callback
                or target.culprit_callback
                or target.short_circuited_by
                or last_mutator_any
            )
        elif isinstance(target, TurnTrace):
            for _, st in reversed(stage_pairs):
                if st.failed_callback or st.culprit_callback:
                    root_cause = st.failed_callback or st.culprit_callback
                    break
            if root_cause is None:
                root_cause = target.short_circuited_by or last_mutator_any
        elif isinstance(target, ModeDiffReport):
            root_cause = (
                target.per_callback_trace.failed_callback
                or target.combined_trace.failed_callback
                or target.combined_trace.culprit_callback
                or target.combined_trace.short_circuited_by
                or last_mutator_any
            )
        else:
            root_cause = last_mutator_any

    return history, root_cause


def _collect_warnings_and_lint(
    target: Any,
    runner: CallbackChainRunner | None = None,
) -> tuple[list[str], list[CallbackChainLintFinding]]:
    """Collects runtime warnings and static lint findings associated with
    `target`.
    """
    warnings: list[str] = []
    stage_pairs = _iter_stage_traces_from_target(target)
    for _, st in stage_pairs:
        for w in st.warnings:
            if w not in warnings:
                warnings.append(w)
    if isinstance(target, TurnTrace) or hasattr(target, "turns"):
        for w in getattr(target, "warnings", ()):
            if w not in warnings:
                warnings.append(w)

    eff_runner = runner or getattr(target, "_runner", None)
    lint_findings: list[CallbackChainLintFinding] = []
    if eff_runner is not None and hasattr(eff_runner, "lint"):
        try:
            if isinstance(target, (CallbackTrace, ModeDiffReport)):
                lint_findings = eff_runner.lint(target.stage)
            else:
                lint_findings = eff_runner.lint()
        except Exception:
            pass

    return warnings, lint_findings


class AgentAssertionError(AssertionError):
    """Self-diagnosing assertion error with callback root-cause and state
    lineage.
    """

    def __init__(
        self,
        message: str,
        *,
        trace: Any = None,
        expected: Any = None,
        actual: Any = None,
        root_cause_callback: str | None = None,
        state_mutation_history: list[str] | None = None,
        lint_findings: list[CallbackChainLintFinding] | None = None,
        focus_state_keys: Sequence[str] = (),
        runner: CallbackChainRunner | None = None,
    ) -> None:
        self.trace = trace
        self.expected = expected
        self.actual = actual

        auto_history, auto_root = _trace_state_lineage(
            trace, focus_keys=focus_state_keys
        )
        self.state_mutation_history: list[str] = (
            list(state_mutation_history)
            if state_mutation_history is not None
            else auto_history
        )
        self.root_cause_callback: str | None = (
            root_cause_callback
            if root_cause_callback is not None
            else auto_root
        )

        runtime_warnings, auto_lint = _collect_warnings_and_lint(
            trace, runner=runner
        )
        self.lint_findings: list[CallbackChainLintFinding] = (
            list(lint_findings) if lint_findings is not None else auto_lint
        )

        sections: list[str] = [message]
        if expected is not None or actual is not None:
            sections.append(f"  Expected: {expected!r}\n  Actual:   {actual!r}")
        if self.root_cause_callback:
            sections.append(
                f"  [Root-Cause Callback]: {self.root_cause_callback}"
            )
        if self.state_mutation_history:
            lineage_lines = "\n    ".join(self.state_mutation_history)
            sections.append(f"  [State Mutation Lineage]:\n    {lineage_lines}")
        if runtime_warnings:
            warn_lines = "\n    ".join(runtime_warnings)
            sections.append(f"  [Runtime Warnings]:\n    {warn_lines}")
        if self.lint_findings:
            lint_lines = "\n    ".join(
                f"[{getattr(f, 'rule_id', getattr(f, 'code', ''))}] "
                f"({f.severity}) in '{f.callback_name}': {f.message}"
                for f in self.lint_findings[:5]
            )
            sections.append(f"  [Static Lint Findings]:\n    {lint_lines}")

        self.diagnostic_message = "\n".join(sections)
        super().__init__(self.diagnostic_message)
