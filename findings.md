# Findings

## PRD Promise Extraction
- The PRD promises a build-time, additive SCRAPI V1 with:
  - Channel Adapter Card files under `adapters/`.
  - A Polymorphism Engine and `cxas poly build` that produce ordinary SCRAPI
    projects per channel.
  - Explicit adapter deltas for instructions, channel-only tools, model
    overrides, callbacks, deployment, and channel evals.
  - Evaluation composition: base evals plus channel evals with coverage signal.
  - Auditable compilation: deviations from the base are visible and reviewable.
  - A Bella Notte reference example as the primary launch artifact.
  - A design-partner workflow that can reveal schema expressiveness,
    compilation correctness, and authoring ergonomics gaps.

## Implementation Inventory
- Present:
  - `src/cxas_scrapi/poly/models.py` defines strict adapter-card Pydantic
    models for instruction diffs, tool changes, tool definitions, model
    overrides, callbacks, eval refs, config overlays, and deployment.
  - `src/cxas_scrapi/poly/validators.py` provides AD001-AD010 validation and
    backs the linter/engine contract.
  - `src/cxas_scrapi/poly/engine.py` compiles one adapter into a complete
    SCRAPI output directory, copies the base project, applies deltas, and writes
    a `.poly_build.json` marker.
  - `src/cxas_scrapi/cli/poly_cli.py` exposes `init`, `build`, `validate`,
    `doctor`, and `diff`.
  - `src/cxas_scrapi/poly/diffing.py` emits stable JSON plus human adapter
    deltas for review.
  - `src/cxas_scrapi/poly/scaffold.py` supports conservative adapter
    scaffolding with optional eval/tool/callback starters.

## Gap Matrix
- Promise: deterministic adapter-card build to ordinary SCRAPI outputs.
  Status: mostly met. `engine.py`, `poly_cli.py`, and hardening tests cover
  build, safe output writing, and lintable compiled projects.
- Promise: explicit, auditable channel deviations.
  Status: partially met. `cxas poly diff` exposes deltas, but there is no single
  launch/readiness report that rolls diff, validation, eval coverage, and
  compileability into a design-partner artifact.
- Promise: evaluation composition with coverage signal.
  Status: partially met. The engine merges channel eval directories and AD006
  warns when an adapter has no eval entries, but no report distinguishes "has an
  eval sourceDir" from launch-quality channel coverage or flags channel eval
  names that collide with base evals before write time.
- Promise: Bella Notte as primary reference example.
  Status: mostly met. Bella Notte has chat and voice adapters, docs, callbacks,
  rich card tooling, and evals. It would benefit from a pre-build readiness step
  in the documented workflow.
- Promise: structured design-partner learning loop.
  Status: gap. There is no command that design partners can run to capture the
  health of a polymorphic project before build/lint/eval/deploy.

## Improvement Candidates
- Early candidates:
  - Add an explicit launch/readiness report that checks the PRD's adoption
    dimensions: per-adapter eval coverage, diff reviewability, buildability,
    compiled lint readiness, and generated next steps.
  - Strengthen coverage diagnostics beyond AD006: distinguish "has an eval
    directory" from "has eval coverage matching meaningful channel deltas."
  - Add machine-readable audit metadata to build outputs so compiled projects
    carry source adapter/card lineage and change summaries.
  - Improve Bella Notte walkthrough docs around design-partner workflow and
    "do not hand-edit compiled output" launch behavior.

## Selected Slice
- Implement `cxas poly readiness`:
  - Text/JSON report with schema version `poly-readiness/v1`.
  - Per-channel status: `ready`, `attention`, or `blocked`.
  - Existing AD validation issues, duplicate channel issues, and compileability.
  - Diff summary from the existing `poly-diff/v1` report builder.
  - Eval coverage counts and duplicate eval/expectation/dataset names between
    the base and channel-specific additions.
  - Actionable next steps that point users back to validate/doctor, channel
    eval authoring, build, lint, and eval.
