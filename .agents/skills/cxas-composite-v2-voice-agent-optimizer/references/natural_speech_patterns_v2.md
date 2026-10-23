# Gemini Composite V2 (`gemini-composite-v2`) Natural Speech Patterns, Prompt Templates & Callback Reference

This reference consolidates the official **Gemini Composite V2** (`gemini-composite-v2`) speaking style guidelines, inline sound and multi-part tone tag taxonomy, empirical tag replacement tables, tool-scoped text normalization templates, contradiction checks, callback patterns, frontend tag sanitizers, and acoustic troubleshooting rules.

______________________________________________________________________

## 1. `app.json` & Speaking Style Configuration for Composite V2

### Removing Legacy V1 Director's Notes

Many Composite V1 (`gemini-composite-v1`) agents use a long **Director's Note** (`# AUDIO PROFILE`, `# THE SCENE`, `# DIRECTOR'S NOTES`, and trailing `## Transcript:`) as the speaking style in `audioProcessingConfig.synthesizeSpeechConfigs.<locale>.instruction`. **Remove it before switching to `gemini-composite-v2`.** Long instructions can make the voice drift over a call and conflict with inline tags.

### How to Write a Composite V2 Speaking Style

1. **Start simple (Empty Style):** Test your agent with an empty `instruction` (`""`) first—Composite V2 reads punctuation and context well out of the box.
1. **Short Speaker-Identity Description (~12 words):** When you want to shape the baseline voice, write a description of **about a dozen words** (≤ 25 words max) focusing on **who the speaker is** (age, vocal timbre, pitch, regional accent, and baseline cadence) rather than temporary emotions (use inline tags for moment-to-moment emotion).
1. **Concise 4-Adjective Baseline:** Alternatively, use the concise 4-word style `"warm, friendly, natural, professional"`.

| Use Case                   | V2 Speaking Style (`instruction`)                                                               | Sample Line                                                                                                  |
| :------------------------- | :---------------------------------------------------------------------------------------------- | :----------------------------------------------------------------------------------------------------------- |
| **General Support**        | `"Friendly American woman in her early 30s, warm mid-pitch voice, relaxed conversational pace"` | `"Hi there, I'd be happy to look into that for you right away."`                                             |
| **Retail Support**         | `"Upbeat woman in her late 20s, bright mid-range voice, quick, friendly cadence"`               | `"Thanks for calling! <breath> I can help you track that order. What's your order number?"`                  |
| **Banking**                | `"Calm man in his 40s, steady low baritone, measured and unhurried delivery"`                   | `"I can see the charge you're asking about, <short pause> and I'll walk you through exactly what happened."` |
| **Telecom**                | `"Friendly Southern US woman in her 50s, warm alto, relaxed, patient drawl"`                    | `"Alright, let's get your internet back up. <short pause> Go ahead and unplug your router for me."`          |
| **Travel**                 | `"Young British man, crisp diction, clear tenor voice, brisk yet polite pace"`                  | `"Good news, <short pause> I found you a seat on the six forty to Heathrow. Shall I book it?"`               |
| **Minimal / Multi-Locale** | `"warm, friendly, natural, professional"` (or `""`)                                             | `"Right, let me check your account... <short pause> everything looks SOLID on our end."`                     |

### Example `app.json` for `gemini-composite-v2`

```json
{
  "modelSettings": {
    "model": "gemini-composite-v2",
    "temperature": 1.0
  },
  "languageSettings": {
    "defaultLanguageCode": "en-US",
    "supportedLanguageCodes": ["es-US", "hi-IN", "cmn-CN", "ko-KR"],
    "enableMultilingualSupport": true
  },
  "audioProcessingConfig": {
    "synthesizeSpeechConfigs": {
      "en-US": {
        "voice": "en-US-Chirp3-HD-Zephyr",
        "speakingRate": 1.0,
        "instruction": "Friendly American woman in her early 30s, warm mid-pitch voice, relaxed conversational pace"
      },
      "es-US": {
        "voice": "es-US-Chirp3-HD-Zephyr",
        "speakingRate": 1.0,
        "instruction": "warm, friendly, natural, professional"
      },
      "hi-IN": {
        "voice": "hi-IN-Chirp3-HD-Zephyr",
        "speakingRate": 1.0,
        "instruction": "warm, friendly, natural, professional"
      },
      "cmn-CN": {
        "voice": "cmn-CN-Chirp3-HD-Zephyr",
        "speakingRate": 1.0,
        "instruction": "warm, friendly, natural, professional"
      },
      "ko-KR": {
        "voice": "ko-KR-Chirp3-HD-Zephyr",
        "speakingRate": 1.0,
        "instruction": "warm, friendly, natural, professional"
      }
    },
    "bargeInConfig": {
      "bargeInAwareness": true
    },
    "inactivityTimeout": "20s"
  }
}
```

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

