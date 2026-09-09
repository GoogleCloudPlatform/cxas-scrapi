---
name: cxas-composite-voice-agent-optimizer
description: >-
  Audits, optimizes, and remediates CXAS agent configurations for Gemini Composite V1 voice naturalness,
  persona styling, and multi-language coverage directly in local workspaces with cxas-scrapi.
  Generates prioritized HTML readiness reports (P0/P1/P2) and applies automated fixes.
---

# CXAS Composite Voice Agent Optimizer

This skill audits, optimizes, and remediates Google Cloud CX Agent Studio (CXAS) and Customer Engagement Suite (CES) agent configurations for **Gemini Composite V1** voice naturalness, persona stability, and multi-language parity.

All core operations execute purely on local workspace files (`app.json`, `agents/*/instruction.txt`, `tools/`). The `cxas` CLI can be used to manage local agent workspace (`cxas pull`, `cxas lint`, `cxas push`).

______________________________________________________________________

## When to Use This Skill

Activate this skill when:

- **Adapting to Composite Models:** Evaluating or migrating an existing or new CXAS agent configuration to Gemini Composite V1.
- **Generating Readiness Reports:** Generating a prioritized (P0/P1/P2) HTML/Markdown assessment report of required voice adaptations when upgrading CXAS agent to use Gemini Composite V1.
- **Optimizing for Composite Models:** Optimizing a CXAS agent to use the best practices when using Gemini Composite V1.

**When NOT to use this skill:**

- Standard text-only chat agents without voice/audio synthesis.
- Non-composite standard TTS/STT pipelines.
- Generic non-voice dialog flow refactoring.

______________________________________________________________________

## ⚠️ Fundamental Rule: Non-Destructive Preservation of Existing Instructions

**CRITICAL — DO NOT REMOVE EXISTING INSTRUCTIONS:** When optimizing an agent for Gemini Composite V1, you MUST NOT delete, remove, or strip existing business logic, domain instructions, taskflows, steps, or operational rules from `global_instruction.txt`, `agents/*/instruction.txt`, or tool docstrings.

All adaptations must be strictly **additive and non-destructive**:

1. **Preserve Full Instruction Sets:** Retain all existing domain instructions, guardrails, step transitions, and business logic verbatim.
2. **Relocate Voice & Speech Guidance to Director's Notes (P0):** Identify voice, speech delivery, accent, vocal tone, and acoustic pacing instructions inside `global_instruction.txt` and `agents/*/instruction.txt`, migrate/consolidate them into the global Director's Note in `app.json` (`synthesizeSpeechConfigs`), and remove them from agent text prompts to prevent context waste and instruction dilution.
3. **Rephrase Prohibited Tags Without Deleting Logic:** When resolving prohibited platform XML tags (e.g., `<context>`, `<state_update>`), rephrase the tag references into plain natural language descriptions (e.g., *"system context"*, *"state update"*) rather than deleting the surrounding rules or instructions.
4. **Augment Tool Docstrings Additively:** In `tools/*/python_function/python_code.py`, preserve all existing descriptions, parameter documentation, and implementation details. Only append or integrate the required `When to Call:`, `When NOT to Call:`, and conversational pacing cues.
5. **Harmonize Conflicting Directives Collaboratively:** When resolving contradictory instructions across scopes, ask the user which behavior to preserve and adjust the wording to eliminate the contradiction without deleting core domain logic.
6. **Enrich Spoken Cues Incrementally:** Add natural voice cues (ellipses `...`, brief bridge words like "umm...") into response instructions without altering the core messaging or domain content.

______________________________________________________________________

## Checklist & Inspection Gates

The optimizer evaluates agent configurations against a prioritized checklist:

### 🔴 Priority P0: Critical Synthesis Blockers & Leakage (Must Fix First)

