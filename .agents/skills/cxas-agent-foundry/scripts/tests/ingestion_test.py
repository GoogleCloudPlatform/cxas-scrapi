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

"""Tests for GECX evaluation coverage analyzer script ingestion."""

import json
import pathlib
import tempfile
from unittest import mock

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import unittest
import yaml

import ingestion
import models



class IngestionTest(unittest.TestCase):

  def test_find_tools_local(self):
    with tempfile.TemporaryDirectory() as tmp_dir:
      tools_path = pathlib.Path(tmp_dir)
      tool_file_1 = tools_path / "helper.json"
      tool_file_2 = tools_path / "query.yaml"

      tool_file_1.write_text(
          json.dumps({"displayName": "CustomHelper"}), encoding="utf-8"
      )
      tool_file_2.write_text("name: custom_query", encoding="utf-8")

      tools = ingestion.find_tools_local(tools_path)
      self.assertEqual(tools, {"CustomHelper", "custom_query"})

  def test_discover_eval_files(self):
    with tempfile.TemporaryDirectory() as tmp_dir:
      agent_path = pathlib.Path(tmp_dir)
      data = models.AgentProjectData(agent_dir=agent_path)

      directories = [
          "evaluations",
          "evaluationDatasets",
          "tool_tests",
          "evals",
          "tests",
      ]
      for d in directories:
        subdir = agent_path / d
        subdir.mkdir(parents=True)
        (subdir / "eval1.json").touch()
        (subdir / "eval2.yaml").touch()
        (subdir / "eval3.yml").touch()
        (subdir / "eval4.txt").touch()

      (agent_path / "root1.yaml").touch()
      (agent_path / "root2.yml").touch()
      (agent_path / "root_ignored.json").touch()

      ingestion._discover_eval_files(agent_path, data)

      self.assertEqual(len(data.eval_files), 17)
      eval_filenames = {p.name for p in data.eval_files}
      self.assertIn("eval1.json", eval_filenames)
      self.assertIn("eval2.yaml", eval_filenames)
      self.assertIn("eval3.yml", eval_filenames)
      self.assertIn("root1.yaml", eval_filenames)
      self.assertIn("root2.yml", eval_filenames)
      self.assertNotIn("eval4.txt", eval_filenames)
      self.assertNotIn("root_ignored.json", eval_filenames)

  def test_parse_agents_config(self):
    with tempfile.TemporaryDirectory() as tmp_dir:
      agent_path = pathlib.Path(tmp_dir)
      agents_dir = agent_path / "agents"
      agents_dir.mkdir()

      # Agent 1 (JSON)
      agent1_file = agents_dir / "agent1.json"
      agent1_data = {
          "displayName": "RootAgent",
          "childAgents": ["ChildAgent1", "ChildAgent2"],
      }
      agent1_file.write_text(json.dumps(agent1_data), encoding="utf-8")

      # Agent 2 (YAML)
      agent2_file = agents_dir / "agent2.yaml"
      agent2_data = {
          "displayName": "ChildAgent1",
          "childAgents": ["SubChildAgent"],
      }
      agent2_file.write_text(yaml.dump(agent2_data), encoding="utf-8")

      # Agent 3 (YML)
      agent3_file = agents_dir / "agent3.yml"
      agent3_data = {
          "displayName": "ChildAgent2",
      }
      agent3_file.write_text(yaml.dump(agent3_data), encoding="utf-8")

      # Agent 4 (Malformed YAML to test exception handling)
      bad_agent_file = agents_dir / "bad.yaml"
      bad_agent_file.write_text(
          "displayName: : : raw invalid", encoding="utf-8"
      )

      data = models.AgentProjectData(agent_dir=agent_path)
      root_agent = ingestion._parse_agents_config(agent_path, data)

      self.assertEqual(root_agent, "RootAgent")
      self.assertEqual(data.agent_directories["RootAgent"], agents_dir)
      self.assertIn(("RootAgent", "ChildAgent1"), data.declared_transfers)
      self.assertIn(("RootAgent", "ChildAgent2"), data.declared_transfers)
      self.assertIn(("ChildAgent1", "SubChildAgent"), data.declared_transfers)
      self.assertIn(("ChildAgent1", "ChildAgent2"), data.declared_transfers)
      self.assertIn(("ChildAgent2", "ChildAgent1"), data.declared_transfers)

  def test_parse_evaluations(self):
    with tempfile.TemporaryDirectory() as tmp_dir:
      evals_dir = pathlib.Path(tmp_dir)

      # 1. Tool test evaluation file (JSON)
      f_tool = evals_dir / "tool_test.json"
      f_tool.write_text(
          json.dumps({
              "displayName": "ToolTestSuite",
              "tests": [{
                  "name": "test_t1",
                  "tool": "tool_1",
                  "args": {"q": "search"},
              }],
          }),
          encoding="utf-8",
      )

      # 2. GECX Native Golden evaluation file (YAML)
      f_golden = evals_dir / "native_golden.yaml"
      golden_yaml = """
golden:
  turns:
    - steps:
        - userInput:
            text: "Hello"
          expectation:
            toolCall:
              tool: "tool_2"
            note: "Important note"
            agentTransfer:
              targetAgent: "AgentB"
            updatedVariables:
              var_1: "val_1"
"""
      f_golden.write_text(golden_yaml, encoding="utf-8")

      # 3. GECX Native Simulation evaluation file (YAML)
      f_simulation = evals_dir / "native_simulation.yaml"
      sim_yaml = """
scenario:
  task: "Complete reservation"
  userFacts:
    - name: "email"
      value: "test@example.com"
"""
      f_simulation.write_text(sim_yaml, encoding="utf-8")

      # 4. SCRAPI Golden evaluation file (JSON)
      f_scrapi_golden = evals_dir / "scrapi_golden.json"
      scrapi_golden_json = {
          "conversations": [{
              "conversation": "scrapi_c1",
              "tags": ["critical"],
              "turns": [{
                  "user": "I want to pay",
                  "agent": "Ok",
                  "tool_calls": [{"action": "tool_3"}],
              }],
              "expectations": ["Ensure payment is processed"],
          }]
      }
      f_scrapi_golden.write_text(
          json.dumps(scrapi_golden_json), encoding="utf-8"
      )

      # 5. SCRAPI Simulation evaluation file (YAML)
      f_scrapi_sim = evals_dir / "scrapi_sim.yaml"
      scrapi_sim_yaml = """
evals:
  - name: "scrapi_s1"
    tags: ["regression"]
    steps:
      - goal: "Test tool_4 usage"
        success_criteria: "User sees tool_4 result"
        response_guide: "Provide assistance"
    expectations:
      - "Success criteria for tool_4 met"
"""
      f_scrapi_sim.write_text(scrapi_sim_yaml, encoding="utf-8")

      eval_files = [
          f_tool,
          f_golden,
          f_simulation,
          f_scrapi_golden,
          f_scrapi_sim,
      ]

      data = models.AgentProjectData(
          agent_dir=evals_dir,
          all_tools={"tool_1", "tool_2", "tool_3", "tool_4", "end_session"},
      )

      ingestion._parse_evaluations(
          eval_files, data, default_root_agent="AgentA"
      )

      # Assert Tool test extraction
      self.assertEqual(data.covered_tools, {"tool_1"})
      self.assertIn("tool_1", data.called_tools)

      # Assert GECX Native Golden expectations & targetAgent tracking
      self.assertIn("tool_2", data.called_tools)
      self.assertIn(("AgentA", "AgentB"), data.covered_transfers)

      # Assert SCRAPI Golden tool extraction
      self.assertIn("tool_3", data.called_tools)

      # Assert SCRAPI Simulation regex tool matching
      self.assertIn("tool_4", data.called_tools)

      # Check chunks text content has expected data
      chunk_texts = [c["text"] for c in data.eval_chunks]
      self.assertTrue(any("Tool Test" in text for text in chunk_texts))
      self.assertTrue(any("Native Eval" in text for text in chunk_texts))
      self.assertTrue(any("Native Simulation" in text for text in chunk_texts))
      self.assertTrue(any("Conversation" in text for text in chunk_texts))
      self.assertTrue(any("Simulation Eval" in text for text in chunk_texts))

  def test_parse_callbacks(self):
    with tempfile.TemporaryDirectory() as tmp_dir:
      agent_path = pathlib.Path(tmp_dir)
      agents_dir = agent_path / "agents"
      agents_dir.mkdir()

      # Callback 1: Has python_code.py and test_file.py
      cb1_dir = agents_dir / "my_callbacks" / "cb_one"
      cb1_dir.mkdir(parents=True)
      (cb1_dir / "python_code.py").touch()
      (cb1_dir / "test_cb_one.py").touch()

      # Callback 2: Has python_code.py but NO test file
      cb2_dir = agents_dir / "other_callbacks" / "cb_two"
      cb2_dir.mkdir(parents=True)
      (cb2_dir / "python_code.py").touch()

      # Callback 3: Match name containing 'callbacks' but NO python_code.py
      # (should be ignored)
      cb3_dir = agents_dir / "fake_callbacks" / "cb_three"
      cb3_dir.mkdir(parents=True)
      (cb3_dir / "somefile.txt").touch()

      data = models.AgentProjectData(agent_dir=agent_path)
      ingestion._parse_callbacks(agent_path, data)

      self.assertIn("my_callbacks/cb_one", data.all_callbacks)
      self.assertIn("my_callbacks/cb_one", data.covered_callbacks)

      self.assertIn("other_callbacks/cb_two", data.all_callbacks)
      self.assertNotIn("other_callbacks/cb_two", data.covered_callbacks)

      self.assertNotIn("fake_callbacks/cb_three", data.all_callbacks)

  def test_parse_callbacks_not_exist(self):
    with tempfile.TemporaryDirectory() as tmp_dir:
      agent_path = pathlib.Path(tmp_dir)
      data = models.AgentProjectData(agent_dir=agent_path)
      # agents directory does not exist, so _parse_callbacks should not
      # call glob.
      with mock.patch.object(
          pathlib.Path,
          "glob",
          side_effect=AssertionError("glob should not be called"),
      ):
        ingestion._parse_callbacks(agent_path, data)
        self.assertFalse(data.all_callbacks)

  def test_ingest_agent_project(self):
    with tempfile.TemporaryDirectory() as tmp_dir:
      agent_path = pathlib.Path(tmp_dir)

      # 1. Tools
      tools_dir = agent_path / "tools"
      tools_dir.mkdir()
      (tools_dir / "tool_a.json").write_text(
          json.dumps({"displayName": "tool_a"}), encoding="utf-8"
      )

      # 2. Agents
      agents_dir = agent_path / "agents"
      agents_dir.mkdir()

      # Agent config
      agent_conf = agents_dir / "root_agent.json"
      agent_conf.write_text(
          json.dumps({"displayName": "MainAgent"}), encoding="utf-8"
      )

      # Instructions
      sub_agent_dir = agents_dir / "MainAgent"
      sub_agent_dir.mkdir()
      (sub_agent_dir / "instruction.txt").write_text(
          "<Rules>Always do A.</Rules>", encoding="utf-8"
      )

      # Global instruction
      (agent_path / "global_instruction.txt").write_text(
          "Global rules", encoding="utf-8"
      )

      # 3. Evaluations
      evals_dir = agent_path / "evals"
      evals_dir.mkdir()
      (evals_dir / "e1.yaml").write_text(
          "displayName: eval_test\n"
          "tests:\n"
          "  - name: test_t1\n"
          "    tool: tool_a\n"
          "    args: {}",
          encoding="utf-8",
      )

      # Call ingest_agent_project
      data = ingestion.ingest_agent_project(agent_path)

      self.assertEqual(data.all_tools, {"tool_a"})
      self.assertIn(evals_dir / "e1.yaml", data.eval_files)
      self.assertEqual(data.covered_tools, {"tool_a"})
      self.assertIn("tool_a", data.called_tools)
      self.assertEqual(
          data.instruction_files,
          [
              sub_agent_dir / "instruction.txt",
              agent_path / "global_instruction.txt",
          ],
      )
      self.assertEqual(len(data.instruction_segments), 2)
      self.assertEqual(data.instruction_segments[0].agent, "MainAgent")
      self.assertEqual(
          data.instruction_segments[0].category,
          models.InstructionCategory.RULES,
      )
      self.assertEqual(data.instruction_segments[0].full_text, "Always do A.")
      self.assertEqual(data.instruction_segments[1].agent, "Global")
      self.assertEqual(data.instruction_segments[1].full_text, "Global rules")


if __name__ == "__main__":
  unittest.main()
