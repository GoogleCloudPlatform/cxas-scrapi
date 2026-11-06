# pylint: disable=missing-module-docstring,undefined-variable
import re
from typing import Any

# Sample data. Replace this with a call to your order system.
SAMPLE_ORDERS = {
    "1001": {
        "status": "Processing",
        "item": "wireless headphones",
        "estimated_delivery": "Friday",
    },
    "1002": {
        "status": "Shipped",
        "item": "a coffee grinder",
        "estimated_delivery": "tomorrow by 8 PM",
    },
    "1003": {
        "status": "Delivered",
        "item": "a desk lamp",
        "delivered_on": "last Tuesday",
    },
}


def lookup_order(order_number: str) -> dict[str, Any]:
  """Looks up an order by its order number.

  Args:
    order_number (str): The order number the customer gave, as a string, e.g.
      "1001".

  Returns:
    dict[str, Any]: The order's status, item, and delivery details, or
      found=False if no order has that number.
  """
  number = re.sub(r"\D", "", order_number or "")
  order = SAMPLE_ORDERS.get(number)
  if order is None:
    context.state["order_id"] = ""
    context.state["order_status"] = ""
    return {"found": False, "order_number": order_number}

  order = dict(order)
  # Keep a cancellation or return made earlier in this conversation.
  if context.state.get("order_id") == number and context.state.get(
      "order_status"
  ) in ("Canceled", "Return Started"):
    order["status"] = context.state["order_status"]
  context.state["order_id"] = number
  context.state["order_status"] = order["status"]
  return {"found": True, "order_number": number, **order}
