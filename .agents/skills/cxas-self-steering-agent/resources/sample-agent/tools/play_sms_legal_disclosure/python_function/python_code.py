# pylint: disable=missing-module-docstring,undefined-variable
from typing import Any

DISCLOSURE_TEXT = (
    "Just so you know, our standard messaging terms apply, and message and"
    " data rates may apply. Is it okay to text the phone number on your"
    " account?"
)


def play_sms_legal_disclosure() -> dict[str, Any]:
  """Shares the text message terms word for word and asks the customer if we can text them.

  Call this directly without replying to the customer first.

  Returns:
    dict[str, Any]: Whether the terms were shared.
  """
  context.state["sms_disclosure_played"] = True
  tools.set_next_agent_actions({
      "customized_response": {
          "content": DISCLOSURE_TEXT,
          "disable_barge_in": True,
      },
  })
  return {"success": True, "status": "terms_shared"}
