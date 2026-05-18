---
title: Tutorials
description: Step-by-step tutorials for building complete, production-ready CXAS agents with SCRAPI — from project setup through evaluations.
---

# Tutorials

Tutorials walk you through building complete agents from scratch. Unlike guides — which explain SCRAPI's individual features — tutorials give you a finished, working agent at the end, with real code, real evaluations, and the full context of why each decision was made.

---

## What you'll build

The current tutorial builds **Bella Notte** — a restaurant reservation agent for a fine-dining Italian restaurant. By the end, you'll have a complete agent that:

- Collects party size, preferred date, and other reservation details through natural conversation.
- Checks availability via a simulated API and presents time options.
- Handles multi-slot input ("table for 2 on Friday under Garcia") with all three setter tools firing in one turn.
- Books the reservation and returns a confirmation number.
- Recovers gracefully from invalid input (party size out of range, past dates, unavailable times).
- Has a full eval suite — 20+ golden evals and 5+ scenario evals — covering the happy path, error recovery, conversational detours, and multi-slot batching.

The agent implements the [Slot Filling Pattern](../patterns/slot-filling.md) end-to-end. Reading that pattern page before starting the tutorial is recommended but not required.

---

## Prerequisites

- Python 3.10 or later
- A Google Cloud project with CX Agent Studio enabled
- A CX Agent Studio app created in the UI (you'll get its app ID from `cxas apps list`)
- SCRAPI installed: `pip install cxas-scrapi`
- SCRAPI authenticated: `gcloud auth application-default login`

---

## Learning path

Work through the tutorial in order — each step builds on the previous one.

| Step | What you'll do |
|---|---|
| 1 — Project setup | Create `gecx-config.json`, understand the folder structure |
| 2 — Agent instruction | Write the XML-tagged instruction with the slot filling protocol |
| 3 — Slot DAG | Define the 7 slots and 2 tasks |
| 4 — Setter tools | Write the setter tools for each slot |
| 5 — The callback | Write `before_model_callback` with DAG evaluation and preemption |
| 6 — Variables | Declare the `sm` variable in your app |
| 7 — Evaluations | Write one golden eval and one scenario eval |
| 8 — Run and iterate | Run evals, read results, and fix failures |

[Start the tutorial →](restaurant-reservation.md)

---

## What you'll learn

After completing the tutorial, you'll understand:

- How the Slot Filling Pattern splits work between the LLM (language) and Python (state, control flow).
- Why tool docstrings must be short and what happens when they're verbose.
- How `before_model_callback` evaluates the DAG and preempts the LLM when a task fires.
- How golden evals check exact tool calls and scenario evals check overall behavior.
- The 7 stabilization gotchas — and how the tutorial's design avoids each one.

---

## After the tutorial

Once you've built Bella Notte, the natural next steps are:

- Read the full [Slot Filling Pattern](../patterns/slot-filling.md) for the complete set of advanced features: conditional slots, retry configuration, readback stall detection, and config validation.
- Explore [Dynamic Prompting](../patterns/dynamic-prompting.md) to see how to adapt the agent's instruction based on conversation phase.
- Add [Self-Healing Errors](../patterns/self-healing.md) to the booking tool so the agent has explicit recovery instructions for API failures.
