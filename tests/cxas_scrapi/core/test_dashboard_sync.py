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

import typing
from unittest.mock import MagicMock

import pytest

from cxas_scrapi.core.dashboard_sync import (
    api_dashboard_to_yaml_dashboard,
    diff_dashboards,
    dump_dashboards_yaml,
    export_remote_dashboards_to_yaml_dict,
    load_dashboards_yaml,
    sync_dashboards,
    validate_dashboards_dict,
    yaml_dashboard_to_api_payload,
)


def test_validate_dashboards_dict_valid() -> None:
    """Test validation of valid dashboards dictionary."""
    valid_data = {
        "version": "1.0",
        "dashboards": [
            {
                "dashboard_id": "dash_1",
                "display_name": "Executive Dashboard",
                "description": "High level KPIs",
                "filter": "agent_id != ''",
                "date_range": {"relative": {"quantity": 7, "unit": "DAY"}},
                "root_container": {
                    "display_name": "Root",
                    "widgets": [
                        {
                            "container": {
                                "display_name": "Overview Tab",
                                "width": 12,
                                "height": 6,
                                "widgets": [
                                    {
                                        "chart": {
                                            "display_name": "Call Volume",
                                            "chart_visualization_type": "SCORE_CARD",
                                            "width": 4,
                                            "height": 3,
                                            "data_source": {
                                                "generative_insights": {
                                                    "sql_query": "SELECT COUNT(1) FROM c",
                                                    "chart_spec": {"mark": "text"},
                                                }
                                            },
                                        }
                                    },
                                    {
                                        "chart_reference": "projects/p/locations/l/dashboards/d1/charts/c1"
                                    },
                                    {
                                        "container": {
                                            "display_name": "Sub Group",
                                            "widgets": [],
                                        }
                                    },
                                ],
                            }
                        }
                    ],
                },
            }
        ],
    }
    errors = validate_dashboards_dict(valid_data)
    assert errors == []


def test_validate_dashboards_dict_single_dashboard() -> None:
    """Test validation of a single dashboard definition at root."""
    single_data = {
        "display_name": "Single Dashboard",
        "root_container": {
            "widgets": [
                {
                    "container": {
                        "display_name": "Tab 1",
                        "widgets": [],
                    }
                }
            ]
        },
    }
    errors = validate_dashboards_dict(single_data)
    assert errors == []


def test_validate_dashboards_dict_errors() -> None:
    """Test validation error cases."""
    # 1. Non-dict root
    assert "Root content must be a mapping" in validate_dashboards_dict([])[0]

    # 2. Missing dashboards and single dashboard
    assert "YAML must contain 'dashboards' list" in validate_dashboards_dict({})[0]

    # 3. Non-list dashboards
    assert "'dashboards' must be a list" in validate_dashboards_dict({"dashboards": "bad"})[0]

    # 4. Empty dashboards list
    assert "'dashboards' list is empty" in validate_dashboards_dict({"dashboards": []})[0]

    # 5. Non-dict item
    errors = validate_dashboards_dict({"dashboards": ["invalid"]})
    assert "Dashboard at index 0 must be a dictionary" in errors[0]

    # 6. Duplicate dashboard_id
    duplicate_data = {
        "dashboards": [
            {
                "dashboard_id": "same_id",
                "display_name": "Dash 1",
                "root_container": {
                    "widgets": [{"container": {"display_name": "Tab"}}]
                },
            },
            {
                "dashboard_id": "same_id",
                "display_name": "Dash 2",
                "root_container": {
                    "widgets": [{"container": {"display_name": "Tab"}}]
                },
            },
        ]
    }
    errors = validate_dashboards_dict(duplicate_data)
    assert any("Duplicate dashboard_id" in e for e in errors)

    # 7. Missing/invalid display_name
    invalid_dash = {
        "dashboards": [
            {
                "dashboard_id": "d1",
                "display_name": "x" * 105,
                "date_range": "not_a_dict",
                "root_container": {
                    "widgets": [
                        {"container": {"display_name": "x" * 105, "widgets": "not_a_list"}}
                    ]
                },
            },
            {
                "dashboard_id": "d2",
                "display_name": "",
                "root_container": {
                    "widgets": [
                        {
                            "container": {
                                "widgets": [
                                    {"chart": {"chart_visualization_type": "INVALID_TYPE"}}
                                ]
                            }
                        }
                    ]
                },
            },
            {
                "dashboard_id": "d3",
                "display_name": "Valid Name",
                "date_range": {"relative": {"unit": "INVALID_UNIT"}},
                "root_container": "not_a_dict",
            },
            {
                "dashboard_id": "d4",
                "display_name": "Valid Name",
                "date_range": {},
                "root_container": {
                    "widgets": [
                        {
                            "container": {
                                "widgets": [
                                    "not_a_dict",
                                    {"chart": {"data_source": "not_a_dict"}},
                                    {"chart_reference": ""},
                                    {},
                                ]
                            }
                        }
                    ]
                },
            },
            {
                "dashboard_id": "d5",
                "display_name": "Valid Name",
                "date_range": {"relative": "not_a_dict"},
                "root_container": {"widgets": []},
            },
            {
                "dashboard_id": "d6",
                "display_name": "Valid Name",
                "root_container": {
                    "widgets": [
                        {"chart": {"display_name": "Invalid Direct Root Chart"}}
                    ]
                },
            },
            {
                "dashboard_id": "d7",
                "display_name": "Valid Name",
            },
        ]
    }
    errors = validate_dashboards_dict(invalid_dash)
    assert any("must not exceed 100 characters" in e for e in errors)
    assert any("'date_range' must be a dictionary" in e for e in errors)
    assert any("The widgets in the root container must be of type Container" in e for e in errors)
    assert any("Root container must contain at least one tab" in e for e in errors)
    assert any("is missing required 'root_container'" in e for e in errors)
    assert any("Invalid chart_visualization_type" in e for e in errors)
    assert any("Invalid relative time unit" in e for e in errors)
    assert any("RootContainer: Container must be a dictionary" in e for e in errors)
    assert any("'chart_reference' must be a non-empty string" in e for e in errors)


