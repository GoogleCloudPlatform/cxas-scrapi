---
name: cxas-composite-v2-voice-agent-optimizer
description: >-
  Audits, migrates, and optimizes CXAS / CES conversational voice agents for Gemini Composite V2
  (gemini-composite-v2). Covers V1-to-V2 migration (removing V1 Director's Notes, converting [tag] to
  <tag>, remapping low-performing tags, and relocating mid-sentence style tags), empty or ~12-word
  speaker-identity speaking styles, angle-bracket <...> inline non-verbal sound/pause tags, multi-part
  tone transition tags (<calm>, <empathetic>, <warm>, <positive>, <clear>, <determination>, <interest>,
  <relief>), ALL-CAPS lexical stress, ... ellipses, /IPA/ pronunciation notation, tool-scoped spoken
  text normalization, prompt contradiction checks, cross-app handoff detection, and .wav ear-audits.
---

# CXAS Composite V2 Voice Optimizer (`gemini-composite-v2`)

This skill provides an end-to-end engineering workflow, automated CLI auditor/migrator, and production reference templates to build, migrate, and optimize Google Cloud CX Agent Studio (CXAS) / Customer Engagement Suite (CES) voice agents for **Gemini Composite V2** (`gemini-composite-v2`).

> [!IMPORTANT]
> **Why Composite V1 Configurations Must Be Updated Before Switching to Composite V2:**
> Legacy **Gemini Composite V1 (`gemini-composite-v1`)** relied on long `# AUDIO PROFILE` + `# THE SCENE` + `# DIRECTOR'S NOTES` + `## Transcript:\n` prompts in `app.json` and square-bracket inline tags (`[prosody rate="85%"]`, `[uhm]`, `[short pause]`, `[sigh]`, `[positive]`, `[reassuring]`, `[neutral]`).
> In **Gemini Composite V2 (`gemini-composite-v2`)**, long Director's Notes cause the voice to drift over a call and conflict with inline tags, while square-bracket tags (`[tag]`) and mid-sentence style tags are read aloud far more often than sentence-opening angle-bracket tags (`<tag>`). **Remove V1 Director's Notes, migrate `[...]` tags to `<...>`, remap deprecated tags, and add tool-scoped text normalization before switching to `gemini-composite-v2`.**

______________________________________________________________________

## ⚠️ Fundamental Rule: Non-Destructive Preservation of Existing Instructions

**CRITICAL — DO NOT REMOVE EXISTING INSTRUCTIONS:** When migrating or optimizing an agent for Gemini Composite V2, you MUST NOT delete, remove, or strip existing business logic, domain instructions, taskflows, steps, or operational rules from `global_instruction.txt`, `agents/*/instruction.txt`, or tool docstrings.

All adaptations must be strictly **additive and non-destructive**:

1. **Preserve Full Domain Instruction Sets:** Retain all existing domain instructions, guardrails, step transitions, and business logic verbatim.
1. **Replace Only Legacy V1 Voice Markup:** Remove V1 `# Audio Profile` / `# Director's note` blocks in `app.json` (`synthesizeSpeechConfigs.<locale>.instruction`) in favor of an empty speaking style (`""`), a ~12-word speaker-identity profile, or `"warm, friendly, natural, professional"`, and migrate inline `[...]` audio tags to Composite V2 `<...>` tags.
1. **Rephrase Prohibited Platform XML Tags Without Deleting Logic:** If any prohibited platform XML tags (`<state_update>`, `<context>`, `<reasoning>`, `<thought>`, `<internal>`, `<call_tool>`, `<parameter_update>`, `<variable_update>`, `<voice_lock>`, `<voice_output>`, `<state>`, `<transition>`, `<transitions>`) appear in instructions, rephrase the tag names into plain natural language or standard XML tags without deleting the surrounding rules.

______________________________________________________________________

## Key Differences: Gemini Composite V1 vs. Gemini Composite V2

