# cxas create

`cxas create` provisions a brand-new, empty app in CX Agent Studio — handy when you're starting a project from scratch or need a clean target before pushing content into it.

## Usage

```
cxas create <name> [--description TEXT] [--app-name ID]
            --project-id PROJECT --location LOCATION
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `name` | Yes | The display name for the new app (e.g., `"My Support Agent"`). This is what you'll see in the CX Agent Studio Console. |

## Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--description TEXT` | No | — | A short human-readable description of what the app does. |
| `--app-name ID` | No | — | Optional specific app ID to use as the resource identifier. If omitted, the platform generates one automatically. |
| `--project-id PROJECT` | Yes | — | GCP project ID where the app will be created. |
| `--location LOCATION` | Yes | — | GCP location (e.g., `global`, `us-central1`). |

## Examples

**Create a simple new app:**

```bash
cxas create "My Support Agent" \
  --project-id my-gcp-project \
  --location us-central1
```

**Create an app with a description:**

```bash
cxas create "Billing Assistant" \
  --description "Handles billing queries and account lookups." \
  --project-id my-gcp-project \
  --location us-central1
```

**Create an app with a custom app ID:**

```bash
cxas create "Billing Assistant" \
  --app-name billing-assistant-v2 \
  --project-id my-gcp-project \
  --location us-central1
```

After creating the app, you'll see the full resource name printed to the console. Copy it — you'll need it for `cxas push` and other commands that target a specific app.

## Related Commands

- [`cxas push`](push.md) — Upload agent content into the newly created app.
- [`cxas branch`](branch.md) — Create a copy of an existing app (a higher-level alternative to create + push).
- [`cxas delete`](delete.md) — Remove an app when it's no longer needed.
