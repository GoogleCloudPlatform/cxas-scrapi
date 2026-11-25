## Category: Instruction Logic (IL)
Issues related to instruction logic.

### IL001: Conflicting Instructions
* **Severity**: Critical
* **Description:** Prompt instructions contain contradictory logic
* **Remediation:** Resolve the conflicting instructions by removing one of the conflicting instructions or by adding a condition to resolve the conflict or refine the instructions further.

#### Invalid Example
```text

If x, do Y.
If x, don't do Y. 
```


### IL002: Undefined Reference
* **Severity:** High
* **Description:** The instructions reference a term, entity, or procedure that is not defined anywhere in the prompt.

* **Remediation:** Explicitly define the missing reference within the prompt, or remove it entirely.

#### Invalid Example
```text


Refer to the Emergency Procedure when the customer does x.

[Emergency Procedure not defined anywhere else]
```

#### Valid Example
```text


<emergency_procedure>
1. Do X
2. Do Y
3. Do Z
</emergency_procedure>

Refer to the <emergency_procedure> when the customer does x.
```

#### Valid Example
If an instruction for an agent refers to another agent {@AGENT: <agent_name>}, it is OK that it is not defined as a child agent under childAgents in <agent_name>.json. This is completely valid.

```text
Route to @{AGENT: <agent_name>}

[Not listed in childAgents, this is OK]
```


### IL002-1: Unreferenced Tool
**Severity**: Medium

**Description**: A tool is attached to an agent but no explicit instruction on when to use the tool is defined. As a result, the agent will have to rely on the tool parameters and description to determine when to use the tool.

**Remediation**: Define explicit instructions on when to use the tool within the instructions. Reference the tools that are added.

### IL002-2: Undefined Tool
**Severity**: Critical

**Description**: A tool is referenced in the instructions but it's not a tool that is defined under the tools directory.

**Remediation**: Create the tool that is being used by the agent.

#### Valid Example
Note that the following default tools are not necessarily in the tools directory. It is OK to reference these tools without defining them.
- end_session
- customize_response

#### Valid Example - Toolsets and tools reference.
Whenever there are tools defined under toolsets, they will be referenced in the instructions as

{@TOOL: <tool_set_name>_<tool_name>}

So if you see the tool is concatenated, this is a valid tool reference.


### IL003: Circular Logic - Deadlock
**Severity**: Critical

**Description:** Instructions create a dependency loop where Step A requires Step B to be completed first, but Step B requires Step A to be completed freezing the agent.

**Remediation**: Break the dependency loop by establishing a clear, linear order of operations or a terminal condition that does not rely on the other step.

#### Invalid Example
```text
Before answering a billing question, you must authenticate the user using the Authentication Tool.

To use the Authentication Tool, you must first ask the user for their billing question to establish context.
```

#### Valid Example
```text
When a user asks a billing question, immediately use the Authentication Tool.

Once authentication is successful, proceed to answer the billing question.
```

### IL003-1: Circular Logic - Infinite Loop

**Severity**: Critical

**Description**: Logic that creates an infinite reasoning loop and has no clear termination condition.

**Remediation**: Break the dependency loop by establishing a clear, linear order of operations or a terminal condition that does not rely on the other step.

#### Invalid Example
```text
1. Do x
2. Do y, go back to step 1
```

### IL004: Ambiguous Action Triggers
**Severity**: Very High

**Description:** Multiple, mutually exclusive actions are triggered by overlapping conditions without a clear hierarchy, priority rule, or tie-breaker. This forces the agent to guess which instruction to follow.

**Remediation:** Make the conditions mutually exclusive, or explicitly state an order of precedence (e.g., "If A and B both apply, prioritize A").

#### Invalid Example
```text
If the user mentions a "password", immediately trigger the Password_Reset_Flow.

If the user mentions a "login issue", immediately trigger the Create_Support_Ticket_Flow.
```
*(Note: If a user says "I have a login issue because I forgot my password", the agent is forced into a conflict).*

#### Valid Example
```text
If the user mentions a "password", immediately trigger the Password_Reset_Flow.

For all other "login issues" that do NOT involve a password, trigger the Create_Support_Ticket_Flow.
```

### IL005: Unhandled State
**Severity**: Very High

