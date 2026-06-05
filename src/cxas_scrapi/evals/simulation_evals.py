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

"""Eval conversation classes for CXAS Scrapi."""

import enum
import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import pandas as pd
import pydantic
import yaml
from rich.progress import Progress

from cxas_scrapi.core.apps import Apps
from cxas_scrapi.core.conversation_history import ConversationHistory
from cxas_scrapi.core.response_parser import ParsedSessionResponse
from cxas_scrapi.core.sessions import Sessions
from cxas_scrapi.core.tools import Tools
from cxas_scrapi.prompts import llm_user_prompts
from cxas_scrapi.utils.eval_utils import (
    Conversation as GoldenConversation,
)
from cxas_scrapi.utils.eval_utils import (
    Conversations as GoldenConversations,
)
from cxas_scrapi.utils.eval_utils import (
    ExpectationResult,
    ExpectationStatus,
    ToolCall,
    Turn,
    evaluate_expectations,
)
from cxas_scrapi.utils.gemini import GeminiGenerate
from cxas_scrapi.utils.rate_limiter import RateLimiter

_FIRST_UTTERANCE = "event: welcome"
_MAX_TURNS = 30
_DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"


class Step(pydantic.BaseModel):
    goal: str = ""
    success_criteria: str = ""
    response_guide: str = ""
    max_turns: int = 0

    static_utterance: str = ""
    # Variable injects are only supported for the first step
    inject_variables: dict[str, Any] = {}

    def model_dump(self, **kwargs):
        kwargs.setdefault("exclude_defaults", True)
        return super().model_dump(**kwargs)


class StepStatus(str, enum.Enum):
    NOT_STARTED = "Not Started"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"


class StepProgress(pydantic.BaseModel):
    step: Step = Step()
    status: StepStatus = StepStatus.NOT_STARTED
    justification: str = ""

    def model_dump(self, **kwargs):
        kwargs.setdefault("exclude_defaults", True)
        return super().model_dump(**kwargs)


class SimulationReport:
    """A report containing both Goals and Expectations DataFrames."""

    def __init__(
        self,
        goals_df: pd.DataFrame,
        expectations_df: pd.DataFrame | None = None,
    ):
        self.goals_df = goals_df
        self.expectations_df = expectations_df

    def __str__(self):
        green = "\033[1;32m"
        red = "\033[1;31m"
        reset = "\033[0m"

        goals_str = self.goals_df.to_string()
        goals_str = goals_str.replace("Completed", f"{green}Completed{reset}")
        goals_str = goals_str.replace("Not Started", f"{red}Not Started{reset}")
        goals_str = goals_str.replace(
            "In Progress", f"{green}In Progress{reset}"
        )

        res = "--- Goal Progress ---\n" + goals_str

        if self.expectations_df is not None:
            exp_str = self.expectations_df.to_string()
            exp_str = exp_str.replace("Not Met", f"{red}Not Met{reset}")
            exp_str = re.sub(r"(?<!Not )Met\b", f"{green}Met{reset}", exp_str)

            res += "\n\n--- Expectations ---\n" + exp_str

        return res

    def _repr_html_(self):
        html = "<h3>Goal Progress</h3>" + self.goals_df._repr_html_()
        if self.expectations_df is not None:
            html += "<h3>Expectations</h3>" + self.expectations_df._repr_html_()
        return html


class Conversation:
    """Base class for users."""

    def __init__(self):
        self.current_turn = 0
        self.utterance_turn = 0
        self.transcript = []

    def get_num_turns(self) -> int:
        """Gets the number of turns in the conversation."""
        return self.current_turn

    def get_transcript(self) -> str:
        """Gets the transcript of the conversation."""
        return "\n".join(self.transcript)

    def _add_agent_response(self, agent_response: str) -> None:
        """Adds an agent response to the transcript."""
        self.transcript.append(f"Agent: {agent_response}")

    def _add_user_utterance(self, user_utterance: str) -> None:
        """Adds a user utterance to the transcript."""
        self.transcript.append(f"User: {user_utterance}")

    def next_user_utterance(
        self, last_agent_response: str
    ) -> tuple[str, dict[str, Any]]:
        """Gets the next user utterance and variables to inject."""
        raise NotImplementedError

    def get_parsed_user_utterances(
        self,
    ) -> tuple[list[str], dict[str, str], dict[int, float]]:
        """Gets all user utterances."""
        raise NotImplementedError


