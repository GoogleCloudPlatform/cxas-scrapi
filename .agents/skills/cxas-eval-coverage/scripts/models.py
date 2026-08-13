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

"""Domain models for GECX evaluation coverage analyzer."""

import dataclasses
import enum
import pathlib
from typing import Any

import pydantic

dataclass = dataclasses.dataclass
field = dataclasses.field
Enum = enum.Enum
Path = pathlib.Path


class CoverageStatus(str, Enum):
    """Status of GECX instruction coverage."""

    COVERED = "Yes"
    UNCOVERED = "No"
    UNTESTABLE = "N/A"


class InstructionCategory(str, Enum):
    """Category of GECX instruction."""

    FUNCTIONAL_INTENT = "Functional Intent"
    BEHAVIORAL_CONSTRAINT = "Behavioral Constraint"
    UNTESTABLE = "Untestable"
    RULES = "Rules"


@dataclass
class InstructionSegment:
    """Represents a segment of parser instruction text with coverage stats."""

    agent: str
    category: InstructionCategory
    directive: str
    quote: str
    full_text: str
    is_testable: bool = True
    covered: CoverageStatus = CoverageStatus.UNCOVERED
    reasoning: str = ""
    evals: list[str] = field(default_factory=list)
    covering_chunk_texts: list[str] = field(default_factory=list)


@dataclass
class AgentProjectData:
    """A unified data model representing the fully ingested GECX project."""

    agent_dir: Path
    all_tools: set[str] = field(default_factory=set)
    eval_files: list[Path] = field(default_factory=list)

    # Aggregated tool coverage metrics (from ingestion)
    called_tools: set[str] = field(default_factory=set)
    covered_tools: set[str] = field(default_factory=set)
    phantom_tools_by_file: dict[Path, set[str]] = field(default_factory=dict)

    # Sub-agent transitions/transfers
    declared_transfers: list[tuple[str, str]] = field(default_factory=list)
    parent_child_transfers: set[tuple[str, str]] = field(default_factory=set)
    covered_transfers: dict[tuple[str, str], list[str]] = field(
        default_factory=dict
    )
    desired_transfers: set[tuple[str, str]] = field(default_factory=set)
    agent_directories: dict[str, Path] = field(default_factory=dict)

    # Callback coverage metrics
    all_callbacks: set[str] = field(default_factory=set)
    covered_callbacks: set[str] = field(default_factory=set)

    # Pre-computed evaluation chunks for instruction similarity judge
    eval_chunks: list[dict[str, Any]] = field(default_factory=list)

    # Ingested instruction files and raw segments
    instruction_files: list[Path] = field(default_factory=list)
    instruction_segments: list[InstructionSegment] = field(default_factory=list)


class CategorizationResult(pydantic.BaseModel):
    """Schema for LLM categorization of instruction segments."""

    is_testable: bool = pydantic.Field(
        description=(
            "True if this is a substantive, testable instruction. False if it "
            "is conversational filler, generic greeting, or non-testable "
            "boilerplate."
        )
    )
    category: str = pydantic.Field(
        description=(
            "Category of the instruction: 'Functional Intent', "
            "'Behavioral Constraint', or 'Untestable'"
        )
    )
    reasoning: str = pydantic.Field(description="Reason for the decision")


class SentimentAnalysisResult(pydantic.BaseModel):
    """Schema for LLM sentiment analysis of user prompts."""

    has_behavioral_diversity: bool = pydantic.Field(
        description=(
            "True if the test suite contains phrasing aimed at testing the "
            "personal, role or behaviour of the agent. False otherwise."
        )
    )
    reasoning: str = pydantic.Field(description="Reason for the decision")


class InstructionSegmentCoverageResult(pydantic.BaseModel):
    """Schema for the LLM evaluation of instruction segment coverage."""

    is_covered: bool = pydantic.Field(
        description=(
            "true if at least one evaluation chunk explicitly tests the "
            "instruction, false otherwise."
        )
    )
    covering_chunk_indices: list[int] = pydantic.Field(
        default_factory=list,
        description=(
            "The 0-based indices of all candidate chunks that test the "
            "instruction. Empty list if none."
        ),
    )
    reasoning: str = pydantic.Field(
        description="A brief reasoning string explaining the decision."
    )
