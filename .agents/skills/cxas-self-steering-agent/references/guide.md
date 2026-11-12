# Guide: self-steering agents on CXAS

This is the reference behind the rules in `SKILL.md`. The code snippets come
from the bundled sample agent in `resources/sample-agent/`.

## Why the design looks like this

- **The model steers the conversation, like a contact center agent.** A new
  hire gets a short briefing, a runbook for each kind of request, a screen
  that shows where the case stands, a few scripts to read word for word, and
  buttons to transfer or end the conversation. Nobody hands them every runbook
  at once or acts behind their back. The design gives the model the same job:
  the power to make every conversational decision through tools, and the
  awareness to know where it is, what it can do right now, and why it did
  things before. Progressive disclosure is the runbook; deterministic actions
  are the scripts and buttons.
- **The model only knows what's in `llm_request`.** Each turn it gets the
  system instruction with its tool declarations, and the history in
  `llm_request.contents` (customer messages, agent replies, tool calls and
  results). When an agent misbehaves, read that request for the turn before
  changing anything. The cause is almost always stale, conflicting, or
  overloaded context, not a model that can't do the job.
- **Conversational agents have little time to think.** Replies have to come
  within seconds, so keep each turn's instruction and tool list to what the
  current step needs. Unrelated rules, future steps, and extra tools add
  tokens, slow the first token, and give the model more ways to pick the wrong
  thing.
- **Every turn is a new person.** The model's reasoning from earlier turns
  isn't in the history; it sees what the agent said and which tools it called,
  but not why. If the history shows an action the current instruction never
  mentions, the model learns that it may improvise. Keep the instruction and
  the history consistent, and leave a `rationale` on routing calls.
- **Keep the model in the loop.** When code sends text or ends the
  conversation, do it through a tool the model calls, so the history shows the
  model's own call, a result saying what happened, and the text as the agent's
  reply. Never tell the model "a callback did X"; it doesn't know what a
  callback is.
- **One agent, not sub-agents.** A sub-agent handoff is a
  `transfer_to_agent(agent_name: "...")` call, and earlier turns show up as
  `[Other Agent] said: ...`. That reads like a transfer to another person, so
  the model announces it, and "don't mention the transfer" rules then
  contradict the tool name and history.

## Instruction structure

`instruction.txt` is a flat series of top-level blocks. Static blocks come first
and the ones that depend on state come after them, so the model reads from the
general to the specific: who it is, the rules, what it can always do, the menus,
where it is now, and what to do now. Each block has one job.

| Block               | Rendered when                                    | What it holds                                                                                    |
| ------------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------------ |
| `<role>`            | always                                           | Who the agent is and what it helps with.                                                         |
| `<context>`         | always                                           | Facts such as `Today's date is {{current_date}}.`                                                |
| `<constraints>`     | always                                           | Reply style and hard rules.                                                                      |
| `<anytime_actions>` | always                                           | The wrap-up rule, the goodbye, the transfer, and what to do when no tool can make a change.      |
| `<playbook_list>`   | always                                           | The playbooks, and when to open or switch them with `open_playbook`.                             |
| `<task_list>`       | a playbook is open                               | The open playbook's tasks, when to load or switch them with `load_task`, and the tool list rule. |
| `<status>`          | always                                           | The open playbook and the loaded task.                                                           |
| `<pending_handoff>` | a tool set `pending_handoff_reason`              | Optional. Takes the task's place and tells the model to transfer now.                            |
| `<identify_order>`  | a task is loaded, order not verified             | Optional. The steps every task starts with, written once (rename it for your record).            |
| `<playbook_rules>`  | a task is loaded and the prerequisite is done    | Rules every task in the playbook shares: switching tasks or records.                             |
| `<task name="...">` | that task is loaded and the prerequisite is done | `<task_rules>`, `<collected>` and `<current_step>`.                                              |

