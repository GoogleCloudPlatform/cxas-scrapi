# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Parent-child topology wiring for consolidated CXAS agents.

After :class:`StructuralConsolidator` collapses N source agents into M
groups, the parent-child topology of the deployed CXAS app is only as
connected as whatever the synthesized PIF XML happened to reference. In
practice the LLM often misses sibling routes, leaving siblings unreachable.

CXAS topology rule: a transfer from sub-agent A to sub-agent B does NOT
require A to have B as a child — the conversation goes A → root → B. So
the safe default is **hub-and-spoke**: the root group has every other
group as a direct child, and non-root groups have NO children. This
trivially avoids the A↔B cycles you get when you naively mirror a
bidirectional source dep graph onto CXAS child-agent links.

Two modes are exposed:

* :func:`compute_group_children_hub_and_spoke` — root.children = every
  non-root group; non-root.children = []. Always cycle-free.
* :func:`compute_group_children_preserve_hierarchy` — derives children
  from the source DFCX dep graph with a BFS-based cycle breaker. The root
  is forced to never appear as anyone's child.

:func:`apply_topology` pushes the computed children to CXAS via
``Agents.update_agent``. :func:`set_app_root_agent` resets the app's
``root_agent`` to whichever group is marked ``is_root`` in the grouping.

:func:`delete_orphan_agents` cleans up the original 1:1 agents that the
consolidation didn't replace in place — multi-pass deletion that respects
CXAS's rule that an agent still listed as a child cannot be deleted.

All functions are idempotent and safe to re-run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cxas_scrapi.core.agents import Agents
from cxas_scrapi.core.apps import Apps
from cxas_scrapi.migration.dfcx_dep_analyzer import DependencyAnalyzer
from cxas_scrapi.migration.structural_consolidator import (
    member_to_group_map,
    root_group_name,
)

if TYPE_CHECKING:
    from cxas_scrapi.migration.ir_bundle import IRBundle

__all__ = [
    "apply_topology",
    "compute_group_children",
    "compute_group_children_hub_and_spoke",
    "compute_group_children_preserve_hierarchy",
    "delete_orphan_agents",
    "set_app_root_agent",
]


def _short(resource_name: str) -> str:
    return (
        resource_name.rsplit("/", maxsplit=1)[-1]
        if "/" in resource_name
        else resource_name
    )


# ---------------------------------------------------------------------------
# Topology computation
# ---------------------------------------------------------------------------


def compute_group_children_hub_and_spoke(
    bundle: "IRBundle",
) -> dict[str, set[str]]:
    """Hub-and-spoke topology: root has every non-root group as a direct
    child; non-root groups have no children. Peer transfers route via root.

    Always cycle-free since the only edges are root → leaf.
    """
    if not bundle.grouping:
        raise RuntimeError(
            "Bundle has no `grouping` field — was Stage 1 consolidation run?"
        )

    root = root_group_name(bundle.grouping, root_key=None)
    if not root:
        raise RuntimeError(
            "Could not determine the root group from the grouping JSON. "
            "Set `is_root: true` on exactly one group."
        )

    children: dict[str, set[str]] = {g: set() for g in bundle.grouping}
    for g in bundle.grouping:
        if g != root:
            children[root].add(g)
    return children


def _build_raw_source_edges(
    bundle: "IRBundle",
) -> dict[str, set[str]]:
    """Walk the source DFCX dep graph and project every cross-group edge
    onto the consolidated groups. Returns parent_group → set(child_group).
    """
    pre_ir = bundle.pre_consolidation_ir or bundle.ir
    m2g_keys = member_to_group_map(bundle.grouping)
    member_to_group: dict[str, str] = {}
    for ir_key, group in m2g_keys.items():
        member_to_group[ir_key] = group
        agent = pre_ir.agents.get(ir_key)
        if agent and agent.display_name:
            member_to_group[agent.display_name] = group

    analyzer = DependencyAnalyzer(bundle.source_agent_data)
    display_to_resource = {v: k for k, v in analyzer.name_map.items()}

    edges: dict[str, set[str]] = {g: set() for g in bundle.grouping}
    for group_name, payload in bundle.grouping.items():
        for member_key in payload.get("agents", []) or []:
            member_resource = display_to_resource.get(member_key)
            if not member_resource:
                agent = pre_ir.agents.get(member_key)
                if agent:
                    member_resource = display_to_resource.get(
                        agent.display_name
                    )
            if not member_resource:
                continue

            for target_resource in analyzer.graph.get(member_resource, set()):
                target_display = analyzer.name_map.get(target_resource)
                if not target_display:
                    continue
                target_group = member_to_group.get(target_display)
                if not target_group or target_group == group_name:
                    continue
                edges[group_name].add(target_group)

    return edges


