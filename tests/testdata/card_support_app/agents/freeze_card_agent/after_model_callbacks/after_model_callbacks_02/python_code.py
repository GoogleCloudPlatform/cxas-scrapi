def after_model_callback(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> Optional[LlmResponse]:
    has_fc = any(p.function_call for p in llm_response.content.parts)
    has_text = any(p.text for p in llm_response.content.parts)
    if has_fc and has_text:
        return LlmResponse.from_parts(
            parts=[p for p in llm_response.content.parts if not p.text]
        )
    return None
