# CXAS SCRAPI Build, Firewall, and Drift Gate

The `cxas` build and governance toolchain compiles multi-brand, multi-modality CES applications deterministically, enforces static architecture invariants (`FW01`–`FW10`), and protects live Console edits via SHA-256 lockfiles and a 3-way (`base` vs. `ours` vs. `live`) drift gate.

## 1. Greenfield Polymorphic Blueprint Builds (`--blueprint`)

For greenfield zero-Python architectures, `cxas build --blueprint` compiles a single declarative blueprint directory (`blueprint.yaml`, `brands/*.yaml`, `modalities/*.yaml`, `runtime_policy.yaml`, and optional `tuning_overlay.yaml`) into one self-contained CES export bundle per `(brand, modality)` pair.

### Blueprint Layout

```text
cymbal_blueprint/
  blueprint.yaml
  runtime_policy.yaml
  tuning_overlay.yaml
  brands/
    cymbal_retail.yaml
    cymbal_direct.yaml
  modalities/
    voice.yaml
    digital.yaml
```

### Commands

```bash
# Compile every (brand, modality) bundle, emit SHA-256 lockfiles, and package ZIPs
cxas build --blueprint cymbal_blueprint/ --out dist/ --write-locks --zip

# Compile a single brand/modality slice and emit deterministic JSON metadata
cxas build \
  --blueprint cymbal_blueprint/ \
  --brand cymbal_retail \
  --modality digital \
  --out dist/ \
  --write-locks \
  --json
```

During `--blueprint` compilation, the compiler:
- Substitutes brand and modality placeholders (`{brand_name}`, `{web_host}`, `{ai_disclosure}`, etc.) while preserving runtime CES session variables (`{{session_ctx.surface_id}}`).
- Prunes disabled specialists, tools, and voice-only supervisor guardrails per brand and modality, automatically stripping single-line prompt bullets that reference pruned resources.
- Synthesizes paired scalar-mirror and `OBJECT` `disablePlannerTransfer` CEL `transferRules` on the root agent from `runtime_policy.yaml` (surface restrictions, customer segment blocks, and emergency kill switches).
- Runs the strict `FW01`–`FW10` static architecture firewall on every compiled bundle before writing output.

---

## 2. Brownfield Core + Manifests Builds (`--core-dir`)

For existing exported CES applications, `cxas build --core-dir ... --manifests-dir ...` compiles a shared `core/` app directory with brand, channel, environment, and locale YAML manifests, and supports optional `runtime_policy.yaml` CEL rule synthesis, dangling-line pruning, lockfile generation, and `standard`-profile firewall checks.

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
    staging.yaml
  locales/
    content_blocks.yaml
```

### Commands

```bash
# Build all brand/channel pairs
cxas build --core-dir core/ --manifests-dir manifests/ --env prod --out dist/ --all

# Build a single brand/channel app
cxas build \
  --core-dir core/ \
  --manifests-dir manifests/ \
  --brand brand_alpha \
  --channel WEB \
  --env prod \
  --out build/brand_alpha/web

# Build with unified CEL runtime policy, dangling-line pruning, lockfiles, and standard firewall
cxas build \
  --core-dir core/ \
  --manifests-dir manifests/ \
  --env prod \
  --out dist/ \
  --all \
  --runtime-policy runtime_policy.yaml \
  --tuning-overlay tuning_overlay.yaml \
  --prune-dangling-lines \
  --no-deployments \
  --write-locks \
  --firewall \
  --firewall-profile standard

# Package an app bundle into a deterministic ZIP archive
cxas package --app-dir build/brand_alpha/web --zip web.zip
```

#### `brands/brand_alpha/identity.yaml`
```yaml
brand_id: brand_alpha
display_name: Cymbal Alpha
assistant_title: Virtual Agent
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

## 3. Static Architecture Firewall (`cxas firewall`)

`cxas firewall` audits a compiled CES bundle offline against ten deterministic governance rules (`FW01`–`FW10`).

```bash
# Strict profile (default): enforces zero Python callbacks/tools alongside FW02-FW10
cxas firewall --bundle dist/cymbal_retail-voice --blueprint cymbal_blueprint/ --profile strict

# Standard profile: permits Python callbacks/tools for brownfield apps while enforcing FW02-FW10
cxas firewall --bundle build/brand_alpha/web --profile standard --json
```

