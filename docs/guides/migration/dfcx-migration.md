---
title: DFCX to CXAS Migration
description: Comprehensive guide to migrating Dialogflow CX agents to CX Agent Studio using the interactive CLI dashboard.
---

# DFCX to CXAS Migration

The `cxas migrate dfcx` command provides an interactive, terminal-based dashboard (powered by `rich` and `ipywidgets`) that guides you through migrating a **Dialogflow CX (DFCX)** agent into **CX Agent Studio (CXAS)**.

This guide covers how DFCX concepts map to CXAS architecture, how to configure the migration tool, what options to select during the interactive prompts, and how to verify the generated output.

---

## Architectural Mapping

Before migrating, it is helpful to understand how core DFCX concepts translate to CXAS's modular, multi-agent architecture:

| Dialogflow CX (DFCX) | CX Agent Studio (CXAS) | Migration Transformation |
|---|---|---|
| **Flow / Page** | **Agent** | DFCX Flows and Pages are converted into optimized CXAS Agents. AST parsers analyze static page entry fulfillments and transition routes, synthesizing them into dynamic LLM instructions, strict guardrails, and explicit routing rules, while extracting telephony loops into deterministic callbacks. |
| **Playbook** | **Agent** | DFCX Playbooks translate directly to modular CXAS Agents. Advanced AI optimization passes restructure prompts into robust state machines, prune hallucinated verbiage, rewrite inline variable assignments to explicit tool calls, and tune step-by-step guidelines for the latest CXAS voice and chat models. |
| **Webhook** | **OpenAPI Tool** | Webhooks are converted into modular OpenAPI tools, automatically wrapped by Python tools to handle context injection and data formatting. |
| **OpenAPI Specification** | **OpenAPI Toolset** | Parses YAML/JSON schemas, replaces legacy session variables (`@dialogflow/sessionId`) with CXAS context injections, and maps endpoints to toolset operations. |
| **Data Store Tool (`dataStoreSpec`)** | **Grounding / RAG Tool** | Migrates Vertex AI Search datastore connections into native CXAS `data_store_tool` definitions for RAG and grounding. |
| **Code Block (`@Action`, Scripts)** | **Python Tool** | AST parsers extract entry and helper functions, strip legacy DFCX decorators, fix return type annotations, rewrite parameter mutations to CXAS native `get_variable`/`set_variable`, and compile standalone CXAS Python tools. |
| **Session Parameter (`$session.params.var`)** | **Global State (`context.state`)** | Parameter mutations are rewritten to use the Agent Development Kit (ADK) native global state: `context.state["var"] = val`. |
| **System Function (`flows.Agent_Transfer`) in Code Blocks** | **Callback Action (`Part.from_agent_transfer`)** | DFCX system directives are mapped to deterministic Python callback actions (e.g., `Part.from_agent_transfer`, `Part.from_end_session`). |
| **Telephony Event (`sys.no-input`)** | **Deterministic Callback** | Telephony loops and silence timeouts are extracted from LLM instructions and delegated to deterministic event callbacks. |
| **Agent Routing Metadata** | **AI Specialist Descriptions** | Concurrently generates concise, capability-focused agent descriptions used by parent routers and peer LLMs to determine precise sub-agent handoffs. |
| **Authentication Profile** | **Secret Manager Integration** | OpenAPI webhook definitions are converted into modular OpenAPI tools. Authentication headers (API keys, OAuth tokens) are extracted and mapped to CXAS Secret Manager auth profiles. |
| **App Optimization** | **Hybrid Optimization Module** | Executes a multi-stage pipeline to deduplicate global variables (staying under CXAS limits), restructure instructions into robust XML State Machines, and inject realistic happy-path tool mocks. |
| **Routing Topology** | **App Architecture & Root Agent** | The topology linker automatically resolves explicit and generative routing dependencies, protects against circular references, and configures the Root Agent for the full CXAS application. |

---

## Prerequisites

Before starting the migration, ensure you have:

1.  **GCP Authentication:** Configured active credentials with access to the target GCP project (`gcloud auth application-default login`).
2.  **Source Agent Data:** Either the live **Agent ID** of the DFCX agent (e.g., `projects/<proj>/locations/<loc>/agents/<uuid>`) or an exported agent bundle (`.zip` or `.json`).
3.  **Target GCP Project:** A GCP project with the CX Agent Studio API enabled.

---

## Entry points

| Command | When to use |
|---|---|
| `cxas migrate dfcx` | Interactive dashboard. Walks you through configuration, resource selection, dependency analysis, then runs the full migration. Best for first-time use and exploration. |
| `cxas migrate dfcx-cxas run` | Non-interactive end-to-end migration with flags. Best for scripted runs and CI. |
| `cxas migrate dfcx-cxas stage1` / `stage2` / `stage3` | Run a single post-migration stage against an existing IR bundle. Best for resuming after a failure or iterating on one stage. |
| `cxas migrate dfcx-cxas resume` | Interactive bundle picker + stage menu. |
| Skill at `.agents/skills/cxas-dfcx-migration/` | InquirerPy prompts + HTML pre-flight preview + Gemini model picker. See the skill's `SKILL.md`. |

