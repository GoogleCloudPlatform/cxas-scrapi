# pylint: disable=invalid-name,undefined-variable,unused-argument,broad-exception-caught,line-too-long
"""After-model callback — payload injection for non-preempted turns.

FRAMEWORK CODE — adapted for multi-agent SM namespacing.
Only _SM_KEY differs between agents.
"""

import json as json_lib
from typing import Optional


_SM_KEY = "sm"


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

  combined = list(llm_response.content.parts) + extra_parts
  return LlmResponse.from_parts(parts=combined)
