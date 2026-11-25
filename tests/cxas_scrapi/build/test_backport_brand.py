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

"""Tests for Console-to-Git `cxas backport --target brand` and `--dry-run`."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from cxas_scrapi.build import (
    BuildGateError,
    DriftGateError,
    backport_live_to_source,
    build_all,
)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _make_multi_brand_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create a 2-brand, 2-channel repo with valid lint-clean core app."""
    repo = tmp_path / "repo"
    core = repo / "core"
    manifests = repo / "manifests"

    _write_json(
        core / "app.json",
        {
            "name": "projects/p/locations/us/apps/@@profile.profile_id@@",
            "displayName": "@@profile.display_name@@ App",
            "rootAgent": "root_agent",
            "globalInstruction": "global_instruction.txt",
            "variableDeclarations": [
                {
                    "name": "current_date",
                    "description": "Current date",
                    "schema": {"type": "STRING", "default": ""},
                }
            ],
        },
    )
    (core / "global_instruction.txt").write_text(
        "Today is {current_date}.\nFollow @@profile.display_name@@ policies.\n",
        encoding="utf-8",
    )
    _write_json(
        core / "environment.json",
        {
            "tools": {
                "order_api": {
                    "openApiTool": {
                        "url": "@@env.api:orders@@",
                    }
                }
            }
        },
    )

    _write_json(
        core / "agents" / "root_agent" / "root_agent.json",
        {
            "displayName": "root_agent",
            "description": "Shared assistant for @@profile.display_name@@.",
            "instruction": "agents/root_agent/instruction.txt",
            "tools": ["end_session", "order_api", "kb_search"],
            "modelSettings": {"model": "gemini-3.5-flash", "temperature": 0.4},
        },
    )
    (core / "agents" / "root_agent" / "instruction.txt").write_text(
        "Hello from @@profile.display_name@@ on {current_date}.\n"
        "Use {@TOOL: order_api} for orders, {@TOOL: kb_search} for FAQs, "
        "and {@TOOL: end_session} to finish.\n",
        encoding="utf-8",
    )

    _write_json(
        core / "tools" / "order_api" / "order_api.json",
        {
            "displayName": "order_api",
            "openApiTool": {
                "description": "Default order lookup.",
                "openApiSchema": "tools/order_api/open_api_tool/open_api_schema.yaml",
            },
        },
    )
    oa_schema = (
        core / "tools" / "order_api" / "open_api_tool" / "open_api_schema.yaml"
    )
    oa_schema.parent.mkdir(parents=True, exist_ok=True)
    oa_schema.write_text(
        "openapi: 3.0.0\n"
        "info:\n  title: Orders\n  version: 1.0.0\n"
        "servers:\n  - url: $env_var\n"
        "paths:\n  /v1/orders:\n    get:\n      summary: List orders\n",
        encoding="utf-8",
    )

    _write_json(
        core / "tools" / "kb_search" / "kb_search.json",
        {
            "displayName": "kb_search",
            "dataStoreTool": {
                "name": "kb_search",
                "description": "Default KB search.",
            },
        },
    )

    _write_yaml(
        manifests / "profiles" / "alpha" / "identity.yaml",
        {
            "profile_id": "alpha",
            "display_name": "Alpha Retail",
            "urls": {"orders": "https://orders.alpha.example.com/v1/orders"},
        },
    )
    _write_yaml(
        manifests / "profiles" / "alpha" / "channel_policy.yaml",
        {
            "profile_id": "alpha",
            "supported_countries": ["US"],
            "channels": {
                "WEB": {
                    "entry_agent": "root_agent",
                    "supported_modalities": ["TEXT"],
                },
                "VOICE_TELEPHONY": {
                    "entry_agent": "root_agent",
                    "supported_modalities": ["AUDIO"],
                },
            },
        },
    )
    _write_yaml(
        manifests / "profiles" / "beta" / "identity.yaml",
        {
            "profile_id": "beta",
            "display_name": "Beta Stores",
            "urls": {"orders": "https://orders.beta.example.com/v1/orders"},
        },
    )
    _write_yaml(
        manifests / "profiles" / "beta" / "channel_policy.yaml",
        {
            "profile_id": "beta",
            "supported_countries": ["US"],
            "channels": {
                "WEB": {
                    "entry_agent": "root_agent",
                    "supported_modalities": ["TEXT"],
                }
            },
        },
    )
    _write_yaml(
        manifests / "envs" / "prod.yaml",
        {
            "environment": "prod",
            "templates": {
                "api": "https://api.prod.example.com/{profile}/{service}"
            },
        },
    )
    return repo, core, manifests


