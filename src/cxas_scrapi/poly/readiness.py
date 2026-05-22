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

"""Launch-readiness reports for polymorphic SCRAPI projects.

The readiness report is a design-partner review artifact: it composes the
existing adapter validators, compiler, and diff report into one summary without
adding a second validation contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from cxas_scrapi.poly.diffing import build_diff_report
from cxas_scrapi.poly.engine import CompilationError, PolymorphismEngine
from cxas_scrapi.poly.models import AdapterCard, EvalReference
from cxas_scrapi.poly.validators import (
    validate_adapter_card,
    validate_all_adapters,
)

READINESS_SCHEMA_VERSION = "poly-readiness/v1"

_EVAL_SURFACES: Tuple[Tuple[str, str, str], ...] = (
    ("evaluations", "evaluations", "evaluations"),
    (
        "evaluation_expectations",
        "evaluationExpectations",
        "evaluationExpectations",
    ),
    ("evaluation_datasets", "evaluationDatasets", "evaluationDatasets"),
)


def build_readiness_report(
    engine: PolymorphismEngine, app_dir: str
) -> Dict[str, Any]:
    """Build a pre-launch readiness report for all adapter cards.

    The caller is expected to load the base project and adapter cards first, but
    this function defensively does so when needed.
    """
    root = Path(app_dir).resolve()
    if engine.base is None:
        engine.load_base_project()
    if not engine.adapter_cards and not engine.adapter_errors:
        engine.load_adapter_cards()

    duplicate_issues = _duplicate_channel_issues(engine, str(root))
    channels: List[Dict[str, Any]] = []

    for card, card_path in engine.adapter_cards:
        channel = card.metadata.channel
        issues = validate_adapter_card(card, str(root))
        issues.extend(
            issue
            for issue in duplicate_issues
            if f"'{channel}'" in issue.get("message", "")
        )
        errors = _count(issues, "error")
        compiled = None
        diff_summary: Optional[Dict[str, Any]] = None
        compile_issue: Optional[Dict[str, Any]] = None
        if errors == 0:
            try:
                compiled = engine.compile(card, card_path, validate=False)
                diff = build_diff_report(
                    engine=engine,
                    card=card,
                    card_path=card_path,
                    compiled=compiled,
                )
                diff_summary = diff["summary"]
            except CompilationError as exc:
                compile_issue = {
                    "rule_id": "COMPILE",
                    "severity": "error",
                    "message": str(exc),
                    "path": _rel(root, card_path),
                }
                issues.extend(exc.issues or [compile_issue])
            except Exception as exc:  # pragma: no cover - defensive boundary
                compile_issue = {
                    "rule_id": "COMPILE",
                    "severity": "error",
                    "message": str(exc),
                    "path": _rel(root, card_path),
                }
                issues.append(compile_issue)

        eval_coverage = _eval_coverage(root, card)
        coverage_warnings = _coverage_warnings(eval_coverage)
        status = _status(issues, coverage_warnings)
        channels.append(
            {
                "channel": channel,
                "display_name": card.metadata.display_name,
                "adapter_path": _rel(root, card_path),
                "status": status,
                "compiled": compiled is not None and compile_issue is None,
                "issues": issues,
                "coverage_warnings": coverage_warnings,
                "diff_summary": diff_summary,
                "eval_coverage": eval_coverage,
                "next_steps": _next_steps(
                    status=status,
                    issues=issues,
                    coverage_warnings=coverage_warnings,
                    app_dir=str(root),
                ),
            }
        )

    summary = _summary(engine.adapter_errors, channels)
    return {
        "schema_version": READINESS_SCHEMA_VERSION,
        "app_dir": str(root),
        "summary": summary,
        "adapter_errors": list(engine.adapter_errors),
        "channels": channels,
    }


def _duplicate_channel_issues(
    engine: PolymorphismEngine, app_dir: str
) -> List[Dict[str, Any]]:
    cards = [card for card, _path in engine.adapter_cards]
    return [
        issue
        for issue in validate_all_adapters(cards, app_dir)
        if issue.get("rule_id") == "AD007"
    ]


def _eval_coverage(root: Path, card: AdapterCard) -> Dict[str, Dict[str, Any]]:
    coverage: Dict[str, Dict[str, Any]] = {}
    for attr, yaml_key, directory in _EVAL_SURFACES:
        base_names = _item_names(root / directory)
        refs = getattr(card, attr)
        channel_names = _names_from_refs(root, refs)
        coverage[yaml_key] = {
            "base_count": len(base_names),
            "channel_count": len(channel_names),
            "channel_names": channel_names,
            "duplicate_names": sorted(set(base_names) & set(channel_names)),
        }
    return coverage


def _names_from_refs(root: Path, refs: Iterable[EvalReference]) -> List[str]:
    names: List[str] = []
    for ref in refs:
        names.extend(_item_names(root / ref.source_dir))
    return sorted(dict.fromkeys(names))


def _item_names(path: Path) -> List[str]:
    if not path.is_dir():
        return []
    names: List[str] = []
    for child in sorted(path.iterdir()):
        if child.is_dir():
            names.append(child.name)
        elif child.suffix in (".json", ".yaml", ".yml"):
            names.append(child.stem)
    return names


def _coverage_warnings(
    eval_coverage: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    warnings: List[Dict[str, Any]] = []
    for surface, coverage in eval_coverage.items():
        duplicates = coverage["duplicate_names"]
        if duplicates:
            warnings.append(
                {
                    "surface": surface,
                    "message": (
                        f"{surface} has duplicate evaluation names that "
                        "will shadow base items when compiled: "
                        f"{', '.join(duplicates)}"
                    ),
                    "duplicate_names": duplicates,
                }
            )
    return warnings


def _status(
    issues: List[Dict[str, Any]],
    coverage_warnings: List[Dict[str, Any]],
) -> str:
    if any(issue.get("severity") == "error" for issue in issues):
        return "blocked"
    if any(issue.get("severity") == "warning" for issue in issues):
        return "attention"
    if coverage_warnings:
        return "attention"
    return "ready"


def _next_steps(
    *,
    status: str,
    issues: List[Dict[str, Any]],
    coverage_warnings: List[Dict[str, Any]],
    app_dir: str,
) -> List[str]:
    if status == "ready":
        return [
            "Run cxas poly build, lint the compiled output, and run channel "
            "evals."
        ]

    steps: List[str] = []
    if any(issue.get("severity") == "error" for issue in issues):
        steps.append(
            f"Run cxas poly doctor --app-dir {app_dir} to inspect blocking "
            "AD issues."
        )
    if any(issue.get("rule_id") == "AD006" for issue in issues):
        steps.append(
            "Add channel-specific evaluations for this adapter or document why "
            "the adapter is non-behavioral."
        )
    if coverage_warnings:
        duplicate_names = sorted(
            {
                name
                for warning in coverage_warnings
                for name in warning["duplicate_names"]
            }
        )
        steps.append(
            "Rename duplicate evaluation names before build so channel evals "
            f"do not shadow base evals: {', '.join(duplicate_names)}."
        )
    if any(issue.get("severity") == "warning" for issue in issues):
        steps.append(
            "Review warning-level AD issues before treating this channel as "
            "launch-ready."
        )
    if not steps:
        steps.append(
            "Review attention items before building and running channel evals."
        )
    return steps


def _summary(
    adapter_errors: List[Dict[str, Any]],
    channels: List[Dict[str, Any]],
) -> Dict[str, Any]:
    errors = _count(adapter_errors, "error")
    warnings = _count(adapter_errors, "warning")
    coverage_warnings = 0
    for channel in channels:
        errors += _count(channel["issues"], "error")
        warnings += _count(channel["issues"], "warning")
        coverage_warnings += len(channel["coverage_warnings"])
    blocked = sum(1 for channel in channels if channel["status"] == "blocked")
    attention = sum(
        1 for channel in channels if channel["status"] == "attention"
    )
    ready = sum(1 for channel in channels if channel["status"] == "ready")
    warnings += coverage_warnings
    return {
        "channels": len(channels),
        "ready": ready,
        "attention": attention,
        "blocked": blocked,
        "errors": errors,
        "warnings": warnings,
        "coverage_warnings": coverage_warnings,
        "launch_ready": bool(channels) and errors == 0 and warnings == 0,
    }


def _count(issues: List[Dict[str, Any]], severity: str) -> int:
    return sum(1 for issue in issues if issue.get("severity") == severity)


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
