---
name: cxas-self-steering-agent
description: >-
  Builds and refactors self-steering CXAS (CES, GECX) conversational agents for
  chat or voice, where the model runs the whole conversation like a contact
  center agent: one root agent, Jinja progressive disclosure (domains and
  workflows loaded through select_domain and load_workflow, or single-level
  load_workflow for up to 12 workflows, plus dynamic tool hiding) and
  deterministic actions (greeting, verbatim text, goodbye and live agent
  handoff through next_agent_actions). Ships a generic 6-tool / 5-variable
  starter agent (5-tool / 4-variable with --single-level) to build from. Use
  when creating a new CXAS agent, adding domains, workflows or tools to an
  agent built this way, replacing a steering agent with sub-agents or a single
  big prompt, or finding out why such an agent sees or calls the wrong tool.
---

# CXAS self-steering agent

This skill builds self-steering CXAS agents. Treat the model like a capable
person on a support team: give it the power to run the whole conversation and
the awareness to know where it is and why, and set up its job so it can do both.
There is no steering agent and no code making decisions behind the model's
back; the agent steers itself. Progressive disclosure and deterministic actions
are the two main techniques, built from a Jinja instruction template, typed
Python tools, session state, and one small callback on top of the loop CXAS
runs.

The skill bundles a generic starter agent
([resources/sample-agent](resources/sample-agent)) with 6 registered tools and
5 core variables (`5` tools and `4` variables with `--single-level`, zero
domain-specific tools to delete) and these scripts:

- `scripts/scaffold_from_sample.py` copies the starter into a new app
  directory (2-level `select_domain` + `load_workflow` by default, or 1-level
  `load_workflow` with `--single-level`).
- `scripts/check_tool_visibility.py` automatically detects 2-level
  (`select_domain` + `load_workflow`), 1-level (`load_workflow`), and legacy
  (`open_playbook` + `load_task`) topologies, renders the instruction across
  domains, workflows, and synthesized `{{% if ... %}}` branches, lists the tools
  the model can see, and catches template and tool anti-patterns `cxas lint`
  misses.
- `scripts/sync_tool_descriptions.py` copies tool docstrings into tool JSON
  files and creates missing `<tool>.json` wrappers automatically.
- `scripts/run_locally.py` runs tools and the callback on your machine.
- `scripts/run_conversation.py` runs a test conversation against the deployed
  app.

Read [references/guide.md](references/guide.md) before designing or changing an
agent, and see
[references/terminology_benchmark.md](references/terminology_benchmark.md) for
the multi-model Gemini metaprompting study and live CES A/B benchmark comparing
2-level `select_domain` + `load_workflow`, 1-level `load_workflow`, and legacy
`open_playbook` + `load_task`.

## Before you start

- Install the CXAS SDK and CLI, which also brings in `jinja2`:
  `pip install cxas-scrapi` (use the workspace's virtual environment if it has
  one).
- Creating an app, pushing, and testing call the CES API, so log in first:
  `gcloud auth application-default login`. Building and checking locally do not
  need it.
- Know the GCP project and CES location (for example `us`) the app lives in.

## Rules

1. **The model steers the conversation.** Treat it like a capable person on a
   support team. It makes every conversational decision: what the customer
   needs, which domain to select, which workflow to load, what to ask, which
   tool to call, when to read a disclosure, and when to hand off or say goodbye.
   Give it the power to act (a tool for everything the agent might do, including
   ending the conversation and transferring) and the awareness to act well
   (where it is, what it can do right now, and why it did what it did before).
   Code never makes a conversational decision behind its back: never run regex,
   keyword, or substring checks on customer utterances or model replies in
   callbacks or tools to decide intent, trigger transfers, or override tool
   arguments. Give tools explicitly typed parameters (`bool` for confirmations,
   `int`/`float` for counts, `Literal[...]` for closed sets,
   `Optional[...] = None` for optional fields) so the model classifies what the
   customer meant, and only validate argument formats (digit count, date
   format) in tool code.
1. **One root agent for the conversation, no conversational sub-agents.** A
   sub-agent handoff reaches the model as `transfer_to_agent(agent_name: "...")`
   plus history lines like `[Other Agent] said: ...`, so the model announces
   "transfers" the customer never asked about. Keep conversational workflows in
   one root agent and swap workflow instructions inside it. If the app attaches
   platform guardrails with a `transferAgent` in `childAgents`, keep
   `transfer_to_agent` and `transferToAgent` hidden in the callback so the root
   model cannot invoke them directly.
