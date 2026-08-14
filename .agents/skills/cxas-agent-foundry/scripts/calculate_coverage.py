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

import asyncio
import dataclasses
import datetime
import json
import os
import pathlib
import sys
from collections.abc import Sequence
from typing import Any

import ingestion
import instruction_coverage
import models
from absl import app, flags

from cxas_scrapi.utils import gcs_utils, gemini

Path = pathlib.Path

_AGENT_DIR = flags.DEFINE_string(
    "agent_dir", None, "Directory path to GECX agent project.", required=True
)
_OUTPUT_FILE = flags.DEFINE_string(
    "output_file",
    None,
    "File path to save JSON coverage report.",
    required=True,
)
_PROJECT_ID = flags.DEFINE_string(
    "project_id",
    None,
    "Google Cloud Project ID for Gemini embeddings and LLM judge.",
)
_LOCATION = flags.DEFINE_string(
    "location",
    "global",
    "Google Cloud location for Gemini services (default: global).",
)
_MODEL = flags.DEFINE_string(
    "model",
    "gemini-2.5-flash",
    "Gemini model name to use for analysis (default: gemini-2.5-flash).",
)
_SKIP_UPLOAD = flags.DEFINE_bool(
    "skip_upload",
    False,
    "Skip uploading the report to Google Cloud Storage.",
)
_GCS_URI = flags.DEFINE_string(
    "gcs_uri",
    None,
    "Google Cloud Storage URI to upload the report to.",
)
_GCS_REPORT_PATH = flags.DEFINE_string(
    "gcs_report_path",
    None,
    "Alternative flag for GCS report upload path.",
)
_CONCURRENCY = flags.DEFINE_integer(
    "concurrency",
    5,
    "Maximum number of concurrent Gemini API requests (default: 5).",
)
_HTML_REPORT = flags.DEFINE_string(
    "html_report",
    None,
    "Optional file path to save HTML coverage report.",
)


def _path_to_str(p: Path, agent_dir: Path) -> str:
    """Converts a Path relative to agent_dir to string."""
    try:
        return str(p.relative_to(agent_dir))
    except ValueError:
        return str(p)


def _segment_to_dict(seg: models.InstructionSegment) -> dict[str, Any]:
    """Converts an InstructionSegment to a dict representation."""
    d: dict[str, Any] = dict(dataclasses.asdict(seg))
    d["category"] = seg.category.value if seg.category else ""
    d["covered"] = (
        "Yes" if seg.covered == models.CoverageStatus.COVERED else "No"
    )
    return d


def generate_json_report(
    output_file: Path,
    total_tools: set[str],
    covered_tools: set[str],
    phantom_tools_by_file: dict[Path, set[str]],
    eval_files: list[Path],
    declared_transfers: list[tuple[str, str]],
    covered_transfers: dict[tuple[str, str], list[str]],
    instruction_segments: list[models.InstructionSegment],
    covered_instruction_segments: list[models.InstructionSegment],
    instruction_files: list[Path],
    agent_dir: Path,
    total_callbacks: set[str],
    covered_callbacks: set[str],
    desired_transfers: set[tuple[str, str]],
    unused_evals: list[str],
    errors: list[str] | None = None,
) -> dict[str, Any]:
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
    # Any tested transfer is implicitly desired and declared.
    for edge in covered_transfers:
        if edge not in desired_transfers:
            desired_transfers.add(edge)
        if edge not in declared_transfers:
            declared_transfers.append(edge)

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

    category_counts: dict[str, int] = {}
    category_covered_counts: dict[str, int] = {}

    for instruction_segment in instruction_segments:
        cat = instruction_segment.category.value
        category_counts[cat] = category_counts.get(cat, 0) + 1
        if instruction_segment.covered == models.CoverageStatus.COVERED:
            category_covered_counts[cat] = (
                category_covered_counts.get(cat, 0) + 1
            )

    output_file.parent.mkdir(parents=True, exist_ok=True)

    phantom_tools_str_keys = {
        _path_to_str(k, agent_dir): list(v)
        for k, v in phantom_tools_by_file.items()
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

    json_data: dict[str, Any] = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
            "instructions": [
                _path_to_str(f, agent_dir) for f in instruction_files
            ],
            "evaluations": [_path_to_str(f, agent_dir) for f in eval_files],
        },
        "unused_evals": unused_evals,
        "instruction_segments": [
            _segment_to_dict(s) for s in instruction_segments
        ],
        "covered_instruction_segments": [
            _segment_to_dict(s) for s in covered_instruction_segments
        ],
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2)

    print(f"Successfully generated JSON coverage report at: {output_file}")
    return json_data


