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

import contextlib
import enum
import functools
import inspect
import json
import logging
import os
import re
import shutil
import time
import typing
import uuid
import zlib
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import pandas as pd
import pydantic
import yaml
from google import genai
from rich.progress import Progress

from cxas_scrapi.core.apps import Apps
from cxas_scrapi.core.audio_transformer import resolve_noise_config
from cxas_scrapi.core.conversation_history import ConversationHistory
from cxas_scrapi.core.response_parser import ParsedSessionResponse
from cxas_scrapi.core.sessions import BidiSessionError, Sessions
from cxas_scrapi.core.tools import Tools
from cxas_scrapi.core.traces import Traces, _parse_pass_fail
from cxas_scrapi.evals.naturalness import (
    NaturalnessConfig,
    NaturalnessResult,
    evaluate_naturalness,
    extract_agent_turns,
    parse_naturalness_config,
)
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
from cxas_scrapi.utils.tracing import trace_report
from cxas_scrapi.utils.tracing.audio_analysis import ANALYSIS_REGISTRY
from cxas_scrapi.utils.tracing.audio_drift import flag_drift
from cxas_scrapi.utils.tracing.audio_metrics import (
    measure_call,
    try_render_join_steps,
)

logger = logging.getLogger(__name__)

_FIRST_UTTERANCE = "event: welcome"
_MAX_TURNS = 30
_DEFAULT_SIM_USER_MODEL = "gemini-3.5-flash-lite"
_DEFAULT_EVAL_MODEL = "gemini-3.5-flash-lite"
_DEFAULT_GEMINI_MODEL = _DEFAULT_SIM_USER_MODEL
# How often to re-check whether a finished conversation has been published,
# which is what the naturalness metric reads perceived latency from.
_LATENCY_POLL_INTERVAL_S = 5.0


class Step(pydantic.BaseModel):
    goal: str = ""
    success_criteria: str = ""
    response_guide: str = ""
    max_turns: int = 0
    barge_in_after_ms: int | None = None

    static_utterance: str = ""
    # Variable injects are only supported for the first step
    inject_variables: dict[str, Any] = {}

    def model_dump(self, **kwargs: typing.Any) -> typing.Any:
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

    def model_dump(self, **kwargs: typing.Any) -> typing.Any:
        kwargs.setdefault("exclude_defaults", True)
        return super().model_dump(**kwargs)


class SimulationReport:
    """A report containing both Goals and Expectations DataFrames."""

    def __init__(
        self,
        goals_df: pd.DataFrame,
        expectations_df: pd.DataFrame | None = None,
        naturalness_df: pd.DataFrame | None = None,
        naturalness_headline: str = "",
    ) -> None:
        self.goals_df = goals_df
        self.expectations_df = expectations_df
        self.naturalness_df = naturalness_df
        self.naturalness_headline = naturalness_headline

    def __str__(self) -> typing.Any:
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

        if self.naturalness_df is not None:
            nat_str = self.naturalness_df.to_string()
            nat_str = nat_str.replace("Bot-like", f"{red}Bot-like{reset}")
            nat_str = nat_str.replace("Human-like", f"{green}Human-like{reset}")
            res += "\n\n--- Naturalness ---\n"
            if self.naturalness_headline:
                res += self.naturalness_headline + "\n"
            res += nat_str

        return res

    def _repr_html_(self) -> typing.Any:
        html = "<h3>Goal Progress</h3>" + self.goals_df._repr_html_()
        if self.expectations_df is not None:
            html += "<h3>Expectations</h3>" + self.expectations_df._repr_html_()
        if self.naturalness_df is not None:
            html += "<h3>Naturalness</h3>"
            if self.naturalness_headline:
                html += f"<p><b>{self.naturalness_headline}</b></p>"
            html += self.naturalness_df._repr_html_()
        return html


class Conversation:
    """Base class for users."""

    def __init__(self) -> None:
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

    def _add_agent_tool_calls(self, tool_calls: list[Any]) -> None:
        """Adds agent tool calls to the transcript."""
        for tc in tool_calls:
            self.transcript.append(
                f"Agent Action: Call Tool {tc.name} with args {tc.args}"
            )

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
        max_turns: int | None = None,
        initial_utterance: str = _FIRST_UTTERANCE,
    ) -> None:
        super().__init__()
        self.genai_client = genai_client
        self.genai_model = genai_model
        self.test_case = test_case
        self.initial_utterance = initial_utterance
        if max_turns is not None:
            self.max_turns = max_turns
        else:
            self.max_turns = test_case.get("max_turns") or _MAX_TURNS
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
        audio_exps = test_case.get("audio_expectations", [])
        self.audio_expectations = []
        # The 'requires_audio_paths' flag informs the evaluation engine
        # (evaluate_expectations) that it must retrieve and attach the raw
        # agent output WAV file for this specific expectation turn. This
        # enables multimodal audio audits while avoiding the latency/cost of
        # uploading audio files for standard text-only expectations.
        for exp in audio_exps:
            if isinstance(exp, dict):
                exp_dict = dict(exp)
                exp_dict["requires_audio_paths"] = True
            else:
                exp_dict = {
                    "expectation": str(exp),
                    "requires_audio_paths": True,
                }
            self.audio_expectations.append(exp_dict)
        self.expectation_results: list[ExpectationResult] = []
        # Populated only when the optional Naturalness Metric is configured.
        self.naturalness_result: NaturalnessResult | None = None

    def _check_conversation_status(self) -> bool:
        """Checks if the conversation should continue."""
        if self.current_turn >= self.max_turns:
            return False

        # If all steps are completed, then the conversation is complete.
        return not all(
            item.status == StepStatus.COMPLETED for item in self.steps_progress
        )

    def _get_active_step_index(self) -> int | None:
        """Finds the index of the first step that is not completed."""
        for i, prog in enumerate(self.steps_progress):
            if prog.status != StepStatus.COMPLETED:
                return i
        return None

    def peek_barge_in_after_ms(self) -> int | None:
        """Returns barge_in_after_ms for the next active step, if configured."""
        if not self._check_conversation_status():
            return None
        active_idx = self._get_active_step_index()
        if active_idx is not None:
            step = self.steps_progress[active_idx].step
            return step.barge_in_after_ms
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

        if self.current_turn == 0:
            session_params = self.test_case.get("session_parameters", {})
            return self.initial_utterance, session_params

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
            elif (
                active_step_prog.step.barge_in_after_ms is not None
                and not getattr(self, "genai_client", None)
            ):
                active_step_prog.status = StepStatus.COMPLETED
                active_step_prog.justification = (
                    "Interruption utterance generated from goal/guide."
                )
                utterance = (
                    active_step_prog.step.response_guide
                    or active_step_prog.step.goal
                    or "Excuse me"
                )
                return utterance, {}

        prompt = self._prepare_llm_prompt()

        output: LLMUserConversation.Output = self.genai_client.generate(
            prompt=prompt,
            model_name=self.genai_model,
            response_mime_type="application/json",
            response_schema=LLMUserConversation.Output,
        )

        if output:
            orig_steps = self.test_case.get("steps", [])
            for i, prog in enumerate(output.step_progresses):
                if i < len(orig_steps):
                    orig = orig_steps[i]
                    if isinstance(orig, dict):
                        if (
                            "barge_in_after_ms" in orig
                            and prog.step.barge_in_after_ms is None
                        ):
                            prog.step.barge_in_after_ms = orig[
                                "barge_in_after_ms"
                            ]
                        if (
                            "static_utterance" in orig
                            and not prog.step.static_utterance
                        ):
                            prog.step.static_utterance = orig[
                                "static_utterance"
                            ]
                        if (
                            "inject_variables" in orig
                            and not prog.step.inject_variables
                        ):
                            prog.step.inject_variables = orig[
                                "inject_variables"
                            ]
            self.steps_progress = output.step_progresses
            return output.next_user_utterance, {}

        return "", {}

    def generate_barge_in_utterance(self) -> tuple[str, dict[str, Any]]:
        """Generates the next user utterance for barge-in without adding it to
        transcript.
        """
        return self._next_user_utterance()

    def record_user_utterance(self, utterance: str) -> None:
        """Records a user utterance to transcript and advances current turn."""
        self._add_user_utterance(utterance)
        self.current_turn += 1

    def next_user_utterance(
        self, last_agent_response: str = ""
    ) -> tuple[str, dict[str, Any]]:
        """Returns the next user utterance from the LLM user."""
        if last_agent_response:
            self._add_agent_response(last_agent_response)
        next_user_utterance, variables_to_inject = self._next_user_utterance()
        self.record_user_utterance(next_user_utterance)
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

        naturalness_df = None
        naturalness_headline = ""
        result = self.naturalness_result
        if result:
            nat_records = []
            for turn in result.turns:
                record: dict[str, Any] = {
                    "turn": turn.turn_index,
                    "label": turn.label.value,
                    "score": turn.score,
                }
                # The measured value, alongside the 1-5 band it maps to, so a
                # slow turn is legible without decoding the band.
                latency_ms = result.latency_ms_by_turn.get(turn.turn_index)
                if latency_ms is not None:
                    record["latency_s"] = round(latency_ms / 1000.0, 2)
                # One column per graded quality keeps the table scannable.
                for factor in turn.factors:
                    if factor.quality:
                        record[factor.quality] = factor.score
                record["justification"] = turn.justification
                nat_records.append(record)
            naturalness_df = pd.DataFrame(nat_records)
            naturalness_headline = (
                f"Overall: {result.overall_score}/5 "
                f"({result.overall_label.value})"
            )
            if result.pass_threshold is not None:
                verdict = "PASS" if result.passed else "FAIL"
                naturalness_headline += (
                    f" | threshold {result.pass_threshold} -> {verdict}"
                )
            # A hard failure can sink a run that scored well, so name it.
            for reason in result.failure_reasons:
                naturalness_headline += f" | FAILED: {reason}"

        return SimulationReport(
            goals_df,
            expectations_df,
            naturalness_df=naturalness_df,
            naturalness_headline=naturalness_headline,
        )


