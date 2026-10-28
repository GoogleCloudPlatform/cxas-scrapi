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

from typing import TYPE_CHECKING, Any

from cxas_scrapi.utils.callback_libs import (
    CallbackContext,
    Content,
    LlmResponse,
    Part,
)

if TYPE_CHECKING:
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
    max_attempts = int(
        fb_cfg.get(
            "maxFallbackAttempts",
            fb_cfg.get("max_fallback_attempts", 0),
        )
        or 0
    )

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
