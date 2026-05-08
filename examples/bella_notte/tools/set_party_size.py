"""Setter tool for the party_size slot."""

from typing import Any


def set_party_size(size: int) -> dict[str, Any]:
  """Record the number of guests.

  Parse natural language: 'just me'=1, 'a couple'=2, 'four of us'=4. Call
  immediately when party size is mentioned, even alongside other info in the
  same message.

  Args:
    size: Number of guests for the reservation.

  Returns:
    Dict with stored=True and value on success, or error=True on failure.
  """
  sm = context.state["sm"]  # noqa: F821  # pylint: disable=undefined-variable

  if not isinstance(size, int):
    try:
      size = int(size)
    except (ValueError, TypeError):
      sm.setdefault("_slot_errors", []).append(
          {"slot": "party_size", "code": "parse_error"}
      )
      return {"error": True}

  if not (1 <= size <= 8):
    sm.setdefault("_slot_errors", []).append(
        {"slot": "party_size", "code": "out_of_range"}
    )
    return {"error": True}

  sm.setdefault("pending", {})["party_size"] = size
  return {"stored": True, "value": size}
