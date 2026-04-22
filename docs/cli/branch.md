# cxas branch

`cxas branch` creates a full copy of an existing app under a new display name — think of it as `git branch` for your CX Agent Studio app, perfect for experimenting without touching the source.

## Usage

```
cxas branch <source> --new-name NAME --project-id PROJECT --location LOCATION
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `source` | Yes | The app to clone. Accepts either a full resource name (`projects/{project}/locations/{location}/apps/{app}`) or a display name. When using a display name, provide `--project-id` and `--location`. |

## Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--new-name NAME` | Yes | — | Display name for the new (branched) app. |
| `--project-id PROJECT` | Yes | — | GCP project ID. |
| `--location LOCATION` | Yes | — | GCP location (e.g., `global`, `us-central1`). |

## How It Works

`cxas branch` is a composite operation. Under the hood it:

1. **Exports** the source app as a ZIP archive (equivalent to `cxas pull`).
2. **Creates** a new app in the same project and location.
3. **Imports** the exported content into the new app (equivalent to `cxas push`).

The original app is never modified. Because the export and import happen in memory, no files are written to your local disk.

## Examples

**Branch an app by display name:**

```bash
cxas branch "My Support Agent" \
  --new-name "My Support Agent (Experiment)" \
  --project-id my-gcp-project \
  --location us-central1
```

**Branch using the full resource name:**

```bash
cxas branch projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --new-name "Support Agent v2" \
  --project-id my-gcp-project \
  --location us-central1
```

**Create a staging copy of a production app:**

```bash
cxas branch "Support Agent — Production" \
  --new-name "Support Agent — Staging" \
  --project-id my-gcp-project \
  --location us-central1
```

## Related Commands

- [`cxas pull`](pull.md) — Download an app locally instead of branching in the cloud.
- [`cxas push`](push.md) — Push local changes into an existing app.
- [`cxas create`](create.md) — Create a new empty app.
- [`cxas delete`](delete.md) — Remove a branch app when you're done with it.
