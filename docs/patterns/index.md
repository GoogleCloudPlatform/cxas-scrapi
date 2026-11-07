---
title: Patterns
description: Reusable architectural patterns for building robust conversational agents with CXAS SCRAPI — dynamic prompting, self-healing errors, and more.
---

# Patterns

Patterns are documented, reusable solutions to problems that come up repeatedly when building conversational agents on CX Agent Studio. Each pattern is grounded in production experience: a clearly defined problem, a concrete solution, and the trade-offs you should know before adopting it.

Unlike guides — which walk you through SCRAPI's mechanics — patterns focus on **agent architecture**. They answer the question "how should my agent be structured to handle X?" rather than "which API call do I make to do Y?"

---

## Available patterns

| Pattern | Problem | When to use |
|---|---|---|
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

- You've hit a specific failure mode (the LLM drifted off instruction, ignored an error, or mishandled state).
- You're starting a new agent that resembles a pattern's problem statement.
- You're reviewing an existing agent and want to understand why it behaves unpredictably.

---

## Patterns vs. guides vs. tutorials

| | Purpose | Audience |
|---|---|---|
| **Patterns** | Architectural solutions to recurring problems | Developers designing agent behavior |
| **Guides** | How to use SCRAPI's features step by step | Developers learning the tooling |
| **Tutorials** | Build a complete agent from scratch | Developers learning end-to-end |

See the [Tutorials](../tutorials/index.md) for concrete end-to-end walkthroughs.
