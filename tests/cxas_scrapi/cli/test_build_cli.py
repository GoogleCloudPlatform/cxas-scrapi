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

"""Unit tests for `cxas build` and `cxas package` CLI commands."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from cxas_scrapi.cli.build_cli import app_build, app_package
from cxas_scrapi.cli.main import get_parser

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


def _setup_cli_workspace(tmp_path: Path) -> tuple[Path, Path]:
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "app.json").write_text(
        json.dumps({"rootAgent": "root"}), encoding="utf-8"
    )
    (core_dir / "global_instruction.txt").write_text(
        "Hello @@profile.display_name@@\n", encoding="utf-8"
    )

    manifests_dir = tmp_path / "manifests"
    p1 = manifests_dir / "profiles" / "profile_1"
    p1.mkdir(parents=True)
    (p1 / "identity.yaml").write_text(
        "profile_id: profile_1\ndisplay_name: Profile One\n", encoding="utf-8"
    )
    (p1 / "channel_policy.yaml").write_text(
        "profile_id: profile_1\n"
        "channels:\n"
        "  WEB:\n"
        "    supported_modalities: ['TEXT']\n"
        "  VOICE_TELEPHONY:\n"
        "    supported_modalities: ['AUDIO']\n",
        encoding="utf-8",
    )
    envs_dir = manifests_dir / "envs"
    envs_dir.mkdir()
    (envs_dir / "prod.yaml").write_text("environment: prod\n", encoding="utf-8")
    return core_dir, manifests_dir


def test_parser_registers_build_and_package() -> None:
    parser = get_parser()
    build_args = parser.parse_args(
        [
            "build",
            "--core-dir",
            "core",
            "--manifests-dir",
            "manifests",
            "--env",
            "prod",
            "--out",
            "dist",
            "--all",
            "--zip",
            "--json",
        ]
    )
    assert build_args.func is app_build
    assert build_args.all is True
    assert build_args.zip is True
    assert build_args.json is True

    pkg_args = parser.parse_args(
        ["package", "--app-dir", "dist/profile_1/web", "--zip", "out.zip"]
    )
    assert pkg_args.func is app_package
    assert pkg_args.zip == "out.zip"


def test_cli_build_single_json_and_package(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    core_dir, manifests_dir = _setup_cli_workspace(tmp_path)
    out_dir = tmp_path / "cli_out"
    app_build(
        argparse.Namespace(
            core_dir=str(core_dir),
            manifests_dir=str(manifests_dir),
            profile="profile_1",
            channel="WEB",
            env="prod",
            out=str(out_dir),
            all=False,
            zip=True,
            json=True,
        )
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["profile_id"] == "profile_1"
    assert payload["channel"] == "WEB"
    assert Path(payload["zip_path"]).is_file()

    zip_path = tmp_path / "packaged.zip"
    app_package(
        argparse.Namespace(
            app_dir=str(out_dir), zip=str(zip_path), env_file=None
        )
    )
    assert zip_path.is_file()
    assert "Packaged app to" in capsys.readouterr().out


def test_cli_build_all_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    core_dir, manifests_dir = _setup_cli_workspace(tmp_path)
    out_root = tmp_path / "all_out"
    app_build(
        argparse.Namespace(
            core_dir=str(core_dir),
            manifests_dir=str(manifests_dir),
            profile=None,
            channel=None,
            env="prod",
            out=str(out_root),
            all=True,
            zip=False,
            json=True,
        )
    )
    reports = json.loads(capsys.readouterr().out)
    assert len(reports) == 2
    assert {r["channel"] for r in reports} == {"WEB", "VOICE_TELEPHONY"}


def test_cli_build_and_package_error_exits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    core_dir, manifests_dir = _setup_cli_workspace(tmp_path)

    # Missing --profile / --channel without --all
    with pytest.raises(SystemExit) as exc:
        app_build(
            argparse.Namespace(
                core_dir=str(core_dir),
                manifests_dir=str(manifests_dir),
                profile=None,
                channel=None,
                env="prod",
                out=str(tmp_path / "out1"),
                all=False,
                zip=False,
                json=False,
            )
        )
    assert exc.value.code == 1
    assert "--profile and --channel are required" in capsys.readouterr().err

    # ManifestError caught cleanly
    with pytest.raises(SystemExit) as exc2:
        app_build(
            argparse.Namespace(
                core_dir=str(core_dir),
                manifests_dir=str(tmp_path / "missing_manifests"),
                profile="profile_1",
                channel="WEB",
                env="prod",
                out=str(tmp_path / "out2"),
                all=False,
                zip=False,
                json=False,
            )
        )
    assert exc2.value.code == 1
    assert "Build Error:" in capsys.readouterr().err

    # Package missing directory caught cleanly
    with pytest.raises(SystemExit) as exc3:
        app_package(
            argparse.Namespace(
                app_dir=str(tmp_path / "no_such_app"),
                zip=str(tmp_path / "nope.zip"),
                env_file=None,
            )
        )
    assert exc3.value.code == 1
    assert "Package Error:" in capsys.readouterr().err


def test_cli_e2e_build_all_zip_and_package_env_resolution(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_cli_workspace(tmp_path)

    out_single = tmp_path / "cli_single"
    r1 = _run_cli(
        [
            "build",
            "--core-dir",
            str(core_dir),
            "--manifests-dir",
            str(manifests_dir),
            "--profile",
            "profile_1",
            "--channel",
            "WEB",
            "--env",
            "prod",
            "--out",
            str(out_single),
            "--zip",
            "--json",
        ]
    )
    assert r1.returncode == 0, r1.stderr
    payload = json.loads(r1.stdout)
    assert payload["profile_id"] == "profile_1"
    assert payload["channel"] == "WEB"
    assert Path(payload["zip_path"]).is_file()

    out_all = tmp_path / "cli_all"
    r2 = _run_cli(
        [
            "build",
            "--core-dir",
            str(core_dir),
            "--manifests-dir",
            str(manifests_dir),
            "--env",
            "prod",
            "--out",
            str(out_all),
            "--all",
            "--zip",
            "--json",
        ]
    )
    assert r2.returncode == 0, r2.stderr
    reports = json.loads(r2.stdout)
    assert len(reports) == 2
    assert {r["channel"] for r in reports} == {"WEB", "VOICE_TELEPHONY"}

    # Verify `cxas package` resolves $env_var and boolean overrides cleanly.
    app_dir = tmp_path / "env_app"
    app_dir.mkdir()
    (app_dir / "app.json").write_text(
        json.dumps(
            {
                "displayName": "CliEnvApp",
                "variableDeclarations": [
                    {
                        "name": "api_host",
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
                            "schema": {"default": "api.prod.example.com"},
                        }
                    ],
                    "languageSettings": {"enableMultilingualSupport": True},
                }
            }
        ),
        encoding="utf-8",
    )
    out_zip = tmp_path / "cli_resolved.zip"
    r3 = _run_cli(
        [
            "package",
            "--app-dir",
            str(app_dir),
            "--env-file",
            str(env_file),
            "--zip",
            str(out_zip),
        ]
    )
    assert r3.returncode == 0, r3.stderr
    assert "Packaged app to" in r3.stdout
    with zipfile.ZipFile(out_zip, "r") as zf:
        staged = json.loads(zf.read("app.json"))
    assert (
        staged["variableDeclarations"][0]["schema"]["default"]
        == "api.prod.example.com"
    )
    assert staged["languageSettings"]["enableMultilingualSupport"] is True


def test_cli_e2e_user_errors_no_tracebacks_and_help_intact(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _setup_cli_workspace(tmp_path)

    error_cases = [
        [
            "build",
            "--core-dir",
            str(core_dir),
            "--manifests-dir",
            str(manifests_dir),
            "--env",
            "prod",
            "--out",
            str(tmp_path / "o1"),
        ],
        [
            "build",
            "--core-dir",
            str(core_dir),
            "--manifests-dir",
            str(tmp_path / "missing_manifests"),
            "--profile",
            "profile_1",
            "--channel",
            "WEB",
            "--env",
            "prod",
            "--out",
            str(tmp_path / "o2"),
        ],
        [
            "build",
            "--core-dir",
            str(core_dir),
            "--manifests-dir",
            str(manifests_dir),
            "--profile",
            "profile_1",
            "--channel",
            "WHATSAPP",
            "--env",
            "prod",
            "--out",
            str(tmp_path / "o3"),
        ],
        [
            "package",
            "--app-dir",
            str(tmp_path / "no_such_app"),
            "--zip",
            str(tmp_path / "nope.zip"),
        ],
        [
            "package",
            "--app-dir",
            str(core_dir),
            "--env-file",
            str(tmp_path / "missing_env.json"),
            "--zip",
            str(tmp_path / "nope2.zip"),
        ],
    ]
    for cli_args in error_cases:
        res = _run_cli(cli_args)
        assert res.returncode != 0, f"Expected failure for {cli_args}"
        assert "Traceback" not in res.stderr

    for sub in (["--help"], ["lint", "--help"], ["push", "--help"]):
        res = _run_cli(sub)
        assert res.returncode == 0, res.stderr
    root_help = _run_cli(["--help"]).stdout
    assert "build" in root_help
    assert "package" in root_help


# Reproduces QA-001: CLI `cxas package` resolves symlinks before validation.
def test_cli_package_rejects_symlink_app_dir_and_env_file(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "real_app"
    app_dir.mkdir()
    (app_dir / "app.json").write_text("{}", encoding="utf-8")

    outside = tmp_path / "outside_secret.json"
    outside.write_text('{"secret": "leaked"}', encoding="utf-8")

    link_app = tmp_path / "link_app"
    link_app.symlink_to(app_dir)
    res_app = _run_cli(
        [
            "package",
            "--app-dir",
            str(link_app),
            "--zip",
            str(tmp_path / "from_link_app.zip"),
        ]
    )
    assert res_app.returncode != 0
    assert "Symlinks are not permitted" in res_app.stderr
    assert "Traceback" not in res_app.stderr

    link_env = tmp_path / "link_env.json"
    link_env.symlink_to(outside)
    res_env = _run_cli(
        [
            "package",
            "--app-dir",
            str(app_dir),
            "--env-file",
            str(link_env),
            "--zip",
            str(tmp_path / "from_link_env.zip"),
        ]
    )
    assert res_env.returncode != 0
    assert "Symlinks are not permitted" in res_env.stderr
    assert "Traceback" not in res_env.stderr