```xml
<role>...</role>

<context>
Today's date is {{current_date}}.
</context>

<constraints>...</constraints>

<anytime_actions>
You can do these at any point in the conversation, without opening a playbook or loading a task first.
- Once the customer has what they asked for, ask whether there's anything else you can help with, unless you just asked. If you couldn't do what they asked, tell them what they can do next instead.
- When the customer says they're all set and don't need anything else, call {@TOOL: graceful_end_session} right away without replying first, and pass a short, warm goodbye in `goodbye_message`.
- When the customer asks for a live representative, or for help with something outside order support and returns, call {@TOOL: execute_live_agent_handover} right away without replying first. Pass a short `reason`, a `conversation_summary` of what the customer wanted, what you've done, and what's still open, and a one-sentence `pre_transfer_message` letting the customer know you're connecting them.
- When the customer wants a change that none of your playbooks or tasks can make, don't call a tool that isn't in your tool list. Tell them plainly that you can't make that change and offer to connect them with a representative. Call {@TOOL: execute_live_agent_handover} only if they say yes.
</anytime_actions>

<playbook_list>
When the customer asks for something, silently call {@TOOL: open_playbook} with the playbook that covers it, before replying or calling any other tool. Do the same when they move on to something a different playbook covers. If the request could fit more than one playbook, ask one short question to find out which they mean first.
- `order_support`: Checking an order's status, canceling an order that hasn't shipped, or texting an order summary.
- `returns`: Returning a delivered order by mailed return label or scheduled courier pickup.
</playbook_list>

{{% if current_playbook %}}
<task_list>
Tasks in the open playbook (`{{current_playbook}}`). Silently call {@TOOL: load_task} with the task that matches the customer's request, before replying or calling any other tool. Do the same when they move on to a different task.
{{% if current_playbook == 'order_support' %}}
- `check_order_status`: Share an order's status and delivery details.
...
{{% endif %}}
Your tool list only has the tools for the loaded task. Never call a tool that isn't in your tool list, even if you called it earlier in the conversation. If you need one that isn't there, first silently call {@TOOL: load_task} with the task that uses it (call {@TOOL: open_playbook} first if that task is in a different playbook), then call the tool.
</task_list>
{{% endif %}}

<status>
{{% if current_task %}}
Open playbook: `{{current_playbook}}`. Loaded task: `{{current_task}}`.
{{% elif current_playbook %}}
Open playbook: `{{current_playbook}}`. No task is loaded yet.
{{% else %}}
No playbook is open yet.
{{% endif %}}
</status>

{{% if pending_handoff_reason %}}
<pending_handoff>
A previous step requires a live representative (reason: `{{pending_handoff_reason}}`). Call {@TOOL: execute_live_agent_handover} right away without replying first. ...
</pending_handoff>

{{% elif current_task and not zip_verified %}}
<identify_order>
Every task starts by finding the customer's order and verifying its shipping ZIP code. Keep the loaded task. Once the ZIP code is verified, the task's next step shows here.
<current_step>
{{% if not order_id %}}
- Ask for the order number if the customer hasn't shared it, then call {@TOOL: lookup_order} with it.
- If the lookup found no order, ask the customer to double-check the number.
{{% else %}}
- Order {{order_id}} was found. Ask for the 5-digit shipping ZIP code on it and call {@TOOL: verify_order_zip} with `postal_code`. Don't share anything about the order until the ZIP code is verified.
- ...
{{% endif %}}
</current_step>
</identify_order>

{{% elif current_task %}}
<playbook_rules>
- Order {{order_id}} ({{order_item}}) is verified and stays selected when the customer asks for something else about it. Silently call {@TOOL: load_task} with that task, after {@TOOL: open_playbook} if it's in the other playbook.
- If the customer means a different order, call {@TOOL: lookup_order} with that order number. They'll need to verify its ZIP code too.
{{% if current_playbook == 'returns' and order_status == 'Delivered' %}}
- No return has been created for this order yet, so the customer can switch between a mailed return label and a courier pickup. Silently call {@TOOL: load_task} with the other task.
{{% endif %}}
</playbook_rules>

{{% if current_task == 'check_order_status' %}}
<task name="check_order_status">
<current_step>
- Order {{order_id}}: {{order_item}}, status {{order_status}}, {{order_delivery}}. Share these details with the customer.
</current_step>
</task>

{{% elif current_task == 'cancel_order' %}}
...
{{% endif %}}
{{% endif %}}
```

- `<playbook_list>` and `<task_list>` stay visible while a task is loaded, so
  the model always knows how to switch when the customer changes the subject
  or has a second request. Never wrap `<playbook_list>` in
  `{{% if not current_playbook %}}` or `<task_list>` in
  `{{% if not current_task %}}`; hiding the menus traps the agent inside the
  first task it loads.
- `<anytime_actions>` is static because a customer can ask to end the
  conversation or speak with a representative at any moment. Keeping both
  outside playbooks means either one takes one model call instead of three
  (`open_playbook`, `load_task`, then the handoff). Its last rule covers
  changes no tool can make, so the model offers a representative instead of
  reaching for a tool it can't see.
- `<status>` tells the model where it is. It matters most right after
  `open_playbook`, when the next call should load a task.
- `<pending_handoff>` is the first branch of the task chain, so once a tool
  has decided on a transfer the model doesn't also get the task's current step
  (such as "ask for the ZIP code again"). It earns its place when tools in
  more than one task or block can decide on a transfer: each tool sets a
  reason, and one branch handles them all whatever is loaded. With a single
  trigger, a branch in that step (`{{% elif zip_attempts >= 2 %}}`) does the
  same job.
