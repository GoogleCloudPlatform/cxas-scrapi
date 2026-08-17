---
name: cxas-composite-voice-optimization
description: >-
  Audits, optimizes, and remediates CXAS agent configurations for Gemini Composite V1 voice naturalness, persona styling, and multi-language coverage directly in local workspaces or with cxas-scrapi. Use when configuring app.json, audio profiles, physical acoustic tags, multi-language voice settings, or making an agent emotive. Don't use for text-only chat agents, non-composite standard TTS/STT, or generic dialog flow architecture.
---

# CXAS Composite Voice Optimization Skill

This skill empowers agents and developers to audit, optimize, and remediate Google Cloud CX Agent Studio (CXAS) and Customer Engagement Suite (CES) application configurations for **Gemini Composite V1** voice naturalness, persona stability, and multi-language coverage directly in local workspaces and through SCRAPI (`cxas-scrapi`).

All core operations can execute purely on local workspace files (`app.json`, `agents/*/instruction.txt`, `tools/`) with **zero GCP network connectivity** requirements, or integrate directly with `cxas` CLI workflows (`cxas lint`, `cxas pull`, `cxas push`).

--------------------------------------------------------------------------------

## When to Use This Skill

Activate this skill when:

-   Responding to requests to *"Make my agent emotive"*, *"Optimize agent voice naturalness"*, or customize voice tone and regional accents.
-   Designing, reviewing, or debugging voice configurations for CXAS / CES applications using Gemini Composite V1 or Gemini Live TTS.
-   Auditing `app.json` for missing or malformed `synthesizeSpeechConfigs`, Audio Profiles, Director's Notes, or missing `## Transcript:\n` hooks.
-   Fixing locale code anti-patterns (e.g. `Accent: en-US`) and replacing them with natural language accent specifications (`Accent: American English`, `Accent: Contemporary Irish English`, `Accent: Australian English`, `Accent: British English`, `Accent: Latin American Spanish`).
-   Enforcing **Rule A007** multi-language voice coverage across all configured language codes.
-   Stripping prohibited internal platform XML tags (`<state_update>`, `<context>`, `<reasoning>`, `<thought>`, `<internal>`) and correcting text-based variable setting anti-patterns (`"Set XXX = YYY"`).
-   Stripping ineffective / inert abstract emotion tags (`[empathetic]`, `[warm]`, `[calm]`) from agent instructions and replacing them with empirical working physical acoustic tags (`[whispers]`, `[sigh]`, `[chuckles]`, `[slow]`, `[seriousness]`).
-   Injecting natural speech prompt cues (micro-pauses via ellipses `...`, bridge words, digit clustering) and anti-looping rules (`<voice_lock>` blocks, empathy capping).

--------------------------------------------------------------------------------

## Workflows & Execution Modes

### Mode A: Interactive Persona Builder ("Make My Agent Emotive")

When a user asks to make their agent emotive, natural, or styled for a specific persona, execute this proactive interactive interview workflow rather than requiring manual documentation reading:

#### Example Trigger Prompts:

-   *"Make my agent emotive and optimize it for a warm, conversational customer care persona in US English."*
-   *"Optimize my agent's voice for UK and Irish customer support with natural accents."*
-   *"Audit my agent workspace for voice anti-patterns, enforce Rule A007 multi-language parity for US English and Latin American Spanish, and strip prohibited `<state_update>` tags."*

1.  **Conduct a Proactive Multi-Turn Alignment Interview:**

    -   **Persona & Brand Tone:** Ask what personality the agent should embody:
        -   *Empathetic Customer Care:* Warm, patient, neighborly, and reassuring.
        -   *Crisp Financial / Technical Support:* Brisk, efficient, focused, and authoritative.
        -   *Playful Concierge / Retail:* Upbeat, engaging, and dynamic.
    -   **Primary & Secondary Regional Accents:** Ask what regional markets and dialects are required:
        -   English: `en-US` ("American English"), `en-IE` ("Contemporary Irish English"), `en-AU` ("Australian English"), `en-GB` ("British English"), `en-CA` ("Canadian English"), `en-IN` ("Indian English").
        -   Spanish: `es-419` ("Latin American Spanish"), `es-US` ("Spanish accent"), `es-ES` ("Castilian Spanish").
        -   Multilingual: `fr-FR` ("Metropolitan French"), `fr-CA` ("French Canadian"), `de-DE` ("German"), `ja-JP` ("Japanese"), `pt-BR` ("Brazilian Portuguese"), `it-IT` ("Italian").
    -   **Formality & Conversational Pacing:** Ask if the domain requires strict formal compliance readouts or natural conversational rapport with micro-pauses (`...`) and bridge words (`"um"`, `"hmm"`, `"let's see..."`).

