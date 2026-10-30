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

"""Unit tests for simulation / CXAS 2.0 Milestone eval lint rule E013."""

from pathlib import Path

import yaml

from cxas_scrapi.utils.lint_rules.evals import SimulationEvalStructure
from cxas_scrapi.utils.linter import LintContext, Severity


def _make_context(project_root: Path) -> LintContext:
    return LintContext(
        project_root=project_root,
        app_dir=project_root,
        evals_dir=project_root / "evals",
    )


def test_e013_valid_milestone_eval_passes(tmp_path: Path) -> None:
    """Valid CXAS 2.0 Milestone and SimulationEvals YAML passes with 0 findings."""
    sim_path = tmp_path / "evals" / "simulations" / "account_sim.yaml"
    sim_path.parent.mkdir(parents=True)
    content = yaml.safe_dump(
        {
            "common_session_parameters": {"env": "prod"},
            "common_expectations": ["The agent is polite."],
            "evals": [
                {
                    "name": "verify_balance",
                    "tags": ["P0", "billing"],
                    "session_parameters": {"account_id": "12345"},
                    "expectations": ["Agent provides balance."],
                    "max_turns": 8,
                    "steps": [
                        {
                            "goal": "Authenticate the user",
                            "success_criteria": "User is authenticated",
                            "inject_variables": {"auth_status": "verified"},
                        },
                        {
                            "goal": "Ask for balance",
                            "success_criteria": "Balance is stated",
                            "response_guide": "Ask clearly",
                        },
                    ],
                }
            ],
        }
    )
    sim_path.write_text(content)

    results = SimulationEvalStructure().check(
        sim_path, content, _make_context(tmp_path)
    )
    assert results == []


def test_e013_missing_steps_and_empty_step_flagged(tmp_path: Path) -> None:
    """E013 flags missing/empty steps list and steps without goal/utterance/event."""
    sim_path = tmp_path / "evals" / "simulations" / "broken_sim.yaml"
    sim_path.parent.mkdir(parents=True)
    content = yaml.safe_dump(
        {
            "evals": [
                {"name": "no_steps_case", "tags": ["P0"]},
                {
                    "name": "empty_step_case",
                    "tags": ["P0"],
                    "steps": [{"max_turns": 3}],
                },
            ]
        }
    )
    sim_path.write_text(content)

    results = SimulationEvalStructure().check(
        sim_path, content, _make_context(tmp_path)
    )
    errors = [r for r in results if r.severity == Severity.ERROR]
    assert len(errors) == 2
    assert any("no_steps_case" in r.message for r in errors)
    assert any("empty_step_case" in r.message for r in errors)


def test_e013_late_inject_variables_and_invalid_event_dict(
    tmp_path: Path,
) -> None:
    """E013 flags inject_variables on step > 1 and event dict missing event/name."""
    sim_path = tmp_path / "evals" / "simulations" / "late_inject.yaml"
    sim_path.parent.mkdir(parents=True)
    content = yaml.safe_dump(
        {
            "evals": [
                {
                    "name": "late_inject_case",
                    "tags": ["P0"],
                    "steps": [
                        {"event": {"invalid_key": "welcome"}},
                        {
                            "goal": "Second step",
                            "success_criteria": "Done",
                            "inject_variables": {"foo": "bar"},
                        },
                    ],
                }
            ]
        }
    )
    sim_path.write_text(content)

    results = SimulationEvalStructure().check(
        sim_path, content, _make_context(tmp_path)
    )
    errors = [r for r in results if r.severity == Severity.ERROR]
    assert len(errors) == 2
    assert any("'event' mapping must specify" in r.message for r in errors)
    assert any("only supported on the first step" in r.message for r in errors)


def test_e013_unknown_keys_emit_warnings_with_suggestions(
    tmp_path: Path,
) -> None:
    """E013 emits WARNING with typo suggestion for unknown eval/step keys."""
    sim_path = tmp_path / "evals" / "simulations" / "typo_sim.yaml"
    sim_path.parent.mkdir(parents=True)
    content = yaml.safe_dump(
        {
            "evals": [
                {
                    "name": "typo_case",
                    "tags": ["P0"],
                    "session_parameter": {"x": 1},
                    "steps": [
                        {
                            "goal": "Greet",
                            "sucess_criteria": "Greeted",
                        }
                    ],
                }
            ]
        }
    )
    sim_path.write_text(content)

    results = SimulationEvalStructure().check(
        sim_path, content, _make_context(tmp_path)
    )
    warnings = [r for r in results if r.severity == Severity.WARNING]
    assert len(warnings) == 2
    assert any("session_parameter" in r.message for r in warnings)
    assert any("sucess_criteria" in r.message for r in warnings)
    assert any("success_criteria" in r.fix_suggestion for r in warnings)
