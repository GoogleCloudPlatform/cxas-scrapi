def set_preferred_date(date: str) -> dict:
    """Record the preferred date in YYYY-MM-DD format. Convert natural language ('this Friday', 'July 4th', 'tomorrow') to YYYY-MM-DD. If year is omitted, assume nearest future occurrence. Call immediately when a date is mentioned, even alongside other info in the same message."""
    import re
    date = str(date).strip()
    if not re.match(r'\d{4}-\d{2}-\d{2}$', date):
        return {
            "error": True,
            "_system_message": "Could you provide the date? For example, 2026-06-17 for June 17th."
        }

    sm = context.state['sm']
    sm['filled']['preferred_date'] = date
    return {"stored": True, "value": date}
