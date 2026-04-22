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

"""
before_model_callback — Root Agent

PURPOSE:
    Implements the TRIGGER PATTERN for deterministic tool calls.
    The LLM decides WHAT to do (detection) by setting _action_trigger via a
    state-setting tool. This callback decides HOW (execution) by intercepting
    the next model call and returning the tool calls with correct args.

WHY THIS PATTERN EXISTS:
    Without it, the LLM sometimes:
    - Forgets to call payload_update_tool before end_session
    - Calls payload_update_tool with empty {} args
    - Calls end_session without saying a farewell message
    - Transfers to the wrong agent

    By intercepting in the callback, we guarantee the correct tools are called
    with the correct arguments, every time.

KEY PATTERNS DEMONSTRATED:
    1. Deterministic greeting: First model call returns a fixed greeting,
       ensuring the customer always hears the same opening — no LLM variance.
    2. Silence handling: Detects "no user activity" signals and responds
       deterministically — repeat last message, then escalate after 3 strikes.
    3. Trigger read + clear: Read the trigger, clear it immediately to prevent
       re-firing on the next model call.
    4. Escalation map: Structured mapping of trigger values to tool call args.
    5. Always include text: The response always includes a text part so the
       customer hears something before the session ends.
    6. LlmResponse.from_parts: Combines text + tool calls in a single response.
"""

import re
from typing import Iterator, Optional


# -------------------------------------------------------------------------
# SILENCE HANDLING HELPERS
#
# WHY THIS IS IN THE CALLBACK:
#   When the user is silent on a voice call, the platform sends a special
#   "<context>no user activity detected for X seconds.</context>" message.
#   The LLM doesn't reliably handle this — it may hallucinate a response,
#   ask a random question, or ignore it entirely. The callback ensures a
#   consistent pattern: repeat the last message twice, then end the session.
#
# HOW IT WORKS:
#   1. is_user_inactive() checks if the latest user message is a silence signal
#   2. get_reversed_agent_messages() finds previous agent messages to repeat
#   3. no_input_counter tracks how many consecutive silences (1st, 2nd, 3rd)
#   4. After 3 silences, end the session gracefully
# -------------------------------------------------------------------------

def is_user_inactive(contents: list) -> bool:
    """Check if the latest user message is a 'no activity' silence signal."""
    silence_pattern = (
        r"<context>no user activity detected for \d+ seconds\.</context>"
    )
    return len(contents) > 1 and any(
        re.search(silence_pattern, p.text, re.IGNORECASE)
        for p in contents[-1].parts
        if p.text
    )


def get_reversed_agent_messages(contents: list) -> Iterator[str]:
    """Yield agent messages from most recent to oldest."""
    for content in reversed(contents):
        texts = []
        for part in content.parts:
            if content.role == "model" and part.text is not None:
                texts.append(part.text)
        if texts:
            yield "".join(texts)


# -------------------------------------------------------------------------
# ESCALATION MAP: Maps trigger values to payload_update_tool args and
# farewell text. Add new escalation types here — don't scatter them
# across instructions.
# -------------------------------------------------------------------------
ESCALATION_MAP = {
    "escalate": {
        "text": "I understand. Let me connect you with a specialist who can help. Please hold for a moment.",
        "payload": {
            "escalation_reason": "_escalation_reason",   # read from state
            "main_topic": "_escalation_topic",            # read from state
            "summary": "Customer requested escalation",
        },
    },
    "api_failure_escalate": {
        "text": "I'm sorry, but I'm experiencing a technical issue. Let me transfer you to a representative who can assist you directly.",
        "payload": {
            "escalation_reason": "System API failure",
            "main_topic": "technical",
            "summary": "Escalated due to API failure during authentication",
        },
    },
}


