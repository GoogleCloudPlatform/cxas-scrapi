# Pull Request / CL Proposal: `feat(cxas-reporting): production-ready 6-dimension reporting generator & live cloud linter`

## Summary

This CL enhances the `cxas-html-reporting` skill and script (`generate_report.py`) to reach 100% production readiness for Google Cloud Customer Engagement Suite (`CXAS` / `CES`) evaluation diagnostics.

### Key Capabilities Added

1. **Exhaustive Schema Parsing (100% `evaluation.proto` Coverage)**
   - Implemented deep schema parsing for Google Cloud CES `v1beta` `evaluation.proto` fields:
     - `TurnReplayResult` / `ScenarioResult` step outcomes.
     - `SemanticSimilarityResult` (`0`–`4` consistency scale + numeric scores).
     - `OverallToolInvocationResult` (`parameterCorrectnessScore`, `toolOrderedInvocationScore`).
     - `HallucinationResult` (`score == 0` justifications).
     - `UserGoalSatisfactionResult` and `TaskCompletionResult`.
     - `ScenarioRubricOutcome` evaluation scores (`< 0.7`).
     - System/Quota/Runtime `EvaluationErrorInfo` enums (`QUOTA_EXHAUSTED`, `RUNTIME_FAILURE`, `USER_SIMULATION_FAILURE`, `CONVERSATION_RETRIEVAL_FAILURE`).

2. **Noise Suppression for Sequence Ordering Scores**
   - In raw evaluation traces, float `toolOrderedInvocationScore: 0` is emitted even when `overallToolInvocationResult.outcome == "PASS"`.
   - Modified script to only flag sequence ordering drops (`[Tool Order Failure]`) when the overall tool invocation step actually evaluates to `"FAIL"`, eliminating diagnostic false positives.

3. **6-Dimension Diagnostic Taxonomy & Dedicated Tabs**
   - **Tool Calls** (`tab-tool-calls`): Schema errors, parameter score drops, unknown tool calls.
   - **State & Variables** (`tab-variables`): Dynamic template variable binding gaps.
   - **Generative & Phrasing** (`tab-semantic`): Hallucinations, semantic similarity drops, custom rubric failures.
   - **Agent Handovers** (`tab-handovers`): Subagent handoff failures, unexpected escalations to `main`, unauthorized sibling transfers.
   - **System & Infrastructure** (`tab-system`): Quota exhaustion, runtime engine crashes, trace retrieval timeouts.
   - **Project Architecture & Prompt Linter Audit** (`tab-linter`): REST API audit (`GET /v1beta/{app_id}/agents`) running 12 SCRAPI best-practice rules directly on all live cloud subagents.

4. **Environment-Aware CES Console Deep Links**
   - Dynamically constructs internal console URLs based on `--env=prod` or `--env=dev`:
     `https://ces-console-dev.corp.google.com/projects/{project}/locations/{location}/apps/{app}/evaluate/{goldens|scenarios}/{eval_id}/results/{result_id}`

5. **Dual Machine & Human Output Formats**
   - `--format=html`: Standalone, portable single-file dashboard with embedded Tailwind & interactive tabs.
   - `--format=json`: Machine-readable `metrics.json` (`categorizedIssues`, `detailedTelemetry`, `projectLinterAudit`) for automated LLM self-healing feedback loops.

---

## Files Modified / Added

| File | Status | Description |
| :--- | :---: | :--- |
| `generate_report.py` | **Updated** | Added 6-category taxonomy, live cloud REST linter, tool-order filter, and deep links. |
| `SKILL.md` | **Updated** | Full skill instruction documentation with flag reference and JSON schema specification. |
| `TEST.md` | **New** | Verification and test documentation following Google Agent Skill review guidelines. |
| `test_generate_report.py` | **New** | Offline unit test suite (`3/3` passing). |

---

## Verification & Testing

### Offline Unit Tests
```bash
python3 test_generate_report.py
```
Output:
```
...
----------------------------------------------------------------------
Ran 3 tests in 0.000s

OK
```

### Live Dev Environment Test
Executed on dev application `72d4c6b5-75d2-44b8-b369-84d2223361dd`:
- **Evaluations Processed**: `10` runs (`1` PASS, `9` FAIL).
- **Console Links Verified**: Clicking deep link routes to `https://ces-console-dev.corp.google.com/.../results/89323b47-14b7-4cbd-a09a-9735b88a9d56`.
- **Unexpected Handovers Surfaced**: `[Routing/Transfer Failure (Turn 2)]: Agent transfer to 'main' failed expectation (Outcome: FAIL).`
- **Project Linter Audit**: Discovered `108` prompt and architectural issues across live subagents over REST API.

---

## Tags
- `EVALIN_REPORT=N/A (Utility Reporting Skill)`
- `SKIP_EVAL=Offline generator and formatting utility without nondeterministic model inference`
