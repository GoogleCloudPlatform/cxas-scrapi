def set_party_size(size: int) -> dict:
    """Record the number of guests (integer, 1-8). Parse natural language: 'just me'=1, 'a couple'=2, 'four of us'=4. Call immediately when party size is mentioned, even alongside other info in the same message."""
    if not isinstance(size, int):
        try:
            size = int(size)
        except (ValueError, TypeError):
            return {
                "error": True,
                "_system_message": "I didn't catch the number of guests. How many will be dining?"
            }

    if not (1 <= size <= 8):
        return {
            "error": True,
            "_system_message": (
                "I'm sorry, we accept reservations for parties of 1 to 8. "
                "For larger parties, please contact our events team at events@bellanotte.com."
            )
        }

    sm = context.state['sm']
    sm['filled']['party_size'] = size
    return {"stored": True, "value": size}
