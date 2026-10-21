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
  playbook and task, lists the tools the model can see, and catches mistakes
  `cxas lint` misses.
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
   conversational decision behind its back; apart from the greeting, code acts
   only when the model asks for it through a tool.
1. **One root agent, no sub-agents.** A sub-agent handoff reaches the model as
   `transfer_to_agent(agent_name: "...")` plus history lines like `[Other Agent] said: ...`, so the model announces "transfers" the customer never
   asked about. Keep one agent and swap task instructions inside it.
1. **Progressive disclosure.** `instruction.txt` has a `<taskflow>` with three
   parts gated on the `current_playbook` and `current_task` variables: the
   playbook list (always shown), the open playbook's task list, and the loaded
   task's steps with `{@TOOL: ...}` references. Always build both layers,
   `open_playbook` and `load_task`, even for a small agent. Group related use
   cases into a playbook with a few tasks (a playbook with one task is fine),
   and keep each list short so the model never picks from a long menu.
1. **Router tools.** `open_playbook(playbook_name, rationale)` and
   `load_task(task_name, rationale)` check exact names (no aliases), return the
   valid names on a miss, and set the state variable. `open_playbook` clears
   `current_task`. The required one-sentence `rationale` stays in the history
   so later turns know why the step happened, since the model's reasoning from
   earlier turns isn't kept.
1. **Branch with Jinja, not prose.** CXAS renders `{{var}}` in place, so `If {{order_id}} is empty, ...` reaches the model as `If is empty, ...`. Use `{% if %}`, `{% elif %}`, `{% else %}` so the model sees only the branch that
   applies, render values with `{{var}}` (never `{var}`), and write each branch
   so it makes sense right after the action that leads to it: the instruction
   is rendered again for the next model call in the same turn.
1. **Static goodbye.** A `<ending_the_conversation>` block above `<taskflow>`
   references `{@TOOL: graceful_end_session}`, so the agent can end the
   conversation with one model call from any state.
1. **Dynamic tool hiding.** The callback hides every tool whose name has no
   word-boundary match in the rendered instruction. Only `open_playbook` and
   `graceful_end_session` may be named in static text. Never put a tool name in
   a Jinja comment or a tag name (`<end_session>` would expose the built-in
   `end_session` tool on every turn), never give a playbook or task the same
   name as a tool, and mention a tool in every branch where the model may need
   it. Keep the "Your tool list only has the tools for the currently loaded
   task..." rule at the end of `<current_task_status>`.
1. **Deterministic actions only where they matter.** Use code for the greeting,
   for text that must be word for word (a tool that sets a state flag and
   queues a `customized_response` through `set_next_agent_actions`), and for
   the goodbye and live agent handoff (a tool that queues the message together
   with `end_session`). The model still decides when to call the tool, the
   result says what happened, and the text lands in the history as the agent's
   own reply. Don't chain workflow steps inside tools or switch `current_task`
   from code; later turns can't explain actions their instructions don't
   mention.
1. **One small callback.** A single `before_model_callback` runs
   `_handle_next_agent_actions`, `_handle_initial_greeting`, and
   `_handle_dynamic_tool_hiding`, in that order. Solve behavior problems in the
   instruction, tool docstrings, and tool results rather than adding callbacks.

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
   playbook, the tools it uses, and the state each tool sets). Show it to the
   user before writing files, unless they asked you to go ahead without
   checking in. Name playbooks and tasks differently from tools.

