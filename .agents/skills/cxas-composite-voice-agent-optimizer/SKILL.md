---
name: cxas-composite-voice-agent-optimizer
description: >-
  Audits, optimizes, and remediates CXAS agent configurations for Gemini Composite v2 voice naturalness
  directly in local workspaces with cxas-scrapi. Upgrades the model to gemini-composite-v2, configures
  prebuilt Chirp 3 HD voices (defaulting to Aoede) or custom cloned voices (NO_INSTRUCTION), clears
  Director's Notes while keeping prebuilt speaking styles concise (<=25 words / ~12-word speaker profile),
  migrates inline audio tags to English angle brackets, enforces Scope-guarded conditional text normalization,
  and generates prioritized (P0/P1/P2) readiness reports with automated fixes.
---

# CXAS Composite Voice Agent Optimizer

This skill audits, optimizes, and remediates Google Cloud CX Agent Studio (CXAS) and Customer Engagement Suite (CES) agent configurations for **Gemini Composite v2** voice naturalness, persona stability, and multi-language parity.

All core operations execute purely on local workspace files (`app.json`, `global_instruction.txt`, `agents/*/instruction.txt`, `tools/`, `callbacks/`). The `cxas` CLI can be used to manage local agent workspaces (`cxas pull`, `cxas lint`, `cxas push`).

Migrating to Composite v2 has three parts:

1. **Model, voice & speaking style.** Set `modelSettings.model` to `gemini-composite-v2` (`temperature` and `speakingRate` are not Composite v2 requirements and are untouched by `--remediate`), configure prebuilt Chirp 3 HD voices (defaulting to `Aoede`, `<locale>-Chirp3-HD-Aoede`) or custom cloned voices (`customVoiceSamples[]` with `"voiceInstructionMode": "NO_INSTRUCTION"`), clear Director's Notes, and keep prebuilt speaking style instructions (`synthesizeSpeechConfigs.<lang>.instruction`) empty (`""`) or concise (`≤ 25` words — ideally a ~12-word speaker-identity profile; custom cloned voices always use `""`).
1. **Audio tags.** Use English angle brackets (`<warm>`, `<breath>`, `<short pause>`), which are read aloud far less often than square brackets (`[warm]`) and followed more reliably. Put style and sound tags at the start of the sentence they color, use only pause tags mid-sentence (after punctuation), never write closing tags (`</prosody>` before speech becomes `<prosody rate="100%">`), and replace poorly performing or translated tags with evaluated English ones.
1. **Prompt hygiene & conditional text normalization.** Merge the affective delivery guidelines into the global instruction and resolve contradictions. Rely on platform runtime inline TTS text normalization for `en-*`, `fr`/`fr-CA`, and `es`/`es-US` (**do not add generic text normalization rules** there); include a `Scope:`-guarded `<instruction id="text normalization for speech">` block **only** when the app declares unsupported locales or needs domain-specific spoken overrides (`42 GB of 100 GB`, `Mbps`, `/IPA/`).

______________________________________________________________________

## When to Use This Skill

Activate this skill when:

- **Migrating to Composite v2:** Upgrading an existing CXAS agent (Composite v1 or older) to Gemini Composite v2.
- **Generating Readiness Reports:** Generating a prioritized (P0/P1/P2) assessment of the voice adaptations required for Gemini Composite v2.
- **Optimizing for Composite v2:** Applying Composite v2 audio tag, voice persona, custom voice, and multilingual best practices to a new or existing agent.

**When NOT to use this skill:**

- Standard text-only chat agents without voice/audio synthesis.
- Non-composite standard TTS/STT pipelines.
- Generic non-voice dialog flow refactoring.

______________________________________________________________________

## ⚠️ Fundamental Rule: Non-Destructive Preservation of Existing Instructions

**CRITICAL — DO NOT REMOVE EXISTING INSTRUCTIONS:** When optimizing an agent for Gemini Composite v2, you MUST NOT delete, remove, or strip existing business logic, domain instructions, taskflows, steps, or operational rules from `global_instruction.txt`, `agents/*/instruction.txt`, or tool docstrings.

All adaptations must be **non-destructive**, with two explicit exceptions (speaking style presets and poorly performing tags):

