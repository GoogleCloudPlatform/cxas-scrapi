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

"""Tests for the BigQuery export of eval results."""

import datetime
import json
import os
import pathlib
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import create_autospec, patch

import numpy as np
import pytest
from google.api_core.exceptions import Forbidden, NotFound

from cxas_scrapi.cli.main import get_parser
from cxas_scrapi.utils import eval_bigquery, reporting
from cxas_scrapi.utils.eval_bigquery import (
    EvalBigQueryError,
    RunContext,
    build_result_rows,
    build_run_row,
    daily_by_tag_view_sql,
    daily_view_sql,
    parse_dataset,
    result_status,
    write_eval_results_to_bigquery,
)
from cxas_scrapi.utils.reporting import generate_combined_report_from_dir

_UTC = datetime.timezone.utc
_STARTED = datetime.datetime(2026, 11, 16, 22, 0, tzinfo=_UTC)


def _ctx(**overrides: Any) -> RunContext:
    values: dict[str, Any] = {
        "run_key": "rk1",
        "started_at": _STARTED,
        "finished_at": _STARTED + datetime.timedelta(minutes=5),
        "eval_duration_s": 290.5,
        "app_name": "projects/p/locations/l/apps/a",
        "suite_name": "nightly",
        "modality": "text",
        "runs": 1,
        "git_sha": "abc123",
        "git_ref": "main",
        "ci_run_id": "42",
        "ci_job_id": "7",
        "ci_attempt": 2,
        "cxas_scrapi_version": "1.11.0",
    }
    values.update(overrides)
    return RunContext(**values)


