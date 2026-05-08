"""Setter tool for the special_requests slot."""

from typing import Any


def set_special_requests(requests: str) -> dict[str, Any]:
  """Record special requests, dietary needs, or seating preferences.

  If the guest says 'none' or equivalent, pass that text as-is. Call immediately
  when the user responds about special requests, even if the answer is 'no' or
  'nothing'.

  Args:
    requests: Special requests text from the guest.

  Returns:
    Dict with stored=True and value on success.
  """
  requests = str(requests).strip()
  sm = context.state['sm']  # noqa: F821  # pylint: disable=undefined-variable
  sm.setdefault('pending', {})['special_requests'] = requests
  return {'stored': True, 'value': requests}