def test_yaml_dashboard_to_api_payload() -> None:
    """Test converting YAML dashboard to API payload."""
    yaml_dict = {
        "dashboard_id": "agent_kpis",
        "display_name": "Agent KPIs",
        "description": "Overview of agent performance",
        "filter": "turn_count > 1",
        "date_range": {"relative": {"quantity": 14, "unit": "day"}},
        "root_container": {
            "display_name": "Root",
            "widgets": [
                {
                    "container": {
                        "display_name": "Performance",
                        "width": 12,
                        "height": 4,
                        "filter": "agent_id != ''",
                        "date_range": {"relative": {"quantity": 7, "unit": "day"}},
                        "widgets": [
                            {
                                "chart": {
                                    "display_name": "Avg Score",
                                    "chart_visualization_type": "score_card",
                                    "width": 4,
                                    "height": 2,
                                    "data_source": {
                                        "generative_insights": {
                                            "sql_query": "SELECT avg_score FROM t",
                                            "chart_spec": {"mark": "text"},
                                        }
                                    },
                                }
                            },
                            {
                                "chart": {
                                    "display_name": "Query Metrics Chart",
                                    "data_source": {
                                        "query_metrics": {"metric": "TOTAL_COUNT"}
                                    },
                                }
                            },
                            {
                                "chart_reference": "projects/p/locations/l/dashboards/d1/charts/c1",
                                "filter": "resolved = true",
                            },
                        ],
                    }
                }
            ],
        },
    }

    dash_id, payload = yaml_dashboard_to_api_payload(yaml_dict)
    assert dash_id == "agent_kpis"
    assert payload["displayName"] == "Agent KPIs"
    assert payload["description"] == "Overview of agent performance"
    assert payload["filter"] == "turn_count > 1"
    assert payload["dateRangeConfig"]["relativeDateRange"] == {
        "quantity": 14,
        "unit": "DAY",
    }
    assert "rootContainer" in payload
    tabs = payload["rootContainer"]["widgets"]
    assert len(tabs) == 1
    assert tabs[0]["container"]["displayName"] == "Performance"
    widgets = tabs[0]["container"]["widgets"]
    assert len(widgets) == 3
    assert widgets[0]["chart"]["chartVisualizationType"] == "SCORE_CARD"
    assert widgets[0]["chart"]["dataSource"]["generativeInsights"]["sqlQuery"] == "SELECT avg_score FROM t"
    assert widgets[1]["chart"]["dataSource"]["queryMetrics"] == {"metric": "TOTAL_COUNT"}
    assert widgets[2]["chartReference"] == "projects/p/locations/l/dashboards/d1/charts/c1"
    assert widgets[2]["filter"] == "resolved = true"


