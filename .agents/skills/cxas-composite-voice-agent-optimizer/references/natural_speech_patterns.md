# Natural Speech Patterns & Anti-Looping Architecture Guide

With Gemini Composite v2, delivery is steered by **inline audio tags** that the reasoning model writes into its response. Director's Notes are cleared during the migration, and speaking style instructions are either cleared or kept below 12 words so they do not conflict with inline tags. Natural prosody, micro-pauses, emotion attunement, and pacing must therefore be engineered into the global and agent instructions.

See the [Tag Reference](tag_reference.md) for the tag vocabulary, the tags to replace, and syntax rules.

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

Lead with validation, and deliver good news gently.

### 1.4 Inline Tag Syntax Standards

1. **Angle brackets only:** `<warm>`, `<short pause>`. Never `[warm]`.
2. **Tags at the sentence start; stacking allowed there:** `<calm><empathetic> I understand.`
3. **Mid-sentence, only pause tags, after punctuation:** `"I checked your account, <short pause> everything is in order."`
4. **No closing tags.** Reset pace with `<prosody rate="100%">` instead of `</prosody>`.
5. **Tag sparsity:** a few tags per turn; do not tag every clause.
6. **Punctuation:** never use exclamation marks; end sentences with full stops or ellipses.

Rules written for the old format must be rewritten, not kept alongside: *"wrap tags in square brackets"*, *"never stack tags"*, *"do not bunch all tags at the front of a sentence"*.

### 1.5 Production Prompt Template (Global Instruction)

#### Placement Guidelines

- **Merge, don't overwrite.** Read the existing global instruction in full first. If it already has a voice or affective delivery section, put the *Audio Tag Format* block (section 4 below) inside it and edit any rule it contradicts in place. Add the full template only when no such section exists.
- **Where it lives.** `globalInstruction` in `app.json` is a filename pointer (`"globalInstruction": "global_instruction.txt"`). If the app has none, create `global_instruction.txt` beside `app.json` and add the key at the top level of `app.json`.
- **Match the file's formatting** (heading style, numbering, bullet pattern) and adapt the registers and examples to the customer's domain, keeping the structure: recognize the emotion, pick a register, open the turn with its tags.
- **Do not add text normalization rules** (numbers, currency, dates, times, IDs, phone numbers, addresses). Composite v2 handles spoken-form normalization in the platform.
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
- **INLINE TAG WEAVING:** Put style and emotion tags at the start of a sentence or utterance (stacking is allowed there); mid-sentence, use only '<short pause>' or '<long pause>' after punctuation at a natural conversational beat.
- **HUMAN-LIKE DELIVERY:** Do NOT speak in clean, polished sentences. Include the small sounds people make when thinking while speaking (e.g. "uhmmm"), and occasionally pause and repeat a word while gathering your thoughts.
- **PUNCTUATION:** Never use exclamation marks; end sentences with a full stop.

### 4. Audio Tag Format
- Write every audio tag in angle brackets: <empathetic>, <short pause>, <chuckles>.
- Put style, emotion, and paralinguistic tags at the start of a sentence or utterance. Stacked tags are fine there, e.g. <calm><empathetic>.
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

Instruct the model to use ellipses for natural pauses during data retrieval or mid-sentence transitions:

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

Composite v2 normalizes numbers, currency, dates, times, phone numbers, and alphanumeric IDs into their spoken form in the platform. **Do not add text normalization or digit-clustering rules** to the global or agent instructions. Keep customer-specific readout rules that already exist (e.g. a NATO-alphabet readout for a code), and report them if they conflict with each other.

### 2.4 Elimination of Reflexive Closings

Ban IVR-style closings (*"Is there anything else I can help you with today?"*) on intermediate turns. Replace them with natural handoffs or comprehension checks (*"Does that departure time work for you?"*).

### 2.5 Empathy Capping

Repeated apologies (*"I completely understand how frustrating that must be..."*) sound robotic and escalate frustration on long calls. Cap empathy statements at **one per call**; later turns move straight to resolution.

______________________________________________________________________

## 3. Conversational Stability & Long-Call Anti-Looping

In long calls (5+ minutes / 25+ turns), recency bias causes repetitive empathy loops and tag repetition.

### 3.1 Sentiment Classification Hook (Before-Agent Callback)

- **Turn-by-turn classification:** classify caller sentiment into a finite set (`{anxious, confused, frustrated, neutral, satisfied}`) once per turn in a `before_agent_callback`.
- **State history tracking:** keep a turn history of emotional states to prevent repetitive empathy phrases.
- **Tag consistency:** inject only tags from the [Tag Reference](tag_reference.md) recommended or supported tiers, in angle brackets, at the start of the sentence they color.
