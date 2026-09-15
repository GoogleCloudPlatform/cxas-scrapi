---
name: cxas-composite-voice-agent-optimizer
description: >-
  Audits, optimizes, and remediates CXAS agent configurations for Gemini Composite V1 voice naturalness,
  persona styling, and multi-language coverage directly in local workspaces with cxas-scrapi.
  Generates prioritized HTML readiness reports (P0/P1/P2) and applies automated fixes.
---

# CXAS Composite Voice Agent Optimizer

This skill audits, optimizes, and remediates Google Cloud CX Agent Studio (CXAS) and Customer Engagement Suite (CES) agent configurations for **Gemini Composite V1** voice naturalness, persona stability, and multi-language parity.

All core operations execute purely on local workspace files (`app.json`, `global_instruction.txt`, `agents/*/instruction.txt`, `tools/`). The `cxas` CLI can be used to manage local agent workspaces (`cxas pull`, `cxas lint`, `cxas push`).

______________________________________________________________________

## When to Use This Skill

Activate this skill when:

- **Adapting to Composite Models:** Evaluating or migrating an existing or new CXAS agent configuration to Gemini Composite V1.
- **Generating Readiness Reports:** Generating a prioritized (P0/P1/P2) HTML/Markdown assessment report of required voice adaptations when upgrading a CXAS agent to Gemini Composite V1.
- **Optimizing for Composite Models:** Optimizing a CXAS agent to apply best practices for Gemini Composite V1.

**When NOT to use this skill:**

- Standard text-only chat agents without voice/audio synthesis.
- Non-composite standard TTS/STT pipelines.
- Generic non-voice dialog flow refactoring.

______________________________________________________________________

## ⚠️ Fundamental Rule: Non-Destructive Preservation of Existing Instructions

**CRITICAL — DO NOT REMOVE EXISTING INSTRUCTIONS:** When optimizing an agent for Gemini Composite V1, you MUST NOT delete, remove, or strip existing business logic, domain instructions, taskflows, steps, or operational rules from `global_instruction.txt`, `agents/*/instruction.txt`, or tool docstrings.

All adaptations must be strictly **additive and non-destructive**:

