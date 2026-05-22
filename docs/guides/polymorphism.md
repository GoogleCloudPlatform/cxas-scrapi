---
title: Polymorphism
description: Author one agent project at the center and compile channel-optimized variants (chat, voice, …) at the edges, using declarative channel adapter cards.
---

# Polymorphism

A reservation agent that works beautifully in a web chat widget rarely works well on a phone call. Chat wants Markdown, numbered lists, and rich confirmation cards. Voice wants two-sentence turns, spelled-out numbers, and filler phrases so the line is never silent. The behavior is *mostly* the same — the same tools, the same flow, the same business rules — but the surface differs.

The naive answer is to fork: maintain `bella_notte_chat/` and `bella_notte_voice/` as two full projects. They immediately start to drift. A bug fixed in one isn't fixed in the other; a new tool added to one is forgotten in the other.

**Polymorphism** is the alternative: author the agent **once** at the center, describe the per-channel *deltas* declaratively, and **compile** a complete, channel-optimized project for each target.

---

## The three primitives

| Primitive | What it is | Where it lives |
|---|---|---|
| **Canonical Agent Card** | Your existing, channel-neutral agent project — `app.json`, `agents/`, `tools/`, `evaluations/`. Nothing new to learn. | The project root |
| **Channel Adapter Card** | A small YAML/JSON file describing what changes for one channel: instruction additions, tool add/remove, model overrides, runtime config, extra callbacks, channel evals, and deployment settings. | `adapters/<channel>.adapter.yaml` |
| **Polymorphism Engine** | The compiler. Reads the base project + adapter cards and writes one complete project directory per channel. | `cxas poly` / `cxas_scrapi.poly` |

---

## The compilation model

> **Author once at the center, specialize at the edges, compile for each target.**

The base project stays channel-neutral. Each adapter card layers a thin set of deltas on top. The engine deep-copies the base, applies the deltas, and emits a directory that looks exactly like a hand-authored project:

```
examples/bella_notte/                 cxas poly build         output/
├── app.json                          ───────────────►        ├── chat/
├── agents/                                                    │   ├── app.json
│   ├── Bella_Notte_Host/                                      │   ├── agents/        (chat instructions)
│   └── Reservation_Agent/                                     │   ├── tools/         (+ send_rich_card)
├── tools/                                                     │   ├── evaluations/   (+ chat evals)
├── evaluations/                                               │   └── gecx-config.json (deployment: WEB_UI / CHAT_ONLY)
└── adapters/                                                  └── voice/
    ├── chat.adapter.yaml                                          ├── agents/        (voice instructions)
    └── voice.adapter.yaml                                         ├── tools/         (no chat-only tools)
                                                                   └── gecx-config.json (deployment: GTP / VOICE_ONLY)
```

Each adapter applies, in order:

1. **Instruction diffs** — `append`, `prepend`, or `replace_section` (replace an XML `<section>…</section>` block) on a named agent's instruction text.
2. **Tool modifications** — add or remove tools from an agent's `tools` list.
3. **Tool definitions** — bring channel-only tools (e.g. `send_rich_card`) into `tools/`.
4. **Model overrides** — set `modelSettings.model` per agent for the channel.
5. **Callbacks** — append channel-specific callbacks, auto-numbered after any existing ones (`before_model_callbacks_02`, …).
6. **Evaluations** — merge channel-specific evaluation directories into `evaluations/`.
7. **Runtime config** — deep-merge a `gecxConfig` block into `gecx-config.json` for channel defaults such as model or modality.
8. **Deployment** — fold a `deployment` block (channel/modality/widget settings) into `gecx-config.json` — the file deploy tooling reads — and set `default_channel`/`modality`.

---

## Starting a new adapter

Use `cxas poly init` when you already have a channel-neutral base app and want a
valid starter workflow for a new channel:

```bash
cxas poly init \
  --app-dir examples/polymorphic_pizza \
  --channel sms \
  --deployment-target TWILIO \
  --modality VOICE_ONLY \
  --with-callback before_model \
  --with-tool send_sms_card
```

The scaffold flow creates only supported adapter-card fields:

- `adapters/<channel>.adapter.yaml`
- a starter `adapters/<channel>_evals/` directory unless `--no-eval` is used
- optional `adapters/<channel>_callbacks/*.py` files referenced by `callbacks`
- optional `adapters/<channel>_tools/<tool>/` folders referenced by
  `toolDefinitions`

`init` refuses to overwrite existing files unless `--force` is passed. Use
`--dry-run` to see the planned files first. The generated text is a starter, not
production behavior; replace it with real channel instructions, eval assertions,
tool descriptions, and callback logic.

---

## Checking launch readiness

Run `cxas poly readiness` before building when a project is heading into CI,
design-partner review, or launch review:

```bash
cxas poly readiness --app-dir examples/bella_notte
cxas poly readiness --app-dir examples/bella_notte --format json
```

The report composes validation, compileability, diff summaries, and eval
coverage. Each channel is marked `ready`, `attention`, or `blocked`, with next
steps for missing channel evals, duplicate eval names that would shadow base
items, or blocking AD rule issues. Use `--strict` when warnings should block the
workflow.

---

## Why the output is "just a project"

The single most important property of the engine: **the compiled output is indistinguishable from a hand-authored SCRAPI project.** Every base file appears in the output with its modifications applied; nothing is left in a half-compiled state.

That means all existing tooling works on the output with **zero changes**:

```bash
cxas poly build --app-dir examples/bella_notte --output-dir ./output
cxas lint  --app-dir ./output/chat     # ✓ passes
cxas lint  --app-dir ./output/voice    # ✓ passes
# run evals, deploy, etc. on ./output/<channel> exactly as usual
```

There is no special "polymorphic runtime." The polymorphism happens entirely at build time. What you deploy is an ordinary project.

---

## When to use adapters vs. separate agents

Reach for **adapters** when:

- The channels share the same core flow, tools, and business logic.
- The differences are presentational or tuning (formatting, verbosity, pacing, a handful of channel-only tools).
- You want a single source of truth and a single place to fix bugs.

Build **separate agents** when:

- The channels have genuinely different conversation graphs or tool sets with little overlap.
- The "delta" would be larger than the base — at that point an adapter hides more than it reveals.

A good rule of thumb: if you find yourself using `replace_section` on most sections, the channels probably want to be separate agents.

---

## Next steps

- **[Polymorphism Pattern](../patterns/polymorphism.md)** — a step-by-step walkthrough of the Bella Notte chat and voice adapters.
- **[`cxas poly` CLI reference](../cli/poly.md)** — `init`, `build`,
  `validate`, `doctor`, `readiness`, and `diff --json`.
