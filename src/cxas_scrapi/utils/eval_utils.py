# pytype: disable=invalid-function-definition

# Copyright 2026 Google LLC
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


"""Utility functions for processing and exporting CXAS Evaluation Results."""

import contextlib
import enum
import glob
import json
import logging
import os
import time
import typing
import uuid
from typing import Annotated, Any

import pandas as pd
import pydantic
import yaml
from google import genai
from pydantic import BaseModel, BeforeValidator

from cxas_scrapi.core.agents import Agents
from cxas_scrapi.core.common import Common
from cxas_scrapi.core.evaluations import Evaluations
from cxas_scrapi.core.tools import Tools
from cxas_scrapi.core.variables import Variables
from cxas_scrapi.prompts import llm_user_prompts
from cxas_scrapi.utils.latency_parser import LatencyParser

# Filename constants for evaluation results
SIM_RESULTS_FILENAME = "sim_results.json"
SHADOW_RESULTS_FILENAME = "shadow_results.json"
TOOL_RESULTS_FILENAME = "tool_results.csv"
CALLBACK_RESULTS_FILENAME = "callback_results.csv"
COMBINED_REPORT_FILENAME = "combined_report.html"
COMBINED_REPORT_JSON_FILENAME = "combined_report.json"
TIMESTAMP_PATTERN = r"\d{8}_\d{6}"

# Whitelist constants for Milestone YAMLs aligned with
# CES MilestoneYamlTranslator.kt
MILESTONE_ALLOWED_ROOT_KEYS = {
    "common_session_parameters",
    "common_expectations",
    "evals",
    "initial_event",
    "metadata",
}
MILESTONE_ALLOWED_EVAL_KEYS = {
    "name",
    "tags",
    "session_parameters",
    "steps",
    "expectations",
    "max_turns",
    "initial_event",
    "metadata",
}
MILESTONE_ALLOWED_STEP_KEYS = {
    "goal",
    "success_criteria",
    "response_guide",
    "max_turns",
    "static_utterance",
    "event",
    "inject_variables",
    "metadata",
}

logger = logging.getLogger(__name__)


def _parse_variables_input(v: Any) -> dict[str, Any]:
    """Normalises the several accepted shapes of an eval variable block.

    Accepted forms:

    * ``{"auth_status": "pending"}`` -- a plain mapping.
    * ``[{"name": "auth_status", "value": "pending"}]`` -- the proto-shaped
      list emitted by ``cxas export`` and shown in the console.
    * ``["auth_status"]`` -- a bare list of names, meaning "look the current
      value up at runtime" (mapped to ``None``).
    * A JSON string of any of the above.
    """
    if v is None:
        return {}
    if isinstance(v, str):
        try:
            return _parse_variables_input(json.loads(v))
        except json.JSONDecodeError:
            return {}
    if isinstance(v, list):
        parsed: dict[str, Any] = {}
        for item in v:
            # Proto shape: {"name": ..., "value": ...}
            if isinstance(item, dict) and "name" in item:
                parsed[str(item["name"])] = item.get("value")
            # Single-entry mapping: {"auth_status": "pending"}
            elif isinstance(item, dict):
                parsed.update({str(k): val for k, val in item.items()})
            # Bare name: fetch the current value at runtime.
            else:
                parsed[str(item)] = None
        return parsed
    if isinstance(v, dict):
        return v
    return {}


# A variable map that tolerates the mapping / list / JSON-string forms.
VariableMap = Annotated[dict[str, Any], BeforeValidator(_parse_variables_input)]


class ToolCall(BaseModel):
    action: str
    args: dict[str, Any] = {}
    output: str | dict[str, Any] | None = None
    agent: str | None = None


class Turn(BaseModel):
    user: str | None = None
    agent: str | list[str] | None = None
    tool_calls: list[ToolCall] = []
    # Session state to stage immediately before this turn's user input.
    # STABLE goldens replay every turn in an isolated session, so state has to
    # be re-declared on each turn that depends on it -- not just the first.
    variables: VariableMap = {}


class Conversation(BaseModel):
    conversation: str
    expectations: list[str] = []
    tags: list[str] = []
    session_parameters: VariableMap = {}
    turns: list[Turn]


class Conversations(BaseModel):
    common_session_parameters: dict[str, Any] = {}
    common_expectations: list[str] = []
    conversations: list[Conversation]


class ExpectationStatus(str, enum.Enum):
    MET = "Met"
    NOT_MET = "Not Met"


class ExpectationResult(pydantic.BaseModel):
    expectation: str
    status: ExpectationStatus = ExpectationStatus.NOT_MET
    justification: str = ""


class ExpectationOutput(pydantic.BaseModel):
    results: list[ExpectationResult] = []


