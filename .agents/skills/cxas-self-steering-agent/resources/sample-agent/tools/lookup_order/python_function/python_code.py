# pylint: disable=missing-module-docstring,undefined-variable
import re
from typing import Any

# Sample data. Replace this with a call to your order system.
SAMPLE_ORDERS = {
    "1001": {
        "status": "Processing",
        "item": "wireless headphones",
        "estimated_delivery": "Friday",
        "zip": "94043",
    },
    "1002": {
        "status": "Shipped",
        "item": "coffee grinder",
        "estimated_delivery": "tomorrow by 8 PM",
        "zip": "98101",
    },
    "1003": {
        "status": "Delivered",
        "item": "desk lamp",
        "delivered_on": "last Tuesday",
        "zip": "10001",
    },
    "1004": {
        "status": "Delivered",
        "item": "running shoes",
        "delivered_on": "yesterday",
        "zip": "60601",
    },
}


def lookup_order(order_number: str) -> dict[str, Any]:
  """Looks up an order by its order number.

  Args:
    order_number (str): The order number the customer gave, as a string, e.g.
      "1001".

  Returns:
    dict[str, Any]: found=True and the order number, or found=False if no
      order has that number. The order's details stay in session state until
      the customer verifies its shipping ZIP code.
  """
  number = re.sub(r"\D", "", order_number or "")
  order = SAMPLE_ORDERS.get(number)
  if order is None:
    _reset_order_state(context.state)
    return {"found": False, "order_number": order_number}

  shared = context.state.get("shared") or {}
  if shared.get("order_id") == number:
    # Keep the status and verification of the order already selected.
    return {"found": True, "order_number": number}

  _reset_order_state(context.state)
  context.state["shared"] = {
      **(context.state.get("shared") or {}),
      "order_id": number,
      "order_status": order["status"],
      "order_item": order["item"],
      "order_delivery": (
          f"estimated delivery {order['estimated_delivery']}"
          if "estimated_delivery" in order
          else f"delivered {order['delivered_on']}"
      ),
      "on_file_zip": order["zip"],
  }
  return {"found": True, "order_number": number}


def _reset_order_state(state: Any) -> None:
  """Clears the previous order's record and every playbook's state for it.

  zip_attempts stays, since failed ZIP codes count for the whole conversation.
  """
  shared = state.get("shared") or {}
  state["shared"] = {
      "order_id": "",
      "order_status": "",
      "order_item": "",
      "order_delivery": "",
      "on_file_zip": "",
      "zip_verified": False,
      "zip_attempts": shared.get("zip_attempts") or 0,
  }
  state["order_support"] = {
      "sms_disclosure_played": "",
      "sms_summary_sent": "",
  }
  state["returns"] = {
      "reason": "",
      "availability_checked": False,
      "pickup_day": "",
      "pickup_window": "",
      "contact_phone": "",
      "summary_confirmed": False,
      "pickup_completed": False,
      "confirmation_id": "",
  }