def _copy_built_as_live(dist_bundle: Path, live_dir: Path) -> Path:
    if live_dir.exists():
        shutil.rmtree(live_dir)
    shutil.copytree(dist_bundle, live_dir)
    # Real CES exports strip deployments/*.yaml
    dep_dir = live_dir / "deployments"
    if dep_dir.is_dir():
        shutil.rmtree(dep_dir)
    return live_dir


def test_target_core_rejected_cleanly(tmp_path: Path) -> None:
    repo, core, manifests = _make_multi_brand_repo(tmp_path)
    dist = repo / "dist"
    build_all(core, manifests, dist, "prod", write_locks=True)
    live = _copy_built_as_live(dist / "alpha" / "web", tmp_path / "live")

    with pytest.raises(BuildGateError, match="only target='brand'"):
        backport_live_to_source(
            core,
            manifests,
            live,
            brand_id="alpha",
            channel="WEB",
            environment="prod",
            target="core",
        )


def test_brand_channel_backport_tokens_tools_global_idempotency_and_revert(
    tmp_path: Path,
) -> None:
    repo, core, manifests = _make_multi_brand_repo(tmp_path)
    dist = repo / "dist"
    build_all(core, manifests, dist, "prod", write_locks=True)
    live = _copy_built_as_live(dist / "alpha" / "web", tmp_path / "live")

    # 1. Edit live global_instruction, agent instruction/model/temp/desc, & tools
    gi_live = live / "global_instruction.txt"
    gi_live.write_text(
        gi_live.read_text("utf-8") + "Always mention Alpha Retail rewards.\n",
        encoding="utf-8",
    )

    ag_instr = live / "agents" / "root_agent" / "instruction.txt"
    ag_instr.write_text(
        ag_instr.read_text("utf-8").replace(
            "Hello from Alpha Retail on {current_date}.",
            "Welcome to Alpha Retail web chat on {current_date}.",
        )
        + "Confirm order numbers before lookup.\n",
        encoding="utf-8",
    )
    ag_json_p = live / "agents" / "root_agent" / "root_agent.json"
    ag_data = json.loads(ag_json_p.read_text("utf-8"))
    ag_data["description"] = "Web assistant for Alpha Retail."
    ag_data["modelSettings"]["model"] = "gemini-3.5-pro"
    ag_data["modelSettings"]["temperature"] = 0.2
    _write_json(ag_json_p, ag_data)

    env_p = live / "environment.json"
    env_data = json.loads(env_p.read_text("utf-8"))
    env_data["tools"]["order_api"]["openApiTool"]["url"] = (
        "https://orders.alpha.example.com/v1/orders"
    )
    _write_json(env_p, env_data)

    oa_p = live / "tools" / "order_api" / "order_api.json"
    oa_data = json.loads(oa_p.read_text("utf-8"))
    oa_data["openApiTool"]["description"] = "Alpha Retail web order API."
    _write_json(oa_p, oa_data)

    kb_p = live / "tools" / "kb_search" / "kb_search.json"
    kb_data = json.loads(kb_p.read_text("utf-8"))
    kb_data["dataStoreTool"]["description"] = "Alpha Retail FAQ lookup."
    _write_json(kb_p, kb_data)

    pr_body_path = tmp_path / "pr_body.md"
    res = backport_live_to_source(
        core,
        manifests,
        live,
        brand_id="alpha",
        channel="WEB",
        environment="prod",
        dist_dir=dist,
        target="brand",
        scope="channel",
        pr_body_out=pr_body_path,
    )

    assert res.dry_run is False
    assert res.app_id == "alpha/web"
    assert res.lint_ran is True
    assert res.lint_errors == 0
    assert sorted(c.resource_id for c in res.changes) == [
        "agents/root_agent",
        "app",
        "tools/kb_search",
        "tools/order_api",
    ]
    assert set(res.unchanged_sibling_bundles) == {
        "alpha/voice_telephony",
        "beta/web",
    }
    assert pr_body_path.is_file()
    pr_md = pr_body_path.read_text("utf-8")
    assert "build(backport(live)) == live" in pr_md
    assert "compile" not in pr_md.lower()

    # Check reverse-tokenized overrides.yaml
    ov_path = manifests / "profiles" / "alpha" / "overrides.yaml"
    assert ov_path.is_file()
    ov_data = yaml.safe_load(ov_path.read_text("utf-8"))
    assert ov_data["global_instruction"]["channels"]["WEB"]["append_rules"] == [
        "Always mention @@profile.display_name@@ rewards."
    ]
    web_ag = ov_data["agents"]["root_agent"]["channels"]["WEB"]
    assert (
        web_ag["description"] == "Web assistant for @@profile.display_name@@."
    )
    assert web_ag["model"] == "gemini-3.5-pro"
    assert web_ag["temperature"] == pytest.approx(0.2)
    assert any(
        r["match"] == "Hello from @@profile.display_name@@ on {current_date}."
        and r["with"]
        == "Welcome to @@profile.display_name@@ web chat on {current_date}."
        for r in web_ag["replace_lines"]
    )
    rebuilt_ag_instr = (
        dist / "alpha" / "web" / "agents" / "root_agent" / "instruction.txt"
    ).read_text("utf-8")
    assert "Confirm order numbers before lookup." in rebuilt_ag_instr
    web_oa = ov_data["tools"]["order_api"]["channels"]["WEB"]
    assert web_oa["description"] == "@@profile.display_name@@ web order API."
    assert web_oa["url"] == "@@profile.urls.orders@@"

    # 2. Idempotency: running a second time produces 0 changes
    res2 = backport_live_to_source(
        core,
        manifests,
        live,
        brand_id="alpha",
        channel="WEB",
        environment="prod",
        dist_dir=dist,
        target="brand",
        scope="channel",
    )
    assert res2.changes == []
    assert res2.modified_files == []

    # 3. Revert live back to unoverridden core -> overrides.yaml cleanly removed
    clean_dist = tmp_path / "clean_dist"
    ov_path_backup = ov_path.read_text("utf-8")
    ov_path.unlink()
    build_all(core, manifests, clean_dist, "prod")
    ov_path.write_text(ov_path_backup, encoding="utf-8")
    reverted_live = _copy_built_as_live(
        clean_dist / "alpha" / "web", tmp_path / "reverted_live"
    )
    res_rev = backport_live_to_source(
        core,
        manifests,
        reverted_live,
        brand_id="alpha",
        channel="WEB",
        environment="prod",
        dist_dir=dist,
        target="brand",
        scope="channel",
    )
    assert res_rev.changes
    assert not ov_path.exists()


