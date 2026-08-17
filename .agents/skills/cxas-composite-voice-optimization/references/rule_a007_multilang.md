# Rule A007: Multi-Language Voice Coverage & Session Parity Specification

Rule A007 enforces complete, symmetrical voice synthesis and prompt configuration across all declared languages in a Google Cloud CX Agent Studio (CXAS) application.

--------------------------------------------------------------------------------

## 1. Overview & Failure Modes

When an application declares support for multiple languages in `languageSettings`, every supported language must have an identical tier of voice fidelity, localized Director's Notes, and session state tracking.

### 1.1 Common Failure Codes

-   **`A007-FAIL-MISSING-LANG`**: A language declared in `languageSettings.supportedLanguageCodes` or `languageSettings.defaultLanguageCode` has no corresponding key in `audioProcessingConfig.synthesizeSpeechConfigs`.
-   **`A007-FAIL-MISSING-VOICE`**: A synthesize speech configuration exists for a locale, but the `voice` identifier is empty or missing.
-   **`A007-FAIL-MISSING-INSTRUCTION`**: A synthesize speech configuration lacks a valid Audio Profile and Director's Note in its `instruction` field.
-   **`A007-FAIL-ACCENT-MISMATCH`**: An instruction was copied from another locale without adapting the accent directive (e.g., `es-419` config specifying `Accent: American English`).
-   **`A007-FAIL-MISSING-VAR`**: The global session variable `user_lang` is not declared in `app.json.variableDeclarations`.

--------------------------------------------------------------------------------

## 2. Audit Algorithm

```text
1. Collect Declared Languages [automated]:
   DeclaredLocales = { app.json.languageSettings.defaultLanguageCode }
                     ∪ app.json.languageSettings.supportedLanguageCodes

2. For each Locale L in DeclaredLocales:
   a. Verify app.json.audioProcessingConfig.synthesizeSpeechConfigs[L] exists. [automated]
   b. Verify synthesizeSpeechConfigs[L].voice is non-empty (e.g., "es-US-Chirp3-HD-Aoede"). [automated]
   c. Verify synthesizeSpeechConfigs[L].instruction contains:
      - Intro verbatim directive [manual]
      - "# Audio Profile" [automated]
      - "# Director's note" [automated]
      - Matching localized natural language accent directive [automated]
      - "## Transcript:\n" hook presence [automated] (strict trailing positioning [manual])
   d. Verify synthesizeSpeechConfigs[L].speakingRate is set (default: 1.0). [manual]

3. Verify Session Variables:
   app.json.variableDeclarations contains entry with name="user_lang" [automated] (schema.type="STRING", default="EN" or default language code [manual]).

4. Verify Agent Instructions:
   a. Root Agent contains <language_detection> block with explicit switch rules. [manual]
   b. Sub-agents contain language lock directives referencing {{user_lang}}. [manual]
   c. Language mutations use dedicated tool calls (e.g. update_language); raw text variable-setting antipatterns are strictly prohibited. [automated]
```

--------------------------------------------------------------------------------

## 3. Language Switching Architecture in Prompts

### 3.1 Root Agent `<language_detection>` Directive

Place this block in `global_instruction.txt` or `root_agent/instruction.txt`:

```xml
<language_detection>
- You must respond to the customer in the language specified by {{user_lang}}.
- You may ONLY trigger a language switch if the customer explicitly requests it (e.g., "Can we speak in Spanish?", "Habla en espanol por favor").
- If the customer speaks an isolated phrase or sentence in another language without an explicit request to change languages, continue responding in {{user_lang}}.
- When an explicit switch is detected, invoke the update_language tool with the new target language code (e.g., "ES") and confirm the switch in the new language.
- DO NOT emit text-based variable-setting directives that assign user_lang in plain text; state updates must occur solely via tool execution.
</language_detection>
```

### 3.2 Sub-Agent Language Lock

Every sub-agent `instruction.txt` must include a strict response lock:

```xml
<role>
You are the Billing Support Agent. You must strictly respond to the customer in the language specified by {{user_lang}}. Do not switch languages unless routed back to triage.
</role>
```

--------------------------------------------------------------------------------

## 4. Default Voice and Chirp3 Mapping by Locale

| Locale Code | Language Name | Recommended Default Voice | Recommended Natural Language Accent |
| :--- | :--- | :--- | :--- |
| `en-US` | English (United States) | `en-US-Chirp3-HD-Aoede` | `Accent: American English` |
| `en-GB` | English (United Kingdom) | `en-GB-Chirp3-HD-Aoede` | `Accent: British English` |
| `en-AU` | English (Australia) | `en-AU-Chirp3-HD-Aoede` | `Accent: Australian English` |
| `en-IE` | English (Ireland) | `en-IE-Chirp3-HD-Aoede` | `Accent: Contemporary Irish English` |
| `en-CA` | English (Canada) | `en-CA-Chirp3-HD-Aoede` | `Accent: Canadian English` |
| `en-IN` | English (India) | `en-IN-Chirp3-HD-Aoede` | `Accent: Indian English` |
| `es-419` | Spanish (Latin America) | `es-US-Chirp3-HD-Aoede` | `Accent: Latin American Spanish` |
| `es-US` | Spanish (United States) | `es-US-Chirp3-HD-Aoede` | `Accent: Spanish accent` |
| `es-ES` | Spanish (Spain) | `es-ES-Chirp3-HD-Aoede` | `Accent: Castilian Spanish` |
| `es-MX` | Spanish (Mexico) | `es-US-Chirp3-HD-Aoede` | `Accent: Latin American Spanish` |
| `fr-FR` | French (France) | `fr-FR-Chirp3-HD-Aoede` | `Accent: Metropolitan French` |
| `fr-CA` | French (Canada) | `fr-CA-Chirp3-HD-Aoede` | `Accent: French Canadian` |
| `de-DE` | German (Germany) | `de-DE-Chirp3-HD-Aoede` | `Accent: German` |
| `ja-JP` | Japanese (Japan) | `ja-JP-Chirp3-HD-Aoede` | `Accent: Japanese` |
| `pt-BR` | Portuguese (Brazil) | `pt-BR-Chirp3-HD-Aoede` | `Accent: Brazilian Portuguese` |
| `pt-PT` | Portuguese (Portugal) | `pt-BR-Chirp3-HD-Aoede` | `Accent: European Portuguese` |
| `it-IT` | Italian (Italy) | `it-IT-Chirp3-HD-Aoede` | `Accent: Italian` |
| `zh-CN` | Chinese (Simplified) | `cmn-CN-Chirp3-HD-Aoede` | `Accent: Mandarin Chinese` |
| `ko-KR` | Korean (South Korea) | `ko-KR-Chirp3-HD-Aoede` | `Accent: Korean` |
| `nl-NL` | Dutch (Netherlands) | `nl-NL-Chirp3-HD-Aoede` | `Accent: Dutch` |
| `hi-IN` | Hindi (India) | `hi-IN-Chirp3-HD-Aoede` | `Accent: Hindi` |
