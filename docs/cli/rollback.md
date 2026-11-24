# cxas rollback

`cxas rollback` flips a CES deployment (`Deployment.app_version`) back to a previously created app version. It resolves version display names (e.g. `v1.1.0`), respects `protected: true` profiles, prints `current -> target`, and warns if any other deployments on the same app still point to the broken version.

## Usage

```bash
cxas rollback --version VERSION [--deployment ID] [--profile PROFILE] [--app-name APP] [--allow-protected]
```

## Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--version VERSION` | Yes | — | Target version display name (e.g. `v1.1.0`), bare version ID, or full version resource name. |
| `--deployment ID` | No* | Profile `target.deployment_id` | Deployment ID (alias `--deployment-id`) to roll back. |
| `--profile PROFILE` | No | `$CXAS_PROFILE` | Target environment profile. |
| `--app-name APP` | No* | Profile `target` | Full CXAS app resource name (`projects/.../locations/.../apps/...`). |
| `--allow-protected` | No | `false` | Allow rolling back a `protected: true` profile from a local workstation (not needed when `CI=true`). |

*Required unless supplied by `--profile` / `CXAS_PROFILE`.

## Example

```bash
cxas rollback \
  --profile prod \
  --deployment voice-prod \
  --version v1.1.0 \
  --allow-protected
```

## Related Commands

- [`cxas lock`](lock.md) — Lock/unlock protected environments.
- [`cxas push`](push.md) — Push versioned releases with `--create-version` and `--fail-if-version-exists`.
