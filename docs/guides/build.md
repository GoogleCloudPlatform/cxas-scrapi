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

`cxas build` always validates the final `app.{json,yaml,yml}` config against the
CES `App` proto schema (`V001`), attributing errors to `core/app.*` or
`envs/<env>.yaml`. When `app:` is present, overlays apply in deterministic order
(later layers win):

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
