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
| `cxas migrate dfcx` | Interactive TUI dashboard. Walks you through configuration, resource selection, dependency analysis, then runs the full migration. Best for first-time use and manual exploration. |
| `cxas migrate dfcx --run` | Non-interactive end-to-end migration with profiles. Best for scripted pipelines and CI runs. |
| `cxas migrate dfcx --optimize --stage {1,2,3}` | Run a single post-migration stage checkpoint against an existing IR bundle. Best for failure recovery or manual step iteration. |
| `cxas migrate dfcx --optimize --stage resume` | Interactive bundle picker + stage menu for resumed tasks. |
| Skill at `.agents/skills/cxas-dfcx-migration/` | InquirerPy prompts + HTML pre-flight preview + Gemini model picker. See the skill's `SKILL.md`. |

All entry points call the same `MigrationService.run_migration` / `run_stage_1` / `run_stage_2` / `run_stage_3` methods — pick whichever matches your workflow.

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
*   **Environment:** Select your target deploy environment via first-letter case-insensitive hotkeys: **`[[P]ROD/[A]UTOPUSH]`** (defaults to `PROD`).
*   **Global App Model:** Select the primary foundational model for the migrated agents (e.g., `gemini-3.1-flash-live`).
*   **Set Migration Profile:** Select your desired optimization depth profile via case-insensitive hotkeys: **`[[B]est Practices Optimization/[F]ast 1:1 Migration Only/[C]ustom (Advanced)]`** (defaults to `Best Practices Optimization`):
    *   **`Best Practices Optimization (B)`** *(Recommended)*: Implicitly enables variables deduplication, Gemini-driven N→M structural consolidation TUI review, Spoke-Hub architecture topology wiring, audit reporting, and disk bundle persistence.
    *   **`Fast 1:1 Migration Only (F)`**: Disables all optimizations/consolidation stages, performing a direct baseline transpile of the DFCX stubs (preserves basic settings with a simple report).
    *   **`Custom (Advanced) (C)`**: Triggers individual granular configuration questions:
        *   *Optimize for CXAS?* (Toggles the overall Optimization passes).
        *   *Choose Spoke-Hub Architecture style:* Select between `hub-and-spoke` (Default) or `original-hierarchy`.
        *   *Persist IR bundle for stage-resume?* (Toggles writing intermediate files).
        *   *Generate Migration Report?* (Toggles markdown audit log output).
*   **Generate Unit Tests? [y/n] (y):** *(Conditional on Standard/Custom)* Auto-generates deterministic unit test goldens and callbacks simulation cases.
*   **Generate Hillclimbing Evals? [y/n] (n):** *(Conditional on Standard/Custom, feature coming)* Enable turn-level hillclimbing evaluations.
*   **Enter Eval Target:** *(Conditional on Standard/Custom, feature coming)* Choose the execution runner environment via case-insensitive hotkeys: **`[[C]ustom API Runner/[N]ative Product Eval (Stub)]`** (defaults to `Custom API Runner`).

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

Before initiating the migration, the interactive TUI executes an automated topological scan of your selection to analyze resource references and incoming/outgoing dependency links:

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

### 8. The Hybrid Optimization Module & Checkpoints
When `Optimize for CXAS` is active, SCRAPI deploys standard optimizations sequentially and records clear Version Checkpoints to track step-level changes:
*   **Initial 1:1 Deploy (Version `0.0.1`):** Deploys the initial direct transpile baseline of DFCX stubs.
*   **Stage 1 Part A: Variable Deduplication (Version `0.0.2`):** Scans all instructions, tools, and callbacks, maps parameter usages globally, and runs an algorithmic pass to merge redundant variables to stay under CXAS limits.
*   **Stage 1 Part B: Structural Gemini Consolidation (Version `0.0.3`):** Prompts a structural N→M agent grouping proposal via Gemini, opens the TUI interactive review editor to refine names/boundaries, compiles the unified instructions, and deploys the consolidated specialists. *(Note: Under the hood, this pass dynamically utilizes our specialized Step 3 consolidation templates: `STEP_3A_CONSOLIDATION_ARCHITECTURE` and `STEP_3B_CONSOLIDATION_INSTRUCTIONS` to enforce safety parameters and empty required tools constraints!)*
*   **Stage 2: State Machines & Tool Mocks (Version `0.0.4`):** Restructures the natural language guidelines into robust XML State Machines (states, guidelines, tool bindings) and automatically injects realistic happy-path `mock_mode` return paths into custom Python tools.
*   **Stage 3: Parent-Child Topology Wiring (Version `0.0.5`):** Traverses sub-agent routing requirements, builds cycle-free routing links matching the target architecture style (e.g. Spoke-Hub), resets the App starting root agent, and prunes old orphan stubs.

