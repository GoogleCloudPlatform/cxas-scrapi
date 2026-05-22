# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Tests for :mod:`cxas_scrapi.migration.integrity_checks`.

Focus on the sentinel-tool whitelist (``end_session``,
``set_session_variables``) which the deploy server auto-registers and
must not be flagged as unknown — covers all three checks
(``agent.tools``, ``agent.toolsets``, ``{@TOOL: ...}`` in instructions).
"""

from __future__ import annotations

from cxas_scrapi.migration.data_models import (
    IRAgent,
    IRMetadata,
    IRTool,
    MigrationIR,
)
from cxas_scrapi.migration.integrity_checks import (
    check_consolidation_integrity,
)


def _ir(
    *,
    tools: dict[str, str] | None = None,
    agents: dict[str, dict] | None = None,
    parameters: dict[str, dict] | None = None,
) -> MigrationIR:
    return MigrationIR(
        metadata=IRMetadata(app_name="t"),
        tools={
            tid: IRTool(
                id=tid,
                name=f"projects/p/locations/us/apps/X/tools/{tid}",
                type=ttype,
                payload={},
            )
            for tid, ttype in (tools or {}).items()
        },
        agents={
            name: IRAgent(
                type="PLAYBOOK",
                display_name=name,
                instruction=spec.get("instruction", ""),
                tools=spec.get("tools", []),
                toolsets=spec.get("toolsets", []),
            )
            for name, spec in (agents or {}).items()
        },
        parameters=parameters or {},
    )


def test_end_session_in_agent_tools_is_not_flagged():
    """The deploy server auto-attaches ``end_session`` as a sentinel
    tool whose resource path appears in ``agent.tools``. The integrity
    check must NOT treat it as an unknown tool."""
    optimized = _ir(
        agents={
            "RootAgent": {
                "tools": [
                    "projects/p/locations/us/apps/X/tools/end_session",
                ],
            },
        },
    )
    current = _ir()  # empty registry

    blocking, warnings = check_consolidation_integrity(optimized, current)

    assert blocking == []


def test_set_session_variables_sentinel_is_not_flagged():
    optimized = _ir(
        agents={
            "RootAgent": {
                "tools": [
                    "projects/p/locations/us/apps/X/tools/"
                    "set_session_variables",
                ],
            },
        },
    )
    current = _ir()

    blocking, warnings = check_consolidation_integrity(optimized, current)

    assert blocking == []


def test_genuinely_unknown_tool_in_agent_tools_is_flagged():
    """A non-sentinel tool that isn't in ``current_ir.tools`` IS a
    blocking error."""
    optimized = _ir(
        agents={
            "RootAgent": {
                "tools": [
                    "projects/p/locations/us/apps/X/tools/nonexistent_tool",
                ],
            },
        },
    )
    current = _ir()

    blocking, warnings = check_consolidation_integrity(optimized, current)

    assert any("nonexistent_tool" in b for b in blocking)


def test_known_tool_resolves_via_short_id():
    optimized = _ir(
        agents={
            "RootAgent": {
                "tools": [
                    "projects/p/locations/us/apps/X/tools/authenticate_user",
                ],
            },
        },
    )
    current = _ir(tools={"authenticate_user": "PYTHON"})

    blocking, warnings = check_consolidation_integrity(optimized, current)

    assert blocking == []


def test_end_session_in_instruction_is_not_flagged():
    """Block 3 (instruction ``{@TOOL: ...}`` refs) also skips sentinels.
    This was already the behavior pre-fix; covered for regression."""
    optimized = _ir(
        agents={
            "RootAgent": {
                "instruction": "Finally call {@TOOL: end_session}.",
            },
        },
    )
    current = _ir()

    blocking, warnings = check_consolidation_integrity(optimized, current)

    assert blocking == []


def test_unknown_agent_ref_is_flagged():
    optimized = _ir(
        agents={
            "RootAgent": {
                "instruction": "Hand off via {@AGENT: NonExistentGroup}.",
            },
        },
    )
    current = _ir()

    blocking, warnings = check_consolidation_integrity(optimized, current)

    assert any("NonExistentGroup" in b for b in blocking)
