"""Setter tool for the selected_time slot."""

from typing import Any


def set_selected_time(time: str) -> dict[str, Any]:
  """Record the guest's chosen time in HH:MM 24-hour format.

  Converts natural language like '6 PM' to '18:00', '7:30 PM' to '19:30'.

  Args:
    time: Time string in HH:MM 24-hour format.

  Returns:
    Dict with stored=True and value on success, or error=True on failure.
  """
  sm = context.state['sm']  # noqa: F821  # pylint: disable=undefined-variable

  if 'available_times' not in sm['filled']:
    sm.setdefault('_slot_errors', []).append(
        {'slot': 'selected_time', 'code': 'prereq_not_met'}
    )
    return {'error': True}

  time = str(time).strip()

  # Convert 24h input to 12h and validate against available options.
  try:
    parts = time.split(':')
    h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    period = 'AM' if h < 12 else 'PM'
    h12 = h % 12 or 12
    time_12h = f'{h12}:{m:02d} {period}'
  except (ValueError, IndexError):
    sm.setdefault('_slot_errors', []).append(
        {'slot': 'selected_time', 'code': 'not_available'}
    )
    return {'error': True}

  available = [t.strip() for t in sm['filled']['available_times'].split(',')]
  if time_12h not in available:
    sm.setdefault('_slot_errors', []).append(
        {'slot': 'selected_time', 'code': 'not_available'}
    )
    return {'error': True}

  sm.setdefault('pending', {})['selected_time'] = time
  return {'stored': True, 'value': time}