1. **Preserve Full Instruction Sets:** Retain all existing domain instructions, guardrails, step transitions, and business logic verbatim.
1. **Clear or Shorten Speaking Style Instructions (Exception):** Clear Director's Notes (`# Audio Profile`, `# The Scene`, `# Director's note`, `<agent_voice>`), feeling-word lists (`"warm, friendly, natural"`), and speaking style instructions over 25 words in `app.json`. For prebuilt voices, keep `synthesizeSpeechConfigs.<lang>.instruction` either as a ~12-word speaker-identity profile (`≤ 25` words) or `""`; for custom cloned voices (`customVoiceSamples[]`), keep both `voiceInstruction` and bound locale `instruction` as `""` with `"voiceInstructionMode": "NO_INSTRUCTION"`. In prompts, remove pasted Audio Profile / Director's Note text and static voice presets (accent, pitch, timbre, `<voice_lock>` blocks) after confirming with the user. Dynamic turn-by-turn emotion recognition, the 7 affective registers, and inline audio tag guidance stay in the global instruction under `Affective_Delivery_and_Voice_Guidelines` per [natural_speech_patterns.md](references/natural_speech_patterns.md).
1. **Migrate Tags, Never Drop the Rule Around Them:** Convert every audio tag to English angle brackets and swap poorly performing tags per [tag_reference.md](references/tag_reference.md). Rewrite rules written for the old format (*"wrap tags in square brackets"*, *"never stack tags"*, *"do not bunch tags at the front of a sentence"*) in place so the file keeps its shape.
1. **Rephrase Prohibited Tags Without Deleting Logic:** When resolving prohibited platform XML tags (strictly restricted to the explicit prohibited list: `<state_update>`, `<context>`, `<reasoning>`, `<thought>`, `<internal>`, `<call_tool>`, `<parameter_update>`, `<variable_update>`, `<voice_lock>`, `<voice_output>`, `<state>`, `<transition>`, `<transitions>`, and banned legacy CamelCase tags like `<Agent>`, `<Role>`, `<Persona>`), rephrase the tag references into plain natural language descriptions (e.g., *"system context"*, *"state update"*) rather than deleting the surrounding rules or instructions. Do NOT flag or check general/standard XML tags, and never confuse audio tags (`<warm>`, `<short pause>`) with prohibited platform tags.
1. **Augment Tool Docstrings Additively (Only if Insufficient):** In `tools/*/python_function/python_code.py`, preserve all existing descriptions, parameter documentation, and implementation details. Only append or integrate explicit `When to Call:` and `When NOT to Call:` execution boundaries if the existing docstring or description is missing, ambiguous, or insufficient.
1. **Harmonize Conflicting Directives Collaboratively:** When resolving contradictory instructions across scopes, ask the user which behavior to preserve and adjust the wording to eliminate the contradiction without deleting core domain logic.
1. **Enrich Spoken Cues Incrementally:** Add natural voice cues (ellipses `...`, brief bridge words, 1–2 ALL-CAPS stress words per turn in Latin scripts, and `/IPA/` pronunciation notation) into response instructions without altering the core messaging or domain content.

______________________________________________________________________

## Checklist & Inspection Gates

Codes in parentheses are reported by `scripts/audit_agent.py`.

### 🔴 Priority P0: Critical Synthesis Blockers, Contradictions & Instruction Clean Up (Must Fix First)

1. [ ] **Model Settings Configuration (CV201):** Set `modelSettings.model` to `"gemini-composite-v2"` in `app.json` and in every agent config that overrides it with an older Composite model. A non-Composite model is only reported; ask the user before changing it. (`modelSettings.temperature` and `speakingRate` are not Composite v2 requirements and are never modified by `--remediate`.)
1. [ ] **Clear Director's Notes & Enforce Speaking Style Bounds (CV203):** Clear any Director's Note field (`directorsNote`, markdown `# Audio Profile` / `# The Scene` / `# Director's note`, or `<agent_voice>` DSP blocks) and feeling-word lists (`"warm, friendly, natural"`). For prebuilt voices, keep `audioProcessingConfig.synthesizeSpeechConfigs.<lang>.instruction` empty (`""`) or concise (`≤ 25` words — ideally a ~12-word speaker-identity profile describing age, timbre, pitch, accent, and pace). For custom cloned voices, keep `customVoiceSamples[].voiceInstruction` and bound `synthesizeSpeechConfigs.<lang>.instruction` set to `""`.
1. [ ] **Custom Cloned Voice Integrity (CV205, CV204):** When `audioProcessingConfig.customVoiceSamples[]` is used, ensure valid `gs://...` URIs on **both** `voiceSampleGcsUri` and `consentAudioGcsUri` (missing either or using deprecated inline `synthesizeSpeechConfigs.<lang>.voiceSampleGcsUri` silently falls back to a prebuilt voice at runtime) and set `"voiceInstructionMode": "NO_INSTRUCTION"` so `cxas push` never auto-generates `<agent_voice>` blocks or restores cached `priorVoiceInstruction` text.
1. [ ] **Remove Speaking Style Presets from Prompts (CV215):** Remove Audio Profile / Director's Note text (`# Audio Profile`, `# The Scene`, `# Director's note`, `## Transcript:`) and static accent, pitch, and timbre presets pasted into `global_instruction.txt` or `agents/*/instruction.txt`, with user confirmation.
1. [ ] **English Angle-Bracket Audio Tag Syntax (CV210):** Convert every audio tag from `[tag]` to `<tag>` and convert translated non-English audio tags (`<pausa corta>`, `<empático>`, `<短暂停顿>`, `<छोटा विराम>`) to canonical English `<...>` tags wherever they appear: instructions, text passed to tools (e.g. `customize_response` content), tool descriptions and docstrings, callback code and comments, JSON and YAML values, evaluation expectations. Leave template placeholders (`[BOOKING_ID]`), markdown links, code subscripts, and control-logic labels (`user intent is [Negative]`) alone.
1. [ ] **Replace Poorly Performing Tags (CV211):** Swap every tag in the [tags-to-replace table](references/tag_reference.md#3-tags-to-replace) for its canonical evaluated tag (e.g. `<hope>` → `<warm>`, `<reassuring>` → `<empathetic>`, `<medium pause>` → `<short pause>`, `<clears throat>` → `<throat-clearing>`, `<slow>` → `<prosody rate="65%">`) and delete `<neutral>`. Reword prose a deletion leaves awkward, and de-duplicate tag lists a remap creates (`<empathetic>, <empathetic>`).
1. [ ] **Tag Placement & Closing Tag Removal (CV213, CV214):** Style, emotion, and paralinguistic sound tags (`<breath>`, `<exhales>`, `<sigh>`, `<laugh>`, `<chuckle>`, `<chuckles>`, `<gasp>`, `<throat-clearing>`) go at the start of the sentence they color; stacking and mid-turn sentence-opening tone shifts are allowed there. Mid-sentence, use only `<short pause>` / `<long pause>` after punctuation. `<prosody rate="N%">` comes first in a stack. Never write closing tags (CV213): delete rules that require them and rewrite `… </prosody>` before continuing speech to `<prosody rate="100%">`.
1. [ ] **Rewrite Old-Format Tag Rules & Avoid Acoustic Collisions (CV215):** Rewrite rules such as *"wrap tags in square brackets"*, *"use bracketed delivery tags"*, *"never stack tags"*, and *"do not bunch all tags at the front of a sentence"*. Never emit `<sigh>`, `<sighs>`, or `<exhales>` on call-ending or transfer turns, never stack `... <short pause>`, and never place an ALL-CAPS word immediately after `<exhales>` or `<sigh>`.
1. [ ] **Global Voice Guidelines & Emotion Attunement (CV216):** Merge the `Affective_Delivery_and_Voice_Guidelines` block from [natural_speech_patterns.md §1.5](references/natural_speech_patterns.md#15-production-prompt-template-global-instruction) into the global instruction. If a voice or affective section already exists, put the *Audio Tag Format* bullets inside it and edit contradicting rules in place; otherwise add the whole block. If a new `global_instruction.txt` is created, **add and reference it in `app.json`** (`"globalInstruction": "global_instruction.txt"`). Conversational sub-agents' instructions must reference `Affective_Delivery_and_Voice_Guidelines` in their response protocol or persona, without copying the block.
1. [ ] **Conditional Tool-Scoped Text Normalization (CV217):** Rely on platform runtime inline TTS text normalization for `en-*`, `fr`/`fr-CA`, and `es`/`es-US` — **do not add generic text normalization or digit-clustering blocks** when all configured locales are in that set. Whenever an existing customer readout block or an unsupported-locale / domain-override `<instruction id="text normalization for speech">` block is present, it **MUST** include a spoken-only `Scope:` guard (`CV217` P0 error if missing) ensuring raw written values (`"1234"`, `"XED1234"`) are passed unchanged to tool arguments.
1. [ ] **Eliminate Prohibited Platform Tags (I015):** Eliminate prohibited platform tags (strictly the explicit list in the Fundamental Rule) which trigger thought-leakage safety filters. Do NOT check or flag general XML tags or audio tags.
1. [ ] **Cross-Scope Contradictory Instruction Resolution:** Detect and resolve all mutually conflicting instructions across `global_instruction.txt`, `agents/*/instruction.txt`, tool descriptions/docstrings, and callbacks. Check in two passes: (1) within the global instruction; (2) the global instruction against each agent, one agent at a time, including that agent's tools. Systematically scan for:
   - *Audio Tag Conflicts:* a tag prescribed in one place and banned or called "inert" in another; two rules assigning different tags or prosody rates to one situation; a blanket markup ban (*"no SSML/XML or bracket tags in responses"*) that silences the global tag block. An agent instruction that *explicitly* says its rules override the global ones is not a contradiction; keep it and convert its tags.
   - *Spoken Transfer Announcements vs. Silent Callbacks/Tools:* When `after_model_callbacks` or handoff tools (`escalation_call`, `call_wrap_up`) handle transfer announcements and farewell audio, prompt directives to speak transfer messages MUST be harmonized to silent handoffs to prevent double-speaking.
   - *Invalid Tool Invocation Markup:* Convert `{@TOOL: tool_name(arg="val")}` to `{@TOOL: tool_name}` with parameters described in natural language.
   - *Pre-Call Tool Pacing vs. Output Grounding:* Harmonize prompts that forbid speaking before tool returns with tool docstrings that require pre-call pacing phrases.
   - *"Say Exactly" vs. Anti-Repetition Deadlocks:* Remove blanket repetition bans that clash with verbatim compliance or verification requirements; let explicit retry ladders (Strike 1, Strike 2) govern rephrasing.
1. [ ] **Strip Negative Conversational Micro-Bans:** Remove prohibitions against natural connective words (`"Sure"`, `"Okay"`, `"One moment"`, `"Let's see"`, `"I understand"`).
1. [ ] **Decouple Global Loop Prohibitions from Local Escalation Ladders:** Remove blunt turn-count repetition caps that prematurely abort structured multi-turn escalation flows.
1. [ ] **Prune Monolithic Few-Shot `<examples>` Debt:** Remove large legacy dialog transcripts from prompt text; maintain vetted golden test cases in evaluation suites instead.
1. [ ] **Eliminate Text-Based Variable Setting Antipatterns:** Replace `"Set booking_verified = true"` style text with structured tool invocations (e.g., `update_booking_status`).
1. [ ] **Remove Redundant Platform Formatting Rules:** Remove instructions mandating plaintext, forbidding markdown headers, or instructing digit spacing; these are handled by the platform baseline.
1. [ ] **Eliminate Reflexive Turn Closings:** Avoid ending every turn with *"Is there anything else?"*.
1. [ ] **Eliminate Deprecated Language-Switching Tools:** Remove `language_switcher` / `en_to_es` style tools; session language is set at session initialization.
1. [ ] **Incorporate Natural Speech Cues, Lexical Stress & `/IPA/`:** Incorporate ellipses `...`, brief bridge words (`"um"`, `"hmm"`, `"let's see"`), 1–2 ALL-CAPS stress words per turn in Latin-script languages, and `/IPA/` pronunciation notation (`/niːv/`) for tricky names or specialized terms.
1. [ ] **Preserve Domain Logic & Taskflows (MANDATORY Non-Destructive Rule):** Never delete business logic, validation rules, or negative operational constraints.

### 🟡 Priority P1: Multi-Language Parity, Session Stability & Tag Review

1. [ ] **Multi-Language Session Variable (`user_language`):** When the application declares multiple `languageSettings.supportedLanguageCodes`, ensure `user_language` or `app_language` is declared in `app.json.variableDeclarations`.
1. [ ] **Multi-Language Voice Parity & Prebuilt `Aoede` Default (CV204, CV203, CV215):** Every declared locale has a `synthesizeSpeechConfigs` entry with a valid `*-Chirp3-HD-*` voice (defaulting to `<locale>-Chirp3-HD-Aoede` when prebuilt voices are used without another configured Chirp 3 HD persona) or bound `customVoiceSamples[]` name, `languageSettings.enableMultilingualSupport` is `true` when multiple locales are declared, non-English locales never copy English accent descriptors (`"American"`, `"Australian"`), and Devanagari utterances keep loanwords/acronyms in native script (`'वाई-फाई'`, `'सिम'`).
1. [ ] **Unsupported-Locale Spoken Normalization (CV217):** When `app.json` declares locales outside platform runtime inline normalization (`en-*`, `fr`/`fr-CA`, `es`/`es-US` — e.g. `es-ES`, `es-MX`, `fr-FR`, `de-DE`, `cmn-CN`, `hi-IN`, `ja-JP`, `ko-KR`), include the `Scope:`-guarded `<instruction id="text normalization for speech">` block from [natural_speech_patterns.md §2.3](references/natural_speech_patterns.md#23-numbers-dates--identifiers).
1. [ ] **Unevaluated Tags & Bracket Typos (CV212):** Tags the evaluation didn't cover (`<seriousness>`, `<excited>`, `<celebratory>`, `<uhm>`, …) and likely bracket typos (`[clam]`) are reported with suggested evaluated tags; swap only what the user confirms. (`<breath>`, `<laugh>`, `<chuckle>`, `<gasp>`, and `<throat-clearing>` are supported Composite v2 tags and require no swap.)
1. [ ] **Prosody Decision:** If the original instructions use `<prosody rate>`, keep every rate verbatim. If they don't, ask the user whether to add prosody; add rates only at the values they choose. See [tag_reference.md §7](references/tag_reference.md#7-prosody-rates).
1. [ ] **Verify Long-Call Stability & Deployment Readiness:** Verify 5+ minute call stability without speaker drift or repetitive empathy. Note for live deployments: `gemini-composite-v2` uses its own regional quota bucket (mapped to `us-central1` for US/Global apps and `europe-west4` for EU apps), and Preview locales such as `hi-IN`, `ja-JP`, and `ko-KR` require project allowlisting before live synthesis.
1. [ ] **Minimize Proactive Unnecessary Call Transfers:** Transfer only on explicit customer escalation; sub-agent handoffs execute silently.
1. [ ] **Cross-App Hand-Offs (CV218):** Identify hand-offs to other apps or queues (`transferToNga`, `transferToDialogflow`, `telephonyTransferCall`, `liveAgentHandoff`) and tell the user the destination keeps its current TTS behavior and needs its own migration. Don't migrate the destination.

### 🟢 Priority P2: Lowest Priority — Placement Review, Callback Hygiene & Tool Pacing (User-Approved Only)

1. [ ] **Mid-Sentence Tag & Stack Review (CV214):** Ensure `<prosody rate="N%">` appears first in stacked tags, move mid-sentence style tags in quoted utterances to sentence start, and punctuate mid-sentence pause tags.
1. [ ] **Callback Tag Regex & Custom Voice Cleanup (CV218, CV205):** Verify `after_model_callback` `INLINE_TAG` regexes cover every Composite v2 tag used in prompts, strip transient `previewAudioContent` blobs from `customVoiceSamples[]`, and optionally bind unbound custom voices (`--bind-custom-voice`).
1. [ ] **Tool-Level Conversational Pacing Directives (T014, User-Approved Only):** Pacing phrases (*"Before calling this tool, speak a brief, natural conversational pacing phrase..."*) MUST NOT be applied across all tools; add them **ONLY for latency-sensitive tools explicitly approved by the person executing the skill**.
1. [ ] **Tool Docstring Sufficiency (Only if Insufficient):** Refine incomplete or ambiguous docstrings with `When to Call:` and `When NOT to Call:` boundaries upon user approval.

______________________________________________________________________

## Modes of Execution

```
                       ┌──────────────────────────────────────────────┐
                       │    CXAS Composite Voice Agent Optimizer      │
                       │         (Gemini Composite v2)                │
                       └──────────────────────┬───────────────────────┘
                                              │
                     ┌────────────────────────┴────────────────────────┐
                     ▼                                                 ▼
     ┌───────────────────────────────┐                 ┌───────────────────────────────┐
     │  Mode 1: Report Generation    │                 │       Mode 2: Fix Mode        │
     │  (Readiness Assessment)       │                 │ (Auto-Remediate & Refactor)   │
     └───────────────┬───────────────┘                 └───────────────┬───────────────┘
                     │                                                 │
     1. Discover workspace configuration               1. `--remediate`: model, voice, style, tags
     2. Run Composite v2 audit (`--report`)            2. Merge global voice guidelines
     3. Semantic review of prompts                     3. Fix tag rules & contradictions
     4. Present prioritized P0/P1/P2 table             4. Approved tool pacing & docstrings
                                                       5. Re-run audit & `cxas lint`
```

The auditor reuses the cxas-scrapi linter (`cxas_scrapi.utils.linter`): `Discovery` finds the app files, Composite v2 checks are `Rule` subclasses (`CV2xx`), and the model-agnostic SDK rules I015 (prohibited tags), V104 (unregistered variables), and T014 (tool pacing) are run alongside them. Run it with the repository's environment (`uv run python3 …`) so `cxas_scrapi` is importable.

| Flag                                 | Behavior                                                                                            |
| :----------------------------------- | :-------------------------------------------------------------------------------------------------- |
| *(none)*                             | One-line-per-finding audit summary                                                                  |
| `--report`                           | Prioritized P0/P1/P2 Markdown report                                                                |
| `--remediate [--dry-run] [--backup]` | Apply (or preview) deterministic fixes; `--backup` writes `<file>.bak` first                        |
| `--prebuilt-voice [PERSONA]`         | With `--remediate`, apply `<locale>-Chirp3-HD-PERSONA` across declared locales (default `Aoede`)    |
| `--bind-custom-voice <name\|auto>`   | With `--remediate`, bind `synthesizeSpeechConfigs.<locale>.voice` to a `customVoiceSamples[]` entry |
| `--lint-report`                      | Standard `cxas lint` output                                                                         |
| `--json` / `--output <path>`         | JSON output and/or write output to `<path>`                                                         |

The script exits `1` while any P0/P1/P2 finding remains (informational cross-app hand-offs do not fail the exit code).

______________________________________________________________________

### Mode 1: Report Generation Mode (Comprehensive Readiness Assessment)

Assesses an existing CXAS agent workspace against **all** checklist gates and generates a unified prioritized report (P0/P1/P2) of the voice adaptations required for Gemini Composite v2.

#### Workflow Steps:

1. **Workspace Discovery:** Locate `app.json`, the global instruction, sub-agent instructions (`agents/*/instruction.txt`), tools (`tools/`), and callbacks. List every agent; the migration is complete only when **every** agent has been checked, including agents that look unused or tag-free.

1. **Pass 1 — Execute Automated Audit:**

   ```bash
   uv run python3 .agents/skills/cxas-composite-voice-agent-optimizer/scripts/audit_agent.py \
     --workspace=<app_dir> --report
   ```

   *(What this covers: model settings (CV201), speaking style instructions and Director's Notes (CV203), multi-locale voice parity and multilingual enablement (CV204), custom cloned voice `gs://` URIs and `NO_INSTRUCTION` mode (CV205), square-bracket and translated tags in every app file (CV210), tags to replace (CV211), unevaluated tags and bracket typos (CV212), closing tags (CV213), `<prosody>` stack order and mid-sentence tags in quoted utterances (CV214), Director's Note text, old-format tag rules, ending-turn sighs, double-pause collisions, and locale/script mismatches in prompts (CV215), missing global tag guidance (CV216), conditional `Scope:`-guarded text normalization (CV217), callback `INLINE_TAG` coverage and cross-app hand-offs (CV218), prohibited platform XML tags (I015), unregistered template variables (V104), and tool pacing (T014 - P2)).*

