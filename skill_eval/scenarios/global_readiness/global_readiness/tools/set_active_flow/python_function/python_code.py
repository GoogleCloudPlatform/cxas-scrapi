"""Activate a structured slot-filling flow.

Setter tool for the active_flow slot. When called, the routing engine
activates the corresponding conditional slots and begins deterministic
collection on the specialist agent.
"""

from typing import Any

_VALID_FLOWS = {"flow_es"}

_FLOW_TO_AGENT = {
    "flow_es": "flow_es",
}


def set_active_flow(flow: str) -> dict[str, Any]:
    """Activate a structured flow and route to the right specialist.

    Args:
      flow: The flow to activate — e.g., 'flow_es'.

    Returns:
      Dict with stored=True and value on success, or agent_action with error on failure.
    """
    flow = str(flow).lower().strip()
    if flow not in _VALID_FLOWS:
        return {"agent_action": {"error": f"Invalid flow: {flow}"}}

    result = {"stored": True, "value": flow}
    target = _FLOW_TO_AGENT.get(flow)
    if target:
        result["target_agent"] = target
    return result
