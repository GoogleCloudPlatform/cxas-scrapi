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

"""Tests for shared-Python bundling wired into packaging, pull and push."""

import io
import json
import zipfile
from pathlib import Path

import pytest

from cxas_scrapi.build.builder import build_app
from cxas_scrapi.cli.main import get_parser
from cxas_scrapi.core.apps import Apps
from cxas_scrapi.core.packaging import package_app_archive

_ENTRYPOINT = (
    "import python_lib.guards\n"
    "def before_model_callback(cb, llm_request):\n"
    "    return python_lib.guards.check()\n"
)
_CB = "agents/a/a_callbacks/before_model_callbacks/python_code.py"


def _make_app(tmp_path: Path) -> Path:
    app_dir = tmp_path / "app"
    (app_dir / "python_lib").mkdir(parents=True)
    (app_dir / "python_lib" / "guards.py").write_text(
        "def check():\n    return None\n"
    )
    cb = app_dir / _CB
    cb.parent.mkdir(parents=True)
    cb.write_text(_ENTRYPOINT)
    (app_dir / "app.json").write_text("{}")
    return app_dir


def test_package_bundles_without_mutating_source(tmp_path: Path) -> None:
    app_dir = _make_app(tmp_path)
    zip_path = package_app_archive(app_dir, tmp_path / "out.zip")
    assert (app_dir / _CB).read_text() == _ENTRYPOINT
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        bundled = z.read(_CB).decode()
    assert "CXAS_SHARED_BUNDLE" in bundled
    assert not any(n.startswith("python_lib/") for n in names)


def test_package_wrapper_dir_prefixes_entries(tmp_path: Path) -> None:
    app_dir = _make_app(tmp_path)
    zip_path = package_app_archive(
        app_dir, tmp_path / "out.zip", wrapper_dir="agent"
    )
    with zipfile.ZipFile(zip_path) as z:
        assert all(n.startswith("agent/") for n in z.namelist())


def test_package_native_python_env_keeps_entrypoint(tmp_path: Path) -> None:
    app_dir = _make_app(tmp_path)
    zip_path = package_app_archive(
        app_dir, tmp_path / "out.zip", native_python_env=True
    )
    with zipfile.ZipFile(zip_path) as z:
        assert z.read(_CB).decode() == _ENTRYPOINT
        assert "pythonEnvFiles/pythonEnvFiles.yaml" in z.namelist()


def test_extract_exported_app_strips_bundles(tmp_path: Path) -> None:
    app_dir = _make_app(tmp_path)
    zip_path = package_app_archive(
        app_dir, tmp_path / "out.zip", wrapper_dir="agent"
    )
    apps = Apps.__new__(Apps)  # extract_exported_app needs no client
    root = apps.extract_exported_app(zip_path.read_bytes(), tmp_path / "pulled")
    assert root == tmp_path / "pulled" / "agent"
    assert (root / _CB).read_text() == _ENTRYPOINT


