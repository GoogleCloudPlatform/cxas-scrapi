# Eval Templates & Patterns

YAML templates, code patterns, and detailed guidance for creating goldens, simulations, tool tests, and callback tests.

## Contents

- [Golden YAML Template](#golden-yaml-template)
- [Simulation YAML Template](#simulation-yaml-template)
- [Tool Tests](#tool-tests)
- [Callback Tests](#callback-tests)
- [Audio Evaluations](#audio-evaluations)
- [Conversational Design Principles](#conversational-design-principles)
- [Critical Eval Gotchas](#critical-eval-gotchas)
- [Customer Profile Management](#customer-profile-management)

---

## Golden YAML Template

**Prerequisite variables:** Goldens and sims need to include all session variables that the agent's callbacks read at startup -- missing variables cause the callback to crash with a KeyError and fall through to default behavior. Read the `before_agent_callback` source code to identify which variables it accesses from `callback_context.state`, and provide all of them in session parameters.

**Watch for case sensitivity** -- if the callback reads `callback_context.state["AccountID"]` (PascalCase), the session parameter must match that exact casing. A mismatch causes a silent KeyError.

Put shared prerequisites in `common_session_parameters` and profile-specific values in per-conversation `session_parameters`.

```yaml
common_session_parameters:
  # Include ALL variables the before_agent_callback reads from state
  # Check the callback source code to find these

conversations:
  - conversation: golden_eval_name
    session_parameters:
      account_id: "9820598207"
      customer_id: "4444444"
    turns:
      - user: "<event>welcome</event>"
        agent: "Hi, I am your virtual assistant. How can I help you today?"
      - user: "Customer's first message"
        agent: "Agent's response"
        tool_calls:
          - action: tool_display_name
            args: {key: value}
          - action: payload_update_tool
            args:
              summary:
                $matchType: "ignore"
                $matchValue: ""
                $originalValue: ""
              escalation_reason:
                $matchType: "semantic"
                $matchValue: "Expected reason"
                $originalValue: ""
      - user: "Goodbye"
        agent: "Thank you for calling. Have a great day!"
        tool_calls:
          - action: end_session
            args:
              session_escalated: false
    expectations:
      - "The agent must do X"
      - "The agent must NOT do Y"
    tags: [P0, HIGH, FR-1.1, auth-routing]
```

**Tool call parameter matching:** Use `$matchType` directives for flexible parameter matching. Supported types: `semantic` (fuzzy meaning match), `ignore` (skip check), `contains` (substring match), `regexp` (regex pattern). Parameters without `$matchType` use exact matching. See the Golden Design Rules below for details.

### Golden Design Rules

These rules are enforced by the linter (E007, E008) and cause automatic FAIL if violated:

1. **Every turn MUST have both a `user` and an `agent` field.** If a turn has `user` but no `agent`, the agent's response is flagged as "UNEXPECTED RESPONSE" and the golden automatically fails. If the first turn is a greeting (no real user input), use `user: "<event>welcome</event>"` to trigger the session start. The platform translates this to `<event>session start</event>` which the `before_model_callback` detects. Even if the agent's exact phrasing varies, include approximate text -- the platform uses semantic comparison.

2. **`agent` MUST be a plain string.** Never use `$matchType` dicts on the `agent` field -- `$matchType` is only valid inside `tool_calls.args`. The platform rejects dicts with a Pydantic validation error on push.

3. **End goldens before sub-agent transfers.** In multi-agent apps, when the root agent transfers to a sub-agent, the sub-agent's response creates turns the golden can't express (only one `agent` field per turn). This causes "UNEXPECTED RESPONSE" failures. End the golden at or before the turn that triggers the transfer.

4. **Callback-enforced responses use exact text.** Greeting, silence handling, and other callback-driven responses are deterministic -- use the exact text from the callback code. LLM-driven responses vary -- use approximate text and let the platform's semantic similarity scorer handle the comparison.

5. **Use `$matchType: "ignore"` for dates.** The LLM reformats dates unpredictably ("1948-07-12" vs "July 12, 1948"). Use `ignore` for date parameters in tool_calls.args.

---

## Simulation YAML Template

Local simulations use SCRAPI's Sessions API with Gemini as the sim user. They run locally in parallel (~1 min for the full suite), not on the platform.

```yaml
- name: eval_name
  tags: [P0, HIGH, category]
  steps:
    - goal: What the sim user should accomplish
      success_criteria: What counts as success
      response_guide: "How the sim user should behave -- include auth details the sim user should provide when asked"
      max_turns: 12
  expectations:
    - "What the agent should do"
    - "The agent must call a tool to check for outages in the customer's area."
    - "The agent must end the session and escalate after exhausting options."
  session_parameters: {account_id: "9820598207", customer_id: "4444444"}
```

**`tags` is required** -- the sim runner filters by `--priority P0`/`P1`/`P2` using the tags field. Sims without tags are invisible to priority filters and silently skipped.

**`success_criteria` defines what counts as success.** End every success_criteria with a clear statement of what satisfies it:
- Escalation: "Being transferred to a specialist counts as a successful outcome."
- Redirect: "Being redirected to the appropriate topic counts as a successful outcome."
- Troubleshooting: "Receiving troubleshooting guidance counts as a successful outcome."

**Sim expectations can verify tool calls** using natural language. The LLM judge evaluates expectations against the full conversation transcript (including tool calls). Phrase tool expectations as behavioral descriptions, not function names -- the judge sees resource IDs in the transcript, not display names:
- Good: "The agent must call a tool to check for network outages"
- Bad: "The agent must call diagnostic_lookup_tool" (judge can't match display names to resource IDs)

Include tool expectations alongside behavioral expectations to verify both WHAT the agent says and WHAT tools it calls.

---

## Tool Tests

Test individual tools in isolation -- faster and more precise than end-to-end conversation evals for catching tool-level regressions.

### CRITICAL: Read tool code before writing expectations

**Before writing any tool test**, you MUST read the tool's Python source code to understand the exact response structure. Tool test failures are most commonly caused by JSONPath expectations that don't match the actual keys returned by the tool.

1. Read the tool's `python_function/python_code.py` file
2. Find all `return` statements to understand the response dict structure
3. Use the exact keys from the return dict in your `$.result.<key>` paths
4. For error cases, check what key the error response uses (e.g., `$.result.agent_action` vs `$.result.error`)

**Example workflow:**
```python
# Tool code returns:
return {"member_name": "Dorothy", "active_plans": [...], "status": "success"}

# CORRECT test expectation:
expectations:
  response:
    - path: "$.result.member_name"   # matches actual key
      operator: is_not_null

# WRONG (common mistake -- guessing at keys):
expectations:
  response:
    - path: "$.result.authenticated"  # key doesn't exist in response!
      operator: is_not_null
```

### Auto-generating test templates

```python
from cxas_scrapi.evals.tool_evals import ToolEvals

tool_evals = ToolEvals(app_name=app_name)

# Auto-generate test templates from tool schemas
tool_evals.generate_tool_tests(
    target_dir="evals/tool_tests",
    mine_tool_data=True,        # Populate args from real conversation data
    mine_conversations_limit=50,
)
```

### YAML format

```yaml
tests:
  - name: diagnostic_lookup_test_1
    tool: diagnostic_lookup_tool
    args:
      account_id: "6666666"
      customer_id: "123456"
    variables: {auth_status: "authenticated"}
    expectations:
      response:
        - path: "$.result.status"
          operator: is_not_null
        - path: "$.result.issue_type"
          operator: contains
          value: "service"
```

**Common pitfalls:**
- The top-level key MUST be `tests:` -- using `test_cases:` causes SCRAPI to silently load 0 tests with no error.
- Each test case needs `tool:` (display name) -- don't use a top-level `tool_name:` key.
- **Session state:** Tool tests run in isolation with no session state. If a tool checks `context.state` (e.g., for auth), use `variables: {auth_status: "authenticated"}` to populate state. The `context` field also exists but `variables` is more reliable for state propagation.
- Response paths MUST start with `$.result.` -- tool responses are nested under `result`.

### Running tool tests

```python
test_cases = tool_evals.load_tool_tests_from_dir("evals/tool_tests")
results_df = tool_evals.run_tool_tests(test_cases, debug=True)
report_df = ToolEvals.generate_report(results_df)
```

### Operators

`equals`, `contains`, `greater_than`, `less_than`, `length_equals`, `length_greater_than`, `length_less_than`, `is_null`, `is_not_null`.

---

## Callback Tests

Test agent callbacks (before_agent, before_model, after_model, etc.) in isolation using pytest. Agent code and tests are separated for maintainability.

### Directory layout

```
evals/callback_tests/
|---- agents/                              # Callback source + symlinks for SCRAPI discovery
|   `---- <agent>/<callback_type>/<base>/
|       |---- python_code.py               # Copy of the callback source
|       `---- test.py                      # Symlink → ../../../../tests/.../test.py
`---- tests/                               # Pytest assertions (you author here)
    `---- <agent>/<callback_type>/<base>/test.py
```

`<callback_type>` is the snake_case JSON field name, e.g. `before_model_callbacks` (with the trailing `_callbacks`). `<base>` is the type with `_callbacks` stripped, e.g. `before_model`. If the agent has multiple callbacks of the same type, append `_<idx>` to the base (`before_model_0`, `before_model_1`).

**Critical:** SCRAPI's `test_all_callbacks_in_app_dir` globs `agents/<agent>/*_callbacks/<base>/test.py` and SILENTLY SKIPS any test whose `python_code.py` isn't in the same directory. The `agents/` symlink + same-dir `python_code.py` is non-optional; tests in `tests/` alone are unreachable. Use `scripts/sync-callbacks.py --from-local <agent_dir>` (pre-push) or `scripts/sync-callbacks.py` (post-push) to populate `agents/` and create the symlinks.

### Running

```python
from cxas_scrapi.evals.callback_evals import CallbackEvals

cb = CallbackEvals()
results_df = cb.test_all_callbacks_in_app_dir(app_dir="evals/callback_tests")
```

### Test pattern — mock injection

The mock pattern has three parts and the order matters. **Do NOT replace `sys.modules['python_code']` with a MagicMock — that swaps in a mock module and the function under test never runs.** Instead, add the python_code directory to `sys.path`, import the real module, attach mocks to its globals, then import the function under test.

```python
import sys
import os
from unittest.mock import MagicMock

# 1. sys.path.insert points at evals/callback_tests/agents/<agent>/<type>/<base>/
#    Path is relative to this test.py at evals/callback_tests/tests/<agent>/<type>/<base>/test.py.
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__),
    "..", "..", "..", "..", "agents", "<agent>",
    "<callback_type>", "<base>",
))

# 2. Import the real module and inject mocks for GECX-provided globals
#    (`tools`, `StatusError`, etc.) BEFORE importing the callback function.
import python_code  # noqa: E402
python_code.tools = MagicMock()
python_code.StatusError = Exception

# 3. Now import the function under test and any helpers.
from python_code import before_agent_callback  # noqa: E402
from cxas_scrapi.utils.callback_libs import CallbackContext  # noqa: E402


def test_returns_early_when_authenticated():
    ctx = CallbackContext(state={"auth_status": "authenticated"})
    result = before_agent_callback(ctx)
    assert result is None


def test_extracts_customer_id_from_datastore():
    python_code.tools.Read_Customer_Datastore_readDatastore.return_value = mock_resp
    ctx = CallbackContext(state={"auth_status": "unauthenticated"})
    before_agent_callback(ctx)
    assert ctx.state["customer_id"] == "999888"
```

For a complete worked example with multiple test classes per branch, see `assets/project-template/evals/callback_tests/tests/root_agent/before_model_callbacks/before_model/test.py`.

### Adding a new callback test

1. Write the test file at `tests/<agent>/<callback_type>/<base>/test.py`.
2. Make sure `agents/<agent>/<callback_type>/<base>/python_code.py` exists (copy from the local `cxas_app/<App>/agents/<agent>/<callback_type>/<dir>/python_code.py` if pre-push; pull from platform with `sync-callbacks.py` if post-push).
3. Run `python scripts/sync-callbacks.py --from-local <agent_dir>` (pre-push) or `python scripts/sync-callbacks.py` (post-push). It populates `agents/` and creates the `agents/.../test.py` symlink. Verify the symlink exists — without it, the runner won't discover the test.

### Update tests when callbacks change

When you modify a callback (add logic, change tool calls, add a new early-return path), untested changes silently break -- so follow up with:
1. Sync the updated code: `python scripts/sync-callbacks.py` (pulls from platform) or `--from-local <agent_dir>` (pre-push). This refreshes `agents/.../python_code.py` and re-creates any missing symlinks.
2. Add tests covering the new/changed behavior.
3. Run all callback tests to verify no regressions.

### What to test per callback type

- `before_agent`: Each early-return condition, tool calls made from the callback, correct text returned, variables set correctly
- `before_model`: Interception conditions (when to bypass LLM), LlmResponse structure, no-op path
- `after_model`: Text injection conditions, no-op when text present, edge cases (whitespace text, multiple tool calls in same response)
- When callbacks call tools directly via `tools.{name}(...)`: verify the tool is called with correct args, verify behavior when the tool call fails

---

## Audio Evaluations

Audio evals run through the full voice pipeline (TTS -> agent -> STT). Audio is configured at **run time**, not at eval creation time -- the same eval can run as text or audio.

### Key Differences from Text Evals

| Aspect | Text | Audio |
|--------|------|-------|
| Channel config | Default (omit) | `--channel audio` |
| Message format | `text` field | `transcript` field |
| Latency data | Minimal | Full span latencies (LLM, tool, callback) |
| Run time | Faster | Slower (TTS/STT overhead) |

### Writing Evals for Audio

- **Same YAML definitions** -- no changes needed to eval files for audio vs text
- **Increase `max_turns`** -- audio needs 4-6 extra turns due to TTS/STT overhead

### Audio-Specific Debugging Gotchas

- **`expect_tools` silently fails in audio mode.** Tool expectations in sim YAML (`expect_tools`) don't work for audio — the platform doesn't return tool call data in audio transcripts. Use `expect_criteria` (LLM-judged behavioral expectations) instead: `"The agent must check for outages"` rather than `expect_tools: ["check_outages_tool"]`.
- **Audio needs more turns.** Silence handling, filler acknowledgments, and chunked speech inflate turn count. Add 4-6 extra turns to `max_turns` to avoid false TIMEOUT failures.
- **Semantic similarity scores differently for spoken language.** Audio output is transcribed before scoring, so contractions and filler words can drag down similarity. If a golden shows TEXT_MISMATCH with `sem_score` 2.0-3.0 and the transcript looks correct when read, lower the similarity threshold for that eval rather than changing the agent.
- **Never run audio agents in text mode for evals.** The runner enforces this (fatal error on `--channel text` for audio apps). Audio agents have voice-specific instructions (pronunciation, cadence, filler handling) that produce unnatural text responses.

---

## Component Test Failures

Component tests (tool tests, callback tests) are deterministic — they should pass 100% of the time. Failures indicate bugs in code, not LLM variance.

**Tool test failures:**
1. Read the failing test's expected output and the actual output from the test results
2. Read the tool's source: `<project>/cxas_app/<AppName>/tools/<tool_name>/python_function/python_code.py`
3. Common causes:
   - Return dict keys don't match expectations (check exact key names)
   - Tool depends on external API that returned unexpected data
   - Tool uses `context` variables not set in the test's `session_params`
4. Fix the tool code or update the test's `session_params` to provide required context

**Callback test failures:**
1. Sync the latest callback code: `python scripts/sync-callbacks.py`
2. Read the failing test in `<project>/evals/callback_tests/agents/<agent>/`
3. Common causes:
   - Callback logic changed on the platform but tests weren't updated
   - Mock objects don't match the real `CallbackContext` shape
   - Callback imports something the test environment doesn't provide (`Part`, `Content`, `LlmResponse`, `LlmRequest`, `CallbackContext` are auto-provided globals on the platform)
4. Fix the test mocks or the callback code, then re-run with pytest

---

## Conversational Design Principles

Evals must test that the agent behaves like a natural human, not a form-fill bot.

**One question at a time (CRITICAL anti-pattern):**
The agent MUST collect information one piece per turn -- ask DOB, wait, ask ZIP, wait, ask ID. Never "What is your DOB, ZIP, and ID?" in one turn.

Goldens should model correct pacing:
```yaml
# CORRECT
turns:
  - user: "<event>welcome</event>"
    agent: "Hi, I am your virtual assistant. How can I help you today?"
  - user: "I need help with my account"
    agent: "I'd be happy to help. What's your date of birth?"
  - user: "July 12, 1948"
    agent: "And your ZIP code?"
  - user: "30033"
    agent: "Do you have your account ID?"
```

Simulation expectations should catch the anti-pattern:
```yaml
expectations:
  - "The agent asked for information one piece at a time, not all at once."
```

**Other principles:**
- Acknowledge before asking ("Thank you. And your ZIP code?")
- Offer alternatives naturally (SSN fallback if no account ID)
- Use verbal nods ("I understand", "Of course")
- Plain language (no jargon)

---

## Critical Eval Gotchas

### `max_turns` Sizing
- Quick tests (redirect, decline): 6 turns
- Standard troubleshooting: 12 turns
- Multi-step troubleshooting with resolution: 16-20 turns
- Audio: add 4-6 extra turns
- Set higher than needed -- eval ends when sim user's task is satisfied

### Eval API Quirks
- `ToolEvals.load_tool_tests_from_dir()` -- NOT `load_tool_tests_from_file()`
- Before writing evals: pull to local + lint + push fixes using full resource path -- NEVER create a new app
- Fixing goldens on the platform: DELETE and recreate -- PATCH cannot update golden structure reliably
- `invalid` flag on evaluations is `readOnly: true`. Auto-set when eval references deleted tool/agent. Delete and recreate to fix.
- Truncating goldens via `update_evaluation()` doesn't work -- the API merges turns, it doesn't replace them. To shorten a golden, delete and recreate.

---

## Customer Profile Management

Evals need mock customer profiles for session parameters. When creating evals:

1. Check if existing profiles (in a CSV or YAML) match the needed test scenario
2. If not, create a new profile with the right auth status, role, service status, and relevant attributes
3. Document the profile mapping so future evals can reuse them

Profile data typically includes: account ID, customer ID (e.g., phone number or member ID), auth status, user role, service status, and relevant line/subscription counts.