1. **Pass 2 — Semantic Voice & Policy Review (LLM Checklist Evaluation):** Evaluate the instructions against gates the script cannot judge:

   - **Global Voice Guidelines (P0):** Does the global instruction define `Affective_Delivery_and_Voice_Guidelines` with the 7 registers, acute precedence, the Negative-Emotion Latch, and the audio tag format rules in angle brackets?
   - **Speaking Style Presets in Prompts (P0):** Static voice styling, accent, pitch, or `<voice_lock>`/`<voice_output>` blocks that should be removed.
   - **Old-Format & Contradictory Tag Rules (P0):** Rules that require square brackets, forbid stacking, push tags mid-sentence, require closing tags, ban recommended tags, or assign different tags/rates to one situation.
   - **Conditional Text Normalization (P0/P1):** Flag any generic normalization block added unnecessarily on `en-*`/`fr-CA`/`es-US` apps, verify `Scope:` protects tool arguments whenever a normalization block is present, and check target-language readout rules on unsupported locales.
   - **Cross-Scope Contradictions, Micro-Bans, Loop Caps, `<examples>` Debt, Text Variable Mutations, Redundant Formatting Rules, Reflexive Closings, Language-Switching Tools (P0):** as in the checklist.
   - **Long-Call Stability & Empathy Capping (P1):** Empathetic fillers and apologies are capped at 1 per call.
   - **Tool Conversational Pacing & Docstring Sufficiency (P2 — User-Approved Only):** List tools missing pacing as P2 with an Open Question asking whether to add pacing to that specific tool.