def generate_html_report(
    html_output_path: Path,
    json_data: dict[str, Any],
) -> None:
    """Generates an HTML coverage report based on the template.

    Args:
        html_output_path: Path where the HTML report will be written.
        json_data: The dictionary containing the complete coverage report data.
    """
    template_path = (
        pathlib.Path(__file__).parent / "coverage_report_template.html"
    )
    try:
        with open(template_path, encoding="utf-8") as f:
            template_content = f.read()
    except Exception as e:
        raise RuntimeError(
            f"Failed to load template {template_path}: {e}"
        ) from e
    json_str = json.dumps(json_data, indent=2, ensure_ascii=False)
    generated_at = json_data.get("generated_at", "")

    html_content = template_content.replace("{{ data_json | safe }}", json_str)
    html_content = html_content.replace("{{ generated_at }}", generated_at)

    html_output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(html_output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"Successfully generated HTML coverage report at: {html_output_path}")


def _read_config_file(agent_dir: Path) -> dict[str, Any]:
    """Looks for gecx-config.json in agent_dir or parents."""
    config = {}
    current = agent_dir.resolve()
    for _ in range(4):
        candidate = current / "gecx-config.json"
        if candidate.exists():
            try:
                with open(candidate, encoding="utf-8") as f:
                    config = json.load(f)
                break
            except (json.JSONDecodeError, OSError):
                pass
        if current.parent == current:
            break
        current = current.parent
    return config


def _upload_to_gcs(
    config: dict[str, Any],
    json_data: dict[str, Any],
    gcs_uri_arg: str | None,
    skip_upload: bool,
) -> None:
    """Handles Google Cloud Storage Uploading of the JSON report."""
    if skip_upload:
        print("Skipped upload due to --skip-upload flag.")
        return

    raw_gcs_uri = gcs_uri_arg or config.get("gcs_report_path")
    if not raw_gcs_uri:
        print(
            "Warning: No GCS URI specified. "
            "Skipping upload. Report remains saved locally."
        )
        return

    if not raw_gcs_uri.startswith("gs://"):
        print(
            f"Error: Invalid GCS URI '{raw_gcs_uri}'. Must start with 'gs://'. "
            "Skipping GCS upload.",
            file=sys.stderr,
        )
        return

    gcs_client = gcs_utils.GCSUtils()
    app_id = config.get("deployed_app_id", "default_app_id")

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    report_filename = f"{timestamp}.json"

    base_uri = raw_gcs_uri.rstrip("/")
    if "[deployed_app_id]" in base_uri or "<deployed_app_id>" in base_uri:
        base_uri = base_uri.replace("[deployed_app_id]", app_id).replace(
            "<deployed_app_id>", app_id
        )
    else:
        # If no placeholder is present, append the app_id to prevent mixing reports.
        base_uri = f"{base_uri}/{app_id}"

    gcs_uri = f"{base_uri}/{report_filename}"

    print(f"Streaming consolidated report directly to: {gcs_uri}...")
    try:
        gcs_client.upload_string(
            gcs_uri=gcs_uri,
            content=json.dumps(json_data, indent=2),
            content_type="application/json",
        )
        print("Upload complete.")
    except (OSError, RuntimeError, ValueError) as e:
        print(
            f"Error: GCS streaming upload failed: {e}",
            file=sys.stderr,
        )