### C. Empirical Tag Replacement Table (V1 → V2)

Evaluations on Composite V2 showed that certain legacy V1 tags are frequently ignored, spoken out loud, or outperformed by a sibling tag. `audit_agent_v2.py --remediate` automatically replaces these and deduplicates any resulting tag lists:

| Legacy / Weak Tag   | Use Instead in Composite V2                 | Why                                                                              |
| :------------------ | :------------------------------------------ | :------------------------------------------------------------------------------- |
| `reassuring`        | `<empathetic>`                              | Frequently spoken aloud mid-sentence; `<empathetic>` is reliably followed        |
| `curiosity`         | `<interest>`                                | Often ignored or spoken aloud; `<interest>` performs significantly better        |
| `professional`      | `<clear>`                                   | `<clear>` produces cleaner, more articulate customer-service delivery            |
| `cautious`          | `<calm>`                                    | `<calm>` steadies delivery reliably                                              |
| `alarm`             | `<calm>`                                    | Rarely changes delivery; `<calm>` is reliable                                    |
| `nervousness`       | `<calm>`                                    | `<calm>` is reliable and avoids vocal instability                                |
| `happy`             | `<positive>`                                | `<positive>` produces a clearer upbeat lift                                      |
| `admiration`        | `<positive>`                                | Acoustically indistinct from `<warm>` / `<positive>`                             |
| `hope`              | `<warm>`                                    | Frequently ignored or spoken aloud; `<warm>` is reliable                         |
| `adoration`         | `<warm>`                                    | Unreliable; `<warm>` is reliable                                                 |
| `crying`            | `<sympathetic>`                             | Renders as unstable pitch; `<sympathetic>` conveys gentle support                |
| `laughs`            | `<chuckles>`                                | `<chuckles>` is more reliable and warmer on customer support calls               |
| `annoyance`         | `<determination>`                           | `<determination>` sounds firm and focused rather than irritated                  |
| `aggression`        | `<determination>`                           | `<determination>` conveys firm resolve without hostility                         |
| `medium pause`      | `<short pause>`                             | Standardize mid-sentence pauses to `<short pause>` or `<long pause>`             |
| `neutral`           | *Delete tag* (or use `<warm>`)              | No longer recommended as an inline tag; leave untagged or open with `<warm>`     |
| `uhm` / `um` / `uh` | *Delete tag* (use `...` or `<short pause>`) | Bracketed filler tags cause mouth-click artifacts; use ellipses (`...`) or words |

### D. Delivery Effects & Known Acoustic Side Effects

| Tag                           | Effect on Delivery                                                      |
| :---------------------------- | :---------------------------------------------------------------------- |
| `<breath>` / `<exhales>`      | Audible breath release before speech; natural transition into an answer |
| `<sigh>` / `<sighs>`          | Audible empathetic inhale/exhale (`<sighs>` is heavier than `<sigh>`)   |
| `<chuckle>` / `<chuckles>`    | Crisp, warm chuckle; the preferred laugh on support calls               |
| `<whispers>` / `<whispering>` | Quiet, soft spoken delivery (`<whispering>` has the strongest drop)     |
| `<short pause>`               | Brief silence inserted at a comma or full-stop boundary                 |
| `<long pause>`                | Noticeably longer silence than `<short pause>`                          |
| `<fast>` / `<faster>`         | Slightly brisker cadence                                                |

