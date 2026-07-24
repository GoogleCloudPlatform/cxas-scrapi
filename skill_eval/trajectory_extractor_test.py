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

import typing
import unittest

from skill_eval import benchmark, trajectory_extractor


class MockToolCall:
    def __init__(
        self, name: typing.Any, args: typing.Any, id: typing.Any = None
    ) -> None:
        self.name = name
        self.args = args
        self.id = id


class MockStep:
    def __init__(
        self,
        step_index: typing.Any,
        thinking: typing.Any = "",
        tool_calls: typing.Any = None,
        status: typing.Any = "UNKNOWN",
        content: typing.Any = "",
        error: typing.Any = "",
        id: typing.Any = "",
    ) -> None:
        self.step_index = step_index
        self.thinking = thinking
        self.tool_calls = tool_calls or []
        self.status = status
        self.content = content
        self.error = error
        self.id = id


class TrajectoryExtractorTest(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.extractor = trajectory_extractor.TrajectoryExtractor()

    def test_extract_new_events_empty(self) -> None:
        res = self.extractor.extract_new_events([])
        assert res == []

    def test_extract_new_events_thinking_and_tool_call(self) -> None:
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
        assert len(res) == 2

        event0 = res[0]
        assert isinstance(event0, benchmark.AgentMessageEvent)
        assert isinstance(event0, benchmark.AgentMessageEvent)
        assert event0.text == "I need to check files."
        assert event0.is_thought

        event1 = res[1]
        assert isinstance(event1, benchmark.ToolCallEvent)
        assert isinstance(event1, benchmark.ToolCallEvent)
        assert event1.name == "list_directory"
        assert event1.args == {"directory_path": "/tmp"}
        assert event1.call_id == "call_1"

    def test_extract_new_events_tool_result(self) -> None:
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
        assert len(res1) == 1
        assert isinstance(res1[0], benchmark.ToolCallEvent)

        res2 = self.extractor.extract_new_events([step1, step2])
        assert len(res2) == 1
        event2 = res2[0]
        assert isinstance(event2, benchmark.ToolResultEvent)
        assert isinstance(event2, benchmark.ToolResultEvent)
        assert event2.call_id == "call_1"
        assert event2.output == "Hello World"
        assert not event2.is_error

    def test_extract_subagent_spawned_and_finished(self) -> None:
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
        assert len(res) == 4

        # ToolCallEvent
        event0 = res[0]
        assert isinstance(event0, benchmark.ToolCallEvent)
        assert isinstance(event0, benchmark.ToolCallEvent)
        assert event0.name == "start_subagent"

        # SubagentEvent (spawned)
        event1 = res[1]
        assert isinstance(event1, benchmark.SubagentEvent)
        assert isinstance(event1, benchmark.SubagentEvent)
        assert event1.action == "spawned"
        assert event1.subagent_id == "sub_1"
        assert event1.prompt == "Research this"

        # ToolResultEvent
        event2 = res[2]
        assert isinstance(event2, benchmark.ToolResultEvent)
        assert isinstance(event2, benchmark.ToolResultEvent)
        assert event2.output == "Synthesized research summary"

        # SubagentEvent (finished)
        event3 = res[3]
        assert isinstance(event3, benchmark.SubagentEvent)
        assert isinstance(event3, benchmark.SubagentEvent)
        assert event3.action == "finished"
        assert event3.subagent_id == "sub_1"


if __name__ == "__main__":
    unittest.main()
