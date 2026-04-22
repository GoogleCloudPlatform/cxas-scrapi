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

"""Utility functions for processing and generating CXAS Tool Tests."""

import ast
import enum
import json
import logging
import os
import time
from datetime import datetime
from typing import Annotated, Any, Dict, List, NamedTuple, Optional

import pandas as pd
import yaml
from google.protobuf.json_format import MessageToDict
from jsonpath_ng import parse
from pydantic import (
    AliasChoices,
    AliasPath,
    BaseModel,
    BeforeValidator,
    Field,
    TypeAdapter,
    model_validator,
)

from cxas_scrapi.core.apps import Apps
from cxas_scrapi.core.common import Common
from cxas_scrapi.core.conversation_history import ConversationHistory
from cxas_scrapi.core.tools import Tools
from cxas_scrapi.core.variables import Variables

logger = logging.getLogger(__name__)

SUMMARY_SCHEMA_COLUMNS = [
    "test_run_timestamp",
    "total_tests",
    "pass_count",
    "pass_rate",
    "agent_name",
    "tester",
    "p50_latency_ms",
    "p90_latency_ms",
    "p99_latency_ms",
]


class SummaryStats(NamedTuple):
    test_run_timestamp: str
    total_tests: int
    pass_count: int
    pass_rate: float
    agent_name: str
    tester: str
    p50_latency_ms: float
    p90_latency_ms: float
    p99_latency_ms: float


class Operator(str, enum.Enum):
    """Operators for testing expectations."""

    EQUALS = "equals"
    CONTAINS = "contains"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    LENGTH_EQUALS = "length_equals"
    LENGTH_GREATER_THAN = "length_greater_than"
    LENGTH_LESS_THAN = "length_less_than"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


class Expectation(BaseModel):
    """Data model for a single test expectation."""

    path: str
    operator: Operator
    value: Optional[Any] = None