1. **Progressive disclosure with flat top-level blocks (2-level vs.
   `--single-level`).**
   - **Choose 1-level or 2-level based on workflow count:**
     - **1-level (`<= 12` total workflows, `--single-level`)**: Use a single
       always-visible `<workflow_list>` and `load_workflow` without an outer
       `select_domain` hop. Eliminating the outer hop cuts Turn-1 latency by
       `21%-33%` (`~510-775 ms`) and router hops by `42%-55%` while keeping
       dynamic tool hiding intact.
     - **2-level (`15+` workflows across multiple business domains, default)**:
       Group related workflows into domains (`<domain_list>` -> `select_domain`
       -> `<workflow_list>` -> `load_workflow`), keeping each menu short so the
       model never picks from a long list at either layer.
   - Keep `instruction.txt` flat instead of deeply nested wrappers: `<role>`,
     `<constraints>`, `<anytime_actions>`, `<domain_list>` (always shown in
     2-level mode), `<workflow_list>` (guarded by `{{% if current_domain %}}`
     in 2-level mode or always shown in 1-level mode, including the tool-list
     rule), `<status>` (holding `Today's date is {{current_date}}.` and current
     domain/workflow status), `{{% if pending_handoff_reason %}}<pending_handoff>`,
     and `{{% elif current_workflow == '...' %}}<workflow name="...">`
     containing `<workflow_rules>`, optional `<collected>`, and `<current_step>`.
     Never wrap instructions in `<context>` (banned by `cxas lint` `I015`).
     Never hide `<domain_list>` once a domain is selected or `<workflow_list>`
     once a workflow is loaded. When multiple workflows share a prerequisite
     (such as customer verification or record lookup), put that prerequisite in
     one shared Jinja-guarded block rather than repeating it across every
     `<workflow>` branch.
1. **Router tools, typed parameters, and fact-only returns.**
   `select_domain(domain_name, rationale)` and
   `load_workflow(workflow_name, rationale)` check exact names (no aliases),
   return fact-only dicts (`{"status": "ok", ...}` or
   `{"status": "error", "error": "...", ...}` — no conversational `"message"`
   prose keys), and set state. In 2-level mode, `select_domain` resets
   `current_workflow = ""` and `load_workflow` returns `no_selected_domain` or
   `workflow_in_different_domain` cleanly on mismatches. Keep a required
   one-sentence `rationale` on routing calls so later turns know why the
   transition happened. Put explicit parameter types and `-> dict[str, Any]`
   return annotations on every Python tool (`T016`), and import `Any` /
   `Optional` from `typing` for CES sandbox compatibility (`C019`).
1. **Branch with Jinja, not prose, and render one step at a time.** CXAS
   renders `{{var}}` in place, so `If {{record_id}} is empty, ...` reaches the
   model as `If is empty, ...`. Use double-brace control tags
   (`{{% if ... %}}`, `{{% elif ... %}}`, `{{% else %}}`, `{{% endif %}}`) so
   the model sees only the active step without injecting `<state_update>`
   blocks into history. Render values with bare `{{var}}` or dot access
   `{{record.status}}` (never expressions like `{{ x or "none" }}`, never
   subscripts like `record['status']`, never `not in`, and never `{var}`). Never
   put `<context>` (`I015`), `{{# ... #}}` / `{# ... #}` comments,
   `<session_snapshot>` state dumps, prose state checks (`If verified is
   false...`), or sample confirmation codes (`NQAA123456`) in `instruction.txt`.
   Keep edit tools and read-only lookups customers may invoke early inside
   `<workflow_rules>`, clear dependent state when an earlier answer changes, and
   state in every done step what can still change (or offer a representative).
1. **Static anytime actions and pending handoff.** `<anytime_actions>` above
   `<domain_list>` / `<workflow_list>` references
   `{@TOOL: graceful_end_session}` and `{@TOOL: execute_live_agent_handover}`
   (for explicit live-agent requests or unsupported changes), so the agent can
   end or hand off the conversation with one model call from any state without
   extra router hops. Keep the unsupported-change catch-all free of domain
   examples so it does not contradict done steps that support amendments.
   `{{% if pending_handoff_reason %}}<pending_handoff>` runs before workflow
   branches so any domain tool can trigger an immediate handoff via
   `{@TOOL: execute_live_agent_handover}` by setting `pending_handoff_reason` in
   state (which `execute_live_agent_handover` reads with priority over LLM
   `reason` and resets to `""`).
