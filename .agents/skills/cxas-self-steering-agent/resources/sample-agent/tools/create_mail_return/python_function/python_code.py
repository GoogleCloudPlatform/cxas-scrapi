# pylint: disable=missing-module-docstring,undefined-variable
from typing import Any, Literal


def create_mail_return(
    reason: Literal["damaged", "wrong_item", "changed_mind", "other"],
) -> dict[str, Any]:
  """Starts a drop-off mail return for the verified delivered order and emails a return label.

  Args:
    reason (Literal["damaged", "wrong_item", "changed_mind", "other"]): Category
      matching the customer's reason for returning the item.

  Returns:
    dict[str, Any]: Return status, return ID, how the label is delivered, and
      the refund timeline, or an error code when prerequisites are not met.
  """
  order_id = context.state.get("order_id") or ""
  if not order_id:
    return {"status": "error", "error": "no_active_order"}
  order_status = context.state.get("order_status") or ""
  if order_status != "Delivered":
    return {
        "status": "error",
        "error": "order_not_delivered",
        "order_status": order_status,
    }
  if not context.state.get("zip_verified"):
    return {"status": "error", "error": "zip_not_verified"}

  context.state["order_status"] = "Return Started"
  return {
      "status": "return_started",
      "order_number": order_id,
      "return_id": f"RM-{order_id}",
      "reason": reason,
      "label_delivery": "emailed",
      "refund_business_days": 5,
  }
