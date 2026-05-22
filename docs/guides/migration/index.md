---
title: Agent Migration & Optimization
description: Automatically convert Dialogflow CX flows and playbooks into modular CXAS agents, complete with topology analysis and prompt optimization.
---

# Agent Migration & Optimization

If you have existing conversational experiences built in **Dialogflow CX (DFCX)**, you don't need to rebuild them from scratch in **CX Agent Studio (CXAS)**. SCRAPI provides a powerful, automated migration toolchain that analyzes your legacy agent, maps its architecture to CXAS concepts, resolves dependencies, and generates optimized instructions and tools.

Whether your DFCX agent uses traditional deterministic Flows (pages, routes, and webhooks), generative Playbooks, or a hybrid combination of both, SCRAPI's migration module handles the heavy lifting of transitioning to, and optimizing for, a modular CXAS multi-agent architecture.

---

## The Migration Workflow

Migrating a DFCX agent involves four main phases:

1.  **Export & Load:** Export your DFCX agent as a JSON/ZIP bundle or provide its live Agent ID.
2.  **Analyze & Select:** Use SCRAPI's dependency analyzer to inspect the agent's topology and intermediate representation, and select which Playbooks and Flows to migrate.
3.  **Transform & Optimize:** SCRAPI automatically converts DFCX pages and routes into CXAS agents, transforms webhooks into Python tools, and optimizes instructions using advanced AI passes.
4.  **Deploy & Verify:** Push the generated resources to your CXAS project and review the automated audit reports and topology diagrams.

---

## What's in this section

`DFCX to CXAS Migration`
:   Step-by-step walkthrough of the interactive `cxas migrate dfcx` CLI dashboard, explaining every configuration option, how to select resources, and how to review the generated output.

---

## Quick Start

To launch the interactive migration dashboard immediately:

```bash
cxas migrate dfcx
```

For a detailed walkthrough of the process, start with [DFCX to CXAS Migration](dfcx-migration.md).
