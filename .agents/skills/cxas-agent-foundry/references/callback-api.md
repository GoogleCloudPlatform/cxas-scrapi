# Callback API Reference

The GECX sandbox auto-provides these globals. Do NOT import them. Use ONLY the methods listed here -- do not guess at additional attributes or constructors.

## Part

**Creating Parts** -- always use factory methods, never raw constructors:

```python
# Text
Part.from_text(text="Hello, how can I help?")

# Tool/function call
Part.from_function_call(name="end_session", args={"session_escalated": True})
Part.from_function_call(name="lookup_benefits", args={"member_id": "H123", "plan_id": "PLN-001"})
```

**Reading Parts:**

```python
part.text                        # str | None -- text content (text mode only)
part.text_or_transcript()        # str | None -- text OR audio transcript (use this for audio-safe detection)
part.has_function_call("end_session")  # bool -- check if this part is a specific function call
part.function_call               # object | None -- the function call (has .name, .args)
part.function_response           # object | None -- the function response
```

**DO NOT USE:**
- `Part(text=...)` -- use `Part.from_text()` instead
- `part.custom_metadata` -- does not exist
- `part.inline_data` -- internal, use `text_or_transcript()` instead

## Content

```python
content.parts    # list[Part] -- the parts in this content
content.role     # str -- "model" or "user"
```

**DO NOT USE:**
- `Content(parts=[...])` -- use `LlmResponse.from_parts()` to build responses

## LlmResponse

**Creating responses** (in before_model callbacks):

```python
# Return a deterministic response with text + tool calls
LlmResponse.from_parts(parts=[
    Part.from_text(text="Let me connect you with someone."),
    Part.from_function_call(name="end_session", args={"session_escalated": True}),
])
```

**Reading responses** (in after_model callbacks):

```python
llm_response.content        # Content -- the response content
llm_response.content.parts  # list[Part] -- iterate over parts
```

**DO NOT USE:**
- `LlmResponse(content=Content(parts=[...]))` -- use `LlmResponse.from_parts()` instead

## LlmRequest

**Reading requests** (in before_model callbacks):

```python
llm_request.contents    # list[Content] -- full conversation history
llm_request.contents[-1]  # Content -- last message (usually user input)
```

## CallbackContext

```python
callback_context.state              # dict -- session state (read/write)
callback_context.events             # list -- full session event history
callback_context.get_last_user_input()  # list[Part] -- parts from last user message
```

**Events** (for walking session history):

```python
for event in reversed(callback_context.events):
    event.is_user()    # bool -- is this a user event?
    event.is_agent()   # bool -- is this an agent event?
    event.parts()      # list[Part] -- parts in this event
```

## Common Patterns

### Detect session start

```python
for part in callback_context.get_last_user_input():
    if part.text == "<event>session start</event>":
        # First model call -- return greeting
```

### Detect silence / no-input

```python
import re

def is_user_inactive(contents: list) -> bool:
    pattern = r"<context>no user activity detected for \d+ seconds\.</context>"
    return len(contents) > 1 and any(
        re.search(pattern, p.text, re.IGNORECASE)
        for p in contents[-1].parts
        if p.text
    )
```

### Check if response has text (audio-safe)

```python
has_text = False
for part in llm_response.content.parts:
    content = part.text_or_transcript()
    if content and len(content.strip()) > 0:
        has_text = True
```

### Check for end_session in response

```python
has_end_session = any(
    part.has_function_call("end_session")
    for part in llm_response.content.parts
)
```

## Session State

- All values are **strings** -- GECX state only supports string values
- Booleans: use `"true"` / `"false"`, check with `str(state.get("flag", "false")).lower() == "true"`
- Counters: use `str(int(state.get("counter", "0")) + 1)`
- Access via `callback_context.state` in callbacks, `context.state` in tools
