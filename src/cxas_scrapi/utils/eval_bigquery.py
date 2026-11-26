"""Export eval results to BigQuery.

Writes one row per run to ``eval_runs`` and one row per result to
``eval_results`` in a user-provided dataset, and keeps two views
(``v_eval_daily``, ``v_eval_daily_by_tag``) on top of them.

Terms used here:

- run: one export of the evals, with its own ``run_key``;
- case: one simulation, golden, tool test or callback test (``test_id``);
- attempt: one repetition of a case within a run;
- result: the outcome of one attempt, one row of ``eval_results`` (the views
  count results as ``executions``).
"""

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

from __future__ import annotations

import dataclasses
import datetime
import logging
import math
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from cxas_scrapi.core.common import Common

logger = logging.getLogger(__name__)

RUNS_TABLE = "eval_runs"
RESULTS_TABLE = "eval_results"
_DAILY_VIEW = "v_eval_daily"
_DAILY_BY_TAG_VIEW = "v_eval_daily_by_tag"

# Result categories, in the order they are reported.
CATEGORIES = ("simulation", "golden", "tool", "callback")
# Pytest-style categories: a failed assertion always carries a message.
_TEST_CATEGORIES = frozenset({"tool", "callback"})

# Feature name of the export. It tags the BigQuery calls (user agent
# ``cxas-scrapi/<version>:<feature>``) and labels the load jobs, so usage and
# cost can be told apart from other BigQuery activity.
_EXPORT_FEATURE = "eval-bq-export"
_JOB_LABEL_KEY = "cxas-scrapi"
# Longest wait for one BigQuery job, in seconds.
JOB_TIMEOUT_S = 600

# Result fields copied to the ``details`` JSON column: the raw status, what
# was tested, and the verdict on each goal and expectation. It's an
# allow-list because results also carry the conversation (transcripts,
# traces, audio, per-turn texts) and the reports add more of it to them in
# place. None of that may reach BigQuery by accident.
_DETAILS_KEYS = (
    "status",
    "tool",
    "agent",
    "callback_type",
    "latency_ms",
    "step_details",
    "expectation_details",
    "naturalness",
    "naturalness_label",
)

# ``project.dataset`` or ``project:dataset``. The project can be domain-scoped
# (``example.com:project``).
_DATASET_RE = re.compile(r"((?:[\w.-]+:)?[\w-]+)[.:](\w+)")
# "met/total", as in the goals and expectations of a simulation result.
_FRACTION_RE = re.compile(r"\s*(\d+)\s*/\s*(\d+)\s*")
_INT64_MAX = 2**63 - 1

# (name, type, mode) tuples, converted to ``bigquery.SchemaField`` on use.
RUNS_SCHEMA: tuple[tuple[str, str, str], ...] = (
    ("run_key", "STRING", "REQUIRED"),
    ("started_at", "TIMESTAMP", "REQUIRED"),
    ("finished_at", "TIMESTAMP", "NULLABLE"),
    ("eval_duration_s", "FLOAT64", "NULLABLE"),
    ("app_name", "STRING", "NULLABLE"),
    ("suite_name", "STRING", "NULLABLE"),
    ("modality", "STRING", "NULLABLE"),
    ("runs", "INT64", "NULLABLE"),
    ("min_run_passes", "INT64", "NULLABLE"),
    ("min_pass_rate", "FLOAT64", "NULLABLE"),
    ("pass_rate", "FLOAT64", "NULLABLE"),
    ("total_cases", "INT64", "NULLABLE"),
    ("passed_cases", "INT64", "NULLABLE"),
    ("gate_passed", "BOOL", "NULLABLE"),
    ("git_sha", "STRING", "NULLABLE"),
    ("git_ref", "STRING", "NULLABLE"),
    ("ci_run_id", "STRING", "NULLABLE"),
    ("ci_job_id", "STRING", "NULLABLE"),
    ("ci_attempt", "INT64", "NULLABLE"),
    ("cxas_scrapi_version", "STRING", "NULLABLE"),
    ("report_url", "STRING", "NULLABLE"),
)

