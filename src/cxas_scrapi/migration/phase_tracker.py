# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Phase tracker — clear, timed start/end markers around long-running phases.

Used to give the user concrete progress signals while the migration or
optimization is running. The markers also feed the final summary table.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from rich.console import Console
from rich.table import Table


class PhaseTracker:
    """Records phase start/end with wall-clock timings."""

    def __init__(self, console: Console):
        self.console = console
        self._records: list[dict] = []
        self._stack: list[dict] = []

    def start(self, label: str, description: str = "") -> None:
        ts = datetime.now()
        record = {
            "label": label,
            "description": description,
            "start": ts,
            "start_monotonic": time.monotonic(),
            "end": None,
            "duration": None,
            "status": "running",
        }
        self._records.append(record)
        self._stack.append(record)
        self.console.rule(
            f"[bold blue]▶ {label}[/]"
            + (f" — {description}" if description else "")
            + f"  [dim]{ts.strftime('%H:%M:%S')}[/]"
        )

    def end(self, label: str, status: str = "ok", note: str = "") -> None:
        for rec in reversed(self._stack):
            if rec["label"] == label and rec["end"] is None:
                rec["end"] = datetime.now()
                rec["duration"] = time.monotonic() - rec["start_monotonic"]
                rec["status"] = status
                rec["note"] = note
                self._stack.remove(rec)
                tag = (
                    "[green]✓[/]"
                    if status == "ok"
                    else "[red]✗[/]"
                    if status == "fail"
                    else "[yellow]~[/]"
                )
                self.console.print(
                    f"{tag} [bold]{label}[/] in {rec['duration']:.1f}s"
                    + (f" — {note}" if note else "")
                )
                return
        # Phase wasn't started; no-op silently to keep the orchestrator robust.

    @contextmanager
    def phase(self, label: str, description: str = "") -> Iterator[None]:
        self.start(label, description)
        try:
            yield
        except Exception as exc:
            self.end(label, status="fail", note=str(exc).splitlines()[0][:120])
            raise
        else:
            self.end(label, status="ok")

    def summary_table(self) -> Table:
        table = Table(title="Pipeline phase summary")
        table.add_column("Phase", style="cyan")
        table.add_column("Description")
        table.add_column("Status", style="green")
        table.add_column("Duration", justify="right")
        for rec in self._records:
            duration = (
                f"{rec['duration']:.1f}s"
                if rec["duration"] is not None
                else "—"
            )
            status_color = {
                "ok": "[green]ok[/]",
                "fail": "[red]fail[/]",
                "skipped": "[yellow]skipped[/]",
                "running": "[blue]running[/]",
            }.get(rec["status"], rec["status"])
            table.add_row(
                rec["label"], rec.get("description", ""), status_color, duration
            )
        return table

    def to_dict(self) -> list[dict]:
        """Serializable snapshot for the audit report."""
        out = []
        for rec in self._records:
            out.append(
                {
                    "label": rec["label"],
                    "description": rec.get("description", ""),
                    "status": rec["status"],
                    "duration_s": (
                        round(rec["duration"], 2)
                        if rec["duration"] is not None
                        else None
                    ),
                    "started_at": rec["start"].isoformat(),
                    "ended_at": rec["end"].isoformat() if rec["end"] else None,
                    "note": rec.get("note", ""),
                }
            )
        return out
