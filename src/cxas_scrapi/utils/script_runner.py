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

"""Loads and executes YAML conversation scripts against a CES agent."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console
from rich.table import Table

from cxas_scrapi.core.chat_session import ChatSession, TurnRecord


@dataclass
class TurnExpectation:
    """Expected outcome for a single turn."""

    agent_contains: str | None = None
    agent_not_contains: str | None = None
    tools_called: list[str] | None = None
    no_transfer: bool = False
    session_ended: bool | None = None


@dataclass
class ScriptTurn:
    """A single turn in a conversation script."""

    user: str
    expect: TurnExpectation | None = None


@dataclass
class ConversationScript:
    """A full conversation script loaded from YAML."""

    name: str
    turns: list[ScriptTurn]
    description: str = ""
    app_name: str | None = None
    channel: str | None = None


@dataclass
class TurnResult:
    """Result of executing a single script turn."""

    turn_index: int
    user_text: str
    agent_text: str
    tool_calls: list[dict]
    transfer: str | None
    session_ended: bool
    expectation_failures: list[str]
    passed: bool


@dataclass
class ScriptResult:
    """Result of executing a full conversation script."""

    script_name: str
    turns: list[TurnResult]
    passed: bool
    total_turns: int
    passed_turns: int
    failed_turns: int
    error: str | None = None


class ScriptRunner:
    """Loads and executes conversation scripts."""

    @staticmethod
    def load_script(path: str | Path) -> ConversationScript:
        """Load a YAML conversation script from disk.

        YAML format:
            name: "test name"
            description: "optional"
            app_name: "optional override"
            channel: "optional"
            turns:
              - user: "message text"
                expect:
                  agent_contains: "substring"
                  tools_called: ["tool1", "tool2"]
                  no_transfer: true
                  session_ended: false

        Args:
            path: Path to the YAML script file.

        Returns:
            A parsed ConversationScript.

        Raises:
            FileNotFoundError: If the path does not exist.
            ValueError: If the YAML structure is invalid.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Script file not found: {path}")

        with open(path, "r") as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            raise ValueError(f"Invalid script format in {path}: expected a YAML mapping")

        if "name" not in data:
            raise ValueError(f"Invalid script format in {path}: missing 'name' field")

        if "turns" not in data or not isinstance(data.get("turns"), list):
            raise ValueError(
                f"Invalid script format in {path}: missing or invalid 'turns' field"
            )

        turns = []
        for i, turn_data in enumerate(data["turns"]):
            if not isinstance(turn_data, dict) or "user" not in turn_data:
                raise ValueError(
                    f"Invalid turn {i} in {path}: each turn must have a 'user' field"
                )

            expect = None
            if "expect" in turn_data and turn_data["expect"] is not None:
                exp_data = turn_data["expect"]
                expect = TurnExpectation(
                    agent_contains=exp_data.get("agent_contains"),
                    agent_not_contains=exp_data.get("agent_not_contains"),
                    tools_called=exp_data.get("tools_called"),
                    no_transfer=exp_data.get("no_transfer", False),
                    session_ended=exp_data.get("session_ended"),
                )

            turns.append(ScriptTurn(user=turn_data["user"], expect=expect))

        return ConversationScript(
            name=data["name"],
            turns=turns,
            description=data.get("description", ""),
            app_name=data.get("app_name"),
            channel=data.get("channel"),
        )

    @staticmethod
    def load_scripts(paths: list[str | Path]) -> list[ConversationScript]:
        """Load multiple scripts. Supports glob patterns via Path.glob.

        Each entry in paths can be:
        - A direct file path (loaded as-is)
        - A glob pattern (e.g., "scripts/*.yaml") resolved from the pattern's parent

        Args:
            paths: List of file paths or glob patterns.

        Returns:
            List of parsed ConversationScripts.
        """
        scripts = []
        for p in paths:
            p = Path(p)
            if p.exists() and p.is_file():
                scripts.append(ScriptRunner.load_script(p))
            elif "*" in str(p) or "?" in str(p):
                # Treat as glob pattern relative to parent directory
                parent = p.parent if p.parent != p else Path(".")
                pattern = p.name
                matched = sorted(parent.glob(pattern))
                for match in matched:
                    scripts.append(ScriptRunner.load_script(match))
            else:
                # Try loading anyway; load_script will raise FileNotFoundError
                scripts.append(ScriptRunner.load_script(p))
        return scripts

    def __init__(
        self,
        app_name: str,
        channel: str | None = None,
        delay: float = 0.5,
        **session_kwargs: Any,
    ):
        self.app_name = app_name
        self.channel = channel
        self.delay = delay
        self.session_kwargs = session_kwargs

    def run_script(self, script: ConversationScript) -> ScriptResult:
        """Execute a single script and return results.

        Creates a new ChatSession per script. Evaluates expectations after each
        turn. Continues even if expectations fail (collects all failures). Stops
        early if the session ends unexpectedly.

        Args:
            script: The conversation script to execute.

        Returns:
            ScriptResult with all TurnResults.
        """
        effective_app = script.app_name or self.app_name
        effective_channel = script.channel or self.channel

        try:
            session = ChatSession(
                app_name=effective_app,
                channel=effective_channel,
                **self.session_kwargs,
            )
        except Exception as exc:
            return ScriptResult(
                script_name=script.name,
                turns=[],
                passed=False,
                total_turns=len(script.turns),
                passed_turns=0,
                failed_turns=0,
                error=f"Failed to create ChatSession: {exc}",
            )

        turn_results: list[TurnResult] = []

        try:
            for i, script_turn in enumerate(script.turns):
                # Check if session already ended before sending
                if session.is_ended:
                    # Mark remaining turns as skipped
                    for j in range(i, len(script.turns)):
                        turn_results.append(
                            TurnResult(
                                turn_index=j,
                                user_text=script.turns[j].user,
                                agent_text="",
                                tool_calls=[],
                                transfer=None,
                                session_ended=True,
                                expectation_failures=["Skipped: session ended early"],
                                passed=False,
                            )
                        )
                    break

                try:
                    turn_record = session.send(script_turn.user)
                except Exception as exc:
                    # Record the error and stop execution
                    turn_results.append(
                        TurnResult(
                            turn_index=i,
                            user_text=script_turn.user,
                            agent_text="",
                            tool_calls=[],
                            transfer=None,
                            session_ended=False,
                            expectation_failures=[f"Exception during send: {exc}"],
                            passed=False,
                        )
                    )
                    break

                # Check expectations
                failures = []
                if script_turn.expect is not None:
                    failures = ScriptRunner.check_expectations(
                        turn_record, script_turn.expect
                    )

                turn_results.append(
                    TurnResult(
                        turn_index=i,
                        user_text=script_turn.user,
                        agent_text=turn_record.agent_text,
                        tool_calls=turn_record.tool_calls,
                        transfer=turn_record.agent_transfer,
                        session_ended=turn_record.session_ended,
                        expectation_failures=failures,
                        passed=len(failures) == 0,
                    )
                )

                # Delay between turns (skip after the last turn)
                if i < len(script.turns) - 1 and self.delay > 0:
                    time.sleep(self.delay)

        finally:
            try:
                session.close()
            except Exception:
                pass

        passed_count = sum(1 for t in turn_results if t.passed)
        failed_count = sum(1 for t in turn_results if not t.passed)

        return ScriptResult(
            script_name=script.name,
            turns=turn_results,
            passed=failed_count == 0 and len(turn_results) == len(script.turns),
            total_turns=len(script.turns),
            passed_turns=passed_count,
            failed_turns=failed_count,
        )

    def run_batch(
        self,
        scripts: list[ConversationScript],
        max_workers: int = 1,
    ) -> list[ScriptResult]:
        """Run multiple scripts sequentially or in parallel.

        Args:
            scripts: List of scripts to execute.
            max_workers: Number of parallel workers. 1 = sequential.

        Returns:
            List of ScriptResults in the same order as input scripts.
        """
        if max_workers <= 1:
            return [self.run_script(s) for s in scripts]

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            results = list(executor.map(self.run_script, scripts))
        return results

    @staticmethod
    def check_expectations(
        turn: TurnRecord,
        expect: TurnExpectation,
    ) -> list[str]:
        """Check a turn against expectations, return list of failures.

        Checks:
        - agent_contains: turn.agent_text must contain the substring
          (case-insensitive)
        - agent_not_contains: turn.agent_text must NOT contain the substring
          (case-insensitive)
        - tools_called: each expected tool must appear in
          turn.tool_calls[*]["action"]
        - no_transfer: turn.agent_transfer must be None/falsy
        - session_ended: turn.session_ended must match expected value

        Args:
            turn: The TurnRecord from the ChatSession.
            expect: The TurnExpectation to check against.

        Returns:
            List of failure description strings. Empty list means all passed.
        """
        failures = []

        if expect.agent_contains is not None:
            if expect.agent_contains.lower() not in turn.agent_text.lower():
                failures.append(
                    f"agent_contains: expected '{expect.agent_contains}' "
                    f"in agent text, got: '{turn.agent_text[:100]}'"
                )

        if expect.agent_not_contains is not None:
            if expect.agent_not_contains.lower() in turn.agent_text.lower():
                failures.append(
                    f"agent_not_contains: expected '{expect.agent_not_contains}' "
                    f"NOT in agent text, but found it"
                )

        if expect.tools_called is not None:
            actual_tools = {tc.get("action", "") for tc in turn.tool_calls}
            for expected_tool in expect.tools_called:
                if expected_tool not in actual_tools:
                    failures.append(
                        f"tools_called: expected tool '{expected_tool}' not found "
                        f"in actual tools: {sorted(actual_tools)}"
                    )

        if expect.no_transfer:
            if turn.agent_transfer:
                failures.append(
                    f"no_transfer: expected no transfer, "
                    f"but got transfer to '{turn.agent_transfer}'"
                )

        if expect.session_ended is not None:
            if turn.session_ended != expect.session_ended:
                failures.append(
                    f"session_ended: expected {expect.session_ended}, "
                    f"got {turn.session_ended}"
                )

        return failures

    @staticmethod
    def results_to_table(results: list[ScriptResult]) -> str:
        """Render batch results as a Rich table string.

        Shows: | Script | Turns | Passed | Failed | Status |

        Args:
            results: List of ScriptResults to render.

        Returns:
            String containing the rendered Rich table.
        """
        table = Table(title="Script Results")
        table.add_column("Script", style="cyan")
        table.add_column("Turns", justify="right")
        table.add_column("Passed", justify="right", style="green")
        table.add_column("Failed", justify="right", style="red")
        table.add_column("Status")

        for result in results:
            status = "[green]PASS[/green]" if result.passed else "[red]FAIL[/red]"
            if result.error:
                status = "[red]ERROR[/red]"
            table.add_row(
                result.script_name,
                str(result.total_turns),
                str(result.passed_turns),
                str(result.failed_turns),
                status,
            )

        buf = StringIO()
        console = Console(file=buf, force_terminal=False, width=120)
        console.print(table)
        return buf.getvalue()

    @staticmethod
    def results_to_json(results: list[ScriptResult]) -> str:
        """Render batch results as JSON with full turn details.

        Args:
            results: List of ScriptResults to render.

        Returns:
            JSON string of the results.
        """
        output = []
        for result in results:
            turns_data = []
            for turn in result.turns:
                turns_data.append(
                    {
                        "turn_index": turn.turn_index,
                        "user_text": turn.user_text,
                        "agent_text": turn.agent_text,
                        "tool_calls": turn.tool_calls,
                        "transfer": turn.transfer,
                        "session_ended": turn.session_ended,
                        "expectation_failures": turn.expectation_failures,
                        "passed": turn.passed,
                    }
                )
            output.append(
                {
                    "script_name": result.script_name,
                    "passed": result.passed,
                    "total_turns": result.total_turns,
                    "passed_turns": result.passed_turns,
                    "failed_turns": result.failed_turns,
                    "error": result.error,
                    "turns": turns_data,
                }
            )
        return json.dumps(output, indent=2)