RESULTS_SCHEMA: tuple[tuple[str, str, str], ...] = (
    ("run_key", "STRING", "REQUIRED"),
    ("started_at", "TIMESTAMP", "REQUIRED"),
    ("category", "STRING", "REQUIRED"),
    ("name", "STRING", "REQUIRED"),
    ("test_id", "STRING", "REQUIRED"),
    ("attempt", "INT64", "NULLABLE"),
    ("tags", "STRING", "REPEATED"),
    ("status", "STRING", "REQUIRED"),
    ("duration_s", "FLOAT64", "NULLABLE"),
    ("turns", "INT64", "NULLABLE"),
    ("goals_met", "INT64", "NULLABLE"),
    ("goals_total", "INT64", "NULLABLE"),
    ("expectations_met", "INT64", "NULLABLE"),
    ("expectations_total", "INT64", "NULLABLE"),
    ("session_id", "STRING", "NULLABLE"),
    ("failed_expectations", "STRING", "REPEATED"),
    ("error", "STRING", "NULLABLE"),
    ("details", "JSON", "NULLABLE"),
)

_RUNS_CLUSTERING = ("suite_name",)
_RESULTS_CLUSTERING = ("category", "name")


class EvalBigQueryError(RuntimeError):
    """Raised when eval results can't be exported to BigQuery."""


@dataclasses.dataclass
class RunContext:
    """Metadata describing one run of the evals."""

    run_key: str
    started_at: datetime.datetime
    finished_at: datetime.datetime | None = None
    # Wall-clock time the evals took to run, when known.
    eval_duration_s: float | None = None
    app_name: str | None = None
    suite_name: str | None = None
    modality: str | None = None
    # Attempts asked for per case (the ``--runs`` option).
    runs: int | None = None
    git_sha: str | None = None
    git_ref: str | None = None
    ci_run_id: str | None = None
    ci_job_id: str | None = None
    ci_attempt: int | None = None
    cxas_scrapi_version: str | None = None
    report_url: str | None = None


def new_run_key() -> str:
    """Returns a unique key identifying one run of the evals."""
    return uuid.uuid4().hex


def cxas_scrapi_version() -> str | None:
    """Returns the installed cxas-scrapi version, if known."""
    try:
        return version("cxas-scrapi")
    except PackageNotFoundError:
        return None


def parse_dataset(bq_dataset: str) -> tuple[str, str]:
    """Splits ``project.dataset`` (or ``project:dataset``) into its parts."""
    match = _DATASET_RE.fullmatch((bq_dataset or "").strip())
    if not match:
        raise ValueError(
            f"Invalid BigQuery dataset {bq_dataset!r}: expected "
            "'project.dataset'."
        )
    return match.group(1), match.group(2)


def bigquery_client(project: str, feature: str) -> bigquery.Client:
    """Returns a BigQuery client whose calls are tagged with ``feature``.

    Uses the library's credentials and user-agent conventions, with
    ``feature`` as the user-agent extension.
    """
    common = Common(user_agent_extension=feature)
    return common.get_bigquery_client(project_id=project)


def job_labels(feature: str) -> dict[str, str]:
    """Returns the labels put on the BigQuery jobs of ``feature``."""
    return {_JOB_LABEL_KEY: feature}


# ---------------------------------------------------------------------------
# Row building (pure functions)
# ---------------------------------------------------------------------------


def _is_missing(value: Any) -> bool:
    """None, NaN, infinity, blank strings and empty collections."""
    if value is None:
        return True
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, str):
        return value.strip().lower() in ("", "nan")
    return isinstance(value, (list, tuple, dict, set)) and not value


def _clean_str(value: Any) -> str | None:
    return None if _is_missing(value) else str(value)


def _to_float(value: Any) -> float | None:
    if _is_missing(value):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _to_int(value: Any) -> int | None:
    """Whole number that fits an INT64 column, else None."""
    number = _to_float(value)
    if number is None or abs(number) > _INT64_MAX:
        return None
    return int(number)


def _jsonable(value: Any) -> Any:
    """Converts a payload to JSON types; NaN and infinity become null."""
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (str, int)):
        return value
    for attr in ("tolist", "isoformat"):  # numpy values, dates
        method = getattr(value, attr, None)
        if callable(method):
            return _jsonable(method())
    return str(value)


