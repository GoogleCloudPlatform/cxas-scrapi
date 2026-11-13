def after_model_callback(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> Optional[LlmResponse]:
    raise RuntimeError("Disabled callback 03 should never execute")