def test_api_dashboard_to_yaml_dashboard() -> None:
    """Test converting API response dictionary to YAML dictionary."""
    api_dash = {
        "name": "projects/proj/locations/loc/dashboards/my_dash",
        "displayName": "My Dashboard",
        "description": "A test dashboard",
        "filter": "agent_id != ''",
        "dateRangeConfig": {"relativeDateRange": {"quantity": 7, "unit": "DAY"}},
        "rootContainer": {"displayName": "Root", "widgets": []},
        "createTime": "2026-01-01T00:00:00Z",
        "updateTime": "2026-01-02T00:00:00Z",
    }
    yaml_dash = api_dashboard_to_yaml_dashboard(api_dash)
    assert yaml_dash["dashboard_id"] == "my_dash"
    assert yaml_dash["display_name"] == "My Dashboard"
    assert yaml_dash["description"] == "A test dashboard"
    assert yaml_dash["filter"] == "agent_id != ''"
    assert "date_range" in yaml_dash
    assert "root_container" in yaml_dash
    assert "createTime" not in yaml_dash


def test_load_and_dump_dashboards_yaml(tmp_path: typing.Any) -> None:
    """Test loading and dumping dashboards YAML files."""
    file_path = tmp_path / "dashboards.yaml"
    data = {
        "version": "1.0",
        "dashboards": [
            {
                "dashboard_id": "d1",
                "display_name": "Dashboard 1",
                "root_container": {
                    "widgets": [
                        {
                            "container": {
                                "display_name": "Tab 1",
                                "widgets": [],
                            }
                        }
                    ]
                },
            }
        ],
    }

    dump_dashboards_yaml(data, file_path)
    loaded = load_dashboards_yaml(file_path)
    assert len(loaded["dashboards"]) == 1
    assert loaded["dashboards"][0]["dashboard_id"] == "d1"

    # Test file not found
    with pytest.raises(FileNotFoundError):
        load_dashboards_yaml(tmp_path / "non_existent.yaml")

    # Test invalid YAML schema
    bad_file = tmp_path / "bad.yaml"
    bad_file.write_text("dashboards: 'invalid'", encoding="utf-8")
    with pytest.raises(ValueError, match="Schema validation errors"):
        load_dashboards_yaml(bad_file)


def test_export_remote_dashboards_to_yaml_dict() -> None:
    """Test exporting remote dashboards while filtering readOnly dashboards."""
    remote = [
        {
            "name": "projects/p/locations/l/dashboards/user_dash",
            "displayName": "User Dash",
            "readOnly": False,
        },
        {
            "name": "projects/p/locations/l/dashboards/system_dash",
            "displayName": "System Dash",
            "readOnly": True,
        },
    ]
    res = export_remote_dashboards_to_yaml_dict(remote, "p", "l")
    assert res["project_id"] == "p"
    assert res["location"] == "l"
    assert len(res["dashboards"]) == 1
    assert res["dashboards"][0]["dashboard_id"] == "user_dash"


def test_diff_dashboards() -> None:
    """Test diffing local dashboards against remote dashboards."""
    local_data = {
        "dashboards": [
            {
                "dashboard_id": "dash_create",
                "display_name": "New Dashboard",
                "root_container": {"widgets": [{"container": {"display_name": "T"}}]},
            },
            {
                "dashboard_id": "dash_update",
                "display_name": "Updated Title",
                "description": "Updated description",
                "filter": "new_filter",
                "date_range": {"relative": {"quantity": 30, "unit": "DAY"}},
                "root_container": {"widgets": [{"container": {"display_name": "T2"}}]},
            },
            {
                "dashboard_id": "dash_unchanged",
                "display_name": "Same Title",
                "root_container": {"widgets": [{"container": {"display_name": "T"}}]},
            },
        ]
    }

    remote_dashboards = [
        {
            "name": "projects/p/locations/l/dashboards/dash_update",
            "displayName": "Old Title",
            "description": "Old description",
            "filter": "old_filter",
            "dateRangeConfig": {"relativeDateRange": {"quantity": 7, "unit": "DAY"}},
            "rootContainer": {"widgets": [{"container": {"display_name": "T1"}}]},
        },
        {
            "name": "projects/p/locations/l/dashboards/dash_unchanged",
            "displayName": "Same Title",
            "rootContainer": {
                "widgets": [{"container": {"displayName": "T", "widgets": []}}]
            },
        },
        {
            "name": "projects/p/locations/l/dashboards/dash_delete",
            "displayName": "To Delete",
            "readOnly": False,
        },
        {
            "name": "projects/p/locations/l/dashboards/sys_dash",
            "displayName": "System Read Only",
            "readOnly": True,
        },
    ]

    diff = diff_dashboards(local_data, remote_dashboards)
    assert len(diff["to_create"]) == 1
    assert diff["to_create"][0][0] == "dash_create"

    assert len(diff["to_update"]) == 1
    assert diff["to_update"][0][0] == "dash_update"
    assert "displayName" in diff["to_update"][0][2]
    assert "description" in diff["to_update"][0][2]
    assert "filter" in diff["to_update"][0][2]
    assert "dateRangeConfig" in diff["to_update"][0][2]
    assert "rootContainer" in diff["to_update"][0][2]

    assert len(diff["to_delete"]) == 1
    assert diff["to_delete"][0] == "projects/p/locations/l/dashboards/dash_delete"

    assert diff["unchanged"] == ["dash_unchanged"]
    assert "=== Configurable Dashboards Diff ===" in diff["report"]
    assert "[+] To Create" in diff["report"]
    assert "[~] To Update" in diff["report"]
    assert "[-] To Delete" in diff["report"]
    assert "[=] Unchanged" in diff["report"]


