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

"""Adversarial QA & E2E tests for PR 8 (`--target core`, deployments sync, multibrand init).

Includes passing edge-case & real CLI E2E checks across all PR 8 focus areas plus
labeled failing reproduction tests (`QA-PR8-001`..`QA-PR8-005`) for open defects.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from google.cloud.ces_v1beta import types

import cxas_scrapi.build.backport as backport_mod
from cxas_scrapi.build.backport import (
    _patch_core_instruction_preserving_conditionals,
    backport_live_to_source,
)
from cxas_scrapi.build.builder import build_all
from cxas_scrapi.build.bundle_io import BuildGateError
from cxas_scrapi.cli.app import _app_pull, _app_push
from cxas_scrapi.core.deployment_sync import (
    export_deployments_to_dir,
    sync_deployments_from_dir,
)
from cxas_scrapi.core.deployments import Deployments
from cxas_scrapi.core.packaging import VALID_ARCHIVE_ROOTS, package_app_archive

_WORKTREE_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _WORKTREE_ROOT / "src"


def _run_cxas(
    *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(_SRC_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "cxas_scrapi.cli.main", *args],
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=120,
    )


def _scaffold_starter(ws: Path) -> tuple[Path, Path]:
    proc = _run_cxas(
        "init",
        "--template",
        "multibrand",
        "--target-dir",
        str(ws),
        "--no-input",
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    return ws / "core", ws / "manifests"


def _chmod_tree_readonly(root: Path) -> None:
    files: list[Path] = []
    dirs: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        dp = Path(dirpath)
        dirs.append(dp)
        for fn in filenames:
            files.append(dp / fn)
    for fp in files:
        os.chmod(fp, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    for dp in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
        os.chmod(
            dp,
            stat.S_IRUSR
            | stat.S_IXUSR
            | stat.S_IRGRP
            | stat.S_IXGRP
            | stat.S_IROTH
            | stat.S_IXOTH,
        )


def _chmod_tree_writable(root: Path) -> None:
    for dirpath, _dirnames, filenames in os.walk(root):
        dp = Path(dirpath)
        os.chmod(dp, stat.S_IRWXU)
        for fn in filenames:
            fp = dp / fn
            if not fp.is_symlink():
                os.chmod(fp, stat.S_IRUSR | stat.S_IWUSR)


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.is_symlink() and ".git" not in p.parts
    }


def _make_real_deployments_client() -> tuple[Deployments, MagicMock]:
    client = Deployments.__new__(Deployments)
    client.app_name = "projects/p/locations/us/apps/a"
    client.creds = None
    gapic = MagicMock()
    gapic.list_deployments.return_value = []
    client.client = gapic
    return client, gapic


# ── 1. cxas backport --target core ──────────────────────────────────────


def test_qa_pr8_core_modality_tags_and_global_identity_prefix_preserved(
    tmp_path: Path,
) -> None:
    """Core backport preserves {@startModality_Voice}, inactive {@startChannel_*}, and # Profile: prefix."""
    ws = tmp_path / "ws"
    core, manifests = _scaffold_starter(ws)

    inst_core = core / "agents" / "root_agent" / "instruction.txt"
    inst_core.write_text(
        "You help @@profile.display_name@@ users.\n"
        "{@startModality_Voice}\n"
        "Keep spoken replies under 2 sentences.\n"
        "{@endModality_Voice}\n"
        "{@startChannel_VOICE_TELEPHONY}\n"
        "Read confirmation numbers slowly.\n"
        "{@endChannel_VOICE_TELEPHONY}\n"
        "Use {@TOOL: lookup_faq} when needed.\n",
        encoding="utf-8",
    )
    dist = ws / "dist"
    build_all(core, manifests, dist, "prod", write_locks=True)

    live = ws / "live"
    shutil.copytree(dist / "brand_alpha" / "digital_app", live)
    live_inst = live / "agents" / "root_agent" / "instruction.txt"
    live_inst.write_text(
        live_inst.read_text(encoding="utf-8").replace(
            "Keep spoken replies under 2 sentences.",
            "Keep spoken replies under 1 sentence.",
        ),
        encoding="utf-8",
    )
    live_gi = live / "global_instruction.txt"
    live_gi.write_text(
        live_gi.read_text(encoding="utf-8")
        + "Always verify account ownership.\n",
        encoding="utf-8",
    )

    res = backport_live_to_source(
        core,
        manifests,
        live,
        dist_dir=dist,
        brand_id="brand_alpha",
        channel="DIGITAL_APP",
        environment="prod",
        target="core",
    )
    assert res.lint_errors == 0
    core_after = inst_core.read_text(encoding="utf-8")
    assert "{@startModality_Voice}" in core_after
    assert "{@endModality_Voice}" in core_after
    assert "Keep spoken replies under 1 sentence." in core_after
    assert "{@startChannel_VOICE_TELEPHONY}" in core_after
    assert "Read confirmation numbers slowly." in core_after
    assert "{@endChannel_VOICE_TELEPHONY}" in core_after

    # Rebuilt global_instruction still starts with '# Profile:' identity block
    rebuilt_gi = (
        dist / "brand_beta" / "digital_app" / "global_instruction.txt"
    ).read_text(encoding="utf-8")
    assert rebuilt_gi.startswith("# Profile:")
    assert "Always verify account ownership." in rebuilt_gi


