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

"""CLI tests for `cxas workspace up | list | down | cleanup`."""

from __future__ import annotations

import argparse
import datetime
import json
from typing import TYPE_CHECKING
from unittest import mock

import pytest
from google.cloud.ces_v1beta import types

from cxas_scrapi.cli import workspace_cli
from cxas_scrapi.cli.main import get_parser
from cxas_scrapi.collab import workspace as ws

if TYPE_CHECKING:
    from pathlib import Path

APP_PREFIX = "projects/test-proj/locations/us/apps"


def test_workspace_parser_subcommands() -> None:
    parser = get_parser()

    up_args = parser.parse_args(
        ["workspace", "up", "--profile", "dev", "--ttl-days", "7"]
    )
    assert up_args.command == "workspace"
    assert up_args.workspace_command == "up"
    assert up_args.profile == "dev"
    assert up_args.ttl_days == 7.0

    list_args = parser.parse_args(
        ["workspace", "list", "--mine", "--include-ci", "--json"]
    )
    assert list_args.workspace_command == "list"
    assert list_args.mine is True
    assert list_args.include_ci is True
    assert list_args.json is True

    down_args = parser.parse_args(["workspace", "down", "--pr", "42", "--yes"])
    assert down_args.workspace_command == "down"
    assert down_args.pr == "42"
    assert down_args.yes is True

    # Alias `delete` also parses cleanly
    del_args = parser.parse_args(["workspace", "delete", "ws/alice/x", "--yes"])
    assert del_args.workspace_command == "delete"

    clean_args = parser.parse_args(
        [
            "workspace",
            "cleanup",
            "--all-users",
            "--include-ci",
            "--dry-run",
            "--yes",
        ]
    )
    assert clean_args.workspace_command == "cleanup"
    assert clean_args.all_users is True
    assert clean_args.include_ci is True
    assert clean_args.dry_run is True


def test_workspace_list_down_and_cleanup_cli(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CXAS_USER", "alice@example.com")
    past = ws.to_iso(ws.utc_now() - datetime.timedelta(hours=3))

    ws_app = types.App(
        name=f"{APP_PREFIX}/ws-1",
        display_name="ws/alice/feat-1",
        metadata={
            "cxas.workspace": json.dumps(
                {"owner": "alice@example.com", "branch": "feat/1"}
            ),
            "cxas.expiry": json.dumps(past),
        },
    )
    plain_app = types.App(
        name=f"{APP_PREFIX}/prod-1",
        display_name="Prod App",
        metadata={},
    )
    ci_app = types.App(
        name=f"{APP_PREFIX}/ci-42",
        display_name="[CI] PR-42",
        metadata={},
        update_time=ws.utc_now() - datetime.timedelta(hours=48),
    )

    fake_apps = mock.MagicMock()
    fake_apps.list_apps.return_value = [ws_app, plain_app, ci_app]

    def _find_by_display(name: str) -> types.App | None:
        for a in [ws_app, plain_app, ci_app]:
            if a.display_name == name:
                return a
        return None

    fake_apps.get_app_by_display_name.side_effect = _find_by_display

    with mock.patch(
        "cxas_scrapi.cli.workspace_cli.Apps", return_value=fake_apps
    ):
        # 1. list --mine --json
        workspace_cli.workspace_list_cmd(
            argparse.Namespace(
                mine=True,
                team=None,
                include_ci=False,
                json=True,
                project_id="test-proj",
                location="us",
                profile=None,
            )
        )
        listed = json.loads(capsys.readouterr().out)
        assert [x["display_name"] for x in listed] == ["ws/alice/feat-1"]

        # 2. down refuses plain non-workspace app
        with pytest.raises(SystemExit) as excinfo:
            workspace_cli.workspace_down_cmd(
                argparse.Namespace(
                    name="Prod App",
                    branch=None,
                    pr=None,
                    force=False,
                    yes=True,
                    project_id="test-proj",
                    location="us",
                    profile=None,
                )
            )
        assert excinfo.value.code == 1
        assert "not a workspace" in capsys.readouterr().err
        fake_apps.delete_app.assert_not_called()

        # 3. cleanup --include-ci --dry-run does not delete
        workspace_cli.workspace_cleanup_cmd(
            argparse.Namespace(
                team=None,
                all_users=True,
                include_ci=True,
                ci_ttl_hours=24.0,
                dry_run=True,
                yes=True,
                project_id="test-proj",
                location="us",
                profile=None,
            )
        )
        out = capsys.readouterr().out
        assert "[DRY RUN]" in out
        assert "[CI] PR-42" in out
        assert "ws/alice/feat-1" in out
        fake_apps.delete_app.assert_not_called()

        # 4. cleanup --include-ci --yes deletes both expired apps
        workspace_cli.workspace_cleanup_cmd(
            argparse.Namespace(
                team=None,
                all_users=True,
                include_ci=True,
                ci_ttl_hours=24.0,
                dry_run=False,
                yes=True,
                project_id="test-proj",
                location="us",
                profile=None,
            )
        )
        assert fake_apps.delete_app.call_count == 2
        assert "deleted 2/2" in capsys.readouterr().out


def test_workspace_up_cmd_prints_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_res = ws.WorkspaceUpResult(
        app_name=f"{APP_PREFIX}/ws-1",
        display_name="ws/alice/feat-1",
        created=True,
        owner="alice@example.com",
        branch="feat/1",
        team="core",
        expiry="2026-12-08T00:00:00Z",
        profile_path=tmp_path / ".cxas" / "profiles" / "ws.local.yml",
    )
    with mock.patch(
        "cxas_scrapi.collab.workspace.workspace_up", return_value=fake_res
    ):
        workspace_cli.workspace_up_cmd(
            argparse.Namespace(
                app_dir=None,
                project_id="test-proj",
                location="us",
                branch=None,
                name=None,
                profile=None,
                team="core",
                ttl_days=14.0,
                env_file=None,
                no_write_profile=False,
            )
        )
    out = capsys.readouterr().out
    assert "Workspace created:" in out
    assert "ws/alice/feat-1" in out
    assert "ws.local.yml" in out
