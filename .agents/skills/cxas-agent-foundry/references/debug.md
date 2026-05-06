# Eval Debugger

Methodology for systematically debugging eval failures and improving agent behavior.

## Core Principles

- **Diagnose first, but default to fixing the agent.** Eval expectations represent the contract with the user. When in doubt, assume the agent is wrong — never change expectations just to make tests pass.
- **Don't trust a single run.** A golden that passes 1/1 may fail 2/5. Use `--runs 5` and `triage-results.py --last 3` to see real pass rate. Only consider an eval stable when it passes consistently.
- **Don't over-fix 4/5 evals.** A 4/5 is likely sim-user randomness. Focus on 3/5 or below — and a 4/5 that drops to 3/5 after a "fix" is a regression.
- **Don't ping-pong.** When fix A regresses eval B, don't flip the fix. Read both transcripts, find the instruction conflict, resolve it. The dispatch system tags clusters with `regression_status: "regression"` and attaches `regression_context` (the prior `--message` and snapshot dir) so the triage subagent reads the prior change first and proposes a forward-narrowing fix instead of a flip. Mixed regression+new clusters auto-split. Trust the split — they need different remediation paths.
- **Don't overfit.** Hardcoded phrase lists, exact-keyword triggers, bypassing the LLM for intent — all signs you're optimizing for goldens at the cost of real conversations. Watch for golden pass rate up + sim pass rate down.
- **Diff before push if local and platform may have diverged.** A blind `cxas pull` overwrites local edits; a blind `cxas push` overwrites platform edits. Pull to a temp dir (`--target-dir <tmp>`), diff against your local `cxas_app/`, reconcile manually, then push.
- **Plan before structural changes.** Cross-cutting changes (new agent, `before_agent` state derivation, multi-agent routing) need user approval; simple `instruction.txt` tweaks don't.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Reading Triage Output](#reading-triage-output)
- [Prioritizing Failures](#prioritizing-failures)
- [The Iteration Loop](#the-iteration-loop)
- [Fix Reference](#fix-reference)
  - [Verifying the subagent's `suggested_fix_type`](#verifying-the-subagents-suggested_fix_type)
  - [Recommended Fix Approach](#recommended-fix-approach)
  - [Improvement Strategies by Issue Type](#improvement-strategies-by-issue-type)
  - [Simulation Failures](#simulation-failures)
  - [Debugging Regressions](#debugging-regressions)
  - [Tuning Scoring Thresholds](#tuning-scoring-thresholds)
  - (component / callback / audio test failures → `references/eval-templates.md`)
  - (decision tree / patterns / transcripts → `agents/triage-failure.md`)
- [Sub-agent and script recovery](#sub-agent-and-script-recovery)
- [Common Mistakes](#common-mistakes)
- [Appendix: Bootstrap from Existing Agent](#appendix-bootstrap-from-existing-agent)

### Load additional references as needed:
- **TDD structure and generation**: `references/tdd-guide.md` -- load when generating or updating the TDD
- **Architecture and anti-patterns**: `references/gecx-design-guide.md` -- load when fixing instruction issues or architectural problems
- **Callback API and patterns**: `references/callback-api.md` -- load when fixing callback behavior
- **Eval YAML formats**: `references/eval-templates.md` -- load when fixing eval configuration
- **Report interpretation**: `references/generating-reports.md` -- load when interpreting triage results or understanding triage categories
- **SCRAPI API calls**: `references/api-reference.md`

## Prerequisites

Check `<project>/gecx-config.json` first for project configuration (where `<project>` is the active project folder from `.active-project`, e.g., `troubleshoot-agent/`). If not present, check memory or ask the user.

| Prerequisite | Check | If missing |
|-------------|-------|------------|
| **Environment** | `.venv/` exists, `cxas-scrapi` installed | Follow Onboarding Flow in `references/setup.md` |
| **App name** | `<project>/gecx-config.json` -> `deployed_app_id` | Ask the user |
| **TDD (Mandatory)** | `<project>/tdd.md` | See `references/build.md` → Eval Creation (reverse-engineer TDD + bootstrap evals + run baseline), then return here |
| **Evals** (goldens, sims, tool tests, callback tests) | `<project>/evals/{goldens,simulations,tool_tests,callback_tests}/` populated | See `references/build.md` → Eval Creation (bootstrap-evals.py + dispatch eval-writer per type) |
| **Target pass rate** | Ask the user | e.g., 90%, 100% -- this is your exit criteria for the iteration loop |
| **Channel** | Ask the user | text or audio |

## Reading Triage Output

The script prints SUMMARY (counts by category), PER-EVAL (per-golden detail), and FAILURES BY CATEGORY (groups). Track **Adjusted pass rate** across iterations — it excludes SCORES_PASS_BUT_FAIL, TIMEOUT, and EVAL_ERROR (platform/config issues you can't fix by changing the agent). Use FAILURES BY CATEGORY to spot patterns: 3 evals failing TOOL_MISSING for the same tool is one fix, not three.

## Prioritizing Failures

When triage shows multiple failures, fix them in this order. Earlier fixes often resolve later ones -- a missing tool fix frequently eliminates text mismatches downstream.

**Fix order:**

1. **EVAL_ERROR** -- broken eval config blocks you from even measuring progress. Fix these first so your signal is clean.
2. **TOOL_TEST_FAIL / CALLBACK_TEST_FAIL** -- foundation tests. When a tool or callback is broken, every golden/sim that depends on it usually fails too. Fixing foundation first cascades — same logic as fixing EVAL_ERROR before chasing application-level symptoms.
3. **TOOL_MISSING** -- the most impactful application-level category. When the agent can't find the right tool, it improvises with wrong tools or skips the action entirely, causing cascading text and expectation failures.
4. **EXPECTATION_FAIL** -- custom LLM judge failures usually indicate real behavioral gaps. Read the judge explanation to understand what's wrong.
5. **HALLUCINATION** -- agent fabricating information is a trust violation. Fix by removing example phrases from instructions and adding grounding constraints.
6. **TEXT_MISMATCH** -- sometimes these resolve after fixing TOOL_MISSING. If they persist, check whether the instruction changed but the golden still expects old phrasing.
7. **EXTRA_TURNS** -- agent produces output after the golden ends (usually a transfer). Either extend the golden to cover the sub-agent response, or end the golden before the transfer.
8. **SIM_MAX_TURNS_EXCEEDED** -- conversation didn't converge in `max_turns`. Bump `max_turns` in the sim YAML (+4–6 for audio) or tighten the sim user's `response_guide`. NOT the same as TIMEOUT.
9. **SIM_USER_OFF_SCRIPT / SIM_TASK_INCOMPLETE** -- sim user diverged or gave up. Usually an eval-side fix to `response_guide` or `success_criteria`.
10. **TIMEOUT / SCORES_PASS_BUT_FAIL** -- platform issues, not agent bugs. Exclude from adjusted pass rate. Increase `max_turns` for golden timeouts; investigate tool latency.

**When multiple evals fail in the same category:** Fix the simplest one first. A quick win gives you a cleaner signal for diagnosing the harder failures.

**Foundation tests outrank application tests of equivalent severity.** Tool tests and callback tests test the building blocks (one tool, one callback). When they fail, every golden/sim that depends on them usually fails too. The `failure_clusters` priority_score already encodes this — foundation categories sit between EVAL_ERROR and TOOL_MISSING.

**The "default to fix the agent" doctrine applies strongly to goldens, weakly to sims.** Goldens encode known-good agent behavior — if a golden fails, the agent is usually wrong. Sims are probabilistic specifications driven by another LLM (the sim user) — a higher fraction of sim failures are eval-side issues (sim user persona, success_criteria, response_guide). For sims, walk *is this sim user behavior? sim config? agent?* before reaching for the agent code.

## The Iteration Loop

Initialize your `todo.md` with the following. Items start unchecked; check only after the step is verifiably done.

1. [ ] Verify prerequisites (above)
2. [ ] Lint (`agents/lint-fixer.md`) + `cxas push` agent code
3. [ ] Run evals: `python scripts/run-and-report.py --runs 5 --auto-revert --json-summary <path> --message "<describe change>" > <project>/eval-reports/last-run.log 2>&1`, then read `<path>`. If the script errors (non-zero exit, or `status: "errored"` in the summary), read `<project>/eval-reports/last-run.log` for the underlying stack — no re-run needed.
4. [ ] Read `failure_clusters` from the JSON summary. Pick the top 5 clusters by `priority_score` and dispatch `agents/triage-failure.md` once per cluster, in parallel. Aggregate the returned diagnoses (a cluster may return one shared diagnosis covering N evals, or split into per-eval diagnoses with `cluster_split: true`).
5. [ ] Plan fix from the aggregated diagnoses + `<project>/experiment_log.md`
6. [ ] Apply fix; back to step 2 until adjusted pass rate ≥ target

**Step 3 — JSON summary fields:** `status`, pass rate, `by_type`, `top_failures` (eval-level INDEX, kept for the iteration log), `failure_clusters` (the dispatch source — see step 4), `platform_errors`, `reverted`. Do NOT plan fixes from either `top_failures` or `failure_clusters` alone — both are selection inputs, not diagnoses.

**Step 4 — why clusters, not raw failures:** N evals failing TOOL_MISSING for the same tool share a single fix. Dispatching N subagents reads N transcripts to learn the same thing. Clustering by `(category, discriminator)` collapses duplicates so the 5-subagent budget covers more *distinct* failure modes per iteration. Subagents that detect false clustering (transcripts disagree) will return `cluster_split: true` with per-eval diagnoses — trust the split.

**Step 5 — you cannot plan a fix without diagnoses.** Skipping triage-failure and planning from `failure_clusters` is the #1 cause of fix-in-the-dark loops where the same eval fails iteration after iteration. The summary tells you WHAT failed; only triage-failure tells you WHY.

**Why step 2 every iteration:** `run-and-report.py` pushes goldens, NOT agent code. If you edit `cxas_app/` and skip `cxas push`, evals run against the old agent and your fix appears to have no effect.

**`--auto-revert`** reverts `cxas_app/` when **goldens** regress (with real agent failures, not platform issues), **tool tests** regress, or **callback tests** regress — provided sims didn't improve. Sims acting as a counter-signal: if sims improved while one of the other types regressed, the change probably helped real conversations and the broken test's expectation may be stale — investigate, don't revert. Sims alone don't trigger reverts (sim user is itself stochastic; sim drops can be noise).

**Write detailed `--message` values.** The message goes into the iteration report and `experiment_log.md`. Good: `"Change: Added trigger pattern for escalation in root_agent before_model_callback. Reason: golden_live_agent_request failing with TOOL_MISSING — LLM says text but forgets to call payload_update_tool. Expected fix: golden_live_agent_request, golden_profanity_escalation."` Bad: `"Fixed escalation"`.

## Fix Reference

Triage-failure (the sub-agent dispatched in step 5 of the loop) does per-eval transcript reading and returns diagnoses. The sections below are reference material for what to do once you have those diagnoses.

### Verifying the subagent's `suggested_fix_type`

The triage-failure subagent buckets each diagnosis into eval-config or agent-code fixes. When a diagnosis surprises you, use the symptom lists below to sanity-check the bucket before acting on it. Most of the time the subagent is right and you skip this; reach for it when the proposed fix feels off (e.g., `eval_edit` for what looks like an agent bug, or vice versa).

`eval_edit` should mean one of:
- Tool arg matching with `args` containing `$matchValue: ""` or exact values that vary
- Vague `response_guide` — sim user (driven by the `response_guide` text) goes off-script or refuses
- Missing or narrow `success_criteria` — sim step doesn't define what "done" looks like, so the agent can satisfy the goal without the eval recognizing it
- Stale golden — instruction changed, golden still expects old phrasing

`instruction_edit` / `tool_config` / `callback_edit` / `architectural` should mean one of:
- Hallucination — agent suggests steps not in tool output
- Instruction contradictions across agents (root vs. sub-agent)
- Wrong routing — `childAgents`, `transferRules`, child agent descriptions
- Guardrails blocking valid input — guardrails run before agent instructions
- Missing tool — instruction references a tool not in the agent's `tools[]` (the LLM silently improvises)

If the diagnosis matches neither list, that's a signal to re-dispatch with `confidence: low` flagged or to surface to the user. Don't apply a fix you can't sanity-check.

### Recommended Fix Approach

The core principle: **LLM detects, callbacks execute.** The LLM handles detection (hostility, frustration, transfer requests, intent classification). Callbacks handle execution that must be deterministic (tool calls with correct args, session termination).

When behavior is flaky:
1. **Check tool availability first** — if the instruction references a tool the agent can't access, the LLM silently improvises. Most common and hardest-to-diagnose issue.
2. **Fix the instruction** — make triggers clearer, remove conflicting constraints, add priority ordering. See `references/gecx-design-guide.md` → "Instruction Design Anti-Patterns".
3. **Use the trigger pattern** — for actions that must be deterministic (escalation, session termination), the instruction tells the LLM to set a state variable, the callback intercepts and executes. See `references/gecx-design-guide.md` → "Trigger pattern for deterministic tool calls".
4. **Use `after_model_callback`** — to guarantee text before `end_session`, or recover when the LLM forgets the state-setting call.

### Improvement Strategies by Issue Type

| Issue | Strategy |
|-------|----------|
| Wrong tool called | Improve tool descriptions, add examples showing correct tool choice |
| Missing tool call | Add explicit instruction: "When user asks X, always use tool Y" |
| Wrong parameters | Add parameter guidance in instructions |
| Bad response tone | Update globalInstruction with persona/tone guidance |
| Hallucination | Add grounding constraint + remove example phrases from instructions |
| Repeated empathy | Add: "Express empathy ONLY ONCE" with explicit prohibition |
| Too verbose | Add: "Keep responses to 2-3 short sentences maximum" |
| Wrong agent routing | Add deterministic transfer rules, improve child agent descriptions |
| Inconsistent behavior | Align sub-agent instructions with root agent; for actions that must be deterministic, use the trigger pattern (see Recommended Fix Approach above) instead of relying on prompt-only changes |
| Guardrail blocking valid input | Disable the offending guardrail (`enabled: false`), change its `action` from DENY to a softer mode, or modify its payload. Payload shape depends on which of the 5 guardrail types it is: `content_filter`, `llm_policy`, `llm_prompt_security`, `model_safety`, `code_callback` |

### Simulation Failures

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| "Task not completed" | `success_criteria` too narrow | Add "X counts as a successful outcome" |
| Sim user goes off-script | `response_guide` too vague | Be extremely directive: "You MUST cooperate fully" |
| Sim user refuses steps | `response_guide` doesn't instruct cooperation | Add "Follow ALL steps without objection" |
| Runs out of turns | Flow exceeds `max_turns` | Increase `max_turns` -- audio needs 4-6 extra |
| Tool not called | Expectation uses function name | Use behavioral description: "must call a tool to check outages" |

**When to convert a golden to a simulation:** If a golden fails >40% of runs despite correct agent behavior (verified by reading transcripts), the flow is inherently variable. Convert to a simulation -- see `references/run.md` -> "Choosing Golden vs Sim" for the decision criteria.

For component test failures (tool tests, callback tests) and audio-specific debugging gotchas, see `references/eval-templates.md` → "Component Test Failures" and "Audio-Specific Debugging Gotchas".

### Debugging Regressions

When a previously-passing eval starts failing after a change:

1. **Identify what changed** -- diff the current agent state against the last passing iteration:
   ```bash
   # Check the experiment log for the last known-good state
   cat <project>/experiment_log.md

   # Diff against the snapshot (run-and-report.py snapshots before each run)
   diff -r <project>/eval-reports/iterations/iteration-<N-1>/snapshot/ <project>/cxas_app/
   ```
2. **Check for tool/agent config drops** -- the second-most-common regression cause is an unintentional removal from a `tools[]` or `childAgents[]` array. Compare the current `<agent>.json` against the snapshot. If a previously-listed tool or sub-agent is gone, that's `tool_config` (or `architectural` for sub-agent removal) -- restore it. This often shows up as TOOL_MISSING failures on evals that worked the prior iteration.
3. **Check for instruction conflicts** -- the most common cause of regressions is a fix for eval A that contradicts constraints needed by eval B. Audit all instruction files together, looking for:
   - Conflicting priority between guidelines and taskflow steps
   - New constraints that block previously-working behaviors
   - Changes to routing logic that affect unrelated flows
4. **Check for callback side effects** -- if you modified a callback, verify it doesn't affect other flows:
   - `before_model_callback` changes affect every model call on that agent
   - `before_agent_callback` changes affect session initialization
   - State variable changes propagate to all downstream logic
5. **Decide: fix forward or revert**
   - If the regression is in a lower-priority eval and the fix improved higher-priority evals, fix forward (adjust the regressed eval)
   - If the regression affects core functionality, revert and try a different approach
   - `--auto-revert` handles this automatically when goldens (with real failures), tool tests, or callback tests regress. Sims act as a counter-signal — if sims improved while another type regressed, the auto-revert holds (mixed signal). Sims do NOT trigger a revert on their own (too noisy).

### Tuning Scoring Thresholds

When goldens fail despite correct agent behavior (verified by reading transcripts), the scoring may be too strict. Use `app-thresholds.py` to view and adjust:

```bash
python .agents/skills/cxas-agent-foundry/scripts/app-thresholds.py show
python .agents/skills/cxas-agent-foundry/scripts/app-thresholds.py set --similarity 2      # lower text matching (1-4)
python .agents/skills/cxas-agent-foundry/scripts/app-thresholds.py set --extra-tools allow  # allow extra tool calls
python .agents/skills/cxas-agent-foundry/scripts/app-thresholds.py set --hallucination disabled
```

**When to use each threshold:**
- **`--similarity`**: Lower (1-2) when the agent says the right thing but with different phrasing. Higher (3-4) when exact phrasing matters (compliance, legal disclaimers).
- **`--extra-tools allow`**: When the agent calls the right tools plus additional helpful ones (e.g., logging, state updates) that the golden doesn't expect.
- **`--hallucination disabled`**: When the hallucination scorer flags grounded information as hallucinated (false positives). Verify by reading the transcript first.

Tune thresholds after confirming the agent behavior is correct (read transcripts) -- not as a substitute for fixing the agent.

---

## Sub-agent and script recovery

| Source | Failure mode | Main thread's response |
|---|---|---|
| `triage-failure` | `confidence: low` | Re-dispatch with more context (additional transcripts), or surface to user: *"Couldn't confidently classify <eval>. Investigate manually?"* |
| `triage-failure` | `cluster_split: true` with multiple low-confidence per-eval diagnoses | The cluster discriminator was misleading. Re-dispatch each split eval as a singleton (own cluster of 1) so the subagent can focus its full reasoning budget on one transcript instead of comparing across them. |
| `coverage-analyst` | Status `stuck` (non-GECX app, malformed JSONs, missing `evals_dir`) | Read the `Reason` line. Re-dispatch with corrected path, or surface if app is genuinely non-GECX. |
| `coverage-analyst` | Status `incomplete` (some sections `_Not analyzed_`) | Report is still useful — note that platform-side sections need `app_name`. Re-dispatch with `app_name` if user wants them. |
| `run-and-report.py` | Non-zero exit, or `status: errored` in summary JSON | Read `<project>/eval-reports/last-run.log` for the stack trace (always written by step 3). Platform issue (auth, quota) → surface to user. Code issue → dispatch lint-fixer or triage-failure. |

For build-time sub-agent recovery (`tdd-writer`, `scaffolder`, `lint-fixer`, `eval-writer`), see `references/build.md` → "Sub-agent failure recovery".

## Common Mistakes

1. **Running evals without pushing first** — `run-and-report.py` pushes goldens, not agent code. After editing `cxas_app/`, you must `cxas push` or evals test the old version.
2. **Deleting evals during active runs** — causes ERROR state on the platform. Wait for the run to reach COMPLETED before modifying evals.
3. **Repeating a failed approach** — always check `experiment_log.md` before proposing a fix. If a similar approach was already tried and caused a regression, try a fundamentally different strategy.
4. **Overriding derived variables in eval `session_params`** — check the `before_agent_callback` source to see which variables are derived. Overriding them skips API calls and breaks downstream logic.
5. **Using agent transcripts as goldens** — tests that the agent does what it already does. Goldens should represent ideal PRD behavior.

