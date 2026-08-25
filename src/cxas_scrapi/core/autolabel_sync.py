"""Declarative YAML sync and diff engine for CCAI Insights rules."""

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

from __future__ import annotations

import difflib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from cxas_scrapi.core.insights import Insights

logger = logging.getLogger(__name__)


def validate_autolabel_rules_dict(data: dict[str, Any]) -> list[str]:
    """Validates autolabel rules YAML dictionary against schema requirements.

    Args:
        data: The dictionary structure loaded from YAML.

    Returns:
        A list of validation error strings. Returns empty list if valid.
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["Root content must be a mapping/dictionary."]

    rules = data.get("autolabeling_rules")
    if not isinstance(rules, list):
        return ["'autolabeling_rules' must be a list."]

    if not rules:
        return ["'autolabeling_rules' list is empty."]

    seen_ids: set[str] = set()
    for idx, rule in enumerate(rules):
        if not isinstance(rule, dict):
            errors.append(f"Rule at index {idx} must be a dictionary.")
            continue

        rule_id = (
            rule.get("rule_id")
            or rule.get("label_key")
            or rule.get("labelKey")
            or rule.get("display_name")
            or f"rule_{idx}"
        )

        if str(rule_id) in seen_ids:
            errors.append(f"Duplicate rule_id or label_key found: '{rule_id}'")
        seen_ids.add(str(rule_id))

        if not rule.get("label_key") and not rule.get("labelKey"):
            errors.append(f"Rule '{rule_id}' is missing required 'label_key'.")

        conditions = rule.get("conditions")
        if not isinstance(conditions, list) or not conditions:
            errors.append(
                f"Rule '{rule_id}' must contain a non-empty 'conditions' list."
            )
        else:
            has_fallback = False
            for c_idx, cond in enumerate(conditions):
                if not isinstance(cond, dict):
                    errors.append(
                        f"Rule '{rule_id}' condition #{c_idx} "
                        "must be a dictionary."
                    )
                    continue
                if "value" not in cond:
                    errors.append(
                        f"Rule '{rule_id}' condition #{c_idx} "
                        "is missing 'value'."
                    )
                if cond.get("condition", None) == "":
                    has_fallback = True

            if not has_fallback:
                logger.debug(
                    "Rule '%s' does not specify an explicit default "
                    "fallback condition (condition: '').",
                    rule_id,
                )

    return errors


def yaml_rule_to_api_payload(
    rule_dict: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Converts a declarative YAML rule dictionary to a CCAI API payload.

    Args:
        rule_dict: The dictionary representation of a single rule from YAML.

    Returns:
        A tuple of (rule_id, api_payload_dict).
    """
    rule_id = (
        rule_dict.get("rule_id")
        or (
            rule_dict.get("name", "").split("/")[-1]
            if rule_dict.get("name")
            else None
        )
        or rule_dict.get("label_key")
        or rule_dict.get("labelKey")
        or "rule"
    )

    display_name = (
        rule_dict.get("display_name")
        or rule_dict.get("displayName")
        or str(rule_id)
    )
    label_key = (
        rule_dict.get("label_key") or rule_dict.get("labelKey") or str(rule_id)
    )

    payload: dict[str, Any] = {
        "displayName": display_name,
        "labelKey": label_key,
        "active": rule_dict.get("active", True),
    }

    if "label_key_type" in rule_dict or "labelKeyType" in rule_dict:
        payload["labelKeyType"] = rule_dict.get(
            "label_key_type"
        ) or rule_dict.get("labelKeyType")

    raw_conditions = rule_dict.get("conditions", [])
    payload["conditions"] = [
        {
            "condition": str(c.get("condition", "")),
            "value": str(c.get("value", "")),
        }
        for c in raw_conditions
        if isinstance(c, dict)
    ]

    return str(rule_id), payload


def api_rule_to_yaml_rule(api_rule: dict[str, Any]) -> dict[str, Any]:
    """Converts an API response dictionary to a clean YAML dictionary.

    Args:
        api_rule: The dictionary response from the CCAI Insights API.

    Returns:
        A formatted YAML rule dictionary.
    """
    name = api_rule.get("name", "")
    rule_id = name.split("/")[-1] if name else api_rule.get("labelKey", "rule")

    yaml_rule: dict[str, Any] = {
        "rule_id": rule_id,
        "display_name": api_rule.get("displayName", rule_id),
        "label_key": api_rule.get("labelKey", rule_id),
        "label_key_type": api_rule.get("labelKeyType", "LABEL_KEY_TYPE_CUSTOM"),
        "active": api_rule.get("active", True),
        "conditions": [
            {
                "condition": c.get("condition", ""),
                "value": c.get("value", ""),
            }
            for c in api_rule.get("conditions", [])
        ],
    }
    return yaml_rule


