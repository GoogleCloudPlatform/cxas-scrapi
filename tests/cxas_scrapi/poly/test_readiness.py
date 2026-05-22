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

"""Tests for polymorphic project readiness reports."""

import shutil
from pathlib import Path

from cxas_scrapi.poly.engine import PolymorphismEngine
from cxas_scrapi.poly.readiness import build_readiness_report


def test_readiness_report_marks_clean_channel_ready(base_dir: Path):
    engine = PolymorphismEngine(str(base_dir))
    engine.load_base_project()
    engine.load_adapter_cards()

    report = build_readiness_report(engine, str(base_dir))

    chat = next(c for c in report["channels"] if c["channel"] == "chat")
    assert report["schema_version"] == "poly-readiness/v1"
    assert chat["status"] == "ready"
    assert chat["compiled"] is True
    assert chat["diff_summary"]["tools_added"] == 1
    assert chat["eval_coverage"]["evaluations"]["channel_count"] == 1
    assert chat["next_steps"] == [
        "Run cxas poly build, lint the compiled output, and run channel evals."
    ]


def test_readiness_report_flags_adapter_without_channel_evals(base_dir: Path):
    engine = PolymorphismEngine(str(base_dir))
    engine.load_base_project()
    engine.load_adapter_cards()

    report = build_readiness_report(engine, str(base_dir))

    voice = next(c for c in report["channels"] if c["channel"] == "voice")
    assert voice["status"] == "attention"
    assert any(issue["rule_id"] == "AD006" for issue in voice["issues"])
    assert any(
        "channel-specific evaluations" in step
        for step in voice["next_steps"]
    )


def test_readiness_report_warns_when_channel_eval_reuses_base_name(
    copied_base: Path,
):
    base_eval = copied_base / "evaluations" / "Chat_Test_Eval"
    base_eval.mkdir(parents=True)
    shutil.copy2(
        copied_base
        / "adapters"
        / "chat_evals"
        / "Chat_Test_Eval"
        / "Chat_Test_Eval.yaml",
        base_eval / "Chat_Test_Eval.yaml",
    )
    engine = PolymorphismEngine(str(copied_base))
    engine.load_base_project()
    engine.load_adapter_cards()

    report = build_readiness_report(engine, str(copied_base))

    chat = next(c for c in report["channels"] if c["channel"] == "chat")
    assert chat["status"] == "attention"
    assert chat["eval_coverage"]["evaluations"]["duplicate_names"] == [
        "Chat_Test_Eval"
    ]
    assert any(
        "duplicate evaluation names" in step for step in chat["next_steps"]
    )
