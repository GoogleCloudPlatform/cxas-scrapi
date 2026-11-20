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

"""Adversarial diff-review regression tests (RL-PENDING-1 .. RL-PENDING-10)."""

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
    extract_normalized_resources,
    load_manifests,
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
        "Route general banking questions to {@AGENT: guide_agent}.\n",
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
        "          description: Card limit updated\n"
        "  /v2/cards/limit:\n"
        "    post:\n"
        "      operationId: updateCardLimitV2\n"
        "      responses:\n"
        "        '200':\n"
        "          description: Card limit updated v2\n",
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
        "lexicon:\n"
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
        "lexicon:\n"
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


def _export_ces_live(bundle_dir: Path, live_dir: Path) -> Path:
    if live_dir.exists():
        shutil.rmtree(live_dir)
    shutil.copytree(bundle_dir, live_dir)
    shutil.rmtree(live_dir / "deployments", ignore_errors=True)
    app_data = _read_json(live_dir / "app.json")
    app_data.pop("locked", None)
    _write(live_dir / "app.json", app_data)
    return live_dir


# RL-PENDING-1
def test_rl_pending_1_three_way_backport_preserves_git_ahead_resources(
    tmp_path: Path,
) -> None:
    """RL-PENDING-1: When a baseline lockfile exists and Git (`ours`) has
    advanced `guide_agent` while CES Console (`live`) only edited `cards_agent`
    (`live == base` on `guide_agent`), `backport_live_to_source` must NOT
    overwrite Git's `guide_agent` commit with stale `live` state."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)
    lockfile = dist / "cymbal_direct/digital_app.lock.json"

    # Console export starts at v1 and edits ONLY cards_agent.
    live = _export_ces_live(
        dist / "cymbal_direct/digital_app", tmp_path / "live"
    )
    cards_live = live / "agents/cards_agent/instruction.txt"
    cards_live.write_text(
        cards_live.read_text(encoding="utf-8")
        + "- Console-added card verification rule.\n",
        encoding="utf-8",
    )

    # Meanwhile, Git advances guide_agent (not yet deployed to live).
    git_guide_text = (
        "Answer @@brand.display_name@@ banking questions using "
        "{@TOOL: kb_search}.\n"
        "- Committed Git-side guide rule that must survive backport.\n"
    )
    (core / "agents/guide_agent/instruction.txt").write_text(
        git_guide_text, encoding="utf-8"
    )

    res = backport_live_to_source(
        core,
        man,
        live,
        brand_id="cymbal_direct",
        channel="DIGITAL_APP",
        environment="prod",
        lockfile_path=lockfile,
        target="core",
    )

    assert [c.resource_id for c in res.changes] == ["agents/cards_agent"]
    assert "Committed Git-side guide rule that must survive backport." in (
        core / "agents/guide_agent/instruction.txt"
    ).read_text(encoding="utf-8")


# RL-PENDING-2
@pytest.mark.parametrize(
    "malicious_id",
    [
        "../..",
        ".",
        "cards_agent/..",
    ],
)
def test_rl_pending_2_override_keys_reject_path_traversal_and_abs_paths(
    tmp_path: Path, malicious_id: str
) -> None:
    """RL-PENDING-2: `BrandOverrides.agents` / `tools` keys containing `..`,
    `.`, or path separators must fail closed (`ManifestError` or `BuildError`)
    and never write files outside `out_dir`."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    victim_dir = tmp_path / "victim_dir"
    victim_dir.mkdir(parents=True, exist_ok=True)

    for key_cand in (malicious_id, str(victim_dir)):
        _write(
            man / "brands/cymbal_direct/overrides.yaml",
            yaml.safe_dump(
                {
                    "brand_id": "cymbal_direct",
                    "agents": {
                        key_cand: {"instruction": "ESCAPED_OUTSIDE_OUT_DIR\n"}
                    },
                },
                sort_keys=False,
            ),
        )
        out_dir = tmp_path / "dist" / "out"
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        with pytest.raises((ManifestError, BuildError)):
            build_app(
                core, man, "cymbal_direct", "DIGITAL_APP", "prod", out_dir
            )

        assert not (tmp_path / "dist" / "instruction.txt").exists()
        assert not (victim_dir / "instruction.txt").exists()


