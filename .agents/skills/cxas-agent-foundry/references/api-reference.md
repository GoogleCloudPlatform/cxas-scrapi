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

**IMPORTANT -- tool naming:** Agent JSON files reference tools by `displayName`. Use **snake_case** for both `name` and `displayName` (e.g., `"lookup_benefits"`, NOT `"Lookup Benefits"`). The `displayName` must exactly match the string in the agent's `tools` array. Mismatched names cause `Reference not found` errors on push.

**Tool Python code**: Tools access session state via the `context` global -- NOT as a function parameter. The platform injects `context` at runtime. Do NOT use `**kwargs` in tool function signatures -- GECX requires explicit named parameters to generate the tool schema. Do NOT use `None` as a default value for parameters (e.g., `member_id: str = None`) -- the platform requires defaults to be strictly type-matching JSON-serializable values (use `""` for strings, `0` for ints). Both `**kwargs` and `None` defaults cause tools to be silently dropped during import with no error.
```python
def my_tool(arg1: str, arg2: str = "") -> dict:
    # Access state via the context global -- do NOT add context as a parameter
    auth = context.state.get("auth_status", "")
    context.state["my_var"] = "value"
    return {"status": "success"}
```

System tools (`end_session`, `customize_response`, `transfer_to_agent`) are built-in -- reference by name in agent JSON, don't create.

**Schema:** `api-schemas/tools.md`

## Variables

```python
variables = Variables(app_name=app_name)
variables.create_variable(variable_name="auth_status", variable_type="STRING", variable_value="")
```

Valid types: `STRING`, `BOOLEAN` only. `INT`/`INTEGER`/`NUMBER` raise `ValueError`.

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
- `callback_context.state` (dict) for variables -- NOT `.session`
- Return `None` from before_model to proceed -- do NOT return `llm_request`
- Platform types (`Part`, `Content`, `LlmResponse`, `LlmRequest`, `CallbackContext`) are auto-provided as globals -- do NOT import them. Everything else (including `from typing import Optional, Iterator`) must be explicitly imported or the callback will fail at push time.
- `llm_request.messages` (plural) NOT `.message`
- Parse counters safely: `int(state.get("x") or 0)`
- **CRITICAL: `before_agent_callback` fires on EVERY turn**, not just when the agent starts. Any state initialization in this callback MUST have an early-return guard (e.g., `if state.get("auth_status"): return None`) to avoid resetting state on every turn.

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
