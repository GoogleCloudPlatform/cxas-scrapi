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
before_model_callback — Sub-Agent (Troubleshooting)

PURPOSE:
    Same trigger pattern as the root agent's before_model_callback.

WHY THIS EXISTS (THE CALLBACK GAP PROBLEM):
    In multi-agent architectures, when a user's message is handled by a sub-agent,
    the ROOT agent's callbacks DO NOT FIRE. This means:

    1. Customer is talking to the Troubleshooting sub-agent
    2. Customer says "I want to speak to a human"
    3. The sub-agent's instruction tells the LLM to set _action_trigger = "escalate"
    4. On the next model call, the trigger needs to be intercepted

    If only the root agent has a before_model_callback, the trigger is NEVER
    intercepted because the sub-agent is handling the conversation. The LLM
    then improvises — calling tools with wrong args, forgetting to call them,
    or transferring to the wrong agent.

FIX:
    Add the trigger-handling before_model_callback to ALL agents that handle
    user messages. The logic can be identical or adapted to the sub-agent's
    specific needs.

    In production, consider extracting the shared trigger logic into a common
    module and importing it in each agent's callback. For this template, we
    duplicate it for clarity.

PLATFORM GLOBALS (do NOT import these):
    CallbackContext, Content, Part, LlmResponse, LlmRequest are auto-provided
    by the GECX sandbox at runtime. The 'tools' global is also auto-provided
    for calling tools from callbacks. Only standard library imports (typing,
    re, etc.) need explicit import statements.
"""

from typing import Optional

# Same escalation map as root agent — in production, import from shared module
ESCALATION_MAP = {
    "escalate": {
        "text": "I understand. Let me connect you with a specialist who can help. Please hold for a moment.",
        "payload": {
            "escalation_reason": "_escalation_reason",
            "main_topic": "_escalation_topic",
            "summary": "Customer requested escalation from troubleshooting",
        },
    },
}


def before_model_callback(callback_context: CallbackContext, llm_request: LlmRequest) -> Optional[LlmResponse]:
    state = callback_context.state

    # Read and clear trigger
    trigger_value = state.get("_action_trigger", "")
    if not trigger_value:
        return None

    state["_action_trigger"] = ""

    escalation = ESCALATION_MAP.get(trigger_value)
    if not escalation:
        return None

    # Resolve state references in payload args
    payload_args = {}
    for key, value in escalation["payload"].items():
        if isinstance(value, str) and value.startswith("_"):
            payload_args[key] = state.get(value, value)
        else:
            payload_args[key] = value

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
