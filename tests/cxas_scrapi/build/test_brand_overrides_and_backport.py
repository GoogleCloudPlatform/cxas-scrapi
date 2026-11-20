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

"""Hermetic tests for Rung 2 brand/channel overrides and Console back-port."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
import yaml

from cxas_scrapi.build import (
    BuildError,
    BuildGateError,
    DriftGateError,
    ManifestError,
    backport_live_to_source,
    build_all,
    build_app,
    check_drift,
    extract_normalized_resources,
    load_manifests,
)
from cxas_scrapi.cli import main as cli_main
from cxas_scrapi.core.python_bundler import bundle_shared_python
from cxas_scrapi.utils.linter import collect_lint_report

GA_AGENT_MODEL = "gemini-3.5-flash"
GA_LITE_MODEL = "gemini-3.5-flash-lite"


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha_map(bundle_dir: Path) -> dict[str, str]:
    return {
        res_id: res.sha256
        for res_id, res in extract_normalized_resources(bundle_dir).items()
    }


def _snapshot_tree_bytes(root: Path) -> dict[str, bytes]:
    snap: dict[str, bytes] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            snap[p.relative_to(root).as_posix()] = p.read_bytes()
    return snap


def _init_git_repo(repo_dir: Path, branch: str = "main") -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Cymbal Bot",
        "GIT_AUTHOR_EMAIL": "bot@cymbal.example",
        "GIT_COMMITTER_NAME": "Cymbal Bot",
        "GIT_COMMITTER_EMAIL": "bot@cymbal.example",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    subprocess.run(
        ["git", "-C", str(repo_dir), "init", "-b", branch],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "add", "."],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "commit", "-m", "Initial Rung 1 source"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _git_current_branch(repo_dir: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_dir), "symbolic-ref", "--short", "HEAD"],
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _make_multi_brand_source(root: Path) -> tuple[Path, Path]:
    """Builds a Cymbal-only 2-brand x 2-channel Rung 1 source tree."""
    core = root / "core"
    _write(
        core / "python_lib/cymbal_helpers.py",
        '"""Shared Cymbal card formatting helpers."""\n\n\n'
        "def format_card_badge(brand_label: str, tier: str) -> str:\n"
        '    """Formats a normalized card tier badge."""\n'
        '    return f"{brand_label}:{tier.upper()}"\n',
    )
    _write(
        core / "app.json",
        {
            "name": "@@brand.brand_id@@-assistant",
            "displayName": "@@brand.display_name@@ Assistant",
            "rootAgent": "root_agent",
            "modelSettings": {"model": GA_AGENT_MODEL},
            "guardrails": ["scope_policy"],
        },
    )
    _write(
        core / "global_instruction.txt",
        "You represent @@brand.display_name@@ across every conversation.\n"
        "Disclosure: @@brand.lexicon.ai_disclosure@@\n"
        "Verify sensitive actions with @@brand.lexicon.auth_method@@ or via "
        "@@brand.urls.portal@@.\n",
    )
    _write(
        core / "agents/root_agent/root_agent.json",
        {
            "displayName": "root_agent",
            "description": "Primary router for @@brand.display_name@@.",
            "instruction": "agents/root_agent/instruction.txt",
            "modelSettings": {"model": GA_AGENT_MODEL},
            "childAgents": ["branch_agent", "cards_agent", "guide_agent"],
            "tools": ["end_session"],
        },
    )
    _write(
        core / "agents/root_agent/instruction.txt",
        "{@startAgentEnabled_branch_agent}\n"
        "Route branch locator questions to {@AGENT: branch_agent}.\n"
        "{@endAgentEnabled_branch_agent}\n"
        "Route card servicing and limit requests to {@AGENT: cards_agent}.\n"
        "Route general product questions to {@AGENT: guide_agent}.\n",
    )
    _write(
        core / "agents/branch_agent/branch_agent.json",
        {
            "displayName": "branch_agent",
            "description": (
                "Branch locator specialist for @@brand.display_name@@."
            ),
            "instruction": "agents/branch_agent/instruction.txt",
            "modelSettings": {"model": GA_AGENT_MODEL},
            "tools": ["find_branches"],
        },
    )
    _write(
        core / "agents/branch_agent/instruction.txt",
        "Locate nearby @@brand.display_name@@ branches using "
        "{@TOOL: find_branches}.\n",
    )
    _write(
        core / "agents/cards_agent/cards_agent.json",
        {
            "displayName": "cards_agent",
            "description": (
                "Card servicing specialist for @@brand.display_name@@."
            ),
            "instruction": "agents/cards_agent/instruction.txt",
            "modelSettings": {"model": GA_AGENT_MODEL, "temperature": 0.2},
            "tools": ["update_card_limit", "format_card_summary"],
        },
    )
    _write(
        core / "agents/cards_agent/instruction.txt",
        "Assist @@brand.display_name@@ cardholders with card controls.\n"
        "Confirm identity with @@brand.lexicon.auth_method@@ before calling "
        "{@TOOL: update_card_limit}.\n"
        "Summarize card tiers with {@TOOL: format_card_summary} and share "
        "self-service guidance on @@brand.urls.portal@@.\n",
    )
    _write(
        core / "agents/guide_agent/guide_agent.json",
        {
            "displayName": "guide_agent",
            "description": "General banking guide for @@brand.display_name@@.",
            "instruction": "agents/guide_agent/instruction.txt",
            "modelSettings": {"model": GA_AGENT_MODEL},
            "tools": ["kb_search"],
        },
    )
    _write(
        core / "agents/guide_agent/instruction.txt",
        "Answer @@brand.display_name@@ banking questions using "
        "{@TOOL: kb_search}.\n",
    )
    _write(
        core / "tools/find_branches/find_branches.json",
        {
            "displayName": "find_branches",
            "openApiTool": {
                "name": "find_branches",
                "description": "Find @@brand.display_name@@ branches.",
                "url": "@@routing.branches_api@@/v1/branches",
                "openApiSchema": (
                    "tools/find_branches/open_api_tool/open_api_schema.yaml"
                ),
            },
        },
    )
    _write(
        core / "tools/find_branches/open_api_tool/open_api_schema.yaml",
        "openapi: 3.0.0\n"
        "info:\n"
        "  title: Cymbal Branches API\n"
        "  version: '1.0'\n"
        "servers:\n"
        "  - url: @@routing.branches_api@@\n"
        "paths:\n"
        "  /v1/branches:\n"
        "    get:\n"
        "      operationId: listBranches\n"
        "      responses:\n"
        "        '200':\n"
        "          description: Branch list\n",
    )
    _write(
        core / "tools/update_card_limit/update_card_limit.json",
        {
            "displayName": "update_card_limit",
            "openApiTool": {
                "name": "update_card_limit",
                "description": (
                    "Update @@brand.display_name@@ card spending limit."
                ),
                "url": "@@routing.cards_api@@/v1/cards/limit",
                "openApiSchema": (
                    "tools/update_card_limit/open_api_tool/open_api_schema.yaml"
                ),
            },
        },
    )
    _write(
        core / "tools/update_card_limit/open_api_tool/open_api_schema.yaml",
        "openapi: 3.0.0\n"
        "info:\n"
        "  title: Cymbal Cards API\n"
        "  version: '1.0'\n"
        "servers:\n"
        "  - url: @@routing.cards_api@@\n"
        "paths:\n"
        "  /v1/cards/limit:\n"
        "    post:\n"
        "      operationId: updateCardLimit\n"
        "      responses:\n"
        "        '200':\n"
        "          description: Card limit updated\n"
        "  /v2/cards/limit:\n"
        "    post:\n"
        "      operationId: updateCardLimitV2\n"
        "      responses:\n"
        "        '200':\n"
        "          description: Card limit updated v2\n",
    )
    _write(
        core / "tools/format_card_summary/format_card_summary.json",
        {
            "displayName": "format_card_summary",
            "pythonFunction": {
                "name": "format_card_summary",
                "description": (
                    "Format a @@brand.display_name@@ card tier summary."
                ),
                "pythonCode": (
                    "tools/format_card_summary/python_function/python_code.py"
                ),
            },
        },
    )
    _write(
        core / "tools/format_card_summary/python_function/python_code.py",
        '"""Card summary helper for @@brand.display_name@@."""\n\n'
        "from python_lib.cymbal_helpers import format_card_badge\n\n\n"
        "def format_card_summary(card_tier: str) -> dict[str, str]:\n"
        '    """Return a concise badge and portal link for the card tier."""\n'
        "    return {\n"
        '        "badge": format_card_badge('
        '"@@brand.display_name@@", card_tier),\n'
        '        "portal": "@@brand.urls.portal@@",\n'
        "    }\n",
    )
    _write(
        core / "tools/kb_search/kb_search.json",
        {
            "displayName": "kb_search",
            "dataStoreTool": {
                "name": "kb_search",
                "description": "Search @@brand.display_name@@ help articles.",
                "modalityConfigs": [
                    {
                        "modalityType": "TEXT",
                        "rewriterConfig": {
                            "modelSettings": {"model": GA_LITE_MODEL}
                        },
                        "summarizationConfig": {
                            "modelSettings": {"model": GA_LITE_MODEL}
                        },
                    }
                ],
            },
        },
    )
    _write(
        core / "guardrails/scope_policy/scope_policy.json",
        {
            "displayName": "scope_policy",
            "enabled": True,
            "llmPolicy": {
                "prompt": "Allow only @@brand.display_name@@ banking topics.",
                "modelSettings": {"model": GA_LITE_MODEL},
            },
        },
    )

    man = root / "manifests"
    _write(
        man / "brands/cymbal_retail/identity.yaml",
        "brand_id: cymbal_retail\n"
        "display_name: Cymbal Retail\n"
        "legal_entity: Cymbal Retail Banking Corp.\n"
        "lexicon:\n"
        "  ai_disclosure: Welcome to Cymbal Retail automated banking.\n"
        "  auth_method: Cymbal Pass\n"
        "urls:\n"
        "  portal: https://portal.cymbal-retail.example\n"
        "tts_voices:\n"
        "  en-US:\n"
        "    voice: en-US-Chirp3-HD-Aoede\n"
        "    speaking_rate: 1.0\n"
        "forbidden_tokens:\n"
        "  - Cymbal Direct Key\n",
    )
    _write(
        man / "brands/cymbal_retail/channel_policy.yaml",
        "brand_id: cymbal_retail\n"
        "api_routing:\n"
        "  branches_api: https://branches.cymbal-retail.example\n"
        "  cards_api: https://cards.cymbal-retail.example\n"
        "channels:\n"
        "  DIGITAL_APP:\n"
        "    supported_modalities: ['TEXT']\n"
        "  VOICE_TELEPHONY:\n"
        "    supported_modalities: ['AUDIO']\n",
    )
    _write(
        man / "brands/cymbal_direct/identity.yaml",
        "brand_id: cymbal_direct\n"
        "display_name: Cymbal Direct\n"
        "legal_entity: Cymbal Direct Digital Corp.\n"
        "lexicon:\n"
        "  ai_disclosure: Welcome to Cymbal Direct automated banking.\n"
        "  auth_method: Cymbal Pass\n"
        "urls:\n"
        "  portal: https://portal.cymbal-direct.example\n"
        "tts_voices:\n"
        "  en-US:\n"
        "    voice: en-US-Chirp3-HD-Puck\n"
        "    speaking_rate: 1.0\n"
        "forbidden_tokens:\n"
        "  - Cymbal Branch Pass\n",
    )
    _write(
        man / "brands/cymbal_direct/channel_policy.yaml",
        "brand_id: cymbal_direct\n"
        "api_routing:\n"
        "  branches_api: https://branches.cymbal-direct.example\n"
        "  cards_api: https://cards.cymbal-direct.example\n"
        "channels:\n"
        "  DIGITAL_APP:\n"
        "    disabled_agents:\n"
        "      - branch_agent\n"
        "    disabled_tools:\n"
        "      - find_branches\n"
        "    supported_modalities: ['TEXT']\n"
        "  VOICE_TELEPHONY:\n"
        "    disabled_agents:\n"
        "      - branch_agent\n"
        "    disabled_tools:\n"
        "      - find_branches\n"
        "    supported_modalities: ['AUDIO']\n",
    )
    _write(
        man / "envs/prod.yaml",
        "environment: prod\n"
        "locked: true\n"
        "models:\n"
        f"  agent: {GA_AGENT_MODEL}\n"
        f"  guardrail: {GA_LITE_MODEL}\n"
        f"  data_store: {GA_LITE_MODEL}\n",
    )
    return core, man


def _materialize_real_ces_export(bundle_dir: Path, live_dir: Path) -> Path:
    """Transforms a compiled bundle into a realistic CES export snapshot."""
    if live_dir.exists():
        shutil.rmtree(live_dir)
    shutil.copytree(bundle_dir, live_dir)

    dep_dir = live_dir / "deployments"
    if dep_dir.exists():
        shutil.rmtree(dep_dir)

    app_data = _read_json(live_dir / "app.json")
    app_data.pop("locked", None)
    orig_disp = str(app_data.get("displayName") or "Cymbal Assistant")
    app_data["displayName"] = f"{orig_disp} Prod Console Copy"
    app_data["etag"] = "etag-cymbal-live-001"
    app_data["updateTime"] = "2026-11-19T12:00:00Z"
    app_data["loggingSettings"] = {
        "conversationLoggingSettings": {"retentionWindow": "31536000s"}
    }
    _write(live_dir / "app.json", app_data)

    env_tools: dict[str, Any] = {}
    tools_root = live_dir / "tools"
    if tools_root.is_dir():
        for t_dir in sorted(tools_root.iterdir()):
            if not t_dir.is_dir():
                continue
            tj_path = t_dir / f"{t_dir.name}.json"
            if not tj_path.is_file():
                continue
            tj = _read_json(tj_path)
            oa = tj.get("openApiTool")
            if isinstance(oa, dict) and isinstance(oa.get("url"), str):
                concrete_url = oa.pop("url")
                _write(tj_path, tj)
                d_name = str(tj.get("displayName") or t_dir.name)
                env_tools[d_name] = {"openApiTool": {"url": concrete_url}}
                oa_yaml = t_dir / "open_api_tool/open_api_schema.yaml"
                if oa_yaml.is_file():
                    rewritten = [
                        "  - url: $env_var"
                        if ln.strip().startswith("- url:")
                        else ln
                        for ln in oa_yaml.read_text(
                            encoding="utf-8"
                        ).splitlines()
                    ]
                    oa_yaml.write_text(
                        "\n".join(rewritten) + "\n", encoding="utf-8"
                    )
    if env_tools:
        _write(live_dir / "environment.json", {"tools": env_tools})

    return live_dir


# ---------------------------------------------------------------------------
# 1. Direct YAML Per-Brand & Per-Channel Overrides (`overrides.yaml`)
# ---------------------------------------------------------------------------


def test_baseline_multi_brand_source_compiles_and_passes_lint_cleanly(
    tmp_path: Path,
) -> None:
    core, man = _make_multi_brand_source(tmp_path / "repo")
    dist = tmp_path / "dist"
    reports = build_all(core, man, dist, "prod", write_locks=True)
    assert len(reports) == 4

    for rep in reports:
        lint_rep = collect_lint_report(
            rep.out_dir,
            manifests_dir=man,
            brand=rep.brand_id,
            channel=rep.channel,
        )
        assert lint_rep.errors == []

    assert (dist / "cymbal_retail/digital_app/agents/branch_agent").is_dir()
    assert not (dist / "cymbal_direct/digital_app/agents/branch_agent").exists()


def test_direct_yaml_brand_and_channel_overrides_preserve_sister_brand_hashes(
    tmp_path: Path,
) -> None:
    core, man = _make_multi_brand_source(tmp_path / "repo")
    base_dist = tmp_path / "base_dist"
    build_all(core, man, base_dist, "prod")
    retail_dig_before = _sha_map(base_dist / "cymbal_retail/digital_app")
    retail_voice_before = _sha_map(base_dist / "cymbal_retail/voice_telephony")

    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "global_instruction:\n"
        "  replace_lines:\n"
        "    - match: Verify sensitive actions with "
        "@@brand.lexicon.auth_method@@ or via @@brand.urls.portal@@.\n"
        "      with: Require @@brand.lexicon.auth_method@@ approval for all "
        "@@brand.display_name@@ account changes.\n"
        "  append_rules:\n"
        "    - Keep @@brand.display_name@@ answers concise and structured.\n"
        "  channels:\n"
        "    DIGITAL_APP:\n"
        "      append_rules:\n"
        "        - Surface self-service links from @@brand.urls.portal@@.\n"
        "agents:\n"
        "  branch_agent:\n"
        "    append_rules:\n"
        "      - Brand-level note skipped cleanly when branch_agent is pruned.\n"
        "  cards_agent:\n"
        "    description: Brand-overridden @@brand.display_name@@ cards agent.\n"
        "    temperature: 0.3\n"
        f"    model: {GA_LITE_MODEL}\n"
        "    replace_lines:\n"
        "      - match: Assist @@brand.display_name@@ cardholders with card "
        "controls.\n"
        "        with: Support @@brand.display_name@@ cardholders using "
        "@@brand.lexicon.auth_method@@.\n"
        "    append_rules:\n"
        "      - Log every card limit request for @@brand.display_name@@.\n"
        "    channels:\n"
        "      DIGITAL_APP:\n"
        "        description: Digital @@brand.display_name@@ cards specialist.\n"
        "        temperature: 0.45\n"
        "        append_rules:\n"
        "          - Direct digital users to @@brand.urls.portal@@.\n"
        "tools:\n"
        "  update_card_limit:\n"
        "    description: Brand override for @@brand.display_name@@ limits.\n"
        "    url: '@@routing.cards_api@@/v2/cards/limit'\n"
        "    channels:\n"
        "      DIGITAL_APP:\n"
        "        description: Digital @@brand.display_name@@ limit endpoint.\n"
        "        url: '@@brand.urls.portal@@/v2/cards/limit'\n",
    )

    ov_dist = tmp_path / "ov_dist"
    build_all(core, man, ov_dist, "prod")

    # Sister brand bundles have 0 SHA-256 changes.
    assert _sha_map(ov_dist / "cymbal_retail/digital_app") == retail_dig_before
    assert (
        _sha_map(ov_dist / "cymbal_retail/voice_telephony")
        == retail_voice_before
    )

    # Digital channel receives brand + DIGITAL_APP overrides while keeping
    # `# Brand: Cymbal Direct` header.
    dig_dir = ov_dist / "cymbal_direct/digital_app"
    dig_gi = (dig_dir / "global_instruction.txt").read_text(encoding="utf-8")
    assert dig_gi.startswith("# Brand: Cymbal Direct\n")
    assert (
        "Require Cymbal Pass approval for all Cymbal Direct account changes."
        in dig_gi
    )
    assert "Keep Cymbal Direct answers concise and structured." in dig_gi
    assert (
        "Surface self-service links from https://portal.cymbal-direct.example."
        in dig_gi
    )

    dig_cards = _read_json(dig_dir / "agents/cards_agent/cards_agent.json")
    assert dig_cards["description"] == "Digital Cymbal Direct cards specialist."
    assert dig_cards["modelSettings"]["model"] == GA_LITE_MODEL
    assert dig_cards["modelSettings"]["temperature"] == pytest.approx(0.45)

    dig_cards_in = (dig_dir / "agents/cards_agent/instruction.txt").read_text(
        encoding="utf-8"
    )
    assert (
        "Support Cymbal Direct cardholders using Cymbal Pass." in dig_cards_in
    )
    assert "Log every card limit request for Cymbal Direct." in dig_cards_in
    assert (
        "Direct digital users to https://portal.cymbal-direct.example."
        in dig_cards_in
    )

    dig_tool = _read_json(
        dig_dir / "tools/update_card_limit/update_card_limit.json"
    )
    assert (
        dig_tool["openApiTool"]["description"]
        == "Digital Cymbal Direct limit endpoint."
    )
    assert (
        dig_tool["openApiTool"]["url"]
        == "https://portal.cymbal-direct.example/v2/cards/limit"
    )
    dig_oa = (
        dig_dir / "tools/update_card_limit/open_api_tool/open_api_schema.yaml"
    ).read_text(encoding="utf-8")
    assert "- url: https://portal.cymbal-direct.example" in dig_oa

    # Voice channel receives brand-level overrides only (not DIGITAL_APP).
    voice_dir = ov_dist / "cymbal_direct/voice_telephony"
    voice_gi = (voice_dir / "global_instruction.txt").read_text(
        encoding="utf-8"
    )
    assert "Surface self-service links" not in voice_gi
    voice_cards = _read_json(voice_dir / "agents/cards_agent/cards_agent.json")
    assert (
        voice_cards["description"]
        == "Brand-overridden Cymbal Direct cards agent."
    )
    assert voice_cards["modelSettings"]["temperature"] == pytest.approx(0.3)
    voice_tool = _read_json(
        voice_dir / "tools/update_card_limit/update_card_limit.json"
    )
    assert (
        voice_tool["openApiTool"]["url"]
        == "https://cards.cymbal-direct.example/v2/cards/limit"
    )

    lint_dig = collect_lint_report(
        dig_dir,
        manifests_dir=man,
        brand="cymbal_direct",
        channel="DIGITAL_APP",
    )
    assert lint_dig.errors == []


def test_explicit_channel_override_on_disabled_agent_fails_closed(
    tmp_path: Path,
) -> None:
    core, man = _make_multi_brand_source(tmp_path / "repo")
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "agents:\n"
        "  branch_agent:\n"
        "    channels:\n"
        "      DIGITAL_APP:\n"
        "        append_rules:\n"
        "          - Cannot target disabled branch_agent on DIGITAL_APP.\n",
    )
    with pytest.raises(BuildError, match="disabled agent 'branch_agent'"):
        build_app(
            core,
            man,
            "cymbal_direct",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out",
        )


# ---------------------------------------------------------------------------
# 2. Fail-Closed Validation (`ManifestError` / `BuildError`)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("yaml_body", "expected_match"),
    [
        (
            "brand_id: cymbal_direct\n"
            "agents:\n"
            "  nonexistent_agent:\n"
            "    description: Missing from core.\n",
            "unknown agent 'nonexistent_agent'",
        ),
        (
            "brand_id: cymbal_direct\n"
            "tools:\n"
            "  nonexistent_tool:\n"
            "    description: Missing from core.\n",
            "unknown tool 'nonexistent_tool'",
        ),
    ],
)
def test_fail_closed_unknown_agent_or_tool_in_overrides(
    tmp_path: Path, yaml_body: str, expected_match: str
) -> None:
    core, man = _make_multi_brand_source(tmp_path / "repo")
    _write(man / "brands/cymbal_direct/overrides.yaml", yaml_body)
    with pytest.raises(BuildError, match=expected_match):
        build_app(
            core,
            man,
            "cymbal_direct",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out",
        )


def test_fail_closed_unpermitted_channel_and_brand_id_mismatch(
    tmp_path: Path,
) -> None:
    core, man = _make_multi_brand_source(tmp_path / "repo")

    # 1. Completely unknown channel key rejected by Pydantic manifest schema.
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "agents:\n"
        "  cards_agent:\n"
        "    channels:\n"
        "      SMS_UNSUPPORTED:\n"
        "        description: Invalid channel enum.\n",
    )
    with pytest.raises(ManifestError, match="unknown channel"):
        load_manifests(man)

    # 2. Known CES channel not enabled in channel_policy.yaml rejected at build.
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "agents:\n"
        "  cards_agent:\n"
        "    channels:\n"
        "      WHATSAPP:\n"
        "        description: Not permitted in cymbal_direct policy.\n",
    )
    with pytest.raises(BuildError, match=r"not declared in channel_policy"):
        build_app(
            core,
            man,
            "cymbal_direct",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out",
        )

    # 3. brand_id mismatch against parent folder name rejected by loader.
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_retail\n",
    )
    with pytest.raises(ManifestError, match="does not match directory name"):
        load_manifests(man)


def test_fail_closed_replace_lines_zero_and_ambiguous_matches(
    tmp_path: Path,
) -> None:
    core, man = _make_multi_brand_source(tmp_path / "repo")

    # 0 matches -> BuildError
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "agents:\n"
        "  cards_agent:\n"
        "    replace_lines:\n"
        "      - match: Line that does not exist anywhere in instruction.txt\n"
        "        with: Replacement line\n",
    )
    with pytest.raises(BuildError, match="replace_lines match not found"):
        build_app(
            core,
            man,
            "cymbal_direct",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_zero",
        )

    # >= 2 ambiguous matches -> BuildError
    cards_instr = core / "agents/cards_agent/instruction.txt"
    cards_instr.write_text(
        cards_instr.read_text(encoding="utf-8")
        + "Repeat check with @@brand.lexicon.auth_method@@.\n"
        + "Repeat check with @@brand.lexicon.auth_method@@.\n",
        encoding="utf-8",
    )
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "agents:\n"
        "  cards_agent:\n"
        "    replace_lines:\n"
        "      - match: Repeat check with @@brand.lexicon.auth_method@@.\n"
        "        with: Deduplicated check.\n",
    )
    with pytest.raises(BuildError, match="ambiguous replace_lines"):
        build_app(
            core,
            man,
            "cymbal_direct",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_ambig",
        )


# ---------------------------------------------------------------------------
# 3. `cxas backport --target brand --scope channel` (CLI E2E + CES Export)
# ---------------------------------------------------------------------------


def test_cli_backport_channel_scope_ces_export_git_branch_pr_body_idempotent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    core, man = _make_multi_brand_source(repo)
    _init_git_repo(repo, branch="main")

    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    sibling_ids = (
        "cymbal_direct/voice_telephony",
        "cymbal_retail/digital_app",
        "cymbal_retail/voice_telephony",
    )
    pre_sibling_shas = {s_id: _sha_map(dist / s_id) for s_id in sibling_ids}

    live = _materialize_real_ces_export(
        dist / "cymbal_direct/digital_app", tmp_path / "live_export"
    )
    assert not (live / "deployments").exists()
    assert "locked" not in _read_json(live / "app.json")

    # Tweak cards_agent description, temperature, mid-prompt line, and rule.
    live_cards_path = live / "agents/cards_agent/cards_agent.json"
    live_cards = _read_json(live_cards_path)
    live_cards["description"] = (
        "Console-tuned Cymbal Direct digital card controls."
    )
    live_cards["modelSettings"]["temperature"] = 0.4
    _write(live_cards_path, live_cards)

    live_cards_in = live / "agents/cards_agent/instruction.txt"
    live_cards_in.write_text(
        "Assist Cymbal Direct cardholders with digital card controls on "
        "https://portal.cymbal-direct.example.\n"
        "Confirm identity with Cymbal Pass before calling "
        "{@TOOL: update_card_limit}.\n"
        "Summarize card tiers with {@TOOL: format_card_summary} and share "
        "self-service guidance on https://portal.cymbal-direct.example.\n"
        "- Confirm updated Cymbal Direct spending limits via "
        "https://portal.cymbal-direct.example.\n",
        encoding="utf-8",
    )

    # Tweak OpenAPI tool description + concrete URL inside live/environment.json
    live_tool_path = live / "tools/update_card_limit/update_card_limit.json"
    live_tool = _read_json(live_tool_path)
    live_tool["openApiTool"]["description"] = (
        "Update Cymbal Direct card spending limit from digital app."
    )
    _write(live_tool_path, live_tool)

    live_env_path = live / "environment.json"
    live_env = _read_json(live_env_path)
    live_env["tools"]["update_card_limit"]["openApiTool"]["url"] = (
        "https://cards.cymbal-direct.example/v2/cards/limit"
    )
    _write(live_env_path, live_env)

    pr_body_path = tmp_path / "PR_BODY.md"
    target_dist_bundle = dist / "cymbal_direct/digital_app"
    target_lock = dist / "cymbal_direct/digital_app.lock.json"

    argv = [
        "cxas",
        "backport",
        "--core-dir",
        str(core),
        "--manifests-dir",
        str(man),
        "--live",
        str(live),
        "--dist",
        str(target_dist_bundle),
        "--pr-body-out",
        str(pr_body_path),
        "--git-branch",
        "backport/cymbal-direct-cards",
    ]
    with mock.patch.object(sys, "argv", argv):
        cli_main.main()

    first_out = capsys.readouterr().out
    assert "ROUND_TRIP_PROOF PASS (cymbal_direct/digital_app)" in first_out
    assert "GIT_BRANCH backport/cymbal-direct-cards" in first_out

    # 1. overrides.yaml reverse-tokenized cleanly with @@brand.*@@ / @@routing@@
    ov_path = man / "brands/cymbal_direct/overrides.yaml"
    assert ov_path.is_file()
    ov_text = ov_path.read_text(encoding="utf-8")
    assert "@@brand.display_name@@" in ov_text
    assert "@@brand.urls.portal@@" in ov_text
    assert "@@routing.cards_api@@/v2/cards/limit" in ov_text
    assert "Cymbal Direct" not in ov_text
    assert "https://portal.cymbal-direct.example" not in ov_text
    assert "https://cards.cymbal-direct.example" not in ov_text

    # 2. Drift between refreshed dist/cymbal_direct/digital_app and live is
    # 100% CLEAN.
    drift_rep = check_drift(
        "cymbal_direct/digital_app",
        target_dist_bundle,
        live,
        lockfile_path=target_lock,
    )
    assert drift_rep.is_clean is True
    assert drift_rep.has_unresolved is False

    # 3. All 3 sibling bundles have 0 SHA-256 changes on full rebuild.
    verify_dist = tmp_path / "verify_dist"
    build_all(core, man, verify_dist, "prod")
    for s_id in sibling_ids:
        assert _sha_map(verify_dist / s_id) == pre_sibling_shas[s_id]

    # 4. PR_BODY.md has zero absolute tmp_path leaks; Git branch switched.
    pr_md = pr_body_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in pr_md
    assert "manifests/brands/cymbal_direct/overrides.yaml" in pr_md
    assert "PASS (0 errors" in pr_md
    assert _git_current_branch(repo) == "backport/cymbal-direct-cards"

    # 5. Second run is idempotent and reports BACKPORT CLEAN.
    with mock.patch.object(sys, "argv", argv):
        cli_main.main()
    second_out = capsys.readouterr().out
    assert "BACKPORT CLEAN: cymbal_direct/digital_app" in second_out


# ---------------------------------------------------------------------------
# 4. Iterative Backports, channel -> brand -> core Promotion & Python Bundler
# ---------------------------------------------------------------------------


def test_iterative_channel_to_brand_to_core_promotion_preserves_conditionals(
    tmp_path: Path,
) -> None:
    core, man = _make_multi_brand_source(tmp_path / "repo")
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    retail_dig_sha = _sha_map(dist / "cymbal_retail/digital_app")
    retail_voice_sha = _sha_map(dist / "cymbal_retail/voice_telephony")

    # Step 1: First channel-scoped Console backport on cymbal_direct/digital_app
    live = _materialize_real_ces_export(
        dist / "cymbal_direct/digital_app", tmp_path / "live"
    )
    (live / "agents/root_agent/instruction.txt").write_text(
        "Route card servicing and limit requests to {@AGENT: cards_agent}.\n"
        "Route general product questions to {@AGENT: guide_agent}.\n"
        "- Mention Cymbal Direct digital self-service on "
        "https://portal.cymbal-direct.example.\n",
        encoding="utf-8",
    )
    res_ch1 = backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist / "cymbal_direct/digital_app",
        target="brand",
        scope="channel",
    )
    assert len(res_ch1.changes) == 1

    # Step 2: Iterative second channel-scoped Console edit on root_agent +
    # cards_agent
    live_cards = _read_json(live / "agents/cards_agent/cards_agent.json")
    live_cards["description"] = "Updated Cymbal Direct card specialist."
    _write(live / "agents/cards_agent/cards_agent.json", live_cards)
    (live / "agents/root_agent/instruction.txt").write_text(
        "Route urgent Cymbal Direct card requests to {@AGENT: cards_agent}.\n"
        "Route general product questions to {@AGENT: guide_agent}.\n"
        "- Mention Cymbal Direct digital self-service on "
        "https://portal.cymbal-direct.example.\n",
        encoding="utf-8",
    )
    res_ch2 = backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist / "cymbal_direct/digital_app",
        target="brand",
        scope="channel",
    )
    assert len(res_ch2.changes) == 2

    ov_after_ch = yaml.safe_load(
        (man / "brands/cymbal_direct/overrides.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert "DIGITAL_APP" in ov_after_ch["agents"]["root_agent"]["channels"]

    # Step 3: Promote channel overrides to brand-wide (`scope="brand"`).
    res_brand = backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist,
        brand_id="cymbal_direct",
        channel="DIGITAL_APP",
        environment="prod",
        target="brand",
        scope="brand",
    )
    assert "cymbal_retail/digital_app" in res_brand.unchanged_sibling_bundles
    assert (
        "cymbal_retail/voice_telephony" in res_brand.unchanged_sibling_bundles
    )

    ov_after_brand = yaml.safe_load(
        (man / "brands/cymbal_direct/overrides.yaml").read_text(
            encoding="utf-8"
        )
    )
    # Stale channels.DIGITAL_APP entries were popped during brand promotion.
    assert "channels" not in ov_after_brand["agents"]["root_agent"]
    assert "channels" not in ov_after_brand["agents"]["cards_agent"]

    # Both cymbal_direct channels updated; cymbal_retail has 0 SHA-256 changes.
    assert "Route urgent Cymbal Direct card requests" in (
        dist / "cymbal_direct/voice_telephony/agents/root_agent/instruction.txt"
    ).read_text(encoding="utf-8")
    assert _sha_map(dist / "cymbal_retail/digital_app") == retail_dig_sha
    assert _sha_map(dist / "cymbal_retail/voice_telephony") == retail_voice_sha

    # Step 4: Promote from brand overrides into shared `core/` (`target="core"`)
    res_core = backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist,
        brand_id="cymbal_direct",
        channel="DIGITAL_APP",
        environment="prod",
        target="core",
    )
    assert res_core.changes

    # Shadowing overrides.yaml is removed/cleaned once promoted into core/.
    ov_path = man / "brands/cymbal_direct/overrides.yaml"
    assert not ov_path.exists()

    # Inactive `{@startAgentEnabled_branch_agent}` block in core/ is preserved!
    core_root_in = (core / "agents/root_agent/instruction.txt").read_text(
        encoding="utf-8"
    )
    assert "{@startAgentEnabled_branch_agent}" in core_root_in
    assert "{@AGENT: branch_agent}" in core_root_in
    assert "{@endAgentEnabled_branch_agent}" in core_root_in
    assert "@@brand.display_name@@" in core_root_in
    assert "@@brand.urls.portal@@" in core_root_in

    # Rebuilt cymbal_retail/digital_app still routes to branch_agent and passes
    # lint with 0 errors.
    retail_root_in = (
        dist / "cymbal_retail/digital_app/agents/root_agent/instruction.txt"
    ).read_text(encoding="utf-8")
    assert "{@AGENT: branch_agent}" in retail_root_in
    assert "Cymbal Retail" in retail_root_in
    lint_retail = collect_lint_report(
        dist / "cymbal_retail/digital_app",
        manifests_dir=man,
        brand="cymbal_retail",
        channel="DIGITAL_APP",
    )
    assert lint_retail.errors == []


def test_core_backport_strips_cxas_shared_bundle_markers_and_reverses_tokens(
    tmp_path: Path,
) -> None:
    core, man = _make_multi_brand_source(tmp_path / "repo")
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    live = _materialize_real_ces_export(
        dist / "cymbal_direct/digital_app", tmp_path / "live_py"
    )
    # Bundle shared python_lib into entrypoint just as packaging/export does.
    bundled_sizes = bundle_shared_python(live)
    live_py_path = (
        live / "tools/format_card_summary/python_function/python_code.py"
    )
    assert bundled_sizes[str(live_py_path)] > 0
    bundled_text = live_py_path.read_text(encoding="utf-8")
    assert "# === BEGIN CXAS_SHARED_BUNDLE" in bundled_text

    # Edit handler body outside CXAS_SHARED_BUNDLE using concrete brand values.
    updated_live_py = bundled_text.replace(
        '"portal": "https://portal.cymbal-direct.example",',
        '"portal": "https://portal.cymbal-direct.example",\n'
        '        "issuer": "Cymbal Direct",',
    )
    live_py_path.write_text(updated_live_py, encoding="utf-8")

    res = backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist / "cymbal_direct/digital_app",
        target="core",
    )
    assert any(
        c.resource_id == "tools/format_card_summary" for c in res.changes
    )

    core_py_text = (
        core / "tools/format_card_summary/python_function/python_code.py"
    ).read_text(encoding="utf-8")
    assert "CXAS_SHARED_BUNDLE" not in core_py_text
    assert '"issuer": "@@brand.display_name@@"' in core_py_text
    assert '"portal": "@@brand.urls.portal@@"' in core_py_text

    # Diverged python_lib module digest in live is rejected fail-closed.
    tampered_live = _materialize_real_ces_export(
        dist / "cymbal_direct/digital_app", tmp_path / "live_tampered_py"
    )
    (tampered_live / "python_lib/cymbal_helpers.py").write_text(
        '"""Modified helper in live only."""\n\n\n'
        "def format_card_badge(brand_label: str, tier: str) -> str:\n"
        '    """Different implementation."""\n'
        '    return f"[{brand_label}/{tier}]"\n',
        encoding="utf-8",
    )
    bundle_shared_python(tampered_live)
    shutil.rmtree(tampered_live / "python_lib")

    with pytest.raises(BuildGateError, match="CXAS_SHARED_BUNDLE"):
        backport_live_to_source(
            core,
            man,
            tampered_live,
            dist_dir=dist / "cymbal_direct/digital_app",
            target="core",
        )


# ---------------------------------------------------------------------------
# 5. Single-Bundle & 1-Level `dist_dir` Lockfile Discovery + In-Place Refresh
# ---------------------------------------------------------------------------


def test_single_bundle_and_one_level_dist_lockfile_discovery_and_refresh(
    tmp_path: Path,
) -> None:
    # Part A: Single-bundle dist directory (`single_dist/app.json` + sibling
    # `single_dist.lock.json`)
    core_a, man_a = _make_multi_brand_source(tmp_path / "repo_single")
    single_dist = tmp_path / "single_dist"
    build_app(
        core_a,
        man_a,
        "cymbal_direct",
        "DIGITAL_APP",
        "prod",
        single_dist,
        write_lock=True,
    )
    single_lock = tmp_path / "single_dist.lock.json"
    assert single_lock.is_file()

    live_a = _materialize_real_ces_export(single_dist, tmp_path / "live_single")
    (live_a / "agents/guide_agent/instruction.txt").write_text(
        "Answer Cymbal Direct product questions using {@TOOL: kb_search} and "
        "https://portal.cymbal-direct.example.\n",
        encoding="utf-8",
    )

    # Concurrent edit in core_a triggers 3-way DriftGateError via discovered
    # sibling lockfile!
    orig_guide = (core_a / "agents/guide_agent/instruction.txt").read_text(
        encoding="utf-8"
    )
    (core_a / "agents/guide_agent/instruction.txt").write_text(
        orig_guide + "- Concurrent Git-side rule.\n", encoding="utf-8"
    )
    with pytest.raises(DriftGateError, match="3-way conflict"):
        backport_live_to_source(core_a, man_a, live_a, dist_dir=single_dist)

    # Restore core_a and verify clean single-bundle refresh without nesting.
    (core_a / "agents/guide_agent/instruction.txt").write_text(
        orig_guide, encoding="utf-8"
    )
    res_single = backport_live_to_source(
        core_a, man_a, live_a, dist_dir=single_dist
    )
    assert res_single.lockfile_path == single_lock
    assert not (single_dist / "cymbal_direct").exists()
    assert (
        check_drift(
            "cymbal_direct/digital_app",
            single_dist,
            live_a,
            lockfile_path=single_lock,
        ).is_clean
        is True
    )

    # Part B: 1-level `dist/<brand>-<channel>` layout
    core_b, man_b = _make_multi_brand_source(tmp_path / "repo_one_level")
    one_dist = tmp_path / "one_level_dist"
    for b_id in ("cymbal_retail", "cymbal_direct"):
        for ch in ("DIGITAL_APP", "VOICE_TELEPHONY"):
            build_app(
                core_b,
                man_b,
                b_id,
                ch,
                "prod",
                one_dist / f"{b_id}-{ch.lower()}",
                write_lock=True,
            )
    one_lock = one_dist / "cymbal_direct-digital_app.lock.json"
    assert one_lock.is_file()

    live_b = _materialize_real_ces_export(
        one_dist / "cymbal_direct-digital_app", tmp_path / "live_one_level"
    )
    (live_b / "agents/guide_agent/instruction.txt").write_text(
        "Answer Cymbal Direct digital questions with {@TOOL: kb_search}.\n",
        encoding="utf-8",
    )

    # Concurrent edit in core_b is caught via discovered 1-level lockfile!
    (core_b / "agents/guide_agent/instruction.txt").write_text(
        orig_guide + "- Another concurrent Git edit.\n", encoding="utf-8"
    )
    with pytest.raises(DriftGateError, match="3-way conflict"):
        backport_live_to_source(
            core_b,
            man_b,
            live_b,
            dist_dir=one_dist,
            brand_id="cymbal_direct",
            channel="DIGITAL_APP",
            environment="prod",
        )

    # Restore core_b and verify in-place 1-level refresh (no 2-level nesting).
    (core_b / "agents/guide_agent/instruction.txt").write_text(
        orig_guide, encoding="utf-8"
    )
    res_one = backport_live_to_source(
        core_b,
        man_b,
        live_b,
        dist_dir=one_dist,
        brand_id="cymbal_direct",
        channel="DIGITAL_APP",
        environment="prod",
    )
    assert res_one.lockfile_path == one_lock
    assert not (one_dist / "cymbal_direct").exists()
    assert not (one_dist / "cymbal_retail").exists()
    assert (
        check_drift(
            "cymbal_direct/digital_app",
            one_dist / "cymbal_direct-digital_app",
            live_b,
            lockfile_path=one_lock,
        ).is_clean
        is True
    )


# ---------------------------------------------------------------------------
# 6. Adversarial Gates (`P001`-`P005`, 3-Way Drift, Orphans, Git Precheck)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutation_kind", "expected_rule"),
    [
        ("sister_brand_leak", "P001"),
        ("forbidden_token_leak", "P001"),
        ("deprecated_model", "P005"),
        ("alias_model", "P005"),
    ],
)
def test_adversarial_lint_p001_and_p005_fail_closed_with_atomic_rollback(
    tmp_path: Path, mutation_kind: str, expected_rule: str
) -> None:
    repo = tmp_path / "repo"
    core, man = _make_multi_brand_source(repo)
    _init_git_repo(repo, branch="main")

    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)
    live = _materialize_real_ces_export(
        dist / "cymbal_direct/digital_app", tmp_path / "live"
    )

    if mutation_kind == "sister_brand_leak":
        (live / "agents/cards_agent/instruction.txt").write_text(
            "Assist Cymbal Retail customers with card controls.\n"
            "Use {@TOOL: update_card_limit} and {@TOOL: format_card_summary}.\n",
            encoding="utf-8",
        )
    elif mutation_kind == "forbidden_token_leak":
        (live / "agents/cards_agent/instruction.txt").write_text(
            "Verify identity with Cymbal Branch Pass before calling "
            "{@TOOL: update_card_limit} or {@TOOL: format_card_summary}.\n",
            encoding="utf-8",
        )
    elif mutation_kind == "deprecated_model":
        cards = _read_json(live / "agents/cards_agent/cards_agent.json")
        cards["modelSettings"]["model"] = "gemini-2.5-flash"
        _write(live / "agents/cards_agent/cards_agent.json", cards)
    else:
        cards = _read_json(live / "agents/cards_agent/cards_agent.json")
        cards["modelSettings"]["model"] = "latest"
        _write(live / "agents/cards_agent/cards_agent.json", cards)

    core_before = _snapshot_tree_bytes(core)
    man_before = _snapshot_tree_bytes(man)

    argv = [
        "cxas",
        "backport",
        "--core-dir",
        str(core),
        "--manifests-dir",
        str(man),
        "--live",
        str(live),
        "--dist",
        str(dist / "cymbal_direct/digital_app"),
        "--git-branch",
        "backport/should-never-exist",
    ]
    with (
        mock.patch.object(sys, "argv", argv),
        pytest.raises(SystemExit) as exc_info,
    ):
        cli_main.main()
    assert exc_info.value.code == 1

    with pytest.raises(BuildGateError, match=expected_rule):
        backport_live_to_source(
            core,
            man,
            live,
            dist_dir=dist / "cymbal_direct/digital_app",
            git_branch="backport/should-never-exist",
        )

    assert _snapshot_tree_bytes(core) == core_before
    assert _snapshot_tree_bytes(man) == man_before
    assert _git_current_branch(repo) == "main"


def test_adversarial_three_way_conflict_diff_markers_and_force_live(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    core, man = _make_multi_brand_source(repo)
    _init_git_repo(repo, branch="main")

    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)
    live = _materialize_real_ces_export(
        dist / "cymbal_direct/digital_app", tmp_path / "live"
    )

    # Concurrent edit in Git `core/` AND Console `live`
    (core / "agents/cards_agent/instruction.txt").write_text(
        "Assist @@brand.display_name@@ cardholders with Git-side policy.\n"
        "Use {@TOOL: update_card_limit} and {@TOOL: format_card_summary}.\n",
        encoding="utf-8",
    )
    (live / "agents/cards_agent/instruction.txt").write_text(
        "Assist Cymbal Direct cardholders with Console-side emergency rule.\n"
        "Use {@TOOL: update_card_limit} and {@TOOL: format_card_summary}.\n",
        encoding="utf-8",
    )

    core_before = _snapshot_tree_bytes(core)
    man_before = _snapshot_tree_bytes(man)
    conflicts_dir = tmp_path / "conflicts"

    with pytest.raises(DriftGateError, match="agents/cards_agent"):
        backport_live_to_source(
            core,
            man,
            live,
            dist_dir=dist / "cymbal_direct/digital_app",
            conflicts_dir=conflicts_dir,
            git_branch="backport/conflict-blocked",
        )

    assert _snapshot_tree_bytes(core) == core_before
    assert _snapshot_tree_bytes(man) == man_before
    assert _git_current_branch(repo) == "main"

    diff_files = list(conflicts_dir.rglob("*.diff"))
    assert len(diff_files) == 1
    diff_text = diff_files[0].read_text(encoding="utf-8")
    assert "<<<<<<<" in diff_text
    assert "=======" in diff_text
    assert ">>>>>>>" in diff_text

    # Passing `--force-live agents/cards_agent` resolves conflict cleanly via CLI
    argv = [
        "cxas",
        "backport",
        "--core-dir",
        str(core),
        "--manifests-dir",
        str(man),
        "--live",
        str(live),
        "--dist",
        str(dist / "cymbal_direct/digital_app"),
        "--force-live",
        "agents/cards_agent",
        "--git-branch",
        "backport/forced-live",
    ]
    with mock.patch.object(sys, "argv", argv):
        cli_main.main()

    assert _git_current_branch(repo) == "backport/forced-live"
    assert (man / "brands/cymbal_direct/overrides.yaml").is_file()
    assert (
        check_drift(
            "cymbal_direct/digital_app",
            dist / "cymbal_direct/digital_app",
            live,
            lockfile_path=dist / "cymbal_direct/digital_app.lock.json",
        ).is_clean
        is True
    )


def test_adversarial_orphan_tool_python_brand_target_and_structural_rollback(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    core, man = _make_multi_brand_source(repo)
    _init_git_repo(repo, branch="main")

    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)
    core_before = _snapshot_tree_bytes(core)
    man_before = _snapshot_tree_bytes(man)

    # Case A: Undeclared orphan tool in live export
    live_orphan = _materialize_real_ces_export(
        dist / "cymbal_direct/digital_app", tmp_path / "live_orphan"
    )
    _write(
        live_orphan / "tools/orphan_lookup/orphan_lookup.json",
        {
            "displayName": "orphan_lookup",
            "openApiTool": {
                "name": "orphan_lookup",
                "description": "Console-created undeclared tool.",
                "url": "https://cards.cymbal-direct.example/v1/orphan",
            },
        },
    )
    with pytest.raises(BuildGateError, match="undeclared orphan resource"):
        backport_live_to_source(
            core,
            man,
            live_orphan,
            dist_dir=dist / "cymbal_direct/digital_app",
        )
    assert _snapshot_tree_bytes(core) == core_before
    assert _snapshot_tree_bytes(man) == man_before

    # Case B: `.py` code edit with `--target brand`
    live_py = _materialize_real_ces_export(
        dist / "cymbal_direct/digital_app", tmp_path / "live_py_brand"
    )
    py_file = (
        live_py / "tools/format_card_summary/python_function/python_code.py"
    )
    py_file.write_text(
        py_file.read_text(encoding="utf-8") + "\n# Console code tweak\n",
        encoding="utf-8",
    )
    with pytest.raises(BuildGateError, match="--target core"):
        backport_live_to_source(
            core,
            man,
            live_py,
            dist_dir=dist / "cymbal_direct/digital_app",
            target="brand",
        )
    assert _snapshot_tree_bytes(core) == core_before
    assert _snapshot_tree_bytes(man) == man_before

    # Case C: Structural agent `tools` list mutation alongside valid prompt edit
    live_struct = _materialize_real_ces_export(
        dist / "cymbal_direct/digital_app", tmp_path / "live_struct"
    )
    (live_struct / "agents/guide_agent/instruction.txt").write_text(
        "Answer Cymbal Direct product questions using {@TOOL: kb_search} "
        "concisely.\n",
        encoding="utf-8",
    )
    cards_json = _read_json(live_struct / "agents/cards_agent/cards_agent.json")
    cards_json["tools"] = ["update_card_limit"]
    _write(live_struct / "agents/cards_agent/cards_agent.json", cards_json)
    (live_struct / "agents/cards_agent/instruction.txt").write_text(
        "Assist Cymbal Direct cardholders using {@TOOL: update_card_limit}.\n",
        encoding="utf-8",
    )
    with pytest.raises(
        BuildGateError, match="Cannot backport structural JSON change"
    ):
        backport_live_to_source(
            core,
            man,
            live_struct,
            dist_dir=dist / "cymbal_direct/digital_app",
            target="brand",
        )
    assert _snapshot_tree_bytes(core) == core_before
    assert _snapshot_tree_bytes(man) == man_before


def test_adversarial_git_branch_precheck_rejects_unsafe_non_git_and_existing(
    tmp_path: Path,
) -> None:
    # 1. Non-git workspace rejects `--git-branch`
    non_git = tmp_path / "non_git"
    core_ng, man_ng = _make_multi_brand_source(non_git)
    dist_ng = tmp_path / "dist_ng"
    build_all(core_ng, man_ng, dist_ng, "prod", write_locks=True)
    live_ng = _materialize_real_ces_export(
        dist_ng / "cymbal_direct/digital_app", tmp_path / "live_ng"
    )
    with pytest.raises(BuildGateError, match="not inside a git worktree"):
        backport_live_to_source(
            core_ng,
            man_ng,
            live_ng,
            dist_dir=dist_ng / "cymbal_direct/digital_app",
            git_branch="backport/non-git",
        )

    # 2. Unsafe branch characters & existing branch name rejected in git repo
    repo = tmp_path / "repo"
    core, man = _make_multi_brand_source(repo)
    _init_git_repo(repo, branch="main")
    subprocess.run(
        ["git", "-C", str(repo), "branch", "existing-branch"],
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
        check=True,
        capture_output=True,
        text=True,
    )
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)
    live = _materialize_real_ces_export(
        dist / "cymbal_direct/digital_app", tmp_path / "live"
    )
    (live / "agents/guide_agent/instruction.txt").write_text(
        "Answer Cymbal Direct questions using {@TOOL: kb_search} clearly.\n",
        encoding="utf-8",
    )
    man_before = _snapshot_tree_bytes(man)

    with pytest.raises(BuildGateError, match="Invalid --git-branch"):
        backport_live_to_source(
            core,
            man,
            live,
            dist_dir=dist / "cymbal_direct/digital_app",
            git_branch="../bad branch",
        )

    with pytest.raises(
        BuildGateError, match="clobber existing local git branch"
    ):
        backport_live_to_source(
            core,
            man,
            live,
            dist_dir=dist / "cymbal_direct/digital_app",
            git_branch="existing-branch",
        )

    assert _snapshot_tree_bytes(man) == man_before
    assert _git_current_branch(repo) == "main"


# ---------------------------------------------------------------------------
# 7. Permanent Round-Trip Edge-Case Regressions
# ---------------------------------------------------------------------------


def test_channel_backport_rewrites_prompt_over_existing_brand_replace_lines(
    tmp_path: Path,
) -> None:
    core, man = _make_multi_brand_source(tmp_path / "repo")
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "agents:\n"
        "  cards_agent:\n"
        "    replace_lines:\n"
        "      - match: Assist @@brand.display_name@@ cardholders with card "
        "controls.\n"
        "        with: Support @@brand.display_name@@ cardholders quickly.\n"
        "    append_rules:\n"
        "      - Keep Cymbal Pass verification active.\n",
    )
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    live = _materialize_real_ces_export(
        dist / "cymbal_direct/digital_app", tmp_path / "live"
    )
    (live / "agents/cards_agent/instruction.txt").write_text(
        "Completely rewritten Cymbal Direct digital card workflow.\n"
        "Call {@TOOL: update_card_limit} or {@TOOL: format_card_summary} "
        "after Cymbal Pass verification.\n",
        encoding="utf-8",
    )
    res = backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist / "cymbal_direct/digital_app",
        target="brand",
        scope="channel",
    )
    assert len(res.changes) == 1
    assert (
        check_drift(
            "cymbal_direct/digital_app",
            dist / "cymbal_direct/digital_app",
            live,
            lockfile_path=dist / "cymbal_direct/digital_app.lock.json",
        ).is_clean
        is True
    )


def test_channel_json_only_edit_preserves_existing_brand_append_rules(
    tmp_path: Path,
) -> None:
    core, man = _make_multi_brand_source(tmp_path / "repo")
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "agents:\n"
        "  cards_agent:\n"
        "    description: Brand-wide @@brand.display_name@@ cards specialist.\n"
        "    append_rules:\n"
        "      - Brand-wide card audit rule for @@brand.display_name@@.\n",
    )
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    live = _materialize_real_ces_export(
        dist / "cymbal_direct/digital_app", tmp_path / "live"
    )
    cards = _read_json(live / "agents/cards_agent/cards_agent.json")
    cards["modelSettings"]["temperature"] = 0.55
    _write(live / "agents/cards_agent/cards_agent.json", cards)

    backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist / "cymbal_direct/digital_app",
        target="brand",
        scope="channel",
    )
    rebuilt_instr = (
        dist / "cymbal_direct/digital_app/agents/cards_agent/instruction.txt"
    ).read_text(encoding="utf-8")
    assert rebuilt_instr.count("Brand-wide card audit rule") == 1


def test_global_instruction_full_override_preserves_identity_header_once(
    tmp_path: Path,
) -> None:
    core, man = _make_multi_brand_source(tmp_path / "repo")
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "global_instruction:\n"
        "  instruction: |\n"
        "    Custom global policy for @@brand.display_name@@.\n"
        "    Authenticate sensitive requests with "
        "@@brand.lexicon.auth_method@@.\n",
    )
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    live = _materialize_real_ces_export(
        dist / "cymbal_direct/digital_app", tmp_path / "live"
    )
    gi_path = live / "global_instruction.txt"
    gi_path.write_text(
        gi_path.read_text(encoding="utf-8")
        + "- Direct digital inquiries to https://portal.cymbal-direct.example.\n",
        encoding="utf-8",
    )

    backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist,
        brand_id="cymbal_direct",
        channel="DIGITAL_APP",
        environment="prod",
        target="brand",
        scope="brand",
    )
    rebuilt_gi = (
        dist / "cymbal_direct/digital_app/global_instruction.txt"
    ).read_text(encoding="utf-8")
    assert rebuilt_gi.count("# Brand: Cymbal Direct") == 1


def test_openapi_schema_desc_sync_rejected_on_brand_accepted_on_core(
    tmp_path: Path,
) -> None:
    core, man = _make_multi_brand_source(tmp_path / "repo")
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    live = _materialize_real_ces_export(
        dist / "cymbal_retail/digital_app", tmp_path / "live"
    )
    new_desc = "Locate Cymbal Retail branches with live wait times."
    tj_path = live / "tools/find_branches/find_branches.json"
    tj = _read_json(tj_path)
    tj["openApiTool"]["description"] = new_desc
    _write(tj_path, tj)

    live_env = _read_json(live / "environment.json")
    live_env["tools"]["find_branches"]["openApiTool"]["url"] = (
        "https://branches.cymbal-retail.example/v2/branches"
    )
    _write(live / "environment.json", live_env)

    oa_path = live / "tools/find_branches/open_api_tool/open_api_schema.yaml"
    oa_path.write_text(
        "openapi: 3.0.0\n"
        "info:\n"
        "  title: Cymbal Branches API\n"
        "  version: '1.0'\n"
        f"  description: {new_desc}\n"
        "servers:\n"
        "  - url: $env_var\n"
        "paths:\n"
        "  /v2/branches:\n"
        "    get:\n"
        "      operationId: listBranches\n"
        f"      summary: {new_desc}\n"
        f"      description: {new_desc}\n"
        "      responses:\n"
        "        '200':\n"
        "          description: Branch list\n",
        encoding="utf-8",
    )

    man_before = _snapshot_tree_bytes(man)
    with pytest.raises(BuildGateError, match="--target core"):
        backport_live_to_source(
            core,
            man,
            live,
            dist_dir=dist / "cymbal_retail/digital_app",
            target="brand",
        )
    assert _snapshot_tree_bytes(man) == man_before

    res_core = backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist / "cymbal_retail/digital_app",
        target="core",
    )
    assert any(c.resource_id == "tools/find_branches" for c in res_core.changes)
    core_oa = (
        core / "tools/find_branches/open_api_tool/open_api_schema.yaml"
    ).read_text(encoding="utf-8")
    assert "/v2/branches:" in core_oa
    assert "@@brand.display_name@@" in core_oa
    assert "@@routing.branches_api@@" in core_oa


# ---------------------------------------------------------------------------
# 8. G1-G4 Regression Suite
# ---------------------------------------------------------------------------


def test_g1_cross_bundle_post_compile_lint_gate_rolls_back_atomically(
    tmp_path: Path,
) -> None:
    # Case A (`--target brand --scope brand`): tool enabled on DIGITAL_APP and
    # disabled on VOICE_TELEPHONY fails P004 on sibling channel and rolls back.
    core_a, man_a = _make_multi_brand_source(tmp_path / "repo_a")
    _write(
        core_a / "agents/cards_agent/instruction.txt",
        "Assist @@brand.display_name@@ cardholders with card controls.\n"
        "Confirm identity with @@brand.lexicon.auth_method@@ before calling "
        "{@TOOL: update_card_limit}.\n"
        "{@startToolEnabled_format_card_summary}\n"
        "Summarize card tiers with {@TOOL: format_card_summary}.\n"
        "{@endToolEnabled_format_card_summary}\n",
    )
    _write(
        man_a / "brands/cymbal_direct/channel_policy.yaml",
        "brand_id: cymbal_direct\n"
        "api_routing:\n"
        "  branches_api: https://branches.cymbal-direct.example\n"
        "  cards_api: https://cards.cymbal-direct.example\n"
        "channels:\n"
        "  DIGITAL_APP:\n"
        "    disabled_agents:\n"
        "      - branch_agent\n"
        "    disabled_tools:\n"
        "      - find_branches\n"
        "    supported_modalities: ['TEXT']\n"
        "  VOICE_TELEPHONY:\n"
        "    disabled_agents:\n"
        "      - branch_agent\n"
        "    disabled_tools:\n"
        "      - find_branches\n"
        "      - format_card_summary\n"
        "    supported_modalities: ['AUDIO']\n",
    )
    dist_a = tmp_path / "dist_a"
    build_all(core_a, man_a, dist_a, "prod", write_locks=True)

    live_a = _materialize_real_ces_export(
        dist_a / "cymbal_direct/digital_app", tmp_path / "live_a"
    )
    cards_in_a = live_a / "agents/cards_agent/instruction.txt"
    cards_in_a.write_text(
        cards_in_a.read_text(encoding="utf-8")
        + "- Always call {@TOOL: format_card_summary} for tier recaps.\n",
        encoding="utf-8",
    )
    man_a_before = _snapshot_tree_bytes(man_a)
    with pytest.raises(BuildGateError, match=r"P004|A003|S002"):
        backport_live_to_source(
            core_a,
            man_a,
            live_a,
            dist_dir=dist_a,
            brand_id="cymbal_direct",
            channel="DIGITAL_APP",
            environment="prod",
            target="brand",
            scope="brand",
        )
    assert _snapshot_tree_bytes(man_a) == man_a_before

    # Case B (`--target core`): token forbidden on cymbal_retail added via
    # cymbal_direct live fails P001 on cymbal_retail and rolls back core/.
    core_b, man_b = _make_multi_brand_source(tmp_path / "repo_b")
    retail_id = man_b / "brands/cymbal_retail/identity.yaml"
    retail_id.write_text(
        retail_id.read_text(encoding="utf-8") + "  - Cymbal Legacy Pin\n",
        encoding="utf-8",
    )
    dist_b = tmp_path / "dist_b"
    build_all(core_b, man_b, dist_b, "prod", write_locks=True)

    live_b = _materialize_real_ces_export(
        dist_b / "cymbal_direct/digital_app", tmp_path / "live_b"
    )
    guide_in_b = live_b / "agents/guide_agent/instruction.txt"
    guide_in_b.write_text(
        guide_in_b.read_text(encoding="utf-8")
        + "- Mention Cymbal Legacy Pin migration help.\n",
        encoding="utf-8",
    )
    core_b_before = _snapshot_tree_bytes(core_b)
    with pytest.raises(BuildGateError, match=r"cymbal_retail.*P001|P001"):
        backport_live_to_source(
            core_b,
            man_b,
            live_b,
            dist_dir=dist_b,
            brand_id="cymbal_direct",
            channel="DIGITAL_APP",
            environment="prod",
            target="core",
        )
    assert _snapshot_tree_bytes(core_b) == core_b_before


def test_g2_explicit_identity_block_token_preserved_on_core_backport(
    tmp_path: Path,
) -> None:
    core, man = _make_multi_brand_source(tmp_path / "repo")
    _write(
        core / "global_instruction.txt",
        "You represent @@brand.display_name@@ across every conversation.\n"
        "@@brand.identity_block@@\n"
        "Verify sensitive actions with @@brand.lexicon.auth_method@@ or via "
        "@@brand.urls.portal@@.\n",
    )
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    live = _materialize_real_ces_export(
        dist / "cymbal_direct/digital_app", tmp_path / "live"
    )
    gi_live = live / "global_instruction.txt"
    gi_live.write_text(
        gi_live.read_text(encoding="utf-8").replace(
            "Verify sensitive actions with Cymbal Pass or via "
            "https://portal.cymbal-direct.example.",
            "Confirm high-value Cymbal Direct actions with Cymbal Pass on "
            "https://portal.cymbal-direct.example.",
        ),
        encoding="utf-8",
    )

    backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist,
        brand_id="cymbal_direct",
        channel="DIGITAL_APP",
        environment="prod",
        target="core",
    )

    core_gi = (core / "global_instruction.txt").read_text(encoding="utf-8")
    assert "@@brand.identity_block@@" in core_gi
    assert "# Brand: Cymbal Direct" not in core_gi
    assert (
        "Confirm high-value @@brand.display_name@@ actions with "
        "@@brand.lexicon.auth_method@@ on @@brand.urls.portal@@." in core_gi
    )
    assert (
        check_drift(
            "cymbal_direct/digital_app",
            dist / "cymbal_direct/digital_app",
            live,
            lockfile_path=dist / "cymbal_direct/digital_app.lock.json",
        ).is_clean
        is True
    )
    for b_id in ("cymbal_retail", "cymbal_direct"):
        for ch in ("DIGITAL_APP", "VOICE_TELEPHONY"):
            lint_rep = collect_lint_report(
                dist / b_id / ch.lower(),
                manifests_dir=man,
                brand=b_id,
                channel=ch,
            )
            assert lint_rep.errors == []


def test_g3_inactive_inline_conditional_preserved_when_blank_line_trimmed(
    tmp_path: Path,
) -> None:
    core, man = _make_multi_brand_source(tmp_path / "repo")
    _write(
        core / "agents/root_agent/instruction.txt",
        "{@startAgentEnabled_branch_agent}Route branch questions to "
        "{@AGENT: branch_agent}.{@endAgentEnabled_branch_agent}\n"
        "Route card servicing and limit requests to {@AGENT: cards_agent}.\n"
        "Route general product questions to {@AGENT: guide_agent}.\n",
    )
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    live = _materialize_real_ces_export(
        dist / "cymbal_direct/digital_app", tmp_path / "live"
    )
    # Trim the leading blank line emitted by the inactive inline conditional
    # and edit the active routing line in Console.
    (live / "agents/root_agent/instruction.txt").write_text(
        "Route priority Cymbal Direct card requests to {@AGENT: cards_agent}.\n"
        "Route general product questions to {@AGENT: guide_agent}.\n",
        encoding="utf-8",
    )

    backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist,
        brand_id="cymbal_direct",
        channel="DIGITAL_APP",
        environment="prod",
        target="core",
    )

    assert (
        check_drift(
            "cymbal_direct/digital_app",
            dist / "cymbal_direct/digital_app",
            live,
            lockfile_path=dist / "cymbal_direct/digital_app.lock.json",
        ).is_clean
        is True
    )
    retail_root = (
        dist / "cymbal_retail/digital_app/agents/root_agent/instruction.txt"
    ).read_text(encoding="utf-8")
    assert "Route branch questions to {@AGENT: branch_agent}." in retail_root


def test_g4_cli_drift_accumulates_repeated_accept_live_and_force_ours() -> None:
    captured: dict[str, Any] = {}

    class _CleanStub:
        resources: list[Any] = []
        git_mirror_commit: str | None = None
        has_unresolved: bool = False

    def _fake_check_drift(*args: Any, **kwargs: Any) -> Any:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _CleanStub()

    argv = [
        "cxas",
        "drift",
        "--app",
        "cymbal_direct/digital_app",
        "--dist",
        "dist_dir",
        "--live",
        "live_dir",
        "--accept-live",
        "a",
        "--accept-live",
        "b",
        "--force-ours",
        "x",
        "--force-ours",
        "y",
    ]
    with (
        mock.patch.object(sys, "argv", argv),
        mock.patch(
            "cxas_scrapi.cli.build_cli.check_drift",
            side_effect=_fake_check_drift,
        ),
    ):
        cli_main.main()

    assert list(captured["kwargs"]["accept_live"]) == ["a", "b"]
    assert list(captured["kwargs"]["force_ours"]) == ["x", "y"]
