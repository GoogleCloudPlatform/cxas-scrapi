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

"""Runtime semantics for CES callback stages (V1 & V2 serialization, state diff,
normalization).
"""

from __future__ import annotations

import copy
import datetime
import json
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pydantic

from cxas_scrapi.utils.callback_libs import (
    BaseModel,
    Content,
    GenerateContentConfig,
    LlmRequest,
    LlmResponse,
    ToolDeclaration,
    _ces_encoder,
    orjson,
)

if TYPE_CHECKING:
    from cxas_scrapi.evals.callback_chain.traces import ImageVersion, StageType

STAGE_TO_FUNC_NAME: dict[StageType, str] = {
    "before_agent": "before_agent_callback",
    "after_agent": "after_agent_callback",
    "before_model": "before_model_callback",
    "after_model": "after_model_callback",
    "before_tool": "before_tool_callback",
    "after_tool": "after_tool_callback",
}

STAGE_TO_AGENT_JSON_KEYS: dict[StageType, tuple[str, ...]] = {
    "before_agent": ("beforeAgentCallbacks", "before_agent_callbacks"),
    "after_agent": ("afterAgentCallbacks", "after_agent_callbacks"),
    "before_model": ("beforeModelCallbacks", "before_model_callbacks"),
    "after_model": ("afterModelCallbacks", "after_model_callbacks"),
    "before_tool": ("beforeToolCallbacks", "before_tool_callbacks"),
    "after_tool": ("afterToolCallbacks", "after_tool_callbacks"),
}

# App proto only defines global_before_model_callbacks (37) and
# global_after_model_callbacks (38).
STAGE_TO_GLOBAL_JSON_KEYS: dict[StageType, tuple[str, ...]] = {
    "before_model": (
        "globalBeforeModelCallbacks",
        "global_before_model_callbacks",
    ),
    "after_model": (
        "globalAfterModelCallbacks",
        "global_after_model_callbacks",
    ),
}

UNSUPPORTED_GLOBAL_JSON_KEYS: frozenset[str] = frozenset(
    {
        "globalBeforeAgentCallbacks",
        "global_before_agent_callbacks",
        "globalAfterAgentCallbacks",
        "global_after_agent_callbacks",
        "globalBeforeToolCallbacks",
        "global_before_tool_callbacks",
        "globalAfterToolCallbacks",
        "global_after_tool_callbacks",
    }
)

_STAGE_ALIASES: dict[str, StageType] = {
    "before_agent": "before_agent",
    "before_agent_callback": "before_agent",
    "before_agent_callbacks": "before_agent",
    "beforeagentcallbacks": "before_agent",
    "after_agent": "after_agent",
    "after_agent_callback": "after_agent",
    "after_agent_callbacks": "after_agent",
    "afteragentcallbacks": "after_agent",
    "before_model": "before_model",
    "before_model_callback": "before_model",
    "before_model_callbacks": "before_model",
    "beforemodelcallbacks": "before_model",
    "after_model": "after_model",
    "after_model_callback": "after_model",
    "after_model_callbacks": "after_model",
    "aftermodelcallbacks": "after_model",
    "before_tool": "before_tool",
    "before_tool_callback": "before_tool",
    "before_tool_callbacks": "before_tool",
    "beforetoolcallbacks": "before_tool",
    "after_tool": "after_tool",
    "after_tool_callback": "after_tool",
    "after_tool_callbacks": "after_tool",
    "aftertoolcallbacks": "after_tool",
}


class _V1CallbackResult(BaseModel):
    """Pydantic model validating callback result payloads in V1 sandbox mode."""

    state: dict[str, Any] | None = None
    content: Content | None = None
    llm_request: LlmRequest | None = None
    llm_response: LlmResponse | None = None
    tool_input: dict[str, Any] | None = None
    tool_response: dict[str, Any] | None = None


_v1_dict_adapter: pydantic.TypeAdapter[dict[Any, Any]] = pydantic.TypeAdapter(
    dict
)


def normalize_stage(stage: str) -> StageType:
    """Normalizes any stage alias into its canonical `StageType`."""
    key = stage.strip()
    if key in _STAGE_ALIASES:
        return _STAGE_ALIASES[key]
    lower_key = key.lower()
    if lower_key in _STAGE_ALIASES:
        return _STAGE_ALIASES[lower_key]
    raise ValueError(
        f"Unsupported callback stage '{stage}'. "
        f"Expected one of {sorted(STAGE_TO_FUNC_NAME.keys())}."
    )