2.  **Generate and Inject Tailored Director's Note into `app.json`:**

    -   Construct complete, localized Director's Notes using natural language accents and localized bridge words for all declared locales.
    -   Ensure every note strictly concludes with the `## Transcript:\n` hook to prevent style prompt leakage into synthesized audio.
    -   Ensure `modelSettings.model = "gemini-composite-v1"` and `modelSettings.temperature = 1.0` in `app.json`.
    -   Inject the configuration globally into `app.json.audioProcessingConfig.synthesizeSpeechConfigs`.

3.  **Apply Acoustic Tagging & Anti-Looping Rules to Sub-Agents:**

    -   Interleave empirical working physical acoustic tags (`[whispers]`, `[sigh]`, `[chuckles]`, `[slow]`, `[seriousness]`, `[excitement]`, `[celebratory]`) at emotional inflection points.
    -   Add `<voice_lock>` blocks to all sub-agents to prevent voice drift, timbre shift, and caller voice mimicking.
    -   Cap empathetic apology phrases to a strict maximum of 1 per call session.

4.  **Sanitize & Run Automated Verification Audit:**

    -   Strip all prohibited internal platform XML tags (`<state_update>`, `<context>`, `<reasoning>`, `<thought>`, `<internal>`).
    -   Replace any text-based variable assignments (`"Set language = es"`) with tool calls (e.g. `update_language`).
    -   Run the 8-pass verification audit to confirm 100% compliance.

--------------------------------------------------------------------------------

### Mode B: Automated Local Voice Audit & In-Place Remediation

#### Step 1: Workspace Discovery & File Inspection

Inspect the local repository to locate the CXAS application configuration files:

1.  Locate `app.json` in the root workspace or under `cxas_app/<AppName>/app.json`.
2.  Locate root instruction files (`global_instruction.txt` or `agents/root_agent/instruction.txt`).
3.  Locate all sub-agent instruction files (`agents/*/instruction.txt`).

#### Step 2: Automated Local Voice Audit

Run the built-in local Python auditor `voice_auditor.py` across all 8 core audit passes:

```bash
python3 .agents/skills/cxas-composite-voice-optimization/scripts/voice_auditor.py \
  --workspace=. --audit-only
```

##### The 8 Core Audit Passes:

1.  **Top-Level Audio Profile & Director's Note (`app_audio_profile`):**
    -   Verify `audioProcessingConfig.synthesizeSpeechConfigs` is defined in `app.json`.
    -   Verify each locale `instruction` contains `# Audio Profile`, `# Director's note`, and `## Transcript:\n` hook headers. *(Reference: `references/directors_notes_guide.md`)*

2.  **Natural Language Accent Directives (`accent_specifications`):**
    -   Check that `Accent:` in Director's Notes uses descriptive natural language strings (`American English`, `Contemporary Irish English`, `Australian English`, `British English`, `Latin American Spanish`) rather than ISO locale codes (`en-US`, `es-US`). *(Reference: `references/directors_notes_guide.md`)*

3.  **Rule A007 Multi-Language Voice Coverage (`rule_a007_multilang`):**
    -   Extract all languages in `languageSettings.defaultLanguageCode` and `languageSettings.supportedLanguageCodes`.
    -   Verify every language has a complete entry in `synthesizeSpeechConfigs` with a voice identifier and Director's Note.
    -   Check for cross-locale accent contradictions against expected language accents.
    -   Verify `user_lang` session variable is declared in `app.json.variableDeclarations`. *(Reference: `references/rule_a007_multilang.md`)*

4.  **Prohibited Platform XML Tags (`prohibited_xml_tags`):**
    -   Scan all `instruction.txt` and `agent.json` files for prohibited internal tags: `<state_update>`, `<context>`, `<reasoning>`, `<thought>`, `<internal>`, `<call_tool>`, `<parameter_update>`, `<variable_update>`.
    -   Custom XML tags trigger CXAS platform thought-leakage regex safety filters, causing tool abortions and generic fallback errors (*"Hmm, I'm having trouble with that right now..."*). *(Reference: `references/empirical_tags_catalog.md`)*

5.  **Variable-Setting Anti-Patterns (`variable_setting_antipatterns`):**
    -   Scan instruction files for raw text variable mutations (e.g. `"Set XXX = YYY"`, `"Set language = es"`).
    -   State mutations cannot occur via raw output text; state changes must occur via tool calls (e.g. `update_language`). *(Reference: `references/empirical_tags_catalog.md`)*

