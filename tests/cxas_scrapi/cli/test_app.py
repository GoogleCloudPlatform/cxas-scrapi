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

"""Tests for the App Lifecycle CLI Commands."""

import argparse
import io
import os
import zipfile
from unittest import mock

import pytest

from cxas_scrapi.cli import app as cli_app


@pytest.fixture
def mock_apps_client():
    with mock.patch("cxas_scrapi.cli.app.Apps") as mock_apps_class:
        mock_instance = mock_apps_class.return_value
        yield mock_instance


@pytest.fixture
def mock_common_get_project_id():
    with mock.patch(
        "cxas_scrapi.cli.app.Common._get_project_id",
        return_value="dummy-project",
    ) as m:
        yield m


@pytest.fixture
def mock_common_get_location():
    with mock.patch(
        "cxas_scrapi.cli.app.Common._get_location",
        return_value="dummy-location",
    ) as m:
        yield m


def test_app_create(
    mock_apps_client, mock_common_get_project_id, mock_common_get_location
):
    args = argparse.Namespace(
        name="Test App",
        description="A test app",
        app_id=None,
        project_id="test-project",
        location="us",
    )

    mock_app_response = mock.MagicMock()
    mock_app_response.name = "projects/test-project/locations/us/apps/123"
    mock_apps_client.create_app.return_value = mock_app_response

    cli_app.app_create(args)

    mock_apps_client.create_app.assert_called_once_with(
        app_id=None, display_name="Test App", description="A test app"
    )


def test_apps_list(mock_apps_client, capsys):
    args = argparse.Namespace(project_id="test-project", location="us")

    app1 = mock.MagicMock()
    app1.name = "projects/test-project/locations/us/apps/1"
    app1.display_name = "App 1"
    app2 = mock.MagicMock()
    app2.name = "projects/test-project/locations/us/apps/2"
    app2.display_name = "App 2"

    mock_apps_client.list_apps.return_value = [app1, app2]

    # We mock pandas import failure to test the fallback printing.
    # It's cleaner to just let it run.
    cli_app.apps_list(args)

    mock_apps_client.list_apps.assert_called_once()

    captured = capsys.readouterr()
    assert "App 1" in captured.out
    assert "App 2" in captured.out


def test_apps_get(
    mock_apps_client,
    mock_common_get_project_id,
    mock_common_get_location,
    capsys,
):
    args = argparse.Namespace(
        app="projects/test-project/locations/us/apps/123",
        project_id="test-project",
        location="us",
    )

    mock_app = mock.MagicMock()
    mock_app.name = "projects/test-project/locations/us/apps/123"
    mock_app.display_name = "My App"
    mock_app.description = "Test Desc"

    mock_apps_client.get_app.return_value = mock_app

    cli_app.apps_get(args)

    mock_apps_client.get_app.assert_called_once_with(
        app_id="projects/test-project/locations/us/apps/123"
    )
    captured = capsys.readouterr()
    assert "My App" in captured.out
    assert "Test Desc" in captured.out


def test_app_pull(
    mock_apps_client,
    mock_common_get_project_id,
    mock_common_get_location,
    tmp_path,
):
    args = argparse.Namespace(
        app="Test App",
        target_dir=str(tmp_path / "pulled_app"),
        project_id="test-project",
        location="us",
    )

    # Mock resolving display name to resource name
    mock_app = mock.MagicMock()
    mock_app.name = "projects/test-project/locations/us/apps/123"
    mock_apps_client.get_app_by_display_name.return_value = mock_app

    # Create a dummy zip file in memory representing the LRO response
    dummy_zip_io = io.BytesIO()
    with zipfile.ZipFile(dummy_zip_io, "w") as zf:
        zf.writestr("app.yaml", "name: Test App")
    dummy_zip_bytes = dummy_zip_io.getvalue()

    mock_lro = mock.MagicMock()
    mock_response = mock.MagicMock()
    mock_response.app_content = dummy_zip_bytes
    mock_lro.result.return_value = mock_response
    mock_apps_client.export_app.return_value = mock_lro

    cli_app.app_pull(args)

    mock_apps_client.export_app.assert_called_once_with(
        app_name="projects/test-project/locations/us/apps/123"
    )
    assert os.path.exists(os.path.join(args.target_dir, "app.yaml"))


