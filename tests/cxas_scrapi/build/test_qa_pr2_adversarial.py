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

"""Adversarial E2E and edge-case QA tests for PR 2 (Multi-Brand Build).

Contains:
- Passing edge-case & CLI E2E regression tests (``test_qa_pr2_...``).
- Failing reproduction tests for open QA defects (``test_qa_pr2_001_...``
  through ``test_qa_pr2_011_...``), which must turn green once fixed.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from cxas_scrapi.build.builder import BuildError, build_app
from cxas_scrapi.build.manifests import ManifestError, load_manifests
from cxas_scrapi.core.deployments import Deployments
from cxas_scrapi.core.packaging import package_app_archive

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


def _make_base_workspace(tmp_path: Path) -> tuple[Path, Path]:
    core_dir = tmp_path / "core"
    (core_dir / "agents" / "root_agent").mkdir(parents=True)
    (core_dir / "agents" / "web_only_dir").mkdir(parents=True)
    (core_dir / "tools" / "active_tool" / "python_function").mkdir(parents=True)
    (core_dir / "tools" / "disabled_tool_dir").mkdir(parents=True)
    (core_dir / "guardrails" / "brand_guard").mkdir(parents=True)

    (core_dir / "app.json").write_text(
        json.dumps(
            {
                "displayName": "@@brand.display_name@@",
                "rootAgent": "root_agent",
                "globalInstruction": "global_instruction.txt",
            }
        ),
        encoding="utf-8",
    )
    (core_dir / "environment.json").write_text(
        json.dumps(
            {
                "region": "us-central1",
                "tools": {
                    "disabled_tool_display": {"endpoint": "https://old.local"},
                    "active_tool": {"endpoint": "https://active.local"},
                },
            }
        ),
        encoding="utf-8",
    )
    (core_dir / "global_instruction.txt").write_text(
        "Welcome to @@brand.display_name@@ (@@brand.legal_entity@@).\n"
        "Account: @@brand.lexicon.account@@\n"
        "Support: @@brand.urls.support@@\n"
        "API: @@env.api:auth@@\n"
        "Routing: @@routing.core_api@@\n",
        encoding="utf-8",
    )
    (core_dir / "agents" / "root_agent" / "root_agent.json").write_text(
        json.dumps(
            {
                "displayName": "root_agent",
                "childAgents": ["web_only_display"],
                "tools": ["disabled_tool_display", "active_tool"],
            }
        ),
        encoding="utf-8",
    )
    (core_dir / "agents" / "root_agent" / "instruction.txt").write_text(
        "Help @@brand.display_name@@ users.\n",
        encoding="utf-8",
    )
    (core_dir / "agents" / "web_only_dir" / "web_only_dir.json").write_text(
        json.dumps({"displayName": "web_only_display"}),
        encoding="utf-8",
    )
    (core_dir / "tools" / "active_tool" / "active_tool.json").write_text(
        json.dumps({"displayName": "active_tool"}),
        encoding="utf-8",
    )
    (
        core_dir
        / "tools"
        / "active_tool"
        / "python_function"
        / "python_code.py"
    ).write_text(
        "def active_tool() -> str:\n"
        '    return "Welcome to @@brand.display_name@@"\n',
        encoding="utf-8",
    )
    (
        core_dir / "tools" / "disabled_tool_dir" / "disabled_tool_dir.json"
    ).write_text(
        json.dumps({"displayName": "disabled_tool_display"}),
        encoding="utf-8",
    )
    (core_dir / "guardrails" / "brand_guard" / "brand_guard.json").write_text(
        json.dumps(
            {
                "llmPolicy": {
                    "prompt": "Guardrail for @@brand.display_name@@ "
                    "(@@brand.legal_entity@@ / @@brand.lexicon.account@@)."
                }
            }
        ),
        encoding="utf-8",
    )

    manifests_dir = tmp_path / "manifests"
    b1 = manifests_dir / "brands" / "brand_1"
    b1.mkdir(parents=True)
    (b1 / "identity.yaml").write_text(
        yaml.safe_dump(
            {
                "brand_id": "brand_1",
                "display_name": "Brand One",
                "assistant_title": "Assistant",
                "legal_entity": "Brand One Corp",
                "tone_guidelines": "Clear and concise.",
                "lexicon": {"account": "Checking Account"},
                "urls": {"support": "https://support.brand1.example.com"},
                "tts_voices": {
                    "en": {"voice": "en-US-Standard-A", "speaking_rate": 1.0}
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (b1 / "channel_policy.yaml").write_text(
        yaml.safe_dump(
            {
                "brand_id": "brand_1",
                "supported_countries": ["US"],
                "api_routing": {"core_api": "https://core.brand1.example.com"},
                "channels": {
                    "WEB": {
                        "supported_modalities": ["TEXT"],
                        "deeplink_widgets": True,
                    },
                    "VOICE_TELEPHONY": {
                        "disabled_agents": ["web_only_display"],
                        "disabled_tools": ["disabled_tool_display"],
                        "supported_modalities": ["AUDIO"],
                    },
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    envs_dir = manifests_dir / "envs"
    envs_dir.mkdir(parents=True)
    (envs_dir / "prod.yaml").write_text(
        yaml.safe_dump(
            {
                "environment": "prod",
                "templates": {
                    "api": "https://api.example.com/{brand_slug}/{env}/{service}"
                },
                "overrides": {"region": "eu-west1"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return core_dir, manifests_dir


# ── Passing Edge-Case & E2E Verification Tests ──────────────────────────


def test_qa_pr2_json_escaping_and_unicode_in_brand_identity(
    tmp_path: Path,
) -> None:
    """Verify quotes, backslashes, newlines, and Unicode stay valid JSON/YAML."""
    core_dir, manifests_dir = _make_base_workspace(tmp_path)
    # Remove .py token usage here; .py escaping is tested separately in QA-006.
    (
        core_dir
        / "tools"
        / "active_tool"
        / "python_function"
        / "python_code.py"
    ).write_text("def active_tool() -> str:\n    return 'ok'\n", "utf-8")

    tricky_display = 'Acme "Prime" \\ Bank äöüß€\nLine 2'
    tricky_legal = 'Acme "Legal" GmbH & Co. KG \\ €'
    tricky_lex = 'Giro "Konto" \\ äöüß€'
    (manifests_dir / "brands" / "brand_1" / "identity.yaml").write_text(
        yaml.safe_dump(
            {
                "brand_id": "brand_1",
                "display_name": tricky_display,
                "legal_entity": tricky_legal,
                "lexicon": {"account": tricky_lex},
                "urls": {"support": "https://example.de/hilfe?q=äöü&a=1"},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_unicode"
    build_app(core_dir, manifests_dir, "brand_1", "WEB", "prod", out_dir)

    app_data = json.loads((out_dir / "app.json").read_text("utf-8"))
    assert app_data["displayName"] == tricky_display

    guard_data = json.loads(
        (out_dir / "guardrails" / "brand_guard" / "brand_guard.json").read_text(
            "utf-8"
        )
    )
    assert tricky_display in guard_data["llmPolicy"]["prompt"]
    assert tricky_legal in guard_data["llmPolicy"]["prompt"]
    assert tricky_lex in guard_data["llmPolicy"]["prompt"]

    dep_data = yaml.safe_load(
        (out_dir / "deployments" / "web.yaml").read_text("utf-8")
    )
    assert dep_data["displayName"] == f"{tricky_display} WEB"

    gi_text = (out_dir / "global_instruction.txt").read_text("utf-8")
    assert tricky_display in gi_text
    assert tricky_lex in gi_text


def test_qa_pr2_binary_files_preserved_in_core(tmp_path: Path) -> None:
    """Verify binary assets (.png, .wav, .bin) in core/ do not crash build."""
    core_dir, manifests_dir = _make_base_workspace(tmp_path)
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe\x80\x81"
    wav_bytes = b"RIFF\x24\x00\x00\x00WAVEfmt \xff\xfe\x00\x80"
    bin_bytes = bytes(range(256))

    (core_dir / "logo.png").write_bytes(png_bytes)
    (core_dir / "agents" / "root_agent" / "greeting.wav").write_bytes(wav_bytes)
    (core_dir / "tools" / "active_tool" / "data.bin").write_bytes(bin_bytes)

    out_dir = tmp_path / "out_bin"
    build_app(core_dir, manifests_dir, "brand_1", "WEB", "prod", out_dir)

    assert (out_dir / "logo.png").read_bytes() == png_bytes
    assert (
        out_dir / "agents" / "root_agent" / "greeting.wav"
    ).read_bytes() == wav_bytes
    assert (
        out_dir / "tools" / "active_tool" / "data.bin"
    ).read_bytes() == bin_bytes


@pytest.mark.parametrize(
    "bad_token",
    [
        "@@brand.nonexistent@@",
        "@@env.missing@@",
        "@@routing.bad@@",
    ],
)
def test_qa_pr2_unresolved_tokens_fail_fast(
    tmp_path: Path, bad_token: str
) -> None:
    """Verify unknown brand/env/routing tokens raise BuildError."""
    core_dir, manifests_dir = _make_base_workspace(tmp_path)
    (core_dir / "global_instruction.txt").write_text(
        f"Broken token: {bad_token}\n", encoding="utf-8"
    )
    with pytest.raises(BuildError, match="Unresolved placeholders"):
        build_app(
            core_dir, manifests_dir, "brand_1", "WEB", "prod", tmp_path / "out"
        )


@pytest.mark.parametrize(
    ("content", "expected_err"),
    [
        ("{@startChannel_WEB}\nUnclosed\n", "Unbalanced conditional tags"),
        ("{@endChannel_WEB}\n", "Unbalanced or mismatched conditional"),
        (
            "{@startChannel_WEB}\n{@endBrand_brand_1}\n",
            "Unbalanced or mismatched conditional",
        ),
        (
            "{@startModality_Voice}\nUnclosed modality\n",
            "Unbalanced modality tags",
        ),
        (
            "{@startModality_Voice}\n{@endModality_Text}\n",
            "Unbalanced or mismatched modality",
        ),
    ],
)
def test_qa_pr2_unclosed_and_mismatched_conditionals_fail(
    tmp_path: Path, content: str, expected_err: str
) -> None:
    """Verify unclosed/mismatched conditional and modality directives fail."""
    core_dir, manifests_dir = _make_base_workspace(tmp_path)
    (core_dir / "global_instruction.txt").write_text(content, encoding="utf-8")
    with pytest.raises(BuildError, match=expected_err):
        build_app(
            core_dir, manifests_dir, "brand_1", "WEB", "prod", tmp_path / "out"
        )


def test_qa_pr2_path_traversal_brand_id_and_core_overlap_rejected(
    tmp_path: Path,
) -> None:
    """Verify traversal brand_id and out_dir overlapping core_dir are rejected."""
    core_dir, manifests_dir = _make_base_workspace(tmp_path)

    with pytest.raises(ManifestError, match="Unknown brand"):
        build_app(
            core_dir,
            manifests_dir,
            "../../escape",
            "WEB",
            "prod",
            tmp_path / "out_escape",
        )

    for overlapping in (core_dir, core_dir / "sub", core_dir.parent):
        with pytest.raises(BuildError, match="must not overlap"):
            build_app(
                core_dir,
                manifests_dir,
                "brand_1",
                "WEB",
                "prod",
                overlapping,
            )


def test_qa_pr2_package_determinism_and_exclusions(tmp_path: Path) -> None:
    """Verify byte-identical ZIPs across sequential builds and junk exclusion."""
    core_dir, manifests_dir = _make_base_workspace(tmp_path)
    (core_dir / "README.md").write_text("junk", encoding="utf-8")
    (core_dir / ".hidden_file").write_text("secret", encoding="utf-8")
    pycache = core_dir / "tools" / "active_tool" / "__pycache__"
    pycache.mkdir(parents=True)
    (pycache / "python_code.cpython-313.pyc").write_bytes(b"\x00\x01")
    (core_dir / "tools" / "active_tool" / ".DS_Store").write_text("x", "utf-8")
    (core_dir / "scratch_junk").mkdir()
    (core_dir / "scratch_junk" / "notes.txt").write_text("junk", "utf-8")

    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    rep1 = build_app(
        core_dir,
        manifests_dir,
        "brand_1",
        "VOICE_TELEPHONY",
        "prod",
        out1,
        package_zip=True,
    )
    os.utime(core_dir / "app.json", (1_700_000_000, 1_700_000_000))
    rep2 = build_app(
        core_dir,
        manifests_dir,
        "brand_1",
        "VOICE_TELEPHONY",
        "prod",
        out2,
        package_zip=True,
    )

    assert rep1.zip_path is not None
    assert rep2.zip_path is not None
    sha1 = hashlib.sha256(rep1.zip_path.read_bytes()).hexdigest()
    sha2 = hashlib.sha256(rep2.zip_path.read_bytes()).hexdigest()
    assert sha1 == sha2

    with zipfile.ZipFile(rep1.zip_path, "r") as zf:
        names = zf.namelist()
        assert names == sorted(names)
        assert "app.json" in names
        assert "README.md" not in names
        assert ".hidden_file" not in names
        assert not any("scratch_junk" in n for n in names)
        assert not any("deployments/" in n for n in names)
        assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)
        assert not any(".DS_Store" in n for n in names)


def test_qa_pr2_package_direct_env_vars_and_bool_coercion_534(
    tmp_path: Path,
) -> None:
    """Verify #534 boolean coercion + variableDefaults in package_app_archive."""
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
                    },
                    {
                        "name": "api_host",
                        "schema": {"type": "STRING", "default": "$env_var"},
                    },
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
                        {"name": "feature_flag", "schema": {"default": True}},
                        {
                            "name": "api_host",
                            "schema": {"default": "api.prod.example.com"},
                        },
                    ],
                    "languageSettings": {
                        "enableMultilingualSupport": "true",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    resolved: list[str] = []
    zip_path = package_app_archive(
        app_dir,
        tmp_path / "pkg.zip",
        env_file,
        resolved_env_vars=resolved,
    )
    assert resolved == ["feature_flag", "api_host"]
    with zipfile.ZipFile(zip_path, "r") as zf:
        staged_app = json.loads(zf.read("app.json"))
    assert staged_app["variableDeclarations"][0]["schema"]["default"] is True
    assert (
        staged_app["variableDeclarations"][1]["schema"]["default"]
        == "api.prod.example.com"
    )
    assert staged_app["languageSettings"]["enableMultilingualSupport"] is True


def test_qa_pr2_cli_e2e_build_all_zip_and_package_happy_paths(
    tmp_path: Path,
) -> None:
    """Verify real CLI subprocesses for build, build --all --zip, and package."""
    core_dir, manifests_dir = _make_base_workspace(tmp_path)

    out_single = tmp_path / "cli_single"
    r1 = _run_cli(
        [
            "build",
            "--core-dir",
            str(core_dir),
            "--manifests-dir",
            str(manifests_dir),
            "--brand",
            "brand_1",
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
    assert payload["brand_id"] == "brand_1"
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

    pkg_zip = tmp_path / "cli_packaged.zip"
    r3 = _run_cli(
        ["package", "--app-dir", str(out_single), "--zip", str(pkg_zip)]
    )
    assert r3.returncode == 0, r3.stderr
    assert pkg_zip.is_file()
    assert "Packaged app to" in r3.stdout


def test_qa_pr2_cli_e2e_user_errors_no_tracebacks(tmp_path: Path) -> None:
    """Verify CLI user errors exit non-zero cleanly without Python tracebacks."""
    core_dir, manifests_dir = _make_base_workspace(tmp_path)

    cases = [
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
            "--brand",
            "brand_1",
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
            "--brand",
            "brand_1",
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
    for cli_args in cases:
        res = _run_cli(cli_args)
        assert res.returncode != 0, f"Expected failure for {cli_args}"
        assert "Traceback" not in res.stderr, (
            f"Raw traceback leaked for {cli_args}:\n{res.stderr}"
        )


def test_qa_pr2_cli_existing_commands_help_and_no_leakage() -> None:
    """Verify existing CLI subcommands remain intact with no PR 1/3/4 leaks."""
    for sub in (
        ["--help"],
        ["lint", "--help"],
        ["push", "--help"],
        ["pull", "--help"],
    ):
        res = _run_cli(sub)
        assert res.returncode == 0, f"Failed for {sub}: {res.stderr}"
    root_help = _run_cli(["--help"]).stdout
    assert "build" in root_help
    assert "package" in root_help
    for leaked in ("P001", "P005", "governed", "bundle-runtime"):
        assert leaked not in root_help


# ── Open Bug Reproduction Tests (Expected to FAIL until fixed) ──────────


def test_qa_pr2_001_out_dir_overlapping_manifests_dir_rejected(
    tmp_path: Path,
) -> None:
    """QA-PR2-001 (HIGH): out_dir overlapping manifests_dir must not wipe it."""
    core_dir, manifests_dir = _make_base_workspace(tmp_path)

    with pytest.raises(BuildError, match="overlap"):
        build_app(
            core_dir,
            manifests_dir,
            "brand_1",
            "WEB",
            "prod",
            manifests_dir,
        )
    assert (manifests_dir / "brands" / "brand_1" / "identity.yaml").is_file()


def test_qa_pr2_002_cli_package_and_build_zip_resolve_env_vars_and_booleans(
    tmp_path: Path,
) -> None:
    """QA-PR2-002 (HIGH): `cxas package` must resolve $env_var & #534 bools."""
    app_dir = tmp_path / "app"
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
    res = _run_cli(
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
    assert res.returncode == 0, res.stderr
    with zipfile.ZipFile(out_zip, "r") as zf:
        staged = json.loads(zf.read("app.json"))
    assert (
        staged["variableDeclarations"][0]["schema"]["default"]
        == "api.prod.example.com"
    )
    assert staged["languageSettings"]["enableMultilingualSupport"] is True


def test_qa_pr2_003_deployments_whatsapp_and_instagram_do_not_crash() -> None:
    """QA-PR2-003 (HIGH): WHATSAPP/INSTAGRAM ChannelType must not crash API."""
    dep = Deployments.__new__(Deployments)
    dep.app_name = "projects/p/locations/l/apps/a"
    dep.client = MagicMock()

    for ch in (
        Deployments.ChannelType.WHATSAPP,
        Deployments.ChannelType.INSTAGRAM,
    ):
        dep.create_deployment("dep1", "Msg Dep", "v1", channel_type=ch)


def test_qa_pr2_004_malformed_json_in_core_raises_build_error(
    tmp_path: Path,
) -> None:
    """QA-PR2-004 (HIGH): Malformed JSON in core/ must raise BuildError."""
    core_dir, manifests_dir = _make_base_workspace(tmp_path)
    (core_dir / "app.json").write_text(
        '{"displayName": "@@brand.display_name@@",}', encoding="utf-8"
    )

    with pytest.raises(BuildError, match=r"(?i)json"):
        build_app(
            core_dir,
            manifests_dir,
            "brand_1",
            "WEB",
            "prod",
            tmp_path / "out_bad_json",
        )


@pytest.mark.parametrize("bad_wrapper", ["../../escape", "/etc/evil", "a/../b"])
def test_qa_pr2_005_package_wrapper_dir_rejects_path_traversal(
    tmp_path: Path, bad_wrapper: str
) -> None:
    """QA-PR2-005 (HIGH): package_app_archive must block wrapper_dir zip-slip."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "app.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match=r"(?i)wrapper|path|invalid|traversal"):
        package_app_archive(
            app_dir, tmp_path / "out.zip", wrapper_dir=bad_wrapper
        )


def test_qa_pr2_006_python_token_substitution_preserves_valid_syntax(
    tmp_path: Path,
) -> None:
    """QA-PR2-006 (HIGH): Brand quotes/newlines must not emit invalid .py."""
    core_dir, manifests_dir = _make_base_workspace(tmp_path)
    (manifests_dir / "brands" / "brand_1" / "identity.yaml").write_text(
        yaml.safe_dump(
            {
                "brand_id": "brand_1",
                "display_name": 'Acme "Prime" \\ Bank\nLine2',
                "legal_entity": "Acme Legal",
                "lexicon": {"account": "Account"},
                "urls": {"support": "https://example.com"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_py_quotes"
    try:
        build_app(core_dir, manifests_dir, "brand_1", "WEB", "prod", out_dir)
    except BuildError:
        # Failing fast with BuildError is acceptable; silently shipping invalid
        # Python is not.
        return
    py_src = (
        out_dir / "tools" / "active_tool" / "python_function" / "python_code.py"
    ).read_text("utf-8")
    ast.parse(py_src)


def test_qa_pr2_007_unclosed_env_service_token_does_not_corrupt_next_line(
    tmp_path: Path,
) -> None:
    """QA-PR2-007 (MEDIUM): Unclosed @@env.api:users must fail, not eat line 2."""
    core_dir, manifests_dir = _make_base_workspace(tmp_path)
    (core_dir / "global_instruction.txt").write_text(
        "Endpoint: @@env.api:users\nWelcome to @@brand.display_name@@\n",
        encoding="utf-8",
    )

    with pytest.raises(BuildError):
        build_app(
            core_dir,
            manifests_dir,
            "brand_1",
            "WEB",
            "prod",
            tmp_path / "out_unclosed_env",
        )


def test_qa_pr2_008_malformed_env_manifest_and_null_app_sections_clean_errors(
    tmp_path: Path,
) -> None:
    """QA-PR2-008 (MEDIUM): Bad templates/YAML/null JSON must not leak crashes."""
    core_dir, manifests_dir = _make_base_workspace(tmp_path)

    # 1. Unclosed brace in env template should raise BuildError (no traceback)
    (manifests_dir / "envs" / "bad_tpl.yaml").write_text(
        "environment: bad_tpl\ntemplates:\n  api: 'https://ex/{brand'\n",
        encoding="utf-8",
    )
    with pytest.raises(BuildError):
        build_app(
            core_dir,
            manifests_dir,
            "brand_1",
            "WEB",
            "bad_tpl",
            tmp_path / "out_bad_tpl",
        )

    # 2. Format-string attribute traversal gadget must be rejected
    (manifests_dir / "envs" / "gadget.yaml").write_text(
        "environment: gadget\ntemplates:\n  api: '{brand.__class__.__name__}'\n",
        encoding="utf-8",
    )
    with pytest.raises(BuildError):
        build_app(
            core_dir,
            manifests_dir,
            "brand_1",
            "WEB",
            "gadget",
            tmp_path / "out_gadget",
        )

    # 3. Non-dict templates in env YAML must raise ManifestError (not TypeError)
    bad_m = tmp_path / "bad_m"
    (bad_m / "brands" / "brand_1").mkdir(parents=True)
    (bad_m / "brands" / "brand_1" / "identity.yaml").write_text(
        "brand_id: brand_1\ndisplay_name: B1\n", encoding="utf-8"
    )
    (bad_m / "envs").mkdir(parents=True)
    (bad_m / "envs" / "prod.yaml").write_text(
        "environment: prod\ntemplates: 123\n", encoding="utf-8"
    )
    with pytest.raises(ManifestError):
        load_manifests(bad_m)

    # 4. audioProcessingConfig: null in app.json must not crash with TypeError
    (core_dir / "app.json").write_text(
        json.dumps(
            {
                "displayName": "@@brand.display_name@@",
                "rootAgent": "root_agent",
                "audioProcessingConfig": None,
                "languageSettings": None,
            }
        ),
        encoding="utf-8",
    )
    out_null = tmp_path / "out_null"
    build_app(core_dir, manifests_dir, "brand_1", "WEB", "prod", out_null)
    app_data = json.loads((out_null / "app.json").read_text("utf-8"))
    assert "synthesizeSpeechConfigs" in app_data["audioProcessingConfig"]


def test_qa_pr2_009_overlapping_and_disabled_branch_malformed_conditionals_rejected(
    tmp_path: Path,
) -> None:
    """QA-PR2-009 (MEDIUM): Overlapping & disabled-branch bad tags must fail."""
    core_dir, manifests_dir = _make_base_workspace(tmp_path)

    # Overlapping inline tags
    (core_dir / "global_instruction.txt").write_text(
        "{@startChannel_WEB}A{@startBrand_brand_1}B"
        "{@endChannel_WEB}C{@endBrand_brand_1}\n",
        encoding="utf-8",
    )
    with pytest.raises(BuildError):
        build_app(
            core_dir,
            manifests_dir,
            "brand_1",
            "WEB",
            "prod",
            tmp_path / "out_overlap",
        )

    # Unclosed inline tag inside an inactive/disabled block
    (core_dir / "global_instruction.txt").write_text(
        "{@startChannel_API}\n"
        "Broken {@startBrand_brand_1}unclosed inline\n"
        "{@endChannel_API}\n",
        encoding="utf-8",
    )
    with pytest.raises(BuildError):
        build_app(
            core_dir,
            manifests_dir,
            "brand_1",
            "WEB",
            "prod",
            tmp_path / "out_disabled_bad",
        )


def test_qa_pr2_010_pruned_root_agent_without_valid_entry_agent_raises_build_error(
    tmp_path: Path,
) -> None:
    """QA-PR2-010 (MEDIUM): Pruning rootAgent without valid entry_agent fails."""
    core_dir, manifests_dir = _make_base_workspace(tmp_path)
    (manifests_dir / "brands" / "brand_1" / "channel_policy.yaml").write_text(
        yaml.safe_dump(
            {
                "brand_id": "brand_1",
                "api_routing": {"core_api": "https://core.brand1.example.com"},
                "channels": {
                    "WEB": {
                        "disabled_agents": ["root_agent"],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(BuildError, match=r"(?i)rootAgent|entry_agent"):
        build_app(
            core_dir,
            manifests_dir,
            "brand_1",
            "WEB",
            "prod",
            tmp_path / "out_no_root",
        )


def test_qa_pr2_011_env_overrides_created_without_base_and_pruned_tools_not_reinjected(
    tmp_path: Path,
) -> None:
    """QA-PR2-011 (MEDIUM): Env overrides work without base & respect pruning."""
    core_dir, manifests_dir = _make_base_workspace(tmp_path)

    # Case 1: core/environment.json absent, overrides still emitted
    (core_dir / "environment.json").unlink()
    out1 = tmp_path / "out_no_base_env"
    build_app(core_dir, manifests_dir, "brand_1", "WEB", "prod", out1)
    assert (out1 / "environment.json").is_file()
    assert json.loads((out1 / "environment.json").read_text("utf-8")) == {
        "region": "eu-west1"
    }

    # Case 2: overrides.tools must not re-inject a disabled tool after pruning
    (core_dir / "environment.json").write_text(
        json.dumps({"tools": {"active_tool": {"endpoint": "https://a.local"}}}),
        encoding="utf-8",
    )
    (manifests_dir / "envs" / "prod.yaml").write_text(
        yaml.safe_dump(
            {
                "environment": "prod",
                "templates": {
                    "api": "https://api.example.com/{brand_slug}/{env}/{service}"
                },
                "overrides": {
                    "tools": {
                        "disabled_tool_display": {
                            "endpoint": "https://leaked.local"
                        }
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    out2 = tmp_path / "out_reinject"
    build_app(
        core_dir, manifests_dir, "brand_1", "VOICE_TELEPHONY", "prod", out2
    )
    env_out = json.loads((out2 / "environment.json").read_text("utf-8"))
    assert "disabled_tool_display" not in env_out.get("tools", {})


# ── Round 1 Re-Test Regressions (Introduced in 82637d6e) ────────────────


def test_qa_pr2_013_python_fstring_no_code_injection_and_preserves_escapes(
    tmp_path: Path,
) -> None:
    """QA-PR2-013 (HIGH): f-strings must escape {}/escapes without injection."""
    core_dir, manifests_dir = _make_base_workspace(tmp_path)
    tricky_brand = "Acme {1 + 1} Bank"
    (manifests_dir / "brands" / "brand_1" / "identity.yaml").write_text(
        yaml.safe_dump(
            {
                "brand_id": "brand_1",
                "display_name": tricky_brand,
                "legal_entity": "Acme Legal",
                "lexicon": {"account": "Account"},
                "urls": {"support": "https://example.com"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    py_path = (
        core_dir
        / "tools"
        / "active_tool"
        / "python_function"
        / "python_code.py"
    )
    py_path.write_text(
        "def active_tool(user: str) -> tuple[str, str]:\n"
        '    msg = f"Line1\\nWelcome to @@brand.display_name@@, {user}"\n'
        '    raw_path = rf"C:\\dir\\@@brand.brand_id@@\\{user}"\n'
        "    return msg, raw_path\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_fstring"
    build_app(core_dir, manifests_dir, "brand_1", "WEB", "prod", out_dir)
    rendered = (
        out_dir / "tools" / "active_tool" / "python_function" / "python_code.py"
    ).read_text("utf-8")

    ns: dict[str, object] = {}
    exec(compile(rendered, str(py_path), "exec"), ns)
    fn = ns["active_tool"]
    assert callable(fn)
    msg, raw_path = fn("Alice")
    # 1. Existing \n escape in same FSTRING_MIDDLE preserved (not literal "\\n")
    # 2. Brand braces "{1 + 1}" treated as literal text, NOT evaluated to "2"
    assert msg == "Line1\nWelcome to Acme {1 + 1} Bank, Alice"
    # 3. Existing backslashes in raw f-string not doubled
    assert raw_path == r"C:\dir\brand_1\Alice"


def test_qa_pr2_014_python_triple_quote_ending_in_triple_quotes(
    tmp_path: Path,
) -> None:
    """QA-PR2-014 (MEDIUM): Triple-quoted strings ending in quotes stay valid."""
    core_dir, manifests_dir = _make_base_workspace(tmp_path)
    brand_val = 'Acme """'
    (manifests_dir / "brands" / "brand_1" / "identity.yaml").write_text(
        yaml.safe_dump(
            {
                "brand_id": "brand_1",
                "display_name": brand_val,
                "legal_entity": "Acme Legal",
                "lexicon": {"account": "Account"},
                "urls": {"support": "https://example.com"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    py_path = (
        core_dir
        / "tools"
        / "active_tool"
        / "python_function"
        / "python_code.py"
    )
    py_path.write_text(
        'def active_tool() -> str:\n    return """@@brand.display_name@@"""\n',
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_triple_q"
    build_app(core_dir, manifests_dir, "brand_1", "WEB", "prod", out_dir)
    rendered = (
        out_dir / "tools" / "active_tool" / "python_function" / "python_code.py"
    ).read_text("utf-8")

    ns: dict[str, object] = {}
    exec(compile(rendered, str(py_path), "exec"), ns)
    fn = ns["active_tool"]
    assert callable(fn)
    assert fn() == brand_val