6.  **Inert Tag Removal & Physical Acoustic Tag Placement (`inert_tags`):**
    -   Scan all instruction files for 43+ inert tags (`[empathetic]`, `[warm]`, `[calm]`, `[short pause]`, `[formal]`) and prosody tags.
    -   Flag or strip inert tags; recommend the 23 empirical working physical acoustic tags (`[whispers]`, `[sigh]`, `[chuckles]`, `[slow]`, `[seriousness]`). *(Reference: `references/empirical_tags_catalog.md`)*

7.  **Natural Speech Prompt Cues (`natural_speech_cues`):**
    -   Check that reflexive turn closings (*"Is there anything else I can help you with today?"*) are removed. *(Reference: `references/natural_speech_patterns.md`)*

8.  **Anti-Looping & Long-Call Stability (`anti_looping_and_stability`):**
    -   Verify `<voice_lock>` blocks are present in sub-agent instructions.
    -   Verify `modelSettings.temperature` in `app.json` is not missing and not below 0.9 (remediation sets 1.0). *(Reference: `references/natural_speech_patterns.md`)*

##### Manual Review Checklist (not automated):
-   **Intro Verbatim Directive:** Verify Director's Notes contain standard verbatim intro directive.
-   **Hook Strict Trailing Position:** Ensure `## Transcript:\n` strictly concludes the Director's Note prompt.
-   **Prompt Language Switching:** Verify root agent contains `<language_detection>` block with explicit switch rules, and sub-agents lock responses to `{{user_lang}}`.
-   **Speaking Rate:** Verify `speakingRate` is configured across all locales (defaults to 1.0).
-   **Voice Output Wrapping:** Verify alphanumeric sequences, case markers, and acronyms are wrapped in `<voice_output>`.
-   **Natural Speech Mandates:** Verify prompt instructions mandate micro-pauses via ellipses (`...`), realistic bridge words (`"um"`, `"hmm"`), and natural number/currency clustering.
-   **Stability Constraints:** Verify empathy phrases are capped at a strict maximum of 1 per call, retry counters (`retry_count`) trigger human escalation after 2 strikes, and spoken transfer announcements precede transfer tool calls.

--------------------------------------------------------------------------------

#### Step 3: Local Remediation & In-Place Patching

Apply in-place remediation to `app.json` and agent instruction files:

```bash
python3 .agents/skills/cxas-composite-voice-optimization/scripts/voice_auditor.py \
  --workspace=. --remediate
```

If post-remediation audit status is `FAILED`, remaining issues (such as `variable_setting_antipatterns` and `natural_speech_cues` which require manual prompt edits) will be printed in detail.

#### Step 4: SCRAPI (`cxas-scrapi`) End-to-End Workflow

For deployed agents, integrate the local auditor into your standard SCRAPI deployment lifecycle:

```bash
# 1. Export the deployed agent configuration from CXAS
cxas pull "<APP_RESOURCE_OR_ID>" --target-dir ./workspace
cd ./workspace

# 2. Run local voice remediation and multi-language parity auto-fix
python3 ../.agents/skills/cxas-composite-voice-optimization/scripts/voice_auditor.py \
  --workspace=. --remediate

# 3. Run SCRAPI structural linter
cxas lint

# 4. Deploy the optimized configuration back to CXAS
cxas push --app-dir . --to "<APP_RESOURCE_OR_ID>"
```

--------------------------------------------------------------------------------

## Detailed Reference Documentation

-   **Composite Model Best Practices Guide & Checklist:** `references/composite_model_guide.md` (13-point quick checklist, generative model requirements, discovery schema properties)
-   **Director's Notes & Audio Profile Guide:** `references/directors_notes_guide.md` (`app.json` schema, natural language accents & regional dialects `en-IE`/`es-419`/`en-AU`/`en-GB`, `## Transcript:\n` hook preservation, multilingual templates)
-   **Empirical Tag Catalog:** `references/empirical_tags_catalog.md` (23 working physical acoustic tags, 43+ inert tags, prohibited XML platform tags, text variable setting bans, `<voice_output>` syntax)
-   **Rule A007 Multi-Language Specification:** `references/rule_a007_multilang.md` (A007 validation algorithm, session tracking, parity checks, tool-based language switching)
-   **Natural Speech & Anti-Looping Patterns:** `references/natural_speech_patterns.md` (Micro-pauses, bridge words, number clustering, state manipulation anti-patterns, `<voice_lock>`, platform thought leakage protection)
