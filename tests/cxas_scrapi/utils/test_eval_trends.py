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

"""Tests for the Markdown trends report of eval results."""

import datetime
import pathlib
from typing import Any
from unittest.mock import MagicMock, create_autospec, patch

import pytest

from cxas_scrapi.utils import eval_trends
from cxas_scrapi.utils.eval_trends import (
    fetch_trends,
    render_trends_markdown,
    trends_queries,
    write_eval_trends_markdown,
)

_UTC = datetime.timezone.utc
_DAYS = ("2026-11-09", "2026-11-10", "2026-11-12", "2026-11-16")
_QUERY_NAMES = {
    "daily",
    "daily_by_category",
    "category_summary",
    "runs",
    "tags",
    "tag_summary",
    "run_changes",
    "longest_failing",
    "failed_expectations",
    "flaky",
}


def _at(day: str, hour: int = 22) -> datetime.datetime:
    return datetime.datetime.fromisoformat(day).replace(hour=hour, tzinfo=_UTC)


def _daily(day: str, runs: int, passed: int, failed: int, **extra: int) -> dict:
    errors = extra.pop("errors", 0)
    return {
        "day": datetime.date.fromisoformat(day),
        "runs": runs,
        "executions": passed + failed + errors,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        **extra,
    }


def _run(day: str, hour: int, passed: int, failed: int, **extra: Any) -> dict:
    values = {
        "run_key": f"{day}-{hour}",
        "started_at": _at(day, hour),
        "suite_name": "nightly",
        "gate_passed": True,
        "eval_duration_s": 1800.0,
        "git_sha": "abcdef1234567890",
        "git_ref": "main",
        "executions": passed + failed,
        "passed": passed,
        "failed": failed,
        "errors": 0,
        "results_duration_s": 9000.0,
        "categories": ["callback", "simulation"],
    }
    values.update(extra)
    return values


def _tag(tag: str, day: str, passed: int, failed: int) -> dict:
    return {
        "tag": tag,
        "day": day,
        "executions": passed + failed,
        "passed": passed,
        "failed": failed,
    }


def _category(category: str, passed: int, failed: int, **extra: Any) -> dict:
    errors = extra.pop("errors", 0)
    values = {
        "category": category,
        "executions": passed + failed + errors,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "p50_duration_s": None,
        "p90_duration_s": None,
    }
    values.update(extra)
    return values


def _trends() -> dict[str, list[dict[str, Any]]]:
    """Four days with runs, the last one with two runs."""
    return {
        "daily": [
            _daily(_DAYS[0], 1, 80, 20, goals_met=150, goals_total=200),
            _daily(_DAYS[1], 1, 82, 18),
            _daily(_DAYS[2], 1, 85, 15, goals_met=160, goals_total=200),
            _daily(
                _DAYS[3],
                2,
                171,
                19,
                errors=10,
                goals_met=310,
                goals_total=400,
                expectations_met=200,
                expectations_total=250,
                turns=1300,
                results_with_turns=130,
            ),
        ],
        "daily_by_category": [
            {**_daily(_DAYS[2], 1, 70, 10), "category": "simulation"},
            {**_daily(_DAYS[2], 1, 15, 5), "category": "callback"},
            {
                **_daily(_DAYS[3], 2, 150, 10, errors=10),
                "category": "simulation",
            },
            {**_daily(_DAYS[3], 2, 21, 9), "category": "callback"},
        ],
        "category_summary": [
            _category("callback", 88, 12, p50_duration_s=0.004),
            _category(
                "simulation",
                330,
                60,
                errors=10,
                p50_duration_s=32.0,
                p90_duration_s=51.5,
            ),
        ],
        "runs": [
            _run(_DAYS[0], 22, 80, 20),
            _run(_DAYS[1], 22, 82, 18),
            _run(_DAYS[2], 22, 85, 15, gate_passed=False),
            _run(_DAYS[3], 10, 81, 9, eval_duration_s=1200.0),
            _run(_DAYS[3], 22, 90, 10),
        ],
        "tags": [
            _tag("payments", _DAYS[0], 10, 0),
            _tag("payments", _DAYS[1], 9, 1),
            _tag("payments", _DAYS[2], 5, 5),
            _tag("payments", _DAYS[3], 4, 6),
            _tag("billing", _DAYS[0], 5, 5),
            _tag("billing", _DAYS[1], 5, 5),
            _tag("billing", _DAYS[2], 9, 1),
            _tag("billing", _DAYS[3], 10, 0),
            *[_tag("smoke", day, 20, 0) for day in _DAYS],
        ],
        "tag_summary": [{"tags": 3, "reported": 3}],
        "run_changes": [
            {
                "suite": "nightly",
                "category": "callback",
                "test_id": "billing/before_model_callbacks/test_x",
                "change": "fixed",
                "tags": [],
                "total": 1,
            },
            {
                "suite": "nightly",
                "category": "simulation",
                "test_id": "pay_1",
                "change": "new_failure",
                "tags": ["payments", "P0"],
                "total": 25,
            },
        ],
        "longest_failing": [
            {
                "suite": "nightly",
                "category": "callback",
                "test_id": "upgrades/before_model_callbacks/test_escalate",
                "failing_runs": 5,
                "failing_since": _at(_DAYS[0]),
                "last_pass_at": None,
                "total": 2,
            },
            {
                "suite": "nightly",
                "category": "simulation",
                "test_id": "pay_2",
                "failing_runs": 3,
                "failing_since": _at(_DAYS[2]),
                "last_pass_at": _at(_DAYS[1]),
                "total": 2,
            },
        ],
        "failed_expectations": [
            {
                "expectation": "Agent confirms | the refund\namount",
                "failures": 12,
                "cases": 3,
            }
        ],
        "flaky": [
            {
                "suite": "nightly",
                "category": "simulation",
                "test_id": "pay_1",
                "git_sha": "abcdef1234567890",
                "passes": 1,
                "fails": 2,
                "total": 1,
            }
        ],
    }