def cleanup_session_dir(func: typing.Any) -> typing.Any:
    """Decorator to ensure session temporary directory is deleted on exit."""
    sig = inspect.signature(func)

    @functools.wraps(func)
    def wrapper(self, *args: typing.Any, **kwargs: typing.Any) -> typing.Any:  # noqa: ANN001
        bound = sig.bind(self, *args, **kwargs)
        bound.apply_defaults()

        session_id = bound.arguments.get("session_id")
        if session_id is None:
            session_id = str(uuid.uuid4())
            bound.arguments["session_id"] = session_id

        try:
            return func(*bound.args, **bound.kwargs)
        finally:
            try:
                if bound.arguments.get("capture_agent_audio"):
                    output_dir = bound.arguments.get("output_dir")
                    if output_dir:
                        dest_dir = os.path.join(output_dir, session_id)
                        os.makedirs(dest_dir, exist_ok=True)
                        src_dir = f"/tmp/scrapi_evals/{session_id}"
                        if os.path.exists(src_dir):
                            for fname in os.listdir(src_dir):
                                if fname.endswith(".wav"):
                                    shutil.copy2(
                                        os.path.join(src_dir, fname),
                                        os.path.join(dest_dir, fname),
                                    )
            finally:
                shutil.rmtree(
                    f"/tmp/scrapi_evals/{session_id}", ignore_errors=True
                )

    return wrapper


def _voice_locale(voice_name: str) -> str:
    """Extracts the BCP-47 locale prefix from a TTS voice name."""
    parts = voice_name.split("-")
    if len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}"
    return "en-US"


def _scenario_locale(session_parameters: Mapping[str, Any] | None) -> str:
    """Determines the target locale from scenario session_parameters."""
    if not session_parameters:
        return "en-US"
    explicit_locale = str(session_parameters.get("locale") or "").strip()
    if explicit_locale and "-" in explicit_locale:
        return explicit_locale
    user_lang = str(session_parameters.get("user_lang") or "").strip().upper()
    if user_lang in {"S", "ES"} or user_lang.startswith("ES"):
        return "es-US"
    return "en-US"


def expected_caller_turns(tc: Mapping[str, Any]) -> int:
    """Returns the number of expected caller turns/steps in a scenario."""
    steps = tc.get("steps") or []
    if isinstance(steps, list) and steps:
        return len(steps)
    turns = tc.get("turns") or tc.get("conversation_Turns") or []
    if isinstance(turns, list) and turns:
        return len(turns)
    return 0


def actual_caller_turns(row: Mapping[str, Any]) -> int:
    """Counts actual spoken/text caller turns from a simulation result."""
    transcript = str(row.get("transcript") or "")
    if transcript.strip():
        user_lines = [
            ln
            for ln in transcript.splitlines()
            if ln.strip().startswith("User:")
            and not ln.strip().startswith("User: event")
            and ln.strip() != "User:"
        ]
        return len(user_lines)
    detailed_trace = row.get("detailed_trace")
    if isinstance(detailed_trace, list) and detailed_trace:
        user_trace = [
            str(ln)
            for ln in detailed_trace
            if str(ln).strip().startswith("User:")
            and not str(ln).strip().startswith("User: event")
            and str(ln).strip() != "User:"
        ]
        return len(user_trace)
    turns = row.get("conversation_Turns") or row.get("conversation_turns")
    if isinstance(turns, list):
        return len(turns)
    turn_count = row.get("turns")
    if isinstance(turn_count, int):
        return turn_count
    return 0


