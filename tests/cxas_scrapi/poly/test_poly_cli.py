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

"""CLI smoke tests for first-wave poly DX commands."""

import json
from pathlib import Path

import pytest

from cxas_scrapi.cli.main import get_parser


def _run_poly(args: list[str], capsys) -> tuple[int, str, str]:
    parser = get_parser()
    namespace = parser.parse_args(["poly", *args])
    with pytest.raises(SystemExit) as exc:
        namespace.func(namespace)
    captured = capsys.readouterr()
    return int(exc.value.code), captured.out, captured.err


def test_cli_diff_json(base_dir: Path, capsys):
    code, out, _err = _run_poly(
        ["diff", "chat", "--app-dir", str(base_dir), "--json"],
        capsys,
    )

    assert code == 0
    report = json.loads(out)
    assert report["schema_version"] == "poly-diff/v1"
    assert report["summary"]["tools_added"] == 1


def test_cli_doctor_explains_bad_adapter(copied_base: Path, capsys):
    (copied_base / "adapters" / "bad.adapter.yaml").write_text(
        "apiVersion: poly.cxas.dev/v1\n"
        "kind: ChannelAdapter\n"
        "metadata: {channel: bad, displayName: Bad}\n"
        "tools:\n"
        "  - {agent: Test_Agent, add: [ghost_tool]}\n"
    )

    code, out, _err = _run_poly(
        ["doctor", "--app-dir", str(copied_base)],
        capsys,
    )

    assert code == 1
    assert "AD005" in out
    assert "likely fix" in out
    assert "adapters/bad.adapter.yaml" in out


def test_cli_readiness_json(base_dir: Path, capsys):
    code, out, _err = _run_poly(
        ["readiness", "--app-dir", str(base_dir), "--format", "json"],
        capsys,
    )

    assert code == 0
    report = json.loads(out)
    assert report["schema_version"] == "poly-readiness/v1"
    assert report["summary"]["channels"] == 2
    assert any(c["channel"] == "chat" for c in report["channels"])


def test_cli_init_scaffolds_channel(copied_base: Path, capsys):
    code, out, _err = _run_poly(
        [
            "init",
            "--app-dir",
            str(copied_base),
            "--channel",
            "sms",
            "--deployment-target",
            "TWILIO",
            "--modality",
            "VOICE_ONLY",
            "--with-tool",
            "send_sms_card",
            "--with-callback",
            "before_model",
        ],
        capsys,
    )

    assert code == 0
    assert "adapters/sms.adapter.yaml" in out
    assert (copied_base / "adapters" / "sms.adapter.yaml").exists()
    assert (
        copied_base / "adapters" / "sms_tools" / "send_sms_card"
    ).is_dir()
