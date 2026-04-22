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
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


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
