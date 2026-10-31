---
name: cxas-composite-v2-voice-agent-optimizer
description: >-
  Audits, migrates, and optimizes CXAS / CES conversational voice agents for Gemini Composite V2
  (gemini-composite-v2 / 3.8 TTS) with prebuilt Chirp 3 HD voices (defaulting to Aoede,
  *-Chirp3-HD-Aoede) or custom cloned voices (customVoiceSamples).
  Covers V1-to-V2 migration (clearing V1 Director's Notes, converting [tag] to <tag>, remapping
  low-performing tags, and relocating mid-sentence style tags), ~12-word vertical speaker-identity
  speaking styles (or empty style + NO_INSTRUCTION for custom voices), angle-bracket <...> sound/pause
  and sentence-opening tone transition tags, optional <prosody rate> pacing, ALL-CAPS lexical stress,
  ... ellipses, /IPA/ pronunciation, tool-scoped spoken text normalization, multi-language naturalness,
  prompt contradiction checks, cross-app handoff detection, and .wav ear-audits.
  Use when an app targets or migrates to gemini-composite-v2 (3.8 TTS). Don't use for gemini-composite-v1
  tuning (use cxas-composite-voice-agent-optimizer).
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
1. **Replace Only Legacy V1 Voice Markup:** Clear V1 `# Audio Profile` / `# Director's note` blocks in `app.json` (`synthesizeSpeechConfigs.<locale>.instruction` and `customVoiceSamples[].voiceInstruction`) and replace them with a ~12-word speaker profile describing **who the speaker is** (age, vocal timbre, pitch, accent, pace — adapted from the vertical industry examples in `references/natural_speech_patterns_v2.md` §1), or leave `""` as a secondary alternative (`--remediate` clears legacy blocks to `""` and prints the removed text so the agent/developer can craft the ~12-word profile). Migrate inline `[...]` audio tags to Composite V2 `<...>` tags, keeping every original `prosody rate` value exactly as written.
1. **Rephrase Prohibited Platform XML Tags Without Deleting Logic:** If any prohibited platform XML tags (listed in P0.3 / `I015`) appear in instructions, rephrase the tag names into plain natural language or standard XML tags without deleting the surrounding rules.

______________________________________________________________________

## Key Differences: Gemini Composite V1 vs. Gemini Composite V2

| Dimension              | Legacy Composite V1                                       | Composite V2 (`gemini-composite-v2`)                                                                                                                                                                                                                      |
| :--------------------- | :-------------------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Model ID**           | `"model": "gemini-composite-v1"`                          | **`"model": "gemini-composite-v2"`** (temperature and `speakingRate` are not V2 requirements).                                                                                                                                                            |
| **Speaking Style**     | `# AUDIO PROFILE` + `# THE SCENE` + `# DIRECTOR'S NOTES`  | **Prebuilt (`*-Chirp3-HD-*`, default `Aoede`):** ~12-word speaker profile (`""` secondary). **Custom (`customVoiceSamples[]`):** `""` + `"voiceInstructionMode": "NO_INSTRUCTION"`.                                                                       |
| **Sound & Pause Tags** | Square brackets (`[short pause]`, `[sigh]`, `[uhm]`)      | **Angle brackets (`<...>`) ONLY — zero `[...]` and zero `</...>`**: `<breath>`, `<exhales>`, `<sigh>`, `<laugh>`, `<chuckle>`, `<gasp>`, `<throat-clearing>`, `<short pause>`, `<long pause>`, optional `<prosody rate="N%">`.                            |
| **Tone Transitions**   | Square-bracket `[positive]`, `[neutral]`, `[reassuring]`  | **Sentence-opening tone tags**: `<calm>`, `<empathetic>`, `<warm>`, `<positive>`, `<enthusiasm>`, `<clear>`, `<determination>`, `<interest>`, `<relief>`, `<sympathetic>`.                                                                                |
| **Tag Remapping**      | `[reassuring]`, `[curiosity]`, `[professional]`, `[slow]` | Remaps `reassuring` → `<empathetic>`, `curiosity` → `<interest>`, `professional` → `<clear>`, `medium pause` → `<short pause>`, `slow` → `<prosody rate="65%">`, drops `<neutral>` / `[uhm]` (see P1.2 & `references/natural_speech_patterns_v2.md` §2D). |
| **Emphasis & Norm.**   | `[prosody rate=...]`; unscoped number rules               | **ALL CAPS** (1–2 words/turn in Latin scripts), **`...`** hesitation, **`/IPA/`** in slashes, and **tool-scoped normalization** (`<instruction id="text normalization for speech">` with `Scope:` guard).                                                 |

______________________________________________________________________

## Master Composite V2 Optimization Checklist

Run through this prioritized checklist (P0 → P1 → P2) whenever auditing, migrating from V1, or building a Composite V2 voice agent:

### 🔴 Priority P0: Critical Composite V2 Synthesis & Migration Blockers

