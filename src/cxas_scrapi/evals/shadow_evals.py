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

"""Shadow evaluation classes for replaying past conversations on CXAS Agents."""

import enum
import io
import json
import logging
import os
import re
import time
import typing
import uuid
import wave
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import pandas as pd
import pydantic
import yaml
from rich.progress import Progress

try:
    from pydub import AudioSegment
except ImportError:
    AudioSegment = None

from cxas_scrapi.core.apps import Apps
from cxas_scrapi.core.audio_transformer import (
    AUDIO_CHANNELS,
    AUDIO_SAMPLE_RATE_HZ,
    AUDIO_SAMPLE_WIDTH,
)
from cxas_scrapi.core.response_parser import ParsedSessionResponse
from cxas_scrapi.core.sessions import BidiSessionError, Sessions
from cxas_scrapi.core.tools import Tools
from cxas_scrapi.core.traces import Traces
from cxas_scrapi.evals.naturalness import (
    NaturalnessConfig,
    NaturalnessResult,
    evaluate_naturalness,
    extract_agent_turns,
    parse_naturalness_config,
)
from cxas_scrapi.evals.simulation_evals import (
    _DEFAULT_GEMINI_MODEL,
    _FIRST_UTTERANCE,
    _MAX_TURNS,
    Conversation,
    Step,
    StepProgress,
    StepStatus,
    cleanup_session_dir,
)
from cxas_scrapi.prompts import llm_user_prompts
from cxas_scrapi.utils.eval_utils import (
    ExpectationResult,
    ExpectationStatus,
    evaluate_expectations,
)
from cxas_scrapi.utils.gcs_utils import GCSUtils
from cxas_scrapi.utils.gemini import GeminiGenerate
from cxas_scrapi.utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class ShadowDecisionType(str, enum.Enum):
    """Decision modes for the Shadow Evaluation simulated user."""

    USE_PAST_AUDIO = "use_past_audio"
    GENERATE_TTS = "generate_tts"
    END_CONVERSATION = "end_conversation"


class ShadowPastTurn(pydantic.BaseModel):
    """Represents a single user turn from a historical conversation."""

    model_config = pydantic.ConfigDict(arbitrary_types_allowed=True)

    turn_index: int
    user_transcript: str = ""
    preceding_agent_response: str = ""
    audio_uri: str | None = None
    audio_path: str | None = None
    has_audio: bool = False
    used: bool = False
    audio_bytes: bytes | None = pydantic.Field(default=None, exclude=True)


class ShadowTurnLog(pydantic.BaseModel):
    """Logs the Shadow SimUser decision and audio source for a single turn."""

    sim_turn: int
    decision: str
    selected_past_turn_index: int | None = None
    user_utterance: str = ""
    audio_source: str = ""
    decision_justification: str = ""
    agent_response: str = ""


class ShadowTestCase(pydantic.BaseModel):
    """Configuration schema for a single ShadowEval test case.

    Requires `conversation_id` and non-empty natural-language `expectations`
    that define the pass criteria for replaying the conversation.
    """

    name: str = ""
    conversation_id: str
    project_id: str | None = None
    location: str | None = None
    app_id: str | None = None
    gcs_bucket: str | None = None
    expectations: list[str | dict[str, Any]]
    audio_expectations: list[str | dict[str, Any]] = pydantic.Field(
        default_factory=list
    )
    goal: str = ""
    response_guide: str = ""
    steps: list[Step] = pydantic.Field(default_factory=list)
    session_parameters: dict[str, Any] = pydantic.Field(default_factory=dict)
    max_turns: int | None = None
    tags: list[str] = pydantic.Field(default_factory=list)
    voice_config: dict[str, Any] | None = None
    naturalness_metric: bool | dict[str, Any] | None = None
    initial_utterance: str = _FIRST_UTTERANCE

    @pydantic.model_validator(mode="after")
    def validate_test_case(self) -> "ShadowTestCase":
        conv_id = (self.conversation_id or "").strip()
        if not conv_id:
            raise ValueError(
                "ShadowTestCase requires a non-empty 'conversation_id'."
            )
        self.conversation_id = conv_id

        if not self.name:
            self.name = f"shadow_{conv_id}"

        valid_exps = []
        for exp in self.expectations or []:
            if isinstance(exp, str) and exp.strip():
                valid_exps.append(exp.strip())
            elif (
                isinstance(exp, dict)
                and str(exp.get("expectation", "")).strip()
            ):
                valid_exps.append(exp)
        if not valid_exps:
            raise ValueError(
                f"ShadowTestCase '{self.name}' (conversation_id="
                f"'{self.conversation_id}') must define at least one "
                "natural-language expectation in 'expectations' to specify "
                "pass criteria."
            )
        self.expectations = valid_exps
        return self


class ShadowReport:
    """A report containing Turn Decisions, Goals, and Expectations
    DataFrames.
    """

    def __init__(
        self,
        turns_df: pd.DataFrame,
        goals_df: pd.DataFrame | None = None,
        expectations_df: pd.DataFrame | None = None,
        naturalness_df: pd.DataFrame | None = None,
        naturalness_headline: str = "",
    ) -> None:
        self.turns_df = turns_df
        self.goals_df = goals_df
        self.expectations_df = expectations_df
        self.naturalness_df = naturalness_df
        self.naturalness_headline = naturalness_headline

    def __str__(self) -> str:
        green = "\033[1;32m"
        yellow = "\033[1;33m"
        red = "\033[1;31m"
        reset = "\033[0m"

        turns_str = (
            self.turns_df.to_string()
            if not self.turns_df.empty
            else "(No turns recorded)"
        )
        turns_str = turns_str.replace(
            "use_past_audio", f"{green}use_past_audio{reset}"
        )
        turns_str = turns_str.replace(
            "generate_tts", f"{yellow}generate_tts{reset}"
        )
        res = "--- Shadow Turn Decisions ---\n" + turns_str

        if self.goals_df is not None and not self.goals_df.empty:
            goals_str = self.goals_df.to_string()
            goals_str = goals_str.replace(
                "Completed", f"{green}Completed{reset}"
            )
            goals_str = goals_str.replace(
                "Not Started", f"{red}Not Started{reset}"
            )
            goals_str = goals_str.replace(
                "In Progress", f"{yellow}In Progress{reset}"
            )
            res += "\n\n--- Goal Progress ---\n" + goals_str

        if self.expectations_df is not None and not self.expectations_df.empty:
            exp_str = self.expectations_df.to_string()
            exp_str = exp_str.replace("Not Met", f"{red}Not Met{reset}")
            exp_str = re.sub(r"(?<!Not )Met\b", f"{green}Met{reset}", exp_str)
            res += "\n\n--- Expectations ---\n" + exp_str

        if self.naturalness_df is not None and not self.naturalness_df.empty:
            nat_str = self.naturalness_df.to_string()
            nat_str = nat_str.replace("Bot-like", f"{red}Bot-like{reset}")
            nat_str = nat_str.replace("Human-like", f"{green}Human-like{reset}")
            res += "\n\n--- Naturalness ---\n"
            if self.naturalness_headline:
                res += self.naturalness_headline + "\n"
            res += nat_str

        return res

    def _repr_html_(self) -> str:
        html = "<h3>Shadow Turn Decisions</h3>" + self.turns_df._repr_html_()
        if self.goals_df is not None and not self.goals_df.empty:
            html += "<h3>Goal Progress</h3>" + self.goals_df._repr_html_()
        if self.expectations_df is not None and not self.expectations_df.empty:
            html += "<h3>Expectations</h3>" + self.expectations_df._repr_html_()
        if self.naturalness_df is not None and not self.naturalness_df.empty:
            html += "<h3>Naturalness</h3>"
            if self.naturalness_headline:
                html += f"<p><b>{self.naturalness_headline}</b></p>"
            html += self.naturalness_df._repr_html_()
        return html


