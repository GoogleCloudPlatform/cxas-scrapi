def after_model_callback(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> Optional[LlmResponse]:
    btx_map = callback_context.state.get("auth_btx_list", {}).get("budget_agent", {})
    for part in llm_response.content.parts:
        if part.function_call and part.function_call.name in btx_map:
            required_btx = btx_map[part.function_call.name]
            auth_ok = tools.check_auth(btx_list=required_btx).json().get("result", False)
            if auth_ok:
                callback_context.state["auth_private"] = {"agent_to_authenticate": ""}
    return None