- [ ] **P0.1 (`app.json` & Agent Model ID, Locale Speech Configs, Custom Voice Samples & Speaking Style):**
  - `modelSettings.model` in `app.json` is `"gemini-composite-v2"`, and any agent-level `modelSettings.model` override in `agents/*/*.json` that still points to an older Composite model is upgraded too. `--remediate` upgrades only an older Composite model or a missing model; any other model is reported for the app owner to decide.
  - Temperature and `speakingRate` are **not** Composite V2 requirements: the auditor reports temperature only as a P2 advisory, and `--remediate` never changes temperature or `speakingRate`.
  - Every declared language (`defaultLanguageCode` + `supportedLanguageCodes`) has a matching entry in `audioProcessingConfig.synthesizeSpeechConfigs` with a `voice` set (defaulting to `<locale>-Chirp3-HD-Aoede` whenever prebuilt voices are used and no existing Chirp 3 HD persona is configured, and with no deprecated inline `voiceSampleGcsUri`, reported as `DEPRECATED_INLINE_VOICE_SAMPLE_URI`).
  - **Custom Cloned Voice Sample Completeness (`customVoiceSamples[]`):** When `audioProcessingConfig.customVoiceSamples` is used, every entry must set valid `gs://...` URIs for **both** `voiceSampleGcsUri` and `consentAudioGcsUri` (`CUSTOM_VOICE_MISSING_GCS_URI`; missing either causes CES to silently fall back to a prebuilt voice at runtime) and set `"voiceInstructionMode": "NO_INSTRUCTION"` (`CUSTOM_VOICE_INSTRUCTION_MODE_NOT_DISABLED`, auto-fixed by `--remediate` so `cxas push` never auto-generates an `<agent_voice>` DSP block or restores a cached `priorVoiceInstruction` from Spanner).
  - **Zero V1 Director's Notes:** `synthesizeSpeechConfigs.<locale>.instruction` and `customVoiceSamples[].voiceInstruction` contain **no** `# Audio Profile`, `# The Scene`, `# Director's note`, `## Transcript:`, or `<agent_voice>` blocks. `--remediate` clears them to `""` (and sets `"voiceInstructionMode": "NO_INSTRUCTION"` on custom voices) and prints the removed text; `--remediate` never injects a style automatically.
- [ ] **P0.2 (Angle-Bracket `<...>` Tag Syntax, Zero `[...]` Tags, Zero Translated Tags & Zero Closing Tags):**
  - Zero legacy square-bracket tags (`[prosody...]`, `[uhm]`, `[short pause]`, `[medium pause]`, `[sigh]`, `[positive]`, `[neutral]`, `[reassuring]`, `[slow]`, `[whispers]`, `[seriousness]`) remain anywhere in `global_instruction.txt`, `agents/*/instruction.txt`, `tools/*/python_function/python_code.py`, `tools/*/*.json`, or callbacks.
  - Zero translated non-English audio tags (`<pausa corta>`, `<empático>`, `<短暂停顿>`, `<छोटा विराम>`, etc.; `TRANSLATED_AUDIO_TAG`, P0) exist in instructions or examples. Composite V2 only recognizes English tag keywords and reads translated tags aloud; `--remediate` converts them to canonical English `<...>` tags.
  - Zero closing audio tags (`</prosody>`, `</warm>`, `</empathetic>`, `</calm>`, `</positive>`) exist anywhere in instructions or examples. Composite V2 inline tags are point markers that stay in effect until the next tag. `--remediate` removes closing tags, and rewrites a `</prosody>` that is followed by more speech as `<prosody rate="100%">`.
- [ ] **P0.3 (Prohibited Platform XML Tags & Template Variable Registration):**
  - Zero prohibited XML tags (`<state_update>`, `<context>`, `<reasoning>`, `<thought>`, `<internal>`, `<call_tool>`, `<parameter_update>`, `<variable_update>`, `<voice_lock>`, `<voice_output>`, `<state>`, `<transition>`, `<transitions>`) in instructions (`I015`).
  - Every `{variable}` referenced in instructions is declared in `app.json` `variableDeclarations` (`V104`), including `{user_language}` from the canonical template (`{current_date}` is built in).

### 🟡 Priority P1: Voice Persona, Speaking Style, Tag Remapping, Placement, Tool-Scoped Normalization & Contradiction Check

