# Multi-Brand CXAS Starter Project

This project was scaffolded with `cxas init --template multibrand`.

## Layout

- `core/`: Shared CES agent application template (`app.json`, `global_instruction.txt`, `agents/`, `tools/`).
- `manifests/profiles/<profile_id>/`: Per-brand `identity.yaml`, `channel_policy.yaml`, and optional `overrides.yaml`.
- `manifests/envs/<env>.yaml`: Environment URL templates and `environment.json` overrides (`dev`, `prod`).
- `manifests/locales/content_blocks.yaml`: Regulated verbatim copy per `(brand, country, language)`.

## Quick Start

1. Build all brand and channel bundles with SHA-256 lockfiles:

   ```bash
   cxas build --core-dir core --manifests-dir manifests --env dev --out dist --all --write-locks
   ```

2. Lint a built bundle:

   ```bash
   cxas lint --app-dir dist/brand_alpha/digital_app
   ```

3. Push with pre-push 3-way drift protection and optional deployment sync:

   ```bash
   cxas push --app-dir dist/brand_alpha/digital_app --to <ces-app> --check-drift --sync-deployments
   ```

4. Back-port approved Console edits back into brand overrides or shared `core/`:

   ```bash
   cxas backport --core-dir core --manifests-dir manifests --live-dir live_export --dist dist --target brand --dry-run
   cxas backport --core-dir core --manifests-dir manifests --live-dir live_export --dist dist --target core --dry-run
   ```
