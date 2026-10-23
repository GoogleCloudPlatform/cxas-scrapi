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

"""Unit tests for in-product Cloud eval diagnostic reporting."""

import unittest
from typing import Any

from cxas_scrapi.utils.cloud_eval_reporter import (
    categorize_cloud_errors,
    compute_performance_summary,
    generate_cloud_json_report,
    parse_evaluation_schema_details,
)


class TestCloudEvalReporter(unittest.TestCase):
    """Test suite verifying schema parsing, noise filters, and categorization."""

    def test_categorize_cloud_errors(self) -> None:
        """Verifies error strings map to correct execution dimensions."""
        errors = [
            "Missing required argument: 'user_id' for tool 'lookup_account'.",
            "Unresolved template variable {customer_token} in prompt.",
            "Agent transfer to 'billing_agent' failed expectation (Outcome: FAIL).",
            "503 Service Unavailable: Quota exceeded for model gemini-pro.",
            "Low consistency (NOT_CONSISTENT, score: 1/4).",
        ]
        cats = categorize_cloud_errors(errors)

        assert errors[0] in cats["Tool Calls"]
        assert errors[1] in cats["State & Variables"]
        assert errors[2] in cats["Agent Handovers"]
        assert errors[3] in cats["System & Infrastructure"]
        assert errors[4] in cats["Generative & Phrasing"]

    def test_unexpected_agent_transfer_capture(self) -> None:
        """Verifies observedAgentTransfer failures are flagged."""
        mock_eval: dict[str, Any] = {
            "evaluationStatus": "FAIL",
            "turnReplayResults": [
                {
                    "expectationOutcome": {
                        "outcome": "FAIL",
                        "observedAgentTransfer": {
                            "displayName": "main",
                            "targetAgent": "projects/test/locations/us/apps/1/agents/main",
                        },
                    },
                    "expectation": {},
                }
            ],
        }
        findings, telemetry = parse_evaluation_schema_details(mock_eval)

        assert "main" in telemetry["agentTransfers"]
        assert any(
            "Agent transfer to 'main' failed expectation" in f for f in findings
        )

    def test_tool_order_false_alarm_suppression(self) -> None:
        """Verifies sequence warnings are suppressed when overall call passed."""
        mock_eval: dict[str, Any] = {
            "evaluationStatus": "PASS",
            "turnReplayResults": [
                {
                    "overallToolInvocationResult": {
                        "outcome": "PASS",
                        "toolOrderedInvocationScore": 0.0,
                    }
                }
            ],
        }
        findings, telemetry = parse_evaluation_schema_details(mock_eval)

        assert telemetry["toolOrderedInvocationScore"] == 0.0
        assert not any("Tool execution order differed" in f for f in findings)

    def test_generate_cloud_json_report(self) -> None:
        """Verifies structured JSON telemetry generation."""
        mock_results = [
            {
                "name": "test_case_1",
                "displayName": "Golden Scenario 1",
                "evaluationStatus": "PASS",
                "turnReplayResults": [],
            }
        ]
        mock_linter_issues = ["[`main`]: Inactive pill `{test}`"]

        payload = generate_cloud_json_report(
            eval_results=mock_results,
            cloud_linter_issues=mock_linter_issues,
            app_id="projects/p/locations/l/apps/a",
            linter_output="OK",
        )

        assert payload["total"] == 1
        assert payload["passed"] == 1
        assert payload["failed"] == 0
        assert payload["schemaVersion"] == "ces.v1beta.evaluation.proto"
        assert payload["projectLinterAudit"]["totalIssues"] == 1
        assert payload["projectLinterAudit"]["issues"] == mock_linter_issues

    def test_undeclared_tool_and_span_errors_capture(self) -> None:
        """Verifies undeclared tool references and span errors from conversation trace are captured."""
        mock_eval: dict[str, Any] = {
            "evaluationStatus": "FAIL",
            "turnReplayResults": [
                {
                    "expectationOutcome": [],
                },
                {
                    "expectationOutcome": [],
                },
            ],
        }
        mock_conv: dict[str, Any] = {
            "turns": [
                {},
                {
                    "root_span": {
                        "attributes": {
                            "undeclared tool references": [
                                "get_premium_and_out_of_bundle_usage"
                            ],
                            "error": "Timeout in tool call",
                        }
                    }
                },
            ]
        }
        findings, _ = parse_evaluation_schema_details(mock_eval, mock_conv)
        assert any(
            "References to undeclared tools: get_premium_and_out_of_bundle_usage"
            in f
            for f in findings
        )
        assert any("Timeout in tool call" in f for f in findings)

    def test_state_machine_config_errors_capture(self) -> None:
        """Verifies state machine config_validation_failed and slot errors are caught and categorized."""
        mock_eval: dict[str, Any] = {
            "evaluationStatus": "FAIL",
            "turnReplayResults": [{"expectationOutcome": []}],
        }
        mock_conv: dict[str, Any] = {
            "turns": [
                {
                    "messages": [
                        {
                            "chunks": [
                                {
                                    "default_variables": {
                                        "sm": {
                                            "_log": [
                                                {
                                                    "src": "before_model",
                                                    "tag": "config_validation_failed",
                                                    "level": "ERROR",
                                                    "data": {
                                                        "errors": [
                                                            "Task 'FetchAccountOverview' input 'account_number:account_number' not in slots",
                                                            "Slot 'account_overview' is unreachable",
                                                        ]
                                                    },
                                                }
                                            ]
                                        }
                                    }
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        findings, _ = parse_evaluation_schema_details(mock_eval, mock_conv)
        assert any("not in slots" in f for f in findings)
        assert any("unreachable" in f for f in findings)
        cats = categorize_cloud_errors(findings)
        assert len(cats["State & Variables"]) == 2

    def test_compute_performance_summary(self) -> None:
        """Verifies calculation of latency averages, max tools, and token economics."""
        mock_evals: list[dict[str, Any]] = [
            {
                "name": "test_perf",
                "turnReplayResults": [
                    {
                        "turn_latency": "2.5s",
                        "tool_call_latencies": [
                            {
                                "display_name": "slow_tool",
                                "execution_latency": "0.45s",
                            }
                        ],
                    }
                ],
            }
        ]
        perf = compute_performance_summary(mock_evals, "app-test-id")
        assert perf["avgTurnSeconds"] == 2.5
        assert perf["maxTurnSeconds"] == 2.5
        assert perf["maxToolName"] == "slow_tool"
        assert perf["maxToolSeconds"] == 0.45

    def test_generate_cloud_json_report_performance_parity(self) -> None:
        """Verifies JSON agentic report includes aggregate performanceTelemetry and per-test granular telemetry."""
        mock_evals: list[dict[str, Any]] = [
            {
                "name": "test_json_parity",
                "evaluationStatus": "PASS",
                "turnReplayResults": [
                    {
                        "turn_latency": "1.2s",
                        "tool_call_latencies": [
                            {
                                "display_name": "lookup_tool",
                                "execution_latency": "0.15s",
                            }
                        ],
                    }
                ],
            }
        ]
        json_report = generate_cloud_json_report(mock_evals, [], "app-test-id")
        assert "performanceTelemetry" in json_report
        assert json_report["performanceTelemetry"]["avgTurnSeconds"] == 1.2
        detailed = json_report["detailedTelemetry"][0]["telemetry"]
        assert "turnLatencies" in detailed
        assert "toolCallLatencies" in detailed
        assert detailed["turnLatencies"][0]["latencySeconds"] == 1.2
        assert detailed["toolCallLatencies"][0]["toolName"] == "lookup_tool"


if __name__ == "__main__":
    unittest.main()
