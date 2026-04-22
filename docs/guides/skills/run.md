---
title: Run Skill
description: The cxas-agent-run skill — running all four eval types and generating combined reports.
---

# Run Skill

The Run skill executes all four evaluation types against your agent and produces a combined report showing the overall health of your agent. It's the "are we good?" step in the development loop.

---

## Invoking the Run skill

The foundry routes you to Run when you express an intent like:

- "Run the evals"
- "What's the pass rate?"
- "Test the agent"
- "How is the agent performing?"

The Run skill is a sub-skill of the [Agent Foundry](agent-foundry.md) — it is automatically routed to when the foundry detects a run/test intent.

---

## What the Run skill does

The Run skill orchestrates all four eval types in the optimal order:

### 1. Callback tests (fastest)

```bash
cxas test-callbacks --app-dir cxas_app/<AppName>
```

Runs all pytest-based callback tests. These are the fastest tests — pure Python, no API calls. The skill reports how many tests passed and flags any failures before proceeding.

### 2. Tool tests (fast, isolated)

```bash
cxas test-tools \
  --app-name "projects/.../apps/<app>" \
  --test-file evals/tool_tests/order_tests.yaml
```

Runs tool tests against the live platform. These are fast but require network access. The skill includes latency statistics in the report.

### 3. Platform goldens (push + run)

```bash
cxas push-eval \
  --app-name "projects/.../apps/<app>" \
  --file evals/goldens/order_lookup.yaml

cxas run --app-name "projects/.../apps/<app>" --wait --filter-auto-metrics
```

Pushes golden files and waits for results. This is the most comprehensive test but also the slowest.

### 4. Local simulations (AI-driven)

```python
from cxas_scrapi.evals.simulation_evals import SimulationEvals
# ... runs all simulation YAML files in parallel ...
```

Runs simulations using the deployment configured in `gecx-config.json`. The skill uses parallel execution (up to 5 concurrent simulations) to keep runtime reasonable.

---

## Combined report

After running all four types, the skill generates a combined report:

```
===========================
CXAS Agent Evaluation Report
===========================

App: My Support Agent
Run at: 2026-04-14 14:32:07

Callback Tests
--------------
  12 tests | 12 passed | 0 failed
  Status: PASS

Tool Tests
----------
  8 tests | 7 passed | 1 failed
  Status: FAIL

  Failed:
    - lookup_order/handles_api_timeout: agent_action field missing
      Expected: is_not_null | Actual: null

Platform Goldens
----------------
  8 conversations | 7 passed | 1 failed
  Status: FAIL

  Failed:
    - order_management/bad_order_id_handling: turn 2
      Expected: "couldn't find" | Actual: "I'll look that up for you"

Local Simulations
-----------------
  3 evals | 8 steps | 6 completed | 2 not completed
  Status: FAIL

  Not completed:
    - billing_inquiry/get_account_number: goal not achieved in 3 turns

===========================
Overall Pass Rate: 28/31 (90%)
===========================
```

The report is written to `test-results/latest-report.md` and printed to the terminal.

---

## Report artifacts

The Run skill writes results to `test-results/`:

```
test-results/
├── latest-report.md              # Human-readable combined report
├── latest-report.json            # Machine-readable combined report
├── callback-tests.log            # Raw pytest output
├── tool-tests.csv                # Tool test results (one row per test)
├── goldens-results.json          # Platform golden results
└── simulations-results.csv       # Simulation results (one row per step)
```

These files are useful for tracking trends over time. If you commit `test-results/` to Git, you can see how the pass rate evolves with each push.

---

## Target pass rate

The Debug skill uses the pass rate from the Run skill to determine when to stop iterating. By default, it targets 100% for tool tests and callback tests, and 80% for goldens and simulations (to account for natural language variability).

You can adjust the target by telling the foundry what you want:

```
Run the evals and then debug until we're at 90% or better
```

---

## Running specific eval types

If you only want to run one type:

```
Run just the tool tests
```

```
Run the goldens only — skip simulations for now
```

The Run skill will run only the specified types and report accordingly.

---

## Handling slow simulations

Local simulations are the slowest part of the Run skill. If you're in a tight iteration loop and don't need simulation results, tell the skill:

```
Run evals but skip the simulations for now
```

The skill will skip the simulation phase and note that the report is incomplete.
