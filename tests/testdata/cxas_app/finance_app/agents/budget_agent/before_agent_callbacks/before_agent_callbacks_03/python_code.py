def before_agent_callback(callback_context: CallbackContext) -> Optional[Content]:
    stack = callback_context.state["complaint_agent_execution_stack"]
    if isinstance(stack, list) and "budget_agent" in stack:
        stack.remove("budget_agent")
    return None
