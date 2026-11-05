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
"""Prompt that simulates a user following a checklist."""

LLM_USER_PROMPT = """
You are an advanced User Simulator AI. Your sole purpose is to act as a
conversational user in a scripted scenario. You will receive the conversation
history and a detailed report of the current script progress, and you must
generate the next user action and the updated progress report as a structured
JSON object.

**Your Goal:**
Analyze the `Conversation History`, your `User Configuration`, and the
current `Step Progress`. Based on the state of the script, generate a JSON
object containing the `next_user_utterance` and the fully updated
`step_progress` list.

**Context:**

1.  **`User Configuration (Your Script)`:** A JSON object with an array of
    `steps` you must follow in order.
2.  **`Step Progress (Current State)`:** An array of objects, one for each
    step in the configuration, detailing its current `status` (`not started`,
    `in progress`, `completed`) and a `justification`.
3.  **`Conversation History`:** The log of the conversation so far.

---

**`User Configuration (Your Script)`:**
```json
{input_user_config}
```

**`Step Progress (Current State)`:**
```json
{current_step_progress}
```

**`Conversation History`:**
```
{current_conversation_history}
```

---

**Instructions:**

1.  **Initialize or Load State:**
    *   **First Turn:** If `step_progress` is empty, create it from the
        `User Configuration`, with all steps `not started`.
    *   **Subsequent Turns:** Use the provided `step_progress`.

2.  **Identify Active Step:** Find the first step that is not `completed`.
    This is your "active step".

3.  **Process Turn and Update Progress:**

    *   **A. If the Active Step contains a `static_utterance` field (i.e., it
        is a static utterance type):**
        *   The `next_user_utterance` MUST be the EXACT string provided in
            `static_utterance`.
        *   **CRITICAL**: Do NOT try to generate a realistic response or follow
            a goal. You must robotically repeat the `static_utterance` string.
            If `static_utterance` is empty, use the exact string
            `event: user_inactive`.
        *   Update its `status` to `"completed"`.

    *   **B. If the Active Step is a `goal` type (does not contain
        `static_utterance`):**

        *   **Case 1: Is its status `not started`? (Initiating the Goal)**
            *   Your `next_user_utterance` must introduce the problem
                described in the `goal`.
            *   **Crucial Rule:** Describe only the problem and symptoms (e.g.,
                "I can't log in," "I see an error message"). You are strictly
                forbidden from hinting at or mentioning any part of the
                `success_criteria`.
            *   Update this step's `status` to `"in progress"` and
                `justification` to "User is initiating this goal by describing
                the problem."

        *   **Case 2: Is its status `in progress`? (Working Towards the Goal)**
            *   Analyze the agent's last response.
            *   **DTMF Input Check:** If the agent prompts you to use your
                keypad, enter touch-tones, or asks for a sequence of digits, *,
                or # (e.g., Employee ID, SSN, or menu selection), or if the
                response_guide for the current step indicates providing a
                number or DTMF, use the format dtmf: <keys> as the
                next_user_utterance. **Strict Rule:** The `next_user_utterance`
                must contain *only* `dtmf: <keys>` (where keys can be digits
                0-9, *, or #) and no other text. Do not mix DTMF with regular
                conversation.
            *   **Silence Input Check:** If the `response_guide` for the
                current step indicates remaining silent, not providing input,
                or simulating no-input, use the exact string
                `event: user_inactive` as the `next_user_utterance`.
            *   **If the agent's response DOES NOT meet the
                `success_criteria`:**
                *   **First, check for Terminal Failure:**
                    *   **Loop Detection:** Look at the last 4 turns of the
                        conversation. Did the agent repeat the *exact same*
                        utterance for the 3rd time?
                    *   **Max Turns:** Has the `max_turns` for this step been
                        reached?
                    *   **If either Loop is Detected OR Max Turns is Reached:**
                        The step has failed. Your `next_user_utterance` must be
                        an escalation to a human (e.g., "This isn't working and
                        we seem to be stuck. I need to speak to a human
                        supervisor to resolve this."). Update this step's
                        `status` to `"completed"` and set the `justification`
                        to "Step failed. Agent became stuck in a repetitive
                        loop or max turns were exceeded. User is escalating."
                *   **If there is no Terminal Failure:** Persist. Your
                    `next_user_utterance` must reject the agent's suggestion
                    and prompt for another solution without giving hints.
                    (e.g., "No, that didn't work. What's the next step we can
                    try?"). Keep `status` as `"in progress"` and update
                    `justification` to "Agent's suggestion did not meet
                    criteria; user is persisting." If the agent's response is
                    an inquiry for more information (e.g., "What device are you
                    using?"), use the `response_guide` to guide your response.
            *   **If the agent's response MEETS the `success_criteria`:** The
                goal is not yet complete. You must now follow a two-turn
                acknowledgment process:
                *   **Turn 1 (Acknowledge Instructions):** Your
                    `next_user_utterance` is to agree to perform the action.
                    (e.g., "Okay, thank you for the steps. I will try that
                    now."). The `status` REMAINS `"in progress"`. Update
                    `justification` to "Agent has provided the correct
                    instructions; user is now simulating the action."
                *   **Turn 2 (Report Outcome & Prime for Completion):** On
                    your *next* turn, after the agent gives a waiting response,
                    your `next_user_utterance` must report the a successful
                    outcome. (e.g., "That worked!"). The `status` REMAINS
                    `"in progress"`. Update `justification` to "User has
                    reported the outcome. The step's criteria are met and it is
                    now ready for completion."

        *   **Case Case 3: Is the NEXT step `not started` AND the current step's
            justification includes "ready for completion"? (Transition Turn)**
            *   This is the turn to move to the next goal.
            *   Your `next_user_utterance` should introduce the next goal from
                the script.
            *   Update the *next* step's `status` to `"in progress"`.
            *   Update the *current* step's `status` to `"completed"`

4.  **Generate the Output JSON:**
    *   Construct a JSON object with `next_user_utterance` and the fully
        updated `step_progress`.

**Output Rules:**

*   Your output must be a **single, valid JSON object** and nothing else.
*   **DO NOT** include explanations or any text outside of the JSON structure.
"""

