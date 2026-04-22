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

from unittest.mock import MagicMock

import pytest

from cxas_scrapi.core.scorecards import Scorecards
from cxas_scrapi.utils.insights_utils import InsightsUtils


class TestInsightsUtils:
    @pytest.fixture
    def utils_with_mocked_client(self):
        utils = InsightsUtils(project_id="test-project", location="us-central1")
        utils.scorecards_client = MagicMock(spec=Scorecards)
        return utils

    def test_match_questions_identical(self, utils_with_mocked_client):
        q1 = {
            "questionBody": "Q1",
            "answerInstructions": "I1",
            "answerChoices": [{"str_value": "Yes", "score": 1.0}],
        }
        q2 = {
            "questionBody": "Q1",
            "answerInstructions": "I1",
            "answerChoices": [{"str_value": "Yes", "score": 1.0}],
        }
        assert utils_with_mocked_client._match_questions(q1, q2) is True

    def test_match_questions_different_body(self, utils_with_mocked_client):
        q1 = {"questionBody": "Q1"}
        q2 = {"questionBody": "Q2"}
        assert utils_with_mocked_client._match_questions(q1, q2) is False

    def test_match_questions_ignores_order(self, utils_with_mocked_client):
        # order is NOT in fields_to_match by default
        q1 = {"questionBody": "Q1", "order": 1}
        q2 = {"questionBody": "Q1", "order": 2}
        assert utils_with_mocked_client._match_questions(q1, q2) is True

    def test_sync_questions_non_destructive(self, utils_with_mocked_client):
        mock_client = utils_with_mocked_client.scorecards_client

        # Existing questions in revision
        existing_q1 = {"name": "existing_q1_name", "questionBody": "KeepMe"}
        existing_q2 = {"name": "existing_q2_name", "questionBody": "DeleteMe"}
        mock_client.list_questions.return_value = [existing_q1, existing_q2]

        # Template questions
        template_q1 = {"questionBody": "KeepMe"}
        template_q2 = {"questionBody": "AddMe"}

        utils_with_mocked_client._sync_questions(
            "revision_name", [template_q1, template_q2]
        )

        # Assertions
        # Should delete existing_q2 because it doesn't match KeepMe or AddMe
        mock_client.delete_question.assert_called_once_with("existing_q2_name")

        # Should create template_q2 (AddMe) because it doesn't match KeepMe
        # or DeleteMe.
        mock_client.create_question.assert_called_once_with(
            "revision_name", template_q2
        )

        # Should patch template_q1 (KeepMe) because it matches existing_q1
        mock_client.patch_question.assert_called_once_with(
            "existing_q1_name", template_q1
        )
