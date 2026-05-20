# pylint: disable=invalid-name,undefined-variable,unused-argument,broad-exception-caught,line-too-long
"""Before-model callback — transfer orchestration.

Hides transfer_to_agent so the LLM uses set_active_flow instead.
When set_active_flow's after_tool_callback sets _pending_transfer,
this callback fires the agent transfer deterministically.
"""

from typing import Optional


def before_model_callback(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> Optional[LlmResponse]:
  """Hide transfer_to_agent; fire pending transfers."""
  llm_request.config.hide_tool("transfer_to_agent")

  agent = callback_context.state.pop("_pending_transfer", "")
  if agent:
    return LlmResponse.from_parts(
        parts=[Part.from_agent_transfer(agent=agent)],
    )

  return {"decision": "OK"}
