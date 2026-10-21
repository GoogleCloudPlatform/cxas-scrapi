# Guide: self-steering agents on CXAS

This is the reference behind the rules in `SKILL.md`. The code snippets come
from the bundled sample agent in `resources/sample-agent/`.

## Why the design looks like this

-   **The model steers the conversation, like a contact center agent.** A new
    hire gets a short briefing, a runbook for each kind of request, a screen
    that shows where the case stands, a few scripts to read word for word, and
    buttons to transfer or end the conversation. Nobody hands them every runbook
    at once or acts behind their back. The design gives the model the same job:
    the power to make every conversational decision through tools, and the
    awareness to know where it is, what it can do right now, and why it did
    things before. Progressive disclosure is the runbook; deterministic actions
    are the scripts and buttons.
-   **The model only knows what's in `llm_request`.** Each turn it gets the
    system instruction with its tool declarations, and the history in
    `llm_request.contents` (customer messages, agent replies, tool calls and
    results). When an agent misbehaves, read that request for the turn before
    changing anything. The cause is usually stale, conflicting, or irrelevant
    context, not a model that can't do the job.
-   **Conversational agents have little time to think.** Replies have to come
    within seconds, so keep each turn's instruction and tool list to what the
    current step needs. Unrelated rules and tools add tokens, slow the first
    token, and give the model more ways to pick the wrong thing.
-   **Every turn is a new person.** The model's reasoning from earlier turns
    isn't in the history; it sees what the agent said and which tools it called,
    but not why. If the history shows an action the current instruction never
    mentions, the model learns that it may improvise. Keep the instruction and
    the history consistent, and leave a `rationale` on routing calls.
-   **Keep the model in the loop.** When code sends text or ends the
    conversation, do it through a tool the model calls, so the history shows the
    model's own call, a result saying what happened, and the text as the agent's
    reply. Never tell the model "a callback did X"; it doesn't know what a
    callback is.
-   **One agent, not sub-agents.** A sub-agent handoff is a
    `transfer_to_agent(agent_name: "...")` call, and earlier turns show up as
    `[Other Agent] said: ...`. That reads like a transfer to another person, so
    the model announces it, and "don't mention the transfer" rules then
    contradict the tool name and history.

## Instruction structure

```xml
<role>...</role>
<constraints>...</constraints>

<ending_the_conversation>
When the customer says they're all set and don't need anything else, call {@TOOL: graceful_end_session} right away without replying first, and pass a short, warm goodbye in `goodbye_message`. You don't need to open a playbook or load a task to end the conversation.
</ending_the_conversation>

<taskflow>
    {{# SECTION 1: AVAILABLE PLAYBOOKS (ALWAYS VISIBLE) #}}
    <section name="Available_Playbooks">
        <playbook_list>
            Available Playbooks (call {@TOOL: open_playbook} to open one):
            - `order_support`: Checking an order's status, canceling an order, or texting an order summary.
            - `returns`: Returning an item from a delivered order.
            - `live_agent`: Connecting the customer with a live representative.
        </playbook_list>
        {% if current_playbook %}
        <current_playbook_status>
            Currently Opened Playbook: "{{current_playbook}}"
            If the customer asks for something covered by a different playbook, silently call {@TOOL: open_playbook} with that playbook FIRST, before replying or calling any other tool.
        </current_playbook_status>
        {% endif %}
    </section>

    {{# SECTION 2: AVAILABLE TASKS IN THE OPENED PLAYBOOK #}}
    {% if current_playbook %}
    <section name="Available_Tasks_In_Playbook">
        <task_list>
            Available Tasks in Currently Opened Playbook ("{{current_playbook}}"):
            {% if current_playbook == 'order_support' %}
            - `check_order_status`: Look up an order and share its status and delivery details.
            ...
            {% endif %}
        </task_list>
        <current_task_status>
            {% if current_task %}
            Currently Loaded Task: "{{current_task}}"
            To switch to a different task in this playbook, silently call {@TOOL: load_task} with the exact task name FIRST, before replying or calling any other tool.
            {% else %}
            No task is loaded yet. Silently call {@TOOL: load_task} with the exact task name from the list above FIRST, before replying or calling any other tool.
            {% endif %}
            Your tool list only has the tools for the currently loaded task. Never call a tool that is not in your tool list, even if you called it earlier in the conversation. If you need a tool that isn't in your list, first silently call {@TOOL: load_task} with the task that uses it (call {@TOOL: open_playbook} first if that task is in another playbook), then call the tool.
        </current_task_status>
    </section>
    {% endif %}

    {{# SECTION 3: INSTRUCTIONS FOR THE LOADED TASK #}}
    {% if current_task %}
    <section name="Loaded_Task_Instruction">
        <active_instruction>
            Instruction for Currently Loaded Task ("{{current_task}}"):
            {% if current_task == 'check_order_status' %}
            <subtask name="Check_Order_Status">...</subtask>
            {% elif ... %}
            {% endif %}
        </active_instruction>
    </section>
    {% endif %}
</taskflow>
```