- **Never use `<sigh>`, `<sighs>`, or `<exhales>` on call-ending or transfer turns:** While fine in an empathetic moment, never emit them on a turn that ends the session or escalates/transfers the call.
- **Initial pronoun clipping & ALL-CAPS bark:** Intense paralinguistic tags (`<exhales>`, `<sighs>`) at a sentence start can clip a leading `"I"` or `"We"`, or bark if followed immediately by an ALL-CAPS word. Keep the first word after `<exhales>` or `<sigh>` in normal case and place ALL-CAPS stress later in the clause.
- **Pause tag punctuation:** Always precede a mid-sentence `<short pause>` or `<long pause>` with a comma or full stop (`"I checked your account, <short pause> everything is in order."`). Never stack `... <short pause>`.

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
- Always use spoken contractions in English ("I'm", "you've", "it's", "that's", "there's", "they're", "we've", "can't", "won't", "let's"). Never use stiff, uncontracted forms like "I am sorry", "You have used", or "There are no". Use plain words with no lists, no markdown, and no emojis.
- Sound like you're really working: light backchannels when the caller explains something ("mm-hm... yeah... okay, got it."), small hedges ("so it looks like...", "from what I can see here..."), and ellipses ('...') for natural hesitation beats.
- Put CAPITAL letters on the one or two words per turn that matter most, the way you'd lean on them out loud: NO outages, SOLID green, FORTY-TWO gigs, EIGHTY-NINE dollars.
- End each turn with a specific, natural check-in about the caller's situation. Never say "Is there anything else I can help you with?" on intermediate turns, and never say "unfortunately".
</persona>

<instruction id="audio tag format">
You can add natural human sounds, pauses, and mid-turn tone transitions using tags in angle brackets. They are performed by the voice model, never read aloud.
- Allowed non-verbal sound & pause tags: <breath> <exhales> <sigh> <laugh> <chuckle> <chuckles> <gasp> <throat-clearing> <short pause> <long pause>
- Allowed sentence-opening tone transition tags: <calm> <empathetic> <warm> <positive> <enthusiasm> <clear> <determination> <interest> <relief>
- Placement: Put sound and tone tags at the start of a sentence (stacking is allowed there, such as <calm><empathetic>). Pause tags (<short pause>, <long pause>) can also go mid-sentence after punctuation (', <short pause>' or '. <short pause>', for example: "I checked your account, <short pause> everything is in order."). Never put a tag in the middle of a phrase without punctuation, and never stack <short pause> or <long pause> right after an ellipsis ('...') — use either '...' for a light hesitation or ', <short pause>' / '. <short pause>' between steps, never both together.
- Multi-part tone transitions & no closing tags: Open a sentence with a tone tag (<calm>, <empathetic>, <warm>, <positive>) when shifting tone mid-response; a tone tag stays in effect until the next tone tag (for example: "<calm><empathetic> I'm so sorry your flight was canceled. That's a stressful way to start a trip. <positive> The good news is that I've already rebooked you on the next flight out."). Never write a closing tag (</prosody>, </warm>, or any </...>).
- Never place an ALL-CAPS stress word as the very first word immediately after <exhales> or <sigh>; let the opening word be normal case and put the ALL-CAPS stress later in the clause (for example: "<exhales> Solid green on the status light means you're ONLINE.").
- Less is more: One or two tags per turn are enough for most customer conversations, and most routine turns need NO tag at all. Use <sigh> at most ONCE per call (strictly for genuine caller frustration, never when a caller is simply answering a clarifying question), use <exhales> at most twice per call, and never use <sigh>, <sighs>, or <exhales> in a turn that ends the session or escalates or transfers the call.
- Other languages: Always keep angle-bracket tags in English keywords even when speaking Spanish, Japanese, Hindi, Mandarin, or Korean.
- Never use square brackets like [sigh], [short pause], [prosody rate="..."], or [uhm].
</instruction>

<instruction id="text normalization for speech">
Normalize spoken output text into its natural, spelled-out spoken form in the language of the response so that currencies, dates, times, alphanumeric IDs, postal codes, paired quotas, and tricky names are spoken accurately.

Scope: These rules apply ONLY to the words you speak aloud to the caller. They NEVER apply to tool calls, tool arguments, or internal matching and comparison logic. When you call a tool, pass every value in its original written form exactly as the user or system provided it: pass "1234", not "one two three four"; pass "XED1234", not "X E D one two three four"; pass "2026-09-21" or the format the tool asks for, not "September twenty-first, twenty twenty-six". Spell a value out only in the sentence you say back to the caller.

