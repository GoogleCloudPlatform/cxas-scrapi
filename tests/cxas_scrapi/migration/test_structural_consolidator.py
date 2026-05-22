# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Tests for :mod:`cxas_scrapi.migration.structural_consolidator`.

Focus: :func:`heal_tool_refs` — the auto-rewrite pass that reconciles
``{@TOOL: …}`` directives against the actual tool registry after
Gemini-driven synthesis.
"""

from __future__ import annotations

from cxas_scrapi.migration.data_models import (
    IRAgent,
    IRMetadata,
    IRTool,
    MigrationIR,
)
from cxas_scrapi.migration.structural_consolidator import (
    StructuralConsolidator,
    _normalize_agent_ref,
    heal_tool_refs,
    rewrite_agent_refs,
)


def _make_ir(
    *,
    tools: dict[str, str],
    agents: dict[str, str],
) -> MigrationIR:
    """Tiny IR builder. ``tools`` maps ``id -> type``;
    ``agents`` maps ``name -> instruction``."""
    return MigrationIR(
        metadata=IRMetadata(app_name="t"),
        tools={
            tid: IRTool(
                id=tid,
                name=f"projects/p/locations/us/apps/X/tools/{tid}",
                type=ttype,
                payload={},
            )
            for tid, ttype in tools.items()
        },
        agents={
            name: IRAgent(
                type="PLAYBOOK",
                display_name=name,
                instruction=instr,
            )
            for name, instr in agents.items()
        },
    )


def test_heal_strips_wrapper_suffix_when_base_exists():
    ir = _make_ir(
        tools={"authenticate_user": "PYTHON"},
        agents={"A": "Call {@TOOL: authenticate_user_wrapper} please."},
    )

    rewrites, unhealed = heal_tool_refs(ir)

    assert rewrites == {"authenticate_user_wrapper": "authenticate_user"}
    assert unhealed == []
    assert (
        ir.agents["A"].instruction == "Call {@TOOL: authenticate_user} please."
    )


def test_heal_strips_tool_suffix_when_base_exists():
    ir = _make_ir(
        tools={"get_account": "PYTHON"},
        agents={"A": "Use {@TOOL: get_account_tool}."},
    )

    rewrites, unhealed = heal_tool_refs(ir)

    assert rewrites == {"get_account_tool": "get_account"}
    assert "{@TOOL: get_account}" in ir.agents["A"].instruction


def test_heal_no_rewrite_when_exact_match_exists():
    """If the suffixed form ALSO exists, leave it alone — the wrapper
    might have been intentional."""
    ir = _make_ir(
        tools={
            "verify_pin": "PYTHON",
            "verify_pin_wrapper": "PYTHON",
        },
        agents={"A": "Use {@TOOL: verify_pin_wrapper}."},
    )

    rewrites, unhealed = heal_tool_refs(ir)

    assert rewrites == {}
    assert unhealed == []
    assert "{@TOOL: verify_pin_wrapper}" in ir.agents["A"].instruction


def test_heal_leaves_genuine_hallucinations_for_integrity_check():
    """Refs with no near match are unhealed and surface as integrity
    errors downstream — heal_tool_refs does not invent rewrites."""
    ir = _make_ir(
        tools={"authenticate_user": "PYTHON"},
        agents={
            "A": (
                "First {@TOOL: authenticate_user_wrapper}, then "
                "{@TOOL: execute_escalation_transfer}."
            ),
        },
    )

    rewrites, unhealed = heal_tool_refs(ir)

    assert rewrites == {"authenticate_user_wrapper": "authenticate_user"}
    assert unhealed == ["execute_escalation_transfer"]
    # Hallucinated ref stays in the instruction for integrity_check.
    assert "{@TOOL: execute_escalation_transfer}" in ir.agents["A"].instruction


def test_heal_ignores_sentinel_refs():
    """``end_session`` and ``set_session_variables`` are auto-registered
    sentinels — never marked unhealed."""
    ir = _make_ir(
        tools={},
        agents={
            "A": (
                "Final {@TOOL: end_session} with reason. "
                "Also {@TOOL: set_session_variables}."
            ),
        },
    )

    rewrites, unhealed = heal_tool_refs(ir)

    assert rewrites == {}
    assert unhealed == []


def test_heal_handles_short_id_resolution():
    """Tool names in the registry are stored with a full resource name;
    refs use the short id. ``heal_tool_refs`` should consider both."""
    ir = MigrationIR(
        metadata=IRMetadata(app_name="t"),
        tools={
            "auth_tool": IRTool(
                id="auth_tool",
                name="projects/p/locations/us/apps/X/tools/auth_short_id",
                type="PYTHON",
                payload={},
            )
        },
        agents={
            "A": IRAgent(
                type="PLAYBOOK",
                display_name="A",
                # Ref via short id (resource-name suffix) — should be
                # recognized as known.
                instruction="{@TOOL: auth_short_id}",
            )
        },
    )

    rewrites, unhealed = heal_tool_refs(ir)

    assert rewrites == {}
    assert unhealed == []


def test_heal_rewrites_across_multiple_agents_dedups_audit():
    """Same ref appearing in multiple agents is rewritten in all of
    them; the returned rewrites dict is deduped."""
    ir = _make_ir(
        tools={"get_rate_plan": "TOOLSET"},
        agents={
            "A": "First {@TOOL: get_rate_plan_wrapper}.",
            "B": "Also {@TOOL: get_rate_plan_wrapper}.",
        },
    )

    rewrites, unhealed = heal_tool_refs(ir)

    assert rewrites == {"get_rate_plan_wrapper": "get_rate_plan"}
    assert unhealed == []
    assert "{@TOOL: get_rate_plan}" in ir.agents["A"].instruction
    assert "{@TOOL: get_rate_plan}" in ir.agents["B"].instruction


def test_heal_empty_ir_is_noop():
    ir = _make_ir(tools={}, agents={})
    rewrites, unhealed = heal_tool_refs(ir)
    assert rewrites == {}
    assert unhealed == []


def test_heal_skips_agents_with_no_instruction():
    ir = MigrationIR(
        metadata=IRMetadata(app_name="t"),
        tools={
            "x": IRTool(id="x", name="x", type="PYTHON", payload={}),
        },
        agents={
            "A": IRAgent(type="PLAYBOOK", display_name="A", instruction=""),
        },
    )
    rewrites, unhealed = heal_tool_refs(ir)
    assert rewrites == {}
    assert unhealed == []


def test_heal_strips_literal_ellipsis_placeholder():
    """Gemini sometimes emits ``{@TOOL: ...}`` (literal ellipsis) as a
    placeholder in a tool list it didn't finish. The heal pass strips
    those directives entirely — they're never a valid tool ID."""
    ir = _make_ir(
        tools={"real_tool": "PYTHON"},
        agents={
            "A": "First {@TOOL: ...} then call {@TOOL: real_tool}.",
            "B": "Use {@TOOL: …}.",  # unicode ellipsis variant
        },
    )

    rewrites, unhealed = heal_tool_refs(ir)

    # Both placeholders recorded as stripped.
    assert "..." in rewrites
    assert "…" in rewrites
    assert unhealed == []
    # The bogus directive is gone from the instructions.
    assert "{@TOOL: ...}" not in ir.agents["A"].instruction
    assert "{@TOOL: …}" not in ir.agents["B"].instruction
    # Real tool ref preserved.
    assert "{@TOOL: real_tool}" in ir.agents["A"].instruction


