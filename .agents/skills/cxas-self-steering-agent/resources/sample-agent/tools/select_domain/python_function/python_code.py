# pylint: disable=missing-module-docstring,undefined-variable,unused-argument
from typing import Any

VALID_DOMAINS = ("live_agent",)


def select_domain(domain_name: str, rationale: str) -> dict[str, Any]:
  """Selects the domain that covers the customer's request.

  Args:
    domain_name (str): Exact name of the domain to select, from the domain
      list.
    rationale (str): Why this domain is being selected. Kept in the conversation
      history so later turns know what prompted it. One short sentence (max 15
      words) stating the customer's need or the tool result that led here, e.g.
      "Customer asked to speak with a representative." Do not restate the
      domain name or include personal data.

  Returns:
    dict[str, Any]: Status and the selected domain, or valid_domains on an
      unknown domain name.
  """
  name = domain_name.strip()
  if name not in VALID_DOMAINS:
    return {
        "status": "error",
        "error": "invalid_domain",
        "valid_domains": list(VALID_DOMAINS),
    }

  context.state["current_domain"] = name
  context.state["current_workflow"] = ""
  return {
      "status": "ok",
      "current_domain": name,
  }
