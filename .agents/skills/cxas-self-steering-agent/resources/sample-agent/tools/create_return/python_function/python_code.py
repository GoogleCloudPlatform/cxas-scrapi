# pylint: disable=missing-module-docstring,undefined-variable
import re
from typing import Any


def create_return(order_number: str, reason: str) -> dict[str, Any]:
  """Starts a return for a delivered order and emails the customer a return label.

  Args:
    order_number (str): The order number of the delivered order, as a string.
    reason (str): The customer's reason for the return, in a few words.

  Returns:
    dict[str, Any]: The return details, including how the label is sent and
      when to expect the refund.
  """
  number = re.sub(r"\D", "", order_number or "")
  if (
      number != context.state.get("order_id")
      or context.state.get("order_status") != "Delivered"
  ):
    return {
        "success": False,
        "error": (
            "Only a delivered order that was looked up in this conversation can"
            " be returned."
        ),
    }

  context.state["order_status"] = "Return Started"
  return {
      "success": True,
      "order_number": number,
      "return_id": f"R{number}",
      "reason": reason,
      "return_label": "emailed to the address on the account",
      "refund_timeline": (
          "within 5 business days after the item arrives back at our warehouse"
      ),
  }
