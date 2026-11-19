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

"""CLI handlers and Rich reporting for ``cxas push --patch`` and ``cxas watch``.

Keeps imports lightweight so ``cxas push --patch`` and ``cxas watch`` avoid
loading heavy analytics modules during fast edit-patch loops.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

if TYPE_CHECKING:
    import argparse

    from cxas_scrapi.core.hot_patch import HotPatchReport

console = Console()


def _infer_app_identifier_from_workspace(app_dir: str) -> str | None:
    """Infers a target app resource name from workspace config if present."""
    base = Path(app_dir).resolve()
    for candidate in (
        base / "gecx-config.json",
        base.parent / "gecx-config.json",
    ):
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                for key in ("app_name", "app_resource_name", "app_id", "app"):
                    val = data.get(key)
                    if isinstance(val, str) and val.startswith("projects/"):
                        return val.strip()
                for key in ("app_name", "app_id", "app"):
                    val = data.get(key)
                    if isinstance(val, str) and val.strip():
                        return val.strip()
            except (OSError, json.JSONDecodeError):
                continue
    return None


def resolve_patch_target(
    args: argparse.Namespace,
    apps_client: Any = None,
    target_app_name: str | None = None,
) -> tuple[Any, str]:
    """Resolves ``(scoped_client, full_app_resource_name)``."""
    from cxas_scrapi.core.hot_patch import (
        DirectCesControlPlane,
        _AppScopedAdapter,
        extract_location,
        extract_project_id,
    )

    if apps_client is not None and target_app_name:
        return apps_client, target_app_name

    app_dir = getattr(args, "app_dir", None) or "."
    identifier = (
        target_app_name
        or getattr(args, "to", None)
        or getattr(args, "app_name", None)
        or getattr(args, "app", None)
        or _infer_app_identifier_from_workspace(app_dir)
    )
    if not identifier:
        console.print(
            "[bold red]Error:[/bold red] Hot-patching requires an existing "
            "target app. Pass [bold]--to <app_resource_or_display_name>[/bold] "
            "or [bold]--app-name projects/.../locations/.../apps/...[/bold]."
        )
        sys.exit(1)

    project_id = extract_project_id(identifier) or getattr(
        args, "project_id", None
    )
    location = extract_location(identifier) or getattr(args, "location", None)
    if not project_id or not location:
        console.print(
            "[bold red]Error:[/bold red] Could not determine project_id or "
            "location. Pass a full resource name "
            "(projects/<proj>/locations/<loc>/apps/<app>) or provide "
            "--project-id and --location."
        )
        sys.exit(1)

    cp = DirectCesControlPlane(project_id=project_id, location=location)
    if identifier.startswith("projects/"):
        app_name = identifier
    else:
        matched = cp.get_app_by_display_name(identifier)
        if matched is None:
            console.print(f"[bold red]App '{identifier}' not found.[/bold red]")
            sys.exit(1)
        app_name = matched.name

    return _AppScopedAdapter(app_name, cp), app_name


def _short_etag(etag: str) -> str:
    if not etag:
        return "—"
    return etag if len(etag) <= 16 else f"{etag[:12]}…"


def _short_sha(sha: str | None) -> str:
    if not sha:
        return "(new)"
    return sha[:8]


def render_hot_patch_report(
    report: HotPatchReport, out_console: Console | None = None
) -> None:
    """Renders a structured Rich summary of the hot-patch execution."""
    c = out_console or console

    for warning in report.structural_warnings:
        c.print(f"[yellow]⚠ Structural warning:[/yellow] {warning}")

    if report.status == "noop":
        c.print(
            Panel(
                "[green]✓ Workspace is already in sync with live CES "
                f"({report.diff_ms:.1f} ms SHA-256 check — "
                "0 RPCs needed).[/green]",
                title="CXAS In-Flight Hot-Patch",
                border_style="green",
            )
        )
        return

    diff_table = Table(
        title="1. Compiled Workspace Delta (SHA-256 Diff -> FieldMask)",
        show_header=True,
        header_style="bold cyan",
    )
    diff_table.add_column("Modified File", style="bold")
    diff_table.add_column("Target")
    diff_table.add_column("FieldMask Path", style="magenta")
    diff_table.add_column("SHA-256 Delta")
    diff_table.add_column("Bytes", justify="right")

    for d in report.deltas:
        diff_table.add_row(
            d.rel_path,
            d.resource_key,
            d.field_mask_path,
            f"{_short_sha(d.old_sha256)} -> {_short_sha(d.new_sha256)}",
            f"{d.size_bytes:,}",
        )
    c.print(diff_table)

    for finding in report.lint_findings:
        color = "red" if finding.severity == "error" else "yellow"
        c.print(f"[{color}]{finding.format_short()}[/{color}]")

    if report.status == "dry_run":
        c.print(
            Panel(
                f"[cyan]Dry-run complete:[/cyan] {len(report.deltas)} file(s) "
                f"validated in [bold]{report.total_ms:.1f} ms[/bold] "
                f"(Diff {report.diff_ms:.1f} ms | "
                f"Scoped Lint {report.lint_ms:.1f} ms). "
                "No remote RPCs sent.",
                title="CXAS Hot-Patch Dry Run",
                border_style="cyan",
            )
        )
        return

    if report.applied_patches:
        has_dag_create = any(
            getattr(p, "operation", "UPDATE") == "CREATE"
            or getattr(p, "dag_level", 1) > 1
            for p in report.applied_patches
        )
        title_str = (
            "2. Topological DAG Execution "
            "(CreateTool / CreateGuardrail / CreateAgent / Update*)"
            if has_dag_create
            else "2. Surgical Control-Plane Updates "
            "(UpdateApp / UpdateAgent / UpdateTool)"
        )
        rpc_table = Table(
            title=title_str,
            show_header=True,
            header_style="bold green",
        )
        rpc_table.add_column("DAG Step", justify="center", style="cyan")
        rpc_table.add_column("Action", justify="center")
        rpc_table.add_column("Resource", style="bold")
        rpc_table.add_column("Type")
        rpc_table.add_column("FieldMask / Operation", style="magenta")
        rpc_table.add_column("Etag (Optimistic Lock)")
        rpc_table.add_column("Payload", justify="right")
        rpc_table.add_column("RPC Latency", justify="right", style="green")

        for p in report.applied_patches:
            op = getattr(p, "operation", "UPDATE")
            lvl = getattr(p, "dag_level", 1)
            op_badge = (
                "[bold cyan]+ CREATE[/bold cyan]"
                if op == "CREATE"
                else "[bold green]~ UPDATE[/bold green]"
            )
            rpc_table.add_row(
                f"L{lvl}",
                op_badge,
                p.display_name,
                p.resource_type,
                ", ".join(p.field_mask_paths),
                f"{_short_etag(p.old_etag)} -> {_short_etag(p.new_etag)}",
                f"{p.payload_bytes:,} B",
                f"{p.latency_ms:.1f} ms",
            )
        c.print(rpc_table)

    if report.rollback_actions:
        rb_table = Table(
            title="3. Reverse-Topological In-Memory Atomic Rollback",
            show_header=True,
            header_style="bold yellow",
        )
        rb_table.add_column("Resource", style="bold")
        rb_table.add_column("Rollback Operation", style="magenta")
        rb_table.add_column("Status")
        rb_table.add_column("Rollback Latency", justify="right")

        for rb in report.rollback_actions:
            rb_op = getattr(rb, "operation", "REVERT")
            if rb.restored:
                status_str = (
                    "[bold green]✓ DELETED (CLEANED UP)[/bold green]"
                    if rb_op == "DELETE"
                    else "[bold green]✓ RESTORED[/bold green]"
                )
            else:
                status_str = f"[bold red]✗ FAILED ({rb.error})[/bold red]"
            rb_table.add_row(
                rb.display_name,
                ", ".join(rb.field_mask_paths),
                status_str,
                f"{rb.latency_ms:.1f} ms",
            )
        c.print(rb_table)

    if report.status == "patched":
        created_cnt = sum(
            1
            for p in report.applied_patches
            if getattr(p, "operation", "UPDATE") == "CREATE"
        )
        updated_cnt = len(report.applied_patches) - created_cnt
        summary_label = (
            f"✓ DAG-created {created_cnt} & hot-patched "
            f"{updated_cnt} resource(s)"
            if created_cnt
            else f"✓ Hot-patched {len(report.applied_patches)} resource(s)"
        )
        c.print(
            Panel(
                f"[bold green]{summary_label} "
                f"({report.total_payload_bytes:,} B delta) in "
                f"{report.total_ms:.1f} ms[/bold green]\n"
                f"  Diff: {report.diff_ms:.1f} ms  •  "
                f"Scoped Lint: {report.lint_ms:.1f} ms  •  "
                f"Etag & Snapshot: {report.snapshot_ms:.1f} ms  •  "
                f"Surgical DAG RPCs: {report.rpc_ms:.1f} ms\n"
                "  [dim]Bypassed monolithic ImportApp ZIP LRO — "
                "Turn N+1 sessions pick up changes with 0 dropped calls.[/dim]",
                title="CXAS In-Flight Hot-Patch Succeeded",
                border_style="green",
            )
        )


def run_hot_patch_cli(
    args: argparse.Namespace,
    apps_client: Any = None,
    target_app_name: str | None = None,
) -> str | None:
    """Executes ``cxas push --patch`` from parsed CLI arguments."""
    from cxas_scrapi.core.hot_patch import (
        EtagConflictError,
        HotPatchApplyError,
        HotPatchEngine,
        ScopedLintError,
    )

    resolved_client, app_name = resolve_patch_target(
        args, apps_client=apps_client, target_app_name=target_app_name
    )
    app_dir = getattr(args, "app_dir", None) or "."

    engine = HotPatchEngine(
        app_name=app_name,
        app_dir=app_dir,
        apps_client=resolved_client,
        agents_client=resolved_client,
        tools_client=resolved_client,
        guardrails_client=resolved_client,
    )

    try:
        report = engine.apply_patch(
            dry_run=bool(getattr(args, "dry_run", False)),
            skip_lint=bool(getattr(args, "skip_lint", False)),
            force_etag=bool(getattr(args, "force_etag", False)),
            expected_etag=getattr(args, "expected_etag", None),
        )
        render_hot_patch_report(report)
        return app_name
    except ScopedLintError as exc:
        render_hot_patch_report(exc.report)
        console.print(
            Panel(
                f"[bold red]Pre-Flight Validation Blocked Patch "
                f"({exc.report.lint_ms:.1f} ms — 0 RPCs sent)[/bold red]\n"
                f"{exc}",
                title="Scoped Pre-Flight Linter",
                border_style="red",
            )
        )
        sys.exit(1)
    except EtagConflictError as exc:
        render_hot_patch_report(exc.report)
        console.print(
            Panel(
                "[bold red]Etag Optimistic Concurrency Conflict[/bold red]\n"
                f"Resource: [bold]{exc.display_name}[/bold] "
                f"({exc.resource_name})\n"
                f"Expected Etag: [yellow]{exc.expected_etag}[/yellow]\n"
                f"Remote Etag:   [cyan]{exc.remote_etag}[/cyan]\n\n"
                "Run [bold]cxas pull[/bold] to merge remote edits or pass "
                "[bold]--force-etag[/bold] to override.",
                title="Overwrite Prevented",
                border_style="red",
            )
        )
        sys.exit(1)
    except HotPatchApplyError as exc:
        render_hot_patch_report(exc.report)
        console.print(
            Panel(
                f"[bold red]{exc}[/bold red]",
                title="Hot-Patch Aborted (Atomic Rollback Guard)",
                border_style="red",
            )
        )
        sys.exit(1)


def run_watch_cli(args: argparse.Namespace) -> None:
    """Executes ``cxas watch`` for live hot-reloading on file save."""
    from cxas_scrapi.core.hot_patch import (
        EtagConflictError,
        HotPatchApplyError,
        HotPatchEngine,
        ScopedLintError,
        StateManifest,
        scan_workspace_hashes,
        sync_manifest_from_workspace,
    )

    scoped_client, app_name = resolve_patch_target(args)
    app_dir = getattr(args, "app_dir", None) or "."
    interval = max(0.1, float(getattr(args, "interval", 0.5) or 0.5))
    max_events = getattr(args, "max_events", None)

    engine = HotPatchEngine(
        app_name=app_name,
        app_dir=app_dir,
        apps_client=scoped_client,
        agents_client=scoped_client,
        tools_client=scoped_client,
        guardrails_client=scoped_client,
    )

    if StateManifest.load(engine.app_root, app_name) is None:
        sync_manifest_from_workspace(
            engine.app_root,
            app_name,
            scoped_client,
            scoped_client,
            scoped_client,
            guardrails_client=scoped_client,
        )

    last_hashes = scan_workspace_hashes(engine.app_root)
    console.print(
        Panel(
            f"Watching [bold]{engine.app_root}[/bold]\n"
            f"Target CES App: [cyan]{app_name}[/cyan]\n"
            f"Poll Interval: {interval:.2f}s  •  Press Ctrl+C to stop.",
            title="CXAS Live Hot-Patch Watch Mode",
            border_style="cyan",
        )
    )

    events_handled = 0
    try:
        while True:
            time.sleep(interval)
            current_hashes = scan_workspace_hashes(engine.app_root)
            if current_hashes == last_hashes:
                continue

            last_hashes = current_hashes
            ts = time.strftime("%H:%M:%S")
            console.print(
                f"\n[bold cyan][{ts}] Change detected — "
                "compiling surgical patch...[/bold cyan]"
            )
            try:
                report = engine.apply_patch(
                    skip_lint=bool(getattr(args, "skip_lint", False)),
                    force_etag=bool(getattr(args, "force_etag", False)),
                )
                render_hot_patch_report(report)
            except (
                ScopedLintError,
                EtagConflictError,
                HotPatchApplyError,
            ) as exc:
                render_hot_patch_report(exc.report)
                console.print(
                    f"[bold red]Watch patch rejected:[/bold red] {exc}"
                )

            events_handled += 1
            if max_events is not None and events_handled >= int(max_events):
                break
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped CXAS watch mode.[/yellow]")


def attach_hot_patch_args(push_parser: argparse.ArgumentParser) -> None:
    """Adds ``--patch`` and related differential hot-patch flags to ``push``."""
    push_parser.add_argument(
        "--patch",
        action="store_true",
        help=(
            "Zero-downtime differential hot-patch: hashes modified prompt, "
            "callback, tool, and guardrail files, runs scoped pre-flight "
            "linting, checks etags, and applies surgical FieldMask RPCs in "
            "topological DAG order instead of a monolithic ImportApp ZIP LRO."
        ),
    )
    push_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --patch, compile SHA-256 diffs and lint without sending RPCs.",
    )
    push_parser.add_argument(
        "--force-etag",
        action="store_true",
        help="With --patch, override remote etag optimistic concurrency check.",
    )
    push_parser.add_argument(
        "--skip-lint",
        action="store_true",
        help="With --patch, bypass scoped pre-flight linting.",
    )
    push_parser.add_argument(
        "--expected-etag",
        help="Optional explicit etag to assert before applying --patch.",
    )


def register(subparsers: Any) -> None:
    """Registers the ``cxas watch`` subparser on the main CLI."""
    parser_watch = subparsers.add_parser(
        "watch",
        help=(
            "Watch local prompt, callback, tool, and guardrail files and "
            "auto-apply surgical hot-patches on save."
        ),
    )
    parser_watch.add_argument(
        "--app-dir", default=".", help="Local app directory to watch."
    )
    parser_watch.add_argument(
        "--to", help="Target App Resource Name or Display Name."
    )
    parser_watch.add_argument(
        "--app-name",
        help="Target App ID (projects/.../locations/.../apps/...).",
    )
    parser_watch.add_argument(
        "--project-id",
        dest="project_id",
        help=(
            "GCP Project ID (required only when targeting an app by display "
            "name)."
        ),
    )
    parser_watch.add_argument(
        "--location",
        help=(
            "GCP Location, e.g. global or us-central1 (required only for "
            "display names)."
        ),
    )
    parser_watch.add_argument(
        "--interval",
        type=float,
        default=0.5,
        help="File-watch polling interval in seconds (default: 0.5).",
    )
    parser_watch.add_argument(
        "--force-etag",
        action="store_true",
        help="Override remote etag mismatches while watching.",
    )
    parser_watch.add_argument(
        "--skip-lint",
        action="store_true",
        help="Skip scoped pre-flight linting on watched saves.",
    )
    parser_watch.add_argument(
        "--max-events",
        type=int,
        default=None,
        help="Optional maximum patch events to process before exiting.",
    )
    parser_watch.set_defaults(func=run_watch_cli)