| Rule | Invariant | `strict` | `standard` |
|------|-----------|----------|------------|
| `FW01` | Python & manifest layout: zero specialist/guardrail Python callbacks, zero `pythonFunction` tools, and zero `.py` files in `strict`; permits callbacks/`pythonFunction`/`.py` files (`<= 64 KiB` per `.py` file, valid UTF-8 & `ast.parse` syntax) plus `<id>/<id>.json` manifest filename match in `standard` | Zero Python enforced | Python (`<= 64 KiB`, valid AST) + `<id>/<id>.json` |
| `FW02` | Specialist tool budget: `<= 6` attached tools per specialist agent | Enforced | Enforced |
| `FW03` | Variable budgets: `<= 16` root `OBJECT` variables and `<= 100` total variables in `app.json` | Enforced | Enforced |
| `FW04` | Prompt byte budgets: `root + global <= 15,360` B (`15 KiB`) and `specialist <= 18,432` B (`18 KiB`) | Enforced | Enforced |
| `FW05` | Brand isolation & placeholders: `0` cross-brand `forbidden_tokens`, `brand.disabled_*` absent, and `0` unresolved single-brace `{placeholder}` tokens | Enforced | Enforced (`.py` & `deployments/` exempt from `{...}` scan) |
| `FW06` | Pure RE2 regexes on `ContentFilter` guardrails (no PCRE lookarounds, backreferences, possessive quantifiers, unsupported inline flags/groups, or Python-only escapes) | Enforced | Enforced |
| `FW07` | Modality & audio supervisors: `Supervisor_*` guardrails must be `enabled: true` with `detectionMode: "NON_BLOCKING"` and no `action` block; `0` `Supervisor_*` on `digital`; `modality.disabled_*` absent | Enforced | Enforced |
| `FW08` | Model pinning & DataStore/guardrail config: version-pinned LLM models across pools, agents, guardrails, and active DataStore rewriter/summarizer (no `default`/`latest`/`auto`), non-supervisor guardrails `enabled: true`, and `DataStoreTool` `NEVER_INCLUDE` + voice `AUDIO` rewriter/summarizer disabled | Enforced | Enforced |
| `FW09` | Control-plane & template hygiene: `app.json` sets `locked: true` and `toolExecutionMode: "SEQUENTIAL"`, `0` `{%` Jinja control blocks, and `0` unstripped `{@start*}` tags | Enforced (`0` `{@start*}`, `0` `deployments/`) | Enforced (`deployments/` sidecar & valid line-isolated `{@startModality_*}` allowed) |
| `FW10` | Referential & runtime-policy integrity: valid JSON manifests, `rootAgent` and `childAgents` exist with no orphan specialists, valid intra-bundle `{@AGENT}` / `{@TOOL}` / `openApiSchema` / `instruction.txt` refs, and valid `runtime_policy.yaml` paired `disablePlannerTransfer` CEL `transferRules` | Enforced | Enforced |

---

## 4. SHA-256 Lockfiles & 3-Way CES-to-Git Drift Gate (`cxas lock`, `cxas drift`, `cxas push`)

CES exports contain server-generated volatile fields (`name`, `uuid`, `etag`, `createTime`, `updateTime`). `cxas lock` and `cxas drift` strip volatile metadata per logical resource (`app`, `agents/<id>`, `tools/<id>`, `guardrails/<id>`, `toolsets/<id>`) and compute canonical SHA-256 digests to classify changes across `base` (`<app>.lock.json`), `ours` (local compiled bundle), and `live` (CES export):

- **`CLEAN`**: `live` matches `ours` (or matches `base` with no conflicting live change).
- **`DRIFT`**: `ours == base` and `live != base` (out-of-band Console hotfix detected).
- **`ORPHAN`**: resource exists in `live` only (`not in base` and `not in ours`).
- **`CONFLICT`**: both `ours` and `live` diverged from `base` (`<<<<<<< live ... ======= ... >>>>>>> blueprint` `.diff` file emitted).

### Generating Lockfiles and Inspecting Drift

```bash
# Write <app>.lock.json lockfiles for every bundle in dist/ (or a single --bundle)
cxas lock --dist dist/
cxas lock --bundle dist/cymbal_retail-digital --lockfile dist/cymbal_retail-digital.lock.json

# Run 3-way drift analysis and snapshot normalized live state to a local git mirror branch
cxas drift \
  --app cymbal_retail-digital \
  --dist dist/ \
  --live exports/cymbal_retail-digital_live/ \
  --lockfile dist/cymbal_retail-digital.lock.json \
  --blueprint cymbal_blueprint/ \
  --conflicts-dir dist/conflicts/ \
  --git-mirror .live-mirror/

# Reconcile an intentional Console hotfix into ours + lockfile (validated through the firewall)
cxas drift \
  --app cymbal_retail-digital \
  --dist dist/ \
  --live exports/cymbal_retail-digital_live/ \
  --blueprint cymbal_blueprint/ \
  --accept-live agents/human_interaction_agent

# Reconcile a live edit on a Python-enabled brownfield bundle under the standard firewall profile
cxas drift \
  --app brand_alpha/web \
  --dist dist/ \
  --live exports/brand_alpha_web_live/ \
  --profile standard \
  --accept-live agents/default_agent

# Explicitly override a live edit with local Git state
cxas drift \
  --app cymbal_retail-digital \
  --dist dist/ \
  --live exports/cymbal_retail-digital_live/ \
  --force-ours agents/credit_cards_agent
```

### Pre-Push Enforcement (`cxas push`)

Passing `--firewall` and/or `--check-drift` to `cxas push` blocks promotion **before** `import_app` / `import_as_new_app` is called if any firewall violation or unresolved `DRIFT` / `ORPHAN` / `CONFLICT` resource is detected. When `--live-dir` is omitted on an existing target app (`--to` or `--app-name`), `cxas push --check-drift` automatically exports the live app to a temporary directory first.

```bash
# Offline pre-push check against a pre-exported live directory
cxas push \
  --app-dir dist/cymbal_retail-digital \
  --to projects/cymbal-prod/locations/us/apps/cymbal-retail-digital \
  --firewall \
  --firewall-profile strict \
  --check-drift \
  --live-dir exports/cymbal_retail-digital_live/ \
  --lockfile dist/cymbal_retail-digital.lock.json \
  --blueprint cymbal_blueprint/

# Live-export pre-push gate with explicit --force-ours override
cxas push \
  --app-dir dist/cymbal_retail-digital \
  --to projects/cymbal-prod/locations/us/apps/cymbal-retail-digital \
  --firewall \
  --check-drift \
  --lockfile dist/cymbal_retail-digital.lock.json \
  --force-ours agents/human_interaction_agent
```

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