1. **Preserve Full Instruction Sets:** Retain all existing domain instructions, guardrails, step transitions, and business logic verbatim.
2. **Relocate Static Voice & Speech Guidance to Director's Notes:** Identify static voice, speech delivery, accent, vocal tone, and acoustic pacing presets inside `global_instruction.txt` and `agents/*/instruction.txt`, migrate/consolidate them into the global Director's Note in `app.json` (`synthesizeSpeechConfigs`), and remove them from agent text prompts to prevent context waste and instruction dilution. *(Note: Static voice personas and accent profiles belong exclusively in Director's Notes in `app.json`. However, dynamic turn-by-turn emotion recognition, the 7 affective registers, and inline empirical audio tags MUST remain defined in `global_instruction.txt` under `<Affective Delivery and Voice Guidelines>` per `references/natural_speech_patterns.md`).*
3. **Rephrase Prohibited Tags Without Deleting Logic:** When resolving prohibited platform XML tags (strictly restricted to the explicit prohibited list: `<state_update>`, `<context>`, `<reasoning>`, `<thought>`, `<internal>`, `<call_tool>`, `<parameter_update>`, `<variable_update>`, `<voice_lock>`, `<voice_output>`, `<state>`, `<transition>`, `<transitions>`, and banned legacy CamelCase tags like `<Agent>`, `<Role>`, `<Persona>`), rephrase the tag references into plain natural language descriptions (e.g., *"system context"*, *"state update"*) rather than deleting the surrounding rules or instructions. Do NOT flag or check all general/standard XML tags.
4. **Augment Tool Docstrings Additively (Only if Insufficient):** In `tools/*/python_function/python_code.py`, preserve all existing descriptions, parameter documentation, and implementation details. Only append or integrate explicit `When to Call:` and `When NOT to Call:` execution boundaries if the existing docstring or description is missing, ambiguous, or insufficient.
5. **Harmonize Conflicting Directives Collaboratively:** When resolving contradictory instructions across scopes, ask the user which behavior to preserve and adjust the wording to eliminate the contradiction without deleting core domain logic.
6. **Enrich Spoken Cues Incrementally:** Add natural voice cues (ellipses `...`, brief bridge words like *"umm..."*) into response instructions without altering the core messaging or domain content.

______________________________________________________________________

## Checklist & Inspection Gates

The optimizer evaluates agent configurations against a prioritized checklist:

### 🔴 Priority P0: Critical Synthesis Blockers, Contradictions & Instruction Clean Up (Must Fix First)

1. [ ] **Audio Profile & Director's Note Configuration:** Add Audio Profile & Director's Note to `app.json` under `synthesizeSpeechConfigs`. Preserve the complete Director's Note with mandatory trailing `## Transcript:\n` hook to prevent style prompt leakage into spoken audio.
2. [ ] **Relocate Voice, Accent & Speaking Instructions to Director's Notes:** Relocate all static voice, accent, pronunciation, delivery style, vocal tone, pitch, speaking pace, and speech-related directives from `global_instruction.txt` and `agents/*/instruction.txt` into `app.json` under `audioProcessingConfig.synthesizeSpeechConfigs` (Director's Note and Audio Profile). Placing static voice/speech directives in agent instructions is ineffective, wastes reasoning context tokens, and causes model confusion.
3. [ ] **Natural Language Accent Strings:** Set Accent using Natural Language (e.g., `Accent: American English`, `Accent: Contemporary Irish English`, `Accent: Australian English`, `Accent: British English`, `Accent: Latin American Spanish`) rather than locale codes (`en-US`).
4. [ ] **Eliminate Prohibited Platform Tags:** Eliminate prohibited platform tags (strictly restricted to the explicit list: `<state_update>`, `<context>`, `<reasoning>`, `<thought>`, `<internal>`, `<call_tool>`, `<parameter_update>`, `<variable_update>`, `<voice_lock>`, `<voice_output>`, `<state>`, `<transition>`, `<transitions>`, and banned legacy CamelCase tags like `<Agent>`, `<Role>`, `<Persona>`) which trigger thought-leakage regex safety filters. Do NOT check or flag general XML tags outside this explicit list.
5. [ ] **Global Voice Guidelines & Emotion Attunement:** If global instructions already exist (whether in `global_instruction.txt`, inline within `app.json`, or directly in the agent instructions), append and integrate the canonical `<Affective Delivery and Voice Guidelines>` block (from `references/natural_speech_patterns.md` §1.5) into that existing location. If a new `global_instruction.txt` is created, **ensure to add and reference it in `app.json`** (under `"globalInstruction": "global_instruction.txt"`). This block instructs the model to: (1) dynamically evaluate caller emotion on every turn into one of 7 core affective registers (`angry`, `sad`, `anxious`, `frustrated`, `confused`, `positive`, `neutral`); (2) enforce acute emotion precedence (`angry` > `sad` > `anxious` > `frustrated` > `confused` > `positive` > `neutral`); (3) enforce the Negative-Emotion Latch (never reverting to a cheerful tone when an upset caller gives terse/neutral replies until explicit relief); (4) weave verified inline empirical audio tags (`[prosody rate="65%"]`, `[slow]`, `[seriousness]`, `[sigh]`, `[short pause]`, `[uhm]`, `[whispers]`, `[positive]`, `[neutral]`); (5) apply voice texture rules (1–3 tags/turn, no adjacent stacked tags, ellipses `...` micro-pauses, localized bridge words, digit clustering, no exclamation marks, max 1 apology); and (6) execute sub-agent transitions and escalation/wrap-up tools silently. Additionally, conversational sub-agents' `instruction.txt` must explicitly reference and enforce `<Affective Delivery and Voice Guidelines>` in their `<response_protocol>` or persona.
6. [ ] **Cross-Scope Contradictory Instruction Resolution:** Detect and resolve all mutually conflicting instructions across `global_instruction.txt`, `agents/*/instruction.txt`, tool descriptions/docstrings, and callbacks. Systematically scan and resolve:
   - *Spoken Transfer Announcements vs. Silent Callbacks/Tools:* When `after_model_callbacks` or handoff tools (`escalation_call`, `call_wrap_up`) handle transfer announcements and farewell audio, agent prompt directives commanding the agent to speak transfer messages (e.g., *"say 'I am transferring you to a live agent now.'"*) MUST be harmonized to silent handoffs to prevent double-speaking.
   - *Invalid Tool Invocation Markup:* Eliminate tool markup containing embedded arguments (e.g., `{@TOOL: tool_name(arg="val")}`) and convert them to valid `{@TOOL: tool_name}` references with parameters described in natural language text.
   - *Pre-Call Tool Pacing vs. Output Grounding:* Harmonize prompts that forbid speaking before tool returns with tool docstrings that require pre-call conversational pacing phrases before latency-sensitive lookups.
   - *"Say Exactly" vs. Anti-Repetition Deadlocks:* Remove blanket repetition bans (e.g., *"Never repeat any prompt or confirmation question verbatim across retry attempts"*) that clash with verbatim compliance, authentication, or legal disclosure requirements (e.g., confirming a 6-character booking reference). Allow explicit multi-turn retry ladders (Strike 1, Strike 2) to govern rephrasing instead of a global ban.
