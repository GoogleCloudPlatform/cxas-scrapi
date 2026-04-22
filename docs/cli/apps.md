# cxas apps

`cxas apps` gives you quick visibility into what's deployed in your project — use `list` to see all your apps at a glance, or `get` to dig into the details of a specific one.

## Usage

```
cxas apps list --project-id PROJECT --location LOCATION

cxas apps get <app> [--project-id PROJECT] [--location LOCATION]
```

---

## cxas apps list

Lists every app in the given project and location, formatted as a table showing display names and resource names.

### Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--project-id PROJECT` | Yes | — | GCP project ID to list apps from. |
| `--location LOCATION` | Yes | — | GCP location (e.g., `global`, `us-central1`). |

### Example

```bash
cxas apps list \
  --project-id my-gcp-project \
  --location us-central1
```

Output:

```
Apps:
 Display Name                     Name
 My Support Agent                 projects/my-gcp-project/locations/us-central1/apps/abc123
 Billing Assistant                projects/my-gcp-project/locations/us-central1/apps/def456
 [CI] PR-42 Test Agent            projects/my-gcp-project/locations/us-central1/apps/ghi789
```

---

## cxas apps get

Retrieves and prints the full details of a single app, including its resource name, display name, description, and timestamps.

### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `app` | Yes | Full resource name or display name of the app to look up. |

### Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--project-id PROJECT` | No* | — | GCP project ID. Required when `app` is a display name. |
| `--location LOCATION` | No* | — | GCP location. Required when `app` is a display name. |

*Required when `app` is specified as a display name.

### Examples

**Get by display name:**

```bash
cxas apps get "My Support Agent" \
  --project-id my-gcp-project \
  --location us-central1
```

**Get by full resource name:**

```bash
cxas apps get projects/my-gcp-project/locations/us-central1/apps/abc123
```

Output:

```
App Details:
Name:         projects/my-gcp-project/locations/us-central1/apps/abc123
Display Name: My Support Agent
Description:  Handles customer support queries.
Create Time:  2026-01-15T10:30:00Z
Update Time:  2026-04-01T08:45:00Z
```

## Related Commands

- [`cxas pull`](pull.md) — Download an app once you know its resource name.
- [`cxas delete`](delete.md) — Remove an app from the list.
- [`cxas branch`](branch.md) — Clone an app into a new one.