def _has_path(edges: dict[str, set[str]], src: str, dst: str) -> bool:
    """BFS reachability: would adding src → dst create a cycle?

    Equivalent to asking "can dst already reach src?".
    """
    if src == dst:
        return True
    visited = {dst}
    queue = [dst]
    while queue:
        node = queue.pop(0)
        for neighbor in edges.get(node, set()):
            if neighbor == src:
                return True
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return False


def compute_group_children_preserve_hierarchy(
    bundle: "IRBundle",
) -> dict[str, set[str]]:
    """Derive children from the source DFCX dep graph, breaking cycles.

    Edges are processed in priority order (root-out edges first, then by
    source group fanout) and any edge that would close a cycle in the
    accepted set is skipped. The accepted set is therefore a DAG.

    The root agent is forced to never appear as a child of any agent.
    """
    if not bundle.grouping:
        raise RuntimeError(
            "Bundle has no `grouping` field — was Stage 1 consolidation run?"
        )

    root = root_group_name(bundle.grouping, root_key=None)
    raw_edges = _build_raw_source_edges(bundle)

    fanout = {g: len(targets) for g, targets in raw_edges.items()}
    candidates: list[tuple[str, str]] = []
    for src, targets in raw_edges.items():
        for tgt in targets:
            if tgt == root:
                continue
            candidates.append((src, tgt))

    def _priority(edge: tuple[str, str]) -> tuple[int, int, str]:
        src, tgt = edge
        return (0 if src == root else 1, -fanout.get(src, 0), src + tgt)

    candidates.sort(key=_priority)

    accepted: dict[str, set[str]] = {g: set() for g in bundle.grouping}
    for src, tgt in candidates:
        if tgt in accepted[src]:
            continue
        if _has_path(accepted, tgt, src):
            continue
        accepted[src].add(tgt)
    return accepted


def compute_group_children(
    bundle: "IRBundle", mode: str = "hub"
) -> dict[str, set[str]]:
    """Dispatch on mode. ``"hub"`` (default) → hub-and-spoke;
    ``"hierarchy"`` → preserve-hierarchy with cycle breaker."""
    if mode == "hierarchy":
        return compute_group_children_preserve_hierarchy(bundle)
    return compute_group_children_hub_and_spoke(bundle)


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def apply_topology(
    bundle: "IRBundle",
    children: dict[str, set[str]],
    *,
    dry_run: bool = False,
    progress=None,
) -> tuple[int, int, int]:
    """Push child_agents to CXAS via update_agent.

    Args:
        bundle: The IR bundle (must have deployed resource names).
        children: parent_group → set(child_group), e.g. from
            :func:`compute_group_children`.
        dry_run: If true, computes the mapping but doesn't call CXAS.
        progress: Optional callable ``(event, payload)`` for per-update
            progress reporting. Events: ``"start"``, ``"updated"``,
            ``"failed"``, ``"missing_groups"``. The skill UI wires this
            to Rich; pure callers can pass ``None``.

    Returns:
        Tuple ``(updated, skipped, failed)``.
    """
    app_resource = bundle.ir.metadata.app_resource_name
    if not app_resource:
        raise RuntimeError("Bundle has no app_resource_name; cannot push.")

    group_resources = {
        name: agent.resource_name
        for name, agent in bundle.ir.agents.items()
        if agent.resource_name
    }
    missing = set(children.keys()) - set(group_resources.keys())
    if missing and progress is not None:
        progress("missing_groups", sorted(missing))

    if progress is not None:
        progress("start", children)

    if dry_run:
        return 0, 0, 0

    agents_client = Agents(app_name=app_resource)
    updated = 0
    skipped = 0
    failed = 0

    for parent, child_groups in children.items():
        parent_resource = group_resources.get(parent)
        if not parent_resource:
            skipped += 1
            continue
        child_resources = [
            group_resources[c] for c in child_groups if c in group_resources
        ]
        try:
            agents_client.update_agent(
                agent_name=parent_resource,
                child_agents=child_resources,
            )
            updated += 1
            if progress is not None:
                progress(
                    "updated",
                    {"parent": parent, "child_count": len(child_resources)},
                )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            if progress is not None:
                progress(
                    "failed",
                    {
                        "parent": parent,
                        "error": str(exc).splitlines()[0][:120],
                    },
                )

    return updated, skipped, failed


