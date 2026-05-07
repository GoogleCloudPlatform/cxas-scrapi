# Agent Reviewer
A set of rules and automated procedures for identifying semantic issues, inconsistencies, and logical design flaws in conversational agent instructions and prompt logic.

Follow these steps carefully. **CRITICAL:** Before starting the review, you MUST initialize `todo.md` in the project workspace to track progress.

# Read Issue Definitions
Prior to starting, read all of the issue definitions below:

| Description | Load |
|-------------|-------|
| Instruction Logic | `definitions/instruction-logic.md` |
| Security | `definitions/security.md` |

# Procedure
- Using the template in `definitions/results-template.md`, first create an empty results file `<agent_name>-review-results-YYYYMMDD-HHMMSS.md` in the project workspace.

- Locate global_instruction.txt if it exists. This global_instruction.txt applies to all agents in the app.

- Locate guardrail policies from the guardrails folder if it exists. These guardrails apply to all agents in the app.

- You must always create an implementation plan in your analysis detailing the steps you will take to thoroughly analyze the agents and identify issues. In your plan, do not analyze the entire app at once. Break it down into manageable analysis units.
  - **Global Instructions + Guardrails + Agent Instructions + Tools**. When analyzing issues, ensure that you take into consideration the global instructions, defined guardrail policies, agent instruction.txt files, and tools, evaluating how their interaction could result in issues.
  - **Comprehensive Evaluation**. You must include the complete set of issue definitions and evaluate the agent against each issue.
  - **Analyze one agent at a time**. Thoroughly analyze and think about one agent at a time, thinking through all possible issues that could be present. As you complete the analysis of one agent, update the results file with the analysis of that agent and continue until all agents have been analyzed. 
  - **Review the results and refine until all aspects have been analyzed**. Carefully review the results file and the issue definitions once more, continue the analysis, and update the results file. You should review and refine at least twice to ensure comprehensive analysis.
  - **Use the linter**. Finally, use the `cxas lint` command to identify any additional issues that may have been missed. Append the results of the linter to the results file as is in the final section. `cxas lint --app-dir=<path> >> <report-file.md>`
  
- [Optional] Ask the user if they want to create an HTML version. If so, create a styled HTML version of the results file by using
  `.venv/bin/python3 .agents/skills/cxas-agent-foundry/scripts/md_to_html.py -i <report.md> -o <report.html>`

# Severity Definitions
For each of the issues, the severity is defined as follows. If the issue already has a predefined severity, use it. If it has a range of severity such as Medium-Critical, determine the appropriate severity level.

| Severity | Description |
| :--- | :--- |
| Critical | The issue will almost certainly cause complete agent failure, severe hallucination, or critical violations of safety/compliance constraints. |
| Very High | The issue will significantly degrade core capabilities, resulting in frequent incorrect outputs, logic breakdowns, or an inability to complete primary tasks. |
| High | The issue causes noticeable degradation in reliability. The agent may struggle with specific edge cases, misinterpret complex instructions, or produce suboptimal outputs. |
| Moderate | The issue causes minor inefficiencies or slight performance degradation. The agent generally functions correctly but may exhibit suboptimal reasoning or require occasional clarifying prompts.|
| Low | The issue has minimal impact on overall performance but represents a deviation from prompt engineering best practices. |