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

import unittest
from unittest.mock import MagicMock, patch

from cxas_scrapi.evals.simulation_evals import SimulationEvals


class MockProto:
    def __init__(self, data):
        self.data = data
    @staticmethod
    def to_dict(obj):
        return obj.data

class TestSimToGolden(unittest.TestCase):
    def setUp(self):
        self.app_name = "projects/p/locations/l/apps/a"

        # Create instance without calling __init__ to avoid complex dependency
        # mocking
        self.sim_evals = MagicMock(spec=SimulationEvals)
        self.sim_evals.app_name = self.app_name
        self.sim_evals.creds = MagicMock()

        # Bind the real method to the mock instance
        self.sim_evals.export_results_to_golden = (
            SimulationEvals.export_results_to_golden.__get__(
                self.sim_evals, SimulationEvals
            )
        )

    @patch('cxas_scrapi.evals.simulation_evals.ConversationHistory')
    def test_export_results_to_golden(self, mock_ch_class):
        mock_ch = mock_ch_class.return_value

        # Mock conversation data
        mock_conv_data = {
            "turns": [
                {
                    "messages": [
                        {"role": "user", "chunks": [{"text": "hello"}]},
                        {"role": "agent", "chunks": [{"text": "hi there"}]}
                    ]
                },
                {
                    "messages": [
                        {"role": "user", "chunks": [{"text": "how are you?"}]},
                        {"role": "agent", "chunks": [
                            {"text": "I am good,"},
                            {
                                "tool_call": {
                                    "display_name": "get_weather",
                                    "args": {"city": "London"}
                                }
                            }
                        ]}
                    ]
                },
                {
                    "messages": [
                        {"role": "get_weather", "chunks": [
                            {
                                "tool_response": {
                                    "display_name": "get_weather",
                                    "response": {"temp": 20}
                                }
                            }
                        ]},
                        {"role": "agent", "chunks": [
                            {"text": "It is 20 degrees."}
                        ]}
                    ]
                }
            ]
        }

        mock_ch.get_conversation.return_value = MockProto(mock_conv_data)

        results = [
            {
                "session_id": "session1",
                "name": "Test Conv",
                "expectation_details": [{"expectation": "Must say hi"}],
                "session_parameters": {"key": "val"}
            }
        ]

        # We need to mock Sessions._expand_pb_struct as it's called in the
        # method
        with patch(
            'cxas_scrapi.core.sessions.Sessions._expand_pb_struct',
            side_effect=lambda x: x
        ):
            yaml_output = self.sim_evals.export_results_to_golden(results)

            # Basic checks on generated YAML
            self.assertIn("user: hello", yaml_output)
            self.assertIn("agent: hi there", yaml_output)
            self.assertIn("user: how are you?", yaml_output)
            self.assertIn("action: get_weather", yaml_output)
            self.assertIn("city: London", yaml_output)
            self.assertIn("output:", yaml_output)
            self.assertIn("temp: 20", yaml_output)
            self.assertIn("- It is 20 degrees.", yaml_output)
            self.assertIn("Must say hi", yaml_output)
            self.assertIn("key: val", yaml_output)

if __name__ == '__main__':
    unittest.main()
