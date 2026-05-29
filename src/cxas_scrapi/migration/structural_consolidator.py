# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Gemini-driven structural consolidation of a 1:1 MigrationIR into a small
number of capability-rich CXAS agents.

Distinct from `CXASOptimizer` (which deduplicates variables and restructures
single-agent instructions). This module proposes an N → M grouping over the
IR's agents and synthesizes new XML instructions for each group via the
`AsyncAgentDesigner` (Step 2A blueprint + Step 2B PIF XML).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
from typing import Any

from rich.console import Console

from cxas_scrapi.migration.data_models import (
    DFCXAgentIR,
    IRAgent,
    MigrationIR,
    MigrationStatus,
)
from cxas_scrapi.migration.designer import AsyncAgentDesigner
from cxas_scrapi.migration.flow_visualizer import (
    FlowDependencyResolver,
    FlowTreeVisualizer,
)
from cxas_scrapi.utils.gemini import GeminiGenerate

logger = logging.getLogger(__name__)

INSTRUCTION_SUMMARY_LEN = 500
GROUP_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{2,84}$")
AGENT_REF_RE = re.compile(r"{@AGENT:\s*([^}]+)}")
SENTINEL_REFS = {"END_SESSION", "END_FLOW"}
DEFAULT_PER_GROUP_TIMEOUT_S = 600


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _short_id(resource_name: str) -> str:
    return (
        resource_name.rsplit("/", maxsplit=1)[-1]
        if "/" in resource_name
        else resource_name
    )


def _instruction_summary(text: str, n: int = INSTRUCTION_SUMMARY_LEN) -> str:
    collapsed = re.sub(r"\s+", " ", text or "").strip()
    return collapsed[:n] + ("…" if len(collapsed) > n else "")


def _sanitize_display_name(name: str, max_len: int = 85) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9_ -]", "", name)
    sanitized = re.sub(r"[ _-]+", " ", sanitized).strip()
    return sanitized[:max_len] or "MigratedAgent"


# `{@TOOL: name}` directive matcher — kept in sync with
# :data:`integrity_checks.TOOL_REF_RE`.
_HEAL_TOOL_REF_RE = re.compile(r"\{@TOOL:\s*([^}]+)\}")

# Suffix transforms tried in order. Each is `(suffix_to_strip, "")` so
# `X<suffix>` rewrites to `X`. Stored as a tuple of (description, callable)
# so we can extend with non-suffix transforms later if needed.
_HEAL_SUFFIX_STRIPS: tuple[str, ...] = ("_wrapper", "_tool")


