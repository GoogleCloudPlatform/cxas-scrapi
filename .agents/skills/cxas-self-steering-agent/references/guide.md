# Guide: self-steering agents on CXAS

This is the reference behind the rules in `SKILL.md`. The core starter code
comes from `resources/sample-agent/`; domain examples below illustrate how to
extend the starter for real applications.

## Why the design looks like this

-   **The model steers the conversation, like a contact center agent.** A new
    hire gets a short briefing, a runbook for each kind of request, a status
    panel showing where the case stands, a few scripts to read word for word,
    and buttons to transfer or end the conversation. Nobody hands them every
    runbook at once or acts behind their back. The design gives the model the
    same job: the power to make every conversational decision through tools, and
    the awareness to know where it is, what it can do right now, and why it did
    things before. Progressive disclosure is the runbook; deterministic actions
    are the scripts and buttons.
-   **The model only knows what's in `llm_request`.** Each turn it gets the
    system instruction with its tool declarations, and the history in
    `llm_request.contents` (customer messages, agent replies, tool calls, and
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
-   **One root agent for the conversation.** A sub-agent handoff is a
    `transfer_to_agent(agent_name: "...")` call, and earlier turns show up as
    `[Other Agent] said: ...`. That reads like a transfer to another person, so
    the model announces it, and "don't mention the transfer" rules then
    contradict the tool name and history. Even when an app attaches platform
    guardrails with a dedicated `transferAgent` in `childAgents`, the callback
    must hide `transfer_to_agent` / `transferToAgent` from the root agent so
    only deterministic guardrails can invoke it.

## Instruction structure

Use clean, flat top-level XML blocks rather than deeply nested wrappers. Never
use `<context>` in `instruction.txt` (`cxas lint` `I015` bans it as an internal
platform tag); put `Today's date is {{current_date}}.` inside `<status>`:

```xml
<role>...</role>
<constraints>...</constraints>

<anytime_actions>
- When the customer says they are all set and do not need anything else, call {@TOOL: graceful_end_session} right away without replying first, and pass a short, warm goodbye in `goodbye_message`. You do not need to open a playbook or load a task to end the conversation.
- When the customer asks to speak with a live representative, call {@TOOL: execute_live_agent_handover} right away without replying first, and pass `reason="customer_requested_agent"` and a one-sentence `pre_transfer_message`.
- When the customer asks for an unsupported request or change that no playbook covers, call {@TOOL: execute_live_agent_handover} right away without replying first, and pass `reason="request_not_supported"` and a one-sentence `pre_transfer_message`.
</anytime_actions>

<playbook_list>
Available Playbooks (call {@TOOL: open_playbook} to open one):
- `account_support`: Checking account status, updating contact details, or sending a summary.
- `live_agent`: Connecting the customer with a live representative.
</playbook_list>

{{% if current_playbook %}}
<task_list>
Available Tasks in Currently Opened Playbook ("{{current_playbook}}"):
{{% if current_playbook == 'account_support' %}}
- `check_account_status`: Look up an account and share its current status.
- `update_contact_email`: Update the email address on an account.
{{% elif current_playbook == 'live_agent' %}}
- `live_agent_escalation`: Connect the customer with a live representative.
{{% endif %}}
Your tool list only has the tools for the currently loaded task. Never call a tool that is not in your tool list, even if you called it earlier in the conversation. If you need a tool that is not in your list, first silently call {@TOOL: load_task} with the task that uses it (call {@TOOL: open_playbook} first if that task is in another playbook), then call the tool.
</task_list>
{{% endif %}}

<status>
Today's date is {{current_date}}.
{{% if current_playbook %}}
Currently Opened Playbook: "{{current_playbook}}". If the customer asks for something covered by a different playbook, silently call {@TOOL: open_playbook} with that playbook first, before replying or calling any other tool.
{{% if current_task %}}
Currently Loaded Task: "{{current_task}}". To switch to a different task in this playbook, silently call {@TOOL: load_task} with the exact task name first, before replying or calling any other tool.
{{% else %}}
No task is loaded yet. Silently call {@TOOL: load_task} with the exact task name from the list above first, before replying or calling any other tool.
{{% endif %}}
{{% else %}}
No playbook is open yet. Silently call {@TOOL: open_playbook} with the matching playbook first when the customer states what they need.
{{% endif %}}
</status>

{{% if pending_handoff_reason %}}
<pending_handoff>
A handoff to a live representative is required (reason: "{{pending_handoff_reason}}").
Call {@TOOL: execute_live_agent_handover} right away without replying first, and pass a one-sentence `pre_transfer_message` letting the customer know you are connecting them.
</pending_handoff>
{{% elif current_task == 'live_agent_escalation' %}}
<task name="live_agent_escalation">
<task_rules>
- Connect the customer with a live representative when they ask for one or when their request requires human help.
</task_rules>
<current_step>
- Call {@TOOL: execute_live_agent_handover} right away without replying first. Pass a short `reason` and a one-sentence `pre_transfer_message` letting the customer know you are connecting them.
</current_step>
</task>
{{% elif current_task == 'check_account_status' %}}
<task name="check_account_status">
<task_rules>...</task_rules>
<current_step>...</current_step>
</task>
{{% endif %}}
```

`<playbook_list>`, `<task_list>`, and `<status>` stay visible while a task is
loaded, so the model always knows where it is and how to pivot when the
customer changes the subject. A first request in a new playbook costs small
routing calls (`open_playbook`, `load_task`, then the domain tool); that's the
trade for a short, focused instruction on every turn. `<anytime_actions>` is static so saying goodbye or handing off on an immediate
live-agent or unsupported request takes one model call instead of three, and
`<pending_handoff>` overrides any active task whenever a domain tool sets
`pending_handoff_reason`.

Always build both routing layers (`open_playbook` and `load_task`), even for a
small agent. Group related use cases into a playbook with a few tasks; a
playbook with a single task is fine. Keep each list short, so the model never has
to pick from a long menu at either layer.

### Shared prerequisite blocks

When multiple tasks in the same playbook require the same prerequisite step (for
example, authenticating the caller or looking up a record before viewing or
modifying it), extract that gate into one shared Jinja-guarded block instead of
duplicating identical lookup logic inside every `<task>`:

```xml
{{% if current_playbook == 'account_support' and not account.id %}}
<shared_prerequisite name="account_lookup">
- Ask for the customer's account ID if they have not shared it, then call {@TOOL: lookup_account} with it.
</shared_prerequisite>
{{% elif pending_handoff_reason %}}
...
{{% elif current_task == 'check_account_status' %}}
...
{{% endif %}}
```

Because `account.id` is checked in one place, adding a third task to
`account_support` reuses the exact same verification behavior with zero drift.

## Router tools

The starter's `open_playbook` and `load_task` check exact names, return
fact-only status/error dicts (no conversational `"message"` prose keys), and
declare explicit parameter types plus `-> dict[str, Any]` (`T016`):

```python
PLAYBOOK_TASKS = {
    "live_agent": ("live_agent_escalation",),
}


def load_task(task_name: str, rationale: str) -> dict[str, Any]:
    """Loads the instructions for a task in the currently open playbook.

    Args:
      task_name (str): Exact name of the task to load.
      rationale (str): Why this task is being loaded. Kept in the conversation
        history so later turns know what prompted it. One short sentence (max 15
        words) stating the customer's request or the tool result that led here,
        e.g. "Customer asked to speak with a human representative." Do not restate
        the task name or include personal data.

    Returns:
      dict[str, Any]: Status and loaded task, or error details on mismatch.
    """
    playbook = context.state.get("current_playbook") or ""
    if playbook not in PLAYBOOK_TASKS:
        return {"status": "error", "error": "no_active_playbook"}

    name = task_name.strip()
    if name not in PLAYBOOK_TASKS[playbook]:
        for other_playbook, tasks in PLAYBOOK_TASKS.items():
            if name in tasks:
                return {
                    "status": "error",
                    "error": "task_in_different_playbook",
                    "task": name,
                    "playbook": other_playbook,
                }
        return {
            "status": "error",
            "error": "invalid_task",
            "current_playbook": playbook,
            "valid_tasks": list(PLAYBOOK_TASKS[playbook]),
        }

    context.state["current_task"] = name
    return {
        "status": "ok",
        "current_playbook": playbook,
        "current_task": name,
    }
```

`open_playbook` follows the same shape over `VALID_PLAYBOOKS = ("live_agent",)`:
it returns `{"status": "error", "error": "invalid_playbook", "valid_playbooks": list(VALID_PLAYBOOKS)}`
on a miss, or sets `current_playbook = name`, clears `current_task = ""`, and
returns `{"status": "ok", "current_playbook": name}`. Keep tool returns
fact-only (`status`, `error`, and domain data) so the model reads `instruction.txt`
for what to say instead of parroting a `"message"` field from tool JSON. Don't
echo `rationale` in the result; the call arguments already keep it in history.

## Task instructions and state design

Write task rules using `<task_rules>` (invariant constraints for the task) and
`<current_step>` (Jinja-branched next step):

```xml
{{% elif current_task == 'update_contact_email' %}}
<task name="update_contact_email">
<task_rules>
- Always confirm the new email address before saving it.
- Never invent account details that were not returned by {@TOOL: lookup_account}.
</task_rules>
<current_step>
{{% if not account.id %}}
- Ask for the customer's account ID if they have not provided it, then call {@TOOL: lookup_account}.
{{% elif account.status == 'Locked' %}}
- Account {{account.id}} is locked, so contact details cannot be updated here. Offer to connect the customer with a live representative, and if they agree, call {@TOOL: open_playbook} with `live_agent`.
{{% elif account.email_updated %}}
- Confirm that the contact email for account {{account.id}} has been updated to {{account.email}}.
{{% else %}}
- If the customer wants to update a different account than {{account.id}}, call {@TOOL: lookup_account} first.
- Ask for the new email address and confirm it, then call {@TOOL: update_account_email} with `account_id` and `email`.
{{% endif %}}
</current_step>
</task>
```

-   **Tool-grouped `OBJECT` variables:** Group cohesive domain state produced by
    a tool into a typed `OBJECT` declaration in `app.json` (for example
    `account` with `id`, `status`, `email`, `email_updated`) rather than adding
    many flat top-level scalars. This keeps `variableDeclarations` well under
    CES's ~100 variable limit (`A015`) and cleanly scopes state resets when a
    different record is looked up. In `instruction.txt`, read nested fields with
    dot notation (`{{account.status}}`), never subscripts
    (`account['status']`).
-   **Write each branch for what the history shows right after the tool call:**
    The next model call in the same turn already sees the new branch. That is
    why `{{% elif account.email_updated %}}` tells the model to confirm the
    update that just happened. Store a record ID on flags when an action is
    scoped to one item so it doesn't carry over to a second lookup.
-   **Variable lifecycle hygiene (`V100`–`V106`):** Declare every variable read
    or written in `instruction.txt`, tools, or callbacks inside `app.json`
    `variableDeclarations` (`V100`, `V104`). Keep variables clean (no unused
    declarations or write-only state keys flagged by `V105`/`V106`). Framework
    keys cleared across turns or written lazily—`current_task`,
    `pending_handoff_reason`, and `next_agent_actions`—are listed under
    `options.V105.ignore` in `cxaslint.yaml`.
-   **`pending_handoff_reason`:** Any domain tool can trigger an immediate live
    agent transfer without baking handoff prose into the tool by setting
    `context.state["pending_handoff_reason"] = "verification_failed"`. On the
    next model call, `<pending_handoff>` activates and directs the model to
    call `execute_live_agent_handover`, which prioritizes
    `pending_handoff_reason` over any fallback `reason` argument passed by the
    model and resets it to `""` (never `None`, which triggers `C011`).
-   **Double-brace Jinja only:** Always use `{{var}}` and `{{% if ... %}}` /
    `{{% elif ... %}}` / `{{% else %}}` / `{{% endif %}}`. Single-brace
    `{% ... %}` injects `<state_update>` blocks into history, and CES leaves
    `{{# ... #}}` / `{# ... #}` comments verbatim in the prompt.
-   **Keep shared tools visible:** Mention tools such as `lookup_account` in
    every branch where the customer might switch records.

## Dynamic tool hiding

```python
TRANSFER_TOOLS = ("transfer_to_agent", "transferToAgent")


def _handle_dynamic_tool_hiding(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
    """Hides transfer tools and any tool not mentioned in the instruction."""
    config = llm_request.config
    if not (
        config
        and config.tools
        and config.system_instruction
        and config.system_instruction.parts
    ):
        return None

    instruction = config.system_instruction.parts[0].text or ""
    for tool_decl in config.tools:
        for func_decl in tool_decl.function_declarations or []:
            if not func_decl.name:
                continue
            if func_decl.name not in TRANSFER_TOOLS and re.search(
                rf"\b{re.escape(func_decl.name)}\b", instruction
            ):
                continue
            if (
                config.excluded_tools is None
                or func_decl.name not in config.excluded_tools
            ):
                config.hide_tool(func_decl.name)
    return None
```

-   **Always hide `TRANSFER_TOOLS` and iterate all `config.tools`:** If an app
    uses platform guardrails with `transferAgent` pointing to a handler in
    `childAgents`, CES adds `transfer_to_agent` / `transferToAgent` (and may
    supply multiple `ToolDeclaration` blocks in `config.tools`). Always hiding
    `TRANSFER_TOOLS` ensures the root model cannot initiate a sub-agent
    transfer on its own while deterministic guardrails can still route as
    configured.
-   **Match on word boundaries:** Built-in `end_session` is a substring of
    `graceful_end_session`; a substring check would expose `end_session`
    whenever `graceful_end_session` is visible.
-   **Keep hidden tools out of tag names and comments:** A tag like
    `<end_session>` exposes `end_session` on every turn, and CES does not
    strip Jinja comments.
-   **Don't name tasks or playbooks after tools:** `<task_list>` stays visible
    while a playbook is open, so a task sharing a tool's name would expose that
    tool prematurely.
-   **Register every tool in the root agent's `tools` list:** Including the
    platform's `end_session` and the compatibility helper
    `set_next_agent_actions`. Dynamic hiding controls what the model sees per
    call.

## Deterministic actions

Use code only for the greeting, word-for-word text, goodbye, and handoff.
Chaining workflow steps inside tools is rigid, and later turns see actions
their instructions don't explain.

The greeting is sent on the first model call of the conversation (matching calls
and chats that begin with a start event before the customer speaks):

```python
GREETING = (
    "Hi, thanks for contacting customer support. How can I help you today?"
)


def _handle_initial_greeting(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
    """Sends the greeting on the first model call of the conversation."""
    if callback_context.state.get("played_greeting"):
        return None
    callback_context.state["played_greeting"] = True
    return LlmResponse.from_parts(
        parts=[Part.from_customized_response(content=GREETING)]
    )
```

Tools queue exact wording and session termination directly by writing
`context.state["next_agent_actions"]`:

```python
def execute_live_agent_handover(
    reason: Optional[str] = None,
    pre_transfer_message: Optional[str] = None,
) -> dict[str, Any]:
    """Hands the conversation to a live representative."""
    pending_reason = str(
        context.state.get("pending_handoff_reason") or ""
    ).strip()
    context.state["pending_handoff_reason"] = ""
    handoff_reason = (
        pending_reason or (reason or "").strip() or "customer_requested_agent"
    )
    message = (pre_transfer_message or "").strip() or DEFAULT_TRANSFER_MESSAGE
    context.state["next_agent_actions"] = {
        "customized_response": {"content": message, "disable_barge_in": True},
        "end_session": {"reason": handoff_reason, "escalated": True},
    }
    return {"status": "transferring", "reason": handoff_reason}
```

`set_next_agent_actions` remains bundled in `tools/` as a compatibility helper
for older tools that call `tools.set_next_agent_actions({...})`, but new tools
should write `context.state["next_agent_actions"] = {...}` directly so there is
no extra nested tool invocation.

When the tool returns, the next model call is intercepted by
`_handle_next_agent_actions`, which plays the queued parts and latches
`end_session`:

```python
def _handle_next_agent_actions(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
    """Plays queued next_agent_actions without calling the model."""
    actions = callback_context.state.pop("next_agent_actions", None)
    if not actions:
        return None

    parts = []
    if response := actions.get("customized_response"):
        parts.append(Part.from_customized_response(**response))
    if end_session := actions.get("end_session"):
        # Part.from_end_session takes reason and escalated (not session_escalated).
        parts.append(Part.from_end_session(**end_session))
        # Latch end_session so follow-up streaming calls cannot hit the model.
        callback_context.state["next_agent_actions"] = {
            "end_session": end_session
        }
    return LlmResponse.from_parts(parts=parts)
```

Popping `next_agent_actions` ensures a `customized_response` is spoken once,
while re-latching `{"end_session": end_session}` prevents follow-up streaming
model calls in the same turn from slipping past to the LLM before the session
terminates.

`graceful_end_session(goodbye_message=...)` queues
`{"reason": "completed", "escalated": False}`, and
`execute_live_agent_handover(reason=..., pre_transfer_message=...)` queues
`{"reason": handoff_reason, "escalated": True}` so the session is marked as
escalated. For verbatim disclosures that do not end the conversation, queue
only `customized_response`, set a state flag (e.g., `disclosed = True`), and
gate the next step in `<current_step>` on that flag.

To share logic between tools without exposing a helper to the model, put it in a
registered tool and omit its name from `instruction.txt`; tools can call each
other via `tools.<tool_name>({...})`.

## The callback

```python
def before_model_callback(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
    """Runs handlers in order and returns the first response built."""
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
avoids the per-callback startup cost on the platform.

## Platform gotchas

-   **CES Jinja subset and output tags:** Templates support `if`/`elif`/`else`,
    `and`, `or`, `not`, `in`, `==`, `!=`, and dot access inside double-brace
    blocks (`{{% ... %}}`), but reject `not in` (write `not a in ['x', 'y']`)
    and subscripts like `account['status']` (use `account.status`). Inside
    `{{ ... }}` output tags, CES only substitutes bare variables (`{{var}}`,
    `{{a.b}}`) and leaves expressions such as `{{ x or "none" }}` or
    `{{ x | default("none") }}` literal; single-brace `{var}` is never
    substituted, and single-brace `{% ... %}` blocks emit `<state_update>` notes
    into history. CES also leaves `{{# ... #}}` / `{# ... #}` comments verbatim
    in the prompt. `check_tool_visibility.py` and `cxas lint` (`I020`) flag
    unsupported constructs, and `check_tool_visibility.py` automatically
    synthesizes `if`/`elif`/`else` branch states (including dotted `OBJECT`
    conditions) to catch hidden-branch regressions, undeclared `OBJECT`
    properties, returned-dict prose keys or `**x` spreads, router state
    violations, missing handoff reasons, and hardcoded sample confirmation codes
    before you push.
-   **Banned `<context>` tag (`I015`):** Never wrap prompt sections in
    `<context>`. Put `Today's date is {{current_date}}.` inside `<status>`.
-   **`Part.from_end_session` signature (`C018`):** Takes `reason` and
    `escalated` (not `session_escalated`). Passing `session_escalated` raises
    `TypeError` at runtime and leaves `next_agent_actions` latched in state.
-   **Never assign `None` to state (`C011`):** Clearing a string key with
    `None` triggers platform errors; reset strings like `pending_handoff_reason`
    to `""`.
-   **Declare every variable (`V100`–`V106`, `A015`):** Undeclared
    tool-written variables are dropped between turns, and undeclared instruction
    variables always render empty. Keep `NUMBER` floats in mind, stay under 95
    top-level declarations (`A015`) by grouping into `OBJECT` variables, and
    preserve `options.V105.ignore` in `cxaslint.yaml` for framework keys.
-   **`current_date` builtin:** CES provides `current_date` in the app's time
    zone (like `2026-10-16[America/New_York]`) without a declaration. Python
    tools run on UTC, so read `context.state["current_date"]` instead of
    `datetime.date.today()` and add a `per_file` `V100: "off"` override for
    that tool file in `cxaslint.yaml`:

    ```yaml
    per_file:
      # Reads current_date, which CES provides without a declaration.
      "tools/<name>/python_function/python_code.py":
        V100: "off"
    ```

    The local scripts fill `current_date` from your machine's clock; pass it in
    `--state` to test a specific day.
-   **Python tool conventions (`C019`, `T004`, `T015`, `T016`):** Import
    `Any` and `Optional` from `typing` explicitly (`C019`), put the entry
    function (named like the tool directory) first (`T004`, `T015`), annotate
    parameters and `-> dict[str, Any]` (`T016`), and keep tool JSON
    descriptions in sync with docstrings via
    `scripts/sync_tool_descriptions.py` (which creates missing `<tool>.json`
    wrappers automatically when run without `--check`).
-   **`cxaslint.yaml` overrides:** The starter disables `I001`, `T001`, `T011`,
    `I012`, `A007`, `A009`, and `T014` with explanations and sets
    `options.V105.ignore: [current_task, pending_handoff_reason, next_agent_actions]`.
    All other rules remain enabled.
-   **Deployment timing:** After `cxas push`, wait about a minute before
    testing. `cxas push` doesn't change an existing app's display name; rename
    it in the console.

## Sample agent

`resources/sample-agent` is a generic, domain-agnostic starter with one root
agent, `Support_Agent`, on `gemini-composite-v1`:

Playbook     | Task                    | Tools the task uses
------------ | ----------------------- | -----------------------------
`live_agent` | `live_agent_escalation` | `execute_live_agent_handover`

Registered tools (6): `open_playbook`, `graceful_end_session`, and
`execute_live_agent_handover` are always visible (from `<playbook_list>` and
`<anytime_actions>`); `load_task` appears once a playbook is open;
`set_next_agent_actions` (compatibility helper) and `end_session` stay hidden
from the model (along with `TRANSFER_TOOLS`).

Declared variables (5): `current_playbook` (`STRING`), `current_task`
(`STRING`), `next_agent_actions` (`OBJECT`), `played_greeting` (`BOOLEAN`), and
`pending_handoff_reason` (`STRING`). Add your domain playbooks, tasks, tools,
and grouped `OBJECT` variables directly on top of this starter without deleting
any retail code.
