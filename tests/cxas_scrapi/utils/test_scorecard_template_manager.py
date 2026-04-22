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

import json

import json5
import pytest
import yaml

from cxas_scrapi.utils import scorecard_template_manager


class TestScorecardTemplateManager:
    def test_save_and_load_template(self, tmp_path):
        template_path = tmp_path / "my_scorecard.json"

        scorecard = {
            "displayName": "Test Scorecard",
            "description": "A test scorecard",
        }
        questions = [
            {"questionBody": "Question 1", "order": 1},
            {"questionBody": "Question 2", "order": 2},
        ]

        scorecard_template_manager.save_scorecard_template(
            scorecard, questions, str(template_path)
        )

        assert template_path.exists()

        loaded_scorecard, loaded_questions = (
            scorecard_template_manager.load_scorecard_template(
                str(template_path)
            )
        )

        assert loaded_scorecard.get("displayName") == "Test Scorecard"
        assert len(loaded_questions) == 2
        assert loaded_questions[0].get("questionBody") == "Question 1"
        assert loaded_questions[1].get("questionBody") == "Question 2"

        with open(template_path, "r") as f:
            template_data = json.load(f)

        assert scorecard_template_manager.SCORECARD_KEY in template_data
        assert scorecard_template_manager.QUESTIONS_KEY in template_data

    def test_save_clears_output_only_fields(self, tmp_path):
        template_path = tmp_path / "filtered_scorecard.json"

        scorecard = {
            "name": "projects/p/locations/l/qaScorecards/s",
            "displayName": "Test",
        }
        questions = [
            {
                "name": (
                    "projects/p/locations/l/qaScorecards/s/revisions/r/qaQuestions/q"
                ),
                "questionBody": "Q",
            }
        ]

        scorecard_template_manager.save_scorecard_template(
            scorecard, questions, str(template_path)
        )

        loaded_scorecard, loaded_questions = (
            scorecard_template_manager.load_scorecard_template(
                str(template_path)
            )
        )

        assert "name" not in loaded_scorecard
        assert "name" not in loaded_questions[0]

    def test_json_format(self, tmp_path):
        path = tmp_path / "template.json"
        scorecard = {"displayName": "JSON Scorecard"}
        questions = [{"questionBody": "Q1", "order": 1}]

        scorecard_template_manager.save_scorecard_template(
            scorecard, questions, str(path)
        )

        with open(path, "r") as f:
            data = json.load(f)
        assert (
            data[scorecard_template_manager.SCORECARD_KEY]["displayName"]
            == "JSON Scorecard"
        )

        loaded_sc, loaded_qs = (
            scorecard_template_manager.load_scorecard_template(str(path))
        )
        assert loaded_sc["displayName"] == "JSON Scorecard"
        assert len(loaded_qs) == 1

    def test_json5_format(self, tmp_path):
        path = tmp_path / "template.json5"
        scorecard = {"displayName": "JSON5 Scorecard"}
        questions = [{"questionBody": "Q1", "order": 1}]

        scorecard_template_manager.save_scorecard_template(
            scorecard, questions, str(path)
        )

        with open(path, "r") as f:
            data = json5.load(f)
        assert (
            data[scorecard_template_manager.SCORECARD_KEY]["displayName"]
            == "JSON5 Scorecard"
        )

        loaded_sc, _ = scorecard_template_manager.load_scorecard_template(
            str(path)
        )
        assert loaded_sc["displayName"] == "JSON5 Scorecard"

    @pytest.mark.parametrize("ext", [".yaml", ".yml"])
    def test_yaml_format(self, tmp_path, ext):
        path = tmp_path / f"template{ext}"
        scorecard = {"displayName": f"YAML Scorecard {ext}"}
        questions = [{"questionBody": "Multi-line\\nInstructions", "order": 1}]

        scorecard_template_manager.save_scorecard_template(
            scorecard, questions, str(path)
        )

        with open(path, "r") as f:
            data = yaml.safe_load(f)
        assert (
            data[scorecard_template_manager.SCORECARD_KEY]["displayName"]
            == f"YAML Scorecard {ext}"
        )
        assert (
            data[scorecard_template_manager.QUESTIONS_KEY][0]["questionBody"]
            == "Multi-line\\nInstructions"
        )

        loaded_sc, loaded_qs = (
            scorecard_template_manager.load_scorecard_template(str(path))
        )
        assert loaded_sc["displayName"] == f"YAML Scorecard {ext}"
        assert loaded_qs[0]["questionBody"] == "Multi-line\\nInstructions"

    def test_json5_loading_with_comments(self, tmp_path):
        path = tmp_path / "comments.json5"
        content = """
        {
          // This is a comment
          qaScorecard: {
            displayName: "Commented Scorecard",
          },
          qaQuestions: [
            {
              questionBody: "Q1",
              order: 1, // Another comment
            }
          ]
        }
        """
        path.write_text(content)

        loaded_sc, loaded_qs = (
            scorecard_template_manager.load_scorecard_template(str(path))
        )
        assert loaded_sc["displayName"] == "Commented Scorecard"
        assert loaded_qs[0]["questionBody"] == "Q1"

    def test_invalid_extension(self, tmp_path):
        path = tmp_path / "template.txt"
        scorecard = {"displayName": "Invalid"}
        questions = []

        with pytest.raises(ValueError):
            scorecard_template_manager.save_scorecard_template(
                scorecard, questions, str(path)
            )

        path.write_text("{}")
        with pytest.raises(ValueError):
            scorecard_template_manager.load_scorecard_template(str(path))
