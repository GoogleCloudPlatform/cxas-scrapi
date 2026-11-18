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

"""Adversarial E2E, UTF-8 boundary, CRLF merge, and deep regression QA suite."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
import yaml

from cxas_scrapi.build import (
    BlueprintCompilerError,
    DriftGateError,
    FirewallError,
    check_drift,
    compile_blueprint,
    enforce_pre_push_gate,
    run_firewall,
)
from cxas_scrapi.cli import main as cli_main

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
BLUEPRINT_DIR = FIXTURES_DIR / "cymbal_blueprint"
SRC_ROOT = Path(__file__).resolve().parents[3] / "src"

EXPECTED_APPS = (
    "cymbal_retail-voice",
    "cymbal_retail-digital",
    "cymbal_direct-voice",
    "cymbal_direct-digital",
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )


def _collect_bytes_tree(root: Path) -> dict[Path, bytes]:
    return {
        p.relative_to(root): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _run_cxas_cli(argv: list[str]) -> int:
    with mock.patch.object(sys, "argv", ["cxas", *argv]):
        try:
            ret = cli_main.main()
        except SystemExit as exc:
            if isinstance(exc.code, int):
                return exc.code
            return 0 if exc.code is None else 1
    return ret if isinstance(ret, int) else 0


def _run_cxas_subprocess(
    *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    existing_py = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{SRC_ROOT}{os.pathsep}{existing_py}" if existing_py else str(SRC_ROOT)
    )
    return subprocess.run(
        [sys.executable, "-P", "-m", "cxas_scrapi.cli.main", *args],
        cwd=str(cwd if cwd is not None else SRC_ROOT.parent),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


# ===========================================================================
# Part 1: E2E User Journeys and Boundary Defenses
# ===========================================================================


def test_qa_e2e_quickstart_full_pipeline_and_zip_isolation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verifies documented CLI workflow and zip D-1 brand isolation."""
    dist_dir = tmp_path / "dist"
    live_dir = tmp_path / "live_export" / "cymbal_retail-voice"
    conflicts_dir = tmp_path / "conflicts"

    capsys.readouterr()
    rc_build_zip = _run_cxas_cli(
        [
            "build",
            "--blueprint",
            str(BLUEPRINT_DIR),
            "--out",
            str(dist_dir),
            "--zip",
        ]
    )
    assert rc_build_zip == 0

    for app_id in EXPECTED_APPS:
        zip_file = dist_dir / f"{app_id}.zip"
        assert zip_file.is_file()
        with zipfile.ZipFile(zip_file, "r") as zf:
            names = zf.namelist()
            assert f"{app_id}/app.json" in names
            assert not any("deployments" in n.lower() for n in names)
            for member in names:
                raw_txt = zf.read(member).decode("utf-8")
                if app_id.startswith("cymbal_direct-"):
                    assert "cymbal retail bank" not in raw_txt.lower()
                    assert "branch_information_agent" not in raw_txt
                    assert "find_branches" not in raw_txt
                else:
                    assert "cymbal direct" not in raw_txt.lower()

    capsys.readouterr()
    rc_fw = _run_cxas_cli(
        [
            "firewall",
            "--bundle",
            str(dist_dir / "cymbal_retail-voice"),
            "--blueprint",
            str(BLUEPRINT_DIR),
            "--json",
        ]
    )
    assert rc_fw == 0
    fw_payload = json.loads(capsys.readouterr().out)
    assert fw_payload["passed"] is True
    assert fw_payload["total_violations"] == 0

    capsys.readouterr()
    rc_lock = _run_cxas_cli(["lock", "--dist", str(dist_dir)])
    assert rc_lock == 0
    for app_id in EXPECTED_APPS:
        assert (dist_dir / f"{app_id}.lock.json").is_file()

    shutil.copytree(dist_dir / "cymbal_retail-voice", live_dir)
    capsys.readouterr()
    rc_drift = _run_cxas_cli(
        [
            "drift",
            "--app",
            "cymbal_retail-voice",
            "--dist",
            str(dist_dir),
            "--live",
            str(live_dir),
            "--lockfile",
            str(dist_dir / "cymbal_retail-voice.lock.json"),
            "--conflicts-dir",
            str(conflicts_dir),
            "--json",
        ]
    )
    assert rc_drift == 0
    drift_payload = json.loads(capsys.readouterr().out)
    assert drift_payload["is_clean"] is True
    assert drift_payload["has_unresolved"] is False

    gate_report = enforce_pre_push_gate(
        dist_dir / "cymbal_retail-voice",
        app_id="cymbal_retail-voice",
        live_dir=live_dir,
        lockfile_path=dist_dir / "cymbal_retail-voice.lock.json",
        blueprint_dir=BLUEPRINT_DIR,
        conflicts_dir=conflicts_dir,
    )
    assert gate_report is not None
    assert gate_report.is_clean is True