def heal_tool_refs(ir: MigrationIR) -> tuple[dict[str, str], list[str]]:
    """Reconcile ``{@TOOL: …}`` references in every agent's instruction
    against the actual tool registry in ``ir.tools``.

    Gemini-synthesized PIF instructions occasionally use a tool ID that
    doesn't exist verbatim — most commonly because the synthesizer
    learned a ``_wrapper`` / ``_tool`` suffix pattern from some tools
    and over-applied it. This pass walks each instruction, finds
    ``{@TOOL: X}`` directives that don't resolve, and applies a small
    set of safe rewrites:

      * ``X_wrapper`` → ``X``   (if ``X`` exists and ``X_wrapper`` doesn't)
      * ``X_tool``    → ``X``   (same rule)

    Only rewrites when the target form exists and the original doesn't.
    If both the original and the candidate exist (rare), the original
    is left alone — the wrapper might have been intentional.

    Does NOT touch refs that resolve directly, sentinel refs
    (``end_session``), or refs with no near-match (those stay for the
    integrity check to surface as genuine hallucinations).

    Args:
        ir: The (consolidated) :class:`MigrationIR` to heal. Mutated in
            place — each agent's ``instruction`` is updated.

    Returns:
        Tuple ``(rewrites_applied, unhealed_refs)``:

        * ``rewrites_applied`` — ``{original_ref: new_ref}`` map of
          every rewrite the pass made, deduped across agents.
        * ``unhealed_refs`` — sorted list of refs that didn't resolve
          and had no safe rewrite. These will be caught by
          :func:`integrity_checks.check_consolidation_integrity`.
    """
    available_ids = set(ir.tools.keys())
    available_short = {_short_id(t.name) for t in ir.tools.values() if t.name}
    # Sentinels that are always auto-registered at deploy time.
    sentinels = {"end_session", "set_session_variables"}
    known = available_ids | available_short | sentinels

    def _candidate(ref: str) -> str | None:
        """Return a safe rewrite for ``ref``, or None."""
        for suffix in _HEAL_SUFFIX_STRIPS:
            if ref.endswith(suffix):
                base = ref[: -len(suffix)]
                if base in known and ref not in known:
                    return base

        # Prefix match check for truncated completion cutoffs (length >= 15
        # to avoid generic collisions)
        if len(ref) >= 15:
            prefix_matches = [k for k in known if k.startswith(ref)]
            if len(prefix_matches) == 1:
                return prefix_matches[0]

        return None

    # Bogus placeholders Gemini sometimes emits when it gives up mid-list.
    # Stripped unconditionally — never a valid tool ID.
    bogus_placeholders = {"...", "…", "TODO", "<tool_name>", "<TOOL_NAME>"}

    rewrites: dict[str, str] = {}
    unhealed: set[str] = set()

    for agent in ir.agents.values():
        instruction = agent.instruction or ""
        if not instruction:
            continue
        new_instruction = instruction
        for match in _HEAL_TOOL_REF_RE.findall(instruction):
            ref = match.strip()
            if ref in known:
                continue
            if ref in bogus_placeholders:
                # Strip the whole `{@TOOL: …}` directive — clearly junk.
                new_instruction = re.sub(
                    r"\{@TOOL:\s*" + re.escape(ref) + r"\s*\}",
                    "",
                    new_instruction,
                )
                rewrites[ref] = "(stripped: placeholder)"
                continue
            cand = _candidate(ref)
            if cand is not None:
                rewrites[ref] = cand
                # Replace `{@TOOL: ref}` → `{@TOOL: cand}` everywhere
                # in this instruction, preserving surrounding whitespace.
                new_instruction = re.sub(
                    r"\{@TOOL:\s*" + re.escape(ref) + r"\s*\}",
                    f"{{@TOOL: {cand}}}",
                    new_instruction,
                )
            else:
                unhealed.add(ref)
        if new_instruction != instruction:
            agent.instruction = new_instruction

    return rewrites, sorted(unhealed)


def detect_root_key(ir: MigrationIR, source_data: Any) -> str | None:
    """Find the IRAgent key corresponding to the source agent's start.

    Matches by display_name against `source_data.start_playbook` resource
    name or by convention 'Default Start Flow'.
    """
    candidates: list[str] = []
    if source_data is not None:
        start_pb = getattr(source_data, "start_playbook", None)
        if start_pb:
            for pb in getattr(source_data, "playbooks", []) or []:
                if pb.get("name") == start_pb:
                    candidates.append(pb.get("displayName"))

    candidates.append("Default Start Flow")

    for key in ir.agents:
        if key in candidates:
            return key
    for key, agent in ir.agents.items():
        if agent.display_name in candidates:
            return key
    return None


def member_to_group_map(groupings: dict) -> dict[str, str]:
    return {
        member: group_name
        for group_name, payload in groupings.items()
        for member in (payload.get("agents") or [])
    }


def root_group_name(groupings: dict, root_key: str | None) -> str | None:
    if not groupings:
        return None
    for name, payload in groupings.items():
        if payload.get("is_root"):
            return name
    if root_key:
        for name, payload in groupings.items():
            if root_key in (payload.get("agents") or []):
                return name
    return next(iter(groupings))


# Suffixes Gemini commonly appends when paraphrasing source agent names
# into a target-like identifier (e.g. "Agent Escalation agent" →
# "AgentEscalationTarget"). Stripped during the normalization pass so the
# core name still matches.
_AGENT_REF_NOISE_SUFFIXES: tuple[str, ...] = (
    "target",
    "wrapper",
    "tool",
    "subagent",
    "sub_agent",
    "agent",
    "flow",
    "playbook",
    "handler",
    "router",
)