def _render(trends: dict[str, list[dict[str, Any]]], **kwargs: Any) -> str:
    return render_trends_markdown(trends, days=30, bq_dataset="p.d", **kwargs)


def _section(md: str, heading: str) -> str:
    """Returns a section: its heading up to the next one of its level."""
    lines = md.splitlines()
    start = lines.index(heading)
    depth = len(heading.split(" ", maxsplit=1)[0])
    end = len(lines)
    for i in range(start + 1, len(lines)):
        marks = lines[i].split(" ", maxsplit=1)[0]
        if marks and set(marks) == {"#"} and len(marks) <= depth:
            end = i
            break
    return "\n".join(lines[start:end])


# ---------------------------------------------------------------------------
# Header and summary
# ---------------------------------------------------------------------------


def test_header_describes_the_period_and_the_filters() -> None:
    md = _render(_trends(), suite_name="nightly", git_ref="main")
    assert md.startswith(
        "# Eval trends\n\n"
        "Last 30 days, suite `nightly`, git ref `main`, from `p.d`. "
        "Pass rate = PASS / (PASS + FAIL). Errors are excluded from pass "
        "rate, goals, expectations and turns. Days are in UTC.\n"
    )
    assert "Last 30 days, all suites, from `p.d`." in _render(_trends())
    assert "Last 30 days, all suites, from `p.d`." in _render(
        _trends(), suite_name="", git_ref="  "
    )


def test_summary_compares_last_day_previous_day_and_period() -> None:
    summary = _section(_render(_trends()), "## Summary")
    assert (
        "| Metric | Last day (11-16) | Previous day (11-12) | Last 30 days |"
    ) in summary
    # Errors are excluded: 171 / (171 + 19); period: 418 / (418 + 72).
    assert "| Pass rate | 90.0% | 85.0% | 85.3% |" in summary
    assert "| Executions | 200 | 100 | 500 |" in summary
    assert "| Errors | 10 | 0 | 10 |" in summary
    assert "| Goal completion | 77.5% | 80.0% | 77.5% |" in summary
    assert "| Expectations met | 80.0% | n/a | 80.0% |" in summary
    assert "| Turns per result (average) | 10.0 | n/a | 10.0 |" in summary
    assert "| Runs | 2 | 1 | 5 |" in summary


def test_summary_leaves_out_metrics_that_were_never_recorded() -> None:
    trends = _trends()
    trends["daily"] = [
        _daily(_DAYS[2], 1, 85, 15),
        _daily(_DAYS[3], 1, 90, 10),
    ]
    summary = _section(_render(trends), "## Summary")
    assert "| Pass rate | 90.0% | 85.0% | 87.5% |" in summary
    assert "Goal completion" not in summary
    assert "Expectations met" not in summary
    assert "Turns per result" not in summary


def test_summary_per_category() -> None:
    table = _section(_render(_trends()), "### By category")
    assert (
        "| Category | Executions | Pass rate | Last day (11-16) | "
        "Previous day (11-12) | p50 duration | p90 duration |"
    ) in table
    assert (
        "| simulation | 400 | 84.6% | 93.8% | 87.5% | 32.0 s | 51.5 s |"
    ) in table
    assert "| callback | 100 | 88.0% | 70.0% | 75.0% | 4 ms | n/a |" in table


