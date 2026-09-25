---
title: ShadowEvals
---

# ShadowEvals

`ShadowEvals` replays a past conversation against the current version of an
agent, streaming the caller's original recorded audio turn by turn and falling
back to TTS only when the new agent needs information no recording contains.
See the [Shadow Evals guide](../../guides/evaluation/shadow-evals.md) for the
YAML format, prerequisites and workflows.

Key concepts:

- **`ShadowTestCase`**: one case, which is a past `conversation_id` plus
  natural-language `expectations`, optional seeded `session_parameters` /
  `use_tool_fakes`, and a `replay_mode` (`hybrid` or `exact`).
- **`ShadowEvals.preflight()`**: checks a case before running it (trace
  reachable, caller turns recorded, target app recording, seeding hints) and
  returns a `ShadowPreflightResult`.
- **`ShadowEvals.run_shadow_evals()`**: runs many cases × runs, optionally in
  parallel, with preflight on by default. Pass `artifacts_dir` to keep audio
  and a side-by-side HTML report per run.
- **`ShadowUserConversation`**: the simulated caller; its `turn_logs` record,
  for every turn, whether recorded audio or TTS was used and why.

## Quick Example

```python
from cxas_scrapi.evals import ShadowEvals

shadow = ShadowEvals(app_name="projects/my-project/locations/us/apps/my-app")
case = {
    "conversation_id": "7f3c9a52-...",
    "gcs_bucket": "gs://my-recordings-bucket",
    "session_parameters": {"customer_tier": "gold"},
    "use_tool_fakes": True,
    "expectations": ["The agent explains the delivery status of the order."],
}

print(shadow.preflight(case).issues)
conv = shadow.run_shadow_conversation(case, artifacts_dir="shadow_artifacts/")
print(conv.generate_report())
print("Side-by-side report:", conv.report_path)
```

## Reference

::: cxas_scrapi.evals.shadow_evals.ShadowEvals

::: cxas_scrapi.evals.shadow_evals.ShadowTestCase

::: cxas_scrapi.evals.shadow_evals.ShadowPreflightResult

::: cxas_scrapi.evals.shadow_evals.ShadowPreflightIssue

::: cxas_scrapi.evals.shadow_evals.ShadowUserConversation

::: cxas_scrapi.evals.shadow_evals.ShadowTurnLog

::: cxas_scrapi.evals.shadow_evals.ShadowReport