1. **Dynamic tool hiding.** `_handle_dynamic_tool_hiding` iterates all
   `config.tools` entries, always hides `TRANSFER_TOOLS` (`transfer_to_agent`,
   `transferToAgent`), and hides every other tool whose name has no word-boundary
   match in the rendered instruction. Only `select_domain` (or `load_workflow` in
   `--single-level` mode), `graceful_end_session`, and
   `execute_live_agent_handover` may be named in static text (and
   `load_workflow` in 2-level mode only inside `{{% if current_domain %}}` or
   domain-gated `<status>`). Never put a tool name in a tag name
   (`<end_session>` would expose `end_session` on every turn) or an unstripped
   comment, never give a domain or workflow the same name as a tool, and mention
   a tool in every branch where the model may need it. Keep the "Your tool list
   only has the tools for the currently loaded workflow..." rule in
   `<workflow_list>`.
1. **Deterministic actions only where they matter.** Use code for the greeting,
   word-for-word disclosures, goodbye, and live agent handoff. Tools queue
   deterministic actions by writing `context.state["next_agent_actions"] = {...}`
   directly (`set_next_agent_actions` remains bundled as a compatibility
   helper). `_handle_next_agent_actions` pops `next_agent_actions`, emits
   `customized_response` and `end_session`, and latches
   `callback_context.state["next_agent_actions"] = {"end_session": end_session}`
   when ending the session so follow-up streaming calls cannot slip through to
   the model. Never write `None` into session state (`C011`); clear string
   variables with `""`. Don't chain workflow steps or switch `current_workflow`
   from domain tools.
1. **One small callback and clean state declarations.** A single
   `before_model_callback` runs `_handle_next_agent_actions`,
   `_handle_initial_greeting`, and `_handle_dynamic_tool_hiding` in order. Never
   add `after_model_callbacks` to inspect or rewrite model replies, and never
   append dynamic rules to `system_instruction` inside callbacks. Group related
   domain fields into typed `OBJECT` declarations in `app.json` rather than
   adding many flat scalar variables (`A015`), declare every read/written
   variable (`V100`, `V104`), avoid dead or write-only variables (`V105`/`V106`),
   and keep `cxaslint.yaml` `options.V105.ignore` for framework keys.

## Build an agent

1. Get a CES app ID. Create one with `cxas create --project-id <project>
   --location <location> <display-name>` (the app ID is the last part of the
   app name it prints), or use an existing app you own. Never push over an app
   someone else is using.

1. Copy the generic starter into a new app directory (pass `--single-level`
   when the agent has `<= 12` total workflows so it uses 1-level
   `load_workflow` without an outer `select_domain` hop):

   ```bash
   python3 <skill-dir>/scripts/scaffold_from_sample.py \
     --target-dir cxas_app/<app-dir> --app-id <app-id> \
     --project-id <project> --agent-name <Root_Agent_Name>
   ```

1. Plan before editing files. Write a table with one row per workflow (its
   domain if 2-level, the `<current_step>` chain, the tools each step uses, the
   state variables or `OBJECT` fields each tool reads or writes, which tools
   can change earlier answers, and what can change after completion). Show it to
   the user before writing files, unless they asked you to go ahead without
   checking in. Name domains and workflows differently from tools.

1. Edit `agents/<Root_Agent_Name>/instruction.txt`. Keep the flat blocks
   (`<role>`, `<constraints>`, `<anytime_actions>`, `<domain_list>` in 2-level
   mode, `<workflow_list>`, `<status>`, `<pending_handoff>`, and
   `<workflow name="...">` with `<workflow_rules>`, optional `<collected>`, and
   `<current_step>`), rewrite `<role>` and `<constraints>` for your domain, and
   add your domains, shared prerequisites, and workflows. Keep
   `Today's date is {{current_date}}.` inside `<status>` (never `<context>`).
   Update `GREETING` in
   `agents/<Root_Agent_Name>/before_model_callbacks/before_model_callbacks_01/python_code.py`
   and `description` in `agents/<Root_Agent_Name>/<Root_Agent_Name>.json`.

1. Update the router tools to match the instruction exactly: in 2-level mode,
   `VALID_DOMAINS` in `tools/select_domain/python_function/python_code.py` and
   `DOMAIN_WORKFLOWS` in `tools/load_workflow/python_function/python_code.py`;
   in `--single-level` mode, `VALID_WORKFLOWS` in
   `tools/load_workflow/python_function/python_code.py` (keep `"live_agent"` /
   `"live_agent_escalation"` if the agent supports live handoff). Replace the
   sample `rationale` examples in router docstrings with ones from your domain.

