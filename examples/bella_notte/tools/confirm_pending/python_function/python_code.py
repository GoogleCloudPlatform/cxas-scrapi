"""Tool to commit pending slot values after user confirms readback.

FRAMEWORK CODE — shared across all agents using the slot-filling engine.
Do not add agent-specific logic here; customize behavior
via the per-agent {config_id}_dag tool.
"""

from typing import Any


def confirm_pending() -> dict[str, Any]:
  """Commit all pending slot values to filled.

  Call when the user affirms the readback is correct.

  Returns:
    Dict with committed slot names and stored=True, or error=True if empty.
  """
  sm = context.state["sm"]  # pylint: disable=undefined-variable
  pending = sm.get("pending", {})
  if not pending:
    return {"error": True}

  committed = list(pending.keys())
  sm["filled"].update(pending)
  sm["pending"] = {}
  sm["_readback_transition"] = True
  return {"committed": committed, "stored": True}
