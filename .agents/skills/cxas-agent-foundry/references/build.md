# Agent Foundry

Build GECX conversational agents from requirements and verify them with evals. This skill handles two workflows:

1. **Full build** -- PRD/requirements -> app + agents + tools + evals -> initial test run
2. **Eval creation** -- existing app -> inspect -> generate goldens/scenarios/simulations

Both workflows use the `cxas-scrapi` library for API interactions and the skill's scripts for running evals. **Prerequisite:** Ensure the virtualenv is set up and activated (`source .venv/bin/activate`). The top-level SKILL.md handles this automatically via the Environment Readiness Check.

### Load additional references as needed:
- **Architecture, folder structure, conventions**: `references/gecx-design-guide.md`
- **Writing callback Python code**: `references/callback-api.md`
- **Interview / gathering requirements**: `references/interview-guide.md`
- **Running verification gates after building**: `references/build-verification.md`
- **Writing eval YAML files**: `references/eval-templates.md`
- **Making SCRAPI API calls**: `references/api-reference.md`
- **Starting a new project from scratch**: `assets/project-template/`

## Table of Contents

- [Entry Point Detection](#entry-point-detection)
- [Interview](#interview)
- [Inspect App](#inspect-app)
- [Build App](#build-app)
- [Generate Evals](#generate-evals)
- [Push and Run](#push-and-run)

## Entry Point Detection

Determine which workflow based on what the user provides:

- **Has requirements but no app** -> Full build (start at Interview)
- **Has an existing app but no evals** -> Eval creation (start at Inspect App)
- **Has an existing app with evals** -> Bootstrap (start at Inspect App, then pull existing evals from the platform before generating new ones -- see `references/debug.md` -> "Bootstrap from existing agent" for the full flow)
- **Has an existing app and wants to edit it** -> Edit cycle (skip to Editing an Existing Agent)
- **Unclear** -> Ask: "Do you already have an agent app, or are we building from scratch? If you have an app, do you already have evals on it?"

## Build Steps

Choose the checklist that matches your entry point and initialize `todo.md`:

**Full build** (requirements -> app -> evals):
1. Interview
2. TDD
3. Build App
4. Generate Evals: Goldens
5. Generate Evals: Simulations
6. Generate Evals: Tool Tests
7. Generate Evals: Callback Tests
8. Push and Run

**Eval creation** (existing app -> evals):
1. Inspect App
2. Generate TDD (from inspection)
3. Generate Evals: Goldens
4. Generate Evals: Simulations
5. Generate Evals: Tool Tests
6. Generate Evals: Callback Tests
7. Push and Run

---

## Interview

Gather requirements, write a Technical Design Document (TDD), then wait for user approval before building.

**If the user already provided a comprehensive PRD or requirements document** (with intents, tools, CUJs, auth flows, etc.), skip the interview entirely. Read the document, then go directly to writing the TDD. The interview exists for users who don't have formal requirements -- don't make users who already did the work repeat it.

**If requirements are incomplete or absent**, see `references/interview-guide.md` for the full interview process: Round 1 (big picture), Round 2 (TDD structure with eval design), golden-vs-scenario decision criteria, golden design principles, and TDD maintenance guidance.

---

## Inspect App

When working with an existing app, inspect it to understand the architecture before creating evals:

```bash
python .agents/skills/cxas-agent-foundry/scripts/inspect-app.py            # Summary
python .agents/skills/cxas-agent-foundry/scripts/inspect-app.py --verbose   # Include instructions + callback code
python .agents/skills/cxas-agent-foundry/scripts/inspect-app.py --json      # Machine-readable output
```

This dumps agents (with tools and callbacks), existing evals, and scoring thresholds. Review the output to understand routing logic, which variables the callback derives vs needs as overrides, and what evals already exist. Share the summary with the user before creating evals.

---

## Build App

When building from scratch, start by copying the project template to a new named folder:

```bash
cp -r .agents/skills/cxas-agent-foundry/assets/project-template/ <project_name>/
echo "<project_name>" > .active-project
```

The template includes a sample 2-agent app with best-practice instructions, callbacks (trigger pattern, text injection, auth derivation), example evals, and a TDD skeleton. Use it as a reference -- adapt the patterns to the user's requirements, don't copy verbatim.

After copying, set the model in `gecx-config.json` and `cxas_app/<AppName>/app.json` based on the user's modality choice:
- **Audio/voice**: `gemini-3.1-flash-live`, `modality: "audio"`, `default_channel: "audio"`
- **Text**: `gemini-3-flash`, `modality: "text"`, `default_channel: "text"`

Then create the app on the platform and push the local files:

```python
from cxas_scrapi.core.apps import Apps

# Create the app (one-time)
apps = Apps(project_id=project, location=location)
app = apps.create_app(app_id=app_name, display_name=display_name, ...)
```

**CRITICAL: ALWAYS run `cxas lint` BEFORE `cxas push`.** If the app has structural issues (like missing tools, unreferenced agents, or schema errors), `cxas push` will fail with an unhelpful API error (e.g., `400 Reference not found`). You MUST fix ALL lint errors before attempting to push.

```bash
# 1. Lint the app (MUST do this first)
cxas lint --app-dir <project>/cxas_app/<AppName>

# 2. Push the entire app (agents, tools, callbacks) from local files
cxas push --app-dir <project>/cxas_app/<AppName> \
  --to <app_resource_name> \
  --project-id <project_id> --location <location>
```

**Always use `cxas push` as the single source of truth** for agents, tools, and callbacks. Do NOT use `create_tool`, `create_agent`, or `create_callback` APIs -- these create resources that get overwritten or orphaned on the next push.

**Tool format**: Tool JSON must use the platform format with `pythonFunction.name`, `pythonFunction.description`, and `executionType`. Use **snake_case** for `name` and `displayName` -- agent JSON references tools by `displayName` and mismatches cause push failures. Tools access session state via the `context` global (NOT as a function parameter). Do NOT use `**kwargs` in tool function signatures -- GECX requires explicit named parameters to generate the tool schema. Tools with `**kwargs` are silently dropped during import with no error. See `api-reference.md` -> Tools for details.

**Callback imports**: The GECX sandbox auto-provides `Part`, `Content`, `LlmResponse`, `LlmRequest`, `CallbackContext` as globals -- do NOT import them. But `typing` types (`Optional`, `Iterator`, `List`, `Dict`) must be explicitly imported. Missing imports cause errors at push time.

**Callback API**: See `references/callback-api.md` for the full API surface, factory methods, and common patterns. Do NOT use raw constructors or guess at attribute names.

### Required Patterns (lint-enforced)

The linter (`cxas lint`) checks for these patterns. Code that violates them will fail lint and block the push. Write code that passes these from the start:

| Rule | What it checks | How to comply |
|------|---------------|---------------|
| **A006** | `app.json` must explicitly list all local tools in its `tools` array | Add every tool in the `tools/` directory to the `tools` array in `app.json` |
| **S001** | `variableDeclarations` in `app.json` must include `name`, `description`, and `schema` | Always include `"description": "..."` on every variable |
| **I002** | `<taskflow>` must contain `<step>` or `<subtask>` children | Group related steps inside `<subtask name="...">` elements within `<taskflow>`. Use `<step name="...">` inside each subtask. Do NOT use custom tag names like `<priority_1>`, `<billing>`, `<eligibility>` |
| **I012** | Agent config lists a tool but instruction never references it | Reference every tool in the instruction using `{@TOOL: tool_name}` format (e.g., `Call {@TOOL: lookup_benefits} with...`) |
| **T001** | Tool Python function must return `agent_action` in error responses | Every error return path must include `"agent_action": "Tell the LLM what to do"` |
| **T009** | Tool function uses `**kwargs` | Use explicit named parameters -- GECX requires them to generate the tool schema. Tools with `**kwargs` are silently dropped during import |
| **T010** | Tool Python file has invalid syntax | Fix the syntax error -- invalid Python causes tools to be silently dropped during import |
| **T011** | Tool parameter uses `None` as default | Use type-matching defaults (e.g., `str = ""`, `int = 0`). `None` defaults cause the platform to silently drop the tool during import |
| **T012** | Tool JSON must include `pythonFunction.description` | Add a description to `pythonFunction` |
| **T013** | Tool JSON must include `pythonFunction.pythonCode` | Add `"pythonCode": "tools/<name>/python_function/python_code.py"` to `pythonFunction` |

### Multi-Agent Routing (CRITICAL)

When creating a multi-agent hierarchy, the parent agent MUST declare all sub-agents in its `childAgents` array inside its `<agent_name>.json` config file.

**CRITICAL RULE:** The strings in the `childAgents` array MUST use underscores (e.g., `"member_benefits_agent"`). The GECX platform matches these references against the agent's exact directory name.

If you use spaces in the `childAgents` array, the platform will drop the sub-agents and all of their tools will be orphaned!

Example `root_agent.json`:
```json
{
  "name": "root_agent",
  "displayName": "root_agent",
  "instruction": "agents/root_agent/instruction.txt",
  "childAgents": [
    "member_benefits_agent",
    "claims_agent"
  ]
}
```

After pushing:
1. Update `<project>/gecx-config.json` with the `deployed_app_id` if this is a new app
2. Run the Quick Verification below to confirm everything works
3. All subsequent edits should be made to local files in `<project>/cxas_app/`, then re-pushed

### Quick Verification (first-time builds)

For a first build, run these 2 essential checks instead of the full 6-gate process:

1. **Smoke test** -- Send "Hello" via Sessions API and confirm the agent responds without errors:
   ```python
   from cxas_scrapi.core.sessions import Sessions
   import uuid
   sessions = Sessions(app_name=APP_NAME)
   r = sessions.run(session_id=f"smoke-{uuid.uuid4().hex[:8]}", text="Hello")
   sessions.parse_result(r)
   ```
2. **Inspect** -- `python .agents/skills/cxas-agent-foundry/scripts/inspect-app.py` (confirm agents, tools, callbacks all exist and match the TDD)

For the full 6-gate verification (recommended before writing production evals), see `references/build-verification.md`.

---

## Generate Evals

**CRITICAL -- PLAN BEFORE WRITING:** If you are generating the initial batch of evaluation YAML files from the TDD, you MUST first propose a coverage plan to the user and get approval before writing files. This ensures coverage consistency across all eval types.

Create eval files for each type. See `references/eval-templates.md` for full YAML templates, code patterns, and examples.

**Before writing any eval:** read the `before_agent_callback` source code to identify which variables it derives from profile identifiers (e.g., auth status, user role, device type). Only override variables the callback can't derive -- overriding derived variables skips API calls and breaks downstream logic.

### 1. Golden YAML (`<project>/evals/goldens/*.yaml`)

Turn-by-turn ideal conversations. See `references/eval-templates.md` -> Golden YAML Template for the full format, `$matchType` directives, golden design rules, and multi-agent considerations.

### 2. Simulation YAML (`<project>/evals/simulations/simulations.yaml`)

Sim-user-driven open-ended tests. See `references/eval-templates.md` -> Simulation YAML Template for format, expectation writing, and audio considerations.

### 3. Tool Tests (`<project>/evals/tool_tests/*.yaml`)

Before writing any tool test expectations, READ each tool's `python_function/python_code.py` to find the exact keys in the return dict. See `references/eval-templates.md` -> Tool Tests for the YAML format, code patterns, and the required workflow.

### 4. Callback Tests (`<project>/evals/callback_tests/`)

Test callbacks in isolation using pytest. Use `sync-callbacks.py` to pull code from platform, then write tests. See `references/eval-templates.md` -> Callback Tests for layout, patterns, and what to test per callback type.

---

## Push and Run

After generating evals, push goldens and run the initial baseline:

```bash
# Run everything + generate baseline report and experiment log (pushes goldens automatically)
python .agents/skills/cxas-agent-foundry/scripts/run-and-report.py --message "Initial baseline" --runs 3
```

The script reads the channel from `gecx-config.json` automatically. Only pass `--channel` to override (e.g., `--channel audio`).

The `run-and-report.py` script snapshots the agent state, runs all eval types, triages results, and generates an iteration report to `<project>/eval-reports/iterations/`. It also creates/updates `<project>/experiment_log.md` to track what was tried.

After the run, update the TDD:
- Fill in the **Pass Rate History** table with the baseline results
- Update the **Changelog** with the date and "Initial baseline"

---

## Editing an Existing Agent

The standard edit-test cycle for an agent that's already on the platform:

1. **Pull** the latest platform state to local:
   ```bash
   cxas pull projects/$PROJECT_ID/locations/$LOCATION/apps/$APP_ID \
     --project-id $PROJECT_ID --location $LOCATION --target-dir <project>/cxas_app/
   ```
2. **Edit** local files in `<project>/cxas_app/` (instructions, callbacks, tools)
3. **Lint** to catch issues before pushing:
   ```bash
   cxas lint --app-dir <project>/cxas_app/<AppName>
   ```
4. **Push** changes back to the platform:
   ```bash
   cxas push --app-dir <project>/cxas_app/<AppName> \
     --to projects/$PROJECT_ID/locations/$LOCATION/apps/$APP_ID \
     --project-id $PROJECT_ID --location $LOCATION
   ```
5. **Run evals** to verify the change didn't cause regressions:
   ```bash
   python .agents/skills/cxas-agent-foundry/scripts/run-and-report.py --message "what changed" --runs 5
   ```

**Always pull before editing** if someone else may have made changes on the platform UI. Pushing without pulling first can overwrite their changes.

## Branching for Development Sandboxes

To develop in isolation without affecting the main app, use `cxas branch` to create a copy:

```bash
cxas branch "projects/$PROJECT_ID/locations/$LOCATION/apps/$APP_ID" \
  --new-name "my-dev-sandbox" \
  --project-id $PROJECT_ID --location $LOCATION
```

This pulls the source app, creates a new app, and pushes the content. Develop and test against the branch, then merge changes back by editing the original app's local files and pushing.

