# pylint: disable=invalid-name,undefined-variable,unused-argument,broad-exception-caught,line-too-long
"""After-tool callback — transfer slot passing.

When set_active_flow is called on the Root Agent, this callback:
1. Pre-fills the specialist's gate slot (active_flow) in the SM.
2. Pre-fills the welcome status to True to skip double greetings.
3. Sets _pending_transfer so before_model can trigger the agent transfer.
"""

from typing import Any, Optional


_SM_KEY = "sm"


def after_tool_callback(
    tool: Tool,
    tool_input: dict[str, Any],
    callback_context: CallbackContext,
    tool_response: dict[str, Any],
) -> Optional[dict[str, Any]]:
  """Capture set_active_flow result and prepare transfer."""
  if tool.name != "set_active_flow":
    return None

  result = tool_response.get("result", tool_response)
  if not result.get("stored"):
    return None

  flow = result["value"]

  # Pre-fill slot filling state machine active_flow gate
  sm = callback_context.state.get(_SM_KEY, {})
  sm.setdefault("filled", {})["active_flow"] = flow
  callback_context.state[_SM_KEY] = sm

  # Set pending target agent
  target = result.get("target_agent")
  if target:
    callback_context.state["_pending_transfer"] = target

  return None