def test_known_categories_come_first_in_a_fixed_order() -> None:
    trends = _trends()
    trends["category_summary"] = [
        _category("zeta", 1, 0),
        _category("callback", 1, 0),
        _category("alpha", 1, 0),
        _category("tool", 1, 0),
        _category("simulation", 1, 0),
        _category("golden", 1, 0),
    ]
    table = _section(_render(trends), "### By category")
    names = [
        line.split(" | ")[0].removeprefix("| ")
        for line in table.splitlines()[4:]
        if line
    ]
    assert names == [
        "simulation",
        "golden",
        "tool",
        "callback",
        "alpha",
        "zeta",
    ]


# ---------------------------------------------------------------------------
# Pass rate per day
# ---------------------------------------------------------------------------


def test_pass_rate_charts_overall_and_per_category() -> None:
    charts = _section(_render(_trends()), "## Pass rate per day")
    assert 'title "Pass rate per day, all categories (%)"' in charts
    assert 'x-axis ["11-09", "11-10", "11-12", "11-16"]' in charts
    # The axis starts a little under the lowest rate.
    assert 'y-axis "Pass rate (%)" 75 --> 100' in charts
    assert "line [80, 82, 85, 90]" in charts
    # A category chart has the days on which the category ran.
    assert 'title "Simulation pass rate per day (%)"' in charts
    assert 'title "Callback pass rate per day (%)"' in charts
    assert charts.count('x-axis ["11-12", "11-16"]') == 2
    assert "line [87.5, 93.8]" in charts
    assert "line [75, 70]" in charts


def test_no_chart_per_category_for_a_single_category() -> None:
    trends = _trends()
    trends["daily_by_category"] = [
        row
        for row in trends["daily_by_category"]
        if row["category"] == "simulation"
    ]
    charts = _section(_render(trends), "## Pass rate per day")
    assert charts.count("```mermaid") == 1
    assert "Simulation pass rate" not in charts


def test_no_chart_for_a_category_that_ran_on_a_single_day() -> None:
    trends = _trends()
    trends["daily_by_category"].append(
        {**_daily(_DAYS[3], 1, 0, 5), "category": "golden"}
    )
    charts = _section(_render(trends), "## Pass rate per day")
    assert "Simulation pass rate" in charts
    # A line needs two points; the summary table has the golden pass rate.
    assert "Golden pass rate" not in charts


def test_pass_rate_chart_skips_days_with_only_errors() -> None:
    trends = _trends()
    trends["daily"][1] = _daily(_DAYS[1], 1, 0, 0, errors=5)
    md = _render(trends)
    charts = _section(md, "## Pass rate per day")
    assert 'x-axis ["11-09", "11-12", "11-16"]' in charts
    assert "line [80, 85, 90]" in charts
    # The day still counts in the volume chart.
    assert "bar [100, 5, 100, 200]" in _section(md, "## Executions per day")


# ---------------------------------------------------------------------------
# Changes since the previous run, longest-failing cases
# ---------------------------------------------------------------------------


def test_changes_since_the_previous_run() -> None:
    changes = _section(_render(_trends()), "## Changes since the previous run")
    assert (
        "For each suite and category, the latest run compared with each "
        "case's previous PASS or FAIL run (runs where a case only errored "
        "are skipped)."
    ) in changes
    assert "**New failures (25, first 1 shown)**" in changes
    # One suite only: no suite column. The tags show what a change is about.
    assert "| Category | Test | Tags |" in changes
    assert "Suite" not in changes
    assert "| simulation | `pay_1` | payments, P0 |" in changes
    assert "**Fixed (1)**" in changes
    assert (
        "| callback | `billing/before_model_callbacks/test_x` |  |" in changes
    )


def test_changes_have_no_tags_column_when_no_change_has_tags() -> None:
    trends = _trends()
    for row in trends["run_changes"]:
        row["tags"] = []
    changes = _section(_render(trends), "## Changes since the previous run")
    assert "| Category | Test |\n" in changes + "\n"
    assert "Tags" not in changes


def test_no_changes_since_the_previous_run() -> None:
    trends = _trends()
    trends["run_changes"] = []
    changes = _section(_render(trends), "## Changes since the previous run")
    assert "**New failures:** none." in changes
    assert "**Fixed:** none." in changes


