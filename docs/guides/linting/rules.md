---
title: Rule Reference
description: Complete reference for all CXAS linter rules, organized by category.
---

# Rule Reference

The CXAS linter has 50+ rules across 7 categories. This page documents every rule — what it checks, what triggers it, and how to fix it.

Severities shown are the defaults. You can override any rule's severity in `cxaslint.yaml`.

=== "I — Instructions"

    Rules in the `I` category check `instruction.txt` files for structural correctness, reference validity, and best-practice adherence.

    | ID | Name | Default | Description |
    |----|------|---------|-------------|
    | I001 | `required-xml-structure` | Error | Instruction must contain `<role>`, `<persona>`, and `<taskflow>` tags |
    | I002 | `taskflow-children` | Error | `<taskflow>` must contain `<subtask>` or `<step>` children |
    | I003 | `excessive-if-else` | Warning | 3 or more `IF/ELSE` blocks in an instruction |
    | I004 | `negative-triggers` | Warning | Negative conditions in `<trigger>` elements |
    | I005 | `conditional-logic-block` | Warning | `<conditional_logic>` block used for intent classification |
    | I006 | `hardcoded-data` | Warning | Hardcoded phone numbers, prices, or other dynamic data |
    | I007 | `instruction-too-long` | Info | Instruction exceeds word count threshold (default 3000 words) |
    | I008 | `invalid-agent-ref` | Error | `{@AGENT: Name}` references an agent that doesn't exist |
    | I009 | `invalid-tool-ref` | Error | `{@TOOL: Name}` references a tool not in the agent's config |
    | I010 | `wrong-agent-syntax` | Error | Wrong agent reference syntax (e.g., `${AGENT:...}` instead of `{@AGENT:...}`) |
    | I011 | `wrong-tool-syntax` | Error | Wrong tool reference syntax (e.g., `{TOOL:...}` instead of `{@TOOL:...}`) |
    | I012 | `unused-tool-in-config` | Warning | Tool is in the agent's JSON config but never referenced in the instruction |
    | I013 | `tool-not-in-config` | Error | Instruction references a tool that's not in the agent's JSON config |

    ---

    **I001 — required-xml-structure**

    Every instruction must have all three of `<role>`, `<persona>`, and `<taskflow>` tags. These sections organize the instruction into context (who the agent is) and behavior (what it does).

    *Triggers:* Any of the three tags is absent.

    *Fix:* Add the missing section. Minimum structure:
    ```xml
    <role>...</role>
    <persona>...</persona>
    <taskflow>
      <subtask name="...">
        <step>...</step>
      </subtask>
    </taskflow>
    ```

    ---

    **I002 — taskflow-children**

    A `<taskflow>` with no children is effectively an empty instruction — the LLM has no steps to follow.

    *Triggers:* `<taskflow>...</taskflow>` present but contains no `<subtask>` or `<step>` elements.

    *Fix:* Add at least one `<subtask name="...">` with `<step>` children.

    ---

    **I003 — excessive-if-else**

    Putting lots of `IF/ELSE` logic in instructions makes the LLM less reliable. LLMs are good at understanding intent, not at executing imperative logic trees. Move branching logic to callbacks.

    *Triggers:* 3 or more lines matching `IF ... ELSE` pattern (case-insensitive).

    *Fix:* Move deterministic branching into a `before_model_callback` or `after_model_callback`. Keep the instruction focused on goals, not conditional flows.

    ---

    **I004 — negative-triggers**

    Negative conditions in `<trigger>` elements confuse the LLM. "Trigger when NOT X" is harder for the model to reason about reliably than "Trigger when Y (the positive case)".

    *Triggers:* `<trigger>` element containing `NOT`, `is NOT`, or similar negations.

    *Fix:* Rewrite as a positive trigger. If you need to exclude a case, use a separate, earlier `<step>` that captures that case explicitly.

    ---

    **I005 — conditional-logic-block**

    `<conditional_logic>` blocks with priority-ordered conditionals are a pattern that confuses models because they require the model to evaluate conditions in order and pick the first match.

    *Triggers:* Presence of `<conditional_logic>` in the instruction.

    *Fix:* Use separate `<step>` elements with distinct triggers instead.

    ---

    **I006 — hardcoded-data**

    Phone numbers, prices, and other frequently-changing data in instructions create maintenance problems and risk the agent giving outdated information.

    *Triggers:* Patterns matching phone numbers or dollar amounts in non-template lines.

    *Fix:* Move dynamic data to tool responses. The agent should call a tool to get the current value, not read it from the instruction.

    *Configurable:* You can add custom patterns in `cxaslint.yaml` under `options.I006.patterns`.

    ---

    **I007 — instruction-too-long**

    Very long instructions tax the model's context window and can reduce reliability. Consider splitting into multiple sub-agents.

    *Triggers:* Word count exceeds the configured threshold (default: 3000 words).

    *Fix:* Split complex responsibilities into sub-agents. Each agent should have a focused, manageable instruction.

    *Configurable:* Set `options.I007.max_words` in `cxaslint.yaml`.

    ---

    **I008 — invalid-agent-ref**

    The `{@AGENT: Name}` syntax transfers control to another agent. If the referenced agent doesn't exist, the LLM can't transfer.

    *Triggers:* `{@AGENT: Name}` where `Name` doesn't match any agent in the app.

    *Fix:* Check the agent display name or resource name. The fix message lists available agents.

    ---

    **I009 — invalid-tool-ref**

    The `{@TOOL: Name}` syntax tells the LLM to call a tool. If the tool doesn't exist in the app's tool registry, it can't be called.

    *Triggers:* `{@TOOL: Name}` where `Name` isn't in the list of known tools.

    *Fix:* Verify the tool's display name. The fix message lists available tools.

    ---

    **I010 — wrong-agent-syntax**

    Common wrong forms for agent references that look almost right but won't be recognized by the platform.

    *Triggers:* `${AGENT:...}`, `{AGENT:...}` (without `@`), or `${@AGENT:...}`.

    *Fix:* Use `{@AGENT: Display Name}` (with `@` sign, space after colon).

    ---

    **I011 — wrong-tool-syntax**

    Similar to I010, but for tool references.

    *Triggers:* `${TOOL:...}`, `{TOOL:...}`, or `${@TOOL:...}`.

    *Fix:* Use `{@TOOL: Tool Name}`.

    ---

    **I012 — unused-tool-in-config**

    If a tool is in the agent's JSON but never referenced in the instruction, the LLM doesn't know it can call the tool — which makes having it in the config pointless.

    *Triggers:* Tool appears in `agent.json`'s `tools` array but has no `{@TOOL: ...}` reference in `instruction.txt`.

    *Fix:* Either add `{@TOOL: tool_name}` to the instruction (with appropriate context), or remove the tool from the agent's config.

    ---

    **I013 — tool-not-in-config**

    The instruction references a tool with `{@TOOL: ...}` that isn't in the agent's JSON config. The LLM will be told to call a tool that it doesn't have access to.

    *Triggers:* `{@TOOL: Name}` where `Name` is not in `agent.json`'s `tools` array.

    *Fix:* Add the tool to the agent's `tools` array, or remove the reference from the instruction.

