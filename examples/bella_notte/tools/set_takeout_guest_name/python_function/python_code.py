"""Setter tool for guest_name slot in takeout."""

from typing import Any


def set_takeout_guest_name(name: str) -> dict[str, Any]:
  """Record the guest name for the takeout order.

  Args:
    name: The name under which to put the order.

  Returns:
    Dict with stored=True and value on success, or error on failure.
  """
  name_str = str(name).strip()
  if not name_str:
    return {"error": True, "error_code": "empty_name"}
  return {"stored": True, "value": name_str}
