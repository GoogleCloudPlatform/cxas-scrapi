---
title: Custom Rules
description: Write lint rules for your own team, and run them with the built-in rules in cxas lint.
---

# Custom Rules

`cxas lint` can run lint rules that you write yourself, next to the built-in rules. You write them in Python, in the same form as the built-in rules, and install them as a package that declares an entry point in the `cxas_scrapi.lint_rules` group.

Custom rules behave like built-in rules. `cxaslint.yaml` can change their severity, pass them options and override them per file. `--rule`, `--only` and `--list-rules` include them, and `--list-rules` marks each custom rule with `[custom: <source>]`.

!!! note
    Custom rule support is experimental. The `Rule`, `LintContext` and `LintResult` interfaces may change in a minor release.

---

## Write a rule

Subclass `Rule` and register the class with the `@rule` decorator. `check()` receives one file and returns a list of results:

```python
from pathlib import Path

from cxas_scrapi.utils.linter import (
    LintContext,
    LintResult,
    Rule,
    Severity,
    rule,
)


@rule("instructions")
class NoTodo(Rule):
    id = "TEAM001"
    name = "no-todo"
    description = "Instruction still contains TODO"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if "TODO" not in content:
            return []
        return [
            self.make_result(
                file=str(file_path.relative_to(context.project_root)),
                message="Instruction still contains TODO",
                fix="Finish the instruction, or remove the TODO",
            )
        ]
```

- **Category:** the argument to `@rule` decides which files the rule receives, as it does for the built-in rules. Use one of `instructions`, `callbacks`, `tools`, `evals`, `config`, `structure` or `schema`.
- **Target:** a `structure` or `schema` rule also needs a `target`, which picks the files it receives: `app_config`, `instruction`, `agent_config`, `tool_config`, `toolset_config`, `guardrail_config`, `evaluation_config` or `eval_expectation_config`. Rules in the other categories need no target.
- **ID:** pick a prefix that the built-in rules do not use, such as `TEAM001`. The linter refuses a rule with no ID, an ID that a built-in rule or another source already uses, and an ID defined twice in one category. Like some built-in rules, one rule ID may appear in several categories.
- **Options:** read the rule's options from `context.options.get(self.id, {})`. Users set them under `options` in `cxaslint.yaml`.

A package of rules must import each of its rule modules, for example from its `__init__.py`. Importing a module runs its `@rule` decorators, which register the rules.

---

## Install rules as a package

Declare the module that holds your rules as an entry point in your package's `pyproject.toml`:

```toml
[project.entry-points."cxas_scrapi.lint_rules"]
team = "team_lint_rules"
```

Install the package in the environment that runs `cxas`. The linter then loads its rules in every project. Errors and `--list-rules` name the rules' source by the entry point's name: `team` here.

---

## When custom rules fail to load

`cxas lint` exits with code 1, and runs no rules, when a source of custom rules:

- fails to import,
- defines a rule with no ID, an ID that is already registered, or an ID twice in one category, or
- puts a rule in a category, or gives it a target, that no built-in rule uses.

With `--json`, the linter prints one result with severity `error` and rule ID `SETUP`, so a script that counts errors still stops.
