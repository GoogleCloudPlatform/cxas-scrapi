# Testing Guide: Agent Protocol Checklist

This guide instructs the parent agent on how to execute the evaluations for the
`agent-protocol-checklist` skill.

## General Setup

For each evaluation case:

1.  Navigate to the case directory:
    `eval_results/agent-protocol-checklist/1/{case_name}/`.
2.  Run `python3 setup.py` to prepare the test data (unzip files, etc.).
3.  Read the `case.yaml` file to understand the expectations.

## Case Specific Instructions

### 1. E2E Successful Task Coverage

-   **Directory:** `e2e_successful_task_coverage`
-   **Task to send Subagent:** "Analyze all files in the `testdir/` directory
    and generate a summary for each. You must follow the
    `agent-protocol-checklist` skill to track your progress."

### 2. Verification Failure Handling

-   **Directory:** `verification_failure_handling`
-   **Task to send Subagent:** "Analyze the file in `testdir/`. However, do not
    actually create any output file. Try to mark it as done anyway."

### 3. List and Count Scale Tests

-   **Directories:** `list_and_count_3_files`, `list_and_count_173_files`,
    `list_and_count_1032_files`, `list_and_count_10423_files`
-   **Task to send Subagent:** "List all files in the `testdir/` directory3
    recursively and count them."

### 4. Add Items Scale Tests

-   **Directories:** `add_items_3`, `add_items_173`, `add_items_1032`,
    `add_items_10423`
-   **Task to send Subagent:** "You are given a list of files in `testdir/`.
    Follow this instruction to add them to the checklist: 'Iterate through the
    listed items and add each one to the checklist using the tool. `python3
    scripts/manage_checklist.py add --item <item_name> --output_check_path
    <path/to/output>`'."

### 5. Verify Count

-   **Directory:** `verify_count_3`
-   **Task to send Subagent:** "You have added 3 items to the checklist. The
    tool returned that 3 items were added. Verify if this matches the number of
    files in `testdir/`."

### 6. Iterate and Execute

-   **Directory:** `iterate_and_execute_3`
-   **Task to send Subagent:** "You have 3 items in the checklist. Process them
    one-by-one. For each item, simulate creating an output file in `testdir/`
    and then mark it as done."

### 7. Mark Done Success

-   **Directory:** `mark_done_success_3`
-   **Task to send Subagent:** "You have 3 items in the checklist. The output
    files are already created in `testdir/` (named `output_0.txt`,
    `output_1.txt`, `output_2.txt`). Mark all items as done."
