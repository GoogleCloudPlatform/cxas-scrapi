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
set_session_state — State-Setting Tool

PURPOSE:
    Writes key-value pairs to session state. This is the "detection" half of the
    trigger pattern: the LLM calls this tool to signal WHAT should happen, then
    the before_model_callback reads the state on the next model call and
    executes HOW.

WHY A DEDICATED TOOL?
    The LLM cannot write to session state directly. It can only communicate
    through tool calls. This tool provides a clean, typed interface for setting
    state variables that callbacks will read.

STATE ACCESS:
    Tools access session state via the `context` global provided by the platform
    at runtime (context.state). Do NOT add context as a function parameter —
    it is a global, not an argument.

USAGE IN INSTRUCTION:
    Call {@TOOL: set_session_state} with:
      - _action_trigger = "escalate"
      - _escalation_reason = "Customer requested human agent"
      - _escalation_topic = "billing"
"""


def set_session_state(_action_trigger: str = "",
                        _escalation_reason: str = "",
                        _escalation_topic: str = "") -> dict:
    """Write trigger and escalation variables to session state.

    Args:
        _action_trigger: Action to trigger (e.g., 'escalate'). Read by before_model_callback.
        _escalation_reason: Brief reason for escalation. Read by before_model_callback.
        _escalation_topic: Main topic (e.g., 'billing', 'technical'). Read by before_model_callback.

    Returns:
        dict: Confirmation of which variables were set.
    """
    updated = {}
    if _action_trigger:
        context.state["_action_trigger"] = _action_trigger
        updated["_action_trigger"] = _action_trigger
    if _escalation_reason:
        context.state["_escalation_reason"] = _escalation_reason
        updated["_escalation_reason"] = _escalation_reason
    if _escalation_topic:
        context.state["_escalation_topic"] = _escalation_topic
        updated["_escalation_topic"] = _escalation_topic

    if not updated:
        return {
            "agent_action": "At least one parameter must be provided to set_session_state."
        }

    return {
        "status": "success",
        "updated_variables": updated,
    }
