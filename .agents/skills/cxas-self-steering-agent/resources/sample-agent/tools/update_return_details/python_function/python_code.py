# pylint: disable=missing-module-docstring,undefined-variable
import re
from typing import Any, Literal, Optional

AVAILABLE_DAYS = ("Thursday", "Friday", "Monday")
VALID_REASONS = ("damaged", "wrong_item", "changed_mind", "other")
VALID_WINDOWS = ("morning", "afternoon")


def update_return_details(
    reason: Optional[
        Literal["damaged", "wrong_item", "changed_mind", "other"]
    ] = None,
    pickup_day: Optional[str] = None,
    pickup_window: Optional[Literal["morning", "afternoon"]] = None,
    contact_phone: Optional[str] = None,
    summary_confirmed: Optional[bool] = None,
) -> dict[str, Any]:
  """Records or updates return pickup details in session state.

  Supports initial slot collection, early details volunteered by the customer,
  and edits during the pre-booking summary review. Modifying any detail resets
  summary_confirmed so the updated summary is confirmed before booking.
  Details collected for a different order start over.

  Args:
    reason (Optional[str]): Why the customer is returning the item, one of
      "damaged", "wrong_item", "changed_mind", or "other".
    pickup_day (Optional[str]): Selected pickup day from
      check_return_pickup_availability (e.g. 'Thursday', 'Friday', or 'Monday').
    pickup_window (Optional[Literal["morning", "afternoon"]]): Selected courier
      time window ('morning' or 'afternoon').
    contact_phone (Optional[str]): Customer's 10-digit US callback phone number
      for the courier.
    summary_confirmed (Optional[bool]): True when the customer confirms the
      pre-booking summary; False when they reject it.

  Returns:
    dict[str, Any]: Validation status, the fields updated in this call, and
      not_recorded for any fields skipped because availability was not checked
      yet, or pickup_already_booked once the pickup has been booked.
  """
  order_id = (context.state.get("order") or {}).get("order_id") or ""
  if not order_id:
    return {"status": "error", "error": "no_active_order"}
  if (context.state.get("return_pickup") or {}).get("order_id") == order_id:
    return {"status": "error", "error": "pickup_already_booked"}
  details = dict(context.state.get("return_details") or {})
  if details.get("order_id") != order_id:
    # Details collected for another order don't carry over.
    details = {
        "order_id": order_id,
        "reason": "",
        "pickup_day": "",
        "pickup_window": "",
        "contact_phone": "",
        "summary_confirmed": False,
    }
  availability_checked = (
      context.state.get("availability_order_id") == order_id
  )
  updated: dict[str, Any] = {}
  not_recorded: dict[str, str] = {}
  edited = False
  if reason is not None:
    if reason not in VALID_REASONS:
      return {
          "status": "invalid_reason",
          "valid_reasons": list(VALID_REASONS),
      }
    details["reason"] = reason
    updated["reason"] = reason
    edited = True
  if pickup_day is not None or pickup_window is not None:
    if not availability_checked:
      if pickup_day is not None:
        not_recorded["pickup_day"] = "availability_not_checked"
      if pickup_window is not None:
        not_recorded["pickup_window"] = "availability_not_checked"
    else:
      if pickup_day is not None:
        matched_day = _match_day(pickup_day)
        if not matched_day:
          context.state["return_details"] = details
          return {
              "status": "invalid_pickup_day",
              "available_days": list(AVAILABLE_DAYS),
              "updated": updated,
          }
        details["pickup_day"] = matched_day
        updated["pickup_day"] = matched_day
        edited = True
      if pickup_window is not None:
        window = pickup_window.strip().lower()
        if window not in VALID_WINDOWS:
          context.state["return_details"] = details
          return {
              "status": "invalid_pickup_window",
              "valid_windows": list(VALID_WINDOWS),
              "updated": updated,
          }
        details["pickup_window"] = window
        updated["pickup_window"] = window
        edited = True
  if contact_phone is not None:
    phone_status, formatted_phone = _validate_us_phone(contact_phone)
    if phone_status != "ok":
      context.state["return_details"] = details
      return {
          "status": phone_status,
          "updated": updated,
      }
    details["contact_phone"] = formatted_phone
    updated["contact_phone"] = formatted_phone
    edited = True
  if edited:
    details["summary_confirmed"] = False
  elif summary_confirmed is not None:
    details["summary_confirmed"] = bool(summary_confirmed)
    updated["summary_confirmed"] = bool(summary_confirmed)
  context.state["return_details"] = details
  result: dict[str, Any] = {"status": "updated", "updated": updated}
  if not_recorded:
    result["not_recorded"] = not_recorded
  return result

def _match_day(raw_day: str) -> str:
  cleaned = (raw_day or "").strip().lower()
  for day in AVAILABLE_DAYS:
    if cleaned == day.lower():
      return day
  return ""


def _validate_us_phone(raw_phone: str) -> tuple[str, str]:
  digits = re.sub(r"\D", "", raw_phone or "")
  if len(digits) == 11 and digits.startswith("1"):
    digits = digits[1:]
  if len(digits) == 7:
    return "missing_area_code", ""
  if len(digits) != 10:
    return "invalid_phone", ""
  return "ok", f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
