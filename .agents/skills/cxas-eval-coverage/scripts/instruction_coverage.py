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

"""Instruction-related evaluation coverage analysis functions."""

import asyncio
from collections import defaultdict
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field
import tenacity #not a brand name, this is an import

from cxas_scrapi.utils.gemini import GeminiGenerate
from utils import cosine_similarity
from models import (
    InstructionSegment,
    InstructionCategory,
    CoverageStatus,
    CategorizationResult,
    SentimentAnalysisResult,
    InstructionSegmentCoverageResult,
)


async def analyze_instruction_categories(
    instruction_segments: List[InstructionSegment],
    gemini_client: Optional[GeminiGenerate] = None,
    errors: Optional[List[str]] = None,
) -> List[InstructionSegment]:
    """Runs LLM classification on instruction segments to categorize them."""
    if not gemini_client or not instruction_segments:
        return instruction_segments

    print(
        f"Categorizing {len(instruction_segments)} instruction segment(s) "
        "using LLM..."
    )

    sem = asyncio.Semaphore(5)

    async def process_segment(segment: InstructionSegment) -> None:
        async with sem:
            prompt = f"""
            Analyze the following GECX AI Agent instruction segment.

            Instruction:
            <INSTRUCTION>
            {segment.full_text}
            </INSTRUCTION>

            Determine if this instruction is testable. An instruction is NOT
            testable if it's pure conversational filler, general formatting
            rules not related to logic, or boilerplate greetings (e.g., "Greet
            the user nicely", "Always say hello"). An instruction IS testable if
            it describes specific functional intents, API/tool execution
            business logic, conditional routing logic, strict validation
            constraints, or distinctive behavioral/safety guardrails.

            If it is testable, categorize it as one of:
            - 'Functional Intent': Explicit actions, API executions, or data
              retrievals.
            - 'Behavioral Constraint': Quality, tone, persona, or safety
              guardrails.

            If it is not testable, set `is_testable` to false and categorize as
            'Untestable'.
            """
            try:
                llm_response = await gemini_client.generate_async(
                    prompt=prompt,
                    response_mime_type="application/json",
                    response_schema=CategorizationResult,
                    temperature=0.0,
                )
                if llm_response:
                    is_testable = getattr(llm_response, "is_testable", True)
                    cat = getattr(
                        llm_response, "category", "Functional Intent"
                    )
                    if isinstance(llm_response, dict):
                        is_testable = llm_response.get("is_testable", True)
                        cat = llm_response.get(
                            "category", "Functional Intent"
                        )

                    segment.is_testable = is_testable

                    if not is_testable or "untestable" in cat.lower():
                        segment.category = InstructionCategory.UNTESTABLE
                        segment.is_testable = False
                    elif "functional" in cat.lower():
                        segment.category = InstructionCategory.FUNCTIONAL_INTENT
                    elif (
                        "behavioral" in cat.lower()
                        or "persona" in cat.lower()
                        or "constraint" in cat.lower()
                    ):
                        segment.category = InstructionCategory.BEHAVIORAL_CONSTRAINT
                    else:
                        try:
                            segment.category = InstructionCategory(cat)
                        except ValueError:
                            segment.category = InstructionCategory.RULES
            except (OSError, UnicodeDecodeError) as e:
                err_msg = (
                    f"LLM categorization failed for segment "
                    f"'{segment.directive}': {e}"
                )
                print(f"Warning: {err_msg}")
                if errors is not None:
                    errors.append(err_msg)
                segment.is_testable = True
                if segment.category not in [
                    InstructionCategory.FUNCTIONAL_INTENT,
                    InstructionCategory.BEHAVIORAL_CONSTRAINT,
                    InstructionCategory.UNTESTABLE,
                ]:
                    orig_cat = segment.category.value if isinstance(segment.category, InstructionCategory) else "Rules"
                    if (
                        "rule" in orig_cat.lower()
                        or "persona" in orig_cat.lower()
                    ):
                        segment.category = InstructionCategory.BEHAVIORAL_CONSTRAINT
                    else:
                        segment.category = InstructionCategory.FUNCTIONAL_INTENT

    tasks = [process_segment(seg) for seg in instruction_segments]
    await asyncio.gather(*tasks)
    return instruction_segments


