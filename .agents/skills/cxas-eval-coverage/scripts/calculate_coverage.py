#!/usr/bin/env python3
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

"""Main execution script for GECX evaluation coverage analyzer."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from cxas_scrapi.utils.gcs_utils import GCSUtils

from ingestion import ingest_agent_project
from instruction_coverage import (
    analyze_instruction_categories,
    determine_desired_transfers_with_llm,
    extract_instruction_coverage,
)

from cxas_scrapi.utils.gemini import GeminiGenerate
from models import InstructionSegment, CoverageStatus, InstructionCategory
import dataclasses


def generate_json_report(
    output_file: Path,
    total_tools: Set[str],
    covered_tools: Set[str],
    phantom_tools_by_file: Dict[Path, Set[str]],
    eval_files: List[Path],
    declared_transfers: List[Tuple[str, str]],
    covered_transfers: Dict[Tuple[str, str], List[str]],
    instruction_segments: List[InstructionSegment],
    covered_instruction_segments: List[InstructionSegment],
    instruction_files: List[Path],
    agent_dir: Path,
    total_callbacks: Set[str],
    covered_callbacks: Set[str],
    desired_transfers: Set[Tuple[str, str]],
    unused_evals: List[str],
    errors: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Generates a JSON coverage report and returns the data.

    Args:
        output_file: Path where the JSON report will be written.
        total_tools: Set of all declared tool names.
        covered_tools: Set of tool names covered by unit tests.
        phantom_tools_by_file: Mapping of evaluation files to phantom tools.
        eval_files: List of all evaluation and test files scanned.
        declared_transfers: List of declared sub-agent transitions.
        covered_transfers: Mapping of transitions to covering evaluations.
        instruction_segments: List of all parsed instruction segments.
        covered_instruction_segments: List of covered instruction segments.
        instruction_files: List of instruction files parsed.
        agent_dir: Root directory of the agent project.
        total_callbacks: Set of all discovered callbacks.
        covered_callbacks: Set of covered callbacks.
        desired_transfers: Set of desired sub-agent transfers.
        unused_evals: List of unused evaluation names.
        errors: Optional list of execution error messages.

    Returns:
        A dictionary containing the complete structured coverage report data.
    """
    uncovered_tools = total_tools - covered_tools
    tool_coverage_pct = (
        (len(covered_tools) / len(total_tools) * 100.0) if total_tools else 0.0
    )

    total_segments = len(instruction_segments)
    total_covered = len(covered_instruction_segments)
    overall_segment_pct = (
        (total_covered / total_segments * 100.0) if total_segments else 0.0
    )

    total_transfers = len(declared_transfers)
    total_transfers_covered = len(covered_transfers)
    transfer_coverage_pct = (
        (total_transfers_covered / total_transfers * 100.0)
        if total_transfers
        else 0.0
    )

    total_cbs = len(total_callbacks)
    covered_cbs = len(covered_callbacks)
    callback_coverage_pct = (
        (covered_cbs / total_cbs * 100.0) if total_cbs else 0.0
    )

    category_counts: Dict[str, int] = {}
    category_covered_counts: Dict[str, int] = {}

    for instruction_segment in instruction_segments:
        cat = instruction_segment.category.value
        category_counts[cat] = category_counts.get(cat, 0) + 1
        if instruction_segment.covered == CoverageStatus.COVERED:
            category_covered_counts[cat] = (
                category_covered_counts.get(cat, 0) + 1
            )

    output_file.parent.mkdir(parents=True, exist_ok=True)

    def _path_to_str(p: Path) -> str:
        try:
            return str(p.relative_to(agent_dir))
        except ValueError:
            return str(p)

    phantom_tools_str_keys = {
        _path_to_str(k): list(v) for k, v in phantom_tools_by_file.items()
    }

    transfers_list = []
    for from_a, to_a in declared_transfers:
        desired = (from_a, to_a) in desired_transfers
        tested = (from_a, to_a) in covered_transfers
        evals = covered_transfers.get((from_a, to_a), [])
        transfers_list.append(
            {
                "from_agent": from_a,
                "to_agent": to_a,
                "is_desired": desired,
                "is_tested": tested,
                "covering_evals": evals,
            }
        )

    def segment_to_dict(seg: InstructionSegment) -> Dict[str, Any]:
        d = dataclasses.asdict(seg)
        d["category"] = seg.category.value
        d["covered"] = "Yes" if seg.covered == CoverageStatus.COVERED else "No"
        return d

    from datetime import datetime

    json_data: Dict[str, Any] = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "metrics": {
            "tool_coverage_percent": tool_coverage_pct,
            "instruction_segment_coverage_percent": overall_segment_pct,
            "transfer_coverage_percent": transfer_coverage_pct,
            "callback_coverage_percent": callback_coverage_pct,
            "total_tools": len(total_tools),
            "covered_tools": len(covered_tools),
            "total_segments": total_segments,
            "covered_segments": total_covered,
            "total_transfers": total_transfers,
            "covered_transfers": total_transfers_covered,
            "total_callbacks": total_cbs,
            "covered_callbacks": covered_cbs,
            "category_counts": category_counts,
            "category_covered_counts": category_covered_counts,
        },
        "errors": errors or [],
        "phantom_tools_by_file": phantom_tools_str_keys,
        "tools": {
            "covered": sorted(covered_tools),
            "uncovered": sorted(uncovered_tools),
        },
        "callbacks": {
            "covered": sorted(covered_callbacks),
            "uncovered": sorted(total_callbacks - covered_callbacks),
        },
        "agent_transfers": transfers_list,
        "scanned_files": {
            "instructions": [_path_to_str(f) for f in instruction_files],
            "evaluations": [_path_to_str(f) for f in eval_files],
        },
        "unused_evals": unused_evals,
        "instruction_segments": [segment_to_dict(s) for s in instruction_segments],
        "covered_instruction_segments": [segment_to_dict(s) for s in covered_instruction_segments],
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2)

    print(f"Successfully generated JSON coverage report at: {output_file}")
    return json_data


