---
name: cxas-vector-steering
description: >-
  Deploy, configure, audit, and maintain CXAS Vector Steering — the Two-Tower Hybrid
  Vector Intent Classification and Routing Engine for CX Agent Studio (CXAS /
  GECX / CES) and Google ADK combining Vertex AI Vector Search with
  conversational Gemini disambiguation. Use this skill whenever a user wants to
  add vector-based intent routing to an agent, eliminate LLM intent
  hallucination or 20k-token prompt bloat, reduce voice/chat turn latency,
  derive domain-specific intent taxonomies and 7-axis training corpora,
  provision Vertex AI Vector Search and Gen2 Cloud Function middleware, or
  scaffold intent_classifier and update_intent tools into a CXAS SCRAPI
  workspace.
---

# CXAS Vector Steering — Two-Tower Hybrid Vector Intent Routing

Vector Steering replaces monolithic, error-prone 20,000-token LLM routing prompts with a **Two-Tower Hybrid Cognitive Architecture** built natively for CX Agent Studio (`cxas-scrapi`) and Google ADK:

1. **Tower 1 (Deterministic Semantic Retrieval)**: Vertex AI Embeddings (`text-embedding-005` / `text-embedding-004` / `text-multilingual-embedding-002` with symmetric `SEMANTIC_SIMILARITY` + L2 normalization) + Vertex AI Vector Search (`BRUTE_FORCE` for `<30k` exact recall in `5–15ms`, or `TREE_AH` for `>=30k` scale) retrieves nearest-neighbor intents in `<40ms`.
1. **Mathematical Gating Middleware**: Evaluates `similarity_threshold` (`0.75`), `disambiguation_delta` (`0.08`), and deterministic multi-tenant/agent-scope filters.
1. **Tower 2 (Conversational Gemini Resolution)**: Injects only the top `1–3` candidate intents (with contrastive semantic descriptions) into the steering agent to route silently, auto-resolve in context, or ask one concise clarification question—protected by a strict `update_intent` allowlist (`0.0%` intent hallucination).

______________________________________________________________________

## Bundled Scripts & Resources

All scripts and starter templates live inside `.agents/skills/cxas-vector-steering/` (installed automatically into any workspace via `uv run cxas init`):

- [scripts/bootstrap_from_scrapi.py](scripts/bootstrap_from_scrapi.py): Reads `gecx-config.json` and harvests existing specialist sub-agents from a pulled CXAS app (`cxas_app/<App>/`) into `vector_steering/vector_steering.yaml`, `vector_steering/intents_catalog.yaml`, and `vector_steering/training_phrases.csv`.
- [scripts/analyze_dataset.py](scripts/analyze_dataset.py): Pre-indexing `--strict` corpus diagnostic gate catching nulls, phrase collisions, `>5x` class skew, unmasked catalog nouns, and accidental PII/PHI leaks across `.csv`, `.tsv`, `.json`, and `.jsonl`.
- [scripts/config_loader.py](scripts/config_loader.py): Strict regex security validators, `gecx-config.json` workspace locator, and proactive ML/Vector DB config advisor (`advise_vector_configuration`).
- [scripts/provision_infra.py](scripts/provision_infra.py): End-to-end GCP provisioner (`--dry-run` supported) for GCS, `BRUTE_FORCE` / `TREE_AH` Index, Index Endpoint, and OIDC-authenticated Gen2 Cloud Function ([resources/cloud_function/main.py](resources/cloud_function/main.py)).
- [scripts/embed_and_sync.py](scripts/embed_and_sync.py): Multi-environment embedder, timestamped GCS backup archiver, resumable uploader, and Vertex AI index updater (`--skip-upload`, `--upload-and-trigger`).
- [scripts/scaffold_agent_tools.py](scripts/scaffold_agent_tools.py): Compiles CES-compliant `toolsets/` (`url: $env_var` + `environment.json`), `tools/`, `app.json` variable declarations, sub-agent wiring, tool unit tests, and steering XML instructions for CXAS (`--target gecx`) and Google ADK (`--target adk`).

______________________________________________________________________

## Mandatory Safety & Approval Gates

