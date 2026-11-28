# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Unit and CXAS SCRAPI integration tests for `.agents/skills/cxas-vector-steering`."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_DIR = REPO_ROOT / ".agents" / "skills" / "cxas-vector-steering"
SCRIPTS_DIR = SKILL_DIR / "scripts"
CF_MAIN_PATH = SKILL_DIR / "resources" / "cloud_function" / "main.py"


def _load_module(name: str, file_path: Path) -> Any:
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location(name, str(file_path))
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


config_loader = _load_module(
    "vs_config_loader", SCRIPTS_DIR / "config_loader.py"
)
analyze_dataset = _load_module(
    "vs_analyze_dataset", SCRIPTS_DIR / "analyze_dataset.py"
)
bootstrap_scrapi = _load_module(
    "vs_bootstrap_scrapi", SCRIPTS_DIR / "bootstrap_from_scrapi.py"
)
embed_and_sync = _load_module(
    "vs_embed_and_sync", SCRIPTS_DIR / "embed_and_sync.py"
)
scaffold_tools = _load_module(
    "vs_scaffold_tools", SCRIPTS_DIR / "scaffold_agent_tools.py"
)
cf_main = _load_module("vs_cf_main", CF_MAIN_PATH)


def test_starter_config_and_sample_corpus_pass_strict_audit() -> None:
    cfg = config_loader.load_vector_steering_config(
        str(
            SKILL_DIR / "resources" / "config" / "vector_steering.example.yaml"
        ),
        base_dir=str(REPO_ROOT),
    )
    catalog = config_loader.load_intents_catalog(
        str(
            SKILL_DIR / "resources" / "config" / "intents_catalog.example.yaml"
        ),
        base_dir=str(REPO_ROOT),
    )
    report = analyze_dataset.analyze_corpus(
        str(
            SKILL_DIR
            / "resources"
            / "sample_data"
            / "sample_training_phrases.csv"
        ),
        catalog=catalog,
        min_word_count=cfg.min_word_count,
        min_phrases_per_intent=cfg.min_phrases_per_intent,
    )
    assert report["critical_count"] == 0
    assert report["warning_count"] == 0
    assert report["distinct_intents"] == 13


def test_security_validators_reject_injection_inputs() -> None:
    with pytest.raises(config_loader.ConfigValidationError):
        config_loader.validate_gcp_project_id("--malicious-flag")
    with pytest.raises(config_loader.ConfigValidationError):
        config_loader.validate_gcp_location("us-central1;rm -rf")
    with pytest.raises(config_loader.ConfigValidationError):
        config_loader.validate_gcs_bucket("gs://../traversal-bucket")
    with pytest.raises(config_loader.ConfigValidationError):
        config_loader.validate_service_account("bad@cymbal.com")


def test_bootstrap_from_scrapi_harvests_pulled_cxas_app(
    tmp_path: Path,
) -> None:
    gecx_cfg = {
        "gcp_project_id": "cymbal-voice-uat",
        "location": "us",
        "app_name": "Cymbal Member Voice Agent",
        "app_dir": "cxas_app/Cymbal_Agent",
        "modality": "audio",
    }
    (tmp_path / "gecx-config.json").write_text(
        json.dumps(gecx_cfg), encoding="utf-8"
    )

    app_root = tmp_path / "cxas_app" / "Cymbal_Agent"
    (app_root / "agents" / "Root_Steering").mkdir(parents=True)
    (app_root / "agents" / "Billing_Dispute").mkdir(parents=True)
    (app_root / "agents" / "Prescription_Refill").mkdir(parents=True)

    (app_root / "app.json").write_text(
        json.dumps({"displayName": "Cymbal", "rootAgent": "Root_Steering"}),
        encoding="utf-8",
    )
    (app_root / "agents" / "Root_Steering" / "Root_Steering.json").write_text(
        json.dumps({"displayName": "Root_Steering"}), encoding="utf-8"
    )
    (
        app_root / "agents" / "Billing_Dispute" / "Billing_Dispute.json"
    ).write_text(
        json.dumps(
            {
                "displayName": "Billing_Dispute",
                "description": "Disputes duplicate statement charges.",
            }
        ),
        encoding="utf-8",
    )
    (
        app_root / "agents" / "Prescription_Refill" / "Prescription_Refill.json"
    ).write_text(
        json.dumps(
            {
                "displayName": "Prescription_Refill",
                "description": "Refills routine pharmacy prescriptions.",
            }
        ),
        encoding="utf-8",
    )

    outputs = bootstrap_scrapi.bootstrap_workspace(
        workspace_root=str(tmp_path),
        output_dir="vector_steering",
    )
    cfg = config_loader.load_vector_steering_config(outputs["config"])
    catalog = config_loader.load_intents_catalog(outputs["catalog"])
    assert cfg.environments["dev"].project_id == "cymbal-voice-uat"
    assert set(catalog.keys()) == {"billing_dispute", "prescription_refill"}

    audit = analyze_dataset.analyze_corpus(
        outputs["corpus_csv"], catalog=catalog
    )
    assert audit["critical_count"] == 0


