# Benchmark Scenarios Context

This document defines how benchmark scenario files in this directory are structured and validated.

## 1. Principles & Flow

*   **End-to-End Isolation**: Every scenario must be an isolated user journey. It must not depend on pre-existing resources in the GCP project (unless prepared via `setup.commands`).
*   **Single-Turn Tasking**: The Subject Under Test (SUT / Antigravity) receives the entire scenario `prompt` in a single turn, executing it synchronously.
*   **Trace-based Auditing**: The automated `Scorer` grades the run trace (conversation history, tool outputs, and GCP API payloads) against the scenario's `rubric` criteria.

## 2. Anatomy of a Scenario File (`.yaml`)

Each benchmark scenario YAML file defines the following fields:

*   `name`: The descriptive name of the scenario.
*   `prompt`: The complete task instructions passed directly to the SUT.
*   `rubric`: A list of criteria objects used for scoring. Each item defines:
    *   `criteria`: What capability is being evaluated.
    *   `perfect`: Guidelines for a perfect score.
    *   `good` (optional): Guidelines for partial success.
    *   `failed` (optional): Guidelines for a failure.
*   `assets` (optional): List of files or directories pre-populated in the SUT's workspace.
*   `setup` (optional): environmental setup config:
    *   `commands`: List of shell commands to run prior to the scenario.

## 3. Scenario Validation

To ensure scenario configurations are formatted correctly and all referenced assets exist, run the validator script from the `skill_eval/` directory:

```bash
python3 scenarios/validate_scenarios.py
```