1. Add your domain tools and variables. The 2-level starter ships ONLY the 6
   core tools (`end_session`, `execute_live_agent_handover`,
   `graceful_end_session`, `load_workflow`, `select_domain`,
   `set_next_agent_actions`) and 5 core variables (`current_domain`,
   `current_workflow`, `next_agent_actions`, `played_greeting`,
   `pending_handoff_reason`); `--single-level` omits `select_domain` and
   `current_domain` (`5` tools, `4` variables). Reword
   `execute_live_agent_handover`'s docstring and default message if the handoff
   goes to someone other than a live representative. Write each domain tool in
   the same shape (`tools/<name>/python_function/python_code.py` plus
   `tools/<name>/<name>.json`, entry function first, explicit parameter types,
   `-> dict[str, Any]` return type, fact-only `status`/`error` dicts without
   `"message"` prose keys), add it to the root agent's `tools` list, and
   declare its state variables (preferring tool-grouped `OBJECT` variables) in
   `app.json` `variableDeclarations`. Then sync tool descriptions:

   ```bash
   python3 <skill-dir>/scripts/sync_tool_descriptions.py cxas_app/<app-dir>
   ```

   `sync_tool_descriptions.py` automatically creates a missing
   `tools/<name>/<name>.json` wrapper when only `python_code.py` exists, and
   updates existing `pythonFunction.description` fields from each entry
   function's docstring.

1. Check before pushing:

   ```bash
   cxas lint --app-dir cxas_app/<app-dir>
   python3 <skill-dir>/scripts/check_tool_visibility.py cxas_app/<app-dir>
   python3 <skill-dir>/scripts/run_locally.py cxas_app/<app-dir> callback \
     'select_domain={"domain_name": "<domain>", "rationale": "test"}' \
     'load_workflow={"workflow_name": "<workflow>", "rationale": "test"}' callback \
     '<tool>={"<arg>": "<value>"}' callback
   ```

   Keep the starter's `cxaslint.yaml`; it disables only the rules that don't fit
   this pattern (`I001`, `T001`, `T011`, `I012`, `A007`, `A009`, `T014`) and
   keeps `options.V105.ignore` for framework keys (`current_workflow`,
   `pending_handoff_reason`, `next_agent_actions` in 2-level mode;
   `pending_handoff_reason`, `next_agent_actions` in `--single-level` mode
   where `load_workflow` is the sole writer of `current_workflow`).
   `check_tool_visibility.py` automatically detects 2-level (`select_domain` +
   `load_workflow`), 1-level (`load_workflow`), and legacy (`open_playbook` +
   `load_task`) topologies, synthesizes branch states from
   `{{% if ... %}}` / `{{% elif ... %}}` conditions (including
   `pending_handoff_reason` and dotted `OBJECT` properties), and fails on
   missing router tools, undeclared instruction variables or `OBJECT`
   properties, single-brace control tags or `{var}` outputs, non-bare
   `{{ ... }}` output expressions, `not in` and subscripts, Jinja comments,
   `<session_snapshot>` state dumps, prose state checks on declared variables,
   hardcoded sample confirmation codes, tool names outside `{@TOOL: ...}` or
   inside `<role>`/`<context>`/`<constraints>` or start-state static text,
   verbatim repeat instructions, tools returning coaching prose (`message`,
   `instructions`, `phrase`, etc.) or spreading dicts (`**x`), missing handoff
   `reason` handling, `afterModelCallbacks`, hidden
   `<domain_list>`/`<workflow_list>` (or legacy `<playbook_list>`/`<task_list>`),
   routers writing non-router state or reading `rationale`, names shared by a
   domain/workflow and a tool, mismatched router lists, unused registered tools,
   and visible `end_session` or `set_next_agent_actions`. Pass extra state with
   `--state '{"current_domain": "...", "current_workflow": "...", ...}'` and
   `--print` to inspect rendered branches. `run_locally.py` walks flows through
   the real tool and callback code; the first `callback` step plays the
   greeting, and later ones print visible tools or queued responses.

1. Push:

   ```bash
   cxas push --app-dir cxas_app/<app-dir> --project-id <project> \
     --location <location> \
     --to projects/<project>/locations/<location>/apps/<app-id> --overwrite
   ```

   Wait about a minute before testing; a session that starts right after a push
   can still run the previous version.

1. Test the greeting, one request per domain/workflow, a goodbye, and a
   handoff. Run a scripted text conversation, then inspect the stored trace to
   verify tool calls, results, and state changes:

   ```bash
   python3 <skill-dir>/scripts/run_conversation.py \
     projects/<project>/locations/<location>/apps/<app-id> \
     "Hi." "<first request>" "<follow-up>" "That's all, thanks."
   cxas trace get <session-id> \
     --app-name projects/<project>/locations/<location>/apps/<app-id> --format text
   ```

   Test corrections too: change an earlier answer at a later step, edit a
   detail while reviewing a summary, ask for a change after completion, and ask
   a later step's question early.

