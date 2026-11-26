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
  if context.state.get("return_pickup_completed"):
    return {
        "status": "error",
        "error": "pickup_already_booked",
        "confirmation_id": context.state.get("return_confirmation_id") or "",
    }
  if summary_confirmed:
    context.state["return_summary_confirmed"] = True

  order_id = context.state.get("order_id") or ""
  missing = [
      name
      for name, ok in (
          ("order_id", bool(order_id)),
          (
              "delivered_order",
              context.state.get("order_status") == "Delivered",
          ),
          ("zip_verified", bool(context.state.get("zip_verified"))),
          ("return_reason", bool(context.state.get("return_reason"))),
          (
              "return_availability_checked",
              bool(context.state.get("return_availability_checked")),
          ),
          ("return_pickup_day", bool(context.state.get("return_pickup_day"))),
          (
              "return_pickup_window",
              bool(context.state.get("return_pickup_window")),
          ),
          (
              "return_contact_phone",
              bool(context.state.get("return_contact_phone")),
          ),
          (
              "return_summary_confirmed",
              bool(context.state.get("return_summary_confirmed")),
          ),
      )
      if not ok
  ]
  if missing:
    return {
        "status": "missing_prerequisites",
        "missing": missing,
    }

  confirmation_id = f"RP-{order_id}"
  pickup_day = context.state["return_pickup_day"]
  pickup_window = context.state["return_pickup_window"]
  context.state["order_status"] = "Return Pickup Scheduled"
  context.state["return_pickup_completed"] = True
  context.state["return_summary_confirmed"] = False
  context.state["return_confirmation_id"] = confirmation_id
  return {
      "status": "scheduled",
      "order_number": order_id,
      "confirmation_id": confirmation_id,
      "pickup_day": pickup_day,
      "pickup_window": pickup_window,
  }
