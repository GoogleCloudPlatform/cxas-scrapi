# Empirical Working Tags vs. Inert Tags Catalog

Empirical testing on Gemini Composite V1 and Gemini TTS models established that
the model responds exclusively to physical acoustic directives and vocal sounds.
Abstract emotional adjectives in brackets produce zero acoustic modulation.

______________________________________________________________________

## 1. ✅ Working Physical Acoustic Tags (26 Empirical Tags)

These 26 tags produce measurable, reproducible changes in pitch, tempo, volume,
duration, or vocal tract acoustic artifacts.

| Category | Tag | Acoustic Measurement / Physical Effect | Recommended Use Case | Concrete Example |
| :--- | :--- | :--- | :--- | :--- |
| **Vocal Sounds** | `[whispers]` / `[whispering]` | RMS Energy drop (0.174 → 0.042) | Private account details, sidebar confirmatory notes | `"Let me check that... [whispers] your code is 4 8 2 9."` |
| **Vocal Sounds** | `[sigh]` / `[sighs]` | Longer duration (+6.1σ to +7.0σ) | Tension release after resolving complex billing issue | `"[sigh] Alright, I located the waived surcharge."` |
| **Vocal Sounds** | `[chuckles]` / `[laughs]` | Longer duration (+6.6σ) | Warm rapport, lighthearted reconnection | `"[chuckles] Oh, I understand—those account numbers are tricky."` |
| **Vocal Sounds** | `[gasp]` | Longer duration (+4.0σ) | Shared surprise at unexpected fee | `"[gasp] Oh wow, I see that duplicate charge."` |
| **Vocal Sounds** | `[exhales]` | Longer duration (+6.5σ) | Steady air release during data lookup | `"[exhales] Okay, let's pull up your statements."` |
| **Vocal Sounds** | `[clears throat]` | Longer duration (+2.9σ) | Resetting transition before structured read | `"[clears throat] Here is the updated breakdown."` |
| **Tempo / Pacing** | `[slow]` / `[slower]` | Slower delivery (+3.9σ duration per word) | Explaining complex prorated bill items | `"[slow] First, go to Settings ... then tap Security."` |
| **Tempo / Pacing** | `[fast]` / `[faster]` | Shorter duration (-3.2σ), brisk delivery | Quick acknowledgment of routine confirmation | `"[fast] Got that updated right away."` |
| **Tempo / Pacing** | `[confusion]` | Slower cadence (+6.8σ), questioning inflection | Double-checking mismatched records | `"[confusion] Hmm... I see two accounts matching that address."` |
| **Tempo / Pacing** | `[sleepy]` / `[bored]` | Lower pitch (172 Hz → 135 Hz) | Stylized personas only | `"[sleepy] Good morning... let me open your file."` |
| **Pitch / Energy** | `[seriousness]` / `[serious]` | Pitch drop (172 Hz → 147 Hz) | Regulatory disclaimers, debt obligations | `"[seriousness] Note that this call is recorded for quality."` |
| **Pitch / Energy** | `[deadpan]` | Pitch drop (172 Hz → 140 Hz) | Neutral, matter-of-fact financial readouts | `"[deadpan] The remaining balance is eighty-four dollars."` |
| **Pitch / Energy** | `[excitement]` / `[excited]` | Pitch rise (+3.4σ), elevated energy | Successfully applying a credit or promo | `"[excitement] Great news! We applied a fifty dollar credit."` |
| **Pitch / Energy** | `[celebratory]` | Pitch rise (+3.9σ), upbeat melodic inflection | Issue fully resolved, account upgrade confirmed | `"[celebratory] You are all set!"` |
| **Pitch / Energy** | `[yelling]` | Extreme pitch rise (+6.6σ) | High-energy scenarios *(use with extreme caution)* | `"[yelling] Look out!"` |

______________________________________________________________________

## 2. ❌ Ineffective / Inert Tags (43+ Tags to Strip)

- **Abstract Emotion Tags**: `[warm]`, `[calm]`, `[clear]`, `[professional]`,
  `[empathetic]`, `[reassuring]`, `[sympathetic]`, `[hope]`, `[happy]`,
  `[crying]`, `[awe]`, `[fearful]`, `[surprised]`, `[cautious]`, `[alarm]`,
  `[anxiety]`, `[relief]`, `[tension]`, `[determination]`, `[enthusiasm]`,
  `[adoration]`, `[interest]`, `[curiosity]`, `[annoyance]`, `[aggression]`,
  `[nervousness]`, `[neutral]`, `[negative]`, `[positive]`, `[admiration]`,
  `[disgusted]`
- **Pause Tags**: `[short pause]`, `[long pause]`, `[short_pause]`,
  `[medium_pause]`, `[prosody rate="85%"]`, `[prosody rate="115%"]`
  *(Remediation: use ellipses `...`)*
- **Delivery Style Tags**: `[formal]`, `[casual]`, `[mumbles]`, `[stammers]`,
  `[breathless]`, `[panic]`

______________________________________________________________________

## 3. 🚫 Prohibited Internal Platform XML Tags

Do NOT output internal platform XML tags (`<state_update>`, `<context>`,
`<reasoning>`, `<thought>`, `<internal>`). Emitting custom XML tags triggers
platform thought-leakage regex safety filters and aborts tool execution.
