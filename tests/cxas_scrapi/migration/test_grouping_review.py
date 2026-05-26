# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Tests for :mod:`cxas_scrapi.migration.grouping_review`.

The interactive InquirerPy loop is mocked at the prompt level; we
verify the loop's response to each action (accept / quit / repropose),
the structure of the rendered Rich trees, and the return contract
(``dict | None`` — never the consolidated IR).
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest
from InquirerPy.prompts.input import InputPrompt
from InquirerPy.prompts.list import ListPrompt
from rich.console import Console as RichConsole

from cxas_scrapi.migration import grouping_review
from cxas_scrapi.migration.data_models import (
    IRAgent,
    IRMetadata,
    MigrationIR,
)


def _make_ir(agent_names: list[str] | None = None) -> MigrationIR:
    names = agent_names or ["RootAgent", "Helper"]
    return MigrationIR(
        metadata=IRMetadata(
            app_name="t",
            app_id="11111111-1111-1111-1111-111111111111",
        ),
        agents={
            n: IRAgent(
                type="PLAYBOOK",
                display_name=n,
                instruction="<x/>",
            )
            for n in names
        },
    )


def _make_consolidator(consolidated_ir: MigrationIR | None = None):
    """Build a fake StructuralConsolidator with the two methods the
    review TUI calls: ``consolidate`` and ``propose_groupings``."""
    consolidator = MagicMock()
    consolidator.consolidate = MagicMock(
        return_value=consolidated_ir or _make_ir(["RootGroup"])
    )
    consolidator.propose_groupings = AsyncMock(
        return_value={"RootGroup": {"agents": ["RootAgent"], "is_root": True}}
    )
    return consolidator


# ---------------------------------------------------------------------------
# render_ir_tree
# ---------------------------------------------------------------------------


def _render_tree_to_str(tree) -> str:
    buf = StringIO()
    RichConsole(file=buf, width=200, force_terminal=False).print(tree)
    return buf.getvalue()


def test_render_ir_tree_marks_root_agent():
    ir = _make_ir()
    tree = grouping_review.render_ir_tree(ir, "Test", root_key="RootAgent")
    out = _render_tree_to_str(tree)
    assert "Test" in out
    assert "RootAgent" in out
    assert "(root)" in out


def test_render_ir_tree_no_root_does_not_mark_anything():
    ir = _make_ir()
    tree = grouping_review.render_ir_tree(ir, "Test", root_key=None)
    assert "(root)" not in _render_tree_to_str(tree)


# ---------------------------------------------------------------------------
# render_diff
# ---------------------------------------------------------------------------


def test_render_diff_prints_summary_stats():
    before = _make_ir(["A", "B", "C"])
    after = _make_ir(["AB", "C"])
    fake_console = MagicMock()
    grouping_review.render_diff(
        before, after, root_key="A", root_group="AB", console=fake_console
    )
    # The summary line is the LAST console.print call
    printed_args = [c.args for c in fake_console.print.call_args_list if c.args]
    summary = printed_args[-1][0]
    assert "agents 3 → 2" in summary


# ---------------------------------------------------------------------------
# interactive_review — accept / quit / preview failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interactive_review_accept_returns_groupings_unchanged():
    """On [a]ccept the loop returns the groupings dict — NOT the
    consolidated IR. Caller is responsible for committing."""
    ir = _make_ir()
    groupings = {"RootGroup": {"agents": ["RootAgent"], "is_root": True}}
    consolidator = _make_consolidator()

    # Stub InquirerPy: render_diff doesn't matter; the select returns "accept".
    fake_select = create_autospec(ListPrompt, instance=True)
    fake_select.execute_async.return_value = "accept"

    with (
        patch.object(grouping_review, "render_diff"),
        patch.object(
            grouping_review.inquirer,
            "select",
            autospec=True,
            return_value=fake_select,
        ),
    ):
        result = await grouping_review.interactive_review(
            ir, groupings, consolidator, root_key="RootAgent"
        )

    assert result is groupings
    # Consolidator was called ONCE for preview (no commit step in the TUI).
    consolidator.consolidate.assert_called_once_with(groupings)


@pytest.mark.asyncio
async def test_interactive_review_quit_returns_none():
    ir = _make_ir()
    groupings = {"RootGroup": {"agents": ["RootAgent"], "is_root": True}}
    consolidator = _make_consolidator()

    fake_select = create_autospec(ListPrompt, instance=True)
    fake_select.execute_async.return_value = "quit"

    with (
        patch.object(grouping_review, "render_diff"),
        patch.object(
            grouping_review.inquirer,
            "select",
            autospec=True,
            return_value=fake_select,
        ),
    ):
        result = await grouping_review.interactive_review(
            ir, groupings, consolidator
        )

    assert result is None


@pytest.mark.asyncio
async def test_interactive_review_preview_failure_returns_none():
    """If consolidator.consolidate raises during preview, the loop
    aborts cleanly with None."""
    ir = _make_ir()
    groupings = {"RootGroup": {"agents": ["RootAgent"], "is_root": True}}
    consolidator = MagicMock()
    consolidator.consolidate = MagicMock(
        side_effect=RuntimeError("consolidate exploded")
    )

    fake_console = MagicMock()
    result = await grouping_review.interactive_review(
        ir, groupings, consolidator, console=fake_console
    )
    assert result is None


@pytest.mark.asyncio
async def test_interactive_review_repropose_then_accept():
    """[r]e-propose calls ``consolidator.propose_groupings(feedback)``
    and uses the new groupings on the next iteration."""
    ir = _make_ir()
    initial_groupings = {"G1": {"agents": ["RootAgent"], "is_root": True}}
    new_groupings = {"G2": {"agents": ["RootAgent", "Helper"], "is_root": True}}
    consolidator = _make_consolidator()
    consolidator.propose_groupings = AsyncMock(return_value=new_groupings)

    # First iteration: select repropose → text prompt for feedback → next
    # iteration: select accept.
    fake_select = create_autospec(ListPrompt, instance=True)
    fake_select.execute_async.side_effect = ["repropose", "accept"]
    fake_text = create_autospec(InputPrompt, instance=True)
    fake_text.execute_async.return_value = "please split G1"

    with (
        patch.object(grouping_review, "render_diff"),
        patch.object(
            grouping_review.inquirer,
            "select",
            autospec=True,
            return_value=fake_select,
        ),
        patch.object(
            grouping_review.inquirer,
            "text",
            autospec=True,
            return_value=fake_text,
        ),
    ):
        result = await grouping_review.interactive_review(
            ir,
            initial_groupings,
            consolidator,
            root_key="RootAgent",
            dep_summary={"some": "summary"},
        )

    assert result is new_groupings
    consolidator.propose_groupings.assert_awaited_once_with(
        "RootAgent", {"some": "summary"}, "please split G1"
    )
