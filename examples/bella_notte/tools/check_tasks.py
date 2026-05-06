def check_tasks() -> dict:
    """Callback-only: evaluates pending tasks when slots are filled. Not exposed to the LLM."""
    sm = context.state['sm']
    filled = sm.get('filled', {})
    task_results = sm.get('task_results', {})
    fired = []

    if ('party_size' in filled
            and 'preferred_date' in filled
            and 'FindAvailableTimes' not in task_results):
        result = _find_available_times(filled['preferred_date'], filled['party_size'])
        if result.get('success'):
            task_results['FindAvailableTimes'] = result
            filled['available_times'] = result['available_times']
            fired.append('FindAvailableTimes')
        else:
            filled.pop('preferred_date', None)
            return {
                "tasks_fired": fired,
                "error": True,
                "_system_message": (
                    "I'm sorry, we don't have availability for that date and party size. "
                    "Could you try a different date?"
                ),
            }

    required = ['party_size', 'preferred_date', 'selected_time', 'guest_name', 'special_requests']
    if (all(s in filled for s in required)
            and 'BookReservation' not in task_results):
        result = _book_reservation(filled)
        if result.get('success'):
            task_results['BookReservation'] = result
            filled['confirmation_number'] = result['confirmation_number']
            sm['status'] = 'complete'
            fired.append('BookReservation')
            return {
                "tasks_fired": fired,
                "booking_complete": True,
                "confirmation_number": result['confirmation_number'],
                "_system_message": (
                    f"Wonderful! Your reservation is confirmed. "
                    f"Your confirmation number is {result['confirmation_number']}. "
                    f"We look forward to welcoming you to Bella Notte!"
                ),
            }
        else:
            retry_count = sm.get('_retries', {}).get('BookReservation', 0) + 1
            sm.setdefault('_retries', {})['BookReservation'] = retry_count
            if retry_count < 2:
                return {
                    "tasks_fired": fired,
                    "error": True,
                    "_system_message": (
                        "I'm having a bit of trouble completing your reservation. "
                        "Let me try once more."
                    ),
                }
            else:
                sm['status'] = 'escalated'
                return {
                    "tasks_fired": fired,
                    "error": True,
                    "escalate": True,
                    "_system_message": (
                        "I'm sorry, I wasn't able to complete your reservation. "
                        "Please call us directly at 555-0100 and we'll get you sorted."
                    ),
                }

    next_q, next_slot = _next_question(sm)
    return {
        "tasks_fired": fired,
        "next_question": next_q,
        "next_slot": next_slot,
        "_system_message": next_q,
    }


def _find_available_times(date: str, party_size: int) -> dict:
    schedule = {
        2: ["6:00 PM", "7:30 PM", "9:00 PM"],
        4: ["7:00 PM", "8:30 PM"],
        6: ["6:00 PM"],
    }
    times = schedule.get(int(party_size), ["6:00 PM", "7:30 PM", "9:00 PM"])
    return {"available_times": ", ".join(times), "success": True}


def _book_reservation(slots: dict) -> dict:
    conf = f"BN-{abs(hash(slots['preferred_date'] + slots['selected_time'] + slots['guest_name'])) % 10000:04d}"
    return {"confirmation_number": conf, "success": True}


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
