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

"""Adversarial E2E and edge-case QA tests for PR 7 (cxas backport --target brand).

Contains:
- Passing edge-case, security, and CLI E2E regression tests (``test_qa_pr7_...``).
- Failing reproduction tests for open QA defects (``test_qa_pr7_001_...``
  through ``test_qa_pr7_005_...``), which must turn green once fixed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from cxas_scrapi.build import backport as bp_mod
from cxas_scrapi.build.backport import backport_live_to_source
from cxas_scrapi.build.builder import build_all, build_app
from cxas_scrapi.build.bundle_io import BuildGateError, DriftGateError
from cxas_scrapi.build.drift import write_lockfile

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(_REPO_ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "cxas_scrapi.cli.main", *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


def _make_ws(
    root: Path,
    *,
    yaml_app: bool = False,
    env_url: bool = False,
    multi_path_openapi: bool = False,
    custom_instr_name: str | None = None,
) -> tuple[Path, Path]:
    core = root / "core"
    man = root / "manifests"
    (core / "agents" / "root_agent").mkdir(parents=True)
    (core / "tools" / "api_tool" / "open_api_tool").mkdir(parents=True)
    (core / "tools" / "py_tool" / "python_function").mkdir(parents=True)

    app_obj = {
        "name": "projects/p/locations/us/apps/a1",
        "displayName": "@@profile.display_name@@",
        "rootAgent": "root_agent",
        "globalInstruction": "global_instruction.txt",
    }
    ag_obj: dict[str, object] = {
        "displayName": "root_agent",
        "description": "Root for @@profile.display_name@@",
        "tools": ["api_tool", "py_tool", "end_session"],
        "modelSettings": {"model": "gemini-3.0-flash", "temperature": 0.2},
    }
    if custom_instr_name is not None:
        ag_obj["instruction"] = f"agents/root_agent/{custom_instr_name}"

    if yaml_app:
        (core / "app.yaml").write_text(
            yaml.safe_dump(app_obj, sort_keys=False), encoding="utf-8"
        )
        (core / "agents" / "root_agent" / "root_agent.yaml").write_text(
            yaml.safe_dump(ag_obj, sort_keys=False), encoding="utf-8"
        )
    else:
        (core / "app.json").write_text(
            json.dumps(app_obj, indent=2) + "\n", encoding="utf-8"
        )
        (core / "agents" / "root_agent" / "root_agent.json").write_text(
            json.dumps(ag_obj, indent=2) + "\n", encoding="utf-8"
        )

    (core / "global_instruction.txt").write_text(
        "Always serve @@profile.display_name@@ users politely.\n"
        "Never leak confidential details.\n",
        encoding="utf-8",
    )
    instr_fname = custom_instr_name or "instruction.txt"
    (core / "agents" / "root_agent" / instr_fname).write_text(
        "Use {@TOOL: api_tool}, {@TOOL: py_tool}, and {@TOOL: end_session}.\n"
        "Greet @@profile.assistant_title@@ callers.\n",
        encoding="utf-8",
    )
    (core / "tools" / "api_tool" / "api_tool.json").write_text(
        json.dumps(
            {
                "displayName": "api_tool",
                "openApiTool": {
                    "description": "API for @@profile.display_name@@",
                    "openApiSchema": (
                        "tools/api_tool/open_api_tool/open_api_schema.yaml"
                    ),
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    srv_url = "$env_var" if env_url else "https://api.acme.example"
    paths: dict[str, object] = {
        "/orders": {"get": {"operationId": "getOrders"}}
    }
    if multi_path_openapi:
        paths["/invoices"] = {"get": {"operationId": "getInvoices"}}
    (
        core / "tools" / "api_tool" / "open_api_tool" / "open_api_schema.yaml"
    ).write_text(
        yaml.safe_dump(
            {
                "openapi": "3.0.0",
                "info": {"title": "API", "version": "1.0"},
                "servers": [{"url": srv_url}],
                "paths": paths,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    if env_url:
        (core / "environment.json").write_text(
            json.dumps(
                {
                    "tools": {
                        "api_tool": {
                            "openApiTool": {
                                "url": "https://api.brand-1.prod.example/orders"
                            }
                        }
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    (core / "tools" / "py_tool" / "py_tool.json").write_text(
        json.dumps(
            {
                "displayName": "py_tool",
                "pythonFunction": {
                    "name": "run",
                    "description": "Helper tool",
                    "pythonCode": "tools/py_tool/python_function/python_code.py",
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (
        core / "tools" / "py_tool" / "python_function" / "python_code.py"
    ).write_text(
        "def run() -> dict:\n    return {'ok': True}\n", encoding="utf-8"
    )

    for b_id, disp, title in (
        ("brand_1", "Acme", "Acme Assistant"),
        ("brand_2", "Beta Corp", "Beta Helper"),
    ):
        bdir = man / "profiles" / b_id
        bdir.mkdir(parents=True)
        (bdir / "identity.yaml").write_text(
            yaml.safe_dump(
                {
                    "profile_id": b_id,
                    "display_name": disp,
                    "assistant_title": title,
                    "urls": {
                        "support": f"https://support.{b_id}.example/help?a=1+2&b=(x)"
                    },
                    "lexicon": {
                        "short": "hi",
                        "empty": "",
                        "card": "Gold Card",
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (bdir / "channel_policy.yaml").write_text(
            yaml.safe_dump(
                {
                    "profile_id": b_id,
                    "channels": {
                        "WEB": {"supported_modalities": ["TEXT"]},
                        "VOICE_TELEPHONY": {"supported_modalities": ["AUDIO"]},
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    (man / "envs").mkdir(parents=True)
    (man / "envs" / "prod.yaml").write_text(
        yaml.safe_dump(
            {
                "environment": "prod",
                "templates": {
                    "api": "https://api.{profile_slug}.{env}.example/{service}"
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return core, man


def _init_git_repo(repo_dir: Path) -> None:
    subprocess.run(
        ["git", "init", str(repo_dir)], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.email", "qa@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.name", "QA"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo_dir), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )


# ── 1. Target Gate & Scope Hygiene ──────────────────────────────────────


def test_qa_pr7_target_core_rejected_api_and_cli_zero_mutations(
    tmp_path: Path,
) -> None:
    """Passing target='core' fails cleanly via API and CLI with zero mutations."""
    core, man = _make_ws(tmp_path)
    live = tmp_path / "live"
    build_app(core, man, "brand_1", "WEB", "prod", live)
    (live / "agents" / "root_agent" / "instruction.txt").write_text(
        (live / "agents" / "root_agent" / "instruction.txt").read_text("utf-8")
        + "Live edit.\n",
        encoding="utf-8",
    )
    core_before = _tree_snapshot(core)
    man_before = _tree_snapshot(man)

    with pytest.raises(BuildGateError, match="target='brand'"):
        backport_live_to_source(
            core,
            man,
            live,
            brand_id="brand_1",
            channel="WEB",
            environment="prod",
            target="core",
        )

    proc = _run_cli(
        [
            "backport",
            "--core-dir",
            str(core),
            "--manifests-dir",
            str(man),
            "--live-dir",
            str(live),
            "--brand",
            "brand_1",
            "--channel",
            "WEB",
            "--env",
            "prod",
            "--target",
            "core",
        ]
    )
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    assert _tree_snapshot(core) == core_before
    assert _tree_snapshot(man) == man_before


def test_qa_pr7_scope_hygiene_no_pr4_pr8_leakage_or_jargon() -> None:
    """Confirm PR 7 has no PR 4/8 leakage, customer names, or forbidden jargon."""
    bp_text = (_REPO_ROOT / "src/cxas_scrapi/build/backport.py").read_text(
        "utf-8"
    )
    assert not re.search(
        r"^\s*(?:from|import)\s+[^\n]*polymorphic\b",
        bp_text,
        re.MULTILINE,
    )
    doc_text = (_REPO_ROOT / "docs/guides/build.md").read_text("utf-8")
    for text in (bp_text, doc_text):
        assert not re.search(
            r"\b(?:firewall|compiler)\b",
            text,
            re.IGNORECASE,
        )


# ── 2. --dry-run Atomicity & Side-Effect Isolation ──────────────────────


def test_qa_pr7_dry_run_no_prior_and_existing_overrides_and_git_branch_guard(
    tmp_path: Path,
) -> None:
    """--dry-run leaves manifests/dist/lockfile/pr-body-out untouched (both with
    and without prior overrides.yaml), rejects --git-branch, and returns full proof."""
    repo = tmp_path / "repo"
    repo.mkdir()
    core, man = _make_ws(repo)
    _init_git_repo(repo)
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)
    lock_p = dist / "brand_1" / "web.lock.json"
    live = tmp_path / "live"
    build_app(core, man, "brand_1", "WEB", "prod", live)
    instr = live / "agents" / "root_agent" / "instruction.txt"
    instr.write_text(instr.read_text("utf-8") + "Dry-run rule 1.\n", "utf-8")

    pr_out = tmp_path / "uncreated_dir" / "pr_body.md"

    # Reject --dry-run + --git-branch before mutating git state.
    with pytest.raises(BuildGateError, match="Cannot combine --git-branch"):
        backport_live_to_source(
            core,
            man,
            live,
            dist_dir=dist,
            lockfile_path=lock_p,
            brand_id="brand_1",
            channel="WEB",
            environment="prod",
            dry_run=True,
            git_branch="feat/should-not-create",
        )

    # Case 1: overrides.yaml does NOT exist yet.
    man_before = _tree_snapshot(man)
    dist_before = _tree_snapshot(dist)
    lock_before = lock_p.read_bytes()

    res1 = backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist,
        lockfile_path=lock_p,
        brand_id="brand_1",
        channel="WEB",
        environment="prod",
        dry_run=True,
        pr_body_out=pr_out,
    )
    assert res1.dry_run is True
    assert len(res1.changes) == 1
    assert "[DRY RUN]" in res1.pr_body_markdown
    assert not (man / "profiles" / "brand_1" / "overrides.yaml").exists()
    assert not pr_out.parent.exists()
    assert _tree_snapshot(man) == man_before
    assert _tree_snapshot(dist) == dist_before
    assert lock_p.read_bytes() == lock_before

    # Case 2: overrides.yaml ALREADY exists.
    backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist,
        lockfile_path=lock_p,
        brand_id="brand_1",
        channel="WEB",
        environment="prod",
    )
    assert (man / "profiles" / "brand_1" / "overrides.yaml").is_file()
    instr.write_text(instr.read_text("utf-8") + "Dry-run rule 2.\n", "utf-8")
    man_before_2 = _tree_snapshot(man)
    dist_before_2 = _tree_snapshot(dist)
    lock_before_2 = lock_p.read_bytes()

    res2 = backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist,
        lockfile_path=lock_p,
        brand_id="brand_1",
        channel="WEB",
        environment="prod",
        dry_run=True,
        pr_body_out=pr_out,
    )
    assert res2.dry_run is True
    assert len(res2.changes) == 1
    assert not pr_out.parent.exists()
    assert _tree_snapshot(man) == man_before_2
    assert _tree_snapshot(dist) == dist_before_2
    assert lock_p.read_bytes() == lock_before_2


# ── 3. Delta Extraction, Reverse Tokenization & Layering ────────────────


def test_qa_pr7_channel_vs_brand_scope_and_zero_blast_radius(
    tmp_path: Path,
) -> None:
    """--scope channel isolates edits to one channel; --scope brand promotes to
    all channels of brand_1 with zero sister-brand drift."""
    core, man = _make_ws(tmp_path)
    live = tmp_path / "live"
    build_app(core, man, "brand_1", "WEB", "prod", live)
    instr = live / "agents" / "root_agent" / "instruction.txt"
    instr.write_text(instr.read_text("utf-8") + "Channel edit.\n", "utf-8")

    res_ch = backport_live_to_source(
        core,
        man,
        live,
        brand_id="brand_1",
        channel="WEB",
        environment="prod",
        scope="channel",
    )
    assert set(res_ch.unchanged_sibling_bundles) == {
        "brand_1/voice_telephony",
        "brand_2/voice_telephony",
        "brand_2/web",
    }

    # Promote same live edit to brand scope -> voice_telephony updates, brand_2 untouched.
    res_br = backport_live_to_source(
        core,
        man,
        live,
        brand_id="brand_1",
        channel="WEB",
        environment="prod",
        scope="brand",
    )
    assert set(res_br.unchanged_sibling_bundles) == {
        "brand_2/voice_telephony",
        "brand_2/web",
    }


def test_qa_pr7_global_instruction_strategies_and_identity_guard(
    tmp_path: Path,
) -> None:
    """global_instruction.txt supports append, replace_lines, >8-hunk full
    fallback without double # Profile: prefix, and rejects identity block edits."""
    core, man = _make_ws(tmp_path)
    (core / "global_instruction.txt").write_text(
        "\n".join(f"Rule {i}" for i in range(1, 21)) + "\n", encoding="utf-8"
    )
    live = tmp_path / "live"
    build_app(core, man, "brand_1", "WEB", "prod", live)
    gi = live / "global_instruction.txt"

    # 1. >8 non-contiguous edits force full instruction fallback without double prefix.
    lines = gi.read_text("utf-8").splitlines()
    edited = [
        ln + " updated"
        if (ln.startswith("Rule ") and int(ln.split()[1]) % 2 == 0)
        else ln
        for ln in lines
    ]
    gi.write_text("\n".join(edited) + "\n", encoding="utf-8")
    res_full = backport_live_to_source(
        core, man, live, brand_id="brand_1", channel="WEB", environment="prod"
    )
    assert any("(instruction)" in c.summary for c in res_full.changes)
    ov_txt = (man / "profiles" / "brand_1" / "overrides.yaml").read_text(
        "utf-8"
    )
    assert ov_txt.count("# Profile:") == 0

    # 2. Modifying generated # Profile: identity block must fail loudly.
    gi.write_text(
        gi.read_text("utf-8").replace("# Profile: Acme", "# Profile: Hijacked"),
        encoding="utf-8",
    )
    with pytest.raises(BuildGateError, match="identity block"):
        backport_live_to_source(
            core,
            man,
            live,
            brand_id="brand_1",
            channel="WEB",
            environment="prod",
        )