async def extract_instruction_coverage(
    instruction_segments: List[InstructionSegment],
    eval_chunks: List[Dict[str, Any]],
    called_tools: Set[str],
    gemini_client: Optional[GeminiGenerate] = None,
    errors: Optional[List[str]] = None,
) -> Tuple[
    List[InstructionSegment],
    List[InstructionSegment],
]:
    """Uses Vector Embeddings and LLM-as-a-judge to determine instruction
    segment coverage against pre-computed eval chunks."""
    if not gemini_client:
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get(
            "GCP_PROJECT"
        )
        location = os.environ.get("GOOGLE_CLOUD_LOCATION") or "global"

        print(f"Initializing Gemini Generate for (Project: {project_id})...")
        gemini_client = GeminiGenerate(
            project_id=project_id,
            location=location,
            model_name="gemini-2.5-flash",
        )

    instruction_segments_texts = [
        instruction_segment.full_text
        for instruction_segment in instruction_segments
    ]
    chunk_texts = [chunk["text"] for chunk in eval_chunks]

    emb_sem = asyncio.Semaphore(3)

    async def batch_generate_embeddings_async(
        texts: List[str], batch_size: int = 100
    ) -> List[Any]:
        if not texts:
            return []

        batches = [
            texts[i : i + batch_size] for i in range(0, len(texts), batch_size)
        ]

        @tenacity.retry(
            wait=tenacity.wait_exponential(multiplier=1, min=2, max=10),
            stop=tenacity.stop_after_attempt(5),
            retry=tenacity.retry_if_exception_type((OSError, UnicodeDecodeError)),
            reraise=True
        )
        async def _call_generate_embeddings(batch):
            return await asyncio.to_thread(
                gemini_client.generate_embeddings, contents=batch
            )

        async def get_batch_embeddings(batch) -> List[Any]:
            async with emb_sem:
                try:
                    return await _call_generate_embeddings(batch)
                except Exception as e:
                    err_msg = f"Failed to generate embeddings for batch: {e}"
                    print(f"Warning: {err_msg}")
                    if errors is not None:
                        errors.append(err_msg)
                    return [None] * len(batch)

        tasks = [get_batch_embeddings(b) for b in batches]
        results = await asyncio.gather(*tasks)

        embeddings = []
        for res in results:
            embeddings.extend(res)
        return embeddings

    instruction_segment_embeddings = []
    chunk_embeddings = []

    if instruction_segments_texts:
        print(
            f"Generating embeddings for {len(instruction_segments_texts)} "
            "instruction segment(s)..."
        )
        instruction_segment_embeddings = (
            await batch_generate_embeddings_async(instruction_segments_texts)
        )

    if chunk_texts:
        print(f"Generating embeddings for {len(chunk_texts)} eval chunk(s)...")
        chunk_embeddings = await batch_generate_embeddings_async(chunk_texts)

    judge_sem = asyncio.Semaphore(5)

    async def run_llm_judge(
        instruction_text: str,
        candidate_chunks: List[Dict[str, Any]],
        idx: int,
    ) -> Tuple[int, bool, List[int], str]:
        async with judge_sem:
            chunks_formatted_text = ""
            for c_idx, c in enumerate(candidate_chunks):
                chunks_formatted_text += (
                    f"\n--- CANDIDATE CHUNK {c_idx} ---\n{c['text']}\n"
                )

            prompt = f"""
            You are an expert LLM as a Judge determining evaluation coverage
            for an AI Agent.

            Agent Instruction to Test:
            <INSTRUCTION>
            {instruction_text}
            </INSTRUCTION>

            Candidate Evaluation Chunks:
            {chunks_formatted_text}

            Analyze the Candidate Evaluation Chunks carefully.
            Determine which of these evaluation chunks explicitly test or
            provide a natural opportunity to demonstrate that the Agent follows
            the provided Agent Instruction.
            - For general persona, tone, or behavioral constraints (e.g., "be
              polite", "sound professional", "be patient"), if the evaluation
              chunk allows the agent to carry out a natural conversation or
              achieve its goal under these guidelines, consider it covered.
            - Only mark as uncovered if the instruction contains a highly
              specific rule or guardrail that is explicitly not triggered,
              tested, or challenged by the evaluation chunk.
            Answer true in `is_covered` if at least one evaluation chunk covers
            or allows natural demonstration of the instruction, and list the
            0-based indices of all covering chunks in `covering_chunk_indices`.
            """
            try:
                llm_response = await gemini_client.generate_async(
                    prompt=prompt,
                    response_mime_type="application/json",
                    response_schema=InstructionSegmentCoverageResult,
                    temperature=0.0,
                )

                if llm_response:
                    is_cov = getattr(llm_response, "is_covered", False)
                    c_indices = getattr(llm_response, "covering_chunk_indices", [])
                    reasoning = getattr(llm_response, "reasoning", "")

                    if isinstance(llm_response, dict):
                        is_cov = llm_response.get("is_covered", False)
                        c_indices = llm_response.get("covering_chunk_indices", [])
                        reasoning = llm_response.get("reasoning", "")

                    return idx, is_cov, c_indices, reasoning
            except (OSError, UnicodeDecodeError) as e:
                err_msg = f"LLM call failed for instruction segment {idx}: {e}"
                print(err_msg)
                if errors is not None:
                    errors.append(err_msg)

            return idx, False, [], ""

    # Initialize coverage states
    segment_states = []
    for i, instruction_segment in enumerate(instruction_segments):
        covered = False
        covering_evals = set()
        covering_chunk_texts = []

        text_to_check = instruction_segment.full_text.lower()
        match_tool = re.search(r"\{@TOOL[:\s]+([^}]+)\}", text_to_check)
        if match_tool:
            tool_name = match_tool.group(1).strip()
            if tool_name in called_tools:
                covered = True
                for chunk in eval_chunks:
                    if tool_name in chunk["text"]:
                        covering_evals.add(chunk["eval_name"])
                        covering_chunk_texts.append(chunk["text"])

        segment_states.append(
            {
                "covered": covered,
                "covering_evals": covering_evals,
                "covering_chunk_texts": covering_chunk_texts,
                "candidate_chunks": [],
            }
        )

    # 2. Prepare tasks for LLM Judge where needed
    llm_tasks = []
    for i, instruction_segment in enumerate(instruction_segments):
        if segment_states[i]["covered"]:
            continue

        if (
            i < len(instruction_segment_embeddings)
            and instruction_segment_embeddings[i]
            and chunk_embeddings
        ):
            i_embedding = instruction_segment_embeddings[i]
            similarities = []
            for j, c_embedding in enumerate(chunk_embeddings):
                if c_embedding:
                    sim = cosine_similarity(i_embedding, c_embedding)
                    similarities.append((sim, j))
                else:
                    similarities.append((0.0, j))

            similarities.sort(reverse=True, key=lambda x: x[0])
            top_candidates = similarities[:5]

            candidate_chunks = [
                eval_chunks[idx] for sim, idx in top_candidates if sim > 0.0
            ]

            if candidate_chunks:
                segment_states[i]["candidate_chunks"] = candidate_chunks
                llm_tasks.append(
                    run_llm_judge(
                        instruction_segment.full_text,
                        candidate_chunks,
                        i,
                    )
                )

    # 3. Execute LLM calls concurrently
    if llm_tasks:
        print(
            f"Running {len(llm_tasks)} instruction coverage "
            "LLM-as-a-judge calls in parallel..."
        )
        llm_results = await asyncio.gather(*llm_tasks)

        for idx, is_cov, c_indices, reasoning in llm_results:
            segment_states[idx]["reasoning"] = reasoning
            candidates = segment_states[idx]["candidate_chunks"]
            if is_cov and c_indices:
                segment_states[idx]["covered"] = True
                for c_idx in c_indices:
                    if 0 <= c_idx < len(candidates):
                        covering_chunk = candidates[c_idx]
                        segment_states[idx]["covering_evals"].add(
                            covering_chunk["eval_name"]
                        )
                        segment_states[idx]["covering_chunk_texts"].append(
                            covering_chunk["text"]
                        )

    # 4. Finalize segments
    covered_instruction_segments = []
    for i, instruction_segment in enumerate(instruction_segments):
        state = segment_states[i]

        # Add details for JSON export
        if state["covered"]:
            instruction_segment.reasoning = state.get(
                "reasoning", "Matched via direct tool reference."
            )
        else:
            instruction_segment.reasoning = state.get(
                "reasoning", "No covering evaluation found."
            )

        instruction_segment.covering_chunk_texts = state.get(
            "covering_chunk_texts", []
        )

        if state["covered"]:
            instruction_segment.covered = CoverageStatus.COVERED
            covered_instruction_segments.append(instruction_segment)
        else:
            instruction_segment.covered = CoverageStatus.UNCOVERED

        evals_set = state["covering_evals"]
        instruction_segment.evals = sorted(list(evals_set))

    return instruction_segments, covered_instruction_segments


