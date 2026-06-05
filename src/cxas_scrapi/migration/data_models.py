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

"""Data models for the Intermediate Representation (IR) of DFCX agents."""

import enum
import glob
import logging
import os
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class MigrationStatus(str, enum.Enum):
    """Represents the status of a migration component."""

    COMPILED = "Compiled"
    GENERATED = "Generated"
    DEPLOYED = "Deployed"
    FAILED = "Failed"
    ERROR = "Error"
    PENDING = "Pending"


# --- Source DFCX Models ---


class DFCXPageModel(BaseModel):
    """Represents a Page in a Flow."""

    page_id: str
    page_data: dict[str, Any]


class DFCXFlowModel(BaseModel):
    """Represents a Flow with its Pages."""

    flow_id: str  # The full resource name of the flow
    flow_data: dict[str, Any]  # The raw flow data
    pages: list[DFCXPageModel] = Field(default_factory=list)


class DFCXAgentIR(BaseModel):
    """Represents the full extracted state of a DFCX Agent."""

    name: str  # The full resource name of the DFCX agent
    display_name: str  # The human-readable name of the DFCX agent
    default_language_code: str
    supported_language_codes: list[str] = Field(default_factory=list)
    time_zone: str | None = None
    description: str | None = None
    start_flow: str | None = None
    start_playbook: str | None = None
    intents: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    entity_types: list[dict[str, Any]] = Field(default_factory=list)
    webhooks: list[dict[str, Any]] = Field(default_factory=list)
    flows: list[DFCXFlowModel] = Field(default_factory=list)
    playbooks: list[dict[str, Any]] = Field(default_factory=list)
    test_cases: list[dict[str, Any]] = Field(default_factory=list)
    generative_settings: dict[str, dict[str, Any]] = Field(default_factory=dict)
    playbook_generative_settings: dict[str, Any] | None = None
    generators: list[dict[str, Any]] = Field(default_factory=list)
    agent_transition_route_groups: list[dict[str, Any]] = Field(
        default_factory=list
    )
    no_speech_timeout: str | None = "7s"
    code_blocks: list[dict[str, Any]] = Field(default_factory=list)


# --- Target Migration IR Models ---


class IRMetadata(BaseModel):
    """Metadata for the migration target."""

    app_name: str  # The display name of the target Polysynth app
    app_id: str | None = None  # The UUID generated for the new Polysynth app
    app_resource_name: str | None = (
        None  # The full resource name of the Polysynth app
    )
    default_model: str = "gemini-2.5-flash-001"


class IRTool(BaseModel):
    """Represents a tool in the target IR."""

    id: str  # Short ID (e.g., "tool_billing")
    name: str  # Full resource name (projects/.../tools/...)
    type: str  # "TOOLSET", "TOOL", "PYTHON"
    payload: dict[str, Any]
    operation_ids: list[str] = Field(default_factory=list)
    status: MigrationStatus = MigrationStatus.COMPILED


class IRAgent(BaseModel):
    """Represents a Generative Agent (Playbook or Flow) in the target IR."""

    type: str  # "FLOW", "PLAYBOOK"
    display_name: str
    instruction: str  # The generated PIF XML
    description: str | None = None
    tools: list[str] = Field(default_factory=list)  # Resource names
    toolsets: list[dict[str, Any]] = Field(
        default_factory=list
    )  # [{"toolset": ..., "toolIds": []}]
    model_settings: dict[str, Any] = Field(default_factory=dict)
    raw_data: dict[str, Any] | None = None  # Original DFCX data
    blueprint: dict[str, Any] | None = None  # Used by Flows
    callbacks: dict[str, Any] | None = None  # Used by Flows
    status: MigrationStatus = MigrationStatus.COMPILED
    resource_name: str | None = None  # Populated after deployment


