"""Single-turn evaluation utility for CXAS Agents.

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
"""

import enum
import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

import pandas as pd
import yaml
from google.protobuf.json_format import MessageToDict
from pydantic import BaseModel, Field, TypeAdapter, model_validator

from cxas_scrapi.core.sessions import Sessions
from cxas_scrapi.core.variables import Variables
from cxas_scrapi.utils.dependency_manager import SessionDependencyManager
from cxas_scrapi.utils.eval_utils import (
    ExpectationStatus,
    evaluate_expectations,
)
from cxas_scrapi.utils.gemini import GeminiGenerate

logger = logging.getLogger(__name__)


class HistoricalContextConfig(BaseModel):
    """Configuration for historical context, either a raw session ID, a test
    name, or explicit utterances."""

    session_id: Optional[str] = None
    test_name: Optional[str] = None
    utterances: Optional[List[Dict[str, Any]]] = None

    @model_validator(mode="after")
    def check_mutually_exclusive(self):
        fields = [self.session_id, self.test_name, self.utterances]
        non_none = [f for f in fields if f is not None]
        if len(non_none) > 1:
            raise ValueError(
                "session_id, test_name, and utterances are mutually exclusive"
            )
        if len(non_none) == 0:
            raise ValueError(
                "One of session_id, test_name, or utterances must be provided"
            )
        return self


class TurnOperator(str, enum.Enum):
    """Operators for testing single-turn expectations."""

    CONTAINS = "contains"
    EQUALS = "equals"
    TOOL_CALLED = "tool_called"
    TOOL_INPUT = "tool_input"
    TOOL_OUTPUT = "tool_output"
    NO_TOOLS_CALLED = "no_tools_called"
    AGENT_TRANSFER = "agent_transfer"


class TurnExpectation(BaseModel):
    """Data model for a single-turn expectation."""

    type: TurnOperator
    value: Optional[Any] = None


class TurnStep(BaseModel):
    """Data model for a single step inside a multi-turn conversation."""

    turn: str
    user: Optional[str] = None
    event: Optional[str] = None
    variables: Dict[str, Any] = Field(default_factory=dict)
    config: Dict[str, Any] = Field(default_factory=dict)
    expectations: list[TurnExpectation | str] = Field(default_factory=list)


class TurnTestCase(BaseModel):
    """Data model for a single-turn test case."""

    name: str
    tags: List[str] = Field(default_factory=list)
    user: Optional[str] = None
    event: Optional[str] = None
    variables: Dict[str, Any] = Field(default_factory=dict)
    historical_contexts: Optional[HistoricalContextConfig] = None
    turn_count: Optional[int] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    expectations: list[TurnExpectation | str] = Field(default_factory=list)
    turns: Optional[List[TurnStep]] = None


class DependencyResolutionError(Exception):
    """Exception raised when a test dependency cannot be resolved."""

    def __init__(self, message: str, skip_result: Dict[str, Any]):
        self.message = message
        self.skip_result = skip_result
        super().__init__(self.message)


