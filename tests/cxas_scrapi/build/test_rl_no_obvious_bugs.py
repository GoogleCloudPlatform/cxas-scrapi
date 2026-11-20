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

"""Failing reproduction tests for Round 1 no-obvious-bugs review findings."""

import json
import shutil
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
import yaml

from cxas_scrapi.build import (
    BuildError,
    BuildGateError,
    ManifestError,
    backport_live_to_source,
    build_all,
    build_app,
    check_drift,
    enforce_pre_push_gate,
)
from cxas_scrapi.cli import main as cli_main

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


def _make_cymbal_source(root: Path) -> tuple[Path, Path]:
    """Creates a minimal Cymbal-only 2-brand x 2-channel Rung 1 source tree."""
    core = root / "core"
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
            "childAgents": ["cards_agent", "guide_agent"],
            "tools": ["end_session"],
        },
    )
    _write(
        core / "agents/root_agent/instruction.txt",
        "Route card servicing requests to {@AGENT: cards_agent}.\n"
        "Route general product questions to {@AGENT: guide_agent}.\n",
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
            "tools": ["update_card_limit"],
        },
    )
    _write(
        core / "agents/cards_agent/instruction.txt",
        "Assist @@brand.display_name@@ cardholders with card controls.\n"
        "Confirm identity with @@brand.lexicon.auth_method@@ before calling "
        "{@TOOL: update_card_limit}.\n",
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
        "          description: Card limit updated\n",
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
        "    speaking_rate: 1.0\n",
    )
    _write(
        man / "brands/cymbal_retail/channel_policy.yaml",
        "brand_id: cymbal_retail\n"
        "api_routing:\n"
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
        "    speaking_rate: 1.0\n",
    )
    _write(
        man / "brands/cymbal_direct/channel_policy.yaml",
        "brand_id: cymbal_direct\n"
        "api_routing:\n"
        "  cards_api: https://cards.cymbal-direct.example\n"
        "channels:\n"
        "  DIGITAL_APP:\n"
        "    supported_modalities: ['TEXT']\n"
        "  VOICE_TELEPHONY:\n"
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


def _materialize_ces_export(bundle_dir: Path, live_dir: Path) -> Path:
    """Copies a compiled bundle into a CES export layout."""
    if live_dir.exists():
        shutil.rmtree(live_dir)
    shutil.copytree(bundle_dir, live_dir)
    dep_dir = live_dir / "deployments"
    if dep_dir.exists():
        shutil.rmtree(dep_dir)

    app_data = _read_json(live_dir / "app.json")
    app_data.pop("locked", None)
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


# RL-PENDING-1
def test_rl_pending_1_overrides_path_traversal_rejected(
    tmp_path: Path,
) -> None:
    """RL-PENDING-1: override keys must not traverse outside out_dir."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    victim_dir = tmp_path / "outside_victim"
    victim_dir.mkdir()
    victim_file = victim_dir / "instruction.txt"
    victim_file.write_text("SENSITIVE OUTSIDE FILE\n", encoding="utf-8")

    rel_traversal = "../" * 16 + victim_dir.as_posix().lstrip("/")
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        f"brand_id: cymbal_direct\n"
        f"agents:\n"
        f"  {rel_traversal!r}:\n"
        f"    instruction: OVERWRITTEN BY TRAVERSAL\n",
    )

    with pytest.raises((ManifestError, BuildError)):
        build_app(
            core,
            man,
            "cymbal_direct",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out",
        )

    assert victim_file.read_text(encoding="utf-8") == "SENSITIVE OUTSIDE FILE\n"


# RL-PENDING-2
def test_rl_pending_2_backport_blank_line_edits_do_not_crash(
    tmp_path: Path,
) -> None:
    """RL-PENDING-2: deleting or inserting after a blank line must not crash."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    _write(
        core / "agents/cards_agent/instruction.txt",
        "Assist @@brand.display_name@@ cardholders with card controls.\n"
        "\n"
        "Confirm identity with @@brand.lexicon.auth_method@@ before calling "
        "{@TOOL: update_card_limit}.\n",
    )
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    # Case A: deleting the blank line between paragraphs
    live_del = _materialize_ces_export(
        dist / "cymbal_direct/digital_app", tmp_path / "live_del"
    )
    (live_del / "agents/cards_agent/instruction.txt").write_text(
        "Assist Cymbal Direct cardholders with card controls.\n"
        "Confirm identity with Cymbal Pass before calling "
        "{@TOOL: update_card_limit}.\n",
        encoding="utf-8",
    )
    backport_live_to_source(
        core,
        man,
        live_del,
        dist_dir=dist / "cymbal_direct/digital_app",
    )
    assert (
        check_drift(
            "cymbal_direct/digital_app",
            dist / "cymbal_direct/digital_app",
            live_del,
            lockfile_path=dist / "cymbal_direct/digital_app.lock.json",
        ).is_clean
        is True
    )

    # Case B: inserting a new line immediately after a blank line
    core_b, man_b = _make_cymbal_source(tmp_path / "repo_b")
    _write(
        core_b / "agents/cards_agent/instruction.txt",
        "Assist @@brand.display_name@@ cardholders with card controls.\n"
        "\n"
        "Confirm identity with @@brand.lexicon.auth_method@@ before calling "
        "{@TOOL: update_card_limit}.\n",
    )
    dist_b = tmp_path / "dist_b"
    build_all(core_b, man_b, dist_b, "prod", write_locks=True)
    live_ins = _materialize_ces_export(
        dist_b / "cymbal_direct/digital_app", tmp_path / "live_ins"
    )
    (live_ins / "agents/cards_agent/instruction.txt").write_text(
        "Assist Cymbal Direct cardholders with card controls.\n"
        "\n"
        "Check card freeze status on https://portal.cymbal-direct.example.\n"
        "Confirm identity with Cymbal Pass before calling "
        "{@TOOL: update_card_limit}.\n",
        encoding="utf-8",
    )
    backport_live_to_source(
        core_b,
        man_b,
        live_ins,
        dist_dir=dist_b / "cymbal_direct/digital_app",
    )
    assert (
        check_drift(
            "cymbal_direct/digital_app",
            dist_b / "cymbal_direct/digital_app",
            live_ins,
            lockfile_path=dist_b / "cymbal_direct/digital_app.lock.json",
        ).is_clean
        is True
    )


