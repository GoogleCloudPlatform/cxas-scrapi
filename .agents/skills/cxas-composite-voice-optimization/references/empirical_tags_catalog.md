# Empirical Working Tags vs. Inert Tags Catalog

Empirical testing across **78 agent configurations** on Gemini Composite V1 and Gemini TTS models established that the model responds exclusively to **physical acoustic directives and vocal sounds**. Abstract emotional adjectives in brackets produce **zero acoustic modulation** (0 dB change in RMS energy, 0 Hz change in fundamental frequency, 0 ms change in duration).

--------------------------------------------------------------------------------

## 1. ✅ Working Physical Acoustic Tags (23 Empirical Tags)

These 23 tags produce measurable, reproducible changes in pitch, tempo, volume, duration, or vocal tract acoustic artifacts.

| Category | Tag | Acoustic Measurement / Physical Effect | Recommended Use Case | Concrete Example |
| :--- | :--- | :--- | :--- | :--- |
| **Vocal Sounds** | `[whispers]` / `[whispering]` | RMS Energy drop (0.174 → 0.042), breathy phonation | Private account details, sidebar confirmatory notes, discreet confirmation | `"Let me check that privately for you... [whispers] your one-time code is 4 8 2 9."` |
| **Vocal Sounds** | `[sigh]` / `[sighs]` | Longer duration (+6.1σ to +7.0σ), audible air release | Tension release after resolving complex billing issue or long lookup | `"[sigh] Alright, I was able to locate the waived surcharge in our billing system."` |
| **Vocal Sounds** | `[chuckles]` / `[laughs]` | Longer duration (+6.6σ), brief vocalized laughter | Warm rapport, lighthearted reconnection, gentle reassurance | `"[chuckles] Oh, I completely understand—those new account numbers are tricky."` |
| **Vocal Sounds** | `[gasp]` | Longer duration (+4.0σ), sharp audible inhalation | Shared surprise at unexpected fee, sudden account discrepancy | `"[gasp] Oh wow, I see that duplicate charge on your statement."` |
| **Vocal Sounds** | `[exhales]` | Longer duration (+6.5σ), steady air release | Thoughtful pause while querying large database | `"[exhales] Okay, let's pull up the last twelve months of statements."` |
| **Vocal Sounds** | `[clears throat]` | Longer duration (+2.9σ), vocal reset sound | Resetting transition before reading formal disclosure or long breakdown | `"[clears throat] Here is the updated breakdown for your monthly services."` |
| **Tempo / Pacing** | `[slow]` / `[slower]` | Slower delivery (+3.9σ duration per word) | Explaining complex prorated bill line items or multi-step navigation | `"[slow] First, go to Settings ... then select Security and Privacy ... and tap 2-Step Verification."` |
| **Tempo / Pacing** | `[fast]` / `[faster]` | Shorter duration (-3.2σ), brisk delivery | Quick acknowledgment of routine confirmation | `"[fast] Got that updated right away."` |
| **Tempo / Pacing** | `[confusion]` | Slower cadence (+6.8σ), rising questioning inflection | Agent double-checking mismatched records or ambiguous input | `"[confusion] Hmm... I see two accounts matching that address. Which one are you inquiring about?"` |
| **Tempo / Pacing** | `[sleepy]` | Lower pitch (172 Hz → 135 Hz), trailing cadence | Stylized personas only *(avoid in standard customer care)* | `"[sleepy] Good morning... let me open your file."` |
| **Tempo / Pacing** | `[bored]` | Monotone suppression, flat pitch | Stylized personas only *(avoid in standard customer care)* | `"[bored] Checking your ticket status now."` |
| **Pitch / Energy** | `[seriousness]` / `[serious]` | Pitch drop (172 Hz → 147 Hz), focused register | Regulatory disclaimers, debt obligations, compliance terms | `"[seriousness] Note that this call is recorded for quality and compliance purposes."` |
| **Pitch / Energy** | `[deadpan]` | Pitch drop (172 Hz → 140 Hz), flat affect | Neutral, matter-of-fact financial readouts | `"[deadpan] The remaining balance on the account is eighty-four dollars."` |
| **Pitch / Energy** | `[excitement]` / `[excited]` | Pitch rise (+3.4σ), elevated vocal energy | Successfully applying a credit, waiver, or promo discount | `"[excitement] Great news! We applied a fifty dollar credit to your current bill."` |
| **Pitch / Energy** | `[celebratory]` | Pitch rise (+3.9σ), upbeat melodic inflection | Issue fully resolved, account upgrade confirmed | `"[celebratory] You're all set! Your new gigabit fiber speed is now active."` |
| **Pitch / Energy** | `[yelling]` | Extreme pitch rise (+6.6σ), high acoustic energy | High-energy scenarios *(use with extreme caution)* | `"[yelling] Look out!"` |

--------------------------------------------------------------------------------

## 2. ❌ Ineffective / Inert Tags (43+ Tags to Flag & Strip)