class ToolEvals:
    """Utility class for testing CXAS Tools."""

    def __init__(self, app_name: str, creds=None):
        """Initializes the ToolEvals class.

        Args:
            app_name: CXAS App name
                (projects/{project}/locations/{location}/apps/{app}).
            creds: Optional Google Cloud credentials.
        """
        self.app_name = app_name

        parts = self.app_name.split("/")
        self.project_id = parts[1] if len(parts) > 1 else ""
        self.location = parts[3] if len(parts) > 3 else "us"

        self.creds = creds
        self.tools_client = Tools(app_name=self.app_name, creds=self.creds)
        self.var_client = Variables(app_name=self.app_name, creds=self.creds)
        try:
            self.tool_map = self.tools_client.get_tools_map(reverse=True)
        except (AttributeError, KeyError, RuntimeError, ValueError) as e:
            logger.warning(
                "Failed to fetch tool map for %s: %s", self.app_name, e
            )
            self.tool_map = {}

    @staticmethod
    def _parse_dict_input(v: Any) -> Dict[str, Any]:
        """Allows YAML to accept a list of strings OR a custom dictionary."""
        if v is None:
            return {}
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return {}
        if isinstance(v, list):
            # Convert list of names to a dict flagged for fetching (None)
            return {str(item): None for item in v}
        if isinstance(v, dict):
            return v
        return {}

    @staticmethod
    def _parse_python_code(python_code: str) -> tuple[dict, list[str]]:
        try:
            tree = ast.parse(python_code)
        except SyntaxError:
            return {}, []

        args = {}
        return_keys = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for arg in node.args.args:
                    arg_name = arg.arg
                    if arg_name != "self":
                        args[arg_name] = f"[{arg_name}]"

                for sub_node in ast.walk(node):
                    if isinstance(sub_node, ast.Return):
                        if isinstance(sub_node.value, ast.Dict):
                            for key in sub_node.value.keys:
                                if isinstance(key, ast.Constant):
                                    return_keys.append(str(key.value))
                break
        return args, list(set(return_keys))

    def _parse_properties(self, props: dict) -> dict:
        result = {}
        for p_name, p_details in props.items():
            p_type = str(p_details.get("type", "string")).lower()
            if p_type == "string":
                result[p_name] = f"[{p_name}]"
            elif p_type in ["integer", "number"]:
                result[p_name] = 0
            elif p_type == "boolean":
                result[p_name] = False
            elif p_type == "array":
                items = p_details.get("items", {})
                if items.get("type", "").lower() == "string":
                    result[p_name] = [f"[{p_name}_1]", f"[{p_name}_2]"]
                else:
                    result[p_name] = []
            elif p_type == "object":
                nested_props = p_details.get("properties", {})
                if nested_props:
                    result[p_name] = self._parse_properties(nested_props)
                else:
                    result[p_name] = {}
            else:
                result[p_name] = f"[{p_name}]"
        return result

    def _get_value_at_path(self, data: Any, path: str) -> Any:
        """Retrieves value using dot notation (e.g., 'a.b.c')."""
        jsonpath_expression = parse(path)
        matches = jsonpath_expression.find(data)
        if matches:
            if len(matches) > 1:
                return [m.value for m in matches]
            return matches[0].value
        return None

    def _check_expectation(
        self, actual: Any, expectation: "Expectation"
    ) -> bool:
        """Checks if actual value meets the expectation."""
        op = expectation.operator
        expected = expectation.value

        if op == Operator.EQUALS:
            return actual == expected
        elif op == Operator.CONTAINS:
            if isinstance(actual, (str, list, dict)):
                return expected in actual
            return False
        elif op == Operator.GREATER_THAN:
            try:
                return actual > expected
            except TypeError:
                return False
        elif op == Operator.LESS_THAN:
            try:
                return actual < expected
            except TypeError:
                return False
        elif op == Operator.LENGTH_EQUALS:
            try:
                return len(actual) == expected
            except TypeError:
                return False
        elif op == Operator.LENGTH_GREATER_THAN:
            try:
                return len(actual) > expected
            except TypeError:
                return False
        elif op == Operator.LENGTH_LESS_THAN:
            try:
                return len(actual) < expected
            except TypeError:
                return False
        elif op == Operator.IS_NULL:
            return actual is None
        elif op == Operator.IS_NOT_NULL:
            return actual is not None
        return False

    def _parse_python_function(self, tool_dict: Dict) -> tuple[Dict, List[str]]:
        """Parses a Python function tool for test template arguments and
        returns."""
        template_args = {}
        expected_returns = []

        properties = (
            tool_dict.get("python_function", {})
            .get("parameters", {})
            .get("properties", {})
        )
        if properties:
            template_args = self._parse_properties(properties)
        else:
            python_code = tool_dict.get("python_function", {}).get(
                "python_code", ""
            )
            if python_code:
                template_args, expected_returns = self._parse_python_code(
                    python_code
                )

        return template_args, expected_returns

    def _parse_openapi_toolset(
        self, tool_dict: Dict, display_name: str
    ) -> tuple[Dict, List[str]]:
        """Parses an OpenAPI toolset for test template arguments."""
        template_args = {}
        expected_returns = []

        schema_str = tool_dict.get("open_api_toolset", {}).get(
            "open_api_schema", ""
        )
        if schema_str:
            try:
                schema = yaml.safe_load(schema_str)
                for _path, methods in schema.get("paths", {}).items():
                    if not isinstance(methods, dict):
                        continue
                    for _method, details in methods.items():
                        if not isinstance(details, dict):
                            continue
                        op_id = details.get("operationId")
                        if op_id and display_name.endswith(op_id):
                            # Path/Query parameters
                            for param in details.get("parameters", []):
                                p_name = param.get("name")
                                p_schema = param.get("schema", {})
                                if p_name and p_schema:
                                    template_args.update(
                                        self._parse_properties(
                                            {p_name: p_schema}
                                        )
                                    )

                            # Request body
                            req_body = details.get("requestBody", {})
                            if req_body:
                                content = req_body.get("content", {})
                                json_schema = content.get(
                                    "application/json", {}
                                ).get("schema", {})
                                if "properties" in json_schema:
                                    template_args.update(
                                        self._parse_properties(
                                            json_schema["properties"]
                                        )
                                    )
            except Exception as e:
                logger.warning(
                    f"Failed to parse OpenAPI schema for {display_name}: {e}"
                )

        return template_args, expected_returns

    def _write_tool_test_template(
        self,
        display_name: str,
        template_args: Dict[str, Any],
        expected_returns: List[str],
        target_dir: str,
        overwrite: bool,
        example_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Writes the generated tool test template to a YAML file."""
        expectations = []
        has_example_response = False

        if example_data and "response" in example_data:
            response_data = example_data["response"]
            if isinstance(response_data, dict) and response_data:
                for k, v in response_data.items():
                    if isinstance(v, (str, int, float, bool)):
                        expectations.append(
                            {"path": f"$.{k}", "operator": "equals", "value": v}
                        )
                    else:
                        expectations.append(
                            {"path": f"$.{k}", "operator": "is_not_null"}
                        )
                has_example_response = True

        if not has_example_response:
            if expected_returns:
                for req_key in expected_returns:
                    expectations.append(
                        {"path": f"$.{req_key}", "operator": "is_not_null"}
                    )
            else:
                expectations = [
                    {
                        "path": "$.result",
                        "operator": "contains",
                        "value": "PASSED",
                    }
                ]

        test_dict = {
            "name": f"{display_name}_test_1",
            "tool": display_name,
            "expectations": {"response": expectations},
        }

        args = template_args
        if example_data and "args" in example_data:
            args = example_data["args"]

        if args:
            test_dict["args"] = args

        test_content = {"tests": [test_dict]}

        # Use safe filename
        safe_name = display_name.replace("/", "_").replace(" ", "_")
        file_path = os.path.join(target_dir, f"{safe_name}.yaml")

        if not os.path.exists(file_path):
            with open(file_path, "w", encoding="utf-8") as f:
                yaml.dump(
                    test_content, f, sort_keys=False, default_flow_style=False
                )
            print(f"Generated test template: {file_path}")
        elif overwrite:
            with open(file_path, "w", encoding="utf-8") as f:
                yaml.dump(
                    test_content, f, sort_keys=False, default_flow_style=False
                )
            print(f"Overwrote existing test template: {file_path}")
        else:
            print(f"Skipping existing test file: {file_path}")

    def _mine_tool_data(self, limit: int) -> Dict[str, Dict[str, Any]]:
        """Mines recent conversations for actual tool payloads to populate
        tests."""
        mined_data = {}
        try:
            history_client = ConversationHistory(
                app_name=self.app_name, creds=self.creds
            )
            convs = list(history_client.list_conversations())[:limit]
            for c in convs:
                full_c = history_client.get_conversation(c.name)
                try:
                    c_dict = MessageToDict(full_c._pb)
                except AttributeError:
                    c_dict = MessageToDict(full_c)

                turns = c_dict.get("turns", [])
                for turn in turns:
                    turn_calls = {}
                    turn_responses = {}

                    messages = turn.get("messages", [])
                    for msg in messages:
                        chunks = msg.get("chunks", [])

                        for chunk in chunks:
                            if "toolCall" in chunk:
                                tc = chunk["toolCall"]
                                tc_id = tc.get("id")
                                if tc_id:
                                    tool_name = tc.get("displayName")
                                    if not tool_name:
                                        full_tool = tc.get("tool", "")
                                        for d_name, t_id in getattr(
                                            self, "tool_map", {}
                                        ).items():
                                            if (
                                                t_id == full_tool
                                                or full_tool.endswith(
                                                    t_id.split("/")[-1]
                                                )
                                            ):
                                                tool_name = d_name
                                                break
                                        else:
                                            tool_name = full_tool

                                    turn_calls[tc_id] = {
                                        "name": tool_name,
                                        "args": tc.get("args", {}),
                                    }

                            if "toolResponse" in chunk:
                                tr = chunk["toolResponse"]
                                tc_id = tr.get("id")
                                if tc_id:
                                    turn_responses[tc_id] = tr.get(
                                        "response", {}
                                    )

                    # Match calls to responses across the entire turn
                    for tc_id, call_info in turn_calls.items():
                        name = call_info["name"]
                        response = turn_responses.get(tc_id, {})

                        # Store if we don't have it yet, or if this one
                        # actually has a response payload and the old one
                        # didn't
                        if name not in mined_data or (
                            not mined_data[name].get("response") and response
                        ):
                            mined_data[name] = {
                                "args": call_info["args"],
                                "response": response,
                            }
            logger.info(
                f"Successfully mined payload data for {len(mined_data)} tools."
            )
        except Exception as e:
            logger.warning(f"Failed to mine tool data: {e}")

        return mined_data

    def load_tool_test_cases_from_file(
        self, test_file_path: str
    ) -> List["ToolTestCase"]:
        """Loads tool tests from a YAML file."""
        with open(test_file_path, "r", encoding="utf-8") as f:
            return self.load_tool_test_cases_from_yaml(f.read())

    def load_tool_tests_from_dir(
        self, directory_path: str = "tool_tests"
    ) -> List["ToolTestCase"]:
        """Recursively loads all YAML tool tests from a directory."""
        all_tests = []
        if not os.path.exists(directory_path):
            print(f"Directory {directory_path} does not exist.")
            return all_tests

        for root, _, files in os.walk(directory_path):
            for file in files:
                if file.endswith(".yaml") or file.endswith(".yml"):
                    file_path = os.path.join(root, file)
                    try:
                        tests = self.load_tool_test_cases_from_file(file_path)
                        all_tests.extend(tests)
                    except Exception as e:
                        logger.error(f"Error loading {file_path}: {e}")

        return all_tests

    def load_tool_test_cases_from_yaml(
        self, yaml_data: str
    ) -> List["ToolTestCase"]:
        """Loads tool tests from a YAML string."""
        raw_data = yaml.safe_load(yaml_data)
        if not raw_data or "tests" not in raw_data:
            return []

        return self.load_tool_test_cases_from_data(raw_data["tests"])

    def load_tool_test_cases_from_data(
        self, test_data: List[Dict[str, Any]]
    ) -> List["ToolTestCase"]:
        """Loads tool tests from a list of dictionaries."""
        # Pre-process data to handle VariableDeclaration objects
        cleaned_data = []
        for case in test_data:
            case_copy = case.copy()
            if "variables" in case_copy and isinstance(
                case_copy["variables"], dict
            ):
                cleaned_vars = {}
                for k, v in case_copy["variables"].items():
                    cleaned_vars[k] = Variables.variable_to_dict(v)
                case_copy["variables"] = cleaned_vars
            cleaned_data.append(case_copy)

        adapter = TypeAdapter(List[ToolTestCase])
        return adapter.validate_python(cleaned_data)

    def validate_tool_test(
        self,
        test_case: "ToolTestCase",
        tool_response: Any,
    ) -> List[str]:
        """Validates the tool response and variables against expectations.

        Returns:
            List of error messages. Empty list if all expectations pass.
        """
        updated_variables = {}
        if isinstance(tool_response, dict) and "variables" in tool_response:
            updated_variables = tool_response["variables"]

        errors = []
        # Validate response
        for exp in test_case.response_expectations:
            resp_data = (
                tool_response.get("response")
                if isinstance(tool_response, dict)
                else tool_response
            )
            actual_value = self._get_value_at_path(resp_data, exp.path)
            if not self._check_expectation(actual_value, exp):
                errors.append(
                    f"Response expectation failed: path='{exp.path}',"
                    f" actual='{actual_value}', expected='{exp.value}',"
                    f" operator='{exp.operator}'"
                )

        # Validate variables
        for exp in test_case.variable_expectations:
            actual_value = self._get_value_at_path(updated_variables, exp.path)
            if not self._check_expectation(actual_value, exp):
                errors.append(
                    f"Variable expectation failed: path='{exp.path}',"
                    f" actual='{actual_value}', expected='{exp.value}',"
                    f" operator='{exp.operator}'"
                )

        return errors

    def run_tool_tests(
        self, test_cases: List["ToolTestCase"], debug: bool = False
    ) -> pd.DataFrame:
        """Runs a list of tool tests.

        Returns:
            A pandas DataFrame of results with status and errors.
        """
        # Fetch and unwrap app variables once
        raw_app_vars = self.var_client.list_variables()
        app_vars_cache = {}
        for var in raw_app_vars:
            try:
                var_dict = MessageToDict(var._pb)
            except AttributeError:
                var_dict = MessageToDict(var)

            schema = var_dict.get("schema", {})
            actual_data = schema.get("default") or var_dict.get("value") or {}
            app_vars_cache[var.name] = actual_data

        # Fetch app metadata and user info once per run
        app_client = Apps(
            project_id=self.project_id, location=self.location, creds=self.creds
        )
        app = app_client.get_app(self.app_name)
        app_display_name = app.display_name if app else "Unknown App"
        tester_email = getattr(self.creds, "service_account_email", "Unknown")

        results = []
        for test_case in test_cases:
            print(f"Running test: {test_case.name} ({test_case.tool})")

            tool_id = self.tool_map.get(test_case.tool)
            if not tool_id:
                error = f"Tool '{test_case.tool}' not found in app."
                print(f"FAILED: {error}")
                results.append(
                    {
                        "test": test_case.name,
                        "tool": test_case.tool,
                        "status": "FAILED",
                        "latency (ms)": 0.0,
                        "app_display_name": app_display_name,
                        "tester": tester_email,
                        "errors": [error],
                    }
                )
                continue

            if "toolsets/" in tool_id and test_case.context:
                error = "Context can only be specified for python tools."
                print(f"FAILED: {error}")
                results.append(
                    {
                        "test": test_case.name,
                        "tool": test_case.tool,
                        "status": "FAILED",
                        "latency (ms)": 0.0,
                        "app_display_name": app_display_name,
                        "tester": tester_email,
                        "errors": [error],
                    }
                )
                continue

            # Filter and merge variables for this specific test case
            final_variables = {}
            for var_name, custom_val in test_case.variables.items():
                if custom_val is None:
                    # User requested an existing app variable by name
                    if var_name in app_vars_cache:
                        final_variables[var_name] = app_vars_cache[var_name]
                    else:
                        print(
                            f"[WARNING] App variable '{var_name}' requested "
                            f"but not found in app."
                        )
                else:
                    # User provided their own custom mock data
                    final_variables[var_name] = custom_val

            latency_ms = 0.0
            tool_response = None
            try:
                if debug:
                    print(f"[DEBUG] Executing tool: {test_case.tool}")
                    print(f"[DEBUG] Tool ID: {tool_id}")
                    print(f"[DEBUG] Args: {test_case.args}")
                    print(f"[DEBUG] Variables: {final_variables}")

                start_time = time.perf_counter()
                tool_response = self.tools_client.execute_tool(
                    tool_display_name=test_case.tool,
                    args=test_case.args,
                    variables=final_variables,
                    context=test_case.context,
                )
                end_time = time.perf_counter()
                latency_ms = (end_time - start_time) * 1000

                if debug:
                    print(f"[DEBUG] Tool Response: {tool_response}")

                errors = self.validate_tool_test(test_case, tool_response)
                status = "PASSED"
                if errors:
                    status = "FAILED"

                print(f"{status}: {test_case.tool} --> {test_case.name}")
                if errors:
                    print(errors)

                results.append(
                    {
                        "test": test_case.name,
                        "tool": test_case.tool,
                        "status": status,
                        "latency (ms)": latency_ms,
                        "app_display_name": app_display_name,
                        "tester": tester_email,
                        "errors": errors,
                        "response": tool_response,
                    }
                )

            except Exception as e:
                # Catch *all* exceptions so the entire test loop doesn't fail
                print(f"ERROR: Exception occurred during test execution: {e}")
                results.append(
                    {
                        "test": test_case.name,
                        "tool": test_case.tool,
                        "status": "ERROR",
                        "latency (ms)": latency_ms,
                        "app_display_name": app_display_name,
                        "tester": tester_email,
                        "errors": [str(e)],
                        "response": tool_response,
                    }
                )

            print("-" * 30)

        return self.tool_tests_to_dataframe(results)

    def generate_tool_tests(
        self,
        target_dir: str = "tool_tests",
        include_tools: Optional[List[str]] = None,
        exclude_tools: Optional[List[str]] = None,
        overwrite: bool = False,
        mine_tool_data: bool = False,
        mine_conversations_limit: int = 50,
    ) -> None:
        """Generates configurable YAML test templates for tools defined in
        the app.

        Parses the application's OpenAPI tool schemas or Python underlying
        functions to try and intelligently scaffold the request arguments and
        expected responses.

        Args:
            target_dir: The directory path where the generated YAML files will
                be saved. Defaults to 'tool_tests'.
            include_tools: An optional list of tool display names to restrict
                the generation. If None, all tools in the app are evaluated.
            exclude_tools: An optional list of tool display names (or prefixes)
                to exclude from generation. Matches if a tool's display name
                starts with any string in this list.
            overwrite: If True, existing YAML test templates in the target
                directory will be overwritten. If False, existing files are
                skipped.
            mine_tool_data: If True, queries recent conversations to populate
                generated tests with real tool payload arguments.
            mine_conversations_limit: The maximum number of conversations to
                scan when mining real tool arguments.
        """
        os.makedirs(target_dir, exist_ok=True)

        mined_data = {}
        if mine_tool_data:
            logger.info("Mining tool data from recent conversations...")
            mined_data = self._mine_tool_data(mine_conversations_limit)

        for display_name, tool_id in self.tool_map.items():
            if include_tools and display_name not in include_tools:
                continue

            if exclude_tools and any(
                display_name.startswith(ex) for ex in exclude_tools
            ):
                continue

            template_args = {}
            expected_returns = []
            # Try to build template args based on schema
            try:
                actual_tool_id = tool_id
                if "toolsets/" in tool_id and "/tools/" in tool_id:
                    # For tools inside a toolset, we need the toolset object
                    # to get the schema
                    actual_tool_id, _ = tool_id.split("/tools/")

                tool_obj = self.tools_client.get_tool(actual_tool_id)
                tool_dict = (
                    type(tool_obj).to_dict(tool_obj)
                    if not isinstance(tool_obj, dict)
                    else tool_obj
                )

                # Handle Python Tools
                if "toolsets/" not in tool_id:
                    if "python_function" in tool_dict:
                        template_args, expected_returns = (
                            self._parse_python_function(tool_dict)
                        )
                    elif not any(
                        key in tool_dict
                        for key in (
                            "data_store_spec",
                            "data_store_tool",
                            "google_search_tool",
                        )
                    ):
                        logger.info(
                            f"Skipping test generation for '{display_name}' "
                            f"as it lacks a supported server-side execution "
                            f"implementation."
                        )
                        continue

                # Handle OpenAPI Toolsets
                else:
                    template_args, _ = self._parse_openapi_toolset(
                        tool_dict, display_name
                    )

            except Exception as e:
                logger.warning(
                    f"Could not fetch tool schema for {display_name}: {e}"
                )

            self._write_tool_test_template(
                display_name,
                template_args,
                expected_returns,
                target_dir,
                overwrite,
                mined_data.get(display_name),
            )

    def tool_tests_to_dataframe(
        self, results: List[Dict[str, Any]]
    ) -> pd.DataFrame:
        """Converts tool test results to a pandas DataFrame for reporting."""
        rows = []
        for res in results:
            errors = res.get("errors", [])
            error_str = "; ".join(errors) if errors else ""
            rows.append(
                {
                    "test_name": res.get("test"),
                    "tool": res.get("tool"),
                    "status": res.get("status"),
                    "latency (ms)": res.get("latency (ms)", 0.0),
                    "app_display_name": res.get(
                        "app_display_name", "Unknown App"
                    ),
                    "tester": res.get("tester", "Unknown"),
                    "errors": error_str,
                }
            )
        return pd.DataFrame(rows)

    @staticmethod
    def _calculate_stats(df: pd.DataFrame) -> SummaryStats:
        """Calculates summary statistics from a tool evals dataframe."""
        total = len(df)
        if total == 0:
            return SummaryStats(
                test_run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                total_tests=0,
                pass_count=0,
                pass_rate=0.0,
                agent_name="Unknown App",
                tester="Unknown",
                p50_latency_ms=0.0,
                p90_latency_ms=0.0,
                p99_latency_ms=0.0,
            )

        pass_count = (df["status"] == "PASSED").sum()
        pass_rate = pass_count / total if total > 0 else 0.0

        latencies = df["latency (ms)"].to_numpy()

        try:
            p50 = pd.Series(latencies).quantile(0.50)
            p90 = pd.Series(latencies).quantile(0.90)
            p99 = pd.Series(latencies).quantile(0.99)
        except Exception:
            p50 = p90 = p99 = 0.0

        # Try to pull the first known app_display_name/tester from the DataFrame
        agent_name = "Unknown App"
        if "app_display_name" in df.columns and not df.empty:
            agent_name = df["app_display_name"].iloc[0]

        tester = "Unknown"
        if "tester" in df.columns and not df.empty:
            tester = df["tester"].iloc[0]

        return SummaryStats(
            test_run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            total_tests=total,
            pass_count=pass_count,
            pass_rate=pass_rate,
            agent_name=agent_name,
            tester=tester,
            p50_latency_ms=float(p50),
            p90_latency_ms=float(p90),
            p99_latency_ms=float(p99),
        )

    @staticmethod
    def generate_report(results_df: pd.DataFrame) -> pd.DataFrame:
        """
        Generates a summary report DataFrame capturing key metrics from tool
        evaluation results.
        """
        stats = ToolEvals._calculate_stats(results_df)

        report_data = {
            col: getattr(stats, col) for col in SUMMARY_SCHEMA_COLUMNS
        }
        return pd.DataFrame([report_data])


class ToolTestCase(BaseModel):
    """Data model for a tool test case."""

    name: str
    tool: str

    # We wrap the type in Annotated to add the BeforeValidator
    args: Annotated[Dict[str, Any], BeforeValidator(Common.empty_to_dict)] = (
        Field(
            default_factory=dict, validation_alias=AliasChoices("args", "agrs")
        )
    )

    variables: Annotated[
        Dict[str, Any], BeforeValidator(ToolEvals._parse_dict_input)
    ] = Field(default_factory=dict)

    context: Annotated[
        Dict[str, Any], BeforeValidator(ToolEvals._parse_dict_input)
    ] = Field(default_factory=dict)

    response_expectations: Annotated[
        List[Expectation], BeforeValidator(Common.empty_to_list)
    ] = Field(
        default_factory=list,
        validation_alias=AliasPath("expectations", "response"),
    )

    variable_expectations: Annotated[
        List[Expectation], BeforeValidator(Common.empty_to_list)
    ] = Field(
        default_factory=list,
        validation_alias=AliasPath("expectations", "variables"),
    )

    @model_validator(mode="after")
    def validate_variables_and_context(self) -> "ToolTestCase":
        if self.variables and self.context:
            raise ValueError(
                "A test case can provide either 'variables' or 'context', "
                "but not both."
            )
        return self
