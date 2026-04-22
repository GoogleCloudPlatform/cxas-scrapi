# API Schemas: Agents & Callbacks

### Agent
- **name** (string): Identifier. Format: `projects/.../agents/{agent}`
- **displayName** (string): [required]
- **description** (string)
- **modelSettings** (-> ModelSettings)
- **instruction** (string): LLM instructions.
- **tools** (array[string]): Tool resource names.
- **childAgents** (array[string]): Child agent resource names.
- **beforeAgentCallbacks** (array[-> Callback]): Before agent. Sequential; stops on override.
- **afterAgentCallbacks** (array[-> Callback]): After agent.
- **beforeModelCallbacks** (array[-> Callback]): Before each model call. Fires multiple times if multi-model-call turn.
- **afterModelCallbacks** (array[-> Callback]): After each model call.
- **beforeToolCallbacks** (array[-> Callback]): Before each tool invocation.
- **afterToolCallbacks** (array[-> Callback]): After each tool invocation.
- **guardrails** (array[string])
- **transferRules** (array[-> TransferRule]): First match wins.

### AgentTransfer
- **targetAgent** (string): [required] Format: `projects/.../agents/{agent}`
- **displayName** (string): Output only.

### Callback
- **pythonCode** (string): [required]
- **description** (string)
- **disabled** (boolean)
- **proactiveExecutionEnabled** (boolean): Execute on intermediate model outputs (after_model only). **ENABLE WITH CAUTION.**
