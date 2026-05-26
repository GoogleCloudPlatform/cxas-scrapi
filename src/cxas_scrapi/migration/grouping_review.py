# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Interactive review TUI for Gemini-proposed agent groupings.

Used by:

* :meth:`MigrationService.run_stage1` — passed as the ``grouping_callback``
  argument; lets a human review / re-propose / merge / split / rename the
  Gemini-proposed grouping before the consolidator commits.
* The skill scripts (via re-export from ``_grouping.py``) which forward
  the same callback into ``run_stage1``.

The TUI returns *just the accepted groupings dict* (or ``None`` to
abort). The caller — `run_stage1` — runs the actual
:meth:`StructuralConsolidator.consolidate` afterwards. Keeping the
consolidate step out of the TUI avoids double-consolidating and keeps
this module a pure UX layer.

Inside the loop we still call ``consolidator.consolidate(groupings)``
once per iteration to *preview* what the accepted state would look like.
That preview is rendered side-by-side with the original 1:1 IR so the
user can see the impact of their edits.
"""

from __future__ import annotations

import logging
from typing import Any

from InquirerPy import inquirer
from InquirerPy.base.control import Choice
from rich.console import Console
from rich.tree import Tree

from cxas_scrapi.migration.data_models import MigrationIR
from cxas_scrapi.migration.structural_consolidator import (
    GROUP_NAME_RE,
    StructuralConsolidator,
    root_group_name,
)

logger = logging.getLogger(__name__)

__all__ = [
    "interactive_review",
    "render_diff",
    "render_ir_tree",
]


# ---------------------------------------------------------------------------
# Rich tree rendering
# ---------------------------------------------------------------------------


def _short_id(resource_name: str) -> str:
    return (
        resource_name.rsplit("/", maxsplit=1)[-1]
        if "/" in resource_name
        else resource_name
    )


def render_ir_tree(
    ir: MigrationIR, title: str, root_key: str | None = None
) -> Tree:
    """Build a Rich Tree of every agent + its tools/toolsets/callbacks."""
    root_label = (
        f"[bold]{title}[/]  ({len(ir.agents)} agents, {len(ir.tools)} tools)"
    )
    tree = Tree(root_label)

    keys = sorted(ir.agents.keys())
    if root_key and root_key in ir.agents:
        keys = [root_key] + [k for k in keys if k != root_key]

    for key in keys:
        agent = ir.agents[key]
        marker = " [yellow](root)[/]" if key == root_key else ""
        agent_node = tree.add(
            f"[cyan]{key}[/] [{agent.type}] '{agent.display_name}'{marker}"
        )
        if agent.description:
            agent_node.add(f"[dim]{agent.description}[/]")
        if agent.tools:
            tools_node = agent_node.add(f"tools ({len(agent.tools)})")
            for t in agent.tools:
                tools_node.add(_short_id(t))
        if agent.toolsets:
            ts_node = agent_node.add(f"toolsets ({len(agent.toolsets)})")
            for ts in agent.toolsets:
                ts_node.add(_short_id(ts.get("toolset", "?")))
        if agent.callbacks:
            cb_present = [k for k, v in agent.callbacks.items() if v]
            if cb_present:
                agent_node.add(f"callbacks: {', '.join(cb_present)}")
        agent_node.add(
            f"[dim]instruction: {len(agent.instruction or '')} bytes[/]"
        )

    if ir.routing_edges:
        edges_node = tree.add(
            f"[bold]routing edges[/] ({len(ir.routing_edges)})"
        )
        for edge in ir.routing_edges[:20]:
            edges_node.add(str(edge))
        if len(ir.routing_edges) > 20:
            edges_node.add(f"… and {len(ir.routing_edges) - 20} more")

    return tree


def render_diff(
    before: MigrationIR,
    after: MigrationIR,
    root_key: str | None,
    root_group: str | None,
    console: Console,
) -> None:
    """Print before / after Rich trees + a summary stats line."""
    console.print()
    console.print(render_ir_tree(before, "Original 1:1 IR", root_key))
    console.print()
    console.print(render_ir_tree(after, "Proposed Optimized IR", root_group))

    tools_before = len(before.tools)
    tools_after = len(after.tools)
    cb_before = sum(1 for a in before.agents.values() if a.callbacks)
    cb_after = sum(1 for a in after.agents.values() if a.callbacks)
    console.print(
        f"\n[bold]Summary:[/] agents {len(before.agents)} → "
        f"{len(after.agents)}, tools preserved {tools_after}/{tools_before}, "
        f"agents with callbacks {cb_before} → {cb_after}"
    )


# ---------------------------------------------------------------------------
# Grouping mutators
# ---------------------------------------------------------------------------


async def _merge_groups(groupings: dict, console: Console) -> dict:
    names = list(groupings.keys())
    choices = [
        Choice(
            value=i,
            name=f"{n} ({len(groupings[n].get('agents', []))} agents)",
        )
        for i, n in enumerate(names)
    ]
    selected = await inquirer.checkbox(
        message=(
            "Pick groups to merge (Space to toggle, Enter to confirm; need ≥2):"
        ),
        choices=choices,
        validate=lambda r: len(r) >= 2 or "Pick at least 2 groups",
    ).execute_async()
    targets = [names[i] for i in selected]
    new_name = await inquirer.text(
        message="New group name:",
        default=targets[0],
        validate=lambda v: bool(GROUP_NAME_RE.match(v)) or "Invalid name",
    ).execute_async()

    merged_agents: list[str] = []
    rationales: list[str] = []
    journeys: list[str] = []
    is_root = False
    for t in targets:
        merged_agents.extend(groupings[t].get("agents", []))
        if groupings[t].get("rationale"):
            rationales.append(groupings[t]["rationale"])
        if groupings[t].get("journey"):
            journeys.append(groupings[t]["journey"])
        is_root = is_root or bool(groupings[t].get("is_root"))
    new_groupings = {k: v for k, v in groupings.items() if k not in targets}
    new_groupings[new_name] = {
        "agents": merged_agents,
        "rationale": " | ".join(rationales),
        "journey": " | ".join(journeys),
        "is_root": is_root,
    }
    return new_groupings


async def _split_group(
    ir: MigrationIR, groupings: dict, console: Console
) -> dict:
    names = list(groupings.keys())
    target = await inquirer.select(
        message="Group to split:",
        choices=[
            Choice(
                value=n,
                name=f"{n} ({len(groupings[n].get('agents', []))} agents)",
            )
            for n in names
        ],
    ).execute_async()
    members = groupings[target].get("agents", [])
    if len(members) < 2:
        console.print(
            "[yellow]Group has fewer than 2 members; cannot split.[/]"
        )
        return groupings
    moving_choices = [
        Choice(value=m, name=f"{m} ('{ir.agents[m].display_name}')")
        for m in members
    ]
    moving = await inquirer.checkbox(
        message="Members to MOVE to a new group:",
        choices=moving_choices,
        validate=lambda r: 0 < len(r) < len(members) or "Pick a strict subset",
    ).execute_async()
    new_name = await inquirer.text(
        message="Name for new group:",
        validate=lambda v: (
            (bool(GROUP_NAME_RE.match(v)) and v not in groupings)
            or "Invalid or duplicate name"
        ),
    ).execute_async()

    new_groupings = {k: dict(v) for k, v in groupings.items()}
    new_groupings[target]["agents"] = [m for m in members if m not in moving]
    new_groupings[new_name] = {
        "agents": list(moving),
        "rationale": f"Split from {target}",
        "journey": "",
        "is_root": False,
    }
    return new_groupings


async def _rename_group(groupings: dict, console: Console) -> dict:
    names = list(groupings.keys())
    old = await inquirer.select(
        message="Group to rename:",
        choices=names,
    ).execute_async()
    new_name = await inquirer.text(
        message="New name:",
        validate=lambda v: (
            (bool(GROUP_NAME_RE.match(v)) and v not in groupings)
            or "Invalid or duplicate name"
        ),
    ).execute_async()
    return {(new_name if k == old else k): v for k, v in groupings.items()}


# ---------------------------------------------------------------------------
# Interactive review loop
# ---------------------------------------------------------------------------


async def interactive_review(
    ir: MigrationIR,
    groupings: dict,
    consolidator: StructuralConsolidator,
    root_key: str | None = None,
    dep_summary: dict[str, Any] | None = None,
    console: Console | None = None,
) -> dict | None:
    """``[a]ccept / [r]e-propose / [m]erge / [s]plit / [n]ame / [q]uit``
    InquirerPy loop with a side-by-side before/after Rich tree preview.

    Args:
        ir: The current (post-Stage-1 variable-dedup) IR — shown as the
            "before" side of the diff.
        groupings: The Gemini-proposed grouping dict to review.
        consolidator: Used for two things only — *preview* the
            consolidation each iteration (``consolidator.consolidate``)
            and *re-propose* with feedback when the user picks
            ``[r]e-propose`` (``consolidator.propose_groupings``). The
            TUI does not commit the consolidation itself.
        root_key: The root-agent IR key (used to highlight the root in
            the rendered trees).
        dep_summary: Dependency-graph summary passed back into
            ``propose_groupings`` on re-propose.
        console: Rich console for printing. Defaults to a fresh
            ``Console()``.

    Returns:
        The accepted ``groupings`` dict, or ``None`` if the user quit.
        The caller (typically ``MigrationService.run_stage1``) is
        responsible for committing the consolidation.
    """
    console = console or Console()
    while True:
        # Preview only — the caller will re-run consolidate after accept.
        try:
            preview_ir = consolidator.consolidate(groupings)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Consolidation preview failed: {exc}[/]")
            return None
        render_diff(
            ir,
            preview_ir,
            root_key,
            root_group_name(groupings, root_key),
            console,
        )

        action = await inquirer.select(
            message="Action:",
            choices=[
                Choice(value="accept", name="[a]ccept"),
                Choice(value="repropose", name="[r]e-propose with feedback"),
                Choice(value="merge", name="[m]erge groups"),
                Choice(value="split", name="[s]plit a group"),
                Choice(value="rename", name="re[n]ame a group"),
                Choice(value="quit", name="[q]uit"),
            ],
            default="accept",
        ).execute_async()

        if action == "accept":
            return groupings
        if action == "quit":
            return None
        if action == "repropose":
            feedback = await inquirer.text(
                message="Feedback for re-proposal:",
            ).execute_async()
            try:
                groupings = await consolidator.propose_groupings(
                    root_key, dep_summary, feedback
                )
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]Re-proposal failed: {exc}[/]")
        elif action == "merge":
            groupings = await _merge_groups(groupings, console)
        elif action == "split":
            groupings = await _split_group(ir, groupings, console)
        elif action == "rename":
            groupings = await _rename_group(groupings, console)
