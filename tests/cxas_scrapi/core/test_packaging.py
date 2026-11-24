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

"""Unit tests for deterministic CXAS app ZIP packaging."""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from typing import TYPE_CHECKING

import pytest

from cxas_scrapi.core.packaging import package_app_archive

if TYPE_CHECKING:
    from pathlib import Path


def test_package_app_archive_roots_and_env_file(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "app.json").write_text("{}", encoding="utf-8")
    (app_dir / "README.md").write_text("excluded", encoding="utf-8")

    deployments = app_dir / "deployments"
    deployments.mkdir()
    (deployments / "web.yaml").write_text("channel: WEB\n", encoding="utf-8")

    python_env_dir = app_dir / "pythonEnvFiles"
    python_env_dir.mkdir()
    (python_env_dir / "requirements.txt").write_text("pydantic\n", "utf-8")

    workflows_dir = app_dir / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / "ci.yaml").write_text("name: ci\n", encoding="utf-8")

    pycache = app_dir / "tools" / "t1" / "__pycache__"
    pycache.mkdir(parents=True)
    (pycache / "mod.cpython-312.pyc").write_bytes(b"pyc")
    (app_dir / "tools" / "t1" / "t1.json").write_text("{}", encoding="utf-8")
    (app_dir / "tools" / "t1" / ".DS_Store").write_text("x", encoding="utf-8")

    env_file = tmp_path / "my_env.json"
    env_file.write_text(json.dumps({"region": "us"}), encoding="utf-8")

    zip_path = tmp_path / "out" / "app.zip"
    package_app_archive(app_dir, zip_path, env_file)

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        assert names == sorted(names)
        assert "app.json" in names
        assert "pythonEnvFiles/requirements.txt" in names
        assert ".github/workflows/ci.yaml" in names
        assert "tools/t1/t1.json" in names
        assert "environment.json" in names
        assert json.loads(zf.read("environment.json")) == {"region": "us"}
        assert "README.md" not in names
        assert "deployments/web.yaml" not in names
        assert not any("__pycache__" in n or ".DS_Store" in n for n in names)


def test_package_app_archive_is_byte_deterministic(tmp_path: Path) -> None:
    app1 = tmp_path / "app1"
    app2 = tmp_path / "app2"
    for app, mtime in ((app1, 1_600_000_000), (app2, 1_750_000_000)):
        (app / "agents" / "a1").mkdir(parents=True)
        app_json = app / "app.json"
        app_json.write_text('{"rootAgent": "a1"}\n', encoding="utf-8")
        instr = app / "agents" / "a1" / "instruction.txt"
        instr.write_text("Hello\n", encoding="utf-8")
        os.utime(app_json, (mtime, mtime))
        os.utime(instr, (mtime, mtime))

    z1 = package_app_archive(app1, tmp_path / "z1.zip", wrapper_dir="agent")
    z2 = package_app_archive(app2, tmp_path / "z2.zip", wrapper_dir="agent")

    assert hashlib.sha256(z1.read_bytes()).hexdigest() == (
        hashlib.sha256(z2.read_bytes()).hexdigest()
    )
    with zipfile.ZipFile(z1, "r") as zf:
        assert "agent/app.json" in zf.namelist()
        assert "agent/agents/a1/instruction.txt" in zf.namelist()


def test_package_app_archive_resolves_env_vars_and_booleans(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "app.json").write_text(
        json.dumps(
            {
                "displayName": "Env App",
                "variableDeclarations": [
                    {
                        "name": "api_host",
                        "description": "Host",
                        "schema": {"type": "STRING", "default": "$env_var"},
                    }
                ],
                "languageSettings": {
                    "defaultLanguageCode": "en",
                    "enableMultilingualSupport": "$env_var",
                },
            }
        ),
        encoding="utf-8",
    )
    env_file = tmp_path / "prod_env.json"
    env_file.write_text(
        json.dumps(
            {
                "app": {
                    "variableDeclarations": [
                        {
                            "name": "api_host",
                            "schema": {"default": "api.example.com"},
                        }
                    ],
                    "languageSettings": {"enableMultilingualSupport": True},
                }
            }
        ),
        encoding="utf-8",
    )

    resolved: list[str] = []
    zip_path = package_app_archive(
        app_dir,
        tmp_path / "resolved.zip",
        env_file,
        resolved_env_vars=resolved,
    )

    assert resolved == ["api_host"]
    with zipfile.ZipFile(zip_path, "r") as zf:
        staged_app = json.loads(zf.read("app.json"))
    assert (
        staged_app["variableDeclarations"][0]["schema"]["default"]
        == "api.example.com"
    )
    assert staged_app["languageSettings"]["enableMultilingualSupport"] is True


def test_package_app_archive_missing_inputs(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="App directory not found"):
        package_app_archive(tmp_path / "missing", tmp_path / "out.zip")

    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "app.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="Environment file not found"):
        package_app_archive(
            app_dir, tmp_path / "out.zip", tmp_path / "missing_env.json"
        )
