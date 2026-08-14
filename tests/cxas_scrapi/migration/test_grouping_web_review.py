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


"""Tests for the HTML grouping confirmation gate.

Exercises the full coroutine end-to-end with the live stdlib HTTP server
running on an ephemeral port; talks to it via :mod:`urllib.request`.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import typing
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any, NoReturn
from unittest.mock import MagicMock

from cxas_scrapi.migration import grouping_web_review
from cxas_scrapi.migration.analysis_reporter import MigrationAnalysisBuilder
from cxas_scrapi.migration.data_models import (
    IRAgent,
    IRMetadata,
    MigrationIR,
)

if TYPE_CHECKING:
    from pathlib import Path


def _ir() -> MigrationIR:
    return MigrationIR(
        metadata=IRMetadata(app_name="t"),
        tools={},
        agents={
            "FlowA": IRAgent(
                type="PLAYBOOK", display_name="FlowA", instruction=""
            ),
            "FlowB": IRAgent(
                type="PLAYBOOK", display_name="FlowB", instruction=""
            ),
            "FlowC": IRAgent(
                type="PLAYBOOK", display_name="FlowC", instruction=""
            ),
        },
    )


def _initial_groupings() -> dict[str, Any]:
    return {
        "RootAgent": {
            "agents": ["FlowA", "FlowB"],
            "is_root": True,
            "rationale": "primary",
            "journey": "",
        },
        "Helpers": {
            "agents": ["FlowC"],
            "is_root": False,
            "rationale": "side flows",
            "journey": "",
        },
    }


class _FakeConsolidator:
    """Stand-in for StructuralConsolidator used by /api/repropose."""

    def __init__(self) -> None:
        self.repropose_calls: list[str | None] = []

    async def propose_groupings(
        self,
        root_key: typing.Any = None,
        dep_summary: typing.Any = None,
        feedback: typing.Any = None,
    ) -> typing.Any:
        self.repropose_calls.append(feedback)
        # Simulate a new proposal where every flow goes to AllInOne.
        return {
            "AllInOne": {
                "agents": ["FlowA", "FlowB", "FlowC"],
                "is_root": True,
                "rationale": f"re-proposed (feedback: {feedback})",
                "journey": "",
            }
        }


def _make_builder(tmp_path: Path) -> MigrationAnalysisBuilder:
    b = MigrationAnalysisBuilder("demo", "Demo", output_dir=tmp_path)
    # Render an initial HTML so the GET /review handler has something to
    # read off disk.
    b.flush()
    return b


def _http_get(url: str, *, timeout: float = 3.0) -> tuple[int, bytes]:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def _http_post(
    url: str, body: dict[str, Any], *, timeout: float = 5.0
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, json.loads(body) if body else {}


def _wait_for_server(host: str, port: int, *, timeout: float = 5.0) -> str:
    base = f"http://{host}:{port}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _http_get(f"{base}/api/status", timeout=0.5)
            return base
        except (urllib.error.URLError, OSError):
            time.sleep(0.05)
    raise RuntimeError(f"server never came up on {base}")


def _start_web_review(
    *,
    builder: MigrationAnalysisBuilder,
    timeout_s: int = 30,
    consolidator: Any | None = None,
) -> tuple[asyncio.Task, asyncio.AbstractEventLoop, threading.Thread, dict]:
    """Run web_review in a background asyncio loop on its own thread.
    Returns (task, loop, thread, shared_state) where shared_state has
    'result' populated when the coroutine completes."""
    shared: dict[str, Any] = {"result": None, "exc": None}

    def _run() -> None:
        loop = asyncio.new_event_loop()
        shared["loop"] = loop
        asyncio.set_event_loop(loop)

        async def _go() -> None:
            try:
                shared["result"] = await grouping_web_review.web_review(
                    ir=_ir(),
                    groupings=_initial_groupings(),
                    consolidator=consolidator or _FakeConsolidator(),
                    root_key="RootAgent",
                    dep_summary={},
                    builder=builder,
                    bind_host="127.0.0.1",
                    bind_port=0,
                    timeout_s=timeout_s,
                    auto_open_browser=False,
                )
            except Exception as exc:  # noqa: BLE001
                shared["exc"] = exc

        loop.run_until_complete(_go())
        loop.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    # Wait until the loop attribute is set.
    deadline = time.time() + 5.0
    while "loop" not in shared and time.time() < deadline:
        time.sleep(0.01)
    return shared.get("task"), shared.get("loop"), thread, shared


def _discover_port(builder: MigrationAnalysisBuilder, shared: dict) -> int:
    """The server logs the URL via builder.console but we need the bound
    port. Wait briefly for the snapshot to gain session_id and probe ports
    starting from a likely range — simpler: read the published HTML and find
    the injected endpoint. Even simpler: poll a small port range with a
    quick HEAD. Cleanest: get it from the test setup. We expose it via a
    test hook below."""
    raise NotImplementedError  # not used — we patch _free_port instead.


# --- Integration tests -------------------------------------------------------


def test_apply_grouping_confirms_and_writes_plan(
    tmp_path: typing.Any, monkeypatch: typing.Any
) -> None:
    """Happy path: POST a valid grouping → server returns ok, plan file
    written, coroutine returns the edited groupings."""
    # Force a known port so we can talk to the server without sniffing.
    fixed_port = 18745
    monkeypatch.setattr(grouping_web_review, "_free_port", lambda: fixed_port)
    b = _make_builder(tmp_path)
    _, _loop, thread, shared = _start_web_review(builder=b)
    base = _wait_for_server("127.0.0.1", fixed_port)

    # Snapshot reflects the initial proposal + session id.
    status, body = _http_get(f"{base}/api/grouping")
    assert status == 200
    snapshot = json.loads(body.decode("utf-8"))
    assert snapshot["status"] == "awaiting_confirmation"
    assert "RootAgent" in snapshot["groupings"]
    assert snapshot["all_flow_names"] == ["FlowA", "FlowB", "FlowC"]

    # User edits: move FlowC into RootAgent, drop Helpers.
    edited = {
        "RootAgent": {
            "agents": ["FlowA", "FlowB", "FlowC"],
            "is_root": True,
            "rationale": "all",
            "journey": "",
        }
    }
    status, body = _http_post(f"{base}/api/grouping", {"groupings": edited})
    assert status == 200
    assert body == {"ok": True}

    thread.join(timeout=5)
    assert shared["exc"] is None
    assert shared["result"] == edited
    plan_path = tmp_path / "demo_grouping_plan.json"
    assert plan_path.exists()
    assert json.loads(plan_path.read_text())["RootAgent"]["is_root"] is True


def test_apply_grouping_auto_allocates_orphans(
    tmp_path: typing.Any, monkeypatch: typing.Any
) -> None:
    """POST unassigned flows -> auto-allocates orphans to root group."""
    fixed_port = 18746
    monkeypatch.setattr(grouping_web_review, "_free_port", lambda: fixed_port)
    b = _make_builder(tmp_path)
    _, _, thread, shared = _start_web_review(builder=b)
    base = _wait_for_server("127.0.0.1", fixed_port)

    # Missing FlowC → auto-assigned to RootAgent by validate_groupings.
    partial = {
        "RootAgent": {
            "agents": ["FlowA", "FlowB"],
            "is_root": True,
            "rationale": "",
            "journey": "",
        }
    }
    status, body = _http_post(f"{base}/api/grouping", {"groupings": partial})
    assert status == 200
    assert body == {"ok": True}
    thread.join(timeout=5)
    assert shared["result"]["RootAgent"]["agents"] == [
        "FlowA",
        "FlowB",
        "FlowC",
    ]


def test_abort_returns_none(
    tmp_path: typing.Any, monkeypatch: typing.Any
) -> None:
    fixed_port = 18747
    monkeypatch.setattr(grouping_web_review, "_free_port", lambda: fixed_port)
    b = _make_builder(tmp_path)
    _, _, thread, shared = _start_web_review(builder=b)
    base = _wait_for_server("127.0.0.1", fixed_port)

    status, body = _http_post(f"{base}/api/abort", {})
    assert status == 200
    assert body == {"ok": True}
    thread.join(timeout=5)
    assert shared["result"] is None
    assert b.snapshot.pending_grouping["status"] == "aborted"


def test_repropose_returns_new_proposal(
    tmp_path: typing.Any, monkeypatch: typing.Any
) -> None:
    fixed_port = 18748
    monkeypatch.setattr(grouping_web_review, "_free_port", lambda: fixed_port)
    b = _make_builder(tmp_path)
    fake_consolidator = _FakeConsolidator()
    _, _, thread, shared = _start_web_review(
        builder=b, consolidator=fake_consolidator
    )
    base = _wait_for_server("127.0.0.1", fixed_port)

    status, body = _http_post(
        f"{base}/api/repropose", {"feedback": "split FlowC out"}
    )
    assert status == 200
    assert body["ok"] is True
    assert "AllInOne" in body["pending"]["groupings"]
    assert fake_consolidator.repropose_calls == ["split FlowC out"]
    # Status now back to awaiting after the new proposal lands.
    assert body["pending"]["status"] == "awaiting_confirmation"

    # Confirm the new proposal.
    new_groupings = body["pending"]["groupings"]
    status, _ = _http_post(f"{base}/api/grouping", {"groupings": new_groupings})
    assert status == 200
    thread.join(timeout=5)
    assert shared["result"] == new_groupings


def test_get_review_serves_html_with_injected_endpoint(
    tmp_path: typing.Any, monkeypatch: typing.Any
) -> None:
    fixed_port = 18749
    monkeypatch.setattr(grouping_web_review, "_free_port", lambda: fixed_port)
    b = _make_builder(tmp_path)
    _, _, thread, _shared = _start_web_review(builder=b)
    base = _wait_for_server("127.0.0.1", fixed_port)

    status, body = _http_get(f"{base}/review")
    assert status == 200
    html = body.decode("utf-8")
    assert "__REVIEW_ENDPOINT__" in html
    assert "panel-grouping" in html

    _http_post(f"{base}/api/abort", {})
    thread.join(timeout=5)


def test_timeout_auto_confirms_proposal(
    tmp_path: typing.Any, monkeypatch: typing.Any
) -> None:
    fixed_port = 18750
    monkeypatch.setattr(grouping_web_review, "_free_port", lambda: fixed_port)
    b = _make_builder(tmp_path)
    # Very short timeout so the test completes quickly.
    shared: dict[str, Any] = {"result": "sentinel", "exc": None}

    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _go() -> None:
            try:
                shared["result"] = await grouping_web_review.web_review(
                    ir=_ir(),
                    groupings=_initial_groupings(),
                    consolidator=_FakeConsolidator(),
                    root_key="RootAgent",
                    dep_summary={},
                    builder=b,
                    bind_host="127.0.0.1",
                    bind_port=0,
                    timeout_s=1,  # tiny
                    auto_open_browser=False,
                )
            except Exception as exc:  # noqa: BLE001
                shared["exc"] = exc

        loop.run_until_complete(_go())
        loop.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=10)
    assert shared["exc"] is None
    assert shared["result"] == _initial_groupings()
    assert b.snapshot.pending_grouping["status"] == "auto_confirmed_timeout"


def test_save_xprs_config_writes_yaml_file(
    tmp_path: typing.Any, monkeypatch: typing.Any
) -> None:
    fixed_port = 18753

    monkeypatch.setattr(grouping_web_review, "_free_port", lambda: fixed_port)
    b = _make_builder(tmp_path)
    _, _, thread, _shared = _start_web_review(builder=b)
    base = _wait_for_server("127.0.0.1", fixed_port)

    yaml_payload = "xprs_version: 1.0\nconversation_graph: []"
    bubbles = [{"id": "b1", "text": "hello", "speaker": "user", "active": True}]

    status, body = _http_post(
        f"{base}/api/xprs/save", {"yaml": yaml_payload, "bubbles": bubbles}
    )
    assert status == 200
    assert body == {"ok": True}

    yaml_path = tmp_path / "demo_xprs_config.yaml"
    assert yaml_path.exists()
    assert yaml_path.read_text(encoding="utf-8") == yaml_payload

    _http_post(f"{base}/api/abort", {})
    thread.join(timeout=5)


def test_get_root_redirects_to_review(
    tmp_path: typing.Any, monkeypatch: typing.Any
) -> None:
    fixed_port = 18751

    monkeypatch.setattr(grouping_web_review, "_free_port", lambda: fixed_port)
    b = _make_builder(tmp_path)
    _, _, thread, _shared = _start_web_review(builder=b)
    base = _wait_for_server("127.0.0.1", fixed_port)

    req = urllib.request.Request(f"{base}/", method="GET")
    # urlopen follows redirects by default; check final URL.
    with urllib.request.urlopen(req, timeout=3) as resp:
        assert resp.geturl().endswith("/review")

    _http_post(f"{base}/api/abort", {})
    thread.join(timeout=5)


# --- Phase 6: file-watch fallback -------------------------------------------


def test_file_watch_applies_valid_edits(
    tmp_path: typing.Any, monkeypatch: typing.Any
) -> None:
    """Editing <target>_grouping_plan.json and saving resolves the gate
    via the same code path as POST /api/grouping."""
    fixed_port = 18752
    monkeypatch.setattr(grouping_web_review, "_free_port", lambda: fixed_port)
    b = _make_builder(tmp_path)
    _, _, thread, shared = _start_web_review(builder=b)
    _wait_for_server("127.0.0.1", fixed_port)

    plan_path = tmp_path / "demo_grouping_plan.json"
    # Wait until the pre-seed write lands.
    deadline = time.time() + 5.0
    while not plan_path.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert plan_path.exists()

    # Bump mtime far enough into the future that polling notices on
    # filesystems with second-granularity mtime.
    edited = {
        "AllInOne": {
            "agents": ["FlowA", "FlowB", "FlowC"],
            "is_root": True,
            "rationale": "edited via file",
            "journey": "",
        }
    }
    plan_path.write_text(json.dumps(edited, indent=2), encoding="utf-8")
    # Force mtime forward so the watcher sees the change even on
    # second-granularity filesystems.
    new_mtime = time.time() + 2
    os.utime(plan_path, (new_mtime, new_mtime))

    thread.join(timeout=10)
    assert shared["exc"] is None
    assert shared["result"] == edited


def test_file_watch_ignores_invalid_json(
    tmp_path: typing.Any, monkeypatch: typing.Any
) -> None:
    """Invalid JSON written to the plan file does NOT resolve the gate."""
    fixed_port = 18765
    monkeypatch.setattr(grouping_web_review, "_free_port", lambda: fixed_port)
    b = _make_builder(tmp_path)
    _, _, thread, _shared = _start_web_review(builder=b)
    base = _wait_for_server("127.0.0.1", fixed_port)

    plan_path = tmp_path / "demo_grouping_plan.json"
    deadline = time.time() + 5.0
    while not plan_path.exists() and time.time() < deadline:
        time.sleep(0.05)

    plan_path.write_text("not json{{", encoding="utf-8")
    os.utime(plan_path, (time.time() + 2, time.time() + 2))

    # Watcher should log a warning but keep waiting. Give it time to poll.
    time.sleep(2)
    assert thread.is_alive()

    # Tidy up via abort.
    _http_post(f"{base}/api/abort", {})
    thread.join(timeout=5)
    assert _shared["result"] is None


def test_apply_grouping_noop_if_already_resolved(
    tmp_path: typing.Any, monkeypatch: typing.Any
) -> None:
    """If the gate is already resolved (event is set), apply_grouping should
    exit immediately as a no-op without running validations or file I/O."""
    ir = MagicMock()
    groupings = {"Group": {"agents": ["FlowA"]}}
    consolidator = MagicMock()
    builder = MagicMock()
    plan_path = tmp_path / "dummy_plan.json"
    loop = MagicMock()
    event = MagicMock()
    result = {}
    console = MagicMock()

    ctx = grouping_web_review._ReviewContext(
        ir=ir,
        groupings=groupings,
        consolidator=consolidator,
        root_key="Root",
        dep_summary={},
        builder=builder,
        plan_path=plan_path,
        loop=loop,
        event=event,
        result=result,
        console=console,
    )

    # Mock validate_groupings to raise an exception if it gets called
    def fail_if_called(*args: typing.Any, **kwargs: typing.Any) -> NoReturn:
        raise AssertionError("validate_groupings should not have been called!")

    monkeypatch.setattr(
        grouping_web_review.structural_consolidator,
        "validate_groupings",
        fail_if_called,
    )

    # Test Pathway 1: early exit via event.is_set() outside the lock
    event.is_set.return_value = True
    errors = ctx.apply_grouping(groupings)
    assert errors == []

    # Test Pathway 2: early exit via self.resolved inside the lock (even if
    # event.is_set() is False)
    event.is_set.return_value = False
    ctx.resolved = True
    errors2 = ctx.apply_grouping(groupings)
    assert errors2 == []


def test_all_flow_names_includes_bracketed_names() -> None:
    """_all_flow_names returns both IRAgent dict key and display_name."""
    ir = MigrationIR(
        metadata=IRMetadata(app_name="t"),
        tools={},
        agents={
            "[Core] Conf Routing": IRAgent(
                type="FLOW",
                display_name="Core Conf Routing",
                instruction="",
            ),
        },
    )
    names = grouping_web_review._all_flow_names(ir)
    assert "[Core] Conf Routing" in names
    assert "Core Conf Routing" in names
