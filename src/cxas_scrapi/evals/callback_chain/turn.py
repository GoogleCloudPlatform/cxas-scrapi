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

"""Single-turn (`run_turn`) and multi-turn (`SessionSimulator`) execution."""

from __future__ import annotations

import contextvars
import copy
import dataclasses
import inspect
import json
import logging
import os
import threading
import time
import traceback
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cxas_scrapi.evals.callback_chain.hydration import (
    _ACTIVE_EXECUTION_CTX,
    _ACTIVE_TOOL_CALL_LOG,
    StateUpdateMode,
    build_contents_with_state_updates,
    compute_state_delta,
    get_agent_in_scope_params,
)
from cxas_scrapi.evals.callback_chain.semantics import normalize_image_version
from cxas_scrapi.evals.callback_chain.traces import (
    CHAIN_LLM_ENV,
    CallbackTrace,
    ExecutionMode,
    ImageVersion,
    LlmStepTrace,
    SessionMetricsSummary,
    ToolStepTrace,
    TurnTrace,
)
from cxas_scrapi.utils.callback_libs import (
    CallbackContext,
    Content,
    Event,
    ExternalResponse,
    FunctionResponse,
    GenerateContentConfig,
    LlmRequest,
    LlmResponse,
    MockExternalResponse,
    Part,
    Tool,
    ToolContext,
)

if TYPE_CHECKING:
    from cxas_scrapi.evals.callback_chain.runner import CallbackChainRunner

logger = logging.getLogger(__name__)


class _TurnRunResult(tuple):  # type: ignore[type-arg]
    """2-tuple `(LlmResponse | None, TurnTrace)` that also delegates `TurnTrace`
    attributes.

    Supports both legacy tuple unpacking:
        `resp, trace = runner.run_turn(...)`
    and Jest-style direct trace assignment:
        `trace = runner.run_turn(user_input="...", ...)`
        `expect_turn(trace).to_succeed()`
    """

    def __new__(
        cls, response: LlmResponse | None, trace: TurnTrace
    ) -> _TurnRunResult:
        return super().__new__(cls, (response, trace))

    @property
    def response(self) -> LlmResponse | None:
        return self[0]

    @property
    def trace(self) -> TurnTrace:
        return self[1]

    def __getattr__(self, name: str) -> Any:
        return getattr(self[1], name)

    def __getnewargs__(self) -> tuple[LlmResponse | None, TurnTrace]:
        return (self[0], self[1])

    def __copy__(self) -> _TurnRunResult:
        return _TurnRunResult(self[0], self[1])

    def __deepcopy__(self, memo: Any) -> _TurnRunResult:
        return _TurnRunResult(
            copy.deepcopy(self[0], memo),
            copy.deepcopy(self[1], memo),
        )


def _make_event(
    content: Content | None,
    *,
    author: str,
    invocation_id: str = "",
    state_delta: dict[str, Any] | None = None,
) -> Event:
    """Wraps a `Content` object into an ADK/CES `Event` instance."""
    ev = Event(
        id=f"evt_{uuid.uuid4().hex[:12]}",
        author=author,
        timestamp=int(time.time()),
        invocation_id=invocation_id,
        content=copy.deepcopy(content) if content is not None else None,
    )
    if state_delta:
        ev.actions.state_delta = copy.deepcopy(state_delta)
    return ev


def invoke_tool_impl(
    impl: Callable[..., Any],
    working_tin: dict[str, Any],
    tool_ctx: ToolContext,
) -> Any:
    """Invokes a tool implementation function with `(input, tool_ctx)` or
    `(input,)`.
    """
    sig = inspect.signature(impl)
    params = list(sig.parameters.values())
    pos_params = [
        p
        for p in params
        if p.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]
    has_varargs = any(
        p.kind == inspect.Parameter.VAR_POSITIONAL for p in params
    )
    has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)
    kw_only_params = [
        p for p in params if p.kind == inspect.Parameter.KEYWORD_ONLY
    ]
    if len(pos_params) >= 2 or has_varargs:
        return impl(copy.deepcopy(working_tin), tool_ctx)
    if len(pos_params) == 1:
        return impl(copy.deepcopy(working_tin))
    if has_varkw or kw_only_params:
        return impl(**copy.deepcopy(working_tin))
    return impl()


_LLM_STRING_RESOLVER: Callable[[str], Callable[..., LlmResponse]] | None = None


def register_llm_resolver(
    resolver: Callable[[str], Callable[..., LlmResponse]] | None,
    *,
    overwrite: bool = True,
) -> None:
    """Registers a callable that resolves a model name string into an LLM
    adapter.
    """
    global _LLM_STRING_RESOLVER  # noqa: PLW0603
    if not overwrite and _LLM_STRING_RESOLVER is not None:
        return
    _LLM_STRING_RESOLVER = resolver


def resolve_llm(
    llm_spec: LlmResponse
    | Sequence[LlmResponse]
    | Callable[..., LlmResponse]
    | str
    | None,
) -> LlmResponse | Sequence[LlmResponse] | Callable[..., LlmResponse] | None:
    """Resolves an LLM snapshot JSON path or model string via the registered LLM
    resolver.
    """
    if isinstance(llm_spec, str):
        candidate_path = Path(llm_spec)
        if candidate_path.suffix.lower() == ".json":
            if not candidate_path.is_file():
                raise FileNotFoundError(
                    f"LLM snapshot file not found: {candidate_path}"
                )
            from cxas_scrapi.evals.callback_chain.mocks import (  # noqa: PLC0415
                MockLlm,
            )

            return MockLlm.from_snapshot(candidate_path)
        if _LLM_STRING_RESOLVER is not None:
            return _LLM_STRING_RESOLVER(llm_spec)
        raise RuntimeError(
            f"LLM string {llm_spec!r} requires an existing snapshot .json "
            "file path, "
            "a registered LLM adapter resolver via register_llm_resolver(...), "
            "or a MockLlm / callable passed directly."
        )
    return llm_spec