7. [ ] **Strip Negative Conversational Micro-Bans (Filler & Bridge Prohibitions):** Remove prohibitions against natural conversational connective words (`"Sure"`, `"Okay"`, `"One moment"`, `"Let's see"`, `"I understand"`). Negative bans on conversational fillers contradict voice warmth and prevent the agent from delivering natural conversational pacing phrases before long-running tool executions.
8. [ ] **Decouple Global Loop Prohibitions from Local Escalation Ladders:** Remove blunt turn-count repetition caps (e.g., *"Strictly prohibited from repeating the same question > 2 times"*) that prematurely abort structured multi-turn escalation flows. Rely on explicit, stateful retry ladders to govern escalation deterministically.
9. [ ] **Prune Monolithic Few-Shot `<examples>` Debt:** Remove large legacy dialog transcripts from prompt text. Legacy examples accumulate format drift, violate newer negative operational rules, and bloat reasoning context. Maintain vetted golden test cases in evaluation suites instead.
10. [ ] **Eliminate Text-Based Variable Setting Antipatterns:** Avoid text-based variable setting (e.g., `"Set booking_verified = true"` or `"Set user_language = es"`); state mutations cannot occur via raw text output in CES. Use structured tool invocations (e.g., `update_booking_status`) instead.
11. [ ] **Remove Redundant Platform Formatting Rules:** Remove instructions mandating plaintext, forbidding markdown headers, or instructing digit spacing; these are handled automatically by the platform baseline.
12. [ ] **Eliminate Reflexive Turn Closings:** Eliminate reflexive turn closings (avoid ending every turn with *"Is there anything else?"*).
13. [ ] **Eliminate Deprecated Language-Switching Tools:** Remove dynamic language-switching tools (`language_switcher`, `en_to_es`); session language is established at IVR/session initialization and dynamic switching tools add latency and risk hallucination.
14. [ ] **Model Settings Configuration:** Set `modelSettings.model` to `"gemini-composite-v1"` and `modelSettings.temperature` to `1.0` (prevents acoustic repetition loops).
15. [ ] **Incorporate Natural Speech Cues:** Incorporate natural speech cues (ellipses `...` and brief bridge words like `"um"`, `"hmm"`, `"let's see"`) in LLM response instructions.
16. [ ] **Preserve Domain Logic & Taskflows (MANDATORY Non-Destructive Rule):** Never delete, wipe, or strip existing business logic, validation rules, or negative operational constraints. Optimization must be strictly additive and restorative.

### 🟡 Priority P1: Multi-Language Parity, Session Stability & Call Flow

1. [ ] **Multi-Language Session Variable (`user_language`):** When the application is multi-lingual (declares `languageSettings.supportedLanguageCodes` with multiple locales), ensure `user_language` or `app_language` is declared in `app.json.variableDeclarations` to track active caller language and prevent language drift. (Single-language/unilingual apps skip this check).
2. [ ] **Multi-Language Voice Parity:** Every configured locale in `languageSettings.supportedLanguageCodes` has a matching entry in `synthesizeSpeechConfigs` with localized Director's Notes, appropriate voice IDs, and native bridge words.
3. [ ] **Verify Long-Call Stability (5+ Minutes):** Verify long-call stability (5+ minutes) without speaker drift, voice fry, or turn exhaustion.
4. [ ] **Minimize Proactive Unnecessary Call Transfers:** Transfer only on explicit customer escalation or hold the line and be rigorous on conversational design. Sub-agent handoffs execute silently via tool calls without speaking internal transition jargon.
5. [ ] **Employ Validated Physical Acoustic Tags:** Employ validated physical acoustic tags (e.g., `[whispers]`, `[sigh]`, `[chuckles]`, `[slow]`, `[seriousness]`).

### 🟢 Priority P2: Lowest Priority — Tool Conversational Pacing & Docstring Hygiene (User-Approved Only)

1. [ ] **Tool-Level Conversational Pacing Directives (Lowest Priority — User-Approved Only):** Spoken conversational pacing phrases (*"Before calling this tool, speak a brief, natural conversational pacing phrase..."*) are the **lowest priority (P2)**. They MUST NOT be automatically applied across all tools. Pacing directives should be added **ONLY for specific latency-sensitive tools that are explicitly selected and approved by the person executing the skill**.
2. [ ] **Tool Docstring Sufficiency (Only if Insufficient):** Inspect declared active tools. If the existing tool docstring or description is incomplete or ambiguous, refine it with clear positive/negative execution boundaries (`When to Call:` and `When NOT to Call:`) upon user approval.

______________________________________________________________________

## Modes of Execution

