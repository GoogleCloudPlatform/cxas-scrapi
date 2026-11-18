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

"""Cross-stack integration tests for Rung 1, Python bundler, and drift gate."""

import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
import yaml

from cxas_scrapi.build import (
    check_drift,
    package_bundle_zip,
    run_firewall,
    write_lockfile,
)
from cxas_scrapi.cli import main as cli_main
from cxas_scrapi.core.packaging import package_app_archive

TARGET_APP_NAME = "projects/test-proj/locations/us/apps/cymbal-app"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )


def _run_cxas_cli(argv: list[str]) -> int:
    with mock.patch.object(sys, "argv", ["cxas", *argv]):
        try:
            ret = cli_main.main()
        except SystemExit as exc:
            if isinstance(exc.code, int):
                return exc.code
            return 0 if exc.code is None else 1
    return ret if isinstance(ret, int) else 0


@pytest.fixture
def mock_apps_client() -> Any:
    with mock.patch("cxas_scrapi.cli.app.Apps", autospec=True) as mock_apps_cls:
        inst = mock_apps_cls.return_value
        mock_app = mock.MagicMock()
        mock_app.name = TARGET_APP_NAME
        mock_app.display_name = "Cymbal Banking App"
        inst.get_app_by_display_name.return_value = mock_app

        mock_import_resp = mock.MagicMock()
        mock_import_resp.name = TARGET_APP_NAME
        mock_import_resp.warnings = []
        mock_import_lro = mock.MagicMock()
        mock_import_lro.result.return_value = mock_import_resp
        inst.import_app.return_value = mock_import_lro
        yield inst


def _create_python_callback_bundle(bundle_dir: Path) -> Path:
    """Creates a valid standard-profile LlmAgent bundle using python_lib/."""
    bundle_dir.mkdir(parents=True, exist_ok=True)

    plib_dir = bundle_dir / "python_lib"
    plib_dir.mkdir(parents=True, exist_ok=True)
    (plib_dir / "greeting_helper.py").write_text(
        "def format_greeting(brand_label: str) -> str:\n"
        '    return f"Welcome to {brand_label}"\n',
        encoding="utf-8",
    )

    cb_rel = (
        "agents/default_agent/before_agent_callbacks/cb_init/python_code.py"
    )
    cb_path = bundle_dir / cb_rel
    cb_path.parent.mkdir(parents=True, exist_ok=True)
    cb_path.write_text(
        "from python_lib.greeting_helper import format_greeting\n\n\n"
        "def before_agent_callback(callback_context):\n"
        '    return format_greeting("Cymbal Bank")\n',
        encoding="utf-8",
    )

    _write_json(
        bundle_dir / "app.json",
        {
            "displayName": "Cymbal Shared Python App",
            "globalInstruction": "global_instruction.txt",
            "guardrails": [],
            "locked": True,
            "modelSettings": {
                "model": "gemini-2.5-flash-001",
                "temperature": 1.0,
            },
            "rootAgent": "default_agent",
            "toolExecutionMode": "SEQUENTIAL",
            "variableDeclarations": [],
        },
    )
    (bundle_dir / "global_instruction.txt").write_text(
        "You are the official Cymbal Bank virtual assistant.\n",
        encoding="utf-8",
    )
    _write_json(
        bundle_dir / "agents" / "default_agent" / "default_agent.json",
        {
            "beforeAgentCallbacks": [
                {
                    "description": "Initialize Cymbal greeting state",
                    "pythonCode": cb_rel,
                }
            ],
            "childAgents": ["cards_agent"],
            "description": "Entry routing agent",
            "displayName": "default_agent",
            "instruction": "agents/default_agent/instruction.txt",
            "modelSettings": {"model": "gemini-2.5-flash-001"},
            "tools": [],
        },
    )
    (bundle_dir / "agents" / "default_agent" / "instruction.txt").write_text(
        "Route credit card questions to {@AGENT: cards_agent}.\n",
        encoding="utf-8",
    )
    _write_json(
        bundle_dir / "agents" / "cards_agent" / "cards_agent.json",
        {
            "description": "Credit card servicing specialist",
            "displayName": "cards_agent",
            "instruction": "agents/cards_agent/instruction.txt",
            "modelSettings": {"model": "gemini-2.5-flash-001"},
            "tools": [],
        },
    )
    (bundle_dir / "agents" / "cards_agent" / "instruction.txt").write_text(
        "Handle Cymbal Bank credit card inquiries.\n",
        encoding="utf-8",
    )
    return bundle_dir


