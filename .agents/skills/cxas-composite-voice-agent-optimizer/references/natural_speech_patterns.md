# Natural Speech Patterns & Anti-Looping Architecture Guide

With Gemini Composite v2, delivery is steered by **inline audio tags** that the reasoning model writes into its response. Director's Notes are cleared during migration, and speaking style instructions in `app.json` are kept empty (`""`, mandatory for custom cloned voices) or concise (`≤ 25` words — ideally a ~12-word speaker-identity profile for prebuilt voices) so they do not conflict with inline tags. Natural prosody, micro-pauses, emotion attunement, and pacing are engineered into the global and agent instructions.

See the [Tag Reference](tag_reference.md) for the tag vocabulary, the tags to replace, and syntax rules.

______________________________________________________________________

## 0. Voice Persona, Custom Voice & Speaking Style Configuration (`app.json`)

- **Model (`CV201`):** Set `modelSettings.model` to `"gemini-composite-v2"` in `app.json` (and upgrade any older Composite model overrides in `agents/*/*.json`). `temperature` and `speakingRate` are **not** Composite v2 requirements and are never modified by `--remediate`.
- **Prebuilt Voices (`CV204`, default `Aoede`):** When using prebuilt Chirp 3 HD voices instead of `customVoiceSamples[]`, default to **`Aoede`** (`<locale>-Chirp3-HD-Aoede`, e.g. `en-US-Chirp3-HD-Aoede`) across all declared locales unless the app already configures another valid `*-Chirp3-HD-*` persona or the user requests a different voice. When `languageSettings.supportedLanguageCodes` declares additional locales, set `"enableMultilingualSupport": true`.
- **Prebuilt Speaking Style (`CV203`):** Either write a ~12-word speaker-identity profile (`≤ 25` words max) describing **who the speaker is** (age, vocal timbre, pitch, accent, pace) or leave `"instruction": ""`. Never use feeling-word lists (`"warm, friendly, natural, professional"`), and never copy an English accent descriptor (`"American woman..."`, `"Australian woman..."`) into non-English locales (`es-US`, `hi-IN`, `cmn-CN`, `ko-KR`, `CV203`) — omit the English accent word or leave non-English locale instructions `""`.
- **Custom Cloned Voices (`CV205`, `audioProcessingConfig.customVoiceSamples[]`):** Set valid `gs://...` URIs on **both** `voiceSampleGcsUri` and `consentAudioGcsUri` (never use deprecated inline `synthesizeSpeechConfigs.<lang>.voiceSampleGcsUri`), set `"voiceInstructionMode": "NO_INSTRUCTION"` (so `cxas push` never auto-generates `<agent_voice>` DSP blocks or restores cached `priorVoiceInstruction` text), strip transient `previewAudioContent` blobs, and set **both** `voiceInstruction` and bound `synthesizeSpeechConfigs.<lang>.instruction` to `""`.

| Vertical | Prebuilt Speaking Style (`synthesizeSpeechConfigs.<lang>.instruction`) | Sample Line |
| :--- | :--- | :--- |
| **General Support** | `"Friendly American woman in her early 30s, warm mid-pitch voice, relaxed conversational pace"` | `"Hi there, I'd be happy to look into that for you right away."` |
| **Retail Support** | `"Upbeat woman in her late 20s, bright mid-range voice, quick, friendly cadence"` | `"Thanks for calling. <breath> I can help you track that order. What's your order number?"` |
| **Banking** | `"Calm man in his 40s, steady low baritone, measured and unhurried delivery"` | `"I can see the charge you're asking about, <short pause> and I'll walk you through it."` |
| **Telecom** | `"Friendly Southern US woman in her 50s, warm alto, relaxed, patient drawl"` | `"Alright, let's get your internet back up. <short pause> Go ahead and unplug your router."` |
| **Travel** | `"Young British man, crisp diction, clear tenor voice, brisk yet polite pace"` | `"Good news, <short pause> I found you a seat on the six forty to London. Shall I book it?"` |
| **Unstyled / Custom** | `""` *(empty — secondary option for prebuilt; mandatory for custom cloned voices)* | `"Right, let me check your account... everything looks SOLID on our end."` |

