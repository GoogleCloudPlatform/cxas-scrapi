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

"""Regression tests for gaps found pushing a compiled bundle to real CES.

1. Governed lock: CES rejects importing a bundle whose app.json has
   ``locked: true`` (FAILED_PRECONDITION "App ... is locked"). ``cxas push``
   imports with ``locked=false`` and applies the lock via ``UpdateApp``.
2. CES system tools (``end_session``) are referenced by name with no
   ``tools/`` directory; lint's platform-tool allowlist is the single source.
3. Drift normalizer: CES round-trip canonicalization must read as CLEAN.
"""

import io
import json
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from cxas_scrapi.build import write_lockfile
from cxas_scrapi.build.drift import (
    check_drift,
    extract_normalized_resources,
)
from cxas_scrapi.cli import main as cli_main
from cxas_scrapi.utils.linter import CES_PLATFORM_SYSTEM_TOOLS, LintContext

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
ROUNDTRIP_DIR = FIXTURES_DIR / "ces_roundtrip_digital"
TARGET_APP_NAME = "projects/test-proj/locations/us/apps/cymbal-retail-digital"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _locked_bundle(tmp_path: Path) -> Path:
    """Real compiled bundle (sanitized fixture) declaring the governed lock."""
    bundle = tmp_path / "bundle"
    shutil.copytree(ROUNDTRIP_DIR / "ours", bundle)
    app = _read_json(bundle / "app.json")
    app["locked"] = True
    _write_json(bundle / "app.json", app)
    return bundle


def test_system_tool_allowlist_is_lints_platform_tools() -> None:
    assert "end_session" in CES_PLATFORM_SYSTEM_TOOLS
    assert set(CES_PLATFORM_SYSTEM_TOOLS) == set(
        LintContext(Path("."), Path("."), Path(".")).platform_tools
    )


# --------------------------------------------------------------------------
# Governed lock: push imports unlocked, then applies the lock
# --------------------------------------------------------------------------


def _zip_app_json(app_content: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(app_content)) as zf:
        names = [
            n
            for n in zf.namelist()
            if n.endswith("/app.json") or n == "app.json"
        ]
        assert names, zf.namelist()
        return json.loads(zf.read(sorted(names, key=len)[0]).decode("utf-8"))


@pytest.fixture
def mock_apps_client() -> Any:
    with mock.patch("cxas_scrapi.cli.app.Apps", autospec=True) as cls:
        inst = cls.return_value
        app = mock.MagicMock()
        app.name = TARGET_APP_NAME
        app.locked = False
        inst.get_app_by_display_name.return_value = app
        inst.get_app.return_value = app
        resp = mock.MagicMock()
        resp.name = TARGET_APP_NAME
        resp.warnings = []
        lro = mock.MagicMock()
        lro.result.return_value = resp
        inst.import_app.return_value = lro
        inst.import_as_new_app.return_value = lro
        yield inst


def _push(argv: list[str]) -> None:
    with mock.patch.object(sys, "argv", ["cxas", "push", *argv]):
        cli_main.main()


def test_push_imports_unlocked_payload_then_applies_lock(
    tmp_path: Path, mock_apps_client: Any
) -> None:
    bundle = _locked_bundle(tmp_path)
    assert _read_json(bundle / "app.json")["locked"] is True

    manager = mock.MagicMock()
    manager.attach_mock(mock_apps_client.import_app, "import_app")
    manager.attach_mock(mock_apps_client.update_app, "update_app")

    _push(
        [
            "--app-dir",
            str(bundle),
            "--to",
            TARGET_APP_NAME,
            "--overwrite",
        ]
    )

    payload = _zip_app_json(
        mock_apps_client.import_app.call_args.kwargs["app_content"]
    )
    assert payload.get("locked") is not True
    # The on-disk bundle is untouched (still declares the governed lock).
    assert _read_json(bundle / "app.json")["locked"] is True
    calls = [c[0] for c in manager.mock_calls]
    assert calls.index("import_app") < calls.index("update_app")
    mock_apps_client.update_app.assert_called_once_with(
        TARGET_APP_NAME, locked=True
    )


def test_push_unlocks_already_locked_target_then_relocks(
    tmp_path: Path, mock_apps_client: Any
) -> None:
    mock_apps_client.get_app.return_value.locked = True
    bundle = _locked_bundle(tmp_path)

    manager = mock.MagicMock()
    manager.attach_mock(mock_apps_client.import_app, "import_app")
    manager.attach_mock(mock_apps_client.update_app, "update_app")

    _push(["--app-dir", str(bundle), "--to", TARGET_APP_NAME, "--overwrite"])

    calls = [
        c for c in manager.mock_calls if c[0] in {"import_app", "update_app"}
    ]
    assert [c[0] for c in calls] == ["update_app", "import_app", "update_app"]
    assert calls[0] == mock.call.update_app(TARGET_APP_NAME, locked=False)
    assert calls[2] == mock.call.update_app(TARGET_APP_NAME, locked=True)