def invoke_mock_llm(
    mock_llm: LlmResponse
    | Sequence[LlmResponse]
    | Callable[..., LlmResponse]
    | str
    | None,
    ctx: CallbackContext,
    eff_request: LlmRequest,
    call_index: int = 0,
) -> LlmResponse:
    """Invokes `mock_llm` and validates that it returns an `LlmResponse`.

    Supports:
    - `None`: returns `LlmResponse.from_parts([Part.from_text("")])`
    - `str`: resolves via the registered LLM adapter resolver and invokes it
    - `LlmResponse`: returns a deep copy of `mock_llm`
    - `Sequence[LlmResponse]`: returns `mock_llm[call_index]` (raises
      `IndexError`
      if `call_index >= len(mock_llm)`)
    - `Callable`: invokes `mock_llm(llm_request, callback_context)` (or
      `(callback_context, llm_request)` if the first parameter is annotated as
      `CallbackContext` or named `ctx` / `callback_context`),
      `mock_llm(llm_request)`,
      or `mock_llm()`.
    """
    mock_llm = resolve_llm(mock_llm)
    if mock_llm is None:
        return LlmResponse.from_parts([Part.from_text("")])
    if isinstance(mock_llm, LlmResponse):
        return copy.deepcopy(mock_llm)
    if isinstance(mock_llm, Sequence) and not isinstance(
        mock_llm, (str, bytes)
    ):
        if call_index >= len(mock_llm):
            raise IndexError(
                "mock_llm sequence exhausted: requested LLM call index "
                f"{call_index}, "
                f"but sequence has length {len(mock_llm)}"
            )
        item = mock_llm[call_index]
        if not isinstance(item, LlmResponse):
            raise TypeError(
                "mock_llm sequence elements must be LlmResponse, got "
                f"{type(item).__name__}"
            )
        return copy.deepcopy(item)
    if callable(mock_llm):
        sig = inspect.signature(mock_llm)
        pos_params = [
            p
            for p in sig.parameters.values()
            if p.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        has_varargs = any(
            p.kind == inspect.Parameter.VAR_POSITIONAL
            for p in sig.parameters.values()
        )
        if len(pos_params) >= 2:
            first_p = pos_params[0]
            first_name = first_p.name.lower()
            first_ann = str(first_p.annotation)
            if (
                first_name in ("ctx", "callback_context", "context")
                or "CallbackContext" in first_ann
            ):
                raw_resp = mock_llm(ctx, eff_request)
            else:
                raw_resp = mock_llm(eff_request, ctx)
        elif len(pos_params) == 1:
            p_name = pos_params[0].name.lower()
            if p_name in ("ctx", "callback_context", "context"):
                raw_resp = mock_llm(ctx)
            else:
                raw_resp = mock_llm(eff_request)
        elif has_varargs:
            raw_resp = mock_llm(eff_request, ctx)
        else:
            raw_resp = mock_llm()

        if not isinstance(raw_resp, LlmResponse):
            raise TypeError(
                "mock_llm callable must return an LlmResponse, got "
                f"{type(raw_resp).__name__}"
            )
        return copy.deepcopy(raw_resp)

    raise TypeError(
        "mock_llm must be an LlmResponse, Sequence[LlmResponse], or callable, "
        f"got {type(mock_llm).__name__}"
    )


def execute_turn(
    runner: CallbackChainRunner,
    ctx: CallbackContext | str | Content | None = None,
    llm_request: LlmRequest | None = None,
    mock_llm: LlmResponse
    | Sequence[LlmResponse]
    | Callable[..., LlmResponse]
    | str
    | None = None,
    *,
    user_input: str | Content | None = None,
    initial_state: dict[str, Any] | None = None,
    llm_callable: LlmResponse
    | Sequence[LlmResponse]
    | Callable[..., LlmResponse]
    | str
    | None = None,
    llm: LlmResponse
    | Sequence[LlmResponse]
    | Callable[..., LlmResponse]
    | str
    | None = None,
    hydrate_from_agent: bool | None = None,
    auto_load_tools: bool = True,
    mode: ExecutionMode | None = None,
    image_version: ImageVersion | None = None,
    new_session: bool | None = None,
    allow_content_update: bool | None = None,
    simulate_struct_numbers: bool | None = None,
    time_zone: str | None = None,
    mock_tools: dict[str, Any] | None = None,
    execute_tools: bool | None = None,
    tool_impls: dict[str, Callable[..., Any] | dict[str, Any]] | None = None,
    include_agent_callbacks: bool | None = None,
    max_llm_calls: int | None = None,
    consecutive_fallback_count: int | None = None,
    strict: bool | None = None,
    record_session_events: bool = False,
    initial_llm_call_index: int = 0,
    state_update_mode: StateUpdateMode = "rearrange",
    _initial_state_event: Event | None = None,
) -> tuple[LlmResponse | None, TurnTrace]:
    """Executes a single-turn callback and tool pipeline.

    Pipeline: `[before_agent ->] (before_model -> mock_llm -> after_model [->
    tool_steps])*`.

    Runtime alignment notes:
    1. **Per-step fresh `LlmRequest` reconstruction**:
       Each LLM step starts from a fresh copy of the caller's base `llm_request`
       plus accumulated conversation `contents` (tool call + tool response
       events).
       Modifications made by `before_model` (such as `excluded_tools` filtering
       or
       `system_instruction` updates) apply only to that single LLM step and do
       not
       bleed into subsequent LLM steps.
    2. **Unhandled tool callback errors abort the turn**:
       When `before_tool` or `after_tool` encounters an error that resolves to
       `error_outcome == "error"` (`bt_out is None` / `at_out is None`, e.g.,
       when
       `enable_exception_handling=False` or `errorHandlingStrategy="NONE"` or
       `"END_SESSION"` where tool stages do not emit an `end_session` payload),
       the
       turn immediately aborts without running subsequent tools, follow-up LLM
       calls, or `after_agent`.
    3. **Tool implementation exceptions**:
       Exceptions thrown by a tool implementation fail the turn in CES because
       `on_tool_error_callback` is not exposed. When `strict=False`, `run_turn`
       catches exceptions raised by `tool_impls`, records
       `ToolStepTrace.tool_error`
       and `TurnTrace.error` (`failed_stage="tool_execution"`,
       `failed_tool=<name>`),
       and aborts the turn; when `strict=True` (or via
       `turn_trace.raise_for_error()`),
       the exception is re-raised directly.
    """
    turn_t0 = time.perf_counter()

    if isinstance(ctx, (str, Content)):
        if user_input is not None:
            raise ValueError(
                "Cannot specify both positional `user_input` and keyword "
                "`user_input` in `run_turn`."
            )
        user_input = ctx
        ctx = None
    elif ctx is not None and not isinstance(ctx, CallbackContext):
        raise TypeError(
            "Expected CallbackContext, str, Content, or None for `ctx`, got "
            f"{type(ctx).__name__}"
        )

    if ctx is not None and initial_state is not None:
        raise ValueError(
            "Cannot specify both `ctx` and `initial_state` in `run_turn`."
        )
    if llm_callable is not None and (mock_llm is not None or llm is not None):
        raise ValueError(
            "Cannot specify `llm_callable` together with `mock_llm` or `llm` "
            "in `run_turn`."
        )

    raw_llm_candidate = (
        llm_callable
        if llm_callable is not None
        else (llm if llm is not None else mock_llm)
    )
    is_jest_invocation = (
        (ctx is None)
        or (user_input is not None)
        or (initial_state is not None)
        or (llm_callable is not None)
    )

    if ctx is None:
        ctx = CallbackContext(state=copy.deepcopy(initial_state or {}))

    if user_input is not None:
        if ctx.user_content is not None:
            raise ValueError(
                "Cannot specify both `ctx.user_content` and `user_input` in "
                "`run_turn`."
            )
        if isinstance(user_input, str):
            u_content = Content(role="user", parts=[Part.from_text(user_input)])
        elif isinstance(user_input, Content):
            u_content = copy.deepcopy(user_input)
        else:
            raise TypeError(
                "Expected str or Content for `user_input`, got "
                f"{type(user_input).__name__}"
            )
        ctx.user_content = copy.deepcopy(u_content)
        if llm_request is None:
            llm_request = LlmRequest(
                contents=[copy.deepcopy(u_content)],
                config=GenerateContentConfig(),
            )
        elif not llm_request.contents:
            llm_request = copy.deepcopy(llm_request)
            llm_request.contents = [copy.deepcopy(u_content)]

    eff_include_agent_callbacks = (
        is_jest_invocation
        if include_agent_callbacks is None
        else include_agent_callbacks
    )
    eff_execute_tools = (
        is_jest_invocation if execute_tools is None else execute_tools
    )

    env_llm = os.environ.get(CHAIN_LLM_ENV) or None
    using_live_or_env_llm = (
        (llm is not None)
        or (llm_callable is not None)
        or (mock_llm is None and env_llm is not None)
    )
    raw_llm_arg = (
        raw_llm_candidate if raw_llm_candidate is not None else env_llm
    )
    if initial_llm_call_index == 0 and callable(
        getattr(raw_llm_arg, "_notify_turn_start", None)
    ):
        raw_llm_arg._notify_turn_start()
    eff_llm = resolve_llm(raw_llm_arg)
    has_agent_source = runner.agent_dir is not None or bool(runner.agent_config)
    eff_hydrate = (
        ((using_live_or_env_llm or is_jest_invocation) and has_agent_source)
        if hydrate_from_agent is None
        else hydrate_from_agent
    )
    eff_max_llm_calls = (
        (
            5
            if (
                (using_live_or_env_llm or is_jest_invocation)
                and eff_execute_tools
            )
            else 1
        )
        if max_llm_calls is None
        else max_llm_calls
    )
    routed_agents: list[str] = (
        [runner.agent_name]
        if runner.agent_name
        else ([ctx.agent_name] if ctx.agent_name else [])
    )
    resolved_agent_name: str | None = (
        runner.agent_name or ctx.agent_name or None
    )
    resolved_user_input: str | None = None
    if ctx.user_content is not None and ctx.user_content.parts:
        u_texts = [p.text for p in ctx.user_content.parts if p.text is not None]
        if u_texts:
            resolved_user_input = "\n".join(u_texts)
    auto_loaded_impls: dict[str, Callable[..., Any]] = (
        runner.load_local_tool_impls()
        if (auto_load_tools and eff_execute_tools)
        else {}
    )

    eff_mode: ExecutionMode = runner.mode if mode is None else mode
    eff_image_version: ImageVersion = (
        runner.image_version
        if image_version is None
        else normalize_image_version(image_version)
    )
    eff_strict = runner.strict if strict is None else strict
    caller_request = (
        copy.deepcopy(llm_request) if llm_request is not None else LlmRequest()
    )
    base_request = copy.deepcopy(caller_request)
    history_contents: list[Content] = copy.deepcopy(
        caller_request.contents or []
    )
    consecutive_fb = (
        runner.initial_consecutive_fallback_count
        if consecutive_fallback_count is None
        else consecutive_fallback_count
    )

    already_init = bool(getattr(ctx, "_ces_session_initialized", False))
    eff_new_sess = (
        (runner.new_session and not already_init)
        if new_session is None
        else new_session
    )
    initial_turn_state: dict[str, Any] = (
        runner.build_initial_state(
            ctx.state,
            new_session=True,
            simulate_struct_numbers=simulate_struct_numbers,
            time_zone=time_zone,
        )
        if eff_new_sess
        else copy.deepcopy(ctx.state or {})
    )
    last_tracked_state: dict[str, Any] = copy.deepcopy(initial_turn_state)
    init_state_ev = (
        _initial_state_event
        if _initial_state_event is not None
        else _make_event(
            None,
            author="user",
            invocation_id=ctx.invocation_id,
            state_delta=compute_state_delta({}, last_tracked_state),
        )
    )
    if record_session_events:
        working_events: list[Event] = ctx.events
    elif ctx.events:
        working_events = copy.deepcopy(ctx.events)
    else:
        working_events = [
            _make_event(
                copy.deepcopy(c),
                author=c.role or "user",
                invocation_id=ctx.invocation_id,
            )
            for c in history_contents
        ]

    def _record_stage_delta(
        new_state: dict[str, Any] | None,
        target_event: Event | None = None,
        *,
        force_new_event: bool = False,
    ) -> dict[str, Any]:
        nonlocal last_tracked_state
        stage_full = dict(new_state or {})
        delta = compute_state_delta(last_tracked_state, stage_full)
        last_tracked_state = copy.deepcopy(stage_full)
        if not delta:
            return delta
        if target_event is not None:
            target_event.actions.state_delta.update(delta)
        elif (
            not force_new_event
            and working_events
            and not working_events[-1].is_user()
        ):
            working_events[-1].actions.state_delta.update(delta)
        else:
            working_events.append(
                _make_event(
                    None,
                    author=runner.agent_name or "model",
                    invocation_id=ctx.invocation_id,
                    state_delta=delta,
                )
            )
        return delta

    before_agent_trace: CallbackTrace | None = None
    if eff_include_agent_callbacks:
        ba_out, before_agent_trace = runner.run_before_agent(
            ctx,
            mode=eff_mode,
            image_version=eff_image_version,
            new_session=new_session,
            simulate_struct_numbers=simulate_struct_numbers,
            time_zone=time_zone,
            mock_tools=mock_tools,
            consecutive_fallback_count=consecutive_fb,
            strict=eff_strict,
        )
        consecutive_fb = before_agent_trace.consecutive_fallback_count_after
        if ba_out is not None or before_agent_trace.error is not None:
            end_sess: dict[str, Any] | None = None
            transfer_ag: str | None = None
            if ba_out is not None:
                for part in ba_out.parts or []:
                    if part.function_call is not None:
                        fn_n = part.function_call.name or ""
                        fn_a = dict(part.function_call.args or {})
                        if fn_n == "end_session":
                            end_sess = fn_a
                        elif fn_n == "transfer_to_agent":
                            transfer_ag = str(fn_a.get("agent_name") or "")
            ba_resp = (
                LlmResponse(content=ba_out) if ba_out is not None else None
            )
            if ba_resp is not None and ba_resp.content is not None:
                ba_ev = _make_event(
                    ba_resp.content,
                    author=runner.agent_name or "model",
                    invocation_id=ctx.invocation_id,
                )
                _record_stage_delta(
                    before_agent_trace.final_state, target_event=ba_ev
                )
                if record_session_events:
                    ctx.events.append(ba_ev)
            else:
                _record_stage_delta(
                    before_agent_trace.final_state, force_new_event=True
                )
            empty_bm_trace = CallbackTrace(
                stage="before_model",
                mode=eff_mode,
                image_version=eff_image_version,
                initial_state=copy.deepcopy(before_agent_trace.final_state),
                final_state=copy.deepcopy(before_agent_trace.final_state),
                consecutive_fallback_count_after=consecutive_fb,
            )
            ba_turn_trace = TurnTrace(
                mode=eff_mode,
                image_version=eff_image_version,
                before_agent_trace=before_agent_trace,
                before_model_trace=empty_bm_trace,
                after_model_trace=None,
                llm_invoked=False,
                after_model_skipped=True,
                llm_request=copy.deepcopy(base_request),
                raw_llm_response=None,
                final_response=ba_resp,
                final_state=copy.deepcopy(before_agent_trace.final_state),
                end_session=end_sess,
                transfer_to=transfer_ag,
                error=before_agent_trace.error,
                failed_stage="before_agent"
                if before_agent_trace.error is not None
                else None,
                consecutive_fallback_count_after=consecutive_fb,
                duration_ms=(time.perf_counter() - turn_t0) * 1000.0,
                routed_agents=list(routed_agents),
                agent_name=resolved_agent_name,
                user_input=resolved_user_input,
            )
            object.__setattr__(ba_turn_trace, "_runner", runner)
            if not record_session_events:
                _record_collected_turn(ba_turn_trace)
            return _TurnRunResult(ba_resp, ba_turn_trace)
        _record_stage_delta(
            before_agent_trace.final_state, force_new_event=True
        )
    llm_steps: list[LlmStepTrace] = []
    llm_error_info: list[tuple[str, Exception]] = []
    llm_warnings: list[str] = []

    def _execute_llm_step(
        step_idx: int,
        call_idx: int,
        is_first_stage: bool,
        fb_count: int,
    ) -> tuple[
        LlmRequest,
        CallbackTrace,
        CallbackTrace | None,
        LlmResponse | None,
        LlmResponse | None,
        bool,
        bool,
        int,
    ]:
        # Reconstruct a fresh LlmRequest from caller_request + events/history,
        # injecting <state_update> blocks for in-scope dynamic variables and
        # re-hydrating
        # system_instruction and tools before each hop's before_model.
        step_req = copy.deepcopy(caller_request)
        step_req.contents = copy.deepcopy(history_contents)
        if eff_hydrate:
            hydration_state = (
                dict(initial_turn_state)
                if not getattr(ctx, "_ces_session_initialized", False)
                else dict(ctx.state or {})
            )
            in_scope_params = get_agent_in_scope_params(
                runner, state=hydration_state
            )
            if in_scope_params and state_update_mode != "none":
                pending_hop_delta = compute_state_delta(
                    last_tracked_state, hydration_state
                )
                events_for_hydration = [init_state_ev, *working_events]
                if pending_hop_delta:
                    events_for_hydration.append(
                        _make_event(
                            None,
                            author=runner.agent_name or "model",
                            invocation_id=ctx.invocation_id,
                            state_delta=pending_hop_delta,
                        )
                    )
                step_req.contents = build_contents_with_state_updates(
                    events_for_hydration,
                    in_scope_params,
                    mode=state_update_mode,
                )
            step_req = runner.hydrate_llm_request(
                step_req, state=hydration_state
            )

        bm_resp, bm_trace = runner.run_before_model(
            ctx,
            step_req,
            mode=eff_mode,
            image_version=eff_image_version,
            new_session=new_session if is_first_stage else False,
            allow_content_update=allow_content_update,
            simulate_struct_numbers=simulate_struct_numbers,
            time_zone=time_zone,
            mock_tools=mock_tools,
            consecutive_fallback_count=fb_count,
            strict=eff_strict,
        )
        eff_req = bm_trace.final_request or step_req
        next_fb = bm_trace.consecutive_fallback_count_after

        if bm_resp is not None or bm_trace.error is not None:
            llm_steps.append(
                LlmStepTrace(
                    step_index=step_idx,
                    request=copy.deepcopy(eff_req),
                    before_model_trace=bm_trace,
                    raw_response=None,
                    after_model_trace=None,
                    final_response=copy.deepcopy(bm_resp)
                    if bm_resp is not None
                    else None,
                    llm_invoked=False,
                    after_model_skipped=True,
                )
            )
            return (
                eff_req,
                bm_trace,
                None,
                None,
                bm_resp,
                False,
                True,
                next_fb,
            )

        llm_t0 = time.perf_counter()
        try:
            raw_resp = invoke_mock_llm(
                eff_llm, ctx, eff_req, call_index=call_idx
            )
        except Exception as exc:
            llm_dur_ms = (time.perf_counter() - llm_t0) * 1000.0
            exc_msg = str(exc)
            is_contract_error = isinstance(exc, (IndexError, TypeError)) and (
                "mock_llm sequence" in exc_msg
                or "mock_llm must be" in exc_msg
                or "mock_llm callable must return" in exc_msg
            )
            if eff_strict or is_contract_error:
                raise
            err_str = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            llm_error_info.append((err_str, exc))
            step_model_name = getattr(eff_llm, "model", None) or eff_req.model
            llm_steps.append(
                LlmStepTrace(
                    step_index=step_idx,
                    request=copy.deepcopy(eff_req),
                    before_model_trace=bm_trace,
                    raw_response=None,
                    after_model_trace=None,
                    final_response=None,
                    llm_invoked=True,
                    after_model_skipped=True,
                    llm_duration_ms=llm_dur_ms,
                    model_name=step_model_name,
                )
            )
            return (
                eff_req,
                bm_trace,
                None,
                None,
                None,
                True,
                True,
                next_fb,
            )
        llm_dur_ms = (time.perf_counter() - llm_t0) * 1000.0

        if raw_resp.block_reason or (
            raw_resp.finish_reason
            and raw_resp.finish_reason
            in ("SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "RECITATION")
        ):
            llm_warnings.append(
                f"LLM response blocked or filtered "
                f"(finish_reason={raw_resp.finish_reason!r}, "
                f"block_reason={raw_resp.block_reason!r})."
            )

        usage = raw_resp.usage_metadata
        prompt_toks = (
            int(usage.prompt_token_count or 0) if usage is not None else 0
        )
        cand_toks = (
            int(usage.candidates_token_count or 0) if usage is not None else 0
        )
        thoughts_toks = (
            int(getattr(usage, "thoughts_token_count", 0) or 0)
            if usage is not None
            else 0
        )
        cached_toks = (
            int(getattr(usage, "cached_content_token_count", 0) or 0)
            if usage is not None
            else 0
        )
        total_toks = (
            int(usage.total_token_count)
            if (usage is not None and usage.total_token_count is not None)
            else (prompt_toks + cand_toks + thoughts_toks)
        )
        step_model_name = (
            raw_resp.model_version
            or getattr(eff_llm, "model", None)
            or eff_req.model
        )

        am_resp, am_trace = runner.run_after_model(
            ctx,
            raw_resp,
            mode=eff_mode,
            image_version=eff_image_version,
            new_session=False,
            simulate_struct_numbers=simulate_struct_numbers,
            time_zone=time_zone,
            mock_tools=mock_tools,
            consecutive_fallback_count=next_fb,
            strict=eff_strict,
        )
        next_fb = am_trace.consecutive_fallback_count_after
        if am_trace.error is not None:
            eff_resp = am_trace.final_response
        else:
            eff_resp = (
                am_resp if am_resp is not None else copy.deepcopy(raw_resp)
            )
        llm_steps.append(
            LlmStepTrace(
                step_index=step_idx,
                request=copy.deepcopy(eff_req),
                before_model_trace=bm_trace,
                raw_response=copy.deepcopy(raw_resp),
                after_model_trace=am_trace,
                final_response=copy.deepcopy(eff_resp)
                if eff_resp is not None
                else None,
                llm_invoked=True,
                after_model_skipped=False,
                llm_duration_ms=llm_dur_ms,
                model_name=step_model_name,
                prompt_tokens=prompt_toks,
                candidates_tokens=cand_toks,
                thoughts_tokens=thoughts_toks,
                cached_tokens=cached_toks,
                total_tokens=total_toks,
                finish_reason=raw_resp.finish_reason,
                block_reason=raw_resp.block_reason,
            )
        )
        return (
            eff_req,
            bm_trace,
            am_trace,
            raw_resp,
            eff_resp,
            True,
            False,
            next_fb,
        )

    (
        eff_request,
        before_trace,
        after_trace,
        raw_llm_resp,
        final_resp,
        llm_invoked,
        after_model_skipped,
        consecutive_fb,
    ) = _execute_llm_step(
        step_idx=0,
        call_idx=initial_llm_call_index,
        is_first_stage=not eff_include_agent_callbacks,
        fb_count=consecutive_fb,
    )

    turn_error: str | None = None
    failed_stage: str | None = None
    failed_tool: str | None = None
    if before_trace.error is not None:
        turn_error = before_trace.error
        failed_stage = "before_model"
    elif llm_error_info:
        turn_error = llm_error_info[-1][0]
        failed_stage = "llm_invocation"
    elif after_trace is not None and after_trace.error is not None:
        turn_error = after_trace.error
        failed_stage = "after_model"

    tool_steps: list[ToolStepTrace] = []
    end_session_info: dict[str, Any] | None = None
    transfer_target: str | None = None
    current_state = copy.deepcopy(
        after_trace.final_state
        if after_trace is not None
        else before_trace.final_state
    )
    post_am_state = copy.deepcopy(current_state)

    stopped_by_error = False
    llm_calls_made = 1 if llm_invoked else 0
    while (
        eff_execute_tools
        and final_resp is not None
        and final_resp.content is not None
        and (after_trace is None or after_trace.error is None)
        and before_trace.error is None
    ):
        fc_parts = [
            p
            for p in (final_resp.content.parts or [])
            if p.function_call is not None
        ]
        if not fc_parts:
            break

        stopped_by_sys_tool = False
        fn_resp_parts: list[Part] = []

        for idx, part in enumerate(fc_parts):
            fc = part.function_call
            assert fc is not None
            fn_name = fc.name or ""
            fn_args = copy.deepcopy(fc.args or {})

            if fn_name == "end_session":
                end_session_info = fn_args
                stopped_by_sys_tool = True
                break
            if fn_name == "transfer_to_agent":
                transfer_target = str(fn_args.get("agent_name") or "")
                stopped_by_sys_tool = True
                break

            tool_ctx = ToolContext(
                function_call_id=fc.id or f"call_{idx + 1}",
                invocation_id=ctx.invocation_id,
                agent_name=ctx.agent_name,
                session_id=ctx.session_id,
                state=copy.deepcopy(current_state),
                user_content=copy.deepcopy(ctx.user_content),
                events=copy.deepcopy(ctx.events),
                language_code=ctx.language_code,
                app_id=ctx.app_id,
                project_id=ctx.project_id,
                project_number=ctx.project_number,
                location=ctx.location,
                turn_index=ctx.turn_index,
                streaming_stage=ctx.streaming_stage,
            )
            tool_ctx._ces_session_initialized = True
            tool_ctx._image_version = eff_image_version
            tool_obj = Tool(name=fn_name, description="")
            initial_tin = copy.deepcopy(fn_args)
            working_tin = copy.deepcopy(fn_args)

            bt_out, bt_trace = runner.run_before_tool(
                tool_ctx,
                tool_obj,
                working_tin,
                mode=eff_mode,
                image_version=eff_image_version,
                new_session=False,
                simulate_struct_numbers=simulate_struct_numbers,
                mock_tools=mock_tools,
                consecutive_fallback_count=consecutive_fb,
                strict=eff_strict,
            )
            consecutive_fb = bt_trace.consecutive_fallback_count_after
            current_state = copy.deepcopy(bt_trace.final_state)
            ctx.state = copy.deepcopy(current_state)

            # Unhandled before_tool error (bt_out is None) aborts the entire
            # turn
            if bt_trace.error is not None and bt_out is None:
                tool_steps.append(
                    ToolStepTrace(
                        tool_name=fn_name,
                        function_call_id=tool_ctx.function_call_id,
                        initial_tool_input=initial_tin,
                        final_tool_input=copy.deepcopy(working_tin),
                        before_tool_trace=bt_trace,
                        after_tool_trace=None,
                        short_circuited_by_before_tool=False,
                        tool_executed=False,
                        missing_impl=False,
                        raw_tool_response=None,
                        final_tool_response=None,
                    )
                )
                turn_error = bt_trace.error
                failed_stage = "before_tool"
                failed_tool = fn_name
                final_resp = None
                stopped_by_error = True
                break

            short_circuited_bt = bt_out is not None
            tool_executed = False
            missing_impl = False
            raw_t_resp: dict[str, Any] | None = None
            tool_dur_ms = 0.0
            tool_source: str | None = None
            nested_log: list[dict[str, Any]] = []

            if short_circuited_bt:
                # Pass before_tool override dict (including {"error": True})
                # into after_tool.
                raw_t_resp = copy.deepcopy(bt_out)
            else:
                # Precedence: auto_loaded_impls (local-python) <
                # runner.mock_tools (env < local-python < ctor user mock_tools)
                # < ctor _user_mock_tools < per-call mock_tools < per-call
                # tool_impls
                eff_impls = {
                    **auto_loaded_impls,
                    **(runner.mock_tools or {}),
                    **getattr(runner, "_user_mock_tools", {}),
                    **(mock_tools or {}),
                    **(tool_impls or {}),
                }
                if fn_name not in eff_impls or eff_impls[fn_name] is None:
                    missing_impl = True
                    logger.warning(
                        "No tool implementation or mock found for tool '%s' "
                        "during run_turn(execute_tools=True).",
                        fn_name,
                    )
                    if eff_strict:
                        raise KeyError(
                            f"No tool implementation or mock found for tool "
                            f"'{fn_name}' (strict=True)."
                        )
                    tool_steps.append(
                        ToolStepTrace(
                            tool_name=fn_name,
                            function_call_id=tool_ctx.function_call_id,
                            initial_tool_input=initial_tin,
                            final_tool_input=copy.deepcopy(working_tin),
                            before_tool_trace=bt_trace,
                            after_tool_trace=None,
                            short_circuited_by_before_tool=False,
                            tool_executed=False,
                            missing_impl=True,
                            raw_tool_response=None,
                            final_tool_response=None,
                        )
                    )
                    continue

                impl = eff_impls[fn_name]
                tool_source = getattr(impl, "_cxas_tool_source", "user-mock")
                tool_executed = True
                tool_t0 = time.perf_counter()
                ctx_token = _ACTIVE_EXECUTION_CTX.set(tool_ctx)
                log_token = _ACTIVE_TOOL_CALL_LOG.set(nested_log)
                try:
                    if callable(impl):
                        inv_resp = invoke_tool_impl(impl, working_tin, tool_ctx)
                    elif isinstance(impl, dict):
                        inv_resp = copy.deepcopy(impl)
                    else:
                        inv_resp = {"result": copy.deepcopy(impl)}
                except Exception as exc:
                    tool_dur_ms = (time.perf_counter() - tool_t0) * 1000.0
                    # Catch tool exception in non-strict mode, record on trace,
                    # and abort turn
                    if eff_strict:
                        raise
                    t_err = (
                        f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
                    )
                    tool_steps.append(
                        ToolStepTrace(
                            tool_name=fn_name,
                            function_call_id=tool_ctx.function_call_id,
                            initial_tool_input=initial_tin,
                            final_tool_input=copy.deepcopy(working_tin),
                            before_tool_trace=bt_trace,
                            after_tool_trace=None,
                            short_circuited_by_before_tool=False,
                            tool_executed=True,
                            missing_impl=False,
                            raw_tool_response=None,
                            final_tool_response=None,
                            tool_error=t_err,
                            raw_exception=exc,
                            tool_duration_ms=tool_dur_ms,
                            source=tool_source,
                            nested_tool_calls=list(nested_log),
                        )
                    )
                    turn_error = t_err
                    failed_stage = "tool_execution"
                    failed_tool = fn_name
                    final_resp = None
                    stopped_by_error = True
                    break
                else:
                    tool_dur_ms = (time.perf_counter() - tool_t0) * 1000.0
                finally:
                    _ACTIVE_EXECUTION_CTX.reset(ctx_token)
                    _ACTIVE_TOOL_CALL_LOG.reset(log_token)

                if isinstance(
                    inv_resp, (MockExternalResponse, ExternalResponse)
                ):
                    try:
                        parsed_json = inv_resp.json()
                    except Exception:
                        parsed_json = {"result": inv_resp.text()}
                    inv_resp = (
                        parsed_json
                        if isinstance(parsed_json, dict)
                        else {"result": parsed_json}
                    )
                elif not isinstance(inv_resp, dict):
                    inv_resp = {"result": inv_resp}
                raw_t_resp = inv_resp

            assert raw_t_resp is not None
            at_out, at_trace = runner.run_after_tool(
                tool_ctx,
                tool_obj,
                working_tin,
                copy.deepcopy(raw_t_resp),
                mode=eff_mode,
                image_version=eff_image_version,
                new_session=False,
                simulate_struct_numbers=simulate_struct_numbers,
                mock_tools=mock_tools,
                consecutive_fallback_count=consecutive_fb,
                strict=eff_strict,
            )
            consecutive_fb = at_trace.consecutive_fallback_count_after
            current_state = copy.deepcopy(at_trace.final_state)
            ctx.state = copy.deepcopy(current_state)

            # Unhandled after_tool error (at_out is None) aborts the entire turn
            if at_trace.error is not None and at_out is None:
                tool_steps.append(
                    ToolStepTrace(
                        tool_name=fn_name,
                        function_call_id=tool_ctx.function_call_id,
                        initial_tool_input=initial_tin,
                        final_tool_input=copy.deepcopy(working_tin),
                        before_tool_trace=bt_trace,
                        after_tool_trace=at_trace,
                        short_circuited_by_before_tool=short_circuited_bt,
                        tool_executed=tool_executed,
                        missing_impl=missing_impl,
                        raw_tool_response=(
                            copy.deepcopy(raw_t_resp) if tool_executed else None
                        ),
                        final_tool_response=None,
                        tool_duration_ms=tool_dur_ms,
                        source=tool_source,
                        nested_tool_calls=list(nested_log),
                    )
                )
                turn_error = at_trace.error
                failed_stage = "after_tool"
                failed_tool = fn_name
                final_resp = None
                stopped_by_error = True
                break

            final_t_resp = (
                at_out if at_out is not None else copy.deepcopy(raw_t_resp)
            )
            tool_steps.append(
                ToolStepTrace(
                    tool_name=fn_name,
                    function_call_id=tool_ctx.function_call_id,
                    initial_tool_input=initial_tin,
                    final_tool_input=copy.deepcopy(working_tin),
                    before_tool_trace=bt_trace,
                    after_tool_trace=at_trace,
                    short_circuited_by_before_tool=short_circuited_bt,
                    tool_executed=tool_executed,
                    missing_impl=missing_impl,
                    raw_tool_response=(
                        copy.deepcopy(raw_t_resp) if tool_executed else None
                    ),
                    final_tool_response=final_t_resp,
                    tool_duration_ms=tool_dur_ms,
                    source=tool_source,
                    nested_tool_calls=list(nested_log),
                )
            )
            fn_resp_parts.append(
                Part(
                    function_response=FunctionResponse(
                        name=fn_name,
                        response=copy.deepcopy(final_t_resp or {}),
                        id=fc.id,
                    )
                )
            )

        if (
            stopped_by_error
            or stopped_by_sys_tool
            or not fn_resp_parts
            or llm_calls_made >= eff_max_llm_calls
        ):
            break

        # Append model FunctionCall content (preserving any thought /
        # thought_signature parts) and user FunctionResponse content to
        # history_contents
        model_hop_parts = [
            p
            for p in (final_resp.content.parts or [])
            if p.function_call is not None
            or p.thought is True
            or p.thought_signature is not None
        ]
        fc_content = Content(role="model", parts=copy.deepcopy(model_hop_parts))
        fr_content = Content(role="user", parts=fn_resp_parts)
        history_contents.append(fc_content)
        history_contents.append(fr_content)
        fc_ev = _make_event(
            fc_content,
            author=runner.agent_name or "model",
            invocation_id=ctx.invocation_id,
        )
        _record_stage_delta(post_am_state, target_event=fc_ev)
        fr_ev = _make_event(
            fr_content,
            author=runner.agent_name or "agent",
            invocation_id=ctx.invocation_id,
        )
        _record_stage_delta(current_state, target_event=fr_ev)
        working_events.append(fc_ev)
        working_events.append(fr_ev)

        (
            eff_request,
            before_trace,
            next_after_trace,
            next_raw_resp,
            final_resp,
            step_llm_invoked,
            step_am_skipped,
            consecutive_fb,
        ) = _execute_llm_step(
            step_idx=len(llm_steps),
            call_idx=initial_llm_call_index + llm_calls_made,
            is_first_stage=False,
            fb_count=consecutive_fb,
        )
        if step_llm_invoked:
            llm_calls_made += 1
            llm_invoked = True
            raw_llm_resp = next_raw_resp
        after_trace = next_after_trace
        after_model_skipped = step_am_skipped
        current_state = copy.deepcopy(
            after_trace.final_state
            if after_trace is not None
            else before_trace.final_state
        )
        post_am_state = copy.deepcopy(current_state)
        if before_trace.error is not None:
            turn_error = before_trace.error
            failed_stage = "before_model"
        elif llm_error_info:
            turn_error = llm_error_info[-1][0]
            failed_stage = "llm_invocation"
            stopped_by_error = True
        elif after_trace is not None and after_trace.error is not None:
            turn_error = after_trace.error
            failed_stage = "after_model"

    # Unified post-processing: inspect final_resp for end_session /
    # transfer_to_agent
    if (
        not stopped_by_error
        and end_session_info is None
        and transfer_target is None
        and final_resp is not None
        and final_resp.content is not None
    ):
        for part in final_resp.content.parts or []:
            if part.function_call is not None:
                fn_name = part.function_call.name or ""
                fn_args = dict(part.function_call.args or {})
                if fn_name == "end_session":
                    end_session_info = fn_args
                elif fn_name == "transfer_to_agent":
                    transfer_target = str(fn_args.get("agent_name") or "")

    final_event_appended = False
    if final_resp is not None and final_resp.content is not None:
        final_ev = _make_event(
            final_resp.content,
            author=runner.agent_name or "model",
            invocation_id=ctx.invocation_id,
        )
        _record_stage_delta(current_state, target_event=final_ev)
        if record_session_events:
            ctx.events.append(final_ev)
            final_event_appended = True
    else:
        _record_stage_delta(current_state, force_new_event=True)

    if llm_error_info:
        stopped_by_error = True

    after_agent_trace: CallbackTrace | None = None
    if (
        eff_include_agent_callbacks
        and not stopped_by_error
        and end_session_info is None
        and transfer_target is None
    ):
        aa_out, after_agent_trace = runner.run_after_agent(
            ctx,
            mode=eff_mode,
            image_version=eff_image_version,
            new_session=False,
            simulate_struct_numbers=simulate_struct_numbers,
            time_zone=time_zone,
            mock_tools=mock_tools,
            consecutive_fallback_count=consecutive_fb,
            strict=eff_strict,
        )
        consecutive_fb = after_agent_trace.consecutive_fallback_count_after
        current_state = copy.deepcopy(after_agent_trace.final_state)
        if after_agent_trace.error is not None:
            turn_error = after_agent_trace.error
            failed_stage = "after_agent"
            if aa_out is None:
                final_resp = None
                if final_event_appended and ctx.events:
                    ctx.events.pop()
                    final_event_appended = False
        if aa_out is not None:
            final_resp = LlmResponse(content=aa_out)
            if final_event_appended and ctx.events:
                prev_delta = copy.deepcopy(ctx.events[-1].actions.state_delta)
                aa_ev = _make_event(
                    aa_out,
                    author=runner.agent_name or "model",
                    invocation_id=ctx.invocation_id,
                    state_delta=prev_delta,
                )
                _record_stage_delta(
                    after_agent_trace.final_state, target_event=aa_ev
                )
                ctx.events[-1] = aa_ev
            elif record_session_events:
                aa_ev = _make_event(
                    aa_out,
                    author=runner.agent_name or "model",
                    invocation_id=ctx.invocation_id,
                )
                _record_stage_delta(
                    after_agent_trace.final_state, target_event=aa_ev
                )
                ctx.events.append(aa_ev)
            else:
                _record_stage_delta(after_agent_trace.final_state)
        else:
            _record_stage_delta(after_agent_trace.final_state)

    turn_trace = TurnTrace(
        mode=eff_mode,
        image_version=eff_image_version,
        before_agent_trace=before_agent_trace,
        before_model_trace=before_trace,
        after_model_trace=after_trace,
        after_agent_trace=after_agent_trace,
        llm_invoked=llm_invoked,
        after_model_skipped=after_model_skipped,
        llm_request=eff_request,
        raw_llm_response=copy.deepcopy(raw_llm_resp)
        if raw_llm_resp is not None
        else None,
        final_response=final_resp,
        final_state=copy.deepcopy(current_state),
        tool_steps=tool_steps,
        llm_steps=llm_steps,
        end_session=end_session_info,
        transfer_to=transfer_target,
        error=turn_error,
        failed_stage=failed_stage,
        failed_tool=failed_tool,
        warnings=list(llm_warnings),
        consecutive_fallback_count_after=consecutive_fb,
        duration_ms=(time.perf_counter() - turn_t0) * 1000.0,
        routed_agents=list(routed_agents),
        agent_name=resolved_agent_name,
        user_input=resolved_user_input,
    )
    object.__setattr__(turn_trace, "_runner", runner)
    if llm_error_info and getattr(turn_trace, "raw_exception", None) is None:
        turn_trace._llm_raw_exception = llm_error_info[-1][1]  # type: ignore[attr-defined]
    if not record_session_events:
        _record_collected_turn(turn_trace)
    return _TurnRunResult(final_resp, turn_trace)


_ACTIVE_TURN_COLLECTOR: contextvars.ContextVar[list[TurnTrace] | None] = (
    contextvars.ContextVar("_ACTIVE_TURN_COLLECTOR", default=None)
)
_FALLBACK_COLLECTOR_STACK: list[list[TurnTrace]] = []
_COLLECTOR_LOCK = threading.Lock()


def _push_active_turn_collector(
    collector: list[TurnTrace],
) -> contextvars.Token[list[TurnTrace] | None]:
    """Registers an active turn collector in ContextVar and thread-fallback
    stack.
    """
    with _COLLECTOR_LOCK:
        _FALLBACK_COLLECTOR_STACK.append(collector)
    return _ACTIVE_TURN_COLLECTOR.set(collector)


def _pop_active_turn_collector(
    token: contextvars.Token[list[TurnTrace] | None] | None,
    collector: list[TurnTrace] | None = None,
) -> None:
    """Restores the previous turn collector in ContextVar and thread-fallback
    stack.
    """
    if token is not None:
        _ACTIVE_TURN_COLLECTOR.reset(token)
    else:
        _ACTIVE_TURN_COLLECTOR.set(None)
    with _COLLECTOR_LOCK:
        if collector is not None:
            for idx in range(len(_FALLBACK_COLLECTOR_STACK) - 1, -1, -1):
                if _FALLBACK_COLLECTOR_STACK[idx] is collector:
                    _FALLBACK_COLLECTOR_STACK.pop(idx)
                    break
        elif _FALLBACK_COLLECTOR_STACK:
            _FALLBACK_COLLECTOR_STACK.pop()


def _record_collected_turn(turn_trace: TurnTrace) -> None:
    """Appends `turn_trace` to the active ContextVar or thread-fallback
    collector.
    """
    collector = _ACTIVE_TURN_COLLECTOR.get()
    if collector is None:
        with _COLLECTOR_LOCK:
            if _FALLBACK_COLLECTOR_STACK:
                collector = _FALLBACK_COLLECTOR_STACK[-1]
    if collector is not None:
        with _COLLECTOR_LOCK:
            collector.append(turn_trace)


def _merge_before_agent_traces(
    first: CallbackTrace | None,
    second: CallbackTrace | None,
) -> tuple[CallbackTrace | None, float]:
    """Merges two `before_agent` traces across an agent transfer hop.

    Returns `(merged_trace, extra_callback_duration_ms)`.
    """
    if first is None:
        return second, 0.0
    if second is None:
        return first, 0.0
    if not first.executed_callbacks:
        merged = copy.deepcopy(second)
        merged.duration_ms = first.duration_ms + second.duration_ms
        return merged, 0.0
    if not second.executed_callbacks:
        merged = copy.deepcopy(first)
        merged.final_state = copy.deepcopy(second.final_state)
        merged.duration_ms = first.duration_ms + second.duration_ms
        return merged, 0.0

    merged = copy.deepcopy(second)
    merged.initial_state = copy.deepcopy(first.initial_state)
    merged.executed_callbacks = list(first.executed_callbacks) + list(
        second.executed_callbacks
    )
    merged.steps = list(first.steps) + list(second.steps)
    merged.warnings = list(first.warnings) + [
        w for w in second.warnings if w not in first.warnings
    ]
    merged.tool_calls = list(first.tool_calls) + list(second.tool_calls)
    merged.stdout = (first.stdout or "") + (second.stdout or "")
    merged.stderr = (first.stderr or "") + (second.stderr or "")
    merged.duration_ms = first.duration_ms + second.duration_ms
    return merged, 0.0


class SessionSimulator:
    """Multi-turn session simulator preserving state, fallback count, events,
    and turn_index.

    Note on `turn_index`:
    In the CES runtime, turn indices are 1-based (`previousTurnCount + 1`), so
    turn 1 executes with `callback_context.turn_index == 1`, turn 2 with `2`,
    etc.
    Before the first turn is executed (`len(sim.turns) == 0`), `sim.turn_index`
    is `0`.
    """

    def __init__(
        self,
        runner: CallbackChainRunner,
        *,
        runners_by_agent: dict[str, CallbackChainRunner] | None = None,
        state: dict[str, Any] | None = None,
        initial_state: dict[str, Any] | None = None,
        ctx: CallbackContext | None = None,
        session_id: str = "sim_session",
        language_code: str = "en-US",
        llm: LlmResponse
        | Sequence[LlmResponse]
        | Callable[..., LlmResponse]
        | str
        | None = None,
        mock_llm: LlmResponse
        | Sequence[LlmResponse]
        | Callable[..., LlmResponse]
        | str
        | None = None,
        llm_callable: LlmResponse
        | Sequence[LlmResponse]
        | Callable[..., LlmResponse]
        | str
        | None = None,
        hydrate_from_agent: bool | None = None,
        auto_load_tools: bool = True,
        auto_follow_transfers: bool = False,
        max_agent_transfers: int = 3,
        include_agent_callbacks: bool = True,
        execute_tools: bool = True,
        tool_impls: dict[str, Callable[..., Any] | dict[str, Any]]
        | None = None,
        state_update_mode: StateUpdateMode = "rearrange",
        mode: ExecutionMode | None = None,
        image_version: ImageVersion | None = None,
        strict: bool | None = None,
        env_tools: Mapping[str, Callable[..., Any]] | None = None,
        mock_tools: dict[str, Any] | None = None,
    ) -> None:
        if state is None and initial_state is not None:
            state = initial_state
        if llm_callable is not None and (
            llm is not None or mock_llm is not None
        ):
            raise ValueError(
                "Cannot specify `llm_callable` together with `llm` or "
                "`mock_llm`."
            )
        if llm is None and llm_callable is not None:
            llm = llm_callable
        if llm is None and mock_llm is not None:
            llm = mock_llm
        self.runner = runner
        self.env_tools: dict[str, Callable[..., Any]] = (
            dict(env_tools)
            if env_tools is not None
            else dict(getattr(runner, "env_tools", {}))
        )
        self.mock_tools: dict[str, Any] = (
            dict(mock_tools)
            if mock_tools is not None
            else dict(getattr(runner, "_user_mock_tools", {}))
        )
        if mock_tools:
            self.runner.mock_tools.update(mock_tools)
        self.runners_by_agent: dict[str, CallbackChainRunner] = dict(
            runners_by_agent or {}
        )
        if mock_tools:
            for r in self.runners_by_agent.values():
                r.mock_tools.update(mock_tools)
        if runner.agent_name:
            self.runners_by_agent.setdefault(runner.agent_name, runner)
        raw_init_llm = (
            llm if llm is not None else (os.environ.get(CHAIN_LLM_ENV) or None)
        )
        self.llm = resolve_llm(raw_init_llm)
        self.hydrate_from_agent = hydrate_from_agent
        self.auto_load_tools = auto_load_tools
        self.auto_follow_transfers = auto_follow_transfers
        self.max_agent_transfers = max_agent_transfers
        self.include_agent_callbacks = include_agent_callbacks
        self.execute_tools = execute_tools
        self.tool_impls: dict[str, Callable[..., Any] | dict[str, Any]] = dict(
            tool_impls or {}
        )
        self.state_update_mode: StateUpdateMode = state_update_mode
        self.mode = mode
        self.image_version = image_version
        self.strict = strict
        self.consecutive_fallback_count: int = (
            runner.initial_consecutive_fallback_count
        )
        self.turns: list[TurnTrace] = []
        self.warnings: list[str] = []
        init_label = self._runner_label(runner)
        self.agent_history: list[str] = [init_label] if init_label else []
        self._entered_new_agent: bool = True
        self._session_llm_call_index: int = 0

        eff_init_img = normalize_image_version(
            image_version if image_version is not None else runner.image_version
        )
        if ctx is not None:
            self.ctx = ctx
            if not getattr(self.ctx, "_ces_session_initialized", False):
                self.ctx.state = runner.build_initial_state(
                    self.ctx.state, new_session=runner.new_session
                )
                self.ctx._ces_session_initialized = True
            if (
                getattr(self.ctx, "_image_version_explicit", False)
                and getattr(self.ctx, "_image_version", "v1") != eff_init_img
            ):
                warn_msg = (
                    f"SessionSimulator: overriding ctx._image_version="
                    f"'{self.ctx._image_version}' to match session "
                    f"image_version='{eff_init_img}'."
                )
                logger.warning(warn_msg)
                self.warnings.append(warn_msg)
            self.ctx._image_version = eff_init_img
        else:
            init_state = runner.build_initial_state(
                state, new_session=runner.new_session
            )
            self.ctx = CallbackContext(
                state=init_state,
                session_id=session_id,
                agent_name=init_label,
                language_code=language_code,
                turn_index=0,
                _image_version=eff_init_img,
            )
            self.ctx._ces_session_initialized = True

        self._initial_state_event: Event = _make_event(
            None,
            author="user",
            invocation_id="session_init",
            state_delta=compute_state_delta({}, self.ctx.state),
        )
        self._last_committed_state: dict[str, Any] = copy.deepcopy(
            self.ctx.state
        )

    def _runner_label(self, r: CallbackChainRunner) -> str:
        """Resolves a human-readable agent name for `r`."""
        if r.agent_name:
            return r.agent_name
        for k, cand in self.runners_by_agent.items():
            if cand is r:
                return k
        return ""

    @property
    def _default_llm(self) -> Any:
        return self.llm

    @classmethod
    def from_app_dir(
        cls,
        app_dir: str | Path,
        *,
        root_agent: str | None = None,
        env_tools: Mapping[str, Callable[..., Any]] | None = None,
        mock_tools: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> SessionSimulator:
        """Constructs a `SessionSimulator` loading all agents under `app_dir /
        'agents'`.
        """
        from cxas_scrapi.evals.callback_chain.runner import (  # noqa: PLC0415
            CallbackChainRunner as _RunnerCls,
        )

        app_root = Path(app_dir).resolve()
        agents_dir = app_root / "agents"
        runners_map: dict[str, CallbackChainRunner] = {}
        load_warnings: list[str] = []
        eff_strict = bool(kwargs.get("strict", False))
        if agents_dir.is_dir():
            for ag_dir in sorted(agents_dir.iterdir()):
                if not ag_dir.is_dir():
                    continue
                try:
                    r = _RunnerCls.from_agent_dir(
                        ag_dir,
                        app_dir=app_root,
                        env_tools=env_tools,
                        mock_tools=mock_tools,
                    )
                    runners_map[ag_dir.name] = r
                    if r.agent_name:
                        runners_map[r.agent_name] = r
                        runners_map[r.agent_name.strip()] = r
                except Exception as exc:
                    warn_msg = (
                        f"[C019] Failed to load agent '{ag_dir.name}' in "
                        "SessionSimulator.from_app_dir: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    logger.warning(warn_msg)
                    load_warnings.append(warn_msg)
                    if eff_strict:
                        raise
        if not runners_map:
            raise ValueError(f"No valid agents found under '{app_root}'.")

        chosen_root = root_agent
        if not chosen_root:
            app_json = app_root / "app.json"
            if app_json.is_file():
                try:
                    app_cfg = json.loads(app_json.read_text(encoding="utf-8"))
                    chosen_root = app_cfg.get("rootAgent") or app_cfg.get(
                        "root_agent"
                    )
                except Exception:
                    pass
        if chosen_root and chosen_root in runners_map:
            initial_runner = runners_map[chosen_root]
        elif "root_agent" in runners_map:
            initial_runner = runners_map["root_agent"]
        else:
            initial_runner = next(iter(runners_map.values()))

        sim = cls(
            initial_runner,
            runners_by_agent=runners_map,
            env_tools=env_tools,
            mock_tools=mock_tools,
            **kwargs,
        )
        sim.warnings.extend(load_warnings)
        return sim

    @property
    def current_agent(self) -> str:
        """Name of the currently active agent runner."""
        return self._runner_label(self.runner)

    @property
    def state(self) -> dict[str, Any]:
        """Current session state dictionary."""
        return self.ctx.state

    @property
    def events(self) -> list[Event]:
        """Accumulated conversation history (`Event` objects)."""
        return self.ctx.events

    @property
    def turn_index(self) -> int:
        """Current 1-based turn index (`0` before the first turn, `1` on turn 1,
        etc.).
        """
        return self.ctx.turn_index

    def is_agent_entry(self) -> bool:
        """Returns True on turn 1 or immediately after an unfollowed
        `transfer_to_agent`.
        """
        if not self.turns:
            return True
        return self._entered_new_agent

    def _resolve_transfer_runner(
        self, target_agent: str
    ) -> CallbackChainRunner | None:
        if target_agent in self.runners_by_agent:
            return self.runners_by_agent[target_agent]
        stripped_target = target_agent.strip()
        if stripped_target in self.runners_by_agent:
            return self.runners_by_agent[stripped_target]
        for k, r in self.runners_by_agent.items():
            if k.strip() == stripped_target or (
                r.agent_name and r.agent_name.strip() == stripped_target
            ):
                return r
        if self.runner.app_dir is not None:
            from cxas_scrapi.evals.callback_chain.runner import (  # noqa: PLC0415
                CallbackChainRunner as _RunnerCls,
            )

            agents_root = self.runner.app_dir / "agents"
            candidate_dir = agents_root / target_agent
            if not candidate_dir.is_dir() and agents_root.is_dir():
                for sub in sorted(agents_root.iterdir()):
                    if not sub.is_dir():
                        continue
                    if sub.name.strip() == stripped_target:
                        candidate_dir = sub
                        break
                    ag_json = sub / f"{sub.name}.json"
                    if ag_json.is_file():
                        try:
                            cfg = json.loads(
                                ag_json.read_text(encoding="utf-8")
                            )
                            dn = (
                                cfg.get("displayName")
                                or cfg.get("display_name")
                                or cfg.get("name")
                                or ""
                            )
                            if (
                                str(dn) == target_agent
                                or str(dn).strip() == stripped_target
                            ):
                                candidate_dir = sub
                                break
                        except Exception:
                            pass
            if candidate_dir.is_dir():
                loaded = _RunnerCls.from_agent_dir(
                    candidate_dir,
                    app_dir=self.runner.app_dir,
                    mode=self.runner.mode,
                    image_version=self.runner.image_version,
                    new_session=False,
                    env_tools=self.env_tools,
                    mock_tools=self.mock_tools,
                )
                self.runners_by_agent[target_agent] = loaded
                self.runners_by_agent[candidate_dir.name] = loaded
                if loaded.agent_name:
                    self.runners_by_agent[loaded.agent_name] = loaded
                return loaded
        return None

    def turn(
        self,
        user_input: str | Content | None = None,
        *,
        mock_llm: LlmResponse
        | Sequence[LlmResponse]
        | Callable[..., LlmResponse]
        | str
        | None = None,
        llm_callable: LlmResponse
        | Sequence[LlmResponse]
        | Callable[..., LlmResponse]
        | str
        | None = None,
        llm: LlmResponse
        | Sequence[LlmResponse]
        | Callable[..., LlmResponse]
        | str
        | None = None,
        hydrate_from_agent: bool | None = None,
        auto_load_tools: bool | None = None,
        auto_follow_transfers: bool | None = None,
        max_agent_transfers: int | None = None,
        llm_request: LlmRequest | None = None,
        execute_tools: bool | None = None,
        tool_impls: dict[str, Callable[..., Any] | dict[str, Any]]
        | None = None,
        include_agent_callbacks: bool | None = None,
        max_llm_calls: int | None = None,
        state_update_mode: StateUpdateMode | None = None,
        mode: ExecutionMode | None = None,
        image_version: ImageVersion | None = None,
        strict: bool | None = None,
    ) -> TurnTrace:
        """Executes one conversational turn and updates session state and
        history.
        """
        sim_turn_t0 = time.perf_counter()
        # Turn index is 1-based (1 on the first turn).
        self.ctx.turn_index += 1
        self.ctx.invocation_id = f"inv_{uuid.uuid4().hex[:12]}"
        initial_label = self._runner_label(self.runner)
        self.ctx.agent_name = initial_label
        self._entered_new_agent = False

        if not self.turns:
            self._initial_state_event.actions.state_delta = compute_state_delta(
                {}, self.ctx.state
            )
            pre_turn_delta: dict[str, Any] = {}
        else:
            pre_turn_delta = compute_state_delta(
                self._last_committed_state, self.ctx.state
            )

        if isinstance(user_input, str):
            user_content: Content | None = Content(
                role="user", parts=[Part.from_text(user_input)]
            )
        elif isinstance(user_input, Content):
            user_content = copy.deepcopy(user_input)
        else:
            user_content = None

        self.ctx.user_content = copy.deepcopy(user_content)
        if user_content is not None:
            self.ctx.events.append(
                _make_event(
                    user_content,
                    author="user",
                    invocation_id=self.ctx.invocation_id,
                    state_delta=pre_turn_delta,
                )
            )
        elif pre_turn_delta:
            if self.ctx.events:
                self.ctx.events[-1].actions.state_delta.update(pre_turn_delta)
            else:
                self._initial_state_event.actions.state_delta.update(
                    pre_turn_delta
                )

        history_contents = [
            copy.deepcopy(ev.content)
            for ev in self.ctx.events
            if ev.content is not None
        ]
        req = (
            copy.deepcopy(llm_request)
            if llm_request is not None
            else LlmRequest(contents=history_contents)
        )
        if not req.contents and history_contents:
            req.contents = history_contents

        eff_tools = {**self.tool_impls, **(tool_impls or {})}
        eff_strict = (
            self.runner.strict
            if (strict is None and self.strict is None)
            else (self.strict if strict is None else strict)
        )
        if llm_callable is not None and (
            llm is not None or mock_llm is not None
        ):
            raise ValueError(
                "Cannot specify `llm_callable` together with `llm` or "
                "`mock_llm`."
            )
        if llm is None and llm_callable is not None:
            llm = llm_callable
        eff_llm = resolve_llm(
            llm
            if llm is not None
            else (mock_llm if mock_llm is not None else self.llm)
        )
        using_session_llm_seq = (
            llm is None
            and mock_llm is None
            and isinstance(self.llm, Sequence)
            and not isinstance(self.llm, (str, bytes))
        )
        call_offset = (
            self._session_llm_call_index if using_session_llm_seq else 0
        )

        eff_hydrate = (
            self.hydrate_from_agent
            if hydrate_from_agent is None
            else hydrate_from_agent
        )
        eff_auto_tools = (
            self.auto_load_tools if auto_load_tools is None else auto_load_tools
        )
        eff_auto_follow = (
            self.auto_follow_transfers
            if auto_follow_transfers is None
            else auto_follow_transfers
        )
        eff_max_transfers = (
            self.max_agent_transfers
            if max_agent_transfers is None
            else max_agent_transfers
        )
        eff_exec_tools = (
            self.execute_tools if execute_tools is None else execute_tools
        )
        eff_inc_agent_cbs = (
            self.include_agent_callbacks
            if include_agent_callbacks is None
            else include_agent_callbacks
        )
        eff_state_update_mode: StateUpdateMode = (
            self.state_update_mode
            if state_update_mode is None
            else state_update_mode
        )
        eff_mode = self.mode if mode is None else mode
        eff_img = self.image_version if image_version is None else image_version
        self.ctx._image_version = normalize_image_version(
            eff_img if eff_img is not None else self.runner.image_version
        )

        routed_agents: list[str] = [initial_label] if initial_label else []
        _, turn_trace = execute_turn(
            self.runner,
            self.ctx,
            req,
            mock_llm=mock_llm,
            llm=eff_llm if (llm is not None or self.llm is not None) else None,
            hydrate_from_agent=eff_hydrate,
            auto_load_tools=eff_auto_tools,
            mode=eff_mode,
            image_version=eff_img,
            new_session=False,
            execute_tools=eff_exec_tools,
            tool_impls=eff_tools,
            include_agent_callbacks=eff_inc_agent_cbs,
            max_llm_calls=max_llm_calls,
            consecutive_fallback_count=self.consecutive_fallback_count,
            strict=eff_strict,
            record_session_events=True,
            initial_llm_call_index=call_offset,
            state_update_mode=eff_state_update_mode,
            _initial_state_event=self._initial_state_event,
        )
        calls_used = sum(1 for s in turn_trace.llm_steps if s.llm_invoked)
        call_offset += calls_used
        if using_session_llm_seq:
            self._session_llm_call_index = call_offset

        # Directly use TurnTrace.consecutive_fallback_count_after
        self.consecutive_fallback_count = (
            turn_trace.consecutive_fallback_count_after
        )

        # Route to target agent runner when transfer_to_agent occurs
        transfers_done = 0
        agent_hops: list[TurnTrace] = []
        while turn_trace.transfer_to:
            target_agent = turn_trace.transfer_to

            # Always append a paired FunctionResponse event for
            # transfer_to_agent so subsequent turns have valid
            # (function_call, function_response) history
            if self.ctx.events:
                last_ev = self.ctx.events[-1]
                if last_ev.content is not None:
                    fc_parts = [
                        p
                        for p in (last_ev.content.parts or [])
                        if p.function_call is not None
                        and p.function_call.name == "transfer_to_agent"
                    ]
                    if fc_parts:
                        fr_parts = [
                            Part(
                                function_response=FunctionResponse(
                                    name="transfer_to_agent",
                                    response={
                                        "result": (
                                            f"Transferred to {target_agent}"
                                        )
                                    },
                                    id=p.function_call.id
                                    if p.function_call
                                    else None,
                                )
                            )
                            for p in fc_parts
                        ]
                        self.ctx.events.append(
                            _make_event(
                                Content(role="user", parts=fr_parts),
                                author=self._runner_label(self.runner)
                                or target_agent
                                or "agent",
                                invocation_id=self.ctx.invocation_id,
                            )
                        )

            next_runner = self._resolve_transfer_runner(target_agent)
            if next_runner is None:
                warn_msg = (
                    "SessionSimulator: transfer_to_agent target "
                    f"'{target_agent}' "
                    f"not found in runners_by_agent "
                    f"(available: {sorted(self.runners_by_agent.keys())}); "
                    f"staying on agent '{self.runner.agent_name}'."
                )
                logger.warning(warn_msg)
                turn_trace.warnings.append(warn_msg)
                self.warnings.append(warn_msg)
                if eff_strict:
                    raise KeyError(warn_msg)
                break

            self.runner = next_runner
            next_label = self._runner_label(next_runner) or target_agent
            self.ctx.agent_name = next_label
            self.agent_history.append(target_agent)
            self._entered_new_agent = True

            if not eff_auto_follow:
                break
            if transfers_done >= eff_max_transfers:
                max_warn = (
                    "SessionSimulator: "
                    f"max_agent_transfers={eff_max_transfers} "
                    f"reached while transferring to '{target_agent}'; "
                    f"stopping auto-follow (sim.runner is now '{next_label}' "
                    "for the next turn)."
                )
                logger.warning(max_warn)
                turn_trace.warnings.append(max_warn)
                self.warnings.append(max_warn)
                break

            if not agent_hops:
                first_hop = dataclasses.replace(turn_trace, agent_hops=[])
                agent_hops.append(first_hop)

            transfers_done += 1
            routed_agents.append(target_agent)
            self._entered_new_agent = False

            hop_history = [
                copy.deepcopy(ev.content)
                for ev in self.ctx.events
                if ev.content is not None
            ]
            hop_req = LlmRequest(contents=hop_history)
            _, hop_trace = execute_turn(
                self.runner,
                self.ctx,
                hop_req,
                mock_llm=mock_llm,
                llm=eff_llm
                if (llm is not None or self.llm is not None)
                else None,
                hydrate_from_agent=eff_hydrate,
                auto_load_tools=eff_auto_tools,
                mode=eff_mode,
                image_version=eff_img,
                new_session=False,
                execute_tools=eff_exec_tools,
                tool_impls=eff_tools,
                include_agent_callbacks=eff_inc_agent_cbs,
                max_llm_calls=max_llm_calls,
                consecutive_fallback_count=self.consecutive_fallback_count,
                strict=eff_strict,
                record_session_events=True,
                initial_llm_call_index=call_offset,
                state_update_mode=eff_state_update_mode,
                _initial_state_event=self._initial_state_event,
            )
            hop_calls = sum(1 for s in hop_trace.llm_steps if s.llm_invoked)
            call_offset += hop_calls
            if using_session_llm_seq:
                self._session_llm_call_index = call_offset

            self.consecutive_fallback_count = (
                hop_trace.consecutive_fallback_count_after
            )
            agent_hops.append(dataclasses.replace(hop_trace, agent_hops=[]))

            merged_ba, extra_cb_dur = _merge_before_agent_traces(
                turn_trace.before_agent_trace,
                hop_trace.before_agent_trace,
            )
            prev_aa_dur = (
                turn_trace.after_agent_trace.duration_ms
                if turn_trace.after_agent_trace is not None
                else 0.0
            )
            merged_llm_steps = [
                dataclasses.replace(ls, step_index=idx_s)
                for idx_s, ls in enumerate(
                    [*turn_trace.llm_steps, *hop_trace.llm_steps]
                )
            ]
            merged_tool_steps = list(turn_trace.tool_steps) + list(
                hop_trace.tool_steps
            )
            merged_warnings = list(turn_trace.warnings) + [
                w for w in hop_trace.warnings if w not in turn_trace.warnings
            ]
            turn_trace = TurnTrace(
                mode=hop_trace.mode,
                image_version=hop_trace.image_version,
                before_agent_trace=merged_ba,
                before_model_trace=hop_trace.before_model_trace,
                after_model_trace=hop_trace.after_model_trace,
                after_agent_trace=hop_trace.after_agent_trace,
                llm_invoked=turn_trace.llm_invoked or hop_trace.llm_invoked,
                after_model_skipped=hop_trace.after_model_skipped,
                llm_request=hop_trace.llm_request,
                raw_llm_response=hop_trace.raw_llm_response,
                final_response=hop_trace.final_response,
                final_state=copy.deepcopy(hop_trace.final_state),
                tool_steps=merged_tool_steps,
                llm_steps=merged_llm_steps,
                end_session=hop_trace.end_session or turn_trace.end_session,
                transfer_to=hop_trace.transfer_to,
                error=hop_trace.error or turn_trace.error,
                failed_stage=hop_trace.failed_stage or turn_trace.failed_stage,
                failed_tool=hop_trace.failed_tool or turn_trace.failed_tool,
                consecutive_fallback_count_after=hop_trace.consecutive_fallback_count_after,
                warnings=merged_warnings,
                agent_hops=list(agent_hops),
                agent_name=hop_trace.agent_name or turn_trace.agent_name,
                user_input=turn_trace.user_input,
                _extra_callback_duration_ms=(
                    turn_trace._extra_callback_duration_ms
                    + extra_cb_dur
                    + prev_aa_dur
                ),
            )

        self._last_committed_state = copy.deepcopy(self.ctx.state)
        turn_trace.routed_agents = list(routed_agents)
        if not turn_trace.agent_name and routed_agents:
            turn_trace.agent_name = routed_agents[0]
        turn_trace.agent_hops = list(agent_hops)
        turn_trace.__post_init__()
        object.__setattr__(turn_trace, "_runner", self.runner)
        turn_trace.duration_ms = (time.perf_counter() - sim_turn_t0) * 1000.0
        self.turns.append(turn_trace)
        _record_collected_turn(turn_trace)
        return turn_trace

    send = turn

    def run_turn(
        self,
        user_input: str | Content | None = None,
        **kwargs: Any,
    ) -> tuple[LlmResponse | None, TurnTrace]:
        """Alias for `turn(...)` returning `(final_response, turn_trace)`."""
        tt = self.turn(user_input, **kwargs)
        return _TurnRunResult(tt.final_response, tt)

    def get_metrics_summary(
        self,
        label: str | None = None,
    ) -> SessionMetricsSummary:
        """Computes aggregated latency and token usage metrics across all
        turns.
        """
        eff_label = label or (
            getattr(self.llm, "model", None)
            or self._runner_label(self.runner)
            or "session"
        )
        return SessionMetricsSummary.from_turns(
            self.turns, label=str(eff_label)
        )

    metrics_summary = get_metrics_summary