# RL-PENDING-3
@pytest.mark.parametrize("omitted_key", ["temperature", "model"])
def test_rl_pending_3_omitted_live_model_settings_key_does_not_keyerror(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], omitted_key: str
) -> None:
    """RL-PENDING-3: When `live` omits `temperature` or `model` present in
    baseline `modelSettings`, `cxas backport --json` must not crash with an
    unhandled `KeyError`."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    live = _export_ces_live(
        dist / "cymbal_direct/digital_app", tmp_path / f"live_{omitted_key}"
    )
    cj_path = live / "agents/cards_agent/cards_agent.json"
    cj = _read_json(cj_path)
    cj["modelSettings"].pop(omitted_key, None)
    _write(cj_path, cj)

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
        "--no-lint",
        "--json",
    ]
    exit_code = 0
    try:
        with mock.patch.object(sys, "argv", argv):
            cli_main.main()
    except SystemExit as exc:
        exit_code = int(exc.code or 0)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code in (0, 1)
    assert "passed" in payload
    if payload["passed"] is False:
        assert omitted_key in payload["error"]


# RL-PENDING-4
def test_rl_pending_4_single_line_unique_substring_replace_preserves_context(
    tmp_path: Path,
) -> None:
    """RL-PENDING-4: `LineReplacement` unique-substring match (`span == 1`)
    must replace only the matched substring on the line rather than wiping out
    the surrounding text on that line."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "agents:\n"
        "  cards_agent:\n"
        "    replace_lines:\n"
        "      - match: card controls\n"
        "        with: digital card freeze and controls\n",
    )
    out = tmp_path / "out"
    build_app(core, man, "cymbal_direct", "DIGITAL_APP", "prod", out)
    first_line = (
        (out / "agents/cards_agent/instruction.txt")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert first_line == (
        "Assist Cymbal Direct cardholders with digital card freeze and "
        "controls."
    )


# RL-PENDING-5
def test_rl_pending_5a_reverse_tokens_never_nests_placeholders_in_brand_yaml(
    tmp_path: Path,
) -> None:
    """RL-PENDING-5a: `_reverse_tokens` / `_reverse_url_tokens` must not
    substitute inside already-emitted `@@...@@` placeholder names when writing
    `overrides.yaml`."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    _write(
        man / "brands/cymbal_direct/identity.yaml",
        "brand_id: cymbal_direct\n"
        "display_name: Cymbal Direct\n"
        "lexicon:\n"
        "  auth_method: Cymbal Pass\n"
        "  portal_noun: portal\n"
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
        "  cymbal_direct_cards: https://cards.cymbal-direct.example\n"
        "channels:\n"
        "  DIGITAL_APP:\n"
        "    supported_modalities: ['TEXT']\n"
        "  VOICE_TELEPHONY:\n"
        "    supported_modalities: ['AUDIO']\n",
    )
    _write(
        man / "brands/cymbal_retail/channel_policy.yaml",
        "brand_id: cymbal_retail\n"
        "api_routing:\n"
        "  cymbal_direct_cards: https://cards.cymbal-retail.example\n"
        "channels:\n"
        "  DIGITAL_APP:\n"
        "    supported_modalities: ['TEXT']\n"
        "  VOICE_TELEPHONY:\n"
        "    supported_modalities: ['AUDIO']\n",
    )
    tj = _read_json(core / "tools/update_card_limit/update_card_limit.json")
    tj["openApiTool"]["url"] = "@@routing.cymbal_direct_cards@@/v1/cards/limit"
    _write(core / "tools/update_card_limit/update_card_limit.json", tj)
    oa_p = core / "tools/update_card_limit/open_api_tool/open_api_schema.yaml"
    oa_p.write_text(
        oa_p.read_text(encoding="utf-8").replace(
            "@@routing.cards_api@@", "@@routing.cymbal_direct_cards@@"
        ),
        encoding="utf-8",
    )

    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)
    live = _export_ces_live(
        dist / "cymbal_direct/digital_app", tmp_path / "live"
    )
    (live / "agents/guide_agent/instruction.txt").write_text(
        "Answer Cymbal Direct questions via {@TOOL: kb_search} and "
        "https://portal.cymbal-direct.example.\n",
        encoding="utf-8",
    )
    ltj = _read_json(live / "tools/update_card_limit/update_card_limit.json")
    ltj["openApiTool"]["url"] = (
        "https://cards.cymbal-direct.example/v2/cards/limit"
    )
    _write(live / "tools/update_card_limit/update_card_limit.json", ltj)

    backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist / "cymbal_direct/digital_app",
        target="brand",
        scope="channel",
    )
    ov_text = (man / "brands/cymbal_direct/overrides.yaml").read_text(
        encoding="utf-8"
    )
    assert "@@brand.urls.portal@@" in ov_text
    assert "@@routing.cymbal_direct_cards@@/v2/cards/limit" in ov_text
    assert "@@brand.urls.@@" not in ov_text
    assert "@@routing.@@" not in ov_text


def test_rl_pending_5b_core_backport_skips_nested_and_brand_only_tokens(
    tmp_path: Path,
) -> None:
    """RL-PENDING-5b: `--target core` must not emit nested placeholders or
    brand-only lexicon keys that break compilation on sister brands."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    _write(
        man / "brands/cymbal_direct/identity.yaml",
        "brand_id: cymbal_direct\n"
        "display_name: Cymbal Direct\n"
        "lexicon:\n"
        "  auth_method: Cymbal Pass\n"
        "  portal_noun: portal\n"
        "  direct_only_badge: Cymbal Wallet\n"
        "urls:\n"
        "  portal: https://portal.cymbal-direct.example\n"
        "tts_voices:\n"
        "  en-US:\n"
        "    voice: en-US-Chirp3-HD-Puck\n"
        "    speaking_rate: 1.0\n",
    )
    _write(
        man / "brands/cymbal_retail/identity.yaml",
        "brand_id: cymbal_retail\n"
        "display_name: Cymbal Retail\n"
        "lexicon:\n"
        "  auth_method: Cymbal Pass\n"
        "  portal_noun: online banking\n"
        "urls:\n"
        "  portal: https://portal.cymbal-retail.example\n"
        "tts_voices:\n"
        "  en-US:\n"
        "    voice: en-US-Chirp3-HD-Aoede\n"
        "    speaking_rate: 1.0\n",
    )
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)
    live = _export_ces_live(
        dist / "cymbal_direct/digital_app", tmp_path / "live"
    )
    (live / "agents/guide_agent/instruction.txt").write_text(
        "Answer Cymbal Direct questions via {@TOOL: kb_search}, "
        "https://portal.cymbal-direct.example, and Cymbal Wallet.\n",
        encoding="utf-8",
    )

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
    core_guide = (core / "agents/guide_agent/instruction.txt").read_text(
        encoding="utf-8"
    )
    assert "@@brand.lexicon.direct_only_badge@@" not in core_guide


