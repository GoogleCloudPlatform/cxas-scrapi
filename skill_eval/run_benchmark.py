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

"""Orchestrator for automated CXAS skill benchmarking."""

import asyncio
import dataclasses
import datetime
import json
import os
import pathlib
import sys
import tempfile
import traceback
import uuid
from collections.abc import Sequence

import google.auth
from absl import app, flags, logging

from skill_eval import agent_heads, benchmark, reporting, scenario, scorer

_SCENARIO_PATH = flags.DEFINE_string(
    "scenario_path",
    "skill_eval/scenarios",
    "Path to the scenario .yaml/.yml file or a directory containing them.",
)
_TIMEOUT = flags.DEFINE_integer(
    "timeout", 2400, "Global timeout in seconds for each agent head run."
)
_PROJECT = flags.DEFINE_string(
    "project",
    None,
    "Google Cloud Project ID for Vertex AI evaluation. Discovered automatically from ADC credentials if omitted.",
)
_LOCATION = flags.DEFINE_string(
    "location",
    "us",
    "Google Cloud Location for CXAS application.",
)
_MODEL_LOCATION = flags.DEFINE_string(
    "model_location",
    "global",
    "Google Cloud Location for Vertex AI model calls.",
)
_SCORER_MODEL = flags.DEFINE_string(
    "scorer_model",
    "gemini-3.1-pro-preview",
    "Gemini model for scoring/rubric.",
)
_OUTPUT_DIR = flags.DEFINE_string(
    "output_dir",
    "skill_eval/reports",
    "Base directory to store reports. A timestamped subdirectory will be "
    "created automatically for each run.",
)
_RUN_FAKE = flags.DEFINE_bool(
    "run_fake", False, "Whether to run the fake scaffolding head."
)
_RUN_ANTIGRAVITY = flags.DEFINE_bool(
    "run_antigravity", True, "Whether to run the Antigravity head."
)

_LS_ADDRESS = flags.DEFINE_string(
    "ls_address",
    None,
    "Address of an existing language server to use for Antigravity Head.",
)
_LS_CSRF_TOKEN = flags.DEFINE_string(
    "ls_csrf_token",
    None,
    "CSRF token for the existing language server.",
)
_ANTIGRAVITY_MODEL = flags.DEFINE_string(
    "antigravity_model",
    "gemini-3.5-flash",
    "The model name for Antigravity head (e.g. gemini-3.5-flash).",
)
_INPUT_REPORT_DIR = flags.DEFINE_string(
    "input_report_dir",
    "",
    "Path to an existing report directory. If provided, skips experiment"
    " execution and regenerates the report.",
)


def _get_workspace_root() -> str:
    """Returns the absolute path to the workspace root folder."""
    if "BUILD_WORKSPACE_DIRECTORY" in os.environ:
        return os.environ["BUILD_WORKSPACE_DIRECTORY"]
    cwd = os.getcwd()

    # Traverse upwards to locate standard workspace indicators like .git or pyproject.toml
    try:
        curr = pathlib.Path(cwd).resolve()
        for parent in [curr, *curr.parents]:
            if (parent / ".git").exists() or (
                parent / "pyproject.toml"
            ).exists():
                return str(parent)
    except Exception:
        pass

    return cwd


def _dump_reproducibility_metadata(
    output_dir: str, workspace_root: str
) -> None:
    """Dumps reproducibility metadata to the report folder."""
    metadata = {}

    metadata["cli_args"] = sys.argv
    metadata["cwd"] = os.getcwd()
    metadata["workspace_root"] = workspace_root

    env_filtered = {}
    for k, v in os.environ.items():
        lower_k = k.lower()
        if k == "SSH_AUTH_KEY":
            env_filtered[k] = v
        elif any(
            secret_term in lower_k
            for secret_term in ["token", "auth", "key", "password", "secret"]
        ):
            env_filtered[k] = "[REDACTED]"
        else:
            env_filtered[k] = v
    metadata["env"] = env_filtered

    vcs_info = {}
    try:
        # Resolve local Git metadata safely if present (open source)
        git_dir = os.path.join(workspace_root, ".git")
        if os.path.exists(git_dir):
            # Read head ref to obtain revision id safely
            head_path = os.path.join(git_dir, "HEAD")
            if os.path.exists(head_path):
                with open(head_path) as f:
                    ref = f.read().strip()
                if ref.startswith("ref:"):
                    ref_path = os.path.join(git_dir, ref.split(" ")[1])
                    if os.path.exists(ref_path):
                        with open(ref_path) as f:
                            vcs_info["git_revision"] = f.read().strip()
                else:
                    vcs_info["git_revision"] = ref
    except Exception:
        pass
    metadata["vcs"] = vcs_info

    repro_path = os.path.join(output_dir, "reproducibility.json")
    with open(repro_path, "w") as f:
        json.dump(metadata, f, indent=2)
    os.chmod(repro_path, 0o644)


