# Profile-Based Build and Packaging

## Overview

The `cxas build` command builds deterministic, profile- and channel-specific CES
apps from one shared `core/` app directory and a set of Pydantic-validated YAML
manifests. A **build profile** (or **app profile**) can represent a brand,
subsidiary, region, business unit, or modality variant—distinct from the CES
`Deployment.channelProfile` field, which describes the transport channel type
(such as `WEB_UI` or `GOOGLE_TELEPHONY_PLATFORM`). No LLM calls, timestamps, or
generated runtime code are involved: running `cxas build` twice on the same
inputs produces identical output trees and ZIP archives.

## One App per environment

Each environment manifest (`envs/dev.yaml`, `envs/staging.yaml`, `envs/prod.yaml`)
targets its own dedicated CES App and GCP project. CES Deployments are **never**
used for environment promotion: instead, each `(profile, channel)` build produces
a standalone App bundle deployed into that environment's dedicated CES App.
Environment-specific App settings live in the `app:` block of `envs/<env>.yaml`
so every environment's App configuration stays declarative, diffable, and
schema-validated in Git.

## Manifest Layout

```text
manifests/
  profiles/
    profile_1/
      identity.yaml
      channel_policy.yaml
    profile_2/
      identity.yaml
      channel_policy.yaml
  envs/
    prod.yaml
    staging.yaml
  locales/
    content_blocks.yaml  # optional
```

### `profiles/profile_1/identity.yaml`

Domain-owned profile identity, lexicon, URLs, explicit `languages` (optional;
defaults to `tts_voices` keys when omitted), and TTS voice bindings:

```yaml
profile_id: profile_1
display_name: Profile One
assistant_title: Virtual Assistant
legal_entity: Profile One Financial Inc.
tone_guidelines: Clear, concise, and helpful.
lexicon:
  account: Checking Account
urls:
  support: https://support.profile1.example.com
languages: ["en"]
tts_voices:
  en:
    voice: en-US-Standard-A
    speaking_rate: 1.0
forbidden_tokens:
  - LegacyProfile
```

### `profiles/profile_1/channel_policy.yaml`

Platform-owned channel capabilities, agent/tool pruning, API routing, and
supported ISO-3166 alpha-2 countries:

```yaml
profile_id: profile_1
supported_countries: ["US", "CA"]
api_routing:
  core_api: https://api.profile1.example.com
channels:
  WEB:
    supported_modalities: ["TEXT"]
    deeplink_widgets: true
  VOICE_TELEPHONY:
    entry_agent: voice_entry
    disabled_agents: [web_only_agent]
    disabled_tools: [rich_card_tool]
    supported_modalities: ["AUDIO"]
```

### `envs/prod.yaml`

Environment templates, `environment.json` overrides, and optional CES `App`
config overrides (`app:`). Template values may use `{profile}`,
`{profile_slug}`, `{env}`, `{channel}`, and `{service}`:

```yaml
environment: prod
locked: true
models:
  agent: gemini-3.5-flash
  guardrail: gemini-3.5-flash-lite
  data_store: gemini-3.5-flash-lite
templates:
  gcp_project: example-prod-{profile_slug}-{env}
  api: https://api.example.com/{env}/{service}
overrides:
  endpoint_region: us-central1
app:
  timeZoneSettings:
    timeZone: America/New_York
  guardrails:
    - profile_safety_guardrail
  audioProcessingConfig:
    inactivityTimeout: 300s
```

- **`models:`** (optional) pins explicit GA model IDs across the three slots
  CES applies (`agent` for `app.json` and agent configs, `guardrail` for LLM
  policy and prompt-security judges, and `data_store` for enabled data-store
  rewriter/summarizer configs).
- **`locked: true`** (optional) declares governed lock policy in built
  `app.json`. When pushing a locked bundle (or pushing to an already-locked
  CES app), `cxas push` unlocks the target if needed, imports with
  `locked=false`, creates an optional version, and applies `locked=true` via
  `UpdateApp` (restoring the lock if import fails).