def test_longest_failing_cases() -> None:
    section = _section(_render(_trends()), "## Longest-failing cases")
    assert (
        "| Category | Test | Failing runs | Failing since | Last pass |"
    ) in section
    assert (
        "| callback | `upgrades/before_model_callbacks/test_escalate` | 5 | "
        "11-09 22:00 | none in the period |"
    ) in section
    assert "| simulation | `pay_2` | 3 | 11-12 22:00 | 11-10 22:00 |" in section
    assert "are shown" not in section


def test_longest_failing_cases_says_when_the_list_is_cut() -> None:
    trends = _trends()
    for row in trends["longest_failing"]:
        row["total"] = 40
    section = _section(_render(trends), "## Longest-failing cases")
    assert "are skipped). The first 2 of 40 are shown." in section


def test_no_longest_failing_cases() -> None:
    trends = _trends()
    trends["longest_failing"] = []
    section = _section(_render(trends), "## Longest-failing cases")
    assert (
        "No case has failed in two or more runs since its last pass" in section
    )
    assert "| Category |" not in section


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


def test_worst_tags_lists_the_lowest_pass_rate_first() -> None:
    trends = _trends()
    trends["tags"].append(_tag("refunds", _DAYS[3], 3, 1))
    section = _section(_render(trends, min_tag_cases=3), "## Pass rate per tag")
    # Every tag found is reported here, so the note has no count.
    assert "Tags used by fewer than 3 cases are not reported, so" in section
    worst = _section(section, "### Worst tags")
    assert "Up to 15 tags, lowest pass rate first" in worst
    assert "on the last 4 days with runs." in worst
    assert (
        "| Tag | Executions | Pass rate | 11-09 | 11-10 | 11-12 | 11-16 |"
    ) in worst
    rows = [line for line in worst.splitlines() if line.startswith("| ")][1:]
    assert rows == [
        "| payments | 40 | 70.0% | 100.0% | 90.0% | 50.0% | 40.0% |",
        "| billing | 40 | 72.5% | 50.0% | 50.0% | 90.0% | 100.0% |",
        # Blank on the days the tag has no result.
        "| refunds | 4 | 75.0% |  |  |  | 75.0% |",
        "| smoke | 80 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |",
    ]


def test_worst_tags_lists_a_limited_number_of_tags() -> None:
    with patch.object(eval_trends, "_MAX_TAGS", 1):
        worst = _section(_render(_trends()), "### Worst tags")
    assert "| payments |" in worst
    assert "| billing |" not in worst
    assert "| smoke |" not in worst


def test_tag_section_says_how_many_tags_are_not_reported() -> None:
    trends = _trends()
    trends["tag_summary"] = [{"tags": 40, "reported": 3}]
    section = _section(_render(trends), "## Pass rate per tag")
    assert (
        "Tags used by fewer than 5 cases are not reported (37 of 40 tags), "
        "so a tag that identifies a single case isn't shown as a group."
    ) in section


def test_tag_section_has_no_limit_note_when_one_case_is_enough() -> None:
    section = _section(
        _render(_trends(), min_tag_cases=1), "## Pass rate per tag"
    )
    assert "not reported" not in section
    assert "### Worst tags" in section


def test_tag_section_says_when_no_tag_has_enough_cases() -> None:
    trends = _trends()
    trends["tags"] = []
    trends["tag_summary"] = [{"tags": 12, "reported": 0}]
    section = _section(_render(trends), "## Pass rate per tag")
    assert (
        "No tag is reported: 12 tags in the period, none used by at least "
        "5 cases."
    ) in section
    assert "### Worst tags" not in section
    assert "### Biggest changes" not in section


def test_no_tag_section_without_tags() -> None:
    trends = _trends()
    trends["tags"] = []
    trends["tag_summary"] = [{"tags": 0, "reported": 0}]
    md = _render(trends)
    assert "## Pass rate per tag" not in md
    assert "### Biggest changes" not in md


def test_tag_changes_compare_the_two_halves_of_the_period() -> None:
    changes = _section(_render(_trends()), "### Biggest changes")
    assert (
        "Pass rate over the last 2 days with runs (11-12 to 11-16), compared "
        "with the 2 days before (11-09 to 11-10). Tags need at least 10 PASS "
        "or FAIL results in both."
    ) in changes
    drops, gains = changes.split("**Largest gains**")
    assert "| payments | 95.0% | 45.0% | -50.0 pts | 20 / 20 |" in drops
    assert "billing" not in drops
    assert "| billing | 50.0% | 95.0% | +45.0 pts | 20 / 20 |" in gains
    assert "payments" not in gains
    # A steady tag is neither a drop nor a gain.
    assert "smoke" not in changes