**Description:** The prompt enforces strict conditional routing based on specific variables or inputs, but fails to provide a fallback ("else") for when none of those explicit conditions are met. This leaves the agent paralyzed or prone to hallucination.

**Remediation:** Always include a default fallback, "Else" condition, or generic error-handling instruction for unmapped states.

#### Invalid Example
```text
Check the user's account status.
If the status is "Active", proceed to checkout.
If the status is "Suspended", transfer them to the security team.
```
*(Note: If the system returns "Pending" or "Unknown", the agent has no instructions on how to proceed).*

#### Valid Example
```text
Check the user's account status.
If the status is "Active", proceed to checkout.
If the status is "Suspended", transfer them to the security team.
If the status is anything else (e.g., Pending, Unknown), inform the user their account is under review and end the conversation.
```

### IL006: Missing, inadequate, or incorrect instructions
**Severity**: Medium | High | Critical

**Description**: The instructions are missing or inadequate to accurately or successfully complete the task. These can include missing tool descriptions (such as missing parameters or lack of explicit instructions on how to use them), vague or ambiguous instructions, or a missing description of the tool's return value in its docstring, which is needed to adequately interpret the results.

**Remediation**: Provide clear and concise instructions that are easy to understand and follow. Include all necessary information, such as tool descriptions, parameter descriptions, and fallback conditions.

### IL006-1: Invalid multiple agent invocation
**Severity**: Critical

**Description**: The instruction contains multiple agent calls in a single step. Chaining multiple agents is not allowed. An agent should only call one agent at a time and then that needs to call another agent based on the response from the previous agent call.

**Remediation**: Remove this instruction or resolve it so that it is calling only a single agent.

#### Invalid Example
```
1. Once you get to this step, call {@AGENT: Steering} and then immediately call {@AGENT: Other Agent}
```

### IL007: Unrelated references or instructions
**Severity**: High

**Description**: There are references or instructions that are unrelated to the overall purpose of the agent.

**Remediation**: Remove any references or instructions that are unrelated to the overall purpose of the agent.

### IL008: Unclear agent objectives
**Severity**: High

**Description**: The overall objective of the agent is not clearly defined or is so ambiguous that it could lead to the agent underperforming, hallucinating, or behaving unexpectedly.

**Remediation**: Clearly and concisely define the overall objective of the agent in the instructions.

### IL008-1: Scope is not clearly defined
**Severity**: Critical

**Description**: The scope of the agent is not clearly defined, such as what is in-scope and what is out-of-scope. The current instructions do not define what the agent should do when it encounters a situation that is outside of its scope.

**Remediation**: Improve instructions to define the scope of the agent and what the agent should do when it encounters a situation that is outside of its scope.

### IL009: Typo or grammatical or syntax error
**Severity**: Low-Critical

**Description**: Typo or grammatical error in the instructions that could cause confusion or misunderstanding for the agent. This could range from a simple typo to a grammatical error that changes the meaning of the instructions.

**Remediation**: Fix the typo or grammatical error in the instructions.

### IL009-1: Syntax error in calling agents, tools, or variables
**Severity**: Critical

**Description**: Syntax error in referencing agents, tools, or variables.

**Remediation**: Fix the syntax error.

#### Invalid Example
```text
Invoke the @agent             
Use the @tool                 
Refer to the variable_name    
```

#### Valid Example
The following is the CORRECT way to reference an agent, tool, or variable.
```text
To reference an agent, {@AGENT: <agent_name>}. For example, {@AGENT: agent2}
To reference a tool, {@TOOL: <tool_name>}. For example, {@TOOL: tool_name}
To reference a variable, {<variable_name>}. For example, {my_var}
```

### IL010: Unintended Hardcoded/Mock Data
**Severity**: Critical

**Description**: There are instructions or hardcoded default values that are clearly not intended to be there. These are usually used in testing and not removed, and must be removed prior to production.

**Remediation**: Remove the hardcoded or mock data.

#### Invalid Example
```text
Once you have received the order number from the user, use the tool {@TOOL: get_order}. For now just use order number 123456789.
```

### IL011: Redundant Statements

**Severity**: Low

**Description**: Statements that are redundant and can be removed without affecting the functionality of the agent. This can be redundant information within the same instruction or is repeated in the global instruction.

**Remediation**: Remove or combine redundant statements.