`cxas build` always validates the final `app.{json,yaml,yml}` config against the
CES `App` proto schema (`V001`), attributing errors to `core/app.*` or
`envs/<env>.yaml`. Canonical camelCase CES `App` field names are required (for
example `displayName`, `rootAgent`, `languageSettings`; `snake_case` keys such
as `root_agent` are rejected). When `app:` is present, overlays apply in
deterministic order (later layers win):

1. `core/app.{json,yaml,yml}` (after token/conditional rendering)
2. `envs/<env>.yaml` `app:` (tokens and conditionals inside `app:` are rendered
   before merging)
3. Profile-derived settings (`rootAgent` from channel `entry_agent`,
   `audioProcessingConfig.synthesizeSpeechConfigs`, and `languageSettings`)

Merge semantics for `app:`:

- Nested mappings deep-merge recursively (for example, `app:` can set
  `audioProcessingConfig.inactivityTimeout` without clobbering profile-derived
  `synthesizeSpeechConfigs`).
- Lists replace wholesale (never concatenated).
- Scalars replace.
- Explicit `null` deletes the key at any depth.
- CES `App` config keys must use camelCase (for example `timeZoneSettings`,
  `audioProcessingConfig`, `rootAgent`), matching `cxas lint` rule `A006`.

Server-managed, read-only, and lock-controlled `App` fields are forbidden at the
top level of `app:` (in both camelCase and snake_case): `name`, `locked`,
`scmSettings` / `scm_settings`, `createTime` / `create_time`, `updateTime` /
`update_time`, `etag`, `deploymentCount` / `deployment_count`,
`predefinedVariableDeclarations` / `predefined_variable_declarations`, and
`validationErrors` / `validation_errors`.

### `locales/content_blocks.yaml` (Optional)

Verbatim regulated copy keyed `profile_id -> country -> language -> key`:

```yaml
blocks:
  profile_1:
    US:
      en:
        greeting: "Hello from Profile One!"
```

## Commands

Build every `(profile, channel)` pair declared in `manifests/`:

```bash
cxas build --core-dir core/ --manifests-dir manifests/ --env prod --out dist/ --all
```

Build a single `(profile, channel)` target and package it to a deterministic ZIP:

```bash
cxas build \
  --core-dir core/ \
  --manifests-dir manifests/ \
  --profile profile_1 \
  --channel WEB \
  --env prod \
  --out dist/profile_1/web \
  --zip
```

Package any CES app directory into a deterministic ZIP archive:

```bash
cxas package --app-dir dist/profile_1/web --zip dist/profile_1_web.zip
```

Inject a custom `environment.json` while packaging:

```bash
cxas package \
  --app-dir dist/profile_1/web \
  --env-file envs/prod_environment.json \
  --zip dist/profile_1_web.zip
```

Lint a built profile/channel app with polymorphic rules `P001`–`P005`:

```bash
cxas lint \
  --app-dir dist/profile_1/web \
  --manifests-dir manifests/
```

Pass `--profile` and `--channel` to override the metadata in `deployments/*.yaml`.

## Placeholders and Conditional Blocks

`cxas build` resolves tokens across `.json`, `.yaml`/`.yml`, `.txt`, and `.py`
files and fails fast if any unresolved `@@...@@` token remains. In YAML files,
quote `@@...@@` scalars (for example, `url: "@@routing.api_base@@"`) because
`@` is a reserved YAML indicator.

- **Profile**: `@@profile.profile_id@@`, `@@profile.display_name@@`,
  `@@profile.assistant_title@@`, `@@profile.legal_entity@@`,
  `@@profile.tone_guidelines@@`, `@@profile.lexicon.<key>@@`,
  `@@profile.urls.<key>@@`
- **Global identity block**: `@@profile.identity_block@@` in
  `global_instruction.txt` (prepended automatically if omitted)
- **Routing & build metadata**: `@@routing.<key>@@`, `@@build.channel@@`,
  `@@build.environment@@`
- **Environment templates**: `@@env.<key>@@` and `@@env.<key>:<service>@@`