def _error_text(item: Mapping[str, Any]) -> str | None:
    """Returns the error message of a result, if it has one."""
    for key in ("error", "errors"):
        value = item.get(key)
        if _is_missing(value):
            continue
        if isinstance(value, (list, tuple, set)):
            value = "; ".join(str(v) for v in value if not _is_missing(v))
        text = str(value).strip()
        if text:
            return text
    return None


def result_status(item: Mapping[str, Any], category: str) -> str:
    """Maps a raw result to PASS, FAIL or ERROR.

    ``ERROR`` means the result couldn't be evaluated (an infrastructure or
    execution problem), so it doesn't count against the agent's pass rate:

    - an explicit ``ERROR`` status, in any category;
    - a failed simulation or golden that carries an error message (their
      status says ``FAIL`` even when the run errored).

    Tool and callback tests are pytest-style: a failed assertion always
    carries a message, so only an explicit ``ERROR`` status is an error.
    """
    if item.get("passed"):
        return "PASS"
    raw_status = str(item.get("status") or "").strip().upper()
    if raw_status in ("ERROR", "ERRORED"):
        return "ERROR"
    if category in _TEST_CATEGORIES:
        return "FAIL"
    return "ERROR" if _error_text(item) else "FAIL"


def _failed_expectations(item: Mapping[str, Any]) -> list[str]:
    details = item.get("expectation_details")
    if not isinstance(details, list):
        details = item.get("expectations")
    if not isinstance(details, list):
        return []
    failed: list[str] = []
    for exp in details:
        if isinstance(exp, dict) and exp.get("status") != "Met":
            text = _clean_str(exp.get("expectation"))
            if text and text not in failed:
                failed.append(text)
    return failed


def _met_and_total(value: Any) -> tuple[int | None, int | None]:
    """Parses a ``"met/total"`` string or a list of ``{"status": ...}``."""
    if isinstance(value, str):
        match = _FRACTION_RE.fullmatch(value)
        if match:
            return _to_int(match.group(1)), _to_int(match.group(2))
    if isinstance(value, list) and value:
        statuses = [v.get("status") for v in value if isinstance(v, dict)]
        if statuses:
            return sum(1 for st in statuses if st == "Met"), len(statuses)
    return None, None


def _turn_count(value: Any) -> int | None:
    return len(value) if isinstance(value, list) else _to_int(value)


def _result_name(item: Mapping[str, Any]) -> str:
    for key in ("name", "test_name", "display_name", "displayName"):
        value = _clean_str(item.get(key))
        if value:
            return value
    return "unnamed"


def _known(value: Any) -> str | None:
    """Text of a field, unless it's missing or the report's "?" placeholder."""
    text = _clean_str(value)
    return None if text == "?" else text


def _test_id(category: str, item: Mapping[str, Any], name: str) -> str:
    """Builds a stable ID; test names repeat across agents and tools."""
    if category == "callback":
        owner = [
            _known(item.get("agent")) or _known(item.get("agent_name")),
            _known(item.get("callback_type")),
        ]
    elif category == "tool":
        owner = [_known(item.get("tool"))]
    else:
        owner = []
    return "/".join([*(part for part in owner if part), name])


def _result_tags(item: Mapping[str, Any], fallback: Sequence[Any]) -> list[str]:
    raw = item.get("tags")
    if not isinstance(raw, (list, tuple)):
        raw = fallback
    tags: list[str] = []
    for tag in raw or []:
        text = _clean_str(tag)
        if text and text not in tags:
            tags.append(text)
    return tags


def _details(item: Mapping[str, Any]) -> dict[str, Any] | None:
    """Returns the ``_DETAILS_KEYS`` fields the result has, as JSON types."""
    kept = {
        key: _jsonable(item[key])
        for key in _DETAILS_KEYS
        if not _is_missing(item.get(key))
    }
    return kept or None