- `<identify_order>` holds the steps every task starts with. See
  [Shared prerequisite block](#shared-prerequisite-block).
- `<playbook_rules>` holds rules that are the same in every task of a
  playbook: the record stays selected when the customer moves to another
  request about it, how to switch to a different record, and switches that are
  open only in some states (the sample's mail return versus courier pickup,
  gated on `order_status == 'Delivered'`). Keep transfer routing out of it
  unless a playbook really routes elsewhere, and then gate the anytime line
  off with `{{% if %}}` rather than writing "follow the playbook's rule
  instead"; two visible rules and two visible tools for one request leave the
  model to pick.
- Describe each playbook and task in the customer's words, so the lines read
  as triggers for loading them. Where two playbooks overlap, add a note to the
  one that owns the overlap ("questions about the return policy belong here
  even when they name a product"), and when a request could fit either, the
  `<playbook_list>` lead tells the model to ask one short question first.
- Keep the indentation flat. Every rendered line reaches the model, and deep
  indentation only adds whitespace.
- The CES design guide's `<persona>`, `<taskflow>` and `<subtask>` tags aren't
  required. The sample's `cxaslint.yaml` turns off I001, the lint rule that
  asks for them.

## Router tools

```python
PLAYBOOK_TASKS = {
    "order_support": (
        "check_order_status",
        "cancel_order",
        "text_order_summary",
    ),
    "returns": (
        "start_mail_return",
        "schedule_return_pickup",
    ),
}


def load_task(task_name: str, rationale: str) -> dict[str, Any]:
  """Loads the instructions for a task in the currently open playbook.

  Args:
    task_name (str): Exact name of the task to load.
    rationale (str): Why this task is being loaded. Kept in the conversation
      history so later turns know what prompted it. One short sentence (max 15
      words) stating the customer's request or the tool result that led here,
      e.g. "Customer changed their mind and wants to cancel." Do not restate the
      task name or include personal data.
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
            "required_playbook": other_playbook,
        }
    return {
        "status": "error",
        "error": "invalid_task",
        "valid_tasks": list(PLAYBOOK_TASKS[playbook]),
    }
  context.state["current_task"] = name
  return {"status": "ok", "current_playbook": playbook, "current_task": name}
```

`open_playbook` has the same shape over `VALID_PLAYBOOKS` and clears
`current_task` to `""`. Neither router tool returns a prose `"message"`, and
neither echoes `rationale` in the result; the call arguments already keep
`rationale` in the history.

Routers write only `current_playbook` and `current_task`. Clearing a record's
state belongs in the tool that changes the record (`lookup_order` here), not in
`load_task`: an agent that cleared the selected order whenever its lookup task
loaded lost a verified order every time the model reloaded that task, and the
verification counter reset with it. Don't copy `rationale` into state or read it
in code; it's a note the model leaves for its later turns. When routing needs
context, such as whether the customer's "yes" is about the order a greeting
asked about, put a typed flag on the domain tool that uses it
(`lookup_order(use_greeted_order=True)`), so the router stays a plain switch.

## Task blocks: task rules, collected facts and one current step

Every task gets the same shape inside its `<task name="...">` block:

- `<task_rules>` (optional): rules that hold at every step of this task: when
  to switch to a sibling task, do's and don'ts that only apply here, and every
  tool the customer may need out of order. That means each tool that changes
  an earlier answer (an `update_...` intake tool for details given early or
  changed later) and each read-only lookup a customer may ask for early. Rules
  every task in the playbook shares, like `lookup_order` for a different
  order, go in `<playbook_rules>` instead. See
  [Going back to an earlier step](#going-back-to-an-earlier-step).
- `<collected>` (optional): a short list of **confirmed** facts, each wrapped
  in `{{% if ... %}}`, so the model doesn't ask for them again. Never list
  unverified on-file data (such as `on_file_zip` or an unconfirmed street
  address on the account); if the model can see an unverified value in the
  prompt, it can read it aloud before the customer verifies it. Never add a
  `<session_snapshot>` block dumping raw state variables
  (`zip_verified: {{zip_verified}}`); it duplicates what the template branches
  on and leaks internal flag names.
- `<current_step>`: one flat `{{% if ... %}} / {{% elif ... %}}` chain,
  ordered from the first prerequisite to the final state, so **only the active
  unfinished step** renders on any model call.

Render one step at a time by default. When every step is on screen, the tools
for steps the customer hasn't reached are visible too, and the model has to work
out from the history which step comes next; that's where it skips a confirmation
or submits early. Token count is the smaller cost: a short task whose steps have
no irreversible action or word-for-word script (ask two questions, then answer)
can list its steps together in one step. Gate a step whenever it leads to a
submit-style tool or a script. The sample's `schedule_return_pickup` task:

```xml
{{% elif current_task == 'schedule_return_pickup' %}}
<task name="schedule_return_pickup">
<task_rules>
- Whenever the customer gives or changes the return reason, pickup day, time window, or callback phone number before the pickup is booked, even at an earlier step or while reviewing the summary, call {@TOOL: update_return_details} with the details they gave. If the result includes `not_recorded`, explain why those details couldn't be saved yet.
- If the customer asks which pickup days or time windows are open, call {@TOOL: check_return_pickup_availability} at any point and share them. If it returns an error, finish the current step first.
</task_rules>
{{% if return_reason %}}
<collected>
Already collected, so don't ask for these again:
- Return reason: {{return_reason}}.
{{% if return_pickup_day %}}- Pickup day: {{return_pickup_day}}.{{% endif %}}
{{% if return_pickup_window %}}- Time window: {{return_pickup_window}}.{{% endif %}}
{{% if return_contact_phone %}}- Callback phone: {{return_contact_phone}}.{{% endif %}}
</collected>
{{% endif %}}
<current_step>
{{% if return_pickup_completed %}}
- Return pickup {{return_confirmation_id}} is booked for {{return_pickup_day}} in the {{return_pickup_window}} window. If you just booked it, share the confirmation ID and the pickup window.
- The booking can't be changed here. If the customer wants to change or cancel it, offer to connect them with a representative.
{{% elif order_status == 'Processing' %}}
...
{{% elif not return_reason %}}
- Ask why they're returning the {{order_item}}, then call {@TOOL: update_return_details} with `reason` (`damaged`, `wrong_item`, `changed_mind`, or `other`).
{{% elif not return_availability_checked %}}
- Call {@TOOL: check_return_pickup_availability} to get the open pickup days and time windows, then share them with the customer.
{{% elif not return_pickup_day or not return_pickup_window %}}
- Ask which of the open pickup days and time windows (`morning` or `afternoon`) the customer wants, then call {@TOOL: update_return_details} with `pickup_day` and `pickup_window`.
{{% elif not return_contact_phone %}}
- Ask for a 10-digit callback phone number for the courier, then call {@TOOL: update_return_details} with `contact_phone`.
- If it returns `missing_area_code`, ask for the full number with the area code. If it returns `invalid_phone`, ask for a valid 10-digit US phone number.
{{% elif not return_summary_confirmed %}}
- Read back the pickup in one sentence: order {{order_id}} ({{order_item}}), {{return_pickup_day}} in the {{return_pickup_window}} window, callback number {{return_contact_phone}}. Ask the customer to confirm so you can book it, and end your turn to wait for their answer.
- If they change a detail, call {@TOOL: update_return_details} with the change, then read back the updated summary.
- Once they confirm, call {@TOOL: submit_return_pickup} with `summary_confirmed=True`.
{{% else %}}
- The customer confirmed the summary. Call {@TOOL: submit_return_pickup} with `summary_confirmed=True` to book the pickup.
{{% endif %}}
</current_step>
</task>
```

Key details in this step chain:

- **Never write prose state checks on declared variables.** Do not write
  `If zip_verified is false, ask for the ZIP code` in prose; the model
  shouldn't evaluate session state flags itself. Branch on state variables
  with `{{% if ... %}}`, and use prose `If ...` inside a step only for fields
  of that step's tool response (`If it returns invalid_zip...`).
- **Write each branch so it makes sense right after the tool call that leads
  to it.** After a tool updates state, the next model call in the same turn
  already sees the new branch. When a step asks for an action that doesn't
  change anything the chain branches on, have the tool set a flag, or the next
  call is told to do it again. For an action on one order, store that order's
  number rather than `True`, so the flag doesn't carry over to a different
  order: the sample's `text_order_summary` task branches on
  `{{% elif sms_disclosure_played != order_id %}}` and
  `{{% elif sms_summary_sent != order_id %}}`.
- **Check the completed state near the top of the chain.**
  `submit_return_pickup` sets `return_pickup_completed` and resets
  `return_summary_confirmed`. With `{{% elif return_pickup_completed %}}` as
  the first step, the turn after booking lands on the completed step instead
  of the summary step. `update_return_details` refuses edits once the pickup
  is booked, so the collected details can't drift from what was submitted.
- **Switching orders restarts the chain.** `lookup_order` clears the per-order
  state (verification, collected details, completion) when the customer moves
  to a different order, so `<identify_order>` renders again for the new order
  and then the task's chain starts over, without reloading the task. It
  doesn't clear `zip_attempts`: `verify_order_zip` counts failures for the
  whole conversation, because a count that resets on a switch lets a customer
  look up another order, come back, and guess again.
- **End the turn at confirmations.** A step that reads something back or asks
  the customer to choose says "and end your turn". Without it, models tend to
  run the lookup, the selection and the submit in one turn and answer the
  question for the customer. In one agent the model kept calling the select
  tool in the same turn as the lookup until the step said to describe one
  record, ask "Is that the one?", end the turn, and never call the select tool
  in the same turn as the lookup; after that it waited in 8 of 8 runs.
- **Task-only rules live in the task.** Safety warnings or things never to
  promise that only apply to one task go in its `<task_rules>`, which keeps
  `<constraints>` short and the rule out of every other turn.
- **Open-ended work can be one step.** Troubleshooting or a run of questions
  and answers doesn't need a state variable per exchange. Give the task one
  step that says how to go through it (one fix at a time, wait while the
  customer tries it) and how it ends: which tool or task comes next when it's
  resolved and when it isn't.
- **No realistic sample confirmation codes in instructions or docstrings.**
  Never write example codes like `"such as NQAA123456"` in `instruction.txt`
  or tool docstrings; when a lookup fails or the model gets confused, it can
  read the example code aloud to the customer as if it were real. Describe the
  format in words instead.

## Shared prerequisite block

Many agents make every task start the same way: find the record and verify the
customer. When that takes more than one tool call (a lookup, a verification with
retries, a second number to try, picking among several matches), write it once
instead of copying it into each task. Agents whose tasks don't share opening
steps don't need any of this.

There are two ways to write it once, and the right one depends on the agent. The
sample uses a shared block, `<identify_order>`, the branch right after
`<pending_handoff>`:

```xml
{{% elif current_task and not zip_verified %}}
<identify_order>
Every task starts by finding the customer's order and verifying its shipping ZIP code. Keep the loaded task. Once the ZIP code is verified, the task's next step shows here.
<current_step>
...
</current_step>
</identify_order>

{{% elif current_task %}}
<playbook_rules>...</playbook_rules>
{{% if current_task == 'check_order_status' %}}
...
```

The customer's task stays loaded the whole time, so `<status>` still shows what
they asked for, and once `verify_order_zip` sets `zip_verified` the next model
call in the same turn gets that task's first step. Each task's chain starts
after the prerequisite and never repeats it.

The other way is a separate lookup task that the other tasks load first and then
come back from: their first step says "no order is selected yet, load the lookup
task, then come back to this task", and the lookup task's done step loads the
task the customer asked for. Each request then costs a `load_task` to the lookup
and another back, and the model has to remember what the customer first asked
for. In return the lookup gets its own `<task_rules>` and its own line in
`<task_list>`. That suits a long identification flow (caller ID, a second
number, several matches, not found), a lookup the customer asks for on its own
("help with a different order"), or a playbook where only some tasks need it.
Prefer the block when every task needs the same few steps and the customer never
asks for them alone.

Keep record-specific details out of the lookup's result until the customer is
verified. The sample's `lookup_order` returns only `found` and the order number
and keeps the item, status and delivery in state; `verify_order_zip` returns
them once the ZIP code matches, and the task steps render them from state.
Return them from a tool even though the steps already show them: when the
details only appeared in the instruction, `gemini-composite-v2` sometimes called
a tool that doesn't exist (`get_order_details`, `fetch_order_status`) to look
them up, which fails the turn.

When a lookup can return several records (orders by phone number, appointments
on an account), offer one at a time, ask "Is that the one?" and end the turn.
Then have a tool that selects the record take its ID, match it only against the
records the lookup stored in state, and on a miss return what the agent may read
out rather than the full list of IDs. Past a handful of matches, ask the
customer to narrow it down instead of reading them all.

## Going back to an earlier step

Customers change their minds: a different order number at the confirmation, a
new pickup day while hearing the summary, the real reason after the cancel went
through, or a question that belongs to a later step. A step chain handles this
without a "back" mechanism, because the chain is computed from state on every
model call. There is no step pointer to rewind. Going back means calling a tool
that changes an earlier answer, and the earlier step renders again on the next
model call in the same turn. Four rules keep that working:

1. **Steps gate instructions and irreversible actions, not corrections.** Name
   every tool that changes an earlier answer where it's visible at every step:
   `lookup_order` for a different order in `<playbook_rules>`, because every
   task needs it, and `update_return_details` for details given early or
   changed later in that task's `<task_rules>`. Do the same for read-only
   lookups a customer may ask for early, like
   `check_return_pickup_availability`. Only submit-style tools
   (`submit_cancellation`, `create_mail_return`, `submit_return_pickup`) and
   word-for-word scripts stay inside their own step. When an edit tool is named
   only in its own step, the model sees its earlier calls in the history, calls
   it anyway, and CES fails the turn with "unexpected function call" (the
   customer hears a fallback such as "Hmm, I'm having trouble with that").
1. **Every edit clears what depended on the old value, and only that.** In the
   sample, `lookup_order` clears the old order's ZIP verification, collected
   details and completion flags when the order changes, and
   `update_return_details` resets `return_summary_confirmed` when a detail
   changes. Otherwise the chain keeps a stale flag and skips a step it should
   redo, which is worse than a visible error because nothing looks wrong. Two
   things must not be cleared by an edit: attempt counters, which only the
   verifying tool changes (or a switch hands out fresh guesses), and done flags
   that belong to another record. Scope each done flag to its record, by
   storing the record's ID (`sms_summary_sent` holds the order number) or by
   clearing it when the record changes; a plain `True` blocks a second booking
   for a different record. Add a column for what each correcting tool clears to
   the plan table, so it's decided before the code is written.
1. **Every done step says what can still change.** After the final action,
   either name the tool that can still change something, or say that nothing
   can and what to offer instead. In tests, done steps without that line got
   improvised handling (a transfer without asking, or a made-up reason why the
   change wasn't possible), while the done step that said "offer to connect
   them with a representative" was followed. That line depends on the agent's
   tools: the sample's done steps say the cancellation, the mail return and the
   booked pickup can't be changed because the sample has no tool for it, and an
   agent with a reschedule or amend tool names that tool there instead.
1. **A catch-all covers the rest.** The last rule in `<anytime_actions>` tells
   the model what to do when no playbook or task covers the change: say so and
   offer a representative, instead of calling a tool it can't see. Keep it
   general. It's static, so the model applies it in every state, and an example
   such as "changing a request that was already submitted" would tell the model
   that submitted requests can't change, even in an agent that has a tool for
   it. Say what can't change in the done step where it's true.

Letting the model call a visible tool before its step is cheap when the tools
validate their prerequisites. In one test the model called
`verify_order_zip(postal_code="")` before the customer gave a ZIP code, got
`invalid_zip` back, and asked for it. Calling a hidden tool always fails the
turn. Make out-of-order tools safe to call and keep them visible, rather than
hiding them and relying on the model never to reach for them.

Test corrections the way you test the happy path: change the first answer at the
last step, change a detail while reviewing a summary, ask for a change after the
final action, and ask a later step's question early.

## Tool design: facts-only results, typed args, and state-backed actions

### 1. Return facts and status codes, never instructions or full-state echoes

Tool responses stay in `llm_request.contents` for the rest of the conversation.
When a tool returns prose instructions
(`"message": "Now ask the customer for their callback phone number"` or
`"next_step_guidance": "..."`), that text stays in the history on every later
turn and contradicts later steps when the customer changes their mind or
switches tasks.

- Return structured status codes and the data behind them
  (`{"status": "zip_mismatch", "verified": False, "attempts_remaining": 1}`),
  and let the active step in `instruction.txt` tell the model how to respond.

- Return the facts the agent will share in a tool result, even when a step
  also renders them from state. Models expect what they tell the customer to
  come from a tool call. When the sample's order details only appeared in the
  instruction, `gemini-composite-v2` called a lookup tool that doesn't exist
  in about 1 of 12 status checks, and rewording the step or the grounding
  constraint didn't stop it; once `verify_order_zip` returned the details, 16
  of 16 passed.

- In intake/update tools (`update_return_details`), return only the fields
  that call updated (`{"status": "updated", "updated": updated}`) plus
  `not_recorded` if co-batched fields were skipped because an earlier step
  (like `check_return_pickup_availability`) hasn't run yet. Do not echo the
  entire session state dictionary on every update call.

- Keep unverified on-file secrets out of tool return dicts. In the sample,
  `lookup_order` saves `on_file_zip` in `context.state["on_file_zip"]` for
  `verify_order_zip` to check, and returns only `found` and the order number;
  the details come back from `verify_order_zip` once the ZIP code matches. If
  a tool looks up an account number or phone number from caller ID, store the
  full value in state for backend calls and return only a masked value (such
  as `account_last4`) to the model.

- Build every result from named fields. Returning a backend response as is, or
  spreading it into the result (`{"status": "ok", **data}`), puts every field
  it has in the history for the rest of the conversation, including on-file
  data nobody verified, and the result changes whenever the backend does.

- Fail closed on backend data as well as on missing prerequisites. An
  eligibility flag that's missing from the response means not eligible
  (`.get("is_eligible_for_cancel", False)`, never `True`), and success needs
  an explicit positive field, not just the absence of an error.

- Put business rules over looked-up data in code. Whether an order can be
  cancelled, whether a return is inside its window or which repair path a
  warranty allows is policy, not a conversational decision. Have the tool work
  it out and return a closed-set code (per item when items differ), and have
  the step say what to tell the customer for each code. Leave one or two
  simple conditions to a Jinja branch on state; a tool adds a model step. When
  the outcome needs facts only the customer can give (where they bought it,
  whether it was dropped), collect each one with a typed parameter and let the
  tool combine them, rather than writing a prose decision tree for the model
  to evaluate.

- Answer knowledge-base questions only from the result. Have the search tool
  return the answer text, a source title and `answer_found`, and give the step
  an explicit not-found branch: name the topic and offer a representative,
  never an answer from the model's own knowledge. Models fill a miss with
  plausible policy if the step doesn't say what to do.

### 2. Use explicit parameter types; never match phrases in code

Never inspect customer utterances or model replies with regexes, keyword lists,
or substring checks in callbacks or tools. If a tool needs a classification or a
yes/no decision, make that an explicitly typed tool parameter so the model
classifies what the customer meant:

- `bool` for confirmations and binary flags (`confirmed: bool = False`,
  `summary_confirmed: Optional[bool] = None`).
- `int` or `float` for counts and amounts.
- `Literal[...]` for closed sets
  (`reason: Literal["damaged", "wrong_item", "changed_mind", "other"]`,
  `pickup_window: Optional[Literal["morning", "afternoon"]] = None`).
- `Optional[T] = None` for optional fields instead of sentinel strings like
  `"none"` or `"skip"`, and never `Union[bool, str]`.

Tool code should only validate argument formats that require exact rules (such
as checking that a US ZIP code is 5 digits in `verify_order_zip`, or
distinguishing a 7-digit `missing_area_code` from `invalid_phone` in
`update_return_details`). Format errors should return a distinct status code
without counting against mismatch retry limits.

### 3. Read prerequisites from state in final action tools

Irreversible action tools (`submit_cancellation`, `create_mail_return`,
`submit_return_pickup`) should read the target `order_id` and collected fields
from `context.state` rather than trusting LLM-copied parameters, and fail closed
if any prerequisite flag (`zip_verified`, `return_availability_checked`,
`return_summary_confirmed`) is missing.

Accept the final confirmation flag (`summary_confirmed: bool = False`) on the
submission tool itself as well as on `update_return_details`. That way, when the
customer says "Yes, book it", the model can call
`submit_return_pickup(summary_confirmed=True)` directly in one step without
needing two sequential tool calls or racing parallel state writes. Any edit to a
booking field in `update_return_details` resets
`return_summary_confirmed = False` so the updated summary is read back before
booking.

Once the action is done, an intake tool shouldn't change the collected details
on its own, or the details the instruction reads back stop matching what was
submitted. The sample has no way to change a booked pickup, so after booking
`update_return_details` and `submit_return_pickup` return
`pickup_already_booked`, and the completed step offers a representative. If your
backend can change a submitted request, add a tool that makes the change there
and updates state in the same call (a reschedule tool, for example), and name it
in the done step instead.

### 4. Tool-triggered handoffs via `pending_handoff_reason`

When a tool determines that the conversation must be handed off to a live
representative (for example, after two non-matching ZIP attempts in
`verify_order_zip`), it should not call `set_next_agent_actions` directly or
return a prose prompt. Instead:

1. The tool sets
   `context.state["pending_handoff_reason"] = "zip_verification_failed"` and
   returns `{"status": "verification_failed", "verified": False, "attempts_remaining": 0}`.

1. On the next model call in the same turn, the first branch of the task chain
   renders in place of the task block:

   ```xml
   {{% if pending_handoff_reason %}}
   <pending_handoff>
   A previous step requires a live representative (reason: `{{pending_handoff_reason}}`). Call {@TOOL: execute_live_agent_handover} right away without replying first, and pass a one-sentence `pre_transfer_message` letting the customer know you're connecting them.
   </pending_handoff>
   {{% elif current_task == 'check_order_status' %}}
   ...
   ```

   The task's current step doesn't render, so the model isn't told to ask for
   the ZIP code again and to transfer at the same time.

1. The model calls `execute_live_agent_handover`, which reads and clears
   `pending_handoff_reason` from state and queues the transfer message and
   `end_session`.

`execute_live_agent_handover` also takes a required `conversation_summary` (what
the customer wanted, what was done, what's still open). The model already knows
the conversation, so writing it costs a few dozen tokens on the transfer turn,
and the representative doesn't have to ask the customer to start over. The
sample stores it in the `handoff_summary` session variable; pass it on in
whatever your contact center integration hands to the representative.

Not every limit has to end in a transfer. When the agent can carry on without
the fact (a guest flow, a self-service link), have the tool count the attempts
and branch the step on the count instead of setting `pending_handoff_reason`.

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

- Match on word boundaries. The built-in `end_session` is a substring of
  `graceful_end_session`; a substring check would expose it whenever the
  goodbye tool is visible, and the model can then end the conversation without
  the goodbye message.
- CES does not strip `{{# ... #}}` or `{# ... #}` comments, so both the model
  and the callback see them verbatim, and tag names count too: a block called
  `<end_session>` keeps `end_session` visible on every turn.
- Any mention counts, not just `{@TOOL: name}`. A sentence like "answer from
  the get_appointments result" shows `get_appointments` in that branch, and
  the model can call it again mid-flow and replace what it found. Refer to an
  earlier result by what it holds ("the appointment you found"), and name a
  tool only as `{@TOOL: name}` where the model may call it.
  `check_tool_visibility.py` reports bare names.
- Static text decides what's visible at the start. Only `open_playbook` and
  the tools named in `<anytime_actions>` belong there. An agent may need more
  than the goodbye and the handoff from every state (a transfer to another
  language, for example); name it in `<anytime_actions>` with its rule, not in
  `<constraints>`, so there's one rule per trigger.
- A task list stays visible while its playbook is open, so a tool named like a
  task is visible before the task loads. The sample's `cancel_order` task uses
  a `submit_cancellation` tool for this reason.
- A tool is visible only while the rendered branch mentions it. If a branch
  drops the only mention of a tool the model used earlier, the model may call
  it anyway from the history and the platform fails the turn with "unexpected
  function call". Keep the tools that change earlier answers and the read-only
  lookups where every step sees them (`lookup_order` in `<playbook_rules>`,
  `update_return_details` and `check_return_pickup_availability` in the task's
  `<task_rules>`) so the customer can switch orders, update a field or ask a
  question at any step, and keep the tool list rule at the end of
  `<task_list>`.
- Register every tool in the root agent's `tools` list, including the
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

If the greeting asks a question ("Are you calling about the order arriving
today?"), the question is already in the history as the agent's own reply, so
the model sees what it asked. Add rules only for answers it can't work out, such
as what to do with a bare "yes", and gate them with
`{{% if not current_playbook %}}` so they disappear once the customer's request
is under way. Otherwise they stay in the prompt and compete with every later yes
or no question.

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
  order_id = context.state.get("order_id") or ""
  if not order_id:
    return {"status": "error", "error": "no_active_order"}
  context.state["sms_disclosure_played"] = order_id
  tools.set_next_agent_actions({
      "customized_response": {"content": DISCLOSURE_TEXT, "disable_barge_in": True},
  })
  return {"status": "terms_shared", "order_number": order_id}
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

`graceful_end_session(goodbye_message)` queues the goodbye with
`{"reason": "completed", "escalated": False}`, and
`execute_live_agent_handover(conversation_summary, reason, pre_transfer_message)` queues the message with `"escalated": True` so the
conversation counts as escalated. Both docstrings tell the model to call the
tool without replying first, so the customer doesn't get a reply and then a
separate goodbye. Gate follow-up steps on the state variable the tool sets (the
sample's `text_order_summary` task shows `send_sms_summary` only once
`sms_disclosure_played` holds the loaded order's number), and have the follow-up
tool check it too.

- The queued text can include values from state or a tool result, like an
  order number or a confirmation ID. What matters is that the model doesn't
  get to rephrase it. Don't return the text instead (`"phrase": ...` with "say
  it exactly as returned"): the model can still reword it, and the text sits
  in the history as tool output rather than as the agent's reply. Only the
  goodbye and handoff tools queue `end_session`.
- One tool calls another with `tools.<tool_name>({...})`, arguments in one
  dict, the way the sample's tools call `set_next_agent_actions`. The call
  returns a response object, and `.json()["result"]` is the other tool's
  return value. Code can call any registered tool, whether the model can see
  it or not. The nested call shows up under the outer tool in
  `cxas trace get`, but not as a separate call in the conversation history. To
  share logic between tools, put it in a helper tool, register it, and keep
  its name out of the instruction so the model never sees it.
  `check_tool_visibility.py` counts a tool as used when code calls it this way
  or a callback returns `Part.from_function_call(name="...")` for it.

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

Never add `after_model_callbacks` to inspect or rewrite model replies with
regexes, and never append dynamic rule blocks to `system_instruction` inside
`before_model_callback`. Any rule that depends on session state belongs in
`instruction.txt` gated by `{{% if ... %}}`, where `check_tool_visibility.py`
can render and verify it.

## Platform gotchas

- **Template expressions in `{{ ... }}` output tags:** CES uses a restricted
  template engine (`FullTemplateEngine`). Inside `{{% if ... %}}` control
  blocks it supports `if`/`elif`/`else`, `and`, `or`, `not`, `in`, `==`, `!=`
  and dot access, but rejects `not in` (write `not a in ['x', 'y']`) and
  subscripts like `order['status']` (use `order.status`). Inside `{{ ... }}`
  output tags, CES **only** substitutes bare variables (`{{var}}`, `{{a.b}}`);
  expressions such as `{{ x or "none" }}` or `{{ "a" if c else "b" }}` are not
  evaluated and are sent to the model as literal `{{ ... }}` code. Standard
  Python `jinja2` evaluates those expressions, so `check_tool_visibility.py`
  explicitly flags non-bare `{{ ... }}` output tags and leaves them literal
  when rendering.

- **Single-brace blocks and variables:** Single-brace `{var}` is never
  substituted, and single-brace control blocks (`{% if ... %}`) make the
  platform attach `<state_update>` notes to the start of the turn. Always use
  double-brace control blocks (`{{% if ... %}}`) and double-brace variables
  (`{{var}}`). Never write `{{var}}` inside a tool-call example in
  `instruction.txt` (such as `call tool(id='{{order_id}}')`) when `order_id`
  can be empty, because it renders as `call tool(id='')`.

- **`Part.from_end_session` arguments:** `Part.from_end_session` takes
  `reason` and `escalated`. Passing `session_escalated` raises a `TypeError`,
  the platform answers with a fallback line, and the queued action stays in
  state, so every later turn fails the same way until it's cleared. The
  `end_session` call it builds does carry `session_escalated`, which is why
  traces and `run_locally.py` show that name.

- **Variable declarations:** Declare every variable in `app.json`
  `variableDeclarations`. Undeclared tool-written variables are dropped
  between turns, and an undeclared variable in the instruction always renders
  empty, which silently flips its branch. `cxas lint` (V100) checks state
  reads and writes in code, and `check_tool_visibility.py` checks the
  variables the instruction uses. `NUMBER` variables come back as floats.

- **`current_date`:** CES provides `current_date` without a declaration, in
  the app's time zone, rendered like `2026-10-16[America/New_York]`. The
  sample puts `Today's date is {{current_date}}.` in `<context>`. Python tools
  run on UTC, so a tool that needs today's date should read
  `context.state["current_date"]` instead of calling `datetime.date.today()`.
  `cxas lint` (V100) only knows it's built in for the instruction, so don't
  declare it; turn V100 off for that tool's file in `cxaslint.yaml`:

  ```yaml
  per_file:
    # Reads current_date, which CES provides without a declaration.
    "tools/<name>/python_function/python_code.py":
      V100: "off"
  ```

  The local scripts fill `current_date` in from your machine's clock; pass it
  in `--state` to test a specific day.

- **Tool docstrings and JSON sync:** The model-facing tool description comes
  from the Python docstring. Keep docstrings focused on what the tool and its
  parameters mean rather than repeating conversation policy, and keep each
  tool JSON's `pythonFunction.description` in sync with
  `scripts/sync_tool_descriptions.py`.

- **Entry function ordering (T004):** Put each tool's entry function, named
  like the tool, first in `python_code.py` and helpers after it, or
  `cxas lint` warns (T004).

- **Linter configuration:** The sample's `cxaslint.yaml` turns off four lint
  rules that don't fit this design (I001, T001, T011, I012), with the reason
  next to each. Keep it in the app directory; every other rule stays on, apart
  from per-file entries like the `current_date` one above.

- **Deployment propagation:** After `cxas push`, wait about a minute before
  testing. `cxas push` doesn't change an existing app's display name; rename
  it in the console.

## Sample agent

`resources/sample-agent` is an online store support agent with one root agent,
`Support_Agent`, on `gemini-composite-v2`.

| Playbook        | Task                     | Tools the task uses                                                                 |
| --------------- | ------------------------ | ----------------------------------------------------------------------------------- |
| `order_support` | `check_order_status`     | none (details render from state)                                                    |
| `order_support` | `cancel_order`           | `submit_cancellation`                                                               |
| `order_support` | `text_order_summary`     | `play_sms_legal_disclosure`, `send_sms_summary`                                     |
| `returns`       | `start_mail_return`      | `create_mail_return`                                                                |
| `returns`       | `schedule_return_pickup` | `update_return_details`, `check_return_pickup_availability`, `submit_return_pickup` |

Every task starts in `<identify_order>` with `lookup_order` and
`verify_order_zip`, and `lookup_order` stays visible in `<playbook_rules>`
afterwards. `open_playbook`, `graceful_end_session`, and
`execute_live_agent_handover` are always visible, and `load_task` appears once a
playbook is open. Two non-matching ZIP codes in one conversation hand the
customer to a representative. `lookup_order` uses sample data:

- Order `1001`: `Processing` (wireless headphones, ZIP `94043`)
- Order `1002`: `Shipped` (coffee grinder, ZIP `98101`)
- Order `1003`: `Delivered` (desk lamp, ZIP `10001`)
- Order `1004`: `Delivered` (running shoes, ZIP `60601`)