def extract_pcm_bytes_from_wav(wav_bytes: bytes) -> bytes:
    """Extracts 16kHz, 1-channel, 16-bit LINEAR16 PCM bytes from WAV bytes.

    If the WAV header indicates a different sample rate, channel count, or
    sample width, converts the audio via pydub.AudioSegment when available.
    If `wav_bytes` does not have a RIFF header, returns `wav_bytes` as-is.
    """
    if not wav_bytes:
        return b""
    if not wav_bytes.startswith(b"RIFF"):
        return wav_bytes

    try:
        with io.BytesIO(wav_bytes) as wav_io, wave.open(wav_io, "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            framerate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())

            if (
                channels == AUDIO_CHANNELS
                and sample_width == AUDIO_SAMPLE_WIDTH
                and framerate == AUDIO_SAMPLE_RATE_HZ
            ):
                return frames
    except Exception as exc:
        logger.debug("Wave header parse failed (%s); trying pydub.", exc)
        frames = b""

    if AudioSegment is not None:
        try:
            segment = AudioSegment.from_file(
                io.BytesIO(wav_bytes), format="wav"
            )
            segment = (
                segment.set_frame_rate(AUDIO_SAMPLE_RATE_HZ)
                .set_channels(AUDIO_CHANNELS)
                .set_sample_width(AUDIO_SAMPLE_WIDTH)
            )
            return segment.raw_data
        except Exception as exc:
            logger.warning("Failed to resample WAV via pydub: %s", exc)

    return frames or wav_bytes


class ShadowUserConversation(Conversation):
    """Simulated user for ShadowEvals that arbitrates between replaying past
    recorded audio turns and generating new responses via TTS.
    """

    class Output(pydantic.BaseModel):
        decision: ShadowDecisionType = ShadowDecisionType.USE_PAST_AUDIO
        selected_past_turn_index: int | None = None
        next_user_utterance: str = ""
        decision_justification: str = ""
        step_progresses: list[StepProgress] = []

    def __init__(
        self,
        genai_client: GeminiGenerate,
        genai_model: str,
        test_case: ShadowTestCase | dict[str, Any],
        past_turns: list[ShadowPastTurn],
        full_past_history: str = "",
        max_turns: int | None = None,
        initial_utterance: str | None = None,
    ) -> None:
        super().__init__()
        self.genai_client = genai_client
        self.genai_model = genai_model
        if isinstance(test_case, dict):
            self.test_case_model = ShadowTestCase(**test_case)
            self.test_case = test_case
        else:
            self.test_case_model = test_case
            self.test_case = test_case.model_dump()

        self.past_turns = [t.model_copy(deep=True) for t in past_turns]
        self.full_past_history = full_past_history or self._build_past_history()
        self.initial_utterance = (
            initial_utterance
            if initial_utterance is not None
            else self.test_case_model.initial_utterance
        )

        if max_turns is not None:
            self.max_turns = max_turns
        elif self.test_case_model.max_turns is not None:
            self.max_turns = self.test_case_model.max_turns
        else:
            default_limit = max(len(self.past_turns) * 2 + 4, 15)
            self.max_turns = min(default_limit, _MAX_TURNS)

        # Derive goal & response_guide if not explicitly provided
        self.goal = self.test_case_model.goal or self._infer_default_goal()
        self.response_guide = (
            self.test_case_model.response_guide
            or self._infer_default_response_guide()
        )

        # Setup step progress tracking
        self.steps_progress: list[StepProgress] = []
        if self.test_case_model.steps:
            for step in self.test_case_model.steps:
                self.steps_progress.append(
                    StepProgress(
                        step=step,
                        status=StepStatus.NOT_STARTED,
                        justification="",
                    )
                )
        else:
            exp_summary = "; ".join(
                str(e.get("expectation", "") if isinstance(e, dict) else e)
                for e in self.test_case_model.expectations
            )
            default_step = Step(
                goal=self.goal,
                success_criteria=exp_summary,
                response_guide=self.response_guide,
            )
            self.steps_progress.append(
                StepProgress(
                    step=default_step,
                    status=StepStatus.NOT_STARTED,
                    justification="",
                )
            )

        self.expectations = list(self.test_case_model.expectations)
        self.audio_expectations: list[dict[str, Any]] = []
        for exp in self.test_case_model.audio_expectations:
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
        self.naturalness_result: NaturalnessResult | None = None
        self.turn_logs: list[ShadowTurnLog] = []
        self.agent_audio_paths: dict[int, str] = {}

    def _build_past_history(self) -> str:
        """Builds a readable transcript from `self.past_turns`."""
        lines = []
        for pt in self.past_turns:
            if pt.preceding_agent_response:
                lines.append(
                    f"[Past Turn {pt.turn_index}] Agent: "
                    f"{pt.preceding_agent_response}"
                )
            lines.append(
                f"[Past Turn {pt.turn_index}] User: {pt.user_transcript}"
            )
        return "\n".join(lines)

    def _infer_default_goal(self) -> str:
        """Synthesizes a default goal from past user turns when omitted."""
        user_texts = [
            pt.user_transcript for pt in self.past_turns if pt.user_transcript
        ]
        if user_texts:
            preview = " -> ".join(user_texts[:3])
            return (
                "Replay the user's original conversation intent ("
                f"{preview}) and achieve the expected resolution."
            )
        return "Complete the user's request as defined in the expectations."

    def _infer_default_response_guide(self) -> str:
        """Synthesizes a default response guide when omitted."""
        return (
            "Prefer replaying the past user audio turns when they match the "
            "new agent's questions. If the new agent deviates or asks a new "
            "clarifying question, generate a concise response via TTS using "
            "the facts from the Full Past Conversation History, then resume "
            "using remaining past audio turns."
        )

    def _check_conversation_status(self) -> bool:
        """Returns True if the shadow conversation should continue."""
        if self.current_turn >= self.max_turns:
            return False
        return not all(
            item.status == StepStatus.COMPLETED for item in self.steps_progress
        )

    def _prepare_shadow_llm_prompt(self) -> str:
        """Builds the prompt for the Shadow LLM User Simulator."""
        steps_list = [prog.step.model_dump() for prog in self.steps_progress]
        json_steps = json.dumps(steps_list, indent=2)
        progress_list = [prog.model_dump() for prog in self.steps_progress]
        json_progress = json.dumps(progress_list, indent=2)

        available_turns = [
            {
                "turn_index": pt.turn_index,
                "user_transcript": pt.user_transcript,
                "preceding_agent_response": pt.preceding_agent_response,
                "has_audio": bool(pt.has_audio and pt.audio_bytes is not None),
                "used": pt.used,
            }
            for pt in self.past_turns
        ]
        json_available_turns = json.dumps(available_turns, indent=2)

        prompt = llm_user_prompts.SHADOW_LLM_USER_PROMPT
        prompt = prompt.replace("{user_goal}", self.goal)
        prompt = prompt.replace("{response_guide}", self.response_guide)
        prompt = prompt.replace("{input_user_config}", json_steps)
        prompt = prompt.replace("{current_step_progress}", json_progress)
        prompt = prompt.replace(
            "{full_past_conversation_history}",
            self.full_past_history or "(No past history)",
        )
        prompt = prompt.replace(
            "{available_past_audio_turns}", json_available_turns
        )
        prompt = prompt.replace(
            "{current_conversation_history}",
            self.get_transcript() or "(Start of conversation)",
        )
        return prompt

    def _find_past_turn(
        self, selected_index: int | None
    ) -> ShadowPastTurn | None:
        """Finds the requested past turn by `turn_index`, or falls back to the
        next sequential unused past turn.
        """
        if selected_index is not None:
            for pt in self.past_turns:
                if pt.turn_index == selected_index and not pt.used:
                    return pt
            for pt in self.past_turns:
                if pt.turn_index == selected_index:
                    return pt

        for pt in self.past_turns:
            if not pt.used:
                return pt
        return None

    def next_user_turn(
        self, last_agent_response: str = ""
    ) -> tuple[str, bytes | None, dict[str, Any], ShadowTurnLog | None]:
        """Determines the next user utterance and whether to stream past raw
        audio bytes or generate new TTS audio.

        Returns:
            Tuple of:
              - `user_utterance`: Text transcript or event/dtmf string.
              - `audio_bytes`: Raw 16kHz PCM bytes if replaying a past GCS
                recording, or `None` if TTS / event should be used.
              - `variables_to_inject`: Session parameters dict.
              - `turn_log`: `ShadowTurnLog` entry describing the decision.
        """
        if last_agent_response:
            self._add_agent_response(last_agent_response)
            if self.turn_logs:
                self.turn_logs[-1].agent_response = last_agent_response

        if not self._check_conversation_status():
            self._add_user_utterance("")
            self.current_turn += 1
            return "", None, {}, None

        # Turn 0: Initial trigger (e.g. event: welcome) or first past turn
        if self.current_turn == 0:
            session_params = dict(self.test_case_model.session_parameters)
            if self.initial_utterance:
                utterance = self.initial_utterance
                self._add_user_utterance(utterance)
                turn_log = ShadowTurnLog(
                    sim_turn=0,
                    decision="event",
                    selected_past_turn_index=None,
                    user_utterance=utterance,
                    audio_source="event",
                    decision_justification="Initial session event trigger.",
                )
                self.turn_logs.append(turn_log)
                self.current_turn += 1
                return utterance, None, session_params, turn_log

            first_pt = self._find_past_turn(None)
            if first_pt is not None:
                first_pt.used = True
                utterance = first_pt.user_transcript
                self._add_user_utterance(utterance)
                has_raw = bool(first_pt.has_audio and first_pt.audio_bytes)
                decision_str = (
                    ShadowDecisionType.USE_PAST_AUDIO.value
                    if has_raw
                    else ShadowDecisionType.GENERATE_TTS.value
                )
                turn_log = ShadowTurnLog(
                    sim_turn=0,
                    decision=decision_str,
                    selected_past_turn_index=first_pt.turn_index,
                    user_utterance=utterance,
                    audio_source=(
                        (
                            first_pt.audio_uri
                            or first_pt.audio_path
                            or "past_audio"
                        )
                        if has_raw
                        else "TTS (fallback: no past audio)"
                    ),
                    decision_justification="Initial turn from past recording.",
                )
                self.turn_logs.append(turn_log)
                self.current_turn += 1
                return (
                    utterance,
                    first_pt.audio_bytes if has_raw else None,
                    session_params,
                    turn_log,
                )

        prompt = self._prepare_shadow_llm_prompt()
        output: ShadowUserConversation.Output = self.genai_client.generate(
            prompt=prompt,
            model_name=self.genai_model,
            response_mime_type="application/json",
            response_schema=ShadowUserConversation.Output,
        )

        if not output:
            self._add_user_utterance("")
            self.current_turn += 1
            return "", None, {}, None

        if output.step_progresses:
            self.steps_progress = output.step_progresses

        if (
            output.decision == ShadowDecisionType.END_CONVERSATION
            or not self._check_conversation_status()
        ):
            for prog in self.steps_progress:
                if prog.status == StepStatus.IN_PROGRESS:
                    prog.status = StepStatus.COMPLETED
                    if not prog.justification:
                        prog.justification = (
                            output.decision_justification
                            or "Shadow conversation completed."
                        )
            self._add_user_utterance("")
            self.current_turn += 1
            return "", None, {}, None

        if output.decision == ShadowDecisionType.USE_PAST_AUDIO:
            matched_pt = self._find_past_turn(output.selected_past_turn_index)
            if matched_pt is not None:
                matched_pt.used = True
                utterance = (
                    matched_pt.user_transcript or output.next_user_utterance
                )
                if matched_pt.has_audio and matched_pt.audio_bytes is not None:
                    self._add_user_utterance(utterance)
                    turn_log = ShadowTurnLog(
                        sim_turn=self.current_turn,
                        decision=ShadowDecisionType.USE_PAST_AUDIO.value,
                        selected_past_turn_index=matched_pt.turn_index,
                        user_utterance=utterance,
                        audio_source=(
                            matched_pt.audio_uri
                            or matched_pt.audio_path
                            or f"user-turn-{matched_pt.turn_index}.wav"
                        ),
                        decision_justification=output.decision_justification,
                    )
                    self.turn_logs.append(turn_log)
                    self.current_turn += 1
                    return utterance, matched_pt.audio_bytes, {}, turn_log

                # Fallback to TTS if past turn had no audio file in GCS
                utterance = utterance or output.next_user_utterance
                self._add_user_utterance(utterance)
                turn_log = ShadowTurnLog(
                    sim_turn=self.current_turn,
                    decision=ShadowDecisionType.GENERATE_TTS.value,
                    selected_past_turn_index=matched_pt.turn_index,
                    user_utterance=utterance,
                    audio_source="TTS (past turn had no audio recording)",
                    decision_justification=(
                        f"{output.decision_justification} "
                        "(Fallback to TTS: past audio bytes unavailable)"
                    ),
                )
                self.turn_logs.append(turn_log)
                self.current_turn += 1
                return utterance, None, {}, turn_log

        # Deviation mode: GENERATE_TTS
        utterance = output.next_user_utterance
        self._add_user_utterance(utterance)
        turn_log = ShadowTurnLog(
            sim_turn=self.current_turn,
            decision=ShadowDecisionType.GENERATE_TTS.value,
            selected_past_turn_index=output.selected_past_turn_index,
            user_utterance=utterance,
            audio_source="TTS",
            decision_justification=output.decision_justification,
        )
        self.turn_logs.append(turn_log)
        self.current_turn += 1
        return utterance, None, {}, turn_log

    def next_user_utterance(
        self, last_agent_response: str = ""
    ) -> tuple[str, dict[str, Any]]:
        """Compatibility wrapper returning `(utterance, variables)`."""
        utterance, _, variables, _ = self.next_user_turn(last_agent_response)
        return utterance, variables

    def generate_report(self) -> ShadowReport:
        """Generates a `ShadowReport` summarizing turn decisions, goals, and
        expectations.
        """
        turn_records = [
            {
                "sim_turn": t.sim_turn,
                "decision": t.decision,
                "past_turn_idx": t.selected_past_turn_index,
                "user_utterance": t.user_utterance,
                "audio_source": t.audio_source,
                "justification": t.decision_justification,
            }
            for t in self.turn_logs
        ]
        turns_df = pd.DataFrame(turn_records)

        goal_records = [
            {
                "goal": prog.step.goal,
                "success_criteria": prog.step.success_criteria,
                "status": prog.status.value,
                "justification": prog.justification,
            }
            for prog in self.steps_progress
        ]
        goals_df = pd.DataFrame(goal_records)

        expectations_df = None
        if self.expectation_results:
            exp_records = [
                {
                    "expectation": res.expectation,
                    "status": res.status.value,
                    "justification": res.justification,
                }
                for res in self.expectation_results
            ]
            expectations_df = pd.DataFrame(exp_records)

        naturalness_df = None
        naturalness_headline = ""
        if self.naturalness_result:
            result = self.naturalness_result
            nat_records = []
            for turn in result.turns:
                record: dict[str, Any] = {
                    "turn": turn.turn_index,
                    "label": turn.label.value,
                    "score": turn.score,
                }
                latency_ms = result.latency_ms_by_turn.get(turn.turn_index)
                if latency_ms is not None:
                    record["latency_s"] = round(latency_ms / 1000.0, 2)
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

        return ShadowReport(
            turns_df=turns_df,
            goals_df=goals_df,
            expectations_df=expectations_df,
            naturalness_df=naturalness_df,
            naturalness_headline=naturalness_headline,
        )


class ShadowEvals(Apps):
    """Replays historical conversations (with past GCS audio + hybrid TTS
    simUser) on a target CXAS Agent and evaluates natural-language expectations.
    """

    max_retries: int = 3
    retry_delay_base: int = 2

    def __init__(
        self,
        app_name: str,
        rate_limiter: RateLimiter | None = None,
        expectations_only: bool = True,
        deployment_id: str | None = None,
        vertex_location: str = "global",
        naturalness: bool | dict[str, Any] | None = None,
        gcs_bucket: str | None = None,
        **kwargs: typing.Any,
    ) -> None:
        self.expectations_only = expectations_only
        self.vertex_location = vertex_location
        self.naturalness = naturalness
        self.default_gcs_bucket = gcs_bucket
        project_id = app_name.split("/")[1]
        location = app_name.split("/")[3]
        super().__init__(project_id=project_id, location=location, **kwargs)
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

    def _resolve_source_app_name(self, test_case: ShadowTestCase) -> str:
        """Resolves the full resource name of the source app where the past
        conversation was recorded.
        """
        proj = test_case.project_id or self.project_id
        loc = test_case.location or self.location
        app_id = test_case.app_id or self.app_name.split("/")[-1]
        if app_id.startswith("projects/"):
            return app_id
        return f"projects/{proj}/locations/{loc}/apps/{app_id}"

    def fetch_past_conversation_data(
        self,
        test_case: ShadowTestCase | dict[str, Any],
        session_dir: str | None = None,
    ) -> tuple[list[ShadowPastTurn], str, dict[str, Any]]:
        """Downloads past conversation transcript and per-turn user audio from
        GCS for a `ShadowTestCase`.

        Args:
            test_case: `ShadowTestCase` or dict specifying `conversation_id`
                (and optional `project_id`, `location`, `app_id`, `gcs_bucket`).
            session_dir: Optional local directory to store downloaded WAV files.

        Returns:
            Tuple of `(past_turns, full_past_history_text, normalized_trace)`.
        """
        tc = (
            ShadowTestCase(**test_case)
            if isinstance(test_case, dict)
            else test_case
        )
        source_app_name = self._resolve_source_app_name(tc)
        traces_client = Traces(app_name=source_app_name, creds=self.creds)
        bucket_override = tc.gcs_bucket or self.default_gcs_bucket
        if bucket_override:
            traces_client.trace_config.audio.bucket_override = bucket_override

        normalized = traces_client.get_normalized(tc.conversation_id)
        start_time = normalized.get("start_time")

        try:
            user_audio_uris = traces_client.get_user_audio_uris(
                tc.conversation_id, start_time=start_time
            )
        except Exception as exc:
            logger.warning(
                "Could not list user audio URIs for conversation %s (%s).",
                tc.conversation_id,
                exc,
            )
            user_audio_uris = {}

        gcs_client = GCSUtils(creds=self.creds) if user_audio_uris else None

        # Extract turns and full history from normalized conversation
        raw_conv = normalized.get("raw") or {}
        raw_turns = raw_conv.get("turns") or []

        past_turns: list[ShadowPastTurn] = []
        history_lines: list[str] = []
        last_agent_texts: list[str] = []
        user_turn_counter = 0

        if raw_turns:
            for turn_idx, p_turn in enumerate(raw_turns, start=1):
                turn_user_texts: list[str] = []
                turn_agent_texts: list[str] = []

                for msg in p_turn.get("messages", []) or []:
                    role = (msg.get("role") or "").strip()
                    chunks = msg.get("chunks") or []
                    for chunk in chunks:
                        text_val = (
                            chunk.get("text") or chunk.get("transcript") or ""
                        ).strip()
                        if text_val:
                            if role.lower() == "user":
                                turn_user_texts.append(text_val)
                                history_lines.append(
                                    f"[Turn {turn_idx}] User: {text_val}"
                                )
                            else:
                                turn_agent_texts.append(text_val)
                                history_lines.append(
                                    f"[Turn {turn_idx}] Agent ({role}): "
                                    f"{text_val}"
                                )
                        if "tool_call" in chunk:
                            tc_chunk = chunk["tool_call"]
                            t_name = (
                                tc_chunk.get("display_name")
                                or tc_chunk.get("name")
                                or tc_chunk.get("tool")
                                or ""
                            )
                            t_args = tc_chunk.get("args", {})
                            history_lines.append(
                                f"[Turn {turn_idx}] Tool Call: {t_name} "
                                f"args={t_args}"
                            )
                        if "tool_response" in chunk:
                            tr_chunk = chunk["tool_response"]
                            t_name = (
                                tr_chunk.get("display_name")
                                or tr_chunk.get("name")
                                or tr_chunk.get("tool")
                                or ""
                            )
                            t_resp = tr_chunk.get("response", {})
                            history_lines.append(
                                f"[Turn {turn_idx}] Tool Response: {t_name} "
                                f"response={t_resp}"
                            )

                if turn_user_texts:
                    user_turn_counter += 1
                    # Match 1-based user turn or 1-based conversation turn
                    audio_uri = user_audio_uris.get(
                        user_turn_counter
                    ) or user_audio_uris.get(turn_idx)
                    audio_path = None
                    pcm_bytes = None

                    if audio_uri and gcs_client is not None:
                        try:
                            raw_wav = gcs_client.download_blob(audio_uri)
                            pcm_bytes = extract_pcm_bytes_from_wav(raw_wav)
                            if session_dir:
                                os.makedirs(session_dir, exist_ok=True)
                                audio_path = os.path.join(
                                    session_dir,
                                    f"past_user_turn_{user_turn_counter}.wav",
                                )
                                with open(audio_path, "wb") as f:
                                    f.write(raw_wav)
                        except Exception as exc:
                            logger.warning(
                                "Failed to download user audio %s: %s",
                                audio_uri,
                                exc,
                            )

                    past_turns.append(
                        ShadowPastTurn(
                            turn_index=user_turn_counter,
                            user_transcript=" ".join(turn_user_texts),
                            preceding_agent_response=" ".join(last_agent_texts),
                            audio_uri=audio_uri,
                            audio_path=audio_path,
                            has_audio=bool(pcm_bytes),
                            used=False,
                            audio_bytes=pcm_bytes,
                        )
                    )
                    last_agent_texts = []

                if turn_agent_texts:
                    last_agent_texts.extend(turn_agent_texts)
        else:
            # Fallback to flat `entries` if `raw.turns` is absent
            for entry in normalized.get("entries", []) or []:
                kind = entry.get("kind")
                turn_num = int(entry.get("turn", 0)) + 1
                if kind == "user":
                    user_turn_counter += 1
                    u_text = (entry.get("text") or "").strip()
                    history_lines.append(f"[Turn {turn_num}] User: {u_text}")
                    audio_uri = user_audio_uris.get(
                        user_turn_counter
                    ) or user_audio_uris.get(turn_num)
                    audio_path = None
                    pcm_bytes = None
                    if audio_uri and gcs_client is not None:
                        try:
                            raw_wav = gcs_client.download_blob(audio_uri)
                            pcm_bytes = extract_pcm_bytes_from_wav(raw_wav)
                        except Exception as exc:
                            logger.warning(
                                "Failed to download user audio %s: %s",
                                audio_uri,
                                exc,
                            )
                    past_turns.append(
                        ShadowPastTurn(
                            turn_index=user_turn_counter,
                            user_transcript=u_text,
                            preceding_agent_response=" ".join(last_agent_texts),
                            audio_uri=audio_uri,
                            audio_path=audio_path,
                            has_audio=bool(pcm_bytes),
                            used=False,
                            audio_bytes=pcm_bytes,
                        )
                    )
                    last_agent_texts = []
                elif kind == "agent":
                    a_text = (entry.get("text") or "").strip()
                    last_agent_texts.append(a_text)
                    history_lines.append(f"[Turn {turn_num}] Agent: {a_text}")
                elif kind == "tool_call":
                    history_lines.append(
                        f"[Turn {turn_num}] Tool Call: {entry.get('tool')} "
                        f"args={entry.get('args')}"
                    )

        return past_turns, "\n".join(history_lines), normalized

    def _parse_agent_response(
        self, response: Any
    ) -> tuple[str, list[str], bool, list[Any]]:
        """Parses the agent response to extract text, trace chunks, session end,
        and tool calls.
        """
        parsed = ParsedSessionResponse(response, tools_map=self.tools_map)
        return (
            parsed.consolidated_agent_text,
            parsed.detailed_trace,
            parsed.session_ended,
            parsed.tool_calls,
        )

    def _send_shadow_request_with_retry(
        self,
        session_id: str,
        user_utterance: str,
        audio_bytes: bytes | None,
        variables: dict[str, Any],
        modality: str,
        console_logging: bool,
        turn_num: int | None = None,
        capture_agent_audio: bool = False,
        background_noise_file: str | None = None,
        burst_noise_files: list[str] | None = None,
        use_tool_fakes: bool = False,
        voice_config: dict[str, Any] | None = None,
    ) -> Any:
        """Sends a shadow turn request to the CES Agent over Bidi or Text with
        retry backoff.

        When `audio_bytes` is provided and `modality == "audio"`, streams the
        raw past audio bytes directly on the Bidi session without TTS.
        When `audio_bytes` is `None`, synthesizes `user_utterance` via TTS (or
        sends an event/dtmf).
        """
        run_kwargs: dict[str, Any] = {
            "session_id": session_id,
            "variables": variables,
            "modality": modality,
            "turn_num": turn_num,
            "capture_agent_audio": capture_agent_audio,
            "background_noise_file": background_noise_file,
            "burst_noise_files": burst_noise_files,
            "use_tool_fakes": use_tool_fakes,
        }
        if voice_config is not None:
            run_kwargs["voice_config"] = voice_config

        response = None
        for attempt in range(self.max_retries):
            try:
                if audio_bytes is not None and modality == "audio":
                    response = self.sessions_client.run(
                        audio=audio_bytes,
                        **run_kwargs,
                    )
                elif user_utterance.startswith("event:"):
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
            except Exception as exc:
                if attempt == self.max_retries - 1:
                    raise exc
                if console_logging:
                    print(
                        f"Warning: CXAS Agent shadow request failed ({exc}). "
                        f"Retrying in {self.retry_delay_base**attempt}s..."
                    )
                time.sleep(self.retry_delay_base**attempt)
        return response

    def _evaluate_expectations(
        self,
        shadow_conv: ShadowUserConversation,
        detailed_trace: list[str],
        model: str,
        console_logging: bool,
        capture_agent_audio: bool = False,
    ) -> None:
        """Evaluates natural-language expectations against the shadow trace."""
        audio_paths = (
            getattr(shadow_conv, "agent_audio_paths", None)
            if capture_agent_audio
            else None
        )
        all_expectations = list(shadow_conv.expectations)
        if shadow_conv.audio_expectations:
            all_expectations.extend(shadow_conv.audio_expectations)

        if all_expectations:
            if console_logging:
                print("\nEvaluating Shadow Expectations...")
            shadow_conv.expectation_results = evaluate_expectations(
                gemini_client=self.genai_client,
                model_name=model,
                trace=detailed_trace,
                expectations=all_expectations,
                audio_paths=audio_paths,
            )

    def _evaluate_naturalness(
        self,
        shadow_conv: ShadowUserConversation,
        detailed_trace: list[str],
        model: str,
        console_logging: bool,
        config: NaturalnessConfig | None,
        modality: str = "audio",
    ) -> None:
        """Evaluates optional Naturalness metric if configured."""
        if config is None:
            return
        if console_logging:
            print("\nEvaluating Naturalness...")
        if modality == "audio" and not config.use_audio:
            config = config.model_copy(update={"use_audio": True})

        speech_index_by_turn = {
            t.turn_index: t.speech_index
            for t in extract_agent_turns(detailed_trace)
        }
        local_audio = dict(getattr(shadow_conv, "agent_audio_paths", {}) or {})
        audio_paths = (
            {
                speech_index_by_turn[turn]: path
                for turn, path in local_audio.items()
                if turn in speech_index_by_turn
            }
            if config.use_audio and local_audio
            else None
        )
        shadow_conv.naturalness_result = evaluate_naturalness(
            gemini_client=self.genai_client,
            model_name=model,
            trace=detailed_trace,
            config=config,
            audio_paths=audio_paths,
            latency_ms_by_turn={},
        )

    @cleanup_session_dir
    def run_shadow_conversation(
        self,
        test_case: ShadowTestCase | dict[str, Any],
        sim_user_model: str | None = _DEFAULT_GEMINI_MODEL,
        eval_model: str | None = _DEFAULT_GEMINI_MODEL,
        session_id: str | None = None,
        console_logging: bool = True,
        modality: str = "audio",
        capture_agent_audio: bool = False,
        background_noise_file: str | None = None,
        burst_noise_files: list[str] | None = None,
        use_tool_fakes: bool = False,
        voice_config: dict[str, Any] | None = None,
        initial_utterance: str | None = None,
        skip_playback_wait: bool = False,
        single_bidi_stream: bool = False,
        max_turns: int | None = None,
        naturalness: bool | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ShadowUserConversation:
        """Replays a past conversation on the target agent over Bidi audio (or
        text) using `ShadowUserConversation` to arbitrate between past audio
        files and TTS.
        """
        sim_user_model = sim_user_model or _DEFAULT_GEMINI_MODEL
        eval_model = eval_model or _DEFAULT_GEMINI_MODEL
        if session_id is None:
            session_id = str(uuid.uuid4())

        tc_model = (
            ShadowTestCase(**test_case)
            if isinstance(test_case, dict)
            else test_case
        )
        tc_dict = tc_model.model_dump()
        naturalness_config = parse_naturalness_config(
            tc_dict,
            naturalness if naturalness is not None else self.naturalness,
        )
        voice_config = voice_config or tc_model.voice_config

        session_dir = f"/tmp/scrapi_evals/{session_id}"
        past_turns, full_past_history, _ = self.fetch_past_conversation_data(
            tc_model, session_dir=session_dir
        )

        shadow_conv = ShadowUserConversation(
            genai_client=self.genai_client,
            genai_model=sim_user_model,
            test_case=tc_model,
            past_turns=past_turns,
            full_past_history=full_past_history,
            max_turns=max_turns,
            initial_utterance=initial_utterance,
        )

        current_sim_turn = 0
        interactive_session = None
        if modality == "audio" and single_bidi_stream:
            client = self.sessions_client
            interactive_session = client.create_interactive_session(
                session_id=session_id,
                capture_agent_audio=capture_agent_audio,
                background_noise_file=background_noise_file,
                use_tool_fakes=use_tool_fakes,
                skip_playback_wait=skip_playback_wait,
                voice_config=voice_config,
            )
            interactive_session.start()

        try:
            if console_logging:
                print(
                    f"Starting shadow conversation replay for "
                    f"conversation_id={tc_model.conversation_id} "
                    f"(session_id={session_id})"
                )

            user_utterance, audio_bytes, variables, turn_log = (
                shadow_conv.next_user_turn()
            )
            accumulated_variables: dict[str, Any] = {}
            if variables:
                accumulated_variables.update(variables)

            detailed_trace: list[str] = []
            if user_utterance:
                past_idx = (
                    turn_log.selected_past_turn_index if turn_log else None
                )
                mode_tag = (
                    f"[Past Audio Turn #{past_idx}]"
                    if turn_log
                    and turn_log.decision
                    == ShadowDecisionType.USE_PAST_AUDIO.value
                    else (
                        "[Generated TTS]"
                        if turn_log
                        and turn_log.decision
                        == ShadowDecisionType.GENERATE_TTS.value
                        else "[Event]"
                    )
                )
                detailed_trace.append(f"User {mode_tag}: {user_utterance}")

            while user_utterance:
                if modality == "audio" and interactive_session:
                    response = interactive_session.send_turn(
                        user_utterance,
                        accumulated_variables,
                        audio_bytes=audio_bytes,
                    )
                    if isinstance(response, dict) and response.get(
                        "session_ended"
                    ):
                        if response.get("connection_error"):
                            raise BidiSessionError(
                                f"Interactive session WebSocket error: "
                                f"{response['connection_error']}"
                            )
                        break
                else:
                    response = self._send_shadow_request_with_retry(
                        session_id=session_id,
                        user_utterance=user_utterance,
                        audio_bytes=audio_bytes,
                        variables=accumulated_variables,
                        modality=modality,
                        console_logging=console_logging,
                        turn_num=current_sim_turn,
                        capture_agent_audio=capture_agent_audio,
                        background_noise_file=background_noise_file,
                        burst_noise_files=burst_noise_files,
                        use_tool_fakes=use_tool_fakes,
                        voice_config=voice_config,
                    )
                if not response:
                    break

                if response and getattr(response, "agent_audio_paths", None):
                    audio_path = response.agent_audio_paths.get(0)
                    if audio_path:
                        shadow_conv.agent_audio_paths[current_sim_turn] = (
                            audio_path
                        )

                if console_logging:
                    self.sessions_client.parse_result(response)

                agent_text, trace_chunks, session_ended, tool_calls = (
                    self._parse_agent_response(response)
                )
                detailed_trace.append("\n".join(trace_chunks))

                if session_ended:
                    if agent_text:
                        shadow_conv._add_agent_response(agent_text)
                        if shadow_conv.turn_logs:
                            shadow_conv.turn_logs[
                                -1
                            ].agent_response = agent_text
                    shadow_conv._add_agent_tool_calls(tool_calls)
                    for prog in shadow_conv.steps_progress:
                        if prog.status != StepStatus.COMPLETED:
                            prog.status = StepStatus.COMPLETED
                            prog.justification = (
                                "Session ended by agent; marking step complete."
                            )
                    break

                shadow_conv._add_agent_tool_calls(tool_calls)
                user_utterance, audio_bytes, variables, turn_log = (
                    shadow_conv.next_user_turn(agent_text)
                )
                if variables:
                    accumulated_variables.update(variables)
                if user_utterance:
                    past_idx = (
                        turn_log.selected_past_turn_index if turn_log else None
                    )
                    mode_tag = (
                        f"[Past Audio Turn #{past_idx}]"
                        if turn_log
                        and turn_log.decision
                        == ShadowDecisionType.USE_PAST_AUDIO.value
                        else "[Generated TTS]"
                    )
                    detailed_trace.append(f"User {mode_tag}: {user_utterance}")

                current_sim_turn += 1

            self._evaluate_expectations(
                shadow_conv,
                detailed_trace,
                eval_model,
                console_logging,
                capture_agent_audio=capture_agent_audio,
            )
            self._evaluate_naturalness(
                shadow_conv,
                detailed_trace,
                eval_model,
                console_logging,
                naturalness_config,
                modality=modality,
            )
            shadow_conv._session_id = session_id
            shadow_conv.session_id = session_id
            shadow_conv._detailed_trace = detailed_trace
            shadow_conv.detailed_trace = detailed_trace
            return shadow_conv
        finally:
            if interactive_session:
                interactive_session.close()

    def _run_single_shadow_job(
        self,
        tc: ShadowTestCase,
        run_idx: int,
        runs: int,
        sim_user_model: str,
        eval_model: str,
        modality: str,
        verbose: bool,
        parallel: int,
        capture_agent_audio: bool = False,
        background_noise_file: str | None = None,
        burst_noise_files: list[str] | None = None,
        use_tool_fakes: bool = False,
        skip_playback_wait: bool = False,
        single_bidi_stream: bool = False,
    ) -> dict[str, Any]:
        """Executes a single shadow eval run and packages the results."""
        name = tc.name
        label = f"{name} (run {run_idx + 1}/{runs})"
        session_id = str(uuid.uuid4())
        try:
            start_ts = time.time()
            conv = self.run_shadow_conversation(
                test_case=tc,
                sim_user_model=sim_user_model,
                eval_model=eval_model,
                session_id=session_id,
                console_logging=verbose and parallel <= 1,
                modality=modality,
                capture_agent_audio=capture_agent_audio,
                background_noise_file=background_noise_file,
                burst_noise_files=burst_noise_files,
                use_tool_fakes=use_tool_fakes,
                skip_playback_wait=skip_playback_wait,
                single_bidi_stream=single_bidi_stream,
            )
            duration_s = round(time.time() - start_ts, 1)

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

            # Expectations are required on every ShadowTestCase and drive pass
            passed = expectations_met == total_exp if total_exp > 0 else False
            if not self.expectations_only and total_goals > 0:
                passed = passed and (goals_completed == total_goals)

            if (
                conv.naturalness_result
                and conv.naturalness_result.passed is not None
            ):
                passed = passed and conv.naturalness_result.passed

            past_audio_used = sum(
                1
                for t in conv.turn_logs
                if t.decision == ShadowDecisionType.USE_PAST_AUDIO.value
            )
            tts_generated = sum(
                1
                for t in conv.turn_logs
                if t.decision == ShadowDecisionType.GENERATE_TTS.value
            )

            status = "PASS" if passed else "FAIL"
            if parallel > 1 or not verbose:
                print(
                    f"  {status}  {label} | expectations: "
                    f"{expectations_met}/{total_exp} | "
                    f"past_audio: {past_audio_used} | tts: {tts_generated} | "
                    f"turns: {conv.current_turn} | {duration_s}s"
                )

            result: dict[str, Any] = {
                "name": name,
                "conversation_id": tc.conversation_id,
                "run": run_idx + 1,
                "passed": passed,
                "goals": f"{goals_completed}/{total_goals}",
                "expectations": f"{expectations_met}/{total_exp}",
                "past_audio_turns_used": past_audio_used,
                "tts_turns_generated": tts_generated,
                "turns": conv.current_turn,
                "duration_s": duration_s,
                "session_id": session_id,
                "session_parameters": tc.session_parameters,
                "transcript": conv.get_transcript(),
                "detailed_trace": getattr(conv, "detailed_trace", []),
                "turn_decisions": [t.model_dump() for t in conv.turn_logs],
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
            if conv.naturalness_result:
                result["naturalness"] = (
                    f"{conv.naturalness_result.overall_score}/5"
                )
                result["naturalness_label"] = (
                    conv.naturalness_result.overall_label.value
                )
                result["naturalness_details"] = (
                    conv.naturalness_result.model_dump()
                )
            return result
        except Exception as exc:
            print(f"  ERROR  {label}: {exc}")
            return {
                "name": name,
                "conversation_id": tc.conversation_id,
                "run": run_idx + 1,
                "passed": False,
                "error": str(exc),
            }

    def run_shadow_evals(
        self,
        test_cases: list[ShadowTestCase | dict[str, Any]],
        runs: int = 1,
        parallel: int = 1,
        sim_user_model: str | None = _DEFAULT_GEMINI_MODEL,
        eval_model: str | None = _DEFAULT_GEMINI_MODEL,
        modality: str = "audio",
        verbose: bool = False,
        capture_agent_audio: bool = False,
        background_noise_file: str | None = None,
        burst_noise_files: list[str] | None = None,
        use_tool_fakes: bool = False,
        expectations_only: bool | None = None,
        skip_playback_wait: bool = False,
        single_bidi_stream: bool = False,
        progress_callback: Callable[[int, int], None] | None = None,
        naturalness: bool | dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Runs a batch of shadow evaluation test cases across `runs` and
        `parallel` workers.
        """
        if expectations_only is not None:
            self.expectations_only = expectations_only
        if naturalness is not None:
            self.naturalness = naturalness

        sim_user_model = sim_user_model or _DEFAULT_GEMINI_MODEL
        eval_model = eval_model or _DEFAULT_GEMINI_MODEL

        validated_cases = [
            ShadowTestCase(**tc) if isinstance(tc, dict) else tc
            for tc in test_cases
        ]
        jobs = [
            (tc, run_idx) for tc in validated_cases for run_idx in range(runs)
        ]

        results: list[dict[str, Any]] = []
        with Progress() as progress:
            task_id = progress.add_task("Running Shadow Evals", total=len(jobs))
            if parallel <= 1:
                for tc, run_idx in jobs:
                    results.append(
                        self._run_single_shadow_job(
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
                            self._run_single_shadow_job,
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
                        ): (tc.name, run_idx)
                        for tc, run_idx in jobs
                    }
                    for future in as_completed(futures):
                        results.append(future.result())
                        progress.update(task_id, advance=1)
                        if progress_callback:
                            progress_callback(len(results), len(jobs))

        return results

    @staticmethod
    def load_shadow_test_cases_from_yaml(
        yaml_data: str,
    ) -> list[ShadowTestCase]:
        """Loads and validates `ShadowTestCase` items from a YAML string."""
        raw_data = yaml.safe_load(yaml_data)
        if not raw_data:
            return []

        global_config: dict[str, Any] = {}
        if isinstance(raw_data, list):
            raw_evals = raw_data
        elif isinstance(raw_data, dict):
            global_config = raw_data.get("config", {}) or {}
            raw_evals = (
                raw_data.get("shadow_evals")
                or raw_data.get("evals")
                or raw_data.get("conversations")
                or []
            )
        else:
            return []

        cases: list[ShadowTestCase] = []
        inherited_keys = (
            "project_id",
            "location",
            "app_id",
            "gcs_bucket",
            "max_turns",
            "voice_config",
        )
        for item in raw_evals:
            if not isinstance(item, dict):
                continue
            merged = dict(item)
            for key in inherited_keys:
                if merged.get(key) is None and key in global_config:
                    merged[key] = global_config[key]
            if "session_parameters" in global_config:
                merged_params = dict(global_config["session_parameters"])
                merged_params.update(merged.get("session_parameters") or {})
                merged["session_parameters"] = merged_params
            cases.append(ShadowTestCase(**merged))
        return cases

    @classmethod
    def load_shadow_test_cases_from_file(
        cls, file_path: str
    ) -> list[ShadowTestCase]:
        """Loads `ShadowTestCase` items from a YAML file path."""
        with open(file_path, encoding="utf-8") as f:
            return cls.load_shadow_test_cases_from_yaml(f.read())

    @classmethod
    def load_shadow_tests_from_dir(
        cls, directory_path: str = "evals/shadows"
    ) -> list[ShadowTestCase]:
        """Recursively loads all YAML shadow test cases from a directory."""
        all_tests: list[ShadowTestCase] = []
        if not os.path.exists(directory_path):
            return all_tests

        for root, _, files in os.walk(directory_path):
            for file in sorted(files):
                if file.endswith((".yaml", ".yml")):
                    file_path = os.path.join(root, file)
                    all_tests.extend(
                        cls.load_shadow_test_cases_from_file(file_path)
                    )
        return all_tests