class DesiredTransfersResult(BaseModel):
    """Schema for the LLM evaluation of desired agent transfers."""

    desired_target_agents: List[str] = Field(
        description=(
            "The exact names of the target agents that this agent could "
            "potentially transfer to."
        )
    )
    reasoning: str = Field(
        description="A brief explanation of how these targets were identified."
    )


async def determine_desired_transfers_with_llm(
    agent_directories: Dict[str, Path],
    declared_transfers: List[Tuple[str, str]],
    gemini_client: Optional[GeminiGenerate] = None,
    errors: Optional[List[str]] = None,
) -> Set[Tuple[str, str]]:
    """Uses LLM to determine which declared transfers are desired."""
    if not gemini_client or not declared_transfers:
        return set()

    desired_transfers = set()

    outbound_transfers = defaultdict(list)
    for from_a, to_a in declared_transfers:
        outbound_transfers[from_a].append(to_a)

    sem = asyncio.Semaphore(5)

    async def process_agent(
        agent_name: str, possible_targets: List[str]
    ) -> None:
        async with sem:
            if agent_name not in agent_directories:
                return

            agent_dir = agent_directories[agent_name]

            # Read all relevant files for this agent
            files_to_check = []
            files_to_check.extend(agent_dir.glob("instruction.*"))
            files_to_check.extend(agent_dir.glob("*.json"))
            files_to_check.extend(agent_dir.glob("*.yaml"))
            files_to_check.extend(agent_dir.glob("*.yml"))
            files_to_check.extend(
                agent_dir.glob("**/*callbacks*/*/python_code.py")
            )

            content_parts = []
            for f in files_to_check:
                if not f.is_file():
                    continue
                try:
                    text = f.read_text(encoding="utf-8")
                    if len(text) > 10000:
                        text = text[:10000] + "... (truncated)"
                    content_parts.append(f"--- FILE: {f.name} ---\n{text}\n")
                except (OSError, UnicodeDecodeError):
                    pass

            agent_files_content = "\n".join(content_parts)
            if not agent_files_content.strip():
                return

            print(
                f"Determining desired transfers for '{agent_name}' with LLM..."
            )

            prompt = f"""
            You are an expert analyzing a GECX conversational agent's
            configuration and logic.

            Agent Name: {agent_name}
            Theoretically Possible Target Agents: {json.dumps(possible_targets)}

            Based on the agent's instructions, configuration, and callback logic
            provided below, determine which of the 'Theoretically Possible
            Target Agents' this agent actually intends to transfer to.
            A transfer might be explicitly mentioned in the instructions (e.g.,
            "transfer to the billing agent" or a tool call like
            `set_active_flow` with flow="billing") or within the callback logic
            (e.g., `Part.from_agent_transfer`).
            Only include targets that have clear evidence of being an intended
            destination.

            Agent Files Content:
            {agent_files_content}
            """

            try:
                llm_response = await gemini_client.generate_async(
                    prompt=prompt,
                    response_mime_type="application/json",
                    response_schema=DesiredTransfersResult,
                    temperature=0.0,
                )

                if llm_response:
                    targets = getattr(llm_response, "desired_target_agents", [])
                    if isinstance(llm_response, dict):
                        targets = llm_response.get("desired_target_agents", [])

                    for t in targets:
                        for pt in possible_targets:
                            if t.lower() == pt.lower():
                                desired_transfers.add((agent_name, pt))
                                break
            except (OSError, UnicodeDecodeError) as e:
                err_msg = (
                    f"LLM desired transfer extraction failed for "
                    f"'{agent_name}': {e}"
                )
                print(f"Warning: {err_msg}")
                if errors is not None:
                    errors.append(err_msg)

    tasks = [
        process_agent(agent_name, targets)
        for agent_name, targets in outbound_transfers.items()
    ]
    await asyncio.gather(*tasks)

    return desired_transfers
