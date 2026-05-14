# `cxas trace`

`cxas trace` is the observability and debugging surface for past conversations.
It composes the Conversational Agents API, Cloud Logging, GCS, and Gemini into
a single, scriptable workflow so you can debug a flaky live conversation, audit
an eval failure, replay a conversation against the current agent, or flag a
platform bug — all from your terminal.

All `cxas trace` subcommands share a few flags:

| Flag | Required | Description |
|------|----------|-------------|
| `--app-name` | yes | Full CXAS App ID (`projects/.../locations/.../apps/...`). |
| `--app-dir` | no | Path to the pulled app directory. Defaults to `.`. Used to read `app.json` (audio bucket, Cloud Logging enablement, model version) and `environment.json`. |
| `--env-file` | no | Explicit path to an `environment.json` file (mirrors the existing `cxas push --env-file` flag). |
| `--environment` | no | Named environment, resolved to `<app-dir>/environment.<name>.json`. |
| `--config` | no | Path to a trace config YAML. Defaults to `./.cxas/trace.yaml`, then `~/.cxas/trace.yaml`, then built-in defaults. |

## Subcommands

| Subcommand | Purpose |
|-----------|---------|
| `cxas trace list` | List conversations filtered by time / source / channel. |
| `cxas trace get <id>` | Fetch a conversation and render a trace report (JSON / Markdown / text / HTML). |
| `cxas trace logs <id>` | Fetch Cloud Logging entries correlated to a conversation. |
| `cxas trace audio download <id>` | Download the GCS audio recording. |
| `cxas trace audio analyze <id>` | Run configured Gemini audio metrics over the recording. |
| `cxas trace triage <id>` | Run text-only Gemini triage prompts over the transcript. |
| `cxas trace replay <id>` | Replay user inputs against the current agent and diff. |
| `cxas trace stats` | Aggregate stats over recent conversations. |
| `cxas trace bundle <id>` | Zip transcript + logs + audio + report into a single archive. |
| `cxas trace bug-report <id>` | Flag a conversation as a platform bug; uploads bundle to a configured GCS bucket. |
| `cxas trace open <id>` | Print (and on macOS, open) the CES Console deep link. |

## Examples

List the 10 most recent audio conversations from the last day:

```
cxas trace list \
  --app-name projects/p/locations/l/apps/a \
  --time-filter 24h --channel AUDIO --limit 10
```

Build a Markdown trace report with merged Cloud Logs, downloaded audio, and
Gemini audio + transcript analysis — written to a file:

```
cxas trace get conv-id-1 \
  --app-name projects/p/locations/l/apps/a \
  --format md --with-logs --with-audio --with-analysis --with-triage \
  --out trace.md
```

Compare a deployed conversation against the current agent:

```
cxas trace replay conv-id-1 --app-name projects/p/locations/l/apps/a --diff
```

Generate a 7-day stats report grouped by source, written as Markdown:

```
cxas trace stats --time-filter 7d --source LIVE \
  --app-name projects/p/locations/l/apps/a --out stats.md
```

Flag a conversation as a platform bug with reason and severity:

```
cxas trace bug-report conv-id-1 \
  --app-name projects/p/locations/l/apps/a \
  --reason "agent hallucinated the refund amount" --severity high
```

## Configuration: `./.cxas/trace.yaml`

`cxas trace` is fully configurable. Drop a `trace.yaml` next to your agent in
`./.cxas/trace.yaml` (or globally at `~/.cxas/trace.yaml`) to override prompts,
log filters, audio behavior, and the bug-report destination. Every field has
a sensible default, so the file is optional.

```yaml
audio:
  bucket_override: null            # leave null to use app.json's gcsBucket
  uri_pattern: "{bucket}/{conversation_id}.wav"
  download_dir: ./.cxas/audio
  mime_type: audio/wav

cloud_logging:
  default_level: WARNING
  time_padding_seconds: 30
  filter_template: |
    severity >= "{level}"
    AND timestamp >= "{start_time}" AND timestamp <= "{end_time}"
    AND (jsonPayload.conversation_id="{conversation_id}"
         OR labels.conversation_id="{conversation_id}")

gemini:
  model: gemini-2.5-flash
  # Audio analyses come from `cxas_scrapi.utils.audio_analysis.ANALYSIS_REGISTRY`
  # (5 built-ins: agent_voice_consistency, no_long_pauses,
  # agent_having_trouble, agent_looping, agent_cutoff). Each declares which
  # files it needs (e.g. `agent-turn-*.wav` vs `full-session.wav`) and a
  # default prompt. Override a prompt per-project by listing the metric here:
  audio_metrics: {}
  # Example override:
  #   agent_cutoff:
  #     prompt: "Custom cutoff prompt for our agent..."
  triage_metrics:
    hallucination:        { prompt: "..." }
    off_topic:            { prompt: "..." }
    failed_understanding: { prompt: "..." }

ui:
  ces_console_base: https://ces.cloud.google.com
  ccai_insights_base: https://ccai.cloud.google.com/insights

bug_report:
  bucket: gs://cxas-platform-bugs
  path_template: "{model_version}/{date}/{user}/{severity}/{conversation_id}/"
  include: [transcript, logs, audio, gemini_analysis, environment]
```

## App-side discovery

`cxas trace` reads the same `app.json` and `environment.json` files that `cxas
pull` writes — no extra setup required. From `app.loggingSettings`:

- `audioRecordingConfig.gcsBucket` → audio download source
- `cloudLoggingSettings.enableCloudLogging` → gates `--with-logs`
- `bigqueryExportSettings` → surfaced in metadata (informational)

`$env_var` placeholders are resolved against the chosen `environment.json` —
the same convention used by `cxas push`.