## Add to an existing agent

To add a domain, workflow, or tool to an agent built this way, work in its app
directory and skip copying the starter (step 2) and rewriting `<role>` /
`<constraints>` from scratch (step 4):

1. Plan the new workflows as in step 3, together with existing workflows that
   share tools, prerequisites, or state with them.
1. Add each new workflow to `<workflow_list>`, to `DOMAIN_WORKFLOWS` (or
   `VALID_WORKFLOWS` in `--single-level` mode), and as a new
   `{{% elif current_workflow == '...' %}}<workflow name="...">` branch in
   `instruction.txt`, and mention the new capability in `<domain_list>` (in
   2-level mode). A new domain also needs a line in `<domain_list>`, its own
   branch in `<workflow_list>`, and an entry in `VALID_DOMAINS`. Update
   `<role>` if the agent's scope expanded.
1. Write new tools and grouped `OBJECT` variables as in step 6; keep edit and
   early-lookup tools in `<workflow_rules>` and name submit-style tools only in
   the step that uses them.
1. Check, push, and test as in steps 7 to 9.

## When the model sees or calls the wrong tool

The model only sees the tools that the rendered instruction names (with
`TRANSFER_TOOLS` always hidden), so the cause is in the instruction rendered for
that state or stale coaching prose in tool results.

1. Find the turn in the stored trace (`cxas trace get`, as in step 9) and work
   out the state at that point: `current_domain`, `current_workflow`,
   `pending_handoff_reason`, and any branch variables.
1. Render that state with `check_tool_visibility.py --state '{...}' --print` to
   read the instruction and tool list the model had. A tool that shouldn't be
   visible is named somewhere in that text—often in a shared block, an
   unstripped comment, or a branch that covers more states than intended.
1. Fix the instruction, tool docstring, or tool return fields rather than adding
   callbacks, reproduce the flow with `run_locally.py`, and re-run step 7 checks.

## Checklist

- [ ] Root agent has no conversational child agents, no `afterModelCallbacks`,
  and any guardrail `transferAgent` keeps `TRANSFER_TOOLS` hidden.
- [ ] `GREETING`, agent `description`, and `<role>` are customized for the
  domain.
- [ ] `VALID_DOMAINS` and `DOMAIN_WORKFLOWS` (or `VALID_WORKFLOWS` in
  `--single-level` mode) match `<domain_list>`, `<workflow_list>`, and
  `<workflow>` branches in `instruction.txt`.
- [ ] `instruction.txt` uses flat top-level blocks (`<role>`, `<constraints>`,
  `<anytime_actions>`, `<domain_list>` in 2-level mode, `<workflow_list>`,
  `<status>`, `<pending_handoff>`, `<workflow>`), keeps
  `Today's date is {{current_date}}.` in `<status>`, and never uses `<context>`.
- [ ] Only `select_domain` (or `load_workflow` in `--single-level` mode),
  `graceful_end_session`, and `execute_live_agent_handover` are named in static
  text; no Jinja comments and no tool names in tag names; no domain or workflow
  shares a tool's name; `end_session`, `set_next_agent_actions`, and
  `TRANSFER_TOOLS` stay hidden.
- [ ] State-dependent rules use double-brace `{{% if ... %}}` blocks (never
  prose state checks or `<session_snapshot>` dumps), values use bare `{{var}}` /
  dot access (no `not in`, no `x['k']`, no `{var}`), and shared prerequisites
  aren't duplicated across workflows.
- [ ] Edit and early-lookup tools sit in `<workflow_rules>`, edits clear
  dependent state, and done steps state what can still change.
- [ ] Tools use typed parameters, `-> dict[str, Any]` returns (`T016`),
  fact-only `status`/`error` dicts (no `"message"` prose keys, no `**x`
  spreads, no unmasked secrets), direct `context.state["next_agent_actions"]`
  writes, `""` (not `None`) when clearing state strings, and zero regex/phrase
  matching on utterances or replies.
- [ ] Every variable used in `instruction.txt`, tools, or callbacks is declared
  in `app.json` (preferring tool-grouped `OBJECT` declarations), with
  `cxaslint.yaml` `options.V105.ignore` kept for framework keys.
- [ ] `sync_tool_descriptions.py --check`, `cxas lint`,
  `check_tool_visibility.py`, and `run_locally.py` all pass without errors.
