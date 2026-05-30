# Chat Debugger

Programmatic conversation debugging using `cxas chat-step`. Reproduce issues, step through turns, inspect slot machine state, and diagnose behavioral bugs.

**When to use this** instead of `debug.md`: Use `debug.md` for eval failure triage (pass rates, categories, scoring). Use this for **live conversation debugging** — "why did the agent say X?", "slots are getting lost", "preempt seems stuck", "the flow switch drops data".

## Quick Reference

```bash
# Resolve app name
cxas apps list --project-id <project_id> --location <location>

# Start a new debug session (turn 1)
cxas chat-step --app-name <full_resource_name> \
  -m "I'd like to book a table for 4" \
  --session-file /tmp/cxas-debug.json \
  --with-log --with-slots --with-flow-context

# Continue the conversation (turn 2+)
cxas chat-step --app-name <full_resource_name> \
  -m "Friday evening" \
  --session-file /tmp/cxas-debug.json \
  --with-log --with-slots

# Inspect without advancing (no message sent)
cxas chat-step --app-name <full_resource_name> \
  --session-file /tmp/cxas-debug.json \
  --inspect-only --with-log debug --with-slots --with-flow-context

# Get formatted trace
cxas chat-step --app-name <full_resource_name> \
  --session-file /tmp/cxas-debug.json \
  --inspect-only --with-trace-report text

# Get full conversation history
cxas chat-step --app-name <full_resource_name> \
  --session-file /tmp/cxas-debug.json \
  --inspect-only --with-turns

# File a bug report
cxas chat-step --app-name <full_resource_name> \
  --session-file /tmp/cxas-debug.json \
  --bug "Agent echoes verbatim instead of rephrasing on readback"
```

> **CLI note:** If `cxas` binary fails with import errors, use `.venv/bin/python3 -m cxas_scrapi.cli.main chat-step ...` instead.

## Workflow

### Step 1: Set Up

1. Get the full app resource name from memory, `gecx-config.json`, or `cxas apps list`
2. Create a session file path: `/tmp/cxas-debug-<issue>.json`
3. Decide channel if the app uses channel-specific behavior: `--channel web` or `--channel audio`

### Step 2: Reproduce

Send the user's reported message sequence one turn at a time. After each turn, examine the output:

```bash
# Turn 1
cxas chat-step --app-name $APP -m "first message" \
  --session-file /tmp/debug.json --with-log --with-slots --with-flow-context

# Read the JSON output. Check:
# - agent_text: what the agent said
# - state.filled_slots: what slots are filled
# - state.active_agent: which agent is handling
# - sm_log: SM events for this turn
# - slot_inspection: deep slot state
# - flow_context: active flow, suspended flows

# Turn 2
cxas chat-step --app-name $APP -m "second message" \
  --session-file /tmp/debug.json --with-log --with-slots
```

### Step 3: Inspect

When something looks wrong, use `--inspect-only` with deeper inspection:

```bash
# Full debug-level log (shows all internal engine events)
cxas chat-step --app-name $APP --session-file /tmp/debug.json \
  --inspect-only --with-log debug --with-slots --with-flow-context

# Get the trace for latency/timing analysis
cxas chat-step --app-name $APP --session-file /tmp/debug.json \
  --inspect-only --with-trace-report text
```

### Step 4: Diagnose

Match what you see to the diagnostic patterns below. For SM event details, see `references/sm-events.md`.

### Step 5: Report

Either file a bug or report findings to the user:

```bash
# File a bug with evidence
cxas chat-step --app-name $APP --session-file /tmp/debug.json \
  --bug "Slots lost on flow switch: party_size and date not transferred"
```

## Reading the Output

The JSON output from `chat-step` has these key sections:

| Field | What to look for |
|-------|-----------------|
| `agent_text` | What the agent actually said — compare to expected behavior |
| `tool_calls` | Which tools were called and with what args |
| `tool_responses` | What tools returned — look for error codes |
| `agent_transfer` | If/where the agent transferred |
| `state.filled_slots` | Currently filled slot values |
| `state.slot_machine` | Raw SM state — check `pending`, `phase`, `status` |
| `state.active_agent` | Which agent is currently handling the conversation |
| `sm_log` | SM event timeline — **primary diagnostic source** |
| `slot_inspection` | Deep slot state: categories, DAG, configuration |
| `flow_context` | Active flow, agent-to-config mapping, suspended flows |

### SM Log: What to Look For

The `sm_log` is a list of events ordered chronologically within a turn. Key patterns:

- **Normal turn**: `invoke` → `progress` → `setter_stored` (×N) → `auto_confirm` or `announce` → `progress`
- **Task firing**: ... → `task_completed` → `on_complete` (if flow done)
- **Transfer**: `bootstrap_transfer` → `transfer_dispatched` → `config_loaded` (on receiving agent)
- **Error**: `slot_error` → (retry) → `slot_error_exhaust`
- **Preemption**: `preemption` — LLM was bypassed, response is deterministic

## Diagnostic Patterns

### Agent echoes user text verbatim (no rephrasing)

**Symptom**: Agent says exactly what the user said instead of naturally incorporating it.

