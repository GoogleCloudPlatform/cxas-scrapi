# pylint: disable=missing-module-docstring,undefined-variable,unused-argument
from typing import Any

PLAYBOOK_TASKS = {
    "order_support": (
        "check_order_status",
        "cancel_order",
        "text_order_summary",
    ),
    "returns": ("start_return",),
    "live_agent": ("live_agent_escalation",),
}


def load_task(task_name: str, rationale: str) -> dict[str, Any]:
  """Loads the instructions for a task in the currently open playbook.

  Args:
    task_name (str): Exact name of the task to load.
    rationale (str): Why this task is being loaded. Kept in the conversation
      history so later turns know what prompted it. One short sentence (max 15
      words) stating the customer's request or the tool result that led here,
      e.g. "Customer changed their mind and wants to cancel." Do not restate the
      task name or include personal data.

  Returns:
    dict[str, Any]: Success status and the loaded task.
  """
  playbook = context.state.get("current_playbook") or ""
  if playbook not in PLAYBOOK_TASKS:
    return {
        "error": "NO_ACTIVE_PLAYBOOK",
        "message": "No playbook is open. Call open_playbook first.",
    }

  name = task_name.strip()
  if name not in PLAYBOOK_TASKS[playbook]:
    for other_playbook, tasks in PLAYBOOK_TASKS.items():
      if name in tasks:
        return {
            "error": "TASK_IN_DIFFERENT_PLAYBOOK",
            "message": (
                f"Task '{name}' is in the '{other_playbook}' playbook. Call"
                f" open_playbook with '{other_playbook}' first."
            ),
        }
    return {
        "error": "INVALID_TASK",
        "message": (
            f"Invalid task name '{name}'. Valid tasks in '{playbook}' are:"
            f" {list(PLAYBOOK_TASKS[playbook])}."
        ),
    }

  context.state["current_task"] = name
  return {
      "success": True,
      "current_task": name,
      "playbook": playbook,
      "message": f"Loaded task '{name}'.",
  }
