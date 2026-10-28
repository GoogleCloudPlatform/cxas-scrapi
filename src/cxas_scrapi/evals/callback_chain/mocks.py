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

"""Declarative MockLlm and ToolSpy builders for Jest-style CES agent tests."""

from __future__ import annotations

import base64
import copy
import dataclasses
import inspect
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from cxas_scrapi.evals.callback_chain.turn import invoke_tool_impl
from cxas_scrapi.utils.callback_libs import (
    CallbackContext,
    Content,
    FunctionCall,
    FunctionResponse,
    LlmRequest,
    LlmResponse,
    Part,
    ToolContext,
    UsageMetadata,
)

_UNSET: Any = object()


def _get_resp_parts(resp: LlmResponse) -> list[Part]:
    """Safely returns the list of `Part` objects from an `LlmResponse`."""
    if resp.content is not None and resp.content.parts:
        return list(resp.content.parts)
    return []


def serialize_llm_response(resp: LlmResponse) -> dict[str, Any]:
    """Serializes an `LlmResponse` (including `thought`, `thought_signature`,
    and `FunctionCall` parts) into a JSON-safe dict.
    """
    parts_out: list[dict[str, Any]] = []
    for p in _get_resp_parts(resp):
        p_dict: dict[str, Any] = {}
        if p.text is not None:
            p_dict["text"] = p.text
        if p.thought is not None:
            p_dict["thought"] = bool(p.thought)
        if p.thought_signature is not None:
            if isinstance(p.thought_signature, (bytes, bytearray)):
                p_dict["thought_signature_b64"] = base64.b64encode(
                    bytes(p.thought_signature)
                ).decode("ascii")
            else:
                p_dict["thought_signature"] = str(p.thought_signature)
        if p.function_call is not None:
            p_dict["function_call"] = {
                "name": p.function_call.name,
                "args": copy.deepcopy(dict(p.function_call.args or {})),
                "id": p.function_call.id,
            }
        if p.function_response is not None:
            p_dict["function_response"] = {
                "name": p.function_response.name,
                "response": copy.deepcopy(
                    dict(p.function_response.response or {})
                ),
                "id": p.function_response.id,
            }
        parts_out.append(p_dict)

    um = resp.usage_metadata
    um_dict: dict[str, int] | None = None
    if um is not None:
        um_dict = {
            "prompt_token_count": int(um.prompt_token_count or 0),
            "candidates_token_count": int(um.candidates_token_count or 0),
            "total_token_count": int(um.total_token_count or 0),
            "cached_content_token_count": int(
                um.cached_content_token_count or 0
            ),
            "thoughts_token_count": int(um.thoughts_token_count or 0),
        }

    return {
        "role": resp.content.role if resp.content else "model",
        "parts": parts_out,
        "usage_metadata": um_dict,
        "model_version": resp.model_version,
        "finish_reason": resp.finish_reason,
        "block_reason": resp.block_reason,
    }


def deserialize_llm_response(
    data: Mapping[str, Any] | LlmResponse,
) -> LlmResponse:
    """Reconstructs an `LlmResponse` from a serialized snapshot dict."""
    if isinstance(data, LlmResponse):
        return copy.deepcopy(data)

    parts_out: list[Part] = []
    for p_dict in data.get("parts") or []:
        if not isinstance(p_dict, Mapping):
            continue
        sig_val: bytes | str | None = None
        if p_dict.get("thought_signature_b64") is not None:
            sig_val = base64.b64decode(
                str(p_dict["thought_signature_b64"]).encode("ascii")
            )
        elif p_dict.get("thought_signature") is not None:
            sig_val = p_dict["thought_signature"]

        fc_obj: FunctionCall | None = None
        if isinstance(p_dict.get("function_call"), Mapping):
            fc_raw = p_dict["function_call"]
            fc_obj = FunctionCall(
                name=str(fc_raw.get("name") or ""),
                args=copy.deepcopy(dict(fc_raw.get("args") or {})),
                id=fc_raw.get("id"),
            )

        fr_obj: FunctionResponse | None = None
        if isinstance(p_dict.get("function_response"), Mapping):
            fr_raw = p_dict["function_response"]
            fr_obj = FunctionResponse(
                name=str(fr_raw.get("name") or ""),
                response=copy.deepcopy(dict(fr_raw.get("response") or {})),
                id=fr_raw.get("id"),
            )

        parts_out.append(
            Part(
                text=p_dict.get("text"),
                thought=p_dict.get("thought"),
                thought_signature=sig_val,
                function_call=fc_obj,
                function_response=fr_obj,
            )
        )

    um_raw = data.get("usage_metadata")
    um_obj: UsageMetadata | None = None
    if isinstance(um_raw, Mapping):
        um_obj = UsageMetadata(
            prompt_token_count=int(um_raw.get("prompt_token_count") or 0),
            candidates_token_count=int(
                um_raw.get("candidates_token_count") or 0
            ),
            total_token_count=int(um_raw.get("total_token_count") or 0),
            cached_content_token_count=int(
                um_raw.get("cached_content_token_count") or 0
            ),
            thoughts_token_count=int(um_raw.get("thoughts_token_count") or 0),
        )

    role = str(data.get("role") or "model")
    return LlmResponse(
        content=Content(role=role, parts=parts_out),
        usage_metadata=um_obj,
        model_version=(
            str(data["model_version"])
            if data.get("model_version") is not None
            else None
        ),
        finish_reason=(
            str(data["finish_reason"])
            if data.get("finish_reason") is not None
            else None
        ),
        block_reason=(
            str(data["block_reason"])
            if data.get("block_reason") is not None
            else None
        ),
    )


