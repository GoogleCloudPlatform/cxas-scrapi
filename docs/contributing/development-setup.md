---
title: Development Setup
---

# Development Setup

This guide walks you through setting up a local development environment for contributing to CXAS SCRAPI. The process is straightforward — you'll be up and running in a few minutes.

## Prerequisites

- **Python 3.10 or later** — SCRAPI supports Python 3.10, 3.11, and 3.12.
- **Git** — for version control.
- **gcloud CLI** — recommended for authentication during development.

## Fork and Clone

Start by forking the repository on GitHub, then clone your fork locally:

```bash
git clone https://github.com/<your-username>/cxas-scrapi.git
cd cxas-scrapi
```

## Set Up a Virtual Environment

We recommend using a virtual environment to keep your dependencies isolated:

```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

## Install in Editable Mode

Install the package in editable mode so that your code changes are immediately reflected without reinstalling:

```bash
pip install -e .
pip install -r requirements.txt
```

This installs SCRAPI along with all development dependencies (ruff, pytest, etc.) and additional runtime dependencies.

If you also want to build the documentation locally:

```bash
pip install -r requirements-docs.txt
```

## Install Pre-commit Hooks

We use pre-commit hooks to catch issues before they reach CI. Install them with:

```bash
pip install pre-commit
pre-commit install
```

This sets up automatic linting (via `ruff`) and test execution on every commit.

## Verify Your Setup

Run the test suite to make sure everything is working:

```bash
pytest
```

And verify the CLI is available:

```bash
cxas --help
```

You should see the full list of available commands.

## Building the Documentation Locally

To preview documentation changes as you write:

```bash
mkdocs serve
```

This starts a local server at `http://127.0.0.1:8000` with live reload — any changes you save will appear in the browser automatically.

## Project Layout

Here's a quick orientation of the repository:

| Directory | What's inside |
|-----------|---------------|
| `src/cxas_scrapi/core/` | Core resource classes (Apps, Agents, Tools, etc.) |
| `src/cxas_scrapi/cli/` | CLI entry points and command handlers |
| `src/cxas_scrapi/evals/` | Evaluation runners (tool, simulation, callback, etc.) |
| `src/cxas_scrapi/utils/` | Utilities, linter engine, and lint rules |
| `tests/` | Unit tests (mirrors the `src/` structure) |
| `docs/` | Documentation source (MkDocs) |
| `.agents/skills/` | AI development skills |
| `examples/` | Usage examples and sample configs |

## Next Steps

- Review the [code style guide](code-style.md) before writing code.
- Read the [PR submission guide](pull-requests.md) when you're ready to contribute.