All entry points call the same `MigrationService.run_migration` / `run_stage1` / `run_stage2` / `run_stage3` methods — pick whichever matches your workflow.

## Step-by-Step Walkthrough (interactive dashboard)

Launch the interactive migration dashboard from your terminal:

```bash
cxas migrate dfcx
```

The dashboard presents a structured interface divided into three main phases: **Configuration**, **Resource Selection**, and **Analysis & Execution**.

### Phase 1: Configuration

When launching the interactive migration dashboard, you will configure global parameters and target paths in the following logical order:

*   **Source Type:** Select whether to load the legacy agent from `ID` or a local `Zip File`.
*   **Source Agent ID / Zip Path:** Enter the live DFCX Agent ID or local zip file path.
*   **Target Project ID:** The GCP project where the migrated app will be deployed (defaults to your active gcloud auth project).
*   **Target Agent Name:** The root name for the new CXAS application (e.g., `retail_banking_app_v1`).
*   **Environment:** Select `PROD` for direct deployment or `AUTOPUSH` for automated continuous integration environments.
*   **Global App Model:** Select the primary foundational model for the migrated agents (e.g., `gemini-3.1-flash-live`).
*   **Optimize for CXAS:** Set to `[y]` (Recommended, default `[y]`) to execute the multi-stage Hybrid Optimization Module passes (deduplicating variables, restructuring instructions to State Machines, and injecting tool mocks).
*   **Run structural consolidation (Gemini N→M agent grouping)?:** *(Opt-in, default `[n]`)* Runs `StructuralConsolidator` after Stage 2 to collapse N source agents into M capability-focused groups. The dashboard auto-accepts the Gemini proposal; use the skill at `.agents/skills/cxas-dfcx-migration/` for the interactive review TUI (accept / re-propose / merge / split / rename).
*   **Run Stage 3 topology wiring?:** *(Opt-in, default `[n]`; requires consolidation)* Wires consolidated-agent parent-child topology so root reaches every group as a direct child. Idempotent.
*   **Persist IR bundle for stage-resume?:** *(Opt-in, default `[n]`)* Writes `<target>_ir.json` after migrate and after each post-migration stage so you can re-run any single stage with `cxas migrate dfcx-cxas stage1` / `stage2` / `stage3`.
*   **Generate Migration Report:** Ensure this is checked to generate a comprehensive markdown audit report (`migration_report.md`) upon completion.
*   **Generate Unit Tests (Auto-Fix):** *(Feature coming)* Automatically generates unit tests and evaluation cases for migrated tools and callbacks.
*   **Generate Hillclimbing Evals:** *(Feature coming)* Enable to automatically generate advanced hillclimbing turn evaluations.
*   **Eval Target:** *(Feature coming)* Choose between `Custom API Runner` or `Native Product Eval`.

### Phase 2: Resource Selection

Once your legacy agent is loaded, the CLI discovers and enumerates all root-level settings, Playbooks, and Flows, assigning each a unique numerical identifier:

```
=== Resource Selection ===

Available Resources:
  1. [Playbook] Cymbal Telco International Roaming Steering
  2. [Playbook] Agent Escalation Playbook
  3. [Flow] Acct Mgmt Address Disambig
  4. [Flow] Default Start Flow
```

The CLI provides a flexible, two-step filtering mechanism to define your precise migration scope:

#### Step 1: Choose Initial Baseline
You will first be prompted to select your starting baseline:
*   Enter `all` (Default) to start with all discovered resources selected.
*   Enter `none` to start with an empty selection.

#### Step 2: Refine via Numbers and Ranges
Based on your initial baseline, you can refine the selection using comma-separated numbers and ranges:
*   **If you chose `all`:** You will be prompted to enter numbers/ranges to **EXCLUDE**. For example, entering `2,4` excludes item 2 and item 4; entering `2-4` excludes items 2, 3, and 4. Pressing `Enter` without typing anything keeps all resources selected.
*   **If you chose `none`:** You will be prompted to enter numbers/ranges to **INCLUDE**. For example, entering `1,3` includes item 1 and item 3; entering `1-3` includes items 1, 2, and 3.

### Phase 3: Dependency Analysis

Before initiating the migration, click **`Analyze References & Dependencies`**. SCRAPI performs an automated topological scan of your selection:

*   **Missing Dependencies (Outgoing):** Identifies resources referenced by your selection that were *not* checked in the selector (e.g., a selected Playbook transfers to an unselected Flow).
*   **Incoming References:** Highlights unselected resources that depend on your current selection.

Ensure all critical dependencies are selected before proceeding.

---

## Automated Transformation & Optimization

When you click **`START MIGRATION`**, SCRAPI executes several automated engineering passes to optimize the DFCX resources for CXAS:

### 1. Concurrent AI Specialist Description Generation
To enable seamless multi-agent routing in CXAS, SCRAPI executes parallel generative passes analyzing each Playbook's instructions and goals:
*   **Specialist Capabilities:** Synthesizes concise, 1-sentence descriptions focusing entirely on the sub-agent's narrow domain expertise.
*   **Router Integration:** These generated descriptions are consumed natively by parent 'router' agents and peer LLM sub-agents to determine exactly when to transition a user to a specialist agent during a conversation.
*   **Asynchronous Execution:** Descriptions are generated concurrently to maximize pipeline throughput during the initial IR compilation phase.

### 2. Tool & Webhook Conversions
SCRAPI provides robust translation engines for legacy DFCX backend integrations:
*   **OpenAPI Context Injection:** Parses YAML/JSON OpenAPI specifications and automatically replaces legacy DFCX variables like `@dialogflow/sessionId` with CXAS context mappings (`x-ces-session-context: $context.session_id`).
*   **Dynamic Webhook Schemas:** Translates generic webhooks into standardized OpenAPI toolsets, generating dynamic schemas based on HTTP methods, URI path parameters, and request body templates.
*   **Secret Manager Auth:** Extracts Basic Auth credentials, API keys, OAuth client secrets, Bearer tokens, and Service Account configurations from legacy specifications and maps them securely to CXAS Secret Manager integration profiles.
*   **Data Store Grounding:** Migrates Vertex AI Search and knowledge base connections into native CXAS `data_store_tool` definitions, preserving grounding descriptions and source datastore paths.

### 3. Code Block AST Transformations
When migrating legacy DFCX fulfillment scripts or inline Cloud Functions, SCRAPI executes robust AST transformations:
*   **Decorator Stripping:** Automatically removes legacy DFCX-specific decorators (`@Action`, `@Handler`).
*   **Return Type Fixing:** Enforces explicit `-> dict` return annotations and injects base dictionary returns if omitted.
*   **Universal Directive Tracking:** Appends system calls (`respond()`, `agentTransfer()`) into a `__cxas_system_directives__` tracking payload returned at the end of the execution scope.
*   **Helper Function Ingestion:** Automatically traverses the AST to bundle shared helper functions and typing imports into the same generated Python tool file.

### 4. Global State & Variable Rewriting
In DFCX, variables are often tracked via `$session.params`. SCRAPI rewrites local variable mutations in Python tools and callbacks to use CXAS `context.state`:

```python
# Legacy DFCX concept: $session.params.retry_count = 0
# Migrated CXAS Python Callback:
context.state["retry_count"] = 0
```

### 5. Prompt Optimization Passes
*   **Tool Chaining Prevention:** SCRAPI identifies instances where DFCX prompts forced sequential tool calls and synthesizes wrapper Python tools that execute the operations sequentially in code, returning only the final filtered context to the LLM.
*   **Pruning Hallucinations:** Generates strict guardrail instructions (e.g., *"Do NOT read out internal context variables"*).
*   **Route Group Pruning:** Scans instructions and automatically removes references to transition routes or target agents that were excluded during resource selection.

### 6. Deterministic System Callbacks
DFCX routing overrides and system directives (`flows.Agent_Transfer`, `add_override`) are converted into standardized system directive payloads and intercepted by auto-generated universal callbacks using native CXAS `Part` actions:

```python
# Auto-generated Universal Callback
if Part.has_function_call('agent_transfer'):
    return Part.from_agent_transfer(agent='escalation_agent')
```

### 7. Partial Responses (`response.partial = True`)
For deterministic greetings or intermediate UI payloads (e.g., sending a client-side view while an async tool runs), SCRAPI generates callbacks with `response.partial = True`, allowing the agent to emit deterministic JSON payloads without terminating the LLM generation loop.

### 8. The Hybrid Optimization Module (Track 3)
When `Optimize for CXAS` is enabled, SCRAPI creates a baseline version backup (`0.0.1`) and executes an advanced 2-stage LLM and algorithmic optimization pipeline:
*   **Stage 1: Global Variable Deduplication (Version `0.0.2`):** Scans all agent instructions, tools, and callbacks for variable references (e.g., `{var}`, `$var`, `get_variable()`), builds a global dependency map, and uses an LLM pass to deduplicate variables, keeping the app under the 95-variable limit. It automatically updates parameter declarations and rewrites text/code references globally.
*   **Stage 2: State Machines & Tool Mocks (Version `0.0.3`):** Concurrently restructures natural language instructions into robust XML State Machines (states, transitions, tool rules), dynamically attaching `set_session_variables` if needed. Simultaneously, it ingests calling agent context and injects highly realistic happy-path `mock_mode` return branches into Python tools.