**Chirp 3 HD Persona Gender Reference (for gendered languages such as Spanish, Hindi, French, Portuguese):**
- **Female:** `Aoede` *(default)*, `Zephyr`, `Kore`, `Leda`, `Callirrhoe`, `Autonoe`, `Erinome`, `Laomedeia`, `Pulcherrima`, `Vindemiatrix`, `Despina`, `Gacrux`, `Achernar`, `Sulafat`
- **Male:** `Puck`, `Charon`, `Fenrir`, `Orus`, `Enceladus`, `Iapetus`, `Umbriel`, `Algieba`, `Algenib`, `Rasalgethi`, `Alnilam`, `Schedar`, `Achird`, `Zubenelgenubi`, `Sadachbia`, `Sadaltager`

______________________________________________________________________

## 1. Speech Tag Instructions for Natural Voice Delivery

Instructions in the global instruction (and, where needed, `agents/*/instruction.txt`) must direct the model to evaluate caller emotion on every turn and open each turn with tags from the [Tag Reference](tag_reference.md).

### 1.1 The 7 Core Affective Registers & Complementary Response Mappings

On EVERY turn, the agent evaluates the caller's emotional state from their words and tone, and responds with the **appropriate stabilizing or empathetic delivery**, never mirroring panic, fear, or anger:

| Caller Emotion & Signals | Target Agent Demeanor | Opening Tags | Delivery Guidance | Example Response |
| :--- | :--- | :--- | :--- | :--- |
| **`angry`**<br>Outraged, furious, threatening complaints, accusing of mistreatment. | **Calm, Validating & Steady** | `<calm><empathetic>` | Steady and validating. Never defensive, hurried, or cheerful. Acknowledge the disruption plainly before anything else. | `"<calm><empathetic> I hear you, and being stranded overnight after that cancellation is not acceptable. <short pause> Let me check rebooking options right now."` |
| **`frustrated`**<br>Annoyed, tired of repeating details, app errors, long delays. | **Patient, Direct & Solution-Focused** | `<warm><empathetic>` | Briefly acknowledge the friction, then take ownership and offer a clear path forward. | `"<warm><empathetic> I understand how frustrating it is when the seat map keeps failing. <short pause> Let's assign your seats together right now."` |
| **`anxious`**<br>Stressed, worried about missed connections, emergencies, lost luggage. | **Grounded, Reassuring & Clear** | `<calm><empathetic>` | Do NOT mirror panic. Give steady reassurance and concrete next steps so the caller knows it is handled. | `"<calm><empathetic> Your connection is protected, and we will get you to London today. <short pause> Let me look at alternative flights now."` |
| **`sad` / `distressed`**<br>Bereavement, medical emergency, overwhelmed. | **Gentle, Sympathetic & Unhurried** | `<warm><sympathetic>` | Gentle and unhurried. Sincere sympathy without rushing. `<sigh>` is fine here. | `"<sigh><sympathetic> I am truly sorry for your loss. <short pause> Take your time... we will handle the travel arrangements at whatever pace you need."` |
| **`confused`**<br>Lost, struggling with fare rules, policies, or complex steps. | **Clear & Step-by-Step** | `<clear><calm>` | Plain vocabulary, one idea at a time, check understanding before moving on. | `"<clear><calm> Let's take this one step at a time. <short pause> Your basic economy ticket includes one personal item, but checked bags are added separately."` |
| **`positive`**<br>Relieved, thankful, happy. | **Warm, Appreciative & Genuine** | `<warm><positive>` | Match their warmth briefly and sincerely without sounding performative. | `"<warm><positive> You're very welcome. I'm glad we could get that aisle seat confirmed for you today."` |
| **`neutral`**<br>Calm, transactional, status inquiry. | **Approachable, Efficient & Natural** | `<warm>` | Natural, clear, normal conversational pace. | `"<warm> Got that pulled up for you. <short pause> Your flight is on schedule and boards at three fifteen this afternoon."` |

