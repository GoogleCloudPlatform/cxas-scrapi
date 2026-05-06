---
name: slot-filling
description: >-
  Comprehensive guide for implementing slot filling in CXAS using the Slot Filling pattern.
---

# Slot Filling in CXAS (The Slot Filling Pattern)

This skill guides you on how to implement slot-filling conversational agents in CXAS using the **Slot Filling Pattern**. CXAS has no native slot-filling primitive, and relying on LLM memory for complex state tracking can be fragile. This pattern uses Python tools to manage slot state programmatically.

## When to Use

Use the Slot Filling pattern when:
-   You need to collect a specific set of data points from the user.
-   The order of collection depends on a complex graph of dependencies (DAG).
-   You want to avoid relying on the LLM to remember collected values.
-   You want to ensure tasks fire automatically and immediately as soon as all required inputs are available.

## Architecture

The pattern uses Python tools to manage slot state programmatically, while the LLM handles natural conversation.

```
User ──► LLM ──► Setter Tool ──► DAG Engine ──► Action Function
                      │                              │
                      ▼                              ▼
              context.state['sm']          Auto-fired task result
```

### Components

-   **`sm` variable**: Runtime state: `{filled, task_results, status}` stored in App `variableDeclarations`.
-   **Setter tools**: One per user-collected slot.
-   **`_next_question()`**: Computes the frontier slot from the DAG.
-   **Action functions**: Business logic (e.g., booking, ordering).
-   **Agent instruction**: Slot Filling Protocol section enforcing tool usage and progressive disclosure.

## Implementation Steps

1.  **Define your slots**: List all data to collect (user-sourced and task-sourced).
2.  **Define your tasks**: Map input slots to tasks, and task outputs to output slots.
3.  **Create setter tools**: One CES Python tool per user-sourced slot.
4.  **Embed DAG logic**: In each setter, check if tasks can fire after storing.
5.  **Write agent instruction**: Copy the Slot Filling Protocol, customize tool selection rules.
6.  **Declare `sm` variable**: Add to App `variableDeclarations`.

## References

-   **Bella Notte Sample**: A complete working example with 5 setter tools, auto-firing tasks, and full progressive disclosure is available in `examples/bella_notte/`.
