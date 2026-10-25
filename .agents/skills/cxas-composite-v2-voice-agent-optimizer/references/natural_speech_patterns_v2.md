# Gemini Composite V2 (`gemini-composite-v2`) Natural Speech Patterns, Prompt Templates & Callback Reference

This reference consolidates the official **Gemini Composite V2** (`gemini-composite-v2`) speaking style guidelines, inline sound and multi-part tone tag taxonomy, empirical tag replacement tables, tool-scoped text normalization templates, contradiction checks, callback patterns, frontend tag sanitizers, and acoustic troubleshooting rules.

______________________________________________________________________

## 1. `app.json` & Speaking Style Configuration for Composite V2

### Removing Legacy V1 Director's Notes

Many Composite V1 (`gemini-composite-v1`) agents use a long **Director's Note** (`# AUDIO PROFILE`, `# THE SCENE`, `# DIRECTOR'S NOTES`, and trailing `## Transcript:`) as the speaking style in `audioProcessingConfig.synthesizeSpeechConfigs.<locale>.instruction`. **Remove it before switching to `gemini-composite-v2`.** Long instructions can make the voice drift over a call and conflict with inline tags.

The auditor's `--remediate` mode clears V1 Director's Notes, and any speaking style longer than 25 words, to an empty style (`""`). It also clears Director's Notes in custom voice samples (`audioProcessingConfig.customVoiceSamples[].voiceInstruction`); an over-long custom voice instruction without a Director's Note is reported (`CUSTOM_VOICE_INSTRUCTION_TOO_LONG`) but not changed. Remediation prints the removed text so a person can write a short speaker profile from it, and it never injects a style.

### How to Write a Composite V2 Speaking Style

1. **Default (empty style):** Use an empty `instruction` (`"instruction": ""`). Composite V2 reads punctuation and context well out of the box.
1. **Only if needed, a short speaker profile (~12 words):** Write a description of **about a dozen words** (≤ 25 words max) about **who the speaker is**: age, vocal timbre, pitch, accent, and pace. Don't describe how the speaker feels; use inline tags for moment-to-moment emotion.
1. **Avoid feeling words as a style:** A style such as `"warm, friendly, natural, professional"` describes how the speaker feels, not who the speaker is. If an app already uses one, suggest `""` (or a short speaker profile) instead.

| Use Case                   | V2 Speaking Style (`instruction`)                                                               | Sample Line                                                                                                  |
| :------------------------- | :---------------------------------------------------------------------------------------------- | :----------------------------------------------------------------------------------------------------------- |
| **Default (any use case)** | `""` (empty style default)                                                                      | `"Right, let me check your account... everything looks SOLID on our end."`                                   |
| **General Support**        | `"Friendly American woman in her early 30s, warm mid-pitch voice, relaxed conversational pace"` | `"Hi there, I'd be happy to look into that for you right away."`                                             |
| **Retail Support**         | `"Upbeat woman in her late 20s, bright mid-range voice, quick, friendly cadence"`               | `"Thanks for calling. <breath> I can help you track that order. What's your order number?"`                  |
| **Banking**                | `"Calm man in his 40s, steady low baritone, measured and unhurried delivery"`                   | `"I can see the charge you're asking about, <short pause> and I'll walk you through exactly what happened."` |
| **Telecom**                | `"Friendly Southern US woman in her 50s, warm alto, relaxed, patient drawl"`                    | `"Alright, let's get your internet back up. <short pause> Go ahead and unplug your router for me."`          |
| **Travel**                 | `"Young British man, crisp diction, clear tenor voice, brisk yet polite pace"`                  | `"Good news, <short pause> I found you a seat on the six forty to Heathrow. Shall I book it?"`               |

> [!TIP]
> Prefer full stops over exclamation marks in spoken replies and in the sample lines you write into prompts. Use a tone tag such as `<positive>` when a reply needs more energy.

### Model, Voice, Multi-Locale & Speaking Rate Settings

- **Model:** Set `modelSettings.model` to `gemini-composite-v2`. `--remediate` upgrades only an older Composite model or a missing model (including agent-level overrides); any other model is reported for the app owner to decide.
- **Temperature and `speakingRate` are not V2 requirements:** The auditor reports temperature only as a P2 advisory (`TEMPERATURE_ADVISORY`), and `--remediate` never changes temperature or `speakingRate`.
- **Voices & Multi-Locale Enablement:** Any `*-Chirp3-HD-*` voice or a custom voice works. Keep the same voice persona across locales (for example, Aoede in every locale), and verify that the voice exists for each locale. When `languageSettings.supportedLanguageCodes` declares additional locales, set `languageSettings.enableMultilingualSupport: true` (`MULTILINGUAL_SUPPORT_NOT_ENABLED`, auto-fixed by `--remediate`). When `--remediate` adds a missing locale, it reuses the default locale's Chirp 3 HD persona (for example, `en-AU-Chirp3-HD-Aoede` → `es-US-Chirp3-HD-Aoede`) and copies its `speakingRate`. Non-Chirp voices are reported (`NON_CHIRP3_HD_VOICE`, P1) and never replaced automatically.
- **Never copy an English-locale speaker profile into non-English locales:** If you write a short speaker profile for `en-US` or `en-AU` (such as `"American woman in her early 30s..."` or `"Australian woman..."`), do **not** copy that string into `es-US`, `hi-IN`, `cmn-CN`, or `ko-KR` (`LOCALE_SPEAKER_PROFILE_ACCENT_MISMATCH`), as it forces a foreign English accent onto non-English speech. Leave non-English locale `instruction` fields empty (`""`) or use a locale-appropriate speaker profile.

#### Chirp 3 HD Voice Persona Gender Reference (for Gendered Languages)