def _stamp_volatile_ces_metadata(live_dir: Path) -> None:
    for idx, json_file in enumerate(sorted(live_dir.rglob("*.json")), start=1):
        rel = json_file.relative_to(live_dir)
        data = _read_json(json_file)
        if rel == Path("app.json"):
            data.update(
                {
                    "createTime": "2026-11-18T10:00:00Z",
                    "etag": 'W/"live-app-etag"',
                    "name": TARGET_APP_NAME,
                    "uid": "uid-cymbal-app-0001",
                    "updateTime": "2026-11-18T12:00:00Z",
                }
            )
        elif len(rel.parts) == 3 and rel.parts[0] in {
            "agents",
            "tools",
            "guardrails",
        }:
            data.update(
                {
                    "createTime": "2026-11-18T10:00:00Z",
                    "etag": f'W/"res-etag-{idx}"',
                    "name": f"{TARGET_APP_NAME}/{rel.parts[0]}/{rel.parts[1]}",
                    "uid": f"uid-res-{idx:04d}",
                    "updateTime": "2026-11-18T12:00:00Z",
                }
            )
        _write_json(json_file, data)


def test_unified_brownfield_core_dir_cli_pipeline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verifies brownfield --core-dir build with pruning, policy, overlay, locks, zip, and firewall."""
    core_dir = tmp_path / "core"
    manifests_dir = tmp_path / "manifests"
    core_dir.mkdir(parents=True)

    _write_json(
        core_dir / "app.json",
        {
            "displayName": "@@brand.display_name@@ Assistant",
            "globalInstruction": "global_instruction.txt",
            "guardrails": [],
            "locked": True,
            "modelSettings": {
                "model": "gemini-2.5-flash-001",
                "temperature": 1.0,
            },
            "rootAgent": "default_agent",
            "toolExecutionMode": "SEQUENTIAL",
            "variableDeclarations": [
                {
                    "description": "Session routing context",
                    "name": "session_ctx",
                    "schema": {
                        "properties": {
                            "disabled_agents": {
                                "properties": {
                                    "branch_agent": {"type": "BOOLEAN"},
                                    "cards_agent": {"type": "BOOLEAN"},
                                },
                                "type": "OBJECT",
                            },
                            "segment": {"type": "STRING"},
                            "surface_id": {"type": "STRING"},
                        },
                        "required": [],
                        "type": "OBJECT",
                    },
                }
            ],
        },
    )
    (core_dir / "global_instruction.txt").write_text(
        "You represent @@brand.display_name@@.\n", encoding="utf-8"
    )

    _write_json(
        core_dir / "agents" / "default_agent" / "default_agent.json",
        {
            "childAgents": ["cards_agent", "branch_agent"],
            "description": "Entry router for @@brand.display_name@@",
            "displayName": "default_agent",
            "instruction": "agents/default_agent/instruction.txt",
            "modelSettings": {"model": "gemini-2.5-flash-001"},
            "tools": [],
        },
    )
    (core_dir / "agents" / "default_agent" / "instruction.txt").write_text(
        "Route card requests to {@AGENT: cards_agent}.\n"
        "- Route in-person branch appointments to {@AGENT: branch_agent}.\n",
        encoding="utf-8",
    )

    for spec_name, spec_desc in (
        ("cards_agent", "Credit card specialist"),
        ("branch_agent", "Branch appointment specialist"),
    ):
        _write_json(
            core_dir / "agents" / spec_name / f"{spec_name}.json",
            {
                "description": spec_desc,
                "displayName": spec_name,
                "instruction": f"agents/{spec_name}/instruction.txt",
                "modelSettings": {"model": "gemini-2.5-flash-001"},
                "tools": [],
            },
        )
        (core_dir / "agents" / spec_name / "instruction.txt").write_text(
            f"Assist Cymbal customers as {spec_name}.\n", encoding="utf-8"
        )

    brand_dir = manifests_dir / "brands" / "cymbal_direct"
    brand_dir.mkdir(parents=True)
    (brand_dir / "identity.yaml").write_text(
        yaml.safe_dump(
            {
                "brand_id": "cymbal_direct",
                "display_name": "Cymbal Direct",
                "tts_voices": {
                    "en-US": {
                        "speaking_rate": 1.0,
                        "voice": "en-US-Chirp3-HD-Aoede",
                    }
                },
                "urls": {"web_host": "www.cymbal-direct.example"},
            }
        ),
        encoding="utf-8",
    )
    (brand_dir / "channel_policy.yaml").write_text(
        yaml.safe_dump(
            {
                "brand_id": "cymbal_direct",
                "channels": {
                    "WEB": {
                        "disabled_agents": ["branch_agent"],
                        "supported_modalities": ["TEXT"],
                    }
                },
                "supported_countries": ["US"],
            }
        ),
        encoding="utf-8",
    )
    envs_dir = manifests_dir / "envs"
    envs_dir.mkdir(parents=True)
    (envs_dir / "prod.yaml").write_text(
        yaml.safe_dump(
            {
                "environment": "prod",
                "templates": {"gcp_project": "cymbal-prod"},
            }
        ),
        encoding="utf-8",
    )

    policy_path = tmp_path / "runtime_policy.yaml"
    policy_path.write_text(
        yaml.safe_dump(
            {
                "kill_switch": {
                    "enabled": True,
                    "expression_template": (
                        "session_ctx.disabled_agents.{agent} == true"
                    ),
                },
                "schema_version": "1.0",
                "segments": {
                    "employee": {"blocked_agents": ["cards_agent"]},
                },
                "surfaces": {
                    "public-web": {
                        "auth_state": "unauthenticated",
                        "blocked_agents": ["cards_agent"],
                        "entry_agent": "default_agent",
                        "modality": "digital",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    overlay_path = tmp_path / "tuning_overlay.yaml"
    overlay_path.write_text(
        yaml.safe_dump(
            {
                "overlays": {
                    "digital": {
                        "root_agent": {
                            "append_rules": [
                                "Provide concise digital guidance for {brand_name} via {web_host}."
                            ]
                        }
                    }
                },
                "schema_version": "1.0",
            }
        ),
        encoding="utf-8",
    )

    dist_dir = tmp_path / "dist"
    capsys.readouterr()
    rc = _run_cxas_cli(
        [
            "build",
            "--core-dir",
            str(core_dir),
            "--manifests-dir",
            str(manifests_dir),
            "--env",
            "prod",
            "--all",
            "--out",
            str(dist_dir),
            "--runtime-policy",
            str(policy_path),
            "--tuning-overlay",
            str(overlay_path),
            "--prune-dangling-lines",
            "--no-deployments",
            "--write-locks",
            "--zip",
            "--firewall",
            "--firewall-profile",
            "standard",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    reports = json.loads(captured.out)
    assert len(reports) == 1
    assert reports[0]["warnings"] == []
    assert reports[0]["runtime_rules_count"] == 6

    out_bundle = dist_dir / "cymbal_direct" / "web"
    assert not (out_bundle / "deployments").exists()
    assert not (out_bundle / "agents" / "branch_agent").exists()

    default_instr = (
        out_bundle / "agents" / "default_agent" / "instruction.txt"
    ).read_text(encoding="utf-8")
    assert "branch_agent" not in default_instr
    assert (
        "Provide concise digital guidance for Cymbal Direct via www.cymbal-direct.example."
        in default_instr
    )

    default_json = _read_json(
        out_bundle / "agents" / "default_agent" / "default_agent.json"
    )
    rule_exprs = {
        (
            r["childAgent"],
            r["disablePlannerTransfer"]["expressionCondition"]["expression"],
        )
        for r in default_json["transferRules"]
    }
    assert (
        "cards_agent",
        "session_ctx.surface_id == 'public-web'",
    ) in rule_exprs
    assert ("cards_agent", "surface_id == 'public-web'") in rule_exprs
    assert ("cards_agent", "session_ctx.segment == 'employee'") in rule_exprs
    assert ("cards_agent", "segment == 'employee'") in rule_exprs
    assert (
        "cards_agent",
        "session_ctx.disabled_agents.cards_agent == true",
    ) in rule_exprs
    assert ("cards_agent", "disabled_agent_cards_agent == true") in rule_exprs

    app_json = _read_json(out_bundle / "app.json")
    var_names = {d["name"] for d in app_json["variableDeclarations"]}
    assert {
        "session_ctx",
        "surface_id",
        "segment",
        "disabled_agent_cards_agent",
    } <= var_names
    assert "disabled_agent_branch_agent" not in var_names

    lock_file = dist_dir / "cymbal_direct" / "web.lock.json"
    zip_file = out_bundle / "cymbal_direct_web.zip"
    assert lock_file.is_file()
    assert zip_file.is_file()
    with zipfile.ZipFile(zip_file, "r") as zf:
        assert "app.json" in zf.namelist()
        assert not any("deployments" in n for n in zf.namelist())

    assert run_firewall(out_bundle, profile="standard") == []


def test_shared_python_bundler_zero_false_positive_drift_and_push_gate(
    tmp_path: Path, mock_apps_client: Any
) -> None:
    """Verifies #552 shared Python bundle markers normalize cleanly with zero false drift."""
    bundle_dir = _create_python_callback_bundle(
        tmp_path / "dist" / "cymbal_app"
    )
    lock_path = write_lockfile(bundle_dir)
    assert lock_path.is_file()

    out_zip = tmp_path / "packaged" / "cymbal_app.zip"
    package_app_archive(bundle_dir, out_zip, wrapper_dir="cymbal_app")
    assert out_zip.is_file()

    cb_member = (
        "cymbal_app/agents/default_agent/before_agent_callbacks/"
        "cb_init/python_code.py"
    )
    extract_root = tmp_path / "live_extracted"
    with zipfile.ZipFile(out_zip, "r") as zf:
        bundled_py = zf.read(cb_member).decode("utf-8")
        assert "# === BEGIN CXAS_SHARED_BUNDLE (sha256:" in bundled_py
        assert "# === END CXAS_SHARED_BUNDLE ===" in bundled_py
        zf.extractall(extract_root)

    live_dir = extract_root / "cymbal_app"
    _stamp_volatile_ces_metadata(live_dir)

    drift_report = check_drift(
        "cymbal_app",
        bundle_dir.parent,
        live_dir,
        lockfile_path=lock_path,
        firewall_profile="standard",
    )
    assert drift_report.is_clean is True
    assert drift_report.has_unresolved is False

    rc_push = _run_cxas_cli(
        [
            "push",
            "--app-dir",
            str(bundle_dir),
            "--to",
            "cymbal_app",
            "--project-id",
            "test-proj",
            "--location",
            "us",
            "--firewall",
            "--firewall-profile",
            "standard",
            "--check-drift",
            "--live-dir",
            str(live_dir),
        ]
    )
    assert rc_push == 0
    mock_apps_client.import_app.assert_called_once()


