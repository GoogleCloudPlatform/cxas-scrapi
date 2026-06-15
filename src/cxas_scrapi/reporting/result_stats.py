from dataclasses import dataclass
from typing import Any


@dataclass
class CategoryStats:
    """Strongly-typed category evaluation statistics metrics container."""

    passed: int
    total: int
    pct: float
    pct_str: str
    value_class: str
    duration_s: float
    modality: str


import pandas as pd
from cxas_scrapi.reporting.result_extractors import (
    GoldenRunResult,
    SimulationRunResult,
    ToolRunResult,
    CallbackRunResult,
)


def _backfill_missing_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Backfills missing statistical values in the aggregated metrics DataFrame.

    Ensures that categories without execution results have safe defaults (e.g., 0
    total,
    0 passed, 0.0 duration, and passed_all set to True).
    """
    df["passed"] = df["passed"].fillna(0).astype(int)
    df["total"] = df["total"].fillna(0).astype(int)
    df["pct"] = df["pct"].fillna(0.0).astype(float)
    df["duration_s"] = df["duration_s"].fillna(0.0).astype(float)
    df["passed_all"] = df["passed_all"].fillna(True).astype(bool)
    return df


def _backfill_default_modalities(df: pd.DataFrame) -> pd.DataFrame:
    """Backfills default modality mappings for missing category rows.

    Standardizes missing modalities to 'text' for goldens/simulations, 'tool' for
    tool tests,
    and 'callback' for callback tests.
    """
    default_modalities = {
        "golden": "text",
        "sim": "text",
        "tool": "tool",
        "callback": "callback",
    }
    df["modality"] = df["modality"].fillna(pd.Series(default_modalities))
    return df


def _apply_simulation_clock_override(
    df: pd.DataFrame, sim_results: list[SimulationRunResult]
) -> pd.DataFrame:
    """Overrides the simulation elapsed duration with the actual total simulation wall clock time.

    By default, summing the durations of individual simulation runs results in a
    cumulative
    elapsed execution time. If a simulation wall clock duration is explicitly
    recorded
    on the simulation run, we override the 'sim' row duration with this value to
    reflect
    the actual real-world execution span.
    """
    if sim_results and sim_results[0].sim_wall_clock_s > 0:
        df.loc["sim", "duration_s"] = float(sim_results[0].sim_wall_clock_s)
    return df


def _enrich_formatting_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Enriches the aggregated statistics DataFrame with formatted presentation columns for UI rendering.

    Adds 'pct_str' containing the rounded integer percentage for display and
    'value_class'
    containing the CSS class name ('pass' or 'fail') based on whether all tests in
    the category
    passed successfully.
    """
    df["pct_str"] = df["pct"].map(lambda x: f"{x:.0f}")
    df["value_class"] = df["passed_all"].map(lambda x: "pass" if x else "fail")
    return df


