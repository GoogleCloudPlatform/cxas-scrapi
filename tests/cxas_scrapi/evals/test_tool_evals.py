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

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from pydantic import ValidationError

from cxas_scrapi.evals.tool_evals import (
    SUMMARY_SCHEMA_COLUMNS,
    Expectation,
    Operator,
    ToolEvals,
    ToolTestCase,
)


def test_operator_enum():
    assert Operator.EQUALS.value == "equals"
    assert Operator.CONTAINS.value == "contains"


def test_expectation_model():
    exp = Expectation(path="$.result", operator=Operator.EQUALS, value="PASSED")
    assert exp.path == "$.result"
    assert exp.operator == Operator.EQUALS
    assert exp.value == "PASSED"


@patch("cxas_scrapi.evals.tool_evals.Tools")
@patch("cxas_scrapi.evals.tool_evals.Variables")
def test_tool_evals_init(mock_variables, mock_tools):
    mock_tools_instance = mock_tools.return_value
    mock_tools_instance.get_tools_map.return_value = {"tool1": "id1"}

    tu = ToolEvals(app_name="projects/p/locations/l/apps/test_app", creds=None)
    assert tu.app_name == "projects/p/locations/l/apps/test_app"
    assert tu.tool_map == {"tool1": "id1"}
    mock_tools_instance.get_tools_map.assert_called_once_with(reverse=True)


def test_parse_dict_input():
    assert ToolEvals._parse_dict_input(None) == {}
    assert ToolEvals._parse_dict_input('{"a": 1}') == {"a": 1}
    assert ToolEvals._parse_dict_input("invalid") == {}
    assert ToolEvals._parse_dict_input(["a", "b"]) == {"a": None, "b": None}
    assert ToolEvals._parse_dict_input({"a": 1}) == {"a": 1}
    assert ToolEvals._parse_dict_input(123) == {}


def test_parse_python_code():
    code = """
def my_tool(arg1, arg2):
    return {"status": "SUCCESS", "id": 123}
"""
    args, returns = ToolEvals._parse_python_code(code)
    assert args == {"arg1": "[arg1]", "arg2": "[arg2]"}
    assert set(returns) == {"status", "id"}


def test_parse_python_code_invalid():
    args, returns = ToolEvals._parse_python_code("def foo( *invalid syntax")
    assert args == {}
    assert returns == []


def test_parse_properties():
    # Helper to avoid instantiating ToolEvals to test isolated util
    tu = ToolEvals.__new__(ToolEvals)
    props = {
        "str_prop": {"type": "string"},
        "int_prop": {"type": "integer"},
        "bool_prop": {"type": "boolean"},
        "arr_prop": {"type": "array", "items": {"type": "string"}},
        "obj_prop": {
            "type": "object",
            "properties": {"nested": {"type": "string"}},
        },
        "unknown_prop": {"type": "unknown"},
    }
    parsed = tu._parse_properties(props)
    assert parsed["str_prop"] == "[str_prop]"
    assert parsed["int_prop"] == 0
    assert parsed["bool_prop"] is False
    assert parsed["arr_prop"] == ["[arr_prop_1]", "[arr_prop_2]"]
    assert parsed["obj_prop"] == {"nested": "[nested]"}
    assert parsed["unknown_prop"] == "[unknown_prop]"


def test_get_value_at_path():
    tu = ToolEvals.__new__(ToolEvals)
    data = {"a": {"b": [{"c": 1}, {"c": 2}]}}
    assert tu._get_value_at_path(data, "$.a.b[0].c") == 1
    assert tu._get_value_at_path(data, "$.a.b[*].c") == [1, 2]
    assert tu._get_value_at_path(data, "$.not.found") is None


def test_check_expectation():
    tu = ToolEvals.__new__(ToolEvals)
    assert tu._check_expectation(
        1, Expectation(path="", operator=Operator.EQUALS, value=1)
    )
    assert not tu._check_expectation(
        1, Expectation(path="", operator=Operator.EQUALS, value=2)
    )

    assert tu._check_expectation(
        "abc", Expectation(path="", operator=Operator.CONTAINS, value="b")
    )
    assert not tu._check_expectation(
        "abc", Expectation(path="", operator=Operator.CONTAINS, value="d")
    )
    assert not tu._check_expectation(
        123, Expectation(path="", operator=Operator.CONTAINS, value="1")
    )

    assert tu._check_expectation(
        5, Expectation(path="", operator=Operator.GREATER_THAN, value=3)
    )
    assert tu._check_expectation(
        3, Expectation(path="", operator=Operator.LESS_THAN, value=5)
    )

    assert tu._check_expectation(
        [1, 2], Expectation(path="", operator=Operator.LENGTH_EQUALS, value=2)
    )
    assert tu._check_expectation(
        [1, 2, 3],
        Expectation(path="", operator=Operator.LENGTH_GREATER_THAN, value=2),
    )
    assert tu._check_expectation(
        [1], Expectation(path="", operator=Operator.LENGTH_LESS_THAN, value=2)
    )

    assert tu._check_expectation(
        None, Expectation(path="", operator=Operator.IS_NULL)
    )
    assert tu._check_expectation(
        1, Expectation(path="", operator=Operator.IS_NOT_NULL)
    )