# RL-PENDING-3
def test_rl_pending_3_backport_omitted_temperature_and_cleared_description(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """RL-PENDING-3: omitted temperature and cleared description handling."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    # Part A: live omits `temperature` from modelSettings (reset to default)
    live_temp = _materialize_ces_export(
        dist / "cymbal_direct/digital_app", tmp_path / "live_temp"
    )
    cards_json = _read_json(live_temp / "agents/cards_agent/cards_agent.json")
    cards_json["modelSettings"].pop("temperature", None)
    _write(live_temp / "agents/cards_agent/cards_agent.json", cards_json)

    argv = [
        "cxas",
        "backport",
        "--core-dir",
        str(core),
        "--manifests-dir",
        str(man),
        "--live",
        str(live_temp),
        "--dist",
        str(dist / "cymbal_direct/digital_app"),
        "--json",
    ]
    exited_code: int | None = None
    with mock.patch.object(sys, "argv", argv):
        try:
            cli_main.main()
        except SystemExit as exc:
            exited_code = int(exc.code) if exc.code is not None else 0

    payload = json.loads(capsys.readouterr().out)
    if exited_code is None or exited_code == 0:
        assert payload["passed"] is True
        assert (
            check_drift(
                "cymbal_direct/digital_app",
                dist / "cymbal_direct/digital_app",
                live_temp,
                lockfile_path=dist / "cymbal_direct/digital_app.lock.json",
            ).is_clean
            is True
        )
    else:
        assert exited_code == 1
        assert payload["passed"] is False
        assert "temperature" in payload["error"]

    # Part B: cleared `description` in live promoted with `--target core`
    core_c, man_c = _make_cymbal_source(tmp_path / "repo_c")
    dist_c = tmp_path / "dist_c"
    build_all(core_c, man_c, dist_c, "prod", write_locks=True)
    live_desc = _materialize_ces_export(
        dist_c / "cymbal_direct/digital_app", tmp_path / "live_desc"
    )
    guide_json = _read_json(live_desc / "agents/guide_agent/guide_agent.json")
    guide_json.pop("description", None)
    _write(live_desc / "agents/guide_agent/guide_agent.json", guide_json)

    backport_live_to_source(
        core_c,
        man_c,
        live_desc,
        dist_dir=dist_c / "cymbal_direct/digital_app",
        target="core",
        run_lint=False,
    )
    assert (
        check_drift(
            "cymbal_direct/digital_app",
            dist_c / "cymbal_direct/digital_app",
            live_desc,
            lockfile_path=dist_c / "cymbal_direct/digital_app.lock.json",
        ).is_clean
        is True
    )


# RL-PENDING-4
def test_rl_pending_4_single_line_substring_replace_lines_preserves_context(
    tmp_path: Path,
) -> None:
    """RL-PENDING-4: single-line substring replace_lines keeps surrounding."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "agents:\n"
        "  cards_agent:\n"
        "    replace_lines:\n"
        "      - match: card controls\n"
        "        with: digital card limits\n",
    )
    out = tmp_path / "out"
    build_app(core, man, "cymbal_direct", "DIGITAL_APP", "prod", out)
    lines = (
        (out / "agents/cards_agent/instruction.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert lines[0] == (
        "Assist Cymbal Direct cardholders with digital card limits."
    )


# RL-PENDING-5
def test_rl_pending_5_backport_does_not_clobber_unpushed_sibling_lockfiles(
    tmp_path: Path,
) -> None:
    """RL-PENDING-5: backport must not overwrite unpushed sibling lockfiles."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    # Snapshot the currently deployed live state of cymbal_retail/digital_app
    live_retail_deployed = _materialize_ces_export(
        dist / "cymbal_retail/digital_app", tmp_path / "live_retail_deployed"
    )

    # Edit cymbal_direct/digital_app in Console and backport to shared core/
    live_direct_edited = _materialize_ces_export(
        dist / "cymbal_direct/digital_app", tmp_path / "live_direct_edited"
    )
    (live_direct_edited / "agents/guide_agent/instruction.txt").write_text(
        "Answer Cymbal Direct banking questions clearly using "
        "{@TOOL: kb_search}.\n",
        encoding="utf-8",
    )
    backport_live_to_source(
        core,
        man,
        live_direct_edited,
        dist_dir=dist,
        brand_id="cymbal_direct",
        channel="DIGITAL_APP",
        environment="prod",
        target="core",
    )

    # Promoting the recompiled cymbal_retail/digital_app against its untouched
    # deployed CES state must pass the pre-push 3-way drift gate cleanly.
    gate_report = enforce_pre_push_gate(
        dist / "cymbal_retail/digital_app",
        app_id="cymbal_retail/digital_app",
        live_dir=live_retail_deployed,
        lockfile_path=dist / "cymbal_retail/digital_app.lock.json",
    )
    assert gate_report is not None
    assert gate_report.has_unresolved is False


# RL-PENDING-6
def test_rl_pending_6_promotion_with_new_live_edit_promotes_existing_overrides(
    tmp_path: Path,
) -> None:
    """RL-PENDING-6: promotion lifts prior overrides alongside new edits."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    live = _materialize_ces_export(
        dist / "cymbal_direct/digital_app", tmp_path / "live"
    )
    # Step 1: channel-scoped override on guide_agent + cards_agent description
    (live / "agents/guide_agent/instruction.txt").write_text(
        "Answer Cymbal Direct digital questions using {@TOOL: kb_search}.\n",
        encoding="utf-8",
    )
    live_cards = _read_json(live / "agents/cards_agent/cards_agent.json")
    live_cards["description"] = "Updated Cymbal Direct card specialist."
    _write(live / "agents/cards_agent/cards_agent.json", live_cards)
    backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist,
        brand_id="cymbal_direct",
        channel="DIGITAL_APP",
        environment="prod",
        target="brand",
        scope="channel",
    )

    # Step 2: fresh Console edit on cards_agent instruction + promote to brand
    (live / "agents/cards_agent/instruction.txt").write_text(
        "Assist Cymbal Direct cardholders quickly before calling "
        "{@TOOL: update_card_limit}.\n",
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
    ov_brand = yaml.safe_load(
        (man / "brands/cymbal_direct/overrides.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert "channels" not in ov_brand["agents"]["guide_agent"]
    assert "digital questions" in (
        dist
        / "cymbal_direct/voice_telephony/agents/guide_agent/instruction.txt"
    ).read_text(encoding="utf-8")

    # Step 3: another Console edit on root_agent + promote all to shared core/
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
    assert not (man / "brands/cymbal_direct/overrides.yaml").exists()
    assert (
        _read_json(core / "agents/cards_agent/cards_agent.json")["description"]
        == "Updated @@brand.display_name@@ card specialist."
    )
    assert "digital questions" in (
        core / "agents/guide_agent/instruction.txt"
    ).read_text(encoding="utf-8")


# RL-PENDING-7
def test_rl_pending_7_optional_instruction_files_in_build_and_backport(
    tmp_path: Path,
) -> None:
    """RL-PENDING-7: optional global_instruction.txt and instruction.txt."""
    # Case A: missing core global_instruction.txt + append_rules override
    core, man = _make_cymbal_source(tmp_path / "repo_a")
    (core / "global_instruction.txt").unlink()
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "global_instruction:\n"
        "  append_rules:\n"
        "    - Always greet @@brand.display_name@@ customers politely.\n",
    )
    dist_a = tmp_path / "dist_a"
    build_all(core, man, dist_a, "prod", write_locks=True)
    gi_compiled = (
        dist_a / "cymbal_direct/digital_app/global_instruction.txt"
    ).read_text(encoding="utf-8")
    assert gi_compiled.startswith("# Brand: Cymbal Direct\n")
    assert "Always greet Cymbal Direct customers politely." in gi_compiled

    live_a = _materialize_ces_export(
        dist_a / "cymbal_direct/digital_app", tmp_path / "live_a"
    )
    (live_a / "global_instruction.txt").write_text(
        gi_compiled + "- Keep Cymbal Direct responses concise.\n",
        encoding="utf-8",
    )
    backport_live_to_source(
        core,
        man,
        live_a,
        dist_dir=dist_a / "cymbal_direct/digital_app",
        run_lint=False,
    )
    assert (
        check_drift(
            "cymbal_direct/digital_app",
            dist_a / "cymbal_direct/digital_app",
            live_a,
            lockfile_path=dist_a / "cymbal_direct/digital_app.lock.json",
        ).is_clean
        is True
    )

    # Case B: agent without instruction.txt whose description changes in live
    core_b, man_b = _make_cymbal_source(tmp_path / "repo_b")
    (core_b / "agents/guide_agent/instruction.txt").unlink()
    guide_cfg = _read_json(core_b / "agents/guide_agent/guide_agent.json")
    guide_cfg.pop("instruction", None)
    _write(core_b / "agents/guide_agent/guide_agent.json", guide_cfg)
    dist_b = tmp_path / "dist_b"
    build_all(core_b, man_b, dist_b, "prod", write_locks=True)

    live_b = _materialize_ces_export(
        dist_b / "cymbal_direct/digital_app", tmp_path / "live_b"
    )
    live_guide = _read_json(live_b / "agents/guide_agent/guide_agent.json")
    live_guide["description"] = "Updated Cymbal Direct guide description."
    _write(live_b / "agents/guide_agent/guide_agent.json", live_guide)

    backport_live_to_source(
        core_b,
        man_b,
        live_b,
        dist_dir=dist_b / "cymbal_direct/digital_app",
        run_lint=False,
    )
    assert (
        check_drift(
            "cymbal_direct/digital_app",
            dist_b / "cymbal_direct/digital_app",
            live_b,
            lockfile_path=dist_b / "cymbal_direct/digital_app.lock.json",
        ).is_clean
        is True
    )


# RL-PENDING-8
def test_rl_pending_8_malformed_openapi_yaml_and_cli_manifest_error_caught(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """RL-PENDING-8: corrupt OpenAPI YAML and CLI ManifestError handling."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    live = _materialize_ces_export(
        dist / "cymbal_direct/digital_app", tmp_path / "live"
    )
    (
        live / "tools/update_card_limit/open_api_tool/open_api_schema.yaml"
    ).write_text("openapi: [unclosed\n", encoding="utf-8")

    with pytest.raises(BuildGateError):
        backport_live_to_source(
            core,
            man,
            live,
            dist_dir=dist / "cymbal_direct/digital_app",
            target="core",
            run_lint=False,
        )

    # CLI `cxas build` must catch ManifestError from invalid overrides.yaml
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_retail\n",
    )
    argv = [
        "cxas",
        "build",
        "--core-dir",
        str(core),
        "--manifests-dir",
        str(man),
        "--brand",
        "cymbal_direct",
        "--channel",
        "DIGITAL_APP",
        "--env",
        "prod",
        "--out",
        str(tmp_path / "out"),
    ]
    with (
        mock.patch.object(sys, "argv", argv),
        pytest.raises(SystemExit) as exc_info,
    ):
        cli_main.main()
    assert exc_info.value.code == 1
    assert "cymbal_retail" in capsys.readouterr().err


# RL-PENDING-9
def test_rl_pending_9_partial_dist_does_not_misidentify_channel_or_brand(
    tmp_path: Path,
) -> None:
    """RL-PENDING-9: partial dist must never infer the wrong channel/brand."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    _write(
        core / "agents/guide_agent/instruction.txt",
        "{@startChannel_DIGITAL_APP}\n"
        "Share self-service links on @@brand.urls.portal@@ via "
        "{@TOOL: kb_search}.\n"
        "{@endChannel_DIGITAL_APP}\n"
        "{@startChannel_VOICE_TELEPHONY}\n"
        "Speak short phone answers for @@brand.display_name@@ via "
        "{@TOOL: kb_search}.\n"
        "{@endChannel_VOICE_TELEPHONY}\n",
    )
    full_dist = tmp_path / "full_dist"
    build_all(core, man, full_dist, "prod")

    live_digital = _materialize_ces_export(
        full_dist / "cymbal_direct/digital_app", tmp_path / "live_digital"
    )
    (live_digital / "agents/guide_agent/instruction.txt").write_text(
        "Share updated self-service links on "
        "https://portal.cymbal-direct.example via {@TOOL: kb_search}.\n",
        encoding="utf-8",
    )

    # Only VOICE_TELEPHONY was previously compiled into partial_dist
    partial_dist = tmp_path / "partial_dist"
    build_app(
        core,
        man,
        "cymbal_direct",
        "VOICE_TELEPHONY",
        "prod",
        partial_dist / "cymbal_direct/voice_telephony",
    )

    caught_err: str | None = None
    res = None
    try:
        res = backport_live_to_source(
            core,
            man,
            live_digital,
            dist_dir=partial_dist,
            brand_id="cymbal_direct",
            environment="prod",
        )
    except BuildGateError as exc:
        caught_err = str(exc)

    if caught_err is not None:
        assert "Could not resolve" in caught_err
        assert not (man / "brands/cymbal_direct/overrides.yaml").exists()
    else:
        assert res is not None
        assert res.app_id == "cymbal_direct/digital_app"
        ov = yaml.safe_load(
            (man / "brands/cymbal_direct/overrides.yaml").read_text(
                encoding="utf-8"
            )
        )
        assert "VOICE_TELEPHONY" not in ov["agents"]["guide_agent"].get(
            "channels", {}
        )


# RL-PENDING-10
def test_rl_pending_10_cleared_tool_description_and_brand_field_removal(
    tmp_path: Path,
) -> None:
    """RL-PENDING-10: cleared tool description on core and brand removal."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    # Part A: clearing tool description with `--target core` must round-trip
    live_tool = _materialize_ces_export(
        dist / "cymbal_direct/digital_app", tmp_path / "live_tool"
    )
    kb = _read_json(live_tool / "tools/kb_search/kb_search.json")
    kb["dataStoreTool"].pop("description", None)
    _write(live_tool / "tools/kb_search/kb_search.json", kb)

    backport_live_to_source(
        core,
        man,
        live_tool,
        dist_dir=dist / "cymbal_direct/digital_app",
        target="core",
        run_lint=False,
    )
    assert (
        check_drift(
            "cymbal_direct/digital_app",
            dist / "cymbal_direct/digital_app",
            live_tool,
            lockfile_path=dist / "cymbal_direct/digital_app.lock.json",
        ).is_clean
        is True
    )

    # Part B: clearing agent description with `--target brand` fails closed
    # up front (not with a generic round-trip verification error)
    core_b, man_b = _make_cymbal_source(tmp_path / "repo_b")
    dist_b = tmp_path / "dist_b"
    build_all(core_b, man_b, dist_b, "prod", write_locks=True)
    live_ag = _materialize_ces_export(
        dist_b / "cymbal_direct/digital_app", tmp_path / "live_ag"
    )
    gj = _read_json(live_ag / "agents/guide_agent/guide_agent.json")
    gj.pop("description", None)
    _write(live_ag / "agents/guide_agent/guide_agent.json", gj)

    with pytest.raises(BuildGateError) as exc_info:
        backport_live_to_source(
            core_b,
            man_b,
            live_ag,
            dist_dir=dist_b / "cymbal_direct/digital_app",
            target="brand",
            run_lint=False,
        )
    assert "Round-trip verification failed" not in str(exc_info.value)
    assert "description" in str(exc_info.value)


# RL-PENDING-11
def test_rl_pending_11_pruned_display_name_warnings_preserved(
    tmp_path: Path,
) -> None:
    """RL-PENDING-11: pruned warnings cover both dir name and displayName."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    gj = _read_json(core / "agents/guide_agent/guide_agent.json")
    gj["displayName"] = "Cymbal Guide Specialist"
    _write(core / "agents/guide_agent/guide_agent.json", gj)

    # Disabled via displayName while root_agent mentions `{@AGENT: guide_agent}`
    _write(
        man / "brands/cymbal_direct/channel_policy.yaml",
        "brand_id: cymbal_direct\n"
        "api_routing:\n"
        "  cards_api: https://cards.cymbal-direct.example\n"
        "channels:\n"
        "  DIGITAL_APP:\n"
        "    disabled_agents:\n"
        "      - Cymbal Guide Specialist\n"
        "    supported_modalities: ['TEXT']\n"
        "  VOICE_TELEPHONY:\n"
        "    supported_modalities: ['AUDIO']\n",
    )
    rep_a = build_app(
        core,
        man,
        "cymbal_direct",
        "DIGITAL_APP",
        "prod",
        tmp_path / "out_a",
    )
    assert not (tmp_path / "out_a/agents/guide_agent").exists()
    assert any("guide_agent" in w for w in rep_a.warnings)

    # Disabled via dir name while override mentions `{@AGENT: displayName}`
    _write(
        man / "brands/cymbal_direct/channel_policy.yaml",
        "brand_id: cymbal_direct\n"
        "api_routing:\n"
        "  cards_api: https://cards.cymbal-direct.example\n"
        "channels:\n"
        "  DIGITAL_APP:\n"
        "    disabled_agents:\n"
        "      - guide_agent\n"
        "    supported_modalities: ['TEXT']\n"
        "  VOICE_TELEPHONY:\n"
        "    supported_modalities: ['AUDIO']\n",
    )
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "agents:\n"
        "  root_agent:\n"
        "    instruction: |\n"
        "      Route general requests to {@AGENT: Cymbal Guide Specialist}.\n",
    )
    rep_b = build_app(
        core,
        man,
        "cymbal_direct",
        "DIGITAL_APP",
        "prod",
        tmp_path / "out_b",
    )
    assert any("Cymbal Guide Specialist" in w for w in rep_b.warnings)


# RL-PENDING-12
def test_rl_pending_12_core_backport_reverts_tool_override_to_baseline(
    tmp_path: Path,
) -> None:
    """RL-PENDING-12: --target core handles reverted tool overrides cleanly."""
    # Part A: revert brand tool description override back to core description
    core, man = _make_cymbal_source(tmp_path / "repo_a")
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "tools:\n"
        "  kb_search:\n"
        "    description: Custom Cymbal Direct KB description.\n",
    )
    dist = tmp_path / "dist_a"
    build_all(core, man, dist, "prod", write_locks=True)

    live = _materialize_ces_export(
        dist / "cymbal_direct/digital_app", tmp_path / "live_a"
    )
    kb = _read_json(live / "tools/kb_search/kb_search.json")
    kb["dataStoreTool"]["description"] = "Search Cymbal Direct help articles."
    _write(live / "tools/kb_search/kb_search.json", kb)

    backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist / "cymbal_direct/digital_app",
        target="core",
    )
    assert not (man / "brands/cymbal_direct/overrides.yaml").exists()
    assert (
        check_drift(
            "cymbal_direct/digital_app",
            dist / "cymbal_direct/digital_app",
            live,
            lockfile_path=dist / "cymbal_direct/digital_app.lock.json",
        ).is_clean
        is True
    )

    # Part B: core tool had no description, override added one, live clears it
    core_b, man_b = _make_cymbal_source(tmp_path / "repo_b")
    core_kb = _read_json(core_b / "tools/kb_search/kb_search.json")
    core_kb["dataStoreTool"].pop("description", None)
    _write(core_b / "tools/kb_search/kb_search.json", core_kb)
    _write(
        man_b / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "tools:\n"
        "  kb_search:\n"
        "    description: Custom Cymbal Direct KB description.\n",
    )
    dist_b = tmp_path / "dist_b"
    build_all(core_b, man_b, dist_b, "prod", write_locks=True)

    live_b = _materialize_ces_export(
        dist_b / "cymbal_direct/digital_app", tmp_path / "live_b"
    )
    kb_b = _read_json(live_b / "tools/kb_search/kb_search.json")
    kb_b["dataStoreTool"].pop("description", None)
    _write(live_b / "tools/kb_search/kb_search.json", kb_b)

    backport_live_to_source(
        core_b,
        man_b,
        live_b,
        dist_dir=dist_b / "cymbal_direct/digital_app",
        target="core",
        run_lint=False,
    )
    assert not (man_b / "brands/cymbal_direct/overrides.yaml").exists()
    assert (
        check_drift(
            "cymbal_direct/digital_app",
            dist_b / "cymbal_direct/digital_app",
            live_b,
            lockfile_path=dist_b / "cymbal_direct/digital_app.lock.json",
        ).is_clean
        is True
    )