In gendered languages (such as Hindi `सकती हूँ` vs. `सकता हूँ`, Spanish `encantada` vs. `encantado`, French, or Portuguese), match first-person verb and adjective gender in prompts and callbacks to your configured Chirp 3 HD persona:

| Vocal Gender        | Chirp 3 HD Voice Names                                                                                                                                                                |
| :------------------ | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Female Personas** | `Aoede`, `Zephyr`, `Kore`, `Leda`, `Callirrhoe`, `Autonoe`, `Erinome`, `Laomedeia`, `Pulcherrima`, `Vindemiatrix`, `Despina`, `Gacrux`, `Achernar`, `Sulafat`                         |
| **Male Personas**   | `Puck`, `Charon`, `Fenrir`, `Orus`, `Enceladus`, `Iapetus`, `Umbriel`, `Algieba`, `Algenib`, `Rasalgethi`, `Alnilam`, `Schedar`, `Achird`, `Zubenelgenubi`, `Sadachbia`, `Sadaltager` |

### Example `app.json` for `gemini-composite-v2`

```json
{
  "modelSettings": {
    "model": "gemini-composite-v2"
  },
  "languageSettings": {
    "defaultLanguageCode": "en-US",
    "supportedLanguageCodes": ["es-US", "hi-IN", "cmn-CN", "ko-KR"],
    "enableMultilingualSupport": true
  },
  "audioProcessingConfig": {
    "synthesizeSpeechConfigs": {
      "en-US": {
        "voice": "en-US-Chirp3-HD-Aoede",
        "speakingRate": 1.0,
        "instruction": ""
      },
      "es-US": {
        "voice": "es-US-Chirp3-HD-Aoede",
        "speakingRate": 1.0,
        "instruction": ""
      },
      "hi-IN": {
        "voice": "hi-IN-Chirp3-HD-Aoede",
        "speakingRate": 1.0,
        "instruction": ""
      },
      "cmn-CN": {
        "voice": "cmn-CN-Chirp3-HD-Aoede",
        "speakingRate": 1.0,
        "instruction": ""
      },
      "ko-KR": {
        "voice": "ko-KR-Chirp3-HD-Aoede",
        "speakingRate": 1.0,
        "instruction": ""
      }
    },
    "bargeInConfig": {
      "bargeInAwareness": true
    },
    "inactivityTimeout": "20s"
  }
}
```

The example omits temperature because it isn't a V2 requirement. It shows `speakingRate` only because most exported apps include it; keep whatever value your app already uses. If you add a speaker profile, put it in that locale's `instruction` field.

______________________________________________________________________

## 2. Composite V2 Inline Tags, Reliability Tiers & Tag Replacements

Composite V2 uses **angle brackets (`<...>`)** instead of square brackets (`[...]`). Inline tags are **point markers**: open a tag and keep talking, and **never write a closing tag** (`</prosody>`, `</warm>`, or any `</...>`). Always keep tags in **English**, even when the agent speaks Spanish, Japanese, Hindi, Mandarin, or Korean.

### A. One-Shot Non-Verbal Sound & Pause Tags

Unlike the speaking style (which shapes the whole turn), a sound tag happens once and finishes:

- **Breathing:** `<breath>`, `<heavy breath>`, `<exhales>`, `<pant>`, `<sigh>`, `<sighs>`
- **Laughter:** `<laugh>`, `<chuckle>`, `<chuckles>`, `<giggle>`, `<snicker>`, `<cackle>`
- **Reactions:** `<gasp>`, `<cough>`, `<sneeze>`, `<throat-clearing>`, `<snort>`, `<tsk>`, `<argh>`
- **Sounds & Soft Delivery:** `<sob>`, `<cry>`, `<whimper>`, `<groan>`, `<yawn>`, `<whispers>`, `<whispering>`
- **Pauses:** `<short pause>`, `<long pause>`

> **Customer Service Best Practice:** In customer-facing agents, restrict prompt instructions to polite, professional tags (`<breath>`, `<exhales>`, `<sigh>`, `<laugh>`, `<chuckle>`, `<chuckles>`, `<gasp>`, `<throat-clearing>`, `<short pause>`, `<long pause>`) so the model never emits `<cackle>`, `<snort>`, `<tsk>`, or `<yawn>` at a caller.

### B. Multi-Part Tone & Style Transition Tags (Persistent Sentence-Opening Tags)

In CX Agent Studio, opening a sentence with a tone tag shifts the delivery style from that sentence until the next tone tag:

- **Recommended Tone & Cadence Tags:** `<calm>`, `<empathetic>`, `<warm>`, `<positive>`, `<enthusiasm>`, `<clear>`, `<determination>`, `<interest>`, `<relief>`, `<fast>`, `<faster>`
- **Supported Tone Tags (keep strictly at sentence start):** `<sympathetic>`, `<tension>`, `<anxiety>`, `<awe>`, `<surprised>`, `<fearful>`, `<negative>`, `<disgusted>`
- **Multi-Part Example:**
  ```text
  <calm><empathetic> I'm so sorry your flight was canceled. That's a stressful way to start a trip. <positive> The good news is that I've already rebooked you on the next flight out.
  ```

### C. Optional Pacing with `<prosody rate="N%">`

`<prosody rate="N%">` changes the speaking rate of the words that follow it. It is optional.

