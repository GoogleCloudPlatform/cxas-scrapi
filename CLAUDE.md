# Project Knowledge & Context

## Official SCRAPI Documentation

The official CXAS SCRAPI docs are hosted at https://googlecloudplatform.github.io/cxas-scrapi/stable/ and are built from the `docs/` folder in this repository. **Treat `docs/` as the authoritative knowledge base for all SCRAPI questions** — consult it before answering questions or performing tasks involving SCRAPI, the `cxas` CLI, or CX Agent Studio agent development.

Key locations:

- `docs/getting-started/` — installation, authentication, IAM permissions, concepts, CLI & Python quickstarts
- `docs/cli/` — reference for every `cxas` command (pull, push, lint, run, ci-test, migrate, etc.)
- `docs/guides/agent-development/` — creating agents, pull/push workflow, branching, team collaboration
- `docs/guides/evaluation/` — golden tests, simulations, tool/callback tests, turn evals, mock tool responses
- `docs/guides/linting/` — lint rules, configuration, CI integration
- `docs/guides/skills/` — AI skills system (agent-foundry build/run/debug, hooks, installation)
- `docs/guides/migration/` — Dialogflow CX → CXAS migration
- `docs/api/` — Python SDK reference
- `docs/patterns/`, `docs/tutorials/`, `docs/design-guide/` — patterns, tutorials, agent design guidance
- `examples/` — working example projects
- `.agents/skills/` — installed agent skills (cxas-agent-foundry, cxas-sim-eval, etc.)

If local docs seem outdated relative to upstream, fetch the hosted site for the latest version.

## Context

This is a fork of GoogleCloudPlatform/cxas-scrapi. Corbin uses it to build and maintain a customer support chatbot in Google CX Agent Studio, and may modify the fork to fit his needs.