1. **Synthesize & Present Unified Prioritized Report:** Combine Pass 1 and Pass 2 into one assessment. **Always generate a markdown table** of all P0, P1, and P2 issues:

   - **Executive Summary:** Overall readiness status (`PASSED` / `FAILED`) and issue counts for P0, P1, and P2.
   - **Prioritized Assessment Table** with columns:
     - **Priority:** `🔴 P0`, `🟡 P1`, or `🟢 P2`.
     - **Issue Description:** Issue code, concise explanation, and clickable links to affected files with line numbers.
     - **Possible Resolution:** Concrete, non-destructive remediation steps.
     - **Open Questions:** **Only** for genuine conflicts or business-logic ambiguity (contradictions, prebuilt ~12-word speaker profile vs. `""`, unevaluated tag swaps, prosody, tool pacing approval). For deterministic fixes, state `*(None - deterministic fix)*`.

   **Table Schema Example:**

   ```markdown
   | Priority | Issue Description | Possible Resolution | Open Questions |
   | :--- | :--- | :--- | :--- |
   | 🔴 **P0** | **`CV203`**<br>`app.json` `synthesizeSpeechConfigs['en-US'].instruction` holds a Director's Note. | Clear the Director's Note and set a ~12-word speaker profile (`≤ 25` words) or `""`. | Use a ~12-word speaker profile or leave `""`? |
   | 🔴 **P0** | **`CV210`**<br>`global_instruction.txt:L14` uses `[warm]` and `[reassuring]`. | Convert to `<warm>` and `<empathetic>`. | *(None - deterministic fix)* |
   | 🔴 **P0** | **`CONTRADICTORY_TAG_RULES`**<br>`global_instruction.txt:L20` prescribes `<warm>` for frustrated callers; `agents/billing/instruction.txt:L8` calls `<warm>` inert. | Keep one rule, or mark the agent rule as an explicit override. | Which rule should win for the billing agent? |
   | 🟡 **P1** | **`CV212`**<br>`global_instruction.txt:L31` uses `<seriousness>` (unevaluated). | Swap to `<clear>` if approved. | Replace `<seriousness>` with `<clear>`? |
   | 🟢 **P2** | **`T014`**<br>`tools/search_flights/python_function/python_code.py` lacks a pacing phrase. | Add varied pacing phrase to docstring if approved. | Add a pre-call pacing phrase to `search_flights`? |
   ```

