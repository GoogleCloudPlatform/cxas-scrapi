# Ingestor Evaluations Execution Guide

This guide explains how to execute the automated requirements ingestion
evaluation and naturalness grading suite in this workspace. Follow these
step-by-step instructions to run batches of tests and compile performance
reports autonomously.

--------------------------------------------------------------------------------

## 1. Compact Test Suite Architecture

The evaluation suite consists of **14 unique, high-fidelity test cases**
organized modularly by ingestor types, completely eliminating redundant folder
duplication on the filesystem:

*   **Cyara Tabular Ingestion Unique Cases (`0001_framework_verification` to
    `0007_support_wrong_item`)**:
    *   *Location*:
        [ingestors/frameworks/cyara/evals/cases/](ingestors/frameworks/cyara/evals/cases/)
    *   *Scope*: Standard state transition matrix validations (Happy paths,
        catering, pickups, delayed drivers, loyalty checks).
*   **Draw.io Flowchart Ingestion Unique Cases (`0008_flowchart_verification` to
    `0012_full_journey_flowchart`)**:
    *   *Location*:
        [ingestors/files/drawio/evals/cases/](ingestors/files/drawio/evals/cases/)
    *   *Scope*: Complex visual flowchart traversals (isolated booking, loyalty,
        delivery, and complete combined full journey flowcharts).
*   **General CUJ Checks (`0013_cuj_json_hierarchy`,
    `0014_cuj_voice_realism`)**:
    *   *Location*: [evals/cases/](evals/cases/)
    *   *Scope*: Core CUJ outputs (hierarchical JSON outputs and agent
        voice-realism).

Each case directory holds a standardized `test.yaml` defining expectations, and
a `testdir/` folder containing raw self-describing requirement files (e.g.,
`booking_flowchart.drawio`, `make_reservation.xml`).

--------------------------------------------------------------------------------

## 2. Step-by-Step Execution Protocol

To run a batch of evaluations, navigate to the project root and execute these
three steps:

### Step 1: Prepare the Batch Spec

Run the batch compiler python script. Slices are compiled by setting
`--batch-start` (0-indexed alphabetical sequence of all merged directories) and
`--batch-size`:

```bash
python3 evals/scripts/prepare_eval_batch.py --batch-start=0 --batch-size=14
```

This dynamically collects the cases, resolves their modular paths, and compiles
the specifications JSON under `/tmp/eval_subagents_spec.json` along with a
matching active batch mapping at `/tmp/active_eval_batch.json`.

### Step 2: Spawn Parallel Ingestion Subagents

Read `/tmp/eval_subagents_spec.json` and invoke the special subagents natively
via your `default_api:invoke_subagent` tool.

*   Pass the exact `Prompt`, `Role`, `TypeName`, and `Workspace` blocks compiled
    inside the JSON spec array for all cases concurrently.
*   Wait about 10-15 seconds for the parallel subagents to complete their runs
    and deliver the YAML scenario transcripts into your context inbox
    automatically.

### Step 3: Run the Grading Scorer

Once all subagent transcripts are delivered and loaded into your context, run
the batch grading script:

```bash
python3 evals/scripts/grade_eval_batch.py
```

The grader crawls the untruncated subagent logs, runs the deterministic rule
assertions, scores the conversational naturalness of each scenario path on a
`0-3` rubric, and prints the final passes count and batch averages instantly!

--------------------------------------------------------------------------------

## 3. Replications Multiplier Protocol (In-Memory Replications)

To run a large-scale trial sequence (e.g., executing 100 concurrent runs to
measure parser reliability) **without duplicating physical folders on disk**,
pass the `--replications` multiplier flag to the compiler:

```bash
python3 evals/scripts/prepare_eval_batch.py --batch-start=0 --batch-size=14 --replications=10
```

This command duplicates the subagent launch specs **in-memory** inside
`/tmp/eval_subagents_spec.json`, appending specific run sequences (e.g. `General
Evaluator-0002_make_reservation-run_1` to `run_10`), spawning 140 concurrent
evaluators dynamically, and grading each of them independently under
`.eval_results/`!

--------------------------------------------------------------------------------

## 4. Accessing Results & History

*   All results are safely isolated inside a dot-prefixed hidden directory
    [evals/.eval_results/](evals/.eval_results/) to prevent VCS changelist
    pollution.
*   **`evals/.eval_results/iteration_XXX/manual_evals_progress.yaml`**: Holds
    the active batch pass/fail progress snapshots for that iteration.
*   **`evals/.eval_results/hill_climb_progress.json`**: Holds the permanent
    chronological iterations history and average naturalness stats.
*   Individual folders under `.eval_results/iteration_XXX/` store the
    `raw_output.txt` and `grade.json` (verdict details and list of naturalness
    ratings per traversed scenario path) for each case.
