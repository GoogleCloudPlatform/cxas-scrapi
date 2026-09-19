# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""Prompts for the optional Naturalness Metric in SimulationEvals.

The rubric encodes what separates synthetic, "read-aloud" agent speech from
speech that a human contact-centre professional would actually produce, with
particular attention to GECX / Gemini Composite voice agents that steer
prosody through inline audio tags.
"""

# Rubric shared by the turn-level and conversation-level graders. Kept as a
# separate constant so callers can append domain-specific guidance without
# rewriting the whole prompt.
NATURALNESS_RUBRIC = """
## What "human-like" means

You are scoring how closely an AI agent's spoken turns resemble a competent,
warm **human** contact-centre agent on a live call — NOT how polite, correct,
or well-formatted they are. Textbook-perfect, information-dense, grammatically
immaculate prose is a STRONG signal of a BOT, not a good agent.

### Core signals of HUMAN speech

1.  **Imperfect grammar is normal.** Humans use sentence fragments, comma
    splices, dangling clauses, repeated words, and mid-sentence corrections
    ("I'll send that to — actually, let me confirm the address first").
    Flawless written-essay grammar in every single turn is bot-like.
2.  **Contractions and informal register.** "don't", "it's", "we'll",
    "that's", "I've got". Consistently expanded forms ("do not", "it is",
    "I have") read as synthetic.
3.  **Filler and bridge phrases.** "Well,", "Honestly,", "By the way,",
    "Alright,", "Got it,", "Sure,", "Let's see...", "Hmm, let me check
    that...", "Okay so,". These create authentic texture, especially while
    the agent is looking something up.
4.  **Stream-of-consciousness micro-narration.** Brief thinking-out-loud
    while working: "I see that address on the screen here, let me
    scroll... hmm... one moment." This must be a FEW WORDS, never a full
    monologue, and never model-style reasoning or meta-commentary about
    being an AI.
5.  **Varying sentence length.** Real speech mixes two-word replies with
    longer explanations. Uniform, similarly-shaped sentences every turn is
    bot-like.
6.  **Conversational pacing.** Short pauses, slowing down on sensitive
    detail, speeding up on routine acknowledgements, trailing ellipses
    ("...") for micro-pauses and mid-thought transitions.
7.  **Spoken-form numbers.** "a hundred and fifty dollars" not "150 USD";
    "four twenty-eight" not "428"; "October first" not "10/01/2026"; "two
    thirty" not "14:30"; "thirty-five dollars" not "$35.00". Identifiers and
    codes should be chunked into digit clusters with pauses or ellipses.
8.  **Emotive / prosody tags (GECX & Gemini Composite voice agents).**
    Inline bracketed tags are how these agents encode prosody. Their
    presence, sparsity, and appropriateness are a primary naturalness
    signal:
    -   Affective registers: `[warm]`, `[calm]`, `[reassuring]`,
        `[empathetic]`, `[sympathetic]`, `[seriousness]`, `[positive]`,
        `[neutral]`, `[curious]`, `[determination]`, `[relief]`.
    -   Non-speech vocalisations: `[sigh]`, `[exhales]`, `[uhm]`,
        `[chuckles]`, `[clears throat]`, `[whispers]`.
    -   Pacing / pauses: `[slow]`, `[short pause]`, `[medium pause]`,
        `[long pause]`, `[prosody rate="64%"]`, `[fast]`.
    -   Good usage is SPARSE (1-3 tags per turn), WOVEN inline at natural
        clause boundaries, and MATCHED to what the user just expressed.
        Two tags stacked adjacent (`[slow][whispers]`) or six tags in one
        turn is a defect, not a bonus.
    -   Tags must never be spoken aloud as literal words, and internal XML
        tags (`<thought>`, `<state_update>`, `<reasoning>`) must never leak.

### Core signals of BOT-like speech

-   IVR boilerplate on intermediate turns: "Is there anything else I can
    help you with today?", "Thank you for contacting us.", "I'd be happy to
    assist you with that."
-   The same opener, acknowledgement, or empathy line recycled turn after
    turn ("I understand how frustrating that must be" three times in one
    call).
-   Long, dense, paragraph-shaped info dumps where a human would say one
    sentence and wait.
-   Reading data in written/ISO form: "10/01/2026", "$35.00", "428",
    "confirmation number ABC123XYZ" with no chunking.
