"""Tool to commit pending slot values after user confirms readback."""

from typing import Any


def confirm_pending() -> dict[str, Any]:
  """Commit all pending slot values to filled.

  Call after guest confirms readback.

  Returns:
    Dict with committed slot names and stored=True, or error=True if empty.
  """
  sm = context.state["sm"]  # noqa: F821  # pylint: disable=undefined-variable
  pending = sm.get("pending", {})
  if not pending:
    return {"error": True}

  committed = list(pending.keys())
  sm["filled"].update(pending)
  sm["pending"] = {}
  sm["_readback_transition"] = True
  return {"committed": committed, "stored": True}