- **Keep original rates when migrating:** `[prosody rate="85%"]` becomes `<prosody rate="85%">`. Only the brackets change; the rate stays exactly as written.
- **Ask before adding prosody:** If the original prompt had no prosody, ask the user before adding any.
- **Put prosody first in a stack:** Write `<prosody rate="70%"><empathetic>`, not `<empathetic><prosody rate="70%">`. `--remediate` reorders such stacks (`PROSODY_NOT_FIRST_IN_STACK`).
- **Return to normal pace explicitly:** There are no closing tags, so write `<prosody rate="100%">` before the words that resume normal pace. When speech continues after a `</prosody>`, `--remediate` rewrites it as `<prosody rate="100%">`.
- **`<slow>` and `<slower>` are read aloud as words:** `--remediate` maps them to `<prosody rate="65%">`.
- **Example:**
  ```text
  <prosody rate="70%"><empathetic> I'm so sorry about the double charge. <prosody rate="100%"> I've already started your refund, and you'll see it on your next statement.
  ```
- **Optional template line:** Add this line to the audio tag block in Section 3 only if the app already uses prosody or the user asks for it:
  ```text
  - Pacing: <prosody rate="N%"> changes the speaking rate of the words that follow it. Put it first in a stack (<prosody rate="70%"><empathetic>), and write <prosody rate="100%"> before the words that return to normal pace.
  ```

### D. Empirical Tag Replacement Table (V1 → V2)

Evaluations on Composite V2 showed that certain legacy V1 tags are frequently ignored, spoken out loud, or outperformed by a sibling tag. The auditor's `--remediate` mode automatically replaces these and deduplicates any resulting tag lists. Rows marked *standardization added by this skill* are conventions of this skill rather than evaluation findings.

| Legacy / Weak Tag   | Use Instead in Composite V2                 | Why                                                                                                          |
| :------------------ | :------------------------------------------ | :----------------------------------------------------------------------------------------------------------- |
| `reassuring`        | `<empathetic>`                              | Frequently spoken aloud mid-sentence; `<empathetic>` is reliably followed                                    |
| `curiosity`         | `<interest>`                                | Often ignored or spoken aloud; `<interest>` performs significantly better                                    |
| `professional`      | `<clear>`                                   | `<clear>` produces cleaner, more articulate customer-service delivery                                        |
| `cautious`          | `<calm>`                                    | `<calm>` steadies delivery reliably                                                                          |
| `alarm`             | `<calm>`                                    | Rarely changes delivery; `<calm>` is reliable                                                                |
| `nervousness`       | `<calm>`                                    | `<calm>` is reliable and avoids vocal instability                                                            |
| `happy`             | `<positive>`                                | `<positive>` produces a clearer upbeat lift                                                                  |
| `admiration`        | `<positive>`                                | Acoustically indistinct from `<warm>` / `<positive>`                                                         |
| `hope`              | `<warm>`                                    | Frequently ignored or spoken aloud; `<warm>` is reliable                                                     |
| `adoration`         | `<warm>`                                    | Unreliable; `<warm>` is reliable                                                                             |
| `crying`            | `<sympathetic>`                             | Renders as unstable pitch; `<sympathetic>` conveys gentle support                                            |
| `laughs`            | `<chuckles>`                                | `<chuckles>` is more reliable and warmer on customer support calls                                           |
| `annoyance`         | `<determination>`                           | `<determination>` sounds firm and focused rather than irritated                                              |
| `aggression`        | `<determination>`                           | `<determination>` conveys firm resolve without hostility                                                     |
| `slow` / `slower`   | `<prosody rate="65%">`                      | Read aloud as a word; use prosody for slower pacing                                                          |
| `exhale`            | `<exhales>`                                 | Canonical spelling of the tag                                                                                |
| `whisper`           | `<whispers>`                                | Canonical spelling of the tag                                                                                |
| `clears throat`     | `<throat-clearing>`                         | Canonical spelling of the tag                                                                                |
| `medium pause`      | `<short pause>`                             | *Standardization added by this skill:* standardize mid-sentence pauses to `<short pause>` or `<long pause>`  |
| `neutral`           | *Delete tag* (or use `<warm>`)              | No longer recommended as an inline tag; leave untagged or open with `<warm>`                                 |
| `uhm` / `um` / `uh` | *Delete tag* (use `...` or `<short pause>`) | *Standardization added by this skill:* write hesitation as ellipses (`...`) or words instead of a filler tag |

**Tags outside these lists:** Tags outside the tiers listed in A–C have unknown behavior on Composite V2. The auditor reports them as `UNEVALUATED_V2_TAG` with a suggested closest tag; swap a tag only if the user agrees.

**Unrecognized bracket tokens:** Bracket tokens that aren't known tags, such as `[clam]`, are reported as `UNRECOGNIZED_BRACKET_TOKEN`. If the token is a typo of an audio tag (`[clam]` → `<calm>`), convert it; if it's a placeholder (for example, `[customer name]`), leave it alone.

### E. Delivery Effects & Known Acoustic Side Effects

| Tag                           | Effect on Delivery                                                      |
| :---------------------------- | :---------------------------------------------------------------------- |
| `<breath>` / `<exhales>`      | Audible breath release before speech; natural transition into an answer |
| `<sigh>` / `<sighs>`          | Audible empathetic inhale/exhale (`<sighs>` is heavier than `<sigh>`)   |
| `<chuckle>` / `<chuckles>`    | Crisp, warm chuckle; the preferred laugh on support calls               |
| `<whispers>` / `<whispering>` | Quiet, soft spoken delivery (`<whispering>` has the strongest drop)     |
| `<short pause>`               | Brief silence inserted at a comma or full-stop boundary                 |
| `<long pause>`                | Noticeably longer silence than `<short pause>`                          |
| `<fast>` / `<faster>`         | Slightly brisker cadence                                                |

