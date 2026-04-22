# cxas pull

Use `cxas pull` to download a CX Agent Studio app to your local machine — it's your starting point any time you want to edit an app's configuration, instructions, or tools in code.

## Usage

```
cxas pull <app> [--target-dir DIR] [--project-id PROJECT] [--location LOCATION]
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `app` | Yes | The app to pull. Accepts either a full resource name (`projects/{project}/locations/{location}/apps/{app}`) or a display name (e.g., `"My Support Agent"`). When using a display name you must also provide `--project-id` and `--location`. |

## Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--target-dir DIR` | No | `.` (current directory) | The local directory to extract the app into. The directory is created if it does not exist. |
| `--project-id PROJECT` | No* | — | GCP project ID. Required when `app` is a display name rather than a full resource name. |
| `--location LOCATION` | No* | — | GCP location (e.g., `global`, `us-central1`). Required when `app` is a display name. |

*Required when `app` is specified as a display name.

## What Gets Downloaded

The pull command exports the app as a ZIP archive and extracts it locally. The resulting directory layout looks like this:

```
my-agent/
├── app.yaml
├── global_instruction.txt
├── environment.json
├── agents/
│   └── pilot/
│       ├── agent.yaml
│       └── instructions.txt
├── tools/
├── guardrails/
├── toolsets/
├── evaluations/
└── evaluationExpectations/
```

This structure is what `cxas push` expects when you upload changes back.

## Examples

**Pull an app by display name into a folder called `my-agent`:**

```bash
cxas pull "My Support Agent" \
  --target-dir ./my-agent \
  --project-id my-gcp-project \
  --location us-central1
```

**Pull using the full resource name (no `--project-id` or `--location` needed):**

```bash
cxas pull projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --target-dir ./my-agent
```

**Pull into the current directory:**

```bash
cxas pull projects/my-gcp-project/locations/global/apps/abc123
```

## Related Commands

- [`cxas push`](push.md) — Upload local changes back to CX Agent Studio.
- [`cxas branch`](branch.md) — Clone an app into a new one without touching your local filesystem.
- [`cxas apps`](apps.md) — List apps to find the resource name you need.