- [ ] **P1.1 (Voice Persona, Multi-Locale Enablement & Speaking Style Design — Prebuilt vs. Custom Cloned Voices):**
  - **Voice persona & multi-locale enablement (`Aoede` default for prebuilt voices):** If the **prebuilt voice type** is chosen instead of a custom cloned voice (`customVoiceSamples[]`)—including new apps, missing/empty `synthesizeSpeechConfigs` or `voice` fields, migrations from legacy non-Chirp voices (`NON_CHIRP3_HD_VOICE`), or switching off a custom voice—**`Aoede` (`<locale>-Chirp3-HD-Aoede`, e.g., `en-US-Chirp3-HD-Aoede`, `en-AU-Chirp3-HD-Aoede`) MUST be used as the default voice persona across all declared locales**, unless the app already configures another valid `*-Chirp3-HD-*` persona the owner wants to keep or the user explicitly requests a different Chirp 3 HD voice (such as `Puck` or `Kore`). Keep the same voice persona across every locale and verify that the voice exists for each locale. Non-Chirp voices not in `customVoiceSamples[]` are reported (`NON_CHIRP3_HD_VOICE`) and only replaced when confirmed (`--remediate --prebuilt-voice` applies `<locale>-Chirp3-HD-Aoede` across all declared locales). When `languageSettings.supportedLanguageCodes` declares additional locales, `languageSettings.enableMultilingualSupport` must be `true` (`MULTILINGUAL_SUPPORT_NOT_ENABLED`, auto-fixed by `--remediate`). When `--remediate` adds a missing locale, it reuses the default locale's Chirp 3 HD persona (falling back to `Aoede`) or custom voice key and copies its `speakingRate`.
  - **Prebuilt Voices (`*-Chirp3-HD-*`, default `Aoede`) — Primary recommendation: ~12-word vertical speaker profile (`""` as secondary alternative):** By default, recommend a short description of **about a dozen words** (≤ 25 words max) describing **who the speaker is** (age, vocal timbre, pitch, accent, and pace), adapted from the vertical industry examples in `references/natural_speech_patterns_v2.md` §1 (**General Support**, **Retail Support**, **Banking**, **Telecom**, **Travel**) to match the app's vertical, Chirp 3 HD voice gender (feminine by default when using `Aoede`), and locale (e.g., `"Friendly American woman in her early 30s, warm mid-pitch voice, relaxed conversational pace"`). Offer an empty speaking style (`"instruction": ""`) as the secondary alternative.
  - **Custom Cloned Voices (`customVoiceSamples[]`) — Primary recommendation: `""` for both `voiceInstruction` and locale `instruction` + `"voiceInstructionMode": "NO_INSTRUCTION"`:** Because the 10–20s reference `.wav` (`voiceSampleGcsUri`) already supplies the speaker's acoustic identity (age, gender, timbre, pitch, accent) and CES concatenates `synthesizeSpeechConfigs.<locale>.instruction` + `customVoiceSamples[].voiceInstruction` into `speechMetadata.style`, any text speaker description competes with the reference audio embedding and causes speaker-similarity drift over 20+ turn calls (`CUSTOM_VOICE_INSTRUCTION_TOO_LONG`, `CUSTOM_VOICE_LOCALE_STYLE_CONFLICT`). Leave **both** fields empty (`""`), set `"voiceInstructionMode": "NO_INSTRUCTION"`, and bind the custom voice across all declared locales (`--remediate --bind-custom-voice auto`) for zero-shot cross-lingual speaker continuity. When an English custom voice speaks a Latin-script secondary language (such as Spanish `es-US`), spell English acronyms phonetically in the target language (e.g., `ene-be-ene` for `NBN`) and avoid ALL-CAPS stress on cognates (`módems`, `facturas`) so the English reference embedding does not pronounce them in English.
  - **Never copy an English-locale accent into non-English locales:** If you write a short speaker profile for `en-US` or `en-AU`, do **not** copy `"American woman..."` or `"Australian woman..."` into `es-US`, `hi-IN`, `cmn-CN`, or `ko-KR` (`LOCALE_SPEAKER_PROFILE_ACCENT_MISMATCH`), as it forces a foreign English accent onto non-English speech. Use a locale-neutral/native speaker profile (e.g., `"Woman in her early 30s, warm mid-pitch voice, relaxed conversational pace"`) or leave non-English locale `instruction` fields empty (`""`).
  - **Don't use feeling words as a speaking style:** `"warm, friendly, natural, professional"` describes how the speaker feels, not who the speaker is. For prebuilt voices, replace it with a ~12-word vertical speaker profile from `references/natural_speech_patterns_v2.md` §1 as the primary option (or `""` as the secondary option). Never put temporary emotions in `synthesizeSpeechConfigs.<locale>.instruction`; use inline angle-bracket tags for moment-to-moment emotion.
  - Styles longer than 25 words (`SPEAKING_STYLE_TOO_LONG`) are cleared to `""` by `--remediate`, which prints the removed text so you can condense it into a ~12-word vertical speaker profile for prebuilt voices.