1. **No Unapproved Cloud Provisioning or Billing Spend**: Always run `provision_infra.py --dry-run` and `embed_and_sync.py --dry-run` first. Present the exact GCP project, region, machine type, and resources to the user and obtain **explicit written approval** before creating or updating cloud resources.
1. **No Unapproved Remote Agent Push**: Before pushing to CX Agent Studio, snapshot the current live state with `uv run cxas versions create`, run `uv run cxas lint --validate-only`, suggest running `uv run cxas llm-lint` on the modified steering agent directory, and obtain **explicit written approval** before running `uv run cxas push`.
1. **Zero Customer Hardcoding & Zero PII/PHI in Corpora**: Keep all project IDs, buckets, index IDs, thresholds, tenant rules, and entity masks inside `vector_steering/vector_steering.yaml`, `vector_steering/intents_catalog.yaml`, and `environment.json`. Never include real customer PII/PHI/PCI (names, SSNs, MRNs, card/order numbers, emails, or phone numbers) in `training_phrases.csv`.

______________________________________________________________________

## End-to-End SCRAPI Deployment Workflow

Follow these phases in order when deploying Vector Steering to any new or existing CXAS SCRAPI or Google ADK agent.

### Phase 0: Architectural Fit & Proactive Vector DB / Model Advisory

Read [references/vector_db_and_embedding_tuning.md](references/vector_db_and_embedding_tuning.md) first so you proactively guide the user toward optimal model and index settings:

1. **Evaluate Fit**:
   - `< 15` completely distinct intents in a low-traffic chat bot: Note that simple prompt routing or [cxas-self-steering-agent](../cxas-self-steering-agent/SKILL.md) playbooks may suffice unless deterministic cutoffs or strict anti-hallucination guarantees are required.
   - `20–500+` intents, overlapping domain vocabulary, voice telephony latency constraints, or multi-tenant/regulated workflows: Proceed with Vector Steering.
1. **Proactively Recommend the Optimal Vector Configuration**:
   - **Embedding Model**: `text-embedding-005` (new English builds), `text-embedding-004` (existing calibrated baselines), `text-multilingual-embedding-002` (100+ languages / bilingual callers), or `gemini-embedding-001` (multi-clause triage with MRL `768`/`1536` + `normalize_l2: true`). Always lock symmetric `SEMANTIC_SIMILARITY`.
   - **Index Algorithm**: `BRUTE_FORCE` for `< 30,000` phrases (`100.0%` exact recall in `5–15ms`, zero leaf-partition misses) vs. `TREE_AH` for `>= 30,000` vectors.
   - **Distance Measure**: `COSINE_DISTANCE` (safe default) vs. `DOT_PRODUCT_DISTANCE` paired with `normalize_l2: true` (`~15–25%` faster SIMD throughput at `>500 RPS`).
   - **Update Method**: `BATCH_UPDATE` (GitHub Actions CI/CD releases) vs. `STREAM_UPDATE` (`<5s` real-time operational hotfixes).

### Phase 1: SCRAPI Workspace Bootstrap & Domain Signal Extraction

Read [references/corpus_engineering.md](references/corpus_engineering.md) and [references/domain_playbooks.md](references/domain_playbooks.md) before authoring intents.

1. **Path A — Existing CXAS Agent (Brownfield)**:
   - Pull the latest agent locally if not already exported:
     ```bash
     uv run cxas pull "<App Display Name or Resource>" --target-dir cxas_app/
     ```
   - Bootstrap Vector Steering directly from `gecx-config.json` and the pulled CXAS app directory:
     ```bash
     uv run python .agents/skills/cxas-vector-steering/scripts/bootstrap_from_scrapi.py \
       --app-dir cxas_app/<YourAgentDir> \
       --output-dir vector_steering
     ```
   - Optionally mine real non-contained caller phrasing via `uv run cxas trace search` (scrubbing all PII/PHI before adding to `vector_steering/training_phrases.csv`).
1. **Path B — Greenfield Agent (PRD or Vertical Only)**:
   - Run `bootstrap_from_scrapi.py` to initialize `vector_steering/` from [vector_steering.example.yaml](resources/config/vector_steering.example.yaml) and [intents_catalog.example.yaml](resources/config/intents_catalog.example.yaml), then derive domain intents, confusable sibling pairs, industry acronyms, and `entity_mask_patterns` using [references/domain_playbooks.md](references/domain_playbooks.md).

