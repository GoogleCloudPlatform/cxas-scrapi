---
name: cxas-self-steering-agent
description: >-
  Builds and refactors self-steering CXAS (CES, GECX) conversational agents for
  chat or voice, where the model runs the whole conversation like a contact
  center agent: one root agent, Jinja progressive disclosure (playbooks and
  tasks loaded through open_playbook and load_task, dynamic tool hiding) and
  deterministic actions (greeting, verbatim text, goodbye and live agent
  handoff through set_next_agent_actions). Ships a working sample agent to
  start from. Use when creating a new CXAS agent, adding playbooks, tasks or
  tools to an agent built this way, replacing a steering agent with
  sub-agents or a single big prompt, or finding out why such an agent sees or
  calls the wrong tool.
---

# CXAS self-steering agent

This skill builds self-steering CXAS agents. Treat the model like a capable
person on a support team: give it the power to run the whole conversation and
the awareness to know where it is and why, and set up its job so it can do both.
There's no steering agent and no code making decisions behind the model's back;
the agent steers itself. Progressive disclosure and deterministic actions are
the two main techniques, built from a Jinja instruction template, Python tools,
session state and one small callback on top of the loop CXAS runs.

The skill bundles a working sample agent
([resources/sample-agent](resources/sample-agent)) and these scripts:

- `scripts/scaffold_from_sample.py` copies the sample into a new app
  directory.
- `scripts/check_tool_visibility.py` renders the instruction for every
  playbook and task, lists the tools the model can see, and catches template
  and tool anti-patterns that `cxas lint` misses.
- `scripts/sync_tool_descriptions.py` copies tool docstrings into the tool
  JSON files.
- `scripts/run_locally.py` runs tools and the callback on your machine.
- `scripts/run_conversation.py` runs a test conversation against the deployed
  app.

Read [references/guide.md](references/guide.md) before designing or changing an
agent. It has the reasoning, the code patterns, and the platform gotchas.

## Before you start

