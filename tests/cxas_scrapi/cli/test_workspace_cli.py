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

"""Tests for the Workspace CLI subcommands."""

import argparse
from unittest import mock

import pytest
import toml

from cxas_scrapi.cli import workspace as cli_ws


@pytest.fixture(autouse=True)
def clear_workspace_cache():
    from cxas_scrapi import workspace as ws

    ws._workspace_config_cache = None
    ws._project_dir = None
    ws._active_project_cache = None
    yield


class TestWorkspaceSet:
    def test_workspace_set_target_dir_success(self, tmp_path):
        # Setup directories
        (tmp_path / ".agents").mkdir()
        project_dir = tmp_path / "my_project"
        project_dir.mkdir()
        (project_dir / "gecx-config.json").touch()

        args = argparse.Namespace(
            target_dir=str(project_dir),
            gcp_project_id=None,
            deployed_app_id=None,
            location=None,
            app_dir=None,
            evals_dir=None,
            output_dir=None,
            model=None,
            modality=None,
        )

        with (
            mock.patch(
                "cxas_scrapi.workspace.find_workspace_root",
                return_value=str(tmp_path),
            ),
            mock.patch("pathlib.Path.cwd", return_value=tmp_path),
        ):
            cli_ws.workspace_set(args)

        # Verify pointer file was created at .scrapi/active-project
        pointer_file = tmp_path / ".scrapi" / "active-project"
        assert pointer_file.exists()
        data = toml.loads(pointer_file.read_text())
        assert data["base-dir"] == str(project_dir.resolve())

    def test_workspace_set_missing_config_exits(self, tmp_path, capsys):
        (tmp_path / ".agents").mkdir()
        project_dir = tmp_path / "my_project"
        project_dir.mkdir()  # no gecx-config.json

        args = argparse.Namespace(
            target_dir=str(project_dir),
            gcp_project_id=None,
            deployed_app_id=None,
            location=None,
            app_dir=None,
            evals_dir=None,
            output_dir=None,
            model=None,
            modality=None,
        )

        with (
            mock.patch(
                "cxas_scrapi.workspace.find_workspace_root",
                return_value=str(tmp_path),
            ),
            mock.patch("pathlib.Path.cwd", return_value=tmp_path),
        ):
            with pytest.raises(SystemExit) as excinfo:
                cli_ws.workspace_set(args)

        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert (
            "does not contain a gecx-config.toml or gecx-config.json file"
            in captured.out
        )
        assert "cxas init --target-dir=" in captured.out

    def test_workspace_set_update_config_success(self, tmp_path):
        (tmp_path / ".agents").mkdir()
        project_dir = tmp_path / "my_project"
        project_dir.mkdir()
        (project_dir / "gecx-config.json").write_text("{}")

        args = argparse.Namespace(
            target_dir=None,
            gcp_project_id="new-project",
            deployed_app_id="new-app",
            location="europe-west1",
            app_dir=None,
            evals_dir=None,
            output_dir=None,
            model=None,
            modality=None,
        )

        with (
            mock.patch(
                "cxas_scrapi.workspace.resolve_project_dir",
                return_value=str(project_dir),
            ),
            mock.patch(
                "cxas_scrapi.workspace.find_workspace_root",
                return_value=str(tmp_path),
            ),
        ):
            cli_ws.workspace_set(args)

        # Verify config file got updated
        toml_path = project_dir / "gecx-config.toml"
        assert toml_path.exists()
        config = toml.loads(toml_path.read_text())
        assert config["default"]["gcp-project-id"] == "new-project"
        assert config["default"]["deployed-app-id"] == "new-app"
        assert config["default"]["location"] == "europe-west1"
        assert not (project_dir / "gecx-config.json").exists()

    def test_workspace_set_non_existent_target_dir_exits(
        self, tmp_path, capsys
    ):
        args = argparse.Namespace(
            target_dir="does-not-exist",
            gcp_project_id=None,
            deployed_app_id=None,
            location=None,
            app_dir=None,
            evals_dir=None,
            output_dir=None,
            model=None,
            modality=None,
        )

        with mock.patch("pathlib.Path.cwd", return_value=tmp_path):
            with pytest.raises(SystemExit) as excinfo:
                cli_ws.workspace_set(args)

        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "does not exist" in captured.out

    def test_workspace_set_missing_workspace_root_exits(self, tmp_path, capsys):
        (tmp_path / ".agents").mkdir()
        project_dir = tmp_path / "my_project"
        project_dir.mkdir()
        (project_dir / "gecx-config.json").touch()

        args = argparse.Namespace(
            target_dir=str(project_dir),
            gcp_project_id=None,
            deployed_app_id=None,
            location=None,
            app_dir=None,
            evals_dir=None,
            output_dir=None,
            model=None,
            modality=None,
        )

        with (
            mock.patch(
                "cxas_scrapi.workspace.find_workspace_root",
                return_value=None,
            ),
            mock.patch("pathlib.Path.cwd", return_value=tmp_path),
        ):
            with pytest.raises(SystemExit) as excinfo:
                cli_ws.workspace_set(args)

        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "Could not find SCRAPI workspace root" in captured.out

    def test_workspace_set_target_dir_outside_workspace_exits(
        self, tmp_path, capsys
    ):
        workspace_root = tmp_path / "ws_root"
        workspace_root.mkdir()
        (workspace_root / ".agents").mkdir()

        outside_project_dir = tmp_path / "outside_project"
        outside_project_dir.mkdir()
        (outside_project_dir / "gecx-config.json").touch()

        args = argparse.Namespace(
            target_dir=str(outside_project_dir),
            gcp_project_id=None,
            deployed_app_id=None,
            location=None,
            app_dir=None,
            evals_dir=None,
            output_dir=None,
            model=None,
            modality=None,
        )

        with (
            mock.patch(
                "cxas_scrapi.workspace.find_workspace_root",
                return_value=str(workspace_root),
            ),
            mock.patch("pathlib.Path.cwd", return_value=workspace_root),
        ):
            with pytest.raises(SystemExit) as excinfo:
                cli_ws.workspace_set(args)

        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "must be inside the workspace" in captured.out

    def test_workspace_set_no_path_no_updates_provided_exits(self, capsys):
        args = argparse.Namespace(
            target_dir=None,
            gcp_project_id=None,
            deployed_app_id=None,
            location=None,
            app_dir=None,
            evals_dir=None,
            output_dir=None,
            model=None,
            modality=None,
        )

        with pytest.raises(SystemExit) as excinfo:
            cli_ws.workspace_set(args)

        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "No path or update flags provided" in captured.out

    def test_workspace_set_no_updates_prints(self, tmp_path, capsys):
        (tmp_path / ".agents").mkdir()
        project_dir = tmp_path / "my_project"
        project_dir.mkdir()
        (project_dir / "gecx-config.json").touch()

        args = argparse.Namespace(
            target_dir=None,
            gcp_project_id=None,
            deployed_app_id=None,
            location=None,
            app_dir=None,
            evals_dir=None,
            output_dir=None,
            model=None,
            modality=None,
        )

        with (
            mock.patch(
                "cxas_scrapi.workspace.resolve_project_dir",
                return_value=str(project_dir),
            ),
            mock.patch(
                "cxas_scrapi.workspace.find_workspace_root",
                return_value=str(tmp_path),
            ),
            mock.patch(
                "cxas_scrapi.workspace.update_workspace_config",
                return_value=(False, str(project_dir / "gecx-config.json")),
            ),
        ):
            # We must pass at least one update flag to trigger the update branch
            args.location = "us"  # but it evaluates to no changes
            cli_ws.workspace_set(args)

        captured = capsys.readouterr()
        assert "No updates provided." in captured.out

    def test_workspace_set_update_config_error_exits(self, tmp_path, capsys):
        (tmp_path / ".agents").mkdir()
        project_dir = tmp_path / "my_project"
        project_dir.mkdir()
        (project_dir / "gecx-config.json").touch()

        args = argparse.Namespace(
            target_dir=None,
            gcp_project_id="new-project",
            deployed_app_id=None,
            location=None,
            app_dir=None,
            evals_dir=None,
            output_dir=None,
            model=None,
            modality=None,
        )

        with (
            mock.patch(
                "cxas_scrapi.workspace.resolve_project_dir",
                return_value=str(project_dir),
            ),
            mock.patch(
                "cxas_scrapi.workspace.find_workspace_root",
                return_value=str(tmp_path),
            ),
            mock.patch(
                "cxas_scrapi.workspace.update_workspace_config",
                side_effect=FileNotFoundError("Config not found"),
            ),
        ):
            with pytest.raises(SystemExit) as excinfo:
                cli_ws.workspace_set(args)

        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "Error: Config not found" in captured.out

    def test_workspace_set_relative_to_cwd_success(self, tmp_path):
        (tmp_path / ".agents").mkdir()
        project_dir = tmp_path / "my_project"
        project_dir.mkdir()
        (project_dir / "gecx-config.json").touch()

        args = argparse.Namespace(
            target_dir="my_project",
            gcp_project_id=None,
            deployed_app_id=None,
            location=None,
            app_dir=None,
            evals_dir=None,
            output_dir=None,
            model=None,
            modality=None,
        )

        with (
            mock.patch("pathlib.Path.cwd", return_value=tmp_path),
            mock.patch(
                "cxas_scrapi.workspace.find_workspace_root",
                return_value=str(tmp_path),
            ),
        ):
            cli_ws.workspace_set(args)

        pointer_file = tmp_path / ".scrapi" / "active-project"
        assert pointer_file.exists()
        data = toml.loads(pointer_file.read_text())
        assert data["base-dir"] == str(project_dir.resolve())

    def test_workspace_set_relative_to_workspace_root_success(self, tmp_path):
        mock_workspace_root = tmp_path / "my_workspace" / "mock_root"
        mock_workspace_root.mkdir(parents=True)

        project_dir = mock_workspace_root / "my_project"
        project_dir.mkdir()
        (project_dir / "gecx-config.json").touch()

        cwd_dir = mock_workspace_root / "deep" / "nested"
        cwd_dir.mkdir(parents=True)

        args = argparse.Namespace(
            target_dir="my_project",
            gcp_project_id=None,
            deployed_app_id=None,
            location=None,
            app_dir=None,
            evals_dir=None,
            output_dir=None,
            model=None,
            modality=None,
        )

        with (
            mock.patch("pathlib.Path.cwd", return_value=cwd_dir),
            mock.patch(
                "cxas_scrapi.workspace.find_workspace_root",
                return_value=str(mock_workspace_root),
            ),
        ):
            cli_ws.workspace_set(args)

        pointer_file = mock_workspace_root / ".scrapi" / "active-project"
        assert pointer_file.exists()
        data = toml.loads(pointer_file.read_text())
        assert data["base-dir"] == str(project_dir.resolve())

    def test_workspace_set_relative_to_ws_root_success_outside_cwd(
        self, tmp_path
    ):
        workspace_root = tmp_path / "ws_root"
        workspace_root.mkdir()

        project_dir = workspace_root / "my_project"
        project_dir.mkdir()
        (project_dir / "gecx-config.json").touch()

        # CWD is completely outside workspace
        cwd_dir = tmp_path / "outside_cwd"
        cwd_dir.mkdir()

        args = argparse.Namespace(
            target_dir="my_project",
            gcp_project_id=None,
            deployed_app_id=None,
            location=None,
            app_dir=None,
            evals_dir=None,
            output_dir=None,
            model=None,
            modality=None,
        )

        with (
            mock.patch("pathlib.Path.cwd", return_value=cwd_dir),
            mock.patch(
                "cxas_scrapi.workspace.find_workspace_root",
                return_value=str(workspace_root),
            ),
        ):
            cli_ws.workspace_set(args)

        pointer_file = workspace_root / ".scrapi" / "active-project"
        assert pointer_file.exists()
        data = toml.loads(pointer_file.read_text())
        assert data["base-dir"] == str(project_dir.resolve())


