---
title: Dynamic Prompting
description: Adapt your agent's instruction mid-conversation by swapping or appending to it from a before_model_callback — reduce context window size, change the LLM's role, and enforce phase-specific behavior.
---

# Dynamic Prompting

A single static instruction works well for simple agents. But when a conversation has meaningfully different phases — pre-authentication vs. authenticated, intake vs. resolution, collection vs. confirmation — keeping one long instruction covering all phases wastes context window tokens and makes it harder to enforce phase-specific behavior. Dynamic prompting solves this by updating the agent's instruction mid-conversation from a `before_model_callback`.

---

## What it is

Dynamic prompting is the practice of using a `before_model_callback` to modify the LLM's effective instruction based on conversation state. You can:

- **Swap** the instruction entirely (replacing a long intake instruction with a shorter resolution one after authentication completes).
- **Append** a phase-specific addendum to the base instruction (adding booking-specific rules after a user chooses a service type).
- **Inject** live data into the instruction (inserting the user's current account status so the LLM can reference it naturally).

---

## When to use it

Dynamic prompting is worth the added complexity when:

- **Phases are mutually exclusive.** The LLM behaves very differently in phase A vs. phase B, and the phase A rules would actively confuse the LLM in phase B.
- **Context window budget is tight.** The base instruction is already large, and phase-specific rules would push it over a sensible limit.
- **You want to prevent specific behaviors.** The LLM must not offer certain options before a prerequisite step is complete, and you want that enforced architecturally rather than via a "don't do X" instruction.

!!! note "Tool visibility first"
    If the goal is preventing the LLM from calling a specific tool at the wrong time, use tool visibility (hiding the tool via the callback) rather than dynamic prompting. Tool visibility is structurally enforced — the LLM cannot call a tool it cannot see. Dynamic prompting is softer and requires the LLM to comply with the changed instruction.

---

## How to update instructions mid-conversation

The `before_model_callback` receives the full `llm_request` object, which includes the agent's current instruction. You can modify it by writing to a state key that your instruction template reads, or by directly modifying the request object if the platform supports it.

The cleanest approach is to use a `_instructions_override` state key that your base instruction checks:

```python
def before_model_callback(callback_context, llm_request):
    state = callback_context.state

    # Phase 1: waiting for authentication
    if not state.get('auth_complete'):
        # No change needed — base instruction handles this phase
        return None

    # Phase 2: authenticated — swap to a shorter, focused instruction
    if state.get('auth_complete') == 'true':
        state['_instructions_override'] = """
<role>
You are a support specialist helping with account issues.
The customer has been verified — do not ask for their ID again.
</role>

<rules>
1. Address the customer by their first name: {customer_name}.
2. You may access account details, billing history, and service options.
3. Escalate to a human only if the customer asks explicitly.
</rules>

<system_directive>
{{system_message}}
</system_directive>
""".strip()

    return None
```

Your agent's base instruction then includes a conditional block that the platform resolves:

```xml
<role>
You are a customer support assistant.
</role>

<!-- The system will substitute this with _instructions_override
     when auth_complete is true. See your playbook configuration. -->
{{#if auth_complete}}
{{_instructions_override}}
{{else}}
<auth_protocol>
Before helping the customer, verify their identity.
Ask for their member ID number.
</auth_protocol>
{{/if}}
```

Alternatively, for simpler cases, append a state-driven addendum without replacing the full instruction:

```python
def before_model_callback(callback_context, llm_request):
    state = callback_context.state
    service_type = state.get('selected_service')

    if service_type == 'billing':
        state['_phase_rules'] = (
            "The customer selected billing support. "
            "You may offer refunds up to $50 without manager approval. "
            "For larger amounts, escalate."
        )
    elif service_type == 'technical':
        state['_phase_rules'] = (
            "The customer selected technical support. "
            "Walk through the standard troubleshooting checklist before escalating."
        )
    else:
        state.pop('_phase_rules', None)

    return None
```

Then in the instruction:

```xml
<phase_rules>
{{_phase_rules}}
</phase_rules>
```

---

## Code example: post-authentication instruction swap

This is the most common use case. A long instruction covering authentication steps should be replaced with a shorter one once authentication is complete, to avoid the LLM re-asking for credentials or second-guessing its authorization.

```python
INSTRUCTION_PRE_AUTH = """
<role>You are a support assistant. You must verify the customer's identity
before helping with any account-related requests.</role>

<auth_protocol>
1. Ask for the customer's member ID (format: ABC-123456).
2. Ask for the last 4 digits of their registered phone number.
3. Do not proceed until both are confirmed by the system.
</auth_protocol>

<system_directive>{{system_message}}</system_directive>
""".strip()

INSTRUCTION_POST_AUTH = """
<role>Support specialist — customer is verified.</role>

<rules>
1. Address the customer as {first_name}.
2. You may view and modify their account.
3. Offer the options the system presents via _system_message.
4. Do not ask for identification again.
</rules>

<system_directive>{{system_message}}</system_directive>
""".strip()


def before_model_callback(callback_context, llm_request):
    state = callback_context.state

    if state.get('auth_complete') == 'true':
        # Overwrite the instruction in state — the agent playbook
        # reads from this key after auth completes
        state['active_instruction'] = INSTRUCTION_POST_AUTH.format(
            first_name=state.get('customer_first_name', 'there')
        )
    else:
        state['active_instruction'] = INSTRUCTION_PRE_AUTH

    return None
```

!!! tip "Keep the post-auth instruction short"
    The pre-auth instruction carries all the authentication logic. Once authentication is done, that content is noise — it wastes tokens and can confuse the LLM into repeating auth steps. The post-auth instruction should be as short as possible: role, permissions, and tone only.

---

## Progressive disclosure of instructions in tool responses

A lighter-weight version of dynamic prompting is injecting guidance through tool responses rather than changing the instruction. This works well for one-off contextual hints without the overhead of full instruction management.

Return `_system_message` from any tool to surface guidance to the LLM on the next turn:

```python
def verify_member_id(member_id: str) -> dict:
    result = _lookup_member(member_id)
    if result['verified']:
        context.state['auth_complete'] = 'true'
        context.state['customer_first_name'] = result['first_name']
        return {
            'verified': True,
            '_system_message': (
                f"Identity confirmed. The customer's name is {result['first_name']}. "
                "Address them by first name and proceed with their request."
            ),
        }
    return {
        'verified': False,
        '_system_message': 'Member ID not found. Ask the customer to double-check it.',
    }
```

The LLM reads the `_system_message` field and incorporates it naturally without you needing to change the instruction at all. This is simpler than full instruction swapping and appropriate for short-lived contextual guidance.

---

## Tips for keeping context window minimal

- **One instruction per phase, not one instruction for all phases.** Don't add an `{{#if auth_complete}}` section to an already-long instruction. Replace the instruction.
- **Move business rules to tool docstrings where they belong.** Rules about how to format an argument ("convert to YYYY-MM-DD") belong in the docstring, not the instruction.
- **Use `_system_message` for per-turn guidance.** Anything that changes turn-by-turn should come through `_system_message`, not the instruction.
- **Remove instructions the framework already enforces.** If tool visibility prevents the LLM from calling a tool, you don't need an instruction saying "don't call X until Y."
- **Profile your instruction length.** Check how many tokens your instruction consumes. Anything over ~1,500 tokens is worth reviewing for redundancy.
