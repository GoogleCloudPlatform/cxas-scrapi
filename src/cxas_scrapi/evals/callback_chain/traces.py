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

"""Trace and diagnostic data structures for CallbackChainRunner."""

from __future__ import annotations

import copy
import dataclasses
import math
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from cxas_scrapi.utils.callback_libs import LlmRequest, LlmResponse

StageType = Literal[
    "before_agent",
    "after_agent",
    "before_model",
    "after_model",
    "before_tool",
    "after_tool",
]

ExecutionMode = Literal["per_callback", "combined"]
ImageVersion = Literal["v1", "v2"]
ErrorOutcome = Literal["none", "fallback", "end_session", "error"]
ErrorPhase = Literal[
    "callback_execution",
    "end_of_chain_serialization",
    "post_validation",
]

CHAIN_MODE_ENV = "CXAS_CALLBACK_MODE"
CHAIN_LLM_ENV = "CXAS_CALLBACK_LLM"

VALID_MODALITIES: frozenset[str] = frozenset({"TEXT", "VOICE", "UNSPECIFIED"})
VALID_CHANNELS: frozenset[str] = frozenset(
    {
        "API",
        "WEB_UI",
        "GOOGLE_TELEPHONY_PLATFORM",
        "TWILIO",
        "FIVE9",
        "GENESYS_CLOUD",
        "AVAYA",
        "AUDIO_CODES",
        "UNSPECIFIED",
        "UNKNOWN",
    }
)


@dataclasses.dataclass
class CallbackSpec:
    """Represents a single callback in a stage chain."""

    name: str
    stage: StageType
    python_code: str
    path: str | None = None
    description: str = ""
    disabled: bool = False
    is_global: bool = False


@dataclasses.dataclass
class CallbackStepTrace:
    """Execution trace for a single callback step within a stage chain."""

    name: str
    is_global: bool = False
    returned_override: bool = False
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    state_after: dict[str, Any] = dataclasses.field(default_factory=dict)
    warnings: list[str] = dataclasses.field(default_factory=list)
    duration_ms: float = 0.0
    python_code: str | None = None
    path: str | None = None

    @property
    def status(self) -> Literal["EXECUTED", "SHORT_CIRCUITED", "FAILED"]:
        """Execution status of this callback step (`"EXECUTED"`,
        `"SHORT_CIRCUITED"`, or `"FAILED"`).
        """
        if self.error is not None:
            return "FAILED"
        if self.returned_override:
            return "SHORT_CIRCUITED"
        return "EXECUTED"


@dataclasses.dataclass
class CallbackTrace:
    """Comprehensive execution trace for a single lifecycle stage chain.

    Attributes:
        stage: Canonical lifecycle stage name.
        mode: Execution mode (`"per_callback"` or `"combined"`).
        image_version: Sandbox image version (`"v1"` or `"v2"`).
        executed_callbacks: Ordered names of callbacks that started execution.
        disabled_callbacks: Names of callbacks skipped due to `disabled: true`.
        skipped_callbacks: Names of enabled callbacks skipped due to
        short-circuit
            or an earlier callback exception.
        short_circuited: True if an enabled callback returned a non-None
        override
            (or broke the combined loop).
        short_circuited_by: Name of the callback that triggered
        short-circuiting.
        failed_callback: Name of the callback that raised an exception or
        returned
            an invalid result during `callback_execution` or `post_validation`.
            Set to `None` when `error_phase == "end_of_chain_serialization"`.
        culprit_callback: Name of the callback that caused the failure
        (including
            the callback that first introduced an unserializable state value
            when
            `error_phase == "end_of_chain_serialization"`).
        error_phase: Phase in which an error occurred (`"callback_execution"`,
            `"end_of_chain_serialization"`, or `"post_validation"`).
        error: Formatted exception message and traceback if a failure occurred.
            Note: Because CES `enable_exception_handling=True` converts
            unhandled
            callback exceptions into fallback responses by default, tests should
            assert `trace.error is None` or call `trace.raise_for_error()`.
        error_outcome: Resolved CES error handling outcome (`"fallback"`,
            `"end_session"`, or `"error"`), or `None` if no error occurred.
        consecutive_fallback_count_after: Updated consecutive fallback count
        after
            this stage execution (0 on success, incremented on `"fallback"`).
        stdout: Concatenated stdout across all executed steps in the stage.
        stderr: Concatenated stderr across all executed steps in the stage.
        initial_state: Deep copy of session state at the start of the stage.
        final_state: Deep copy of committed session state at the end of the
        stage.
        final_request: Final `LlmRequest` (only populated for `before_model`).
        override_response: Explicit override returned by a callback (or fallback
        /
            end_session response on error), or `None` if all callbacks returned
            `None`.
        effective_response: Effective response after the stage completes. For
            `after_model`, if `override_response` is `None`, this is the
            original
            `llm_response`; for all other stages, equals `override_response`.
        final_response: Alias for `effective_response` (for `after_model`, the
            effective `LlmResponse`; for other stages, the override response).
        final_tool_input: Final `tool_input` dict (populated for `before_tool`).
        tool_calls: Recorded mock tool invocations `[{"tool": ..., "args":
        ...}]`.
        steps: Per-callback step traces.
        warnings: Runtime diagnostic warnings (e.g., mutable state aliasing
        hazards).
    """

    stage: StageType
    mode: ExecutionMode
    image_version: ImageVersion = "v1"
    executed_callbacks: list[str] = dataclasses.field(default_factory=list)
    disabled_callbacks: list[str] = dataclasses.field(default_factory=list)
    skipped_callbacks: list[str] = dataclasses.field(default_factory=list)
    short_circuited: bool = False
    short_circuited_by: str | None = None
    failed_callback: str | None = None
    culprit_callback: str | None = None
    error_phase: ErrorPhase | None = None
    error: str | None = None
    error_outcome: Literal["fallback", "end_session", "error"] | None = None
    consecutive_fallback_count_after: int = 0
    stdout: str = ""
    stderr: str = ""
    initial_state: dict[str, Any] = dataclasses.field(default_factory=dict)
    final_state: dict[str, Any] = dataclasses.field(default_factory=dict)
    final_request: LlmRequest | None = None
    override_response: Any = None
    effective_response: Any = None
    final_response: Any = None
    final_tool_input: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    steps: list[CallbackStepTrace] = dataclasses.field(default_factory=list)
    warnings: list[str] = dataclasses.field(default_factory=list)
    response_mutations: list[str] = dataclasses.field(default_factory=list)
    request_mutations: list[str] = dataclasses.field(default_factory=list)
    duration_ms: float = 0.0
    raw_exception: Exception | None = dataclasses.field(
        default=None, repr=False, compare=False
    )

    def raise_for_error(self) -> None:
        """Re-raises the underlying exception if this stage encountered an
        error.
        """
        if self.raw_exception is not None:
            raise self.raw_exception
        if self.error is not None:
            raise RuntimeError(self.error)


