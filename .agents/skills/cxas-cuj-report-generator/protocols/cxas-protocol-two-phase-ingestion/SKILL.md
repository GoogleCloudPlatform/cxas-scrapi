---
name: cxas-protocol-two-phase-ingestion
description: "A two-phase protocol for extracting structure and generating transcripts from customer artifacts."
---

# Two-Phase Ingestion Protocol

This protocol defines a strict two-phase process for analyzing customer
artifacts to extract system structure and generate high-fidelity conversation
transcripts.

## Phase 1: Structural Digestion

-   **Goal**: Extract raw data from artifacts into a digestible form (e.g., YAML
    or structured Markdown).
-   **Scope**: Map the exact Taxonomy, Intents, Tools, Connections, state
    transitions, and node logic.
-   **Rule**: DO NOT infer transcripts during this phase. Focus purely on
    structural extraction.
-   **Exception**: If explicit transcripts are already present in the raw data
    (e.g., inside Cyara XML test data), they can be extracted directly.

## Phase 2: Transcript Generation

-   **Goal**: Generate full natural conversation transcripts based on the
    digested structural data.
-   **Mechanism**: Use LLM reasoning to read the structural data and generate
    transcripts.
-   **Rule**: Must use the `./append_turn.py` script (located in the parent skill directory)
    to build transcripts turn-by-turn to ensure schema compliance.
