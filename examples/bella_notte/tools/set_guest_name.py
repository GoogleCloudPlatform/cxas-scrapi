def set_guest_name(name: str) -> dict:
    """Record the guest's name exactly as provided. Accept ANY format (first name, last name, full name, nickname) without asking for clarification. Call immediately when a name is mentioned, even alongside other info in the same message."""
    name = str(name).strip()
    if not name:
        return {
            "error": True,
            "_system_message": "I didn't catch the name. What name should I put the reservation under?"
        }

    sm = context.state['sm']
    sm['filled']['guest_name'] = name
    return {"stored": True, "value": name}
