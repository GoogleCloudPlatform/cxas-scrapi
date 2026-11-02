---
name: cxas-composite-voice-agent-optimizer
description: >-
  Audits, optimizes, and remediates CXAS agent configurations for Gemini Composite v2 voice naturalness
  directly in local workspaces with cxas-scrapi. Upgrades the model to gemini-composite-v2, clears speaking
  style instructions and Director's Notes (or keeps speaking style below 12 words), migrates inline audio tags
  from square to angle brackets, and replaces poorly performing tags. Generates prioritized (P0/P1/P2) readiness
  reports and applies automated fixes.
---

# CXAS Composite Voice Agent Optimizer

This skill audits, optimizes, and remediates Google Cloud CX Agent Studio (CXAS) and Customer Engagement Suite (CES) agent configurations for **Gemini Composite v2** voice naturalness, persona stability, and multi-language parity.

All core operations execute purely on local workspace files (`app.json`, `global_instruction.txt`, `agents/*/instruction.txt`, `tools/`, `callbacks/`). The `cxas` CLI can be used to manage local agent workspaces (`cxas pull`, `cxas lint`, `cxas push`).

Migrating to Composite v2 has three parts:

1. **Model & speaking style.** Set `modelSettings.model` to `gemini-composite-v2`, clear Director's Notes, and either clear every speaking style instruction (`synthesizeSpeechConfigs.<lang>.instruction`, `customVoiceSamples[].voiceInstruction`) or keep it below 12 words. Composite v2 is steered by inline audio tags; long prompt-level speaking style instructions conflict with them.
1. **Audio tags.** Use angle brackets (`<warm>`), which are read aloud far less often than square brackets (`[warm]`) and followed more often. Put tags at the start of the sentence they color, use only pause tags mid-sentence (after punctuation), and replace tags that performed poorly with better-performing ones.
1. **Prompt hygiene.** Merge the affective delivery guidelines into the global instruction and resolve contradictions. **Do not add text normalization rules** (numbers, currency, dates, IDs, addresses): Composite v2 handles them in the platform.

______________________________________________________________________

## When to Use This Skill

Activate this skill when:

- **Migrating to Composite v2:** Upgrading an existing CXAS agent (Composite v1 or older) to Gemini Composite v2.
- **Generating Readiness Reports:** Generating a prioritized (P0/P1/P2) assessment of the voice adaptations required for Gemini Composite v2.
- **Optimizing for Composite v2:** Applying Composite v2 audio tag and voice best practices to a new or existing agent.

**When NOT to use this skill:**

- Standard text-only chat agents without voice/audio synthesis.
- Non-composite standard TTS/STT pipelines.
- Generic non-voice dialog flow refactoring.

______________________________________________________________________

## ⚠️ Fundamental Rule: Non-Destructive Preservation of Existing Instructions

**CRITICAL — DO NOT REMOVE EXISTING INSTRUCTIONS:** When optimizing an agent for Gemini Composite v2, you MUST NOT delete, remove, or strip existing business logic, domain instructions, taskflows, steps, or operational rules from `global_instruction.txt`, `agents/*/instruction.txt`, or tool docstrings.

All adaptations must be **non-destructive**, with two explicit exceptions (speaking style presets and poorly performing tags):

