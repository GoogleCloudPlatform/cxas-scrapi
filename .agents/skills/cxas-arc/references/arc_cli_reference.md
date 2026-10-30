# `cxas arc` flag reference

Generated from the `argparse` definitions in `cxas_scrapi/cli/arc_cli.py`. Run `cxas arc <command> --help` for the live text.

## Sub-commands

| Command                    | Purpose                                  |
| -------------------------- | ---------------------------------------- |
| `cxas arc chat`            | Send a message on the sticky session.    |
| `cxas arc confirm`         | Answer the pending confirmation card.    |
| `cxas arc answer`          | Answer a clarification option panel.     |
| `cxas arc sessions list`   | List sessions on the app.                |
| `cxas arc sessions get`    | Show the sticky (or given) session.      |
| `cxas arc sessions new`    | Start a fresh sticky session.            |
| `cxas arc sessions delete` | Delete the sticky (or given) session.    |
| `cxas arc cancel`          | Cancel the in-flight turn.               |
| `cxas arc suggestions`     | Show onboarding suggestions for the app. |

## Target and session flags (all sub-commands)

Available on every `cxas arc` sub-command.

| Flag                              | Default                     | Description                                                                                                        |
| --------------------------------- | --------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `--app-name` `APP_NAME`           | —                           | Full app resource name (`projects/*/locations/*/apps/*`) or a short app id combined with `--project`/`--location`. |
| `--url` `URL`                     | —                           | CX Agent Studio console URL of the app (may include `/assistantSessions/<id>`); alternative to `--app-name`.       |
| `--project` `PROJECT`             | —                           | Project id or number for a short `--app-name`.                                                                     |
| `--location` `LOCATION`           | —                           | Location for a short `--app-name` (default: $CES_LOCATION or us).                                                  |
| `--api-endpoint` `API_ENDPOINT`   | —                           | API host (default: $CES_API_ENDPOINT or ces.googleapis.com).                                                       |
| `--session-id` `SESSION_ID`       | —                           | Use this session (short id or full name).                                                                          |
| `--new-session`                   | —                           | Start a fresh sticky session.                                                                                      |
| `--session-store` `SESSION_STORE` | `~/.cxas/arc_sessions.json` | Sticky session store (default: `~/.cxas/arc_sessions.json`).                                                       |
| `--format` `{markdown,json}`      | `markdown`                  | Output format (`json` is stable for agents).                                                                       |
| `--quiet`                         | —                           | Suppress `[arc]` progress on stderr.                                                                               |

## Turn flags (`chat`, `confirm`, `answer`)

Available on the sub-commands that send a turn to Arc.

| Flag                                                  | Default   | Description                                                                                                                                                                 |
| ----------------------------------------------------- | --------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--confirmation-policy` `{manual,click,mixed}`        | `manual`  | manual: show cards and stop; click: confirm gate cards and answer questions with the first option; mixed: like click but types the answer with `--typed-reply-probability`. |
| `--deny-tools` `GLOB`                                 | —         | Tool name glob that `click`/`mixed` must decline instead of confirming (repeatable, e.g. `*delete*` or `*deploy*`).                                                         |
| `--typed-reply-probability` `TYPED_REPLY_PROBABILITY` | `0.2`     | Probability of typing instead of clicking under `mixed` policy.                                                                                                             |
| `--confirmation-seed` `CONFIRMATION_SEED`             | —         | RNG seed for reproducible `mixed` policy decisions.                                                                                                                         |
| `--max-confirmation-rounds` `MAX_CONFIRMATION_ROUNDS` | `5`       | Maximum confirmation rounds auto-answered in a single turn.                                                                                                                 |
| `--client-profile` `{console,legacy}`                 | `console` | `legacy` omits `CONFIRMATION_CARDS` (server auto-denies gated tools).                                                                                                       |
| `--no-auto-resolve-lro`                               | —         | Do not poll evaluation runs / operations and resume the session.                                                                                                            |
| `--timeout-seconds` `TIMEOUT_SECONDS`                 | `1800`    | Overall deadline in seconds for the turn and any polled LROs.                                                                                                               |
| `--stream-idle-timeout` `STREAM_IDLE_TIMEOUT`         | `180.0`   | Seconds without streamed events before giving up.                                                                                                                           |
| `--include-thoughts`                                  | —         | Include streamed model thoughts in markdown output.                                                                                                                         |
| `--no-tools`                                          | —         | Hide tool activity.                                                                                                                                                         |

## `cxas arc chat` flags

| Flag                              | Default | Description                                                               |
| --------------------------------- | ------- | ------------------------------------------------------------------------- |
| `--message`, `-m` `MESSAGE`       | —       | User message.                                                             |
| `--message-file` `MESSAGE_FILE`   | —       | Read the message from a file.                                             |
| `--attach` `PATH`                 | —       | File or directory to zip and attach (repeatable).                         |
| `--attach-dry-run`                | —       | Only list the bundle.                                                     |
| `--attach-allow-secrets`          | —       | Include `.env*`, key and credential files excluded by default.            |
| `--attach-max-mb` `ATTACH_MAX_MB` | `20`    | Zip size cap in MiB.                                                      |
| `--bucket` `BUCKET`               | —       | GCS bucket for attachments (default: `<project>-agent-assist-artifacts`). |
| `--attached-gcs-uri` `GS_URI`     | —       | Attach an existing `gs://` object (repeatable).                           |
| `--download-artifacts` `DIR`      | —       | Download session artifacts after the turn.                                |

## `cxas arc confirm` flags

| Flag                                  | Default | Description                                                         |
| ------------------------------------- | ------- | ------------------------------------------------------------------- |
| `--confirmation-id` `CONFIRMATION_ID` | —       | Card id (default: latest pending).                                  |
| `--confirmed`                         | —       | Click the confirm button.                                           |
| `--declined`                          | —       | Click the decline button.                                           |
| `--choices` `CHOICES`                 | —       | Per-question answers: letter, number or free text, comma-separated. |
| `--choice` `CHOICE`                   | —       | Single question answer (repeatable; preserves commas).              |
| `--answer-text` `ANSWER_TEXT`         | —       | Raw answer text for a question card.                                |
| `--skip` `{round,all}`                | —       | Skip this round or all questions.                                   |

## `cxas arc answer` flags

| Flag                   | Default | Description                                            |
| ---------------------- | ------- | ------------------------------------------------------ |
| `--choices` `CHOICES`  | —       | Per-question answers, comma-separated.                 |
| `--choice` `CHOICE`    | —       | Single question answer (repeatable; preserves commas). |
| `--skip` `{round,all}` | —       | Skip this round or all questions.                      |

## `cxas arc sessions list` flags

| Flag                      | Default | Description                         |
| ------------------------- | ------- | ----------------------------------- |
| `--page-size` `PAGE_SIZE` | `20`    | Maximum sessions returned per page. |
| `--filter` `FILTER`       | —       | AIP-160 filter.                     |

## `cxas arc cancel` flags

| Flag                  | Default | Description                               |
| --------------------- | ------- | ----------------------------------------- |
| `--turn-id` `TURN_ID` | —       | Turn id to cancel (default: active turn). |
