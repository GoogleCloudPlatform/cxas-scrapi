# Technical Design Document (TDD) Guide

The TDD (`<project>/tdd.md`) is the living source of truth for agent architecture and eval coverage. It's written once (from requirements or by reverse-engineering an existing agent), then kept in sync as the agent evolves.

## Table of Contents

- [When to Generate](#when-to-generate)
- [TDD Sections](#tdd-sections)
- [Generating from Requirements](#generating-from-requirements)
- [Generating from an Existing Agent](#generating-from-an-existing-agent)
- [Keeping the TDD Current](#keeping-the-tdd-current)

## When to Generate

| Situation | Approach |
|-----------|----------|
| Building a new agent from a PRD | Generate from requirements (interview or PRD) |
| Existing agent, no TDD | Reverse-engineer from agent code |
| Existing agent, has TDD | Verify it's up to date, then use as-is |

## TDD Sections

Every TDD must include these sections. See `assets/project-template/tdd.md` for the Markdown structure -- use it as a formatting reference only, never copy its example data.

### Agent Design

1. **Architecture** -- root agent + sub-agents, what each one handles, the agent hierarchy
2. **Tools** -- every tool with its name, type (Python function / API connector / system), and purpose
3. **Routing Logic** -- how users get routed (auth status, issue type, flags, transfer rules)
4. **Variables** -- every session variable, where it comes from (session param vs derived in callback), and whether evals should override it
5. **Callbacks** -- every callback across every agent, what it does, and which agents it's attached to

### Eval Design

6. **Coverage Map** -- for each distinct requirement or behavior, map to an eval type with rationale:

   | Requirement | Eval Type | Rationale | Priority | Severity | Tags |
   |-------------|-----------|-----------|----------|----------|------|

   Use these criteria to choose golden vs sim:
   - **Use goldens** when callbacks enforce the behavior, tool calls are predictable, and the agent path is deterministic
   - **Use sims** when KB returns varying results, troubleshooting steps vary, or behavioral goals (not exact responses) are being tested
   - See `references/interview-guide.md` -> "Golden vs Scenario Decision" for the full decision table

7. **Test Data** -- customer profiles for session parameters (which account IDs, customer IDs, and scenarios to use)

### Tracking

8. **Pass Rate History** -- table updated after each eval run
9. **Known Issues** -- outstanding problems
10. **Changelog** -- dated log of TDD changes

## Generating from Requirements

When you have a PRD or requirements doc (or have completed the interview in `references/interview-guide.md`):

**Recommended:** dispatch the `agents/tdd-writer.md` sub-agent with `output_path` and `sources` (a list of `{path, description}` for each artifact the user provided — PRD, sample conversations, customer profiles, reference agent TDDs, etc.; see the sub-agent's Inputs section for the full kind table). It auto-detects draft mode, performs the steps below, and returns the TDD content + an "Open questions" handoff. The main thread then runs step 5 (show the handoff to the user, ask for approval, re-dispatch tdd-writer if changes are requested). Use the inline steps only if you specifically need to do this in the main thread (e.g., the user is iterating on small TDD edits and a fresh sub-agent would lose conversational context).

1. Read the requirements thoroughly
2. Write the **Agent Design** sections by translating requirements into architecture, tools, routing, variables, and callbacks
3. Write the **Eval Design** sections. For each requirement in the PRD, specify:
   - **Eval type** -- golden or sim, with rationale (see criteria above)
   - **What it tests** -- the specific behavior being verified
   - **Priority and severity** -- P0/P1/P2, NO-GO/HIGH/MEDIUM/LOW
   - **Session parameters** -- which customer profile and what variables to provide
   - **For goldens** -- summary of the ideal conversation flow (user turns, expected agent actions)
   - **For sims** -- task description, max turns, success criteria
   - **Tool tests** -- which tools need isolated tests and what to assert on their outputs
   - **Callback tests** -- which callbacks need tests and what logic paths to cover
   - **Tags** -- for filtering (category, PRD ID, priority)
4. Include build steps: a numbered list of what will be created, in order (agents, tools, variables, callbacks, goldens, sims, tool tests, callback tests)
5. **Ask the user to review and approve before building anything** -- they may want to adjust architecture, add/remove evals, change priorities, or modify routing

## Generating from an Existing Agent

When you have an agent on the platform but no TDD (the "bootstrap" path):

**Recommended:** dispatch the `agents/tdd-writer.md` sub-agent with `app_dir` and `output_path` (optional `sources` of supplementary artifacts — PRDs, design docs, sample conversations — enrich "why"; optional `evals_dir` marks already-covered behaviors). The sub-agent auto-detects reverse-engineer mode, performs the steps below, and returns the TDD content + an "Open questions" handoff. The main thread shows the handoff to the user, asks for approval, and re-dispatches tdd-writer with change requests if needed — the approval loop lives on the main thread, not in the sub-agent. Use the inline steps only if you specifically need to do this in the main thread (e.g., the user is iterating on small TDD edits and a fresh sub-agent would lose conversational context).

1. **Read the agent code first** -- `app.json`, all agent config files, all instruction files, all callback Python files, and all tool Python files. Use `inspect-app.py --verbose` for a summary.

2. **Write each section from the real agent data** -- do NOT copy `assets/project-template/tdd.md`. The template contains example rows (like `set_session_state`, `account_id`) that will contaminate your output. Instead:
   - **Architecture**: list the actual root agent and sub-agents from `app.json` + agent configs, describe what each handles based on its instruction file
   - **Tools**: list every tool from the `tools/` directory with its real name and purpose (read the Python code to understand what it does)
   - **Routing Logic**: describe actual routing from instruction files, `childAgents` arrays, and `transferRules`
   - **Variables**: list every variable from `app.json` `variableDeclarations`, noting which are derived in `before_agent_callback` (mark these as "NEVER override in evals") vs provided as session params
   - **Callbacks**: list every callback from every agent, describing what each actually does (read the Python)
   - **Coverage Map**: for each distinct behavior you found in the instructions, decide golden vs sim

3. **Ask the user to review** before generating evals from the TDD.

## Keeping the TDD Current

The TDD is updated **before** evals are updated -- it's the spec that evals are derived from.

- When requirements change -> update the TDD first, then update evals to match
- When agent behavior changes -> update the TDD to reflect the new behavior
- After each eval iteration -> update the Pass Rate History table (the first row is the initial baseline; label the Changelog entry "Initial baseline")
- After fixing failures -> update the Changelog with what changed and why