### Phase 2: Contrastive Catalog & 7-Axis Corpus Synthesis

1. **Author `vector_steering/intents_catalog.yaml`**:
   - Write contrastive 1–2 sentence `description` fields stating both **inclusion** and **exclusion** boundaries against sibling intents.
   - Configure `allowed_tenants`, `excluded_tenants`, and `suppress_in_agents` where applicable.
1. **Synthesize `vector_steering/training_phrases.csv` Across All 7 Axes**:
   - Distribute `25–60` phrases per intent (`8–12` for rapid prototypes) across direct imperatives, polite questions, layperson symptom stories, domain acronyms, voice ASR speech, chat shorthand, and hard-negative boundary phrases.
   - Generalize high-gravity catalog nouns and add regex guards to `dataset.entity_mask_patterns` in `vector_steering/vector_steering.yaml`.
1. **Run the Strict Corpus Diagnostic Gate (Mandatory)**:
   ```bash
   uv run python .agents/skills/cxas-vector-steering/scripts/analyze_dataset.py \
     --config vector_steering/vector_steering.yaml \
     --strict
   ```

### Phase 3: GCP Infrastructure Provisioning & Vector Indexing

Read [references/gcp_provisioning_and_cicd.md](references/gcp_provisioning_and_cicd.md) for IAM roles, shard sizing, cross-project ADC quota setup, and regulated-network ingress.

1. **Preview Plan (`--dry-run`) & Obtain User Approval**:
   ```bash
   uv run python .agents/skills/cxas-vector-steering/scripts/provision_infra.py \
     --config vector_steering/vector_steering.yaml \
     --env dev \
     --dry-run
   ```
1. **Provision & Record Resource Identifiers**:
   After explicit user approval, run without `--dry-run` and record `index_resource_name`, `index_endpoint_id`, and `cloud_function_url` in `vector_steering/vector_steering.yaml`.

### Phase 4: CXAS SCRAPI & ADK Agent Integration + Verification

Read [references/gecx_agent_integration.md](references/gecx_agent_integration.md) for SCRAPI `environment.json` `$env_var` resolution, steering sub-agent wiring, and `before_model_callback` single-pass patterns.

1. **Scaffold Tools, OpenAPI Toolset, `environment.json`, & Steering Prompt**:
   ```bash
   uv run python .agents/skills/cxas-vector-steering/scripts/scaffold_agent_tools.py \
     --config vector_steering/vector_steering.yaml \
     --app-dir cxas_app/<YourAgentDir> \
     --env dev \
     --merge-app-vars \
     --sync-env-json \
     --wire-steering-agent <SteeringAgentName>
   ```
   *(For standalone Google ADK Python agents, pass `--target adk`.)*
1. **Merge Steering Instructions & Transfer Rules**:
   - Merge the generated `steering_instruction_<modality>.txt` into `agents/<SteeringAgentName>/instruction.txt` non-destructively, preserving existing business/compliance rules.
   - Configure deterministic `transferRules` on child sub-agents keyed on `Intent == '<canonical_intent>'`.
1. **Run Structural Linting, Semantic Linting & Tool Tests**:
   ```bash
   uv run cxas lint --validate-only --app-dir cxas_app/<YourAgentDir>
   uv run cxas llm-lint --agent-dir cxas_app/<YourAgentDir>/agents/<SteeringAgentName>
   ```
1. **Snapshot & Push (With Explicit User Approval)**:
   ```bash
   uv run cxas versions create --app-name "<App Resource>" --display-name "pre-vector-steering"
   uv run cxas push cxas_app/<YourAgentDir> --to "<App Resource>" --env-file cxas_app/<YourAgentDir>/environment.json
   ```

### Phase 5: Day-2 Maintenance, Threshold Calibration & GitOps CI/CD

1. **Trace-Driven Refinement**: Harvest `NO_MATCH` or misrouted utterances via `uv run cxas trace search`, scrub PII, append to `vector_steering/training_phrases.csv`, re-run `analyze_dataset.py --strict`, and sync via `embed_and_sync.py`.
1. **GitHub Actions Automation**: Copy [resources/ci/github-actions.template.yml](resources/ci/github-actions.template.yml) into `.github/workflows/` to run `--strict` corpus validation + `cxas lint` on PRs and `--upload-and-trigger` index updates on merge.
