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

"""Utility functions for generating reports."""

from collections.abc import Mapping, Sequence
import datetime
import glob
import json
import os

from typing import Any

from cxas_scrapi.core import tools
from cxas_scrapi.reporting.base_components import ComponentGroup
from cxas_scrapi.reporting.report_components import (
    CallbackCard,
    Controls,
    EmptyComponent,
    FailurePatterns,
    GoldenSectionCard,
    Header,
    Report,
    ResultsTable,
    Scorecard,
    SimSectionCard,
    ToolCard,
)
from cxas_scrapi.reporting.report_components import fmt_duration
from cxas_scrapi.reporting.result_extractors import (
    CallbackRunResult,
    GoldenRunResult,
    SimulationRunResult,
    ToolRunResult,
)
from cxas_scrapi.reporting.result_stats import (
    EvaluationStats,
    get_evaluation_result_stats,
)
from cxas_scrapi.utils import gcs_utils
from cxas_scrapi.evals.result_loaders import (
    load_callback_test_results,
    load_golden_results,
    load_sim_results,
    load_tool_test_results,
)
import pandas as pd


def generate_html_report(
    results: list[dict[str, Any]],
    output_path: str,
    modality: str,
    model: str,
    app_name: str = "",
    wall_clock_s: float | None = None,
    user_agent_extension: str | None = None,
) -> None:
    """Generate an HTML report and save it locally or upload to GCS."""
    generate_combined_html_report(
        sim_results=results,
        output_path=output_path,
        app_name=app_name,
        sim_modality=modality,
        model=model,
        sim_wall_clock_s=wall_clock_s,
        user_agent_extension=user_agent_extension,
        report_title="Simulation Eval Report",
    )
def _upload_to_gcs(output_path: str, html_content: str) -> str | None:
    """Uploads the report to GCS and returns the mTLS URL or None on failure."""
    try:
        gcs = gcs_utils.GCSUtils()
        mtls_url = gcs.upload_string(output_path, html_content)
        print(f"Report uploaded to GCS: {output_path}")
        print(f"Authenticated URL: {mtls_url}")
        return mtls_url
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"WARNING: GCS upload failed ({e}). Falling back to local file.")
        return None


def generate_html_report(
    results: list[dict[str, Any]],
    output_path: str,
    modality: str,
    model: str,
    app_name: str = "",
    wall_clock_s: float | None = None,
    user_agent_extension: str | None = None,
) -> None:
    """Generate an HTML report and save it locally or upload to GCS."""
    generate_combined_html_report(
        sim_results=results,
        output_path=output_path,
        app_name=app_name,
        sim_modality=modality,
        model=model,
        sim_wall_clock_s=wall_clock_s,
        user_agent_extension=user_agent_extension,
        report_title="Simulation Eval Report",
    )

