# Guide: self-steering agents on CXAS

This is the reference behind the rules in `SKILL.md`. The core starter code
comes from `resources/sample-agent/`; domain-neutral examples below illustrate
how to add domains, workflows, domain tools, and state variables.

## Why the design looks like this

- **The model steers the conversation, like a contact center agent.** A new hire
  gets a short briefing, a workflow guide for each kind of request, a status
  panel showing where the case stands, a few scripts to read word for word, and
  buttons to transfer or end the conversation. Nobody hands them every workflow
  at once or acts behind their back. The design gives the model the same job:
  the power to make every conversational decision through tools, and the
  awareness to know where it is, what it can do right now, and why it did
  things before. Progressive disclosure is the workflow guide; deterministic
  actions are the scripts and buttons.
- **Why `domain` and `workflow` (and when to use 1-level `--single-level`).**
  Across `160` metaprompting runs on Gemini models (`gemini-4-argon`,
  `gemini-4-carbon`, `gemini-4-hydrogen`, `gemini-3.8-flash`), calling an empty
  outer category a "playbook" and the inner multi-step guide a "task" is
  semantically inverted (`0/120` blind preference; `0/40` rated natural): in
  SRE, automation playbooks, contact-center SOPs, and Dialogflow CX priors, a
  playbook/workflow is the executable step-by-step guide, whereas a task is a
  single atomic step. Using `select_domain` for the outer business area and
  `load_workflow` for the inner step-by-step guide matches Gemini's native
  hierarchy priors (`Selected Domain: "X". No workflow is loaded yet.`).
  Furthermore, when an agent has `<= 12` total workflows, dropping the outer
  domain hop altogether (`--single-level`, using a single always-visible
  `<workflow_list>` and `load_workflow`) cuts Turn-1 latency by `21%-33%`
  (`510-775 ms`) and router hops by `42%-55%` while preserving dynamic tool
  hiding. See [references/terminology_benchmark.md](terminology_benchmark.md)
  for the full empirical study and live CES benchmark results.
- **The model only knows what's in `llm_request`.** Each turn it gets the
  system instruction with its tool declarations, and the history in
  `llm_request.contents` (customer messages, agent replies, tool calls, and
  results). When an agent misbehaves, read that request for the turn before
  changing anything. The cause is almost always stale, conflicting, or
  overloaded context, not a model that can't do the job.
- **Conversational agents have little time to think.** Replies have to come
  within seconds, so keep each turn's instruction and tool list to what the
  current step needs. Unrelated rules, future steps, and extra tools add tokens,
  slow the first token, and give the model more ways to pick the wrong thing.
- **Every turn is a new person.** The model's reasoning from earlier turns isn't
  in the history; it sees what the agent said and which tools it called, but not
  why. If the history shows an action the current instruction never mentions,
  the model learns that it may improvise. Keep the instruction and the history
  consistent, and leave a `rationale` on routing calls.
- **Keep the model in the loop.** When code sends text or ends the
  conversation, do it through a tool the model calls, so the history shows the
  model's own call, a result saying what happened, and the text as the agent's
  reply. Never tell the model "a callback did X"; it doesn't know what a
  callback is.
- **One root agent for the conversation.** A sub-agent handoff is a
  `transfer_to_agent(agent_name: "...")` call, and earlier turns show up as
  `[Other Agent] said: ...`. That reads like a transfer to another person, so
  the model announces it, and "don't mention the transfer" rules then contradict
  the tool name and history. Even when an app attaches platform guardrails with
  a dedicated `transferAgent` in `childAgents`, the callback must hide
  `transfer_to_agent` / `transferToAgent` from the root agent so only
  deterministic guardrails can invoke it.

## Instruction structure

Use clean, flat top-level XML blocks rather than deeply nested wrappers. Static
blocks come first and state-dependent blocks come after them, so the model reads
from the general to the specific: who it is, the rules, what it can always do,
the menus, where it is now, and what to do now. Never use `<context>` in
`instruction.txt` (`cxas lint` `I015` bans it as an internal platform tag); put
`Today's date is {{current_date}}.` inside `<status>`:

Block | Rendered when | What it holds
--- | --- | ---
`<role>` | always | Who the agent is and what it helps with.
`<constraints>` | always | Reply style and hard rules.
`<anytime_actions>` | always | Goodbye, transfer, and unsupported-request handling.
`<domain_list>` | always (2-level mode) | Available domains, and when to call `select_domain`.
`<workflow_list>` | `current_domain` set (2-level) or always (1-level) | Available workflows, when to call `load_workflow`, and the tool-list rule.
`<status>` | always | `Today's date is {{current_date}}.`, selected domain (if 2-level), and loaded workflow.
`<pending_handoff>` | `pending_handoff_reason` set | Takes the workflow's place and tells the model to transfer now.
`<workflow name="...">` | that workflow is loaded | `<workflow_rules>`, optional `<collected>`, and `<current_step>`.

```xml
<role>...</role>
<constraints>...</constraints>

<anytime_actions>
- When the customer says they are all set and don't need anything else, call {@TOOL: graceful_end_session} right away without replying first, and pass a short, warm goodbye in `goodbye_message`. You don't need to select a domain or load a workflow to end the conversation.
- When the customer asks for a live representative, call {@TOOL: execute_live_agent_handover} right away without replying first. Pass `reason="customer_requested_agent"` and a one-sentence `pre_transfer_message` letting the customer know you're connecting them.
- When the customer wants a change that none of your domains or workflows cover, don't call a tool that isn't in your tool list. Tell them plainly that you can't make that change and offer to connect them with a representative. Call {@TOOL: execute_live_agent_handover} only if they say yes.
</anytime_actions>

<domain_list>
When the customer asks for something, silently call {@TOOL: select_domain} with the domain that covers it, before replying or calling any other tool. Do the same when they move on to something a different domain covers.
- `account_support`: Checking a record's status, updating contact details, or sending a confirmation summary.
- `service_requests`: Opening a follow-up request or scheduling a service appointment on a completed record.
- `live_agent`: Connecting the customer with a live representative.
</domain_list>

{{% if current_domain %}}
<workflow_list>
Workflows in the selected domain (`{{current_domain}}`). Silently call {@TOOL: load_workflow} with the workflow that matches the customer's request, before replying or calling any other tool. Do the same when they move on to a different workflow.
{{% if current_domain == 'account_support' %}}
- `check_record_status`: Look up a record and share its current status and details.
- `update_contact_email`: Update the contact email address on a record.
{{% elif current_domain == 'live_agent' %}}
- `live_agent_escalation`: Connect the customer with a live representative.
{{% endif %}}
Your tool list only has the tools for the loaded workflow. Never call a tool that isn't in your tool list, even if you called it earlier in the conversation. If you need one that isn't there, first silently call {@TOOL: load_workflow} with the workflow that uses it (call {@TOOL: select_domain} first if that workflow is in a different domain), then call the tool.
</workflow_list>
{{% endif %}}

<status>
Today's date is {{current_date}}.
{{% if current_domain %}}
Selected Domain: "{{current_domain}}". If the customer asks for something covered by a different domain, silently call {@TOOL: select_domain} with that domain first, before replying or calling any other tool.
{{% if current_workflow %}}
Loaded Workflow: "{{current_workflow}}". To switch to a different workflow in this domain, silently call {@TOOL: load_workflow} with the exact workflow name first, before replying or calling any other tool.
{{% else %}}
No workflow is loaded yet. Silently call {@TOOL: load_workflow} with the exact workflow name from the list above first, before replying or calling any other tool.
{{% endif %}}
{{% else %}}
No domain is selected yet. Silently call {@TOOL: select_domain} with the matching domain first when the customer states what they need.
{{% endif %}}
</status>

{{% if pending_handoff_reason %}}
<pending_handoff>
A previous step requires a live representative (reason: "{{pending_handoff_reason}}").
Call {@TOOL: execute_live_agent_handover} right away without replying first, and pass a one-sentence `pre_transfer_message` letting the customer know you are connecting them.
</pending_handoff>
{{% elif current_workflow == 'live_agent_escalation' %}}
<workflow name="live_agent_escalation">
<workflow_rules>
- Connect the customer with a live representative when they ask for one or when their request requires human help.
</workflow_rules>
<current_step>
- Call {@TOOL: execute_live_agent_handover} right away without replying first. Pass a short `reason` and a one-sentence `pre_transfer_message` letting the customer know you are connecting them.
</current_step>
</workflow>
{{% elif current_workflow == 'check_record_status' %}}
<workflow name="check_record_status">
<workflow_rules>
- If the customer asks about a different record, call {@TOOL: lookup_record} with that record ID.
</workflow_rules>
<current_step>
{{% if not account.id %}}
- Ask for the record ID if the customer hasn't shared it, then call {@TOOL: lookup_record} with it.
{{% else %}}
- Share the summary and status ({{account.status}}) for record {{account.id}}.
{{% endif %}}
</current_step>
</workflow>
{{% endif %}}
```

