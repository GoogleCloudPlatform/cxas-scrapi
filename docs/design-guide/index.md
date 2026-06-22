---
title: Design Guide
description: Practical principles for building reliable, maintainable agents on CX Agent Studio.
---

# Design Guide

LLMs are capable, but they have real limitations. Faithfulness errors cause agents to state things that aren't true. Instruction-following errors cause agents to skip steps, perform them out of order, or take actions that weren't requested. Tool-calling errors cause agents to invoke the wrong function or pass incorrect parameters. Critically, all of these problems get worse as instructions grow longer and more complex.

Building a reliable production agent means designing around these limitations deliberately — not hoping the model will figure it out.

!!! quote "The core philosophy"
    When we treat prompts as "vibes" or polite requests and let Gemini figure it out, we get inconsistent results. When we treat them as software — with explicit algorithms, inputs, outputs, and error handling — we achieve higher reliability.

This section documents the design practices that have proven effective for building agents on CX Agent Studio with SCRAPI.

---

## Best practice areas

| Area | Key principle | Page |
|------|--------------|-------|
| **Instruction Design** | Use XML tags, explicit taskflows, and actionable constraints | [Instruction Design](instruction-design.md) |
| **Agent Architecture** | Start single-agent; pivot to multi-agent only when needed | [Agent Architecture](agent-architecture.md) |
| **Tool Design** | Semantic names, descriptive docstrings, explicit parameters | [Tool Design](tool-design.md) |
| **Error Handling** | Return `agent_action` keys; validate early; catch exceptions | [Error Handling](error-handling.md) |
| **Variables** | JSON schemas over individual variables; mind type coercion | [Variables](variables.md) |
| **Callbacks** | Guard every `before_agent_callback`; use for dynamic prompting | [Callbacks](callbacks.md) |
| **Guardrails** | Layer prompt guard, blocklists, safety, rules, and callbacks for defense in depth | [Guardrails](guardrails.md) |

---

## Where to start

If you're designing a new agent from scratch, read these pages in order:

1. [Instruction Design](instruction-design.md) — every agent starts with instructions, and getting the structure right matters more than getting the content right on the first try.
2. [Agent Architecture](agent-architecture.md) — decide early whether a single agent covers your use case or whether you need multiple agents with handoffs.
3. [Tool Design](tool-design.md) — tools are where most production failures originate. Design them defensively.
4. [Error Handling](error-handling.md) — build recovery paths in from the start rather than adding them after failures surface in testing.
5. [Variables](variables.md) — understand how session state works before you write callbacks or tools that depend on it.
6. [Callbacks](callbacks.md) — dynamic prompting and slot-filling patterns that let you build more capable agents without inflating instruction length.
7. [Guardrails](guardrails.md) — layer safety boundaries, content filters, and behavioral rules to keep your agent on-task and compliant.

---

## The workspace structure

A well-organized agent project keeps configuration, code, tests, and documentation in predictable locations. The standard layout for a CXAS project is:

```
<project>/
├── gecx-config.json          # GCP project, app ID, modality
├── cxas_app/
│   ├── app.json              # App resource definition
│   ├── agents/
│   │   └── My_Agent/
│   │       ├── My_Agent.json               # Agent config
│   │       ├── instruction.txt             # Agent instruction
│   │       ├── before_agent_callbacks/
│   │       │   └── before_agent_callbacks_01/
│   │       │       └── python_code.py
│   │       └── before_model_callbacks/
│   │           └── before_model_callbacks_01/
│   │               └── python_code.py
│   ├── tools/
│   │   └── my_tool/
│   │       ├── my_tool.json                # Tool schema
│   │       └── python_function/
│   │           └── python_code.py
│   ├── guardrails/
│   │   └── My_Guardrail/
│   │       └── guardrail_config.json       # Guardrail config
│   ├── evaluations/          # Golden evals (each in its own named folder)
│   ├── evaluationDatasets/   # Shared eval datasets
│   └── evaluationExpectations/ # Reusable eval expectations
├── evals/                    # Local/draft eval files (pre-push)
├── eval-reports/             # HTML evaluation reports
└── experiment_log.md         # Iteration history and decisions
```

The `tdd.md` file is the most important file that isn't code. It documents what the agent is supposed to do, what the critical user journeys are, and what the design decisions were. Keep it updated as the agent evolves.

!!! tip "Version control your workspace"
    The entire project directory — including `tdd.md`, `evals/`, and `experiment_log.md` — should live in version control. The `eval-reports/` directory can be gitignored since reports are reproducible.

---

## Further reading

The design practices in this section are distilled from the **GECX Agent Design Guide** — the Google document that captures lessons learned from production agent deployments. It goes deeper on many of the topics covered here:

[GECX Agent Design Guide (Google Doc) →](https://docs.google.com/document/d/1ncyyYL6w-VLuj3xLzFGF5T_37b4wUzsFOfiBzxdBvpA/edit?resourcekey=0-6253kz_1pW1UFXCw8Ln_eQ&tab=t.0#heading=h.9d1mfetzj1ii)
