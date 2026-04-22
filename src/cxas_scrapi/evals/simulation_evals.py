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
from typing import Any, Dict, List, Optional

import pandas as pd
import pydantic

from cxas_scrapi.core.apps import Apps
from cxas_scrapi.core.sessions import Sessions
from cxas_scrapi.core.tools import Tools
from cxas_scrapi.prompts import llm_user_prompts
from cxas_scrapi.utils.eval_utils import (
    ExpectationResult,
    ExpectationStatus,
    evaluate_expectations,
)
from cxas_scrapi.utils.gemini import GeminiGenerate

_FIRST_UTTERANCE = "event: welcome"
_MAX_TURNS = 30
_DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite-preview"


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
        expectations_df: Optional[pd.DataFrame] = None,
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
    ) -> tuple[str, Dict[str, Any]]:
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
        test_case: Dict[str, Any],
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
        self.expectation_results: List[ExpectationResult] = []

    def _next_user_utterance(self) -> tuple[str, Dict[str, Any]]:
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
        if self.current_turn >= self.max_turns:
            return "", {}

        # If all steps are completed, then the conversation is complete.
        if all(
            item.status == StepStatus.COMPLETED for item in self.steps_progress
        ):
            return "", {}

        if self.current_turn == 0:
            session_params = self.test_case.get("session_parameters", {})
            if not self.test_case["steps"][0].get("static_utterance", None):
                return _FIRST_UTTERANCE, session_params
            inject_vars = self.test_case["steps"][0].get("inject_variables", {})
            merged_vars = {**session_params, **inject_vars}
            return self.test_case["steps"][0]["static_utterance"], merged_vars

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

    def __init__(self, app_name: str, **kwargs):
        self.app_name = app_name
        project_id = app_name.split("/")[1]
        location = app_name.split("/")[3]
        super().__init__(project_id=project_id, location=location, **kwargs)
        self.sessions_client = Sessions(app_name, **kwargs)
        self.tools_map = Tools(app_name=app_name).get_tools_map()

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
        agent_text = ""
        session_ended = False
        trace_chunks = []

        for output in response.outputs:
            if hasattr(output, "text") and output.text:
                agent_text += output.text + " "
                trace_chunks.append(f"Agent Text: {output.text}")

            tool_calls_msg = getattr(output, "tool_calls", None)
            if tool_calls_msg and hasattr(tool_calls_msg, "tool_calls"):
                for tc in tool_calls_msg.tool_calls:
                    tool_name = getattr(tc, "tool", "") or getattr(
                        tc, "display_name", ""
                    )
                    expanded_args = Sessions._expand_pb_struct(tc.args)
                    trace_chunks.append(
                        f"Tool Call (Output): {tool_name} "
                        f"with args {expanded_args}"
                    )
                    if "end_session" in tool_name:
                        session_ended = True

            diagnostic_info = getattr(output, "diagnostic_info", None)
            if diagnostic_info and hasattr(diagnostic_info, "messages"):
                for message in diagnostic_info.messages:
                    for chunk in getattr(message, "chunks", []):
                        add_text, ended = self._process_diagnostic_chunk(
                            chunk, trace_chunks
                        )
                        agent_text += add_text
                        if ended:
                            session_ended = True

        return agent_text.strip(), trace_chunks, session_ended

    def _process_diagnostic_chunk(
        self, chunk: Any, trace_chunks: list[str]
    ) -> tuple[str, bool]:
        """Processes a single diagnostic chunk and updates trace_chunks."""
        agent_text_add = ""
        session_ended = False

        chunk_type = (
            chunk._pb.WhichOneof("data") if hasattr(chunk, "_pb") else None
        )
        if chunk_type == "tool_call":
            tc = chunk.tool_call
            tool_name = getattr(tc, "display_name", "") or getattr(
                tc, "tool", ""
            )
            if (
                tool_name
                and "/tools/" in tool_name
                and hasattr(self, "tools_map")
            ):
                tool_name = self.tools_map.get(tool_name, tool_name)
            expanded_args = Sessions._expand_pb_struct(tc.args)
            trace_chunks.append(
                f"Tool Call: {tool_name} with args {expanded_args}"
            )
            if "end_session" in tool_name:
                session_ended = True
        elif chunk_type == "tool_response":
            tr = chunk.tool_response
            tool_name = getattr(tr, "display_name", "") or getattr(
                tr, "tool", ""
            )
            if (
                tool_name
                and "/tools/" in tool_name
                and hasattr(self, "tools_map")
            ):
                tool_name = self.tools_map.get(tool_name, tool_name)
            expanded_response = Sessions._expand_pb_struct(tr.response)
            trace_chunks.append(
                f"Tool Response: {tool_name} with result {expanded_response}"
            )
        elif chunk_type == "text":
            agent_text_add = chunk.text + " "
            trace_chunks.append(f"Agent Text (Diag): {chunk.text}")

        return agent_text_add, session_ended

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
        variables: Dict[str, Any],
        modality: str,
        console_logging: bool,
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
                    )
                elif user_utterance.startswith("dtmf:"):
                    response = self.sessions_client.run(
                        session_id=session_id,
                        dtmf=user_utterance.removeprefix("dtmf:").strip(),
                        variables=variables,
                        modality=modality,
                    )
                else:
                    response = self.sessions_client.run(
                        session_id=session_id,
                        text=user_utterance,
                        variables=variables,
                        modality=modality,
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
        test_case: Dict[str, Any],
        model: str = _DEFAULT_GEMINI_MODEL,
        session_id: Optional[str] = None,
        console_logging: bool = True,
        modality: str = "text",
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

        detailed_trace = []
        detailed_trace.append(f"User: {user_utterance}")

        while user_utterance:
            response = self._send_request_with_retry(
                session_id, user_utterance, variables, modality, console_logging
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
            if user_utterance:
                detailed_trace.append(f"User: {user_utterance}")

        if console_logging:
            self._print_completion_status(eval_conv)

        self._evaluate_expectations(
            eval_conv, detailed_trace, model, console_logging
        )
        eval_conv.detailed_trace = detailed_trace
        return eval_conv

    def run_simulations(
        self,
        test_cases: List[Dict[str, Any]],
        runs: int = 1,
        parallel: int = 1,
        model: str = _DEFAULT_GEMINI_MODEL,
        modality: str = "text",
        verbose: bool = False,
    ) -> List[Dict[str, Any]]:
        """Runs multiple simulations, optionally in parallel.

        Args:
            test_cases: List of test case dictionaries.
            runs: Number of runs per test case.
            parallel: Number of parallel workers (capped at 25).
            model: Gemini model to use.
            modality: 'text' or 'audio'.
            verbose: Whether to log to console (only active if parallel=1).
        """
        jobs = []
        for tc in test_cases:
            for run_idx in range(runs):
                jobs.append((tc, run_idx))

        results = []

        def _run_job(tc, run_idx):
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

        if parallel <= 1:
            for tc, run_idx in jobs:
                results.append(_run_job(tc, run_idx))
        else:
            max_workers = min(parallel, 25)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(_run_job, tc, run_idx): (
                        tc["name"],
                        run_idx,
                    )
                    for tc, run_idx in jobs
                }
                for future in as_completed(futures):
                    results.append(future.result())

        return results