1. **Preserve Full Instruction Sets:** Retain all existing domain instructions, guardrails, step transitions, and business logic verbatim.
1. **Clear or Shorten Speaking Style Instructions (Exception):** Clear Director's Notes and either clear speaking style instructions in `app.json` (the script clears them by default) or keep them below 12 words. In prompts, remove pasted Audio Profile / Director's Note text and static voice presets (accent, pitch, timbre, `<voice_lock>` blocks) after confirming with the user. Do **not** relocate full Director's Notes anywhere: long speaking style prompts conflict with inline tags. Dynamic turn-by-turn emotion recognition, the 7 affective registers, and inline audio tag guidance stay in the global instruction under `Affective_Delivery_and_Voice_Guidelines` per [natural_speech_patterns.md](references/natural_speech_patterns.md).
1. **Migrate Tags, Never Drop the Rule Around Them:** Convert every audio tag to angle brackets and swap poorly performing tags per [tag_reference.md](references/tag_reference.md). Rewrite rules written for the old format (*"wrap tags in square brackets"*, *"never stack tags"*, *"do not bunch tags at the front of a sentence"*) in place so the file keeps its shape.
1. **Rephrase Prohibited Tags Without Deleting Logic:** When resolving prohibited platform XML tags (strictly restricted to the explicit prohibited list: `<state_update>`, `<context>`, `<reasoning>`, `<thought>`, `<internal>`, `<call_tool>`, `<parameter_update>`, `<variable_update>`, `<voice_lock>`, `<voice_output>`, `<state>`, `<transition>`, `<transitions>`, and banned legacy CamelCase tags like `<Agent>`, `<Role>`, `<Persona>`), rephrase the tag references into plain natural language descriptions (e.g., *"system context"*, *"state update"*) rather than deleting the surrounding rules or instructions. Do NOT flag or check general/standard XML tags, and never confuse audio tags (`<warm>`, `<short pause>`) with prohibited platform tags.
1. **Augment Tool Docstrings Additively (Only if Insufficient):** In `tools/*/python_function/python_code.py`, preserve all existing descriptions, parameter documentation, and implementation details. Only append or integrate explicit `When to Call:` and `When NOT to Call:` execution boundaries if the existing docstring or description is missing, ambiguous, or insufficient.
1. **Harmonize Conflicting Directives Collaboratively:** When resolving contradictory instructions across scopes, ask the user which behavior to preserve and adjust the wording to eliminate the contradiction without deleting core domain logic.
1. **Enrich Spoken Cues Incrementally:** Add natural voice cues (ellipses `...`, brief bridge words like *"umm..."*) into response instructions without altering the core messaging or domain content.

______________________________________________________________________

## Checklist & Inspection Gates

Codes in parentheses are reported by `scripts/audit_agent.py`.

### 🔴 Priority P0: Critical Synthesis Blockers, Contradictions & Instruction Clean Up (Must Fix First)

