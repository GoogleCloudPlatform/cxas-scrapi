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

"""Unit tests for the `cxas push` pre-push firewall and 3-way drift gate."""

import json
import shutil
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from cxas_scrapi.build import (
    compile_blueprint,
    package_bundle_zip,
    write_lockfile,
)
from cxas_scrapi.cli import main as cli_main

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
BLUEPRINT_DIR = FIXTURES_DIR / "cymbal_blueprint"
TARGET_APP_NAME = "projects/test-proj/locations/us/apps/cymbal-retail-digital"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _materialize_live_snapshot(
    compiled_bundle_dir: Path,
    target_live_dir: Path,
    overlay_dir: Path | None = None,
) -> Path:
    if target_live_dir.exists():
        shutil.rmtree(target_live_dir)
    shutil.copytree(compiled_bundle_dir, target_live_dir)

    volatile_root = FIXTURES_DIR / "live_volatile_overlay"
    app_volatile = _read_json(volatile_root / "app.json")
    res_template = _read_json(volatile_root / "resource_metadata_template.json")
    project_prefix = res_template["projectPrefix"]
    uuid_ns = res_template["uuidNamespace"]

    for idx, json_path in enumerate(
        sorted(target_live_dir.rglob("*.json")), start=1
    ):
        rel = json_path.relative_to(target_live_dir)
        data = _read_json(json_path)
        if rel == Path("app.json"):
            data.update(app_volatile)
        elif len(rel.parts) >= 2 and rel.parts[0] in {
            "agents",
            "tools",
            "guardrails",
        }:
            data.update(
                {
                    "name": f"{project_prefix}/{rel.parts[0]}/{rel.parts[1]}",
                    "uuid": f"{uuid_ns}{idx:04d}",
                    "etag": res_template["etag"],
                    "createTime": res_template["createTime"],
                    "updateTime": res_template["updateTime"],
                }
            )
        _write_json(json_path, data)

    if overlay_dir is not None and overlay_dir != volatile_root:
        for src in sorted(overlay_dir.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(overlay_dir)
            if rel.name == "resource_metadata_template.json":
                continue
            dst = target_live_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.suffix == ".json" and dst.exists():
                merged = _read_json(dst)
                merged.update(_read_json(src))
                _write_json(dst, merged)
            else:
                shutil.copy2(src, dst)

    return target_live_dir


def _configure_mock_apps_client(mock_apps_client: Any) -> None:
    mock_app = mock.MagicMock()
    mock_app.name = TARGET_APP_NAME
    mock_app.display_name = (
        "Cymbal Retail Bank Conversational Banking (Digital Multimodal)"
    )
    mock_apps_client.get_app_by_display_name.return_value = mock_app

    mock_import_resp = mock.MagicMock()
    mock_import_resp.name = TARGET_APP_NAME
    mock_import_resp.warnings = []
    mock_import_lro = mock.MagicMock()
    mock_import_lro.result.return_value = mock_import_resp
    mock_apps_client.import_app.return_value = mock_import_lro


@pytest.fixture
def mock_apps_client() -> Any:
    with mock.patch("cxas_scrapi.cli.app.Apps", autospec=True) as mock_apps_cls:
        inst = mock_apps_cls.return_value
        _configure_mock_apps_client(inst)
        yield inst


def _invoke_cxas_push(argv: list[str]) -> None:
    with mock.patch.object(sys, "argv", ["cxas", "push", *argv]):
        cli_main.main()


def test_push_aborts_before_import_app_on_firewall_violation(
    tmp_path: Path, mock_apps_client: Any
) -> None:
    dist_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR,
        dist_dir,
        brand="cymbal_retail",
        modality="digital",
        write_locks=True,
    )
    bundle_dir = dist_dir / "cymbal_retail-digital"
    lock_path = dist_dir / "cymbal_retail-digital.lock.json"
    live_clean = _materialize_live_snapshot(
        bundle_dir,
        tmp_path / "live_clean",
        FIXTURES_DIR / "live_volatile_overlay",
    )

    app_json_path = bundle_dir / "app.json"
    app_json = _read_json(app_json_path)
    app_json["locked"] = False
    _write_json(app_json_path, app_json)

    with pytest.raises(SystemExit) as exc_info:
        _invoke_cxas_push(
            [
                "--app-dir",
                str(bundle_dir),
                "--to",
                "cymbal_retail-digital",
                "--project-id",
                "test-proj",
                "--location",
                "us",
                "--firewall",
                "--check-drift",
                "--live-dir",
                str(live_clean),
                "--lockfile",
                str(lock_path),
                "--blueprint",
                str(BLUEPRINT_DIR),
            ]
        )
    assert exc_info.value.code == 1
    mock_apps_client.import_app.assert_not_called()


