def before_model_callback(callback_context, llm_request) -> dict:
    """Evaluates the slot filling DAG after tool execution. Fires ready tasks and computes the next question."""
    sm = callback_context.state.get('sm', {})
    filled = sm.get('filled', {})
    task_results = sm.get('task_results', {})

    task_fired = False
    system_msg = None

    if 'party_size' in filled and 'preferred_date' in filled and 'FindAvailableTimes' not in task_results:
        schedule = {
            2: ["6:00 PM", "7:30 PM", "9:00 PM"],
            4: ["7:00 PM", "8:30 PM"],
            6: ["6:00 PM"],
        }
        times = schedule.get(int(filled['party_size']), ["6:00 PM", "7:30 PM", "9:00 PM"])
        task_results['FindAvailableTimes'] = {"available_times": ", ".join(times), "success": True}
        filled['available_times'] = ", ".join(times)
        task_fired = True
        system_msg = f"Great choice! We have availability at {filled['available_times']}. Which time works best for you?"

    required = ['party_size', 'preferred_date', 'selected_time', 'guest_name', 'special_requests']
    if all(s in filled for s in required) and 'BookReservation' not in task_results:
        hash_input = filled['preferred_date'] + filled['selected_time'] + filled['guest_name']
        conf = f"BN-{abs(hash(hash_input)) % 10000:04d}"
        task_results['BookReservation'] = {"confirmation_number": conf, "success": True}
        filled['confirmation_number'] = conf
        sm['status'] = 'complete'
        task_fired = True
        system_msg = f"Wonderful! Your reservation is confirmed. Your confirmation number is {conf}. We look forward to welcoming you to Bella Notte!"

    if not task_fired:
        order = [
            ("party_size", "How many guests will be dining?"),
            ("preferred_date", "What date would you like to come in?"),
        ]
        if 'available_times' in filled:
            order.append(("selected_time", f"We have availability at {filled['available_times']}. Which time works best for you?"))
        order += [
            ("guest_name", "What name should I put the reservation under?"),
            ("special_requests", "Do you have any special requests or dietary needs? Just say none if not."),
        ]
        for slot_name, question in order:
            if slot_name not in filled:
                system_msg = question
                break

    sm['_system_message'] = system_msg

    if task_fired and llm_request.contents and len(llm_request.contents) > 1:
        return LlmResponse.from_parts(parts=[
            Part.from_text(text=system_msg),
        ])

    return {'decision': 'OK', 'reason': 'ok'}