def _normalize_agent_ref(name: str) -> str:
    """Aggressively normalize an agent reference for fuzzy matching.

    Strategy:
      1. Insert spaces at camelCase boundaries
         ("AgentEscalationTarget" → "agent escalation target")
      2. Replace ``_-`` runs with spaces; lowercase; drop non-alphanumerics
      3. Repeatedly strip trailing "noise" words like ``target``, ``agent``,
         ``flow`` etc. that Gemini glues onto a base name
      4. Collapse internal whitespace

    Example transforms:
      ``AgentEscalationTarget`` → ``escalation``
      ``Agent Escalation agent`` → ``escalation``
      ``Subflow_API_Error`` → ``subflow api error`` (no noise to strip)
      ``Subflows`` → ``subflows``
    """
    # camelCase → space-separated
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", spaced)
    # punctuation → space
    spaced = re.sub(r"[_\-?!.,/]+", " ", spaced)
    # lowercase + collapse whitespace
    tokens = [t for t in spaced.lower().split() if t.strip()]
    # Strip trailing noise suffixes repeatedly
    changed = True
    while changed and len(tokens) > 1:
        changed = False
        if tokens[-1] in _AGENT_REF_NOISE_SUFFIXES:
            tokens.pop()
            changed = True
    return " ".join(tokens)


def _agent_ref_prefix_match(
    needle: str, haystack: dict[str, str]
) -> str | None:
    """Last-resort matcher: if the needle's normalized form is a prefix
    (or contains) a known display_name's normalized form, return that
    group. Used when full equality fails (``Subflow_API_Error`` →
    ``Subflows``)."""
    if not needle:
        return None
    for display, grp in haystack.items():
        norm_display = _normalize_agent_ref(display)
        if not norm_display:
            continue
        # Either side being a prefix / substring of the other is enough.
        if (
            norm_display.startswith(needle)
            or needle.startswith(norm_display)
            or norm_display in needle
            or needle in norm_display
        ):
            return grp
    return None


def rewrite_agent_refs(
    instruction: str,
    member_to_group: dict[str, str],
    member_display_to_group: dict[str, str],
    self_group: str,
    *,
    group_names: set[str] | None = None,
) -> str:
    """Rewrite {@AGENT: X} so X points to the group containing the original
    target. Refs to members of the same group collapse to an empty string
    (completing the subtask naturally). Unresolved refs also collapse to
    completion to prevent linter errors.

    Resolution order:
      1. Exact match against ``group_names`` — Gemini emitted a consolidated
         group name directly. No rewrite needed.
      2. Exact IR-key match in ``member_to_group``.
      3. Exact display_name match in ``member_display_to_group``.
      4. Normalized-name match (camelCase split, lowercase, strip
         ``_-?!`` and trailing noise suffixes like ``Target``, ``Agent``,
         ``Flow``).
      5. Prefix/substring match on the normalized name.
      6. Strip the directive — unresolved.
    """
    group_names = group_names or set()

    def _sub(match: re.Match) -> str:
        raw = match.group(1).strip()
        if raw.upper() in SENTINEL_REFS:
            if raw.upper() == "END_SESSION":
                return "{@TOOL: end_session}"
            return ""

        # 1. Exact group-name match (Gemini emitted the consolidated name).
        if raw in group_names:
            target_group = raw
        else:
            # 2. Exact IR-key match.
            target_group = member_to_group.get(raw)

        # 3. Exact display_name match.
        if target_group is None:
            target_group = member_display_to_group.get(raw)

        # 4. Normalized match against member display names.
        if target_group is None:
            normalized_raw = _normalize_agent_ref(raw)
            for display, grp in member_display_to_group.items():
                if _normalize_agent_ref(display) == normalized_raw:
                    target_group = grp
                    break

        # 4b. Normalized match against group names (e.g. "RootAgent" raw
        # vs "Root Agent" group).
        if target_group is None:
            normalized_raw = _normalize_agent_ref(raw)
            for grp in group_names:
                if _normalize_agent_ref(grp) == normalized_raw:
                    target_group = grp
                    break

        # 5. Prefix/substring match (last resort before stripping).
        if target_group is None:
            target_group = _agent_ref_prefix_match(
                _normalize_agent_ref(raw), member_display_to_group
            )

        if target_group is None:
            logger.warning(
                "Unresolved {@AGENT: %s} reference inside %r; stripping to "
                "subtask completion.",
                raw,
                self_group,
            )
            return ""

        if target_group == self_group:
            return ""

        return f"{{@AGENT: {target_group}}}"

    rewritten = AGENT_REF_RE.sub(_sub, instruction)
    rewritten = re.sub(r"{@TOOL:\s*END_FLOW\s*}", "", rewritten, flags=re.I)
    return re.sub(r"\n{3,}", "\n\n", rewritten)