def test_push_aborts_before_import_app_on_live_drift(
    tmp_path: Path, mock_apps_client: Any
) -> None:
    dist_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR,
        dist_dir,
        brand="cymbal_retail",
        modality="digital",
        write_locks=True,
    )
    bundle_dir = dist_dir / "cymbal_retail-digital"
    lock_path = dist_dir / "cymbal_retail-digital.lock.json"
    live_drift = _materialize_live_snapshot(
        bundle_dir,
        tmp_path / "live_drift",
        FIXTURES_DIR / "live_cymbal_retail_digital_drift",
    )

    with pytest.raises(SystemExit) as exc_info:
        _invoke_cxas_push(
            [
                "--app-dir",
                str(bundle_dir),
                "--to",
                "cymbal_retail-digital",
                "--project-id",
                "test-proj",
                "--location",
                "us",
                "--firewall",
                "--check-drift",
                "--live-dir",
                str(live_drift),
                "--lockfile",
                str(lock_path),
                "--blueprint",
                str(BLUEPRINT_DIR),
            ]
        )
    assert exc_info.value.code == 1
    mock_apps_client.import_app.assert_not_called()


def test_push_aborts_before_import_app_on_live_conflict(
    tmp_path: Path, mock_apps_client: Any
) -> None:
    dist_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR,
        dist_dir,
        brand="cymbal_retail",
        modality="digital",
    )
    bundle_dir = dist_dir / "cymbal_retail-digital"
    lock_path = write_lockfile(bundle_dir)

    live_conflict = _materialize_live_snapshot(
        bundle_dir,
        tmp_path / "live_conflict",
        FIXTURES_DIR / "live_cymbal_retail_digital_conflict",
    )

    ours_instr = (
        bundle_dir / "agents" / "credit_cards_agent" / "instruction.txt"
    )
    ours_instr.write_text(
        ours_instr.read_text(encoding="utf-8")
        + "\n- Always verify billing cutoff before waiving late fees.\n",
        encoding="utf-8",
    )
    conflicts_dir = tmp_path / "conflicts"

    with pytest.raises(SystemExit) as exc_info:
        _invoke_cxas_push(
            [
                "--app-dir",
                str(bundle_dir),
                "--to",
                "cymbal_retail-digital",
                "--project-id",
                "test-proj",
                "--location",
                "us",
                "--firewall",
                "--check-drift",
                "--live-dir",
                str(live_conflict),
                "--lockfile",
                str(lock_path),
                "--blueprint",
                str(BLUEPRINT_DIR),
                "--conflicts-dir",
                str(conflicts_dir),
            ]
        )
    assert exc_info.value.code == 1
    assert (conflicts_dir / "agents" / "credit_cards_agent.diff").is_file()
    mock_apps_client.import_app.assert_not_called()


def test_push_aborts_before_import_app_on_live_orphan(
    tmp_path: Path, mock_apps_client: Any
) -> None:
    dist_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR,
        dist_dir,
        brand="cymbal_retail",
        modality="digital",
        write_locks=True,
    )
    bundle_dir = dist_dir / "cymbal_retail-digital"
    lock_path = dist_dir / "cymbal_retail-digital.lock.json"
    live_orphan = _materialize_live_snapshot(
        bundle_dir,
        tmp_path / "live_orphan",
        FIXTURES_DIR / "live_cymbal_retail_digital_orphan",
    )

    with pytest.raises(SystemExit) as exc_info:
        _invoke_cxas_push(
            [
                "--app-dir",
                str(bundle_dir),
                "--to",
                "cymbal_retail-digital",
                "--project-id",
                "test-proj",
                "--location",
                "us",
                "--firewall",
                "--check-drift",
                "--live-dir",
                str(live_orphan),
                "--lockfile",
                str(lock_path),
                "--blueprint",
                str(BLUEPRINT_DIR),
            ]
        )
    assert exc_info.value.code == 1
    mock_apps_client.import_app.assert_not_called()