- Install the CXAS SDK and CLI, which also brings in `jinja2`: `pip install cxas-scrapi` (use the workspace's virtual environment if it has one).
- Creating an app, pushing and testing call the CES API, so log in first:
  `gcloud auth application-default login`. Building and checking locally don't
  need it.
- Know the GCP project and CES location (for example `us`) the app lives in.

## Rules

1. **The model steers the conversation.** Treat it like a capable person on a
   support team. It makes every conversational decision: what the customer
   needs, which playbook to open, what to ask, which tool to call, when to read
   a disclosure, and when to hand off or say goodbye. Give it the power to act
   (a tool for everything the agent might do, including ending the conversation
   and transferring) and the awareness to act well (where it is, what it can do
   right now, and why it did what it did before). Code never makes a
   conversational decision behind its back: never run regex, keyword, or
   substring checks on customer utterances or model replies in callbacks or
   tools to decide intent, trigger transfers, or override tool arguments. Give
   tools explicitly typed parameters (`bool` for confirmations, `int`/`float`
   for counts, `Literal[...]` for closed sets, `Optional[...] = None` for
   optional fields) so the model classifies what the customer meant, and only
   validate argument formats (digit count, date format) in tool code.
1. **One root agent, no sub-agents.** A sub-agent handoff reaches the model as
   `transfer_to_agent(agent_name: "...")` plus history lines like `[Other Agent] said: ...`, so the model announces "transfers" the customer never
   asked about. Keep one agent and swap task instructions inside it.
1. **Progressive disclosure.** `instruction.txt` is a series of flat top-level
   blocks, static ones first: `<role>`, `<context>`, `<constraints>`,
   `<anytime_actions>` and `<playbook_list>`. Then come the blocks that depend
   on state: `<task_list>` (the open playbook's tasks, rendered whenever
   `current_playbook` is set), `<status>` (the open playbook and loaded task),
   and one chain for the loaded task: `<pending_handoff>` if a tool can decide
   on a transfer, a shared prerequisite block such as `<identify_order>` if the
   tasks start with the same steps, and otherwise `<playbook_rules>` (rules
   every task in the playbook shares, such as switching tasks or records)
   followed by one `<task name="...">` block. Never hide `<playbook_list>` once
   a playbook is open or `<task_list>` once a task is loaded; the model needs
   both to switch when the customer changes the subject or has a second
   request. When several tasks start with the same steps (finding and verifying
   the record), write them once instead of copying them into every task. Choose
   per agent between a shared prerequisite block, which renders in place of the
   task's steps while the task stays loaded, and a separate task the model
   loads first and then comes back from. The block saves two router calls per
   request and keeps the customer's task in `<status>`; a separate task fits a
   long identification flow with its own rules, or one the customer asks for on
   its own ("help with a different order"). Always build both layers,
   `open_playbook` and `load_task`, even for a small agent. Group related use
   cases into a playbook with a few tasks (a playbook with one task is fine),
   and keep each list short so the model never picks from a long menu.
1. **Router tools.** `open_playbook(playbook_name, rationale)` and
   `load_task(task_name, rationale)` check exact names (no aliases), return
   structured status and the valid names on a miss, and set the state variable.
   `open_playbook` clears `current_task`. The required one-sentence `rationale`
   stays in the history so later turns know why the step happened. Routers
   write only `current_playbook` and `current_task`: they never set or clear
   domain state (the tool that changes a record clears what depended on it, so
   a stray reload can't wipe a verified record) and never read `rationale`. If
   routing needs context, such as "this is about the order from the greeting",
   put a typed flag on the domain tool that uses it.
1. **Branch with Jinja, not prose, and render one step at a time.**
   - Use double-brace control tags (`{{% if ... %}}`, `{{% elif ... %}}`,
     `{{% else %}}`, `{{% endif %}}`) so the model sees only the branch that
     applies without injecting `<state_update>` blocks into the conversation
     history.
   - Give every `<task>` block the same shape: optional `<task_rules>` (rules
     for every step of this task, including do's and don'ts that only apply
     here), an optional `<collected>` list of confirmed facts in plain
     sentences (never unverified on-file records or a raw
     `<session_snapshot>` variable dump), and a `<current_step>` block with
     one flat `{{% if ... %}} / {{% elif ... %}}` chain, so **only the active
     unfinished step** renders on any model call. A short task whose steps
     have no irreversible action or word-for-word script can list its steps
     together in one step; gate a step whenever it leads to a submit-style
     tool or a script, so the model can't reach it early.
   - Steps gate instructions and irreversible actions, not corrections. The
     chain is computed from state on every model call, so going back means
     calling a tool that changes an earlier answer, and the earlier step then
     renders again on the next call. Put every tool that changes an earlier
     answer (a lookup that switches the order, an `update_...` intake tool)
     and every read-only lookup a customer may ask for early in
     `<task_rules>`, so it's visible at every step. Keep only submit-style
     actions and word-for-word scripts inside their own step. An edit tool
     named only in its own step gets called anyway from the history, and CES
     fails the turn with "unexpected function call".
   - Every edit clears what depended on the old value (switching orders
     clears the old order's verification and collected details, and changing
     a booking detail resets `summary_confirmed`), so the chain goes back to
     the first step that needs redoing instead of skipping it. Clear only
     what depended on it: never reset an attempt counter outside the tool
     that verifies (the sample counts ZIP failures for the whole
     conversation, so switching orders can't start the count over), and scope
     every done flag to its record by storing the record's ID or clearing the
     flag when the record changes.
   - A step that asks the customer to confirm or choose says "and end your
     turn", so the model waits instead of answering for them. Never call a
     selection or submit tool in the same turn as the lookup that produced
     the choices.
   - Every done step says what can still change and with which tool, or, when
     the agent has no tool for it, that it can't and what to offer instead.
     Without that line the model improvises, for example transferring without
     asking or making up why a change isn't possible.
   - Never write prose state checks (`If zip_verified is false...`) on
     session state variables; branch with `{{% if ... %}}` instead. Only
     fields of the current step's tool result
     (`If it returns invalid_zip...`) belong in prose inside that step.
   - In output tags, CES only substitutes bare variables (`{{var}}`,
     `{{a.b}}`), never expressions like `{{ x or "none" }}` or
     `{{ "a" if c else "b" }}` (which CES sends to the model as literal code)
     and never single-brace `{var}`. Never put `{{var}}` inside tool-call
     examples (`f(arg='{{x}}')`) where an empty `x` renders as `f(arg='')`,
     and never write realistic sample confirmation codes
     (`such as NQAA123456`) in instructions or docstrings because the model
     can read them aloud to customers as real data.
   - Do not put `{{# ... #}}` or `{# ... #}` comments in `instruction.txt`:
     the platform leaves them in the prompt sent to the model and callback.
1. **Tools return facts and status codes, never instructions, full-state
   echoes, or unmasked secrets.**
   - Facts the agent shares (order details, dates, confirmation codes) come
     back in a tool result, not only in the rendered instruction. A step may
     also show them from state, but when the only source was the instruction,
     `gemini-composite-v2` sometimes invented a lookup tool to fetch them and
     failed the turn.
   - Tool responses stay in `llm_request.contents` for the rest of the
     conversation. Never return prose coaching keys (`message`,
     `instructions`, `next_step_guidance`, `wrap_up_guidance`) telling the
     model what to say or do next; put behavior rules in `instruction.txt`.
   - Intake/update tools (`update_...`) return only the fields updated in
     that call (plus `not_recorded` when co-batched fields were skipped
     because an earlier gate wasn't complete), never a full state dump.
   - Keep unverified on-file data (such as `on_file_zip` or full account
     numbers) in session state for verification tools to check, and return
     only verified flags or masked values (such as `last4`) to the model. The
     sample's `lookup_order` returns only whether the order exists, and
     `verify_order_zip` returns the order's details once the ZIP code
     matches.
   - Build results from named fields. Never return a raw backend payload or
     spread one into the result (`**data`), and fail closed on backend data:
     eligibility flags default to `False`, and success needs an explicit
     positive field.
   - When the customer picks one of several looked-up records, the tool that
     selects it takes the ID, matches it only against the records the lookup
     stored in state, and on a miss returns what the agent may read out (such
     as masked labels), not the full list of IDs.
   - Final action tools (`submit_cancellation`, `submit_return_pickup`) read
     the target ID and collected fields from validated session state rather
     than trusting LLM-copied arguments, fail closed when prerequisites are
     missing, and accept the final confirmation flag (`confirmed=True` or
     `summary_confirmed=True`) directly so a single-turn confirm-and-submit
     call cannot race. Reset `summary_confirmed` when any booking detail is
     edited, and check the completed state near the top of the step chain so
     resetting `summary_confirmed` after booking can't bring the summary step
     back. Once the action is done, an intake tool must not change the
     collected details on its own, or state drifts from what was submitted:
     either refuse (the sample returns `pickup_already_booked` because it has
     no way to change a booking), or, if the backend can change a submitted
     request, add a tool that changes it there and updates state in the same
     call, and name that tool in the done step.
1. **Anytime actions.** `<anytime_actions>` is static and names
   `{@TOOL: graceful_end_session}` and `{@TOOL: execute_live_agent_handover}`
   (plus any other action that must work from any state, such as a transfer to
   another language), so the agent can end the conversation or transfer in one
   model call from any state without opening a playbook. Rules for these tools
   live only here, never in `<role>`, `<context>` or `<constraints>`, so each
   trigger has one rule. If a playbook sends transfers somewhere else, gate the
   anytime line off with `{{% if %}}` instead of writing "follow the playbook's
   rule instead". `execute_live_agent_handover` takes a required
   `conversation_summary` (what the customer wanted, what was done, what's
   still open) for the representative. `<anytime_actions>` also holds the
   wrap-up rule: ask whether there's anything else once the customer has what
   they asked for, and give a next step first when the agent couldn't help.
   When a tool decides a transfer is required (such as after too many
   verification mismatches), it sets `pending_handoff_reason` in state. The
   first branch of the task chain, `{{% if pending_handoff_reason %}}`, then
   renders a `<pending_handoff>` block in place of the task, so the model gets
   one instruction (transfer now) rather than a transfer rule next to a task
   step, and `execute_live_agent_handover` reads and clears
   `pending_handoff_reason`. `<anytime_actions>` also holds a catch-all: when
   the customer wants a change that no playbook or task covers, the model says
   so and offers a representative instead of calling a tool that isn't in its
   list. Keep the catch-all free of domain examples. It's static, so an example
   like "such as changing a submitted request" tells the model that submitted
   requests can't change, even in an agent that has a tool for it. Say what
   can't change in the done step where it's true.
1. **Dynamic tool hiding.** The callback hides every tool whose name has no
   word-boundary match in the rendered instruction. Only `open_playbook` and
   the tools named in `<anytime_actions>` may be named in static text. Name a
   tool only as `{@TOOL: name}` and only where the model may call it; a bare
   name in prose ("the lookup_order result") shows the tool in that branch too.
   Never put a tool name in a tag name (`<end_session>` would expose the
   built-in `end_session` tool on every turn) or an unstripped comment, never
   give a playbook or task the same name as a tool, and mention a tool in every
   branch where the model may need it. Keep the "Your tool list only has the
   tools for the loaded task..." rule at the end of `<task_list>`.
1. **Deterministic actions only where they matter.** Use code for the greeting,
   for text that must be word for word (a tool that sets a state flag and
   queues a `customized_response` through `set_next_agent_actions`), and for
   the goodbye and live agent handoff (a tool that queues the message together
   with `end_session`). The model still decides when to call the tool, the
   result says what happened, and the text lands in the history as the agent's
   own reply. A tool never returns text for the model to repeat (`phrase`,
   `script`, "say it exactly as returned"), and only the goodbye and handoff
   tools queue `end_session`. Don't chain workflow steps inside tools or switch
   `current_task` from code; later turns can't explain actions their
   instructions don't mention.
1. **One small callback.** A single `before_model_callback` runs
   `_handle_next_agent_actions`, `_handle_initial_greeting`, and
   `_handle_dynamic_tool_hiding`, in that order. Never add
   `after_model_callbacks` to inspect or rewrite model replies, and never
   append dynamic rule blocks to `system_instruction` in
   `before_model_callback` (gate rules with `{{% if ... %}}` in
   `instruction.txt` instead).

## Build an agent

1. Get a CES app ID. Create one with `cxas create --project-id <project> --location <location> <display-name>` (the app ID is the last part of the
   app name it prints), or use an existing app you own. Never push over an app
   someone else is using.

1. Copy the sample into a new app directory:

   ```bash
   python3 <skill-dir>/scripts/scaffold_from_sample.py \
     --target-dir cxas_app/<app-dir> --app-id <app-id> \
     --project-id <project> --agent-name <Root_Agent_Name>
   ```

1. Plan before editing files. Write a table with one row per task (its
   playbook, the steps in its `<current_step>` chain, the tools each step uses,
   the state each tool sets, the tool that changes each earlier answer and the
   state it clears, and what can still change once the task is done). Steps
   that several tasks share go in one row for the shared prerequisite block.
   Show it to the user before writing files, unless they asked you to go ahead
   without checking in. Name playbooks and tasks differently from tools.

1. Edit `agents/<Root_Agent_Name>/instruction.txt`. Keep the blocks, their
   order and their tag names (`<role>`, `<context>`, `<constraints>`,
   `<anytime_actions>`, `<playbook_list>`, `<task_list>`, `<status>`,
   `<pending_handoff>`, the shared prerequisite block (`<identify_order>` in
   the sample; rename it for your record, or drop it if tasks share no steps),
   `<playbook_rules>`, and one `<task name="...">` block per task with
   `<task_rules>`, `<collected>` and `<current_step>` inside) but rewrite their
   content for the new agent: the business and use cases in `<role>`,
   `Today's date is {{current_date}}.` in `<context>` if anything depends on
   dates, its rules in `<constraints>`, and your playbooks, tasks and step
   chains in place of the sample's. Also change `GREETING` in
   `agents/<Root_Agent_Name>/before_model_callbacks/before_model_callbacks_01/python_code.py`
   and the `description` in `agents/<Root_Agent_Name>/<Root_Agent_Name>.json`.

1. Update the router tools to match the instruction exactly: `VALID_PLAYBOOKS`
   in `tools/open_playbook/python_function/python_code.py` and `PLAYBOOK_TASKS`
   in `tools/load_task/python_function/python_code.py`. Replace the sample's
   `rationale` examples in both docstrings with ones from your domain.

1. Replace the sample's domain tools. Delete `lookup_order`,
   `verify_order_zip`, `submit_cancellation`, `play_sms_legal_disclosure`,
   `send_sms_summary`, `create_mail_return`,
   `check_return_pickup_availability`, `update_return_details`, and
   `submit_return_pickup` from `tools/` and from the root agent JSON's `tools`
   list, and delete the `order_id`, `order_status`, `order_item`,
   `order_delivery`, `on_file_zip`, `zip_verified`, `zip_attempts`,
   `sms_disclosure_played`, `sms_summary_sent`, `return_reason`,
   `return_availability_checked`, `return_pickup_day`, `return_pickup_window`,
   `return_contact_phone`, `return_summary_confirmed`,
   `return_pickup_completed`, and `return_confirmation_id` variables from
   `app.json`. Keep `open_playbook`, `load_task`, `graceful_end_session`,
   `execute_live_agent_handover`, `set_next_agent_actions`, and `end_session`,
   and the `current_playbook`, `current_task`, `next_agent_actions`,
   `played_greeting`, `pending_handoff_reason`, and `handoff_summary`
   variables. Reword the docstring and default message of
   `execute_live_agent_handover` if the handoff goes to someone other than a
   representative. Write each new tool in the same shape as a sample tool
   (`tools/<name>/python_function/python_code.py` plus
   `tools/<name>/<name>.json`, entry function first), add it to the `tools`
   list, and declare every state variable it reads or writes in `app.json`
   `variableDeclarations`. Then sync the descriptions:
   `python3 <skill-dir>/scripts/sync_tool_descriptions.py cxas_app/<app-dir>`.
   The sync only updates tools that already have a JSON file, so copy a sample
   tool's JSON, replace the tool name in it, and leave the description for the
   sync to fill in.

1. Check before pushing:

   ```bash
   cxas lint --app-dir cxas_app/<app-dir>
   python3 <skill-dir>/scripts/check_tool_visibility.py cxas_app/<app-dir>
   python3 <skill-dir>/scripts/run_locally.py cxas_app/<app-dir> callback \
     'open_playbook={"playbook_name": "<playbook>", "rationale": "test"}' \
     'load_task={"task_name": "<task>", "rationale": "test"}' callback \
     '<tool>={"<arg>": "<value>"}' callback
   ```

   Keep the sample's `cxaslint.yaml`; it turns off only the four rules that
   don't fit this design. The checker fails on a missing `load_task`,
   undeclared instruction variables, single-brace control tags or variables,
   non-bare `{{ ... }}` output expressions, `not in` and subscripts (which a
   push rejects), Jinja comments (which the platform does not strip), hidden
   playbook or task lists, `<session_snapshot>` state dumps, prose state checks
   on declared variables, tools returning coaching prose or text to repeat
   (`message`, `instructions`, `phrase`) or spreading a dict into the result,
   `afterModelCallbacks`, names shared by a playbook or task and a tool, router
   lists that don't match the instruction, routers writing other state or
   reading `rationale`, tool names outside `{@TOOL: ...}` or in `<role>`,
   `<context>` or `<constraints>`, tools visible at the start that
   `<anytime_actions>` doesn't name, registered tools nothing uses, and a
   visible `end_session` or `set_next_agent_actions`. To check branches that
   depend on other state, pass the full state, including the playbook and task,
   with `--state '{"current_playbook": "...", "current_task": "...", ...}'`,
   and add `--print` to read the rendered instruction. `run_locally.py` walks
   one flow per task through the real tool and callback code, so Python errors
   show up now instead of after a push. The first `callback` step plays the
   greeting, and each later one prints the tools the model would get next. The
   script doesn't stop you from calling a tool the last callback step hid, so
   follow the visible tools it prints.

1. Push:

   ```bash
   cxas push --app-dir cxas_app/<app-dir> --project-id <project> \
     --location <location> \
     --to projects/<project>/locations/<location>/apps/<app-id> --overwrite
   ```

   Wait about a minute before testing; a session that starts right after a push
   can still run the previous version.

1. Test the greeting, one request per playbook, a goodbye, and a handoff. Run a
   scripted text conversation, then read the stored conversation to check the
   tool calls, results and state changes, not only the replies:

   ```bash
   python3 <skill-dir>/scripts/run_conversation.py \
     projects/<project>/locations/<location>/apps/<app-id> \
     "Hi." "<first request>" "<follow-up>" "That's all, thanks."
   cxas trace get <session-id> \
     --app-name projects/<project>/locations/<location>/apps/<app-id> --format text
   ```

   The first message gets the greeting. The stored conversation can take up to
   a minute to show up. The CXAS console simulator works too.

   Test corrections too: change the first answer at the last step, change a
   detail while reviewing a summary, ask for a change after the final action,
   and ask a later step's question early. A reply like "Hmm, I'm having trouble
   with that" usually means the model called a tool the current step hides.

## Add to an existing agent

To add a playbook, task or tool to an agent built this way, work in its app
directory and skip the parts of "Build an agent" that only apply to a new agent:
copying the sample (step 2), rewriting the whole instruction (step 4), and
deleting the sample's tools and variables (step 6).

1. Plan the new tasks as in step 3, together with the existing tasks that share
   tools or state with them.
1. Add each new task to its playbook's lines in `<task_list>`, to
   `PLAYBOOK_TASKS`, and as a new `{{% elif current_task == '...' %}}` branch
   holding its own `<task name="...">` block (it starts after the shared
   prerequisite block, so it doesn't repeat those steps), and mention the new
   request in that playbook's line in `<playbook_list>`. A new playbook also
   needs a line in `<playbook_list>`, its own branch in `<task_list>`, and an
   entry in `VALID_PLAYBOOKS`. Update `<role>` if the agent now covers more.
1. Write new tools as in step 6. Name a submit-style tool only in the step that
   uses it, and put tools that change earlier answers or look things up in the
   task's `<task_rules>`.
1. Check, push and test as in steps 7 to 9.

## When the model sees or calls the wrong tool

The model only sees what is in `llm_request` for that turn: the rendered system
instruction, the visible tool declarations, and `llm_request.contents`.

1. Find the turn in the stored conversation (`cxas trace get`, as in step 9)
   and work out the state at that point: `current_playbook`, `current_task`,
   and the variables the instruction branches on.
1. Render that state with `check_tool_visibility.py --state '{...}' --print` to
   read the exact instruction and tool list the model had. A tool that
   shouldn't be visible is named somewhere in that text, often in a shared
   block, an unstripped Jinja comment, or a branch that covers more states than
   intended. If the tool belongs there, check whether an earlier tool response
   in `contents` returned stale coaching prose or whether two steps rendered at
   once.
1. Fix the instruction, tool docstring, or tool return fields rather than
   adding callback heuristics, reproduce the flow with `run_locally.py`, and
   run the checks in step 7 again.

## Checklist

- [ ] The root agent has no child agents and no `afterModelCallbacks`.
- [ ] For a new agent, none of the sample's order or return tools or variables
  are left, and `GREETING`, the agent `description` and `<role>` describe it.
- [ ] `VALID_PLAYBOOKS` and `PLAYBOOK_TASKS` match the instruction exactly,
  and `<playbook_list>` and `<task_list>` stay visible while a task is loaded.
- [ ] Only `open_playbook` and the tools named in `<anytime_actions>` are
  named in static text, and every tool is named as `{@TOOL: name}`; no Jinja
  comments in `instruction.txt` and no tool names in tag names; no playbook or
  task shares a tool's name.
- [ ] Steps several tasks share are written once (a prerequisite block or a
  separate task), and the router tools write only `current_playbook` and
  `current_task`.
- [ ] Every `<task>` block has the same shape (`<task_rules>`, `<collected>`,
  `<current_step>`), and its `<current_step>` is one flat
  `{{% if ... %}} / {{% elif ... %}}` chain, so only one step renders at a
  time.
- [ ] Tools that change an earlier answer, and read-only lookups a customer
  may ask for early, are named in `<task_rules>`; every edit clears what
  depended on the old value, and no tool but the verifier resets an attempt
  counter; confirm and choose steps end the turn; every done step says what
  can still change.
- [ ] State-dependent rules use double-brace `{{% if ... %}}` blocks (never
  prose state checks or `<session_snapshot>` dumps), and output tags use bare
  `{{var}}` variables (never `{{ x or "none" }}` or `{var}`).
- [ ] Tools use explicitly typed parameters (`bool`, `int`/`float`,
  `Literal[...]`, `Optional[...] = None`), return structured status codes and
  facts built from named fields (no `message` or coaching prose, no text to
  repeat, no raw payloads or full-state echoes, no unmasked secrets), and
  never run regex or phrase checks on utterances or model replies.
- [ ] Every variable the instruction or tools use is declared in `app.json`.
- [ ] Tool JSON descriptions match the docstrings (`sync_tool_descriptions.py --check`).
- [ ] `cxas lint` passes, `check_tool_visibility.py` reports no problems, and
  a `run_locally.py` flow per task runs without errors.
