# Generating & Interpreting Reports

## Report Types

### 1. Iteration Report (primary)
Generated automatically by `run-and-report.py` after each eval run. Shows what changed, pass rates by eval type, and failure triage.

```bash
# Automatic (recommended) -- runs evals + triage + report in one command
python .agents/skills/cxas-agent-foundry/scripts/run-and-report.py --message "what changed" --runs 5

# Manual -- generate report from existing results
python .agents/skills/cxas-agent-foundry/scripts/generate-iteration-report.py report --message "Fixed X by doing Y"
```

Output: HTML report in `<project>/eval-reports/iterations/iteration-N/`. Includes:
- Agent diff (what changed since last iteration)
- Golden pass rates (per-eval and overall)
- Sim pass rates (per-eval and overall)
- Tool test results
- Callback test results
- Triage categories for failures

### 2. Combined Report
Cross-iteration trend analysis showing pass rate progression over time.

```bash
python .agents/skills/cxas-agent-foundry/scripts/generate-combined-report.py
```

Output: HTML report in `<project>/eval-reports/combined/`. Includes:
- Pass rate trends across all iterations
- Per-eval stability analysis
- Regression detection

### 3. Coverage Analysis
On-demand analysis of eval coverage against agent architecture. No script -- generate manually by reading agent config and evals, then cross-referencing. See the Coverage Report Template below.

## Interpreting Results

### Key Metrics

| Metric | Good | Investigate | Action |
|--------|------|-------------|--------|
| Golden pass rate | >90% across 5+ runs | 70-90% | Check triage categories -- fix agent or tune thresholds |
| Sim pass rate | >80% across 3+ runs | 60-80% | Read transcripts, check expectations |
| Tool test pass rate | 100% | <100% | Fix tool code -- these are deterministic |
| Callback test pass rate | 100% | <100% | Fix callback code -- these are deterministic |

### Triage Categories

The triage script (`triage-results.py`) categorizes each failure:

| Category | Meaning | Fix |
|----------|---------|-----|
| TEXT_MISMATCH | Agent phrasing differs from golden | Fix instruction or make response deterministic via callback |
| TOOL_MISSING | Wrong tool or missing tool call | Fix instruction, check tool availability, use trigger pattern |
| EXPECTATION_FAIL | Custom LLM judge expectation not met | Read judge explanation -- fix agent or rephrase expectation |
| EXTRA_TURNS | Agent produces output after golden ends | End golden before transfer, or extend to cover sub-agent |
| SCORES_PASS_BUT_FAIL | Platform scorer bug -- all scores pass but result is FAIL | Not fixable -- platform issue. Exclude from adjusted pass rate |
| TIMEOUT | Eval timed out | Increase max_turns, check tool latency |

### When to Adjust vs When to Fix

- **Adjusted pass rate** (excluding SCORES_PASS_BUT_FAIL and TIMEOUT) is the real metric -- use this to track progress
- **Don't trust a single run** -- run goldens at least 5 times and use `triage-results.py --last 3` to average
- **4/5 evals are fine** -- the one failure is likely LLM/sim-user variance. Focus on evals at 3/5 or below
- **Golden pass rate up but sim pass rate down** = overfitting. The agent is becoming more rigid, not better

## Coverage Report Template

When asked to analyze eval coverage, produce this structure:

```markdown
# Eval Coverage Report -- <APP_NAME>

## Agent Architecture
- Root Agent: <name>
- Sub-agents: <list>

## Coverage Summary
| Dimension | Total | Covered | Coverage % | Gaps |
|-----------|-------|---------|------------|------|
| Agents    |       |         |            |      |
| Tools     |       |         |            |      |
| Transfers |       |         |            |      |
| Callbacks |       |         |            |      |

## Instruction Directive Coverage
For each agent, decompose the instruction into testable directives and cross-reference against evals.

| Agent | Category | Directive | Covered? | Covering Eval(s) |
|-------|----------|-----------|----------|-------------------|

Categories: Persona, Conversation Flow, Tool Usage, Conditional Behavior, Guardrails, Escalation, Response Format, Edge Cases, State Management, Transfer Rules.

## Gaps & Recommendations
1. Untested tools
2. Untested transfers
3. Uncovered instruction directives
4. Missing negative/edge case tests
5. Untested callbacks
```

## Experiment Log

The `run-and-report.py` script maintains `<project>/experiment_log.md` tracking what was tried across iterations. Before proposing fixes, check this log to avoid repeating approaches that caused regressions.

---

## Detailed Coverage Report Template

When generating a coverage analysis manually, use `references/api-reference.md` -> Diagnostic REST Commands to fetch agent config, evals, tools, guardrails, and expectations. Then produce this report:

```
# Eval Coverage Report -- <APP_NAME>
Generated: <DATE>

## Agent Architecture
- Root Agent: <name>
- Sub-agents: <list with descriptions>
- Total agents: <N>

## Coverage Summary

| Dimension           | Total | Covered by Evals | Coverage % | Gaps |
|---------------------|-------|-------------------|------------|------|
| Agents              |       |                   |            |      |
| Tools               |       |                   |            |      |
| Agent Transfers     |       |                   |            |      |
| Guardrails          |       |                   |            |      |
| Instruction Intents |       |                   |            |      |

## Evaluation Inventory

| # | Name | Type | Tags | Turns/Rubrics | Tools Tested | Agents Tested | Status |
|---|------|------|------|---------------|-------------|---------------|--------|

## Tool Coverage

| Tool Name | Type | Used in N Evals | Eval Names | Params Tested |
|-----------|------|-----------------|------------|---------------|

## Agent Transfer Coverage

| From Agent | To Agent | Tested? | Eval Names |
|------------|----------|---------|------------|
```

