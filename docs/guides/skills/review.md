---
title: Review Skill
description: Using the Agent Reviewer skill to audit instructions for logical flaws and security risks.
---

# Review Skill

The Review skill acts as an automated "Agent Reviewer" that audits your conversational agents for semantic issues, logical design flaws, and security risks. Unlike the [Run skill](run.md) which tests behavior via evaluations, the Review skill performs a deep inspection of the instructions and prompt logic itself.

Think of it as a senior prompt engineer performing a code review on your agent's configuration.

---

## Invoking the Review skill

The foundry routes you to Review when you express an intent like:

- "Review the agent prompts"
- "Audit the instructions for logic issues"
- "Look for security vulnerabilities in the prompt"
- "Analyze my instruction logic"

The Review skill is a sub-skill of the [Agent Foundry](agent-foundry.md) — it is automatically routed to when the foundry detects a review or audit intent.

---

## What the Review skill analyzes

The Reviewer doesn't just look at files in isolation. it analyzes the entire application context:

1. **Global Instructions**: Checks `global_instruction.txt` which applies to all agents.
2. **Guardrail Policies**: Inspects policies in the `guardrails/` folder for safety and compliance.
3. **Agent Instructions**: Analyzes `instruction.txt` for each agent.
4. **Tool Definitions**: Evaluates how agents interact with tools and whether those interactions are safe and logical.

---

## The Review Workflow

The Review skill follows a rigorous process to ensure comprehensive analysis:

### 1. Initialization
The skill initializes a `todo.md` file in your project workspace to track its progress. It also creates a timestamped results file: `<agent_name>-review-results-YYYYMMDD-HHMMSS.md`.

### 2. Analysis Phase
The Reviewer breaks the app down into manageable units. It analyzes one agent at a time, considering its specific instructions alongside global instructions and guardrails.

It checks for categories such as:

- **Instruction Logic**: Missing edge case handling, circular logic, or contradictory instructions.

- **Security**: Prompt injection vulnerabilities, sensitive data exposure, or unauthorized tool access.

- **Consistency**: Ensuring the agent's persona and rules remain consistent across different scenarios.

### 3. Refinement
The skill is designed to "think twice." After the initial analysis, it reviews its own findings and issue definitions to ensure nothing was missed. It refines the results at least twice before finalizing the report.

### 4. Linter Integration
Finally, the skill runs `cxas lint` to catch structural or schema issues and appends those results to the final report.

---

## Severity Definitions

Each issue identified is assigned a severity level:

| Severity | Impact |
| :--- | :--- |
| **Critical** | Almost certain to cause agent failure, severe hallucination, or safety violations. |
| **Very High** | Significantly degrades core capabilities or causes frequent incorrect outputs. |
| **High** | Noticeable degradation in reliability or failure in complex edge cases. |
| **Moderate** | Minor inefficiencies or slight Reasoning errors; generally functions correctly. |
| **Low** | Minimal impact; deviation from prompt engineering best practices. |

---

## Understanding the Results

The Review report includes a **Summary Table** and **Detailed Observations**.

### Summary Table
The table provides a quick view of every issue found:

| Column | Description |
| :--- | :--- |
| **reference** | The specific file and line number(s) where the issue occurs. |
| **issue_name** | A concise name for the problem (e.g., "Circular Logic"). |
| **severity** | The impact level of the issue. |
| **description** | How the issue manifests in your specific agent. |
| **recommendation** | Actionable steps to fix the problem. |

### Reports in HTML
You can ask the skill to generate a styled HTML version of the report for easier sharing with your team:

```bash
.venv/bin/python3 .agents/skills/cxas-agent-foundry/scripts/md_to_html.py -i <report.md> -o <report.html>
```

---

## Next Steps

Once you've reviewed the findings:

1. **Apply Recommendations**: Use the suggestions in the report to update your `instruction.txt` or tool definitions.

2. **Re-run Review**: Verify the fixes by asking the skill to "review the changes."

3. **Run Evals**: Use the [Run skill](run.md) to confirm that the behavioral performance matches the logical improvements.
