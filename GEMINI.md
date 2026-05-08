# Gemini CLI Mandates

## Sub-Agent Delegation & Reasoning
1. **Ban on Generalist Agent:** You MUST NEVER delegate ANY task to the `generalist` sub-agent. The `generalist` agent is explicitly forbidden in this workspace due to its tendency to take shortcuts and produce low-quality work.
2. **Mandatory Plan Mode:** For complex tasks, cross-cutting architectural changes, or generating multiple files from a TDD, you MUST use the `enter_plan_mode` tool. Plan Mode is for architectural decisions and strategy; however, execution should leverage specialized sub-agents where appropriate.
3. **Authorized Specialized Sub-Agents:** You are explicitly authorized and encouraged to use specialized sub-agents provided by the `cxas-agent-foundry` skill (such as `lint-fixer`, `eval-writer`, `coverage-analyst`, `tdd-writer`, and `triage-failure`) for their exact designated purposes to save main-thread context and execute repetitive tasks efficiently.

## Code Generation Quality
When translating specifications, requirements, or a TDD into code (especially `instruction.txt` files, Python scripts, or callbacks):
1. **No Scripted Generation:** You MUST NOT write or execute Python, bash, or other scripts (e.g., `fix_linter.py`) to auto-generate, bulk-edit, or "stub out" files.
2. **Manual File Writing & Delegation:** While scripting is banned, delegating repetitive manual file generation (like fixing numerous lint errors or writing multiple eval YAMLs) to the approved specialized sub-agents (e.g., `lint-fixer`, `eval-writer`) is the preferred method for bulk operations. If not delegating to a specialized sub-agent, you MUST use the built-in `write_file` or `replace` tools to manually author the complete content of every file across multiple turns. Every file must contain its complete persona, hard rules, and exhaustive taskflow exactly as detailed in the specifications.

## Linter Enforcement
1. **Zero Warnings Policy:** The GECX linter now treats warnings as fatal errors. You MUST achieve 0 errors AND 0 warnings. Missing tool docstrings, unreferenced tools, and schema issues are considered blockers. You must not proceed to deployment until the linter passes completely.