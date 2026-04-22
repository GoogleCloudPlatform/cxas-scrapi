---
title: Turn Evals
description: Single-turn assertions with TurnEvals and the TurnOperator enum.
---

# Turn Evals

Turn Evals are the most lightweight evaluation type in SCRAPI. They send a single message (or a short scripted sequence of messages) to your agent and assert on specific properties of the response — whether the agent called a particular tool, what it said, or whether it transferred control to a sub-agent.

Use Turn Evals when you need quick, targeted assertions on specific agent behaviors without the overhead of scripting a full conversation.

---

## When to use Turn Evals

Turn Evals are a good fit when:

- You want to verify that a specific input always triggers a specific tool call
- You're checking that the agent *doesn't* call tools in certain situations
- You want to verify agent transfer happens for the right inputs
- You need a fast check during development without waiting for platform goldens to run

They're less appropriate for multi-turn conversations or for checking the quality of natural language responses — use [Local Simulations](local-simulations.md) for those.

---

## The `TurnEvals` class

```python
from cxas_scrapi.evals.turn_evals import TurnEvals

turn_evals = TurnEvals(
    app_name="projects/my-project/locations/us/apps/my-app",
)
```

---

## The `TurnOperator` enum

`TurnOperator` defines the assertion types available for each turn:

| Operator | Description |
|----------|-------------|
| `CONTAINS` | Agent response contains the expected string |
| `EQUALS` | Agent response exactly equals the expected string |
| `TOOL_CALLED` | The specified tool was called during this turn |
| `TOOL_INPUT` | The specified tool was called with the expected input argument |
| `TOOL_OUTPUT` | The specified tool returned the expected output |
| `NO_TOOLS_CALLED` | No tools were called during this turn |
| `AGENT_TRANSFER` | The agent transferred to the specified sub-agent |

```python
from cxas_scrapi.evals.turn_evals import TurnOperator

# Available values
TurnOperator.CONTAINS
TurnOperator.EQUALS
TurnOperator.TOOL_CALLED
TurnOperator.TOOL_INPUT
TurnOperator.TOOL_OUTPUT
TurnOperator.NO_TOOLS_CALLED
TurnOperator.AGENT_TRANSFER
```

---

## Writing turn eval tests

### Defining tests in YAML

Turn eval tests are defined in YAML files. Each test specifies user input and expectations:

```yaml
conversations:
  - conversation: order_lookup_triggers_tool
    user: "What's the status of order ORD-12345?"
    variables:
      order_12345_status: "shipped"
    expectations:
      - type: tool_called
        value: "lookup_order"

  - conversation: welcome_message_check
    event: "welcome"
    expectations:
      - type: contains
        value: "Welcome"
      - type: no_tools_called

  - conversation: tool_receives_correct_order_id
    user: "Check order ORD-12345 please"
    expectations:
      - type: tool_input
        value:
          order_id: "ORD-12345"

  - conversation: billing_transfers_to_billing_agent
    user: "I want to dispute a charge on my bill"
    expectations:
      - type: agent_transfer
        value: "billing-agent"
```

### Multi-turn test cases

Turn Evals support short scripted sequences using the `turns` field:

```yaml
conversations:
  - conversation: order_id_collection_flow
    turns:
      - turn: ask_about_order
        user: "I want to check my order"
        expectations:
          - type: no_tools_called
          - type: contains
            value: "order ID"
      - turn: provide_order_id
        user: "It's ORD-12345"
        expectations:
          - type: tool_called
            value: "lookup_order"
```

### Running tests programmatically

```python
from cxas_scrapi.evals.turn_evals import TurnEvals

turn_evals = TurnEvals(app_name="projects/my-project/locations/us/apps/my-app")

# Load test cases from YAML
test_cases = turn_evals.load_turn_test_cases_from_file("evals/turn_evals/core_assertions.yaml")

# Run all tests — returns a pandas DataFrame
results_df = turn_evals.run_turn_tests(test_cases)
```

---

## Running from a YAML file

You can also define turn eval test cases in YAML:

```yaml
test_cases:
  - name: "order_lookup_triggers_tool"
    session_parameters:
      order_12345_status: "shipped"
    turns:
      - turn: user
        user: "What's the status of order ORD-12345?"
        expectations:
          - type: tool_called
            value: "lookup_order"

  - name: "welcome_has_no_tools"
    turns:
      - turn: event
        event: welcome
        expectations:
          - type: no_tools_called
```

You can also load from a directory to run all YAML files at once:

```python
test_cases = turn_evals.load_turn_tests_from_dir("evals/turn_evals/")
results_df = turn_evals.run_turn_tests(test_cases)
```

---

## Interpreting results

`run_turn_tests` returns a DataFrame with one row per expectation:

| Column | Description |
|--------|-------------|
| `test_name` | Name of the test case |
| `turn` | Which turn in a multi-turn test |
| `user` | The user input that was sent |
| `status` | `SUCCESS` or `FAILURE` |
| `errors` | Error details if the assertion failed |
| `expected` | What was expected |
| `actual` | What was observed |

```python
results_df = turn_evals.run_turn_tests(test_cases)

failed = results_df[results_df["status"] == "FAILURE"]
if not failed.empty:
    print("Failed assertions:")
    for _, row in failed.iterrows():
        print(f"  {row['test_name']}: {row['errors']}")
```

---

## Integration with the skills system

The Run skill includes Turn Evals as part of its combined reporting. When you run all four eval types, Turn Eval results appear in the combined report alongside tool tests, goldens, and simulations, making it easy to see the full picture at a glance.
