# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Pytest plugin for CES callback chain trace collection and snapshots."""

from __future__ import annotations

import contextlib
import dataclasses
import os
from pathlib import Path
from typing import Any

import pytest

from cxas_scrapi.evals.callback_chain.compare import (
    _pop_active_mode_diff_collector,
    _push_active_mode_diff_collector,
)
from cxas_scrapi.evals.callback_chain.runner import (
    _pop_active_stage_collector,
    _push_active_stage_collector,
)
from cxas_scrapi.evals.callback_chain.snapshots import (
    _ACTIVE_SNAPSHOT_CONTEXT,
    SnapshotManager,
    _reset_unnamed_snapshot_counts,
)
from cxas_scrapi.evals.callback_chain.traces import (
    CHAIN_LLM_ENV,
    CallbackTrace,
    ModeDiffReport,
    TurnTrace,
)
from cxas_scrapi.evals.callback_chain.turn import (
    _pop_active_turn_collector,
    _push_active_turn_collector,
)


@dataclasses.dataclass
class CxasTraceCollector:
    """Per-test trace collector exposed via `cxas_trace_collector` fixture.

    Traces emitted from context-less background worker threads (e.g.
    `ThreadPoolExecutor`) are attributed to the innermost active collector on
    the fallback stack; running concurrent trace collectors across threads in
    the same process is not supported.
    """

    test_name: str
    nodeid: str
    turn_traces: list[TurnTrace] = dataclasses.field(default_factory=list)
    stage_traces: list[CallbackTrace] = dataclasses.field(default_factory=list)
    mode_diffs: list[ModeDiffReport] = dataclasses.field(default_factory=list)

    @property
    def has_traces(self) -> bool:
        """True if at least one turn, stage, or mode diff was recorded."""
        return bool(self.turn_traces or self.stage_traces or self.mode_diffs)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Registers `--cxas-*` CLI flags on pytest."""
    group = parser.getgroup(
        "cxas", "CES Callback Chain & Agent Testing Options"
    )
    options = [
        (
            "--cxas-update-snapshots",
            {
                "action": "store_true",
                "default": False,
                "help": (
                    "Update stored .snap.json snapshots with current trace "
                    "outputs."
                ),
            },
        ),
        (
            "--cxas-snapshot-ci",
            {
                "action": "store_true",
                "default": False,
                "help": (
                    "Fail when a snapshot entry is missing instead of creating "
                    "it on the fly."
                ),
            },
        ),
        (
            "--cxas-llm",
            {
                "action": "store",
                "default": None,
                "metavar": "SNAPSHOT_OR_ID",
                "help": (
                    "Optional LLM snapshot JSON path or registered adapter "
                    "identifier (sets CXAS_CALLBACK_LLM; live model strings "
                    "require register_llm_resolver)."
                ),
            },
        ),
    ]
    for opt_name, kwargs in options:
        with contextlib.suppress(ValueError):
            # Option already registered (e.g., nested pytest invocation)
            group.addoption(opt_name, **kwargs)


def pytest_configure(config: pytest.Config) -> None:
    """Initializes session-wide trace storage and optional `--cxas-llm` env."""
    config._cxas_session_data = {  # type: ignore[attr-defined]
        "turn_traces": {},
        "stage_traces": {},
        "mode_diffs": {},
        "test_results": [],
    }
    llm_opt = config.getoption("--cxas-llm", default=None)
    if llm_opt:
        config._cxas_prev_llm_env = os.environ.get(CHAIN_LLM_ENV)  # type: ignore[attr-defined]
        if str(llm_opt).strip().lower() == "mock":
            os.environ.pop(CHAIN_LLM_ENV, None)
        else:
            os.environ[CHAIN_LLM_ENV] = str(llm_opt)


def pytest_unconfigure(config: pytest.Config) -> None:
    """Restores `CXAS_CALLBACK_LLM` if overridden by `--cxas-llm`."""
    if hasattr(config, "_cxas_prev_llm_env"):
        prev = config._cxas_prev_llm_env  # type: ignore[attr-defined]
        if prev is None:
            os.environ.pop(CHAIN_LLM_ENV, None)
        else:
            os.environ[CHAIN_LLM_ENV] = prev


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Pushes active trace collectors and snapshot context before each test."""
    if getattr(item, "_cxas_tokens", None) is not None:
        return
    if _ACTIVE_SNAPSHOT_CONTEXT.get() is None:
        _reset_unnamed_snapshot_counts()
    collector = CxasTraceCollector(
        test_name=item.name,
        nodeid=item.nodeid,
    )
    turn_token = _push_active_turn_collector(collector.turn_traces)
    stage_token = _push_active_stage_collector(collector.stage_traces)
    diff_token = _push_active_mode_diff_collector(collector.mode_diffs)

    update_snaps = bool(
        item.config.getoption("--cxas-update-snapshots", default=False)
    )
    ci_mode = bool(item.config.getoption("--cxas-snapshot-ci", default=False))

    orig_test_file = os.environ.get("CXAS_ORIGINAL_TEST_FILE")
    test_file_path = (
        str(Path(orig_test_file).resolve())
        if orig_test_file
        else str(Path(str(item.fspath)).resolve())
    )
    scoped_test_name = (
        item.nodeid.split("::", 1)[1] if "::" in item.nodeid else item.name
    )
    snap_token = _ACTIVE_SNAPSHOT_CONTEXT.set(
        {
            "test_file": test_file_path,
            "test_name": scoped_test_name,
            "nodeid": item.nodeid,
            "update_snapshots": update_snaps,
            "ci_mode": ci_mode,
            "unnamed_counts": {},
        }
    )

    item._cxas_collector = collector  # type: ignore[attr-defined]
    item._cxas_tokens = (turn_token, stage_token, diff_token, snap_token)  # type: ignore[attr-defined]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Any:
    """Captures test execution outcomes."""
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"_cxas_rep_{rep.when}", rep)