def test_qa_pr8_002_core_instruction_multiline_token_preserves_active_channel_tags(
    tmp_path: Path,
) -> None:
    """Repro QA-PR8-002 (HIGH): Multi-line token fallback strips active {@startChannel_*} wrappers."""
    raw_core = (
        "Tone: @@profile.tone_guidelines@@\n"
        "{@startChannel_DIGITAL_APP}\n"
        "Show clickable rich cards.\n"
        "{@endChannel_DIGITAL_APP}\n"
        "{@startChannel_VOICE_TELEPHONY}\n"
        "Speak slowly over phone.\n"
        "{@endChannel_VOICE_TELEPHONY}\n"
        "Shared closing line.\n"
    )
    base_built = (
        "Tone: Be warm.\nBe concise.\n"
        "Show clickable rich cards.\n"
        "Shared closing line.\n"
    )
    live_built = (
        "Tone: Be warm.\nBe concise.\n"
        "Show clickable rich cards.\n"
        "Updated shared closing line.\n"
    )
    patched = _patch_core_instruction_preserving_conditionals(
        raw_core,
        base_built,
        live_built,
        brand_id="brand_alpha",
        channel="DIGITAL_APP",
        disabled_agents=set(),
        disabled_tools=set(),
        rev_fn=lambda s: s,
    )
    assert "{@startChannel_DIGITAL_APP}" in patched
    assert "{@endChannel_DIGITAL_APP}" in patched
    assert "{@startChannel_VOICE_TELEPHONY}" in patched

    # End-to-end verification: VOICE_TELEPHONY must NOT receive DIGITAL_APP cards
    ws = tmp_path / "ws"
    core, manifests = _scaffold_starter(ws)
    id_p = manifests / "profiles" / "brand_alpha" / "identity.yaml"
    id_data = yaml.safe_load(id_p.read_text(encoding="utf-8"))
    id_data["tone_guidelines"] = "Be warm.\nBe concise.\n"
    id_p.write_text(
        yaml.safe_dump(id_data, sort_keys=False),
        encoding="utf-8",
    )
    (core / "agents" / "root_agent" / "instruction.txt").write_text(
        raw_core, encoding="utf-8"
    )
    dist = ws / "dist"
    build_all(core, manifests, dist, "prod", write_locks=True)
    live = ws / "live"
    shutil.copytree(dist / "brand_alpha" / "digital_app", live)
    li = live / "agents" / "root_agent" / "instruction.txt"
    li.write_text(
        li.read_text(encoding="utf-8").replace(
            "Shared closing line.", "Updated shared closing line."
        ),
        encoding="utf-8",
    )
    backport_live_to_source(
        core,
        manifests,
        live,
        dist_dir=dist,
        brand_id="brand_alpha",
        channel="DIGITAL_APP",
        environment="prod",
        target="core",
        run_lint=False,
    )
    voice_inst = (
        dist
        / "brand_alpha"
        / "voice_telephony"
        / "agents"
        / "root_agent"
        / "instruction.txt"
    ).read_text(encoding="utf-8")
    assert "Show clickable rich cards." not in voice_inst


def test_qa_pr8_core_brand_only_lexicon_not_reverse_tokenized_into_core(
    tmp_path: Path,
) -> None:
    """A lexicon key defined only on brand_alpha must NOT be lifted into core/."""
    ws = tmp_path / "ws"
    core, manifests = _scaffold_starter(ws)
    id_alpha = manifests / "profiles" / "brand_alpha" / "identity.yaml"
    id_alpha_doc = yaml.safe_load(id_alpha.read_text(encoding="utf-8"))
    id_alpha_doc.setdefault("lexicon", {})["alpha_only_perk"] = "PlatinumLounge"
    id_alpha.write_text(yaml.safe_dump(id_alpha_doc), encoding="utf-8")

    dist = ws / "dist"
    build_all(core, manifests, dist, "prod", write_locks=True)
    live = ws / "live"
    shutil.copytree(dist / "brand_alpha" / "digital_app", live)
    li = live / "agents" / "root_agent" / "instruction.txt"
    li.write_text(
        li.read_text(encoding="utf-8") + "Mention PlatinumLounge benefits.\n",
        encoding="utf-8",
    )
    res = backport_live_to_source(
        core,
        manifests,
        live,
        dist_dir=dist,
        brand_id="brand_alpha",
        channel="DIGITAL_APP",
        environment="prod",
        target="core",
    )
    assert res.lint_errors == 0
    core_inst = (core / "agents" / "root_agent" / "instruction.txt").read_text(
        encoding="utf-8"
    )
    assert "@@profile.lexicon.alpha_only_perk@@" not in core_inst
    assert "PlatinumLounge" in core_inst


