"""Setter tool for the preferred_date slot."""

import datetime
from typing import Any


def set_preferred_date(date: str) -> dict[str, Any]:
  """Record the preferred date in YYYY-MM-DD format.

  Convert natural language ('this Friday', 'July 4th', 'tomorrow') to
  YYYY-MM-DD. If year is omitted, assume nearest future occurrence. Call
  immediately when a date is mentioned, even alongside other info in the same
  message.

  Args:
    date: Date string in YYYY-MM-DD format.

  Returns:
    Dict with stored=True and value on success, or error=True on failure.
  """
  sm = context.state['sm']  # noqa: F821  # pylint: disable=undefined-variable

  date = str(date).strip()
  try:
    parsed = datetime.datetime.strptime(date, '%Y-%m-%d').date()
  except ValueError:
    sm.setdefault('_slot_errors', []).append(
        {'slot': 'preferred_date', 'code': 'invalid_format'}
    )
    return {'error': True}

  if parsed < datetime.date.today():
    sm.setdefault('_slot_errors', []).append(
        {'slot': 'preferred_date', 'code': 'past_date'}
    )
    return {'error': True}

  sm.setdefault('pending', {})['preferred_date'] = date
  return {'stored': True, 'value': date}