### 9. Topology Linking & Root Agent Configuration
The topology linker automatically traverses explicit (`referencedPlaybooks`) and generative (`{@AGENT: name}`) routing dependencies, establishes parent/child relationships in CXAS, protects against circular references, and configures the canonical Root Agent for the full application.

---

## Non-Interactive: `cxas migrate dfcx-cxas`

For scripted / CI use, the `dfcx-cxas` subcommand tree exposes the same `MigrationService` methods the interactive dashboard calls — no skill scripts required.

### `cxas migrate dfcx-cxas run` — end-to-end

```bash
cxas migrate dfcx-cxas run \
  --source-agent-id projects/<src_proj>/locations/us/agents/<uuid> \
  --project-id <target_proj> --location us \
  --target-name my_app \
  --consolidate --stage3 --persist-bundle
```

Flags:

| Flag | Default | Effect |
|---|---|---|
| `--source-agent-id` / `--source-zip` | required (one of) | Live DFCX agent or local export zip. |
| `--project-id` | required | Target GCP project. |
| `--location` | `us` | Target CXAS location. Avoid `global` — most projects don't support it. |
| `--target-name` | required | Display name for the new CXAS app. |
| `--env` | `PROD` | `PROD` or `AUTOPUSH`. |
| `--model` | repo default | Foundation model for the migrated agents. |
| `--no-optimize` | optimize on | Skip the Stage 1 + Stage 2 optimization passes. |
| `--consolidate` | off | Run Gemini structural consolidation after Stage 2 (auto-accepts proposed grouping). |
| `--stage3` | off | Run Stage 3 topology wiring (requires `--consolidate`). |
| `--persist-bundle` | off | Write `<target>_ir.json` for stage-level resume. |

### `cxas migrate dfcx-cxas stage1 | stage2 | stage3` — single stage

Each loads `<target>_ir.json` (produced by an earlier `run` or by the skill), restores a `MigrationService`, and runs one stage. Useful for re-running a single stage after a fix without paying the full migration cost.

```bash
# Re-run Stage 1 (variable dedup; --no-consolidate skips the Gemini grouping)
cxas migrate dfcx-cxas stage1 --target-name my_app --no-consolidate

# Stage 2 with optional unit-test regen + lint + report (defaults: all enabled)
cxas migrate dfcx-cxas stage2 --target-name my_app

# Stage 3 in dry-run mode — print the proposed parent → children mapping
cxas migrate dfcx-cxas stage3 --target-name my_app --dry-run
```

Common flags across `stage1` / `stage2` / `stage3`:

| Flag | Effect |
|---|---|
| `--target-name TARGET` / `--ir-bundle PATH` | Resolve the IR bundle. |
| `--project-id` / `--location` | Override the bundle's project / location. |
| `--no-persist` | Skip writing the updated bundle back to disk. |
| `--yes` / `-y` | Non-interactive. |

### `cxas migrate dfcx-cxas resume` — interactive picker

Lists every `*_ir.json` in the cwd, prompts you to pick one, then prompts for which stage (1/2/3) to run. The right entry point when you've stepped away and aren't sure which target you were last working on.

---

## Post-Migration Verification

Upon completion, SCRAPI outputs several critical artifacts to your working directory:

```
./
├── migration_<TargetName>.log         # Detailed execution log
├── migration_report.md                # Comprehensive markdown audit report
├── <TargetName>_topology.svg          # High-level visual topology diagram
└── cxas_app/<TargetName>/             # Pulled CXAS application source code
```

### Reviewing the Audit Report
Open `migration_report.md` to review the full audit of the migration. The report includes:
*   **App Details & Metadata:** Source DFCX ID and target CXAS App ID.
*   **AI-Augmented Analysis:** Generative AI summaries of user journeys and component analysis.
*   **Variables & Tools Migrated:** Explicit mapping tables of original vs. sanitized resource names.
*   **AST Code Block Dependencies:** Summary of injected toolset dependencies.
*   **Skipped Resources:** A prioritized list of resources that could not be migrated automatically, along with actionable engineering recommendations.

### Next Steps

1.  **Inspect Local Source:** Navigate to `cxas_app/<TargetName>/` to inspect the generated YAML configurations, instructions, and Python code.
2.  **Run Linter:** Execute `cxas lint` to verify that the generated configuration complies with all 60+ CXAS best practices.
3.  **Deploy & Test:** Use `cxas push` to upload any manual refinements and `cxas test-tools` to execute the auto-generated test cases against the live platform.