# RL-PENDING-6
def test_rl_pending_6_trailing_blank_line_in_live_prompt_roundtrips_cleanly(
    tmp_path: Path,
) -> None:
    """RL-PENDING-6: A trailing blank line (`\\n\\n`) in a Console-edited
    `instruction.txt` must round-trip cleanly under `--target brand`."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    live = _export_ces_live(
        dist / "cymbal_direct/digital_app", tmp_path / "live"
    )
    (live / "agents/guide_agent/instruction.txt").write_text(
        "Answer Cymbal Direct banking questions using {@TOOL: kb_search}.\n"
        "- Share self-service links when helpful.\n\n",
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
        extract_normalized_resources(dist / "cymbal_direct/digital_app")[
            "agents/guide_agent"
        ].sha256
        == extract_normalized_resources(live)["agents/guide_agent"].sha256
    )


# RL-PENDING-7
def test_rl_pending_7a_brand_scope_promotes_sibling_resource_with_new_edit(
    tmp_path: Path,
) -> None:
    """RL-PENDING-7a: Running `--target brand --scope brand` when `live` has
    both an existing channel override on `root_agent` AND a new Console tweak
    on `cards_agent` must promote `root_agent` across channels too."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "agents:\n"
        "  root_agent:\n"
        "    channels:\n"
        "      DIGITAL_APP:\n"
        "        append_rules:\n"
        "          - Existing digital routing note for @@brand.display_name@@.\n",
    )
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    live = _export_ces_live(
        dist / "cymbal_direct/digital_app", tmp_path / "live"
    )
    cards_in = live / "agents/cards_agent/instruction.txt"
    cards_in.write_text(
        cards_in.read_text(encoding="utf-8")
        + "- New Console rule on cards_agent.\n",
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
    assert "Existing digital routing note for Cymbal Direct." in (
        dist / "cymbal_direct/voice_telephony/agents/root_agent/instruction.txt"
    ).read_text(encoding="utf-8")


def test_rl_pending_7b_core_target_promotes_overridden_fields_with_new_edit(
    tmp_path: Path,
) -> None:
    """RL-PENDING-7b: Running `--target core` when `live` has both existing
    `overrides.yaml` customizations AND a new Console tweak must promote all
    overridden resources and fields into `core/` and clean `overrides.yaml`."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "agents:\n"
        "  root_agent:\n"
        "    append_rules:\n"
        "      - Existing brand routing note for @@brand.display_name@@.\n"
        "  cards_agent:\n"
        "    description: Overridden @@brand.display_name@@ card specialist.\n",
    )
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    live = _export_ces_live(
        dist / "cymbal_direct/digital_app", tmp_path / "live"
    )
    cards_in = live / "agents/cards_agent/instruction.txt"
    cards_in.write_text(
        cards_in.read_text(encoding="utf-8")
        + "- New Console rule on cards_agent.\n",
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
        == "Overridden @@brand.display_name@@ card specialist."
    )
    assert "Existing brand routing note for @@brand.display_name@@." in (
        core / "agents/root_agent/instruction.txt"
    ).read_text(encoding="utf-8")


# RL-PENDING-8
def test_rl_pending_8a_missing_core_global_instruction_retains_brand_header(
    tmp_path: Path,
) -> None:
    """RL-PENDING-8a: Missing optional `core/global_instruction.txt` must still
    receive the `# Brand:` identity header when `overrides.yaml` specifies
    `global_instruction.append_rules`."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    (core / "global_instruction.txt").unlink()
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "global_instruction:\n"
        "  append_rules:\n"
        "    - Always greet @@brand.display_name@@ users politely.\n",
    )
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    gi_text = (
        dist / "cymbal_direct/digital_app/global_instruction.txt"
    ).read_text(encoding="utf-8")
    assert gi_text.startswith("# Brand: Cymbal Direct\n")
    assert "Always greet Cymbal Direct users politely." in gi_text