def test_shared_python_callback_console_drift_accept_live_conflict_and_tamper_rejection(
    tmp_path: Path,
    mock_apps_client: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verifies Console callback drift, accept-live unbundling, 3-way conflict, and tamper block."""
    cb_rel = Path(
        "agents/default_agent/before_agent_callbacks/cb_init/python_code.py"
    )

    # (a) Valid Console edit outside # === BEGIN CXAS_SHARED_BUNDLE ===
    bundle_a = _create_python_callback_bundle(
        tmp_path / "case_a" / "dist" / "cymbal_app"
    )
    lock_a = write_lockfile(bundle_a)
    zip_a = package_app_archive(
        bundle_a,
        tmp_path / "case_a" / "cymbal_app.zip",
        wrapper_dir="cymbal_app",
    )
    live_root_a = tmp_path / "case_a" / "live"
    with zipfile.ZipFile(zip_a, "r") as zf:
        zf.extractall(live_root_a)
    live_a = live_root_a / "cymbal_app"
    _stamp_volatile_ces_metadata(live_a)

    live_py_a = live_a / cb_rel
    assert "# === BEGIN CXAS_SHARED_BUNDLE" in live_py_a.read_text(
        encoding="utf-8"
    )
    live_py_a.write_text(
        live_py_a.read_text(encoding="utf-8")
        + "\n# Console hotfix outside shared bundle\n",
        encoding="utf-8",
    )

    drift_a = check_drift(
        "cymbal_app",
        bundle_a.parent,
        live_a,
        lockfile_path=lock_a,
        firewall_profile="standard",
    )
    assert drift_a.by_id["agents/default_agent"].status == "DRIFT"

    accepted_a = check_drift(
        "cymbal_app",
        bundle_a.parent,
        live_a,
        lockfile_path=lock_a,
        accept_live=["agents/default_agent"],
        firewall_profile="standard",
    )
    assert accepted_a.has_unresolved is False
    adopted_py = (bundle_a / cb_rel).read_text(encoding="utf-8")
    assert "# Console hotfix outside shared bundle" in adopted_py
    assert "CXAS_SHARED_BUNDLE" not in adopted_py

    # (b) Concurrent Console edit + local Git edit -> line-granular CONFLICT
    bundle_b = _create_python_callback_bundle(
        tmp_path / "case_b" / "dist" / "cymbal_app"
    )
    lock_b = write_lockfile(bundle_b)
    zip_b = package_app_archive(
        bundle_b,
        tmp_path / "case_b" / "cymbal_app.zip",
        wrapper_dir="cymbal_app",
    )
    live_root_b = tmp_path / "case_b" / "live"
    with zipfile.ZipFile(zip_b, "r") as zf:
        zf.extractall(live_root_b)
    live_b = live_root_b / "cymbal_app"
    _stamp_volatile_ces_metadata(live_b)

    (live_b / cb_rel).write_text(
        (live_b / cb_rel).read_text(encoding="utf-8")
        + "\n# Live Console greeting tweak\n",
        encoding="utf-8",
    )
    (bundle_b / cb_rel).write_text(
        (bundle_b / cb_rel).read_text(encoding="utf-8")
        + "\n# Local blueprint greeting tweak\n",
        encoding="utf-8",
    )

    conflicts_b = tmp_path / "case_b" / "conflicts"
    conflict_rep = check_drift(
        "cymbal_app",
        bundle_b.parent,
        live_b,
        lockfile_path=lock_b,
        conflicts_dir=conflicts_b,
        firewall_profile="standard",
    )
    assert conflict_rep.by_id["agents/default_agent"].status == "CONFLICT"
    diff_text = (conflicts_b / "agents" / "default_agent.diff").read_text(
        encoding="utf-8"
    )
    assert "<<<<<<< live" in diff_text
    assert "=======" in diff_text
    assert ">>>>>>> blueprint" in diff_text
    assert "# Live Console greeting tweak" in diff_text
    assert "# Local blueprint greeting tweak" in diff_text
    assert "CXAS_SHARED_BUNDLE" not in diff_text

    # (c) Tampering inside # === BEGIN CXAS_SHARED_BUNDLE === in live_dir
    bundle_c = _create_python_callback_bundle(
        tmp_path / "case_c" / "dist" / "cymbal_app"
    )
    lock_c = write_lockfile(bundle_c)
    zip_c = package_app_archive(
        bundle_c,
        tmp_path / "case_c" / "cymbal_app.zip",
        wrapper_dir="cymbal_app",
    )
    live_root_c = tmp_path / "case_c" / "live"
    with zipfile.ZipFile(zip_c, "r") as zf:
        zf.extractall(live_root_c)
    live_c = live_root_c / "cymbal_app"
    _stamp_volatile_ces_metadata(live_c)

    tampered_py = (
        (live_c / cb_rel)
        .read_text(encoding="utf-8")
        .replace("Welcome to ", "Tampered inside shared bundle ")
    )
    (live_c / cb_rel).write_text(tampered_py, encoding="utf-8")

    capsys.readouterr()
    rc_tamper = _run_cxas_cli(
        [
            "push",
            "--app-dir",
            str(bundle_c),
            "--to",
            "cymbal_app",
            "--project-id",
            "test-proj",
            "--location",
            "us",
            "--firewall",
            "--firewall-profile",
            "standard",
            "--check-drift",
            "--live-dir",
            str(live_c),
            "--lockfile",
            str(lock_c),
        ]
    )
    assert rc_tamper == 1
    tamper_err = capsys.readouterr().err
    assert "Pre-push gate failed:" in tamper_err
    assert "Block tampered in Console" in tamper_err
    assert "Traceback" not in tamper_err
    mock_apps_client.import_app.assert_not_called()


def test_firewall_strict_vs_standard_profile_guards(tmp_path: Path) -> None:
    """Verifies strict vs standard profile rules including 64 KiB and syntax checks."""
    py_bundle = _create_python_callback_bundle(tmp_path / "py_bundle")
    (py_bundle / "deployments").mkdir(parents=True)
    (py_bundle / "deployments" / "web.yaml").write_text(
        "channel: WEB\n", encoding="utf-8"
    )

    strict_ids = {v.rule_id for v in run_firewall(py_bundle, profile="strict")}
    assert "FW01" in strict_ids
    assert "FW09" in strict_ids

    assert run_firewall(py_bundle, profile="standard") == []
    assert (
        _run_cxas_cli(
            [
                "firewall",
                "--bundle",
                str(py_bundle),
                "--profile",
                "standard",
            ]
        )
        == 0
    )

    # > 65,536 bytes (64 KiB CES callback limit) -> FW01 in standard profile
    oversized_bundle = tmp_path / "py_oversized"
    shutil.copytree(py_bundle, oversized_bundle)
    cb_path = (
        oversized_bundle
        / "agents"
        / "default_agent"
        / "before_agent_callbacks"
        / "cb_init"
        / "python_code.py"
    )
    cb_path.write_text('PAYLOAD = "' + ("x" * 66_000) + '"\n', encoding="utf-8")
    assert len(cb_path.read_bytes()) > 65_536
    oversized_ids = {
        v.rule_id for v in run_firewall(oversized_bundle, profile="standard")
    }
    assert "FW01" in oversized_ids

    # Invalid Python syntax -> FW01 in standard profile
    syntax_bundle = tmp_path / "py_syntax_err"
    shutil.copytree(py_bundle, syntax_bundle)
    bad_cb_path = (
        syntax_bundle
        / "agents"
        / "default_agent"
        / "before_agent_callbacks"
        / "cb_init"
        / "python_code.py"
    )
    bad_cb_path.write_text("def broken(:\n    pass\n", encoding="utf-8")
    syntax_ids = {
        v.rule_id for v in run_firewall(syntax_bundle, profile="standard")
    }
    assert "FW01" in syntax_ids


def test_package_bundle_zip_deterministic_via_package_app_archive(
    tmp_path: Path,
) -> None:
    """Verifies two calls to package_bundle_zip emit byte-identical SHA-256 archives."""
    py_bundle = _create_python_callback_bundle(tmp_path / "cymbal_app")

    zip_1 = package_bundle_zip(py_bundle, tmp_path / "out_1" / "cymbal_app.zip")
    zip_2 = package_bundle_zip(py_bundle, tmp_path / "out_2" / "cymbal_app.zip")

    sha_1 = hashlib.sha256(zip_1.read_bytes()).hexdigest()
    sha_2 = hashlib.sha256(zip_2.read_bytes()).hexdigest()
    assert sha_1 == sha_2

    with zipfile.ZipFile(zip_1, "r") as zf:
        names = zf.namelist()
        assert "cymbal_app/app.json" in names
        bundled_cb = zf.read(
            "cymbal_app/agents/default_agent/before_agent_callbacks/"
            "cb_init/python_code.py"
        ).decode("utf-8")
        assert "# === BEGIN CXAS_SHARED_BUNDLE (sha256:" in bundled_cb


def test_u1_two_level_core_dir_all_lockfile_discovery_and_git_forward_drift(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verifies U1 2-level <brand>/<channel> lockfile discovery, Git-forward CLEAN, and cxas lock."""
    core_dir = tmp_path / "core"
    manifests_dir = tmp_path / "manifests"
    core_dir.mkdir(parents=True)

    _write_json(
        core_dir / "app.json",
        {
            "displayName": "@@brand.display_name@@ Assistant",
            "globalInstruction": "global_instruction.txt",
            "guardrails": [],
            "locked": True,
            "modelSettings": {
                "model": "gemini-2.5-flash-001",
                "temperature": 1.0,
            },
            "rootAgent": "default_agent",
            "toolExecutionMode": "SEQUENTIAL",
            "variableDeclarations": [],
        },
    )
    (core_dir / "global_instruction.txt").write_text(
        "You represent @@brand.display_name@@.\n", encoding="utf-8"
    )
    _write_json(
        core_dir / "agents" / "default_agent" / "default_agent.json",
        {
            "childAgents": ["cards_agent"],
            "description": "Entry router",
            "displayName": "default_agent",
            "instruction": "agents/default_agent/instruction.txt",
            "modelSettings": {"model": "gemini-2.5-flash-001"},
            "tools": [],
        },
    )
    (core_dir / "agents" / "default_agent" / "instruction.txt").write_text(
        "Route card requests to {@AGENT: cards_agent}.\n", encoding="utf-8"
    )
    _write_json(
        core_dir / "agents" / "cards_agent" / "cards_agent.json",
        {
            "description": "Credit card specialist",
            "displayName": "cards_agent",
            "instruction": "agents/cards_agent/instruction.txt",
            "modelSettings": {"model": "gemini-2.5-flash-001"},
            "tools": [],
        },
    )
    (core_dir / "agents" / "cards_agent" / "instruction.txt").write_text(
        "Cymbal v1 card guidance.\n", encoding="utf-8"
    )

    brand_dir = manifests_dir / "brands" / "cymbal_direct"
    brand_dir.mkdir(parents=True)
    (brand_dir / "identity.yaml").write_text(
        yaml.safe_dump(
            {
                "brand_id": "cymbal_direct",
                "display_name": "Cymbal Direct",
                "tts_voices": {
                    "en-US": {
                        "speaking_rate": 1.0,
                        "voice": "en-US-Chirp3-HD-Aoede",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (brand_dir / "channel_policy.yaml").write_text(
        yaml.safe_dump(
            {
                "brand_id": "cymbal_direct",
                "channels": {
                    "WEB": {
                        "disabled_agents": [],
                        "supported_modalities": ["TEXT"],
                    }
                },
                "supported_countries": ["US"],
            }
        ),
        encoding="utf-8",
    )
    envs_dir = manifests_dir / "envs"
    envs_dir.mkdir(parents=True)
    (envs_dir / "prod.yaml").write_text(
        yaml.safe_dump(
            {
                "environment": "prod",
                "templates": {"gcp_project": "cymbal-prod"},
            }
        ),
        encoding="utf-8",
    )

    dist_dir = tmp_path / "dist"
    rc_v1 = _run_cxas_cli(
        [
            "build",
            "--core-dir",
            str(core_dir),
            "--manifests-dir",
            str(manifests_dir),
            "--env",
            "prod",
            "--all",
            "--out",
            str(dist_dir),
            "--write-locks",
        ]
    )
    assert rc_v1 == 0
    bundle_dir = dist_dir / "cymbal_direct" / "web"
    lock_file = dist_dir / "cymbal_direct" / "web.lock.json"
    assert bundle_dir.is_dir()
    assert lock_file.is_file()

    # Snapshot v1 into live_v1 and stamp volatile metadata
    live_v1 = tmp_path / "live_v1"
    shutil.copytree(bundle_dir, live_v1)
    _stamp_volatile_ces_metadata(live_v1)

    # Advance core/ to v2 and rebuild ours WITHOUT --write-locks (base == live == v1, ours == v2)
    (core_dir / "agents" / "cards_agent" / "instruction.txt").write_text(
        "Cymbal v2 Git-forward card guidance.\n", encoding="utf-8"
    )
    rc_v2 = _run_cxas_cli(
        [
            "build",
            "--core-dir",
            str(core_dir),
            "--manifests-dir",
            str(manifests_dir),
            "--env",
            "prod",
            "--all",
            "--out",
            str(dist_dir),
        ]
    )
    assert rc_v2 == 0

    for app_selector in ("cymbal_direct/web", "cymbal_direct-web"):
        capsys.readouterr()
        rc_drift = _run_cxas_cli(
            [
                "drift",
                "--app",
                app_selector,
                "--dist",
                str(dist_dir),
                "--live",
                str(live_v1),
                "--json",
            ]
        )
        drift_out = capsys.readouterr()
        assert rc_drift == 0, drift_out.err
        payload = json.loads(drift_out.out)
        assert payload["passed"] is True
        res_by_id = {r["resource_id"]: r for r in payload["resources"]}
        assert res_by_id["agents/cards_agent"]["status"] == "CLEAN"
        assert res_by_id["agents/cards_agent"]["base_sha256"] is not None
        assert (
            res_by_id["agents/cards_agent"]["ours_sha256"]
            != res_by_id["agents/cards_agent"]["live_sha256"]
        )

    # Delete nested lockfile and run `cxas lock --dist <dist>`
    lock_file.unlink()
    assert not lock_file.exists()
    rc_lock = _run_cxas_cli(["lock", "--dist", str(dist_dir)])
    assert rc_lock == 0
    assert lock_file.is_file()
    lock_json = _read_json(lock_file)
    assert lock_json["app_id"] == "cymbal_direct/web"


def test_u2_cli_drift_profile_standard_accepts_live_on_python_bundle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verifies U2 `cxas drift --profile standard --accept-live` on Python + deployments bundle."""
    bundle_dir = _create_python_callback_bundle(
        tmp_path / "dist" / "cymbal_app"
    )
    (bundle_dir / "deployments").mkdir(parents=True)
    (bundle_dir / "deployments" / "web.yaml").write_text(
        "channel: WEB\n", encoding="utf-8"
    )
    lock_path = write_lockfile(bundle_dir)

    zip_path = package_app_archive(
        bundle_dir, tmp_path / "cymbal_app.zip", wrapper_dir="cymbal_app"
    )
    live_root = tmp_path / "live_root"
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(live_root)
    live_drifted = live_root / "cymbal_app"
    _stamp_volatile_ces_metadata(live_drifted)

    cb_rel = Path(
        "agents/default_agent/before_agent_callbacks/cb_init/python_code.py"
    )
    (live_drifted / cb_rel).write_text(
        (live_drifted / cb_rel).read_text(encoding="utf-8")
        + "\n# Live Console tweak under standard profile\n",
        encoding="utf-8",
    )

    # Default strict profile rejects --accept-live due to FW01 / FW09 and rolls back
    capsys.readouterr()
    rc_strict = _run_cxas_cli(
        [
            "drift",
            "--app",
            "cymbal_app",
            "--dist",
            str(bundle_dir),
            "--live",
            str(live_drifted),
            "--lockfile",
            str(lock_path),
            "--accept-live",
            "agents/default_agent",
        ]
    )
    strict_err = capsys.readouterr().err
    assert rc_strict == 1
    assert "FW01" in strict_err

    # --profile standard succeeds and updates ours + lockfile cleanly
    capsys.readouterr()
    rc_standard = _run_cxas_cli(
        [
            "drift",
            "--app",
            "cymbal_app",
            "--dist",
            str(bundle_dir),
            "--live",
            str(live_drifted),
            "--lockfile",
            str(lock_path),
            "--profile",
            "standard",
            "--accept-live",
            "agents/default_agent",
        ]
    )
    std_captured = capsys.readouterr()
    assert rc_standard == 0, std_captured.err
    assert "# Live Console tweak under standard profile" in (
        bundle_dir / cb_rel
    ).read_text(encoding="utf-8")

    # Follow-up cxas drift is CLEAN (exit 0)
    rc_followup = _run_cxas_cli(
        [
            "drift",
            "--app",
            "cymbal_app",
            "--dist",
            str(bundle_dir),
            "--live",
            str(live_drifted),
            "--lockfile",
            str(lock_path),
        ]
    )
    assert rc_followup == 0


def test_u3_shared_python_lib_out_of_band_drift_and_accept_live_backport(
    tmp_path: Path,
    mock_apps_client: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verifies U3 out-of-band python_lib drift detection, --accept-live module back-port, and v3 Git-forward CLEAN."""
    ours_dir = _create_python_callback_bundle(
        tmp_path / "ours_dist" / "cymbal_app"
    )
    lock_path = write_lockfile(ours_dir)
    helper_rel = Path("python_lib/greeting_helper.py")
    assert "Welcome to " in (ours_dir / helper_rel).read_text(encoding="utf-8")

    # Simulate an out-of-band hotfix to python_lib/greeting_helper.py ONLY (entrypoint wrapper lines identical)
    hotfix_src = tmp_path / "hotfix_src" / "cymbal_app"
    shutil.copytree(ours_dir, hotfix_src)
    (hotfix_src / helper_rel).write_text(
        "def format_greeting(brand_label: str) -> str:\n"
        '    return f"HOTFIX_WELCOME to {brand_label}"\n',
        encoding="utf-8",
    )
    hotfix_zip = package_app_archive(
        hotfix_src, tmp_path / "hotfix_v2.zip", wrapper_dir="cymbal_app"
    )
    live_v2_root = tmp_path / "live_v2_root"
    with zipfile.ZipFile(hotfix_zip, "r") as zf:
        zf.extractall(live_v2_root)
    live_v2 = live_v2_root / "cymbal_app"
    _stamp_volatile_ces_metadata(live_v2)

    # 1. check_drift and cxas push --check-drift catch DRIFT on agents/default_agent (no silent clobber)
    drift_rep = check_drift(
        "cymbal_app",
        ours_dir.parent,
        live_v2,
        lockfile_path=lock_path,
        firewall_profile="standard",
    )
    assert drift_rep.by_id["agents/default_agent"].status == "DRIFT"

    capsys.readouterr()
    rc_push = _run_cxas_cli(
        [
            "push",
            "--app-dir",
            str(ours_dir),
            "--to",
            "cymbal_app",
            "--project-id",
            "test-proj",
            "--location",
            "us",
            "--firewall",
            "--firewall-profile",
            "standard",
            "--check-drift",
            "--live-dir",
            str(live_v2),
            "--lockfile",
            str(lock_path),
        ]
    )
    assert rc_push == 1
    mock_apps_client.import_app.assert_not_called()

    # 2. cxas drift --profile standard --accept-live back-ports python_lib/greeting_helper.py and updates lockfile
    capsys.readouterr()
    rc_accept = _run_cxas_cli(
        [
            "drift",
            "--app",
            "cymbal_app",
            "--dist",
            str(ours_dir),
            "--live",
            str(live_v2),
            "--lockfile",
            str(lock_path),
            "--profile",
            "standard",
            "--accept-live",
            "agents/default_agent",
        ]
    )
    accept_out = capsys.readouterr()
    assert rc_accept == 0, accept_out.err
    assert "HOTFIX_WELCOME" in (ours_dir / helper_rel).read_text(
        encoding="utf-8"
    )

    post_accept = check_drift(
        "cymbal_app",
        ours_dir.parent,
        live_v2,
        lockfile_path=lock_path,
        firewall_profile="standard",
    )
    assert post_accept.is_clean is True

    # 3. Commit/lock v2 baseline and make a local Git-forward v3 edit on python_lib/greeting_helper.py
    write_lockfile(ours_dir, lock_path)
    (ours_dir / helper_rel).write_text(
        "def format_greeting(brand_label: str) -> str:\n"
        '    return f"GIT_V3_WELCOME to {brand_label}"\n',
        encoding="utf-8",
    )
    v3_forward = check_drift(
        "cymbal_app",
        ours_dir.parent,
        live_v2,
        lockfile_path=lock_path,
        firewall_profile="standard",
    )
    assert v3_forward.is_clean is True
    assert v3_forward.by_id["agents/default_agent"].status == "CLEAN"