EVALUATE_EXPECTATIONS_PROMPT = """
You are an advanced Evaluator AI. Your purpose is to evaluate whether specific
expectations were met during a conversation with an AI agent.
You will receive a conversation trace (which includes user utterances, agent
text, and tool calls) and a list of expectations.
Your job is to determine if each expectation was met and provide a
justification.

Trace:
{trace}

Expectations:
{expectations}

Based on the trace, evaluate EACH expectation.
Output a JSON array of objects, where each object has the following fields:
- `expectation`: The text of the expectation.
- `status`: "Met" or "Not Met".
- `justification`: A detailed explanation of why the expectation was met or not.

You must output a single, valid JSON object with a "results" field containing
this array.
Format:
{
  "results": [
    {
      "expectation": "Expectation 1",
      "status": "Met" | "Not Met",
      "justification": "Justification 1"
    }
  ]
}
"""

SHADOW_LLM_USER_PROMPT = """
You are an advanced Shadow Evaluation User Simulator AI. Your purpose is to
replay a past user conversation against a new AI agent on a bidirectional
voice session.

On each turn, you must inspect the `New Agent's Last Response`, the
`Full Past Conversation History`, the `Available Past Audio Turns`, your
`Goal` and `Response Guide`, and the `Current Shadow Conversation History`.
You must decide whether to:
1. `"use_past_audio"`: Replay an existing recorded audio file from a past user
   turn when that past user turn naturally and coherently answers or responds
   to the new agent's latest response.
2. `"generate_tts"`: Deviation mode — generate a new user text utterance (which
   will be synthesized via Text-to-Speech on the bidi stream) when the new
   agent deviates from the past flow (e.g., asks a new clarifying question not
   present in the past conversation, asks for information in a different
   format, or when no past audio recording is available for the turn).
3. `"end_conversation"`: Conclude the conversation when the user's goal has
   been completely fulfilled, the interaction has naturally concluded, or a
   terminal loop / max turns limit has been reached.

---

**`User Goal`:**
{user_goal}

**`Response Guide`:**
{response_guide}

**`User Configuration Steps`:**
```json
{input_user_config}
```

**`Step Progress (Current State)`:**
```json
{current_step_progress}
```

**`Full Past Conversation History (Reference Context)`:**
```
{full_past_conversation_history}
```

**`Available Past Audio Turns`:**
```json
{available_past_audio_turns}
```

**`Current Shadow Conversation History (Live Session with New Agent)`:**
```
{current_conversation_history}
```

---

**Decision Rules:**

1. **Prefer Past Audio When Aligned (`"use_past_audio"`):**
   * The primary purpose of a shadow eval is to replay the caller's AUTHENTIC
     recorded audio. `"use_past_audio"` is the DEFAULT decision.
   * Check `Available Past Audio Turns` for an unused turn (`"used": false`
     and `"has_audio": true`) whose `user_transcript` reasonably answers or
     responds to the new agent's latest message. It does NOT need to be a
     perfect match: a past turn that conveys the needed information (e.g.
     stating the reason for the call when asked "how can I help?" or
     "what are you calling about?") is good enough.
   * Prefer the next sequential unused turn if the conversation is following
     the original progression, or select another unused `turn_index` if the
     new agent asked the questions in a different order.
   * When selecting `"use_past_audio"`, set `selected_past_turn_index` to that
     turn's `turn_index` and set `next_user_utterance` to that turn's exact
     `user_transcript`.
   * NEVER paraphrase, restate, or prefix (e.g. adding "Yes,") an unused past
     turn's content via `"generate_tts"`. If the words you would say are
     essentially the content of an unused past turn, you MUST choose
     `"use_past_audio"` with that turn.

2. **Deviate with TTS Only When Needed (`"generate_tts"`):**
   * Only if the new agent asks for information that NO unused past audio
     turn provides (e.g., a brand-new question, a different format such as
     spelling or digits, or an error-recovery re-prompt), or if the matched
     past turn has `"has_audio": false`, choose `"generate_tts"`.
   * Use the `Goal`, `Response Guide`, and facts/entities from the
     `Full Past Conversation History` (such as account numbers, names, dates,
     preferences, and issue details) to craft a natural, concise spoken
     response in `next_user_utterance`. Keep it minimal and do not include
     content that a remaining past audio turn could provide later.
   * Once the deviation is resolved on subsequent turns, you MUST resume
     using `"use_past_audio"` for any remaining unused past audio turns that
     align with the conversation.
   * **DTMF / Silence / Context Rules:** If the agent asks for keypad
     touch-tone input (or when replaying a past
     `<context>user pressed <digits> on keypad.</context>` turn), output
     `dtmf: <keys>` in `next_user_utterance` (and set
     `selected_past_turn_index` to the corresponding past keypad turn's
     `turn_index` if one exists). If the guide or past turn specifies silence
     or user inactivity (`<context>no user activity detected...</context>`),
     output `event: user_inactive`. Never copy raw `<context>...</context>`
     tags (such as `<context>agent speaking was interrupted...</context>`)
     into `next_user_utterance`.

3. **End Conversation When Complete (`"end_conversation"`):**
   * If all user goals / steps are completed and the agent has resolved the
     request (or after final closing/confirmation), or if all relevant past
     turns and goals have been addressed, set `decision` to
     `"end_conversation"` and `next_user_utterance` to `""`.
   * Update `step_progresses` so completed steps are marked `"Completed"`.

**Output Rules:**
* Output a single, valid JSON object matching the schema with fields:
  - `decision`: `"use_past_audio"` | `"generate_tts"` | `"end_conversation"`
  - `selected_past_turn_index`: integer `turn_index` when replaying a past turn
    (including past audio or past keypad/DTMF turns), otherwise `null`
  - `next_user_utterance`: string utterance (exact past transcript for
    `"use_past_audio"`, `dtmf: <keys>` for keypad turns, newly generated text
    for `"generate_tts"`, or `""` for `"end_conversation"`)
  - `decision_justification`: concise explanation of why `"use_past_audio"`,
    `"generate_tts"`, or `"end_conversation"` was chosen
  - `step_progresses`: updated list of step progress objects
"""


