---
title: Platform Goldens
description: Write, push, and run deterministic turn-by-turn evaluation tests on the CX Agent Studio platform.
---

# Platform Goldens

Platform Goldens are the most thorough evaluation type SCRAPI provides. They run on the CX Agent Studio platform itself, exercising the full agent loop — model inference, tool calls, and callbacks — in a controlled, deterministic way. You script a conversation, specify expected responses and tool call expectations, and the platform tells you whether the agent behaved as intended.

---

## What goldens test

A golden is a scripted conversation. Each turn in the conversation has:

- A user input (what the human says)
- An expected agent response (what you want the agent to say)
- Optional: expected tool calls with expected arguments and responses

The platform runs the conversation through the live agent and compares the actual output to your expectations using configurable match types (semantic similarity, substring containment, exact match, or regex).

Goldens are best for testing *known, correct* agent behavior — happy paths, error handling paths, and any behavior that should be deterministic.

---

## YAML format

Golden files use the `conversations:` key at the top level:

```yaml
# common session parameters inject mock data for all conversations in this file
common_session_parameters:
  order_12345_status: "shipped"
  order_12345_eta: "2026-04-18"

conversations:
  - conversation: "happy_path_order_lookup"
    turns:
      - user: "Hi, I'd like to check on my order"
        agent: "Of course! Could you share your order ID?"

      - user: "It's ORD-12345"
        tool_calls:
          - action: lookup_order
            args:
              order_id:
                value: "ORD-12345"
                $matchType: contains
        agent: "Your order ORD-12345 has shipped and should arrive by April 18th."

  - conversation: "missing_order_id"
    turns:
      - user: "Where's my stuff?"
        agent: "I'd be happy to help track your order. Could you provide your order ID?"
```

### Required fields

| Field | Type | Description |
|-------|------|-------------|
| `conversations` | list | Array of conversation objects |
| `conversation` | string | Unique name for this conversation |
| `turns` | list | The ordered turns in the conversation |

### Turn fields

| Field | Type | Description |
|-------|------|-------------|
| `user` | string | The user's message for this turn |
| `agent` | string or list[string] | Expected agent response (required — omitting causes "UNEXPECTED RESPONSE" failures). Use a list when the agent may respond with multiple text chunks. |
| `tool_calls` | list | Expected tool invocations during this turn |
| `event` | string | Instead of `user`, inject a platform event (e.g., `"welcome"`) |
| `variables` | dict or list | Session state to stage immediately before this turn's user input. See [Per-turn variables](#per-turn-variables). |

### Tool call fields

