# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Run the canonical-schema instruction lint rules against in-memory text.

Used by ``StructuralConsolidator.synthesize_instructions`` to validate
Gemini's output before it ships to deploy. Reuses the registered rules
in ``cxas_scrapi.utils.lint_rules.instructions`` rather than maintaining
a parallel schema checker.

Only the schema/syntax rules that operate on standalone instruction
text are run — rules that need cross-file context (agent registry,
toolset config) are skipped because the synthesis call site has no
access to a project root on disk.
"""

from __future__ import annotations

from pathlib import Path

# Importing the module triggers the @rule decorators that register
# I001-I015 into the global rule registry.
import cxas_scrapi.utils.lint_rules.instructions  # noqa: F401
from cxas_scrapi.utils.linter import (
    _RULE_REGISTRY,
    LintContext,
    LintResult,
    Severity,
)

# Curated to schema/syntax rules that work without filesystem context:
#   I001 — required <role>/<persona>/<taskflow> tags
#   I002 — <taskflow> needs <subtask> or <step> children
#   I010 — wrong agent-reference syntax (e.g. ${AGENT:...})
#   I011 — wrong tool-reference syntax
#   I015 — banned legacy CamelCase / state-machine tags
_SYNTHESIS_GATING_RULES = frozenset({"I001", "I002", "I010", "I011", "I015"})


def lint_instruction_text(text: str, agent_name: str) -> list[LintResult]:
    """Return ERROR-severity diagnostics for a synthesized instruction.

    Builds a minimal ``LintContext`` rooted at ``/`` and synthesizes a
    pseudo file path ``/<agent_name>.txt`` so the registered rules can
    use their normal ``file_path.relative_to(project_root)`` plumbing.
    """
    root = Path("/")
    context = LintContext(project_root=root, app_dir=root, evals_dir=root)
    file_path = root / f"{agent_name}.txt"

    results: list[LintResult] = []
    for rule_obj in _RULE_REGISTRY.get("instructions", []):
        if rule_obj.id not in _SYNTHESIS_GATING_RULES:
            continue
        for r in rule_obj.check(file_path, text, context):
            if r.severity == Severity.ERROR:
                results.append(r)
    return results