def test_push_succeeds_and_calls_import_app_when_clean(
    tmp_path: Path, mock_apps_client: Any
) -> None:
    dist_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR,
        dist_dir,
        brand="cymbal_retail",
        modality="digital",
        write_locks=True,
    )
    bundle_dir = dist_dir / "cymbal_retail-digital"
    lock_path = dist_dir / "cymbal_retail-digital.lock.json"
    live_clean = _materialize_live_snapshot(
        bundle_dir,
        tmp_path / "live_clean",
        FIXTURES_DIR / "live_volatile_overlay",
    )

    _invoke_cxas_push(
        [
            "--app-dir",
            str(bundle_dir),
            "--to",
            "cymbal_retail-digital",
            "--project-id",
            "test-proj",
            "--location",
            "us",
            "--firewall",
            "--check-drift",
            "--live-dir",
            str(live_clean),
            "--lockfile",
            str(lock_path),
            "--blueprint",
            str(BLUEPRINT_DIR),
        ]
    )

    mock_apps_client.import_app.assert_called_once()
    call_kwargs = mock_apps_client.import_app.call_args.kwargs
    assert call_kwargs["app_name"] == TARGET_APP_NAME
    assert isinstance(call_kwargs["app_content"], bytes)
    assert len(call_kwargs["app_content"]) > 0


def test_push_succeeds_when_force_ours_resolves_dirty_resource(
    tmp_path: Path, mock_apps_client: Any
) -> None:
    dist_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR,
        dist_dir,
        brand="cymbal_retail",
        modality="digital",
        write_locks=True,
    )
    bundle_dir = dist_dir / "cymbal_retail-digital"
    lock_path = dist_dir / "cymbal_retail-digital.lock.json"
    live_drift = _materialize_live_snapshot(
        bundle_dir,
        tmp_path / "live_drift",
        FIXTURES_DIR / "live_cymbal_retail_digital_drift",
    )

    _invoke_cxas_push(
        [
            "--app-dir",
            str(bundle_dir),
            "--to",
            "cymbal_retail-digital",
            "--project-id",
            "test-proj",
            "--location",
            "us",
            "--firewall",
            "--check-drift",
            "--live-dir",
            str(live_drift),
            "--lockfile",
            str(lock_path),
            "--blueprint",
            str(BLUEPRINT_DIR),
            "--force-ours",
            "agents/human_interaction_agent",
        ]
    )

    mock_apps_client.import_app.assert_called_once()


def test_push_check_drift_without_live_dir_exports_live_zip_and_aborts_on_drift(
    tmp_path: Path, mock_apps_client: Any
) -> None:
    dist_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR,
        dist_dir,
        brand="cymbal_retail",
        modality="digital",
        write_locks=True,
    )
    bundle_dir = dist_dir / "cymbal_retail-digital"
    lock_path = dist_dir / "cymbal_retail-digital.lock.json"

    live_drift = _materialize_live_snapshot(
        bundle_dir,
        tmp_path / "live_drift_export" / "cymbal_retail-digital",
        FIXTURES_DIR / "live_cymbal_retail_digital_drift",
    )
    exported_zip = package_bundle_zip(
        live_drift, tmp_path / "remote_drift_export.zip"
    )
    exported_bytes = exported_zip.read_bytes()

    def _fake_export_app(
        app_name: str,
        gcs_uri: str | None = None,
        local_path: str | None = None,
        export_format: str = "JSON",
        app_version: str | None = None,
    ) -> Any:
        del app_name, gcs_uri, export_format, app_version
        if local_path:
            Path(local_path).write_bytes(exported_bytes)
        resp = mock.MagicMock()
        resp.app_content = exported_bytes
        return resp

    mock_apps_client.export_app.side_effect = _fake_export_app

    with pytest.raises(SystemExit) as exc_info:
        _invoke_cxas_push(
            [
                "--app-dir",
                str(bundle_dir),
                "--to",
                "cymbal_retail-digital",
                "--project-id",
                "test-proj",
                "--location",
                "us",
                "--firewall",
                "--check-drift",
                "--lockfile",
                str(lock_path),
                "--blueprint",
                str(BLUEPRINT_DIR),
            ]
        )
    assert exc_info.value.code == 1
    mock_apps_client.export_app.assert_called_once()
    mock_apps_client.import_app.assert_not_called()


