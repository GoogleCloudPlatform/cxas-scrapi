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

"""Unit tests for cxas_scrapi.collab.workspace."""

from __future__ import annotations

import datetime
import json
from typing import TYPE_CHECKING, Any
from unittest import mock

import pytest
import yaml
from google.cloud.ces_v1beta import types

from cxas_scrapi.collab import workspace as ws

if TYPE_CHECKING:
    from pathlib import Path

APP_PREFIX = "projects/test-proj/locations/us/apps"


def _make_app(
    app_id: str,
    display_name: str,
    metadata: dict[str, Any] | None = None,
    update_time: datetime.datetime | None = None,
) -> types.App:
    encoded = {
        k: (v if isinstance(v, str) else json.dumps(v))
        for k, v in (metadata or {}).items()
    }
    kwargs: dict[str, Any] = {
        "name": f"{APP_PREFIX}/{app_id}",
        "display_name": display_name,
        "metadata": encoded,
    }
    if update_time is not None:
        kwargs["update_time"] = update_time
    return types.App(**kwargs)


def test_is_workspace_recognizes_cxas_workspace_without_parent() -> None:
    # Local workspace created via `workspace up` has cxas.workspace, NO cxas.parent
    app = _make_app(
        "ws-1",
        "ws/alice/feat-x",
        metadata={
            "cxas.workspace": {"owner": "alice@example.com", "branch": "feat/x"}
        },
    )
    info = ws.workspace_info_from_app(app)
    assert info.is_workspace is True
    assert info.parent_app is None
    assert info.owner == "alice@example.com"
    assert info.branch == "feat/x"

    # Plain app is NOT a workspace
    plain = _make_app("plain-1", "Production Agent")
    assert ws.workspace_info_from_app(plain).is_workspace is False

    # [CI] PR-* app IS recognized as a CI workspace
    ci_app = _make_app("ci-1", "[CI] PR-42")
    ci_info = ws.workspace_info_from_app(ci_app)
    assert ci_info.is_ci is True
    assert ci_info.is_workspace is True


def test_delete_workspace_enforces_not_a_workspace_and_not_owner() -> None:
    apps = mock.MagicMock()

    plain = _make_app("plain-1", "Prod")
    with pytest.raises(ws.NotAWorkspaceError):
        ws.delete_workspace(apps, plain, user="alice@example.com")
    apps.delete_app.assert_not_called()

    bobs = _make_app(
        "ws-bob",
        "ws/bob/feat-y",
        metadata={"cxas.workspace": {"owner": "bob@example.com"}},
    )
    with pytest.raises(ws.NotOwnerError):
        ws.delete_workspace(apps, bobs, user="alice@example.com", force=False)
    apps.delete_app.assert_not_called()

    # Force allows deleting another user's workspace
    ws.delete_workspace(apps, bobs, user="alice@example.com", force=True)
    apps.delete_app.assert_called_once_with(
        app_name=f"{APP_PREFIX}/ws-bob", force=True
    )


def test_expired_workspaces_includes_expired_ci_pr_apps() -> None:
    now = datetime.datetime(
        2026, 11, 24, 16, 0, 0, tzinfo=datetime.timezone.utc
    )
    past = ws.to_iso(now - datetime.timedelta(hours=2))
    future = ws.to_iso(now + datetime.timedelta(days=5))

    expired_ws = _make_app(
        "ws-old",
        "ws/alice/old",
        metadata={
            "cxas.workspace": {"owner": "alice@example.com"},
            "cxas.expiry": past,
        },
    )
    fresh_ws = _make_app(
        "ws-fresh",
        "ws/alice/fresh",
        metadata={
            "cxas.workspace": {"owner": "alice@example.com"},
            "cxas.expiry": future,
        },
    )
    old_ci = _make_app(
        "ci-old",
        "[CI] PR-99",
        update_time=now - datetime.timedelta(hours=48),
    )
    new_ci = _make_app(
        "ci-new",
        "[CI] PR-100",
        update_time=now - datetime.timedelta(hours=2),
    )

    apps = mock.MagicMock()
    apps.list_apps.return_value = [expired_ws, fresh_ws, old_ci, new_ci]

    found = ws.expired_workspaces(
        apps,
        owner="alice@example.com",
        all_users=False,
        include_ci=True,
        ci_ttl_hours=24.0,
        now=now,
    )
    assert [i.display_name for i in found] == ["[CI] PR-99", "ws/alice/old"]
    assert ws.count_by_owner(found) == {"[CI]": 1, "alice@example.com": 1}

    without_ci = ws.expired_workspaces(
        apps,
        owner="alice@example.com",
        include_ci=False,
        now=now,
    )
    assert [i.display_name for i in without_ci] == ["ws/alice/old"]


def test_workspace_up_refuses_shared_branch_without_explicit_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ws.SharedBranchError, match="main"):
        ws.workspace_up(
            app_dir=str(tmp_path),
            project_id="test-proj",
            location="us",
            branch="main",
            apps=mock.MagicMock(),
            user="alice@example.com",
        )