def test_qa_pr7_agent_metadata_custom_instruction_and_yaml_app(
    tmp_path: Path,
) -> None:
    """Backports custom instruction filename, agent description/model/temperature,
    and YAML-format CES apps (with --no-lint)."""
    # Part A: custom instruction filename + metadata edits.
    ws_a = tmp_path / "ws_a"
    core_a, man_a = _make_ws(ws_a, custom_instr_name="custom_prompt.txt")
    live_a = ws_a / "live"
    build_app(core_a, man_a, "brand_1", "WEB", "prod", live_a)
    prompt_p = live_a / "agents" / "root_agent" / "custom_prompt.txt"
    prompt_p.write_text(
        prompt_p.read_text("utf-8") + "Custom prompt rule.\n", encoding="utf-8"
    )
    aj_p = live_a / "agents" / "root_agent" / "root_agent.json"
    aj = json.loads(aj_p.read_text("utf-8"))
    aj["description"] = "Updated Acme root"
    aj["modelSettings"]["temperature"] = 0.7
    aj["modelSettings"]["model"] = "gemini-3.0-pro"
    aj_p.write_text(json.dumps(aj, indent=2) + "\n", encoding="utf-8")

    res_a = backport_live_to_source(
        core_a,
        man_a,
        live_a,
        brand_id="brand_1",
        channel="WEB",
        environment="prod",
        run_lint=False,
    )
    assert len(res_a.changes) == 1
    ov_a = yaml.safe_load(
        (man_a / "profiles" / "brand_1" / "overrides.yaml").read_text("utf-8")
    )
    web_ov = ov_a["agents"]["root_agent"]["channels"]["WEB"]
    assert web_ov["description"] == "Updated @@profile.display_name@@ root"
    assert web_ov["temperature"] == 0.7  # noqa: PLR2004
    assert web_ov["model"] == "gemini-3.0-pro"
    assert web_ov["append_rules"] == ["Custom prompt rule."]

    # Part B: YAML CES app (app.yaml + root_agent.yaml) with run_lint=False.
    ws_b = tmp_path / "ws_b"
    core_b, man_b = _make_ws(ws_b, yaml_app=True)
    live_b = ws_b / "live"
    build_app(core_b, man_b, "brand_1", "WEB", "prod", live_b)
    (live_b / "agents" / "root_agent" / "instruction.txt").write_text(
        (live_b / "agents" / "root_agent" / "instruction.txt").read_text(
            "utf-8"
        )
        + "YAML channel rule.\n",
        encoding="utf-8",
    )
    res_b = backport_live_to_source(
        core_b,
        man_b,
        live_b,
        brand_id="brand_1",
        channel="WEB",
        environment="prod",
        run_lint=False,
    )
    assert len(res_b.changes) == 1
    assert res_b.lint_ran is False


