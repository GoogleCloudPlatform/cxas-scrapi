"""Markdown trends report for eval results exported to BigQuery.

Reads the ``eval_runs`` / ``eval_results`` tables written by
:mod:`cxas_scrapi.utils.eval_bigquery` and renders a self-contained Markdown
report with Mermaid charts: pass rate per day and per category, changes since
the previous run, longest-failing cases, tags (worst and biggest changes),
most frequent failed expectations, volume, run time and flaky cases.
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

import collections
import json
import math
import os
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from google.cloud import bigquery

from cxas_scrapi.utils import eval_bigquery

# Feature name of the trends report: user-agent extension and job label.
_TRENDS_FEATURE = "eval-bq-trends"

# Start of the period: today and the days before it, in UTC, so that every
# day of the report is a whole day.
_PERIOD = "TIMESTAMP(DATE_SUB(CURRENT_DATE(), INTERVAL @days - 1 DAY))"
_COUNTS = """COUNT(*) AS executions,
  COUNTIF(x.status = 'PASS') AS passed,
  COUNTIF(x.status = 'FAIL') AS failed,
  COUNTIF(x.status = 'ERROR') AS errors"""
_COUNTERS = (
    "runs",
    "executions",
    "passed",
    "failed",
    "errors",
    "goals_met",
    "goals_total",
    "expectations_met",
    "expectations_total",
    "turns",
    "results_with_turns",
)
# Longest lists and tables of the report.
_MAX_CHANGES = 20  # new failures, and fixed cases
_MAX_LONGEST_FAILING = 15
_MAX_TAGS = 15
_MAX_TAG_CHANGES = 5  # largest drops, and largest gains
_MAX_FAILED_EXPECTATIONS = 10
_MAX_RUNS = 10
_MAX_FLAKY = 10
# Days shown per tag, and the longest window compared for tag changes.
_TAG_DAYS = 7
# Results a tag needs in each window before its pass rates are compared.
_MIN_WINDOW_RESULTS = 10
# A line chart needs two points.
_MIN_LINE_POINTS = 2
_NA = "n/a"
_NEEDS_TWO_RUNS = (
    "Needs at least two runs of a suite and category in the period."
)

_Row = Mapping[str, Any]


def _clean_filter(value: str | None) -> str | None:
    """Turns blank filter strings (for example unset CI variables) into None."""
    text = (value or "").strip()
    return text or None


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def trends_queries(
    project: str, dataset: str, min_tag_cases: int = 5
) -> dict[str, str]:
    """Returns the named queries behind the trends report.

    Every query takes three parameters: ``@days`` (INT64), ``@suite`` and
    ``@ref`` (STRING, NULL for all suites / all git refs). A case "passes" in
    a run when at least one of its attempts passed. Runs without results are
    ignored. The latest and the previous run are found per suite and
    category, so a run of some categories only is compared with the last run
    that had them. Tags used by fewer than ``min_tag_cases`` different cases
    (for example a unique ID per case) are left out of ``tags`` and of the
    tags listed with each change; ``tag_summary`` counts all tags and the
    reported ones.
    """
    runs = f"`{project}.{dataset}.{eval_bigquery.RUNS_TABLE}`"
    results = f"`{project}.{dataset}.{eval_bigquery.RESULTS_TABLE}`"
    joined = f"{results} AS x JOIN {runs} AS r USING (run_key)"
    # The filter on x.started_at prunes the partitions of eval_results.
    where = (
        f"r.started_at >= {_PERIOD}\n"
        "  AND (@suite IS NULL OR r.suite_name = @suite)\n"
        "  AND (@ref IS NULL OR r.git_ref = @ref)\n"
        f"  AND x.started_at >= {_PERIOD}"
    )
    per_case_run = f"""
  SELECT IFNULL(r.suite_name, '') AS suite, x.category, x.test_id,
    r.run_key, r.started_at,
    LOGICAL_OR(x.status = 'PASS') AS has_pass,
    LOGICAL_OR(x.status = 'FAIL') AS has_fail
  FROM {joined}
  WHERE {where}
  GROUP BY suite, x.category, x.test_id, r.run_key, r.started_at"""
    return {
        "daily": f"""
SELECT DATE(r.started_at) AS day,
  COUNT(DISTINCT r.run_key) AS runs, {_COUNTS},
  SUM(IF(x.status != 'ERROR', x.goals_met, NULL)) AS goals_met,
  SUM(IF(x.status != 'ERROR', x.goals_total, NULL)) AS goals_total,
  SUM(IF(x.status != 'ERROR', x.expectations_met, NULL)) AS expectations_met,
  SUM(
    IF(x.status != 'ERROR', x.expectations_total, NULL)
  ) AS expectations_total,
  SUM(IF(x.status != 'ERROR', x.turns, NULL)) AS turns,
  COUNTIF(x.status != 'ERROR' AND x.turns IS NOT NULL) AS results_with_turns