def normalize_image_version(image_version: str | None) -> ImageVersion:
    """Validates and normalizes `image_version` (`'v1'` or `'v2'`)."""
    if image_version is None:
        return "v1"
    norm = image_version.strip().lower()
    if norm in ("v1", "v2"):
        return norm  # type: ignore[return-value]
    raise ValueError(
        f"Invalid image_version '{image_version}'. Expected 'v1' or 'v2'."
    )


def format_current_date(
    time_zone: str | None = None,
    now: datetime.datetime | None = None,
) -> str:
    """Formats `current_date` matching the CES runtime format.

    Uses the pattern `yyyy-MM-dd['['VV']']`:
    - When `timeZone` is empty or UTC (`"UTC"` / `"Z"`): `"2026-10-16[Z]"`
    - When `timeZone` is an IANA zone (e.g. `"Europe/Berlin"`):
      `"2026-10-16[Europe/Berlin]"`
    """
    tz_str = (time_zone or "").strip()
    if not tz_str or tz_str.upper() in ("UTC", "Z", "GMT"):
        tz: datetime.tzinfo = datetime.timezone.utc
        zone_id = "Z"
    else:
        try:
            tz = ZoneInfo(tz_str)
            zone_id = tz_str
        except (ZoneInfoNotFoundError, KeyError, ValueError):
            tz = datetime.timezone.utc
            zone_id = "Z"

    dt = now.astimezone(tz) if now is not None else datetime.datetime.now(tz)
    return f"{dt.strftime('%Y-%m-%d')}[{zone_id}]"


def dump_state_bytes(
    state: dict[str, Any], *, image_version: ImageVersion = "v1"
) -> bytes:
    """Serializes `state` according to V1 (`pydantic.TypeAdapter(dict)`) or V2
    (`orjson`).

    - In `v1`: validates top-level keys via `_V1CallbackResult(state=state)`
      (raising `pydantic.ValidationError` on top-level non-str keys) and dumps
      via
      `_v1_dict_adapter.dump_json(state)` (which coerces nested int keys to str,
      formats `datetime` as ISO, and converts `NaN`/`Inf` to `null`).
    - In `v2`: dumps via `orjson.dumps(state, default=_ces_encoder)` (which
      rejects non-str dict keys at any nesting depth with `TypeError`).
    """
    if not isinstance(state, dict):
        raise TypeError(
            f"callback_context.state must be a dict, got {type(state).__name__}"
        )
    if image_version == "v1":
        _V1CallbackResult(state=state)
        return _v1_dict_adapter.dump_json(state)
    return orjson.dumps(state, default=_ces_encoder)


def json_round_trip(value: Any, *, image_version: ImageVersion = "v1") -> Any:
    """Performs a sandbox-equivalent JSON serialize + deserialize round-trip."""
    if image_version == "v1" and isinstance(value, dict):
        raw_bytes = dump_state_bytes(value, image_version="v1")
        return json.loads(raw_bytes.decode("utf-8"))
    raw_bytes = orjson.dumps(value, default=_ces_encoder)
    return orjson.loads(raw_bytes)


def coerce_struct_numbers(value: Any) -> Any:
    """Simulates Protobuf `Struct` number representation.

    In the CES runtime, JSON values converted into Protobuf `Struct`
    (`number_value` = `double`) and back turn all numeric values (excluding
    booleans) into `float` in Python (e.g. `3` -> `3.0`).
    """
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return float(value)
    if isinstance(value, list):
        return [coerce_struct_numbers(item) for item in value]
    if isinstance(value, dict):
        return {k: coerce_struct_numbers(v) for k, v in value.items()}
    return value


def json_normalize_for_diff(
    value: Any, *, image_version: ImageVersion = "v1"
) -> Any:
    """Normalizes values via JSON round-trip and float-coerces integral numbers
    for diffing.
    """

    def _canonicalize_numbers(v: Any) -> Any:
        if isinstance(v, bool) or v is None:
            return v
        if isinstance(v, float) and v.is_integer():
            return int(v)
        if isinstance(v, list):
            return [_canonicalize_numbers(i) for i in v]
        if isinstance(v, dict):
            return {k: _canonicalize_numbers(val) for k, val in v.items()}
        return v

    try:
        rt = json_round_trip(value, image_version=image_version)
        return _canonicalize_numbers(rt)
    except Exception:
        return copy.deepcopy(value)


