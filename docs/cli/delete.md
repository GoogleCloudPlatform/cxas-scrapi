# cxas delete

`cxas delete` permanently removes an app from CX Agent Studio — use it to clean up temporary CI apps, old branches, or anything you no longer need.

!!! warning "Deletion is permanent"
    There is no undo. Make sure you've pulled a copy first if you might want the content later.

## Usage

```
cxas delete [--app-name APP] [--display-name NAME]
            [--project-id PROJECT] [--location LOCATION]
            [--force]
```

## Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--app-name APP` | No* | — | Full resource name of the app to delete (e.g., `projects/{project}/locations/{location}/apps/{app}`). Required if `--display-name` is not provided. |
| `--display-name NAME` | No* | — | Display name of the app to delete. The CLI looks it up and resolves the resource name automatically. Required if `--app-name` is not provided. |
| `--project-id PROJECT` | No* | — | GCP project ID. Required when using `--display-name`. |
| `--location LOCATION` | No* | — | GCP location. Required when using `--display-name`. |
| `--force` | No | `false` | Force deletion even if the app still has child resources (e.g., evaluations, agents). Without this flag the platform may reject the request if the app is not empty. |

*You must provide either `--app-name` OR (`--display-name` + `--project-id` + `--location`).

## Examples

**Delete by display name:**

```bash
cxas delete \
  --display-name "My Support Agent" \
  --project-id my-gcp-project \
  --location us-central1
```

**Delete by full resource name:**

```bash
cxas delete \
  --app-name projects/my-gcp-project/locations/us-central1/apps/abc123
```

**Force-delete an app that still has resources inside it:**

```bash
cxas delete \
  --app-name projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --force
```

**Clean up a temporary CI app by its deterministic display name:**

```bash
cxas delete \
  --display-name "[CI] PR-123 Test Agent" \
  --project-id my-gcp-project \
  --location us-central1
```

## Related Commands

- [`cxas apps`](apps.md) — List apps to find the one you want to delete.
- [`cxas create`](create.md) — Create a new app.
- [`cxas ci-test`](ci-test.md) — The CI test lifecycle automatically manages temporary app creation and cleanup.
