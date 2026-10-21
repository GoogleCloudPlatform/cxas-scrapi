---
title: Skill Evals
---

# Skill Evals

SCRAPI skills are instructions, references, scripts, and fixtures that help an
AI assistant perform a repeatable agent development workflow. When a change
adds or modifies a skill, include skill evals that show the workflow still
works for realistic user requests.

Skill evals are different from CX Agent Studio app evals. App evals test the
behavior of a generated conversational agent. Skill evals test whether the
skill guides an AI assistant through the right development workflow, uses the
right files and scripts, and produces useful artifacts.

## When to add or update skill evals

Add or update skill evals when a PR:

- Adds a new folder under `.agents/skills/`.
- Changes a `SKILL.md` file or its trigger description.
- Changes a skill's bundled scripts, references, assets, or fixtures.
- Changes expected workflow behavior, such as how the foundry builds, runs, or
  debugs an agent.
- Fixes a bug that a skill eval could have caught.

Small typo-only changes usually do not need new eval coverage.

## File layout

Put evals inside the skill directory:

```text
.agents/skills/example-skill/
|-- SKILL.md
|-- evals/
|   |-- evals.json
|   `-- fixtures/
|       `-- ...
|-- references/
`-- scripts/
```

`evals/evals.json` is the entry point. Fixture files should be small,
reviewable examples that let the prompt run without relying on private data or
external state.

## `evals.json` format

Use this structure:

```json
{
  "skill_name": "example-skill",
  "evals": [
    {
      "id": 1,
      "prompt": "User's example task prompt",
      "expected_output": "Description of a successful result",
      "files": ["evals/fixtures/example-input.md"],
      "expectations": [
        "The assistant reads the provided fixture before proposing changes.",
        "The assistant produces the expected artifact in the requested format.",
        "The assistant explains any important limitation instead of guessing."
      ]
    }
  ]
}
```

Fields:

| Field | Description |
|-------|-------------|
| `skill_name` | Skill name from the `SKILL.md` frontmatter. |
| `evals[].id` | Stable unique identifier within this eval file. |
| `evals[].prompt` | A realistic user request that should trigger or use the skill. |
| `evals[].expected_output` | Human-readable description of successful behavior. |
| `evals[].files` | Optional fixture paths, relative to the skill root. |
| `evals[].expectations` | Verifiable statements reviewers or graders can check. |

## Writing good eval prompts

Write prompts the way a contributor or user would actually ask for the
workflow. Good prompts are specific enough to evaluate, but not so scripted
that the assistant can pass by matching exact words.

Prefer prompts that exercise:

- The normal successful path.
- A missing input or setup problem.
- A realistic failure or debugging case.
- A workflow boundary, such as "do not push yet" or "only create evals".

For skill trigger changes, include both examples that should use the skill and
nearby examples that should not.

## Writing good expectations

Expectations should describe observable behavior. A reviewer should be able to
read the assistant output and decide whether each expectation passed.

Good expectations:

- Check that the assistant inspected the relevant fixture or app export.
- Check that required artifacts are created or updated.
- Check that the right script or CLI workflow is used when the skill provides
  one.
- Check that unsafe platform actions are not taken unless the prompt requests
  them.
- Check that the assistant reports uncertainty or missing data instead of
  inventing details.

Avoid expectations that only check for exact phrasing unless exact phrasing is
the behavior under test.

## Examples

### Agent foundry workflow eval

```json
{
  "skill_name": "cxas-agent-foundry",
  "evals": [
    {
      "id": 1,
      "prompt": "Create useful evals for the attached CXAS app export. Cover happy paths, edge cases, and tool behavior.",
      "expected_output": "A focused eval plan and eval artifacts based on the actual app export.",
      "files": [
        "evals/fixtures/support-app/app.yaml",
        "evals/fixtures/support-app/agents/main_agent.yaml",
        "evals/fixtures/support-app/tools/order_status.yaml"
      ],
      "expectations": [
        "Inspects the app fixture before proposing evals.",
        "Creates scenarios that reflect the app's actual intents, tools, and persona.",
        "Includes happy-path and edge-case coverage.",
        "Distinguishes platform golden evals, simulation evals, tool tests, and callback tests where relevant.",
        "Does not push changes to a remote app unless explicitly requested."
      ]
    }
  ]
}
```

### Simulation eval conversion eval

```json
{
  "skill_name": "cxas-sim-eval",
  "evals": [
    {
      "id": 1,
      "prompt": "Convert the provided golden evaluation JSON into SCRAPI simulation eval cases.",
      "expected_output": "Valid simulation eval YAML with high-level user goals and inferred tool expectations.",
      "files": [
        "evals/fixtures/goldens/order_status_golden.json",
        "evals/fixtures/tool_schemas/order_tools.json"
      ],
      "expectations": [
        "Produces valid simulation eval YAML.",
        "Summarizes the user goal instead of copying the full golden transcript verbatim.",
        "Includes expected tool calls when they can be inferred from the golden eval and tool schema.",
        "Preserves important assertions about correctness, tone, and final answer content.",
        "Explains any fixture or schema data needed for a complete conversion."
      ]
    }
  ]
}
```

## Running skill evals

Skill evals are most useful when compared against a baseline:

- **With skill**: run the prompt with the changed skill available.
- **Baseline**: run the same prompt without the skill, or with the previous
  version of the skill when reviewing a regression fix.

Compare the outputs against the `expectations` in `evals.json`. For each
expectation, record whether it passed and cite the output evidence used to make
that decision.

Until SCRAPI provides a shared skill benchmark runner, include the relevant
manual run notes in the PR description. If a skill change also affects Python
code, scripts, or documentation, run the normal repository checks for those
files as well.

## Pull request checklist

Before opening a PR that adds or changes a skill:

- [ ] Add or update `evals/evals.json` for the skill.
- [ ] Include realistic prompts that represent the workflow being changed.
- [ ] Include fixtures for app exports, eval reports, schemas, or source files
      needed by the prompts.
- [ ] Write expectations that can be verified from the assistant output.
- [ ] Include at least one edge case or failure path when the workflow has one.
- [ ] Add should-trigger and should-not-trigger examples when changing a skill
      description.
- [ ] Describe how the skill evals were run or reviewed in the PR.