@dataclasses.dataclass
class ToolStepTrace:
    """Execution trace for a single tool call in
    `run_turn(execute_tools=True)`.
    """

    tool_name: str
    function_call_id: str | None
    initial_tool_input: dict[str, Any]
    final_tool_input: dict[str, Any]
    before_tool_trace: CallbackTrace
    after_tool_trace: CallbackTrace | None = None
    short_circuited_by_before_tool: bool = False
    tool_executed: bool = False
    missing_impl: bool = False
    raw_tool_response: dict[str, Any] | None = None
    final_tool_response: dict[str, Any] | None = None
    tool_error: str | None = None
    tool_duration_ms: float = 0.0
    source: str | None = None
    nested_tool_calls: list[dict[str, Any]] = dataclasses.field(
        default_factory=list
    )
    raw_exception: BaseException | None = dataclasses.field(
        default=None, repr=False, compare=False
    )

    def to_dict(self) -> dict[str, Any]:
        """Serializes this tool step trace into a JSON-compatible dictionary."""
        return {
            "tool_name": self.tool_name,
            "function_call_id": self.function_call_id,
            "initial_tool_input": copy.deepcopy(self.initial_tool_input),
            "final_tool_input": copy.deepcopy(self.final_tool_input),
            "short_circuited_by_before_tool": (
                self.short_circuited_by_before_tool
            ),
            "tool_executed": self.tool_executed,
            "missing_impl": self.missing_impl,
            "raw_tool_response": copy.deepcopy(self.raw_tool_response),
            "final_tool_response": copy.deepcopy(self.final_tool_response),
            "tool_error": self.tool_error,
            "tool_duration_ms": self.tool_duration_ms,
            "total_duration_ms": self.total_duration_ms,
            "source": self.source,
            "nested_tool_calls": copy.deepcopy(self.nested_tool_calls),
        }

    @property
    def total_duration_ms(self) -> float:
        return (
            self.before_tool_trace.duration_ms
            + self.tool_duration_ms
            + (
                self.after_tool_trace.duration_ms
                if self.after_tool_trace is not None
                else 0.0
            )
        )

    @property
    def short_circuited(self) -> bool:
        return self.short_circuited_by_before_tool

    @property
    def missing_tool_impl(self) -> bool:
        return self.missing_impl

    @property
    def initial_input(self) -> dict[str, Any]:
        return self.initial_tool_input

    @property
    def modified_input(self) -> dict[str, Any]:
        return self.final_tool_input


