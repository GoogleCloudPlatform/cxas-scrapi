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
  order = context.state.get("order") or {}
  order_id = order.get("order_id") or ""
  booking = context.state.get("return_pickup") or {}
  if order_id and booking.get("order_id") == order_id:
    return {
        "status": "error",
        "error": "pickup_already_booked",
        "confirmation_id": booking.get("confirmation_id") or "",
    }
  details = context.state.get("return_details") or {}
  if details.get("order_id") != order_id:
    details = {}
  zip_check = context.state.get("zip_check") or {}
  missing = [
      name
      for name, ok in (
          ("order_id", bool(order_id)),
          ("delivered_order", order.get("status") == "Delivered"),
          ("zip_verified", zip_check.get("verified_order_id") == order_id),
          (
              "no_mail_return",
              context.state.get("mail_return_order_id") != order_id,
          ),
          ("reason", bool(details.get("reason"))),
          (
              "availability_checked",
              context.state.get("availability_order_id") == order_id,
          ),
          ("pickup_day", bool(details.get("pickup_day"))),
          ("pickup_window", bool(details.get("pickup_window"))),
          ("contact_phone", bool(details.get("contact_phone"))),
          (
              "summary_confirmed",
              summary_confirmed or bool(details.get("summary_confirmed")),
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
  context.state["return_pickup"] = {
      "order_id": order_id,
      "confirmation_id": confirmation_id,
      "pickup_day": details["pickup_day"],
      "pickup_window": details["pickup_window"],
  }
  return {
      "status": "scheduled",
      "order_number": order_id,
      "confirmation_id": confirmation_id,
      "pickup_day": details["pickup_day"],
      "pickup_window": details["pickup_window"],
  }