def _build_text_response(
    text: str,
    *,
    thought: str | None = None,
) -> LlmResponse:
    """Constructs an `LlmResponse` containing optional thought and text
    parts.
    """
    parts: list[Part] = []
    if thought is not None:
        parts.append(Part(text=thought, thought=True))
    parts.append(Part.from_text(text))
    return LlmResponse.from_parts(parts)


def _build_tool_call_response(
    calls: Sequence[
        tuple[str, Mapping[str, Any]] | Mapping[str, Any] | FunctionCall
    ],
    *,
    thought: str | None = None,
) -> LlmResponse:
    """Constructs an `LlmResponse` with one or more `FunctionCall` parts."""
    parts: list[Part] = []
    if thought is not None:
        parts.append(Part(text=thought, thought=True))
    for idx, item in enumerate(calls, 1):
        if isinstance(item, FunctionCall):
            fc = copy.deepcopy(item)
            if not fc.id:
                fc.id = f"call_{idx}"
            parts.append(Part(function_call=fc))
        elif isinstance(item, tuple) and len(item) == 2:
            t_name, t_args = item
            parts.append(
                Part(
                    function_call=FunctionCall(
                        name=str(t_name),
                        args=copy.deepcopy(dict(t_args or {})),
                        id=f"call_{idx}",
                    )
                )
            )
        elif isinstance(item, Mapping):
            t_name = str(item.get("name") or item.get("tool") or "")
            t_args = dict(item.get("args") or {})
            parts.append(
                Part(
                    function_call=FunctionCall(
                        name=t_name,
                        args=copy.deepcopy(t_args),
                        id=str(item.get("id") or f"call_{idx}"),
                    )
                )
            )
        else:
            raise TypeError(
                f"Unsupported tool call specification: {item!r}. "
                "Expected (name, args_dict), {'name': ..., 'args': ...}, or "
                "FunctionCall."
            )
    return LlmResponse.from_parts(parts)


def _invoke_rule_target(
    rule: LlmResponse | Callable[..., LlmResponse],
    req: LlmRequest,
    ctx: CallbackContext,
) -> LlmResponse:
    """Resolves an `LlmResponse` or callable rule into a fresh `LlmResponse`."""
    if isinstance(rule, LlmResponse):
        return copy.deepcopy(rule)
    if callable(rule):
        sig = inspect.signature(rule)
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
            first_name = pos_params[0].name.lower()
            first_ann = str(pos_params[0].annotation)
            if (
                first_name in ("ctx", "callback_context", "context")
                or "CallbackContext" in first_ann
            ):
                out = rule(ctx, req)
            else:
                out = rule(req, ctx)
        elif len(pos_params) == 1:
            p_name = pos_params[0].name.lower()
            if p_name in ("ctx", "callback_context", "context"):
                out = rule(ctx)
            else:
                out = rule(req)
        elif has_varargs:
            out = rule(req, ctx)
        else:
            out = rule()
        if isinstance(out, str):
            return _build_text_response(out)
        if not isinstance(out, LlmResponse):
            raise TypeError(
                f"MockLlm response callable must return LlmResponse (or str), "
                f"got {type(out).__name__}"
            )
        return copy.deepcopy(out)
    raise TypeError(f"Unsupported MockLlm rule target: {type(rule).__name__}")


def _extract_full_prompt_text(req: LlmRequest) -> str:
    """Extracts concatenated system_instruction and contents text from `req`."""
    chunks: list[str] = []
    if req.config is not None and req.config.system_instruction is not None:
        sys_inst = req.config.system_instruction
        if isinstance(sys_inst, str):
            chunks.append(sys_inst)
        elif isinstance(sys_inst, Content):
            for p in sys_inst.parts or []:
                if p.text:
                    chunks.append(p.text)
    for content in req.contents or []:
        for p in content.parts or []:
            if p.text:
                chunks.append(p.text)
    return "\n".join(chunks)


def _request_follows_tool(
    req: LlmRequest,
    tool_name: str,
    *,
    contains: str | None = None,
) -> bool:
    """Returns True if the latest user turn in `req.contents` has a
    FunctionResponse for `tool_name`.
    """
    if not req.contents:
        return False
    for content in reversed(req.contents):
        if content.role == "user" and content.parts:
            fr_parts = [
                p.function_response
                for p in content.parts
                if p.function_response is not None
            ]
            if not fr_parts:
                continue
            for fr in fr_parts:
                if fr.name == tool_name:
                    if contains is None:
                        return True
                    serialized = json.dumps(
                        fr.response or {}, ensure_ascii=False, default=repr
                    )
                    if contains in serialized:
                        return True
            return False
    return False


@dataclasses.dataclass
class _PredicateRuleEntry:
    """Internal entry for a conditional predicate rule on `MockLlm`."""

    predicate: Callable[[LlmRequest, CallbackContext], bool]
    target: LlmResponse | Callable[..., LlmResponse]
    description: str
    max_times: int | None = None
    match_count: int = 0


