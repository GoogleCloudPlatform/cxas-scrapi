# Technical Design Document (TDD)

> This is a **living document** -- update it whenever requirements, agent behavior, or evals change.
> Update the TDD first, then update evals to match.

## Agent Design

### Architecture
<!-- Root agent + sub-agents. What each one handles. -->
- **Root Agent:** Intent detection, authentication routing, escalation handling
- **Sub-Agents:** List each sub-agent and its scope (e.g., Troubleshooting Agent -- handles device/service diagnostics)

### Tools
<!-- Tool name, type (Python function / API connector / system), and purpose. -->
| Tool Name | Type | Purpose |
|-----------|------|---------|
| `set_session_state` | Python function | Writes to session state (used by trigger pattern) |
| `payload_update_tool` | Python function | Sends structured data to external systems |

### Routing Logic
<!-- How customers get routed -- auth status, issue type, flags, etc. -->
- Unauthenticated users -> limited self-service flow
- Authenticated users -> full support flow with sub-agent routing
- API failures -> deterministic escalation via callback

### Variables
<!-- Session variables, where they come from, naming conventions. -->
| Variable | Source | Notes |
|----------|--------|-------|
| `account_id` | Session parameter | Provided by telephony platform |
| `customer_id` | Session parameter | Provided by telephony platform |
| `auth_status` | Derived in before_agent_callback | NEVER override in evals |
| `user_role` | Derived in before_agent_callback | NEVER override in evals |
| `_action_trigger` | Set by LLM via state-setting tool | Read/cleared by before_model_callback |

### Callbacks
<!-- Which callbacks exist and what they do. -->
| Callback | Agent | Purpose |
|----------|-------|---------|
| `before_agent` | root_agent | Auth/profile variable derivation from identifiers |
| `before_model` | root_agent | Trigger pattern -- reads `_action_trigger`, returns deterministic tools |
| `after_model` | root_agent | Text injection before end_session |
| `before_model` | sub_agent | Same trigger pattern (callbacks on ALL agents) |

### Guardrails
<!-- Only add guardrails for requirements marked as CRITICAL in the PRD. Skip this section if none apply. -->
<!-- For each critical requirement, identify the guardrail type and action. -->

| Source Requirement | Guardrail Name | Type | Action | Scope | Notes |
|--------------------|---------------|------|--------|-------|-------|
| FR-5.1 (No PII leakage) (example) | `pii_leak_guard` | `llm_policy` | `DENY` | `AGENT_RESPONSE` | Blocks responses containing sensitive data in plain text (example) |

---

## Eval Design

### Coverage Map
<!-- For each PRD requirement, map to eval type + rationale. -->
| Requirement | Eval Type | Rationale | Priority | Severity | Tags |
|-------------|-----------|-----------|----------|----------|------|
| Auth routing | Golden | Callback-enforced, deterministic | P0 | NO-GO | `auth-routing, FR-1.1` |
| Escalation flow | Golden | Trigger pattern, deterministic | P0 | HIGH | `escalation, FR-2.1` |
| Troubleshooting | Sim | KB-dependent, steps vary | P1 | HIGH | `troubleshooting, FR-3.1` |
| No sensitive data leakage (example) | Guardrail | Critical requirement -- custom llm_policy guardrail (example) | P0 | NO-GO | `guardrail, FR-5.1` |

### Golden vs Sim Decision
<!-- Apply the key question: is the behavior deterministic for this flow? -->
- **Use goldens** when callbacks enforce the behavior, tool calls are predictable, routing is being tested
- **Use sims** when KB returns varying results, troubleshooting steps vary, behavioral goals are tested

### Test Data (Customer Profiles)
<!-- Mock customer profiles for session parameters. -->
| Profile | account_id | customer_id | Scenario |
|---------|------------|-------------|----------|
| Auth success | `9820598207` | `4444444` | Authenticated customer, active service |
| Auth failure | `0000000000` | `0000000` | Unknown account, triggers escalation |

---

## Build Steps

1. Create app + agents with instructions (root_agent, sub_agent)
2. Create tools + tool configurations
3. Define variables and session parameters
4. Implement callbacks (before_agent, before_model, after_model)
5. Create guardrails (only if TDD Guardrails section has entries)
6. Write golden YAML files
7. Write simulation YAML entries
8. Write tool test YAML files
9. Write callback test files (python_code.py + test.py)
10. Write guardrail test YAML files (only if custom guardrails were created in step 5)
11. Run initial eval suite
12. Hill-climb: fix failures, update TDD, re-run

---

## Pass Rate History

| Date | Goldens | Sims | Tool Tests | Callback Tests | Guardrail Tests | Notes |
|------|---------|------|------------|----------------|-----------------|-------|
| YYYY-MM-DD | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | Initial |

---

## Known Issues

- (none yet)

---

## Changelog

| Date | Change | Author |
|------|--------|--------|
| YYYY-MM-DD | Initial TDD created | -- |
