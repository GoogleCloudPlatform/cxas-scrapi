# SM Event Tag Reference

Complete reference for slot machine `_log` entries. Use with `cxas chat-step --with-log` output.

Each entry in `sm["_log"]` is a dict: `{"src": "engine"|"callback"|"after_tool", "tag": "<tag>", "level": "DEBUG"|"INFO"|"WARN"|"ERROR", ...data}`.

## Flow Lifecycle

| Tag | Level | Source | Key Fields | Meaning |
|-----|-------|--------|------------|---------|
| `config_loaded` | INFO | callback | `config_id`, `n_slots`, `n_tasks` | Slot filling config loaded into SM — flow is starting |
| `config_resolved` | INFO | callback | `config_id` | Config selected for this agent (multi-config resolution) |
| `config_validation_failed` | ERROR | callback | `errors` | Config has structural errors — flow won't work |
| `cross_config_validation_failed` | ERROR | callback | `config_a`, `config_b`, `errors` | Two configs have conflicting declarations |
| `gate_active` | DEBUG | callback | `config_id`, `gate_slot` | Flow gated — waiting for gate slot before proceeding |
| `missing_tools` | WARN | callback | `missing` | Config references tools not available on this agent |
| `cancel_preempt` | INFO | callback | `config_id` | Cancelled a pending preemption (flow switching) |
| `cancel_flow_called` | INFO | after_tool | `reason` | User/agent explicitly cancelled the current flow |
| `terminal_fallback` | WARN | callback | | Engine hit a terminal state — fallback behavior triggered |

## Flow Context Switching

| Tag | Level | Source | Key Fields | Meaning |
|-----|-------|--------|------------|---------|
| `flow_state_saved` | INFO | after_tool | `flow`, `n_slots` | Flow paused — slots saved for later restoration |
| `flow_state_restored` | INFO | after_tool | `flow`, `n_slots` | Flow resumed — saved slots restored into SM |
| `bootstrap_stored` | INFO | after_tool | `slot`, `value` | Slot value pre-filled from bootstrap/transfer data |
| `bootstrap_transfer` | INFO | after_tool | `tool`, `target` | Agent transfer triggered with slot bootstrap |
| `bootstrap_reentry` | DEBUG | after_tool | `tool`, `target` | Re-entering same agent after bootstrap (no transfer needed) |
| `transfer_dispatched` | INFO | callback | `agent` | Transfer to another agent initiated |
| `transfer_slots_consumed` | INFO | callback | `slots` | Transferred slot values consumed by receiving agent |

## Slot Filling

| Tag | Level | Source | Key Fields | Meaning |
|-----|-------|--------|------------|---------|
| `setter_stored` | INFO | after_tool | `tool`, `slot`, `value` | Single-slot setter stored a value |
| `multi_setter_stored` | INFO | after_tool | `tool`, `slots` | Multi-slot setter stored values |
| `multi_setter_error` | WARN | after_tool | `tool`, `error` | Multi-setter failed for some slots |
| `setter_error` | WARN | after_tool | `tool`, `slot`, `code` | Single setter failed validation |
| `fill_slots` | DEBUG | engine | `slot`, `value` | Internal: slot value committed to filled dict |
| `slot_deactivated` | DEBUG | engine | `slot`, `source` | Slot removed from active tracking (`source`: filled/pending/deferred) |
| `inferred_slots` | INFO | after_tool | `slots` | Slots inferred from other slot values |
| `prereq_not_met` | WARN | after_tool | `slot`, `missing` | Slot can't be filled — prerequisite slot not yet filled |
| `validation_failed` | WARN | after_tool | `slot`, `code` | Slot value failed validation rule |

## Confirmation & Readback

| Tag | Level | Source | Key Fields | Meaning |
|-----|-------|--------|------------|---------|
| `auto_confirm` | INFO | engine | `user_msg` | User confirmed — pending slots committed |
| `auto_confirm_inline` | INFO | engine | `committed` | Inline confirm: user said yes + provided new data in same message |
| `announce` | DEBUG | engine | `slot` | Slot announced to user (readback) |
| `announce_cycle_break` | WARN | engine | `slot` | Announce cycle broken to prevent infinite readback loop |

## Corrections

| Tag | Level | Source | Key Fields | Meaning |
|-----|-------|--------|------------|---------|
| `correction_applied` | INFO | engine | `slot`, `value`, `old` | Slot value corrected (old → new) |
| `slot_correction_pending` | INFO | after_tool | `tool`, `slot`, `value` | Correction queued — awaiting confirmation |
| `slot_correction_overwrite` | INFO | after_tool | `tool`, `slot`, `value` | Correction overwrote existing value directly |
| `correction_not_found` | WARN | after_tool | `tool`, `slot` | Correction requested but target slot not found |
| `rejection_applied` | INFO | engine | `slot` | User rejected a readback value |

## Tasks

| Tag | Level | Source | Key Fields | Meaning |
|-----|-------|--------|------------|---------|
| `task` | INFO/WARN | engine | `name`, `ok` | Task completed (ok=True) or failed (ok=False) |
| `task_completed` | INFO/WARN | after_tool | `name`, `success` | Task tool returned result |
| `task_exhaust` | ERROR | engine | `name` | Task failed all retry attempts |
| `task_refire_blocked` | WARN | engine | `task` | Task would fire again but is blocked (already succeeded) |
| `on_complete` | INFO | engine | `task`, `action` | Flow completed — running on_complete action |
| `on_complete_auto_resume` | INFO | engine | `task`, `flow` | Flow completed — auto-resuming a suspended flow |

## Slot Errors

| Tag | Level | Source | Key Fields | Meaning |
|-----|-------|--------|------------|---------|
| `slot_error` | WARN | engine | `slot`, `code`, `retries` | Slot validation error with retry count |
| `slot_error_exhaust` | ERROR | engine | `slot` | Slot exhausted all retries — giving up |

## Steering (Off-Topic Detection)

| Tag | Level | Source | Key Fields | Meaning |
|-----|-------|--------|------------|---------|
| `steer_back_soft` | INFO | engine | `turns`, `directive` | Soft redirect — gentle nudge back to flow |
| `steer_back_hard` | WARN | engine | `turns`, `msg` | Hard redirect — explicit instruction to stay on topic |
| `steer_back_escalate` | WARN | engine | `turns` | Escalation — too many off-topic turns, transferring out |
| `steer_back_correction_yield` | DEBUG | engine | | Steer-back yielded to a pending correction |
| `steer_back_correction_grace` | DEBUG | engine | | Steer-back gave grace period for correction to complete |

## Engine Internals

| Tag | Level | Source | Key Fields | Meaning |
|-----|-------|--------|------------|---------|
| `invoke` | DEBUG | engine | | Engine invoked for this turn |
| `progress` | DEBUG | engine | `phase`, `action`, `slots_*` | Engine progress snapshot (phase, DAG state) |
| `preemption` | INFO | callback | `config_id`, `msg` | LLM bypassed — deterministic response injected |
| `payload_route` | DEBUG | engine | `path` | Internal routing decision for preempt/stash/question |
| `payloads_injected` | DEBUG | callback | | Stashed payloads injected into conversation |
| `re_deferred` | DEBUG | engine | `slot`, `task` | Slot re-deferred after partial task completion |
