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
import os
import sys
from unittest.mock import MagicMock, patch

# Add the skill directory to sys.path so we can import fetch_losses
sys.path.append(
    os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            "../../../.agents/skills/cxas-loss-analysis/scripts",
        )
    )
)

import fetch_losses


def test_ccai_to_cxas_dict():
    ccai_conv = {
        "transcript": {
            "transcriptSegments": [
                {
                    "segmentParticipant": {"role": "CUSTOMER"},
                    "text": "hello, I need help with my bill",
                },
                {
                    "segmentParticipant": {"role": "AGENT"},
                    "text": "sure, I can help with that",
                },
            ]
        }
    }
    result = fetch_losses.ccai_to_cxas_dict(ccai_conv)
    assert len(result["turns"]) == 2
    assert result["turns"][0]["messages"][0]["role"] == "user"
    assert (
        result["turns"][0]["messages"][0]["chunks"][0]["text"]
        == "hello, I need help with my bill"
    )
    assert result["turns"][1]["messages"][0]["role"] == "agent"
    assert (
        result["turns"][1]["messages"][0]["chunks"][0]["text"]
        == "sure, I can help with that"
    )


@patch("cxas_scrapi.core.insights.Insights")
def test_extract_transcript(mock_insights_class):
    mock_client = MagicMock()
    mock_insights_class.return_value = mock_client

    mock_client.get_conversation.return_value = {
        "transcript": {
            "transcriptSegments": [
                {
                    "segmentParticipant": {"role": "CUSTOMER"},
                    "text": "billing issue",
                }
            ]
        }
    }

    conv_summary = {"name": "projects/p/locations/l/conversations/conv_123"}
    result = fetch_losses.extract_transcript(mock_client, conv_summary)

    assert result is not None
    assert result["conversation_id"] == "conv_123"
    assert "billing issue" in result["transcript"]


@patch("fetch_losses.Insights")
@patch("sys.argv")
def test_main_end_to_end(mock_argv, mock_insights_class, tmp_path):
    mock_insights = MagicMock()
    mock_insights_class.return_value = mock_insights

    # Mock list_conversations: 2 contained, 3 non-contained
    mock_insights.list_conversations.return_value = [
        {
            "name": "projects/p/locations/l/conversations/c1",
            "labels": {"sessionContained": "true"},
        },
        {
            "name": "projects/p/locations/l/conversations/c2",
            "labels": {"sessionContained": "false"},
        },
        {
            "name": "projects/p/locations/l/conversations/c3",
            "labels": {"sessionContained": "true"},
        },
        {
            "name": "projects/p/locations/l/conversations/c4",
            "labels": {},
        },  # missing = loss
        {
            "name": "projects/p/locations/l/conversations/c5",
            "labels": {"sessionContained": "false"},
        },
    ]

    # Mock get_conversation for details
    def mock_get_conv(name):
        conv_id = name.split("/")[-1]
        return {
            "name": name,
            "transcript": {
                "transcriptSegments": [
                    {
                        "segmentParticipant": {"role": "CUSTOMER"},
                        "text": f"utterance from {conv_id}",
                    }
                ]
            },
        }

    mock_insights.get_conversation.side_effect = mock_get_conv

    output_file = tmp_path / "raw_losses.json"

    # Set CLI args
    mock_argv.clear()
    sys.argv = [
        "fetch_losses.py",
        "--project-id",
        "test-project",
        "--location",
        "us",
        "--app-id",
        "test-app",
        "--limit",
        "10",
        "--loss-limit",
        "5",
        "--output-file",
        str(output_file),
    ]

    # Run main
    fetch_losses.main()

    # Verify output file exists and contains the expected fields
    assert output_file.exists()

    with open(output_file) as f:
        data = json.load(f)
        assert data["total_inspected"] == 5
        assert data["total_losses"] == 3
        assert data["containment_rate"] == 40.0
        assert len(data["chunks"]) == 1

        chunk_file = data["chunks"][0]
        assert os.path.exists(chunk_file)

        with open(chunk_file) as cf:
            chunk_data = json.load(cf)
            assert len(chunk_data) == 3

            # Verify records downloaded are the losses
            conv_ids = [t["conversation_id"] for t in chunk_data]
            assert "c2" in conv_ids
            assert "c4" in conv_ids
            assert "c5" in conv_ids
            assert "c1" not in conv_ids
            assert "c3" not in conv_ids