- [ ] **P1.2 (Audio Tag Guidance, Tag Remapping, Placement, Sparsity & Anti-Collision Rules):**
  - **Audio tag guidance:** `<instruction id="audio tag format">` (or an equivalent audio tag section) is defined in `global_instruction.txt`, specifying angle-bracket non-verbal sound/pause tags and sentence-opening tone tags.
  - **Remap low-performing or non-canonical V1 tags:** Replace `<reassuring>` → `<empathetic>`, `<curiosity>` → `<interest>`, `<professional>` → `<clear>`, `<cautious>` / `<alarm>` / `<nervousness>` → `<calm>`, `<happy>` / `<admiration>` → `<positive>`, `<hope>` / `<adoration>` → `<warm>`, `<crying>` → `<sympathetic>`, `<laughs>` → `<chuckles>`, `<annoyance>` / `<aggression>` → `<determination>`, `<medium pause>` → `<short pause>`, `<slow>` / `<slower>` → `<prosody rate="65%">`, `<exhale>` → `<exhales>`, `<whisper>` → `<whispers>`, `<clears throat>` → `<throat-clearing>`, and remove `<neutral>` and `[uhm]`. Deduplicate any tag lists where a remap creates duplicate items (`<empathetic>, <warm>, <reassuring>` → `<empathetic>, <warm>`).
  - **Unknown tags — suggest, don't swap:** Tags outside the documented lists have unknown behavior. The auditor reports them as `UNEVALUATED_V2_TAG` with a suggested closest tag; swap only if the user agrees. Unrecognized bracket tokens such as `[clam]` are reported as `UNRECOGNIZED_BRACKET_TOKEN`: convert a typo of an audio tag (`[clam]` → `<calm>`), but leave a placeholder alone.
  - **Prosody is optional:** Keep every original `prosody rate` exactly as written (only the brackets change), and ask the user before adding prosody where there was none. Put prosody first in a stack (`<prosody rate="70%"><empathetic>`) and, because there are no closing tags, return to normal pace with `<prosody rate="100%">` before the words that resume it. `--remediate` reorders stacks, rewrites `</prosody>` that way when speech continues, and maps `<slow>` / `<slower>` (read aloud as words) to `<prosody rate="65%">`.
  - **Less is more:** 1–2 tags per turn are enough for most customer conversations; most routine turns need **no sound tag at all**, and avoid sound tags on Turn 0 greeting unless using a natural `<breath>` after an opening phrase (`"Thanks for calling. <breath> I can help you track that order."`).
  - **Sentence-start sound & tone tags:** Put non-verbal sound tags (`<breath>`, `<exhales>`, `<sigh>`, `<laugh>`, `<chuckle>`, `<gasp>`) and multi-part tone transition tags (`<calm>`, `<empathetic>`, `<warm>`, `<positive>`, `<enthusiasm>`, `<clear>`, `<determination>`, `<interest>`, `<relief>`) at the **start of a sentence**. Mid-sentence style tags are read aloud far more often than sentence-opening tags.
  - **Multi-part tone transitions:** Use sentence-opening tone tags to shift delivery mid-response; a tone tag stays in effect until the next tone tag (e.g., `"<calm><empathetic> I'm so sorry your flight was canceled. That's a stressful way to start a trip. <positive> The good news is that I've already rebooked you on the next flight out."`).
  - **Never use `<sigh>`, `<sighs>`, or `<exhales>` on call-ending or escalation turns:** While fine in an empathetic moment, never emit `<sigh>`, `<sighs>`, or `<exhales>` on a turn that ends the session or escalates/transfers the call; open those turns with a tone tag such as `<calm><empathetic>` instead. The auditor reports violations as `SIGH_ON_ENDING_OR_TRANSFER_TURN`.
  - **Mid-sentence pause tags after punctuation:** Place `<short pause>` or `<long pause>` at sentence start or mid-sentence **after a comma or full stop** (`"I checked your account, <short pause> everything is in order."`). Never place a pause tag inside a phrase without preceding punctuation (`now <long pause>` → `now, <long pause>`).
  - **No `... <short pause>` double-pause collision:** Never stack `<short pause>` or `<long pause>` immediately after an ellipsis (`...`); use either `...` for a light hesitation or `", <short pause>"` / `". <short pause>"`, never both together.
  - **No ALL-CAPS word immediately after `<exhales>` or `<sigh>`:** Let the first word after `<exhales>` or `<sigh>` be normal case and put ALL-CAPS stress later in the clause (e.g., `"<exhales> Solid green on the status light means you're ONLINE."`, never `"<exhales> SOLID green..."`). Watch for initial pronoun (`"I"` / `"We"`) clipping after intense `<exhales>` or `<sighs>`.
  - **Keep tags in English across all languages:** Always write `<breath>`, `<sigh>`, `<short pause>`, `<calm>`, `<empathetic>`, `<positive>`, etc. in English keywords even when the agent speaks Spanish, Japanese, Hindi, Mandarin, or Korean.
- [ ] **P1.3 (Emphasis, Hesitation, IPA Pronunciation & Tool-Scoped Spoken Text Normalization):**
  - **1–2 ALL-CAPS stress words per turn (Latin-script languages):** Put CAPITAL letters on 1–2 words per turn that a human advisor naturally leans on (`NO outages`, `SOLID green`, `FORTY-TWO gigs`). In non-Latin scripts without uppercase/lowercase (Devanagari, Hanzi, Hangul, Kana), convey emphasis through sentence-opening tone tags (`<positive>`, `<warm>`, `<empathetic>`) and natural emphatic phrasing rather than inserting Latin ALL-CAPS words.
  - **Ellipses (`...`)** for natural hesitation and conversational beats.
  - **IPA in slashes (`/IPA/`)** for tricky names or specialized terms (e.g., `/niːv/`).
  - **Tool-Scoped Spoken Normalization (`<instruction id="text normalization for speech">`):** Include spoken normalization rules and examples for currencies (including cents and large amounts), dates, times, phone numbers, percentages, URLs and emails, street addresses, ordinals, decimals, units, abbreviations, alphanumeric IDs, postal codes, and paired quota figures (`"FORTY-TWO of your hundred gigs"` instead of `"42 of your 100 gigs"`), **AND include the mandatory `Scope:` guard** stating that normalization applies **ONLY to words spoken aloud to the user and NEVER to tool calls, tool arguments, or internal matching** (so tools receive `"1234"` and `"XED1234"`, never `"one two three four"`). The examples are the instruction, so keep the full list; swap currency examples to the local currency, adjust date order for day-first locales (e.g., en-AU, en-GB), and spell out numbers/currencies/dates in the **active target language** (`{user_language}`, e.g., `"ochenta y nueve dólares"`, `"नवासी डॉलर"`, `"八十九美元"`, `"팔십구 달러"` — never English number words inside a non-English sentence).