=== "C — Callbacks"

    Rules in the `C` category check callback `python_code.py` files for naming conventions, correct signatures, and safe coding practices.

    | ID | Name | Default | Description |
    |----|------|---------|-------------|
    | C001 | `callback-fn-name` | Error | Callback function name must match callback type |
    | C002 | `callback-args` | Error | Callback must have correct argument count for its type |
    | C003 | `callback-camelcase` | Error | CES requires snake_case function names, not camelCase |
    | C004 | `callback-return-type` | Warning | Model callbacks should return `LlmResponse`, not `dict` |
    | C005 | `callback-hardcoded-phrases` | Warning | Hardcoded phrase lists for intent detection |
    | C006 | `callback-bare-except` | Warning | Bare `except:` without logging swallows errors silently |
    | C007 | `callback-tool-naming` | Info | Verify `tools.*` call uses correct naming convention |
    | C008 | `callback-missing-typing-import` | Error | Uses typing types without importing them |
    | C009 | `callback-signature` | Error | Callback function must have correct type annotations |
    | C010 | `callback-python-syntax` | Error | Callback Python file must have valid syntax |

    ---

    **C001 — callback-fn-name**

    Each callback type has a required entry function name. The platform looks for this specific function name when loading the callback.

    | Callback type | Required function name |
    |---------------|----------------------|
    | `before_model_callbacks` | `before_model_callback` |
    | `after_model_callbacks` | `after_model_callback` |
    | `before_agent_callbacks` | `before_agent_callback` |
    | `after_agent_callbacks` | `after_agent_callback` |
    | `before_tool_callbacks` | `before_tool_callback` |
    | `after_tool_callbacks` | `after_tool_callback` |

    *Fix:* Rename or add the entry function with the correct name.

    ---

    **C002 — callback-args**

    Each callback type has a required set of arguments. The platform passes specific objects; missing arguments cause runtime errors.

    | Callback type | Required arguments |
    |---------------|--------------------|
    | `before_model_callbacks` | `callback_context`, `llm_request` |
    | `after_model_callbacks` | `callback_context`, `llm_response` |
    | `before_agent_callbacks` | `callback_context` |
    | `after_agent_callbacks` | `callback_context` |
    | `before_tool_callbacks` | `tool`, `input`, `callback_context` |
    | `after_tool_callbacks` | `tool`, `input`, `callback_context`, `tool_response` |

    ---

    **C003 — callback-camelcase**

    CES requires all function names to be `snake_case`. Functions with camelCase names are silently ignored.

    *Triggers:* Any function definition using camelCase (e.g., `def injectContext():`).

    *Fix:* Rename to `inject_context`.

    ---

    **C004 — callback-return-type**

    Model callbacks (before/after model) should return `LlmResponse` or `None`, never a raw `dict`. Returning a dict causes unpredictable behavior.

    *Fix:* Use `return LlmResponse.from_parts(parts=[Part.from_text(text="...")])`.

    ---

    **C005 — callback-hardcoded-phrases**

    Hardcoded lists of phrases for intent detection in callbacks miss natural language variations. The LLM in the instruction is better at this.

    *Fix:* Keep intent detection in the instruction. Use callbacks for execution only.

    ---

    **C006 — callback-bare-except**

    A bare `except:` catches everything — including platform-internal exceptions that should propagate. Always catch specific exceptions or use `except Exception as e:` with logging.

    *Fix:* Replace `except:` with `except Exception as e: logging.error(...)`.

    ---

    **C008 — callback-missing-typing-import**

    Using `Optional`, `List`, `Dict`, etc. without `from typing import ...` causes `NameError` at push time, silently breaking the callback.

    *Fix:* Add `from typing import Optional, List, Dict` (or whichever types you use).

    ---

    **C009 — callback-signature**

    Type annotations must be exactly right — the platform validates signatures and silently drops callbacks with wrong types.

    *Fix:* Use the exact signatures defined in the platform documentation. Example for `before_model_callback`:
    ```python
    def before_model_callback(
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> Optional[LlmResponse]:
    ```

    ---

    **C010 — callback-python-syntax**

    Invalid Python syntax means the callback file can't be loaded. The platform silently fails to register callbacks with syntax errors.

    *Fix:* Fix the syntax error. Run `python -m py_compile python_code.py` locally to check.