def test_app_push(mock_apps_client, tmp_path):
    args = argparse.Namespace(
        app_dir=str(tmp_path),
        to=None,
        display_name="New App Name",
        project_id="test-project",
        location="us",
    )

    # Create some dummy agent files
    with open(os.path.join(tmp_path, "app.yaml"), "w") as f:
        f.write("name: test")

    mock_result = mock.MagicMock()
    mock_lro = mock.MagicMock()
    mock_imported_app = mock.MagicMock()
    mock_imported_app.name = "projects/test-project/locations/us/apps/new-id"
    mock_lro.result.return_value = mock_imported_app
    mock_result = mock_lro  # import_app returns LRO or App directly.

    mock_apps_client.import_as_new_app.return_value = mock_result

    cli_app.app_push(args)

    mock_apps_client.import_as_new_app.assert_called_once()
    call_args = mock_apps_client.import_as_new_app.call_args[1]
    assert call_args["display_name"] == "New App Name"
    assert "app_content" in call_args


def test_app_branch(
    mock_apps_client, mock_common_get_project_id, mock_common_get_location
):
    args = argparse.Namespace(
        source="projects/test-project/locations/us/apps/source-id",
        new_name="Branched App",
        project_id="test-project",
        location="us",
    )

    # Mock export
    dummy_zip_bytes = b"dummy_zip_data"
    mock_export_lro = mock.MagicMock()
    mock_export_response = mock.MagicMock()
    mock_export_response.app_content = dummy_zip_bytes
    mock_export_lro.result.return_value = mock_export_response
    mock_apps_client.export_app.return_value = mock_export_lro

    # Mock import
    mock_import_lro = mock.MagicMock()
    mock_imported_app = mock.MagicMock()
    mock_imported_app.name = "projects/test-project/locations/us/apps/branch-id"
    mock_import_lro.result.return_value = mock_imported_app
    mock_apps_client.import_as_new_app.return_value = mock_import_lro

    cli_app.app_branch(args)

    mock_apps_client.export_app.assert_called_once_with(
        app_name="projects/test-project/locations/us/apps/source-id"
    )
    mock_apps_client.import_as_new_app.assert_called_once_with(
        app_content=dummy_zip_bytes, display_name="Branched App"
    )


def test_app_delete_by_app_id(
    mock_apps_client, mock_common_get_project_id, mock_common_get_location
):
    args = argparse.Namespace(
        app_name="projects/test-project/locations/us/apps/123",
        display_name=None,
        project_id=None,
        location=None,
        force=True,
    )

    cli_app.app_delete(args)

    mock_apps_client.delete_app.assert_called_once_with(
        app_name="projects/test-project/locations/us/apps/123", force=True
    )


def test_app_delete_by_display_name(mock_apps_client):
    args = argparse.Namespace(
        app_id=None,
        display_name="My App",
        project_id="test-project",
        location="us",
        force=False,
    )

    mock_app = mock.MagicMock()
    mock_app.name = "projects/test-project/locations/us/apps/123"
    mock_apps_client.get_app_by_display_name.return_value = mock_app

    cli_app.app_delete(args)

    mock_apps_client.get_app_by_display_name.assert_called_once_with("My App")
    mock_apps_client.delete_app.assert_called_once_with(
        app_name="projects/test-project/locations/us/apps/123", force=False
    )


def test_app_delete_missing_args(mock_apps_client, capsys):
    args = argparse.Namespace(
        app_id=None,
        display_name=None,
        project_id="test-project",
        location="us",
        force=False,
    )

    with pytest.raises(SystemExit) as excinfo:
        cli_app.app_delete(args)

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "Error: Must provide either --app_name OR" in captured.out


_LINT_RESOURCE_DEFAULTS = dict(
    agent=None,
    tool=None,
    toolset=None,
    guardrail=None,
    evaluation=None,
    evaluation_expectations=None,
)


