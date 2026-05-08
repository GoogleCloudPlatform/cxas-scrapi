# Agent Reviewer
A set of rules and automated procedures for identifying semantic issues, inconsistencies, and logical design flaws in conversational agent instructions and prompt logic.

# Read Issue Definitions
Prior to starting, read all of the issue definitions below:

| Category | Load |
|-------------|-------|
| Instruction Logic | `definitions/instruction-logic.md` |
| Security | `definitions/security.md` |
| Multi-Agent | `definitions/multi-agent.md` |

## Issue Severity Definitions
For each of the issues, the severity is defined as follows. If the issue already has a predefined severity, use it. If it has a range of severity such as Medium-Critical, determine the appropriate severity level.

| Severity | Description |
| :--- | :--- |
| Critical | The issue will almost certainly cause complete agent failure, severe hallucination, or critical violations of safety/compliance constraints. |
| Very High | The issue will significantly degrade core capabilities, resulting in frequent incorrect outputs, logic breakdowns, or an inability to complete primary tasks. |
| High | The issue causes noticeable degradation in reliability. The agent may struggle with specific edge cases, misinterpret complex instructions, or produce suboptimal outputs. |
| Moderate | The issue causes minor inefficiencies or slight performance degradation. The agent generally functions correctly but may exhibit suboptimal reasoning or require occasional clarifying prompts.|
| Low | The issue has minimal impact on overall performance but represents a deviation from prompt engineering best practices. |

# Templates
Read the following templates;

| Name | Description | Path |
| :--- | :--- | :--- |
| `results-template.md` | The template for the results | `definitions/results-template.md` |

# Sub reviewer agents
If you are able to invoke sub-agents, read the sub agent definition below:

| Name | Description | Path | 
| --- | --- | --- |
| Reviewer | The reviewer agent that will be doing the review work. | `.gemini/agents/reviewer.md` | 

# ALWAYS DELEGATE IF POSSIBLE
If you are able to delegate to other agents, read `.gemini/agents/reviewer.md` which is a sub-agent doing the review work. Delegate one reviewer per sub-agent that you are analyzing. For example, if you have 3 sub-agents, delegate 3 reviewers. Once you have the results from each of the reviewer, comibne them into the final results template. In your implementation plan, make sure you outline the delegation.

To prevent throttling, you must invoke the sub-agents sequentially one at a time.

# Single-Agent (Component) and Multi-Agent (System) Analysis
This section will provide you an explanation of the different levels of analysis. You will be tasked to do single-agent, multi-agent, or both. 