The following tags have been proven empirically to sit **within background control noise**. They do NOT alter pitch, duration, or timbre. Leaving them in prompts risks text leaking into synthesized speech or causing unpredictable tokenization artifacts.

### 2.1 Abstract Emotion Tags (31 Tags)

`[warm]`, `[calm]`, `[clear]`, `[professional]`, `[empathetic]`, `[reassuring]`, `[sympathetic]`, `[hope]`, `[happy]`, `[crying]`, `[awe]`, `[fearful]`, `[surprised]`, `[cautious]`, `[alarm]`, `[anxiety]`, `[relief]`, `[tension]`, `[determination]`, `[enthusiasm]`, `[adoration]`, `[interest]`, `[curiosity]`, `[annoyance]`, `[aggression]`, `[nervousness]`, `[neutral]`, `[negative]`, `[positive]`, `[admiration]`, `[disgusted]`

### 2.2 Prosody / Pause Bracket Tags

`[short pause]`, `[long pause]`, `[short_pause]`, `[long_pause]`, `[medium pause]`, `[medium_pause]`, `[prosody rate="..."]`, `[prosody ...]` \
*Remediation:* Replace pause bracket tags with natural punctuation such as ellipses (`...`) or commas (`,`).

### 2.3 Delivery Style Tags (6 Tags)

`[formal]`, `[casual]`, `[mumbles]`, `[stammers]`, `[breathless]`, `[panic]` \
*Remediation:* Convey style directly through prompt instructions, word choice, and physical acoustic tags.

--------------------------------------------------------------------------------

## 3. 🚫 Prohibited Internal Platform XML Tags in Agent Text Output Streams

> [!CAUTION] **DO NOT USE CUSTOM XML TAGS OR INTERNAL PLATFORM KEYWORDS IN AGENT TEXT OUTPUT.** \
> Emitting custom or internal XML-like tags in generated conversational text triggers CXAS platform-level thought-leakage regex safety filters for Gemini Composite V1. When triggered, this causes active tool calls to abort immediately and forces the runtime to return generic retry error fallbacks (*"Hmm, I'm having trouble with that right now. Do you want me to try again?"*).

| Prohibited Tag / Keyword | Reason for Failure | Correct Alternative |
| :--- | :--- | :--- |
| `<state_update>...</state_update>` | Internal platform keyword. Intercepted by platform thought-leakage regex; aborts tool execution. | Remove tag entirely. Use backend tool calls (e.g. `update_language`) to mutate state. |
| `<context>`, `</context>` | Internal platform context delimiter. Triggers thought-leakage safety blocker. | Strip tag. Express relevant business details naturally in standard plain text. |
| `<reasoning>`, `</reasoning>` | Internal chain-of-thought marker. Triggers thought-leakage suppression filter. | Keep reasoning implicit within internal model thinking; do not emit raw tags. |
| `<thought>`, `</thought>` | Model deliberation delimiter. Triggers thought-leakage blocker. | Do not output thoughts or deliberation tags in text stream. Rely on platform thought protection. |
| `<internal>`, `</internal>` | Reserved platform keyword. Triggers thought-leakage regex. | Remove tag entirely. |

--------------------------------------------------------------------------------

## 4. 🚫 Text-Based Variable-Setting Anti-Patterns

> [!WARNING] **PROMPT INSTRUCTIONS CANNOT MUTATE RUNTIME VARIABLES VIA TEXT OUTPUT.** \
> Never instruct an agent to emit strings such as `"Set user_lang = ES"`, `"Set language = es"`, or `"Set keypad_entered = true"` in conversational text responses.

*   **Why It Fails:** Text emitted by the model is routed directly to the TTS synthesizer for audio generation; it does not mutate session state or flow parameters. Furthermore, assignment strings confuse downstream NLU and may be read out loud to the caller.
*   **Correct Solution:** Use dedicated, structured tool calls (e.g., `update_language(lang="ES")`) to mutate session parameters, or define runtime flow parameters in CXAS.

--------------------------------------------------------------------------------

## 5. ✅ Supported Phonetic Pronunciation & Acronym Tags (`<voice_output>`)

Unlike prohibited platform XML tags, `<voice_output>` is an officially supported synthesizer directive for alphanumeric sequences, case markers, acronyms, and phonetic respelling.

### 5.1 Alphanumeric & Case-Sensitive Sequences

When reading passwords, tracking numbers, or verification codes:

```text
Spelled as: <voice_output>Upper case</voice_output> <voice_output>A</voice_output> as in Alpha, <voice_output>lower case</voice_output> <voice_output>b</voice_output> as in Bravo, <voice_output>7</voice_output>, <voice_output>9</voice_output>.
```

### 5.2 Acronyms vs. Words

Prevent the model from mispronouncing acronyms as words (e.g., reading "RSA" as a single word or "SQL" inconsistently):

```text
The system uses <voice_output>S-Q-L</voice_output> database encryption and <voice_output>R-S-A</voice_output> keys.
```

### 5.3 Phonetic Name Respellings

For unusual brand names or geographic locations:

```text
Your appointment is at our <voice_output>Mil-pee-tus</voice_output> service center.
```