def test_qa_pr8_001_core_python_reverse_tokenization_preserves_identifiers(
    tmp_path: Path,
) -> None:
    """Repro QA-PR8-001 (HIGH): Python reverse-tokenization corrupts identifiers outside string literals."""
    ws = tmp_path / "ws"
    core, manifests = _scaffold_starter(ws)
    for brand_id, prod_val in (
        ("brand_alpha", "credit"),
        ("brand_beta", "debit"),
    ):
        idp = manifests / "profiles" / brand_id / "identity.yaml"
        doc = yaml.safe_load(idp.read_text(encoding="utf-8"))
        doc.setdefault("lexicon", {})["product_kind"] = prod_val
        idp.write_text(yaml.safe_dump(doc), encoding="utf-8")

    tdir = core / "tools" / "check_credit_score"
    (tdir / "python_function").mkdir(parents=True)
    (tdir / "check_credit_score.json").write_text(
        json.dumps(
            {
                "displayName": "check_credit_score",
                "pythonFunction": {
                    "name": "check_credit_score",
                    "description": "Check score.",
                    "pythonCode": "tools/check_credit_score/python_function/python_code.py",
                },
            }
        ),
        encoding="utf-8",
    )
    (tdir / "python_function" / "python_code.py").write_text(
        "def check_credit_score(credit_id: str) -> dict:\n"
        '    """Return score."""\n'
        "    return {'credit_id': credit_id, 'kind': 'credit', 'ok': True}\n",
        encoding="utf-8",
    )
    ag_p = core / "agents" / "root_agent" / "root_agent.json"
    ag_doc = json.loads(ag_p.read_text(encoding="utf-8"))
    ag_doc["tools"].append("check_credit_score")
    ag_p.write_text(json.dumps(ag_doc), encoding="utf-8")

    dist = ws / "dist"
    build_all(core, manifests, dist, "prod", write_locks=True)

    live = ws / "live"
    shutil.copytree(dist / "brand_alpha" / "digital_app", live)
    lpy = (
        live
        / "tools"
        / "check_credit_score"
        / "python_function"
        / "python_code.py"
    )
    lpy.write_text(
        "def check_credit_score(credit_id: str) -> dict:\n"
        '    """Return verified credit score."""\n'
        "    return {'credit_id': credit_id, 'kind': 'credit', 'ok': True}\n",
        encoding="utf-8",
    )

    res = backport_live_to_source(
        core,
        manifests,
        live,
        dist_dir=dist,
        brand_id="brand_alpha",
        channel="DIGITAL_APP",
        environment="prod",
        target="core",
        run_lint=False,
    )
    assert res.changes
    core_py = (tdir / "python_function" / "python_code.py").read_text(
        encoding="utf-8"
    )
    assert "def check_credit_score(credit_id: str) -> dict:" in core_py
    assert "@@profile.lexicon.product_kind@@" in core_py


def test_qa_pr8_core_python_rejects_syntax_error_tampered_bundle_and_shared_lib_drift(
    tmp_path: Path,
) -> None:
    """Core backport rejects invalid Python syntax, tampered bundle markers, and python_lib digest drift."""
    ws = tmp_path / "ws"
    core, manifests = _scaffold_starter(ws)

    # Add shared python_lib module and a callback importing it
    py_lib = core / "python_lib"
    py_lib.mkdir()
    (py_lib / "__init__.py").write_text("", encoding="utf-8")
    (py_lib / "helpers.py").write_text(
        "def tag() -> str:\n    return 'v1'\n", encoding="utf-8"
    )
    cb_dir = core / "agents" / "root_agent" / "before_agent_callbacks" / "cb_01"
    cb_dir.mkdir(parents=True)
    (cb_dir / "python_code.py").write_text(
        "from python_lib.helpers import tag\n\n"
        "def before_agent_callback(callback_context):\n"
        "    _ = tag()\n"
        "    return None\n",
        encoding="utf-8",
    )
    dist = ws / "dist"
    build_all(core, manifests, dist, "prod", write_locks=True)

    # 1. Invalid Python syntax
    live_bad_syn = ws / "live_bad_syn"
    shutil.copytree(dist / "brand_alpha" / "digital_app", live_bad_syn)
    cb_syn = (
        live_bad_syn
        / "agents"
        / "root_agent"
        / "before_agent_callbacks"
        / "cb_01"
        / "python_code.py"
    )
    cb_syn.write_text("def before_agent_callback(:\n", encoding="utf-8")
    with pytest.raises(
        BuildGateError, match=r"not valid Python|invalid Python syntax"
    ):
        backport_live_to_source(
            core,
            manifests,
            live_bad_syn,
            brand_id="brand_alpha",
            channel="DIGITAL_APP",
            environment="prod",
            target="core",
            run_lint=False,
        )

    # 2. Tampered CXAS_SHARED_BUNDLE (mutated shared helper digest)
    live_drift = ws / "live_drift"
    shutil.copytree(dist / "brand_alpha" / "digital_app", live_drift)
    cb_drift = (
        live_drift
        / "agents"
        / "root_agent"
        / "before_agent_callbacks"
        / "cb_01"
        / "python_code.py"
    )
    cb_drift.write_text(
        cb_drift.read_text(encoding="utf-8").replace(
            "return 'v1'", "return 'v2'"
        ),
        encoding="utf-8",
    )
    with pytest.raises(
        BuildGateError, match=r"Block tampered in Console|CXAS_SHARED_BUNDLE"
    ):
        backport_live_to_source(
            core,
            manifests,
            live_drift,
            brand_id="brand_alpha",
            channel="DIGITAL_APP",
            environment="prod",
            target="core",
            run_lint=False,
        )