# ---------------------------------------------------------------------------
# _normalize_agent_ref — handles camelCase, suffixes, punctuation
# ---------------------------------------------------------------------------


def test_normalize_agent_ref_handles_camelcase_with_noise_suffix():
    """``AgentEscalationTarget`` should normalize to something that
    matches ``Agent Escalation agent`` after stripping noise tokens."""
    # Both forms drop the trailing noise suffix (`Target` and trailing
    # `agent`, respectively) and yield the same canonical string.
    a = _normalize_agent_ref("AgentEscalationTarget")
    b = _normalize_agent_ref("Agent Escalation agent")
    assert a == b == "agent escalation"


def test_normalize_agent_ref_matches_source_display():
    """The source display name ``Agent Escalation agent`` normalizes
    to the same string as ``AgentEscalationTarget`` so fuzzy matching
    can connect them."""
    a = _normalize_agent_ref("Agent Escalation agent")
    b = _normalize_agent_ref("AgentEscalationTarget")
    assert a == b


def test_normalize_agent_ref_preserves_anything_else_punctuation():
    """``Anything Else?`` → ``anything else`` (question mark stripped)."""
    assert _normalize_agent_ref("Anything Else?") == "anything else"
    assert _normalize_agent_ref("AnythingElseTarget") == "anything else"


