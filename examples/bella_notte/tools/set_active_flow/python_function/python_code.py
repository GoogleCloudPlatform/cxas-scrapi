"""Activate a structured slot-filling flow.

Setter tool for the active_flow slot. When called, the routing engine
activates the corresponding conditional slots and begins deterministic
collection on the specialist agent.
"""

from typing import Any


_VALID_FLOWS = {"reservation", "takeout"}

_FLOW_TO_AGENT = {
    "reservation": "Reservation_Agent",
    "takeout": "Takeout_Agent",
}


def set_active_flow(flow: str) -> dict[str, Any]:
  """Activate a structured flow and route to the right specialist.

  Args:
    flow: The flow to activate — 'reservation' or 'takeout'.

  Returns:
    Dict with stored=True and value on success, or error=True on failure.
  """
  flow = str(flow).lower().strip()
  if flow not in _VALID_FLOWS:
    return {"error": True, "error_code": "invalid_flow"}
  result = {"stored": True, "value": flow}
  target = _FLOW_TO_AGENT.get(flow)
  if target:
    result["target_agent"] = target
  return result