def pytest_runtest_teardown(
    item: pytest.Item, nextitem: pytest.Item | None
) -> None:
    """Pops active collectors and aggregates traces into session storage."""
    tokens = getattr(item, "_cxas_tokens", None)
    collector: CxasTraceCollector | None = getattr(
        item, "_cxas_collector", None
    )
    if tokens is not None:
        item._cxas_tokens = None  # type: ignore[attr-defined]
        turn_token, stage_token, diff_token, snap_token = tokens
        _pop_active_turn_collector(
            turn_token,
            collector.turn_traces if collector is not None else None,
        )
        _pop_active_stage_collector(
            stage_token,
            collector.stage_traces if collector is not None else None,
        )
        _pop_active_mode_diff_collector(
            diff_token,
            collector.mode_diffs if collector is not None else None,
        )
        _ACTIVE_SNAPSHOT_CONTEXT.reset(snap_token)

    session_data = getattr(item.config, "_cxas_session_data", None)
    if not isinstance(session_data, dict) or collector is None:
        return

    rep_call = getattr(item, "_cxas_rep_call", None)
    rep_setup = getattr(item, "_cxas_rep_setup", None)
    status = "PASSED"
    dur_ms = 0.0
    err_summary = ""
    if rep_call is not None:
        dur_ms = float(getattr(rep_call, "duration", 0.0) or 0.0) * 1000.0
        if rep_call.failed:
            status = "FAILED"
            err_summary = str(rep_call.longreprtext or "")[:300]
        elif rep_call.skipped:
            status = "SKIPPED"
    elif rep_setup is not None and rep_setup.skipped:
        status = "SKIPPED"

    if collector.turn_traces:
        session_data["turn_traces"][item.nodeid] = list(collector.turn_traces)
    if collector.stage_traces:
        session_data["stage_traces"][item.nodeid] = list(collector.stage_traces)
    for idx, md in enumerate(collector.mode_diffs, 1):
        lbl = (
            f"{item.nodeid} ({md.stage})"
            if len(collector.mode_diffs) == 1
            else f"{item.nodeid} (#{idx}: {md.stage})"
        )
        session_data["mode_diffs"][lbl] = md

    session_data["test_results"].append(
        {
            "test_name": item.nodeid,
            "status": status,
            "duration_ms": round(dur_ms, 2),
            "error": err_summary,
        }
    )


@pytest.fixture
def cxas_trace_collector(request: pytest.FixtureRequest) -> CxasTraceCollector:
    """Fixture providing access to traces collected during the current test."""
    collector = getattr(request.node, "_cxas_collector", None)
    if isinstance(collector, CxasTraceCollector):
        return collector
    fallback = CxasTraceCollector(
        test_name=request.node.name,
        nodeid=request.node.nodeid,
    )
    return fallback


@pytest.fixture
def cxas_snapshot(request: pytest.FixtureRequest) -> SnapshotManager:
    """Fixture providing a `SnapshotManager` for `--cxas-update-snapshots`."""
    update_flag = bool(
        request.config.getoption("--cxas-update-snapshots", default=False)
    )
    return SnapshotManager(update_snapshots=update_flag)
