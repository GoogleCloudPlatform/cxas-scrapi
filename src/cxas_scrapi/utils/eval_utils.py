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

"""Utility functions for processing and exporting CXAS Evaluation Results."""

import enum
import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Union

import pandas as pd
import pydantic
import yaml
from pydantic import BaseModel

from cxas_scrapi.core.agents import Agents
from cxas_scrapi.core.common import Common
from cxas_scrapi.core.evaluations import Evaluations
from cxas_scrapi.core.tools import Tools
from cxas_scrapi.core.variables import Variables
from cxas_scrapi.prompts import llm_user_prompts
from cxas_scrapi.utils.latency_parser import LatencyParser

logger = logging.getLogger(__name__)


class ToolCall(BaseModel):
    action: str
    args: Dict[str, Any] = {}
    output: Optional[Union[str, Dict[str, Any]]] = None
    agent: Optional[str] = None


class Turn(BaseModel):
    user: Optional[str] = None
    agent: Optional[Union[str, List[str]]] = None
    tool_calls: List[ToolCall] = []


class Conversation(BaseModel):
    conversation: str
    expectations: List[str] = []
    tags: List[str] = []
    session_parameters: Dict[str, Any] = {}
    turns: List[Turn]


class Conversations(BaseModel):
    common_session_parameters: Dict[str, Any] = {}
    common_expectations: List[str] = []
    conversations: List[Conversation]


