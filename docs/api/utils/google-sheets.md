---
title: GoogleSheetsUtils
---

# GoogleSheetsUtils

`GoogleSheetsUtils` makes it easy to read from and write to Google Sheets — a common destination for eval results, test reports, and conversation analytics that need to be shared with non-technical stakeholders.

You'll typically reach for this class after running a batch of evaluations with `ToolEvals` or `SimulationEvals` and wanting to push the results DataFrame into a shared spreadsheet automatically.

## Quick Example

```python
from cxas_scrapi import GoogleSheetsUtils
import pandas as pd

gs = GoogleSheetsUtils(creds_path="/path/to/service_account.json")

# Read data from a sheet
df = gs.read_sheet(
    spreadsheet_id="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms",
    sheet_name="Tool Eval Results",
)
print(df.head())

# Write a DataFrame back to a sheet
results_df = pd.DataFrame([
    {"test": "lookup_account", "status": "PASSED", "latency_ms": 145.2},
    {"test": "set_session_state", "status": "FAILED", "latency_ms": 88.0},
])

gs.write_sheet(
    spreadsheet_id="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms",
    sheet_name="Tool Eval Results",
    data=results_df,
)
print("Results uploaded to Google Sheets!")
```

## Reference

::: cxas_scrapi.utils.google_sheets_utils.GoogleSheetsUtils
