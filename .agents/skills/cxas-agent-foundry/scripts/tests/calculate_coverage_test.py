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

"""Tests for GECX evaluation coverage analyzer script executing logic."""

import pathlib
import tempfile
from unittest import mock

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import unittest

import calculate_coverage



class CalculateCoverageTest(unittest.TestCase):

  def test_generate_json_report(self):
    with tempfile.TemporaryDirectory() as tmp_dir:
      out_path = pathlib.Path(tmp_dir) / "report.json"
      agent_path = pathlib.Path(tmp_dir)

      json_data = calculate_coverage.generate_json_report(
          output_file=out_path,
          total_tools={"tool_a"},
          covered_tools={"tool_a"},
          phantom_tools_by_file={},
          eval_files=[],
          declared_transfers=[("A", "B")],
          covered_transfers={("A", "B"): ["eval_1"]},
          instruction_segments=[],
          covered_instruction_segments=[],
          instruction_files=[],
          agent_dir=agent_path,
          total_callbacks=set(),
          covered_callbacks=set(),
          desired_transfers={("A", "B")},
          unused_evals=[],
      )
      self.assertTrue(out_path.exists())
      self.assertEqual(json_data["metrics"]["tool_coverage_percent"], 100.0)

  def test_generate_html_report(self):
    with tempfile.TemporaryDirectory() as tmp_dir:
      out_path = pathlib.Path(tmp_dir) / "report.html"
      json_data = {
          "generated_at": "2026-06-25 12:00:00",
          "metrics": {"tool_coverage_percent": 100.0},
      }
      calculate_coverage.generate_html_report(out_path, json_data)
      self.assertTrue(out_path.exists())
      html_content = out_path.read_text(encoding="utf-8")
      expected_strings = [
          "2026-06-25 12:00:00",
          '"tool_coverage_percent": 100.0',
          "<!DOCTYPE html>",
      ]
      for expected_string in expected_strings:
        with self.subTest(expected_string=expected_string):
          self.assertIn(expected_string, html_content)

  @mock.patch.object(
      calculate_coverage, "async_main", autospec=True, spec_set=True
  )
  def test_main(self, mock_async_main):
    calculate_coverage.main(["calculate_coverage.py"])
    mock_async_main.assert_called_once()


if __name__ == "__main__":
  unittest.main()
