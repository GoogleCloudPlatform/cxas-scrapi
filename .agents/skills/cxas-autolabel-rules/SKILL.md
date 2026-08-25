---
name: cxas-autolabel-rules
description: >-
  Author, validate, and manage Contact Center AI (CCAI) Insights Autolabeling Rules.
  Use when users want to translate business labeling logic into CEL condition expressions,
  maintain declarative autolabel_rules.yaml configurations, or synchronize rules to GCP projects.
---

# CCAI Insights Autolabeling Rules Skill

This skill guides you in authoring, refining, validating, and synchronizing **Contact Center AI (CCAI) Insights Autolabeling Rules**.

Autolabeling rules enrich ingested conversations with custom key-value metadata evaluated via Common Expression Language (CEL).

______________________________________________________________________

## 1. Overview & Declarative YAML Schema

Rules are defined declaratively in `autolabel_rules.yaml`:

```yaml
version: "1.0"
project_id: "your-gcp-project-id"
location: "us-central1"

autolabeling_rules:
  - rule_id: "agent_domain"
    display_name: "Agent Domain Classifier"
    label_key: "agent_domain"
    label_key_type: "LABEL_KEY_TYPE_CUSTOM"
    active: true
    conditions:
      - condition: "containsSubAgent(conversation, 'billing_specialist')"
        value: "'billing'"
      - condition: "containsSubAgent(conversation, 'tech_support')"
        value: "'tech_support'"
      - condition: ""
        value: "'general'"
```

### Core Schema Requirements

1. **`rule_id`**: Unique alphanumeric identifier (snake_case or kebab-case).
1. **`label_key`**: The metadata key name to attach to `conversation.labels`.
1. **`conditions`**: Ordered array of conditions evaluated top-to-bottom.
   - `condition`: CEL boolean expression (e.g. `conversation.duration > 300`).
   - `value`: CEL expression or quoted string literal (e.g. `'vip'`, `'escalated'`).
   - **Mandatory Fallback**: The final condition in every rule MUST be `condition: ""` to act as the default fallback value.

______________________________________________________________________

## 2. Common Expression Language (CEL) Authoring Rules

Refer to the [CEL Cookbook](references/cel_cookbook.md) for full syntax and function references.

### Built-in Helper Functions

- **Sub-Agent / Flow Detection**:
  ```cel
  containsSubAgent(conversation, "billing_subagent")
  ```
- **Session Parameter Matching**:
  ```cel
  hasSessionParam(conversation, "authenticated", "true")
  ```
- **Sentiment Analysis**:
  ```cel
  hasCallerSentiment(conversation, "NEGATIVE")
  ```
- **Turn & Duration Checks**:
  ```cel
  conversation.duration > 300 && conversation.turnCount >= 10
  ```

______________________________________________________________________

## 3. Step-by-Step Workflow

Follow these steps when helping a user build or update autolabeling rules:

### Step 1: Ingest Requirements

- Ask the user what conversation properties or behaviors they want to classify (e.g., specific sub-agents, customer sentiment, authentication status, duration thresholds).
- Determine target GCP project ID and location.

### Step 2: Draft or Edit Declarative YAML

- Create or update `autolabel_rules.yaml` in the user's workspace.
- Translate business logic into clear, prioritized CEL expressions.
- Ensure every rule ends with a fallback (`condition: ""`).

### Step 3: Compare with Active Remote Rules (`diff`)

Run `diff` to preview additions, modifications, and deletions:

```bash
uv run cxas insights diff-autolabel-rules --file autolabel_rules.yaml
```

### Step 4: Dry-Run Deploy

Run `push` with `--dry-run` to verify API compatibility:

```bash
uv run cxas insights push-autolabel-rules --file autolabel_rules.yaml --dry-run
```

### Step 5: Deploy to GCP Project (`push`)

Deploy the changes:

```bash
uv run cxas insights push-autolabel-rules --file autolabel_rules.yaml
```

*(To delete remote rules that are no longer in the local YAML, append `--force`.)*

### Step 6: Export Existing Rules (`pull`)

To export existing active rules from an environment into YAML:

```bash
uv run cxas insights pull-autolabel-rules --parent projects/<PROJECT>/locations/<LOCATION> --out autolabel_rules.yaml
```

______________________________________________________________________

## 4. References & Tooling

- **[CEL Cookbook](references/cel_cookbook.md)**: Curated expressions and helper function recipes.
- **[JSON Schema](references/schema.json)**: Schema definition for YAML linting.
- **[Design Document](references/design.md)**: Architectural specification.
