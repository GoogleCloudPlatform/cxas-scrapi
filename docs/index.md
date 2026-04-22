---
title: CXAS SCRAPI
description: The Python library and CLI for building, testing, deploying, and maintaining Google CX Agent Studio conversational agents.
hide:
  - navigation
  - toc
---

# CXAS SCRAPI

**The scripting API for Google CX Agent Studio — build, test, deploy, and maintain conversational agents with confidence.**

<div class="grid cards" markdown>

-   **Quick install**

    ---

    ```sh
    pip install cxas-scrapi
    ```

    Python 3.10+ · Apache 2.0 · [GitHub](https://github.com/GoogleCloudPlatform/cxas-scrapi)

</div>

---

## What is CXAS SCRAPI?

Building conversational agents on Google CX Agent Studio is powerful — but the platform's raw API can feel low-level when you want to do something practical, like running a full suite of evaluations, linting your agent configs before a release, or wiring your agent into a CI/CD pipeline. That's exactly what CXAS SCRAPI is for.

CXAS SCRAPI is a high-level Python library and CLI that wraps the official `google-cloud-ces` client. It gives you friendly Python classes for every CX Agent Studio resource — Apps, Agents, Tools, Guardrails, Sessions, Evaluations, and more — plus a `cxas` command-line tool that lets you pull and push agent configs, run evaluations, and lint your agents against 60+ best-practice rules. Whether you're working in a Jupyter notebook, writing a Python script, or wiring things together in a GitHub Actions workflow, SCRAPI fits naturally into your workflow.

---

<figure class="diagram">
  <img src="assets/diagrams/architecture.svg" alt="CXAS SCRAPI Architecture">
</figure>

---

## What can you do with it?

<div class="grid cards" markdown>

-   :fontawesome-brands-python: **Python API**

    ---

    Pythonic classes for every CX Agent Studio resource. Create, read, update, and delete Apps, Agents, Tools, Guardrails, Sessions, Variables, Versions, and more — all from clean, readable Python code.

    [API Reference →](api/index.md)

-   :material-console: **CLI**

    ---

    The `cxas` command-line tool brings your agent configs down to disk with `cxas pull`, validates them with `cxas lint`, and sends them back with `cxas push`. Everything you need to manage agents without touching the UI.

    [CLI Reference →](cli/index.md)

-   :material-test-tube: **Evaluations**

    ---

    Five evaluation types — Platform Goldens, Local Simulations, Tool Tests, Callback Tests, and Turn Evals — let you validate your agent's behavior end-to-end, from individual tool calls to full multi-turn conversations.

    [Evaluation Guide →](guides/evaluation/index.md)

-   :material-check-decagram: **Linter**

    ---

    60+ lint rules check your agent configurations against established best practices before you push. Catch problems like missing tool descriptions, unsafe guardrail configurations, and structural issues early.

    [Linting Guide →](guides/linting/index.md)

-   :material-robot: **AI Skills**

    ---

    The skills system brings AI-powered assistance directly into your development workflow. Skills can build, run, debug, and inspect your agents using the SCRAPI API as their backbone.

    [Skills Guide →](guides/skills/index.md)

-   :material-magnify: **Insights**

    ---

    Analyze conversation quality with CCAI Insights scorecards. Import, export, and manage scorecards programmatically or via the CLI.

    [Insights Guide →](guides/insights/index.md)

</div>

---

## Where to go next

<div class="grid cards" markdown>

-   :material-rocket-launch: **New here? Start with Getting Started**

    ---

    Install SCRAPI, authenticate with Google Cloud, and run your first command in minutes.

    [Get Started →](getting-started/index.md)

-   :material-console: **Want the CLI reference?**

    ---

    Every `cxas` command, flag, and example — all in one place.

    [CLI Reference →](cli/index.md)

-   :fontawesome-brands-python: **Building something in Python?**

    ---

    Full API docs for every class and method, auto-generated from docstrings.

    [API Reference →](api/index.md)

-   :material-book-open-variant: **Looking for a guide?**

    ---

    Step-by-step walkthroughs for evaluations, linting, CI/CD, branching, and more.

    [Guides →](guides/index.md)

</div>
