---
name: dfcx-framework-ingestor
description: "Parsing and Ingestion directives for the Dialogflow CX (DFCX) agent structure."
---

# Dialogflow CX Ingestor Skill

This skill standardizes the ingestion, parsing, and behavior extraction of
conversational agents exported in the official, public Dialogflow CX (DFCX)
format.

--------------------------------------------------------------------------------

## 1. DFCX Export Package Layout

An official Dialogflow CX agent export is packaged as a `.zip` archive. When
extracted, it conforms to the following public directory layout:

```
<agent_zip_root>/
├── agent.json                       # Global agent metadata (default language, timezone, display name)
├── intents/                         # Declared intents and training phrases
│   └── <intent_name>/
│       └── <intent_name>.json       # Intent configuration
├── entityTypes/                     # Custom entity declarations
│   └── <entity_name>/
│       └── <entity_name>.json       # Entity configuration and synonyms
├── transitionRouteGroups/           # Shared flow-level transition groups
├── webhooks/                        # Declared webhooks and backend REST integrations
│   └── <webhook_name>.json          # Webhook configuration (defining target URI and credentials)
└── flows/                           # Subfolders representing individual Flows
    └── <flow_name>/                 # Flow directory (e.g., Default Start Flow, Billing)
        ├── <flow_name>.json         # Flow configuration listing flow-level transition routes and entry points
        ├── transitionRouteGroups/   # Shared transition route groups specific to this flow
        └── pages/                   # Directory containing individual page configurations
            └── <page_name>.json     # Page definition containing entry prompts, form parameters, and transitions
```

--------------------------------------------------------------------------------

## 2. Ingestion & Flow Tracing Directives

The Ingestor Agent MUST parse the extracted file structures using standard
directory traversal and JSON parsing, mapping them to the primary coverage
checklist:

### A. CUJ Mapping

*   **CUJ Discovery**: Treat each **Flow folder** under `flows/` (e.g.,
    `flows/Default Start Flow/` or `flows/Billing Flow/`) as a high-level
    **Parent CUJ**. The folder's directory name represents the CUJ title.

### B. Sub-intent & Trigger Extraction

For each Flow, traverse `<flow_name>.json` and all `.json` files inside its
`pages/` subdirectory:

1.  **Flow-Level Transition Routes**:
    -   Parse the `"transitionRoutes"` array inside `<flow_name>.json`.
    -   Identify each transition triggered by an intent (where `"intent"` is
        declared, e.g., `projects/.../intents/<intent_id>`).
    -   Map each unique triggered transition as a **Sub-intent / Scenario**
        (using the target page/flow name to synthesize its name).
2.  **Page-Level Transition Routes**:
    -   For each page `.json` inside `pages/`, parse the `"transitionRoutes"`
        array to map conversational branches and trigger conditions.
3.  **Form Parameter nodes**:
    -   Parse the `"form.parameters"` array inside a page JSON.
    -   Each parameter collected (e.g., email_address, postal_code) represents a
        sub-intent step in the conversational walkthrough.
4.  **Fulfillment Webhooks**:
    -   Identify `"triggerFulfillment.webhook"` inside transition routes or page
        configs.
    -   Resolve the webhook's target endpoint destination URI by looking up its
        matching configuration file inside the global `webhooks/` directory
        (e.g. `webhooks/<webhook_name>.json`'s `"genericWebService.uri"`).
    -   **Note**: Webhooks represent standard REST integrations defined as
        **toolsets** (indexed via OpenAPI specifications `open_api_schema.yaml`
        under the `toolsets/` directory).

### C. Alignment with Canonical `cxas-agent-migration` Standards

When parsing Dialogflow CX layouts, the Ingestor Agent SHOULD align the
extracted behaviors with the open-source `cxas-agent-migration` topology (N->M
agents consolidation):

1.  **Playbook/Flow to Agent Persona**: Each extracted Flow directory represents
    a distinct sub-agent boundary. Treat the flow's transitions and event
    handlers as the sub-agent's private prompt limits and routing callbacks.
2.  **Deduplicate & Group Variables**: Consolidate intent parameters and page
    forms variables using `cxas-agent-migration` Stage 1 rules (variable
    deduplication) to avoid parameter bloat in transcripts.
3.  **Topological Rewiring**: Track the source dependency graph (parent flows ->
    children flows) using `cxas-agent-migration` Stage 3 rules, ensuring that
    transitions from steering accurately reflect the legacy DFCX intent routing.

--------------------------------------------------------------------------------

## 3. Dialogue Simulation & Transcript Guidelines

When simulating conversation transcripts based on parsed DFCX folder packages,
enforce the following rules:

### A. Verbatim Prompts Extraction

To guarantee the simulated agent speaks the exact developer-defined prompts,
parse the fulfillment blocks:

1.  **Page Entry Prompts**:
    -   Read `"entryFulfillment.messages"` inside `pages/<page_name>.json`.
    -   Extract speech variants under `"text": {"text": [...]}`. The simulated
        `Agent` turn MUST output this string verbatim.
2.  **Form Slot-Filling Prompts**:
    -   Read `"initialPromptFulfillment.messages"` inside
        `"form.parameters[].fillBehavior"` in the page JSON.
    -   The simulated `Agent` turn asking for the parameter MUST output these
        prompt strings verbatim.
3.  **Transition Prompts**:
    -   Read `"triggerFulfillment.messages"` inside transition routes.

### B. Programmatic Flow Traversal & Intent Discovery

To discover conversational paths and map execution sequences:

1.  **Flow Start Point**: Access `flows/<flow_name>/<flow_name>.json` and locate
    the `"transitionRoutes"` inside the root flow config. These define the entry
    intents (e.g. `"intent": "support_request"`).
2.  **State Traversal**:
    -   For each route, trace the target node: if the target is a sub-flow (e.g.
        `"targetFlow": "flows/Billing/"`), pivot to that sub-flow config.
    -   If the target is a page (e.g. `"targetPage": "Collect Email"`), load and
        parse the page configuration file `pages/Collect Email.json`.
3.  **Fulfillment Sequence Tracking**:
    -   Trace visited pages in chronological order along the path to extract the
        sequential webhook invocations (`triggerFulfillment.webhook`) and
        parameter mappings, mapping each valid end-to-end path as a scenario
        sequence.
