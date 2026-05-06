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

## Gates 2-6: run them via gate-check.py

```bash
# Gates 1-5 (Gate 6 skipped by default — needs prompts file)
python .agents/skills/cxas-agent-foundry/scripts/gate-check.py

# Include Gate 6 by passing a prompts file
python .agents/skills/cxas-agent-foundry/scripts/gate-check.py \
  --multi-turn /tmp/gate6-prompts.json

# Skip Gate 1's push round-trip (verify only, don't modify the platform)
python .agents/skills/cxas-agent-foundry/scripts/gate-check.py --skip-push
```

The script prints a per-gate pass/fail and writes a JSON summary to `<project>/eval-reports/gate-check-<timestamp>.json` for sub-agent consumption. Read the JSON for structured findings; read the stdout for a human-readable trace.

`--multi-turn <file.json>` accepts a JSON list of prompts to run sequentially in one session. Example:
```json
[
  {"text": "I need help with my account"},
  {"text": "July 12, 1948"},
  {"text": "30033"},
  {"text": "H123456"},
  {"text": "Why was my last claim denied?"}
]
```

**The gate semantics (what each gate checks and why) are described below.** The script implements all of these — read this section if a gate fails and you need to understand the failure, or if you need to extend the script.

### Gate 2: Agent hierarchy
Verifies `app.root_agent` is set and that every agent listed by `get_agents_map()` is reachable. Fails if the app has no root agent or if `root_agent` points at a name that doesn't appear in the agents list.

### Gate 3: Tool associations (including system tools)
Lists each agent's tools and warns if **any** agent (root or sub) is missing `end_session`. ALL agents MUST have `end_session` in their `tools` array. Without it, the platform throws `Tool not found: end_session` when the agent or its callbacks try to end the session — even sub-agents, which may call `end_session` for escalation or silence handling.

### Gate 4: Callback inventory
Counts callbacks per agent per type. Informational — there's no failure condition (callbacks are optional), but the inventory should match what's documented in the TDD.

### Gate 5: Single-turn smoke test
Sends "Hello" and confirms the agent responds without a callback crash.

### Gate 6: Multi-turn smoke test
Test natural conversational pacing -- provide info ONE piece at a time, like a real caller. The script runs each prompt sequentially in one session and reports any errors. **You must still read the printed agent responses** to confirm pacing — the script can't tell whether the agent asked for DOB + ZIP + ID in one turn (which would be wrong) or asked for them one at a time (correct).

**If the agent asks for DOB + ZIP + ID in a single turn, STOP and fix the instruction before proceeding.** Add: `<rule>Ask for ONE piece of information per turn.</rule>`

**Only proceed to writing evals after ALL 6 gates pass.**
