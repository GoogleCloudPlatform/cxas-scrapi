def set_special_requests(requests: str) -> dict:
    """Record special requests, dietary needs, or seating preferences. If the guest says 'none' or equivalent, pass that text as-is. Call immediately when the user responds about special requests, even if the answer is 'no' or 'nothing'."""
    requests = str(requests).strip()
    sm = context.state['sm']
    sm['filled']['special_requests'] = requests
    return {"stored": True, "value": requests}