The first two sections stay visible while a task is loaded, so the model always
knows where it is and how to move when the customer changes the subject. A first
request in a new area costs a few small model calls (`open_playbook`,
`load_task`, then the domain tool); that's the trade for a short, focused
instruction on every turn. The goodbye is static because it can come at any
point, and keeping it out of a playbook means it takes one model call instead of
three.

Always build both layers, even for a small agent. Group related use cases into a
playbook with a few tasks; a playbook with a single task is fine. Keep each list
short, so the model never has to pick from a long menu at either layer.

## Router tools

```python
PLAYBOOK_TASKS = {
    "order_support": ("check_order_status", "cancel_order", "text_order_summary"),
    "returns": ("start_return",),
    "live_agent": ("live_agent_escalation",),
}


def load_task(task_name: str, rationale: str) -> dict[str, Any]:
  """Loads the instructions for a task in the currently open playbook.

  Args:
    task_name (str): Exact name of the task to load.
    rationale (str): Why this task is being loaded. Kept in the conversation
      history so later turns know what prompted it. One short sentence (max
      15 words) stating the customer's request or the tool result that led
      here, e.g. "Customer changed their mind and wants to cancel." Do not
      restate the task name or include personal data.
  """
  playbook = context.state.get("current_playbook") or ""
  if playbook not in PLAYBOOK_TASKS:
    return {"error": "NO_ACTIVE_PLAYBOOK", "message": "No playbook is open. Call open_playbook first."}
  name = task_name.strip()
  if name not in PLAYBOOK_TASKS[playbook]:
    for other_playbook, tasks in PLAYBOOK_TASKS.items():
      if name in tasks:
        return {"error": "TASK_IN_DIFFERENT_PLAYBOOK",
                "message": f"Task '{name}' is in the '{other_playbook}' playbook. Call open_playbook with '{other_playbook}' first."}
    return {"error": "INVALID_TASK",
            "message": f"Invalid task name '{name}'. Valid tasks in '{playbook}' are: {list(PLAYBOOK_TASKS[playbook])}."}
  context.state["current_task"] = name
  return {"success": True, "current_task": name, "playbook": playbook, "message": f"Loaded task '{name}'."}
```

`open_playbook` is the same shape over `VALID_PLAYBOOKS` and also sets
`current_task` to `""`. Don't echo `rationale` in the result; the call arguments
already keep it in the history.

## Task instructions

Write state-dependent rules as Jinja branches. The cancel task from the sample:

```xml
{% elif current_task == 'cancel_order' %}
<subtask name="Cancel_Order">
    {% if not order_id %}
    - Ask for the order number if the customer hasn't shared it, then call {@TOOL: lookup_order} with it.
    {% else %}
    - If the customer means a different order than {{order_id}}, call {@TOOL: lookup_order} with that order number first.
    {% if order_status == 'Processing' %}
    - Order {{order_id}} hasn't shipped yet, so it can be canceled. Confirm that the customer wants to cancel it, and ask for a short reason if they haven't given one.
    - Once they confirm, call {@TOOL: submit_cancellation} with `order_number` and `reason`.
    {% elif order_status == 'Canceled' %}
    - Order {{order_id}} is canceled. If you just canceled it, confirm that and share the refund timeline from the result.
    {% elif order_status == 'Shipped' %}
    - Order {{order_id}} has already shipped, so it can't be canceled. Let the customer know they can start a return once it's delivered.
    {% elif order_status == 'Delivered' %}
    - Order {{order_id}} was already delivered, so it can't be canceled. Offer to start a return instead. If the customer wants one, silently call {@TOOL: open_playbook} with `returns`.
    {% else %}
    - Order {{order_id}} has status "{{order_status}}", so it can't be canceled. Let the customer know.
    {% endif %}
    {% endif %}
</subtask>
```

-   Tools set the state (`lookup_order` sets `order_id` and `order_status`), and
    the template picks the branch. Tools return data and outcomes; the
    instruction says what to do with them.
-   After a tool changes state, the next model call in the same turn already
    sees the new branch. That's why the `Canceled` branch says "If you just
    canceled it, confirm that". Write every branch so it explains what the
    history shows. If a branch asks for an action that doesn't change any
    variable the branch depends on, have the tool set a flag, or the next call
    is told to do it again. For an action on one order, store that order's
    number instead of `true`, so the flag doesn't carry over to the next order.
