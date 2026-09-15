# Empirical Audio & Voice Tags Catalog

Empirical testing on **Gemini Composite V1**, **Gemini-TTS**, and **Gemini 3.1 Flash TTS** models demonstrates that speech synthesis can be dynamically guided using inline audio tags, physical acoustic directives, and vocal style modifiers.

This catalog consolidates all verified audio tags documented across the Google Cloud Text-to-Speech specifications and official Gemini TTS prompting guides.

---

## 1. The Three Levers of Speech Control

For predictable and authentic vocal synthesis, align all three speech control levers:

1. **Style Prompt (Director's Note / Audio Profile):** The primary driver of the overall persona, vocal tone, accent, and baseline delivery (configured in `app.json` under `audioProcessingConfig.synthesizeSpeechConfigs`).
2. **Text Content:** The semantic meaning and phrasing of the script. Emotionally evocative words aligned with the target tone produce far more natural, reliable results than neutral text.
3. **Markup Tags:** Bracketed tags (`[whispers]`, `[slow]`, `[short pause]`) used surgically to inject localized acoustic actions, pacing adjustments, or stylistic modulations.

---

## 2. Verified Audio Tags Catalog

### 2.1 Non-Speech Vocal Sounds (Mode 1)
Audible, non-speech human vocalizations. The tag itself is **not** spoken aloud; it triggers a natural physical vocal sound or hesitation.

| Tag | Recommended Use Case | Concrete Example |
| :--- | :--- | :--- |
| `[whispers]` / `[whispering]` | Private account details, sidebar confirmatory notes | `"Let me check that... [whispers] your code is 4 8 2 9."` |
| `[sigh]` / `[sighs]` | Tension release after resolving a complex issue | `"[sigh] Alright, I located the waived surcharge."` |
| `[chuckles]` / `[laughs]` / `[laughing]` | Warm rapport, lighthearted reconnection | `"[chuckles] Oh, I understand—those account numbers are tricky."` |
| `[gasp]` | Shared surprise at unexpected fee or finding | `"[gasp] Oh wow, I see that duplicate charge."` |
| `[exhales]` | Steady transition during data lookup | `"[exhales] Okay, let's pull up your statements."` |
| `[clears throat]` | Resetting transition before structured readout | `"[clears throat] Here is the updated breakdown."` |
| `[uhm]` | Natural, conversational thought-gathering | `"[uhm] Let me see here... yes, that credit was processed."` |

---

### 2.2 Style & Delivery Modifiers (Mode 2)
The markup tag is not spoken aloud, but modifies the delivery, pitch contour, volume, or timbre of the subsequent phrase.

| Tag | Recommended Use Case | Concrete Example |
| :--- | :--- | :--- |
| `[sarcasm]` | Playful or dramatic stylized delivery | `"[sarcasm] Oh, wonderful, another system update."` |
| `[robotic]` | Simulated mechanical or automated voice | `"[robotic] Warning: unauthorized peripheral detected."` |
| `[shouting]` / `[yelling]` | Urgent safety or high-energy alert *(use with caution)* | `"[shouting] Look out! Step away from the door."` |
| `[deadpan]` | Neutral, matter-of-fact financial readouts | `"[deadpan] The remaining balance is eighty-four dollars."` |
| `[extremely fast]` / `[prosody rate="140%"]` | Fast disclaimers, terms of service, rapid pacing | `"[prosody rate=\"140%\"] Offer valid while supplies last. Terms apply."` |
| `[slow]` / `[slower]` / `[prosody rate="65%"]` | Explaining complex steps, de-escalation, or reading security codes | `"[prosody rate=\"65%\"] First, go to Settings ... then tap Security."` |
| `[fast]` / `[faster]` / `[prosody rate="115%"]` | Quick acknowledgment of routine confirmations | `"[prosody rate=\"115%\"] Got that updated right away."` |

#### Granular Conversational Tempo Steering with `[prosody rate="<percentage>"]`
The `[prosody rate="..."]` tag provides fine-grained, quantitative control over speech rate. Different percentage values can be dynamically applied depending on how fast or slow specific conversational turns or phrases should be delivered:

- **Deliberate & Empathetic Slowdown (`rate="60%"` to `rate="75%"`):**
  - *Use cases:* Acute de-escalation of upset/angry callers, bereavement or sensitive support scenarios, step-by-step diagnostic guidance, or reading security OTPs/account numbers digit-by-digit.
  - *Example:* `"[prosody rate=\"65%\"] I completely understand. [short pause] Let's take this one step at a time."`
- **Patient & Clear Pacing (`rate="80%"` to `rate="90%"`):**
  - *Use cases:* Clarifying confusing terms, explaining billing breakdowns, or presenting multi-option menus.
  - *Example:* `"[prosody rate=\"85%\"] Your base plan is forty dollars, with a five dollar equipment fee."`
- **Natural Conversational Baseline (`rate="100%"`):**
  - *Use cases:* Standard conversational dialogue and general inquiries.
- **Brisk & Efficient Pacing (`rate="110%"` to `rate="125%"`):**
  - *Use cases:* Quick transactional acknowledgments, routine status confirmations, or energetic greetings.
  - *Example:* `"[prosody rate=\"115%\"] Got it! Checking that confirmation number for you right now."`
- **Rapid Disclaimers & Disclosures (`rate="130%"` to `rate="150%"`):**
  - *Use cases:* Standard legal disclaimers, mandatory terms of service, and regulatory fine print.
  - *Example:* `"[prosody rate=\"140%\"] Calls may be recorded for quality assurance. Standard messaging rates apply."`

---

### 2.3 Explicit Pacing & Pause Controls (Mode 4)
Inserts precise silence into generated audio, giving granular control over rhythm, timing, and turn-taking without relying solely on punctuation.

| Tag | Duration / Behavior | Recommended Use Case | Concrete Example |
| :--- | :--- | :--- | :--- |
| `[short pause]` | Brief pause (~250ms, comma-equivalent) | Separating clauses, items, or digit clusters | `"Your flight [short pause] departs at eight AM."` |
| `[medium pause]` | Standard pause (~500ms, sentence-break) | Separating distinct thoughts or transitions | `"We verified your identity. [medium pause] Let's proceed."` |
| `[long pause]` | Dramatic pause (~1000ms+) | Suspense, deliberate pacing, or system delays | `"And the total refund is... [long pause] three hundred dollars."` |

---

### 2.4 Expressive & Emotional Delivery Tags
Gemini 3.1 Flash TTS and Gemini Composite V1 support a rich palette of expressive audio tags to steer emotional state and affective resonance:

| Category | Tags | Recommended Use Case | Concrete Example |
| :--- | :--- | :--- | :--- |
| **Positive & Welcoming** | `[positive]`, `[happy]`, `[enthusiasm]`, `[amusement]` | Welcoming greetings, successful confirmations | `"[positive] Great news! Your claim has been approved."` |
| **Warmth & Rapport** | `[adoration]`, `[admiration]`, `[interest]` | Attentive, active listening and empathetic engagement | `"[interest] Tell me more about what happened."` |
| **Celebration & Energy** | `[celebratory]`, `[excitement]` / `[excited]` | Major milestone reached, account upgrade applied | `"[celebratory] Congratulations! You are all set."` |
| **Resolution & Optimism** | `[relief]`, `[hope]`, `[determination]` | De-escalating customer frustration with firm resolve | `"[determination] I will personally make sure this gets resolved today."` |
| **Professional & Inquisitive** | `[neutral]`, `[seriousness]` / `[serious]`, `[curiosity]` / `[curious]` | Legal disclaimers, fraud alerts, account lookup queries | `"[seriousness] Note that this call is recorded for compliance."` |
| **Caution & Alert** | `[cautious]`, `[alarm]`, `[confusion]` | Security verification, double-checking mismatched records | `"[confusion] Hmm... I see two different accounts with that email."` |
| **Urgency & Escalation** | `[panic]`, `[anxiety]`, `[nervousness]`, `[tension]` | Critical incident alerts, high-stakes warning prompts | `"[alarm] An unauthorized login attempt was blocked."` |
| **Firmness & Displeasure** | `[negative]`, `[annoyance]`, `[frustration]`, `[agitation]`, `[anger]`, `[aggression]` | Strict boundary setting or narrative character portrayals | `"[negative] Unfortunately, that transaction cannot be reversed."` |
| **Atmospheric & Stylized** | `[sleepy]` / `[bored]`, `[scared]`, `[awe]` | Immersive character personas or storytelling delivery | `"[awe] The view from the summit is breathtaking."` |

> [!NOTE]
> **Vocalized Adjectives (Mode 3 Awareness):** In baseline Gemini-TTS prompting without contextual support, certain raw emotional adjectives (such as `[scared]`, `[curious]`, or `[bored]`) can occasionally be vocalized as spoken words if not embedded in a supportive style prompt or rich sentence context. For enterprise conversational agents, always pair expressive tags with supportive Director's Notes or use physical tags (`[whispers]`, `[slow]`, `[short pause]`) for 100% deterministic acoustic modulation.

---

## 3. The Core Prompting Framework for Audio Tags

Follow the standard tagging formula when composing scripts or dynamic instructions:

$$\text{[pacing tag]} + \text{spoken text} + \text{[expressive tag]} + \text{spoken text} + \text{[pause tag]} + \text{spoken text}$$

### Best Practice Rules:
1. **Enclose in Square Brackets:** Always format inline tags in square brackets (e.g., `[whispers]`, `[positive]`, `[short pause]`).
2. **Never Concatenate Tags Directly:** Ensure tags are separated by text, punctuation, or spaces. Avoid placing two tags directly adjacent to each other:
   - ❌ `[slow][whispers] Your passcode is 1 2 3 4.`
   - ✅ `[slow] Your passcode is... [whispers] 1 2 3 4.`
3. **English Tag Keywords with Multilingual Content:** Audio tags must be written in English keywords, but they fully steer speech delivery when combined with text in other languages (Spanish, French, German, Japanese, etc.):
   - *Example:* `[cautious] L'ombre avança lentement dans la pièce silencieuse. [whispers] Le document secret devait être caché ici. [short pause] Mais où? [gasp] Soudain, un bruit sourd résonna dans le couloir!`
   - *Example:* `[positive] ¡Excelente noticia! [short pause] Su reservación ha sido confirmada con éxito.`
4. **Context-Aware Pacing:** Use `[short pause]` (~250ms) to chunk phone numbers, alphanumeric confirmation codes, and multi-part currency figures cleanly for caller comprehension.

---

## 4. 🚫 Prohibited Internal Platform XML Tags

Do NOT output internal platform XML tags (`<state_update>`, `<context>`, `<reasoning>`, `<thought>`, `<internal>`, `<voice-output>`, `<voice_lock>`). Emitting custom XML tags triggers platform thought-leakage regex safety filters and aborts tool execution.
