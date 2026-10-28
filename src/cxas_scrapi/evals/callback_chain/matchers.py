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

"""Fluent Jest-style matchers and self-diagnosing assertions for CES agents."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping, Sequence
from re import Pattern
from typing import TYPE_CHECKING, Any

from cxas_scrapi.evals.callback_chain.hydration import compute_state_delta
from cxas_scrapi.evals.callback_chain.snapshots import (
    SnapshotManager,
    _extract_active_tools,
    _extract_injected_state_updates,
    _extract_system_instruction_text,
    _find_unresolved_placeholders,
    serialize_session_snapshot,
    serialize_stage_snapshot,
    serialize_turn_snapshot,
)
from cxas_scrapi.evals.callback_chain.traces import (
    CallbackTrace,
    LlmStepTrace,
    ModeDiffReport,
    TurnTrace,
)
from cxas_scrapi.evals.callback_chain.turn import SessionSimulator

if TYPE_CHECKING:
    from pathlib import Path

    from cxas_scrapi.evals.callback_chain.lint import CallbackChainLintFinding
    from cxas_scrapi.evals.callback_chain.runner import CallbackChainRunner
    from cxas_scrapi.utils.callback_libs import LlmResponse

_UNSET: Any = object()


def _iter_stage_traces_from_target(
    target: CallbackTrace
    | TurnTrace
    | ModeDiffReport
    | SessionSimulator
    | None,
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
    if isinstance(target, SessionSimulator):
        out = []
        for idx, tt in enumerate(target.turns, 1):
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
        elif _values_differ(prev_state[k], curr_state[k]):
            step_changes.append(
                f"modified '{k}': {prev_state[k]!r} -> {curr_state[k]!r}"
            )
            if k in focus_set:
                touched_focus = True
    return step_changes, touched_focus


def _trace_state_lineage(
    target: CallbackTrace
    | TurnTrace
    | ModeDiffReport
    | SessionSimulator
    | None,
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
    elif isinstance(target, SessionSimulator):
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
    target: CallbackTrace
    | TurnTrace
    | ModeDiffReport
    | SessionSimulator
    | None,
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
    if isinstance(target, (TurnTrace, SessionSimulator)):
        for w in target.warnings:
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
        trace: CallbackTrace
        | TurnTrace
        | ModeDiffReport
        | SessionSimulator
        | None = None,
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


def _match_text_or_regex(
    pattern: str | Pattern[str],
    text: str,
) -> bool:
    if isinstance(pattern, Pattern):
        return bool(pattern.search(text))
    return str(pattern) in text


def _subset_mismatches(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    mismatches: dict[str, dict[str, Any]] = {}
    for k, exp_v in expected.items():
        if k not in actual:
            mismatches[str(k)] = {"expected": exp_v, "actual": "<MISSING>"}
        elif _values_differ(exp_v, actual[k]):
            mismatches[str(k)] = {"expected": exp_v, "actual": actual[k]}
    return mismatches


def _analyze_stage_state_mutations(
    trace: CallbackTrace,
    in_callback: str | None = None,
) -> tuple[dict[str, Any], dict[str, tuple[Any, Any]], dict[str, Any]]:
    """Computes `(added, modified, removed)` state keys for `trace` or a
    specific callback step.
    """
    added: dict[str, Any] = {}
    modified: dict[str, tuple[Any, Any]] = {}
    removed: dict[str, Any] = {}

    if in_callback is not None:
        prev_state = copy.deepcopy(trace.initial_state)
        for step in trace.steps:
            curr_state = step.state_after
            if step.name == in_callback:
                for k in sorted(
                    set(prev_state.keys()) | set(curr_state.keys())
                ):
                    if k == "_session":
                        continue
                    in_prev = k in prev_state
                    in_curr = k in curr_state
                    if not in_prev and in_curr:
                        added[k] = curr_state[k]
                    elif in_prev and not in_curr:
                        removed[k] = prev_state[k]
                    elif _values_differ(prev_state[k], curr_state[k]):
                        modified[k] = (prev_state[k], curr_state[k])
            prev_state = copy.deepcopy(curr_state)
        return added, modified, removed

    # Stage-wide comparison across initial_state -> final_state, supplemented by
    # per-step tracking
    init_s = trace.initial_state
    final_s = trace.final_state
    for k in sorted(set(init_s.keys()) | set(final_s.keys())):
        if k == "_session":
            continue
        in_prev = k in init_s
        in_curr = k in final_s
        if not in_prev and in_curr:
            added[k] = final_s[k]
        elif in_prev and not in_curr:
            removed[k] = init_s[k]
        elif _values_differ(init_s[k], final_s[k]):
            modified[k] = (init_s[k], final_s[k])

    return added, modified, removed


class StageExpectation:
    """Fluent Jest-style assertion wrapper for a single stage (`CallbackTrace`
    or `ModeDiffReport`).
    """

    def __init__(
        self,
        target: CallbackTrace | tuple[Any, CallbackTrace] | ModeDiffReport,
        *,
        runner: CallbackChainRunner | None = None,
        snapshot_dir: str | Path | None = None,
        update_snapshots: bool | None = None,
        _negated: bool = False,
        _positive_self: StageExpectation | None = None,
    ) -> None:
        self._override_value: Any = None
        self._diff_report: ModeDiffReport | None = None
        if (
            isinstance(target, tuple)
            and len(target) == 2
            and isinstance(target[1], CallbackTrace)
        ):
            self._override_value = target[0]
            self._trace = target[1]
        elif isinstance(target, ModeDiffReport):
            self._diff_report = target
            self._trace = target.per_callback_trace
            self._override_value = target.per_callback_response
        elif isinstance(target, CallbackTrace):
            self._trace = target
            self._override_value = target.override_response
        else:
            raise TypeError(
                "expect_stage() requires a CallbackTrace, (override, "
                "CallbackTrace) tuple, "
                f"or ModeDiffReport; got {type(target).__name__}."
            )

        self._runner: CallbackChainRunner | None = (
            runner
            or getattr(target, "_runner", None)
            or getattr(self._trace, "_runner", None)
        )
        self._snapshot_dir = snapshot_dir
        self._update_snapshots = update_snapshots
        self._negated = _negated
        self._positive_self = _positive_self

    @property
    def not_(self) -> StageExpectation:
        """Inverts the single immediately following matcher assertion."""
        pos = self._positive_self if self._positive_self is not None else self
        target_obj: Any = (
            self._diff_report
            if self._diff_report is not None
            else (self._override_value, self._trace)
        )
        return StageExpectation(
            target_obj,
            runner=self._runner,
            snapshot_dir=self._snapshot_dir,
            update_snapshots=self._update_snapshots,
            _negated=not self._negated,
            _positive_self=pos,
        )

    def _check(
        self,
        passed: bool,
        *,
        pos_msg: str,
        neg_msg: str,
        expected: Any = None,
        actual: Any = None,
        focus_state_keys: Sequence[str] = (),
        lint_findings: list[CallbackChainLintFinding] | None = None,
    ) -> StageExpectation:
        trace_target: CallbackTrace | ModeDiffReport = (
            self._diff_report if self._diff_report is not None else self._trace
        )
        if not self._negated and not passed:
            raise AgentAssertionError(
                pos_msg,
                trace=trace_target,
                expected=expected,
                actual=actual,
                focus_state_keys=focus_state_keys,
                lint_findings=lint_findings,
                runner=self._runner,
            )
        if self._negated and passed:
            raise AgentAssertionError(
                neg_msg,
                trace=trace_target,
                expected=f"not ({expected!r})"
                if expected is not None
                else None,
                actual=actual,
                focus_state_keys=focus_state_keys,
                lint_findings=lint_findings,
                runner=self._runner,
            )
        return self._positive_self if self._positive_self is not None else self

    def to_succeed(self) -> StageExpectation:
        """Asserts that no callback in the stage raised an error (`trace.error
        is None`).
        """
        err = self._trace.error
        passed = err is None
        failed_cb = self._trace.failed_callback or self._trace.culprit_callback
        return self._check(
            passed,
            pos_msg=(
                f"Expected stage '{self._trace.stage}' to succeed without "
                f"error, but callback '{failed_cb}' failed with:\n{err}"
            ),
            neg_msg=(
                f"Expected stage '{self._trace.stage}' NOT to succeed, "
                "but it completed with no errors."
            ),
            expected=None,
            actual=err.splitlines()[0] if err else None,
        )

    to_succeed_without_errors = to_succeed

    def to_fail(
        self,
        *,
        with_callback: str | None = None,
        outcome: str | None = None,
        message_contains: str | None = None,
    ) -> StageExpectation:
        """Asserts that the stage encountered an error matching the specified
        criteria.
        """
        err = self._trace.error
        actual_cb = self._trace.failed_callback or self._trace.culprit_callback
        actual_outcome = self._trace.error_outcome

        passed = err is not None
        if passed and with_callback is not None:
            passed = with_callback in (actual_cb, self._trace.failed_callback)
        if passed and outcome is not None:
            passed = actual_outcome == outcome
        if passed and message_contains is not None:
            passed = message_contains in (err or "")

        return self._check(
            passed,
            pos_msg=(
                f"Expected stage '{self._trace.stage}' to fail "
                f"(callback={with_callback!r}, outcome={outcome!r}, "
                f"message_contains={message_contains!r}), "
                f"but got failed_callback={actual_cb!r}, "
                f"outcome={actual_outcome!r}, "
                f"error={err.splitlines()[0] if err else None!r}."
            ),
            neg_msg=(
                f"Expected stage '{self._trace.stage}' NOT to fail with "
                f"(callback={with_callback!r}, outcome={outcome!r}, "
                f"message_contains={message_contains!r}), "
                f"but it did."
            ),
            expected={
                "error": True,
                "callback": with_callback,
                "outcome": outcome,
                "message_contains": message_contains,
            },
            actual={
                "error": err is not None,
                "callback": actual_cb,
                "outcome": actual_outcome,
            },
        )

    def to_have_executed(
        self,
        *callback_names: str | Sequence[str],
        exact_order: bool = False,
    ) -> StageExpectation:
        """Asserts that the specified callbacks executed during this stage."""
        flat_names: list[str] = []
        for item in callback_names:
            if isinstance(item, str):
                flat_names.append(item)
            elif isinstance(item, Sequence):
                flat_names.extend(str(x) for x in item)
            else:
                raise TypeError(
                    f"Callback name must be str, got {type(item).__name__}"
                )

        actual = list(self._trace.executed_callbacks)
        if exact_order:
            passed = actual == flat_names
        elif self._negated:
            passed = any(name in actual for name in flat_names)
        else:
            passed = all(name in actual for name in flat_names)

        return self._check(
            passed,
            pos_msg=(
                f"Expected stage '{self._trace.stage}' to execute callbacks "
                f"{flat_names!r} "
                f"(exact_order={exact_order}), but executed {actual!r}."
            ),
            neg_msg=(
                f"Expected stage '{self._trace.stage}' NOT to execute "
                f"callbacks {flat_names!r} "
                f"(exact_order={exact_order}), but executed {actual!r}."
            ),
            expected=flat_names,
            actual=actual,
        )

    def to_have_skipped(
        self,
        *callback_names: str | Sequence[str],
    ) -> StageExpectation:
        """Asserts that the specified callbacks were skipped or disabled."""
        flat_names: list[str] = []
        for item in callback_names:
            if isinstance(item, str):
                flat_names.append(item)
            elif isinstance(item, Sequence):
                flat_names.extend(str(x) for x in item)
            else:
                raise TypeError(
                    f"Callback name must be str, got {type(item).__name__}"
                )

        skipped_all = list(self._trace.skipped_callbacks) + list(
            self._trace.disabled_callbacks
        )
        if self._negated:
            passed = any(name in skipped_all for name in flat_names)
        else:
            passed = all(name in skipped_all for name in flat_names)
        return self._check(
            passed,
            pos_msg=(
                f"Expected stage '{self._trace.stage}' to skip callbacks "
                f"{flat_names!r}, "
                f"but skipped={self._trace.skipped_callbacks!r}, "
                f"disabled={self._trace.disabled_callbacks!r} "
                f"(executed={self._trace.executed_callbacks!r})."
            ),
            neg_msg=(
                f"Expected stage '{self._trace.stage}' NOT to skip callbacks "
                f"{flat_names!r}, "
                f"but they were in skipped/disabled {skipped_all!r}."
            ),
            expected=flat_names,
            actual=skipped_all,
        )

    def to_have_short_circuited(
        self,
        at_callback: str | None = None,
        *,
        by: str | None = None,
    ) -> StageExpectation:
        """Asserts that the stage chain short-circuited (optionally at a
        specific callback).
        """
        eff_by = at_callback if at_callback is not None else by
        passed = bool(self._trace.short_circuited)
        if passed and eff_by is not None:
            passed = self._trace.short_circuited_by == eff_by
        return self._check(
            passed,
            pos_msg=(
                f"Expected stage '{self._trace.stage}' to short-circuit"
                f"{f' at {eff_by!r}' if eff_by else ''}, "
                f"but short_circuited={self._trace.short_circuited}, "
                f"short_circuited_by={self._trace.short_circuited_by!r}."
            ),
            neg_msg=(
                f"Expected stage '{self._trace.stage}' NOT to short-circuit"
                f"{f' at {eff_by!r}' if eff_by else ''}, "
                f"but it short-circuited at {self._trace.short_circuited_by!r}."
            ),
            expected={"short_circuited": True, "at_callback": eff_by},
            actual={
                "short_circuited": self._trace.short_circuited,
                "at_callback": self._trace.short_circuited_by,
            },
        )

    to_short_circuit = to_have_short_circuited

    def to_continue(self) -> StageExpectation:
        """Asserts that the stage did NOT short-circuit and encountered no
        errors.
        """
        passed = (
            not self._trace.short_circuited
            and self._trace.override_response is None
            and self._trace.error is None
        )
        return self._check(
            passed,
            pos_msg=(
                f"Expected stage '{self._trace.stage}' to continue without "
                "short-circuiting "
                "or failing, but "
                f"short_circuited={self._trace.short_circuited} "
                f"(by={self._trace.short_circuited_by!r}), "
                f"error={self._trace.error!r}."
            ),
            neg_msg=(
                f"Expected stage '{self._trace.stage}' NOT to continue "
                "cleanly, "
                "but it completed without short-circuiting or error."
            ),
            expected={"short_circuited": False, "error": None},
            actual={
                "short_circuited": self._trace.short_circuited,
                "short_circuited_by": self._trace.short_circuited_by,
                "error": self._trace.error.splitlines()[0]
                if self._trace.error
                else None,
            },
        )

    def to_return_override(
        self,
        expected: Any = _UNSET,
    ) -> StageExpectation:
        """Asserts that the stage returned an override value (optionally
        matching `expected`).
        """
        actual = (
            self._override_value
            if self._override_value is not None
            else self._trace.override_response
        )
        if expected is _UNSET:
            passed = actual is not None or bool(self._trace.short_circuited)
        elif isinstance(expected, type):
            passed = isinstance(actual, expected)
        elif isinstance(expected, Mapping) and isinstance(actual, Mapping):
            passed = len(_subset_mismatches(expected, actual)) == 0
        else:
            passed = actual == expected

        match_clause = (
            f" matching {expected!r}" if expected is not _UNSET else ""
        )
        return self._check(
            passed,
            pos_msg=(
                f"Expected stage '{self._trace.stage}' to return override"
                f"{match_clause}, but override was {actual!r}."
            ),
            neg_msg=(
                f"Expected stage '{self._trace.stage}' NOT to return override"
                f"{match_clause}, but override was {actual!r}."
            ),
            expected="<non-None override>" if expected is _UNSET else expected,
            actual=actual,
        )

    def to_set_state(
        self,
        _expected_dict: Mapping[str, Any] | None = None,
        /,
        **expected_kv: Any,
    ) -> StageExpectation:
        """Asserts that `trace.final_state` contains the expected key-value
        pairs.
        """
        expected: dict[str, Any] = {}
        if _expected_dict is not None:
            expected.update(dict(_expected_dict))
        if expected_kv:
            expected.update(expected_kv)
        if not expected:
            raise ValueError(
                "to_set_state() requires at least one key-value pair to assert."
            )

        mismatches = _subset_mismatches(expected, self._trace.final_state)
        passed = len(mismatches) == 0
        actual_subset = {
            k: self._trace.final_state.get(k, "<MISSING>") for k in expected
        }
        return self._check(
            passed,
            pos_msg=(
                f"Expected stage '{self._trace.stage}' final_state to match "
                f"{expected!r}, "
                f"but found mismatches: {mismatches!r}."
            ),
            neg_msg=(
                f"Expected stage '{self._trace.stage}' final_state NOT to "
                f"match {expected!r}, "
                f"but all keys matched: {actual_subset!r}."
            ),
            expected=expected,
            actual=actual_subset,
            focus_state_keys=list(expected.keys()),
        )

    def to_have_state_key(
        self,
        key: str,
        expected_value: Any = _UNSET,
    ) -> StageExpectation:
        """Asserts that `key` is present in `trace.final_state` (optionally with
        `expected_value`).
        """
        if expected_value is _UNSET:
            passed = key in self._trace.final_state
            exp_repr: Any = f"key {key!r} present"
        else:
            passed = key in self._trace.final_state and not _values_differ(
                self._trace.final_state[key], expected_value
            )
            exp_repr = {key: expected_value}
        val_clause = (
            f" == {expected_value!r}" if expected_value is not _UNSET else ""
        )
        return self._check(
            passed,
            pos_msg=(
                f"Expected stage '{self._trace.stage}' final_state to have "
                f"{key!r}{val_clause}, "
                f"but got {self._trace.final_state.get(key, '<MISSING>')!r}."
            ),
            neg_msg=(
                f"Expected stage '{self._trace.stage}' final_state NOT to "
                f"have {key!r}{val_clause}, "
                f"but it was {self._trace.final_state.get(key, '<MISSING>')!r}."
            ),
            expected=exp_repr,
            actual=self._trace.final_state.get(key, "<MISSING>"),
            focus_state_keys=[key],
        )

    def to_have_modified_state(
        self,
        *keys: str,
        in_callback: str | None = None,
        **expected_kv: Any,
    ) -> StageExpectation:
        """Asserts that the specified existing state keys were modified during
        the stage.
        """
        added, modified, removed = _analyze_stage_state_mutations(
            self._trace, in_callback=in_callback
        )
        target_keys = list(keys) + [k for k in expected_kv if k not in keys]
        if not target_keys:
            passed = bool(modified or added or removed)
        elif self._negated:
            passed = any(k in modified for k in keys) or any(
                k in modified and not _values_differ(modified[k][1], v)
                for k, v in expected_kv.items()
            )
        else:
            passed = all(k in modified for k in target_keys)
            if passed and expected_kv:
                passed = all(
                    not _values_differ(modified[k][1], v)
                    for k, v in expected_kv.items()
                )

        cb_scope = f" in callback {in_callback!r}" if in_callback else ""
        cb_missing_note = (
            f" Note: callback {in_callback!r} did not execute in stage "
            f"'{self._trace.stage}' "
            f"(executed={self._trace.executed_callbacks!r})."
            if in_callback is not None
            and in_callback not in self._trace.executed_callbacks
            else ""
        )
        return self._check(
            passed,
            pos_msg=(
                f"Expected stage '{self._trace.stage}'{cb_scope} to modify "
                "state keys "
                f"{target_keys or '<any>'!r}"
                f"{f' to {expected_kv!r}' if expected_kv else ''}, "
                f"but modified={ {k: v[1] for k, v in modified.items()}!r} "
                f"(added={list(added.keys())!r}, "
                f"removed={list(removed.keys())!r}).{cb_missing_note}"
            ),
            neg_msg=(
                f"Expected stage '{self._trace.stage}'{cb_scope} NOT to "
                "modify state keys "
                f"{target_keys or '<any>'!r}, "
                f"but modified={ {k: v[1] for k, v in modified.items()}!r}."
            ),
            expected=expected_kv
            if expected_kv
            else (target_keys or "<modified state>"),
            actual={k: v[1] for k, v in modified.items()},
            focus_state_keys=target_keys,
        )

    def to_have_added_state(
        self,
        *keys: str,
        in_callback: str | None = None,
        **expected_kv: Any,
    ) -> StageExpectation:
        """Asserts that the specified state keys were newly added during the
        stage.
        """
        added, modified, removed = _analyze_stage_state_mutations(
            self._trace, in_callback=in_callback
        )
        target_keys = list(keys) + [k for k in expected_kv if k not in keys]
        if not target_keys:
            passed = bool(added)
        elif self._negated:
            passed = any(k in added for k in keys) or any(
                k in added and not _values_differ(added[k], v)
                for k, v in expected_kv.items()
            )
        else:
            passed = all(k in added for k in target_keys)
            if passed and expected_kv:
                passed = all(
                    not _values_differ(added[k], v)
                    for k, v in expected_kv.items()
                )

        cb_scope = f" in callback {in_callback!r}" if in_callback else ""
        cb_missing_note = (
            f" Note: callback {in_callback!r} did not execute in stage "
            f"'{self._trace.stage}' "
            f"(executed={self._trace.executed_callbacks!r})."
            if in_callback is not None
            and in_callback not in self._trace.executed_callbacks
            else ""
        )
        return self._check(
            passed,
            pos_msg=(
                f"Expected stage '{self._trace.stage}'{cb_scope} to add state "
                "keys "
                f"{target_keys or '<any>'!r}"
                f"{f' with {expected_kv!r}' if expected_kv else ''}, "
                f"but added={added!r} (modified={list(modified.keys())!r}, "
                f"removed={list(removed.keys())!r}).{cb_missing_note}"
            ),
            neg_msg=(
                f"Expected stage '{self._trace.stage}'{cb_scope} NOT to add "
                "state keys "
                f"{target_keys or '<any>'!r}, but added={added!r}."
            ),
            expected=expected_kv
            if expected_kv
            else (target_keys or "<added state>"),
            actual=added,
            focus_state_keys=target_keys,
        )

    def to_have_removed_state(
        self,
        *keys: str,
        in_callback: str | None = None,
    ) -> StageExpectation:
        """Asserts that the specified state keys were removed during the
        stage.
        """
        added, modified, removed = _analyze_stage_state_mutations(
            self._trace, in_callback=in_callback
        )
        target_keys = list(keys)
        if not target_keys:
            passed = bool(removed)
        elif self._negated:
            passed = any(k in removed for k in target_keys)
        else:
            passed = all(k in removed for k in target_keys)

        cb_scope = f" in callback {in_callback!r}" if in_callback else ""
        cb_missing_note = (
            f" Note: callback {in_callback!r} did not execute in stage "
            f"'{self._trace.stage}' "
            f"(executed={self._trace.executed_callbacks!r})."
            if in_callback is not None
            and in_callback not in self._trace.executed_callbacks
            else ""
        )
        return self._check(
            passed,
            pos_msg=(
                f"Expected stage '{self._trace.stage}'{cb_scope} to remove "
                "state keys "
                f"{target_keys or '<any>'!r}, "
                f"but removed={list(removed.keys())!r} "
                f"(added={list(added.keys())!r}, "
                f"modified={list(modified.keys())!r}).{cb_missing_note}"
            ),
            neg_msg=(
                f"Expected stage '{self._trace.stage}'{cb_scope} NOT to "
                "remove state keys "
                f"{target_keys or '<any>'!r}, but "
                f"removed={list(removed.keys())!r}."
            ),
            expected=target_keys or "<removed state>",
            actual=list(removed.keys()),
            focus_state_keys=target_keys,
        )

    def to_preserve_state(self) -> StageExpectation:
        """Asserts that `trace.final_state` equals `trace.initial_state` (no
        state mutations).
        """
        delta = compute_state_delta(
            self._trace.initial_state, self._trace.final_state
        )
        passed = len(delta) == 0
        return self._check(
            passed,
            pos_msg=(
                f"Expected stage '{self._trace.stage}' to preserve session "
                "state unchanged, "
                f"but state delta was {delta!r}."
            ),
            neg_msg=(
                f"Expected stage '{self._trace.stage}' NOT to preserve "
                "session state, "
                "but no state keys were modified."
            ),
            expected={},
            actual=delta,
            focus_state_keys=list(delta.keys()),
        )

    def to_call_mock_tool(
        self,
        tool_name: str,
        *,
        with_args: Mapping[str, Any] | None = None,
        partial_args: Mapping[str, Any] | None = None,
        with_source: str | None = None,
        with_status: int | None = None,
        times: int | None = None,
    ) -> StageExpectation:
        """Asserts that callbacks in this stage invoked
        `tools.<tool_name>(...)`.
        """
        matching_calls = [
            c
            for c in self._trace.tool_calls
            if c.get("tool") == tool_name or c.get("name") == tool_name
        ]
        if with_source is not None:
            matching_calls = [
                c for c in matching_calls if c.get("source") == with_source
            ]
        if with_status is not None:
            matching_calls = [
                c
                for c in matching_calls
                if c.get("status_code", 200) == with_status
            ]

        def _extract_mock_call_kwargs(
            call_entry: Mapping[str, Any],
        ) -> dict[str, Any]:
            merged: dict[str, Any] = {}
            raw_args = call_entry.get("args")
            if isinstance(raw_args, Mapping):
                merged.update(raw_args)
            elif (
                isinstance(raw_args, (list, tuple))
                and len(raw_args) == 1
                and isinstance(raw_args[0], Mapping)
            ):
                merged.update(raw_args[0])
            raw_kwargs = call_entry.get("kwargs")
            if isinstance(raw_kwargs, Mapping):
                merged.update(raw_kwargs)
            return merged

        if with_args is not None:
            matching_calls = [
                c
                for c in matching_calls
                if not _subset_mismatches(
                    with_args, _extract_mock_call_kwargs(c)
                )
            ]
        if partial_args is not None:
            matching_calls = [
                c
                for c in matching_calls
                if not _subset_mismatches(
                    partial_args, _extract_mock_call_kwargs(c)
                )
            ]
        if times is not None:
            passed = len(matching_calls) == times
        else:
            passed = len(matching_calls) >= 1

        status_clause = (
            f" with status {with_status!r}" if with_status is not None else ""
        )
        return self._check(
            passed,
            pos_msg=(
                f"Expected stage '{self._trace.stage}' to call mock tool "
                f"{tool_name!r}"
                f"{f' with args {dict(with_args)!r}' if with_args else ''}"
                f"{f' with source {with_source!r}' if with_source else ''}"
                f"{status_clause}"
                f"{f' exactly {times} time(s)' if times is not None else ''}, "
                f"but recorded tool calls were {self._trace.tool_calls!r}."
            ),
            neg_msg=(
                f"Expected stage '{self._trace.stage}' NOT to call mock tool "
                f"{tool_name!r}"
                f"{f' with args {dict(with_args)!r}' if with_args else ''}, "
                f"but it matched {len(matching_calls)} call(s): "
                f"{matching_calls!r}."
            ),
            expected={
                "tool": tool_name,
                "with_args": dict(with_args) if with_args else None,
                "with_source": with_source,
                "with_status": with_status,
                "times": times,
            },
            actual=self._trace.tool_calls,
        )

    to_have_called_tool = to_call_mock_tool

    def to_have_no_warnings(self) -> StageExpectation:
        """Asserts that no runtime warnings (e.g. state aliasing hazards) were
        emitted.
        """
        warns = list(self._trace.warnings)
        passed = len(warns) == 0
        return self._check(
            passed,
            pos_msg=(
                f"Expected stage '{self._trace.stage}' to have no runtime "
                "warnings, "
                f"but found {len(warns)} warning(s): {warns!r}."
            ),
            neg_msg=(
                f"Expected stage '{self._trace.stage}' to emit runtime "
                "warnings, "
                "but none were recorded."
            ),
            expected=[],
            actual=warns,
        )

    def to_be_lint_clean(
        self,
        *,
        ignore_codes: Sequence[str] = (),
    ) -> StageExpectation:
        """Asserts that `runner.lint(stage)` produces zero findings (excluding
        `ignore_codes`).
        """
        if self._runner is None:
            raise AgentAssertionError(
                "to_be_lint_clean() requires a runner reference. Pass "
                "`runner=runner` "
                "to `expect_stage(trace, runner=runner)` or run the stage via "
                "`runner.run_stage(...)`."
            )
        ignore_set = set(ignore_codes)
        all_findings = self._runner.lint(self._trace.stage)
        active_findings = [
            f
            for f in all_findings
            if getattr(f, "rule_id", getattr(f, "code", "")) not in ignore_set
        ]
        passed = len(active_findings) == 0
        formatted = [
            f"[{getattr(f, 'rule_id', getattr(f, 'code', ''))}] "
            f"({f.severity}) {f.callback_name}: {f.message}"
            for f in active_findings
        ]
        return self._check(
            passed,
            pos_msg=(
                f"Expected stage '{self._trace.stage}' to be lint-clean, "
                f"but found {len(active_findings)} finding(s): {formatted!r}."
            ),
            neg_msg=(
                f"Expected stage '{self._trace.stage}' NOT to be lint-clean, "
                "but zero lint findings were reported."
            ),
            expected=[],
            actual=formatted,
            lint_findings=active_findings,
        )

    def to_be_mode_consistent(self) -> StageExpectation:
        """Asserts that `per_callback` and `combined` execution modes produce
        identical results.
        """
        if self._diff_report is None:
            raise AgentAssertionError(
                "to_be_mode_consistent() requires a ModeDiffReport. "
                "Use `expect_stage(runner.compare_stage_modes(stage, "
                "initial_state=...)).to_be_mode_consistent()`."
            )
        report = self._diff_report
        passed = not report.has_diff
        explanations = report.explain()
        return self._check(
            passed,
            pos_msg=(
                f"Expected stage '{report.stage}' to be mode-consistent "
                "between "
                f"'per_callback' and 'combined', but divergence was detected:\n"
                f"  state_diffs: {report.state_diffs!r}\n"
                f"  response_diff: {report.response_diff!r}\n"
                f"  explanations: {explanations!r}"
            ),
            neg_msg=(
                f"Expected stage '{report.stage}' to diverge between "
                "'per_callback' "
                "and 'combined' modes, but both modes produced identical "
                "results."
            ),
            expected={"has_diff": False},
            actual={
                "has_diff": report.has_diff,
                "state_diffs": report.state_diffs,
                "response_diff": report.response_diff,
            },
            focus_state_keys=list(report.state_diffs.keys()),
        )

    def to_match_snapshot(
        self,
        name: str | None = None,
        *,
        snapshot_dir: str | Path | None = None,
        update_snapshots: bool | None = None,
    ) -> StageExpectation:
        """Asserts that the deterministic stage snapshot matches the stored
        `.snap.json` entry.
        """
        eff_dir = (
            snapshot_dir if snapshot_dir is not None else self._snapshot_dir
        )
        eff_upd = (
            update_snapshots
            if update_snapshots is not None
            else self._update_snapshots
        )
        mgr = SnapshotManager(snapshot_dir=eff_dir, update_snapshots=eff_upd)
        target_obj: CallbackTrace | ModeDiffReport = (
            self._diff_report if self._diff_report is not None else self._trace
        )
        if not self._negated:
            mgr.match_stage(target_obj, name=name, update_snapshots=eff_upd)
            return (
                self._positive_self if self._positive_self is not None else self
            )

        payload = serialize_stage_snapshot(target_obj)
        exists_on_disk, matches_expected, _, _ = mgr.check_matches_readonly(
            payload, name=name
        )
        if not exists_on_disk:
            raise AgentAssertionError(
                (
                    f"Cannot assert `.not_.to_match_snapshot({name!r})` when "
                    "no "
                    "stored snapshot exists on disk."
                ),
                trace=target_obj,
                runner=self._runner,
            )
        if matches_expected:
            raise AgentAssertionError(
                f"Expected stage '{self._trace.stage}' NOT to match snapshot "
                f"{name!r}, "
                "but it matched identically.",
                trace=target_obj,
                runner=self._runner,
            )
        return self._positive_self if self._positive_self is not None else self


class TurnExpectation:
    """Fluent Jest-style assertion wrapper for a full turn (`TurnTrace`)."""

    def __init__(
        self,
        turn: TurnTrace | tuple[LlmResponse | None, TurnTrace],
        *,
        runner: CallbackChainRunner | None = None,
        snapshot_dir: str | Path | None = None,
        update_snapshots: bool | None = None,
        _negated: bool = False,
        _positive_self: TurnExpectation | None = None,
    ) -> None:
        if (
            isinstance(turn, tuple)
            and len(turn) == 2
            and isinstance(turn[1], TurnTrace)
        ):
            self._response = turn[0]
            self._turn = turn[1]
        elif isinstance(turn, TurnTrace):
            self._turn = turn
            self._response = turn.final_response
        else:
            raise TypeError(
                "expect_turn() requires a TurnTrace or (LlmResponse, "
                "TurnTrace) tuple; "
                f"got {type(turn).__name__}."
            )
        self._runner: CallbackChainRunner | None = runner or getattr(
            self._turn, "_runner", None
        )
        self._snapshot_dir = snapshot_dir
        self._update_snapshots = update_snapshots
        self._negated = _negated
        self._positive_self = _positive_self

    @property
    def not_(self) -> TurnExpectation:
        """Inverts the single immediately following matcher assertion."""
        pos = self._positive_self if self._positive_self is not None else self
        return TurnExpectation(
            self._turn,
            runner=self._runner,
            snapshot_dir=self._snapshot_dir,
            update_snapshots=self._update_snapshots,
            _negated=not self._negated,
            _positive_self=pos,
        )

    def _check(
        self,
        passed: bool,
        *,
        pos_msg: str,
        neg_msg: str,
        expected: Any = None,
        actual: Any = None,
        focus_state_keys: Sequence[str] = (),
    ) -> TurnExpectation:
        if not self._negated and not passed:
            raise AgentAssertionError(
                pos_msg,
                trace=self._turn,
                expected=expected,
                actual=actual,
                focus_state_keys=focus_state_keys,
                runner=self._runner,
            )
        if self._negated and passed:
            raise AgentAssertionError(
                neg_msg,
                trace=self._turn,
                expected=f"not ({expected!r})"
                if expected is not None
                else None,
                actual=actual,
                focus_state_keys=focus_state_keys,
                runner=self._runner,
            )
        return self._positive_self if self._positive_self is not None else self

    def _all_llm_steps(self) -> list[LlmStepTrace]:
        if self._turn.llm_steps:
            return list(self._turn.llm_steps)
        if self._turn.llm_request is not None:
            return [
                LlmStepTrace(
                    step_index=0,
                    request=self._turn.llm_request,
                    before_model_trace=self._turn.before_model_trace,
                    raw_response=self._turn.raw_llm_response,
                    after_model_trace=self._turn.after_model_trace,
                    final_response=self._turn.final_response,
                    llm_invoked=self._turn.llm_invoked,
                    after_model_skipped=self._turn.after_model_skipped,
                )
            ]
        return []

    def _resolve_llm_steps(self, hop: int | None) -> list[LlmStepTrace]:
        steps = self._all_llm_steps()
        if hop is None:
            if not steps:
                raise AgentAssertionError(
                    "No LLM hops were recorded on this TurnTrace.",
                    trace=self._turn,
                    runner=self._runner,
                )
            return steps
        if (
            not isinstance(hop, int)
            or isinstance(hop, bool)
            or hop == 0
            or hop < -1
        ):
            raise ValueError(
                "LLM hop index must be 1-based (hop >= 1) or -1 for the last "
                f"hop, got {hop!r}."
            )
        if not steps:
            raise AgentAssertionError(
                (
                    f"Requested LLM hop {hop}, but no LLM hops were recorded "
                    "on "
                    "this TurnTrace."
                ),
                trace=self._turn,
                runner=self._runner,
            )
        if hop == -1:
            return [steps[-1]]
        if hop > len(steps):
            raise AgentAssertionError(
                (
                    f"Requested 1-based LLM hop {hop}, but turn only recorded "
                    f"{len(steps)} LLM hop(s)."
                ),
                trace=self._turn,
                expected=f"hop <= {len(steps)}",
                actual=len(steps),
                runner=self._runner,
            )
        return [steps[hop - 1]]

    # 1. Lifecycle & Outcome matchers
    def to_succeed(self) -> TurnExpectation:
        """Asserts that the turn completed without any callback, tool, or LLM
        errors.
        """
        err = self._turn.error
        passed = err is None
        return self._check(
            passed,
            pos_msg=(
                f"Expected turn to succeed without error, but it failed at "
                f"stage={self._turn.failed_stage!r}, "
                f"tool={self._turn.failed_tool!r}:\n{err}"
            ),
            neg_msg=(
                "Expected turn NOT to succeed, but it completed with no errors."
            ),
            expected=None,
            actual=err.splitlines()[0] if err else None,
        )

    to_succeed_without_errors = to_succeed

    def to_fail(
        self,
        *,
        at_stage: str | None = None,
        at_tool: str | None = None,
        message_contains: str | None = None,
    ) -> TurnExpectation:
        """Asserts that the turn failed matching the optional stage, tool, and
        error substring.
        """
        err = self._turn.error
        passed = err is not None
        if passed and at_stage is not None:
            passed = self._turn.failed_stage == at_stage
        if passed and at_tool is not None:
            passed = self._turn.failed_tool == at_tool
        if passed and message_contains is not None:
            passed = message_contains in (err or "")

        return self._check(
            passed,
            pos_msg=(
                f"Expected turn to fail (at_stage={at_stage!r}, "
                f"at_tool={at_tool!r}, "
                f"message_contains={message_contains!r}), but got "
                f"failed_stage={self._turn.failed_stage!r}, "
                f"failed_tool={self._turn.failed_tool!r}, "
                f"error={err.splitlines()[0] if err else None!r}."
            ),
            neg_msg=(
                f"Expected turn NOT to fail with (at_stage={at_stage!r}, "
                f"at_tool={at_tool!r}, "
                f"message_contains={message_contains!r}), but it did."
            ),
            expected={
                "error": True,
                "at_stage": at_stage,
                "at_tool": at_tool,
                "message_contains": message_contains,
            },
            actual={
                "error": err is not None,
                "failed_stage": self._turn.failed_stage,
                "failed_tool": self._turn.failed_tool,
            },
        )

    def to_have_executed_callback(
        self,
        *callback_names: str | Sequence[str],
        stage: str | None = None,
    ) -> TurnExpectation:
        """Asserts that all specified callbacks executed during the turn
        (optionally within `stage`).
        """
        flat_names: list[str] = []
        for item in callback_names:
            if isinstance(item, str):
                flat_names.append(item)
            elif isinstance(item, Sequence):
                flat_names.extend(str(x) for x in item)
            else:
                raise TypeError(
                    f"Callback name must be str, got {type(item).__name__}"
                )

        if stage is not None:
            executed: list[str] = []
            for _, st in _iter_stage_traces_from_target(self._turn):
                if st.stage == stage:
                    for cb_name in st.executed_callbacks:
                        if cb_name not in executed:
                            executed.append(cb_name)
        else:
            executed = list(self._turn.executed_callbacks)

        if self._negated:
            passed = any(name in executed for name in flat_names)
        else:
            passed = all(name in executed for name in flat_names)
        stage_suffix = f" in stage {stage!r}" if stage is not None else ""
        return self._check(
            passed,
            pos_msg=(
                "Expected turn to have executed callback(s) "
                f"{flat_names!r}{stage_suffix}, "
                f"but executed callbacks were {executed!r}."
            ),
            neg_msg=(
                "Expected turn NOT to have executed callback(s) "
                f"{flat_names!r}{stage_suffix}, "
                f"but executed callbacks were {executed!r}."
            ),
            expected=flat_names,
            actual=executed,
        )

    def to_have_short_circuited_at(
        self,
        stage: str | None = None,
        callback_name: str | None = None,
        *,
        by: str | None = None,
    ) -> TurnExpectation:
        """Asserts that the turn was short-circuited at `stage` (optionally by
        `callback_name`).
        """
        eff_cb = callback_name if callback_name is not None else by
        short_circuited_stages = [
            (st.stage, st.short_circuited_by)
            for _, st in _iter_stage_traces_from_target(self._turn)
            if st.short_circuited
        ]
        if stage is not None:
            matching = [
                (s_name, cb)
                for s_name, cb in short_circuited_stages
                if s_name == stage and (eff_cb is None or cb == eff_cb)
            ]
            passed = len(matching) >= 1
        else:
            sc_by = self._turn.short_circuited_by
            passed = sc_by is not None and (eff_cb is None or sc_by == eff_cb)

        return self._check(
            passed,
            pos_msg=(
                f"Expected turn to short-circuit"
                f"{f' at stage {stage!r}' if stage else ''}"
                f"{f' by {eff_cb!r}' if eff_cb else ''}, "
                f"but short_circuited_stages={short_circuited_stages!r} "
                f"(turn.short_circuited_by={self._turn.short_circuited_by!r})."
            ),
            neg_msg=(
                f"Expected turn NOT to short-circuit"
                f"{f' at stage {stage!r}' if stage else ''}"
                f"{f' by {eff_cb!r}' if eff_cb else ''}, "
                f"but short_circuited_stages={short_circuited_stages!r}."
            ),
            expected={"stage": stage, "callback": eff_cb},
            actual=short_circuited_stages,
        )

    def to_short_circuit(
        self,
        *,
        by: str | None = None,
        stage: str | None = None,
    ) -> TurnExpectation:
        """Asserts that the turn was short-circuited by a callback."""
        return self.to_have_short_circuited_at(stage=stage, callback_name=by)

    def to_invoke_llm(
        self,
        *,
        times: int | None = None,
    ) -> TurnExpectation:
        """Asserts that the LLM was invoked (optionally an exact number of
        times).
        """
        invoked_count = sum(1 for s in self._turn.llm_steps if s.llm_invoked)
        if not self._turn.llm_steps and self._turn.llm_invoked:
            invoked_count = 1
        if times is not None:
            passed = invoked_count == times
        else:
            passed = invoked_count >= 1
        times_clause = (
            f" exactly {times} time(s)"
            if times is not None
            else " at least once"
        )
        return self._check(
            passed,
            pos_msg=(
                f"Expected turn to invoke LLM{times_clause}, "
                f"but LLM was invoked {invoked_count} time(s)."
            ),
            neg_msg=(
                f"Expected turn NOT to invoke LLM"
                f"{f' {times} time(s)' if times is not None else ''}, "
                f"but LLM was invoked {invoked_count} time(s)."
            ),
            expected=times if times is not None else ">= 1",
            actual=invoked_count,
        )

    def to_skip_llm(self) -> TurnExpectation:
        """Asserts that the LLM was NOT invoked during the turn."""
        invoked_count = sum(1 for s in self._turn.llm_steps if s.llm_invoked)
        if not self._turn.llm_steps and self._turn.llm_invoked:
            invoked_count = 1
        passed = invoked_count == 0 and not self._turn.llm_invoked
        return self._check(
            passed,
            pos_msg=(
                f"Expected turn to skip LLM invocation, "
                f"but LLM was invoked {invoked_count} time(s)."
            ),
            neg_msg=(
                "Expected turn NOT to skip LLM invocation, "
                "but LLM was never invoked."
            ),
            expected=0,
            actual=invoked_count,
        )

    def to_reply_matching(
        self,
        pattern_or_substring: str | Pattern[str],
        *,
        regex: bool = False,
    ) -> TurnExpectation:
        """Asserts that the final turn response text matches
        `pattern_or_substring`.
        """
        resp = self._turn.final_response
        texts: list[str] = []
        if resp is not None and resp.content is not None and resp.content.parts:
            for p in resp.content.parts:
                if p.text is not None and not getattr(p, "thought", False):
                    texts.append(p.text)
        full_reply = "\n".join(texts)
        eff_pattern: str | Pattern[str] = (
            re.compile(pattern_or_substring)
            if regex and isinstance(pattern_or_substring, str)
            else pattern_or_substring
        )
        passed = _match_text_or_regex(eff_pattern, full_reply)
        return self._check(
            passed,
            pos_msg=(
                f"Expected turn final reply to match {pattern_or_substring!r}, "
                f"but got {full_reply!r}."
            ),
            neg_msg=(
                "Expected turn final reply NOT to match "
                f"{pattern_or_substring!r}, "
                f"but got {full_reply!r}."
            ),
            expected=pattern_or_substring,
            actual=full_reply,
        )

    to_reply_with = to_reply_matching

    def to_have_transferred_to(
        self,
        *agent_names: str | Sequence[str],
    ) -> TurnExpectation:
        """Asserts that the turn transferred across `agent_names`."""
        flat_names: list[str] = []
        for item in agent_names:
            if isinstance(item, str):
                flat_names.append(item)
            elif isinstance(item, Sequence):
                flat_names.extend(str(x) for x in item)
            else:
                raise TypeError(
                    f"Agent name must be str, got {type(item).__name__}"
                )
        if not flat_names:
            raise ValueError(
                "to_have_transferred_to() requires at least one agent name."
            )

        actual_transfer = self._turn.transfer_to
        routed_targets = [a.strip() for a in self._turn.routed_agents[1:]]
        if len(flat_names) == 1:
            target_agent = flat_names[0].strip()
            passed = (
                actual_transfer is not None
                and actual_transfer.strip() == target_agent
            ) or target_agent in routed_targets
        else:
            expected_norm = [a.strip() for a in flat_names]
            all_routed = [a.strip() for a in self._turn.routed_agents]
            passed = expected_norm in (routed_targets, all_routed)

        return self._check(
            passed,
            pos_msg=(
                f"Expected turn to transfer to {flat_names!r}, "
                f"but transfer_to={actual_transfer!r} "
                f"(routed_agents={self._turn.routed_agents!r})."
            ),
            neg_msg=(
                f"Expected turn NOT to transfer to {flat_names!r}, "
                f"but transfer_to={actual_transfer!r} "
                f"(routed_agents={self._turn.routed_agents!r})."
            ),
            expected=flat_names[0] if len(flat_names) == 1 else flat_names,
            actual=actual_transfer
            if len(flat_names) == 1
            else self._turn.routed_agents,
        )

    to_transfer_to = to_have_transferred_to

    def to_end_session(
        self,
        *,
        by_callback: str | None = None,
        with_params: Mapping[str, Any] | None = None,
    ) -> TurnExpectation:
        """Asserts that the turn triggered `end_session` (optionally by
        `by_callback` / `with_params`).
        """
        actual = self._turn.end_session
        passed = actual is not None
        if passed and with_params is not None:
            passed = not _subset_mismatches(with_params, actual or {})
        if passed and by_callback is not None:
            end_callbacks = [
                st.short_circuited_by
                or st.failed_callback
                or st.culprit_callback
                for _, st in _iter_stage_traces_from_target(self._turn)
                if by_callback
                in (
                    st.short_circuited_by,
                    st.failed_callback,
                    st.culprit_callback,
                )
                or (
                    by_callback in st.executed_callbacks
                    and any(
                        c.get("tool") == "end_session"
                        or c.get("name") == "end_session"
                        for c in st.tool_calls
                    )
                )
            ]
            passed = (
                len(end_callbacks) > 0
                or self._turn.short_circuited_by == by_callback
            )
        params_clause = (
            f" with params {dict(with_params)!r}" if with_params else ""
        )
        return self._check(
            passed,
            pos_msg=(
                f"Expected turn to end session"
                f"{f' by callback {by_callback!r}' if by_callback else ''}"
                f"{params_clause}, "
                f"but end_session={actual!r}, "
                f"short_circuited_by={self._turn.short_circuited_by!r}."
            ),
            neg_msg=(
                f"Expected turn NOT to end session"
                f"{f' by callback {by_callback!r}' if by_callback else ''}"
                f"{params_clause}, "
                f"but end_session={actual!r}."
            ),
            expected={
                "end_session": True,
                "by_callback": by_callback,
                "with_params": dict(with_params) if with_params else None,
            },
            actual=actual,
        )

    # 2. Prompt & Hydration Security / Integrity matchers
    def _prompt_text_for_step(self, ls: LlmStepTrace) -> str:
        chunks: list[str] = []
        sys_text = _extract_system_instruction_text(ls.request)
        if sys_text:
            chunks.append(sys_text)
        if ls.request is not None and ls.request.contents:
            for content in ls.request.contents:
                for part in content.parts or []:
                    if part.text is not None:
                        chunks.append(part.text)
        return "\n".join(chunks)

    def _full_serializable_prompt_surface(self, ls: LlmStepTrace) -> str:
        """Extracts all text, function calls, function responses, inline data,
        and tool schemas sent to the LLM.
        """
        req = ls.request
        if req is None:
            return ""
        chunks: list[str] = [self._prompt_text_for_step(ls)]
        if req.contents:
            for content in req.contents:
                for part in content.parts or []:
                    if part.function_call is not None:
                        chunks.append(part.function_call.name or "")
                        chunks.append(
                            json.dumps(
                                part.function_call.args or {},
                                default=str,
                                ensure_ascii=False,
                            )
                        )
                    if part.function_response is not None:
                        chunks.append(part.function_response.name or "")
                        chunks.append(
                            json.dumps(
                                part.function_response.response or {},
                                default=str,
                                ensure_ascii=False,
                            )
                        )
                    if part.inline_data is not None:
                        if part.inline_data.display_name:
                            chunks.append(part.inline_data.display_name)
                        if part.inline_data.transcript_field:
                            chunks.append(part.inline_data.transcript_field)
                        if isinstance(part.inline_data.data, bytes):
                            chunks.append(
                                part.inline_data.data.decode(
                                    "utf-8", errors="ignore"
                                )
                            )
        if req.config is not None and req.config.tools:
            for tool in req.config.tools:
                for decl in tool.function_declarations or []:
                    chunks.append(decl.name or "")
                    chunks.append(decl.description or "")
                    if decl.parameters:
                        chunks.append(
                            json.dumps(
                                decl.parameters,
                                default=str,
                                ensure_ascii=False,
                            )
                        )
                    if decl.response:
                        chunks.append(
                            json.dumps(
                                decl.response,
                                default=str,
                                ensure_ascii=False,
                            )
                        )
        return "\n".join(chunks)

    def to_contain_in_system_instruction(
        self,
        substring_or_regex: str | Pattern[str],
        *,
        hop: int = 1,
    ) -> TurnExpectation:
        """Asserts that `llm_request.config.system_instruction` at 1-based `hop`
        matches `substring_or_regex`.
        """
        steps = self._resolve_llm_steps(hop)
        sys_text = "\n".join(
            _extract_system_instruction_text(s.request) for s in steps
        )
        passed = _match_text_or_regex(substring_or_regex, sys_text)
        return self._check(
            passed,
            pos_msg=(
                f"Expected system_instruction (hop={hop}) to match "
                f"{substring_or_regex!r}, "
                f"but got {sys_text[:500]!r}."
            ),
            neg_msg=(
                f"Expected system_instruction (hop={hop}) NOT to match "
                f"{substring_or_regex!r}, "
                f"but it was present in {sys_text[:500]!r}."
            ),
            expected=substring_or_regex,
            actual=sys_text[:500] + ("..." if len(sys_text) > 500 else ""),
        )

    def to_have_in_prompt(
        self,
        substring_or_regex: str | Pattern[str],
        *,
        hop: int = 1,
    ) -> TurnExpectation:
        """Asserts that the hydrated prompt (`system_instruction` or `contents`)
        at 1-based `hop` matches `substring_or_regex`.
        """
        steps = self._resolve_llm_steps(hop)
        prompt_text = "\n".join(self._prompt_text_for_step(s) for s in steps)
        passed = _match_text_or_regex(substring_or_regex, prompt_text)
        return self._check(
            passed,
            pos_msg=(
                f"Expected LLM prompt (hop={hop}) to match "
                f"{substring_or_regex!r}, "
                f"but it was not found in hydrated prompt."
            ),
            neg_msg=(
                f"Expected LLM prompt (hop={hop}) NOT to match "
                f"{substring_or_regex!r}, "
                f"but it was present in hydrated prompt."
            ),
            expected=substring_or_regex,
            actual=prompt_text[:500]
            + ("..." if len(prompt_text) > 500 else ""),
        )

    def to_leak_in_prompt(
        self,
        *forbidden_substrings: str,
        hop: int | None = None,
    ) -> TurnExpectation:
        """Checks whether any of `forbidden_substrings` appear anywhere in the
        LLM request payload across hops and `agent_hops`.

        Scans `system_instruction`, `contents` text, `<state_update>` /
        `<current_state>`,
        `function_call` / `function_response` payloads, `inline_data`, and tool
        declarations.
        Typically chained with `.not_`:
            `expect_turn(trace).not_.to_leak_in_prompt("SECRET_P1",
            "raw_token")`
        """
        if not forbidden_substrings:
            raise ValueError(
                "to_leak_in_prompt() requires at least one substring to check."
            )
        steps = list(self._resolve_llm_steps(hop))
        if hop is None and self._turn.agent_hops:
            seen_ids = {id(s) for s in steps}
            for hop_trace in list(self._turn.agent_hops):
                for hs in hop_trace.llm_steps:
                    if id(hs) not in seen_ids:
                        steps.append(hs)
                        seen_ids.add(id(hs))

        leaked: list[str] = []
        for s in steps:
            txt = self._full_serializable_prompt_surface(s)
            for sub in forbidden_substrings:
                if sub in txt and sub not in leaked:
                    leaked.append(sub)
        passed = len(leaked) > 0
        return self._check(
            passed,
            pos_msg=(
                f"Expected LLM prompt (hop={hop}) to contain leaked "
                "substring(s) from "
                f"{list(forbidden_substrings)!r}, but none appeared in prompt."
            ),
            neg_msg=(
                f"Expected LLM prompt (hop={hop}) NOT to leak any of "
                f"{list(forbidden_substrings)!r}, but found leaked "
                f"substring(s): {leaked!r}."
            ),
            expected=list(forbidden_substrings),
            actual=leaked,
            focus_state_keys=list(forbidden_substrings),
        )

    def to_have_unresolved_placeholders(
        self,
        *,
        hop: int | None = None,
    ) -> TurnExpectation:
        """Checks whether any unresolved `{var}` or `{{var}}` placeholders
        remain in the prompt.

        Typically chained with `.not_`:
            `expect_turn(trace).not_.to_have_unresolved_placeholders()`
        """
        steps = self._resolve_llm_steps(hop)
        found: list[str] = []
        for s in steps:
            for ph in _find_unresolved_placeholders(
                self._prompt_text_for_step(s)
            ):
                if ph not in found:
                    found.append(ph)
        passed = len(found) > 0
        return self._check(
            passed,
            pos_msg=(
                f"Expected LLM prompt (hop={hop}) to have unresolved "
                "placeholders, "
                "but all placeholders were resolved."
            ),
            neg_msg=(
                f"Expected LLM prompt (hop={hop}) NOT to have unresolved "
                "placeholders, "
                f"but found unresolved placeholder(s): {found!r}."
            ),
            expected="unresolved placeholders present",
            actual=found,
        )

    def to_inject_state_update(
        self,
        key: str | None = None,
        expected_value: Any = _UNSET,
        *,
        keys: Sequence[str] | None = None,
        hop: int = 1,
        source: str = "state_update",
        **expected_kv: Any,
    ) -> TurnExpectation:
        """Asserts that `<state_update>` (or `<current_state>` when
        `source="current_state"` / `"any"`) injected at 1-based `hop` includes
        `key` (and optional `expected_value` / `expected_kv`).
        """
        keys_to_check: list[str] = [str(k) for k in (keys or ())]
        kv_to_check: dict[str, Any] = dict(expected_kv)

        if key is not None:
            if expected_value is not _UNSET:
                kv_to_check[key] = expected_value
            else:
                keys_to_check.append(key)

        if not keys_to_check and not kv_to_check:
            raise ValueError(
                "to_inject_state_update() requires at least one key name or "
                "key=value pair."
            )

        steps = self._resolve_llm_steps(hop)
        merged_updates: dict[str, Any] = {}
        for s in steps:
            merged_updates.update(
                _extract_injected_state_updates(s.request, source=source)
            )

        missing_keys = [k for k in keys_to_check if k not in merged_updates]
        kv_mismatches = _subset_mismatches(kv_to_check, merged_updates)
        if self._negated:
            passed = any(k in merged_updates for k in keys_to_check) or any(
                k in merged_updates and not _values_differ(merged_updates[k], v)
                for k, v in kv_to_check.items()
            )
        else:
            passed = len(missing_keys) == 0 and len(kv_mismatches) == 0
        focus = list(keys_to_check) + list(kv_to_check.keys())

        return self._check(
            passed,
            pos_msg=(
                f"Expected LLM hop {hop} <{source}> to inject "
                f"keys={keys_to_check!r} "
                f"and values={kv_to_check!r}, but injected <{source}> was "
                f"{merged_updates!r} "
                f"(missing_keys={missing_keys!r}, "
                f"mismatches={kv_mismatches!r})."
            ),
            neg_msg=(
                f"Expected LLM hop {hop} <{source}> NOT to inject "
                f"keys={keys_to_check!r} "
                f"and values={kv_to_check!r}, but injected <{source}> was "
                f"{merged_updates!r}."
            ),
            expected={"keys": keys_to_check, "values": kv_to_check},
            actual=merged_updates,
            focus_state_keys=focus,
        )

    def to_expose_tools(
        self,
        *tool_names: str | Sequence[str],
        hop: int = 1,
    ) -> TurnExpectation:
        """Asserts that all `tool_names` are exposed in
        `llm_request.config.tools` at 1-based `hop`.
        """
        flat_names: list[str] = []
        for item in tool_names:
            if isinstance(item, str):
                flat_names.append(item)
            elif isinstance(item, Sequence):
                flat_names.extend(str(x) for x in item)
            else:
                raise TypeError(
                    f"Tool name must be str, got {type(item).__name__}"
                )
        if not flat_names:
            raise ValueError(
                "to_expose_tools() requires at least one tool name."
            )

        steps = self._resolve_llm_steps(hop)
        active = _extract_active_tools(steps[-1].request)
        if self._negated:
            passed = any(name in active for name in flat_names)
        else:
            passed = all(name in active for name in flat_names)
        return self._check(
            passed,
            pos_msg=(
                f"Expected LLM hop {hop} to expose tool(s) {flat_names!r}, "
                f"but active tools were {active!r}."
            ),
            neg_msg=(
                f"Expected LLM hop {hop} NOT to expose tool(s) {flat_names!r}, "
                f"but active tools were {active!r}."
            ),
            expected=flat_names,
            actual=active,
        )

    def to_exclude_tools(
        self,
        *tool_names: str | Sequence[str],
        hop: int = 1,
    ) -> TurnExpectation:
        """Asserts that none of `tool_names` are exposed in
        `llm_request.config.tools` at 1-based `hop`.
        """
        flat_names: list[str] = []
        for item in tool_names:
            if isinstance(item, str):
                flat_names.append(item)
            elif isinstance(item, Sequence):
                flat_names.extend(str(x) for x in item)
            else:
                raise TypeError(
                    f"Tool name must be str, got {type(item).__name__}"
                )
        if not flat_names:
            raise ValueError(
                "to_exclude_tools() requires at least one tool name."
            )

        steps = self._resolve_llm_steps(hop)
        active = _extract_active_tools(steps[-1].request)
        still_exposed = [name for name in flat_names if name in active]
        if self._negated:
            passed = any(name not in active for name in flat_names)
        else:
            passed = len(still_exposed) == 0
        return self._check(
            passed,
            pos_msg=(
                f"Expected LLM hop {hop} to exclude tool(s) {flat_names!r}, "
                f"but {still_exposed!r} remained in active tools {active!r}."
            ),
            neg_msg=(
                f"Expected LLM hop {hop} NOT to exclude tool(s) "
                f"{flat_names!r}, "
                f"but active tools were {active!r}."
            ),
            expected=f"excluded {flat_names!r}",
            actual=active,
        )

    # 3. Tool Execution & Post-Processing matchers
    def to_have_called_tool(
        self,
        tool_name: str,
        *,
        with_args: Mapping[str, Any] | None = None,
        partial_args: Mapping[str, Any] | None = None,
        with_source: str | None = None,
        with_status: int | None = None,
        times: int | None = None,
    ) -> TurnExpectation:
        """Asserts that `tool_name` was called during the turn.

        Args:
            tool_name: Name of the tool expected to be invoked.
            with_args: Exact argument dictionary match against
            `final_tool_input` or `initial_tool_input`.
            partial_args: Subset argument dictionary match against
            `final_tool_input` or `initial_tool_input`.
            with_source: Optional provenance source filter (`"user-mock"`,
            `"local-python"`, `"openapi-example"`, `"dev-http"`, `"kb-fake"`).
            with_status: Optional HTTP/tool status code filter (e.g. `200`,
            `500`).
            times: Exact number of matching calls expected.
        """
        matching_steps = [
            ts for ts in self._turn.tool_steps if ts.tool_name == tool_name
        ]
        if with_source is not None:
            matching_steps = [
                ts
                for ts in matching_steps
                if getattr(ts, "source", None) == with_source
            ]
        if with_status is not None:
            matching_steps = [
                ts
                for ts in matching_steps
                if (500 if ts.tool_error else 200) == with_status
            ]
        if with_args is not None:
            expected_exact = dict(with_args)
            matching_steps = [
                ts
                for ts in matching_steps
                if expected_exact
                in (ts.final_tool_input, ts.initial_tool_input)
            ]
        if partial_args is not None:
            matching_steps = [
                ts
                for ts in matching_steps
                if not _subset_mismatches(partial_args, ts.final_tool_input)
                or not _subset_mismatches(partial_args, ts.initial_tool_input)
            ]

        def _extract_cb_call_kwargs(c: Mapping[str, Any]) -> dict[str, Any]:
            merged: dict[str, Any] = {}
            raw_args = c.get("args")
            if isinstance(raw_args, Mapping):
                merged.update(raw_args)
            elif (
                isinstance(raw_args, (list, tuple))
                and len(raw_args) == 1
                and isinstance(raw_args[0], Mapping)
            ):
                merged.update(raw_args[0])
            raw_kwargs = c.get("kwargs")
            if isinstance(raw_kwargs, Mapping):
                merged.update(raw_kwargs)
            return merged

        def _match_cb_call_entry(c: Mapping[str, Any]) -> bool:
            if c.get("tool") != tool_name and c.get("name") != tool_name:
                return False
            if with_source is not None and c.get("source") != with_source:
                return False
            if (
                with_status is not None
                and c.get("status_code", 200) != with_status
            ):
                return False
            call_args = _extract_cb_call_kwargs(c)
            if with_args is not None and call_args != dict(with_args):
                return False
            return not (
                partial_args is not None
                and _subset_mismatches(partial_args, call_args)
            )

        # Check mock_tools calls made inside callbacks AND nested_tool_calls
        # inside ToolStepTrace
        cb_tool_calls: list[dict[str, Any]] = []
        for _, st in _iter_stage_traces_from_target(self._turn):
            for c in st.tool_calls:
                if _match_cb_call_entry(c):
                    cb_tool_calls.append(c)
        for ts in self._turn.tool_steps:
            for nc in getattr(ts, "nested_tool_calls", None) or []:
                if _match_cb_call_entry(nc):
                    cb_tool_calls.append(nc)

        total_matches = len(matching_steps) + len(cb_tool_calls)
        if times is not None:
            passed = total_matches == times
        else:
            passed = total_matches >= 1

        recorded_summary = [
            {
                "tool": ts.tool_name,
                "args": ts.final_tool_input,
                "source": getattr(ts, "source", None),
            }
            for ts in self._turn.tool_steps
        ] + cb_tool_calls

        exact_args_clause = (
            f" with exact args {dict(with_args)!r}"
            if with_args is not None
            else ""
        )
        partial_args_clause = (
            f" with partial args {dict(partial_args)!r}"
            if partial_args is not None
            else ""
        )
        source_clause = (
            f" with source {with_source!r}" if with_source is not None else ""
        )
        status_clause = (
            f" with status {with_status!r}" if with_status is not None else ""
        )
        return self._check(
            passed,
            pos_msg=(
                f"Expected turn to call tool {tool_name!r}"
                f"{exact_args_clause}"
                f"{partial_args_clause}"
                f"{source_clause}"
                f"{status_clause}"
                f"{f' exactly {times} time(s)' if times is not None else ''}, "
                f"but recorded tool calls were {recorded_summary!r}."
            ),
            neg_msg=(
                f"Expected turn NOT to call tool {tool_name!r}"
                f"{exact_args_clause}"
                f"{partial_args_clause}, "
                f"but it was called {total_matches} time(s): "
                f"{recorded_summary!r}."
            ),
            expected={
                "tool": tool_name,
                "with_args": dict(with_args) if with_args is not None else None,
                "partial_args": (
                    dict(partial_args) if partial_args is not None else None
                ),
                "with_source": with_source,
                "with_status": with_status,
                "times": times,
            },
            actual=recorded_summary,
        )

    def to_short_circuit_tool(
        self,
        tool_name: str,
        *,
        by: str | None = None,
    ) -> TurnExpectation:
        """Asserts that `tool_name` was short-circuited by `before_tool` without
        running the tool impl.
        """
        matching = [
            ts
            for ts in self._turn.tool_steps
            if ts.tool_name == tool_name and ts.short_circuited_by_before_tool
        ]
        if by is not None:
            matching = [
                ts
                for ts in matching
                if ts.before_tool_trace.short_circuited_by == by
            ]
        passed = len(matching) >= 1
        actual_summary = [
            {
                "tool": ts.tool_name,
                "short_circuited": ts.short_circuited_by_before_tool,
                "by": ts.before_tool_trace.short_circuited_by,
            }
            for ts in self._turn.tool_steps
            if ts.tool_name == tool_name
        ]
        return self._check(
            passed,
            pos_msg=(
                f"Expected tool {tool_name!r} to be short-circuited by "
                "before_tool"
                f"{f' ({by!r})' if by else ''}, "
                f"but tool steps were {actual_summary!r}."
            ),
            neg_msg=(
                f"Expected tool {tool_name!r} NOT to be short-circuited by "
                "before_tool"
                f"{f' ({by!r})' if by else ''}, "
                f"but it was: {actual_summary!r}."
            ),
            expected={"tool": tool_name, "short_circuited": True, "by": by},
            actual=actual_summary,
        )

    def to_have_tool_response(
        self,
        tool_name: str,
        expected_subset: Mapping[str, Any] | None = None,
        /,
        **expected_kv: Any,
    ) -> TurnExpectation:
        """Asserts that `tool_name` produced a `final_tool_response` matching
        `expected_subset` / `expected_kv`.
        """
        expected: dict[str, Any] = {}
        if expected_subset is not None:
            expected.update(dict(expected_subset))
        if expected_kv:
            expected.update(expected_kv)

        matching_steps = [
            ts for ts in self._turn.tool_steps if ts.tool_name == tool_name
        ]
        passed = False
        actual_resps = [ts.final_tool_response for ts in matching_steps]
        for ts in matching_steps:
            resp = ts.final_tool_response or {}
            if not _subset_mismatches(expected, resp):
                passed = True
                break

        return self._check(
            passed,
            pos_msg=(
                f"Expected tool {tool_name!r} final_tool_response to match "
                f"{expected!r}, "
                f"but recorded responses were {actual_resps!r}."
            ),
            neg_msg=(
                f"Expected tool {tool_name!r} final_tool_response NOT to "
                f"match {expected!r}, "
                f"but it matched in {actual_resps!r}."
            ),
            expected=expected,
            actual=actual_resps,
        )

    def to_have_single_part_on_tool_call(
        self,
        tool_name: str | None = None,
    ) -> TurnExpectation:
        """Asserts that any `LlmResponse` containing a `FunctionCall` (for
        `tool_name`) has only a single non-thought part.

        In CES runtime, when an `after_model` callback or LLM emits a client
        function /
        `transfer_to_agent` / `end_session` tool call alongside extra text parts
        in the
        same response, the runtime or client handler can fail or drop parts
        unless stripped
        to a single `FunctionCall` part.
        """
        candidate_responses: list[tuple[str, LlmResponse]] = []
        for ls in self._all_llm_steps():
            if ls.final_response is not None:
                candidate_responses.append(
                    (f"hop#{ls.step_index + 1}", ls.final_response)
                )
        if not candidate_responses and self._turn.final_response is not None:
            candidate_responses.append(
                ("final_response", self._turn.final_response)
            )

        matched_any_fc = False
        violations: list[dict[str, Any]] = []
        for lbl, resp in candidate_responses:
            if resp.content is None or not resp.content.parts:
                continue
            parts = list(resp.content.parts)
            fc_parts = [p for p in parts if p.function_call is not None]
            if tool_name is not None:
                fc_parts = [
                    p
                    for p in fc_parts
                    if (p.function_call and p.function_call.name == tool_name)
                ]
            if not fc_parts:
                continue
            matched_any_fc = True
            non_thought_parts = [
                p for p in parts if not getattr(p, "thought", False)
            ]
            if len(non_thought_parts) != 1:
                violations.append(
                    {
                        "source": lbl,
                        "part_count": len(non_thought_parts),
                        "parts": [
                            (
                                f"function_call:{p.function_call.name}"
                                if p.function_call is not None
                                else f"text:{p.text!r}"
                            )
                            for p in non_thought_parts
                        ],
                    }
                )

        passed = matched_any_fc and len(violations) == 0
        return self._check(
            passed,
            pos_msg=(
                f"Expected LlmResponse with tool call"
                f"{f' {tool_name!r}' if tool_name else ''} to have a single "
                "non-thought part, "
                f"but matched_any_fc={matched_any_fc}, "
                f"violations={violations!r}."
            ),
            neg_msg=(
                f"Expected LlmResponse with tool call"
                f"{f' {tool_name!r}' if tool_name else ''} NOT to have a "
                "single non-thought part, "
                "but all matching tool call responses had exactly 1 "
                "non-thought part."
            ),
            expected="1 non-thought part on tool call response",
            actual=violations
            if violations
            else ("no matching tool call" if not matched_any_fc else 1),
        )

    to_have_only_single_part_on_client_function = (
        to_have_single_part_on_tool_call
    )
    to_have_Only_single_part_On_client_function = (
        to_have_single_part_on_tool_call
    )

    # 4. State & Snapshot matchers
    def to_set_state(
        self,
        _expected_dict: Mapping[str, Any] | None = None,
        /,
        **expected_kv: Any,
    ) -> TurnExpectation:
        """Asserts that `turn.final_state` contains the expected key-value
        pairs.
        """
        expected: dict[str, Any] = {}
        if _expected_dict is not None:
            expected.update(dict(_expected_dict))
        if expected_kv:
            expected.update(expected_kv)
        if not expected:
            raise ValueError(
                "to_set_state() requires at least one key-value pair to assert."
            )

        mismatches = _subset_mismatches(expected, self._turn.final_state)
        passed = len(mismatches) == 0
        actual_subset = {
            k: self._turn.final_state.get(k, "<MISSING>") for k in expected
        }
        return self._check(
            passed,
            pos_msg=(
                f"Expected turn final_state to match {expected!r}, "
                f"but found mismatches: {mismatches!r}."
            ),
            neg_msg=(
                f"Expected turn final_state NOT to match {expected!r}, "
                f"but all keys matched: {actual_subset!r}."
            ),
            expected=expected,
            actual=actual_subset,
            focus_state_keys=list(expected.keys()),
        )

    def to_have_state_key(
        self,
        key: str,
        expected_value: Any = _UNSET,
    ) -> TurnExpectation:
        """Asserts that `key` is present in `turn.final_state` (optionally with
        `expected_value`).
        """
        if expected_value is _UNSET:
            passed = key in self._turn.final_state
            exp_repr: Any = f"key {key!r} present"
        else:
            passed = key in self._turn.final_state and not _values_differ(
                self._turn.final_state[key], expected_value
            )
            exp_repr = {key: expected_value}
        val_clause = (
            f" == {expected_value!r}" if expected_value is not _UNSET else ""
        )
        return self._check(
            passed,
            pos_msg=(
                f"Expected turn final_state to have {key!r}{val_clause}, "
                f"but got {self._turn.final_state.get(key, '<MISSING>')!r}."
            ),
            neg_msg=(
                f"Expected turn final_state NOT to have {key!r}{val_clause}, "
                f"but it was {self._turn.final_state.get(key, '<MISSING>')!r}."
            ),
            expected=exp_repr,
            actual=self._turn.final_state.get(key, "<MISSING>"),
            focus_state_keys=[key],
        )

    def to_have_final_state(
        self,
        key_or_dict: str | Mapping[str, Any] | None = None,
        expected_value: Any = _UNSET,
        **expected_kv: Any,
    ) -> TurnExpectation:
        """Asserts that `turn.final_state` matches `(key, expected_value)`, a
        mapping, or `**expected_kv`.
        """
        if isinstance(key_or_dict, str):
            if expected_value is _UNSET and not expected_kv:
                return self.to_have_state_key(key_or_dict)
            merged = (
                {key_or_dict: expected_value}
                if expected_value is not _UNSET
                else {}
            )
            merged.update(expected_kv)
            return self.to_set_state(merged)
        return self.to_set_state(key_or_dict, **expected_kv)

    def to_match_snapshot(
        self,
        name: str | None = None,
        *,
        include_prompt: bool = True,
        include_full_prompt: bool = False,
        snapshot_dir: str | Path | None = None,
        update_snapshots: bool | None = None,
    ) -> TurnExpectation:
        """Asserts that the deterministic turn snapshot matches the stored
        `.snap.json` entry.
        """
        eff_dir = (
            snapshot_dir if snapshot_dir is not None else self._snapshot_dir
        )
        eff_upd = (
            update_snapshots
            if update_snapshots is not None
            else self._update_snapshots
        )
        mgr = SnapshotManager(snapshot_dir=eff_dir, update_snapshots=eff_upd)
        if not self._negated:
            mgr.match_turn(
                self._turn,
                name=name,
                include_prompt=include_prompt,
                include_full_prompt=include_full_prompt,
                update_snapshots=eff_upd,
            )
            return (
                self._positive_self if self._positive_self is not None else self
            )

        payload = serialize_turn_snapshot(
            self._turn,
            include_prompt=include_prompt,
            include_full_prompt=include_full_prompt,
        )
        exists_on_disk, matches_expected, _, _ = mgr.check_matches_readonly(
            payload, name=name
        )
        if not exists_on_disk:
            raise AgentAssertionError(
                (
                    f"Cannot assert `.not_.to_match_snapshot({name!r})` when "
                    "no "
                    "stored snapshot exists on disk."
                ),
                trace=self._turn,
                runner=self._runner,
            )
        if matches_expected:
            raise AgentAssertionError(
                (
                    f"Expected turn NOT to match snapshot {name!r}, but it "
                    "matched identically."
                ),
                trace=self._turn,
                runner=self._runner,
            )
        return self._positive_self if self._positive_self is not None else self


class SessionExpectation:
    """Fluent Jest-style assertion wrapper for a multi-turn
    `SessionSimulator`.
    """

    def __init__(
        self,
        session: SessionSimulator | Sequence[TurnTrace],
        *,
        runner: CallbackChainRunner | None = None,
        snapshot_dir: str | Path | None = None,
        update_snapshots: bool | None = None,
        _negated: bool = False,
        _positive_self: SessionExpectation | None = None,
    ) -> None:
        if isinstance(session, SessionSimulator):
            self._session: SessionSimulator | None = session
            self._turns = list(session.turns)
            self._agent_history = list(session.agent_history)
            self._state = session.state
            self._runner = runner or session.runner
        elif isinstance(session, Sequence) and not isinstance(
            session, (str, bytes)
        ):
            self._session = None
            turns: list[TurnTrace] = []
            for item in session:
                if (
                    isinstance(item, tuple)
                    and len(item) == 2
                    and isinstance(item[1], TurnTrace)
                ):
                    turns.append(item[1])
                elif isinstance(item, TurnTrace):
                    turns.append(item)
                else:
                    raise TypeError(
                        f"expect_session() sequence items must be TurnTrace, "
                        f"got {type(item).__name__}."
                    )
            self._turns = turns
            history: list[str] = []
            for t in turns:
                for ag in t.routed_agents:
                    if not history or history[-1] != ag:
                        history.append(ag)
            self._agent_history = history
            self._state = turns[-1].final_state if turns else {}
            self._runner = runner or (
                getattr(turns[-1], "_runner", None) if turns else None
            )
        else:
            raise TypeError(
                "expect_session() requires a SessionSimulator or "
                "Sequence[TurnTrace]; "
                f"got {type(session).__name__}."
            )
        self._snapshot_dir = snapshot_dir
        self._update_snapshots = update_snapshots
        self._negated = _negated
        self._positive_self = _positive_self

    @property
    def not_(self) -> SessionExpectation:
        """Inverts the single immediately following matcher assertion."""
        pos = self._positive_self if self._positive_self is not None else self
        target_obj: Any = (
            self._session if self._session is not None else self._turns
        )
        return SessionExpectation(
            target_obj,
            runner=self._runner,
            snapshot_dir=self._snapshot_dir,
            update_snapshots=self._update_snapshots,
            _negated=not self._negated,
            _positive_self=pos,
        )

    def turn(self, index: int = 1) -> TurnExpectation:
        """Returns a `TurnExpectation` for the 1-based turn `index` (or `-1` for
        the last turn).
        """
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or index == 0
            or index < -1
        ):
            raise ValueError(
                "Session turn index must be 1-based (index >= 1) or -1 for "
                f"the last turn, got {index!r}."
            )
        if not self._turns:
            raise AgentAssertionError(
                "Session has no recorded turns.",
                trace=self._session,
                runner=self._runner,
            )
        if index == -1:
            target_turn = self._turns[-1]
        elif index > len(self._turns):
            raise AgentAssertionError(
                (
                    f"Requested 1-based turn {index}, but session only "
                    "recorded "
                    f"{len(self._turns)} turn(s)."
                ),
                trace=self._session,
                expected=f"index <= {len(self._turns)}",
                actual=len(self._turns),
                runner=self._runner,
            )
        else:
            target_turn = self._turns[index - 1]
        return TurnExpectation(
            target_turn,
            runner=self._runner,
            snapshot_dir=self._snapshot_dir,
            update_snapshots=self._update_snapshots,
        )

    def _check(
        self,
        passed: bool,
        *,
        pos_msg: str,
        neg_msg: str,
        expected: Any = None,
        actual: Any = None,
        focus_state_keys: Sequence[str] = (),
    ) -> SessionExpectation:
        trace_target: SessionSimulator | TurnTrace | None = (
            self._session
            if self._session is not None
            else (self._turns[-1] if self._turns else None)
        )
        if not self._negated and not passed:
            raise AgentAssertionError(
                pos_msg,
                trace=trace_target,
                expected=expected,
                actual=actual,
                focus_state_keys=focus_state_keys,
                runner=self._runner,
            )
        if self._negated and passed:
            raise AgentAssertionError(
                neg_msg,
                trace=trace_target,
                expected=f"not ({expected!r})"
                if expected is not None
                else None,
                actual=actual,
                focus_state_keys=focus_state_keys,
                runner=self._runner,
            )
        return self._positive_self if self._positive_self is not None else self

    def to_succeed(self) -> SessionExpectation:
        """Asserts that every turn in the session completed without error."""
        failed_turns = [
            (idx, t.failed_stage, t.error.splitlines()[0] if t.error else "")
            for idx, t in enumerate(self._turns, 1)
            if t.error is not None
        ]
        passed = len(failed_turns) == 0
        return self._check(
            passed,
            pos_msg=(
                f"Expected all {len(self._turns)} session turn(s) to succeed, "
                f"but found failures: {failed_turns!r}."
            ),
            neg_msg=(
                f"Expected session NOT to succeed cleanly, "
                f"but all {len(self._turns)} turn(s) succeeded."
            ),
            expected=[],
            actual=failed_turns,
        )

    to_have_zero_errors = to_succeed

    def to_have_zero_fallbacks(self) -> SessionExpectation:
        """Asserts that no stage across any turn triggered an error fallback."""
        fallback_stages: list[tuple[int, str, str | None]] = []
        for idx, t in enumerate(self._turns, 1):
            for lbl, st in _iter_stage_traces_from_target(t):
                if st.error_outcome == "fallback":
                    fallback_stages.append(
                        (idx, lbl, st.failed_callback or st.culprit_callback)
                    )
        passed = len(fallback_stages) == 0
        return self._check(
            passed,
            pos_msg=(
                f"Expected session to have zero fallback outcomes, "
                f"but found fallbacks: {fallback_stages!r}."
            ),
            neg_msg=(
                "Expected session to have at least one fallback outcome, "
                "but none occurred."
            ),
            expected=[],
            actual=fallback_stages,
        )

    def to_complete_turns(self, expected_count: int) -> SessionExpectation:
        """Asserts that the session executed `expected_count` turns."""
        actual = len(self._turns)
        passed = actual == expected_count
        return self._check(
            passed,
            pos_msg=(
                f"Expected session to have {expected_count} turn(s), but got "
                f"{actual}."
            ),
            neg_msg=(
                f"Expected session NOT to have {expected_count} turn(s), but "
                f"got {actual}."
            ),
            expected=expected_count,
            actual=actual,
        )

    to_have_turn_count = to_complete_turns

    def to_have_routed_agents(
        self,
        *agent_names: str | Sequence[str],
    ) -> SessionExpectation:
        """Asserts that the session's `agent_history` matches `agent_names`."""
        flat_names: list[str] = []
        for item in agent_names:
            if isinstance(item, str):
                flat_names.append(item)
            elif isinstance(item, Sequence):
                flat_names.extend(str(x) for x in item)
            else:
                raise TypeError(
                    f"Agent name must be str, got {type(item).__name__}"
                )

        actual = [a.strip() for a in self._agent_history]
        expected_norm = [a.strip() for a in flat_names]
        passed = actual == expected_norm
        return self._check(
            passed,
            pos_msg=(
                f"Expected session to follow agent path {flat_names!r}, "
                f"but actual agent_history was {self._agent_history!r}."
            ),
            neg_msg=(
                f"Expected session NOT to follow agent path {flat_names!r}, "
                f"but actual agent_history was {self._agent_history!r}."
            ),
            expected=flat_names,
            actual=self._agent_history,
        )

    to_follow_agent_path = to_have_routed_agents

    def to_end_on_agent(self, agent_name: str) -> SessionExpectation:
        """Asserts that the currently active agent at the end of the session is
        `agent_name`.
        """
        actual = (
            self._session.current_agent
            if self._session is not None
            else (self._agent_history[-1] if self._agent_history else "")
        )
        passed = actual.strip() == agent_name.strip()
        return self._check(
            passed,
            pos_msg=(
                f"Expected session to end on agent {agent_name!r}, "
                f"but current_agent is {actual!r}."
            ),
            neg_msg=(
                f"Expected session NOT to end on agent {agent_name!r}, "
                f"but current_agent is {actual!r}."
            ),
            expected=agent_name,
            actual=actual,
        )

    to_end_at_agent = to_end_on_agent

    def to_set_state(
        self,
        _expected_dict: Mapping[str, Any] | None = None,
        /,
        **expected_kv: Any,
    ) -> SessionExpectation:
        """Asserts that the final session state contains the expected key-value
        pairs.
        """
        expected: dict[str, Any] = {}
        if _expected_dict is not None:
            expected.update(dict(_expected_dict))
        if expected_kv:
            expected.update(expected_kv)
        if not expected:
            raise ValueError(
                "to_set_state() requires at least one key-value pair to assert."
            )

        mismatches = _subset_mismatches(expected, self._state)
        passed = len(mismatches) == 0
        actual_subset = {k: self._state.get(k, "<MISSING>") for k in expected}
        return self._check(
            passed,
            pos_msg=(
                f"Expected session final state to match {expected!r}, "
                f"but found mismatches: {mismatches!r}."
            ),
            neg_msg=(
                f"Expected session final state NOT to match {expected!r}, "
                f"but all keys matched: {actual_subset!r}."
            ),
            expected=expected,
            actual=actual_subset,
            focus_state_keys=list(expected.keys()),
        )

    to_set_final_state = to_set_state

    def to_have_state_key(
        self,
        key: str,
        expected_value: Any = _UNSET,
    ) -> SessionExpectation:
        """Asserts that `key` is present in the final session state (optionally
        with `expected_value`).
        """
        if expected_value is _UNSET:
            passed = key in self._state
            exp_repr: Any = f"key {key!r} present"
        else:
            passed = key in self._state and not _values_differ(
                self._state[key], expected_value
            )
            exp_repr = {key: expected_value}
        val_clause = (
            f" == {expected_value!r}" if expected_value is not _UNSET else ""
        )
        return self._check(
            passed,
            pos_msg=(
                f"Expected session state to have {key!r}{val_clause}, "
                f"but got {self._state.get(key, '<MISSING>')!r}."
            ),
            neg_msg=(
                f"Expected session state NOT to have {key!r}{val_clause}, "
                f"but it was {self._state.get(key, '<MISSING>')!r}."
            ),
            expected=exp_repr,
            actual=self._state.get(key, "<MISSING>"),
            focus_state_keys=[key],
        )

    def to_have_final_state(
        self,
        key_or_dict: str | Mapping[str, Any] | None = None,
        expected_value: Any = _UNSET,
        **expected_kv: Any,
    ) -> SessionExpectation:
        """Asserts that the final session state matches `(key, expected_value)`,
        a mapping, or `**expected_kv`.
        """
        if isinstance(key_or_dict, str):
            if expected_value is _UNSET and not expected_kv:
                return self.to_have_state_key(key_or_dict)
            merged = (
                {key_or_dict: expected_value}
                if expected_value is not _UNSET
                else {}
            )
            merged.update(expected_kv)
            return self.to_set_state(merged)
        return self.to_set_state(key_or_dict, **expected_kv)

    def to_stay_within_budget(
        self,
        *,
        max_total_tokens: int | None = None,
        max_duration_ms: float | None = None,
        max_llm_calls: int | None = None,
        max_tool_calls: int | None = None,
    ) -> SessionExpectation:
        """Asserts that cumulative session resource consumption stays within
        specified limits.
        """
        if (
            max_total_tokens is None
            and max_duration_ms is None
            and max_llm_calls is None
            and max_tool_calls is None
        ):
            raise ValueError(
                "to_stay_within_budget() requires at least one budget limit "
                "(max_total_tokens, max_duration_ms, max_llm_calls, or "
                "max_tool_calls)."
            )
        total_tokens = sum(t.total_tokens for t in self._turns)
        total_duration_ms = sum(t.duration_ms for t in self._turns)
        total_llm_calls = sum(
            sum(1 for ls in t.llm_steps if ls.llm_invoked)
            or (1 if t.llm_invoked else 0)
            for t in self._turns
        )
        total_tool_calls = sum(len(t.tool_steps) for t in self._turns)

        violations: list[str] = []
        if max_total_tokens is not None and total_tokens > max_total_tokens:
            violations.append(
                f"total_tokens={total_tokens} > "
                f"max_total_tokens={max_total_tokens}"
            )
        if max_duration_ms is not None and total_duration_ms > max_duration_ms:
            violations.append(
                f"total_duration_ms={total_duration_ms:.2f} > "
                f"max_duration_ms={max_duration_ms:.2f}"
            )
        if max_llm_calls is not None and total_llm_calls > max_llm_calls:
            violations.append(
                f"total_llm_calls={total_llm_calls} > "
                f"max_llm_calls={max_llm_calls}"
            )
        if max_tool_calls is not None and total_tool_calls > max_tool_calls:
            violations.append(
                f"total_tool_calls={total_tool_calls} > "
                f"max_tool_calls={max_tool_calls}"
            )

        passed = len(violations) == 0
        actual_metrics = {
            "total_tokens": total_tokens,
            "total_duration_ms": round(total_duration_ms, 2),
            "total_llm_calls": total_llm_calls,
            "total_tool_calls": total_tool_calls,
        }
        expected_budget = {
            k: v
            for k, v in {
                "max_total_tokens": max_total_tokens,
                "max_duration_ms": max_duration_ms,
                "max_llm_calls": max_llm_calls,
                "max_tool_calls": max_tool_calls,
            }.items()
            if v is not None
        }
        return self._check(
            passed,
            pos_msg=(
                f"Expected session to stay within budget {expected_budget!r}, "
                f"but exceeded: {violations!r} (actual={actual_metrics!r})."
            ),
            neg_msg=(
                f"Expected session to exceed budget {expected_budget!r}, "
                f"but stayed within limits (actual={actual_metrics!r})."
            ),
            expected=expected_budget,
            actual=actual_metrics,
        )

    def to_match_snapshot(
        self,
        name: str | None = None,
        *,
        include_prompt: bool = True,
        include_full_prompt: bool = False,
        snapshot_dir: str | Path | None = None,
        update_snapshots: bool | None = None,
    ) -> SessionExpectation:
        """Asserts that the deterministic session snapshot matches the stored
        `.snap.json` entry.
        """
        eff_dir = (
            snapshot_dir if snapshot_dir is not None else self._snapshot_dir
        )
        eff_upd = (
            update_snapshots
            if update_snapshots is not None
            else self._update_snapshots
        )
        mgr = SnapshotManager(snapshot_dir=eff_dir, update_snapshots=eff_upd)
        target_obj: SessionSimulator | Sequence[TurnTrace] = (
            self._session if self._session is not None else self._turns
        )
        if not self._negated:
            mgr.match_session(
                target_obj,
                name=name,
                include_prompt=include_prompt,
                include_full_prompt=include_full_prompt,
                update_snapshots=eff_upd,
            )
            return (
                self._positive_self if self._positive_self is not None else self
            )

        payload = serialize_session_snapshot(
            target_obj,
            include_prompt=include_prompt,
            include_full_prompt=include_full_prompt,
        )
        exists_on_disk, matches_expected, _, _ = mgr.check_matches_readonly(
            payload, name=name
        )
        if not exists_on_disk:
            raise AgentAssertionError(
                (
                    f"Cannot assert `.not_.to_match_snapshot({name!r})` when "
                    "no "
                    "stored snapshot exists on disk."
                ),
                trace=self._session
                if self._session is not None
                else (self._turns[-1] if self._turns else None),
                runner=self._runner,
            )
        if matches_expected:
            raise AgentAssertionError(
                (
                    f"Expected session NOT to match snapshot {name!r}, but it "
                    "matched identically."
                ),
                trace=self._session
                if self._session is not None
                else (self._turns[-1] if self._turns else None),
                runner=self._runner,
            )
        return self._positive_self if self._positive_self is not None else self