- `<domain_list>` and `<workflow_list>` stay visible while a workflow is
  loaded, so the model always knows how to switch when the customer changes the
  subject or has a second request. Never wrap `<domain_list>` in
  `{{% if not current_domain %}}` or `<workflow_list>` in
  `{{% if not current_workflow %}}`; hiding the menus traps the agent inside
  the first workflow it loads.
- In **1-level direct mode (`--single-level`)**, `<domain_list>` and
  `current_domain` are omitted, and `<workflow_list>` is rendered
  unconditionally at the top level so the agent loads any workflow in a single
  router tool call (`load_workflow`). Use 1-level mode whenever the agent has
  `<= 12` workflows, and 2-level mode (`select_domain` + `load_workflow`) when
  grouping `15+` workflows across multiple domains.
- `<anytime_actions>` is static so saying goodbye or handing off on an immediate
  live-agent or unsupported request takes one model call instead of three
  (`select_domain`, `load_workflow`, then the handoff). Keep the catch-all free
  of domain examples; state what can't change inside the done step where that
  rule applies.
- `<pending_handoff>` overrides any active workflow whenever a domain tool sets
  `pending_handoff_reason`.
- `check_tool_visibility.py` automatically detects all three topologies (2-level
  `select_domain` + `load_workflow`, 1-level `load_workflow`, and legacy
  `open_playbook` + `load_task`) so existing apps continue to validate without
  rewrites.

### Shared prerequisite blocks

When multiple workflows in the same domain require the same prerequisite step
(for example, authenticating the caller or looking up a record before viewing
or modifying it), extract that gate into one shared Jinja-guarded block instead
of duplicating identical lookup logic inside every `<workflow>`:

```xml
{{% if current_domain == 'account_support' and not account.id %}}
<shared_prerequisite name="account_lookup">
- Ask for the customer's account ID if they have not shared it, then call {@TOOL: lookup_account} with it.
</shared_prerequisite>
{{% elif pending_handoff_reason %}}
...
{{% elif current_workflow == 'check_record_status' %}}
...
{{% endif %}}
```

Because `account.id` is checked in one place, adding a third workflow to
`account_support` reuses the exact same verification behavior with zero drift.

## Router tools

The starter's `select_domain` and `load_workflow` check exact names, return
fact-only status/error dicts (no conversational `"message"` prose keys), and
declare explicit parameter types plus `-> dict[str, Any]` (`T016`):

```python
DOMAIN_WORKFLOWS = {
    "live_agent": ("live_agent_escalation",),
}


def load_workflow(workflow_name: str, rationale: str) -> dict[str, Any]:
  """Loads the instructions for a workflow in the currently selected domain.

  Args:
    workflow_name (str): Exact name of the workflow to load.
    rationale (str): Why this workflow is being loaded. Kept in the conversation
      history so later turns know what prompted it. One short sentence (max 15
      words) stating the customer's request or the tool result that led here,
      e.g. "Connect the customer with a live representative." Do not restate the
      workflow name or include personal data.

  Returns:
    dict[str, Any]: Status and the loaded workflow, or an error code with the
      required domain or valid workflows.
  """
  domain = context.state.get("current_domain") or ""
  if domain not in DOMAIN_WORKFLOWS:
    return {
        "status": "error",
        "error": "no_selected_domain",
    }

  name = workflow_name.strip()
  if name not in DOMAIN_WORKFLOWS[domain]:
    for other_domain, workflows in DOMAIN_WORKFLOWS.items():
      if name in workflows:
        return {
            "status": "error",
            "error": "workflow_in_different_domain",
            "required_domain": other_domain,
        }
    return {
        "status": "error",
        "error": "invalid_workflow",
        "current_domain": domain,
        "valid_workflows": list(DOMAIN_WORKFLOWS[domain]),
    }

  context.state["current_workflow"] = name
  return {
      "status": "ok",
      "current_domain": domain,
      "current_workflow": name,
  }
```

