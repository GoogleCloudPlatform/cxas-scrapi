# CXAS SCRAPI Build and Packaging

## Declarative Build

The `cxas build` command generates deterministic brand/channel apps from a core app directory and a set of manifests.

### Manifest Layout

```
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

`cxas build --core-dir core/ --manifests-dir manifests/ --env prod --all`
`cxas build --core-dir core/ --manifests-dir manifests/ --brand brand_alpha --channel WEB --env prod --out build/brand_alpha/web`
`cxas package --app-dir build/brand_alpha/web --zip web.zip`


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
