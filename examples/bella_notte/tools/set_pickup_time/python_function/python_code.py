"""Setter tool for pickup_time slot."""

from typing import Any


def set_pickup_time(time: str) -> dict[str, Any]:
  """Record the pickup time for the takeout order.

  Args:
    time: The pickup time (e.g., '6:30 PM', '19:00').

  Returns:
    Dict with stored=True and value on success, or error on failure.
  """
  time_str = str(time).strip()
  if not time_str:
    return {"error": True, "error_code": "empty_time"}
  return {"stored": True, "value": time_str}
