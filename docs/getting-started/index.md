---
title: Getting Started
description: Everything you need to go from zero to a running CXAS SCRAPI setup.
---

# Getting Started

Welcome! If you've never used CXAS SCRAPI before, you're in the right place. This section will walk you through everything you need to go from a fresh environment to running your first evaluation, linting your first agent, or writing your first Python script against the CX Agent Studio API.

Don't worry if you're not sure where to start — pick the path below that matches what you're trying to do, and the guides will meet you where you are.

---

## Choose your path

=== "I want to install SCRAPI"

    Start here if you haven't installed the library yet, or if you want to understand requirements and set up a virtual environment properly.

    !!! tip "Recommended first step"
        Even if you're jumping straight to a quickstart, you'll want to install first.

    [Installation →](installation.md){ .md-button .md-button--primary }

=== "I need to set up authentication"

    SCRAPI talks to Google Cloud on your behalf, so you'll need credentials. This guide covers every auth method — local development, Colab, Cloud Run, service accounts, and OAuth tokens.

    !!! info "Authentication is required for everything"
        All SCRAPI commands and API calls require valid Google Cloud credentials.

    [Authentication →](authentication.md){ .md-button .md-button--primary }

=== "I want to write Python code"

    If you're building scripts, notebooks, or applications with the SCRAPI Python API, the Python quickstart will get you from `import` to your first API response in under five minutes.

    [Python Quickstart →](quickstart-python.md){ .md-button .md-button--primary }

=== "I want to use the CLI"

    If you prefer the command line, the CLI quickstart shows you how to pull agent configs, run the linter, push changes, and run tests — all from your terminal.

    [CLI Quickstart →](quickstart-cli.md){ .md-button .md-button--primary }

---

## What's covered in Getting Started

Here's a quick overview of each page in this section, so you know what to expect:

`Installation`
:   How to install `cxas-scrapi` with pip, set up a virtual environment, install from source, and verify your setup. Covers Python 3.10+ requirements and the `gcloud` CLI recommendation.

`Authentication`
:   A full walkthrough of how SCRAPI finds your credentials, in priority order. Covers Application Default Credentials (ADC) via the gcloud CLI, Google Colab interactive auth, Cloud Functions and Cloud Run ambient credentials, service account JSON keys, and the `CXAS_OAUTH_TOKEN` environment variable.

`Python Quickstart`
:   Four progressive tasks — list your Apps, list Agents, send a test message, and run a simple evaluation — with complete, runnable code for each.

`CLI Quickstart`
:   Five practical tasks — list apps, pull a config, lint it, push changes, and run tool tests — showing how to use the `cxas` command to manage your agents from the terminal.

`Key Concepts`
:   A conceptual overview of the resource hierarchy (App → Agent → Tools/Callbacks/Guardrails), the `app_name` resource identifier format, the five evaluation types, the linter, the skills system, and the "create on platform, edit locally" workflow philosophy.

---

!!! question "Not sure where to go after this?"
    Once you've finished Getting Started, head to the [Guides](../guides/index.md) section for deeper dives into specific topics like CI/CD, branching, and advanced evaluations.
