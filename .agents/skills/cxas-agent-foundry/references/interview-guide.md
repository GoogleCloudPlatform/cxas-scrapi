# Interview Guide

## Contents

- [Round 1: The Big Picture](#round-1-the-big-picture)
- [Round 2: Write the Technical Design Document (TDD)](#round-2-write-the-technical-design-document-tdd)
  - [Agent Design](#agent-design)
  - [Eval Design](#eval-design)
  - [Build Steps](#build-steps)
- [Keeping the TDD Current](#keeping-the-tdd-current)
- [Golden vs Scenario Decision](#golden-vs-scenario-decision)
- [Golden Design Principles](#golden-design-principles)

---

## Round 1: The Big Picture

1. **What does this agent do?** -- "customer support for billing issues", "booking assistant", etc.
2. **Modality** -- Voice/audio or text? This determines the model:
   - **Audio/voice**: `gemini-3.1-flash-live` (streaming, real-time voice)
   - **Text**: `gemini-3-flash` (text-only, lower latency)
3. **Requirements source** -- Ask for the PRD, spec doc, or requirements. Can be a file path, URL, or pasted text. If they don't have a formal doc, interview them to build one.
4. **Existing resources** -- Do they have sample conversations, mock data, customer profiles, or an existing agent to reference?

## Round 2: Write the Technical Design Document (TDD)

After gathering requirements, write a TDD to `tdd.md` in the project root. This is a **living document** -- it persists as the source of truth for the agent architecture and eval coverage. When requirements change later, the TDD is updated first, then evals are updated to match.

Ask the user to review and approve the TDD before building anything.

### Agent Design
1. **Agent architecture** -- root agent + sub-agents, what each one handles
2. **Tools needed** -- knowledge base, API connectors, session tools (with tool names and types)
3. **Routing logic** -- how customers get routed (auth status, issue type, etc.)
4. **Variables** -- what session variables are needed and where they come from
5. **Callbacks** -- before/after agent callbacks for setup logic (auth, profile lookup)

### Eval Design
For each requirement in the PRD:
1. **Eval type** -- golden or scenario (with rationale)
2. **What it tests** -- the specific behavior being verified
3. **Priority and severity** -- P0/P1/P2, NO-GO/HIGH/MEDIUM/LOW
4. **Session parameters** -- which customer profile, what variables
5. **For goldens** -- summary of the ideal conversation flow
6. **For scenarios** -- task description, max turns, LLM expectations
7. **Tool tests** -- which tools need isolated tests and what to assert
8. **Callback tests** -- which callbacks need tests and what logic paths to cover
9. **Tags** -- for filtering (category, PRD ID, priority)

### Build Steps
Numbered list of exactly what will be created, in order:
1. App + agents with instructions
2. Tools + tool configurations
3. Variables
4. Callbacks
5. Golden YAML files
6. Scenario YAML entries
7. Simulation YAML entries
8. Tool test YAML files
9. Callback test files (python_code.py + test.py)
10. Initial eval run

**Wait for user approval before proceeding.** The user may want to adjust the architecture, add/remove evals, change priorities, or modify the routing logic. Don't build anything until the TDD is approved.

## Keeping the TDD Current

Keep the TDD in sync with reality. When requirements, agent behavior, or evals change, update the TDD first, then update evals to match. Hooks remind you to update the TDD after pushing changes.

## Golden vs Scenario Decision

The key question: **is the agent's behavior deterministic for this flow?**

| Use Goldens When | Use Scenarios/Sims When |
|-----------------|------------------------|
| Agent flow is deterministic -- same input always produces same output | Agent uses a knowledge base that returns varying results per query |
| Tool calls are consistent and predictable | Troubleshooting steps vary (KB returns different steps each time) |
| Callbacks enforce the behavior (before_model, after_model) | Agent phrasing naturally varies due to LLM generation |
| Routing is the primary thing being tested | Behavioral goals are being tested (e.g., "escalates after 3 failures") |
| The conversation follows a fixed script | The conversation path depends on tool responses |

**Examples:**
- Auth API failure -> immediate escalation: **Golden** (callback-enforced, deterministic)
- Profanity -> escalation with message: **Golden** (instruction-driven but consistent trigger)
- Auth routing -> diagnostic check -> status response: **Golden** (callback generates response from template)
- Troubleshooting step-by-step with resolution checks: **Sim** (KB returns different steps)
- "Contact customer service" in tool response -> escalate: **Sim** (depends on KB returning specific phrase)

**Rule of thumb:** If you need to make a golden pass by making the agent MORE deterministic (via callbacks), that's the right approach. If the golden keeps failing because the agent's response inherently varies (KB-dependent), convert it to a sim.

## Golden Design Principles

See `references/eval-templates.md` -> Golden Design Rules for golden design principles and common pitfalls.
