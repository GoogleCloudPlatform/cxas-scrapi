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
  order = context.state.get("order") or {}
  order_id = order.get("order_id") or ""
  if not order_id:
    return {"status": "error", "error": "no_active_order"}
  zip_check = context.state.get("zip_check") or {}
  if zip_check.get("verified_order_id") != order_id:
    return {"status": "error", "error": "zip_not_verified"}
  if order.get("status") != "Delivered":
    return {
        "status": "error",
        "error": "order_not_delivered",
        "order_status": order.get("status") or "",
    }
  if context.state.get("mail_return_order_id") == order_id:
    return {"status": "error", "error": "return_already_started"}
  if (context.state.get("return_pickup") or {}).get("order_id") == order_id:
    return {"status": "error", "error": "pickup_already_booked"}

  context.state["mail_return_order_id"] = order_id
  return {
      "status": "return_started",
      "order_number": order_id,
      "return_id": f"RM-{order_id}",
      "reason": reason,
      "label_delivery": "emailed",
      "refund_business_days": 5,
  }
