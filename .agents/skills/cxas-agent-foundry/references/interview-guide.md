# Interview Guide

## Contents

- [Round 1: The Big Picture](#round-1-the-big-picture)
- [Round 2: Write the Technical Design Document (TDD)](#round-2-write-the-technical-design-document-tdd)
  - [Agent Design](#agent-design)
  - [Guardrail Design](#guardrail-design)
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

### Guardrail Design

**Only create guardrails for requirements explicitly marked as critical in the PRD** (e.g., P0/NO-GO, "CRITICAL", "MUST NOT", compliance-mandated). Not every requirement needs a guardrail -- guardrails add latency and complexity. Reserve them for behaviors where failure has severe consequences (safety, compliance, data leakage, brand damage).

For each critical requirement, determine if a guardrail is needed and which type:

| Critical Requirement Pattern | Guardrail Type | Example |
|------------------------------|---------------|---------|
| Agent must never produce harmful/toxic content | `model_safety` | Block hate speech, dangerous content, sexually explicit, harassment |
| Agent must not leak PII or sensitive data | `llm_policy` | Custom policy: "Flag any response containing SSN, credit card, or account numbers in plain text" |
| Agent must resist prompt injection / jailbreaking | `llm_prompt_security` | Default prompt security settings |
| Agent must not discuss off-topic subjects | `llm_policy` | Custom policy: "Flag responses about topics outside of [domain]" |
| Agent must not hallucinate confirmations | `llm_policy` | Custom policy: "Flag responses that confirm an action was completed without a preceding tool call" |
| Specific words/phrases must be blocked | `content_filter` | Block competitor names, profanity, internal jargon |

For each guardrail, document in the TDD:

1. **Source requirement** -- which PRD requirement (with ID) drives this guardrail
2. **Guardrail type** -- one of: `model_safety`, `llm_policy`, `llm_prompt_security`, `content_filter`, `code_callback`
3. **Action on trigger** -- what happens when the guardrail fires: `DENY` (block + generic message), `generativeAnswer` (block + LLM-generated safe response), or `transferAgent` (block + hand off to human)
4. **Scope** (for `llm_policy`) -- `AGENT_RESPONSE` (check agent output) or `USER_INPUT` (check user input)
5. **Policy prompt** (for `llm_policy`) -- the validation prompt the guardrail LLM uses to evaluate content

**If no requirements in the PRD are marked as critical or no critical requirements map to guardrail-appropriate behaviors, skip this section entirely.** Default platform safety settings are always active -- only add explicit guardrails when the PRD demands protection beyond the defaults.

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
9. **Guardrail tests** -- if custom guardrails were created in the Guardrail Design section, define test cases for each guardrail (inputs that should trigger it, inputs that should pass through). Only include this if custom guardrails exist
10. **Tags** -- for filtering (category, PRD ID, priority)

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
