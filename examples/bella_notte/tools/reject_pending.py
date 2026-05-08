"""Tool to discard pending slot values after user rejects readback."""

from typing import Any


def reject_pending() -> dict[str, Any]:
  """Discard all pending slot values.

  Call when guest says the readback is wrong.

  Returns:
    Dict with discarded slot names and stored=True.
  """
  sm = context.state['sm']  # noqa: F821  # pylint: disable=undefined-variable
  discarded = list(sm.get('pending', {}).keys())
  sm['pending'] = {}
  return {'discarded': discarded, 'stored': True}