def test_diff_dashboards_all_in_sync() -> None:
    """Test diffing when all dashboards match."""
    local_data = {
        "dashboards": [
            {
                "dashboard_id": "d1",
                "display_name": "Dash 1",
                "root_container": {"widgets": []},
            }
        ]
    }
    remote = [
        {
            "name": "projects/p/locations/l/dashboards/d1",
            "displayName": "Dash 1",
            "rootContainer": {"widgets": []},
        }
    ]
    diff = diff_dashboards(local_data, remote)
    assert "All 1 dashboard(s) are in sync" in diff["report"]


def test_sync_dashboards(tmp_path: typing.Any) -> None:
    """Test sync_dashboards in dry-run, force, and non-force modes."""
    file_path = tmp_path / "dashboards.yaml"
    data = {
        "dashboards": [
            {
                "dashboard_id": "d_new",
                "display_name": "New Dash",
                "root_container": {"widgets": [{"container": {"display_name": "T"}}]},
            },
            {
                "dashboard_id": "d_mod",
                "display_name": "Updated Mod Dash",
                "root_container": {"widgets": [{"container": {"display_name": "T"}}]},
            },
        ]
    }
    dump_dashboards_yaml(data, file_path)

    remote = [
        {
            "name": "projects/p/locations/l/dashboards/d_mod",
            "displayName": "Old Mod Dash",
            "rootContainer": {"widgets": [{"container": {"display_name": "T"}}]},
        },
        {
            "name": "projects/p/locations/l/dashboards/d_orphan",
            "displayName": "Orphan Dash",
            "readOnly": False,
        },
    ]

    mock_client = MagicMock()
    mock_client.parent = "projects/p/locations/l"
    mock_client.list_dashboards.return_value = remote

    # 1. Dry run
    res_dry = sync_dashboards(mock_client, file_path, dry_run=True)
    assert res_dry["created"] == ["d_new"]
    assert res_dry["updated"] == ["d_mod"]
    assert res_dry["skipped_delete"] == ["projects/p/locations/l/dashboards/d_orphan"]
    mock_client.create_dashboard.assert_not_called()
    mock_client.update_dashboard.assert_not_called()
    mock_client.delete_dashboard.assert_not_called()

    # 2. Apply without force (should skip delete)
    res_apply = sync_dashboards(mock_client, file_path, force=False, dry_run=False)
    assert res_apply["created"] == ["d_new"]
    assert res_apply["updated"] == ["d_mod"]
    assert res_apply["deleted"] == []
    assert res_apply["skipped_delete"] == ["projects/p/locations/l/dashboards/d_orphan"]
    mock_client.create_dashboard.assert_called_once()
    mock_client.update_dashboard.assert_called_once()
    mock_client.delete_dashboard.assert_not_called()

    # 3. Apply with force (should delete orphan)
    mock_client.reset_mock()
    mock_client.list_dashboards.return_value = remote
    res_force = sync_dashboards(mock_client, file_path, force=True, dry_run=False)
    assert res_force["deleted"] == ["projects/p/locations/l/dashboards/d_orphan"]
    mock_client.delete_dashboard.assert_called_once_with(
        name="projects/p/locations/l/dashboards/d_orphan"
    )