def _row(category: str, item: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Builds the single row of one result of the given category."""
    key = {"simulation": "sim_results"}.get(category, f"{category}_results")
    (row,) = build_result_rows(_ctx(), **{key: [item]}, **kwargs)
    return row


# ---------------------------------------------------------------------------
# Row building
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("item", "category", "expected"),
    [
        ({"passed": True, "error": "ignored"}, "simulation", "PASS"),
        ({"passed": False, "errors": []}, "simulation", "FAIL"),
        ({"passed": False, "error": "Timeout"}, "simulation", "ERROR"),
        # Platform simulations say FAIL even when the run errored.
        (
            {"passed": False, "status": "FAIL", "error": "Agent Exception"},
            "simulation",
            "ERROR",
        ),
        ({"passed": False, "errors": "quota exceeded"}, "golden", "ERROR"),
        ({"passed": False, "status": " errored "}, "tool", "ERROR"),
        # A failed assertion carries a message but is a failure.
        (
            {"passed": False, "status": "FAILED", "error": "assert 1 == 2"},
            "callback",
            "FAIL",
        ),
    ],
)
def test_result_status(
    item: dict[str, Any], category: str, expected: str
) -> None:
    assert result_status(item, category) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("my-project.evals", ("my-project", "evals")),
        (" my-project:evals ", ("my-project", "evals")),
        ("example.com:my-project.evals", ("example.com:my-project", "evals")),
    ],
)
def test_parse_dataset(value: str, expected: tuple[str, str]) -> None:
    assert parse_dataset(value) == expected


@pytest.mark.parametrize("value", ["evals", "a.b.c", "x.", "p . d"])
def test_parse_dataset_rejects_other_shapes(value: str) -> None:
    with pytest.raises(ValueError, match=r"project\.dataset"):
        parse_dataset(value)


def test_build_run_row_uses_gate_summary_and_context() -> None:
    row = build_run_row(
        _ctx(),
        {
            "pass_rate": 0.95,
            "total_cases": 20,
            "passed_cases": 19,
            "gate_passed": True,
            "min_run_passes": 1,
            "min_pass_rate": 0.9,
        },
    )
    assert row["run_key"] == "rk1"
    assert row["started_at"] == "2026-11-16T22:00:00+00:00"
    assert row["eval_duration_s"] == 290.5
    assert (row["pass_rate"], row["total_cases"]) == (0.95, 20)
    assert (row["min_run_passes"], row["min_pass_rate"]) == (1, 0.9)
    assert (row["ci_job_id"], row["ci_attempt"]) == ("7", 2)
    assert {f[0] for f in eval_bigquery.RUNS_SCHEMA} == set(row)


@pytest.mark.parametrize(
    ("min_pass_rate", "gate_passed", "expected"),
    [
        (0.9, False, False),
        # Without a minimum pass rate the gate summary says "passed" anyway.
        (None, True, None),
    ],
)
def test_run_row_has_a_gate_result_only_when_a_gate_was_asked_for(
    min_pass_rate: float | None, gate_passed: bool, expected: bool | None
) -> None:
    row = build_run_row(
        _ctx(), {"gate_passed": gate_passed, "min_pass_rate": min_pass_rate}
    )
    assert row["gate_passed"] is expected


def test_build_run_row_without_gate_summary() -> None:
    naive = datetime.datetime(2026, 11, 16, 22, 0)
    row = build_run_row(_ctx(started_at=naive, finished_at=None), None)
    # A naive time is taken as UTC.
    assert row["started_at"] == "2026-11-16T22:00:00+00:00"
    assert row["finished_at"] is None
    assert row["pass_rate"] is None
    assert row["gate_passed"] is None
    assert row["min_run_passes"] is None


def test_simulation_row() -> None:
    row = _row(
        "simulation",
        {
            "name": "billing_refund",
            "run": 2,
            "passed": False,
            "duration_s": 31.5,
            "goals": "1/2",
            "expectations": "1/2",
            "turns": 15,
            "session_id": "sess-1",
            "expectation_details": [
                {"expectation": "Confirms refund", "status": "Not Met"},
                {"expectation": "Greets user", "status": "Met"},
            ],
        },
        tags_by_name={"billing_refund": ["smoke", "billing"]},
    )
    assert row["category"] == "simulation"
    assert (row["name"], row["test_id"]) == ("billing_refund", "billing_refund")
    assert row["status"] == "FAIL"
    assert row["attempt"] == 2
    assert row["duration_s"] == 31.5
    assert row["tags"] == ["smoke", "billing"]
    assert (row["goals_met"], row["goals_total"]) == (1, 2)
    assert (row["expectations_met"], row["expectations_total"]) == (1, 2)
    assert row["turns"] == 15
    assert row["session_id"] == "sess-1"
    assert row["failed_expectations"] == ["Confirms refund"]
    assert row["error"] is None
    assert {f[0] for f in eval_bigquery.RESULTS_SCHEMA} == set(row)


def test_tags_come_from_the_result_or_the_simulation_definition() -> None:
    sims = [
        {"name": "s1", "passed": True, "tags": ["high", "home", "high", ""]},
        {"name": "s2", "passed": True},
    ]
    tools = [{"name": "s2", "tool": "billing", "passed": True}]
    rows = build_result_rows(
        _ctx(),
        sim_results=sims,
        tool_results=tools,
        tags_by_name={"s1": ["ignored"], "s2": ["from-definition"]},
    )
    # Tags on the result win and are deduplicated; definition tags only
    # apply to simulations, even when a tool test has the same name.
    assert [row["tags"] for row in rows] == [
        ["high", "home"],
        ["from-definition"],
        [],
    ]


def test_golden_row_counts_expectations_and_turns_from_lists() -> None:
    row = _row(
        "golden",
        {
            "name": "g1",
            "passed": False,
            "expectations": [
                {"expectation": "a", "status": "Met"},
                {"expectation": "b", "status": "Not Met"},
                {"expectation": "c", "status": "Met"},
            ],
            "turns": [{"user": "hi"}, {"user": "bye"}],
        },
    )
    assert row["category"] == "golden"
    assert (row["expectations_met"], row["expectations_total"]) == (2, 3)
    assert row["turns"] == 2
    assert row["failed_expectations"] == ["b"]
    assert row["goals_total"] is None


def test_tool_row_uses_the_tool_in_its_id_and_latency_as_duration() -> None:
    row = _row(
        "tool",
        {
            "name": "get_balance_ok",
            "tool": "get_balance",
            "passed": True,
            "status": "PASSED",
            "latency_ms": np.int64(250),
            "errors": float("nan"),
        },
    )
    assert row["test_id"] == "get_balance/get_balance_ok"
    assert row["duration_s"] == pytest.approx(0.25)
    assert row["error"] is None


@pytest.mark.parametrize(
    ("category", "owner"),
    [
        ("tool", {"tool": None}),
        ("tool", {"tool": "?"}),
        ("callback", {"agent": "?", "callback_type": "?"}),
    ],
)
def test_id_without_a_known_owner_is_the_test_name(
    category: str, owner: dict[str, Any]
) -> None:
    # "?" is the placeholder the report puts in missing fields.
    row = _row(category, {"name": "t1", "passed": True, **owner})
    assert row["test_id"] == "t1"


def test_callback_rows_with_the_same_name_get_distinct_ids() -> None:
    callbacks = [
        {
            "name": "test_escalate",
            "agent": "billing",
            "callback_type": "before_model_callbacks",
            "passed": False,
            "status": "FAILED",
            "error": "AssertionError: assert 1 == 2",
        },
        {
            "name": "test_escalate",
            "agent_name": "upgrades",
            "callback_type": "before_model_callbacks",
            "passed": True,
            "status": "PASSED",
            "error": "",
        },
    ]
    failed, passed = build_result_rows(_ctx(), callback_results=callbacks)
    assert failed["test_id"] == "billing/before_model_callbacks/test_escalate"
    assert passed["test_id"] == "upgrades/before_model_callbacks/test_escalate"
    assert (failed["status"], passed["status"]) == ("FAIL", "PASS")
    assert failed["error"] == "AssertionError: assert 1 == 2"
    assert (failed["attempt"], passed["attempt"]) == (1, 1)


def test_attempts_are_counted_when_results_have_no_run_number() -> None:
    rows = build_result_rows(
        _ctx(),
        golden_results=[
            {"name": "g1", "passed": True},
            {"name": "g1", "passed": False},
            {"name": "g2", "passed": True},
        ],
    )
    assert [(r["name"], r["attempt"]) for r in rows] == [
        ("g1", 1),
        ("g1", 2),
        ("g2", 1),
    ]


def test_name_fallbacks_and_items_that_are_not_results() -> None:
    rows = build_result_rows(
        _ctx(),
        sim_results=[
            {"test_name": "from_test_name", "passed": True},
            {"display_name": "from_display_name", "passed": True},
            {"displayName": "fromDisplayName", "passed": True},
            {"passed": True},
            "not a result",
            None,
        ],
    )
    assert [r["name"] for r in rows] == [
        "from_test_name",
        "from_display_name",
        "fromDisplayName",
        "unnamed",
    ]


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"error": "  boom  "}, "boom"),
        ({"error": float("nan"), "errors": ["a", "", "b"]}, "a; b"),
        ({"error": " ", "errors": {}}, None),
    ],
)
def test_error_column(item: dict[str, Any], expected: str | None) -> None:
    row = _row("simulation", {"name": "s", "passed": False, **item})
    assert row["error"] == expected


def test_numbers_too_large_for_the_table_are_left_empty() -> None:
    row = _row(
        "simulation",
        {
            "name": "s",
            "passed": True,
            "duration_s": float("inf"),
            "turns": 1e30,
            "goals": "99999999999999999999999/1",
        },
    )
    assert row["duration_s"] is None
    assert row["turns"] is None
    assert (row["goals_met"], row["goals_total"]) == (None, 1)


def test_details_keep_the_verdicts_and_what_was_tested() -> None:
    steps = [{"goal": "g", "status": "Completed", "justification": "ok"}]
    expectations = [{"expectation": "e", "status": "Met"}]
    row = _row(
        "simulation",
        {
            "name": "s",
            "passed": False,
            "status": "FAIL",
            "step_details": steps,
            "expectation_details": expectations,
            "naturalness": "4/5",
            "naturalness_label": "Human-like",
        },
    )
    assert row["details"] == {
        "status": "FAIL",
        "step_details": steps,
        "expectation_details": expectations,
        "naturalness": "4/5",
        "naturalness_label": "Human-like",
    }


def test_details_of_tool_and_callback_tests() -> None:
    tool = _row(
        "tool",
        {
            "name": "t",
            "tool": "get_balance",
            "passed": True,
            "status": "PASSED",
            "latency_ms": np.int64(250),
            "errors": float("nan"),
        },
    )
    callback = _row(
        "callback",
        {
            "name": "c",
            "agent": "billing",
            "callback_type": "before_model_callbacks",
            "passed": True,
            "status": "PASSED",
            "error": "",
        },
    )
    assert tool["details"] == {
        "status": "PASSED",
        "tool": "get_balance",
        "latency_ms": 250,
    }
    assert callback["details"] == {
        "status": "PASSED",
        "agent": "billing",
        "callback_type": "before_model_callbacks",
    }


def test_details_never_hold_other_fields() -> None:
    secret = [{"text": "my card is 4111"}]
    item = {
        "name": "s",
        "passed": True,
        # Conversation content and payloads of a raw result.
        "transcript": secret,
        "detailed_trace": secret,
        "turns": secret,
        "session_parameters": secret,
        "naturalness_details": secret,
        "audio_drift": secret,
        # Added to the results in place by the reports.
        "_processed_trace": secret,
        "_ces_audio": secret,
        # Anything else that isn't listed.
        "a_field_added_later": secret,
    }
    row = _row("simulation", item)
    assert row["details"] is None
    assert "4111" not in json.dumps(row)


def test_details_are_strict_json() -> None:
    row = _row(
        "simulation",
        {
            "name": "s",
            "passed": True,
            "step_details": [
                {"score": float("nan"), "weight": np.float64(1.5)},
                {"score": float("-inf"), "count": np.int64(3)},
            ],
        },
    )
    assert row["details"] == {
        "step_details": [
            {"score": None, "weight": 1.5},
            {"score": None, "count": 3},
        ]
    }
    # Load jobs reject NaN and Infinity.
    json.dumps(row, allow_nan=False)


# ---------------------------------------------------------------------------
# Views and writer
# ---------------------------------------------------------------------------


def test_view_sql_excludes_errors_and_unnests_tags() -> None:
    daily = daily_view_sql("p", "d")
    by_tag = daily_by_tag_view_sql("p", "d")
    assert "`p.d.eval_results`" in daily
    assert "`p.d.eval_runs`" in daily
    assert "COUNTIF(x.status IN ('PASS', 'FAIL'))" in daily
    assert "goal_completion_rate" in daily
    assert "expectation_met_rate" in daily
    assert "CROSS JOIN UNNEST(x.tags) AS tag" in by_tag


def _client(location: str | None = "EU", views_exist: bool = False) -> Any:
    """A BigQuery client double that follows the real client's signatures."""
    client = create_autospec(eval_bigquery.bigquery.Client, instance=True)
    client.get_dataset.return_value = SimpleNamespace(location=location)
    # New tables: creating one returns it as it was asked for.
    client.create_table.side_effect = lambda table, exists_ok: table
    if not views_exist:
        client.get_table.side_effect = NotFound("no such view")
    return client


def _rows() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    run_row = build_run_row(_ctx(), {"pass_rate": 1.0})
    result_rows = build_result_rows(
        _ctx(), sim_results=[{"name": "s", "passed": True}]
    )
    return run_row, result_rows


def test_write_creates_tables_loads_rows_and_creates_views() -> None:
    client = _client()
    run_row, result_rows = _rows()

    summary = write_eval_results_to_bigquery(
        "p.d", run_row, result_rows, client=client
    )

    assert summary == {"dataset": "p.d", "run_key": "rk1", "results": 1}
    created = [c.args[0] for c in client.create_table.call_args_list]
    tables = {t.table_id: t for t in created if not t.view_query}
    assert set(tables) == {"eval_runs", "eval_results"}
    for table in tables.values():
        assert table.time_partitioning.field == "started_at"
        assert table.time_partitioning.type_ == "DAY"
    assert tables["eval_runs"].clustering_fields == ["suite_name"]
    assert tables["eval_results"].clustering_fields == ["category", "name"]
    views = {t.table_id: t.view_query for t in created if t.view_query}
    assert set(views) == {"v_eval_daily", "v_eval_daily_by_tag"}
    assert views["v_eval_daily"] == daily_view_sql("p", "d")

    loads = client.load_table_from_json.call_args_list
    # Results first: a failed load must not leave a run without results.
    assert [c.args[1] for c in loads] == ["p.d.eval_results", "p.d.eval_runs"]
    assert loads[0].args[0] == result_rows
    assert loads[1].args[0] == [run_row]
    assert all(c.kwargs["location"] == "EU" for c in loads)
    for load in loads:
        job_config = load.kwargs["job_config"]
        # Appended: an export never replaces the history.
        assert job_config.write_disposition == "WRITE_APPEND"
        assert job_config.schema_update_options == ["ALLOW_FIELD_ADDITION"]
    # New tables are loaded with the current columns.
    assert _loaded_columns(client, "p.d.eval_results") == [
        name for name, _, _ in eval_bigquery.RESULTS_SCHEMA
    ]
    # Load jobs are labelled, so a project can find the export's jobs.
    assert [c.kwargs["job_config"].labels for c in loads] == [
        {"cxas-scrapi": "eval-bq-export"}
    ] * 2
    # Appending needs no query job, and the dataset is never created.
    client.query.assert_not_called()
    client.create_dataset.assert_not_called()


def test_default_client_is_the_library_client_tagged_with_the_feature() -> None:
    client = _client()
    with patch.object(eval_bigquery, "Common", autospec=True) as common:
        common.return_value.get_bigquery_client.return_value = client
        write_eval_results_to_bigquery("p.d", *_rows())
    # The library's client factory puts the extension in the user agent.
    common.assert_called_once_with(user_agent_extension="eval-bq-export")
    common.return_value.get_bigquery_client.assert_called_once_with(
        project_id="p"
    )
    assert client.load_table_from_json.call_count == 2


def _loaded_columns(client: Any, table_ref: str) -> list[str]:
    (load,) = [
        c
        for c in client.load_table_from_json.call_args_list
        if c.args[1] == table_ref
    ]
    return [field.name for field in load.kwargs["job_config"].schema]


def test_load_names_every_column_of_an_existing_table() -> None:
    # A load job fails if it leaves out a column the table has (written by
    # another version), and adds the columns the table doesn't have yet.
    field = eval_bigquery.bigquery.SchemaField
    existing = [
        field("run_key", "STRING", mode="REQUIRED"),
        field("from_another_version", "STRING"),
    ]
    client = _client()
    client.create_table.side_effect = lambda table, exists_ok: SimpleNamespace(
        schema=existing
    )

    write_eval_results_to_bigquery("p.d", *_rows(), client=client)

    current = [name for name, _, _ in eval_bigquery.RESULTS_SCHEMA]
    assert _loaded_columns(client, "p.d.eval_results") == [
        "run_key",
        "from_another_version",
        *[name for name in current if name != "run_key"],
    ]


def test_write_without_results_still_records_the_run() -> None:
    client = _client()
    run_row, _ = _rows()
    summary = write_eval_results_to_bigquery("p.d", run_row, [], client=client)
    assert summary["results"] == 0
    loads = client.load_table_from_json.call_args_list
    assert [c.args[1] for c in loads] == ["p.d.eval_runs"]


def test_failed_results_load_leaves_no_run_row() -> None:
    # A load job reports a bad row or a schema mismatch when it's waited for.
    client = _client()
    client.load_table_from_json.return_value.result.side_effect = RuntimeError(
        "load failed"
    )
    run_row, result_rows = _rows()
    with pytest.raises(RuntimeError, match="load failed"):
        write_eval_results_to_bigquery(
            "p.d", run_row, result_rows, client=client
        )
    assert client.load_table_from_json.call_count == 1
    assert client.load_table_from_json.call_args.args[1] == "p.d.eval_results"


def test_problem_with_a_view_fails_the_export_before_any_row_is_written() -> (
    None
):
    client = _client()
    client.get_table.side_effect = Forbidden("no access to the view")
    with pytest.raises(Forbidden):
        write_eval_results_to_bigquery("p.d", *_rows(), client=client)
    client.load_table_from_json.assert_not_called()


def test_views_are_only_updated_when_their_definition_changed() -> None:
    client = _client(views_exist=True)
    current = SimpleNamespace(view_query=daily_view_sql("p", "d"))
    outdated = SimpleNamespace(view_query="SELECT 1")
    client.get_table.side_effect = [current, outdated]
    run_row, result_rows = _rows()

    write_eval_results_to_bigquery("p.d", run_row, result_rows, client=client)

    client.update_table.assert_called_once_with(outdated, ["view_query"])
    assert outdated.view_query == daily_by_tag_view_sql("p", "d")
    created = [c.args[0].table_id for c in client.create_table.call_args_list]
    assert created == ["eval_runs", "eval_results"]


@pytest.mark.parametrize(
    ("create_dataset", "message"),
    [(False, "bq mk --dataset"), (True, "--bq-location")],
)
def test_missing_dataset_is_never_created_implicitly(
    create_dataset: bool, message: str
) -> None:
    # Creating it needs a location too: it can't change afterwards.
    client = _client()
    client.get_dataset.side_effect = NotFound("missing")
    with pytest.raises(EvalBigQueryError, match=message):
        write_eval_results_to_bigquery(
            "p.d", *_rows(), create_dataset=create_dataset, client=client
        )
    client.create_dataset.assert_not_called()
    client.load_table_from_json.assert_not_called()


def test_create_dataset_with_location() -> None:
    client = _client()
    client.get_dataset.side_effect = NotFound("missing")
    client.create_dataset.return_value = SimpleNamespace(location="EU")
    write_eval_results_to_bigquery(
        "p.d", *_rows(), create_dataset=True, location="EU", client=client
    )
    dataset = client.create_dataset.call_args.args[0]
    assert (dataset.dataset_id, dataset.location) == ("d", "EU")
    assert client.load_table_from_json.call_args.kwargs["location"] == "EU"


@pytest.mark.parametrize(
    ("dataset_location", "requested", "job_location"),
    [("EU", "eu", "EU"), (None, "EU", "EU"), ("US", "EU", None)],
)
def test_location_is_checked_against_the_dataset(
    dataset_location: str | None, requested: str, job_location: str | None
) -> None:
    client = _client(location=dataset_location)
    if job_location is None:
        with pytest.raises(EvalBigQueryError, match="is in US, not EU"):
            write_eval_results_to_bigquery(
                "p.d", *_rows(), location=requested, client=client
            )
        return
    write_eval_results_to_bigquery(
        "p.d", *_rows(), location=requested, client=client
    )
    assert client.load_table_from_json.call_args.kwargs["location"] == (
        job_location
    )


# ---------------------------------------------------------------------------
# Export from the combined report
# ---------------------------------------------------------------------------

_CI_VARS = (
    "GITHUB_SHA",
    "GITHUB_REF_NAME",
    "GITHUB_RUN_ID",
    "GITHUB_RUN_ATTEMPT",
    "CI_COMMIT_SHA",
    "CI_COMMIT_REF_NAME",
    "CI_PIPELINE_ID",
    "CI_JOB_ID",
    "KOKORO_GIT_COMMIT",
    "KOKORO_GOB_BRANCH",
    "KOKORO_BUILD_ID",
    "GUITAR_CHANGELIST",
    "GUITAR_WORKFLOW_ID",
    "CITC_CL",
    "CITC_CLIENT",
)
_ONE_SIM = [{"name": "s", "passed": True}]


@pytest.fixture(autouse=True)
def no_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tests see the same run context on a laptop and in CI."""
    for var in _CI_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def tokyo_time(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Local time nine hours ahead of UTC, so the two can't be mixed up."""
    if not hasattr(time, "tzset"):
        pytest.skip("needs time.tzset() to change the local time zone")
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _write_json(path: pathlib.Path, payload: Any) -> pathlib.Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _set_mtime(path: pathlib.Path, when: datetime.datetime) -> None:
    os.utime(path, (when.timestamp(), when.timestamp()))


def _report(
    out_dir: pathlib.Path, client: Any = None, **kwargs: Any
) -> tuple[Any, dict[str, list[dict[str, Any]]]]:
    """Runs the report, with the export writing to a BigQuery client double.

    Simulations only, unless ``include`` says otherwise. Returns what the
    report returned and the rows the export loaded, by table name.
    """
    client = client or _client()
    kwargs.setdefault("include", ["sims"])
    with patch.object(
        eval_bigquery, "bigquery_client", autospec=True, return_value=client
    ):
        result = generate_combined_report_from_dir(
            output_dir=str(out_dir), **kwargs
        )
    loaded = {
        call.args[1].rsplit(".", 1)[-1]: call.args[0]
        for call in client.load_table_from_json.call_args_list
    }
    return result, loaded


def _time(run_row: dict[str, Any], column: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(run_row[column])


def test_report_exports_the_run_with_its_gitlab_context(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CI_COMMIT_SHA", "sha-gitlab")
    monkeypatch.setenv("CI_COMMIT_REF_NAME", "feature/x")
    monkeypatch.setenv("CI_PIPELINE_ID", "1001")
    monkeypatch.setenv("CI_JOB_ID", "2002")
    _write_json(tmp_path / "sim_results.json", _ONE_SIM)

    (_, gate), loaded = _report(
        tmp_path,
        suite_name="nightly",
        bq_dataset="p.d",
        return_gate_status=True,
    )

    (run_row,) = loaded["eval_runs"]
    run_key = run_row["run_key"]
    assert run_row["suite_name"] == "nightly"
    assert (run_row["git_sha"], run_row["git_ref"]) == (
        "sha-gitlab",
        "feature/x",
    )
    assert (run_row["ci_run_id"], run_row["ci_job_id"]) == ("1001", "2002")
    assert run_row["ci_attempt"] is None
    assert (run_row["total_cases"], run_row["passed_cases"]) == (1, 1)
    assert [row["run_key"] for row in loaded["eval_results"]] == [run_key]
    assert gate["bigquery_export"] == {
        "dataset": "p.d",
        "run_key": run_key,
        "results": 1,
    }
    stamp = (tmp_path / "EVAL_STAMP-nightly.txt").read_text(encoding="utf-8")
    assert f"run_key:       {run_key}" in stamp
    assert (
        f"Exported 1 eval result(s) to p.d (run_key {run_key})."
        in capsys.readouterr().out
    )


@pytest.mark.parametrize(("attempt", "expected"), [("3", 3), ("retry", None)])
def test_github_context_wins_and_the_attempt_is_parsed(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    attempt: str,
    expected: int | None,
) -> None:
    monkeypatch.setenv("GITHUB_SHA", "sha-github")
    monkeypatch.setenv("GITHUB_RUN_ID", "77")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", attempt)
    monkeypatch.setenv("CI_COMMIT_SHA", "sha-gitlab")
    monkeypatch.setenv("CI_PIPELINE_ID", "1001")
    _write_json(tmp_path / "sim_results.json", _ONE_SIM)

    _, loaded = _report(tmp_path, bq_dataset="p.d")

    (run_row,) = loaded["eval_runs"]
    assert (run_row["git_sha"], run_row["ci_run_id"]) == ("sha-github", "77")
    assert run_row["ci_attempt"] == expected
    assert run_row["git_ref"] == "local"


def test_simulations_get_the_tags_of_their_definitions(
    tmp_path: pathlib.Path,
) -> None:
    sim_dir = tmp_path / "sims"
    sim_dir.mkdir()
    (sim_dir / "billing.yaml").write_text(
        "evals:\n  - name: refund_1\n    tags: [high, billing]\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    _write_json(
        out_dir / "sim_results.json", [{"name": "refund_1", "passed": True}]
    )

    _, loaded = _report(out_dir, simulation_dir=str(sim_dir), bq_dataset="p.d")

    assert [row["tags"] for row in loaded["eval_results"]] == [
        ["high", "billing"]
    ]


def test_dataset_options_reach_the_export(tmp_path: pathlib.Path) -> None:
    _write_json(tmp_path / "sim_results.json", _ONE_SIM)
    client = _client()
    client.get_dataset.side_effect = NotFound("missing")
    client.create_dataset.return_value = SimpleNamespace(location="EU")

    _report(
        tmp_path,
        client=client,
        bq_dataset="p.d",
        bq_create_dataset=True,
        bq_location="EU",
    )

    dataset = client.create_dataset.call_args.args[0]
    assert (dataset.dataset_id, dataset.location) == ("d", "EU")


def test_every_category_is_exported(tmp_path: pathlib.Path) -> None:
    _write_json(tmp_path / "sim_results.json", _ONE_SIM)
    _write_json(tmp_path / "ces_results.json", [{"name": "g", "passed": False}])
    (tmp_path / "tool_results.csv").write_text(
        "test_name,tool,status,latency (ms),errors\n"
        "balance_ok,get_balance,PASSED,250,\n",
        encoding="utf-8",
    )
    (tmp_path / "callback_results.csv").write_text(
        "test_name,agent_name,callback_type,status,error_message\n"
        "test_escalate,billing,before_model_callbacks,FAILED,assert 1 == 2\n",
        encoding="utf-8",
    )

    _, loaded = _report(
        tmp_path,
        include=["sims", "goldens", "tools", "callbacks"],
        bq_dataset="p.d",
    )

    rows = {row["category"]: row for row in loaded["eval_results"]}
    assert {c: r["status"] for c, r in rows.items()} == {
        "simulation": "PASS",
        "golden": "FAIL",
        "tool": "PASS",
        "callback": "FAIL",
    }
    assert rows["tool"]["test_id"] == "get_balance/balance_ok"
    assert rows["tool"]["duration_s"] == pytest.approx(0.25)
    assert rows["callback"]["test_id"] == (
        "billing/before_model_callbacks/test_escalate"
    )
    assert rows["callback"]["error"] == "assert 1 == 2"


def test_every_golden_attempt_is_exported(tmp_path: pathlib.Path) -> None:
    # The report shows one reconciled result per golden.
    _write_json(
        tmp_path / "ces_results.json",
        [
            {"name": "g", "passed": False},
            {"name": "g", "passed": True},
            {"name": "g", "passed": False},
        ],
    )

    _, loaded = _report(
        tmp_path, include=["goldens"], min_run_passes=2, bq_dataset="p.d"
    )

    assert [(r["attempt"], r["status"]) for r in loaded["eval_results"]] == [
        (1, "FAIL"),
        (2, "PASS"),
        (3, "FAIL"),
    ]
    (run_row,) = loaded["eval_runs"]
    assert (run_row["total_cases"], run_row["passed_cases"]) == (1, 0)


def test_conversation_shown_in_the_report_is_not_exported(
    tmp_path: pathlib.Path,
) -> None:
    # The HTML report adds the processed trace to each result in place.
    _write_json(
        tmp_path / "sim_results.json",
        [
            {
                "name": "s",
                "passed": True,
                "transcript": "User: my card number is 4111",
                "detailed_trace": [
                    "User: my card number is 4111\nAgent Text: thanks Jane"
                ],
                "expectation_details": [
                    {"expectation": "Greets the user", "status": "Met"}
                ],
            }
        ],
    )
    report_path = tmp_path / "report.html"

    _, loaded = _report(
        tmp_path, output_path=str(report_path), bq_dataset="p.d"
    )

    assert "4111" in report_path.read_text(encoding="utf-8")
    (row,) = loaded["eval_results"]
    assert "4111" not in json.dumps(loaded)
    assert row["details"] == {
        "expectation_details": [
            {"expectation": "Greets the user", "status": "Met"}
        ]
    }


_WRITTEN = datetime.datetime(2026, 10, 29, 15, 47, 27, tzinfo=_UTC)
# 15:47:27 in Tokyo, where the tests run these cases, is 06:47:27 UTC.
_NAMED = datetime.datetime(2026, 10, 29, 6, 47, 27, tzinfo=_UTC)
_TEN_MINUTES = datetime.timedelta(seconds=600)


@pytest.mark.usefixtures("tokyo_time")
@pytest.mark.parametrize(
    ("file_name", "wall_clock_s", "started", "finished", "duration"),
    [
        # --timestamped names the files when the run starts, in local time;
        # the file time is then ignored.
        (
            "sim_results_20261029_154727.json",
            600,
            _NAMED,
            _NAMED + _TEN_MINUTES,
            600,
        ),
        ("sim_results_20261029_154727.json", None, _NAMED, _NAMED, None),
        # Without a timestamp, the files were written when the run finished.
        ("sim_results.json", 600, _WRITTEN - _TEN_MINUTES, _WRITTEN, 600),
        ("sim_results.json", None, _WRITTEN, _WRITTEN, None),
    ],
    ids=["name+duration", "name", "file time+duration", "file time"],
)
def test_run_is_dated_from_the_result_files(
    tmp_path: pathlib.Path,
    file_name: str,
    wall_clock_s: float | None,
    started: datetime.datetime,
    finished: datetime.datetime,
    duration: float | None,
) -> None:
    payload = _ONE_SIM
    if wall_clock_s is not None:
        payload = {"wall_clock_s": wall_clock_s, "results": _ONE_SIM}
    sims = _write_json(tmp_path / file_name, payload)
    _set_mtime(sims, _WRITTEN)

    _, loaded = _report(tmp_path, bq_dataset="p.d")

    (run_row,) = loaded["eval_runs"]
    assert _time(run_row, "started_at") == started
    assert _time(run_row, "finished_at") == finished
    assert run_row["eval_duration_s"] == duration
    assert [row["started_at"] for row in loaded["eval_results"]] == [
        run_row["started_at"]
    ]


@pytest.mark.parametrize("wall_clock_s", ["soon", 0, 1e999, 10**400])
def test_run_time_that_is_not_a_positive_number_is_left_empty(
    tmp_path: pathlib.Path, wall_clock_s: Any
) -> None:
    sims = _write_json(
        tmp_path / "sim_results.json",
        {"wall_clock_s": wall_clock_s, "results": _ONE_SIM},
    )
    _set_mtime(sims, _WRITTEN)

    _, loaded = _report(tmp_path, bq_dataset="p.d")

    (run_row,) = loaded["eval_runs"]
    assert run_row["eval_duration_s"] is None
    assert _time(run_row, "started_at") == _WRITTEN


def test_run_of_the_latest_result_file_dates_the_export(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "tool_results.csv").write_text(
        "test_name,tool,status\nbalance_ok,get_balance,PASSED\n",
        encoding="utf-8",
    )
    written = datetime.datetime(2026, 10, 29, 15, 47, 27, tzinfo=_UTC)
    _set_mtime(tmp_path / "tool_results.csv", written)

    _, loaded = _report(tmp_path, include=["tools"], bq_dataset="p.d")

    (run_row,) = loaded["eval_runs"]
    assert _time(run_row, "started_at") == written


def test_run_without_result_files_is_dated_from_the_command(
    tmp_path: pathlib.Path,
) -> None:
    before = datetime.datetime.now(_UTC)

    _, loaded = _report(tmp_path, bq_dataset="p.d")

    after = datetime.datetime.now(_UTC)
    # Nothing to load into eval_results; the run is still recorded.
    assert list(loaded) == ["eval_runs"]
    (run_row,) = loaded["eval_runs"]
    assert before <= _time(run_row, "started_at") <= after
    assert run_row["total_cases"] == 0


def test_run_started_by_the_report_is_timed(tmp_path: pathlib.Path) -> None:
    results = {"simulation": _ONE_SIM, "tool": [], "callback": [], "golden": []}
    before = datetime.datetime.now(_UTC)
    with patch.object(reporting, "run_all_evals", return_value=results):
        _, loaded = _report(
            tmp_path,
            run=True,
            app_name="projects/p/locations/l/apps/a",
            bq_dataset="p.d",
        )
    after = datetime.datetime.now(_UTC)

    (run_row,) = loaded["eval_runs"]
    started = _time(run_row, "started_at")
    finished = _time(run_row, "finished_at")
    assert before <= started <= finished <= after
    assert (finished - started).total_seconds() == pytest.approx(
        run_row["eval_duration_s"]
    )


def test_report_without_bq_dataset_does_not_export(
    tmp_path: pathlib.Path,
) -> None:
    # The run time is only read for the export: an unusable one must not
    # break a report that doesn't export.
    _write_json(
        tmp_path / "sim_results.json",
        {"wall_clock_s": 10**400, "results": _ONE_SIM},
    )
    client = _client()

    (path, gate), loaded = _report(
        tmp_path, client=client, suite_name="nightly", return_gate_status=True
    )

    assert os.path.exists(path)
    assert loaded == {}
    assert client.mock_calls == []
    assert "run_key" not in gate
    assert "bigquery_export" not in gate
    stamp = (tmp_path / "EVAL_STAMP-nightly.txt").read_text(encoding="utf-8")
    assert "run_key" not in stamp


def test_export_alone_returns_the_report_path_and_records_the_gate_numbers(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_json(tmp_path / "sim_results.json", _ONE_SIM)

    path, loaded = _report(tmp_path, bq_dataset="p.d")

    assert os.path.exists(path)
    # Like the other publishing options, the export prints the gate summary.
    assert "----- 1/1 cases passed -----" in capsys.readouterr().out
    (run_row,) = loaded["eval_runs"]
    assert (run_row["pass_rate"], run_row["total_cases"]) == (1.0, 1)
    # No minimum pass rate was asked for, so there is no gate result.
    assert run_row["gate_passed"] is None


def test_failed_export_does_not_break_the_report_or_the_gate(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_json(tmp_path / "sim_results.json", _ONE_SIM)
    client = _client()
    client.load_table_from_json.side_effect = RuntimeError("quota exceeded")

    (path, gate), _ = _report(
        tmp_path,
        client=client,
        bq_dataset="p.d",
        min_pass_rate=0.5,
        return_gate_status=True,
    )

    assert os.path.exists(path)
    assert gate["gate_passed"] is True
    assert gate["bigquery_export"] is None
    out = capsys.readouterr().out
    assert "::warning::BigQuery export to p.d failed: quota exceeded" in out
    assert "Exported" not in out


# ---------------------------------------------------------------------------
# Tags from the simulation definitions
# ---------------------------------------------------------------------------

_SIM_YAML = "evals:\n  - name: {name}\n    tags: [{tag}]\n"


def test_tags_are_read_from_the_files_the_runner_reads(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "a.yaml").write_text(_SIM_YAML.format(name="a", tag="t1"))
    (tmp_path / "b.yml").write_text(_SIM_YAML.format(name="b", tag="t2"))
    (tmp_path / "c.json").write_text(
        json.dumps({"evals": [{"name": "c", "tags": ["t3"]}]})
    )
    # What can't be used is skipped.
    (tmp_path / "broken.yaml").write_text("evals: [unclosed")
    (tmp_path / "strings.yaml").write_text("- just a string\n- another\n")
    (tmp_path / "no_tags.yaml").write_text("evals:\n  - name: untagged\n")
    # Like the runner, only the top level of the folder is read.
    nested = tmp_path / "archive"
    nested.mkdir()
    (nested / "old.yaml").write_text(_SIM_YAML.format(name="a", tag="old"))

    assert reporting._load_sim_tags_by_name(str(tmp_path)) == {
        "a": ["t1"],
        "b": ["t2"],
        "c": ["t3"],
    }


def test_tags_are_read_from_a_single_file(tmp_path: pathlib.Path) -> None:
    sim_file = tmp_path / "only.yaml"
    sim_file.write_text(_SIM_YAML.format(name="a", tag="t1"))
    assert reporting._load_sim_tags_by_name(str(sim_file)) == {"a": ["t1"]}


def test_tags_default_to_the_runner_simulation_folder(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert reporting._load_sim_tags_by_name(None) == {}
    assert reporting._load_sim_tags_by_name(str(tmp_path / "missing")) == {}

    folder = tmp_path / "evals" / "simulations"
    folder.mkdir(parents=True)
    (folder / "a.yaml").write_text(_SIM_YAML.format(name="a", tag="t1"))
    assert reporting._load_sim_tags_by_name(None) == {"a": ["t1"]}


def test_tags_resolve_from_app_dir_when_simulation_dir_is_not_explicit(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    app_sims = tmp_path / "my_agent" / "evals" / "simulations"
    app_sims.mkdir(parents=True)
    (app_sims / "a.yaml").write_text(_SIM_YAML.format(name="a", tag="from_app"))

    # Both None (Python API) and the CLI default resolve under app_dir.
    assert reporting._load_sim_tags_by_name(None, app_dir="my_agent") == {
        "a": ["from_app"]
    }
    assert reporting._load_sim_tags_by_name(
        "evals/simulations/", app_dir="my_agent"
    ) == {"a": ["from_app"]}

    # An explicit non-default simulation_dir is not overridden by app_dir.
    assert (
        reporting._load_sim_tags_by_name(
            str(tmp_path / "missing"), app_dir="my_agent"
        )
        == {}
    )

    # When app_dir has no evals/simulations/, fall back to ./evals/simulations/.
    cwd_sims = tmp_path / "evals" / "simulations"
    cwd_sims.mkdir(parents=True)
    (cwd_sims / "a.yaml").write_text(_SIM_YAML.format(name="a", tag="from_cwd"))
    assert reporting._load_sim_tags_by_name(
        "evals/simulations/", app_dir=str(tmp_path / "other_agent")
    ) == {"a": ["from_cwd"]}


def test_cli_report_with_app_dir_and_run_loads_sim_tags_without_simulation_dir(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    app_sims = tmp_path / "my_agent" / "evals" / "simulations"
    app_sims.mkdir(parents=True)
    (app_sims / "billing.yaml").write_text(
        "evals:\n  - name: refund_1\n    tags: [high, billing]\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    results = {
        "simulation": [{"name": "refund_1", "passed": True}],
        "tool": [],
        "callback": [],
        "golden": [],
    }
    client = _client()

    args = get_parser().parse_args(
        [
            "evals",
            "report",
            "--output-dir",
            str(out_dir),
            "--app-dir",
            "my_agent",
            "--run",
            "--bq-dataset",
            "p.d",
        ]
    )
    with (
        patch.object(reporting, "run_all_evals", return_value=results),
        patch.object(
            eval_bigquery, "bigquery_client", autospec=True, return_value=client
        ),
    ):
        args.func(args)

    loaded = {
        call.args[1].rsplit(".", 1)[-1]: call.args[0]
        for call in client.load_table_from_json.call_args_list
    }
    assert [row["tags"] for row in loaded["eval_results"]] == [
        ["high", "billing"]
    ]
