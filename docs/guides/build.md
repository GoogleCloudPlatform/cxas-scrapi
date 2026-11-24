# Multi-Brand Build and Packaging

## Overview

The `cxas build` command builds deterministic, brand- and channel-specific CES
apps from one shared `core/` app directory and a set of Pydantic-validated YAML
manifests. No LLM calls, timestamps, or generated runtime code are involved:
running `cxas build` twice on the same inputs produces identical output trees
and ZIP archives.

## Manifest Layout

```text
manifests/
  brands/
    brand_1/
      identity.yaml
      channel_policy.yaml
    brand_2/
      identity.yaml
      channel_policy.yaml
  envs/
    prod.yaml
    staging.yaml
  locales/
    content_blocks.yaml  # optional
```

### `brands/brand_1/identity.yaml`

Business-owned brand identity, lexicon, URLs, and TTS voice bindings:

```yaml
brand_id: brand_1
display_name: Brand One
assistant_title: Virtual Assistant
legal_entity: Brand One Financial Inc.
tone_guidelines: Clear, concise, and helpful.
lexicon:
  account: Checking Account
urls:
  support: https://support.brand1.example.com
tts_voices:
  en:
    voice: en-US-Standard-A
    speaking_rate: 1.0
```

### `brands/brand_1/channel_policy.yaml`

Platform-owned channel capabilities, agent/tool pruning, API routing, and
supported ISO-3166 alpha-2 countries:

```yaml
brand_id: brand_1
supported_countries: ["US", "CA"]
api_routing:
  core_api: https://api.brand1.example.com
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

Environment templates and `environment.json` overrides. Template values may use
`{brand}`, `{brand_slug}`, `{env}`, `{channel}`, and `{service}`:

```yaml
environment: prod
templates:
  gcp_project: example-prod-{brand_slug}-{env}
  api: https://api.example.com/{env}/{service}
overrides:
  endpoint_region: us-central1
```

### `locales/content_blocks.yaml` (Optional)

Verbatim regulated copy keyed `brand_id -> country -> language -> key`:

```yaml
blocks:
  brand_1:
    US:
      en:
        greeting: "Hello from Brand One!"
```

## Commands

Build every `(brand, channel)` pair declared in `manifests/`:

```bash
cxas build --core-dir core/ --manifests-dir manifests/ --env prod --out dist/ --all
```

Build a single `(brand, channel)` target and package it to a deterministic ZIP:

```bash
cxas build \
  --core-dir core/ \
  --manifests-dir manifests/ \
  --brand brand_1 \
  --channel WEB \
  --env prod \
  --out dist/brand_1/web \
  --zip
```

Package any CES app directory into a deterministic ZIP archive:

```bash
cxas package --app-dir dist/brand_1/web --zip dist/brand_1_web.zip
```

Inject a custom `environment.json` while packaging:

```bash
cxas package \
  --app-dir dist/brand_1/web \
  --env-file envs/prod_environment.json \
  --zip dist/brand_1_web.zip
```

## Placeholders and Conditional Blocks

`cxas build` resolves tokens across `.json`, `.txt`, and `.py` files and fails
fast if any unresolved `@@...@@` token remains:

- **Brand**: `@@brand.brand_id@@`, `@@brand.display_name@@`,
  `@@brand.assistant_title@@`, `@@brand.legal_entity@@`,
  `@@brand.tone_guidelines@@`, `@@brand.lexicon.<key>@@`, `@@brand.urls.<key>@@`
- **Global identity block**: `@@brand.identity_block@@` in
  `global_instruction.txt` (prepended automatically if omitted)
- **Routing & build metadata**: `@@routing.<key>@@`, `@@build.channel@@`,
  `@@build.environment@@`
- **Environment templates**: `@@env.<key>@@` and `@@env.<key>:<service>@@`

Conditional blocks let shared prompts and Python files branch cleanly by
channel, brand, or active resource without runtime branching:

- `{@startChannel_<CHANNEL>} ... {@endChannel_<CHANNEL>}`
- `{@startBrand_<BRAND_ID>} ... {@endBrand_<BRAND_ID>}`
- `{@startAgentEnabled_<AGENT>} ... {@endAgentEnabled_<AGENT>}`
- `{@startToolEnabled_<TOOL>} ... {@endToolEnabled_<TOOL>}`

## Output Artifacts

Each built app directory includes:

- Pruned `agents/` and `tools/` (and cleaned `childAgents` / `tools`
  references, plus warnings for stale `{@AGENT: ...}` or `{@TOOL: ...}`
  mentions in surviving `.txt` files)
- Updated `app.json` (`audioProcessingConfig.synthesizeSpeechConfigs`,
  `languageSettings`, and `rootAgent` fallback when missing or pruned)
- Merged `environment.json`
- Generated `deployments/<channel-slug>.yaml` describing the channel profile,
  countries, languages, and modalities
- Optional `<brand_id>_<channel-slug>.zip` when `--zip` is passed
