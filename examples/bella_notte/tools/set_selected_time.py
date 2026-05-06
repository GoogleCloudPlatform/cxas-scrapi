def set_selected_time(time: str) -> dict:
    """Record the guest's chosen time in HH:MM 24-hour format ('6 PM'→'18:00', '7:30 PM'→'19:30', '9 PM'→'21:00'). Only valid after available times have been presented to the guest."""
    sm = context.state['sm']

    if 'available_times' not in sm['filled']:
        return {
            "error": True,
            "_system_message": "I'd love to get you that time! I just need to check availability first. How many guests will be joining us, and what date works best for you?"
        }

    time = str(time).strip()
    sm['filled']['selected_time'] = time
    return {"stored": True, "value": time}
