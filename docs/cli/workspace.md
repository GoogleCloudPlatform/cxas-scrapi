# cxas workspace

`cxas workspace` manages ephemeral GitOps CES workspaces tied to your feature branches — spinning up an isolated sandbox app from local source, emitting a gitignored `.cxas/profiles/ws.local.yml` profile so every CLI command targets it seamlessly, and sweeping expired sandboxes or orphaned `[CI] PR-*` apps automatically.

## Usage

```bash
cxas workspace up [--app-dir DIR] [--profile PROFILE] [--name NAME] [--team TEAM] [--ttl-days DAYS] [--env-file FILE] [--no-write-profile]
cxas workspace list [--mine] [--team TEAM] [--include-ci] [--json]
cxas workspace down [NAME] [--branch BRANCH] [--pr NUM] [--force] [--yes]
cxas workspace cleanup [--team TEAM] [--all-users] [--include-ci] [--ci-ttl-hours HOURS] [--dry-run] [--yes]
```

## Subcommands

### `cxas workspace up`

Idempotently creates or updates an ephemeral workspace app named `ws/<user>/<branch-slug>` (or `--name`), inherits `project_id`, `location`, `env_file`, `app_dir`, and `overrides` from `--profile` if provided, stamps `cxas.workspace` + `cxas.expiry` metadata, and writes `.cxas/profiles/ws.local.yml`.

Refuses shared branches (`main`, `master`, `dev`, `qa`, `uat`, `prod`) unless `--name` is passed explicitly.

| Option | Default | Description |
|--------|---------|-------------|
| `--app-dir DIR` | Profile `app_dir` or `.` | Local app directory to push. |
| `--profile PROFILE` | `$CXAS_PROFILE` | Base profile to inherit target project/location, `env_file`, and overrides from. |
| `--name NAME` | `ws/<user>/<branch>` | Explicit workspace display name. |
| `--branch BRANCH` | Current Git branch | Override detected Git branch name. |
| `--team TEAM` | — | Optional team tag recorded in `cxas.team`. |
| `--ttl-days DAYS` | `14` | Expiration TTL in days (recorded in `cxas.expiry`). |
| `--env-file FILE` | Profile `env_file` | Environment JSON file to inject. |
| `--no-write-profile` | `false` | Skip writing `.cxas/profiles/ws.local.yml`. |

### `cxas workspace list`

Lists workspace apps in the project/location.

| Option | Default | Description |
|--------|---------|-------------|
| `--mine` | `false` | Show only workspaces owned by the current user. |
| `--team TEAM` | — | Filter by `cxas.team`. |
| `--include-ci` | `false` | Also include automated `[CI] PR-*` apps. |
| `--json` | `false` | Emit JSON output. |

### `cxas workspace down` (alias: `delete`)

Deletes a workspace app. Refuses to delete non-workspace apps (`NotAWorkspaceError`) and refuses to delete another user's workspace unless `--force` is passed.

| Option | Default | Description |
|--------|---------|-------------|
| `name` | Current branch workspace | Workspace display name or resource name. |
| `--branch BRANCH` | Current Git branch | Delete the workspace for a specific branch. |
| `--pr NUM` | — | Delete the `[CI] PR-<num>` ephemeral app. |
| `--force` | `false` | Allow deleting a workspace owned by another user. |
| `--yes`, `-y` | `false` | Skip interactive confirmation. |

### `cxas workspace cleanup`

Deletes expired workspaces (and optionally orphaned `[CI] PR-*` apps older than `--ci-ttl-hours`).

| Option | Default | Description |
|--------|---------|-------------|
| `--team TEAM` | — | Restrict sweep to one team. |
| `--all-users` | `false` | Sweep expired workspaces for all owners (not just yourself). |
| `--include-ci` | `false` | Also sweep orphaned `[CI] PR-*` apps. |
| `--ci-ttl-hours HOURS` | `24` | Age threshold (in hours) for `[CI] PR-*` apps. |
| `--dry-run` | `false` | Print expired workspaces without deleting them. |
| `--yes`, `-y` | `false` | Skip interactive confirmation. |

## Examples

```bash
# Spin up a personal sandbox for your current feature branch
cxas workspace up --profile dev --team payments

# Iterate with zero stash tax using the generated ws.local profile
export CXAS_PROFILE=ws.local
cxas push
cxas run --tags smoke --wait

# Tear down when your PR merges
cxas workspace down --yes

# Nightly CI sweep of expired workspaces and orphaned PR apps
cxas workspace cleanup --all-users --include-ci --yes --project-id my-proj --location us
```

## Related Commands

- [`cxas profile`](profile.md) — Runtime profiles (`ws.local.yml`).
- [`cxas push`](push.md) — Push local edits to your workspace or environment.
