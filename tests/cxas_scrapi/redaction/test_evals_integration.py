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

"""Integration of ``redaction_expectations`` with sims and goldens."""

import pathlib
import types
import typing
from unittest.mock import MagicMock, patch

import yaml

from cxas_scrapi.evals.simulation_evals import SimulationEvals
from cxas_scrapi.redaction.evals import (
    check_golden_results,
    golden_redaction_expectations,
)
from cxas_scrapi.redaction.policy import DEFAULT_POLICY_FILENAME
from cxas_scrapi.utils.eval_utils import Conversations
from cxas_scrapi.utils.reporting import _load_sim_test_cases

from .conftest import POLICY_DICT

APP = "projects/p/locations/l/apps/a"


def _project(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / DEFAULT_POLICY_FILENAME).write_text(yaml.safe_dump(POLICY_DICT))
    evals = tmp_path / "evals"
    evals.mkdir()
    return evals


def test_sim_loader_merges_redaction_expectations(
    tmp_path: pathlib.Path,
) -> None:
    evals = _project(tmp_path)
    sim = evals / "sims.yaml"
    sim.write_text(
        yaml.safe_dump(
            {
                "common_redaction_expectations": {"require": ["payment_card"]},
                "evals": [
                    {"name": "a", "steps": []},
                    {
                        "name": "b",
                        "steps": [],
                        "redaction_expectations": {
                            "values": {"security_code": ["456"]}
                        },
                    },
                    {"name": "c", "steps": [], "redaction_expectations": False},
                ],
            }
        )
    )
    a, b, c = _load_sim_test_cases(str(sim))
    assert a["redaction_expectations"] == {"require": ["payment_card"]}
    assert a["_redaction_base_dir"] == str(evals)
    assert b["redaction_expectations"]["values"] == {"security_code": ["456"]}
    assert b["redaction_expectations"]["require"] == ["payment_card"]
    assert "redaction_expectations" not in c
    assert "_redaction_base_dir" not in c


def test_sim_check_redaction_runs_live_check(tmp_path: pathlib.Path) -> None:
    evals = _project(tmp_path)
    sim = SimulationEvals.__new__(SimulationEvals)
    sim.app_name = APP
    sim.creds = None
    tc = {
        "name": "pay",
        "redaction_expectations": {"require": ["payment_card"]},
        "_redaction_base_dir": str(evals),
    }
    conv = types.SimpleNamespace(turn_messages=[[{"role": "user"}]])
    with patch(
        "cxas_scrapi.evals.simulation_evals.LiveRedactionCheck"
    ) as check_cls:
        check_cls.return_value.check_capture.return_value.to_dict.return_value = {
            "passed": True
        }
        out = sim._check_redaction(tc, conv, "sess-1")
    assert out == {"passed": True}
    capture = check_cls.return_value.check_capture.call_args.args[0]
    assert capture.session_id == "sess-1"
    assert capture.turn_messages == [[{"role": "user"}]]
    assert check_cls.return_value.check_capture.call_args.kwargs["require"] == [
        "payment_card"
    ]

    assert sim._check_redaction({"name": "x"}, conv, "s") is None
    bad = {"name": "x", "redaction_expectations": "missing.yaml"}
    result = sim._check_redaction(bad, conv, "s")
    assert result["passed"] is False
    assert "not found" in result["error"]


def test_golden_models_accept_redaction_keys() -> None:
    data = Conversations.model_validate(
        {
            "common_redaction_expectations": True,
            "conversations": [
                {
                    "conversation": "g",
                    "turns": [{"user": "hi"}],
                    "redaction_expectations": {"require": ["x"]},
                }
            ],
        }
    )
    assert data.conversations[0].redaction_expectations == {"require": ["x"]}


def test_golden_redaction_expectations(tmp_path: pathlib.Path) -> None:
    evals = _project(tmp_path)
    golden = evals / "goldens.yaml"
    golden.write_text(
        yaml.safe_dump(
            {
                "common_redaction_expectations": True,
                "conversations": [
                    {"conversation": "g1", "turns": []},
                    {
                        "conversation": "g2",
                        "turns": [],
                        "redaction_expectations": False,
                    },
                ],
            }
        )
    )
    spec = golden_redaction_expectations(str(golden))
    assert list(spec) == ["g1"]
    assert spec["g1"]["base_dir"] == str(evals)
    assert golden_redaction_expectations(str(evals / "missing.yaml")) == {}


def test_check_golden_results(tmp_path: pathlib.Path) -> None:
    evals = _project(tmp_path)
    results: list[dict[str, typing.Any]] = [
        {"name": "g1", "passed": True, "conversations": ["c1", "c2"]},
        {"name": "g2", "passed": True, "session_id": "evaluation-9"},
        {"name": "untracked", "passed": True},
    ]
    spec = {"expectations": True, "base_dir": str(evals)}
    report = MagicMock()
    report.to_dict.return_value = {"passed": False, "summary": {"leak": 1}}
    with patch("cxas_scrapi.redaction.replay.RedactionReplay") as replay_cls:
        replay_cls.return_value.replay_conversation_ids.return_value = report
        check_golden_results(APP, results, {"g1": spec, "g2": spec})

    calls = replay_cls.return_value.replay_conversation_ids.call_args_list
    assert calls[0].args[0] == ["c1", "c2"]
    assert calls[1].args[0] == [f"{APP}/conversations/evaluation-9"]
    assert replay_cls.call_count == 1  # one replayer per distinct policy
    assert results[0]["passed"] is False
    assert results[0]["redaction"]["summary"] == {"leak": 1}
    assert "redaction" not in results[2]


def test_check_golden_results_reports_errors(tmp_path: pathlib.Path) -> None:
    evals = _project(tmp_path)
    results = [{"name": "g1", "passed": True}]
    spec = {"expectations": True, "base_dir": str(evals)}
    with patch("cxas_scrapi.redaction.replay.RedactionReplay"):
        check_golden_results(APP, results, {"g1": spec})
    assert results[0]["passed"] is False
    assert "no evaluation conversations" in results[0]["redaction"]["error"]
