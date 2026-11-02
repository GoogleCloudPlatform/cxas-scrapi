---
title: Evaluations
---

# Evaluations

`Evaluations` is the Swiss Army knife for CXAS's built-in evaluation system. It lets you list, run, export, create, and update both *golden* evaluations (fixed expected conversation transcripts) and *scenario* evaluations (probabilistic multi-turn scenarios).

A few things worth knowing about this class:

- **`run_evaluation()`** can target specific evaluations by display name, or sweep entire categories (`goldens`, `scenarios`, `all`).
- **`import_evaluations(yaml_content=..., conflict_strategy=...)`** imports CXAS 2.0 Milestone YAML (`evals:`) or Golden YAML directly via the CES REST `:importEvaluations` endpoint.
- **`get_evaluation_rest()` / `get_evaluation_result_rest()` / `run_evaluation_rest()`** interact with the CES REST API directly to preserve `scenario.milestones` and `scenarioResult.milestoneResults` (`COMPLETED`, `IN_PROGRESS`, `NOT_STARTED`).
- **`export_evaluation()`** converts a CXAS Golden or Scenario Milestone evaluation into a human-readable YAML or JSON file, with evaluation expectations sideloaded as separate JSON files.
- **`ExportFormat`** is a simple enum: `ExportFormat.YAML` (default) or `ExportFormat.JSON`.
- **`bulk_export_evals()`** is a convenience wrapper that exports every evaluation of a given type to a local directory in one go.
- **`update_evaluation()`** and **`sync_evaluations()`** automatically resolve tool `displayName` references in expectations to server-minted tool resource IDs, fall back to `delete` + `create` when `UpdateEvaluation` returns `501 Not Implemented`, and support tag-scoped pruning (`prune_tag`).

## Quick Example

```python
from cxas_scrapi import Evaluations
from cxas_scrapi.core.evaluations import ExportFormat

app_name = "projects/my-project/locations/us/apps/my-app-id"
evals = Evaluations(app_name=app_name)

# List everything
all_evals = evals.list_evaluations()
print(f"Found {len(all_evals)} evaluations")

# Run a specific golden by display name
run_op = evals.run_evaluation(evaluations=["My Golden Conversation"])

# Run all scenarios (including CXAS 2.0 Milestone evaluations) at once
evals.run_evaluation(eval_type="scenarios", run_count=3)

# Export one golden to YAML
yaml_str = evals.export_evaluation(
    evaluation_id=all_evals[0].name,
    output_format=ExportFormat.YAML,
    output_path="exported_eval.yaml",
)

# Bulk export all goldens to a local directory
evals.bulk_export_evals(eval_type="goldens", output_dir="./exports")
```

## Reference

::: cxas_scrapi.core.evaluations.Evaluations

::: cxas_scrapi.core.evaluations.ExportFormat