class MigrationIR(BaseModel):
    """The full state of the migration offline workspace."""

    metadata: IRMetadata
    parameters: dict[str, dict[str, Any]] = Field(default_factory=dict)
    tools: dict[str, IRTool] = Field(default_factory=dict)
    agents: dict[str, IRAgent] = Field(default_factory=dict)
    routing_edges: list[dict[str, Any]] = Field(default_factory=list)
    test_cases: dict[str, Any] = Field(default_factory=dict)
    test_runs: dict[str, Any] = Field(default_factory=dict)
    optimization_logs: dict[str, Any] = Field(default_factory=dict)


class MigrationConfig(BaseModel):
    """Configuration for the migration process."""

    project_id: str
    target_name: str
    model: str
    env: str = "PROD"
    profile: str = "standard"
    architecture: str = "hub-and-spoke"
    gen_report: bool = True
    gen_unit_tests: bool = True
    gen_hillclimbing_evals: bool = False
    eval_runner_target: str = "Custom API Runner"
    migration_version: str = "2.0"
    optimize_for_cxas: bool = False
    persist_bundle: bool = False
    interactive: bool = False
    source_agent_data_override: DFCXAgentIR | None = None

    @property
    def consolidate(self) -> bool:
        """Backward-compatibility property hook: consolidation is active
        whenever optimize is active.
        """
        return self.optimize_for_cxas

    @property
    def run_stage_3(self) -> bool:
        """Backward-compatibility property hook: Stage 3 parent-child routing
        is active whenever optimize is active.
        """
        return self.optimize_for_cxas


class StageHistoryEntry(BaseModel):
    phase: str  # "migrate", "stage_1", "stage_2", "stage_3"
    status: str  # "ok", "fail", "partial"
    started_at: datetime
    ended_at: datetime | None = None
    notes: str = ""


class IRBundle(BaseModel):
    """Persisted state shared across migrate / stage_1 / stage_2."""

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

    # --- Disk I/O Methods (Native OO Class/Instance Methods) ---

    @staticmethod
    def _bundle_filename(target_name: str) -> str:
        return f"{target_name}_ir.json"

    def save(self, path: str) -> str:
        """Atomic write — write to a tempfile then rename."""
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(self.model_dump_json(indent=2))
        os.replace(tmp, path)
        logger.info("IR bundle saved → %s", path)
        return path

    def save_for_target(self, target_name: str, cwd: str | None = None) -> str:
        path = os.path.join(
            cwd or os.getcwd(), self._bundle_filename(target_name)
        )
        return self.save(path)

    @classmethod
    def load(cls, path: str) -> "IRBundle":
        with open(path) as f:
            return cls.model_validate_json(f.read())

    @classmethod
    def find_default_bundle(
        cls, target_name: str | None = None, cwd: str | None = None
    ) -> str | None:
        """Locate an IR bundle on disk.

        If `target_name` is supplied: returns `<cwd>/<target_name>_ir.json`
        if it
        exists, else None.
        Otherwise: returns the newest `*_ir.json` in cwd, or None.
        """
        cwd = cwd or os.getcwd()
        if target_name:
            candidate = os.path.join(cwd, cls._bundle_filename(target_name))
            return candidate if os.path.exists(candidate) else None
        matches = glob.glob(os.path.join(cwd, "*_ir.json"))
        if not matches:
            return None
        matches.sort(key=os.path.getmtime, reverse=True)
        return matches[0]

    # --- Convenience Mutator Instance Methods ---

    def append_stage(
        self, phase: str, status: str, started_at: datetime, notes: str = ""
    ) -> None:
        self.stage_history.append(
            StageHistoryEntry(
                phase=phase,
                status=status,
                started_at=started_at,
                ended_at=datetime.now(),
                notes=notes,
            )
        )

    def attach_version(self, display_name: str, description: str) -> None:
        self.version_checkpoints.append((display_name, description))

    def attach_grouping(self, groupings: dict[str, Any]) -> None:
        self.grouping = groupings
