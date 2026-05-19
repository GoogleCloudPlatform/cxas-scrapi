---
title: Patterns
description: Reusable architectural patterns for building robust conversational agents with CXAS SCRAPI — slot filling, dynamic prompting, self-healing errors, and more.
---

# Patterns

Patterns are documented, reusable solutions to problems that come up repeatedly when building conversational agents on CX Agent Studio. Each pattern is grounded in production experience: a clearly defined problem, a concrete solution, and the trade-offs you should know before adopting it.

Unlike guides — which walk you through SCRAPI's mechanics — patterns focus on **agent architecture**. They answer the question "how should my agent be structured to handle X?" rather than "which API call do I make to do Y?"

---

## Available patterns

| Pattern | Problem | When to use |
|---|---|---|
| [Slot Filling](slot-filling.md) | Collecting multiple structured inputs from a user in a natural conversation — without the LLM forgetting state or deciding on its own when to fire backend tasks. | Any agent that gathers information before taking an action: bookings, orders, registrations, claims, intake forms. |
| [Dynamic Prompting](dynamic-prompting.md) | Adapting the agent's instruction mid-conversation based on what the user has done. | Agents with distinct phases (pre-auth vs. post-auth, intake vs. resolution) where the LLM's role meaningfully changes. |
| [Self-Healing Errors](self-healing.md) | Giving the LLM exact recovery instructions when a tool call fails, instead of relying on the model to improvise. | Any agent with tool calls that can fail in predictable ways — API timeouts, validation errors, missing permissions. |

---

## How to read these pages

Each pattern page is structured the same way:

1. **Problem** — what breaks if you don't use this pattern.
2. **Solution** — the approach, with an architecture diagram.
3. **Implementation** — the code, config, and wiring you need.
4. **Gotchas** — the non-obvious things that will bite you in production.
5. **Evaluation** — how to write tests that verify the pattern is working.

---

## When to use patterns

Patterns are not mandatory — you can build effective agents without them. But
they become valuable when:

- You've hit a specific failure mode (the LLM forgot a slot, called a task too early, ignored an error).
- You're starting a new agent that resembles a pattern's problem statement.
- You're reviewing an existing agent and want to understand why it behaves unpredictably.

### Slot Filling — the default for structured flows

**Start with the [Slot Filling](slot-filling.md) pattern** whenever your agent
collects structured information before taking an action. This includes:

- **Bookings and scheduling** — appointments, reservations, service calls
- **Payments and billing** — payment method, amount, date, confirmation
- **Registrations and intake** — user onboarding, claims, applications
- **Sensitive operations** — account changes, identity verification, medical intake
- **Any flow requiring determinism** — where tasks must fire at exactly the right time, inputs must be validated before acting, and the LLM must not hallucinate or skip steps

The slot filling pattern is **strongly preferred over XML `<taskflow>` tags**
for these use cases. XML taskflows rely on LLM memory for state tracking, which
degrades in long conversations and cannot guarantee correct task firing order.
The slot filling pattern keeps state in Python (`context.state`), making it
deterministic, auditable, and testable.

If you're new to SCRAPI, start with the [Slot Filling](slot-filling.md) pattern
and the [Restaurant Reservation Tutorial](../tutorials/restaurant-reservation.md).
The [`examples/bella_notte/`](../examples/bella_notte/) directory contains a
complete reference implementation.

---

## Patterns vs. guides vs. tutorials

| | Purpose | Audience |
|---|---|---|
| **Patterns** | Architectural solutions to recurring problems | Developers designing agent behavior |
| **Guides** | How to use SCRAPI's features step by step | Developers learning the tooling |
| **Tutorials** | Build a complete agent from scratch | Developers learning end-to-end |

The [Bella Notte restaurant reservation tutorial](../tutorials/restaurant-reservation.md) is the concrete implementation of the Slot Filling pattern. Reading the pattern first and then working through the tutorial is the recommended path.
