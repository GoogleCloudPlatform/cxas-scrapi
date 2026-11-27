def before_agent_callback(callback_context: CallbackContext) -> Optional[Content]:
    identifiers = callback_context.state.get("budget_account_identifiers", {})
    for account in identifiers.get("all_accounts", []):
        account.pop("product_id", None)
    return None
