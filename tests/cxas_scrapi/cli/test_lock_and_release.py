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

"""CLI tests for lock, unlock, lock-status, rollback, and push/pull guardrails."""

from __future__ import annotations

import argparse
import datetime
import io
import json
import subprocess
import zipfile
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest import mock

if TYPE_CHECKING:
    from pathlib import Path

import pytest
import yaml
from google.cloud.ces_v1beta import types

from cxas_scrapi.cli import app as cli_app
from cxas_scrapi.cli import lock_cli
from cxas_scrapi.cli import main as main_cli
from cxas_scrapi.collab.drift import DriftEntry, DriftReport
from cxas_scrapi.core.apps import Apps

APP_NAME = "projects/test-proj/locations/us/apps/test-app"


def _write_profile(tmp_path: Path, name: str, data: dict) -> Path:
    (tmp_path / ".git").mkdir(exist_ok=True)
    pdir = tmp_path / ".cxas" / "profiles"
    pdir.mkdir(parents=True, exist_ok=True)
    p = pdir / f"{name}.yml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def test_apps_import_app_sets_ignore_app_lock() -> None:
    with mock.patch("cxas_scrapi.core.apps.AgentServiceClient"):
        apps = Apps(project_id="p", location="us")
        apps.client = mock.MagicMock()
        apps.import_app(
            app_name=APP_NAME,
            app_content=b"zipbytes",
            ignore_app_lock=True,
        )
        req = apps.client.import_app.call_args.kwargs["request"]
        assert req.ignore_app_lock is True