def persist_grouping(groupings: dict, path: str) -> str:
    with open(path, "w") as f:
        json.dump(groupings, f, indent=2)
    return path


def load_grouping(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Gemini grouping prompt
# ---------------------------------------------------------------------------


def _build_grouping_prompt(
    ir: MigrationIR,
    root_key: str | None,
    dep_summary: dict[str, Any] | None = None,
    feedback: str | None = None,
) -> str:
    agents_payload = []
    for key, agent in ir.agents.items():
        agents_payload.append(
            {
                "key": key,
                "display_name": agent.display_name,
                "type": agent.type,
                "description": agent.description or "",
                "tools": [_short_id(t) for t in agent.tools],
                "toolsets": [
                    _short_id(ts.get("toolset", "?")) for ts in agent.toolsets
                ],
                "callbacks": [
                    k for k, v in (agent.callbacks or {}).items() if v
                ],
                "instruction_summary": _instruction_summary(agent.instruction),
                "is_source_root": key == root_key,
            }
        )

    tool_inventory = []
    for tool in ir.tools.values():
        tool_inventory.append(
            {
                "id": tool.id,
                "type": tool.type,
                "display_name": (tool.payload or {}).get("displayName")
                or (tool.payload or {}).get("display_name", tool.id),
            }
        )

    dep_block = ""
    if dep_summary:
        dep_block = (
            "\n\nSOURCE DEPENDENCY GRAPH (which 1:1 IR agents reference each "
            "other in the source). Prefer grouping resources that reference "
            "each other:\n"
            f"{json.dumps(dep_summary, indent=2)}\n"
        )

    feedback_block = ""
    if feedback:
        feedback_block = (
            "\n\nUSER FEEDBACK ON PREVIOUS PROPOSAL (apply this revision):\n"
            f"{feedback}\n"
        )

    return f"""You are an expert conversational AI architect consolidating a
fragmented Dialogflow CX (DFCX) agent into a small number of capability-rich
CXAS GenAI agents. The DFCX-side agents have ALREADY been compiled into a
1:1 CXAS IR. Your job is to propose a grouping over those IR agents that
preserves user-journey coverage with fewer, more cohesive agents.

CXAS IR AGENTS (1:1 from source):
{json.dumps(agents_payload, indent=2)}

TOOL INVENTORY (only tools referenceable by grouped agents):
{json.dumps(tool_inventory, indent=2)}

ROUTING EDGES (current cross-agent references):
{json.dumps(ir.routing_edges[:50], indent=2) if ir.routing_edges else "[]"}
{dep_block}{feedback_block}
RULES:
1. Every IR agent key MUST appear in exactly one group's "agents" list.
2. Produce 3 to 7 groups (fewer if the source has very few agents).
3. Group names: PascalCase journey-oriented (e.g., "AuthenticationAgent",
   "BillingAgent", "OrderManagementAgent"). One group MUST be "RootAgent" or
   set "is_root": true; that group MUST contain the IR agent with
   "is_source_root": true.
4. Each group's tools/callbacks will be the UNION of its members' tools and
   callbacks. Do NOT invent new tool names. Do NOT propose merges that drop
   capability.
5. Return ONLY a valid JSON object with this exact shape:

{{
  "GroupName1": {{
    "agents": ["ir_agent_key", ...],
    "rationale": "1-2 sentences on why these belong together",
    "journey": "the user journey this group covers",
    "is_root": true | false
  }},
  ...
}}
"""


def _parse_grouping_response(response: str) -> dict:
    cleaned = (
        response.strip().removeprefix("```json").removeprefix("```").strip()
    )
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(
            f"Could not find JSON object in LLM response:\n{response}"
        )
    return json.loads(cleaned[start : end + 1])


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_groupings(
    ir: MigrationIR, groupings: dict, root_key: str | None
) -> None:
    if not (1 <= len(groupings) <= 12):
        raise ValueError(
            f"Group count {len(groupings)} outside allowed range [1, 12]."
        )

    seen: set[str] = set()
    for group_name, payload in groupings.items():
        if not GROUP_NAME_RE.match(group_name):
            raise ValueError(f"Invalid group name: {group_name!r}")
        members = payload.get("agents") or []
        if not members:
            raise ValueError(f"Group {group_name!r} has no agents.")
        for m in members:
            if m in seen:
                raise ValueError(f"IR agent {m!r} assigned to multiple groups.")
            if m not in ir.agents:
                raise ValueError(
                    f"Group {group_name!r} references unknown IR agent {m!r}."
                )
            seen.add(m)

    missing = set(ir.agents.keys()) - seen
    if missing:
        raise ValueError(
            f"IR agents not assigned to any group: {sorted(missing)}"
        )

    if root_key:
        root_groups = [
            name
            for name, payload in groupings.items()
            if payload.get("is_root")
            or root_key in (payload.get("agents") or [])
        ]
        if not root_groups:
            raise ValueError(
                "No group claims is_root=true and no group contains the "
                f"source root agent key {root_key!r}."
            )


# ---------------------------------------------------------------------------
# Consolidation
# ---------------------------------------------------------------------------


def consolidate(ir: MigrationIR, groupings: dict) -> MigrationIR:
    """Build a new MigrationIR whose agents are the proposed groups.

    Preserves metadata, tools, parameters, and routing_edges from the
    original. Member instructions are concatenated under section headers;
    tools/toolsets/callbacks are unioned. Cross-group {@AGENT:} references
    are rewritten; same-group references are stripped.
    """
    new_ir = ir.model_copy(deep=True)
    new_ir.agents = {}

    m2g = member_to_group_map(groupings)
    member_display_to_group = {
        ir.agents[k].display_name: g for k, g in m2g.items() if k in ir.agents
    }

    for group_name, payload in groupings.items():
        members = payload.get("agents") or []
        member_agents = [ir.agents[m] for m in members if m in ir.agents]
        if not member_agents:
            continue

        instructions: list[str] = []
        tools: list[str] = []
        toolsets: list[dict] = []
        callbacks: dict[str, str] = {}
        types: set[str] = set()

        for m_key, agent in zip(members, member_agents, strict=False):
            section = (
                f"<!-- Section: {agent.display_name} ({m_key}) -->\n"
                f"{agent.instruction or ''}"
            )
            instructions.append(
                rewrite_agent_refs(
                    section, m2g, member_display_to_group, group_name
                )
            )

            for t in agent.tools:
                if t not in tools:
                    tools.append(t)

            for ts in agent.toolsets:
                key = ts.get("toolset")
                if not any(
                    existing.get("toolset") == key for existing in toolsets
                ):
                    toolsets.append(dict(ts))

            for cb_type, cb_code in (agent.callbacks or {}).items():
                if not cb_code:
                    continue
                callbacks[cb_type] = (
                    callbacks.get(cb_type, "")
                    + ("\n\n" if callbacks.get(cb_type) else "")
                    + cb_code
                )

            types.add(agent.type)

        description = (
            payload.get("journey")
            or payload.get("rationale")
            or f"Consolidated agent: {group_name}"
        )

        new_ir.agents[group_name] = IRAgent(
            type="FLOW" if "FLOW" in types else "PLAYBOOK",
            display_name=_sanitize_display_name(group_name),
            description=description,
            instruction="\n\n".join(instructions),
            tools=tools,
            toolsets=toolsets,
            callbacks=callbacks or None,
            status=MigrationStatus.COMPILED,
        )

    return new_ir


# ---------------------------------------------------------------------------
# Synthesis (Step 2A blueprint + Step 2B PIF XML per group)
# ---------------------------------------------------------------------------


def _build_combined_tree_view(
    members: list[str],
    source_data: DFCXAgentIR,
    ir: MigrationIR,
) -> str:
    """Render a combined tree view of all source flows/playbooks in a group,
    suitable as context for the AsyncAgentDesigner."""
    if source_data is None:
        # Fall back to the IR agent instruction text.
        chunks: list[str] = []
        for m_key in members:
            agent = ir.agents.get(m_key)
            if agent:
                chunks.append(
                    f"\n--- Agent: {agent.display_name} ---\n"
                    f"{agent.instruction or ''}\n"
                )
        return "".join(chunks)

    resolver = FlowDependencyResolver(source_data)
    combined = ""
    for m_key in members:
        flow_wrapper = next(
            (
                f
                for f in source_data.flows
                if f.flow_data.get("displayName") == m_key
                or f.flow_id.endswith(m_key)
            ),
            None,
        )
        if flow_wrapper:
            context_data = resolver.resolve(flow_wrapper)
            viz = FlowTreeVisualizer(context_data)
            buf = io.StringIO()
            flow_console = Console(file=buf, width=120, force_terminal=False)
            flow_console.print(viz.build_tree())
            combined += (
                f"\n--- Flow: {flow_wrapper.flow_data.get('displayName')} "
                f"---\n{buf.getvalue()}"
            )
        else:
            agent = ir.agents.get(m_key)
            if agent:
                combined += (
                    f"\n--- Agent: {agent.display_name} ---\n"
                    f"{agent.instruction or ''}\n"
                )
    return combined


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class StructuralConsolidator:
    """Gemini-driven N→M agent grouping + per-group instruction synthesis.

    This is distinct from `CXASOptimizer` (variable dedup + per-agent
    instruction restructuring): it operates at the agent-set level, collapsing
    many small agents into a small number of capability-rich agents.

    Typical usage:
        consolidator = StructuralConsolidator(ir, gemini_client, source_data)
        groupings = await consolidator.propose_groupings(root_key, dep_summary)
        validate_groupings(ir, groupings, root_key)  # also done inside propose
        consolidated_ir = consolidator.consolidate(groupings)
        await consolidator.synthesize_instructions(consolidated_ir, groupings)
    """

    def __init__(
        self,
        ir: MigrationIR,
        gemini_client: GeminiGenerate,
        source_data: DFCXAgentIR | None = None,
    ):
        self.ir = ir
        self.gemini = gemini_client
        self.source_data = source_data

    async def propose_groupings(
        self,
        root_key: str | None = None,
        dep_summary: dict[str, Any] | None = None,
        feedback: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        prompt = _build_grouping_prompt(
            self.ir, root_key, dep_summary, feedback
        )
        response = await self.gemini.generate_async(
            prompt=prompt,
            system_prompt=(
                "You are an expert AI architect. Output valid JSON only."
            ),
        )
        if not response:
            raise RuntimeError(
                "Gemini returned no response for grouping proposal."
            )
        groupings = _parse_grouping_response(response)
        validate_groupings(self.ir, groupings, root_key)
        return groupings

    def consolidate(self, groupings: dict) -> MigrationIR:
        return consolidate(self.ir, groupings)

    @staticmethod
    def _build_available_groups_context(groupings: dict) -> str:
        """Render the consolidated group inventory for the synthesis
        prompt — each group name plus the original member display names
        it absorbed. Tells Gemini exactly which ``{@AGENT: …}`` IDs are
        valid transfer targets so it stops inventing new ones."""
        if not groupings:
            return "(no other consolidated groups)"
        lines: list[str] = []
        for grp_name, payload in groupings.items():
            members = payload.get("agents") or []
            members_str = (
                ", ".join(members)
                if members
                else "(no original members listed)"
            )
            tag = " [root]" if payload.get("is_root") else ""
            lines.append(f"- {grp_name}{tag} (consolidates: {members_str})")
        return "\n".join(lines)

    async def synthesize_instructions(
        self,
        consolidated_ir: MigrationIR,
        groupings: dict,
        *,
        per_group_timeout_s: int = DEFAULT_PER_GROUP_TIMEOUT_S,
    ) -> dict[str, str]:
        """Synthesize PIF XML instructions for each consolidated group.

        Each per-group call is wrapped in `asyncio.wait_for(..., timeout=...)`
        so a single hang on Gemini doesn't block the others. On timeout or
        error, the existing concatenated instruction stays in place and the
        group is recorded in the returned status dict.

        Returns a per-group status dict like
        ``{group_name: "ok" | "timeout" | "error"}``.
        """
        designer = AsyncAgentDesigner(gemini_client=self.gemini)
        m2g = member_to_group_map(groupings)
        member_display_to_group = {
            self.ir.agents[k].display_name: g
            for k, g in m2g.items()
            if k in self.ir.agents
        }
        available_groups_context = self._build_available_groups_context(
            groupings
        )

        async def _one(group_name: str, members: list[str]) -> str:
            combined_tree = _build_combined_tree_view(
                members, self.source_data, self.ir
            )
            if not combined_tree.strip():
                logger.warning(
                    "No tree view for %s; keeping concatenated fallback.",
                    group_name,
                )
                return "no-context"

            try:
                blueprint = await asyncio.wait_for(
                    designer.run_step_2a(
                        flow_name=group_name,
                        tree_view=combined_tree,
                        target_ir=self.ir,
                        available_groups=available_groups_context,
                        self_group=group_name,
                    ),
                    timeout=per_group_timeout_s,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Step 2A timed out for %s after %ds; keeping fallback.",
                    group_name,
                    per_group_timeout_s,
                )
                return "timeout"
            except Exception as exc:  # noqa: BLE001
                logger.warning("Step 2A failed for %s: %s", group_name, exc)
                return "error"

            if "error" in blueprint:
                return "blueprint-error"

            try:
                xml_instructions = await asyncio.wait_for(
                    designer.run_step_2b_instructions(
                        flow_name=group_name,
                        blueprint=blueprint,
                        tree_view=combined_tree,
                        # Pass the IR so the 2B prompt receives the exact
                        # tool registry — prevents Gemini from
                        # hallucinating ``_wrapper`` / ``_tool`` suffixes
                        # on tool IDs that don't actually exist.
                        target_ir=self.ir,
                        available_groups=available_groups_context,
                        self_group=group_name,
                    ),
                    timeout=per_group_timeout_s,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Step 2B timed out for %s after %ds; keeping fallback.",
                    group_name,
                    per_group_timeout_s,
                )
                return "timeout"
            except Exception as exc:  # noqa: BLE001
                logger.warning("Step 2B failed for %s: %s", group_name, exc)
                return "error"

            if not xml_instructions:
                return "empty-response"

            xml_instructions = rewrite_agent_refs(
                xml_instructions,
                m2g,
                member_display_to_group,
                group_name,
                # Gemini may emit a consolidated group name directly when
                # the synthesis prompt advertises them — accept those as
                # exact matches without going through the member lookup.
                group_names=set(groupings.keys()),
            )
            consolidated_ir.agents[group_name].instruction = xml_instructions
            return "ok"

        statuses: dict[str, str] = {}
        results = await asyncio.gather(
            *(
                _one(group_name, payload.get("agents", []))
                for group_name, payload in groupings.items()
            ),
            return_exceptions=False,
        )
        for (group_name, _), status in zip(
            groupings.items(), results, strict=True
        ):
            statuses[group_name] = status
        return statuses