def test_brand_scope_promotes_across_brand_channels_zero_sister_brand_drift(
    tmp_path: Path,
) -> None:
    repo, core, manifests = _make_multi_brand_repo(tmp_path)
    dist = repo / "dist"
    build_all(core, manifests, dist, "prod", write_locks=True)
    live = _copy_built_as_live(dist / "alpha" / "web", tmp_path / "live")

    ag_instr = live / "agents" / "root_agent" / "instruction.txt"
    ag_instr.write_text(
        ag_instr.read_text("utf-8") + "Always thank Alpha Retail customers.\n",
        encoding="utf-8",
    )

    res = backport_live_to_source(
        core,
        manifests,
        live,
        brand_id="alpha",
        channel="WEB",
        environment="prod",
        dist_dir=dist,
        target="brand",
        scope="brand",
    )

    assert "beta/web" in res.unchanged_sibling_bundles
    assert "alpha/voice_telephony" not in res.unchanged_sibling_bundles

    ov_data = yaml.safe_load(
        (manifests / "profiles" / "alpha" / "overrides.yaml").read_text("utf-8")
    )
    # Promoted to brand-level (not nested under channels.WEB)
    assert ov_data["agents"]["root_agent"]["append_rules"] == [
        "Always thank @@profile.display_name@@ customers."
    ]
    assert "channels" not in ov_data["agents"]["root_agent"]
    # Voice channel bundle now includes the promoted rule
    voice_instr = (
        dist
        / "alpha"
        / "voice_telephony"
        / "agents"
        / "root_agent"
        / "instruction.txt"
    ).read_text("utf-8")
    assert "Always thank Alpha Retail customers." in voice_instr


