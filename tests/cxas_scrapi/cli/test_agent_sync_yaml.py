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

"""Tests for the cxas agent sync-yaml CLI subcommand and pull/push workflows."""

from __future__ import annotations

import argparse
import io
import json
import zipfile
from typing import TYPE_CHECKING
from unittest import mock

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from cxas_scrapi.cli.agent import agent_sync_yaml
from cxas_scrapi.cli.app import _app_pull, _app_push
from cxas_scrapi.cli.main import get_parser

SAMPLE_YAML = """\
instruction: |
  You are an appointment booking specialist.
tools:
  check_availability:
    external_tool: check_availability
  book_slot:
    external_tool: book_slot
"""

SAMPLE_GUIDED_AGENT_JSON = {
    "displayName": "appointment_agent",
    "guidedAgent": {
        "configSource": {
            "format": "YAML",
            "inlineText": SAMPLE_YAML,
        }
    },
    "tools": ["book_slot", "check_availability"],
}

SAMPLE_NON_GUIDED_AGENT_JSON = {
    "displayName": "standard_agent",
    "instruction": "You are a general agent.",
    "tools": ["general_tool"],
}


def test_parser_registration() -> None:
    """Verify cxas agent sync-yaml subcommand parser registers all arguments."""
    parser = get_parser()
    args = parser.parse_args(
        [
            "agent",
            "sync-yaml",
            "--agent-dir",
            "/tmp/agent",
            "--dir",
            "/tmp/app",
            "--to-json",
            "--check",
        ]
    )
    assert args.command == "agent"
    assert args.agent_command == "sync-yaml"
    assert args.agent_dir == "/tmp/agent"
    assert args.dir == "/tmp/app"
    assert args.to_json is True
    assert args.to_yaml is False
    assert args.check is True


def test_app_pull_extracts_definition_yaml(tmp_path: Path) -> None:
    """_app_pull extracts definition.yaml for guided agents in export zip."""
    dest_dir = tmp_path / "pulled_app"

    dummy_zip_io = io.BytesIO()
    with zipfile.ZipFile(dummy_zip_io, "w") as zf:
        zf.writestr("app.yaml", "name: TestApp\n")
        zf.writestr(
            "agents/charter_agent/agent.json",
            json.dumps(SAMPLE_GUIDED_AGENT_JSON, indent=2),
        )
        zf.writestr(
            "agents/standard_agent/agent.json",
            json.dumps(SAMPLE_NON_GUIDED_AGENT_JSON, indent=2),
        )

    mock_client = mock.MagicMock()
    mock_lro = mock.MagicMock()
    mock_response = mock.MagicMock()
    mock_response.app_content = dummy_zip_io.getvalue()
    mock_lro.result.return_value = mock_response
    mock_client.export_app.return_value = mock_lro

    _app_pull(mock_client, "test-app", str(dest_dir))

    # Verify definition.yaml extracted for guided agent
    guided_yaml = dest_dir / "agents" / "charter_agent" / "definition.yaml"
    assert guided_yaml.is_file()
    assert guided_yaml.read_text(encoding="utf-8") == SAMPLE_YAML

    # Verify non-guided agent was skipped
    standard_yaml = dest_dir / "agents" / "standard_agent" / "definition.yaml"
    assert not standard_yaml.exists()


