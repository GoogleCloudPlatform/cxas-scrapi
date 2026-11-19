# CXAS SCRAPI Build, Lint Gate, Governed Lock, and Drift Gate

`cxas build --core-dir ... --manifests-dir ...` is the single build path: one shared, templated `core/` CES app plus
Pydantic-validated YAML manifests (brand identity, channel policy, environment, locale content blocks) compile into
one CES app per `brand x channel`. Compiled apps are checked by `cxas lint` (including the polymorphic rules
P001-P005), locked to a canonical SHA-256 lockfile, and promoted with `cxas push` behind an optional lint gate,
3-way drift gate, and a governed lock applied after import.

## 1. Core + Manifests Builds (`cxas build`)

### Manifest Layout

```text
manifests/
  brands/
    brand_alpha/
      identity.yaml
      channel_policy.yaml
    brand_beta/
      identity.yaml
      channel_policy.yaml
  envs/
    prod.yaml
  locales/
    content_blocks.yaml
```

`core/` files may use `@@brand.*@@`, `@@routing.*@@`, `@@env.*@@`, `@@build.channel@@` / `@@build.environment@@`
placeholders and `{@startChannel_X}` / `{@startBrand_X}` / `{@startAgentEnabled_X}` / `{@startToolEnabled_X}`
conditional tags. Placeholders are rendered in `*.json`, `*.txt`, `*.py`, and `*.yaml` (e.g. OpenAPI schemas).

### Commands

```bash
# Build all brand/channel pairs (+ <app>.lock.json lockfiles)
cxas build --core-dir core/ --manifests-dir manifests/ --env prod --out dist/ --all --write-locks

# Build a single brand/channel app
cxas build \
  --core-dir core/ \
  --manifests-dir manifests/ \
  --brand brand_alpha \
  --channel WEB \
  --env prod \
  --out dist/brand_alpha/web

# Package an app bundle into a deterministic ZIP archive
cxas package --app-dir dist/brand_alpha/web --zip web.zip
```

Every compiled app also gets `deployments/<channel>.yaml` (`brandId`, `channel`, `environment`, ...). It is build
metadata (never part of the pushed archive) and gives `cxas lint` its brand context.

#### `brands/brand_alpha/identity.yaml`
```yaml
brand_id: brand_alpha
display_name: Cymbal Alpha
assistant_title: Virtual Agent
lexicon:
  ai_disclosure: "Hello! You are chatting with Cymbal Alpha's AI assistant."
forbidden_tokens: [BetaTAN]   # P001: never in brand_alpha's compiled app
tts_voices:
  en:
    voice: en-US-Standard-A
    speaking_rate: 1.0
```

#### `brands/brand_alpha/channel_policy.yaml`
```yaml
brand_id: brand_alpha
supported_countries: ["US", "CA"]
channels:
  WEB:
    supported_modalities: ['TEXT']
  VOICE_TELEPHONY:
    entry_agent: custom_entry
    disabled_agents: [pruned_agent]
    disabled_tools: [secret_tool]
    supported_modalities: ['AUDIO']
```

#### `envs/prod.yaml`
```yaml
environment: prod
templates:
  gcp_project: cymbal-prod-{brand_slug}-{env}
overrides:
  some_key: prod_val
locked: true            # governed lock: compiled app.json declares locked: true
models:                 # pins only the slots CES applies
  agent: gemini-3.5-flash             # app.json + every agent modelSettings.model
  guardrail: gemini-3.5-flash-lite    # llmPolicy / llmPromptSecurity.customPolicy judge model
  data_store: gemini-3.5-flash-lite   # data-store tool rewriter + summarizer models
```

#### `locales/content_blocks.yaml`
```yaml
blocks:
  brand_alpha:
    US:
      en:
        greeting: "Hello from Cymbal Alpha!"
```

---

## 2. Polymorphic Lint Rules (`cxas lint`)

```bash
cxas lint --app-dir dist/brand_alpha/web --manifests-dir manifests/
```

| ID | Severity | Check |
| :-- | :-- | :-- |
| `P001` | error | No sister brand `display_name` and no own `forbidden_tokens` anywhere in the compiled app. |
| `P002` | warning | An agent references at most `max_tools` tools (default 20, `cxaslint.yaml` `options.P002.max_tools`). |
| `P003` | error | No unresolved `@@...@@` placeholders or `{@start/end...}` build tags. |
| `P004` | error | Agents/tools disabled for the brand's channel are absent and unreferenced (dirs, `tools`, `childAgents`, `{@AGENT/TOOL: x}`). |
| `P005` | error | Explicit agent / LLM guardrail / data-store rewriter+summarizer models are not an alias (`default`, `latest`, `auto`) or a deprecated `gemini-1.x/2.0/2.5` model. |

P001/P004 need brand context: brand and channel come from the bundle's `deployments/*.yaml` (or `--brand` /
`--channel`), manifests from `--manifests-dir` (or `<app>/manifests`). Without context they are skipped.
Instruction size is covered by `I007` (`instruction-too-long`) and missing agent/tool references by `S002` / `S004`
(CES system tools `end_session`, `customize_response` need no `tools/` directory).

---