@dataclasses.dataclass
class LlmStepTrace:
    """Execution trace for a single LLM call cycle (`before_model -> MockLLM ->
    after_model`).
    """

    step_index: int
    request: LlmRequest
    before_model_trace: CallbackTrace
    raw_response: LlmResponse | None = None
    after_model_trace: CallbackTrace | None = None
    final_response: LlmResponse | None = None
    llm_invoked: bool = False
    after_model_skipped: bool = False
    llm_duration_ms: float = 0.0
    model_name: str | None = None
    prompt_tokens: int = 0
    candidates_tokens: int = 0
    thoughts_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    finish_reason: str | None = None
    block_reason: str | None = None

    @property
    def short_circuited(self) -> bool:
        return self.before_model_trace.short_circuited

    @property
    def total_duration_ms(self) -> float:
        return (
            self.before_model_trace.duration_ms
            + self.llm_duration_ms
            + (
                self.after_model_trace.duration_ms
                if self.after_model_trace is not None
                else 0.0
            )
        )

    @property
    def llm_request(self) -> LlmRequest:
        return self.request

    @property
    def llm_request_snapshot(self) -> LlmRequest:
        """Snapshot of the `LlmRequest` sent to the model at this hop."""
        return self.request

    @property
    def raw_llm_response(self) -> LlmResponse | None:
        return self.raw_response

    def raise_for_error(self) -> None:
        """Re-raises any exception from `before_model_trace` or
        `after_model_trace`.
        """
        self.before_model_trace.raise_for_error()
        if self.after_model_trace is not None:
            self.after_model_trace.raise_for_error()


class _AgentHopList(list["TurnTrace"]):
    """List of multi-agent transfer hop traces with single-hop fallback
    indexing.

    When no multi-agent transfers occurred (`len(agent_hops) == 0`), indexing
    `agent_hops[0]` or `agent_hops[-1]` returns the owning `TurnTrace` for
    ergonomic single-agent inspection, while `len(agent_hops)` remains `0` and
    iteration yields no transfer hops. Use `trace.hops` to always obtain a
    non-empty list of agent hop traces (`[trace]` for single-agent turns).
    """

    def __init__(
        self,
        iterable: Sequence[TurnTrace] = (),
        *,
        owner: TurnTrace | None = None,
    ) -> None:
        super().__init__(iterable)
        self._owner = owner

    def __getitem__(self, index: Any) -> Any:  # type: ignore[override]
        if (
            len(self) == 0
            and self._owner is not None
            and isinstance(index, int)
            and index in (0, -1)
        ):
            return self._owner
        return super().__getitem__(index)


