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
    dict[str, Any]: Status and the order number for which terms were shared.
  """
  order_id = context.state.get("order_id") or ""
  if not order_id:
    return {"status": "error", "error": "no_active_order"}

  context.state["sms_disclosure_played"] = order_id
  tools.set_next_agent_actions({
      "customized_response": {
          "content": DISCLOSURE_TEXT,
          "disable_barge_in": True,
      },
  })
  return {"status": "terms_shared", "order_number": order_id}