class BenchmarkOrchestrator:
    """Orchestrates parallel execution of benchmark scenarios."""

    def __init__(
        self,
        report_dir: str,
        project: str | None = None,
        model_location: str = "global",
    ):
        self._report_dir = report_dir
        self._results: list[benchmark.ConversationResult] = []
        self._lock = asyncio.Lock()
        self._init_semaphore = asyncio.Semaphore(
            1
        )  # Strict sequential initialization
        self._run_semaphore = asyncio.Semaphore(
            1
        )  # Sequential runs to eliminate process-global os.environ race conditions
        self._scorer = scorer.Scorer(
            project=project,
            location=model_location,
            model_name=_SCORER_MODEL.value,
        )

    async def initialize_reports(
        self,
        *,
        scenarios: Sequence[scenario.Scenario],
        enabled_heads: Sequence[str],
    ) -> None:
        """Creates initial hub (index.html) with all scenarios as pending.

        Args:
          scenarios: List of loaded Scenario objects.
          enabled_heads: List of enabled head names.
        """
        for scen in scenarios:
            for head in enabled_heads:
                self._results.append(
                    benchmark.ConversationResult(
                        scenario_name=scen.name,
                        scenario_text=scen.text,
                        head_name=head,
                        turns=0,
                        total_time_sec=0.0,
                        messages=[],
                        turn_metrics=[],
                        status=benchmark.ExecutionStatus.PENDING,
                        success=None,
                    )
                )
        await self.update_reports()

    async def report_crash(self, error_msg: str) -> None:
        """Updates index.html with a critical suite-level failure message."""
        async with self._lock:
            index_html = reporting.generate_index_html(self._results)
            crash_html = (
                '<div style="background: #fce8e6; color: #c5221f; padding: 2rem;'
                " border-radius: 8px; border: 1px solid #f5c6cb; margin-bottom:"
                ' 2rem; font-weight: bold;">'
                f"CRITICAL SUITE FAILURE: {error_msg}</div>"
            )
            index_html = index_html.replace(
                "<h1>CXAS Skill Evaluation Benchmark</h1>",
                f"<h1>CXAS Skill Evaluation Benchmark</h1>{crash_html}",
            )
            reporting.save_report(
                index_html, os.path.join(self._report_dir, "index.html")
            )

    async def run_head(
        self,
        head: benchmark.BaseAgentHead,
        runner: benchmark.BenchmarkRunner,
    ) -> None:
        """Runs a single agent head with a timeout and updates reports."""
        scenario_name = runner.scenario_name
        scenario_text = runner.scenario_text

        async with self._run_semaphore:
            logging.info(
                "[%s] [%s] Starting evaluation...", scenario_name, head.name
            )

            try:
                # BenchmarkRunner.run now handles sim, turns,
                # grading, and granular status updates.
                final_res = await asyncio.wait_for(
                    runner.run(
                        head,
                        on_turn_completed=self.update_reports,
                        init_semaphore=self._init_semaphore,
                    ),
                    timeout=float(_TIMEOUT.value),
                )

                await self.update_reports(final_res)
                logging.info(
                    "[%s] [%s] Finished successfully.", scenario_name, head.name
                )
            except asyncio.TimeoutError:
                logging.error(
                    "[%s] [%s] Timed out after %ds.",
                    scenario_name,
                    head.name,
                    _TIMEOUT.value,
                )
                await self._handle_error(
                    head,
                    scenario_name,
                    scenario_text,
                    f"Timed out after {_TIMEOUT.value}s.",
                )
            except Exception as e:
                logging.exception(
                    "[%s] [%s] Unexpected failure: %s",
                    scenario_name,
                    head.name,
                    e,
                )
                await self._handle_error(
                    head, scenario_name, scenario_text, f"Crashed: {e}"
                )
            finally:
                await head.close()

    async def update_reports(
        self, partial_res: benchmark.ConversationResult | None = None
    ) -> None:
        """Updates internal state and writes reporting files."""
        async with self._lock:
            if partial_res:
                # Update or add result
                found = False
                for i, r in enumerate(self._results):
                    if (
                        r.scenario_name == partial_res.scenario_name
                        and r.head_name == partial_res.head_name
                    ):
                        self._results[i] = partial_res
                        found = True
                        break
                if not found:
                    self._results.append(partial_res)

            # Generate Summary Report (index.html)
            index_html = reporting.generate_index_html(self._results)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                reporting.save_report,
                index_html,
                os.path.join(self._report_dir, "index.html"),
            )

            # Generate Individual Detailed Reports (.html and .json)
            if partial_res:
                detail_html = reporting.generate_detail_html(
                    partial_res, report_dir=self._report_dir
                )
                safe_name = scenario.get_safe_filename(
                    partial_res.scenario_name
                )
                filename_base = f"{safe_name}_{partial_res.head_name}"
                await loop.run_in_executor(
                    None,
                    reporting.save_report,
                    detail_html,
                    os.path.join(
                        self._report_dir, f"detail_{filename_base}.html"
                    ),
                )
                json_report = reporting.generate_json_report(partial_res)
                await loop.run_in_executor(
                    None,
                    reporting.save_report,
                    json_report,
                    os.path.join(self._report_dir, f"{filename_base}.json"),
                )

    async def _handle_error(
        self,
        head: benchmark.BaseAgentHead,
        scenario_name: str,
        scenario_text: str,
        msg: str,
    ) -> None:
        """Updates the reporting state with a failure result."""
        res = benchmark.ConversationResult(
            scenario_name=scenario_name,
            scenario_text=scenario_text,
            head_name=head.name,
            turns=0,
            total_time_sec=0.0,
            messages=[],
            turn_metrics=[],
            status=benchmark.ExecutionStatus.FAILED,
            success=False,
            failure_reason=msg,
        )
        await self.update_reports(res)