=== "T — Tools"

    Rules in the `T` category check tool `python_code.py` files.

    | ID | Name | Default | Description |
    |----|------|---------|-------------|
    | T001 | `tool-error-pattern` | Error | Tool must return `agent_action` on error |
    | T002 | `tool-docstring` | Warning | Tool missing docstring |
    | T003 | `tool-type-hints` | Info | Tool function arguments lack type hints |
    | T004 | `tool-fn-name` | Warning | Tool function name should match tool directory name |
    | T005 | `tool-high-cardinality` | Info | High-cardinality input arguments |
    | T006 | `tool-return-explosion` | Info | Tool returning excessive data |
    | T007 | `tool-name-snake-case` | Error | Tool JSON name/displayName must be snake_case |
    | T008 | `tool-displayname-unreferenced` | Warning | Tool displayName not referenced by any agent |
    | T009 | `tool-kwargs-signature` | Error | Tool function uses `**kwargs` |
    | T010 | `tool-python-syntax` | Error | Tool Python file must have valid syntax |
    | T011 | `tool-none-default` | Error | Tool parameter uses `None` as default value |

    ---

    **T001 — tool-error-pattern**

    When a tool encounters an error, it should return a dict with an `agent_action` key. This gives the agent a message to relay to the user instead of hallucinating a response.

    *Triggers:* No `agent_action` string found anywhere in the tool file.

    *Fix:*
    ```python
    if error_condition:
        return {"agent_action": "I encountered an error looking up that information. Please try again."}
    ```

    ---

    **T002 — tool-docstring**

    CES uses the tool's docstring as its description for the LLM. Without a docstring, the LLM has to guess when to call the tool based on its name alone.

    *Fix:* Add a descriptive docstring explaining when and how the LLM should use the tool.

    ---

    **T004 — tool-fn-name**

    The tool function name should match the directory name for consistency. CES uses the function name for certain routing.

    ---

    **T005 — tool-high-cardinality**

    Arguments like `timestamp`, `latitude/longitude`, or internal `session_id`/`request_id` values are hard for users to express, especially in voice mode.

    *Fix:* Design arguments that a human can express naturally (e.g., `region`, `category`, `last_n_days`).

    ---

    **T007 — tool-name-snake-case**

    Tool names with spaces or mixed case cause issues with the platform's routing. All tool names and `displayName` values must be `snake_case`.

    *Fix:* Change `"displayName": "Look Up Order"` to `"displayName": "lookup_order"`.

    ---

    **T009 — tool-kwargs-signature**

    The platform silently drops tools that use `**kwargs` in their signature. Always use explicit named parameters.

    *Fix:* Replace `def my_tool(**kwargs):` with `def my_tool(param1: str = '', param2: str = '') -> dict:`.

    ---

    **T011 — tool-none-default**

    Parameters with `None` as a default value cause the platform to silently drop the tool during import. Use type-appropriate defaults instead.

    *Fix:* Change `def tool(name: str = None):` to `def tool(name: str = '') -> dict:`.

