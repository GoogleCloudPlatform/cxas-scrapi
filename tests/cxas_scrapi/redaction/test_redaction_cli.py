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

import argparse
import json
import pathlib
import typing
from unittest.mock import patch

import pytest
import yaml

from cxas_scrapi.cli import redaction_cli
from cxas_scrapi.cli.main import get_parser
from cxas_scrapi.redaction.checker import check_pairs
from cxas_scrapi.redaction.extract import AlignedLeaf, Leaf
from cxas_scrapi.redaction.policy import RedactionPolicy, Surface
from cxas_scrapi.redaction.report import render_html

from .conftest import POLICY_DICT

APP = "projects/p/locations/l/apps/a"


def _policy_file(tmp_path: pathlib.Path) -> pathlib.Path:
    p = tmp_path / "redaction_policy.yaml"
    p.write_text(yaml.safe_dump(POLICY_DICT))
    return p


def _report(over_redacted: bool) -> typing.Any:
    policy = RedactionPolicy.model_validate(POLICY_DICT)
    stored = "call [X]" if over_redacted else "call 988"
    raw = Leaf(0, 0, 0, "Agent", Surface.AGENT_TEXT, "text", "", "call 988")
    return check_pairs(
        [AlignedLeaf(raw, Leaf(*[*raw.__dict__.values()][:7], stored))],
        policy,
        "s",
        "live",
    )


def _ns(**kw: typing.Any) -> argparse.Namespace:
    base = {
        "app_name": APP,
        "policy": None,
        "output_json": None,
        "output_html": None,
        "max_findings": 5,
        "no_fail": False,
        "require": [],
    }
    base.update(kw)
    return argparse.Namespace(**base)


def test_parser_registers_redaction_group() -> None:
    args = get_parser().parse_args(
        ["redaction", "replay", "--app-name", APP, "--evaluation-run", "r1"]
    )
    assert args.func is redaction_cli.redaction_replay
    assert args.mode == "platform"
    args = get_parser().parse_args(
        ["redaction", "live", "--app-name", APP, "--script", "s.yaml"]
    )
    assert args.func is redaction_cli.redaction_live
    assert args.modality == "text"


def test_validate(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _policy_file(tmp_path)
    redaction_cli.redaction_validate(argparse.Namespace(policy=str(path)))
    out = capsys.readouterr().out
    assert "OK (5 categories)" in out
    assert "payment_card" in out

    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 2\ncategories: []\n")
    with pytest.raises(SystemExit) as exc:
        redaction_cli.redaction_validate(argparse.Namespace(policy=str(bad)))
    assert exc.value.code == 1


def test_replay_writes_reports_and_exit_code(tmp_path: pathlib.Path) -> None:
    path = _policy_file(tmp_path)
    out_json = tmp_path / "r.json"
    out_html = tmp_path / "r.html"
    with patch("cxas_scrapi.redaction.RedactionReplay") as replay_cls:
        replay_cls.return_value.replay_evaluation_run.return_value = _report(
            True
        )
        with pytest.raises(SystemExit) as exc:
            redaction_cli.redaction_replay(
                _ns(
                    policy=str(path),
                    evaluation_run="r1",
                    conversation=None,
                    file=None,
                    mode="platform",
                    inspect_template=None,
                    deidentify_template=None,
                    output_json=str(out_json),
                    output_html=str(out_html),
                )
            )
    assert exc.value.code == 1
    data = json.loads(out_json.read_text())
    assert data["passed"] is False
    assert "over_redaction" in out_html.read_text()


def test_replay_no_fail_and_missing_inputs(tmp_path: pathlib.Path) -> None:
    path = _policy_file(tmp_path)
    common = {
        "policy": str(path),
        "mode": "platform",
        "inspect_template": None,
        "deidentify_template": None,
    }
    with pytest.raises(SystemExit) as exc:
        redaction_cli.redaction_replay(
            _ns(evaluation_run=None, conversation=None, file=None, **common)
        )
    assert exc.value.code == 2

    with patch("cxas_scrapi.redaction.RedactionReplay") as replay_cls:
        replay_cls.return_value.replay_conversation_ids.return_value = _report(
            True
        )
        redaction_cli.redaction_replay(
            _ns(
                evaluation_run=None,
                conversation=["c1"],
                file=None,
                no_fail=True,
                **common,
            )
        )


def test_live_command(tmp_path: pathlib.Path) -> None:
    path = _policy_file(tmp_path)
    script = tmp_path / "s.yaml"
    script.write_text(
        yaml.safe_dump(
            {
                "conversations": [
                    {"name": "a", "turns": ["hi"]},
                    {"name": "b", "turns": ["yo"]},
                ]
            }
        )
    )
    with patch("cxas_scrapi.redaction.LiveRedactionCheck") as check_cls:
        check_cls.return_value.run_script.return_value = _report(False)
        redaction_cli.redaction_live(
            _ns(
                policy=str(path),
                script=str(script),
                name=["b"],
                modality="audio",
                deployment_id=None,
                persist_timeout=5.0,
            )
        )
    convs = check_cls.return_value.run_script.call_args.args[0]
    assert [c.name for c in convs] == ["b"]
    assert check_cls.return_value.run_script.call_args.kwargs["modality"] == (
        "audio"
    )


def test_variants(capsys: pytest.CaptureFixture[str]) -> None:
    redaction_cli.redaction_variants(argparse.Namespace(value="1199"))
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "1199"
    assert "double one double niner" in out


def test_render_html_escapes() -> None:
    report = _report(True)
    report.findings[0].raw_excerpt = "<script>"
    html = render_html(report)
    assert "&lt;script&gt;" in html
    assert "<script>" not in html
