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

"""Utility functions for processing and running CXAS Guardrail Tests."""

import datetime
import json
import logging
import time
from typing import Annotated, Any, Dict, List, NamedTuple, Optional

import pandas as pd
from alive_progress import alive_it
from google.protobuf.json_format import MessageToDict
from pydantic import BaseModel, BeforeValidator, Field

from cxas_scrapi.core.agents import Agents
from cxas_scrapi.core.apps import Apps
from cxas_scrapi.core.sessions import Sessions
from cxas_scrapi.utils.eval_utils import EvalUtils

logger = logging.getLogger(__name__)


SUMMARY_SCHEMA_COLUMNS = [
    "test_run_timestamp",
    "test_type",
    "total_tests",
    "pass_count",
    "pass_rate",
    "p50_latency_ms",
    "p90_latency_ms",
    "p99_latency_ms",
    "agent_name",
    "model",
]


class SummaryStats(NamedTuple):
    """Container for evaluation summary statistics."""

    pass_count: int
    pass_rate: float
    total_tests: int
    p50_latency_ms: float
    p90_latency_ms: float
    p99_latency_ms: float
    agent_name: str
    model: str


class GuardrailTestCase(BaseModel):
    """Data model for a guardrail test case."""

    name: str = "Guardrail Test"
    user_input: str
    variables: Annotated[
        Dict[str, Any], BeforeValidator(EvalUtils.parse_variables_input)
    ] = Field(default_factory=dict)
    expected_guardrail_name: Optional[str] = None
    expected_guardrail_type: Optional[str] = None
    expected_parameters: Optional[str] = None