class _HopRuleBuilder:
    """Fluent action builder bound to a specific 1-based LLM hop index (and
    optional 1-based turn).
    """

    def __init__(
        self,
        parent: MockLlm,
        hop: int,
        *,
        turn: int | None = None,
    ) -> None:
        if not isinstance(hop, int) or isinstance(hop, bool) or hop < 1:
            raise ValueError(
                f"MockLlm.on_hop(n) requires a 1-based integer hop >= 1, "
                f"got {hop!r}."
            )
        if turn is not None and (
            not isinstance(turn, int) or isinstance(turn, bool) or turn < 1
        ):
            raise ValueError(
                f"MockLlm.on_turn(t) requires a 1-based integer turn >= 1, "
                f"got {turn!r}."
            )
        self._parent = parent
        self._hop = hop
        self._turn = turn

    def _get_rule(self) -> LlmResponse | Callable[..., LlmResponse] | None:
        if self._turn is not None:
            return self._parent._turn_hop_rules.get((self._turn, self._hop))
        return self._parent._hop_rules.get(self._hop)

    def _set_rule(self, rule: LlmResponse | Callable[..., LlmResponse]) -> None:
        if self._turn is not None:
            self._parent._turn_hop_rules[(self._turn, self._hop)] = rule
        else:
            self._parent._hop_rules[self._hop] = rule

    def call_tool(
        self,
        tool_name: str,
        _args_dict: Mapping[str, Any] | None = None,
        /,
        **kwargs: Any,
    ) -> MockLlm:
        """Scripts this hop to emit a `FunctionCall` for `tool_name`."""
        merged_args: dict[str, Any] = {}
        if _args_dict is not None:
            merged_args.update(copy.deepcopy(dict(_args_dict)))
        if kwargs:
            merged_args.update(copy.deepcopy(kwargs))

        existing = self._get_rule()
        if isinstance(existing, LlmResponse) and existing.content is not None:
            existing_fcs = [
                p
                for p in (existing.content.parts or [])
                if p.function_call is not None
            ]
            if existing_fcs:
                next_idx = len(existing_fcs) + 1
                new_part = Part(
                    function_call=FunctionCall(
                        name=tool_name,
                        args=merged_args,
                        id=f"call_{next_idx}",
                    )
                )
                combined_parts = [*list(existing.content.parts or []), new_part]
                self._set_rule(LlmResponse.from_parts(combined_parts))
                return self._parent

        self._set_rule(_build_tool_call_response([(tool_name, merged_args)]))
        return self._parent

    def call_tools(
        self,
        calls: Sequence[
            tuple[str, Mapping[str, Any]] | Mapping[str, Any] | FunctionCall
        ],
        *,
        thought: str | None = None,
    ) -> MockLlm:
        """Scripts this hop to emit multiple parallel `FunctionCall` parts."""
        self._set_rule(_build_tool_call_response(calls, thought=thought))
        return self._parent

    def transfer_to_agent(
        self,
        agent_name: str,
        **kwargs: Any,
    ) -> MockLlm:
        """Scripts this hop to call `transfer_to_agent(agent_name=...)`."""
        return self.call_tool(
            "transfer_to_agent", {"agent_name": agent_name, **kwargs}
        )

    def end_session(self, **kwargs: Any) -> MockLlm:
        """Scripts this hop to call `end_session(...)`."""
        return self.call_tool("end_session", kwargs)

    def reply_text(
        self,
        text: str,
        *,
        thought: str | None = None,
    ) -> MockLlm:
        """Scripts this hop to reply with `text` (and optional `thought`)."""
        self._set_rule(_build_text_response(text, thought=thought))
        return self._parent

    def respond(
        self,
        response_or_callable: LlmResponse | Callable[..., LlmResponse],
    ) -> MockLlm:
        """Scripts this hop with a custom `LlmResponse` or callable."""
        self._set_rule(response_or_callable)
        return self._parent


class _TurnRuleBuilder:
    """Fluent builder bound to a specific 1-based conversational turn."""

    def __init__(self, parent: MockLlm, turn: int) -> None:
        if not isinstance(turn, int) or isinstance(turn, bool) or turn < 1:
            raise ValueError(
                f"MockLlm.on_turn(t) requires a 1-based integer turn >= 1, "
                f"got {turn!r}."
            )
        self._parent = parent
        self._turn = turn

    def on_hop(self, hop: int) -> _HopRuleBuilder:
        """Configures a response for 1-based `hop` within this turn."""
        return _HopRuleBuilder(self._parent, hop, turn=self._turn)

    def call_tool(
        self,
        tool_name: str,
        _args_dict: Mapping[str, Any] | None = None,
        /,
        **kwargs: Any,
    ) -> MockLlm:
        """Scripts hop 1 of this turn to call `tool_name`."""
        return self.on_hop(1).call_tool(tool_name, _args_dict, **kwargs)

    def reply_text(
        self,
        text: str,
        *,
        thought: str | None = None,
    ) -> MockLlm:
        """Scripts hop 1 of this turn to reply with `text`."""
        return self.on_hop(1).reply_text(text, thought=thought)