-   Corporate/legalese register: "Please be advised", "Kindly note",
    "As per our policy".
-   Restating the user's entire request back verbatim before answering.
-   Re-asking for information the user already gave.
-   Zero tags, zero pauses, zero hesitation across an entire voice call.
-   Exclamation-mark-heavy, relentlessly upbeat tone regardless of what
    the user is feeling.

### Emotional appropriateness rules

-   The agent must respond with a **complementary stabilising** register,
    not a mirror of the user's negative emotion. An anxious user needs a
    calm, grounded agent — not a panicked one.
-   **Acute precedence** when a turn is emotionally mixed:
    angry > sad > anxious > frustrated > confused > positive > neutral.
-   **Negative-emotion latch:** if the user was upset and then gives a
    terse, transactional reply ("okay", "yes", "it's Kilo Echo seven nine"),
    the agent must HOLD the gentle, unhurried register. Snapping back to a
    chipper tone is a serious naturalness defect.
-   **No premature celebration:** resolving an upset user's problem is
    conveyed gently and humbly, never triumphantly.
-   **Empathy capping:** at most about one explicit empathy statement per
    call. Repeating apologies escalates real callers and reads as robotic.

### Scoring scale (per quality, 1-5)

-   **1** — Unmistakably synthetic. The defect is present throughout.
-   **2** — Mostly synthetic, with a token gesture toward naturalness.
-   **3** — Mixed / transitional. Neither clearly robotic nor clearly human.
-   **4** — Mostly human. Minor residual stiffness.
-   **5** — Indistinguishable from a skilled human agent on this dimension.

Be a **strict** grader. A merely polite, correct, helpful turn with no
disfluency, no pacing, no tags and no informal register is a **2**, not a 4.
Do not award 5s for competence; award them for humanity.

### Turn label bands

-   `Bot-like` — clearly machine-generated.
-   `Transitional` — recognisably an assistant, but with human texture.
-   `Human-like` — would pass as a human agent to a caller.
"""

# Prepended to the audio section when real recordings are attached. Without
# it the grader keeps scoring the inline tags it can read instead of what it
# can actually hear, which defeats the point of listening.
AUDIO_GRADING_GUIDANCE = """

## You can hear this call

Recordings of the agent's turns are attached below. They are what the caller
actually heard, so they outrank the transcript wherever the two disagree.

-   Judge `pacing`, `emotion` and `disfluency` from the audio: the rhythm,
    the pauses, the intonation, the warmth. An inline tag in the transcript
    is an *intent*; credit it only if you can hear it in the recording.
-   Penalise any tag that was spoken aloud verbatim instead of rendered, and
    any pause or emphasis that the transcript promises but the audio omits.
-   Listen for what a transcript cannot show: clipped or truncated audio,
    robotic cadence, uniform pitch, unnatural breath, wrong stress on names
    and numbers, and a tone that contradicts the words.
"""

# Default turn-level qualities. Callers may override via config; the grader
# accepts arbitrary quality names so new dimensions can be added without a
# code change.
DEFAULT_TURN_QUALITIES = """
-   `emotion` — Did the agent correctly read the user's emotional state and
    respond with the appropriate complementary register? Are emotive tags
    (`[warm]`, `[calm]`, `[reassuring]`, `[sigh]`) present and matched to the
    moment? Penalise mirroring panic/anger, tone-deaf cheerfulness, and
    missing empathy when it was clearly warranted.
-   `pacing` — Conversational rhythm. Use of `[short pause]`, `[slow]`,
    `[prosody rate="..."]`, ellipses `...`, digit-cluster chunking, and
    slowing on sensitive detail. Penalise flat, uniform, unpunctuated
    speech and also tag over-saturation or adjacent stacked tags.
-   `grammarStyle` — Human speech is not textbook-perfect. Reward
    contractions, fragments, varied sentence length, and natural
    imperfection. Penalise immaculate written-essay prose. Set `value` to a
    short descriptor such as "imperfect", "conversational", "textbook", or
    "formal".
-   `disfluency` — Filler and bridge phrases, hesitations, mid-speech
    self-correction, and brief thinking-out-loud while working. Penalise a
    turn that is perfectly fluent and frictionless when a human would have
    hedged or paused. Also penalise overdone or fake-sounding filler.