class LLMUserConversation(Conversation):
    """An interactive user that provides input from the command line."""

    class Output(pydantic.BaseModel):
        next_user_utterance: str = ""
        step_progresses: list[StepProgress] = []

    def __init__(
        self,
        genai_client: GeminiGenerate,
        genai_model: str,
        test_case: dict[str, Any],
        max_turns: int = _MAX_TURNS,
    ):
        super().__init__()
        self.genai_client = genai_client
        self.genai_model = genai_model
        self.test_case = test_case
        self.max_turns = max_turns
        self.steps_progress = []
        for step in test_case["steps"]:
            self.steps_progress.append(
                StepProgress(
                    step=Step(
                        **{
                            k: v
                            for k, v in step.items()
                            if k != "inject_variables"
                        }
                    ),
                    status=StepStatus.NOT_STARTED,
                    justification="",
                )
            )
        self.expectations = test_case.get("expectations", [])
        self.expectation_results: list[ExpectationResult] = []

    def _check_conversation_status(self) -> bool:
        """Checks if the conversation should continue."""
        if self.current_turn >= self.max_turns:
            return False

        # If all steps are completed, then the conversation is complete.
        if all(
            item.status == StepStatus.COMPLETED for item in self.steps_progress
        ):
            return False

        return True

    def _get_active_step_index(self) -> int | None:
        """Finds the index of the first step that is not completed."""
        for i, prog in enumerate(self.steps_progress):
            if prog.status != StepStatus.COMPLETED:
                return i
        return None

    def _prepare_llm_prompt(self) -> str:
        """Prepares the prompt for the LLM user."""
        step_list = self.test_case["steps"]
        json_step_list = json.dumps(
            [Step(**s).model_dump() for s in step_list], indent=2
        )
        prompt = llm_user_prompts.LLM_USER_PROMPT.replace(
            "{input_user_config}",
            json_step_list,
        )
        prompt = prompt.replace(
            "{current_conversation_history}",
            self.get_transcript(),
        )
        step_progress_list = [step.model_dump() for step in self.steps_progress]
        json_step_progress_list = json.dumps(step_progress_list, indent=2)
        prompt = prompt.replace(
            "{current_step_progress}",
            json_step_progress_list,
        )
        return prompt

    def _next_user_utterance(self) -> tuple[str, dict[str, Any]]:
        """Generates the next user utterance and variables to inject based
        on the conversation history.

        This method uses an LLM to determine the next utterance, considering the
        current turn, maximum turns, and the completion status of the
        defined steps.

        Returns:
          - The generated next user utterance as a string. Returns an empty
            string if the conversation has reached the maximum number of
            turns or all steps are completed.
          - The variables to inject as a dict.
        """
        if not self._check_conversation_status():
            return "", {}

        active_idx = self._get_active_step_index()
        if active_idx is not None:
            active_step_prog = self.steps_progress[active_idx]
            if active_step_prog.step.static_utterance:
                # Mark static step as completed
                active_step_prog.status = StepStatus.COMPLETED
                active_step_prog.justification = (
                    "Static utterance sent (bypassed LLM)."
                )

                utterance = active_step_prog.step.static_utterance
                session_params = self.test_case.get("session_parameters", {})
                inject_vars = self.test_case["steps"][active_idx].get(
                    "inject_variables", {}
                )
                merged_vars = {**session_params, **inject_vars}
                return utterance, merged_vars

        if self.current_turn == 0:
            session_params = self.test_case.get("session_parameters", {})
            return _FIRST_UTTERANCE, session_params

        prompt = self._prepare_llm_prompt()

        output: LLMUserConversation.Output = self.genai_client.generate(
            prompt=prompt,
            model_name=self.genai_model,
            response_mime_type="application/json",
            response_schema=LLMUserConversation.Output,
        )

        if output:
            self.steps_progress = output.step_progresses
            return output.next_user_utterance, {}

        return "", {}

    def next_user_utterance(
        self, last_agent_response: str = ""
    ) -> tuple[str, dict[str, Any]]:
        """Returns the next user utterance from the LLM user."""
        if last_agent_response:
            self._add_agent_response(last_agent_response)
        next_user_utterance, variables_to_inject = self._next_user_utterance()
        self._add_user_utterance(next_user_utterance)
        self.current_turn += 1
        return next_user_utterance, variables_to_inject

    def generate_report(self) -> Any:
        """
        Generates a pandas DataFrame report of the conversation step
        progress.
        """
        records = []
        for prog in self.steps_progress:
            records.append(
                {
                    "goal": prog.step.goal,
                    "success_criteria": prog.step.success_criteria,
                    "status": prog.status.value,
                    "justification": prog.justification,
                }
            )
        goals_df = pd.DataFrame(records)

        expectations_df = None
        if self.expectation_results:
            exp_records = []
            for res in self.expectation_results:
                exp_records.append(
                    {
                        "expectation": res.expectation,
                        "status": res.status.value,
                        "justification": res.justification,
                    }
                )
            expectations_df = pd.DataFrame(exp_records)

        return SimulationReport(goals_df, expectations_df)