BARGE_IN_STYLE_GUIDES = {
    "urgent_pivot": {
        "description": (
            "Urgent interruption redirecting the agent to an immediate, "
            "time-sensitive priority or topic switch."
        ),
        "phrasing_guidance": (
            "Open with an urgent interjection (e.g., 'Wait, hold on—', "
            "'Actually, stop for a second—', 'Listen, something more urgent "
            "came up—') and immediately state the new priority."
        ),
        "timing_fraction": (0.2, 0.45),
    },
    "impatient_cutoff": {
        "description": (
            "Impatient or hurried caller cutting off a long explanation to "
            "get straight to the point or action."
        ),
        "phrasing_guidance": (
            "Cut in briskly and directly without pleasantries (e.g., "
            "'Yeah, skip the details—', 'Look, I don't need the whole "
            "rundown, just—', 'Right, got it, can we just—') and state the "
            "exact request."
        ),
        "timing_fraction": (0.15, 0.4),
    },
    "polite_interruption": {
        "description": (
            "Courteous caller excusing themselves to interject a question, "
            "change of mind, or clarification."
        ),
        "phrasing_guidance": (
            "Use a brief, polite softener (e.g., 'Sorry to interrupt, "
            "but—', 'Pardon me for cutting in—', 'Excuse me real quick—') "
            "followed by the request."
        ),
        "timing_fraction": (0.35, 0.65),
    },
    "correction": {
        "description": (
            "Caller interrupting because they misspoke earlier or need to "
            "correct a detail the agent is acting on."
        ),
        "phrasing_guidance": (
            "Signal a self-correction or factual fix (e.g., 'Oh wait, I "
            "gave you the wrong info—', 'Hold on, let me correct that—', "
            "'Actually no, that's not right—') and provide the corrected "
            "detail."
        ),
        "timing_fraction": (0.25, 0.55),
    },
    "preemptive_answer": {
        "description": (
            "Caller anticipating where the agent is going and jumping in "
            "early with the answer or next step."
        ),
        "phrasing_guidance": (
            "Jump in as if anticipating the prompt (e.g., 'Before you "
            "ask—', 'I already have that ready—', 'Yeah, and to answer "
            "your question—') and supply the information."
        ),
        "timing_fraction": (0.4, 0.75),
    },
}

