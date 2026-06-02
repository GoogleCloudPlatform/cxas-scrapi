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

from skill_eval import benchmark, trajectory_extractor


class MockToolCall:
    def __init__(self, name, args, id=None):
        self.name = name
        self.args = args
        self.id = id


class MockStep:
    def __init__(
        self,
        step_index,
        thinking="",
        tool_calls=None,
        status="UNKNOWN",
        content="",
        error="",
        id="",
    ):
        self.step_index = step_index
        self.thinking = thinking
        self.tool_calls = tool_calls or []
        self.status = status
        self.content = content
        self.error = error
        self.id = id


class TrajectoryExtractorTest(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.extractor = trajectory_extractor.TrajectoryExtractor()

    def test_extract_new_events_empty(self):
        res = self.extractor.extract_new_events([])
        self.assertEqual(res, [])

    def test_extract_new_events_thinking_and_tool_call(self):
        step = MockStep(
            step_index=1,
            thinking="I need to check files.",
            tool_calls=[
                MockToolCall(
                    name="list_directory",
                    args={"directory_path": "/tmp"},
                    id="call_1",
                )
            ],
            status="ACTIVE",
            id="step_1",
        )
        res = self.extractor.extract_new_events([step])
        self.assertEqual(len(res), 2)

        event0 = res[0]
        self.assertIsInstance(event0, benchmark.AgentMessageEvent)
        assert isinstance(event0, benchmark.AgentMessageEvent)
        self.assertEqual(event0.text, "I need to check files.")
        self.assertTrue(event0.is_thought)

        event1 = res[1]
        self.assertIsInstance(event1, benchmark.ToolCallEvent)
        assert isinstance(event1, benchmark.ToolCallEvent)
        self.assertEqual(event1.name, "list_directory")
        self.assertEqual(event1.args, {"directory_path": "/tmp"})
        self.assertEqual(event1.call_id, "call_1")

    def test_extract_new_events_tool_result(self):
        # Step 1: Tool call (Active)
        step1 = MockStep(
            step_index=1,
            tool_calls=[
                MockToolCall(
                    name="view_file", args={"path": "test.txt"}, id="call_1"
                )
            ],
            status="ACTIVE",
            id="step_1",
        )
        # Step 2: Tool call completed (DONE)
        step2 = MockStep(
            step_index=2,
            tool_calls=[
                MockToolCall(
                    name="view_file", args={"path": "test.txt"}, id="call_1"
                )
            ],
            status="DONE",
            content="Hello World",
            id="step_2",
        )

        res1 = self.extractor.extract_new_events([step1])
        self.assertEqual(len(res1), 1)
        self.assertIsInstance(res1[0], benchmark.ToolCallEvent)

        res2 = self.extractor.extract_new_events([step1, step2])
        self.assertEqual(len(res2), 1)
        event2 = res2[0]
        self.assertIsInstance(event2, benchmark.ToolResultEvent)
        assert isinstance(event2, benchmark.ToolResultEvent)
        self.assertEqual(event2.call_id, "call_1")
        self.assertEqual(event2.output, "Hello World")
        self.assertFalse(event2.is_error)

    def test_extract_subagent_spawned_and_finished(self):
        step = MockStep(
            step_index=1,
            tool_calls=[
                MockToolCall(
                    name="start_subagent",
                    args={"prompt": "Research this", "trajectory_id": "sub_1"},
                    id="call_sub",
                )
            ],
            status="DONE",
            content="Synthesized research summary",
            id="step_sub",
        )
        res = self.extractor.extract_new_events([step])
        self.assertEqual(len(res), 4)

        # ToolCallEvent
        event0 = res[0]
        self.assertIsInstance(event0, benchmark.ToolCallEvent)
        assert isinstance(event0, benchmark.ToolCallEvent)
        self.assertEqual(event0.name, "start_subagent")

        # SubagentEvent (spawned)
        event1 = res[1]
        self.assertIsInstance(event1, benchmark.SubagentEvent)
        assert isinstance(event1, benchmark.SubagentEvent)
        self.assertEqual(event1.action, "spawned")
        self.assertEqual(event1.subagent_id, "sub_1")
        self.assertEqual(event1.prompt, "Research this")

        # ToolResultEvent
        event2 = res[2]
        self.assertIsInstance(event2, benchmark.ToolResultEvent)
        assert isinstance(event2, benchmark.ToolResultEvent)
        self.assertEqual(event2.output, "Synthesized research summary")

        # SubagentEvent (finished)
        event3 = res[3]
        self.assertIsInstance(event3, benchmark.SubagentEvent)
        assert isinstance(event3, benchmark.SubagentEvent)
        self.assertEqual(event3.action, "finished")
        self.assertEqual(event3.subagent_id, "sub_1")


if __name__ == "__main__":
    unittest.main()
