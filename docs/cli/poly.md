# cxas poly

`cxas poly` is the **Polymorphism Engine**: it compiles a single base agent project plus per-channel **adapter cards** into channel-optimized agent project directories. The compiled output is a complete, ordinary SCRAPI project — lintable, evaluable, and deployable with no special handling.

See the **[Polymorphism guide](../guides/polymorphism.md)** for concepts and the **[Polymorphism pattern](../patterns/polymorphism.md)** for a full walkthrough.

The command has six subcommands:

| Subcommand | Purpose |
|---|---|
| [`cxas poly init`](#cxas-poly-init) | Scaffold starter adapter cards and referenced assets. |
| [`cxas poly build`](#cxas-poly-build) | Compile channel-optimized projects. |
| [`cxas poly validate`](#cxas-poly-validate) | Validate adapter cards against the base project. |
| [`cxas poly doctor`](#cxas-poly-doctor) | Explain validation failures with guided fixes. |
| [`cxas poly readiness`](#cxas-poly-readiness) | Summarize validation, diff, eval coverage, and build readiness before launch review. |
| [`cxas poly diff`](#cxas-poly-diff) | Show what an adapter changes for a channel. |

Adapter cards live in `<app-dir>/adapters/` and are named `*.adapter.yaml`, `*.adapter.yml`, or `*.adapter.json`.

---

## cxas poly init

Scaffold one or more starter channel adapters from an existing direct SCRAPI app
directory. The command writes only fields supported by the current adapter-card
schema, then creates the minimum referenced filesystem around the card: optional
channel evals, optional channel-only tool definitions, and optional callbacks.

### Usage

```
cxas poly init [--app-dir DIR]
               [--channel NAME[,NAME] ...]
               [--agent AGENT]
               [--deployment-target auto|none|CHANNEL_TYPE]
               [--modality auto|none|MODALITY]
               [--with-tool TOOL_NAME]
               [--with-callback CALLBACK_TYPE]
               [--no-eval]
               [--dry-run]
               [--force]
```

If `--channel` is omitted in an interactive terminal, the command prompts for a
comma-separated channel list. In automation, pass `--channel` explicitly.

### Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--app-dir DIR` | No | `.` | Path to the existing base app. Must directly contain `app.json`. |
| `--channel NAME` | No in TTY, yes in automation | prompt | Channel id to scaffold. Repeat or comma-separate for multiple channels. |
| `--agent AGENT` | No | app `rootAgent` | Agent directory or displayName to receive starter instruction/tool/callback deltas. |
| `--display-name NAME` | No | generated | Adapter display name for a single channel. |
| `--display-name-template TEMPLATE` | No | `{app} - {channel_title}` | Template for multiple adapters. Supports `{app}`, `{channel}`, `{channel_title}`, and `{channel_slug}`. |
| `--deployment-target VALUE` | No | `auto` | `auto`, `none`, or a supported `deployment.channelType` enum such as `WEB_UI`, `API`, `TWILIO`, or `GOOGLE_TELEPHONY_PLATFORM`. |
| `--modality VALUE` | No | `auto` | `auto`, `none`, or a supported modality enum such as `CHAT_ONLY` or `VOICE_ONLY`. |
| `--with-tool TOOL_NAME` | No | none | Create a channel-only Python tool under `adapters/<channel>_tools/` and reference it via `toolDefinitions`. May be repeated. |
| `--with-callback TYPE` | No | none | Create and reference a starter callback. Supported types match the adapter schema: `before_model`, `after_model`, `before_tool`, `after_tool`, `before_agent`, `after_agent`. |
| `--no-eval` | No | off | Skip the starter `adapters/<channel>_evals/` directory. |
| `--dry-run` | No | off | Print planned files without writing. |
| `--force` | No | off | Overwrite existing scaffold files. Without it, init refuses to clobber files. |

### Auto defaults

`auto` deployment defaults are intentionally small and explicit:

| Channel id | `channelType` | `modality` |
|------------|---------------|------------|
| `chat`, `web` | `WEB_UI` | `CHAT_ONLY` |
| `voice`, `phone`, `telephony` | `GOOGLE_TELEPHONY_PLATFORM` | `VOICE_ONLY` |
| `api` | `API` | `CHAT_ONLY` |

Unknown channel ids are allowed, but `auto` does not invent a deployment block
for them. Pass `--deployment-target` and `--modality` when you want one.

### Example

```bash
cxas poly init \
  --app-dir examples/polymorphic_pizza \
  --channel sms \
  --deployment-target TWILIO \
  --modality VOICE_ONLY \
  --with-tool send_sms_card \
  --with-callback before_model
```

This creates:

```
adapters/sms.adapter.yaml
adapters/sms_evals/Sms_Smoke/Sms_Smoke.yaml
adapters/sms_tools/send_sms_card/send_sms_card.json
adapters/sms_tools/send_sms_card/python_code.py
adapters/sms_callbacks/before_model.py
```

Run `cxas poly validate --app-dir examples/polymorphic_pizza --explain` next,
then replace the starter instruction/eval/tool/callback content with real
channel behavior.

---

## cxas poly build

Compile one or all channels into output project directories.

### Usage

```
cxas poly build [--app-dir DIR]
                [--channel NAME|all]
                [--output-dir DIR]
```

### Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--app-dir DIR` | No | `.` (current directory) | Path to the base agent project root (the directory containing `app.json`, `agents/`, `tools/`, `adapters/`). |
| `--channel NAME` | No | `all` | A specific channel to compile (matched against an adapter's `metadata.channel`), or `all` to compile every adapter. |
| `--output-dir DIR` | No | `./output` | Directory to write compiled projects into. Each channel is written to `<output-dir>/<channel>/`. Must not overlap the base project. |
| `--force` | No | off | Overwrite a `<output-dir>/<channel>/` that is non-empty and was **not** created by `cxas poly build`. Without it, such a directory is left untouched. |
| `--strict` | No | off | Treat warnings as errors and abort the build. |

### Behavior

- With `--channel all`, all adapters are validated first; if any **error**-severity issue is found (including a malformed card), nothing is written.
- With a specific `--channel`, only that adapter is validated and compiled.
- A `<output-dir>/<channel>/` directory is replaced only when it is empty or was previously produced by `cxas poly build` (it carries a `.poly_build.json` marker); otherwise the build refuses unless `--force` is given. The output directory may never overlap the base project.

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | All requested channels compiled and written successfully. |
| `1` | No adapters found, unknown `--channel`, validation errors, or a missing base project. |

### Example

```bash
cxas poly build --app-dir examples/bella_notte --output-dir ./output
```

```
Compiled channel 'chat'  -> ./output/chat
Compiled channel 'voice' -> ./output/voice

Done. 2 channel(s) written to ./output
```

---

## cxas poly validate

Validate every adapter card against the base project structure. Prints results in the same severity/rule-ID format as `cxas lint`.

### Usage

```
cxas poly validate [--app-dir DIR] [--format text|json] [--strict] [--explain]
```

### Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--app-dir DIR` | No | `.` (current directory) | Path to the base agent project root. |
| `--format` | No | `text` | Output format. `json` emits a machine-readable report (`errors`, `warnings`, `issues[]`) for CI. |
| `--strict` | No | off | Exit non-zero if any warnings are present (not just errors). |
| `--explain` | No | off | Add guided issue explanations: what failed, why, where to look, and a likely fix shape. With `--format json`, emits the doctor report shape. |

### Rules

| ID | Severity | Check |
|----|----------|-------|
| `AD001` | error | Adapter card has required fields (`apiVersion`, `kind`, `metadata.channel`), valid types, and no unknown fields (malformed cards are reported here, not as a traceback). |
| `AD002` | error | Every agent referenced in `instructionDiffs`, `tools`, `modelOverrides`, `callbacks` exists in `agents/`. |
| `AD003` | error | `replace_section` diffs set `sectionTag`, and a matching `<sectionTag …>…</sectionTag>` block (attributes allowed) exists in the target instruction. |
| `AD004` | warning | A tool `remove` references a tool not in the base agent's tool list. |
| `AD005` | error | A tool `add` references a tool defined neither in `tools/`, the adapter's `toolDefinitions`, nor a platform tool; or a referenced `pythonCode`/`sourceDir` is missing. |
| `AD006` | warning | The adapter declares no `evaluations` entries. |
| `AD007` | error | Two adapter cards target the same `metadata.channel`. |
| `AD008` | error | A referenced `sourceDir`/`pythonCode` path escapes the project root. |
| `AD009` | error | `deployment` `channelType`/`modality`/`theme` use known, supported values. |
| `AD010` | error | `toolDefinitions` declare a supported `toolType` (`python`, `openapi`). |

> **Note on rule IDs:** the `AD` prefix is used (not `A`) because the `config` lint category already owns `A001`–`A006`. The same rules run inside `cxas lint --only adapters`.

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | No errors (warnings may be present, unless `--strict`), or no adapter cards found. |
| `1` | One or more **error**-severity issues (or any warnings with `--strict`), or a missing base project. |

### Example

```bash
cxas poly validate --app-dir examples/bella_notte
```

```
All 2 adapter card(s) valid.
```

---

## cxas poly doctor

Explain adapter validation findings in a guided debugging format. `doctor` uses
the same validators as `validate`; it only enriches their issues with context.

### Usage

```
cxas poly doctor [--app-dir DIR] [--format text|json] [--strict]
```

### Output

For each issue, doctor answers:

- what failed
- why the rule exists
- the adapter file and field path that need attention
- related referenced files or directories when the message includes them
- a likely fix shape

Example:

```
ERROR [AD005] adapters/chat.adapter.yaml
  field: tools[0]
  look at: adapters/chat_tools/send_rich_card
  what failed: tools[0] adds 'send_rich_card' which has no definition...
  why: The compiler only copies files that exist under the direct app root...
  likely fix: Create the referenced sourceDir/pythonCode path...
```

`cxas poly validate --explain` is equivalent for developers who start from the
validation command. Use `cxas poly doctor --format json` for structured tooling.

---

## cxas poly readiness

Summarize whether a polymorphic project is ready for design-partner or launch
review. `readiness` does not write output; it composes the existing validators,
compiler, and diff report into one pre-build report.

Use it after `validate`/`diff` and before `build` when you want a single answer
to: Which channels are ready? Which need attention? Are channel evals present?
Will any channel eval names shadow base eval names in the compiled project?

### Usage

```
cxas poly readiness [--app-dir DIR] [--format text|json] [--strict]
```

### Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--app-dir DIR` | No | `.` (current directory) | Path to the base agent project root. |
| `--format` | No | `text` | Output format. `json` emits a stable `poly-readiness/v1` report for CI and review artifacts. |
| `--strict` | No | off | Exit non-zero if any warnings are present. |

### Output

Each channel is marked:

| Status | Meaning |
|--------|---------|
| `ready` | The adapter validates, compiles, has no warning-level findings, and has no eval namespace collisions. |
| `attention` | The adapter has warning-level findings such as missing channel evals or duplicate eval names. |
| `blocked` | The adapter has validation or compile errors. |

The JSON report includes:

- `schema_version: "poly-readiness/v1"`
- `summary` counts for ready/attention/blocked channels, errors, warnings, and launch readiness
- `adapter_errors[]` for malformed cards that could not be parsed
- `channels[]` with adapter path, AD issues, coverage warnings, diff summary,
  eval coverage counts, duplicate eval names, and next steps

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | No errors. Warnings may be present unless `--strict` is passed. |
| `1` | One or more errors, warnings with `--strict`, or a missing base project. |

### Example

```bash
cxas poly readiness --app-dir examples/bella_notte
cxas poly readiness --app-dir examples/bella_notte --format json
```

Example text output:

```
Poly readiness   2 ready, 0 attention, 0 blocked
Issues: 0 error(s), 0 warning(s)

chat (ready)   adapters/chat.adapter.yaml
  diff: 2 agent(s), 2 instruction diff(s), 1 tool add(s), 0 tool remove(s), 1 callback(s)
  evals: 33 base, 1 channel
  next: Run cxas poly build, lint the compiled output, and run channel evals.
```

---

## cxas poly diff

Show what a channel's adapter changes without writing any files. By default the
command renders a reviewer-friendly text diff. Use `--json` for a stable
machine-readable report.

### Usage

```
cxas poly diff CHANNEL [--app-dir DIR] [--json]
```

### Arguments & options

| Argument / Option | Required | Default | Description |
|-------------------|----------|---------|-------------|
| `CHANNEL` | Yes | — | The channel to diff (matched against an adapter's `metadata.channel`). |
| `--app-dir DIR` | No | `.` (current directory) | Path to the base agent project root. |
| `--json` | No | off | Emit a `poly-diff/v1` JSON report for CI and tooling. |

### Output

For each touched agent, the text diff shows instruction additions/replacements,
tool changes (`+`/`-`), model overrides (`old -> new`), and added callbacks,
followed by new tools, merged evaluations, `gecxConfig` overlays, and deployment
overrides.

The JSON report includes:

- `schema_version: "poly-diff/v1"`
- `channel`, `adapter_path`, and `app_dir`
- `summary` counts
- grouped `agents[]` deltas
- `tool_definitions_added[]`
- `evaluation_merges`
- `gecx_config_overlay`
- `deployment`
- a flattened `deltas[]` list for simple CI checks

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Diff rendered successfully. |
| `1` | Unknown channel, compilation error, or a missing base project. |

### Example

```bash
cxas poly diff voice --app-dir examples/bella_notte
```

```
Channel: voice   (adapter: adapters/voice.adapter.yaml)
Summary: 2 agent(s), 2 instruction diff(s), 0 tool add(s), 0 tool remove(s), 1 callback(s), 1 eval merge(s)

agents/Bella_Notte_Host
  instruction: + N line(s) append
  callbacks: + before_model (Inject voice pacing and filler-phrase hints.)

gecx-config.json (deployment)
  + channel_type: GOOGLE_TELEPHONY_PLATFORM
  + modality: VOICE_ONLY
```

JSON example:

```bash
cxas poly diff chat --app-dir examples/bella_notte --json
```

```json
{
  "schema_version": "poly-diff/v1",
  "channel": "chat",
  "adapter_path": "adapters/chat.adapter.yaml",
  "summary": {
    "agents_touched": 2,
    "tools_added": 1,
    "callbacks_added": 1,
    "deployment_changed": true
  }
}
```

---

## See also

- **[Polymorphism guide](../guides/polymorphism.md)**
- **[Polymorphism pattern](../patterns/polymorphism.md)**
- **[`cxas lint`](lint.md)** — the `adapters` category runs `AD001`–`AD010` as part of a normal lint.