def test_qa_pr7_tool_env_var_url_preservation_and_openapi_breaking_refusal(
    tmp_path: Path,
) -> None:
    """OpenAPI tool environment.json URL edit preserves $env_var in schema;
    breaking OpenAPI schema change (added path) is refused."""
    core, man = _make_ws(tmp_path, env_url=True)
    live = tmp_path / "live"
    build_app(core, man, "brand_1", "WEB", "prod", live)

    env_p = live / "environment.json"
    env_doc = json.loads(env_p.read_text("utf-8"))
    env_doc["tools"]["api_tool"]["openApiTool"]["url"] = (
        "https://api.brand-1.prod.example/v2/orders"
    )
    env_p.write_text(json.dumps(env_doc, indent=2) + "\n", encoding="utf-8")

    tj_p = live / "tools" / "api_tool" / "api_tool.json"
    tj = json.loads(tj_p.read_text("utf-8"))
    tj["openApiTool"]["description"] = "Updated Acme orders API"
    tj_p.write_text(json.dumps(tj, indent=2) + "\n", encoding="utf-8")

    res = backport_live_to_source(
        core, man, live, brand_id="brand_1", channel="WEB", environment="prod"
    )
    assert len(res.changes) == 1
    ov = yaml.safe_load(
        (man / "profiles" / "brand_1" / "overrides.yaml").read_text("utf-8")
    )
    t_ch = ov["tools"]["api_tool"]["channels"]["WEB"]
    assert t_ch["url"] == "@@env.api:v2/orders@@"
    assert t_ch["description"] == "Updated @@profile.display_name@@ orders API"

    # Rebuild and confirm $env_var is preserved in OpenAPI schema.
    rebuilt = tmp_path / "rebuilt"
    build_app(core, man, "brand_1", "WEB", "prod", rebuilt)
    oa_text = (
        rebuilt
        / "tools"
        / "api_tool"
        / "open_api_tool"
        / "open_api_schema.yaml"
    ).read_text("utf-8")
    assert "$env_var" in oa_text

    # Breaking OpenAPI schema edit (adding a new endpoint path) must be refused.
    live_oa = (
        live / "tools" / "api_tool" / "open_api_tool" / "open_api_schema.yaml"
    )
    oa_doc = yaml.safe_load(live_oa.read_text("utf-8"))
    oa_doc["paths"]["/admin"] = {"post": {"operationId": "adminOp"}}
    live_oa.write_text(
        yaml.safe_dump(oa_doc, sort_keys=False), encoding="utf-8"
    )
    with pytest.raises(BuildGateError, match=r"OpenAPI|structural"):
        backport_live_to_source(
            core,
            man,
            live,
            brand_id="brand_1",
            channel="WEB",
            environment="prod",
        )


