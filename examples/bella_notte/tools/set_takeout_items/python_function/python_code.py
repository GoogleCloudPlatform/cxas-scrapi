"""Setter tool for takeout_items slot."""

from typing import Any


def set_takeout_items(items: str) -> dict[str, Any]:
  """Record the items the guest wants to order for takeout.

  Args:
    items: The food items they want to order.

  Returns:
    Dict with stored=True and value on success, or error on failure.
  """
  items_str = str(items).strip()
  if not items_str:
    return {"error": True, "error_code": "empty_items"}
  return {"stored": True, "value": items_str}
