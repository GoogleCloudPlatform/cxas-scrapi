# pylint: disable=missing-module-docstring,undefined-variable,unused-argument
from typing import Any

VALID_PLAYBOOKS = (
    "order_support",
    "returns",
)


def open_playbook(playbook_name: str, rationale: str) -> dict[str, Any]:
  """Opens the playbook that covers the customer's request.

  Args:
    playbook_name (str): Exact name of the playbook to open, from the playbook
      list.
    rationale (str): Why this playbook is being opened. Kept in the conversation
      history so later turns know what prompted it. One short sentence (max 15
      words) stating the customer's need or the tool result that led here, e.g.
      "Customer wants to know when their order will arrive." Do not restate the
      playbook name or include personal data.

  Returns:
    dict[str, Any]: Status and the opened playbook, or valid_playbooks on an
      unknown playbook name.
  """
  name = playbook_name.strip()
  if name not in VALID_PLAYBOOKS:
    return {
        "status": "error",
        "error": "invalid_playbook",
        "valid_playbooks": list(VALID_PLAYBOOKS),
    }

  context.state["current_playbook"] = name
  context.state["current_task"] = ""
  return {
      "status": "ok",
      "current_playbook": name,
  }