def test_lock_unlock_and_lock_status_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_profile(
        tmp_path,
        "qa",
        {
            "target": {
                "project_id": "test-proj",
                "location": "us",
                "app_id": "test-app",
            }
        },
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CXAS_USER", "alice@example.com")

    state = {"metadata": {}, "locked": False, "etag": "e1"}

    def _get_app(name: str) -> types.App:
        return types.App(
            name=name,
            metadata=dict(state["metadata"]),
            locked=state["locked"],
            etag=state["etag"],
        )

    def _update_app(request: types.UpdateAppRequest) -> types.App:
        paths = list(request.update_mask.paths)
        if "metadata" in paths:
            state["metadata"] = dict(request.app.metadata)
        if "locked" in paths:
            state["locked"] = bool(request.app.locked)
        state["etag"] += "x"
        return _get_app(request.app.name)

    fake_apps = mock.MagicMock()
    fake_apps.get_app.side_effect = _get_app
    fake_apps.client.update_app.side_effect = _update_app

    with mock.patch("cxas_scrapi.cli.app.Apps", return_value=fake_apps):
        lock_cli.cmd_lock(
            argparse.Namespace(
                app=None,
                app_name=None,
                project_id=None,
                location=None,
                profile="qa",
                ttl=30,
                reason="deploy",
                force=False,
                no_native=False,
            )
        )
        assert state["locked"] is True
        assert "Locked" in capsys.readouterr().out

        lock_cli.cmd_lock_status(
            argparse.Namespace(
                app=None,
                app_name=None,
                project_id=None,
                location=None,
                profile="qa",
                json=True,
            )
        )
        out = capsys.readouterr().out
        # First line is the profile banner; JSON follows
        json_str = out[out.index("{") :]
        parsed = json.loads(json_str)
        assert parsed["native_locked"] is True
        assert parsed["lock"]["by"] == "alice@example.com"

        lock_cli.cmd_unlock(
            argparse.Namespace(
                app=None,
                app_name=None,
                project_id=None,
                location=None,
                profile="qa",
                force=False,
            )
        )
        assert state["locked"] is False
        assert "Unlocked" in capsys.readouterr().out


def test_push_refuses_foreign_lock_even_with_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_dir = tmp_path / "agent"
    app_dir.mkdir()
    (app_dir / "app.json").write_text(
        json.dumps({"displayName": "My App", "rootAgent": "root"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CXAS_USER", "bob@example.com")
    future = (
        datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(minutes=30)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    fake_apps = mock.MagicMock()
    fake_apps.get_app.return_value = types.App(
        name=APP_NAME,
        locked=True,
        metadata={
            "cxas.lock": json.dumps(
                {"by": "alice@example.com", "until": future}
            )
        },
        etag="e1",
    )

    with (
        mock.patch("cxas_scrapi.cli.app.Apps", return_value=fake_apps),
        pytest.raises(SystemExit) as excinfo,
    ):
        cli_app.app_push(
            argparse.Namespace(
                app_dir=str(app_dir),
                to=APP_NAME,
                app_name=None,
                display_name=None,
                env_file=None,
                project_id=None,
                location=None,
                overwrite=True,
                with_lock=True,
                on_drift="overwrite",
                fail_if_version_exists=False,
                create_version=False,
                profile=None,
                allow_protected=False,
                dry_run=False,
            )
        )

    assert excinfo.value.code == 1
    assert "alice@example.com" in capsys.readouterr().err
    fake_apps.import_app.assert_not_called()


def test_push_with_lock_relocks_even_on_import_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_dir = tmp_path / "agent"
    app_dir.mkdir()
    (app_dir / "app.json").write_text(
        json.dumps({"displayName": "My App", "rootAgent": "root"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CXAS_USER", "alice@example.com")
    future = (
        datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(minutes=30)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    state = {
        "locked": True,
        "metadata": {
            "cxas.lock": json.dumps(
                {"by": "alice@example.com", "until": future}
            )
        },
        "etag": "e1",
    }

    def _get_app(name: str) -> types.App:
        return types.App(
            name=name,
            locked=state["locked"],
            metadata=dict(state["metadata"]),
            etag=state["etag"],
        )

    def _update_app(request: types.UpdateAppRequest) -> types.App:
        if "metadata" in request.update_mask.paths:
            state["metadata"] = dict(request.app.metadata)
        if "locked" in request.update_mask.paths:
            state["locked"] = bool(request.app.locked)
        state["etag"] += "x"
        return _get_app(request.app.name)

    fake_apps = mock.MagicMock()
    fake_apps.get_app.side_effect = _get_app
    fake_apps.client.update_app.side_effect = _update_app
    fake_apps.import_app.side_effect = RuntimeError("simulated CES failure")

    with (
        mock.patch("cxas_scrapi.cli.app.Apps", return_value=fake_apps),
        pytest.raises(SystemExit) as excinfo,
    ):
        cli_app.app_push(
            argparse.Namespace(
                app_dir=str(app_dir),
                to=APP_NAME,
                app_name=None,
                display_name=None,
                env_file=None,
                project_id=None,
                location=None,
                overwrite=False,
                with_lock=True,
                on_drift="overwrite",
                fail_if_version_exists=False,
                create_version=False,
                profile=None,
                allow_protected=False,
                dry_run=False,
            )
        )

    assert excinfo.value.code == 1
    # App MUST be re-locked after the crash!
    assert state["locked"] is True


def test_push_preserves_cxas_keys_and_enforces_drift_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_dir = tmp_path / "agent"
    app_dir.mkdir()
    (app_dir / "app.json").write_text(
        json.dumps({"displayName": "My App", "rootAgent": "root"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CXAS_USER", "alice@example.com")

    fake_apps = mock.MagicMock()
    fake_apps.get_app.return_value = types.App(
        name=APP_NAME,
        locked=False,
        metadata={
            "cxas.last_push": json.dumps({"seq": 5, "by": "alice@example.com"}),
            "cxas.workspace": "true",
        },
        etag="e1",
    )

    drift = DriftReport(
        app_name=APP_NAME,
        has_baseline=True,
        baseline_seq=5,
        entries=[
            DriftEntry(
                seq=6,
                author="console@example.com",
                action="UPDATE",
                resource_type="AGENT",
                resource=f"{APP_NAME}/agents/root",
                display_name="root",
            )
        ],
    )

    # --on-drift=fail blocks push
    with (
        mock.patch("cxas_scrapi.cli.app.Apps", return_value=fake_apps),
        mock.patch("cxas_scrapi.cli.app.check_drift", return_value=drift),
        pytest.raises(SystemExit) as excinfo,
    ):
        cli_app.app_push(
            argparse.Namespace(
                app_dir=str(app_dir),
                to=APP_NAME,
                app_name=None,
                display_name=None,
                env_file=None,
                project_id=None,
                location=None,
                overwrite=False,
                with_lock=False,
                on_drift="fail",
                fail_if_version_exists=False,
                create_version=False,
                profile=None,
                allow_protected=False,
                dry_run=False,
            )
        )
    assert excinfo.value.code == 1
    assert "Drift detected" in capsys.readouterr().err
    fake_apps.import_app.assert_not_called()

    # --on-drift=overwrite succeeds and preserves cxas.* metadata in zip
    lro = mock.MagicMock()
    lro.result.return_value = SimpleNamespace(name=APP_NAME, warnings=[])
    fake_apps.import_app.return_value = lro

    with (
        mock.patch("cxas_scrapi.cli.app.Apps", return_value=fake_apps),
        mock.patch("cxas_scrapi.cli.app.record_last_push") as mock_rec,
    ):
        cli_app.app_push(
            argparse.Namespace(
                app_dir=str(app_dir),
                to=APP_NAME,
                app_name=None,
                display_name=None,
                env_file=None,
                project_id=None,
                location=None,
                overwrite=False,
                with_lock=False,
                on_drift="overwrite",
                fail_if_version_exists=False,
                create_version=False,
                profile=None,
                allow_protected=False,
                dry_run=False,
            )
        )

    zip_bytes = fake_apps.import_app.call_args.kwargs["app_content"]
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        staged_app = json.loads(zf.read("agent/app.json").decode("utf-8"))
    assert staged_app["metadata"]["cxas.workspace"] == "true"
    mock_rec.assert_called_once()


def test_push_fail_if_version_exists_and_dedup_detection(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_dir = tmp_path / "agent"
    app_dir.mkdir()
    (app_dir / "app.json").write_text(
        json.dumps({"displayName": "My App", "rootAgent": "root"}),
        encoding="utf-8",
    )
    fake_apps = mock.MagicMock()
    fake_apps.get_app.return_value = types.App(
        name=APP_NAME, locked=False, metadata={}, etag="e1"
    )
    lro = mock.MagicMock()
    lro.result.return_value = SimpleNamespace(name=APP_NAME, warnings=[])
    fake_apps.import_app.return_value = lro

    # 1. Existing version name rejected pre-push
    fake_versions = mock.MagicMock()
    fake_versions.list_versions.return_value = [
        SimpleNamespace(name=f"{APP_NAME}/versions/1", display_name="v1.0.0")
    ]
    with (
        mock.patch("cxas_scrapi.cli.app.Apps", return_value=fake_apps),
        mock.patch("cxas_scrapi.cli.app.Versions", return_value=fake_versions),
        pytest.raises(SystemExit) as excinfo,
    ):
        cli_app.app_push(
            argparse.Namespace(
                app_dir=str(app_dir),
                to=APP_NAME,
                app_name=None,
                display_name=None,
                env_file=None,
                project_id=None,
                location=None,
                overwrite=False,
                with_lock=False,
                on_drift="overwrite",
                fail_if_version_exists=True,
                create_version=True,
                version_name="v1.0.0",
                version_description=None,
                profile=None,
                allow_protected=False,
                dry_run=False,
            )
        )
    assert excinfo.value.code == 1
    assert "already exists" in capsys.readouterr().err
    fake_apps.import_app.assert_not_called()

    # 2. CES CreateAppVersion deduplication detected when display_name differs
    fake_versions.list_versions.return_value = []
    fake_versions.create_version.return_value = SimpleNamespace(
        name=f"{APP_NAME}/versions/1", display_name="v1.0.0"
    )
    with (
        mock.patch("cxas_scrapi.cli.app.Apps", return_value=fake_apps),
        mock.patch("cxas_scrapi.cli.app.Versions", return_value=fake_versions),
        mock.patch("cxas_scrapi.cli.app.record_last_push"),
        pytest.raises(SystemExit) as excinfo,
    ):
        cli_app.app_push(
            argparse.Namespace(
                app_dir=str(app_dir),
                to=APP_NAME,
                app_name=None,
                display_name=None,
                env_file=None,
                project_id=None,
                location=None,
                overwrite=False,
                with_lock=False,
                on_drift="overwrite",
                fail_if_version_exists=True,
                create_version=True,
                version_name="v1.0.1",
                version_description=None,
                profile=None,
                allow_protected=False,
                dry_run=False,
            )
        )
    assert excinfo.value.code == 1
    assert "deduplicated" in capsys.readouterr().err


def test_rollback_resolves_version_and_warns_other_deployments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_profile(
        tmp_path,
        "prod",
        {
            "protected": True,
            "target": {
                "project_id": "test-proj",
                "location": "us",
                "app_id": "test-app",
                "deployment_id": "voice-prod",
            },
        },
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CI", raising=False)

    # Blocked locally without --allow-protected
    with pytest.raises(SystemExit) as excinfo:
        lock_cli.cmd_rollback(
            argparse.Namespace(
                app_name=None,
                deployment=None,
                version="v1.0.0",
                profile="prod",
                allow_protected=False,
                project_id=None,
                location=None,
            )
        )
    assert excinfo.value.code == 1
    assert "protected profile" in capsys.readouterr().err

    # Allowed with --allow-protected
    fake_versions = mock.MagicMock()
    fake_versions.resolve_version_name.return_value = f"{APP_NAME}/versions/1"

    fake_deps = mock.MagicMock()
    fake_deps.get_deployment.return_value = SimpleNamespace(
        name=f"{APP_NAME}/deployments/voice-prod",
        app_version=f"{APP_NAME}/versions/2",
    )
    fake_deps.list_deployments.return_value = [
        SimpleNamespace(
            name=f"{APP_NAME}/deployments/voice-prod",
            app_version=f"{APP_NAME}/versions/1",
        ),
        SimpleNamespace(
            name=f"{APP_NAME}/deployments/chat-prod",
            app_version=f"{APP_NAME}/versions/2",
        ),
    ]

    with (
        mock.patch(
            "cxas_scrapi.core.versions.Versions", return_value=fake_versions
        ),
        mock.patch(
            "cxas_scrapi.core.deployments.Deployments", return_value=fake_deps
        ),
    ):
        lock_cli.cmd_rollback(
            argparse.Namespace(
                app_name=None,
                deployment=None,
                version="v1.0.0",
                profile="prod",
                allow_protected=True,
                project_id=None,
                location=None,
            )
        )

    fake_deps.update_deployment.assert_called_once_with(
        deployment_id="voice-prod",
        app_version=f"{APP_NAME}/versions/1",
    )
    captured = capsys.readouterr()
    assert f"{APP_NAME}/versions/2 -> {APP_NAME}/versions/1" in captured.out
    assert "chat-prod" in captured.err


def test_deployments_promote_resolves_version_display_name() -> None:
    fake_versions = mock.MagicMock()
    fake_versions.resolve_version_name.return_value = f"{APP_NAME}/versions/42"
    fake_deps = mock.MagicMock()

    with (
        mock.patch(
            "cxas_scrapi.core.versions.Versions", return_value=fake_versions
        ),
        mock.patch(
            "cxas_scrapi.core.deployments.Deployments", return_value=fake_deps
        ),
    ):
        main_cli.deployments_promote(
            argparse.Namespace(
                app_name=APP_NAME,
                app_resource_name=None,
                deployment_id="live",
                version="v1.2.0",
                traffic_split=None,
                profile=None,
                allow_protected=False,
            )
        )

    fake_versions.resolve_version_name.assert_called_once_with("v1.2.0")
    fake_deps.update_deployment.assert_called_once_with(
        deployment_id="live",
        app_version=f"{APP_NAME}/versions/42",
    )


def test_pull_branch_creates_git_branch_and_rejects_dirty_tree(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    subprocess.run(
        ["git", "init"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=tmp_path, check=True
    )
    (tmp_path / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    # Dirty tree rejected
    (tmp_path / "README.md").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        cli_app._prepare_pull_branch("fix/console", cwd=str(tmp_path))
    assert excinfo.value.code == 1
    assert "uncommitted changes" in capsys.readouterr().err

    # Clean tree creates and switches branch
    subprocess.run(
        ["git", "checkout", "--", "README.md"], cwd=tmp_path, check=True
    )
    cli_app._prepare_pull_branch("fix/console", cwd=str(tmp_path))
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert branch == "fix/console"
