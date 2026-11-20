def before_agent_callback(callback_context: CallbackContext) -> Optional[Content]:
    caller_data = callback_context.state.get("caller_data", {})
    active_group = caller_data.get("intent", {}).get("active_group")
    group_list = caller_data.get("authentication", {}).get("groups", [])
    accounts = []
    for grp in group_list:
        if grp.get("id") == active_group:
            for prod in grp.get("products", []):
                accounts.append(dict(prod))
    shared_payload = {"all_accounts": accounts}
    callback_context.state["budget_all_accounts"] = shared_payload
    callback_context.state["budget_account_identifiers"] = shared_payload
    if accounts:
        callback_context.state["budget_current_account"] = accounts[0]
    return None