def generate_combined_html_report(
    golden_results: list[dict[str, Any]] | None = None,
    sim_results: list[dict[str, Any]] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
    callback_results: list[dict[str, Any]] | None = None,
    output_path: str | None = None,
    app_name: str = "",
    golden_modality: str = "text",
    sim_modality: str = "text",
    ces_base: str | None = None,
    report_title: str = "Combined Eval Report",
    model: str | None = None,
    sim_wall_clock_s: float | None = None,
    user_agent_extension: str | None = None,
    bg_noise_file: str | None = None,
    burst_noise_files: list[str] | None = None,
) -> str:
    """Generate combined HTML report declaratively from results lists."""
    if ces_base is None:
        parts = app_name.split("/") if app_name else []
        project_id_idx = 1
        location_idx = 3
        app_id_idx = 5
        project_id = (
            parts[project_id_idx] if len(parts) > project_id_idx else ""
        )
        location = parts[location_idx] if len(parts) > location_idx else ""
        app_id = parts[app_id_idx] if len(parts) > app_id_idx else ""
        ces_base = (
            f"https://ces.cloud.google.com/projects/{project_id}/locations/{location}/apps/{app_id}"
            if app_id
            else ""
        )

    # 1. Fallback initialize raw results lists safely!
    golden_results = golden_results or []
    sim_results = sim_results or []
    tool_results = tool_results or []
    callback_results = callback_results or []

    # 2. Set modalities on raw records!
    for r in golden_results:
        r["modality"] = golden_modality
    for r in sim_results:
        r["modality"] = sim_modality
        r["sim_wall_clock_s"] = sim_wall_clock_s or 0

    # 3. Wrap all raw records inside lossless strongly-typed data models!
    golden_models = [GoldenRunResult(raw=r) for r in golden_results]
    sim_models = [SimulationRunResult(raw=r) for r in sim_results]
    tool_models = [ToolRunResult(raw=r) for r in tool_results]
    callback_models = [CallbackRunResult(raw=r) for r in callback_results]

    # 4. Pre-calculate strongly-typed metrics statistics Pandas DataFrame.
    stats_df = get_evaluation_result_stats(
        golden_results=golden_models,
        sim_results=sim_models,
        tool_results=tool_models,
        callback_results=callback_models,
    )
    # Wrap compiled stats DataFrame into a strongly-typed EvaluationStats layer!
    stats = EvaluationStats(
        stats_df,
        golden_results=golden_models,
        sim_results=sim_models,
        tool_results=tool_models,
        callback_results=callback_models,
    )

    # 5. Calculate overall combined metrics cleanly from stats wrapper properties.
    passed = stats.passed_sum
    total = stats.total_sum
    pct = stats.overall_pct
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 6. Master results searchable index table.
    unified_mapped = []
    for g in golden_models:
        unified_mapped.append(
            {
                "name": g.name,
                "type": "golden",
                "passed": g.passed,
                "duration_str": fmt_duration(g.duration_s),
                "session_id": g.name,
                "error": "",
            }
        )

    # Group sim name grouping.
    sim_grouped = {}
    for s in sim_models:
        if s.name not in sim_grouped:
            sim_grouped[s.name] = []
        sim_grouped[s.name].append(s)

    for name, runs in sim_grouped.items():
        # Group passed status
        s_passed_all = all(r.passed for r in runs)
        unified_mapped.append(
            {
                "name": name,
                "type": "sim",
                "passed": s_passed_all,
                "duration_str": fmt_duration(runs[0].duration_s),
                "session_id": runs[0].session_id,
                "error": "",
                "run_results": runs,
            }
        )

    for t in tool_models:
        unified_mapped.append(
            {
                "name": t.name,
                "type": "tool",
                "passed": t.passed,
                "duration_str": f"{t.latency_ms:.0f}ms"
                if t.latency_ms
                else "-",
                "session_id": t.tool,
                "error": t.errors[:80],
            }
        )
    for c in callback_models:
        unified_mapped.append(
            {
                "name": c.name,
                "type": "callback",
                "passed": c.passed,
                "duration_str": "-",
                "session_id": c.agent,
                "error": c.error[:80],
            }
        )

    # Prepare tools map for template if needed
    tools_map = {}
    if app_name:
        try:
            tools_map = tools.Tools(
                app_name=app_name, user_agent_extension=user_agent_extension
            ).get_tools_map()
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    # Process traces for simulation results to simplify template
    if sim_results:
        for r in sim_results:
            trace = r.get("detailed_trace", [])
            if trace:
                parsed = []
                for entry in trace:
                    for raw_line in entry.split("\n"):
                        line = raw_line.strip()
                        if not line or line.startswith("Agent Text (Diag):"):
                            continue
                        for path, dname in tools_map.items():
                            line = line.replace(path, dname)
                        if line.startswith("Agent Text:"):
                            parsed.append(
                                ("agent", line[len("Agent Text:") :].strip())
                            )
                        elif line.startswith("User:"):
                            parsed.append(("user", line[5:].strip()))
                        elif line.startswith("Tool Call"):
                            parsed.append(("tool_call", line))
                        elif line.startswith("Tool Response"):
                            parsed.append(("tool_resp", line))
                        else:
                            parsed.append(("system", line))

                merged = []
                for kind, text in parsed:
                    if kind == "agent" and merged and merged[-1][0] == "agent":
                        merged[-1] = ("agent", merged[-1][1] + " " + text)
                    elif (
                        kind == "tool_resp"
                        and merged
                        and merged[-1][0] == "tool_call"
                    ):
                        merged[-1] = ("tool_pair", merged[-1][1], text)
                    else:
                        merged.append((kind, text))
                r["_processed_trace"] = merged

    # Assemble polymorphic section cards dynamically.
    sections = []
    if golden_results:
        sections.append(
            GoldenSectionCard(
                golden_results=golden_models, stats=stats, ces_base=ces_base
            )
        )
    if sim_results:
        sections.append(
            SimSectionCard(
                sim_results=sim_models, stats=stats, ces_base=ces_base
            )
        )
    if tool_results:
        sections.append(ToolCard(tool_results=tool_models, stats=stats))
    if callback_results:
        sections.append(
            CallbackCard(callback_results=callback_models, stats=stats)
        )

    summary_cards = [s.get_summary_card() for s in sections]

    ts_str = f"{ts} | model: {model}" if model else ts

    # Compose Report Component Tree.
    report = Report(
        title=f"{report_title} - {ts}",
        body=ComponentGroup(
            [
                Header(report_title),
                Scorecard(
                    ts=ts_str,
                    summary_cards=summary_cards,
                    stats=stats,
                    model=model,
                    report_title=report_title,
                ),
                Controls(),
                ResultsTable(unified=unified_mapped),
                FailurePatterns(failure_groups=stats.failure_groups)
                if stats.failure_groups
                else EmptyComponent(),
                ComponentGroup(sections),
            ]
        ),
    )

    # Render recursively at the final boundary.
    html_out = report.render()

    if output_path:
        if output_path.startswith("gs://"):
            mtls_url = _upload_to_gcs(output_path, html_out)
            if not mtls_url:
                # Fallback to local file if upload failed
                filename = output_path.rsplit("/", maxsplit=1)[-1]
                if not filename.endswith(".html"):
                    filename = "report_fallback.html"
                output_path = filename
                with open(output_path, "w") as f:
                    f.write(html_out)
        else:
            with open(output_path, "w") as f:
                f.write(html_out)

    return html_out


