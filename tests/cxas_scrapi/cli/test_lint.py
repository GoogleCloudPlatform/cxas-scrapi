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

"""Tests for CLI lint command with Guided Agent rules (Rule A011)."""

import argparse
import json
import typing
from pathlib import Path
from unittest import mock

import pytest

from cxas_scrapi.cli import app as cli_app


def _lint_args(
    app_dir: typing.Any = None,
    evals_dir: str = "evals/",
    json_output: bool = False,
    show_fixes: bool = False,
    only: typing.Any = None,
    rule: typing.Any = None,
    agents: typing.Any = None,
    tools: typing.Any = None,
    list_rules: bool = False,
    validate_only: bool = False,
    agent: typing.Any = None,
    tool: typing.Any = None,
    toolset: typing.Any = None,
    guardrail: typing.Any = None,
    evaluation: typing.Any = None,
    evaluation_expectations: typing.Any = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        app_dir=str(app_dir) if app_dir else None,
        evals_dir=evals_dir,
        json_output=json_output,
        show_fixes=show_fixes,
        only=only,
        rule=rule,
        agents=agents,
        tools=tools,
        list_rules=list_rules,
        validate_only=validate_only,
        agent=agent,
        tool=tool,
        toolset=toolset,
        guardrail=guardrail,
        evaluation=evaluation,
        evaluation_expectations=evaluation_expectations,
    )


def _make_guided_app(
    tmp_path: Path,
    synced: bool = True,
    missing_yaml: bool = False,
    has_instruction_field: bool = False,
) -> None:
    """Helper to create an app with a root Guided Agent."""
    (tmp_path / "app.json").write_text(
        json.dumps(
            {
                "name": "guided_test_app",
                "displayName": "Guided Test App",
                "rootAgent": "root_agent",
            }
        )
    )

    agent_dir = tmp_path / "agents" / "root_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)

    yaml_content = "tools:\n  - end_session\nrules:\n  - Greet user\n"
    if not missing_yaml:
        if not synced:
            (agent_dir / "definition.yaml").write_text(
                "tools:\n  - end_session\n"
                "rules:\n  - Greet user\n  - Extra uncommitted rule\n"
            )
        else:
            (agent_dir / "definition.yaml").write_text(yaml_content)

    agent_data: dict[str, typing.Any] = {
        "displayName": "root_agent",
        "tools": ["end_session"],
        "guidedAgent": {
            "configSource": {
                "format": "YAML",
                "inlineText": yaml_content,
            }
        },
    }
    if has_instruction_field:
        agent_data["instruction"] = "Forbidden raw instruction string"

    (agent_dir / "root_agent.json").write_text(json.dumps(agent_data, indent=2))


def test_cli_lint_guided_agent_synced(
    capsys: typing.Any, tmp_path: Path
) -> None:
    """Test cxas lint succeeds on an app with a synchronized Guided Agent."""
    _make_guided_app(tmp_path, synced=True)
    args = _lint_args(tmp_path, json_output=True, only="config")

    with (
        mock.patch("cxas_scrapi.utils.lint_rules.schema.json_format.ParseDict"),
        pytest.raises(SystemExit) as excinfo,
    ):
        cli_app.app_lint(args)

    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    results = json.loads(captured.out)
    # Check no A010 errors reported
    a011_errors = [r for r in results if r["rule_id"] == "A011"]
    assert len(a011_errors) == 0


def test_cli_lint_guided_agent_drift_fails(
    capsys: typing.Any, tmp_path: Path
) -> None:
    """Test cxas lint fails on an out-of-sync Guided Agent."""
    _make_guided_app(tmp_path, synced=False)
    args = _lint_args(tmp_path, json_output=True, only="config")

    with (
        mock.patch("cxas_scrapi.utils.lint_rules.schema.json_format.ParseDict"),
        pytest.raises(SystemExit) as excinfo,
    ):
        cli_app.app_lint(args)

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    results = json.loads(captured.out)
    a011_errors = [r for r in results if r["rule_id"] == "A011"]
    assert len(a011_errors) == 1
    assert "out of sync" in a011_errors[0]["message"]
    assert "Extra uncommitted rule" in a011_errors[0]["message"]
    assert "cxas agent sync-yaml" in a011_errors[0]["fix_suggestion"]


def test_cli_lint_guided_agent_missing_yaml_fails(
    capsys: typing.Any, tmp_path: Path
) -> None:
    """Test cxas lint fails with A010 when definition.yaml is missing."""
    _make_guided_app(tmp_path, missing_yaml=True)
    args = _lint_args(tmp_path, json_output=True, only="config")

    with (
        mock.patch("cxas_scrapi.utils.lint_rules.schema.json_format.ParseDict"),
        pytest.raises(SystemExit) as excinfo,
    ):
        cli_app.app_lint(args)

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    results = json.loads(captured.out)
    a011_errors = [r for r in results if r["rule_id"] == "A011"]
    assert len(a011_errors) == 1
    assert "missing definition.yaml" in a011_errors[0]["message"].lower()
    assert "cxas agent sync-yaml --to-yaml" in a011_errors[0]["fix_suggestion"]


def test_cli_lint_filter_rule_a011(capsys: typing.Any, tmp_path: Path) -> None:
    """Test cxas lint with --rule A011 filters execution to only A010."""
    _make_guided_app(tmp_path, synced=False)
    args = _lint_args(tmp_path, json_output=True, rule="A011")

    with (
        mock.patch("cxas_scrapi.utils.lint_rules.schema.json_format.ParseDict"),
        pytest.raises(SystemExit) as excinfo,
    ):
        cli_app.app_lint(args)

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    results = json.loads(captured.out)
    assert len(results) >= 1
    for r in results:
        assert r["rule_id"] == "A011"