Conditional blocks let shared prompts and Python files branch cleanly by
channel, profile, or active resource without runtime branching:

- `{@startChannel_<CHANNEL>} ... {@endChannel_<CHANNEL>}`
- `{@startProfile_<PROFILE_ID>} ... {@endProfile_<PROFILE_ID>}`
- `{@startAgentEnabled_<AGENT>} ... {@endAgentEnabled_<AGENT>}`
- `{@startToolEnabled_<TOOL>} ... {@endToolEnabled_<TOOL>}`

## Output Artifacts

Each built app directory includes:

- Pruned `agents/` and `tools/` (and cleaned `childAgents` / `tools`
  references, plus warnings for stale `{@AGENT: ...}` or `{@TOOL: ...}`
  mentions in surviving `.txt` files)
- Updated `app.json` (or `app.yaml` / `app.yml`)
  (`audioProcessingConfig.synthesizeSpeechConfigs`, `languageSettings`, and
  `rootAgent` synced to any configured `entry_agent`—which must match a
  surviving agent under `agents/`)
- Merged `environment.json`
- Generated `deployments/<channel-slug>.yaml` **build metadata** recording the
  `profileId`, CES `channelProfile`, countries, languages, modalities, and
  `entryAgent` (this is local build metadata—excluded from packaged ZIPs—and
  **not** a live CES Deployment resource)
- Optional `<profile_id>_<channel-slug>.zip` when `--zip` is passed

## Shared Python Bundler

`cxas push`, `cxas package`, and `cxas build` automatically bundle shared Python
modules from a `python_lib/` directory into callback entrypoints
(`agents/*/*_callbacks*/**/python_code.py`) and Python tool entrypoints
(`tools/*/python_function/python_code.py`).

Bundling uses AST-based topological tree-shaking inside
`# === BEGIN CXAS_SHARED_BUNDLE (sha256:<digest>) ===` /
`# === END CXAS_SHARED_BUNDLE ===` markers. For each entrypoint, the bundler
computes the transitive dependency closure from `python_lib/`, validates syntax,
enforces a `64 KiB` per-file bundle budget (`32 MiB` total archive budget), and
emits a deterministic marker block.

On `cxas pull`, `strip_shared_bundles` removes these marker blocks cleanly while
preserving edits made *outside* the marker block. If the marker block itself was
modified in the Cloud Console (digest mismatch, unexpected statements, or drift
against local `python_lib/`), `cxas pull` raises `BundleDigestMismatchError` to
prevent silent data loss unless `--force` is passed.

In parallel, `pythonEnvFiles/pythonEnvFiles.yaml` and
`pythonEnvFiles/content/python_lib/*.py` are synthesized from `python_lib/`.
Passing `--native-python-env` on `cxas push`, `cxas package`, or `cxas build`
bypasses AST marker injection and leaves `python_code.py` entrypoints untouched
for native `PythonEnvFile` imports.

## Interim Runtime Helpers (`cxas_scrapi.runtime`)

Optional interim helpers are provided under `cxas_scrapi.runtime` for `LlmAgent`
apps. They are not required for profile identity (`cxas build`) and are
incompatible with Guided Agents:

| Helper | Purpose (`LlmAgent` only) | Python-Free Alternative Today | Platform Sunset Trigger |
|--------|---------------------------|-------------------------------|-------------------------|
| `escape_search_filter_literal` / `build_scoped_datastore_filter` | Escapes `\` and `"` to build scoped multi-tenant search filters | Use declarative search components | Declarative search integration |
| `get_verbatim_content_block` / `resolve_content_block_or_handoff` | Resolves localized static copy, failing closed to handoff | Use `cxas build` content macros | Native content blocks integration |
| `adapt_presentation` | Inspects `channel_class` to adapt rich payloads vs. plain audio text | Use declarative channel routing | Declarative channels integration |
| `check_disabled_agent` | Inspects `disabled_agents` context and emits a short-circuit return | Build-time manifest exclusions (`cxas build`) | Native agent routing policies |