async def async_main() -> None:
    """Main async entry point execution for calculating coverage."""
    agent_dir = Path(_AGENT_DIR.value)
    output_file = Path(_OUTPUT_FILE.value)

    if _PROJECT_ID.value:
        os.environ["GOOGLE_CLOUD_PROJECT"] = _PROJECT_ID.value
    if _LOCATION.value:
        os.environ["GOOGLE_CLOUD_LOCATION"] = _LOCATION.value

    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get(
        "GCP_PROJECT"
    )
    if not project_id:
        raise ValueError(
            "GOOGLE_CLOUD_PROJECT environment variable must be set."
        )

    location = os.environ.get("GOOGLE_CLOUD_LOCATION") or "global"

    print(f"Initializing Gemini Generate for (Project: {project_id})...")
    gemini_client = gemini.GeminiGenerate(
        project_id=project_id,
        location=location,
        model_name=_MODEL.value,
    )

    execution_errors = []

    print(f"Ingesting and parsing agent workspace at: {agent_dir}...")
    agent_data = ingestion.ingest_agent_project(agent_dir)

    print(
        "Running transfer extraction and instruction categorization in"
        " parallel..."
    )
    transfer_task = instruction_coverage.determine_desired_transfers_with_llm(
        agent_data.agent_directories,
        agent_data.declared_transfers,
        gemini_client,
        errors=execution_errors,
        concurrency=_CONCURRENCY.value,
    )
    category_task = instruction_coverage.analyze_instruction_categories(
        agent_data.instruction_segments,
        gemini_client,
        errors=execution_errors,
        concurrency=_CONCURRENCY.value,
    )

    (
        agent_data.desired_transfers,
        agent_data.instruction_segments,
    ) = await asyncio.gather(transfer_task, category_task)

    agent_data.desired_transfers.update(agent_data.parent_child_transfers)

    testable_segments = [
        s for s in agent_data.instruction_segments if s.is_testable
    ]

    (
        instruction_segments,
        covered_instruction_segments,
    ) = await instruction_coverage.extract_instruction_coverage(
        testable_segments,
        agent_data.eval_chunks,
        agent_data.called_tools,
        gemini_client,
        errors=execution_errors,
        concurrency=_CONCURRENCY.value,
    )

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
            "Please verify if these tools were renamed, deleted, or misspelled.\n"
        )

    all_eval_names = {chunk["eval_name"] for chunk in agent_data.eval_chunks}
    used_eval_names = set()
    for evals in agent_data.covered_transfers.values():
        used_eval_names.update(evals)
    for seg in covered_instruction_segments:
        if seg.evals:
            used_eval_names.update(seg.evals)
    for chunk in agent_data.eval_chunks:
        if (
            chunk["text"].startswith("Tool Test:")
            and chunk["eval_name"] in all_eval_names
        ):
            lines = chunk["text"].split("\n")
            if len(lines) > 1 and lines[1].startswith("Tool: "):
                tool_name = lines[1][len("Tool: ") :].strip()
                if tool_name in agent_data.covered_tools:
                    used_eval_names.add(chunk["eval_name"])
    unused_evals = sorted(list(all_eval_names - used_eval_names))

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

    config = _read_config_file(agent_dir)
    gcs_uri_val = _GCS_URI.value or _GCS_REPORT_PATH.value
    _upload_to_gcs(config, json_data, gcs_uri_val, _SKIP_UPLOAD.value)

    if _HTML_REPORT.value:
        generate_html_report(Path(_HTML_REPORT.value), json_data)


def main(argv: Sequence[str]) -> None:
    if len(argv) > 1:
        raise app.UsageError("Too many command-line arguments.")
    asyncio.run(async_main())


if __name__ == "__main__":
    app.run(main)
