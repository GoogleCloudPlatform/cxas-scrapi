---
name: cxas-composite-voice-agent-optimizer
description: >-
  Audits, optimizes, and remediates CXAS agent configurations for Gemini Composite V1 voice naturalness,
  persona styling, and multi-language coverage directly in local workspaces with cxas-scrapi.
  Generates prioritized HTML readiness reports (P0/P1/P2) and applies automated fixes.
---

# CXAS Composite Voice Agent Optimizer

This skill audits, optimizes, and remediates Google Cloud CX Agent Studio (CXAS)
and Customer Engagement Suite (CES) agent configurations for **Gemini Composite
V1** voice naturalness, persona stability, and multi-language parity.

All core operations execute purely on local workspace files (`app.json`,
`agents/*/instruction.txt`, `tools/`). The `cxas` CLI can be used to manage local 
agent workspace (`cxas pull`, `cxas lint`, `cxas push`).

______________________________________________________________________

## When to Use This Skill

Activate this skill when:

- **Adapting to Composite Models:** Evaluating or migrating an existing or new
  CXAS agent configuration to Gemini Composite V1.
- **Generating Readiness Reports:** Generating a prioritized (P0/P1/P2)
  HTML/Markdown assessment report of required voice adaptations.
- **Remediating Voice Configurations:** Fixing missing or malformed
  `synthesizeSpeechConfigs`, Audio Profiles, Director's Notes, or sampling
  temperature in `app.json`.
- **Enforcing Multi-Language Parity:** Implementing multi-language voice
  coverage across all configured language codes.
- **Normalizing Accent Directives:** Replacing ISO locale codes (`Accent: en-US`) with natural language accent descriptions (`Accent: American English`).
- **Sanitizing Agent Instructions:** Stripping prohibited internal platform
  XML tags (`<state_update>`, `<context>`, `<reasoning>`, `<thought>`,
  `<internal>`) and replacing raw text variable mutations (`"Set XXX = YYY"`)
  with tool calls.
