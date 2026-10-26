# `cxas redaction`

Verify what logging redaction (the DLP templates in
`loggingSettings.redactionConfig`) removes from and keeps in persisted
conversations, against a declarative `redaction_policy.yaml`.

See the [Redaction Checks guide](../guides/evaluation/redaction-checks.md)
for the policy format and concepts.

## Subcommands

| Subcommand | Purpose |
|-----------|---------|
| `cxas redaction validate [POLICY]` | Validate a policy file and list its categories. |
| `cxas redaction replay` | Replay raw evaluation/conversation transcripts through the app's DLP templates (offline, no agent calls). |
| `cxas redaction live` | Run scripted sessions and compare live turns with the persisted, redacted conversation. |
| `cxas redaction variants VALUE` | Print conversational renderings of a digit value. |

`--policy` defaults to the nearest `redaction_policy.yaml` (current directory
and its parents).

## `cxas redaction replay`

| Flag | Description |
|------|-------------|
| `--app-name` | App resource name. Required to read evaluation runs, fetch conversations, or resolve the templates from the app. |
| `--evaluation-run` | Evaluation run ID or resource name; every result is replayed. |
| `--conversation` | One or more conversation IDs or resource names. |
| `--file` | One or more exported conversation JSON files. |
| `--mode` | `platform` (default), `conversation` or `key_aware`. |
| `--inspect-template` / `--deidentify-template` | Override the templates (for example to test a candidate before deploying it). |
| `--require` | Categories that must appear at least once. |

```bash
cxas redaction replay \
  --app-name projects/my-project/locations/us/apps/my-app \
  --evaluation-run 8e0fd044-fcc7-449d-9ae7-1c97fa456976 \
  --mode platform --output-html redaction.html
```

## `cxas redaction live`

| Flag | Description |
|------|-------------|
| `--app-name` | App resource name (required). |
| `--script` | YAML with `conversations: [{name, turns, variables, redaction_expectations}]` (required). |
| `--name` | Only run these conversation names. |
| `--modality` | `text` (default) or `audio`. |
| `--deployment-id` | Run sessions against a deployment. |
| `--persist-timeout` | Seconds to wait for the conversation to be persisted (default 90). |

```bash
cxas redaction live \
  --app-name projects/my-project/locations/us/apps/my-app \
  --script redaction_script.yaml --modality audio
```

## Output flags (`replay`, `live`)

| Flag | Description |
|------|-------------|
| `--output-json` | Write the full report as JSON. |
| `--output-html` | Write a self-contained HTML report. |
| `--max-findings` | Findings printed in the terminal (default 50). |
| `--no-fail` | Exit `0` even when thresholds are exceeded. |

Both commands exit `1` when the policy thresholds are exceeded.
