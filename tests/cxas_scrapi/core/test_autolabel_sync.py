"""Unit tests for autolabel_sync module."""

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

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cxas_scrapi.core.autolabel_sync import (
    api_rule_to_yaml_rule,
    diff_autolabel_rules,
    dump_autolabel_rules_yaml,
    export_remote_rules_to_yaml_dict,
    load_autolabel_rules_yaml,
    sync_autolabel_rules,
    validate_autolabel_rules_dict,
    yaml_rule_to_api_payload,
)


def test_validate_autolabel_rules_dict_valid() -> None:
    valid_data = {
        "version": "1.0",
        "project_id": "p",
        "location": "l",
        "autolabeling_rules": [
            {
                "rule_id": "category",
                "label_key": "category",
                "conditions": [
                    {
                        "condition": "conversation.agent_id == 'vip'",
                        "value": "'vip'",
                    },
                    {"condition": "", "value": "'default'"},
                ],
            }
        ],
    }
    errors = validate_autolabel_rules_dict(valid_data)
    assert errors == []


def test_validate_autolabel_rules_dict_invalid() -> None:
    # Not a dict
    assert "mapping" in validate_autolabel_rules_dict([])[0]

    # Missing autolabeling_rules
    assert "list" in validate_autolabel_rules_dict({})[0]

    # Empty rules
    assert (
        "empty" in validate_autolabel_rules_dict({"autolabeling_rules": []})[0]
    )

    # Missing label_key & missing conditions
    invalid = {
        "autolabeling_rules": [
            {"rule_id": "r1"},
            {"rule_id": "r1", "label_key": "k", "conditions": []},
        ]
    }
    errors = validate_autolabel_rules_dict(invalid)
    assert any("missing required 'label_key'" in e for e in errors)
    assert any("Duplicate rule_id" in e for e in errors)
    assert any("non-empty 'conditions'" in e for e in errors)


def test_yaml_rule_to_api_payload_and_back() -> None:
    rule_dict = {
        "rule_id": "r1",
        "display_name": "Rule 1",
        "label_key": "category",
        "label_key_type": "LABEL_KEY_TYPE_CUSTOM",
        "active": True,
        "conditions": [{"condition": "", "value": "'general'"}],
    }
    rule_id, payload = yaml_rule_to_api_payload(rule_dict)
    assert rule_id == "r1"
    assert payload["displayName"] == "Rule 1"
    assert payload["labelKey"] == "category"
    assert payload["conditions"] == [{"condition": "", "value": "'general'"}]

    # API back to YAML
    api_resp = {
        "name": "projects/p/locations/l/autoLabelingRules/r1",
        "displayName": "Rule 1",
        "labelKey": "category",
        "labelKeyType": "LABEL_KEY_TYPE_CUSTOM",
        "active": True,
        "conditions": [{"condition": "", "value": "'general'"}],
    }
    yaml_res = api_rule_to_yaml_rule(api_resp)
    assert yaml_res["rule_id"] == "r1"
    assert yaml_res["display_name"] == "Rule 1"


def test_load_and_dump_yaml(tmp_path: Path) -> None:
    data = {
        "version": "1.0",
        "project_id": "my-project",
        "location": "us-central1",
        "autolabeling_rules": [
            {
                "rule_id": "escalation",
                "label_key": "escalation",
                "conditions": [{"condition": "", "value": "'low'"}],
            }
        ],
    }
    file_path = tmp_path / "autolabel_rules.yaml"
    dump_autolabel_rules_yaml(data, file_path)

    loaded = load_autolabel_rules_yaml(file_path)
    assert loaded["project_id"] == "my-project"
    assert len(loaded["autolabeling_rules"]) == 1


