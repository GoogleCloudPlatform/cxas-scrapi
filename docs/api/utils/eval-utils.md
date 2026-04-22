---
title: EvalUtils
---

# EvalUtils

`EvalUtils` extends `Evaluations` with convenience methods for working with YAML-based evaluation files — the format used by the CXAS Scrapi eval runner. It knows how to load conversations from a YAML file, validate them against a Pydantic schema, and convert them to pandas DataFrames for analysis or reporting.

Think of `EvalUtils` as the bridge between your local eval files and the CXAS API: load from disk, inspect or transform, then push or run via the inherited `Evaluations` methods.

Key Pydantic models you'll encounter:

- **`Conversation`** — one test conversation with turns, expectations, tags, and session parameters.
- **`Turn`** — a single round-trip with `user` text, `agent` response, and optional `tool_calls`.
- **`Conversations`** — the top-level container, supporting `common_session_parameters` shared across all conversations.

## Quick Example

```python
from cxas_scrapi import EvalUtils

app_name = "projects/my-project/locations/us/apps/my-app-id"
eu = EvalUtils(app_name=app_name)

# Load a YAML eval file
conversations = eu.load_golden_evals_from_yaml("evals/billing_evals.yaml")
print(f"Loaded {len(conversations.conversations)} conversations")

# Convert to a DataFrame for analysis
df = eu.evals_to_dataframe(conversations)
print(df[["conversation", "turns", "expectations"]].head())

# Export evaluation results to a spreadsheet-ready format
results_df = eu.get_evaluation_results_dataframe()
results_df.to_csv("eval_results.csv", index=False)
```

## Reference

::: cxas_scrapi.utils.eval_utils.EvalUtils

::: cxas_scrapi.utils.eval_utils.Conversation

::: cxas_scrapi.utils.eval_utils.Conversations

::: cxas_scrapi.utils.eval_utils.Turn
