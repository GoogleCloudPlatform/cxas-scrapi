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
import asyncio
import concurrent.futures
import enum
import inspect
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, NamedTuple

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
from cxas_scrapi.utils.callback_libs import ToolContext

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
    value: Any | None = None


class _OfflineMockToolsDispatcher:
    """Mock tools dispatcher supporting Xbox v2 positional dict calls
    `tools.<name>({...})` in offline tool tests.
    """

    def __init__(
        self,
        mock_tools: dict[str, Any] | None = None,
        local_executors: dict[str, Any] | None = None,
    ) -> None:
        self._mock_tools = dict(mock_tools or {})
        self._local_executors = dict(local_executors or {})

    def __getattr__(self, name: str) -> Any:
        def _invoke(*pos_args: Any, **kwargs: Any) -> Any:
            if kwargs:
                raise TypeError(
                    f"Tool '{name}' in CES Xbox v2 does not accept keyword "
                    "arguments; pass a single positional dict argument "
                    f"tools.{name}({{...}})."
                )
            if len(pos_args) > 1:
                raise TypeError(
                    f"Tool '{name}' takes at most 1 positional dict argument "
                    f"({len(pos_args)} given)."
                )
            args = pos_args[0] if pos_args else None
            call_args: dict[str, Any] = {}
            if isinstance(args, dict):
                call_args.update(args)
            elif args is not None:
                raise TypeError(
                    f"Tool '{name}' expects a single positional dict argument, "
                    f"got {type(args).__name__}."
                )

            if name in self._mock_tools:
                mock_val = self._mock_tools[name]
                if callable(mock_val):
                    return mock_val(call_args)
                if isinstance(mock_val, dict):
                    return dict(mock_val)
                return mock_val

            if name in self._local_executors:
                return self._local_executors[name](call_args)

            raise RuntimeError(
                f"Tool '{name}' is not mocked and not found in local Python "
                "tools during offline execution."
            )

        return _invoke