def test_qa_pr8_core_override_cleanup_preserves_unrelated_channel_and_model_overrides(
    tmp_path: Path,
) -> None:
    """Promoting DIGITAL_APP edits prunes only superseded overrides, preserving VOICE and model/temperature."""
    ws = tmp_path / "ws"
    core, manifests = _scaffold_starter(ws)
    ov_path = manifests / "profiles" / "brand_alpha" / "overrides.yaml"
    ov_path.write_text(
        yaml.safe_dump(
            {
                "profile_id": "brand_alpha",
                "agents": {
                    "root_agent": {
                        "temperature": 0.25,
                        "channels": {
                            "DIGITAL_APP": {
                                "append_rules": ["Digital rule to promote."],
                            },
                            "VOICE_TELEPHONY": {
                                "append_rules": ["Keep voice-only rule."],
                            },
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    dist = ws / "dist"
    build_all(core, manifests, dist, "prod", write_locks=True)

    live = ws / "live"
    shutil.copytree(dist / "brand_alpha" / "digital_app", live)
    backport_live_to_source(
        core,
        manifests,
        live,
        dist_dir=dist,
        brand_id="brand_alpha",
        channel="DIGITAL_APP",
        environment="prod",
        target="core",
    )
    assert ov_path.is_file()
    remaining = yaml.safe_load(ov_path.read_text(encoding="utf-8"))
    ag_rem = remaining["agents"]["root_agent"]
    assert ag_rem["temperature"] == 0.25
    assert "DIGITAL_APP" not in ag_rem.get("channels", {})
    assert ag_rem["channels"]["VOICE_TELEPHONY"]["append_rules"] == [
        "Keep voice-only rule."
    ]


def test_qa_pr8_core_dry_run_readonly_zero_mutations_and_remapped_paths(
    tmp_path: Path,
) -> None:
    """--target core --dry-run on 0o555/0o444 core/ & manifests/ performs zero disk writes."""
    ws = tmp_path / "ws"
    core, manifests = _scaffold_starter(ws)
    dist = ws / "dist"
    build_all(core, manifests, dist, "prod", write_locks=True)

    live = ws / "live"
    shutil.copytree(dist / "brand_alpha" / "digital_app", live)
    li = live / "agents" / "root_agent" / "instruction.txt"
    li.write_text(
        li.read_text(encoding="utf-8") + "Dry-run core preview line.\n",
        encoding="utf-8",
    )

    core_before = _tree_snapshot(core)
    man_before = _tree_snapshot(manifests)
    mtimes_before = {
        p: p.stat().st_mtime_ns
        for p in [*core.rglob("*"), *manifests.rglob("*")]
        if p.is_file()
    }

    _chmod_tree_readonly(core)
    _chmod_tree_readonly(manifests)
    try:
        res = backport_live_to_source(
            core,
            manifests,
            live,
            dist_dir=dist,
            brand_id="brand_alpha",
            channel="DIGITAL_APP",
            environment="prod",
            target="core",
            dry_run=True,
        )
    finally:
        _chmod_tree_writable(core)
        _chmod_tree_writable(manifests)

    assert res.dry_run is True
    assert res.lint_errors == 0
    assert res.modified_files == [
        (core / "agents" / "root_agent" / "instruction.txt").resolve()
    ]
    assert _tree_snapshot(core) == core_before
    assert _tree_snapshot(manifests) == man_before
    for p, mt in mtimes_before.items():
        assert p.stat().st_mtime_ns == mt


def test_qa_pr8_core_atomic_rollback_on_refresh_and_git_checkout_failure(
    tmp_path: Path,
) -> None:
    """Failure during _refresh_dist_and_lock or _checkout_git_branch rolls back core, manifests, dist, and lockfile."""
    ws = tmp_path / "ws"
    core, manifests = _scaffold_starter(ws)
    dist = ws / "dist"
    build_all(core, manifests, dist, "prod", write_locks=True)

    live = ws / "live"
    shutil.copytree(dist / "brand_alpha" / "digital_app", live)
    li = live / "agents" / "root_agent" / "instruction.txt"
    li.write_text(
        li.read_text(encoding="utf-8") + "Should be rolled back.\n",
        encoding="utf-8",
    )

    core_before = _tree_snapshot(core)
    man_before = _tree_snapshot(manifests)
    dist_before = _tree_snapshot(dist)
    lock_p = dist / "brand_alpha" / "digital_app.lock.json"
    lock_before = lock_p.read_bytes()

    with (
        patch.object(
            backport_mod,
            "_refresh_dist_and_lock",
            side_effect=RuntimeError("simulated dist failure"),
        ),
        pytest.raises(RuntimeError, match="simulated dist failure"),
    ):
        backport_live_to_source(
            core,
            manifests,
            live,
            dist_dir=dist,
            lockfile_path=lock_p,
            brand_id="brand_alpha",
            channel="DIGITAL_APP",
            environment="prod",
            target="core",
            run_lint=False,
        )

    assert _tree_snapshot(core) == core_before
    assert _tree_snapshot(manifests) == man_before
    assert _tree_snapshot(dist) == dist_before
    assert lock_p.read_bytes() == lock_before


def test_qa_pr8_core_yaml_app_custom_instruction_and_openapi_breaking_refusal(
    tmp_path: Path,
) -> None:
    """YAML CES app + custom instruction filename works; structural OpenAPI breaking change is refused."""
    ws = tmp_path / "ws"
    core, manifests = _scaffold_starter(ws)

    app_data = json.loads((core / "app.json").read_text(encoding="utf-8"))
    (core / "app.json").unlink()
    (core / "app.yaml").write_text(yaml.safe_dump(app_data), encoding="utf-8")

    ag_dir = core / "agents" / "root_agent"
    ag_data = json.loads(
        (ag_dir / "root_agent.json").read_text(encoding="utf-8")
    )
    (ag_dir / "root_agent.json").unlink()
    ag_data["instruction"] = "agents/root_agent/custom_prompt.txt"
    (ag_dir / "root_agent.yaml").write_text(
        yaml.safe_dump(ag_data), encoding="utf-8"
    )
    (ag_dir / "instruction.txt").rename(ag_dir / "custom_prompt.txt")

    dist = ws / "dist"
    build_all(core, manifests, dist, "prod", write_locks=True)

    # 1. Safe custom instruction edit on YAML app succeeds
    live = ws / "live"
    shutil.copytree(dist / "brand_alpha" / "digital_app", live)
    p_custom = live / "agents" / "root_agent" / "custom_prompt.txt"
    p_custom.write_text(
        p_custom.read_text(encoding="utf-8") + "Custom YAML prompt line.\n",
        encoding="utf-8",
    )
    res = backport_live_to_source(
        core,
        manifests,
        live,
        dist_dir=dist,
        brand_id="brand_alpha",
        channel="DIGITAL_APP",
        environment="prod",
        target="core",
        run_lint=False,
    )
    assert (ag_dir / "custom_prompt.txt").resolve() in res.modified_files
    assert "Custom YAML prompt line." in (
        ag_dir / "custom_prompt.txt"
    ).read_text(encoding="utf-8")

    # 2. Breaking OpenAPI schema change (adding required parameter) is refused
    live_oa_break = ws / "live_oa_break"
    shutil.copytree(dist / "brand_alpha" / "digital_app", live_oa_break)
    oa_file = (
        live_oa_break
        / "tools"
        / "lookup_faq"
        / "open_api_tool"
        / "open_api_schema.yaml"
    )
    oa_doc = yaml.safe_load(oa_file.read_text(encoding="utf-8"))
    first_path = next(iter(oa_doc["paths"].keys()))
    first_op = next(iter(oa_doc["paths"][first_path].keys()))
    oa_doc["paths"][first_path][first_op].setdefault("parameters", []).append(
        {
            "name": "breaking_param",
            "in": "query",
            "required": True,
            "schema": {"type": "string"},
        }
    )
    oa_file.write_text(yaml.safe_dump(oa_doc), encoding="utf-8")

    with pytest.raises(BuildGateError, match="OpenAPI schema"):
        backport_live_to_source(
            core,
            manifests,
            live_oa_break,
            dist_dir=dist,
            brand_id="brand_alpha",
            channel="DIGITAL_APP",
            environment="prod",
            target="core",
            run_lint=False,
        )


# ── 2. deployments/*.yaml Pull/Push Sync ────────────────────────────────


@pytest.mark.parametrize(
    "bad_id",
    ["../escape", "/abs", "a/b", r"a\b", "", "   ", "bad:id"],
)
def test_qa_pr8_export_deployments_rejects_malicious_ids_and_symlinks(
    tmp_path: Path, bad_id: str
) -> None:
    """export_deployments_to_dir rejects path-traversal IDs, symlinks, and preserves build metadata."""
    app_root = tmp_path / "app"
    dep_dir = app_root / "deployments"
    dep_dir.mkdir(parents=True)
    meta_file = dep_dir / "brand_alpha_digital.yaml"
    meta_file.write_text(
        "profileId: brand_alpha\nchannel: DIGITAL_APP\nenvironment: prod\n",
        encoding="utf-8",
    )

    bad_client = MagicMock()
    bad_client.list_deployments.return_value = [{"deploymentId": bad_id}]
    with pytest.raises(ValueError, match="deploymentId"):
        export_deployments_to_dir(bad_client, app_root)
    assert meta_file.is_file()

    # Valid export strips volatile fields and preserves build metadata
    good_client = MagicMock()
    good_client.list_deployments.return_value = [
        {
            "name": "projects/p/locations/us/apps/a/deployments/web-prod",
            "displayName": "Web Prod",
            "appVersion": "projects/p/locations/us/apps/a/versions/v1",
            "createTime": "2026-01-01T00:00:00Z",
            "updateTime": "2026-01-02T00:00:00Z",
            "etag": "abc123",
            "channelProfile": {
                "channelType": "WEB_UI",
                "webWidgetConfig": {"modality": "CHAT_ONLY", "theme": "DARK"},
            },
        }
    ]
    written = export_deployments_to_dir(good_client, app_root)
    assert [p.name for p in written] == ["web-prod.yaml"]
    exported = yaml.safe_load(written[0].read_text(encoding="utf-8"))
    for volatile in ("name", "createTime", "updateTime", "etag"):
        assert volatile not in exported
    assert exported["deploymentId"] == "web-prod"
    assert meta_file.is_file()

    # Symlinked deployments dir is rejected
    outside = tmp_path / "outside"
    outside.mkdir()
    sym_app = tmp_path / "sym_app"
    sym_app.mkdir()
    (sym_app / "deployments").symlink_to(outside)
    with pytest.raises(ValueError, match="Symlinks"):
        export_deployments_to_dir(good_client, sym_app)


def test_qa_pr8_sync_deployments_handles_corrupt_duplicates_symlinks_and_zip_isolation(
    tmp_path: Path,
) -> None:
    """sync_deployments_from_dir handles corrupt/non-UTF-8/duplicate/symlink files and ZIP excludes deployments/."""
    assert "deployments" not in VALID_ARCHIVE_ROOTS

    app_root = tmp_path / "app"
    dep_dir = app_root / "deployments"
    dep_dir.mkdir(parents=True)
    (app_root / "app.json").write_text(
        json.dumps({"displayName": "test_app"}), encoding="utf-8"
    )
    (dep_dir / "channel_meta.yaml").write_text(
        "profileId: brand_alpha\nchannel: DIGITAL_APP\n", encoding="utf-8"
    )
    (dep_dir / "web.yml").write_text(
        "deploymentId: web-live\n"
        "displayName: Web Live\n"
        "appVersion: v1\n"
        "channelProfile:\n"
        "  channelType: WEB_UI\n"
        "  disableDtmf: true\n"
        "  disableBargeInControl: true\n"
        "  noiseSuppressionLevel: high\n"
        "  personaProperty:\n"
        "    persona: CONCISE\n"
        "  webWidgetConfig:\n"
        "    modality: CHAT_ONLY\n"
        "    theme: DARK\n"
        "    webWidgetTitle: Support\n",
        encoding="utf-8",
    )

    # Confirm package_app_archive NEVER packs deployments/ into ImportApp ZIP
    out_zip = tmp_path / "app.zip"
    package_app_archive(app_root, out_zip, wrapper_dir="agent")
    with zipfile.ZipFile(out_zip) as zf:
        assert not any("deployments/" in name for name in zf.namelist())

    # Valid .yml + build metadata skip
    mock_client = MagicMock()
    mock_client.app_name = "projects/p/locations/us/apps/a"
    mock_client.list_deployments.return_value = []
    summary = sync_deployments_from_dir(mock_client, app_root)
    assert summary == {
        "created": ["web-live"],
        "updated": [],
        "skipped": ["channel_meta.yaml"],
    }
    mock_client.create_deployment.assert_called_once_with(
        deployment_id="web-live",
        display_name="Web Live",
        app_version="projects/p/locations/us/apps/a/versions/v1",
        channel_type="WEB_UI",
        modality="CHAT_ONLY",
        theme="DARK",
        web_widget_title="Support",
        disable_dtmf=True,
        disable_barge_in_control=True,
        persona_property="CONCISE",
        noise_suppression_level="high",
    )

    # Duplicate deploymentId across .yaml and .yml rejected
    (dep_dir / "dup.yaml").write_text(
        "deploymentId: web-live\ndisplayName: Dup\nappVersion: v2\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Duplicate deploymentId"):
        sync_deployments_from_dir(mock_client, app_root)
    (dep_dir / "dup.yaml").unlink()

    # Corrupt YAML, non-mapping YAML, and non-UTF-8 file fail cleanly with ValueError
    bad_file = dep_dir / "bad.yaml"
    for payload, err_pat in (
        (b":\n  - [unclosed", r"Invalid YAML"),
        (b"- list_root\n", r"Expected a YAML mapping"),
        (b"\xff\xfe\x00", r"Failed to read deployment file"),
    ):
        bad_file.write_bytes(payload)
        with pytest.raises(ValueError, match=err_pat):
            sync_deployments_from_dir(mock_client, app_root)
    bad_file.unlink()

    # Symlinked YAML file rejected
    outside_yml = tmp_path / "outside.yaml"
    outside_yml.write_text(
        "deploymentId: ext\ndisplayName: E\nappVersion: v1\n"
    )
    (dep_dir / "link.yaml").symlink_to(outside_yml)
    with pytest.raises(ValueError, match="Symlinks"):
        sync_deployments_from_dir(mock_client, app_root)


def test_qa_pr8_003_deployment_sync_contact_center_integration_roundtrip(
    tmp_path: Path,
) -> None:
    """Repro QA-PR8-003 (HIGH): Exported CONTACT_CENTER_INTEGRATION crashes Deployments on push sync."""
    app_root = tmp_path / "app"
    app_root.mkdir()
    dep_proto = types.Deployment(
        name="projects/p/locations/us/apps/a/deployments/cc-integ",
        display_name="CC Integration",
        app_version="projects/p/locations/us/apps/a/versions/v1",
        channel_profile=types.ChannelProfile(
            channel_type=types.ChannelProfile.ChannelType.CONTACT_CENTER_INTEGRATION
        ),
    )
    export_client = MagicMock()
    export_client.list_deployments.return_value = [dep_proto]
    export_deployments_to_dir(export_client, app_root)

    real_client, gapic = _make_real_deployments_client()
    summary = sync_deployments_from_dir(real_client, app_root)
    assert summary["created"] == ["cc-integ"]
    assert gapic.create_deployment.call_count == 1


def test_qa_pr8_004_deployment_sync_prevalidates_enums_before_mutating(
    tmp_path: Path,
) -> None:
    """Repro QA-PR8-004 (HIGH): Invalid enum in 2nd YAML mutates 1st deployment before failing with KeyError."""
    app_root = tmp_path / "app"
    dep_dir = app_root / "deployments"
    dep_dir.mkdir(parents=True)
    (dep_dir / "01-good.yaml").write_text(
        "deploymentId: good-dep\n"
        "displayName: Good\n"
        "appVersion: v1\n"
        "channelProfile:\n"
        "  channelType: WEB_UI\n",
        encoding="utf-8",
    )
    (dep_dir / "02-bad.yaml").write_text(
        "deploymentId: bad-dep\n"
        "displayName: Bad\n"
        "appVersion: v1\n"
        "channelProfile:\n"
        "  channelType: BOGUS_CHANNEL\n",
        encoding="utf-8",
    )

    real_client, gapic = _make_real_deployments_client()
    with pytest.raises(ValueError, match=r"channelType|BOGUS_CHANNEL"):
        sync_deployments_from_dir(real_client, app_root)
    assert gapic.create_deployment.call_count == 0


def test_qa_pr8_cli_app_push_and_pull_sync_deployments_error_gating(
    tmp_path: Path,
) -> None:
    """_app_push --sync-deployments must NOT sync deployments when import_app fails or returns warnings."""
    app_root = tmp_path / "app"
    (app_root / "deployments").mkdir(parents=True)
    (app_root / "app.json").write_text(
        json.dumps({"displayName": "test_app"}), encoding="utf-8"
    )
    (app_root / "deployments" / "prod-web.yaml").write_text(
        "deploymentId: prod-web\ndisplayName: Prod Web\nappVersion: v1\n",
        encoding="utf-8",
    )
    args = argparse.Namespace(
        overwrite=False,
        create_version=False,
        sync_deployments=True,
        native_python_env=False,
    )

    # 1. import_app returns server warnings -> sys.exit(1), Deployments NOT invoked
    apps_warn = MagicMock()
    apps_warn.creds = MagicMock()
    lro = MagicMock()
    resp = MagicMock()
    resp.name = "projects/p/locations/us/apps/a"
    resp.warnings = ["Invalid resource"]
    lro.result.return_value = resp
    apps_warn.import_app.return_value = lro

    with (
        patch("cxas_scrapi.core.deployments.Deployments") as mock_deps,
        pytest.raises(SystemExit) as exc_info,
    ):
        _app_push(
            app_dir=str(app_root),
            apps_client=apps_warn,
            target_app_name="projects/p/locations/us/apps/a",
            args=args,
            _skip_pre_push_gate=True,
        )
    assert exc_info.value.code == 1
    assert not mock_deps.called

    # 2. import_app raises RuntimeError -> sys.exit(1), Deployments NOT invoked
    apps_err = MagicMock()
    apps_err.creds = MagicMock()
    apps_err.import_app.side_effect = RuntimeError("API 500")
    with (
        patch("cxas_scrapi.core.deployments.Deployments") as mock_deps,
        pytest.raises(SystemExit) as exc_info,
    ):
        _app_push(
            app_dir=str(app_root),
            apps_client=apps_err,
            target_app_name="projects/p/locations/us/apps/a",
            args=args,
            _skip_pre_push_gate=True,
        )
    assert exc_info.value.code == 1
    assert not mock_deps.called

    # 3. _app_pull --sync-deployments exports deployments on success
    pull_out = tmp_path / "pulled"
    zip_buf = tmp_path / "exported.zip"
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("agent/app.json", json.dumps({"displayName": "pulled"}))
    apps_pull = MagicMock()
    apps_pull.creds = MagicMock()
    pull_lro = MagicMock()
    pull_resp = MagicMock()
    pull_resp.app_content = zip_buf.read_bytes()
    pull_lro.result.return_value = pull_resp
    apps_pull.export_app.return_value = pull_lro
    pull_args = argparse.Namespace(
        sync_deployments=True, format="JSON", unbundle_python=False
    )
    with patch("cxas_scrapi.core.deployments.Deployments") as mock_deps_cls:
        mock_deps_inst = mock_deps_cls.return_value
        mock_deps_inst.list_deployments.return_value = [
            {
                "name": "projects/p/locations/us/apps/a/deployments/live-web",
                "displayName": "Live Web",
                "appVersion": "v1",
            }
        ]
        _app_pull(
            apps_client=apps_pull,
            app_name="projects/p/locations/us/apps/a",
            target_dir=str(pull_out),
            args=pull_args,
        )
    assert (pull_out / "agent" / "deployments" / "live-web.yaml").is_file() or (
        pull_out / "deployments" / "live-web.yaml"
    ).is_file()


# ── 3. cxas init --template multibrand & Full Real CLI E2E ──────────────


def test_qa_pr8_init_multibrand_refuses_symlinks_and_existing_core_no_input(
    tmp_path: Path,
) -> None:
    """cxas init --template multibrand rejects symlinked target/core and existing core/ with --no-input."""
    # Symlinked --target-dir refused
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    sym_target = tmp_path / "sym_target"
    sym_target.symlink_to(real_dir)
    p_sym = _run_cxas(
        "init",
        "--template",
        "multibrand",
        "--target-dir",
        str(sym_target),
        "--no-input",
    )
    assert p_sym.returncode != 0
    assert list(real_dir.iterdir()) == []

    # Existing core/ with --no-input refuses without clobbering core/
    existing = tmp_path / "existing_core"
    (existing / "core").mkdir(parents=True)
    sentinel = existing / "core" / "sentinel.txt"
    sentinel.write_text("KEEP", encoding="utf-8")
    p_exists = _run_cxas(
        "init",
        "--template",
        "multibrand",
        "--target-dir",
        str(existing),
        "--no-input",
    )
    assert p_exists.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "KEEP"


def test_qa_pr8_005_init_multibrand_refusal_is_atomic_zero_partial_writes(
    tmp_path: Path,
) -> None:
    """Repro QA-PR8-005 (MEDIUM): init --template multibrand partially writes README.md & core/ before refusing on manifests/."""
    # Case 1: manifests/ already exists with --no-input -> must NOT write README.md or core/
    proj = tmp_path / "existing_manifests"
    (proj / "manifests").mkdir(parents=True)
    (proj / "manifests" / "keep.yaml").write_text("k: v\n", encoding="utf-8")
    p_refuse = _run_cxas(
        "init",
        "--template",
        "multibrand",
        "--target-dir",
        str(proj),
        "--no-input",
    )
    assert p_refuse.returncode != 0
    assert sorted(p.name for p in proj.iterdir()) == ["manifests"]

    # Case 2: manifests is a symlink (even with --force) -> must NOT write README.md or core/
    outside = tmp_path / "outside"
    outside.mkdir()
    sym_proj = tmp_path / "sym_manifests"
    sym_proj.mkdir()
    (sym_proj / "manifests").symlink_to(outside)
    p_sym = _run_cxas(
        "init",
        "--template",
        "multibrand",
        "--target-dir",
        str(sym_proj),
        "--force",
    )
    assert p_sym.returncode != 0
    assert sorted(p.name for p in sym_proj.iterdir()) == ["manifests"]


def test_qa_pr8_full_real_cli_e2e_init_build_lint_drift_backport_brand_and_core(
    tmp_path: Path,
) -> None:
    """Real CLI E2E: init multibrand -> build --all --write-locks -> lint (0 errors) -> drift -> backport brand -> core."""
    ws = tmp_path / "starter"
    p_init = _run_cxas(
        "init",
        "--template",
        "multibrand",
        "--target-dir",
        str(ws),
        "--no-input",
    )
    assert p_init.returncode == 0, p_init.stderr

    p_build = _run_cxas(
        "build",
        "--core",
        str(ws / "core"),
        "--manifests",
        str(ws / "manifests"),
        "--env",
        "prod",
        "--all",
        "--out",
        str(ws / "dist"),
        "--write-locks",
    )
    assert p_build.returncode == 0, p_build.stderr

    built_bundles = sorted(
        d
        for d in (ws / "dist").glob("*/*")
        if d.is_dir() and (d / "app.json").is_file()
    )
    assert len(built_bundles) == 4
    for bundle in built_bundles:
        p_lint = _run_cxas("lint", "--app-dir", str(bundle))
        assert p_lint.returncode == 0, (
            f"lint failed on {bundle}:\n{p_lint.stdout}\n{p_lint.stderr}"
        )

    live = ws / "live_alpha_digital"
    shutil.copytree(ws / "dist" / "brand_alpha" / "digital_app", live)

    p_drift = _run_cxas(
        "drift",
        "--app",
        "brand_alpha/digital_app",
        "--dist",
        str(ws / "dist"),
        "--live",
        str(live),
        "--lockfile",
        str(ws / "dist" / "brand_alpha" / "digital_app.lock.json"),
    )
    assert p_drift.returncode == 0, p_drift.stdout + p_drift.stderr

    inst = live / "agents" / "root_agent" / "instruction.txt"
    inst.write_text(
        inst.read_text(encoding="utf-8")
        + "\nAlways confirm before finalizing.\n",
        encoding="utf-8",
    )

    p_brand = _run_cxas(
        "backport",
        "--core",
        str(ws / "core"),
        "--manifests",
        str(ws / "manifests"),
        "--live",
        str(live),
        "--dist",
        str(ws / "dist"),
        "--brand",
        "brand_alpha",
        "--channel",
        "DIGITAL_APP",
        "--env",
        "prod",
        "--target",
        "brand",
    )
    assert p_brand.returncode == 0, p_brand.stdout + p_brand.stderr
    assert (
        ws / "manifests" / "profiles" / "brand_alpha" / "overrides.yaml"
    ).is_file()

    p_core = _run_cxas(
        "backport",
        "--core",
        str(ws / "core"),
        "--manifests",
        str(ws / "manifests"),
        "--live",
        str(live),
        "--dist",
        str(ws / "dist"),
        "--brand",
        "brand_alpha",
        "--channel",
        "DIGITAL_APP",
        "--env",
        "prod",
        "--target",
        "core",
    )
    assert p_core.returncode == 0, p_core.stdout + p_core.stderr
    assert not (
        ws / "manifests" / "profiles" / "brand_alpha" / "overrides.yaml"
    ).exists()
    assert "Always confirm before finalizing." in (
        ws / "core" / "agents" / "root_agent" / "instruction.txt"
    ).read_text(encoding="utf-8")
