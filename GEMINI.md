# Gemini CLI Mandates

## Sub-Agent Delegation & Reasoning
1. **Ban on Generalist Agent:** You MUST NEVER delegate ANY task to the `generalist` sub-agent. The `generalist` agent is explicitly forbidden in this workspace due to its tendency to take shortcuts and produce low-quality work.
2. **Mandatory Plan Mode:** For complex tasks, cross-cutting architectural changes, or generating multiple files from a TDD, you MUST use the `enter_plan_mode` tool. Do not attempt to use sub-agents to bypass complexity. Use your highest reasoning capabilities directly.

## Code Generation Quality
When translating specifications, requirements, or a TDD into code (especially `instruction.txt` files, Python scripts, or callbacks):
1. **No Scripted Generation:** You MUST NOT write or execute Python, bash, or other scripts (e.g., `fix_linter.py`) to auto-generate, bulk-edit, or "stub out" files.
2. **Manual File Writing:** You MUST use the built-in `write_file` or `replace` tools to manually author the complete content of every file. If there are many files, you must process them across multiple turns rather than resorting to scripting shortcuts. Every file must contain its complete persona, hard rules, and exhaustive taskflow exactly as detailed in the specifications.

## Linter Enforcement
1. **Zero Warnings Policy:** The GECX linter now treats warnings as fatal errors. You MUST achieve 0 errors AND 0 warnings. Missing tool docstrings, unreferenced tools, and schema issues are considered blockers. You must not proceed to deployment until the linter passes completely.