# cxas llm-lint

`cxas llm-lint` is an AI-driven semantic prompt linter that uses Gemini to review natural language rules, tone, persona consistency, style guidelines, and logical contradictions across your GECX sub-agent instructions.

Unlike traditional static linters, `cxas llm-lint` understands the intent and nuances of human language, helping you craft robust, high-quality prompt engineering before deploying your conversational agents.

---

## Static Linting (`cxas lint`) vs AI Semantic Linting (`cxas llm-lint`)

When developing agents in CX Agent Studio, you should use both linters at different stages of your workflow:

| Feature | `cxas lint` (Static Structural Linter) | `cxas llm-lint` (AI Semantic Linter) |
| :--- | :--- | :--- |
| **Primary Focus** | File layout, YAML/JSON schemas, `app.yaml`/`app.json` configs, callback names, missing mandatory files. | Semantic meaning, clarity, persona adherence, tone, edge-case handling, logical prompt contradictions. |
| **Mechanism** | Deterministic rule engine across 60+ static structural checks. | Advanced LLM analysis using Google Gemini (`gemini-2.5-flash` by default). |
| **Speed** | Instantaneous (< 1 second). | A few seconds per agent/callback state. |
| **Target Scope** | Entire application directory or individual components. | A single sub-agent directory (`instruction.txt`, `global_instruction.txt`, dynamic callbacks). |
| **When to Run** | **Continuously** while authoring files, inside pre-commit hooks, and as an automated CI fast-fail gate. | **Iteratively** when writing/refactoring prompts, before peer code reviews, or during qualitative QA. |

---

## Usage

```bash
cxas llm-lint --agent-dir DIR
              [--project-id ID]
              [--location REGION]
              [--model MODEL_NAME]
              [--output FILE_PATH]
```

## Options

| Option | Required | Default | Description |
| :--- | :--- | :--- | :--- |
| `--agent-dir DIR` | **Yes** | — | Path to the specific sub-agent directory containing `instruction.txt`. |
| `--project-id ID` | No | Auto-detected | GCP Project ID for Vertex AI Gemini queries. If omitted, checks environment variables or `gecx-config.json`. |
| `--location REGION` | No | `us-central1` | GCP region for Vertex AI endpoints. |
| `--model MODEL_NAME` | No | `gemini-2.5-flash` | Gemini model name to use for semantic analysis. |
| `--output FILE_PATH`| No | `<agent_dir>/llm_lint_report.md` | Optional path to save the generated Markdown lint report. |

---

## How It Works

When you invoke `cxas llm-lint` on a sub-agent directory, the tool performs a multi-stage semantic analysis:

1. **Context Resolution:** Automatically walks up the directory tree to discover `global_instruction.txt` (if present) so Gemini understands overarching platform constraints and brand guidelines.
2. **Base Prompt Review:** Deeply analyzes the sub-agent's primary `instruction.txt` against standard conversational AI best practices (such as avoiding negative prompts, ensuring clear turn-taking, and verifying robust escalation pathways).
3. **Dynamic State Machine Review:** Discovers any dynamic state instructions defined inside Python callback files (`before_agent_callbacks`, `after_agent_callbacks`, etc.). It parses the Python abstract syntax tree (AST) to extract state-machine dictionary mappings and individually reviews each dynamic instruction state.
4. **Architecture Guardrails:** Warns developers if dynamic prompt instructions are placed in non-standard callbacks outside of `before_agent_callbacks`.

---

## Examples

### 1. Lint a Sub-Agent's Base Instructions
Run a semantic check on the `support` sub-agent:

```bash
cxas llm-lint --agent-dir ./my-app/agents/support
```
*Outputs actionable critique to stdout and writes a full structured report to `./my-app/agents/support/llm_lint_report.md`.*

### 2. Use a Advanced Model Tier
For extremely complex reasoning or subtle tone checks, run with Gemini Pro or Flash Premium:

```bash
cxas llm-lint --agent-dir ./my-app/agents/billing --model gemini-2.5-pro
```

### 3. Specify a Custom Output Report Path
Export the semantic analysis report directly to your team's QA artifacts directory:

```bash
cxas llm-lint --agent-dir ./my-app/agents/triage --output ./reports/triage_semantic_lint.md
```