1. [ ] **Audio Profile & Director's Note Configuration:** Add Audio Profile & Director's Note to `app.json` or Global Voice Settings in CXAS. Preserve the whole Director's Note (with mandatory trailing `## Transcript:\n` hook) to prevent style prompt leakage into spoken audio.
1. [ ] **Relocate Voice, Accent & Speaking Instructions to Director's Notes (P0 Critical):** Relocate all voice, accent, pronunciation, delivery style, vocal tone, pitch, speaking pace, and speech-related directives from `global_instruction.txt` and `agents/*/instruction.txt` into `app.json` under `audioProcessingConfig.synthesizeSpeechConfigs` (Director's Note and Audio Profile). With Gemini Composite V1, Director's Notes configured in `app.json` are the **ONLY** mechanism to provide speech and delivery guidance to the TTS synthesis model. Placing voice/speech directives in agent instructions is completely ineffective, wastes reasoning context tokens, and can cause instruction dilution or model confusion.
1. [ ] **Natural Language Accent Strings:** Set Accent using Natural Language (e.g., `Accent: American English`, `Accent: Contemporary Irish English`, `Accent: Australian English`, `Accent: British English`, `Accent: Latin American Spanish`) rather than locale codes (`en-US`).
1. [ ] **Eliminate Prohibited Platform Tags:** Eliminate prohibited platform tags (e.g., `<state_update>`, `<context>`, `<reasoning>`, `<thought>`, `<internal>`, `<call_tool>`, `<parameter_update>`, `<variable_update>`, `<voice_lock>`, `<voice_output>`) which trigger thought-leakage regex safety filters.
1. [ ] **Cross-Scope Contradictory Instruction Resolution:** Detect and resolve contradictory instructions across `global_instruction.txt`, `agents/*/instruction.txt`, and tool descriptions/docstrings. Mutually conflicting directives (e.g., an agent instruction mandating *"Always call the `manage_service_appointment` tool, wait for its response and then generate a response. Never say anything which is not in the tool response."* vs a tool docstring stating *"Before calling this tool, speak a brief, natural conversational pacing phrase with varied options..."*) confuse the reasoning model, causing execution deadlocks, hesitation loops, empty turns, or safety fallbacks. Collaborate with the user by asking targeted questions on which behavior to preserve and harmonize the directives.
1. [ ] **Model Settings Configuration:** `modelSettings.model` is set to `"gemini-composite-v1"` and `modelSettings.temperature` is set to `1.0` (prevents acoustic repetition loops).
1. [ ] **Incorporate Natural Speech Cues:** Incorporate natural speech cues (ellipses `...` and brief bridge words like `"um"`, `"hmm"`, `"let's see"`) in LLM response instructions.

### 🟡 Priority P1: Multi-Language Parity, Session Stability & Call Flow

1. [ ] **Instruction Tool Docstrings & Conversational Pacing Directives:** All tools actively referenced in agent instructions must define unambiguous execution contracts (`When to Call:` and `When NOT to Call:`) and spoken conversational pacing directives (*"Before calling this tool, speak a brief, natural conversational pacing phrase..."*) to prevent caller dead air.
1. [ ] **Multi-Language Session Variable (`user_language`):** When the application is multi-lingual (declares `languageSettings.supportedLanguageCodes` with multiple locales), ensure `user_language` or `app_language` is declared in `app.json.variableDeclarations` to track active caller language and prevent language drift. (Single-language/unilingual apps skip this check).
1. [ ] **Verify Long-Call Stability (5+ Minutes):** Verify long-call stability (5+ minutes) without speaker drift, voice fry, or turn exhaustion.
1. [ ] **Eliminate Reflexive Turn Closings:** Eliminate reflexive turn closings (avoid ending every turn with *"Is there anything else?"*).
1. [ ] **Enforce Anti-Looping Rules:** Enforce anti-looping rules (cap repetitive empathetic filler or apology phrases to max 1 per session; trigger retry escalations after 2 strikes).
1. [ ] **Minimize Proactive Unnecessary Call Transfers:** Transfer only on explicit customer escalation or hold the line and be rigorous on conversational design. Sub-agent handoffs execute silently via tool calls without speaking internal transition jargon.
1. [ ] **Employ Validated Physical Acoustic Tags:** Employ validated physical acoustic tags (e.g., `[whispers]`, `[sigh]`, `[chuckles]`, `[slow]`, `[seriousness]`).


### 🟢 Priority P2: Speech Hygiene, Pacing & Conversational Texture

