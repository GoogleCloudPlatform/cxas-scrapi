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

"""High-level orchestration runner for CXAS evaluations."""

import glob
import json
import os
import time

from google.cloud.ces_v1beta.types import RunEvaluationOperationMetadata

from cxas_scrapi.core.evaluations import Evaluations
from cxas_scrapi.evals.callback_evals import CallbackEvals
from cxas_scrapi.evals.simulation_evals import SimulationEvals
from cxas_scrapi.evals.tool_evals import ToolEvals
from cxas_scrapi.utils.eval_utils import EvalUtils
from cxas_scrapi.utils.rate_limiter import RateLimiter


def run_all_evals(
    app_name: str,
    modality: str = "text",
    runs: int = 1,
    goldens_dir: str | None = None,
    tool_test_file: str | None = None,
    simulation_dir: str | None = None,
    app_dir: str | None = None,
    output_dir: str | None = None,
    filter_files: list[str] | None = None,
    filter_tags: list[str] | None = None,
    parallel: int = 1,
    golden_timeout: int = 600,
    include: list[str] | None = None,
    rate_limiter: RateLimiter | None = None,
    bg_noise_file: str | None = None,
    burst_noise_files: list[str] | None = None,
):
    """Runs all 4 types of evaluations and returns aggregated results.

    This high-level orchestration function decouples execution logic from pure
    HTML report generation.
    """
    from cxas_scrapi.utils.reporting import (  # noqa: PLC0415
        _load_sim_test_cases,
        load_golden_results,
    )

    results = {"callback": [], "tool": [], "golden": [], "simulation": []}

    include = include or ["sims", "goldens", "tools", "callbacks"]

    # 1. Platform goldens (Trigger async)
    evaluations_to_run = []
    run_name = None
    if "goldens" in include:
        if not goldens_dir:
            goldens_dir = "evals/goldens/"
        if app_name and os.path.exists(goldens_dir):
            eval_client = Evaluations(app_name=app_name)
            eval_utils = EvalUtils(app_name=app_name)

            if os.path.isdir(goldens_dir):
                golden_files = glob.glob(os.path.join(goldens_dir, "*.yaml"))
                print(
                    f"Found {len(golden_files)} golden files in {goldens_dir}"
                )
            else:
                golden_files = [goldens_dir]

            if filter_files:
                golden_files = [
                    f
                    for f in golden_files
                    if any(
                        pattern.lower() in os.path.basename(f).lower()
                        for pattern in filter_files
                    )
                ]

            for gf in golden_files:
                print(f"Pushing golden file {gf}")
                evals = eval_utils.load_golden_evals_from_yaml(gf)
                for eval_dict in evals:
                    if filter_tags:
                        tags = eval_dict.get("tags", [])
                        if not any(t in filter_tags for t in tags):
                            continue
                    res = eval_client.update_evaluation(
                        evaluation=eval_dict, app_name=app_name
                    )
                    evaluations_to_run.append(res.name)

            if evaluations_to_run:
                print(f"Running evaluations: {evaluations_to_run}")
                operation = eval_client.run_evaluation(
                    evaluations=evaluations_to_run,
                    app_name=app_name,
                    modality=modality,
                    run_count=runs,
                )

                print(
                    "  Waiting for evaluation run name to appear in operation "
                    "metadata..."
                )
                for i in range(12):
                    time.sleep(10)
                    refreshed = operation._refresh(None)
                    meta = RunEvaluationOperationMetadata()
                    meta._pb.ParseFromString(refreshed.metadata.value)
                    if meta.evaluation_run:
                        run_name = meta.evaluation_run
                        print(f"  Run name resolved: {run_name}")
                        break
                    print(f"  Waiting... ({(i + 1) * 10}s)")

    # 2. Callback tests
    if "callbacks" in include:
        if not app_dir and app_name:
            app_dir = f"cxas_app/{app_name.rsplit('/', 1)[-1]}"
        if app_dir and os.path.exists(app_dir):
            print(f"Running callback tests in {app_dir}")
            callback_evals = CallbackEvals()
            df = callback_evals.test_all_callbacks_in_app_dir(app_dir=app_dir)
            results["callback"] = df.to_dict(orient="records")
            if output_dir:
                df.to_csv(
                    os.path.join(output_dir, "callback_results.csv"),
                    index=False,
                )

    # 3. Tool tests
    if "tools" in include:
        if not tool_test_file:
            tool_test_file = "evals/tool_tests/"
        if app_name and os.path.exists(tool_test_file):
            tool_evals = ToolEvals(app_name=app_name)

            if os.path.isdir(tool_test_file):
                tool_files = glob.glob(os.path.join(tool_test_file, "*.yaml"))
                print(
                    f"Found {len(tool_files)} tool test files "
                    f"in {tool_test_file}"
                )
            else:
                tool_files = [tool_test_file]

            if filter_files:
                tool_files = [
                    f
                    for f in tool_files
                    if any(
                        pattern.lower() in os.path.basename(f).lower()
                        for pattern in filter_files
                    )
                ]

            test_cases = []
            for tf in tool_files:
                print(f"Loading tool tests from {tf}")
                cases = tool_evals.load_tool_test_cases_from_file(tf)
                if filter_tags:
                    cases = [
                        c
                        for c in cases
                        if any(
                            t in filter_tags
                            for t in (
                                c.get("tags", [])
                                if isinstance(c, dict)
                                else (getattr(c, "tags", None) or [])
                            )
                        )
                    ]
                test_cases.extend(cases)

            if test_cases:
                print(f"Running {len(test_cases)} tool tests")
                df = tool_evals.run_tool_tests(test_cases)
                results["tool"] = df.to_dict(orient="records")
                if output_dir:
                    df.to_csv(
                        os.path.join(output_dir, "tool_results.csv"),
                        index=False,
                    )

    # 4. Local simulations
    if "sims" in include:
        if not simulation_dir:
            simulation_dir = "evals/simulations/"
        if app_name and os.path.exists(simulation_dir):
            sim_files = glob.glob(os.path.join(simulation_dir, "*.yaml"))
            if filter_files:
                sim_files = [
                    f
                    for f in sim_files
                    if any(
                        pattern.lower() in os.path.basename(f).lower()
                        for pattern in filter_files
                    )
                ]

            if sim_files:
                sim_evals = SimulationEvals(
                    app_name=app_name, rate_limiter=rate_limiter
                )
                test_cases = []
                for sf in sim_files:
                    cases = _load_sim_test_cases(sf)
                    if cases:
                        if filter_tags:
                            cases = [
                                c
                                for c in cases
                                if any(
                                    t in filter_tags for t in c.get("tags", [])
                                )
                            ]
                        test_cases.extend(cases)
                if test_cases:
                    print(
                        f"Running {len(test_cases)} simulations across "
                        f"{len(sim_files)} files"
                    )
                    sim_results = sim_evals.run_simulations(
                        test_cases,
                        runs=runs,
                        parallel=parallel,
                        modality=modality,
                        background_noise_file=bg_noise_file,
                        burst_noise_files=burst_noise_files,
                    )
                    results["simulation"] = sim_results
                    if output_dir:
                        save_path = os.path.join(output_dir, "sim_results.json")
                        with open(save_path, "w") as f:
                            json.dump(sim_results, f, indent=2)

    # 5. Platform goldens (Wait for results)
    if "goldens" in include and run_name:
        print(f"Waiting for evaluation run {run_name} to complete...")
        utils = EvalUtils(app_name=app_name)
        utils.wait_for_run_and_get_results(
            run_name=run_name, timeout_seconds=golden_timeout
        )
        results["golden"] = load_golden_results(run_name, app_name)

    return results