def get_evaluation_result_stats(
    *,
    golden_results: list[GoldenRunResult],
    sim_results: list[SimulationRunResult],
    tool_results: list[ToolRunResult],
    callback_results: list[CallbackRunResult],
) -> pd.DataFrame:
    """Aggregates and compiles all result metrics statistics via a single-shot Pandas GroupBy.

    Index: ['golden', 'sim', 'tool', 'callback']
    Columns: ['passed', 'total', 'pct', 'duration_s', 'modality', 'passed_all',
    'pct_str', 'value_class']

    Args:
      golden_results: Strongly-typed golden results list.
      sim_results: Strongly-typed simulation results list.
      tool_results: Strongly-typed tool results list.
      callback_results: Strongly-typed callback results list.

    Returns:
      A unified metrics statistics Pandas DataFrame.
    """
    # Standardize and merge all results list cleanly using list comprehensions and list concatenation.
    all_records = (
        [
            {
                "type": "golden",
                "passed": r.passed,
                "duration_s": r.duration_s,
                "modality": r.modality,
            }
            for r in golden_results
        ]
        + [
            {
                "type": "sim",
                "passed": r.passed,
                "duration_s": r.duration_s,
                "modality": r.modality,
            }
            for r in sim_results
        ]
        + [
            {
                "type": "tool",
                "passed": r.passed,
                "duration_s": 0.0,
                "modality": "tool",
            }
            for r in tool_results
        ]
        + [
            {
                "type": "callback",
                "passed": r.passed,
                "duration_s": 0.0,
                "modality": "callback",
            }
            for r in callback_results
        ]
    )

    # If no records exist, return a standardized default empty DataFrame.
    if not all_records:
        return pd.DataFrame(
            columns=[
                "passed",
                "total",
                "pct",
                "duration_s",
                "modality",
                "passed_all",
                "pct_str",
                "value_class",
            ],
            index=["golden", "sim", "tool", "callback"],
        ).fillna(
            {
                "passed": 0,
                "total": 0,
                "pct": 0.0,
                "duration_s": 0.0,
                "modality": "text",
                "passed_all": True,
                "pct_str": "0",
                "value_class": "pass",
            }
        )

    # Load into a single unified DataFrame.
    df = pd.DataFrame(all_records)

    # Convert boolean passed column directly to integer.
    df["passed"] = df["passed"].astype(int)

    # Aggregate columns using GroupBy to calculate statistics.
    stats_df = df.groupby("type").agg(
        passed=("passed", "sum"),
        total=("passed", "count"),
        pct=("passed", lambda x: float(x.mean() * 100)),
        duration_s=("duration_s", "sum"),
        passed_all=("passed", lambda x: bool(x.all())),
        modality=("modality", "first"),
    )

    # Reindex to standard categories, backfilling missing results types.
    standard_index = ["golden", "sim", "tool", "callback"]
    stats_df = stats_df.reindex(standard_index)

    # Chain private helpers sequentially using Pandas DataFrame pipe chaining.
    stats_df = (
        stats_df.pipe(_backfill_missing_stats)
        .pipe(_backfill_default_modalities)
        .pipe(_apply_simulation_clock_override, sim_results=sim_results)
        .pipe(_enrich_formatting_columns)
    )

    return stats_df


def classify_failure_groups(
    *,
    golden_results: list[GoldenRunResult],
    sim_results: list[SimulationRunResult],
    tool_results: list[ToolRunResult],
    callback_results: list[CallbackRunResult],
) -> dict[str, set[tuple[str, str]]]:
    """Analyzes and groups all failed evaluation items by their failure reasons."""
    failure_groups: dict[str, set[tuple[str, str]]] = {}

    # 1. Golden failures classification
    for r in golden_results:
        if r.passed:
            continue
        for turn in r.turns:
            for comp in turn.get("comparisons", []):
                if comp.get("outcome") == "FAIL":
                    ctype = comp.get("type", "?")
                    expected = str(comp.get("expected", ""))[:60]
                    actual = str(comp.get("actual", ""))[:60]
                    if ctype == "transfer":
                        if actual == "(missed)":
                            reason = f"Routing missed: expected transfer to {expected}"
                        else:
                            reason = f"Wrong routing: expected {expected}, got {actual}"
                    elif ctype == "tool_call" and actual == "(missed)":
                        if expected:
                            reason = f"Tool not called: {expected}"
                        else:
                            continue
                    elif ctype == "tool_call" and expected != actual:
                        reason = (
                            f"Wrong tool: expected {expected}, got {actual}"
                        )
                    elif ctype == "text":
                        reason = "Semantic similarity too low"
                    else:
                        continue
                    failure_groups.setdefault(reason, set()).add(
                        ("golden", r.name)
                    )
        for exp in r.expectations:
            if not exp.is_met:
                reason = exp.expectation[:80]
                failure_groups.setdefault(
                    f"Expectation not met: {reason}", set()
                ).add(("golden", r.name))

    # 2. Simulation failures classification
    for r in sim_results:
        if r.passed:
            continue
        for step in r.step_details:
            if step.status != "Completed":
                reason = f"Goal not completed: {step.goal[:60]}"
                failure_groups.setdefault(reason, set()).add(("sim", r.name))
        for exp in r.expectation_details:
            if not exp.is_met:
                reason = f"Expectation not met: {exp.expectation[:60]}"
                failure_groups.setdefault(reason, set()).add(("sim", r.name))

    # 3. Tool failures classification
    for r in tool_results:
        if r.passed:
            continue
        errors = r.errors
        if (
            "operator='Operator.CONTAINS'" in errors
            and "expected='PASSED'" in errors
        ):
            reason = "Default expectation: $.result contains PASSED (needs customization)"
        elif "operator='Operator" in errors:
            reason = (
                errors.split(",", maxsplit=1)[0][:80]
                if "," in errors
                else errors[:80]
            )
        else:
            reason = errors[:80] if errors else "Unknown tool failure"
        failure_groups.setdefault(reason, set()).add(("tool", r.name))

    # 4. Callback failures classification
    for r in callback_results:
        if r.passed:
            continue
        reason = r.error if r.error else "Unknown error"
        failure_groups.setdefault(f"Callback: {reason[:80]}", set()).add(
            ("callback", r.name)
        )

    return failure_groups


