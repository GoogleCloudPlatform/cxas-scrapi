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

"""Payload Update Tool -- records escalation metadata for downstream systems.

PURPOSE:
    Called by the before_model_callback during deterministic escalation to
    record why the session was escalated. The payload is typically consumed
    by the telephony platform or CRM after the session ends.

    The LLM does NOT call this tool directly -- the before_model_callback
    calls it via tools.payload_update_tool({...}) as part of the trigger
    pattern. See the before_model_callback for the full flow.

TOOL TYPE:
    This is a Python function tool. In callbacks, call it as:
        tools.payload_update_tool({"summary": "...", ...})

    The 'context' global is auto-provided by the platform at runtime.
    Do NOT add 'context' as a function parameter.

Args:
    summary: Brief description of the customer's issue and resolution attempt.
    escalation_reason: Why the session is being escalated.
    main_topic: Category for routing (e.g., "billing", "technical", "general").

Returns:
    dict with status and the recorded payload.
"""


def payload_update_tool(
    summary: str = "",
    escalation_reason: str = "",
    main_topic: str = ""
) -> dict:
    """Updates the session payload with escalation metadata.

    Args:
        summary: Brief description of the customer's issue (REQUIRED).
        escalation_reason: Why escalation is needed (REQUIRED).
        main_topic: Category for routing -- e.g., "billing", "technical" (REQUIRED).

    Returns:
        dict: {"status": "success", "payload": {...}} on success,
              or {"status": "error", "agent_action": "..."} on failure.
    """
    if not summary and not escalation_reason:
        return {
            "status": "error",
            "agent_action": "You must provide at least a summary or escalation_reason."
        }

    payload = {
        "summary": summary,
        "escalation_reason": escalation_reason,
        "main_topic": main_topic,
    }

    # In production, this would write to a CRM, ticketing system, or
    # telephony platform payload. For the template, we just return success.
    return {
        "status": "success",
        "payload": payload
    }