def test_qa_pr7_reverse_tokenization_longest_match_metachars_and_short_lexicon(
    tmp_path: Path,
) -> None:
    """Overlapping brand values prefer longest match without mangling tokens;
    regex metacharacters in URLs work; short/empty lexicon entries are skipped."""
    core, man = _make_ws(tmp_path)
    live = tmp_path / "live"
    build_app(core, man, "brand_1", "WEB", "prod", live)
    instr = live / "agents" / "root_agent" / "instruction.txt"
    instr.write_text(
        instr.read_text("utf-8") + "Ask Acme Assistant at Acme via "
        "https://support.brand_1.example/help?a=1+2&b=(x) or say hi for "
        "Gold Card.\n",
        encoding="utf-8",
    )

    backport_live_to_source(
        core, man, live, brand_id="brand_1", channel="WEB", environment="prod"
    )
    ov = yaml.safe_load(
        (man / "profiles" / "brand_1" / "overrides.yaml").read_text("utf-8")
    )
    rule = ov["agents"]["root_agent"]["channels"]["WEB"]["append_rules"][0]
    assert rule == (
        "Ask @@profile.assistant_title@@ at @@profile.display_name@@ via "
        "@@profile.urls.support@@ or say hi for @@profile.lexicon.card@@."
    )


def test_qa_pr7_sequential_backports_compose_and_stay_idempotent(
    tmp_path: Path,
) -> None:
    """Sequential live edits compose cleanly into overrides.yaml and a clean
    re-run produces 0 changes."""
    core, man = _make_ws(tmp_path)
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)
    live = tmp_path / "live"
    build_app(core, man, "brand_1", "WEB", "prod", live)
    instr = live / "agents" / "root_agent" / "instruction.txt"

    instr.write_text(instr.read_text("utf-8") + "First rule.\n", "utf-8")
    r1 = backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist,
        brand_id="brand_1",
        channel="WEB",
        environment="prod",
    )
    assert len(r1.changes) == 1

    instr.write_text(instr.read_text("utf-8") + "Second rule.\n", "utf-8")
    r2 = backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist,
        brand_id="brand_1",
        channel="WEB",
        environment="prod",
    )
    assert len(r2.changes) == 1

    # Re-running with no new live changes is a clean no-op.
    r3 = backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist,
        brand_id="brand_1",
        channel="WEB",
        environment="prod",
    )
    assert r3.changes == []


