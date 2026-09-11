# Gemini Composite Model Adoption & Voice Optimization Guide

**Author:** Google Cloud Applied AI Team  
**Platform:** Google Cloud CX Agent Studio (CXAS) / Customer Engagement Suite (CES)

--------------------------------------------------------------------------------

## Overview

This guide provides architectural recommendations, empirical findings, and operational best practices to optimize voice quality, naturalness, and conversational pacing for agents developed with the **Gemini Composite Model (v1)** on CX Agent Studio (CXAS) and Customer Engagement Suite (CES).

The composite architecture decouples conversational LLM reasoning from acoustic speech synthesis. This design enables high-fidelity voice persona steering, dynamic physical acoustic tagging, and multi-language parity.

--------------------------------------------------------------------------------

## Quick Checklist for Composite Voice Optimization

Use this 13-point checklist when building, auditing, or migrating an application to the Gemini Composite Model:

1.  [ ] **Add Audio Profile & Director's Note** to `app.json` under `audioProcessingConfig.synthesizeSpeechConfigs`.
2.  [ ] **Set Accent using Natural Language** (e.g., `Accent: American English`, `Accent: Contemporary Irish English`, `Accent: Australian English`, `Accent: British English`, `Accent: Latin American Spanish`) rather than locale codes (`en-US`).
3.  [ ] **Preserve Strict `## Transcript:\n` Hook** at the conclusion of Director's Notes to prevent style prompt leakage into spoken audio.
4.  [ ] **Remove Inert Abstract Tags** (e.g., `[empathetic]`, `[warm]`, `[calm]`, `[short pause]`).
5.  [ ] **Eliminate Prohibited Platform XML Tags** (e.g., `<state_update>`, `<context>`, `<reasoning>`, `<thought>`, `<internal>`).
6.  [ ] **Avoid Text-Based Variable Setting** (e.g., `"Set language = es"`; state changes must occur via structured tool calls like `update_language`).
7.  [ ] **Employ Validated Physical Acoustic Tags** (e.g., `[whispers]`, `[sigh]`, `[chuckles]`, `[slow]`, `[seriousness]`).
8.  [ ] **Incorporate Natural Speech Cues** (ellipses `...` and brief bridge words like `"um"`, `"hmm"`, `"let's see"`) in LLM response instructions.
9.  [ ] **Format Number & Currency Clusters** for natural, chunked reading (e.g., credit cards, phone numbers).
10. [ ] **Enforce Anti-Looping Rules** (cap repetitive empathetic filler or apology phrases to max 1 per session).
11. [ ] **Eliminate Reflexive Turn Closings** (avoid ending intermediate turns with *"Is there anything else I can help you with today?"*).
12. [ ] **Verify Long-Call Stability (5+ Minutes / 25+ Turns)** without speaker drift, voice fry, or turn exhaustion.
13. [ ] **Configure Model Sampling & Voice Lock** (`modelSettings.temperature = 1.0` in `app.json` and `<voice_lock>` blocks in sub-agent instructions).

--------------------------------------------------------------------------------

## 1. Synthesis Model Interplay & Discovery Schema

### 1.1 `SynthesizeSpeechConfig` Schema

The discovery document for Customer Engagement Suite (`https://ces.googleapis.com/$discovery/rest?version=v1`) defines the following properties on `SynthesizeSpeechConfig`:

-   **`voice`** *(string)*: Voice identifier, e.g. `en-US-Chirp3-HD-Aoede` or bare Gemini voice names (e.g. `Zephyr`, `Aoede`).
-   **`instruction`** *(string)*: Free-text style prompt containing the `# Audio Profile` and `# Director's note`. Steers persona, pacing, intonation, and accent.
-   **`speakingRate`** *(number)*: Multiplier for speech pace (default: `1.0`).
-   **`model`** *(string)*: Synthesis engine. When set to `gemini-3.1-flash-tts-preview`, generative voice synthesis is enabled. When omitted/empty, Chirp3-HD is used.
-   **`voiceSampleGcsUri`** *(string)*: Optional Cloud Storage URI for custom voice samples.
-   **`consentAudioGcsUri`** *(string)*: Optional Cloud Storage URI for voice consent audio.

### 1.2 Generative Synthesis (`gemini-3.1-flash-tts-preview`) vs. Chirp3-HD

-   **Generative Synthesis Requirement:** The `instruction` field is qualified as applying "when using a generative model". Setting `"model": "gemini-3.1-flash-tts-preview"` enables full generative prompt conditioning.
-   **Voice Naming Shift:** Switching to the generative TTS model requires bare voice names (e.g., `Zephyr`, `Aoede`, `Puck`) rather than full Chirp3-HD identifiers (e.g., `en-US-Chirp3-HD-Zephyr`).
-   **Legacy Chirp3-HD:** When `model` is empty, the platform defaults to Chirp3-HD. In GA deployments, top-level Director's Notes condition acoustic priors through platform allowlisting (`experiments/features/gemini_tts_model.gcl`).

--------------------------------------------------------------------------------

## 2. Multi-Language Voice Coverage & Rule A007