`select_domain` follows the same shape over `VALID_DOMAINS = ("live_agent",)`:
it returns `{"status": "error", "error": "invalid_domain", "valid_domains": list(VALID_DOMAINS)}`
on a miss, or sets `current_domain = name`, clears `current_workflow = ""`, and
returns `{"status": "ok", "current_domain": name}`. In `--single-level` mode,
`load_workflow` checks `VALID_WORKFLOWS` directly without `current_domain`. Keep
tool returns fact-only (`status`, `error`, and domain data) so the model reads
`instruction.txt` for what to say instead of parroting a `"message"` field from
tool JSON. Don't echo `rationale` in the result; the call arguments already
keep it in history.

## Workflow blocks: rules, collected facts, and one current step

Every workflow gets the same shape inside its `<workflow name="...">` block:

- `<workflow_rules>` (optional): rules that hold at every step—when to switch
  to a sibling workflow, and every tool the customer may need out of order
  (tools that change an earlier answer plus read-only lookups customers may ask
  early).
- `<collected>` (optional): a short list of **confirmed** facts, each wrapped in
  `{{% if ... %}}`, so the model doesn't ask for them again. Never list
  unverified on-file data (such as `on_file_postal_code` or an unconfirmed
  street address on the account), and never add a `<session_snapshot>` variable
  dump.
- `<current_step>`: one flat `{{% if ... %}} / {{% elif ... %}}` chain ordered
  from the first prerequisite to completion, so **only the active unfinished
  step** renders on any model call.

Example of a multi-step `schedule_appointment` workflow:

```xml
{{% elif current_workflow == 'schedule_appointment' %}}
<workflow name="schedule_appointment">
<workflow_rules>
- If the customer prefers to open a standard follow-up request instead of booking an appointment, silently call {@TOOL: load_workflow} with `open_follow_up` first.
- If the customer means a different record, call {@TOOL: lookup_record} with that record ID first.
- Whenever the customer gives or changes the appointment reason, preferred day, time window, or callback phone number before the appointment is booked, even at an earlier step or while reviewing the summary, call {@TOOL: update_appointment_details} with the details they gave. If the result includes `not_recorded`, explain why those details couldn't be saved yet.
- If the customer asks which appointment days or time windows are open, call {@TOOL: check_appointment_availability} at any point and share them. If it returns an error, finish the current step first.
</workflow_rules>
{{% if record.id %}}
<collected>
Already collected, so don't ask for these again:
- Record {{record.id}}: {{record.summary}} (status: {{record.status}}).
{{% if appointment.postal_code_verified %}}- Account postal code verified.{{% endif %}}
{{% if appointment.reason %}}- Appointment reason: {{appointment.reason}}.{{% endif %}}
{{% if appointment.day %}}- Appointment day: {{appointment.day}}.{{% endif %}}
{{% if appointment.window %}}- Time window: {{appointment.window}}.{{% endif %}}
{{% if appointment.contact_phone %}}- Callback phone: {{appointment.contact_phone}}.{{% endif %}}
</collected>
{{% endif %}}
<current_step>
{{% if not record.id %}}
- Ask for the record ID if the customer hasn't shared it, then call {@TOOL: lookup_record} with it.
{{% elif appointment.completed %}}
- Appointment {{appointment.confirmation_id}} is booked for {{appointment.day}} in the {{appointment.window}} window. If you just booked it, share the confirmation ID and the time window.
- The booking can't be changed here. If the customer wants to change or cancel it, offer to connect them with a representative.
{{% elif record.status == 'Pending' %}}
- Record {{record.id}} is still pending initial review, so a follow-up appointment isn't needed yet. Offer to check or update the pending record instead.
{{% elif not appointment.postal_code_verified %}}
- Ask for the 5-digit postal code on record {{record.id}} and call {@TOOL: verify_postal_code} with `postal_code`.
- If it returns `invalid_postal_code`, ask for a 5-digit postal code. If it returns `postal_code_mismatch`, let the customer know it didn't match our records and ask them to try again.
{{% elif not appointment.reason %}}
- Ask why they're scheduling the appointment for {{record.summary}}, then call {@TOOL: update_appointment_details} with `reason` (`follow_up`, `technical_issue`, `billing_question`, or `other`).
{{% elif not appointment.availability_checked %}}
- Call {@TOOL: check_appointment_availability} to get the open appointment days and time windows, then share them with the customer.
{{% elif not appointment.day or not appointment.window %}}
- Ask which of the open appointment days and time windows (`morning` or `afternoon`) the customer wants, then call {@TOOL: update_appointment_details} with `appointment_day` and `appointment_window`.
{{% elif not appointment.contact_phone %}}
- Ask for a 10-digit callback phone number for the specialist, then call {@TOOL: update_appointment_details} with `contact_phone`.
- If it returns `missing_area_code`, ask for the full number with the area code. If it returns `invalid_phone`, ask for a valid 10-digit phone number.
{{% elif not appointment.summary_confirmed %}}
- Read back the appointment in one sentence: record {{record.id}} ({{record.summary}}), {{appointment.day}} in the {{appointment.window}} window, callback number {{appointment.contact_phone}}. Ask the customer to confirm so you can book it.
- If they change a detail, call {@TOOL: update_appointment_details} with the change, then read back the updated summary.
- Once they confirm, call {@TOOL: submit_appointment} with `summary_confirmed=True`.
{{% else %}}
- The customer confirmed the summary. Call {@TOOL: submit_appointment} with `summary_confirmed=True` to book the appointment.
{{% endif %}}
</current_step>
</workflow>
```

