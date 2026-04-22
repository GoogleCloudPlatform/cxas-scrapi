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
after_model_callback — Root Agent

PURPOSE:
    Injects farewell text before end_session when the LLM calls end_session
    without saying anything first.

WHY THIS EXISTS:
    The LLM frequently calls end_session without producing any text, causing
    the customer to hear silence before the call disconnects. This callback
    ensures the customer always hears a goodbye message.

THE MULTI-MODEL-CALL PROBLEM:
    A single conversational "turn" can span MULTIPLE model calls. For example:
      - Model call 1: LLM produces text ("Thank you for calling!")
      - Model call 2: LLM calls payload_update_tool
      - Model call 3: LLM calls end_session (no text)

    The after_model_callback fires on EACH model call separately. A naive check
    for "no text in this response" would inject text on call 3 even though the
    agent already said something in call 1 — causing DOUBLE text.

FIX:
    Use callback_context.events to check if the agent already produced text in
    a prior model call within the same turn. Only inject if no text was produced
    anywhere in the current turn.

KEY PATTERNS DEMONSTRATED:
    1. text_or_transcript(): Use instead of part.text for audio-safe detection.
       In audio mode, the LLM produces transcripts, not text. text_or_transcript()
       handles both.
    2. callback_context.events: Full session event history. Walk backwards from
       the most recent event to find the last user message; if any agent event
       between now and then has text, the agent already spoke.
    3. Prepend text before end_session: Put the farewell Part BEFORE the
       end_session Part so the customer hears it first.

PLATFORM GLOBALS (do NOT import these):
    CallbackContext, Content, Part, LlmResponse, LlmRequest are auto-provided
    by the GECX sandbox at runtime. Only standard library imports need explicit
    import statements.
"""

from typing import Optional

# The farewell message to inject when the LLM ends the session silently.
# Keep this generic — the LLM should have already said something contextual.
FAREWELL_TEXT = "Thank you for calling Acme Corp support. Have a great day!"


def after_model_callback(callback_context: CallbackContext, llm_response: LlmResponse) -> Optional[LlmResponse]:

    # -------------------------------------------------------------------------
    # STEP 1: Check if THIS model call contains end_session AND text.
    # -------------------------------------------------------------------------
    has_end_session = False
    has_text_this_call = False

    for part in llm_response.content.parts:
        if part.has_function_call("end_session"):
            has_end_session = True
        else:
            # text_or_transcript() handles both text and audio transcripts.
            # WHY not part.text? In audio mode, the LLM produces transcripts
            # (part.inline_data with transcript metadata), not part.text.
            # text_or_transcript() returns whichever is available.
            content = part.text_or_transcript()
            if content and len(content.strip()) > 0:
                has_text_this_call = True

    # If there's no end_session, or the LLM already said something, no-op.
    if not has_end_session or has_text_this_call:
        return None

    # -------------------------------------------------------------------------
    # STEP 2: Check if the agent produced text in an EARLIER model call
    # within this same turn.
    #
    # WHY: The multi-model-call problem. Walk backwards through events until
    # we hit the last user message. If any agent event in between has text,
    # the agent already spoke — don't double-text.
    # -------------------------------------------------------------------------
    for event in reversed(callback_context.events):
        if event.is_user():
            # Reached the last user message — no prior agent text found
            break
        if event.is_agent():
            for p in event.parts():
                content = p.text_or_transcript()
                if content and len(content.strip()) > 0:
                    # Agent already said something in an earlier model call
                    return None

    # -------------------------------------------------------------------------
    # STEP 3: No text anywhere in this turn — inject farewell BEFORE end_session.
    #
    # WHY prepend? The customer needs to hear the farewell before the session
    # terminates. Parts are processed in order.
    # -------------------------------------------------------------------------
    new_parts = [Part.from_text(text=FAREWELL_TEXT)]
    new_parts.extend(llm_response.content.parts)
    return LlmResponse.from_parts(parts=new_parts)
