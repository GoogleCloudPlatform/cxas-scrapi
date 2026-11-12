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
  order_id = context.state.get("order_id") or ""
  if not order_id:
    return {"status": "error", "error": "no_active_order"}
  order_status = context.state.get("order_status") or ""
  if order_status != "Processing":
    return {
        "status": "error",
        "error": "order_not_cancelable",
        "order_status": order_status,
    }
  if not context.state.get("zip_verified"):
    return {"status": "error", "error": "zip_not_verified"}
  if not confirmed:
    return {"status": "error", "error": "confirmation_required"}

  context.state["order_status"] = "Canceled"
  return {
      "status": "canceled",
      "order_number": order_id,
      "reason": reason,
      "refund_business_days": "3 to 5",
  }