class ToolEvals:
    """Utility class for testing CXAS Tools."""

    def __init__(
        self,
        app_name: str | None = None,
        creds: Any = None,
        user_agent_extension: str | None = None,
        app_dir: str | None = None,
        offline: bool = False,
    ) -> None:
        """Initializes the ToolEvals class.

        Args:
            app_name: Optional CXAS App name
                (projects/{project}/locations/{location}/apps/{app}).
            creds: Optional Google Cloud credentials.
            user_agent_extension: Optional user agent extension string.
            app_dir: Optional local app directory for offline tool execution.
            offline: If True (or when app_dir is provided without app_name),
                do not instantiate GCP clients.
        """
        self.app_name = app_name or ""
        self.app_dir = app_dir
        self.offline = bool(offline or (app_dir and not app_name))
        self.creds = creds
        self.user_agent_extension = user_agent_extension

        if self.offline:
            self.project_id = ""
            self.location = "us"
            self.tools_client = None  # type: ignore[assignment]
            self.var_client = None  # type: ignore[assignment]
            self.tool_map = {}
            return

        if not self.app_name:
            raise ValueError(
                "Either 'app_name' or 'app_dir' (with offline mode) must be "
                "provided."
            )

        parts = self.app_name.split("/")
        self.project_id = parts[1] if len(parts) > 1 else ""
        self.location = parts[3] if len(parts) > 3 else "us"

        self.tools_client = Tools(
            app_name=self.app_name,
            creds=self.creds,
            user_agent_extension=user_agent_extension,
        )
        self.var_client = Variables(
            app_name=self.app_name,
            creds=self.creds,
            user_agent_extension=user_agent_extension,
        )
        try:
            self.tool_map = self.tools_client.get_tools_map(reverse=True)
        except (AttributeError, KeyError, RuntimeError, ValueError) as e:
            logger.warning(
                "Failed to fetch tool map for %s: %s", self.app_name, e
            )
            self.tool_map = {}

    @staticmethod
    def _parse_dict_input(v: Any) -> dict[str, Any]:
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
                    if isinstance(sub_node, ast.Return):  # noqa: SIM102
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

    def _parse_python_function(self, tool_dict: dict) -> tuple[dict, list[str]]:
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
        self, tool_dict: dict, display_name: str
    ) -> tuple[dict, list[str]]:
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
        template_args: dict[str, Any],
        expected_returns: list[str],
        target_dir: str,
        overwrite: bool,
        example_data: dict[str, Any] | None = None,
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

    def _mine_tool_data(self, limit: int) -> dict[str, dict[str, Any]]:
        """Mines recent conversations for actual tool payloads to populate
        tests."""
        mined_data = {}
        try:
            history_client = ConversationHistory(
                app_name=self.app_name,
                creds=self.creds,
                user_agent_extension=self.user_agent_extension,
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
    ) -> list["ToolTestCase"]:
        """Loads tool tests from a YAML or JSON file."""
        with open(test_file_path, encoding="utf-8") as f:
            return self.load_tool_test_cases_from_yaml(f.read())

    def load_tool_tests_from_dir(
        self, directory_path: str = "tool_tests"
    ) -> list["ToolTestCase"]:
        """Recursively loads all YAML/JSON tool tests from a directory."""
        all_tests = []
        if not os.path.exists(directory_path):
            print(f"Directory {directory_path} does not exist.")
            return all_tests

        for root, _, files in os.walk(directory_path):
            for file in files:
                if file.endswith((".yaml", ".yml", ".json")):
                    file_path = os.path.join(root, file)
                    try:
                        tests = self.load_tool_test_cases_from_file(file_path)
                        all_tests.extend(tests)
                    except Exception as e:
                        logger.error(f"Error loading {file_path}: {e}")

        return all_tests

    def load_tool_test_cases_from_yaml(
        self, yaml_data: str
    ) -> list["ToolTestCase"]:
        """Loads tool tests from a YAML or JSON string."""
        raw_data = yaml.safe_load(yaml_data)
        if not raw_data:
            return []
        if isinstance(raw_data, list):
            return self.load_tool_test_cases_from_data(raw_data)
        if isinstance(raw_data, dict):
            for key in ("tests", "test_cases", "tool_tests"):
                if key in raw_data and isinstance(raw_data[key], list):
                    return self.load_tool_test_cases_from_data(raw_data[key])
        return []

    def load_tool_test_cases_from_data(
        self, test_data: list[dict[str, Any]]
    ) -> list["ToolTestCase"]:
        """Loads tool tests from a list of dictionaries."""
        # Pre-process data to handle VariableDeclaration objects and auto-names
        cleaned_data = []
        for idx, case in enumerate(test_data):
            if not isinstance(case, dict):
                continue
            case_copy = case.copy()
            if not any(k in case_copy for k in ("name", "test_name", "id")):
                tool_label = (
                    case_copy.get("tool")
                    or case_copy.get("tool_name")
                    or case_copy.get("display_name")
                    or "tool"
                )
                case_copy["name"] = f"{tool_label}_test_{idx + 1}"
            if "variables" in case_copy and isinstance(
                case_copy["variables"], dict
            ):
                cleaned_vars = {}
                for k, v in case_copy["variables"].items():
                    cleaned_vars[k] = Variables.variable_to_dict(v)
                case_copy["variables"] = cleaned_vars
            cleaned_data.append(case_copy)

        adapter = TypeAdapter(list[ToolTestCase])
        return adapter.validate_python(cleaned_data)

    def validate_tool_test(
        self,
        test_case: "ToolTestCase",
        tool_response: Any,
    ) -> list[str]:
        """Validates the tool response and variables against expectations.

        Returns:
            List of error messages. Empty list if all expectations pass.
        """
        updated_variables = {}
        if isinstance(tool_response, dict) and "variables" in tool_response:
            updated_variables = tool_response["variables"]

        errors = []
        resp_data = (
            tool_response.get("response")
            if isinstance(tool_response, dict) and "response" in tool_response
            else tool_response
        )

        # Validate path-based response expectations
        for exp in test_case.response_expectations:
            actual_value = self._get_value_at_path(resp_data, exp.path)
            if not self._check_expectation(actual_value, exp):
                errors.append(
                    f"Response expectation failed: path='{exp.path}',"
                    f" actual='{actual_value}', expected='{exp.value}',"
                    f" operator='{exp.operator}'"
                )

        # Validate direct expected_output / expected_response / expected dict
        if test_case.expected_output is not None:
            expected_out = test_case.expected_output
            if isinstance(expected_out, dict) and isinstance(resp_data, dict):
                for k, exp_val in expected_out.items():
                    act_val = resp_data.get(k)
                    if act_val != exp_val:
                        errors.append(
                            f"Expected output mismatch for key '{k}': "
                            f"actual='{act_val}', expected='{exp_val}'"
                        )
            elif resp_data != expected_out:
                errors.append(
                    f"Expected output mismatch: actual='{resp_data}', "
                    f"expected='{expected_out}'"
                )

        # Validate expected_status if specified
        if test_case.expected_status is not None:
            exp_status = str(test_case.expected_status).strip()
            actual_status = None
            if isinstance(resp_data, dict) and "status" in resp_data:
                actual_status = str(resp_data["status"]).strip()
            elif isinstance(tool_response, dict) and "status" in tool_response:
                actual_status = str(tool_response["status"]).strip()
            if (
                actual_status is not None
                and actual_status.upper() != exp_status.upper()
                and exp_status.upper() not in ("PASSED", "FAILED", "SKIPPED")
            ):
                errors.append(
                    f"Expected status mismatch: actual='{actual_status}', "
                    f"expected='{exp_status}'"
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

    @staticmethod
    def _discover_local_tools(app_dir: str) -> dict[str, dict[str, Any]]:
        """Discovers local tools and toolsets under <app_dir>/tools/ and
        <app_dir>/toolsets/, indexing by directory name, displayName, and
        pythonFunction.name.
        """
        app_path = Path(app_dir)
        catalog: dict[str, dict[str, Any]] = {}

        tools_root = app_path / "tools"
        if tools_root.exists() and tools_root.is_dir():
            for tool_dir in sorted(tools_root.iterdir()):
                if not tool_dir.is_dir():
                    continue
                tool_meta: dict[str, Any] = {}
                preferred_json = tool_dir / f"{tool_dir.name}.json"
                json_candidates = (
                    [preferred_json]
                    if preferred_json.exists()
                    else sorted(tool_dir.glob("*.json"))
                )
                for jf in json_candidates:
                    try:
                        with open(jf, encoding="utf-8") as f:
                            loaded = json.load(f)
                        if isinstance(loaded, dict):
                            tool_meta = loaded
                            break
                    except (OSError, json.JSONDecodeError):
                        continue

                code_candidates = [
                    tool_dir / "python_function" / "python_code.py",
                    tool_dir / "python_code.py",
                ]
                py_func_cfg = (
                    tool_meta.get("pythonFunction")
                    or tool_meta.get("python_function")
                    or {}
                )
                if isinstance(py_func_cfg, dict):
                    code_ref = py_func_cfg.get("pythonCode") or py_func_cfg.get(
                        "python_code"
                    )
                    if isinstance(code_ref, str) and code_ref.strip():
                        code_candidates.insert(0, app_path / code_ref)
                        code_candidates.insert(1, tool_dir / code_ref)

                code_path: Path | None = None
                for cand in code_candidates:
                    if cand.exists() and cand.is_file():
                        code_path = cand
                        break

                display_name = str(
                    tool_meta.get("displayName")
                    or tool_meta.get("display_name")
                    or tool_dir.name
                ).strip()
                py_func_name = ""
                if isinstance(py_func_cfg, dict):
                    py_func_name = str(py_func_cfg.get("name") or "").strip()

                info = {
                    "dir_name": tool_dir.name,
                    "display_name": display_name,
                    "python_function_name": py_func_name,
                    "code_path": code_path,
                    "is_python": code_path is not None,
                    "tool_meta": tool_meta,
                }
                for key in (tool_dir.name, display_name, py_func_name):
                    if key:
                        catalog[key] = info

        toolsets_root = app_path / "toolsets"
        if toolsets_root.exists() and toolsets_root.is_dir():
            for ts_dir in sorted(toolsets_root.iterdir()):
                if not ts_dir.is_dir():
                    continue
                ts_meta: dict[str, Any] = {}
                for jf in sorted(ts_dir.glob("*.json")):
                    try:
                        with open(jf, encoding="utf-8") as f:
                            loaded = json.load(f)
                        if isinstance(loaded, dict):
                            ts_meta = loaded
                            break
                    except (OSError, json.JSONDecodeError):
                        continue
                display_name = str(
                    ts_meta.get("displayName")
                    or ts_meta.get("display_name")
                    or ts_dir.name
                ).strip()
                info = {
                    "dir_name": ts_dir.name,
                    "display_name": display_name,
                    "python_function_name": "",
                    "code_path": None,
                    "is_python": False,
                    "tool_meta": ts_meta,
                }
                for key in (ts_dir.name, display_name):
                    if key and key not in catalog:
                        catalog[key] = info

        return catalog

    @staticmethod
    def _discover_local_app_metadata(
        app_dir: str,
    ) -> tuple[str, dict[str, Any]]:
        """Reads app display name and default variable values from app.json
        or app.yaml in app_dir.
        """
        app_path = Path(app_dir)
        app_display_name = app_path.name or "Local App"
        app_vars_cache: dict[str, Any] = {}

        for cfg_name in ("app.json", "app.yaml", "app.yml"):
            cfg_file = app_path / cfg_name
            if not cfg_file.exists() or not cfg_file.is_file():
                continue
            try:
                with open(cfg_file, encoding="utf-8") as f:
                    data = (
                        json.load(f)
                        if cfg_name.endswith(".json")
                        else yaml.safe_load(f)
                    )
                if not isinstance(data, dict):
                    continue
                disp = data.get("displayName") or data.get("display_name")
                if disp:
                    app_display_name = str(disp)
                var_decls = (
                    data.get("variableDeclarations")
                    or data.get("variable_declarations")
                    or []
                )
                if isinstance(var_decls, list):
                    for vd in var_decls:
                        if not isinstance(vd, dict):
                            continue
                        vname = vd.get("name")
                        schema = vd.get("schema") or {}
                        if vname:
                            app_vars_cache[str(vname)] = (
                                schema.get("default")
                                if isinstance(schema, dict)
                                and "default" in schema
                                else vd.get("value")
                            )
                break
            except Exception:
                continue

        return app_display_name, app_vars_cache

    @staticmethod
    def _execute_local_python_tool(
        code_path: Path,
        preferred_func_name: str,
        call_args: dict[str, Any],
        tool_context: ToolContext,
        tools_dispatcher: _OfflineMockToolsDispatcher,
    ) -> Any:
        """Executes a local Python tool function in an isolated namespace,
        supporting both sync and async functions and injecting `context` and
        `tools`.
        """
        with open(code_path, encoding="utf-8") as f:
            code_str = f.read()

        tree = ast.parse(code_str, filename=str(code_path))
        top_funcs = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if not top_funcs:
            raise ValueError(
                f"No top-level Python function found in {code_path}"
            )

        target_func_name = None
        if preferred_func_name and preferred_func_name in top_funcs:
            target_func_name = preferred_func_name
        else:
            public_funcs = [fn for fn in top_funcs if not fn.startswith("_")]
            target_func_name = public_funcs[0] if public_funcs else top_funcs[0]

        namespace: dict[str, Any] = {
            "__name__": f"cxas_offline_tool_{target_func_name}",
            "__file__": str(code_path),
            "context": tool_context,
            "tools": tools_dispatcher,
            "ToolContext": ToolContext,
        }
        compiled = compile(tree, filename=str(code_path), mode="exec")
        exec(compiled, namespace)

        fn = namespace.get(target_func_name)
        if not callable(fn):
            raise ValueError(
                f"Function '{target_func_name}' is not callable in {code_path}"
            )

        sig = inspect.signature(fn)
        params = sig.parameters
        accepts_var_kw = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
        bound_kwargs: dict[str, Any] = {}
        for k, v in (call_args or {}).items():
            if accepts_var_kw or k in params:
                bound_kwargs[k] = v

        # If function explicitly declares `context` or `tool_context` parameter
        # and it wasn't passed in args, pass tool_context
        if "context" in params and "context" not in bound_kwargs:
            bound_kwargs["context"] = tool_context
        if "tool_context" in params and "tool_context" not in bound_kwargs:
            bound_kwargs["tool_context"] = tool_context

        # Also support single-positional-dict style if function has 1 param
        # and call_args keys don't match param name
        non_default_pos = [
            p
            for p in params.values()
            if p.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
            and p.name not in ("context", "tool_context")
        ]
        if (
            not bound_kwargs
            and call_args
            and len(non_default_pos) == 1
            and non_default_pos[0].name not in call_args
        ):
            raw_result = fn(call_args)
        else:
            raw_result = fn(**bound_kwargs)

        if inspect.iscoroutine(raw_result):
            try:
                asyncio.get_running_loop()
                # Run in a separate thread if an event loop is already running
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=1
                ) as pool:
                    raw_result = pool.submit(asyncio.run, raw_result).result()
            except RuntimeError:
                raw_result = asyncio.run(raw_result)

        return raw_result

    def run_offline_tool_tests(
        self,
        test_cases: list["ToolTestCase"],
        app_dir: str | None = None,
        debug: bool = False,
    ) -> pd.DataFrame:
        """Runs tool tests completely offline against local Python function
        tools in `<app_dir>/tools/`.
        """
        target_app_dir = app_dir or self.app_dir
        if not target_app_dir:
            raise ValueError(
                "'app_dir' is required for offline tool test execution."
            )
        if not os.path.exists(target_app_dir):
            raise FileNotFoundError(
                f"App directory not found: {target_app_dir}"
            )

        catalog = self._discover_local_tools(target_app_dir)
        app_display_name, app_vars_cache = self._discover_local_app_metadata(
            target_app_dir
        )
        tester_email = "offline"

        results = []
        for test_case in test_cases:
            print(f"Running offline test: {test_case.name} ({test_case.tool})")

            tool_info = catalog.get(test_case.tool)
            # Also check if a prefix matches a known non-Python toolset
            if tool_info is None:
                for key, cand_info in catalog.items():
                    if not cand_info["is_python"] and test_case.tool.startswith(
                        f"{key}_"
                    ):
                        tool_info = cand_info
                        break

            # Build initial state from variables / state / context
            initial_state: dict[str, Any] = {}
            for var_name, custom_val in test_case.variables.items():
                if custom_val is None:
                    if var_name in app_vars_cache:
                        initial_state[var_name] = app_vars_cache[var_name]
                else:
                    initial_state[var_name] = custom_val

            if test_case.state:
                initial_state.update(test_case.state)

            context_dict: dict[str, Any] = {}
            if test_case.context:
                context_dict = dict(test_case.context)
                if isinstance(context_dict.get("state"), dict):
                    merged_state = dict(context_dict["state"])
                    merged_state.update(initial_state)
                    initial_state = merged_state
                elif isinstance(context_dict.get("variables"), dict):
                    merged_state = dict(context_dict["variables"])
                    merged_state.update(initial_state)
                    initial_state = merged_state
                else:
                    # Treat flat context keys as state variables if neither
                    # 'state' nor ToolContext fields are present
                    known_ctx_fields = set(ToolContext.model_fields.keys()) | {
                        "userContent",
                        "invocationId",
                        "agentName",
                        "sessionId",
                        "streamingStage",
                        "languageCode",
                        "projectId",
                        "projectNumber",
                        "appId",
                        "turnIndex",
                        "functionCallId",
                        "variables",
                    }
                    for ck, cv in context_dict.items():
                        if ck not in known_ctx_fields:
                            initial_state.setdefault(ck, cv)

            # Check if the target tool itself is mocked or non-Python
            if tool_info is None:
                if test_case.tool in test_case.mock_tools:
                    mock_val = test_case.mock_tools[test_case.tool]
                    resp_payload = (
                        mock_val(test_case.args)
                        if callable(mock_val)
                        else mock_val
                    )
                    tool_response = {
                        "response": resp_payload,
                        "variables": initial_state,
                    }
                    errors = self.validate_tool_test(test_case, tool_response)
                    status = "FAILED" if errors else "PASSED"
                    results.append(
                        {
                            "test": test_case.name,
                            "tool": test_case.tool,
                            "status": status,
                            "latency (ms)": 0.0,
                            "app_display_name": app_display_name,
                            "tester": tester_email,
                            "errors": errors,
                            "response": tool_response,
                        }
                    )
                    continue

                error = (
                    f"Tool '{test_case.tool}' not found in local app "
                    f"directory '{target_app_dir}'."
                )
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

            if not tool_info["is_python"] or tool_info["code_path"] is None:
                if test_case.tool in test_case.mock_tools:
                    mock_val = test_case.mock_tools[test_case.tool]
                    resp_payload = (
                        mock_val(test_case.args)
                        if callable(mock_val)
                        else mock_val
                    )
                    tool_response = {
                        "response": resp_payload,
                        "variables": initial_state,
                    }
                    errors = self.validate_tool_test(test_case, tool_response)
                    status = "FAILED" if errors else "PASSED"
                    results.append(
                        {
                            "test": test_case.name,
                            "tool": test_case.tool,
                            "status": status,
                            "latency (ms)": 0.0,
                            "app_display_name": app_display_name,
                            "tester": tester_email,
                            "errors": errors,
                            "response": tool_response,
                        }
                    )
                    continue

                skip_msg = (
                    f"SKIPPED: Tool '{test_case.tool}' is a non-Python tool "
                    "(no python_code.py found); offline execution applies to "
                    "Python function tools unless mocked via 'mock_tools'."
                )
                print(skip_msg)
                results.append(
                    {
                        "test": test_case.name,
                        "tool": test_case.tool,
                        "status": "SKIPPED",
                        "latency (ms)": 0.0,
                        "app_display_name": app_display_name,
                        "tester": tester_email,
                        "errors": [skip_msg],
                    }
                )
                continue

            tool_ctx_kwargs: dict[str, Any] = {
                k: v
                for k, v in context_dict.items()
                if k in ToolContext.model_fields
                or k
                in (
                    "userContent",
                    "invocationId",
                    "agentName",
                    "sessionId",
                    "streamingStage",
                    "languageCode",
                    "projectId",
                    "projectNumber",
                    "appId",
                    "turnIndex",
                    "functionCallId",
                )
            }
            tool_ctx_kwargs["state"] = initial_state
            tool_context = ToolContext(**tool_ctx_kwargs)

            # Build local executors for peer Python tools in case a tool calls
            # another local Python tool via `tools.<other_tool>({...})`
            local_executors: dict[str, Any] = {}
            dispatcher = _OfflineMockToolsDispatcher(
                mock_tools=test_case.mock_tools,
                local_executors=local_executors,
            )

            def _make_peer_executor(
                cp: Path,
                fn: str,
                ctx: ToolContext,
                disp: _OfflineMockToolsDispatcher,
            ) -> Any:
                return lambda a: self._execute_local_python_tool(
                    cp, fn, a, ctx, disp
                )

            for peer_key, peer_info in catalog.items():
                if peer_info["is_python"] and peer_info["code_path"]:
                    peer_code = peer_info["code_path"]
                    peer_fn_name = (
                        peer_info["python_function_name"]
                        or peer_info["dir_name"]
                    )
                    local_executors[peer_key] = _make_peer_executor(
                        peer_code, peer_fn_name, tool_context, dispatcher
                    )

            latency_ms = 0.0
            tool_response = None
            try:
                if debug:
                    print(f"[DEBUG] Offline executing tool: {test_case.tool}")
                    print(f"[DEBUG] Code path: {tool_info['code_path']}")
                    print(f"[DEBUG] Args: {test_case.args}")
                    print(f"[DEBUG] Initial state: {tool_context.state}")

                preferred_fn = (
                    tool_info["python_function_name"]
                    or tool_info["dir_name"]
                    or test_case.tool
                )
                start_time = time.perf_counter()
                raw_output = self._execute_local_python_tool(
                    code_path=tool_info["code_path"],
                    preferred_func_name=preferred_fn,
                    call_args=test_case.args,
                    tool_context=tool_context,
                    tools_dispatcher=dispatcher,
                )
                end_time = time.perf_counter()
                latency_ms = (end_time - start_time) * 1000

                merged_vars = dict(tool_context.state)
                if getattr(tool_context, "actions", None) and getattr(
                    tool_context.actions, "state_delta", None
                ):
                    merged_vars.update(tool_context.actions.state_delta)

                if (
                    isinstance(raw_output, dict)
                    and "response" in raw_output
                    and "variables" in raw_output
                ):
                    tool_response = raw_output
                elif isinstance(raw_output, dict):
                    tool_response = {
                        "response": raw_output,
                        "variables": merged_vars,
                    }
                else:
                    tool_response = {
                        "response": {"result": raw_output},
                        "variables": merged_vars,
                    }

                if debug:
                    print(f"[DEBUG] Tool Response: {tool_response}")

                errors = self.validate_tool_test(test_case, tool_response)
                status = "FAILED" if errors else "PASSED"

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

    def run_tool_tests(
        self,
        test_cases: list["ToolTestCase"],
        debug: bool = False,
        offline: bool | None = None,
        app_dir: str | None = None,
    ) -> pd.DataFrame:
        """Runs a list of tool tests (online against GCP or offline against
        local app_dir).

        Returns:
            A pandas DataFrame of results with status and errors.
        """
        use_offline = (
            bool(offline)
            if offline is not None
            else (self.offline or bool(app_dir and not self.app_name))
        )
        if use_offline:
            return self.run_offline_tool_tests(
                test_cases=test_cases,
                app_dir=app_dir or self.app_dir,
                debug=debug,
            )

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
            project_id=self.project_id,
            location=self.location,
            creds=self.creds,
            user_agent_extension=self.user_agent_extension,
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
        include_tools: list[str] | None = None,
        exclude_tools: list[str] | None = None,
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
        self, results: list[dict[str, Any]]
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

    name: str = Field(
        default="tool_test",
        validation_alias=AliasChoices("name", "test_name", "id"),
    )
    tool: str = Field(
        validation_alias=AliasChoices("tool", "tool_name", "display_name")
    )

    # We wrap the type in Annotated to add the BeforeValidator
    args: Annotated[dict[str, Any], BeforeValidator(Common.empty_to_dict)] = (
        Field(
            default_factory=dict,
            validation_alias=AliasChoices(
                "args", "agrs", "input", "inputs", "parameters"
            ),
        )
    )

    variables: Annotated[
        dict[str, Any], BeforeValidator(ToolEvals._parse_dict_input)
    ] = Field(default_factory=dict)

    state: Annotated[
        dict[str, Any], BeforeValidator(ToolEvals._parse_dict_input)
    ] = Field(default_factory=dict)

    context: Annotated[
        dict[str, Any], BeforeValidator(ToolEvals._parse_dict_input)
    ] = Field(default_factory=dict)

    mock_tools: Annotated[
        dict[str, Any], BeforeValidator(Common.empty_to_dict)
    ] = Field(default_factory=dict)

    expected_output: Any | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "expected_output", "expected_response", "expected"
        ),
    )

    expected_status: str | None = Field(default=None)

    response_expectations: Annotated[
        list[Expectation], BeforeValidator(Common.empty_to_list)
    ] = Field(
        default_factory=list,
        validation_alias=AliasPath("expectations", "response"),
    )

    variable_expectations: Annotated[
        list[Expectation], BeforeValidator(Common.empty_to_list)
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
