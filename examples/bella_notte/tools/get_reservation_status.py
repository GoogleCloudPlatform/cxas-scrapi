def get_reservation_status() -> dict:
    """Diagnostic snapshot showing filled/missing slots, completed tasks, and next question. Only call if you cannot determine what the user is providing. Do NOT call after a setter tool."""
    sm = context.state['sm']
    user_slots = ['party_size', 'preferred_date', 'selected_time', 'guest_name', 'special_requests']
    missing = [s for s in user_slots if s not in sm['filled']]

    next_q, next_slot = _next_question(sm)
    return {
        "status": sm.get('status', 'in_progress'),
        "filled_slots": dict(sm.get('filled', {})),
        "missing_slots": missing,
        "tasks_completed": list(sm.get('task_results', {}).keys()),
        "next_question": next_q,
        "next_slot": next_slot,
    }


def _next_question(sm: dict) -> tuple:
    filled = sm.get('filled', {})
    order = [
        ("party_size", "How many guests will be dining?"),
        ("preferred_date", "What date would you like to come in?"),
    ]
    if 'available_times' in filled:
        order.append((
            "selected_time",
            f"We have availability at {filled['available_times']}. Which time works best for you?"
        ))
    order += [
        ("guest_name", "What name should I put the reservation under?"),
        ("special_requests", "Do you have any special requests or dietary needs? Just say none if not."),
    ]
    for slot_name, question in order:
        if slot_name not in filled:
            return question, slot_name
    return "All information collected!", None
