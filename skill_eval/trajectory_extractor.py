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

"""Extracts tool calls and thoughts from public Antigravity SDK history."""

from typing import Any

from skill_eval import benchmark


class TrajectoryExtractor:
    """Extracts trajectory events from public Antigravity SDK Step history."""

    def __init__(self) -> None:
        self._last_index = 0
        self._emitted_calls = set()

    def extract_new_events(
        self,
        history: list[Any],
    ) -> list[benchmark.TrajectoryEvent]:
        """Extracts new trajectory events from the conversation history."""
        events: list[benchmark.TrajectoryEvent] = []
        start_idx = self._last_index
        self._last_index = len(history)

        for i in range(start_idx, len(history)):
            step = history[i]

            # Extract Thoughts
            if hasattr(step, "thinking") and step.thinking:
                events.append(
                    benchmark.AgentMessageEvent(
                        text=step.thinking.strip(),
                        is_thought=True,
                        source="main",
                    )
                )

            # Extract Tool Calls and Tool Results
            if hasattr(step, "tool_calls") and step.tool_calls:
                for call in step.tool_calls:
                    call_id = (
                        call.id
                        or step.id
                        or f"step_{getattr(step, 'step_index', i)}"
                    )

                    # Check if it's a subagent spawn
                    if call.name == "start_subagent":
                        status_val = getattr(step, "status", "")
                        if hasattr(status_val, "value"):
                            status_val = status_val.value

                        # If we haven't emitted the spawn/call event yet, do so first
                        if call_id not in self._emitted_calls:
                            self._emitted_calls.add(call_id)
                            subagent_id = (
                                call.args.get("trajectory_id") or call_id
                            )
                            events.append(
                                benchmark.ToolCallEvent(
                                    call_id=call_id,
                                    name=call.name,
                                    args=call.args,
                                    source="main",
                                )
                            )
                            events.append(
                                benchmark.SubagentEvent(
                                    subagent_id=subagent_id,
                                    action="spawned",
                                    prompt=call.args.get("prompt"),
                                    source="main",
                                )
                            )

                        # If the step is completed, emit finished events
                        if status_val in ("DONE", "ERROR"):
                            subagent_id = (
                                call.args.get("trajectory_id") or call_id
                            )
                            events.append(
                                benchmark.ToolResultEvent(
                                    call_id=call_id,
                                    output=getattr(step, "content", "")
                                    or getattr(step, "error", "")
                                    or "Success",
                                    is_error=(status_val == "ERROR"),
                                    source="main",
                                )
                            )
                            events.append(
                                benchmark.SubagentEvent(
                                    subagent_id=subagent_id,
                                    action="finished",
                                    source="main",
                                )
                            )
                    else:
                        # Regular tool
                        status_val = getattr(step, "status", "")
                        if hasattr(status_val, "value"):
                            status_val = status_val.value

                        # Emit ToolCallEvent if not yet emitted
                        if call_id not in self._emitted_calls:
                            self._emitted_calls.add(call_id)
                            events.append(
                                benchmark.ToolCallEvent(
                                    call_id=call_id,
                                    name=call.name,
                                    args=call.args,
                                    source="main",
                                )
                            )

                        # Emit ToolResultEvent if completed
                        if status_val in ("DONE", "ERROR"):
                            events.append(
                                benchmark.ToolResultEvent(
                                    call_id=call_id,
                                    output=getattr(step, "content", "")
                                    or getattr(step, "error", "")
                                    or "Success",
                                    is_error=(status_val == "ERROR"),
                                    source="main",
                                )
                            )

        return events