| Dimension                                                                      | Legacy Composite V1 (`cxas-composite-voice-agent-optimizer`)                                                     | Composite V2 (`cxas-composite-v2-voice-agent-optimizer`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| :----------------------------------------------------------------------------- | :--------------------------------------------------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Model Identifier**                                                           | `"model": "gemini-composite-v1"`                                                                                 | **`"model": "gemini-composite-v2"`** (with `"temperature": 1.0`)                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| **`app.json` Speaking Style (`synthesizeSpeechConfigs.<locale>.instruction`)** | Multi-paragraph `# AUDIO PROFILE` + `# THE SCENE` + `# DIRECTOR'S NOTES` + trailing `## Transcript:\n` hook      | **Remove V1 Director's Notes.** Leave empty (`""`), use a **~12-word speaker profile** describing **who the speaker is** (age, vocal timbre, pitch, regional accent, baseline cadence — e.g., `"Friendly American woman in her early 30s, warm mid-pitch voice, relaxed conversational pace"`), or use the concise 4-word style `"warm, friendly, natural, professional"`.                                                                                                                                                 |
| **Inline Non-Verbal Audio Tags**                                               | Square brackets: `[short pause]`, `[medium pause]`, `[sigh]`, `[uhm]`, `[laughs]`, `[prosody rate="66%"]`        | **Angle brackets (`<...>`) ONLY — zero square brackets (`[...]`) and zero closing tags (`</...>`)**:<br>• **Breathing:** `<breath>`, `<heavy breath>`, `<exhales>`, `<pant>`, `<sigh>`, `<sighs>`<br>• **Laughter:** `<laugh>`, `<chuckle>`, `<chuckles>`, `<giggle>`, `<snicker>`, `<cackle>`<br>• **Reactions:** `<gasp>`, `<cough>`, `<sneeze>`, `<throat-clearing>`, `<snort>`, `<tsk>`, `<argh>`<br>• **Sounds:** `<sob>`, `<cry>`, `<whimper>`, `<groan>`, `<yawn>`<br>• **Pauses:** `<short pause>`, `<long pause>` |
| **Multi-Part Tone & Style Transitions**                                        | Square-bracket `[positive]`, `[neutral]`, `[reassuring]`, `[curiosity]`, `[professional]`, `[cautious]`          | **Sentence-opening tone tags in angle brackets**: `<calm>`, `<empathetic>`, `<warm>`, `<positive>`, `<enthusiasm>`, `<clear>`, `<determination>`, `<interest>`, `<relief>` (plus supported `<sympathetic>`). A tone tag is a point marker that stays in effect until the next tone tag; never write closing tags (`</warm>`, `</prosody>`).                                                                                                                                                                                |
| **Tag Remapping (V1 → V2)**                                                    | Used `[reassuring]`, `[curiosity]`, `[professional]`, `[cautious]`, `[happy]`, `[hope]`, `[laughs]`, `[neutral]` | Automatically remaps low-performing tags: `reassuring` → `<empathetic>`, `curiosity` → `<interest>`, `professional` → `<clear>`, `cautious`/`alarm`/`nervousness` → `<calm>`, `happy`/`admiration` → `<positive>`, `hope`/`adoration` → `<warm>`, `crying` → `<sympathetic>`, `laughs` → `<chuckles>`, `annoyance`/`aggression` → `<determination>`, `medium pause` → `<short pause>`, and deletes `<neutral>` / `[uhm]`.                                                                                                  |
| **Emphasis, Hesitation & Spoken Text Normalization**                           | Quantitative `[prosody rate="64%"]` / `[uhm]` tags; unscoped number rules                                        | • **ALL CAPS** on 1–2 key words per turn for word stress (`NO outages`, `SOLID green`)<br>• **Ellipsis (`...`)** for natural hesitation (~250ms beat); **never use `[uhm]`**<br>• **IPA spelling in slashes** (`/niːv/`) for tricky names<br>• **Tool-scoped spoken normalization** (`<instruction id="text normalization for speech">` with an explicit **`Scope:`** guard so normalized words are spoken to the caller but raw values like `"1234"` are passed to tools).                                                |

______________________________________________________________________

## Master Composite V2 Optimization Checklist

Run through this prioritized checklist (P0 → P1 → P2) whenever auditing, migrating from V1, or building a Composite V2 voice agent:

### 🔴 Priority P0: Critical Composite V2 Synthesis & Migration Blockers

- [ ] **P0.1 (`app.json` & Agent Model ID, Temperature & Speaking Style):**
  - `modelSettings` in `app.json` is set to `{"model": "gemini-composite-v2", "temperature": 1.0}`, and any agent-level `modelSettings.model` override in `agents/*/*.json` is also upgraded to `"gemini-composite-v2"`.
  - Every declared language (`defaultLanguageCode` + `supportedLanguageCodes`) has a matching entry in `audioProcessingConfig.synthesizeSpeechConfigs` with `"speakingRate": 1.0` and a `*-Chirp3-HD-*` (or custom brand) voice.
  - **Zero V1 Director's Notes:** `synthesizeSpeechConfigs.<locale>.instruction` contains **no** `# Audio Profile`, `# The Scene`, `# Director's note`, or `## Transcript:` headers. Instead, leave `instruction` empty (`""`), use a **~12-word speaker profile** focused on who the speaker is (age, vocal timbre, pitch, regional accent, baseline cadence), or use `"warm, friendly, natural, professional"`.
- [ ] **P0.2 (Angle-Bracket `<...>` Tag Syntax, Zero `[...]` Tags & Zero Closing Tags):**
  - `<instruction id="audio tag format">` (or equivalent audio tag section) is defined in `global_instruction.txt` specifying angle-bracket non-verbal sound/pause tags and sentence-opening tone tags.
  - Zero legacy square-bracket tags (`[prosody...]`, `[uhm]`, `[short pause]`, `[medium pause]`, `[sigh]`, `[positive]`, `[neutral]`, `[reassuring]`, `[slow]`, `[whispers]`, `[seriousness]`) remain anywhere in `global_instruction.txt`, `agents/*/instruction.txt`, `tools/*/python_function/python_code.py`, `tools/*/*.json`, or callbacks.
  - Zero closing audio tags (`</prosody>`, `</warm>`, `</empathetic>`, `</calm>`, `</positive>`) exist anywhere in instructions or examples. Composite V2 inline tags are point markers that stay in effect until the next tag.
- [ ] **P0.3 (Prohibited Platform XML Tags & Template Variable Registration):**
  - Zero prohibited XML tags (`<state_update>`, `<context>`, `<reasoning>`, `<thought>`, `<internal>`, `<voice_lock>`, `<voice_output>`) in instructions (`I015`).
  - Every `{variable}` referenced in instructions is declared in `app.json` `variableDeclarations` (`V104`).

### 🟡 Priority P1: Speaking Style Design, Tag Remapping, Placement, Tool-Scoped Normalization & Contradiction Check

- [ ] **P1.1 (Suggested Speaking Style Design — Who the Speaker Is, Not Temporary Emotion):**
  - Start simple: test with an empty speaking style first, or use a short description of **about a dozen words** (≤ 25 words max) describing **age, vocal timbre, pitch, regional accent, and baseline cadence** (e.g., `"Friendly American woman in her early 30s, warm mid-pitch voice, relaxed conversational pace"`, `"Calm man in his 40s, steady low baritone, measured and unhurried delivery"`).
  - Never put temporary emotions in `synthesizeSpeechConfigs.<locale>.instruction`; use inline angle-bracket tags for moment-to-moment emotion.
- [ ] **P1.2 (Tag Remapping, Placement, Sparsity & Anti-Collision Rules):**
  - **Remap low-performing V1 tags:** Replace `<reassuring>` → `<empathetic>`, `<curiosity>` → `<interest>`, `<professional>` → `<clear>`, `<cautious>` / `<alarm>` / `<nervousness>` → `<calm>`, `<happy>` / `<admiration>` → `<positive>`, `<hope>` / `<adoration>` → `<warm>`, `<crying>` → `<sympathetic>`, `<laughs>` → `<chuckles>`, `<annoyance>` / `<aggression>` → `<determination>`, `<medium pause>` → `<short pause>`, and remove `<neutral>` and `[uhm]`. Deduplicate any tag lists where a remap creates duplicate items (`<empathetic>, <warm>, <reassuring>` → `<empathetic>, <warm>`).
  - **Less is more:** 1–2 tags per turn are enough for most customer conversations; most routine turns need **no sound tag at all**, and avoid sound tags on Turn 0 greeting unless using a natural `<breath>` after an opening phrase (`"Thanks for calling! <breath> I can help you track that order."`).
  - **Sentence-start sound & tone tags:** Put non-verbal sound tags (`<breath>`, `<exhales>`, `<sigh>`, `<laugh>`, `<chuckle>`, `<gasp>`) and multi-part tone transition tags (`<calm>`, `<empathetic>`, `<warm>`, `<positive>`, `<enthusiasm>`, `<clear>`, `<determination>`, `<interest>`, `<relief>`) at the **start of a sentence**. Mid-sentence style tags are read aloud far more often than sentence-opening tags.
  - **Multi-part tone transitions:** Use sentence-opening tone tags to shift delivery mid-response; a tone tag stays in effect until the next tone tag (e.g., `"<calm><empathetic> I'm so sorry your flight was canceled. That's a stressful way to start a trip. <positive> The good news is that I've already rebooked you on the next flight out."`).
  - **Never use `<sigh>`, `<sighs>`, or `<exhales>` on call-ending or escalation turns:** While fine in an empathetic moment, never emit `<sigh>`, `<sighs>`, or `<exhales>` on a turn that ends the session or escalates/transfers the call.
  - **Mid-sentence pause tags after punctuation:** Place `<short pause>` or `<long pause>` at sentence start or mid-sentence **after a comma or full stop** (`"I checked your account, <short pause> everything is in order."`). Never place a pause tag inside a phrase without preceding punctuation (`now <long pause>` → `now, <long pause>`).
  - **No `... <short pause>` double-pause collision:** Never stack `<short pause>` or `<long pause>` immediately after an ellipsis (`...`); use either `...` for a light hesitation or `", <short pause>"` / `". <short pause>"`, never both together.
  - **No ALL-CAPS word immediately after `<exhales>` or `<sigh>`:** Let the first word after `<exhales>` or `<sigh>` be normal case and put ALL-CAPS stress later in the clause (e.g., `"<exhales> Solid green on the status light means you're ONLINE."`, never `"<exhales> SOLID green..."`). Watch for initial pronoun (`"I"` / `"We"`) clipping after intense `<exhales>` or `<sighs>`.
  - **Keep tags in English across all languages:** Always write `<breath>`, `<sigh>`, `<short pause>`, `<calm>`, `<empathetic>`, `<positive>`, etc. in English keywords even when the agent speaks Spanish, Japanese, Hindi, Mandarin, or Korean.
- [ ] **P1.3 (Emphasis, Hesitation, IPA Pronunciation & Tool-Scoped Spoken Text Normalization):**
  - **1–2 ALL-CAPS stress words per turn** on words a human advisor naturally leans on (`NO outages`, `SOLID green`, `FORTY-TWO gigs`).
  - **Ellipses (`...`)** for natural hesitation and conversational beats.
  - **IPA in slashes (`/IPA/`)** for tricky names or specialized terms (e.g., `/niːv/`).
  - **Tool-Scoped Spoken Normalization (`<instruction id="text normalization for speech">`):** Include spoken normalization rules for currencies, dates, times, alphanumeric IDs, postal codes, and paired quota figures (`"FORTY-TWO of your hundred gigs"` instead of `"42 of your 100 gigs"`), **AND include the mandatory `Scope:` guard** stating that normalization applies **ONLY to words spoken aloud to the user and NEVER to tool calls, tool arguments, or internal matching** (so tools receive `"1234"` and `"XED1234"`, never `"one two three four"`).
- [ ] **P1.4 (Conversational Pacing, Pre-Tool Bridges & Concise Turn Length):**
  - Keep turns concise (typically **2–3 sentences, ≤ 45 words, single paragraph** with no bullet lists, markdown, or emojis) and share at most 2 facts after a tool call before checking in.
  - Use natural spoken contractions (`"I'm"`, `"you've"`, `"it's"`, `"that's"`, `"there's"`, `"can't"`, `"won't"`, `"let's"`).
  - Speak a short 2–5 word pre-tool bridge in the caller's language before latency-sensitive tool calls, avoid echoing the same noun phrase in the post-tool opening, and connect with a soft conversational opener (`"Okay, so..."`, `"Right, so..."`).
- [ ] **P1.5 (Mandatory Prompt Contradiction Check & Explicit Agent Overrides):**
  - Check tag rules and normalization rules in two passes: (1) within `global_instruction.txt`, every rule against every other; (2) `global_instruction.txt` against each sub-agent (`agents/*/instruction.txt`) and its tool instructions/docstrings.
  - Remove leftover V1 prose rules that contradict Composite V2 (`"wrap tags in square brackets"`, `"never stack tags"`, `"do not bunch all tags at the front of a sentence"`, blanket `"no XML/angle-bracket markup in responses"`, or rules requiring closing tags).
  - Resolve conflicting spoken number/date/currency formats (e.g., a blanket `"read every number digit by digit"` vs. reading plain quantities as cardinals and reserving digit-by-digit for IDs, account numbers, reference codes, and postal codes).
  - **Explicit Agent Overrides:** Sub-agents may intentionally override global rules by stating so explicitly (`"In this agent, these rules override the global Affective Delivery and Voice Guidelines"`). Keep explicit overrides intact, convert their tags to `<...>`, and document them in your summary.

### 🟢 Priority P2: Callbacks, Web Widget Sanitization, Cross-App Handoffs & Ear-Audit Verification

- [ ] **P2.1 (`after_model_callback` Safety Net for Composite V2):**
  - Preserves all Composite V2 angle-bracket sound, pause, and tone tags via `INLINE_TAG` regex so deterministic text normalization (like English contractions) never mutates tag contents.
  - Injects a least-recently-used holding bridge if the model calls a lookup tool in silence.
  - Guarantees spoken empathetic transfer confirmation precedes human-transfer tool calls and delays `end_session` until the transfer tool returns.
- [ ] **P2.2 (Tool Docstring Pacing & Web Portal Streaming Tag Sanitizer):**
  - Python tool docstrings include `"Before calling this tool, speak a brief pacing phrase..."`, `When to Call:`, and `When NOT to Call:` (for latency-sensitive tools approved by the developer).
  - Web client / chat widget strips all Composite V2 angle-bracket sound, pause, and tone tags (plus partial streaming `<...` tokens) from rendered transcript bubbles while preserving audio synthesis.
- [ ] **P2.3 (Cross-App Hand-Off Identification):**
  - Identify and report any cross-app or external hand-offs (`transferToNga` to another CES app, `transferToDialogflow` to a Dialogflow CX agent, `telephonyTransferCall`, `liveAgentHandoff`). Note that a transferred destination runs its own separate session with its own voice, TTS model, speaking style, and prompts, and needs its own Composite V2 migration.
- [ ] **P2.4 (Local E2E Audio Simulation & Turn-by-Turn `.wav` Ear-Audit):**
  - Run `cxas eval run-sim` with audio input/output enabled, extract per-turn `.wav` files, listen to the generated audio, and score against the 6-dimension Human-Likeness Rubric (target ≥ 9.0 / 10).

______________________________________________________________________

## How to Use This Skill (3 Operational Modes)

### Mode 1: Audit & Prioritized Composite V2 Readiness Report

Run the automated Composite V2 auditor to inspect `app.json`, `global_instruction.txt`, `agents/*/instruction.txt`, `agents/*/*.json`, `tools/`, and `after_model_callbacks/` (including cross-app hand-off detection):

```bash
python3 .agents/skills/cxas-composite-v2-voice-agent-optimizer/scripts/audit_agent_v2.py \
  --workspace="<PATH_TO_CXAS_APP_DIR>" \
  --report
```

### Mode 2: Automated + Semantic Composite V2 Migration & Remediation

1. **Run Automated In-Place Migration & Remediation** (`--remediate` upgrades `app.json` and agent overrides to `"model": "gemini-composite-v2"` with `"temperature": 1.0`, replaces legacy V1 `# Audio Profile` / `# Director's note` blocks while preserving valid ~12-word V2 speaker profiles or empty styles, ensures `*-Chirp3-HD-*` voices across declared locales, converts `[tag]` → `<tag>` while skipping placeholders and control-logic labels, remaps low-performing tags and deduplicates tag lists, inserts commas before mid-sentence pause tags inside utterances, relocates mid-sentence style tags in quoted utterances to sentence start, and strips forbidden closing audio tags):
   ```bash
   python3 .agents/skills/cxas-composite-v2-voice-agent-optimizer/scripts/audit_agent_v2.py \
     --workspace="<PATH_TO_CXAS_APP_DIR>" \
     --remediate
   ```
1. **Apply Semantic Prompt & Callback Optimizations** using the canonical templates in [`references/natural_speech_patterns_v2.md`](references/natural_speech_patterns_v2.md):
   - Add `<instruction id="audio tag format">` (covering V2 non-verbal sound/pause tags and multi-part `<calm>`, `<empathetic>`, `<warm>`, `<positive>`, `<clear>`, `<determination>`, `<interest>`, `<relief>` tone tags) and `<instruction id="text normalization for speech">` (including the mandatory **`Scope:`** tool-call guard and `/IPA/` pronunciation notation) to `global_instruction.txt`.
   - Refactor `<persona>`, `<Affective Delivery and Voice Guidelines>`, `<response_protocol>`, and `<taskflow>` examples non-destructively to enforce concise single-paragraph turns, 1–2 ALL-CAPS stress words, pre-tool bridges + post-tool soft connectors, and clean tag placement.
   - Run the **Contradiction Check** across `global_instruction.txt`, `agents/*/instruction.txt`, and tool instructions to resolve any conflicting tag or number-reading rules.
   - Add or update `after_model_callback` for V2 tag preservation, contractions, silent-lookup bridge injection, and warm-transfer speech ordering.
1. **Run Verification & Structural Linter:**
   ```bash
   python3 .agents/skills/cxas-composite-v2-voice-agent-optimizer/scripts/audit_agent_v2.py \
     --workspace="<PATH_TO_CXAS_APP_DIR>" \
     --report

   cxas lint --app-dir "<PATH_TO_CXAS_APP_DIR>"
   ```
1. **Push & Snapshot Version on CXAS:**
   ```bash
   cxas push --app-dir "<PATH_TO_CXAS_APP_DIR>"
   cxas versions create --app-name "<APP_ID>" --display-name "vX.Y-composite-v2" --description "Optimized for Gemini Composite V2"
   ```

### Mode 3: Local E2E Audio Simulation & Ear-Audit Loop

After static checks pass, verify actual synthesized voice quality on an E2E multi-turn simulation:

1. **Run Local Audio Simulation:**
   ```bash
   cxas eval run-sim "<PATH_TO_SIMULATIONS_YAML>" \
     --app-dir "<PATH_TO_CXAS_APP_DIR>" \
     --scenario "<SCENARIO_NAME>" \
     --output "<RESULTS_JSON_PATH>"
   ```
1. **Extract & Inspect Turn-by-Turn Audio (`.wav`)**:
   - Inspect each turn's transcript and listen to key `.wav` files directly (Greeting Turn 0, Troubleshooting Turn, Resolution Turn, Account Readout Turn, Multi-Part Empathy-to-Solution Transition Turn, Warm Transfer Turn).
1. **Score Against the 6-Dimension Composite V2 Human-Likeness Rubric** (target ≥ 9.0 / 10):
   1. **Vocal Timbre, Warmth & Speaker Consistency** (Stable persona without V1 Director's Note drift, authentic regional accent)
   1. **Inline Sound & Multi-Part Tone Tag Execution** (`<breath>`, `<exhales>`, `<sigh>`, `<short pause>`, `<calm><empathetic> ... <positive>` placed cleanly at boundaries, zero `... <short pause>` double-stutter, zero `<exhales> ALL-CAPS` bark, zero `<sigh>`/`<exhales>` on call-ending/transfer turns)
   1. **Lexical Stress (ALL-CAPS) & Rhythm** (Natural pitch lift on 1–2 capitalized words per turn without `[prosody rate]` warping)
   1. **Conversational Pacing, Bridges & Turn Length** (2–5 word varied pre-tool bridges, soft post-tool connectors, no noun echo, ≤ 45 words/turn)
   1. **Phonetic Accuracy, `/IPA/` & Alphanumeric Readout** (Accurate tracking/phone/address readout, `/IPA/` slash notation for tricky names, zero `"42%"` hallucination, raw written arguments passed to tools)
   1. **Emotional Attunement & Empathy Restraint** (Smooth multi-part tone transitions from empathy to resolution, natural warm transfer confirmation before `end_session`)

______________________________________________________________________

## Reference Documentation

- **[Composite V2 Natural Speech Patterns, Prompt Templates & Callback Reference](references/natural_speech_patterns_v2.md)**: Complete production templates for `gemini-composite-v2`, including speaking style examples by industry, full V2 inline sound/pause and multi-part tone tag reference, empirical V1-to-V2 tag replacement table, tool-scoped text normalization block, contradiction & cross-app hand-off checklist, `after_model_callback`, web widget tag-stripping regexes, and the acoustic troubleshooting table.