- [ ] **P1.4 (Conversational Pacing, Pre-Tool Bridges, Multi-Language Naturalness & Concise Turn Length):**
  - Keep turns concise (typically **2–3 sentences, ≤ 45 words, single paragraph** with no bullet lists, markdown, or emojis) and share at most 2 facts after a tool call before checking in.
  - Use natural spoken contractions in English (`"I'm"`, `"you've"`, `"it's"`, `"that's"`, `"there's"`, `"can't"`, `"won't"`, `"let's"`).
  - Prefer full stops over exclamation marks in spoken replies; use a tone tag such as `<positive>` when a reply needs more energy.
  - Speak a short 2–5 word pre-tool bridge in the caller's active language (`{user_language}`) before latency-sensitive tool calls, avoid echoing the same noun phrase in the post-tool opening, and connect with a soft conversational opener (`"Okay, so..."`, `"Right, so..."`).
  - **Multi-language register, voice-persona gender agreement & non-Latin script purity:**
    - Use polite metropolitan register (`usted` in Spanish, `आप`/`जी` in Hindi, `您` in Mandarin, `해요체` in Korean, `丁寧語` in Japanese).
    - In gendered languages (Hindi `सकती हूँ` vs. `सकता हूँ`, Spanish `encantada` vs. `encantado`, French, Portuguese), match first-person verb and adjective gender to the configured Chirp 3 HD voice persona (`Aoede` [default prebuilt voice]/`Zephyr`/`Kore`/`Leda` = female; `Puck`/`Charon`/`Fenrir`/`Orus` = male).
    - When responding in a non-Latin script (especially Devanagari Hindi, Mandarin, Korean, or Japanese after the caller switches languages from an English session), write brand names, technical acronyms, and loanwords phonetically in the **native script** (e.g., Devanagari `'वाई-फाई'` and `'सिम'` instead of Latin `'Wi-Fi'` and `'SIM'`, checked as `MIXED_LATIN_IN_DEVANAGARI_UTTERANCE`) and avoid dense English loanword chains separated by `और` (which sounds like English `"or"` and can trigger mid-sentence English code-switching in TTS).