def test_load_yaml_errors(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_autolabel_rules_yaml(tmp_path / "non_existent.yaml")

    bad_file = tmp_path / "bad.yaml"
    bad_file.write_text("invalid_list:\n  - item\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Schema validation errors"):
        load_autolabel_rules_yaml(bad_file)


def test_export_remote_rules_to_yaml_dict() -> None:
    remote = [
        {
            "name": "projects/p/locations/l/autoLabelingRules/r1",
            "displayName": "Rule 1",
            "labelKey": "cat",
            "conditions": [],
        }
    ]
    data = export_remote_rules_to_yaml_dict(remote, "p", "l")
    assert data["version"] == "1.0"
    assert data["project_id"] == "p"
    assert len(data["autolabeling_rules"]) == 1
    assert data["autolabeling_rules"][0]["rule_id"] == "r1"


def test_diff_autolabel_rules() -> None:
    local_data = {
        "autolabeling_rules": [
            {
                "rule_id": "r1",
                "display_name": "Rule 1 Updated",
                "label_key": "cat",
                "active": True,
                "conditions": [{"condition": "", "value": "'new'"}],
            },
            {
                "rule_id": "r2_new",
                "display_name": "New Rule",
                "label_key": "new_key",
                "conditions": [{"condition": "", "value": "'v'"}],
            },
            {
                "rule_id": "r3_same",
                "display_name": "Unchanged Rule",
                "label_key": "k3",
                "conditions": [{"condition": "", "value": "'same'"}],
            },
        ]
    }

    remote_rules = [
        {
            "name": "projects/p/locations/l/autoLabelingRules/r1",
            "displayName": "Rule 1 Old",
            "labelKey": "cat",
            "active": True,
            "conditions": [{"condition": "", "value": "'old'"}],
        },
        {
            "name": "projects/p/locations/l/autoLabelingRules/r3_same",
            "displayName": "Unchanged Rule",
            "labelKey": "k3",
            "active": True,
            "conditions": [{"condition": "", "value": "'same'"}],
        },
        {
            "name": "projects/p/locations/l/autoLabelingRules/r4_orphan",
            "displayName": "Orphan Rule",
            "labelKey": "k4",
            "conditions": [],
        },
    ]

    diff_res = diff_autolabel_rules(local_data, remote_rules)

    # To create
    assert len(diff_res["to_create"]) == 1
    assert diff_res["to_create"][0][0] == "r2_new"

    # To update
    assert len(diff_res["to_update"]) == 1
    assert diff_res["to_update"][0][0] == "r1"
    assert "displayName" in diff_res["to_update"][0][2]
    assert "conditions" in diff_res["to_update"][0][2]

    # To delete
    assert diff_res["to_delete"] == [
        "projects/p/locations/l/autoLabelingRules/r4_orphan"
    ]

    # Unchanged
    assert diff_res["unchanged"] == ["r3_same"]
    assert "=== AutoLabeling Rules Diff ===" in diff_res["report"]


def test_sync_autolabel_rules(tmp_path: Path) -> None:
    data = {
        "version": "1.0",
        "project_id": "p",
        "location": "l",
        "autolabeling_rules": [
            {
                "rule_id": "r1",
                "display_name": "Rule 1",
                "label_key": "cat",
                "conditions": [{"condition": "", "value": "'v'"}],
            }
        ],
    }
    file_path = tmp_path / "autolabel_rules.yaml"
    dump_autolabel_rules_yaml(data, file_path)

    mock_client = MagicMock()
    mock_client.parent = "projects/p/locations/l"

    # 1. Dry run
    mock_client.list_autolabeling_rules.return_value = []
    summary_dry = sync_autolabel_rules(mock_client, file_path, dry_run=True)
    assert mock_client.create_autolabeling_rule.call_count == 0
    assert summary_dry["created"] == []

    # 2. Live run creating r1
    summary_live = sync_autolabel_rules(mock_client, file_path, dry_run=False)
    assert mock_client.create_autolabeling_rule.call_count == 1
    assert summary_live["created"] == ["r1"]

    # 3. Live run with update and delete (force=True)
    mock_client.list_autolabeling_rules.return_value = [
        {
            "name": "projects/p/locations/l/autoLabelingRules/r1",
            "displayName": "Old Name",
            "labelKey": "cat",
            "conditions": [{"condition": "", "value": "'v'"}],
        },
        {
            "name": "projects/p/locations/l/autoLabelingRules/old_rule",
            "displayName": "Old Rule",
            "labelKey": "old",
            "conditions": [],
        },
    ]
    summary_update = sync_autolabel_rules(
        mock_client, file_path, force=True, dry_run=False
    )
    assert mock_client.update_autolabeling_rule.call_count == 1
    assert mock_client.delete_autolabeling_rule.call_count == 1
    assert summary_update["updated"] == ["r1"]
    assert summary_update["deleted"] == [
        "projects/p/locations/l/autoLabelingRules/old_rule"
    ]
