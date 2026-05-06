# Build & Edit Lifecycle

Build, evaluate, and edit GECX conversational agents.

| You have... | Jump to |
|---|---|
| Requirements but no app | [Full Build](#full-build) (gates 0-6) |
| Existing app, no evals | [Eval Creation](#eval-creation-existing-app) |
| Existing app + evals, want to change something | [Editing an Existing Agent](#editing-an-existing-agent) |
| Want a sandboxed copy to experiment in | [Branching for Development Sandboxes](#branching-for-development-sandboxes) |
| Existing app + evals, want to debug failures | `references/debug.md` (not this file) |

---

## Full Build

### Mandatory Sequence

Each gate must be satisfied before the next. Skipping is a deviation, not an optimization.

| Gate | What | How to verify |
|---|---|---|
| **0** | `<project>/todo.md` initialized from the Build Steps checklist below | File exists, lists every step |
| **1** | Requirements gathered (from sources like PRD/spec/sample conversations/profiles, from interview per `references/interview-guide.md` Round 1, or both) | Explicit answers to agent purpose, modality, intents, tools, auth flow surfaced in the conversation |
| **2** | `agents/tdd-writer.md` dispatched; main thread runs the user-approval loop | `<project>/tdd.md` exists AND user explicitly approved |
| **3** | `agents/scaffolder.md` dispatched against the approved TDD | Scaffolder returns `status: complete` |
| **4** | `agents/lint-fixer.md` dispatched (Zero Warnings Policy: errors AND deterministic warnings fixed; ambiguous warnings → `unresolved`) | Sub-agent returns `status: clean` |
| **5** | `agents/eval-writer.md` dispatched once per eval type — see eval-writer's "File layout per type" | Every Coverage Map CUJ appears as an entry; max 4 eval-writer dispatches |
| **6** | `cxas push` exits 0 AND `python scripts/gate-check.py` exits 0 | `gate-check.py` exits 0 |

**Discipline:**
- **Anti-fabrication:** don't claim "deployed", "linted clean", or "smoke test passed" without verifying. If a tool exited non-zero, the work isn't done.
- **Sub-agent stuck:** read the `unresolved` / `Reason` field, surface judgment calls to the user, re-dispatch with their answer. Don't pull the work back inline.

### Build Steps (todo.md template)

1. Gather requirements (gate 1)
2. TDD + user approval (gate 2)
3. Scaffold app (gate 3)
4. Lint clean (gate 4)
5. Generate evals — one eval-writer dispatch per type (gate 5)
6. Push + verify (gate 6)

### Gather Requirements (gate 1)

Collect available artifacts (PRD/spec, sample conversations, mock data, customer profiles, reference agent). For pastes longer than ~1 page, save to `<project>/sources/<short-name>.md` first so the dispatch prompt stays compact. Surface a one-message summary of agent purpose, modality, intents, tools, and auth flow before checking the box.

For interview structure when requirements are missing, see `references/interview-guide.md`.

### TDD Approval (gate 2)

Dispatch `agents/tdd-writer.md` with `output_path: <project>/tdd.md` plus `sources: [{path, description}, ...]`. The sub-agent returns the TDD content + an "Open questions" handoff. Show that to the user, ask for approval, and re-dispatch with change requests until approved.

**If you sent the TDD once and didn't loop, you skipped this gate.** A single tdd-writer dispatch with no follow-up doesn't satisfy gate 2 — the user must explicitly approve.

### Scaffold (gate 3)

Dispatch `agents/scaffolder.md` with `tdd_path: <project>/tdd.md` and `app_dir: <project>/cxas_app/<AppName>/`. Wait for the manifest.

**Verify the manifest before moving on.** Check that the file counts match the TDD's expectations — if the TDD's Architecture lists 7 sub-agents and the manifest shows 5 written, something's missing. If `status: incomplete`, re-dispatch for the missing files only (don't author them inline).

### Generate Evals (gate 5)

**Plan before writing.** Propose a coverage plan to the user and get approval before dispatching anything.

Then dispatch `agents/eval-writer.md` once per eval type (4 types: goldens, sims, tool_tests, callback_tests) with `eval_type` and `tdd_path`. The sub-agent owns the rest.

### Push (gate 6)

**Do not push if gate 4 didn't return `status: clean`.** Pushing with unresolved lint errors fails with unhelpful platform errors (`400 Reference not found`-class).

```bash
cxas push --app-dir <project>/cxas_app/<AppName> \
  --to projects/<project_id>/locations/<location>/apps/<app_id>
```

(First-time push only: replace `--to` with `--display-name "My App"` — `cxas push` auto-creates. `configure.py` normally pre-creates the app, so `--to` is the standard case.)

Then verify: `python .agents/skills/cxas-agent-foundry/scripts/gate-check.py` (or `--skip-push` for a faster smoke check).

### Run Baseline (post-gate-6)

```bash
python .agents/skills/cxas-agent-foundry/scripts/run-and-report.py --message "Initial baseline" --runs 3
```

**Don't claim "baseline run" without checking the report.** Read `<project>/eval-reports/iterations/<latest>` and surface pass rates and any platform errors to the user — the script can succeed with all evals failing.

After the run, update the TDD's Pass Rate History and Changelog — see `references/tdd-guide.md` → "Keeping the TDD Current".

---

## Eval Creation (existing app)

For an existing platform app with no local TDD or evals. Skips gates 1-4 (no requirements gathering, no scaffolding); reverse-engineer the TDD, bootstrap evals from the platform, fill the gaps.

### Build Steps (todo.md template)

1. `cxas pull` to local (idempotent — always do this first; `tdd-writer` reverse mode reads local code):
   ```bash
   cxas pull projects/$PROJECT_ID/locations/$LOCATION/apps/$APP_ID \
     --project-id $PROJECT_ID --location $LOCATION --target-dir <project>/cxas_app/
   ```
2. `python scripts/inspect-app.py` — share summary with user
3. Dispatch `agents/tdd-writer.md` with `app_dir: <project>/cxas_app/<AppName>/` (auto-detects reverse mode); show the handoff to the user, ask for approval, re-dispatch on changes
4. `python scripts/bootstrap-evals.py` — exports platform goldens (with stripped expectations), generates tool test skeletons with mined args, syncs callbacks, writes a sim skeleton
5. Dispatch `agents/eval-writer.md` per type to fill the gaps — author expectations on bootstrapped goldens, add new goldens for uncovered Coverage Map behaviors, tighten tool test paths, write callback `test.py` files, write sims from scratch
6. `run-and-report.py` — pushes goldens and runs the baseline

---

## Editing an Existing Agent

The standard edit-test cycle for an agent that's already on the platform:

1. **Pull** the latest platform state to local:
   ```bash
   cxas pull projects/$PROJECT_ID/locations/$LOCATION/apps/$APP_ID \
     --project-id $PROJECT_ID --location $LOCATION --target-dir <project>/cxas_app/
   ```
2. **Edit** local files in `<project>/cxas_app/`
3. **Lint** — dispatch `agents/lint-fixer.md`; wait for `status: clean`
4. **Push**:
   ```bash
   cxas push --app-dir <project>/cxas_app/<AppName> \
     --to projects/$PROJECT_ID/locations/$LOCATION/apps/$APP_ID
   ```
5. **Run evals** to verify no regressions:
   ```bash
   python .agents/skills/cxas-agent-foundry/scripts/run-and-report.py --message "Describe what changed and why" --runs 5
   ```

For structural edits (new agent, new tool, new `childAgents` entry), also run `python scripts/gate-check.py` after step 5 — eval results don't surface platform-side issues like dropped sub-agents or orphaned tools.

Always pull before editing — pushing without pulling first overwrites whatever's on the platform.

---

## Branching for Development Sandboxes

To develop in isolation:

```bash
cxas branch "projects/$PROJECT_ID/locations/$LOCATION/apps/$APP_ID" \
  --new-name "my-dev-sandbox" \
  --project-id $PROJECT_ID --location $LOCATION
```

Pulls the source app, creates a new app, and pushes the content. To merge back, follow the [Editing an Existing Agent](#editing-an-existing-agent) flow against the original app's resource.

---

## Additional references

- **TDD structure and maintenance**: `references/tdd-guide.md`
- **Architecture, folder structure, conventions**: `references/gecx-design-guide.md`
- **Callback runtime API**: `references/callback-api.md`
- **Interview / gathering requirements**: `references/interview-guide.md`
- **Verification gates after building**: `references/build-verification.md`
- **Eval YAML format and patterns**: `references/eval-templates.md`
- **SCRAPI API calls**: `references/api-reference.md`
- **Project template (copy this for new projects)**: `assets/project-template/`