def test_extract_exported_app_flat_archive(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("app.json", "{}")
        z.writestr(_CB, _ENTRYPOINT)
    apps = Apps.__new__(Apps)
    root = apps.extract_exported_app(buf.getvalue(), tmp_path / "pulled")
    assert root == tmp_path / "pulled"


def test_cli_flags_registered() -> None:
    parser = get_parser()
    assert parser.parse_args(["pull", "my-app", "--force"]).force is True
    assert (
        parser.parse_args(["push", "--native-python-env"]).native_python_env
        is True
    )
    assert (
        parser.parse_args(
            ["package", "--app-dir", "x", "--native-python-env"]
        ).native_python_env
        is True
    )
    assert (
        parser.parse_args(
            [
                "build",
                "--core-dir",
                "c",
                "--manifests-dir",
                "m",
                "--env",
                "prod",
                "--out",
                "o",
                "--all",
                "--native-python-env",
            ]
        ).native_python_env
        is True
    )


def test_flat_ces_callback_layout_bundled_and_stripped(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    (app_dir / "python_lib").mkdir(parents=True)
    (app_dir / "python_lib" / "guards.py").write_text(
        "def check():\n    return None\n"
    )
    flat_cb = "agents/root_agent/before_model_callbacks_01/python_code.py"
    cb_path = app_dir / flat_cb
    cb_path.parent.mkdir(parents=True)
    cb_path.write_text(_ENTRYPOINT)
    (app_dir / "app.json").write_text("{}")

    zip_path = package_app_archive(
        app_dir, tmp_path / "out.zip", wrapper_dir="agent"
    )
    with zipfile.ZipFile(zip_path) as z:
        assert "CXAS_SHARED_BUNDLE" in z.read(f"agent/{flat_cb}").decode()

    apps = Apps.__new__(Apps)
    root = apps.extract_exported_app(zip_path.read_bytes(), tmp_path / "pulled")
    assert (root / flat_cb).read_text() == _ENTRYPOINT


def test_extract_exported_app_rejects_zip_slip(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../escape.txt", "bad")
    apps = Apps.__new__(Apps)
    with pytest.raises(ValueError, match="Path traversal"):
        apps.extract_exported_app(buf.getvalue(), tmp_path / "pulled")


def test_build_app_bundles_shared_python_and_native_mode(
    tmp_path: Path,
) -> None:
    core_dir = tmp_path / "core"
    (core_dir / "python_lib").mkdir(parents=True)
    (core_dir / "python_lib" / "guards.py").write_text(
        'PROFILE = "@@profile.profile_id@@"\ndef check():\n    return PROFILE\n'
    )
    cb_rel = "agents/root_agent/before_model_callbacks/cb_1/python_code.py"
    (core_dir / cb_rel).parent.mkdir(parents=True)
    (core_dir / cb_rel).write_text(_ENTRYPOINT)
    (core_dir / "agents" / "root_agent" / "root_agent.json").write_text(
        json.dumps({"displayName": "root_agent"})
    )
    (core_dir / "agents" / "root_agent" / "instruction.txt").write_text("Hi.")
    (core_dir / "app.json").write_text(
        json.dumps({"displayName": "App", "rootAgent": "root_agent"})
    )

    manifests = tmp_path / "manifests"
    profile_dir = manifests / "profiles" / "brand_a"
    profile_dir.mkdir(parents=True)
    (profile_dir / "identity.yaml").write_text(
        "profile_id: brand_a\ndisplay_name: Brand A\n"
    )
    (profile_dir / "channel_policy.yaml").write_text(
        "profile_id: brand_a\nchannels:\n  WEB:\n    supported_modalities: [TEXT]\n"
    )
    (manifests / "envs").mkdir(parents=True)
    (manifests / "envs" / "prod.yaml").write_text("environment: prod\n")

    out_bundled = tmp_path / "dist_bundled"
    rep = build_app(
        core_dir,
        manifests,
        "brand_a",
        "WEB",
        "prod",
        out_bundled,
        package_zip=True,
    )
    assert "CXAS_SHARED_BUNDLE" in (out_bundled / cb_rel).read_text()
    assert rep.zip_path is not None
    with zipfile.ZipFile(rep.zip_path) as z:
        assert "CXAS_SHARED_BUNDLE" in z.read(cb_rel).decode()
        assert not any(n.startswith("python_lib/") for n in z.namelist())

    out_native = tmp_path / "dist_native"
    rep_native = build_app(
        core_dir,
        manifests,
        "brand_a",
        "WEB",
        "prod",
        out_native,
        package_zip=True,
        native_python_env=True,
    )
    assert (out_native / cb_rel).read_text() == _ENTRYPOINT
    assert (out_native / "pythonEnvFiles" / "pythonEnvFiles.yaml").is_file()
    assert rep_native.zip_path is not None
    with zipfile.ZipFile(rep_native.zip_path) as z:
        assert z.read(cb_rel).decode() == _ENTRYPOINT
        assert "pythonEnvFiles/pythonEnvFiles.yaml" in z.namelist()


def test_package_app_archive_includes_app_yml(tmp_path: Path) -> None:
    app_dir = tmp_path / "yml_app"
    app_dir.mkdir()
    app_yml_content = "displayName: Yml App\nrootAgent: root_agent\n"
    (app_dir / "app.yml").write_text(app_yml_content, encoding="utf-8")
    (app_dir / "agents" / "root_agent").mkdir(parents=True)
    (app_dir / "agents" / "root_agent" / "instruction.txt").write_text(
        "Hello.\n", encoding="utf-8"
    )

    zip_path = package_app_archive(app_dir, tmp_path / "yml_app.zip")
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        assert "app.yml" in names
        assert z.read("app.yml").decode("utf-8") == app_yml_content
        assert "agents/root_agent/instruction.txt" in names
