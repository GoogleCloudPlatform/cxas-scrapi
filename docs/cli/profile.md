# cxas profile

`cxas profile` manages runtime environment profiles (`.cxas/profiles/*.yml`) so developers and CI pipelines can switch between personal sandboxes, `dev`, `qa`, and `prod` without modifying tracked files in Git.

## Usage

```bash
cxas profile list [--json]
cxas profile show [NAME] [--diff] [--json]
cxas profile validate [NAME] [--all] [--json]
```

## Profile Discovery Order

Every profile-aware command (`push`, `pull`, `delete`, `ci-test`, `run`, `evals report`, `llm-lint`, `deployments promote`) and foundry eval script resolves profiles in this order:

1. **Explicit file path** passed to `--profile` (ends in `.yml`/`.yaml`/`.json`, contains a path separator, or exists on disk).
2. **Named profile** at `<project_or_repo_root>/.cxas/profiles/<name>.{yml,yaml,json}` — respecting `GECX_PROJECT` and `.active-project` so sibling project profiles never leak into each other.
3. **`CXAS_PROFILE` environment variable** when `--profile` is omitted.

## Profile Schema

```yaml
# .cxas/profiles/plesciuc.local.yml (gitignored)
extends: dev                              # optional single inheritance (cycle-checked)
protected: false                          # set true on qa.yml / prod.yml
description: Personal sandbox for v2 testing
target:
  project_id: my-sandbox-dev              # canonical; projected into gecx-config.json
  location: europe-west2
  app_id: digital-assistant-v2            # or display_name: "DA - Plesciuc v2"
  deployment_id: voice-dev                # optional
app_dir: src/agent                        # optional (relative to project/repo root)
env_file: ~/.secrets/db-dev.env.json      # ~ and ${ENV_VAR} expanded
overrides:
  gecx-config.json:
    model: gemini-2.5-pro
    modality: text
    gcs_bucket: null                      # null deletes a key
  app.json:                               # applies to app.json OR app.yaml
    displayName: "DA - Plesciuc v2"
    modelSettings:
      model: "gemini-2.5-pro"
```

## Guardrails

- **Protected environments (`protected: true`):** Local mutating commands (`push`, `delete`, `deployments promote`) refuse to run against a `protected: true` profile unless `--allow-protected` is passed or `CI=true` is set. Every mutation prints a 1-line banner `Profile: <name> -> <project>/<location>/<app>` first.
- **No silent env-var target hijacking:** Passing `--to` explicitly on the CLI while a profile is active only via `CXAS_PROFILE` fails fast so personal `app.json` overrides and `env_file` are never silently injected onto another app.
- **Canonical `target`:** `target.{project_id, location, app_id}` is projected into `gecx-config.json` as `{gcp_project_id, location, deployed_app_id}`. Duplicating those keys inside `overrides.gecx-config.json` is rejected.
- **Strict override whitelist & secret warnings:** Only `app.json`, `app.yaml`, and `gecx-config.json` are permitted under `overrides`. Secret-like keys (`api_key`, `token`, `secret`, `password`) emit warnings recommending `env_file` or Secret Manager.
- **Safe `cxas pull --profile`:** Pull preserves tracked base values for keys overridden by `overrides.app.json`/`app.yaml` unless `--accept-profile-overrides` is passed.

## Subcommands

### `cxas profile list`

Lists all profiles discovered in `.cxas/profiles/`, marking the active `CXAS_PROFILE` (if any) and showing `[protected]` and `extends` inheritance.

| Option | Description |
|--------|-------------|
| `--json` | Emit machine-readable JSON output. |

### `cxas profile show [NAME]`

Prints the fully resolved profile (after `extends` merging), source chain, target, paths, and override keys. Defaults to `$CXAS_PROFILE` if `NAME` is omitted.

| Option | Description |
|--------|-------------|
| `--diff` | Display unified diffs against local `app.json`/`app.yaml` and `gecx-config.json`. |
| `--json` | Emit resolved profile as JSON. |

### `cxas profile validate [NAME]`

Validates profile syntax, `extends` inheritance cycles, override file whitelist, duplicate target keys, and post-merge required app config keys.

| Option | Description |
|--------|-------------|
| `--all` | Validate every profile in `.cxas/profiles/`. |
| `--json` | Emit validation results as JSON. |

## Examples

**Inspect all profiles and preview a diff before pushing:**

```bash
cxas profile list
cxas profile show plesciuc.local --diff
cxas push --profile plesciuc.local --dry-run
```

**Use `CXAS_PROFILE` for a zero-stash developer session:**

```bash
export CXAS_PROFILE=plesciuc.local
cxas push
cxas run --tags smoke --wait
```

**Validate all profiles in CI:**

```bash
cxas profile validate --all
```

## Related Commands

- [`cxas push`](push.md) — Push with `--profile`, `--dry-run`, and `--allow-protected`.
- [`cxas pull`](pull.md) — Pull safely with `--profile` (preserves overridden base keys).