class _PredicateRuleBuilder:
    """Fluent action builder bound to a conditional request/context
    predicate.
    """

    def __init__(
        self,
        parent: MockLlm,
        predicate: Callable[[LlmRequest, CallbackContext], bool],
        description: str,
        *,
        max_times: int | None = None,
    ) -> None:
        self._parent = parent
        self._predicate = predicate
        self._description = description
        self._max_times = max_times

    def once(self) -> _PredicateRuleBuilder:
        """Restricts this predicate rule to fire at most once (one-shot)."""
        self._max_times = 1
        return self

    def times(self, n: int) -> _PredicateRuleBuilder:
        """Restricts this predicate rule to fire at most `n` times."""
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            raise ValueError(
                f"Predicate `.times(n)` requires an integer >= 1, got {n!r}."
            )
        self._max_times = n
        return self

    def _register(
        self, target: LlmResponse | Callable[..., LlmResponse]
    ) -> MockLlm:
        self._parent._predicate_rules.append(
            _PredicateRuleEntry(
                predicate=self._predicate,
                target=target,
                description=self._description,
                max_times=self._max_times,
            )
        )
        return self._parent

    def call_tool(
        self,
        tool_name: str,
        _args_dict: Mapping[str, Any] | None = None,
        /,
        **kwargs: Any,
    ) -> MockLlm:
        """Scripts matching requests to emit a `FunctionCall` for
        `tool_name`.
        """
        merged_args: dict[str, Any] = {}
        if _args_dict is not None:
            merged_args.update(copy.deepcopy(dict(_args_dict)))
        if kwargs:
            merged_args.update(copy.deepcopy(kwargs))
        resp = _build_tool_call_response([(tool_name, merged_args)])
        return self._register(resp)

    def call_tools(
        self,
        calls: Sequence[
            tuple[str, Mapping[str, Any]] | Mapping[str, Any] | FunctionCall
        ],
        *,
        thought: str | None = None,
    ) -> MockLlm:
        """Scripts matching requests to emit multiple parallel `FunctionCall`
        parts.
        """
        resp = _build_tool_call_response(calls, thought=thought)
        return self._register(resp)

    def transfer_to_agent(
        self,
        agent_name: str,
        **kwargs: Any,
    ) -> MockLlm:
        """Scripts matching requests to call
        `transfer_to_agent(agent_name=...)`.
        """
        return self.call_tool(
            "transfer_to_agent", {"agent_name": agent_name, **kwargs}
        )

    def end_session(self, **kwargs: Any) -> MockLlm:
        """Scripts matching requests to call `end_session(...)`."""
        return self.call_tool("end_session", kwargs)

    def reply_text(
        self,
        text: str,
        *,
        thought: str | None = None,
    ) -> MockLlm:
        """Scripts matching requests to reply with `text`."""
        resp = _build_text_response(text, thought=thought)
        return self._register(resp)

    def respond(
        self,
        response_or_callable: LlmResponse | Callable[..., LlmResponse],
    ) -> MockLlm:
        """Scripts matching requests with a custom `LlmResponse` or callable."""
        return self._register(response_or_callable)


