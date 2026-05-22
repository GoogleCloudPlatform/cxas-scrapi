# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""IR bundle persistence — single source of truth for cross-stage state.

The IR bundle is the artifact that lets independent CLI invocations of
``cxas migrate dfcx-cxas {run, stage1, stage2, stage3}`` (and the
equivalent skill scripts) resume against the same migration without
re-fetching the source DFCX agent or re-running the per-flow
Step 2A/2B/2C compile pipeline. It contains:

* The original :class:`MigrationConfig` (project, location, model, etc.)
* The source :class:`DFCXAgentIR` (needed for tool-mock context in Stage 2)
* The current :class:`MigrationIR` (target — mutated by each stage)
* Stage history + version checkpoints for the audit report.
"""

from __future__ import annotations

import glob
import logging
import os
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from cxas_scrapi.migration.data_models import (
    DFCXAgentIR,
    MigrationConfig,
    MigrationIR,
)

logger = logging.getLogger(__name__)


class StageHistoryEntry(BaseModel):
    phase: str  # "migrate", "stage1", "stage2"
    status: str  # "ok", "fail", "partial"
    started_at: datetime
    ended_at: datetime | None = None
    notes: str = ""


class IRBundle(BaseModel):
    """Persisted state shared across migrate / stage1 / stage2."""

    schema_version: str = "2"
    created_at: datetime = Field(default_factory=datetime.now)
    config: MigrationConfig
    source_agent_data: DFCXAgentIR
    ir: MigrationIR
    stage_history: list[StageHistoryEntry] = Field(default_factory=list)
    app_url: str | None = None
    version_checkpoints: list[tuple[str, str]] = Field(default_factory=list)
    grouping: dict[str, Any] | None = (
        None  # populated when Stage 1 consolidates
    )
    # Snapshot of `ir` BEFORE consolidation mutated agents/tools, kept on disk
    # so the original 1:1 IR survives even after the consolidation flow
    # collapses N agents into M groups.
    pre_consolidation_ir: MigrationIR | None = None

    def resolve_location(self, default: str = "us") -> str:
        """Return the CXAS location for this bundle's app.

        Tries to parse `projects/<p>/locations/<L>/apps/<a>` out of
        :attr:`app_url`; falls back to `default` ("us") if that fails.
        """
        if self.app_url and "/locations/" in self.app_url:
            try:
                return self.app_url.split("/locations/")[1].split("/")[0]
            except (IndexError, AttributeError):
                pass
        return default


# ---------------------------------------------------------------------------
# Disk I/O
# ---------------------------------------------------------------------------


def _bundle_filename(target_name: str) -> str:
    return f"{target_name}_ir.json"


def save(bundle: IRBundle, path: str) -> str:
    """Atomic write — write to a tempfile then rename."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(bundle.model_dump_json(indent=2))
    os.replace(tmp, path)
    logger.info("IR bundle saved → %s", path)
    return path


def save_for_target(
    bundle: IRBundle, target_name: str, cwd: str | None = None
) -> str:
    path = os.path.join(cwd or os.getcwd(), _bundle_filename(target_name))
    return save(bundle, path)


def load(path: str) -> IRBundle:
    with open(path) as f:
        return IRBundle.model_validate_json(f.read())


def find_default_bundle(
    target_name: str | None = None, cwd: str | None = None
) -> str | None:
    """Locate an IR bundle on disk.

    If `target_name` is supplied: returns `<cwd>/<target_name>_ir.json` if it
    exists, else None.
    Otherwise: returns the newest `*_ir.json` in cwd, or None.
    """
    cwd = cwd or os.getcwd()
    if target_name:
        candidate = os.path.join(cwd, _bundle_filename(target_name))
        return candidate if os.path.exists(candidate) else None
    matches = glob.glob(os.path.join(cwd, "*_ir.json"))
    if not matches:
        return None
    matches.sort(key=os.path.getmtime, reverse=True)
    return matches[0]


# ---------------------------------------------------------------------------
# Convenience mutators
# ---------------------------------------------------------------------------


def append_stage(
    bundle: IRBundle,
    phase: str,
    status: str,
    started_at: datetime,
    notes: str = "",
) -> None:
    bundle.stage_history.append(
        StageHistoryEntry(
            phase=phase,
            status=status,
            started_at=started_at,
            ended_at=datetime.now(),
            notes=notes,
        )
    )


def attach_version(
    bundle: IRBundle, display_name: str, description: str
) -> None:
    bundle.version_checkpoints.append((display_name, description))


def attach_grouping(bundle: IRBundle, groupings: dict[str, Any]) -> None:
    bundle.grouping = groupings