### Instruction Coverage Analysis (Deep Prompt Audit)

This is the most critical section of a coverage report. For EACH agent:

1. **Read the full instruction text** from the agent config
2. **Decompose the instruction into discrete, testable directives** using the categories below
3. **Cross-reference each directive against all evaluations** to determine if any eval exercises that behavior
4. **Flag uncovered directives** as gaps

#### How to Decompose Agent Instructions

Parse the instruction text and extract every distinct directive into one of these categories:

| Category | What to look for | Examples |
|----------|-----------------|---------|
| **Persona / Identity** | Role definitions, tone, communication style | "You are a virtual assistant", "Be professional and factual" |
| **Conversation Flow Rules** | Ordered steps, required sequences, state transitions | "First ask X, then do Y", "After authentication, proceed to..." |
| **Tool Usage Rules** | When to call which tool, required parameters | "Use the FAQ tool when...", "Call the diagnostic tool after..." |
| **Conditional Behaviors** | If/then/else logic, branching on user input or state | "If user has multiple lines, ask which one", "If API fails, escalate" |
| **Guardrails / Constraints** | Things the agent must never do, boundaries | "NEVER ask the user to call...", "Only use information from tool responses" |
| **Escalation Rules** | When to transfer to human or another agent | "Escalate if user uses profanity", "Transfer to sub_agent_a when..." |
| **Response Format Rules** | How to format responses, pronunciation | "Pronounce numbers as individual digits", "Use brief acknowledgements" |
| **Edge Case Handling** | Specific scenarios with special handling | "If user asks about competitor...", "Handle inappropriate questions by..." |
| **Variable / State Management** | Session variables to set, state transitions | "Set auth_status to...", "Update device_type when..." |
| **Transfer Rules** | When to route to child agents | "Transfer to sub_agent_b when user reports a specific issue type" |

#### Instruction Directive Table

For each agent, produce:

| # | Agent | Category | Directive | Instruction Quote | Covered? | Covering Eval(s) | Notes |
|---|-------|----------|-----------|-------------------|----------|-------------------|-------|

#### How to Determine if a Directive is Covered

- **Golden eval covers a directive** if a turn's `userInput` would trigger the directive's condition AND an `expectation` (toolCall, agentResponse, agentTransfer, updatedVariables) verifies the expected outcome
- **Scenario eval covers a directive** if the scenario's `task` triggers the directive AND a `rubric` or `scenarioExpectation` checks the behavior
- **NOT covered** if no eval triggers the condition, or an eval triggers it but has no expectation verifying the outcome, or the covering eval is `invalid: true`

#### Coverage Summary Per Agent

```
### <AGENT_NAME> Instruction Coverage

- **Total directives extracted**: N
- **Covered**: N (X%)
- **Partially covered**: N (X%)
- **Not covered**: N (X%)

**Most critical uncovered directives:**
1. <directive> -- Why it matters: <impact if untested>
2. <directive> -- Why it matters: <impact if untested>
```

### Additional Coverage Tables

```
## Python Code / Callback Coverage

| Location | Callback Type | Description | Tested? | Eval Name |
|----------|---------------|-------------|---------|-----------|

## Guardrail Coverage

| Guardrail | Type | Enabled | Tested? | Eval Name |
|-----------|------|---------|---------|-----------|

## Custom LLM Judges (Evaluation Expectations)

| Name | Prompt Summary | Tags | Used In Evals |
|------|----------------|------|---------------|

## Scheduled Runs

| Name | Frequency | Active | Next Run | Evals Included |
|------|-----------|--------|----------|----------------|

## Evaluation Datasets

| Name | # Evals | Evals Included |
|------|---------|----------------|

## Gaps & Recommendations

1. Untested tools
2. Untested agent transfers
3. Uncovered instruction directives
4. Missing negative/edge case tests
5. Untested guardrails
6. Untested callbacks/python code
7. No scheduled runs (recommend if missing)
```

---

## Tips for Report Generation

- **Instruction analysis is the most important part of coverage** -- read every agent's full instruction text, not just summaries
- When decomposing instructions, look for these textual patterns:
  - Numbered lists / bullet points -> individual directives
  - `<persona>`, `<guidelines>`, `<constraints>` XML tags -> section boundaries
  - "always", "never", "must", "should", "do not" -> hard constraints/guardrails
  - "if...then", "when...do" -> conditional behaviors
  - "transfer to", "escalate", "hand off" -> transfer/escalation rules
  - "call", "use", "invoke" + tool name -> tool usage rules
  - "set", "update", "store" + variable name -> state management rules
  - Indented sub-bullets under a step -> sub-directives (each testable independently)
- Include the **exact quote** from the instruction for each directive so the user can trace it back
- For golden evals: map each expectation's tool reference back to the tool's displayName for readability
- For scenario evals: rubric scores below 0.7 are a concern
- Hallucination scores above 0.3 warrant investigation
- Semantic similarity below 3.0 (on 0-4 scale) indicates response quality issues
- Tool invocation correctness below 1.0 means missed or wrong tool calls
- Compare trends across the last 10 results to spot regressions
- When a golden eval is marked `invalid: true`, it references a deleted/changed tool -- flag prominently