def test_analyzer_catches_collisions_pii_and_entity_leaks(
    tmp_path: Path,
) -> None:
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text(
        "intent,phrase\n"
        "cancel_order,Cancel my order right now\n"
        "modify_order,Cancel my order right now!\n"
        "cancel_order,Contact user@cymbal.com with SSN 123-45-6789 for glp1_brand\n",
        encoding="utf-8",
    )
    report = analyze_dataset.analyze_corpus(
        str(bad_csv),
        entity_mask_patterns=[r"\bglp1_brand\b"],
    )
    assert len(report["conflicts"]) == 1
    assert len(report["pii_leaks"]) == 1
    assert len(report["unmasked_entities"]) == 1


def test_cloud_function_http_security_and_tie_gating() -> None:
    class _MockReq:
        def __init__(self, method: str, body: Any) -> None:
            self.method = method
            self.headers: dict[str, str] = {}
            self.args: dict[str, str] = {}
            self.content_length = 64
            self._body = body

        def get_json(self, silent: bool = True) -> Any:  # noqa: ARG002
            return self._body

    _, status_get, _ = cf_main.classify_intent(_MockReq("GET", {}))
    assert status_get == 405

    neighbors = [
        SimpleNamespace(id="check_credit_balance:::1", distance=0.216),
        SimpleNamespace(id="check_gift_card_balance:::2", distance=0.233),
    ]
    res = cf_main.evaluate_neighbors(
        neighbors,
        similarity_threshold=0.75,
        disambiguation_delta=0.08,
    )
    assert res["requires_disambiguation"] is True
    assert res["intent"] == "unknown"
    assert res["disambiguation_delta"] == pytest.approx(0.017, abs=1e-4)


def test_proactive_vector_advisor_warns_on_small_tree_ah(
    tmp_path: Path,
) -> None:
    cfg_file = tmp_path / "vs.yaml"
    cfg_file.write_text(
        "version: '1.0'\n"
        "embedding:\n"
        "  model: 'text-embedding-005'\n"
        "  task_type: 'RETRIEVAL_QUERY'\n"
        "environments:\n"
        "  dev:\n"
        "    project_id: 'cymbal-dev-01'\n"
        "    location: 'us-central1'\n"
        "    bucket_name: 'cymbal-dev-bucket'\n"
        "    index_algorithm: 'TREE_AH'\n"
        "    leaf_node_embedding_count: 1000\n"
        "    leaf_nodes_to_search_percent: 10\n",
        encoding="utf-8",
    )
    cfg = config_loader.load_vector_steering_config(str(cfg_file))
    nudges = config_loader.advise_vector_configuration(
        cfg, "dev", corpus_size=1200
    )
    joined = "\n".join(nudges)
    assert "SEMANTIC_SIMILARITY" in joined
    assert "BRUTE_FORCE" in joined
    assert "Tree-AH Recall" in joined


