---
title: Agent Architecture
description: When to use a single agent versus multiple agents, and how to structure both.
---

# Agent Architecture

The most common architecture mistake is adding agents prematurely. Multi-agent setups add real complexity — handoff logic, shared state, routing decisions — and that complexity has to be paid for in maintenance and debuggability. Start with a single agent and add agents only when the single-agent design demonstrates concrete limitations.

<figure class="diagram">
  <img src="../../assets/diagrams/agent-architecture.svg" alt="Agent Architecture Patterns">
</figure>

---

## Single-agent

A single agent handles the entire conversation. All tools, instructions, and state live in one place.

### When to use

- The conversation follows a linear or near-linear flow
- State is simple enough to track in a handful of variables
- The agent has fewer than two distinct capabilities (e.g., one primary task with simple fallbacks)
- You're starting a new project and the full complexity isn't yet known

### Trade-offs

| Benefit | Limitation |
|---------|-----------|
| Simple to reason about — one instruction set, one state space | Instructions grow long as capabilities increase |
| Easy to test — all behavior in one place | A single very long instruction set degrades model reliability |
| Fast to build and iterate | Can feel awkward if the agent needs radically different personas for different tasks |
| No handoff logic to maintain | All tools visible in every turn, including irrelevant ones |

### Workspace structure

```
customer_support/
├── gecx-config.json
├── cxas_app/
│   ├── app.json
│   ├── agents/
│   │   └── Support_Agent/
│   │       ├── Support_Agent.json
│   │       ├── instruction.txt
│   │       ├── before_agent_callbacks/
│   │       │   └── before_agent_callbacks_01/
│   │       │       └── python_code.py
│   │       └── before_model_callbacks/
│   │           └── before_model_callbacks_01/
│   │               └── python_code.py
│   ├── tools/
│   │   ├── lookup_order/
│   │   │   ├── lookup_order.json
│   │   │   └── python_function/
│   │   │       └── python_code.py
│   │   └── issue_refund/
│   │       ├── issue_refund.json
│   │       └── python_function/
│   │           └── python_code.py
│   └── evaluations/
│       └── Happy_Path_-_Linear_Flow/
│           └── Happy_Path_-_Linear_Flow.json
```

---

## Multi-agent

Multiple agents divide responsibility. One agent typically acts as the orchestrator (or "root agent"), routing the conversation to specialized sub-agents based on the user's intent.

### When to use

- Different parts of the conversation need genuinely different personas (e.g., a triage agent vs. a billing specialist)
- The full set of critical user journeys is too complex for one instruction set to handle reliably
- A single-agent prototype has been built and tested, and you've confirmed it degrades under the full scope
- You need strict capability isolation — sub-agents that cannot accidentally perform actions outside their domain

### Trade-offs

| Benefit | Limitation |
|---------|-----------|
| Each agent's instructions stay focused and short | Handoff logic is a new failure surface |
| Agents can have distinct personas and tool sets | State must be explicitly shared across agents |
| Easier to test sub-agents in isolation | Routing errors can cause confusing user experiences |
| Scales more gracefully to large capability sets | More files, more moving parts, harder to onboard |

### Workspace structure

```
my_agent/
├── gecx-config.json
├── cxas_app/
│   ├── app.json
│   ├── agents/
│   │   ├── Root_Agent/
│   │   │   ├── Root_Agent.json        # Orchestrator — routes to sub-agents
│   │   │   ├── instruction.txt
│   │   │   └── before_model_callbacks/
│   │   │       └── before_model_callbacks_01/
│   │   │           └── python_code.py
│   │   ├── Reservation_Agent/
│   │   │   ├── Reservation_Agent.json
│   │   │   └── instruction.txt
│   │   └── Cancellation_Agent/
│   │       ├── Cancellation_Agent.json
│   │       └── instruction.txt
│   ├── tools/
│   │   ├── check_availability/
│   │   │   ├── check_availability.json
│   │   │   └── python_function/
│   │   │       └── python_code.py
│   │   └── cancel_reservation/
│   │       ├── cancel_reservation.json
│   │       └── python_function/
│   │           └── python_code.py
│   └── evaluations/
│       └── Happy_Path/
│           └── Happy_Path.json
```

---

## How to decide

!!! info "Decision guide"
    Start with these questions:

    1. **Does this agent need more than one distinct persona?** If yes, lean toward multi-agent.
    2. **Does this agent have more than two substantially different capabilities?** If yes, single-agent instructions will grow long. Consider splitting.
    3. **Have you built a single-agent version?** If no, build one first. Real failure modes are easier to see in a working prototype than in a design document.
    4. **Is the single-agent version failing evals in ways that better instructions can't fix?** If yes, that's the signal to split.

The decision isn't permanent. A single-agent design can be refactored into multi-agent later. The reverse is harder. When in doubt, start simple.

---

## The "start simple" philosophy

Multi-agent architectures look appealing in design documents because they seem modular and clean. In practice, they introduce coordination problems that are hard to anticipate until you're debugging them in a failing eval.

The correct sequence is:

1. **Build a single-agent prototype.** Get it working against your core user journeys.
2. **Run evals.** Identify where it fails and why.
3. **Try to fix failures with better instructions, tools, or callbacks.** Most failures can be addressed this way.
4. **If you've exhausted those options**, and the root cause is genuinely that the instruction set is too large or the personas are too different, then split into multiple agents.

This sequence gives you a baseline to compare against. A multi-agent design that performs worse than the single-agent prototype is a signal that the problem was never architectural.

!!! warning "Avoid tool explosion"
    A common driver of premature multi-agent adoption is having too many tools. Before splitting into multiple agents, check whether you can reduce tool count instead. Tools with overlapping functionality, high-cardinality arguments, or poor naming are often better candidates for redesign than handoff. See [Tool Design](tool-design.md) for guidance.