-   `{var}` (single braces) isn't substituted. The model sees the literal
    placeholder and only gets the value from state update notes that the
    platform attaches to the start of the turn. Use `{{var}}`.
-   Shared tools such as `lookup_order` are mentioned in every branch where the
    customer might need them, so they stay visible.

## Dynamic tool hiding

```python
def _handle_dynamic_tool_hiding(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
  """Hides every tool that the rendered instruction doesn't mention."""
  config = llm_request.config
  if not (config and config.tools and config.system_instruction and config.system_instruction.parts):
    return None
  instruction = config.system_instruction.parts[0].text
  for func_decl in config.tools[0].function_declarations:
    if re.search(rf"\b{re.escape(func_decl.name)}\b", instruction):
      continue
    if config.excluded_tools is None or func_decl.name not in config.excluded_tools:
      config.hide_tool(func_decl.name)
  return None
```

-   Match on word boundaries. The built-in `end_session` is a substring of
    `graceful_end_session`; a substring check would expose it whenever the
    goodbye tool is visible, and the model can then end the conversation without
    the goodbye message.
-   The callback receives the instruction with `{{# ... #}}` comments still in
    it, and tag names count too: a block called `<end_session>` keeps
    `end_session` visible on every turn.
-   A task list stays visible while its playbook is open, so a tool named like a
    task is visible before the task loads. The sample's `cancel_order` task uses
    a `submit_cancellation` tool for this reason.
-   A tool is visible only while the rendered branch mentions it. If a branch
    drops the only mention of a tool the model used earlier, the model may call
    it anyway from the history and the platform fails the turn with "unexpected
    function call". The rule at the end of `<current_task_status>` covers the
    rest.
-   Register every tool in the root agent's `tools` list, including the
    platform's `end_session` and the `set_next_agent_actions` helper. Hiding
    decides what the model sees per call.

## Deterministic actions

Use code only for the greeting, word-for-word text, and the goodbye or handoff.
Chaining workflow steps inside tools (a tool that queues the next tool, or one
that switches `current_task`) is rigid, and later turns see actions their
instructions don't explain.

The greeting is sent on the first model call of the conversation. It assumes the
conversation opens before the customer says anything, the way a phone call
connects or a chat window sends a start event.

```python
GREETING = "Hi, thanks for contacting customer support. How can I help you today?"


def _handle_initial_greeting(callback_context, llm_request):
  """Sends the greeting on the first model call of the conversation."""
  if callback_context.state.get("played_greeting"):
    return None
  callback_context.state["played_greeting"] = True
  return LlmResponse.from_parts(parts=[Part.from_customized_response(content=GREETING)])
```

Exact wording goes through a tool that queues it with `set_next_agent_actions`,
a small Python tool that stores actions in the `next_agent_actions` state
variable:

```python
def set_next_agent_actions(customized_response=None, end_session=None) -> None:
  """Queues the agent's next reply and actions so they play without calling the model."""
  actions = context.state["next_agent_actions"] = {}
  if customized_response:
    actions["customized_response"] = customized_response
  if end_session:
    actions["end_session"] = end_session


def play_sms_legal_disclosure() -> dict[str, Any]:
  """Shares the text message terms word for word and asks the customer if we can text them.

  Call this directly without replying to the customer first.
  """
  context.state["sms_disclosure_played"] = True
  tools.set_next_agent_actions({
      "customized_response": {"content": DISCLOSURE_TEXT, "disable_barge_in": True},
  })
  return {"success": True, "status": "terms_shared"}
```

When the tool returns, the next model call is intercepted by the first callback
handler, which plays the queued actions without calling the model:

```python
def _handle_next_agent_actions(callback_context, llm_request):
  """Plays what a tool queued with set_next_agent_actions, without calling the model."""
  actions = callback_context.state.pop("next_agent_actions", None)
  if not actions:
    return None
  parts = []
  if response := actions.get("customized_response"):
    parts.append(Part.from_customized_response(**response))
  if end_session := actions.get("end_session"):
    # Part.from_end_session takes reason and escalated (not session_escalated).
    parts.append(Part.from_end_session(**end_session))
  return LlmResponse.from_parts(parts=parts)
```

`graceful_end_session(goodbye_message)` queues the goodbye with `{"reason":
"completed", "escalated": False}`, and `execute_live_agent_handover(reason,
pre_transfer_message)` queues the message with `"escalated": True` so the
conversation counts as escalated. Both docstrings tell the model to call the
tool without replying first, so the customer doesn't get a reply and then a
separate goodbye. Gate follow-up steps on the state flag the tool sets (the
sample's `text_order_summary` task shows `send_sms_summary` only once
`sms_disclosure_played` is true), and have the follow-up tool check the flag
too.