def test_scaffolded_cxas_app_executes_and_passes_cxas_lint(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "Sample_CXAS_App"
    (app_dir / "agents" / "Steering_Agent").mkdir(parents=True)
    (app_dir / "agents" / "Order_Status").mkdir(parents=True)

    app_json = {
        "name": "00000000-0000-4000-8000-000000000001",
        "displayName": "Sample_CXAS_App",
        "rootAgent": "Steering_Agent",
        "timeZoneSettings": {"timeZone": "America/New_York"},
        "languageSettings": {
            "defaultLanguageCode": "en-US",
            "supportedLanguageCodes": ["en-US"],
        },
        "modelSettings": {"model": "gemini-2.5-flash"},
        "variableDeclarations": [],
    }
    (app_dir / "app.json").write_text(
        json.dumps(app_json, indent=2), encoding="utf-8"
    )

    steering_json = {
        "name": "00000000-0000-4000-8000-000000000002",
        "displayName": "Steering_Agent",
        "instruction": "agents/Steering_Agent/instruction.txt",
        "childAgents": ["Order_Status"],
        "tools": ["end_session"],
    }
    (app_dir / "agents" / "Steering_Agent" / "Steering_Agent.json").write_text(
        json.dumps(steering_json, indent=2), encoding="utf-8"
    )

    order_json = {
        "name": "00000000-0000-4000-8000-000000000003",
        "displayName": "Order_Status",
        "description": "Tracks customer order delivery status.",
        "instruction": "agents/Order_Status/instruction.txt",
    }
    (app_dir / "agents" / "Order_Status" / "Order_Status.json").write_text(
        json.dumps(order_json, indent=2), encoding="utf-8"
    )
    (app_dir / "agents" / "Order_Status" / "instruction.txt").write_text(
        "<role>Help the customer check order delivery status.</role>\n",
        encoding="utf-8",
    )

    cfg = config_loader.load_vector_steering_config(
        str(
            SKILL_DIR / "resources" / "config" / "vector_steering.example.yaml"
        ),
        base_dir=str(REPO_ROOT),
    )
    catalog = config_loader.load_intents_catalog(
        str(
            SKILL_DIR / "resources" / "config" / "intents_catalog.example.yaml"
        ),
        base_dir=str(REPO_ROOT),
    )

    outputs = scaffold_tools.scaffold_bundle(
        cfg=cfg,
        catalog=catalog,
        output_dir=str(app_dir),
        env_key="dev",
        modality="voice",
        target_platform="gecx",
        merge_app_vars=True,
        sync_env_json=True,
        wire_steering_agent="Steering_Agent",
    )

    (app_dir / "agents" / "Steering_Agent" / "instruction.txt").write_text(
        Path(outputs["steering_instruction"]).read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    mock_vars: dict[str, Any] = {"tenant_id": "standard"}
    mock_context = SimpleNamespace(
        variables=mock_vars, agent_name="Steering_Agent"
    )
    mock_tools = SimpleNamespace(
        vector_intent_router_classify_intent=lambda payload: {  # noqa: ARG005
            "intent": "vip_concierge_support",
            "confidence_score": 0.91,
            "is_match_found": True,
            "requires_disambiguation": False,
            "top_matches": [
                {"intent": "vip_concierge_support", "confidence": 0.91},
                {"intent": "order_status", "confidence": 0.82},
            ],
        }
    )

    cls_ns: dict[str, Any] = {"context": mock_context, "tools": mock_tools}
    exec(  # noqa: S102
        Path(outputs["classifier_tool_code"]).read_text(encoding="utf-8"),
        cls_ns,
    )
    cls_res = cls_ns["intent_classifier"]("Where is my package")
    assert cls_res["intent"] == "order_status"
    assert cls_res["processed_action"] == "INTENT_SET"

    upd_ns: dict[str, Any] = {"context": mock_context}
    exec(  # noqa: S102
        Path(outputs["update_intent_tool_code"]).read_text(encoding="utf-8"),
        upd_ns,
    )
    blocked = upd_ns["update_intent"]("hallucinated_intent_name")
    assert blocked["status"] == "rejected"
    accepted = upd_ns["update_intent"]("order_status")
    assert accepted["status"] == "success"
    assert mock_vars["Intent"] == "order_status"

    adk_out = scaffold_tools.scaffold_bundle(
        cfg=cfg,
        catalog=catalog,
        output_dir=str(tmp_path / "adk_bundle"),
        target_platform="adk",
    )
    assert Path(adk_out["adk_tools_module"]).is_file()

    lint_proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "cxas_scrapi.cli.main",
            "lint",
            "--validate-only",
            "--app-dir",
            str(app_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert lint_proc.returncode == 0, (
        f"cxas lint failed:\nSTDOUT:\n{lint_proc.stdout}\nSTDERR:\n{lint_proc.stderr}"
    )


def test_skill_markdown_links_resolve() -> None:
    link_re = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
    md_files = list(SKILL_DIR.rglob("*.md"))
    assert md_files
    for md_file in md_files:
        text = md_file.read_text(encoding="utf-8")
        for match in link_re.finditer(text):
            raw_target = match.group(1).split("#", 1)[0].strip()
            if not raw_target or raw_target.startswith(("http://", "https://")):
                continue
            resolved = (md_file.parent / raw_target).resolve()
            assert resolved.exists(), (
                f"Broken link '{raw_target}' in {md_file} -> {resolved}"
            )