def test_tag_changes_need_enough_results_in_both_windows() -> None:
    trends = _trends()
    trends["tags"] += [
        _tag("rare", _DAYS[0], 9, 0),
        _tag("rare", _DAYS[2], 5, 5),
        _tag("rare", _DAYS[3], 5, 5),
    ]
    md = _render(trends)
    assert "| rare |" in _section(md, "### Worst tags")
    assert "rare" not in _section(md, "### Biggest changes")


def test_tag_changes_without_any_change() -> None:
    trends = _trends()
    trends["tags"] = [t for t in trends["tags"] if t["tag"] == "smoke"]
    changes = _section(_render(trends), "### Biggest changes")
    assert "**Largest drops:** none." in changes
    assert "**Largest gains:** none." in changes


# ---------------------------------------------------------------------------
# Failed expectations
# ---------------------------------------------------------------------------


def test_failed_expectations_are_safe_table_cells() -> None:
    trends = _trends()
    trends["failed_expectations"] += [
        {"expectation": "Agent says <name>", "failures": 2},
        {"expectation": "word " * 60, "failures": 1},
    ]
    section = _section(_render(trends), "## Most frequent failed expectations")
    rows = [line for line in section.splitlines() if line.startswith("| ")]
    assert rows[0] == "| Failures | Cases | Expectation |"
    # One line, and "|" can't end the cell.
    assert rows[1] == "| 12 | 3 | Agent confirms \\| the refund amount |"
    # "<" would start an HTML tag, which Markdown viewers hide.
    assert rows[2] == "| 2 | 0 | Agent says &lt;name> |"
    # Long texts are cut to 160 characters.
    text = rows[3].split(" | ")[-1].removesuffix(" |")
    assert len(text) == 160
    assert text.endswith("word…")


def test_ids_are_shown_character_for_character() -> None:
    # A code span shows "<" as it is, and would end at a backtick.
    trends = _trends()
    trends["run_changes"][1]["test_id"] = "refund <5 min> `fast`"
    changes = _section(_render(trends), "## Changes since the previous run")
    assert "| simulation | `refund <5 min> 'fast'` |" in changes


def test_no_failed_expectations_section_without_rows() -> None:
    trends = _trends()
    trends["failed_expectations"] = []
    assert "Most frequent failed expectations" not in _render(trends)


# ---------------------------------------------------------------------------
# Volume, run time and runs
# ---------------------------------------------------------------------------


def test_charts_use_plain_decimals_for_large_numbers() -> None:
    trends = _trends()
    trends["daily"][-1]["executions"] = 1234567
    md = _render(trends)
    assert "bar [100, 100, 100, 1234567]" in md
    assert 'y-axis "Executions" 0 --> 1358023.7' in md
    # Mermaid can't parse scientific notation such as 1.358e+06.
    assert "e+" not in md


def test_run_time_chart_uses_the_wall_clock_time_of_the_runs() -> None:
    section = _section(_render(_trends()), "## Time to run the evals")
    assert "Wall-clock time of the eval runs, summed per day." in section
    assert "had no recorded run time" not in section
    assert 'title "Time to run the evals per day (minutes)"' in section
    # 11-16 has two runs: (1200 s + 1800 s) / 60.
    assert "bar [30, 30, 30, 50]" in section


def test_run_time_chart_says_how_many_runs_have_no_run_time() -> None:
    trends = _trends()
    trends["runs"][0]["eval_duration_s"] = None
    trends["runs"][3]["eval_duration_s"] = None
    section = _section(_render(trends), "## Time to run the evals")
    assert "2 of 5 runs had no recorded run time and aren't counted." in section
    assert 'title "Time to run the evals per day (minutes)"' in section
    assert "bar [0, 30, 30, 30]" in section


def test_run_time_chart_falls_back_to_the_sum_of_result_durations() -> None:
    trends = _trends()
    for run in trends["runs"]:
        run["eval_duration_s"] = None
    section = _section(_render(trends), "## Time to run the evals")
    assert "The run time wasn't recorded for these runs" in section
    assert 'title "Total duration of all results per day (minutes)"' in section
    assert "bar [150, 150, 150, 300]" in section


def test_no_run_time_chart_without_any_duration() -> None:
    trends = _trends()
    for run in trends["runs"]:
        run["eval_duration_s"] = None
        run["results_duration_s"] = None
    section = _section(_render(trends), "## Time to run the evals")
    assert "mermaid" not in section
    assert "The run time wasn't recorded" not in section
    assert "### Last 5 runs" in section