**Check**: Look for `preemption` in `sm_log`. If present, the LLM was bypassed — a callback returned a hardcoded `LlmResponse`.

**Root cause**: Usually `readback_transition` + unconditional `preempt = True` in the engine. When the user confirms + provides new data, inline-confirm fires and sets a readback_transition prefix. If the engine also preempts, the prefix gets prepended to the user's text and returned as-is.

**Fix**: Make preemption conditional — only preempt when there's no fresh readback pending (no new slots need confirmation via LLM).

### Slots lost on agent transfer / flow switch

**Symptom**: User provides slot data (e.g., "table for 4 on Friday") during a transfer, but the receiving agent doesn't see it.

**Check**:
1. Look for `bootstrap_transfer` — was the transfer triggered?
2. Look for `transfer_slots_consumed` — did the receiving agent consume the slots?
3. Look for `flow_state_saved` / `flow_state_restored` — was flow context preserved?
4. Check `_gate_user_text` in `state.slot_machine` — was the user's message captured?

**Root cause options**:
- `_gate_user_text` not captured during the early return path (status=complete or gate_active)
- `pass_through_on_transfer` not enabled (structured bootstrap disabled)
- Flow context not saved before switching

**Fix**: Ensure `_gate_user_text` is captured in ALL early return paths of `before_model_callback`, not just the gate path.

### Agent not progressing (stuck in loop)

**Symptom**: Agent keeps asking the same question or doesn't move forward.

**Check**:
1. `steer_back_*` events — is the engine detecting off-topic input?
2. `slot_error` / `slot_error_exhaust` — is a slot repeatedly failing validation?
3. `task_exhaust` — did a task fail all retries?
4. `announce_cycle_break` — is readback stuck in a loop?

**Root cause options**:
- Steer-back counter incremented incorrectly (user's on-topic message misclassified)
- Slot validation too strict (rejecting valid input)
- Task dependency not met (prereq slot missing)

### Wrong slot value stored

**Symptom**: A slot has the wrong value (e.g., "2" instead of "4" for party_size).

**Check**:
1. Find `setter_stored` or `multi_setter_stored` events for that slot — what value was stored?
2. Check `correction_applied` — was a correction attempted?
3. Check `slot_correction_pending` / `slot_correction_overwrite` — was a correction queued?
4. Look at `tool_calls` — which setter tool was called and with what args?

**Root cause options**:
- LLM extracted wrong value from user message
- Multi-setter mapped value to wrong slot
- Correction tool overwrote the value

### Readback / confirmation wrong

**Symptom**: Agent confirms wrong values or skips confirmation entirely.

**Check**:
1. `auto_confirm` — was an automatic confirm triggered? What was the user message?
2. `auto_confirm_inline` — which slots were committed?
3. Check `state.slot_machine.pending` — are there uncommitted slots?
4. Check `state.slot_machine.phase` — is it `awaiting_readback` or `awaiting_confirmation`?

**Root cause options**:
- `_starts_affirmative()` matching too broadly (user didn't mean "yes")
- Inline confirm committing slots before readback
- Pending slots not populated correctly

### Task not firing

**Symptom**: All prerequisite slots are filled but the task doesn't execute.

**Check**:
1. Look at `slot_inspection` → DAG section — are all prereqs shown as filled?
2. Check for `task_refire_blocked` — task already succeeded and is locked
3. Check `progress` events — what `action` does the engine report?
4. Verify the task tool is available (`missing_tools` event)

**Root cause options**:
- Prereq slot is in `pending`, not `filled` (needs confirmation first)
- Task already succeeded — status is locked
- Task tool name doesn't match the tool available on the agent

### Flow doesn't start (gate not opening)

**Symptom**: Agent responds but doesn't enter the slot filling flow.

**Check**:
1. `gate_active` — is the gate slot check firing?
2. `config_loaded` — was the config loaded at all?
3. Check `flow_context.active_config_id` — is a config active?
4. `config_validation_failed` — did the config fail validation?

**Root cause options**:
- Gate slot not filled (user hasn't triggered the flow entry condition)
- Config not registered in `before_model_callback`
- Config validation errors blocking load

## Session File Management

Session files persist conversation state between `chat-step` invocations:

```json
{
  "session_id": "abc-123",
  "turn_count": 3,
  "app_name": "projects/.../apps/...",
  "variable_state": {
    "sm": { "filled": {...}, "_log": [...] },
    "_active_config_id": "reservation",
    "agent_config_map": "{...}"
  }
}
```

- **New session**: omit `--session-file` or point to a non-existent path
- **Resume session**: point to an existing session file
- **Inspect without advancing**: use `--inspect-only` with existing session file
- **Compare sessions**: run the same message sequence with different agent code, save to different session files, compare the JSON output

### Cleanup

```bash
rm /tmp/cxas-debug-*.json
```

## Combining with Eval Debugging

When an eval fails and you need to understand why:

1. Read the eval's turn sequence from the golden/sim YAML
2. Replay the sequence with `chat-step`, inspecting after each turn
3. Compare the `chat-step` output against the eval's expected behavior
4. Use `--with-log debug` at the turn where behavior diverges

This bridges the gap between `debug.md` (which tells you WHAT failed) and understanding WHY it failed at the SM level.
