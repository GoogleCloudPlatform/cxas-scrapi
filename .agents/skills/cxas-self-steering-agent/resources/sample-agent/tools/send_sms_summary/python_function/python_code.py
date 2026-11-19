# pylint: disable=missing-module-docstring,undefined-variable
from typing import Any


def send_sms_summary() -> dict[str, Any]:
  """Texts an order summary for the loaded order to the phone number on the account.

  Only call this after the text message terms were shared for the loaded order
  and the customer agreed to get the text.

  Returns:
    dict[str, Any]: Delivery status and the order number summarized.
  """
  order_id = context.state.get("order_id") or ""
  if not order_id:
    return {"status": "error", "error": "no_active_order"}
  if context.state.get("sms_disclosure_played") != order_id:
    return {"status": "error", "error": "sms_disclosure_required"}
  context.state["sms_summary_sent"] = order_id
  return {
      "status": "sent",
      "order_number": order_id,
  }
