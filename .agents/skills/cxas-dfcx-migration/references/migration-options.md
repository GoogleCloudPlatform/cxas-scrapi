# Migration Configuration Options Reference

Detailed documentation for each configuration parameter in the DFCX-to-CXAS migration.

## Source Agent

### Agent ID
- **Format:** `projects/<project_id>/locations/<location>/agents/<uuid>`
- **When to use:** When the source DFCX agent is deployed and accessible via the Conversational Agents API. Requires active `gcloud` authentication with appropriate permissions.

### Zip File
- **Format:** Local file path to a `.zip` export
- **When to use:** When you have a local export of the DFCX agent, or when the agent is not directly accessible via API (e.g., different project, restricted access). Export the agent from the Dialogflow CX console first.

## Configuration Parameters

### Google Cloud Project ID
- **Required:** Yes
- **Description:** The GCP project where the new CXAS agent will be created. This is the *target* project -- it may differ from the source agent's project.

### Target Agent Name
- **Default:** `migrated_agent_<YYYYMMDD_HHMMSS>`
- **Description:** The display name for the new CXAS application. This name appears in the CXAS console and is used as a prefix for exported files (reports, visualizations).

### Environment
- **Options:** `PROD`, `AUTOPUSH`
- **Default:** `PROD`
- **Description:**
  - `PROD` -- Standard production API endpoints. Use for all regular migrations.
  - `AUTOPUSH` -- Pre-production API endpoints for testing new platform features. Only use if specifically directed by the CXAS team.

### Global App Model
- **Options:**
  - `gemini-3.0-flash-001` -- Latest Flash model, best balance of speed and quality
  - `gemini-3.0-pro-001` -- Pro-tier model for complex reasoning tasks
  - `gemini-2.5-flash-001` -- Stable Flash model (default)
  - `gemini-2.5-flash-native-audio-preview` -- Flash with native audio support (preview)
  - `gemini-3-flash-native-audio` -- Flash with native audio support (GA)
- **Default:** `gemini-2.5-flash-001`
- **Description:** The Gemini model assigned to all agents in the migrated app. Individual agent models can be changed post-migration in the CXAS console. Choose audio-capable models if the source agent handles voice interactions.

### Logic Version
- **Options:** `1.0`, `2.0`
- **Default:** `2.0`
- **Description:**
  - `1.0` -- Legacy migration logic. Only supports Playbook-based agents. Use for simple agents with only Playbooks.
  - `2.0` -- Current migration logic. Supports Playbooks, Flows, and Hybrid (Playbooks + Flows) agents. **Required** for any agent containing Flows.
- **Important:** If your source agent has Flows and you select version 1.0, the Flows will be skipped.

### Generate Migration Report
- **Default:** `yes`
- **Description:** Produces a detailed Markdown report documenting the migration, including:
  - All converted tools and their mapping
  - All generated agents with descriptions
  - Parameter migrations
  - Warnings and issues encountered
  - The report is downloaded as `<target_name>_migration_report.md`.

### Generate Unit Tests (Auto-Fix)
- **Default:** `yes`
- **Description:** Automatically generates unit tests for the migrated tools and agents. When enabled, the migration system will also attempt to auto-fix common issues detected by the tests.

### Generate Hillclimbing Evals
- **Default:** `no`
- **Description:** Generates iterative optimization evaluations that progressively improve agent quality. These evals run the agent through scenarios and use the results to suggest instruction improvements. This is an advanced feature for post-migration optimization.

### Eval Target
- **Options:** `Custom API Runner`, `Native Product Eval (Stub)`
- **Default:** `Custom API Runner`
- **Description:**
  - `Custom API Runner` -- Uses the SCRAPI evaluation framework to run tests via the API. Full-featured with detailed reporting.
  - `Native Product Eval (Stub)` -- Placeholder for native CXAS evaluation integration. Limited functionality.

### Optimize for CXAS
- **Default:** `no`
- **Description:** Applies CXAS-specific optimizations to the generated instructions and agent configuration. When enabled, the migration may restructure instructions to better leverage CXAS-specific features like agent routing syntax, tool invocation patterns, and callback hooks.

## Resource Selection

### Playbooks
Playbooks are the primary building blocks in Playbook-based DFCX agents. Each playbook maps to a CXAS Agent with:
- Converted instructions (with `{@AGENT:}` and `{@TOOL:}` routing syntax)
- Linked tools and toolsets
- Model settings

