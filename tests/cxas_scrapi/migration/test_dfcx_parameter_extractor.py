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

import re
from unittest.mock import MagicMock

from cxas_scrapi.migration.dfcx_parameter_extractor import (
    DFCXParameterExtractor,
)


def test_infer_schema_from_value():
    assert DFCXParameterExtractor.infer_schema_from_value(True) == {
        "type": "BOOLEAN"
    }
    assert DFCXParameterExtractor.infer_schema_from_value(1) == {
        "type": "NUMBER"
    }
    assert DFCXParameterExtractor.infer_schema_from_value(1.5) == {
        "type": "NUMBER"
    }
    assert DFCXParameterExtractor.infer_schema_from_value("string") == {
        "type": "STRING"
    }
    assert DFCXParameterExtractor.infer_schema_from_value({}) == {
        "type": "OBJECT"
    }
    assert DFCXParameterExtractor.infer_schema_from_value([]) == {
        "type": "ARRAY"
    }


def test_register_param_new():
    unified_parameters = {}
    parameter_name_map = {}
    DFCXParameterExtractor.register_param(
        "session.params.var1",
        {"type": "STRING"},
        "desc",
        "source",
        unified_parameters,
        parameter_name_map,
    )

    assert "var1" in unified_parameters
    assert unified_parameters["var1"]["name"] == "var1"
    assert unified_parameters["var1"]["schema"] == {"type": "STRING"}
    assert parameter_name_map["session.params.var1"] == "var1"


def test_register_param_upgrade_type():
    unified_parameters = {
        "var1": {
            "name": "var1",
            "schema": {"type": "STRING"},
            "_confidence": 1,
        }
    }
    parameter_name_map = {"session.params.var1": "var1"}

    DFCXParameterExtractor.register_param(
        "session.params.var1",
        {"type": "NUMBER"},
        "desc",
        "source",
        unified_parameters,
        parameter_name_map,
    )

    assert unified_parameters["var1"]["schema"] == {"type": "NUMBER"}
    assert unified_parameters["var1"]["_confidence"] == 2


def test_deep_scan_for_variables():
    obj = {
        "setParameterActions": [{"parameter": "var1", "value": 123}],
        "text": "Hello $var2",
    }
    unified_parameters = {}
    parameter_name_map = {}
    var_pattern = re.compile(
        r"\$(?:session\.params\.|page\.params\.|flow\.params\.)?([a-zA-Z_][a-zA-Z0-9_-]*)"
    )

    DFCXParameterExtractor.deep_scan_for_variables(
        obj, var_pattern, unified_parameters, parameter_name_map
    )

    assert "var1" in unified_parameters
    assert "var2" in unified_parameters
    assert unified_parameters["var1"]["schema"] == {"type": "NUMBER"}
    assert unified_parameters["var2"]["schema"] == {"type": "STRING"}


def test_migrate_parameters():
    source_agent_data = {
        "playbooks": [
            {
                "displayName": "Playbook1",
                "inputParameterDefinitions": [
                    {
                        "name": "var1",
                        "typeSchema": {"inlineSchema": {"type": "STRING"}},
                    }
                ],
            }
        ],
        "flows": [
            {
                "pages": [
                    {
                        "value": {
                            "displayName": "Page1",
                            "form": {
                                "parameters": [
                                    {
                                        "displayName": "var2",
                                        "entityType": "sys.number",
                                    }
                                ]
                            },
                        }
                    }
                ]
            }
        ],
    }
    mock_reporter = MagicMock()

    final_declarations, parameter_name_map = (
        DFCXParameterExtractor.migrate_parameters(
            source_agent_data, mock_reporter
        )
    )

    assert len(final_declarations) == 2
    assert parameter_name_map["var1"] == "var1"
    assert parameter_name_map["var2"] == "var2"
    assert mock_reporter.log_variable.call_count == 2