class EvalUtils(Evaluations):
    """Utility class for processing and exporting CXAS Evaluation Results."""

    def __init__(self, app_name: str, env: str = "PROD"):
        """Initializes the EvalUtils class for processing Evaluation Results.

        Args:
            app_name: CXAS App ID
                (projects/{project}/locations/{location}/apps/{app}).
            env: Environment override (default: PROD).
        """
        super().__init__(app_name=app_name, env=env)
        self.app_name = app_name
        self.tools_client = Tools(app_name=self.app_name, creds=self.creds)
        self.var_client = Variables(app_name=self.app_name, creds=self.creds)
        try:
            self.tool_map = self.tools_client.get_tools_map(reverse=True)
        except (AttributeError, KeyError, RuntimeError, ValueError) as e:
            logger.warning(
                "Failed to fetch tool map for %s: %s", self.app_name, e
            )
            self.tool_map = {}
        self.agents_client = Agents(app_name=self.app_name, creds=self.creds)
        try:
            self.agent_map = self.agents_client.get_agents_map(reverse=True)
        except (AttributeError, KeyError, RuntimeError, ValueError) as e:
            logger.warning(
                "Failed to fetch agent map for %s: %s", self.app_name, e
            )
        # Defer import to break circular dependency:
        # conversation_history -> latency_parser -> eval_utils
        # -> conversation_history
        from cxas_scrapi.core.conversation_history import (  # noqa: PLC0415
            ConversationHistory,
        )

        self.ch_client = ConversationHistory(
            app_name=self.app_name, creds=self.creds
        )
        self.eval_client = Evaluations(app_name=self.app_name, env=env)

    @staticmethod
    def parse_variables_input(v: Any) -> Dict[str, Any]:
        """Allows YAML to accept a list of strings OR a custom dictionary."""
        if v is None:
            return {}
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return {}
        if isinstance(v, list):
            # Convert list of names to a dict flagged for fetching (None)
            return {str(item): None for item in v}
        if isinstance(v, dict):
            return v
        return {}

    @staticmethod
    def _map_outcome(val):
        if isinstance(val, int):
            outcome_map = {0: "UNSPECIFIED", 1: "PASS", 2: "FAIL"}
            return outcome_map.get(val, f"UNKNOWN_{val}")
        return str(val) if val is not None else None

    @staticmethod
    def score_result_audio(result) -> bool:
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
    def _extract_tool_call_args(tool_call: Dict[str, Any]) -> Dict[str, Any]:
        """Extracts tool arguments from various possible alias keys."""
        for key in ["args", "arguments"]:
            if key in tool_call:
                return tool_call[key]
        return {}

    def _process_dataset_turn(
        self,
        turn: Turn,
        session_params: Dict[str, Any],
        params_injected: bool,
    ) -> Dict[str, Any]:
        """Processes a single turn from a conversation dataset into JSON steps.

        Returns:
            A tuple of (steps_list, updated_params_injected).
        """
        steps = []
        user_input_text = turn.user

        if not params_injected and session_params:
            steps.append({"userInput": {"variables": session_params}})
            params_injected = True

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
        results: Optional[Union[List[Any], str]] = None,
        eval_names: Optional[Union[List[str], str]] = None,
    ) -> tuple[
        List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]
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

        if results is None:
            results = []
            evaluations = self.list_evaluations(self.app_name)
            for evaluation in evaluations:
                if (
                    eval_names
                    and evaluation.display_name not in eval_names
                    and evaluation.name not in eval_names
                ):
                    continue
                results.extend(self.list_evaluation_results(evaluation.name))

        run_summaries = []
        expectations = []
        turns = []
        eval_cache = {}

        for result in results:
            res_dict = type(result).to_dict(result)
            result_name = res_dict.get("name", "")
            eval_name = "/".join(result_name.split("/")[:-2])
            display_name = "Unknown Evaluation"
            if eval_name:
                if eval_name not in eval_cache:
                    try:
                        eval_obj = self.get_evaluation(eval_name)
                        eval_cache[eval_name] = eval_obj.display_name
                    except (AttributeError, KeyError, RuntimeError):
                        eval_cache[eval_name] = "Unknown Evaluation"
                display_name = eval_cache[eval_name]

            raw_status = res_dict.get("evaluation_status", 0)
            if isinstance(raw_status, int):
                status_map = {0: "UNSPECIFIED", 1: "PASS", 2: "FAIL"}
                status_str = status_map.get(raw_status, f"UNKNOWN_{raw_status}")
            else:
                status_str = str(raw_status)

            golden = res_dict.get("golden_result", {})
            metrics = golden if golden else {}

            sem_score_raw = metrics.get("semantic_similarity_result", {}).get(
                "score"
            )
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
            tool_score_str = (
                f"{int(tool_score_raw * 100)}%"
                if isinstance(tool_score_raw, (int, float))
                else EvalUtils._map_outcome(tool_score_raw)
            )

            exec_state_raw = res_dict.get("execution_state", 0)
            if isinstance(exec_state_raw, int):
                exec_state_map = {
                    0: "UNSPECIFIED",
                    1: "RUNNING",
                    2: "COMPLETED",
                    3: "ERROR",
                }
                exec_state_str = exec_state_map.get(
                    exec_state_raw, f"UNKNOWN_{exec_state_raw}"
                )
            else:
                exec_state_str = str(exec_state_raw)

            base_info = {
                "display_name": display_name,
                "evaluation_run": res_dict.get("evaluation_run", ""),
                "eval_result_id": result_name,
                "evaluation_status": status_str,
                "execution_state": exec_state_str,
                "error_message": res_dict.get("error_info", {}).get(
                    "error_message", "Unknown Agent Exception"
                ),
                "semantic_score": sem_score_str,
                "tool_invocation_score": tool_score_str,
                "create_time": res_dict.get("create_time", ""),
                "update_time": res_dict.get("update_time", ""),
            }
            run_summaries.append(base_info)

            expectation_list = metrics.get(
                "expectation_results", []
            ) or metrics.get("evaluation_expectation_results", [])
            for exp_item in expectation_list:
                is_new_format = "prompt" in exp_item
                not_met_count = (
                    1
                    if is_new_format and exp_item.get("outcome") == 2
                    else exp_item.get("not_met_count", 0)
                )
                met_count = (
                    1
                    if is_new_format and exp_item.get("outcome") == 1
                    else exp_item.get("met_count", 0)
                )

                row = {
                    "display_name": display_name,
                    "eval_result_id": result_name,
                    "evaluation_run": base_info.get("evaluation_run"),
                    "evaluation_status": base_info.get("evaluation_status"),
                    "record_type": "summary_expectation",
                    "expectation": str(
                        exp_item.get("prompt", exp_item.get("expectation", ""))
                    ),
                    "met_count": met_count,
                    "not_met_count": not_met_count,
                    "met_percentage": exp_item.get("met_percentage", 0.0),
                    "not_met_percentage": exp_item.get(
                        "not_met_percentage", 0.0
                    ),
                    "explanation": str(exp_item.get("explanation", "")),
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
        results: Optional[Union[List[Any], str]] = None,
        eval_names: Optional[Union[List[str], str]] = None,
    ) -> Dict[str, Any]:
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
            try:
                outcomes = json.loads(outcomes_str)
            except (json.JSONDecodeError, TypeError):
                pass

            def _get_exp_act(outcome_obj):
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
                        e_dict["agent_transfer"].get(
                            "target_agent", "agent_transfer"
                        ),
                    )
                    f_type = "Routing / Agent"

                if "observed_agent_response" in outcome_obj:
                    chunks = outcome_obj["observed_agent_response"].get(
                        "chunks", []
                    )
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
                        outcome_obj["observed_agent_transfer"].get(
                            "target_agent", ""
                        ),
                    )

                return e_text, a_text, f_type

            sem_handled = False
            tool_handled = False

            # Process individual explicit expectation failures
            for outcome_obj in outcomes:
                e, a, f = _get_exp_act(outcome_obj)
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
                        e_text, a_text, _ = _get_exp_act(outcome_obj)
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
                        e_text, a_text, _ = _get_exp_act(outcome_obj)
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
        results: Optional[List[Any]] = None,
        eval_names: Optional[List[str]] = None,
        app_name: Optional[str] = None,
    ) -> Dict[str, pd.DataFrame]:
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
            def _aggregate(arr):
                if not arr:
                    return {"Average": 0, "p50": 0, "p90": 0, "p99": 0}
                ser = pd.Series(arr)
                return {
                    "Average": int(ser.mean()),
                    "p50": int(ser.quantile(0.50)),
                    "p90": int(ser.quantile(0.90)),
                    "p99": int(ser.quantile(0.99)),
                }

            t_agg = _aggregate(run_total_turn_latencies)
            tc_agg = _aggregate(run_tool_latencies)
            llm_agg = _aggregate(run_llm_latencies)
            gr_agg = _aggregate(run_guardrail_latencies)
            cb_agg = _aggregate(run_callback_latencies)

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
        project_id: Optional[str] = None,
        if_exists: str = "append",
    ):
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
    ) -> Optional[Dict[str, Any]]:
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
    ) -> List[Dict[str, Any]]:
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
            with open(yaml_file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (IOError, yaml.YAMLError) as e:
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
                for t in golden["turns"]:
                    turn = Turn.model_validate(t)
                    result = self._process_dataset_turn(
                        turn, session_params={}, params_injected=True
                    )
                    json_turns.append({"steps": result["steps"]})

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
        expectations: List[Any],
        base_dir: Optional[str] = None,
        auto_sideload: bool = False,
    ) -> List[str]:
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
    ) -> List[Dict[str, Any]]:
        """Polls for completion of an evaluation run and returns results.

        Args:
            run_name: Name of the evaluation run.
            timeout_seconds: Max time to wait.

        Returns:
            A list of evaluation results.
        """
        logger.info("Waiting for evaluation run %s to complete...", run_name)
        start_time = time.time()
        while True:
            run_status = self.eval_client.get_evaluation_run(run_name)
            if run_status.state.name in ["COMPLETED", "ERROR"]:
                break
            if time.time() - start_time > timeout_seconds:
                raise TimeoutError(f"Evaluation run {run_name} timed out.")
            time.sleep(10)

        results = self.eval_client.list_evaluation_results_by_run(run_name)
        return results

    def create_and_run_evaluation_from_yaml(
        self,
        yaml_file_path: str,
        app_name: Optional[str] = None,
        modality: str = "text",
        run_count: Optional[int] = None,
    ) -> Dict[str, Any]:
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


class ExpectationStatus(str, enum.Enum):
    MET = "Met"
    NOT_MET = "Not Met"


class ExpectationResult(pydantic.BaseModel):
    expectation: str
    status: ExpectationStatus = ExpectationStatus.NOT_MET
    justification: str = ""


class ExpectationOutput(pydantic.BaseModel):
    results: List[ExpectationResult] = []


def evaluate_expectations(
    gemini_client: Any,
    model_name: str,
    trace: List[str],
    expectations: List[str],
) -> List[ExpectationResult]:
    """Evaluates expectations against the conversation trace using an LLM.

    Args:
        genai_client: The GenAI client instance.
        model_name: The Gemini model name to use.
        trace: A list of strings representing the conversation trace.
        expectations: A list of strings representing the expectations.

    Returns:
        A list of ExpectationResult objects.
    """

    full_trace_str = "\n\n".join(trace)
    prompt = llm_user_prompts.EVALUATE_EXPECTATIONS_PROMPT.replace(
        "{trace}", full_trace_str
    )
    prompt = prompt.replace(
        "{expectations}", json.dumps(expectations, indent=2)
    )

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
