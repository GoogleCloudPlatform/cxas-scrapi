#!/usr/bin/env python3
"""Offline unit test suite for `generate_report.py`."""

import sys
import unittest
from pathlib import Path

# Add skill directory to import path
skill_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(skill_dir))

from generate_report import categorize_errors, parse_evaluation_schema_details


class TestReportGenerator(unittest.TestCase):

    def test_categorize_errors(self):
        sample_errors = [
            "[Tool Parameter Correctness Failure (Turn 1)]: Accuracy 0%.",
            "[Routing/Transfer Failure (Turn 2)]: Agent transfer to 'main' failed expectation (Outcome: FAIL).",
            "[Hallucination (Turn 3)]: Output contains hallucinated facts.",
            "[System Error]: QUOTA_EXHAUSTED.",
            "Missing state variable binding."
        ]
        cats = categorize_errors(sample_errors)
        self.assertIn("[Tool Parameter Correctness Failure (Turn 1)]: Accuracy 0%.", cats["Tool Calls"])
        self.assertIn("[Routing/Transfer Failure (Turn 2)]: Agent transfer to 'main' failed expectation (Outcome: FAIL).", cats["Agent Handovers"])
        self.assertIn("[Hallucination (Turn 3)]: Output contains hallucinated facts.", cats["Generative & Phrasing"])
        self.assertIn("[System Error]: QUOTA_EXHAUSTED.", cats["System & Infrastructure"])
        self.assertIn("Missing state variable binding.", cats["State & Variables"])

    def test_unexpected_agent_transfer_capture(self):
        sample_turn = {
            "conversation": "test_conv",
            "expectationOutcome": [
                {
                    "observedAgentTransfer": {
                        "displayName": "main",
                        "targetAgent": "projects/p/locations/l/apps/a/agents/root-agent"
                    },
                    "outcome": "FAIL"
                }
            ]
        }
        eval_data = {"goldenResult": {"turnReplayResults": [sample_turn]}}
        findings, telemetry = parse_evaluation_schema_details(eval_data)
        self.assertTrue(any("Agent transfer to 'main' failed expectation" in f for f in findings))
        self.assertEqual(len(telemetry["agentTransfers"]), 1)
        self.assertEqual(telemetry["agentTransfers"][0]["transfer"]["displayName"], "main")

    def test_tool_order_false_alarm_suppression(self):
        sample_turn = {
            "conversation": "test_conv",
            "overallToolInvocationResult": {
                "outcome": "PASS",
                "toolInvocationScore": 1.0
            },
            "toolOrderedInvocationScore": 0.0
        }
        eval_data = {"goldenResult": {"turnReplayResults": [sample_turn]}}
        findings, _ = parse_evaluation_schema_details(eval_data)
        self.assertFalse(any("Tool Order Failure" in f for f in findings))


if __name__ == "__main__":
    unittest.main()