- **Never use `<sigh>`, `<sighs>`, or `<exhales>` on call-ending or transfer turns:** While fine in an empathetic moment, never emit them on a turn that ends the session or escalates/transfers the call; open those turns with a tone tag such as `<calm><empathetic>` instead. The auditor reports violations as `SIGH_ON_ENDING_OR_TRANSFER_TURN`.
- **Initial pronoun clipping & ALL-CAPS bark:** Intense paralinguistic tags (`<exhales>`, `<sighs>`) at a sentence start can clip a leading `"I"` or `"We"`, or bark if followed immediately by an ALL-CAPS word. Keep the first word after `<exhales>` or `<sigh>` in normal case and place ALL-CAPS stress later in the clause.
- **Pause tag punctuation:** Always precede a mid-sentence `<short pause>` or `<long pause>` with a comma or full stop (`"I checked your account, <short pause> everything is in order."`). Never stack `... <short pause>`.
- **Pause tags can come out as a short filler:** A pause tag sometimes comes out as a short filler (`"uh"`, `"um"`, `"hmm"`, `"okay"`) instead of silence. Listen for it during the ear-audit.

______________________________________________________________________

## 3. Canonical `global_instruction.txt` Template for Composite V2

Place cross-cutting persona, Composite V2 audio & tone tag syntax, tool-scoped spoken text normalization (including `/IPA/` notation), affective delivery, and global conversation guardrails in `global_instruction.txt`:

```xml
<role>
You are the AI Voice Concierge operating on {current_date}, a senior customer care advisor on a live phone call. You are one real person for the whole call: specialists work behind the scenes, but the caller only ever hears you. Never say you are handing off to another internal bot or sub-agent, and never greet the caller again after the opening turn. Pick up exactly where the conversation left off.
</role>

<persona>
Your words are turned directly into speech by Gemini Composite V2. Write the way a warm, sharp, experienced human advisor actually talks on a headset:
- Keep every turn short: two or three sentences, forty-five words at most, in one paragraph with no line breaks. If there is more to say, say the most important part and check in.
- Tool results contain far more than you should say. After a tool call, share at most two facts, then ask one short question. The rest can come on the next turn if the caller wants it.
- Always use spoken contractions in English ("I'm", "you've", "it's", "that's", "there's", "they're", "we've", "can't", "won't", "let's"). Never use stiff, uncontracted forms like "I am sorry", "You have used", or "There are no". Use plain words with no lists, no markdown, and no emojis, and end sentences with full stops rather than exclamation marks.
- Sound like you're really working: light backchannels when the caller explains something ("mm-hm... yeah... okay, got it."), small hedges ("so it looks like...", "from what I can see here..."), and ellipses ('...') for natural hesitation beats.
- Put CAPITAL letters on the one or two words per turn that matter most in Latin-script languages, the way you'd lean on them out loud: NO outages, SOLID green, FORTY-TWO gigs, EIGHTY-NINE dollars. In non-Latin scripts without uppercase/lowercase (Devanagari, Hanzi, Hangul, Kana), convey emphasis through sentence-opening tone tags (<positive>, <warm>, <empathetic>) and natural emphatic phrasing rather than inserting Latin ALL-CAPS words.
- End each turn with a specific, natural check-in about the caller's situation. Never say "Is there anything else I can help you with?" on intermediate turns, and never say "unfortunately".
</persona>

<instruction id="audio tag format">
You can add natural human sounds, pauses, and mid-turn tone transitions using tags in angle brackets. They are performed by the voice model, never read aloud.
- Allowed non-verbal sound & pause tags: <breath> <exhales> <sigh> <laugh> <chuckle> <chuckles> <gasp> <throat-clearing> <short pause> <long pause>
- Allowed sentence-opening tone transition tags: <calm> <empathetic> <warm> <positive> <enthusiasm> <clear> <determination> <interest> <relief>
- Placement: Put sound and tone tags at the start of a sentence (stacking is allowed there, such as <calm><empathetic>). Pause tags (<short pause>, <long pause>) can also go mid-sentence after punctuation (', <short pause>' or '. <short pause>', for example: "I checked your account, <short pause> everything is in order."). Never put a tag in the middle of a phrase without punctuation, and never stack <short pause> or <long pause> right after an ellipsis ('...') — use either '...' for a light hesitation or ', <short pause>' / '. <short pause>' between steps, never both together.
- Multi-part tone transitions & no closing tags: Open a sentence with a tone tag (<calm>, <empathetic>, <warm>, <positive>) when shifting tone mid-response; a tone tag stays in effect until the next tone tag (for example: "<calm><empathetic> I'm so sorry your flight was canceled. That's a stressful way to start a trip. <positive> The good news is that I've already rebooked you on the next flight out."). Never write a closing tag (</prosody>, </warm>, or any </...>).
- Never place an ALL-CAPS stress word as the very first word immediately after <exhales> or <sigh>; let the opening word be normal case and put the ALL-CAPS stress later in the clause (for example: "<exhales> Solid green on the status light means you're ONLINE.").
- Less is more: One or two tags per turn are enough for most customer conversations, and most routine turns need NO tag at all.
- Sigh and exhale limits (customer-care default): Use <sigh> at most ONCE per call (strictly for genuine caller frustration, never when a caller is simply answering a clarifying question), and use <exhales> at most twice per call.
- Never use <sigh>, <sighs>, or <exhales> in a turn that ends the session or escalates or transfers the call. Open those turns with a tone tag such as <calm><empathetic> instead.
- Other languages: Always keep angle-bracket tags in English keywords (<short pause>, <calm>, <empathetic>, <positive>) even when speaking Spanish, Japanese, Hindi, Mandarin, or Korean — never translate tags (never write <pausa corta> or <短暂停顿>).
- Never use square brackets like [sigh], [short pause], [prosody rate="..."], or [uhm].
</instruction>

<instruction id="text normalization for speech">
Normalize spoken output text into its natural, spelled-out spoken form in the language of the response so that currencies, dates, times, phone numbers, percentages, web and email addresses, street addresses, alphanumeric IDs, postal codes, paired quotas, and tricky names are spoken accurately.

Scope: These rules apply ONLY to the words you speak aloud to the caller. They NEVER apply to tool calls, tool arguments, or internal matching and comparison logic. When you call a tool, pass every value in its original written form exactly as the user or system provided it: pass "1234", not "one two three four"; pass "XED1234", not "X E D one two three four"; pass "2026-09-21" or the format the tool asks for, not "September twenty-first, twenty twenty-six". Spell a value out only in the sentence you say back to the caller.

- Use International Phonetic Alphabet (IPA) spelling in slashes for tricky names or terms, such as /niːv/.
- When speaking a non-English language ({user_language}), always spell out currencies, numbers, dates, times, and percentages in words in that active target language (for example, "ochenta y nueve dólares" in Spanish, "नवासी डॉलर" in Hindi, "八十九美元" in Mandarin, "팔십구 달러" in Korean — never use English number words inside a non-English sentence).
- Write currency, dates, alphanumeric codes, and paired quota figures in words so they read smoothly:
  - <inline_example>"$89" or "$89.00" → "eighty-nine dollars" (never say "point zero zero")</inline_example>
  - <inline_example>"$87.25" → "eighty-seven dollars and twenty-five cents"</inline_example>
  - <inline_example>"$5/day" → "five dollars a day" (never write a slash with a unit; write "a day" or "a month")</inline_example>
  - <inline_example>"42 GB of 100 GB" → "FORTY-TWO of your hundred gigs" (prevents reading "42 / 100" as "forty-two percent")</inline_example>
  - <inline_example>"Jan 02 2026" or "2026-01-02" → "January second, twenty twenty-six"</inline_example>
  - <inline_example>"confirmation XED1234" → "confirmation X E D one two three four" (spell out alphanumeric IDs, account numbers, and postal codes letter-by-letter and digit-by-digit, or group with "..." between chunks)</inline_example>
  - <inline_example>"$0.45" → "forty-five cents"</inline_example>
  - <inline_example>"$3,415.60" → "three thousand four hundred fifteen dollars and sixty cents"</inline_example>
  - <inline_example>"$2,750,000" → "two million seven hundred fifty thousand dollars"</inline_example>
  - <inline_example>"11/04/2026" → "November fourth, twenty twenty-six" (month first)</inline_example>
  - <inline_example>"ticket QT-58204" → "ticket Q T... five eight two zero four"</inline_example>
- Write phone numbers, times, percentages, decimals, ordinals, units, addresses, and abbreviations the way a person says them:
  - <inline_example>"(312) 555-0148" → "three one two... five five five... zero one four eight" (read phone numbers digit by digit in their usual groups)</inline_example>
  - <inline_example>"1-800-555-0199" → "one eight hundred... five five five... zero one nine nine"</inline_example>
  - <inline_example>"14:30" → "two thirty in the afternoon"; "09:05" → "nine oh five in the morning"</inline_example>
  - <inline_example>"12.5%" → "twelve point five percent"; "20%" → "twenty percent"</inline_example>
  - <inline_example>"3.75" → "three point seven five"; "0.5" → "zero point five"</inline_example>
  - <inline_example>"3rd" → "third"; "22nd floor" → "twenty-second floor"</inline_example>
  - <inline_example>"5 km" → "five kilometers"; "250 ml" → "two hundred fifty milliliters"; "20 Mbps" → "twenty megabits per second"</inline_example>
  - <inline_example>"help.example.com/returns" → "help dot example dot com slash returns"</inline_example>
  - <inline_example>"billing@example.com" → "billing at example dot com"</inline_example>
  - <inline_example>"1450 Maple Ave, Unit 3C" → "fourteen fifty Maple Avenue, unit three C"</inline_example>
  - <inline_example>"Dr. Okafor" → "Doctor Okafor"; "18 Birch Dr." → "eighteen Birch Drive"; "Pine St." → "Pine Street"; "St. Clair Ave" → "Saint Clair Avenue"</inline_example>
</instruction>

<Affective Delivery and Voice Guidelines>
1. **EMOTION ATTUNEMENT & MULTI-PART TONE SHIFTS:** Prioritize mixed caller emotions in this order: 'angry' > 'sad' / 'distressed' > 'anxious' > 'frustrated' > 'confused' > 'positive' > 'neutral'.
   - **'angry':** Open with `<calm><empathetic>`. Acknowledge the issue plainly and concisely without defensiveness, then state what you can do right now.
   - **'frustrated':** Open with `<warm><empathetic>`. Acknowledge the friction briefly and plainly, then move directly to helping.
   - **'anxious':** Open with `<calm><empathetic>`. Reassure steadily and concretely by stating specific next steps.
   - **'sad':** Open with `<warm><sympathetic>`. Be respectful, steady, and supportive without over-dramatizing.
   - **'confused':** Open with `<clear><calm>`. Use plain, direct language, give one idea at a time, and check understanding.
   - **'positive':** Open with `<warm><positive>`. Match their positive energy naturally and concisely.
   - **'neutral':** Open with `<warm>` (or no tag). Respond naturally, clearly, and at a comfortable conversational pace.
   - When transitioning from empathy to the resolution or good news in the same turn, shift cleanly at the sentence boundary with `<positive>` or `<warm>`.
   - **THE NEGATIVE-EMOTION LATCH:** If a caller was previously upset and gives a terse reply ("okay", "yes"), hold a patient, calm, unhurried delivery until they express relief. Cap explicit apologies ("I'm sorry") to at most once per call.
2. **THINKING OUT LOUD & MULTILINGUAL NATURALNESS:**
   - Before calling a lookup tool, say a short thinking line (two to five words) in the caller's active language ({user_language}), such as "Let me check that...", "Déjeme revisar eso..." ('es-US'), "稍等，我帮您查一下..." ('cmn-CN'), "जी, एक सेकंड रुकिए..." ('hi-IN'), "네, 잠시만 기다려 주세요..." ('ko-KR'), or "少々お待ちくださいね..." ('ja-JP').
   - Never repeat the same noun phrase in the opening of your post-tool answer that you just said in your pre-tool bridge, and open post-tool answers with a soft conversational connector ("Okay, so...", "Right, so...", "Bueno, ...", "好的，...", "जी, तो...", "네, 확인해 보니...") so the bridge and the answer flow as one continuous human thought.
   - Always pass English parameter enums and raw written identifiers to backend tools, then synthesize the returned guidance naturally into the caller's active language while keeping angle-bracket audio/tone tags in English.
   - **Polite metropolitan register & voice-persona gender agreement:**
     - **Spanish ('es-US'):** Use polite 'usted' register and warm conversational connectors ("Claro que sí", "Perfecto, ya lo veo"). Match first-person adjective gender to the configured voice persona (e.g., feminine "encantada" for female voices like Aoede/Zephyr; masculine "encantado" for male voices like Puck/Charon).
     - **Mandarin ('cmn-CN'):** Use polite '您' ('nín') register, natural conversational particles ('呢', '吧', '好的'), and native Chinese vocabulary.
     - **Hindi ('hi-IN'):** Use polite 'आप' ('aap') and 'जी' ('jee') register, and match first-person verb gender to the configured voice persona (feminine 'सकती हूँ' / 'देखती हूँ' for female voices like Aoede/Zephyr; masculine 'सकता हूँ' / 'देखता हूँ' for male voices like Puck/Charon). Always write brand names, technical acronyms, and loanwords in Devanagari script rather than Latin letters, and avoid dense chains of transliterated English words separated by 'और' (which sounds like English "or" and can cause the TTS phonemizer to code-switch into English mid-sentence).
     - **Korean ('ko-KR'):** Use polite, warm '해요체' / '하십시오체' register with natural spoken connectors ("네, 고객님", "잠시만요") and write brand/service terms in Hangul.
     - **Japanese ('ja-JP'):** Use polite, warm '丁寧語' ('です・ます' form) with natural customer-care phrasing ("かしこまりました", "確認いたしますね").
</Affective Delivery and Voice Guidelines>
```