def test_last_runs_table_lists_the_newest_run_first() -> None:
    runs = _section(_render(_trends()), "### Last 5 runs")
    assert (
        "| Run (UTC) | Pass rate | Executions | Errors | Gate | Run time | "
        "Results time | Ref | Commit |"
    ) in runs
    assert runs.index("| 11-16 22:00 |") < runs.index("| 11-16 10:00 |")
    assert runs.index("| 11-16 10:00 |") < runs.index("| 11-12 22:00 |")
    assert (
        "| 11-16 10:00 | 90.0% | 90 | 0 | passed | 20.0 min | 2.5 h | main | "
        "`abcdef123456` |"
    ) in runs
    assert (
        "| 11-12 22:00 | 85.0% | 100 | 0 | failed | 30.0 min | 2.5 h | main | "
        "`abcdef123456` |"
    ) in runs


def test_last_runs_table_marks_values_that_were_not_recorded() -> None:
    trends = _trends()
    trends["runs"][-1].update(
        gate_passed=None, eval_duration_s=None, git_ref=None, git_sha=None
    )
    runs = _section(_render(trends), "### Last 5 runs")
    assert (
        "| 11-16 22:00 | 90.0% | 100 | 0 | n/a | n/a | 2.5 h | n/a | `n/a` |"
    ) in runs


def test_last_runs_table_shows_ten_runs_at_most() -> None:
    trends = _trends()
    trends["runs"] = [_run(_DAYS[3], hour, 9, 1) for hour in range(12)]
    runs = _section(_render(trends), "### Last 10 runs")
    assert "| 11-16 11:00 |" in runs
    assert "| 11-16 02:00 |" in runs
    assert "| 11-16 01:00 |" not in runs


# ---------------------------------------------------------------------------
# Flaky cases
# ---------------------------------------------------------------------------


def test_flaky_cases() -> None:
    flaky = _section(_render(_trends()), "## Flaky cases")
    assert "Cases with both PASS and FAIL on the same commit." in flaky
    assert "are shown" not in flaky
    assert "| Category | Test | Commit | Passes | Fails |" in flaky
    assert "| simulation | `pay_1` | `abcdef123456` | 1 | 2 |" in flaky


def test_flaky_cases_says_when_the_list_is_cut() -> None:
    trends = _trends()
    trends["flaky"][0]["total"] = 14
    flaky = _section(_render(trends), "## Flaky cases")
    assert "The first 1 of 14 are shown." in flaky


def test_flaky_section_keeps_local_runs_note_when_empty() -> None:
    trends = _trends()
    trends["flaky"] = []
    flaky = _section(_render(trends), "## Flaky cases")
    assert "Runs without a commit (local runs) are ignored." in flaky
    assert "None in this period." in flaky


# ---------------------------------------------------------------------------
# Whole report
# ---------------------------------------------------------------------------


def test_report_has_one_chart_per_series() -> None:
    md = _render(_trends())
    # Pass rate (all, simulation, callback), executions and run time.
    assert md.count("```mermaid") == 5
    executions = _section(md, "## Executions per day")
    assert 'x-axis ["11-09", "11-10", "11-12", "11-16"]' in executions
    assert "bar [100, 100, 100, 200]" in executions


def test_suite_column_when_the_period_has_several_suites() -> None:
    trends = _trends()
    trends["runs"][0]["suite_name"] = "smoke"
    md = _render(trends)
    changes = _section(md, "## Changes since the previous run")
    assert "| Suite | Category | Test |" in changes
    assert "| nightly | simulation | `pay_1` |" in changes
    assert (
        "| Suite | Category | Test | Failing runs | Failing since | Last pass |"
    ) in _section(md, "## Longest-failing cases")
    assert "| Run (UTC) | Suite | Pass rate |" in md
    assert "| 11-09 22:00 | smoke | 80.0% |" in md
    flaky = _section(md, "## Flaky cases")
    assert "| Suite | Category | Test | Commit | Passes | Fails |" in flaky
    assert (
        "| nightly | simulation | `pay_1` | `abcdef123456` | 1 | 2 |" in flaky
    )


def test_report_of_a_single_run() -> None:
    trends = _trends()
    trends = {
        "daily": trends["daily"][-1:],
        "runs": trends["runs"][-1:],
        "tags": [t for t in trends["tags"] if t["day"] == _DAYS[3]],
    }
    md = _render(trends)
    assert "| Metric | Last day (11-16) | Last 30 days |" in md
    assert "Previous day" not in md
    needs_two_runs = (
        "Needs at least two runs of a suite and category in the period."
    )
    assert needs_two_runs in _section(md, "## Changes since the previous run")
    assert needs_two_runs in _section(md, "## Longest-failing cases")
    assert "Needs at least two days with runs in the period." in _section(
        md, "### Biggest changes"
    )
    assert (
        "Needs at least two days with PASS or FAIL results in the period."
        in _section(md, "## Pass rate per day")
    )
    assert "### Last 1 run\n" in md
    assert "None in this period." in _section(md, "## Flaky cases")
    # Only the bar charts are left: executions and run time.
    assert md.count("```mermaid") == 2


