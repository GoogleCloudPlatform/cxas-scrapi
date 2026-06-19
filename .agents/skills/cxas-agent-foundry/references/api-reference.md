# GECX API Reference (SCRAPI)

SCRAPI backstop for when the bundled scripts don't cover your use case. For most operations, use the scripts in `.agents/skills/cxas-agent-foundry/scripts/` instead.

For exact field names, enum values, or threshold structures, see the schema files in `api-schemas/`.

## Table of Contents

- [Authentication](#authentication)
- [Before Making ANY API Call](#before-making-any-api-call)
- [Build Order](#build-order)
- [Common Mistakes](#common-mistakes)
- [Apps](#apps)
- [Agents](#agents)
- [Tools](#tools)
- [Variables](#variables)
- [Callbacks](#callbacks)
- [Sessions](#sessions)
- [Evaluations](#evaluations)
- [Inspecting an Existing App](#inspecting-an-existing-app)
- [Version Management](#version-management)
- [Diagnostic REST Commands](#diagnostic-rest-commands)

## Authentication

SCRAPI picks up credentials automatically (application-default or service account). No manual token management.

```python
from cxas_scrapi.core.apps import Apps
apps = Apps(project_id="my-project", location="us")

# Or from an existing app
from cxas_scrapi.core.agents import Agents
agents = Agents(app_name="projects/my-project/locations/us/apps/APP_ID")
```

## Before Making ANY API Call

Check the actual source code -- docs may be stale:
```bash
grep -A 20 "def create_" .venv/lib/python3.13/site-packages/cxas_scrapi/core/<module>.py
```

## Build Order

1. Set model on app (may fail if no root agent -- catch and retry after step 3)
2. Check existing agents with `get_agents_map(reverse=True)` to avoid ALREADY_EXISTS
3. Create agents (skip existing), link sub-agents via `child_agents`
4. Associate system tools (`end_session`) -- built-in, do NOT create
5. Create custom tools, associate with agents via `update_agent(tools=[...])`
6. Create variables
7. Create callbacks
8. Set root agent + model on app
9. Pull to local: `cxas pull $APP_NAME --target-dir cxas_app/`
10. Run linter: `cxas lint --app-dir cxas_app/`
11. Run build verification gates (see `build-verification.md`)

## Common Mistakes

- `Agents()` needs full resource path as `app_name` -- not separate project/app/location args
- `parent_agent` and `sub_agents` do NOT exist -- use `child_agents`
- Set model on app BEFORE creating agents -- default `gemini-2.5-flash` may not be available
- Check `get_agents_map()` before creating -- duplicates cause ALREADY_EXISTS errors
- Tools must be associated via `update_agent(tools=[...])` -- creating them is not enough
- `end_session` is a built-in system tool -- associate it, don't create it
- `create_callback` APPENDS -- be aware when calling multiple times
- Variables: use `variable_name` not `name`, only `STRING`/`BOOLEAN` types, parse counters with `int(val or 0)`

## Apps

```python
apps = Apps(project_id=project_id, location=location)
app = apps.create_app(display_name="My Agent App", description="...")
app_name = app.name  # Full resource path
```

**Schema:** `api-schemas/apps.md`

## Agents

```python
agents = Agents(app_name=app_name)
root = agents.create_agent(display_name="root_agent", instruction="...")

# Link sub-agents -- use child_agents, NOT parent_agent
agents.update_agent(agent_name=root.name, child_agents=[sub.name])
```

**Key methods:** `create_agent`, `update_agent`, `get_agents_map(reverse=True)`, `list_agents`

**Agent proto fields:** `name`, `display_name`, `description`, `model_settings`, `instruction`, `tools`, **`child_agents`**, `before_agent_callbacks`, `after_agent_callbacks`, `before_model_callbacks`, `after_model_callbacks`, `guardrails`, `toolsets`, `transfer_rules`

**Schema:** `api-schemas/agents.md`

## Tools

**Prefer `cxas push` over the `create_tool` API.** Tools created via `cxas push` (in the `tools/` directory) are automatically associated with agents. Tools created via `create_tool` require manual association and get cleared on the next push.

**Tool JSON format** (in `tools/<name>/<name>.json`):
```json
{
    "name": "<tool_name>",
    "pythonFunction": {
        "name": "<function_name>",
        "pythonCode": "tools/<name>/python_function/python_code.py",
        "description": "Tool description for the LLM."
    },
    "executionType": "SYNCHRONOUS",
    "displayName": "<tool_name>"
}
```

### Toolsets (OpenAPI Toolsets)

While standard Tools wrap a single function (like a Python sandboxed function), **Toolsets** allow you to expose multiple tools at once, typically by importing an external service specification like an OpenAPI schema.

**Prefer `cxas push` over the `create_toolset` API.** Similar to tools, toolsets defined locally in the `toolsets/` directory are automatically pushed and managed.

#### Local Folder Structure
An OpenAPI toolset is defined by a directory in `toolsets/` containing a configuration JSON and the OpenAPI schema file:

```
toolsets/
└── <toolset_name>/
    ├── <toolset_name>.json  # Toolset configuration
    └── open_api_toolset/
        └── open_api_schema.yaml  # OpenAPI 3.0.0 schema
```

#### Toolset JSON Format (in `toolsets/<name>/<name>.json`):
```json
{
  "displayName": "<toolset_name>",
  "description": "Description of the toolset for the LLM.",
  "openApiToolset": {
    "openApiSchema": "toolsets/<toolset_name>/open_api_toolset/open_api_schema.yaml",
    "apiAuthentication": {
      "apiKeyConfig": {
        "keyName": "Authorization",
        "requestLocation": "HEADER",
        "apiKeySecretVersion": "projects/my-project/secrets/my-api-key/versions/latest"
      }
    }
  }
}
```
*(Note: `apiAuthentication` is optional and supports `apiKeyConfig`, `oauthConfig`, `serviceAccountAuthConfig`, or `serviceAgentIdTokenAuthConfig`.)*

#### Assigning Toolsets to an Agent (in Agent JSON)
Unlike standard tools which are listed in the `tools` array, **toolsets are assigned to an agent using the `toolsets` array**.

To assign a toolset to an agent, add an entry to the `toolsets` array in the agent's JSON file (`agents/<agent_name>/<agent_name>.json`). You can optionally restrict the agent's access to only specific tools (operations) within that toolset by providing their raw `operationId`s in the `toolIds` array:

```json
{
  "displayName": "my_agent",
  "tools": ["end_session"],
  "toolsets": [
    {
      "toolset": "my_toolset",
      "toolIds": ["listProducts", "getProductDetails"]
    }
  ]
}
```
*   **`toolset`**: The local display name/ID of the toolset (e.g., `"my_toolset"`).
*   **`toolIds`**: Optional list of raw `operationId`s from the OpenAPI schema. If omitted, the agent has access to ALL operations defined in the toolset's schema.

#### Calling Toolset Tools from Callbacks
When invoking a toolset tool directly from Python callback code (e.g., in `before_model` or `after_model`), use the combined name as a method on the `tools` global:

```python
# Call 'getProductDetails' from 'my_toolset'
result = tools.my_toolset_getProductDetails(product_id="123")
```

**IMPORTANT -- tool naming:** Agent JSON files reference tools by `displayName`. Use **snake_case** for both `name` and `displayName` (e.g., `"lookup_benefits"`, NOT `"Lookup Benefits"`). The `displayName` must exactly match the string in the agent's `tools` array. Mismatched names cause `Reference not found` errors on push.

**Tool Python code**: Tools access session state via the `context` global -- NOT as a function parameter. The platform injects `context` at runtime. `context.state` and `context.variables` are interchangeable and point to the same object.

You can also use these built-in shorthand functions available in the tool's global scope:
- `get_variable(name: str, default: Any = None)`
- `set_variable(name: str, value: Any)`
- `remove_variable(name: str)`

Do NOT use `**kwargs` in tool function signatures -- CXAS requires explicit named parameters to generate the tool schema. Do NOT use `None` as a default value for parameters (e.g., `member_id: str = None`) -- the platform requires defaults to be strictly type-matching JSON-serializable values (use `""` for strings, `0` for ints). Both `**kwargs` and `None` defaults cause tools to be silently dropped during import with no error.

```python
def my_tool(arg1: str, arg2: str = "") -> dict:
    # Access state via the context global
    auth = context.state.get("auth_status", "")
    
    # Or use shorthand functions
    auth = get_variable("auth_status", "")
    set_variable("my_var", "value")
    
    return {"status": "success"}
```

System tools (`end_session`, `customize_response`, `transfer_to_agent`) are built-in -- reference by name in agent JSON, don't create.

**Schema:** `api-schemas/tools.md`

## Variables

```python
from cxas_scrapi.core.variables import Variables, VariableType

variables = Variables(app_name=app_name)

# Use VariableType Enum or equivalent strings
variables.create_variable(
    variable_name="auth_status",
    variable_type=VariableType.STRING,  # or "STRING"
    variable_value="",
)
```

Supported types (mapping to UI concepts):
- `STRING` (Text)
- `INTEGER` (Number)
- `NUMBER` (Number)
- `BOOLEAN` (True/False)
- `OBJECT` (Custom schema)

**Note on Arrays**: Any of the types above can be configured as an array (e.g., an array of strings).
- `ARRAY`: Used to represent a list of items. The items within the array must be one of the other supported types (e.g., a list of strings or a list of objects). Note that nested arrays (array of arrays) are not supported in the UI and should be avoided in configuration.

### Referencing Variables in Instructions

The agent supports two types of variables in instructions:

*   **Static Variables**: Compiled directly into the agent prompt *before* the model call. They act as a direct 1:1 text substitution.
    *   **Syntax**: `{{variable_name}}` (double curly braces)
    *   **Use case**: Configuration data, rigid business rules, or large contextual payloads that don't change during a conversation.
    *   *Warning*: Updating static variable values invalidates prompt caching, potentially leading to higher latency.

*   **Dynamic Variables**: Can be updated at any point during a conversation by tools, callbacks, or API requests. They are appended as `<state_update>` events to conversation history.
    *   **Syntax**: `{variable_name}` (single curly braces)
    *   **Use case**: Information extracted from the user, outputs from external APIs (tools), or mutating state.

### Referencing Variables in OpenAPI Specs

You can inject variables from the session context (like session ID or custom variables) into your OpenAPI requests using the `x-ces-session-context` extension field within the parameter definition.

**Available values**:
*   `$context.project_id`, `$context.project_number`, `$context.location`, `$context.app_id`, `$context.session_id`, `$context.turn_index` (for resource IDs and metadata).
*   `$context.variables.variable_name` (for a specific custom variable).
*   `$context.variables` (for all context variables as an object).

**Example**:
```yaml
      parameters:
        - name: X-SESSION
          in: header
          description: session id
          required: true
          schema:
            type: string
          # This extension injects the session ID
          x-ces-session-context: $context.session_id
```

## Callbacks

```python
from google.protobuf import field_mask_pb2  # MUST import -- SDK bug
callbacks = Callbacks(app_name=app_name)

callbacks.create_callback(
    agent_id=root.name,           # Full resource path
    callback_type="before_agent",  # lowercase: before_agent, after_agent, before_model, after_model
    python_code="def before_agent_callback(callback_context): ...",
)
```

**Callback signatures (with types -- no imports needed, lint-enforced by C009):**
- `before_agent_callback(callback_context: CallbackContext) -> Optional[Content]`
- `after_agent_callback(callback_context: CallbackContext) -> Optional[Content]`
- `before_model_callback(callback_context: CallbackContext, llm_request: LlmRequest) -> Optional[LlmResponse]`
- `after_model_callback(callback_context: CallbackContext, llm_response: LlmResponse) -> Optional[LlmResponse]`
- `before_tool_callback(tool: Tool, input: dict[str, Any], callback_context: CallbackContext) -> Optional[dict[str, Any]]`
- `after_tool_callback(tool: Tool, input: dict[str, Any], callback_context: CallbackContext, tool_response: dict[str, Any]) -> Optional[dict[str, Any]]`

**Callback runtime API (inside callback code):**
- `callback_context.state` (dict) for variables -- NOT `.session`. `callback_context.state` and `callback_context.variables` are interchangeable and point to the same object.
- `CallbackContext` also provides shorthand methods for variables:
  - `callback_context.get_variable(name: str, default: Any = None)`
  - `callback_context.set_variable(name: str, value: Any)`
  - `callback_context.remove_variable(name: str)`
- Return `None` from before_model to proceed -- do NOT return `llm_request`
- Platform types (`Part`, `Content`, `LlmResponse`, `LlmRequest`, `CallbackContext`) are auto-provided as globals -- do NOT import them. Everything else (including `from typing import Optional, Iterator`) must be explicitly imported or the callback will fail at push time.
- `llm_request.contents` is the conversation history (a list of `Content` objects with `.role` and `.parts`). NOT `.messages`, NOT `.message` — those raise `'LlmRequest' object has no attribute ...` at platform runtime. To iterate model/user turns, walk `llm_request.contents`. See the template's `before_model_callbacks_01/python_code.py` for a working example.
- Parse counters safely: `int(state.get("x") or 0)`
- **CRITICAL: `before_agent_callback` fires on EVERY turn**, not just when the agent starts. Any state initialization in this callback MUST have an early-return guard (e.g., `if state.get("auth_status"): return None`) to avoid resetting state on every turn.

**Common callback patterns (the model can't infer these from training data — they're platform-specific):**

```python
# 1. Construct a response (text + tool calls) from before_model
return LlmResponse.from_parts(parts=[
    Part(text="I'll transfer you to a specialist now."),
    Part(function_call=Part.FunctionCall(
        name="transfer_to_agent",
        args={"agent": "billing_agent"},
    )),
])

# 2. Access the user's most recent input (parts list — text, audio, events)
for part in callback_context.get_last_user_input():
    if part.text == "<event>session start</event>":
        return LlmResponse.from_parts(parts=[Part(text="Hi, how can I help?")])

# 3. Walk full session event history (used by after_model to dedupe text
#    across multiple model calls within one turn)
for event in reversed(callback_context.events):
    # event has .author, .content (with .parts), .timestamp, etc.
    ...

# 4. Call a tool FROM a callback (NOT a tool call by the agent — direct invocation)
#    Python function tools: tools.<function_name>(args)
#    API connector tools:   tools.<DisplayName>_<OperationId>(args)
response = tools.lookup_account(account_id="123", customer_id="456")
# or:
response = tools.Read_Customer_Datastore_readDatastore(record_id="...")
```

**Key methods:** `create_callback` (appends), `update_callback(index=0)`, `delete_callback(index=0)`, `list_callbacks`

**Schema:** `api-schemas/agents.md` (Callback schema is agent-scoped)

## Sessions

```python
sessions = Sessions(app_name=app_name)
r = sessions.run(session_id="test-1", text="Hello", variables={"account_id": "123"})
sessions.parse_result(r)
```

**Schema:** `api-schemas/sessions.md`

## Evaluations

```python
evals = Evaluations(app_name=app_name)
evals_map = evals.get_evaluations_map()
run = evals.run_evaluation(evaluations=["eval_name"], modality="audio", run_count=5)
results = evals.list_evaluation_results_by_run(run_id)
```

**Structured results:**
```python
utils = EvalUtils(app_name=app_name)
dfs = utils.evals_to_dataframe(eval_names=["golden_auth"])
# dfs["summary"], dfs["failures"], dfs["trace"]
```

**Schema:** `api-schemas/evaluations.md` -- includes threshold fields, scoring enums, result structures

## Inspecting an Existing App

```python
app = apps.get_app(app_name)
agents_map = agents.get_agents_map(reverse=True)  # {display_name: resource_path}
tools_map = tools.get_tools_map()
agent = agents.get_agent(resource_path)
print(agent.instruction)
```

## Version Management

```python
versions = Versions(app_name=app_name)
versions.create_version(display_name="Pre-improvement snapshot")  # for rollback
versions.list_versions()
versions.revert_version(version_name=version_name)
```

## Diagnostic REST Commands

For ad-hoc debugging when SCRAPI doesn't cover your use case. Requires `TOKEN=$(gcloud auth print-access-token)` and `BASE="https://ces.googleapis.com/v1beta/projects/${PROJECT}/locations/${LOCATION}/apps/${APP_ID}"`.

```bash
# Review conversations (live or simulator)
curl -s "${BASE}/conversations?sources=LIVE&pageSize=10" -H "Authorization: Bearer ${TOKEN}"
curl -s "${BASE}/conversations/${CONVERSATION_ID}" -H "Authorization: Bearer ${TOKEN}" | jq '.turns[].messages[]'

# Check recent changes
curl -s "${BASE}/changelogs?pageSize=20" -H "Authorization: Bearer ${TOKEN}" | jq '.changelogs[] | {createTime, action, resourceType, author}'

# Check guardrails
curl -s "${BASE}/guardrails" -H "Authorization: Bearer ${TOKEN}"

# Execute a tool directly (bypass agent)
curl -s -X POST "${BASE}:executeTool" -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
  -d '{"tool": "'"${BASE}"'/tools/${TOOL_ID}", "arguments": {"param1": "value1"}}'

# Stream session (real-time debugging)
curl -s -X POST "${BASE}/sessions/${SESSION_ID}:streamRunSession" -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" -d '{"config": {"enableTextStreaming": true}, "inputs": [{"text": "Hello"}]}'

# Test with fake tools (bypass real API calls)
curl -s -X POST "${BASE}/sessions/${SESSION_ID}:runSession" -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" -d '{"config": {"useToolFakes": true}, "inputs": [{"text": "Hello"}]}'

# Check deployments
curl -s "${BASE}/deployments" -H "Authorization: Bearer ${TOKEN}"

# Retrieve toolset tools (MCP/OpenAPI debugging)
curl -s -X POST "${BASE}/toolsets/${TOOLSET_ID}:retrieveTools" -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" -d '{}'
```
