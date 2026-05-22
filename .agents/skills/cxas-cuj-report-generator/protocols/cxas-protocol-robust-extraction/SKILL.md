---
name: cxas-protocol-robust-extraction
description: "A robust methodology for LLM-based requirements gathering and high-fidelity artifact generation. Employs 'Divide, Conquer, and Verify' tactics using specialized subagents, iterative exhaustion loops, and batched execution to ensure zero data loss."
---

# Robust Extraction Protocol

This protocol defines the standard operating procedure for extracting exhaustive
requirements (like subintents, CUJs, or logic rules) from large, complex, or
fragmented customer artifacts.

It prevents the common LLM pitfalls of "context drift" and "truncation" by
enforcing a strict "Divide, Conquer, and Verify" methodology.

## General Principles & Anti-Hallucination Guardrails

To ensure 100% coverage and prevent data loss due to tool limits or implicit
filtering, follow these principles across all phases:

*   **Quantify the Scope**: Before spawning any subagents or starting
    extraction, determine the exact total count of target items (files,
    directories, database rows). Record this number as your "Success Target."
    You must verify that the sum of items processed equals this target before
    proceeding to consolidation.
*   **Coverage over Curation**: Default to 100% extraction coverage. Never
    assume the user only wants the "top" or "most interesting" items unless
    explicitly instructed to apply a quality filter. A standard or repetitive
    item is still data that must be reported.
*   **Circumvent Tool Caps**: Be aware that search and listing tools often have
    display limits (e.g., capped at 50 or 1000 results). If the expected scale
    (from the Quantify step) exceeds the tool's limit, you must partition the
    work (e.g., by alphabet or ID range) to ensure no items are hidden by the
    tool's cap.
*   **Maintain Traceability**: For every extracted requirement or item, record
    the source file or location it was extracted from. This allows for easy
    verification and provides context when reviewing the consolidated results.

## Core Directives

When tasked with comprehensive extraction or generation from a large corpus, you
MUST follow this four-phase methodology:

### Phase 1: Parallel Expert Discovery

Never use a single generalist agent or a single prompt to read all files.

1.  Categorize the input artifacts (e.g., Code/ADK, Diagrams, Test Cases).
2.  Spawn **specialized expert subagents** (e.g., `cxas-ingestor-adk`) in
    parallel, providing each with only the context relevant to their expertise.
3.  Consolidate their initial findings into a centralized list.

### Phase 2: The Iterative Exhaustion Loop

LLMs often miss items in a single pass of a large document. You must force them
to iterate.

1.  Provide the current consolidated list of findings back to the expert
    subagents.
2.  Ask a direct question: *"Based on your artifacts, are there ANY MORE items
    missing from this list? If yes, list them. If no, reply EXACTLY 'NO'."*
3.  **The Loop Rule:** You MUST continue this loop, updating the consolidated
    list each time, until **ALL** expert subagents unanimously reply with "NO".

### Phase 3: Logical Clustering

Once the exhaustive list is finalized (e.g., 100+ subintents), organize it.

1.  Group the granular findings into high-level logical categories (Parent
    CUJs).
2.  Verify with the experts that the parent categories encompass all findings.

### Phase 4: Batched Execution & Verification

Never ask an LLM to generate 100+ complex artifacts (like conversational
transcripts) in a single prompt. It will hallucinate or truncate.

1.  **Batching:** Divide the exhaustive list into small, manageable batches
    (e.g., 10 batches of 10 items). Write these batches to temporary files.
2.  **Delegated Execution:** Spawn a new subagent for *each* batch. Instruct
    them to process *only* their assigned batch and write the output to a
    specific file.
3.  **The Verification Gate:** As the orchestrator, you MUST verify the output
    of each subagent. Did they generate an output for *every single item* in
    their batch?
4.  If YES: Accept the batch.
5.  If NO: Discard the output and **respawn** the subagent for that specific
    batch with stronger steering instructions.
6.  **Consolidation:** Only when all batches pass the Verification Gate, merge
    them into the final, exhaustive deliverable.