## Single-Agent Analysis
In this analysis, you are focused on reviewing a single agent in isolation. You must understand the interactions between the following components:
  - Global Instructions (Any global instructions from the global_instruction.txt file)
  - Guardrails (Any guardrail policies from the guardrails folder)
  - instruction.txt (The agent's instruction)
  - callbacks (Any callbacks for the specific agent)
  - tools (Tools that are attached to the agent)

In doing your analysis for the single-agent, you must consider all of the components above as they combine to form the behavior of the agent.

## Multi-Agent Analysis
In this analysis, you are focused on groups of sub-agents that are related to each other. For example, multiple sub agents could be working together for a particular use case such as billing. In this analysis, you are focusing on issues found in the `multi-agent.md` file, as well as any other related issues. You must understand the interactions between the sub-agents and how they combine to form the behavior of the system. 

You must:
   - Identify groups of sub-agents that could be working together
   - How the conversation may move from one sub-agent to another sub-agent



# MAIN AGENT PROCEDURE
If you are the main agent that will be delegating to other sub review agents. Follow the procedure below.

1. Using the template in `definitions/results-template.md`, first create an empty combined results file `<agent_name>-review-results-YYYYMMDD-HHMMSS.md` in the project workspace.

2. Understand the structure of the app/agent to be reviewed.
- Locate the global_instruction.txt in the root of the application/agent.
- Locate the guardrails in the guardrails folders.
- Locate the agent files in the agents folders.
  - Identify groupings of agents that are clearly working together for a particular use case.
- Locate the tools and toolsets folders.

3. **CRITICAL** You must always create an implementation plan and `todo.md` detailing the complehensive steps that you will take. The implementation plan must include the following:

- **Phase 1: Single-agent analysis.** For each of the agents in the application, assign a reviewer sub-agent to conduct a single-agent analysis. For example,
  - agents/agent_a delagated to reviewer agent 1
  - agents/agent_b delegated to reviewer agent 2
  - ...

- **Phase 2: Multi-agent analysis.** For each of the groups of agents that are working together, conduct a multi-agent analysis.
  - agents/group_a_agent_a1, agents/group_a_agent_a2, ... delegated to reviewer agent 3
  - agents/group_b_agent_b1, agents/group_b_agent_b2, ... delegated to reviewer agent 4

- **Phase 3: Compilation.** Once all sub-agents have completed their analysis, combine their results into the final results file. Make sure to have different sections for agents as defined in `definitions/results-template.md`. 

- **Phase 4: Final Linting.** Run the lint command with the redirection operator to directly append to the report file without viewing the results. Use the command exactly as provided: `.venv/bin/cxas lint --app-dir=<path> >> /path/to/<agent_name>-review-results-YYYYMMDD-HHMMSS.md`

- **Phase 5: Optional HTML Generation.** Ask the user if they want to create an HTML version. If so, create a styled HTML version of the results file by using
  `.venv/bin/python3 .agents/skills/cxas-agent-foundry/scripts/md_to_html.py -i <report.md> -o <report.html>`


4. Execute on the implementation plan once approved.
  

# STANDALONE OR DELEGEE PROCEDURE
You are working alone (not working with a main agent) or you are a delegee sub-agent working with a main agent. Follow the procedure below.

1. Understand if you are doing a single or multi-agent analysis.

1. Understand the structure of the app/agent to be reviewed.
- Locate the global_instruction.txt in the root of the application/agent.
- Locate the guardrails in the guardrails folders.
- Locate the agent files in the agents folders.
  - Identify groupings of agents that are clearly working together for a particular use case.
- Locate the tools and toolsets folders.

2. Using the template in `definitions/results-template.md`, first create an empty results file `<agent_name>-review-results-YYYYMMDD-HHMMSS.md` in the project workspace.

3. **CRITICAL** You must always create an implementation plan and `todo.md` in your analysis detailing the steps you will take to thoroughly analyze the agents and identify issues. In your plan, do not analyze the entire app at once. Break it down into manageable analysis units. If there are multiple issues present for the same instruction, make sure to include all of them.
  - **Perform single-agent analysis**. Conduct a single-agent analysis for each of the agents that you are assigned. 

  - **Comprehensive Evaluation**. You must include the complete set of issue definitions and evaluate the agent against each issue.

  - **Analyze and update results one sub-agent at a time**. For each of the agents that you are assigned, you must analyze one agent completely then you MUST update the results file. So that you are incrementally building up the results file. Your to-do list must reflect this:
      - Analyze agent_a for each of the category issues and update the results file.
      - Analyze agent_b for each of the category issues and update the results file.
      - ...

  - **Review the results and refine until all aspects have been analyzed**. Carefully review the results file and the issue definitions once more, continue the analysis, and update the results file. You should review and refine at least twice to ensure comprehensive analysis.

  - **[STANDALONE ONLY] Use the linter**. If you are running in standalone (NOT A DELEGEE), run the lint command with the redirection operator to directly append to the report file without viewing the results. Use the command exactly as provided: `.venv/bin/cxas lint --app-dir=<path> >> /path/to/<agent_name>-review-results-YYYYMMDD-HHMMSS.md`
  
- **[STANDALONE ONLY, OPTIONAL]** If you are running in standalone (NOT A DELEGEE), ask the user if they want to create an HTML version. If so, create a styled HTML version of the results file by using
  `.venv/bin/python3 .agents/skills/cxas-agent-foundry/scripts/md_to_html.py -i <report.md> -o <report.html>`