class MockLlm:
    """Declarative fluent mock LLM builder for single-turn and multi-turn CES
    agent tests.

    Hop indices configured via `.on_hop(n)` are 1-based and reset automatically
    at the
    start of each conversational turn in `run_turn` / `SessionSimulator`. Use
    `.on_turn(t).on_hop(n)` for turn-specific overrides across multi-turn
    sessions.

    Example:
        ```python
        llm = (
            MockLlm()
            .on_hop(1).call_tool("get_balances", balance_flag=0)
            .on_hop(2).reply_text("Your account balance is 1250 EUR.")
        )
        ```
    """

    _is_cxas_mock_llm: bool = True

    def __init__(self, default_text: str | None = None) -> None:
        self._hop_rules: dict[
            int, LlmResponse | Callable[..., LlmResponse]
        ] = {}
        self._turn_hop_rules: dict[
            tuple[int, int], LlmResponse | Callable[..., LlmResponse]
        ] = {}
        self._predicate_rules: list[_PredicateRuleEntry] = []
        self._sequential_rules: list[
            LlmResponse | Callable[..., LlmResponse]
        ] = []
        self._seq_cursor: int = 0
        self._turn_counter: int = 0
        self._turn_hop_counter: int = 0
        self._default_rule: LlmResponse | Callable[..., LlmResponse] | None = (
            _build_text_response(default_text)
            if default_text is not None
            else None
        )
        self.requests: list[LlmRequest] = []
        self.contexts: list[CallbackContext] = []
        self.responses: list[LlmResponse] = []

    def _notify_turn_start(self) -> None:
        """Internal hook invoked at the start of a new conversational turn."""
        self._turn_counter += 1
        self._turn_hop_counter = 0

    @property
    def call_count(self) -> int:
        """Total number of LLM calls handled so far."""
        return len(self.requests)

    def reset(self) -> MockLlm:
        """Clears recorded requests, contexts, responses, and resets cursors and
        counters.
        """
        self.requests.clear()
        self.contexts.clear()
        self.responses.clear()
        self._seq_cursor = 0
        self._turn_counter = 0
        self._turn_hop_counter = 0
        for entry in self._predicate_rules:
            entry.match_count = 0
        return self

    @classmethod
    def from_snapshot(
        cls,
        source: str | Path | Mapping[str, Any] | Sequence[Any] | Any,
        *,
        default_text: str | None = None,
    ) -> MockLlm:
        """Creates a `MockLlm` pre-populated with recorded `LlmResponse` hops
        (including thoughts and tool calls) for zero-latency offline replay.
        """
        from cxas_scrapi.evals.callback_chain.traces import (  # noqa: PLC0415
            TurnTrace,
        )

        mock = cls(default_text=default_text)

        if hasattr(source, "responses") and isinstance(
            source.responses, Sequence
        ):
            for resp in source.responses:
                mock.then_respond(deserialize_llm_response(resp))
            return mock

        if isinstance(source, (str, Path)):
            raw_data = json.loads(Path(source).read_text(encoding="utf-8"))
            return cls.from_snapshot(raw_data, default_text=default_text)

        if isinstance(source, Mapping):
            fmt = source.get("format")
            if fmt is not None and fmt != "cxas_llm_snapshot_v1":
                raise ValueError(f"Unsupported LLM snapshot format: {fmt!r}")
            if "responses" in source:
                raw_resps = source["responses"]
                if not isinstance(raw_resps, list):
                    raise ValueError(
                        "Snapshot 'responses' field must be a list."
                    )
                for item in raw_resps:
                    if not isinstance(item, (Mapping, LlmResponse)):
                        raise ValueError(
                            f"Invalid snapshot response entry: {item!r}"
                        )
                    mock.then_respond(deserialize_llm_response(item))
                return mock
            if "turns_responses" in source:
                raw_turns = source["turns_responses"]
                if not isinstance(raw_turns, list):
                    raise ValueError(
                        "Snapshot 'turns_responses' field must be a list."
                    )
                for t_list in raw_turns:
                    if not isinstance(t_list, list):
                        raise ValueError(
                            "Each entry in 'turns_responses' must be a list."
                        )
                    for item in t_list:
                        if not isinstance(item, (Mapping, LlmResponse)):
                            raise ValueError(
                                f"Invalid snapshot response entry: {item!r}"
                            )
                        mock.then_respond(deserialize_llm_response(item))
                return mock
            valid_resp_keys = {
                "parts",
                "content",
                "role",
                "finish_reason",
                "block_reason",
                "usage_metadata",
                "model_version",
            }
            if any(k in source for k in valid_resp_keys):
                mock.then_respond(deserialize_llm_response(source))
                return mock
            raise ValueError(
                "Snapshot mapping must contain 'responses', 'turns_responses', "
                "or valid LlmResponse keys."
            )

        if isinstance(source, TurnTrace):
            for step in source.llm_steps:
                step_resp = step.raw_response or step.final_response
                if step_resp is not None:
                    mock.then_respond(copy.deepcopy(step_resp))
            return mock

        if isinstance(source, Sequence) and not isinstance(
            source, (str, bytes, bytearray)
        ):
            items = list(source)
            if items and all(isinstance(x, TurnTrace) for x in items):
                for turn in items:
                    for step in turn.llm_steps:
                        step_resp = step.raw_response or step.final_response
                        if step_resp is not None:
                            mock.then_respond(copy.deepcopy(step_resp))
            else:
                for item in items:
                    if isinstance(item, LlmResponse):
                        mock.then_respond(copy.deepcopy(item))
                    elif isinstance(item, Mapping):
                        mock.then_respond(deserialize_llm_response(item))
                    else:
                        raise TypeError(
                            f"Unsupported snapshot sequence element type: "
                            f"{type(item).__name__}"
                        )
            return mock

        raise TypeError(
            f"Unsupported snapshot source type: {type(source).__name__}"
        )

    def save_snapshot(
        self,
        path: str | Path,
        *,
        model: str | None = None,
    ) -> Path:
        """Exports the responses recorded in `self.responses` (or scripted
        sequential responses) to a JSON snapshot file.
        """
        resps = (
            list(self.responses)
            if self.responses
            else [
                r for r in self._sequential_rules if isinstance(r, LlmResponse)
            ]
        )
        serialized_resps = [serialize_llm_response(r) for r in resps]
        payload: dict[str, Any] = {
            "format": "cxas_llm_snapshot_v1",
            "responses": serialized_resps,
        }
        if model is not None:
            payload["model"] = model
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return target

    def on_hop(self, hop: int) -> _HopRuleBuilder:
        """Configures a response for a specific 1-based LLM hop (`hop >= 1`)."""
        return _HopRuleBuilder(self, hop)

    def on_turn(self, turn: int) -> _TurnRuleBuilder:
        """Configures a response scoped to a specific 1-based conversational
        turn (`turn >= 1`).
        """
        return _TurnRuleBuilder(self, turn)

    # Sequential rule builders
    def then_call_tool(
        self,
        tool_name: str,
        _args_dict: Mapping[str, Any] | None = None,
        /,
        **kwargs: Any,
    ) -> MockLlm:
        """Appends a sequential tool call response for the next unscripted
        hop.
        """
        merged_args: dict[str, Any] = {}
        if _args_dict is not None:
            merged_args.update(copy.deepcopy(dict(_args_dict)))
        if kwargs:
            merged_args.update(copy.deepcopy(kwargs))
        self._sequential_rules.append(
            _build_tool_call_response([(tool_name, merged_args)])
        )
        return self

    def then_call_tools(
        self,
        calls: Sequence[
            tuple[str, Mapping[str, Any]] | Mapping[str, Any] | FunctionCall
        ],
        *,
        thought: str | None = None,
    ) -> MockLlm:
        """Appends a sequential multi-tool call response for the next unscripted
        hop.
        """
        self._sequential_rules.append(
            _build_tool_call_response(calls, thought=thought)
        )
        return self

    def then_transfer_to_agent(
        self,
        agent_name: str,
        **kwargs: Any,
    ) -> MockLlm:
        """Appends a sequential `transfer_to_agent` call for the next unscripted
        hop.
        """
        return self.then_call_tool(
            "transfer_to_agent", {"agent_name": agent_name, **kwargs}
        )

    def then_end_session(self, **kwargs: Any) -> MockLlm:
        """Appends a sequential `end_session` call for the next unscripted
        hop.
        """
        return self.then_call_tool("end_session", kwargs)

    def then_reply_text(
        self,
        text: str,
        *,
        thought: str | None = None,
    ) -> MockLlm:
        """Appends a sequential text reply for the next unscripted hop."""
        self._sequential_rules.append(
            _build_text_response(text, thought=thought)
        )
        return self

    def then_respond(
        self,
        response_or_callable: LlmResponse | Callable[..., LlmResponse],
    ) -> MockLlm:
        """Appends a sequential `LlmResponse` or callable for the next
        unscripted hop.
        """
        self._sequential_rules.append(response_or_callable)
        return self

    # Direct convenience aliases for sequential scripting
    call_tool = then_call_tool
    call_tools = then_call_tools
    transfer_to_agent = then_transfer_to_agent
    end_session = then_end_session
    reply_text = then_reply_text
    respond = then_respond

    # Conditional / predicate rule builders
    def when_agent(
        self,
        agent_name: str,
        *,
        times: int | None = None,
        once: bool = False,
    ) -> _PredicateRuleBuilder:
        """Matches when `ctx.agent_name == agent_name`."""
        target = agent_name.strip()

        def _pred(_req: LlmRequest, ctx: CallbackContext) -> bool:
            actual = (ctx.agent_name or "").strip()
            return actual == target

        eff_times = 1 if once else times
        return _PredicateRuleBuilder(
            self,
            _pred,
            f"when_agent({agent_name!r})",
            max_times=eff_times,
        )

    def when_prompt_contains(
        self,
        substring: str,
        *,
        times: int | None = None,
        once: bool = False,
    ) -> _PredicateRuleBuilder:
        """Matches when `substring` appears in `system_instruction` or
        `contents`.
        """

        def _pred(req: LlmRequest, _ctx: CallbackContext) -> bool:
            return substring in _extract_full_prompt_text(req)

        eff_times = 1 if once else times
        return _PredicateRuleBuilder(
            self,
            _pred,
            f"when_prompt_contains({substring!r})",
            max_times=eff_times,
        )

    def when_after_tool(
        self,
        tool_name: str,
        *,
        contains: str | None = None,
        times: int | None = None,
        once: bool = False,
    ) -> _PredicateRuleBuilder:
        """Matches when the latest user turn in `contents` is a
        `FunctionResponse` from `tool_name`.
        """

        def _pred(req: LlmRequest, _ctx: CallbackContext) -> bool:
            return _request_follows_tool(req, tool_name, contains=contains)

        eff_times = 1 if once else times
        return _PredicateRuleBuilder(
            self,
            _pred,
            f"when_after_tool({tool_name!r})",
            max_times=eff_times,
        )

    when_tool_output = when_after_tool

    def when(
        self,
        predicate: Callable[..., bool],
        *,
        description: str = "custom_predicate",
        times: int | None = None,
        once: bool = False,
    ) -> _PredicateRuleBuilder:
        """Matches when `predicate(llm_request, ctx)` (or
        `predicate(llm_request)`) returns True.
        """
        sig = inspect.signature(predicate)
        pos_params = [
            p
            for p in sig.parameters.values()
            if p.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]

        def _wrapped(req: LlmRequest, ctx: CallbackContext) -> bool:
            if len(pos_params) >= 2:
                return bool(predicate(req, ctx))
            if len(pos_params) == 1:
                p_name = pos_params[0].name.lower()
                if p_name in ("ctx", "callback_context", "context"):
                    return bool(predicate(ctx))
                return bool(predicate(req))
            return bool(predicate())

        eff_times = 1 if once else times
        return _PredicateRuleBuilder(
            self, _wrapped, description, max_times=eff_times
        )

    # Default fallback builders
    def default_reply(
        self,
        text: str,
        *,
        thought: str | None = None,
    ) -> MockLlm:
        """Configures a fallback text response when no hop, predicate, or
        sequential rule matches.
        """
        self._default_rule = _build_text_response(text, thought=thought)
        return self

    def default_response(
        self,
        response_or_callable: LlmResponse | Callable[..., LlmResponse],
    ) -> MockLlm:
        """Configures a fallback `LlmResponse` or callable when no other rule
        matches.
        """
        self._default_rule = response_or_callable
        return self

    def __call__(
        self,
        first_arg: LlmRequest | CallbackContext | None = None,
        second_arg: CallbackContext | LlmRequest | None = None,
    ) -> LlmResponse:
        """Executes the mock LLM for the current hop and records request,
        context, and response.
        """
        if isinstance(first_arg, CallbackContext) and isinstance(
            second_arg, LlmRequest
        ):
            ctx = first_arg
            req = second_arg
        elif isinstance(first_arg, LlmRequest):
            req = first_arg
            ctx = (
                second_arg
                if isinstance(second_arg, CallbackContext)
                else CallbackContext()
            )
        elif isinstance(first_arg, CallbackContext):
            ctx = first_arg
            req = LlmRequest()
        else:
            req = LlmRequest()
            ctx = CallbackContext()

        # `on_hop(n)` is strictly 1-based per-turn (`turn_hop`); `global_hop` is
        # tracked solely for diagnostic reporting when no rule matches.
        global_hop = len(self.requests) + 1
        self._turn_hop_counter += 1
        turn_hop = self._turn_hop_counter
        eff_turn = (
            self._turn_counter
            if self._turn_counter >= 1
            else max(int(getattr(ctx, "turn_index", 0) or 0), 1)
        )
        self.requests.append(copy.deepcopy(req))
        self.contexts.append(copy.deepcopy(ctx))

        chosen_rule: LlmResponse | Callable[..., LlmResponse] | None = None
        if (eff_turn, turn_hop) in self._turn_hop_rules:
            chosen_rule = self._turn_hop_rules[(eff_turn, turn_hop)]
        elif turn_hop in self._hop_rules:
            chosen_rule = self._hop_rules[turn_hop]
        else:
            for entry in self._predicate_rules:
                if (
                    entry.max_times is not None
                    and entry.match_count >= entry.max_times
                ):
                    continue
                if entry.predicate(req, ctx):
                    entry.match_count += 1
                    chosen_rule = entry.target
                    break
            if chosen_rule is None and self._seq_cursor < len(
                self._sequential_rules
            ):
                chosen_rule = self._sequential_rules[self._seq_cursor]
                self._seq_cursor += 1
            if chosen_rule is None and self._default_rule is not None:
                chosen_rule = self._default_rule

        if chosen_rule is None:
            configured_hops = sorted(self._hop_rules.keys())
            pred_descs = [e.description for e in self._predicate_rules]
            raise AssertionError(
                f"MockLlm has no response configured for hop {turn_hop} "
                f"(turn={eff_turn}, global_hop={global_hop}, "
                f"agent={ctx.agent_name!r}). "
                f"Configured on_hop indices: {configured_hops}; "
                f"predicate rules: {pred_descs}; "
                f"sequential rules consumed: "
                f"{self._seq_cursor}/{len(self._sequential_rules)}. "
                f"Add `.on_hop({turn_hop}).reply_text(...)` or "
                f"`.default_reply(...)`."
            )

        resp = _invoke_rule_target(chosen_rule, req, ctx)
        self.responses.append(copy.deepcopy(resp))
        return resp