def test_app_push_compiles_definition_yaml_before_upload(
    tmp_path: Path,
) -> None:
    """_app_push compiles definition.yaml into agent.json before upload."""
    app_dir = tmp_path / "push_app"
    agent_dir = app_dir / "agents" / "guided_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)

    # Put definition.yaml with 2 tools and instruction
    (agent_dir / "definition.yaml").write_text(SAMPLE_YAML, encoding="utf-8")

    # Put agent.json that has outdated content and a forbidden instruction field
    outdated_json = {
        "displayName": "guided_agent",
        "instruction": "forbidden instruction",
        "tools": ["outdated_tool"],
    }
    (agent_dir / "agent.json").write_text(
        json.dumps(outdated_json, indent=2), encoding="utf-8"
    )

    mock_client = mock.MagicMock()
    mock_lro = mock.MagicMock()
    mock_imported = mock.MagicMock()
    mock_imported.name = "projects/p/locations/l/apps/new"
    mock_lro.result.return_value = mock_imported
    mock_client.import_as_new_app.return_value = mock_lro

    _app_push(str(app_dir), apps_client=mock_client, display_name="Test Push")

    mock_client.import_as_new_app.assert_called_once()
    uploaded_bytes = mock_client.import_as_new_app.call_args[1]["app_content"]

    with zipfile.ZipFile(io.BytesIO(uploaded_bytes)) as zf:
        namelist = zf.namelist()
        matching = [name for name in namelist if name.endswith("agent.json")]
        assert len(matching) >= 1
        staged_json_bytes = zf.read(matching[0])
        staged_json = json.loads(staged_json_bytes.decode("utf-8"))

    # Assert compiled properties
    assert "instruction" not in staged_json
    assert staged_json["guidedAgent"]["configSource"]["format"] == "YAML"
    assert (
        staged_json["guidedAgent"]["configSource"]["inlineText"] == SAMPLE_YAML
    )
    assert staged_json["tools"] == ["book_slot", "check_availability"]


def test_agent_sync_yaml_app_dir_multiple_agents(tmp_path: Path) -> None:
    """sync-yaml processes guided agents and skips non-guided agents."""
    app_dir = tmp_path / "app"
    ga1_dir = app_dir / "agents" / "ga1"
    ga2_dir = app_dir / "agents" / "ga2"
    std_dir = app_dir / "agents" / "std"

    ga1_dir.mkdir(parents=True, exist_ok=True)
    ga2_dir.mkdir(parents=True, exist_ok=True)
    std_dir.mkdir(parents=True, exist_ok=True)

    # ga1 has definition.yaml -> should compile agent.json
    (ga1_dir / "definition.yaml").write_text(SAMPLE_YAML, encoding="utf-8")

    # ga2 has agent.json with guidedAgent -> should extract definition.yaml
    (ga2_dir / "agent.json").write_text(
        json.dumps(SAMPLE_GUIDED_AGENT_JSON, indent=2), encoding="utf-8"
    )

    # std has standard agent.json -> should be skipped
    (std_dir / "agent.json").write_text(
        json.dumps(SAMPLE_NON_GUIDED_AGENT_JSON, indent=2), encoding="utf-8"
    )

    args = argparse.Namespace(
        agent_dir=None,
        dir=str(app_dir),
        to_json=False,
        to_yaml=False,
        check=False,
    )
    agent_sync_yaml(args)

    # ga1 agent.json compiled
    assert (ga1_dir / "agent.json").is_file()
    ga1_data = json.loads((ga1_dir / "agent.json").read_text(encoding="utf-8"))
    assert ga1_data["tools"] == ["book_slot", "check_availability"]
    assert "instruction" not in ga1_data

    # ga2 definition.yaml extracted
    assert (ga2_dir / "definition.yaml").is_file()
    assert (ga2_dir / "definition.yaml").read_text(
        encoding="utf-8"
    ) == SAMPLE_YAML

    # std definition.yaml not created
    assert not (std_dir / "definition.yaml").exists()


def test_agent_sync_yaml_single_agent_dir(tmp_path: Path) -> None:
    """sync-yaml targets a single agent directory when --agent-dir is given."""
    agent_dir = tmp_path / "my_single_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "definition.yaml").write_text(SAMPLE_YAML, encoding="utf-8")

    args = argparse.Namespace(
        agent_dir=str(agent_dir),
        dir=".",
        to_json=False,
        to_yaml=False,
        check=False,
    )
    agent_sync_yaml(args)

    assert (agent_dir / "agent.json").is_file()
    data = json.loads((agent_dir / "agent.json").read_text(encoding="utf-8"))
    assert data["tools"] == ["book_slot", "check_availability"]


