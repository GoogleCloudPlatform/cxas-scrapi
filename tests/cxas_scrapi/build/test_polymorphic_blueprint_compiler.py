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

"""Hermetic unit and contract test suite for the polymorphic blueprint compiler."""

import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any
from unittest import mock

import pydantic
import pytest
import yaml

from cxas_scrapi.build import (
    BlueprintCompilerError,
    CompilerError,
    DriftGateError,
    FirewallError,
    check_drift,
    compile_blueprint,
    enforce_pre_push_gate,
    extract_normalized_resources,
    package_bundle_zip,
    run_firewall,
    write_lockfile,
)
from cxas_scrapi.cli import main as cli_main

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
BLUEPRINT_DIR = FIXTURES_DIR / "cymbal_blueprint"

EXPECTED_BUNDLES = {
    "cymbal_retail-voice",
    "cymbal_retail-digital",
    "cymbal_direct-voice",
    "cymbal_direct-digital",
}

ALL_SPECIALISTS = {
    "authentication_agent",
    "credit_cards_agent",
    "wire_transfer_agent",
    "branch_information_agent",
    "human_interaction_agent",
    "guide_agent",
}

CYMBAL_DIRECT_SURVIVING_SPECIALISTS = ALL_SPECIALISTS - {
    "branch_information_agent"
}


# ---------------------------------------------------------------------------
# Hermetic Helpers
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
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


def _iter_bundle_text_files(bundle_dir: Path) -> list[Path]:
    return sorted(
        p
        for p in bundle_dir.rglob("*")
        if p.is_file() and p.suffix in {".json", ".txt", ".yaml"}
    )


def _collect_bytes_tree(root: Path) -> dict[Path, bytes]:
    return {
        p.relative_to(root): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _extract_disable_transfer_rules(
    root_agent_json: dict[str, Any],
) -> list[tuple[str, str]]:
    """Returns (specialist_short_name, expression) pairs for disablePlannerTransfer rules."""
    extracted: list[tuple[str, str]] = []
    for rule in root_agent_json.get("transferRules", []):
        if "disablePlannerTransfer" not in rule:
            continue
        dpt = rule["disablePlannerTransfer"]
        if isinstance(dpt, dict):
            expr = (
                dpt.get("expression")
                or dpt.get("condition")
                or json.dumps(dpt, sort_keys=True)
            )
        else:
            expr = rule.get("expression") or rule.get("condition") or str(dpt)
        child = str(rule.get("childAgent", "")).split("/")[-1]
        extracted.append((child, str(expr)))
    return extracted


def _extract_exact_transfer_rule_pairs(
    root_agent_json: dict[str, Any],
) -> list[tuple[str, str]]:
    """Returns (specialist_short_name, raw_expression) pairs for disablePlannerTransfer rules."""
    pairs: list[tuple[str, str]] = []
    for rule in root_agent_json.get("transferRules", []):
        if not isinstance(rule, dict) or "disablePlannerTransfer" not in rule:
            continue
        dpt = rule["disablePlannerTransfer"]
        expr = ""
        if isinstance(dpt, dict):
            ec = dpt.get("expressionCondition")
            if isinstance(ec, dict) and isinstance(ec.get("expression"), str):
                expr = ec["expression"]
            elif isinstance(dpt.get("expression"), str):
                expr = dpt["expression"]
            elif isinstance(dpt.get("condition"), str):
                expr = dpt["condition"]
        child = str(rule.get("childAgent", "")).split("/")[-1]
        pairs.append((child, expr))
    return pairs


def _find_tenant_ids_in_bundle(bundle_dir: Path) -> set[int]:
    """Extracts rendered tenantId / tenant_id integer values from bundle files."""
    found: set[int] = set()

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, val in node.items():
                norm = str(key).lower().replace("_", "")
                if norm == "tenantid":
                    if isinstance(val, int) and not isinstance(val, bool):
                        found.add(val)
                    elif isinstance(val, str) and val.isdigit():
                        found.add(int(val))
                    elif isinstance(val, dict):
                        for sub_k in ("default", "value", "const", "example"):
                            sub_v = val.get(sub_k)
                            if isinstance(sub_v, int) and not isinstance(
                                sub_v, bool
                            ):
                                found.add(sub_v)
                            elif isinstance(sub_v, str) and sub_v.isdigit():
                                found.add(int(sub_v))
                        _walk(val)
                else:
                    _walk(val)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    pattern = re.compile(
        r"tenant[_ ]?id[\"']?\s*[:=]\s*[\"']?(\d+)", re.IGNORECASE
    )
    for file_path in _iter_bundle_text_files(bundle_dir):
        text = file_path.read_text(encoding="utf-8")
        if file_path.suffix == ".json":
            _walk(json.loads(text))
        for match in pattern.finditer(text):
            found.add(int(match.group(1)))
    return found


def _materialize_live_snapshot(
    compiled_bundle_dir: Path,
    target_live_dir: Path,
    overlay_dir: Path | None = None,
) -> Path:
    """Builds a synthetic CES ExportApp snapshot with server-injected volatile fields."""
    if target_live_dir.exists():
        shutil.rmtree(target_live_dir)
    shutil.copytree(compiled_bundle_dir, target_live_dir)

    volatile_root = FIXTURES_DIR / "live_volatile_overlay"
    app_volatile = _read_json(volatile_root / "app.json")
    res_template = _read_json(volatile_root / "resource_metadata_template.json")
    project_prefix = res_template["projectPrefix"]
    uuid_ns = res_template["uuidNamespace"]

    json_files = sorted(target_live_dir.rglob("*.json"))
    for idx, json_path in enumerate(json_files, start=1):
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


def _get_surface_config(
    runtime_policy_data: dict[str, Any], surface_id: str
) -> dict[str, Any]:
    surfaces = runtime_policy_data["surfaces"]
    if isinstance(surfaces, dict):
        return surfaces[surface_id]
    for entry in surfaces:
        if (
            entry.get("id") == surface_id
            or entry.get("surface_id") == surface_id
        ):
            return entry
    raise KeyError(f"Surface {surface_id!r} not found in runtime_policy.yaml")


# ---------------------------------------------------------------------------
# 1. Compilation, CLI & Bundle Layout Tests
# ---------------------------------------------------------------------------


def test_four_bundles_compile_cleanly_via_api_and_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api_out = tmp_path / "dist_api"
    results = compile_blueprint(BLUEPRINT_DIR, api_out)

    assert len(results) == 4
    assert {r.app_id for r in results} == EXPECTED_BUNDLES

    for res in results:
        assert res.violations == []
        assert isinstance(res.runtime_rules_count, int)
        assert res.runtime_rules_count > 0
        assert res.out_dir == api_out / res.app_id
        app_json = _read_json(res.out_dir / "app.json")
        assert "modelPools" not in app_json
        metadata = app_json.get("metadata", {})
        assert isinstance(metadata, dict)
        assert metadata
        assert all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in metadata.items()
        )
        assert (res.out_dir / "global_instruction.txt").is_file()
        assert (
            res.out_dir / "agents" / "root_agent" / "root_agent.json"
        ).is_file()
        assert (
            res.out_dir / "agents" / "root_agent" / "instruction.txt"
        ).is_file()
        assert run_firewall(res.out_dir, blueprint_dir=BLUEPRINT_DIR) == []

    capsys.readouterr()
    cli_out = tmp_path / "dist_cli"
    rc = _run_cxas_cli(
        [
            "build",
            "--blueprint",
            str(BLUEPRINT_DIR),
            "--out",
            str(cli_out),
            "--write-locks",
        ]
    )
    assert rc == 0
    build_stdout = capsys.readouterr().out
    for app_id in EXPECTED_BUNDLES:
        assert app_id in build_stdout
    assert "branch_information_agent" in build_stdout
    assert "find_branches" in build_stdout
    assert re.search(r"runtime[_ ]rules?\b", build_stdout, re.IGNORECASE)
    for res in results:
        assert str(res.runtime_rules_count) in build_stdout
    assert "0 violations" in build_stdout.lower()

    for app_id in EXPECTED_BUNDLES:
        assert (cli_out / app_id / "app.json").is_file()
        assert (
            _run_cxas_cli(
                [
                    "firewall",
                    "--bundle",
                    str(cli_out / app_id),
                    "--blueprint",
                    str(BLUEPRINT_DIR),
                ]
            )
            == 0
        )

    filtered_out = tmp_path / "dist_filtered"
    rc_filtered = _run_cxas_cli(
        [
            "build",
            "--blueprint",
            str(BLUEPRINT_DIR),
            "--out",
            str(filtered_out),
            "--brand",
            "cymbal_direct",
            "--modality",
            "digital",
        ]
    )
    assert rc_filtered == 0
    emitted_dirs = {p.name for p in filtered_out.iterdir() if p.is_dir()}
    assert emitted_dirs == {"cymbal_direct-digital"}


# ---------------------------------------------------------------------------
# 2. Hard Brand Isolation (D-1)
# ---------------------------------------------------------------------------