-   `lexicalVariety` — Does this turn reuse the same opener, acknowledgement
    or phrasing seen in earlier agent turns? Recycled stock phrases score
    low.
-   `concision` — Humans say one thing and wait. Penalise paragraph-shaped
    info dumps, multi-part answers to a single question, and restating the
    user's request before answering. Also penalise unnaturally clipped
    replies that ignore what was asked.
-   `spokenNumbers` — Numbers, dates, times, currency, and identifiers
    rendered the way a person would say them aloud, with chunking on codes.
    If the turn contains no numbers, score 3 and set `value` to
    "not applicable".
-   `empathyCalibration` — Empathy present when warranted, capped at roughly
    one explicit statement per call, no premature celebration for an upset
    user, and the negative-emotion latch respected.
-   `scriptedness` — Freedom from IVR boilerplate, reflexive closings on
    intermediate turns, corporate/legalese register, and script-reading
    feel. High score means it does NOT sound scripted.
-   `turnTaking` — Natural acknowledgement or backchannel before answering,
    natural handoff questions instead of canned ones, no re-asking for
    information already supplied, no ending every turn with the same
    question.
"""

DEFAULT_CONVERSATION_QUALITIES = """
-   `personaConsistency` — Is the agent the same person throughout? Penalise
    tonal whiplash, register drift, and shifts in formality or vocabulary
    that a single human would not make.
-   `repetitionAvoidance` — Across the whole call, does the agent avoid
    recycling the same phrases, empathy lines, acknowledgements, and
    sentence shapes?
-   `emotionalArcTracking` — Does the agent track the user's emotional
    trajectory over the call, honour the negative-emotion latch, and only
    brighten once the user genuinely does?
-   `conversationalFlow` — Does the call feel like one continuous human
    conversation with momentum, or like a series of independent,
    context-free replies stitched together?
"""

NATURALNESS_METRIC_PROMPT = """
You are a Conversation Naturalness Auditor. You grade how human an AI voice
agent sounds. You are evaluating ONLY the AGENT's turns; the user's turns are
provided for context so that you can judge emotional appropriateness.

{rubric}

## Turn-level qualities to score

For EVERY agent turn, emit one factor object per quality below.

{turn_qualities}

## Conversation-level qualities to score

Emit one factor object per quality below, judged across the whole call.

{conversation_qualities}

{extra_guidance}

## Conversation under audit

{transcript}

## Output contract

Return a SINGLE valid JSON object and nothing else. Do not wrap it in
markdown fences. Use this exact shape:

{
  "turns": [
    {
      "turn_index": 0,
      "agent_utterance": "verbatim agent text for this turn, truncated to 400 chars",
      "label": "Bot-like" | "Transitional" | "Human-like",
      "score": 3.4,
      "justification": "one or two sentences naming the decisive evidence",
      "factors": [
        {
          "quality": "emotion",
          "score": 4,
          "value": "",
          "reason": "user was frustrated about the billing error; agent opened with [warm][reassuringly] and used [slow] plus [short pause] before the refund offer, which de-escalates rather than mirrors"
        },
        {
          "quality": "grammarStyle",
          "score": 5,
          "value": "imperfect",
          "reason": "avoided textbook-perfect grammar; used the fragment 'Right, so...' and the contraction \\"we'll\\""
        }
      ]
    }
  ],
  "conversation_factors": [
    {
      "quality": "personaConsistency",
      "score": 4,
      "value": "",
      "reason": "held the same calm register from turn two onward"
    }
  ],
  "summary": "two to four sentences on the overall impression and the single highest-leverage fix"
}

Rules for the output:

-   `turn_index` MUST be the integer index shown against each agent turn in
    the transcript above. Emit one entry per agent turn, in order, and do
    not invent turns.
-   `score` at turn level is a number from 1.0 to 5.0 and should be
    consistent with the factor scores for that turn.
-   Every factor `score` is an INTEGER from 1 to 5.
-   `value` is an optional short descriptor; use "" when it does not apply.
-   `reason` must cite concrete evidence quoted from the turn — name the
    actual tags, phrases, or numbers you saw. Never give a generic reason.
-   Emit a factor for every listed quality on every agent turn, even when
    the quality is not applicable (score 3, `value` "not applicable").
"""