def update_state_from_snapshot(
    committed_state: dict[str, Any],
    snapshot_before: dict[str, Any],
    mutated_state: dict[str, Any],
    *,
    simulate_struct_numbers: bool = True,
    image_version: ImageVersion = "v1",
) -> dict[str, Any]:
    """Updates committed session state from a pre-step snapshot and mutated
    state.

    1. Serializes `mutated_state` through V1 (`pydantic`) or V2 (`orjson`).
    2. When `simulate_struct_numbers=True`, converts integers in the
       round-tripped
       struct to `float` matching Protobuf `Struct` number representation.
    3. Removes keys that existed in `snapshot_before` but are absent in
       `new_state`.
    4. Removes keys whose value in `new_state` is `None`.
    5. Upserts all other keys from `new_state` into `committed_state`.
    """
    rt_state = json_round_trip(mutated_state, image_version=image_version)
    if not isinstance(rt_state, dict):
        raise TypeError(
            "callback_context.state must serialize to a JSON object, got "
            f"{type(rt_state).__name__}"
        )
    if simulate_struct_numbers:
        rt_state = coerce_struct_numbers(rt_state)

    next_state = copy.deepcopy(committed_state)

    for old_key in snapshot_before:
        if old_key not in rt_state:
            next_state.pop(old_key, None)

    for key, val in rt_state.items():
        if val is None:
            next_state.pop(key, None)
        else:
            next_state[key] = val

    return next_state


def detect_state_aliasing(state: dict[str, Any]) -> list[str]:
    """Detects distinct top-level state keys that share a mutable container in
    memory.
    """
    container_to_top_keys: dict[int, set[str]] = {}
    ignored_top_keys = {"_session", "session_metadata"}

    def _visit(obj: Any, top_key: str, seen: set[int]) -> None:
        if not isinstance(obj, (dict, list)):
            return
        oid = id(obj)
        if oid in seen:
            return
        seen.add(oid)
        container_to_top_keys.setdefault(oid, set()).add(top_key)
        if isinstance(obj, dict):
            for v in obj.values():
                _visit(v, top_key, seen)
        elif isinstance(obj, list):
            for item in obj:
                _visit(item, top_key, seen)

    for top_k, top_v in state.items():
        if top_k in ignored_top_keys:
            continue
        _visit(top_v, top_k, set())

    shared_groups: set[tuple[str, ...]] = set()
    for keys in container_to_top_keys.values():
        if len(keys) > 1:
            shared_groups.add(tuple(sorted(keys)))

    warnings: list[str] = []
    for group in sorted(shared_groups):
        warnings.append(
            f"State keys {list(group)} share the same mutable dict/list "
            "reference in memory."
        )
    return warnings


def normalize_llm_response(
    raw_resp: Any, *, image_version: ImageVersion = "v1"
) -> LlmResponse:
    """Normalizes an `LlmResponse` returned by a model callback.

    - In `v1`, `_CallbackResult(llm_response=raw_resp)` accepts a `dict` that
      conforms
      to `LlmResponse` schema (`content`/`partial`/`turn_complete`); in `v2` (or
      invalid dicts),
      requires an `LlmResponse` instance.
    - Preserves `content` and `partial`; strips `turn_complete`
      (`turnComplete`).
    - Reorders `content.parts` so all non-`function_call` parts appear first,
      followed by all `function_call` parts.
    """
    if (
        image_version == "v1"
        and isinstance(raw_resp, dict)
        and bool(raw_resp)
        and set(raw_resp.keys()).issubset(
            {
                "content",
                "partial",
                "turn_complete",
                "turnComplete",
                "usage_metadata",
                "usageMetadata",
                "model_version",
                "modelVersion",
                "finish_reason",
                "finishReason",
                "block_reason",
                "blockReason",
            }
        )
    ):
        v1_res = _V1CallbackResult(llm_response=raw_resp)
        if v1_res.llm_response is not None:
            raw_resp = v1_res.llm_response
    if not isinstance(raw_resp, LlmResponse):
        raise TypeError(
            "Expected LlmResponse from model callback, got "
            f"{type(raw_resp).__name__}"
        )

    # Stash binary thought_signature values before JSON round-trip so raw bytes
    # survive
    orig_sigs: list[bytes | str | None] = []
    safe_resp = copy.deepcopy(raw_resp)
    if safe_resp.content is not None and safe_resp.content.parts:
        for p in safe_resp.content.parts:
            orig_sigs.append(p.thought_signature)
            if isinstance(p.thought_signature, bytes):
                p.thought_signature = None

    rt_dict = json_round_trip(safe_resp, image_version="v2")
    validated = LlmResponse.model_validate(rt_dict)
    if validated.content is not None and validated.content.parts:
        for idx, p in enumerate(validated.content.parts):
            if idx < len(orig_sigs) and orig_sigs[idx] is not None:
                p.thought_signature = orig_sigs[idx]

    reordered_content: Content | None = None
    if validated.content is not None:
        raw_parts = validated.content.parts or []
        non_fc_parts = [p for p in raw_parts if p.function_call is None]
        fc_parts = [p for p in raw_parts if p.function_call is not None]
        reordered_content = Content(
            role=validated.content.role,
            parts=non_fc_parts + fc_parts,
        )

    return LlmResponse(
        content=reordered_content,
        partial=validated.partial,
        turn_complete=None,
        usage_metadata=validated.usage_metadata,
        model_version=validated.model_version,
        finish_reason=validated.finish_reason,
        block_reason=validated.block_reason,
    )