**Template notes:**

- **Declare `{user_language}`:** The template uses `{user_language}`, which must be declared in `app.json` `variableDeclarations` (lint rule `V104`). `{current_date}` is built in.
- **Keep the full normalization example list:** The examples are the instruction, so keep every example when you adapt the block. Swap the currency examples to the local currency, and adjust date order for day-first locales (e.g., en-AU, en-GB: `"11/04/2026"` → `"the eleventh of April, twenty twenty-six"`).
- **Sigh and exhale limits are optional tuning:** Using `<sigh>` at most once per call and `<exhales>` at most twice per call is a sensible default for customer care; tune it per brand.
- **Keep the ending and transfer rule:** Never use `<sigh>`, `<sighs>`, or `<exhales>` in a turn that ends the session or escalates/transfers the call; open those turns with a tone tag such as `<calm><empathetic>`. The auditor reports violations as `SIGH_ON_ENDING_OR_TRANSFER_TURN`.
- **Prosody is opt-in:** Add the optional pacing line from Section 2C only if the app already uses prosody or the user asks for it.

______________________________________________________________________

## 4. Prompt Contradiction Check & Cross-App Hand-Off Guide

### A. Three-Pass Contradiction Check (Mandatory After Migration)

Contradictions between legacy prompt rules and newly added Composite V2 rules are a common cause of residual tag readouts and tool-argument failures. Check tag rules and normalization rules in three passes:

1. **Pass 1 (Within `global_instruction.txt`):** Compare every voice/tag rule and number-reading rule against every other rule in `global_instruction.txt`.
1. **Pass 2 (`global_instruction.txt` vs. Sub-Agents & Tools):** Compare `global_instruction.txt` against each sub-agent's `agents/*/instruction.txt` and its tool descriptions/docstrings.
1. **Pass 3 (Callbacks & Eval/Test Assertions):** Compare the rules against callbacks and eval/test assertions. For example, a test that forbids adjacent or stacked tags blocks `<calm><empathetic>`.

**Resolution protocol (surface, offer, apply what the user chooses):**

1. **Surface every conflict.** For each one, report the kind (tag or normalization), the tags or value types involved, every file and line where it appears, and the conflicting sentences, quoted.
1. **Offer options:** keep one rule, narrow one rule's scope, or use a different tag.
1. **Apply only what the user chooses.** The only exception: rules that require closing tags are removed, because Composite V2 has no closing tags.

Example conflict report:

```text
Conflict 1 (tag): stacked tone tags such as <calm><empathetic>
- global_instruction.txt:18  "Never stack tags; use one tag at a time."
- global_instruction.txt:64  "Put sound and tone tags at the start of a sentence (stacking is allowed there, such as <calm><empathetic>)."
- agents/billing_agent/instruction.txt:27  "If the caller is angry, open with <calm><empathetic>."
Options: (a) keep one rule: allow stacking at sentence start and drop "Never stack tags"
         (b) narrow the older rule: "Never stack more than two tags, and only at the start of a sentence"
         (c) use a different tag: open angry turns with <empathetic> alone
```

**Common Contradictions to Surface:**