- [ ] **P1.5 (Mandatory Prompt Contradiction Check & Explicit Agent Overrides):**
  - Check tag rules and normalization rules in three passes: (1) within `global_instruction.txt`, every rule against every other; (2) `global_instruction.txt` against each sub-agent (`agents/*/instruction.txt`) and its tools and docstrings; (3) callbacks and eval/test assertions (for example, a test that forbids adjacent or stacked tags blocks `<calm><empathetic>`).
  - **Surface every conflict instead of silently fixing it:** report the kind (tag or normalization), the tags or value types involved, every file and line, and the conflicting sentences quoted. Offer options (keep one, narrow one's scope, or use a different tag) and apply only what the user chooses. The only exception: rules that require closing tags are removed, because Composite V2 has no closing tags.
  - Common conflicts: leftover V1 prose rules (`"wrap tags in square brackets"`, `"never stack tags"`, `"do not bunch all tags at the front of a sentence"`, blanket `"no XML/angle-bracket markup in responses"`); conflicting spoken number/date/currency formats (e.g., a blanket `"read every number digit by digit"` vs. reading plain quantities as cardinals and reserving digit-by-digit for IDs, account numbers, reference codes, and postal codes); a casual currency readout (e.g., `"sixteen sixty-six"`) that doesn't say it's an exception; and a rule that opens transfer or goodbye turns with `<sigh>` / `<exhales>`.
  - **Explicit Agent Overrides:** Sub-agents may intentionally override global rules by stating so explicitly (`"In this agent, these rules override the global Affective Delivery and Voice Guidelines"`). Keep explicit overrides intact, convert their tags to `<...>`, and document them in your summary.

### 🟢 Priority P2: Callbacks, Web Widget Sanitization, Cross-App Handoffs & Ear-Audit Verification

- [ ] **P2.1 (`after_model_callback` Safety Net for Composite V2):**
  - Preserves all Composite V2 angle-bracket sound, pause, and tone tags via `INLINE_TAG` regex so deterministic text normalization (like English contractions) never mutates tag contents. The auditor checks that the callback's `INLINE_TAG` pattern covers every tag your instructions actually use (`CALLBACK_MISSING_V2_TAG_REGEX`).
  - Injects a least-recently-used holding bridge if the model calls a lookup tool in silence.
  - Guarantees spoken empathetic transfer confirmation precedes human-transfer tool calls and delays `end_session` until the transfer tool returns.
- [ ] **P2.2 (Tool Docstring Pacing & Web Portal Streaming Tag Sanitizer):**
  - Python tool docstrings include `"Before calling this tool, speak a brief pacing phrase..."`, `When to Call:`, and `When NOT to Call:` (for latency-sensitive tools approved by the developer).
  - Web client / chat widget strips all Composite V2 angle-bracket sound, pause, and tone tags (plus partial streaming `<...` tokens) from rendered transcript bubbles while preserving audio synthesis.
- [ ] **P2.3 (Cross-App Hand-Off Identification):**
  - Identify and report any cross-app or external hand-offs (`transferToNga` to another CX Agent Studio app, `transferToDialogflow` to a Dialogflow CX agent, `telephonyTransferCall`, `liveAgentHandoff`); the auditor lists them as informational `handoff` / `app_reference` entries. Note that a transferred destination runs its own separate session with its own voice, TTS model, speaking style, and prompts, and needs its own Composite V2 migration.
- [ ] **P2.4 (Local E2E Audio Simulation & Turn-by-Turn `.wav` Ear-Audit):**
  - Run an audio simulation with agent audio capture (see Mode 3), listen to the per-turn `.wav` files recorded in `agent_audio_paths`, and score against the 6-dimension Human-Likeness Rubric (target ≥ 9.0 / 10).

______________________________________________________________________

## Auditor Finding Codes

Each auditor finding carries one of these codes (shown in the `--report` output and its `--json` form):

| Code                                         | Priority | Fixed by `--remediate`?                                        |
| :------------------------------------------- | :------- | :------------------------------------------------------------- |
| `MODEL_NOT_COMPOSITE_V2`                     | P0       | Yes (older Composite or missing model)                         |
| `AGENT_MODEL_NOT_COMPOSITE_V2`               | P0       | Yes (older Composite overrides)                                |
| `MISSING_SYNTHESIZE_SPEECH_CONFIGS`          | P0       | Yes (scaffolds declared locales; defaults prebuilt to `Aoede`) |
| `MISSING_LOCALE_SPEECH_CONFIG`               | P0       | Yes (inherits persona; defaults prebuilt to `Aoede`)           |
| `MISSING_VOICE_ID`                           | P0       | Yes (inherits persona; defaults prebuilt to `Aoede`)           |
| `LEGACY_V1_DIRECTORS_NOTE_IN_V2_STYLE`       | P0       | Yes (cleared)                                                  |
| `CUSTOM_VOICE_DIRECTORS_NOTE`                | P0       | Yes (clears `voiceInstruction`, sets `NO_INSTRUCTION`)         |
| `CUSTOM_VOICE_INSTRUCTION_MODE_NOT_DISABLED` | P0       | Yes (sets `voiceInstructionMode: NO_INSTRUCTION`)              |
| `CUSTOM_VOICE_MISSING_GCS_URI`               | P0       | No (missing `gs://` on sample or consent URI)                  |
| `DEPRECATED_INLINE_VOICE_SAMPLE_URI`         | P0       | No (deprecated inline `voiceSampleGcsUri`)                     |
| `LEGACY_V1_SQUARE_BRACKET_TAG`               | P0       | Yes                                                            |
| `TRANSLATED_AUDIO_TAG`                       | P0       | Yes (converts to English `<...>` tags)                         |
| `CLOSING_AUDIO_TAG_FORBIDDEN`                | P0       | Yes                                                            |
| `MISSING_INSTRUCTION_FILE`                   | P0       | No                                                             |
| `NO_INSTRUCTIONS_FOUND`                      | P0       | No                                                             |
| `I015` / `V104` (structural lint)            | P0       | No                                                             |
| `NON_CHIRP3_HD_VOICE`                        | P1       | Yes with `--prebuilt-voice [PERSONA]` (default `Aoede`)        |
| `MULTILINGUAL_SUPPORT_NOT_ENABLED`           | P1       | Yes (enables `enableMultilingualSupport`)                      |
| `LOCALE_SPEAKER_PROFILE_ACCENT_MISMATCH`     | P1       | No (English accent in non-English locale)                      |
| `SPEAKING_STYLE_TOO_LONG`                    | P1       | Yes (cleared)                                                  |
| `CUSTOM_VOICE_INSTRUCTION_TOO_LONG`          | P1       | Yes (clears `voiceInstruction`, sets `NO_INSTRUCTION`)         |
| `CUSTOM_VOICE_LOCALE_STYLE_CONFLICT`         | P1       | Yes (clears non-empty custom-voice locale `instruction`)       |
| `STALE_V2_AUDIO_TAG`                         | P1       | Yes                                                            |
| `MIXED_LATIN_IN_DEVANAGARI_UTTERANCE`        | P1       | No (Latin words inside Devanagari utterance)                   |
| `UNEVALUATED_V2_TAG`                         | P1       | No (suggestion only)                                           |
| `UNRECOGNIZED_BRACKET_TOKEN`                 | P1       | No                                                             |
| `PROSODY_NOT_FIRST_IN_STACK`                 | P1       | Yes                                                            |
| `MID_SENTENCE_TAG_PLACEMENT`                 | P1       | Yes (adds comma before pause; moves style tag to start)        |
| `SIGH_ON_ENDING_OR_TRANSFER_TURN`            | P1       | No (product decision)                                          |
| `CONTRADICTORY_LEGACY_TAG_RULE`              | P1       | No                                                             |
| `ELLIPSIS_PAUSE_TAG_COLLISION`               | P1       | No                                                             |
| `ALL_CAPS_IMMEDIATELY_AFTER_VOCAL_TAG`       | P1       | No                                                             |
| `MISSING_GLOBAL_INSTRUCTION`                 | P1       | No                                                             |
| `MISSING_V2_AUDIO_TAG_GUIDANCE`              | P1       | No                                                             |
| `MISSING_TEXT_NORMALIZATION_BLOCK`           | P1       | No                                                             |
| `UNSCOPED_TEXT_NORMALIZATION`                | P1       | No                                                             |
| `MISSING_ALL_CAPS_LEXICAL_STRESS_RULE`       | P1       | No                                                             |
| `TEMPERATURE_ADVISORY`                       | P2       | No (advisory)                                                  |
| `UNBOUND_CUSTOM_VOICE_SAMPLE`                | P2       | Yes with `--bind-custom-voice <name\|auto>`                    |
| `CUSTOM_VOICE_STALE_PREVIEW_AUDIO_BLOB`      | P2       | Yes (strips `previewAudioContent` from `app.json`)             |
| `CALLBACK_MISSING_V2_TAG_REGEX`              | P2       | No                                                             |
| `handoff` / `app_reference`                  | Info     | No (informational cross-app hand-offs)                         |

______________________________________________________________________

## How to Use This Skill (3 Operational Modes)

### Mode 1: Audit & Prioritized Composite V2 Readiness Report

Run the automated Composite V2 auditor to inspect `app.json`, `global_instruction.txt`, `agents/*/instruction.txt`, `agents/*/*.json`, `tools/`, and `after_model_callbacks/` (including cross-app hand-off detection):

```bash
uv run python .agents/skills/cxas-composite-v2-voice-agent-optimizer/scripts/audit_agent_v2.py \
  --workspace="<PATH_TO_CXAS_APP_DIR>" \
  --report
```

- Add `--json` to print the report as JSON, and `--output <file>` to write it to a file (for example, `--report --json --output composite_v2_report.json`).
- Each finding carries a code from [Auditor Finding Codes](#auditor-finding-codes).

> [!NOTE]
> If the script runs without the `cxas_scrapi` package (for example, with plain `python3`), the structural lint pass (`I015` / `V104`) is shown as **SKIPPED**. Run it with `uv run python` so that pass runs.

### Mode 2: Automated + Semantic Composite V2 Migration & Remediation

1. **Snapshot a Version Before Changing Anything (When Connected to a Live CES App):**
   ```bash
   uv run cxas versions create --app-name "<APP_RESOURCE_NAME>" --display-name "pre-composite-v2"
   ```
1. **Preview the Automated Migration & Run the Three-Pass Contradiction Check** (`--dry-run` prints a unified diff and writes nothing):
   ```bash
   uv run python .agents/skills/cxas-composite-v2-voice-agent-optimizer/scripts/audit_agent_v2.py \
     --workspace="<PATH_TO_CXAS_APP_DIR>" \
     --remediate --dry-run
   ```
   - Inspect `global_instruction.txt`, `agents/*/instruction.txt`, tool descriptions/docstrings, callbacks, and eval/test assertions across all three passes of the **Contradiction Check** (P1.5).
1. **Batch All User Decisions in One Turn (If Any Require Confirmation):**
   - When choosing between a prebuilt voice vs. a custom cloned voice (e.g., on a new app, `NON_CHIRP3_HD_VOICE`, or an unbound `customVoiceSamples[]` entry), **if prebuilt voice is chosen without a specific persona request, default to `Aoede` (`<locale>-Chirp3-HD-Aoede`) across all declared locales** (while keeping any already-configured valid `*-Chirp3-HD-*` persona unless the owner wants to change it).
   - Present a recommended **~12-word vertical speaker profile** from [`references/natural_speech_patterns_v2.md` §1](references/natural_speech_patterns_v2.md) adapted to the app's vertical, Chirp 3 HD voice gender (feminine when using default `Aoede`), and locale (or offer `""` as the secondary alternative) when replacing a legacy V1 Director's Note or feeling-word style.
   - Surface any **P1.5 prompt contradictions** (quoting the conflicting lines and offering options), any **`UNEVALUATED_V2_TAG`** swap suggestions, and ask before adding `<prosody rate>` where the app had none.
1. **Run Automated In-Place Migration & Remediation** (`--backup` saves `<file>.bak` before changing each file; add `--prebuilt-voice` \[defaults to `Aoede`, or `--prebuilt-voice <Persona>`\] when switching to a prebuilt Chirp 3 HD voice, or `--bind-custom-voice auto` when binding a single `customVoiceSamples[]` voice across all declared locales in one pass). This applies all `Yes` items in [Auditor Finding Codes](#auditor-finding-codes) (`app.json` model/locale/voice/custom-voice remediation per P0.1/P1.1 and `[tag]` → `<tag>` / remap / placement fixes per P0.2/P1.2) and prints any removed speaking styles so you can set the chosen ~12-word speaker profile for prebuilt voices:
   ```bash
   uv run python .agents/skills/cxas-composite-v2-voice-agent-optimizer/scripts/audit_agent_v2.py \
     --workspace="<PATH_TO_CXAS_APP_DIR>" \
     --remediate --backup
   ```
1. **Apply Semantic Prompt, Speaking Style & Callback Optimizations** using the canonical templates in [`references/natural_speech_patterns_v2.md`](references/natural_speech_patterns_v2.md):
   - Ensure `synthesizeSpeechConfigs.<locale>.voice` uses `<locale>-Chirp3-HD-Aoede` whenever prebuilt voice was chosen without another explicit `*-Chirp3-HD-*` persona, and set `synthesizeSpeechConfigs.<locale>.instruction` to the chosen ~12-word vertical speaker profile (without copying English-locale accents into non-English locales) or `""`.
   - Add (or merge into an existing equivalent block) `<instruction id="audio tag format">` and `<instruction id="text normalization for speech">` (including the mandatory **`Scope:`** tool-call guard, target-language number words, and `/IPA/` pronunciation notation) to `global_instruction.txt`.
   - Refactor `<persona>`, `<Affective Delivery and Voice Guidelines>`, `<response_protocol>`, and `<taskflow>` examples non-destructively to enforce concise single-paragraph turns, 1–2 ALL-CAPS stress words (in Latin-script languages), pre-tool bridges + post-tool soft connectors, voice-persona gender agreement, non-Latin script purity, and clean tag placement.
   - Apply the user's chosen resolutions for any P1.5 contradictions and `UNEVALUATED_V2_TAG` items.
   - Add or update `after_model_callback` for V2 tag preservation, contractions, silent-lookup bridge injection, and warm-transfer speech ordering.
1. **Run Verification & Structural Linter:**
   ```bash
   uv run python .agents/skills/cxas-composite-v2-voice-agent-optimizer/scripts/audit_agent_v2.py \
     --workspace="<PATH_TO_CXAS_APP_DIR>" \
     --report

   uv run cxas lint --app-dir "<PATH_TO_CXAS_APP_DIR>"
   ```
1. **Push & Snapshot Again (When Targeting a Live CES App):** First delete the `.bak` files (or move them out of the app directory), because `cxas push` uploads everything inside `agents/` and `tools/`. Always pass `--to` with the full app resource name; without it, `cxas push` creates a new app.
   ```bash
   uv run cxas push --app-dir "<PATH_TO_CXAS_APP_DIR>" --to "<APP_RESOURCE_NAME>"
   uv run cxas versions create --app-name "<APP_RESOURCE_NAME>" --display-name "composite-v2" --description "Optimized for Gemini Composite V2"
   ```
1. **Report Back** using the summary format below.

#### Summary Format

Report the migration to the user in this order:

1. **Script edits:** counts per pass and the files changed.
1. **Manual fixes:** file, line, and change for each fix.
1. **Global instruction blocks:** blocks added or merged, and the prosody decision.
1. **Contradictions:** each conflict with the user's resolution, or marked as still open.
1. **Agent overrides:** explicit overrides kept (with their tags converted to `<...>`).
1. **Cross-app hand-offs:** each hand-off found, or "none".
1. **Remaining findings:** for example, unevaluated tags the user chose to keep.

### Mode 3: Local E2E Audio Simulation & Ear-Audit Loop

After static checks pass, verify actual synthesized voice quality on an E2E multi-turn simulation:

1. **Run an Audio Simulation with Agent Audio Capture** (pass `GECX_PROJECT="<PROJECT_FOLDER>"` so the runner resolves the right project, and mirror `/tmp/scrapi_evals` during execution because `scrapi-sim-runner.py` cleans up `/tmp/scrapi_evals/<session_id>` on exit):
   ```bash
   mkdir -p /tmp/ear_audit_wavs && (while true; do cp -ru /tmp/scrapi_evals/* /tmp/ear_audit_wavs/ 2>/dev/null || true; sleep 1; done) &
   WATCHER_PID=$!; trap "kill $WATCHER_PID 2>/dev/null || true" EXIT
   GECX_PROJECT="<PROJECT_FOLDER>" uv run python .agents/skills/cxas-agent-foundry/scripts/scrapi-sim-runner.py run \
     --eval "<SCENARIO_NAME>" \
     --channel audio \
     --capture-agent-audio
   ```
1. **Extract & Inspect Turn-by-Turn Audio (`.wav`)**:
   - Per-turn WAV files are preserved in `/tmp/ear_audit_wavs/<session_id>/turn_<N>_agent.wav` (or captured directly via `Sessions(app_name).run(..., modality="audio", capture_agent_audio=True, turn_num=i)`).
   - Inspect each turn's transcript and listen to key `.wav` files directly (Greeting Turn 0, Troubleshooting Turn, Resolution Turn, Account Readout Turn, Multi-Part Empathy-to-Solution Transition Turn, Multilingual Turn, Warm Transfer Turn).
1. **Score Against the 6-Dimension Composite V2 Human-Likeness Rubric** (target ≥ 9.0 / 10):
   1. **Vocal Timbre, Warmth & Speaker Consistency** (Stable ~12-word speaker profile without V1 Director's Note drift, authentic regional accent)
   1. **Inline Sound & Multi-Part Tone Tag Execution** (`<breath>`, `<exhales>`, `<sigh>`, `<short pause>`, `<calm><empathetic> ... <positive>` placed cleanly at boundaries, zero `... <short pause>` double-stutter, zero `<exhales> ALL-CAPS` bark, zero `<sigh>`/`<exhales>` on call-ending/transfer turns)
   1. **Lexical Stress (ALL-CAPS) & Rhythm** (Natural pitch lift on 1–2 capitalized words per turn in Latin-script languages)
   1. **Conversational Pacing, Bridges & Turn Length** (2–5 word varied pre-tool bridges, soft post-tool connectors, no noun echo, ≤ 45 words/turn)
   1. **Phonetic Accuracy, `/IPA/` & Alphanumeric Readout** (Accurate tracking/phone/address readout, `/IPA/` slash notation for tricky names, zero `"42%"` hallucination, raw written arguments passed to tools)
   1. **Emotional Attunement, Multilingual Fluency & Empathy Restraint** (Smooth multi-part tone transitions, native non-Latin script purity and voice-persona gender agreement, natural warm transfer confirmation before `end_session`)

______________________________________________________________________

## Reference Documentation

- **[Composite V2 Natural Speech Patterns, Prompt Templates & Callback Reference](references/natural_speech_patterns_v2.md)**: Complete production templates for `gemini-composite-v2`, including speaking style guidance (~12-word vertical speaker profile by default, or empty as a secondary alternative), voice persona rules, full V2 inline sound/pause and multi-part tone tag reference, optional `<prosody rate>` pacing, empirical V1-to-V2 tag replacement table, tool-scoped text normalization block, three-pass contradiction protocol & cross-app hand-off guide, `after_model_callback`, web widget tag-stripping regexes, and the acoustic troubleshooting table.