def test_two_runs_of_different_suites_cannot_be_compared() -> None:
    trends = _trends()
    trends["runs"] = [
        _run(_DAYS[2], 22, 85, 15, suite_name="smoke"),
        _run(_DAYS[3], 22, 90, 10),
    ]
    md = _render(trends)
    assert (
        md.count(
            "Needs at least two runs of a suite and category in the period."
        )
        == 2
    )


def test_split_category_runs_cannot_be_compared() -> None:
    trends = _trends()
    trends["runs"] = [
        _run(_DAYS[2], 22, 10, 0, categories=["tool"]),
        _run(_DAYS[3], 22, 90, 10, categories=["simulation"]),
    ]
    trends["run_changes"] = []
    trends["longest_failing"] = []
    md = _render(trends)
    assert (
        md.count(
            "Needs at least two runs of a suite and category in the period."
        )
        == 2
    )
    assert "**New failures:** none." not in md


def test_single_run_category_is_listed_as_not_compared() -> None:
    trends = _trends()
    trends["runs"][-1]["categories"] = ["callback", "golden", "simulation"]
    md = _render(trends)
    note = "Not compared (only one run in the period): golden."
    assert note in _section(md, "## Changes since the previous run")
    assert note in _section(md, "## Longest-failing cases")


def test_report_without_the_runs_table() -> None:
    trends = _trends()
    trends["runs"] = []
    md = _render(trends)
    assert "## Time to run the evals" in md
    assert "### Last" not in md


def test_report_without_results() -> None:
    md = render_trends_markdown({"daily": []}, days=1, bq_dataset="p.d")
    assert "Last 1 day, all suites" in md
    assert "No eval results in this period." in md
    assert "mermaid" not in md
    assert "## " not in md


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def test_every_query_is_filtered_on_period_suite_and_git_ref() -> None:
    queries = trends_queries("p", "d")
    assert set(queries) == _QUERY_NAMES
    for name, sql in queries.items():
        assert "`p.d.eval_results`" in sql, name
        assert "`p.d.eval_runs`" in sql, name
        assert "@days" in sql, name
        assert "@suite IS NULL OR r.suite_name = @suite" in sql, name
        assert "@ref IS NULL OR r.git_ref = @ref" in sql, name
        # Whole days in UTC, and partition pruning on eval_results.
        period = "TIMESTAMP(DATE_SUB(CURRENT_DATE(), INTERVAL @days - 1 DAY))"
        assert f"r.started_at >= {period}" in sql, name
        assert f"x.started_at >= {period}" in sql, name


def test_queries_apply_the_report_rules() -> None:
    queries = trends_queries("p", "d", min_tag_cases=3)
    # Errors don't dilute goal/expectation/turn metrics or failed expectations.
    assert "IF(x.status != 'ERROR', x.goals_met, NULL)" in queries["daily"]
    assert "x.status != 'ERROR' AND x.turns IS NOT NULL" in queries["daily"]
    assert "AND x.status = 'FAIL'" in queries["failed_expectations"]
    # A run of some categories only must not hide the others' last run, and
    # error-only runs are skipped when looking for a case's previous verdict.
    assert (
        "PARTITION BY suite, category ORDER BY started_at DESC, run_key"
    ) in queries["run_changes"]
    assert "WHERE c.has_pass OR c.has_fail" in queries["run_changes"]
    assert (
        "WHERE cur.vn = 1 AND cur.rn = 1 AND prev.vn = 2"
        in (queries["run_changes"])
    )
    assert (
        "JOIN latest AS l USING (suite, category)"
        in (queries["longest_failing"])
    )
    assert (
        "ARRAY_AGG(DISTINCT x.category ORDER BY x.category) AS categories"
        in (queries["runs"])
    )
    # Tags used by too few cases are left out, and counted.
    assert "HAVING COUNT(DISTINCT test_id) >= 3" in queries["tags"]
    assert "HAVING COUNT(DISTINCT test_id) >= 3" in queries["run_changes"]
    assert "COUNT(*) AS tags" in queries["tag_summary"]
    assert "COUNTIF(cases >= 3) AS reported" in queries["tag_summary"]
    # Local runs and runs without a commit can't be flaky on a commit.
    assert (
        "r.git_sha IS NOT NULL AND r.git_sha != 'local'" in (queries["flaky"])
    )
    assert "COUNT(*) OVER () AS total" in queries["flaky"]