FROM {joined}
WHERE {where}
GROUP BY day ORDER BY day""",
        "daily_by_category": f"""
SELECT DATE(r.started_at) AS day, x.category, {_COUNTS}
FROM {joined}
WHERE {where}
GROUP BY day, x.category ORDER BY day, x.category""",
        "category_summary": f"""
SELECT x.category, {_COUNTS},
  APPROX_QUANTILES(x.duration_s, 100)[OFFSET(50)] AS p50_duration_s,
  APPROX_QUANTILES(x.duration_s, 100)[OFFSET(90)] AS p90_duration_s
FROM {joined}
WHERE {where}
GROUP BY x.category""",
        "runs": f"""
SELECT r.run_key, r.started_at, r.suite_name, r.gate_passed,
  r.eval_duration_s, r.git_sha, r.git_ref, {_COUNTS},
  SUM(x.duration_s) AS results_duration_s,
  ARRAY_AGG(DISTINCT x.category ORDER BY x.category) AS categories
FROM {joined}
WHERE {where}
GROUP BY r.run_key, r.started_at, r.suite_name, r.gate_passed,
  r.eval_duration_s, r.git_sha, r.git_ref
ORDER BY r.started_at""",
        "tags": f"""
WITH tagged AS (
  SELECT tag, DATE(r.started_at) AS day, x.test_id, x.status
  FROM {joined}
  CROSS JOIN UNNEST(x.tags) AS tag
  WHERE {where}
),
kept AS (
  SELECT tag FROM tagged
  GROUP BY tag HAVING COUNT(DISTINCT test_id) >= {int(min_tag_cases)}
)
SELECT tag, day, COUNT(*) AS executions,
  COUNTIF(status = 'PASS') AS passed,
  COUNTIF(status = 'FAIL') AS failed
FROM tagged JOIN kept USING (tag)
GROUP BY tag, day ORDER BY tag, day""",
        "tag_summary": f"""
SELECT COUNT(*) AS tags,
  COUNTIF(cases >= {int(min_tag_cases)}) AS reported
FROM (
  SELECT tag, COUNT(DISTINCT x.test_id) AS cases
  FROM {joined}
  CROSS JOIN UNNEST(x.tags) AS tag
  WHERE {where}
  GROUP BY tag
)""",
        "run_changes": f"""
WITH per_case_run AS ({per_case_run}
),
ranked AS (
  SELECT suite, category, run_key,
    ROW_NUMBER() OVER (
      PARTITION BY suite, category ORDER BY started_at DESC, run_key
    ) AS rn
  FROM (
    SELECT DISTINCT suite, category, run_key, started_at FROM per_case_run
  )
),
verdicts AS (
  SELECT c.suite, k.rn, c.category, c.test_id, c.run_key, c.has_pass,
    ROW_NUMBER() OVER (
      PARTITION BY c.suite, c.category, c.test_id ORDER BY k.rn
    ) AS vn
  FROM per_case_run AS c JOIN ranked AS k USING (suite, category, run_key)
  WHERE c.has_pass OR c.has_fail
),
changes AS (
  SELECT cur.suite, cur.category, cur.test_id, cur.run_key,
    IF(cur.has_pass, 'fixed', 'new_failure') AS change
  FROM verdicts AS cur
  JOIN verdicts AS prev USING (suite, category, test_id)
  WHERE cur.vn = 1 AND cur.rn = 1 AND prev.vn = 2
    AND cur.has_pass != prev.has_pass
),
case_tags AS (
  SELECT x.run_key, x.category, x.test_id, tag
  FROM {joined}
  CROSS JOIN UNNEST(x.tags) AS tag
  WHERE {where}
),
kept_tags AS (
  SELECT tag FROM case_tags
  GROUP BY tag HAVING COUNT(DISTINCT test_id) >= {int(min_tag_cases)}
),
change_tags AS (
  SELECT c.suite, c.category, c.test_id,
    ARRAY_AGG(DISTINCT t.tag ORDER BY t.tag) AS tags
  FROM changes AS c
  JOIN case_tags AS t USING (run_key, category, test_id)
  JOIN kept_tags USING (tag)
  GROUP BY c.suite, c.category, c.test_id
)
SELECT c.suite, c.category, c.test_id, c.change,
  IFNULL(t.tags, []) AS tags,
  COUNT(*) OVER (PARTITION BY c.change) AS total
FROM changes AS c
LEFT JOIN change_tags AS t USING (suite, category, test_id)
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY c.change ORDER BY c.suite, c.category, c.test_id
) <= {_MAX_CHANGES}
ORDER BY c.change, c.suite, c.category, c.test_id""",
        "longest_failing": f"""
