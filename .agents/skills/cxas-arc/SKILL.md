---
name: cxas-arc
description: >-
  Talks to Arc, the AI assistant built into the CX Agent Studio console, through
  the `cxas arc` CLI to ask questions about a CX Agent Studio app, design or
  revise an agent contract, build an agent from a PRD or requirements, import
  attached app or evaluation files, and run or hillclimb evaluations. Use when
  the user says "ask Arc", mentions the CX Agent Studio "AI assistant", asks for
  an "agent contract", wants to "build an agent from a PRD with Arc", wants to
  "hillclimb evaluations with Arc", or mentions `cxas arc`. Don't use for
  deterministic resource CRUD or CI (pull, push, lint, run evals, push evals)
  when Arc's reasoning is not requested; use the regular `cxas` commands and the
  `cxas-agent-foundry` skill instead.
---

# Arc (CX Agent Studio AI assistant)

Arc is the conversational builder assistant embedded in the CX Agent Studio console. `cxas arc` drives it over the same public API the console uses, so an agent session from the terminal gets the same streamed answers, confirmation cards, clarification option panels, evaluation-run resumption and artifacts a console user gets. Full flag table: [references/arc_cli_reference.md](references/arc_cli_reference.md) (or run `cxas arc <command> --help`).

## When to use Arc vs. the rest of `cxas`

| Need                                                                                                                                                                                         | Use                                                                     |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| Export, edit, lint, push an app; push or run evaluation YAML; CI gates                                                                                                                       | `cxas pull / push / lint / push-eval / run` (deterministic, scriptable) |
| Explain what an app does, design or revise an agent contract, build an agent from a PRD, import an attached app or eval bundle "as Arc sees fit", diagnose and hillclimb failing evaluations | `cxas arc`                                                              |

Arc keeps state in an *assistant session* on the app. The CLI remembers one sticky session per app in `~/.cxas/arc_sessions.json` and the last unanswered card or option panel in `~/.cxas/arc_sessions.pending.json`.

## Operating modes

### Co-Pilot (default)

1. **Read the user's context first**: the PRD, design docs, local app directory, eval files. Summarize requirements before involving Arc.
1. **Send one `cxas arc chat`** with the question or the synthesized requirements (`--message-file` for anything longer than a paragraph; `--new-session` for a new task).
1. **Inspect the output sections**: `### [Tool Activity]`, the answer text, `### [Errors]`, `### [Artifact] agent_contract.yaml`, `### [Contract Progress]`, `### [Confirmation Requests] ⚠️ awaiting your answer`, `### [Clarification Options]`, `### [Next Steps]`.
1. **Auto-answer what the context already covers**: if Arc asks something answered in the PRD or code, reply in the next `cxas arc chat` turn without interrupting the user.
1. **Escalate genuine product decisions**: when Arc asks a business question the context does not answer, or presents a completed contract for sign-off, show the options or summary to the user, get their decision, and relay it to Arc in natural language.
1. **Relay results** back to the user with the session id so they can continue in the console.

### Autopilot ("build end-to-end", "auto-answer Arc's questions")

1. Act as the product owner across consecutive turns on the same sticky session.
1. Answer `### [Clarification Options]` with `cxas arc answer --choices ...` (or `--choice` when free-text answers contain commas) using best judgment grounded in the PRD.
1. Answer `### [Confirmation Requests]` with `cxas arc confirm --confirmed` (or `--choices` for question cards). When using `--confirmation-policy click` for unattended runs on non-disposable apps, pass `--deny-tools "*delete*" --deny-tools "*deploy*" --deny-tools "*publish*"` so destructive or production-publishing tools are declined unless the user explicitly authorized them.
1. Iterate until the contract artifact reports draft completion / 100% across its pillars.
1. Ask Arc in natural language to finalize the contract and build the agent; confirm build cards while keeping destructive/deploy gates guarded as needed.
1. Ask Arc to generate and run evaluations, then to diagnose and fix failures and re-run. Evaluation runs are polled and the session resumes automatically.
1. Report pass rates per iteration and the final state to the user.

## Command cheat-sheet

