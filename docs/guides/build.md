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