class EvalUtils(Evaluations):
    """Utility class for processing and exporting CXAS Evaluation Results."""

    def __init__(self, app_name: str, env: str = "PROD", **kwargs: Any) -> None:
        """Initializes the EvalUtils class for processing Evaluation Results.

        Args:
            app_name: CXAS App ID
                (projects/{project}/locations/{location}/apps/{app}).
            env: Environment override (default: PROD).
            **kwargs (Any): Additional arguments passed to the parent class
                (Evaluations).
        """
        super().__init__(app_name=app_name, env=env, **kwargs)
        self.app_name = app_name
        self.tools_client = Tools(app_name=self.app_name, creds=self.creds)
        self.var_client = Variables(app_name=self.app_name, creds=self.creds)
        try:
            self.tool_map = self.tools_client.get_tools_map(reverse=True)
        except Exception as e:
            logger.warning(
                "Failed to fetch tool map for %s: %s", self.app_name, e
            )
            self.tool_map = {}
        self.agents_client = Agents(app_name=self.app_name, creds=self.creds)
        try:
            self.agent_map = self.agents_client.get_agents_map(reverse=True)
        except Exception as e:
            logger.warning(
                "Failed to fetch agent map for %s: %s", self.app_name, e
            )
            self.agent_map = {}
        # Defer import to break circular dependency:
        # conversation_history -> latency_parser -> eval_utils
        # -> conversation_history
        from cxas_scrapi.core.conversation_history import (  # noqa: PLC0415
            ConversationHistory,
        )

        self.ch_client = ConversationHistory(
            app_name=self.app_name, creds=self.creds
        )
        self.eval_client = Evaluations(
            app_name=self.app_name, env=env, creds=self.creds
        )

    @staticmethod
    def parse_variables_input(v: Any) -> dict[str, Any]:
        """Allows YAML to accept a list, a JSON string OR a dictionary.

        See :func:`_parse_variables_input` for the accepted shapes.
        """
        return _parse_variables_input(v)

    @staticmethod
    def _map_outcome(val: typing.Any) -> typing.Any:
        if isinstance(val, int):
            outcome_map = {0: "UNSPECIFIED", 1: "PASS", 2: "FAIL"}
            return outcome_map.get(val, f"UNKNOWN_{val}")
        return str(val) if val is not None else None

    @staticmethod
    def is_milestone_eval_data(data: Any) -> bool:
        """Determines if the given data represents a Milestone evaluation."""
        if isinstance(data, dict):
            if "evals" in data and isinstance(data["evals"], list):
                return True
            if "steps" in data and isinstance(data["steps"], list):
                return any(
                    isinstance(s, dict)
                    and ("goal" in s or "success_criteria" in s)
                    for s in data["steps"]
                )
            scenario = data.get("scenario")
            if isinstance(scenario, dict) and isinstance(
                scenario.get("milestones"), list
            ):
                return True
            if isinstance(data.get("milestones"), list):
                return True
        elif isinstance(data, list):
            if data and all(
                isinstance(item, dict)
                and "steps" in item
                and isinstance(item["steps"], list)
                for item in data
            ):
                return True
        return False

    @staticmethod
    def _format_milestone_expectation(exp: Any) -> str:
        """Normalizes a string or dict expectation into a prompt string."""
        if isinstance(exp, dict):
            title = exp.get("title") or exp.get("name")
            body = (
                exp.get("expectation")
                or exp.get("prompt")
                or exp.get("description")
                or ""
            )
            if title and body:
                return f"{title}: {body}"
            if body:
                return str(body)
            if title:
                return str(title)
            return ""
        return str(exp) if exp else ""

    @staticmethod
    def sanitize_milestone_eval_dict(data: Any) -> dict[str, Any]:
        """Normalizes and sanitizes simulation evals data into strict Milestone
        YAML format compatible with CES platform MilestoneYamlTranslator.

        Removes non-whitelisted keys (e.g. client-only audio_expectations,
        naturalness_metric, barge_in_after_ms) and ensures inject_variables
        only exists on step 0 (hoisting to session_parameters or step 0 if found
        on later steps).
        """
        if not data:
            return {"evals": []}

        if isinstance(data, dict) and (
            (
                isinstance(data.get("scenario"), dict)
                and isinstance(data["scenario"].get("milestones"), list)
            )
            or isinstance(data.get("milestones"), list)
        ):
            data = Evaluations.eval_dict_to_yaml(data)

        root_dict: dict[str, Any] = {}
        evals_list: list[dict[str, Any]] = []

        if isinstance(data, dict):
            if "evals" in data and isinstance(data["evals"], list):
                common_params = data.get(
                    "common_session_parameters"
                ) or data.get("commonSessionParameters")
                if isinstance(common_params, dict) and common_params:
                    root_dict["common_session_parameters"] = (
                        Common.unwrap_struct(common_params)
                    )
                common_exps = data.get("common_expectations") or data.get(
                    "commonExpectations"
                )
                if isinstance(common_exps, list) and common_exps:
                    formatted_common_exps = [
                        EvalUtils._format_milestone_expectation(e)
                        for e in common_exps
                    ]
                    formatted_common_exps = [
                        e for e in formatted_common_exps if e
                    ]
                    if formatted_common_exps:
                        root_dict["common_expectations"] = formatted_common_exps
                init_evt = data.get("initial_event") or data.get("initialEvent")
                if init_evt:
                    root_dict["initial_event"] = init_evt
                evals_list = data["evals"]
            elif "steps" in data:
                evals_list = [data]
            else:
                evals_list = []
        elif isinstance(data, list):
            evals_list = [item for item in data if isinstance(item, dict)]

        sanitized_evals: list[dict[str, Any]] = []
        client_only_fields = [
            "barge_in_after_ms",
            "audio_expectations",
            "audio_analysis",
            "naturalness_metric",
            "naturalness",
            "voice_config",
        ]
        for eval_idx, item in enumerate(evals_list):
            if not isinstance(item, dict):
                continue

            name = item.get("name") or f"milestone_eval_{eval_idx + 1}"
            stripped_eval_fields = [
                f for f in client_only_fields if item.get(f) is not None
            ]
            if stripped_eval_fields:
                logger.warning(
                    "Stripping unsupported client-only field(s) %s from "
                    "evaluation '%s' for Milestone platform compatibility.",
                    stripped_eval_fields,
                    name,
                )
            sanitized_eval: dict[str, Any] = {"name": str(name)}

            tags = item.get("tags")
            if tags and isinstance(tags, list):
                sanitized_eval["tags"] = [str(t) for t in tags]

            session_params = (
                item.get("session_parameters")
                or item.get("sessionParameters")
                or {}
            )
            if session_params:
                sanitized_eval["session_parameters"] = Common.unwrap_struct(
                    session_params
                )
            else:
                sanitized_eval["session_parameters"] = {}

            expectations = (
                item.get("expectations")
                or item.get("evaluation_expectations")
                or item.get("evaluationExpectations")
                or []
            )
            if expectations and isinstance(expectations, list):
                formatted_exps = [
                    EvalUtils._format_milestone_expectation(e)
                    for e in expectations
                ]
                formatted_exps = [e for e in formatted_exps if e]
                if formatted_exps:
                    sanitized_eval["expectations"] = formatted_exps

            max_turns = (
                item.get("max_turns")
                or item.get("maxTurns")
                or item.get("max_turn_count")
                or item.get("maxTurnCount")
            )
            if max_turns:
                with contextlib.suppress(ValueError, TypeError):
                    sanitized_eval["max_turns"] = int(max_turns)

            init_event = item.get("initial_event") or item.get("initialEvent")
            if init_event:
                sanitized_eval["initial_event"] = init_event

            eval_meta = item.get("metadata")
            if isinstance(eval_meta, dict) and eval_meta:
                sanitized_eval["metadata"] = Common.unwrap_struct(eval_meta)

            raw_steps = item.get("steps", [])
            if not isinstance(raw_steps, list):
                raw_steps = [raw_steps]

            sanitized_steps: list[dict[str, Any]] = []
            hoisted_vars: dict[str, Any] = {}

            for step_idx, raw_step in enumerate(raw_steps):
                if not isinstance(raw_step, dict):
                    continue

                stripped_step_fields = [
                    f for f in client_only_fields if raw_step.get(f) is not None
                ]
                if stripped_step_fields:
                    logger.warning(
                        "Stripping unsupported client-only field(s) %s from "
                        "step %d in evaluation '%s'.",
                        stripped_step_fields,
                        step_idx,
                        name,
                    )

                goal = raw_step.get("goal", "")
                success_criteria = (
                    raw_step.get("success_criteria")
                    or raw_step.get("successCriteria")
                    or ""
                )
                if not goal and not success_criteria:
                    goal = f"Step {step_idx + 1}"
                    success_criteria = "Goal achieved"
                elif not goal:
                    goal = success_criteria
                elif not success_criteria:
                    success_criteria = goal

                step_dict: dict[str, Any] = {
                    "goal": str(goal),
                    "success_criteria": str(success_criteria),
                }

                resp_guide = (
                    raw_step.get("response_guide")
                    or raw_step.get("responseGuide")
                    or raw_step.get("protocol")
                )
                if resp_guide:
                    step_dict["response_guide"] = str(resp_guide)

                step_max_turns = (
                    raw_step.get("max_turns")
                    or raw_step.get("max_turn_count")
                    or raw_step.get("maxTurnCount")
                )
                if step_max_turns:
                    with contextlib.suppress(ValueError, TypeError):
                        step_dict["max_turns"] = int(step_max_turns)

                static_utt = raw_step.get("static_utterance") or raw_step.get(
                    "staticUtterance"
                )
                if static_utt:
                    step_dict["static_utterance"] = str(static_utt)

                event_val = raw_step.get("event")
                if event_val:
                    step_dict["event"] = str(event_val)

                inject_vars = raw_step.get("inject_variables") or raw_step.get(
                    "injectVariables"
                )
                if inject_vars and isinstance(inject_vars, dict):
                    unwrapped_inj = Common.unwrap_struct(inject_vars)
                    if step_idx == 0:
                        step_dict["inject_variables"] = unwrapped_inj
                    else:
                        logger.warning(
                            "Hoisting inject_variables from step %d to "
                            "session_parameters for evaluation '%s' (platform "
                            "only allows inject_variables on step 0).",
                            step_idx,
                            name,
                        )
                        hoisted_vars.update(unwrapped_inj)

                step_meta = raw_step.get("metadata")
                if isinstance(step_meta, dict) and step_meta:
                    step_dict["metadata"] = Common.unwrap_struct(step_meta)

                sanitized_steps.append(step_dict)

            if hoisted_vars:
                sanitized_eval["session_parameters"].update(hoisted_vars)

            if not sanitized_eval["session_parameters"]:
                sanitized_eval.pop("session_parameters", None)

            sanitized_eval["steps"] = sanitized_steps

            sanitized_eval = {
                k: v
                for k, v in sanitized_eval.items()
                if k in MILESTONE_ALLOWED_EVAL_KEYS
            }
            sanitized_evals.append(sanitized_eval)

        root_dict["evals"] = sanitized_evals
        return {
            k: v
            for k, v in root_dict.items()
            if k in MILESTONE_ALLOWED_ROOT_KEYS
        }

    @staticmethod
    def convert_sim_evals_to_milestone_yaml(source: Any) -> str:
        """Converts local simulation evals (file path, directory path, or
        dict/list) into a CES Platform-compatible Milestone YAML string.
        """
        raw_cases: list[dict[str, Any]] = []
        root_data: dict[str, Any] = {}

        if isinstance(source, str):
            if os.path.isdir(source):
                yaml_files = sorted(
                    glob.glob(os.path.join(source, "*.yaml"))
                    + glob.glob(os.path.join(source, "*.yml"))
                    + glob.glob(os.path.join(source, "*.json"))
                )
                for fpath in yaml_files:
                    with open(fpath, encoding="utf-8") as f:
                        content = (
                            yaml.safe_load(f)
                            if fpath.endswith((".yaml", ".yml"))
                            else json.load(f)
                        )
                        if isinstance(content, dict):
                            if "evals" in content and isinstance(
                                content["evals"], list
                            ):
                                file_common_params = (
                                    content.get("common_session_parameters")
                                    or content.get("commonSessionParameters")
                                    or {}
                                )
                                file_common_exps = (
                                    content.get("common_expectations")
                                    or content.get("commonExpectations")
                                    or []
                                )
                                file_init_evt = content.get(
                                    "initial_event"
                                ) or content.get("initialEvent")
                                for c in content["evals"]:
                                    if not isinstance(c, dict):
                                        continue
                                    case_copy = dict(c)
                                    if (
                                        isinstance(file_common_params, dict)
                                        and file_common_params
                                    ):
                                        existing_params = (
                                            case_copy.get("session_parameters")
                                            or case_copy.get(
                                                "sessionParameters"
                                            )
                                            or {}
                                        )
                                        case_copy["session_parameters"] = {
                                            **Common.unwrap_struct(
                                                file_common_params
                                            ),
                                            **Common.unwrap_struct(
                                                existing_params
                                            ),
                                        }
                                    if (
                                        isinstance(file_common_exps, list)
                                        and file_common_exps
                                    ):
                                        existing_exps = (
                                            case_copy.get("expectations")
                                            or case_copy.get(
                                                "evaluation_expectations"
                                            )
                                            or case_copy.get(
                                                "evaluationExpectations"
                                            )
                                            or []
                                        )
                                        case_copy["expectations"] = list(
                                            file_common_exps
                                        ) + list(existing_exps)
                                    if (
                                        file_init_evt
                                        and "initial_event" not in case_copy
                                        and "initialEvent" not in case_copy
                                    ):
                                        case_copy["initial_event"] = (
                                            file_init_evt
                                        )
                                    raw_cases.append(case_copy)
                            elif "steps" in content:
                                raw_cases.append(content)
                        elif isinstance(content, list):
                            raw_cases.extend(
                                [c for c in content if isinstance(c, dict)]
                            )
                root_data = {"evals": raw_cases}
            elif os.path.isfile(source):
                with open(source, encoding="utf-8") as f:
                    content = (
                        yaml.safe_load(f)
                        if source.endswith((".yaml", ".yml"))
                        else json.load(f)
                    )
                    root_data = content
            else:
                try:
                    root_data = yaml.safe_load(source)
                except Exception:
                    root_data = json.loads(source)
        elif isinstance(source, (dict, list)):
            root_data = source
        else:
            raise ValueError(f"Unsupported source type: {type(source)}")

        sanitized = EvalUtils.sanitize_milestone_eval_dict(root_data)
        return yaml.dump(sanitized, sort_keys=False, allow_unicode=True)

    def push_milestone_evals_from_yaml(
        self,
        source: Any,
        app_name: str | None = None,
        conflict_strategy: str | int = "OVERWRITE",
    ) -> Any:
        """Converts and pushes local simulation evaluations to the platform as
        Milestone evaluations via the REST importEvaluations endpoint.
        """
        app_name = app_name or self.app_name
        yaml_content = self.convert_sim_evals_to_milestone_yaml(source)
        return self.eval_client.import_evaluations(
            app_name=app_name,
            yaml_content=yaml_content,
            conflict_strategy=conflict_strategy,
        )

    @staticmethod
    def score_result_audio(result: typing.Any) -> bool:
        """Score a single result using audio-correct method.
        In audio mode, taskCompleted is broken (always False).
        Use goalScore AND allExpectationsSatisfied instead.
        """
        res_dict = (
            type(result).to_dict(result)
            if not isinstance(result, dict)
            else result
        )
        sr = res_dict.get("scenario_result", {})
        goal = sr.get("user_goal_satisfaction_result", {}).get("score", 0)
        all_exp = sr.get("all_expectations_satisfied", False)
        return (goal == 1) and all_exp

    @staticmethod
    def _extract_tool_call_args(tool_call: dict[str, Any]) -> dict[str, Any]:
        """Extracts tool arguments from various possible alias keys."""
        for key in ["args", "arguments"]:
            if key in tool_call:
                return tool_call[key]
        return {}

    @staticmethod
    def _get_exp_act(outcome_obj: dict[str, Any]) -> tuple[str, str, str]:
        e_text = ""
        a_text = "(None / Missed)"
        f_type = "Turn Expectation"
        e_dict = outcome_obj.get("expectation", {})

        if "agent_response" in e_dict:
            chunks = e_dict["agent_response"].get("chunks", [])
            e_text = "agent_response"
            if chunks:
                e_text = chunks[0].get("text", "agent_response")
            f_type = "Semantic Similarity"
        elif "tool_call" in e_dict:
            e_text = e_dict["tool_call"].get(
                "display_name",
                e_dict["tool_call"].get("id", "tool_call"),
            )
            f_type = "Tool Call"
        elif "tool_response" in e_dict:
            e_text = e_dict["tool_response"].get(
                "display_name", "tool_response"
            )
            f_type = "Tool Response"
        elif "agent_transfer" in e_dict:
            e_text = e_dict["agent_transfer"].get(
                "display_name",
                e_dict["agent_transfer"].get("target_agent", "agent_transfer"),
            )
            f_type = "Routing / Agent"

        if "observed_agent_response" in outcome_obj:
            chunks = outcome_obj["observed_agent_response"].get("chunks", [])
            a_text = chunks[0].get("text", "") if chunks else ""
        elif "observed_tool_call" in outcome_obj:
            a_text = outcome_obj["observed_tool_call"].get(
                "display_name",
                outcome_obj["observed_tool_call"].get("id", ""),
            )
        elif "observed_tool_response" in outcome_obj:
            a_text = outcome_obj["observed_tool_response"].get(
                "display_name",
                outcome_obj["observed_tool_response"].get("id", ""),
            )
        elif "observed_agent_transfer" in outcome_obj:
            a_text = outcome_obj["observed_agent_transfer"].get(
                "display_name",
                outcome_obj["observed_agent_transfer"].get("target_agent", ""),
            )

        return e_text, a_text, f_type

    @staticmethod
    def _aggregate(arr: list[Any]) -> dict[str, int]:
        if not arr:
            return {"Average": 0, "p50": 0, "p90": 0, "p99": 0}
        ser = pd.Series(arr)
        return {
            "Average": int(ser.mean()),
            "p50": int(ser.quantile(0.50)),
            "p90": int(ser.quantile(0.90)),
            "p99": int(ser.quantile(0.99)),
        }

    def _process_dataset_turn(
        self,
        turn: Turn,
        session_params: dict[str, Any],
        params_injected: bool,
    ) -> dict[str, Any]:
        """Processes a single turn from a conversation dataset into JSON steps.

        Conversation-level ``session_parameters`` are staged once, on the first
        turn. Turn-level ``variables`` are staged on every turn that declares
        them: STABLE replays each turn in an isolated session with no carried
        state, so a stateful multi-turn golden has to re-declare whatever the
        turn depends on.

        Args:
            turn: The turn to compile.
            session_params: Conversation-level parameters, injected once.
            params_injected: Whether ``session_params`` were already staged by
                an earlier turn.

        Returns:
            A dict of ``{"steps": [...], "params_injected": bool}``.
        """
        steps = []
        user_input_text = turn.user

        variables: dict[str, Any] = {}
        if not params_injected and session_params:
            variables.update(session_params)
            params_injected = True
        # Turn-level values are more specific, so they win on conflict.
        variables.update(turn.variables)

        if variables:
            steps.append({"userInput": {"variables": variables}})

        user_input_obj = {}
        if user_input_text is None:
            user_input_obj["event"] = {"event": "welcome"}
        else:
            user_input_obj["text"] = str(user_input_text)

        steps.append({"userInput": user_input_obj})

        if turn.agent:
            agents = (
                turn.agent if isinstance(turn.agent, list) else [turn.agent]
            )
            for agent_text in agents:
                if "# silent" not in agent_text:
                    steps.append(
                        {
                            "expectation": {
                                "agentResponse": {
                                    "role": "agent",
                                    "chunks": [{"text": agent_text}],
                                }
                            }
                        }
                    )

        for tool_call in turn.tool_calls:
            action = tool_call.action

            if not action:
                logger.warning("Skipping empty action in tool call.")
                continue

            if action == "transfer_to_agent" and (
                tool_call.args.get("agent") or tool_call.agent
            ):
                agent_name = tool_call.args.get("agent") or tool_call.agent
                agent_resource = self.agent_map.get(agent_name, agent_name)
                steps.append(
                    {
                        "expectation": {
                            "agentTransfer": {"targetAgent": agent_resource}
                        }
                    }
                )
                continue

            tool_call_id = f"adk-{uuid.uuid4()}"
            # Resolve tool name
            tool_resource = self.tool_map.get(action, action)

            # Fallback for built-in actions or tools not in map
            if action not in self.tool_map and not tool_resource.startswith(
                "projects/"
            ):
                tool_resource = f"{self.app_name}/tools/{action}"

            tool_call_expectation = {
                "expectation": {
                    "toolCall": {
                        "id": tool_call_id,
                        "tool": tool_resource,
                        "args": tool_call.args,
                    }
                }
            }
            steps.append(tool_call_expectation)

            if tool_call.output is not None:
                steps.append(
                    {
                        "expectation": {
                            "toolResponse": {
                                "id": tool_call_id,
                                "tool": tool_resource,
                                "response": tool_call.output,
                            }
                        }
                    }
                )

        return {"steps": steps, "params_injected": params_injected}

    def _parse_eval_results(
        self,
        results: list[Any] | str | None = None,
        eval_names: list[str] | str | None = None,
    ) -> tuple[
        list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]
    ]:
        if isinstance(results, str):
            eval_names = [results]
            results = None
        elif (
            isinstance(results, list)
            and len(results) > 0
            and isinstance(results[0], str)
        ):
            eval_names = results
            results = None

        if isinstance(eval_names, str):
            eval_names = [eval_names]

        eval_cache = {}
        if results is None:
            results = []
            evaluations = self.list_evaluations(self.app_name)
            seen_eval_resources: set[str] = set()
            for evaluation in evaluations:
                eval_res_name = getattr(evaluation, "name", "")
                if eval_res_name:
                    eval_cache[eval_res_name] = getattr(
                        evaluation, "display_name", "Unknown Evaluation"
                    )
                if eval_res_name in seen_eval_resources:
                    continue
                if (
                    eval_names
                    and evaluation.display_name not in eval_names
                    and eval_res_name not in eval_names
                ):
                    continue
                if eval_res_name:
                    seen_eval_resources.add(eval_res_name)
                results.extend(self.list_evaluation_results(eval_res_name))

        run_summaries = []
        expectations = []
        turns = []

        for result in results:
            res_dict = (
                type(result).to_dict(result)
                if not isinstance(result, dict)
                else result
            )
            result_name = res_dict.get("name", "")
            eval_name = "/".join(result_name.split("/")[:-2])
            display_name = "Unknown Evaluation"
            if eval_name:
                if eval_name not in eval_cache:
                    try:
                        eval_obj = self.get_evaluation(eval_name)
                        disp = (
                            (
                                eval_obj.get("displayName")
                                or eval_obj.get("display_name")
                            )
                            if isinstance(eval_obj, dict)
                            else getattr(eval_obj, "display_name", None)
                        )
                        if not disp or not isinstance(disp, str):
                            raise ValueError("Missing display_name")
                        eval_cache[eval_name] = disp
                    except Exception:
                        try:
                            eval_obj = self.eval_client.get_evaluation_rest(
                                eval_name
                            )
                            eval_cache[eval_name] = (
                                eval_obj.get("displayName")
                                or eval_obj.get("display_name")
                                or "Unknown Evaluation"
                                if isinstance(eval_obj, dict)
                                else "Unknown Evaluation"
                            )
                        except Exception:
                            eval_cache[eval_name] = "Unknown Evaluation"
                display_name = eval_cache[eval_name]

            raw_status = (
                res_dict.get("evaluation_status")
                if "evaluation_status" in res_dict
                else res_dict.get("evaluationStatus", 0)
            )
            if isinstance(raw_status, int):
                status_map = {0: "UNSPECIFIED", 1: "PASS", 2: "FAIL"}
                status_str = status_map.get(raw_status, f"UNKNOWN_{raw_status}")
            else:
                status_str = str(raw_status)

            golden = (
                res_dict.get("golden_result")
                or res_dict.get("goldenResult")
                or {}
            )
            scenario = (
                res_dict.get("scenario_result")
                or res_dict.get("scenarioResult")
                or {}
            )
            metrics = golden if golden else scenario

            sem_score_raw = metrics.get("semantic_similarity_result", {}).get(
                "score"
            )
            if not sem_score_raw and "semanticSimilarityResult" in metrics:
                sem_score_raw = metrics["semanticSimilarityResult"].get("score")
            sem_score_str = None
            if isinstance(sem_score_raw, (int, float)):
                score_val = (
                    int(sem_score_raw)
                    if sem_score_raw == int(sem_score_raw)
                    else sem_score_raw
                )
                sem_score_str = f"{score_val} / 4.0"
            elif sem_score_raw is not None:
                sem_score_str = str(sem_score_raw)

            tool_score_raw = metrics.get(
                "overall_tool_invocation_result", {}
            ).get("tool_invocation_score")
            if not tool_score_raw and "overallToolInvocationResult" in metrics:
                tool_score_raw = metrics["overallToolInvocationResult"].get(
                    "toolInvocationScore"
                )
            tool_score_str = (
                f"{int(tool_score_raw * 100)}%"
                if isinstance(tool_score_raw, (int, float))
                else EvalUtils._map_outcome(tool_score_raw)
            )

            exec_state_raw = (
                res_dict.get("execution_state")
                if "execution_state" in res_dict
                else res_dict.get("executionState", 0)
            )
            if isinstance(exec_state_raw, int):
                exec_state_map = {
                    0: "UNSPECIFIED",
                    1: "RUNNING",
                    2: "COMPLETED",
                    3: "ERROR",
                    4: "CANCELLED",
                    5: "QUEUED",
                }
                exec_state_str = exec_state_map.get(
                    exec_state_raw, f"UNKNOWN_{exec_state_raw}"
                )
            else:
                exec_state_str = str(exec_state_raw)

            err_info = (
                res_dict.get("error_info") or res_dict.get("errorInfo") or {}
            )
            err_msg = (
                err_info.get("error_message")
                or err_info.get("errorMessage")
                or "Unknown Agent Exception"
            )

            base_info = {
                "display_name": display_name,
                "evaluation_run": res_dict.get("evaluation_run")
                or res_dict.get("evaluationRun", ""),
                "eval_result_id": result_name,
                "conversation": scenario.get("conversation")
                or golden.get("conversation")
                or "",
                "evaluation_status": status_str,
                "execution_state": exec_state_str,
                "error_message": err_msg,
                "semantic_score": sem_score_str,
                "tool_invocation_score": tool_score_str,
                "create_time": res_dict.get("create_time")
                or res_dict.get("createTime", ""),
                "update_time": res_dict.get("update_time")
                or res_dict.get("updateTime", ""),
            }
            run_summaries.append(base_info)

            expectation_list = (
                metrics.get("expectation_results")
                or metrics.get("expectationResults")
                or metrics.get("evaluation_expectation_results")
                or metrics.get("evaluationExpectationResults")
                or []
            )
            for exp_item in expectation_list:
                is_new_format = "prompt" in exp_item or "outcome" in exp_item
                outcome_val = exp_item.get("outcome")
                if is_new_format:
                    is_met = outcome_val in (1, "PASS", "MET", "STATUS_MET")
                    not_met_count = 0 if is_met else 1
                    met_count = 1 if is_met else 0
                else:
                    not_met_count = exp_item.get(
                        "not_met_count"
                    ) or exp_item.get("notMetCount", 0)
                    met_count = exp_item.get("met_count") or exp_item.get(
                        "metCount", 0
                    )

                row = {
                    "display_name": display_name,
                    "eval_result_id": result_name,
                    "evaluation_run": base_info.get("evaluation_run"),
                    "evaluation_status": base_info.get("evaluation_status"),
                    "record_type": "summary_expectation",
                    "expectation": str(
                        exp_item.get("prompt")
                        or exp_item.get("expectation")
                        or exp_item.get("evaluationExpectation", "")
                    ),
                    "met_count": met_count,
                    "not_met_count": not_met_count,
                    "met_percentage": (
                        exp_item.get("met_percentage")
                        or exp_item.get("metPercentage", 0.0)
                    ),
                    "not_met_percentage": (
                        exp_item.get("not_met_percentage")
                        or exp_item.get("notMetPercentage", 0.0)
                    ),
                    "explanation": str(exp_item.get("explanation", "")),
                }
                expectations.append(row)

            # Process milestone results if scenario result is present
            milestone_results = (
                scenario.get("milestone_results")
                or scenario.get("milestoneResults")
                or []
            )
            for m_res in milestone_results:
                milestone_obj = m_res.get("milestone", {})
                m_goal = (
                    milestone_obj.get("goal")
                    or milestone_obj.get("success_criteria")
                    or milestone_obj.get("successCriteria")
                    or "Milestone Goal"
                )
                m_criteria = (
                    milestone_obj.get("success_criteria")
                    or milestone_obj.get("successCriteria")
                    or ""
                )
                raw_m_status = m_res.get("status", "NOT_STARTED")
                if isinstance(raw_m_status, int):
                    m_status_map = {
                        0: "UNSPECIFIED",
                        1: "NOT_STARTED",
                        2: "IN_PROGRESS",
                        3: "COMPLETED",
                    }
                    m_status = m_status_map.get(raw_m_status, str(raw_m_status))
                else:
                    m_status = str(raw_m_status)
                is_completed = m_status in ("COMPLETED", "STATUS_COMPLETED")
                justification = str(m_res.get("justification", ""))
                row = {
                    "display_name": display_name,
                    "eval_result_id": result_name,
                    "evaluation_run": base_info.get("evaluation_run"),
                    "evaluation_status": base_info.get("evaluation_status"),
                    "record_type": "milestone",
                    "expectation": m_goal,
                    "success_criteria": m_criteria,
                    "status": "COMPLETED" if is_completed else m_status,
                    "explanation": justification,
                    "met_count": 1 if is_completed else 0,
                    "not_met_count": 0 if is_completed else 1,
                    "met_percentage": 100.0 if is_completed else 0.0,
                    "not_met_percentage": 0.0 if is_completed else 100.0,
                }
                expectations.append(row)

            turn_list = golden.get("turn_replay_results", [])
            if isinstance(turn_list, list):
                for i, turn in enumerate(turn_list):
                    if not isinstance(turn, dict):
                        continue
                    row = {
                        "display_name": display_name,
                        "eval_result_id": result_name,
                        "evaluation_run": base_info.get("evaluation_run"),
                        "evaluation_status": base_info.get("evaluation_status"),
                        "turn_index": i + 1,
                    }
                    lat = turn.get("turn_latency", {})
                    row["latency_seconds"] = (
                        lat.get("seconds", 0) if isinstance(lat, dict) else None
                    )

                    sem_res = turn.get("semantic_similarity_result", {})
                    if isinstance(sem_res, dict):
                        row["semantic_score"] = sem_res.get("score")
                        row["semantic_outcome"] = EvalUtils._map_outcome(
                            sem_res.get("outcome")
                        )
                    else:
                        row["semantic_score"] = None
                        row["semantic_outcome"] = None

                    hal_res = turn.get("hallucination_result", {})
                    row["hallucination_score"] = (
                        hal_res.get("score")
                        if isinstance(hal_res, dict)
                        else None
                    )
                    row["hallucination"] = (
                        hal_res.get("label", "")
                        if isinstance(hal_res, dict)
                        else None
                    )

                    tool_score = turn.get("tool_invocation_score")
                    if not tool_score:
                        tool_score = turn.get(
                            "overall_tool_invocation_result", {}
                        ).get("tool_invocation_score")
                    row["tool_invocation_score"] = EvalUtils._map_outcome(
                        tool_score
                    )

                    outcomes = turn.get("expectation_outcome", [])
                    row["expectation_outcomes"] = json.dumps(outcomes)

                    turns.append(row)

        return run_summaries, expectations, turns

    def evals_to_dataframe(
        self,
        results: list[Any] | str | None = None,
        eval_names: list[str] | str | None = None,
    ) -> dict[str, Any]:
        """Provides three simplified views of the evaluation data.

        Returns:
            A dict with 'summary', 'failures', and 'trace' DataFrames.
        """
        run_summaries, expectations, turns = self._parse_eval_results(
            results, eval_names
        )

        # Summary
        df_summary = pd.DataFrame(run_summaries)
        if not df_summary.empty:
            if "create_time" in df_summary.columns:
                df_summary["create_time"] = pd.to_datetime(
                    df_summary["create_time"]
                )
            if "update_time" in df_summary.columns:
                df_summary["update_time"] = pd.to_datetime(
                    df_summary["update_time"]
                )
            if "update_time" in df_summary.columns:
                df_summary = df_summary.sort_values(
                    by="update_time", ascending=False
                ).reset_index(drop=True)

        # Failures
        failures = []
        for run_sum in run_summaries:
            if run_sum.get("execution_state") in (
                "ERROR",
                "ERRORED",
            ) or run_sum.get("evaluation_status") in ("ERROR", "ERRORED"):
                raw_err = run_sum.get(
                    "error_message", "Unknown Agent Exception"
                )
                failures.append(
                    {
                        "display_name": run_sum.get("display_name", "Unknown"),
                        "eval_result_id": run_sum.get("eval_result_id", ""),
                        "turn_index": None,
                        "failure_type": "System Engine Error",
                        "expected": "Run evaluation to completion",
                        "actual": f"Error: {raw_err}",
                        "score": None,
                    }
                )

        # Metadata
        all_metadata = []

        for exp in expectations:
            if exp.get("record_type") == "milestone":
                if exp.get("not_met_count", 0) > 0:
                    status_lbl = exp.get("status", "NOT_STARTED")
                    explanation = exp.get("explanation", "")
                    actual_text = (
                        f"({status_lbl}) {explanation}"
                        if explanation
                        else f"({status_lbl})"
                    )
                    failures.append(
                        {
                            "display_name": exp["display_name"],
                            "eval_result_id": exp["eval_result_id"],
                            "turn_index": None,
                            "failure_type": "Milestone",
                            "expected": str(exp.get("expectation", "")),
                            "actual": actual_text,
                            "score": None,
                        }
                    )
                status_val = exp.get("status", "NOT_STARTED")
                outcome_str = "PASS" if status_val == "COMPLETED" else "FAIL"
                all_metadata.append(
                    {
                        "display_name": exp["display_name"],
                        "evaluation_run": exp.get("evaluation_run"),
                        "eval_result_id": exp["eval_result_id"],
                        "evaluation_status": exp.get("evaluation_status"),
                        "turn_index": None,
                        "type": "Milestone",
                        "expected": exp["expectation"],
                        "actual": exp["explanation"],
                        "outcome": outcome_str,
                        "score": status_val,
                        "success_criteria": exp.get("success_criteria", ""),
                    }
                )
                continue

            if exp.get("not_met_count", 0) > 0:
                explanation = exp.get("explanation", "")
                actual_text = (
                    f"(Not Met) {explanation}" if explanation else "(Not Met)"
                )
                failures.append(
                    {
                        "display_name": exp["display_name"],
                        "eval_result_id": exp["eval_result_id"],
                        "turn_index": None,
                        "failure_type": "Expectation",
                        "expected": str(exp.get("expectation", "")),
                        "actual": actual_text,
                        "score": None,
                    }
                )
            if exp.get("record_type") == "summary_expectation":
                not_met = exp.get("not_met_count", 0)
                met = exp.get("met_count", 0)
                outcome_str = "PASS" if not_met == 0 else "FAIL"

                all_metadata.append(
                    {
                        "display_name": exp["display_name"],
                        "evaluation_run": exp.get("evaluation_run"),
                        "eval_result_id": exp["eval_result_id"],
                        "evaluation_status": exp.get("evaluation_status"),
                        "turn_index": None,
                        "type": "Custom Expectation",
                        "expected": exp["expectation"],
                        "actual": exp["explanation"],
                        "outcome": outcome_str,
                        "score": f"{met} / {met + not_met}",
                    }
                )

        for turn in turns:
            outcomes_str = turn.get("expectation_outcomes", "[]")
            outcomes = []
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                outcomes = json.loads(outcomes_str)

            sem_handled = False
            tool_handled = False

            # Process individual explicit expectation failures
            for outcome_obj in outcomes:
                e, a, f = self._get_exp_act(outcome_obj)
                if f == "Tool Response":
                    continue
                raw_outcome = outcome_obj.get("outcome")
                outcome_str = EvalUtils._map_outcome(raw_outcome)

                score_val = None
                if f == "Semantic Similarity":
                    raw_score = turn.get("semantic_score")
                    if isinstance(raw_score, (int, float)):
                        s_val = (
                            int(raw_score)
                            if raw_score == int(raw_score)
                            else raw_score
                        )
                        score_val = f"{s_val} / 4.0"
                    else:
                        score_val = (
                            str(raw_score) if raw_score is not None else None
                        )
                elif f == "Tool Call":
                    raw_score = turn.get("tool_invocation_score")
                    if isinstance(raw_score, (int, float)):
                        score_val = f"{int(raw_score * 100)}%"
                    else:
                        score_val = EvalUtils._map_outcome(raw_score)

                all_metadata.append(
                    {
                        "display_name": turn["display_name"],
                        "eval_result_id": turn["eval_result_id"],
                        "evaluation_run": turn.get("evaluation_run"),
                        "evaluation_status": turn.get("evaluation_status"),
                        "turn_index": turn["turn_index"],
                        "type": f,
                        "expected": e,
                        "actual": a,
                        "outcome": outcome_str,
                        "score": score_val,
                    }
                )

                if raw_outcome == 2 or outcome_str == "FAIL":
                    if f == "Semantic Similarity":
                        sem_handled = True
                    elif f == "Tool Call":
                        raw_score = turn.get("tool_invocation_score")
                        if EvalUtils._map_outcome(raw_score) == "FAIL":
                            tool_handled = True

                    failures.append(
                        {
                            "display_name": turn["display_name"],
                            "eval_result_id": turn["eval_result_id"],
                            "turn_index": turn["turn_index"],
                            "failure_type": f,
                            "expected": e,
                            "actual": a,
                            "score": score_val,
                        }
                    )

            # Process overall semantic failure if not caught
            sem_outcome = turn.get("semantic_outcome")
            if sem_outcome == "FAIL" and not sem_handled:
                e_text, a_text = "", ""
                for outcome_obj in outcomes:
                    if "agent_response" in outcome_obj.get("expectation", {}):
                        e_text, a_text, _ = self._get_exp_act(outcome_obj)
                        break
                raw_score = turn.get("semantic_score")
                if isinstance(raw_score, (int, float)):
                    s_val = (
                        int(raw_score)
                        if raw_score == int(raw_score)
                        else raw_score
                    )
                    score_val = f"{s_val} / 4.0"
                else:
                    score_val = (
                        str(raw_score) if raw_score is not None else None
                    )

                failures.append(
                    {
                        "display_name": turn["display_name"],
                        "eval_result_id": turn["eval_result_id"],
                        "turn_index": turn["turn_index"],
                        "failure_type": "Semantic Similarity",
                        "expected": e_text,
                        "actual": a_text,
                        "score": score_val,
                    }
                )

            # Process overall tool failure if not caught
            tool_outcome = turn.get("tool_invocation_score")
            if tool_outcome == "FAIL" and not tool_handled:
                e_text, a_text = "", ""
                for outcome_obj in outcomes:
                    if "tool_call" in outcome_obj.get("expectation", {}):
                        e_text, a_text, _ = self._get_exp_act(outcome_obj)
                        break
                failures.append(
                    {
                        "display_name": turn["display_name"],
                        "eval_result_id": turn["eval_result_id"],
                        "turn_index": turn["turn_index"],
                        "failure_type": "Tool Call",
                        "expected": e_text,
                        "actual": a_text,
                        "score": "FAIL",
                    }
                )

        df_failures = pd.DataFrame(failures)
        if not df_failures.empty:
            df_failures = df_failures.sort_values(
                by=["eval_result_id", "turn_index"]
            ).reset_index(drop=True)

        df_traces = pd.DataFrame(turns)

        df_metadata = pd.DataFrame(all_metadata)

        return {
            "summary": df_summary,
            "failures": df_failures,
            "trace": df_traces,
            "metadata": df_metadata,
        }

    def get_latency_metrics_dfs(
        self,
        results: list[Any] | None = None,
        eval_names: list[str] | None = None,
        app_name: str | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Generates latency metrics DataFrames from results and traces.

        Args:
            results: An optional list of Eval Result List payload chunks.
            eval_names: Alternatively, an optional list of string Display
                Names / Names of Evals.
            app_name: Optional override if retrieving Conversation traces
                dynamically.
        """
        if not results:
            if not getattr(self, "app_name", None) and not app_name:
                raise ValueError(
                    "app_name must be set to look up evaluations by name."
                )
            results = []
            for name in eval_names or []:
                # Retrieve all results for the provided display name
                results.extend(self.list_evaluation_results(name))

        if not results:
            return {
                "eval_summary": pd.DataFrame(),
                "eval_details": pd.DataFrame(),
                "tool_summary": pd.DataFrame(),
                "tool_details": pd.DataFrame(),
                "callback_summary": pd.DataFrame(),
                "callback_details": pd.DataFrame(),
                "guardrail_summary": pd.DataFrame(),
                "guardrail_details": pd.DataFrame(),
            }

        conv_ids = set()
        for res_obj in results:
            res_dict = (
                type(res_obj).to_dict(res_obj)
                if not isinstance(res_obj, dict)
                else res_obj
            )
            turns = res_dict.get("golden_result", {}).get(
                "turn_replay_results", []
            )
            for t in turns:
                if t.get("conversation"):
                    conv_ids.add(t.get("conversation"))

        target_app = app_name or getattr(self, "app_name", None)
        traces = {}
        if target_app and conv_ids:
            if target_app == getattr(self, "app_name", None):
                ch_getter = self.ch_client.get_conversation
            else:
                # Defer import to break circular dependency:
                # conversation_history -> latency_parser -> eval_utils
                # -> conversation_history
                from cxas_scrapi.core.conversation_history import (  # noqa: PLC0415
                    ConversationHistory,
                )

                ch_client = ConversationHistory(
                    app_name=target_app, creds=self.creds
                )
                ch_getter = ch_client.get_conversation
            traces = LatencyParser.fetch_conversation_traces(
                list(conv_ids), ch_getter
            )

        eval_details_rows = []
        eval_summary_agg = []
        tool_details_rows = []
        callback_details_rows = []
        guardrail_details_rows = []
        llm_details_rows = []

        for res_obj in results:
            res_dict = (
                type(res_obj).to_dict(res_obj)
                if not isinstance(res_obj, dict)
                else res_obj
            )
            result_name = res_dict.get("name", "")
            tokens = result_name.split("/")
            eval_result_id = tokens[-1] if tokens else result_name
            eval_name = "/".join(tokens[:-2]) if len(tokens) >= 2 else ""

            # Get display name
            display_name = eval_name
            evals_map = getattr(self, "_get_or_load_evals_map", lambda x: {})(
                getattr(self, "app_name", None)
            )
            if evals_map:
                for lookup_name, full_path in evals_map.get(
                    "goldens", {}
                ).items():
                    if full_path == eval_name:
                        display_name = lookup_name
                        break
                for lookup_name, full_path in evals_map.get(
                    "scenarios", {}
                ).items():
                    if full_path == eval_name:
                        display_name = lookup_name
                        break

            golden = res_dict.get("golden_result", {})
            turns = golden.get("turn_replay_results", [])

            run_total_turn_latencies = []
            run_tool_latencies = []
            run_llm_latencies = []
            run_guardrail_latencies = []
            run_callback_latencies = []

            for turn_idx, t in enumerate(turns):
                total_turn_ms = LatencyParser._parse_duration_ms(
                    t.get("turn_latency", "0s")
                )

                # Turn specific items
                tool_calls = t.get("tool_call_latencies", [])
                turn_tool_ms = sum(
                    LatencyParser._parse_duration_ms(
                        tc.get("execution_latency", "0s")
                    )
                    for tc in tool_calls
                )
                tool_names = ", ".join(
                    [
                        tc.get("display_name", tc.get("tool", ""))
                        for tc in tool_calls
                    ]
                )

                turn_llm_ms = 0.0
                turn_guardrail_ms = 0.0
                turn_callback_ms = 0.0

                cid = t.get("conversation")
                if cid and cid in traces:
                    conv = traces[cid]
                    conv_turns = conv.get("turns", [])
                    # Match by index assuming evaluating linearly
                    if turn_idx < len(conv_turns):
                        trace_turn = conv_turns[turn_idx]
                        root = trace_turn.get("root_span", {})
                        if root:
                            sums = LatencyParser._process_spans(
                                [root],
                                eval_result_id,
                                turn_idx + 1,
                                tool_details_rows,
                                callback_details_rows,
                                guardrail_details_rows,
                                llm_details_rows,
                                context_key="eval_result_id",
                            )
                            turn_llm_ms = sums["LLM"]
                            turn_guardrail_ms = sums["Guardrail"]
                            turn_callback_ms = sums["Callback"]

                run_total_turn_latencies.append(total_turn_ms)
                if turn_tool_ms > 0:
                    run_tool_latencies.append(turn_tool_ms)
                if turn_llm_ms > 0:
                    run_llm_latencies.append(turn_llm_ms)
                if turn_guardrail_ms > 0:
                    run_guardrail_latencies.append(turn_guardrail_ms)
                if turn_callback_ms > 0:
                    run_callback_latencies.append(turn_callback_ms)

                eval_details_rows.append(
                    {
                        "display_name": display_name,
                        "eval_result_id": eval_result_id,
                        "turn_index": turn_idx + 1,
                        "Total Turn Latency (ms)": int(total_turn_ms),
                        "Tool Call Latencies (ms)": int(turn_tool_ms),
                        "LLM Latencies (ms)": int(turn_llm_ms),
                        "Guardrail Latencies (ms)": int(turn_guardrail_ms),
                        "Callback Latencies (ms)": int(turn_callback_ms),
                        "tool_names": tool_names,
                    }
                )

            # Compute run summary aggregations
            t_agg = self._aggregate(run_total_turn_latencies)
            tc_agg = self._aggregate(run_tool_latencies)
            llm_agg = self._aggregate(run_llm_latencies)
            gr_agg = self._aggregate(run_guardrail_latencies)
            cb_agg = self._aggregate(run_callback_latencies)

            t_p50, t_p90, t_p99 = t_agg["p50"], t_agg["p90"], t_agg["p99"]
            llm_p50, llm_p90, llm_p99 = (
                llm_agg["p50"],
                llm_agg["p90"],
                llm_agg["p99"],
            )
            tc_p50, tc_p90, tc_p99 = tc_agg["p50"], tc_agg["p90"], tc_agg["p99"]
            gr_p50, gr_p90, gr_p99 = gr_agg["p50"], gr_agg["p90"], gr_agg["p99"]
            cb_p50, cb_p90, cb_p99 = cb_agg["p50"], cb_agg["p90"], cb_agg["p99"]

            p50_90_99_turn = f"{t_p50} ms | {t_p90} ms | {t_p99} ms"
            p50_90_99_llm = f"{llm_p50} ms | {llm_p90} ms | {llm_p99} ms"
            p50_90_99_tc = f"{tc_p50} ms | {tc_p90} ms | {tc_p99} ms"
            p50_90_99_gr = f"{gr_p50} ms | {gr_p90} ms | {gr_p99} ms"
            p50_90_99_cb = f"{cb_p50} ms | {cb_p90} ms | {cb_p99} ms"

            eval_summary_agg.append(
                {
                    "display_name": display_name,
                    "eval_result_id": eval_result_id,
                    "evaluation_type": "Golden" if golden else "Scenario",
                    "Average (Turn)": f"""{t_agg["Average"]} ms""",
                    "p50 | p90 | p99 (Turn)": p50_90_99_turn,
                    "Average (LLM)": f"""{llm_agg["Average"]} ms""",
                    "p50 | p90 | p99 (LLM)": p50_90_99_llm,
                    "Average (Tool Call)": f"""{tc_agg["Average"]} ms""",
                    "p50 | p90 | p99 (Tool Call)": p50_90_99_tc,
                    "Average (Guardrail)": f"""{gr_agg["Average"]} ms""",
                    "p50 | p90 | p99 (Guardrail)": p50_90_99_gr,
                    "Average (Callback)": f"""{cb_agg["Average"]} ms""",
                    "p50 | p90 | p99 (Callback)": p50_90_99_cb,
                }
            )

        eval_details = pd.DataFrame(eval_details_rows)
        eval_summary = pd.DataFrame(eval_summary_agg)

        tool_details = pd.DataFrame(tool_details_rows)
        callback_details = pd.DataFrame(callback_details_rows)
        guardrail_details = pd.DataFrame(guardrail_details_rows)

        tool_summary = LatencyParser.build_summary_df(
            tool_details, ["tool_name"]
        )
        callback_summary = LatencyParser.build_summary_df(
            callback_details, ["agent", "stage", "description"]
        )
        guardrail_summary = LatencyParser.build_summary_df(
            guardrail_details, ["agent", "name"]
        )

        return {
            "eval_summary": eval_summary,
            "eval_details": eval_details,
            "tool_summary": tool_summary,
            "tool_details": tool_details,
            "callback_summary": callback_summary,
            "callback_details": callback_details,
            "guardrail_summary": guardrail_summary,
            "guardrail_details": guardrail_details,
        }

    def to_bigquery(
        self,
        df: Any,
        dataset_table: str,
        project_id: str | None = None,
        if_exists: str = "append",
    ) -> None:
        """Exports a pandas DataFrame to a Google BigQuery table."""
        target_project = project_id or self._get_project_id(self.app_name)
        df.to_gbq(
            destination_table=dataset_table,
            project_id=target_project,
            if_exists=if_exists,
            credentials=self.creds,
        )
        print(
            f"Successfully uploaded {len(df)} rows to "
            f"{target_project}.{dataset_table}"
        )

    def load_golden_eval_from_yaml(
        self, yaml_file_path: str, auto_sideload: bool = False
    ) -> dict[str, Any] | None:
        """Parses a YAML file and creates a Golden eval input from it.

        Supports two formats:
        1. A compressed YAML format matching
        tests/testdata/compressed_example.yaml
        2. A YAML format matching the YAML from export_app, e.g.
        tests/testdata/exported_eval_example.yaml

        Args:
            yaml_file_path: Path to the YAML file to be parsed.

        Returns:
            A dictionary matching the Golden Evaluation proto structure.
        """
        evals = self.load_golden_evals_from_yaml(yaml_file_path)
        return evals[0] if evals else None

    def load_golden_evals_from_yaml(
        self, yaml_file_path: str, auto_sideload: bool = False
    ) -> list[dict[str, Any]]:
        """Parses a YAML file and returns a list of Golden eval inputs.

        Similar to load_golden_eval_from_yaml, but returns all conversations
        found in a dataset format instead of just the first one.

        Args:
            yaml_file_path: Path to the YAML file to be parsed.

        Returns:
            A list of dictionaries matching the Golden Evaluation proto
                structure.
        """
        try:
            with open(yaml_file_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as e:
            logger.error("Failed to load YAML from %s: %s", yaml_file_path, e)
            return []

        if not data:
            return []

        base_dir = os.path.dirname(yaml_file_path)
        all_evals = []

        # Handle Dataset format (list of conversations)
        if "conversations" in data and isinstance(data["conversations"], list):
            dataset = Conversations.model_validate(data)
            for conversation in dataset.conversations:
                # Merge session parameters
                session_params = dataset.common_session_parameters.copy()
                session_params.update(conversation.session_parameters)

                # Extract basic info
                display_name = conversation.conversation

                # Process turns
                json_turns = []
                params_injected = False
                for turn in conversation.turns:
                    result = self._process_dataset_turn(
                        turn, session_params, params_injected
                    )
                    json_turns.append({"steps": result["steps"]})
                    params_injected = result["params_injected"]

                # Combine common and conversation-specific expectations
                expectations = (
                    dataset.common_expectations + conversation.expectations
                )
                tags = conversation.tags

                # Final processing of expectations (handles side-loading)
                eval_expectations = self._process_conversation_expectations(
                    expectations, base_dir=base_dir, auto_sideload=auto_sideload
                )

                all_evals.append(
                    {
                        "displayName": display_name,
                        "tags": tags,
                        "golden": {
                            "turns": json_turns,
                            "evaluationExpectations": eval_expectations,
                        },
                    }
                )

        # Handle Evaluation Resource or Direct Export format
        else:
            display_name = (
                data.get("displayName") or data.get("name") or "Imported_Eval"
            )
            tags = data.get("tags") or []
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",")]

            golden = data.get("golden", data)
            json_turns = []

            # If turns are already in JSON proto format (Case 1)
            if (
                "turns" in golden
                and golden["turns"]
                and "steps" in golden["turns"][0]
            ):
                json_turns = golden["turns"]
            # Otherwise process raw YAML turns (Case 1b)
            elif "turns" in golden:
                # A top-level variables block applies to the whole golden and
                # is staged once, on the first turn.
                root_params = _parse_variables_input(
                    golden.get("variables")
                    or golden.get("session_parameters")
                    or data.get("variables")
                    or data.get("session_parameters")
                )
                params_injected = False
                for t in golden["turns"]:
                    turn = Turn.model_validate(t)
                    result = self._process_dataset_turn(
                        turn,
                        session_params=root_params,
                        params_injected=params_injected,
                    )
                    json_turns.append({"steps": result["steps"]})
                    params_injected = result["params_injected"]

            expectations = (
                golden.get("evaluationExpectations")
                or data.get("expectations")
                or []
            )

            # Final processing of expectations (handles side-loading)
            eval_expectations = self._process_conversation_expectations(
                expectations, base_dir=base_dir, auto_sideload=auto_sideload
            )

            all_evals.append(
                {
                    "displayName": display_name,
                    "tags": tags,
                    "golden": {
                        "turns": json_turns,
                        "evaluationExpectations": eval_expectations,
                    },
                }
            )

        # Inject the file name as a tag
        file_tag = os.path.splitext(os.path.basename(yaml_file_path))[0]
        for eval_dict in all_evals:
            tags = eval_dict.get("tags", [])
            if not isinstance(tags, list):
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.split(",") if t.strip()]
                else:
                    tags = []
            if file_tag not in tags:
                tags.append(file_tag)
            eval_dict["tags"] = tags

        return all_evals

    def _process_conversation_expectations(
        self,
        expectations: list[Any],
        base_dir: str | None = None,
        auto_sideload: bool = False,
    ) -> list[str]:
        """Processes a list of conversation expectations, resolving prompt
        to resource names.

        If a string expectation matches a local 'evaluationExpectations/*.json'
        file, it resolves the prompt from that file first.
        """
        processed_expectations = []
        for exp in expectations:
            prompt_str = None
            display_name = None

            if isinstance(exp, str):
                prompt_str = exp
            elif isinstance(exp, dict):
                prompt_str = exp.get("prompt")
                display_name = exp.get("displayName")

                # Also handle exported YAML format
                if not prompt_str and "llmCriteria" in exp:
                    prompt_str = exp["llmCriteria"].get("prompt")

                if not prompt_str and "llm_criteria" in exp:
                    prompt_str = exp["llm_criteria"].get("prompt")

            if prompt_str:
                # Proactive side-loading: If it's a raw prompt and we have a
                # base_dir,
                # ensure it's saved to the local filesystem.
                if (
                    auto_sideload
                    and base_dir
                    and not prompt_str.startswith("projects/")
                ):
                    try:
                        exp_id = Common.sanitize_expectation_id(
                            display_name or prompt_str
                        )
                        eval_exp_dir = os.path.join(
                            base_dir, "evaluationExpectations"
                        )
                        os.makedirs(eval_exp_dir, exist_ok=True)

                        exp_filename = os.path.join(
                            eval_exp_dir, f"{exp_id}.json"
                        )
                        if not os.path.exists(exp_filename):
                            exp_content = {
                                "displayName": display_name or exp_id,
                                "llmCriteria": {"prompt": prompt_str},
                            }
                            with open(exp_filename, "w", encoding="utf-8") as f:
                                json.dump(exp_content, f, indent=2)
                            logger.info(
                                "Auto-sideloaded expectation to %s",
                                exp_filename,
                            )
                    except Exception as e:
                        logger.warning(
                            "Failed to auto-sideload expectation: %s", e
                        )

                # Find or create resource from prompt
                kwargs = {"llm_prompt": prompt_str}
                if display_name:
                    kwargs["display_name"] = display_name

                res_name = (
                    self.eval_client.find_or_create_evaluation_expectation(
                        **kwargs
                    )
                )
                processed_expectations.append(res_name)
            else:
                # Handle non-string expectations by appending string
                # representation
                logger.warning("Skipping non-string expectation: %s", exp)
                processed_expectations.append(str(exp))

        return processed_expectations

    def wait_for_run_and_get_results(
        self,
        run_name: str,
        timeout_seconds: int = 300,
        prefer_rest: bool = True,
    ) -> list[Any]:
        """Polls for completion of an evaluation run and returns results.

        Args:
            run_name: Name of the evaluation run.
            timeout_seconds: Max time to wait.
            prefer_rest: If True, attempts to fetch results via REST to preserve
                internal scenario milestone results.

        Returns:
            A list of evaluation results.
        """
        logger.info("Waiting for evaluation run %s to complete...", run_name)
        start_time = time.time()
        eval_result_names: list[str] = []
        while True:
            try:
                run_status = self.eval_client.get_evaluation_run(run_name)
                # The state can be a value the installed stubs don't define yet.
                # proto-plus then returns the raw int, which has no `.name`, so
                # compare by int value: anything that is not UNSPECIFIED (0) or
                # RUNNING (1) means the run is no longer in progress.
                state = run_status.state
                eval_result_names = list(
                    getattr(run_status, "evaluation_results", [])
                )
                try:
                    state_int = int(state)
                    is_finished = state_int not in (0, 1)
                    if is_finished and not hasattr(state, "name"):
                        logger.warning(
                            "Evaluation run %s ended in state %s, which this "
                            "client does not recognize. Treating it as "
                            "finished.",
                            run_name,
                            state_int,
                        )
                except (TypeError, ValueError):
                    state_name = getattr(state, "name", str(state))
                    is_finished = state_name in (
                        "COMPLETED",
                        "ERROR",
                        "CANCELLED",
                    )
            except Exception:
                if not prefer_rest:
                    raise
                run_status_dict = self.eval_client.get_evaluation_run_rest(
                    run_name
                )
                state_name = str(run_status_dict.get("state", ""))
                eval_result_names = list(
                    run_status_dict.get("evaluationResults", [])
                )
                is_finished = state_name in (
                    "COMPLETED",
                    "ERROR",
                    "CANCELLED",
                    "2",
                    "3",
                    "4",
                )
            if is_finished:
                break
            if time.time() - start_time > timeout_seconds:
                raise TimeoutError(f"Evaluation run {run_name} timed out.")
            time.sleep(5)

        if prefer_rest and eval_result_names:
            results = []
            for r_name in eval_result_names:
                try:
                    res = self.eval_client.get_evaluation_result_rest(r_name)
                except Exception:
                    res = self.eval_client.get_evaluation_result(r_name)
                results.append(res)
            return results

        return self.eval_client.list_evaluation_results_by_run(run_name)

    def create_and_run_evaluation_from_yaml(
        self,
        yaml_file_path: str,
        app_name: str | None = None,
        modality: str = "text",
        run_count: int | None = None,
    ) -> dict[str, Any]:
        """Loads, creates, and runs an evaluation from a YAML file.

        Args:
            yaml_file_path: Path to the YAML file.
            app_name: Optional parent App ID. Defaults to self.app_name.
            modality: "text" (default) or "audio".
            run_count: Number of times to run the evaluation. Default is 1
                per golden, 5 per scenario.

        Returns:
            A dictionary containing the evaluation and the run response.
        """
        app_name = app_name or self.app_name
        if not app_name:
            raise ValueError("app_name is required.")

        # 1. Load the evaluation from YAML
        evaluation_dict = self.load_golden_eval_from_yaml(yaml_file_path)
        if not evaluation_dict:
            raise ValueError(f"Failed to load evaluation from {yaml_file_path}")

        display_name = evaluation_dict.get("displayName")
        if not display_name:
            raise ValueError("YAML evaluation missing displayName")

        # 2. Check if it already exists
        evals_map = self._get_or_load_evals_map(app_name)
        existing_resource_name = evals_map.get("goldens", {}).get(
            display_name
        ) or evals_map.get("scenarios", {}).get(display_name)

        if existing_resource_name:
            logger.info(
                "Found existing evaluation '%s' (%s), reusing it.",
                display_name,
                existing_resource_name,
            )
            # Update evaluation
            evaluation_dict["name"] = existing_resource_name
            evaluation_obj = self.update_evaluation(evaluation_dict)
        else:
            logger.info("Evaluation '%s' not found, creating it.", display_name)
            evaluation_obj = self.create_evaluation(
                evaluation=evaluation_dict, app_name=app_name
            )
            logger.info("Created evaluation: %s", evaluation_obj.name)

        # Run the evaluation using the resource name
        run_response = self.run_evaluation(
            evaluations=[evaluation_obj.name],
            app_name=app_name,
            modality=modality,
            run_count=run_count,
        )

        logger.info("Started evaluation run: %s", run_response.operation.name)

        return {
            "evaluation": evaluation_obj,
            "run": run_response,
        }


def evaluate_expectations(
    gemini_client: Any,
    model_name: str,
    trace: list[str],
    expectations: list[Any],
    audio_paths: dict[int, str] | None = None,
) -> list[ExpectationResult]:
    """Evaluates expectations against the conversation trace using an LLM.

    Args:
        gemini_client: The GenAI client instance.
        model_name: The Gemini model name to use.
        trace: A list of strings representing the conversation trace.
        expectations: A list of expectations (strings or dictionaries).
        audio_paths: Optional dictionary mapping simulation turn numbers to
          audio WAV file paths.

    Returns:
        A list of ExpectationResult objects.
    """
    prompt_expectations = []
    requires_audio = False

    for exp in expectations:
        if isinstance(exp, dict):
            title = exp.get("title") or exp.get("name") or "Expectation"
            text = exp.get("expectation", "")
            prompt_expectations.append(f"{title}: {text}")
            if exp.get("requires_audio_paths", False):
                requires_audio = True
        elif hasattr(exp, "expectation"):
            title = getattr(exp, "title", "Expectation")
            text = getattr(exp, "expectation", str(exp))
            prompt_expectations.append(f"{title}: {text}")
            if getattr(exp, "requires_audio_paths", False):
                requires_audio = True
        else:
            prompt_expectations.append(str(exp))

    trace_text = (
        "Refer to the conversation history and interleaved raw audio "
        "files below."
        if (audio_paths and requires_audio)
        else "\n\n".join(trace)
    )

    prompt_text = llm_user_prompts.EVALUATE_EXPECTATIONS_PROMPT.replace(
        "{trace}", trace_text
    ).replace("{expectations}", json.dumps(prompt_expectations, indent=2))

    if audio_paths and requires_audio:
        contents = [prompt_text, "\n\nCONVERSATION TRACE AND RAW AUDIO:\n"]

        for i, item in enumerate(trace):
            contents.append(f"\n{item}\n")

            # Interleave audio for agent turns (odd indices of detailed_trace)
            if i % 2 == 1:
                sim_turn = (i - 1) // 2
                audio_path = audio_paths.get(sim_turn)
                if audio_path and os.path.exists(audio_path):
                    try:
                        with open(audio_path, "rb") as f:
                            contents.append(
                                genai.types.Part.from_bytes(
                                    data=f.read(), mime_type="audio/wav"
                                )
                            )
                            logger.info(
                                "Interleaved audio %s for turn %s",
                                audio_path,
                                sim_turn,
                            )
                    except Exception as e:
                        logger.error(
                            "Failed to read or wrap audio file %s: %s",
                            audio_path,
                            e,
                        )
        prompt = contents
    else:
        prompt = prompt_text

    try:
        output: ExpectationOutput = gemini_client.generate(
            prompt=prompt,
            model_name=model_name,
            response_mime_type="application/json",
            response_schema=ExpectationOutput,
        )
        if output:
            return output.results
        return []
    except Exception as e:
        logging.getLogger(__name__).error(f"Error evaluating expectations: {e}")
        return []


def add_timestamp_suffix(filename: str, timestamp: str | None) -> str:
    """Appends a timestamp suffix to a filename, preserving the extension.

    Args:
        filename: The original filename (e.g., 'results.json').
        timestamp: The timestamp string to append (e.g., '20260622_171403').

    Returns:
        The filename with the timestamp appended (e.g.,
        'results_20260622_171403.json'), or the original filename if timestamp
        is None or empty.
    """
    if not timestamp:
        return filename
    base, ext = os.path.splitext(filename)
    return f"{base}_{timestamp}{ext}"