def _get_default_project_id() -> str:
    """Discovers the GCP project ID dynamically from ADC credentials."""
    try:
        _, project_id = google.auth.default()
        if project_id:
            return project_id
    except Exception:
        pass
    raise RuntimeError(
        "Could not discover GCP project ID dynamically. Please specify --project or configure ADC credentials."
    )


async def main_async() -> None:
    """Main entry point for the benchmark orchestrator."""

    workspace_root = _get_workspace_root()
    if workspace_root:
        logging.info(
            "Changing global CWD to workspace root: %s", workspace_root
        )
        os.chdir(workspace_root)

    scenario_files = []
    raw_paths = _SCENARIO_PATH.value.split(",")

    for raw_path in raw_paths:
        path = raw_path
        if not os.path.isabs(path):
            # Always try relative to workspace root first
            path = os.path.join(workspace_root, raw_path)

        # Fallback if root join didn't work (e.g. absolute path).
        if not os.path.exists(path) and os.path.isabs(raw_path):
            path = raw_path

        if os.path.isdir(path):
            scenario_files.extend(
                [
                    os.path.join(path, f)
                    for f in os.listdir(path)
                    if (f.endswith(".yaml") or f.endswith(".yml"))
                ]
            )
        elif os.path.isfile(path):
            scenario_files.append(path)
        else:
            raise ValueError(
                f"Scenario path not found: {raw_path} (resolved to {path})"
            )

    if not scenario_files:
        raise ValueError(f"No YAML scenarios found in {_SCENARIO_PATH.value}")

    scenario_files.sort()

    output_dir = _OUTPUT_DIR.value
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = os.path.join(output_dir, f"run-{timestamp}")

    if not output_dir.startswith("/"):
        output_dir = os.path.join(workspace_root, output_dir)
    os.makedirs(output_dir, mode=0o755, exist_ok=True)
    os.chmod(output_dir, 0o755)
    _dump_reproducibility_metadata(output_dir, workspace_root)

    project_id = _PROJECT.value or _get_default_project_id()
    orchestrator = BenchmarkOrchestrator(
        report_dir=output_dir,
        project=project_id,
        model_location=_MODEL_LOCATION.value,
    )

    # Initialized below if not input_report_dir

    if _INPUT_REPORT_DIR.value:
        input_dir = _INPUT_REPORT_DIR.value
        if not os.path.isabs(input_dir):
            input_dir = os.path.join(workspace_root, input_dir)

        logging.info(
            "Regenerating report from existing directory using gfile: %s",
            input_dir,
        )
        count = 0
        try:
            files = os.listdir(input_dir)
        except Exception as e:
            logging.error("Failed to list directory %s: %s", input_dir, e)
            sys.exit(1)

        for f in sorted(files):
            if f.endswith(".json") and f != "reproducibility.json":
                full_path = os.path.join(input_dir, f)
                try:
                    with open(full_path) as fp:
                        raw_data = fp.read()

                    with tempfile.NamedTemporaryFile(
                        mode="wt", suffix=".json", delete=False
                    ) as tmp:
                        tmp.write(raw_data)
                        tmp_path = tmp.name
                    res = reporting.load_json_report(tmp_path)
                    os.unlink(tmp_path)
                    await orchestrator.update_reports(res)
                    count += 1
                except Exception as e:
                    logging.error(
                        "Failed reading report file %s: %s", full_path, e
                    )
                    sys.exit(1)

        if count == 0:
            logging.error(
                "CRITICAL: Found 0 .json report files in %s. Aborting to prevent"
                " silent empty reports.",
                input_dir,
            )
            sys.exit(1)

        logging.info("Successfully loaded %d conversation results.", count)
        await orchestrator.update_reports()
        logging.info("Successfully regenerated reports into %s", output_dir)
        return

    # Immediate hub creation for better UX
    enabled_heads = [
        head_name
        for head_name, is_enabled in [
            ("ScaffoldingTestAgent", _RUN_FAKE.value),
            ("Antigravity", _RUN_ANTIGRAVITY.value),
        ]
        if is_enabled
    ]

    # Load scenarios early to get consistent names
    scenarios_with_paths = []
    for scenario_path in scenario_files:
        try:
            scen = scenario.Scenario.from_file(scenario_path)
            scenarios_with_paths.append((scen, scenario_path))
        except ValueError:
            logging.exception("Failed to parse scenario %s", scenario_path)
            sys.exit(1)

    seen_names = set()
    for scen, path in scenarios_with_paths:
        if scen.name in seen_names:
            logging.error(
                "Duplicate scenario name detected: %s. File: %s",
                scen.name,
                path,
            )
            sys.exit(1)
        seen_names.add(scen.name)

    # Extract scenarios for initialization
    scenarios = [s for s, _ in scenarios_with_paths]

    await orchestrator.initialize_reports(
        scenarios=scenarios, enabled_heads=enabled_heads
    )
    index_path = os.path.join(output_dir, "index.html")
    url = pathlib.Path(index_path).resolve().as_uri()
    print(f"\nBenchmark Hub: {url}\n", flush=True)

    antigravity_model_val = _ANTIGRAVITY_MODEL.value

    tasks = []
    for scen, scenario_path in scenarios_with_paths:
        run_uuid = uuid.uuid4().hex[:8]

        def subst(
            text: str, run_uuid: str = run_uuid, project_id: str = project_id
        ) -> str:
            return text.replace("{uuid}", run_uuid).replace(
                "{project_id}", project_id
            )

        formatted_setup = dataclasses.replace(
            scen.setup,
            commands=tuple(subst(cmd) for cmd in scen.setup.commands),
        )
        formatted_rubric = [
            dataclasses.replace(
                r,
                criteria=subst(r.criteria),
                perfect=subst(r.perfect),
                good=subst(r.good),
                failed=subst(r.failed),
            )
            for r in scen.rubric
        ]
        formatted_scen = dataclasses.replace(
            scen,
            prompt=subst(scen.prompt),
            text=subst(scen.text),
            setup=formatted_setup,
            rubric=formatted_rubric,
        )

        runner = benchmark.BenchmarkRunner(
            scen=formatted_scen,
            scorer_instance=orchestrator._scorer,
        )

        if _RUN_FAKE.value:
            tasks.append(
                orchestrator.run_head(
                    agent_heads.ScaffoldingTestAgent(
                        scenario_name=formatted_scen.name,
                        name="ScaffoldingTestAgent",
                    ),
                    runner,
                )
            )

        if _RUN_ANTIGRAVITY.value:
            tasks.append(
                orchestrator.run_head(
                    agent_heads.AntigravityAgentHead(
                        scenario_name=formatted_scen.name,
                        ls_address=_LS_ADDRESS.value,
                        ls_csrf_token=_LS_CSRF_TOKEN.value,
                        model=antigravity_model_val,
                        project=_PROJECT.value,
                        assets=formatted_scen.assets,
                        setup_commands=formatted_scen.setup.commands,
                        scenario_path=scenario_path,
                        run_uuid=run_uuid,
                    ),
                    runner,
                )
            )

    if tasks:
        logging.info(
            "Executing %d total benchmarks concurrently...", len(tasks)
        )
        try:
            await asyncio.gather(*tasks)
        except Exception as e:
            logging.exception("Suite execution failed")
            await orchestrator.report_crash(str(e))

    index_path = os.path.join(output_dir, "index.html")
    url = pathlib.Path(index_path).resolve().as_uri()
    print(f"\nFinal report available at: {url}\n")


def main(argv: Sequence[str]) -> None:
    if len(argv) > 1:
        raise app.UsageError("Too many command-line arguments.")

    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        logging.exception("Benchmark failed: %s", e)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    app.run(main)
