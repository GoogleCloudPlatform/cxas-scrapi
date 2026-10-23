# Verification & Test Suite (`TEST.md`) for `cxas-html-reporting`

This document defines manual and automated verification procedures for the `cxas-html-reporting` agent skill and generator script (`generate_report.py`).

---

## 1. Unit & Schema Parsing Verification Tests

Run the standalone unit test suite to verify schema parsing, error categorization, and false-alarm noise filter behavior:

```bash
python3 test_generate_report.py
```

### Expected Results
- **Schema Mapping**: All Protobuf score fields (`semanticSimilarityResult`, `overallToolInvocationResult`, `hallucinationResult`, `rubricOutcomes`) map without errors.
- **Tool Order Filter**: `toolOrderedInvocationScore < 1.0` is ignored when overall outcome equals `"PASS"`.
- **Unexpected Handovers**: Observed agent transfer to `main` with outcome `"FAIL"` is routed to `Agent Handovers` category.
- **REST Linter Audit**: Regex patterns catch backticked pills and single-parent violations.

---

## 2. Functional End-to-End Test (Live Cloud Project)

Execute both HTML and JSON reporting workflows against a live test application:

```bash
# Test 1: Generate Human-Facing HTML Report in Dev Environment
python3 generate_report.py \
  --app-id="projects/connectors-incubation-test-1/locations/us-east1/apps/72d4c6b5-75d2-44b8-b369-84d2223361dd" \
  --output="/tmp/test_eval_report.html" \
  --format="html" \
  --env="dev"

# Test 2: Generate Machine-Readable JSON Telemetry in Dev Environment
python3 generate_report.py \
  --app-id="projects/connectors-incubation-test-1/locations/us-east1/apps/72d4c6b5-75d2-44b8-b369-84d2223361dd" \
  --output="/tmp/test_eval_report.json" \
  --format="json" \
  --env="dev"
```

### Verification Criteria

| Verification Check | Target Artifact | Verification Assertion |
| :--- | :--- | :--- |
| **HTML File Creation** | `/tmp/test_eval_report.html` | File exists, size `> 30 KB`, contains valid `<!DOCTYPE html>`. |
| **Console Domain Link** | `/tmp/test_eval_report.html` | Clickable links point to `ces-console-dev.corp.google.com`. |
| **Tab Count Badges** | `/tmp/test_eval_report.html` | Header contains dynamic tabs: *Tool Calls*, *State & Variables*, *Generative & Phrasing*, *Agent Handovers*, and *Project Linter Audit*. |
| **JSON Schema Compliance** | `/tmp/test_eval_report.json` | Top-level keys `total`, `passed`, `failed`, `projectLinterAudit`, and `results` present. |
| **Handovers Category** | `/tmp/test_eval_report.json` | Result `5ea0f649-52f1-46a8-84d7-0cb1def9b747` lists transfer to `'main'` under `"Agent Handovers"`. |
| **Cloud Project Linter** | `/tmp/test_eval_report.json` | `projectLinterAudit.totalIssues` displays live prompt/architectural issues (`> 0`). |

---

## 3. Test Execution Summary

| Test Suite | Execution Command | Result | Notes |
| :--- | :--- | :---: | :--- |
| **Unit Test Parser Suite** | `python3 test_generate_report.py` | ✅ PASS | Offline fixtures verify schema parsing |
| **HTML Report Generator** | `generate_report.py --format=html` | ✅ PASS | Generates standalone single-file dashboard |
| **JSON Machine Telemetry** | `generate_report.py --format=json` | ✅ PASS | Exports deep telemetry for subagent fix loops |
| **Cloud Project Linter** | `audit_cloud_project_linter()` | ✅ PASS | Audits live agents via REST API |