class TestWorkspaceShow:
    def test_workspace_show_success(self, tmp_path, capsys):
        (tmp_path / "gecx-config.json").write_text(
            '{"gcp_project_id": "test-id", "deployed_app_id": "test-app"}'
        )

        args = argparse.Namespace()

        with mock.patch(
            "cxas_scrapi.workspace.resolve_project_dir",
            return_value=str(tmp_path),
        ):
            cli_ws.workspace_show(args)

        captured = capsys.readouterr()
        normalized_out = captured.out.replace("\n", "").replace(" ", "")
        assert "ProjectPath:" in normalized_out
        assert str(tmp_path).replace(" ", "") in normalized_out
        assert "test-id" in normalized_out

    def test_workspace_show_unresolved_exits(self, capsys):
        args = argparse.Namespace()

        with mock.patch(
            "cxas_scrapi.workspace.resolve_project_dir",
            side_effect=ValueError("Resolution failed"),
        ):
            with pytest.raises(SystemExit) as excinfo:
                cli_ws.workspace_show(args)

        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "Resolution failed" in captured.out

    def test_workspace_show_missing_config_prints(self, tmp_path, capsys):
        # project dir resolved but config file does not exist
        args = argparse.Namespace()

        with mock.patch(
            "cxas_scrapi.workspace.resolve_project_dir",
            return_value=str(tmp_path),
        ):
            with pytest.raises(SystemExit) as excinfo:
                cli_ws.workspace_show(args)

        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert (
            "Neither gecx-config.toml nor gecx-config.json found"
            in captured.out
        )


class TestWorkspaceCreate:
    @mock.patch("cxas_scrapi.workspace.create_default_config")
    def test_workspace_create_success(self, mock_create, tmp_path):
        args = argparse.Namespace(target_dir=str(tmp_path))

        cli_ws.workspace_create(args)

        mock_create.assert_called_once_with(str(tmp_path))


class TestWorkspaceUnset:
    def test_workspace_unset_success(self, capsys):
        args = argparse.Namespace()

        with mock.patch(
            "cxas_scrapi.workspace.unset_active_project",
            return_value=True,
        ):
            cli_ws.workspace_unset(args)

        captured = capsys.readouterr()
        assert "Successfully unset" in captured.out

    def test_workspace_unset_not_found(self, capsys):
        args = argparse.Namespace()

        with mock.patch(
            "cxas_scrapi.workspace.unset_active_project",
            return_value=False,
        ):
            cli_ws.workspace_unset(args)

        captured = capsys.readouterr()
        assert (
            "No active project workspace configuration was set" in captured.out
        )