def _compile_tool_results_card(
    *,
    tool_results: Sequence[ToolRunResult],
    t_passed: int,
    t_total: int,
) -> ToolCard | str:
    """Compile the ToolCard component declaratively.

    Compiles without premature rendering.
    """
    if not tool_results:
        return ""
    return ToolCard(tool_results=list(tool_results))


def _compile_callback_results_card(
    *,
    callback_results: Sequence[CallbackRunResult],
    c_passed: int,
    c_total: int,
) -> CallbackCard | str:
    """Compile the CallbackCard component declaratively.

    Compiles without premature rendering.
    """
    if not callback_results:
        return ""
    return CallbackCard(callback_results=list(callback_results))



def generate_combined_report_from_dir(
    output_dir: str,
    golden_run: str | None = None,
    app_name: str | None = None,
    output_path: str | None = None,
    run: bool = False,
    app_dir: str | None = None,
    tool_test_file: str | None = None,
    goldens_dir: str | None = None,
    simulation_dir: str | None = None,
    include: list[str] | None = None,
    modality: str = "text",
    runs: int = 1,
    filter_files: list[str] | None = None,
    filter_tags: list[str] | None = None,
    parallel: int = 1,
    golden_timeout: int = 600,
    bg_noise_file: str | None = None,
    burst_noise_files: list[str] | None = None,
    use_tool_fakes: bool = False,
) -> str:
    """Load results from directory and generate combined HTML report.

    Args:
      output_dir: Directory containing the evaluation results.
      golden_run: The golden evaluation run ID.
      app_name: CX Agent Studio (CXAS) agent resource name.
      output_path: Optional GCS or local path to write the HTML report to.
      run: If True, triggers execution of evals before compiling report.
      app_dir: Directory containing CXAS agent code.
      tool_test_file: Path to tool tests definition file.
      goldens_dir: Directory containing golden test cases.
      simulation_dir: Directory containing simulation test cases.
      include: List of evaluation types to include ('sims', 'goldens', etc).
      modality: The modality used for the evaluation (e.g., 'text').
      runs: Number of simulation runs.
      filter_files: List of specific files to filter evaluations by.
      filter_tags: List of specific tags to filter evaluations by.
      parallel: Degree of parallelism for the runs.
      golden_timeout: Golden run execution timeout in seconds.
      bg_noise_file: Path to background noise audio file to play during
        replay.
      burst_noise_files: List of paths to burst noise audio files injected
        during replay.
      use_tool_fakes: Use fake tools for the session if available.

    Returns:
      The rendered combined HTML report string.
    """
    if not os.path.isdir(output_dir):
        raise ValueError(f"{output_dir} is not a directory.")

    if include is None or "all" in include:
        include = ["sims", "goldens", "tools", "callbacks"]

    sim_results = []
    tool_results = []
    callback_results = []
    golden_results = []

    if run:
        run_results = run_all_evals(
            app_name=app_name,
            app_dir=app_dir,
            tool_test_file=tool_test_file,
            goldens_dir=goldens_dir,
            simulation_dir=simulation_dir,
            output_dir=output_dir,
            modality=modality,
            runs=runs,
            filter_files=filter_files,
            filter_tags=filter_tags,
            parallel=parallel,
            golden_timeout=golden_timeout,
            include=include,
            bg_noise_file=bg_noise_file,
            burst_noise_files=burst_noise_files,
            use_tool_fakes=use_tool_fakes,
        )
        sim_results = run_results["simulation"] if "sims" in include else []
        # Map tool results to expected format if needed
        if "tools" in include:
            for r in run_results["tool"]:
                tool_results.append(
                    {
                        "name": r.get("test_name", r.get("test", "?")),
                        "tool": r.get("tool", "?"),
                        "passed": r.get("status", "").upper()
                        in ("PASSED", "PASS"),
                        "status": r.get("status", "?"),
                        "latency_ms": r.get("latency (ms)", 0),
                        "errors": r.get("errors", ""),
                    }
                )
        # Map callback results
        if "callbacks" in include:
            for r in run_results["callback"]:
                callback_results.append(
                    {
                        "name": r.get("test_name", "?"),
                        "agent": r.get("agent_name", "?"),
                        "callback_type": r.get("callback_type", "?"),
                        "passed": r.get("status", "").upper()
                        in ("PASSED", "PASS"),
                        "status": r.get("status", "?"),
                        "error": r.get("error_message", ""),
                    }
                )
        golden_results = run_results["golden"] if "goldens" in include else []
    else:
        sim_files = []
        if "sims" in include:
            sim_files = glob.glob(os.path.join(output_dir, "sim_results*.json"))

        tool_files = []
        callback_files = []
        if "tools" in include:
            tool_files = glob.glob(
                os.path.join(output_dir, "tool_results*.csv")
            )
            tool_files.extend(
                glob.glob(os.path.join(output_dir, "tool_results*.json"))
            )
        if "callbacks" in include:
            callback_files = glob.glob(
                os.path.join(output_dir, "callback_results*.csv")
            )
            callback_files.extend(
                glob.glob(os.path.join(output_dir, "callback_results*.json"))
            )

        if sim_files:
            with open(sim_files[0]) as f:
                data = json.load(f)
                # New envelope format: {"wall_clock_s": N, "results": [...]}
                # Old format: [...]
                if isinstance(data, dict):
                    sim_results = data.get("results", [])
                else:
                    sim_results = data
            print(f"Loaded {len(sim_results)} sim results from {sim_files[0]}")

        if tool_files:
            tf = tool_files[0]
            if tf.endswith(".csv"):
                df = pd.read_csv(tf)
            else:
                df = pd.read_json(tf)
            for _, row in df.iterrows():
                tool_results.append(
                    {
                        "name": row.get("test_name", row.get("test", "?")),
                        "tool": row.get("tool", "?"),
                        "passed": row.get("status", "").upper()
                        in ("PASSED", "PASS"),
                        "status": row.get("status", "?"),
                        "latency_ms": row.get("latency (ms)", 0),
                        "errors": row.get("errors", ""),
                    }
                )
            print(f"Loaded {len(tool_results)} tool results from {tf}")

        if callback_files:
            cf = callback_files[0]
            if cf.endswith(".csv"):
                df = pd.read_csv(cf)
            else:
                df = pd.read_json(cf)
            for _, row in df.iterrows():
                callback_results.append(
                    {
                        "name": row.get("test_name", "?"),
                        "agent": row.get("agent_name", "?"),
                        "callback_type": row.get("callback_type", "?"),
                        "passed": row.get("status", "").upper()
                        in ("PASSED", "PASS"),
                        "status": row.get("status", "?"),
                        "error": row.get("error_message", ""),
                    }
                )
            print(f"Loaded {len(callback_results)} callback results from {cf}")

        if golden_run:
            if not app_name:
                raise ValueError(
                    "--app-name is required when golden_run is specified."
                )
            golden_results = load_golden_results(
                golden_run, app_name, include=include
            )

    if not output_path:
        output_path = os.path.join(output_dir, "combined_report.html")

    return generate_combined_html_report(
        golden_results=golden_results,
        sim_results=sim_results,
        tool_results=tool_results,
        callback_results=callback_results,
        output_path=output_path,
        app_name=app_name or "",
        golden_modality=modality,
        sim_modality=modality,
        bg_noise_file=bg_noise_file,
        burst_noise_files=burst_noise_files,
    )