- **Tool-grouped `OBJECT` variables:** Group cohesive domain state produced by a
  tool into typed `OBJECT` declarations in `app.json` (for example `record` or
  `appointment`) rather than adding many flat top-level scalars. This keeps
  `variableDeclarations` well under CES's ~100 variable limit (`A015`) and
  cleanly scopes state resets when a different record is looked up. In
  `instruction.txt`, read nested fields with dot notation (`{{record.status}}`),
  never subscripts (`record['status']`).
- **Write each branch for what the history shows right after the tool call:**
  The next model call in the same turn already sees the new branch. Check
  completion flags near the top of the chain so resetting `summary_confirmed` on
  an edit after submission can't bring the summary step back, and store record
  IDs on single-record flags (such as `disclosure_played != record.id`) so they
  do not leak across lookups.
- **Never write prose state checks on declared variables:** Do not write `If
  postal_code_verified is false...` in prose; branch on session state with
  `{{% if ... %}}`, and reserve prose `If ...` for the active step's tool
  response status (`If it returns invalid_postal_code...`).
- **Variable lifecycle hygiene (`V100`–`V106`):** Declare every variable read or
  written in `instruction.txt`, tools, or callbacks inside `app.json`
  `variableDeclarations` (`V100`, `V104`). Keep variables clean (no unused
  declarations or write-only state keys flagged by `V105`/`V106`). Framework
  keys cleared across turns or written lazily—`current_workflow` (2-level
  mode), `pending_handoff_reason`, and `next_agent_actions`—are listed under
  `options.V105.ignore` in `cxaslint.yaml` (`--single-level` omits
  `current_workflow` since `load_workflow` is its sole writer).
- **Double-brace Jinja only:** Always use `{{var}}` and `{{% if ... %}}` /
  `{{% elif ... %}}` / `{{% else %}}` / `{{% endif %}}`. Single-brace
  `{% ... %}` injects `<state_update>` blocks into history, and CES leaves
  `{{# ... #}}` / `{# ... #}` comments verbatim in the prompt. Never write
  realistic sample confirmation codes (like `NQAA123456`) in instructions or
  docstrings; describe formats in words instead.

## Going back to an earlier step

A step chain is recomputed from state on every model call—there is no step
pointer to rewind. Four rules keep corrections working seamlessly:

1. **Steps gate instructions and irreversible actions, not corrections.** Keep
   tools that change an earlier answer (`lookup_record`,
   `update_appointment_details`) and read-only lookups (`check_appointment_availability`)
   in `<workflow_rules>` so they remain visible at every step. Only submit-style
   tools and word-for-word scripts stay inside their own step.
1. **Every edit clears what depended on the old value.** Switching records
   clears per-record verification/details, and editing a booking detail resets
   `appointment.summary_confirmed = False` so the summary re-confirms.
1. **Every done step says what can still change.** Either name the amendment
   tool in the done step or state that nothing can change and offer a
   representative. Once submitted without an amendment tool, refuse edits in the
   intake tool (`already_completed`) so state stays true to what was submitted.
