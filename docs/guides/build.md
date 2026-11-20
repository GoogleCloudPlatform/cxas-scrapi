# CXAS SCRAPI Build, Lint Gate, Governed Lock, Drift Gate, and Back-Porting

`cxas build --core-dir ... --manifests-dir ...` is the single build path: one shared, templated `core/` CES app plus
Pydantic-validated YAML manifests (brand identity, channel policy, optional per-brand `overrides.yaml`, environment,
locale content blocks) build into one CES app per `brand x channel`. Built apps are checked by `cxas lint`
(including the polymorphic rules P001-P005), locked to a canonical SHA-256 lockfile, promoted with `cxas push` behind
an optional lint gate, 3-way drift gate, and governed lock, and reconciled from Console tweaks via `cxas backport`.

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
      overrides.yaml        # optional per-brand agent/tool/instruction overrides
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

Every built app also gets `deployments/<channel>.yaml` (`brandId`, `channel`, `environment`, ...). It is build
metadata (never part of the pushed archive) and gives `cxas lint` its brand context.

#### `brands/brand_alpha/identity.yaml`
```yaml
brand_id: brand_alpha
display_name: Cymbal Alpha
assistant_title: Virtual Agent
lexicon:
  ai_disclosure: "Hello! You are chatting with Cymbal Alpha's AI assistant."
forbidden_tokens: [CymbalBetaPass]   # P001: never in brand_alpha's built app
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
locked: true            # governed lock: built app.json declares locked: true
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
| `P001` | error | No sister brand `display_name` and no own `forbidden_tokens` anywhere in the built app. |
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

## 5. Per-Brand Overrides & Console-to-Git Back-Porting (`overrides.yaml`, `cxas backport`)

### Per-Brand `overrides.yaml` (`manifests/brands/<brand_id>/overrides.yaml`)

An individual brand team (for example, `cymbal_direct` for `"Cymbal Direct"`) can customize `global_instruction`,
individual agents, and tools in `manifests/brands/cymbal_direct/overrides.yaml` without touching shared `core/` and
with **zero blast radius** on sibling brands (`cymbal_retail` for `"Cymbal Retail"` builds byte-for-byte unchanged):

```yaml
brand_id: cymbal_direct

global_instruction:
  replace_lines:
    - match: "Support hours are 08:00-20:00 local time."
      with: "24/7 digital support is available inside @@brand.urls.portal@@."
  append_rules:
    - "Always identify yourself as the @@brand.display_name@@ digital concierge."
  channels:
    DIGITAL_APP:
      append_rules:
        - "Surface self-service controls before offering a live transfer."

agents:
  credit_cards_agent:
    description: "Card controls and limit servicing specialist for @@brand.display_name@@."
    temperature: 1.0
    model: gemini-3.5-flash
    replace_lines:
      - match: "Confirm the last four card digits before submitting a limit change."
        with: "Confirm the last four digits and @@brand.lexicon.auth_method@@ approval first."
    append_rules:
      - "Remind @@brand.display_name@@ cardholders that virtual cards activate immediately."
    channels:
      DIGITAL_APP:
        append_rules:
          - "Direct cardholders to @@brand.urls.portal@@ for instant card freeze."

tools:
  change_credit_card_limit:
    description: "Update a @@brand.display_name@@ card limit authorized with @@brand.lexicon.auth_method@@."
    url: "https://api.cymbal-direct.example.com/v1/cards/limit"
    channels:
      DIGITAL_APP:
        description: "In-app @@brand.display_name@@ card limit update via @@brand.lexicon.auth_method@@."
