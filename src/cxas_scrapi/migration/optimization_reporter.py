# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""OptimizationReporter: writes a Markdown audit report describing the
consolidated CXAS app produced by stage1.py + stage2.py."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class OptimizationReporter:
    """Collects every artifact produced by the optimization pipeline and
    emits a single audit report."""

    source_id: str = ""
    target_name: str = ""
    target_resource: str = ""
    app_url: str = ""
    dependency_outgoing: list[str] = field(default_factory=list)
    dependency_incoming: list[str] = field(default_factory=list)
    groupings: dict[str, dict[str, Any]] = field(default_factory=dict)
    before_agent_count: int = 0
    after_agent_count: int = 0
    stage1_logs: list[dict[str, Any]] = field(default_factory=list)
    stage2_logs: list[dict[str, Any]] = field(default_factory=list)
    versions: list[tuple[str, str]] = field(default_factory=list)
    unit_test_counts: dict[str, int] = field(default_factory=dict)
    unit_test_path: str = ""
    grouping_path: str = ""
    lint_passed: bool | None = None
    lint_output_excerpt: str = ""

    def set_app_info(
        self,
        source_id: str,
        target_name: str,
        target_resource: str,
        app_url: str,
    ) -> None:
        self.source_id = source_id
        self.target_name = target_name
        self.target_resource = target_resource
        self.app_url = app_url

    def set_dependency_summary(
        self, outgoing: list[str], incoming: list[str]
    ) -> None:
        self.dependency_outgoing = list(outgoing)
        self.dependency_incoming = list(incoming)

    def set_grouping(
        self,
        groupings: dict,
        before_count: int,
        after_count: int,
        path: str = "",
    ) -> None:
        self.groupings = groupings
        self.before_agent_count = before_count
        self.after_agent_count = after_count
        self.grouping_path = path

    def set_optimizer_logs(
        self,
        stage1_logs: list[dict[str, Any]] | None,
        stage2_logs: list[dict[str, Any]] | None,
    ) -> None:
        self.stage1_logs = list(stage1_logs or [])
        self.stage2_logs = list(stage2_logs or [])

    def set_version_checkpoints(self, versions: list[tuple[str, str]]) -> None:
        self.versions = list(versions)

    def set_unit_test_summary(
        self, per_agent: dict[str, int], path: str
    ) -> None:
        self.unit_test_counts = dict(per_agent)
        self.unit_test_path = path

    def set_lint_result(self, passed: bool, output_excerpt: str) -> None:
        self.lint_passed = passed
        # Cap the excerpt so the report stays scannable.
        self.lint_output_excerpt = (output_excerpt or "")[:4000]

    # ------------------------------------------------------------------
    # Markdown rendering
    # ------------------------------------------------------------------

    def generate_markdown(self) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        out: list[str] = [
            "# CXAS Optimization Audit Report",
            f"**Generated:** `{ts}`\n",
            "## App Details",
            f"- **Source DFCX Agent:** `{self.source_id or 'N/A'}`",
            f"- **Target CXAS App:** `{self.target_name or 'N/A'}`",
            f"- **App Resource:** `{self.target_resource or 'N/A'}`",
            f"- **Console URL:** {self.app_url or 'N/A'}\n",
        ]

        out += [
            "## Consolidation Summary",
            f"- **1:1 IR agents (before grouping):** {self.before_agent_count}",
            f"- **Consolidated agents (after):** {self.after_agent_count}",
        ]
        if self.grouping_path:
            out.append(f"- **Grouping JSON artifact:** `{self.grouping_path}`")
        out.append("")

        if self.groupings:
            out += [
                "### Grouping Detail",
                "| Group | Members | Journey | Root |",
                "|---|---|---|---|",
            ]
            for name, payload in self.groupings.items():
                members = ", ".join(payload.get("agents", []) or [])
                journey = (payload.get("journey") or "").replace("|", "\\|")
                is_root = "yes" if payload.get("is_root") else ""
                out.append(f"| `{name}` | {members} | {journey} | {is_root} |")
            out.append("")

        if self.dependency_outgoing or self.dependency_incoming:
            out += ["## Source Dependency Analysis"]
            if self.dependency_outgoing:
                out.append(
                    "**Outgoing (selection references → not selected):**"
                )
                for r in self.dependency_outgoing:
                    out.append(f"- {r}")
            if self.dependency_incoming:
                out.append(
                    "\n**Incoming (not selected → references selection):**"
                )
                for r in self.dependency_incoming:
                    out.append(f"- {r}")
            out.append("")

        if self.stage1_logs or self.stage2_logs:
            out += ["## CXASOptimizer Logs"]
            if self.stage1_logs:
                out += [
                    "### Stage 1 — Variable Deduplication",
                    "| Stage | Action | Details |",
                    "|---|---|---|",
                ]
                for entry in self.stage1_logs:
                    out.append(
                        f"| `{entry.get('stage', '?')}` | "
                        f"`{entry.get('action', '?')}` | "
                        f"{entry.get('details', '')} |"
                    )
                out.append("")
            if self.stage2_logs:
                out += [
                    "### Stage 2 — Instruction State Machines + Tool Mocks",
                    "| Stage | Action | Details |",
                    "|---|---|---|",
                ]
                for entry in self.stage2_logs:
                    out.append(
                        f"| `{entry.get('stage', '?')}` | "
                        f"`{entry.get('action', '?')}` | "
                        f"{entry.get('details', '')} |"
                    )
                out.append("")

        if self.versions:
            out += [
                "## CXAS Version Checkpoints",
                "| Display Name | Description |",
                "|---|---|",
            ]
            for display, desc in self.versions:
                out.append(f"| `{display}` | {desc} |")
            out.append("")

        if self.unit_test_counts:
            out += [
                "## Deterministic Unit Tests",
                f"- **Artifact:** `{self.unit_test_path or 'N/A'}`",
                "- **Tests per agent:**",
            ]
            for name, count in self.unit_test_counts.items():
                out.append(f"  - `{name}`: {count}")
            out.append("")

        if self.lint_passed is not None:
            out += [
                "## Lint",
                "- **Status:** "
                + ("✅ passed" if self.lint_passed else "⚠️ issues found"),
            ]
            if self.lint_output_excerpt:
                out += [
                    "",
                    "<details><summary>Lint output (excerpt)</summary>",
                    "",
                    "```",
                    self.lint_output_excerpt,
                    "```",
                    "",
                    "</details>",
                ]
            out.append("")

        return "\n".join(out)

    def export(self, filename: str) -> str:
        content = self.generate_markdown()
        with open(filename, "w") as f:
            f.write(content)
        logger.info("Optimization report written: %s", filename)
        return filename

    def to_json(self) -> str:
        return json.dumps(
            {
                "source_id": self.source_id,
                "target_name": self.target_name,
                "groupings": self.groupings,
                "stage1_logs": self.stage1_logs,
                "stage2_logs": self.stage2_logs,
                "versions": self.versions,
                "unit_test_counts": self.unit_test_counts,
                "lint_passed": self.lint_passed,
            },
            indent=2,
        )
