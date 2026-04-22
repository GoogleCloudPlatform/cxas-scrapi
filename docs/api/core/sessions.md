---
title: Sessions
---

# Sessions

`Sessions` is how you actually *talk* to a CXAS agent. You can send text, events, DTMF digits, raw audio bytes, or multimodal blobs — and get back the agent's response in a structured object you can inspect or render.

The class supports two modalities via the `Modality` enum:

- **`Modality.TEXT`** (default) — synchronous request/response over gRPC. Perfect for evals, notebooks, and scripted tests.
- **`Modality.AUDIO`** — asynchronous bidirectional streaming over a WebSocket (`BidiRunSession`). This is what you use for phone or voice integrations.

`Sessions` also has a handy `parse_result()` method that pretty-prints the full conversation trace — tool calls, transfers, custom payloads — in the terminal or as colorful HTML in a Jupyter notebook.

## Quick Example

```python
from cxas_scrapi import Sessions

app_name = "projects/my-project/locations/us/apps/my-app-id"

sessions = Sessions(app_name=app_name)

# Start a conversation
session_id = sessions.create_session_id()

# Text turn
response = sessions.run(
    session_id=session_id,
    text="I need help with my bill",
)
sessions.parse_result(response)

# Send an event (e.g., a welcome trigger)
sessions.run(
    session_id=session_id,
    event="WELCOME",
    event_vars={"caller_id": "+14155551234"},
)

# Audio turn — convert text to speech automatically
audio_response = sessions.run(
    session_id=session_id,
    text="Tell me my account balance",
    modality="audio",
)
```

## Reference

::: cxas_scrapi.core.sessions.Sessions
    options:
      members:
        - __init__
        - create_session_id
        - run
        - parse_result
        - send_event
        - async_bidi_run_session
        - make_text_request
        - get_file_data

::: cxas_scrapi.core.sessions.Modality
