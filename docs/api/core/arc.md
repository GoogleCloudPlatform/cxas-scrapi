---
title: Arc
---

# Arc

`Arc` is the Python client for Arc, the AI assistant built into the CX Agent Studio console. It drives an assistant session on an app the way the console does, over the public `v1beta` REST API: streamed turns (`StreamChatAiAssistant`), confirmation-card answers, clarification option panels, client-managed long-running operations (evaluation runs are polled and the session is resumed automatically), session CRUD and session artifacts.

Each turn returns a [`TurnStreamEvents`](#cxas_scrapi.utils.arc_events.TurnStreamEvents) aggregate that you can render as markdown or inspect field by field. Confirmation cards are answered by a [`ConfirmationPolicy`](#cxas_scrapi.utils.arc_confirmation_policy.ConfirmationPolicy) (`manual`, `click`, `mixed`, `scripted`) or explicitly with `send_confirmation_response`. The `cxas arc` CLI ([reference](../../cli/arc.md)) is a thin wrapper around this class.

## Quick Example

```python
from cxas_scrapi.core.arc import Arc
from cxas_scrapi.utils.arc_confirmation_policy import make_policy

arc = Arc(app_name="projects/my-project/locations/us/apps/my-app")
session_id = "arc-sdk-demo"

# Ask a question; cards stay pending with the default manual policy.
events = arc.chat(session_id, "What does this app do? Summarize its agents and tools.")
print(events.format_markdown())

# Build something, clicking through confirmation cards like an attentive user.
events = arc.chat(
    session_id,
    "Add a guardrail that blocks requests for medical advice.",
    confirmation_policy=make_policy("click"),
    timeout_seconds=1800,
)
print(events.text)
for action in events.confirmation_actions:
    print(action.tool, action.action, "typed" if action.typed else "clicked")

# Anything still waiting for a human decision?
for card in events.pending_confirmation_requests:
    print(card.confirmation_id, card.tool, card.context)
    # e.g. arc.send_confirmation_response(session_id, card.confirmation_id, confirmed=True)

# Artifacts recorded on the session (gcsUri pointers), e.g. an exported bundle.
for artifact in arc.session_artifacts(session_id):
    print(artifact.get("displayName"), artifact.get("gcsUri"))
```

Errors surface as `ArcApiError` (HTTP status and decoded message) or `ArcStreamTimeout` (idle stream or turn deadline; `.partial` holds what streamed so far).

## Reference

::: cxas_scrapi.core.arc

::: cxas_scrapi.utils.arc_events.TurnStreamEvents

::: cxas_scrapi.utils.arc_confirmation_policy
    options:
      members:
        - ConfirmationPolicy
        - ManualPolicy
        - ClickPolicy
        - MixedPolicy
        - ScriptedPolicy
        - make_policy