def test_agent_sync_yaml_check_in_sync(tmp_path: Path) -> None:
    """sync-yaml --check exits cleanly (0) when definitions are synchronized."""
    agent_dir = tmp_path / "synced_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "definition.yaml").write_text(SAMPLE_YAML, encoding="utf-8")
    (agent_dir / "agent.json").write_text(
        json.dumps(SAMPLE_GUIDED_AGENT_JSON, indent=2), encoding="utf-8"
    )

    args = argparse.Namespace(
        agent_dir=str(agent_dir),
        dir=".",
        to_json=False,
        to_yaml=False,
        check=True,
    )
    # Should not raise SystemExit
    agent_sync_yaml(args)


def test_agent_sync_yaml_check_drift_detected(tmp_path: Path) -> None:
    """sync-yaml --check exits with code 1 when drift is detected."""
    agent_dir = tmp_path / "drift_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "definition.yaml").write_text(
        SAMPLE_YAML + "# local change\n", encoding="utf-8"
    )
    (agent_dir / "agent.json").write_text(
        json.dumps(SAMPLE_GUIDED_AGENT_JSON, indent=2), encoding="utf-8"
    )

    args = argparse.Namespace(
        agent_dir=str(agent_dir),
        dir=".",
        to_json=False,
        to_yaml=False,
        check=True,
    )
    with pytest.raises(SystemExit) as exc_info:
        agent_sync_yaml(args)
    assert exc_info.value.code == 1


def test_agent_sync_yaml_to_json_flag(tmp_path: Path) -> None:
    """sync-yaml --to-json compiles definition.yaml into agent.json."""
    agent_dir = tmp_path / "agent_to_json"
    agent_dir.mkdir(parents=True, exist_ok=True)
    custom_yaml = "tools:\n  - custom_tool\n"
    (agent_dir / "definition.yaml").write_text(custom_yaml, encoding="utf-8")

    args = argparse.Namespace(
        agent_dir=str(agent_dir),
        dir=".",
        to_json=True,
        to_yaml=False,
        check=False,
    )
    agent_sync_yaml(args)

    assert (agent_dir / "agent.json").is_file()
    data = json.loads((agent_dir / "agent.json").read_text(encoding="utf-8"))
    assert data["tools"] == ["custom_tool"]
    assert data["guidedAgent"]["configSource"]["inlineText"] == custom_yaml


def test_agent_sync_yaml_to_yaml_flag(tmp_path: Path) -> None:
    """sync-yaml --to-yaml extracts agent.json into definition.yaml."""
    agent_dir = tmp_path / "agent_to_yaml"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "agent.json").write_text(
        json.dumps(SAMPLE_GUIDED_AGENT_JSON, indent=2), encoding="utf-8"
    )

    args = argparse.Namespace(
        agent_dir=str(agent_dir),
        dir=".",
        to_json=False,
        to_yaml=True,
        check=False,
    )
    agent_sync_yaml(args)

    assert (agent_dir / "definition.yaml").is_file()
    assert (agent_dir / "definition.yaml").read_text(
        encoding="utf-8"
    ) == SAMPLE_YAML


def test_agent_sync_yaml_conflicting_flags() -> None:
    """sync-yaml fails with code 1 if conflicting flags are passed."""
    args_both = argparse.Namespace(
        agent_dir=None,
        dir=".",
        to_json=True,
        to_yaml=True,
        check=False,
    )
    with pytest.raises(SystemExit) as exc1:
        agent_sync_yaml(args_both)
    assert exc1.value.code == 1

    args_check_json = argparse.Namespace(
        agent_dir=None,
        dir=".",
        to_json=True,
        to_yaml=False,
        check=True,
    )
    with pytest.raises(SystemExit) as exc2:
        agent_sync_yaml(args_check_json)
    assert exc2.value.code == 1
