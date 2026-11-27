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
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from cxas_scrapi.evals.callback_chain.snapshots import SnapshotManager
    from cxas_scrapi.evals.callback_chain.traces import (
        CallbackTrace,
        ModeDiffReport,
        TurnTrace,
    )

_CHAIN_LLM_ENV = "CXAS_CALLBACK_LLM"
_CHAIN_IMAGE_VERSION_ENV = "CXAS_CALLBACK_IMAGE_VERSION"


@dataclasses.dataclass
class CxasTraceCollector:
    """Per-test trace collector exposed via `cxas_trace_collector` fixture.

    Active trace collectors are scoped via `contextvars.ContextVar`. When
    dispatching callback or turn execution to a `ThreadPoolExecutor` or
    background thread, use `submit_in_context(executor, fn, *args, **kwargs)`
    (or `contextvars.copy_context().run`) so traces attach deterministically
    to the current test's collector.
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
        (
            "--cxas-app-dir",
            {
                "action": "store",
                "default": None,
                "metavar": "DIR",
                "help": (
                    "Optional CES app directory for `callback_runner` and "
                    "`session_sim` fixtures."
                ),
            },
        ),
        (
            "--cxas-agent",
            {
                "action": "store",
                "default": None,
                "metavar": "AGENT",
                "help": (
                    "Optional agent directory/display name for "
                    "`callback_runner` and `session_sim` fixtures."
                ),
            },
        ),
        (
            "--cxas-image-version",
            {
                "action": "store",
                "default": None,
                "choices": ["v1", "v2"],
                "help": (
                    "Optional CES sandbox image version (`v1` or `v2`) for "
                    "`callback_runner` and `session_sim` fixtures."
                ),
            },
        ),
    ]
    for opt_name, kwargs in options:
        with contextlib.suppress(ValueError):
            # Option already registered (e.g., nested pytest invocation)
            group.addoption(opt_name, **kwargs)


def pytest_configure(config: pytest.Config) -> None:
    """Configures optional `--cxas-llm` environment override."""
    llm_opt = config.getoption("--cxas-llm", default=None)
    if llm_opt:
        config._cxas_prev_llm_env = os.environ.get(_CHAIN_LLM_ENV)  # type: ignore[attr-defined]
        if str(llm_opt).strip().lower() == "mock":
            os.environ.pop(_CHAIN_LLM_ENV, None)
        else:
            os.environ[_CHAIN_LLM_ENV] = str(llm_opt)


def pytest_unconfigure(config: pytest.Config) -> None:
    """Restores `CXAS_CALLBACK_LLM` if overridden by `--cxas-llm`."""
    if hasattr(config, "_cxas_prev_llm_env"):
        prev = config._cxas_prev_llm_env  # type: ignore[attr-defined]
        if prev is None:
            os.environ.pop(_CHAIN_LLM_ENV, None)
        else:
            os.environ[_CHAIN_LLM_ENV] = prev


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Pushes active trace collectors and snapshot context before each test."""
    if getattr(item, "_cxas_tokens", None) is not None:
        return
    from cxas_scrapi.evals.callback_chain.compare import (  # noqa: PLC0415
        _push_active_mode_diff_collector,
    )
    from cxas_scrapi.evals.callback_chain.runner import (  # noqa: PLC0415
        _push_active_stage_collector,
    )
    from cxas_scrapi.evals.callback_chain.snapshots import (  # noqa: PLC0415
        _ACTIVE_SNAPSHOT_CONTEXT,
        _reset_unnamed_snapshot_counts,
    )
    from cxas_scrapi.evals.callback_chain.turn import (  # noqa: PLC0415
        _push_active_turn_collector,
    )

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
    ) or os.environ.get("CXAS_UPDATE_SNAPSHOTS", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    ci_mode = bool(
        item.config.getoption("--cxas-snapshot-ci", default=False)
    ) or os.environ.get("CXAS_SNAPSHOT_CI", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )

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
def pytest_runtest_teardown(
    item: pytest.Item, nextitem: pytest.Item | None
) -> Any:
    """Pops active collectors after fixture finalizers complete."""
    del nextitem
    yield
    tokens = getattr(item, "_cxas_tokens", None)
    collector: CxasTraceCollector | None = getattr(
        item, "_cxas_collector", None
    )
    if tokens is not None:
        from cxas_scrapi.evals.callback_chain.compare import (  # noqa: PLC0415
            _pop_active_mode_diff_collector,
        )
        from cxas_scrapi.evals.callback_chain.runner import (  # noqa: PLC0415
            _pop_active_stage_collector,
        )
        from cxas_scrapi.evals.callback_chain.snapshots import (  # noqa: PLC0415
            _ACTIVE_SNAPSHOT_CONTEXT,
        )
        from cxas_scrapi.evals.callback_chain.turn import (  # noqa: PLC0415
            _pop_active_turn_collector,
        )

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
    """Fixture providing a `SnapshotManager` honoring `--cxas-update-snapshots`,
    `CXAS_UPDATE_SNAPSHOTS`, `--cxas-snapshot-ci`, and `CXAS_SNAPSHOT_CI`.
    """
    from cxas_scrapi.evals.callback_chain.snapshots import (  # noqa: PLC0415
        SnapshotManager,
    )

    update_flag = bool(
        request.config.getoption("--cxas-update-snapshots", default=False)
    )
    ci_flag = bool(
        request.config.getoption("--cxas-snapshot-ci", default=False)
    )
    return SnapshotManager(
        update_snapshots=True if update_flag else None,
        ci_mode=True if ci_flag else None,
    )


@pytest.fixture
def snapshot_manager(cxas_snapshot: SnapshotManager) -> SnapshotManager:
    """Alias fixture for `cxas_snapshot`."""
    return cxas_snapshot


@pytest.fixture
def expect() -> Any:
    """Fixture returning the unified Jest-style `expect` entrypoint."""
    from cxas_scrapi.evals.callback_chain.expect import (  # noqa: PLC0415
        expect as _expect_fn,
    )

    return _expect_fn


@pytest.fixture
def mock_llm() -> Any:
    """Fixture returning a fresh `MockLlm` builder instance per test."""
    from cxas_scrapi.evals.callback_chain.mocks import MockLlm  # noqa: PLC0415

    return MockLlm()


@pytest.fixture
def tool_spy() -> Any:
    """Fixture returning a fresh `ToolSpy` spy/mock instance per test."""
    from cxas_scrapi.evals.callback_chain.mocks import ToolSpy  # noqa: PLC0415

    return ToolSpy()


def _infer_app_and_agent_dirs(
    request: pytest.FixtureRequest,
) -> tuple[Path | None, Path | None, str | None]:
    """Infers `(app_dir, agent_dir, agent_name)` from CLI options, env vars, or
    enclosing test directory layout.
    """
    opt_app = request.config.getoption("--cxas-app-dir", default=None) or (
        os.environ.get("CXAS_CALLBACK_APP_DIR")
    )
    opt_agent = request.config.getoption("--cxas-agent", default=None) or (
        os.environ.get("CXAS_CALLBACK_AGENT")
    )
    if opt_app:
        ap = Path(str(opt_app)).resolve()
        ag = (ap / "agents" / str(opt_agent)) if opt_agent else None
        return (
            ap,
            (ag if ag and ag.is_dir() else None),
            (str(opt_agent) if opt_agent else None),
        )

    orig_file = os.environ.get("CXAS_ORIGINAL_TEST_FILE")
    start = (
        Path(orig_file).resolve()
        if orig_file
        else Path(str(request.node.fspath)).resolve()
    )
    from cxas_scrapi.evals.callback_chain.semantics import (  # noqa: PLC0415
        find_app_config_path,
    )

    curr = start.parent
    for _ in range(6):
        if curr.parent.name == "agents" and (
            find_app_config_path(curr.parent.parent) is not None
            or (curr / f"{curr.name}.json").is_file()
        ):
            return curr.parent.parent, curr, curr.name
        if (
            find_app_config_path(curr) is not None
            and (curr / "agents").is_dir()
        ):
            return curr, None, (str(opt_agent) if opt_agent else None)
        if curr.parent == curr:
            break
        curr = curr.parent
    return None, None, (str(opt_agent) if opt_agent else None)


def _resolve_fixture_image_version(request: pytest.FixtureRequest) -> str:
    opt_img = request.config.getoption(
        "--cxas-image-version", default=None
    ) or os.environ.get(_CHAIN_IMAGE_VERSION_ENV)
    if opt_img and str(opt_img).strip() in ("v1", "v2"):
        return str(opt_img).strip()
    return "v1"


@pytest.fixture
def callback_runner(request: pytest.FixtureRequest) -> Any:
    """Fixture returning a `CallbackChainRunner` loaded from `--cxas-app-dir` /
    enclosing agent directory (or an empty in-memory runner when standalone).
    """
    from cxas_scrapi.evals.callback_chain.runner import (  # noqa: PLC0415
        CallbackChainRunner,
    )

    img_ver: Any = _resolve_fixture_image_version(request)
    ap_dir, ag_dir, ag_name = _infer_app_and_agent_dirs(request)
    if ag_dir is not None:
        return CallbackChainRunner.from_agent_dir(
            ag_dir, app_dir=ap_dir, image_version=img_ver
        )
    if ap_dir is not None:
        return CallbackChainRunner.from_app_dir(
            ap_dir, agent_name=ag_name, image_version=img_ver
        )
    return CallbackChainRunner.from_callbacks({}, image_version=img_ver)


@pytest.fixture
def session_sim(request: pytest.FixtureRequest, callback_runner: Any) -> Any:
    """Fixture returning a `SessionSimulator` bound to `--cxas-app-dir` or
    `callback_runner`.
    """
    from cxas_scrapi.evals.callback_chain.turn import (  # noqa: PLC0415
        SessionSimulator,
    )

    img_ver: Any = _resolve_fixture_image_version(request)
    ap_dir, _, ag_name = _infer_app_and_agent_dirs(request)
    if ap_dir is not None and (ap_dir / "agents").is_dir():
        return SessionSimulator.from_app_dir(
            ap_dir, root_agent=ag_name, image_version=img_ver
        )
    return SessionSimulator(callback_runner, image_version=img_ver)
