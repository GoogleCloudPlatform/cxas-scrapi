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
from cxas_scrapi.utils.rate_limiter import RateLimiter

app_name = "projects/my-project/locations/us/apps/my-app-id"

# Optional: configure a rate limiter to pace simulation turns and prevent quota exhaustion
limiter = RateLimiter(requests_per_minute=30.0)
sim = SimulationEvals(app_name=app_name, rate_limiter=limiter)

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

## Naturalness Metric (optional)

`SimulationEvals` can also grade *how human the agent sounds*. The metric is opt-in: a test case that does not declare a `naturalness_metric` block behaves exactly as before, with no extra Gemini call and no extra keys in the results. See the [Local Simulations guide](../../guides/evaluation/local-simulations.md#naturalness-metric) for the YAML schema and worked examples.

- **`NaturalnessConfig`** (Pydantic model) — the per-test-case configuration: which model grades, which qualities are scored, the label bands, the turn/conversation blend weight, the perceived-latency bands and weight, and an optional `pass_threshold`. Unknown keys are tolerated, so newer YAML still loads.
- **`NaturalnessResult`** — the aggregate result for one simulation: `overall_score`, `overall_label`, per-turn gradings, conversation-level factors, `factor_averages`, `label_counts`, `latency_ms_by_turn`, `failure_reasons`, and `passed`. Exposed as `conversation.naturalness_result`.
- **`TurnNaturalness`** — the grading for a single agent turn: its label, 1–5 score, justification, and the factors behind it.
- **`NaturalnessFactor`** — one scored quality (e.g. `pacing`) with its 1–5 score, an optional short `value` descriptor, and the evidence-citing `reason`.
- **`NaturalnessLabel`** enum — `Bot-like`, `Transitional`, or `Human-like`.
- **`evaluate_naturalness()`** — grades a simulation trace directly, optionally against the agent's recorded audio and the platform's per-turn perceived latency. Returns `None` (after logging) when there is nothing to grade or the grading call fails, so the metric can never break a run.

In `audio` modality the agent's recordings are attached to the grading call automatically, so pacing and emotion are judged from what the caller heard. `perceivedLatency` is measured from the conversation trace rather than judged, and a turn past `latency_failure_threshold_ms` fails the simulation outright.

## Reference

::: cxas_scrapi.evals.simulation_evals.SimulationEvals

::: cxas_scrapi.evals.simulation_evals.Step

::: cxas_scrapi.evals.simulation_evals.StepStatus

::: cxas_scrapi.evals.simulation_evals.SimulationReport

::: cxas_scrapi.evals.naturalness.NaturalnessConfig

::: cxas_scrapi.evals.naturalness.NaturalnessResult

::: cxas_scrapi.evals.naturalness.TurnNaturalness

::: cxas_scrapi.evals.naturalness.NaturalnessFactor

::: cxas_scrapi.evals.naturalness.NaturalnessLabel

::: cxas_scrapi.evals.naturalness.evaluate_naturalness
