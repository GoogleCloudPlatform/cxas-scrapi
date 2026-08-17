# CXAS SCRAPI Development & Agent Guide

Welcome to **CXAS SCRAPI** (`cxas_scrapi`). This guide outlines the available developer agents, skills, and tools in this repository.

## Repository Setup & Tooling

Requires Python 3.10+ and [astral-uv](https://docs.astral.sh/uv/) for high-speed package management.

- **Sync dependencies**: `uv sync --all-extras`
- **Run tests**: `uv run pytest`
- **Lint & format**: `uv run ruff check . && uv run ruff format .`
- **Pre-commit checks**: `uv run pre-commit run --all-files`

## Available Skills

This workspace provides several specialized AI skills to assist with development.

- **`cxas-agent-foundry`**: The primary skill for the end-to-end GECX agent lifecycle. Use this for building agents from PRDs, generating and running evals, debugging failures, and syncing code.
- **`cxas-autolabel-rules`**: Author, validate, and manage Contact Center AI (CCAI) Insights Autolabeling Rules declaratively via YAML and CEL.
- **`cxas-composite-voice-optimization`**: Skill for optimizing Gemini Composite V1 voice naturalness, persona styling, empirical physical acoustic tagging, and Rule A007 multi-language parity.
- **`cxas-configurable-dashboards`**: Author, validate, and manage CCAI Insights Configurable Dashboards declaratively via YAML, Vega-Lite specs, and SQL metrics queries.
- **`cxas-sim-eval`**: A utility skill for converting CXAS golden evaluations to SCRAPI SimulationEvals test cases.

## CLI Features

The `cxas` CLI tool provides programmatic interaction with Dialogflow CX agents and Google Cloud resources. Run `cxas --help` to explore available subcommands.