def test_hard_brand_isolation_cymbal_retail_vs_cymbal_direct(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "dist"
    results = {r.app_id: r for r in compile_blueprint(BLUEPRINT_DIR, out_dir)}

    for app_id in ("cymbal_direct-voice", "cymbal_direct-digital"):
        bundle = out_dir / app_id
        res = results[app_id]
        assert "branch_information_agent" in res.pruned_agents
        assert "branch_information_agent" not in res.active_agents
        assert "find_branches" in res.pruned_tools
        assert "find_branches" not in res.active_tools

        for p in bundle.rglob("*"):
            assert "branch_information_agent" not in p.name
            assert "find_branches" not in p.name

        for file_path in _iter_bundle_text_files(bundle):
            content = file_path.read_text(encoding="utf-8")
            assert "branch_information_agent" not in content, (
                f"Leaked branch_information_agent in {file_path}"
            )
            assert "find_branches" not in content, (
                f"Leaked find_branches in {file_path}"
            )
            assert "Cymbal Retail Bank" not in content, (
                f"Leaked forbidden sister-brand token in {file_path}"
            )

        root_json = _read_json(
            bundle / "agents" / "root_agent" / "root_agent.json"
        )
        child_names = {
            str(c).split("/")[-1] for c in root_json.get("childAgents", [])
        }
        assert child_names == CYMBAL_DIRECT_SURVIVING_SPECIALISTS

    for app_id in ("cymbal_retail-voice", "cymbal_retail-digital"):
        bundle = out_dir / app_id
        assert (
            bundle
            / "agents"
            / "branch_information_agent"
            / "branch_information_agent.json"
        ).is_file()
        assert (
            bundle / "agents" / "branch_information_agent" / "instruction.txt"
        ).is_file()
        assert (
            bundle / "tools" / "find_branches" / "find_branches.json"
        ).is_file()

        root_json = _read_json(
            bundle / "agents" / "root_agent" / "root_agent.json"
        )
        child_names = {
            str(c).split("/")[-1] for c in root_json.get("childAgents", [])
        }
        assert child_names == ALL_SPECIALISTS

        for file_path in _iter_bundle_text_files(bundle):
            content = file_path.read_text(encoding="utf-8")
            assert "cymbal direct" not in content.lower(), (
                f"Leaked forbidden token Cymbal Direct in {file_path}"
            )


def test_brand_tts_voices_and_tenant_ids(tmp_path: Path) -> None:
    out_dir = tmp_path / "dist"
    compile_blueprint(BLUEPRINT_DIR, out_dir)

    retail_voice_app = (out_dir / "cymbal_retail-voice" / "app.json").read_text(
        encoding="utf-8"
    )
    direct_voice_app = (out_dir / "cymbal_direct-voice" / "app.json").read_text(
        encoding="utf-8"
    )

    assert "en-US-Chirp3-HD-Charon" in retail_voice_app
    assert "en-US-Chirp3-HD-Aoede" not in retail_voice_app

    assert "en-US-Chirp3-HD-Aoede" in direct_voice_app
    assert "en-US-Chirp3-HD-Charon" not in direct_voice_app

    for app_id in ("cymbal_retail-voice", "cymbal_retail-digital"):
        tenant_ids = _find_tenant_ids_in_bundle(out_dir / app_id)
        assert 0 in tenant_ids
        assert 2 not in tenant_ids

    for app_id in ("cymbal_direct-voice", "cymbal_direct-digital"):
        tenant_ids = _find_tenant_ids_in_bundle(out_dir / app_id)
        assert 2 in tenant_ids
        assert 0 not in tenant_ids


# ---------------------------------------------------------------------------
# 3. Modality Pruning (Voice vs. Digital)
# ---------------------------------------------------------------------------


def test_digital_modality_prunes_supervisor_guardrails_and_sip_tool(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "dist"
    compile_blueprint(BLUEPRINT_DIR, out_dir)

    for app_id in ("cymbal_retail-digital", "cymbal_direct-digital"):
        bundle = out_dir / app_id
        app_json = _read_json(bundle / "app.json")

        for g_ref in app_json.get("guardrails", []):
            assert "Supervisor_" not in str(g_ref)

        guardrails_dir = bundle / "guardrails"
        if guardrails_dir.exists():
            for p in guardrails_dir.iterdir():
                assert not p.name.startswith("Supervisor_")

        assert not (bundle / "tools" / "sip_transfer_to_queue").exists()

        for file_path in _iter_bundle_text_files(bundle):
            text = file_path.read_text(encoding="utf-8")
            assert "sip_transfer_to_queue" not in text
            assert "Supervisor_" not in text


def test_voice_modality_prunes_digital_tools_keeps_supervisors_and_disables_audio_rewriter(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "dist"
    compile_blueprint(BLUEPRINT_DIR, out_dir)

    for app_id in ("cymbal_retail-voice", "cymbal_direct-voice"):
        bundle = out_dir / app_id
        app_json = _read_json(bundle / "app.json")

        assert not (bundle / "tools" / "display_deeplink").exists()
        assert not (bundle / "tools" / "request_websocket_handover").exists()

        for file_path in _iter_bundle_text_files(bundle):
            text = file_path.read_text(encoding="utf-8")
            assert "display_deeplink" not in text
            assert "request_websocket_handover" not in text

        supervisor_files = sorted(
            (bundle / "guardrails").glob("Supervisor_*/Supervisor_*.json")
        )
        assert len(supervisor_files) >= 1
        attached_guardrails = {
            str(g).split("/")[-1] for g in app_json.get("guardrails", [])
        }
        for sup_file in supervisor_files:
            sup_json = _read_json(sup_file)
            assert "action" not in sup_json
            assert sup_file.parent.name in attached_guardrails
            sup_block = sup_json.get("supervisor", {})
            assert sup_block.get("detectionMode") == "NON_BLOCKING"
            assert sup_block.get("type")

        datastore_tools = []
        for tool_file in sorted((bundle / "tools").glob("*/*.json")):
            tool_json = _read_json(tool_file)
            if "dataStoreTool" in tool_json:
                datastore_tools.append(tool_json["dataStoreTool"])
        assert len(datastore_tools) >= 1

        for ds in datastore_tools:
            assert ds.get("name")
            assert ds.get("description")
            assert ds.get("engineSource")
            mod_configs = ds.get("modalityConfigs", [])
            audio_cfgs = [
                mc
                for mc in mod_configs
                if (mc.get("modalityType") or mc.get("modality")) == "AUDIO"
            ]
            assert len(audio_cfgs) == 1
            audio_cfg = audio_cfgs[0]
            rewriter = (
                audio_cfg.get("rewriterConfig")
                or audio_cfg.get("rewriter")
                or {}
            )
            summarizer = (
                audio_cfg.get("summarizationConfig")
                or audio_cfg.get("summarizerConfig")
                or audio_cfg.get("summarizer")
                or {}
            )
            assert rewriter.get("disabled") is True
            assert summarizer.get("disabled") is True


# ---------------------------------------------------------------------------
# 4. Runtime Policy, Zero Deployments, Templates & Schema Validation
# ---------------------------------------------------------------------------


def test_runtime_surface_segment_kill_switch_and_zero_deployments(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "dist"
    compile_blueprint(BLUEPRINT_DIR, out_dir)

    for p in out_dir.rglob("*"):
        assert p.name != "deployments"
        assert "deployments" not in p.parts

    for app_id in EXPECTED_BUNDLES:
        bundle = out_dir / app_id
        app_json = _read_json(bundle / "app.json")
        assert "deployments" not in app_json

        root_json = _read_json(
            bundle / "agents" / "root_agent" / "root_agent.json"
        )
        rules = _extract_disable_transfer_rules(root_json)

        assert any(
            child == "credit_cards_agent"
            and "session_ctx.segment == 'employee'" in expr
            for child, expr in rules
        )

        if app_id.endswith("-digital"):
            for blocked in ("credit_cards_agent", "wire_transfer_agent"):
                assert any(
                    child == blocked
                    and "session_ctx.surface_id == 'public-web'" in expr
                    for child, expr in rules
                )
        else:
            assert not any("public-web" in expr for _, expr in rules)

        expected_specialists = (
            ALL_SPECIALISTS
            if app_id.startswith("cymbal_retail-")
            else CYMBAL_DIRECT_SURVIVING_SPECIALISTS
        )
        kill_switch_targets = {
            child
            for child, expr in rules
            if f"session_ctx.disabled_agents.{child} == true" in expr
        }
        assert kill_switch_targets == expected_specialists
        if app_id.startswith("cymbal_direct-"):
            assert not any(
                child == "branch_information_agent" for child, _ in rules
            )

        txt_files = sorted(bundle.rglob("*.txt"))
        saw_session_ctx_var = False
        for txt_file in txt_files:
            txt = txt_file.read_text(encoding="utf-8")
            assert "{%" not in txt, f"Jinja control block found in {txt_file}"
            assert "{@start" not in txt, (
                f"Legacy modality tag found in {txt_file}"
            )
            if "{{session_ctx" in txt:
                saw_session_ctx_var = True
        assert saw_session_ctx_var


def test_empty_tuning_overlay_matches_absent_tuning_overlay_byte_for_byte(
    tmp_path: Path,
) -> None:
    bp_empty = tmp_path / "bp_empty"
    bp_absent = tmp_path / "bp_absent"
    shutil.copytree(BLUEPRINT_DIR, bp_empty)
    shutil.copytree(BLUEPRINT_DIR, bp_absent)

    (bp_empty / "tuning_overlay.yaml").write_text(
        'schema_version: "1.0"\noverlays: {}\n', encoding="utf-8"
    )
    overlay_absent = bp_absent / "tuning_overlay.yaml"
    if overlay_absent.exists():
        overlay_absent.unlink()

    out_empty = tmp_path / "out_empty"
    out_absent = tmp_path / "out_absent"
    compile_blueprint(bp_empty, out_empty)
    compile_blueprint(bp_absent, out_absent)

    tree_empty = _collect_bytes_tree(out_empty)
    tree_absent = _collect_bytes_tree(out_absent)
    assert tree_empty.keys() == tree_absent.keys()
    assert tree_empty == tree_absent


@pytest.mark.parametrize(
    "relative_yaml",
    [
        "blueprint.yaml",
        "brands/cymbal_retail.yaml",
        "modalities/digital.yaml",
        "runtime_policy.yaml",
        "tuning_overlay.yaml",
    ],
)
def test_pydantic_extra_forbid_rejects_unknown_yaml_fields(
    tmp_path: Path, relative_yaml: str
) -> None:
    bp_copy = tmp_path / "bp_invalid"
    shutil.copytree(BLUEPRINT_DIR, bp_copy)

    yaml_path = bp_copy / relative_yaml
    existing = (
        yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        if yaml_path.exists()
        else {}
    )
    if not isinstance(existing, dict):
        existing = {}
    existing["unexpected_extra_schema_field"] = "forbidden"
    yaml_path.write_text(yaml.safe_dump(existing), encoding="utf-8")

    with pytest.raises(
        (BlueprintCompilerError, CompilerError, pydantic.ValidationError)
    ):
        compile_blueprint(bp_copy, tmp_path / "out")


# ---------------------------------------------------------------------------
# 5. Ten Dedicated Negative Firewall Tests (FW01 .. FW10)
# ---------------------------------------------------------------------------


def test_fw01_rejects_subagent_callback_and_python_tool(tmp_path: Path) -> None:
    out_a = tmp_path / "out_a"
    compile_blueprint(
        BLUEPRINT_DIR,
        out_a,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_a = out_a / "cymbal_retail-digital"
    spec_path = (
        bundle_a / "agents" / "credit_cards_agent" / "credit_cards_agent.json"
    )
    spec_json = _read_json(spec_path)
    spec_json["beforeModelCallbacks"] = [
        {
            "description": "Illegal specialist callback",
            "pythonCode": (
                "def before_model_callback(callback_context, llm_request):\n"
                "    return None\n"
            ),
        }
    ]
    _write_json(spec_path, spec_json)

    violations_a = run_firewall(bundle_a, blueprint_dir=BLUEPRINT_DIR)
    assert "FW01" in {v.rule_id for v in violations_a}
    assert (
        _run_cxas_cli(
            [
                "firewall",
                "--bundle",
                str(bundle_a),
                "--blueprint",
                str(BLUEPRINT_DIR),
            ]
        )
        != 0
    )

    out_b = tmp_path / "out_b"
    compile_blueprint(
        BLUEPRINT_DIR,
        out_b,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_b = out_b / "cymbal_retail-digital"
    py_tool_path = (
        bundle_b / "tools" / "forbidden_py_tool" / "forbidden_py_tool.json"
    )
    _write_json(
        py_tool_path,
        {
            "displayName": "forbidden_py_tool",
            "description": "Synthetic forbidden Python function tool",
            "pythonFunction": {
                "name": "forbidden_py_tool",
                "pythonCode": "def forbidden_py_tool(args):\n    return {'ok': True}\n",
            },
        },
    )
    auth_path = (
        bundle_b
        / "agents"
        / "authentication_agent"
        / "authentication_agent.json"
    )
    auth_json = _read_json(auth_path)
    existing_tools = auth_json.get("tools", [])
    use_prefix = bool(existing_tools and "/" in str(existing_tools[0]))
    tool_ref = "tools/forbidden_py_tool" if use_prefix else "forbidden_py_tool"
    auth_json.setdefault("tools", []).append(tool_ref)
    _write_json(auth_path, auth_json)

    violations_b = run_firewall(bundle_b, blueprint_dir=BLUEPRINT_DIR)
    assert "FW01" in {v.rule_id for v in violations_b}


def test_fw02_rejects_more_than_6_tools_on_specialist(tmp_path: Path) -> None:
    out_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR,
        out_dir,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle = out_dir / "cymbal_retail-digital"

    available_tool_names = sorted(
        p.name for p in (bundle / "tools").iterdir() if p.is_dir()
    )
    assert len(available_tool_names) >= 7

    spec_path = (
        bundle / "agents" / "credit_cards_agent" / "credit_cards_agent.json"
    )
    spec_json = _read_json(spec_path)
    existing_tools = spec_json.get("tools", [])
    use_prefix = bool(existing_tools and "/" in str(existing_tools[0]))
    spec_json["tools"] = [
        f"tools/{name}" if use_prefix else name
        for name in available_tool_names[:7]
    ]
    _write_json(spec_path, spec_json)

    violations = run_firewall(bundle, blueprint_dir=BLUEPRINT_DIR)
    assert "FW02" in {v.rule_id for v in violations}


def test_fw03_rejects_excess_object_or_total_variables(tmp_path: Path) -> None:
    out_a = tmp_path / "out_a"
    compile_blueprint(
        BLUEPRINT_DIR,
        out_a,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_a = out_a / "cymbal_retail-digital"
    app_a_path = bundle_a / "app.json"
    app_a = _read_json(app_a_path)
    existing_vars = app_a.get("variableDeclarations", [])

    obj_template: dict[str, Any] | None = None
    for decl in existing_vars:
        schema = decl.get("schema", decl)
        if str(schema.get("type", "")).upper() == "OBJECT":
            obj_template = copy.deepcopy(decl)
            break

    seventeen_objects = []
    for i in range(17):
        if obj_template is not None:
            entry = copy.deepcopy(obj_template)
            entry["name"] = f"synthetic_obj_{i}"
        else:
            entry = {
                "name": f"synthetic_obj_{i}",
                "description": "Synthetic root object variable",
                "schema": {
                    "type": "OBJECT",
                    "properties": {"field": {"type": "STRING"}},
                },
            }
        seventeen_objects.append(entry)

    app_a["variableDeclarations"] = seventeen_objects
    _write_json(app_a_path, app_a)

    violations_a = run_firewall(bundle_a, blueprint_dir=BLUEPRINT_DIR)
    assert "FW03" in {v.rule_id for v in violations_a}

    out_b = tmp_path / "out_b"
    compile_blueprint(
        BLUEPRINT_DIR,
        out_b,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_b = out_b / "cymbal_retail-digital"
    app_b_path = bundle_b / "app.json"
    app_b = _read_json(app_b_path)
    decls_b = list(app_b.get("variableDeclarations", []))
    while len(decls_b) < 101:
        idx = len(decls_b)
        decls_b.append(
            {
                "name": f"synthetic_scalar_var_{idx}",
                "description": "Synthetic scalar string variable",
                "schema": {"type": "STRING"},
            }
        )
    app_b["variableDeclarations"] = decls_b
    _write_json(app_b_path, app_b)

    violations_b = run_firewall(bundle_b, blueprint_dir=BLUEPRINT_DIR)
    assert "FW03" in {v.rule_id for v in violations_b}


def test_fw04_rejects_oversized_prompts(tmp_path: Path) -> None:
    out_a = tmp_path / "out_a"
    compile_blueprint(
        BLUEPRINT_DIR,
        out_a,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_a = out_a / "cymbal_retail-digital"
    root_instr = bundle_a / "agents" / "root_agent" / "instruction.txt"
    root_instr.write_text(
        root_instr.read_text(encoding="utf-8") + "\n" + ("R" * 15_500),
        encoding="utf-8",
    )
    assert len(root_instr.read_bytes()) > 15_360

    violations_a = run_firewall(bundle_a, blueprint_dir=BLUEPRINT_DIR)
    assert "FW04" in {v.rule_id for v in violations_a}

    out_b = tmp_path / "out_b"
    compile_blueprint(
        BLUEPRINT_DIR,
        out_b,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_b = out_b / "cymbal_retail-digital"
    spec_instr = bundle_b / "agents" / "credit_cards_agent" / "instruction.txt"
    spec_instr.write_text(
        spec_instr.read_text(encoding="utf-8") + "\n" + ("S" * 18_600),
        encoding="utf-8",
    )
    assert len(spec_instr.read_bytes()) > 18_432

    violations_b = run_firewall(bundle_b, blueprint_dir=BLUEPRINT_DIR)
    assert "FW04" in {v.rule_id for v in violations_b}


def test_fw05_rejects_cross_brand_token_leak(tmp_path: Path) -> None:
    bp_local = tmp_path / "blueprint"
    shutil.copytree(BLUEPRINT_DIR, bp_local)
    out_dir = tmp_path / "dist"
    compile_blueprint(
        bp_local,
        out_dir,
        brand_filter="cymbal_direct",
        modality_filter="digital",
    )
    bundle = out_dir / "cymbal_direct-digital"

    spec_instr = bundle / "agents" / "credit_cards_agent" / "instruction.txt"
    spec_instr.write_text(
        spec_instr.read_text(encoding="utf-8")
        + "\nPlease visit your nearest Cymbal Retail Bank counter.\n",
        encoding="utf-8",
    )

    violations = run_firewall(bundle, blueprint_dir=BLUEPRINT_DIR)
    assert "FW05" in {v.rule_id for v in violations}
    assert (
        _run_cxas_cli(
            [
                "firewall",
                "--bundle",
                str(bundle),
                "--blueprint",
                str(BLUEPRINT_DIR),
            ]
        )
        != 0
    )

    violations_auto = run_firewall(bundle)
    assert "FW05" in {v.rule_id for v in violations_auto}

    out_ph = tmp_path / "dist_ph"
    compile_blueprint(
        BLUEPRINT_DIR,
        out_ph,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_ph = out_ph / "cymbal_retail-digital"
    guide_instr = bundle_ph / "agents" / "guide_agent" / "instruction.txt"
    guide_instr.write_text(
        guide_instr.read_text(encoding="utf-8")
        + "\nRefer customer to {unknown_placeholder} for fee schedules.\n",
        encoding="utf-8",
    )
    violations_ph = run_firewall(bundle_ph, blueprint_dir=BLUEPRINT_DIR)
    assert "FW05" in {v.rule_id for v in violations_ph}


@pytest.mark.parametrize(
    "bad_regex",
    [
        "(?=foo)bar",
        "(?!secret)",
        "(?<=abc)def",
        "(?<!xyz)123",
        r"(abc)\1",
        "(?>secret)",
        "a++",
        "(?P<x>foo)(?P=x)",
        "(a)(?(1)b|c)",
    ],
)
def test_fw06_rejects_non_re2_lookaround_regex(
    tmp_path: Path, bad_regex: str
) -> None:
    out_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR,
        out_dir,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle = out_dir / "cymbal_retail-digital"

    cf_path = (
        bundle
        / "guardrails"
        / "pii_account_content_filter"
        / "pii_account_content_filter.json"
    )
    cf_json = _read_json(cf_path)
    cf_json.setdefault("contentFilter", {}).setdefault(
        "bannedContents", []
    ).append(bad_regex)
    _write_json(cf_path, cf_json)

    violations = run_firewall(bundle, blueprint_dir=BLUEPRINT_DIR)
    assert "FW06" in {v.rule_id for v in violations}


def test_fw07_rejects_supervisor_with_action_or_on_digital(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "dist"
    compile_blueprint(BLUEPRINT_DIR, out_dir, brand_filter="cymbal_retail")
    voice_bundle = out_dir / "cymbal_retail-voice"
    digital_bundle = out_dir / "cymbal_retail-digital"

    sup_source = (
        voice_bundle
        / "guardrails"
        / "Supervisor_Audio_Mismatch"
        / "Supervisor_Audio_Mismatch.json"
    )
    assert sup_source.is_file()
    sup_name = sup_source.parent.name

    digital_sup_path = (
        digital_bundle / "guardrails" / sup_name / f"{sup_name}.json"
    )
    _write_json(digital_sup_path, _read_json(sup_source))
    digital_app_path = digital_bundle / "app.json"
    digital_app = _read_json(digital_app_path)
    existing_g = digital_app.get("guardrails", [])
    use_prefix = bool(existing_g and "/" in str(existing_g[0]))
    digital_app.setdefault("guardrails", []).append(
        f"guardrails/{sup_name}" if use_prefix else sup_name
    )
    _write_json(digital_app_path, digital_app)

    violations_digital = run_firewall(
        digital_bundle, blueprint_dir=BLUEPRINT_DIR
    )
    assert "FW07" in {v.rule_id for v in violations_digital}

    sup_voice_json = _read_json(sup_source)
    sup_voice_json["action"] = {
        "respondImmediately": {
            "responses": [{"text": "Forbidden supervisor action."}]
        }
    }
    _write_json(sup_source, sup_voice_json)

    violations_voice = run_firewall(voice_bundle, blueprint_dir=BLUEPRINT_DIR)
    assert "FW07" in {v.rule_id for v in violations_voice}

    out_blocking = tmp_path / "dist_blocking"
    compile_blueprint(
        BLUEPRINT_DIR,
        out_blocking,
        brand_filter="cymbal_retail",
        modality_filter="voice",
    )
    voice_blocking = out_blocking / "cymbal_retail-voice"
    sup_blocking_path = (
        voice_blocking
        / "guardrails"
        / "Supervisor_Audio_Mismatch"
        / "Supervisor_Audio_Mismatch.json"
    )
    sup_blocking_json = _read_json(sup_blocking_path)
    sup_blocking_json.setdefault("supervisor", {})["detectionMode"] = "BLOCKING"
    _write_json(sup_blocking_path, sup_blocking_json)

    violations_blocking = run_firewall(
        voice_blocking, blueprint_dir=BLUEPRINT_DIR
    )
    assert "FW07" in {v.rule_id for v in violations_blocking}


def test_fw08_rejects_unpinned_models_and_missing_never_include(
    tmp_path: Path,
) -> None:
    out_a1 = tmp_path / "out_a1"
    compile_blueprint(
        BLUEPRINT_DIR,
        out_a1,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_a1 = out_a1 / "cymbal_retail-digital"
    root_path = bundle_a1 / "agents" / "root_agent" / "root_agent.json"
    root_json = _read_json(root_path)
    root_json["modelSettings"] = {"model": "gemini-latest"}
    _write_json(root_path, root_json)

    violations_a1 = run_firewall(bundle_a1, blueprint_dir=BLUEPRINT_DIR)
    assert "FW08" in {v.rule_id for v in violations_a1}

    bp_unpinned = tmp_path / "bp_unpinned"
    shutil.copytree(BLUEPRINT_DIR, bp_unpinned)
    bp_yaml_path = bp_unpinned / "blueprint.yaml"
    bp_yaml = yaml.safe_load(bp_yaml_path.read_text(encoding="utf-8"))
    bp_yaml["model_pools"]["agent_planner"] = "default"
    bp_yaml_path.write_text(yaml.safe_dump(bp_yaml), encoding="utf-8")

    res_pool = compile_blueprint(
        bp_unpinned,
        tmp_path / "out_pool_check",
        brand_filter="cymbal_retail",
        modality_filter="digital",
        fail_on_firewall=False,
    )
    assert "FW08" in {v.rule_id for v in res_pool[0].violations}
    with pytest.raises((FirewallError, BlueprintCompilerError)):
        compile_blueprint(
            bp_unpinned,
            tmp_path / "out_pool_fail",
            brand_filter="cymbal_retail",
            modality_filter="digital",
            fail_on_firewall=True,
        )

    out_a2 = tmp_path / "out_a2"
    compile_blueprint(
        BLUEPRINT_DIR,
        out_a2,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_a2 = out_a2 / "cymbal_retail-digital"
    spec_path = bundle_a2 / "agents" / "guide_agent" / "guide_agent.json"
    spec_json = _read_json(spec_path)
    spec_json["modelSettings"] = {"model": "default"}
    _write_json(spec_path, spec_json)
    violations_a2 = run_firewall(bundle_a2, blueprint_dir=BLUEPRINT_DIR)
    assert "FW08" in {v.rule_id for v in violations_a2}

    out_b = tmp_path / "out_b"
    compile_blueprint(
        BLUEPRINT_DIR,
        out_b,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_b = out_b / "cymbal_retail-digital"
    ds_tool_file = next(
        p
        for p in sorted((bundle_b / "tools").glob("*/*.json"))
        if "dataStoreTool" in _read_json(p)
    )
    ds_tool_json = _read_json(ds_tool_file)
    ds_tool_json["dataStoreTool"]["filterParameterBehavior"] = "ALWAYS_INCLUDE"
    _write_json(ds_tool_file, ds_tool_json)

    violations_b = run_firewall(bundle_b, blueprint_dir=BLUEPRINT_DIR)
    assert "FW08" in {v.rule_id for v in violations_b}

    out_c = tmp_path / "out_c"
    compile_blueprint(
        BLUEPRINT_DIR,
        out_c,
        brand_filter="cymbal_retail",
        modality_filter="voice",
    )
    bundle_c = out_c / "cymbal_retail-voice"
    ds_voice_file = next(
        p
        for p in sorted((bundle_c / "tools").glob("*/*.json"))
        if "dataStoreTool" in _read_json(p)
    )
    ds_voice_json = _read_json(ds_voice_file)
    for mc in ds_voice_json["dataStoreTool"]["modalityConfigs"]:
        if mc.get("modalityType") == "AUDIO":
            mc["rewriterConfig"]["disabled"] = False
    _write_json(ds_voice_file, ds_voice_json)
    violations_c = run_firewall(bundle_c, blueprint_dir=BLUEPRINT_DIR)
    assert "FW08" in {v.rule_id for v in violations_c}

    out_d = tmp_path / "out_d"
    compile_blueprint(
        BLUEPRINT_DIR,
        out_d,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_d = out_d / "cymbal_retail-digital"
    ds_text_file = next(
        p
        for p in sorted((bundle_d / "tools").glob("*/*.json"))
        if "dataStoreTool" in _read_json(p)
    )
    ds_text_json = _read_json(ds_text_file)
    for mc in ds_text_json["dataStoreTool"]["modalityConfigs"]:
        if mc.get("modalityType") == "TEXT":
            mc["rewriterConfig"]["disabled"] = False
            mc["rewriterConfig"].pop("modelSettings", None)
    _write_json(ds_text_file, ds_text_json)
    violations_d = run_firewall(bundle_d, blueprint_dir=BLUEPRINT_DIR)
    assert "FW08" in {v.rule_id for v in violations_d}

    out_e = tmp_path / "out_e"
    compile_blueprint(
        BLUEPRINT_DIR,
        out_e,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_e = out_e / "cymbal_retail-digital"
    ps_path = (
        bundle_e
        / "guardrails"
        / "prompt_security_guard"
        / "prompt_security_guard.json"
    )
    ps_json = _read_json(ps_path)
    ps_json["llmPromptSecurity"] = {"defaultSettings": {}}
    _write_json(ps_path, ps_json)
    violations_e = run_firewall(bundle_e, blueprint_dir=BLUEPRINT_DIR)
    assert "FW08" in {v.rule_id for v in violations_e}


def test_fw09_rejects_jinja_control_blocks(tmp_path: Path) -> None:
    out_a = tmp_path / "dist_a"
    compile_blueprint(
        BLUEPRINT_DIR,
        out_a,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_a = out_a / "cymbal_retail-digital"
    instr_path = bundle_a / "agents" / "credit_cards_agent" / "instruction.txt"
    instr_path.write_text(
        instr_path.read_text(encoding="utf-8")
        + "\n{% if surface == 'mobile' %}hi{% endif %}\n",
        encoding="utf-8",
    )
    violations_a = run_firewall(bundle_a, blueprint_dir=BLUEPRINT_DIR)
    assert "FW09" in {v.rule_id for v in violations_a}

    out_b = tmp_path / "dist_b"
    compile_blueprint(
        BLUEPRINT_DIR,
        out_b,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_b = out_b / "cymbal_retail-digital"
    guide_instr = bundle_b / "agents" / "guide_agent" / "instruction.txt"
    guide_instr.write_text(
        guide_instr.read_text(encoding="utf-8")
        + "\n{@startModality_Voice}Speak concisely.{@endModality_Voice}\n",
        encoding="utf-8",
    )
    violations_b = run_firewall(bundle_b, blueprint_dir=BLUEPRINT_DIR)
    assert "FW09" in {v.rule_id for v in violations_b}

    out_c = tmp_path / "dist_c"
    compile_blueprint(
        BLUEPRINT_DIR,
        out_c,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_c = out_c / "cymbal_retail-digital"
    app_c_path = bundle_c / "app.json"
    app_c = _read_json(app_c_path)
    app_c["deployments"] = {}
    _write_json(app_c_path, app_c)
    violations_c = run_firewall(bundle_c, blueprint_dir=BLUEPRINT_DIR)
    assert "FW09" in {v.rule_id for v in violations_c}


def test_fw10_rejects_unresolvable_runtime_policy_reference(
    tmp_path: Path,
) -> None:
    bp_a = tmp_path / "bp_a"
    shutil.copytree(BLUEPRINT_DIR, bp_a)
    rp_a_path = bp_a / "runtime_policy.yaml"
    rp_a = yaml.safe_load(rp_a_path.read_text(encoding="utf-8"))
    pub_web = _get_surface_config(rp_a, "public-web")
    pub_web.setdefault("blocked_agents", []).append("nonexistent_specialist")
    rp_a_path.write_text(yaml.safe_dump(rp_a), encoding="utf-8")

    res_a = compile_blueprint(
        bp_a,
        tmp_path / "out_a",
        brand_filter="cymbal_retail",
        modality_filter="digital",
        fail_on_firewall=False,
    )
    assert "FW10" in {v.rule_id for v in res_a[0].violations}
    with pytest.raises(FirewallError):
        compile_blueprint(
            bp_a,
            tmp_path / "out_a_strict",
            brand_filter="cymbal_retail",
            modality_filter="digital",
            fail_on_firewall=True,
        )

    bp_b = tmp_path / "bp_b"
    shutil.copytree(BLUEPRINT_DIR, bp_b)
    rp_b_path = bp_b / "runtime_policy.yaml"
    rp_b = yaml.safe_load(rp_b_path.read_text(encoding="utf-8"))
    mobile_cfg = _get_surface_config(rp_b, "mobile-app")
    mobile_cfg["entry_agent"] = "branch_information_agent"
    rp_b_path.write_text(yaml.safe_dump(rp_b), encoding="utf-8")

    res_b = compile_blueprint(
        bp_b,
        tmp_path / "out_b",
        brand_filter="cymbal_direct",
        modality_filter="digital",
        fail_on_firewall=False,
    )
    assert "FW10" in {v.rule_id for v in res_b[0].violations}

    out_c = tmp_path / "out_c"
    compile_blueprint(
        BLUEPRINT_DIR,
        out_c,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_c = out_c / "cymbal_retail-digital"

    violations_bad_bp = run_firewall(
        bundle_c, blueprint_dir=tmp_path / "nonexistent_bp"
    )
    assert "FW10" in {v.rule_id for v in violations_bad_bp}
    assert (
        _run_cxas_cli(
            [
                "firewall",
                "--bundle",
                str(bundle_c),
                "--blueprint",
                str(tmp_path / "nonexistent_bp"),
            ]
        )
        != 0
    )

    root_path = bundle_c / "agents" / "root_agent" / "root_agent.json"
    root_json = _read_json(root_path)
    root_json.setdefault("transferRules", []).append(
        {
            "childAgent": "nonexistent_dangling_specialist",
            "direction": "PARENT_TO_CHILD",
            "disablePlannerTransfer": {
                "expression": "session_ctx.segment == 'employee'"
            },
        }
    )
    _write_json(root_path, root_json)

    violations_c = run_firewall(bundle_c, blueprint_dir=BLUEPRINT_DIR)
    assert "FW10" in {v.rule_id for v in violations_c}


# ---------------------------------------------------------------------------
# 6. Drift, 3-Way Conflict, Orphan Detection, Git Mirror & Pre-Push Gate Tests
# ---------------------------------------------------------------------------


def test_volatile_fields_normalize_to_clean_and_gate_succeeds(
    tmp_path: Path,
) -> None:
    dist_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR,
        dist_dir,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_dir = dist_dir / "cymbal_retail-digital"
    lock_path = write_lockfile(bundle_dir)
    assert lock_path.is_file()

    live_dir = _materialize_live_snapshot(
        bundle_dir,
        tmp_path / "live_clean",
        FIXTURES_DIR / "live_volatile_overlay",
    )
    live_app_raw = _read_json(live_dir / "app.json")
    for volatile_key in ("name", "uuid", "etag", "createTime", "updateTime"):
        assert volatile_key in live_app_raw

    assert extract_normalized_resources(
        bundle_dir
    ) == extract_normalized_resources(live_dir)

    conflicts_dir = tmp_path / "conflicts"
    report = check_drift(
        "cymbal_retail-digital",
        dist_dir,
        live_dir,
        lockfile_path=lock_path,
        conflicts_dir=conflicts_dir,
    )
    assert report.is_clean is True
    assert report.has_unresolved is False
    assert all(r.status == "CLEAN" for r in report.resources)

    assert (
        _run_cxas_cli(
            [
                "drift",
                "--app",
                "cymbal_retail-digital",
                "--dist",
                str(dist_dir),
                "--live",
                str(live_dir),
                "--lockfile",
                str(lock_path),
                "--conflicts-dir",
                str(conflicts_dir),
            ]
        )
        == 0
    )

    gate_report = enforce_pre_push_gate(
        bundle_dir,
        app_id="cymbal_retail-digital",
        live_dir=live_dir,
        lockfile_path=lock_path,
        blueprint_dir=BLUEPRINT_DIR,
        conflicts_dir=conflicts_dir,
    )
    assert gate_report is not None
    assert gate_report.is_clean is True


def test_live_console_drift_blocks_by_default_and_accepts_live(
    tmp_path: Path,
) -> None:
    dist_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR,
        dist_dir,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_dir = dist_dir / "cymbal_retail-digital"
    lock_path = write_lockfile(bundle_dir)

    live_dir = _materialize_live_snapshot(
        bundle_dir,
        tmp_path / "live_drift",
        FIXTURES_DIR / "live_cymbal_retail_digital_drift",
    )
    conflicts_dir = tmp_path / "conflicts"

    report = check_drift(
        "cymbal_retail-digital",
        dist_dir,
        live_dir,
        lockfile_path=lock_path,
        conflicts_dir=conflicts_dir,
    )
    assert report.is_clean is False
    assert report.has_unresolved is True
    by_id = {r.resource_id: r for r in report.resources}
    assert by_id["agents/human_interaction_agent"].status == "DRIFT"

    assert (
        _run_cxas_cli(
            [
                "drift",
                "--app",
                "cymbal_retail-digital",
                "--dist",
                str(dist_dir),
                "--live",
                str(live_dir),
                "--lockfile",
                str(lock_path),
                "--conflicts-dir",
                str(conflicts_dir),
            ]
        )
        != 0
    )

    with pytest.raises(DriftGateError):
        enforce_pre_push_gate(
            bundle_dir,
            app_id="cymbal_retail-digital",
            live_dir=live_dir,
            lockfile_path=lock_path,
            blueprint_dir=BLUEPRINT_DIR,
            conflicts_dir=conflicts_dir,
        )

    assert (
        _run_cxas_cli(
            [
                "drift",
                "--app",
                "cymbal_retail-digital",
                "--dist",
                str(dist_dir),
                "--live",
                str(live_dir),
                "--lockfile",
                str(lock_path),
                "--blueprint",
                str(BLUEPRINT_DIR),
                "--conflicts-dir",
                str(conflicts_dir),
                "--accept-live",
                "agents/human_interaction_agent",
            ]
        )
        == 0
    )

    backported_instr = (
        bundle_dir / "agents" / "human_interaction_agent" / "instruction.txt"
    ).read_text(encoding="utf-8")
    assert "Console Hotfix Note (2026-11-17):" in backported_instr

    followup = check_drift(
        "cymbal_retail-digital",
        dist_dir,
        live_dir,
        lockfile_path=lock_path,
        conflicts_dir=conflicts_dir,
    )
    assert followup.is_clean is True
    assert followup.has_unresolved is False
    gate_after = enforce_pre_push_gate(
        bundle_dir,
        app_id="cymbal_retail-digital",
        live_dir=live_dir,
        lockfile_path=lock_path,
        blueprint_dir=BLUEPRINT_DIR,
        conflicts_dir=conflicts_dir,
    )
    assert gate_after is not None
    assert gate_after.is_clean is True


def test_accept_live_without_prior_lockfile_backports_and_creates_lockfile(
    tmp_path: Path,
) -> None:
    dist_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR,
        dist_dir,
        brand_filter="cymbal_retail",
        modality_filter="digital",
        write_locks=False,
    )
    bundle_dir = dist_dir / "cymbal_retail-digital"
    default_lock = dist_dir / "cymbal_retail-digital.lock.json"
    assert not default_lock.exists()

    live_dir = _materialize_live_snapshot(
        bundle_dir,
        tmp_path / "live_drift_no_lock",
        FIXTURES_DIR / "live_cymbal_retail_digital_drift",
    )
    conflicts_dir = tmp_path / "conflicts"

    assert (
        _run_cxas_cli(
            [
                "drift",
                "--app",
                "cymbal_retail-digital",
                "--dist",
                str(dist_dir),
                "--live",
                str(live_dir),
                "--blueprint",
                str(BLUEPRINT_DIR),
                "--conflicts-dir",
                str(conflicts_dir),
                "--accept-live",
                "agents/human_interaction_agent",
            ]
        )
        == 0
    )
    assert default_lock.is_file()
    assert "Console Hotfix Note (2026-11-17):" in (
        bundle_dir / "agents" / "human_interaction_agent" / "instruction.txt"
    ).read_text(encoding="utf-8")

    followup = check_drift(
        "cymbal_retail-digital",
        dist_dir,
        live_dir,
        conflicts_dir=conflicts_dir,
    )
    assert followup.is_clean is True
    assert followup.has_unresolved is False
    gate_after = enforce_pre_push_gate(
        bundle_dir,
        app_id="cymbal_retail-digital",
        live_dir=live_dir,
        blueprint_dir=BLUEPRINT_DIR,
        conflicts_dir=conflicts_dir,
    )
    assert gate_after is not None
    assert gate_after.is_clean is True


def test_live_three_way_conflict_writes_diff_markers_and_honors_force_ours(
    tmp_path: Path,
) -> None:
    bp_copy = tmp_path / "blueprint"
    shutil.copytree(BLUEPRINT_DIR, bp_copy)

    dist_dir = tmp_path / "dist"
    compile_blueprint(
        bp_copy,
        dist_dir,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_dir = dist_dir / "cymbal_retail-digital"
    lock_path = write_lockfile(bundle_dir)

    live_dir = _materialize_live_snapshot(
        bundle_dir,
        tmp_path / "live_conflict",
        FIXTURES_DIR / "live_cymbal_retail_digital_conflict",
    )

    overlay_yaml_path = bp_copy / "tuning_overlay.yaml"
    overlay_data = yaml.safe_load(overlay_yaml_path.read_text(encoding="utf-8"))
    overlay_data.setdefault("overlays", {}).setdefault(
        "digital", {}
    ).setdefault("credit_cards_agent", {}).setdefault(
        "append_rules", []
    ).append(
        "Always check the current billing cycle cutoff date before quoting card fee waivers."
    )
    overlay_yaml_path.write_text(
        yaml.safe_dump(overlay_data, allow_unicode=True), encoding="utf-8"
    )
    compile_blueprint(
        bp_copy,
        dist_dir,
        brand_filter="cymbal_retail",
        modality_filter="digital",
        write_locks=False,
    )

    conflicts_dir = tmp_path / "conflicts"
    report = check_drift(
        "cymbal_retail-digital",
        dist_dir,
        live_dir,
        lockfile_path=lock_path,
        conflicts_dir=conflicts_dir,
    )
    assert report.is_clean is False
    assert report.has_unresolved is True

    by_id = {r.resource_id: r for r in report.resources}
    cc_drift = by_id["agents/credit_cards_agent"]
    assert cc_drift.status == "CONFLICT"

    diff_path = conflicts_dir / "agents" / "credit_cards_agent.diff"
    assert diff_path.is_file()
    diff_text = diff_path.read_text(encoding="utf-8")
    assert "<<<<<<< live" in diff_text
    assert "=======" in diff_text
    assert ">>>>>>> blueprint" in diff_text

    assert (
        _run_cxas_cli(
            [
                "drift",
                "--app",
                "cymbal_retail-digital",
                "--dist",
                str(dist_dir),
                "--live",
                str(live_dir),
                "--lockfile",
                str(lock_path),
                "--conflicts-dir",
                str(conflicts_dir),
            ]
        )
        != 0
    )
    with pytest.raises(DriftGateError):
        enforce_pre_push_gate(
            bundle_dir,
            app_id="cymbal_retail-digital",
            live_dir=live_dir,
            lockfile_path=lock_path,
            blueprint_dir=bp_copy,
            conflicts_dir=conflicts_dir,
        )

    assert (
        _run_cxas_cli(
            [
                "drift",
                "--app",
                "cymbal_retail-digital",
                "--dist",
                str(dist_dir),
                "--live",
                str(live_dir),
                "--lockfile",
                str(lock_path),
                "--conflicts-dir",
                str(conflicts_dir),
                "--force-ours",
                "agents/credit_cards_agent",
            ]
        )
        == 0
    )
    resolved_gate = enforce_pre_push_gate(
        bundle_dir,
        app_id="cymbal_retail-digital",
        live_dir=live_dir,
        lockfile_path=lock_path,
        blueprint_dir=bp_copy,
        conflicts_dir=conflicts_dir,
        force_ours=["agents/credit_cards_agent"],
    )
    assert resolved_gate is not None
    assert resolved_gate.has_unresolved is False


def test_live_orphan_tool_detected_and_refuses_push(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR,
        dist_dir,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_dir = dist_dir / "cymbal_retail-digital"
    lock_path = write_lockfile(bundle_dir)

    live_dir = _materialize_live_snapshot(
        bundle_dir,
        tmp_path / "live_orphan",
        FIXTURES_DIR / "live_cymbal_retail_digital_orphan",
    )
    assert (
        live_dir
        / "tools"
        / "console_adhoc_lookup"
        / "console_adhoc_lookup.json"
    ).is_file()

    conflicts_dir = tmp_path / "conflicts"
    report = check_drift(
        "cymbal_retail-digital",
        dist_dir,
        live_dir,
        lockfile_path=lock_path,
        conflicts_dir=conflicts_dir,
    )
    assert report.is_clean is False
    assert report.has_unresolved is True
    by_id = {r.resource_id: r for r in report.resources}
    assert by_id["tools/console_adhoc_lookup"].status == "ORPHAN"

    assert (
        _run_cxas_cli(
            [
                "drift",
                "--app",
                "cymbal_retail-digital",
                "--dist",
                str(dist_dir),
                "--live",
                str(live_dir),
                "--lockfile",
                str(lock_path),
                "--conflicts-dir",
                str(conflicts_dir),
            ]
        )
        != 0
    )

    with pytest.raises(DriftGateError):
        enforce_pre_push_gate(
            bundle_dir,
            app_id="cymbal_retail-digital",
            live_dir=live_dir,
            lockfile_path=lock_path,
            blueprint_dir=BLUEPRINT_DIR,
            conflicts_dir=conflicts_dir,
        )

    assert (
        _run_cxas_cli(
            [
                "drift",
                "--app",
                "cymbal_retail-digital",
                "--dist",
                str(dist_dir),
                "--live",
                str(live_dir),
                "--lockfile",
                str(lock_path),
                "--blueprint",
                str(BLUEPRINT_DIR),
                "--conflicts-dir",
                str(conflicts_dir),
                "--accept-live",
                "tools/console_adhoc_lookup",
            ]
        )
        == 0
    )
    backported_orphan = (
        bundle_dir
        / "tools"
        / "console_adhoc_lookup"
        / "console_adhoc_lookup.json"
    )
    assert backported_orphan.is_file()
    backported_orphan_json = _read_json(backported_orphan)
    for volatile_key in ("etag", "createTime", "updateTime", "uuid"):
        assert volatile_key not in backported_orphan_json

    followup = check_drift(
        "cymbal_retail-digital",
        dist_dir,
        live_dir,
        lockfile_path=lock_path,
        conflicts_dir=conflicts_dir,
    )
    assert followup.is_clean is True
    assert followup.has_unresolved is False
    gate_after = enforce_pre_push_gate(
        bundle_dir,
        app_id="cymbal_retail-digital",
        live_dir=live_dir,
        lockfile_path=lock_path,
        blueprint_dir=BLUEPRINT_DIR,
        conflicts_dir=conflicts_dir,
    )
    assert gate_after is not None
    assert gate_after.is_clean is True


def test_drift_git_mirror_commits_normalized_snapshot_to_live_branch(
    tmp_path: Path,
) -> None:
    dist_dir = tmp_path / "dist"
    compile_blueprint(
        BLUEPRINT_DIR,
        dist_dir,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_dir = dist_dir / "cymbal_retail-digital"
    lock_path = write_lockfile(bundle_dir)

    live_dir = _materialize_live_snapshot(
        bundle_dir,
        tmp_path / "live_for_mirror",
        FIXTURES_DIR / "live_volatile_overlay",
    )

    mirror_dir = tmp_path / "git_mirror"
    conflicts_dir = tmp_path / "conflicts"
    report = check_drift(
        "cymbal_retail-digital",
        dist_dir,
        live_dir,
        lockfile_path=lock_path,
        conflicts_dir=conflicts_dir,
        git_mirror=mirror_dir,
    )
    assert report.git_mirror_commit
    head_proc = subprocess.run(
        [
            "git",
            "-C",
            str(mirror_dir),
            "rev-parse",
            "--verify",
            "live/cymbal_retail-digital",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert head_proc.stdout.strip() == report.git_mirror_commit

    remote_proc = subprocess.run(
        ["git", "-C", str(mirror_dir), "remote", "-v"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert remote_proc.stdout == ""

    tracked_proc = subprocess.run(
        [
            "git",
            "-C",
            str(mirror_dir),
            "ls-tree",
            "-r",
            "--name-only",
            "live/cymbal_retail-digital",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    tracked_files = [
        line.strip()
        for line in tracked_proc.stdout.splitlines()
        if line.strip()
    ]
    assert tracked_files
    for rel_file in tracked_files:
        blob_proc = subprocess.run(
            [
                "git",
                "-C",
                str(mirror_dir),
                "show",
                f"live/cymbal_retail-digital:{rel_file}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        for volatile_key in ("etag", "createTime", "updateTime", "uuid"):
            assert f'"{volatile_key}"' not in blob_proc.stdout

    cli_mirror_dir = tmp_path / "git_mirror_cli"
    rc = _run_cxas_cli(
        [
            "drift",
            "--app",
            "cymbal_retail-digital",
            "--dist",
            str(dist_dir),
            "--live",
            str(live_dir),
            "--lockfile",
            str(lock_path),
            "--conflicts-dir",
            str(conflicts_dir),
            "--git-mirror",
            str(cli_mirror_dir),
        ]
    )
    assert rc == 0
    cli_branch_proc = subprocess.run(
        [
            "git",
            "-C",
            str(cli_mirror_dir),
            "rev-parse",
            "--verify",
            "live/cymbal_retail-digital",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert len(cli_branch_proc.stdout.strip()) >= 7
    cli_remote_proc = subprocess.run(
        ["git", "-C", str(cli_mirror_dir), "remote", "-v"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert cli_remote_proc.stdout == ""


# ---------------------------------------------------------------------------
# 7. Project-Owner Enhancements: Scalar Mirrors, Rollback, Diffs & Packaging
# ---------------------------------------------------------------------------


def test_scalar_mirror_variables_and_paired_transfer_rules(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "dist"
    results = compile_blueprint(BLUEPRINT_DIR, out_dir)
    assert {r.app_id for r in results} == EXPECTED_BUNDLES

    expected_object_vars = {
        "session_ctx",
        "auth_ctx",
        "cards_ctx",
        "transfer_ctx",
        "consent_ctx",
    }

    for app_id in sorted(EXPECTED_BUNDLES):
        bundle = out_dir / app_id
        app_raw_text = (bundle / "app.json").read_text(encoding="utf-8")
        app_json = json.loads(app_raw_text)
        var_decls = app_json.get("variableDeclarations", [])
        var_types = {
            decl["name"]: str(decl.get("schema", decl).get("type", "")).upper()
            for decl in var_decls
            if isinstance(decl, dict) and "name" in decl
        }

        for obj_var in expected_object_vars:
            assert var_types.get(obj_var) == "OBJECT", (
                f"{app_id}: missing or wrong type for OBJECT variable {obj_var}"
            )

        assert var_types.get("surface_id") == "STRING"
        assert var_types.get("segment") == "STRING"

        expected_specialists = (
            ALL_SPECIALISTS
            if app_id.startswith("cymbal_retail-")
            else CYMBAL_DIRECT_SURVIVING_SPECIALISTS
        )
        expected_disabled_mirrors = {
            f"disabled_agent_{spec}" for spec in expected_specialists
        }
        actual_disabled_mirrors = {
            name for name in var_types if name.startswith("disabled_agent_")
        }
        assert actual_disabled_mirrors == expected_disabled_mirrors
        assert len(actual_disabled_mirrors) == (
            6 if app_id.startswith("cymbal_retail-") else 5
        )
        for mirror_name in actual_disabled_mirrors:
            assert var_types[mirror_name] == "BOOLEAN"

        if app_id.startswith("cymbal_direct-"):
            assert "disabled_agent_branch_information_agent" not in var_types
            assert "disabled_agent_branch_information_agent" not in app_raw_text

        root_json = _read_json(
            bundle / "agents" / "root_agent" / "root_agent.json"
        )
        rule_pairs = _extract_exact_transfer_rule_pairs(root_json)
        assert rule_pairs
        for child, expr in rule_pairs:
            assert "||" not in expr, (
                f"{app_id}: combined || expression forbidden on {child}: {expr}"
            )
            assert " or " not in expr.lower(), (
                f"{app_id}: combined OR expression forbidden on {child}: {expr}"
            )

        rule_set = set(rule_pairs)

        if app_id.endswith("-digital"):
            for blocked in ("credit_cards_agent", "wire_transfer_agent"):
                assert (
                    blocked,
                    "session_ctx.surface_id == 'public-web'",
                ) in rule_set
                assert (blocked, "surface_id == 'public-web'") in rule_set
        else:
            assert not any("public-web" in expr for _, expr in rule_pairs)

        assert (
            "credit_cards_agent",
            "session_ctx.segment == 'employee'",
        ) in rule_set
        assert ("credit_cards_agent", "segment == 'employee'") in rule_set

        for spec in expected_specialists:
            assert (
                spec,
                f"session_ctx.disabled_agents.{spec} == true",
            ) in rule_set
            assert (spec, f"disabled_agent_{spec} == true") in rule_set

        if app_id.startswith("cymbal_direct-"):
            assert not any(
                child == "branch_information_agent"
                or "branch_information_agent" in expr
                for child, expr in rule_pairs
            )


def test_accept_live_rejects_and_rolls_back_firewall_violating_console_edits(
    tmp_path: Path,
) -> None:
    dist_retail = tmp_path / "dist_retail"
    compile_blueprint(
        BLUEPRINT_DIR,
        dist_retail,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_retail = dist_retail / "cymbal_retail-digital"
    lock_retail = write_lockfile(bundle_retail)

    gr_rel = Path(
        "guardrails/banking_scope_llm_policy/banking_scope_llm_policy.json"
    )
    orig_gr_json = _read_json(bundle_retail / gr_rel)
    orig_pinned_model = orig_gr_json["llmPolicy"]["modelSettings"]["model"]
    assert orig_pinned_model
    orig_retail_tree = _collect_bytes_tree(bundle_retail)
    orig_retail_lock_bytes = lock_retail.read_bytes()

    live_retail_none = _materialize_live_snapshot(
        bundle_retail,
        tmp_path / "live_retail_wiped_none",
        FIXTURES_DIR / "live_volatile_overlay",
    )
    live_gr_none = _read_json(live_retail_none / gr_rel)
    live_gr_none["llmPolicy"]["modelSettings"] = None
    _write_json(live_retail_none / gr_rel, live_gr_none)

    with pytest.raises(FirewallError) as exc_retail:
        check_drift(
            "cymbal_retail-digital",
            dist_retail,
            live_retail_none,
            lockfile_path=lock_retail,
            conflicts_dir=tmp_path / "conflicts_retail_none",
            accept_live=["guardrails/banking_scope_llm_policy"],
            blueprint_dir=BLUEPRINT_DIR,
        )
    assert "FW08" in {v.rule_id for v in exc_retail.value.violations}

    rolled_back_gr = _read_json(bundle_retail / gr_rel)
    assert (
        rolled_back_gr["llmPolicy"]["modelSettings"]["model"]
        == orig_pinned_model
    )
    assert _collect_bytes_tree(bundle_retail) == orig_retail_tree
    assert lock_retail.read_bytes() == orig_retail_lock_bytes

    live_retail_removed = _materialize_live_snapshot(
        bundle_retail,
        tmp_path / "live_retail_wiped_removed",
        FIXTURES_DIR / "live_volatile_overlay",
    )
    live_gr_removed = _read_json(live_retail_removed / gr_rel)
    live_gr_removed["llmPolicy"].pop("modelSettings", None)
    _write_json(live_retail_removed / gr_rel, live_gr_removed)

    rc_retail_cli = _run_cxas_cli(
        [
            "drift",
            "--app",
            "cymbal_retail-digital",
            "--dist",
            str(dist_retail),
            "--live",
            str(live_retail_removed),
            "--lockfile",
            str(lock_retail),
            "--blueprint",
            str(BLUEPRINT_DIR),
            "--conflicts-dir",
            str(tmp_path / "conflicts_retail_cli"),
            "--accept-live",
            "guardrails/banking_scope_llm_policy",
        ]
    )
    assert rc_retail_cli != 0
    assert (
        _read_json(bundle_retail / gr_rel)["llmPolicy"]["modelSettings"][
            "model"
        ]
        == orig_pinned_model
    )
    assert _collect_bytes_tree(bundle_retail) == orig_retail_tree
    assert lock_retail.read_bytes() == orig_retail_lock_bytes

    dist_direct = tmp_path / "dist_direct"
    compile_blueprint(
        BLUEPRINT_DIR,
        dist_direct,
        brand_filter="cymbal_direct",
        modality_filter="digital",
    )
    bundle_direct = dist_direct / "cymbal_direct-digital"
    lock_direct = write_lockfile(bundle_direct)
    orig_direct_tree = _collect_bytes_tree(bundle_direct)
    orig_direct_lock_bytes = lock_direct.read_bytes()

    live_direct = _materialize_live_snapshot(
        bundle_direct,
        tmp_path / "live_direct_brand_leak",
        FIXTURES_DIR / "live_volatile_overlay",
    )
    direct_guide_instr = (
        live_direct / "agents" / "guide_agent" / "instruction.txt"
    )
    direct_guide_instr.write_text(
        direct_guide_instr.read_text(encoding="utf-8")
        + "\nFor in-person appointments, please contact Cymbal Retail Bank.\n",
        encoding="utf-8",
    )

    with pytest.raises(FirewallError) as exc_direct:
        check_drift(
            "cymbal_direct-digital",
            dist_direct,
            live_direct,
            lockfile_path=lock_direct,
            conflicts_dir=tmp_path / "conflicts_direct",
            accept_live=["agents/guide_agent"],
            blueprint_dir=BLUEPRINT_DIR,
        )
    assert "FW05" in {v.rule_id for v in exc_direct.value.violations}

    rc_direct_cli = _run_cxas_cli(
        [
            "drift",
            "--app",
            "cymbal_direct-digital",
            "--dist",
            str(dist_direct),
            "--live",
            str(live_direct),
            "--lockfile",
            str(lock_direct),
            "--blueprint",
            str(BLUEPRINT_DIR),
            "--conflicts-dir",
            str(tmp_path / "conflicts_direct_cli"),
            "--accept-live",
            "agents/guide_agent",
        ]
    )
    assert rc_direct_cli != 0
    assert "Cymbal Retail Bank" not in (
        bundle_direct / "agents" / "guide_agent" / "instruction.txt"
    ).read_text(encoding="utf-8")
    assert _collect_bytes_tree(bundle_direct) == orig_direct_tree
    assert lock_direct.read_bytes() == orig_direct_lock_bytes


def test_conflict_diff_pinpoints_exact_instruction_lines(
    tmp_path: Path,
) -> None:
    bp_copy = tmp_path / "blueprint"
    shutil.copytree(BLUEPRINT_DIR, bp_copy)

    dist_dir = tmp_path / "dist"
    compile_blueprint(
        bp_copy,
        dist_dir,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_dir = dist_dir / "cymbal_retail-digital"
    lock_path = write_lockfile(bundle_dir)

    live_dir = _materialize_live_snapshot(
        bundle_dir,
        tmp_path / "live_bullet_conflict",
        FIXTURES_DIR / "live_volatile_overlay",
    )
    live_instr_path = (
        live_dir / "agents" / "credit_cards_agent" / "instruction.txt"
    )
    orig_lines = live_instr_path.read_text(encoding="utf-8").splitlines()
    edited_lines: list[str] = []
    replaced_one_bullet = False
    for line in orig_lines:
        if not replaced_one_bullet and "block_freeze_credit_card" in line:
            edited_lines.append(
                line
                + " Always ask whether the card was lost abroad (travel cap 2,500 € / äöüß)."
            )
            replaced_one_bullet = True
        else:
            edited_lines.append(line)
    assert replaced_one_bullet
    live_instr_path.write_text("\n".join(edited_lines) + "\n", encoding="utf-8")

    overlay_yaml_path = bp_copy / "tuning_overlay.yaml"
    overlay_data = yaml.safe_load(overlay_yaml_path.read_text(encoding="utf-8"))
    overlay_rule = "Always check the statement cycle cutoff date before quoting card fee waivers (€ / äöüß)."
    overlay_data.setdefault("overlays", {}).setdefault(
        "digital", {}
    ).setdefault("credit_cards_agent", {}).setdefault(
        "append_rules", []
    ).append(overlay_rule)
    overlay_yaml_path.write_text(
        yaml.safe_dump(overlay_data, allow_unicode=True), encoding="utf-8"
    )
    compile_blueprint(
        bp_copy,
        dist_dir,
        brand_filter="cymbal_retail",
        modality_filter="digital",
        write_locks=False,
    )

    conflicts_dir = tmp_path / "conflicts"
    report = check_drift(
        "cymbal_retail-digital",
        dist_dir,
        live_dir,
        lockfile_path=lock_path,
        conflicts_dir=conflicts_dir,
    )
    assert report.by_id["agents/credit_cards_agent"].status == "CONFLICT"

    diff_path = conflicts_dir / "agents" / "credit_cards_agent.diff"
    assert diff_path.is_file()
    diff_text = diff_path.read_text(encoding="utf-8")

    assert '"_instructionLines"' in diff_text
    assert '"_instructionText"' not in diff_text
    assert "<<<<<<< live" in diff_text
    assert "=======" in diff_text
    assert ">>>>>>> blueprint" in diff_text

    outside_lines: list[str] = []
    live_conflict_lines: list[str] = []
    blueprint_conflict_lines: list[str] = []
    region: str | None = None
    for line in diff_text.splitlines():
        if line == "<<<<<<< live":
            region = "live"
        elif line == "=======":
            region = "blueprint"
        elif line == ">>>>>>> blueprint":
            region = None
        elif region == "live":
            live_conflict_lines.append(line)
        elif region == "blueprint":
            blueprint_conflict_lines.append(line)
        else:
            outside_lines.append(line)

    outside_blob = "\n".join(outside_lines)
    inside_blob = "\n".join(live_conflict_lines + blueprint_conflict_lines)

    assert '"displayName": "credit_cards_agent"' in outside_blob
    assert '"displayName": "credit_cards_agent"' not in inside_blob
    assert (
        "You handle all credit card servicing requests for Cymbal Retail Bank"
        in outside_blob
    )
    assert (
        "You handle all credit card servicing requests for Cymbal Retail Bank"
        not in inside_blob
    )
    assert "## Tools and Security Rules" in outside_blob
    assert "## Tools and Security Rules" not in inside_blob

    assert (
        "Always ask whether the card was lost abroad (travel cap 2,500 € / äöüß)."
        in "\n".join(live_conflict_lines)
    )
    assert overlay_rule in "\n".join(blueprint_conflict_lines)


def test_fw09_rejects_unlocked_app_or_parallel_tool_execution(
    tmp_path: Path,
) -> None:
    out_a = tmp_path / "dist_unlocked"
    compile_blueprint(
        BLUEPRINT_DIR,
        out_a,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_a = out_a / "cymbal_retail-digital"
    app_a_path = bundle_a / "app.json"
    app_a = _read_json(app_a_path)
    app_a["locked"] = False
    _write_json(app_a_path, app_a)

    violations_a = run_firewall(bundle_a, blueprint_dir=BLUEPRINT_DIR)
    assert "FW09" in {v.rule_id for v in violations_a}

    out_b = tmp_path / "dist_parallel"
    compile_blueprint(
        BLUEPRINT_DIR,
        out_b,
        brand_filter="cymbal_retail",
        modality_filter="digital",
    )
    bundle_b = out_b / "cymbal_retail-digital"
    app_b_path = bundle_b / "app.json"
    app_b = _read_json(app_b_path)
    app_b["toolExecutionMode"] = "PARALLEL"
    _write_json(app_b_path, app_b)

    violations_b = run_firewall(bundle_b, blueprint_dir=BLUEPRINT_DIR)
    assert "FW09" in {v.rule_id for v in violations_b}


def test_reproducible_zip_packaging_and_json_cli_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_1 = tmp_path / "dist_zip_1"
    out_2 = tmp_path / "dist_zip_2"
    compile_blueprint(BLUEPRINT_DIR, out_1, package_zips=True)
    compile_blueprint(BLUEPRINT_DIR, out_2, package_zips=True)

    zip_1 = out_1 / "cymbal_retail-voice.zip"
    zip_2 = out_2 / "cymbal_retail-voice.zip"
    assert zip_1.is_file()
    assert zip_2.is_file()

    manual_zip = package_bundle_zip(
        out_1 / "cymbal_retail-voice", tmp_path / "manual.zip"
    )
    assert manual_zip.is_file()

    with zipfile.ZipFile(zip_1, "r") as zf:
        names = zf.namelist()
        assert "cymbal_retail-voice/app.json" in names

    sha_1 = hashlib.sha256(zip_1.read_bytes()).hexdigest()
    sha_2 = hashlib.sha256(zip_2.read_bytes()).hexdigest()
    sha_manual = hashlib.sha256(manual_zip.read_bytes()).hexdigest()
    assert sha_1 == sha_2 == sha_manual

    out_cli_zip = tmp_path / "dist_cli_zip"
    capsys.readouterr()
    rc_zip = _run_cxas_cli(
        [
            "build",
            "--blueprint",
            str(BLUEPRINT_DIR),
            "--out",
            str(out_cli_zip),
            "--zip",
        ]
    )
    assert rc_zip == 0
    cli_zip_file = out_cli_zip / "cymbal_retail-voice.zip"
    assert cli_zip_file.is_file()
    assert hashlib.sha256(cli_zip_file.read_bytes()).hexdigest() == sha_1

    out_json = tmp_path / "dist_json"
    capsys.readouterr()
    rc_build_json = _run_cxas_cli(
        [
            "build",
            "--blueprint",
            str(BLUEPRINT_DIR),
            "--out",
            str(out_json),
            "--write-locks",
            "--json",
        ]
    )
    assert rc_build_json == 0
    build_payload = json.loads(capsys.readouterr().out)
    assert isinstance(build_payload, dict)
    bundles_list = (
        build_payload.get("bundles")
        or build_payload.get("apps")
        or build_payload.get("results")
    )
    assert isinstance(bundles_list, list)
    assert len(bundles_list) == 4
    assert {b["app_id"] for b in bundles_list} == EXPECTED_BUNDLES

    capsys.readouterr()
    rc_fw_json = _run_cxas_cli(
        [
            "firewall",
            "--bundle",
            str(out_json / "cymbal_retail-digital"),
            "--blueprint",
            str(BLUEPRINT_DIR),
            "--json",
        ]
    )
    assert rc_fw_json == 0
    fw_payload = json.loads(capsys.readouterr().out)
    assert isinstance(fw_payload, dict)
    assert "violations" in fw_payload
    assert fw_payload["violations"] == []

    live_clean = _materialize_live_snapshot(
        out_json / "cymbal_retail-digital",
        tmp_path / "live_json_clean",
        FIXTURES_DIR / "live_volatile_overlay",
    )
    capsys.readouterr()
    rc_drift_json = _run_cxas_cli(
        [
            "drift",
            "--app",
            "cymbal_retail-digital",
            "--dist",
            str(out_json),
            "--live",
            str(live_clean),
            "--lockfile",
            str(out_json / "cymbal_retail-digital.lock.json"),
            "--conflicts-dir",
            str(tmp_path / "conflicts_json"),
            "--json",
        ]
    )
    assert rc_drift_json == 0
    drift_payload = json.loads(capsys.readouterr().out)
    assert isinstance(drift_payload, dict)
    assert drift_payload.get("app_id") == "cymbal_retail-digital"
    assert isinstance(drift_payload.get("resources"), list)
    assert drift_payload.get("is_clean") is True


# ---------------------------------------------------------------------------
# 8. S1 & S2 Regression Tests (DataStore Name Preservation & Standalone Apps)
# ---------------------------------------------------------------------------


def test_s1_datastore_source_name_preserved_on_accept_live_and_detected_as_drift(
    tmp_path: Path,
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
    ds_rel = Path("tools/search_cc_knowledge/search_cc_knowledge.json")

    orig_tool_json = _read_json(bundle_dir / ds_rel)
    orig_ds_name = orig_tool_json["dataStoreTool"]["engineSource"][
        "dataStoreSources"
    ][0]["dataStore"]["name"]
    assert orig_ds_name.startswith("projects/")
    assert "/dataStores/" in orig_ds_name

    # Mutate ONLY dataStore.name in live without changing engineSource.engine
    live_dir = _materialize_live_snapshot(
        bundle_dir,
        tmp_path / "live_ds_mutated",
        FIXTURES_DIR / "live_volatile_overlay",
    )
    mutated_ds_name = f"{orig_ds_name}-v2"
    live_tool_json = _read_json(live_dir / ds_rel)
    live_tool_json["dataStoreTool"]["engineSource"]["dataStoreSources"][0][
        "dataStore"
    ]["name"] = mutated_ds_name
    _write_json(live_dir / ds_rel, live_tool_json)

    drift_report = check_drift(
        "cymbal_retail-digital",
        dist_dir,
        live_dir,
        lockfile_path=lock_path,
    )
    assert drift_report.by_id["tools/search_cc_knowledge"].status == "DRIFT"

    accept_report = check_drift(
        "cymbal_retail-digital",
        dist_dir,
        live_dir,
        lockfile_path=lock_path,
        accept_live=["tools/search_cc_knowledge"],
        blueprint_dir=BLUEPRINT_DIR,
    )
    assert accept_report.has_unresolved is False

    adopted_tool_json = _read_json(bundle_dir / ds_rel)
    for volatile_key in ("name", "uuid", "etag", "createTime", "updateTime"):
        assert volatile_key not in adopted_tool_json
    adopted_ds_obj = adopted_tool_json["dataStoreTool"]["engineSource"][
        "dataStoreSources"
    ][0]["dataStore"]
    assert adopted_ds_obj == {"name": mutated_ds_name}


def _create_standalone_ces_bundle(bundle_dir: Path) -> Path:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        bundle_dir / "app.json",
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
    (bundle_dir / "global_instruction.txt").write_text(
        "You are a helpful banking virtual assistant.\n", encoding="utf-8"
    )
    _write_json(
        bundle_dir / "agents" / "default_agent" / "default_agent.json",
        {
            "childAgents": ["billing_agent"],
            "description": "Entry routing agent",
            "displayName": "default_agent",
            "instruction": "agents/default_agent/instruction.txt",
            "modelSettings": {"model": "gemini-2.5-flash-001"},
            "tools": [],
        },
    )
    (bundle_dir / "agents" / "default_agent" / "instruction.txt").write_text(
        "Route billing questions to {@AGENT: billing_agent}.\n",
        encoding="utf-8",
    )
    _write_json(
        bundle_dir / "agents" / "billing_agent" / "billing_agent.json",
        {
            "description": "Billing specialist agent",
            "displayName": "billing_agent",
            "instruction": "agents/billing_agent/instruction.txt",
            "modelSettings": {"model": "gemini-2.5-flash-001"},
            "tools": [],
        },
    )
    (bundle_dir / "agents" / "billing_agent" / "instruction.txt").write_text(
        "Assist customers with statement and billing inquiries.\n",
        encoding="utf-8",
    )
    return bundle_dir


def test_s2_standalone_non_blueprint_app_with_custom_root_agent(
    tmp_path: Path,
) -> None:
    standalone_dir = _create_standalone_ces_bundle(
        tmp_path / "standalone_clean"
    )
    assert "metadata" not in _read_json(standalone_dir / "app.json")
    assert run_firewall(standalone_dir) == []

    # Oversized instruction on custom rootAgent default_agent (> 15,360 B, < 18,432 B) -> FW04
    oversized_dir = tmp_path / "standalone_oversized"
    shutil.copytree(standalone_dir, oversized_dir)
    (oversized_dir / "agents" / "default_agent" / "instruction.txt").write_text(
        "R" * 15_400 + "\n", encoding="utf-8"
    )
    oversized_rules = {v.rule_id for v in run_firewall(oversized_dir)}
    assert "FW04" in oversized_rules

    # Unpinned model ("gemini-latest") on custom rootAgent default_agent -> FW08
    unpinned_dir = tmp_path / "standalone_unpinned"
    shutil.copytree(standalone_dir, unpinned_dir)
    default_json_path = (
        unpinned_dir / "agents" / "default_agent" / "default_agent.json"
    )
    default_json = _read_json(default_json_path)
    default_json["modelSettings"] = {"model": "gemini-latest"}
    _write_json(default_json_path, default_json)
    unpinned_rules = {v.rule_id for v in run_firewall(unpinned_dir)}
    assert "FW08" in unpinned_rules

    # Unlinked specialist agent absent from default_agent.childAgents -> FW10
    unlinked_dir = tmp_path / "standalone_unlinked"
    shutil.copytree(standalone_dir, unlinked_dir)
    _write_json(
        unlinked_dir / "agents" / "unlinked_agent" / "unlinked_agent.json",
        {
            "description": "Unlinked specialist",
            "displayName": "unlinked_agent",
            "instruction": "agents/unlinked_agent/instruction.txt",
            "modelSettings": {"model": "gemini-2.5-flash-001"},
            "tools": [],
        },
    )
    (unlinked_dir / "agents" / "unlinked_agent" / "instruction.txt").write_text(
        "Unlinked specialist prompt.\n", encoding="utf-8"
    )
    unlinked_rules = {v.rule_id for v in run_firewall(unlinked_dir)}
    assert "FW10" in unlinked_rules