1. Edit `agents/<Root_Agent_Name>/instruction.txt`. Keep the blocks and their
   tag names (`<role>`, `<constraints>`, `<ending_the_conversation>` and the
   three `<taskflow>` sections) but rewrite their content for the new agent:
   the business and use cases in `<role>`, its rules in `<constraints>`, and
   your playbooks, tasks and subtasks in place of the sample's. Keep `Today's date is {{current_date}}.` if anything depends on dates. Also change
   `GREETING` in
   `agents/<Root_Agent_Name>/before_model_callbacks/before_model_callbacks_01/python_code.py`
   and the `description` in `agents/<Root_Agent_Name>/<Root_Agent_Name>.json`.

1. Update the router tools to match the instruction exactly: `VALID_PLAYBOOKS`
   in `tools/open_playbook/python_function/python_code.py` and `PLAYBOOK_TASKS`
   in `tools/load_task/python_function/python_code.py`. Replace the sample's
   `rationale` examples in both docstrings with ones from your domain.

1. Replace the sample's domain tools. Delete `lookup_order`,
   `submit_cancellation`, `play_sms_legal_disclosure`, `send_sms_summary` and
   `create_return` from `tools/` and from the root agent JSON's `tools` list,
   and delete the `order_id`, `order_status` and `sms_disclosure_played`
   variables from `app.json`. Keep `open_playbook`, `load_task`,
   `graceful_end_session`, `execute_live_agent_handover`,
   `set_next_agent_actions` and `end_session`, and the `current_playbook`,
   `current_task`, `next_agent_actions` and `played_greeting` variables. Reword
   the docstring and default message of `execute_live_agent_handover` if the
   handoff goes to someone other than a representative. Write each new tool in
   the same shape as a sample tool
   (`tools/<name>/python_function/python_code.py` plus
   `tools/<name>/<name>.json`, entry function first), add it to the `tools`
   list, and declare every state variable it reads or writes in `app.json`
   `variableDeclarations`. Then sync the descriptions: `python3 <skill-dir>/scripts/sync_tool_descriptions.py cxas_app/<app-dir>`. The sync
   only updates tools that already have a JSON file, so copy a sample tool's
   JSON, replace the tool name in it, and leave the description for the sync to
   fill in.

1. Check before pushing:

   ```bash
   cxas lint --app-dir cxas_app/<app-dir>
   python3 <skill-dir>/scripts/check_tool_visibility.py cxas_app/<app-dir>
   python3 <skill-dir>/scripts/run_locally.py cxas_app/<app-dir> callback \
     'open_playbook={"playbook_name": "<playbook>", "rationale": "test"}' \
     'load_task={"task_name": "<task>", "rationale": "test"}' callback \
     '<tool>={"<arg>": "<value>"}' callback
   ```

   Keep the sample's `cxaslint.yaml`; it turns off only the rules that
   don't fit this design. The checker fails on a missing `load_task`,
   undeclared instruction variables, `not in` and subscripts (which a push
   rejects), tool names in comments, names shared by a playbook or task and a
   tool, router lists that don't match the instruction, registered tools
   nothing uses, and a visible `end_session` or `set_next_agent_actions`. To
   check branches that depend on other state, pass the full state, including
   the playbook and task, with `--state '{"current_playbook": "...", "current_task": "...", ...}'`, and add `--print` to read the rendered
   instruction. `run_locally.py` walks one flow per task through the real tool
   and callback code, so Python errors show up now instead of after a push. The
   first `callback` step plays the greeting, and each later one prints the
   tools the model would get next. The script doesn't stop you from calling a
   tool the last callback step hid, so follow the visible tools it prints.

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

## Add to an existing agent

To add a playbook, task or tool to an agent built this way, work in its app
directory and skip the parts of "Build an agent" that only apply to a new agent:
copying the sample (step 2), rewriting the whole instruction (step 4), and
deleting the sample's tools and variables (step 6).

1. Plan the new tasks as in step 3, together with the existing tasks that share
   tools or state with them.
1. Add each new task to its playbook's task list, to `PLAYBOOK_TASKS`, and as a
   new `current_task` branch in `instruction.txt`, and mention the new request
   in that playbook's line in the playbook list. A new playbook also needs a
   line in the playbook list, its own task list branch, and an entry in
   `VALID_PLAYBOOKS`. Update `<role>` if the agent now covers more.
1. Write new tools as in step 6, and name each one only in the branches where
   the model needs it.
1. Check, push and test as in steps 7 to 9.

## When the model sees or calls the wrong tool

The model only sees the tools that the rendered instruction names, so the cause
is in the instruction it got for that state.

1. Find the turn in the stored conversation (`cxas trace get`, as in step 9)
   and work out the state at that point: `current_playbook`, `current_task`,
   and the variables the instruction branches on.
1. Render that state with `check_tool_visibility.py --state '{...}' --print` to
   read the instruction and the tool list the model had. A tool that shouldn't
   be visible is named somewhere in that text, often in a shared block, a Jinja
   comment, or a branch that covers more states than intended. If the tool
   belongs there, look for the wording that sent the model to it.
1. Fix the instruction rather than adding code, reproduce the flow with
   `run_locally.py`, and run the checks in step 7 again.

## Checklist

- [ ] The root agent has no child agents.
- [ ] For a new agent, none of the sample's order tools or variables are left,
  and `GREETING`, the agent `description` and `<role>` describe it.
- [ ] `VALID_PLAYBOOKS` and `PLAYBOOK_TASKS` match the instruction exactly.
- [ ] Only `open_playbook` and `graceful_end_session` are named in static
  text; no tool names in comments or tag names; no playbook or task shares a
  tool's name.
- [ ] State-dependent rules are Jinja branches, and values use `{{var}}`.
- [ ] Every variable the instruction or tools use is declared in `app.json`.
- [ ] Tool JSON descriptions match the docstrings (`sync_tool_descriptions.py --check`).
- [ ] `cxas lint` passes, `check_tool_visibility.py` reports no problems, and
  a `run_locally.py` flow per task runs without errors.
