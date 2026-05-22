# PLAN.md

## Goal
Review the current SCRAPI polymorphic-agent implementation against
`/Users/andrew/Downloads/Polymorphic_Agent_Architecture_Product_Proposal.md`,
identify gaps between the implementation and the PRD promises, select a narrow
set of high-leverage improvements, implement and verify them, and prepare a PR
with meaningful product progress.

## Context
- Repo root: `/Users/andrew/.codex/worktrees/c72c/cxas-scrapi-polymorphic`
- Current git state: detached `HEAD`, clean at start.
- PRD: `/Users/andrew/Downloads/Polymorphic_Agent_Architecture_Product_Proposal.md`
- Relevant implementation:
  - `src/cxas_scrapi/poly/models.py`
  - `src/cxas_scrapi/poly/validators.py`
  - `src/cxas_scrapi/poly/engine.py`
  - `src/cxas_scrapi/poly/diffing.py`
  - `src/cxas_scrapi/poly/diagnostics.py`
  - `src/cxas_scrapi/poly/scaffold.py`
  - `src/cxas_scrapi/cli/poly_cli.py`
  - `src/cxas_scrapi/utils/lint_rules/adapters.py`
- Relevant docs and examples:
  - `README.md`
  - `docs/cli/poly.md`
  - `docs/guides/polymorphism.md`
  - `docs/patterns/polymorphism.md`
  - `examples/bella_notte/**`
  - `examples/polymorphic_pizza/**`
- Relevant tests:
  - `tests/cxas_scrapi/poly/**`

## Constraints
- Preserve the repo's existing contract: polymorphism is build-time only, and
  compiled output must remain an ordinary SCRAPI project.
- Do not invent unsupported adapter card fields unless intentionally extending
  the schema with tests, validation, engine behavior, and docs.
- Do not hand-edit compiled output directories; rebuild from base projects plus
  adapter cards.
- Do not fetch, push, or deploy until the implementation is ready and the PR
  publishing step requires it.
- Use small, additive, testable changes. Avoid unrelated refactors and metadata
  churn.
- Every behavior change needs focused tests.

## Milestones

### Milestone 1 - PRD Gap Review
Extract the PRD's concrete promises, map them to current code/docs/examples,
and record gaps by severity and launch value in `findings.md`.

### Milestone 2 - Improvement Selection And Design
Choose a bounded improvement set that materially advances V1 launch readiness,
state the reasoning and trade-offs, and update this plan with exact files and
verification commands.

Selected slice: add `cxas poly readiness`, a pre-launch design-partner report
that composes current validation, adapter diff summaries, eval coverage,
duplicate eval-name detection, and compileability into one human/JSON surface.
This fills the PRD gap around launch evidence and workflow ergonomics without
changing the build-time-only adapter contract.

### Milestone 3 - Implementation
Implement the selected improvements in the smallest coherent slice, including
tests and docs/examples where behavior or workflow changes.

### Milestone 4 - Verification
Run targeted unit tests, CLI smoke checks against the examples, and diff
cleanliness checks. Record all results in `progress.md`.

### Milestone 5 - Branch, Commit, And PR
Create/switch to a `codex/` branch, commit one logical change using Conventional
Commits, push, and open a PR with the gap analysis and verification summary.

## Verification Commands
Initial expected commands from the repo root; update after Milestone 2 if the
selected improvement changes the surface area.

```bash
git diff --check
uv run --with-editable . --with alive-progress ruff check src/cxas_scrapi/poly src/cxas_scrapi/cli/poly_cli.py tests/cxas_scrapi/poly
uv run --with-editable . --with alive-progress pytest tests/cxas_scrapi/poly -q
uv run --with-editable . --with alive-progress cxas poly validate --app-dir examples/bella_notte
uv run --with-editable . --with alive-progress cxas poly doctor --app-dir examples/bella_notte
uv run --with-editable . --with alive-progress cxas poly diff chat --app-dir examples/bella_notte --json
uv run --with-editable . --with alive-progress cxas poly build --app-dir examples/bella_notte --output-dir /tmp/poly_prd_alignment_build
uv run --with-editable . --with alive-progress cxas lint --app-dir /tmp/poly_prd_alignment_build/chat
uv run --with-editable . --with alive-progress cxas lint --app-dir /tmp/poly_prd_alignment_build/voice
```

## Acceptance Criteria
- `findings.md` contains a concrete PRD-to-implementation gap matrix.
- The implemented slice addresses one or more high-value V1 launch gaps rather
  than cosmetic cleanup.
- Tests cover the changed behavior and describe user-visible outcomes.
- Docs/examples explain the improved workflow accurately.
- Targeted verification commands pass or any remaining failures are documented
  with a clear reason.
- A PR exists with a concise summary, gap rationale, and verification evidence.

## Progress
- [x] Milestone 1 complete
- [x] Milestone 2 complete
- [x] Milestone 3 complete
- [x] Milestone 4 complete
- [x] Milestone 5 complete

## Decision Log
- 2026-05-22 01:55 - Treat the PRD as the primary source of truth for launch
  promises and select an implementation slice only after mapping those promises
  to current code, docs, examples, and tests.
- 2026-05-22 02:03 - Select a readiness report over deeper schema expansion:
  the schema/compiler already cover V1 primitives, while the PRD's design
  partner launch plan lacks a single artifact that proves validation, coverage,
  auditability, and compileability before build/deploy.

## Notes / Blockers
- Verification completed:
  - `git diff --check`
  - `uv run --with-editable . --with alive-progress ruff check src/cxas_scrapi/poly src/cxas_scrapi/cli/poly_cli.py tests/cxas_scrapi/poly`
  - `uv run --with-editable . --with alive-progress pytest tests/cxas_scrapi/poly -q` (89 passed, 1 existing pytest config warning)
  - `uv run --with-editable . --with alive-progress cxas poly readiness --app-dir examples/bella_notte`
  - `uv run --with-editable . --with alive-progress cxas poly readiness --app-dir examples/bella_notte --format json`
  - `uv run --with-editable . --with alive-progress cxas poly validate --app-dir examples/bella_notte`
  - `uv run --with-editable . --with alive-progress cxas poly doctor --app-dir examples/bella_notte`
  - `uv run --with-editable . --with alive-progress cxas poly diff chat --app-dir examples/bella_notte --json`
  - `uv run --with-editable . --with alive-progress cxas poly build --app-dir examples/bella_notte --output-dir /tmp/poly_prd_alignment_build_c72c_readiness_20260522`
  - `uv run --with-editable . --with alive-progress cxas lint --app-dir /tmp/poly_prd_alignment_build_c72c_readiness_20260522/chat`
  - `uv run --with-editable . --with alive-progress cxas lint --app-dir /tmp/poly_prd_alignment_build_c72c_readiness_20260522/voice`
- No blockers.
- Draft PR: https://github.com/GoogleCloudPlatform/cxas-scrapi/pull/172
