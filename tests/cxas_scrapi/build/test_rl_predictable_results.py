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

"""Failing reproduction tests for predictable-results review findings."""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from cxas_scrapi.build import backport_live_to_source, build_all
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


def _snapshot_tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


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
        ["git", "-C", str(repo_dir), "commit", "-m", "Initial Cymbal source"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _make_cymbal_source(root: Path) -> tuple[Path, Path]:
    core = root / "core"
    _write(
        core / "app.json",
        {
            "name": "@@brand.brand_id@@-assistant",
            "displayName": "@@brand.display_name@@ Assistant",
            "rootAgent": "root_agent",
            "modelSettings": {"model": GA_AGENT_MODEL},
        },
    )
    _write(
        core / "global_instruction.txt",
        "You represent @@brand.display_name@@ across every conversation.\n"
        "Verify sensitive actions via @@brand.urls.portal@@.\n",
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
        "Route branch questions to {@AGENT: branch_agent}.\n"
        "{@endAgentEnabled_branch_agent}\n"
        "Route card requests to {@AGENT: cards_agent}.\n"
        "Route general questions to {@AGENT: guide_agent}.\n",
    )
    _write(
        core / "agents/branch_agent/branch_agent.json",
        {
            "displayName": "branch_agent",
            "description": "Branch specialist for @@brand.display_name@@.",
            "instruction": "agents/branch_agent/instruction.txt",
            "modelSettings": {"model": GA_AGENT_MODEL},
        },
    )
    _write(
        core / "agents/branch_agent/instruction.txt",
        "Assist with @@brand.display_name@@ branch locations.\n",
    )
    _write(
        core / "agents/cards_agent/cards_agent.json",
        {
            "displayName": "cards_agent",
            "description": "Card specialist for @@brand.display_name@@.",
            "instruction": "agents/cards_agent/instruction.txt",
            "modelSettings": {"model": GA_AGENT_MODEL, "temperature": 0.2},
            "tools": ["update_card_limit"],
        },
    )
    _write(
        core / "agents/cards_agent/instruction.txt",
        "Assist @@brand.display_name@@ cardholders using "
        "{@TOOL: update_card_limit}.\n",
    )
    _write(
        core / "agents/guide_agent/guide_agent.json",
        {
            "displayName": "guide_agent",
            "description": "General guide for @@brand.display_name@@.",
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
                "description": "Update @@brand.display_name@@ card limit.",
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
        "          description: Updated\n",
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

    man = root / "manifests"
    _write(
        man / "brands/cymbal_retail/identity.yaml",
        "brand_id: cymbal_retail\n"
        "display_name: Cymbal Retail\n"
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
        "    disabled_agents:\n"
        "      - branch_agent\n"
        "    supported_modalities: ['TEXT']\n"
        "  VOICE_TELEPHONY:\n"
        "    disabled_agents:\n"
        "      - branch_agent\n"
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


def _export_ces_live_copy(bundle_dir: Path, live_dir: Path) -> Path:
    if live_dir.exists():
        shutil.rmtree(live_dir)
    shutil.copytree(bundle_dir, live_dir)
    dep_dir = live_dir / "deployments"
    if dep_dir.exists():
        shutil.rmtree(dep_dir)
    app_data = _read_json(live_dir / "app.json")
    app_data.pop("locked", None)
    _write(live_dir / "app.json", app_data)
    return live_dir


# RL-PENDING-1: Repeat runs of backport (--scope brand and --target core)
# must be idempotent (Run 1 completes promotion; Run 2 is a no-op).
def test_rl_pending_1_backport_scope_brand_and_core_are_idempotent(
    tmp_path: Path,
) -> None:
    # Subcase A: --target brand --scope brand with existing channel override +
    # new Console edit on another agent.
    core_a, man_a = _make_cymbal_source(tmp_path / "case_a/repo")
    _write(
        man_a / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "agents:\n"
        "  cards_agent:\n"
        "    channels:\n"
        "      DIGITAL_APP:\n"
        "        description: Channel-scoped @@brand.display_name@@ cards.\n",
    )
    dist_a = tmp_path / "case_a/dist"
    build_all(core_a, man_a, dist_a, "prod", write_locks=True)
    live_a = _export_ces_live_copy(
        dist_a / "cymbal_direct/digital_app", tmp_path / "case_a/live"
    )
    (live_a / "agents/guide_agent/instruction.txt").write_text(
        "Answer Cymbal Direct banking questions clearly using "
        "{@TOOL: kb_search}.\n",
        encoding="utf-8",
    )

    res_a1 = backport_live_to_source(
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
    man_a_after_1 = _snapshot_tree_bytes(man_a)
    dist_a_after_1 = _snapshot_tree_bytes(dist_a)

    res_a2 = backport_live_to_source(
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
    assert res_a1.changes
    assert res_a2.changes == []
    assert res_a2.modified_files == []
    assert _snapshot_tree_bytes(man_a) == man_a_after_1
    assert _snapshot_tree_bytes(dist_a) == dist_a_after_1

    # Subcase B: --target core with existing brand override on cards_agent
    # description + Console edit on cards_agent instruction.txt.
    core_b, man_b = _make_cymbal_source(tmp_path / "case_b/repo")
    _write(
        man_b / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "agents:\n"
        "  cards_agent:\n"
        "    description: Overridden @@brand.display_name@@ card specialist.\n",
    )
    dist_b = tmp_path / "case_b/dist"
    build_all(core_b, man_b, dist_b, "prod", write_locks=True)
    live_b = _export_ces_live_copy(
        dist_b / "cymbal_direct/digital_app", tmp_path / "case_b/live"
    )
    (live_b / "agents/cards_agent/instruction.txt").write_text(
        "Assist Cymbal Direct cardholders promptly using "
        "{@TOOL: update_card_limit}.\n",
        encoding="utf-8",
    )

    res_b1 = backport_live_to_source(
        core_b,
        man_b,
        live_b,
        dist_dir=dist_b,
        brand_id="cymbal_direct",
        channel="DIGITAL_APP",
        environment="prod",
        target="core",
    )
    core_b_after_1 = _snapshot_tree_bytes(core_b)
    man_b_after_1 = _snapshot_tree_bytes(man_b)

    res_b2 = backport_live_to_source(
        core_b,
        man_b,
        live_b,
        dist_dir=dist_b,
        brand_id="cymbal_direct",
        channel="DIGITAL_APP",
        environment="prod",
        target="core",
    )
    assert res_b1.changes
    assert res_b2.changes == []
    assert res_b2.modified_files == []
    assert _snapshot_tree_bytes(core_b) == core_b_after_1
    assert _snapshot_tree_bytes(man_b) == man_b_after_1


# RL-PENDING-2: Documented `cxas backport` CLI commands in docs/guides/build.md
# must execute cleanly against a realistic CES live export (no deployments/).
def test_rl_pending_2_docs_build_guide_backport_examples_execute_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    guide_text = (repo_root / "docs/guides/build.md").read_text("utf-8")
    bash_blocks = re.findall(r"```bash\n(.*?)```", guide_text, flags=re.DOTALL)
    backport_blocks = [b for b in bash_blocks if "cxas backport" in b]
    assert backport_blocks, "Expected cxas backport bash block in build.md"

    commands: list[list[str]] = []
    normalized = backport_blocks[0].replace("\\\n", " ")
    for raw_line in normalized.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("cxas backport "):
            commands.append(shlex.split(stripped))
    assert len(commands) >= 3

    ws = tmp_path / "workspace"
    core, man = _make_cymbal_source(ws)
    _init_git_repo(ws, branch="main")
    dist = ws / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    live = _export_ces_live_copy(
        dist / "cymbal_direct/digital_app",
        ws / "exports/cymbal_direct_digital_live",
    )
    (live / "agents/guide_agent/instruction.txt").write_text(
        "Answer Cymbal Direct questions clearly using {@TOOL: kb_search}.\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(ws)
    for argv in commands:
        with mock.patch.object(sys, "argv", argv):
            cli_main.main()


# RL-PENDING-3: Passing `--force-live` to resolve a 3-way conflict must not
# leave undocumented `conflicts/.../*.diff` side-effect files inside the repo
# or dist root on a successful (exit=0) backport.
def test_rl_pending_3_backport_force_live_leaves_no_conflict_side_effects(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    core, man = _make_cymbal_source(repo)
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    live = _export_ces_live_copy(
        dist / "cymbal_direct/digital_app", tmp_path / "live"
    )
    (core / "agents/cards_agent/instruction.txt").write_text(
        "Git-side Cymbal rule using {@TOOL: update_card_limit}.\n",
        encoding="utf-8",
    )
    (live / "agents/cards_agent/instruction.txt").write_text(
        "Console-side Cymbal Direct rule using {@TOOL: update_card_limit}.\n",
        encoding="utf-8",
    )

    backport_live_to_source(
        core,
        man,
        live,
        brand_id="cymbal_direct",
        channel="DIGITAL_APP",
        environment="prod",
        lockfile_path=dist / "cymbal_direct/digital_app.lock.json",
        force_live=["agents/cards_agent"],
    )
    assert not (repo / "conflicts").exists()


# RL-PENDING-4: `cxas backport --no-lint` must not claim `cxas lint: PASS` in
# the generated PR body when the lint gate was skipped.
def test_rl_pending_4_backport_no_lint_pr_body_does_not_claim_lint_passed(
    tmp_path: Path,
) -> None:
    core, man = _make_cymbal_source(tmp_path / "repo")
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    live = _export_ces_live_copy(
        dist / "cymbal_direct/digital_app", tmp_path / "live"
    )
    # Intentional P001 sister-brand mention allowed only because run_lint=False
    (live / "agents/guide_agent/instruction.txt").write_text(
        "Compare Cymbal Retail and Cymbal Direct via {@TOOL: kb_search}.\n",
        encoding="utf-8",
    )
    pr_path = tmp_path / "PR_BODY.md"
    res = backport_live_to_source(
        core,
        man,
        live,
        brand_id="cymbal_direct",
        channel="DIGITAL_APP",
        environment="prod",
        run_lint=False,
        pr_body_out=pr_path,
    )
    lint_lines = [
        ln
        for ln in res.pr_body_markdown.splitlines()
        if "cxas lint" in ln.lower()
    ]
    assert lint_lines
    assert "PASS" not in lint_lines[0]
    assert pr_path.read_text("utf-8") == res.pr_body_markdown


# RL-PENDING-5: `cxas build --json` warnings must be deterministic across
# PYTHONHASHSEED and must reflect post-override instructions without stale or
# duplicate entries.
def test_rl_pending_5_build_warnings_deterministic_and_accurate_with_overrides(
    tmp_path: Path,
) -> None:
    core, man = _make_cymbal_source(tmp_path / "repo")
    _write(
        core / "agents/legacy_agent/legacy_agent.json",
        {
            "displayName": "legacy_agent",
            "description": "Legacy specialist for @@brand.display_name@@.",
            "instruction": "agents/legacy_agent/instruction.txt",
            "modelSettings": {"model": GA_AGENT_MODEL},
        },
    )
    _write(core / "agents/legacy_agent/instruction.txt", "Legacy flow.\n")
    _write(
        man / "brands/cymbal_direct/channel_policy.yaml",
        "brand_id: cymbal_direct\n"
        "api_routing:\n"
        "  cards_api: https://cards.cymbal-direct.example\n"
        "channels:\n"
        "  DIGITAL_APP:\n"
        "    disabled_agents:\n"
        "      - branch_agent\n"
        "      - legacy_agent\n"
        "    supported_modalities: ['TEXT']\n",
    )
    _write(
        core / "agents/cards_agent/instruction.txt",
        "Assist @@brand.display_name@@ cardholders with "
        "{@AGENT: branch_agent} and {@TOOL: update_card_limit}.\n",
    )
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "agents:\n"
        "  cards_agent:\n"
        "    replace_lines:\n"
        "      - match: 'Assist @@brand.display_name@@ cardholders with "
        "{@AGENT: branch_agent} and {@TOOL: update_card_limit}.'\n"
        "        with: 'Assist @@brand.display_name@@ cardholders with "
        "{@TOOL: update_card_limit}.'\n"
        "  root_agent:\n"
        "    append_rules:\n"
        "      - 'Do not transfer to {@AGENT: branch_agent} or "
        "{@AGENT: legacy_agent}.'\n",
    )

    repo_src = str(Path(__file__).resolve().parents[3] / "src")
    outputs_by_seed: dict[str, list[str]] = {}
    for seed in ("1", "3"):
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "cxas_scrapi.cli.main",
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
                str(tmp_path / f"out_{seed}"),
                "--json",
            ],
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": repo_src,
                "PYTHONHASHSEED": seed,
            },
            check=True,
            capture_output=True,
            text=True,
        )
        outputs_by_seed[seed] = json.loads(proc.stdout)["warnings"]

    assert outputs_by_seed["1"] == outputs_by_seed["3"]
    warnings = outputs_by_seed["1"]
    assert len(warnings) == len(set(warnings))
    assert not any("cards_agent" in w for w in warnings)