Target an app with `--url "<console-url>"` or `--app-name projects/<project>/locations/<location>/apps/<app>` (short id plus `--project` and optional `--location`, defaulting to `us`, also works). Add `--format json` whenever you parse the result.

```bash
# Ask about an existing app
cxas arc chat --app-name <APP> --message "What does this app do? Summarize its agents, tools and guardrails."

# Start a new task from a PRD (fresh sticky session)
cxas arc chat --app-name <APP> --new-session --message-file <prd.md>

# Continue the conversation (same sticky session, no session flag needed)
cxas arc chat --app-name <APP> --message "1. ... 2. ... 3. ..."

# Clarification option panel -> pick per question (letter, number or free text)
cxas arc answer --app-name <APP> --choice A --choice C --choice "Only after 6pm, weekdays"
cxas arc answer --app-name <APP> --skip all

# Confirmation card -> click like the console
cxas arc confirm --app-name <APP> --confirmed
cxas arc confirm --app-name <APP> --declined
cxas arc confirm --app-name <APP> --choices B,A
cxas arc confirm --app-name <APP> --confirmation-id <ID> --confirmed

# Unattended: click through build cards while guarding destructive/deploy tools
cxas arc chat --app-name <APP> --confirmation-policy click \
  --deny-tools "*delete*" --deny-tools "*deploy*" \
  --message "Approved. Finalize the contract and build the agent."

# Attach a local app dir and/or eval YAML (zipped and uploaded for Arc)
cxas arc chat --app-name <APP> --attach <app_dir> --attach <evals_dir> --attach-dry-run
cxas arc chat --app-name <APP> --attach <app_dir> --attach <evals_dir> \
  --message "Import the attached app and its evaluations into this app, then run the evals and report per file what was imported or rejected."

# Long turns (builds, evaluation runs)
cxas arc chat --app-name <APP> --timeout-seconds 3600 --message "Run all evaluations and fix the failures."

# Bring Arc's changes back
cxas arc chat --app-name <APP> --download-artifacts <dir> --message "Export the files you changed as an artifact bundle."

# Sessions and recovery
cxas arc sessions new    --app-name <APP>
cxas arc sessions list   --app-name <APP>
cxas arc sessions get    --app-name <APP>          # messages, artifacts, active turn
cxas arc cancel          --app-name <APP>          # stop an in-flight turn (may return UNIMPLEMENTED)
cxas arc suggestions     --app-name <APP>          # may return UNIMPLEMENTED where not enabled
```

JSON output (`--format json`): top-level `action`, `session_id`, `canonical_session_id`, `app_name`, `api_endpoint`, `client_profile`, `events`, `pending_confirmations`, `suggested_commands`, `notes`, `uploaded_attachments`, `downloaded_artifacts` (on turn failures, `action` is `"error"` with `error` and `partial_events`). Inside `events`: `text`, `errors`, `tool_calls`, `tool_responses`, `artifacts`, `option_questions`, `confirmation_requests`, `confirmation_actions`, `contract_metadata`, `initiated_lros`, `handoff`, `turn_completed`. Check `pending_confirmations` and `events.option_questions` after every turn to decide the next command; `suggested_commands` already contains runnable follow-ups. Exit codes: `0` success, `1` API/stream/bundle error, `2` CLI usage error, `3` empty turn (typically a missing or unreadable app).

## Card etiquette

- A card under `### [Confirmation Requests]` means Arc paused a gated tool (publish contract, update or deploy the app, delete resources, `ask_user` questions). Answer it with `cxas arc confirm`. Typing "yes" via `chat` is a *typed reply* handed to the paused tool, not a click; the CLI prints a note when you do that.
- In Co-Pilot mode never auto-confirm cards whose tool deploys, publishes, overwrites or deletes something without the user's explicit go-ahead. Show the card's `Tool` and `Context` and ask.
- `--confirmation-policy click` confirms gate cards and picks the first option on question cards for up to `--max-confirmation-rounds` (default 5). Use it only in Autopilot mode (adding `--deny-tools "*delete*" --deny-tools "*deploy*" --deny-tools "*publish*"` unless the user authorized those actions) or on apps the user has declared disposable. `mixed` (with `--typed-reply-probability` and `--confirmation-seed`) exists for testing the typed path; `--client-profile legacy` makes the server auto-deny gated tools and is only for reproducing old behaviour.
- `--confirmation-id` defaults to the latest pending card; pass it explicitly when several cards are open.