1. **Keep the catch-all in `<anytime_actions>` general.** Omit workflow-specific
   examples so it never contradicts done steps that allow changes.

## Tool design: facts-only results, typed args, and state-backed actions

- **Return facts and status codes, never coaching prose or `**x` spreads:**
  Tool responses persist in `llm_request.contents`. Return structured status
  codes and data fields (`{"status": "postal_code_mismatch", "verified": False}`)
  —never `"message"`, `"instructions"`, or `"next_step_guidance"`, never raw
  `**x` dict spreads, never full state echoes on updates, and never unverified
  on-file secrets (keep `on_file_postal_code` in state and return only verified
  flags or masked values like `last4`).
- **Use explicit parameter types; never match phrases in code:** Never inspect
  customer utterances or model replies with regexes or keyword lists in tools or
  callbacks. Use `bool`, `int`/`float`, `Literal[...]`, and
  `Optional[...] = None` parameters so the model classifies what the customer
  meant, and limit tool validation to strict format rules (like 5-digit postal
  codes).
- **Read prerequisites from state in final action tools:** Submission tools read
  the target ID and collected details from `context.state`, fail closed when
  prerequisites are missing, and accept `summary_confirmed: bool = False` so a
  single-turn confirm-and-submit call cannot race.
- **Tool-triggered handoffs via `pending_handoff_reason`:** When a domain tool
  determines an immediate transfer is needed, set
  `context.state["pending_handoff_reason"] = "verification_failed"`. On the
  next model call in the same turn, `<pending_handoff>` renders in place of the
  workflow step, and `execute_live_agent_handover` consumes and resets
  `pending_handoff_reason` to `""`.

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
      if (
          func_decl.name not in TRANSFER_TOOLS
          and re.search(rf"\b{re.escape(func_decl.name)}\b", instruction)
      ):
        continue
      if (
          config.excluded_tools is None
          or func_decl.name not in config.excluded_tools
      ):
        config.hide_tool(func_decl.name)
  return None
