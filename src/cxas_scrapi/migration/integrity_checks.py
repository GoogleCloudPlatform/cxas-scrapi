# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Pre-deploy integrity checks for a consolidated :class:`MigrationIR`.

After :class:`StructuralConsolidator` has collapsed N source agents into M
groups and synthesized per-group PIF XML, the new agents may reference
tools, toolsets, sibling agents, or variables that don't exist in the
current IR. Deploying that state to CXAS would cause runtime failures
(unknown tool, dangling agent transfer, etc.).

:func:`check_consolidation_integrity` scans the consolidated IR before
deploy and returns two lists:

* **blocking_errors** — references to things that don't exist; would 100%
  fail at runtime (e.g. ``{@TOOL: foo}`` when ``foo`` isn't deployed,
  ``{@AGENT: X}`` when X isn't a known group).
* **warnings** — best-effort variable-reference flagging; many false
  positives expected from PIF template syntax, so surfaced but not fatal.
"""

from __future__ import annotations

import re

from cxas_scrapi.migration.data_models import MigrationIR
from cxas_scrapi.migration.structural_consolidator import (
    AGENT_REF_RE,
    SENTINEL_REFS,
)

__all__ = [
    "check_consolidation_integrity",
    "PROMPT_VAR_RE",
    "TOOL_REF_RE",
]

# Variable refs in PIF instructions: {var}, `var`, $var. Loose enough to
# avoid false positives but tight enough to catch dangling refs.
PROMPT_VAR_RE = re.compile(
    r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}"
    r"|`([a-zA-Z_][a-zA-Z0-9_]*)`"
    r"|\$([a-zA-Z_][a-zA-Z0-9_]*)"
)

# `{@TOOL: tool_name}` directive refs in PIF instructions.
TOOL_REF_RE = re.compile(r"\{@TOOL:\s*([^}]+)\}")


def _short_id(resource_name: str) -> str:
    return (
        resource_name.rsplit("/", maxsplit=1)[-1]
        if "/" in resource_name
        else resource_name
    )


def check_consolidation_integrity(
    optimized_ir: MigrationIR, current_ir: MigrationIR
) -> tuple[list[str], list[str]]:
    """Validate the consolidated IR before deploy.

    Args:
        optimized_ir: The post-consolidation IR about to be deployed.
        current_ir: The IR that holds the canonical tools / parameters /
            other resources the optimized IR references. In practice this
            is the post-Stage-1 (variable-deduped) IR.

    Returns:
        Tuple ``(blocking_errors, warnings)``.

        * `blocking_errors` — tool / toolset / `{@TOOL:}` / `{@AGENT:}`
          references that point at something not in `current_ir`. Callers
          should refuse to deploy unless the user explicitly overrides.
        * `warnings` — variable refs (`{var}` / `` `var` `` / `$var`) not
          in `current_ir.parameters`. Best-effort signal only.
    """
    blocking: list[str] = []
    warnings_: list[str] = []

    available_tool_resources = {t.name for t in current_ir.tools.values()}
    available_tool_ids = set(current_ir.tools.keys())
    new_group_names = set(optimized_ir.agents.keys())
    available_vars = set(current_ir.parameters.keys())
    sentinel_lower = {s.lower() for s in SENTINEL_REFS} | {"end_session"}
    # Sentinel tool short-IDs that the server auto-registers at deploy
    # time — they're never in current_ir.tools but are valid refs in
    # agent.tools / agent.toolsets / instructions. Kept in sync with
    # MigrationService._inject_system_variables sentinel handling.
    sentinel_tool_ids = {"end_session", "set_session_variables"}

    for group_name, agent in optimized_ir.agents.items():
        # 1. Tool refs (the agent.tools list)
        for tool_ref in agent.tools:
            short = _short_id(tool_ref)
            if short in sentinel_tool_ids:
                continue
            if (
                tool_ref not in available_tool_resources
                and short not in available_tool_ids
            ):
                blocking.append(
                    f"Group {group_name!r} references unknown tool "
                    f"{short!r} (resource: {tool_ref})"
                )

        # 2. Toolset refs
        for ts in agent.toolsets:
            ts_id = ts.get("toolset", "") or ""
            if not ts_id:
                continue
            short = _short_id(ts_id)
            if short in sentinel_tool_ids:
                continue
            if (
                ts_id not in available_tool_resources
                and short not in available_tool_ids
            ):
                blocking.append(
                    f"Group {group_name!r} references unknown toolset {short!r}"
                )

        # 3. {@TOOL: name} refs in the instruction
        instruction = agent.instruction or ""
        for raw_tool_ref in TOOL_REF_RE.findall(instruction):
            tool_name = raw_tool_ref.strip()
            if tool_name in sentinel_tool_ids:
                continue
            if tool_name not in available_tool_ids and not any(
                _short_id(t.name) == tool_name or t.id == tool_name
                for t in current_ir.tools.values()
            ):
                blocking.append(
                    f"Group {group_name!r} instruction has "
                    f"{{@TOOL: {tool_name}}} but no such tool exists"
                )

        # 4. {@AGENT: X} refs must point at a valid group OR sentinel.
        for raw in AGENT_REF_RE.findall(instruction):
            ref = raw.strip()
            if ref.lower() in sentinel_lower:
                continue
            if ref not in new_group_names:
                blocking.append(
                    f"Group {group_name!r} instruction has "
                    f"{{@AGENT: {ref}}} but no such group exists"
                )

        # 5. Variable refs — best-effort warning only.
        unknown_vars: set[str] = set()
        for match in PROMPT_VAR_RE.findall(instruction):
            v = next((g for g in match if g), None)
            if v and v not in available_vars and not v.startswith("@"):
                unknown_vars.add(v)
        if unknown_vars:
            sample = sorted(unknown_vars)[:5]
            extra = (
                f" (+{len(unknown_vars) - 5} more)"
                if len(unknown_vars) > 5
                else ""
            )
            warnings_.append(
                f"Group {group_name!r}: {len(unknown_vars)} variable refs "
                f"not in params dict: {', '.join(sample)}{extra}"
            )

    return blocking, warnings_
