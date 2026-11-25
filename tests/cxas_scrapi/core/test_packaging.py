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
import yaml

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


@pytest.mark.parametrize(
    "bad_wrapper", ["../../escape", "/etc/evil", "a/../b", r"a\b"]
)
def test_package_wrapper_dir_rejects_path_traversal(
    tmp_path: Path, bad_wrapper: str
) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "app.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match=r"(?i)wrapper|path|invalid|traversal"):
        package_app_archive(
            app_dir, tmp_path / "out.zip", wrapper_dir=bad_wrapper
        )


def test_package_resolves_string_boolean_env_override(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "app.json").write_text(
        json.dumps(
            {
                "displayName": "BoolEnvApp",
                "variableDeclarations": [
                    {
                        "name": "feature_flag",
                        "schema": {"type": "BOOLEAN", "default": "$env_var"},
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
                        {"name": "feature_flag", "schema": {"default": True}}
                    ],
                    "languageSettings": {"enableMultilingualSupport": "true"},
                }
            }
        ),
        encoding="utf-8",
    )

    resolved: list[str] = []
    zip_path = package_app_archive(
        app_dir, tmp_path / "pkg.zip", env_file, resolved_env_vars=resolved
    )
    assert resolved == ["feature_flag"]
    with zipfile.ZipFile(zip_path, "r") as zf:
        staged_app = json.loads(zf.read("app.json"))
    assert staged_app["variableDeclarations"][0]["schema"]["default"] is True
    assert staged_app["languageSettings"]["enableMultilingualSupport"] is True


def test_package_includes_app_yml_root(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "app.yml").write_text("rootAgent: main\n", encoding="utf-8")

    zip_path = package_app_archive(app_dir, tmp_path / "app_yml.zip")
    with zipfile.ZipFile(zip_path, "r") as zf:
        assert "app.yml" in zf.namelist()
        assert zf.read("app.yml").decode("utf-8") == "rootAgent: main\n"


def test_package_rejects_symlinks(tmp_path: Path) -> None:
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    # Symlinked app_dir
    real_app = tmp_path / "real_app"
    real_app.mkdir()
    (real_app / "app.json").write_text("{}", encoding="utf-8")
    link_app = tmp_path / "link_app"
    link_app.symlink_to(real_app)
    with pytest.raises(ValueError, match="Symlinks are not permitted"):
        package_app_archive(link_app, tmp_path / "out1.zip")

    # Symlinked env_file
    link_env = tmp_path / "link_env.json"
    link_env.symlink_to(outside)
    with pytest.raises(ValueError, match="Symlinks are not permitted"):
        package_app_archive(real_app, tmp_path / "out2.zip", link_env)

    # Symlinked archive root file
    app_root_link = tmp_path / "app_root_link"
    app_root_link.mkdir()
    (app_root_link / "app.json").symlink_to(outside)
    with pytest.raises(ValueError, match="Symlinks are not permitted"):
        package_app_archive(app_root_link, tmp_path / "out3.zip")

    # Symlinked nested file inside agents/
    app_nested = tmp_path / "app_nested"
    (app_nested / "agents" / "a1").mkdir(parents=True)
    (app_nested / "app.json").write_text("{}", encoding="utf-8")
    (app_nested / "agents" / "a1" / "leak.txt").symlink_to(outside)
    with pytest.raises(ValueError, match="Symlinks are not permitted"):
        package_app_archive(app_nested, tmp_path / "out4.zip")

    # Symlinked nested directory inside agents/
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    app_dir_link = tmp_path / "app_dir_link"
    (app_dir_link / "agents").mkdir(parents=True)
    (app_dir_link / "app.json").write_text("{}", encoding="utf-8")
    (app_dir_link / "agents" / "linked_agent").symlink_to(outside_dir)
    with pytest.raises(ValueError, match="Symlinks are not permitted"):
        package_app_archive(app_dir_link, tmp_path / "out5.zip")

    # Symlinked output_zip target must be rejected without modifying target
    target_file = tmp_path / "precious_target.txt"
    target_file.write_text("do not overwrite", encoding="utf-8")
    link_zip = tmp_path / "out_symlink.zip"
    link_zip.symlink_to(target_file)
    with pytest.raises(ValueError, match="Symlinks are not permitted"):
        package_app_archive(real_app, link_zip)
    assert target_file.read_text("utf-8") == "do not overwrite"


# Reproduces QA-002: app.yml skips $env_var and boolean env resolution.
def test_package_app_yml_resolves_env_vars_and_booleans(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app_yml_env"
    app_dir.mkdir()
    (app_dir / "app.yml").write_text(
        "displayName: YmlEnvApp\n"
        "variableDeclarations:\n"
        "  - name: api_host\n"
        "    schema:\n"
        "      type: STRING\n"
        '      default: "$env_var"\n'
        "languageSettings:\n"
        "  defaultLanguageCode: en\n"
        '  enableMultilingualSupport: "$env_var"\n',
        encoding="utf-8",
    )

    # Without environment.json, packaging must fail fast on unresolved $env_var.
    with pytest.raises(ValueError, match=r"environment\.json was not found"):
        package_app_archive(app_dir, tmp_path / "missing_env.zip")

    env_file = tmp_path / "prod_env.json"
    env_file.write_text(
        json.dumps(
            {
                "app": {
                    "variableDeclarations": [
                        {
                            "name": "api_host",
                            "schema": {"default": "api.yml.example.com"},
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
        tmp_path / "resolved_yml.zip",
        env_file,
        resolved_env_vars=resolved,
    )
    assert resolved == ["api_host"]
    with zipfile.ZipFile(zip_path, "r") as zf:
        staged = yaml.safe_load(zf.read("app.yml").decode("utf-8"))
    assert (
        staged["variableDeclarations"][0]["schema"]["default"]
        == "api.yml.example.com"
    )
    assert staged["languageSettings"]["enableMultilingualSupport"] is True


# Reproduces QA-004: package_app_archive corrupts unquoted YAML 1.2 `no` to False.
def test_package_app_yaml_preserves_yaml12_language_code_no(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app_yaml12"
    app_dir.mkdir()
    (app_dir / "app.yaml").write_text(
        "displayName: Nordic App\n"
        "languageSettings:\n"
        "  defaultLanguageCode: no\n"
        "  supportedLanguageCodes: [en, no]\n"
        '  enableMultilingualSupport: "$env_var"\n'
        "variableDeclarations:\n"
        "  - name: api_host\n"
        "    schema:\n"
        "      type: STRING\n"
        '      default: "$env_var"\n',
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
                            "schema": {"default": "api.no.example.com"},
                        }
                    ],
                    "languageSettings": {"enableMultilingualSupport": True},
                }
            }
        ),
        encoding="utf-8",
    )
    zip_path = package_app_archive(app_dir, tmp_path / "yaml12.zip", env_file)
    with zipfile.ZipFile(zip_path, "r") as zf:
        staged = yaml.safe_load(zf.read("app.yaml").decode("utf-8"))
    assert staged["languageSettings"]["defaultLanguageCode"] == "no"
    assert staged["languageSettings"]["supportedLanguageCodes"] == ["en", "no"]