- **Enforcing Tool Pacing & Docstring Standards:** Auditing and injecting
  conversational pacing directives (*"Before calling this tool, speak a
  brief..."*) into tool docstrings to prevent caller dead air during backend
  execution.
- **Syncing Tool Declarations & Eliminating Deprecated Switchers:** Ensuring
  all tools referenced in instructions are declared in agent configs, and
  deprecating dynamic language-switching tools.
- **Calibrating Acoustic Tags:** Replacing inert emotion tags (`[empathetic]`,
  `[warm]`, `[calm]`, `[short pause]`) with empirical physical acoustic tags
  (`[whispers]`, `[sigh]`, `[chuckles]`, `...`).
- **Engineering Natural Speech & Stability:** Adding micro-pauses (`...`),
  localized bridge words, number clustering, and anti-looping rules.

**When NOT to use this skill:**

- Standard text-only chat agents without voice/audio synthesis.
- Non-composite standard TTS/STT pipelines.
- Generic non-voice dialog flow refactoring.

______________________________________________________________________

## Checklist & Inspection Gates

The optimizer evaluates agent configurations against a prioritized checklist:

### 🔴 Priority P0: Critical Synthesis Blockers & Leakage (Must Fix First)

1. [ ] **Audio Profile & Director's Note Configuration:** Add Audio Profile &
   Director's Note to `app.json` or Global Voice Settings in CXAS. Preserve the
   whole Director's Note (with mandatory trailing `## Transcript:\n` hook) to
   prevent style prompt leakage into spoken audio.
1. [ ] **Natural Language Accent Strings:** Set Accent using Natural Language
   (e.g., `Accent: American English`, `Accent: Contemporary Irish English`,
   `Accent: Australian English`, `Accent: British English`, `Accent: Latin American Spanish`) rather than locale codes (`en-US`).
1. [ ] **Eliminate Prohibited Platform Tags:** Eliminate prohibited platform
   tags (e.g., `<state_update>`, `<context>`, `<reasoning>`, `<thought>`,
   `<internal>`, `<call_tool>`, `<parameter_update>`, `<variable_update>`, `<voice_lock>`, `<voice_output>`) which trigger thought-leakage regex safety filters.
1. [ ] **Declared Tool Synchronization:** Every tool referenced in agent instructions
   (e.g., `{@TOOL: tool_name}`, `[Tool Call: tool_name]`) MUST be declared in the
   agent's configuration `.json` under `"tools"`. Undeclared tools throw fatal
   `ToolNotFoundError` exceptions at runtime.
1. [ ] **Model Settings Configuration:** `modelSettings.model` is set to
   `"gemini-composite-v1"` and `modelSettings.temperature` is set to `1.0`
   (prevents acoustic repetition loops).
1. [ ] **Incorporate Natural Speech Cues:** Incorporate natural speech cues
   (ellipses `...` and brief bridge words like `"um"`, `"hmm"`, `"let's see"`)
   in LLM response instructions.

### 🟡 Priority P1: Multi-Language Parity, Session Stability & Call Flow

7. [ ] **Tool Docstrings & Conversational Pacing Directives (Selective by Latency):**
   All tools define unambiguous execution contracts (When to Call & When NOT to Call).
   For Python tools, docstrings are authored and maintained directly in
   `tools/<name>/python_function/python_code.py` as the canonical source of truth.
   Engineering judgment is applied to inject conversational pacing directives
   (*"Before calling this tool, speak a brief, natural conversational pacing phrase..."*)
   selectively on tools that perform remote lookups, searches, database mutations, or
   long-running API calls to prevent caller dead air, while exempting fast/synchronous
   tools (intent classification, local state access, session wrap-up / exit).
1. [ ] **Eliminate Deprecated Language-Switching Tools:** Remove dynamic
   language-switching tools (`language_switcher`, `en_to_es`); session language
   is established at IVR/session initialization and dynamic switching tools add
   latency and risk hallucination.
1. [ ] **Verify Long-Call Stability (5+ Minutes):** Verify long-call stability
   (5+ minutes) without speaker drift, voice fry, or turn exhaustion.
1. [ ] **Minimize Proactive Unnecessary Call Transfers:** Transfer only on
   explicit customer escalation or hold the line and be rigorous on
   conversational design. Sub-agent handoffs execute silently via tool calls
   without speaking internal transition jargon.
1. [ ] **Employ Validated Physical Acoustic Tags:** Employ validated physical
   acoustic tags (e.g., `[whispers]`, `[sigh]`, `[chuckles]`, `[slow]`,
   `[seriousness]`).
1. [ ] **Eliminate Reflexive Turn Closings:** Eliminate reflexive turn closings
   (avoid ending every turn with *"Is there anything else?"*).

### 🟢 Priority P2: Speech Hygiene, Pacing & Conversational Texture

13. [ ] **Multi-Language Voice Parity:** Every configured locale in
    `languageSettings.supportedLanguageCodes` has a matching entry in
    `synthesizeSpeechConfigs` with localized Director's Notes, appropriate voice
    IDs, and native bridge words.
01. [ ] **Avoid Text-Based Variable Setting:** Avoid text-based variable setting
    (e.g., `"Set login_status = true"` or `"Set keypad_mentioned = true"`); use
    structured tool invocations for state changes (e.g., `update_login_status`).
01. [ ] **Remove Inert Abstract Tags:** Remove inert abstract tags (e.g.,
    `[empathetic]`, `[warm]`, `[calm]`, `[short pause]`).
01. [ ] **Format Number & Currency Clusters:** Format number and currency
    clusters for natural, chunked reading (e.g., credit card numbers, phone
    numbers).
01. [ ] **Enforce Anti-Looping Rules:** Enforce anti-looping rules (cap
    repetitive empathetic filler or apology phrases to max 1 per session;
    trigger retry escalations after 2 strikes).

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
     2. Run multi-pass acoustic & tool audit           2. Declare session variables (`user_language`)
     3. Generate prioritized Markdown report           3. Contextually refactor XML, prompts & tags
     4. Review prioritized P0/P1/P2 plan               4. Refactor Python tool docstrings & pacing
                                                       5. Run verification audit & `cxas lint`
```

______________________________________________________________________

### Mode 1: Report Generation Mode

Assesses an existing CXAS agent workspace, checks all inspection gates, and
generates a prioritized report detailing action items required
to adapt the agent to Gemini Composite V1.

#### Workflow Steps:

1. **Workspace Discovery:** Locate `app.json`, global instructions, sub-agent
   instructions (`agents/*/instruction.txt`), and tool definitions in the
   workspace.

1. **Execute Auditor Report Command:** Run the local auditor to evaluate the
   workspace against all P0/P1/P2 rules:

   ```bash
   # Generate human-readable Markdown report
   python3 .agents/skills/cxas-composite-voice-agent-optimizer/scripts/audit_agent.py \
     --workspace=. --report
   ```

   For automated CI pipelines or programmatic JSON consumption:

   ```bash
   # Output structured JSON report
   python3 .agents/skills/cxas-composite-voice-agent-optimizer/scripts/audit_agent.py \
     --workspace=. --report --json-output
   ```

1. **Review Prioritized Findings:**

   - **Executive Summary:** Overall readiness status (`PASSED` / `FAILED`),
     and total issue count breakdown across P0, P1, and P2.
   - **Itemized Findings:** Itemized list of findings detailing Issue Code,
     Priority Tier, Affected File/Location, and Concrete Guidance.

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

   - **Declare Session Variables (`user_language`):** In multi-language applications, register `user_language` or `app_language` in `app.json.variableDeclarations` so that runtime language state is tracked reliably.
   - **Contextual Prohibited XML Refactoring:** Review flagged internal XML tags (`<state_update>`, `<thought>`, `<reasoning>`, `<context>`, `<call_tool>`, `<voice_lock>`, `<voice_output>`). Rather than blindly stripping them via regex without context:
     - Convert `<state_update>` or `<variable_update>` into dedicated tool invocations (e.g., `update_account_state()`).
     - Remove raw leaked thought/reasoning blocks and `<voice_lock>`/`<voice_output>` tags from prompt instructions.
   - **Contextual Emotion Tag Replacement:** Review abstract emotion tags (`[empathetic]`, `[warm]`, `[calm]`, `[short pause]`). Replace them with supported physical acoustic cues (`[whispers]`, `[sigh]`, `[chuckles]`, etc.) or ellipses (`...`) where acoustic emphasis is genuinely desired, or remove them if redundant.
   - **Eliminate Text Variable Mutations:** Replace `"Set user_language = ES"` with tool invocations.
   - **Sync Declared Tools:** Verify all tool references in agent prompts (e.g. `{@TOOL: ...}`) are declared in the agent's `.json` configuration. Remove or declare missing tools.
   - **Eliminate Deprecated Language Switchers:** Deprecate dynamic language switching tools (`language_switcher`, `en_to_es`); set session language at session init.

1. **Interactive Tool Docstring & Conversational Pacing Refactoring (Collaborative with User):**

   Tool docstrings serve as explicit runtime execution contracts for Gemini Composite V1 reasoning models. Because tool docstrings encode brand-specific voice texture and business constraints, **they are intentionally NOT auto-remediated blindly via CLI flags**. Instead, they are audited automatically and remediated interactively with user input.

   - **Auditing Scope:** Only tools actively declared in `agent.json["tools"]` across the application are audited for docstring contracts and pacing directives. Unused orphan tools in the `tools/` folder are excluded.

   - **Python Tools Canonical Source of Truth:**
     - For Python tools, ALWAYS author and edit the docstring directly in `tools/<tool_name>/python_function/python_code.py`.
     - **DO NOT** edit the description in `tools/<tool_name>/<tool_name>.json` for Python tools. Modifying `.json` files for Python tools can cause schema desynchronization or get overwritten during build.
     - For OpenAPI, Client, or Data Store tools without Python code, edit their respective configuration `.json` file.

   - **Interactive Step-by-Step Refactoring Process:**
     1. **Review Flagged Tools from Report:** Inspect the Priority P1 findings for `MISSING_TOOL_CONVERSATIONAL_PACING`, `MISSING_TOOL_WHEN_TO_CALL`, and `MISSING_TOOL_WHEN_NOT_TO_CALL`.
     2. **Categorize by Latency & Architecture:**
        - **Latency-Sensitive Tools (Requires Pacing Phrase):** External API calls, DB queries, ServiceNow incidents, remote auth checks, or CRM lookups.
        - **Fast / Synchronous / Terminal Tools (Exempt from Pacing):** Intent classification, routing, local session variable setters/getters, retry counters, or session exit (`itx_end_session`).
        - **Callback Conflict Check:** If the application uses legacy `after_model_callbacks` or trivia tools to fill wait time, confirm with the user whether to transition to native model-level pacing phrases or align the prompt instructions.
     3. **Solicit User Phrasing & Author Docstring:** Present the proposed docstring structure to the user, incorporating:
        - Concise function summary.
        - Conversational pacing directive with natural speech texture (*"Before calling this tool, speak a brief, natural conversational pacing phrase..."*).
        - `When to Call:` positive trigger conditions.
        - `When NOT to Call:` negative operational boundaries.
     4. **Apply to Python Source Code:** Write the approved docstring into `tools/<name>/python_function/python_code.py`.

   - **Docstring Pattern Example:**
     ```python
     def search_customer_account(phone_number: str) -> dict:
         """Searches for customer accounts by phone number.

         Before calling this tool, speak a brief, natural conversational pacing phrase
         to keep the caller informed (e.g., 'Let me look up your account details...').

         When to Call:
         - Call when the customer provides their phone number for account lookup.

         When NOT to Call:
         - Do NOT call if the phone number has fewer than 10 digits.
         """
     ```

1. **Run Verification & Quality Gates:** Verify that all audit passes succeed
   and run the static structural linter:

   ```bash
   # Run verification audit
   python3 .agents/skills/cxas-composite-voice-agent-optimizer/scripts/audit_agent.py \
     --workspace=. --report

   # Run SCRAPI structural linter
   cxas lint
   ```

1. **SCRAPI Deployment Lifecycle (for Deployed Agents):** When optimizing
   agents deployed on CXAS / CES:

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

Detailed specifications, templates, and empirical data are organized in the
following guides:

- [Director's Notes & Audio Profile Guide](references/directors_notes_guide.md):
  Complete schema definitions, global placement rationale, accent
  normalization tables, and multilingual golden templates (`en-US`, `en-GB`,
  `en-AU`, `en-IE`, `es-US`/`es-419`).
- [Empirical Tags Catalog](references/empirical_tags_catalog.md): 26 working
  physical acoustic tags with measurements and 43+ inert tags to strip.
- [Natural Speech Patterns & Anti-Looping Guide](references/natural_speech_patterns.md):
  Micro-pauses (`...`), localized bridge words, digit clustering, and empathy
  capping.
- [Tool Design & Conversational Pacing](references/tool_design_and_pacing.md):
  Tool docstring contracts, spoken pacing phrases before tool execution,
  payload contamination prevention, and execution standards.
- [Global & Agent Instruction Guidelines](references/instructions_guide.md):
  Platform baseline vs. application responsibilities, dialogue sanitization,
  and prompt hygiene checklists.
