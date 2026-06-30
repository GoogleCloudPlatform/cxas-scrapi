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

"""Tests for rubric scorer."""

import asyncio
from unittest import mock

from absl.testing import absltest

from skill_eval import scenario, scorer


class ScorerTest(absltest.TestCase):
    @mock.patch("google.genai.Client")
    def test_score_calls_gemini_and_parses_json(self, mock_client_class):
        mock_client = mock_client_class.return_value
        mock_response = mock.MagicMock()
        mock_response.text = (
            '{"scores": [{"criteria": "C1", "score": 2, "reasoning": "r"}],'
            ' "summary": "s"}'
        )
        mock_client.models.generate_content.return_value = mock_response

        scen = scenario.Scenario(
            name="test",
            text="dummy",
            prompt="dummy",
            rubric=[
                scenario.Rubric(
                    criteria="C1", perfect="perfect", good="good", failed="fail"
                )
            ],
            assets=[],
        )
        s = scorer.Scorer(project="p")

        result = asyncio.run(
            s.grade_conversation(
                scen,
                [
                    scorer.ScorerTurn(
                        user_message="hi",
                        agent_response="ok",
                        tool_interactions=[],
                    )
                ],
            )
        )

        self.assertEqual(result.total_score, 2)
        self.assertEqual(result.scores[0].criteria, "C1")
        self.assertEqual(result.summary, "s")


if __name__ == "__main__":
    absltest.main()