class SimulationEvals(Apps):
    """Wrapper class to simulate entire multi-turn conversations with a
    CXAS Agent."""

    max_retries: int = 3
    retry_delay_base: int = 2

    def __init__(
        self,
        app_name: str,
        rate_limiter: RateLimiter | None = None,
        **kwargs,
    ):
        self.app_name = app_name
        project_id = app_name.split("/")[1]
        location = app_name.split("/")[3]
        super().__init__(project_id=project_id, location=location, **kwargs)
        self.sessions_client = Sessions(
            app_name, rate_limiter=rate_limiter, **kwargs
        )
        self.tools_map = Tools(app_name=app_name, **kwargs).get_tools_map()

        # Vertex AI requires a specific region (e.g. global), whereas CXAS
        # Apps use 'us' or 'eu'
        vertex_location = "global"

        self.genai_client = GeminiGenerate(
            project_id=self.project_id,
            location=vertex_location,
            credentials=self.creds,
        )

    def _parse_agent_response(
        self, response: Any
    ) -> tuple[str, list[str], bool]:
        """Parses the agent response to extract text and trace information.

        Returns:
            A tuple of (agent_text, trace_chunks, session_ended)
        """
        parsed = ParsedSessionResponse(response, tools_map=self.tools_map)
        return (
            parsed.consolidated_agent_text,
            parsed.detailed_trace,
            parsed.session_ended,
        )

    def _evaluate_expectations(
        self,
        eval_conv: LLMUserConversation,
        detailed_trace: list[str],
        model: str,
        console_logging: bool,
    ) -> None:
        """Evaluates expectations against the conversation trace.

        Modifies `eval_conv.expectation_results` in place.
        """
        if eval_conv.expectations and isinstance(eval_conv.expectations, list):
            if console_logging:
                print("\nEvaluating Expectations...")

            eval_conv.expectation_results = evaluate_expectations(
                gemini_client=self.genai_client,
                model_name=model,
                trace=detailed_trace,
                expectations=eval_conv.expectations,
            )

    def _send_request_with_retry(
        self,
        session_id: str,
        user_utterance: str,
        variables: dict[str, Any],
        modality: str,
        console_logging: bool,
        background_noise_file: str | None = None,
        burst_noise_files: list[str] | None = None,
    ) -> Any:
        """Sends a request to the CES Agent with exponential backoff for
        transient errors.
        """
        response = None
        for attempt in range(self.max_retries):
            try:
                if user_utterance.startswith("event:"):
                    response = self.sessions_client.run(
                        session_id=session_id,
                        event=user_utterance.removeprefix("event:").strip(),
                        variables=variables,
                        modality=modality,
                        background_noise_file=background_noise_file,
                        burst_noise_files=burst_noise_files,
                    )
                elif user_utterance.startswith("dtmf:"):
                    response = self.sessions_client.run(
                        session_id=session_id,
                        dtmf=user_utterance.removeprefix("dtmf:").strip(),
                        variables=variables,
                        modality=modality,
                        background_noise_file=background_noise_file,
                        burst_noise_files=burst_noise_files,
                    )
                else:
                    response = self.sessions_client.run(
                        session_id=session_id,
                        text=user_utterance,
                        variables=variables,
                        modality=modality,
                        background_noise_file=background_noise_file,
                        burst_noise_files=burst_noise_files,
                    )
                break
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise e
                if console_logging:
                    print(
                        "Warning: CXAS Agent request failed "
                        f"({e}). Retrying in "
                        f"{self.retry_delay_base**attempt}s..."
                    )
                time.sleep(self.retry_delay_base**attempt)
        return response

    def _print_completion_status(self, eval_conv: LLMUserConversation) -> None:
        """Prints the final step progress of the conversation."""
        print("\n--- Conversation Complete ---")
        print("Final Step Progress:")
        for step_prog in eval_conv.steps_progress:
            print(
                f"- Goal: {step_prog.step.goal} | "
                f"Status: {step_prog.status.value}"
            )
            if step_prog.justification:
                print(f"  Justification: {step_prog.justification}")

    def simulate_conversation(
        self,
        test_case: dict[str, Any],
        model: str = _DEFAULT_GEMINI_MODEL,
        session_id: str | None = None,
        console_logging: bool = True,
        modality: str = "text",
        background_noise_file: str | None = None,
        burst_noise_files: list[str] | None = None,
    ) -> LLMUserConversation:
        """Runs the simulated conversation loop.

        Args:
            test_case: The test case dictionary defining evaluation steps.
            model: The Gemini model used for evaluating turns.
            console_logging: Whether to print interaction transcript to
                the console.
        """
        if session_id is None:
            session_id = str(uuid.uuid4())
        eval_conv = LLMUserConversation(
            genai_client=self.genai_client,
            genai_model=model,
            test_case=test_case,
        )

        if console_logging:
            print(
                f"Starting simulated conversation with session ID: {session_id}"
            )

        # Initialize the first turn manually
        user_utterance, variables = eval_conv.next_user_utterance()
        accumulated_variables = {}
        if variables:
            accumulated_variables.update(variables)

        detailed_trace = []
        detailed_trace.append(f"User: {user_utterance}")

        while user_utterance:
            response = self._send_request_with_retry(
                session_id,
                user_utterance,
                accumulated_variables,
                modality,
                console_logging,
                background_noise_file,
                burst_noise_files,
            )
            if not response:
                break

            if console_logging:
                self.sessions_client.parse_result(response)

            agent_text, trace_chunks, session_ended = (
                self._parse_agent_response(response)
            )
            detailed_trace.append("\n".join(trace_chunks))

            if session_ended:
                if agent_text:
                    eval_conv._add_agent_response(agent_text)
                if console_logging:
                    print(
                        "\nSession has been closed by the Agent via "
                        "end_session tool."
                    )
                break

            # Get the next simulated user utterance based on the agent's
            # response
            user_utterance, variables = eval_conv.next_user_utterance(
                agent_text
            )
            if variables:
                accumulated_variables.update(variables)
            if user_utterance:
                detailed_trace.append(f"User: {user_utterance}")

        if console_logging:
            self._print_completion_status(eval_conv)

        self._evaluate_expectations(
            eval_conv, detailed_trace, model, console_logging
        )
        eval_conv.detailed_trace = detailed_trace
        return eval_conv

    def _prepare_simulation_jobs(
        self, test_cases: list[dict[str, Any]], runs: int
    ) -> list[tuple[dict[str, Any], int]]:
        """Prepares a list of simulation jobs to run."""
        jobs = []
        for tc in test_cases:
            for run_idx in range(runs):
                jobs.append((tc, run_idx))
        return jobs

    def _run_single_simulation_job(
        self,
        tc: dict[str, Any],
        run_idx: int,
        runs: int,
        model: str,
        modality: str,
        verbose: bool,
        parallel: int,
        background_noise_file: str | None = None,
        burst_noise_files: list[str] | None = None,
    ) -> dict[str, Any]:
        """Runs a single simulation job and returns the results."""
        name = tc["name"]
        label = f"{name} (run {run_idx + 1}/{runs})"
        session_id = str(uuid.uuid4())
        try:
            _start = time.time()

            conv = self.simulate_conversation(
                test_case=tc,
                model=model,
                session_id=session_id,
                console_logging=verbose and parallel <= 1,
                modality=modality,
                background_noise_file=background_noise_file,
                burst_noise_files=burst_noise_files,
            )
            duration_s = round(time.time() - _start, 1)

            goals_completed = sum(
                1
                for p in conv.steps_progress
                if p.status == StepStatus.COMPLETED
            )
            total_goals = len(conv.steps_progress)
            expectations_met = sum(
                1
                for r in conv.expectation_results
                if r.status == ExpectationStatus.MET
            )
            total_exp = len(conv.expectation_results)

            passed = goals_completed == total_goals
            if total_exp > 0:
                passed = passed and (expectations_met == total_exp)

            status = "PASS" if passed else "FAIL"
            if parallel > 1 or not verbose:
                print(
                    f"  {status}  {label} | goals: "
                    f"{goals_completed}/{total_goals} | "
                    f"expectations: {expectations_met}/{total_exp} | "
                    f"turns: {conv.current_turn} | {duration_s}s"
                )

            return {
                "name": name,
                "run": run_idx + 1,
                "passed": passed,
                "goals": f"{goals_completed}/{total_goals}",
                "expectations": f"{expectations_met}/{total_exp}",
                "turns": conv.current_turn,
                "duration_s": duration_s,
                "session_id": session_id,
                "session_parameters": tc.get("session_parameters", {}),
                "transcript": conv.get_transcript(),
                "detailed_trace": getattr(conv, "detailed_trace", []),
                "step_details": [
                    {
                        "goal": p.step.goal,
                        "success_criteria": p.step.success_criteria,
                        "status": p.status.value,
                        "justification": p.justification,
                    }
                    for p in conv.steps_progress
                ],
                "expectation_details": [
                    {
                        "expectation": r.expectation,
                        "status": r.status.value,
                        "justification": r.justification,
                    }
                    for r in conv.expectation_results
                ],
            }
        except Exception as e:
            print(f"  ERROR  {label}: {e}")
            return {
                "name": name,
                "run": run_idx + 1,
                "passed": False,
                "error": str(e),
            }

    def _aggregate_simulation_results(
        self,
        jobs: list[tuple[dict[str, Any], int]],
        runs: int,
        parallel: int,
        model: str,
        modality: str,
        verbose: bool,
        background_noise_file: str | None = None,
        burst_noise_files: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Aggregates results from multiple simulation jobs."""
        results = []
        with Progress() as progress:
            task_id = progress.add_task("Running Simulations", total=len(jobs))

            if parallel <= 1:
                for tc, run_idx in jobs:
                    results.append(
                        self._run_single_simulation_job(
                            tc,
                            run_idx,
                            runs,
                            model,
                            modality,
                            verbose,
                            parallel,
                            background_noise_file,
                            burst_noise_files,
                        )
                    )
                    progress.update(task_id, advance=1)
            else:
                max_workers = min(parallel, 25)
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {
                        executor.submit(
                            self._run_single_simulation_job,
                            tc,
                            run_idx,
                            runs,
                            model,
                            modality,
                            verbose,
                            parallel,
                            background_noise_file,
                            burst_noise_files,
                        ): (tc["name"], run_idx)
                        for tc, run_idx in jobs
                    }
                    for future in as_completed(futures):
                        results.append(future.result())
                        progress.update(task_id, advance=1)

        return results

    def run_simulations(
        self,
        test_cases: list[dict[str, Any]],
        runs: int = 1,
        parallel: int = 1,
        model: str = _DEFAULT_GEMINI_MODEL,
        modality: str = "text",
        verbose: bool = False,
        background_noise_file: str | None = None,
        burst_noise_files: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Runs multiple simulations, optionally in parallel.

        Args:
            test_cases: List of test case dictionaries.
            runs: Number of runs per test case.
            parallel: Number of parallel workers (capped at 25).
            model: Gemini model to use.
            modality: 'text' or 'audio'.
            verbose: Whether to log to console (only active if parallel=1).
        """
        jobs = self._prepare_simulation_jobs(test_cases, runs)
        return self._aggregate_simulation_results(
            jobs,
            runs,
            parallel,
            model,
            modality,
            verbose,
            background_noise_file,
            burst_noise_files,
        )

    def _add_agent_text(self, turn: Turn, text: str) -> None:
        """Consistently handles adding agent text to a Turn."""
        if not text:
            return
        if turn.agent:
            if isinstance(turn.agent, list):
                turn.agent.append(text)
            else:
                turn.agent = [turn.agent, text]
        else:
            turn.agent = text

    def _match_tool_response(
        self, turn: Turn, tool_name: str, response: Any
    ) -> None:
        """Matches a tool response to the latest call for the same tool."""
        if not turn or not turn.tool_calls:
            return

        for tc_obj in reversed(turn.tool_calls):
            if tc_obj.action == tool_name and tc_obj.output is None:
                tc_obj.output = response
                break

    def _handle_text_chunk(self, chunk: dict[str, Any], turn: Turn) -> None:
        """Processes a text chunk from the platform response."""
        text = chunk.get("text", "").strip()
        if text:
            self._add_agent_text(turn, text)

    def _handle_tool_call_chunk(
        self, chunk: dict[str, Any], turn: Turn
    ) -> None:
        """Processes a tool call chunk from the platform response."""
        tc = chunk["tool_call"]
        tool_name = tc.get("display_name") or tc.get("tool")
        args = Sessions._expand_pb_struct(tc.get("args", {}))
        turn.tool_calls.append(ToolCall(action=tool_name, args=args))

    def _handle_tool_response_chunk(
        self, chunk: dict[str, Any], turn: Turn
    ) -> None:
        """Processes a tool response chunk from the platform response."""
        tr = chunk["tool_response"]
        tool_name = tr.get("display_name") or tr.get("tool")
        response = Sessions._expand_pb_struct(tr.get("response", {}))
        self._match_tool_response(turn, tool_name, response)

    def _handle_agent_transfer_chunk(
        self, chunk: dict[str, Any], turn: Turn
    ) -> None:
        """Processes an agent transfer chunk from the platform response."""
        # For golden export, we represent this as a special tool call or skip if
        # model doesn't support. eval_utils uses it for expectations.
        at = chunk["agent_transfer"]
        target = at.get("display_name") or at.get("target_agent", "unknown")
        # Standardizing as a 'transfer_to_agent' tool call for parity with
        # eval_utils._process_dataset_turn
        turn.tool_calls.append(
            ToolCall(action="transfer_to_agent", args={"agent": target})
        )

    def _handle_payload_chunk(self, chunk: dict[str, Any], turn: Turn) -> None:
        """Processes a custom payload chunk from the platform response."""
        # Custom payloads don't have a direct field in Turn/ToolCall model
        # for golden export usually, but we could add to agent text as a note
        payload = Sessions._expand_pb_struct(chunk.get("payload", {}))
        self._add_agent_text(turn, f"[Custom Payload]: {json.dumps(payload)}")

    def _process_platform_chunk(
        self, chunk: dict[str, Any], turn: Turn
    ) -> None:
        """Dispatches platform chunks to their respective handlers."""
        if "text" in chunk:
            self._handle_text_chunk(chunk, turn)
        elif "tool_call" in chunk:
            self._handle_tool_call_chunk(chunk, turn)
        elif "tool_response" in chunk:
            self._handle_tool_response_chunk(chunk, turn)
        elif "agent_transfer" in chunk:
            self._handle_agent_transfer_chunk(chunk, turn)
        elif "payload" in chunk:
            self._handle_payload_chunk(chunk, turn)

    def _parse_platform_messages(
        self, messages: list[dict[str, Any]], turns: list[Turn]
    ) -> Turn | None:
        """Parses a list of platform messages into turns."""
        current_turn = turns[-1] if turns else None

        for msg in messages:
            role = msg.get("role", "")
            chunks = msg.get("chunks", [])

            if role == "user":
                text = " ".join(
                    [c.get("text", "") for c in chunks if "text" in c]
                ).strip()
                current_turn = Turn(user=text, tool_calls=[])
                turns.append(current_turn)
            else:
                if not current_turn:
                    current_turn = Turn(tool_calls=[])
                    turns.append(current_turn)

                for chunk in chunks:
                    self._process_platform_chunk(chunk, current_turn)

        return current_turn

    def _get_turns_from_platform(self, session_id: str) -> list[Turn]:
        """Fetches and parses turns from the platform conversation history."""
        ch = ConversationHistory(app_name=self.app_name, creds=self.creds)
        conv_obj = ch.get_conversation(session_id)
        conv_dict = type(conv_obj).to_dict(conv_obj)

        turns = []
        for p_turn in conv_dict.get("turns", []):
            self._parse_platform_messages(p_turn.get("messages", []), turns)
        return turns

    def _parse_trace_line(self, line: str, turns: list[Turn]) -> Turn | None:
        """Parses a single line from the local trace."""
        current_turn = turns[-1] if turns else None

        if line.startswith("User: "):
            current_turn = Turn(user=line[6:].strip(), tool_calls=[])
            turns.append(current_turn)
        elif line.startswith("Agent Text: "):
            if not current_turn:
                current_turn = Turn(tool_calls=[])
                turns.append(current_turn)
            self._add_agent_text(current_turn, line[12:].strip())
        elif line.startswith("Agent Transfer: "):
            if not current_turn:
                current_turn = Turn(tool_calls=[])
                turns.append(current_turn)
            target = line[16:].strip().removeprefix("Transferred to ")
            current_turn.tool_calls.append(
                ToolCall(action="transfer_to_agent", args={"agent": target})
            )
        elif line.startswith("Custom Payload: "):
            if not current_turn:
                current_turn = Turn(tool_calls=[])
                turns.append(current_turn)
            self._add_agent_text(
                current_turn, f"[Custom Payload]: {line[16:].strip()}"
            )

        return current_turn

    def _get_turns_from_local_trace(self, trace: list[str]) -> list[Turn]:
        """Parses turns from the local simulation trace (fallback)."""
        turns = []
        for line in trace:
            self._parse_trace_line(line, turns)
        return turns

    def _get_turns(self, res: dict[str, Any]) -> list[Turn]:
        """Orchestrates turn retrieval with platform-to-local fallback."""
        session_id = res.get("session_id")
        if not session_id:
            return []

        try:
            return self._get_turns_from_platform(session_id)
        except Exception as e:
            print(
                f"Warning: Failed to fetch conversation {session_id} "
                f"from platform: {e}. Falling back to local trace."
            )
            return self._get_turns_from_local_trace(
                res.get("detailed_trace", [])
            )

    def export_results_to_golden(
        self,
        results: list[dict[str, Any]],
        output_path: str | None = None,
    ) -> str:
        """Exports simulation results to a Golden Evaluation YAML file.

        Fetches the full conversation trace for each simulation from the
        platform to ensure accuracy.

        Args:
            results: The list of results returned by run_simulations.
            output_path: Optional local path to save the generated YAML.

        Returns:
            The generated YAML string.
        """
        conversations_list = []

        for res in results:
            turns = self._get_turns(res)
            if not turns:
                continue

            expectations = [
                e["expectation"] for e in res.get("expectation_details", [])
            ]
            params = res.get("session_parameters", {})

            conversations_list.append(
                GoldenConversation(
                    conversation=res.get("name", "Simulated_Conversation"),
                    turns=turns,
                    expectations=expectations,
                    session_parameters=params,
                )
            )

        dataset = GoldenConversations(conversations=conversations_list)
        yaml_content = yaml.dump(
            dataset.model_dump(exclude_none=True),
            sort_keys=False,
            allow_unicode=True,
        )

        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(yaml_content)

        return yaml_content