def test_dry_run_computes_full_proof_with_zero_disk_mutations(
    tmp_path: Path,
) -> None:
    repo, core, manifests = _make_multi_brand_repo(tmp_path)
    dist = repo / "dist"
    build_all(core, manifests, dist, "prod", write_locks=True)
    live = _copy_built_as_live(dist / "alpha" / "web", tmp_path / "live")

    ag_instr = live / "agents" / "root_agent" / "instruction.txt"
    ag_instr.write_text(
        ag_instr.read_text("utf-8") + "Dry-run preview rule.\n",
        encoding="utf-8",
    )

    def _tree_digest(root: Path) -> dict[str, bytes]:
        return {
            p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*"))
            if p.is_file()
        }

    before_core = _tree_digest(core)
    before_man = _tree_digest(manifests)
    before_dist = _tree_digest(dist)
    pr_out = tmp_path / "should_not_exist.md"

    res = backport_live_to_source(
        core,
        manifests,
        live,
        brand_id="alpha",
        channel="WEB",
        environment="prod",
        dist_dir=dist,
        target="brand",
        scope="channel",
        dry_run=True,
        pr_body_out=pr_out,
    )

    assert res.dry_run is True
    assert len(res.changes) == 1
    assert res.changes[0].resource_id == "agents/root_agent"
    assert "[DRY RUN]" in res.pr_body_markdown
    assert not pr_out.exists()
    assert not (manifests / "profiles" / "alpha" / "overrides.yaml").exists()
    assert _tree_digest(core) == before_core
    assert _tree_digest(manifests) == before_man
    assert _tree_digest(dist) == before_dist

    # --dry-run + --git-branch is refused loudly
    with pytest.raises(BuildGateError, match="--git-branch with --dry-run"):
        backport_live_to_source(
            core,
            manifests,
            live,
            brand_id="alpha",
            channel="WEB",
            environment="prod",
            dry_run=True,
            git_branch="feat/preview",
        )


def test_three_way_conflict_force_live_and_git_ahead_preservation(
    tmp_path: Path,
) -> None:
    repo, core, manifests = _make_multi_brand_repo(tmp_path)
    dist = repo / "dist"
    build_all(core, manifests, dist, "prod", write_locks=True)
    lock_path = dist / "alpha" / "web.lock.json"
    live = _copy_built_as_live(dist / "alpha" / "web", tmp_path / "live")

    # Concurrent Git edit on root_agent AND kb_search (git-ahead)
    core_ag_instr = core / "agents" / "root_agent" / "instruction.txt"
    core_ag_instr.write_text(
        core_ag_instr.read_text("utf-8") + "Git-side agent rule.\n",
        encoding="utf-8",
    )
    core_kb_p = core / "tools" / "kb_search" / "kb_search.json"
    core_kb = json.loads(core_kb_p.read_text("utf-8"))
    core_kb["dataStoreTool"]["description"] = "Git-ahead KB description."
    _write_json(core_kb_p, core_kb)

    # Concurrent live edit on root_agent (conflict!) while kb_search untouched in live
    live_ag_instr = live / "agents" / "root_agent" / "instruction.txt"
    live_ag_instr.write_text(
        live_ag_instr.read_text("utf-8") + "Live-side agent rule.\n",
        encoding="utf-8",
    )

    conflicts_dir = tmp_path / "conflicts"
    with pytest.raises(DriftGateError, match="3-way conflict"):
        backport_live_to_source(
            core,
            manifests,
            live,
            brand_id="alpha",
            channel="WEB",
            environment="prod",
            lockfile_path=lock_path,
            conflicts_dir=conflicts_dir,
        )
    assert any(conflicts_dir.rglob("*.diff"))

    # Resolving conflict via --force-live succeeds AND preserves git-ahead kb_search!
    res = backport_live_to_source(
        core,
        manifests,
        live,
        brand_id="alpha",
        channel="WEB",
        environment="prod",
        dist_dir=dist,
        lockfile_path=lock_path,
        force_live=("agents/root_agent",),
    )
    assert [c.resource_id for c in res.changes] == ["agents/root_agent"]
    # Git-ahead kb_search keeps Git's updated description in refreshed dist!
    refreshed_kb = json.loads(
        (
            dist / "alpha" / "web" / "tools" / "kb_search" / "kb_search.json"
        ).read_text("utf-8")
    )
    assert (
        refreshed_kb["dataStoreTool"]["description"]
        == "Git-ahead KB description."
    )


