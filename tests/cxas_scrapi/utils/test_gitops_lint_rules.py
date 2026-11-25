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

"""Unit tests for opt-in GitOps lint rules (S014 and V105)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import yaml

from cxas_scrapi.utils.linter import (
    Discovery,
    LintConfig,
    LintReport,
    build_context,
    build_registry,
    run_rules,
)

if TYPE_CHECKING:
    from pathlib import Path


def _create_sample_app(
    root: Path,
    *,
    variables: list[dict] | None = None,
    sub_agents: tuple[str, ...] = ("billing_agent",),
) -> Path:
    app_dir = root / "src" / "agent"
    app_dir.mkdir(parents=True)
    app_data = {
        "displayName": "Support Bot",
        "rootAgent": "Root Agent",
        "variableDeclarations": variables or [],
    }
    (app_dir / "app.json").write_text(json.dumps(app_data), encoding="utf-8")

    root_dir = app_dir / "agents" / "root_agent"
    root_dir.mkdir(parents=True)
    (root_dir / "root_agent.json").write_text(
        json.dumps({"displayName": "Root Agent"}), encoding="utf-8"
    )
    (root_dir / "instruction.txt").write_text(
        "Route users.\n", encoding="utf-8"
    )

    for name in sub_agents:
        sub_dir = app_dir / "agents" / name
        sub_dir.mkdir(parents=True)
        (sub_dir / f"{name}.json").write_text(
            json.dumps({"displayName": name.replace("_", " ").title()}),
            encoding="utf-8",
        )
        (sub_dir / "instruction.txt").write_text(
            f"Handle {name}.\n", encoding="utf-8"
        )

    return app_dir


def _run_specific(root: Path, rule_ids: set[str]) -> LintReport:
    config = LintConfig.load(root)
    app_dir = root / config.app_dir
    evals_dir = root / config.evals_dir
    discovery = Discovery(app_dir=app_dir, evals_dir=evals_dir)
    context = build_context(root, config, discovery)
    registry = build_registry()
    report = LintReport()
    run_rules(
        registry,
        config,
        context,
        discovery,
        report,
        specific_rules=rule_ids,
    )
    return report


def test_s014_silent_by_default(tmp_path: Path) -> None:
    """S014 emits zero findings when require_agent_contracts is unset/False."""
    _create_sample_app(tmp_path, sub_agents=("billing_agent", "orders_agent"))
    (tmp_path / "cxaslint.yaml").write_text(
        "app_dir: src/agent\n", encoding="utf-8"
    )

    report = _run_specific(tmp_path, {"S014"})
    assert report.results == []


def test_s014_flags_missing_subagent_contracts_and_passes_when_present(
    tmp_path: Path,
) -> None:
    """S014 flags non-root sub-agents missing contract docs and ignores root."""
    _create_sample_app(tmp_path, sub_agents=("billing_agent", "orders_agent"))
    (tmp_path / "cxaslint.yaml").write_text(
        yaml.safe_dump(
            {
                "app_dir": "src/agent",
                "require_agent_contracts": True,
                "contracts_dir": "docs/contracts",
            }
        ),
        encoding="utf-8",
    )

    # Only billing_agent has a contract; root_agent does not need one
    contracts_dir = tmp_path / "docs" / "contracts"
    contracts_dir.mkdir(parents=True)
    (contracts_dir / "billing_agent.md").write_text(
        "# Billing\n", encoding="utf-8"
    )

    report = _run_specific(tmp_path, {"S014"})
    assert len(report.results) == 1
    assert report.results[0].rule_id == "S014"
    assert "orders_agent" in report.results[0].message
    assert "root_agent" not in report.results[0].message

    # Adding orders_agent.md clears all findings
    (contracts_dir / "orders_agent.md").write_text(
        "# Orders\n", encoding="utf-8"
    )
    report_after = _run_specific(tmp_path, {"S014"})
    assert report_after.results == []


def test_v105_silent_by_default(tmp_path: Path) -> None:
    """V105 emits zero findings when allowed_variable_prefixes is unset/empty."""
    _create_sample_app(
        tmp_path,
        variables=[
            {"name": "anything_goes", "schema": {"type": "STRING"}},
            {"name": "legacyVar", "schema": {"type": "STRING"}},
        ],
    )
    (tmp_path / "cxaslint.yaml").write_text(
        "app_dir: src/agent\n", encoding="utf-8"
    )

    report = _run_specific(tmp_path, {"V105"})
    assert report.results == []


def test_v105_enforces_allowed_variable_prefixes(tmp_path: Path) -> None:
    """V105 flags declared variables that don't start with an allowed prefix."""
    _create_sample_app(
        tmp_path,
        variables=[
            {"name": "user_id", "schema": {"type": "STRING"}},
            {"name": "session_locale", "schema": {"type": "STRING"}},
            {"name": "bad_var", "schema": {"type": "STRING"}},
        ],
    )
    (tmp_path / "cxaslint.yaml").write_text(
        yaml.safe_dump(
            {
                "app_dir": "src/agent",
                "allowed_variable_prefixes": ["user_", "session_"],
            }
        ),
        encoding="utf-8",
    )

    report = _run_specific(tmp_path, {"V105"})
    assert len(report.results) == 1
    assert report.results[0].rule_id == "V105"
    assert "bad_var" in report.results[0].message

    # Fixing app.json clears all V105 findings
    app_json = tmp_path / "src" / "agent" / "app.json"
    data = json.loads(app_json.read_text(encoding="utf-8"))
    data["variableDeclarations"] = [
        {"name": "user_id", "schema": {"type": "STRING"}},
        {"name": "session_locale", "schema": {"type": "STRING"}},
    ]
    app_json.write_text(json.dumps(data), encoding="utf-8")

    report_after = _run_specific(tmp_path, {"V105"})
    assert report_after.results == []
