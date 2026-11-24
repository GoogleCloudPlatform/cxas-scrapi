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
from pathlib import Path

import pytest

from cxas_scrapi.cli.build_cli import app_build, app_package
from cxas_scrapi.cli.main import get_parser


def _setup_cli_workspace(tmp_path: Path) -> tuple[Path, Path]:
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "app.json").write_text(
        json.dumps({"rootAgent": "root"}), encoding="utf-8"
    )
    (core_dir / "global_instruction.txt").write_text(
        "Hello @@brand.display_name@@\n", encoding="utf-8"
    )

    manifests_dir = tmp_path / "manifests"
    b1 = manifests_dir / "brands" / "brand_1"
    b1.mkdir(parents=True)
    (b1 / "identity.yaml").write_text(
        "brand_id: brand_1\ndisplay_name: Brand One\n", encoding="utf-8"
    )
    (b1 / "channel_policy.yaml").write_text(
        "brand_id: brand_1\n"
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
        ["package", "--app-dir", "dist/brand_1/web", "--zip", "out.zip"]
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
            brand="brand_1",
            channel="WEB",
            env="prod",
            out=str(out_dir),
            all=False,
            zip=True,
            json=True,
        )
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["brand_id"] == "brand_1"
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
            brand=None,
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

    # Missing --brand / --channel without --all
    with pytest.raises(SystemExit) as exc:
        app_build(
            argparse.Namespace(
                core_dir=str(core_dir),
                manifests_dir=str(manifests_dir),
                brand=None,
                channel=None,
                env="prod",
                out=str(tmp_path / "out1"),
                all=False,
                zip=False,
                json=False,
            )
        )
    assert exc.value.code == 1
    assert "--brand and --channel are required" in capsys.readouterr().err

    # ManifestError caught cleanly
    with pytest.raises(SystemExit) as exc2:
        app_build(
            argparse.Namespace(
                core_dir=str(core_dir),
                manifests_dir=str(tmp_path / "missing_manifests"),
                brand="brand_1",
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
