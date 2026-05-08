"""Setter tool for the guest_name slot."""

from typing import Any


def set_guest_name(name: str) -> dict[str, Any]:
  """Record the guest's name exactly as provided.

  Accept ANY format (first name, last name, full name, nickname) without asking
  for clarification. Call immediately when a name is mentioned, even alongside
  other info in the same message.

  Args:
    name: Guest name string.

  Returns:
    Dict with stored=True and value on success, or error=True on failure.
  """
  sm = context.state['sm']  # noqa: F821  # pylint: disable=undefined-variable

  name = str(name).strip()
  if not name:
    sm.setdefault('_slot_errors', []).append(
        {'slot': 'guest_name', 'code': 'empty_name'}
    )
    return {'error': True}

  sm.setdefault('pending', {})['guest_name'] = name
  return {'stored': True, 'value': name}
