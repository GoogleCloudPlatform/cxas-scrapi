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

"""HTML-driven grouping confirmation gate.

Boots a local HTTP server in a daemon thread that serves the migration
analysis report with the Grouping Review tab pre-active. Coordinates
between the browser (via JSON POST to /api/grouping) and the awaiting
async pipeline via an asyncio.Event.

Used by ``MigrationService._run_stage1_consolidation`` as the default
``grouping_callback`` when ``config.web_confirm_grouping`` is True.

Implementation note: the design plan calls for aiohttp; we use stdlib
``http.server`` + a daemon thread instead so this module ships without
a new dependency. The contract surface (async coroutine, JSON in/out,
asyncio.Event coordination) is identical.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import logging
import socket
import threading
import uuid
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console

from cxas_scrapi.migration import structural_consolidator

if TYPE_CHECKING:
    from cxas_scrapi.migration.analysis_reporter import (
        MigrationAnalysisBuilder,
    )
    from cxas_scrapi.migration.data_models import MigrationIR
    from cxas_scrapi.migration.structural_consolidator import (
        StructuralConsolidator,
    )

logger = logging.getLogger(__name__)


def _all_flow_names(ir: MigrationIR) -> list[str]:
    """Stable list of the source flow / member display names visible to
    the consolidator. Used to drive client-side orphan detection."""
    return sorted({a.display_name for a in ir.agents.values()})


def _build_pending(
    groupings: dict[str, Any],
    *,
    ir: MigrationIR,
    root_key: str | None,
    dep_summary: dict | None,
    session_id: str,
    status: str = "awaiting_confirmation",
) -> dict[str, Any]:
    return {
        "groupings": groupings,
        "all_flow_names": _all_flow_names(ir),
        "root_key": root_key,
        "dep_summary": dep_summary or {},
        "status": status,
        "session_id": session_id,
    }


class _ReviewContext:
    """Mutable state shared between the asyncio coroutine and the HTTP
    server thread. Methods are thread-safe via ``self._lock``."""

    def __init__(
        self,
        *,
        ir: MigrationIR,
        builder: MigrationAnalysisBuilder,
        plan_path: Path,
        loop: asyncio.AbstractEventLoop,
        event: asyncio.Event,
        result: dict[str, Any],
        console: Console,
        groupings: dict[str, Any] | None = None,
        consolidator: StructuralConsolidator | None = None,
        root_key: str | None = None,
        dep_summary: dict | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self.resolved = False
        self.ir = ir
        self.builder = builder
        self.plan_path = plan_path
        self.loop = loop
        self.event = event
        self.result = result
        self.console = console
        self.session_id = uuid.uuid4().hex
        self.consolidator = consolidator
        self.root_key = root_key
        self.dep_summary = dep_summary or {}
        self.server: Any = None
        self.review_url: str = ""

        if groupings is not None:
            self.pending: dict[str, Any] = _build_pending(
                groupings,
                ir=ir,
                root_key=root_key,
                dep_summary=dep_summary,
                session_id=self.session_id,
            )
        else:
            self.pending = {
                "status": "analyzing",
                "groupings": {},
                "all_flow_names": _all_flow_names(ir),
                "root_key": None,
                "dep_summary": {},
                "session_id": self.session_id,
            }
        self._sync_snapshot()

    def update(
        self,
        *,
        groupings: dict[str, Any],
        consolidator: StructuralConsolidator,
        root_key: str | None,
        dep_summary: dict | None,
    ) -> None:
        """Update the context with active proposals and consolidator."""
        with self._lock:
            self.consolidator = consolidator
            self.root_key = root_key
            self.dep_summary = dep_summary or {}
            self.pending = _build_pending(
                groupings,
                ir=self.ir,
                root_key=root_key,
                dep_summary=dep_summary,
                session_id=self.session_id,
            )
            # Switch status to awaiting confirmation
            self.pending["status"] = "awaiting_confirmation"
        self._sync_snapshot()

    def _sync_snapshot(self) -> None:
        """Push the current pending payload into the report snapshot."""
        try:
            snap = self.snapshot()  # Thread-safe copy taken under lock!
            self.builder.snapshot.pending_grouping = snap
            self.builder.flush()
        except Exception as exc:  # noqa: BLE001
            logger.warning("analysis snapshot flush failed: %s", exc)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            # Defensive copy so callers can't mutate our state.
            return json.loads(json.dumps(self.pending))

    def set_status(self, status: str) -> None:
        with self._lock:
            self.pending["status"] = status
        self._sync_snapshot()

    def apply_grouping(self, new_groupings: dict[str, Any]) -> list[str]:
        """Validate the user-edited grouping. On success, persist the plan,
        resolve the asyncio Event, and return []. On failure, return a list
        of error strings without resolving."""
        if self.resolved or self.event.is_set():
            return []
        try:
            structural_consolidator.validate_groupings(
                self.ir, new_groupings, self.root_key
            )
        except Exception as exc:  # noqa: BLE001
            return [str(exc)]

        with self._lock:
            if self.resolved:
                return []
            self.resolved = True
            self.pending["groupings"] = new_groupings
            self.pending["status"] = "confirmed"
            self.result["groupings"] = new_groupings
            self.result["aborted"] = False

        # Run I/O and signaling outside the lock to prevent deadlocks on the
        # event loop!
        try:
            structural_consolidator.persist_grouping(
                new_groupings, str(self.plan_path)
            )
        except Exception as exc:  # noqa: BLE001
            return [f"failed to persist plan: {exc}"]
        self._sync_snapshot()
        self.loop.call_soon_threadsafe(self.event.set)
        return []

    def abort(self) -> None:
        with self._lock:
            if self.resolved:
                return
            self.resolved = True
            self.pending["status"] = "aborted"
            self.result["groupings"] = None
            self.result["aborted"] = True
        self._sync_snapshot()
        self.loop.call_soon_threadsafe(self.event.set)

    def repropose(self, feedback: str | None) -> dict[str, Any]:
        """Block (on the server thread) waiting for Gemini to return a new
        proposal scheduled on the asyncio loop. Returns the new pending
        payload or raises on failure."""
        self.set_status("reproposing")

        async def _ask():
            return await self.consolidator.propose_groupings(
                root_key=self.root_key,
                dep_summary=self.dep_summary,
                feedback=feedback,
            )

        fut = asyncio.run_coroutine_threadsafe(_ask(), self.loop)
        new_groupings = fut.result(timeout=120)
        new_session = uuid.uuid4().hex
        with self._lock:
            self.session_id = new_session
            self.pending = _build_pending(
                new_groupings,
                ir=self.ir,
                root_key=self.root_key,
                dep_summary=self.dep_summary,
                session_id=new_session,
            )
        self._sync_snapshot()
        return self.snapshot()


def _make_handler(
    ctx: _ReviewContext,
    *,
    review_url: str,
):
    """Build a BaseHTTPRequestHandler class bound to ``ctx``."""

    class Handler(http.server.BaseHTTPRequestHandler):
        # Quiet the noisy default access log; route through our logger.
        def log_message(self, format, *args) -> None:  # noqa: A002
            logger.debug("[grouping-review] %s", format % args)

        # ----- helpers ---------------------------------------------------

        def _respond_json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _respond_html(self, html: str, status: int = 200) -> None:
            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> Any:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8") or "{}")

        # ----- routing ---------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802
            try:
                if self.path in {"/", ""}:
                    self.send_response(302)
                    self.send_header("Location", "/review")
                    self.end_headers()
                    return
                if self.path == "/review":
                    html = ctx.builder.html_path.read_text(encoding="utf-8")
                    # Inject the live review endpoint so the JS enables
                    # Confirm/Abort/Re-propose. The bootstrap script must
                    # run before the existing inline script does its read.
                    inject = (
                        f"<script>window.__REVIEW_ENDPOINT__ = "
                        f"{json.dumps(review_url)};</script>"
                    )
                    html = html.replace("</head>", inject + "</head>", 1)
                    self._respond_html(html)
                    return
                if self.path == "/api/grouping":
                    self._respond_json(ctx.snapshot())
                    return
                if self.path == "/api/status":
                    self._respond_json({"status": ctx.snapshot()["status"]})
                    return
                if self.path == "/api/report_data":
                    if ctx.builder.json_path.exists():
                        self._respond_json(
                            json.loads(
                                ctx.builder.json_path.read_text(
                                    encoding="utf-8"
                                )
                            )
                        )
                    else:
                        self._respond_json(ctx.builder.snapshot.to_dict())
                    return
                self._respond_json({"error": "not found"}, status=404)
            except Exception as exc:  # noqa: BLE001
                logger.exception("GET %s failed", self.path)
                self._respond_json({"error": str(exc)}, status=500)

        def do_POST(self) -> None:  # noqa: N802
            try:
                if self.path == "/api/grouping":
                    payload = self._read_json()
                    new_g = payload.get("groupings")
                    if not isinstance(new_g, dict):
                        self._respond_json(
                            {
                                "ok": False,
                                "errors": ["body.groupings must be an object"],
                            },
                            status=400,
                        )
                        return
                    errors = ctx.apply_grouping(new_g)
                    if errors:
                        self._respond_json(
                            {"ok": False, "errors": errors}, status=400
                        )
                        return
                    self._respond_json({"ok": True})
                    return
                if self.path == "/api/repropose":
                    payload = self._read_json()
                    feedback = payload.get("feedback") or None
                    try:
                        new_pending = ctx.repropose(feedback)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("re-propose failed: %s", exc)
                        self._respond_json(
                            {"ok": False, "error": str(exc)}, status=502
                        )
                        return
                    self._respond_json({"ok": True, "pending": new_pending})
                    return
                if self.path == "/api/xprs/save":
                    payload = self._read_json()
                    yaml_content = payload.get("yaml")
                    bubbles_list = payload.get("bubbles")
                    if not yaml_content or not isinstance(bubbles_list, list):
                        self._respond_json(
                            {
                                "ok": False,
                                "error": (
                                    "body must contain yaml and bubbles list"
                                ),
                            },
                            status=400,
                        )
                        return

                    current_data = (
                        getattr(ctx.ir, "xprs_designer_data", {}) or {}
                    )
                    ctx.ir.xprs_designer_data = {
                        "raw": current_data.get("raw", []),
                        "categorized": current_data.get("categorized", {}),
                        "rationales": current_data.get("rationales", {}),
                        "canvas_bubbles": bubbles_list,
                        "compiled_yaml": yaml_content,
                    }

                    ctx.builder.snapshot.xprs_designer_data = (
                        ctx.ir.xprs_designer_data
                    )

                    from pathlib import Path  # noqa: PLC0415

                    xprs_yaml_path = (
                        Path(ctx.builder.output_dir).resolve()
                        / f"{ctx.builder.target_name}_xprs_config.yaml"
                    )
                    xprs_yaml_path.parent.mkdir(parents=True, exist_ok=True)
                    xprs_yaml_path.write_text(yaml_content, encoding="utf-8")
                    logger.info(
                        "Persisted compiled xprs config to: %s", xprs_yaml_path
                    )

                    ctx._sync_snapshot()
                    self._respond_json({"ok": True})
                    return
                if self.path == "/api/abort":
                    ctx.abort()
                    self._respond_json({"ok": True})
                    return
                self._respond_json({"error": "not found"}, status=404)

            except Exception as exc:  # noqa: BLE001
                logger.exception("POST %s failed", self.path)
                self._respond_json({"error": str(exc)}, status=500)

    return Handler


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _watch_plan_file(
    ctx: _ReviewContext, *, poll_interval_s: float = 1.0
) -> None:
    """Background task: poll plan_path's mtime and apply on change.

    Converges with the POST /api/grouping path via ctx.apply_grouping —
    whichever fires first wins; the other becomes a no-op once the
    asyncio.Event is set.
    """
    try:
        baseline = ctx.plan_path.stat().st_mtime
    except OSError:
        baseline = 0.0
    while not ctx.event.is_set():
        await asyncio.sleep(poll_interval_s)
        if ctx.event.is_set():
            return
        try:
            mtime = ctx.plan_path.stat().st_mtime
        except OSError:
            continue
        if mtime <= baseline:
            continue
        baseline = mtime
        try:
            text = ctx.plan_path.read_text(encoding="utf-8")
            parsed = json.loads(text)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "[grouping-review] file-watch: %s is not valid JSON: %s",
                ctx.plan_path,
                exc,
            )
            continue
        if not isinstance(parsed, dict):
            logger.warning(
                "[grouping-review] file-watch: %s does not contain a"
                " grouping dict; ignoring.",
                ctx.plan_path,
            )
            continue
        errors = ctx.apply_grouping(parsed)
        if errors:
            logger.warning(
                "[grouping-review] file-watch: edits to %s failed"
                " validation: %s",
                ctx.plan_path,
                "; ".join(errors),
            )
            continue
        ctx.console.print(
            f"[cyan][grouping-review] file-watch: detected change to"
            f" {ctx.plan_path.name}; consolidation resuming.[/]"
        )
        return


async def boot_review_server(
    *,
    ir: MigrationIR,
    builder: MigrationAnalysisBuilder,
    bind_host: str = "127.0.0.1",
    bind_port: int = 0,
    timeout_s: int = 1800,
    auto_open_browser: bool = True,
    plan_path: Path | str | None = None,
    console: Console | None = None,
) -> _ReviewContext:
    """Start the review server early in the background. Does not block."""
    console = console or Console()
    loop = asyncio.get_running_loop()
    event = asyncio.Event()
    result: dict[str, Any] = {"groupings": None, "aborted": False}

    target_name = getattr(builder, "target_name", "migration")
    if plan_path is None:
        plan_path = (
            Path(builder.output_dir) / f"{target_name}_grouping_plan.json"
        )
    plan_path = Path(plan_path)

    ctx = _ReviewContext(
        ir=ir,
        builder=builder,
        plan_path=plan_path,
        loop=loop,
        event=event,
        result=result,
        console=console,
    )

    # Bind the server.
    port = bind_port if bind_port else _free_port()
    review_url = f"http://{bind_host}:{port}/review"
    handler_cls = _make_handler(ctx, review_url=review_url)
    server = http.server.ThreadingHTTPServer((bind_host, port), handler_cls)
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever, name="grouping-review-server", daemon=True
    )
    thread.start()

    ctx.server = server
    ctx.review_url = review_url

    banner = (
        "\n" + "=" * 80 + "\n"
        "🎉 MIGRATION PROGRESS DASHBOARD IS LIVE!\n\n"
        "You can monitor the migration and optimization progress live here:\n"
        f"[bold cyan]{review_url}[/]\n\n"
        "👉 Once Stage 1 variable deduplication completes, this page will\n"
        "   automatically unlock the interactive Grouping Review editor where\n"
        "   you can customize the agent consolidation layout.\n"
        + "=" * 80
        + "\n"
    )
    console.print(banner)

    if auto_open_browser:
        try:
            webbrowser.open_new_tab(review_url)
        except Exception as exc:  # noqa: BLE001
            logger.debug("could not auto-open browser: %s", exc)

    return ctx


async def web_review(
    *,
    ir: MigrationIR,
    groupings: dict[str, Any],
    consolidator: StructuralConsolidator,
    root_key: str | None = None,
    dep_summary: dict | None = None,
    builder: MigrationAnalysisBuilder,
    bind_host: str = "127.0.0.1",
    bind_port: int = 0,
    timeout_s: int = 1800,
    auto_open_browser: bool = True,
    plan_path: Path | str | None = None,
    console: Console | None = None,
    active_context: _ReviewContext | None = None,
) -> dict | None:
    """Block the pipeline until the user confirms a grouping in the browser.

    If active_context is provided, reuses the running server;
    otherwise boots a new one.
    """
    console = console or Console()

    if active_context is not None:
        ctx = active_context
        ctx.update(
            groupings=groupings,
            consolidator=consolidator,
            root_key=root_key,
            dep_summary=dep_summary,
        )
        review_url = ctx.review_url
        plan_path = ctx.plan_path
        event = ctx.event
        result = ctx.result
    else:
        loop = asyncio.get_running_loop()
        event = asyncio.Event()
        result = {"groupings": None, "aborted": False}

        target_name = getattr(builder, "target_name", "migration")
        if plan_path is None:
            plan_path = (
                Path(builder.output_dir) / f"{target_name}_grouping_plan.json"
            )
        plan_path = Path(plan_path)

        ctx = _ReviewContext(
            ir=ir,
            builder=builder,
            plan_path=plan_path,
            loop=loop,
            event=event,
            result=result,
            console=console,
            groupings=groupings,
            consolidator=consolidator,
            root_key=root_key,
            dep_summary=dep_summary,
        )

        # Bind the server.
        port = bind_port if bind_port else _free_port()
        review_url = f"http://{bind_host}:{port}/review"
        handler_cls = _make_handler(ctx, review_url=review_url)
        server = http.server.ThreadingHTTPServer((bind_host, port), handler_cls)
        server.daemon_threads = True
        thread = threading.Thread(
            target=server.serve_forever,
            name="grouping-review-server",
            daemon=True,
        )
        thread.start()
        ctx.server = server
        ctx.review_url = review_url

    # Pre-seed the plan file
    try:
        structural_consolidator.persist_grouping(groupings, str(plan_path))
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to pre-seed plan file: %s", exc)

    banner = (
        "\n" + "=" * 80 + "\n"
        "🎉 INTERACTIVE GROUPING REVIEW IS LIVE!\n\n"
        "Access your interactive migration report and confirm groupings here:\n"
        f"[bold cyan]{review_url}[/]\n\n"
        "👉 Use this page to drag-and-drop flows, mark root agents, and\n"
        "   request Gemini re-proposals. Once confirmed, you can continue\n"
        "   monitoring the optimization and deployment progress live from\n"
        "   this same page!\n"
        f"   (Timeout: {timeout_s}s)\n" + "=" * 80 + "\n"
    )
    console.print(banner)

    # Only auto-open if we didn't start the server early (to avoid double tabs)
    if active_context is None and auto_open_browser:
        try:
            webbrowser.open_new_tab(review_url)
        except Exception as exc:  # noqa: BLE001
            logger.debug("could not auto-open browser: %s", exc)

    watcher_task = asyncio.create_task(_watch_plan_file(ctx))
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout_s)
    except asyncio.TimeoutError:
        console.print(
            "[yellow][grouping-review] timed out; aborting consolidation[/]"
        )
        ctx.abort()
    finally:
        watcher_task.cancel()
        try:
            await watcher_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        # NOTE: we intentionally do NOT shut down the server here. Stage
        # 2 / Stage 3 continue rewriting <target>_migration_analysis.html
        # via builder.flush(), and the user-held browser tab at /review
        # keeps reading that file fresh on every refresh — so they can
        # watch the consolidation, optimization, and topology phases
        # complete from the same page (with the Grouping Review tab
        # showing the confirmed plan + status badge). The server runs in
        # a daemon thread and dies cleanly at process exit.
        console.print(
            f"[cyan][grouping-review] review server still live at"
            f" {review_url} — refresh to watch Stage 2/3 progress.[/]"
        )

    if result["aborted"] or result["groupings"] is None:
        return None
    return result["groupings"]
