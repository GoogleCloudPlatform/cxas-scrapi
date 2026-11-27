def after_model_callback(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> Optional[LlmResponse]:
    if not callback_context.state.get("freeze_card_tool_final_boolean"):
        return None
    payload = callback_context.state.get("payload_for_successful_freeze", {})
    new_parts = []
    rewritten = False
    for part in llm_response.content.parts:
        if (
            part.function_call
            and part.function_call.name == "client_function_final_information"
        ):
            new_parts.append(
                Part.from_function_call(
                    "client_function_final_information", dict(payload)
                )
            )
            rewritten = True
        else:
            new_parts.append(part)
    if rewritten:
        return LlmResponse.from_parts(parts=new_parts)
    return None