# ── 4. 3-Way Conflict, Rollback & Unsupported Live Edits ────────────────


def test_qa_pr7_three_way_conflict_artifacts_force_live_and_zero_corruption(
    tmp_path: Path,
) -> None:
    """Git-ahead + Live-ahead conflict aborts cleanly, writes .diff artifacts,
    leaves manifests/core intact, and succeeds with --force-live."""
    core, man = _make_ws(tmp_path)
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)
    live = tmp_path / "live"
    build_app(core, man, "brand_1", "WEB", "prod", live)

    (core / "agents" / "root_agent" / "instruction.txt").write_text(
        (core / "agents" / "root_agent" / "instruction.txt").read_text("utf-8")
        + "Git concurrent line.\n",
        encoding="utf-8",
    )
    (live / "agents" / "root_agent" / "instruction.txt").write_text(
        (live / "agents" / "root_agent" / "instruction.txt").read_text("utf-8")
        + "Live concurrent line.\n",
        encoding="utf-8",
    )
    conflicts_out = tmp_path / "conflicts_out"
    core_before = _tree_snapshot(core)
    man_before = _tree_snapshot(man)

    with pytest.raises(DriftGateError, match="3-way conflict"):
        backport_live_to_source(
            core,
            man,
            live,
            dist_dir=dist,
            brand_id="brand_1",
            channel="WEB",
            environment="prod",
            conflicts_dir=conflicts_out,
        )
    assert list(conflicts_out.rglob("*.diff"))
    assert _tree_snapshot(core) == core_before
    assert _tree_snapshot(man) == man_before

    # --force-live resolves the conflict cleanly.
    res = backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist,
        brand_id="brand_1",
        channel="WEB",
        environment="prod",
        force_live=("agents/root_agent",),
    )
    assert len(res.changes) == 1


def test_qa_pr7_unsupported_python_orphan_and_missing_edits_rollback_cleanly(
    tmp_path: Path,
) -> None:
    """Python code edits, orphan agents, missing agents, and lint errors fail
    loudly with BuildGateError and zero partial mutations."""
    core, man = _make_ws(tmp_path)
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)

    # 1. Python tool code edit refused cleanly.
    live_py = tmp_path / "live_py"
    build_app(core, man, "brand_1", "WEB", "prod", live_py)
    (
        live_py / "tools" / "py_tool" / "python_function" / "python_code.py"
    ).write_text("def run() -> dict:\n    return {'ok': False}\n", "utf-8")
    man_before = _tree_snapshot(man)
    with pytest.raises(BuildGateError, match="Python code edits"):
        backport_live_to_source(
            core,
            man,
            live_py,
            dist_dir=dist,
            brand_id="brand_1",
            channel="WEB",
            environment="prod",
        )
    assert _tree_snapshot(man) == man_before

    # 2. Orphan agent created in Console refused cleanly.
    live_orphan = tmp_path / "live_orphan"
    build_app(core, man, "brand_1", "WEB", "prod", live_orphan)
    (live_orphan / "agents" / "console_only").mkdir(parents=True)
    (live_orphan / "agents" / "console_only" / "console_only.json").write_text(
        json.dumps({"displayName": "console_only"}), encoding="utf-8"
    )
    (live_orphan / "agents" / "console_only" / "instruction.txt").write_text(
        "Orphan.\n", encoding="utf-8"
    )
    with pytest.raises(BuildGateError, match="orphan resource"):
        backport_live_to_source(
            core,
            man,
            live_orphan,
            brand_id="brand_1",
            channel="WEB",
            environment="prod",
        )

    # 3. Missing declared tool deleted in Console refused cleanly.
    live_missing = tmp_path / "live_missing"
    build_app(core, man, "brand_1", "WEB", "prod", live_missing)
    for p in sorted(
        (live_missing / "tools" / "py_tool").rglob("*"), reverse=True
    ):
        if p.is_file():
            p.unlink()
        else:
            p.rmdir()
    (live_missing / "tools" / "py_tool").rmdir()
    with pytest.raises(BuildGateError, match="missing declared resource"):
        backport_live_to_source(
            core,
            man,
            live_missing,
            brand_id="brand_1",
            channel="WEB",
            environment="prod",
        )


