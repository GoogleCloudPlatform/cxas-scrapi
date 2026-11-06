# cxas push

`cxas push` uploads a local agent directory to CX Agent Studio, either updating an existing app or creating a fresh one — it's how your local edits make it into the platform.

## Usage

```
cxas push [--app-dir DIR] [--to TARGET] [--env-file FILE]
          [--app-name APP] [--display-name NAME]
          [--create-version] [--version-name NAME] [--version-description DESC]
          [--overwrite] [--sync-shared]
          [--project-id PROJECT] [--location LOCATION]
```

## Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--app-dir DIR` | No | `.` (current directory) | Path to the local agent directory to upload. |
| `--to TARGET` | No | — | Target app to overwrite. Accepts a full resource name or display name. When provided, the existing app is updated in place. |
| `--env-file FILE` | No | — | Path to a JSON environment file to inject as `environment.json` before uploading. Useful for supplying environment-specific secrets or variables without committing them. |
| `--app-name APP` | No | — | Explicit app resource name to push to (v1beta API). Use this when you want to overwrite a specific app and already know its full ID. |
| `--display-name NAME` | No | `"Pushed Agent"` | Display name for the new app when no target is specified and a new app will be created. |
| `--create-version` | No | `false` | Automatically create a version snapshot after a successful push. |
| `--version-name NAME` | No | `import-TIMESTAMP` | Custom display name for the created version snapshot (used with `--create-version`). |
| `--version-description DESC` | No | — | Description text for the created version snapshot (used with `--create-version`). |
| `--overwrite` | No | `false` | Overwrite existing resources with imported data. Unmatched existing resources will be deleted. |
| `--sync-shared` | No | `false` | Materialize shared callbacks and instruction fragments via `cxas sync-shared` before packaging and uploading. |
| `--project-id PROJECT` | No | — | GCP project ID. Required for a display-name target or when creating a new app. Inferred from a full target resource name. |
| `--location LOCATION` | No | — | GCP location (e.g., `global`, `us-central1`). Required for a display-name target or when creating a new app. Inferred from a full target resource name. |

## What Gets Uploaded

The CLI bundles the following top-level items from `--app-dir` into a ZIP archive before uploading:

- `app.yaml` / `app.json`
- `global_instruction.txt`
- `environment.json` (or the file you pass via `--env-file`)
- `agents/`, `tools/`, `examples/`, `guardrails/`, `toolsets/`
- `evaluations/`, `evaluationDatasets/`, `evaluationExpectations/`
- `.github/workflows/`

Any other top-level directories or files in `--app-dir` (aside from standard repository/tooling metadata such as `.git`, `.gitignore`, `README.md`, `cxaslint.yaml`, `Dockerfile`, and `cxas_types.pyi`) are **not** part of the CXAS export format and will be excluded from the uploaded archive with an explicit warning:

```text
⚠️  Warning: The following top-level entries in ./my-agent are not part of the CXAS export format and will not be uploaded by cxas push: shared, custom_scripts
```

## Sharing Callbacks & Instruction Fragments (`cxas sync-shared`)

Because the CES runtime executes each agent callback in an isolated sandbox and `cxas push` only uploads standard export directories, shared Python modules in top-level folders (such as `shared/`) cannot be imported directly at runtime. Use `cxas sync-shared` (or `cxas push --sync-shared`) to deterministically materialize shared code and prompt fragments into your agents before pushing:

1. **Inline Callback Header Directive (`# @cxas-shared: <rel_path>`)**:
   Place `# @cxas-shared: shared/callbacks/auth_guard.py` within the first 5 lines of any `agents/<agent>/**/*.py` callback file. Running `cxas sync-shared` copies the canonical file from `<app_dir>/shared/callbacks/auth_guard.py` into the target callback while preserving the `# @cxas-shared:` header.
2. **Instruction Fragment Includes (`<!-- @cxas-include: <rel_path> -->`)**:
   Wrap shared prompt sections in `instruction.txt` or `global_instruction.txt` with HTML comment markers:
   ```markdown
   <!-- @cxas-include: shared/instructions/tone.txt -->
   (Synchronized automatically by cxas sync-shared)
   <!-- @cxas-end-include -->
   ```
   Because CES preserves HTML comments in `instruction.txt` across exports, `cxas pull` followed by `cxas sync-shared` is round-trip idempotent.
3. **Manifest Mapping (`shared/manifest.yaml` or `cxas_shared.yaml`)**:
   Optionally declare canonical sources and target callback paths in `shared/manifest.yaml`:
   ```yaml
   mappings:
     - source: shared/callbacks/common_before_model.py
       targets:
         - agents/agent_a/before_model_callbacks/cb/python_code.py
         - agents/agent_b/before_model_callbacks/cb/python_code.py
   ```

Run `cxas sync-shared --app-dir ./my-agent --check` in CI to fail with exit code `1` if any target has drifted from its canonical source.

## Examples

**Push the current directory to an existing app by display name:**

```bash
cxas push \
  --to "My Support Agent" \
  --project-id my-gcp-project \
  --location us-central1
```

**Push a specific directory and create a new app with a custom display name:**

```bash
cxas push \
  --app-dir ./my-agent \
  --display-name "My Support Agent (Staging)" \
  --project-id my-gcp-project \
  --location us-central1
```

**Push current directory and create a version snapshot with a custom name:**

```bash
cxas push \
  --to "My Support Agent" \
  --create-version \
  --version-name "v1.2.0" \
  --version-description "Release candidate v1.2.0" \
  --project-id my-gcp-project \
  --location us-central1
```

**Push using a separate environment file (keeps secrets out of source control):**

```bash
cxas push \
  --app-dir ./my-agent \
  --to projects/my-gcp-project/locations/us-central1/apps/abc123 \
  --env-file ./secrets/env-prod.json \
  --project-id my-gcp-project \
  --location us-central1
```

## Related Commands

- [`cxas pull`](pull.md) — Download an app to a local directory first.
- [`cxas create`](create.md) — Create an empty app shell before pushing.
- [`cxas ci-test`](ci-test.md) — Run the full push + test lifecycle in CI.