def test_push_check_drift_without_live_dir_exports_live_zip_and_imports_when_clean(
    tmp_path: Path, mock_apps_client: Any
) -> None:
    dist_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR,
        dist_dir,
        brand="cymbal_retail",
        modality="digital",
        write_locks=True,
    )
    bundle_dir = dist_dir / "cymbal_retail-digital"
    lock_path = dist_dir / "cymbal_retail-digital.lock.json"

    live_clean = _materialize_live_snapshot(
        bundle_dir,
        tmp_path / "live_clean_export" / "cymbal_retail-digital",
        FIXTURES_DIR / "live_volatile_overlay",
    )
    exported_zip = package_bundle_zip(
        live_clean, tmp_path / "remote_clean_export.zip"
    )
    exported_bytes = exported_zip.read_bytes()

    mock_export_resp = mock.MagicMock()
    mock_export_resp.app_content = exported_bytes
    mock_apps_client.export_app.return_value = mock_export_resp

    _invoke_cxas_push(
        [
            "--app-dir",
            str(bundle_dir),
            "--to",
            "cymbal_retail-digital",
            "--project-id",
            "test-proj",
            "--location",
            "us",
            "--firewall",
            "--check-drift",
            "--lockfile",
            str(lock_path),
            "--blueprint",
            str(BLUEPRINT_DIR),
        ]
    )

    mock_apps_client.export_app.assert_called_once()
    mock_apps_client.import_app.assert_called_once()