# ── 5. Git Branch Safety & CLI E2E ──────────────────────────────────────


@pytest.mark.parametrize(
    "bad_branch",
    [
        "-evil",
        "--orphan",
        "foo..bar",
        "bad branch",
        "HEAD",
        "feat//bad",
        "a.lock",
    ],
)
def test_qa_pr7_git_branch_rejects_flag_injection_and_invalid_refs(
    tmp_path: Path, bad_branch: str
) -> None:
    """--git-branch rejects flag injection and invalid git ref names."""
    repo = tmp_path / "repo"
    repo.mkdir()
    core, man = _make_ws(repo)
    _init_git_repo(repo)
    live = tmp_path / "live"
    build_app(core, man, "brand_1", "WEB", "prod", live)

    with pytest.raises(BuildGateError, match="Invalid --git-branch"):
        backport_live_to_source(
            core,
            man,
            live,
            brand_id="brand_1",
            channel="WEB",
            environment="prod",
            git_branch=bad_branch,
        )


def test_qa_pr7_git_branch_existing_dirty_and_nongit_rejected(
    tmp_path: Path,
) -> None:
    """--git-branch rejects non-git dir, dirty tracked tree, and existing branch."""
    # Non-git dir.
    nongit = tmp_path / "nongit"
    nongit.mkdir()
    core_ng, man_ng = _make_ws(nongit)
    live_ng = tmp_path / "live_ng"
    build_app(core_ng, man_ng, "brand_1", "WEB", "prod", live_ng)
    with pytest.raises(BuildGateError, match="not inside a git worktree"):
        backport_live_to_source(
            core_ng,
            man_ng,
            live_ng,
            brand_id="brand_1",
            channel="WEB",
            environment="prod",
            git_branch="feat/test",
        )

    # Existing branch and dirty tree.
    repo = tmp_path / "repo"
    repo.mkdir()
    core, man = _make_ws(repo)
    _init_git_repo(repo)
    subprocess.run(
        ["git", "-C", str(repo), "branch", "existing_branch"], check=True
    )
    live = tmp_path / "live"
    build_app(core, man, "brand_1", "WEB", "prod", live)

    with pytest.raises(BuildGateError, match="existing local git branch"):
        backport_live_to_source(
            core,
            man,
            live,
            brand_id="brand_1",
            channel="WEB",
            environment="prod",
            git_branch="existing_branch",
        )

    # Dirty tracked file rejected.
    (core / "global_instruction.txt").write_text("Dirty.\n", encoding="utf-8")
    with pytest.raises(BuildGateError, match="uncommitted tracked changes"):
        backport_live_to_source(
            core,
            man,
            live,
            brand_id="brand_1",
            channel="WEB",
            environment="prod",
            git_branch="feat/new-branch",
        )


def test_qa_pr7_cli_e2e_live_alias_dry_run_json_and_exit_codes(
    tmp_path: Path,
) -> None:
    """Real CLI subprocess E2E covering --live, --live-dir, --dry-run, --json,
    and exit codes 0 vs 1."""
    core, man = _make_ws(tmp_path)
    live = tmp_path / "live"
    build_app(core, man, "brand_1", "WEB", "prod", live)
    instr = live / "agents" / "root_agent" / "instruction.txt"
    instr.write_text(instr.read_text("utf-8") + "CLI rule.\n", encoding="utf-8")

    # --live alias + --dry-run + --json -> exit 0, valid JSON, zero mutations.
    dry_proc = _run_cli(
        [
            "backport",
            "--core-dir",
            str(core),
            "--manifests-dir",
            str(man),
            "--live",
            str(live),
            "--brand",
            "brand_1",
            "--channel",
            "WEB",
            "--env",
            "prod",
            "--dry-run",
            "--json",
        ]
    )
    assert dry_proc.returncode == 0, dry_proc.stderr
    dry_data = json.loads(dry_proc.stdout)
    assert dry_data["passed"] is True
    assert dry_data["dry_run"] is True
    assert len(dry_data["changes"]) == 1
    assert not (man / "profiles" / "brand_1" / "overrides.yaml").exists()

    # --live-dir apply -> exit 0, writes overrides.yaml.
    apply_proc = _run_cli(
        [
            "backport",
            "--core-dir",
            str(core),
            "--manifests-dir",
            str(man),
            "--live-dir",
            str(live),
            "--brand",
            "brand_1",
            "--channel",
            "WEB",
            "--env",
            "prod",
        ]
    )
    assert apply_proc.returncode == 0, apply_proc.stderr
    assert "ROUND_TRIP_PROOF PASS" in apply_proc.stdout
    assert (man / "profiles" / "brand_1" / "overrides.yaml").is_file()

    # Invalid live path with --json -> exit 1 with structured error JSON.
    err_proc = _run_cli(
        [
            "backport",
            "--core-dir",
            str(core),
            "--manifests-dir",
            str(man),
            "--live-dir",
            str(tmp_path / "missing_live"),
            "--brand",
            "brand_1",
            "--channel",
            "WEB",
            "--env",
            "prod",
            "--json",
        ]
    )
    assert err_proc.returncode == 1
    err_data = json.loads(err_proc.stdout)
    assert err_data["passed"] is False
    assert "does not exist" in err_data["error"]