def test_rl_pending_8b_agent_without_instruction_file_backports_cleanly(
    tmp_path: Path,
) -> None:
    """RL-PENDING-8b: Agents without an optional `instruction.txt` file must
    backport metadata changes cleanly without failing on FileNotFoundError."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    (core / "agents/guide_agent/instruction.txt").unlink()
    gj = _read_json(core / "agents/guide_agent/guide_agent.json")
    gj.pop("instruction", None)
    _write(core / "agents/guide_agent/guide_agent.json", gj)

    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)
    live = _export_ces_live(
        dist / "cymbal_direct/digital_app", tmp_path / "live"
    )
    lgj = _read_json(live / "agents/guide_agent/guide_agent.json")
    lgj["description"] = "Updated Cymbal Direct guide description."
    _write(live / "agents/guide_agent/guide_agent.json", lgj)

    res = backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist / "cymbal_direct/digital_app",
        target="brand",
        scope="channel",
        run_lint=False,
    )
    assert any(c.resource_id == "agents/guide_agent" for c in res.changes)


# RL-PENDING-9
def test_rl_pending_9_tool_url_override_on_non_openapi_tool_fails_closed(
    tmp_path: Path,
) -> None:
    """RL-PENDING-9: Specifying `url` in `overrides.yaml` for a non-OpenAPI
    tool (`dataStoreTool` / `pythonFunction`) must fail closed with
    `BuildError` instead of being silently ignored."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "tools:\n"
        "  kb_search:\n"
        "    url: https://portal.cymbal-direct.example/invalid\n",
    )
    with pytest.raises(BuildError, match="kb_search"):
        build_app(
            core,
            man,
            "cymbal_direct",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out",
        )