class GuardrailEvals:
    """Utility class for testing CXAS Guardrails."""

    def __init__(self, app_name: str, **kwargs):
        """Initializes the GuardrailEvals class.

        Args:
            app_name: CXAS App name
                (projects/{project}/locations/{location}/apps/{app}).
        """
        self.app_name = app_name
        self.kwargs = kwargs
        self.agents_client = Agents(app_name=self.app_name, **kwargs)

    def _get_project_id(self, name: str) -> str:
        """Extracts the project ID from a resource name."""
        try:
            return name.split("/")[1]
        except IndexError as e:
            raise ValueError(f"Invalid resource name format: {name}") from e

    def _get_location(self, name: str) -> str:
        """Extracts the location from a resource name."""
        try:
            return name.split("/")[3]
        except IndexError as e:
            raise ValueError(f"Invalid resource name format: {name}") from e

    def _search_span_dict(
        self, span_dict: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Recursively searches a trace span dictionary for a guardrail trigger.

        Looks for span attributes with keys 'name' and ('type', 'guardrailType',
        or 'guardrail_type'), which indicate a guardrail evaluation span.
        """
        if not isinstance(span_dict, dict):
            return None

        # Check if the current span represents a guardrail
        attrs = span_dict.get("attributes", {})
        if "name" in attrs and any(
            k in attrs for k in ("type", "guardrailType", "guardrail_type")
        ):
            return span_dict

        # Recurse into child spans
        child_spans = span_dict.get(
            "childSpans", span_dict.get("child_spans", [])
        )
        for child in child_spans:
            res = self._search_span_dict(child)
            if res:
                return res
        return None

    def get_agent_text_from_outputs(self, outputs: List[Any]) -> str:
        """Extracts the agent text from session response outputs."""
        texts = []
        for out in outputs:
            message = getattr(out, "message", None)
            if message and hasattr(message, "text"):
                texts.append(message.text)
        return " - ".join(texts)

    def run_guardrail_tests(
        self,
        df: pd.DataFrame,
        debug: bool = False,
        console_logging: bool = False,
    ) -> pd.DataFrame:
        """Runs guardrail evaluation tests from a pandas DataFrame.

        Args:
            df: Pandas DataFrame containing the test cases.
            debug: Whether to print debug information.
            console_logging: Whether to print a summarized output to console.

        Returns:
            A new pandas DataFrame with test results appended as columns.
        """
        # Validate that essential columns exist
        required_cols = ["user_input"]
        for col in required_cols:
            if col not in df.columns:
                raise ValueError(
                    f"Required column '{col}' not found in DataFrame."
                )

        sessions_client = Sessions(app_name=self.app_name, **self.kwargs)

        # Try to get the app display name and configured model
        app_display_name = "Unknown App"
        configured_model = "Unknown Model"
        try:
            apps_client = Apps(
                project_id=self._get_project_id(self.app_name),
                location=self._get_location(self.app_name),
                **self.kwargs,
            )
            app_obj = apps_client.get_app(self.app_name)
            app_display_name = app_obj.display_name

            # Default to the app model setting
            configured_model = app_obj.model_settings.model

            # Check if root agent overrides the app model setting
            root_agent = self.agents_client.get_agent(app_obj.root_agent)
            if (
                hasattr(root_agent, "model_settings")
                and root_agent.model_settings.model
            ):
                configured_model = root_agent.model_settings.model

        except (AttributeError, KeyError, RuntimeError, ValueError) as e:
            logger.warning(
                "Could not retrieve app display name or model for "
                f"{self.app_name}: {e}"
            )

        results = []
        for index, row in alive_it(
            df.iterrows(), total=len(df), title="Running Guardrail Tests"
        ):
            # Replace NaNs with None for Pydantic validation
            row_dict = {
                k: (v if pd.notna(v) else None)
                for k, v in row.to_dict().items()
            }

            # Use test_id for name if available
            if "test_id" in row_dict and row_dict["test_id"]:
                row_dict["name"] = str(row_dict["test_id"])
            elif "name" not in row_dict or not row_dict["name"]:
                row_dict["name"] = f"Test_{index}"

            try:
                test_case = GuardrailTestCase(**row_dict)
            except (TypeError, ValueError) as e:
                logger.error(
                    f"Failed to parse row {index} into GuardrailTestCase: {e}"
                )
                results.append({"pass": False, "error": str(e)})
                continue

            if debug:
                print(f"Running guardrail test: {test_case.name}")

            session_id = sessions_client.create_session_id()

            try:
                parts = session_id.split("/")
                project, location = parts[1], parts[3]
                session_uuid = parts[-1]
                base_url = "https://ccai.cloud.google.com/insights"
                path = (
                    f"projects/{project}/locations/{location}/quality"
                    f"/conversations/{session_uuid}"
                )
                session_id_link = (
                    f'=HYPERLINK("{base_url}/{path}", "{session_uuid}")'
                )
            except (IndexError, ValueError):
                session_id_link = session_id

            error_msg = None
            actual_triggered = False
            actual_guardrail_name = None
            actual_guardrail_type = None
            actual_reason = None
            latency_ms = None
            agent_response_text = ""

            try:
                # Execute user query
                start_time = time.perf_counter()
                res = sessions_client.run(
                    session_id=session_id,
                    text=test_case.user_input,
                    variables=test_case.variables,
                )
                latency_ms = round((time.perf_counter() - start_time) * 1000, 2)

                outputs = getattr(res, "outputs", []) or []
                agent_response_text = self.get_agent_text_from_outputs(outputs)

                for output in outputs:  # pylint: disable=not-an-iterable
                    diagnostic_info = getattr(output, "diagnostic_info", None)
                    if diagnostic_info and hasattr(
                        diagnostic_info, "root_span"
                    ):
                        root_span = diagnostic_info.root_span

                        try:
                            # Safely unwrap the protobuf or dict trace
                            span_dict = (
                                MessageToDict(root_span._pb)
                                if hasattr(root_span, "_pb")
                                else MessageToDict(root_span)
                            )
                        except (
                            AttributeError,
                            KeyError,
                            TypeError,
                            ValueError,
                        ):
                            span_dict = (
                                dict(root_span)
                                if isinstance(root_span, dict)
                                else {}
                            )

                        triggered_span = self._search_span_dict(span_dict)
                        if triggered_span:
                            actual_triggered = True
                            attrs = triggered_span.get("attributes", {})
                            actual_guardrail_name = attrs.get("name")
                            actual_guardrail_type = attrs.get(
                                "type",
                                attrs.get(
                                    "guardrailType", attrs.get("guardrail_type")
                                ),
                            )
                            actual_reason = attrs.get("reason")
                            break  # Found the triggered guardrail

            except (AttributeError, KeyError, RuntimeError, ValueError) as e:
                error_msg = str(e)
                logger.error(
                    "Error running session for test '%s': %s", test_case.name, e
                )

            passed = True

            has_expected_name = bool(
                test_case.expected_guardrail_name
                and test_case.expected_guardrail_name.strip()
                and test_case.expected_guardrail_name.lower() != "none"
            )
            has_expected_type = bool(
                test_case.expected_guardrail_type
                and test_case.expected_guardrail_type.strip()
                and test_case.expected_guardrail_type.lower() != "none"
            )
            expected_triggered = has_expected_name or has_expected_type

            error_details = []
            if error_msg:
                passed = False
                error_details.append(error_msg)
            elif expected_triggered != actual_triggered:
                passed = False
                error_details.append(
                    f"Expected trigger: {expected_triggered}, "
                    f"Actual trigger: {actual_triggered}"
                )
            elif actual_triggered and expected_triggered:
                if (
                    has_expected_name
                    and test_case.expected_guardrail_name
                    != actual_guardrail_name
                ):
                    passed = False
                    error_details.append(
                        f"Expected guardrail name "
                        f"'{test_case.expected_guardrail_name}', but got "
                        f"'{actual_guardrail_name}'"
                    )

                if has_expected_type and actual_guardrail_type:
                    norm_expected = (
                        test_case.expected_guardrail_type.lower()
                        .replace(" ", "")
                        .replace("_", "")
                    )
                    norm_actual = (
                        actual_guardrail_type.lower()
                        .replace(" ", "")
                        .replace("_", "")
                    )

                    matched = False
                    if norm_expected in (
                        "promptguard",
                        "rules",
                        "llmpolicy",
                        "llmpromptsecurity",
                    ):
                        matched = norm_actual in (
                            "llmpolicy",
                            "llmpromptsecurity",
                        )
                    elif norm_expected in ("blocklist", "contentfilter"):
                        matched = norm_actual in ("blocklist", "contentfilter")
                    elif norm_expected in (
                        "rai",
                        "raisafety",
                        "safety",
                        "modelsafety",
                    ):
                        matched = norm_actual in (
                            "raisafety",
                            "safety",
                            "modelsafety",
                        )
                    else:
                        matched = norm_expected == norm_actual

                    if not matched:
                        passed = False
                        error_details.append(
                            f"Expected guardrail type matching "
                            f"'{test_case.expected_guardrail_type}', but got "
                            f"'{actual_guardrail_type}'"
                        )

            data = {
                "actual_triggered": actual_triggered,
                "actual_guardrail_name": actual_guardrail_name,
                "actual_guardrail_type": actual_guardrail_type,
                "actual_reason": actual_reason,
                "agent_response": agent_response_text,
                "latency (ms)": latency_ms,
                "Session ID link": session_id_link,
                "error": error_msg,
                "error_details": error_details,
                "pass": passed,
                "app_name": self.app_name,
                "app_display_name": app_display_name,
                "model": configured_model,
            }
            results.append(data)

            if debug:
                print(f"  Passed: {passed}")
                if actual_triggered:
                    print(f"  Triggered: {actual_guardrail_name}")
                    print(f"  Reason: {str(actual_reason)[:100]}...")

        if console_logging:
            print("\n######## Test Results ########\n")
            passed_count = sum(1 for res in results if res["pass"])
            failed_count = len(results) - passed_count

            for i, res in enumerate(results):
                test_id = df.iloc[i].get(
                    "test_id", df.iloc[i].get("name", f"Test_{i}")
                )
                status = "SUCCESS" if res["pass"] else "FAILURE"
                print(f"{status}: {test_id}")
                if not res["pass"] and res.get("error_details"):
                    print(json.dumps(res["error_details"]))

            passed_c, failed_c = passed_count, failed_count
            print(
                f"\n######## Summary ########\nTotal Tests: {len(results)} | "
                f"Passed: {passed_c} | Failed: {failed_c}\n"
            )

        # Append results to the original dataframe
        results_df = pd.DataFrame(results)
        return pd.concat([df.reset_index(drop=True), results_df], axis=1)

    @staticmethod
    def _calculate_stats(df: pd.DataFrame) -> SummaryStats:
        """Calculates summary statistics from a test results DataFrame."""
        total_tests = len(df)
        if total_tests == 0:
            return SummaryStats(0, 0.0, 0, 0.0, 0.0, 0.0, "Unknown", "Unknown")

        pass_count = sum(1 for p in df.get("pass", []) if p is True)
        pass_rate = 0.0
        if total_tests > 0:
            pass_rate = round(pass_count / total_tests, 2)

        has_latency = (
            "latency (ms)" in df.columns
            and not df.empty
            and not df["latency (ms)"].dropna().empty
        )
        if has_latency:
            latency_dropna = df["latency (ms)"].dropna()
            p50 = round(latency_dropna.quantile(0.50), 2)
            p90 = round(latency_dropna.quantile(0.90), 2)
            p99 = round(latency_dropna.quantile(0.99), 2)
        else:
            p50 = p90 = p99 = 0.0

        agent_name = (
            df["app_display_name"].iloc[0]
            if "app_display_name" in df.columns and not df.empty
            else "Unknown"
        )
        model = (
            df["model"].iloc[0]
            if "model" in df.columns and not df.empty
            else "Unknown"
        )

        return SummaryStats(
            pass_count=pass_count,
            pass_rate=pass_rate,
            total_tests=total_tests,
            p50_latency_ms=p50,
            p90_latency_ms=p90,
            p99_latency_ms=p99,
            agent_name=agent_name,
            model=model,
        )

    def generate_report(
        self, df: pd.DataFrame, test_type: str = "guardrails_test"
    ) -> pd.DataFrame:
        """Generates a summary stats report for recent tests."""
        report_timestamp = datetime.datetime.now()
        stats = GuardrailEvals._calculate_stats(df)

        df_report = pd.DataFrame(
            columns=SUMMARY_SCHEMA_COLUMNS,
            data=[
                [
                    report_timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    test_type,
                    stats.total_tests,
                    stats.pass_count,
                    stats.pass_rate,
                    stats.p50_latency_ms,
                    stats.p90_latency_ms,
                    stats.p99_latency_ms,
                    stats.agent_name,
                    stats.model,
                ]
            ],
        )

        return df_report
