"""After-model callback — payload injection for non-preempted turns.

FRAMEWORK CODE — adapted for multi-agent SM namespacing.
Only _SM_KEY differs between agents.
"""
# pylint: disable=undefined-variable

import json as json_lib
from typing import Optional


_SM_KEY = "sm"


def _extract_payload_parts(response_parts):
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

  announce = sm.pop("_pending_payloads", None)
  question = sm.pop("_pending_question_payloads", None)

  if not announce and not question:
    return None

  for event in reversed(callback_context.events):
    if event.is_user():
      break
    if event.is_agent():
      if event.parts():
        return None

  extra_parts = []

  if announce:
    extra_parts.extend(_extract_payload_parts(announce))

  if question and not announce:
    slot_name = question.get("slot")
    filled = sm.get("filled", {})
    pending = sm.get("pending", {})
    if slot_name and slot_name not in filled and slot_name not in pending:
      extra_parts.extend(_extract_payload_parts(question.get("parts", [])))

  if not extra_parts:
    return None

  combined = list(llm_response.content.parts) + extra_parts
  return LlmResponse.from_parts(parts=combined)
