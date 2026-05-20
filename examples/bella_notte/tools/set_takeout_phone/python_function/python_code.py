"""Setter tool for contact_phone slot in takeout."""

import re
from typing import Any


def set_takeout_phone(phone: str) -> dict[str, Any]:
  """Record the contact phone number for the takeout order.

  Args:
    phone: The contact phone number.

  Returns:
    Dict with stored=True and value on success, or error on failure.
  """
  phone_str = str(phone).strip()
  digits = re.sub(r"\D", "", phone_str)
  if len(digits) < 7:
    return {"error": True, "error_code": "invalid_phone"}
  return {"stored": True, "value": phone_str}
