# pylint: disable=missing-module-docstring,undefined-variable
from typing import Any, Optional


def set_next_agent_actions(
    customized_response: Optional[dict[str, Any]] = None,
    end_session: Optional[dict[str, Any]] = None,
) -> None:
  """Queues the agent's next reply and actions so they play without calling the model."""
  actions = context.state["next_agent_actions"] = {}
  if customized_response:
    actions["customized_response"] = customized_response
  if end_session:
    actions["end_session"] = end_session
