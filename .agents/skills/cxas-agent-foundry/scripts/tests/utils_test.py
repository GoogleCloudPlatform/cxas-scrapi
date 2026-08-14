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

"""Tests for GECX evaluation coverage analyzer script utils."""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import unittest

import models
import utils



class UtilsTest(unittest.TestCase):

  def test_parse_instruction_content_xml(self):
    content = """
    <Rules>
    1. First instruction rule.
    2. Second instruction rule.
    </Rules>
    <Functional_Intent>
    * Perform search capability.
    </Functional_Intent>
    """
    segments = utils.parse_instruction_content(content, "TestAgent")
    self.assertEqual(len(segments), 3)

    # Check first segment
    self.assertEqual(segments[0].agent, "TestAgent")
    self.assertEqual(segments[0].category, models.InstructionCategory.RULES)
    self.assertEqual(segments[0].full_text, "First instruction rule.")

    # Check third segment
    self.assertEqual(
        segments[2].category, models.InstructionCategory.FUNCTIONAL_INTENT
    )
    self.assertEqual(segments[2].full_text, "Perform search capability.")

  def test_parse_instruction_content_fallback(self):
    content = "Just some raw unstructured text.\nLine two."
    segments = utils.parse_instruction_content(content, "TestAgent")
    self.assertEqual(len(segments), 1)
    self.assertEqual(segments[0].category, models.InstructionCategory.RULES)

  def test_parse_instruction_content_directive_and_quote(self):
    # Short content: directive fits within q_text and has no trailing '...'
    short_content = "1. Short directive text here."
    segments = utils.parse_instruction_content(short_content, "TestAgent")
    self.assertEqual(len(segments), 1)
    self.assertEqual(segments[0].directive, "Short directive text here.")
    self.assertEqual(segments[0].quote, '"Short directive text here."')

    # Medium content: > 5 words, so directive should be truncated with '...'
    medium_content = "1. This directive text has more than five words in it."
    segments = utils.parse_instruction_content(medium_content, "TestAgent")
    self.assertEqual(len(segments), 1)
    self.assertEqual(segments[0].directive, "This directive text has more...")
    self.assertEqual(
        segments[0].quote,
        '"This directive text has more than five words in it."',
    )

    # Long content: > 200 character content, quote should be truncated
    # with '...'.
    long_content = "1. " + " ".join(["word"] * 50)  # ~250 chars
    segments = utils.parse_instruction_content(long_content, "TestAgent")
    self.assertEqual(len(segments), 1)
    self.assertEqual(segments[0].directive, "word word word word word...")
    expected_quote = f'"{segments[0].full_text[:200]}..."'
    self.assertEqual(segments[0].quote, expected_quote)

  def test_cosine_similarity_identical(self):
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    similarity = utils.cosine_similarity(v1, v2)
    self.assertAlmostEqual(similarity, 1.0)

  def test_cosine_similarity_orthogonal(self):
    v1 = [1.0, 0.0]
    v2 = [0.0, 1.0]
    similarity = utils.cosine_similarity(v1, v2)
    self.assertAlmostEqual(similarity, 0.0)

  def test_cosine_similarity_zero_vector(self):
    v1 = [0.0, 0.0]
    v2 = [1.0, 1.0]
    similarity = utils.cosine_similarity(v1, v2)
    self.assertEqual(similarity, 0.0)

  def test_find_target_agent(self):
    obj = {
        "someKey": "value",
        "targetAgent": "AgentA",
        "nested": [{"targetAgent": "AgentB"}, {"other": "AgentC"}],
    }
    targets = utils.find_target_agent(obj)
    self.assertEqual(targets, ["AgentA", "AgentB"])

  def test_dot_product(self):
    self.assertAlmostEqual(utils.dot_product([1.0, 2.0], [3.0, 4.0]), 11.0)

  def test_magnitude(self):
    self.assertAlmostEqual(utils.magnitude([3.0, 4.0]), 5.0)


if __name__ == "__main__":
  unittest.main()
