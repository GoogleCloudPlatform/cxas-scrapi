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
from typing import Any, Dict, List, Optional

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
    page_data: Dict[str, Any]


class DFCXFlowModel(BaseModel):
    """Represents a Flow with its Pages."""

    flow_id: str  # The full resource name of the flow
    flow_data: Dict[str, Any]  # The raw flow data
    pages: List[DFCXPageModel] = Field(default_factory=list)


class DFCXAgentIR(BaseModel):
    """Represents the full extracted state of a DFCX Agent."""

    name: str  # The full resource name of the DFCX agent
    display_name: str  # The human-readable name of the DFCX agent
    default_language_code: str
    supported_language_codes: List[str] = Field(default_factory=list)
    time_zone: Optional[str] = None
    description: Optional[str] = None
    start_flow: Optional[str] = None
    start_playbook: Optional[str] = None
    intents: List[Dict[str, Any]] = Field(default_factory=list)
    tools: List[Dict[str, Any]] = Field(default_factory=list)
    entity_types: List[Dict[str, Any]] = Field(default_factory=list)
    webhooks: List[Dict[str, Any]] = Field(default_factory=list)
    flows: List[DFCXFlowModel] = Field(default_factory=list)
    playbooks: List[Dict[str, Any]] = Field(default_factory=list)
    test_cases: List[Dict[str, Any]] = Field(default_factory=list)
    generative_settings: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    playbook_generative_settings: Optional[Dict[str, Any]] = None
    generators: List[Dict[str, Any]] = Field(default_factory=list)
    agent_transition_route_groups: List[Dict[str, Any]] = Field(
        default_factory=list
    )
    no_speech_timeout: Optional[str] = "7s"
    code_blocks: List[Dict[str, Any]] = Field(default_factory=list)


# --- Target Migration IR Models ---


class IRMetadata(BaseModel):
    """Metadata for the migration target."""

    app_name: str  # The display name of the target Polysynth app
    app_id: Optional[str] = None  # The UUID generated for the new Polysynth app
    app_resource_name: Optional[str] = (
        None  # The full resource name of the Polysynth app
    )
    default_model: str = "gemini-2.5-flash-001"


class IRTool(BaseModel):
    """Represents a tool in the target IR."""

    id: str  # Short ID (e.g., "tool_billing")
    name: str  # Full resource name (projects/.../tools/...)
    type: str  # "TOOLSET", "TOOL", "PYTHON"
    payload: Dict[str, Any]
    operation_ids: List[str] = Field(default_factory=list)
    status: MigrationStatus = MigrationStatus.COMPILED


class IRAgent(BaseModel):
    """Represents a Generative Agent (Playbook or Flow) in the target IR."""

    type: str  # "FLOW", "PLAYBOOK"
    display_name: str
    description: Optional[str] = None
    instruction: str  # The generated PIF XML
    tools: List[str] = Field(default_factory=list)  # Resource names
    toolsets: List[Dict[str, Any]] = Field(
        default_factory=list
    )  # [{"toolset": ..., "toolIds": []}]
    model_settings: Dict[str, Any] = Field(default_factory=dict)
    raw_data: Optional[Dict[str, Any]] = None  # Original DFCX data
    blueprint: Optional[Dict[str, Any]] = None  # Used by Flows
    callbacks: Optional[Dict[str, Any]] = None  # Used by Flows
    status: MigrationStatus = MigrationStatus.COMPILED
    resource_name: Optional[str] = None  # Populated after deployment


class MigrationIR(BaseModel):
    """The full state of the migration offline workspace."""

    metadata: IRMetadata
    parameters: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    tools: Dict[str, IRTool] = Field(default_factory=dict)
    agents: Dict[str, IRAgent] = Field(default_factory=dict)
    routing_edges: List[Dict[str, Any]] = Field(default_factory=list)
    test_cases: Dict[str, Any] = Field(default_factory=dict)
    test_runs: Dict[str, Any] = Field(default_factory=dict)
    optimization_logs: Dict[str, Any] = Field(default_factory=dict)


class MigrationConfig(BaseModel):
    """Configuration for the migration process."""

    project_id: str
    target_name: str
    env: str = "PROD"
    model: str
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
    source_agent_data_override: Optional[DFCXAgentIR] = None

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
    ended_at: Optional[datetime] = None
    notes: str = ""


class IRBundle(BaseModel):
    """Persisted state shared across migrate / stage1 / stage2."""

    schema_version: str = "2"
    created_at: datetime = Field(default_factory=datetime.now)
    config: MigrationConfig
    source_agent_data: DFCXAgentIR
    ir: MigrationIR
    stage_history: List[StageHistoryEntry] = Field(default_factory=list)
    app_url: Optional[str] = None
    version_checkpoints: List[tuple[str, str]] = Field(default_factory=list)
    grouping: Optional[Dict[str, Any]] = (
        None  # populated when Stage 1 consolidates
    )
    pre_consolidation_ir: Optional[MigrationIR] = None

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

    def save_for_target(
        self, target_name: str, cwd: Optional[str] = None
    ) -> str:
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
        cls, target_name: Optional[str] = None, cwd: Optional[str] = None
    ) -> Optional[str]:
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

    def attach_grouping(self, groupings: Dict[str, Any]) -> None:
        self.grouping = groupings
