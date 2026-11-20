# pylint: disable=missing-module-docstring,undefined-variable,unused-argument
from typing import Any

PLAYBOOK_TASKS = {
    "live_agent": ("live_agent_escalation",),
}


def load_task(task_name: str, rationale: str) -> dict[str, Any]:
  """Loads the instructions for a task in the currently open playbook.

  Args:
    task_name (str): Exact name of the task to load.
    rationale (str): Why this task is being loaded. Kept in the conversation
      history so later turns know what prompted it. One short sentence (max 15
      words) stating the customer's request or the tool result that led here,
      e.g. "Customer asked to speak with a human representative." Do not restate
      the task name or include personal data.

  Returns:
    dict[str, Any]: Status and loaded task, or error details on mismatch.
  """
  playbook = context.state.get("current_playbook") or ""
  if playbook not in PLAYBOOK_TASKS:
    return {"status": "error", "error": "no_active_playbook"}

  name = task_name.strip()
  if name not in PLAYBOOK_TASKS[playbook]:
    for other_playbook, tasks in PLAYBOOK_TASKS.items():
      if name in tasks:
        return {
            "status": "error",
            "error": "task_in_different_playbook",
            "task": name,
            "playbook": other_playbook,
        }
    return {
        "status": "error",
        "error": "invalid_task",
        "current_playbook": playbook,
        "valid_tasks": list(PLAYBOOK_TASKS[playbook]),
    }

  context.state["current_task"] = name
  return {
      "status": "ok",
      "current_playbook": playbook,
      "current_task": name,
  }
