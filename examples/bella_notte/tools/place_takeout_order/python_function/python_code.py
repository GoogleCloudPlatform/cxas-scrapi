"""Executor tool for the PlaceTakeoutOrder task."""

import hashlib
from typing import Any


def place_takeout_order(
    takeout_items: str,
    pickup_time: str,
    guest_name: str,
    contact_phone: str,
) -> dict[str, Any]:
  """Place the takeout order in the restaurant ordering system.

  Args:
    takeout_items: The items ordered.
    pickup_time: The desired pickup time.
    guest_name: The name on the order.
    contact_phone: The contact phone number.

  Returns:
    Dict with success=True and confirmation_code on success.
  """
  if (
      not takeout_items
      or not pickup_time
      or not guest_name
      or not contact_phone
  ):
    return {"error": True, "error_code": "missing_fields"}

  # Generate a stable confirmation code based on input details
  seed = f"{takeout_items}-{pickup_time}-{guest_name}-{contact_phone}"
  digest = hashlib.md5(seed.encode("utf-8")).hexdigest()
  confirmation_code = f"T-{digest[:6].upper()}"

  return {
      "success": True,
      "confirmation_code": confirmation_code,
  }