def test_tool_test_case_validation():
    # Test that empty expectations works
    tc = ToolTestCase(name="t1", tool="tool1")
    assert tc.args == {}
    assert tc.response_expectations == []

    # Test parsing aliases
    tc2 = ToolTestCase(
        name="t2",
        tool="tool2",
        args={"a": 1},
        expectations={
            "response": [{"path": "$.x", "operator": "equals", "value": 1}]
        },
    )
    assert tc2.response_expectations[0].path == "$.x"

    # Test context/variables mutual exclusivity
    with pytest.raises(
        ValidationError, match="either 'variables' or 'context'"
    ):
        ToolTestCase(
            name="t3", tool="tool3", variables={"a": 1}, context={"b": 2}
        )

    # Context only works
    tc4 = ToolTestCase(name="t4", tool="tool4", context={"a": 1})
    assert tc4.context == {"a": 1}
    assert tc4.variables == {}

    # Variables only works
    tc5 = ToolTestCase(name="t5", tool="tool5", variables={"a": 1})
    assert tc5.variables == {"a": 1}
    assert tc5.context == {}


def test_parse_python_function():
    tu = ToolEvals.__new__(ToolEvals)
    # Tool with properties in schema
    t1 = {
        "python_function": {
            "parameters": {"properties": {"foo": {"type": "string"}}}
        }
    }
    args, returns = tu._parse_python_function(t1)
    assert args == {"foo": "[foo]"}
    assert returns == []

    # Tool with just python_code
    t2 = {
        "python_function": {
            "python_code": "def func(hello):\n    return {'world': 1}"
        }
    }
    args2, returns2 = tu._parse_python_function(t2)
    assert args2 == {"hello": "[hello]"}
    assert returns2 == ["world"]


def test_parse_openapi_toolset():
    tu = ToolEvals.__new__(ToolEvals)
    schema = """
paths:
  /test:
    get:
      operationId: getTest
      parameters:
        - name: q
          schema:
            type: string
    post:
      operationId: postTest
      requestBody:
        content:
          application/json:
            schema:
              properties:
                body_prop:
                  type: integer
"""
    t1 = {"open_api_toolset": {"open_api_schema": schema}}

    args, returns = tu._parse_openapi_toolset(t1, "MyTool_getTest")
    assert args == {"q": "[q]"}
    assert returns == []

    args2, returns2 = tu._parse_openapi_toolset(t1, "MyTool_postTest")
    assert args2 == {"body_prop": 0}
    assert returns2 == []


def test_validate_tool_test():
    tu = ToolEvals.__new__(ToolEvals)

    tc = ToolTestCase(
        name="test1",
        tool="tool1",
        expectations={
            "response": [
                {"path": "$.status", "operator": "equals", "value": "OK"}
            ],
            "variables": [{"path": "$.var1", "operator": "is_not_null"}],
        },
    )

    resp_pass = {"response": {"status": "OK"}, "variables": {"var1": "found"}}
    errors = tu.validate_tool_test(tc, resp_pass)
    assert len(errors) == 0

    resp_fail = {"response": {"status": "ERROR"}, "variables": {}}
    errors = tu.validate_tool_test(tc, resp_fail)
    assert len(errors) == 2
    assert "Response expectation failed" in errors[0]
    assert "Variable expectation failed" in errors[1]


@patch("cxas_scrapi.evals.tool_evals.Tools")
@patch("cxas_scrapi.evals.tool_evals.Variables")
@patch("cxas_scrapi.evals.tool_evals.Apps")
def test_run_tool_tests(mock_apps, mock_variables, mock_tools):
    mock_tools_instance = mock_tools.return_value
    mock_tools_instance.get_tools_map.return_value = {
        "tool1": "projects/p/locations/l/apps/test_app/tools/tool1"
    }
    mock_tools_instance.execute_tool.return_value = {
        "response": {"status": "OK"}
    }

    mock_var_instance = mock_variables.return_value
    mock_var_instance.list_variables.return_value = []

    mock_app_instance = MagicMock()
    mock_app_instance.display_name = "Test App"
    mock_apps.return_value.get_app.return_value = mock_app_instance

    tu = ToolEvals(app_name="projects/p/locations/l/apps/test_app", creds=None)

    tc = ToolTestCase(
        name="test1",
        tool="tool1",
        expectations={
            "response": [
                {"path": "$.status", "operator": "equals", "value": "OK"}
            ]
        },
    )

    # Run tests
    df = tu.run_tool_tests([tc])

    assert len(df) == 1
    assert df.iloc[0]["status"] == "PASSED"
    assert df.iloc[0]["test_name"] == "test1"
    assert "latency (ms)" in df.columns
    assert df.iloc[0]["app_display_name"] == "Test App"
    assert df.iloc[0]["tester"] == "Unknown"

    # Ensure it calls execute_tool correctly
    mock_tools_instance.execute_tool.assert_called_once_with(
        tool_display_name="tool1",
        args={},
        variables={},
        context={},
    )


