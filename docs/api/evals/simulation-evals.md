---
title: SimulationEvals
---

# SimulationEvals

`SimulationEvals` runs AI-driven end-to-end conversation simulations against your CXAS agent. Instead of scripting exact utterances, you describe *goals* and *success criteria* — and a Gemini model figures out what to say at each turn to try to achieve them. This is a great way to test how your agent handles realistic, messy, unpredictable conversations.

Here are the key concepts:

- **`Step`** (Pydantic model) — a single goal within a simulation, with a `goal`, `success_criteria`, optional `response_guide`, and a `max_turns` limit. Steps can also include a `static_utterance` for when you want a fixed first message, and `inject_variables` for seeding session state.
- **`StepStatus`** enum — tracks whether each step is `NOT_STARTED`, `IN_PROGRESS`, or `COMPLETED`.
- **`simulate_conversation()`** — drives the full multi-turn loop, returning an `LLMUserConversation` object that contains the transcript, step progress, and expectation results.
- **`generate_report()`** — produces a `SimulationReport` with two DataFrames: goal progress and expectation results. It renders as styled HTML in a Jupyter notebook.

## Quick Example

```python
from cxas_scrapi import SimulationEvals

app_name = "projects/my-project/locations/us/apps/my-app-id"
sim = SimulationEvals(app_name=app_name)

test_case = {
    "steps": [
        {
            "goal": "User wants to check their account balance",
            "success_criteria": "Agent provides a numeric balance and account status",
            "max_turns": 5,
        },
        {
            "goal": "User asks to dispute a charge",
            "success_criteria": "Agent acknowledges the dispute and provides a reference number",
            "max_turns": 8,
        },
    ],
    "expectations": [
        "The agent should never ask for the full credit card number",
        "The agent should offer to escalate if it cannot resolve the dispute",
    ],
}

# Run the simulation
conversation = sim.simulate_conversation(
    test_case=test_case,
    console_logging=True,
)

# View the report
report = conversation.generate_report()
print(report)  # Colorized in terminal, styled HTML in Jupyter
```

## Reference

::: cxas_scrapi.evals.simulation_evals.SimulationEvals

::: cxas_scrapi.evals.simulation_evals.Step

::: cxas_scrapi.evals.simulation_evals.StepStatus

::: cxas_scrapi.evals.simulation_evals.SimulationReport
