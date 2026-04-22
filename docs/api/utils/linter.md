---
title: Linter
---

# Linter

The `cxas_scrapi.utils.linter` module is a rule-based lint engine for validating CXAS agent repositories against best practices and structural requirements. It's inspired by tools like Ruff and pylint: rules are first-class objects with IDs and configurable severity, auto-registered via a `@rule` decorator, and run against your app's local file tree.

The lint engine is also what powers the `cxas lint` CLI command — so everything here is available to you programmatically if you want to build custom tooling or integrate linting into a CI step.

## Key Components

| Component | What it does |
|---|---|
| `Severity` | Enum: `ERROR`, `WARNING`, `INFO`, `OFF`. Controls whether a rule failure blocks CI. |
| `LintResult` | Dataclass: holds the file path, rule ID, severity, message, line number, and optional fix suggestion. |
| `LintContext` | Dataclass: shared context passed to every rule — agent names, tool names, directories, and config options. |
| `Rule` | Abstract base class for all lint rules. Subclass it and implement `check()`. |
| `@rule(category)` | Decorator that auto-registers a `Rule` subclass into the global registry. |
| `run_rules()` | The main runner: discovers files, dispatches rules by category, and collects results into a `LintReport`. |
| `LintConfig` | Loaded from `cxaslint.yaml` — controls which rules are active and at what severity. |

## Quick Example

```python
from pathlib import Path
from cxas_scrapi.utils.linter import (
    build_registry,
    build_context,
    run_rules,
    LintConfig,
    LintReport,
    Discovery,
)

project_root = Path(".")
config = LintConfig.load(project_root)
discovery = Discovery(
    app_dir=project_root / config.app_dir,
    evals_dir=project_root / config.evals_dir,
)
registry = build_registry()
context = build_context(project_root, config, discovery)

report = LintReport()
run_rules(registry, config, context, discovery, report)
report.print_summary(show_fixes=True)

# Exit with code 1 if there are errors (great for CI)
report.print_and_exit()
```

Writing a custom rule:

```python
from cxas_scrapi.utils.linter import Rule, LintResult, LintContext, rule
from pathlib import Path

@rule("instructions")
class NoPlaceholderInstructions(Rule):
    id = "C001"
    name = "No placeholder instructions"
    description = "Instruction files should not contain TODO placeholders."
    default_severity = Severity.WARNING

    def check(self, file_path: Path, content: str, context: LintContext) -> list[LintResult]:
        results = []
        for i, line in enumerate(content.splitlines(), start=1):
            if "TODO" in line:
                results.append(self.make_result(str(file_path), "Found TODO placeholder", line=i))
        return results
```

## Reference

::: cxas_scrapi.utils.linter.Severity

::: cxas_scrapi.utils.linter.LintResult

::: cxas_scrapi.utils.linter.LintContext

::: cxas_scrapi.utils.linter.Rule

::: cxas_scrapi.utils.linter.LintReport

::: cxas_scrapi.utils.linter.LintConfig

::: cxas_scrapi.utils.linter.Discovery

::: cxas_scrapi.utils.linter.build_registry

::: cxas_scrapi.utils.linter.build_context

::: cxas_scrapi.utils.linter.run_rules

::: cxas_scrapi.utils.linter.rule
