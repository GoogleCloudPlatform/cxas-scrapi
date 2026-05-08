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
lookup_account — Account Lookup Tool

PURPOSE:
    Retrieves customer account details from the backend system.
    Demonstrates proper error handling in GECX tools.

KEY PATTERNS:
    1. Error response includes agent_action: Tells the LLM what to do when the
       tool fails (e.g., "inform the customer" vs "retry" vs "escalate"). Without
       this, the LLM guesses — sometimes silently swallowing the error.
    2. Auth check via context.state: Tools can read session state via the `context`
       global (provided by the platform at runtime) to enforce preconditions.
       Do NOT add context as a function parameter — it is a global.
    3. Structured response: Returns a consistent schema so the LLM can reliably
       extract fields. Inconsistent response shapes cause extraction errors.
"""


def lookup_account(account_id: str) -> dict:
    """Look up account details by account ID.

    Args:
        account_id: The customer's account ID.

    Returns:
        dict: Account details or error information.
    """
    # -------------------------------------------------------------------------
    # AUTH CHECK: Verify the session is authenticated before returning data.
    # This is a defense-in-depth check — the instruction also tells the LLM
    # not to call this tool for unauthenticated users, but instructions are
    # suggestions, not guarantees.
    # -------------------------------------------------------------------------
    auth_status = context.state.get("auth_status", "")
    if auth_status != "authenticated":
        return {
            "status": "error",
            "error": "Account lookup requires an authenticated session.",
            "agent_action": "Inform the customer that you cannot access account details without verification.",
        }

    if not account_id:
        return {
            "status": "error",
            "error": "account_id is required.",
            "agent_action": "Ask the customer for their account ID.",
        }

    # -------------------------------------------------------------------------
    # API CALL: In a real implementation, this would call your backend API.
    # Replace this stub with your actual API integration.
    # -------------------------------------------------------------------------
    try:
        # Stub response — replace with actual API call
        account_data = {
            "status": "success",
            "account": {
                "account_id": account_id,
                "plan": "Premium",
                "balance": "$45.00",
                "next_billing_date": "2099-01-15",  # Stub data -- replace with actual API call
                "service_status": "active",
            },
        }
        return account_data

    except Exception as e:
        # -------------------------------------------------------------------------
        # ERROR HANDLING: Return a structured error with agent_action.
        #
        # WHY agent_action? Without it, the LLM might:
        # - Silently ignore the error and make up account details
        # - Say "I encountered an error" without offering next steps
        # - Retry the call in an infinite loop
        #
        # agent_action tells the LLM exactly what to do, reducing improvisation.
        # -------------------------------------------------------------------------
        return {
            "status": "error",
            "error": f"Failed to retrieve account: {str(e)}",
            "agent_action": "Inform the customer that you are unable to access their account details at this time and offer to connect them with a specialist.",
        }
