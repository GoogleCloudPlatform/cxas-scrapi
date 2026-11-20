# pylint: disable=missing-module-docstring,undefined-variable,unused-argument
from typing import Any

DOMAIN_WORKFLOWS = {
    "live_agent": ("live_agent_escalation",),
}


def load_workflow(workflow_name: str, rationale: str) -> dict[str, Any]:
  """Loads the instructions for a workflow in the currently selected domain.

  Args:
    workflow_name (str): Exact name of the workflow to load.
    rationale (str): Why this workflow is being loaded. Kept in the conversation
      history so later turns know what prompted it. One short sentence (max 15
      words) stating the customer's request or the tool result that led here,
      e.g. "Connect the customer with a live representative." Do not restate
      the workflow name or include personal data.

  Returns:
    dict[str, Any]: Status and the loaded workflow, or an error code with the
      required domain or valid workflows.
  """
  domain = context.state.get("current_domain") or ""
  if domain not in DOMAIN_WORKFLOWS:
    return {
        "status": "error",
        "error": "no_selected_domain",
    }

  name = workflow_name.strip()
  if name not in DOMAIN_WORKFLOWS[domain]:
    for other_domain, workflows in DOMAIN_WORKFLOWS.items():
      if name in workflows:
        return {
            "status": "error",
            "error": "workflow_in_different_domain",
            "required_domain": other_domain,
        }
    return {
        "status": "error",
        "error": "invalid_workflow",
        "current_domain": domain,
        "valid_workflows": list(DOMAIN_WORKFLOWS[domain]),
    }

  context.state["current_workflow"] = name
  return {
      "status": "ok",
      "current_domain": domain,
      "current_workflow": name,
  }