def expect_stage(
    trace_or_tuple: CallbackTrace | tuple[Any, CallbackTrace] | ModeDiffReport,
    *,
    runner: CallbackChainRunner | None = None,
    snapshot_dir: str | Path | None = None,
    update_snapshots: bool | None = None,
) -> StageExpectation:
    """Entry point for Jest-style fluent assertions on a stage trace or
    ModeDiffReport.
    """
    return StageExpectation(
        trace_or_tuple,
        runner=runner,
        snapshot_dir=snapshot_dir,
        update_snapshots=update_snapshots,
    )


def expect_turn(
    turn_or_tuple: TurnTrace | tuple[LlmResponse | None, TurnTrace],
    *,
    runner: CallbackChainRunner | None = None,
    snapshot_dir: str | Path | None = None,
    update_snapshots: bool | None = None,
) -> TurnExpectation:
    """Entry point for Jest-style fluent assertions on a single-turn
    `TurnTrace`.
    """
    return TurnExpectation(
        turn_or_tuple,
        runner=runner,
        snapshot_dir=snapshot_dir,
        update_snapshots=update_snapshots,
    )


def expect_session(
    session: SessionSimulator | Sequence[TurnTrace],
    *,
    runner: CallbackChainRunner | None = None,
    snapshot_dir: str | Path | None = None,
    update_snapshots: bool | None = None,
) -> SessionExpectation:
    """Entry point for Jest-style fluent assertions on a multi-turn
    `SessionSimulator`.
    """
    return SessionExpectation(
        session,
        runner=runner,
        snapshot_dir=snapshot_dir,
        update_snapshots=update_snapshots,
    )


