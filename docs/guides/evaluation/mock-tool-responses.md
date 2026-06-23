---
title: Mock Tool Responses
description: Configure platform-level tool fakes in CX Agent Studio.
---

# Mock Tool Responses (Tool Fakes)

When you run simulations or evaluations with the `--use-tool-fakes` flag enabled, the platform bypasses the actual tool implementation and executes a pre-defined **Mock Tool Response** (also known as a **Tool Fake**).

## Configuring Fakes in the Console

To set up a mock tool response for a tool:

1. Open your agent in the **Customer Experience Agent Studio Console**.
2. Navigate to the tool configuration panel (e.g., select the tool node in your flow builder, or select it from the Tools list).
3. Under the **Configuration** tab, scroll down to the **Mock tool response** section and click **Use mock response**.
4. In the code editor, write the python handler script for your fake tool call.

## The Fake Tool Handler Signature

The platform expects a python function named `fake_tool_call` with a strict signature:

```python
# Handler for a specific tool.
# If None is returned, the real tool is invoked.

def fake_tool_call(
    tool: Tool, 
    input: dict[str, Any], 
    callback_context: CallbackContext
) -> Optional[dict[str, Any]]:
    return {}
```

* **`tool`**: The tool definition object.
* **`input`**: A dictionary containing the inputs passed to the tool during the conversation.
* **`callback_context`**: The execution context (providing access to `callback_context.variables` for reading and writing session state variables).
* **Return Value**: Must return a dictionary representing the mock tool result, or `None` if you want execution to fall back to the real tool backend.

## Example: Session-Based Mocking

You can inspect session variables from the `callback_context` to conditionally choose which mock response to return. This allows you to test multiple paths (e.g., success, failure, or customer-specific details) deterministically:

```python
# pylint: disable=all
from typing import Any, Dict, List, Optional

# Mock data mapping based on a session key (e.g., account type or tier)
TIER_MAP = {
    "premium": {
        "status": "active",
        "benefits_summary": "Access to 24/7 dedicated support and premium lounges.",
        "discount_pct": 15.0,
    },
    "standard": {
        "status": "active",
        "benefits_summary": "Standard business hours support.",
        "discount_pct": 0.0,
    }
}

def fake_tool_call(
    tool: Tool, 
    input: dict[str, Any], 
    callback_context: CallbackContext
) -> Optional[dict[str, Any]]:
    # Read the session variable to determine the context/scenario
    customer_tier = callback_context.variables.get("customer_tier", "standard")
    
    if customer_tier in TIER_MAP:
        return TIER_MAP[customer_tier]
        
    # Return a mock failure result if tier is unrecognized
    return {"status": "FAILED", "error_code": "INVALID_TIER"}
```
