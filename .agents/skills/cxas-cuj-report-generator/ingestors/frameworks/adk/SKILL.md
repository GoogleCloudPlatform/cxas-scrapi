---
name: adk-framework-ingestor
description: "Parsing and ingestion directives for the Python-based Agent Development Kit (ADK) conversational agent framework."
---

# ADK Framework Ingestor Skill

This skill standardizes how agents ingest, parse, and extract conversational
behaviors and Critical User Journeys (CUJs) from Python workspaces built on the
Agent Development Kit (ADK) framework.

--------------------------------------------------------------------------------

## 1. ADK Workspace Layout

An ADK workspace typically consists of multiple decoupled microservices, each
containing its own python project. The structure below is illustrative; actual
directory and file names may vary:

```
<workspace_root>/
├── <service_name>/              # Individual project directory (e.g., router, auth, useraccount)
│   ├── main.py                  # Fast API or WebSocket entry point
│   ├── ReadMe.md                # Setup and configuration details
│   ├── pyproject.toml           # Dependency list
│   ├── vitals.yaml              # Health check parameters
│   └── app/                     # Core application module
│       ├── agents/              # Individual conversational agent definitions
│       │   └── <agent_name>/
│       │       ├── agent.py     # Configures the agent, lists tools, and declares child agents
│       │       ├── prompt.py    # Defines raw prompt strings and formatting logic
│       │       └── tools.py     # Implements agent-specific tool methods
│       ├── config/              # Environment configuration module
│       │   ├── app.py           # General settings and global prompts
│       │   └── state.py         # Defines state machine keys and initializer dictionaries
│       └── services/            # Back-end service integrations
```

--------------------------------------------------------------------------------

## 2. Ingestion & Parsing Directives

Unlike declarative frameworks, ADK agent behaviors are defined procedurally in
Python. The parser MUST dynamically discover and extract conversational behaviors
following these directives (do not assume the specific names in the examples below
are present in the target codebase):

### A. Agent Registry Parsing

1.  **Root Agent Discovery**: Identify the primary entry agent by inspecting the
    application entry points (e.g., `main.py` or the main router service). Locate
    its definition file (typically under `app/agents/<root_agent_name>/agent.py`)
    and extract the root agent class declaration.
2.  **Sub-Agent Mapping**: Trace how child agents are registered. Look for
    dictionaries or lists mapping states to agents (common patterns include
    variables like `state_agents`, `sub_agents`, or transition mappings) to
    establish the agent hierarchy.
3.  **Registered Tools Identification**: Map python tool functions passed to the
    agent constructor (typically via a `tools=[...]` argument or decorator). Trace
    their parameter structures in the corresponding `tools.py` or imported modules.
    *   **Rule**: If a python tool function invokes helper methods from an external
        toolset (e.g., `tools.<toolset_name>_<operation>`), classify this tool as
        a **Webhook**. Recursively extract its parameter/response schemas from the
        associated OpenAPI specification (typically found in
        `toolsets/<toolset_name>/open_api_toolset/open_api_schema.yaml` or similar).

### B. Prompt & Constraint Extraction

Read the prompt definition files (typically `prompt.py` or `prompts.py`) associated
with each discovered agent:

1.  **Primary Prompt Text**: Locate the core prompt string variables containing
    system instructions (e.g., variables ending in `_PROMPT`).
2.  **Custom Verbalization Rules**: Extract programmatic formatting blocks or
    string concatenations that append mandatory verbal instructions (e.g., rules
    forcing the agent to relay messages verbatim or format specific outputs).

### C. State Machine & Variable Mapping

Because state transitions are written in Python, you MUST map the context
variables used for flow control (typically defined in `app/config/state.py` or
equivalent state configuration files):

1.  **Context Variables**: Catalog all state keys or context variables (e.g.,
    session variables, flags, or status codes) that act as triggers for branching.
2.  **Transition Conditions**: Analyze the agent's decision logic (e.g., in
    `callbacks.py` or transition handler methods) to map conditional checks
    directing flows to other agents (e.g., checking if a user is authenticated
    before transferring to a secure agent).

--------------------------------------------------------------------------------

## 3. Dialogue Simulation Guidelines

When simulating natural dialogue transcripts from parsed ADK models, follow
these guidelines:

1.  **Dialogue Entry Triggers**: Start the dialogue with a `User` turn that
    naturally triggers the entry conditions for the target state or agent being
    tested.
2.  **Strict Verbatim Playback**: If the extracted prompts contain explicit,
    non-negotiable formatting rules (e.g., spelling out numbers, avoiding specific
    phrases), the simulated `Agent` turns MUST strictly adhere to those rules.
3.  **Implicit Transitions**: Represent programmatic transitions (e.g., silent
    state updates, automatic transfers, or background confirmations) in the
    transcripts as immediate `system_action` blocks or silent transfers rather
    than generating artificial spoken turns.