def before_model_callback(callback_context: CallbackContext, llm_request: LlmRequest) -> Optional[LlmResponse]:
    state = callback_context.state

    # -------------------------------------------------------------------------
    # DETERMINISTIC GREETING: When the session starts, return a fixed greeting.
    # This ensures the customer always hears the same opening — no LLM
    # variance, no filler phrases, no "How can I help you today?" variations.
    #
    # WHY a callback instead of instruction?
    # The instruction can say "Respond with: Hi, I am..." but the LLM
    # sometimes paraphrases, adds filler, or skips it entirely. A callback
    # guarantees the exact greeting every time.
    #
    # HOW: The platform sends "<event>session start</event>" as the first
    # user input. We detect this and return the greeting immediately.
    # -------------------------------------------------------------------------
    for part in callback_context.get_last_user_input():
        if part.text == "<event>session start</event>":
            greeting = (
                "Hi, I am your virtual assistant. "
                "How can I help you today?"
            )
            return LlmResponse.from_parts(parts=[
                Part.from_text(text=greeting),
            ])

    # -------------------------------------------------------------------------
    # SILENCE HANDLING: Detect "no user activity" and respond deterministically.
    #
    # Pattern: 1st silence → repeat last message, 2nd → repeat again with
    # different prefix, 3rd → end session gracefully.
    #
    # WHY deterministic? The LLM sometimes hallucinates responses to silence
    # or asks unrelated questions. The callback ensures consistent behavior.
    # -------------------------------------------------------------------------
    try:
        if is_user_inactive(llm_request.contents):
            no_input_counter = int(state.get("no_input_counter", 0)) + 1
            state["no_input_counter"] = str(no_input_counter)

            if no_input_counter < 3:
                reversed_msgs = get_reversed_agent_messages(llm_request.contents)
                if no_input_counter == 1:
                    last_msg = next(reversed_msgs, "How can I help you?")
                    return LlmResponse.from_parts(parts=[
                        Part.from_text(text=f"Sorry, I didn't hear anything. {last_msg}")
                    ])
                else:
                    next(reversed_msgs, None)  # skip the "Sorry, I didn't hear" repeat
                    original_msg = next(reversed_msgs, "How can I help you?")
                    return LlmResponse.from_parts(parts=[
                        Part.from_text(text=f"I still can't hear you. {original_msg}")
                    ])
            else:
                return LlmResponse.from_parts(parts=[
                    Part.from_text(
                        text="I'm sorry, but I'm unable to hear you. "
                             "Please try calling again later. Have a great day."
                    ),
                    Part.from_function_call(
                        name="end_session",
                        args={"session_escalated": False, "reason": "no_input_limit"},
                    ),
                ])
        else:
            # User spoke — reset the silence counter
            state["no_input_counter"] = "0"
    except Exception as e:
        print(f"Error in silence handling: {e}")

    # -------------------------------------------------------------------------
    # CHECK API FAILURE FLAG: If the before_agent_callback flagged an API
    # failure, escalate deterministically on the next model call after greeting.
    # -------------------------------------------------------------------------
    if str(state.get("api_failed", "false")).lower() == "true":
        state["api_failed"] = "false"  # clear to prevent re-firing
        trigger_value = "api_failure_escalate"
    else:
        # -------------------------------------------------------------------------
        # READ TRIGGER: The instruction told the LLM to call set_session_state
        # with _action_trigger = "escalate". We read and CLEAR it immediately.
        # -------------------------------------------------------------------------
        trigger_value = state.get("_action_trigger", "")
        if trigger_value:
            state["_action_trigger"] = ""  # clear to prevent re-firing

    if not trigger_value:
        # No trigger set — let the LLM handle this model call normally
        return None

    # -------------------------------------------------------------------------
    # RESOLVE ESCALATION: Look up the escalation config and build the response.
    # -------------------------------------------------------------------------
    escalation = ESCALATION_MAP.get(trigger_value)
    if not escalation:
        # Unknown trigger value — log it and let the LLM continue
        return None

    # Build payload args, resolving state references
    payload_args = {}
    for key, value in escalation["payload"].items():
        if isinstance(value, str) and value.startswith("_"):
            # Value is a state variable reference — read from state
            payload_args[key] = state.get(value, value)
        else:
            payload_args[key] = value

    # -------------------------------------------------------------------------
    # RETURN DETERMINISTIC RESPONSE: Text + payload_update + end_session.
    #
    # WHY always include text?
    # The LLM sometimes calls end_session without saying anything. By always
    # including a text part, the customer always hears a farewell message.
    # No need for a has_text check — we always provide text here.
    # -------------------------------------------------------------------------
    return LlmResponse.from_parts(parts=[
        Part.from_text(text=escalation["text"]),
        Part.from_function_call(
            name="payload_update_tool",
            args=payload_args,
        ),
        Part.from_function_call(
            name="end_session",
            args={"session_escalated": True},
        ),
    ])