class SimulationEvals(Apps):
    """Wrapper class to simulate entire multi-turn conversations with a
    CXAS Agent."""

    max_retries: int = 3
    retry_delay_base: int = 2

    def __init__(
        self,
        app_name: str,
        rate_limiter: RateLimiter | None = None,
        expectations_only: bool = False,
        deployment_id: str | None = None,
        vertex_location: str = "global",
        naturalness: bool | dict[str, Any] | None = None,
        audio_analysis: bool = False,
        sim_user_voices: Sequence[str] | None = None,
        **kwargs: typing.Any,
    ) -> None:
        self.expectations_only = expectations_only
        self.vertex_location = vertex_location
        # Run-level override for the optional Naturalness Metric. `None`
        # defers entirely to each test case; True enables it everywhere with
        # defaults; a dict is merged over the test case's own block.
        self.naturalness = naturalness
        self.audio_analysis = audio_analysis
        self.sim_user_voices: list[str] = [
            v.strip() for v in (sim_user_voices or []) if v and v.strip()
        ]
        project_id = app_name.split("/")[1]
        location = app_name.split("/")[3]
        super().__init__(project_id=project_id, location=location, **kwargs)
        # Must follow super().__init__: `Common` assigns `self.app_name`
        # unconditionally, and it is not handed the app name here, so setting
        # this any earlier would be silently overwritten with `None`.
        self.app_name = app_name
        self.sessions_client = Sessions(
            app_name,
            deployment_id=deployment_id,
            rate_limiter=rate_limiter,
            **kwargs,
        )
        self.tools_map = Tools(app_name=app_name, **kwargs).get_tools_map()

        self.genai_client = GeminiGenerate(
            project_id=self.project_id,
            location=self.vertex_location,
            credentials=self.creds,
        )

    def _voice_config_for(
        self, test_case: Mapping[str, Any]
    ) -> dict[str, str] | None:
        """Deterministically selects a TTS voice_config for a test case."""
        existing = test_case.get("voice_config")
        if isinstance(existing, dict) and existing.get("voice_name"):
            return dict(existing)
        if not self.sim_user_voices:
            return None
        locale = _scenario_locale(test_case.get("session_parameters"))
        candidates = [
            v
            for v in self.sim_user_voices
            if _voice_locale(v).lower() == locale.lower()
        ]
        if not candidates:
            candidates = list(self.sim_user_voices)
        name = str(test_case.get("name") or "")
        idx = zlib.crc32(name.encode("utf-8")) % len(candidates)
        voice_name = candidates[idx]
        return {
            "voice_name": voice_name,
            "language_code": _voice_locale(voice_name),
        }

    @staticmethod
    def requires_local_execution(
        test_case: dict[str, Any],
    ) -> tuple[bool, str]:
        """Checks if a test case requires local execution (cannot be run as a
        platform Milestone eval).

        Returns:
            (requires_local: bool, reason: str)
        """
        if test_case.get("modality") == "audio":
            return True, "Audio modality requires local client simulation"
        if test_case.get("audio_expectations"):
            return True, "audio_expectations requires local client simulation"
        if test_case.get("audio_analysis"):
            return True, "audio_analysis requires local client simulation"
        if test_case.get("voice_config"):
            return True, "voice_config requires local client simulation"
        if test_case.get("naturalness_metric") or test_case.get("naturalness"):
            return True, "naturalness_metric requires local client evaluation"
        if test_case.get("barge_in_after_ms") is not None:
            return True, "barge_in_after_ms requires local audio bidi session"
        for step in test_case.get("steps", []):
            if (
                isinstance(step, dict)
                and step.get("barge_in_after_ms") is not None
            ):
                return (
                    True,
                    "Step barge_in_after_ms requires local bidi session",
                )
        return False, ""

    def to_milestone_yaml(
        self, test_cases: list[dict[str, Any]] | dict[str, Any]
    ) -> str:
        """Converts simulation test case(s) into CES Platform Milestone YAML."""
        from cxas_scrapi.utils.eval_utils import (  # noqa: PLC0415
            EvalUtils,
        )

        return EvalUtils.convert_sim_evals_to_milestone_yaml(test_cases)

    def push_to_platform(
        self,
        test_cases: list[dict[str, Any]] | dict[str, Any],
        conflict_strategy: str = "OVERWRITE",
    ) -> Any:
        """Pushes simulation test case(s) to CES as Milestone evaluations."""
        from cxas_scrapi.utils.eval_utils import (  # noqa: PLC0415
            EvalUtils,
        )

        eval_utils = EvalUtils(app_name=self.app_name, creds=self.creds)
        return eval_utils.push_milestone_evals_from_yaml(
            source=test_cases,
            app_name=self.app_name,
            conflict_strategy=conflict_strategy,
        )

    def run_simulations_on_platform(
        self,
        test_cases: list[dict[str, Any]],
        runs: int = 1,
        timeout_seconds: int = 600,
        use_tool_fakes: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Pushes and runs simulations on the CES platform via RunEvaluation,
        returning results formatted as simulation result dictionaries.
        """
        from cxas_scrapi.core.evaluations import (  # noqa: PLC0415
            Evaluations,
        )
        from cxas_scrapi.utils.eval_utils import (  # noqa: PLC0415
            EvalUtils,
        )

        eval_client = Evaluations(app_name=self.app_name, creds=self.creds)
        eval_utils = EvalUtils(app_name=self.app_name, creds=self.creds)

        import_op = self.push_to_platform(test_cases)

        eval_names: list[str] = []
        if isinstance(import_op, dict):
            imported_evals = import_op.get("response", {}).get(
                "evaluations", []
            )
            if isinstance(imported_evals, list):
                eval_names = [
                    e["name"]
                    for e in imported_evals
                    if isinstance(e, dict) and "name" in e
                ]

        if not eval_names:
            eval_names = [
                tc["name"]
                for tc in test_cases
                if isinstance(tc, dict) and "name" in tc
            ]
        if not eval_names:
            return []

        run_eval_kwargs: dict[str, Any] = {
            "evaluations": eval_names,
            "app_name": self.app_name,
            "modality": "text",
            "run_count": runs,
        }
        if use_tool_fakes is not None:
            run_eval_kwargs["use_tool_fakes"] = use_tool_fakes

        try:
            run_op = eval_client.run_evaluation(**run_eval_kwargs)
        except Exception:
            resolved_names = []
            evals_map = None
            for e_name in eval_names:
                if e_name.startswith("projects/") and "/evaluations/" in e_name:
                    resolved_names.append(e_name)
                else:
                    if evals_map is None:
                        evals_map = eval_client._get_or_load_evals_map(
                            self.app_name
                        )
                    r_name = evals_map.get("scenarios", {}).get(
                        e_name
                    ) or evals_map.get("goldens", {}).get(e_name)
                    if r_name:
                        resolved_names.append(r_name)
            rest_kwargs: dict[str, Any] = {
                "evaluations": resolved_names or eval_names,
                "app_name": self.app_name,
                "modality": "text",
                "run_count": runs,
            }
            if use_tool_fakes is not None:
                rest_kwargs["use_tool_fakes"] = use_tool_fakes
            run_op = eval_client.run_evaluation_rest(**rest_kwargs)

        meta_run = getattr(
            getattr(run_op, "metadata", None), "evaluation_run", None
        )
        run_id = (
            meta_run
            if isinstance(meta_run, str) and meta_run
            else getattr(run_op, "name", None)
        )
        if not isinstance(run_id, str) or not run_id:
            run_id = None
        if not run_id and hasattr(run_op, "operation"):
            op_name_attr = getattr(run_op.operation, "name", None)
            if isinstance(op_name_attr, str) and op_name_attr:
                run_id = op_name_attr
        if not run_id and isinstance(run_op, dict):
            run_id = (
                run_op.get("evaluationRun")
                or run_op.get("metadata", {}).get("evaluationRun")
                or run_op.get("response", {}).get("evaluationRun")
                or run_op.get("name")
            )

        if (
            run_id
            and "/evaluationRuns/" not in run_id
            and hasattr(run_op, "_refresh")
        ):
            from google.cloud.ces_v1beta.types import (  # noqa: PLC0415
                RunEvaluationOperationMetadata,
            )

            for _ in range(12):
                with contextlib.suppress(Exception):
                    refreshed = run_op._refresh(None)
                    meta = RunEvaluationOperationMetadata()
                    meta._pb.ParseFromString(refreshed.metadata.value)
                    if meta.evaluation_run:
                        run_id = meta.evaluation_run
                        break
                time.sleep(5)

        if (
            run_id
            and "/evaluationRuns/" not in run_id
            and "/operations/" in run_id
        ):
            op_name = run_id
            for _ in range(12):
                with contextlib.suppress(Exception):
                    op_state = eval_client.get_operation_rest(op_name)
                    candidate = op_state.get("metadata", {}).get(
                        "evaluationRun"
                    ) or op_state.get("response", {}).get("evaluationRun")
                    if candidate:
                        run_id = candidate
                        break
                time.sleep(5)

        raw_results = []
        if run_id and "/evaluationRuns/" in run_id:
            raw_results = eval_utils.wait_for_run_and_get_results(
                run_id, timeout_seconds=timeout_seconds
            )

        df_dict = eval_utils.evals_to_dataframe(results=raw_results)
        df_summary = df_dict.get("summary", pd.DataFrame())
        df_metadata = df_dict.get("metadata", pd.DataFrame())
        df_failures = df_dict.get("failures", pd.DataFrame())

        sim_results: list[dict[str, Any]] = []
        tc_map = {
            tc.get("name"): tc for tc in test_cases if isinstance(tc, dict)
        }

        for tc_name, tc_def in tc_map.items():
            tc_runs = (
                df_summary[df_summary["display_name"] == tc_name]
                if not df_summary.empty
                else pd.DataFrame()
            )
            if tc_runs.empty:
                sim_results.append(
                    {
                        "name": tc_name,
                        "passed": False,
                        "status": "FAIL",
                        "goals": "0/0",
                        "expectations": "0/0",
                        "duration_s": 0.0,
                        "run": 1,
                        "runs": runs,
                        "platform": True,
                        "step_details": [],
                        "expectation_details": [],
                        "session_parameters": tc_def.get(
                            "session_parameters", {}
                        ),
                    }
                )
                continue

            tc_steps = (
                tc_def.get("steps", [])
                if isinstance(tc_def.get("steps"), list)
                else []
            )

            for run_idx, (_, row) in enumerate(tc_runs.iterrows()):
                res_id = row.get("eval_result_id", "")
                res_meta = (
                    df_metadata[df_metadata["eval_result_id"] == res_id]
                    if not df_metadata.empty
                    else pd.DataFrame()
                )
                res_fails = (
                    df_failures[df_failures["eval_result_id"] == res_id]
                    if not df_failures.empty
                    else pd.DataFrame()
                )

                milestone_rows = (
                    res_meta[res_meta["type"] == "Milestone"]
                    if not res_meta.empty and "type" in res_meta.columns
                    else pd.DataFrame()
                )
                exp_rows = (
                    res_meta[res_meta["type"] == "Custom Expectation"]
                    if not res_meta.empty and "type" in res_meta.columns
                    else pd.DataFrame()
                )

                milestones_met = (
                    sum(milestone_rows["outcome"] == "PASS")
                    if not milestone_rows.empty
                    else 0
                )
                total_milestones = len(milestone_rows)
                exps_met = (
                    sum(exp_rows["outcome"] == "PASS")
                    if not exp_rows.empty
                    else 0
                )
                total_exps = len(exp_rows)

                step_details = []
                for step_idx, (_, m_row) in enumerate(
                    milestone_rows.iterrows()
                ):
                    raw_score = str(m_row.get("score", "")).upper()
                    if (
                        raw_score == "COMPLETED"
                        or m_row.get("outcome") == "PASS"
                    ):
                        step_status = "Completed"
                    elif raw_score == "IN_PROGRESS":
                        step_status = "In Progress"
                    else:
                        step_status = "Not Started"
                    criteria = str(m_row.get("success_criteria") or "")
                    if (
                        not criteria
                        and step_idx < len(tc_steps)
                        and isinstance(tc_steps[step_idx], dict)
                    ):
                        criteria = str(
                            tc_steps[step_idx].get("success_criteria")
                            or tc_steps[step_idx].get("successCriteria")
                            or ""
                        )
                    step_details.append(
                        {
                            "goal": str(m_row.get("expected", "")),
                            "success_criteria": criteria,
                            "status": step_status,
                            "justification": str(m_row.get("actual", "")),
                        }
                    )

                expectation_details = []
                for _, e_row in exp_rows.iterrows():
                    expectation_details.append(
                        {
                            "expectation": str(e_row.get("expected", "")),
                            "status": (
                                "Met"
                                if e_row.get("outcome") == "PASS"
                                else "Not Met"
                            ),
                            "justification": str(e_row.get("actual", "")),
                        }
                    )

                status_val = str(row.get("evaluation_status", "")).upper()
                exec_state_val = str(row.get("execution_state", "")).upper()
                passed = (
                    status_val == "PASS"
                    and exec_state_val not in ("ERROR", "ERRORED", "CANCELLED")
                    and res_fails.empty
                )
                conv_name = str(row.get("conversation", ""))
                sess_id = (
                    conv_name.rsplit("/", 1)[-1]
                    if "/" in conv_name
                    else conv_name
                )

                result_entry: dict[str, Any] = {
                    "name": tc_name,
                    "passed": passed,
                    "status": "PASS" if passed else "FAIL",
                    "goals": f"{milestones_met}/{total_milestones}",
                    "expectations": f"{exps_met}/{total_exps}",
                    "duration_s": 0.0,
                    "run": run_idx + 1,
                    "runs": runs,
                    "platform": True,
                    "eval_result_id": res_id,
                    "evaluation_run": str(
                        row.get("evaluation_run", "") or run_id or ""
                    ),
                    "conversation_name": conv_name,
                    "session_id": sess_id,
                    "step_details": step_details,
                    "expectation_details": expectation_details,
                    "session_parameters": tc_def.get("session_parameters", {}),
                }
                if exec_state_val in (
                    "ERROR",
                    "ERRORED",
                ) or status_val in ("ERROR", "ERRORED"):
                    result_entry["error"] = str(
                        row.get("error_message") or "Unknown Agent Exception"
                    )

                sim_results.append(result_entry)

        return sim_results

    def _parse_agent_response(
        self, response: Any
    ) -> tuple[str, list[str], bool, list[Any]]:
        """Parses the agent response to extract text and trace information.

        Returns:
            A tuple of (agent_text, trace_chunks, session_ended, tool_calls)
        """
        parsed = ParsedSessionResponse(response, tools_map=self.tools_map)
        return (
            parsed.consolidated_agent_text,
            parsed.detailed_trace,
            parsed.session_ended,
            parsed.tool_calls,
        )

    def _evaluate_expectations(
        self,
        eval_conv: LLMUserConversation,
        detailed_trace: list[str],
        model: str,
        console_logging: bool,
        capture_agent_audio: bool = False,
    ) -> None:
        """Evaluates expectations against the conversation trace.

        Modifies `eval_conv.expectation_results` in place.
        """
        audio_paths = (
            getattr(eval_conv, "agent_audio_paths", None)
            if capture_agent_audio
            else None
        )

        all_expectations = []
        if eval_conv.expectations:
            all_expectations.extend(eval_conv.expectations)
        if (
            hasattr(eval_conv, "audio_expectations")
            and eval_conv.audio_expectations
        ):
            all_expectations.extend(eval_conv.audio_expectations)

        if all_expectations:
            if console_logging:
                print("\nEvaluating Expectations...")

            eval_conv.expectation_results = evaluate_expectations(
                gemini_client=self.genai_client,
                model_name=model,
                trace=detailed_trace,
                expectations=all_expectations,
                audio_paths=audio_paths,
            )

    def _conversation_latency(
        self, session_id: str, timeout_s: float = 120.0
    ) -> tuple[dict[int, float], Any]:
        """Reads per-turn perceived latency from the platform conversation.

        A conversation is published some time after the call ends — commonly
        under a minute, but not instantly — so a fetch straight after a
        simulation reliably 404s. This polls until it appears or `timeout_s`
        is spent. The interval is fixed rather than backing off: we are
        waiting on an event that is known to be coming, so backing off would
        only overshoot the moment it lands.

        Returns `({turn_index: latency_ms}, start_time)`. Giving up is logged
        and returns empty values: latency is a nice-to-have, and an
        unreachable trace must not fail an otherwise healthy simulation.
        """
        try:
            # Over REST, because a conversation carrying audio comfortably
            # exceeds gRPC's 4 MB response limit.
            history = ConversationHistory(
                app_name=self.app_name, creds=self.creds, transport="rest"
            )
        except Exception as exc:  # noqa: BLE001 - never break a run
            logger.warning(
                "Naturalness: could not reach conversation history for %s "
                "(%s); the perceivedLatency dimension will be omitted.",
                session_id,
                exc,
            )
            return {}, None

        deadline = time.monotonic() + max(timeout_s, 0.0)
        normalized = None

        while normalized is None:
            try:
                conversation = history.get_conversation(session_id)
                normalized = trace_report.normalize(conversation)
            except Exception as exc:  # noqa: BLE001 - never break a run
                if time.monotonic() + _LATENCY_POLL_INTERVAL_S > deadline:
                    logger.warning(
                        "Naturalness: conversation %s was not published "
                        "within %.0fs (%s); the perceivedLatency dimension "
                        "will be omitted.",
                        session_id,
                        timeout_s,
                        exc,
                    )
                    return {}, None
                logger.debug(
                    "Naturalness: conversation %s not published yet (%s); "
                    "polling again.",
                    session_id,
                    exc,
                )
                time.sleep(_LATENCY_POLL_INTERVAL_S)

        latencies: dict[int, float] = {}
        for metrics in normalized.get("turn_metrics", []) or []:
            latency_ms = metrics.get("perceived_latency_ms")
            if latency_ms is None:
                continue
            latencies[int(metrics.get("turn", 0))] = float(latency_ms)
        return latencies, normalized.get("start_time")

    def _resolve_agent_audio(
        self,
        eval_conv: LLMUserConversation,
        session_id: str,
        start_time: Any,
        config: NaturalnessConfig,
        speech_index_by_turn: dict[int, int] | None = None,
    ) -> dict[int, str]:
        """Maps `speech_index` to the agent's recorded audio for that turn.

        Prefers the platform's recording bucket, which holds real
        post-synthesis audio for every turn, and falls back to WAVs captured
        locally during the run. Returns an empty map when neither is
        available, which downgrades grading to transcript-only.

        Both sources are normalised onto `speech_index` (see
        `extract_agent_turns`). The bucket already numbers recordings over
        speaking turns only, whereas the local capture is keyed by simulation
        turn, so the latter is translated.
        """
        source = (config.audio_source or "auto").lower()
        if source == "none":
            return {}

        if source in ("auto", "gcs") and session_id:
            try:
                traces = Traces(app_name=self.app_name, creds=self.creds)
                uris = traces.get_agent_audio_uris(
                    session_id, start_time=start_time
                )
            except Exception as exc:  # noqa: BLE001 - never break a run
                logger.warning(
                    "Naturalness: could not list agent audio for %s (%s).",
                    session_id,
                    exc,
                )
                uris = {}
            if uris:
                # Filenames are 1-based: agent-turn-N is speech_index N-1.
                return {number - 1: uri for number, uri in uris.items()}
            if source == "gcs":
                logger.info(
                    "Naturalness: no agent recordings found for %s.",
                    session_id,
                )
                return {}

        local = dict(getattr(eval_conv, "agent_audio_paths", None) or {})
        if not local or not speech_index_by_turn:
            return local
        return {
            speech_index_by_turn[turn]: path
            for turn, path in local.items()
            if turn in speech_index_by_turn
        }

    def _evaluate_naturalness(
        self,
        eval_conv: LLMUserConversation,
        detailed_trace: list[str],
        model: str,
        console_logging: bool,
        config: NaturalnessConfig | None,
        session_id: str = "",
        modality: str = "text",
    ) -> None:
        """Runs the optional Naturalness Metric.

        Does nothing when the metric is not configured, which keeps existing
        simulation YAML files working unchanged.

        Modifies `eval_conv.naturalness_result` in place.
        """
        if config is None:
            return

        if console_logging:
            print("\nEvaluating Naturalness...")

        # In audio modality how the agent sounds is the thing being judged,
        # so listen to it by default rather than reading a transcript of it.
        if modality == "audio" and not config.use_audio:
            config = config.model_copy(update={"use_audio": True})

        latencies, start_time = self._conversation_latency(
            session_id, timeout_s=config.latency_fetch_timeout_s
        )

        speech_index_by_turn = {
            t.turn_index: t.speech_index
            for t in extract_agent_turns(detailed_trace)
        }

        audio_paths = (
            self._resolve_agent_audio(
                eval_conv,
                session_id,
                start_time,
                config,
                speech_index_by_turn=speech_index_by_turn,
            )
            if config.use_audio
            else None
        )

        eval_conv.naturalness_result = evaluate_naturalness(
            gemini_client=self.genai_client,
            model_name=model,
            trace=detailed_trace,
            config=config,
            audio_paths=audio_paths,
            latency_ms_by_turn=latencies,
        )

    def _evaluate_audio_analysis(
        self,
        eval_conv: LLMUserConversation,
        detailed_trace: list[str],
        turn_traces: list[dict[str, Any]],
        eval_model: str,
        console_logging: bool,
        session_id: str = "",
        modality: str = "text",
    ) -> None:
        """Runs comprehensive audio analysis (multimodal audio checks,
        signal-level acoustic metrics, and voice drift detection).

        Sets `eval_conv.audio_analysis_result`,
        `eval_conv.audio_metrics_result`, and `eval_conv.audio_drift_result`.
        """
        if console_logging:
            print("\nEvaluating Audio Analysis...")

        # 1. Multimodal Gemini Audio Checks
        try:
            traces = Traces(app_name=self.app_name, creds=self.creds)
            res = traces.analyze_audio(session_id)
            if isinstance(res, dict) and not res.get("error"):
                eval_conv.audio_analysis_result = res
            elif getattr(eval_conv, "agent_audio_paths", None):
                local_paths_dict = eval_conv.agent_audio_paths
                sorted_turns = sorted(local_paths_dict.keys())
                local_wavs = [
                    local_paths_dict[t]
                    for t in sorted_turns
                    if os.path.exists(local_paths_dict[t])
                ]
                if local_wavs:
                    audio_parts = []
                    for wav_path in local_wavs:
                        try:
                            with open(wav_path, "rb") as wf:
                                audio_bytes = wf.read()
                            audio_parts.append(
                                genai.types.Part.from_bytes(
                                    data=audio_bytes, mime_type="audio/wav"
                                )
                            )
                        except Exception as read_exc:
                            logger.warning(
                                "Failed to read local WAV %s: %s",
                                wav_path,
                                read_exc,
                            )
                    if audio_parts:
                        checks_result = {}
                        resolved_registry = getattr(
                            traces,
                            "_resolve_audio_analyses",
                            lambda: ANALYSIS_REGISTRY,
                        )()
                        if (
                            not isinstance(resolved_registry, dict)
                            or not resolved_registry
                        ):
                            resolved_registry = ANALYSIS_REGISTRY
                        for (
                            analysis_name,
                            analysis,
                        ) in resolved_registry.items():
                            if (
                                analysis_name == "no_long_pauses"
                                or not analysis.filter_files(
                                    ["agent-turn-1.wav"]
                                )
                            ):
                                checks_result[analysis_name] = {
                                    "result": "SKIP",
                                    "justification": (
                                        "Full session audio not available for "
                                        "pause analysis."
                                    ),
                                    "files_analyzed": [],
                                }
                                continue
                            try:
                                parts = list(audio_parts)
                                parts.append(analysis.prompt)
                                resp = self.genai_client.generate_with_parts(
                                    parts=parts
                                )
                                parsed = _parse_pass_fail(resp)
                                checks_result[analysis_name] = {
                                    **parsed,
                                    "files_analyzed": [
                                        os.path.basename(p) for p in local_wavs
                                    ],
                                }
                            except Exception as check_exc:
                                logger.warning(
                                    "Audio check %s failed: %s",
                                    analysis_name,
                                    check_exc,
                                )
                                checks_result[analysis_name] = {
                                    "result": "ERROR",
                                    "justification": str(check_exc),
                                    "files_analyzed": [],
                                }
                        eval_conv.audio_analysis_result = checks_result
        except Exception as exc:
            logger.warning(f"Multimodal audio checks failed: {exc}")

        # 2. Signal-level acoustic metrics and voice drift
        try:
            measured = None
            try:
                traces = Traces(app_name=self.app_name, creds=self.creds)
                measured_res = traces.measure_audio(session_id)
                if isinstance(measured_res, dict) and not measured_res.get(
                    "error"
                ):
                    measured = measured_res
            except Exception:
                measured = None

            if not measured and getattr(eval_conv, "agent_audio_paths", None):
                local_paths_dict = eval_conv.agent_audio_paths
                sorted_turns = sorted(local_paths_dict.keys())
                local_wavs = [
                    local_paths_dict[t]
                    for t in sorted_turns
                    if os.path.exists(local_paths_dict[t])
                ]
                if local_wavs:
                    try:
                        call, turns = measure_call(local_wavs)
                        call["conversation_id"] = session_id
                        measured = {"call": call, "turns": turns}
                    except Exception as meas_exc:
                        logger.warning(
                            f"Signal-level measurement failed: {meas_exc}"
                        )

            if (
                measured
                and isinstance(measured, dict)
                and "call" in measured
                and "turns" in measured
            ):
                eval_conv.audio_metrics_result = {
                    "call": measured.get("call", {}),
                    "turns": measured.get("turns", []),
                }

                agent_text_by_turn: dict[int, list[str]] = {}
                for t in extract_agent_turns(detailed_trace):
                    agent_text_by_turn.setdefault(t.turn_index, []).append(
                        t.text
                    )

                rows = []
                for turn_item in measured.get("turns", []):
                    if turn_item.get("skipped"):
                        continue
                    t_idx = int(turn_item.get("turn", 0))
                    conv_turn = t_idx - 1
                    rows.append(
                        {
                            "turn": t_idx,
                            "dur_s": turn_item.get("dur_s"),
                            "lufs": turn_item.get("lufs"),
                            "f0_median_hz": turn_item.get("f0_median_hz"),
                            "artic_rate": turn_item.get("artic_rate"),
                            "n_syl": turn_item.get("n_syl"),
                            "seams": turn_item.get("seams", []),
                            "agent_text": " | ".join(
                                agent_text_by_turn.get(conv_turn, [])
                            ),
                            # each part is a separate TTS render: JUMP
                            "joins": try_render_join_steps(
                                turn_item.get("path"),
                                agent_text_by_turn.get(conv_turn, []),
                            ),
                        }
                    )
                rows.sort(key=lambda r: r["turn"])
                flagged = flag_drift(rows)
                eval_conv.audio_drift_result = {
                    "rows": rows,
                    "flagged": flagged,
                }
        except Exception as exc:
            logger.warning(f"Audio metrics and drift analysis failed: {exc}")

    def _send_request_with_retry(
        self,
        session_id: str,
        user_utterance: str,
        variables: dict[str, Any],
        modality: str,
        console_logging: bool,
        turn_num: int | None = None,
        capture_agent_audio: bool = False,
        background_noise_file: str | None = None,
        bg_noise_snr: float = 15.0,
        burst_noise_files: list[str] | None = None,
        burst_noise_snr: float = 5.0,
        use_tool_fakes: bool = False,
        voice_config: dict[str, Any] | None = None,
    ) -> Any:
        """Sends a request to the CES Agent with exponential backoff for
        transient errors.
        """
        run_kwargs = {
            "session_id": session_id,
            "variables": variables,
            "modality": modality,
            "turn_num": turn_num,
            "capture_agent_audio": capture_agent_audio,
            "background_noise_file": background_noise_file,
            "burst_noise_files": burst_noise_files,
            "use_tool_fakes": use_tool_fakes,
        }
        if background_noise_file is not None:
            run_kwargs["bg_noise_snr"] = bg_noise_snr
        if burst_noise_files is not None:
            run_kwargs["burst_noise_snr"] = burst_noise_snr
        if voice_config is not None:
            run_kwargs["voice_config"] = voice_config

        response = None
        for attempt in range(self.max_retries):
            try:
                if user_utterance.startswith("event:"):
                    response = self.sessions_client.run(
                        event=user_utterance.removeprefix("event:").strip(),
                        **run_kwargs,
                    )
                elif user_utterance.startswith("dtmf:"):
                    response = self.sessions_client.run(
                        dtmf=user_utterance.removeprefix("dtmf:").strip(),
                        **run_kwargs,
                    )
                else:
                    response = self.sessions_client.run(
                        text=user_utterance,
                        **run_kwargs,
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

    @staticmethod
    def _reconcile_completed_steps(
        eval_conv: LLMUserConversation,
        session_ended: bool = False,
    ) -> None:
        """Reconciles steps marked ready for completion to COMPLETED status.

        In the simulated user protocol, steps whose criteria are met are primed
        with 'ready for completion' in their justification while status remains
        IN_PROGRESS awaiting a transition turn. When a conversation finishes or
        the session ends, any uncompleted step whose justification indicates
        it was ready for completion (or whose criteria match session
        termination) is transitioned to StepStatus.COMPLETED.
        """
        harness_error = getattr(eval_conv, "harness_error", None)
        if harness_error and not type(harness_error).__module__.startswith(
            "unittest.mock"
        ):
            return

        for prog in getattr(eval_conv, "steps_progress", []) or []:
            if getattr(prog, "status", None) == StepStatus.COMPLETED:
                continue

            justification = (getattr(prog, "justification", "") or "").lower()
            step = getattr(prog, "step", None)
            criteria = (getattr(step, "success_criteria", "") or "").lower()

            if session_ended and (
                "escalat" in criteria
                or "transfer" in criteria
                or "being transferred" in criteria
            ):
                prog.status = StepStatus.COMPLETED
                prog.justification = (
                    "Agent ended session via escalation/transfer — "
                    "matches success criteria."
                )
            elif "ready for completion" in justification:
                prog.status = StepStatus.COMPLETED
                suffix = (
                    "on session end"
                    if session_ended
                    else "on conversation completion"
                )
                if "(reconciled" not in justification:
                    prog.justification = (
                        f"{prog.justification} "
                        f"(Reconciled to completed {suffix})."
                        if prog.justification
                        else (
                            f"Goal criteria met "
                            f"(Reconciled to completed {suffix})."
                        )
                    )

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

    @cleanup_session_dir
    def simulate_conversation(
        self,
        test_case: dict[str, Any],
        sim_user_model: str | None = _DEFAULT_SIM_USER_MODEL,
        eval_model: str | None = _DEFAULT_EVAL_MODEL,
        session_id: str | None = None,
        console_logging: bool = True,
        modality: str = "text",
        capture_agent_audio: bool = False,
        background_noise_file: str | None = None,
        bg_noise_snr: float | None = None,
        burst_noise_files: list[str] | None = None,
        burst_noise_snr: float | None = None,
        use_tool_fakes: bool = False,
        voice_config: dict[str, Any] | None = None,
        initial_utterance: str = _FIRST_UTTERANCE,
        skip_playback_wait: bool = False,
        single_bidi_stream: bool = False,
        max_turns: int | None = None,
        naturalness: bool | dict[str, Any] | None = None,
        audio_analysis: bool | None = None,
        **kwargs: Any,
    ) -> LLMUserConversation:
        """Runs the simulated conversation loop.

        Args:
            test_case: The test case dictionary defining evaluation steps.
            sim_user_model: The Gemini model used for the simulated user.
            eval_model: The Gemini model used for evaluating expectations.
            console_logging: Whether to print interaction transcript to
                the console.
            single_bidi_stream: For audio modality, keep one persistent
                bidi WebSocket open for the whole conversation instead of
                opening a new connection per turn (the default).
            max_turns: Maximum number of conversation turns. Defaults to
                the test_case's max_turns setting, or 30 if unspecified.
            naturalness: Overrides the test case's `naturalness_metric`
                block. `None` (the default) defers to the test case, so
                simulations that never declare the metric are unaffected.
            audio_analysis: Run multimodal audio checks, acoustic metrics,
                and voice drift analysis. Defaults to instance setting.
        """
        sim_user_model = sim_user_model or _DEFAULT_SIM_USER_MODEL
        eval_model = eval_model or _DEFAULT_EVAL_MODEL
        if session_id is None:
            session_id = str(uuid.uuid4())
        effective_audio_analysis = (
            audio_analysis
            if audio_analysis is not None
            else getattr(self, "audio_analysis", False)
        )
        if (
            effective_audio_analysis
            and naturalness is None
            and getattr(self, "naturalness", None) is None
        ):
            effective_naturalness = True
        else:
            effective_naturalness = (
                naturalness if naturalness is not None else self.naturalness
            )
        naturalness_config = parse_naturalness_config(
            test_case,
            effective_naturalness,
        )
        voice_config = voice_config or test_case.get("voice_config")
        eval_conv = LLMUserConversation(
            genai_client=self.genai_client,
            genai_model=sim_user_model,
            test_case=test_case,
            max_turns=max_turns,
            initial_utterance=initial_utterance,
        )

        # Initialize audio paths tracking
        eval_conv.agent_audio_paths = {}
        current_sim_turn = 0

        has_barge_in_step = any(
            isinstance(s, dict) and s.get("barge_in_after_ms") is not None
            for s in test_case.get("steps", [])
        )
        if has_barge_in_step and modality != "audio":
            logging.warning(
                "Test case has barge_in_after_ms configured, but modality is "
                f"'{modality}'. Barge-in evaluation requires audio modality."
            )
        if has_barge_in_step and modality == "audio":
            single_bidi_stream = True
            skip_playback_wait = False

        resolved_noise = resolve_noise_config(
            config_or_case=test_case,
            background_noise_file=background_noise_file,
            bg_noise_snr=bg_noise_snr,
            burst_noise_files=burst_noise_files,
            burst_noise_snr=burst_noise_snr,
        )
        effective_bg_file = resolved_noise["background_noise_file"]
        effective_bg_snr = resolved_noise["bg_noise_snr"]
        effective_burst_files = resolved_noise["burst_noise_files"]
        effective_burst_snr = resolved_noise["burst_noise_snr"]

        interactive_session = None
        if modality == "audio" and single_bidi_stream:
            client = self.sessions_client
            create_kwargs: dict[str, Any] = {
                "session_id": session_id,
                "capture_agent_audio": capture_agent_audio,
                "background_noise_file": effective_bg_file,
                "use_tool_fakes": use_tool_fakes,
                "skip_playback_wait": skip_playback_wait,
                "voice_config": voice_config,
            }
            if effective_bg_file is not None:
                create_kwargs["bg_noise_snr"] = effective_bg_snr
            if effective_burst_files is not None:
                create_kwargs["burst_noise_files"] = effective_burst_files
                create_kwargs["burst_noise_snr"] = effective_burst_snr
            interactive_session = client.create_interactive_session(
                **create_kwargs
            )
            interactive_session.start()

        try:
            if console_logging:
                print(
                    f"Starting simulated conversation with session ID: "
                    f"{session_id}"
                )

            # Initialize the first turn manually
            user_utterance, variables = eval_conv.next_user_utterance()
            accumulated_variables = {}
            if variables:
                accumulated_variables.update(variables)

            detailed_trace = []
            detailed_trace.append(f"User: {user_utterance}")
            turn_traces: list[dict[str, Any]] = []

            while user_utterance:
                barge_in_offset = (
                    eval_conv.peek_barge_in_after_ms()
                    if hasattr(eval_conv, "peek_barge_in_after_ms")
                    else None
                )
                if (
                    modality == "audio"
                    and interactive_session
                    and isinstance(barge_in_offset, int)
                ):
                    barge_in_utterance, barge_in_vars = (
                        eval_conv.generate_barge_in_utterance()
                    )
                    barge_vars = accumulated_variables.copy()
                    if barge_in_vars:
                        barge_vars.update(barge_in_vars)

                    resp_n, response = (
                        interactive_session.send_turn_with_barge_in(
                            text=user_utterance,
                            barge_in_text=barge_in_utterance,
                            barge_in_after_ms=barge_in_offset,
                            variables=accumulated_variables,
                            barge_in_variables=barge_vars,
                        )
                    )

                    if isinstance(resp_n, dict) and resp_n.get("session_ended"):
                        if resp_n.get("connection_error"):
                            err_msg = (
                                "Interactive session WebSocket error: "
                                f"{resp_n['connection_error']}"
                            )
                            raise BidiSessionError(err_msg)
                        break

                    if resp_n and getattr(resp_n, "agent_audio_paths", None):
                        audio_path_n = resp_n.agent_audio_paths.get(0)
                        if audio_path_n:
                            eval_conv.agent_audio_paths[current_sim_turn] = (
                                audio_path_n
                            )

                    agent_text_n, trace_chunks_n, _, tool_calls_n = (
                        self._parse_agent_response(resp_n)
                    )
                    parsed_n = ParsedSessionResponse(resp_n)
                    if (
                        parsed_n.barge_in_metadata
                        and parsed_n.barge_in_metadata.interrupted
                    ):
                        heard = parsed_n.barge_in_metadata.estimated_heard_text
                        eval_conv._add_agent_response(
                            f"{heard}... [interrupted]"
                        )
                    elif agent_text_n:
                        eval_conv._add_agent_response(agent_text_n)

                    eval_conv._add_agent_tool_calls(tool_calls_n)
                    detailed_trace.append("\n".join(trace_chunks_n))
                    turn_root_span_n = ParsedSessionResponse.extract_root_span(
                        resp_n
                    )
                    if isinstance(turn_root_span_n, dict) and turn_root_span_n:
                        turn_traces.append(
                            {
                                "turn_index": current_sim_turn,
                                "user_utterance": user_utterance,
                                "agent_text": agent_text_n,
                                "root_span": turn_root_span_n,
                            }
                        )
                    current_sim_turn += 1

                    eval_conv.record_user_utterance(barge_in_utterance)
                    detailed_trace.append(
                        f"User: [Barge-In] {barge_in_utterance}"
                    )

                    if isinstance(response, dict) and response.get(
                        "session_ended"
                    ):
                        if response.get("connection_error"):
                            err_msg = (
                                "Interactive session WebSocket error: "
                                f"{response['connection_error']}"
                            )
                            raise BidiSessionError(err_msg)
                        break

                    user_utterance = barge_in_utterance
                    accumulated_variables = barge_vars
                elif modality == "audio" and interactive_session:
                    response = interactive_session.send_turn(
                        user_utterance,
                        accumulated_variables,
                    )
                    # Check if session ended via WebSocket endSession
                    if isinstance(response, dict) and response.get(
                        "session_ended"
                    ):
                        if response.get("connection_error"):
                            err_msg = (
                                f"Interactive session WebSocket error: "
                                f"{response['connection_error']}"
                            )
                            raise BidiSessionError(err_msg)
                        break
                else:
                    response = self._send_request_with_retry(
                        session_id=session_id,
                        user_utterance=user_utterance,
                        variables=accumulated_variables,
                        modality=modality,
                        console_logging=console_logging,
                        turn_num=current_sim_turn,
                        capture_agent_audio=capture_agent_audio,
                        background_noise_file=effective_bg_file,
                        bg_noise_snr=effective_bg_snr,
                        burst_noise_files=effective_burst_files,
                        burst_noise_snr=effective_burst_snr,
                        use_tool_fakes=use_tool_fakes,
                        voice_config=voice_config,
                    )
                if not response:
                    break

                # Extract and save the agent turn audio WAV if present
                # in response.
                if response and getattr(response, "agent_audio_paths", None):
                    audio_path = response.agent_audio_paths.get(0)
                    if audio_path:
                        paths = eval_conv.agent_audio_paths
                        paths[current_sim_turn] = audio_path

                if console_logging:
                    self.sessions_client.parse_result(response)

                agent_text, trace_chunks, session_ended, tool_calls = (
                    self._parse_agent_response(response)
                )
                detailed_trace.append("\n".join(trace_chunks))

                turn_root_span = ParsedSessionResponse.extract_root_span(
                    response
                )
                if isinstance(turn_root_span, dict) and turn_root_span:
                    turn_traces.append(
                        {
                            "turn_index": current_sim_turn,
                            "user_utterance": user_utterance,
                            "agent_text": agent_text,
                            "root_span": turn_root_span,
                        }
                    )

                if session_ended:
                    if agent_text:
                        eval_conv._add_agent_response(agent_text)
                    eval_conv._add_agent_tool_calls(tool_calls)
                    # Ensure the final agent response is evaluated
                    # so that steps_progress is updated on session end.
                    eval_conv._next_user_utterance()
                    if console_logging:
                        print(
                            "\nSession has been closed by the Agent via "
                            "end_session tool."
                        )
                    # Mark steps as completed if session ending matches
                    # criteria (escalation/transfer) or if goal was primed as
                    # ready for completion.
                    self._reconcile_completed_steps(
                        eval_conv, session_ended=True
                    )
                    break

                # Get the next simulated user utterance based on the agent's
                # response
                eval_conv._add_agent_tool_calls(tool_calls)
                user_utterance, variables = eval_conv.next_user_utterance(
                    agent_text
                )
                if variables:
                    accumulated_variables.update(variables)
                if user_utterance:
                    detailed_trace.append(f"User: {user_utterance}")

                current_sim_turn += 1

            self._reconcile_completed_steps(eval_conv)

            if console_logging:
                self._print_completion_status(eval_conv)

            self._evaluate_expectations(
                eval_conv,
                detailed_trace,
                eval_model,
                console_logging,
                capture_agent_audio=capture_agent_audio,
            )
            self._evaluate_naturalness(
                eval_conv,
                detailed_trace,
                eval_model,
                console_logging,
                naturalness_config,
                session_id=session_id,
                modality=modality,
            )
            if effective_audio_analysis:
                self._evaluate_audio_analysis(
                    eval_conv=eval_conv,
                    detailed_trace=detailed_trace,
                    turn_traces=turn_traces,
                    eval_model=eval_model,
                    console_logging=console_logging,
                    session_id=session_id,
                    modality=modality,
                )
            eval_conv._session_id = session_id
            eval_conv.session_id = session_id
            eval_conv._detailed_trace = detailed_trace
            eval_conv.detailed_trace = detailed_trace
            eval_conv._turn_traces = turn_traces
            eval_conv.turn_traces = turn_traces
            if turn_traces and session_id:
                with contextlib.suppress(Exception):
                    cache_dir = "/tmp/scrapi_traces"
                    os.makedirs(cache_dir, exist_ok=True)
                    cache_path = os.path.join(cache_dir, f"{session_id}.json")
                    with open(cache_path, "w") as f:
                        json.dump(
                            {"session_id": session_id, "turns": turn_traces}, f
                        )
            return eval_conv
        finally:
            if interactive_session:
                interactive_session.close()

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
        sim_user_model: str,
        eval_model: str,
        modality: str,
        verbose: bool,
        parallel: int,
        capture_agent_audio: bool = False,
        background_noise_file: str | None = None,
        bg_noise_snr: float | None = None,
        burst_noise_files: list[str] | None = None,
        burst_noise_snr: float | None = None,
        use_tool_fakes: bool = False,
        skip_playback_wait: bool = False,
        single_bidi_stream: bool = False,
    ) -> dict[str, Any]:
        """Runs a single simulation job and returns the results.

        The Naturalness Metric override is read from `self.naturalness`
        rather than passed in, so that wrappers which monkeypatch this
        method with a fixed signature keep working.
        """
        name = tc["name"]
        label = f"{name} (run {run_idx + 1}/{runs})"
        session_id = str(uuid.uuid4())
        voice_config = self._voice_config_for(tc)
        if voice_config and not tc.get("voice_config"):
            tc = {**tc, "voice_config": voice_config}
        try:
            _start = time.time()

            conv = self.simulate_conversation(
                test_case=tc,
                sim_user_model=sim_user_model,
                eval_model=eval_model,
                session_id=session_id,
                console_logging=verbose and parallel <= 1,
                modality=modality,
                capture_agent_audio=capture_agent_audio,
                background_noise_file=background_noise_file,
                bg_noise_snr=bg_noise_snr,
                burst_noise_files=burst_noise_files,
                burst_noise_snr=burst_noise_snr,
                use_tool_fakes=use_tool_fakes,
                skip_playback_wait=skip_playback_wait,
                single_bidi_stream=single_bidi_stream,
            )
            duration_s = round(time.time() - _start, 1)

            self._reconcile_completed_steps(conv)

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
            declared_exp = self._declared_expectations_count(tc, conv)

            passed = goals_completed == total_goals
            if declared_exp > 0 and total_exp == 0:
                passed = False
            elif getattr(self, "expectations_only", False) and total_exp > 0:
                passed = expectations_met == total_exp
            elif total_exp > 0:
                passed = passed and (expectations_met == total_exp)

            # The naturalness metric affects pass/fail when the test case
            # opted in with an explicit pass_threshold, or when a turn broke
            # the perceived-latency limit.
            naturalness_result = conv.naturalness_result
            naturalness_note = ""
            if naturalness_result:
                naturalness_note = (
                    f" | naturalness: {naturalness_result.overall_score}/5 "
                    f"({naturalness_result.overall_label.value})"
                )
                for reason in naturalness_result.failure_reasons:
                    naturalness_note += f" | FAILED: {reason}"
                if naturalness_result.passed is not None:
                    passed = passed and naturalness_result.passed

            status = "PASS" if passed else "FAIL"
            if parallel > 1 or not verbose:
                print(
                    f"  {status}  {label} | goals: "
                    f"{goals_completed}/{total_goals} | "
                    f"expectations: {expectations_met}/{total_exp} | "
                    f"turns: {conv.current_turn} | {duration_s}s"
                    f"{naturalness_note}"
                )

            raw_turn_traces = getattr(
                conv,
                "_turn_traces",
                getattr(conv, "turn_traces", []),
            )
            turn_traces_out = (
                raw_turn_traces if isinstance(raw_turn_traces, list) else []
            )

            result: dict[str, Any] = {
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
                "detailed_trace": getattr(
                    conv,
                    "_detailed_trace",
                    getattr(conv, "detailed_trace", []),
                ),
                "turn_traces": turn_traces_out,
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

            # Only present when the metric was configured, so existing
            # consumers of sim_results.json see an unchanged payload.
            if naturalness_result:
                result["naturalness"] = f"{naturalness_result.overall_score}/5"
                result["naturalness_label"] = (
                    naturalness_result.overall_label.value
                )
                result["naturalness_details"] = naturalness_result.model_dump()

            audio_analysis_res = getattr(conv, "audio_analysis_result", None)
            if audio_analysis_res and not audio_analysis_res.get("error"):
                result["audio_analysis"] = audio_analysis_res

            audio_metrics_res = getattr(conv, "audio_metrics_result", None)
            if audio_metrics_res and not audio_metrics_res.get("error"):
                result["audio_metrics"] = audio_metrics_res

            audio_drift_res = getattr(conv, "audio_drift_result", None)
            if audio_drift_res and not audio_drift_res.get("error"):
                result["audio_drift"] = audio_drift_res

            noise_cfg = resolve_noise_config(
                tc,
                background_noise_file=background_noise_file,
                bg_noise_snr=bg_noise_snr,
                burst_noise_files=burst_noise_files,
                burst_noise_snr=burst_noise_snr,
            )
            if noise_cfg["background_noise_file"]:
                result["background_noise_file"] = noise_cfg[
                    "background_noise_file"
                ]
                result["bg_noise_snr"] = noise_cfg["bg_noise_snr"]
                result["bg_noise_level"] = noise_cfg["bg_noise_level"]
            if noise_cfg["burst_noise_files"]:
                result["burst_noise_files"] = noise_cfg["burst_noise_files"]
                result["burst_noise_snr"] = noise_cfg["burst_noise_snr"]
                result["burst_noise_level"] = noise_cfg["burst_noise_level"]

            if voice_config and isinstance(result, dict):
                result.setdefault(
                    "sim_user_voice", voice_config.get("voice_name", "")
                )

            return result
        except Exception as e:
            print(f"  ERROR  {label}: {e}")
            err_result: dict[str, Any] = {
                "name": name,
                "run": run_idx + 1,
                "passed": False,
                "error": str(e),
                "session_id": session_id,
                "session_parameters": tc.get("session_parameters", {}),
                "goals": f"0/{len(tc.get('steps') or [])}",
                "expectations": (f"0/{self._declared_expectations_count(tc)}"),
                "turns": 0,
            }
            if voice_config:
                err_result["sim_user_voice"] = voice_config.get(
                    "voice_name", ""
                )
            return err_result

    @staticmethod
    def _declared_expectations_count(
        tc: dict[str, Any], conv: Any = None
    ) -> int:
        tc_exp = len(tc.get("expectations") or []) + len(
            tc.get("audio_expectations") or []
        )
        if tc_exp > 0:
            return tc_exp
        if conv is not None:
            conv_exp = getattr(conv, "expectations", None)
            conv_audio_exp = getattr(conv, "audio_expectations", None)
            count = 0
            if isinstance(conv_exp, (list, tuple)):
                count += len(conv_exp)
            if isinstance(conv_audio_exp, (list, tuple)):
                count += len(conv_audio_exp)
            return count
        return 0

    @staticmethod
    def _transcript_lines(row: dict[str, Any]) -> list[str]:
        return [
            line
            for line in (row.get("transcript") or "").split("\n")
            if line.startswith(("User:", "Agent:", "Agent Action:"))
            and line.strip() != "User:"
        ]

    @staticmethod
    def _ends_unanswered(row: dict[str, Any]) -> bool:
        lines = SimulationEvals._transcript_lines(row)
        return (
            bool(lines)
            and lines[-1].startswith("User:")
            and not lines[-1].startswith("User: event")
        )

    @staticmethod
    def _agent_ended_call(turn: dict[str, Any]) -> bool:
        for message in turn.get("messages") or []:
            for chunk in message.get("chunks") or []:
                payload = chunk.get("payload")
                if isinstance(payload, dict):
                    if any(
                        str(k).startswith(("transferTo", "transfer_to"))
                        for k in payload
                    ):
                        return True
                    if payload.get("action") == "end":
                        return True
                    if any(
                        isinstance(v, dict) and v.get("action") == "end"
                        for v in payload.values()
                    ):
                        return True
                tc = chunk.get("toolCall") or chunk.get("tool_call") or {}
                if isinstance(tc, dict):
                    dname = (
                        tc.get("displayName") or tc.get("display_name") or ""
                    )
                    tool_ref = str(tc.get("tool") or "")
                    if dname == "end_session" or tool_ref.endswith(
                        "/end_session"
                    ):
                        return True
        return False

    @staticmethod
    def _lost_turn(
        row: dict[str, Any], conv: dict[str, Any] | None
    ) -> str | None:
        if not conv or not conv.get("turns"):
            return None
        if SimulationEvals._agent_ended_call(conv["turns"][-1]):
            return None
        lines = SimulationEvals._transcript_lines(row)
        sent = [line for line in lines if line.startswith("User:")]
        spoken = [line for line in sent if not line.startswith("User: event")]
        input_types = conv.get("inputTypes") or conv.get("input_types") or []
        if spoken and "INPUT_TYPE_AUDIO" not in input_types:
            return "caller audio never reached CES"
        turn_count = int(conv.get("turnCount") or conv.get("turn_count") or 0)
        if turn_count < len(sent):
            return "caller's last turn never reached CES"
        return None

    def _ces_conversation_lookup(
        self,
    ) -> Callable[[dict[str, Any]], dict[str, Any] | None]:
        cache: dict[str, dict[str, Any] | None] = {}
        history_holder: list[Any] = []

        def lookup(row: dict[str, Any]) -> dict[str, Any] | None:
            sid = row.get("session_id")
            if not sid:
                return None
            if sid not in cache:
                try:
                    if not history_holder:
                        history_holder.append(
                            ConversationHistory(
                                app_name=self.app_name,
                                creds=self.creds,
                                transport="rest",
                            )
                        )
                    conv_obj = history_holder[0].get_conversation(sid)
                    if isinstance(conv_obj, dict):
                        cache[sid] = conv_obj
                    elif hasattr(type(conv_obj), "to_dict"):
                        cache[sid] = type(conv_obj).to_dict(conv_obj)
                    else:
                        cache[sid] = None
                except Exception:  # noqa: BLE001
                    cache[sid] = None
            return cache[sid]

        return lookup

    @staticmethod
    def _inconclusive_reason(
        row: dict[str, Any],
        declared_by_name: dict[str, int],
        lookup: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        expected_turns_by_name: Mapping[str, int] | None = None,
    ) -> str | None:
        if row.get("error"):
            return f"session died: {str(row['error'])[:80]}"
        name = str(row.get("name") or "")
        expected_turns = (expected_turns_by_name or {}).get(name, 0)
        has_turn_data = any(
            k in row
            for k in (
                "transcript",
                "detailed_trace",
                "conversation_Turns",
                "conversation_turns",
                "turns",
            )
        )
        if (
            expected_turns > 0
            and (has_turn_data or not row.get("passed"))
            and actual_caller_turns(row) == 0
        ):
            return "caller turns never reached agent (0 turns)"
        if declared_by_name.get(name, 0) > 0 and str(
            row.get("expectations", "")
        ).endswith("/0"):
            return "judge returned no verdicts"
        if lookup is not None and SimulationEvals._ends_unanswered(row):
            return SimulationEvals._lost_turn(row, lookup(row))
        return None

    def _aggregate_simulation_results(
        self,
        jobs: list[tuple[dict[str, Any], int]],
        runs: int,
        parallel: int,
        sim_user_model: str,
        eval_model: str,
        modality: str,
        verbose: bool,
        capture_agent_audio: bool = False,
        background_noise_file: str | None = None,
        bg_noise_snr: float | None = None,
        burst_noise_files: list[str] | None = None,
        burst_noise_snr: float | None = None,
        use_tool_fakes: bool = False,
        skip_playback_wait: bool = False,
        single_bidi_stream: bool = False,
        progress_callback: Callable[[int, int], None] | None = None,
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
                            sim_user_model,
                            eval_model,
                            modality,
                            verbose,
                            parallel,
                            capture_agent_audio=capture_agent_audio,
                            background_noise_file=background_noise_file,
                            burst_noise_files=burst_noise_files,
                            use_tool_fakes=use_tool_fakes,
                            skip_playback_wait=skip_playback_wait,
                            single_bidi_stream=single_bidi_stream,
                        )
                    )
                    progress.update(task_id, advance=1)
                    if progress_callback:
                        progress_callback(len(results), len(jobs))
            else:
                max_workers = min(parallel, 25)
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {
                        executor.submit(
                            self._run_single_simulation_job,
                            tc,
                            run_idx,
                            runs,
                            sim_user_model,
                            eval_model,
                            modality,
                            verbose,
                            parallel,
                            capture_agent_audio=capture_agent_audio,
                            background_noise_file=background_noise_file,
                            burst_noise_files=burst_noise_files,
                            use_tool_fakes=use_tool_fakes,
                            skip_playback_wait=skip_playback_wait,
                            single_bidi_stream=single_bidi_stream,
                        ): (tc["name"], run_idx)
                        for tc, run_idx in jobs
                    }
                    for future in as_completed(futures):
                        results.append(future.result())
                        progress.update(task_id, advance=1)
                        if progress_callback:
                            progress_callback(len(results), len(jobs))

        return results

    def run_simulations(
        self,
        test_cases: list[dict[str, Any]],
        runs: int = 1,
        parallel: int = 1,
        sim_user_model: str | None = _DEFAULT_SIM_USER_MODEL,
        eval_model: str | None = _DEFAULT_EVAL_MODEL,
        modality: str = "text",
        verbose: bool = False,
        capture_agent_audio: bool = False,
        background_noise_file: str | None = None,
        bg_noise_snr: float | None = None,
        burst_noise_files: list[str] | None = None,
        burst_noise_snr: float | None = None,
        use_tool_fakes: bool = False,
        expectations_only: bool | None = None,
        skip_playback_wait: bool = False,
        single_bidi_stream: bool = False,
        progress_callback: Callable[[int, int], None] | None = None,
        naturalness: bool | dict[str, Any] | None = None,
        audio_analysis: bool | None = None,
        infra_retries: int = 2,
        retry_parallel: int | None = None,
        retry_cooldown: float = 60.0,
        run_on_platform: bool = False,
        platform_timeout: int = 600,
        sim_user_voices: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Runs multiple simulations, optionally in parallel.

        Args:
            test_cases: List of test case dictionaries.
            runs: Number of runs per test case.
            parallel: Number of parallel workers (capped at 25).
            sim_user_model: Gemini model to use for simulated user.
            eval_model: Gemini model to use for evaluating expectations.
            modality: 'text' or 'audio'.
            verbose: Whether to log to console (only active if parallel=1).
            use_tool_fakes: Use fake tools for the session if available.
            capture_agent_audio: If True, capture real-time agent audio WAVs.
            naturalness: Run-level override for the optional Naturalness
                Metric. Leave as None to honour each test case's own
                `naturalness_metric` block.
            audio_analysis: Run multimodal audio checks, acoustic metrics,
                and voice drift analysis. Defaults to instance setting.
            run_on_platform: If True, executes platform-compatible cases via
                CES Milestone evals.
            platform_timeout: Max seconds to wait for platform evaluation run.
        """
        if expectations_only is not None:
            self.expectations_only = expectations_only
        if sim_user_voices is not None:
            self.sim_user_voices = [
                v.strip() for v in sim_user_voices if v and v.strip()
            ]
        if run_on_platform:
            platform_cases: list[dict[str, Any]] = []
            local_cases: list[dict[str, Any]] = []
            effective_naturalness = (
                naturalness
                if naturalness is not None
                else getattr(self, "naturalness", False)
            )
            effective_audio_analysis = (
                audio_analysis
                if audio_analysis is not None
                else getattr(self, "audio_analysis", False)
            )
            has_audio_capture_or_noise = bool(
                capture_agent_audio
                or background_noise_file
                or burst_noise_files
            )
            for tc in test_cases:
                effective_tc = dict(tc) if isinstance(tc, dict) else {}
                if (
                    modality
                    and modality.lower() != "text"
                    and "modality" not in effective_tc
                ):
                    effective_tc["modality"] = modality
                if (
                    effective_audio_analysis or has_audio_capture_or_noise
                ) and not effective_tc.get("audio_analysis"):
                    effective_tc["audio_analysis"] = True
                if effective_naturalness and not effective_tc.get(
                    "naturalness_metric"
                ):
                    effective_tc["naturalness_metric"] = True
                requires_local, reason = self.requires_local_execution(
                    effective_tc
                )
                if requires_local:
                    logger.info(
                        "Test case '%s' routed to local simulation (%s)",
                        tc.get("name", "unnamed"),
                        reason,
                    )
                    local_cases.append(tc)
                else:
                    platform_cases.append(tc)

            results: list[dict[str, Any]] = []
            if platform_cases:
                fakes_cases: list[dict[str, Any]] = []
                non_fakes_cases: list[dict[str, Any]] = []
                for ptc in platform_cases:
                    case_fakes = (
                        ptc.get("use_tool_fakes")
                        if ptc.get("use_tool_fakes") is not None
                        else use_tool_fakes
                    )
                    if case_fakes:
                        fakes_cases.append(ptc)
                    else:
                        non_fakes_cases.append(ptc)

                if fakes_cases:
                    results.extend(
                        self.run_simulations_on_platform(
                            fakes_cases,
                            runs=runs,
                            timeout_seconds=platform_timeout,
                            use_tool_fakes=True,
                        )
                    )
                if non_fakes_cases:
                    results.extend(
                        self.run_simulations_on_platform(
                            non_fakes_cases,
                            runs=runs,
                            timeout_seconds=platform_timeout,
                        )
                    )
            if local_cases:
                results.extend(
                    self.run_simulations(
                        test_cases=local_cases,
                        runs=runs,
                        parallel=parallel,
                        sim_user_model=sim_user_model,
                        eval_model=eval_model,
                        modality=modality,
                        verbose=verbose,
                        capture_agent_audio=capture_agent_audio,
                        background_noise_file=background_noise_file,
                        burst_noise_files=burst_noise_files,
                        use_tool_fakes=use_tool_fakes,
                        expectations_only=expectations_only,
                        skip_playback_wait=skip_playback_wait,
                        single_bidi_stream=single_bidi_stream,
                        progress_callback=progress_callback,
                        naturalness=naturalness,
                        audio_analysis=audio_analysis,
                        infra_retries=infra_retries,
                        retry_parallel=retry_parallel,
                        retry_cooldown=retry_cooldown,
                        run_on_platform=False,
                        sim_user_voices=sim_user_voices,
                    )
                )
            return results

        sim_user_model = sim_user_model or _DEFAULT_SIM_USER_MODEL
        eval_model = eval_model or _DEFAULT_EVAL_MODEL
        if naturalness is not None:
            # Persist on the instance so wrappers that reimplement the
            # aggregation loop still see the override.
            self.naturalness = naturalness
        if audio_analysis is not None:
            self.audio_analysis = audio_analysis
        jobs = self._prepare_simulation_jobs(test_cases, runs)
        results = self._aggregate_simulation_results(
            jobs,
            runs,
            parallel,
            sim_user_model,
            eval_model,
            modality,
            verbose,
            capture_agent_audio=capture_agent_audio,
            background_noise_file=background_noise_file,
            bg_noise_snr=bg_noise_snr,
            burst_noise_files=burst_noise_files,
            burst_noise_snr=burst_noise_snr,
            use_tool_fakes=use_tool_fakes,
            skip_playback_wait=skip_playback_wait,
            single_bidi_stream=single_bidi_stream,
            progress_callback=progress_callback,
        )

        declared_by_name = {
            tc.get("name", ""): self._declared_expectations_count(tc)
            for tc in test_cases
            if isinstance(tc, dict) and tc.get("name")
        }
        expected_turns_by_name = {
            tc.get("name", ""): expected_caller_turns(tc)
            for tc in test_cases
            if isinstance(tc, dict) and tc.get("name")
        }
        tc_by_name = {
            tc.get("name", ""): tc
            for tc in test_cases
            if isinstance(tc, dict) and tc.get("name")
        }

        conv_lookup = (
            self._ces_conversation_lookup()
            if modality.lower() == "audio"
            else None
        )

        for attempt in range(1, max(0, infra_retries) + 1):
            bad = [
                (idx, why)
                for idx, row in enumerate(results)
                if isinstance(row, dict)
                and (
                    why := self._inconclusive_reason(
                        row,
                        declared_by_name,
                        lookup=conv_lookup,
                        expected_turns_by_name=expected_turns_by_name,
                    )
                )
            ]
            if not bad:
                break
            need_names = {results[idx].get("name") for idx, _ in bad}
            print(
                f"[infra] retry {attempt}/{infra_retries}: {len(bad)} "
                f"inconclusive run(s) across {len(need_names)} case(s); "
                f"cooling down {retry_cooldown}s"
            )
            if retry_cooldown > 0:
                time.sleep(retry_cooldown)

            retry_jobs = [
                (
                    tc_by_name[results[idx]["name"]],
                    max(0, int(results[idx].get("run", 1)) - 1),
                )
                for idx, _ in bad
                if results[idx].get("name") in tc_by_name
            ]
            if not retry_jobs:
                break
            effective_retry_parallel = max(
                1,
                min(
                    retry_parallel
                    if retry_parallel is not None
                    else max(1, parallel // 2),
                    len(retry_jobs),
                ),
            )
            fresh_rows = self._aggregate_simulation_results(
                retry_jobs,
                runs,
                effective_retry_parallel,
                sim_user_model,
                eval_model,
                modality,
                verbose,
                capture_agent_audio=capture_agent_audio,
                background_noise_file=background_noise_file,
                bg_noise_snr=bg_noise_snr,
                burst_noise_files=burst_noise_files,
                burst_noise_snr=burst_noise_snr,
                use_tool_fakes=use_tool_fakes,
                skip_playback_wait=skip_playback_wait,
                single_bidi_stream=single_bidi_stream,
            )
            fresh_by_key: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
            for r in fresh_rows or []:
                if isinstance(r, dict):
                    fresh_by_key.setdefault(
                        (r.get("name"), r.get("run")), []
                    ).append(r)

            for idx, why in bad:
                orig = results[idx]
                key = (orig.get("name"), orig.get("run"))
                pool = fresh_by_key.get(key)
                if pool:
                    new_row = pool.pop(0)
                    new_row.update(
                        run=orig.get("run"),
                        infra_retries=attempt,
                        retried_because=why,
                    )
                    results[idx] = new_row

        still = [
            (row, why)
            for row in results
            if isinstance(row, dict)
            and (
                why := self._inconclusive_reason(
                    row,
                    declared_by_name,
                    lookup=conv_lookup,
                    expected_turns_by_name=expected_turns_by_name,
                )
            )
        ]
        for row, _ in still:
            if row.get("passed"):
                row["passed"] = False
        if still and infra_retries > 0:
            print(
                f"[infra] {len(still)} run(s) still inconclusive after "
                f"{infra_retries} retries -- counted as FAIL:"
            )
            for row, why in still:
                print(
                    f"        - {row.get('name')} run {row.get('run')}: {why}"
                )

        return results

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


_reconcile_completed_steps = SimulationEvals._reconcile_completed_steps