def test_push_relocks_previously_locked_target_when_import_fails(
    tmp_path: Path, mock_apps_client: Any
) -> None:
    mock_apps_client.get_app.return_value.locked = True
    mock_apps_client.import_app.side_effect = RuntimeError("boom")
    bundle = _locked_bundle(tmp_path)

    with pytest.raises(SystemExit):
        _push(
            ["--app-dir", str(bundle), "--to", TARGET_APP_NAME, "--overwrite"]
        )

    assert mock_apps_client.update_app.call_args_list == [
        mock.call(TARGET_APP_NAME, locked=False),
        mock.call(TARGET_APP_NAME, locked=True),
    ]


def test_push_unlocked_bundle_to_unlocked_target_never_calls_update_app(
    tmp_path: Path, mock_apps_client: Any
) -> None:
    bundle = _locked_bundle(tmp_path)
    app = _read_json(bundle / "app.json")
    app["locked"] = False
    _write_json(bundle / "app.json", app)

    _push(["--app-dir", str(bundle), "--to", TARGET_APP_NAME, "--overwrite"])

    mock_apps_client.import_app.assert_called_once()
    mock_apps_client.update_app.assert_not_called()


# --------------------------------------------------------------------------
# Fix 3: drift normalizer vs real CES round-trip canonicalization
# --------------------------------------------------------------------------


def test_real_ces_roundtrip_export_normalizes_identically() -> None:
    ours = extract_normalized_resources(ROUNDTRIP_DIR / "ours")
    live = extract_normalized_resources(ROUNDTRIP_DIR / "live")
    assert set(ours) == set(live)
    mismatched = sorted(r for r in ours if ours[r].sha256 != live[r].sha256)
    assert mismatched == []


def test_real_ces_roundtrip_three_way_drift_is_clean(tmp_path: Path) -> None:
    ours_dir = tmp_path / "app"
    shutil.copytree(ROUNDTRIP_DIR / "ours", ours_dir)
    lock = write_lockfile(ours_dir, tmp_path / "app.lock.json")
    report = check_drift(
        "app",
        ours_dir,
        ROUNDTRIP_DIR / "live",
        lockfile_path=lock,
    )
    assert {r.resource_id: r.status for r in report.resources} == {
        r.resource_id: "CLEAN" for r in report.resources
    }


def test_real_content_change_still_drifts_after_normalization(
    tmp_path: Path,
) -> None:
    live_dir = tmp_path / "live"
    shutil.copytree(ROUNDTRIP_DIR / "live", live_dir)
    gi = live_dir / "global_instruction.txt"
    gi.write_text(
        gi.read_text(encoding="utf-8").replace("Willkommen bei", "Hallo von"),
        encoding="utf-8",
    )
    ours = extract_normalized_resources(ROUNDTRIP_DIR / "ours")
    live = extract_normalized_resources(live_dir)
    assert ours["app"].sha256 != live["app"].sha256

    # Order-sensitive content and non-default values still count.
    root = live_dir / "agents" / "root_agent" / "root_agent.json"
    data = _read_json(root)
    data["tools"] = [*data.get("tools", []), "extra_tool"]
    _write_json(root, data)
    env = live_dir / "environment.json"
    env_data = _read_json(env)
    env_data["tools"]["verify_tpin"]["openApiTool"]["url"] = (
        "https://evil.example/x"
    )
    _write_json(env, env_data)
    live = extract_normalized_resources(live_dir)
    assert ours["agents/root_agent"].sha256 != live["agents/root_agent"].sha256
    assert ours["tools/verify_tpin"].sha256 != live["tools/verify_tpin"].sha256


def test_non_default_logging_settings_still_drift(
    tmp_path: Path,
) -> None:
    live_dir = tmp_path / "live"
    shutil.copytree(ROUNDTRIP_DIR / "live", live_dir)
    app = _read_json(live_dir / "app.json")
    app["loggingSettings"] = {
        "conversationLoggingSettings": {"retentionWindow": "86400s"}
    }
    _write_json(live_dir / "app.json", app)
    ours = extract_normalized_resources(ROUNDTRIP_DIR / "ours")
    assert (
        ours["app"].sha256
        != extract_normalized_resources(live_dir)["app"].sha256
    )


def test_app_lock_state_is_control_plane_not_drift(tmp_path: Path) -> None:
    """CES never exports `locked` (GetApp reported locked=True while the
    export's app.json had no `locked` key), and push applies the lock via
    UpdateApp, so the bundle's declared lock must not read as drift."""
    ours_dir = tmp_path / "ours"
    shutil.copytree(ROUNDTRIP_DIR / "ours", ours_dir)
    app = _read_json(ours_dir / "app.json")
    app["locked"] = True
    _write_json(ours_dir / "app.json", app)
    assert "locked" not in _read_json(ROUNDTRIP_DIR / "live" / "app.json")
    assert (
        extract_normalized_resources(ours_dir)["app"].sha256
        == extract_normalized_resources(ROUNDTRIP_DIR / "live")["app"].sha256
    )
