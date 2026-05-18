"""Multi-slot setter for party_size and preferred_date."""

import datetime
from typing import Any


def set_reservation_basics(
    party_size: int = 0,
    preferred_date: str = "",
) -> dict[str, Any]:
  """Record party size and/or preferred date for the reservation.

  Call this tool whenever the guest mentions how many people OR a date, even
  if only one value is provided. Parse natural language for party size
  ('just me'=1, 'a couple'=2, 'four of us'=4) and dates ('this Friday',
  'July 4th', 'tomorrow' -> YYYY-MM-DD). Call immediately, even alongside
  other info in the same message.

  Args:
    party_size: Number of guests (1-8). Pass 0 to omit.
    preferred_date: Date in YYYY-MM-DD format. Pass empty string to omit.

  Returns:
    Dict with stored=True and per-field values/errors.
  """
  values = {}
  field_errors = {}

  if party_size:
    try:
      size = int(party_size)
    except (ValueError, TypeError):
      field_errors["party_size"] = "parse_error"
      size = None
    if size is not None:
      if not (1 <= size <= 8):
        field_errors["party_size"] = "out_of_range"
      else:
        values["party_size"] = size

  if preferred_date:
    date_str = str(preferred_date).strip()
    try:
      parsed = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
      field_errors["preferred_date"] = "invalid_format"
      parsed = None
    if parsed is not None:
      if parsed < datetime.date.today():
        field_errors["preferred_date"] = "past_date"
      else:
        values["preferred_date"] = date_str

  if not values and not field_errors:
    return {"error": True, "error_code": "no_input"}

  result = {"stored": True, "values": values}
  if field_errors:
    result["field_errors"] = field_errors
  return result
