"""Multi-slot setter for guest_name and special_requests."""

from typing import Any


def set_guest_info(
    guest_name: str = "",
    special_requests: str = "",
) -> dict[str, Any]:
  """Record the guest's name and/or special requests.

  Call this tool whenever the guest mentions their name OR special requests /
  dietary needs, even if only one value is provided. Accept any name format
  (first, last, full, nickname). If the guest says 'none' for special
  requests, pass that text as-is. Call immediately, even alongside other info.

  Args:
    guest_name: Guest name string. Pass empty string to omit.
    special_requests: Special requests or dietary needs.
      Pass empty string to omit.

  Returns:
    Dict with stored=True and per-field values/errors.
  """
  values = {}
  field_errors = {}

  if guest_name:
    name = str(guest_name).strip()
    if not name:
      field_errors["guest_name"] = "empty_name"
    else:
      values["guest_name"] = name

  if special_requests:
    values["special_requests"] = str(special_requests).strip()

  if not values and not field_errors:
    return {"error": True, "error_code": "no_input"}

  result = {"stored": True, "values": values}
  if field_errors:
    result["field_errors"] = field_errors
  return result