def test_rollback_on_lint_failure_and_fail_closed_guards(
    tmp_path: Path,
) -> None:
    repo, core, manifests = _make_multi_brand_repo(tmp_path)
    dist = repo / "dist"
    build_all(core, manifests, dist, "prod", write_locks=True)
    live = _copy_built_as_live(dist / "alpha" / "web", tmp_path / "live")

    # 1. Tampering with '# Profile:' identity block fails and leaves manifests untouched
    gi_live = live / "global_instruction.txt"
    gi_live.write_text(
        "Completely replaced without header.\n", encoding="utf-8"
    )
    with pytest.raises(BuildGateError, match="# Profile:"):
        backport_live_to_source(
            core,
            manifests,
            live,
            brand_id="alpha",
            channel="WEB",
            environment="prod",
        )
    assert not (manifests / "profiles" / "alpha" / "overrides.yaml").exists()

    # 2. Live lint error (unclosed/invalid tool reference or undeclared variable)
    live2 = _copy_built_as_live(dist / "alpha" / "web", tmp_path / "live2")
    instr2 = live2 / "agents" / "root_agent" / "instruction.txt"
    instr2.write_text(
        instr2.read_text("utf-8") + "Read {undeclared_secret_var}.\n",
        encoding="utf-8",
    )
    with pytest.raises(BuildGateError, match="failed cxas lint"):
        backport_live_to_source(
            core,
            manifests,
            live2,
            brand_id="alpha",
            channel="WEB",
            environment="prod",
        )
    assert not (manifests / "profiles" / "alpha" / "overrides.yaml").exists()

    # 3. Structural tool change rejected
    live3 = _copy_built_as_live(dist / "alpha" / "web", tmp_path / "live3")
    kb3_p = live3 / "tools" / "kb_search" / "kb_search.json"
    kb3 = json.loads(kb3_p.read_text("utf-8"))
    kb3["executionType"] = "SYNCHRONOUS"
    _write_json(kb3_p, kb3)
    with pytest.raises(BuildGateError, match="structural tool change"):
        backport_live_to_source(
            core,
            manifests,
            live3,
            brand_id="alpha",
            channel="WEB",
            environment="prod",
        )

    # 4. Symlink inside live_dir rejected
    live4 = _copy_built_as_live(dist / "alpha" / "web", tmp_path / "live4")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    os.symlink(outside, live4 / "evil_link.txt")
    with pytest.raises(BuildGateError, match="Symlinks are not permitted"):
        backport_live_to_source(
            core,
            manifests,
            live4,
            brand_id="alpha",
            channel="WEB",
            environment="prod",
        )

    # 5. dist_dir overlapping core_dir rejected
    live5 = _copy_built_as_live(dist / "alpha" / "web", tmp_path / "live5")
    with pytest.raises(BuildGateError, match="must not overlap"):
        backport_live_to_source(
            core,
            manifests,
            live5,
            brand_id="alpha",
            channel="WEB",
            environment="prod",
            dist_dir=core,
        )


def test_custom_agent_instruction_filename_backport(tmp_path: Path) -> None:
    repo, core, manifests = _make_multi_brand_repo(tmp_path)
    # Switch root_agent to a custom instruction filename
    old_instr = core / "agents" / "root_agent" / "instruction.txt"
    custom_instr = core / "agents" / "root_agent" / "custom_prompt.txt"
    custom_instr.write_text(old_instr.read_text("utf-8"), encoding="utf-8")
    old_instr.unlink()
    ag_p = core / "agents" / "root_agent" / "root_agent.json"
    ag_d = json.loads(ag_p.read_text("utf-8"))
    ag_d["instruction"] = "agents/root_agent/custom_prompt.txt"
    _write_json(ag_p, ag_d)

    dist = repo / "dist"
    build_all(core, manifests, dist, "prod")
    live = _copy_built_as_live(dist / "alpha" / "web", tmp_path / "live")
    live_custom = live / "agents" / "root_agent" / "custom_prompt.txt"
    live_custom.write_text(
        live_custom.read_text("utf-8") + "Custom filename rule.\n",
        encoding="utf-8",
    )

    res = backport_live_to_source(
        core,
        manifests,
        live,
        brand_id="alpha",
        channel="WEB",
        environment="prod",
        dist_dir=dist,
        run_lint=False,
    )
    assert [c.resource_id for c in res.changes] == ["agents/root_agent"]
    assert "Custom filename rule." in (
        dist / "alpha" / "web" / "agents" / "root_agent" / "custom_prompt.txt"
    ).read_text("utf-8")


