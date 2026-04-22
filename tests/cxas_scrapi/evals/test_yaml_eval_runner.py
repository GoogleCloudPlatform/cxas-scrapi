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

import glob
import os

import pytest

from cxas_scrapi.utils.eval_utils import EvalUtils


def pytest_generate_tests(metafunc):
    """
    Dynamically generates tests based on the --eval-dir command-line argument.
    """
    if "yaml_path" in metafunc.fixturenames:
        eval_dir = metafunc.config.getoption("eval_dir")
        if eval_dir:
            target_dir = os.path.abspath(eval_dir)
            yaml_files = sorted(
                glob.glob(os.path.join(target_dir, "*.yaml"))
                + glob.glob(os.path.join(target_dir, "*.yml"))
            )
            metafunc.parametrize(
                "yaml_path", yaml_files, ids=lambda x: os.path.basename(x)
            )
        else:
            # Fallback if no eval-dir is provided, parameterize with empty list
            # or error
            metafunc.parametrize("yaml_path", [])


@pytest.mark.online
def test_evaluation_from_yaml(yaml_path, request):
    """
    Test case that runs a single CXAS evaluation from a YAML file.
    Creates the evaluation, triggers a run, waits for completion,
    and asserts on the pass/fail status of the results.
    """
    app_id = request.config.getoption("app_id")
    if not app_id:
        pytest.fail("--app-id command-line argument is not set.")

    eval_utils = EvalUtils(app_id=app_id)

    # Reload logic: Delete existing evaluation if flag is set
    reload = request.config.getoption("--reload")

    evaluations = eval_utils.load_golden_evals_from_yaml(yaml_path)
    if reload:
        for evaluation in evaluations:
            eval_utils.update_evaluation(evaluation=evaluation, app_id=app_id)

    evals_to_run = [evaluation["displayName"] for evaluation in evaluations]

    # Run Evals
    responses = eval_utils.run_evaluation(evaluations=evals_to_run)
    if hasattr(responses, "result"):
        eval_response = responses.result()
    else:
        eval_response = responses

    # Wait for completion and get results
    results = eval_utils.wait_for_run_and_get_results(
        eval_response.evaluation_run
    )

    assert len(results) > 0, (
        f"No evaluation results found for run {eval_response.evaluation_run}"
    )

    # Each result should have an evaluation_status.
    failed_results = []
    for res in results:
        status_str = EvalUtils._map_outcome(res.evaluation_status)

        display_info = f"Result: {res.name} | Status: {status_str}"
        if status_str != "PASS":
            failed_results.append(display_info)
            print(f"[FAIL] {display_info}")
        else:
            print(f"[PASS] {display_info}")

    assert not failed_results, "The following results failed:\n" + "\n".join(
        failed_results
    )