def load_autolabel_rules_yaml(file_path: str | Path) -> dict[str, Any]:
    """Loads and validates an autolabel rules YAML file.

    Args:
        file_path: Path to the YAML file.

    Returns:
        The loaded dictionary.

    Raises:
        ValueError: If file is invalid or fails schema validation.
        FileNotFoundError: If file does not exist.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Autolabel rules file not found: {file_path}")

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(
            f"Invalid YAML content in {file_path}: Expected a mapping at root."
        )

    errors = validate_autolabel_rules_dict(data)
    if errors:
        raise ValueError(
            f"Schema validation errors in {file_path}:\n - "
            + "\n - ".join(errors)
        )

    return data


def dump_autolabel_rules_yaml(
    data: dict[str, Any], file_path: str | Path
) -> None:
    """Dumps an autolabel rules dictionary to a cleanly formatted YAML file.

    Args:
        data: The dictionary structure to serialize.
        file_path: Destination path for the YAML file.
    """
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            data,
            f,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
        )


def export_remote_rules_to_yaml_dict(
    remote_rules: list[dict[str, Any]],
    project_id: str,
    location: str,
) -> dict[str, Any]:
    """Packages remote API rules into the standard declarative YAML format.

    Args:
        remote_rules: List of rules returned from Insights client.
        project_id: GCP project ID.
        location: GCP location.

    Returns:
        The declarative dictionary.
    """
    rules_yaml = [api_rule_to_yaml_rule(r) for r in remote_rules]
    return {
        "version": "1.0",
        "project_id": project_id,
        "location": location,
        "autolabeling_rules": rules_yaml,
    }


def diff_autolabel_rules(
    local_data: dict[str, Any],
    remote_rules: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compares local YAML rules against active remote rules in GCP.

    Args:
        local_data: Parsed local YAML dictionary.
        remote_rules: List of active rules from CCAI Insights API.

    Returns:
        A diff dictionary with keys:
            - 'to_create': list of (rule_id, payload) to create
            - 'to_update': list of (rule_id, payload, mask, name) to update
            - 'to_delete': list of remote_name to delete
            - 'unchanged': list of rule_id that are already in sync
            - 'report': Human-readable formatted summary string
    """
    local_rules = local_data.get("autolabeling_rules", [])

    # Map remote rules by their terminal ID and labelKey
    remote_by_id: dict[str, dict[str, Any]] = {}
    for r in remote_rules:
        name = r.get("name", "")
        rule_id = name.split("/")[-1] if name else r.get("labelKey", "")
        if rule_id:
            remote_by_id[rule_id] = r

    to_create: list[tuple[str, dict[str, Any]]] = []
    to_update: list[tuple[str, dict[str, Any], list[str], str]] = []
    unchanged: list[str] = []
    seen_remote_ids: set[str] = set()

    for r in local_rules:
        rule_id, payload = yaml_rule_to_api_payload(r)
        if rule_id not in remote_by_id:
            to_create.append((rule_id, payload))
        else:
            seen_remote_ids.add(rule_id)
            remote = remote_by_id[rule_id]
            remote_name = remote.get("name", "")
            changed_fields: list[str] = []

            if payload.get("displayName") != remote.get("displayName"):
                changed_fields.append("displayName")
            if payload.get("labelKey") != remote.get("labelKey"):
                changed_fields.append("labelKey")
            if "labelKeyType" in payload and payload.get(
                "labelKeyType"
            ) != remote.get("labelKeyType"):
                changed_fields.append("labelKeyType")
            if payload.get("active") != remote.get("active", True):
                changed_fields.append("active")

            # Compare conditions list
            local_conds = payload.get("conditions", [])
            remote_conds = [
                {
                    "condition": c.get("condition", ""),
                    "value": c.get("value", ""),
                }
                for c in remote.get("conditions", [])
            ]
            if local_conds != remote_conds:
                changed_fields.append("conditions")

            if changed_fields:
                to_update.append(
                    (rule_id, payload, changed_fields, remote_name)
                )
            else:
                unchanged.append(rule_id)

    to_delete = [
        remote.get("name", "")
        for rid, remote in remote_by_id.items()
        if rid not in seen_remote_ids and remote.get("name")
    ]

    report = format_diff_report(
        to_create=to_create,
        to_update=to_update,
        to_delete=to_delete,
        unchanged=unchanged,
        remote_by_id=remote_by_id,
    )

    return {
        "to_create": to_create,
        "to_update": to_update,
        "to_delete": to_delete,
        "unchanged": unchanged,
        "report": report,
    }