______________________________________________________________________

### Mode 2: Fix Mode

Combines **automated in-place remediation** with **context-aware prompt refactoring** and **tool docstring engineering**.

#### Workflow Steps:

1. **Execute Automated Remediation:** Preview, then apply:

   ```bash
   S=.agents/skills/cxas-composite-voice-agent-optimizer/scripts/audit_agent.py
   uv run python3 $S --workspace=<app_dir> --remediate --dry-run
   uv run python3 $S --workspace=<app_dir> --remediate
   ```

   **What `--remediate` patches:**

   - **Model (`CV201`):** `modelSettings.model` → `gemini-composite-v2` in `app.json` (added if missing) and in agent configs that override it with an older Composite model. Non-Composite models, `modelSettings.temperature`, and `speakingRate` are never modified.
   - **Voice & Custom Voice (`CV204`, `CV205`):** Scaffolds missing `synthesizeSpeechConfigs` entries or empty `voice` fields across declared locales (inheriting an existing `*-Chirp3-HD-*` persona or defaulting to `<locale>-Chirp3-HD-Aoede`; pass `--prebuilt-voice [PERSONA]` to replace legacy non-Chirp voices, or `--bind-custom-voice <name|auto>` to bind a custom voice), sets `languageSettings.enableMultilingualSupport` to `true` when multiple locales are declared, sets `"voiceInstructionMode": "NO_INSTRUCTION"` on `customVoiceSamples[]`, and strips transient `previewAudioContent` blobs.
   - **Speaking style (`CV203`):** Clears Director's Notes, feeling-word lists, prebuilt styles over 25 words, and any non-empty custom-voice `voiceInstruction` / bound locale `instruction` to `""`, printing cleared text under `removed_styles` so you can craft a ~12-word prebuilt speaker profile if desired (valid `≤ 25`-word prebuilt speaker profiles are preserved intact).
   - **Audio tags, in every app text file (`CV210`, `CV211`, `CV213`, `CV214`):** `[tag]` and translated non-English tags → English `<tag>`, tags-to-replace swapped (`<neutral>` deleted), `</prosody>` before continuing speech → `<prosody rate="100%">` (standalone closing tags deleted), `<prosody>` moved first in stacked tags, remap-created duplicates removed, and mid-sentence tags in quoted utterances punctuated or moved to sentence start. Quotes are escaped inside JSON strings, and scalar JSON config edits preserve existing file formatting.

