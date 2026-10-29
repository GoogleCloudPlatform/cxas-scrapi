# `cxas arc`

Arc is the AI assistant built into the CX Agent Studio console. It can explain
what an app does, design and revise an agent contract from your requirements,
build agents, tools and guardrails, generate and run evaluations, and iterate on
failures. `cxas arc` drives Arc from the terminal over the same public API the
console uses (`SessionService.StreamChatAiAssistant` on `ces.googleapis.com`),
so a scripted or agent-driven session exercises exactly the paths a console user
exercises: streamed turns, confirmation cards, clarification option panels,
client-managed long-running operations and session artifacts.

Use the rest of `cxas` (`pull`, `push`, `lint`, `run`, `push-eval`, …) for
deterministic CRUD and CI. Use `cxas arc` when you want Arc's reasoning: contract
design, building an agent from a PRD, importing an attached app or eval bundle,
or hillclimbing an evaluation pass rate.

## Prerequisites

- The CX Agent Studio API (`ces.googleapis.com`) is enabled in the project.
- Credentials: `gcloud auth application-default login`, or set
  `CXAS_OAUTH_TOKEN` / pass `--oauth-token` (see [CLI Reference](index.md)).
- IAM: a `roles/ces.*` role on the project (`roles/ces.admin` or
  `roles/ces.editor` for building; `roles/ces.viewer` is enough to ask
  questions). Attachments and `--download-artifacts` additionally need object
  read/write access on the artifacts bucket.
- Endpoint: the API host defaults to `ces.googleapis.com`. Override it with
  `CES_API_ENDPOINT` or `--api-endpoint`.

## Targeting an app

Every sub-command accepts one of:

| Form | Example |
|------|---------|
| Console URL | `--url "https://ces.cloud.google.com/projects/<project>/locations/<location>/apps/<app>"` (may end in `/assistantSessions/<id>`) |
| Full resource name | `--app-name projects/<project>/locations/<location>/apps/<app>` |
| Short id | `--app-name <app> --project <project> --location <location>` |

## Sub-commands

| Sub-command | Purpose |
|-------------|---------|
| `cxas arc chat` | Send a message (and optional attachments) on the sticky session. |
| `cxas arc confirm` | Click a pending confirmation card: confirm, decline, pick options, skip. |
| `cxas arc answer` | Submit a clarification option panel the way the console's Submit button does. |
| `cxas arc sessions new\|list\|get\|delete` | Manage assistant sessions on the app. |
| `cxas arc cancel` | Cancel the in-flight turn. |
| `cxas arc suggestions` | Print the onboarding suggestions the console shows on an empty session. |

Common flags (all sub-commands): `--url`, `--app-name`, `--project`,
`--location`, `--api-endpoint`, `--session-id`, `--new-session`,
`--session-store`, `--format markdown|json`, `--quiet`.

Turn flags (`chat`, `confirm`, `answer`): `--confirmation-policy`,
`--typed-reply-probability`, `--confirmation-seed`,
`--max-confirmation-rounds`, `--client-profile`, `--no-auto-resolve-lro`,
`--timeout-seconds`, `--stream-idle-timeout`, `--include-thoughts`,
`--no-tools`. Run `cxas arc <sub-command> --help` for the full list.

## Sticky sessions

A conversation with Arc lives in an *assistant session* on the app. The CLI
remembers one session per `(api_endpoint, app_name)` pair in
`~/.cxas/arc_sessions.json`, so consecutive commands continue the same
conversation without repeating `--session-id`.

```bash
cxas arc sessions new  --app-name projects/p/locations/l/apps/a   # fresh sticky session
cxas arc sessions list --app-name projects/p/locations/l/apps/a   # sessions on the app
cxas arc sessions get  --app-name projects/p/locations/l/apps/a   # messages, artifacts, active turn
cxas arc sessions delete --app-name projects/p/locations/l/apps/a # delete + forget locally
```

- `--new-session` on any turn command starts a fresh session first.
- `--session-id <short-id-or-full-name>` (or `/assistantSessions/<id>` in
  `--url`) switches to that session and makes it sticky.
- `--session-store PATH` relocates the store; the pending-card sidecar lives
  next to it as `<store>.pending.json` (default
  `~/.cxas/arc_sessions.pending.json`).

