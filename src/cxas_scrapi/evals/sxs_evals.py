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

"""Side-by-side evaluation runner for comparing two CXAS Apps."""

import time
from typing import List, Optional

import pandas as pd
import yaml

from cxas_scrapi.evals.simulation_evals import SimulationEvals
from cxas_scrapi.evals.turn_evals import TurnEvals
from cxas_scrapi.utils.rate_limiter import RateLimiter
from cxas_scrapi.utils.reporting import _load_sim_test_cases


def _detect_eval_type(eval_file: str) -> str:
    """Return 'turn' if the YAML has a 'conversations' key, 'sim' if 'evals'."""
    with open(eval_file) as f:
        top = yaml.safe_load(f) or {}
    if "evals" in top:
        return "sim"
    return "turn"


class SxSEvals:
    """Runs the same YAML eval suite against two CES Apps and compares results.

    Supports both TurnEvals (``conversations`` key) and SimulationEvals
    (``evals`` key) YAML formats. The format is auto-detected from the file.

    Example::

        sxs = SxSEvals(
            app_name_a="projects/my-project/locations/global/apps/app-v1",
            app_name_b="projects/my-project/locations/global/apps/app-v2",
            label_a="v1 (baseline)",
            label_b="v2 (candidate)",
        )

        # Turn-eval YAML (conversations: key)
        results = sxs.run_sxs("evals/turn_tests/my_tests.yaml")

        # Simulation YAML (evals: key)
        results = sxs.run_sxs("evals/simulations/my_sims.yaml")

        from cxas_scrapi.utils.sxs_reporting import generate_sxs_html_report
        generate_sxs_html_report(results, "sxs_report.html")
    """

    def __init__(
        self,
        app_name_a: str,
        app_name_b: str,
        label_a: str = "App A",
        label_b: str = "App B",
        creds=None,
        rate_limiter: Optional[RateLimiter] = None,
    ):
        self.label_a = label_a
        self.label_b = label_b
        self._app_name_a = app_name_a
        self._app_name_b = app_name_b
        self._creds = creds
        self._rate_limiter = rate_limiter
        self._evals_a = TurnEvals(
            app_name=app_name_a, creds=creds, rate_limiter=rate_limiter
        )
        self._evals_b = TurnEvals(
            app_name=app_name_b, creds=creds, rate_limiter=rate_limiter
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_sxs(
        self,
        eval_file: str,
        filter_tags: Optional[List[str]] = None,
        runs: int = 1,
        modality: str = "text",
    ) -> dict:
        """Run eval suite against both apps and return structured SxS results.

        Auto-detects whether *eval_file* is a TurnEvals or SimulationEvals
        YAML and dispatches accordingly.

        Args:
            eval_file: Path to a YAML eval file (either format).
            filter_tags: Only run cases whose tags intersect with this list.
            runs: Number of runs per simulation (ignored for turn evals).
            modality: ``"text"`` or ``"audio"`` (sim evals only).

        Returns:
            A dict compatible with
            :func:`cxas_scrapi.utils.sxs_reporting.generate_sxs_html_report`.
        """
        eval_type = _detect_eval_type(eval_file)
        if eval_type == "sim":
            results = self._run_sxs_sims(
                eval_file,
                filter_tags=filter_tags,
                runs=runs,
                modality=modality,
            )
        else:
            results = self._run_sxs_turns(eval_file, filter_tags=filter_tags)
        results["modality"] = modality
        return results

    # ------------------------------------------------------------------
    # Turn-eval path
    # ------------------------------------------------------------------

    def _run_sxs_turns(
        self,
        eval_file: str,
        filter_tags: Optional[List[str]] = None,
    ) -> dict:
        test_cases = self._evals_a.load_turn_test_cases_from_file(eval_file)
        if filter_tags:
            test_cases = [
                tc
                for tc in test_cases
                if any(t in filter_tags for t in tc.tags)
            ]

        print(f"Loaded {len(test_cases)} turn test cases from {eval_file}")

        print(f"\nRunning {len(test_cases)} tests against {self.label_a}...")
        t0 = time.time()
        df_a = self._evals_a.run_turn_tests(
            test_cases, session_id_prefix="sxs_a_"
        )
        dur_a = time.time() - t0

        print(f"\nRunning {len(test_cases)} tests against {self.label_b}...")
        t0 = time.time()
        df_b = self._evals_b.run_turn_tests(
            test_cases, session_id_prefix="sxs_b_"
        )
        dur_b = time.time() - t0

        return self._build_sxs_turn_results(df_a, df_b, dur_a, dur_b)

    def _build_sxs_turn_results(
        self,
        df_a: pd.DataFrame,
        df_b: pd.DataFrame,
        dur_a: float,
        dur_b: float,
    ) -> dict:
        all_names = list(
            dict.fromkeys(
                list(df_a["test_name"].unique())
                + list(df_b["test_name"].unique())
            )
        )

        tests = []
        for name in all_names:
            rows_a = df_a[df_a["test_name"] == name]
            rows_b = df_b[df_b["test_name"] == name]

            pass_a = len(rows_a) > 0 and all(rows_a["status"] == "SUCCESS")
            pass_b = len(rows_b) > 0 and all(rows_b["status"] == "SUCCESS")

            session_a = rows_a["session_id"].iloc[0] if len(rows_a) > 0 else ""
            session_b = rows_b["session_id"].iloc[0] if len(rows_b) > 0 else ""

            turn_ids_a = (
                list(rows_a["turn"].unique()) if len(rows_a) > 0 else []
            )
            turn_ids_b = (
                list(rows_b["turn"].unique()) if len(rows_b) > 0 else []
            )
            all_turn_ids = list(dict.fromkeys(turn_ids_a + turn_ids_b))

            turns = []
            for turn_id in all_turn_ids:
                ta = rows_a[rows_a["turn"] == turn_id]
                tb = rows_b[rows_b["turn"] == turn_id]

                user = (
                    ta["user"].iloc[0]
                    if len(ta) > 0
                    else tb["user"].iloc[0]
                    if len(tb) > 0
                    else ""
                )

                def _agg_checks(rows: pd.DataFrame) -> list:
                    checks = []
                    for _, row in rows.iterrows():
                        if (
                            row["expected"]
                            or row["actual"]
                            or row["status"] == "FAILURE"
                            or row.get("llm_results")
                        ):
                            checks.append(
                                {
                                    "expected": str(row["expected"] or ""),
                                    "actual": str(row["actual"] or ""),
                                    "status": row["status"],
                                    "errors": str(row["errors"] or ""),
                                    "llm_results": str(
                                        row.get("llm_results") or ""
                                    ),
                                }
                            )
                    return checks

                turns.append(
                    {
                        "turn": turn_id,
                        "user": user,
                        "status_a": (
                            "SUCCESS"
                            if len(ta) > 0 and all(ta["status"] == "SUCCESS")
                            else "FAILURE"
                        ),
                        "status_b": (
                            "SUCCESS"
                            if len(tb) > 0 and all(tb["status"] == "SUCCESS")
                            else "FAILURE"
                        ),
                        "checks_a": _agg_checks(ta),
                        "checks_b": _agg_checks(tb),
                    }
                )

            tests.append(
                {
                    "type": "turn",
                    "name": name,
                    "pass_a": pass_a,
                    "pass_b": pass_b,
                    "session_a": session_a,
                    "session_b": session_b,
                    "turns": turns,
                }
            )

        return self._wrap_results(tests, dur_a, dur_b)

    # ------------------------------------------------------------------
    # Simulation-eval path
    # ------------------------------------------------------------------

    def _run_sxs_sims(
        self,
        eval_file: str,
        filter_tags: Optional[List[str]] = None,
        runs: int = 1,
        modality: str = "text",
    ) -> dict:
        test_cases = _load_sim_test_cases(eval_file)
        if filter_tags:
            test_cases = [
                tc
                for tc in test_cases
                if any(t in (tc.get("tags") or []) for t in filter_tags)
            ]

        print(
            f"Loaded {len(test_cases)} simulation test cases from {eval_file}"
        )

        sim_a = SimulationEvals(
            app_name=self._app_name_a,
            creds=self._creds,
            rate_limiter=self._rate_limiter,
        )
        sim_b = SimulationEvals(
            app_name=self._app_name_b,
            creds=self._creds,
            rate_limiter=self._rate_limiter,
        )

        print(f"\nRunning {len(test_cases)} sims against {self.label_a}...")
        t0 = time.time()
        results_a = sim_a.run_simulations(
            test_cases, runs=runs, modality=modality
        )
        dur_a = time.time() - t0

        print(f"\nRunning {len(test_cases)} sims against {self.label_b}...")
        t0 = time.time()
        results_b = sim_b.run_simulations(
            test_cases, runs=runs, modality=modality
        )
        dur_b = time.time() - t0

        return self._build_sxs_sim_results(results_a, results_b, dur_a, dur_b)

    def _build_sxs_sim_results(
        self,
        results_a: list,
        results_b: list,
        dur_a: float,
        dur_b: float,
    ) -> dict:
        # Index by name (use first run per name when runs > 1)
        idx_a = {}
        for r in results_a:
            idx_a.setdefault(r["name"], r)

        idx_b = {}
        for r in results_b:
            idx_b.setdefault(r["name"], r)

        all_names = list(dict.fromkeys(list(idx_a.keys()) + list(idx_b.keys())))

        tests = []
        for name in all_names:
            ra = idx_a.get(name, {})
            rb = idx_b.get(name, {})

            pass_a = ra.get("passed", False)
            pass_b = rb.get("passed", False)

            # Build step comparison
            steps_a = {s["goal"]: s for s in ra.get("step_details", [])}
            steps_b = {s["goal"]: s for s in rb.get("step_details", [])}
            all_goals = list(
                dict.fromkeys(list(steps_a.keys()) + list(steps_b.keys()))
            )

            steps = []
            for goal in all_goals:
                sa = steps_a.get(goal, {})
                sb = steps_b.get(goal, {})
                steps.append(
                    {
                        "goal": goal,
                        "success_criteria": sa.get(
                            "success_criteria",
                            sb.get("success_criteria", ""),
                        ),
                        "status_a": sa.get("status", "Not Started"),
                        "status_b": sb.get("status", "Not Started"),
                        "justification_a": sa.get("justification", ""),
                        "justification_b": sb.get("justification", ""),
                    }
                )

            # Build expectation comparison (matched positionally)
            exps_a = ra.get("expectation_details", [])
            exps_b = rb.get("expectation_details", [])
            max_exps = max(len(exps_a), len(exps_b))
            expectations = []
            for i in range(max_exps):
                ea = exps_a[i] if i < len(exps_a) else {}
                eb = exps_b[i] if i < len(exps_b) else {}
                expectations.append(
                    {
                        "expectation": ea.get(
                            "expectation", eb.get("expectation", "")
                        ),
                        "status_a": ea.get("status", "Not Met"),
                        "status_b": eb.get("status", "Not Met"),
                        "justification_a": ea.get("justification", ""),
                        "justification_b": eb.get("justification", ""),
                    }
                )

            tests.append(
                {
                    "type": "sim",
                    "name": name,
                    "pass_a": pass_a,
                    "pass_b": pass_b,
                    "session_a": ra.get("session_id", ""),
                    "session_b": rb.get("session_id", ""),
                    "goals_a": ra.get("goals", "0/0"),
                    "goals_b": rb.get("goals", "0/0"),
                    "expectations_a": ra.get("expectations", "0/0"),
                    "expectations_b": rb.get("expectations", "0/0"),
                    "turns_a": ra.get("turns", 0),
                    "turns_b": rb.get("turns", 0),
                    "steps": steps,
                    "expectations": expectations,
                    # Use detailed_trace (not transcript) — it has tool calls
                    "trace_a": ra.get("detailed_trace", []),
                    "trace_b": rb.get("detailed_trace", []),
                }
            )

        return self._wrap_results(tests, dur_a, dur_b)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _wrap_results(self, tests: list, dur_a: float, dur_b: float) -> dict:
        _priority = {
            (True, False): 0,
            (False, True): 1,
            (False, False): 2,
            (True, True): 3,
        }
        tests.sort(key=lambda t: _priority[(t["pass_a"], t["pass_b"])])

        total = len(tests)
        pass_a = sum(1 for t in tests if t["pass_a"])
        pass_b = sum(1 for t in tests if t["pass_b"])

        return {
            "label_a": self.label_a,
            "label_b": self.label_b,
            "duration_a": dur_a,
            "duration_b": dur_b,
            "summary": {
                "total": total,
                "pass_a": pass_a,
                "pass_b": pass_b,
                "both_pass": sum(
                    1 for t in tests if t["pass_a"] and t["pass_b"]
                ),
                "both_fail": sum(
                    1 for t in tests if not t["pass_a"] and not t["pass_b"]
                ),
                "regressions": sum(
                    1 for t in tests if t["pass_a"] and not t["pass_b"]
                ),
                "improvements": sum(
                    1 for t in tests if not t["pass_a"] and t["pass_b"]
                ),
            },
            "tests": tests,
        }
