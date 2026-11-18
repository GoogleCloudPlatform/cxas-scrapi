# pylint: disable=missing-module-docstring,undefined-variable
from typing import Any


def submit_return_pickup(summary_confirmed: bool = False) -> dict[str, Any]:
  """Books the courier return pickup using the validated details in session state.

  Only call this after reading back the return pickup summary (order, pickup
  day, time window, and callback phone) and getting the customer's confirmation.

  Args:
    summary_confirmed (bool): True when the customer just confirmed the
      pre-booking summary.

  Returns:
    dict[str, Any]: Booking status, confirmation ID, and scheduled pickup
      window, missing_prerequisites with the list of missing fields, or
      pickup_already_booked if this order's pickup is already booked.
  """
  order = context.state.get("shared") or {}
  returns = dict(context.state.get("returns") or {})
  if returns.get("pickup_completed"):
    return {
        "status": "error",
        "error": "pickup_already_booked",
        "confirmation_id": returns.get("confirmation_id") or "",
    }
  if summary_confirmed:
    returns["summary_confirmed"] = True
    context.state["returns"] = returns

  order_id = order.get("order_id") or ""
  missing = [
      name
      for name, ok in (
          ("order_id", bool(order_id)),
          ("delivered_order", order.get("order_status") == "Delivered"),
          ("zip_verified", bool(order.get("zip_verified"))),
          ("reason", bool(returns.get("reason"))),
          ("availability_checked", bool(returns.get("availability_checked"))),
          ("pickup_day", bool(returns.get("pickup_day"))),
          ("pickup_window", bool(returns.get("pickup_window"))),
          ("contact_phone", bool(returns.get("contact_phone"))),
          ("summary_confirmed", bool(returns.get("summary_confirmed"))),
      )
      if not ok
  ]
  if missing:
    return {
        "status": "missing_prerequisites",
        "missing": missing,
    }

  confirmation_id = f"RP-{order_id}"
  context.state["shared"] = {**order, "order_status": "Return Pickup Scheduled"}
  context.state["returns"] = {
      **returns,
      "pickup_completed": True,
      "summary_confirmed": False,
      "confirmation_id": confirmation_id,
  }
  return {
      "status": "scheduled",
      "order_number": order_id,
      "confirmation_id": confirmation_id,
      "pickup_day": returns["pickup_day"],
      "pickup_window": returns["pickup_window"],
  }