- **Tag Syntax & Placement Conflicts:** Leftover rules saying *"wrap tags in square brackets"*, *"never stack tags"*, *"do not bunch all tags at the front of a sentence"*, *"never use XML/angle-bracket markup in responses"*, or rules requiring closing tags (`</prosody>`). Rules that require closing tags are the one kind that is removed without asking.
- **Number & Date Normalization Conflicts:** A blanket *"read every number digit by digit"* rule fighting cardinal number reading (reserve digit-by-digit for IDs, account numbers, reference codes, and postal codes), conflicting spoken date/currency formats, or a tool instruction that tells the model to pass a spoken form (`"one two three four"`) into a tool parameter.
- **Casual Currency Readouts:** An example that reads a price casually (for example, `"$16.66"` as `"sixteen sixty-six"`) without saying it's an exception to the currency rule.
- **Sighs on Transfer or Goodbye Turns:** A rule that opens transfer or goodbye turns with `<sigh>` or `<exhales>`, which conflicts with the rule against them on those turns.
- **Test Assertions That Block Valid Tags:** Eval or test assertions that forbid adjacent or stacked tags, which blocks `<calm><empathetic>`.
- **Explicit Agent Overrides:** A sub-agent may intentionally override global delivery rules if it states so explicitly (e.g., `"In this agent, these rules override the global Affective Delivery and Voice Guidelines"`). Keep explicit overrides intact, convert their tags to `<...>`, and list them under **Agent overrides** in the migration summary.

Report the results with the summary format in [SKILL.md](../SKILL.md#summary-format).

### B. Cross-App & External Hand-Offs (`transferToNga`, `transferToDialogflow`, `telephonyTransferCall`, `liveAgentHandoff`)

When an agent hands a call to another CX Agent Studio app (`transferToNga`), a Dialogflow CX agent (`transferToDialogflow`), a phone number (`telephonyTransferCall`), or a human queue (`liveAgentHandoff`), the destination runs in a **separate session with its own voice, TTS model, speaking style, and prompts**. `audit_agent_v2.py` automatically detects and lists these hand-offs (as informational `handoff` / `app_reference` entries) so you can inform the application owner if any target app needs its own Composite V2 migration.

______________________________________________________________________

## 5. Acoustic Troubleshooting Matrix: Composite V2 Failure Modes & Fixes

During turn-by-turn `.wav` ear-auditing, watch for these empirical acoustic failure modes and apply their verified remedies:

| Acoustic Symptom in `.wav` Audio                                                                                                      | Root Cause in Prompt / Config                                                                                                                                                                                                       | Verified Composite V2 Fix                                                                                                                                                                                                                                                                                                                                                           |
| :------------------------------------------------------------------------------------------------------------------------------------ | :---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **0. Voice drifts over a multi-turn call or conflicts with inline tags**                                                              | Leftover V1 multi-paragraph `# AUDIO PROFILE` / `# THE SCENE` / `# DIRECTOR'S NOTES` in `synthesizeSpeechConfigs.<locale>.instruction`                                                                                              | Clear the V1 Director's Note to an empty style (`""`); `--remediate` does this and prints the removed text. Only if needed, write a short description of about a dozen words describing **who the speaker is** (age, timbre, pitch, accent, pace), not how they feel.                                                                                                               |
| **1. Double-stutter / an unnatural double pause mid-turn**                                                                            | Stacking an ellipsis right before a pause tag (`"... <short pause>"`) or placing `<short pause>` mid-clause without punctuation                                                                                                     | Never stack `...` and `<short pause>`. Place `<short pause>` only at sentence start or after a comma or full stop (`", <short pause>"` or `". <short pause>"`).                                                                                                                                                                                                                     |
| **2. Loud, barked opening syllable or clipped leading `"I"` / `"We"` after a breath**                                                 | Placing an ALL-CAPS word or leading pronoun right after `<exhales>` or `<sigh>` (e.g., `"<exhales> SOLID green..."`)                                                                                                                | Keep the first word after `<exhales>` or `<sigh>` normal case and move the ALL-CAPS stress word later in the clause: `"<exhales> Solid green on the status light means you're ONLINE."`                                                                                                                                                                                             |
| **3. Number normalizer reads `"X of your 100"` as `"X percent"`**                                                                     | Writing raw digits `"42 of your 100 gigs"` causes `"42 / 100"` to be read as `"forty-two percent"`                                                                                                                                  | Write both numbers in words: `"FORTY-TWO of your hundred gigs"`.                                                                                                                                                                                                                                                                                                                    |
| **4. Pronounceable prefix spelled out letter-by-letter or tricky name mispronounced**                                                 | Writing pronounceable prefixes in all-caps triggers acronym spelling; unusual proper nouns lack phonetic hints                                                                                                                      | Write pronounceable prefixes in mixed case, or provide **IPA spelling in slashes** (e.g., `/niːv/`) for tricky names.                                                                                                                                                                                                                                                               |
| **5. Mouth-click / literal tag readout (`"...warm, your refund..."`)**                                                                | Using legacy V1 square-bracket tags (`[uhm]`, `[warm]`), unremapped tags (`<reassuring>`), or mid-sentence style tags                                                                                                               | Run the auditor with `--remediate` to convert `[...]` to `<...>`, remap `<reassuring>` → `<empathetic>`, and move style tags to sentence start.                                                                                                                                                                                                                                     |
| **6. Tool lookup fails after adding spoken text normalization**                                                                       | Model passes spelled-out words (`"one two three four"`) into a tool argument expecting `"1234"`                                                                                                                                     | Include the **`Scope:`** paragraph in `<instruction id="text normalization for speech">` restricting normalization to spoken output only.                                                                                                                                                                                                                                           |
| **7. Heavy sigh on a routine turn or call-ending/transfer turn**                                                                      | Model inserts `<sigh>` or `<exhales>` when answering a routine question or transferring/ending the call                                                                                                                             | Explicitly forbid `<sigh>`, `<sighs>`, and `<exhales>` on session-ending, escalation, or transfer turns (open them with a tone tag such as `<calm><empathetic>`; the auditor reports `SIGH_ON_ENDING_OR_TRANSFER_TURN`), and optionally cap `<sigh>` (for example, once per call) for genuine frustration.                                                                          |
| **8. Mid-sentence English code-switching in Hindi or non-Latin script (e.g., reading `में आपकी` as `"in your"`, or wrong verb gender)** | Caller asked in English to switch languages (so session `languageCode` stayed `en-*`) and the reply mixed Latin acronyms (`Wi-Fi`, `SIM`, `PIN`) or dense English loanwords separated by `और`, or lacked voice-persona gender rules | Write brand names and acronyms phonetically in the native script (e.g., Devanagari `'वाई-फाई'` and `'सिम'`; checked as `MIXED_LATIN_IN_DEVANAGARI_UTTERANCE`), use native phrasing instead of English loanword chains, and specify first-person verb gender matching the Chirp 3 HD voice persona (`सकती हूँ` for female personas like `Aoede`/`Zephyr`; `सकता हूँ` for male personas). |

______________________________________________________________________

## 6. `after_model_callback` Pattern for Composite V2

Use an `after_model_callback` to deterministically enforce:

1. **Contraction Normalization outside Composite V2 Tags (English-Only Guarded):** Splits text on `INLINE_TAG` covering all Composite V2 non-verbal sound, pause, and sentence-opening tone tags so tags are never altered while uncontracted English forms (`"cannot"`, `"you have used"`, clause-initial `"It is"`) are contracted to `"can't"`, `"you've used"`, `"It's"`. Skip English contraction regexes when the active language is non-English (`cmn-CN`, `hi-IN`, `ko-KR`, `es-US`, `ja-JP`) or when the response contains non-Latin script:
   ```python
   INLINE_TAG = re.compile(
       r"(<(?:"
       r"breath|heavy breath|exhales|pant|sigh|sighs|"
       r"laugh|chuckle|chuckles|giggle|snicker|cackle|"
       r"gasp|cough|sneeze|throat-clearing|snort|tsk|argh|"
       r"sob|cry|whimper|groan|yawn|whispers|whispering|"
       r"short pause|long pause|fast|faster|"
       r"calm|empathetic|warm|positive|enthusiasm|"
       r"clear|determination|interest|relief|sympathetic|"
       r"tension|anxiety|awe|surprised|fearful|negative|disgusted|"
       r'prosody\s+rate="?\d{1,3}%"?'
       r")>|\[[^\]]*\])",
       re.IGNORECASE,
   )
   ```
   The auditor checks that the callback's `INLINE_TAG` pattern covers every tag your instructions actually use (`CALLBACK_MISSING_V2_TAG_REGEX`). When you add a tag to an instruction, add it to `INLINE_TAG` too.
1. **Multilingual Silent Lookup Bridge Fallback:** If the model invokes a lookup tool with no accompanying text part, prepends the least-recently-used bridge phrase in the caller's active language (`"Let me have a look..."`, `"Déjeme revisar eso..."`, `"稍等，我帮您查一下..."`, `"जी, एक सेकंड रुकिए..."`, `"네, 잠시만 기다려 주세요..."`).
1. **Multilingual Warm Transfer Speech Ordering & `end_session` Guard:** Ensures spoken empathetic confirmation in the caller's active language (opened with `<calm><empathetic>`, never `<sigh>` or `<exhales>`, and matching the voice persona's grammatical gender) is emitted *before* the transfer tool call, and strips any parallel `end_session` call from the same turn so `end_session` runs only after the transfer tool returns.

