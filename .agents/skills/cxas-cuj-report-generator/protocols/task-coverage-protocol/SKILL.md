---
name: task-coverage-protocol
description: "Enforces task coverage and prevents drift via a deterministic checklist tool with output verification."
---

# PROTOCOL: Task Coverage Checklist

This protocol governs how you must handle large, repetitive, or multi-step tasks
to prevent context drift and ensure 100% task coverage. You must use a
deterministic checklist tool to track progress and verify completion.

## Workflow

When given a task that involves processing multiple items (e.g., "analyze all
files in this directory"), follow these steps:

1.  **List and Count**: Use a tool to list the items recursively and print the
    total count.
2.  **Initialize Checklist**: Call the checklist tool to initialize a new
    checklist:
    ```bash
    python3 scripts/manage_checklist.py init --title "My Task Title"
    ```
3.  **Add Items**: Iterate through the listed items and add each one to the
    checklist using the tool. This ensures the LLM does not skip or summarize
    items:
    ```bash
    python3 scripts/manage_checklist.py add --item "item_name" --output_check_path "path/to/output"
    ```
    *   **Rule**: You MUST pass `--output_check_path` pointing to the file or
        directory where output for this item will be saved.
4.  **Verify Count**: The tool will return the number of items added. Verify
    that this matches your initial count.
5.  **Iterate and Execute**: Process the items one-by-one (or in batches if
    delegating to subagents).
6.  **Mark Done**: When an item is processed and output produced, call the tool
    to mark it as done:
    ```bash
    python3 scripts/manage_checklist.py done --item "item_name"
    ```
    *   **Verification**: The tool will automatically verify that output exists
        at the specified `output_check_path` and is not empty. If verification
        fails, the tool will error and you must redo the work for that item.

7.  **Conclusion**: When all items are marked done, report completion to the
    user.

## Handling Parallel Processing

If you batch items and assign them to subagents:

1.  You are still responsible for the primary checklist.
2.  Instruct subagents to produce output in the designated paths.
3.  Call the `done` command on the primary checklist only after the subagent
    reports success and you verify their output.