```
                       ┌──────────────────────────────────────────────┐
                       │    CXAS Composite Voice Agent Optimizer      │
                       └──────────────────────┬───────────────────────┘
                                              │
                     ┌────────────────────────┴────────────────────────┐
                     ▼                                                 ▼
     ┌───────────────────────────────┐                 ┌───────────────────────────────┐
     │  Mode 1: Report Generation    │                 │       Mode 2: Fix Mode        │
     │  (Readiness Assessment)       │                 │(Audio Patch & Guided Refactor)│
     └───────────────┬───────────────┘                 └───────────────┬───────────────┘
                     │                                                 │
     1. Discover workspace configuration               1. Execute `--remediate` for `app.json`
     2. Run multi-pass acoustic & tool audit           2. Declare `user_language` (if multi-lingual)
     3. Generate prioritized Markdown report           3. Contextually refactor XML, prompts & tags
     4. Review prioritized P0/P1/P2 plan               4. Refactor Python tool docstrings & pacing
                                                       5. Run verification audit & `cxas lint`
```

______________________________________________________________________

### Mode 1: Report Generation Mode (Comprehensive Readiness Assessment)

Assesses an existing CXAS agent workspace, checks **all** checklist inspection gates across both static configurations and semantic prompt policies, and generates a unified prioritized report (P0/P1/P2) detailing required voice adaptations for Gemini Composite V1.

#### Workflow Steps:

1. **Workspace Discovery:** Locate `app.json`, `global_instruction.txt`, sub-agent instructions (`agents/*/instruction.txt`), and tool definitions (`tools/`) in the workspace.

