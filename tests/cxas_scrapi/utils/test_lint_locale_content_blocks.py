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

import pytest
import yaml

from cxas_scrapi.utils.lint_rules.structure import (
    CrosscheckLocalesContentBlocks,
    _LocaleYamlLoader,
)
from cxas_scrapi.utils.linter import (
    Discovery,
    LintConfig,
    LintContext,
    LintReport,
    build_context,
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


def test_g002_nested_app_layout_discovers_project_root_deployments(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "cxas_app"
    app_dir.mkdir()
    (app_dir / "app.json").write_text(json.dumps({"displayName": "nested"}))
    _setup(
        tmp_path,
        "brandId: brand_a\nsupportedCountries: [US]\nsupportedLanguages: [es]\n",
    )
    cfg = LintConfig(app_dir="cxas_app")
    disc = Discovery(app_dir, tmp_path / "evals")
    ctx = build_context(tmp_path, cfg, disc)
    report = LintReport()
    run_rules(build_registry(), cfg, ctx, disc, report, specific_rules={"G002"})
    assert [r.rule_id for r in report.results] == ["G002"]
    assert "lang=es" in report.results[0].message


def test_g002_snake_case_keys_and_norway_unquoted_codes(
    tmp_path: Path,
) -> None:
    dep = _setup(
        tmp_path,
        "brand_id: brand_a\nsupported_countries: [NO]\n"
        "supported_languages: [no, nb]\n",
        blocks="blocks:\n  brand_a:\n    NO:\n      no: {greeting: Hei}\n",
    )
    messages = _check(tmp_path, dep)
    assert len(messages) == 1
    assert "country=NO" in messages[0]
    assert "lang=nb" in messages[0]


def test_locale_yaml_loader_safety_and_malformed_yaml(tmp_path: Path) -> None:
    # Keeps unquoted NO/no as strings while rejecting unsafe Python tags
    loaded = yaml.load("country: NO\nlang: no\n", Loader=_LocaleYamlLoader)
    assert loaded == {"country": "NO", "lang": "no"}
    with pytest.raises(yaml.YAMLError):
        yaml.load(
            "!!python/object/apply:os.system ['echo hi']",
            Loader=_LocaleYamlLoader,
        )

    # Malformed YAML syntax in either file must not crash G002
    dep = _setup(tmp_path, "brandId: [unclosed", blocks=_BLOCKS)
    assert _check(tmp_path, dep) == []
    dep.write_text("brandId: brand_a\nsupportedCountries: [US]\n")
    (tmp_path / "manifests" / "locales" / "content_blocks.yaml").write_text(
        "blocks: [unclosed"
    )
    assert _check(tmp_path, dep) == []