def normalize_agent_content(
    raw_content: Any, stage: StageType, *, image_version: ImageVersion = "v1"
) -> Content:
    """Serializes and validates `Content` returned by `before_agent` /
    `after_agent`.
    """
    if (
        image_version == "v1"
        and isinstance(raw_content, dict)
        and bool(raw_content)
        and set(raw_content.keys()).issubset({"parts", "role"})
    ):
        v1_res = _V1CallbackResult(content=raw_content)
        if v1_res.content is not None:
            raw_content = v1_res.content
    if not isinstance(raw_content, Content):
        raise TypeError(
            f"Expected Content from {stage} callback, got "
            f"{type(raw_content).__name__}"
        )
    rt_dict = json_round_trip(raw_content, image_version="v2")
    return Content.model_validate(rt_dict)


def validate_before_agent_result(content: Content) -> None:
    """Validates the post-execution `Content` returned by a `before_agent`
    callback.

    In the CES runtime, this check runs after `state` is committed and outside
    the callback exception handler (so it always produces a hard
    `FAILED_PRECONDITION` / `UNSUPPORTED_CALLBACK_RESULT` error and never
    triggers a fallback response).
    """
    for part in content.parts or []:
        if part.function_call is not None:
            fn_name = part.function_call.name
            if fn_name != "end_session":
                raise ValueError(
                    "UNSUPPORTED_CALLBACK_RESULT: Before agent callback only "
                    f"supports end_session tool call, got '{fn_name}'."
                )