def set_app_root_agent(bundle: "IRBundle") -> tuple[bool, str]:
    """Set the app's root_agent to whichever group is marked ``is_root``.

    Returns ``(ok, message)``. ``ok=False`` covers both "no grouping /
    no root" (treated as a no-op success in the False sense) and a real
    CXAS error — inspect ``message`` to disambiguate.
    """
    if not bundle.grouping:
        return False, "No grouping on bundle; nothing to set."
    rg = root_group_name(bundle.grouping, root_key=None)
    if not rg:
        return False, "No is_root group in grouping; nothing to set."
    agent = bundle.ir.agents.get(rg)
    if not agent or not agent.resource_name:
        return False, f"Root group {rg!r} has no resource_name."

    try:
        apps_client = Apps(
            project_id=bundle.config.project_id,
            location=bundle.resolve_location(),
        )
        apps_client.update_app(
            bundle.ir.metadata.app_resource_name,
            root_agent=agent.resource_name,
        )
        short = _short(agent.resource_name)
        return True, f"Set app start agent → {rg} ({short})"
    except Exception as exc:  # noqa: BLE001
        return False, f"Failed to set app root agent: {exc}"


# ---------------------------------------------------------------------------
# Orphan cleanup
# ---------------------------------------------------------------------------


def delete_orphan_agents(
    app_resource_name: str,
    keep_resources: set[str],
    *,
    max_passes: int = 5,
    progress=None,
) -> tuple[int, int]:
    """Delete every CXAS agent under ``app_resource_name`` whose resource
    name is not in ``keep_resources``.

    CXAS rejects deleting an agent still listed as a child of another
    agent. We do up to ``max_passes`` iterations: each pass deletes
    whatever is currently a leaf (no longer referenced as a child by any
    remaining orphan), exposing new leaves on the next pass. Stops early
    once a pass makes no progress.

    Args:
        app_resource_name: The deployed app's resource name.
        keep_resources: Resource names that must NOT be deleted (i.e.
            the consolidated agents).
        max_passes: Maximum delete-pass iterations before giving up.
        progress: Optional callable ``(event, payload)`` for reporting.
            Events: ``"init_failed"``, ``"empty"``, ``"pass_start"``,
            ``"deleted"``, ``"no_progress"``.

    Returns:
        Tuple ``(total_deleted, remaining_undeletable)``.
    """
    try:
        agents_client = Agents(app_name=app_resource_name)
    except Exception as exc:  # noqa: BLE001
        if progress is not None:
            progress("init_failed", str(exc))
        return 0, 0

    total_deleted = 0
    pass_num = 0
    while pass_num < max_passes:
        pass_num += 1
        try:
            live_agents = agents_client.list_agents()
        except Exception as exc:  # noqa: BLE001
            if progress is not None:
                progress("list_failed", str(exc))
            break

        orphans = [a for a in live_agents if a.name not in keep_resources]
        if not orphans:
            if pass_num == 1 and progress is not None:
                progress("empty", None)
            break

        if pass_num == 1 and progress is not None:
            progress("pass_start", {"pass": pass_num, "count": len(orphans)})

        deleted_this_pass = 0
        failed_this_pass: list[tuple[str, str, str]] = []
        for agent in orphans:
            short = _short(agent.name)
            try:
                agents_client.delete_agent(agent_name=agent.name)
                deleted_this_pass += 1
                total_deleted += 1
                if progress is not None:
                    progress(
                        "deleted",
                        {
                            "pass": pass_num,
                            "display_name": agent.display_name,
                            "short": short,
                        },
                    )
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).splitlines()[0][:80]
                failed_this_pass.append((agent.display_name, short, msg))

        if deleted_this_pass == 0:
            if progress is not None:
                progress(
                    "no_progress",
                    {
                        "pass": pass_num,
                        "failed": failed_this_pass,
                    },
                )
            return total_deleted, len(failed_this_pass)

    try:
        live_agents = agents_client.list_agents()
        remaining = sum(1 for a in live_agents if a.name not in keep_resources)
    except Exception:  # noqa: BLE001
        remaining = 0
    return total_deleted, remaining