=== "E — Evals"

    Rules in the `E` category check golden and simulation YAML files.

    | ID | Name | Default | Description |
    |----|------|---------|-------------|
    | E001 | `eval-yaml-parse` | Error | Eval file must be valid YAML |
    | E002 | `eval-structure` | Error | Golden eval must have `conversations:` key |
    | E003 | `eval-tool-exists` | Warning | Tool calls in evals must reference existing tools |
    | E004 | `eval-session-param` | Warning | Session parameters should reference known variables |
    | E005 | `eval-duplicate-keys` | Error | Duplicate YAML keys (second overwrites first) |
    | E006 | `eval-no-mocks` | Warning | Golden with `tool_calls` but no `session_parameters` |
    | E007 | `eval-agent-not-string` | Error | Golden `agent` field must be a plain string, not a dict |
    | E008 | `eval-missing-agent` | Warning | Golden turn has `user` but no `agent` field |
    | E009 | `eval-sim-missing-tags` | Warning | Simulation eval missing `tags` field |
    | E010 | `eval-tool-test-wrong-key` | Error | Tool test uses `test_cases` instead of `tests` |
    | E011 | `eval-invalid-match-type` | Error | Invalid `$matchType` value in golden `tool_calls` |

    ---

    **E006 — eval-no-mocks**

    A golden that exercises tool calls without mock session parameters will make real API calls during testing — unpredictable and slow. Provide `common_session_parameters` to mock tool responses.

    ---

    **E008 — eval-missing-agent**

    Every turn with a `user` field must have a corresponding `agent` field. Without it, the platform reports "UNEXPECTED RESPONSE" for any agent response, causing false failures.

    ---

    **E010 — eval-tool-test-wrong-key**

    SCRAPI expects tool test YAML to use `tests:` as the top-level list key. Using `test_cases:` causes all tests to be silently skipped — SCRAPI returns 0 tests with no error.

    *Fix:* Rename `test_cases:` to `tests:`.

    ---

    **E011 — eval-invalid-match-type**

    Valid `$matchType` values are: `ignore`, `semantic`, `contains`, `regexp`. Common typos like `regex` (should be `regexp`) or `fuzzy` (should be `semantic`) silently fall back to exact matching.

=== "A — Config"

    Rules in the `A` category check `app.json` and agent JSON configuration files.

    | ID | Name | Default | Description |
    |----|------|---------|-------------|
    | A001 | `config-json-parse` | Error | Config file must be valid JSON |
    | A002 | `config-required-fields` | Error | Config must have required fields (`name`, `displayName`) |
    | A003 | `config-tool-exists` | Error | Agent config references a non-existent tool |
    | A004 | `config-missing-instruction` | Error | Agent directory must have an `instruction.txt` file |
    | A005 | `config-root-missing-end-session` | Error | Root agent must have `end_session` tool |

    ---

    **A003 — config-tool-exists**

    An agent JSON that lists a tool that doesn't exist in the app results in the agent having no access to that tool — silently, with no error from the platform.

    ---

    **A005 — config-root-missing-end-session**

    The root agent must have `end_session` in its `tools` array. Without it, the agent can never cleanly terminate a conversation.

=== "S — Structure"

    Rules in the `S` category perform cross-reference checks between multiple files.

    | ID | Name | Default | Description |
    |----|------|---------|-------------|
    | S002 | `agent-tool-references` | Error | Instruction references tools not in the agent's tool list |
    | S003 | `callback-file-references` | Error | Agent JSON references callback files that don't exist |
    | S004 | `child-agent-references` | Error | Agent JSON references child agents that don't exist |

    These rules are similar to I009 and I013 but operate at a higher level, cross-referencing the agent JSON config against the local file system.

=== "V — Schema"

    Rules in the `V` category validate resource configs against the CES protobuf schema. These catch structural issues that would cause API errors on push.

    | ID | Name | Default | Description |
    |----|------|---------|-------------|
    | V001 | `schema-app-valid` | Error | App config conforms to CES proto schema |
    | V002 | `schema-agent-valid` | Error | Agent config conforms to CES proto schema |
    | V003 | `schema-tool-valid` | Error | Tool config conforms to CES proto schema |
    | V004 | `schema-toolset-valid` | Error | Toolset config conforms to CES proto schema |
    | V005 | `schema-guardrail-valid` | Error | Guardrail config conforms to CES proto schema |
    | V006 | `schema-evaluation-valid` | Error | Evaluation config conforms to CES proto schema |
    | V007 | `schema-eval-expectation-valid` | Error | Evaluation expectation config conforms to CES proto schema |

    Schema validation catches issues like unknown fields, wrong types, and missing required fields — before you try to push and get a cryptic API error.