@patch("cxas_scrapi.evals.tool_evals.Tools")
@patch("cxas_scrapi.evals.tool_evals.Variables")
@patch("cxas_scrapi.evals.tool_evals.Apps")
def test_run_tool_tests_with_context(mock_apps, mock_variables, mock_tools):
    mock_tools_instance = mock_tools.return_value
    mock_tools_instance.get_tools_map.return_value = {
        "tool1": "projects/p/locations/l/apps/test_app/tools/tool1"
    }
    mock_tools_instance.execute_tool.return_value = {
        "response": {"status": "OK"}
    }

    mock_var_instance = mock_variables.return_value
    mock_var_instance.list_variables.return_value = []

    mock_app_instance = MagicMock()
    mock_app_instance.display_name = "Test App"
    mock_apps.return_value.get_app.return_value = mock_app_instance

    tu = ToolEvals(app_name="projects/p/locations/l/apps/test_app", creds=None)

    tc = ToolTestCase(
        name="test2",
        tool="tool1",
        context={"ctx1": "value2"},
        expectations={
            "response": [
                {"path": "$.status", "operator": "equals", "value": "OK"}
            ]
        },
    )

    # Run tests
    df = tu.run_tool_tests([tc])

    assert len(df) == 1
    assert df.iloc[0]["status"] == "PASSED"
    assert df.iloc[0]["test_name"] == "test2"
    assert "latency (ms)" in df.columns
    assert df.iloc[0]["app_display_name"] == "Test App"

    # Ensure it calls execute_tool correctly
    mock_tools_instance.execute_tool.assert_called_once_with(
        tool_display_name="tool1",
        args={},
        variables={},
        context={"ctx1": "value2"},
    )


@patch("cxas_scrapi.evals.tool_evals.Tools")
@patch("cxas_scrapi.evals.tool_evals.Variables")
@patch("cxas_scrapi.evals.tool_evals.Apps")
def test_run_tool_tests_openapi_with_context_fails(
    mock_apps, mock_variables, mock_tools
):
    mock_tools_instance = mock_tools.return_value
    mock_tools_instance.get_tools_map.return_value = {
        "tool1": "toolsets/my_openapi_tool"
    }

    mock_var_instance = mock_variables.return_value
    mock_var_instance.list_variables.return_value = []

    mock_app_instance = MagicMock()
    mock_app_instance.display_name = "Test App"
    mock_apps.return_value.get_app.return_value = mock_app_instance

    tu = ToolEvals(app_name="projects/p/locations/l/apps/test_app", creds=None)

    tc = ToolTestCase(
        name="test_openapi",
        tool="tool1",
        context={"ctx1": "value2"},
    )

    # Run tests
    df = tu.run_tool_tests([tc])

    assert len(df) == 1
    assert df.iloc[0]["status"] == "FAILED"
    assert df.iloc[0]["app_display_name"] == "Test App"

    # execute_tool should not be called
    mock_tools_instance.execute_tool.assert_not_called()


def test_calculate_stats():
    tu = ToolEvals.__new__(ToolEvals)
    df = pd.DataFrame(
        [
            {
                "status": "PASSED",
                "latency (ms)": 100,
                "app_display_name": "App 1",
                "tester": "user@google.com",
            },
            {
                "status": "FAILED",
                "latency (ms)": 200,
                "app_display_name": "App 1",
                "tester": "user@google.com",
            },
            {
                "status": "PASSED",
                "latency (ms)": 300,
                "app_display_name": "App 1",
                "tester": "user@google.com",
            },
        ]
    )

    stats = tu._calculate_stats(df)

    assert stats.total_tests == 3
    assert stats.pass_count == 2
    assert stats.pass_rate == 2 / 3
    assert stats.p50_latency_ms == 200.0
    assert stats.p90_latency_ms == 280.0
    assert stats.p99_latency_ms == 298.0
    assert stats.agent_name == "App 1"
    assert stats.tester == "user@google.com"


def test_calculate_stats_empty():
    tu = ToolEvals.__new__(ToolEvals)
    df = pd.DataFrame()

    stats = tu._calculate_stats(df)
    assert stats.total_tests == 0
    assert stats.pass_count == 0
    assert stats.pass_rate == 0.0


def test_generate_report():
    tu = ToolEvals.__new__(ToolEvals)
    df = pd.DataFrame(
        [
            {
                "status": "PASSED",
                "latency (ms)": 100,
                "app_display_name": "App 1",
                "tester": "user@google.com",
            },
        ]
    )

    report_df = tu.generate_report(df)

    assert list(report_df.columns) == SUMMARY_SCHEMA_COLUMNS
    assert len(report_df) == 1
    assert report_df.iloc[0]["total_tests"] == 1
    assert report_df.iloc[0]["pass_rate"] == 1.0