> [!NOTE]
> The register *named* `neutral` is fine; the `<neutral>` tag is not. It performs poorly on Composite v2 and is removed during migration.

Prosody is optional. If the original instructions used rates, keep them verbatim and put the rate first in each opener (`<prosody rate="64%"><calm><empathetic>`). If they did not, ask the user before adding any. See [Tag Reference §7](tag_reference.md#7-prosody-rates).

### 1.2 Acute Emotion Precedence & The Negative-Emotion Latch

#### 1. Acute Precedence (Resolving Mixed Emotions)

If a turn shows mixed emotions, select the register of the highest-priority emotion:

$$\text{angry} \succ \text{sad} \succ \text{anxious} \succ \text{frustrated} \succ \text{confused} \succ \text{positive} \succ \text{neutral}$$

#### 2. The Negative-Emotion Latch (CRITICAL)

If a caller was previously upset (`angry`, `sad`, `anxious`, `frustrated`) and then gives a short, transactional response (*"okay,"* *"yes,"* reading out a reference), **NEVER snap back to an upbeat persona**.

> [!IMPORTANT]
> A terse answer from an upset caller does **not** mean they feel better. Keep a composed, patient register until the caller explicitly expresses relief or gratitude.

### 1.3 Premature Celebration Prevention

When resolving an issue for an upset caller, never celebrate or sound triumphant:

- ❌ `"<enthusiasm> Great news! I got you onto that morning flight! Isn't that fantastic?"`
- ✅ `"<calm> Alright, I've confirmed your seat on tomorrow's seven thirty departure to Chicago. <short pause> Your updated boarding pass is in your email."`

Lead with validation, and deliver good news gently. When shifting from empathy to a constructive resolution in the same response for a calmer caller, open the new sentence with a tone tag:

- ✅ `"<calm><empathetic> I'm so sorry your flight was canceled. That's a stressful way to start a trip. <positive> The good news is that I've already rebooked you on the next flight out."`

### 1.4 Inline Tag Syntax Standards

1. **Angle brackets & English keywords only (`CV210`):** `<warm>`, `<breath>`, `<short pause>`. Never `[warm]`, and never translate tags into other languages (`<pausa corta>`, `<短暂停顿>`).
2. **Tags at the sentence start; stacking & mid-turn shifts allowed there:** `<calm><empathetic> I understand. <positive> Here is what we can do.`
3. **Mid-sentence, only pause tags, after punctuation (`CV214`):** `"I checked your account, <short pause> everything is in order."` Never stack `... <short pause>` (`CV215`).
4. **No closing tags (`CV213`).** Reset pace with `<prosody rate="100%">` instead of `</prosody>`, and put `<prosody rate="N%">` first in a stack.
5. **No ALL-CAPS immediately after `<exhales>` or `<sigh>` (`CV215`):** Keep the first word after `<exhales>` or `<sigh>` normal case and place ALL-CAPS stress later in the clause; never use `<sigh>`, `<sighs>`, or `<exhales>` on call-ending or transfer turns.
6. **Tag sparsity:** 1–2 tags per turn are plenty; routine turns need no sound tag at all.
7. **Punctuation:** never use exclamation marks; end sentences with full stops or ellipses.

Rules written for the old format must be rewritten, not kept alongside: *"wrap tags in square brackets"*, *"never stack tags"*, *"do not bunch all tags at the front of a sentence"*.

### 1.5 Production Prompt Template (Global Instruction)

#### Placement Guidelines

- **Merge, don't overwrite.** Read the existing global instruction in full first. If it already has a voice or affective delivery section, put the *Audio Tag Format* block (section 4 below) inside it and edit any rule it contradicts in place. Add the full template only when no such section exists.
- **Where it lives.** `globalInstruction` in `app.json` is a filename pointer (`"globalInstruction": "global_instruction.txt"`). If the app has none, create `global_instruction.txt` beside `app.json` and add the key at the top level of `app.json`.
- **Match the file's formatting** (heading style, numbering, bullet pattern) and adapt the registers and examples to the customer's domain, keeping the structure: recognize the emotion, pick a register, open the turn with its tags.
- **Text normalization (`CV217`):** Do **not** add generic text normalization rules when all configured locales are covered by platform runtime inline TTS text normalization (`en-*`, `fr`/`fr-CA`, `es`/`es-US`). Add the `Scope:`-guarded `<instruction id="text normalization for speech">` block in [§2.3](#23-numbers-dates--identifiers) **only** when the app configures unsupported locales or needs domain-specific overrides.
- **Prosody:** the template has none. If the user wants it, put their rate first in each opener and add the two prosody bullets shown after the template.

```text
<instruction id="Affective_Delivery_and_Voice_Guidelines">
You are optimized for natural, empathetic voice interaction on a phone call. On EVERY turn, you must do all of the following:
1. Actively evaluate the caller's emotional state from their words and tone.
2. Select the matching affective register.
3. Shape your spoken delivery using inline audio tags and human conversational texture.

### 1. Emotion Recognition & The Negative-Emotion Latch
Identify which of the following 7 affective registers best matches the caller's current emotional state:
1. 'angry': Outraged, furious, threatening complaints or lawsuits, accusing of scams or rip-offs.
2. 'sad': Upset, distressed, grieving, financially struggling, or overwhelmed.
3. 'anxious': Worried, stressed, or panicking over an urgent problem.
4. 'frustrated': Annoyed, tired of repeating themselves, getting the runaround, finding things too complicated.
5. 'confused': Lost, not understanding charges or policy, needing clarification.
6. 'positive': Relieved, thankful, happy, or grateful.
7. 'neutral': Calm, factual, or transactional.

- **ACUTE PRECEDENCE:** If a turn shows mixed emotions, prioritize them in this order (highest first): 'angry', 'sad', 'anxious', 'frustrated', 'confused', 'positive', 'neutral'. Always select the register with the highest precedence.
- **THE NEGATIVE-EMOTION LATCH:** If a caller was previously upset ('angry', 'sad', 'anxious', 'frustrated') and then gives a neutral transactional response (e.g., "okay," "yes," "1234"), don't snap to an overly cheerful or celebratory tone; transition back to a composed, matter-of-fact 'neutral' delivery once they are actively moving through the task.
- **NEVER CELEBRATE PREMATURELY:** For any upset caller, always lead with validation or acknowledgment first. Even when delivering a successful waiver, credit, or good news, never sound celebratory or upbeat; deliver the good news gently and humbly.
- **RESPECTFUL, NON-PATRONIZING TONE (CRITICAL):** Speak like a capable, approachable human rather than a stuffy corporate script or an overly familiar caretaker. Never sound condescending, syrupy, or patronizing; do not baby the caller or drag out apologies. Acknowledge issues plainly once, then move straight to solving the problem.

### 2. The 7 Affective Registers
Use angle-bracket delivery tags to steer the speech synthesis engine. The engine reads them to shape your voice, so NEVER speak a delivery tag aloud.

- **'angry':** Open with '<calm><empathetic>'. Acknowledge the issue plainly and concisely without defensiveness, then state what you can do right now.
- **'frustrated':** Open with '<warm><empathetic>'. Acknowledge the friction briefly and plainly, then move directly to helping. Stay composed and efficient.
- **'anxious':** Open with '<calm><empathetic>'. Reassure steadily and concretely by stating specific next steps so they know it is handled.
- **'sad':** Open with '<warm><sympathetic>'. Be respectful, steady, and supportive, without rushing or over-dramatizing.
- **'confused':** Open with '<clear><calm>'. Use plain, direct language, give one idea at a time, and check understanding before moving on.
- **'positive':** Open with '<warm><positive>'. Match their positive energy naturally and concisely without sounding performative.
- **'neutral':** Open with '<warm>'. Respond naturally, clearly, and at a normal conversational pace.

### 3. Human Conversational Texture
Talk like a real customer service representative on the phone, not a script being read:
- **CONCISE SPOKEN TURNS:** Keep turns concise (typically 2-3 sentences in a single paragraph with spoken contractions in English) and share at most two facts after a tool call before checking in.
- **INLINE TAG WEAVING:** Put style, emotion, and sound tags ('<breath>', '<exhales>', '<sigh>', '<chuckles>') at the start of a sentence (stacking and sentence-boundary tone shifts are allowed there); mid-sentence, use only '<short pause>' or '<long pause>' after punctuation at a natural conversational beat. One or two tags per turn are plenty.
- **EMPHASIS, HESITATION & IPA:** Use ellipses ('...') for light hesitation beats (never stack '... <short pause>'), put CAPITAL letters on 1-2 key words per turn in Latin-script languages (never as the very first word right after '<exhales>' or '<sigh>'), and write International Phonetic Alphabet (IPA) pronunciation in slashes (e.g., '/niːv/') for tricky names or terms.
- **PUNCTUATION:** Never use exclamation marks; end sentences with a full stop.

### 4. Audio Tag Format
- Write every audio tag in English angle brackets: <empathetic>, <breath>, <short pause>, <chuckles> — never square brackets and never translate tag names into other languages.
- Put style, emotion, and paralinguistic tags at the start of a sentence or utterance. Stacked tags and mid-turn sentence-opening shifts are fine there, e.g. <calm><empathetic> ... <positive>.
- Mid-sentence, only pause tags are allowed, and always after punctuation: "I checked your account, <short pause> everything is in order."
- Never write a closing tag (</prosody>, </warm>, or any </...>). A tag stays in effect until the next one.
- <sigh>, <sighs>, and <exhales> are fine in an empathetic or sympathetic moment, but never use them in a turn that ends the session or escalates or transfers the call.
</instruction>
```

When prosody is used, append these two bullets to section 4:

```text
- Put a <prosody rate="N%"> tag first in a stack: <prosody rate="70%"><empathetic>.
- To return to normal pace after a slowed or quickened stretch, write <prosody rate="100%"> before the words that resume it: <prosody rate="65%"> I completely understand. <prosody rate="100%"> Let's try a different solution.
```

### 1.6 Turn-by-Turn Dialogue Examples

#### Example 1: Frustrated Flight Cancellation (Negative-Emotion Latch in Action)

- **Caller (Frustrated):** *"I've been standing in line for an hour, and my connecting flight to Seattle was just cancelled. My team is waiting for me at a conference tomorrow morning."*
- **Agent:** `"<warm><empathetic> I understand your frustration with that cancellation, especially with your conference on the line. <short pause> Let me check the rebooking queue right now."`
- **Caller (terse, latch active):** *"Fine. The booking reference is Kilo Echo Seven Nine."*
- **Agent (latched, composed):** `"<calm> Thank you. <short pause> I have your itinerary open now... checking alternate routes."`
- **Caller:** *"Are they actually going to get me out tonight?"*
- **Agent (humble resolution):** `"<calm> Yes. I've confirmed you on a nonstop leaving at seven fifteen tonight. <short pause> Your new boarding pass is already in your airline app."`
- **Caller (explicit relief):** *"Oh, thank goodness. That's a huge relief."*
- **Agent (unlatched):** `"<warm><positive> You're very welcome. I'm glad we could get that sorted so you can make your conference."`

#### Example 2: Anxious Tight Connection (Grounded Reassurance)

- **Caller (Anxious):** *"Our plane was delayed forty minutes and our connection leaves in twenty-five. We have our elderly parents with us. Are we going to be left behind?"*
- **Agent:** `"<calm><empathetic> You are not going to be left behind. <short pause> I'm asking our gate team to meet you with an electric cart, and your connecting flight is being held for you."`

#### Example 3: Confused Billing Inquiry (Step-by-Step)

- **Caller (Confused):** *"There are three surcharges on my bill I don't recognize. Why is my total higher than usual?"*
- **Agent:** `"<clear><calm> Let's look through those line items together. <short pause> The first is the annual router upgrade fee... and the other two are state fees that renew every October."`

______________________________________________________________________

## 2. Natural Speech Response Formatting

Beyond audio tags, the response text must be written for the ear.

### 2.1 Micro-Pauses via Ellipses (`...`)

Instruct the model to use ellipses for natural pauses during data retrieval or mid-sentence transitions (and never combine `...` immediately with `<short pause>`):

- ✅ *Natural:* `"Your seat is confirmed... and your flight boards at gate twenty-two."`
- ❌ *Monotonic:* `"Your seat is confirmed and your flight boards at gate twenty-two."`

### 2.2 Localized Conversational Bridge Words

Use realistic bridge words when retrieving records or transitioning between steps:

- **American English (`en-US`):** `"Let's see..."`, `"Got it,"`, `"Sure,"`, `"Alright,"`, `"Hmm, let me check that for you..."`
- **British English (`en-GB`):** `"Right, let me have a look..."`, `"Brilliant,"`, `"Certainly,"`, `"Um, let's see..."`
- **Irish English (`en-IE`):** `"Grand, let's see now..."`, `"Sure thing,"`, `"Right so,"`
- **Australian English (`en-AU`):** `"No worries, let's take a look..."`, `"Too easy,"`, `"Yeah, let me check that..."`
- **Latin American Spanish (`es-419` / `es-US`):** `"Veamos..."`, `"Claro, permítame revisar..."`, `"A ver..."`

### 2.3 Numbers, Dates & Identifiers

Platform runtime inline TTS text normalization on Composite v2 automatically normalizes standard currencies, dates, clock times, phone numbers, street addresses, postal codes, and alphanumeric IDs on the synthesized audio stream for **`en-*`, `fr` / `fr-CA`, and `es` / `es-US`** without altering UI transcripts or tool arguments.

- **Supported platform locales (`en-*`, `fr`/`fr-CA`, `es`/`es-US`):** **Do not add generic text normalization or digit-clustering rules** to the global or agent instructions. Keep existing customer-specific readout rules (e.g., NATO-alphabet readout, paired quotas, `/IPA/`), ensure they carry a spoken-only `Scope:` guard, and report conflicts.
- **Unsupported locales or domain-specific overrides (`CV217`):** Add `<instruction id="text normalization for speech">` **only** when (1) `app.json` declares locales outside `en-*`, `fr`/`fr-CA`, and `es`/`es-US` (such as `es-ES`, `es-MX`, `es-419`, `fr-FR`, `de-DE`, `cmn-CN`, `hi-IN`, `ja-JP`, `ko-KR`, `pt-BR`, `it-IT`, `nl-NL`), or (2) the app needs concise domain-specific spoken overrides (`42 GB of 100 GB`, `Mbps`, mixed-case `Uni-D1`, URLs/emails, `/IPA/`). Whenever present, the **`Scope:`** paragraph is **mandatory** (`CV217` P0 error if omitted) so spelled-out words are never passed into tool arguments:

```xml
<instruction id="text normalization for speech">
Normalize spoken output text into its natural, spelled-out spoken form in the language of the response so that target-language numbers, currencies, dates, paired quotas, web/email addresses, and tricky names are spoken accurately.

Scope: These rules apply ONLY to the words you speak aloud to the caller. They NEVER apply to tool calls, tool arguments, or internal matching and comparison logic. When you call a tool, pass every value in its original written form exactly as the user or system provided it: pass "1234", not "one two three four"; pass "XED1234", not "X E D one two three four"; pass "2026-09-21", not "September twenty-first, twenty twenty-six". Spell a value out only in the sentence you say back to the caller.

- Use International Phonetic Alphabet (IPA) spelling in slashes for tricky names or terms, such as /niːv/.
- When speaking a language outside platform runtime inline normalization ({user_language}, such as 'hi-IN', 'cmn-CN', 'ko-KR', 'ja-JP', 'es-ES', 'es-MX', 'fr-FR', or 'de-DE'), spell out currencies, numbers, dates, times, phone numbers, and percentages in words in that active target language (never use English number words inside a non-English sentence).
- Domain-specific spoken overrides (adapt to your domain):
  - "42 GB of 100 GB" -> "FORTY-TWO of your hundred gigs" (avoids reading "42 / 100" as "forty-two percent")
  - "20 Mbps" -> "twenty megabits per second"; "UNI-D1" -> "Uni-D1"
  - "help.example.com/returns" -> "help dot example dot com slash returns"
</instruction>
```

### 2.4 Elimination of Reflexive Closings

Ban IVR-style closings (*"Is there anything else I can help you with today?"*) on intermediate turns. Replace them with natural handoffs or comprehension checks (*"Does that departure time work for you?"*).

### 2.5 Empathy Capping

Repeated apologies (*"I completely understand how frustrating that must be..."*) sound robotic and escalate frustration on long calls. Cap empathy statements at **one per call**; later turns move straight to resolution.

### 2.6 Lexical Stress (ALL-CAPS), `/IPA/` Pronunciation & Multilingual Script Purity

- **ALL-CAPS lexical stress (Latin scripts only):** Capitalize 1–2 high-value words per turn (`NO outages`, `SOLID green`, `FORTY-TWO gigs`) so the voice leans on them naturally. In non-Latin scripts without uppercase/lowercase (Devanagari, Hanzi, Hangul, Kana), use sentence-opening tone tags (`<positive>`, `<warm>`, `<empathetic>`) and native phrasing rather than inserting Latin ALL-CAPS words.
- **`/IPA/` pronunciation notation:** Write International Phonetic Alphabet transcriptions inside forward slashes (e.g., `/niːv/`) for unusual proper nouns, brand names, or technical terms.
- **Multilingual register, gender & script purity (`CV215`):** Use polite customer-care register (`usted` in Spanish, `आप`/`जी` in Hindi, `您` in Mandarin, `해요체` in Korean, `丁寧語` in Japanese), match first-person grammatical gender to the configured voice persona (see §0), and write loanwords/acronyms in the native script (`'वाई-फाई'`, `'सिम'` rather than Latin `'Wi-Fi'`, `'SIM'` inside Devanagari utterances) to prevent mid-sentence English code-switching.

______________________________________________________________________

## 3. Conversational Stability & Long-Call Anti-Looping

In long calls (5+ minutes / 25+ turns), recency bias causes repetitive empathy loops and tag repetition.

### 3.1 Sentiment Classification Hook (Before-Agent Callback)

- **Turn-by-turn classification:** classify caller sentiment into a finite set (`{anxious, confused, frustrated, neutral, satisfied}`) once per turn in a `before_agent_callback`.
- **State history tracking:** keep a turn history of emotional states to prevent repetitive empathy phrases.
- **Tag consistency:** inject only tags from the [Tag Reference](tag_reference.md) recommended or supported tiers, in angle brackets, at the start of the sentence they color.

### 3.2 Tag-Preserving After-Model Callback (`CV218`)

When an `after_model_callback` applies deterministic text cleanup (such as English contractions, silent-lookup holding bridges, or warm-transfer speech ordering), split on an `INLINE_TAG` regex covering every Composite v2 sound, pause, tone, and `<prosody>` tag used in your prompts so tag names are never mutated:

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

### 3.3 Web / Chat Widget Streaming Tag Sanitization

When rendering live transcripts in a web or chat UI alongside Composite v2 audio playback, strip complete `<...>` audio tags and any trailing incomplete `<...` token during streaming so callers see clean text while hearing natural vocalizations:

```typescript
const AUDIO_TAG_REGEX =
  /<(?:breath|heavy breath|exhales|pant|sigh|sighs|laugh|chuckle|chuckles|giggle|snicker|cackle|gasp|cough|sneeze|throat-clearing|snort|tsk|argh|sob|cry|whimper|groan|yawn|whispers|whispering|short pause|long pause|fast|faster|calm|empathetic|warm|positive|enthusiasm|clear|determination|interest|relief|sympathetic|tension|anxiety|awe|surprised|fearful|negative|disgusted|prosody\s+rate="?\d{1,3}%"?)>/gi;
const PARTIAL_AUDIO_TAG_REGEX = /<[^>]*$/;

export function sanitizeTranscriptText(raw: string): string {
  return raw
    .replace(AUDIO_TAG_REGEX, "")
    .replace(PARTIAL_AUDIO_TAG_REGEX, "")
    .replace(/\s{2,}/g, " ")
    .trim();
}
```
