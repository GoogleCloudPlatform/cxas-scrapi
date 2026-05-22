# Polymorphism Proof of Concept with SCRAPI

<html>
    <h2 align="center">
      Polymorphic Agents Experiment - Author one agent at the center. Compile channel-optimized variants at the edges.
    </h2>
    <h3 align="center">
      Important Links:
      <a href="https://googlecloudplatform.github.io/cxas-scrapi/stable/">Docs</a>,
      <a href="examples/">Examples</a>,
      <a href="docs/guides/polymorphism.md">Polymorphism Guide</a>,
      <a href="docs/cli/poly.md"><code>cxas poly</code> CLI</a>
    </h3>
</html>

CXAS SCRAPI is a Python API, CLI, and set of Agent Skills for CX Agent Studio.
This README focuses on its **architecture** and on the feature this repository is
built around: **polymorphism** — authoring an agent project once and compiling a
complete, channel-optimized project (chat, voice, …) for each target.

> New to the project's auth, install, and core SDK? See the
> [official docs](https://googlecloudplatform.github.io/cxas-scrapi/stable/).
> This document assumes you just want to understand how the pieces fit together
> and how to use polymorphism.

---

## Table of contents

1. [The core idea](#the-core-idea)
2. [Quickstart: the Polymorphic Pizza demo](#quickstart-the-polymorphic-pizza-demo)
3. [Architecture at a glance](#architecture-at-a-glance)
4. [Inside the polymorphism engine](#inside-the-polymorphism-engine)
5. [Using polymorphism, step by step](#using-polymorphism-step-by-step)
6. [Adapter card reference](#adapter-card-reference)
7. [The compilation pipeline](#the-compilation-pipeline)
8. [Driving the engine from Python](#driving-the-engine-from-python)
9. [Validation rules](#validation-rules)
10. [When to use adapters vs. separate agents](#when-to-use-adapters-vs-separate-agents)
11. [Where to go next](#where-to-go-next)

---

## The core idea

A reservation agent that works beautifully in a web chat widget rarely works
well on a phone call. Chat wants Markdown, numbered lists, and rich confirmation
cards. Voice wants two-sentence turns, spelled-out numbers, and filler phrases so
the line is never silent. The behavior is *mostly* the same — same tools, same
flow, same business rules — but the surface differs.

The naive answer is to **fork**: maintain `bella_notte_chat/` and
`bella_notte_voice/` as two full projects. They immediately drift. A bug fixed in
one isn't fixed in the other; a tool added to one is forgotten in the other.

**Polymorphism** is the alternative:

> **Author the agent once at the center, describe the per-channel _deltas_
> declaratively, and _compile_ a complete, channel-optimized project for each
> target.**

The polymorphism happens entirely at **build time**. There is no special
"polymorphic runtime" — what you deploy is an ordinary SCRAPI project.

```
              base project                      cxas poly build           one project per channel
  ┌───────────────────────────────┐                                  ┌──────────────────────────┐
  │ app.json · agents/ · tools/    │                                  │ output/chat/   (Markdown, │
  │ evaluations/ (channel-neutral) │  ──────────────────────────►    │   rich cards, WEB_UI)     │
  │                                │        compile + validate        ├──────────────────────────┤
  │ adapters/                      │                                  │ output/voice/  (terse,    │
  │   chat.adapter.yaml   (deltas) │                                  │   spoken, TELEPHONY)      │
  │   voice.adapter.yaml  (deltas) │                                  └──────────────────────────┘
  └───────────────────────────────┘
```

### The three primitives

| Primitive | What it is | Where it lives |
|---|---|---|
| **Canonical Agent Card** | Your ordinary, channel-neutral agent project — `app.json`, `agents/`, `tools/`, `evaluations/`. Nothing new to learn. | The project root |
| **Channel Adapter Card** | A small YAML/JSON file describing what changes for one channel: instruction edits, tool add/remove, model overrides, extra callbacks, channel evals, and deployment settings. | `adapters/<channel>.adapter.yaml` |
| **Polymorphism Engine** | The compiler. Reads the base project + adapter cards and writes one complete project directory per channel. | `cxas poly` / `cxas_scrapi.poly` |

---

## Quickstart: the Polymorphic Pizza demo

Want to see it work right now? The repository ships a small, beginner-friendly
demo — **Polymorphic Pizza** — that compiles **one** agent project into a
**chat agent** and a **voice agent**. You do **not** need a Google Cloud account
to build and inspect them.

**Prerequisites:** Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
# 1. Install the cxas CLI (once), from the repo root:
uv sync --extra dev

# 2. Validate the demo's two channel adapters:
uv run cxas poly validate --app-dir examples/polymorphic_pizza

# 3. Preview a channel delta in either human or JSON form:
uv run cxas poly diff chat --app-dir examples/polymorphic_pizza
uv run cxas poly diff chat --app-dir examples/polymorphic_pizza --json

# 4. Check pre-build launch readiness:
uv run cxas poly readiness --app-dir examples/polymorphic_pizza

# 5. Compile the base project into a chat agent and a voice agent:
uv run cxas poly build --app-dir examples/polymorphic_pizza --output-dir ./output

# 6. The output is just a normal project — lint it like any other:
uv run cxas lint --app-dir ./output/chat
uv run cxas lint --app-dir ./output/voice
```

You'll get two finished projects under `./output/`: `chat/` (Markdown plus a
visual order card, deployed to a web widget) and `voice/` (short spoken turns,
deployed to telephony) — both generated from the **same** `agents/`, `tools/`,
and `evaluations/`. Want to preview the per-channel changes before building? Run
`uv run cxas poly diff chat --app-dir examples/polymorphic_pizza` (or `voice`).
If validation fails, `uv run cxas poly doctor --app-dir examples/polymorphic_pizza`
prints the same AD rule IDs with field paths and likely fixes.

Full file-by-file walkthrough, a chat-vs-voice comparison, and what to try next:
**[examples/polymorphic_pizza/README.md](examples/polymorphic_pizza/README.md)**.

> Prefer a more advanced example with a slot-filling framework? The
> [step-by-step section below](#using-polymorphism-step-by-step) uses the larger
> Bella Notte project.

---

## Architecture at a glance

The library is organized by responsibility. Each top-level package under
`src/cxas_scrapi/` owns one concern:

| Package | Responsibility |
|---|---|
| [`core/`](src/cxas_scrapi/core) | High-level building blocks mapped to CXAS resource types — `Apps`, `Agents`, `Tools`, `Guardrails`, `Deployments`, `Sessions`, etc. The main public SDK surface. |
| [`poly/`](src/cxas_scrapi/poly) | **The polymorphism engine** — adapter-card models, validators, compiler, diffing, diagnostics, scaffolding, and readiness reports. Pure local file I/O; intentionally **GCP-free** (no `google.cloud.*` imports, no network). |
| [`cli/`](src/cxas_scrapi/cli) | The `cxas` command line. `cli/poly_cli.py` wires up `cxas poly init / build / validate / doctor / readiness / diff`. |
| [`evals/`](src/cxas_scrapi/evals) | Executing and analyzing agent evaluations — goldens, simulations, latency. |
| [`utils/`](src/cxas_scrapi/utils) | Pagination, proto/response flattening, linting, Sheets/GCS integrations. |
| [`migration/`](src/cxas_scrapi/migration) | Tools for migrating legacy Dialogflow CX agents into CXAS. |

The dependency arrow points one way: **`poly/` depends on nothing in the rest of
the SDK** (and pulls in no GCP libraries), so the compiler runs anywhere — CI, a
laptop, a sandbox — without credentials. The CLI and the rest of the SDK depend
on `poly/`, never the reverse.

```
cli/poly_cli.py ──► poly/engine.py ──► poly/models.py
                          │                  ▲
                          └──► poly/validators.py
                          (no google.cloud.*, no network)
```

---

## Inside the polymorphism engine

The `poly/` package is organized around small, local modules. Reading the
schema, validators, compiler, and readiness report top to bottom is the fastest
way to understand the whole feature:

### `poly/models.py` — the schema

Pydantic models that define the **shape of an adapter card**. The top-level
[`AdapterCard`](src/cxas_scrapi/poly/models.py) holds:

- `metadata` ([`AdapterMetadata`](src/cxas_scrapi/poly/models.py)) — `channel`,
  `displayName`, `description`.
- `instruction_diffs` (`List[InstructionDiff]`) — edits to instruction text.
- `tools` (`List[ToolModification]`) — add/remove tools on an agent.
- `tool_definitions` (`List[ToolDefinition]`) — bring channel-only tools in.
- `model_overrides` (`List[ModelOverride]`) — per-agent model swap.
- `callbacks` (`List[CallbackDefinition]`) — channel-specific callbacks.
- `evaluations` (`List[EvalReference]`) — extra eval directories to merge.
- `gecx_config` (`Dict[str, Any]`) — channel-specific `gecx-config.json`
  overlay.
- `deployment` (`Optional[DeploymentOverride]`) — channel/modality/widget config.

Every model uses `populate_by_name=True`, so cards can be written in friendly
**camelCase** (`displayName`, `sectionTag`, `pythonCode`) while the Python code
reads them as snake_case. Unknown fields are rejected instead of ignored, so a
typo in an adapter cannot silently disappear. The result of a compile is a
[`CompiledAgentConfig`](src/cxas_scrapi/poly/models.py): a pure-data snapshot of
everything the engine will write to disk, so callers can introspect or transform
it before anything touches the filesystem.

### `poly/validators.py` — the safety net

Pure, local checks (`AD001`–`AD010`) that are the single source of truth — they
run inside `cxas poly validate`, back the `adapters` category of `cxas lint`, and
are invoked by `compile()` itself **before** any deltas are applied. Each returns
a list of issue dicts shaped exactly like the linter's results (`{rule_id,
severity, message, path}`). See [Validation rules](#validation-rules).

### `poly/engine.py` — the compiler

[`PolymorphismEngine`](src/cxas_scrapi/poly/engine.py) does the work in three
phases:

1. **Load** — `load_base_project()` reads `app.json`, every agent (config +
   instruction + callback code), and every tool into memory as
   `BaseProject`. `load_adapter_cards()` globs `adapters/*.adapter.{yaml,yml,json}`.
2. **Compile** — `compile(card)` deep-copies the base and applies the card's
   deltas in a fixed order (see [pipeline](#the-compilation-pipeline)), returning
   a `CompiledAgentConfig`. `compile_all()` validates and compiles every card.
3. **Write** — `write_output(compiled, dir)` materializes one complete project
   directory: untouched base files are copied verbatim; agents, `app.json`, and
   `gecx-config.json` are reconstructed; channel-only tools/evals/deployment are
   added.

### `poly/readiness.py` — the launch review

`build_readiness_report()` composes validation, compileability, diff summaries,
and eval coverage into a `poly-readiness/v1` report. It is the design-partner
review surface: run `cxas poly readiness` before `build` when you want to know
which channels are ready, which need attention, and whether channel eval names
would shadow base evals in compiled output.

---

## Using polymorphism, step by step

The repository ships a complete, runnable example — the **Bella Notte**
restaurant agent — under [`examples/bella_notte/`](examples/bella_notte). All
commands below operate on it.

### 1. Start from an ordinary project

The base project is a normal SCRAPI project — nothing about it is
polymorphism-specific. The only addition is an `adapters/` directory:

```
examples/bella_notte/
├── app.json
├── gecx-config.json
├── agents/
│   ├── Bella_Notte_Host/        # root agent: routes reservation vs. takeout
│   ├── Reservation_Agent/       # slot-filling specialist
│   └── Takeout_Agent/
├── tools/                       # set_active_flow, book_reservation, …
├── evaluations/                 # shared, channel-neutral goldens
└── adapters/                    # ← the per-channel deltas live here
    ├── chat.adapter.yaml
    └── voice.adapter.yaml
```

Write the base instructions to be **channel-neutral**: describe *what* the agent
does, not *how* it should look on a screen or sound on a call.

### 2. Scaffold a starter adapter when adding a new channel

If you are starting from an existing base app, `init` creates a valid starter
card and any referenced eval/tool/callback files:

```bash
cxas poly init \
  --app-dir examples/bella_notte \
  --channel sms \
  --deployment-target TWILIO \
  --modality VOICE_ONLY \
  --with-callback before_model
```

By default, `init` includes a starter eval so validation does not immediately
warn about missing channel coverage. Use `--dry-run` to inspect planned files or
`--no-eval` for a non-behavioral adapter. The generated content is intentionally
plain: replace it with real channel-specific instructions, evals, and runtime
hooks before shipping.

### 3. Write a channel adapter card

An adapter card is a short declaration of deltas. Here is the voice adapter,
annotated:

```yaml
apiVersion: poly.cxas.dev/v1
kind: ChannelAdapter
metadata:
  channel: voice                       # the channel name (becomes output/voice/)
  displayName: Bella Notte — Voice

# Layer voice behavior on top of the base instructions.
instructionDiffs:
  - agent: Bella_Notte_Host
    mode: append                       # append | prepend | replace_section
    content: |
      <channel_voice>
      You are speaking on a phone call. There is no screen.
      - Keep every response to two or three short sentences.
      - Spell out numbers and times ("six thirty in the evening").
      </channel_voice>

# Inject pacing hints right before the model call.
callbacks:
  - agent: Bella_Notte_Host
    type: before_model
    pythonCode: adapters/voice_callbacks/voice_pacing.py
    description: Inject voice pacing and filler-phrase hints.

# Fold channel-specific evaluations into the compiled evaluations/.
evaluations:
  - sourceDir: adapters/voice_evals

# Channel/modality settings, folded into the compiled gecx-config.json.
deployment:
  channelType: GOOGLE_TELEPHONY_PLATFORM
  modality: VOICE_ONLY
```

The chat adapter pulls the *same* base in the opposite direction — Markdown,
numbered lists, and an extra `send_rich_card` tool. See
[`chat.adapter.yaml`](examples/bella_notte/adapters/chat.adapter.yaml) for the
full version, which also demonstrates `tools` + `toolDefinitions`.

### 4. Validate before you build

```bash
cxas poly validate --app-dir examples/bella_notte
```

```
All 2 adapter card(s) valid.
```

Validation checks that every referenced agent/tool/section actually exists, that
no two cards claim the same channel, and more — see
[Validation rules](#validation-rules).

When a card is not valid yet, use the guided form:

```bash
cxas poly doctor --app-dir examples/bella_notte
# or
cxas poly validate --app-dir examples/bella_notte --explain
```

Doctor reuses the same validators, then adds what failed, why it matters, which
adapter field or referenced path to inspect, and a likely fix shape.

### 5. Preview the changes with `diff`

`diff` shows exactly what an adapter will change, **without writing anything**.
The human output is for review, and `--json` emits a stable `poly-diff/v1`
report for CI/tooling:

```bash
cxas poly diff chat --app-dir examples/bella_notte
cxas poly diff chat --app-dir examples/bella_notte --json
```

```
Channel: chat   (adapter: adapters/chat.adapter.yaml)

agents/Bella_Notte_Host
  instruction: + 7 line(s) append
    + <channel_chat>
    + You are responding in a web chat widget that renders Markdown.
    + - Use **bold** for emphasis and short numbered lists for options.
    + ...
  tools (2 -> 3):
    + send_rich_card
  callbacks: + before_model (Inject rich card formatting hints for chat confirmations.)
agents/Reservation_Agent
  instruction: + 8 line(s) append
    + <channel_chat>
    + Present available time slots as a numbered, selectable list, e.g.:
    +   1. 6:00 PM
    + ...

tools/
  + send_rich_card

evaluations/
  + 1 eval(s): Rich_Card_Confirmation

gecx-config.json (deployment)
  + channel_type: WEB_UI
  + modality: CHAT_ONLY
  + theme: LIGHT
  + web_widget_title: Bella Notte Reservations
```

### 6. Check launch readiness

Before writing compiled output, run the pre-build readiness report:

```bash
cxas poly readiness --app-dir examples/bella_notte
cxas poly readiness --app-dir examples/bella_notte --format json
```

`readiness` gives you one launch-review artifact: validation issues,
compileability, the diff summary, base/channel eval counts, duplicate eval names
that would shadow base evals in compiled output, and concrete next steps. Use
`--strict` in CI when warning-level gaps should block launch.

### 7. Build the channels

```bash
cxas poly build --app-dir examples/bella_notte --output-dir ./output
```

```
Compiled channel 'chat' -> ./output/chat
Compiled channel 'voice' -> ./output/voice

Done. 2 channel(s) written to ./output
```

### 8. Lint, evaluate, and deploy — the output is "just a project"

The single most important property of the engine: **the compiled output is
indistinguishable from a hand-authored project.** Every existing command works on
it with zero changes:

```bash
cxas lint --app-dir ./output/chat     # ✓ passes
cxas lint --app-dir ./output/voice    # ✓ passes
# run evals, deploy, etc. on ./output/<channel> exactly as usual
```

There is no half-compiled state and no special runtime to install. That is the
payoff of *author once at the center*: you maintain a single agent, and the
channel-specific surfaces fall out of two short adapter files.

---

## Adapter card reference

An adapter card declares deltas in optional sections. Each is applied to
the deep-copied base during compilation.

| Section | Field | What it does |
|---|---|---|
| **Instruction diffs** | `instructionDiffs[]` | Edit an agent's instruction text. `mode: append` / `prepend` add text; `mode: replace_section` swaps the contents of an XML-style `<sectionTag>…</sectionTag>` block. |
| **Tool modifications** | `tools[]` | `add`/`remove` tool names on a named agent's `tools` list. |
| **Tool definitions** | `toolDefinitions[]` | Bring a channel-only tool into `tools/` from a `sourceDir`. `toolType: python` brings its `<name>.json` + normalized `python_code.py`; `toolType: openapi` copies the source directory verbatim. Required when a `tools.add` references a tool not in the base. |
| **Model overrides** | `modelOverrides[]` | Set `modelSettings.model` for an agent in this channel. |
| **Callbacks** | `callbacks[]` | Append a channel-specific callback (`before_model`, `after_model`, `before_tool`, `after_tool`, `before_agent`, `after_agent`). Auto-numbered after any existing ones. |
| **Evaluations** | `evaluations[]`, `evaluationExpectations[]`, `evaluationDatasets[]` | Merge a `sourceDir` of channel-specific evaluations, expectations, and datasets into the matching output directories. |
| **Runtime config** | `gecxConfig` | Deep-merge channel-specific `gecx-config.json` defaults such as model or modality before compiler-owned fields are finalized. |
| **Deployment** | `deployment` | Fold a `deployment` block (channel type, modality, web-widget config) into `gecx-config.json` — the file deploy tooling reads — and set `default_channel`/`modality`. |

Two conveniences worth knowing:

- **Agents may be referenced by display name or directory name** — the engine
  resolves either form.
- **`replace_section` is surgical.** It only replaces the matched `<tag>…</tag>`
  block, so the rest of the instruction stays byte-for-byte intact. If most of
  your sections need `replace_section`, that's a signal the channels may want to
  be [separate agents](#when-to-use-adapters-vs-separate-agents).

---

## The compilation pipeline

`PolymorphismEngine.compile()` first **validates** the card (the same checks as
`cxas poly validate` — the single source of truth), then applies it in a fixed,
deterministic order. Understanding the order explains the output:

1. **Validate.** Run the adapter validators; if any error-severity issue is
   found, raise a `CompilationError` carrying the *full* list — nothing is
   applied. A validated card always compiles.
2. **Deep-copy the base.** Agent configs, instructions, and existing callback
   code are copied so the base is never mutated.
3. **Instruction diffs.** `append` / `prepend` / `replace_section` on each named
   agent (`replace_section` matches `<tag …>…</tag>`, attributes allowed).
4. **Tool add / remove.** Update each agent's `tools` list (adds are
   de-duplicated; removes are filtered out).
5. **Tool definitions.** Read each channel-only tool's directory; `python` tools
   normalize to `tools/<name>/python_function/python_code.py`, `openapi` tools
   are copied verbatim.
6. **Model overrides.** Set `modelSettings.model` per agent.
7. **Callbacks.** Append channel callbacks, auto-numbering after existing ones
   (e.g. a second `before_model` callback becomes `before_model_callbacks_02`).
8. **Evaluations.** Merge each channel eval / expectation / dataset directory.
9. **Runtime config.** Deep-merge `gecxConfig` into `gecx-config.json`.
10. **Deployment + gecx config.** Fold the `deployment` block into
    `gecx-config.json`; set `default_channel`, `app_dir`, and (for voice)
    `modality`.

Validation surfaces *every* problem at once: all issues are accumulated and, if
any are errors, raised together as a single `CompilationError` — the engine never
applies a partial or invalid card. Malformed adapter files are reported as
`AD001` issues (with the file name), never as a Python traceback.

---

## Driving the engine from Python

Everything the CLI does is available programmatically. The engine is GCP-free, so
this snippet runs anywhere:

```python
from cxas_scrapi.poly import PolymorphismEngine, CompilationError

engine = PolymorphismEngine("examples/bella_notte")
engine.load_base_project()
engine.load_adapter_cards()          # populates engine.adapters {channel: (card, path)}

try:
    # Compile and write every channel.
    compiled = engine.compile_all()  # {channel: CompiledAgentConfig}, validated first
    for channel, config in compiled.items():
        out_dir = engine.write_output(config, f"./output/{channel}")
        print(f"wrote {channel} -> {out_dir}")
except CompilationError as err:
    for issue in err.issues:
        print(issue["severity"], issue["rule_id"], issue["message"])
```

Because `compile()` returns a `CompiledAgentConfig` *before* anything is written,
you can inspect or post-process the compiled state — agents, instructions, merged
tools/evals, deployment — and only call `write_output()` when you're ready.

The public exports live in
[`cxas_scrapi.poly`](src/cxas_scrapi/poly/__init__.py): `PolymorphismEngine`,
`CompilationError`, `AdapterCard`, `CompiledAgentConfig`, and the individual
delta models.

---

## Validation rules

Run via `cxas poly validate` (or as the `adapters` category of `cxas lint`). The
`AD` prefix avoids collision with the `config` lint category, which owns
`A001`–`A006`.

| ID | Severity | Check |
|----|----------|-------|
| `AD001` | error | Adapter card has required fields (`apiVersion`, `kind`, `metadata.channel`), valid types, and no unknown fields (malformed cards report here, not as a traceback). |
| `AD002` | error | Every agent referenced in `instructionDiffs`, `tools`, `modelOverrides`, `callbacks` exists in `agents/`. |
| `AD003` | error | `replace_section` diffs set `sectionTag`, and a matching `<sectionTag …>…</sectionTag>` block (attributes allowed) exists in the target instruction. |
| `AD004` | warning | A tool `remove` references a tool not in the base agent's tool list. |
| `AD005` | error | A tool `add` references a tool defined neither in `tools/`, the adapter's `toolDefinitions`, nor a platform tool; or a referenced `pythonCode`/`sourceDir` is missing. |
| `AD006` | warning | The adapter declares no `evaluations` entries. |
| `AD007` | error | Two adapter cards target the same `metadata.channel`. |
| `AD008` | error | A referenced `sourceDir`/`pythonCode` path escapes the project root. |
| `AD009` | error | `deployment` `channelType`/`modality`/`theme` use known, supported values. |
| `AD010` | error | `toolDefinitions` declare a supported `toolType` (`python`, `openapi`). |

`cxas poly build --channel all` runs this validation first and writes **nothing**
if any error-severity issue is found.

---

## When to use adapters vs. separate agents

Reach for **adapters** when:

- The channels share the same core flow, tools, and business logic.
- The differences are presentational or tuning — formatting, verbosity, pacing,
  a handful of channel-only tools.
- You want a single source of truth and one place to fix bugs.

Build **separate agents** when:

- The channels have genuinely different conversation graphs or tool sets with
  little overlap.
- The "delta" would be larger than the base — at that point an adapter hides more
  than it reveals.

A good rule of thumb: if you find yourself using `replace_section` on most
sections, the channels probably want to be separate agents.

---

## Where to go next

- **[Polymorphism guide](docs/guides/polymorphism.md)** — concepts and the
  compilation model in depth.
- **[Polymorphism pattern](docs/patterns/polymorphism.md)** — a full Bella Notte
  chat-vs-voice walkthrough.
- **[`cxas poly` CLI reference](docs/cli/poly.md)** — every flag for `build`,
  `validate`, `doctor`, `readiness`, and `diff`.
- **[Examples](examples/)** — the runnable Bella Notte project and its adapters.
- **[Official docs](https://googlecloudplatform.github.io/cxas-scrapi/stable/)**
  — install, authentication, and the full core SDK.

---

## Contributing

We welcome contributions and feature requests! Fork the project, create a feature
branch, commit your changes, and open a pull request. See
[CONTRIBUTING.md](CONTRIBUTING.md) for details.

## License

Distributed under the Apache 2.0 License. See [LICENSE.txt](LICENSE.txt).

## References

- [CX Agent Studio Documentation](https://docs.cloud.google.com/customer-engagement-ai/conversational-agents/ps)
- [CX Agent Studio Console](https://ces.cloud.google.com/)