## 3. SHA-256 Lockfiles & 3-Way CES-to-Git Drift (`cxas lock`, `cxas drift`)

`cxas lock` and `cxas drift` canonicalize every logical resource (`app`, `agents/<id>`, `tools/<id>`,
`guardrails/<id>`, `toolsets/<id>`) so a CES round trip reads as `CLEAN`: volatile fields (`name`, `uuid`, `etag`,
`createTime`, `updateTime`), proto3 defaults CES omits (`false`, `null`, `[]`, `{}`), alphabetically re-sorted
reference lists, re-ordered `variableDeclarations`, OpenAPI `servers[].url` -> `$env_var` (+ `environment.json`), the
push-time app `displayName`, the server-default `loggingSettings`, and the app `locked` state (never exported).

- **`CLEAN`**: `live` matches `ours` (or matches `base` with no conflicting live change).
- **`DRIFT`**: `ours == base` and `live != base` (out-of-band Console hotfix detected).
- **`ORPHAN`**: resource exists in `live` only.
- **`CONFLICT`**: both `ours` and `live` diverged from `base` (`<<<<<<< live ... ======= ... >>>>>>> ours` `.diff`).

```bash
cxas lock --bundle dist/brand_alpha/web --lockfile dist/brand_alpha/web.lock.json
cxas drift --app brand_alpha/web --dist dist/brand_alpha/web --live exports/live/ \
  --lockfile dist/brand_alpha/web.lock.json --conflicts-dir dist/conflicts/
cxas drift ... --accept-live agents/human_interaction_agent   # adopt a Console hotfix into ours + lockfile
cxas drift ... --force-ours agents/credit_cards_agent         # keep local Git state
```

---

## 4. Pre-Push Gates and Governed Lock (`cxas push`)

```bash
cxas push \
  --app-dir dist/brand_alpha/web \
  --to projects/cymbal-prod/locations/us/apps/brand-alpha-web \
  --overwrite \
  --lint --manifests-dir manifests/ \
  --check-drift --live-dir exports/live/ --lockfile dist/brand_alpha/web.lock.json \
  --create-version --version-name release-42
```

- `--lint` runs `cxas lint` first and refuses the push on any lint error (opt-in: existing apps with pre-existing
  lint errors keep pushing unchanged).
- `--check-drift` refuses on unresolved `DRIFT` / `ORPHAN` / `CONFLICT`; without `--live-dir` the live app is
  exported to a temporary directory first.
- Governed lock: CES rejects importing an app whose `app.json` has `locked: true`
  (`FAILED_PRECONDITION ... is locked`). When the bundle declares `locked: true` (env manifest `locked: true`),
  push imports a copy with `locked=false`, creates the version, then calls `UpdateApp(locked=true)`. An already-locked
  target is unlocked first and re-locked afterwards; a failed import restores the lock.

---

## Python Bundler

The `cxas push`, `cxas package`, and `cxas build` commands automatically bundle shared Python code from a `python_lib/` directory into callbacks (`agents/*/*_callbacks/*/python_code.py`) and Python tools (`tools/*/python_function/python_code.py`).

This is achieved via AST-driven topological tree-shaking within `# === BEGIN CXAS_SHARED_BUNDLE (sha256:<digest>) ===` / `# === END CXAS_SHARED_BUNDLE ===` markers. The bundler parses module imports, computes the exact transitive dependency closure per entrypoint, and injects a deterministic `64 KiB`-budgeted 3-line marker.

On `cxas pull`, these marker blocks are stripped cleanly (`strip_shared_bundles`). If edits were made in the Cloud Console *outside* the marker block, they are preserved. If the marker block itself was edited (e.g., tampered logic or a mismatching digest), `cxas pull` raises a `BundleDigestMismatchError` to prevent silent corruption, unless the `--force` flag is passed to override.

Alternatively, `pythonEnvFiles/pythonEnvFiles.yaml` can be synthesized seamlessly alongside `pythonEnvFiles/content/python_lib/*.py`. Passing the `--native-python-env` flag bypasses AST marker bundling entirely and switches to native `PythonEnvFile` imports, preparing agents for when the platform feature is globally enabled.

## Interim cxas_runtime Helpers

The interim Python bridge includes four helpers under `cxas_scrapi.runtime`. **Note:** These are optional bridges for `LlmAgent` apps, are not required for brand identity (`cxas build`), and are incompatible with Guided Agents.

| Helper | Purpose (LlmAgent only) | Python-Free Alternative Today | Platform Sunset Trigger |
|--------|-------------------------|-------------------------------|-------------------------|
| `escape_search_filter_literal` / `build_scoped_datastore_filter` | Escapes `\` and `"` to build secure, multi-tenant search filters | Use declarative search components | Declarative search integration |
| `get_verbatim_content_block` / `resolve_content_block_or_handoff` | Resolves localized static copy gracefully, failing-closed to handoff | Use `cxas build` content macros | Content blocks native integration |
| `adapt_presentation` | Inspects `channel_class` to adapt rich payloads vs. plain audio text | Use declarative channel routing | Declarative channels integration |
| `check_disabled_agent` | Resolves `disabled_agents` context and emits a short-circuit return | Compile-time manifest exclusions (`cxas build`) | Agent routing policies |
