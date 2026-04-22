# GECX Project Template

A reference template encoding best practices from the cxas-agent-foundry skill for building GECX (Google Customer Experience) conversational agents.

## What This Template Demonstrates

This is a simple 2-agent customer support app (root + troubleshooting sub-agent) that shows the **patterns**, not a production-ready app. Every file contains comments explaining WHY things are done a certain way.

### Patterns by File

| File | Pattern |
|------|---------|
| `gecx-config.json` | Standard project configuration with placeholders |
| `tdd.md` | Living Technical Design Document structure -- update before building |
| `cxas_app/Sample_Support_Agent/app.json` | App config with variable declarations, naming conventions commentary |
| `cxas_app/Sample_Support_Agent/agents/root_agent/root_agent.json` | Agent config with tool/callback references matching CXAS export format |
| `cxas_app/Sample_Support_Agent/agents/root_agent/instruction.txt` | XML structure, priority-ordered steps, positive triggers, trigger pattern for escalation, "On the FIRST/SECOND" for unintelligible handling |
| `cxas_app/Sample_Support_Agent/agents/troubleshoot_agent/instruction.txt` | Scoped sub-agent with aligned tone, shared tools, same escalation pattern |
| `cxas_app/Sample_Support_Agent/agents/root_agent/before_agent_callbacks/` | Auth/profile derivation from identifiers, early-return, error handling, tool calls from callbacks |
| `cxas_app/Sample_Support_Agent/agents/root_agent/before_model_callbacks/` | Trigger pattern -- LLM detects, callback executes. Escalation map with state references |
| `cxas_app/Sample_Support_Agent/agents/root_agent/after_model_callbacks/` | Text injection before end_session, multi-model-call problem fix using `callback_context.events` |
| `cxas_app/Sample_Support_Agent/agents/troubleshoot_agent/before_model_callbacks/` | Callback gap fix -- trigger handling on ALL agents, not just root |
| `cxas_app/Sample_Support_Agent/tools/set_session_state/` | State-setting tool for the trigger pattern, with typed schema |
| `cxas_app/Sample_Support_Agent/tools/lookup_account/` | Sample API tool with auth check, structured errors, agent_action pattern |
| `evals/goldens/example_auth_routing.yaml` | Non-derived session params only, truncated at last deterministic turn, `$matchType` directives, tags |
| `evals/goldens/example_escalation.yaml` | Callback-driven escalation golden, trigger pattern tool calls |
| `evals/simulations/example_simulations.yaml` | Behavioral goals, tool expectations as descriptions, "MUST cooperate fully", appropriate max_turns |
| `evals/callback_tests/` | Mock injection pattern, pytest for before_model_callback, tests for trigger/no-op/text paths |

## How to Use

1. **Copy** this template to a new project folder
2. **Fill in** `gecx-config.json` with your GCP project details
3. **Write your TDD** using `tdd.md` as a skeleton -- fill in your agent design, eval plan, and build steps
4. **Get TDD approval** before building anything
5. **Adapt the app** -- modify instructions, callbacks, and tools for your use case
6. **Write evals first** (TDD approach) -- adapt the example YAMLs for your requirements
7. **Run evals** to validate, then hill-climb

## Key Principles

These are the core principles encoded in this template. See the reference docs for deeper detail.

- **Instructions tell the LLM WHAT (detection); callbacks enforce HOW (execution)** -- Keep detection generative and natural. Make execution deterministic and reliable.
- **Never override derived variables in evals** -- `auth_status` and `user_role` must be derived by callbacks, not hardcoded in session parameters.
- **Trigger pattern for deterministic tool calls** -- Instruction sets a state trigger, callback intercepts and returns correct tool calls.
- **Callbacks on ALL agents** -- The callback gap problem: sub-agent flows bypass root callbacks.
- **text_or_transcript() for audio safety** -- Handles both text and audio transcripts.
- **callback_context.events for multi-model-call turns** -- Prevents double-text injection.
- **Goldens for deterministic flows, sims for variable flows** -- If behavior inherently varies, use a sim.
- **Positive triggers only** -- No "NOT X" conditions in instruction triggers.
- **Simple natural language over programmatic logic** -- "On the FIRST... On the SECOND..." beats counter variables.

## Reference Documentation

For deeper guidance on each topic:

- **Instruction design, callback patterns, anti-patterns:** `.agents/skills/cxas-agent-foundry/references/gecx-design-guide.md`
- **Eval YAML formats and test patterns:** `.agents/skills/cxas-agent-foundry/references/eval-templates.md`
- **TDD structure, golden vs sim decisions:** `.agents/skills/cxas-agent-foundry/references/interview-guide.md`