| Field | Type | Description |
|-------|------|-------------|
| `action` | string | The tool display name |
| `args` | dict | Expected arguments |
| `output` | dict | Expected tool response (used to mock the tool's return value) |

### Match types (`$matchType`)

The `$matchType` field controls how argument and response values are compared:

| Value | Description |
|-------|-------------|
| `semantic` | Gemini-powered semantic similarity check (default for `agent` fields) |
| `contains` | The actual value must contain the expected value as a substring |
| `exact` | Exact string match |
| `regexp` | Regular expression match |
| `ignore` | Skip this field during comparison |

### Session parameters

`common_session_parameters` injects data into every conversation in the file. Use this to mock tool responses without making real API calls:

```yaml
common_session_parameters:
  # When the agent calls lookup_order, the platform returns these values
  order_12345_status: "shipped"
  order_12345_eta: "2026-04-18"
```

You can also set `session_parameters` per conversation to override the common values for specific test cases.

### Per-turn variables

Conversation-level parameters are staged once, before the first turn. A turn can
also declare its own `variables:` block, which is staged immediately before that
turn's user input and overrides any conversation-level value of the same name.

This matters for stateful multi-turn goldens replayed with
`--golden-run-method STABLE`: STABLE replays each turn in an isolated session
with no carried-over state, so any state a turn depends on has to be re-declared
on that turn.

```yaml
conversations:
  - conversation: "auth_then_lookup"
    turns:
      - user: "I want to check my account"
        variables:
          auth_status: "pending"
        agent: "Sure — could you give me your PIN?"

      - user: "1234"
        variables:
          auth_status: "success"   # re-declared: STABLE won't carry turn 1's state
        agent: "Thanks, you're verified. What would you like to look up?"
```

The proto-shaped list form produced by `cxas export` is also accepted:

```yaml
        variables:
          - name: "auth_status"
            value: "success"
```

### Tags

Tags let you filter which goldens to run in CI or report on:

```yaml
conversations:
  - conversation: "happy_path_order_lookup"
    tags: ["P0", "order_management"]
    turns:
      - ...
```

---

## Pushing goldens to the platform

Before you can run goldens, you need to push them to the platform. `--file` accepts either a single YAML file or a directory of YAML files:

```bash
# Push a single file
cxas push-eval \
  --app-name "projects/my-project/locations/us/apps/my-app" \
  --file evals/goldens/order_lookup.yaml

# Sync an entire directory and prune removed evaluations carrying a specific tag
cxas push-eval \
  --app-name "projects/my-project/locations/us/apps/my-app" \
  --file evals/goldens/ \
  --prune-tag regression
```

### `cxas push-eval` CLI Options

| Option | Default | Description |
|--------|---------|-------------|
| `--app-name` | *(required)* | CXAS app resource path (`projects/.../locations/.../apps/...`) |
| `--file` | *(required)* | Path to a YAML file or directory of YAML files containing evaluation definitions |
| `--prune` | `False` | Delete remote evaluations whose `display_name` is not present in the local YAML files |
| `--prune-tag` | `None` | Only prune remote evaluations that carry this tag and are absent from local YAML files (implies `--prune`) |
| `--dry-run` | `False` | Preview evaluation upsert and prune actions without mutating the server |

When syncing evaluations:
- Tool `displayName` references in `toolCall` / `toolResponse` expectations are automatically resolved to the target app's server-minted tool resource IDs.
- If the platform returns `501 Not Implemented` on `UpdateEvaluation`, SCRAPI automatically falls back to `delete` + `create`.

---

## Running goldens

Once pushed, run the evaluations and wait for results:

```bash
cxas run \
  --app-name "projects/my-project/locations/us/apps/my-app" \
  --wait
```

`--wait` polls until all evaluations complete and then prints a summary. Without `--wait`, the command starts the run and returns immediately — useful when you want to check results later.

### Filtering runs

You can filter which conversations to run using tags:

```bash
cxas run \
  --app-name "projects/my-project/locations/us/apps/my-app" \
  --tags P0 order_management \
  --runs 3 \
  --min-run-passes 2 \
  --min-pass-rate 0.90 \
  --golden-run-method NAIVE \
  --wait
```

### `cxas run` CLI Options

| Option | Default | Description |
|--------|---------|-------------|
| `--app-name` | *(required)* | CXAS app resource path (`projects/.../locations/.../apps/...`) |
| `--evaluation-id` | `None` | Specific evaluation resource name to run |
| `--display-name-prefix` | `None` | Run all evaluations whose display name starts with this prefix |
| `--tags` | `[]` | Space-separated list of tags; runs evaluations matching any tag |
| `--modality` | `text` | Execution modality (`text` or `audio`) |
| `--golden-run-method` | `STABLE` | Replay method for golden evaluations (`STABLE` or `NAIVE`) |
| `--runs` | `1` | Number of evaluation runs to execute for `k-of-N` reconciliation |
| `--min-run-passes` | `None` | Minimum passing runs per evaluation for `k-of-N` voting (defaults to `1` when `--runs > 1`) |
| `--min-pass-rate` | `None` | Minimum fraction of evaluations (`0.0`–`1.0`) that must pass; fails closed if `0` run |
| `--wait` | `False` | Wait for evaluation runs to complete and exit `0` on pass or `1` on fail |
| `--filter-auto-metrics` | `False` | Ignore automated semantic similarity/hallucination metrics and evaluate custom expectations only |

### Exit codes

| Exit code | Meaning |
|-----------|---------|
| 0 | All evaluations passed |
| 1 | One or more evaluations failed |
| 2 | SCRAPI error (invalid arguments, auth failure, etc.) |

These exit codes make `cxas run --wait` suitable for CI — your pipeline fails if any golden fails.

---

## Interpreting results

The output of `cxas run --wait` includes a summary table:

```
Evaluation Results
==================
Conversation             | Turns | Pass | Fail | Score
-------------------------|-------|------|------|------
happy_path_order_lookup  |   2   |   2  |   0  | 100%
missing_order_id         |   1   |   1  |   0  | 100%
bad_order_id_handling    |   2   |   1  |   1  |  50%

Total: 3 conversations, 5 turns, 4 pass, 1 fail
```

For each failing turn, the platform provides a detailed comparison showing what was expected versus what was actually produced.

### Common failure patterns

**"UNEXPECTED RESPONSE"**
: The turn has a `user` field but no `agent` field. The platform always expects an agent response — if you don't specify one, any response is flagged as unexpected. Fix: always add an `agent` field. Linter rule E008 catches this.

**Semantic match failures**
: The agent's response was correct in meaning but phrased differently than expected. Consider making the expected `agent` text less specific, or using `$matchType: contains` for key facts.

**Tool call argument mismatches**
: The agent called the tool with different arguments than expected. Check the instruction to ensure the agent is extracting the right parameters, and check if `$matchType: ignore` is appropriate for any arguments you don't care about.

---

## Full working example

Here's a complete golden file for an order management agent:

```yaml
common_session_parameters:
  order_12345_status: "shipped"
  order_12345_eta: "2026-04-18"
  order_99999_status: "not_found"

conversations:
  - conversation: "successful_order_lookup"
    tags: ["P0", "order_management"]
    turns:
      - event: welcome
        agent: "Welcome to Acme Support! How can I help you today?"

      - user: "I want to check my order status"
        agent: "Of course! Please share your order ID and I'll look that up for you."

      - user: "Order ID is ORD-12345"
        tool_calls:
          - action: lookup_order
            args:
              order_id:
                value: "ORD-12345"
                $matchType: contains
        agent: "shipped"

  - conversation: "order_not_found"
    tags: ["P0", "order_management", "error_handling"]
    turns:
      - user: "Check order ORD-99999"
        tool_calls:
          - action: lookup_order
            args:
              order_id:
                value: "ORD-99999"
                $matchType: exact
        agent: "I wasn't able to find that order"
```

```bash
# Push and run
cxas push-eval \
  --app-name "projects/my-project/locations/us/apps/my-app" \
  --file evals/goldens/order_management.yaml

cxas run --app-name "projects/my-project/locations/us/apps/my-app" --wait
```
