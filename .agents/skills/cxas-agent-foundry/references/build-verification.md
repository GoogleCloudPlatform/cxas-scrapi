# Build Verification Gates

Run these gates IN ORDER after building agents/tools/callbacks. ALL must pass before writing evals.

## Table of Contents

- [CRITICAL: Always use the existing app](#critical-always-use-the-existing-app)
- [Gate 1: Pull, Lint and Push](#gate-1-pull-lint-and-push)
- [Gate 2: Agent hierarchy](#gate-2-agent-hierarchy)
- [Gate 3: Tool associations (including system tools)](#gate-3-tool-associations-including-system-tools)
- [Gate 4: Callback inventory](#gate-4-callback-inventory)
- [Gate 5: Single-turn smoke test](#gate-5-single-turn-smoke-test)
- [Gate 6: Multi-turn smoke test](#gate-6-multi-turn-smoke-test)

## CRITICAL: Always use the existing app

Read `gecx-config.json` for the app ID. Always construct the full resource path:
```python
APP_NAME = f"projects/{PROJECT_ID}/locations/{LOCATION}/apps/{APP_ID}"
```
**NEVER call `apps.create_app()` or `cxas create` during verification or eval runs.** That creates a new orphaned app. Always use the existing `deployed_app_id` from `gecx-config.json` with every SCRAPI client, `cxas pull`, `cxas push`, and eval script command.

## Gate 1: Pull, Lint and Push
Sync platform state to local, lint, fix issues, then push fixes back:
```bash
# 1. Pull platform state to local
GOOGLE_CLOUD_PROJECT=$PROJECT_ID .venv/bin/cxas pull \
  projects/$PROJECT_ID/locations/$LOCATION/apps/$APP_ID \
  --project-id $PROJECT_ID --location $LOCATION --target-dir cxas_app/

# 2. Run linter
.venv/bin/cxas lint --app-dir cxas_app/

# 3. If lint found issues -- fix them locally in cxas_app/, then push back
GOOGLE_CLOUD_PROJECT=$PROJECT_ID .venv/bin/cxas push \
  --app-dir cxas_app/ \
  --to projects/$PROJECT_ID/locations/$LOCATION/apps/$APP_ID \
  --project-id $PROJECT_ID --location $LOCATION

# 4. Re-pull to confirm sync
GOOGLE_CLOUD_PROJECT=$PROJECT_ID .venv/bin/cxas pull \
  projects/$PROJECT_ID/locations/$LOCATION/apps/$APP_ID \
  --project-id $PROJECT_ID --location $LOCATION --target-dir cxas_app/

# 5. Re-lint -- must pass clean
.venv/bin/cxas lint --app-dir cxas_app/
```
The `--to` flag in `cxas push` MUST use the full resource path `projects/.../apps/$APP_ID` -- not just the UUID. Using the wrong path or omitting `--to` may create a new app.

## Gate 2: Agent hierarchy
```python
agents_map = agents_client.get_agents_map(reverse=True)
print(agents_map)  # Verify root + all sub-agents exist, count matches TDD
```

## Gate 3: Tool associations (including system tools)
```python
tools_map = {t.display_name: t.name for t in tools_client.list_tools()}
print(f"Tools on platform: {list(tools_map.keys())}")

# Get root agent name from app config
app = apps_client.get_app(APP_NAME)
root_agent_name = app.root_agent.split("/")[-1] if app.root_agent else None

for name, resource in agents_map.items():
    agent = agents_client.get_agent(resource)
    tool_ids = [t.split('/')[-1] for t in (agent.tools or [])]
    is_root = (resource == app.root_agent) or (name == root_agent_name)
    has_end = any("end_session" in t for t in (agent.tools or []))
    flag = ""
    if is_root and not has_end:
        flag = " WARNING: ROOT MISSING end_session"
    print(f"  {name}: {tool_ids}{flag}")
```
ALL agents MUST have `end_session` listed in their `tools` array in the agent JSON config. Without it, the platform throws `Tool not found: end_session` when the agent or its callbacks try to end the session. This includes sub-agents -- even if they typically transfer back to root, the LLM or callbacks may call `end_session` (e.g., for escalation or silence handling).

## Gate 4: Callback inventory
```python
for name, resource in agents_map.items():
    cbs = cb_client.list_callbacks(resource)
    for cb_type, cb_list in cbs.items():
        if cb_list:
            count = len(cb_list)
            print(f"  {name}/{cb_type}: {count}")
```

## Gate 5: Single-turn smoke test
```python
import uuid
from cxas_scrapi.core.sessions import Sessions
sessions = Sessions(app_name=APP_NAME)
r = sessions.run(session_id=f"gate5-{uuid.uuid4().hex[:8]}", text="Hello")
sessions.parse_result(r)
# Must get a response, no callback crash
```

## Gate 6: Multi-turn smoke test
Test natural conversational pacing -- provide info ONE piece at a time, like a real caller:
```python
sid = f"gate6-{uuid.uuid4().hex[:8]}"
r1 = sessions.run(session_id=sid, text="<intent matching a CUJ from TDD>")
sessions.parse_result(r1)
# CHECK: agent should ask for ONE thing (e.g., DOB), not dump all questions at once

r2 = sessions.run(session_id=sid, text="<first piece of auth info, e.g., DOB>")
sessions.parse_result(r2)
# CHECK: agent should acknowledge and ask for the NEXT thing (e.g., ZIP), not two more

r3 = sessions.run(session_id=sid, text="<second piece, e.g., ZIP>")
sessions.parse_result(r3)
# CHECK: agent asks for ONE more thing (e.g., member ID)

r4 = sessions.run(session_id=sid, text="<third piece, e.g., member ID>")
sessions.parse_result(r4)
# CHECK: agent authenticates, then handles the original intent

r5 = sessions.run(session_id=sid, text="<specific question the sub-agent should handle>")
sessions.parse_result(r5)
# Must: route to sub-agent, call correct tool, return data
```
**If the agent asks for DOB + ZIP + ID in a single turn, STOP and fix the instruction before proceeding.** Add: `<rule>Ask for ONE piece of information per turn.</rule>`

**Only proceed to writing evals after ALL 6 gates pass.**