WITH per_case_run AS ({per_case_run}
),
latest AS (
  SELECT suite, category, MAX(started_at) AS latest_at
  FROM per_case_run
  GROUP BY suite, category
),
last_pass AS (
  SELECT suite, category, test_id,
    MAX(IF(has_pass, started_at, NULL)) AS last_pass_at
  FROM per_case_run
  GROUP BY suite, category, test_id
)
SELECT c.suite, c.category, c.test_id,
  COUNTIF(c.has_fail) AS failing_runs,
  MIN(IF(c.has_fail, c.started_at, NULL)) AS failing_since,
  ANY_VALUE(p.last_pass_at) AS last_pass_at,
  COUNT(*) OVER () AS total
FROM per_case_run AS c
JOIN last_pass AS p USING (suite, category, test_id)
JOIN latest AS l USING (suite, category)
WHERE p.last_pass_at IS NULL OR c.started_at > p.last_pass_at
GROUP BY c.suite, c.category, c.test_id
HAVING failing_runs >= 2
  AND LOGICAL_OR(c.started_at = l.latest_at AND c.has_fail)
ORDER BY failing_runs DESC, failing_since, c.category, c.test_id
LIMIT {_MAX_LONGEST_FAILING}""",
        "failed_expectations": f"""
SELECT expectation, COUNT(*) AS failures,
  COUNT(DISTINCT x.test_id) AS cases
FROM {joined}
CROSS JOIN UNNEST(x.failed_expectations) AS expectation
WHERE {where}
  AND x.status = 'FAIL'
GROUP BY expectation
ORDER BY failures DESC, expectation
LIMIT {_MAX_FAILED_EXPECTATIONS}""",
        "flaky": f"""
SELECT IFNULL(r.suite_name, '') AS suite, x.category, x.test_id,
  r.git_sha,
  COUNTIF(x.status = 'PASS') AS passes,
  COUNTIF(x.status = 'FAIL') AS fails,
  COUNT(*) OVER () AS total
FROM {joined}
WHERE {where}
  AND r.git_sha IS NOT NULL AND r.git_sha != 'local'
GROUP BY suite, x.category, x.test_id, r.git_sha
HAVING passes > 0 AND fails > 0
ORDER BY fails DESC, passes DESC, x.test_id
LIMIT {_MAX_FLAKY}""",
    }


def fetch_trends(
    bq_dataset: str,
    days: int = 30,
    suite_name: str | None = None,
    git_ref: str | None = None,
    min_tag_cases: int = 5,
    client: bigquery.Client | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Runs :func:`trends_queries` and returns their rows by query name."""
    project, dataset = eval_bigquery.parse_dataset(bq_dataset)
    suite_name = _clean_filter(suite_name)
    git_ref = _clean_filter(git_ref)
    client = client or eval_bigquery.bigquery_client(project, _TRENDS_FEATURE)
    location = client.get_dataset(f"{project}.{dataset}").location
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("days", "INT64", int(days)),
            bigquery.ScalarQueryParameter("suite", "STRING", suite_name),
            bigquery.ScalarQueryParameter("ref", "STRING", git_ref),
        ],
        labels=eval_bigquery.job_labels(_TRENDS_FEATURE),
    )
    # Submit every query first so they run concurrently, then collect.
    jobs = {
        name: client.query(sql, job_config=job_config, location=location)
        for name, sql in trends_queries(project, dataset, min_tag_cases).items()
    }
    return {
        name: [
            dict(row.items())
            for row in job.result(timeout=eval_bigquery.JOB_TIMEOUT_S)
        ]
        for name, job in jobs.items()
    }


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _int(value: Any) -> int:
    """Whole number, 0 when missing: a BigQuery sum over no rows is NULL."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _pct(numerator: Any, denominator: Any) -> float | None:
    total = _int(denominator)
    return None if total == 0 else 100.0 * _int(numerator) / total


def _pass_rate(row: _Row | None) -> float | None:
    if row is None:
        return None
    passed = _int(row.get("passed"))
    return _pct(passed, passed + _int(row.get("failed")))


def _fmt_pct(value: float | None) -> str:
    return _NA if value is None else f"{value:.1f}%"


def _fmt_duration(seconds: Any) -> str:
    value = _float(seconds)
    if value is None:
        return _NA
    if value < 1:
        return f"{value * 1000:.0f} ms"
    if value < 60:
        return f"{value:.1f} s"
    if value < 3600:
        return f"{value / 60:.1f} min"
    return f"{value / 3600:.1f} h"


def _day_key(day: Any) -> str:
    """``YYYY-MM-DD`` of a date, a timestamp or their text."""
    return str(day)[:10]


def _day_label(day_key: str) -> str:
    return day_key[5:10] if len(day_key) >= 10 else day_key


def _fmt_ts(value: Any) -> str:
    """``MM-DD HH:MM`` of a timestamp or its text."""
    return _NA if value is None else str(value)[5:16].replace("T", " ")


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _days(count: int) -> str:
    return _plural(count, "day")


def _one_line(value: Any, max_len: int | None = None) -> str:
    """A value on one line, cut to ``max_len``, that can't end a table cell."""
    text = " ".join(str(value if value is not None else "").split())
    if max_len and len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text.replace("|", "\\|")