def generate_html_report(
    json_data: Dict[str, Any], output_file: Path
) -> None:
    """Generates a HTML coverage report

    Args:
        json_data: The structured coverage report data dictionary.
        output_file: Path to write the generated HTML report.
    """
    template_path = Path(__file__).parent / "coverage_report_template.html"
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found at {template_path}")

    with open(template_path, "r", encoding="utf-8") as f:
        template_content = f.read()

    from datetime import datetime
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Perform simple placeholder replacement to keep HTML completely self-contained.
    html = template_content.replace("{{ generated_at }}", generated_at)
    data_json_str = json.dumps(json_data, indent=2, ensure_ascii=False)
    html = html.replace("{{ data_json | safe }}", data_json_str)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Successfully generated HTML coverage report at: {output_file}")


async def main() -> None:
    """Main CLI entry point for calculating agent evaluation coverage."""
    parser = argparse.ArgumentParser(description="Calculate eval coverage.")
    parser.add_argument(
        "--agent-dir",
        required=True,
        help="Directory path to GECX agent project.",
    )
    parser.add_argument(
        "--output-file",
        required=True,
        help="File path to save JSON coverage report.",
    )
    parser.add_argument(
        "--html-report",
        help="Optional file path to also save an interactive HTML report.",
    )
    parser.add_argument(
        "--project-id",
        help="Google Cloud Project ID for Gemini embeddings and LLM judge.",
    )
    parser.add_argument(
        "--location",
        default="global",
        help="Google Cloud location for Gemini services (default: global).",
    )
    parser.add_argument(
        "--model",
        default="gemini-2.5-flash",
        help=(
            "Gemini model name to use for analysis "
            "(default: gemini-2.5-flash)."
        ),
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help="Skip uploading the report to Google Cloud Storage.",
    )
    parser.add_argument(
        "--gcs-uri",
        "--gcs-report-path",
        dest="gcs_uri",
        help="Google Cloud Storage URI to upload the report to (overrides gecx-config.json).",
    )
    args = parser.parse_args()

    agent_dir = Path(args.agent_dir)
    output_file = Path(args.output_file)

    if args.project_id:
        os.environ["GOOGLE_CLOUD_PROJECT"] = args.project_id
    if args.location:
        os.environ["GOOGLE_CLOUD_LOCATION"] = args.location

    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get(
        "GCP_PROJECT"
    )
    location = os.environ.get("GOOGLE_CLOUD_LOCATION") or "global"

    print(f"Initializing Gemini Generate for (Project: {project_id})...")
    gemini_client = GeminiGenerate(
        project_id=project_id,
        location=location,
        model_name=args.model,
    )

    execution_errors = []

    # 1. Unified Ingestion Pass (Reads files once, chunks everything)
    print(f"Ingesting and parsing agent workspace at: {agent_dir}...")
    agent_data = ingest_agent_project(agent_dir)

    print("Running transfer extraction and instruction categorization in parallel...")
    transfer_task = determine_desired_transfers_with_llm(
        agent_data.agent_directories,
        agent_data.declared_transfers,
        gemini_client,
        errors=execution_errors,
    )
    category_task = analyze_instruction_categories(
        agent_data.instruction_segments, gemini_client, errors=execution_errors
    )

    agent_data.desired_transfers, agent_data.instruction_segments = await asyncio.gather(
        transfer_task, category_task
    )

    # Automatically mark every parent to child transfer as desired
    agent_data.desired_transfers.update(agent_data.parent_child_transfers)

    # Filter out untestable segments
    testable_segments = [
        s
        for s in agent_data.instruction_segments
        if s.is_testable
    ]

    # 3. Run instruction coverage analysis pass
    instruction_segments, covered_instruction_segments = (
        await extract_instruction_coverage(
            testable_segments,
            agent_data.eval_chunks,
            agent_data.called_tools,
            gemini_client,
            errors=execution_errors,
        )
    )

    # 4. Warnings for phantoms
    if agent_data.phantom_tools_by_file:
        print(
            "\n[WARNING] Detected tools that are referenced in evaluations "
            "but do not exist in the tools directory:"
        )
        for ef, phantoms in sorted(agent_data.phantom_tools_by_file.items()):
            try:
                rel_path = ef.relative_to(agent_dir)
            except ValueError:
                rel_path = ef
            print(f"  - {rel_path}: {', '.join(sorted(phantoms))}")
        print(
            "Please verify if these tools were renamed, deleted, or "
            "misspelled.\n"
        )

    # 5. Compute unused evals
    all_eval_names = {chunk["eval_name"] for chunk in agent_data.eval_chunks}
    used_eval_names = set()
    for evals in agent_data.covered_transfers.values():
        used_eval_names.update(evals)
    for seg in covered_instruction_segments:
        if seg.evals:
            used_eval_names.update(seg.evals)
    unused_evals = sorted(list(all_eval_names - used_eval_names))

    # 6. Generate clean report
    json_data = generate_json_report(
        output_file=output_file,
        total_tools=agent_data.all_tools,
        covered_tools=agent_data.covered_tools,
        phantom_tools_by_file=agent_data.phantom_tools_by_file,
        eval_files=agent_data.eval_files,
        declared_transfers=agent_data.declared_transfers,
        covered_transfers=agent_data.covered_transfers,
        instruction_segments=instruction_segments,
        covered_instruction_segments=covered_instruction_segments,
        instruction_files=agent_data.instruction_files,
        agent_dir=agent_dir,
        total_callbacks=agent_data.all_callbacks,
        covered_callbacks=agent_data.covered_callbacks,
        desired_transfers=agent_data.desired_transfers,
        unused_evals=unused_evals,
        errors=execution_errors,
    )

    if args.html_report:
        html_file = Path(args.html_report)
        generate_html_report(json_data, html_file)

    # Look for gecx-config.json in agent_dir or parents
    config = {}
    current = agent_dir.resolve()
    for _ in range(4):
        candidate = current / "gecx-config.json"
        if candidate.exists():
            with open(candidate, "r", encoding="utf-8") as f:
                config = json.load(f)
            break
        if current.parent == current:
            break
        current = current.parent

    # 7. Handle Google Cloud Storage Uploading
    if args.skip_upload:
        print("Skipped upload due to --skip-upload flag.")
    else:
        raw_gcs_uri = args.gcs_uri or config.get("gcs_report_path")
        if not raw_gcs_uri:
            print(
                "Warning: No GCS URI specified via --gcs-uri or config gcs_report_path. "
                "Skipping upload. Report remains saved locally."
            )
        elif not raw_gcs_uri.startswith("gs://"):
            print(
                f"Error: Invalid GCS URI '{raw_gcs_uri}'. Must start with 'gs://'. "
                "Skipping GCS upload.",
                file=sys.stderr,
            )
        else:
            gcs_utils = GCSUtils()
            app_id = config.get("deployed_app_id", "default_app_id")

            from datetime import datetime
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            report_filename = f"{timestamp}.json"

            base_uri = raw_gcs_uri.replace("[deployed_app_id]", app_id).rstrip("/")
            if app_id not in base_uri:
                gcs_uri = f"{base_uri}/{app_id}/{report_filename}"
            else:
                gcs_uri = f"{base_uri}/{report_filename}"

            print(f"Streaming consolidated report directly to: {gcs_uri}...")
            try:
                gcs_utils.upload_string(
                    gcs_uri=gcs_uri,
                    content=json.dumps(json_data, indent=2),
                    content_type="application/json",
                )
                print("Upload complete.")
            except Exception as e:
                print(
                    f"Error: GCS streaming upload failed: {e}",
                    file=sys.stderr,
                )


if __name__ == "__main__":
    asyncio.run(main())
