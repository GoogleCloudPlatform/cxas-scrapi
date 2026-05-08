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
before_agent_callback — Root Agent

PURPOSE:
    Derives auth/profile variables from identifiers (account_id, customer_id)
    provided as session parameters.

IMPORTANT: before_agent_callback fires on EVERY agent turn, not just once
    at conversation start. The early-return guard below is critical — without
    it, this callback would reset state variables on every turn, wiping auth
    status and other derived data.

KEY PATTERNS DEMONSTRATED:
    1. Early-return: If variables are already set, skip the lookup. This is
       REQUIRED because the callback fires every turn, not just on first entry.
    2. Never override auth_status/user_role in evals: These are DERIVED here from
       real (or mock) API calls. Overriding them in session_parameters bypasses
       the authentication logic and gives false eval results.
    3. Error handling: API failures set a flag that the before_model_callback can
       use for deterministic escalation, rather than letting the LLM improvise.
    4. Tool calls from callbacks: Use tools.ToolName(args) to call tools directly.
       Python function tools use the function name; API connector tools use
       DisplayName_OperationId format.

PLATFORM GLOBALS (do NOT import these):
    CallbackContext, Content, Part, LlmResponse, LlmRequest, LlmRequest
    are auto-provided by the GECX sandbox at runtime. Importing them will
    cause errors. The 'tools' global is also auto-provided for calling
    tools from callbacks. Only standard library imports (typing, re, etc.)
    need explicit import statements.
"""

from typing import Optional


def before_agent_callback(callback_context: CallbackContext) -> Optional[Content]:
    state = callback_context.state

    # -------------------------------------------------------------------------
    # EARLY RETURN: If auth_status is already set, we've already done the
    # lookup. This happens when the agent is re-invoked (e.g., after a
    # sub-agent transfers back to root).
    # -------------------------------------------------------------------------
    if state.get("auth_status"):
        return None

    # -------------------------------------------------------------------------
    # READ IDENTIFIERS: These come from session parameters set by the
    # telephony platform (IVR, SIP headers, etc.). They are the RAW
    # identifiers — not yet validated.
    # -------------------------------------------------------------------------
    account_id = state.get("account_id", "")
    customer_id = state.get("customer_id", "")

    if not account_id or not customer_id:
        # No identifiers provided — mark as unauthenticated
        state["auth_status"] = "unauthenticated"
        state["user_role"] = "unknown"
        state["customer_name"] = ""
        return None

    # -------------------------------------------------------------------------
    # LOOKUP: Call the customer datastore to validate the identifiers and
    # retrieve the customer profile.
    #
    # WHY tools.ToolName() instead of a direct API call?
    # Callbacks run in the GECX sandbox. External HTTP calls may be blocked.
    # Using tools.ToolName() routes through the platform's tool infrastructure,
    # which handles auth, retries, and logging.
    #
    # Tool naming:
    #   - Python function tools: tools.function_name(args)
    #   - API connector tools:   tools.DisplayName_OperationId(args)
    # -------------------------------------------------------------------------
    try:
        response = tools.Read_Customer_Datastore_readDatastore(
            account_id=account_id,
            customer_id=customer_id,
        )

        # Parse the response — adapt this to your actual API schema
        customer_data = response.get("customer", {})
        state["auth_status"] = "authenticated"
        state["user_role"] = customer_data.get("role", "standard")
        state["customer_name"] = customer_data.get("name", "")
        state["service_status"] = customer_data.get("service_status", "unknown")

    except Exception as e:
        # -------------------------------------------------------------------------
        # ERROR HANDLING: Don't let the LLM improvise when the API fails.
        # Set a flag so the before_model_callback can escalate deterministically.
        #
        # WHY a flag instead of returning escalation text here?
        # before_agent_callback runs before ANY model call. If we return Content
        # here, it replaces the agent's greeting — the customer hears an
        # escalation message with no context. Instead, we let the greeting
        # happen normally, then the before_model_callback checks the flag on
        # the NEXT model call and escalates with proper context.
        # -------------------------------------------------------------------------
        state["auth_status"] = "unauthenticated"
        state["user_role"] = "unknown"
        state["customer_name"] = ""
        state["api_failed"] = "true"
        state["api_error_detail"] = str(e)

    return None