class ToolSpy:
    """Spy and mock wrapper for CES tool implementations (`tool_impls` and
    `mock_tools`).

    Supports static return values (`returns=...`, including explicit
    `returns=None`),
    wrapping real callables (`wraps=fn`), and raising or sequencing side effects
    (`side_effect=...`), while recording all invocations in `.calls`,
    `.return_values`,
    and `.exceptions` (aligned 1:1 with calls; `None` when a call succeeds).
    """

    def __init__(
        self,
        returns: Any = _UNSET,
        *,
        wraps: Callable[..., Any] | None = None,
        side_effect: Any = None,
        name: str | None = None,
    ) -> None:
        self._has_explicit_returns = returns is not _UNSET
        self._returns = None if returns is _UNSET else copy.deepcopy(returns)
        self._wraps = wraps
        self._side_effect = side_effect
        self._side_effect_items: list[Any] | None = None
        self._side_effect_index: int = 0
        if (
            side_effect is not None
            and not isinstance(
                side_effect, (BaseException, type, str, bytes, Mapping)
            )
            and not callable(side_effect)
            and isinstance(side_effect, Iterable)
        ):
            self._side_effect_items = list(side_effect)
        self.name = name or (
            getattr(wraps, "__name__", None)
            if wraps is not None
            else "tool_spy"
        )
        self.calls: list[dict[str, Any]] = []
        self._raw_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.contexts: list[ToolContext | None] = []
        self.return_values: list[Any] = []
        self.exceptions: list[BaseException | None] = []

    @property
    def call_count(self) -> int:
        """Number of times this tool spy was invoked."""
        return len(self.calls)

    @property
    def called(self) -> bool:
        """True if this tool spy was invoked at least once."""
        return len(self.calls) > 0

    @property
    def last_args(self) -> dict[str, Any] | None:
        """Arguments dict from the most recent invocation, or `None` if never
        called.
        """
        return copy.deepcopy(self.calls[-1]) if self.calls else None

    @property
    def last_return_value(self) -> Any:
        """Return value from the most recent successful invocation, or
        `None`.
        """
        return (
            copy.deepcopy(self.return_values[-1])
            if self.return_values
            else None
        )

    def reset(self) -> ToolSpy:
        """Clears recorded calls, contexts, return values, and exceptions, and
        rewinds `side_effect`.
        """
        self.calls.clear()
        self._raw_calls.clear()
        self.contexts.clear()
        self.return_values.clear()
        self.exceptions.clear()
        self._side_effect_index = 0
        return self

    @staticmethod
    def _invoke_callable(
        fn: Callable[..., Any],
        *,
        is_ces_convention: bool,
        call_args: dict[str, Any],
        tool_ctx: ToolContext | None,
        raw_args: tuple[Any, ...],
        raw_kwargs: dict[str, Any],
    ) -> Any:
        if is_ces_convention and tool_ctx is not None:
            return invoke_tool_impl(fn, call_args, tool_ctx)
        return fn(*copy.deepcopy(raw_args), **copy.deepcopy(raw_kwargs))

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        tool_ctx: ToolContext | None = None
        is_ces_convention = False
        if (
            len(args) == 2
            and isinstance(args[0], Mapping)
            and isinstance(args[1], CallbackContext)
            and not kwargs
        ):
            is_ces_convention = True
            call_args = copy.deepcopy(dict(args[0]))
            tool_ctx = (
                args[1]
                if isinstance(args[1], ToolContext)
                else ToolContext(state=args[1].state)
            )
            raw_for_record = ((), copy.deepcopy(call_args))
        elif len(args) == 1 and isinstance(args[0], Mapping) and not kwargs:
            call_args = copy.deepcopy(dict(args[0]))
            raw_for_record = ((), copy.deepcopy(call_args))
        elif not args and kwargs:
            call_args = copy.deepcopy(kwargs)
            raw_for_record = ((), copy.deepcopy(kwargs))
        elif args and not kwargs:
            call_args = {"args": copy.deepcopy(list(args))}
            raw_for_record = (copy.deepcopy(args), {})
        elif args and kwargs:
            call_args = {
                "args": copy.deepcopy(list(args)),
                **copy.deepcopy(kwargs),
            }
            raw_for_record = (copy.deepcopy(args), copy.deepcopy(kwargs))
        else:
            call_args = {}
            raw_for_record = ((), {})

        self.calls.append(copy.deepcopy(call_args))
        self._raw_calls.append(raw_for_record)
        self.contexts.append(tool_ctx)

        try:
            if self._side_effect_items is not None:
                if self._side_effect_index >= len(self._side_effect_items):
                    raise AssertionError(
                        f"ToolSpy '{self.name}' side_effect sequence "
                        f"exhausted on call #{len(self.calls)}."
                    )
                effect_item = self._side_effect_items[self._side_effect_index]
                self._side_effect_index += 1
                if isinstance(effect_item, BaseException):
                    raise effect_item
                if isinstance(effect_item, type) and issubclass(
                    effect_item, BaseException
                ):
                    raise effect_item()
                if callable(effect_item):
                    result = self._invoke_callable(
                        effect_item,
                        is_ces_convention=is_ces_convention,
                        call_args=call_args,
                        tool_ctx=tool_ctx,
                        raw_args=args,
                        raw_kwargs=kwargs,
                    )
                else:
                    result = copy.deepcopy(effect_item)
            elif self._side_effect is not None:
                if isinstance(self._side_effect, BaseException):
                    raise self._side_effect
                if isinstance(self._side_effect, type) and issubclass(
                    self._side_effect, BaseException
                ):
                    raise self._side_effect()
                if callable(self._side_effect):
                    result = self._invoke_callable(
                        self._side_effect,
                        is_ces_convention=is_ces_convention,
                        call_args=call_args,
                        tool_ctx=tool_ctx,
                        raw_args=args,
                        raw_kwargs=kwargs,
                    )
                else:
                    result = copy.deepcopy(self._side_effect)
            elif self._wraps is not None:
                result = self._invoke_callable(
                    self._wraps,
                    is_ces_convention=is_ces_convention,
                    call_args=call_args,
                    tool_ctx=tool_ctx,
                    raw_args=args,
                    raw_kwargs=kwargs,
                )
            elif self._has_explicit_returns:
                result = copy.deepcopy(self._returns)
            else:
                result = {"status": "ok"}
        except BaseException as exc:
            self.exceptions.append(exc)
            raise

        self.exceptions.append(None)
        self.return_values.append(copy.deepcopy(result))
        return result

    def assert_called(self) -> ToolSpy:
        """Asserts that the tool was called at least once."""
        from cxas_scrapi.evals.callback_chain.matchers import (  # noqa: PLC0415
            AgentAssertionError,
        )

        if not self.called:
            raise AgentAssertionError(
                f"Expected ToolSpy '{self.name}' to have been called at "
                f"least once, "
                "but it was never called.",
                expected="call_count >= 1",
                actual=0,
            )
        return self

    def assert_called_once(self) -> ToolSpy:
        """Asserts that the tool was called exactly once."""
        from cxas_scrapi.evals.callback_chain.matchers import (  # noqa: PLC0415
            AgentAssertionError,
        )

        if self.call_count != 1:
            raise AgentAssertionError(
                f"Expected ToolSpy '{self.name}' to have been called "
                f"exactly once, but it was called {self.call_count} time(s). "
                f"Calls: {self.calls!r}",
                expected=1,
                actual=self.call_count,
            )
        return self

    def assert_not_called(self) -> ToolSpy:
        """Asserts that the tool was never called."""
        from cxas_scrapi.evals.callback_chain.matchers import (  # noqa: PLC0415
            AgentAssertionError,
        )

        if self.called:
            raise AgentAssertionError(
                f"Expected ToolSpy '{self.name}' not to be called, "
                f"but it was called {self.call_count} time(s) with: "
                f"{self.calls!r}",
                expected=0,
                actual=self.call_count,
            )
        return self

    def assert_called_with(
        self,
        *expected_args: Any,
        **expected_kwargs: Any,
    ) -> ToolSpy:
        """Asserts that the most recent tool call exactly matched the expected
        arguments.
        """
        from cxas_scrapi.evals.callback_chain.matchers import (  # noqa: PLC0415
            AgentAssertionError,
        )

        self.assert_called()
        if (
            len(expected_args) == 1
            and isinstance(expected_args[0], Mapping)
            and not expected_kwargs
        ):
            expected: dict[str, Any] = dict(expected_args[0])
        elif not expected_args:
            expected = dict(expected_kwargs)
        else:
            expected = {
                "args": list(expected_args),
                **expected_kwargs,
            }

        actual = self.last_args or {}
        if actual != expected:
            mismatches: dict[str, dict[str, Any]] = {}
            all_keys = sorted(set(expected.keys()) | set(actual.keys()))
            for k in all_keys:
                if k not in actual:
                    mismatches[k] = {
                        "expected": expected[k],
                        "actual": "<MISSING>",
                    }
                elif k not in expected:
                    mismatches[k] = {
                        "expected": "<UNEXPECTED>",
                        "actual": actual[k],
                    }
                elif actual[k] != expected[k]:
                    mismatches[k] = {
                        "expected": expected[k],
                        "actual": actual[k],
                    }
            raise AgentAssertionError(
                f"ToolSpy '{self.name}' last call arguments did not exactly "
                f"match expected values.\n"
                f"  Expected: {expected!r}\n"
                f"  Actual:   {actual!r}\n"
                f"  Diffs:    {mismatches!r}",
                expected=expected,
                actual=actual,
            )
        return self

    def assert_called_with_partial(
        self,
        _expected_dict: Mapping[str, Any] | None = None,
        /,
        **expected_kwargs: Any,
    ) -> ToolSpy:
        """Asserts that the most recent tool call contained at least the
        expected key-value pairs.
        """
        from cxas_scrapi.evals.callback_chain.matchers import (  # noqa: PLC0415
            AgentAssertionError,
        )

        self.assert_called()
        expected: dict[str, Any] = {}
        if _expected_dict is not None:
            expected.update(dict(_expected_dict))
        if expected_kwargs:
            expected.update(expected_kwargs)

        actual = self.last_args or {}
        mismatches: dict[str, dict[str, Any]] = {}
        for k, exp_v in expected.items():
            if k not in actual:
                mismatches[k] = {"expected": exp_v, "actual": "<MISSING>"}
            elif actual[k] != exp_v:
                mismatches[k] = {"expected": exp_v, "actual": actual[k]}

        if mismatches:
            raise AgentAssertionError(
                f"ToolSpy '{self.name}' last call arguments did not match "
                f"expected subset.\n"
                f"  Expected subset: {expected!r}\n"
                f"  Actual:          {actual!r}\n"
                f"  Diffs:           {mismatches!r}",
                expected=expected,
                actual=actual,
            )
        return self

    def assert_called_once_with(
        self,
        *expected_args: Any,
        **expected_kwargs: Any,
    ) -> ToolSpy:
        """Asserts that the tool was called exactly once with the expected
        arguments.
        """
        self.assert_called_once()
        return self.assert_called_with(*expected_args, **expected_kwargs)