@dataclasses.dataclass
class TurnTrace:
    """Execution trace for a single-turn pipeline (`run_turn`)."""

    mode: ExecutionMode
    before_model_trace: CallbackTrace
    after_model_trace: CallbackTrace | None
    llm_invoked: bool
    after_model_skipped: bool
    llm_request: LlmRequest | None = None
    raw_llm_response: LlmResponse | None = None
    final_response: LlmResponse | None = None
    final_state: dict[str, Any] = dataclasses.field(default_factory=dict)
    image_version: ImageVersion = "v1"
    before_agent_trace: CallbackTrace | None = None
    after_agent_trace: CallbackTrace | None = None
    tool_steps: list[ToolStepTrace] = dataclasses.field(default_factory=list)
    llm_steps: list[LlmStepTrace] = dataclasses.field(default_factory=list)
    end_session: dict[str, Any] | None = None
    transfer_to: str | None = None
    error: str | None = None
    failed_stage: str | None = None
    failed_tool: str | None = None
    consecutive_fallback_count_after: int = 0
    warnings: list[str] = dataclasses.field(default_factory=list)
    duration_ms: float = 0.0
    routed_agents: list[str] = dataclasses.field(default_factory=list)
    agent_hops: list[TurnTrace] = dataclasses.field(default_factory=list)
    agent_name: str | None = None
    user_input: str | None = None
    _extra_callback_duration_ms: float = dataclasses.field(
        default=0.0, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not isinstance(self.agent_hops, _AgentHopList):
            self.agent_hops = _AgentHopList(self.agent_hops, owner=self)
        elif self.agent_hops._owner is None:
            self.agent_hops._owner = self

    @property
    def hops(self) -> list[TurnTrace]:
        """Returns all agent hop traces, or `[self]` for a single-agent turn."""
        return list(self.agent_hops) if len(self.agent_hops) > 0 else [self]

    @property
    def prompt_tokens(self) -> int:
        return sum(s.prompt_tokens for s in self.llm_steps)

    @property
    def candidates_tokens(self) -> int:
        return sum(s.candidates_tokens for s in self.llm_steps)

    @property
    def thoughts_tokens(self) -> int:
        return sum(s.thoughts_tokens for s in self.llm_steps)

    @property
    def cached_tokens(self) -> int:
        return sum(s.cached_tokens for s in self.llm_steps)

    @property
    def total_tokens(self) -> int:
        return sum(s.total_tokens for s in self.llm_steps)

    @property
    def has_error(self) -> bool:
        return self.error is not None

    @property
    def llm_duration_ms(self) -> float:
        return sum(s.llm_duration_ms for s in self.llm_steps)

    @property
    def callback_duration_ms(self) -> float:
        total = self._extra_callback_duration_ms
        if self.before_agent_trace is not None:
            total += self.before_agent_trace.duration_ms
        if self.llm_steps:
            for ls in self.llm_steps:
                total += ls.before_model_trace.duration_ms
                if ls.after_model_trace is not None:
                    total += ls.after_model_trace.duration_ms
        else:
            total += self.before_model_trace.duration_ms
            if self.after_model_trace is not None:
                total += self.after_model_trace.duration_ms
        for ts in self.tool_steps:
            total += ts.before_tool_trace.duration_ms
            if ts.after_tool_trace is not None:
                total += ts.after_tool_trace.duration_ms
        if self.after_agent_trace is not None:
            total += self.after_agent_trace.duration_ms
        return total

    @property
    def tool_duration_ms(self) -> float:
        return sum(t.tool_duration_ms for t in self.tool_steps)

    @property
    def executed_callbacks(self) -> list[str]:
        out: list[str] = []
        if self.before_agent_trace is not None:
            out.extend(self.before_agent_trace.executed_callbacks)
        if self.llm_steps:
            first_ls = self.llm_steps[0]
            out.extend(first_ls.before_model_trace.executed_callbacks)
            if first_ls.after_model_trace is not None:
                out.extend(first_ls.after_model_trace.executed_callbacks)
        else:
            out.extend(self.before_model_trace.executed_callbacks)
            if self.after_model_trace is not None:
                out.extend(self.after_model_trace.executed_callbacks)
        for ts in self.tool_steps:
            out.extend(ts.before_tool_trace.executed_callbacks)
            if ts.after_tool_trace is not None:
                out.extend(ts.after_tool_trace.executed_callbacks)
        for ls in self.llm_steps[1:]:
            out.extend(ls.before_model_trace.executed_callbacks)
            if ls.after_model_trace is not None:
                out.extend(ls.after_model_trace.executed_callbacks)
        if self.after_agent_trace is not None:
            out.extend(self.after_agent_trace.executed_callbacks)
        return out

    @property
    def short_circuited_by(self) -> str | None:
        if (
            self.before_agent_trace is not None
            and self.before_agent_trace.short_circuited_by
        ):
            return self.before_agent_trace.short_circuited_by
        for ls in self.llm_steps:
            if ls.before_model_trace.short_circuited_by:
                return ls.before_model_trace.short_circuited_by
            if (
                ls.after_model_trace is not None
                and ls.after_model_trace.short_circuited_by
            ):
                return ls.after_model_trace.short_circuited_by
        if self.before_model_trace.short_circuited_by:
            return self.before_model_trace.short_circuited_by
        if self.after_model_trace is not None:
            return self.after_model_trace.short_circuited_by
        return None

    def raise_for_error(self) -> None:
        """Re-raises the first underlying exception encountered during the
        turn.
        """
        if self.before_agent_trace is not None:
            self.before_agent_trace.raise_for_error()
        for ls in self.llm_steps:
            ls.raise_for_error()
        self.before_model_trace.raise_for_error()
        llm_exc = getattr(self, "_llm_raw_exception", None)
        if llm_exc is not None:
            raise llm_exc
        if self.after_model_trace is not None:
            self.after_model_trace.raise_for_error()
        for ts in self.tool_steps:
            ts.before_tool_trace.raise_for_error()
            if ts.raw_exception is not None:
                raise ts.raw_exception
            if ts.tool_error is not None:
                raise RuntimeError(ts.tool_error)
            if ts.after_tool_trace is not None:
                ts.after_tool_trace.raise_for_error()
        if self.after_agent_trace is not None:
            self.after_agent_trace.raise_for_error()
        if self.error is not None:
            raise RuntimeError(self.error)


@dataclasses.dataclass
class ModeDiffReport:
    """Comparison report between `per_callback` and `combined` execution
    modes.
    """

    stage: StageType
    per_callback_trace: CallbackTrace
    combined_trace: CallbackTrace
    per_callback_response: Any
    combined_response: Any
    has_state_diff: bool
    has_response_diff: bool
    has_execution_order_diff: bool
    has_error_diff: bool
    state_diffs: dict[str, dict[str, Any]] = dataclasses.field(
        default_factory=dict
    )
    response_diff: dict[str, Any] | None = None
    normalized_states: dict[str, dict[str, Any]] = dataclasses.field(
        default_factory=dict
    )
    image_version: ImageVersion = "v1"

    @property
    def has_diff(self) -> bool:
        return (
            self.has_state_diff
            or self.has_response_diff
            or self.has_execution_order_diff
            or self.has_error_diff
        )

    @property
    def has_divergence(self) -> bool:
        """Alias for `self.has_diff`."""
        return self.has_diff

    def explain(self) -> list[str]:
        """Generates human-readable root-cause explanations for detected mode
        differences.
        """
        explanations: list[str] = []
        if not self.has_diff:
            return explanations

        # 1. Check runtime aliasing warnings from combined mode
        if self.combined_trace.warnings:
            for w in self.combined_trace.warnings:
                explanations.append(
                    f"[Aliasing / Shared Reference (C012)] {w} In 'combined' "
                    "mode, "
                    "in-place mutations by a subsequent callback affect all "
                    "aliased keys, "
                    "whereas 'per_callback' mode isolates keys via JSON "
                    "round-trip after each step."
                )

        # 2. Check execution order differences (e.g. tool callback returning {}
        # or mid-chain error)
        if self.has_execution_order_diff:
            if (
                self.stage in ("before_tool", "after_tool")
                and self.combined_trace.short_circuited
                and not self.per_callback_trace.short_circuited
                and self.combined_response is None
            ):
                sc_by = self.combined_trace.short_circuited_by
                explanations.append(
                    f"[Tool Empty/Non-Struct Return (C015)] Callback '{sc_by}' "
                    "returned an empty dict `{}` (or non-struct in v2). In "
                    "'combined' mode, "
                    "the Python loop checks `if tool_response is not None: "
                    "break` and skips "
                    "remaining callbacks, whereas in 'per_callback' mode the "
                    "runtime treats "
                    "an empty dict `{}` as no override (`None`) and continues "
                    "executing "
                    "subsequent callbacks."
                )
            elif self.has_error_diff:
                per_failed = self.per_callback_trace.failed_callback
                comb_phase = self.combined_trace.error_phase
                comb_culprit = self.combined_trace.culprit_callback
                explanations.append(
                    "[Error Timing Difference] In 'per_callback' mode, "
                    "state/return "
                    "serialization runs after each callback (failing at "
                    f"'{per_failed}' and "
                    "preserving prior callbacks' committed state), whereas in "
                    "'combined' mode "
                    "serialization runs once at the end of the chain "
                    f"(phase='{comb_phase}', "
                    f"culprit='{comb_culprit}')."
                )
            else:
                per_exec = self.per_callback_trace.executed_callbacks
                comb_exec = self.combined_trace.executed_callbacks
                explanations.append(
                    "[Execution Order Divergence] 'per_callback' executed "
                    f"{per_exec} "
                    f"while 'combined' executed {comb_exec}."
                )

        # 3. Evidence-based detection of int -> float coercion and C016/C017
        # mutations
        coerced_num_keys: set[str] = set()
        for per_step, comb_step in zip(
            self.per_callback_trace.steps,
            self.combined_trace.steps,
            strict=False,
        ):
            for s_key, per_val in per_step.state_after.items():
                if s_key in ("_session", "session_metadata", "current_date"):
                    continue
                comb_val = comb_step.state_after.get(s_key)
                if (
                    isinstance(per_val, float)
                    and isinstance(comb_val, int)
                    and not isinstance(comb_val, bool)
                    and per_val == float(comb_val)
                ):
                    coerced_num_keys.add(s_key)

        resp_muts = (
            self.combined_trace.response_mutations
            or self.per_callback_trace.response_mutations
        )
        req_muts = (
            self.combined_trace.request_mutations
            or self.per_callback_trace.request_mutations
        )

        for key, diff_pair in self.state_diffs.items():
            v_per = diff_pair.get("per_callback")
            v_comb = diff_pair.get("combined")
            if coerced_num_keys:
                explanations.append(
                    f"[Protobuf Struct Number Coercion] State key '{key}' "
                    "differs "
                    f"({v_per!r} vs {v_comb!r}); intermediate state key(s) "
                    f"{sorted(coerced_num_keys)} had `int` coerced to `float` "
                    "across "
                    "Protobuf Struct round-trips in 'per_callback' mode while "
                    "remaining "
                    "`int` in 'combined' mode."
                )
            elif resp_muts:
                explanations.append(
                    "[In-Place LlmResponse Mutation (C016)] State key "
                    f"'{key}' differs "
                    f"({v_per!r} vs {v_comb!r}); callback(s) {resp_muts} "
                    "mutated "
                    "`llm_response` in-place while returning `None` (visible "
                    "to later "
                    "callbacks in 'combined' mode, discarded in "
                    "'per_callback' mode)."
                )
            elif req_muts:
                explanations.append(
                    "[Non-Persisted LlmRequest Mutation (C017)] State key "
                    f"'{key}' differs "
                    f"({v_per!r} vs {v_comb!r}); callback(s) {req_muts} "
                    "mutated "
                    "non-persisted `llm_request` fields while returning `None` "
                    "(visible in 'combined' mode, discarded in 'per_callback' "
                    "mode)."
                )
            elif not self.combined_trace.warnings:
                explanations.append(
                    f"[State Divergence on '{key}'] "
                    f"'per_callback'={v_per!r} vs 'combined'={v_comb!r}."
                )

        if self.has_error_diff and not self.has_execution_order_diff:
            per_fc = self.per_callback_trace.failed_callback
            per_out = self.per_callback_trace.error_outcome
            comb_fc = self.combined_trace.failed_callback
            comb_culp = self.combined_trace.culprit_callback
            comb_out = self.combined_trace.error_outcome
            explanations.append(
                f"[Error Outcome Divergence] 'per_callback' failed={per_fc!r} "
                f"(outcome={per_out!r}) vs 'combined' failed={comb_fc!r}, "
                f"culprit={comb_culp!r} (outcome={comb_out!r})."
            )

        return explanations


@dataclasses.dataclass
class SessionMetricsSummary:
    """Aggregated latency and token metrics across a session or turn batch."""

    label: str = "local"
    model_name: str | None = None
    turn_count: int = 0
    llm_call_count: int = 0
    tool_call_count: int = 0
    callback_execution_count: int = 0
    total_duration_ms: float = 0.0
    llm_duration_ms: float = 0.0
    callback_duration_ms: float = 0.0
    tool_duration_ms: float = 0.0
    prompt_tokens: int = 0
    candidates_tokens: int = 0
    thoughts_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    models_used: list[str] = dataclasses.field(default_factory=list)
    routed_agents: list[str] = dataclasses.field(default_factory=list)

    @property
    def avg_turn_duration_ms(self) -> float:
        return (
            self.total_duration_ms / self.turn_count
            if self.turn_count > 0
            else 0.0
        )

    @property
    def avg_llm_call_duration_ms(self) -> float:
        return (
            self.llm_duration_ms / self.llm_call_count
            if self.llm_call_count > 0
            else 0.0
        )

    def to_dict(self) -> dict[str, Any]:
        """Serializes the summary into a plain dictionary."""
        return dataclasses.asdict(self)

    @classmethod
    def from_turns(
        cls,
        turns: Sequence[TurnTrace],
        *,
        label: str = "local",
        model_name: str | None = None,
    ) -> SessionMetricsSummary:
        """Aggregates a sequence of `TurnTrace` objects into a
        `SessionMetricsSummary`.
        """
        models_seen: list[str] = []
        agents_seen: list[str] = []
        for t in turns:
            for s in t.llm_steps:
                if s.model_name and s.model_name not in models_seen:
                    models_seen.append(s.model_name)
            for ag in t.routed_agents:
                if ag and ag not in agents_seen:
                    agents_seen.append(ag)
        inferred_model = (
            model_name
            if model_name is not None
            else (models_seen[0] if models_seen else None)
        )
        return cls(
            label=label,
            model_name=inferred_model,
            turn_count=len(turns),
            llm_call_count=sum(
                sum(1 for s in t.llm_steps if s.llm_invoked) for t in turns
            ),
            tool_call_count=sum(len(t.tool_steps) for t in turns),
            callback_execution_count=sum(
                len(t.executed_callbacks) for t in turns
            ),
            total_duration_ms=sum(t.duration_ms for t in turns),
            llm_duration_ms=sum(t.llm_duration_ms for t in turns),
            callback_duration_ms=sum(t.callback_duration_ms for t in turns),
            tool_duration_ms=sum(t.tool_duration_ms for t in turns),
            prompt_tokens=sum(t.prompt_tokens for t in turns),
            candidates_tokens=sum(t.candidates_tokens for t in turns),
            thoughts_tokens=sum(t.thoughts_tokens for t in turns),
            cached_tokens=sum(t.cached_tokens for t in turns),
            total_tokens=sum(t.total_tokens for t in turns),
            models_used=models_seen,
            routed_agents=agents_seen,
        )

    @classmethod
    def from_cloud_turn_outputs(
        cls,
        turn_outputs: Sequence[Any] = (),
        *,
        label: str = "Cloud CES Session",
        model_name: str | None = None,
        total_duration_ms: float = 0.0,
        prompt_tokens: int = 0,
        candidates_tokens: int = 0,
        total_tokens: int = 0,
    ) -> SessionMetricsSummary:
        """Constructs a `SessionMetricsSummary` from cloud session turn
        outputs.
        """
        turns_n = len(turn_outputs)
        llm_calls = 0
        tool_calls = 0
        dur = total_duration_ms
        p_tok = prompt_tokens
        c_tok = candidates_tokens
        t_tok = total_tokens
        for item in turn_outputs:
            if isinstance(item, dict):
                dur += float(item.get("duration_ms", 0.0) or 0.0)
                llm_calls += int(item.get("llm_call_count", 1) or 0)
                tool_calls += int(item.get("tool_call_count", 0) or 0)
                p_tok += int(item.get("prompt_tokens", 0) or 0)
                c_tok += int(item.get("candidates_tokens", 0) or 0)
                t_tok += int(item.get("total_tokens", 0) or 0)
            else:
                llm_calls += 1
        if t_tok == 0 and (p_tok or c_tok):
            t_tok = p_tok + c_tok
        return cls(
            label=label,
            model_name=model_name,
            turn_count=turns_n,
            llm_call_count=llm_calls,
            tool_call_count=tool_calls,
            callback_execution_count=0,
            total_duration_ms=dur,
            llm_duration_ms=0.0,
            callback_duration_ms=0.0,
            tool_duration_ms=0.0,
            prompt_tokens=p_tok,
            candidates_tokens=c_tok,
            total_tokens=t_tok,
        )


@dataclasses.dataclass
class BenchmarkComparisonReport:
    """Quantitative comparison between baseline (e.g. cloud CES) and candidate
    runs.
    """

    baseline: SessionMetricsSummary
    candidates: list[SessionMetricsSummary] = dataclasses.field(
        default_factory=list
    )

    @property
    def baseline_label(self) -> str:
        """Label of the baseline execution summary."""
        return self.baseline.label

    @property
    def summaries(self) -> dict[str, SessionMetricsSummary]:
        """Mapping of summary label to `SessionMetricsSummary` (baseline +
        candidates).
        """
        out: dict[str, SessionMetricsSummary] = {
            self.baseline.label: self.baseline
        }
        for idx, cand in enumerate(self.candidates):
            key = (
                cand.label
                if cand.label not in out
                else f"{cand.label}_{idx + 1}"
            )
            out[key] = cand
        return out

    def __getitem__(self, key: str) -> SessionMetricsSummary:
        return self.summaries[key]

    def __contains__(self, key: object) -> bool:
        return key in self.summaries

    def keys(self) -> Any:
        return self.summaries.keys()

    def values(self) -> Any:
        return self.summaries.values()

    def items(self) -> Any:
        return self.summaries.items()

    def speedup_vs_baseline(self, candidate_index: int = 0) -> float | None:
        """Returns `baseline.total_duration_ms /
        candidate.total_duration_ms`.
        """
        if candidate_index < 0 or candidate_index >= len(self.candidates):
            raise IndexError(
                f"candidate_index {candidate_index} out of range for "
                f"{len(self.candidates)} candidate(s)."
            )
        cand = self.candidates[candidate_index]
        if (
            cand.total_duration_ms <= 0.0
            or self.baseline.total_duration_ms <= 0.0
        ):
            return None
        return self.baseline.total_duration_ms / cand.total_duration_ms

    def token_savings_ratio(self, candidate_index: int = 0) -> float | None:
        """Returns `1.0 - (candidate.total_tokens / baseline.total_tokens)`."""
        if candidate_index < 0 or candidate_index >= len(self.candidates):
            raise IndexError(
                f"candidate_index {candidate_index} out of range for "
                f"{len(self.candidates)} candidate(s)."
            )
        if self.baseline.total_tokens <= 0:
            return None
        cand = self.candidates[candidate_index]
        return 1.0 - (cand.total_tokens / self.baseline.total_tokens)

    def to_dict(self) -> dict[str, Any]:
        """Serializes the comparison report into a structured dictionary."""
        summaries_dict = {k: s.to_dict() for k, s in self.summaries.items()}
        deltas: dict[str, dict[str, Any]] = {}
        base = self.baseline
        for idx, cand in enumerate(self.candidates):
            dur_delta = cand.total_duration_ms - base.total_duration_ms
            dur_ratio = (
                cand.total_duration_ms / base.total_duration_ms
                if base.total_duration_ms > 0.0
                else None
            )
            deltas[cand.label] = {
                "total_duration_ms_delta": dur_delta,
                "duration_ratio_vs_baseline": dur_ratio,
                "speedup_vs_baseline": self.speedup_vs_baseline(idx),
                "prompt_tokens_delta": cand.prompt_tokens - base.prompt_tokens,
                "candidates_tokens_delta": (
                    cand.candidates_tokens - base.candidates_tokens
                ),
                "total_tokens_delta": cand.total_tokens - base.total_tokens,
                "token_ratio_vs_baseline": (
                    cand.total_tokens / base.total_tokens
                    if base.total_tokens > 0
                    else None
                ),
                "token_savings_ratio": self.token_savings_ratio(idx),
            }
        return {
            "baseline_label": self.baseline_label,
            "baseline": base.to_dict(),
            "candidates": [c.to_dict() for c in self.candidates],
            "summaries": summaries_dict,
            "deltas_vs_baseline": deltas,
        }

    def to_dataframe(self) -> Any:
        """Converts all summaries and relative deltas into a
        `pandas.DataFrame`.
        """
        import pandas as pd  # noqa: PLC0415

        rows: list[dict[str, Any]] = []
        base = self.baseline
        base_row = base.to_dict()
        base_row["duration_ratio_vs_baseline"] = (
            1.0 if base.total_duration_ms > 0.0 else None
        )
        base_row["speedup_vs_baseline"] = (
            1.0 if base.total_duration_ms > 0.0 else None
        )
        base_row["total_tokens_delta"] = 0
        base_row["token_ratio_vs_baseline"] = (
            1.0 if base.total_tokens > 0 else None
        )
        base_row["token_savings_ratio"] = 0.0 if base.total_tokens > 0 else None
        rows.append(base_row)

        for idx, cand in enumerate(self.candidates):
            row = cand.to_dict()
            row["duration_ratio_vs_baseline"] = (
                cand.total_duration_ms / base.total_duration_ms
                if base.total_duration_ms > 0.0
                else None
            )
            row["speedup_vs_baseline"] = self.speedup_vs_baseline(idx)
            row["total_tokens_delta"] = cand.total_tokens - base.total_tokens
            row["token_ratio_vs_baseline"] = (
                cand.total_tokens / base.total_tokens
                if base.total_tokens > 0
                else None
            )
            row["token_savings_ratio"] = self.token_savings_ratio(idx)
            rows.append(row)
        return pd.DataFrame(rows)

    def to_markdown_table(self) -> str:
        """Renders a GitHub-flavored Markdown table comparing latency &
        tokens.
        """
        headers = (
            "| Label | Turns | LLM Calls | Tool Calls "
            "| Callback Time (ms) | LLM Time (ms) | Total Time (ms) "
            "| Speedup vs Baseline | Total Tokens | Token Savings |"
        )
        sep = (
            "| :--- | ---: | ---: | ---: "
            "| ---: | ---: | ---: "
            "| ---: | ---: | ---: |"
        )
        lines = [headers, sep]

        def _fmt_row(
            s: SessionMetricsSummary,
            speedup: float | None,
            savings: float | None,
        ) -> str:
            cb_str = (
                f"{s.callback_duration_ms:,.1f}"
                if s.callback_duration_ms > 0.0
                or s.callback_execution_count > 0
                else "-"
            )
            llm_str = (
                f"{s.llm_duration_ms:,.1f}"
                if s.llm_duration_ms > 0.0 or s.callback_execution_count > 0
                else "-"
            )
            speedup_str = (
                "-"
                if speedup is None or not math.isfinite(speedup)
                else f"{speedup:,.1f}x"
            )
            savings_str = (
                "-"
                if savings is None or not math.isfinite(savings)
                else f"{savings * 100.0:.1f}%"
            )
            return (
                f"| {s.label} | {s.turn_count} | {s.llm_call_count} "
                f"| {s.tool_call_count} | {cb_str} | {llm_str} "
                f"| {s.total_duration_ms:,.1f} | {speedup_str} "
                f"| {s.total_tokens:,} | {savings_str} |"
            )

        base_speedup = 1.0 if self.baseline.total_duration_ms > 0.0 else None
        base_savings = 0.0 if self.baseline.total_tokens > 0 else None
        lines.append(_fmt_row(self.baseline, base_speedup, base_savings))
        for idx, cand in enumerate(self.candidates):
            lines.append(
                _fmt_row(
                    cand,
                    self.speedup_vs_baseline(idx),
                    self.token_savings_ratio(idx),
                )
            )
        return "\n".join(lines)

    def to_markdown(self) -> str:
        """Alias for `to_markdown_table()`."""
        return self.to_markdown_table()


def _coerce_metrics_summary(
    item: Any, default_label: str
) -> SessionMetricsSummary:
    if isinstance(item, SessionMetricsSummary):
        return item
    if hasattr(item, "get_metrics_summary") and callable(
        item.get_metrics_summary
    ):
        return item.get_metrics_summary(label=default_label)
    if isinstance(item, TurnTrace):
        return SessionMetricsSummary.from_turns([item], label=default_label)
    if isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
        return SessionMetricsSummary.from_turns(list(item), label=default_label)
    raise TypeError(
        "Expected SessionMetricsSummary, SessionSimulator, or "
        "Sequence[TurnTrace], "
        f"got {type(item).__name__}"
    )


def compare_execution_metrics(
    first: SessionMetricsSummary | Sequence[TurnTrace] | dict[str, Any] | Any,
    *candidates: SessionMetricsSummary | Sequence[TurnTrace] | Any,
    baseline: str | None = None,
) -> BenchmarkComparisonReport:
    """Builds a `BenchmarkComparisonReport` comparing baseline and candidate
    runs.

    Supports both:
    - `compare_execution_metrics(baseline_summary, cand1, cand2, ...)`
    - `compare_execution_metrics({"run_a": turns_a, "run_b": turns_b},
      baseline="run_a")`
    """
    if isinstance(first, dict) and not candidates:
        if not first:
            raise ValueError(
                "compare_execution_metrics requires at least one run."
            )
        coerced_map: dict[str, SessionMetricsSummary] = {
            str(lbl): _coerce_metrics_summary(val, str(lbl))
            for lbl, val in first.items()
        }
        base_key = (
            baseline
            if (baseline and baseline in coerced_map)
            else next(iter(coerced_map))
        )
        base_summary = coerced_map[base_key]
        cand_summaries = [s for k, s in coerced_map.items() if k != base_key]
        return BenchmarkComparisonReport(
            baseline=base_summary,
            candidates=cand_summaries,
        )

    base_summary = _coerce_metrics_summary(first, baseline or "Baseline")
    cand_summaries = [
        _coerce_metrics_summary(c, f"Candidate {idx + 1}")
        for idx, c in enumerate(candidates)
    ]
    return BenchmarkComparisonReport(
        baseline=base_summary,
        candidates=cand_summaries,
    )