### 2.1 Scope & Resolution Mechanics

-   **Composite-Only Scope:** `audioProcessingConfig.synthesizeSpeechConfigs` configures a separate synthesis step, which only composite models execute. Native audio models (such as `gemini-3.1-flash-live`) generate speech directly and do not consult this map.
-   **Case-Insensitive Resolution & Root Language Fallback:** The platform resolves locale keys case-insensitively and falls back to the root language. For example:
    -   `es` serves `es-US`, `es-419`, and `es-ES`.
    -   `EN-US` serves `en-us`.
    -   However, an `en-US` entry does NOT serve `es-US`.
-   **Rule A007:** this skill's audit rule (`rule_a007_multilang` pass in `voice_auditor.py`) verifies that every locale declared in `languageSettings.supportedLanguageCodes` and `languageSettings.defaultLanguageCode` is served by an entry in `synthesizeSpeechConfigs`, and that delivery keys (`voice`, `instruction`, `speakingRate`, `model`) do not drift across locales. (Note: not currently part of `cxas lint`.)

### 2.2 Symmetrical Locale Configuration Recipe

```json
{
  "languageSettings": {
    "defaultLanguageCode": "en-US",
    "supportedLanguageCodes": ["es-US"],
    "enableMultilingualSupport": true
  },
  "audioProcessingConfig": {
    "synthesizeSpeechConfigs": {
      "en-US": {
        "voice": "en-US-Chirp3-HD-Aoede",
        "speakingRate": 1.0,
        "instruction": "Read the following transcript based on the audio profile and director's note. You must read the entire transcript strictly verbatim.\n\n# Audio Profile\nYou are a real human being working in customer care - warm, patient, and highly empathetic. You are sitting at your desk, answering a live phone call. This delivery must sound 100% unscripted, like a genuine, spontaneous conversation with someone you truly want to help.\n\n# Director's note\n* Persona & Tone: Friendly, grounded, and authentically conversational. Imagine you are helping a neighbor. Speak with a subtle \"smile\" in your voice, keeping the energy upbeat but deeply reassuring.\n* Pacing: Keep the pace brisk and efficient, like a busy but highly competent agent quickly relaying information. Do not over-exaggerate pauses. Keep filler words incredibly brief.\n* Intonation & Emotion: Use natural, dynamic pitch variations to express active listening and engagement. Avoid sounding monotone, rigid, or like an automated recording. Let genuine human warmth guide your vocal melody.\n* Realism & Imperfections: If bridge words (like \"um,\" \"ah,\" or \"hmm\") are in the text, deliver them naturally and thoughtfully, exactly as a human does when searching for information or gathering their thoughts.\n* Consistency: Maintain your natural, human conversational style throughout the entire read. When reading phone numbers, account IDs, or digits, group them naturally with slight pauses (e.g., reading a phone number in clusters), just as you would when reading numbers off a screen to a friend.\n* Accent: American English\n\n## Transcript:\n"
      },
      "es-US": {
        "voice": "es-US-Chirp3-HD-Aoede",
        "speakingRate": 1.0,
        "instruction": "Read the following transcript based on the audio profile and director's note. You must read the entire transcript strictly verbatim.\n\n# Audio Profile\nYou are a real human being working in customer care - warm, patient, and highly empathetic. You are sitting at your desk, answering a live phone call. This delivery must sound 100% unscripted, like a genuine, spontaneous conversation with someone you truly want to help.\n\n# Director's note\n* Persona & Tone: Friendly, grounded, and authentically conversational. Imagine you are helping a neighbor. Speak with a subtle \"smile\" in your voice, keeping the energy upbeat but deeply reassuring.\n* Pacing: Keep the pace brisk and efficient, like a busy but highly competent agent quickly relaying information. Do not over-exaggerate pauses. Keep filler words incredibly brief.\n* Intonation & Emotion: Use natural, dynamic pitch variations to express active listening and engagement. Avoid sounding monotone, rigid, or like an automated recording. Let genuine human warmth guide your vocal melody.\n* Realism & Imperfections: If bridge words (like \"eh,\" \"ah,\" or \"veamos\") are in the text, deliver them naturally and thoughtfully, exactly as a human does when searching for information or gathering their thoughts.\n* Consistency: Maintain your natural, human conversational style throughout the entire read. When reading phone numbers, account IDs, or digits, group them naturally with slight pauses (e.g., reading a phone number in clusters), just as you would when reading numbers off a screen to a friend.\n* Accent: Latin American Spanish\n\n## Transcript:\n"
      }
    }
  }
}
```

--------------------------------------------------------------------------------

## 3. Platform Safeguards & Thought-Leakage Filters

-   **Thought-Leakage Safety Regex:** CXAS runtime automatically monitors output streams for internal reasoning delimiters (`<state_update>`, `<context>`, `<reasoning>`, `<thought>`, `<internal>`). Emitting custom XML tags causes runtime abortions and returns generic retry messages.
-   **Zero Defensive Prompt Bloat:** The platform provides built-in suppression; developers do not need long negative prompts in sub-agent instructions. Keep instructions concise and focused purely on business logic.
