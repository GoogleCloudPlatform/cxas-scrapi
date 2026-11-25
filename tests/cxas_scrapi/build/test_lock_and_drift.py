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

"""Unit & CLI tests for SHA-256 lockfiles (`cxas lock`) and 3-way drift."""

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from cxas_scrapi.build import (
    BuildGateError,
    build_all,
    build_app,
    check_drift,
    extract_normalized_resources,
    write_lockfile,
)
from cxas_scrapi.cli import main as cli_main
from cxas_scrapi.core.python_bundler import (
    bundle_shared_python,
    inspect_entrypoint_bundle,
    strip_shared_bundle_text,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
ROUNDTRIP_DIR = FIXTURES_DIR / "ces_roundtrip_digital"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_sample_source(root: Path) -> tuple[Path, Path]:
    core = root / "core"
    _write_json(
        core / "app.json",
        {
            "name": "@@profile.profile_id@@-assistant",
            "displayName": "@@profile.display_name@@ Assistant",
            "rootAgent": "root_agent",
        },
    )
    _write_text(
        core / "global_instruction.txt",
        "You represent @@profile.display_name@@ only.\n",
    )
    _write_json(
        core / "agents/root_agent/root_agent.json",
        {
            "displayName": "root_agent",
            "instruction": "agents/root_agent/instruction.txt",
            "childAgents": ["guide_agent"],
            "tools": ["end_session"],
        },
    )
    _write_text(
        core / "agents/root_agent/instruction.txt",
        "Route questions to {@AGENT: guide_agent}.\n",
    )
    _write_json(
        core / "agents/guide_agent/guide_agent.json",
        {
            "displayName": "guide_agent",
            "instruction": "agents/guide_agent/instruction.txt",
            "tools": [],
        },
    )
    _write_text(
        core / "agents/guide_agent/instruction.txt",
        "Answer @@profile.display_name@@ questions.\n",
    )
    _write_text(core / "python_lib/__init__.py", "")
    _write_text(
        core / "python_lib/helper.py",
        'TAG = "@@profile.profile_id@@"\ndef tag() -> str:\n    return TAG\n',
    )
    _write_text(
        core / "agents/root_agent/before_model_callbacks/cb_1/python_code.py",
        "import python_lib.helper\ndef before_model():\n    return python_lib.helper.tag()\n",
    )

    man = root / "manifests"
    _write_text(
        man / "profiles/cymbal_retail/identity.yaml",
        "profile_id: cymbal_retail\ndisplay_name: Cymbal Retail\n",
    )
    _write_text(
        man / "profiles/cymbal_retail/channel_policy.yaml",
        "profile_id: cymbal_retail\nchannels:\n  WEB:\n    supported_modalities: ['TEXT']\n",
    )
    _write_text(man / "envs/dev.yaml", "environment: dev\n")
    return core, man


def _run_cli(argv: list[str]) -> None:
    with mock.patch.object(sys, "argv", ["cxas", *argv]):
        cli_main.main()


# --------------------------------------------------------------------------
# CES round-trip canonicalization & drift detection
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
    assert report.is_clean
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

    root = live_dir / "agents" / "root_agent" / "root_agent.json"
    data = _read_json(root)
    data["tools"] = [*data.get("tools", []), "extra_tool"]
    _write_json(root, data)
    env = live_dir / "environment.json"
    env_data = _read_json(env)
    env_data["tools"]["verify_pin"]["openApiTool"]["url"] = (
        "https://evil.example/x"
    )
    _write_json(env, env_data)
    live = extract_normalized_resources(live_dir)
    assert ours["agents/root_agent"].sha256 != live["agents/root_agent"].sha256
    assert ours["tools/verify_pin"].sha256 != live["tools/verify_pin"].sha256


def test_non_default_logging_settings_still_drift(tmp_path: Path) -> None:
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
    """CES never exports `locked`, so a bundle's `locked` key is not drift."""
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


# --------------------------------------------------------------------------
# Build --write-locks, cxas lock, cxas drift CLI, accept-live & git mirror
# --------------------------------------------------------------------------


def test_build_app_and_build_all_write_sibling_lockfiles(
    tmp_path: Path,
) -> None:
    core, man = _make_sample_source(tmp_path / "src")
    out_single = tmp_path / "dist_single" / "cymbal_web"
    rep = build_app(
        core, man, "cymbal_retail", "WEB", "dev", out_single, write_lock=True
    )
    assert rep.lockfile_path == out_single.parent / "cymbal_web.lock.json"
    assert rep.lockfile_path.is_file()
    assert not (out_single / "cymbal_web.lock.json").exists()

    out_all = tmp_path / "dist_all"
    reports = build_all(core, man, out_all, "dev", write_locks=True)
    assert len(reports) == 1
    assert (
        reports[0].lockfile_path == out_all / "cymbal_retail" / "web.lock.json"
    )
    assert reports[0].lockfile_path.is_file()


def test_bundled_vs_native_python_env_normalizes_identically(
    tmp_path: Path,
) -> None:
    """Bundled and --native-python-env apps share identical normalized hashes."""
    core, man = _make_sample_source(tmp_path / "src")
    out_bundled = tmp_path / "dist_bundled"
    out_native = tmp_path / "dist_native"

    build_app(core, man, "cymbal_retail", "WEB", "dev", out_bundled)
    build_app(
        core,
        man,
        "cymbal_retail",
        "WEB",
        "dev",
        out_native,
        native_python_env=True,
    )

    cb_rel = "agents/root_agent/before_model_callbacks/cb_1/python_code.py"
    bundled_py = (out_bundled / cb_rel).read_text(encoding="utf-8")
    native_py = (out_native / cb_rel).read_text(encoding="utf-8")
    assert "CXAS_SHARED_BUNDLE" in bundled_py
    assert "CXAS_SHARED_BUNDLE" not in native_py
    assert strip_shared_bundle_text(bundled_py) == native_py

    clean_b, dig_b, mods_b, _ = inspect_entrypoint_bundle(bundled_py)
    clean_n, dig_n, mods_n, _ = inspect_entrypoint_bundle(
        native_py, app_dir=out_native
    )
    assert clean_b == clean_n
    assert dig_b == dig_n
    assert mods_b == mods_n

    res_b = extract_normalized_resources(out_bundled)
    res_n = extract_normalized_resources(out_native)
    assert (
        res_b["agents/root_agent"].sha256 == res_n["agents/root_agent"].sha256
    )


def test_lock_and_drift_cli_json_accept_live_and_git_mirror(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    core, man = _make_sample_source(tmp_path / "src")
    out = tmp_path / "dist" / "cymbal_web"
    build_app(core, man, "cymbal_retail", "WEB", "dev", out)

    lock_path = tmp_path / "dist" / "cymbal_web.lock.json"
    _run_cli(["lock", "--bundle", str(out)])
    assert lock_path.is_file()
    assert "LOCKED cymbal_web" in capsys.readouterr().out

    # Create drifted live copy with modified guide_agent + shared python_lib edit
    live = tmp_path / "live"
    shutil.copytree(out, live)
    (live / "agents" / "guide_agent" / "instruction.txt").write_text(
        "Live Console edited guide instruction.\n", encoding="utf-8"
    )
    (live / "python_lib" / "helper.py").write_text(
        'TAG = "live_hotfix"\ndef tag() -> str:\n    return TAG\n',
        encoding="utf-8",
    )
    bundle_shared_python(live)

    # Without resolution, cxas drift --json exits 1 and reports DRIFT
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        _run_cli(
            [
                "drift",
                "--app",
                "cymbal_web",
                "--dist",
                str(out),
                "--live",
                str(live),
                "--lockfile",
                str(lock_path),
                "--json",
            ]
        )
    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is False
    by_id = {r["resource_id"]: r for r in payload["resources"]}
    assert by_id["agents/guide_agent"]["status"] == "DRIFT"
    assert by_id["agents/root_agent"]["status"] == "DRIFT"

    # Accept live edits and commit live snapshot to git mirror
    mirror = tmp_path / "git_mirror"
    capsys.readouterr()
    _run_cli(
        [
            "drift",
            "--app",
            "cymbal_web",
            "--dist",
            str(out),
            "--live",
            str(live),
            "--lockfile",
            str(lock_path),
            "--accept-live",
            "agents/guide_agent",
            "agents/root_agent",
            "--git-mirror",
            str(mirror),
            "--json",
        ]
    )
    resolved_payload = json.loads(capsys.readouterr().out)
    assert resolved_payload["passed"] is True
    assert resolved_payload["git_mirror_commit"]
    assert "Live Console edited" in (
        out / "agents" / "guide_agent" / "instruction.txt"
    ).read_text(encoding="utf-8")
    assert "live_hotfix" in (out / "python_lib" / "helper.py").read_text(
        encoding="utf-8"
    )

    # Re-running drift after accept-live is CLEAN
    recheck = check_drift("cymbal_web", out, live, lockfile_path=lock_path)
    assert recheck.is_clean


def test_drift_conflict_markers_and_selector_errors(tmp_path: Path) -> None:
    core, man = _make_sample_source(tmp_path / "src")
    out = tmp_path / "dist" / "cymbal_web"
    build_app(core, man, "cymbal_retail", "WEB", "dev", out)
    lock_path = write_lockfile(out)

    live = tmp_path / "live"
    shutil.copytree(out, live)
    (out / "agents" / "guide_agent" / "instruction.txt").write_text(
        "Ours edited line.\n", encoding="utf-8"
    )
    (live / "agents" / "guide_agent" / "instruction.txt").write_text(
        "Live edited line.\n", encoding="utf-8"
    )
    conflicts_dir = tmp_path / "conflicts"

    report = check_drift(
        "cymbal_web",
        out,
        live,
        lockfile_path=lock_path,
        conflicts_dir=conflicts_dir,
    )
    assert report.by_id["agents/guide_agent"].status == "CONFLICT"
    diff_path = report.by_id["agents/guide_agent"].conflict_path
    assert diff_path is not None
    assert diff_path.is_file()
    diff_text = diff_path.read_text(encoding="utf-8")
    assert "<<<<<<< live" in diff_text
    assert ">>>>>>> ours" in diff_text

    # Overlap between --accept-live and --force-ours raises BuildGateError
    with pytest.raises(BuildGateError, match="cannot specify both"):
        check_drift(
            "cymbal_web",
            out,
            live,
            lockfile_path=lock_path,
            accept_live=["agents/guide_agent"],
            force_ours=["agents/guide_agent"],
        )


def test_drift_rejects_symlinks_path_traversal_and_corrupt_inputs(
    tmp_path: Path,
) -> None:
    core, man = _make_sample_source(tmp_path / "src")
    out = tmp_path / "dist" / "cymbal_web"
    build_app(core, man, "cymbal_retail", "WEB", "dev", out)
    lock_path = write_lockfile(out)

    # Path traversal or flag-like app_id rejected
    for bad_id in ("../escape", "--orphan", "a\\b", ""):
        with pytest.raises(BuildGateError):
            check_drift(bad_id, out, out, lockfile_path=lock_path)

    # Symlink inside app bundle rejected
    outside = tmp_path / "secret.txt"
    outside.write_text("SECRET\n", encoding="utf-8")
    sym_app = tmp_path / "sym_app"
    shutil.copytree(out, sym_app)
    (sym_app / "global_instruction.txt").unlink()
    os.symlink(outside, sym_app / "global_instruction.txt")
    with pytest.raises(BuildGateError, match="Symlinks"):
        extract_normalized_resources(sym_app)

    # Corrupt JSON raises BuildGateError
    bad_json_app = tmp_path / "bad_json"
    shutil.copytree(out, bad_json_app)
    (bad_json_app / "app.json").write_text("{not-json", encoding="utf-8")
    with pytest.raises(BuildGateError, match="Corrupt JSON"):
        extract_normalized_resources(bad_json_app)

    # Non-UTF-8 file raises BuildGateError
    bad_utf8_app = tmp_path / "bad_utf8"
    shutil.copytree(out, bad_utf8_app)
    (bad_utf8_app / "global_instruction.txt").write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(BuildGateError, match="Failed to read"):
        extract_normalized_resources(bad_utf8_app)