-   The queued text can include values from state or a tool result, like an
    order number or a date. What matters is that the model doesn't get to
    rephrase it.
-   One tool calls another with `tools.<tool_name>({...})`, arguments in one
    dict, the way the sample's tools call `set_next_agent_actions`. The call
    returns a response object, and `.json()["result"]` is the other tool's
    return value. Code can call any registered tool, whether the model can see
    it or not. The nested call shows up under the outer tool in `cxas trace
    get`, but not as a separate call in the conversation history. To share logic
    between tools, put it in a helper tool, register it, and keep its name out
    of the instruction so the model never sees it.

## The callback

```python
def before_model_callback(callback_context, llm_request):
  """Runs the handlers in order and returns the first response one of them builds."""
  for handler in (
      _handle_next_agent_actions,
      _handle_initial_greeting,
      _handle_dynamic_tool_hiding,
  ):
    if (response := handler(callback_context, llm_request)) is not None:
      return response
  return None
```

Queued actions go first, the greeting second, and tool hiding last because it
only matters for calls that reach the model. One callback with several handlers
avoids the startup cost the platform adds per separate callback.

## Platform gotchas

-   CXAS instruction templates support `if`/`elif`/`else`, `and`, `or`, `not`,
    `in`, `==`, `!=` and dot access, but reject `not in` (write `not a in ['x',
    'y']`) and subscripts like `order['status']` (use `order.status`). `cxas
    lint` doesn't catch these and the push fails with "Invalid template syntax",
    so `check_tool_visibility.py` flags them.
-   `Part.from_end_session` takes `reason` and `escalated`. Passing
    `session_escalated` raises a `TypeError`, the platform answers with a
    fallback line, and the queued action stays in state, so every later turn
    fails the same way until it's cleared. The `end_session` call it builds does
    carry `session_escalated`, which is why traces and `run_locally.py` show
    that name.
-   Declare every variable in `app.json` `variableDeclarations`. Undeclared
    tool-written variables are dropped between turns, and an undeclared variable
    in the instruction always renders empty, which silently flips its branch.
    `cxas lint` (V100) checks state reads and writes in code, and
    `check_tool_visibility.py` checks the variables the instruction uses.
    `NUMBER` variables come back as floats.
-   CES provides `current_date` without a declaration, in the app's time zone,
    rendered like `2026-10-16[America/New_York]`. The sample puts `Today's date
    is {{current_date}}.` in `<role>`. Python tools run on UTC, so a tool that
    needs today's date should read `context.state["current_date"]` instead of
    calling `datetime.date.today()`. `cxas lint` (V100) only knows it's built in
    for the instruction, so don't declare it; turn V100 off for that tool's file
    in `cxaslint.yaml`:

    ```yaml
    per_file:
      # Reads current_date, which CES provides without a declaration.
      "tools/<name>/python_function/python_code.py":
        V100: "off"
    ```

    The local scripts fill `current_date` in from your machine's clock; pass it
    in `--state` to test a specific day.
-   The model-facing tool description comes from the Python docstring. Keep each
    tool JSON's `pythonFunction.description` in sync with
    `scripts/sync_tool_descriptions.py`.
-   Put each tool's entry function, named like the tool, first in
    `python_code.py` and helpers after it, or `cxas lint` warns (T004).
-   The sample's `cxaslint.yaml` turns off the lint rules that don't fit this
    design (I001, T001, T011, I012, A007, A009, T014), with the reason next to
    each. Keep it in the app directory; every other rule stays on, apart from
    per-file entries like the `current_date` one above.
-   After `cxas push`, wait about a minute before testing. `cxas push` doesn't
    change an existing app's display name; rename it in the console.

## Sample agent

`resources/sample-agent` is an online store support agent with one root agent,
`Support_Agent`, on `gemini-composite-v1`.

Playbook        | Task                    | Tools the task uses
--------------- | ----------------------- | -------------------
`order_support` | `check_order_status`    | `lookup_order`
`order_support` | `cancel_order`          | `lookup_order`, `submit_cancellation`
`order_support` | `text_order_summary`    | `lookup_order`, `play_sms_legal_disclosure`, `send_sms_summary`
`returns`       | `start_return`          | `lookup_order`, `create_return`
`live_agent`    | `live_agent_escalation` | `execute_live_agent_handover`

`open_playbook` and `graceful_end_session` are always visible, and `load_task`
appears once a playbook is open. `lookup_order` uses sample data: order 1001 is
processing, 1002 has shipped, 1003 was delivered, and any other number isn't
found.