- Use International Phonetic Alphabet (IPA) spelling in slashes for tricky names or terms, such as /niːv/.
- Write currency, dates, alphanumeric codes, and paired quota figures in words so they read smoothly:
  - <inline_example>"$89" or "$89.00" → "eighty-nine dollars" (never say "point zero zero")</inline_example>
  - <inline_example>"$87.25" → "eighty-seven dollars and twenty-five cents"</inline_example>
  - <inline_example>"$5/day" → "five dollars a day" (never write a slash with a unit; write "a day" or "a month")</inline_example>
  - <inline_example>"42 GB of 100 GB" → "FORTY-TWO of your hundred gigs" (prevents reading "42 / 100" as "forty-two percent")</inline_example>
  - <inline_example>"Jan 02 2026" or "2026-01-02" → "January second, twenty twenty-six"</inline_example>
  - <inline_example>"confirmation XED1234" → "confirmation X E D one two three four" (spell out alphanumeric IDs, account numbers, and postal codes letter-by-letter and digit-by-digit, or group with "..." between chunks)</inline_example>
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
2. **THINKING OUT LOUD & MULTILINGUAL BRIDGE PHRASES:**
   - Before calling a lookup tool, say a short thinking line (two to five words) in the caller's active language ({user_language}).
   - Never repeat the same noun phrase in the opening of your post-tool answer that you just said in your pre-tool bridge, and open post-tool answers with a soft conversational connector ("Okay, so...", "Right, so...") so the bridge and the answer flow as one continuous human thought.
   - Always pass English parameter enums and raw written identifiers to backend tools, then synthesize the returned guidance naturally into the caller's active language while keeping angle-bracket audio/tone tags in English.