def _cell(value: Any, max_len: int | None = None) -> str:
    """Text for a Markdown table cell.

    ``<`` is escaped: it would start an HTML tag, which Markdown viewers hide.
    """
    return _one_line(value, max_len).replace("<", "&lt;")


def _code(value: Any, max_len: int | None = None) -> str:
    """An ID as a code span in a table cell, shown character for character."""
    return "`" + _one_line(value, max_len).replace("`", "'") + "`"


def _table(header: Sequence[str], rows: Iterable[Sequence[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(row) + " |" for row in rows]
    return [*out, ""]


def _sum_rows(rows: Iterable[_Row]) -> dict[str, int]:
    totals = dict.fromkeys(_COUNTERS, 0)
    for row in rows:
        for key in _COUNTERS:
            totals[key] += _int(row.get(key))
    return totals


def _rate_floor(rates: Sequence[float | None]) -> float:
    known = [r for r in rates if r is not None]
    if not known:
        return 0
    return math.floor(max(0.0, min(known) - 5) / 5) * 5


def _chart_number(value: float) -> str:
    """Formats a number as a plain decimal: Mermaid has no 1e+04 notation."""
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


def _mermaid_chart(
    title: str,
    labels: Sequence[str],
    y_label: str,
    series: Sequence[tuple[str, Sequence[float | None]]],
    y_min: float = 0,
    y_max: float | None = None,
) -> str:
    """Renders a Mermaid ``xychart-beta`` block (gaps are drawn as 0)."""
    values = [v for _, data in series for v in data if v is not None]
    top = y_max if y_max is not None else max([*values, 1]) * 1.1
    x_axis = ", ".join(json.dumps(label) for label in labels)
    lines = [
        "```mermaid",
        "xychart-beta",
        f"  title {json.dumps(title)}",
        f"  x-axis [{x_axis}]",
        f"  y-axis {json.dumps(y_label)} {_chart_number(y_min)} --> "
        f"{_chart_number(top)}",
    ]
    for kind, data in series:
        points = ", ".join("0" if v is None else _chart_number(v) for v in data)
        lines.append(f"  {kind} [{points}]")
    lines.append("```")
    return "\n".join(lines)


def _categories(rows: Iterable[_Row]) -> list[str]:
    """Categories of the rows: the known ones first, in their usual order."""
    found = {str(r.get("category")) for r in rows}
    known = [c for c in eval_bigquery.CATEGORIES if c in found]
    return known + sorted(found - set(known))


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------


def _summary_metrics(row: _Row) -> dict[str, str]:
    with_turns = _int(row.get("results_with_turns"))
    turns = _int(row.get("turns")) / with_turns if with_turns else None
    return {
        "Pass rate": _fmt_pct(_pass_rate(row)),
        "Executions": str(_int(row.get("executions"))),
        "Errors": str(_int(row.get("errors"))),
        "Goal completion": _fmt_pct(
            _pct(row.get("goals_met"), row.get("goals_total"))
        ),
        "Expectations met": _fmt_pct(
            _pct(row.get("expectations_met"), row.get("expectations_total"))
        ),
        "Turns per result (average)": _NA if turns is None else f"{turns:.1f}",
        "Runs": str(_int(row.get("runs"))),
    }


def _summary_section(
    daily: Sequence[_Row],
    category_summary: Sequence[_Row],
    by_category: Sequence[_Row],
    day_keys: Sequence[str],
    days: int,
) -> list[str]:
    last_label = f"Last day ({_day_label(day_keys[-1])})"
    columns = [(last_label, _summary_metrics(daily[-1]))]
    prev_label = None
    if len(daily) > 1:
        prev_label = f"Previous day ({_day_label(day_keys[-2])})"
        columns.append((prev_label, _summary_metrics(daily[-2])))
    columns.append((f"Last {_days(days)}", _summary_metrics(_sum_rows(daily))))
    rows = []
    for metric in columns[0][1]:
        cells = [values[metric] for _, values in columns]
        if any(cell != _NA for cell in cells):
            rows.append([metric, *cells])
    out = ["## Summary", ""]
    out += _table(["Metric", *[label for label, _ in columns]], rows)

    if not category_summary:
        return out
    per_day = {
        (_day_key(r["day"]), str(r.get("category"))): r for r in by_category
    }
    summary = {str(r.get("category")): r for r in category_summary}
    header = ["Category", "Executions", "Pass rate", last_label]
    if prev_label:
        header.append(prev_label)
    header += ["p50 duration", "p90 duration"]
    rows = []
    for category in _categories(category_summary):
        row = summary[category]
        cells = [
            category,
            str(_int(row.get("executions"))),
            _fmt_pct(_pass_rate(row)),
            _fmt_pct(_pass_rate(per_day.get((day_keys[-1], category)))),
        ]
        if prev_label:
            previous = per_day.get((day_keys[-2], category))
            cells.append(_fmt_pct(_pass_rate(previous)))
        cells += [
            _fmt_duration(row.get("p50_duration_s")),
            _fmt_duration(row.get("p90_duration_s")),
        ]
        rows.append(cells)
    out += ["### By category", ""]
    out += _table(header, rows)
    return out


def _pass_rate_chart(title: str, rows: Sequence[_Row]) -> list[str]:
    """Charts the pass rate of the days that have PASS or FAIL results.

    Returns nothing for fewer than two days: a line needs two points.
    """
    points = [(_day_key(r["day"]), _pass_rate(r)) for r in rows]
    points = [(day, rate) for day, rate in points if rate is not None]
    if len(points) < _MIN_LINE_POINTS:
        return []
    rates = [rate for _, rate in points]
    chart = _mermaid_chart(
        title,
        [_day_label(day) for day, _ in points],
        "Pass rate (%)",
        [("line", rates)],
        y_min=_rate_floor(rates),
        y_max=100,
    )
    return [chart, ""]


def _pass_rate_section(
    daily: Sequence[_Row], by_category: Sequence[_Row]
) -> list[str]:
    out = ["## Pass rate per day", ""]
    chart = _pass_rate_chart("Pass rate per day, all categories (%)", daily)
    if not chart:
        return [
            *out,
            "Needs at least two days with PASS or FAIL results in the period.",
            "",
        ]
    out += chart
    categories = _categories(by_category)
    if len(categories) < 2:
        return out
    for category in categories:
        rows = [r for r in by_category if str(r.get("category")) == category]
        title = f"{category.capitalize()} pass rate per day (%)"
        out += _pass_rate_chart(title, rows)
    return out


def _comparison_coverage(runs: Iterable[_Row]) -> tuple[bool, list[str]]:
    """Checks which suites/categories have at least two runs to compare.

    Returns ``(has_comparable, uncompared_categories)`` where
    ``uncompared_categories`` lists categories that appeared in the period
    but never had two runs in any suite. Falls back to per-suite counting
    when run rows don't carry ``categories``.
    """
    run_list = list(runs)
    per_pair: collections.Counter[tuple[str, str]] = collections.Counter()
    seen_categories: set[str] = set()
    for row in run_list:
        suite = str(row.get("suite_name") or "")
        cats = row.get("categories")
        if cats:
            for category in cats:
                cat = str(category)
                per_pair[(suite, cat)] += 1
                seen_categories.add(cat)
    if not seen_categories:
        per_suite = collections.Counter(
            str(r.get("suite_name") or "") for r in run_list
        )
        return any(count >= 2 for count in per_suite.values()), []
    comparable_categories = {
        cat for (_, cat), count in per_pair.items() if count >= 2
    }
    uncompared = seen_categories - comparable_categories
    known = [c for c in eval_bigquery.CATEGORIES if c in uncompared]
    ordered = known + sorted(uncompared - set(known))
    return bool(comparable_categories), ordered


def _uncompared_note(categories: Sequence[str]) -> str:
    if not categories:
        return ""
    joined = ", ".join(categories)
    return f" Not compared (only one run in the period): {joined}."


def _case_header(show_suite: bool) -> list[str]:
    return [*(["Suite"] if show_suite else []), "Category", "Test"]


def _case_rows(rows: Iterable[_Row], show_suite: bool) -> list[list[str]]:
    """The cells of :func:`_case_header` for rows of the case queries."""
    cells = []
    for row in rows:
        suite = [_cell(row.get("suite"))] if show_suite else []
        test = _code(row.get("test_id"), 120)
        cells.append([*suite, _cell(row.get("category")), test])
    return cells


def _run_changes_section(
    changes: Sequence[_Row],
    has_two_runs: bool,
    uncompared: Sequence[str],
    show_suite: bool,
) -> list[str]:
    out = ["## Changes since the previous run", ""]
    if not has_two_runs:
        return [*out, _NEEDS_TWO_RUNS, ""]
    out += [
        "For each suite and category, the latest run compared with each "
        "case's previous PASS or FAIL run (runs where a case only errored "
        f"are skipped).{_uncompared_note(uncompared)}",
        "",
    ]
    # Tags help see what a change is about; the column is left out when no
    # changed case has a reported tag.
    with_tags = any(r.get("tags") for r in changes)
    header = [*_case_header(show_suite), *(["Tags"] if with_tags else [])]
    for change, title in (("new_failure", "New failures"), ("fixed", "Fixed")):
        rows = [r for r in changes if r.get("change") == change]
        if not rows:
            out += [f"**{title}:** none.", ""]
            continue
        total = max(_int(r.get("total")) for r in rows)
        count = str(total)
        if total > len(rows):
            count += f", first {len(rows)} shown"
        cells = _case_rows(rows, show_suite)
        if with_tags:
            for row, case in zip(rows, cells, strict=True):
                case.append(_cell(", ".join(row.get("tags") or []), 80))
        out += [f"**{title} ({count})**", ""]
        out += _table(header, cells)
    return out


def _longest_failing_section(
    rows: Sequence[_Row],
    has_two_runs: bool,
    uncompared: Sequence[str],
    show_suite: bool,
) -> list[str]:
    out = ["## Longest-failing cases", ""]
    if not has_two_runs:
        return [*out, _NEEDS_TWO_RUNS, ""]
    if not rows:
        return [
            *out,
            "No case has failed in two or more runs since its last pass up to "
            f"the latest run.{_uncompared_note(uncompared)}",
            "",
        ]
    total = max(_int(r.get("total")) for r in rows)
    shown = (
        f" The first {len(rows)} of {total} are shown."
        if total > len(rows)
        else ""
    )
    out += [
        "Cases that fail in the latest run and haven't passed since, with at "
        "least two failing runs in the period (runs where a case only errored "
        f"are skipped).{_uncompared_note(uncompared)}{shown}",
        "",
    ]
    header = [*_case_header(show_suite), "Failing runs", "Failing since"]
    header.append("Last pass")
    cells = []
    for row, case in zip(rows, _case_rows(rows, show_suite), strict=True):
        last_pass = row.get("last_pass_at")
        cells.append(
            [
                *case,
                str(_int(row.get("failing_runs"))),
                _fmt_ts(row.get("failing_since")),
                "none in the period"
                if last_pass is None
                else _fmt_ts(last_pass),
            ]
        )
    return out + _table(header, cells)


def _tag_totals(per_day: Mapping[str, _Row], day_keys: Iterable[str]) -> _Row:
    return _sum_rows(per_day[k] for k in day_keys if k in per_day)


def _tag_sections(
    tag_rows: Sequence[_Row],
    tag_summary: Sequence[_Row],
    day_keys: Sequence[str],
    min_tag_cases: int,
) -> list[str]:
    by_tag: dict[str, dict[str, _Row]] = {}
    for row in tag_rows:
        by_tag.setdefault(str(row["tag"]), {})[_day_key(row["day"])] = row
    summary = tag_summary[0] if tag_summary else {}
    all_tags = _int(summary.get("tags"))
    not_reported = all_tags - _int(summary.get("reported"))
    limit = _plural(min_tag_cases, "case")
    if not by_tag:
        if not_reported <= 0:
            return []
        return [
            "## Pass rate per tag",
            "",
            f"No tag is reported: {_plural(all_tags, 'tag')} in the period, "
            f"none used by at least {limit}.",
            "",
        ]
    totals = []
    for tag, per_day in by_tag.items():
        total = _tag_totals(per_day, day_keys)
        rate = _pass_rate(total)
        # Tags without a pass rate (errors only) go last.
        totals.append((math.inf if rate is None else rate, tag, total))
    totals.sort(key=lambda t: (t[0], -t[2]["executions"], t[1]))
    shown_days = list(day_keys[-_TAG_DAYS:])
    rows = []
    for _, tag, total in totals[:_MAX_TAGS]:
        per_day = by_tag[tag]
        daily_rates = [
            _fmt_pct(_pass_rate(per_day[k])) if k in per_day else ""
            for k in shown_days
        ]
        rows.append(
            [
                _cell(tag, 60),
                str(total["executions"]),
                _fmt_pct(_pass_rate(total)),
                *daily_rates,
            ]
        )
    out = ["## Pass rate per tag", ""]
    if min_tag_cases > 1:
        count = (
            f" ({not_reported} of {_plural(all_tags, 'tag')})"
            if not_reported > 0
            else ""
        )
        out += [
            f"Tags used by fewer than {limit} are not reported{count}, so a "
            "tag that identifies a single case isn't shown as a group.",
            "",
        ]
    out += [
        "### Worst tags",
        "",
        f"Up to {_MAX_TAGS} tags, lowest pass rate first, with their pass "
        f"rate on the last {_days(len(shown_days))} with runs.",
        "",
    ]
    header = ["Tag", "Executions", "Pass rate"]
    out += _table([*header, *[_day_label(k) for k in shown_days]], rows)
    out += _tag_changes(by_tag, day_keys)
    return out


def _tag_changes(
    by_tag: Mapping[str, Mapping[str, _Row]], day_keys: Sequence[str]
) -> list[str]:
    out = ["### Biggest changes", ""]
    window = min(_TAG_DAYS, len(day_keys) // 2)
    if window < 1:
        return [*out, "Needs at least two days with runs in the period.", ""]
    recent = day_keys[-window:]
    before = day_keys[-2 * window : -window]
    changes = []
    for tag, per_day in by_tag.items():
        old, new = _tag_totals(per_day, before), _tag_totals(per_day, recent)
        old_n = old["passed"] + old["failed"]
        new_n = new["passed"] + new["failed"]
        if min(old_n, new_n) < _MIN_WINDOW_RESULTS:
            continue
        old_rate, new_rate = (
            _pct(old["passed"], old_n),
            _pct(new["passed"], new_n),
        )
        changes.append(
            (new_rate - old_rate, tag, old_rate, new_rate, old_n, new_n)
        )

    def _span(keys: Sequence[str]) -> str:
        first, last = _day_label(keys[0]), _day_label(keys[-1])
        return first if first == last else f"{first} to {last}"

    out += [
        f"Pass rate over the last {_days(window)} with runs "
        f"({_span(recent)}), compared with the {_days(window)} before "
        f"({_span(before)}). Tags need at least {_MIN_WINDOW_RESULTS} PASS "
        "or FAIL results in both.",
        "",
    ]
    drops = sorted(c for c in changes if c[0] < 0)[:_MAX_TAG_CHANGES]
    gains = sorted((c for c in changes if c[0] > 0), reverse=True)
    gains = gains[:_MAX_TAG_CHANGES]
    header = ["Tag", "Before", "After", "Change", "Results (before / after)"]
    for title, rows in (("Largest drops", drops), ("Largest gains", gains)):
        if not rows:
            out += [f"**{title}:** none.", ""]
            continue
        out += [f"**{title}**", ""]
        out += _table(
            header,
            [
                [
                    _cell(tag, 60),
                    _fmt_pct(old_rate),
                    _fmt_pct(new_rate),
                    f"{delta:+.1f} pts",
                    f"{old_n} / {new_n}",
                ]
                for delta, tag, old_rate, new_rate, old_n, new_n in rows
            ],
        )
    return out


def _failed_expectations_section(rows: Sequence[_Row]) -> list[str]:
    if not rows:
        return []
    out = [
        "## Most frequent failed expectations",
        "",
        "Expectations that were not met most often in the period.",
        "",
    ]
    return out + _table(
        ["Failures", "Cases", "Expectation"],
        [
            [
                str(_int(row.get("failures"))),
                str(_int(row.get("cases"))),
                _cell(row.get("expectation"), 160),
            ]
            for row in rows
        ],
    )


def _run_time_chart(runs: Sequence[_Row], day_keys: Sequence[str]) -> list[str]:
    """Charts the time to run the evals per day, with what it measures."""
    timed = [r for r in runs if _float(r.get("eval_duration_s")) is not None]
    if timed:
        field = "eval_duration_s"
        title = "Time to run the evals per day (minutes)"
        note = "Wall-clock time of the eval runs, summed per day."
        if len(timed) < len(runs):
            note += (
                f" {len(runs) - len(timed)} of {_plural(len(runs), 'run')} "
                "had no recorded run time and aren't counted."
            )
    else:
        field = "results_duration_s"
        title = "Total duration of all results per day (minutes)"
        note = (
            "The run time wasn't recorded for these runs, so this chart sums "
            "the durations of all results. Results run in parallel, so the "
            "actual run time is shorter."
        )
    per_day = dict.fromkeys(day_keys, 0.0)
    for run in runs:
        key = _day_key(run.get("started_at"))
        if key in per_day:
            per_day[key] += _float(run.get(field)) or 0.0
    minutes = [per_day[k] / 60 for k in day_keys]
    if not any(minutes):
        return []
    labels = [_day_label(k) for k in day_keys]
    chart = _mermaid_chart(title, labels, "Minutes", [("bar", minutes)])
    return [note, "", chart, ""]


def _volume_and_time_section(
    daily: Sequence[_Row],
    runs: Sequence[_Row],
    day_keys: Sequence[str],
    show_suite: bool,
) -> list[str]:
    labels = [_day_label(k) for k in day_keys]
    executions = [float(_int(r.get("executions"))) for r in daily]
    out = [
        "## Executions per day",
        "",
        _mermaid_chart(
            "Executions per day", labels, "Executions", [("bar", executions)]
        ),
        "",
        "## Time to run the evals",
        "",
    ]
    out += _run_time_chart(runs, day_keys)
    if not runs:
        return out
    header = ["Run (UTC)", *(["Suite"] if show_suite else []), "Pass rate"]
    header += ["Executions", "Errors", "Gate", "Run time", "Results time"]
    header += ["Ref", "Commit"]
    rows = []
    for run in runs[-_MAX_RUNS:][::-1]:
        gate = run.get("gate_passed")
        rows.append(
            [
                _fmt_ts(run.get("started_at")),
                *([_cell(run.get("suite_name"))] if show_suite else []),
                _fmt_pct(_pass_rate(run)),
                str(_int(run.get("executions"))),
                str(_int(run.get("errors"))),
                _NA if gate is None else ("passed" if gate else "failed"),
                _fmt_duration(run.get("eval_duration_s")),
                _fmt_duration(run.get("results_duration_s")),
                _cell(run.get("git_ref") or _NA, 40),
                _code(str(run.get("git_sha") or _NA)[:12]),
            ]
        )
    out += [
        f"### Last {_plural(len(rows), 'run')}",
        "",
        "Run time is the wall-clock time of the run, when recorded. Results "
        "time is the sum of the durations of all its results.",
        "",
    ]
    return out + _table(header, rows)


def _flaky_section(rows: Sequence[_Row], show_suite: bool) -> list[str]:
    out = ["## Flaky cases", ""]
    intro = (
        "Cases with both PASS and FAIL on the same commit. Runs without a "
        "commit (local runs) are ignored."
    )
    if not rows:
        return [*out, intro, "", "None in this period.", ""]
    total = max(_int(r.get("total")) for r in rows)
    shown = (
        f" The first {len(rows)} of {total} are shown."
        if total > len(rows)
        else ""
    )
    out += [f"{intro}{shown}", ""]
    header = [*_case_header(show_suite), "Commit", "Passes", "Fails"]
    cells = [
        [
            *case,
            _code(str(row.get("git_sha") or "")[:12]),
            str(_int(row.get("passes"))),
            str(_int(row.get("fails"))),
        ]
        for row, case in zip(rows, _case_rows(rows, show_suite), strict=True)
    ]
    return out + _table(header, cells)


def render_trends_markdown(
    trends: Mapping[str, Sequence[_Row]],
    days: int,
    bq_dataset: str,
    suite_name: str | None = None,
    git_ref: str | None = None,
    min_tag_cases: int = 5,
) -> str:
    """Renders the trends report from :func:`fetch_trends` output.

    ``suite_name``, ``git_ref`` and ``min_tag_cases`` only describe the
    filters that :func:`fetch_trends` applied.
    """

    def rows(name: str) -> list[_Row]:
        return list(trends.get(name) or [])

    suite_name = _clean_filter(suite_name)
    git_ref = _clean_filter(git_ref)
    daily = rows("daily")
    scope = f"suite `{suite_name}`" if suite_name else "all suites"
    if git_ref:
        scope += f", git ref `{git_ref}`"
    out = [
        "# Eval trends",
        "",
        f"Last {_days(days)}, {scope}, from `{bq_dataset}`. Pass rate = "
        "PASS / (PASS + FAIL). Errors are excluded from pass rate, goals, "
        "expectations and turns. Days are in UTC.",
        "",
    ]
    if not daily:
        out.append("No eval results in this period.")
        return "\n".join(out) + "\n"

    day_keys = [_day_key(r["day"]) for r in daily]
    runs = rows("runs")
    by_category = rows("daily_by_category")
    show_suite = len({str(r.get("suite_name") or "") for r in runs}) > 1
    has_two_runs, uncompared = _comparison_coverage(runs)
    out += _summary_section(
        daily, rows("category_summary"), by_category, day_keys, days
    )
    out += _pass_rate_section(daily, by_category)
    out += _run_changes_section(
        rows("run_changes"), has_two_runs, uncompared, show_suite
    )
    out += _longest_failing_section(
        rows("longest_failing"), has_two_runs, uncompared, show_suite
    )
    out += _tag_sections(
        rows("tags"), rows("tag_summary"), day_keys, min_tag_cases
    )
    out += _failed_expectations_section(rows("failed_expectations"))
    out += _volume_and_time_section(daily, runs, day_keys, show_suite)
    out += _flaky_section(rows("flaky"), show_suite)
    return "\n".join(out).rstrip("\n") + "\n"


def write_eval_trends_markdown(
    bq_dataset: str,
    output_path: str,
    days: int = 30,
    suite_name: str | None = None,
    git_ref: str | None = None,
    min_tag_cases: int = 5,
    client: bigquery.Client | None = None,
) -> str:
    """Queries the tables and writes the Markdown trends report."""
    suite_name = _clean_filter(suite_name)
    git_ref = _clean_filter(git_ref)
    trends = fetch_trends(
        bq_dataset,
        days=days,
        suite_name=suite_name,
        git_ref=git_ref,
        min_tag_cases=min_tag_cases,
        client=client,
    )
    markdown = render_trends_markdown(
        trends,
        days=days,
        bq_dataset=bq_dataset,
        suite_name=suite_name,
        git_ref=git_ref,
        min_tag_cases=min_tag_cases,
    )
    directory = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(directory, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(markdown)
    return output_path
