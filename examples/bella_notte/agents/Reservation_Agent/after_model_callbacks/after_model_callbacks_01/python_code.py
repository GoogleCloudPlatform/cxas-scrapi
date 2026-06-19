# pylint: disable=invalid-name,undefined-variable,unused-argument,broad-exception-caught,line-too-long
"""After-model callback — payload injection for non-preempted turns.

FRAMEWORK CODE — adapted for multi-agent SM namespacing.
Only _SM_KEY differs between agents.
"""

import json as json_lib
import logging
from typing import Optional


_SM_KEY = "sm"

_LEVEL_MAP = {"DEBUG": logging.DEBUG, "INFO": logging.INFO,
              "WARN": logging.WARNING, "ERROR": logging.ERROR}
_LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
_logger = logging.getLogger("slot_filling.after_model")


def _log(sm, tag, level="INFO", **data):
  """Emit structured log entry; append to sm["_log"].

  Args:
    sm: Session state machine dict (callback_context.state).
    tag: Short label identifying the log event.
    level: Severity — DEBUG, INFO, WARN, or ERROR.
    **data: Arbitrary key-value payload for the log entry.
  """
  min_level = sm.get("_log_level", "INFO")
  if _LEVEL_ORDER.get(level, 1) < _LEVEL_ORDER.get(min_level, 1):
    return
  entry = {"src": "after_model", "tag": tag, "level": level,
           "data": {k: v for k, v in data.items() if v is not None}}
  _logger.log(_LEVEL_MAP.get(level, logging.INFO),
              json_lib.dumps(entry, default=str))
  sm.setdefault("_log", []).append(entry)


def _extract_response_parts(response_parts):
  """Extract payload parts only — text parts are for the preempted path."""
  parts = []
  for rp in response_parts:
    if rp.get("type") == "payload":
      parts.append(
          Part.from_json(
              json_lib.dumps(rp["data"])))
  return parts


def after_model_callback(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> Optional[LlmResponse]:
  """Inject stashed payloads into the LLM response if present."""
  sm = callback_context.state.get(_SM_KEY, {})

  if not sm.get("_pending_payloads") and not sm.get(
      "_pending_question_payloads"):
    return None

  for part in llm_response.content.parts:
    if getattr(part, "function_call", None):
      return None

  announce = sm.pop("_pending_payloads", None)
  question = sm.pop("_pending_question_payloads", None)

  extra_parts = []

  if announce:
    extra_parts.extend(_extract_response_parts(announce))

  if question:
    slot_name = question.get("slot")
    filled = sm.get("filled", {})
    pending = sm.get("pending", {})
    if slot_name and slot_name not in filled and slot_name not in pending:
      extra_parts.extend(_extract_response_parts(question.get("parts", [])))

  if not extra_parts:
    return None

  _log(sm, "payloads_injected", "DEBUG",
       n_announce=len(_extract_response_parts(announce)) if announce else 0,
       n_question=len(_extract_response_parts(question.get("parts", []))) if question else 0)
  callback_context.state[_SM_KEY] = sm
  combined = list(llm_response.content.parts) + extra_parts
  return LlmResponse.from_parts(parts=combined)