def test_git_branch_validation_dirty_tree_guard_and_checkout(
    tmp_path: Path,
) -> None:
    repo, core, manifests = _make_multi_brand_repo(tmp_path)
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)], check=True, env=env
    )
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "initial"], check=True, env=env
    )

    dist = tmp_path / "dist"
    build_all(core, manifests, dist, "prod")
    live = _copy_built_as_live(dist / "alpha" / "web", tmp_path / "live")
    live_instr = live / "agents" / "root_agent" / "instruction.txt"
    live_instr.write_text(
        live_instr.read_text("utf-8") + "Branch rule.\n", encoding="utf-8"
    )

    # Reject flag injection & invalid git refs
    for bad_branch in ("-evil", "bad..ref", "bad.lock", "a@{b}", "bad ref"):
        with pytest.raises(BuildGateError, match="Invalid --git-branch"):
            backport_live_to_source(
                core,
                manifests,
                live,
                brand_id="alpha",
                channel="WEB",
                environment="prod",
                git_branch=bad_branch,
            )

    # Reject dirty tracked working tree
    gi_core = core / "global_instruction.txt"
    orig_gi = gi_core.read_text("utf-8")
    gi_core.write_text(orig_gi + "# dirty\n", encoding="utf-8")
    with pytest.raises(BuildGateError, match="uncommitted tracked changes"):
        backport_live_to_source(
            core,
            manifests,
            live,
            brand_id="alpha",
            channel="WEB",
            environment="prod",
            git_branch="backport/alpha-web",
        )
    gi_core.write_text(orig_gi, encoding="utf-8")

    # Succeeds on clean working tree and switches branch
    res = backport_live_to_source(
        core,
        manifests,
        live,
        brand_id="alpha",
        channel="WEB",
        environment="prod",
        git_branch="backport/alpha-web",
    )
    assert res.git_branch == "backport/alpha-web"
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert head.stdout.strip() == "backport/alpha-web"


def test_cli_backport_dry_run_apply_and_reject_core(tmp_path: Path) -> None:
    repo, core, manifests = _make_multi_brand_repo(tmp_path)
    dist = repo / "dist"
    build_all(core, manifests, dist, "prod", write_locks=True)
    live = _copy_built_as_live(dist / "alpha" / "web", tmp_path / "live")
    ag_instr = live / "agents" / "root_agent" / "instruction.txt"
    ag_instr.write_text(
        ag_instr.read_text("utf-8") + "CLI backport rule.\n", encoding="utf-8"
    )

    repo_root = Path(__file__).resolve().parents[3]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "src"}

    # 1. --dry-run with --live-dir alias
    dry_proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "cxas_scrapi.cli.main",
            "backport",
            "--core-dir",
            str(core),
            "--manifests-dir",
            str(manifests),
            "--live-dir",
            str(live),
            "--dist",
            str(dist),
            "--brand",
            "alpha",
            "--channel",
            "WEB",
            "--env",
            "prod",
            "--target",
            "brand",
            "--dry-run",
            "--json",
        ],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert dry_proc.returncode == 0, dry_proc.stdout + dry_proc.stderr
    dry_payload = json.loads(dry_proc.stdout)
    assert dry_payload["passed"] is True
    assert dry_payload["dry_run"] is True
    assert len(dry_payload["changes"]) == 1
    assert not (manifests / "profiles" / "alpha" / "overrides.yaml").exists()

    # 2. Real apply via CLI with --pr-body-out
    pr_md = tmp_path / "cli_pr.md"
    apply_proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "cxas_scrapi.cli.main",
            "backport",
            "--core-dir",
            str(core),
            "--manifests-dir",
            str(manifests),
            "--live",
            str(live),
            "--dist",
            str(dist),
            "--brand",
            "alpha",
            "--channel",
            "WEB",
            "--env",
            "prod",
            "--target",
            "brand",
            "--pr-body-out",
            str(pr_md),
            "--json",
        ],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert apply_proc.returncode == 0, apply_proc.stdout + apply_proc.stderr
    apply_payload = json.loads(apply_proc.stdout)
    assert apply_payload["passed"] is True
    assert apply_payload["dry_run"] is False
    assert (manifests / "profiles" / "alpha" / "overrides.yaml").is_file()
    assert pr_md.is_file()

    # 3. --target core rejected cleanly by CLI
    core_proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "cxas_scrapi.cli.main",
            "backport",
            "--core-dir",
            str(core),
            "--manifests-dir",
            str(manifests),
            "--live",
            str(live),
            "--target",
            "core",
        ],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert core_proc.returncode != 0