def test_qa_e2e_utf8_multibyte_exact_boundaries(tmp_path: Path) -> None:
    """Verifies FW04 measures UTF-8 bytes rather than str length at caps."""
    dist_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR, dist_dir, brand="cymbal_retail", modality="digital"
    )
    bundle = dist_dir / "cymbal_retail-digital"

    global_bytes = len((bundle / "global_instruction.txt").read_bytes())
    root_path = bundle / "agents" / "root_agent" / "instruction.txt"
    prefix = root_path.read_text(encoding="utf-8")
    remaining = 15_360 - global_bytes - len(prefix.encode("utf-8"))
    assert remaining > 10
    exact_root = prefix + ("€" * (remaining // 3)) + ("a" * (remaining % 3))
    root_path.write_text(exact_root, encoding="utf-8")

    assert len(root_path.read_bytes()) + global_bytes == 15_360
    assert len(exact_root) < 10_000
    assert run_firewall(bundle, blueprint_dir=BLUEPRINT_DIR) == []

    root_path.write_text(exact_root + "a", encoding="utf-8")
    assert len(root_path.read_bytes()) + global_bytes == 15_361
    violations_root = run_firewall(bundle, blueprint_dir=BLUEPRINT_DIR)
    assert [v.rule_id for v in violations_root] == ["FW04"]

    root_path.write_text(prefix, encoding="utf-8")
    spec_path = bundle / "agents" / "credit_cards_agent" / "instruction.txt"
    spec_prefix = spec_path.read_text(encoding="utf-8")
    spec_rem = 18_432 - len(spec_prefix.encode("utf-8"))
    exact_spec = spec_prefix + ("€" * (spec_rem // 3)) + ("b" * (spec_rem % 3))
    spec_path.write_text(exact_spec, encoding="utf-8")

    assert len(spec_path.read_bytes()) == 18_432
    assert len(exact_spec) < 10_000
    assert run_firewall(bundle, blueprint_dir=BLUEPRINT_DIR) == []

    spec_path.write_text(exact_spec + "b", encoding="utf-8")
    assert len(spec_path.read_bytes()) == 18_433
    violations_spec = run_firewall(bundle, blueprint_dir=BLUEPRINT_DIR)
    assert [v.rule_id for v in violations_spec] == ["FW04"]


def test_qa_e2e_windows_crlf_and_multibyte_three_way_merge(
    tmp_path: Path,
) -> None:
    """Verifies CRLF normalization and simultaneous accept-live plus force-ours."""
    dist_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR,
        dist_dir,
        brand="cymbal_retail",
        modality="digital",
        write_locks=True,
    )
    bundle = dist_dir / "cymbal_retail-digital"

    live_crlf = tmp_path / "live_crlf"
    shutil.copytree(bundle, live_crlf)
    for fp in live_crlf.rglob("*"):
        if fp.is_file():
            txt = fp.read_text(encoding="utf-8")
            fp.write_bytes(txt.replace("\n", "\r\n").encode("utf-8"))

    crlf_report = check_drift("cymbal_retail-digital", dist_dir, live_crlf)
    assert crlf_report.is_clean is True

    live_mixed = tmp_path / "live_mixed"
    shutil.copytree(bundle, live_mixed)
    hia_live = (
        live_mixed / "agents" / "human_interaction_agent" / "instruction.txt"
    )
    hia_live.write_text(
        hia_live.read_text(encoding="utf-8")
        + "\n- Confirm toll-free callback eligibility (äöüß€).\n",
        encoding="utf-8",
    )
    wire_live = (
        live_mixed / "agents" / "wire_transfer_agent" / "instruction.txt"
    )
    wire_live.write_text(
        wire_live.read_text(encoding="utf-8")
        + "\n- Verify instant wire threshold up to 100,000 € (äöüß).\n",
        encoding="utf-8",
    )
    wire_ours = bundle / "agents" / "wire_transfer_agent" / "instruction.txt"
    wire_ours.write_text(
        wire_ours.read_text(encoding="utf-8")
        + "\n- Confirm beneficiary name-match verification (äöüß€).\n",
        encoding="utf-8",
    )

    conflicts_dir = tmp_path / "conflicts"
    mixed_report = check_drift(
        "cymbal_retail-digital",
        dist_dir,
        live_mixed,
        conflicts_dir=conflicts_dir,
        accept_live=["agents/human_interaction_agent"],
        force_ours=["agents/wire_transfer_agent"],
        blueprint_dir=BLUEPRINT_DIR,
    )
    assert mixed_report.has_unresolved is False
    assert (
        mixed_report.by_id["agents/human_interaction_agent"].resolved_by
        == "accept-live"
    )
    assert (
        mixed_report.by_id["agents/wire_transfer_agent"].resolved_by
        == "force-ours"
    )

    diff_text = (
        conflicts_dir / "agents" / "wire_transfer_agent.diff"
    ).read_text(encoding="utf-8")
    assert "\r" not in diff_text
    assert "100,000 €" in diff_text
    assert "beneficiary name-match verification" in diff_text


def test_qa_e2e_tuning_overlay_pruned_agent_and_jinja_rejection(
    tmp_path: Path,
) -> None:
    """Verifies tuning_overlay prunes branch_information_agent on cymbal_direct and blocks Jinja."""
    bp_copy = tmp_path / "bp"
    shutil.copytree(BLUEPRINT_DIR, bp_copy)
    to_path = bp_copy / "tuning_overlay.yaml"
    to_data = yaml.safe_load(to_path.read_text(encoding="utf-8"))
    to_data["overlays"]["digital"]["branch_information_agent"] = {
        "append_rules": [
            "Always highlight step-free accessibility details for {brand_name} on {web_host}."
        ]
    }
    to_path.write_text(
        yaml.safe_dump(to_data, allow_unicode=True), encoding="utf-8"
    )

    dist_dir = tmp_path / "dist"
    compile_blueprint(bp_copy, dist_dir)

    retail_branch_txt = (
        dist_dir
        / "cymbal_retail-digital"
        / "agents"
        / "branch_information_agent"
        / "instruction.txt"
    ).read_text(encoding="utf-8")
    assert (
        "step-free accessibility details for Cymbal Retail Bank on www.cymbal-retail.example"
        in retail_branch_txt
    )
    assert not (
        dist_dir
        / "cymbal_direct-digital"
        / "agents"
        / "branch_information_agent"
    ).exists()

    to_data["overlays"]["digital"]["guide_agent"] = {
        "append_rules": ["{% if segment == 'employee' %}Internal{% endif %}"]
    }
    to_path.write_text(
        yaml.safe_dump(to_data, allow_unicode=True), encoding="utf-8"
    )
    with pytest.raises(FirewallError) as exc_info:
        compile_blueprint(bp_copy, tmp_path / "dist_jinja")
    assert "FW09" in {v.rule_id for v in exc_info.value.violations}


def test_qa_e2e_multi_resource_accept_live_atomic_rollback_and_idempotency(
    tmp_path: Path,
) -> None:
    """Verifies atomic rollback across multi-resource accept-live and idempotency."""
    dist_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR,
        dist_dir,
        brand="cymbal_retail",
        modality="digital",
        write_locks=True,
    )
    bundle = dist_dir / "cymbal_retail-digital"
    lock_path = dist_dir / "cymbal_retail-digital.lock.json"
    orig_tree = _collect_bytes_tree(bundle)
    orig_lock = lock_path.read_bytes()

    live_bad = tmp_path / "live_bad"
    shutil.copytree(bundle, live_bad)
    (
        live_bad / "agents" / "authentication_agent" / "instruction.txt"
    ).write_text(
        (
            live_bad / "agents" / "authentication_agent" / "instruction.txt"
        ).read_text(encoding="utf-8")
        + "\n- Valid supplementary rule for Cymbal Approve.\n",
        encoding="utf-8",
    )
    (live_bad / "agents" / "guide_agent" / "instruction.txt").write_text(
        (live_bad / "agents" / "guide_agent" / "instruction.txt").read_text(
            encoding="utf-8"
        )
        + "\n- Refer inquiries to CYMBAL DIRECT.\n",
        encoding="utf-8",
    )

    with pytest.raises(FirewallError) as exc_info:
        check_drift(
            "cymbal_retail-digital",
            dist_dir,
            live_bad,
            accept_live=[
                "agents/authentication_agent",
                "agents/guide_agent",
            ],
            blueprint_dir=BLUEPRINT_DIR,
        )
    assert "FW05" in {v.rule_id for v in exc_info.value.violations}
    assert _collect_bytes_tree(bundle) == orig_tree
    assert lock_path.read_bytes() == orig_lock

    r1 = check_drift(
        "cymbal_retail-digital",
        dist_dir,
        live_bad,
        accept_live=["agents/authentication_agent"],
        force_ours=["agents/guide_agent"],
        blueprint_dir=BLUEPRINT_DIR,
    )
    assert r1.has_unresolved is False
    tree_after_first = _collect_bytes_tree(bundle)
    lock_after_first = lock_path.read_bytes()

    r2 = check_drift(
        "cymbal_retail-digital",
        dist_dir,
        live_bad,
        accept_live=["agents/authentication_agent"],
        force_ours=["agents/guide_agent"],
        blueprint_dir=BLUEPRINT_DIR,
    )
    assert r2.has_unresolved is False
    assert r2.by_id["agents/authentication_agent"].status == "CLEAN"
    assert _collect_bytes_tree(bundle) == tree_after_first
    assert lock_path.read_bytes() == lock_after_first


def test_qa_e2e_case_insensitive_brand_leak_detection(tmp_path: Path) -> None:
    """Verifies mixed-case sister-brand tokens trigger FW05 across file types."""
    dist_dir = tmp_path / "dist"
    compile_blueprint(BLUEPRINT_DIR, dist_dir)

    direct_bundle = dist_dir / "cymbal_direct-digital"
    oa_yaml = (
        direct_bundle
        / "tools"
        / "list_credit_cards"
        / "open_api_tool"
        / "open_api_schema.yaml"
    )
    oa_yaml.write_text(
        oa_yaml.read_text(encoding="utf-8")
        + "\n# Legacy Cymbal Retail BANK endpoint\n",
        encoding="utf-8",
    )
    direct_violations = run_firewall(direct_bundle, blueprint_dir=BLUEPRINT_DIR)
    assert "FW05" in {v.rule_id for v in direct_violations}


# ===========================================================================
# Part 2: Defect-Reproduction Regression Tests (QA-001 through QA-010)
# ===========================================================================


def test_qa_001_firewall_blocks_disabled_brand_and_modality_tools(
    tmp_path: Path,
) -> None:
    """Verifies disabled_tools and disabled_agents are rejected by firewall and accept-live."""
    dist_dir = tmp_path / "dist"
    compile_blueprint(BLUEPRINT_DIR, dist_dir, write_locks=True)

    live_direct = tmp_path / "live_direct"
    shutil.copytree(dist_dir / "cymbal_direct-digital", live_direct)
    shutil.copytree(
        dist_dir
        / "cymbal_retail-digital"
        / "tools"
        / "book_branch_appointment",
        live_direct / "tools" / "book_branch_appointment",
    )
    for fp in (live_direct / "tools" / "book_branch_appointment").rglob("*"):
        if fp.is_file():
            fp.write_text(
                fp.read_text(encoding="utf-8")
                .replace("Cymbal Retail Bank", "Cymbal Direct")
                .replace("cymbal-retail", "cymbal-direct"),
                encoding="utf-8",
            )

    with pytest.raises(FirewallError):
        check_drift(
            "cymbal_direct-digital",
            dist_dir,
            live_direct,
            accept_live=["tools/book_branch_appointment"],
            blueprint_dir=BLUEPRINT_DIR,
        )

    shutil.copytree(
        dist_dir / "cymbal_retail-digital" / "tools" / "display_deeplink",
        dist_dir / "cymbal_retail-voice" / "tools" / "display_deeplink",
    )
    voice_violations = run_firewall(
        dist_dir / "cymbal_retail-voice", blueprint_dir=BLUEPRINT_DIR
    )
    assert voice_violations, (
        "Expected firewall violation when modality-disabled tool "
        "'display_deeplink' is present in cymbal_retail-voice."
    )


def test_qa_002_subsequent_build_after_accept_live_does_not_silently_wipe_hotfix(
    tmp_path: Path,
) -> None:
    """Verifies Console hotfix accepted via accept-live is not silently wiped on next build."""
    bp_copy = tmp_path / "blueprint"
    dist_dir = tmp_path / "dist"
    live_dir = tmp_path / "live_retail_digital"
    shutil.copytree(BLUEPRINT_DIR, bp_copy)

    compile_blueprint(
        bp_copy,
        dist_dir,
        brand="cymbal_retail",
        modality="digital",
        write_locks=True,
    )
    shutil.copytree(dist_dir / "cymbal_retail-digital", live_dir)

    hotfix_marker = "HOTFIX-CARD-FREEZE-DESK-800"
    hia_live = (
        live_dir / "agents" / "human_interaction_agent" / "instruction.txt"
    )
    hia_live.write_text(
        hia_live.read_text(encoding="utf-8") + f"\n- {hotfix_marker}\n",
        encoding="utf-8",
    )
    check_drift(
        "cymbal_retail-digital",
        dist_dir,
        live_dir,
        accept_live=["agents/human_interaction_agent"],
        blueprint_dir=bp_copy,
    )

    to_path = bp_copy / "tuning_overlay.yaml"
    to_data = yaml.safe_load(to_path.read_text(encoding="utf-8"))
    to_data["overlays"]["digital"]["credit_cards_agent"]["append_rules"].append(
        "Quote standard replacement card delivery window of 5 business days."
    )
    to_path.write_text(
        yaml.safe_dump(to_data, allow_unicode=True), encoding="utf-8"
    )
    compile_blueprint(
        bp_copy,
        dist_dir,
        brand="cymbal_retail",
        modality="digital",
        write_locks=False,
    )

    hia_compiled = (
        dist_dir
        / "cymbal_retail-digital"
        / "agents"
        / "human_interaction_agent"
        / "instruction.txt"
    ).read_text(encoding="utf-8")
    report = check_drift(
        "cymbal_retail-digital", dist_dir, live_dir, blueprint_dir=bp_copy
    )

    hotfix_preserved = hotfix_marker in hia_compiled
    regression_blocked = (
        report.by_id["agents/human_interaction_agent"].status != "CLEAN"
    )
    assert hotfix_preserved or regression_blocked, (
        "Accepted Console hotfix was wiped from dist/ by build, yet drift "
        "reported CLEAN, allowing push to overwrite the live hotfix."
    )


def test_qa_003_firewall_blocks_snake_case_callbacks_guardrail_code_callback_and_alt_json_names(
    tmp_path: Path,
) -> None:
    """Verifies FW01 and FW06 catch snake_case callbacks, codeCallback, and alternate json filenames."""
    dist_a = tmp_path / "dist_a"
    compile_blueprint(
        BLUEPRINT_DIR, dist_a, brand="cymbal_retail", modality="digital"
    )
    bundle_a = dist_a / "cymbal_retail-digital"
    cc_path = (
        bundle_a / "agents" / "credit_cards_agent" / "credit_cards_agent.json"
    )
    cc_json = _read_json(cc_path)
    cc_json["before_model_callbacks"] = [
        {
            "description": "Snake-case Python callback",
            "python_code": "def cb(ctx, req):\n    return None\n",
        }
    ]
    _write_json(cc_path, cc_json)
    violations_a = run_firewall(bundle_a, blueprint_dir=BLUEPRINT_DIR)
    assert "FW01" in {v.rule_id for v in violations_a}

    dist_b = tmp_path / "dist_b"
    compile_blueprint(
        BLUEPRINT_DIR, dist_b, brand="cymbal_retail", modality="digital"
    )
    bundle_b = dist_b / "cymbal_retail-digital"
    _write_json(
        bundle_b / "guardrails" / "py_guard" / "py_guard.json",
        {
            "displayName": "py_guard",
            "enabled": True,
            "codeCallback": {
                "beforeModelCallback": {
                    "description": "Python guardrail callback",
                    "pythonCode": "def cb(ctx):\n    return None\n",
                }
            },
        },
    )
    violations_b = run_firewall(bundle_b, blueprint_dir=BLUEPRINT_DIR)
    assert "FW01" in {v.rule_id for v in violations_b}

    dist_c = tmp_path / "dist_c"
    compile_blueprint(
        BLUEPRINT_DIR,
        dist_c,
        brand="cymbal_retail",
        modality="digital",
        write_locks=True,
    )
    bundle_c = dist_c / "cymbal_retail-digital"
    _write_json(
        bundle_c / "tools" / "sneaky_py" / "tool.json",
        {
            "displayName": "sneaky_py",
            "pythonFunction": {
                "name": "sneaky_py",
                "pythonCode": "def sneaky_py(a): return a",
            },
        },
    )
    _write_json(
        bundle_c / "guardrails" / "sneaky_re" / "guardrail.json",
        {
            "displayName": "sneaky_re",
            "enabled": True,
            "contentFilter": {
                "bannedContents": ["(?=secret)123"],
                "matchType": "REGEXP_MATCH",
            },
        },
    )
    violations_c = run_firewall(bundle_c, blueprint_dir=BLUEPRINT_DIR)
    rule_ids_c = {v.rule_id for v in violations_c}
    assert "FW01" in rule_ids_c
    assert "FW06" in rule_ids_c


def test_qa_004_firewall_catches_dangling_refs_orphan_agents_disabled_guardrails_and_missing_root_instr(
    tmp_path: Path,
) -> None:
    """Verifies intra-bundle referential integrity, enabled guardrails, and missing instruction files."""
    dist_a = tmp_path / "dist_a"
    compile_blueprint(
        BLUEPRINT_DIR, dist_a, brand="cymbal_retail", modality="digital"
    )
    bundle_a = dist_a / "cymbal_retail-digital"
    shutil.rmtree(bundle_a / "guardrails" / "prompt_security_guard")
    assert run_firewall(bundle_a, blueprint_dir=BLUEPRINT_DIR), (
        "Expected violation for dangling guardrail in app.json['guardrails']"
    )

    dist_b = tmp_path / "dist_b"
    compile_blueprint(
        BLUEPRINT_DIR, dist_b, brand="cymbal_retail", modality="digital"
    )
    bundle_b = dist_b / "cymbal_retail-digital"
    shutil.rmtree(bundle_b / "tools" / "list_credit_cards")
    assert run_firewall(bundle_b, blueprint_dir=BLUEPRINT_DIR), (
        "Expected violation for dangling tool in credit_cards_agent.json['tools']"
    )

    dist_c = tmp_path / "dist_c"
    compile_blueprint(
        BLUEPRINT_DIR, dist_c, brand="cymbal_retail", modality="digital"
    )
    bundle_c = dist_c / "cymbal_retail-digital"
    (bundle_c / "agents" / "root_agent" / "instruction.txt").unlink()
    (bundle_c / "global_instruction.txt").write_text(
        "G" * 20_000 + "\n", encoding="utf-8"
    )
    violations_c = run_firewall(bundle_c, blueprint_dir=BLUEPRINT_DIR)
    assert "FW04" in {v.rule_id for v in violations_c}

    dist_d = tmp_path / "dist_d"
    compile_blueprint(
        BLUEPRINT_DIR, dist_d, brand="cymbal_retail", modality="digital"
    )
    bundle_d = dist_d / "cymbal_retail-digital"
    gr_path = (
        bundle_d
        / "guardrails"
        / "prompt_security_guard"
        / "prompt_security_guard.json"
    )
    gr_json = _read_json(gr_path)
    gr_json["enabled"] = False
    _write_json(gr_path, gr_json)
    assert run_firewall(bundle_d, blueprint_dir=BLUEPRINT_DIR), (
        "Expected violation when attached guardrail sets enabled=False"
    )


def test_qa_005_shared_git_mirror_isolates_per_app_branch_histories(
    tmp_path: Path,
) -> None:
    """Verifies interleaved git-mirror runs do not cross-contaminate sister-brand branches."""
    dist_dir = tmp_path / "dist"
    compile_blueprint(BLUEPRINT_DIR, dist_dir, write_locks=True)
    mirror_repo = tmp_path / "shared_mirror"

    live_retail = tmp_path / "live_retail"
    live_direct = tmp_path / "live_direct"
    shutil.copytree(dist_dir / "cymbal_retail-digital", live_retail)
    shutil.copytree(dist_dir / "cymbal_direct-voice", live_direct)

    check_drift(
        "cymbal_retail-digital", dist_dir, live_retail, git_mirror=mirror_repo
    )
    check_drift(
        "cymbal_direct-voice", dist_dir, live_direct, git_mirror=mirror_repo
    )
    check_drift(
        "cymbal_retail-digital", dist_dir, live_retail, git_mirror=mirror_repo
    )

    retail_log = subprocess.run(
        [
            "git",
            "-C",
            str(mirror_repo),
            "log",
            "--oneline",
            "live/cymbal_retail-digital",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    direct_log = subprocess.run(
        [
            "git",
            "-C",
            str(mirror_repo),
            "log",
            "--oneline",
            "live/cymbal_direct-voice",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert "live/cymbal_direct-voice" not in retail_log, (
        f"Sister-brand branch contaminated live/cymbal_retail-digital history:\n{retail_log}"
    )
    assert "live/cymbal_retail-digital" not in direct_log, (
        f"Sister-brand branch contaminated live/cymbal_direct-voice history:\n{direct_log}"
    )


def test_qa_006_fw06_catches_escaped_char_class_possessive_and_python_only_regex_and_allows_valid_char_class(
    tmp_path: Path,
) -> None:
    """Verifies FW06 catches escaped char-class possessives and Python-only escapes while allowing [(?=]."""
    dist_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR, dist_dir, brand="cymbal_retail", modality="digital"
    )
    bundle = dist_dir / "cymbal_retail-digital"
    cf_path = (
        bundle
        / "guardrails"
        / "pii_account_content_filter"
        / "pii_account_content_filter.json"
    )

    for invalid_re2 in (
        r"[\s]a*+",
        r"[\\]secret?+",
        r"(?#comment)acct",
        r"(?a)\w+",
        r"US[0-9]{20}\Z",
    ):
        cf_json = _read_json(cf_path)
        cf_json["contentFilter"]["bannedContents"] = [invalid_re2]
        _write_json(cf_path, cf_json)
        violations = run_firewall(bundle, blueprint_dir=BLUEPRINT_DIR)
        assert "FW06" in {v.rule_id for v in violations}, (
            f"FW06 missed non-RE2 pattern: {invalid_re2!r}"
        )

    cf_json = _read_json(cf_path)
    cf_json["contentFilter"]["bannedContents"] = [r"[(?=]"]
    _write_json(cf_path, cf_json)
    valid_violations = run_firewall(bundle, blueprint_dir=BLUEPRINT_DIR)
    assert "FW06" not in {v.rule_id for v in valid_violations}


def test_qa_007_drift_rejects_missing_explicit_lockfile_typo_selectors_and_ambiguous_leaf_selectors(
    tmp_path: Path,
) -> None:
    """Verifies explicit missing lockfile, typo selectors, and ambiguous leaf selectors raise errors."""
    dist_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR,
        dist_dir,
        brand="cymbal_retail",
        modality="digital",
        write_locks=True,
    )
    bundle = dist_dir / "cymbal_retail-digital"

    live_deleted = tmp_path / "live_deleted"
    shutil.copytree(bundle, live_deleted)
    shutil.rmtree(live_deleted / "agents" / "guide_agent")
    with pytest.raises(BlueprintCompilerError):
        check_drift(
            "cymbal_retail-digital",
            dist_dir,
            live_deleted,
            lockfile_path=dist_dir / "cymbal_retail-digtal-TYPO.lock.json",
        )

    live_clean = tmp_path / "live_clean"
    shutil.copytree(bundle, live_clean)
    with pytest.raises(BlueprintCompilerError):
        check_drift(
            "cymbal_retail-digital",
            dist_dir,
            live_clean,
            accept_live=["agents/nonexistent_typo_specialist"],
        )

    live_ambig = tmp_path / "live_ambig"
    shutil.copytree(bundle, live_ambig)
    _write_json(
        live_ambig / "agents" / "shared_leaf" / "shared_leaf.json",
        {
            "displayName": "shared_leaf",
            "modelSettings": {"model": "gemini-2.5-flash-001"},
        },
    )
    _write_json(
        live_ambig / "tools" / "shared_leaf" / "shared_leaf.json",
        {"displayName": "shared_leaf", "executionType": "SYNCHRONOUS"},
    )
    with pytest.raises(BlueprintCompilerError):
        check_drift(
            "cymbal_retail-digital",
            dist_dir,
            live_ambig,
            force_ours=["shared_leaf"],
        )


def test_qa_008_corrupt_or_non_mapping_json_returns_clean_json_error_without_traceback(
    tmp_path: Path,
) -> None:
    """Verifies corrupt or non-dict JSON in live emits clean JSON error without traceback."""
    dist_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR,
        dist_dir,
        brand="cymbal_retail",
        modality="digital",
        write_locks=True,
    )
    bundle = dist_dir / "cymbal_retail-digital"

    for label, content in (("corrupt", "{bad_json"), ("array_root", "[]")):
        live_bad = tmp_path / f"live_{label}"
        shutil.copytree(bundle, live_bad)
        (live_bad / "agents" / "guide_agent" / "guide_agent.json").write_text(
            content, encoding="utf-8"
        )
        proc = _run_cxas_subprocess(
            "drift",
            "--app",
            "cymbal_retail-digital",
            "--dist",
            str(dist_dir),
            "--live",
            str(live_bad),
            "--json",
        )
        assert proc.returncode == 1
        assert "Traceback" not in proc.stderr, (
            f"Unhandled traceback for {label}:\n{proc.stderr}"
        )
        payload = json.loads(proc.stdout)
        assert payload.get("passed") is False
        assert payload.get("error")


def test_qa_009_tuning_overlay_rejects_unknown_targets_and_fw05_catches_dotted_or_hyphenated_single_braces(
    tmp_path: Path,
) -> None:
    """Verifies unknown tuning_overlay keys and single-brace dotted or hyphenated tokens fail."""
    bp_typo = tmp_path / "bp_typo"
    shutil.copytree(BLUEPRINT_DIR, bp_typo)
    (bp_typo / "tuning_overlay.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "overlays": {
                    "telephony": {
                        "root_agent": {"append_rules": ["Wrong modality key"]}
                    },
                    "digital": {
                        "credit_card_agent": {
                            "append_rules": ["Typo specialist key"]
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(BlueprintCompilerError):
        compile_blueprint(bp_typo, tmp_path / "out_typo")

    bp_brace = tmp_path / "bp_brace"
    shutil.copytree(BLUEPRINT_DIR, bp_brace)
    (bp_brace / "tuning_overlay.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "overlays": {
                    "digital": {
                        "guide_agent": {
                            "append_rules": [
                                "Greet customers of {brand-name} on {session_ctx.surface_id}."
                            ]
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(FirewallError) as exc_info:
        compile_blueprint(
            bp_brace,
            tmp_path / "out_brace",
            brand="cymbal_retail",
            modality="digital",
        )
    assert "FW05" in {v.rule_id for v in exc_info.value.violations}


def test_qa_010_rebuild_without_zip_removes_stale_zip_and_multiline_bullet_pruning_leaves_no_orphan_fragment(
    tmp_path: Path,
) -> None:
    """Verifies rebuild without zip removes stale zip and multi-line bullets prune cleanly."""
    bp_copy = tmp_path / "bp"
    dist_dir = tmp_path / "dist"
    shutil.copytree(BLUEPRINT_DIR, bp_copy)

    compile_blueprint(
        bp_copy,
        dist_dir,
        brand="cymbal_retail",
        modality="digital",
        package_zips=True,
    )
    stale_zip = dist_dir / "cymbal_retail-digital.zip"
    assert stale_zip.is_file()
    old_sha = hashlib.sha256(stale_zip.read_bytes()).hexdigest()

    bp_yaml = yaml.safe_load(
        (bp_copy / "blueprint.yaml").read_text(encoding="utf-8")
    )
    bp_yaml["global_instruction"] += "\n- Updated security policy 2026-11-18.\n"
    bp_yaml["specialists"]["guide_agent"]["instruction"] += (
        "\n- On digital channels link self-service journeys via {@TOOL: display_deeplink}\n"
        "  directly into the interactive card management view for customers.\n"
    )
    (bp_copy / "blueprint.yaml").write_text(
        yaml.safe_dump(bp_yaml, allow_unicode=True), encoding="utf-8"
    )

    compile_blueprint(bp_copy, dist_dir, package_zips=False)

    if stale_zip.is_file():
        assert hashlib.sha256(stale_zip.read_bytes()).hexdigest() != old_sha, (
            "Stale cymbal_retail-digital.zip from previous build stayed untouched in dist/!"
        )

    voice_guide = (
        dist_dir
        / "cymbal_retail-voice"
        / "agents"
        / "guide_agent"
        / "instruction.txt"
    ).read_text(encoding="utf-8")
    assert (
        "directly into the interactive card management view" not in voice_guide
    )


# ===========================================================================
# Part 3: Deep Re-Test Regression Suite
# ===========================================================================


def test_qa_r1_ported_console_hotfix_returns_to_clean_and_force_ours_overrides(
    tmp_path: Path,
) -> None:
    """Verifies ported hotfix returns to CLEAN and force-ours overrides DRIFT."""
    bp_copy = tmp_path / "bp"
    dist_dir = tmp_path / "dist"
    live_dir = tmp_path / "live"
    shutil.copytree(BLUEPRINT_DIR, bp_copy)

    compile_blueprint(
        bp_copy,
        dist_dir,
        brand="cymbal_retail",
        modality="digital",
        write_locks=True,
    )
    bundle_dir = dist_dir / "cymbal_retail-digital"
    lock_path = dist_dir / "cymbal_retail-digital.lock.json"
    shutil.copytree(bundle_dir, live_dir)

    hotfix_line = (
        "- HOTFIX-R1: Share card emergency desk +1-800-555-0191 immediately."
    )
    hia_live = (
        live_dir / "agents" / "human_interaction_agent" / "instruction.txt"
    )
    hia_live.write_text(
        hia_live.read_text(encoding="utf-8") + "\n" + hotfix_line + "\n",
        encoding="utf-8",
    )
    check_drift(
        "cymbal_retail-digital",
        dist_dir,
        live_dir,
        lockfile_path=lock_path,
        accept_live=["agents/human_interaction_agent"],
        blueprint_dir=bp_copy,
    )

    compile_blueprint(
        bp_copy,
        dist_dir,
        brand="cymbal_retail",
        modality="digital",
        write_locks=False,
    )
    unported = check_drift(
        "cymbal_retail-digital",
        dist_dir,
        live_dir,
        lockfile_path=lock_path,
        blueprint_dir=bp_copy,
    )
    assert unported.by_id["agents/human_interaction_agent"].status == "DRIFT"
    with pytest.raises(DriftGateError):
        enforce_pre_push_gate(
            bundle_dir,
            app_id="cymbal_retail-digital",
            live_dir=live_dir,
            lockfile_path=lock_path,
            blueprint_dir=bp_copy,
        )

    forced_report = enforce_pre_push_gate(
        bundle_dir,
        app_id="cymbal_retail-digital",
        live_dir=live_dir,
        lockfile_path=lock_path,
        blueprint_dir=bp_copy,
        force_ours=["agents/human_interaction_agent"],
    )
    assert forced_report is not None
    assert forced_report.has_unresolved is False

    bp_yaml = yaml.safe_load(
        (bp_copy / "blueprint.yaml").read_text(encoding="utf-8")
    )
    bp_yaml["specialists"]["human_interaction_agent"]["instruction"] = (
        bp_yaml["specialists"]["human_interaction_agent"]["instruction"].rstrip(
            "\n"
        )
        + "\n\n"
        + hotfix_line
        + "\n"
    )
    bp_yaml["specialists"]["credit_cards_agent"]["instruction"] += (
        "\n- Updated card servicing rule R1.\n"
    )
    (bp_copy / "blueprint.yaml").write_text(
        yaml.safe_dump(bp_yaml, allow_unicode=True), encoding="utf-8"
    )

    compile_blueprint(
        bp_copy,
        dist_dir,
        brand="cymbal_retail",
        modality="digital",
        write_locks=False,
    )
    ported = check_drift(
        "cymbal_retail-digital",
        dist_dir,
        live_dir,
        lockfile_path=lock_path,
        blueprint_dir=bp_copy,
    )
    assert ported.is_clean is True
    clean_report = enforce_pre_push_gate(
        bundle_dir,
        app_id="cymbal_retail-digital",
        live_dir=live_dir,
        lockfile_path=lock_path,
        blueprint_dir=bp_copy,
    )
    assert clean_report is not None
    assert clean_report.is_clean is True


def test_qa_r1_re2_nested_escaped_char_classes_and_valid_named_groups(
    tmp_path: Path,
) -> None:
    """Verifies nested escaped bracket classes and valid RE2 named groups."""
    dist_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR, dist_dir, brand="cymbal_retail", modality="digital"
    )
    bundle = dist_dir / "cymbal_retail-digital"
    cf_path = (
        bundle
        / "guardrails"
        / "pii_account_content_filter"
        / "pii_account_content_filter.json"
    )

    for bad_pat in (
        r"[\]a]b++",
        r"[^^]b{1,3}+",
        r"foo\u0041",
        r"(?i-a:foo)",
    ):
        cf_json = _read_json(cf_path)
        cf_json["contentFilter"]["bannedContents"] = [bad_pat]
        _write_json(cf_path, cf_json)
        violations = run_firewall(bundle, blueprint_dir=BLUEPRINT_DIR)
        assert "FW06" in {v.rule_id for v in violations}

    for valid_pat in (
        r"(?P<acct>US[0-9]{20})",
        r"(?i)cymbal_[0-9]+?",
        r"[(?=)(?!)(?>)\\1]",
    ):
        cf_json = _read_json(cf_path)
        cf_json["contentFilter"]["bannedContents"] = [valid_pat]
        _write_json(cf_path, cf_json)
        violations = run_firewall(bundle, blueprint_dir=BLUEPRINT_DIR)
        assert "FW06" not in {v.rule_id for v in violations}


def test_qa_r1_disjoint_git_mirror_roots_multihop_placeholders_and_fw10_tags(
    tmp_path: Path,
) -> None:
    """Verifies disjoint git roots, 3-hop placeholders, and FW10 tags."""
    bp_hop = tmp_path / "bp_hop"
    shutil.copytree(BLUEPRINT_DIR, bp_hop)
    for brand_fname in ("cymbal_retail.yaml", "cymbal_direct.yaml"):
        b_yaml = yaml.safe_load(
            (bp_hop / "brands" / brand_fname).read_text(encoding="utf-8")
        )
        b_yaml["placeholders"]["hop1"] = "Service: {hop2}"
        b_yaml["placeholders"]["hop2"] = "Portal {hop3}"
        b_yaml["placeholders"]["hop3"] = "{brand_name} ({web_host})"
        (bp_hop / "brands" / brand_fname).write_text(
            yaml.safe_dump(b_yaml, allow_unicode=True), encoding="utf-8"
        )
    bp_main = yaml.safe_load(
        (bp_hop / "blueprint.yaml").read_text(encoding="utf-8")
    )
    bp_main["specialists"]["guide_agent"]["instruction"] += "\n- {hop1}\n"
    (bp_hop / "blueprint.yaml").write_text(
        yaml.safe_dump(bp_main, allow_unicode=True), encoding="utf-8"
    )

    dist_dir = tmp_path / "dist"
    compile_blueprint(bp_hop, dist_dir, write_locks=True)
    guide_txt = (
        dist_dir
        / "cymbal_retail-digital"
        / "agents"
        / "guide_agent"
        / "instruction.txt"
    ).read_text(encoding="utf-8")
    assert (
        "Service: Portal Cymbal Retail Bank (www.cymbal-retail.example)"
        in guide_txt
    )

    mirror = tmp_path / "mirror"
    live_retail = tmp_path / "live_retail"
    live_direct = tmp_path / "live_direct"
    shutil.copytree(dist_dir / "cymbal_retail-digital", live_retail)
    shutil.copytree(dist_dir / "cymbal_direct-voice", live_direct)

    check_drift(
        "cymbal_retail-digital", dist_dir, live_retail, git_mirror=mirror
    )
    check_drift("cymbal_direct-voice", dist_dir, live_direct, git_mirror=mirror)
    _write_json(
        live_retail / "tools" / "temp_t" / "temp_t.json",
        {"displayName": "temp_t", "executionType": "SYNCHRONOUS"},
    )
    c_add = check_drift(
        "cymbal_retail-digital", dist_dir, live_retail, git_mirror=mirror
    ).git_mirror_commit
    shutil.rmtree(live_retail / "tools" / "temp_t")
    c_del = check_drift(
        "cymbal_retail-digital", dist_dir, live_retail, git_mirror=mirror
    ).git_mirror_commit

    root_retail = subprocess.run(
        [
            "git",
            "-C",
            str(mirror),
            "rev-list",
            "--max-parents=0",
            "live/cymbal_retail-digital",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    root_direct = subprocess.run(
        [
            "git",
            "-C",
            str(mirror),
            "rev-list",
            "--max-parents=0",
            "live/cymbal_direct-voice",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert root_retail != root_direct
    assert (
        "A\ttools/temp_t.json"
        in subprocess.run(
            [
                "git",
                "-C",
                str(mirror),
                "diff-tree",
                "--no-commit-id",
                "--name-status",
                "-r",
                str(c_add),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert (
        "D\ttools/temp_t.json"
        in subprocess.run(
            [
                "git",
                "-C",
                str(mirror),
                "diff-tree",
                "--no-commit-id",
                "--name-status",
                "-r",
                str(c_del),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )

    bundle_tag = tmp_path / "bundle_tag"
    shutil.copytree(dist_dir / "cymbal_retail-digital", bundle_tag)
    (
        bundle_tag
        / "tools"
        / "verify_tpin"
        / "open_api_tool"
        / "open_api_schema.yaml"
    ).unlink()
    (bundle_tag / "agents" / "root_agent" / "instruction.txt").write_text(
        (bundle_tag / "agents" / "root_agent" / "instruction.txt").read_text(
            encoding="utf-8"
        )
        + "\nRoute via {@AGENT: missing_ag} and {@TOOL: missing_tl}.\n",
        encoding="utf-8",
    )
    fw10_violations = [
        v
        for v in run_firewall(bundle_tag, blueprint_dir=bp_hop)
        if v.rule_id == "FW10"
    ]
    assert len(fw10_violations) == 3


def test_s4_subprocess_cli_isolated_from_shadowing_calendar_cwd(
    tmp_path: Path,
) -> None:
    """Verifies subprocess CLI uses -P and safe cwd so local calendar/ never shadows stdlib."""
    shadow_cwd = tmp_path / "shadow_workspace"
    (shadow_cwd / "calendar").mkdir(parents=True)
    (shadow_cwd / "calendar" / "__init__.py").write_text(
        "raise RuntimeError('stdlib calendar shadowed by cwd!')\n",
        encoding="utf-8",
    )

    dist_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR, dist_dir, brand="cymbal_retail", modality="digital"
    )
    bundle = dist_dir / "cymbal_retail-digital"

    proc_default = _run_cxas_subprocess(
        "firewall",
        "--bundle",
        str(bundle),
        "--blueprint",
        str(BLUEPRINT_DIR),
        "--json",
    )
    assert proc_default.returncode == 0, proc_default.stderr
    assert json.loads(proc_default.stdout)["passed"] is True

    proc_shadow = _run_cxas_subprocess(
        "firewall",
        "--bundle",
        str(bundle),
        "--blueprint",
        str(BLUEPRINT_DIR),
        "--json",
        cwd=shadow_cwd,
    )
    assert proc_shadow.returncode == 0, proc_shadow.stderr
    assert json.loads(proc_shadow.stdout)["passed"] is True
    assert not list(shadow_cwd.rglob("*.pyc"))
