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

"""``redaction_expectations`` support for golden evals.

Golden evals run on the platform, whose evaluation conversations are not
subject to logging redaction. Goldens that declare
``redaction_expectations`` are therefore checked by replaying their
evaluation conversations through the app's DLP templates.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import yaml

from cxas_scrapi.redaction.policy import (
    RedactionExpectations,
    merge_redaction_expectations,
)

logger = logging.getLogger(__name__)


def golden_redaction_expectations(yaml_path: str) -> dict[str, dict[str, Any]]:
    """Maps golden display names to their merged redaction expectations.

    Returns ``{display_name: {"expectations": <raw block>, "base_dir": ...}}``
    for conversations that opt in (directly or via
    ``common_redaction_expectations``).
    """
    try:
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        logger.warning("Could not read %s: %s", yaml_path, e)
        return {}
    if not isinstance(data, dict):
        return {}
    common = data.get("common_redaction_expectations")
    base_dir = os.path.dirname(os.path.abspath(yaml_path))
    out: dict[str, dict[str, Any]] = {}
    for conv in data.get("conversations") or []:
        if not isinstance(conv, dict) or "conversation" not in conv:
            continue
        merged = merge_redaction_expectations(
            common, conv.get("redaction_expectations")
        )
        if merged:
            out[str(conv["conversation"])] = {
                "expectations": merged,
                "base_dir": base_dir,
            }
    return out


def check_golden_results(
    app_name: str,
    results: list[dict[str, Any]],
    expectations_by_name: dict[str, dict[str, Any]],
    creds: Any = None,
    mode: str = "platform",
) -> None:
    """Replays opted-in golden results and annotates them in place.

    Adds ``result["redaction"]`` (the report as a dict) and fails the result
    when the policy thresholds are exceeded.
    """
    from cxas_scrapi.redaction.replay import RedactionReplay  # noqa: PLC0415

    replayers: dict[str, RedactionReplay] = {}
    for result in results:
        spec = expectations_by_name.get(result.get("name", ""))
        if not spec:
            continue
        try:
            expectations = RedactionExpectations.parse(spec["expectations"])
            if expectations is None:
                continue
            policy = expectations.resolve_policy(spec["base_dir"])
            names = result.get("conversations") or []
            if not names and result.get("session_id"):
                names = [f"{app_name}/conversations/{result['session_id']}"]
            if not names:
                raise ValueError("result has no evaluation conversations")
            key = policy.model_dump_json()
            replay = replayers.get(key)
            if replay is None:
                replay = RedactionReplay(
                    policy, app_name=app_name, mode=mode, creds=creds
                )
                replayers[key] = replay
            report = replay.replay_conversation_ids(
                names,
                require=expectations.require,
                dedupe=True,
                source=result["name"],
            )
            summary = report.to_dict()
        except Exception as e:
            logger.warning("Redaction replay failed for %s: %s", result, e)
            summary = {"passed": False, "error": str(e)}
        result["redaction"] = summary
        if not summary.get("passed", False):
            result["passed"] = False
        counts = summary.get("summary") or {}
        print(
            f"  Redaction {'ok' if summary.get('passed') else 'FAILED'}: "
            f"{result['name']}"
            + (
                f" (leaks {counts.get('leak', 0)}, partial "
                f"{counts.get('partial_leak', 0)}, over-redacted "
                f"{counts.get('over_redaction', 0)})"
                if counts
                else f" ({summary.get('error')})"
            )
        )