### Flows
Flows are state-machine-based conversation logic in Flow-based DFCX agents. Each flow is processed through a multi-step AI pipeline:
1. **Step 2A:** Architecture blueprinting (analyzes flow structure)
2. **Step 2B:** Instruction generation (produces PIF XML instructions)
3. **Step 2C:** Tool and callback generation (creates Python tools and callbacks)

Each flow maps to a CXAS Agent with AI-generated instructions, tools, and callbacks.

## Migration Types

| Type | Selected Resources | Logic Version |
|------|-------------------|---------------|
| Pure Playbooks | Only Playbooks | 1.0 or 2.0 |
| Pure Flows | Only Flows | 2.0 required |
| Hybrid Agent | Playbooks + Flows | 2.0 required |

## Three-Script Flow

The skill is structured as three independently runnable scripts. State persists between them via a single `<target>_ir.json` bundle on disk.

### `migrate.py` — 1:1 conversion

Loads the source DFCX agent, prompts for project + location + target name (or accepts `--project-id` / `--location` / `--target-name` as overrides), runs the full `MigrationService.run_migration` pipeline (`optimize_for_cxas=False`), then persists the resulting `MigrationIR` to `<target>_ir.json`.

| Flag | Effect |
|------|--------|
| `--source-agent-id <id>` | Full DFCX resource name (mutually exclusive with `--zip-file`). |
| `--zip-file <path>` | Local DFCX export. |
| `--project-id <id>` | **Target** GCP project (prompted if omitted). |
| `--location <us\|eu\|global>` | **Target** location (default `us`; never default to `global`). |
| `--target-name <name>` | New CXAS app display name (prompted if omitted). |
| `--env <PROD\|AUTOPUSH>` | Deployment environment (default PROD). |
| `--model <gemini-…>` | Global model for the new app. |
| `--migration-version <1.0\|2.0>` | Logic version (default 2.0). |
| `--gen-report` / `--no-gen-report` | Generate `<target>_migration_report.md` (default yes). |
| `--gen-unit-tests` / `--no-gen-unit-tests` | Generate `<target>_unit_tests.json` (default yes). |
| `--no-preview-html` | Skip the pre-flight HTML preview. |
| `--preview-only` | Generate the HTML preview and exit (no migration). |
| `--export-svg` | Also call `MainVisualizer.export_visualizations`. |
| `--skip-resource-selection` | Migrate everything (skip multi-select picker). |
| `--skip-dependency-analysis` | Skip the dependency-analysis step. |
| `--yes` / `-y` | Non-interactive; accept defaults. |

### `stage1.py` — variable dedup + optional Gemini consolidation

Loads `<target>_ir.json`, runs `CXASOptimizer.optimize_stage1()` (variable deduplication) and optionally `StructuralConsolidator` (Gemini-driven N→M agent grouping + per-group PIF XML synthesis). Pushes via `is_update_pass=True` deploys. Creates CXAS Version `0.0.1`. Persists the updated IR back to disk.

| Flag | Effect |
|------|--------|
| `--ir-bundle <path>` | Path to `<target>_ir.json` (mutually exclusive with `--target-name`). |
| `--target-name <name>` | Resolves to `<target>_ir.json` in the cwd. |
| `--project-id <id>` / `--location <loc>` | Override the bundle values (rare). |
| `--no-consolidate` | Skip the Gemini consolidation; only run `optimize_stage1()`. |
| `--no-instruction-review` | Skip the per-group view/edit/re-synthesize step. |
| `--gemini-model <model>` | Model for the grouping proposal (default `gemini-3.1-pro-preview`). |
| `--grouping-json <path>` | Replay a previously persisted grouping. |
| `--yes` / `-y` | Non-interactive. |

### `stage2.py` — instruction state machines + tool mocks + lint + report

Loads `<target>_ir.json`, runs `CXASOptimizer.optimize_stage2()` (PLAYBOOK XML state-machine restructuring + Python tool `mock_mode` injection). Pushes via `is_update_pass=True` deploys. Creates CXAS Version `0.0.2`. Re-generates `<target>_unit_tests.json`. Runs `cxas pull` + `cxas lint`. Writes `<target>_optimization_report.md`. Persists the updated IR back to disk.

