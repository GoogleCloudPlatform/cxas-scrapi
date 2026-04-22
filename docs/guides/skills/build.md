---
title: Build Skill
description: The cxas-agent-build skill — PRD interview, TDD, eval creation, and agent bootstrapping.
---

# Build Skill

The Build skill handles the initial creation of an agent from scratch. It guides you through a Product Requirements Document (PRD) interview, generates test cases using Test-Driven Development (TDD) principles, creates evaluation files, and then makes the necessary API calls to create all the resources on the platform.

By the time the Build skill finishes, you have a fully wired-up agent with a populated instruction, tools configured, and a first set of evaluations ready to run.

---

## Invoking the Build skill

The foundry routes you to Build when you express an intent like:

- "Build a new agent"
- "Add a new capability to the agent"
- "Create an agent that handles X"
- "Add a tool for Y"

The Build skill is a sub-skill of the [Agent Foundry](agent-foundry.md) — it is automatically routed to when the foundry detects a build intent.

---

## Phase 1: PRD interview

The Build skill starts with a structured interview to understand what you're building. It asks questions like:

**About the agent's purpose:**
- What problem does this agent solve?
- Who are the users? What's their context when they reach this agent?
- What are the 3-5 most important things the agent must be able to do?

**About the tools:**
- What data does the agent need to access? (This becomes tools)
- What systems will it connect to?
- What should happen when those systems are unavailable?

**About constraints:**
- Are there topics the agent should *never* discuss?
- What should the agent do at the end of a conversation?
- Are there regulatory or compliance constraints?

**About the conversation flow:**
- What does a successful conversation look like? (Walk me through an example)
- What are the most common ways conversations go wrong?

You can answer in natural language. The skill interprets your answers and synthesizes them into a structured design.

---

## Phase 2: TDD — defining test cases first

Before writing a single line of instruction, the Build skill generates test cases from the PRD. This is intentional — defining how you'll measure success before building helps you avoid building the wrong thing.

The skill creates:

**Tool tests** (what your tools should return):
```yaml
tests:
  - name: "order_found_shipped"
    tool: "lookup_order"
    input:
      order_id: "ORD-12345"
    assertions:
      - path: "$.status"
        operator: is_not_null
      - path: "$.estimated_delivery"
        operator: is_not_null
```

**Platform goldens** (key conversation paths):
```yaml
conversations:
  - conversation: "successful_order_lookup"
    tags: ["P0"]
    turns:
      - event: welcome
        agent: "Welcome to support"
      - user: "Check order ORD-12345"
        tool_calls:
          - action: lookup_order
        agent: "Your order has shipped"
```

**Local simulations** (goal-based scenarios):
```yaml
evals:
  - name: "complete_order_inquiry"
    steps:
      - goal: "Customer asks about order and gets the status"
        success_criteria: "Agent provided order status with delivery date"
        max_turns: 4
```

You review and adjust these test cases before the skill proceeds to build anything. The skill explicitly asks: "Do these test cases capture what you need? Are there edge cases we should add?"

---

## Phase 3: Creating resources on the platform

Once the test cases are approved, the skill creates all the resources:

1. **Creates the app** (if it doesn't exist) via `cxas create`
2. **Creates each agent** via the SCRAPI Python API
3. **Sets the root agent** on the app
4. **Creates each tool** via the SCRAPI Python API
5. **Associates tools with agents**
6. **Writes the instruction** file based on the PRD
7. **Creates eval files** in the appropriate directories

The skill uses the SCRAPI Python API or CLI commands for each step, reporting what it's doing and catching any errors.

---

## Phase 4: Initial push and lint

After creating resources:

1. Runs `cxas lint` on the new files
2. Fixes any lint errors automatically
3. Runs `cxas push` to sync everything to the platform
4. Runs `cxas pull` to ensure the local directory matches the platform state

---

## What the Build skill produces

By the end of a Build run, your project has:

```
cxas_app/<AppName>/
├── app.json
└── agents/
    └── <agent_name>/
        ├── instruction.txt    # Written from the PRD interview
        └── <agent_name>.json  # Configured with tools

evals/
├── goldens/<agent_name>_goldens.yaml    # From the TDD phase
├── tool_tests/<tool_name>_tests.yaml    # From the TDD phase
└── simulations/<agent_name>_sims.yaml  # From the TDD phase
```

The natural next step is to run the [Run skill](run.md) to see how many evaluations pass.

---

## Iterating with Build

Build isn't just for new agents. You can use it to add capabilities to existing agents:

```
You: I want to add a new capability to my agent

Build: I see you already have a "support-root" agent. What would you like to build?

You: Add a tool for checking account balances

Build: Let me understand the requirements. What information do you need to look up a balance?
       [continues with a focused PRD interview for just this capability]
```

The skill is context-aware — it reads the existing instruction and tool list before generating new tests, so it doesn't create duplicates or conflicts.
