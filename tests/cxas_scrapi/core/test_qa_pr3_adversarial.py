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

"""Adversarial E2E and edge-case QA tests for PR 3 (Shared Python Bundler).

Contains:
- Passing adversarial edge-case, isolated runtime, PR2+PR3 interaction,
  runtime helper, and CLI E2E regression tests (``test_qa_pr3_...``).
- Failing reproduction tests for open QA defects (``test_qa_pr3_001_...``
  through ``test_qa_pr3_012_...``), which must turn green once fixed.
"""

from __future__ import annotations

import hashlib
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
import yaml

from cxas_scrapi.build.builder import BuildError, build_app
from cxas_scrapi.cli.app import _app_pull
from cxas_scrapi.core import packaging as packaging_mod
from cxas_scrapi.core.apps import Apps
from cxas_scrapi.core.packaging import package_app_archive
from cxas_scrapi.core.python_bundler import (
    BUNDLE_BEGIN_PREFIX,
    BUNDLE_END_LINE,
    MAX_BUNDLE_BYTES_PER_FILE,
    MAX_TOTAL_ARCHIVE_BYTES,
    BundleDigestMismatchError,
    BundleSizeError,
    PythonBundlerError,
    bundle_shared_python,
    strip_shared_bundles,
    synthesize_python_env_files,
)
from cxas_scrapi.runtime import (
    MissingContentBlock,
    adapt_presentation,
    build_scoped_datastore_filter,
    check_disabled_agent,
    escape_search_filter_literal,
    get_verbatim_content_block,
    resolve_content_block_or_handoff,
)
from cxas_scrapi.utils.linter import (
    Discovery,
    LintConfig,
    LintReport,
    Severity,
    build_context,
    build_registry,
    run_rules,
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


def _purge_python_lib_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    for mod in [
        m
        for m in list(sys.modules)
        if m == "python_lib" or m.startswith("python_lib.")
    ]:
        monkeypatch.delitem(sys.modules, mod, raising=False)


def _make_callback(app_dir: Path, name: str, code: str) -> Path:
    cb = (
        app_dir
        / "agents"
        / "root_agent"
        / "before_model_callbacks"
        / name
        / "python_code.py"
    )
    cb.parent.mkdir(parents=True, exist_ok=True)
    cb.write_text(code, encoding="utf-8")
    return cb


def _make_brand_workspace(
    tmp_path: Path, *, display_name: str = 'O\'Brien "Café"\nLine2 — 日本語'
) -> tuple[Path, Path]:
    core = tmp_path / "core"
    (core / "python_lib").mkdir(parents=True)
    (core / "python_lib" / "__init__.py").write_text("", encoding="utf-8")
    (core / "python_lib" / "branding.py").write_text(
        "from __future__ import annotations\n"
        'BRAND_NAME = "@@profile.display_name@@"\n'
        "def greet(user: str) -> str:\n"
        '    return f"Welcome to {BRAND_NAME}, {user}!"\n',
        encoding="utf-8",
    )
    (core / "python_lib" / "web_only.py").write_text(
        'WIDGET = "rich_card"\n', encoding="utf-8"
    )
    cb = _make_callback(
        core,
        "cb_1",
        "from __future__ import annotations\n"
        "from python_lib.branding import greet as brand_greet\n"
        "{@startChannel_WEB}\n"
        "import python_lib.web_only\n"
        "{@endChannel_WEB}\n"
        'LABEL = "@@profile.display_name@@"\n'
        "def run() -> tuple[str, str]:\n"
        '    return (LABEL, brand_greet("Alice"))\n',
    )
    assert cb.is_file()
    (core / "agents" / "root_agent" / "root_agent.json").write_text(
        json.dumps({"displayName": "root_agent"}), encoding="utf-8"
    )
    (core / "agents" / "root_agent" / "instruction.txt").write_text(
        "Hi @@profile.display_name@@.\n", encoding="utf-8"
    )
    (core / "app.json").write_text(
        json.dumps(
            {
                "displayName": "App @@profile.profile_id@@",
                "rootAgent": "root_agent",
                "variableDeclarations": [
                    {
                        "name": "feature_flag",
                        "schema": {"type": "BOOLEAN", "default": "$env_var"},
                    },
                    {
                        "name": "api_host",
                        "schema": {"type": "STRING", "default": "$env_var"},
                    },
                ],
                "loggingSettings": {
                    "bigqueryExportSettings": {"enabled": "$env_var"}
                },
            }
        ),
        encoding="utf-8",
    )
    (core / "environment.json").write_text(
        json.dumps(
            {
                "app": {
                    "variableDeclarations": [
                        {"name": "feature_flag", "schema": {"default": True}},
                        {
                            "name": "api_host",
                            "schema": {"default": "api.prod.example.com"},
                        },
                    ],
                    "loggingSettings": {
                        "bigqueryExportSettings": {"enabled": "true"}
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    manifests = tmp_path / "manifests"
    bdir = manifests / "profiles" / "brand_x"
    bdir.mkdir(parents=True)
    (bdir / "identity.yaml").write_text(
        yaml.safe_dump({"profile_id": "brand_x", "display_name": display_name}),
        encoding="utf-8",
    )
    (bdir / "channel_policy.yaml").write_text(
        "profile_id: brand_x\n"
        "channels:\n"
        "  WEB:\n"
        "    supported_modalities: [TEXT]\n"
        "  VOICE_TELEPHONY:\n"
        "    supported_modalities: [AUDIO]\n",
        encoding="utf-8",
    )
    (manifests / "envs").mkdir(parents=True)
    (manifests / "envs" / "prod.yaml").write_text(
        "environment: prod\n", encoding="utf-8"
    )
    return core, manifests


# ---------------------------------------------------------------------------
# Passing adversarial edge-case & E2E regression tests
# ---------------------------------------------------------------------------


def test_qa_pr3_isolated_exec_future_relative_transitive_aliased(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Isolated exec() with __future__, relative, transitive, and aliased imports."""
    app_dir = tmp_path / "app"
    (app_dir / "python_lib" / "sub").mkdir(parents=True)
    (app_dir / "python_lib" / "__init__.py").write_text("", encoding="utf-8")
    (app_dir / "python_lib" / "sub" / "__init__.py").write_text(
        "", encoding="utf-8"
    )
    (app_dir / "python_lib" / "base.py").write_text(
        "from __future__ import annotations\nBASE: int = 3\n",
        encoding="utf-8",
    )
    (app_dir / "python_lib" / "sub" / "mod.py").write_text(
        "from __future__ import annotations\n"
        "from .. import base\n"
        "def fn(x: int) -> int:\n"
        "    return x * base.BASE\n",
        encoding="utf-8",
    )
    (app_dir / "python_lib" / "c.py").write_text(
        "from __future__ import annotations\n"
        "from .sub.mod import fn\n"
        "def c_fn(x: int) -> int:\n"
        "    return fn(x) + 1\n",
        encoding="utf-8",
    )
    (app_dir / "python_lib" / "b.py").write_text(
        "from . import c\n"
        "def b_fn(x: int) -> int:\n"
        "    return c.c_fn(x) + 10\n",
        encoding="utf-8",
    )
    (app_dir / "python_lib" / "a.py").write_text(
        "from python_lib.b import b_fn as bf\n"
        "def f(x: int) -> int:\n"
        "    return bf(x) + 100\n",
        encoding="utf-8",
    )

    ep = app_dir / "tools" / "calc" / "python_function" / "python_code.py"
    ep.parent.mkdir(parents=True)
    ep.write_text(
        '"""Tool docstring."""\n'
        "from __future__ import annotations\n"
        "import python_lib.a as a_mod\n"
        "from python_lib.a import f as g\n"
        "def run() -> int:\n"
        "    return a_mod.f(5) + g(5)\n",
        encoding="utf-8",
    )

    bundle_shared_python(app_dir)
    bundled = ep.read_text(encoding="utf-8")
    assert bundled.index("from __future__ import annotations") < bundled.index(
        "# === BEGIN CXAS_SHARED_BUNDLE"
    )

    shutil.rmtree(app_dir / "python_lib")
    _purge_python_lib_modules(monkeypatch)
    ns: dict[str, object] = {}
    exec(bundled, ns)
    assert ns["run"]() == 2 * ((5 * 3 + 1) + 10 + 100)  # type: ignore[operator]


def test_qa_pr3_bundle_size_64kib_and_archive_32mib_exact_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify exact 64 KiB bundle cap and 32 MiB archive cap boundaries (+1 fails)."""
    assert MAX_BUNDLE_BYTES_PER_FILE == 64 * 1024
    assert MAX_TOTAL_ARCHIVE_BYTES == 32 * 1024 * 1024

    app_dir = tmp_path / "b64"
    (app_dir / "python_lib").mkdir(parents=True)
    (app_dir / "python_lib" / "big.py").write_text("X = ''\n", encoding="utf-8")
    cb = _make_callback(app_dir, "cb1", "import python_lib.big\n")

    overhead = bundle_shared_python(app_dir)[str(cb)]
    pad = MAX_BUNDLE_BYTES_PER_FILE - overhead
    assert pad > 0

    (app_dir / "python_lib" / "big.py").write_text(
        "X = '" + ("a" * pad) + "'\n", encoding="utf-8"
    )
    exact = bundle_shared_python(app_dir)[str(cb)]
    assert exact == MAX_BUNDLE_BYTES_PER_FILE

    (app_dir / "python_lib" / "big.py").write_text(
        "X = '" + ("a" * (pad + 1)) + "'\n", encoding="utf-8"
    )
    with pytest.raises(BundleSizeError):
        bundle_shared_python(app_dir)

    # Archive total-size exact boundary (monkeypatched cap for fast hermetic check)
    arch_app = tmp_path / "arch"
    arch_app.mkdir()
    payload = '{"displayName":"A"}'
    (arch_app / "app.json").write_text(payload, encoding="utf-8")
    exact_bytes = len(payload.encode("utf-8"))

    monkeypatch.setattr(packaging_mod, "MAX_TOTAL_ARCHIVE_BYTES", exact_bytes)
    out_zip = package_app_archive(arch_app, tmp_path / "exact.zip")
    assert out_zip.is_file()

    monkeypatch.setattr(
        packaging_mod, "MAX_TOTAL_ARCHIVE_BYTES", exact_bytes - 1
    )
    with pytest.raises(BundleSizeError):
        package_app_archive(arch_app, tmp_path / "over.zip")


def test_qa_pr3_idempotency_tamper_markers_and_crlf(tmp_path: Path) -> None:
    """Verify bundle->strip->bundle idempotency, tamper detection, nested/duplicate markers, CRLF."""
    app_dir = tmp_path / "app"
    (app_dir / "python_lib").mkdir(parents=True)
    (app_dir / "python_lib" / "h.py").write_text("VAL = 1\n", encoding="utf-8")
    orig = "import python_lib.h\ndef cb():\n    return python_lib.h.VAL\n"
    cb = _make_callback(app_dir, "cb1", orig)

    bundle_shared_python(app_dir)
    bundled_1 = cb.read_text(encoding="utf-8")

    # Idempotent re-bundle without stripping
    bundle_shared_python(app_dir)
    assert cb.read_text(encoding="utf-8") == bundled_1

    # Round-trip bundle -> strip -> bundle produces identical bytes and digest
    assert strip_shared_bundles(app_dir) == [cb]
    assert cb.read_text(encoding="utf-8") == orig
    bundle_shared_python(app_dir)
    assert cb.read_text(encoding="utf-8") == bundled_1

    # CRLF line endings strip cleanly
    cb.write_bytes(bundled_1.replace("\n", "\r\n").encode("utf-8"))
    assert strip_shared_bundles(app_dir) == [cb]
    assert cb.read_text(encoding="utf-8") == orig

    # Nested markers rejected when force=False
    bundle_shared_python(app_dir)
    end_marker = "# === END CXAS_SHARED_BUNDLE ==="
    nested = bundled_1.replace(end_marker, f"{end_marker}\n{end_marker}", 1)
    cb.write_text(nested, encoding="utf-8")
    with pytest.raises(BundleDigestMismatchError):
        strip_shared_bundles(app_dir, force=False)

    # Single-byte tamper inside marker rejected when force=False, stripped when force=True
    tampered = bundled_1.replace("VAL = 1", "VAL = 2", 1)
    cb.write_text(tampered, encoding="utf-8")
    with pytest.raises(BundleDigestMismatchError):
        strip_shared_bundles(app_dir, force=False)
    assert strip_shared_bundles(app_dir, force=True) == [cb]
    assert cb.read_text(encoding="utf-8") == orig


def test_qa_pr3_native_python_env_collision_safe_ids_and_no_leak(
    tmp_path: Path,
) -> None:
    """Verify --native-python-env collision-safe <=36-char IDs and zero python_lib/ leak."""
    app_dir = tmp_path / "app"
    plib = app_dir / "python_lib"
    (plib / "very_long_shared_namespace_segment").mkdir(parents=True)
    for i in range(4):
        (
            plib
            / "very_long_shared_namespace_segment"
            / f"module_variant_{i}.py"
        ).write_text(f"V = {i}\n", encoding="utf-8")
    orig = (
        "import python_lib.very_long_shared_namespace_segment.module_variant_0\n"
        "def cb():\n    return 0\n"
    )
    cb = _make_callback(app_dir, "cb1", orig)
    (app_dir / "app.json").write_text("{}", encoding="utf-8")

    zip_path = package_app_archive(
        app_dir, tmp_path / "native.zip", native_python_env=True
    )
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        assert not any(n.startswith("python_lib/") for n in names)
        assert "pythonEnvFiles/pythonEnvFiles.yaml" in names
        cb_content = zf.read(
            "agents/root_agent/before_model_callbacks/cb1/python_code.py"
        ).decode("utf-8")
        assert cb_content == orig
        assert "CXAS_SHARED_BUNDLE" not in cb_content

        manifest = yaml.safe_load(
            zf.read("pythonEnvFiles/pythonEnvFiles.yaml").decode("utf-8")
        )
        entries = manifest["pythonEnvFiles"]
        ids = [e["name"] for e in entries]
        assert len(ids) == 4
        assert len(set(ids)) == 4
        assert all(5 <= len(item) <= 36 for item in ids)
        assert cb.read_text(encoding="utf-8") == orig


def test_qa_pr3_build_brand_tokens_in_python_lib_and_channel_treeshaking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR2+PR3: tricky brand tokens in python_lib/ & callbacks execute cleanly and tree-shake by channel."""
    core, manifests = _make_brand_workspace(tmp_path)

    out_web = tmp_path / "dist_web"
    rep_web = build_app(
        core, manifests, "brand_x", "WEB", "prod", out_web, package_zip=True
    )
    out_voice = tmp_path / "dist_voice"
    rep_voice = build_app(
        core,
        manifests,
        "brand_x",
        "VOICE_TELEPHONY",
        "prod",
        out_voice,
        package_zip=True,
    )
    assert rep_web.zip_path is not None
    assert rep_voice.zip_path is not None

    cb_rel = "agents/root_agent/before_model_callbacks/cb_1/python_code.py"
    with zipfile.ZipFile(rep_web.zip_path) as zf:
        assert not any(n.startswith("python_lib/") for n in zf.namelist())
        web_cb = zf.read(cb_rel).decode("utf-8")
        assert "python_lib/web_only.py" in web_cb
        _purge_python_lib_modules(monkeypatch)
        ns: dict[str, object] = {}
        exec(web_cb, ns)
        label, greeting = ns["run"]()  # type: ignore[operator]
        expected_name = 'O\'Brien "Café"\nLine2 — 日本語'
        assert label == expected_name
        assert greeting == f"Welcome to {expected_name}, Alice!"

    with zipfile.ZipFile(rep_voice.zip_path) as zf:
        assert not any(n.startswith("python_lib/") for n in zf.namelist())
        voice_cb = zf.read(cb_rel).decode("utf-8")
        # Channel conditional removed `import python_lib.web_only`, so tree-shaker excludes it
        assert "python_lib/web_only.py" not in voice_cb


def test_qa_pr3_package_python_lib_env_var_booleans_534_and_determinism(
    tmp_path: Path,
) -> None:
    """Verify cxas package with python_lib + #534 booleans + $env_var produces real bools & deterministic sha256."""
    core, manifests = _make_brand_workspace(tmp_path)
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"

    rep1 = build_app(
        core, manifests, "brand_x", "WEB", "prod", out1, package_zip=True
    )
    rep2 = build_app(
        core, manifests, "brand_x", "WEB", "prod", out2, package_zip=True
    )
    assert rep1.zip_path is not None
    assert rep2.zip_path is not None

    sha1 = hashlib.sha256(rep1.zip_path.read_bytes()).hexdigest()
    sha2 = hashlib.sha256(rep2.zip_path.read_bytes()).hexdigest()
    assert sha1 == sha2

    with zipfile.ZipFile(rep1.zip_path) as zf:
        app_cfg = json.loads(zf.read("app.json").decode("utf-8"))
        assert (
            app_cfg["loggingSettings"]["bigqueryExportSettings"]["enabled"]
            is True
        )
        decls = {
            d["name"]: d["schema"]["default"]
            for d in app_cfg["variableDeclarations"]
        }
        assert decls["feature_flag"] is True
        assert decls["api_host"] == "api.prod.example.com"


def test_qa_pr3_runtime_helpers_valid_edge_cases() -> None:
    """Verify valid edge cases across search_filter, content_blocks, presentation, and kill_switch."""
    # search_filter: control chars & non-string rejected; quotes/backslashes escaped
    for bad in ("\x00", "\x1f", "\x7f", "\n", "\r", "\t"):
        with pytest.raises(ValueError, match="Invalid character"):
            escape_search_filter_literal(f"ok{bad}end")
    for non_str in (None, 123, ["a"]):
        with pytest.raises(ValueError, match="must be a string"):
            escape_search_filter_literal(non_str)  # type: ignore[arg-type]
    assert escape_search_filter_literal('a\\b"c') == 'a\\\\b\\"c'
    for bad_ctx in (
        {"brand_id": "", "country_code": "US", "lang": "en"},
        {"brand_id": "B", "country_code": None, "lang": "en"},
        {"brand_id": "B", "country_code": "US", "lang": 123},
    ):
        with pytest.raises(ValueError, match="Missing required context keys"):
            build_scoped_datastore_filter({"context": bad_ctx})

    # content_blocks: 4-tier fallback chain
    cat = {
        "B": {"DEFAULT": {"en": {"k1": "brand_default"}}},
        "default": {
            "DE": {"de": {"k2": "default_country"}},
            "DEFAULT": {"en": {"k3": "global_default"}},
        },
    }
    assert (
        get_verbatim_content_block(cat, "B", "US", "en", "k1")
        == "brand_default"
    )
    assert (
        get_verbatim_content_block(cat, "X", "DE", "de", "k2")
        == "default_country"
    )
    assert (
        get_verbatim_content_block(cat, "X", "FR", "en", "k3")
        == "global_default"
    )
    with pytest.raises(MissingContentBlock):
        get_verbatim_content_block(cat, "X", "FR", "en", "missing")

    # presentation: all DIGITAL_CHANNELS + part_factory + voice links/bold/code/URLs
    for ch in (
        "DIGITAL_APP",
        "WEB",
        "WEBCHAT",
        "MOBILE",
        "WHATSAPP",
        "INSTAGRAM",
    ):
        out = adapt_presentation(
            {"context": {"channel_class": ch}},
            "Hi **bold**",
            {"b": 2, "a": 1},
            part_factory=lambda s: f"PART:{s}",
        )
        assert out["channel_mode"] == "digital"
        assert out["widget_json"] == '{"a": 1, "b": 2}'
        assert out["part"] == 'PART:{"a": 1, "b": 2}'

    voice = adapt_presentation(
        {"context": {"channel_class": "VOICE"}},
        "Visit [Help](https://example.com) or https://foo.bar and run `cmd` **now**",
    )
    assert voice["channel_mode"] == "voice"
    assert voice["text"] == "Visit Help or and run cmd now"
    assert voice["widget_json"] is None

    # kill_switch: list, set, tuple, JSON string, comma string, malformed types
    for val in (["a1"], {"a1"}, ("a1",), '["a1"]', "x, a1 , y"):
        res = check_disabled_agent(
            {"context": {"disabled_agents": val}}, "a1", fallback_message="off"
        )
        assert res == {"short_circuit": True, "agent": "a1", "message": "off"}
    for malformed in (None, "", 123, {"a1": True}, []):
        assert (
            check_disabled_agent(
                {"context": {"disabled_agents": malformed}},
                "a1",
                fallback_message="off",
            )
            is None
        )


def test_qa_pr3_cli_e2e_and_extract_zip_slip_posix(tmp_path: Path) -> None:
    """Real CLI subprocess E2E for build, build --native-python-env --zip, package, push/pull --help."""
    for sub, flag in (
        ("build", "--native-python-env"),
        ("package", "--native-python-env"),
        ("push", "--native-python-env"),
        ("pull", "--force"),
    ):
        r = _run_cli([sub, "--help"])
        assert r.returncode == 0, r.stderr
        assert flag in r.stdout

    core, manifests = _make_brand_workspace(tmp_path)
    out_native = tmp_path / "cli_native"
    r_build = _run_cli(
        [
            "build",
            "--core-dir",
            str(core),
            "--manifests-dir",
            str(manifests),
            "--profile",
            "brand_x",
            "--channel",
            "WEB",
            "--env",
            "prod",
            "--out",
            str(out_native),
            "--native-python-env",
            "--zip",
        ]
    )
    assert r_build.returncode == 0, r_build.stderr
    assert (out_native / "pythonEnvFiles" / "pythonEnvFiles.yaml").is_file()
    cb_rel = "agents/root_agent/before_model_callbacks/cb_1/python_code.py"
    assert "CXAS_SHARED_BUNDLE" not in (out_native / cb_rel).read_text("utf-8")

    pkg_zip = tmp_path / "cli_pkg.zip"
    r_pkg = _run_cli(
        ["package", "--app-dir", str(out_native), "--zip", str(pkg_zip)]
    )
    assert r_pkg.returncode == 0, r_pkg.stderr
    with zipfile.ZipFile(pkg_zip) as zf:
        assert "CXAS_SHARED_BUNDLE" in zf.read(cb_rel).decode("utf-8")
        assert not any(n.startswith("python_lib/") for n in zf.namelist())

    # Apps.extract_exported_app strips valid bundles and rejects POSIX Zip-Slip
    apps = Apps.__new__(Apps)
    extracted = apps.extract_exported_app(
        pkg_zip.read_bytes(), tmp_path / "extracted"
    )
    assert "CXAS_SHARED_BUNDLE" not in (extracted / cb_rel).read_text("utf-8")

    for bad_member in ("../escape.txt", "/etc/evil", "agent/../../escape.txt"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(bad_member, "bad")
        with pytest.raises(ValueError, match="Path traversal"):
            apps.extract_exported_app(buf.getvalue(), tmp_path / "bad_extract")


# ---------------------------------------------------------------------------
# Failing reproduction tests for open QA defects (QA-PR3-001 .. QA-PR3-012)
# ---------------------------------------------------------------------------


def test_qa_pr3_001_symlink_in_python_lib_escaping_app_dir_rejected(
    tmp_path: Path,
) -> None:
    """QA-PR3-001 [BLOCKER]: Symlink in python_lib/ pointing outside app_dir must not be bundled or archived."""
    secret = tmp_path / "outside_secret.py"
    secret.write_text("SECRET = 'TOP_SECRET_LEAKED'\n", encoding="utf-8")

    app_dir = tmp_path / "app"
    (app_dir / "python_lib").mkdir(parents=True)
    os.symlink(secret, app_dir / "python_lib" / "leak.py")
    _make_callback(
        app_dir,
        "cb1",
        "import python_lib.leak\ndef run():\n    return python_lib.leak.SECRET\n",
    )
    (app_dir / "app.json").write_text("{}", encoding="utf-8")

    with pytest.raises((PythonBundlerError, ValueError)):
        bundle_shared_python(app_dir)

    with pytest.raises((PythonBundlerError, ValueError)):
        synthesize_python_env_files(app_dir)

    with pytest.raises((PythonBundlerError, ValueError)):
        package_app_archive(app_dir, tmp_path / "leak.zip")


def test_qa_pr3_002_nested_namespace_subpackage_without_init_executes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """QA-PR3-002 [HIGH]: Nested python_lib/sub/mod.py without intermediate __init__.py must import cleanly."""
    # Case 1: no __init__.py anywhere
    app1 = tmp_path / "app1"
    (app1 / "python_lib" / "sub").mkdir(parents=True)
    (app1 / "python_lib" / "sub" / "mod.py").write_text(
        "def fn():\n    return 'ns_ok'\n", encoding="utf-8"
    )
    cb1 = _make_callback(
        app1,
        "cb1",
        "import python_lib.sub.mod\ndef run():\n    return python_lib.sub.mod.fn()\n",
    )
    bundle_shared_python(app1)
    shutil.rmtree(app1 / "python_lib")
    _purge_python_lib_modules(monkeypatch)
    ns1: dict[str, object] = {}
    exec(cb1.read_text(encoding="utf-8"), ns1)
    assert ns1["run"]() == "ns_ok"  # type: ignore[operator]

    # Case 2: python_lib/__init__.py exists, python_lib/sub/__init__.py absent
    app2 = tmp_path / "app2"
    (app2 / "python_lib" / "sub").mkdir(parents=True)
    (app2 / "python_lib" / "__init__.py").write_text("", encoding="utf-8")
    (app2 / "python_lib" / "sub" / "mod.py").write_text(
        "def fn():\n    return 'sub_ok'\n", encoding="utf-8"
    )
    cb2 = _make_callback(
        app2,
        "cb1",
        "import python_lib.sub.mod\ndef run():\n    return python_lib.sub.mod.fn()\n",
    )
    bundle_shared_python(app2)
    shutil.rmtree(app2 / "python_lib")
    _purge_python_lib_modules(monkeypatch)
    ns2: dict[str, object] = {}
    exec(cb2.read_text(encoding="utf-8"), ns2)
    assert ns2["run"]() == "sub_ok"  # type: ignore[operator]


def test_qa_pr3_003_circular_imports_in_python_lib_execute_in_isolated_ns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """QA-PR3-003 [HIGH]: Standard circular imports (a imports b, b imports a) must execute in isolated namespace."""
    app_dir = tmp_path / "app"
    (app_dir / "python_lib").mkdir(parents=True)
    (app_dir / "python_lib" / "__init__.py").write_text("", encoding="utf-8")
    (app_dir / "python_lib" / "a.py").write_text(
        "import python_lib.b\n"
        "VAL_A = 10\n"
        "def fa():\n"
        "    return python_lib.b.VAL_B + 1\n",
        encoding="utf-8",
    )
    (app_dir / "python_lib" / "b.py").write_text(
        "import python_lib.a\n"
        "VAL_B = 41\n"
        "def fb():\n"
        "    return python_lib.a.VAL_A + 2\n",
        encoding="utf-8",
    )
    cb = _make_callback(
        app_dir,
        "cb1",
        "import python_lib.a\n"
        "def run():\n"
        "    return (python_lib.a.fa(), python_lib.a.python_lib.b.fb())\n",
    )
    bundle_shared_python(app_dir)
    shutil.rmtree(app_dir / "python_lib")
    _purge_python_lib_modules(monkeypatch)
    ns: dict[str, object] = {}
    exec(cb.read_text(encoding="utf-8"), ns)
    assert ns["run"]() == (42, 12)  # type: ignore[operator]


def test_qa_pr3_004_package_init_and_pooled_sandbox_preserve_sibling_attrs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """QA-PR3-004 [HIGH]: Package __init__.py and sibling callbacks in a pooled sandbox must not wipe attached submodules."""
    # Case A: pkg/__init__.py imports .a, a imports .b; pkg.b must remain accessible
    app_a = tmp_path / "app_a"
    (app_a / "python_lib" / "pkg").mkdir(parents=True)
    (app_a / "python_lib" / "__init__.py").write_text("", encoding="utf-8")
    (app_a / "python_lib" / "pkg" / "__init__.py").write_text(
        "from . import a\n", encoding="utf-8"
    )
    (app_a / "python_lib" / "pkg" / "a.py").write_text(
        "from . import b\nA = b.B + 1\n", encoding="utf-8"
    )
    (app_a / "python_lib" / "pkg" / "b.py").write_text(
        "B = 41\n", encoding="utf-8"
    )
    cb_a = _make_callback(
        app_a,
        "cb1",
        "import python_lib.pkg.a\n"
        "import python_lib.pkg.b\n"
        "def run():\n"
        "    return python_lib.pkg.b.B + python_lib.pkg.a.A\n",
    )
    bundle_shared_python(app_a)
    shutil.rmtree(app_a / "python_lib")
    _purge_python_lib_modules(monkeypatch)
    ns_a: dict[str, object] = {}
    exec(cb_a.read_text(encoding="utf-8"), ns_a)
    assert ns_a["run"]() == 83  # type: ignore[operator]

    # Case B: Pooled sandbox with 2 callbacks in the same app having different closures
    app_b = tmp_path / "app_b"
    (app_b / "python_lib").mkdir(parents=True)
    (app_b / "python_lib" / "__init__.py").write_text("", encoding="utf-8")
    (app_b / "python_lib" / "a.py").write_text(
        "def fa():\n    return 'A'\n", encoding="utf-8"
    )
    (app_b / "python_lib" / "b.py").write_text(
        "def fb():\n    return 'B'\n", encoding="utf-8"
    )
    cb1 = _make_callback(
        app_b,
        "cb1",
        "import python_lib.a\n"
        "def run1():\n"
        "    import python_lib.a\n"
        "    return python_lib.a.fa()\n",
    )
    cb2 = _make_callback(
        app_b,
        "cb2",
        "import python_lib.b\n"
        "def run2():\n"
        "    import python_lib.b\n"
        "    return python_lib.b.fb()\n",
    )
    bundle_shared_python(app_b)
    shutil.rmtree(app_b / "python_lib")
    _purge_python_lib_modules(monkeypatch)
    ns1: dict[str, object] = {}
    ns2: dict[str, object] = {}
    exec(cb1.read_text(encoding="utf-8"), ns1)
    exec(cb2.read_text(encoding="utf-8"), ns2)
    assert ns2["run2"]() == "B"  # type: ignore[operator]
    assert ns1["run1"]() == "A"  # type: ignore[operator]


def test_qa_pr3_005_zip_slip_backslash_and_atomic_pull_validation(
    tmp_path: Path,
) -> None:
    """QA-PR3-005 [HIGH]: Backslash Zip-Slip must be blocked; _app_pull must validate before overwriting local files."""
    apps = Apps.__new__(Apps)

    # Case A: Backslash path traversal entry in extract_exported_app
    for bad in ("..\\escape.txt", "agent\\..\\..\\escape.txt"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(bad, "pwn")
        with pytest.raises(ValueError, match="Path traversal"):
            apps.extract_exported_app(buf.getvalue(), tmp_path / "bs_out")

    # Case B: _app_pull without --app-dir-name must reject Zip-Slip archive
    buf_slip = io.BytesIO()
    with zipfile.ZipFile(buf_slip, "w") as zf:
        zf.writestr("../escaped.txt", "pwn")
    fake_client = MagicMock()
    fake_lro = MagicMock()
    fake_lro.result.return_value = MagicMock(app_content=buf_slip.getvalue())
    fake_client.export_app.return_value = fake_lro

    with pytest.raises(SystemExit) as exc_slip:
        _app_pull(
            fake_client,
            "projects/p/locations/l/apps/a",
            str(tmp_path / "pull_slip"),
        )
    assert exc_slip.value.code == 1

    # Case C: _app_pull must NOT overwrite existing local files when bundle strip fails
    buf_tampered = io.BytesIO()
    with zipfile.ZipFile(buf_tampered, "w") as zf:
        zf.writestr("my_app/app.json", "{}")
        zf.writestr(
            "my_app/agents/a/before_model_callbacks/cb1/python_code.py",
            "# === BEGIN CXAS_SHARED_BUNDLE (sha256:0000000000000000) ===\n"
            "TAMPERED = 1\n"
            "# === END CXAS_SHARED_BUNDLE ===\n"
            "def cb():\n    pass\n",
        )
    pull_dir = tmp_path / "pull_atomic"
    local_cb = (
        pull_dir
        / "my_app"
        / "agents"
        / "a"
        / "before_model_callbacks"
        / "cb1"
        / "python_code.py"
    )
    local_cb.parent.mkdir(parents=True)
    clean_local = "# CLEAN LOCAL FILE\ndef cb():\n    return 'local'\n"
    local_cb.write_text(clean_local, encoding="utf-8")

    fake_lro.result.return_value = MagicMock(
        app_content=buf_tampered.getvalue()
    )
    with pytest.raises(SystemExit) as exc_tamper:
        _app_pull(fake_client, "projects/p/locations/l/apps/a", str(pull_dir))
    assert exc_tamper.value.code == 1
    assert local_cb.read_text(encoding="utf-8") == clean_local


def test_qa_pr3_006_type_checking_only_imports_not_bundled_at_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """QA-PR3-006 [HIGH]: Imports guarded by `if TYPE_CHECKING:` must not be bundled or executed at runtime."""
    app_dir = tmp_path / "app"
    (app_dir / "python_lib").mkdir(parents=True)
    (app_dir / "python_lib" / "types_only.py").write_text(
        "import nonexistent_typing_only_dep\nclass MyType:\n    pass\n",
        encoding="utf-8",
    )
    cb = _make_callback(
        app_dir,
        "cb1",
        "from __future__ import annotations\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from python_lib.types_only import MyType\n"
        "def run(x: MyType | None = None) -> str:\n"
        "    return 'ok'\n",
    )

    bundle_shared_python(app_dir)
    bundled = cb.read_text(encoding="utf-8")
    assert "types_only" not in bundled or "CXAS_SHARED_BUNDLE" not in bundled

    shutil.rmtree(app_dir / "python_lib")
    _purge_python_lib_modules(monkeypatch)
    ns: dict[str, object] = {}
    exec(bundled, ns)
    assert ns["run"]() == "ok"  # type: ignore[operator]


def test_qa_pr3_007_runtime_helpers_handle_none_non_mapping_and_frozenset() -> (
    None
):
    """QA-PR3-007 [HIGH]: Runtime helpers must fail gracefully on None/non-mapping inputs and honor frozenset."""
    # content_blocks: None/non-mapping catalog -> MissingContentBlock / fail-closed handoff
    with pytest.raises(MissingContentBlock):
        get_verbatim_content_block(None, "B", "US", "en", "k")  # type: ignore[arg-type]
    with pytest.raises(MissingContentBlock):
        get_verbatim_content_block([], "B", "US", "en", "k")  # type: ignore[arg-type]
    with pytest.raises(MissingContentBlock):
        get_verbatim_content_block(
            {"B": {"US": {"en": {"k": 123}}}}, "B", "US", "en", "k"
        )

    state: dict[str, object] = {
        "context": {"brand_id": "B", "country_code": "US", "lang": "en"}
    }
    ans = resolve_content_block_or_handoff(
        state,
        None,
        "k",
        fallback_utterance="handoff",  # type: ignore[arg-type]
    )
    assert ans == "handoff"
    assert state["context"]["handoff_reason"] == "missing_statutory_copy"  # type: ignore[index]

    # presentation: None/non-mapping state and None spoken_text must not crash
    res_none = adapt_presentation(None, "hello **world**")  # type: ignore[arg-type]
    assert res_none["channel_mode"] == "voice"
    assert res_none["text"] == "hello world"
    res_bad = adapt_presentation("not-a-dict", "hello")  # type: ignore[arg-type]
    assert res_bad["channel_mode"] == "voice"
    res_empty = adapt_presentation({}, None)  # type: ignore[arg-type]
    assert res_empty["text"] == ""

    # search_filter: None state raises ValueError (not AttributeError)
    with pytest.raises(ValueError, match="Missing required context keys"):
        build_scoped_datastore_filter(None)  # type: ignore[arg-type]

    # kill_switch: None state returns None; frozenset must trigger kill switch
    assert check_disabled_agent(None, "a1", fallback_message="out") is None  # type: ignore[arg-type]
    killed = check_disabled_agent(
        {"context": {"disabled_agents": frozenset({"a1"})}},
        "a1",
        fallback_message="out",
    )
    assert killed == {"short_circuit": True, "agent": "a1", "message": "out"}


def test_qa_pr3_008_search_filter_extra_clauses_and_whitespace_validation() -> (
    None
):
    """QA-PR3-008 [HIGH]: Reject bare str, empty, and paren-injection extra_clauses plus whitespace context keys."""
    valid_state = {
        "context": {"brand_id": "B", "country_code": "US", "lang": "en"}
    }

    with pytest.raises(ValueError, match=r"extra_clauses|sequence|string"):
        build_scoped_datastore_filter(
            valid_state, extra_clauses='status: ANY("OK")'
        )
    with pytest.raises(ValueError, match=r"clause|empty|blank|invalid"):
        build_scoped_datastore_filter(valid_state, extra_clauses=[""])
    with pytest.raises(ValueError, match=r"clause|empty|blank|invalid"):
        build_scoped_datastore_filter(valid_state, extra_clauses=["   "])
    with pytest.raises(
        ValueError, match=r"parenthes|unbalanced|clause|invalid"
    ):
        build_scoped_datastore_filter(
            valid_state, extra_clauses=['true) OR (brand: ANY("EVIL")']
        )
    with pytest.raises(ValueError, match=r"context|empty|blank|Missing"):
        build_scoped_datastore_filter(
            {"context": {"brand_id": "   ", "country_code": "US", "lang": "en"}}
        )


def test_qa_pr3_009_presentation_voice_markdown_preserves_snake_case_and_cleans_blocks() -> (
    None
):
    """QA-PR3-009 [MEDIUM]: Voice scrubber must preserve snake_case and cleanly strip fenced code, headings, lists, and ***bold***."""
    snake = adapt_presentation(
        {}, "Check account_id_number and my_order_status"
    )
    assert snake["text"] == "Check account_id_number and my_order_status"

    fenced = adapt_presentation({}, "Run ```python\nprint(1)\n``` now")
    assert "`" not in fenced["text"]

    triple = adapt_presentation({}, "This is ***very important***")
    assert triple["text"] == "This is very important"

    bullets = adapt_presentation({}, "* first\n* second\n* third")
    assert "*" not in bullets["text"]

    heading = adapt_presentation({}, "## Summary\nHello")
    assert not heading["text"].startswith("#")


def test_qa_pr3_010_non_utf8_python_lib_and_entrypoint_syntax_error_handled_cleanly(
    tmp_path: Path,
) -> None:
    """QA-PR3-010 [MEDIUM]: Non-UTF-8 python_lib file and entrypoint SyntaxError must raise PythonBundlerError (no CLI traceback)."""
    # Case A: Non-UTF-8 file in python_lib raises PythonBundlerError (not raw UnicodeDecodeError)
    app_enc = tmp_path / "app_enc"
    (app_enc / "python_lib").mkdir(parents=True)
    (app_enc / "python_lib" / "bad.py").write_bytes(b"\xff\xfe\x00invalid\n")
    _make_callback(app_enc, "cb1", "import python_lib.bad\n")
    with pytest.raises(PythonBundlerError):
        bundle_shared_python(app_enc)

    # Case B: CLI build on core with non-UTF-8 python_lib file prints clean error (no Traceback)
    core, manifests = _make_brand_workspace(tmp_path / "cli_enc")
    (core / "python_lib" / "bad_enc.py").write_bytes(b"\xff\xfe\x00invalid\n")
    r = _run_cli(
        [
            "build",
            "--core-dir",
            str(core),
            "--manifests-dir",
            str(manifests),
            "--profile",
            "brand_x",
            "--channel",
            "WEB",
            "--env",
            "prod",
            "--out",
            str(tmp_path / "cli_enc_out"),
        ]
    )
    assert r.returncode != 0
    assert "Traceback" not in r.stderr

    # Case C: SyntaxError in entrypoint raises PythonBundlerError instead of silently bundling 0 bytes
    app_syn = tmp_path / "app_syn"
    (app_syn / "python_lib").mkdir(parents=True)
    (app_syn / "python_lib" / "good.py").write_text("X = 1\n", encoding="utf-8")
    _make_callback(
        app_syn, "cb1", "import python_lib.good\ndef broken(:\n    pass\n"
    )
    with pytest.raises(PythonBundlerError):
        bundle_shared_python(app_syn)


def test_qa_pr3_011_synthesize_python_env_files_prunes_deleted_modules(
    tmp_path: Path,
) -> None:
    """QA-PR3-011 [MEDIUM]: Re-running synthesize_python_env_files after deleting a module must prune stale files."""
    app_dir = tmp_path / "app"
    (app_dir / "python_lib").mkdir(parents=True)
    (app_dir / "python_lib" / "keep.py").write_text("K = 1\n", encoding="utf-8")
    (app_dir / "python_lib" / "old.py").write_text("O = 2\n", encoding="utf-8")

    synthesize_python_env_files(app_dir)
    (app_dir / "python_lib" / "old.py").unlink()
    synthesize_python_env_files(app_dir)

    yaml_text = (app_dir / "pythonEnvFiles" / "pythonEnvFiles.yaml").read_text(
        encoding="utf-8"
    )
    assert "old.py" not in yaml_text
    assert not (
        app_dir / "pythonEnvFiles" / "content" / "python_lib" / "old.py"
    ).exists()


def test_qa_pr3_012_future_import_with_semicolon_statement_executes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """QA-PR3-012 [MEDIUM]: `from __future__ import annotations; import python_lib.a` must place bundle before the import."""
    app_dir = tmp_path / "app"
    (app_dir / "python_lib").mkdir(parents=True)
    (app_dir / "python_lib" / "a.py").write_text("X = 7\n", encoding="utf-8")
    cb = _make_callback(
        app_dir,
        "cb1",
        "from __future__ import annotations; import python_lib.a\n"
        "def run() -> int:\n"
        "    return python_lib.a.X\n",
    )

    bundle_shared_python(app_dir)
    shutil.rmtree(app_dir / "python_lib")
    _purge_python_lib_modules(monkeypatch)
    ns: dict[str, object] = {}
    exec(cb.read_text(encoding="utf-8"), ns)
    assert ns["run"]() == 7  # type: ignore[operator]


def test_qa_pr3_013_build_app_rejects_symlinks_in_core_python_lib(
    tmp_path: Path,
) -> None:
    """QA-PR3-013 [HIGH]: build_app must not dereference symlinks in core_dir (bypassing QA-PR3-001)."""
    secret = tmp_path / "outside_secret.py"
    secret.write_text(
        "SECRET = 'EXFILTRATED_VIA_BUILD_APP'\n", encoding="utf-8"
    )
    core, manifests = _make_brand_workspace(tmp_path / "ws")
    os.symlink(secret, core / "python_lib" / "leak.py")
    _make_callback(
        core,
        "cb_leak",
        "import python_lib.leak\ndef run():\n    return python_lib.leak.SECRET\n",
    )

    with pytest.raises((BuildError, PythonBundlerError, ValueError)):
        build_app(
            core,
            manifests,
            "brand_x",
            "WEB",
            "prod",
            tmp_path / "dist_leak",
            package_zip=True,
        )


def test_qa_pr3_014_pooled_sandbox_clears_stale_attrs_across_digests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """QA-PR3-014 [HIGH]: Reusing sys.modules in pooled sandbox must clear stale attrs when module digest changes."""
    app1 = tmp_path / "app1"
    app2 = tmp_path / "app2"
    for ad in (app1, app2):
        (ad / "python_lib").mkdir(parents=True)
        (ad / "python_lib" / "__init__.py").write_text("", encoding="utf-8")
        _make_callback(
            ad,
            "cb1",
            "import python_lib.cfg\n"
            "def get_secret():\n"
            "    return getattr(python_lib.cfg, 'TENANT_A_SECRET', None)\n"
            "def get_pkg_secret():\n"
            "    return getattr(python_lib, 'PKG_SECRET_A', None)\n",
        )

    (app1 / "python_lib" / "__init__.py").write_text(
        "PKG_SECRET_A = 'pkg-secret-a'\n", encoding="utf-8"
    )
    (app1 / "python_lib" / "cfg.py").write_text(
        "BRAND = 'A'\nTENANT_A_SECRET = 'secret-a'\n", encoding="utf-8"
    )
    (app2 / "python_lib" / "__init__.py").write_text(
        "BRAND_INIT = 'B'\n", encoding="utf-8"
    )
    (app2 / "python_lib" / "cfg.py").write_text(
        "BRAND = 'B'\n", encoding="utf-8"
    )

    bundle_shared_python(app1)
    bundle_shared_python(app2)

    cb1_code = (
        app1
        / "agents"
        / "root_agent"
        / "before_model_callbacks"
        / "cb1"
        / "python_code.py"
    ).read_text(encoding="utf-8")
    cb2_code = (
        app2
        / "agents"
        / "root_agent"
        / "before_model_callbacks"
        / "cb1"
        / "python_code.py"
    ).read_text(encoding="utf-8")

    _purge_python_lib_modules(monkeypatch)
    ns1: dict[str, object] = {}
    ns2: dict[str, object] = {}
    exec(cb1_code, ns1)
    assert ns1["get_secret"]() == "secret-a"  # type: ignore[operator]
    assert ns1["get_pkg_secret"]() == "pkg-secret-a"  # type: ignore[operator]

    exec(cb2_code, ns2)
    assert ns2["get_secret"]() is None  # type: ignore[operator]
    assert ns2["get_pkg_secret"]() is None  # type: ignore[operator]


# ---------------------------------------------------------------------------
# PR 3 Extension QA: C025 (SharedPythonBundleIntegrity)
# ---------------------------------------------------------------------------


def _run_c025_only(app_dir: Path) -> LintReport:
    reg = build_registry()
    cfg = LintConfig.load(app_dir)
    disc = Discovery(app_dir, app_dir / "evals")
    ctx = build_context(app_dir, cfg, disc)
    rep = LintReport()
    run_rules(reg, cfg, ctx, disc, rep, specific_rules={"C025"})
    return rep


def _make_lintable_app(root: Path, cb_code: str) -> tuple[Path, Path]:
    (root / "agents" / "root_agent" / "before_model_callbacks" / "cb_1").mkdir(
        parents=True, exist_ok=True
    )
    (root / "app.json").write_text(
        json.dumps({"displayName": "LintApp", "rootAgent": "root_agent"}),
        encoding="utf-8",
    )
    (root / "agents" / "root_agent" / "root_agent.json").write_text(
        json.dumps({"displayName": "root_agent", "tools": ["end_session"]}),
        encoding="utf-8",
    )
    (root / "agents" / "root_agent" / "instruction.txt").write_text(
        "<role>Assistant</role>\n", encoding="utf-8"
    )
    cb = (
        root
        / "agents"
        / "root_agent"
        / "before_model_callbacks"
        / "cb_1"
        / "python_code.py"
    )
    cb.write_text(cb_code, encoding="utf-8")
    return root, cb


def test_qa_pr3_c025_cli_e2e_build_native_unbuilt_missing_and_overrides(
    tmp_path: Path,
) -> None:
    """Real CLI E2E: cxas build (inline & native) -> cxas lint, unbuilt warning, missing error, overrides, --list-rules."""
    r_list = _run_cli(["lint", "--list-rules"])
    assert r_list.returncode == 0, r_list.stderr
    assert "C025" in r_list.stdout
    assert len(build_registry().all_rules()) >= 105  # noqa: PLR2004

    core, manifests = _make_brand_workspace(tmp_path)
    # Simplify callback for lint cleanly (valid CES callback signature)
    _make_callback(
        core,
        "cb_1",
        "from python_lib.branding import greet\n"
        "def before_model_callback(callback_context, llm_request):\n"
        "    _ = greet('user')\n"
        "    return None\n",
    )

    # 1. Inline cxas build -> cxas lint --rule C025 & --only callbacks -> exit 0, 0 findings
    out_inline = tmp_path / "dist_inline"
    r_b1 = _run_cli(
        [
            "build",
            "--core-dir",
            str(core),
            "--manifests-dir",
            str(manifests),
            "--profile",
            "brand_x",
            "--channel",
            "WEB",
            "--env",
            "prod",
            "--out",
            str(out_inline),
        ]
    )
    assert r_b1.returncode == 0, r_b1.stderr
    r_l1 = _run_cli(
        ["lint", "--app-dir", str(out_inline), "--rule", "C025", "--json"]
    )
    assert r_l1.returncode == 0, r_l1.stderr
    assert json.loads(r_l1.stdout) == []
    r_cb1 = _run_cli(
        [
            "lint",
            "--app-dir",
            str(out_inline),
            "--only",
            "callbacks",
            "--json",
        ]
    )
    assert r_cb1.returncode == 0, r_cb1.stderr
    assert json.loads(r_cb1.stdout) == []

    # 2. Native --native-python-env build -> cxas lint --rule C025 & --only callbacks -> exit 0, 0 findings
    out_native = tmp_path / "dist_native"
    r_b2 = _run_cli(
        [
            "build",
            "--core-dir",
            str(core),
            "--manifests-dir",
            str(manifests),
            "--profile",
            "brand_x",
            "--channel",
            "WEB",
            "--env",
            "prod",
            "--out",
            str(out_native),
            "--native-python-env",
        ]
    )
    assert r_b2.returncode == 0, r_b2.stderr
    r_l2 = _run_cli(
        ["lint", "--app-dir", str(out_native), "--rule", "C025", "--json"]
    )
    assert r_l2.returncode == 0, r_l2.stderr
    assert json.loads(r_l2.stdout) == []

    # 3. Unbuilt core with python_lib -> C025 WARNING, exit 0
    r_l3 = _run_cli(
        ["lint", "--app-dir", str(core), "--rule", "C025", "--json"]
    )
    assert r_l3.returncode == 0
    findings_3 = json.loads(r_l3.stdout)
    assert len(findings_3) == 1
    assert findings_3[0]["rule_id"] == "C025"
    assert findings_3[0]["severity"] == "warning"

    # 4. cxaslint.yaml C025: error escalates unbuilt warning to exit 1
    (core / "cxaslint.yaml").write_text(
        "rules:\n  C025: error\n", encoding="utf-8"
    )
    r_l4 = _run_cli(
        ["lint", "--app-dir", str(core), "--rule", "C025", "--json"]
    )
    assert r_l4.returncode == 1
    assert json.loads(r_l4.stdout)[0]["severity"] == "error"

    # 5. cxaslint.yaml C025: off silences it -> exit 0, []
    (core / "cxaslint.yaml").write_text(
        "rules:\n  C025: off\n", encoding="utf-8"
    )
    r_l5 = _run_cli(
        ["lint", "--app-dir", str(core), "--rule", "C025", "--json"]
    )
    assert r_l5.returncode == 0
    assert json.loads(r_l5.stdout) == []
    (core / "cxaslint.yaml").unlink()

    # 6. Missing python_lib everywhere -> C025 ERROR, exit 1; --only callbacks dispatches once
    shutil.rmtree(core / "python_lib")
    r_l6 = _run_cli(
        ["lint", "--app-dir", str(core), "--rule", "C025", "--json"]
    )
    assert r_l6.returncode == 1
    findings_6 = json.loads(r_l6.stdout)
    assert len(findings_6) == 1
    assert findings_6[0]["severity"] == "error"
    r_cat = _run_cli(
        ["lint", "--app-dir", str(core), "--only", "callbacks", "--json"]
    )
    assert r_cat.returncode == 1
    c025_cat = [f for f in json.loads(r_cat.stdout) if f["rule_id"] == "C025"]
    assert len(c025_cat) == 1


def test_qa_pr3_c025_edge_cases_syntax_utf8_markers_type_checking_yaml_symlinks(
    tmp_path: Path,
) -> None:
    """Verify C025 edge cases: syntax/UTF-8 errors, malformed markers, TYPE_CHECKING, corrupt/non-UTF-8 YAML, symlinks."""
    # Syntax & non-UTF-8 in python_lib/ and pythonEnvFiles/content/ + non-UTF-8 pythonEnvFiles.yaml
    app1, _ = _make_lintable_app(
        tmp_path / "app1", "def cb():\n    return None\n"
    )
    (app1 / "python_lib").mkdir()
    (app1 / "python_lib" / "bad.py").write_text("def x(:\n", encoding="utf-8")
    (app1 / "pythonEnvFiles" / "content" / "python_lib").mkdir(parents=True)
    (app1 / "pythonEnvFiles" / "content" / "python_lib" / "raw.py").write_bytes(
        b"\xff\xfe\x00"
    )
    (app1 / "pythonEnvFiles" / "pythonEnvFiles.yaml").write_bytes(
        b"\xff\xfe\x00"
    )
    rep1 = _run_c025_only(app1)
    assert {r.file for r in rep1.results} == {
        "python_lib/bad.py",
        "pythonEnvFiles/content/python_lib/raw.py",
        "pythonEnvFiles/pythonEnvFiles.yaml",
    }
    assert all(r.severity == Severity.ERROR for r in rep1.results)

    # Non-dict YAML root in pythonEnvFiles.yaml
    app2, _ = _make_lintable_app(
        tmp_path / "app2", "def cb():\n    return None\n"
    )
    (app2 / "pythonEnvFiles").mkdir()
    (app2 / "pythonEnvFiles" / "pythonEnvFiles.yaml").write_text(
        "- just\n- a\n- list\n", encoding="utf-8"
    )
    rep2 = _run_c025_only(app2)
    assert len(rep2.results) == 1
    assert rep2.results[0].severity == Severity.ERROR

    # Nested namespace module `from python_lib.pkg.sub import fn` present vs missing
    app3, cb3 = _make_lintable_app(
        tmp_path / "app3",
        "from python_lib.pkg.sub import fn\ndef cb():\n    return fn()\n",
    )
    (app3 / "python_lib" / "pkg").mkdir(parents=True)
    (app3 / "python_lib" / "pkg" / "sub.py").write_text(
        "def fn():\n    return 1\n", encoding="utf-8"
    )
    synthesize_python_env_files(app3)
    assert _run_c025_only(app3).results == []
    cb3.write_text(
        "from python_lib.pkg.missing import fn\ndef cb():\n    return fn()\n",
        encoding="utf-8",
    )
    rep3_miss = _run_c025_only(app3)
    assert len(rep3_miss.results) == 1
    assert rep3_miss.results[0].severity == Severity.ERROR


def test_qa_pr3_015_c025_rejects_bogus_tampered_and_stale_inline_bundles(
    tmp_path: Path,
) -> None:
    """QA-PR3-015 [HIGH]: C025 must reject bogus BEGIN headers, tampered bundle digests, and stale bundles missing imports."""
    # Case A: Missing sha256 digest in BEGIN header + non-hex sha256 header
    app_bogus, _ = _make_lintable_app(
        tmp_path / "app_bogus",
        f"{BUNDLE_BEGIN_PREFIX} ===\n"
        f"{BUNDLE_END_LINE}\n"
        "import python_lib.missing\n"
        "def cb():\n    return None\n",
    )
    rep_bogus = _run_c025_only(app_bogus)
    assert any(r.severity == Severity.ERROR for r in rep_bogus.results)

    app_nonhex, _ = _make_lintable_app(
        tmp_path / "app_nonhex",
        f"{BUNDLE_BEGIN_PREFIX} (sha256:ZZZZZZZZZZZZZZZZ) ===\n"
        f"{BUNDLE_END_LINE}\n"
        "import python_lib.missing\n"
        "def cb():\n    return None\n",
    )
    rep_nonhex = _run_c025_only(app_nonhex)
    assert any(r.severity == Severity.ERROR for r in rep_nonhex.results)

    # Case B: Real bundle tampered in Console (digest mismatch)
    app_tamper, cb_tamper = _make_lintable_app(
        tmp_path / "app_tamper",
        "import python_lib.a\ndef cb():\n    return python_lib.a.V\n",
    )
    (app_tamper / "python_lib").mkdir()
    (app_tamper / "python_lib" / "a.py").write_text("V = 1\n", encoding="utf-8")
    bundle_shared_python(app_tamper)
    bundled = cb_tamper.read_text(encoding="utf-8")
    cb_tamper.write_text(
        bundled.replace("V = 1", "V = 999", 1), encoding="utf-8"
    )
    rep_tamper = _run_c025_only(app_tamper)
    assert any(r.severity == Severity.ERROR for r in rep_tamper.results)

    # Case C: Real bundle missing a newly added python_lib import
    cb_tamper.write_text(
        bundled + "\nimport python_lib.never_bundled\n", encoding="utf-8"
    )
    rep_stale = _run_c025_only(app_tamper)
    assert any(r.severity == Severity.ERROR for r in rep_stale.results)


def test_qa_pr3_016_c025_validates_python_env_yaml_entries_and_declared_paths(
    tmp_path: Path,
) -> None:
    """QA-PR3-016 [HIGH]: C025 must reject empty/undeclared pythonEnvFiles.yaml, non-dict entries, and dangling filePaths."""
    app, _ = _make_lintable_app(
        tmp_path / "app_env",
        "import python_lib.a\ndef cb():\n    return python_lib.a.V\n",
    )
    (app / "pythonEnvFiles" / "content" / "python_lib").mkdir(parents=True)
    (app / "pythonEnvFiles" / "content" / "python_lib" / "a.py").write_text(
        "V = 1\n", encoding="utf-8"
    )
    yaml_path = app / "pythonEnvFiles" / "pythonEnvFiles.yaml"

    # Case A: Empty pythonEnvFiles list (file sits on disk but is NOT declared in manifest)
    yaml_path.write_text("pythonEnvFiles: []\n", encoding="utf-8")
    assert any(
        r.severity == Severity.ERROR for r in _run_c025_only(app).results
    )

    # Case B: Dangling filePath that does not exist on disk
    yaml_path.write_text(
        "pythonEnvFiles:\n"
        "  - name: ghost\n"
        "    filePath: content/python_lib/does_not_exist.py\n",
        encoding="utf-8",
    )
    assert any(
        r.severity == Severity.ERROR for r in _run_c025_only(app).results
    )

    # Case C: Non-dict entry inside pythonEnvFiles list
    yaml_path.write_text("pythonEnvFiles:\n  - not-a-dict\n", encoding="utf-8")
    assert any(
        r.severity == Severity.ERROR for r in _run_c025_only(app).results
    )


def test_qa_pr3_017_c025_allows_from_namespace_subpackage_import_in_native_env(
    tmp_path: Path,
) -> None:
    """QA-PR3-017 [MEDIUM]: `from python_lib.pkg import sub` must pass when `python_lib/pkg/sub/` is a namespace subpackage."""
    app, _ = _make_lintable_app(
        tmp_path / "app_ns",
        "from python_lib.pkg import sub\ndef cb():\n    return sub.mod.V\n",
    )
    (app / "python_lib" / "pkg" / "sub").mkdir(parents=True)
    (app / "python_lib" / "pkg" / "sub" / "mod.py").write_text(
        "V = 1\n", encoding="utf-8"
    )
    synthesize_python_env_files(app)
    assert _run_c025_only(app).results == []


def test_qa_pr3_018_c025_ignores_bundle_marker_text_inside_docstrings(
    tmp_path: Path,
) -> None:
    """QA-PR3-018 [MEDIUM]: Bundle marker text inside a docstring must not trigger a false-positive C025 error."""
    app, _ = _make_lintable_app(
        tmp_path / "app_doc",
        "def cb():\n"
        '    """Example header:\n'
        f"    {BUNDLE_BEGIN_PREFIX} (sha256:0000000000000000) ===\n"
        '    """\n'
        "    return None\n",
    )
    assert _run_c025_only(app).results == []