| Flag | Effect |
|------|--------|
| `--ir-bundle <path>` | Path to `<target>_ir.json`. |
| `--target-name <name>` | Resolves to `<target>_ir.json` in the cwd. |
| `--project-id <id>` / `--location <loc>` | Override the bundle values. |
| `--no-unit-tests` | Skip the deterministic unit test regeneration. |
| `--no-lint` | Skip post-deploy lint. |
| `--no-report` | Skip the audit markdown. |
| `--yes` / `-y` | Non-interactive. |

### Stage details

- **Stage 1 — Variable Deduplication.** `CXASOptimizer.optimize_stage1` scans every IR instruction, Python tool body, and `before_model_callback`/`after_model_callback` for variable references (`{var}`, `` `var` ``, `$var`, `get_variable("…")`, `state["…"]`, `payload[...]`, `kwargs[...]`). Asks Gemini to consolidate them into a smaller global vocabulary that fits CXAS's 95-variable cap, then rewrites the IR globally (parameters dict + agent instructions + tool descriptions + tool Python code + callbacks).
- **Stage 1 (consolidation, optional).** `StructuralConsolidator.propose_groupings` proposes 3–7 PascalCase journey-oriented groups, validates against the IR, and `consolidate(...)` collapses them. Per-group `synthesize_instructions(...)` runs `AsyncAgentDesigner` Step 2A (architecture blueprint) + Step 2B (PIF XML) — each call wrapped in `asyncio.wait_for(timeout=SYNTHESIS_TIMEOUT_S)` (default 600 s) so a single hung Gemini call no longer blocks the others.
- **Stage 2 — Instruction State Machines + Tool Mocks.** Two parallel passes on the (possibly consolidated) agents:
  1. `PLAYBOOK` agents have their instructions restructured into XML state machines via Gemini. Auto-detects use of `set_session_variables` and registers the helper tool on the fly.
  2. Each Python tool has a `mock_mode` happy-path branch injected, using the *calling agents'* instructions + callbacks as context so the mocks return realistic data.

Both stages emit `optimization_logs` that flow into `<target>_ir.json` (under `ir.optimization_logs.stages`) and into `<target>_optimization_report.md`.

## IR bundle (`<target>_ir.json`)

Pydantic `IRBundle` written by `migrate.py` and updated in place by `stage1.py` / `stage2.py`. Schema:

| Field | Type | Description |
|---|---|---|
| `schema_version` | str | "1" — for forward-compat. |
| `created_at` | datetime | When the bundle was first written. |
| `config` | `MigrationConfig` | Original migration config (project, target name, env, model, etc.). |
| `source_agent_data` | `DFCXAgentIR` | The full source DFCX agent — needed by Stage 2 for tool-mock context. |
| `ir` | `MigrationIR` | The current target IR. Mutated by every stage. |
| `stage_history` | list of entries | One entry per `migrate` / `stage1` / `stage2` run with status, timing, and notes. |
| `app_url` | str \| null | CXAS console URL of the deployed app. |
| `version_checkpoints` | list of (display_name, description) | CXAS Version snapshots created (`0.0.1` after Stage 1, `0.0.2` after Stage 2). |
| `grouping` | dict \| null | Set when Stage 1 ran consolidation; same content as `<target>_grouping.json`. |

Killing a stage script mid-run leaves the bundle untouched (only persisted on success). Re-running picks up where the last successful stage left off.

## Environment variables

| Variable | Purpose |
|---|---|
| `SYNTHESIS_TIMEOUT_S` | Per-group synthesis timeout (default 600 s). |
| `EDITOR` | Editor used by Stage 1's `[e]dit` instruction review. Falls back to `vi`. |

## Artifacts produced

`migrate.py`:
- `<target>_ir.json` — IR bundle (input for `stage1.py`).
- `<target>_migration_report.md` — when `--gen-report` (default yes).
- `<target>_unit_tests.json` — when `--gen-unit-tests` (default yes).
- `<target>_tree_preview.html`, `<target>_topology.mmd`, `<target>_tools.mmd` — pre-flight preview.
- `<target>_topology.svg`, `<target>_detailed_resources.md` — when `--export-svg`.

`stage1.py`:
- Updated `<target>_ir.json` with Stage 1 logs and (if consolidate) the consolidated agents.
- `<target>_grouping.json` — when consolidation runs.
- CXAS Version `0.0.1`.

`stage2.py`:
- Updated `<target>_ir.json` with Stage 2 logs.
- Regenerated `<target>_unit_tests.json` against the final agents.
- `<target>_optimization_report.md` — full audit.
- CXAS Version `0.0.2`.