```

In `manifests/brands/cymbal_direct/identity.yaml`, those placeholders bind cleanly to Cymbal Direct values (for
example `display_name: "Cymbal Direct"`, `urls.portal: "https://direct.cymbal.example.com"`, and
`lexicon.auth_method: "Cymbal Pass"`).

### Console-to-Git PR Back-Porting (`cxas backport`)

When a prompt engineer or brand owner refines instructions, descriptions, models, temperatures, or OpenAPI URLs in the
CES Console UI and exports the live app, `cxas backport` lifts those changes back into Git safely:

1. **Pre- & Post-Backport `cxas lint` Gate (`P001`–`P005` + structural rules)**: Validates the live export and the
   rebuilt bundle through `cxas lint` (unless `--no-lint` is passed), catching sister-brand leaks (`P001`),
   unresolved `@@...@@` / `{@start...}` tokens (`P003`), disabled agent/tool leaks (`P004`), or unpinned/deprecated
   models (`P005`).
2. **3-Way Lockfile Conflict Protection**: Compares `base` (`<app>.lock.json`), `ours` (built `core/` +
   `manifests/`), and `live` so concurrent Git edits emit `.diff` conflict markers instead of being silently
   overwritten (unless explicitly overridden via `--force-live`).
3. **Reverse-Templating & Scoped Targeting**: Lifts brand literals back to `@@brand.display_name@@` /
   `@@brand.urls.*@@` / `@@brand.lexicon.*@@` tokens and writes either to
   `manifests/brands/<brand>/overrides.yaml` (`--target brand`, scoped to `--scope channel` by default or
   `--scope brand` across all channels of that brand) or directly to shared `core/` (`--target core`).
4. **Round-Trip SHA-256 Proof**: Rebuilds from updated `core/` + `manifests/`, verifies canonical SHA-256 resource
   equality with `live` (`ROUND_TRIP_PROOF PASS`), and proves sibling brand/channel bundles (`cymbal_retail/*`,
   other channels of `cymbal_direct`) remain untouched (`UNCHANGED_SIBLINGS`).

```bash
# Channel-scoped brand override (default: --target brand --scope channel)
cxas backport \
  --core-dir core/ \
  --manifests-dir manifests/ \
  --live exports/cymbal_direct_digital_live/ \
  --brand cymbal_direct \
  --channel DIGITAL_APP \
  --env prod \
  --dist dist/ \
  --lockfile dist/cymbal_direct/digital_app.lock.json \
  --pr-body-out backport_pr.md \
  --git-branch backport/cymbal-direct-cards

# Brand-wide override across all channels of cymbal_direct (--target brand --scope brand)
cxas backport \
  --core-dir core/ \
  --manifests-dir manifests/ \
  --live exports/cymbal_direct_digital_live/ \
  --brand cymbal_direct \
  --channel DIGITAL_APP \
  --env prod \
  --target brand \
  --scope brand \
  --dist dist/

# Shared core/ promotion across every brand (--target core)
cxas backport \
  --core-dir core/ \
  --manifests-dir manifests/ \
  --live exports/cymbal_direct_digital_live/ \
  --brand cymbal_direct \
  --channel DIGITAL_APP \
  --env prod \
  --target core \
  --dist dist/ \
  --json
```

Real CES Console exports carry no `deployments/*.yaml`, so pass `--brand`, `--channel`, and `--env` whenever `--dist`
points at a multi-brand `dist/` root. When they are omitted, `cxas backport` infers the app only if every declared
candidate is built under `dist/`, exactly one of them has the live export's resource set, and it shares at least one
identical normalized SHA-256 resource; otherwise it fails closed and lists the ambiguous or unproven candidates.
Resources that Git advanced since the last lockfile while the Console left them untouched (`CLEAN` with
`live == base`, `ours != base`) keep their Git state; `--accept-live` hotfixes are still backported. Only the
reconciled app's `.lock.json` is
refreshed (sibling lockfiles are never rewritten). Conflict `.diff` files are written only when an unresolved conflict
remains (or when `--conflicts-dir` is passed explicitly).

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
| `check_disabled_agent` | Resolves `disabled_agents` context and emits a short-circuit return | Build-time manifest exclusions (`cxas build`) | Agent routing policies |