def test_normalize_agent_ref_keeps_root_tokens():
    """Don't over-strip — the last surviving non-noise token must
    remain even if it happens to look generic."""
    assert _normalize_agent_ref("Subflows") == "subflows"
    # Subflow_API_Error doesn't end in a noise suffix, so it's preserved.
    assert _normalize_agent_ref("Subflow_API_Error") == "subflow api error"


# ---------------------------------------------------------------------------
# rewrite_agent_refs — fuzzy matching to consolidated groups
# ---------------------------------------------------------------------------


def test_rewrite_agent_refs_matches_camelcase_target_suffix():
    """``{@AGENT: AgentEscalationTarget}`` rewrites to the group
    containing ``Agent Escalation agent``."""
    member_display_to_group = {
        "Agent Escalation agent": "SessionManagementAgent",
        "Anything Else?": "SessionManagementAgent",
    }
    result = rewrite_agent_refs(
        instruction="Hand off via {@AGENT: AgentEscalationTarget}.",
        member_to_group={},
        member_display_to_group=member_display_to_group,
        self_group="AccountSecurityAgent",
        group_names={"SessionManagementAgent", "AccountSecurityAgent"},
    )
    assert "{@AGENT: SessionManagementAgent}" in result
    assert "AgentEscalationTarget" not in result


def test_rewrite_agent_refs_accepts_group_name_directly():
    """Gemini may emit a consolidated group name verbatim (the new
    prompt advertises them). Treat as exact match — no rewrite needed."""
    result = rewrite_agent_refs(
        instruction="Route to {@AGENT: AddressManagementAgent}.",
        member_to_group={},
        member_display_to_group={},
        self_group="RootAgent",
        group_names={"RootAgent", "AddressManagementAgent"},
    )
    assert "{@AGENT: AddressManagementAgent}" in result


def test_rewrite_agent_refs_prefix_match_fallback():
    """The prefix matcher catches refs whose normalized form is a
    prefix/substring of a known display_name (after normalization).

    Example: ``AccountProfile`` (Gemini abbreviation) →
    normalized ``account profile`` is a prefix of
    ``Account Profile Manager``' normalized form."""
    member_display_to_group = {
        "Account Profile Manager": "AccountProfileAgent",
    }
    result = rewrite_agent_refs(
        instruction="Hand off to {@AGENT: AccountProfile}.",
        member_to_group={},
        member_display_to_group=member_display_to_group,
        self_group="RootAgent",
        group_names={"AccountProfileAgent", "RootAgent"},
    )
    assert "{@AGENT: AccountProfileAgent}" in result


def test_rewrite_agent_refs_strips_genuine_hallucinations():
    """A ref with no plausible match still gets stripped (existing
    behavior preserved). Examples: ``MainIntentRouter``, ``LiveAgent``
    — Gemini-invented routing concepts that don't exist anywhere."""
    member_display_to_group = {
        "Default Start Flow": "RootAgent",
    }
    result = rewrite_agent_refs(
        instruction="Try {@AGENT: NeverHeardOfThis}.",
        member_to_group={},
        member_display_to_group=member_display_to_group,
        self_group="RootAgent",
        group_names={"RootAgent"},
    )
    assert "{@AGENT:" not in result


def test_rewrite_agent_refs_self_group_collapses_to_empty():
    """Transfers to the agent's own group collapse to an empty
    string — the subtask completes naturally."""
    member_display_to_group = {
        "Acct Mgmt General": "AccountProfileAgent",
    }
    result = rewrite_agent_refs(
        instruction="Loop back to {@AGENT: Acct Mgmt General}.",
        member_to_group={},
        member_display_to_group=member_display_to_group,
        self_group="AccountProfileAgent",
        group_names={"AccountProfileAgent"},
    )
    assert "{@AGENT:" not in result


# ---------------------------------------------------------------------------
# _build_available_groups_context — surface to Gemini synthesis prompt
# ---------------------------------------------------------------------------


def test_build_available_groups_context_lists_groups_with_members():
    groupings = {
        "RootAgent": {
            "agents": ["Default Start Flow", "Welcome Playbook"],
            "is_root": True,
        },
        "AddressManagementAgent": {
            "agents": ["Acct Mgmt Address Disambig", "Address Collection"],
            "is_root": False,
        },
    }
    context = StructuralConsolidator._build_available_groups_context(groupings)
    assert "- RootAgent [root] (consolidates: Default Start Flow," in context
    assert "- AddressManagementAgent (consolidates:" in context
    assert "Acct Mgmt Address Disambig" in context


def test_build_available_groups_context_empty_groupings():
    context = StructuralConsolidator._build_available_groups_context({})
    assert "no other consolidated groups" in context