def format_diff_report(
    to_create: list[tuple[str, dict[str, Any]]],
    to_update: list[tuple[str, dict[str, Any], list[str], str]],
    to_delete: list[str],
    unchanged: list[str],
    remote_by_id: dict[str, dict[str, Any]],
) -> str:
    """Formats a diff breakdown into a readable multi-line summary."""
    lines: list[str] = ["=== AutoLabeling Rules Diff ==="]

    if not to_create and not to_update and not to_delete:
        lines.append(
            f"All {len(unchanged)} rule(s) are in sync with remote project."
        )
        return "\n".join(lines)

    if to_create:
        lines.append(f"\n[+] To Create ({len(to_create)}):")
        for rid, payload in to_create:
            key = payload.get("labelKey")
            num_cond = len(payload.get("conditions", []))
            lines.append(f"  + {rid} (Label: {key}, Conditions: {num_cond})")

    if to_update:
        lines.append(f"\n[~] To Update ({len(to_update)}):")
        for rid, payload, fields, _ in to_update:
            lines.append(f"  ~ {rid} (Fields changed: {', '.join(fields)})")
            remote = remote_by_id.get(rid, {})
            if "conditions" in fields:
                local_json = json.dumps(
                    payload.get("conditions", []), indent=2
                ).splitlines()
                remote_json = json.dumps(
                    remote.get("conditions", []), indent=2
                ).splitlines()
                diff = list(
                    difflib.unified_diff(
                        remote_json,
                        local_json,
                        fromfile="remote",
                        tofile="local",
                        lineterm="",
                    )
                )
                for d in diff[:12]:
                    lines.append(f"      {d}")

    if to_delete:
        lines.append(f"\n[-] To Delete from remote ({len(to_delete)}):")
        for name in to_delete:
            lines.append(f"  - {name}")

    if unchanged:
        lines.append(
            f"\n[=] Unchanged ({len(unchanged)}): {', '.join(unchanged)}"
        )

    return "\n".join(lines)


def sync_autolabel_rules(
    client: Insights,
    file_path: str | Path,
    parent: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Synchronizes local YAML rules to GCP Contact Center Insights.

    Args:
        client: The initialized Insights client.
        file_path: Path to the local autolabel_rules.yaml file.
        parent: Optional parent override (e.g. projects/P/locations/L).
        force: If True, deletes remote rules missing from local YAML.
        dry_run: If True, calculates and prints diff without API calls.

    Returns:
        A dictionary summarizing executed actions:
            - 'created': list of created rule IDs
            - 'updated': list of updated rule IDs
            - 'deleted': list of deleted rule names
            - 'skipped_delete': list of remote rule names kept when force=False
    """
    local_data = load_autolabel_rules_yaml(file_path)
    target_parent = parent or client.parent

    remote_rules = client.list_autolabeling_rules(parent=target_parent)
    diff_res = diff_autolabel_rules(local_data, remote_rules)

    summary: dict[str, Any] = {
        "created": [],
        "updated": [],
        "deleted": [],
        "skipped_delete": [],
        "diff_report": diff_res["report"],
    }

    if dry_run:
        logger.info(
            "Dry run complete. No modifications applied.\n%s",
            diff_res["report"],
        )
        return summary

    # Execute Creations
    for rule_id, payload in diff_res["to_create"]:
        logger.info("Creating autolabeling rule '%s'...", rule_id)
        client.create_autolabeling_rule(
            auto_labeling_rule=payload,
            auto_labeling_rule_id=rule_id,
            parent=target_parent,
        )
        summary["created"].append(rule_id)

    # Execute Updates
    for rule_id, payload, update_mask, remote_name in diff_res["to_update"]:
        logger.info(
            "Updating autolabeling rule '%s' (fields: %s)...",
            rule_id,
            update_mask,
        )
        client.update_autolabeling_rule(
            name=remote_name,
            auto_labeling_rule=payload,
            update_mask=update_mask,
        )
        summary["updated"].append(rule_id)

    # Execute Deletions
    if diff_res["to_delete"]:
        if force:
            for remote_name in diff_res["to_delete"]:
                logger.info(
                    "Deleting remote autolabeling rule '%s' (--force)...",
                    remote_name,
                )
                client.delete_autolabeling_rule(remote_name)
                summary["deleted"].append(remote_name)
        else:
            logger.warning(
                "Remote autolabeling rules exist that are not in "
                "local YAML (%d rules). Use --force to delete remote rules.",
                len(diff_res["to_delete"]),
            )
            summary["skipped_delete"] = diff_res["to_delete"]

    return summary