```

- **Always hide `TRANSFER_TOOLS` and iterate all `config.tools`:** If an app
  uses platform guardrails with `transferAgent` pointing to a handler in
  `childAgents`, CES adds `transfer_to_agent` / `transferToAgent` (and may
  supply multiple `ToolDeclaration` blocks in `config.tools`). Always hiding
  `TRANSFER_TOOLS` ensures the root model cannot initiate a sub-agent transfer
  on its own while deterministic guardrails can still route as configured.
- **Match on word boundaries:** Built-in `end_session` is a substring of
  `graceful_end_session`; a substring check would expose `end_session` whenever
  `graceful_end_session` is visible.
- **Keep hidden tools out of tag names and comments:** A tag like
  `<end_session>` exposes `end_session` on every turn, and CES does not strip
  Jinja comments.
- **Don't name domains or workflows after tools:** `<workflow_list>` stays
  visible while a domain is selected (or always in `--single-level` mode), so a
  workflow sharing a tool's name would expose that tool prematurely.
- **Register every tool in the root agent's `tools` list:** Including the
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

For verbatim non-terminal disclosures, queue only `customized_response`
directly in `context.state["next_agent_actions"] = {"customized_response": ...}`
along with a per-record state flag (for example `disclosure_played = record_id`)
so the disclosure plays once and `<current_step>` moves to the next branch.
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
avoids the per-callback startup cost on the platform. Never add
`after_model_callbacks` to inspect or rewrite model replies, and never append
dynamic rule blocks inside `before_model_callback`.

## Platform gotchas

- **CES Jinja subset and output tags:** Templates support `if`/`elif`/`else`,
  `and`, `or`, `not`, `in`, `==`, `!=`, and dot access inside double-brace
  blocks (`{{% ... %}}`), but reject `not in` (write `not a in ['x', 'y']`) and
  subscripts like `account['status']` (use `account.status`). Inside
  `{{ ... }}` output tags, CES only substitutes bare variables (`{{var}}`,
  `{{a.b}}`) and leaves expressions such as `{{ x or "none" }}` or
  `{{ x | default("none") }}` literal; single-brace `{var}` is never
  substituted, and single-brace `{% ... %}` blocks emit `<state_update>` notes
  into history. CES also leaves `{{# ... #}}` / `{# ... #}` comments verbatim in
  the prompt. `check_tool_visibility.py` and `cxas lint` (`I020`) flag
  unsupported constructs, and `check_tool_visibility.py` automatically
  synthesizes `if`/`elif`/`else` branch states across all three routing
  topologies (`domain_workflow`, `single_workflow`, `playbook_task`, including
  dotted `OBJECT` conditions) to catch hidden-branch regressions, undeclared
  `OBJECT` properties, returned-dict prose keys or `**x` spreads, router state
  violations, missing handoff reasons, and hardcoded sample confirmation codes
  before you push.
- **Banned `<context>` tag (`I015`):** Never wrap prompt sections in
  `<context>`. Put `Today's date is {{current_date}}.` inside `<status>`.
- **`Part.from_end_session` signature (`C018`):** Takes `reason` and
  `escalated` (not `session_escalated`). Passing `session_escalated` raises
  `TypeError` at runtime and leaves `next_agent_actions` latched in state.
- **Never assign `None` to state (`C011`):** Clearing a string key with `None`
  triggers platform errors; reset strings like `pending_handoff_reason` to
  `""`.
- **Declare every variable (`V100`–`V106`, `A015`):** Undeclared tool-written
  variables are dropped between turns, and undeclared instruction variables
  always render empty. Keep `NUMBER` floats in mind, stay under 95 top-level
  declarations (`A015`) by grouping into `OBJECT` variables, and preserve
  `options.V105.ignore` in `cxaslint.yaml` for framework keys.
- **`current_date` builtin:** CES provides `current_date` in the app's time zone
  (like `2026-10-16[America/New_York]`) without a declaration, placed inside
  `<status>` in `instruction.txt`. Python tools run on UTC, so read
  `context.state["current_date"]` instead of `datetime.date.today()` and add a
  `per_file` `V100: "off"` override for that tool file in `cxaslint.yaml`:

  ```yaml
  per_file:
    # Reads current_date, which CES provides without a declaration.
    "tools/<name>/python_function/python_code.py":
      V100: "off"
  ```

  The local scripts fill `current_date` from your machine's clock; pass it in
  `--state` to test a specific day.
- **Python tool conventions (`C019`, `T004`, `T015`, `T016`):** Import `Any`
  and `Optional` from `typing` explicitly (`C019`), put the entry function
  (named like the tool directory) first (`T004`, `T015`), annotate parameters
  and `-> dict[str, Any]` (`T016`), and keep tool JSON descriptions in sync with
  docstrings via `scripts/sync_tool_descriptions.py` (which creates missing
  `<tool>.json` wrappers automatically when run without `--check`).
- **`cxaslint.yaml` overrides:** The starter disables `I001`, `T001`, `T011`,
  `I012`, `A007`, `A009`, and `T014` with explanations and sets
  `options.V105.ignore: [current_workflow, pending_handoff_reason, next_agent_actions]`
  (or `[pending_handoff_reason, next_agent_actions]` in `--single-level` mode).
  All other rules remain enabled.
- **Deployment timing:** After `cxas push`, wait about a minute before testing.
  `cxas push` doesn't change an existing app's display name; rename it in the
  console.

## Sample agent

`resources/sample-agent` is a generic, domain-agnostic starter with one root
agent, `Support_Agent`, on `gemini-composite-v1`:

Domain | Workflow | Tools the workflow uses
--- | --- | ---
`live_agent` | `live_agent_escalation` | `execute_live_agent_handover`

Registered tools (`6` in 2-level mode; `5` in `--single-level` mode):
`select_domain` (omitted in `--single-level`), `graceful_end_session`, and
`execute_live_agent_handover` are always visible (from `<domain_list>` and
`<anytime_actions>`, or `<workflow_list>` for `load_workflow` in `--single-level`
mode); `load_workflow` appears once a domain is selected in 2-level mode;
`set_next_agent_actions` (compatibility helper) and `end_session` stay hidden
from the model (along with `TRANSFER_TOOLS`).

Declared variables (`5` in 2-level mode; `4` in `--single-level` mode):
`current_domain` (`STRING`, omitted in `--single-level`), `current_workflow`
(`STRING`), `next_agent_actions` (`OBJECT`), `played_greeting` (`BOOLEAN`), and
`pending_handoff_reason` (`STRING`). Add your domains, workflows, tools, and
grouped `OBJECT` variables directly on top of this starter without deleting any
domain-specific files.