Sessions are created lazily by the first turn; `sessions new` only reserves a
local id. Progress messages are printed on stderr prefixed with `[arc]`;
suppress them with `--quiet`.

## `chat`

```bash
cxas arc chat --url "<console-url>" --message "What does this app do? Summarize its agents and tools."
cxas arc chat --url "<console-url>" --message-file prd.md --new-session
```

Provide `--message`/`-m` or `--message-file` (not both). A turn with only
`--attach` sends the default text "Attached files for you to review.".

The markdown output starts with a header line (`**Session**`, `**Endpoint**`,
`**Profile**`) followed by whichever of these sections the turn produced:

| Section | Meaning |
|---------|---------|
| `### [Tool Activity]` | Tool calls and responses Arc executed (hide with `--no-tools`). |
| *(plain text)* | Arc's streamed answer. |
| `### [Artifact] <name> (state=…, mime=…, gcs_uri=…)` | A streamed artifact, e.g. the agent contract YAML or an exported bundle. |
| `### [Contract Progress]` | Contract-related flags for this turn: related / draft complete / finalized. |
| `### [Confirmation Actions]` | Cards answered during this turn (by you or by a policy), typed vs. clicked. |
| `### [Confirmation Requests] ⚠️ awaiting your answer` | Arc paused a gated tool on a card. Answer it with `confirm`. |
| `### [Clarification Options]` | Multiple-choice questions without a card. Answer with `answer` or a plain `chat` message. |
| `### [Next Steps]` | Ready-to-run `confirm` / `answer` / `chat` commands for this session. |
| `### [Turn failed] partial output` | What streamed before an error or timeout. |

`--include-thoughts` adds a `### [Thoughts]` section when the server streams
them.

## Confirmation cards vs. typed replies

Before Arc runs a gated tool (publishing a contract, updating or deploying the
app, asking you questions) the server pauses the turn and streams a
*confirmation card*. The CLI renders it, records it in the pending sidecar, and
prints follow-up commands. Answer it the way a console user clicks:

```bash
cxas arc confirm --url "<console-url>" --confirmed            # click the confirm button
cxas arc confirm --url "<console-url>" --declined             # click the decline button
cxas arc confirm --url "<console-url>" --choices A,"Only after 6pm"   # question card: one answer per question
cxas arc confirm --url "<console-url>" --answer-text "..."    # raw answer text
cxas arc confirm --url "<console-url>" --skip round           # or --skip all
cxas arc confirm --url "<console-url>" --confirmation-id <id> --confirmed
```

Exactly one of `--confirmed`, `--declined`, `--choices`, `--answer-text` or
`--skip` is required. `--choices` takes a letter (`A`), a 1-based number (`1`)
or free text per question, comma-separated; an empty entry skips that question.
`--confirmation-id` defaults to the latest pending card of the sticky session.

Sending `cxas arc chat --message "yes"` while a card is pending is a *typed
reply*: the server hands the text to the paused tool as the user's answer rather
than approving it. The CLI prints a note when this happens. Prefer `confirm`
unless you are deliberately exercising the typed path.

Clarification option panels (`### [Clarification Options]`) have no card. Submit
them like the console's Submit button:

```bash
cxas arc answer --url "<console-url>" --choices B,A
cxas arc answer --url "<console-url>" --skip all
```

### Confirmation policies

`--confirmation-policy` (on `chat`, `confirm`, `answer`) decides what happens to
cards raised during the turn:

| Policy | Behaviour |
|--------|-----------|
| `manual` (default) | Show cards and stop; you answer them. |
| `click` | Confirm gate cards and answer question cards with the first option, until no card is pending (at most `--max-confirmation-rounds`, default 5). |
| `mixed` | Like `click`, but types the answer instead of clicking with probability `--typed-reply-probability` (default 0.2); `--confirmation-seed` makes the draw reproducible. |

Every automatic answer is listed under `### [Confirmation Actions]` (and
`events.confirmation_actions` in JSON) with the action, typed vs. clicked, the
policy and the round.

`--client-profile legacy` omits the `CONFIRMATION_CARDS` capability; the server
then auto-denies gated tools instead of asking. Use it only to reproduce that
historical behaviour.

## Evaluation runs and other long-running operations

