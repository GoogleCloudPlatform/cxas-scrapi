import os
import time


def request_skill_optimization(current_skill, failures):
    failure_log = ""
    for f in failures:
        failure_log += f"- Case {f['case']}: {f['rationale']}\n"

    prompt = f"""You are an expert AI Prompt Optimizer. Your task is to refine an agent's skill guidelines (`SKILL.md`) to fix a set of failed evaluation cases.

Current SKILL.md:
{current_skill}

Failed cases and rationales:
{failure_log}

Analyze why the agent failed to satisfy expectations, and rewrite `SKILL.md` adding highly specific, concrete rules to prevent these failures in the future. Preserve all unrelated rules.
Specifically, enforce these critical rules:
1. The `tool_call` (such as `end_session`) and `webhook_call` fields MUST be written at the root level of individual turn objects in the YAML transcript, and MUST NOT be nested under `enrichment` or any other parent key.

Output the complete, optimized SKILL.md content directly in your response. Do not include any markdown wrappers or conversational explanations, just the exact new optimized SKILL.md content."""

    prompt_path = "/tmp/optimizer_prompt.txt"
    response_path = "/tmp/optimizer_response.txt"

    if os.path.exists(response_path):
        os.remove(response_path)

    with open(prompt_path, "w") as f:
        f.write(prompt)

    instructions = [
        f"Read the prompt from: `{prompt_path}`",
        "Spawn a specialized 'Optimizer' subagent using `invoke_subagent`.",
        (
            "Instruct the subagent to write its final, complete SKILL.md output"
            f" exactly to: `{response_path}`"
        ),
    ]

    formatted_instructions = "\n".join(
        f"{i + 1}. {instr}" for i, instr in enumerate(instructions)
    )

    print(
        "\n=======================\n"
        "🤖 AGENT ACTION REQUIRED: SPAWN OPTIMIZER SUBAGENT\n"
        "=======================\n"
        f"{formatted_instructions}\n"
    )