## Bootstrapping journey (PRD to evaluated agent)

1. **App**: `cxas create "<Name>" --app-id <app> --project-id <project> --location <location>` if the app does not exist yet. Arc sessions always live on an app.
1. **PRD**: `cxas arc chat --app-name <APP> --new-session --message-file prd.md`. Arc inspects the app and asks clarification questions.
1. **Clarifications**: answer with `cxas arc answer --choices` (or `--choice`) or a numbered `chat` reply (Co-Pilot: escalate real product decisions). Arc streams `### [Artifact] agent_contract.yaml` with a completion percentage and `### [Contract Progress]`.
1. **Contract 100%**: repeat until the artifact reports draft completion. Summarize the contract to the user for approval in Co-Pilot mode.
1. **Approve and build**: `cxas arc chat --message "Approved. Finalize the contract and build the agent now."`, then `cxas arc confirm --confirmed` for each card (or `--confirmation-policy click --deny-tools "*delete*"` in Autopilot). Arc creates variables, guardrails, tools with mocks and the agent hierarchy, runs smoke sessions and snapshots a version.
1. **Evaluations**: either push your own YAML with `cxas push-eval` and ask Arc to run it, or ask Arc to generate scenarios. Use `--timeout-seconds 3600` for runs; the CLI polls the evaluation run within that overall deadline and resumes the session with the pass rate.
1. **Hillclimb**: ask Arc to diagnose failing scenarios, fix generalizable causes (instructions, journeys, tool mocks, callbacks) rather than hard-code test literals, and re-run. Track the pass rate per turn; stop when the target is met or progress stalls.
1. **Bring it home**: `--download-artifacts <dir>` for artifacts Arc produced, then `cxas pull` to sync the app locally and let the user review the diff.

## Pitfalls

- **Long turns**: builds and evaluation runs can take many minutes. Pass `--timeout-seconds` (default 1800) generously; it governs the entire turn including LRO polling. Do not lower `--stream-idle-timeout` (default 180) below what Arc needs between tool calls.
- **Idle timeout / partial output**: on `Stream idle ...` or `Turn exceeded ...` the CLI prints `### [Turn failed] partial output` (or `partial_events` in JSON). The server may still have finished the turn: run `cxas arc sessions get` to read the latest messages and artifacts before resending, or `cxas arc cancel` to stop it.
- **Pending card note**: if `chat` prints "a confirmation card is pending ... sent as a typed reply", you probably meant `cxas arc confirm`.
- **New task, new session**: use `--new-session` so an old contract does not bleed into a new build. Use `--session-id` to return to an earlier session (`sessions list` shows them; `sessions get` inspects one without changing the sticky session).
- **Attachments**: bundle limits are 20 MiB zipped, 100 MiB raw, 10,000 files; `.git/`, `.ssh/`, `.venv/`, `node_modules/`, `__pycache__/`, build output trees, binary/media files, and secret-like files (`.env*`, keys, `*credentials*.json`, `service_account*.json`) are excluded by default. Preview with `--attach-dry-run` before uploading; pass `--bucket` if the default bucket cannot be derived.
- **Verify counts yourself**: after an import, confirm with `cxas pull` or the evaluation list rather than trusting Arc's summary alone.
- **`UNIMPLEMENTED` from `suggestions` or `cancel`** is expected where those endpoints are not enabled yet; `chat`, `confirm`, `answer` and `sessions` are unaffected. A turn you could not cancel still finishes server-side; read it with `cxas arc sessions get`.
- **Auth and endpoint**: `gcloud auth application-default login` or `CXAS_OAUTH_TOKEN`; override the API host with `CES_API_ENDPOINT` or `--api-endpoint` (non-`*.googleapis.com` hosts require `CXAS_ALLOW_ANY_API_ENDPOINT=1`).
