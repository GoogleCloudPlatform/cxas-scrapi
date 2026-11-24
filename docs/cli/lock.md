# cxas lock / unlock / lock-status

Manage native CES edit locks (`App.locked`) backed by CAS-safe `cxas.lock` TTL metadata in `App.metadata`. Locking prevents concurrent pushes and accidental Cloud Console edits on shared environments like `qa` and `prod`.

## Usage

```bash
cxas lock [APP] [--profile PROFILE] [--ttl MINUTES] [--reason TEXT] [--force] [--no-native]
cxas unlock [APP] [--profile PROFILE] [--force]
cxas lock-status [APP] [--profile PROFILE] [--json]
```

## Options

### `cxas lock`

| Option | Default | Description |
|--------|---------|-------------|
| `app` / `--app-name` | Profile target | App resource name or display name. |
| `--profile PROFILE` | `$CXAS_PROFILE` | Target environment profile. |
| `--ttl MINUTES` | `60` | Lock expiration window in minutes. |
| `--reason TEXT` | — | Optional reason recorded in `cxas.lock`. |
| `--force` | `false` | Steal an active lock held by another user. |
| `--no-native` | `false` | Record `cxas.lock` metadata without setting `App.locked`. |

### `cxas unlock`

| Option | Default | Description |
|--------|---------|-------------|
| `app` / `--app-name` | Profile target | App resource name or display name. |
| `--profile PROFILE` | `$CXAS_PROFILE` | Target environment profile. |
| `--force` | `false` | Force-release a lock held by another user. |

### `cxas lock-status`

| Option | Default | Description |
|--------|---------|-------------|
| `app` / `--app-name` | Profile target | App resource name or display name. |
| `--profile PROFILE` | `$CXAS_PROFILE` | Target environment profile. |
| `--json` | `false` | Emit JSON lock status (`lock`, `native_locked`, `stale`). |

## Atomic Locked Releases

Pair `cxas lock` with `cxas push --with-lock` in CI release workflows:

```bash
cxas push \
  --profile prod \
  --with-lock \
  --create-version \
  --version-name v1.2.0 \
  --fail-if-version-exists \
  --on-drift=fail
```

`--with-lock` atomically unlocks `App.locked` (while keeping `cxas.lock` held by the caller), runs the push, and re-locks `App.locked` inside a `finally` block so a failed push never leaves a protected environment unlocked.

## Related Commands

- [`cxas push`](push.md) — Push with `--with-lock`, `--on-drift`, and `--fail-if-version-exists`.
- [`cxas rollback`](rollback.md) — Roll back a deployment to a previous version.
