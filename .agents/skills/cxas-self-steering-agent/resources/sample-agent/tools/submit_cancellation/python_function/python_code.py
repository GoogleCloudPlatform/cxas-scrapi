# pylint: disable=missing-module-docstring,undefined-variable
import re
from typing import Any


def submit_cancellation(order_number: str, reason: str) -> dict[str, Any]:
  """Cancels an order that hasn't shipped yet.

  Args:
    order_number (str): The order number to cancel, as a string.
    reason (str): The customer's reason for canceling, in a few words.

  Returns:
    dict[str, Any]: Whether the order was canceled, and the refund timeline.
  """
  number = re.sub(r"\D", "", order_number or "")
  if (
      number != context.state.get("order_id")
      or context.state.get("order_status") != "Processing"
  ):
    return {
        "success": False,
        "error": (
            "Only an order that was looked up in this conversation and hasn't"
            " shipped can be canceled."
        ),
    }

  context.state["order_status"] = "Canceled"
  return {
      "success": True,
      "order_number": number,
      "status": "Canceled",
      "reason": reason,
      "refund_timeline": "3 to 5 business days",
  }
