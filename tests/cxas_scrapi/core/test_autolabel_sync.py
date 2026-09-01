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
    format_diff_report,
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


def test_validate_autolabel_rules_dict_invalid_root_and_list() -> None:
    # Not a dict
    assert "mapping" in validate_autolabel_rules_dict([])[0]
    assert "mapping" in validate_autolabel_rules_dict("invalid")[0]

    # Missing or non-list autolabeling_rules
    assert "must be a list" in validate_autolabel_rules_dict({})[0]
    assert (
        "must be a list"
        in validate_autolabel_rules_dict({"autolabeling_rules": "str"})[0]
    )

    # Empty rules list
    assert (
        "is empty"
        in validate_autolabel_rules_dict({"autolabeling_rules": []})[0]
    )


def test_validate_autolabel_rules_dict_invalid_rule_structures() -> None:
    # Non-dictionary rule item
    data_with_non_dict_rule = {"autolabeling_rules": ["not_a_dict"]}
    errors = validate_autolabel_rules_dict(data_with_non_dict_rule)
    assert any("must be a dictionary" in e for e in errors)

    # Missing label_key & missing conditions
    invalid = {
        "autolabeling_rules": [
            {"rule_id": "r1"},
            {"rule_id": "r1", "label_key": "k", "conditions": []},
            {"rule_id": "r2", "label_key": "k2", "conditions": "not_a_list"},
        ]
    }
    errors = validate_autolabel_rules_dict(invalid)
    assert any("missing required 'label_key'" in e for e in errors)
    assert any("Duplicate rule_id" in e for e in errors)
    assert any("non-empty 'conditions'" in e for e in errors)

    # Conditions with non-dict item and missing value
    invalid_conds = {
        "autolabeling_rules": [
            {
                "rule_id": "r3",
                "label_key": "k3",
                "conditions": [
                    "not_a_dict",
                    {"condition": "c1"},  # missing value
                ],
            }
        ]
    }
    errors_conds = validate_autolabel_rules_dict(invalid_conds)
    assert any("condition #0 must be a dictionary" in e for e in errors_conds)
    assert any("condition #1 is missing 'value'" in e for e in errors_conds)


def test_validate_autolabel_rules_dict_no_fallback_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    data_no_fallback = {
        "version": "1.0",
        "autolabeling_rules": [
            {
                "rule_id": "r_no_fb",
                "label_key": "k_fb",
                "conditions": [
                    {"condition": "conversation.turns > 5", "value": "'long'"}
                ],
            }
        ],
    }
    with caplog.at_level("DEBUG"):
        errors = validate_autolabel_rules_dict(data_no_fallback)
    assert errors == []
    assert "does not specify an explicit default fallback" in caplog.text


def test_yaml_rule_to_api_payload_full_and_fallback_fields() -> None:
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
    assert payload["labelKeyType"] == "LABEL_KEY_TYPE_CUSTOM"
    assert payload["active"] is True
    assert payload["conditions"] == [{"condition": "", "value": "'general'"}]

    # Fallback to name extraction and labelKey camelCase
    rule_dict_alt = {
        "name": "projects/p/locations/l/autoLabelingRules/extracted_id",
        "displayName": "Extracted Display",
        "labelKey": "extracted_key",
        "labelKeyType": "LABEL_KEY_TYPE_SYSTEM",
        "conditions": [{"condition": "c", "value": 123}],
    }
    rule_id_alt, payload_alt = yaml_rule_to_api_payload(rule_dict_alt)
    assert rule_id_alt == "extracted_id"
    assert payload_alt["displayName"] == "Extracted Display"
    assert payload_alt["labelKey"] == "extracted_key"
    assert payload_alt["labelKeyType"] == "LABEL_KEY_TYPE_SYSTEM"
    assert payload_alt["conditions"] == [{"condition": "c", "value": "123"}]

    # Minimal rule with only label_key
    min_rule = {"label_key": "only_key"}
    min_id, min_payload = yaml_rule_to_api_payload(min_rule)
    assert min_id == "only_key"
    assert min_payload["displayName"] == "only_key"
    assert min_payload["labelKey"] == "only_key"
    assert min_payload["active"] is True
    assert min_payload["conditions"] == []


def test_api_rule_to_yaml_rule_variations() -> None:
    api_resp = {
        "name": "projects/p/locations/l/autoLabelingRules/r1",
        "displayName": "Rule 1",
        "labelKey": "category",
        "labelKeyType": "LABEL_KEY_TYPE_CUSTOM",
        "active": False,
        "conditions": [{"condition": "", "value": "'general'"}],
    }
    yaml_res = api_rule_to_yaml_rule(api_resp)
    assert yaml_res["rule_id"] == "r1"
    assert yaml_res["display_name"] == "Rule 1"
    assert yaml_res["label_key"] == "category"
    assert yaml_res["label_key_type"] == "LABEL_KEY_TYPE_CUSTOM"
    assert yaml_res["active"] is False

    # API response without name (falling back to labelKey)
    api_resp_no_name = {
        "displayName": "No Name Rule",
        "labelKey": "fallback_key",
    }
    yaml_res_no_name = api_rule_to_yaml_rule(api_resp_no_name)
    assert yaml_res_no_name["rule_id"] == "fallback_key"
    assert yaml_res_no_name["conditions"] == []


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
    # Test directory creation when nested parent does not exist
    file_path = tmp_path / "nested" / "dir" / "autolabel_rules.yaml"
    dump_autolabel_rules_yaml(data, file_path)
    assert file_path.exists()

    loaded = load_autolabel_rules_yaml(file_path)
    assert loaded["project_id"] == "my-project"
    assert len(loaded["autolabeling_rules"]) == 1