def test_normalize_date_range_and_container_fields() -> None:
    """Test absolute date range and optional container/chart fields normalization."""
    dash_dict = {
        "dashboard_id": "d_full",
        "display_name": "Full Dash",
        "description": "Full Desc",
        "filter": "active = true",
        "date_range": {
            "absolute": {
                "startTime": "2026-01-01T00:00:00Z",
                "endTime": "2026-01-31T23:59:59Z",
            }
        },
        "root_container": {
            "display_name": "Root Container",
            "description": "Root Desc",
            "width": 12,
            "height": 10,
            "filter": "country = 'US'",
            "date_range": {
                "absolute": {
                    "startTime": "2026-01-01T00:00:00Z",
                    "endTime": "2026-01-31T23:59:59Z",
                }
            },
            "widgets": [
                {
                    "container": {
                        "display_name": "Tab 1",
                        "description": "Tab Desc",
                        "width": 12,
                        "height": 5,
                        "filter": "region = 'EAST'",
                        "date_range": {"relative": {"quantity": 1, "unit": "MONTH"}},
                        "widgets": [
                            {
                                "chart": {
                                    "display_name": "Chart Full",
                                    "description": "Chart Desc",
                                    "chart_visualization_type": "LINE",
                                    "width": 6,
                                    "height": 4,
                                    "filter": "sentiment > 0",
                                    "date_range": {
                                        "relative": {"quantity": 3, "unit": "WEEK"}
                                    },
                                    "data_source": {
                                        "custom_source": {"custom": "data"}
                                    },
                                }
                            }
                        ],
                    }
                }
            ],
        },
    }
    dash_id, payload = yaml_dashboard_to_api_payload(dash_dict)
    assert dash_id == "d_full"
    assert payload["description"] == "Full Desc"
    assert payload["filter"] == "active = true"
    assert "absoluteDateRange" in payload["dateRangeConfig"]
    root = payload["rootContainer"]
    assert root["description"] == "Root Desc"
    assert root["width"] == 12
    assert root["height"] == 10
    assert root["filter"] == "country = 'US'"
    assert "absoluteDateRange" in root["dateRangeConfig"]
    tab = root["widgets"][0]["container"]
    assert tab["description"] == "Tab Desc"
    assert tab["filter"] == "region = 'EAST'"
    chart = tab["widgets"][0]["chart"]
    assert chart["description"] == "Chart Desc"
    assert chart["width"] == 6
    assert chart["height"] == 4
    assert chart["filter"] == "sentiment > 0"
    assert chart["dataSource"] == {"custom_source": {"custom": "data"}}


def test_diff_dashboards_single_object_and_non_root_update() -> None:
    """Test diffing with single dashboard object and non-rootContainer field updates."""
    local_single = {
        "dashboard_id": "d_single",
        "display_name": "Updated Title Only",
        "description": "Same Desc",
        "root_container": {
            "widgets": [{"container": {"displayName": "T", "widgets": []}}]
        },
    }
    remote = [
        {
            "name": "projects/p/locations/l/dashboards/d_single",
            "displayName": "Old Title",
            "description": "Same Desc",
            "rootContainer": {
                "widgets": [{"container": {"displayName": "T", "widgets": []}}]
            },
        }
    ]
    diff = diff_dashboards(local_single, remote)
    assert len(diff["to_update"]) == 1
    assert diff["to_update"][0][2] == ["displayName"]
    assert "[~] To Update" in diff["report"]
    assert "Fields changed: displayName" in diff["report"]


def test_validation_deep_edge_cases() -> None:
    """Test deep container and chart validation edge cases."""
    invalid_structure = {
        "dashboards": [
            {
                "dashboard_id": "edge_cases",
                "display_name": "Edge Cases",
                "root_container": {
                    "widgets": [
                        {
                            "container": {
                                "display_name": "Tab with edge widgets",
                                "date_range": {},  # empty date range
                                "widgets": [
                                    {"container": "not_a_dict"},
                                    {"chart": "not_a_dict"},
                                    {
                                        "container": {
                                            "display_name": "Nested Tab",
                                            "date_range": {
                                                "relative": {"unit": "DAY"}
                                            },
                                            "widgets": [],
                                        }
                                    },
                                ],
                            }
                        }
                    ]
                },
            }
        ]
    }
    errors = validate_dashboards_dict(invalid_structure)
    assert any("must specify either 'relative' or 'absolute'" in e for e in errors)
    assert any("Container must be a dictionary" in e for e in errors)
    assert any("Chart must be a dictionary" in e for e in errors)
