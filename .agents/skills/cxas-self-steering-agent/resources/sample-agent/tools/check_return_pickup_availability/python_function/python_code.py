# pylint: disable=missing-module-docstring,undefined-variable
from typing import Any

AVAILABLE_DAYS = ("Thursday", "Friday", "Monday")
AVAILABLE_WINDOWS = ("morning", "afternoon")


def check_return_pickup_availability() -> dict[str, Any]:
  """Looks up available courier pickup days and time windows for the verified delivered order.

  Returns:
    dict[str, Any]: Available pickup days and windows, or an error code if no
      verified delivered order is active.
  """
  order = context.state.get("shared") or {}
  order_id = order.get("order_id") or ""
  if not order_id:
    return {"status": "error", "error": "no_active_order"}
  if order.get("order_status") != "Delivered":
    return {"status": "error", "error": "order_not_delivered"}
  if not order.get("zip_verified"):
    return {"status": "error", "error": "zip_not_verified"}

  context.state["returns"] = {
      **(context.state.get("returns") or {}),
      "availability_checked": True,
  }
  return {
      "status": "ok",
      "available_days": list(AVAILABLE_DAYS),
      "windows": list(AVAILABLE_WINDOWS),
  }
