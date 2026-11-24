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

"""Unit tests for the G002 locale/content-block cross-check rule."""

import json
from pathlib import Path

from cxas_scrapi.utils.lint_rules.structure import (
    CrosscheckLocalesContentBlocks,
)
from cxas_scrapi.utils.linter import (
    Discovery,
    LintConfig,
    LintContext,
    LintReport,
    build_registry,
    run_rules,
)

_BLOCKS = "blocks:\n  brand_a:\n    US:\n      en: {greeting: Hello}\n"


def _context(app_root: Path) -> LintContext:
    return LintContext(
        project_root=app_root,
        app_dir=app_root,
        evals_dir=app_root / "evals",
        app_root=app_root,
    )


def _setup(tmp_path: Path, deployment: str, blocks: str = _BLOCKS) -> Path:
    locales = tmp_path / "manifests" / "locales"
    locales.mkdir(parents=True)
    (locales / "content_blocks.yaml").write_text(blocks)
    deps = tmp_path / "deployments"
    deps.mkdir()
    dep = deps / "web.yaml"
    dep.write_text(deployment)
    return dep


def _check(tmp_path: Path, dep: Path) -> list[str]:
    rule = CrosscheckLocalesContentBlocks()
    return [
        r.message for r in rule.check(dep, dep.read_text(), _context(tmp_path))
    ]


def test_g002_declared_tuple_passes(tmp_path: Path) -> None:
    dep = _setup(
        tmp_path,
        "brandId: brand_a\nsupportedCountries: ['US']\n"
        "supportedLanguages: ['en']\n",
    )
    assert _check(tmp_path, dep) == []


def test_g002_missing_language_flagged(tmp_path: Path) -> None:
    dep = _setup(
        tmp_path,
        "brandId: brand_a\nsupportedCountries: ['US']\n"
        "supportedLanguages: ['en', 'fr']\n",
    )
    messages = _check(tmp_path, dep)
    assert len(messages) == 1
    assert "lang=fr" in messages[0]


def test_g002_unknown_brand_flagged_per_tuple(tmp_path: Path) -> None:
    dep = _setup(
        tmp_path,
        "brandId: brand_b\nsupportedCountries: ['US', 'CA']\n"
        "supportedLanguages: ['en']\n",
    )
    assert len(_check(tmp_path, dep)) == 2


def test_g002_no_catalog_skips(tmp_path: Path) -> None:
    deps = tmp_path / "deployments"
    deps.mkdir()
    dep = deps / "web.yaml"
    dep.write_text("brandId: brand_a\nsupportedCountries: ['US']\n")
    assert _check(tmp_path, dep) == []


def test_g002_empty_or_malformed_files_do_not_crash(tmp_path: Path) -> None:
    dep = _setup(tmp_path, "", blocks="")
    assert _check(tmp_path, dep) == []
    catalog = tmp_path / "manifests" / "locales" / "content_blocks.yaml"
    catalog.write_text("blocks:\n  brand_a:\n    US: null\n")
    dep.write_text(
        "brandId: brand_a\nsupportedCountries: ['US']\n"
        "supportedLanguages: ['en']\n"
    )
    assert len(_check(tmp_path, dep)) == 1
    catalog.write_text(_BLOCKS)
    dep.write_text("- just\n- a list\n")
    assert _check(tmp_path, dep) == []


def test_g002_dispatched_for_deployment_yaml(tmp_path: Path) -> None:
    (tmp_path / "app.json").write_text(json.dumps({"displayName": "app"}))
    _setup(
        tmp_path,
        "brandId: brand_a\nsupportedCountries: ['US']\n"
        "supportedLanguages: ['de']\n",
    )
    report = LintReport()
    run_rules(
        build_registry(),
        LintConfig(),
        _context(tmp_path),
        Discovery(tmp_path, tmp_path / "evals"),
        report,
        specific_rules={"G002"},
    )
    assert [r.rule_id for r in report.results] == ["G002"]
