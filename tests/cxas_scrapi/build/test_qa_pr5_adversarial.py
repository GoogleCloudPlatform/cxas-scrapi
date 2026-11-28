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

"""Adversarial E2E and edge-case QA tests for PR 5 (Lockfiles & 3-Way Drift).

Contains:
- Passing adversarial edge-case, bundler inspection, lockfile, 3-way drift,
  pre-push gate, and CLI E2E regression tests (``test_qa_pr5_...``).
- Failing reproduction tests for open QA defects (``test_qa_pr5_001_...``
  through ``test_qa_pr5_007_...``), which must turn green once fixed.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cxas_scrapi.build.builder import build_app
from cxas_scrapi.build.bundle_io import BuildGateError
from cxas_scrapi.build.drift import (
    check_drift,
    extract_normalized_resources,
    write_lockfile,
)
from cxas_scrapi.cli.app import _app_push, _export_live_app_to_dir
from cxas_scrapi.core.python_bundler import (
    BundleDigestMismatchError,
    PythonBundlerError,
    bundle_shared_python,
    inspect_entrypoint_bundle,
    strip_shared_bundle_text,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(_REPO_ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "cxas_scrapi.cli.main", *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _make_simple_app(
    app_dir: Path,
    *,
    display_name: str = "TestApp",
    agent_instruction: str = "Hello user.\n",
    global_instruction: str = "Global rules.\n",
) -> Path:
    (app_dir / "agents" / "root_agent").mkdir(parents=True, exist_ok=True)
    (app_dir / "app.json").write_text(
        json.dumps({"displayName": display_name, "rootAgent": "root_agent"}),
        encoding="utf-8",
    )
    (app_dir / "global_instruction.txt").write_text(
        global_instruction, encoding="utf-8"
    )
    (app_dir / "agents" / "root_agent" / "root_agent.json").write_text(
        json.dumps({"displayName": "root_agent"}), encoding="utf-8"
    )
    (app_dir / "agents" / "root_agent" / "instruction.txt").write_text(
        agent_instruction, encoding="utf-8"
    )
    return app_dir


def _make_brand_workspace(tmp_path: Path) -> tuple[Path, Path]:
    core = tmp_path / "core"
    _make_simple_app(core, display_name="@@profile.display_name@@")
    (core / "python_lib").mkdir(parents=True)
    (core / "python_lib" / "__init__.py").write_text("", encoding="utf-8")
    (core / "python_lib" / "helper.py").write_text(
        'TAG = "@@profile.profile_id@@"\n', encoding="utf-8"
    )
    cb = (
        core
        / "agents"
        / "root_agent"
        / "before_model_callbacks"
        / "cb_1"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)
    cb.write_text(
        "import python_lib.helper\ndef run():\n    return python_lib.helper.TAG\n",
        encoding="utf-8",
    )

    manifests = tmp_path / "manifests"
    bdir = manifests / "profiles" / "brand_a"
    bdir.mkdir(parents=True)
    (bdir / "identity.yaml").write_text(
        "profile_id: brand_a\ndisplay_name: Brand A\n", encoding="utf-8"
    )
    (bdir / "channel_policy.yaml").write_text(
        "profile_id: brand_a\nchannels:\n  WEB:\n    supported_modalities: [TEXT]\n",
        encoding="utf-8",
    )
    (manifests / "envs").mkdir(parents=True)
    (manifests / "envs" / "prod.yaml").write_text(
        "environment: prod\n", encoding="utf-8"
    )
    return core, manifests


def _zip_dir_bytes(src_dir: Path, wrapper: str = "agent") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for fpath in sorted(src_dir.rglob("*")):
            if fpath.is_file():
                rel = fpath.relative_to(src_dir).as_posix()
                zf.writestr(f"{wrapper}/{rel}", fpath.read_bytes())
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Passing adversarial & E2E regression tests
# ---------------------------------------------------------------------------


def test_qa_pr5_bundler_inspect_and_strip_text_edge_cases(
    tmp_path: Path,
) -> None:
    """Probe strip_shared_bundle_text & inspect_entrypoint_bundle across edge cases."""
    app_dir = tmp_path / "app"
    (app_dir / "python_lib" / "sub").mkdir(parents=True)
    (app_dir / "python_lib" / "sub" / "mod.py").write_text(
        "VAL = 7\n", encoding="utf-8"
    )
    (app_dir / "python_lib" / "a.py").write_text(
        "import python_lib.b\nA = 1\n", encoding="utf-8"
    )
    (app_dir / "python_lib" / "b.py").write_text(
        "import python_lib.a\nB = 2\n", encoding="utf-8"
    )
    (app_dir / "python_lib" / "typing_only.py").write_text(
        "import nonexistent_pkg\nclass T:\n    pass\n", encoding="utf-8"
    )

    ep_orig = (
        "from __future__ import annotations; import python_lib.sub.mod\n"
        "from typing import TYPE_CHECKING\n"
        "import python_lib.a\n"
        "if TYPE_CHECKING:\n"
        "    from python_lib.typing_only import T\n"
        "def cb(x: T | None = None):\n"
        "    return python_lib.sub.mod.VAL + python_lib.a.A\n"
    )
    ep = (
        app_dir
        / "agents"
        / "root_agent"
        / "before_model_callbacks"
        / "cb_1"
        / "python_code.py"
    )
    ep.parent.mkdir(parents=True)
    ep.write_text(ep_orig, encoding="utf-8")

    # Inspect unbundled: skips TYPE_CHECKING, captures namespace subpackage & circular closure
    clean_unb, digest_unb, mods_unb, _pkgs_unb = inspect_entrypoint_bundle(
        ep_orig, ep, app_dir=app_dir
    )
    assert clean_unb == ep_orig
    assert digest_unb is not None
    assert "python_lib.sub.mod" in mods_unb
    assert "python_lib.a" in mods_unb
    assert "python_lib.b" in mods_unb
    assert "python_lib.typing_only" not in mods_unb

    # Bundle and inspect bundled: digest matches unbundled inspection
    bundle_shared_python(app_dir)
    bundled = ep.read_text(encoding="utf-8")
    clean_bun, digest_bun, mods_bun, _pkgs_bun = inspect_entrypoint_bundle(
        bundled, ep, app_dir=app_dir
    )
    assert digest_bun == digest_unb
    assert set(mods_bun) == set(mods_unb)
    assert strip_shared_bundle_text(bundled, ep) == clean_bun

    # Tampered digest, duplicate blocks, and unclosed markers rejected
    tampered = bundled.replace("VAL = 7", "VAL = 999", 1)
    with pytest.raises(BundleDigestMismatchError):
        strip_shared_bundle_text(tampered, ep)
    with pytest.raises(BundleDigestMismatchError):
        inspect_entrypoint_bundle(tampered, ep, app_dir=app_dir)
    assert strip_shared_bundle_text(tampered, ep, force=True) == clean_bun

    end_marker = "# === END CXAS_SHARED_BUNDLE ===\n"
    block = bundled[
        bundled.index("# === BEGIN CXAS_SHARED_BUNDLE") : bundled.index(
            end_marker
        )
        + len(end_marker)
    ]
    with pytest.raises(BundleDigestMismatchError, match="2 shared bundle"):
        strip_shared_bundle_text(block + bundled, ep)
    with pytest.raises(BundleDigestMismatchError, match="Malformed"):
        strip_shared_bundle_text(bundled.replace(end_marker, "", 1), ep)


def test_qa_pr5_canonicalization_crlf_malformed_non_utf8_symlinks_and_openapi(
    tmp_path: Path,
) -> None:
    """Verify CRLF normalization, OpenAPI env_var round-trip, and strict error/symlink rejection."""
    # CRLF vs LF + OpenAPI $env_var with environment.json URL normalizes identically
    ours = _make_simple_app(tmp_path / "ours")
    live = _make_simple_app(tmp_path / "live")
    (ours / "agents" / "root_agent" / "instruction.txt").write_bytes(
        b"Line 1\nLine 2\n"
    )
    (live / "agents" / "root_agent" / "instruction.txt").write_bytes(
        b"Line 1\r\nLine 2\r\n"
    )

    for d, tool_json, oa_url in (
        (
            ours,
            {
                "displayName": "t1",
                "openApiTool": {
                    "openApiSchema": "tools/t1/open_api_tool/open_api_schema.yaml",
                    "url": "https://api.example.com/v1",
                },
            },
            "https://api.example.com",
        ),
        (
            live,
            {
                "displayName": "t1",
                "openApiTool": {
                    "openApiSchema": "tools/t1/open_api_tool/open_api_schema.yaml"
                },
            },
            "$env_var",
        ),
    ):
        (d / "tools" / "t1" / "open_api_tool").mkdir(parents=True)
        (d / "tools" / "t1" / "t1.json").write_text(
            json.dumps(tool_json), encoding="utf-8"
        )
        (
            d / "tools" / "t1" / "open_api_tool" / "open_api_schema.yaml"
        ).write_text(
            f"openapi: 3.0.0\nservers:\n  - url: {oa_url}\npaths: {{}}\n",
            encoding="utf-8",
        )
    (live / "environment.json").write_text(
        json.dumps(
            {
                "tools": {
                    "t1": {"openApiTool": {"url": "https://api.example.com/v1"}}
                }
            }
        ),
        encoding="utf-8",
    )

    res_o = extract_normalized_resources(ours)
    res_l = extract_normalized_resources(live)
    assert (
        res_o["agents/root_agent"].sha256 == res_l["agents/root_agent"].sha256
    )
    assert res_o["tools/t1"].sha256 == res_l["tools/t1"].sha256

    # Malformed JSON rejected
    bad_json = _make_simple_app(tmp_path / "bad_json")
    (bad_json / "app.json").write_text("{not-json", encoding="utf-8")
    with pytest.raises(BuildGateError, match="Corrupt JSON"):
        extract_normalized_resources(bad_json)

    # Non-UTF-8 instruction file rejected
    bad_utf8 = _make_simple_app(tmp_path / "bad_utf8")
    (bad_utf8 / "global_instruction.txt").write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(BuildGateError, match="Failed to read"):
        extract_normalized_resources(bad_utf8)

    # Symlink in app_dir, agent, and python_lib rejected
    outside = tmp_path / "outside.py"
    outside.write_text("SECRET = 1\n", encoding="utf-8")
    sym_app = _make_simple_app(tmp_path / "sym_app")
    (sym_app / "python_lib").mkdir()
    os.symlink(outside, sym_app / "python_lib" / "leak.py")
    cb = (
        sym_app
        / "agents"
        / "root_agent"
        / "before_model_callbacks"
        / "c1"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)
    cb.write_text("import python_lib.leak\n", encoding="utf-8")
    with pytest.raises(BuildGateError, match="Symlinks are not permitted"):
        extract_normalized_resources(sym_app)

    sym_dir = tmp_path / "sym_dir"
    os.symlink(ours, sym_dir)
    with pytest.raises(BuildGateError, match="Symlinks are not permitted"):
        extract_normalized_resources(sym_dir)


def test_qa_pr5_lockfile_corrupt_symlink_and_build_write_locks_isolation(
    tmp_path: Path,
) -> None:
    """Verify corrupt/symlinked lockfiles fail fast and build --write-locks keeps lockfile outside bundle/ZIP."""
    core, manifests = _make_brand_workspace(tmp_path)
    out_dir = tmp_path / "dist" / "brand_a_web"

    rep = build_app(
        core,
        manifests,
        "brand_a",
        "WEB",
        "prod",
        out_dir,
        package_zip=True,
        write_lock=True,
    )
    assert rep.lockfile_path is not None
    assert rep.lockfile_path.is_file()
    assert rep.lockfile_path.parent == out_dir.parent
    assert not list(out_dir.rglob("*.lock.json"))
    assert rep.zip_path is not None
    with zipfile.ZipFile(rep.zip_path) as zf:
        assert not any(n.endswith(".lock.json") for n in zf.namelist())

    # Corrupt JSON, non-dict resources, non-string sha256, and symlinked lockfile rejected
    for name, raw in (
        ("corrupt.lock.json", "{bad"),
        (
            "bad_res.lock.json",
            json.dumps({"schema_version": "1.0", "resources": []}),
        ),
        (
            "bad_sha.lock.json",
            json.dumps(
                {
                    "schema_version": "1.0",
                    "resources": {"app": {"sha256": 123, "normalized": "{}"}},
                }
            ),
        ),
        (
            "bad_id.lock.json",
            json.dumps(
                {
                    "schema_version": "1.0",
                    "resources": {
                        "../escape": {"sha256": "a" * 64, "normalized": "{}"}
                    },
                }
            ),
        ),
    ):
        lf = tmp_path / name
        lf.write_text(raw, encoding="utf-8")
        with pytest.raises(BuildGateError):
            check_drift("brand_a_web", out_dir, out_dir, lockfile_path=lf)

    sym_lock = tmp_path / "sym.lock.json"
    os.symlink(rep.lockfile_path, sym_lock)
    with pytest.raises(BuildGateError, match="Symlinks are not permitted"):
        write_lockfile(out_dir, lockfile_path=sym_lock)
    with pytest.raises(BuildGateError, match="Symlinks are not permitted"):
        check_drift("brand_a_web", out_dir, out_dir, lockfile_path=sym_lock)


def test_qa_pr5_three_way_all_states_and_no_lockfile_baseline(
    tmp_path: Path,
) -> None:
    """Exercise all 3-way drift states plus no-lockfile baseline and conflict markers."""
    base_d = tmp_path / "base"
    ours_d = tmp_path / "ours"
    live_d = tmp_path / "live"
    for d in (base_d, ours_d, live_d):
        d.mkdir(parents=True)
        (d / "app.json").write_text(
            json.dumps({"displayName": "App"}), encoding="utf-8"
        )
        for name in (
            "clean_a",
            "ours_edit",
            "live_edit",
            "both_edit",
            "del_live",
        ):
            (d / "agents" / name).mkdir(parents=True)
            (d / "agents" / name / f"{name}.json").write_text(
                json.dumps({"displayName": name}), encoding="utf-8"
            )
            (d / "agents" / name / "instruction.txt").write_text(
                "line 1\nbase\nline 3\n", encoding="utf-8"
            )

    lock_path = write_lockfile(base_d)
    (ours_d / "agents" / "ours_edit" / "instruction.txt").write_text(
        "line 1\nours\nline 3\n", encoding="utf-8"
    )
    (live_d / "agents" / "live_edit" / "instruction.txt").write_text(
        "line 1\nlive\nline 3\n", encoding="utf-8"
    )
    (ours_d / "agents" / "both_edit" / "instruction.txt").write_text(
        "line 1\nours\nline 3\n", encoding="utf-8"
    )
    (live_d / "agents" / "both_edit" / "instruction.txt").write_text(
        "line 1\nlive\nline 3\n", encoding="utf-8"
    )
    shutil.rmtree(live_d / "agents" / "del_live")

    (ours_d / "agents" / "new_ours").mkdir()
    (ours_d / "agents" / "new_ours" / "new_ours.json").write_text(
        json.dumps({"displayName": "new_ours"}), encoding="utf-8"
    )
    (live_d / "agents" / "orphan_live").mkdir()
    (live_d / "agents" / "orphan_live" / "orphan_live.json").write_text(
        json.dumps({"displayName": "orphan_live"}), encoding="utf-8"
    )

    conflicts_dir = tmp_path / "conflicts"
    rep = check_drift(
        "my_app",
        ours_d,
        live_d,
        lockfile_path=lock_path,
        conflicts_dir=conflicts_dir,
    )
    by_id = rep.by_id
    assert by_id["app"].status == "CLEAN"
    assert by_id["agents/clean_a"].status == "CLEAN"
    assert by_id["agents/ours_edit"].status == "CLEAN"
    assert by_id["agents/new_ours"].status == "CLEAN"
    assert by_id["agents/live_edit"].status == "DRIFT"
    assert by_id["agents/del_live"].status == "DRIFT"
    assert by_id["agents/orphan_live"].status == "ORPHAN"
    assert by_id["agents/both_edit"].status == "CONFLICT"
    assert by_id["agents/both_edit"].conflict_path is not None
    diff_text = by_id["agents/both_edit"].conflict_path.read_text("utf-8")
    assert "<<<<<<< live" in diff_text
    assert "=======" in diff_text
    assert ">>>>>>> ours" in diff_text

    # No-lockfile baseline: differing shared resources are CONFLICT, live-only is ORPHAN
    rep_nolock = check_drift(
        "my_app", ours_d, live_d, conflicts_dir=tmp_path / "conflicts_nolock"
    )
    assert rep_nolock.by_id["agents/clean_a"].status == "CLEAN"
    assert rep_nolock.by_id["agents/new_ours"].status == "CLEAN"
    assert rep_nolock.by_id["agents/live_edit"].status == "CONFLICT"
    assert rep_nolock.by_id["agents/orphan_live"].status == "ORPHAN"


def test_qa_pr5_accept_live_bundled_python_orphan_and_atomic_rollback(
    tmp_path: Path,
) -> None:
    """Verify --accept-live bundled Python adoption, orphan adoption, and atomic rollback on mid-way failure."""
    ours = _make_simple_app(tmp_path / "ours")
    live = _make_simple_app(tmp_path / "live")
    for d, val in ((ours, "1"), (live, "2")):
        (d / "python_lib").mkdir()
        (d / "python_lib" / "h.py").write_text(f"V = {val}\n", encoding="utf-8")
        cb = (
            d
            / "agents"
            / "root_agent"
            / "before_model_callbacks"
            / "c1"
            / "python_code.py"
        )
        cb.parent.mkdir(parents=True)
        cb.write_text(
            "import python_lib.h\ndef cb():\n    return python_lib.h.V\n",
            encoding="utf-8",
        )
    bundle_shared_python(live)
    shutil.rmtree(live / "python_lib")

    # Add a live-only orphan tool to adopt as well
    (live / "tools" / "live_tool").mkdir(parents=True)
    (live / "tools" / "live_tool" / "live_tool.json").write_text(
        json.dumps({"displayName": "live_tool"}), encoding="utf-8"
    )

    rep = check_drift(
        "ours",
        ours,
        live,
        accept_live=["agents/root_agent", "tools/live_tool"],
    )
    assert rep.by_id["agents/root_agent"].resolved_by == "accept-live"
    assert rep.by_id["tools/live_tool"].resolved_by == "accept-live"
    assert "V = 2" in (ours / "python_lib" / "h.py").read_text("utf-8")
    assert (ours / "tools" / "live_tool" / "live_tool.json").is_file()
    assert check_drift("ours", ours, live).is_clean

    # Atomic rollback: when 2nd accepted resource fails, 1st resource & lockfile are restored
    rb_ours = _make_simple_app(tmp_path / "rb_ours")
    rb_live = _make_simple_app(tmp_path / "rb_live")
    for d, txt in ((rb_ours, "ours"), (rb_live, "live")):
        for name in ("a1", "a2"):
            (d / "agents" / name).mkdir(parents=True)
            (d / "agents" / name / f"{name}.json").write_text(
                json.dumps({"displayName": name}), encoding="utf-8"
            )
            (d / "agents" / name / "instruction.txt").write_text(
                f"{txt} {name}\n", encoding="utf-8"
            )
    # Corrupt JSON in live a2's sub-file so adoption fails after a1 is already adopted
    (rb_live / "agents" / "a2" / "sub").mkdir()
    (rb_live / "agents" / "a2" / "sub" / "broken.json").write_text(
        "{not-json", encoding="utf-8"
    )
    lock_rb = write_lockfile(rb_ours)
    orig_lock_text = lock_rb.read_text("utf-8")

    with pytest.raises(BuildGateError, match="Corrupt JSON"):
        check_drift(
            "rb_ours",
            rb_ours,
            rb_live,
            lockfile_path=lock_rb,
            accept_live=["agents/a1", "agents/a2"],
        )
    assert (rb_ours / "agents" / "a1" / "instruction.txt").read_text(
        "utf-8"
    ) == "ours a1\n"
    assert lock_rb.read_text("utf-8") == orig_lock_text


def test_qa_pr5_push_check_drift_zip_slip_abort_force_and_native_env(
    tmp_path: Path,
) -> None:
    """Verify _export_live_app_to_dir Zip-Slip/symlink defense and _app_push --check-drift behavior."""
    for label, entry_name, is_sym in (
        ("dotdot", "../evil.txt", False),
        ("backslash", "agent\\..\\..\\evil.txt", False),
        ("symlink", "agent/link.py", True),
    ):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            info = zipfile.ZipInfo(entry_name)
            if is_sym:
                info.external_attr = 0o120777 << 16
            zf.writestr(info, "pwn")
        raw = buf.getvalue()
        fake = MagicMock()

        def _fake_export(
            app_name: str,
            local_path: str | None = None,
            export_format: str = "JSON",
            data: bytes = raw,
        ) -> MagicMock:
            if local_path:
                Path(local_path).write_bytes(data)
            return MagicMock(app_content=data)

        fake.export_app = _fake_export
        with pytest.raises(BuildGateError):
            _export_live_app_to_dir(
                fake, "projects/p/locations/l/apps/a", tmp_path / f"ex_{label}"
            )

    # Full _app_push --check-drift: aborts before import_app on conflict, writes conflicts, proceeds with --force-ours + --native-python-env
    ours = _make_simple_app(tmp_path / "push_ours", agent_instruction="ours\n")
    (ours / "python_lib").mkdir()
    (ours / "python_lib" / "h.py").write_text("X = 1\n", encoding="utf-8")
    cb = (
        ours
        / "agents"
        / "root_agent"
        / "before_model_callbacks"
        / "c1"
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True)
    cb.write_text("import python_lib.h\n", encoding="utf-8")
    lock_file = write_lockfile(ours)

    live = _make_simple_app(tmp_path / "push_live", agent_instruction="live\n")
    (live / "python_lib").mkdir()
    (live / "python_lib" / "h.py").write_text("X = 1\n", encoding="utf-8")
    cb_l = (
        live
        / "agents"
        / "root_agent"
        / "before_model_callbacks"
        / "c1"
        / "python_code.py"
    )
    cb_l.parent.mkdir(parents=True)
    cb_l.write_text("import python_lib.h\n", encoding="utf-8")
    # Also mutate ours so it's a true CONFLICT
    (ours / "agents" / "root_agent" / "instruction.txt").write_text(
        "ours_changed\n", encoding="utf-8"
    )

    live_zip = _zip_dir_bytes(live)
    apps_client = MagicMock()

    def _export_live(
        app_name: str,
        local_path: str | None = None,
        export_format: str = "JSON",
    ) -> MagicMock:
        if local_path:
            Path(local_path).write_bytes(live_zip)
        return MagicMock(app_content=live_zip)

    apps_client.export_app = _export_live
    apps_client.import_app.return_value = MagicMock(
        name="projects/p/locations/l/apps/a", warnings=[]
    )

    conflicts_out = tmp_path / "push_conflicts"
    args_block = argparse.Namespace(
        check_drift=True,
        live_dir=None,
        lockfile=str(lock_file),
        conflicts_dir=str(conflicts_out),
        force_ours=[],
        native_python_env=True,
        overwrite=False,
        create_version=False,
    )
    with pytest.raises(SystemExit) as exc:
        _app_push(
            app_dir=str(ours),
            apps_client=apps_client,
            target_app_name="projects/p/locations/l/apps/a",
            args=args_block,
        )
    assert exc.value.code == 1
    apps_client.import_app.assert_not_called()
    assert (conflicts_out / "agents" / "root_agent.diff").is_file()

    # Now pass --force-ours agents/root_agent + --native-python-env: proceeds to import_app
    args_force = argparse.Namespace(
        check_drift=True,
        live_dir=None,
        lockfile=str(lock_file),
        conflicts_dir=str(conflicts_out),
        force_ours=["agents/root_agent"],
        native_python_env=True,
        overwrite=False,
        create_version=False,
    )
    _app_push(
        app_dir=str(ours),
        apps_client=apps_client,
        target_app_name="projects/p/locations/l/apps/a",
        args=args_force,
    )
    apps_client.import_app.assert_called_once()
    uploaded_bytes = apps_client.import_app.call_args.kwargs["app_content"]
    with zipfile.ZipFile(io.BytesIO(uploaded_bytes)) as zf:
        assert "agent/pythonEnvFiles/pythonEnvFiles.yaml" in zf.namelist()
        assert "CXAS_SHARED_BUNDLE" not in zf.read(
            "agent/agents/root_agent/before_model_callbacks/c1/python_code.py"
        ).decode("utf-8")


def test_qa_pr5_cli_e2e_build_write_locks_lock_drift_and_hygiene(
    tmp_path: Path,
) -> None:
    """Real CLI subprocesses for build --write-locks, lock, drift (text + --json), and push --help hygiene."""
    r_push = _run_cli(["push", "--help"])
    assert r_push.returncode == 0
    assert "--check-drift" in r_push.stdout
    assert "--lint" not in r_push.stdout
    assert "--locked" not in r_push.stdout

    core, manifests = _make_brand_workspace(tmp_path)
    out_dir = tmp_path / "dist" / "brand_a_web"
    r_build = _run_cli(
        [
            "build",
            "--core-dir",
            str(core),
            "--manifests-dir",
            str(manifests),
            "--profile",
            "brand_a",
            "--channel",
            "WEB",
            "--env",
            "prod",
            "--out",
            str(out_dir),
            "--write-locks",
            "--zip",
        ]
    )
    assert r_build.returncode == 0, r_build.stderr
    lock_path = out_dir.parent / "brand_a_web.lock.json"
    assert lock_path.is_file()

    # Standalone cxas lock
    custom_lock = tmp_path / "custom.lock.json"
    r_lock = _run_cli(
        ["lock", "--bundle", str(out_dir), "--lockfile", str(custom_lock)]
    )
    assert r_lock.returncode == 0, r_lock.stderr
    assert custom_lock.is_file()

    # Clean drift -> exit 0 (text + --json)
    live_dir = tmp_path / "live_clean"
    shutil.copytree(out_dir, live_dir)
    r_clean = _run_cli(
        [
            "drift",
            "--app",
            "brand_a_web",
            "--dist",
            str(out_dir),
            "--live",
            str(live_dir),
            "--lockfile",
            str(custom_lock),
            "--json",
        ]
    )
    assert r_clean.returncode == 0, r_clean.stderr
    clean_payload = json.loads(r_clean.stdout)
    assert clean_payload["passed"] is True
    assert clean_payload["is_clean"] is True

    # Live drift -> exit 1 (text + --json)
    (live_dir / "agents" / "root_agent" / "instruction.txt").write_text(
        "Console edited!\n", encoding="utf-8"
    )
    r_dirty = _run_cli(
        [
            "drift",
            "--app",
            "brand_a_web",
            "--dist",
            str(out_dir),
            "--live",
            str(live_dir),
            "--lockfile",
            str(custom_lock),
            "--json",
        ]
    )
    assert r_dirty.returncode == 1
    dirty_payload = json.loads(r_dirty.stdout)
    assert dirty_payload["passed"] is False

    # Flag/traversal app_ids rejected by drift CLI without traceback
    for bad_app in ("-evil", "--orphan", "../escape"):
        r_bad = _run_cli(
            [
                "drift",
                "--app",
                bad_app,
                "--dist",
                str(out_dir),
                "--live",
                str(live_dir),
            ]
        )
        assert r_bad.returncode != 0
        assert "Traceback" not in r_bad.stderr


# ---------------------------------------------------------------------------
# Failing reproduction tests for open QA defects (QA-PR5-001 .. QA-PR5-007)
# ---------------------------------------------------------------------------


def test_qa_pr5_001_git_mirror_rejects_dirty_repo_non_empty_dir_and_overlap(
    tmp_path: Path,
) -> None:
    """QA-PR5-001 [BLOCKER]: --git-mirror must not wipe dirty git repos, non-empty non-repo dirs, or ours/live bundles."""
    app_dir = _make_simple_app(tmp_path / "app")

    # Case A: Dirty existing git repo must be rejected without deleting uncommitted files
    dirty_repo = tmp_path / "dirty_repo"
    dirty_repo.mkdir()
    subprocess.run(
        ["git", "init"], cwd=dirty_repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "dev"], cwd=dirty_repo, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "dev@example.com"],
        cwd=dirty_repo,
        check=True,
    )
    (dirty_repo / "tracked.txt").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=dirty_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=dirty_repo,
        check=True,
        capture_output=True,
    )
    (dirty_repo / "tracked.txt").write_text(
        "uncommitted edit\n", encoding="utf-8"
    )
    (dirty_repo / "important_wip.txt").write_text(
        "untracked work\n", encoding="utf-8"
    )

    with pytest.raises(BuildGateError, match=r"dirty|uncommitted|clean|mirror"):
        check_drift("app", app_dir, app_dir, git_mirror=dirty_repo)
    assert (dirty_repo / "important_wip.txt").read_text(
        "utf-8"
    ) == "untracked work\n"
    assert (dirty_repo / "tracked.txt").read_text(
        "utf-8"
    ) == "uncommitted edit\n"

    # Case B: Non-empty non-git directory must be rejected without wiping user files
    non_repo = tmp_path / "user_docs"
    non_repo.mkdir()
    (non_repo / "notes.txt").write_text("keep me\n", encoding="utf-8")
    with pytest.raises(BuildGateError, match=r"non-empty|git|mirror|empty"):
        check_drift("app", app_dir, app_dir, git_mirror=non_repo)
    assert (non_repo / "notes.txt").read_text("utf-8") == "keep me\n"

    # Case C: git_mirror overlapping ours_dir or live_dir must be rejected
    with pytest.raises(BuildGateError, match=r"overlap|mirror"):
        check_drift("app", app_dir, app_dir, git_mirror=app_dir)
    assert (app_dir / "agents" / "root_agent" / "root_agent.json").is_file()


def test_qa_pr5_002_yaml_ces_app_resources_extracted_and_drift_detected(
    tmp_path: Path,
) -> None:
    """QA-PR5-002 [HIGH]: YAML-format CES apps (app.yaml, agents/*/*.yaml, tools/*/*.yaml) must be extracted and detect drift."""
    y_ours = tmp_path / "yaml_ours"
    y_live = tmp_path / "yaml_live"
    for yd, model in (
        (y_ours, "gemini-2.5-flash"),
        (y_live, "gemini-2.5-pro-drifted"),
    ):
        (yd / "agents" / "root_agent").mkdir(parents=True)
        (yd / "tools" / "t1").mkdir(parents=True)
        (yd / "app.yaml").write_text(
            f"displayName: YamlApp\nrootAgent: root_agent\nmodelSettings:\n  model: {model}\n",
            encoding="utf-8",
        )
        (yd / "agents" / "root_agent" / "root_agent.yaml").write_text(
            f"displayName: root_agent\nmodelSettings:\n  model: {model}\n",
            encoding="utf-8",
        )
        (yd / "agents" / "root_agent" / "instruction.txt").write_text(
            "Same instruction.\n", encoding="utf-8"
        )
        (yd / "tools" / "t1" / "t1.yaml").write_text(
            f"displayName: t1\ndescription: {model}\n", encoding="utf-8"
        )

    res_o = extract_normalized_resources(y_ours)
    res_l = extract_normalized_resources(y_live)
    assert "app" in res_o
    assert res_o["app"].sha256 != res_l["app"].sha256
    assert (
        res_o["agents/root_agent"].sha256 != res_l["agents/root_agent"].sha256
    )
    assert res_o["tools/t1"].sha256 != res_l["tools/t1"].sha256


def test_qa_pr5_003_lockfile_validates_schema_version_resources_key_and_sha256_hex(
    tmp_path: Path,
) -> None:
    """QA-PR5-003 [HIGH]: Lockfile loader must reject unsupported schema_version, missing resources key, and non-hex sha256."""
    app_dir = _make_simple_app(tmp_path / "app")

    bad_cases = [
        ("wrong_ver.lock.json", {"schema_version": "99.0", "resources": {}}),
        ("missing_ver.lock.json", {"resources": {}}),
        (
            "missing_resources.lock.json",
            {"schema_version": "1.0", "app_id": "app"},
        ),
        (
            "non_hex_sha.lock.json",
            {
                "schema_version": "1.0",
                "resources": {
                    "app": {"sha256": "not-a-hex-sha256!", "normalized": "{}"}
                },
            },
        ),
    ]
    for fname, payload in bad_cases:
        lf = tmp_path / fname
        lf.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(BuildGateError):
            check_drift("app", app_dir, app_dir, lockfile_path=lf)


def test_qa_pr5_004_conflicts_dir_overlapping_ours_or_live_rejected(
    tmp_path: Path,
) -> None:
    """QA-PR5-004 [HIGH]: --conflicts-dir overlapping ours_dir or live_dir must raise BuildGateError."""
    ours = _make_simple_app(tmp_path / "ours", agent_instruction="ours\n")
    live = _make_simple_app(tmp_path / "live", agent_instruction="live\n")

    for bad_conflicts in (ours, ours / "agents", live, live / "agents"):
        with pytest.raises(BuildGateError, match=r"overlap|conflicts_dir"):
            check_drift("ours", ours, live, conflicts_dir=bad_conflicts)
    assert not list(ours.rglob("*.diff"))
    assert not list(live.rglob("*.diff"))


def test_qa_pr5_005_openapi_concrete_url_change_detected_without_env_json(
    tmp_path: Path,
) -> None:
    """QA-PR5-005 [HIGH]: Changing concrete OpenAPI servers[].url without environment.json must not be masked as CLEAN."""
    ours = _make_simple_app(tmp_path / "oa_ours")
    live = _make_simple_app(tmp_path / "oa_live")
    for d, url in (
        (ours, "https://api.legit.example.com"),
        (live, "https://api.evil.attacker.com"),
    ):
        (d / "tools" / "t1" / "open_api_tool").mkdir(parents=True)
        (d / "tools" / "t1" / "t1.json").write_text(
            json.dumps(
                {
                    "displayName": "t1",
                    "openApiTool": {
                        "openApiSchema": "tools/t1/open_api_tool/open_api_schema.yaml"
                    },
                }
            ),
            encoding="utf-8",
        )
        (
            d / "tools" / "t1" / "open_api_tool" / "open_api_schema.yaml"
        ).write_text(
            f"openapi: 3.0.0\nservers:\n  - url: {url}\npaths: {{}}\n",
            encoding="utf-8",
        )

    res_o = extract_normalized_resources(ours)
    res_l = extract_normalized_resources(live)
    assert res_o["tools/t1"].sha256 != res_l["tools/t1"].sha256


def test_qa_pr5_006_inspect_entrypoint_bundle_rejects_syntax_error(
    tmp_path: Path,
) -> None:
    """QA-PR5-006 [MEDIUM]: inspect_entrypoint_bundle must raise PythonBundlerError on SyntaxError in unbundled entrypoints."""
    app_dir = tmp_path / "syn_app"
    (app_dir / "python_lib").mkdir(parents=True)
    (app_dir / "python_lib" / "h.py").write_text("X = 1\n", encoding="utf-8")
    ep = (
        app_dir
        / "agents"
        / "root_agent"
        / "before_model_callbacks"
        / "c1"
        / "python_code.py"
    )
    ep.parent.mkdir(parents=True)
    bad_code = "import python_lib.h\ndef broken(:\n    pass\n"
    ep.write_text(bad_code, encoding="utf-8")

    with pytest.raises(PythonBundlerError, match="not valid Python"):
        inspect_entrypoint_bundle(bad_code, ep, app_dir=app_dir)


def test_qa_pr5_007_accept_live_preserves_binary_assets_in_resource_dir(
    tmp_path: Path,
) -> None:
    """QA-PR5-007 [MEDIUM]: --accept-live must copy binary assets (e.g. .png) verbatim instead of crashing on UTF-8 decode."""
    ours = _make_simple_app(tmp_path / "bin_ours", agent_instruction="old\n")
    live = _make_simple_app(tmp_path / "bin_live", agent_instruction="new\n")
    png_bytes = b"\x89PNG\r\n\x1a\n\xff\xfe\x00\x01"
    (live / "agents" / "root_agent" / "asset.png").write_bytes(png_bytes)

    rep = check_drift("bin_ours", ours, live, accept_live=["agents/root_agent"])
    assert rep.by_id["agents/root_agent"].resolved_by == "accept-live"
    assert (
        ours / "agents" / "root_agent" / "asset.png"
    ).read_bytes() == png_bytes


def test_qa_pr5_008_accept_live_adopts_yaml_app_and_flat_yaml_resources(
    tmp_path: Path,
) -> None:
    """QA-PR5-008 [HIGH]: --accept-live must adopt live app.yaml and flat *.yaml resources so re-check is CLEAN."""
    y_ours = tmp_path / "y_ours"
    y_live = tmp_path / "y_live"
    for yd, ver in ((y_ours, "v1"), (y_live, "v2")):
        (yd / "agents" / "root_agent").mkdir(parents=True)
        (yd / "tools").mkdir(parents=True)
        (yd / "app.yaml").write_text(
            f"displayName: YamlApp\nrootAgent: root_agent\nmodelSettings:\n  model: {ver}\n",
            encoding="utf-8",
        )
        (yd / "agents" / "root_agent" / "root_agent.yaml").write_text(
            f"displayName: root_agent\nmodelSettings:\n  model: {ver}\n",
            encoding="utf-8",
        )
        (yd / "tools" / "flat_tool.yaml").write_text(
            f"displayName: flat_tool\ndescription: {ver}\n",
            encoding="utf-8",
        )

    lf = write_lockfile(y_ours)
    rep = check_drift(
        "y_ours",
        y_ours,
        y_live,
        lockfile_path=lf,
        accept_live=["app", "agents/root_agent", "tools/flat_tool"],
    )
    assert rep.by_id["app"].resolved_by == "accept-live"
    assert rep.by_id["tools/flat_tool"].resolved_by == "accept-live"

    recheck = check_drift("y_ours", y_ours, y_live, lockfile_path=lf)
    assert recheck.is_clean, [
        (r.resource_id, r.status) for r in recheck.resources
    ]
