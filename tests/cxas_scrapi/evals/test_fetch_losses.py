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

import pytest

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


def test_extract_transcript():
    conv = {
        "name": "projects/p/locations/l/conversations/conv_123",
        "transcript": {
            "transcriptSegments": [
                {
                    "segmentParticipant": {"role": "CUSTOMER"},
                    "text": "billing issue",
                }
            ]
        },
    }
    result = fetch_losses.extract_transcript(conv)

    assert result is not None
    assert result["conversation_id"] == "conv_123"
    assert "billing issue" in result["transcript"]


@patch("fetch_losses.Insights")
@patch("sys.argv")
def test_main_end_to_end(mock_argv, mock_insights_class, tmp_path):
    mock_insights = MagicMock()
    mock_insights_class.return_value = mock_insights

    # Mock list_conversations returning FULL conversations
    mock_insights.list_conversations.return_value = [
        {
            "name": "projects/p/locations/l/conversations/c2",
            "labels": {"sessionContained": "false"},
            "transcript": {
                "transcriptSegments": [
                    {
                        "segmentParticipant": {"role": "CUSTOMER"},
                        "text": "utterance from c2",
                    }
                ]
            },
        },
        {
            "name": "projects/p/locations/l/conversations/c4",
            "labels": {},
            "transcript": {
                "transcriptSegments": [
                    {
                        "segmentParticipant": {"role": "CUSTOMER"},
                        "text": "utterance from c4",
                    }
                ]
            },
        },
        {
            "name": "projects/p/locations/l/conversations/c5",
            "labels": {"sessionContained": "false"},
            "transcript": {
                "transcriptSegments": [
                    {
                        "segmentParticipant": {"role": "CUSTOMER"},
                        "text": "utterance from c5",
                    }
                ]
            },
        },
    ]

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
        "5",
        "--output-file",
        str(output_file),
    ]

    # Run main
    fetch_losses.main()

    # Verify list_conversations was called with default filter and FULL view
    mock_insights.list_conversations.assert_called_once_with(
        filter_str='agent_id="test-app" AND -labels.sessionContained:"true"',
        view="FULL",
        page_size=100,
        max_pages=1,
    )

    # Verify output file exists and contains the expected fields
    assert output_file.exists()

    with open(output_file) as f:
        data = json.load(f)
        assert data["total_losses"] == 3
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


@patch("fetch_losses.Insights")
@patch("sys.argv")
def test_main_with_time_filters(mock_argv, mock_insights_class, tmp_path):
    mock_insights = MagicMock()
    mock_insights_class.return_value = mock_insights

    mock_insights.list_conversations.return_value = []

    output_file = tmp_path / "raw_losses.json"

    # Set CLI args with time filters
    sys.argv = [
        "fetch_losses.py",
        "--project-id",
        "test-project",
        "--location",
        "us",
        "--app-id",
        "test-app",
        "--limit",
        "5",
        "--start-time",
        "2026-05-20T00:00:00Z",
        "--end-time",
        "2026-05-26T23:59:59Z",
        "--output-file",
        str(output_file),
    ]

    # We expect sys.exit(0) because list_conversations returns empty list
    with pytest.raises(SystemExit) as e:
        fetch_losses.main()
    assert e.value.code == 0

    # Verify list_conversations was called with correct filter (including
    # default loss filter) and FULL view
    mock_insights.list_conversations.assert_called_once_with(
        filter_str=(
            'agent_id="test-app" AND -labels.sessionContained:"true" '
            'AND create_time >= "2026-05-20T00:00:00Z" '
            'AND create_time <= "2026-05-26T23:59:59Z"'
        ),
        view="FULL",
        page_size=100,
        max_pages=1,
    )


@patch("fetch_losses.Insights")
@patch("sys.argv")
def test_main_with_custom_filter(mock_argv, mock_insights_class, tmp_path):
    mock_insights = MagicMock()
    mock_insights_class.return_value = mock_insights

    mock_insights.list_conversations.return_value = []

    output_file = tmp_path / "raw_losses.json"

    # Set CLI args with custom filter
    sys.argv = [
        "fetch_losses.py",
        "--project-id",
        "test-project",
        "--location",
        "us",
        "--app-id",
        "test-app",
        "--limit",
        "5",
        "--filter",
        'labels.sessionContained="true" AND labels.someKey="someValue"',
        "--output-file",
        str(output_file),
    ]

    with pytest.raises(SystemExit) as e:
        fetch_losses.main()
    assert e.value.code == 0

    # Verify list_conversations was called with custom filter instead of
    # default loss filter and FULL view
    mock_insights.list_conversations.assert_called_once_with(
        filter_str=(
            'agent_id="test-app" AND labels.sessionContained="true" '
            'AND labels.someKey="someValue"'
        ),
        view="FULL",
        page_size=100,
        max_pages=1,
    )