When Arc starts an evaluation run or another long-running operation, the console
polls it and posts the result back into the session. `cxas arc` does the same by
default: it polls the operation (or the evaluation run it references, reporting
`evaluation run RUNNING: n/total` on stderr), then resumes the session with an
`operationCompletedEvent` so Arc continues with its analysis in the same
command. Evaluation results carry `pass_rate`, `passed_count`, `failed_count`
and `total_count` back to Arc.

- `--no-auto-resolve-lro` disables polling; the turn returns as soon as the
  operation is started.
- `--timeout-seconds` (default 1800) is the overall deadline of a turn.
- `--stream-idle-timeout` (default 180) is how long the CLI waits without any
  streamed event before giving up; Arc can be silent for a few minutes while it
  runs tools, so do not set this much lower.

## Attachments and artifacts

```bash
cxas arc chat --url "<console-url>" --attach ./cxas_app/MyApp --attach ./evals --attach-dry-run
cxas arc chat --url "<console-url>" --attach ./cxas_app/MyApp --attach ./evals \
  --message "Import the attached app and its evaluations into this app, then run the evals."
cxas arc chat --url "<console-url>" --attached-gcs-uri gs://bucket/path/bundle.zip --message "Review this."
```

`--attach` (repeatable) zips the files and directories verbatim (paths relative
to their common root, plus an `arc_bundle.json` manifest), uploads the zip to
`gs://<project-id>-agent-assist-artifacts/agent-assist-uploads/` (override with
`--bucket`), and attaches it to the turn. Pass an existing object with
`--attached-gcs-uri` (repeatable). A single `.zip` input is uploaded as-is and
cannot be combined with other paths.

- `--attach-dry-run` prints the bundle report (`### [Attachment Bundle]`)
  without uploading or calling Arc.
- Limits, checked before upload: 20 MiB zipped (`--attach-max-mb` can only
  lower this), 100 MiB uncompressed, 10,000 files. Errors list the largest
  entries so you can attach narrower paths.
- Default exclusions: `.git/`, `.hg/`, `.jj/`, `__pycache__/`,
  `node_modules/`, `bazel-*` build output trees, symlinks, and binary or
  media files (images, audio, video, archives, `.jar`/`.so`/`.par`/`.pyc`).
- The default bucket name needs the project *id*; when you target by project
  number the CLI resolves it through Resource Manager, or you can pass
  `--bucket` explicitly.

`--download-artifacts DIR` saves every artifact recorded on the session (for
example an exported app bundle Arc produced) into `DIR` after the turn and
lists them under `### [Downloaded Artifacts]`.

## `cancel`

```bash
cxas arc cancel --url "<console-url>"              # cancel the in-flight turn (may return UNIMPLEMENTED today)
cxas arc cancel --url "<console-url>" --turn-id <id>
```

## `suggestions`

```bash
cxas arc suggestions --url "<console-url>"
```

Prints the starter prompts the console shows on an empty session. On apps or
projects where the feature is not enabled the API returns `UNIMPLEMENTED`; that
is expected and does not affect `chat`.

## `--format json`

Every sub-command accepts `--format json`; the turn commands (`chat`,
`confirm`, `answer`) print one JSON object with a stable shape intended for
coding agents and scripts:

| Key | Content |
|-----|---------|
| `action` | `chat`, `confirm` or `answer`. |
| `session_id` | Short sticky session id. |
| `canonical_session_id` | Full `…/assistantSessions/<id>` resource name. |
| `app_name`, `api_endpoint`, `client_profile` | Resolved target. |
| `events` | The aggregated turn (see below). |
| `pending_confirmations` | Cards still awaiting an answer (same records as `events.confirmation_requests`, filtered). |
| `suggested_commands` | The `### [Next Steps]` commands as strings. |
| `notes` | Advisory notes, e.g. the typed-reply warning. |

`events` is the serialized `TurnStreamEvents` aggregate:

| `events.*` | Content |
|------------|---------|
| `text` | Arc's answer text (all resumed sub-streams concatenated). |
| `thoughts`, `statuses` | Streamed thoughts and status lines. |
| `tool_calls[]` | `{id, tool, display_name, args}` |
| `tool_responses[]` | `{id, tool, display_name, response}` |
| `artifacts[]` | `{artifact_id, display_name, mime_type, content, gcs_uri, state}` |
| `option_questions[]` | `{question, options[{title, details, submit_text}]}` |
| `confirmation_requests[]` | `{confirmation_id, context, positive_label, negative_label, questions[], tool, agent_name, expire_time}` |
| `confirmation_actions[]` | `{confirmation_id, tool, action, text, typed, policy, round, reason, followup_chars, followup_tool_calls}` |
| `contract_metadata` | `{contract_related, contract_finalized, contract_draft_complete}` or `null`. |
| `initiated_lros[]` | `{operation_name, target_resource_name, operation_type, status, metadata}` |
| `turn_completed` | `{turn_id, reason, final_sequence_number}` or `null`. |
| `last_sequence_number` | Last stream sequence number seen. |

`sessions list|get`, `cancel` and `suggestions` print the raw API resource (or
`{"action": "cancel", "session_id": …, "cancelled": bool}`). With
`--format json` the attachment and download reports go to stderr so stdout
stays parseable.

## End-to-end example

```bash
APP=projects/my-project/locations/us/apps/orders-assistant

# 1. Create an empty app (skip if it already exists).
cxas create "Orders Assistant" --app-id orders-assistant \
  --project-id my-project --location us

# 2. Submit the PRD on a fresh session. Arc inspects the app and asks
#    clarification questions (### [Clarification Options]).
cxas arc chat --app-name $APP --new-session --message-file prd.md

# 3. Answer them in your own words, or pick options; Arc drafts the contract
#    (### [Artifact] agent_contract.yaml, ### [Contract Progress]).
cxas arc chat --app-name $APP --message "1. Mock the order API with realistic payloads. 2. Escalate to a human after one failed lookup."
cxas arc answer --app-name $APP --choices A,C

# 4. Approve and build. Gated tools pause on cards; click through them.
cxas arc chat --app-name $APP --message "Approved. Finalize the contract and build the agent now."
cxas arc confirm --app-name $APP --confirmed

# 5. Generate and run evaluations. The evaluation run is polled and the
#    session resumes automatically with the pass rate.
cxas arc chat --app-name $APP --timeout-seconds 3600 \
  --message "Generate evaluation scenarios for the core journeys, run them, and fix any failures."

# 6. Bring Arc's work back locally.
cxas arc chat --app-name $APP --download-artifacts ./arc_artifacts \
  --message "Export the files you changed as an artifact bundle."
cxas pull $APP --project-id my-project --location us --target-dir ./cxas_app
```

For unattended runs add `--confirmation-policy click` to steps 4–5 and
`--format json` to parse the result.

## Troubleshooting

| Symptom | What to do |
|---------|------------|
| `HTTP 401` / `UNAUTHENTICATED` | Refresh credentials: `gcloud auth application-default login`, or check `CXAS_OAUTH_TOKEN` is current. |
| `HTTP 403` / `PERMISSION_DENIED` | Grant a `roles/ces.*` role on the project; for `--attach` / `--download-artifacts` also check Storage permissions on the bucket. |
| `HTTP 404` on the app | Check `--app-name` / `--url` (project id vs. number, location) and that `--api-endpoint` / `CES_API_ENDPOINT` points at the right host. |
| `Stream idle for 180s after N events.` | Arc was silent longer than `--stream-idle-timeout`. The partial output is printed under `### [Turn failed]`; run `cxas arc sessions get` to see whether the turn finished server-side, then continue on the same session (or `cancel` it). |
| `Turn exceeded 1800s deadline` | Raise `--timeout-seconds` for long builds and evaluation runs. |
| `Note: a confirmation card is pending; this message is sent as a typed reply` | You used `chat` while a card was pending. Use `cxas arc confirm` to click instead. |
| `No pending confirmation card is recorded for this session` | The sidecar has no card for this session (different machine or store). Pass `--confirmation-id` from the last output, or re-run `sessions get`. |
| `cxas arc suggestions` prints `UNIMPLEMENTED` | Onboarding suggestions are not enabled for this app; `chat` still works. |
| `cxas arc cancel` prints `UNIMPLEMENTED` | Turn cancellation is not yet served on the public endpoint for this app. The turn still finishes server-side; close the client and use `cxas arc sessions get` to read the result. |
| `Could not resolve the project id` on `--attach` | Pass `--bucket <project-id>-agent-assist-artifacts` (or any bucket you can write to). |

See also the [Python API](../api/core/arc.md) for `cxas_scrapi.core.arc.Arc`.