def run_all_evals(
    app_name: str,
    app_dir: str | None = None,
    tool_test_file: str | None = None,
    goldens_dir: str | None = None,
    simulation_dir: str | None = None,
    output_dir: str | None = None,
    modality: str = "text",
    runs: int = 1,
    filter_files: list[str] | None = None,
    filter_tags: list[str] | None = None,
    parallel: int = 1,
    golden_timeout: int = 600,
    include: list[str] | None = None,
    bg_noise_file: str | None = None,
    burst_noise_files: list[str] | None = None,
    use_tool_fakes: bool = False,
) -> dict[str, Any]:
    """Runs all 4 types of evaluations and returns aggregated results.

    Deprecated legacy wrapper. Use
    `cxas_scrapi.evals.runner.run_all_evals` directly.

    Args:
      app_name: CX Agent Studio (CXAS) agent resource name.
      app_dir: Directory containing CXAS agent code.
      tool_test_file: Path to tool tests definition file.
      goldens_dir: Directory containing golden test cases.
      simulation_dir: Directory containing simulation test cases.
      output_dir: Directory to write output evaluation results.
      modality: The modality used for the evaluation (e.g., 'text').
      runs: Number of simulation runs.
      filter_files: List of specific files to filter evaluations by.
      filter_tags: List of specific tags to filter evaluations by.
      parallel: Degree of parallelism for the runs.
      golden_timeout: Golden run execution timeout in seconds.
      include: List of evaluation types to include.
      bg_noise_file: Path to background noise audio file to play during replay.
      burst_noise_files: List of paths to burst noise audio files injected during
        replay.
      burst_noise_files: List of paths to burst noise audio files injected
        during replay.
      use_tool_fakes: Use fake tools for the session if available.

    Returns:
      A dict containing lists of results for 'simulation', 'golden', 'tool', and
      'callback'.
    """
    from cxas_scrapi.evals import runner as evals_runner

    return evals_runner.run_all_evals(
        app_name=app_name,
        modality=modality,
        runs=runs,
        goldens_dir=goldens_dir,
        tool_test_file=tool_test_file,
        simulation_dir=simulation_dir,
        app_dir=app_dir,
        output_dir=output_dir,
        filter_files=filter_files,
        filter_tags=filter_tags,
        parallel=parallel,
        golden_timeout=golden_timeout,
        include=include,
        bg_noise_file=bg_noise_file,
        burst_noise_files=burst_noise_files,
        use_tool_fakes=use_tool_fakes,
    )
