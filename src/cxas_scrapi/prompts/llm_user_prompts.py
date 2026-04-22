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

    *   **A. If the Active Step is a `goal` type:**

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
            *   Update the *current* step's `status` to `"completed"`.

    *   **B. If the Active Step is a `static_utterance` type:**
        *   The `next_user_utterance` is the exact string from
            `static_utterance`. If `static_utterance` is empty, use the exact
            string `event: user_inactive`. Update its `status` to
            `"completed"`.

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
