# Agent Evaluation Framework (`skill_eval/`)

Evaluation benchmark framework for CXAS building using Scrapi CLI and skills.

## Table of Contents
1. [Setup & Installation](#setup--installation)
2. [Scenario & Rubric Definitions](#scenario--rubric-definitions)
3. [Fast Offline Dry Run / Verification Mode](#fast-offline-dry-run--verification-mode)
4. [Automated Quality Assurance (QA) & Anti-Slop Audit Checks](#automated-quality-assurance-qa--anti-slop-audit-checks)
5. [Running the Active Benchmark Evaluation](#running-the-active-benchmark-evaluation)

---

## Setup & Installation

```bash
uv pip install -r requirements.txt
```

You will need Gemini API key. You can get one from [Google AI Studio](https://aistudio.google.com/).

```bash
export GEMINI_API_KEY="your_api_key_here"
```

Configure GCP Application Default Credentials (ADC) on your machine to allow Vertex AI grading:

```bash
gcloud auth application-default login
```

Note that the provided key and project will be charged for Cloud/Gemini usage.

---

## Scenario & Rubric Definitions

Scenarios are standard YAML files placed inside `scenarios/`. Each scenario specifies:
- `name`: The unique name of the scenario.
- `context`: The user simulator's persona instructions.
- `steps`: Pre-defined checklist steps for verification.
- `rubric`: rubrics criteria to assess perfect, good, or failed completion.
- `assets`: Packaged files the agent has access to in its workspace.

Example:
```yaml
name: adding_guardrails
context: "You are a user looking to set up security guardrails..."
steps:
  - "Verify that policy was successfully initialized."
rubric:
  - criteria: "autonomous guardrail configuration"
    perfect: "Autonomously configured correct policies."
```

---

## Dry run

You can run the evaluation with a "fake" head to quickly verify the pipeline.

```bash
python run_benchmark.py --run_fake --run_antigravity=False
```

---

## Running the Active Benchmark Evaluation

To run the live evaluation using Gemini models and Vertex AI:

```bash
python run_benchmark.py
```