def _iso(value: datetime.datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value.isoformat()


def build_run_row(
    ctx: RunContext, gate_summary: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Builds the ``eval_runs`` row for one run.

    ``pass_rate``, ``total_cases`` and ``passed_cases`` are the gate's
    case-level numbers (a case passes when enough of its attempts pass, and
    an errored case counts as failed), which can differ from rates computed
    over the rows of ``eval_results``.
    """
    summary = gate_summary or {}
    # Without a minimum pass rate there is no gate to pass or fail.
    has_gate = summary.get("min_pass_rate") is not None
    return {
        "run_key": ctx.run_key,
        "started_at": _iso(ctx.started_at),
        "finished_at": _iso(ctx.finished_at),
        "eval_duration_s": _to_float(ctx.eval_duration_s),
        "app_name": ctx.app_name,
        "suite_name": ctx.suite_name,
        "modality": ctx.modality,
        "runs": ctx.runs,
        "min_run_passes": _to_int(summary.get("min_run_passes")),
        "min_pass_rate": _to_float(summary.get("min_pass_rate")),
        "pass_rate": _to_float(summary.get("pass_rate")),
        "total_cases": _to_int(summary.get("total_cases")),
        "passed_cases": _to_int(summary.get("passed_cases")),
        "gate_passed": summary.get("gate_passed") if has_gate else None,
        "git_sha": ctx.git_sha,
        "git_ref": ctx.git_ref,
        "ci_run_id": ctx.ci_run_id,
        "ci_job_id": ctx.ci_job_id,
        "ci_attempt": ctx.ci_attempt,
        "cxas_scrapi_version": ctx.cxas_scrapi_version,
        "report_url": ctx.report_url,
    }


def build_result_rows(
    ctx: RunContext,
    sim_results: Iterable[Mapping[str, Any]] | None = None,
    golden_results: Iterable[Mapping[str, Any]] | None = None,
    tool_results: Iterable[Mapping[str, Any]] | None = None,
    callback_results: Iterable[Mapping[str, Any]] | None = None,
    tags_by_name: Mapping[str, Sequence] | None = None,
) -> list[dict[str, Any]]:
    """Builds one ``eval_results`` row per raw result.

    ``tags_by_name`` maps simulation names to the tags of their definition;
    it is only applied to simulations, so a tool or callback test that shares
    a simulation's name doesn't inherit its tags.
    """
    tags_by_name = tags_by_name or {}
    rows: list[dict[str, Any]] = []
    groups = (sim_results, golden_results, tool_results, callback_results)
    for category, items in zip(CATEGORIES, groups, strict=True):
        attempts: dict[str, int] = {}
        for item in items or []:
            if not isinstance(item, Mapping):
                continue
            name = _result_name(item)
            test_id = _test_id(category, item, name)
            attempt = _to_int(item.get("run"))
            if attempt is None:
                attempts[test_id] = attempts.get(test_id, 0) + 1
                attempt = attempts[test_id]
            duration = _to_float(item.get("duration_s"))
            if duration is None and category == "tool":
                latency_ms = _to_float(item.get("latency_ms"))
                duration = None if latency_ms is None else latency_ms / 1000
            goals_met, goals_total = _met_and_total(item.get("goals"))
            exp_met, exp_total = _met_and_total(item.get("expectations"))
            if exp_total is None:
                exp_met, exp_total = _met_and_total(
                    item.get("expectation_details")
                )
            definition_tags = (
                tags_by_name.get(name, []) if category == "simulation" else []
            )
            rows.append(
                {
                    "run_key": ctx.run_key,
                    "started_at": _iso(ctx.started_at),
                    "category": category,
                    "name": name,
                    "test_id": test_id,
                    "attempt": attempt,
                    "tags": _result_tags(item, definition_tags),
                    "status": result_status(item, category),
                    "duration_s": duration,
                    "turns": _turn_count(item.get("turns")),
                    "goals_met": goals_met,
                    "goals_total": goals_total,
                    "expectations_met": exp_met,
                    "expectations_total": exp_total,
                    "session_id": _clean_str(item.get("session_id")),
                    "failed_expectations": _failed_expectations(item),
                    "error": _error_text(item),
                    "details": _details(item),
                }
            )
    return rows


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


def daily_view_sql(project: str, dataset: str) -> str:
    """Returns the ``v_eval_daily`` definition."""
    return f"""
SELECT
  DATE(r.started_at) AS day,
  r.suite_name,
  x.category,
  COUNT(DISTINCT r.run_key) AS runs,
  COUNT(*) AS executions,
  COUNTIF(x.status = 'PASS') AS passed,
  COUNTIF(x.status = 'FAIL') AS failed,
  COUNTIF(x.status = 'ERROR') AS errors,
  SAFE_DIVIDE(COUNTIF(x.status = 'PASS'),
              COUNTIF(x.status IN ('PASS', 'FAIL'))) AS pass_rate,
  SAFE_DIVIDE(SUM(x.goals_met), SUM(x.goals_total)) AS goal_completion_rate,
  SAFE_DIVIDE(SUM(x.expectations_met),
              SUM(x.expectations_total)) AS expectation_met_rate,
  AVG(x.turns) AS avg_turns,
  APPROX_QUANTILES(x.duration_s, 100)[OFFSET(50)] AS p50_duration_s,
  APPROX_QUANTILES(x.duration_s, 100)[OFFSET(90)] AS p90_duration_s,
  SUM(x.duration_s) AS total_duration_s
FROM `{project}.{dataset}.{RESULTS_TABLE}` AS x
JOIN `{project}.{dataset}.{RUNS_TABLE}` AS r USING (run_key)
GROUP BY day, r.suite_name, x.category
""".strip()


def daily_by_tag_view_sql(project: str, dataset: str) -> str:
    """Returns the ``v_eval_daily_by_tag`` definition."""
    return f"""
SELECT
  DATE(r.started_at) AS day,
  r.suite_name,
  x.category,
  tag,
  COUNT(DISTINCT r.run_key) AS runs,
  COUNT(*) AS executions,
  COUNTIF(x.status = 'PASS') AS passed,
  COUNTIF(x.status = 'FAIL') AS failed,
  COUNTIF(x.status = 'ERROR') AS errors,
  SAFE_DIVIDE(COUNTIF(x.status = 'PASS'),
              COUNTIF(x.status IN ('PASS', 'FAIL'))) AS pass_rate,
  APPROX_QUANTILES(x.duration_s, 100)[OFFSET(50)] AS p50_duration_s
FROM `{project}.{dataset}.{RESULTS_TABLE}` AS x
JOIN `{project}.{dataset}.{RUNS_TABLE}` AS r USING (run_key)
CROSS JOIN UNNEST(x.tags) AS tag
GROUP BY day, r.suite_name, x.category, tag
""".strip()


# ---------------------------------------------------------------------------
# BigQuery writer
# ---------------------------------------------------------------------------


def _schema(
    fields: Sequence[tuple[str, str, str]],
) -> list[bigquery.SchemaField]:
    return [bigquery.SchemaField(n, t, mode=m) for n, t, m in fields]


def _ensure_dataset(
    client: bigquery.Client,
    project: str,
    dataset: str,
    create_dataset: bool,
    location: str | None,
) -> bigquery.Dataset:
    dataset_ref = f"{project}.{dataset}"
    try:
        ds = client.get_dataset(dataset_ref)
    except NotFound as exc:
        if not create_dataset:
            loc = location or "<location>"
            raise EvalBigQueryError(
                f"BigQuery dataset {dataset_ref} not found. Create it first, "
                f"e.g.: bq mk --dataset --location={loc} "
                f"{project}:{dataset} (or pass --bq-create-dataset "
                "--bq-location <location>)."
            ) from exc
        if not location:
            raise EvalBigQueryError(
                "--bq-create-dataset requires --bq-location: the dataset "
                "location can't be changed after creation."
            ) from exc
        new_ds = bigquery.Dataset(dataset_ref)
        new_ds.location = location
        ds = client.create_dataset(new_ds, exists_ok=True)
        logger.info("Created BigQuery dataset %s in %s", dataset_ref, location)
        return ds
    if location and ds.location and ds.location.lower() != location.lower():
        raise EvalBigQueryError(
            f"BigQuery dataset {dataset_ref} is in {ds.location}, not "
            f"{location}."
        )
    return ds


def _ensure_table(
    client: bigquery.Client,
    table_ref: str,
    fields: Sequence[tuple[str, str, str]],
    clustering: Sequence[str],
) -> list[bigquery.SchemaField]:
    """Creates the table if missing and returns the schema to load with.

    A load job must name every column the table has, and may add new ones.
    The returned schema is therefore the table's own columns followed by the
    columns it doesn't have yet, so versions with fewer or more columns can
    append to the same table.
    """
    schema = _schema(fields)
    table = bigquery.Table(table_ref, schema=schema)
    table.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY, field="started_at"
    )
    table.clustering_fields = list(clustering)
    existing = list(client.create_table(table, exists_ok=True).schema)
    known = {field.name for field in existing}
    return existing + [field for field in schema if field.name not in known]


def _ensure_view(client: bigquery.Client, view_ref: str, sql: str) -> None:
    """Creates the view, or updates it when its definition changed."""
    try:
        view = client.get_table(view_ref)
    except NotFound:
        view = bigquery.Table(view_ref)
        view.view_query = sql
        client.create_table(view, exists_ok=True)
        return
    if view.view_query != sql:
        view.view_query = sql
        client.update_table(view, ["view_query"])


def _load_rows(
    client: bigquery.Client,
    table_ref: str,
    rows: list[dict[str, Any]],
    schema: list[bigquery.SchemaField],
    location: str | None,
) -> None:
    if not rows:
        return
    job_config = bigquery.LoadJobConfig(
        schema=schema,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        schema_update_options=[
            bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION
        ],
        labels=job_labels(_EXPORT_FEATURE),
    )
    job = client.load_table_from_json(
        rows, table_ref, job_config=job_config, location=location
    )
    job.result(timeout=JOB_TIMEOUT_S)


def write_eval_results_to_bigquery(
    bq_dataset: str,
    run_row: dict[str, Any],
    result_rows: list[dict[str, Any]],
    create_dataset: bool = False,
    location: str | None = None,
    client: bigquery.Client | None = None,
) -> dict[str, Any]:
    """Appends one run to ``eval_runs`` / ``eval_results``.

    Tables and views are created or updated first, so a problem with them
    fails the export before it writes a row. Rows are appended with load
    jobs, so ``run_row["run_key"]`` must be unique per call. Results are
    loaded before the run row: if a load fails, no run is left behind without
    its results.

    Raises:
        EvalBigQueryError: if the dataset is missing (and not created) or the
            location doesn't match.
    """
    project, dataset = parse_dataset(bq_dataset)
    client = client or bigquery_client(project, _EXPORT_FEATURE)
    ds = _ensure_dataset(client, project, dataset, create_dataset, location)
    job_location = ds.location or location
    runs_ref = f"{project}.{dataset}.{RUNS_TABLE}"
    results_ref = f"{project}.{dataset}.{RESULTS_TABLE}"
    runs_schema = _ensure_table(client, runs_ref, RUNS_SCHEMA, _RUNS_CLUSTERING)
    results_schema = _ensure_table(
        client, results_ref, RESULTS_SCHEMA, _RESULTS_CLUSTERING
    )
    views = (
        (_DAILY_VIEW, daily_view_sql(project, dataset)),
        (_DAILY_BY_TAG_VIEW, daily_by_tag_view_sql(project, dataset)),
    )
    for view, sql in views:
        _ensure_view(client, f"{project}.{dataset}.{view}", sql)
    _load_rows(client, results_ref, result_rows, results_schema, job_location)
    _load_rows(client, runs_ref, [run_row], runs_schema, job_location)
    return {
        "dataset": f"{project}.{dataset}",
        "run_key": run_row["run_key"],
        "results": len(result_rows),
    }


def export_eval_results(
    bq_dataset: str,
    ctx: RunContext,
    gate_summary: Mapping[str, Any] | None,
    sim_results: Iterable[Mapping[str, Any]] | None = None,
    golden_results: Iterable[Mapping[str, Any]] | None = None,
    tool_results: Iterable[Mapping[str, Any]] | None = None,
    callback_results: Iterable[Mapping[str, Any]] | None = None,
    tags_by_name: Mapping[str, Sequence] | None = None,
    create_dataset: bool = False,
    location: str | None = None,
    client: bigquery.Client | None = None,
) -> dict[str, Any]:
    """Builds the rows of one run and writes them to BigQuery.

    Raises on failure. A caller that must not fail because of the export,
    such as the eval report, catches the exception.

    Returns:
        The dataset, the run key and the number of results written.
    """
    return write_eval_results_to_bigquery(
        bq_dataset,
        build_run_row(ctx, gate_summary),
        build_result_rows(
            ctx,
            sim_results=sim_results,
            golden_results=golden_results,
            tool_results=tool_results,
            callback_results=callback_results,
            tags_by_name=tags_by_name,
        ),
        create_dataset=create_dataset,
        location=location,
        client=client,
    )
