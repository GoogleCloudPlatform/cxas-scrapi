# pylint: disable=missing-module-docstring,undefined-variable
from typing import Any, Optional

DEFAULT_TRANSFER_MESSAGE = (
    "Sure, let me connect you with a representative who can help."
)


def execute_live_agent_handover(
    reason: Optional[str] = None,
    pre_transfer_message: Optional[str] = None,
) -> dict[str, Any]:
  """Hands the conversation to a live representative.

  Call this directly without replying to the customer first. Put the message
  for the customer in pre_transfer_message instead.

  Args:
    reason (Optional[str]): Short reason code for the handoff, e.g.
      'customer_requested_agent' or 'request_not_supported'. Used when
      pending_handoff_reason is not already set in session state.
    pre_transfer_message (Optional[str]): One sentence letting the customer know
      you're connecting them with a representative.

  Returns:
    dict[str, Any]: Handoff status and reason.
  """
  pending_reason = str(
      context.state.get("pending_handoff_reason") or ""
  ).strip()
  context.state["pending_handoff_reason"] = ""
  handoff_reason = (
      pending_reason or (reason or "").strip() or "customer_requested_agent"
  )
  message = (pre_transfer_message or "").strip() or DEFAULT_TRANSFER_MESSAGE
  context.state["next_agent_actions"] = {
      "customized_response": {"content": message, "disable_barge_in": True},
      "end_session": {"reason": handoff_reason, "escalated": True},
  }
  return {"status": "transferring", "reason": handoff_reason}