# RL-PENDING-10
def test_rl_pending_10a_cli_build_catches_manifest_error_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """RL-PENDING-10a: `cxas build` must catch `ManifestError`/`BuildGateError`
    cleanly and exit with code 1 instead of crashing with a raw traceback."""
    core, man = _make_cymbal_source(tmp_path / "repo")
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
        "--env",
        "prod",
        "--out",
        str(tmp_path / "out_cli"),
        "--all",
    ]
    with (
        mock.patch.object(sys, "argv", argv),
        pytest.raises(SystemExit) as exc_info,
    ):
        cli_main.main()
    assert exc_info.value.code == 1
    assert "cymbal_retail" in capsys.readouterr().err


def test_rl_pending_10b_compiler_wraps_malformed_openapi_yaml_in_build_error(
    tmp_path: Path,
) -> None:
    """RL-PENDING-10b: `_apply_brand_overrides` must wrap malformed OpenAPI
    YAML in `BuildError` instead of leaking raw `yaml.YAMLError`."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "tools:\n"
        "  update_card_limit:\n"
        "    url: https://cards.cymbal-direct.example/v2/cards/limit\n",
    )
    (
        core / "tools/update_card_limit/open_api_tool/open_api_schema.yaml"
    ).write_text("openapi: [unclosed\n", encoding="utf-8")
    load_manifests(man)
    with pytest.raises(BuildError):
        build_app(
            core,
            man,
            "cymbal_direct",
            "DIGITAL_APP",
            "prod",
            tmp_path / "out_bad_oa",
        )


# RL-PENDING-11
def test_rl_pending_11_backport_promotes_accepted_from_live_hotfix(
    tmp_path: Path,
) -> None:
    """RL-PENDING-11: A Console hotfix previously adopted via
    `cxas drift --accept-live` (`accepted_from_live=True`, `r.status == 'DRIFT'`
    despite `live_sha == base_sha`) must NOT be misclassified into
    `git_ahead_ids` and skipped during `backport_live_to_source`."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)
    lockfile = dist / "cymbal_direct/digital_app.lock.json"

    live = _export_ces_live(
        dist / "cymbal_direct/digital_app", tmp_path / "live"
    )
    cards_in = live / "agents/cards_agent/instruction.txt"
    cards_in.write_text(
        cards_in.read_text(encoding="utf-8")
        + "- Emergency Console hotfix rule for Cymbal Direct.\n",
        encoding="utf-8",
    )

    # Operator adopts the Console hotfix into dist + lockfile via check_drift.
    drift_adopted = check_drift(
        "cymbal_direct/digital_app",
        dist / "cymbal_direct/digital_app",
        live,
        lockfile_path=lockfile,
        accept_live=["agents/cards_agent"],
    )
    assert drift_adopted.has_unresolved is False
    assert (
        _read_json(lockfile)["resources"]["agents/cards_agent"].get(
            "accepted_from_live"
        )
        is True
    )

    # Developer runs backport_live_to_source to lift the hotfix into Git.
    res = backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist / "cymbal_direct/digital_app",
        lockfile_path=lockfile,
        target="brand",
        scope="channel",
    )
    assert [c.resource_id for c in res.changes] == ["agents/cards_agent"]
    assert (man / "brands/cymbal_direct/overrides.yaml").is_file()
    assert "Emergency Console hotfix rule for Cymbal Direct." in (
        dist / "cymbal_direct/digital_app/agents/cards_agent/instruction.txt"
    ).read_text(encoding="utf-8")


