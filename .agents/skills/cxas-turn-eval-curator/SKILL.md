---
name: cxas-turn-eval-curator
description: >-
  Generates curated, deterministic AUDIO turn-eval probes from SCRAPI
  SimulationEvals runs. Use when converting simulation trajectories into
  single-turn turn evals for fast instruction iteration or to build the
  train/val eval set for the instruction optimizer (cxas_scrapi.optimize).
  A deterministic script extracts a draft (tool calls, transfers, inputs,
  outputs) from each sim turn; the agent then curates it — pruning incidental
  assertions, flagging observed bugs, reconstructing missing tool outputs from
  the spoken answer plus the test fixture, and converting free-text
  expectations to deterministic operators. Triggers: "turn evals from
  simulations", "convert sim evals to turn evals", "curate harvested
  assertions", "build the eval set for the optimizer".
---

# CXAS Simulation → Curated Turn-Eval Builder

Turn a few expensive simulation runs into many cheap, deterministic, single-turn
**audio** turn-eval probes. The split is deliberate:

- **Extraction is deterministic** (`scripts/sim_to_turn_evals.py`) — parse
  trajectories, harvest `tool_called` / `tool_input` / `tool_output` /
  `agent_transfer`, build `historical_contexts` + `turn_count`.
- **Curation is agent judgment** (this skill) — decide which observed behaviors
  are *required* vs incidental, flag bugs, and strengthen assertions.

The agent runs at AUTHORING time only. Output is static deterministic YAML; the
eval-runtime loop stays LLM-free. Curated probes feed `cxas sxs` (run) and
`cxas_scrapi.optimize.reflective_loop` (instruction optimization gate).

Read `references/operators.md` for operator semantics, `historical_contexts`
forms, audio gotchas, and the full curation heuristics. Read it before curating.

## Steps

### 1. Check environment
```bash
python -c "import cxas_scrapi" && gcloud auth list
```

### 2. Get inputs
Ask the user for: the App resource name (`projects/.../apps/...`); the
simulation source — either an existing sim results JSON or a sim YAML to capture
from; and the output path. Confirm the agent is audio-native (default
`modality: AUDIO`).

### 3. Generate the draft (deterministic)
Run the extractor with `--review` so the harvested assertions print for
inspection. Default `--capture-modality audio` honors the audio constraint.
```bash
python .agents/skills/cxas-turn-eval-curator/scripts/sim_to_turn_evals.py \
  --app-name "projects/.../apps/..." \
  --eval-file evals/simulations/<sims>.yaml --run --capture-modality audio \
  --output <draft>.yaml --review
```
Use `--sim-results <results.json>` instead of `--eval-file --run` to convert a
prior run. Key flags: `--context session|utterances`, `--input-keys`,
`--output-keys`, `--carry-expectations`, `--min-assertions`, `--fetch-retries`.

If the run reports "falling back to local trace", tool **outputs** are
unavailable for those scenarios (the conversation read flapped) — plan to
reconstruct them in curation (Step 4).

### 4. Curate (agent judgment — the point of this skill)
Read `references/operators.md` first. For each probe in the draft, apply
judgment the script cannot. Fetch the agent's tool schemas and the scenario's
`test_accounts_data` fixture as needed for grounding:
- **Prune** incidental tool assertions; keep the load-bearing ones.
- **Flag, do not encode, suspected bugs** — captures from FAILED sims or clearly
  wrong behavior must be surfaced to the user, never asserted as expected.
- **Reconstruct missing `tool_output`** from the spoken answer + fixture when the
  trace lacked outputs.
- **Convert** free-text expectations to deterministic operators where possible;
  keep only genuinely subjective ones as bare strings.
- **Fix audio-form assertions** (`"152.10"` → `"152 10"`).
- **Name and tag** probes by intent.

Present the curated changes as a diff and get user sign-off before trusting the
file.

### 5. Use the curated probes
```bash
# Run side-by-side (baseline vs candidate deployment), audio:
cxas sxs --app-name-a <baseline> --app-name-b <candidate> --eval-file <curated>.yaml

# Or feed the optimizer as its train/val gate:
python -m cxas_scrapi.optimize.reflective_loop \
  --candidate-app <scratch-app> --agent-name <.../agents/root_agent> \
  --eval-file <curated>.yaml --rounds 6 --output best_instruction.txt
```

## Notes
- Curation quality is load-bearing: un-curated assertions can make the optimizer
  optimize toward a captured bug. Always complete Step 4.
- Pairs with `cxas-sim-eval` (golden → sim) and `cxas-loss-analysis` (failure
  analysis). This skill is sim → curated turn-eval.