def _client(location: str | None = "EU") -> MagicMock:
    client = create_autospec(eval_trends.bigquery.Client, instance=True)
    client.get_dataset.return_value.location = location
    client.query.return_value.result.return_value = [{"day": "2026-11-16"}]
    return client


def _query_parameters(call: Any) -> dict[str, tuple[str, str | None]]:
    # Read the API representation: reading ``query_parameters`` back parses
    # values with helpers that other tests replace with mocks.
    resource = call.kwargs["job_config"].to_api_repr()
    return {
        p["name"]: (p["parameterType"]["type"], p["parameterValue"]["value"])
        for p in resource["query"]["queryParameters"]
    }


def test_fetch_trends_returns_the_rows_of_every_query() -> None:
    client = _client()

    trends = fetch_trends("p.d", min_tag_cases=2, client=client)

    assert set(trends) == _QUERY_NAMES
    assert trends["daily"] == [{"day": "2026-11-16"}]
    client.get_dataset.assert_called_once_with("p.d")
    queries = trends_queries("p", "d", min_tag_cases=2)
    assert [c.args[0] for c in client.query.call_args_list] == list(
        queries.values()
    )
    for call in client.query.call_args_list:
        resource = call.kwargs["job_config"].to_api_repr()
        assert resource["labels"] == {"cxas-scrapi": "eval-bq-trends"}


@pytest.mark.parametrize(
    ("location", "filters", "parameters"),
    [
        (
            "EU",
            {"days": 7, "suite_name": "nightly", "git_ref": "main"},
            {
                "days": ("INT64", "7"),
                "suite": ("STRING", "nightly"),
                "ref": ("STRING", "main"),
            },
        ),
        # No filter: every suite and git ref.
        (
            None,
            {},
            {
                "days": ("INT64", "30"),
                "suite": ("STRING", None),
                "ref": ("STRING", None),
            },
        ),
        # Blank filters (e.g. unset CI variables) act as no filter.
        (
            "US",
            {"suite_name": "", "git_ref": "   "},
            {
                "days": ("INT64", "30"),
                "suite": ("STRING", None),
                "ref": ("STRING", None),
            },
        ),
    ],
)
def test_fetch_trends_runs_in_the_dataset_location_with_the_filters(
    location: str | None,
    filters: dict[str, Any],
    parameters: dict[str, tuple[str, str | None]],
) -> None:
    client = _client(location=location)

    fetch_trends("p.d", client=client, **filters)

    for call in client.query.call_args_list:
        assert call.kwargs["location"] == location
        assert _query_parameters(call) == parameters


def test_fetch_trends_without_a_client_uses_the_tagged_client() -> None:
    client = _client()
    with patch.object(
        eval_trends.eval_bigquery,
        "bigquery_client",
        autospec=True,
        return_value=client,
    ) as make_client:
        fetch_trends("p.d")
    make_client.assert_called_once_with("p", "eval-bq-trends")
    assert client.query.call_count == len(_QUERY_NAMES)


def test_fetch_trends_submits_every_query_before_waiting_for_results() -> None:
    client = _client()
    events = []

    def _submit(*_args: Any, **_kwargs: Any) -> MagicMock:
        events.append("submitted")
        job = MagicMock()
        job.result.side_effect = lambda **_: events.append("collected") or []
        return job

    client.query.side_effect = _submit

    fetch_trends("p.d", client=client)

    count = len(_QUERY_NAMES)
    assert events == ["submitted"] * count + ["collected"] * count


def test_write_eval_trends_markdown_applies_and_describes_the_filters(
    tmp_path: pathlib.Path,
) -> None:
    out = tmp_path / "reports" / "trends.md"
    client = _client()
    with patch.object(
        eval_trends, "fetch_trends", autospec=True, return_value=_trends()
    ) as fetch:
        path = write_eval_trends_markdown(
            "p.d",
            str(out),
            days=14,
            suite_name="nightly",
            git_ref="main",
            min_tag_cases=3,
            client=client,
        )

    fetch.assert_called_once_with(
        "p.d",
        days=14,
        suite_name="nightly",
        git_ref="main",
        min_tag_cases=3,
        client=client,
    )
    assert path == str(out)
    text = out.read_text(encoding="utf-8")
    assert "Last 14 days, suite `nightly`, git ref `main`, from `p.d`." in text
    assert "Tags used by fewer than 3 cases are not reported" in text