class ToolSpyExpectation:
    """Fluent Jest-style assertions for a `ToolSpy` instance."""

    def __init__(self, spy: Any, *, negated: bool = False) -> None:
        self._spy = spy
        self._negated = negated

    @property
    def not_(self) -> ToolSpyExpectation:
        return ToolSpyExpectation(self._spy, negated=not self._negated)

    def to_have_been_called(
        self,
        *,
        times: int | None = None,
        with_args: Mapping[str, Any] | None = None,
        partial_args: Mapping[str, Any] | None = None,
    ) -> ToolSpyExpectation:
        calls = list(getattr(self._spy, "calls", []))
        matching = calls
        if with_args is not None:
            matching = [
                c
                for c in matching
                if isinstance(c, Mapping)
                and not _subset_mismatches(with_args, c)
            ]
        if partial_args is not None:
            matching = [
                c
                for c in matching
                if isinstance(c, Mapping)
                and not _subset_mismatches(partial_args, c)
            ]
        if times is not None:
            passed = len(matching) == times
        else:
            passed = len(matching) > 0
        if self._negated:
            passed = not passed
        if not passed:
            raise AgentAssertionError(
                f"Expected ToolSpy '{getattr(self._spy, 'name', 'spy')}' call "
                "assertion to pass "
                f"(times={times}, with_args={with_args}, "
                f"partial_args={partial_args}), "
                f"but recorded calls were {calls!r}."
            )
        return self

    def to_have_called_tool(
        self,
        tool_name: str | None = None,
        *,
        times: int | None = None,
        with_args: Mapping[str, Any] | None = None,
        partial_args: Mapping[str, Any] | None = None,
    ) -> ToolSpyExpectation:
        spy_name = getattr(self._spy, "name", None)
        if tool_name is not None and spy_name and spy_name != tool_name:
            if not self._negated:
                raise AgentAssertionError(
                    f"Expected ToolSpy name '{tool_name}', got '{spy_name}'."
                )
            return self
        return self.to_have_been_called(
            times=times,
            with_args=with_args,
            partial_args=partial_args,
        )


