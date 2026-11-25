# pylint: disable=missing-module-docstring,undefined-variable
from typing import Any, Optional


def set_next_agent_actions(
    customized_response: Optional[dict[str, Any]] = None,
    end_session: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
  """Compatibility helper that queues next_agent_actions in session state.

  Tools should normally write context.state["next_agent_actions"] directly;
  this helper is retained for tools that call tools.set_next_agent_actions.

  Args:
    customized_response (Optional[dict[str, Any]]): Arguments for
      Part.from_customized_response to play on the next model call.
    end_session (Optional[dict[str, Any]]): Arguments for Part.from_end_session
      to play on the next model call.

  Returns:
    dict[str, Any]: Queueing status.
  """
  actions: dict[str, Any] = {}
  if customized_response:
    actions["customized_response"] = customized_response
  if end_session:
    actions["end_session"] = end_session
  context.state["next_agent_actions"] = actions
  return {"status": "queued"}
