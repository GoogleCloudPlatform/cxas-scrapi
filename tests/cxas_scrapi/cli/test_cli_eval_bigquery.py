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

"""CLI tests for the BigQuery export flags of `cxas evals report`."""

import pathlib
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from cxas_scrapi.cli.main import get_parser

_REPORT = "cxas_scrapi.utils.reporting.generate_combined_report_from_dir"


def _run(*argv: str) -> None:
    args = get_parser().parse_args(list(argv))
    args.func(args)


@pytest.fixture
def report() -> Iterator[MagicMock]:
    with patch(_REPORT, return_value="report.html") as mock:
        yield mock


def test_evals_report_forwards_the_bq_flags(
    tmp_path: pathlib.Path, report: MagicMock
) -> None:
    _run(
        "evals",
        "report",
        "--output-dir",
        str(tmp_path),
        "--bq-dataset",
        "my-project.cxas_evals",
        "--bq-create-dataset",
        "--bq-location",
        "EU",
    )
    kwargs = report.call_args.kwargs
    assert kwargs["bq_dataset"] == "my-project.cxas_evals"
    assert kwargs["bq_create_dataset"] is True
    assert kwargs["bq_location"] == "EU"


def test_evals_report_with_the_dataset_only(
    tmp_path: pathlib.Path, report: MagicMock
) -> None:
    _run(
        "evals",
        "report",
        "--output-dir",
        str(tmp_path),
        "--bq-dataset",
        "my-project.cxas_evals",
    )
    kwargs = report.call_args.kwargs
    assert kwargs["bq_dataset"] == "my-project.cxas_evals"
    assert "bq_create_dataset" not in kwargs
    assert "bq_location" not in kwargs


def test_evals_report_without_bq_flags_does_not_export(
    tmp_path: pathlib.Path,
    report: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _run("evals", "report", "--output-dir", str(tmp_path))
    kwargs = report.call_args.kwargs
    assert "bq_dataset" not in kwargs
    assert "bq_create_dataset" not in kwargs
    assert "bq_location" not in kwargs
    assert "Warning" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "flags", [["--bq-create-dataset"], ["--bq-location", "EU"]]
)
def test_evals_report_warns_about_bq_flags_without_a_dataset(
    tmp_path: pathlib.Path,
    report: MagicMock,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
) -> None:
    _run("evals", "report", "--output-dir", str(tmp_path), *flags)
    kwargs = report.call_args.kwargs
    assert "bq_dataset" not in kwargs
    assert "bq_create_dataset" not in kwargs
    assert "bq_location" not in kwargs
    assert (
        "Warning: --bq-create-dataset and --bq-location are ignored without "
        "--bq-dataset."
    ) in capsys.readouterr().out


def test_evals_report_checks_the_dataset_options_before_running(
    tmp_path: pathlib.Path,
    report: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        _run(
            "evals",
            "report",
            "--output-dir",
            str(tmp_path),
            "--bq-dataset",
            "p.d",
            "--bq-create-dataset",
        )
    assert exc.value.code == 1
    report.assert_not_called()
    assert "--bq-create-dataset requires --bq-location" in (
        capsys.readouterr().out
    )


def test_evals_report_dataset_must_be_a_project_and_a_dataset(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        _run(
            "evals",
            "report",
            "--output-dir",
            str(tmp_path),
            "--bq-dataset",
            "cxas_evals",
        )
    assert exc.value.code == 2
    assert "expected 'project.dataset'" in capsys.readouterr().err