class EvaluationStats:
    """Strongly-typed wrapper encapsulating compiled metrics Pandas DataFrame."""

    def __init__(
        self,
        df: pd.DataFrame,
        golden_results: list[GoldenRunResult] | None = None,
        sim_results: list[SimulationRunResult] | None = None,
        tool_results: list[ToolRunResult] | None = None,
        callback_results: list[CallbackRunResult] | None = None,
    ):
        self._df = df
        self.golden_results = golden_results or []
        self.sim_results = sim_results or []
        self.tool_results = tool_results or []
        self.callback_results = callback_results or []

    def _get_category(self, type_str: str) -> CategoryStats:
        """Pristine internal helper to package a CategoryStats container."""
        return CategoryStats(
            passed=int(self._df.loc[type_str, "passed"]),
            total=int(self._df.loc[type_str, "total"]),
            pct=float(self._df.loc[type_str, "pct"]),
            pct_str=str(self._df.loc[type_str, "pct_str"]),
            value_class=str(self._df.loc[type_str, "value_class"]),
            duration_s=float(self._df.loc[type_str, "duration_s"]),
            modality=str(self._df.loc[type_str, "modality"]),
        )

    @property
    def golden(self) -> CategoryStats:
        """Pristine Golden test category metrics."""
        return self._get_category("golden")

    @property
    def sim(self) -> CategoryStats:
        """Pristine Simulation test category metrics."""
        return self._get_category("sim")

    @property
    def tool(self) -> CategoryStats:
        """Pristine Tool test category metrics."""
        return self._get_category("tool")

    @property
    def callback(self) -> CategoryStats:
        """Pristine Callback test category metrics."""
        return self._get_category("callback")

    @property
    def passed_sum(self) -> int:
        """Returns the overall combined passed sum across all categories."""
        return int(self._df["passed"].sum())

    @property
    def total_sum(self) -> int:
        """Returns the overall combined total sum across all categories."""
        return int(self._df["total"].sum())

    @property
    def overall_pct(self) -> float:
        """Calculates and returns the overall combined pass rate percentage."""
        passed = self.passed_sum
        total = self.total_sum
        return (100 * passed / total) if total else 0.0

    @property
    def failure_groups(self) -> dict[str, set[tuple[str, str]]]:
        """Categorizes and groups all failed evaluation items by failure reasons."""
        return classify_failure_groups(
            golden_results=self.golden_results,
            sim_results=self.sim_results,
            tool_results=self.tool_results,
            callback_results=self.callback_results,
        )
