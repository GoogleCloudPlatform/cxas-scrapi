# Callback API Reference

The GECX sandbox auto-provides these globals. Do NOT import them. Use ONLY the methods listed here -- do not guess at additional attributes or constructors.

`typing` types (`Optional`, `Iterator`, `List`, `Dict`, etc.) are NOT auto-provided — they must be explicitly imported. Missing typing imports cause errors at push time.

## Part

**Creating Parts** -- always use factory methods, never raw constructors:

```python
# Text (NOTE: Part.from_text takes ONLY `text: str` -- NEVER pass `partial=True`!)
Part.from_text(text="Hello, how can I help?")

# Customized speech / DTMF / barge-in control (ideal for greetings & legal disclaimers)
Part.from_customized_response(
    content="Hi, thanks for calling Acme Support. How can I help you today?",
    disable_barge_in=False,
)

# Terminal session end / escalation (keyword-only `reason` and `escalated` -- NOT `session_escalated`!)
Part.from_end_session(reason="user_requested_human", escalated=True)
Part.from_end_session(reason="resolved", escalated=False)

# Deterministic sub-agent transfer (uses directory/resource name)
Part.from_agent_transfer(agent="billing_agent")

# Tool/function call (target Python tool MUST have `-> dict` return annotation!)
Part.from_function_call(name="lookup_benefits", args={"member_id": "H123", "plan_id": "PLN-001"})

# Pre-recorded GCS audio or JSON UI payload
Part.from_audio(audio_uri="gs://my-bucket/hold.wav", cancellable=False, interruptable=True)
Part.from_json(data='{"card": "summary"}')
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
# 1. Deterministic Preemption (Bypasses LLM completely, speaks text, waits for user):
LlmResponse.from_parts(parts=[
    Part.from_text(text="What is your account number?"),
])

# 2. Tool Delegation with Audio Filler (Runs tool, feeds toolResponse to LLM, triggers LLM reasoning):
# IMPORTANT: Do NOT pass partial=True to Part.from_text, and NEVER set response.partial = True
# when parts contains a function_call (silently drops the tool call!) or inline audio tags.
LlmResponse.from_parts(parts=[
    Part.from_text(text="Let me look that up for you."),
    Part.from_function_call(name="lookup_account", args={"id": "123"}),
])

# 3. Silent Close on a Caller Turn (e.g., after Two-Part Reply already spoke the answer):
# MUST return Part.from_text(text="") -- NEVER return parts=[] on a caller turn!
LlmResponse.from_parts(parts=[
    Part.from_text(text=""),
])
```

**LLM Lifecycle Decision Matrix:**
- **Text-Only Parts (`Part.from_text` / `Part.from_customized_response`)**: CXAS sends speech to user and closes bot turn. **LLM is bypassed.**
- **FunctionCall Parts (`Part.from_function_call`)**: CXAS executes tool (target Python tool MUST have `-> dict` return annotation) and feeds `toolResponse` back to LLM. **LLM is invoked.**
- **Never combine `response.partial = True` with `function_call` or inline tags**: Setting `response.partial = True` alongside `Part.from_function_call` silently drops the tool call; combining it with `<warm>` / `<prosody ...>` tags reads tag names aloud and clips audio at ~1.9 s.
- **Never return `parts=[]` on a caller turn**: Erases the caller turn and leaves voice sessions permanently deaf (`parts=[]` is valid **only** on `<context>no user activity detected...</context>` inactivity ticks).
- **Gotcha (`EMPTY_RESPONSE`)**: If you need to speak a prompt deterministically and wait for user speech, do NOT return a `FunctionCall` part in the same response. Execute the tool synchronously via `tools.<tool_name>()` in Python and return only `Part.from_text(...)`.

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
    if (part.text_or_transcript() or "") == "<event>session start</event>":
        return LlmResponse.from_parts(parts=[
            Part.from_text(text="Hi, thanks for calling Acme Support. How can I help you today?"),
        ])
```

### Detect silence / no-input (audio-safe)

```python
import re

def is_user_inactive(contents: list) -> bool:
    pattern = r"<context>no user activity detected for \d+ seconds\.</context>"
    return len(contents) > 1 and any(
        re.search(pattern, p.text_or_transcript() or "", re.IGNORECASE)
        for p in contents[-1].parts
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

- Declare all variables in `app.json` under `variableDeclarations` (keep total ≤95 variables and total serialized state <256 KB)
- Supported schema types: `OBJECT`, `ARRAY`, `STRING`, `BOOLEAN`, `INTEGER`, `NUMBER` (always use `STRING` for IDs/ZIPs -- `NUMBER` deserializes integers to floats like `12345.0`)
- Booleans: coerce defensively (`str(state.get("flag", "false")).lower() == "true"` -- `"false"` is truthy in Python!)
- Dates: sandbox runs in UTC -- read `callback_context.state.get("current_date")` for the app's configured timezone date
- Access via `callback_context.state` in callbacks, `context.state` in tools (do NOT set `state["key"] = None`, which deletes keys or fails schema checks; reset to typed empty defaults like `""`, `False`, `{}`)
