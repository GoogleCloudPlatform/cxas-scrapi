---
name: task-coverage-protocol
description: "Enforces 100% task and file ingestion coverage via an asynchronous, supervisor-driven state polling mechanism."
---

# PROTOCOL: Supervisor-Driven Task Coverage Checklist

This protocol governs how you must handle large, repetitive, or multi-step
requirements ingestion tasks to prevent context drift, avoid memory exhaustion,
and guarantee 100% file coverage. Workloads are partitioned into sliced batches
dynamically monitored by a decoupled supervisor.

## Architectural Overview

Instead of attempting to list and process hundreds of files in a single
conversation turn, tasks are executed asynchronously:

1.  The **Ingestor Supervisor** scans the directory, partitions the files, and
    outputs a structured batch directive.
2.  You (or parallel subagents) consume this directive, process only the
    assigned slice of files, and write the output files to the designated
    folder.
3.  The supervisor verifies and grades the outputs before queueing the next
    slice.

--------------------------------------------------------------------------------

## Execution Workflow

### 1. Scoped Scan & Scoping Setup

When instructed to ingest a requirements directory (e.g. "Ingest and parse all
files in this folder"), you MUST launch the Supervisor script to initialize the
task state:

```bash
python3 protocols/task-coverage-protocol/scripts/ingestor_supervisor.py \
  --source_dir="path/to/source" \
  --target_skill=".agents/skills/cxas-cuj-report-generator/"
```

### 2. Consume the Handoff Directive

The supervisor will scan the target folder, slice the files into batch lists of
size 10, and print a structured directive to standard output:
`SPAWN_BATCH_DIRECTIVE: {"spec_file": "/tmp/ingestor/batch_spec.json",
"output_dir": "/tmp/ingestor/outputs/"}`

Upon seeing this token, you MUST:

1.  Read the spec file `/tmp/ingestor/batch_spec.json` to retrieve the list of
    files, prompts, and expected output paths.
2.  Spawn fresh, parallel subagents (one per file or partition) using their
    respective prompt files (e.g.,
    `/tmp/ingestor/prompts/<file_name>_prompt.txt`).
3.  Instruct the subagents to write their generated YAML transcripts directly to
    `/tmp/ingestor/outputs/<file_name>_output.txt`.

### 3. Yield Terminal & Polling Gate

Once parallel subagents have been spawned in the background, you MUST
immediately stop calling tools and go idle (or report status) to release the
terminal lock. The supervisor will poll the output directory until all files are
completed.

### 4. Progress Verification & Next Slices

When all subagents have delivered their YAML transcripts, run the supervisor
script again:

```bash
python3 protocols/task-coverage-protocol/scripts/ingestor_supervisor.py \
  --source_dir="path/to/source" \
  --target_skill=".agents/skills/cxas-cuj-report-generator/"
```

*   **Verification Pass**: If the supervisor grades all outputs as passing, it
    automatically advances the file pointer and writes the next batch spec.
    Consume the new directive and repeat the parallel spawning cycle!
*   **Verification Fail**: If the supervisor detects any syntax, schema, or
    linguistic violations, it automatically triggers recovery, rebasing, or load
    bisection. Re-spawn the corrective subagents as directed.

### 5. Final Compilation & Completion

When all batch slices are fully processed, the supervisor will declare `All
files successfully ingested!` and compile the final interactive CUJ reports.