def _lint_args(tmp_path=None, **overrides):
    """Build an argparse.Namespace with all lint flags defaulted."""
    defaults = dict(
        app_dir=str(tmp_path) if tmp_path else ".",
        list_rules=False,
        json_output=False,
        validate_only=False,
        only=None,
        rule=None,
        fix=False,
        **_LINT_RESOURCE_DEFAULTS,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _make_lint_app(tmp_path, agents=None):
    """Helper to create a minimal app for lint testing."""
    (tmp_path / "app.json").write_text(
        '{"name": "test-app",'
        ' "displayName": "Test App",'
        ' "rootAgent": "root_agent"}'
    )
    (tmp_path / "agents").mkdir()
    for name in agents or ["root_agent"]:
        agent_dir = tmp_path / "agents" / name
        agent_dir.mkdir()
        (agent_dir / "instruction.txt").write_text(
            "<role>test</role>"
            "<persona>test</persona>"
            "<taskflow><subtask name='main'>"
            "<step>do it</step>"
            "</subtask></taskflow>"
        )
        (agent_dir / f"{name}.json").write_text(
            f'{{"displayName": "{name}", "tools": ["end_session"]}}'
        )


def test_app_lint_list_rules(capsys):
    args = _lint_args(list_rules=True)

    with pytest.raises(SystemExit) as excinfo:
        cli_app.app_lint(args)

    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "I001" in captured.out
    assert "V001" in captured.out
    assert "Available Rules" in captured.out


def test_app_lint_no_app_found(capsys, tmp_path):
    args = _lint_args(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        cli_app.app_lint(args)

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "ERROR: No app directory found" in captured.out


def test_app_lint_clean_app(capsys, tmp_path):
    _make_lint_app(tmp_path)
    args = _lint_args(tmp_path)

    with mock.patch(
        "cxas_scrapi.utils.lint_rules.schema.json_format.ParseDict"
    ):
        with pytest.raises(SystemExit) as excinfo:
            cli_app.app_lint(args)

    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "Lint PASSED" in captured.out


def test_app_lint_with_errors(capsys, tmp_path):
    (tmp_path / "app.json").write_text(
        '{"name": "test", "displayName": "Test", "rootAgent": "root_agent"}'
    )
    (tmp_path / "agents").mkdir()
    agent_dir = tmp_path / "agents" / "root_agent"
    agent_dir.mkdir()
    (agent_dir / "instruction.txt").write_text("No XML tags here.")
    (agent_dir / "root_agent.json").write_text(
        '{"displayName": "root_agent", "tools": ["end_session"]}'
    )

    args = _lint_args(tmp_path)

    with mock.patch(
        "cxas_scrapi.utils.lint_rules.schema.json_format.ParseDict"
    ):
        with pytest.raises(SystemExit) as excinfo:
            cli_app.app_lint(args)

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "Lint FAILED" in captured.out


def test_app_lint_json_output(capsys, tmp_path):
    _make_lint_app(tmp_path)
    args = _lint_args(tmp_path, json_output=True)

    with mock.patch(
        "cxas_scrapi.utils.lint_rules.schema.json_format.ParseDict"
    ):
        with pytest.raises(SystemExit) as excinfo:
            cli_app.app_lint(args)

    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    import json  # noqa: PLC0415,I001

    parsed = json.loads(captured.out)
    assert isinstance(parsed, list)


def test_app_lint_validate_only(capsys, tmp_path):
    _make_lint_app(tmp_path)
    args = _lint_args(tmp_path, json_output=True, validate_only=True)

    with mock.patch(
        "cxas_scrapi.utils.lint_rules.schema.json_format.ParseDict"
    ):
        with pytest.raises(SystemExit) as excinfo:
            cli_app.app_lint(args)

    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    import json  # noqa: PLC0415,I001

    results = json.loads(captured.out)
    valid_prefixes = ("A", "S", "V")
    for r in results:
        assert any(r["rule_id"].startswith(p) for p in valid_prefixes), (
            f"Expected config/structure/schema rule, got {r['rule_id']}"
        )


def test_app_lint_only_filter(capsys, tmp_path):
    _make_lint_app(tmp_path)
    args = _lint_args(tmp_path, json_output=True, only="config")

    with mock.patch(
        "cxas_scrapi.utils.lint_rules.schema.json_format.ParseDict"
    ):
        with pytest.raises(SystemExit) as excinfo:
            cli_app.app_lint(args)

    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    import json  # noqa: PLC0415,I001

    results = json.loads(captured.out)
    for r in results:
        assert r["rule_id"].startswith("A"), (
            f"Expected only A-rules with --only config, got {r['rule_id']}"
        )


def test_app_lint_rule_filter(capsys, tmp_path):
    _make_lint_app(tmp_path)
    args = _lint_args(tmp_path, json_output=True, rule="I001")

    with mock.patch(
        "cxas_scrapi.utils.lint_rules.schema.json_format.ParseDict"
    ):
        with pytest.raises(SystemExit) as excinfo:
            cli_app.app_lint(args)

    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    import json  # noqa: PLC0415,I001

    results = json.loads(captured.out)
    for r in results:
        assert r["rule_id"] == "I001", (
            f"Expected only I001 with --rule I001, got {r['rule_id']}"
        )
