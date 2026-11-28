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

"""Tests for `cxas init --template multibrand` and full offline E2E pipeline."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cxas_scrapi.build import (
    backport_live_to_source,
    build_all,
    check_drift,
    load_manifests,
)
from cxas_scrapi.cli import app as cli_app
from cxas_scrapi.utils.linter import (
    Discovery,
    LintConfig,
    LintReport,
    build_context,
    build_registry,
    run_rules,
)


def _lint_bundle(bundle_dir: Path) -> LintReport:
    registry = build_registry()
    config = LintConfig.load(bundle_dir)
    discovery = Discovery(
        bundle_dir / config.app_dir, bundle_dir / config.evals_dir
    )
    context = build_context(bundle_dir, config, discovery)
    report = LintReport()
    run_rules(registry, config, context, discovery, report)
    return report


def test_init_default_template_preserves_skills_behavior(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_prefix = tmp_path / "prefix"
    skills_root = fake_prefix / "share" / "cxas-scrapi" / "skills"
    skills_root.mkdir(parents=True)
    (skills_root / "AGENTS.md").write_text("# Skills\n", encoding="utf-8")
    monkeypatch.setattr(sys, "prefix", str(fake_prefix))

    target = tmp_path / "default_proj"
    target.mkdir()
    cli_app.app_init(
        argparse.Namespace(
            target_dir=str(target),
            template="default",
            force=False,
            no_input=True,
        )
    )
    assert (target / "AGENTS.md").read_text("utf-8") == "# Skills\n"
    assert not (target / "manifests").exists()


def test_init_multibrand_scaffolds_overwrite_guards_and_cli(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    env = {
        **os.environ,
        "PYTHONPATH": str(repo_root / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    proj = tmp_path / "starter_proj"

    # 1. Scaffold via CLI --template multibrand
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "cxas_scrapi.cli.main",
            "init",
            "--template",
            "multibrand",
            "--target-dir",
            str(proj),
            "--no-input",
        ],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (proj / "README.md").is_file()
    assert (proj / "core" / "app.json").is_file()
    assert (proj / "core" / "global_instruction.txt").is_file()
    assert (
        proj / "manifests" / "profiles" / "brand_alpha" / "identity.yaml"
    ).is_file()
    assert (
        proj / "manifests" / "profiles" / "brand_beta" / "identity.yaml"
    ).is_file()
    assert (proj / "manifests" / "envs" / "dev.yaml").is_file()
    assert (proj / "manifests" / "envs" / "prod.yaml").is_file()
    assert (proj / "manifests" / "locales" / "content_blocks.yaml").is_file()

    # Validate manifests strictly (no extra/PR4-only fields).
    manifests = load_manifests(proj / "manifests")
    assert sorted(manifests.profiles) == ["brand_alpha", "brand_beta"]
    assert sorted(manifests.envs) == ["dev", "prod"]
    assert manifests.content_blocks is not None
    for b_id in manifests.profiles:
        missing = manifests.content_blocks.missing_tuples(
            b_id,
            manifests.policy(b_id).supported_countries,
            manifests.profile(b_id).languages,
        )
        assert missing == []

    # 2. Re-running with --no-input (without --force) fails cleanly without clobbering.
    (proj / "README.md").write_text("custom edits\n", encoding="utf-8")
    proc_conflict = subprocess.run(
        [
            sys.executable,
            "-m",
            "cxas_scrapi.cli.main",
            "init",
            "--template",
            "multibrand",
            "--target-dir",
            str(proj),
            "--no-input",
        ],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_conflict.returncode == 1
    assert "already exists" in proc_conflict.stderr
    assert (proj / "README.md").read_text("utf-8") == "custom edits\n"

    # 3. Re-running with --force overwrites cleanly.
    proc_force = subprocess.run(
        [
            sys.executable,
            "-m",
            "cxas_scrapi.cli.main",
            "init",
            "--template",
            "multibrand",
            "--target-dir",
            str(proj),
            "--force",
            "--no-input",
        ],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_force.returncode == 0, proc_force.stdout + proc_force.stderr
    assert "cxas init --template multibrand" in (proj / "README.md").read_text(
        "utf-8"
    )

    # 4. Symlink target-dir rejected cleanly.
    sym_target = tmp_path / "sym_target"
    sym_target.symlink_to(proj)
    with pytest.raises(SystemExit) as excinfo:
        cli_app.app_init(
            argparse.Namespace(
                target_dir=str(sym_target),
                template="multibrand",
                force=True,
                no_input=True,
            )
        )
    assert excinfo.value.code == 1


def test_init_multibrand_full_offline_e2e_pipeline(tmp_path: Path) -> None:
    """Full offline E2E: init -> build --all --write-locks -> lint -> drift -> backport brand -> backport core."""
    proj = tmp_path / "e2e_proj"
    cli_app.app_init(
        argparse.Namespace(
            target_dir=str(proj),
            template="multibrand",
            force=False,
            no_input=True,
        )
    )

    core = proj / "core"
    manifests = proj / "manifests"
    dist = proj / "dist"

    # 1. Build all brand x channel bundles with lockfiles.
    reports = build_all(core, manifests, dist, "dev", write_locks=True)
    assert len(reports) == 4
    for r in reports:
        assert r.lockfile_path is not None
        assert r.lockfile_path.is_file()
        # 2. Lint every built bundle -> 0 errors (and 0 warnings!).
        lint_rep = _lint_bundle(r.out_dir)
        assert lint_rep.errors == [], [
            (e.rule_id, e.file, e.message) for e in lint_rep.errors
        ]
        assert lint_rep.warnings == [], [
            (w.rule_id, w.file, w.message) for w in lint_rep.warnings
        ]

    # 3. Drift check on an unedited export is CLEAN.
    alpha_web = dist / "brand_alpha" / "digital_app"
    alpha_lock = dist / "brand_alpha" / "digital_app.lock.json"
    live = tmp_path / "live_alpha_web"
    shutil.copytree(alpha_web, live)
    shutil.rmtree(live / "deployments")

    clean_drift = check_drift(
        "brand_alpha/digital_app", alpha_web, live, lockfile_path=alpha_lock
    )
    assert clean_drift.is_clean

    # 4. Simulate a Console UI edit on live root_agent/instruction.txt -> DRIFT.
    live_in = live / "agents" / "root_agent" / "instruction.txt"
    live_in.write_text(
        live_in.read_text("utf-8").replace(
            "Greet the customer warmly",
            "Greet the Alpha Financial customer warmly",
        ),
        encoding="utf-8",
    )
    dirty_drift = check_drift(
        "brand_alpha/digital_app", alpha_web, live, lockfile_path=alpha_lock
    )
    assert not dirty_drift.is_clean
    assert dirty_drift.by_id["agents/root_agent"].status == "DRIFT"

    # 5. Backport to brand overrides.yaml (--target brand --scope channel).
    bp_brand = backport_live_to_source(
        core,
        manifests,
        live,
        dist_dir=dist,
        brand_id="brand_alpha",
        channel="DIGITAL_APP",
        environment="dev",
        target="brand",
        scope="channel",
    )
    assert bp_brand.lint_errors == 0
    assert (manifests / "profiles" / "brand_alpha" / "overrides.yaml").is_file()
    assert sorted(bp_brand.unchanged_sibling_bundles) == [
        "brand_alpha/voice_telephony",
        "brand_beta/digital_app",
        "brand_beta/voice_telephony",
    ]

    # 6. Promote the edit from brand overrides into shared core/ (--target core).
    bp_core = backport_live_to_source(
        core,
        manifests,
        live,
        dist_dir=dist,
        brand_id="brand_alpha",
        channel="DIGITAL_APP",
        environment="dev",
        target="core",
    )
    assert bp_core.lint_errors == 0
    assert not (
        manifests / "profiles" / "brand_alpha" / "overrides.yaml"
    ).exists()
    core_in_text = (
        core / "agents" / "root_agent" / "instruction.txt"
    ).read_text("utf-8")
    assert "Greet the @@profile.display_name@@ customer warmly" in core_in_text
    assert "{@startChannel_VOICE_TELEPHONY}" in core_in_text
    # Beta Direct automatically inherits the promoted core edit with its own brand name!
    beta_in_text = (
        dist
        / "brand_beta"
        / "digital_app"
        / "agents"
        / "root_agent"
        / "instruction.txt"
    ).read_text("utf-8")
    assert "Greet the Beta Direct customer warmly" in beta_in_text
