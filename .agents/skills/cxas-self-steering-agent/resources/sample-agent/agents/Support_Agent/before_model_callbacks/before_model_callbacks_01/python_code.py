# pylint: disable=missing-module-docstring,undefined-variable,unused-argument
import re
from typing import Optional

GREETING = (
    "Hi, thanks for contacting customer support. How can I help you today?"
)


def before_model_callback(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
  """Runs the handlers in order and returns the first response one of them builds.

  Args:
    callback_context: The session state and other context CES passes in.
    llm_request: The request CES is about to send to the model.

  Returns:
    The first response a handler builds, which CES uses instead of calling the
    model, or None to call the model with only the tools the rendered
    instruction mentions.
  """
  for handler in (
      _handle_next_agent_actions,
      _handle_initial_greeting,
      _handle_dynamic_tool_hiding,
  ):
    if (response := handler(callback_context, llm_request)) is not None:
      return response
  return None


def _handle_next_agent_actions(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
  """Plays what a tool queued with set_next_agent_actions, without calling the model."""
  actions = callback_context.state.pop("next_agent_actions", None)
  if not actions:
    return None

  parts = []
  if response := actions.get("customized_response"):
    parts.append(Part.from_customized_response(**response))
  if end_session := actions.get("end_session"):
    # Part.from_end_session takes reason and escalated (not session_escalated).
    parts.append(Part.from_end_session(**end_session))
  return LlmResponse.from_parts(parts=parts)


def _handle_initial_greeting(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
  """Sends the greeting on the first model call of the conversation."""
  if callback_context.state.get("played_greeting"):
    return None
  callback_context.state["played_greeting"] = True
  return LlmResponse.from_parts(
      parts=[Part.from_customized_response(content=GREETING)]
  )


def _handle_dynamic_tool_hiding(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
  """Hides every tool that the rendered instruction doesn't mention."""
  config = llm_request.config
  if not (
      config
      and config.tools
      and config.system_instruction
      and config.system_instruction.parts
  ):
    return None

  instruction = config.system_instruction.parts[0].text
  for func_decl in config.tools[0].function_declarations:
    if re.search(rf"\b{re.escape(func_decl.name)}\b", instruction):
      continue
    if (
        config.excluded_tools is None
        or func_decl.name not in config.excluded_tools
    ):
      config.hide_tool(func_decl.name)
  return None
