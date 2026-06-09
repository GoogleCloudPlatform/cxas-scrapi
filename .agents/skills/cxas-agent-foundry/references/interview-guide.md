# Interview Guide

## Contents

- [Round 1: The Big Picture](#round-1-the-big-picture)
- [Round 2: Write the Technical Design Document (TDD)](#round-2-write-the-technical-design-document-tdd)
- [Golden vs Scenario Decision](#golden-vs-scenario-decision)
- [Golden Design Principles](#golden-design-principles)

---

## Round 1: The Big Picture

1. **What does this agent do?** -- "customer support for billing issues", "booking assistant", etc.
2. **Modality** -- Voice/audio or text? This determines the model:
   - **Audio/voice**: `gemini-3.1-flash-live` (streaming, real-time voice)
   - **Text**: `gemini-3-flash` (text-only, lower latency)
   - **If voice:** Ask about speaking rate requirements. The default tempo can feel slow for some use cases. Set `speakingRate` in the Console's voice config rather than relying on prompt instructions — natural language pacing guidance is unreliable (see `references/gecx-design-guide.md` → Speech Rate and Pacing).
3. **Requirements source** -- Ask for the PRD, spec doc, or requirements. Can be a file path, URL, or pasted text. If they don't have a formal doc, interview them to build one.
4. **Existing resources** -- Do they have sample conversations, mock data, customer profiles, or an existing agent to reference?
5. **Multilingual requirements** -- Does this agent need to support more than one language?
   - **Which languages?** (e.g., English + German, English + Spanish)
   - **Switching mode:** Should switching be **explicit-only** (user must say "speak German") or **auto-detected** (agent detects from utterance)? **Default to explicit-only** -- auto-detection is non-deterministic on `gemini-3.1-flash-live` and is not recommended for production.
   - **Datastore language:** Is the knowledge base / datastore in a different language than the agent instructions? If yes, the translate-around-tool-calls pattern is required (see `references/gecx-design-guide.md` → Multilingual Agents).
   - **Voice persona:** Is there a specific approved voice? Non-default voices have a known issue where additional languages revert to the default voice. Re-saving app voice settings in the Console fixes this (see `references/gecx-design-guide.md` → Voice / Audio).

## Round 2: Write the Technical Design Document (TDD)

After gathering requirements, write the TDD following `references/tdd-guide.md` -> "Generating from Requirements". The TDD covers agent architecture (agents, tools, routing, variables, callbacks) and eval design (coverage map, test data, build steps).

**Wait for user approval before proceeding.** The user may want to adjust the architecture, add/remove evals, change priorities, or modify the routing logic. Don't build anything until the TDD is approved.

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