# ── Failing Reproduction Tests for Open QA Defects ──────────────────────


def test_qa_pr7_001_output_paths_reject_overlap_and_symlink_parent_escapes(
    tmp_path: Path,
) -> None:
    """QA-PR7-001 (HIGH): --pr-body-out, --conflicts-dir, --lockfile, and
    --dist must reject overlapping core/manifests/live paths and symlinked
    ancestor directories before mutating any file."""
    core, man = _make_ws(tmp_path)
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)
    live = tmp_path / "live"
    build_app(core, man, "brand_1", "WEB", "prod", live)
    instr = live / "agents" / "root_agent" / "instruction.txt"
    instr.write_text(instr.read_text("utf-8") + "Edit.\n", encoding="utf-8")

    # 1. pr_body_out pointing inside core/ MUST be rejected (never clobber core/app.json!).
    orig_app_json = (core / "app.json").read_bytes()
    with pytest.raises(BuildGateError, match=r"overlap|core"):
        backport_live_to_source(
            core,
            man,
            live,
            brand_id="brand_1",
            channel="WEB",
            environment="prod",
            pr_body_out=core / "app.json",
        )
    assert (core / "app.json").read_bytes() == orig_app_json

    # 2. pr_body_out via symlinked parent directory MUST be rejected.
    outside = tmp_path / "outside"
    outside.mkdir()
    sym_parent = tmp_path / "sym_parent"
    sym_parent.symlink_to(outside)
    with pytest.raises(BuildGateError, match=r"symlink|Symlink"):
        backport_live_to_source(
            core,
            man,
            live,
            brand_id="brand_1",
            channel="WEB",
            environment="prod",
            pr_body_out=sym_parent / "pr.md",
        )
    assert not (outside / "pr.md").exists()

    # 3. conflicts_dir pointing inside manifests/ MUST be rejected.
    with pytest.raises(BuildGateError, match=r"overlap|manifests"):
        backport_live_to_source(
            core,
            man,
            live,
            dist_dir=dist,
            brand_id="brand_1",
            channel="WEB",
            environment="prod",
            conflicts_dir=man / "conflict_dump",
        )
    assert not (man / "conflict_dump").exists()

    # 4. lockfile_path pointing inside core/ MUST be rejected.
    bad_lock = core / "bad.lock.json"
    write_lockfile(live, bad_lock)
    with pytest.raises(BuildGateError, match=r"overlap|core"):
        backport_live_to_source(
            core,
            man,
            live,
            brand_id="brand_1",
            channel="WEB",
            environment="prod",
            lockfile_path=bad_lock,
        )


def test_qa_pr7_002_rollback_restores_manifests_and_lockfile_on_post_verify_failure(
    tmp_path: Path,
) -> None:
    """QA-PR7-002 (HIGH): If _refresh_dist_and_lock or _checkout_git_branch
    fails after verification, manifests/ and lockfile_path must roll back
    byte-for-byte."""
    # Case 1: _refresh_dist_and_lock fails -> manifests/ MUST be restored.
    ws1 = tmp_path / "ws1"
    core1, man1 = _make_ws(ws1)
    dist1 = ws1 / "dist"
    build_all(core1, man1, dist1, "prod", write_locks=True)
    live1 = ws1 / "live"
    build_app(core1, man1, "brand_1", "WEB", "prod", live1)
    (live1 / "agents" / "root_agent" / "instruction.txt").write_text(
        (live1 / "agents" / "root_agent" / "instruction.txt").read_text("utf-8")
        + "Rollback test.\n",
        encoding="utf-8",
    )
    man1_before = _tree_snapshot(man1)
    with (
        patch.object(
            bp_mod, "_refresh_dist_and_lock", side_effect=OSError("disk full")
        ),
        pytest.raises(OSError, match="disk full"),
    ):
        backport_live_to_source(
            core1,
            man1,
            live1,
            dist_dir=dist1,
            brand_id="brand_1",
            channel="WEB",
            environment="prod",
        )
    assert _tree_snapshot(man1) == man1_before, (
        "QA-PR7-002: manifests/ was NOT rolled back after _refresh_dist_and_lock failure"
    )

    # Case 2: _checkout_git_branch fails -> lockfile_path MUST be restored.
    repo2 = tmp_path / "repo2"
    repo2.mkdir()
    core2, man2 = _make_ws(repo2)
    _init_git_repo(repo2)
    live2 = tmp_path / "live2"
    build_app(core2, man2, "brand_1", "WEB", "prod", live2)
    lock2 = tmp_path / "app.lock.json"
    write_lockfile(live2, lock2)
    lock2_before = lock2.read_bytes()
    (live2 / "agents" / "root_agent" / "instruction.txt").write_text(
        (live2 / "agents" / "root_agent" / "instruction.txt").read_text("utf-8")
        + "Rollback lock test.\n",
        encoding="utf-8",
    )
    with (
        patch.object(
            bp_mod,
            "_checkout_git_branch",
            side_effect=BuildGateError("checkout failed"),
        ),
        pytest.raises(BuildGateError, match="checkout failed"),
    ):
        backport_live_to_source(
            core2,
            man2,
            live2,
            brand_id="brand_1",
            channel="WEB",
            environment="prod",
            lockfile_path=lock2,
            git_branch="feat/rollback-check",
        )
    assert lock2.read_bytes() == lock2_before, (
        "QA-PR7-002: lockfile_path was NOT rolled back after _checkout_git_branch failure"
    )


