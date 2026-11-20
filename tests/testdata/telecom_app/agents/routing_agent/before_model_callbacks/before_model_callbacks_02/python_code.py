def before_model_callback(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
    if callback_context.state.get("user_disconnect"):
        callback_context.state["disconnect_logged"] = True
    return None
