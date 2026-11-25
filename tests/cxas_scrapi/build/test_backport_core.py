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

"""Tests for Console-to-Git `cxas backport --target core`."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

import cxas_scrapi.build.backport as bp_mod
from cxas_scrapi.build import (
    BuildGateError,
    backport_live_to_source,
    build_all,
    build_app,
)
from cxas_scrapi.build.backport import (
    _analyze_inline_conditionals,
    _patch_core_instruction_preserving_conditionals,
)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _snapshot_mtimes(root: Path) -> dict[str, int]:
    return {
        p.relative_to(root).as_posix(): p.stat().st_mtime_ns
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _make_readonly(root: Path) -> None:
    for dirpath, _dirnames, filenames in os.walk(root, topdown=False):
        dp = Path(dirpath)
        for fname in filenames:
            os.chmod(dp / fname, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        os.chmod(
            dp,
            stat.S_IRUSR
            | stat.S_IXUSR
            | stat.S_IRGRP
            | stat.S_IXGRP
            | stat.S_IROTH
            | stat.S_IXOTH,
        )


def _make_writable(root: Path) -> None:
    for dirpath, _dirnames, filenames in os.walk(root):
        dp = Path(dirpath)
        os.chmod(dp, stat.S_IRWXU)
        for fname in filenames:
            os.chmod(dp / fname, stat.S_IRUSR | stat.S_IWUSR)


def _copy_built_as_live(built_dir: Path, live_dir: Path) -> Path:
    if live_dir.exists():
        shutil.rmtree(live_dir)
    shutil.copytree(built_dir, live_dir)
    dep_dir = live_dir / "deployments"
    if dep_dir.is_dir():
        shutil.rmtree(dep_dir)
    return live_dir


def _make_core_repo(
    tmp_path: Path,
    *,
    with_python_lib: bool = False,
    with_guardrail: bool = False,
) -> tuple[Path, Path, Path]:
    """Create a 2-brand, 2-channel lint-clean multi-brand repo."""
    repo = tmp_path / "repo"
    core = repo / "core"
    manifests = repo / "manifests"

    app_cfg: dict[str, Any] = {
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
    }
    if with_guardrail:
        app_cfg["guardrails"] = ["tone_guardrail"]

    _write_json(core / "app.json", app_cfg)
    (core / "global_instruction.txt").write_text(
        "@@profile.identity_block@@\n\n"
        "Today is {current_date}.\n"
        "Follow @@profile.display_name@@ guidelines.\n",
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

    root_tools = ["end_session", "order_api", "kb_search"]
    if with_python_lib:
        root_tools.append("helper_py")

    root_cfg: dict[str, Any] = {
        "displayName": "root_agent",
        "description": "Shared assistant for @@profile.display_name@@.",
        "instruction": "agents/root_agent/instruction.txt",
        "tools": root_tools,
        "childAgents": ["web_helper"],
        "modelSettings": {"model": "gemini-3.0-flash", "temperature": 0.3},
    }
    if with_python_lib:
        root_cfg["beforeModelCallbacks"] = [
            {
                "pythonCode": (
                    "agents/root_agent/before_model_callbacks/c1/python_code.py"
                )
            }
        ]
    _write_json(core / "agents" / "root_agent" / "root_agent.json", root_cfg)
    (core / "agents" / "root_agent" / "instruction.txt").write_text(
        "<role>\n"
        "You help @@profile.display_name@@ customers with "
        "@@profile.lexicon.account@@ on {current_date}.\n"
        "</role>\n"
        "<instructions>\n"
        "{@startChannel_WEB}\n"
        "Share web links from @@profile.urls.support@@.\n"
        "{@endChannel_WEB}\n"
        "{@startChannel_VOICE_TELEPHONY}\n"
        "Speak concisely over the phone.\n"
        "{@endChannel_VOICE_TELEPHONY}\n"
        "{@startAgentEnabled_web_helper}\n"
        "Delegate web flows to {@AGENT: web_helper}.\n"
        "{@endAgentEnabled_web_helper}\n"
        "Use {@TOOL: order_api} and {@TOOL: kb_search}, and call "
        "{@TOOL: end_session} when finished.\n"
        "</instructions>\n",
        encoding="utf-8",
    )

    _write_json(
        core / "agents" / "web_helper" / "web_helper.json",
        {
            "displayName": "web_helper",
            "description": "Web-only helper for @@profile.display_name@@.",
            "instruction": "agents/web_helper/instruction.txt",
            "tools": ["kb_search"],
            "modelSettings": {"model": "gemini-3.0-flash", "temperature": 0.2},
        },
    )
    (core / "agents" / "web_helper" / "instruction.txt").write_text(
        "Handle web self-service on {current_date} using {@TOOL: kb_search}.\n",
        encoding="utf-8",
    )

    _write_json(
        core / "tools" / "order_api" / "order_api.json",
        {
            "displayName": "order_api",
            "openApiTool": {
                "description": "Look up @@profile.display_name@@ orders.",
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
        "  description: Look up orders.\n"
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
                "description": "Search @@profile.display_name@@ articles.",
            },
        },
    )

    if with_python_lib:
        (core / "python_lib").mkdir(parents=True, exist_ok=True)
        (core / "python_lib" / "fmt.py").write_text(
            "def tag_msg(brand: str, msg: str) -> str:\n"
            '    return f"[{profile}] {msg}"\n',
            encoding="utf-8",
        )
        cb_path = (
            core
            / "agents"
            / "root_agent"
            / "before_model_callbacks"
            / "c1"
            / "python_code.py"
        )
        cb_path.parent.mkdir(parents=True, exist_ok=True)
        cb_path.write_text(
            "from python_lib.fmt import tag_msg\n\n"
            "def before_model_callback(callback_context, llm_request):\n"
            '    _ = tag_msg("@@profile.display_name@@", "start")\n'
            "    return None\n",
            encoding="utf-8",
        )
        _write_json(
            core / "tools" / "helper_py" / "helper_py.json",
            {
                "displayName": "helper_py",
                "pythonFunction": {
                    "name": "helper_py",
                    "description": "Format greeting for @@profile.display_name@@.",
                    "pythonCode": "tools/helper_py/python_function/python_code.py",
                },
            },
        )
        py_tool = (
            core / "tools" / "helper_py" / "python_function" / "python_code.py"
        )
        py_tool.parent.mkdir(parents=True, exist_ok=True)
        py_tool.write_text(
            "from python_lib.fmt import tag_msg\n\n"
            "def helper_py(topic: str) -> dict:\n"
            '    """Format a branded greeting."""\n'
            '    return {"message": tag_msg("@@profile.display_name@@", topic)}\n',
            encoding="utf-8",
        )

    if with_guardrail:
        _write_json(
            core / "guardrails" / "tone_guardrail" / "tone_guardrail.json",
            {
                "displayName": "tone_guardrail",
                "description": "Keep @@profile.display_name@@ responses polite.",
                "enabled": True,
                "llmPolicy": {
                    "prompt": "Ensure replies match @@profile.display_name@@ tone.",
                },
            },
        )

    _write_yaml(
        manifests / "profiles" / "alpha" / "identity.yaml",
        {
            "profile_id": "alpha",
            "display_name": "Alpha Retail",
            "assistant_title": "Alpha Guide",
            "legal_entity": "Alpha Retail Ltd.",
            "tone_guidelines": "Warm and concise.",
            "lexicon": {
                "account": "Alpha Account",
                "alpha_only": "Alpha Exclusive Perks",
            },
            "urls": {"support": "https://support.alpha.example.com"},
            "tts_voices": {
                "en-US": {"voice": "en-US-Neural2-F", "speaking_rate": 1.0}
            },
        },
    )
    _write_yaml(
        manifests / "profiles" / "alpha" / "channel_policy.yaml",
        {
            "profile_id": "alpha",
            "supported_countries": ["US"],
            "api_routing": {"portal": "https://portal.alpha.example.com"},
            "channels": {
                "WEB": {"supported_modalities": ["TEXT"]},
                "VOICE_TELEPHONY": {
                    "disabled_agents": ["web_helper"],
                    "supported_modalities": ["AUDIO"],
                },
            },
        },
    )
    _write_yaml(
        manifests / "profiles" / "beta" / "identity.yaml",
        {
            "profile_id": "beta",
            "display_name": "Beta Direct",
            "assistant_title": "Beta Helper",
            "legal_entity": "Beta Direct Inc.",
            "tone_guidelines": "Direct and helpful.",
            "lexicon": {"account": "Beta Account"},
            "urls": {"support": "https://support.beta.example.com"},
            "tts_voices": {
                "en-US": {"voice": "en-US-Neural2-D", "speaking_rate": 1.0}
            },
        },
    )
    _write_yaml(
        manifests / "profiles" / "beta" / "channel_policy.yaml",
        {
            "profile_id": "beta",
            "supported_countries": ["US"],
            "api_routing": {"portal": "https://portal.beta.example.com"},
            "channels": {
                "WEB": {"supported_modalities": ["TEXT"]},
                "VOICE_TELEPHONY": {
                    "disabled_agents": ["web_helper"],
                    "supported_modalities": ["AUDIO"],
                },
            },
        },
    )
    _write_yaml(
        manifests / "envs" / "prod.yaml",
        {
            "environment": "prod",
            "templates": {
                "api": "https://api.example.com/{profile_slug}/{service}",
            },
            "overrides": {},
        },
    )
    return repo, core, manifests


def test_core_instruction_patching_preserves_multiline_and_inline_conditionals() -> (
    None
):
    raw_core = (
        "Header line.\n"
        "{@startChannel_VOICE_TELEPHONY}\n"
        "Voice-only directive.\n"
        "{@endChannel_VOICE_TELEPHONY}\n"
        "Shared body line.\n"
        "{@startProfile_beta}Beta inline note.{@endProfile_beta}\n"
        "Footer line.\n"
    )
    base_built = "Header line.\nShared body line.\n\nFooter line.\n"
    live_built = "Header line.\nUpdated shared body line.\nFooter line.\n"

    patched = _patch_core_instruction_preserving_conditionals(
        raw_core,
        base_built,
        live_built,
        brand_id="alpha",
        channel="WEB",
        disabled_agents=set(),
        disabled_tools=set(),
        rev_fn=lambda s: s,
    )
    assert "{@startChannel_VOICE_TELEPHONY}" in patched
    assert "Voice-only directive." in patched
    assert "{@startProfile_beta}" in patched
    assert "Beta inline note." in patched
    assert "Updated shared body line." in patched

    # Mixed active + inactive inline conditional on a modified line fails closed.
    mixed_line = "Always say hi {@startChannel_VOICE_TELEPHONY}aloud{@endChannel_VOICE_TELEPHONY}."
    kind, _ = _analyze_inline_conditionals(
        mixed_line, lambda k, a: not (k == "Channel" and a == "VOICE_TELEPHONY")
    )
    assert kind == "mixed_inactive"

    with pytest.raises(BuildGateError, match="inactive inline conditional"):
        _patch_core_instruction_preserving_conditionals(
            mixed_line + "\n",
            "Always say hi .\n",
            "Always say hello.\n",
            brand_id="alpha",
            channel="WEB",
            disabled_agents=set(),
            disabled_tools=set(),
            rev_fn=lambda s: s,
        )


def test_core_backport_multi_brand_reverse_tokenization_safety(
    tmp_path: Path,
) -> None:
    repo, core, manifests = _make_core_repo(tmp_path)
    dist = repo / "dist"
    build_all(core, manifests, dist, "prod", write_locks=True)
    live = _copy_built_as_live(dist / "alpha" / "web", tmp_path / "live")

    # Edit live prompt with both a cross-brand term ("Alpha Retail", "Alpha Account")
    # and an alpha-only lexicon value ("Alpha Exclusive Perks") not present in beta.
    live_in = live / "agents" / "root_agent" / "instruction.txt"
    live_in.write_text(
        live_in.read_text("utf-8").replace(
            "Share web links",
            "Highlight Alpha Retail Alpha Account and Alpha Exclusive Perks. Share web links",
        ),
        encoding="utf-8",
    )

    res = backport_live_to_source(
        core,
        manifests,
        live,
        dist_dir=dist,
        brand_id="alpha",
        channel="WEB",
        environment="prod",
        target="core",
    )

    assert res.target == "core"
    assert res.lint_errors == 0
    core_in_text = (
        core / "agents" / "root_agent" / "instruction.txt"
    ).read_text("utf-8")
    # Cross-brand tokens lifted; alpha-only lexicon value left literal so beta builds.
    assert "@@profile.display_name@@" in core_in_text
    assert "@@profile.lexicon.account@@" in core_in_text
    assert "@@profile.lexicon.alpha_only@@" not in core_in_text
    assert "Alpha Exclusive Perks" in core_in_text
    # Inactive VOICE_TELEPHONY conditional block preserved verbatim!
    assert "{@startChannel_VOICE_TELEPHONY}" in core_in_text
    assert "Speak concisely over the phone." in core_in_text

    # Beta rebuilt cleanly with Beta Direct & Beta Account substituted.
    beta_in = (
        dist / "beta" / "web" / "agents" / "root_agent" / "instruction.txt"
    ).read_text("utf-8")
    assert "Beta Direct" in beta_in
    assert "Beta Account" in beta_in


def test_core_backport_python_callback_and_tool_reverse_tokenization(
    tmp_path: Path,
) -> None:
    repo, core, manifests = _make_core_repo(tmp_path, with_python_lib=True)
    dist = repo / "dist"
    build_all(core, manifests, dist, "prod", write_locks=True)
    live = _copy_built_as_live(dist / "alpha" / "web", tmp_path / "live")

    # Edit live callback outside CXAS_SHARED_BUNDLE and live Python tool.
    cb_live = (
        live
        / "agents"
        / "root_agent"
        / "before_model_callbacks"
        / "c1"
        / "python_code.py"
    )
    assert "BEGIN CXAS_SHARED_BUNDLE" in cb_live.read_text("utf-8")
    cb_live.write_text(
        cb_live.read_text("utf-8").replace('"start"', '"welcome_alpha"'),
        encoding="utf-8",
    )
    tool_live = (
        live / "tools" / "helper_py" / "python_function" / "python_code.py"
    )
    tool_live.write_text(
        tool_live.read_text("utf-8").replace(
            'return {"message": tag_msg("Alpha Retail", topic)}',
            'return {"message": tag_msg("Alpha Retail", f"ok:{topic}")}',
        ),
        encoding="utf-8",
    )

    # --target brand must refuse Python edits and suggest --target core.
    with pytest.raises(BuildGateError, match="--target core"):
        backport_live_to_source(
            core,
            manifests,
            live,
            brand_id="alpha",
            channel="WEB",
            environment="prod",
            target="brand",
        )

    res = backport_live_to_source(
        core,
        manifests,
        live,
        dist_dir=dist,
        brand_id="alpha",
        channel="WEB",
        environment="prod",
        target="core",
    )
    assert res.target == "core"
    assert res.lint_errors == 0

    cb_core = (
        core
        / "agents"
        / "root_agent"
        / "before_model_callbacks"
        / "c1"
        / "python_code.py"
    ).read_text("utf-8")
    assert "BEGIN CXAS_SHARED_BUNDLE" not in cb_core
    assert '"welcome_alpha"' in cb_core
    assert "@@profile.display_name@@" in cb_core

    tool_core = (
        core / "tools" / "helper_py" / "python_function" / "python_code.py"
    ).read_text("utf-8")
    assert "BEGIN CXAS_SHARED_BUNDLE" not in tool_core
    assert 'f"ok:{topic}"' in tool_core
    assert "@@profile.display_name@@" in tool_core


def test_core_backport_tools_guardrails_and_override_promotion_idempotent(
    tmp_path: Path,
) -> None:
    repo, core, manifests = _make_core_repo(tmp_path, with_guardrail=True)
    # Seed an existing brand override that should be promoted & cleaned up.
    _write_yaml(
        manifests / "profiles" / "alpha" / "overrides.yaml",
        {
            "profile_id": "alpha",
            "agents": {
                "root_agent": {
                    "description": "Promoted assistant for @@profile.display_name@@.",
                }
            },
        },
    )
    dist = repo / "dist"
    build_all(core, manifests, dist, "prod", write_locks=True)
    live = _copy_built_as_live(dist / "alpha" / "web", tmp_path / "live")

    # Edit live guardrail prompt, tool description/URL, and global_instruction.
    gr_live = live / "guardrails" / "tone_guardrail" / "tone_guardrail.json"
    gr_doc = json.loads(gr_live.read_text("utf-8"))
    gr_doc["llmPolicy"]["prompt"] = (
        "Always maintain Alpha Retail respectful tone."
    )
    _write_json(gr_live, gr_doc)

    tl_live = live / "tools" / "order_api" / "order_api.json"
    tl_doc = json.loads(tl_live.read_text("utf-8"))
    tl_doc["openApiTool"]["description"] = "Query Alpha Retail order history."
    _write_json(tl_live, tl_doc)

    env_live = live / "environment.json"
    env_doc = json.loads(env_live.read_text("utf-8"))
    env_doc["tools"]["order_api"]["openApiTool"]["url"] = (
        "https://api.example.com/alpha/orders-v2"
    )
    _write_json(env_live, env_doc)

    gi_live = live / "global_instruction.txt"
    gi_live.write_text(
        gi_live.read_text("utf-8") + "Always confirm Alpha Retail policy.\n",
        encoding="utf-8",
    )

    res = backport_live_to_source(
        core,
        manifests,
        live,
        dist_dir=dist,
        brand_id="alpha",
        channel="WEB",
        environment="prod",
        target="core",
    )
    assert res.lint_errors == 0
    # Existing overrides.yaml had only the promoted description -> deleted cleanly.
    assert not (manifests / "profiles" / "alpha" / "overrides.yaml").exists()

    core_ag = json.loads(
        (core / "agents" / "root_agent" / "root_agent.json").read_text("utf-8")
    )
    assert (
        core_ag["description"]
        == "Promoted assistant for @@profile.display_name@@."
    )
    core_gr = json.loads(
        (
            core / "guardrails" / "tone_guardrail" / "tone_guardrail.json"
        ).read_text("utf-8")
    )
    assert (
        core_gr["llmPolicy"]["prompt"]
        == "Always maintain @@profile.display_name@@ respectful tone."
    )
    core_env = json.loads((core / "environment.json").read_text("utf-8"))
    assert (
        core_env["tools"]["order_api"]["openApiTool"]["url"]
        == "@@env.api:orders-v2@@"
    )
    assert "@@profile.identity_block@@" in (
        core / "global_instruction.txt"
    ).read_text("utf-8")

    # Idempotency: re-running against the same live export is a clean no-op.
    res2 = backport_live_to_source(
        core,
        manifests,
        live,
        dist_dir=dist,
        brand_id="alpha",
        channel="WEB",
        environment="prod",
        target="core",
    )
    assert res2.changes == []
    assert res2.modified_files == []


def test_core_dry_run_readonly_core_and_manifests_zero_touch(
    tmp_path: Path,
) -> None:
    repo, core, manifests = _make_core_repo(tmp_path)
    dist = repo / "dist"
    build_all(core, manifests, dist, "prod", write_locks=True)
    live = _copy_built_as_live(dist / "alpha" / "web", tmp_path / "live")

    live_in = live / "agents" / "root_agent" / "instruction.txt"
    live_in.write_text(
        live_in.read_text("utf-8") + "Shared dry-run note for Alpha Retail.\n",
        encoding="utf-8",
    )

    core_before = _snapshot_tree(core)
    man_before = _snapshot_tree(manifests)
    dist_before = _snapshot_tree(dist)
    core_mtimes = _snapshot_mtimes(core)
    man_mtimes = _snapshot_mtimes(manifests)

    _make_readonly(core)
    _make_readonly(manifests)
    try:
        res = backport_live_to_source(
            core,
            manifests,
            live,
            dist_dir=dist,
            brand_id="alpha",
            channel="WEB",
            environment="prod",
            target="core",
            dry_run=True,
        )
    finally:
        _make_writable(core)
        _make_writable(manifests)

    assert res.dry_run is True
    assert res.target == "core"
    assert res.changes
    # Modified files remapped back to caller core/, not temp dir!
    assert (
        core / "agents" / "root_agent" / "instruction.txt"
    ) in res.modified_files
    assert all(
        p.is_relative_to(core) or p.is_relative_to(manifests)
        for p in res.modified_files
    )
    assert "[DRY RUN]" in res.pr_body_markdown
    assert _snapshot_tree(core) == core_before
    assert _snapshot_tree(manifests) == man_before
    assert _snapshot_tree(dist) == dist_before
    assert _snapshot_mtimes(core) == core_mtimes
    assert _snapshot_mtimes(manifests) == man_mtimes


def test_core_rollback_restores_all_files_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, core, manifests = _make_core_repo(tmp_path)
    dist = repo / "dist"
    build_all(core, manifests, dist, "prod", write_locks=True)
    lock_p = dist / "alpha" / "web.lock.json"
    live = _copy_built_as_live(dist / "alpha" / "web", tmp_path / "live")

    live_in = live / "agents" / "root_agent" / "instruction.txt"
    live_in.write_text(
        live_in.read_text("utf-8") + "Will be rolled back.\n",
        encoding="utf-8",
    )

    core_before = _snapshot_tree(core)
    man_before = _snapshot_tree(manifests)
    dist_before = _snapshot_tree(dist)
    lock_before = lock_p.read_bytes()

    orig_build_all = bp_mod.build_all
    calls = {"n": 0}

    def _boom_on_post_build(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise BuildGateError("Simulated post-backport rebuild failure")
        return orig_build_all(*args, **kwargs)

    monkeypatch.setattr(bp_mod, "build_all", _boom_on_post_build)

    with pytest.raises(BuildGateError, match="Simulated post-backport"):
        backport_live_to_source(
            core,
            manifests,
            live,
            dist_dir=dist,
            lockfile_path=lock_p,
            brand_id="alpha",
            channel="WEB",
            environment="prod",
            target="core",
        )

    assert _snapshot_tree(core) == core_before
    assert _snapshot_tree(manifests) == man_before
    assert _snapshot_tree(dist) == dist_before
    assert lock_p.read_bytes() == lock_before


def test_core_pruned_sibling_zero_blast_radius_and_cli_e2e(
    tmp_path: Path,
) -> None:
    repo, core, manifests = _make_core_repo(tmp_path)
    dist = repo / "dist"
    build_all(core, manifests, dist, "prod", write_locks=True)
    live = _copy_built_as_live(dist / "alpha" / "web", tmp_path / "live")

    # Edit web_helper (which is disabled in VOICE_TELEPHONY for both brands!).
    wh_live = live / "agents" / "web_helper" / "instruction.txt"
    wh_live.write_text(
        wh_live.read_text("utf-8") + "Offer Alpha Retail web widget help.\n",
        encoding="utf-8",
    )

    repo_root = Path(__file__).resolve().parents[3]
    env = {
        **os.environ,
        "PYTHONPATH": str(repo_root / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    # 1. CLI --target core --dry-run --json
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
            "core",
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
    assert dry_payload["target"] == "core"
    assert dry_payload["dry_run"] is True
    # VOICE_TELEPHONY siblings disable web_helper -> 0 blast radius!
    assert "alpha/voice_telephony" in dry_payload["unchanged_sibling_bundles"]
    assert "beta/voice_telephony" in dry_payload["unchanged_sibling_bundles"]

    # 2. CLI --target core apply
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
            "core",
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
    assert "@@profile.display_name@@" in (
        core / "agents" / "web_helper" / "instruction.txt"
    ).read_text("utf-8")
    # Rebuild verification: beta/web received the shared web_helper update.
    assert "Beta Direct" in (
        dist / "beta" / "web" / "agents" / "web_helper" / "instruction.txt"
    ).read_text("utf-8")
    # Build check: zero forbidden jargon in backport output / PR body.
    assert "compiler" not in apply_payload["pr_body_markdown"].lower()
    assert "firewall" not in apply_payload["pr_body_markdown"].lower()
    assert build_app(
        core, manifests, "beta", "WEB", "prod", tmp_path / "check_beta"
    )