def test_load_yaml_errors(tmp_path: Path) -> None:
    with pytest.raises(
        FileNotFoundError, match="Autolabel rules file not found"
    ):
        load_autolabel_rules_yaml(tmp_path / "non_existent.yaml")

    bad_non_dict = tmp_path / "bad_list.yaml"
    bad_non_dict.write_text("- item1\n- item2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Expected a mapping at root"):
        load_autolabel_rules_yaml(bad_non_dict)

    bad_schema = tmp_path / "bad_schema.yaml"
    bad_schema.write_text("invalid_key:\n  - item\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Schema validation errors"):
        load_autolabel_rules_yaml(bad_schema)


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


def test_diff_autolabel_rules_and_report() -> None:
    local_data = {
        "autolabeling_rules": [
            {
                "rule_id": "r1",
                "display_name": "Rule 1 Updated",
                "label_key": "cat_updated",
                "label_key_type": "LABEL_KEY_TYPE_SYSTEM",
                "active": False,
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
                "label_key_type": "LABEL_KEY_TYPE_CUSTOM",
                "active": True,
                "conditions": [{"condition": "", "value": "'same'"}],
            },
        ]
    }

    remote_rules = [
        {
            "name": "projects/p/locations/l/autoLabelingRules/r1",
            "displayName": "Rule 1 Old",
            "labelKey": "cat_old",
            "labelKeyType": "LABEL_KEY_TYPE_CUSTOM",
            "active": True,
            "conditions": [{"condition": "", "value": "'old'"}],
        },
        {
            "name": "projects/p/locations/l/autoLabelingRules/r3_same",
            "displayName": "Unchanged Rule",
            "labelKey": "k3",
            "labelKeyType": "LABEL_KEY_TYPE_CUSTOM",
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
    rule_id, payload, fields, remote_name = diff_res["to_update"][0]
    assert rule_id == "r1"
    assert payload["displayName"] == "Rule 1 Updated"
    assert "displayName" in fields
    assert "labelKey" in fields
    assert "labelKeyType" in fields
    assert "active" in fields
    assert "conditions" in fields
    assert remote_name == "projects/p/locations/l/autoLabelingRules/r1"

    # To delete
    assert diff_res["to_delete"] == [
        "projects/p/locations/l/autoLabelingRules/r4_orphan"
    ]

    # Unchanged
    assert diff_res["unchanged"] == ["r3_same"]
    assert "[+] To Create" in diff_res["report"]
    assert "[~] To Update" in diff_res["report"]
    assert "[-] To Delete" in diff_res["report"]
    assert "[=] Unchanged" in diff_res["report"]


def test_format_diff_report_all_in_sync() -> None:
    report = format_diff_report(
        to_create=[],
        to_update=[],
        to_delete=[],
        unchanged=["r1", "r2"],
        remote_by_id={},
    )
    assert "All 2 rule(s) are in sync" in report


def test_sync_autolabel_rules_workflows(tmp_path: Path) -> None:
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
    assert summary_dry["diff_report"] != ""

    # 2. Live run creating r1 with custom parent
    summary_live = sync_autolabel_rules(
        mock_client,
        file_path,
        parent="projects/other/locations/global",
        dry_run=False,
    )
    assert mock_client.create_autolabeling_rule.call_count == 1
    mock_client.create_autolabeling_rule.assert_called_with(
        auto_labeling_rule={
            "displayName": "Rule 1",
            "labelKey": "cat",
            "active": True,
            "conditions": [{"condition": "", "value": "'v'"}],
        },
        auto_labeling_rule_id="r1",
        parent="projects/other/locations/global",
    )
    assert summary_live["created"] == ["r1"]

    # 3. Live run with update and skip deletion (force=False)
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
    summary_skip_del = sync_autolabel_rules(
        mock_client, file_path, force=False, dry_run=False
    )
    assert mock_client.update_autolabeling_rule.call_count == 1
    assert mock_client.delete_autolabeling_rule.call_count == 0
    assert summary_skip_del["updated"] == ["r1"]
    assert summary_skip_del["deleted"] == []
    assert summary_skip_del["skipped_delete"] == [
        "projects/p/locations/l/autoLabelingRules/old_rule"
    ]

    # 4. Live run with force=True deletion
    summary_force_del = sync_autolabel_rules(
        mock_client, file_path, force=True, dry_run=False
    )
    assert mock_client.delete_autolabeling_rule.call_count == 1
    assert summary_force_del["deleted"] == [
        "projects/p/locations/l/autoLabelingRules/old_rule"
    ]
