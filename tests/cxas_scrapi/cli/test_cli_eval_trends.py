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

"""CLI tests for `cxas evals trends` and the tip that points to it."""

import pathlib
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from cxas_scrapi.cli.main import get_parser

_REPORT = "cxas_scrapi.utils.reporting.generate_combined_report_from_dir"
_WRITE_TRENDS = "cxas_scrapi.utils.eval_trends.write_eval_trends_markdown"
_TIP = "Tip: add --bq-dataset <project.dataset>"


def _run(*argv: str) -> None:
    args = get_parser().parse_args(list(argv))
    args.func(args)


# ---------------------------------------------------------------------------
# cxas evals trends
# ---------------------------------------------------------------------------


def _fake_write(**kwargs: Any) -> str:
    """Stands in for the report writer: writes a one-line report."""
    path = pathlib.Path(kwargs["output_path"])
    path.write_text("# Eval trends\n", encoding="utf-8")
    return str(path)


@pytest.fixture
def write_trends() -> Iterator[MagicMock]:
    with patch(_WRITE_TRENDS, side_effect=_fake_write) as mock:
        yield mock


def test_evals_trends_forwards_its_options(
    tmp_path: pathlib.Path,
    write_trends: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "trends.md"
    _run(
        "evals",
        "trends",
        "--bq-dataset",
        "p.d",
        "--days",
        "7",
        "--suite-name",
        "nightly",
        "--git-ref",
        "main",
        "--min-tag-cases",
        "2",
        "--output",
        str(out),
    )
    write_trends.assert_called_once_with(
        bq_dataset="p.d",
        output_path=str(out),
        days=7,
        suite_name="nightly",
        git_ref="main",
        min_tag_cases=2,
    )
    assert f"Trends report written to {out}" in capsys.readouterr().out


def test_evals_trends_defaults(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    write_trends: MagicMock,
) -> None:
    monkeypatch.chdir(tmp_path)
    _run("evals", "trends", "--bq-dataset", "p.d")
    write_trends.assert_called_once_with(
        bq_dataset="p.d",
        output_path="eval_trends.md",
        days=30,
        suite_name=None,
        git_ref=None,
        min_tag_cases=5,
    )


def test_evals_trends_treats_blank_filters_as_unset(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    write_trends: MagicMock,
) -> None:
    monkeypatch.chdir(tmp_path)
    _run(
        "evals",
        "trends",
        "--bq-dataset",
        "p.d",
        "--suite-name",
        "",
        "--git-ref",
        "  ",
    )
    write_trends.assert_called_once_with(
        bq_dataset="p.d",
        output_path="eval_trends.md",
        days=30,
        suite_name=None,
        git_ref=None,
        min_tag_cases=5,
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["--days", "7"],
        ["--bq-dataset", "cxas_evals"],
        ["--bq-dataset", "p.d", "--days", "0"],
        ["--bq-dataset", "p.d", "--days", "abc"],
        ["--bq-dataset", "p.d", "--min-tag-cases", "0"],
    ],
    ids=[
        "no dataset",
        "dataset without a project",
        "zero days",
        "days not a number",
        "zero cases per tag",
    ],
)
def test_evals_trends_rejects_invalid_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        get_parser().parse_args(["evals", "trends", *argv])
    assert exc.value.code == 2


def test_evals_trends_appends_the_report_to_the_step_summary(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    write_trends: MagicMock,
) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text("# Earlier step\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    _run(
        "evals",
        "trends",
        "--bq-dataset",
        "p.d",
        "--output",
        str(tmp_path / "trends.md"),
        "--github-step-summary",
    )
    assert summary.read_text(encoding="utf-8") == (
        "# Earlier step\n# Eval trends\n\n"
    )


def test_evals_trends_leaves_the_step_summary_alone_without_the_flag(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    write_trends: MagicMock,
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    _run(
        "evals",
        "trends",
        "--bq-dataset",
        "p.d",
        "--output",
        str(tmp_path / "trends.md"),
    )
    assert not summary.exists()


def test_evals_trends_step_summary_flag_outside_github_actions(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    write_trends: MagicMock,
) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    out = tmp_path / "trends.md"
    _run(
        "evals",
        "trends",
        "--bq-dataset",
        "p.d",
        "--output",
        str(out),
        "--github-step-summary",
    )
    assert out.read_text(encoding="utf-8") == "# Eval trends\n"
    assert list(tmp_path.iterdir()) == [out]


def test_evals_trends_failure_exits_non_zero(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with (
        patch(_WRITE_TRENDS, side_effect=RuntimeError("boom")),
        pytest.raises(SystemExit) as exc,
    ):
        _run(
            "evals",
            "trends",
            "--bq-dataset",
            "p.d",
            "--output",
            str(tmp_path / "t.md"),
        )
    assert exc.value.code == 1
    assert "Failed to generate the trends report: boom" in (
        capsys.readouterr().out
    )


# ---------------------------------------------------------------------------
# Tip printed by cxas evals report
# ---------------------------------------------------------------------------


@pytest.fixture
def report() -> Iterator[MagicMock]:
    with patch(_REPORT, return_value="report.html") as mock:
        yield mock


def test_evals_report_points_to_the_export_and_the_trends_report(
    tmp_path: pathlib.Path,
    report: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _run("evals", "report", "--output-dir", str(tmp_path))
    out = capsys.readouterr().out
    assert out.count(_TIP) == 1
    assert "cxas evals trends" in out


def test_evals_report_has_no_tip_when_exporting(
    tmp_path: pathlib.Path,
    report: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _run(
        "evals",
        "report",
        "--output-dir",
        str(tmp_path),
        "--bq-dataset",
        "p.d",
    )
    assert _TIP not in capsys.readouterr().out


def test_evals_report_dry_run_has_no_tip(
    tmp_path: pathlib.Path,
    report: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _run("evals", "report", "--output-dir", str(tmp_path), "--dry-run")
    assert report.call_args.kwargs["dry_run"] is True
    assert _TIP not in capsys.readouterr().out