1. [ ] **Declared Active Tool Contracts & Pacing (P2 Hygiene):** Other active tools declared in agent configurations (but not directly mentioned in instructions) define docstring contracts and conversational pacing directives.
1. [ ] **Multi-Language Voice Parity:** Every configured locale in `languageSettings.supportedLanguageCodes` has a matching entry in `synthesizeSpeechConfigs` with localized Director's Notes, appropriate voice IDs, and native bridge words.
1. [ ] **Avoid Text-Based Variable Setting:** Avoid text-based variable setting (e.g., `"Set login_status = true"` or `"Set keypad_mentioned = true"`); use structured tool invocations for state changes (e.g., `update_login_status`).
1. [ ] **Remove Inert Abstract Tags:** Remove inert abstract tags (e.g., `[empathetic]`, `[warm]`, `[calm]`, `[short pause]`).
1. [ ] **Format Number & Currency Clusters:** Format number and currency clusters for natural, chunked reading (e.g., credit card numbers, phone numbers).
1. [ ] **Eliminate Deprecated Language-Switching Tools:** Remove dynamic language-switching tools (`language_switcher`, `en_to_es`); session language is established at IVR/session initialization and dynamic switching tools add latency and risk hallucination.


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

   *(What this covers: `app.json` `synthesizeSpeechConfigs`, Director's Notes headers, trailing `## Transcript:\n` hooks, sampling temperature, Chirp3-HD voice mapping, natural language accents, declared tool docstring presence, missing pacing directives on active tools, prohibited XML tags, and text variable mutations).*

3. **Pass 2 — Semantic Voice & Policy Review (LLM Checklist Evaluation):** Actively evaluate the workspace instructions against the qualitative checklist gates not covered by static scripts:
   - **Relocate Voice, Accent & Speaking Directives to Director's Notes (P0):** Review `global_instruction.txt` and `agents/*/instruction.txt` for voice styling, accent directives, vocal tone, speech pace, pronunciation rules, or `<voice_lock>`/`<voice_output>` blocks that belong in Director's Notes rather than reasoning prompts. With Gemini Composite V1, Director's Notes configured in `app.json` are the only way to provide speech-related guidance to the TTS model. 
   - **Cross-Scope Contradictory Instructions (P0):** Review `global_instruction.txt`, `agents/*/instruction.txt`, and tool docstrings to detect conflicting constraints (e.g., agent prompts mandating silence before tool return vs. tool docstrings requiring pre-call conversational pacing phrases).
   - **Speech Texture & Natural Hesitation Directives (P0):** Inspect instructions for micro-pause ellipses (`...`), natural hesitation bridge words, and digit clustering rules.
   - **Long-Call Stability & Empathy Capping (P1):** Verify that empathetic fillers and apologies are capped to a maximum of 1 occurrence per call.
   - **Silent Sub-Agent Routing & Spoken Transfer Announcements (P1):** Ensure sub-agent handoffs execute silently without transition jargon, while human escalation tools include spoken verbal announcements.

4. **Synthesize & Present Unified Prioritized Report:** Combine findings from both Pass 1 (Static) and Pass 2 (Semantic) into a single structured assessment. **Always generate a comprehensive markdown table** of all P0, P1, and P2 issues with the following structure:

   - **Executive Summary:** Overall readiness status (`PASSED` / `FAILED`) and issue count breakdown across P0, P1, and P2.
   - **Prioritized Assessment Table:** A unified table with the following columns:
     - **Priority:** `🔴 P0` (Critical Voice & Synthesis Blockers), `🟡 P1` (High Impact Multi-Language & Stability), or `🟢 P2` (Medium Impact Hygiene & Texture).
     - **Issue Description:** Issue identifier/code, concise explanation of the problem, and clickable markdown links to affected files with exact line numbers.
     - **Files Affected:** List of files affected by the issue.
     - **Possible Resolution:** Concrete, actionable, non-destructive remediation steps complying with the non-destructive guidelines.
     - **Open Questions:** **Add open questions ONLY when there is a genuine conflict or business-logic ambiguity that cannot be resolved without information/clarification from the user** (e.g., cross-scope contradictory instructions between prompt silence and tool pacing phrases, conflicting brand routing rules, or business-specific verbal announcements). For deterministic/standard technical fixes (e.g., missing Director's Note header, standard `app.json` schema patches, adding docstring contracts), omit questions or leave blank.

   **Table Schema Example:**
   ```markdown
   | Priority | Issue Description | Possible Resolution | Open Questions |
   | :--- | :--- | :--- | :--- |
   | 🔴 **P0** | **`MISPLACED_VOICE_INSTRUCTIONS`**<br>`instruction.txt:L8` contains voice tone and accent directives. | Relocate voice/accent instructions into `app.json` Director's Note and remove from agent prompt. | *(None - deterministic fix)* |
   | 🔴 **P0** | **`CONTRADICTORY_INSTRUCTIONS`**<br>`instruction.txt:L20` forbids speech before tool calls, but `tools/search.py` requires pacing. | Harmonize prompt to permit conversational pacing before backend lookup. | Does the business require total silence during tool execution or is conversational pacing preferred? |
   | 🔴 **P0** | **`MISSING_DIRECTORS_NOTE`**<br>`app.json:L6` lacks Director's Note and `## Transcript:\n` hook. | Inject standardized Director's Note with Audio Profile and trailing hook. | *(None - deterministic fix)* |
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

1. **Contextual Instruction & Session Variable Refactoring (Prompt-Level):**

   - **Relocate Voice, Accent & Speaking Directives to Director's Notes (P0):**
     - Extract all speech delivery, voice styling, vocal tone (e.g., *"warm"*, *"upbeat"*, *"smile in voice"*), accent (e.g., *"American English"*), pronunciation, speaking rate, and acoustic pacing directives from `global_instruction.txt` and `agents/*/instruction.txt`.
     - Consolidate and preserve these voice directives by incorporating them into `# Director's note` or `# Audio Profile` under `app.json.audioProcessingConfig.synthesizeSpeechConfigs[<locale>].instruction`.
     - Strip the voice-specific directives from the agent instruction text files, retaining all domain rules, taskflows, and business logic.
   - **Contextual Prohibited XML Refactoring:** Review flagged internal XML tags (`<state_update>`, `<thought>`, `<reasoning>`, `<context>`, `<call_tool>`, `<voice_lock>`, `<voice_output>`). Rather than blindly stripping them via regex without context:
     - Convert `<state_update>` or `<variable_update>` into dedicated tool invocations (e.g., `update_account_state()`).
     - Remove raw leaked thought/reasoning blocks and `<voice_lock>`/`<voice_output>` tags from prompt instructions.
   - **Contextual Emotion Tag Replacement:** Review abstract emotion tags (`[empathetic]`, `[warm]`, `[calm]`, `[short pause]`). Replace them with supported physical acoustic cues (`[whispers]`, `[sigh]`, `[chuckles]`, etc.) or ellipses (`...`) where acoustic emphasis is genuinely desired, or remove them if redundant.
   - **Detect & Resolve Cross-Scope Contradictory Instructions (P0):**
     - Audit for mutually conflicting instructions between `global_instruction.txt`, `agents/*/instruction.txt`, and tool docstrings/descriptions.
     - **Classic Contradiction Pattern:**
       - *Agent Instruction:* *"Always call the `manage_service_appointment` tool, wait for its response and then generate a response. Never say anything which is not in the tool response."*
       - *Tool Docstring:* *"Before calling this tool, speak a brief, natural conversational pacing phrase with varied options to avoid repetitive responses (e.g., 'Let me check the available time slots for you...', 'Just a minute, let me look it up...', or 'Reserving that time window now.')."*
     - **Why This Causes Fatal Failures:** The agent instruction prohibits generating speech before receiving a tool response, while the tool docstring mandates speaking a pacing phrase before calling the tool. In Gemini Composite V1, this conflicting instruction set creates an impossible constraint that triggers model hesitation loops, deadlocks, repeated retries, or safety fallbacks.
     - **Interactive Resolution Workflow (Ask the User):**
       1. Flag the conflicting statements across the agent prompt and tool docstring.
       2. Present the contradiction clearly to the user and ask targeted clarifying questions to determine the intended behavior:
          - *Option A (Recommended):* Harmonize the agent instruction to permit conversational pacing phrases before tool invocation (e.g., *"Before invoking `manage_service_appointment`, speak a natural conversational pacing phrase. Once the tool returns, synthesize your final response grounded strictly in the tool output."*).
          - *Option B:* If the business strictly requires complete silence until the backend returns, remove the pacing directive from the tool docstring and document why dead air is acceptable for that specific tool.
       3. Apply the chosen resolution non-destructively to the agent instruction or tool code.
   - **Declare Multi-Language Session Variable (`user_language`):** When the application supports multiple languages (`languageSettings.supportedLanguageCodes`), ensure `user_language` is declared in `app.json.variableDeclarations`. If the app is unilingual / single-locale, skip this check.
   - **Eliminate Text Variable Mutations:** Replace `"Set user_language = ES"` with tool invocations.
   - **Sync Declared Tools:** Verify all tool references in agent prompts (e.g. `{@TOOL: ...}`) are declared in the agent's `.json` configuration. Remove or declare missing tools.
   - **Eliminate Deprecated Language Switchers:** Deprecate dynamic language switching tools (`language_switcher`, `en_to_es`); set session language at session init.
   - **Eliminate Reflexive Turn Closings:** Eliminate reflexive turn closings (avoid ending every turn with `"Is there anything else?"`).


1. **Interactive Tool Docstring & Conversational Pacing Refactoring (Collaborative with User):**

   Tool docstrings serve as explicit runtime execution contracts for Gemini Composite V1 reasoning models. Because tool docstrings encode brand-specific voice texture and business constraints, **they are intentionally NOT auto-remediated blindly via CLI flags**. Instead, they are audited automatically and remediated interactively with user input.

   - **Auditing Scope & Priority Tiering:** Tools actively declared in `agent.json["tools"]` or referenced in agent instructions (`{@TOOL: ...}`) are audited. Tools mentioned in agent instructions are flagged at **Priority P1** (high-impact execution contracts), while other active declared tools are flagged at **Priority P2** (hygiene). Unused orphan tools in the `tools/` folder are excluded.

   - **Python Tools Canonical Source of Truth:**
     - For Python tools, ALWAYS author and edit the docstring directly in `tools/<tool_name>/python_function/python_code.py`.
     - **DO NOT** edit the description in `tools/<tool_name>/<tool_name>.json` for Python tools. Modifying `.json` files for Python tools can cause schema desynchronization or get overwritten during build.
     - For OpenAPI, Client, or Data Store tools without Python code, edit their respective configuration `.json` file.

   - **Interactive Step-by-Step Refactoring Process:**
     1. **Review Flagged Tools from Report:** Inspect the Priority P1 (tools in instructions) and Priority P2 findings for `MISSING_TOOL_CONVERSATIONAL_PACING`, `MISSING_TOOL_WHEN_TO_CALL` and `MISSING_TOOL_WHEN_NOT_TO_CALL`.
     2. **Tool Execution & Conversational Pacing Design:**
        - **Conversational Pacing Directives:** In Gemini Composite V1, spoken pacing phrases (*"Before calling this tool, speak a brief, natural conversational pacing phrase..."*) prevent dead air across tool executions. Terminal tools (e.g. session wrap-up, exit, test mocks) are exempt from missing pacing checks.
        - **Cross-Scope Contradiction Check:** Verify that agent and global instructions do not contradict the tool docstring (e.g., demanding complete silence before tool return while the tool specifies a pacing directive). Ask the user which behavior to preserve and align the directives.
        - **Callback Conflict Check:** If the application uses legacy `after_model_callbacks` or trivia tools to fill wait time, confirm with the user whether to transition to native model-level pacing phrases or align the prompt instructions.
     3. **Solicit User Phrasing & Author Docstring:** Present the proposed docstring structure to the user, incorporating:
        - Concise function summary.
        - Conversational pacing directive with multiple natural phrasing options (*"Before calling this tool, speak a brief, natural conversational pacing phrase with varied options (e.g., 'Let me check that for you...', 'Just a minute, let me look it up...', 'Checking that for you now...') to prevent repetitive responses."*).
        - `When to Call:` positive trigger conditions.
        - `When NOT to Call:` negative operational boundaries.
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

1. **Run Verification & Quality Gates:** Verify that all audit passes succeed and run the static structural linter:

   ```bash
   # Run verification audit
   python3 .agents/skills/cxas-composite-voice-agent-optimizer/scripts/audit_agent.py \
     --workspace=. --report

   # Run SCRAPI structural linter
   cxas lint
   ```

1. **Verify Checklist Above:** Run through the entire checklist above and verify that all items are checked off.

1. **SCRAPI Deployment Lifecycle (for Deployed Agents):** When optimizing agents deployed on CXAS / CES:

   ```bash
   # 1. Export the deployed agent configuration from CXAS
   cxas pull "<APP_RESOURCE_OR_ID>" --target-dir ./workspace
   cd ./workspace

   # 2. Run local voice remediation for app.json
   python3 ../.agents/skills/cxas-composite-voice-agent-optimizer/scripts/audit_agent.py \
     --workspace=. --remediate

   # 3. Refactor Python tool docstrings in tools/*/python_function/python_code.py as needed
   # 4. Run SCRAPI structural linter
   cxas lint

   # 5. Deploy the optimized configuration back to CXAS
   cxas push --app-dir . --to "<APP_RESOURCE_OR_ID>"
   ```

______________________________________________________________________

## Reference Documentation

Detailed specifications, templates, and empirical data are organized in the following guides:

- [Director's Notes & Audio Profile Guide](references/directors_notes_guide.md): Complete schema definitions, global placement rationale, accent normalization tables, and multilingual golden templates.
- [Global & Agent Instruction Guidelines](references/instructions_guide.md): Platform baseline vs. application responsibilities, dialogue sanitization, and prompt hygiene checklists.
- [Empirical Tags Catalog](references/empirical_tags_catalog.md): List of working physical acoustic tags to be used and inert tags to strip.
- [Natural Speech Patterns & Anti-Looping Guide](references/natural_speech_patterns.md): Micro-pauses (`...`), localized bridge words, digit clustering, and empathy capping.
- [Tool Design & Conversational Pacing](references/tool_design_and_pacing.md): Tool docstring contracts, spoken pacing phrases before tool execution, payload contamination prevention, and execution standards.
