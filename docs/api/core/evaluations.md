---
title: Evaluations
---

# Evaluations

`Evaluations` is the Swiss Army knife for CXAS's built-in evaluation system. It lets you list, run, export, create, and update both *golden* evaluations (fixed expected conversation transcripts) and *scenario* evaluations (probabilistic multi-turn scenarios).

A few things worth knowing about this class:

- **`run_evaluation()`** can target specific evaluations by display name, or sweep entire categories (`goldens`, `scenarios`, `all`).
- **`export_evaluation()`** converts a CXAS evaluation into a human-readable YAML or JSON file, with evaluation expectations sideloaded as separate JSON files.
- **`ExportFormat`** is a simple enum: `ExportFormat.YAML` (default) or `ExportFormat.JSON`.
- **`bulk_export_evals()`** is a convenience wrapper that exports every evaluation of a given type to a local directory in one go.

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

# Run all scenarios at once
evals.run_evaluation(eval_type="scenarios")

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
