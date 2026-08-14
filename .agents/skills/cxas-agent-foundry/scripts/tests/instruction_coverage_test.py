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

"""Tests for GECX evaluation coverage analyzer script modules."""

import asyncio
from unittest import mock

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import unittest

import instruction_coverage
import models



class InstructionCoverageTest(unittest.TestCase):

  @mock.patch(
      "instruction_coverage._call_generate_embeddings"
  )
  def test_get_batch_embeddings_success(self, mock_call_embeddings):
    # Mock embedding output matching batch size
    mock_call_embeddings.return_value = [[0.1, 0.2], [0.3, 0.4]]

    sem = asyncio.Semaphore(1)
    client_mock = mock.MagicMock()

    embeddings = asyncio.run(
        instruction_coverage._get_batch_embeddings(
            client_mock, ["text1", "text2"], sem, errors=[]
        )
    )

    self.assertEqual(embeddings, [[0.1, 0.2], [0.3, 0.4]])

  @mock.patch(
      "instruction_coverage._call_generate_embeddings"
  )
  def test_get_batch_embeddings_mismatch_failsafe(self, mock_call_embeddings):
    # Mock empty response indicating API exception or failure
    mock_call_embeddings.return_value = []

    sem = asyncio.Semaphore(1)
    client_mock = mock.MagicMock()
    errors = []

    embeddings = asyncio.run(
        instruction_coverage._get_batch_embeddings(
            client_mock, ["text1", "text2"], sem, errors=errors
        )
    )

    # Asserts it safely returned None pads to prevent index shifting
    self.assertEqual(embeddings, [None, None])
    self.assertEqual(len(errors), 1)
    self.assertIn("Embeddings size mismatch", errors[0])

  @mock.patch(
      "instruction_coverage._process_segment"
  )
  def test_analyze_instruction_categories(self, unused_mock_process):
    segments = [
        models.InstructionSegment(
            agent="A",
            category=models.InstructionCategory.RULES,
            directive="x",
            quote="y",
            full_text="test text",
        )
    ]
    gemini_client = mock.MagicMock()
    out_segments = asyncio.run(
        instruction_coverage.analyze_instruction_categories(
            segments, gemini_client
        )
    )
    self.assertEqual(len(out_segments), 1)

  @mock.patch(
      "instruction_coverage._batch_generate_embeddings_async"
  )
  def test_extract_instruction_coverage(self, mock_generate_embeddings):
    segments = [
        models.InstructionSegment(
            agent="A",
            category=models.InstructionCategory.RULES,
            directive="x",
            quote="y",
            full_text="tool test",
        )
    ]
    mock_generate_embeddings.return_value = []

    gemini_client = mock.MagicMock()
    out_segs, out_cov = asyncio.run(
        instruction_coverage.extract_instruction_coverage(
            segments, [], {"my_tool"}, gemini_client
        )
    )
    self.assertEqual(len(out_segs), 1)
    self.assertEqual(len(out_cov), 0)

  @mock.patch(
      "instruction_coverage._process_agent"
  )
  def test_determine_desired_transfers_with_llm(self, unused_mock_process):
    gemini_client = mock.MagicMock()
    res = asyncio.run(
        instruction_coverage.determine_desired_transfers_with_llm(
            {}, [("A", "B")], gemini_client
        )
    )
    self.assertEqual(res, set())


if __name__ == "__main__":
  unittest.main()