def test_s2_push_firewall_succeeds_for_standalone_default_agent_bundle(
    tmp_path: Path, mock_apps_client: Any
) -> None:
    standalone_dir = tmp_path / "standalone_app"
    _write_json(
        standalone_dir / "app.json",
        {
            "displayName": "Standalone Banking Assistant",
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
    (standalone_dir / "global_instruction.txt").write_text(
        "You are a helpful banking virtual assistant.\n", encoding="utf-8"
    )
    _write_json(
        standalone_dir / "agents" / "default_agent" / "default_agent.json",
        {
            "childAgents": ["billing_agent"],
            "description": "Entry routing agent",
            "displayName": "default_agent",
            "instruction": "agents/default_agent/instruction.txt",
            "modelSettings": {"model": "gemini-2.5-flash-001"},
            "tools": [],
        },
    )
    (
        standalone_dir / "agents" / "default_agent" / "instruction.txt"
    ).write_text(
        "Route billing inquiries to {@AGENT: billing_agent}.\n",
        encoding="utf-8",
    )
    _write_json(
        standalone_dir / "agents" / "billing_agent" / "billing_agent.json",
        {
            "description": "Billing specialist agent",
            "displayName": "billing_agent",
            "instruction": "agents/billing_agent/instruction.txt",
            "modelSettings": {"model": "gemini-2.5-flash-001"},
            "tools": [],
        },
    )
    (
        standalone_dir / "agents" / "billing_agent" / "instruction.txt"
    ).write_text("Handle billing inquiries accurately.\n", encoding="utf-8")

    _invoke_cxas_push(
        [
            "--app-dir",
            str(standalone_dir),
            "--to",
            "standalone-app",
            "--project-id",
            "test-proj",
            "--location",
            "us",
            "--firewall",
        ]
    )
    mock_apps_client.import_app.assert_called_once()


def test_s3_push_check_drift_handles_corrupt_zip_and_export_rpc_error_cleanly(
    tmp_path: Path,
    mock_apps_client: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dist_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR,
        dist_dir,
        brand="cymbal_retail",
        modality="digital",
        write_locks=True,
    )
    bundle_dir = dist_dir / "cymbal_retail-digital"
    lock_path = dist_dir / "cymbal_retail-digital.lock.json"

    # (a) export_app returns corrupt non-ZIP bytes
    corrupt_resp = mock.MagicMock()
    corrupt_resp.app_content = b"not-a-zip"
    mock_apps_client.export_app.return_value = corrupt_resp

    capsys.readouterr()
    with pytest.raises(SystemExit) as exc_corrupt:
        _invoke_cxas_push(
            [
                "--app-dir",
                str(bundle_dir),
                "--to",
                "cymbal_retail-digital",
                "--project-id",
                "test-proj",
                "--location",
                "us",
                "--check-drift",
                "--lockfile",
                str(lock_path),
            ]
        )
    assert exc_corrupt.value.code == 1
    corrupt_err = capsys.readouterr().err
    assert "Pre-push gate failed:" in corrupt_err
    assert "Traceback" not in corrupt_err
    mock_apps_client.import_app.assert_not_called()

    # (b) export_app raises RuntimeError("RPC unavailable")
    mock_apps_client.export_app.side_effect = RuntimeError("RPC unavailable")

    capsys.readouterr()
    with pytest.raises(SystemExit) as exc_rpc:
        _invoke_cxas_push(
            [
                "--app-dir",
                str(bundle_dir),
                "--to",
                "cymbal_retail-digital",
                "--project-id",
                "test-proj",
                "--location",
                "us",
                "--check-drift",
                "--lockfile",
                str(lock_path),
            ]
        )
    assert exc_rpc.value.code == 1
    rpc_err = capsys.readouterr().err
    assert "Pre-push gate failed:" in rpc_err
    assert "RPC unavailable" in rpc_err
    assert "Traceback" not in rpc_err
    mock_apps_client.import_app.assert_not_called()


def test_s3_push_firewall_fails_fast_before_remote_lookup_or_export(
    tmp_path: Path,
    mock_apps_client: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dist_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR,
        dist_dir,
        brand="cymbal_retail",
        modality="digital",
        write_locks=True,
    )
    bundle_dir = dist_dir / "cymbal_retail-digital"
    app_json_path = bundle_dir / "app.json"
    app_json = _read_json(app_json_path)
    app_json["locked"] = False
    _write_json(app_json_path, app_json)

    capsys.readouterr()
    with pytest.raises(SystemExit) as exc_info:
        _invoke_cxas_push(
            [
                "--app-dir",
                str(bundle_dir),
                "--to",
                "cymbal_retail-digital",
                "--project-id",
                "test-proj",
                "--location",
                "us",
                "--firewall",
                "--check-drift",
                "--blueprint",
                str(BLUEPRINT_DIR),
            ]
        )
    assert exc_info.value.code == 1
    err_out = capsys.readouterr().err
    assert "Pre-push gate failed:" in err_out
    mock_apps_client.get_app_by_display_name.assert_not_called()
    mock_apps_client.export_app.assert_not_called()
    mock_apps_client.import_app.assert_not_called()


def test_s4_push_check_drift_auto_discovers_sibling_lockfile_when_omitted(
    tmp_path: Path,
    mock_apps_client: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dist_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR,
        dist_dir,
        brand="cymbal_retail",
        modality="digital",
        write_locks=True,
    )
    bundle_dir = dist_dir / "cymbal_retail-digital"
    sibling_lock = dist_dir / "cymbal_retail-digital.lock.json"
    assert sibling_lock.is_file()

    live_deleted = _materialize_live_snapshot(
        bundle_dir,
        tmp_path / "live_deleted_guide",
        FIXTURES_DIR / "live_volatile_overlay",
    )
    shutil.rmtree(live_deleted / "agents" / "guide_agent")

    capsys.readouterr()
    with pytest.raises(SystemExit) as exc_info:
        _invoke_cxas_push(
            [
                "--app-dir",
                str(bundle_dir),
                "--to",
                "cymbal_retail-digital",
                "--project-id",
                "test-proj",
                "--location",
                "us",
                "--check-drift",
                "--live-dir",
                str(live_deleted),
            ]
        )
    assert exc_info.value.code == 1
    err_out = capsys.readouterr().err
    assert "agents/guide_agent=DRIFT" in err_out
    mock_apps_client.import_app.assert_not_called()
