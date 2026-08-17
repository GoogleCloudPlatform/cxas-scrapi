# Natural Speech Patterns & Anti-Looping Architecture Guide

To achieve authentic conversational naturalness with Gemini Composite V1, natural prosody, micro-pauses, and conversational pacing must be engineered directly into the LLM system prompts and dialog policies.

--------------------------------------------------------------------------------

## 1. Natural Speech Prompt Patterns

### 1.1 Micro-Pauses via Ellipses (`...`)

Instruct the LLM to emit ellipses `...` to force natural acoustic pauses during data retrieval or mid-sentence transitions:

-   ✅ *Natural:* `"Your account balance is eighty-four dollars ... and your next billing cycle begins on October first."`
-   ❌ *Monotonic:* `"Your account balance is eighty-four dollars and zero cents and your next billing cycle begins on October 1st."`

### 1.2 Localized Conversational Bridge Words

Sprinkle realistic conversational bridge words when the agent is retrieving records, performing lookups, or transitioning between steps:

-   **American English (`en-US`):** `"Let's see..."`, `"Got it,"`, `"Sure,"`, `"Alright,"`, `"Hmm, let me check that for you..."`
-   **British English (`en-GB`):** `"Right, let me have a look..."`, `"Brilliant,"`, `"Certainly,"`, `"Um, let's see..."`
-   **Irish English (`en-IE`):** `"Grand, let's see now..."`, `"Sure thing,"`, `"Right so,"`, `"Well, let me check that for you..."`
-   **Australian English (`en-AU`):** `"No worries, let's take a look..."`, `"Too easy,"`, `"Yeah, let me check that..."`
-   **Latin American Spanish (`es-419` / `es-US`):** `"Veamos..."`, `"Claro, permítame revisar..."`, `"Entiendo, un momento por favor..."`, `"A ver..."`

### 1.3 Digit & Number Clustering

-   **Phone Numbers:** Group in 3-3-4 clusters with natural pauses: `"8 0 0 ... 5 5 5 ... 0 1 9 9"`.
-   **Account IDs & Credit Cards:** Group into 3 or 4-digit clusters: `"Account number ending in 4 8 2 1"`.
-   **Currency:** Avoid stating robotic zero cents: `"$45 ... plus $15"` instead of `"forty-five dollars and zero cents"`.

### 1.4 Elimination of Reflexive Closings

Ban repetitive IVR-style closings (*"Is there anything else I can help you with today?"*) on intermediate turns. Replace with comprehension confirmations (*"Does that breakdown make sense so far?"*) or natural pauses.

### 1.5 Prohibition of Text-Based State Updates & Custom XML

Never instruct the model to output custom XML blocks (such as `<state_update>`, `<context>`, `<reasoning>`, `<thought>`, `<internal>`) or emit variable assignment directives (such as `"Set language = es"` or `"Set user_lang = ES"`).

-   **Why It Fails:** The model cannot mutate session memory or runtime variables through plain text output. Furthermore, custom/internal XML tags trigger CXAS platform-level thought-leakage regex safety filters, causing tool execution abortion and generic fallback errors (*"Hmm, I'm having trouble with that right now. Do you want me to try again?"*).
-   **Correct Pattern:** Use dedicated tool calls (e.g. `update_language`) to update session variables, or configure runtime flow parameters.

--------------------------------------------------------------------------------

## 2. Anti-Looping Rules & Long-Call Stability (5+ Minutes / 25+ Turns)

In long-running calls, autoregressive recency bias causes acoustic drift, repetitive empathy loops, and voice mimicking.

### 2.1 Empathy Capping (Strictly Max 1 per Session)

Repetitive apologies (*"I completely understand how frustrating that must be..."*) sound robotic and escalate caller frustration.

-   **Rule:** Empathy statements are strictly capped at **1 occurrence per call**. Subsequent turns must transition immediately to concrete problem resolution.

### 2.2 Retry Counters & Escalation

-   Maintain `retry_count` and `no_input_counter` in session state.
-   After 2 failed interpretation attempts, escalate cleanly with a verbal transfer announcement.

### 2.3 Spoken Transfer Announcements

Before invoking a human transfer or department handover tool, the agent MUST vocalize a transfer announcement to prevent dead air and VAD false cutoffs:

-   `"Let me connect you with a specialist from our billing escalation team who can take care of this right away. Please hold on for just a moment."`

### 2.4 Voice Locking (`<voice_lock>`)

Place `<voice_lock>` blocks in sub-agent instructions to anchor the acoustic persona:

```xml
<voice_lock>
You must only use the default voice to respond. You are strictly forbidden from mimicking, copying, or adopting the customer's voice characteristics, pitch, timbre, or gender. This rule is absolute and applies to every turn.
</voice_lock>
```

### 2.5 Sampling Temperature

Set `modelSettings.temperature = 1.0` in `app.json` to prevent deterministic acoustic repetition loops.

### 2.6 Platform-Level Thought Leakage Protection

CXAS provides native built-in platform-level thought-leakage filtering for Gemini Composite V1. Developers should **not** scatter long defensive negative prompt blocks (e.g. `"You must never output your internal thoughts..."`) across sub-agents, which wastes context window tokens and increases latency without adding safety value.