class TurnEvals:
    """Class to manage and execute single-turn assertions on CXAS Agents."""

    def __init__(self, app_name: str, creds=None):
        """Initializes the TurnEvals class.

        Args:
            app_name: CXAS App Name
            creds: Optional Google Cloud credentials
        """
        self.app_name = app_name
        self.creds = creds
        self.sessions_client = Sessions(
            app_name=self.app_name, creds=self.creds
        )
        self.var_client = Variables(app_name=self.app_name, creds=self.creds)

        # Initialize GenAI Client
        project_id = app_name.split("/")[1]
        vertex_location = "global"

        self.genai_client = GeminiGenerate(
            project_id=project_id,
            location=vertex_location,
            credentials=self.creds,
        )

        # Initialize Test Dependency Manager
        self.dependency_manager = SessionDependencyManager()

    def load_turn_test_cases_from_file(
        self, test_file_path: str
    ) -> List[TurnTestCase]:
        """Loads turn tests from a YAML file."""
        with open(test_file_path, "r", encoding="utf-8") as f:
            return self.load_turn_test_cases_from_yaml(f.read())

    def load_turn_tests_from_dir(
        self, directory_path: str = "turn_tests"
    ) -> List[TurnTestCase]:
        """Recursively loads all YAML turn tests from a directory."""
        all_tests = []
        if not os.path.exists(directory_path):
            print(f"Directory {directory_path} does not exist.")
            return all_tests

        for root, _, files in os.walk(directory_path):
            for file in files:
                if file.endswith(".yaml") or file.endswith(".yml"):
                    file_path = os.path.join(root, file)
                    try:
                        tests = self.load_turn_test_cases_from_file(file_path)
                        all_tests.extend(tests)
                    except Exception as e:
                        logger.error(f"Error loading {file_path}: {e}")

        return all_tests

    def load_turn_test_cases_from_yaml(
        self, yaml_data: str
    ) -> List[TurnTestCase]:
        """Loads turn tests from a YAML string."""
        raw_data = yaml.safe_load(yaml_data)
        if not raw_data:
            return []
        # Support both 'conversations' and 'tests' formats
        raw_tests = raw_data.get("conversations", raw_data.get("tests", []))
        if not raw_tests:
            return []

        # Map 'conversation' to 'name' for Pydantic validation if needed
        for t in raw_tests:
            if "conversation" in t and "name" not in t:
                t["name"] = t["conversation"]

        global_config = raw_data.get("config", {})
        adapter = TypeAdapter(List[TurnTestCase])
        tests = adapter.validate_python(raw_tests)

        for t in tests:
            merged = global_config.copy()
            merged.update(t.config)
            t.config = merged
            if t.turns:
                for step in t.turns:
                    step_merged = merged.copy()
                    step_merged.update(step.config)
                    step.config = step_merged

        return tests

    def _check_dict_subset(self, subset: dict, superset: dict) -> bool:
        """Checks if all key-value pairs in subset exist exactly in superset.
        Supports {} as a wildcard meaning 'the key must exist, but values do
        not matter'.
        """

        for k, v in subset.items():
            if k not in superset:
                return False
            # {} acts as a wildcard asserting exists
            if isinstance(v, dict) and not v:
                continue

            super_val = superset[k]

            # If expected is a dict but actual is a JSON string, try to parse
            # the actual
            if isinstance(v, dict) and isinstance(super_val, str):
                try:
                    super_val = json.loads(super_val)
                except json.JSONDecodeError:
                    pass

            # Recursive check if both are dicts
            if isinstance(v, dict) and isinstance(super_val, dict):
                if not self._check_dict_subset(v, super_val):
                    return False
            elif super_val != v:
                if str(super_val) != str(v):
                    return False
        return True

    def _extract_tools_from_span(
        self,
        span: Dict[str, Any],
        called_tools: List[str],
        tool_inputs: Dict[str, Any],
        tool_outputs: Dict[str, Any],
    ):
        """Recursively extract tool calls from a span and its children."""
        if span.get("name") == "Tool":
            attrs = span.get("attributes", {})
            tool_name = attrs.get("name", "")
            if tool_name:
                if tool_name not in called_tools:
                    called_tools.append(tool_name)

                if "args" in attrs and tool_name not in tool_inputs:
                    tool_inputs[tool_name] = attrs["args"]
                if "response" in attrs and tool_name not in tool_outputs:
                    tool_outputs[tool_name] = attrs["response"]

        for child in span.get("childSpans", []):
            self._extract_tools_from_span(
                child, called_tools, tool_inputs, tool_outputs
            )

    def _extract_signals(self, turn_response: Any) -> Dict[str, Any]:
        """Extracts text, tools, and transfers from a turn response."""

        try:
            resp_dict = MessageToDict(turn_response._pb)
        except AttributeError:
            resp_dict = (
                MessageToDict(turn_response)
                if hasattr(turn_response, "DESCRIPTOR")
                else (turn_response if isinstance(turn_response, dict) else {})
            )

        outputs = resp_dict.get("outputs", [])

        # Aggregate text, tools, and transfers
        full_text = ""
        called_tools = []
        tool_inputs = {}
        tool_outputs = {}
        target_agent = ""

        # Some payloads might be simpler dicts depending on the cxas core
        if not outputs and "text" in resp_dict:
            full_text = str(resp_dict["text"])

            # Fallback for toolCalls in simple dicts
            tcs_msg = resp_dict.get("toolCalls", {})
            for tc in tcs_msg.get("toolCalls", []):
                tool_name = tc.get("displayName", tc.get("tool", ""))
                if tool_name and tool_name not in called_tools:
                    called_tools.append(tool_name)
                if tool_name not in tool_inputs:
                    tool_inputs[tool_name] = tc.get("args", {})

        def add_snippet(snippet: str):
            nonlocal full_text
            snippet = str(snippet).strip()
            if not snippet:
                return
            if snippet not in full_text:
                if full_text and not full_text.endswith(" "):
                    full_text += " "
                full_text += snippet

        for out in outputs:
            # only collect the raw output text for this turn, avoiding trace
            # history
            if "text" in out:
                add_snippet(out["text"])

            diag = out.get("diagnosticInfo", {})
            messages = diag.get("messages", [])

            # Extract any nested tools from rootSpan
            root_span = diag.get("rootSpan", {})
            if root_span:
                self._extract_tools_from_span(
                    root_span, called_tools, tool_inputs, tool_outputs
                )

            for msg in messages:
                if msg.get("role") == "user":
                    continue

                for chunk in msg.get("chunks", []):
                    if "text" in chunk:
                        add_snippet(chunk["text"])
                    if "transcript" in chunk:
                        add_snippet(chunk["transcript"])
                    if "toolCall" in chunk:
                        tc = chunk["toolCall"]
                        tool_name = tc.get("displayName", tc.get("tool", ""))
                        if tool_name and tool_name not in called_tools:
                            called_tools.append(tool_name)
                        if tool_name not in tool_inputs:
                            tool_inputs[tool_name] = tc.get("args", {})
                    if "toolResponse" in chunk:
                        tr = chunk["toolResponse"]
                        tool_name = tr.get("displayName", tr.get("tool", ""))
                        if tool_name not in tool_outputs:
                            tool_outputs[tool_name] = tr.get("response", {})
                    if "agentTransfer" in chunk:
                        at = chunk["agentTransfer"]
                        target_agent = at.get("displayName", "")
                        if not target_agent:
                            agent_str = at.get(
                                "agent", at.get("targetAgent", "")
                            )
                            target_agent = (
                                agent_str.split("/")[-1]
                                if "/" in agent_str
                                else agent_str
                            )

            # Fallback to high-level outputs if no diagnostic trace is available
            if not messages:
                if "text" in out:
                    full_text += str(out["text"]) + " "
                # Check top-level toolCalls and agentTransfers
                tcs_msg = out.get("toolCalls", {})
                for tc in tcs_msg.get("toolCalls", []):
                    tool_name = tc.get("displayName", tc.get("tool", ""))
                    if tool_name and tool_name not in called_tools:
                        called_tools.append(tool_name)
                    if tool_name not in tool_inputs:
                        tool_inputs[tool_name] = tc.get("args", {})

        return {
            "full_text": full_text,
            "called_tools": called_tools,
            "tool_inputs": tool_inputs,
            "tool_outputs": tool_outputs,
            "target_agent": target_agent,
        }

    def _resolve_historical_context(self, case: TurnTestCase) -> Optional[Any]:
        """Resolves historical context for a test case.

        Args:
            case: The test case containing historical context config.

        Returns:
            The resolved history (session ID, utterances, or None).

        Raises:
            DependencyResolutionError: If a dependency cannot be resolved.
        """
        if not case.historical_contexts:
            return None

        if case.historical_contexts.session_id:
            return case.historical_contexts.session_id

        if case.historical_contexts.utterances:
            return case.historical_contexts.utterances

        if case.historical_contexts.test_name:
            test_name = case.historical_contexts.test_name
            print(f"Resolving dependency: {test_name}")
            cached_id = self.dependency_manager.resolve_session_id(test_name)
            if cached_id:
                print(f"Using cached session ID: {cached_id}")
                return cached_id

            print(f"Dependency {test_name} not found in cache.")
            skip_result = {
                "test_name": case.name,
                "turn": "",
                "user": "",
                "status": "SKIPPED",
                "errors": f"Missing or failed dependency: {test_name}",
                "expected": "",
                "actual": "",
                "session_id": "",
                "llm_results": "",
            }
            raise DependencyResolutionError(
                f"Failed to resolve {test_name}", skip_result
            )

        return None

    def validate_turn_test(self, test_case: Any, turn_response: Any):
        """Validates the turn response against defined expectations."""

        signals = self._extract_signals(turn_response)
        full_text = signals["full_text"]
        called_tools = signals["called_tools"]
        tool_inputs = signals["tool_inputs"]
        tool_outputs = signals["tool_outputs"]
        target_agent = signals["target_agent"]

        results = []
        llm_expectations = []
        for exp in test_case.expectations:
            if isinstance(exp, str):
                llm_expectations.append(exp)
                continue

            op = exp.type
            expected = exp.value

            # Rule-based evaluation
            status = "SUCCESS"
            justification = ""
            actual = ""

            if op == TurnOperator.EQUALS:
                actual = full_text.strip()
                if actual != str(expected).strip():
                    status = "FAILURE"
                    justification = (
                        f"EQUALS failed: Expected '{expected}', Got '{actual}'"
                    )
            elif op == TurnOperator.CONTAINS:
                actual = full_text.strip()
                if str(expected) not in actual:
                    status = "FAILURE"
                    justification = (
                        f"CONTAINS failed: '{expected}' not found in '{actual}'"
                    )
            elif op == TurnOperator.TOOL_CALLED:
                actual = str(called_tools)
                found = any(
                    expected == t or t.endswith(expected) for t in called_tools
                )
                if not found:
                    status = "FAILURE"
                    justification = (
                        f"TOOL_CALLED failed: Expected tool '{expected}' was "
                        f"not called. Tools called: {called_tools}"
                    )
            elif op == TurnOperator.NO_TOOLS_CALLED:
                actual = str(called_tools)
                if called_tools:
                    status = "FAILURE"
                    justification = (
                        f"NO_TOOLS_CALLED failed: Tools were called: "
                        f"{called_tools}"
                    )
            elif op == TurnOperator.AGENT_TRANSFER:
                actual = target_agent
                if actual != expected and not actual.endswith(expected):
                    status = "FAILURE"
                    justification = (
                        f"AGENT_TRANSFER failed: Expected transfer to "
                        f"'{expected}', actually transferred to '{actual}'"
                    )
            elif op == TurnOperator.TOOL_INPUT:
                actual = str(tool_inputs)
                if not isinstance(expected, dict):
                    status = "FAILURE"
                    justification = (
                        "TOOL_INPUT failed: expectation value must be a "
                        "dictionary."
                    )
                # 1) Try matching against the top-level tool_inputs
                # container
                elif self._check_dict_subset(expected, tool_inputs):
                    pass
                else:
                    # 2) Fallback to checking nested argument dicts for
                    # any tool
                    match_found = False
                    for _t_name, t_args in tool_inputs.items():
                        if self._check_dict_subset(expected, t_args):
                            match_found = True
                            break
                    if not match_found:
                        status = "FAILURE"
                    justification = (
                        f"TOOL_INPUT failed: No tool call contained "
                        f"matching arguments {expected}. Actual tool "
                        f"inputs: {tool_inputs}"
                    )
            elif op == TurnOperator.TOOL_OUTPUT:
                actual = str(tool_outputs)
                if not isinstance(expected, dict):
                    status = "FAILURE"
                    justification = (
                        "TOOL_OUTPUT failed: expectation value must be a "
                        "dictionary."
                    )
                # 1) Try matching against the top-level tool_outputs
                # container
                elif self._check_dict_subset(expected, tool_outputs):
                    pass
                else:
                    # 2) Fallback to checking nested response dicts for
                    # any tool
                    match_found = False
                    for _t_name, t_resp in tool_outputs.items():
                        if self._check_dict_subset(expected, t_resp):
                            match_found = True
                            break
                    if not match_found:
                        status = "FAILURE"
                    justification = (
                        f"TOOL_OUTPUT failed: No tool response "
                        f"contained matching outputs {expected}. "
                        f"Actual tool outputs: {tool_outputs}"
                    )

            results.append(
                {
                    "expectation": f"{op.name}: {expected}",
                    "status": status,
                    "expected": str(expected),
                    "actual": actual,
                    "justification": justification,
                }
            )

        if llm_expectations:
            # Build trace
            trace_chunks = []
            user_input = getattr(test_case, "user", "") or getattr(
                test_case, "event", ""
            )
            trace_chunks.append(f"User: {user_input}")
            trace_chunks.append(f"Agent Text: {full_text.strip()}")
            for tool in called_tools:
                trace_chunks.append(
                    f"Tool Call: {tool} with args {tool_inputs.get(tool, {})}"
                )
                trace_chunks.append(
                    f"Tool Response: {tool} with result "
                    f"{tool_outputs.get(tool, {})}"
                )
            if target_agent:
                trace_chunks.append(f"Agent Transfer: {target_agent}")

            model_name = test_case.config.get(
                "gemini_model", "gemini-3.1-flash-lite-preview"
            )

            llm_results = evaluate_expectations(
                gemini_client=self.genai_client,
                model_name=model_name,
                trace=trace_chunks,
                expectations=llm_expectations,
            )

            for r in llm_results:
                results.append(
                    {
                        "expectation": r.expectation,
                        "status": (
                            "SUCCESS"
                            if r.status == ExpectationStatus.MET
                            else "FAILURE"
                        ),
                        "expected": "",
                        "actual": "",
                        "justification": r.justification,
                    }
                )

        return results

    def _topological_sort(
        self, cases: List[TurnTestCase]
    ) -> List[TurnTestCase]:
        """Sorts test cases topologically based on their dependencies.

        Args:
            cases: The list of test cases to sort.

        Returns:
            A list of test cases in dependency order (parents before children).

        Raises:
            ValueError: If a circular dependency is detected.
        """
        name_to_case = {case.name: case for case in cases}
        adj = {case.name: [] for case in cases}
        for case in cases:
            if case.historical_contexts and case.historical_contexts.test_name:
                dependency = case.historical_contexts.test_name
                if dependency in name_to_case:
                    adj[dependency].append(case.name)

        visited = {name: 0 for name in name_to_case}
        result = []

        def dfs(u):
            visited[u] = 1
            for v in adj[u]:
                if visited[v] == 1:
                    raise ValueError(
                        f"Circular dependency detected involving {u} and {v}"
                    )
                if visited[v] == 0:
                    dfs(v)
            visited[u] = 2
            result.append(name_to_case[u])

        for name in name_to_case:
            if visited[name] == 0:
                dfs(name)

        return result[::-1]

    def run_turn_tests(
        self,
        test_cases: List[TurnTestCase],
        debug: bool = False,
        session_id_prefix: str = "turn_eval_",
    ) -> pd.DataFrame:
        """Runs a list of single-turn tests. Every test runs in a brand new
        session."""

        results = []

        # Sort test cases topologically to handle dependencies
        try:
            sorted_cases = self._topological_sort(test_cases)
        except ValueError as e:
            print(f"Error in dependency resolution: {e}")
            raise

        for case in sorted_cases:
            print(f"Running Turn Test: {case.name}")

            try:
                resolved_history = self._resolve_historical_context(case)
            except DependencyResolutionError as e:
                results.append(e.skip_result)
                continue

            # 1. Create a brand new session ID for true stateless execution
            test_session_id = f"{session_id_prefix}{uuid.uuid4().hex[:8]}"

            try:
                if case.turns:
                    # Multi-turn sequence
                    full_conversation_trace = []
                    for step in case.turns:
                        if debug:
                            input_str = (
                                step.user
                                if step.user
                                else f"<event>{step.event}</event>"
                            )
                            print(
                                f"[DEBUG] Step: {step.turn} | Input: "
                                f"{input_str}"
                            )
                            print(f"[DEBUG] Session ID: {test_session_id}")
                            print(f"[DEBUG] Variables: {step.variables}")

                        # Merge config
                        merged_config = case.config.copy()
                        merged_config.update(step.config)

                        turn_response = self.sessions_client.run(
                            session_id=test_session_id,
                            text=step.user,
                            event=step.event,
                            variables=step.variables,
                            historical_contexts=(
                                resolved_history
                                if step is case.turns[0]
                                else None
                            ),
                            **merged_config,
                        )

                        # Extract trace for this turn
                        signals = self._extract_signals(turn_response)
                        user_input = getattr(step, "user", "") or getattr(
                            step, "event", ""
                        )
                        full_conversation_trace.append(f"User: {user_input}")
                        full_conversation_trace.append(
                            f"Agent Text: {signals['full_text'].strip()}"
                        )
                        for tool in signals["called_tools"]:
                            full_conversation_trace.append(
                                f"Tool Call: {tool} with args "
                                f"{signals['tool_inputs'].get(tool, {})}"
                            )
                            full_conversation_trace.append(
                                f"Tool Response: {tool} with result "
                                f"{signals['tool_outputs'].get(tool, {})}"
                            )
                        if signals["target_agent"]:
                            full_conversation_trace.append(
                                f"Agent Transfer: {signals['target_agent']}"
                            )

                        eval_results = self.validate_turn_test(
                            step, turn_response
                        )

                        status = "SUCCESS"
                        if any(r["status"] == "FAILURE" for r in eval_results):
                            status = "FAILURE"

                        print(f"{status}: {case.name} - {step.turn}")
                        for r in eval_results:
                            if r["status"] == "FAILURE":
                                print(f"  - {r['justification']}")

                        if not eval_results:
                            results.append(
                                {
                                    "test_name": case.name,
                                    "turn": step.turn,
                                    "user": step.user or f"Event: {step.event}",
                                    "status": "SUCCESS",
                                    "errors": "",
                                    "expected": "",
                                    "actual": "",
                                    "session_id": test_session_id,
                                    "llm_results": "",
                                }
                            )
                        else:
                            for r in eval_results:
                                results.append(
                                    {
                                        "test_name": case.name,
                                        "turn": step.turn,
                                        "user": (
                                            step.user or f"Event: {step.event}"
                                        ),
                                        "status": r["status"],
                                        "errors": (
                                            r["justification"]
                                            if r["status"] == "FAILURE"
                                            else ""
                                        ),
                                        "expected": r["expected"],
                                        "actual": r["actual"],
                                        "session_id": test_session_id,
                                        "llm_results": (
                                            f"{r['expectation']}: "
                                            f"{r['status']} "
                                            f"({r['justification']})"
                                            if not r["expected"]
                                            else ""
                                        ),
                                    }
                                )

                        if status == "FAILURE":
                            print(
                                f"Aborting multi-turn sequence '{case.name}' "
                                f"due to failure at '{step.turn}'."
                            )
                            break
                        print("-" * 30)

                    # Conversation-level expectations
                    if case.expectations:
                        llm_expectations = [
                            exp
                            for exp in case.expectations
                            if isinstance(exp, str)
                        ]
                        if llm_expectations:
                            print(
                                f"Evaluating conversation-level expectations "
                                f"for: {case.name}"
                            )

                            model_name = case.config.get(
                                "gemini_model", "gemini-3.1-flash-lite-preview"
                            )

                            llm_results = evaluate_expectations(
                                gemini_client=self.genai_client,
                                model_name=model_name,
                                trace=full_conversation_trace,
                                expectations=llm_expectations,
                            )

                            for r in llm_results:
                                results.append(
                                    {
                                        "test_name": case.name,
                                        "turn": "CONVERSATION",
                                        "user": "",
                                        "status": (
                                            "SUCCESS"
                                            if r.status == ExpectationStatus.MET
                                            else "FAILURE"
                                        ),
                                        "errors": (
                                            r.justification
                                            if r.status != ExpectationStatus.MET
                                            else ""
                                        ),
                                        "expected": "",
                                        "actual": "",
                                        "session_id": test_session_id,
                                        "llm_results": (
                                            f"{r.expectation}: {r.status} "
                                            f"({r.justification})"
                                        ),
                                    }
                                )
                                status_str = (
                                    "SUCCESS"
                                    if r.status == ExpectationStatus.MET
                                    else "FAILURE"
                                )
                                print(
                                    f"{status_str}: Conversation-level - "
                                    f"{r.expectation}"
                                )
                                if r.status != ExpectationStatus.MET:
                                    print(f"  - {r.justification}")
                else:
                    # 2. Run the single turn
                    if debug:
                        input_str = (
                            case.user
                            if case.user
                            else f"<event>{case.event}</event>"
                        )
                        print(f"[DEBUG] Input: {input_str}")
                        print(f"[DEBUG] Session ID: {test_session_id}")
                        print(f"[DEBUG] Variables: {case.variables}")

                    turn_response = self.sessions_client.run(
                        session_id=test_session_id,
                        text=case.user,
                        event=case.event,
                        variables=case.variables,
                        historical_contexts=(
                            resolved_history if resolved_history else None
                        ),
                        turn_count=(
                            case.turn_count
                            if case.turn_count is not None
                            else None
                        ),
                        **case.config,
                    )

                    # 3. Validate expectations
                    eval_results = self.validate_turn_test(case, turn_response)

                    status = "SUCCESS"
                    if any(r["status"] == "FAILURE" for r in eval_results):
                        status = "FAILURE"

                    print(f"{status}: {case.name}")
                    for r in eval_results:
                        if r["status"] == "FAILURE":
                            print(f"  - {r['justification']}")

                    if not eval_results:
                        results.append(
                            {
                                "test_name": case.name,
                                "turn": "",
                                "user": case.user or f"Event: {case.event}",
                                "status": "SUCCESS",
                                "errors": "",
                                "expected": "",
                                "actual": "",
                                "session_id": test_session_id,
                                "llm_results": "",
                            }
                        )
                    else:
                        for r in eval_results:
                            results.append(
                                {
                                    "test_name": case.name,
                                    "turn": "",
                                    "user": case.user or f"Event: {case.event}",
                                    "status": r["status"],
                                    "errors": (
                                        r["justification"]
                                        if r["status"] == "FAILURE"
                                        else ""
                                    ),
                                    "expected": r["expected"],
                                    "actual": r["actual"],
                                    "session_id": test_session_id,
                                    "llm_results": (
                                        f"{r['expectation']}: {r['status']} "
                                        f"({r['justification']})"
                                        if not r["expected"]
                                        else ""
                                    ),
                                }
                            )

                # Cache session ID if all turns passed
                all_passed = True
                for r in results:
                    if r["test_name"] == case.name and r["status"] == "FAILURE":
                        all_passed = False
                        break

                if all_passed:
                    self.dependency_manager.cache_session_id(
                        case.name, test_session_id
                    )

            except Exception as e:
                print(f"FAILURE: Exception {e}")
                results.append(
                    {
                        "test_name": case.name,
                        "turn": "",
                        "user": case.user,
                        "status": "FAILURE",
                        "errors": str(e),
                        "expected": "",
                        "actual": "",
                        "session_id": test_session_id,
                    }
                )

            print("=" * 30)

        return pd.DataFrame(results)
