---
title: Debug Skill
description: The cxas-agent-debug skill — analyzing failures, fixing agents, and iterating to a target pass rate.
---

# Debug Skill

The Debug skill analyzes evaluation failures and fixes them. It reads the failure reports from the Run skill, identifies the root cause, proposes and applies changes, then re-runs the affected evals to verify the fix. It iterates until the agent reaches the target pass rate.

---

## Invoking the Debug skill

The foundry routes you to Debug when you express an intent like:

- "The evals are failing"
- "Fix the instruction"
- "The agent isn't calling the right tool"
- "Debug these failures"

The Debug skill is a sub-skill of the [Agent Foundry](agent-foundry.md) — it is automatically routed to when the foundry detects a debug intent.

If you've just run the Run skill and it found failures, the foundry often asks:

```
3 evals are failing. Would you like me to debug them?
```

---

## How the Debug skill triages failures

The skill reads the run report from `test-results/latest-report.json` and classifies each failure by root cause:

### Tool test failures

Tool test failures indicate a problem in the tool's Python code, not the instruction. The skill:

1. Reads the failing test case (input, assertion, actual output)
2. Reads the tool's Python code
3. Identifies the bug (missing error handling, wrong field name, etc.)
4. Proposes a fix to the Python code
5. Asks for your approval before applying

Example diagnosis:

```
Tool test "lookup_order/handles_api_timeout" FAILED
  Assertion: $.agent_action is_not_null
  Actual: null (the key is missing)

Root cause: The tool doesn't handle connection timeouts. When the external API
times out, the tool raises an exception that gets swallowed by the platform,
and the function returns None instead of a dict.

Proposed fix:
  Line 24: except requests.exceptions.Timeout:
               return {"agent_action": "The system is temporarily unavailable. Please try again."}

Apply this fix? [yes/no]
```

### Golden failures

Golden failures indicate a mismatch between expected and actual agent behavior. The skill:

1. Reads the failing conversation and turn
2. Reads the agent's instruction
3. Determines if the issue is in the instruction, a tool, or the golden itself
4. Proposes the appropriate fix

Example diagnosis:

```
Golden "order_management/bad_order_id_handling" turn 2 FAILED
  Expected: response contains "couldn't find"
  Actual: "I'll look that up for you"

Analysis: The agent called lookup_order even for an invalid order ID,
then relayed a confusing response. The instruction doesn't tell the
agent how to handle cases where lookup_order returns an agent_action.

Proposed fix to instruction.txt (in the order_lookup subtask):
  After: Call {@TOOL: lookup_order}
  Add: If the tool returns an agent_action, relay the message to the user and ask if they'd like to try a different order ID.

Apply this fix? [yes/no]
```

### Simulation failures

Simulation failures indicate the agent couldn't complete a conversational goal. The skill:

1. Reads the step goal and success criteria
2. Reviews the conversation transcript (stored in the run report)
3. Identifies where the conversation went off track
4. Proposes instruction changes to guide the agent better

---

## The iteration loop

After applying a fix, the Debug skill re-runs *only the evals that were failing*:

```
Applying fix to instruction.txt...
Running affected evals...

Re-running: order_management/bad_order_id_handling... PASS
Re-running: billing/account_balance_missing_id... PASS

Pass rate: 31/31 (100%)
Target reached. Done!
```

If the fix didn't fully work (some evals still fail), the skill analyzes the remaining failures and proposes additional fixes. It continues iterating until either:

- The target pass rate is reached
- The skill has exhausted its fixes (it tells you what it tried and asks for guidance)
- You tell it to stop

---

## Target pass rate

The default targets are:

| Eval type | Default target |
|-----------|---------------|
| Callback tests | 100% |
| Tool tests | 100% |
| Platform goldens | 80% |
| Simulations | 80% |

Goldens and simulations use 80% because some variation in LLM responses is expected and acceptable. Tool and callback tests are deterministic, so 100% is the right target.

You can override these:

```
Debug the failures — target 90% for goldens
```

```
Stop after the first successful pass — don't iterate further
```

---

## What the Debug skill will and won't change

**Will change:**
- `instruction.txt` — rewriting subtasks, triggers, and steps
- Tool `python_code.py` — adding error handling, fixing return values
- Callback `python_code.py` — fixing signature issues, adding null checks
- Agent JSON `tools` array — adding missing tools

**Will not change without asking:**
- Golden eval files (e.g., the expected response might be wrong)
- Session parameters in eval files
- `gecx-config.json`
- `cxaslint.yaml`

When the Debug skill suspects that a golden itself is wrong (e.g., the expected response is too strict), it will flag this:

```
Note: The golden expects "exact match" on the phrase "couldn't find", but
the agent's response ("I wasn't able to locate that order") is semantically
correct. Consider updating the golden to use $matchType: semantic, or
broadening the expected text.

Should I update the golden, or do you want the agent to use that exact phrasing?
```

---

## Asking for help

The Debug skill will tell you when it's stuck:

```
I've tried 3 fixes for "billing_inquiry/complete_flow" and the simulation
is still not completing the step "Customer provides account number".

The conversation transcript shows the agent is correctly asking for the
account number, but the simulated user isn't providing it in the right format.
This might be a prompt engineering issue with the simulation itself.

Options:
  1. I'll adjust the simulation's response_guide to help the simulated user
  2. You can review the conversation transcript: test-results/simulations-results.csv
  3. Mark this simulation as known-flaky and move on
```

The skill knows when to ask for human judgment rather than continuing to iterate blindly.