def expect(
    target: Any,
    *,
    runner: CallbackChainRunner | None = None,
    snapshot_dir: str | Path | None = None,
    update_snapshots: bool | None = None,
) -> (
    StageExpectation | TurnExpectation | SessionExpectation | ToolSpyExpectation
):
    """Unified Jest-style `expect(target)` entry point dispatching to stage,
    turn, session, or ToolSpy matchers.
    """
    if (
        hasattr(target, "assert_called")
        and hasattr(target, "calls")
        and not isinstance(target, (CallbackTrace, TurnTrace))
    ):
        return ToolSpyExpectation(target)
    if isinstance(target, (CallbackTrace, ModeDiffReport)) or (
        isinstance(target, tuple)
        and len(target) == 2
        and isinstance(target[1], CallbackTrace)
    ):
        return expect_stage(
            target,
            runner=runner,
            snapshot_dir=snapshot_dir,
            update_snapshots=update_snapshots,
        )
    if isinstance(target, TurnTrace) or (
        isinstance(target, tuple)
        and len(target) == 2
        and isinstance(target[1], TurnTrace)
    ):
        return expect_turn(
            target,
            runner=runner,
            snapshot_dir=snapshot_dir,
            update_snapshots=update_snapshots,
        )
    return expect_session(
        target,
        runner=runner,
        snapshot_dir=snapshot_dir,
        update_snapshots=update_snapshots,
    )