1. [ ] **Model Settings Configuration (CV201, CV202):** Set `modelSettings.model` to `"gemini-composite-v2"` in `app.json` and in every agent config that overrides it with an older Composite model, and `modelSettings.temperature` to `1.0` (prevents acoustic repetition loops). A non-Composite model is only reported; ask the user before changing it.
1. [ ] **Clear or Shorten Speaking Style Instructions (CV203):** Either clear to `""` or keep below 12 words every `audioProcessingConfig.synthesizeSpeechConfigs.<lang>.instruction` and `audioProcessingConfig.customVoiceSamples[].voiceInstruction`, and clear any Director's Note field (`directorsNote` / markdown `# Audio Profile` or `# Director's note` blocks). Full Director's Notes must be removed; if a speaking style instruction is kept, it must be below 12 words.
1. [ ] **Remove Speaking Style Presets from Prompts (CV215):** Remove Audio Profile / Director's Note text (`# Audio Profile`, `# Director's note`, `## Transcript:`) and static accent, pitch, and timbre presets pasted into `global_instruction.txt` or `agents/*/instruction.txt`, with user confirmation.
1. [ ] **Angle-Bracket Audio Tag Syntax (CV210):** Convert every audio tag from `[tag]` to `<tag>`, wherever it appears: instructions, text passed to tools (e.g. `customize_response` content), tool descriptions and docstrings, callback code and comments, JSON and YAML values, evaluation expectations. A rule such as *"no audio tokens in the content field"* doesn't stop the conversion; report it as a contradiction instead. Leave template placeholders (`[BOOKING_ID]`), markdown links, code subscripts, and control-logic labels (`user intent is [Negative]`) alone.
1. [ ] **Replace Poorly Performing Tags (CV211):** Swap every tag in the [tags-to-replace table](references/tag_reference.md#3-tags-to-replace) for its better-performing tag (e.g. `<hope>` → `<warm>`, `<reassuring>` → `<empathetic>`, `<slow>` → `<prosody rate="65%">`) and delete `<neutral>`. Reword prose a deletion leaves awkward, and de-duplicate tag lists a remap creates (`<empathetic>, <empathetic>`).
1. [ ] **Tag Placement & Format:** Style, emotion, and paralinguistic tags go at the start of the sentence they color; stacking is allowed there. Mid-sentence, only `<short pause>` / `<long pause>`, after punctuation. `<prosody rate="N%">` comes first in a stack. Never write closing tags (CV213): delete rules that require them and rewrite `… </prosody>` examples to the `<prosody rate="100%">` reset form.
1. [ ] **Rewrite Old-Format Tag Rules (CV215):** Rewrite rules such as *"wrap tags in square brackets"*, *"use bracketed delivery tags"*, *"never stack tags"*, and *"do not bunch all tags at the front of a sentence"* so they say what the audio tag format block says. Keep whatever the rule said about tag density.
1. [ ] **Global Voice Guidelines & Emotion Attunement (CV216):** Merge the `Affective_Delivery_and_Voice_Guidelines` block from [natural_speech_patterns.md §1.5](references/natural_speech_patterns.md#15-production-prompt-template-global-instruction) into the global instruction. If a voice or affective section already exists, put the *Audio Tag Format* bullets inside it and edit contradicting rules in place; otherwise add the whole block. If a new `global_instruction.txt` is created, **add and reference it in `app.json`** (`"globalInstruction": "global_instruction.txt"`). Conversational sub-agents' instructions must reference `Affective_Delivery_and_Voice_Guidelines` in their response protocol or persona, without copying the block.
1. [ ] **No Text Normalization Rules:** Do not add text normalization, digit-spacing, or digit-clustering instructions for numbers, currency, dates, times, phone numbers, alphanumeric IDs, or addresses. Composite v2 normalizes them in the platform. Keep customer-specific readout rules that already exist; report them if they conflict.
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
1. [ ] **Incorporate Natural Speech Cues:** Incorporate ellipses `...` and brief bridge words (`"um"`, `"hmm"`, `"let's see"`) in response instructions.
1. [ ] **Preserve Domain Logic & Taskflows (MANDATORY Non-Destructive Rule):** Never delete business logic, validation rules, or negative operational constraints.

### 🟡 Priority P1: Multi-Language Parity, Session Stability & Tag Review

1. [ ] **Multi-Language Session Variable (`user_language`):** When the application declares multiple `languageSettings.supportedLanguageCodes`, ensure `user_language` or `app_language` is declared in `app.json.variableDeclarations`.
1. [ ] **Multi-Language Voice Parity (CV204):** Every declared locale has a `synthesizeSpeechConfigs` entry with a voice, and its speaking style instruction is either empty or below 12 words.
1. [ ] **Unevaluated Tags (CV212):** Tags the evaluation didn't cover (`<seriousness>`, `<excited>`, `<celebratory>`, `<uhm>`, …) are converted but their behavior is unknown. Present the suggested evaluated tag and swap only what the user confirms.
1. [ ] **Prosody Decision:** If the original instructions use `<prosody rate>`, keep every rate verbatim. If they don't, ask the user whether to add prosody; add rates only at the values they choose. See [tag_reference.md §7](references/tag_reference.md#7-prosody-rates).
1. [ ] **Verify Long-Call Stability (5+ Minutes):** Verify stability without speaker drift, repetitive empathy, or tag repetition.
1. [ ] **Minimize Proactive Unnecessary Call Transfers:** Transfer only on explicit customer escalation; sub-agent handoffs execute silently.
1. [ ] **Cross-App Hand-Offs:** Identify hand-offs to other apps (`transferToNga`, `transferToDialogflow`) and tell the user the destination keeps its current TTS behavior and needs its own migration. Don't migrate the destination.

### 🟢 Priority P2: Lowest Priority — Placement Review, Tool Pacing & Docstring Hygiene (User-Approved Only)

1. [ ] **Mid-Sentence Tag Review (CV214):** Review quoted example utterances where a style tag sits mid-sentence or a pause tag follows a word without punctuation, and move or punctuate them.
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
     1. Discover workspace configuration               1. `--remediate`: model, style, tags
     2. Run Composite v2 audit (`--report`)            2. Merge global voice guidelines
     3. Semantic review of prompts                     3. Fix tag rules & contradictions
     4. Present prioritized P0/P1/P2 table             4. Approved tool pacing & docstrings
                                                       5. Re-run audit & `cxas lint`
```

The auditor reuses the cxas-scrapi linter (`cxas_scrapi.utils.linter`): `Discovery` finds the app files, Composite v2 checks are `Rule` subclasses (`CV2xx`), and the model-agnostic SDK rules I015 (prohibited tags), V104 (unregistered variables), and T014 (tool pacing) are run alongside them. Run it with the repository's environment (`uv run python3 …`) so `cxas_scrapi` is importable.

| Flag                      | Behavior                                                             |
| :------------------------ | :------------------------------------------------------------------- |
| *(none)*                  | One-line-per-finding audit summary                                   |
| `--report`                | Prioritized P0/P1/P2 Markdown report                                 |
| `--remediate [--dry-run]` | Apply (or list) deterministic fixes, then print the remaining issues |
| `--lint-report`           | Standard `cxas lint` output                                          |
| `--json`                  | JSON output for any of the above                                     |

The script exits `1` while any finding remains.

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

   *(What this covers: model and temperature (CV201, CV202), speaking style instructions and Director's Notes (CV203), voice parity (CV204), square-bracket tags in every app file (CV210), tags to replace (CV211), unevaluated tags (CV212), closing tags (CV213), mid-sentence tags in quoted utterances (CV214), Director's Note text and old-format tag rules in prompts (CV215), missing global tag guidance (CV216), prohibited platform XML tags (I015), unregistered template variables (V104), and tool pacing (T014 - P2)).*

1. **Pass 2 — Semantic Voice & Policy Review (LLM Checklist Evaluation):** Evaluate the instructions against gates the script cannot judge:

   - **Global Voice Guidelines (P0):** Does the global instruction define `Affective_Delivery_and_Voice_Guidelines` with the 7 registers, acute precedence, the Negative-Emotion Latch, and the audio tag format rules in angle brackets?
   - **Speaking Style Presets in Prompts (P0):** Static voice styling, accent, pitch, or `<voice_lock>`/`<voice_output>` blocks that should be removed.
   - **Old-Format & Contradictory Tag Rules (P0):** Rules that require square brackets, forbid stacking, push tags mid-sentence, require closing tags, ban recommended tags, or assign different tags/rates to one situation.
   - **Text Normalization (P0):** Flag any *new* normalization block an earlier migration added; existing customer-specific readout rules are kept.
   - **Cross-Scope Contradictions, Micro-Bans, Loop Caps, `<examples>` Debt, Text Variable Mutations, Redundant Formatting Rules, Reflexive Closings, Language-Switching Tools (P0):** as in the checklist.
   - **Long-Call Stability & Empathy Capping (P1):** Empathetic fillers and apologies are capped at 1 per call.
   - **Tool Conversational Pacing & Docstring Sufficiency (P2 — User-Approved Only):** List tools missing pacing as P2 with an Open Question asking whether to add pacing to that specific tool.

1. **Synthesize & Present Unified Prioritized Report:** Combine Pass 1 and Pass 2 into one assessment. **Always generate a markdown table** of all P0, P1, and P2 issues:

   - **Executive Summary:** Overall readiness status (`PASSED` / `FAILED`) and issue counts for P0, P1, and P2.
   - **Prioritized Assessment Table** with columns:
     - **Priority:** `🔴 P0`, `🟡 P1`, or `🟢 P2`.
     - **Issue Description:** Issue code, concise explanation, and clickable links to affected files with line numbers.
     - **Possible Resolution:** Concrete, non-destructive remediation steps.
     - **Open Questions:** **Only** for genuine conflicts or business-logic ambiguity (contradictions, unevaluated tag swaps, prosody, tool pacing approval). For deterministic fixes, state `*(None - deterministic fix)*`.

   **Table Schema Example:**

   ```markdown
   | Priority | Issue Description | Possible Resolution | Open Questions |
   | :--- | :--- | :--- | :--- |
   | 🔴 **P0** | **`CV203`**<br>`app.json` `synthesizeSpeechConfigs['en-US'].instruction` holds a Director's Note. | Clear it to `""` or replace with a speaking style below 12 words; Composite v2 is steered by inline tags. | Clear speaking style or keep a short phrase (<12 words)? |
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

   - **Model:** `modelSettings.model` → `gemini-composite-v2` in `app.json` (added if missing) and in agent configs that override it with an older Composite model; `modelSettings.temperature` → `1.0` in `app.json`. Non-Composite models are left for the user.
   - **Speaking style:** clears every `synthesizeSpeechConfigs.<lang>.instruction`, `customVoiceSamples[].voiceInstruction`, and Director's Note field to `""` (or, if the user wants to keep a brief speaking style, set it to a phrase below 12 words).
   - **Audio tags, in every app text file** (`*.txt, *.json, *.jsonl, *.md, *.py, *.yaml, *.yml`): `[tag]` → `<tag>`, tags-to-replace swapped (`<neutral>` deleted), remap-created duplicates in tag lists removed. Prosody rates are kept verbatim (`[prosody rate=85%]` → `<prosody rate="85%">`). Quotes are escaped inside JSON strings.

   It does **not** add or change voices (CV204 is reported for the user to decide), move tags within sentences, rewrite prose, swap unevaluated tags, or touch speaking style presets in prompts; those are manual. JSON config edits rewrite only the changed values and keep the file's formatting (a full re-serialization happens only when `modelSettings` must be added). Review the diff.

1. **Merge `Affective_Delivery_and_Voice_Guidelines` into the Global Instruction (P0 Mandatory):**

   - Read the existing global instruction in full. **Merge, don't overwrite:** put the *Audio Tag Format* bullets into the existing voice or affective section; add the full template from [natural_speech_patterns.md §1.5](references/natural_speech_patterns.md#15-production-prompt-template-global-instruction) only when none exists. Match the file's heading and bullet style.
   - If a new `global_instruction.txt` is created beside `app.json`, **add `"globalInstruction": "global_instruction.txt"` to `app.json`**.
   - Ask about prosody before adding any `<prosody rate>` ([tag_reference.md §7](references/tag_reference.md#7-prosody-rates)). Without prosody, omit the two prosody bullets.
   - Do **not** add a text normalization block.
   - Update conversational sub-agent instructions to reference `Affective_Delivery_and_Voice_Guidelines` (don't copy the block into them).

1. **Manual Tag Fixes (P0 Mandatory):**

   - **Old-format rules (CV215):** rewrite *"wrap tags in square brackets"*, *"never stack tags"*, *"use bracketed delivery tags … never speak a bracketed tag aloud"*, *"do not bunch all tags at the front"*. Afterwards, search the prompts for "bracket".
   - **Speaking style presets (CV215):** remove Director's Note / Audio Profile text and static accent or pitch presets from prompts, with user confirmation.
   - **Placement (CV214):** move mid-sentence style tags in example utterances to the sentence start; add punctuation before mid-sentence pause tags.
   - **Closing tags (CV213):** delete rules that require closing tags; rewrite `… </prosody>` to the `<prosody rate="100%">` reset form, keeping the rate.
   - **Unrecognized bracketed words:** a typo (`[clam]`) or an unlisted sound is an audio tag: convert it. A variable or placeholder is not: leave it.
   - **Prose left awkward by a deleted `<neutral>` or a remap** (*"use `<warm>` to greet and `<warm>` to close"*): reword.

1. **Systematic Cross-Scope Contradiction Clean-up & Prompt Refactoring (P0 Mandatory):**

   - **Audio Tag & Prosody Contradictions:** Report every conflict with its file, line, and the quoted sentences, then ask the user which rule wins (keep one, narrow one's scope, use a different tag). Edit the losing rule in place. Never resolve them yourself; the one exception is rules that require closing tags, which you remove.
   - **Spoken Transfer Announcements vs. Silent Callbacks/Tools:** Search for `"transfer you"`, `"transferring you"`, `"live agent"`, `"representative"`. If a callback or handoff tool handles the transfer message, harmonize the prompt to a silent handoff.
   - **Invalid Tool Invocation Markup:** Search for `\{@TOOL:\s*[^}\s]+\s*\(` and replace with clean `{@TOOL: tool_name}` references.
   - **Pre-Call Tool Pacing vs. Output Grounding:** Permit pacing phrases before latency-sensitive tools while keeping handoff/wrap-up tools silent.
   - **"Say Exactly" vs. Anti-Repetition Deadlocks, Micro-Bans, Loop Caps, `<examples>` Debt, Text Variable Mutations, Redundant Formatting Rules, Reflexive Closings, Language Switchers:** as in the checklist.
   - **Prohibited XML Refactoring (I015):** Rephrase prohibited platform tags into plain language without deleting domain logic.
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

   No file may still contain a `[tag]`-style audio tag (CV210 must be clear).

1. **Verify Checklist & Summarize:** Run through the checklist above, then end with a summary in this order: (1) script edits per file; (2) manual fixes with file and line; (3) global instruction changes and the prosody decision; (4) contradictions with the user's resolution or still open; (5) agents that explicitly override the global rules; (6) cross-app hand-offs, or "none"; (7) remaining findings the user chose to keep.

1. **SCRAPI Deployment Lifecycle (for Deployed Agents):**

   ```bash
   # 1. Export the deployed agent configuration from CXAS
   uv run cxas pull "<APP_RESOURCE_OR_ID>" --target-dir ./workspace

   # 2. Run local Composite v2 remediation, then steps 2-7 above
   uv run python3 .agents/skills/cxas-composite-voice-agent-optimizer/scripts/audit_agent.py \
     --workspace=./workspace --remediate

   # 3. Run SCRAPI structural linter
   uv run cxas lint

   # 4. Deploy the optimized configuration back to CXAS
   uv run cxas push --app-dir ./workspace --to "<APP_RESOURCE_OR_ID>"
   ```

______________________________________________________________________

## Reference Documentation

- [Tag Reference](references/tag_reference.md): Angle-bracket syntax, reliability tiers, tags to replace, unevaluated tags, delivery effects, validated combinations, prosody rates, and known side effects.
- [Natural Speech Patterns & Anti-Looping Guide](references/natural_speech_patterns.md): 7 affective registers, the Negative-Emotion Latch, the `Affective_Delivery_and_Voice_Guidelines` template, micro-pauses, bridge words, and empathy capping.
- [Global & Agent Instruction Guidelines](references/instructions_guide.md): Platform baseline vs. application responsibilities, contradiction resolution, and prompt hygiene checklists.
- [Tool Design & Conversational Pacing](references/tool_design_and_pacing.md): Tool docstring contracts, spoken pacing phrases before tool execution, payload contamination prevention, and execution standards.