1. **Merge `Affective_Delivery_and_Voice_Guidelines` into the Global Instruction (P0 Mandatory):**

   - Read the existing global instruction in full. **Merge, don't overwrite:** put the *Audio Tag Format* bullets into the existing voice or affective section; add the full template from [natural_speech_patterns.md §1.5](references/natural_speech_patterns.md#15-production-prompt-template-global-instruction) only when none exists. Match the file's heading and bullet style.
   - If a new `global_instruction.txt` is created beside `app.json`, **add `"globalInstruction": "global_instruction.txt"` to `app.json`**.
   - Ask about prosody before adding any `<prosody rate>` ([tag_reference.md §7](references/tag_reference.md#7-prosody-rates)). Without prosody, omit the two prosody bullets.
   - Do **not** add a generic text normalization block when all locales are in `en-*`, `fr`/`fr-CA`, or `es`/`es-US`; add the `Scope:`-guarded block from [natural_speech_patterns.md §2.3](references/natural_speech_patterns.md#23-numbers-dates--identifiers) only for unsupported locales (`CV217`) or domain overrides.
   - Update conversational sub-agent instructions to reference `Affective_Delivery_and_Voice_Guidelines` (don't copy the block into them).

1. **Manual Tag & Prompt Hygiene Fixes (P0/P1 Mandatory):**

   - **Old-format rules & collisions (CV215):** rewrite *"wrap tags in square brackets"*, *"never stack tags"*, *"use bracketed delivery tags … never speak a bracketed tag aloud"*, *"do not bunch all tags at the front"*; remove `<sigh>`/`<sighs>`/`<exhales>` on call-ending/transfer turns, `... <short pause>` collisions, ALL-CAPS words right after `<exhales>`/`<sigh>`, English accent words in non-English locales, and Latin words inside Devanagari utterances. Afterwards, search the prompts for `"bracket"`.
   - **Speaking style presets (CV215):** remove Director's Note / Audio Profile text and static accent or pitch presets from prompts, with user confirmation.
   - **Unrecognized bracketed words & unevaluated tags (CV212):** a typo (`[clam]`) is an audio tag (`<calm>`); a variable or placeholder is left alone; unevaluated tags are swapped only with user approval.
   - **Prose left awkward by a deleted `<neutral>` or a remap** (*"use `<warm>` to greet and `<warm>` to close"*): reword.

1. **Systematic Cross-Scope Contradiction Clean-up & Prompt Refactoring (P0 Mandatory):**

   - **Audio Tag & Prosody Contradictions:** Report every conflict with its file, line, and the quoted sentences, then ask the user which rule wins (keep one, narrow one's scope, use a different tag). Edit the losing rule in place. Never resolve them yourself; the one exception is rules that require closing tags, which you remove.
   - **Spoken Transfer Announcements vs. Silent Callbacks/Tools:** Search for `"transfer you"`, `"transferring you"`, `"live agent"`, `"representative"`. If a callback or handoff tool handles the transfer message, harmonize the prompt to a silent handoff.
   - **Invalid Tool Invocation Markup:** Search for `\{@TOOL:\s*[^}\s]+\s*\(` and replace with clean `{@TOOL: tool_name}` references.
   - **Pre-Call Tool Pacing vs. Output Grounding:** Permit pacing phrases before latency-sensitive tools while keeping handoff/wrap-up tools silent.
   - **"Say Exactly" vs. Anti-Repetition Deadlocks, Micro-Bans, Loop Caps, `<examples>` Debt, Text Variable Mutations, Redundant Formatting Rules, Reflexive Closings, Language Switchers:** as in the checklist.
   - **Prohibited XML Refactoring (I015) & Callback Tag Regex (CV218):** Rephrase prohibited platform tags into plain language without deleting domain logic, and update `after_model_callback` `INLINE_TAG` regexes to preserve all Composite v2 tags used in prompts.
   - **Declare `user_language`** for multi-language apps, and **sync declared tools** referenced in prompts (`{@TOOL: ...}`) with each agent's `.json`.

1. **Interactive Tool Docstring & Conversational Pacing Refactoring (Lowest Priority - Only for Approved Tools):**

   Tool docstrings are runtime execution contracts for the reasoning model and are **never auto-remediated**.

   - **T014 is P2 / advisory:** ask which latency-sensitive tools (if any) should get pacing phrases. **Do not modify unapproved tools.** Terminal tools (wrap-up, exit, mocks) are exempt.

   - **Python tools:** edit the docstring in `tools/<tool_name>/python_function/python_code.py`, **not** the tool `.json`. For OpenAPI, Client, or Data Store tools, edit their `.json`.

   - **Docstring sufficiency:** keep clear docstrings as they are; add `When to Call:` / `When NOT to Call:` only when missing or ambiguous.

   - **Contradiction & callback checks:** make sure prompts don't demand silence before a tool that now has a pacing phrase, and confirm with the user before replacing legacy `after_model_callbacks` or trivia tools.

   - **Docstring Pattern Example:**

     ```python
     def search_customer_account(phone_number: str) -> dict:
         """Searches for customer accounts by phone number.

         Before calling this tool, speak a brief, natural conversational pacing phrase
         with varied phrasing to avoid repetition across turns (e.g., 'Let me check that for you...',
         'Just a minute, let me look it up...', or 'Looking up your account now...').

         When to Call:
         - Call when the customer provides their phone number for account lookup.

         When NOT to Call:
         - Do NOT call if the phone number has fewer than 10 digits.
         """
     ```

1. **Run Verification & Quality Gates:**

   ```bash
   # Composite v2 audit; only user-kept findings (e.g. CV212) may remain
   uv run python3 .agents/skills/cxas-composite-voice-agent-optimizer/scripts/audit_agent.py \
     --workspace=<app_dir> --lint-report

   # SCRAPI structural linter
   uv run cxas lint
   ```

   No file may still contain a `[tag]`-style audio tag (CV210 must be clear). Delete any `.bak` files created with `--backup` before running `cxas push`.

1. **Verify Checklist & Summarize:** Run through the checklist above, then end with a summary in this order: (1) script edits per file; (2) manual fixes with file and line; (3) global instruction changes and the prosody decision; (4) contradictions with the user's resolution or still open; (5) agents that explicitly override the global rules; (6) cross-app hand-offs, or "none"; (7) remaining findings the user chose to keep.

1. **SCRAPI Deployment Lifecycle (for Deployed Agents):**

   ```bash
   # 1. Export the deployed agent configuration from CXAS
   uv run cxas pull "<APP_RESOURCE_OR_ID>" --target-dir ./workspace

   # 2. Snapshot current live version before pushing Composite v2 changes
   uv run cxas versions create --app-name "<APP_RESOURCE_OR_ID>" --display-name "pre-composite-v2"

   # 3. Run local Composite v2 remediation, then steps 2-7 above
   uv run python3 .agents/skills/cxas-composite-voice-agent-optimizer/scripts/audit_agent.py \
     --workspace=./workspace --remediate

   # 4. Run SCRAPI structural linter
   uv run cxas lint

   # 5. Deploy the optimized configuration back to CXAS
   uv run cxas push --app-dir ./workspace --to "<APP_RESOURCE_OR_ID>"
   ```

### Mode 3: Optional Audio Simulation & `.wav` Ear-Audit

After static checks pass, optionally run an audio simulation with `--channel audio --capture-agent-audio` (via `cxas-agent-foundry`'s `scrapi-sim-runner.py` or `Sessions(app_name).run(..., modality="audio", capture_agent_audio=True)`) and inspect the per-turn `.wav` files to verify vocal consistency across turns, clean `<breath>` / `<short pause>` / sentence-opening tone shifts without double pauses or barked syllables, natural ALL-CAPS stress, and accurate `/IPA/` and alphanumeric readouts.

______________________________________________________________________

## Reference Documentation

- [Tag Reference](references/tag_reference.md): Angle-bracket syntax, reliability tiers, tags to replace, unevaluated tags, delivery effects, validated combinations, prosody rates, and known side effects.
- [Natural Speech Patterns & Anti-Looping Guide](references/natural_speech_patterns.md): Prebuilt `Aoede` and custom-voice `app.json` patterns, 7 affective registers, the Negative-Emotion Latch, the `Affective_Delivery_and_Voice_Guidelines` template, conditional `Scope:`-guarded text normalization, ALL-CAPS / `/IPA/` formatting, callbacks, and web tag sanitization.
- [Global & Agent Instruction Guidelines](references/instructions_guide.md): Platform baseline vs. application responsibilities, contradiction resolution, and prompt hygiene checklists.
- [Tool Design & Conversational Pacing](references/tool_design_and_pacing.md): Tool docstring contracts, spoken pacing phrases before tool execution, payload contamination prevention, and execution standards.
