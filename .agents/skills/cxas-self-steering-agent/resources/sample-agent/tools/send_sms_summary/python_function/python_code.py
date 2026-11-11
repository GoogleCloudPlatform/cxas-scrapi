# pylint: disable=missing-module-docstring,undefined-variable
import re
from typing import Any


def send_sms_summary(order_number: str) -> dict[str, Any]:
  """Texts an order summary to the phone number on the customer's account.

  Only call this after the text message terms were shared and the customer
  agreed to get the text.

  Args:
    order_number (str): The order number to summarize, as a string.

  Returns:
    dict[str, Any]: Whether the text was sent.
  """
  if not context.state.get("sms_disclosure_played"):
    return {
        "success": False,
        "error": "Share the text message terms with the customer first.",
    }
  return {
      "success": True,
      "order_number": re.sub(r"\D", "", order_number or ""),
      "status": "sent",
  }
