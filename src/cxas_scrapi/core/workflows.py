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

"""Core Workflows class for CXAS Scrapi."""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class VariableMetadata:
    variable: str
    description: Optional[str] = None
    is_required: Optional[bool] = None


@dataclass
class Reasoning:
    prompt: str
    output_variables: Optional[List[VariableMetadata]] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {"prompt": self.prompt}
        if self.output_variables:
            result["output_variables"] = [
                asdict(v) for v in self.output_variables
            ]
        return result


@dataclass
class PythonCode:
    code: str

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code}


@dataclass
class WorkflowAction:
    # Only one of these should be set
    reasoning: Optional[Reasoning] = None
    python_code: Optional[PythonCode] = None
    # Add other action types (ToolCall, TellUser, AskUser) as needed

    def to_dict(self) -> Dict[str, Any]:
        if self.reasoning:
            return {"reasoning": self.reasoning.to_dict()}
        if self.python_code:
            return {"python_code": self.python_code.to_dict()}
        return {}


@dataclass
class Transition:
    workflow_step_id: str
    condition_type: Optional[str] = (
        None  # Placeholder for oneof condition logic
    )

    def to_dict(self) -> Dict[str, Any]:
        return {"workflow_step_id": self.workflow_step_id}


@dataclass
class WorkflowStep:
    id: str
    display_name: str
    action: WorkflowAction
    transitions: List[Transition] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "action": self.action.to_dict(),
            "transitions": [t.to_dict() for t in self.transitions],
        }


@dataclass
class WorkflowAgent:
    workflow_steps: List[WorkflowStep] = field(default_factory=list)
    root_step_id: Optional[str] = None

    def add_step(self, step: WorkflowStep):
        self.workflow_steps.append(step)
        if not self.root_step_id:
            self.root_step_id = step.id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workflow_steps": [step.to_dict() for step in self.workflow_steps],
            "root_step_id": self.root_step_id,
        }