def apply_before_model_request_updates(
    base_request: LlmRequest,
    mutated_request: LlmRequest,
    *,
    allow_content_update: bool,
    image_version: ImageVersion = "v1",
) -> LlmRequest:
    """Applies in-place `LlmRequest` updates from a `before_model` callback.

    - Both V1 and V2:
      - Round-trip `mutated_request` through JSON.
      - Update `contents` only when `allow_content_update=True`.
      - Return early if `mutated_request.config` is `None`.
      - Filter `tools` declarations using `excluded_tools`, and do NOT carry
        `excluded_tools` onto the rebuilt `LlmRequest.config`.
    - V1:
      - Returns early if both `config.system_instruction` is `None` and
        `config.excluded_tools` is `None`.
      - Only updates `system_instruction` if `config.system_instruction` is not
        `None`.
    - V2:
      - Unconditionally overwrites `system_instruction` whenever `config` is
        present
        (even if `config.system_instruction` is `None`).
    """
    safe_req = copy.deepcopy(mutated_request)
    content_sigs: dict[tuple[int, int], bytes | str | None] = {}
    for c_idx, c in enumerate(safe_req.contents or []):
        for p_idx, p in enumerate(c.parts or []):
            if p.thought_signature is not None:
                content_sigs[(c_idx, p_idx)] = p.thought_signature
                if isinstance(p.thought_signature, bytes):
                    p.thought_signature = None

    rt_dict = json_round_trip(safe_req, image_version="v2")
    updated = LlmRequest.model_validate(rt_dict)
    for (c_idx, p_idx), sig_val in content_sigs.items():
        if c_idx < len(updated.contents or []):
            parts_list = updated.contents[c_idx].parts or []
            if p_idx < len(parts_list):
                parts_list[p_idx].thought_signature = sig_val
    result = copy.deepcopy(base_request)

    if (
        not getattr(result, "_initial_tools_meta", None)
        and result.config is not None
        and result.config.tools
    ):
        init_meta: list[tuple[str, str]] = []
        for t_decl in result.config.tools:
            for fn in t_decl.function_declarations or []:
                if fn.name:
                    init_meta.append((fn.name, fn.description or ""))
        result._initial_tools_meta = init_meta

    if allow_content_update:
        result.contents = copy.deepcopy(updated.contents)

    if updated.config is None:
        return result

    updated_cfg = updated.config

    if image_version == "v1":
        if (
            updated_cfg.system_instruction is None
            and updated_cfg.excluded_tools is None
        ):
            return result

        if result.config is None:
            result.config = GenerateContentConfig()

        if updated_cfg.system_instruction is not None:
            result.config.system_instruction = copy.deepcopy(
                updated_cfg.system_instruction
            )

        if updated_cfg.excluded_tools is not None:
            excluded_names = set(updated_cfg.excluded_tools)
            for ex_n in updated_cfg.excluded_tools:
                if ex_n not in result._excluded_tools_applied:
                    result._excluded_tools_applied.append(str(ex_n))
            if result.config.tools:
                new_tools: list[ToolDeclaration] = []
                for tool_decl in result.config.tools:
                    if tool_decl.function_declarations is not None:
                        kept = [
                            fn
                            for fn in tool_decl.function_declarations
                            if (fn.name or "") not in excluded_names
                        ]
                        new_tools.append(
                            ToolDeclaration(function_declarations=kept)
                        )
                    else:
                        new_tools.append(copy.deepcopy(tool_decl))
                result.config.tools = new_tools
        result.config.excluded_tools = None
        return result

    # V2 path
    if result.config is None:
        result.config = GenerateContentConfig()

    result.config.system_instruction = copy.deepcopy(
        updated_cfg.system_instruction
    )

    excluded_names = set(updated_cfg.excluded_tools or [])
    for ex_n in updated_cfg.excluded_tools or []:
        if ex_n not in result._excluded_tools_applied:
            result._excluded_tools_applied.append(str(ex_n))
    if result.config.tools:
        new_tools = []
        for tool_decl in result.config.tools:
            if tool_decl.function_declarations is not None:
                kept = [
                    fn
                    for fn in tool_decl.function_declarations
                    if (fn.name or "") not in excluded_names
                ]
                new_tools.append(ToolDeclaration(function_declarations=kept))
            else:
                new_tools.append(copy.deepcopy(tool_decl))
        result.config.tools = new_tools

    result.config.excluded_tools = None
    return result


def extract_tool_input_update(
    current_tool_input: dict[str, Any],
    work_tool_input: Any,
    *,
    simulate_struct_numbers: bool = True,
    image_version: ImageVersion = "v1",
) -> dict[str, Any]:
    """Extracts updated tool input from `before_tool` callback mutations.

    In the CES runtime, if the serialized tool input struct is non-empty,
    `input` is cleared and replaced with the updated struct; if the struct is
    empty (`{}`), the previous `input` is preserved unchanged.
    """
    rt_tool_in = json_round_trip(work_tool_input, image_version=image_version)
    if isinstance(rt_tool_in, dict) and len(rt_tool_in) > 0:
        if simulate_struct_numbers:
            rt_tool_in = coerce_struct_numbers(rt_tool_in)
        return rt_tool_in
    return copy.deepcopy(current_tool_input)


def validate_and_normalize_return(
    stage: StageType,
    raw_ret: Any,
    *,
    image_version: ImageVersion = "v1",
) -> Any:
    """Validates and normalizes a callback's return value according to stage and
    image version.

    For `before_tool` and `after_tool`:
    - In `v1`: `_CallbackResult(tool_response=raw_ret)` validates that `raw_ret`
      is
      `Optional[dict[str, Any]]` (raising `pydantic.ValidationError` on non-dict
      returns).
      Then the runtime treats an empty dict `{}` as `None` (no override).
    - In `v2`: the runtime treats both non-struct (non-dict) and empty struct
      `{}`
      returns as `None` (continue without short-circuiting).
    """
    if raw_ret is None:
        return None
    if stage in ("before_agent", "after_agent"):
        return normalize_agent_content(
            raw_ret, stage, image_version=image_version
        )
    if stage in ("before_model", "after_model"):
        return normalize_llm_response(raw_ret, image_version=image_version)
    if stage in ("before_tool", "after_tool"):
        if image_version == "v1":
            _V1CallbackResult(tool_response=raw_ret)
        if not isinstance(raw_ret, dict):
            return None
        rt = json_round_trip(raw_ret, image_version=image_version)
        if not isinstance(rt, dict) or len(rt) == 0:
            return None
        return rt
    return raw_ret
