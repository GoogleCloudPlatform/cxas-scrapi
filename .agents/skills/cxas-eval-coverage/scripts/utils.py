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
from typing import Any

import models

_NUMBERED_LIST_RE = re.compile(r"^\d+[\.\)]\s*")
_BULLET_LIST_RE = re.compile(r"^[\-\*]\s*")
_XML_TAG_RE = re.compile(r"<([a-zA-Z0-9_-]+)>(.*?)</\1>", re.DOTALL)


def _add_instruction_segment(
    quote_lines: list[str],
    cat_name: str,
    a_name: str,
    instruction_segments: list[models.InstructionSegment],
) -> None:
    """Formats and appends an instruction segment to the segments list."""
    q_text = " ".join(quote_lines).strip()
    if len(q_text) > 10:
        q_text = _NUMBERED_LIST_RE.sub("", q_text)
        q_text = _BULLET_LIST_RE.sub("", q_text)
        q_text = q_text.strip()
        directive_title = " ".join(q_text.split()[:5])
        if len(directive_title) < len(q_text):
            directive_title += "..."

        quote_val = (
            f'"{q_text[:200]}..."' if len(q_text) > 200 else f'"{q_text}"'
        )

        cat_enum = models.InstructionCategory.RULES
        try:
            cat_enum = models.InstructionCategory(cat_name)
        except ValueError:
            pass

        instruction_segments.append(
            models.InstructionSegment(
                agent=a_name,
                category=cat_enum,
                directive=directive_title,
                quote=quote_val,
                full_text=q_text,
            )
        )


def _chunk_lines_into_segments(
    lines_list: list[str],
    cat_name: str,
    agent_name: str,
    instruction_segments: list[models.InstructionSegment],
) -> None:
    """Chunks instruction lines and appends segments to the segments list."""
    current_quote = []
    for line in lines_list:
        stripped = line.strip()
        if not stripped:
            continue
        if (
            _NUMBERED_LIST_RE.search(stripped)
            or stripped.startswith("-")
            or stripped.startswith("*")
        ):
            if current_quote:
                _add_instruction_segment(
                    current_quote, cat_name, agent_name, instruction_segments
                )
            current_quote = [stripped]
        else:
            current_quote.append(stripped)
    if current_quote:
        _add_instruction_segment(
            current_quote, cat_name, agent_name, instruction_segments
        )


def find_target_agent(obj: Any) -> list[str]:
    """Recursively searches for 'targetAgent' fields in an object.

    Args:
        obj: The parsed configuration object (dict, list, etc.) to search.

    Returns:
        A list of target agent names discovered within the object.
    """
    target_agents: list[str] = []
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


def dot_product(v1: list[float], v2: list[float]) -> float:
    """Calculates the dot product of two vectors.

    Args:
        v1: The first vector of floating-point numbers.
        v2: The second vector of floating-point numbers.

    Returns:
        The scalar dot product of the two vectors.
    """
    return sum(a * b for a, b in zip(v1, v2, strict=True))


def magnitude(v: list[float]) -> float:
    """Calculates the Euclidean magnitude of a vector.

    Args:
        v: A vector of floating-point numbers.

    Returns:
        The Euclidean norm (magnitude) of the vector.
    """
    return math.hypot(*v)


def cosine_similarity(v1: list[float], v2: list[float]) -> float:
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
) -> list[models.InstructionSegment]:
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
    instruction_segments: list[models.InstructionSegment] = []

    sections = list(_XML_TAG_RE.finditer(content))

    last_end = 0
    for match in sections:
        tag = match.group(1)
        text = match.group(2)
        start = match.start()

        # Capture any untagged text appearing before this XML tag as "Rules"
        untagged_text = content[last_end:start].strip()
        if untagged_text:
            _chunk_lines_into_segments(
                untagged_text.split("\n"),
                "Rules",
                agent_name,
                instruction_segments,
            )

        category = tag.replace("_", " ").title()
        _chunk_lines_into_segments(
            text.split("\n"), category, agent_name, instruction_segments
        )
        last_end = match.end()

    # Capture any remaining untagged text after the final XML tag
    remaining_text = content[last_end:].strip()
    if remaining_text:
        _chunk_lines_into_segments(
            remaining_text.split("\n"),
            "Rules",
            agent_name,
            instruction_segments,
        )

    return instruction_segments
