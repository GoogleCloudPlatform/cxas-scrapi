---
title: Local Simulations
description: AI-driven open-ended conversation tests using SimulationEvals.
---

# Local Simulations

Local Simulations take a different approach to testing than Platform Goldens. Instead of scripting exact conversations and expected responses, you describe a *goal* and let an AI-powered user simulator (Gemini) try to achieve it. At the end, Gemini judges whether the agent met the goal and any additional expectations you specified.

This is valuable when you want to test that an agent can *complete a task*, without caring about the exact phrasing of each response — which is especially important for voice agents where natural language variation is expected.

---

## How simulations work

1. SCRAPI starts a real session with your agent using the Sessions API
2. Gemini plays the role of a human user, sending messages to the agent to try to achieve the goal
3. The conversation continues until the goal is met, the max number of turns is reached, or the agent ends the session
4. Gemini evaluates whether each step's `success_criteria` was met and whether any `expectations` were satisfied
5. SCRAPI produces a report with pass/fail status for each step and expectation

Because the user is simulated by a language model, the conversation is non-deterministic — each run may produce slightly different messages. This mirrors how real users behave.

---

## YAML format

Simulation files use the `evals:` key at the top level:

```yaml
evals:
  - name: "successful_order_lookup"
    tags: ["P0", "order_management"]
    session_parameters:
      order_12345_status: "shipped"
      order_12345_eta: "2026-04-18"
    steps:
      - goal: "Ask about the status of order ORD-12345"
        success_criteria: "The user has provided order ID ORD-12345 and the agent has acknowledged it"
        response_guide: "The user is a customer checking on a recent purchase. They are polite but want a quick answer."
        max_turns: 3

      - goal: "Get the order status and delivery date"
        success_criteria: "The agent has provided the shipping status and the estimated delivery date"
        max_turns: 2

    expectations:
      - "The agent correctly identified the order as shipped"
      - "The agent mentioned the estimated delivery date"
      - "The agent maintained a friendly, helpful tone throughout"
```

### Top-level fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Unique name for this evaluation |
| `tags` | list | Tags for filtering (e.g., `["P0", "smoke"]`) |
| `session_parameters` | dict | Variables injected at session start |
| `steps` | list | Ordered sequence of conversational goals |
| `expectations` | list | Post-conversation quality assertions evaluated by Gemini |

### Step fields

| Field | Type | Description |
|-------|------|-------------|
| `goal` | string | What the simulated user is trying to accomplish in this step |
| `success_criteria` | string | The condition that determines whether this step is complete |
| `response_guide` | string | Persona and context hints for the simulated user |
| `max_turns` | int | Maximum turns allowed before declaring the step incomplete |
| `static_utterance` | string | Instead of AI simulation, send this exact text (useful for testing specific inputs) |
| `inject_variables` | dict | Variables to inject for the first step only (overrides session_parameters) |

### Expectations

Expectations are evaluated by Gemini *after the full conversation completes*, looking at the entire transcript. They're natural language assertions:

```yaml
expectations:
  - "The agent never made up information that wasn't in the tool response"
  - "The agent asked for the order ID before looking it up"
  - "The agent offered to help with anything else before ending"
```

Each expectation is judged as Met or Not Met, with a justification from Gemini.

---

## The `SimulationEvals` class

For programmatic use, import `SimulationEvals`:

```python
from cxas_scrapi.evals.simulation_evals import SimulationEvals

sim_evals = SimulationEvals(
    app_name="projects/my-project/locations/us/apps/my-app",
)
```

### Running a single evaluation programmatically

The `simulate_conversation` method takes a `test_case` dict defining the steps and expectations:

```python
from cxas_scrapi.evals.simulation_evals import SimulationEvals

sim_evals = SimulationEvals(app_name="projects/my-project/locations/us/apps/my-app")

test_case = {
    "steps": [
        {
            "goal": "Ask about order ORD-12345",
            "success_criteria": "User provided order ID and agent acknowledged",
            "max_turns": 3,
        },
        {
            "goal": "Get delivery date",
            "success_criteria": "Agent provided estimated delivery date",
            "max_turns": 2,
        },
    ],
    "expectations": [
        "Agent maintained professional tone",
        "Agent never hallucinated data",
    ],
}

eval_conv = sim_evals.simulate_conversation(test_case=test_case)
report = eval_conv.generate_report()

# Goals report (one row per step)
print(report.goals_df)

# Expectations report (one row per expectation)
if report.expectations_df is not None:
    print(report.expectations_df)
```

### Running in parallel

Simulations can be slow because they involve multiple real API calls. Run them in parallel to speed things up:

```python
import concurrent.futures

test_cases = [...]  # list of test_case dicts

def run_single(tc):
    eval_conv = sim_evals.simulate_conversation(test_case=tc, console_logging=False)
    return eval_conv.generate_report()

with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
    futures = [executor.submit(run_single, tc) for tc in test_cases]
    reports = [f.result() for f in concurrent.futures.as_completed(futures)]
```

!!! tip "Parallel execution and rate limits"
    The Sessions API and Gemini both have rate limits. Start with `max_workers=3` and increase if you're not hitting errors. The skills system's Run skill handles this automatically.

---

## Audio modality

If your agent handles voice conversations, you can run simulations in audio mode:

```python
sim_evals = SimulationEvals(app_name="projects/my-project/locations/us/apps/my-app")

eval_conv = sim_evals.simulate_conversation(
    test_case=test_case,
    modality="audio",  # default is "text"
)
```

In audio mode, SCRAPI uses the Sessions API's audio streaming endpoint. The simulated user's messages are still text internally, but they're processed by the agent's audio pipeline, which exercises TTS/STT and any audio-specific callbacks.

---

## Interpreting results

The `SimulationReport` object has two DataFrames:

### `goals_df`

| Column | Description |
|--------|-------------|
| `eval_name` | Name of the simulation |
| `step_index` | Which step (0-indexed) |
| `goal` | The goal text |
| `status` | `Completed` or `Not Completed` |
| `justification` | Gemini's explanation |
| `turns_used` | How many turns it took |

### `expectations_df`

| Column | Description |
|--------|-------------|
| `eval_name` | Name of the simulation |
| `expectation` | The expectation text |
| `status` | `Met` or `Not Met` |
| `justification` | Gemini's explanation |

### Reading the output

```python
# Overall pass rate
total = len(report.goals_df)
passed = (report.goals_df["status"] == "Completed").sum()
print(f"Steps completed: {passed}/{total} ({passed/total*100:.0f}%)")

# Failed steps
failed = report.goals_df[report.goals_df["status"] != "Completed"]
for _, row in failed.iterrows():
    print(f"FAILED: {row['goal']}")
    print(f"  Reason: {row['justification']}")
```

---

## Tips for writing good simulations

**Keep steps focused**
: Each step should test one thing. Broad goals like "complete the full conversation" are hard to debug when they fail.

**Write meaningful success criteria**
: "The agent helped the user" is too vague. "The agent provided the order status and delivery date" is testable.

**Use `response_guide` to set tone**
: If your agent needs to handle impatient users or edge cases, use `response_guide` to set that context for the simulator.

**Use `static_utterance` for exact inputs**
: When you want to test how the agent handles a specific phrasing (e.g., "what's my ETA?"), use `static_utterance` to send that exact text.

**Use session parameters for mocking**
: Just like goldens, use `session_parameters` to inject mock tool responses so your simulations are deterministic and fast.
