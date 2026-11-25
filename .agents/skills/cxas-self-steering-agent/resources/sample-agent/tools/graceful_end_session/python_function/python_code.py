# pylint: disable=missing-module-docstring,undefined-variable
from typing import Any, Optional

DEFAULT_GOODBYE = (
    "You're all set. Thanks for contacting us, and have a great day."
)


def graceful_end_session(
    goodbye_message: Optional[str] = None,
) -> dict[str, Any]:
  """Says goodbye and ends the conversation.

  Call this directly without replying to the customer first. Put the goodbye
  in goodbye_message instead.

  Args:
    goodbye_message (Optional[str]): A short, warm goodbye to send before the
      conversation ends.

  Returns:
    dict[str, Any]: Session termination status.
  """
  message = (goodbye_message or "").strip() or DEFAULT_GOODBYE
  context.state["next_agent_actions"] = {
      "customized_response": {"content": message, "disable_barge_in": True},
      "end_session": {"reason": "completed", "escalated": False},
  }
  return {"status": "ending"}
