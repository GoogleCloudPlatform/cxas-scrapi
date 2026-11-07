---
title: Tutorials
description: Step-by-step tutorials for building complete, production-ready CXAS agents with SCRAPI — from project setup through evaluations.
---

# Tutorials

Tutorials walk you through building complete agents and workflows from scratch. Unlike guides — which explain SCRAPI's individual features — tutorials give you a finished, working implementation with real code, real evaluations, and the full context of why each decision was made.

---

## Available tutorials

<div class="grid cards" markdown>

-   :material-robot: **Setting Up Your Agentic Development Environment**

    ---

    Learn how to pair an agent harness with CXAS SCRAPI to automate the agent development lifecycle: bootstrapping agents from PRDs, running local evaluations, and iteratively improving configurations.

    [Start Tutorial →](agentic-development-env.md)

</div>

---

## What you'll learn

In the **Agentic Development Environment** tutorial, you'll learn how to:

- Set up a local development environment treating CXAS agent configurations as code.
- Connect an agent harness (e.g., Antigravity or another AI coding assistant) with SCRAPI.
- Bootstrap a functional agent from requirements using SCRAPI CLI and Python SDK.
- Configure local evaluations, run tests against your agent configurations, and analyze evaluation logs.
- Establish a source-control-first workflow (branching, pull requests, CI/CD) for multi-developer teams.

---

## Prerequisites

Before starting the tutorial, ensure you have:

- Python 3.10 or later
- A Google Cloud project with CX Agent Studio enabled
- SCRAPI installed: `pip install cxas-scrapi`
- SCRAPI authenticated: `gcloud auth application-default login`
- An agent harness or AI coding assistant

[Start the tutorial →](agentic-development-env.md)