2. **Pass 1 — Execute Automated Structural & Audio Audit:** Run the local auditor CLI to evaluate deterministic configuration rules:

   ```bash
   # Generate baseline structural Markdown report
   python3 .agents/skills/cxas-composite-voice-agent-optimizer/scripts/audit_agent.py \
     --workspace=. --report
   ```

   *(What this covers: `app.json` `synthesizeSpeechConfigs` (A007), Director's Notes headers (A007), trailing `## Transcript:\n` hooks (A007), natural language accents (A008), multi-language audio profile parity (A009), model settings and sampling temperature (A010), prohibited platform XML tags (I015), unregistered template variables (V104), and conversational tool pacing (T014 - P2)).*

3. **Pass 2 — Semantic Voice & Policy Review (LLM Checklist Evaluation):** Actively evaluate the workspace instructions against qualitative checklist gates not covered by static scripts:
   - **Audit Global Instructions for Voice Guidelines & Emotion Tags (P0 Critical):** Verify whether global instructions exist (in `global_instruction.txt`, inline within `app.json`, or directly in the agent instructions) and define the canonical `<Affective Delivery and Voice Guidelines>` block with all 7 affective registers (`angry`, `sad`, `anxious`, `frustrated`, `confused`, `positive`, `neutral`), acute emotion precedence, the Negative-Emotion Latch, and inline empirical audio tags (`[prosody rate="65%"]`, `[slow]`, `[seriousness]`, `[sigh]`, `[short pause]`, `[uhm]`, `[whispers]`, `[positive]`, `[neutral]`). Flag as `🔴 P0` if `<Affective Delivery and Voice Guidelines>` is missing from the global instructions. If a new `global_instruction.txt` will be created, verify that `app.json` will be updated to link it (`"globalInstruction": "global_instruction.txt"`).
   - **Relocate Static Voice & Speech Directives to Director's Notes (P0):** Review `global_instruction.txt` and `agents/*/instruction.txt` for static voice styling, accent directives, vocal tone, speech pace, pronunciation rules, or `<voice_lock>`/`<voice_output>` blocks that belong in Director's Notes rather than reasoning prompts. With Gemini Composite V1, Director's Notes configured in `app.json` are the only way to provide static speech presets to the TTS model.
   - **Cross-Scope Contradictory Instructions Audit (P0 Critical):** Review `global_instruction.txt`, `agents/*/instruction.txt`, tool docstrings, and callbacks to detect conflicting directives. Specifically scan for:
     * *Spoken Transfer Announcements vs. Silent Callbacks/Tools:* Prompt instructions commanding the agent to speak transfer messages (e.g., *"I am transferring you to a live agent now"*) when callbacks (e.g. `after_model_callbacks`) or tools (`escalation_call`, `call_wrap_up`) inject transfer messaging, causing double-speaking.
     * *Invalid Tool Invocation Markup:* Prompt instructions containing tool calls with embedded arguments (e.g., `{@TOOL: tool_name(arg="val")}`).
     * *Tool Pacing vs. Output Grounding:* Prompts mandating complete silence before tool return vs. tool docstrings requiring pre-call conversational pacing phrases.
     * *"Say Exactly" vs. Anti-Repetition Deadlocks:* Conflicts between exact verbatim compliance/verification phrases and blanket anti-repetition rules.
   - **Strip Negative Conversational Micro-Bans (P0):** Identify and remove blanket prohibitions against natural conversational connective words (`"Sure"`, `"Okay"`, `"One moment"`, `"Let's see"`, `"I understand"`) that impede natural speech and tool pacing.
   - **Decouple Global Loop Prohibitions from Local Escalation Ladders (P0):** Replace blunt turn-count repetition caps with deterministic multi-strike retry and escalation ladders.
   - **Prune Monolithic Few-Shot `<examples>` Debt (P0):** Strip large legacy dialog transcripts from prompt text to prevent format drift, rule contradiction, and reasoning token bloat.
   - **Eliminate Text-Based Variable Setting Antipatterns (P0):** Inspect instruction files for raw text variable mutation statements (e.g., `Set user_language = es`, `Set booking_verified = true`). In CES, state mutations cannot occur via raw output text; verify that state changes are mediated through tool calls (e.g., `update_booking_status`) instead.
   - **Remove Redundant Platform Formatting Rules (P0):** Remove instructions mandating plaintext, forbidding markdown headers, or instructing digit spacing, as these are natively handled by the platform.
   - **Eliminate Reflexive Turn Closings (P0):** Eliminate reflexive turn closings (avoid ending every turn with `"Is there anything else?"`).
   - **Eliminate Deprecated Language-Switching Tools (P0):** Deprecate dynamic language switching tools (`language_switcher`, `en_to_es`); session language is established at IVR/session initialization.
   - **Speech Texture & Natural Hesitation Directives (P0):** Inspect instructions for micro-pause ellipses (`...`), natural hesitation bridge words, and digit clustering rules.
   - **Long-Call Stability & Empathy Capping (P1):** Verify that empathetic fillers and apologies are capped to a maximum of 1 occurrence per call.
   - **Tool Conversational Pacing & Docstring Sufficiency (P2 — Lowest Priority / User-Approved Only):** Spoken conversational pacing phrases are **lowest priority (P2)** and MUST NOT be assumed or applied across all tools. In the readiness assessment, list tools missing conversational pacing as Priority P2 with an explicit Open Question asking the person executing the skill whether they approve adding pacing to that specific tool. Only recommend adding docstring contracts (`When to Call:` and `When NOT to Call:`) if the existing docstring is ambiguous or insufficient.

4. **Synthesize & Present Unified Prioritized Report:** Combine findings from both Pass 1 (Static) and Pass 2 (Semantic) into a single structured assessment. **Always generate a comprehensive markdown table** of all P0, P1, and P2 issues with the following structure:

   - **Executive Summary:** Overall readiness status (`PASSED` / `FAILED`) and issue count breakdown across P0, P1, and P2.
   - **Prioritized Assessment Table:** A unified table with the following columns:
     - **Priority:** `🔴 P0` (Critical Voice & Synthesis Blockers), `🟡 P1` (High Impact Multi-Language & Stability), or `🟢 P2` (Lowest Priority: Tool Pacing, Hygiene & Texture).
     - **Issue Description:** Issue identifier/code, concise explanation of the problem, and clickable markdown links to affected files with exact line numbers.
     - **Possible Resolution:** Concrete, actionable, non-destructive remediation steps complying with the non-destructive guidelines.
     - **Open Questions:** **Add open questions ONLY when there is a genuine conflict or business-logic ambiguity that cannot be resolved without clarification from the user** (e.g., asking which specific latency-sensitive tools are approved for conversational pacing phrases, cross-scope contradictory instructions between prompt silence and tool pacing, or conflicting brand routing rules). For deterministic/standard technical fixes (e.g., missing Director's Note header, standard `app.json` schema patches), omit questions or state `*(None - deterministic fix)*`.

   **Table Schema Example:**
   ```markdown
   | Priority | Issue Description | Possible Resolution | Open Questions |
   | :--- | :--- | :--- | :--- |
   | 🔴 **P0** | **`MISPLACED_VOICE_INSTRUCTIONS`**<br>`instruction.txt:L8` contains voice tone and accent directives. | Relocate voice/accent instructions into `app.json` Director's Note and remove from agent prompt. | *(None - deterministic fix)* |
   | 🔴 **P0** | **`CONTRADICTORY_INSTRUCTIONS`**<br>`instruction.txt:L20` forbids speech before tool calls, but `tools/search.py` requires pacing. | Harmonize prompt to permit conversational pacing before backend lookup. | Does the business require total silence during tool execution or is conversational pacing preferred? |
   | 🔴 **P0** | **`MISSING_DIRECTORS_NOTE`**<br>`app.json:L6` lacks Director's Note and `## Transcript:\n` hook. | Inject standardized Director's Note with Audio Profile and trailing hook. | *(None - deterministic fix)* |
   | 🟢 **P2** | **`MISSING_TOOL_CONVERSATIONAL_PACING`**<br>`tools/search_flights/python_code.py` lacks spoken pacing phrase. | Add varied pacing phrase to docstring if approved by user. | Do you approve adding a pre-call conversational pacing phrase to `search_flights`? |
   ```

______________________________________________________________________

### Mode 2: Fix Mode

Combines **automated in-place remediation** for structural audio configurations with **context-aware semantic prompt refactoring** and **tool docstring engineering** to ensure complete compliance.

#### Workflow Steps:

1. **Execute Automated Audio Remediation:** Run the auditor in remediation mode to automatically patch `app.json` audio settings:

   ```bash
   python3 .agents/skills/cxas-composite-voice-agent-optimizer/scripts/audit_agent.py \
     --workspace=. --remediate
   ```

   **What `--remediate` safely patches in `app.json`:**

   - **Audio Profile & Director's Notes:** Injects or updates `synthesizeSpeechConfigs` in `app.json` with complete Audio Profile, Director's Note, and trailing `## Transcript:\n` hooks.
   - **Natural Language Accent Strings:** Replaces raw ISO codes with natural language descriptions (e.g., `Accent: American English`, `Accent: Spanish accent`).
   - **Model & Sampling Calibration:** Sets `modelSettings.model = "gemini-composite-v1"` and `modelSettings.temperature = 1.0` to eliminate acoustic repetition loops.
   - **Multilingual Coverage & Language Drift Prevention:** Injects symmetrical localized voice entries and default Chirp3-HD voices for all declared supported language codes.

2. **Author / Inject `<Affective Delivery and Voice Guidelines>` into Global Instructions (P0 Mandatory):**

   - **If global instructions exist** (whether in `global_instruction.txt`, inline within `app.json`, or directly in the agent instructions), append and integrate the complete `<Affective Delivery and Voice Guidelines>` block into that existing location.
   - **If a new `global_instruction.txt` is created** at the workspace root, **ensure to add and reference it in `app.json`** (e.g., set `"globalInstruction": "global_instruction.txt"` in `app.json`) so the CXAS/CES platform recognizes and compiles it.
   - Inject the complete production `<Affective Delivery and Voice Guidelines>` template from `references/natural_speech_patterns.md` §1.5 containing:
     - **7 Core Affective Registers & Tag Mappings:** `angry` (`[prosody rate="65%"]`, `[seriousness]`, `[sigh]`), `frustrated` (`[slow]`, `[short pause]`), `anxious` (`[slow]`, `[seriousness]`, `[short pause]`), `sad` / `distressed` (`[prosody rate="65%"]`, `[sigh]`, `[whispers]`), `confused` (`[slow]`, `[short pause]`, `[uhm]`, `[neutral]`), `positive` (`[positive]`, `[happy]`), and `neutral` (`[neutral]`, `[short pause]`).
     - **Acute Emotion Precedence:** `angry` > `sad` > `anxious` > `frustrated` > `confused` > `positive` > `neutral`.
     - **The Negative-Emotion Latch:** Never switch to a cheerful tone when an upset customer provides terse or neutral answers ("yes", "okay", reading confirmation codes); maintain an unhurried, patient register until explicit relief.
     - **Voice Texture & Speech Delivery Rules:** 1 to 3 tags per turn maximum, no adjacent stacked tags, ellipses `...` micro-pauses, localized bridge words (`"Let's see..."`, `"Got it,"`, `"Sure,"`, `"Alright,"`), digit clustering, no exclamation marks, and empathy capping (strictly max 1 apology per session).
     - **Silent Handoffs & Callbacks:** Handoffs and wrap-up tools execute silently.
   - Update all conversational sub-agent `instruction.txt` files (in their `<response_protocol>` or `<persona>`) to explicitly instruct the agent to evaluate customer emotion on every turn per `<Affective Delivery and Voice Guidelines>` and weave in verified empirical audio tags.

3. **Systematic Cross-Scope Contradiction Clean-up & Prompt Refactoring (P0 Mandatory):**

   - **Scan & Resolve Spoken Transfer Announcements vs. Silent Callbacks/Tools (P0 Critical):**
     - Search all instruction files for transfer wording: `"transfer you"`, `"transferring you"`, `"live agent"`, `"representative"`.
     - Inspect whether `after_model_callbacks` or handoff tools (`escalation_call`, `call_wrap_up`) inject transfer audio or handle transfer messaging automatically.
     - If the system/callback handles the transfer message, **harmonize the prompt instruction to command silent handoff** (e.g., *"address their question before initiating the handoff, then proceed with the silent escalation handoff (do NOT speak a transfer message; the system will handle the transfer message automatically)"*). **Never let the prompt command speaking a transfer message that duplicates callback or tool audio.**
   - **Scan & Clean Invalid Tool Invocation Markup (P0 Critical):**
     - Search all instruction files for regex `\{@TOOL:\s*[^}\s]+\s*\(` (tool tags with embedded function arguments).
     - Remove embedded arguments from inside `{@TOOL: ...}` and replace with clean `{@TOOL: tool_name}` references, describing any parameter values in natural language text.
   - **Harmonize Pre-Call Tool Pacing vs. Output Grounding (P0 Critical):**
     - Audit prompts that mandate complete silence before tool return against tool docstrings that require conversational pacing phrases.
     - Harmonize prompt `<response_protocol>` to explicitly permit conversational pacing phrases before latency-sensitive backend tools (`get_available_schedule_windows`, `modify_appointment`, `search_knowledge_agent`), while keeping handoff/wrap-up tools strictly silent.
   - **Eliminate "Say Exactly" vs. Anti-Repetition Deadlocks (P0):** Remove global anti-repetition rules that contradict verbatim compliance, authentication, or legal disclosure requirements (e.g., verifying a 6-character booking reference). Allow explicit multi-turn retry ladders (Strike 1, Strike 2) to specify explicit rephrasing variants rather than imposing a blanket prohibition against repeating exact strings.
   - **Relocate Static Voice & Accent Directives to Director's Notes (P0):**
     - Extract static speech delivery presets, accent declarations, and vocal tone labels from agent instructions.
     - Ensure they are preserved in `app.json` `synthesizeSpeechConfigs` Director's Notes, and strip them from prompt text to prevent context dilution.
   - **Contextual Prohibited XML Refactoring (P0):** Review flagged prohibited internal XML tags (strictly restricted to the explicit list: `<state_update>`, `<thought>`, `<reasoning>`, `<context>`, `<internal>`, `<call_tool>`, `<parameter_update>`, `<variable_update>`, `<voice_lock>`, `<voice_output>`, `<state>`, `<transition>`, `<transitions>`, and banned legacy CamelCase tags like `<Agent>`, `<Role>`, `<Persona>`). Rephrase prohibited tags into plain natural language (e.g., *"system context"*, *"state update"*) rather than deleting domain logic. Do NOT check, flag, or convert standard/custom taskflow XML tags outside this explicit list.
   - **Strip Negative Conversational Micro-Bans (P0):** Eliminate blanket prohibitions against natural conversational connective words (`"Sure"`, `"Okay"`, `"One moment"`, `"Let's see"`, `"I understand"`).
   - **Decouple Global Loop Prohibitions from Local Escalation Ladders (P0):** Remove blunt turn-count repetition caps; rely on explicit, stateful retry ladders to govern escalation deterministically.
   - **Prune Monolithic Few-Shot `<examples>` Debt (P0):** Remove large legacy dialog transcripts from prompt text to eliminate format drift and reasoning token bloat.
   - **Eliminate Text Variable Mutations (P0):** Replace `"Set variable = value"` with tool invocations (e.g., `update_booking_status`).
   - **Remove Redundant Platform Formatting Rules (P0):** Remove instructions mandating plaintext, forbidding markdown headers, or instructing digit spacing; these are handled automatically by the platform baseline.
   - **Eliminate Reflexive Turn Closings (P0):** Eliminate reflexive turn closings (avoid ending every turn with `"Is there anything else?"`).
   - **Eliminate Deprecated Language Switchers (P0):** Deprecate dynamic language switching tools (`language_switcher`, `en_to_es`); set session language at session init.
   - **Declare Multi-Language Session Variable (`user_language`):** When the application supports multiple languages (`languageSettings.supportedLanguageCodes`), ensure `user_language` is declared in `app.json.variableDeclarations`.
   - **Sync Declared Tools:** Verify all tool references in agent prompts (e.g. `{@TOOL: ...}`) are declared in the agent's `.json` configuration. Remove or declare missing tools.

4. **Interactive Tool Docstring & Conversational Pacing Refactoring (Lowest Priority - Only for Approved Tools):**

   Tool docstrings serve as explicit runtime execution contracts for Gemini Composite V1 reasoning models. Because tool docstrings encode brand-specific voice texture and business constraints, **they are intentionally NOT auto-remediated blindly via CLI flags**. Instead, they are audited advisory items and remediated interactively only when desired.

   - **Auditing Scope & Priority Tiering:** 
     - **Tool Conversational Pacing (`T014`) is Priority P2 (Lowest Priority / Advisory):** Spoken conversational pacing directives must **ONLY be added for tools explicitly approved by the person executing the skill**. Never auto-generate or bulk-inject pacing directives across all tools without explicit user approval.
     - **Tool Operational Contracts (`When to Call:`, `When NOT to Call:`):** Missing execution bounds on active runtime tools are Priority P1 findings.
     - Unused orphan tools in the `tools/` folder are excluded.

   - **Python Tools Canonical Source of Truth:**
     - For Python tools, ALWAYS author and edit the docstring directly in `tools/<tool_name>/python_function/python_code.py`.
     - **DO NOT** edit the description in `tools/<tool_name>/<tool_name>.json` for Python tools. Modifying `.json` files for Python tools can cause schema desynchronization or get overwritten during build.
     - For OpenAPI, Client, or Data Store tools without Python code, edit their respective configuration `.json` file.

   - **Interactive Step-by-Step Refactoring Process:**
     1. **Review Flagged Tools & Request User Approval:** Inspect findings for missing conversational pacing (`T014`). Ask the person executing the skill which specific latency-sensitive tools (if any) they approve for adding conversational pacing phrases. **Do not modify pacing for any unapproved tool.**
     2. **Tool Execution & Conversational Pacing Design (Only for Approved Tools):**
        - **Conversational Pacing Directives:** For user-approved tools, specify spoken pacing phrases (*"Before calling this tool, speak a brief, natural conversational pacing phrase..."*) to prevent dead air across tool executions. Terminal tools (e.g. session wrap-up, exit, test mocks) are exempt.
        - **Docstring Sufficiency Check (Only if Insufficient):** Check if the existing docstring clearly explains the function, parameters, and execution bounds. If the existing docstring is already clear and sufficient, preserve it. Only author or refine explicit `When to Call:` and `When NOT to Call:` sections if the existing documentation is incomplete, missing, or ambiguous.
        - **Cross-Scope Contradiction Check:** Verify that agent and global instructions do not contradict the tool docstring (e.g., demanding complete silence before tool return while the tool specifies a pacing directive). Ask the user which behavior to preserve and align the directives.
        - **Callback Conflict Check:** If the application uses legacy `after_model_callbacks` or trivia tools to fill wait time, confirm with the user whether to transition to native model-level pacing phrases or align the prompt instructions.
     3. **Solicit User Phrasing & Author Docstring:** Present the proposed docstring structure to the user for the approved tool(s), incorporating:
        - Concise function summary.
        - Conversational pacing directive with multiple natural phrasing options (*"Before calling this tool, speak a brief, natural conversational pacing phrase with varied options (e.g., 'Let me check that for you...', 'Just a minute, let me look it up...', 'Checking that for you now...') to prevent repetitive responses."*).
        - `When to Call:` positive trigger conditions (if needed).
        - `When NOT to Call:` negative operational boundaries (if needed).
     4. **Apply to Python Source Code:** Write the approved docstring into `tools/<name>/python_function/python_code.py`.

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

5. **Run Verification & Quality Gates:** Verify that all audit passes succeed and run the model-specific structural linter:

   ```bash
   # Run verification audit
   python3 .agents/skills/cxas-composite-voice-agent-optimizer/scripts/audit_agent.py \
     --workspace=. --report

   # Run SCRAPI model-specific structural linter for Gemini Composite V1
   cxas lint --model=gemini-composite-v1 --model-only
   ```

6. **Verify Checklist Above:** Run through the entire checklist above and verify that all items are checked off.

7. **SCRAPI Deployment Lifecycle (for Deployed Agents):** When optimizing agents deployed on CXAS / CES:

   ```bash
   # 1. Export the deployed agent configuration from CXAS
   cxas pull "<APP_RESOURCE_OR_ID>" --target-dir ./workspace
   cd ./workspace

   # 2. Run local voice remediation for app.json
   python3 ../.agents/skills/cxas-composite-voice-agent-optimizer/scripts/audit_agent.py \
     --workspace=. --remediate

   # 3. Refactor Python tool docstrings in tools/*/python_function/python_code.py as needed
   # 4. Run SCRAPI structural linter for Gemini Composite V1
   cxas lint --model=gemini-composite-v1 --model-only

   # 5. Deploy the optimized configuration back to CXAS
   cxas push --app-dir . --to "<APP_RESOURCE_OR_ID>"
   ```

______________________________________________________________________

## Reference Documentation

- [Director's Notes & Audio Profile Guide](references/directors_notes_guide.md): Complete schema definitions, global placement rationale, accent normalization tables, and multilingual golden templates.
- [Global & Agent Instruction Guidelines](references/instructions_guide.md): Platform baseline vs. application responsibilities, dialogue sanitization, and prompt hygiene checklists.
- [Empirical Tags Catalog](references/empirical_tags_catalog.md): List of working physical acoustic tags to be used.
- [Natural Speech Patterns & Anti-Looping Guide](references/natural_speech_patterns.md): Micro-pauses (`...`), localized bridge words, digit clustering, and empathy capping.
- [Tool Design & Conversational Pacing](references/tool_design_and_pacing.md): Tool docstring contracts, spoken pacing phrases before tool execution, payload contamination prevention, and execution standards.
