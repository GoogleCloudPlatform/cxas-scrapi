def before_model_callback(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
    for content in llm_request.contents:
        for part in content.parts:
            if part.text and "sys.remote-call-disconnected" in part.text:
                callback_context.state["user_disconnect"] = True
                callback_context.state["call_dropped_by"] = "far_end"
                tools.complete_wrapup_tool(
                    mdn=callback_context.state.get("mdn", "")
                )
    return None
