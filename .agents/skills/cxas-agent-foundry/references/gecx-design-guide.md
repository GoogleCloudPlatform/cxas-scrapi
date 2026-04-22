# GECX Agent Design Guide

## Contents

- [Workspace and Folder Structure](#workspace-and-folder-structure)
  - [Key Conventions](#key-conventions)
- [Background](#background)
- [Summary of Best Practices](#summary-of-best-practices)
  - [Instructions](#instructions)
  - [Architecture](#architecture)
  - [Variables and state management](#variables-and-state-management)
  - [Tool design](#tool-design)
  - [Error handling](#error-handling)
  - [Advanced patterns](#advanced-patterns)
- [Instructions Format](#instructions-format)
  - [Role](#role)
  - [Persona](#persona)
  - [Ambiguity](#ambiguity)
- [Agent Architecture](#agent-architecture)
  - [Single-Agent](#single-agent)
  - [Multi-Agent](#multi-agent)
  - [Using the multi-agent framework](#using-the-multi-agent-framework)
- [Variables](#variables)
- [Tool Design Guidelines](#tool-design-guidelines)
  - [Common Tooling Pitfalls](#common-tooling-pitfalls)
  - [Tool wrappers](#tool-wrappers)
- [Error Handling Guidelines](#error-handling-guidelines)
  - [Common Error Handling Pitfalls](#common-error-handling-pitfalls)
- [Callback Patterns for Deterministic Behavior](#callback-patterns-for-deterministic-behavior)
- [Instruction Design Anti-Patterns](#instruction-design-anti-patterns)
- [Source Control](#source-control)
- [Advanced techniques](#advanced-techniques)
  - [Dynamic Prompting](#dynamic-prompting)
  - [Instructions in a tool response](#instructions-in-a-tool-response)

## Workspace and Folder Structure

Agent development uses a **hybrid approach** -- local files in git for version control, with SCRAPI for running evals and platform operations. The core principle: **create and edit locally, push to platform**.

Each agent is managed within a dedicated `<project>` workspace folder containing:
- **`gecx-config.json`** -- Centralized config (project ID, app ID, location, modality).
- **`cxas_app/`** -- Local agent code (instructions, callbacks, tools). The canonical source for agent definitions.
- **`tdd.md`** -- Technical Design Document (the source of truth for architecture).
- **`evals/`** -- Test definitions:
  - `goldens/*.yaml` (Platform ideal conversations)
  - `simulations/simulations.yaml` (Local sim eval definitions)
  - `tool_tests/*.yaml` (Isolated tool test cases)
  - `callback_tests/` (Callback code + assertions)
- **`eval-reports/`** -- HTML reports generated after running evals, including historical snapshots in `iterations/`.
- **`experiment_log.md`** -- Tracks iterations, what was tried, and pass rate progression over time.

### Key Conventions
- **TDD is the source of truth.** The TDD defines architecture and eval coverage. Evals follow the TDD, not the agent's current behavior. Update the TDD first.
- **Session variables:** Only override what the agent's `before_agent_callback` can't derive. Never override `auth_status` or `user_role` directly if the callback relies on APIs to set them.
- **Fix the agent first.** When evals fail, assume the agent is wrong. Only modify evals as a last resort.
- **YAML formatting:** Hand-write YAML instead of using `yaml.dump()` to avoid reformatting.

## Background
LLMs today are highly capable and serve as fundamental building blocks for agent building. Providing the right context will enable them to solve complex problems. By orchestrating multiple LLM calls together as autonomous agents, developers can automate human-level reasoning tasks.

However, building an enterprise-grade agentic system is not a simple task given the following model limitations that we see today:
- **Faithfulness**: The agent may hallucinate
- **Instruction Following**: The agent may not always follow the instructions; specifically:
  - It may skip instruction steps
  - It may follow the steps in the wrong order
  - It may follow the wrong instructions
  - It may only do parts of the tasks you instructed
- **Tool calling**: The agent might not call tools correctly:
  - It may not call a tool when it is required to
  - It may call the right tool, but with the wrong parameters
  - It may call the right tool, but with extra, unneeded parameters

These problems get magnified as the instructions grow bigger, or as conversation goes longer. Because of this, we generally advocate for a multi-agent architecture that uses tools to maintain state and dynamically inject instructions during the flow of the conversation.

While writing clear and unambiguous instructions is the most critical component to mitigating these factors as much as possible, there are also other techniques that have been proven to work. This guide synthesizes some of the practices that have worked for agents in production.

When we treat prompts as "vibes" or polite requests and let Gemini figure it out, we will get inconsistent results. When we treat them as software, with explicit well defined algorithms, inputs, outputs, and error handling, we achieve higher reliability and consistency.

## Summary of Best Practices

### Instructions
- **XML Formatting:** Use structured tags (e.g., `<role>`, `<step>`) to improve instruction following and model parsing.
- **Unambiguous Instructions:** Be clear and concise; ambiguity is the enemy of execution.

### Architecture
- **Start Simple:** Begin with a single-agent architecture for prototypes and linear flows to maintain lower latency and speed; then pivot to a multi-agent architecture once you introduce 2+ capabilities
- **Modular Design:** Build reusable sub-agents (e.g., an "Authentication" agent) and isolate them to specific use cases to minimize lossy handoffs.
- **Determinism:** Find opportunities to offload logic from instructions to callbacks and tool calls.
- **Agents as Code:** Utilize standard versioning and peer review processes.
- **Test Driven Development:** Create evals even before you create your agent to guide the efficacy of your prompts.

### Variables and state management
- **JSON Schemas:** Use structured schemas rather than a long list of individual variables to prevent "variable explosion" and context degradation.
- **Semantic Naming:** Name variables descriptively so the model understands their contextual importance.

### Tool design
- **Tool Wrappers:** Favor tool wrappers over sequentially calling tools in instructions and high cardinality OpenAPI tools to mitigate latency and cascading failures.
- **Descriptive Docstrings:** Treat tool descriptions as core instructions for accurate invocation routing.

### Error handling
- **Early Validation:** Verify mandatory inputs and prerequisites before calling external services.
- **Actionable Recovery:** Return an agent_action key in failures to provide the model with deterministic recovery steps.

### Advanced patterns
- **Dynamic Prompting:** Update instructions via callbacks to minimize active context
- **Progressive Disclosure:** Embed instructions in tool responses to trigger rules only when relevant.

## Instructions Format
While you can write your instructions in natural language, your agent will perform better if you format instructions using an XML structure, which can help the model better follow instructions. Use the following XML tags:

| Tag | Description |
|-----|-------------|
| `<role>` | Defines the agent's core function or responsibility |
| `<persona>` | Describes the agent's personality, tone, and behavioral guidelines |
| `<primary_goal>` | Within `<persona>`, specifies the agent's main objective |
| `<constraints>` | Lists rules or limitations the agent must follow |
| `<taskflow>` | Outlines conversational flows as a series of subtasks |
| `<subtask>` | Within `<taskflow>`, a specific part of the conversation flow, containing one or more steps |
| `<step>` | Within `<subtask>`, an individual step that includes a trigger and an action |
| `<trigger>` | Within `<step>`, the condition or user input that initiates a step |
| `<action>` | Within `<step>`, the action the agent should take when a step is triggered |
| `<examples>` | Contains few-shot examples to guide agent behavior for specific scenarios |

### Structuring Taskflows with Subtasks

Group related steps into `<subtask>` elements rather than placing bare `<step>` elements directly inside `<taskflow>`. Subtask grouping helps the LLM focus on one concern at a time and reduces context confusion when the instruction has many steps.

**When to create a subtask:**
- Steps that belong to the same domain (e.g., authentication, troubleshooting, escalation)
- Steps that share a common trigger context (e.g., all error-handling steps)
- Steps that represent a distinct phase of the conversation (e.g., diagnosis vs guided resolution)

**Example structure:**
```xml
<taskflow>
    <subtask name="Authentication">
        <step name="Verify_Identity">...</step>
    </subtask>
    <subtask name="Issue_Resolution">
        <step name="Diagnose_Problem">...</step>
        <step name="Apply_Fix">...</step>
    </subtask>
    <subtask name="Escalation">
        <step name="Transfer_To_Human">...</step>
        <step name="Unintelligible_Handling">...</step>
    </subtask>
</taskflow>
```

See `assets/project-template/` for a full working example with WHY comments explaining each grouping decision.

### Role
Define a unique and highly specific role for each agent to ensure clarity. Maintain the highest level of specificity possible, leveraging Gemini to brainstorm a robust persona definition that makes it clear what the purpose of the agent is and avoids ambiguity.

#### Bad Role Example

You are a Store agent.

#### Good Role Example

```xml
<role>
    You are a Troubleshooting Support Specialist. Your primary goal is to understand a user's problem thoroughly, and then guide them through the appropriate troubleshooting and support actions to resolve their issues effectively.

    You are NOT a robot reading a script; you are a professional and empathetic support specialist whose job is to partner with the user to deeply understand their problem and guide them to the right solution.
</role>
```

### Persona
The persona should be set globally so that the agent is consistent across the entire conversation. Similar to a goal, be specific as to how you want your agent to behave. Here is a good starting point.

```xml
<persona>
    - Your tone MUST be professional, warm, and helpful.
    - Use clear, simple, and direct language that is easy to follow.
    - Favor being direct. When an acknowledgment is necessary, cycle through a wide variety of phrases (e.g., "Sure,", "Got it," "Okay") to keep the conversation natural and avoid using the exact same phrase multiple times.
    - Speak with a slow, rhythmic cadence appropriate for a phone conversation.
    - You speak with a standard American English accent and female voice. Your pronunciation, enunciation, and prosody must consistently reflect a standard US accent at all times. <-- MODIFY THIS FOR YOUR LOCALE AND GENDER
</persona>
```

### Ambiguity
Write your instructions to be as clear as possible. When in doubt, use gemini to help you iterate on your prompt.

#### Ambiguous Example

```xml
<subtask name="Clarification_and_Disambiguation">
    <step name="Assess_Input_Quality">
        <trigger>User input received.</trigger>
        <action>
            1. IF VAGUE: Ask a clarifying question.
            2. IF SPECIFIC: IMMEDIATELY trigger Formulate_and_Execute_Query. Do not attempt to answer.
        </action>
    </step>
</subtask>
```

Rationale: We do not define what is vague and what is specific, which leaves the LLM to get overly creative, which leads to agent instability.

#### Clear Example

```xml
<subtask name="Clarification_And_Routing">
    <step name="Clarify_User_Problem">
        <trigger>User states their problem.</trigger>
        <action>
          1. Analyze user's problem for specificity (The Actionability Test):
             Apply the test: "If you handed this problem statement to another support
             agent with no other context, would they know exactly how to troubleshoot?"
            - FAIL (Generic/Vague):
              - "It's broken", "It's not working", "It's acting weird"
              - "It is slow" (without context: what is slow?)
              - "It's showing an error message" (what error message?)
            - PASS (Specific/Actionable):
              - [Component] + [Symptom] (e.g., "Laptop screen has black spots")
              - [Function] + [Failure] (e.g., "WiFi connected but no websites load")
          2. Decision Logic:
            - If FAILS the test: ask clarifying questions (multi-turn loop)
            - If PASSES: transition to Classify_Problem_And_Route
          3. Synthesize and Update:
            - Combine original + clarified details into a rich problem statement
            - Call {@TOOL: set_user_problem} with the new description
            - Proceed to Classify_Problem_And_Route
        </action>
    </step>
</subtask>
```

Rationale: There is clear guidance for what queries need additional clarification.

## Agent Architecture
When you begin agent building, one of the most critical decisions is whether you will build a single agent framework or a multi-agent framework.

Single Agent: Implement a single, comprehensive instruction set supported by multiple tools to maintain unified agent logic.
Multi Agent: Decompose the system into specialized sub-agents, each governed by a dedicated prompt, utilizing handoffs to manage distinct taskflows. The multi-agent framework has a root agent with intent detection, which then passes off to a sub-agent to execute any particular use case.

You should start with a single agent, then decompose into specialized agents as conversational complexity increases or if instruction following begins to degrade. Breaking up agent logic into more "bite sized" pieces helps improve context management for the agent, which will lead to better performance.

To illustrate, imagine a retail agent that has 100 capabilities. However, it doesn't need access to all those capabilities at once; if a customer asks "where is my order?" the agent only needs to access the logic for handling order status. If you give the agent that context, but also the context for the 99 other intents, the agent is likely to make a mistake, because it might not know which instructions to follow.

So, while starting with a single agent approach is the simplest, note that if you have many use cases on your customer roadmap, you will likely need to pivot to a multi-agent architecture.

### Single-Agent
A single agent uses one single prompt to define all agent behaviors at the root level.

A single agent framework is best in the following circumstances:
- Quick prototypes and testing
- Simple and straightforward conversations (ex: show my cart items, status of an order, etc.)
- Linear conversation flows (simple branches and start/end states)
- Simple state definitions and transitions

Examples for Single Agents: A Password Reset Assistant or an Order Status Checker. Single-agent architectures excel when interactions require a linear task flow and straightforward conversation without complex cognitive branching.

#### Benefits of Single Agent
- **Higher implementation velocity:** A single agent framework can be built rapidly
- **Lower turn latency:** Since logic is contained in a single LLM call, this will typically be faster than a multi-agent approach

#### Limitations of Single Agent
- **Behavior Drift:** The model's output or personality may inconsistently shift over the course of an interaction
- **Tool Call Issues:** Model may fail to execute tools or provide incorrect parameters during the invocation
- **Context Rot:** As the history length increases, conversational coherence and instruction following degrades
- **Instruction Overload:** The model starts to ignore or skip logic as instructions become more complex, too many rules, ambiguity, conflicting instructions
- **Collaboration:** Hard for multiple people to make changes to the same agent

### Multi-Agent
A multi-agent architecture is the preferred architecture for agents that have multiple (2+ capabilities). This is best in the following circumstances:
- If the role/persona are meaningfully different between use cases
- If you have multiple disjoint CUJs that do not need to share context
- A CUJ is highly complex and can be broken down by multiple distinct (ideally sequential) logical steps
- When your single-agent is still having trouble following instructions even after extensive quality hill climbing

#### Benefits of Multi-Agent Architectures:
- **Debugging:** Provides precise isolation and resolution of logic failures within specific sub-agent contexts (Use code tree walk from root agent to sub agents/tools to identify failure points)
- **Targeted Evaluation:** Enables the implementation of specialized evals(golden and scenarios) for individual agent states.
- **Instruction Modularity:** Promotes loose coupling of instruction sets, mitigating the risk of context rot and instruction overload.
- **Enhanced Encapsulation:** Provides control over tool invocation routing and functional parameter precision through dedicated sub-agents
- **Architectural Scalability:** Establishes a modular design that efficiently scales to accommodate increasing conversational complexity through specialized sub-agents.

#### Limitations of Multi Agent
- **Latency:** Passing context from one agent to another can add latency
- **Context loss:** While variables can preserved, some context will be lost when handing off from one sub-agent to another
- **Development time:** A multi-agent framework is more complex, which leads to longer development time

A simple test is to offload part of the agent logic (instruction + tool def) to a standalone LLM call with specialized prompt - if that yields better results, it may hint towards splitting off into a specialized agent

### Using the multi-agent framework
With the benefits of the multi-agent framework in mind, it is best to think about how to break up the business logic into individual use cases. The best way to think about this is to follow two best practices:
- Build sub-agents that can be re-used across multiple intents (e.g., an "authentication" agent that could be used across various secure use cases for a banking agent)
- Isolate sub-agents to specific use cases that reflect the customer journey to minimize a sub-agent handing off to another sub-agent (which will introduce context loss)

Below are some examples illustrating both bad and good ways to use the multi-agent framework.

#### Bad example: Scheduling appointment broken into 3 agents
Splitting "collect availability -> find slots -> confirm booking" across 3 agents. Why this is bad:
- Flow Fragmentation: The underlying CUJ is a single, fluid flow that doesn't need decomposition
- Context Degradation: Agent handoffs are inherently lossy -- the booking agent loses what the availability agent learned
- Experience Disruption: Excessive transfers create a disjointed experience, forcing users to repeat data

#### Good example: Support agent with auth + specialized sub-agents
Root agent handles intent detection and authentication. Specialized sub-agents handle distinct CUJs: `troubleshooting_agent` (diagnose + resolve device issues), `billing_agent` (account charges, payment plans), `scheduling_agent` (appointment booking end-to-end). Each sub-agent owns its entire CUJ without handing off to another sub-agent.

Architectural Rationale:
- **Functional Specialization:** Decomposes agents only when roles or personas are distinctly different to ensure focused, unambiguous logic.
- **Contextual Isolation:** Targets disjointed Customer User Journeys (CUJs) that do not require shared context, effectively mitigating lossy handoff risks.
- **Operational Reliability:** Segments complex CUJs into discrete, sequential logical steps to maintain high-fidelity instruction following.
- **Architectural Scalability:** Enables scaling across multiple specialized specialists while facilitating precise evaluation and debugging within isolated contexts.

## Variables
Variables serve as the foundational architecture for maintaining agent state, providing the necessary visibility and structure for robust management. They are key for dynamic prompting. Adhere to the following patterns when defining your variables:
- **Observability Variables:** Best practices for internal monitoring
- **Lenses into State:** View these as diagnostic debug windows that provide insight into the internal logic of your agent.
- **Naming Conventions:** Apply consistent patterns, such as underscore prefixes, to distinguish them from core logic variables.
- **JSON Schemas:** Capture complex state data within structured schemas rather than managing a fragmented list of individual variables.
- **State Management:** Guidelines for efficient state control
- **Variable names:** Important to note that variable values are not substituted in prompts, changes to variable values are provided as part of conversation history. It is important to give semantically descriptive names for the model to understand the importance of variables in the context.
- **When to use State Variables:** Use state variables to collect/update information, perform routing/state transitions, remember checkpoints
- **Variable Explosion:** Implement JSON schemas to mitigate the complexity and "context rot" caused by an excessive number of state variables.
- **Feature Encapsulation:** Consolidate variables related to a specific tool or logical feature into a single, unified JSON state variable.
- **Deterministic Steering:** Use state variables as the primary mechanism for directing tool calls and callback triggers.
- **Dynamic Prompting:** Leverage variables to enable advanced, context-aware prompt updates programmatically.
- **Instruction Density:** Since variables determine instruction complexity, retain only those vital for decision-making and eliminate "ghost" variables.

Contrary to common belief, variables are not directly substituted into the prompt; instead, the agent tracks the entire history of variable updates to maintain state.

## Tool Design Guidelines
An agent is only as capable as its environment. A good tool design bridges the gap between reasoning and action, transforming a static model into a dynamic problem-solver. A good tool design prioritizes low latency and high reliability.

Implement tools to encapsulate deterministic logic or manage external system integrations required by your agent. Transactional tools, informational tools, orchestration tools.

Adhere to the following guidelines when defining your tool architecture:
- **Tool Name:** Utilize a semantically descriptive identifier that clarifies the tool's function, as the model relies on this for invocation routing.
- **Tool Description:** Provide a comprehensive explanation of the tool's core utility. The model uses this to determine when to call the tool.
- **Input Arguments:** Define the specific parameters required for execution. The model uses this to decide how to call the tool. As a thumb rule, practice designing tool inputs that are easily expressible by humans in voice mode. Try saying the example inputs out loud and estimate difficulty for a human to express these inputs without mistakes/errors.
- **Output Schema:** Specify a structured format for returned data to maintain logic consistency. This output schema should only include data that is needed by the LLM.
- **Examples:** Include few-shot scenarios to reinforce correct tool utilization patterns. This helps the model call the tool properly and reduces erroneous invocations.
- **Mocks:** Figure out how to test your tool if it is making a real call

Tool docstrings are equivalent to instructions. You should treat them with utmost importance. See our public docs for more information.

#### Bad tool design

```python
def check_info(id):
  return db.fetch_balance(id)
```

Rationale: Without meaningful docs, named parameters and return values, the LLM will not know how to appropriately call the tool. E.g. if you have multiple "ids" in your context, the LLM may get confused as to which one to use here.

#### Good tool design

```python
def retrieve_customer_account_balance(customer_id: str) -> dict:
  """Retrieves the current outstanding balance for a specific customer.

  Args:
    customer_id: The unique alphanumeric identifier for the customer.

  Returns:
    A dictionary containing the 'balance' (float) and 'currency' (str).
  """
  return db.fetch_balance(customer_id)
```

### Common Tooling Pitfalls
- **Ambiguous Tool Naming:** Employing semantically similar identifiers (e.g., "check_appointment" vs. "check_booking") introduces ambiguity that significantly degrades invocation routing accuracy.
- **Complex Input Arguments:** Constructing intricate schemas--such as dictionaries, lists, or free-form strings--increases the probability of the model supplying erroneous parameters during tool execution.
- **High Cardinality Arguments:** Providing parameters with an extensive range of potential values can reduce the model's ability to select tools deterministically. Avoid using input arguments that expect values in a continuous scale or have high cardinality (bad examples: exact_timestamp_ms, raw_latitude_longitude, or unique_session_id). Design good arguments that a human can express in voice mode as a thumb rule(Ex: region/country, last_n_days, topic_category etc).
- **Tool Explosion:** Exposing an excessive volume of tools within a single agent context often leads to instruction overload and diminished routing precision.
- **Execution Latency:** High-latency operations result in "dead air"; utilize filler statements to maintain engagement when long-running tool calls are unavoidable.
- **Tool Return Value Explosion:** The LLM sees the entire tool response. Returning excessive volumes of data, specially data that the LLM does not need can bloat the context and can result in degraded performance
- **Sequential tool calls in instructions:** Relying on the model to execute multiple tools in a specific order through natural language instructions can often lead to skipped steps, incorrect sequencing, or the use of wrong parameters when tool calls are chained in prompts.

### Tool wrappers
Write "unified" tool wrappers to encapsulate multiple operations and sequential API calls within a single execution block. You can also use a similar pattern for wrapping OpenAPI tool calls that return irrelevant data to the LLM.

Utilize the following functional consolidation patterns:
- **Functional Orchestration:** Replace fragmented `get_available_slots` and `create_event` tools with a comprehensive `schedule_event` tool to handle availability and reservation logic in one turn
- **Contextual Filtering:** Prefer a specialized `search_logs` tool that isolates high-relevance log segments and diagnostic context over a raw, high-cardinality `list_logs` invocation and filtering in the instructions.
- **State Aggregation:** Consolidate `get_customer_by_id`, `list_transactions` and `list_notes` into a single `get_customer_context` tool to provide a unified, structured data schema immediately.
See our docs for more information.

## Error Handling Guidelines

### Architecting for Robust Error Handling
- **Early Prerequisite Validation:** Prioritize the verification of mandatory inputs and context variables before executing core logic or initiating external service calls. Design variables specifically for information gathering and error capturing to ensure complete context before proceeding.
- **Graceful Exception Handling:** Implement structured try-except blocks for all external integrations, including API calls, database operations, and file systems. Ensure the agent handles exceptions gracefully to prevent logic degradation.
- **Failure Categorization:** Explicitly distinguish between tool invocation failures and logical errors returned in the response to maintain high routing precision and appropriate recovery behavior.
- **Deterministic Recovery Actions:** When an error occurs, the tool MUST return a dictionary with an agent_action key. This provides the model with exact instructions on what to communicate to the user and how to transition to a corrective taskflow.
- **Enhanced Observability:** Log granular failure details and update internal state variables during error handling. Use these observability lenses as diagnostic windows for debugging agent performance.
- **Docstring Failure Schematics:** Clearly define the structured format of the failure response within the tool's docstring to guide the model's understanding of failure states.

### Common Error Handling Pitfalls
- **Ambiguous Error Feedback:** Utilizing generic messaging such as "An error occurred" or "Failed to receive data" provides no actionable guidance for the agent to execute a deterministic recovery.
- **Schematic Inconsistency:** Discrepancies between a tool's docstring and its actual failure response structure significantly degrade the model's ability to route and interpret error states correctly.
- **Inadequate Observability:** Failing to implement granular logging for tool executions and internal state updates obscures the diagnostic windows required for robust debugging.
- **API Response Neglect:** Validating only the HTTP status code while skipping content validation of the response payload prevents the agent from identifying and acting upon critical logical errors.
- **Generic Exception Swallowing:** Implementing broad exception catches can mask underlying failures, preventing the agent from performing graceful degradation or corrective taskflows.
- **Context Completeness Bias:** Assuming that mandatory prerequisites and state variables are always present without proactive verification leads to degradation in instruction following.

## Callback Patterns for Deterministic Behavior

Use callbacks when behavior MUST be deterministic -- don't rely on instructions alone for critical flows like escalation, goodbye messages, or session termination. See `references/callback-api.md` for the full API surface and code examples.

**Key principle: Instructions tell the LLM WHAT to do (detection), callbacks ENFORCE HOW (execution).** Keep detection generative and natural. Make execution deterministic and reliable.

#### Common callback patterns:
- **Deterministic farewell**: `after_model_callback` injects text before `end_session` (LLM often calls `end_session` without speaking first)
- **Deterministic greeting**: `before_model_callback` intercepts session start and returns static greeting
- **Silence handling** (voice): `before_model_callback` detects "no user activity" signals, tracks counter in state, ends session after 3 silences
- **Deterministic escalation**: `LlmResponse.from_parts()` combines text + agent transfer in a single response
- **Calling tools from callbacks**: Use `tools.{function_name}(args)` for Python tools, `tools.{DisplayName}_{OperationId}(args)` for API connectors. System tools (`end_session`) cannot be called from callbacks.

#### Multi-model-call turns
The LLM can split a single turn across multiple model calls. The `after_model_callback` fires on EACH call separately. Use `callback_context.events` to check if the agent already produced text in a prior model call -- prevents double-text injection.

#### Trigger pattern for deterministic tool calls
The LLM decides WHAT to do (detection), the callback decides HOW (execution). The instruction tells the LLM to set a state variable (via a state-setting tool), then the `before_model_callback` intercepts and returns the tool calls with correct args -- bypassing the LLM entirely. This prevents missing tools, empty args, and unwanted transfers.

**Critical**: The state-setting tool MUST be in the agent's tool list. In multi-agent architectures, the trigger-handling callback must exist on ALL agents -- not just root.

#### Trigger recovery
When the LLM says the expected text but forgets to call the state-setting tool, the `after_model_callback` can detect the agent's own output and set the trigger for the next model call. This is not overfitting -- it detects the agent's instruction-driven text, not user input.

#### When to move logic from instruction to callback
If the behavior is a simple check (variable/flag) followed by a fixed response and tool calls -- with no LLM judgment needed -- it belongs entirely in a callback. If the behavior requires interpreting user intent (e.g., detecting a specific issue category in free text), the detection stays in the instruction but the action can be callback-driven via a flag.

#### Platform tool resolution errors bypass try/except
If you use the wrong tool name when calling `tools.{name}(...)` from a callback, the platform throws an error before your Python code executes -- `try/except` won't catch it. Always verify the exact tool name from the platform before using it in callbacks.

#### Preventing empty tool args
Defense in depth: (1) Better docstrings with `(REQUIRED)` markers and examples, (2) Tool-level fallback reading from state when args empty, (3) Trigger pattern as backup.

#### Tool docstrings guide the LLM
The LLM reads tool descriptions and parameter docstrings when deciding what args to pass. Clear docstrings with `(REQUIRED)` markers and concrete examples reduce empty-arg calls. Fix typos in arg names -- the LLM uses them for parameter matching.

#### Don't overfit with callbacks
Callbacks should enforce EXECUTION, not reimplement DETECTION. Signs of overfitting:
- Hardcoded phrase lists -- miss natural variations
- Callbacks bypassing LLM for intent classification
- Overly specific trigger keywords

The agent might pass goldens with phrase matching, but fail on real conversations. Sims are a better proxy for real-world performance.

#### Simpler instructions outperform complex ones
Adding programmatic logic to instructions (state-tracked counters, multi-step conditionals, explicit keyword requirements) confuses the LLM. A simple "On the FIRST attempt... On the SECOND attempt..." works better than "First call the state-setting tool to increment count, then check count value and branch." The LLM handles natural language patterns better than code-like logic.

#### Don't fight the LLM
GUIDE, don't PREVENT:
- `hide_tool()` reduces tool awareness -> worse instruction-following
- "Do NOT call this tool" confuses the LLM
- Removing tools breaks instructions and goldens that reference them
- Complex programmatic logic in instructions -> LLM handles natural language better

Instead: clear instructions, good tool docstrings, callbacks as a safety net.

#### Never remove tools without auditing instructions first.
Removing a tool breaks any instruction, golden, or constraint that references it.

For the full callback API surface (factory methods, reading parts, session state), see `references/callback-api.md`. For full code examples of all patterns above, see `assets/project-template/`.

## Instruction Design Anti-Patterns

These patterns cause regressions in practice. Avoid them.

| Anti-Pattern | Why It Fails | Do This Instead |
|-------------|-------------|----------------|
| Wholesale instruction rewrites | LLM relies on verbose context; "cleaner" versions lose information the model needs | Make small, targeted edits. Test after each change. |
| `conditional_logic` for intent classification | LLM gets confused by priority-ordered conditionals and falls back to generic refusals | Use separate `<step>` elements with distinct triggers |
| Negative conditions in triggers ("NOT [excluded category]") | LLM treats the negative as something to check, gets confused | Use positive triggers only; put the excluded case as an earlier, separate step |
| Eager follow-up triggers ("After answering any question") | Fires after sub-agent returns, causing wrong responses | Use specific triggers tied to resolution points |
| Relying on instruction for text-before-escalation | LLM calls tools without speaking first, ignoring "First Respond" text | Use `after_model_callback` to inject text before `end_session` |
| Simplifying instructions by removing examples/context | LLM loses the context it was depending on for correct behavior | Keep examples and context; reduce redundancy instead |
| Hardcoded phrase lists in callbacks for detection | Misses natural variations ("I'm fed up" won't match `["unacceptable"]`). Agent overfits to evals, fails real conversations | Keep detection in instructions (LLM understands intent). Use callbacks only for execution (trigger pattern) |
| Complex programmatic logic in instructions | State-tracked counters, multi-step conditionals confuse the LLM and reduce reliability | Use simple natural language: "On the FIRST... On the SECOND..." The LLM handles this better than code-like logic |
| Overly specific trigger keywords ("EXPLICITLY said 'current line'") | Makes the agent rigid and keyword-dependent instead of understanding intent naturally | Use natural language triggers. Trust the LLM's understanding of context |
| Escalation tool calls in instruction only | LLM sometimes says text but forgets to call tools, or calls them with empty args | Use trigger pattern: instruction sets a state trigger via a state-setting tool, callback returns tools |
| Escalation trigger callbacks on root agent only | Sub-agent flows bypass root callbacks -- trigger never fires | Add trigger-handling `before_model_callback` to ALL agents |
| Using `hide_tool()` to prevent empty-arg calls | Reduces LLM's tool awareness, causes worse instruction-following overall | Use better docstrings + tool-level state fallback + trigger pattern instead |
| "Do NOT call this tool" in instructions | Confuses the LLM, often reduces tool calling reliability | Guide with positive instructions ("call {@TOOL: state_setting_tool} with...") not negative constraints |

## Source Control
You can use the UI to build agents, but you must get them checked into source control (github, gitlab, etc) for easier management. Use SCRAPI to help you sync back and forth between the UI and source control.

### Pull an App
```bash
cxas pull {app_identifier} --project-id {project_id} --location {location} --target-dir {local_dir}
```

### Push Local Files
```bash
cxas push --app-dir {local_dir} --to {app_identifier} --project-id {project_id} --location {location}
```

### Branch an App
```bash
cxas branch "{source_app}" --new-name "{new_display_name}" --project-id {project_id} --location {location}
```

Using source control also enables multiple developers to work on the same app and maintain a single source of truth without stepping on other people's toes. The recommended pattern is to mimic software engineering best practices:
- **Branch:** Create a branch of the agent, so that they have their own sandbox
- **Modify:** Implement the feature that they're working on, including adding evals
- **Review:** Get a code review from someone on your team
- **Merge:** Push the agents back to the main source repository

## Advanced Techniques

### Dynamic Prompting
Leverage variables within instructions and update them programmatically through a `before_agent_callback`. Use for multi-step flows to reduce the agent's active context window. Improves instruction following when you have nested conditions.

**Pattern**: Replace complex nested conditionals in instructions with a single variable reference (`{dynamic_instruction}`), then use the `before_agent_callback` to resolve conditions and set the instruction text. Guard with an early-return check so it only runs once.

### Instructions in a Tool Response
Embed instructions in tool return values for progressive disclosure -- certain instructions only matter after a step is achieved. The tool returns contextual instruction strings based on the result (e.g., in-warranty vs out-of-warranty paths), and the instruction tells the agent "You MUST follow the instructions in the tool's response." Include `agent_action` keys in error returns for self-healing when prerequisites are missing.

See `assets/project-template/` for full implementation examples of both patterns.