def test_workspace_up_idempotent_creates_updates_and_writes_ws_local_yml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    app_dir = tmp_path / "src" / "agent"
    app_dir.mkdir(parents=True)
    (app_dir / "app.json").write_text(
        json.dumps({"displayName": "Base App", "rootAgent": "root"}),
        encoding="utf-8",
    )
    pdir = tmp_path / ".cxas" / "profiles"
    pdir.mkdir(parents=True)
    (pdir / "dev.yml").write_text(
        yaml.safe_dump(
            {
                "target": {
                    "project_id": "dev-proj",
                    "location": "europe-west2",
                    "app_id": "shared-dev",
                },
                "app_dir": "src/agent",
                "overrides": {
                    "app.json": {"displayName": "Shared Dev"},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    state: dict[str, Any] = {"exists": False, "metadata": {}, "etag": "e1"}
    ws_app_name = "projects/dev-proj/locations/europe-west2/apps/ws-123"

    fake_apps = mock.MagicMock()

    def _get_by_name(display_name: str) -> Any:
        if state["exists"]:
            return types.App(
                name=ws_app_name,
                display_name=display_name,
                metadata=dict(state["metadata"]),
                etag=state["etag"],
            )
        return None

    def _get_app(name: str) -> types.App:
        return types.App(
            name=name,
            metadata=dict(state["metadata"]),
            locked=False,
            etag=state["etag"],
        )

    def _update_app(request: types.UpdateAppRequest) -> types.App:
        state["metadata"] = dict(request.app.metadata)
        state["etag"] += "x"
        return _get_app(request.app.name)

    fake_apps.get_app_by_display_name.side_effect = _get_by_name
    fake_apps.get_app.side_effect = _get_app
    fake_apps.client.update_app.side_effect = _update_app

    def _fake_push(**_kwargs: Any) -> str:
        state["exists"] = True
        return ws_app_name

    with mock.patch("cxas_scrapi.cli.app._app_push", side_effect=_fake_push):
        # First call creates workspace and writes ws.local.yml
        res1 = ws.workspace_up(
            profile="dev",
            branch="feat/my-feature",
            team="payments",
            ttl_days=7,
            apps=fake_apps,
            user="alice@example.com",
        )
        assert res1.created is True
        assert res1.display_name == "ws/alice/feat-my-feature"
        assert res1.profile_path == pdir / "ws.local.yml"
        assert res1.profile_path.is_file()

        written = yaml.safe_load(res1.profile_path.read_text(encoding="utf-8"))
        assert written["target"]["project_id"] == "dev-proj"
        assert written["target"]["location"] == "europe-west2"
        assert written["target"]["app_id"] == "ws-123"
        assert written["overrides"]["app.json"]["displayName"] == (
            "ws/alice/feat-my-feature"
        )
        assert "cxas.workspace" in state["metadata"]
        assert "cxas.expiry" in state["metadata"]
        assert json.loads(state["metadata"]["cxas.team"]) == "payments"

        # Second call is idempotent (updates existing app)
        res2 = ws.workspace_up(
            profile="dev",
            branch="feat/my-feature",
            apps=fake_apps,
            user="alice@example.com",
        )
        assert res2.created is False
        assert res2.app_name == ws_app_name


def test_no_merge_engine_exported() -> None:
    assert not hasattr(ws, "merge_workspace")
    assert not hasattr(ws, "MergePlan")
    assert not hasattr(ws, "MergeResult")


def test_workspace_up_without_profile_pins_display_name_and_respects_cxas_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With profile=None, staging & ws.local.yml must still pin displayName;
    with CXAS_PROFILE set, workspace_up must inherit it."""
    (tmp_path / ".git").mkdir()
    app_dir = tmp_path / "agent"
    app_dir.mkdir()
    (app_dir / "app.json").write_text(
        json.dumps({"displayName": "Base Tracked Name", "rootAgent": "root"}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CXAS_PROFILE", raising=False)

    ws_app_name = "projects/p1/locations/us/apps/ws-999"
    state: dict[str, Any] = {"metadata": {}, "etag": "e1"}
    fake_apps = mock.MagicMock()
    fake_apps.get_app_by_display_name.return_value = None
    fake_apps.get_app.side_effect = lambda name: types.App(
        name=name,
        metadata=dict(state["metadata"]),
        locked=False,
        etag=state["etag"],
    )

    def _update(request: types.UpdateAppRequest) -> types.App:
        state["metadata"] = dict(request.app.metadata)
        state["etag"] += "x"
        return fake_apps.get_app(request.app.name)

    fake_apps.client.update_app.side_effect = _update

    captured_profiles: list[Any] = []

    def _fake_push(**kwargs: Any) -> str:
        captured_profiles.append(kwargs.get("profile"))
        return ws_app_name

    with mock.patch("cxas_scrapi.cli.app._app_push", side_effect=_fake_push):
        res = ws.workspace_up(
            app_dir=str(app_dir),
            project_id="p1",
            location="us",
            name="ws-custom",
            profile=None,
            apps=fake_apps,
            user="alice@example.com",
        )

    assert captured_profiles[0] is not None
    assert captured_profiles[0].app_overrides["displayName"] == "ws-custom"
    assert res.profile_path is not None
    written = yaml.safe_load(res.profile_path.read_text(encoding="utf-8"))
    assert written["overrides"]["app.json"]["displayName"] == "ws-custom"

    # Now set CXAS_PROFILE=envdev and omit profile= arg; project/location come from it
    pdir = tmp_path / ".cxas" / "profiles"
    (pdir / "envdev.yml").write_text(
        yaml.safe_dump(
            {
                "target": {
                    "project_id": "env-proj",
                    "location": "europe-west2",
                },
                "app_dir": "agent",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CXAS_PROFILE", "envdev")
    with mock.patch("cxas_scrapi.cli.app._app_push", side_effect=_fake_push):
        res_env = ws.workspace_up(
            name="ws-from-env",
            profile=None,
            apps=fake_apps,
            user="alice@example.com",
        )
    assert res_env.profile_path is not None
    written_env = yaml.safe_load(
        res_env.profile_path.read_text(encoding="utf-8")
    )
    assert written_env["target"]["project_id"] == "env-proj"
    assert written_env["target"]["location"] == "europe-west2"
