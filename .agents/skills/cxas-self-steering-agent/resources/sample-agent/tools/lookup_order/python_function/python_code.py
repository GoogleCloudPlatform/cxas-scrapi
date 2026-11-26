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
  found = SAMPLE_ORDERS.get(number)
  if found is None:
    context.state["order"] = {
        "order_id": "",
        "status": "",
        "item": "",
        "delivery": "",
        "on_file_zip": "",
    }
    return {"found": False, "order_number": order_number}

  context.state["order"] = {
      "order_id": number,
      "status": found["status"],
      "item": found["item"],
      "delivery": (
          f"estimated delivery {found['estimated_delivery']}"
          if "estimated_delivery" in found
          else f"delivered {found['delivered_on']}"
      ),
      "on_file_zip": found["zip"],
  }
  return {"found": True, "order_number": number}