</Affective Delivery and Voice Guidelines>
```

______________________________________________________________________

## 4. Prompt Contradiction Check & Cross-App Hand-Off Guide

### A. Two-Pass Contradiction Check (Mandatory After Migration)

Contradictions between legacy prompt rules and newly added Composite V2 rules are the #1 cause of residual tag readouts or tool-argument failures. Run a two-pass review:

1. **Pass 1 (Within `global_instruction.txt`):** Compare every voice/tag rule and number-reading rule against every other rule in `global_instruction.txt`.
1. **Pass 2 (`global_instruction.txt` vs. Sub-Agents & Tools):** Compare `global_instruction.txt` against each sub-agent's `agents/*/instruction.txt` and its tool descriptions/docstrings.

**Common Contradictions to Fix:**

- **Tag Syntax & Placement Conflicts:** Leftover rules saying *"wrap tags in square brackets"*, *"never stack tags"*, *"do not bunch all tags at the front of a sentence"*, *"never use XML/angle-bracket markup in responses"*, or rules requiring closing tags (`</prosody>`).
- **Number & Date Normalization Conflicts:** A blanket *"read every number digit by digit"* rule fighting cardinal number reading (reserve digit-by-digit for IDs, account numbers, reference codes, and postal codes), conflicting spoken date/currency formats, or a tool instruction that tells the model to pass a spoken form (`"one two three four"`) into a tool parameter.
- **Explicit Agent Overrides:** A sub-agent may intentionally override global delivery rules if it states so explicitly (e.g., `"In this agent, these rules override the global Affective Delivery and Voice Guidelines"`). Keep explicit overrides intact, convert their tags to `<...>`, and list them under **Agent Overrides** in the migration report.

### B. Cross-App & External Hand-Offs (`transferToNga`, `transferToDialogflow`, `telephonyTransferCall`, `liveAgentHandoff`)

When an agent hands a call to another CES app (`transferToNga`), a Dialogflow CX agent (`transferToDialogflow`), a phone number (`telephonyTransferCall`), or a human queue (`liveAgentHandoff`), the destination runs in a **separate session with its own voice, TTS model, speaking style, and prompts**. `audit_agent_v2.py` automatically detects and lists these hand-offs so you can inform the application owner if any target CES app needs its own Composite V2 migration.

______________________________________________________________________

## 5. Acoustic Troubleshooting Matrix: Composite V2 Failure Modes & Fixes

During turn-by-turn `.wav` ear-auditing, watch for these empirical acoustic failure modes and apply their verified remedies:

| Acoustic Symptom in `.wav` Audio                                                      | Root Cause in Prompt / Config                                                                                                          | Verified Composite V2 Fix                                                                                                                                                               |
| :------------------------------------------------------------------------------------ | :------------------------------------------------------------------------------------------------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **0. Voice drifts over a multi-turn call or conflicts with inline tags**              | Leftover V1 multi-paragraph `# AUDIO PROFILE` / `# THE SCENE` / `# DIRECTOR'S NOTES` in `synthesizeSpeechConfigs.<locale>.instruction` | Remove the V1 Director's Note. Leave `instruction` empty (`""`) or replace with a ~12-word description of **who the speaker is** (age, timbre, pitch, accent, baseline cadence).        |
| **1. Double-stutter / unnatural ~900ms gap mid-turn**                                 | Stacking an ellipsis right before a pause tag (`"... <short pause>"`) or placing `<short pause>` mid-clause without punctuation        | Never stack `...` and `<short pause>`. Place `<short pause>` only at sentence start or after a comma or full stop (`", <short pause>"` or `". <short pause>"`).                         |
| **2. Loud, barked opening syllable or clipped leading `"I"` / `"We"` after a breath** | Placing an ALL-CAPS word or leading pronoun right after `<exhales>` or `<sigh>` (e.g., `"<exhales> SOLID green..."`)                   | Keep the first word after `<exhales>` or `<sigh>` normal case and move the ALL-CAPS stress word later in the clause: `"<exhales> Solid green on the status light means you're ONLINE."` |
| **3. Number normalizer reads `"X of your 100"` as `"X percent"`**                     | Writing raw digits `"42 of your 100 gigs"` causes `"42 / 100"` to be read as `"forty-two percent"`                                     | Write both numbers in words: `"FORTY-TWO of your hundred gigs"`.                                                                                                                        |
| **4. Pronounceable prefix spelled out letter-by-letter or tricky name mispronounced** | Writing pronounceable prefixes in all-caps triggers acronym spelling; unusual proper nouns lack phonetic hints                         | Write pronounceable prefixes in mixed case, or provide **IPA spelling in slashes** (e.g., `/niːv/`) for tricky names.                                                                   |
| **5. Mouth-click / literal tag readout (`"...warm, your refund..."`)**                | Using legacy V1 square-bracket tags (`[uhm]`, `[warm]`), unremapped tags (`<reassuring>`), or mid-sentence style tags                  | Run `audit_agent_v2.py --remediate` to convert `[...]` to `<...>`, remap `<reassuring>` → `<empathetic>`, and move style tags to sentence start.                                        |
| **6. Tool lookup fails after adding spoken text normalization**                       | Model passes spelled-out words (`"one two three four"`) into a tool argument expecting `"1234"`                                        | Include the **`Scope:`** paragraph in `<instruction id="text normalization for speech">` restricting normalization to spoken output only.                                               |
| **7. Heavy sigh on a routine turn or call-ending/transfer turn**                      | Model inserts `<sigh>` or `<exhales>` when answering a routine question or transferring/ending the call                                | Explicitly forbid `<sigh>`, `<sighs>`, and `<exhales>` on session-ending, escalation, or transfer turns, and cap `<sigh>` to at most once per call for genuine frustration.             |

______________________________________________________________________

## 6. `after_model_callback` Pattern for Composite V2

Use an `after_model_callback` to deterministically enforce:

1. **Contraction Normalization outside Composite V2 Tags:** Splits text on `INLINE_TAG` covering all Composite V2 non-verbal sound, pause, and sentence-opening tone tags so tags are never altered while uncontracted English forms (`"cannot"`, `"you have used"`, clause-initial `"It is"`) are contracted to `"can't"`, `"you've used"`, `"It's"`:
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
1. **Silent Lookup Bridge Fallback:** If the model invokes a lookup tool with no accompanying text part, prepends the least-recently-used bridge phrase (`"Let me have a look..."`, `"Sure, one sec..."`, `"Bear with me a sec..."`).
1. **Warm Transfer Speech Ordering & `end_session` Guard:** Ensures spoken empathetic confirmation is emitted *before* the transfer tool call, and strips any parallel `end_session` call from the same turn so `end_session` runs only after the transfer tool returns.

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