# RL-PENDING-12
def test_rl_pending_12a_infer_app_never_misroutes_to_sibling_channel(
    tmp_path: Path,
) -> None:
    """RL-PENDING-12a: When `--channel` is omitted against a multi-channel
    `dist/` whose channels share the same resource set, reverting a channel
    override on `DIGITAL_APP` + editing another agent must fail closed as
    ambiguous instead of silently targeting `VOICE_TELEPHONY`."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "agents:\n"
        "  cards_agent:\n"
        "    channels:\n"
        "      DIGITAL_APP:\n"
        "        append_rules:\n"
        "          - Old digital card note for @@brand.display_name@@.\n",
    )
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    voice_before = (
        dist
        / "cymbal_direct/voice_telephony/agents/guide_agent/instruction.txt"
    ).read_text(encoding="utf-8")

    # Export DIGITAL_APP (no deployments/), revert cards_agent override, and
    # edit guide_agent in Console.
    live = _export_ces_live(
        dist / "cymbal_direct/digital_app", tmp_path / "live_digital"
    )
    (live / "agents/cards_agent/instruction.txt").write_text(
        (
            dist
            / "cymbal_direct/voice_telephony/agents/cards_agent/instruction.txt"
        ).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (live / "agents/guide_agent/instruction.txt").write_text(
        "Answer Cymbal Direct digital questions via {@TOOL: kb_search}.\n",
        encoding="utf-8",
    )

    with pytest.raises(BuildGateError, match=r"Ambiguous|Could not resolve"):
        backport_live_to_source(
            core,
            man,
            live,
            dist_dir=dist,
            environment="prod",
        )

    assert (
        dist
        / "cymbal_direct/voice_telephony/agents/guide_agent/instruction.txt"
    ).read_text(encoding="utf-8") == voice_before


def test_rl_pending_12b_infer_app_rejects_zero_sha_match_bundle(
    tmp_path: Path,
) -> None:
    """RL-PENDING-12b: `_infer_app_from_dist` must not infer an app when 0
    normalized resources in `live` match the single candidate `dist/` bundle."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    shutil.rmtree(man / "brands/cymbal_retail")
    _write(
        man / "brands/cymbal_direct/channel_policy.yaml",
        "brand_id: cymbal_direct\n"
        "api_routing:\n"
        "  cards_api: https://cards.cymbal-direct.example\n"
        "channels:\n"
        "  DIGITAL_APP:\n"
        "    supported_modalities: ['TEXT']\n",
    )
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    # Create a completely distinct live export with 0 matching SHA-256 hashes.
    unrelated = tmp_path / "unrelated_live"
    _write(
        unrelated / "app.json",
        {
            "displayName": "Unrelated Cymbal App",
            "rootAgent": "other_root",
            "modelSettings": {"model": GA_AGENT_MODEL},
        },
    )
    _write(
        unrelated / "agents/other_root/other_root.json",
        {
            "displayName": "other_root",
            "modelSettings": {"model": GA_AGENT_MODEL},
        },
    )

    with pytest.raises(BuildGateError, match="Could not resolve"):
        backport_live_to_source(
            core,
            man,
            unrelated,
            dist_dir=dist,
            environment="prod",
        )


# RL-PENDING-13
def test_rl_pending_13_pruned_warnings_cover_display_name_and_dir_id(
    tmp_path: Path,
) -> None:
    """RL-PENDING-13: `_pruned_reference_warnings` must warn when an agent or
    tool is disabled by `displayName` and referenced by directory ID (or
    disabled by directory ID and referenced by `displayName`)."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    _write(
        core / "agents/branch_dir/branch_dir.json",
        {
            "displayName": "branch_locator",
            "description": "Branch specialist for @@brand.display_name@@.",
            "instruction": "agents/branch_dir/instruction.txt",
            "modelSettings": {"model": GA_AGENT_MODEL},
        },
    )
    _write(core / "agents/branch_dir/instruction.txt", "Locate branches.\n")
    _write(
        core / "tools/branch_tool_dir/branch_tool_dir.json",
        {
            "displayName": "branch_finder",
            "pythonFunction": {
                "name": "branch_finder",
                "description": "Find Cymbal branches.",
            },
        },
    )
    _write(
        man / "brands/cymbal_direct/channel_policy.yaml",
        "brand_id: cymbal_direct\n"
        "api_routing:\n"
        "  cards_api: https://cards.cymbal-direct.example\n"
        "channels:\n"
        "  DIGITAL_APP:\n"
        "    disabled_agents:\n"
        "      - branch_locator\n"
        "    disabled_tools:\n"
        "      - branch_tool_dir\n"
        "    supported_modalities: ['TEXT']\n",
    )
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "agents:\n"
        "  root_agent:\n"
        "    append_rules:\n"
        "      - 'Avoid {@AGENT: branch_dir} and {@TOOL: branch_finder}.'\n",
    )

    rep = build_app(
        core,
        man,
        "cymbal_direct",
        "DIGITAL_APP",
        "prod",
        tmp_path / "out",
    )
    assert any("branch_dir" in w for w in rep.warnings)
    assert any("branch_finder" in w for w in rep.warnings)
