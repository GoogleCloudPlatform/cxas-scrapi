# pylint: disable=missing-module-docstring,undefined-variable
from typing import Any, Literal


def submit_cancellation(
    reason: Literal[
        "changed_mind", "ordered_by_mistake", "found_better_price", "other"
    ],
    confirmed: bool = False,
) -> dict[str, Any]:
  """Cancels the currently loaded order after ZIP verification and confirmation.

  Args:
    reason (str): Why the customer is canceling, one of "changed_mind",
      "ordered_by_mistake", "found_better_price", or "other".
    confirmed (bool): True once the customer explicitly confirms canceling the
      order.

  Returns:
    dict[str, Any]: Cancellation status, order number, and refund timeline, or
      an error code when prerequisites are not met.
  """
  order = context.state.get("order") or {}
  order_id = order.get("order_id") or ""
  if not order_id:
    return {"status": "error", "error": "no_active_order"}
  zip_check = context.state.get("zip_check") or {}
  if zip_check.get("verified_order_id") != order_id:
    return {"status": "error", "error": "zip_not_verified"}
  if context.state.get("canceled_order_id") == order_id:
    return {"status": "error", "error": "already_canceled"}
  if order.get("status") != "Processing":
    return {
        "status": "error",
        "error": "order_not_cancelable",
        "order_status": order.get("status") or "",
    }
  if not confirmed:
    return {"status": "error", "error": "confirmation_required"}

  context.state["canceled_order_id"] = order_id
  return {
      "status": "canceled",
      "order_number": order_id,
      "reason": reason,
      "refund_business_days": "3 to 5",
  }