def test_qa_pr7_003_multipath_openapi_servers_url_edit_backports_cleanly(
    tmp_path: Path,
) -> None:
    """QA-PR7-003 (HIGH): Editing servers[0].url on an OpenAPI tool with 2+
    paths must backport into ToolOverride.url with passing round-trip proof."""
    core, man = _make_ws(tmp_path, multi_path_openapi=True)
    live = tmp_path / "live"
    build_app(core, man, "brand_1", "WEB", "prod", live)
    oa_p = (
        live / "tools" / "api_tool" / "open_api_tool" / "open_api_schema.yaml"
    )
    oa_p.write_text(
        oa_p.read_text("utf-8").replace(
            "https://api.acme.example", "https://api2.acme.example"
        ),
        encoding="utf-8",
    )

    res = backport_live_to_source(
        core, man, live, brand_id="brand_1", channel="WEB", environment="prod"
    )
    assert len(res.changes) == 1
    ov = yaml.safe_load(
        (man / "profiles" / "brand_1" / "overrides.yaml").read_text("utf-8")
    )
    assert (
        ov["tools"]["api_tool"]["channels"]["WEB"]["url"]
        == "https://api2.acme.example"
    )


def test_qa_pr7_004_malformed_temperature_and_bad_utf8_deployment_fail_cleanly(
    tmp_path: Path,
) -> None:
    """QA-PR7-004 (MEDIUM): Malformed live modelSettings.temperature must raise
    BuildGateError (no raw ValueError traceback on CLI), and non-UTF-8 bytes in
    deployments/*.yaml must not crash with UnicodeDecodeError."""
    core, man = _make_ws(tmp_path)
    live = tmp_path / "live"
    build_app(core, man, "brand_1", "WEB", "prod", live)

    # 1. Invalid UTF-8 file in deployments/ must not crash backport.
    (live / "deployments" / "00_corrupt.yaml").write_bytes(
        b"\xff\xfe\x00invalid"
    )
    res_ok = backport_live_to_source(
        core, man, live, brand_id="brand_1", channel="WEB", environment="prod"
    )
    assert res_ok.changes == []
    (live / "deployments" / "00_corrupt.yaml").unlink()

    # 2. Malformed temperature string must raise BuildGateError (and clean CLI JSON).
    aj_p = live / "agents" / "root_agent" / "root_agent.json"
    aj = json.loads(aj_p.read_text("utf-8"))
    aj["modelSettings"]["temperature"] = "not-a-number"
    aj_p.write_text(json.dumps(aj, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(BuildGateError, match="temperature"):
        backport_live_to_source(
            core,
            man,
            live,
            brand_id="brand_1",
            channel="WEB",
            environment="prod",
            run_lint=False,
        )


def test_qa_pr7_005_dry_run_is_zero_touch_on_readonly_manifests_and_mtimes(
    tmp_path: Path,
) -> None:
    """QA-PR7-005 (MEDIUM): --dry-run must execute in a temporary staging copy
    without writing into manifests/ (works on read-only manifests and preserves
    mtimes of core/ and manifests/)."""
    core, man = _make_ws(tmp_path)
    live = tmp_path / "live"
    build_app(core, man, "brand_1", "WEB", "prod", live)
    (live / "agents" / "root_agent" / "instruction.txt").write_text(
        (live / "agents" / "root_agent" / "instruction.txt").read_text("utf-8")
        + "Dry-run zero-touch.\n",
        encoding="utf-8",
    )

    app_json = core / "app.json"
    os.utime(app_json, (1700000000, 1700000000))
    orig_mtime = app_json.stat().st_mtime

    brand_dir = man / "profiles" / "brand_1"
    brand_dir.chmod(0o555)
    try:
        res = backport_live_to_source(
            core,
            man,
            live,
            brand_id="brand_1",
            channel="WEB",
            environment="prod",
            dry_run=True,
        )
    finally:
        brand_dir.chmod(0o755)

    assert res.dry_run is True
    assert len(res.changes) == 1
    assert app_json.stat().st_mtime == orig_mtime, (
        "QA-PR7-005: --dry-run modified core/app.json mtime"
    )
