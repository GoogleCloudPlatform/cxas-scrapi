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

"""Utility functions for GECX evaluation coverage script."""

import math
import re
from typing import Any, Dict, List

from models import InstructionSegment, InstructionCategory


def find_target_agent(obj: Any) -> List[str]:
    """Recursively searches for 'targetAgent' fields in an object.

    Args:
        obj: The parsed configuration object (dict, list, etc.) to search.

    Returns:
        A list of target agent names discovered within the object.
    """
    target_agents: List[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "targetAgent":
                target_agents.append(v)
            else:
                target_agents.extend(find_target_agent(v))
    elif isinstance(obj, list):
        for item in obj:
            target_agents.extend(find_target_agent(item))
    return target_agents


def dot_product(v1: List[float], v2: List[float]) -> float:
    """Calculates the dot product of two vectors.

    Args:
        v1: The first vector of floating-point numbers.
        v2: The second vector of floating-point numbers.

    Returns:
        The scalar dot product of the two vectors.
    """
    return sum(a * b for a, b in zip(v1, v2, strict=True))


def magnitude(v: List[float]) -> float:
    """Calculates the Euclidean magnitude of a vector.

    Args:
        v: A vector of floating-point numbers.

    Returns:
        The Euclidean norm (magnitude) of the vector.
    """
    return math.hypot(*v)


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """Calculates the cosine similarity between two vectors.

    Args:
        v1: The first vector of floating-point numbers.
        v2: The second vector of floating-point numbers.

    Returns:
        The cosine similarity float between -1.0 and 1.0 (0.0 if zero norm).
    """
    mag1 = magnitude(v1)
    mag2 = magnitude(v2)
    if not mag1 or not mag2:
        return 0.0
    return dot_product(v1, v2) / (mag1 * mag2)


def parse_instruction_content(
    content: str, agent_name: str
) -> List[InstructionSegment]:
    """Parses instruction file content and splits it into structured segments.

    Supports both XML-tagged sections (e.g., <Rules>...) and raw files
    (fallback to 'Rules').

    Args:
        content: The raw text content of the instruction file.
        agent_name: The name of the agent owning the instructions.

    Returns:
        A list of instruction segment dataclasses containing full text and
        metadata.
    """
    instruction_segments: List[InstructionSegment] = []

    def add_instruction_segment(
        quote_lines: List[str], cat_name: str, a_name: str
    ) -> None:
        q_text = " ".join(quote_lines).strip()
        if len(q_text) > 10:
            q_text = re.sub(r"^\d+[\.\)]\s*", "", q_text)
            q_text = re.sub(r"^[\-\*]\s*", "", q_text)
            q_text = q_text.strip()
            directive_title = " ".join(q_text.split()[:5])
            if len(directive_title) < len(q_text):
                directive_title += "..."

            quote_val = (
                f'"{q_text[:200]}..."' if len(q_text) > 200 else f'"{q_text}"'
            )

            cat_enum = InstructionCategory.RULES
            try:
                cat_enum = InstructionCategory(cat_name)
            except ValueError:
                pass

            instruction_segments.append(
                InstructionSegment(
                    agent=a_name,
                    category=cat_enum,
                    directive=directive_title,
                    quote=quote_val,
                    full_text=q_text,
                )
            )

    def chunk_lines_into_segments(
        lines_list: List[str], cat_name: str
    ) -> None:
        current_quote = []
        for line in lines_list:
            stripped = line.strip()
            if not stripped:
                continue
            if (
                re.match(r"^\d+[\.\)]\s*", stripped)
                or stripped.startswith("-")
                or stripped.startswith("*")
            ):
                if current_quote:
                    add_instruction_segment(
                        current_quote, cat_name, agent_name
                    )
                current_quote = [stripped]
            else:
                current_quote.append(stripped)
        if current_quote:
            add_instruction_segment(current_quote, cat_name, agent_name)

    sections = re.findall(r"<([a-zA-Z0-9_-]+)>(.*?)</\1>", content, re.DOTALL)

    for tag, text in sections:
        category = tag.replace("_", " ").title()
        chunk_lines_into_segments(text.split("\n"), category)

    if not sections:
        chunk_lines_into_segments(content.split("\n"), "Rules")

    return instruction_segments