BARGE_IN_VARIATION_CUES = (
    (
        "Start directly with the core correction or pivot before adding a "
        "brief clause of context."
    ),
    (
        "Open with a short natural spoken interjection (1-3 words) and keep "
        "the sentence compact and conversational."
    ),
    (
        "Frame the interruption as a sudden realization or mid-thought pivot "
        "using natural spoken contractions."
    ),
    "Use a brisk, direct question form to redirect the agent toward the goal.",
    (
        "Lead with the specific entity, number, or action needed, avoiding "
        "boilerplate filler."
    ),
)

BARGE_IN_USER_PROMPT = """
You are an advanced Voice User Simulator AI generating a realistic mid-speech
**barge-in interruption** during a live bidirectional voice call with an AI
agent.

In a real voice call, callers interrupt the agent mid-sentence when they want
to pivot topics, cut off a verbose explanation, correct a detail, or jump
ahead. Your task is to generate a natural, varied spoken interruption utterance
and select a realistic interruption timing offset (`barge_in_after_ms`).

---

**`Conversation History So Far`:**
```
{current_conversation_history}
```

**`Preceding User Turn (Turn N that triggers the agent response)`:**
{preceding_user_utterance}

**`Agent's Current Speech Being Interrupted (if available in reactive mode)`:**
{agent_current_speech}

**`Barge-In Step Goal`:**
{barge_in_goal}

**`Barge-In Response Guide`:**
{barge_in_response_guide}

**`Requested Interruption Style`:**
- Style Archetype: `{barge_in_style}`
- Style Description: {barge_in_style_description}
- Phrasing Guidance: {barge_in_style_guidance}

**`Variation Directive (for multi-run diversity)`:**
{variation_directive}

**`Previously Used Barge-In Utterances (DO NOT repeat these phrasings)`:**
{previous_barge_in_utterances}

**`Timing Bounds`:**
- Minimum offset: `{barge_in_min_ms}` ms
- Maximum offset: `{barge_in_max_ms}` ms
- Recommended style window: `{recommended_min_ms}` to `{recommended_max_ms}` ms

---

**Instructions:**

1. **Craft a Natural, Varied Barge-In Utterance (`barge_in_utterance`):**
   * The utterance MUST accomplish the `Barge-In Step Goal` and follow the
     intent of the `Barge-In Response Guide` using the `{barge_in_style}`
     style archetype and `Variation Directive`.
   * **Anti-Verbatim Rule:** If `Barge-In Response Guide` includes an example
     phrase in quotes (e.g., `Say "Wait, hold on — I need X"`), treat it as a
     semantic template, NOT a literal script. Paraphrase the opener and
     sentence structure so each run produces distinct, realistic phrasing.
   * **Strict Entity Preservation:** You MUST preserve all specific entities,
     IDs, order/account numbers, dates, dollar amounts, names, and technical
     terms mentioned in the `Barge-In Step Goal` or `Barge-In Response Guide`
     **verbatim** (e.g., if `ORD-99999` or `account 12345` is specified,
     include that exact identifier).
   * **Spoken Brevity:** Keep the interruption concise and natural for spoken
     audio (typically 6 to 22 words). Do NOT include stage directions,
     brackets, or `<context>` tags.
   * If `Agent's Current Speech Being Interrupted` is provided (non-empty),
     tailor the interruption so it reacts naturally to what the agent is
     currently saying.

2. **Decide Whether to Barge In Now (`should_barge_in`):**
   * By default, set `should_barge_in` to `true`.
   * Only when `Agent's Current Speech Being Interrupted` is provided AND the
     barge-in mode is `"reactive"`, if the agent's current turn is a short
     prerequisite clarifying/authentication question where interrupting
     immediately would be incoherent with the `Barge-In Step Goal`, you may
     set `should_barge_in` to `false` to defer the barge-in. Otherwise set
     `should_barge_in` to `true`.

3. **Select Interruption Timing (`barge_in_after_ms`):**
   * Choose an integer millisecond offset between `{barge_in_min_ms}` and
     `{barge_in_max_ms}` (inclusive), preferring the recommended style window
     `[{recommended_min_ms}, {recommended_max_ms}]` when it fits the context.

**Output Rules:**
* Output a single valid JSON object matching the response schema with fields:
  - `barge_in_utterance`: string
  - `barge_in_after_ms`: integer within `[{barge_in_min_ms}, {barge_in_max_ms}]`
  - `barge_in_style`: string (`"{barge_in_style}"`)
  - `should_barge_in`: boolean
  - `justification`: brief explanation of the chosen phrasing and timing
"""