### 9. Topology Linking & Root Agent Configuration
The topology linker automatically traverses explicit (`referencedPlaybooks`) and generative (`{@AGENT: name}`) routing dependencies, establishes parent/child relationships in CXAS, protects against circular references, and configures the canonical Root Agent for the full application.

---

## Non-Interactive command pipeline paths

For scripted terminal environments or automated CI pipelines, the CLI provides non-interactive, flag-driven pathways under the `cxas migrate dfcx` command.

### E2E automated script path (`--run`)

Runs the entire DFCX→CXAS pipeline from source fetch to optimization checks E2E.

```bash
cxas migrate dfcx --run \
  --source-agent-id "projects/<src_proj>/locations/us/agents/<uuid>" \
  --project-id <target_proj> --location us \
  --target-name my_app \
  --profile standard
```

E2E pipeline flags:

| Flag | Default | Effect |
|---|---|---|
| `--source-agent-id` / `--source-zip` | required (one of) | Target DFCX Agent ID or local export `.zip` path. |
| `--project-id` | required | Target GCP Project ID. |
| `--location` | `us` | Target GCP Location. Avoid `global` (unsupported). |
| `--target-name` | required | Human-readable name prefix for the target app and intermediate bundles. |
| `--env` | `PROD` | Deployment target environment (`PROD` or `AUTOPUSH`). |
| `--model` | standard pro | Foundational model to configure for target agents. |
| `--profile` | `standard` | Optimization depth profile (`standard`, `direct`, or `custom`). |
| `--no-optimize` | off | Custom Mode: Force-skip all Stage 1/2/3 optimization passes. |
| `--persist-bundle` | off | Custom Mode: Enable saving intermediate `<target>_ir.json` bundles. |
| `--yes` / `-y` | off | Auto-confirm standard prompts and progress dialogs. |

### Checkpoint optimization stage runs (`--optimize`)

Loads a previously saved `<target>_ir.json` bundle state from disk, instantiates the service workspace from that state, and runs a single isolated stage checkpoint. This is highly useful for recovery or rapid, step-level logic tuning without re-fetching sources.

```bash
# Run Stage 1 (dedup + Gemini consolidation grouping TUI)
cxas migrate dfcx --optimize --stage 1 --target-name my_app

# Run Stage 2 (schedules instruction state-machines, mocks, lints, and report)
cxas migrate dfcx --optimize --stage 2 --target-name my_app --no-lint

# Run Stage 3 (parent-child Spoke-Hub topology linking)
cxas migrate dfcx --optimize --stage 3 --target-name my_app --architecture original-hierarchy
```

Stage Checkpoint command flags:

| Flag | Effect |
|---|---|
| `--stage {1,2,3,resume}` | The target optimization checkpoint or step resume TUI menu to load (Required). |
| `--target-name TARGET` / `--ir-bundle PATH` | Target bundle pointer (uses targets list default if omitted). |
| `--project-id` / `--location` | Override target GCP Credentials configuration parameters. |
| `--version-label LABEL` | Target Version display_name override. Defaults to: `0.0.3` (Stage 1), `0.0.4` (Stage 2), `0.0.5` (Stage 3). |
| `--architecture STYLE` | Stage 3: The target Spoke-Hub structure layout style (`hub-and-spoke` or `original-hierarchy`). |
| `--no-persist` | Skip writing stage mutations back to the disk bundle. |
| `--no-unit-tests` | Stage 2: Skip regenerating mock test JSON maps. |
| `--no-lint` | Stage 2: Skip running standard linter practice verification sweeps. |
| `--no-report` | Stage 2: Skip compiling the optimization audit report markdown. |

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