______________________________________________________________________

## 7. Web Portal / Chat Widget Streaming Tag Sanitization

When rendering live transcripts in a web portal alongside Composite V2 audio playback, strip all V2 angle-bracket sound, pause, and tone tags (and any partial trailing `<...` token during streaming) so the customer only sees clean text while hearing the vocalizations and tone shifts in audio:

```typescript
// Strip complete Composite V2 angle-bracket sound/pause/tone tags and any legacy V1 square-bracket tags
const AUDIO_TAG_REGEX =
  /<(?:breath|heavy breath|exhales|pant|sigh|sighs|laugh|chuckle|chuckles|giggle|snicker|cackle|gasp|cough|sneeze|throat-clearing|snort|tsk|argh|sob|cry|whimper|groan|yawn|whispers|whispering|short pause|long pause|fast|faster|calm|empathetic|warm|positive|enthusiasm|clear|determination|interest|relief|sympathetic|tension|anxiety|awe|surprised|fearful|negative|disgusted|prosody\s+rate="?\d{1,3}%"?)>|\[(?:prosody[^\]]*|short pause|medium pause|long pause|sigh|sighs|exhales|chuckles|laughs|gasp|uhm|positive|neutral|reassuring|curiosity|professional|cautious|slow|whispers|seriousness)\]/gi;

// Strip trailing incomplete tag during token streaming (e.g. "Hello <short pa")
const PARTIAL_AUDIO_TAG_REGEX = /<[^>]*$|\[[^\]]*$/;

export function sanitizeTranscriptText(raw: string): string {
  return raw
    .replace(AUDIO_TAG_REGEX, '')
    .replace(PARTIAL_AUDIO_TAG_REGEX, '')
    .replace(/\s{2,}/g, ' ')
    .trim();
}
```
