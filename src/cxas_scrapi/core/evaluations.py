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


"""Core Evaluations class for CXAS Scrapi."""

import base64
import concurrent.futures
import contextlib
import hashlib
import json
import os
import time
import typing
from enum import Enum
from typing import Any

import yaml
from google.auth.transport.requests import AuthorizedSession
from google.cloud.ces_v1beta import (
    AgentServiceClient,
    EvaluationServiceClient,
    types,
)
from google.protobuf import field_mask_pb2, json_format

from cxas_scrapi.core.agents import Agents
from cxas_scrapi.core.common import DEFAULT_API_ENDPOINT, Common
from cxas_scrapi.core.tools import Tools


class ExportFormat(Enum):
    YAML = "yaml"
    JSON = "json"


class Evaluations(Common):
    def __init__(
        self, app_name: str, env: str = "PROD", **kwargs: typing.Any
    ) -> None:
        """Initializes the Evaluations client.

        Args:
            app_name: CXAS App ID
                (projects/{project}/locations/{location}/apps/{app}).
            env: Environment override (default: PROD).
        """
        # Pass app_name to Common for client_options determination
        super().__init__(app_name=app_name, **kwargs)

        self.app_name = app_name

        # Parse project and location from app_name using Common helpers
        self.project_id = self._get_project_id(app_name)
        self.location = self._get_location(app_name)

        # Initialize SDK Client
        self.client = EvaluationServiceClient(
            transport=self.get_grpc_transport(EvaluationServiceClient),
            client_info=self.client_info,
        )
        self.resource_type = "evaluations"
        self.evals_map: dict[str, dict[str, str]] = {}
        self._eval_search_index: dict[str, str] = {}

    def _get_rest_session(self) -> AuthorizedSession:
        """Returns an authorized requests Session for REST calls."""
        session = AuthorizedSession(self.creds)
        if (
            os.environ.get("GOOGLE_API_USE_CLIENT_CERTIFICATE", "").lower()
            == "true"
        ):
            with contextlib.suppress(Exception):
                session.configure_mtls_channel()
        return session

    def _get_rest_base_url(self) -> str:
        """Returns base REST URL, respecting client_options or
        DEFAULT_API_ENDPOINT."""
        if isinstance(self.client_options, dict):
            endpoint = (
                self.client_options.get("api_endpoint") or DEFAULT_API_ENDPOINT
            )
        else:
            endpoint = (
                getattr(self.client_options, "api_endpoint", None)
                or DEFAULT_API_ENDPOINT
            )
        if (
            os.environ.get("GOOGLE_API_USE_CLIENT_CERTIFICATE", "").lower()
            == "true"
            and endpoint == "ces.googleapis.com"
        ):
            endpoint = "ces.mtls.googleapis.com"
        if not endpoint.startswith("http://") and not endpoint.startswith(
            "https://"
        ):
            endpoint = f"https://{endpoint}"
        return endpoint.rstrip("/")

    def _get_rest_headers(self) -> dict[str, str]:
        """Standard headers for CES REST requests."""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": getattr(self, "user_agent", "cxas-scrapi"),
        }
        if getattr(self, "project_id", None):
            headers["X-Goog-User-Project"] = self.project_id
        return headers

    def get_evaluation_rest(self, evaluation_id: str) -> dict[str, Any]:
        """Fetches an evaluation via the REST endpoint (preserving scenario
        milestones)."""
        session = self._get_rest_session()
        base_url = self._get_rest_base_url()
        url = f"{base_url}/v1beta/{evaluation_id.lstrip('/')}"
        resp = session.get(url, headers=self._get_rest_headers())
        resp.raise_for_status()
        return resp.json()

    def get_evaluation_result_rest(
        self, evaluation_result_id: str
    ) -> dict[str, Any]:
        """Fetches an evaluation result via REST endpoint (preserving
        scenarioResult)."""
        session = self._get_rest_session()
        base_url = self._get_rest_base_url()
        url = f"{base_url}/v1beta/{evaluation_result_id.lstrip('/')}"
        resp = session.get(url, headers=self._get_rest_headers())
        resp.raise_for_status()
        return resp.json()

    def list_evaluations_rest(
        self, app_name: str | None = None
    ) -> list[dict[str, Any]]:
        """Lists all evaluations in the given app via REST."""
        app_name = app_name or self.app_name
        if not app_name:
            raise ValueError("app_name is required.")
        session = self._get_rest_session()
        base_url = self._get_rest_base_url()
        evaluations: list[dict[str, Any]] = []
        page_token = ""
        while True:
            url = (
                f"{base_url}/v1beta/{app_name.lstrip('/')}"
                f"/evaluations?pageSize=100"
            )
            if page_token:
                url += f"&pageToken={page_token}"
            resp = session.get(url, headers=self._get_rest_headers())
            resp.raise_for_status()
            data = resp.json()
            evaluations.extend(data.get("evaluations", []))
            page_token = data.get("nextPageToken", "")
            if not page_token:
                break
        return evaluations

    def get_evaluation_run_rest(self, run_name: str) -> dict[str, Any]:
        """Fetches an evaluation run via the REST endpoint."""
        session = self._get_rest_session()
        base_url = self._get_rest_base_url()
        url = f"{base_url}/v1beta/{run_name.lstrip('/')}"
        resp = session.get(url, headers=self._get_rest_headers())
        resp.raise_for_status()
        return resp.json()

    def get_operation_rest(self, operation_name: str) -> dict[str, Any]:
        """Fetches a long-running operation via the REST endpoint."""
        session = self._get_rest_session()
        base_url = self._get_rest_base_url()
        url = f"{base_url}/v1beta/{operation_name.lstrip('/')}"
        resp = session.get(url, headers=self._get_rest_headers())
        resp.raise_for_status()
        return resp.json()

    def run_evaluation_rest(
        self,
        evaluations: list[str],
        app_name: str | None = None,
        modality: str = "text",
        run_count: int | None = None,
        golden_run_method: str = "STABLE",
        use_tool_fakes: bool | None = None,
    ) -> dict[str, Any]:
        """Triggers an evaluation run via the REST endpoint."""
        app_name = app_name or self.app_name
        if not app_name:
            raise ValueError("app_name is required.")
        session = self._get_rest_session()
        base_url = self._get_rest_base_url()
        url = f"{base_url}/v1beta/{app_name.lstrip('/')}:runEvaluation"
        config: dict[str, Any] = {
            "evaluationChannel": (
                "AUDIO" if modality.lower() == "audio" else "TEXT"
            )
        }
        if use_tool_fakes is True:
            config["toolCallBehaviour"] = "FAKE"
        elif use_tool_fakes is False:
            config["toolCallBehaviour"] = "REAL"
        body: dict[str, Any] = {
            "evaluations": list(evaluations),
            "config": config,
        }
        if run_count:
            body["runCount"] = run_count
        if golden_run_method:
            body["goldenRunMethod"] = golden_run_method.upper()
        resp = session.post(url, json=body, headers=self._get_rest_headers())
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def parse_eval_to_yaml(filepath: typing.Any) -> typing.Any:
        """Parses a CXAS Evaluation textproto file into the target FDE
        YAML format."""
        with open(filepath) as f:
            text = f.read()

        parsed = Common.parse_textproto(text)
        return Evaluations.eval_dict_to_yaml(parsed)

    @property
    def tools_map(self) -> dict[str, str]:
        """Lazily fetches and caches the tools map for resolving empty
        tool display names."""
        if getattr(self, "_tools_map", None) is None:
            try:
                self._tools_map = Tools(self.app_name).get_tools_map()
            except Exception as e:
                print(f"Warning: Failed to fetch tools map for resolution: {e}")
                self._tools_map = {}
        return self._tools_map

    @staticmethod
    def eval_dict_to_yaml(
        eval_dict: typing.Any, tools_map: dict[str, str] | None = None
    ) -> typing.Any:
        """Parses a CXAS Evaluation dictionary into the target FDE YAML
        format."""
        # Check if this is a Scenario Milestone Evaluation
        scenario = eval_dict.get("scenario") or {}
        milestones = (
            scenario.get("milestones") if isinstance(scenario, dict) else None
        )
        if not milestones and "milestones" in eval_dict:
            milestones = eval_dict["milestones"]

        if milestones:
            eval_name = (
                eval_dict.get("display_name")
                or eval_dict.get("displayName")
                or "Converted_Eval"
            )
            eval_entry: dict[str, Any] = {
                "name": eval_name,
                "steps": [],
            }
            tags = eval_dict.get("tags") or []
            if tags:
                eval_entry["tags"] = tags

            session_params = (
                eval_dict.get("session_parameters")
                or eval_dict.get("sessionParameters")
                or {}
            )
            if not session_params and isinstance(scenario, dict):
                session_params = (
                    scenario.get("session_parameters")
                    or scenario.get("sessionParameters")
                    or scenario.get("variable_overrides")
                    or scenario.get("variableOverrides")
                    or {}
                )
            if session_params:
                eval_entry["session_parameters"] = Common.unwrap_struct(
                    session_params
                )

            expectations = (
                eval_dict.get("expectations")
                or (
                    scenario.get("evaluation_expectations")
                    if isinstance(scenario, dict)
                    else None
                )
                or (
                    scenario.get("evaluationExpectations")
                    if isinstance(scenario, dict)
                    else None
                )
                or []
            )
            if expectations:
                eval_entry["expectations"] = list(dict.fromkeys(expectations))

            eval_max_turns = (
                eval_dict.get("max_turns")
                or eval_dict.get("maxTurns")
                or (
                    scenario.get("max_turns") or scenario.get("maxTurns")
                    if isinstance(scenario, dict)
                    else None
                )
            )
            if eval_max_turns:
                with contextlib.suppress(ValueError, TypeError):
                    eval_entry["max_turns"] = int(eval_max_turns)

            init_event = (
                eval_dict.get("initial_event")
                or eval_dict.get("initialEvent")
                or (
                    scenario.get("initial_event")
                    or scenario.get("initialEvent")
                    if isinstance(scenario, dict)
                    else None
                )
            )
            if init_event:
                eval_entry["initial_event"] = init_event

            eval_meta = eval_dict.get("metadata") or (
                scenario.get("metadata") if isinstance(scenario, dict) else None
            )
            if isinstance(eval_meta, dict) and eval_meta:
                eval_entry["metadata"] = Common.unwrap_struct(eval_meta)

            for m in milestones:
                if not isinstance(m, dict):
                    continue
                step: dict[str, Any] = {
                    "goal": m.get("goal", ""),
                    "success_criteria": m.get("success_criteria")
                    or m.get("successCriteria", ""),
                }
                resp_guide = (
                    m.get("response_guide")
                    or m.get("responseGuide")
                    or m.get("protocol")
                )
                if resp_guide:
                    step["response_guide"] = resp_guide
                max_t = (
                    m.get("max_turns")
                    or m.get("max_turn_count")
                    or m.get("maxTurnCount")
                )
                if max_t:
                    step["max_turns"] = int(max_t)
                stat_utt = m.get("static_utterance") or m.get("staticUtterance")
                if stat_utt:
                    step["static_utterance"] = stat_utt
                evt = m.get("event")
                if evt:
                    step["event"] = evt
                inj_var = m.get("inject_variables") or m.get("injectVariables")
                if inj_var:
                    step["inject_variables"] = Common.unwrap_struct(inj_var)
                step_meta = m.get("metadata")
                if isinstance(step_meta, dict) and step_meta:
                    step["metadata"] = Common.unwrap_struct(step_meta)

                eval_entry["steps"].append(step)

            out_yaml: dict[str, Any] = {}
            common_params = eval_dict.get(
                "common_session_parameters"
            ) or eval_dict.get("commonSessionParameters")
            if common_params:
                out_yaml["common_session_parameters"] = Common.unwrap_struct(
                    common_params
                )
            out_yaml["evals"] = [eval_entry]
            return out_yaml

        golden = eval_dict.get("golden", {})
        turns = golden.get("turns", [])
        if not isinstance(turns, list):
            turns = [turns]

        conversation_entry = {
            "conversation": eval_dict.get("display_name", "Converted_Eval"),
            "turns": [],
            "expectations": [],
            "mocks": [],
        }

        tags = eval_dict.get("tags", [])
        if tags:
            conversation_entry["tags"] = tags

        session_params = {}
        id_to_tool = {}

        for turn in turns:
            steps = turn.get("steps", [])
            if not isinstance(steps, list):
                steps = [steps]

            current_turn = {}
            for step in steps:
                if "user_input" in step:
                    ui = step["user_input"]

                    if "variables" in ui:
                        session_params.update(ui["variables"])

                    if "text" in ui:
                        # Whenever we see userInput[text], it's the start
                        # of a new turn
                        if current_turn:
                            conversation_entry["turns"].append(current_turn)
                            current_turn = {}
                        current_turn["user"] = ui["text"]
                    elif "event" in ui:
                        event = ui["event"]
                        event_str = (
                            event.get("event", str(event))
                            if isinstance(event, dict)
                            else str(event)
                        )
                        current_turn["user_event"] = event_str

                if "expectation" in step:
                    exp = step["expectation"]
                    if "agent_response" in exp:
                        ar = exp["agent_response"]
                        chunks = ar.get("chunks", [])
                        if not isinstance(chunks, list):
                            chunks = [chunks]
                        text = " ".join(
                            [c.get("text", "") for c in chunks if "text" in c]
                        )
                        # If we already have an agent response, convert to
                        # list or append
                        if "agent" in current_turn:
                            if isinstance(current_turn["agent"], list):
                                current_turn["agent"].append(text)
                            else:
                                current_turn["agent"] = [
                                    current_turn["agent"],
                                    text,
                                ]
                        else:
                            current_turn["agent"] = text

                    if "agent_transfer" in exp:
                        at = exp["agent_transfer"]
                        target_agent = at.get("target_agent", "")
                        if "tool_calls" not in current_turn:
                            current_turn["tool_calls"] = []
                        current_turn["tool_calls"].append(
                            {
                                "action": "transfer_to_agent",
                                "agent": target_agent,
                            }
                        )

                    if "tool_call" in exp:
                        tc = exp["tool_call"]
                        args = tc.get("args", {})
                        unwrapped_args = Common.unwrap_struct(args)
                        display_name = tc.get("display_name", "")

                        if not display_name and tools_map:
                            display_name = tools_map.get(tc.get("tool", ""), "")

                        if not display_name:
                            display_name = tc.get("tool", "")

                        if "tool_calls" not in current_turn:
                            current_turn["tool_calls"] = []
                        current_turn["tool_calls"].append(
                            {
                                "action": display_name,
                                "args": unwrapped_args,
                            }
                        )
                        id_to_tool[tc.get("id", "")] = display_name

                    if "tool_response" in exp:
                        tr = exp["tool_response"]
                        res = tr.get("response", {})
                        unwrapped_res = Common.unwrap_struct(res)
                        tool_name = id_to_tool.get(tr.get("id", ""), "")

                        if not tool_name and tools_map:
                            tool_name = tools_map.get(tr.get("tool", ""), "")

                        if not tool_name:
                            tool_name = tr.get("tool", "")

                        conversation_entry["mocks"].append(
                            {"tool": tool_name, "response": unwrapped_res}
                        )

            # End of source turn: append whatever we have in current_turn
            if current_turn:
                conversation_entry["turns"].append(current_turn)

        # Include golden evaluation expectations
        exp_refs = golden.get("evaluation_expectations", [])
        if not isinstance(exp_refs, list):
            exp_refs = [exp_refs]
        conversation_entry["expectations"].extend(exp_refs)
        conversation_entry["expectations"] = list(
            dict.fromkeys(conversation_entry["expectations"])
        )

        if session_params:
            unwrapped_params = Common.unwrap_struct(session_params)
            conversation_entry["session_parameters"] = unwrapped_params

        # Handle common session parameters
        common_session_params = eval_dict.get("session_parameters", {})
        if not common_session_params:
            common_session_params = golden.get("session_parameters", {})

        out_yaml = {}
        if common_session_params:
            out_yaml["common_session_parameters"] = Common.unwrap_struct(
                common_session_params
            )

        out_yaml["conversations"] = [conversation_entry]

        return out_yaml

    @staticmethod
    def process_export_operation(export_op: Any) -> bytes | None:
        """Processes the export operation and returns app content bytes.

        Args:
            export_op: The operation object from export_app (or result).

        Returns:
            bytes: The app content bytes if successful, None otherwise.
        """
        try:
            # Check if it has .result(), otherwise assume it's the
            # response or operation
            if hasattr(export_op, "result"):
                export_response = export_op.result()
            else:
                export_response = export_op
        except Exception as e:
            # logger is not defined in this class scope usually, but we
            # can import it or just pass
            # We'll use a local logger or just print if absolutely needed,
            # but user asked to remove prints.
            # Ideally we log via a module logger.
            print(
                f"Export operation result() failed or not an LRO: "
                f"{e}. Checking if it returned response directly."
            )
            export_response = export_op

        # The SDK returns ExpectAppResponse which has app_content
        if export_response and hasattr(export_response, "app_content"):
            app_content_bytes = export_response.app_content
            # Removed print statements as requested

            # Optional: We could still log what we found if we had a logger,
            # but strictly removing prints.
            # verify it's a valid zip by opening it?

            return app_content_bytes
        else:
            return None

    def list_evaluations(
        self, app_name: str | None = None
    ) -> list[types.Evaluation]:
        """Lists evaluations within a specific app.

        Args:
            app_name: Parent App ID. Defaults to self.app_name.
        """
        app_name = app_name or self.app_name
        if not app_name:
            raise ValueError("app_name is required.")

        request = types.ListEvaluationsRequest(parent=app_name)
        response = self.client.list_evaluations(request=request)
        return list(response)

    def list_evaluation_results(
        self, evaluation_display_name: str
    ) -> list[types.EvaluationResult]:
        """Fetches all evaluation results for a specific evaluation.

        Args:
            evaluation_display_name: Full resource name or display name of
                                     the evaluation
        """
        evaluation_name = evaluation_display_name
        if "/evaluations/" not in evaluation_name:
            if not getattr(self, "app_name", None):
                raise ValueError(
                    "app_name must be set to look up evaluations by "
                    "display name."
                )
            evals_map = self._get_or_load_evals_map(self.app_name)

            if evaluation_name in evals_map.get("goldens", {}):
                evaluation_name = evals_map["goldens"][evaluation_name]
            elif evaluation_name in evals_map.get("scenarios", {}):
                evaluation_name = evals_map["scenarios"][evaluation_name]
            else:
                raise ValueError(
                    f"No evaluation found with display name: "
                    f"'{evaluation_name}'"
                )

        request = types.ListEvaluationResultsRequest(parent=evaluation_name)
        response = self.client.list_evaluation_results(request=request)
        return list(response)

    def get_evaluation_result(
        self, evaluation_result_id: str
    ) -> types.EvaluationResult:
        """Fetches the FULL payload for a single evaluation result."""
        request = types.GetEvaluationResultRequest(name=evaluation_result_id)
        return self.client.get_evaluation_result(request=request)

    def get_evaluation_run(self, evaluation_run_id: str) -> types.EvaluationRun:
        """Gets details of the specified evaluation run by its full
        resource name.

        Args:
            evaluation_run_id: Full resource name of the evaluation run.
        """
        request = types.GetEvaluationRunRequest(name=evaluation_run_id)
        return self.client.get_evaluation_run(request=request)

    def list_evaluation_results_by_run(
        self, evaluation_run_id: str
    ) -> list[types.EvaluationResult]:
        """Fetches all evaluation results associated with a specific
        evaluation run.

        Args:
            evaluation_run_id: Full resource name of the evaluation run.
        """
        if "/evaluationRuns/" not in evaluation_run_id:
            raise ValueError(
                f"Invalid evaluation_run_id format: {evaluation_run_id}"
            )

        run_status = self.client.get_evaluation_run(name=evaluation_run_id)
        result_names = list(run_status.evaluation_results or [])
        if not result_names:
            return []
        if len(result_names) == 1:
            return [self.client.get_evaluation_result(name=result_names[0])]

        max_workers = min(16, len(result_names))
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers
        ) as executor:
            return list(
                executor.map(
                    lambda rname: self.client.get_evaluation_result(name=rname),
                    result_names,
                )
            )

    def build_search_index(
        self, app_name: str | None = None, force: bool = False
    ) -> None:
        """Builds a JSON string index of all evaluations for fast searching.

        Args:
            app_name: Parent App ID. Defaults to self.app_name.
            force: If True, rebuilds the index even if already built.
        """
        app_name = app_name or self.app_name
        if not force and self._eval_search_index:
            return

        evaluations = self.list_evaluations(app_name)
        self._eval_search_index = {}

        for eval_obj in evaluations:
            # Convert to dictionary and then to JSON string
            eval_dict = type(eval_obj).to_dict(eval_obj)
            # Dump to string and convert to lowercase for case-insensitive
            # searching
            self._eval_search_index[eval_obj.display_name] = json.dumps(
                eval_dict
            ).lower()

    def search_evaluations(
        self,
        app_name: str,
        tools: list[str] | None = None,
        variables: list[str] | None = None,
        agents: list[str] | None = None,
        rebuild_index: bool = False,
    ) -> list[str]:
        """Searches querying evaluations and filters by connected tools,
        variables, or agents.

        Args:
            app_name: Parent App ID.
            tools: List of tool display names to search for.
            variables: List of variable names to search for.
            agents: List of agent display names to search for.
            rebuild_index: If True, forcefully rebuilds the search index.

        Returns:
            List of Evaluation display names that match the search criteria.
        """
        search_terms = []

        if tools:
            tools_client = Tools(app_name=app_name, creds=self.creds)
            tools_map = tools_client.get_tools_map(reverse=True)
            for tool_name in tools:
                if tool_name in tools_map:
                    # Append the resource ID name in lowercase
                    search_terms.append(tools_map[tool_name].lower())
                else:
                    raise ValueError(f"Tool '{tool_name}' not found in App.")

        if agents:
            agents_client = Agents(app_name=app_name, creds=self.creds)
            agents_map = agents_client.get_agents_map(reverse=True)
            for agent_name in agents:
                if agent_name in agents_map:
                    # Append the resource ID name in lowercase
                    search_terms.append(agents_map[agent_name].lower())
                else:
                    raise ValueError(f"Agent '{agent_name}' not found in App.")

        if variables:
            for var_name in variables:
                search_terms.append(var_name.lower())

        if not search_terms:
            raise ValueError(
                "Must provide at least one search term (tools, variables, "
                "or agents)."
            )

        self.build_search_index(app_name, force=rebuild_index)

        matched_evals = []
        for eval_name, eval_str in self._eval_search_index.items():
            # Check if all search terms are in the evaluation JSON string
            if all(term in eval_str for term in search_terms):
                matched_evals.append(eval_name)

        return matched_evals

    def get_evaluations_map(
        self, app_name: str | None = None, reverse: bool = False
    ) -> dict[str, dict[str, str]]:
        """Creates a map of Evaluation full names to display names,
        grouped by type.

        Returns a dictionary with 'goldens' and 'scenarios' keys, each
        containing a sub-dictionary of the mappings.

        Args:
            app_name: Parent App ID. Defaults to self.app_name.
            reverse: If True, map display_name -> name.
        """
        app_name = app_name or self.app_name

        try:
            evaluations = self.list_evaluations(app_name)
        except Exception:
            evaluations = self.list_evaluations_rest(app_name)
        evaluations_dict: dict[str, dict[str, str]] = {
            "goldens": {},
            "scenarios": {},
        }

        for evaluation in evaluations:
            if isinstance(evaluation, dict):
                display_name = evaluation.get("displayName") or evaluation.get(
                    "display_name"
                )
                name = evaluation.get("name")
                is_golden = (
                    "golden" in evaluation and evaluation["golden"] is not None
                )
                is_scenario = (
                    "scenario" in evaluation
                    and evaluation["scenario"] is not None
                )
            else:
                display_name = evaluation.display_name
                name = evaluation.name
                is_golden = bool(getattr(evaluation, "golden", None))
                is_scenario = bool(getattr(evaluation, "scenario", None))
                if (
                    not is_golden
                    and not is_scenario
                    and hasattr(evaluation, "_pb")
                ):
                    with contextlib.suppress(Exception):
                        if evaluation._pb.HasField("golden"):
                            is_golden = True
                        elif evaluation._pb.HasField("scenario"):
                            is_scenario = True

            if display_name and name:
                target_dict = None
                if is_golden:
                    target_dict = evaluations_dict["goldens"]
                elif is_scenario:
                    target_dict = evaluations_dict["scenarios"]

                if target_dict is not None:
                    if reverse:
                        target_dict[display_name] = name
                    else:
                        target_dict[name] = display_name

        return evaluations_dict

    def _get_or_load_evals_map(
        self, app_name: str | None = None
    ) -> dict[str, dict[str, str]]:
        """Gets a map of reverse evaluations from cache or loads it if
        missing."""
        if not self.evals_map:
            self.evals_map = self.get_evaluations_map(app_name, reverse=True)
        return self.evals_map

    def get_evaluation(self, evaluation_id: str) -> types.Evaluation:
        """Gets a specific evaluation."""
        request = types.GetEvaluationRequest(name=evaluation_id)
        return self.client.get_evaluation(request=request)

    def export_evaluation(
        self,
        evaluation_id: str,
        output_format: ExportFormat = ExportFormat.YAML,
        output_path: str | None = None,
    ) -> str:
        """
        Fetches a specific evaluation and exports it to the specified format.

        Args:
            evaluation_id: Full resource name of the evaluation.
            output_format: Output format. Defaults to ExportFormat.YAML.
            output_path: Optional local path to write the exported evaluation.
                If provided, evaluation expectations are sideloaded as JSON
                files.

        Returns:
            A string containing the formatted output.
        """
        if isinstance(output_format, str):
            try:
                output_format = ExportFormat(output_format.lower())
            except ValueError:
                print(
                    f"Warning: Invalid output_format '{output_format}'. "
                    f"Using YAML."
                )
                output_format = ExportFormat.YAML

        eval_dict = None
        try:
            eval_obj = self.get_evaluation(evaluation_id=evaluation_id)
            eval_dict = (
                eval_obj
                if isinstance(eval_obj, dict)
                else type(eval_obj).to_dict(eval_obj)
            )
        except Exception:
            eval_dict = None

        has_golden_turns = (
            isinstance(eval_dict, dict)
            and isinstance(eval_dict.get("golden"), dict)
            and "turns" in eval_dict["golden"]
        )
        has_milestones = isinstance(eval_dict, dict) and (
            "milestones" in eval_dict
            or (
                isinstance(eval_dict.get("scenario"), dict)
                and "milestones" in eval_dict["scenario"]
            )
        )
        if not has_golden_turns and not has_milestones:
            with contextlib.suppress(Exception):
                rest_dict = self.get_evaluation_rest(
                    evaluation_id=evaluation_id
                )
                if isinstance(rest_dict, dict) and rest_dict:
                    eval_dict = rest_dict

        if not isinstance(eval_dict, dict):
            eval_dict = {}

        out_dict = self.eval_dict_to_yaml(eval_dict, tools_map=self.tools_map)

        # Resolve expectation resource names to LLM prompts and optionally
        # sideload for conversations and evals
        entries_to_resolve = []
        if "conversations" in out_dict:
            entries_to_resolve.extend(out_dict["conversations"])
        if "evals" in out_dict:
            entries_to_resolve.extend(out_dict["evals"])

        for entry in entries_to_resolve:
            if "expectations" not in entry or not entry["expectations"]:
                continue

            resolved_prompts = []
            for exp_ref in entry["expectations"]:
                # Only resolve if it looks like a resource name
                if "/evaluationExpectations/" in exp_ref:
                    try:
                        exp_obj = self.get_evaluation_expectation(exp_ref)

                        prompt_text = None
                        if (
                            hasattr(exp_obj, "llm_criteria")
                            and exp_obj.llm_criteria
                        ):
                            prompt_text = getattr(
                                exp_obj.llm_criteria, "prompt", None
                            )

                        if prompt_text:
                            resolved_prompts.append(prompt_text)

                            # If output_path is provided, sideload as JSON
                            if output_path:
                                base_dir = os.path.dirname(output_path)
                                eval_exp_dir = os.path.join(
                                    base_dir, "evaluationExpectations"
                                )
                                exp_id = Common.sanitize_expectation_id(
                                    prompt_text
                                )

                                os.makedirs(eval_exp_dir, exist_ok=True)
                                exp_filename = os.path.join(
                                    eval_exp_dir, f"{exp_id}.json"
                                )

                                exp_display_name = (
                                    getattr(exp_obj, "display_name", None)
                                    or exp_id
                                )
                                exp_content = {
                                    "displayName": exp_display_name,
                                    "llmCriteria": {"prompt": prompt_text},
                                }
                                with open(
                                    exp_filename, "w", encoding="utf-8"
                                ) as f:
                                    json.dump(exp_content, f, indent=2)

                        else:
                            # Fallback to original ref if nothing extracted
                            resolved_prompts.append(exp_ref)

                    except Exception as e:
                        # Fallback to resource name if resolution fails
                        print(
                            f"Failed to fetch evaluation expectation "
                            f"'{exp_ref}': {e}"
                        )
                        resolved_prompts.append(exp_ref)
                else:
                    # Not a resource name, keep as is
                    resolved_prompts.append(exp_ref)

            entry["expectations"] = resolved_prompts

        if output_format == ExportFormat.JSON:
            content = json.dumps(out_dict, indent=2)
        else:
            # Fallback to YAML for all other formats
            content = yaml.dump(out_dict, sort_keys=False, allow_unicode=True)

        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(content)

        return content

    def create_evaluation(
        self,
        evaluation: types.Evaluation | dict[str, Any],
        app_name: str | None = None,
    ) -> types.Evaluation:
        """Creates an evaluation.

        Args:
            evaluation: The Evaluation object or dict to create.
            app_name: Parent App ID. Defaults to self.app_name.
        """
        app_name = app_name or self.app_name
        if not app_name:
            raise ValueError("app_name is required.")

        if isinstance(evaluation, dict):
            # Use json_format to parse dict to message to handle
            # camelCase/snake_case
            eval_message = types.Evaluation()
            # Parse into the underlying protobuf message
            json_format.ParseDict(
                evaluation, eval_message._pb, ignore_unknown_fields=True
            )
            evaluation = eval_message

        request = types.CreateEvaluationRequest(
            parent=app_name, evaluation=evaluation
        )
        return self.client.create_evaluation(request=request)

    @staticmethod
    def _has_tool_expectations(eval_dict: dict[str, Any]) -> bool:
        golden = eval_dict.get("golden") or eval_dict
        for turn in golden.get("turns") or []:
            for step in turn.get("steps") or []:
                exp = step.get("expectation") or {}
                if (
                    "toolCall" in exp
                    or "tool_call" in exp
                    or "toolResponse" in exp
                    or "tool_response" in exp
                ):
                    return True
        return False

    def resolve_evaluation_tool_references(
        self,
        evaluation: dict[str, Any],
        app_name: str | None = None,
        tools_reverse_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Resolves tool display names to tool resource names in-place."""
        if not isinstance(evaluation, dict) or not self._has_tool_expectations(
            evaluation
        ):
            return evaluation

        app_name = app_name or self.app_name
        if tools_reverse_map is None:
            try:
                tools_reverse_map = Tools(
                    app_name=app_name, creds=self.creds
                ).get_tools_map(reverse=True)
            except Exception:  # noqa: BLE001
                tools_reverse_map = {}

        if not tools_reverse_map:
            return evaluation

        valid_resources = set(tools_reverse_map.values())
        golden = evaluation.get("golden") or evaluation
        for turn in golden.get("turns") or []:
            for step in turn.get("steps") or []:
                exp = step.get("expectation") or {}
                for key in (
                    "toolCall",
                    "tool_call",
                    "toolResponse",
                    "tool_response",
                ):
                    tc = exp.get(key)
                    if not isinstance(tc, dict):
                        continue
                    tool_ref = tc.get("tool", "")
                    if tool_ref in valid_resources:
                        continue
                    dname = (
                        tc.get("displayName")
                        or tc.get("display_name")
                        or (
                            tool_ref.split("/")[-1]
                            if "/" in str(tool_ref)
                            else str(tool_ref)
                        )
                    )
                    if dname and dname in tools_reverse_map:
                        tc["tool"] = tools_reverse_map[dname]
        return evaluation

    def update_evaluation(
        self,
        evaluation: types.Evaluation | dict[str, Any],
        app_name: str | None = None,
        replace: bool = False,
    ) -> types.Evaluation:
        """Updates an evaluation. If it doesn't exist, it creates it.

        Automatically falls back to delete + create when the platform returns
        501 Not Implemented on UpdateEvaluation (or when ``replace=True``).

        Args:
            evaluation: The Evaluation object or dict to update.
            app_name: Parent App ID. Defaults to self.app_name.
            replace: If True, deletes any existing evaluation with the same
                display_name and recreates it via CreateEvaluation.
        """
        app_name = app_name or self.app_name
        if not app_name:
            raise ValueError("app_name is required.")

        # Convert dict to types.Evaluation if needed
        if isinstance(evaluation, dict):
            if self._has_tool_expectations(evaluation):
                self.resolve_evaluation_tool_references(
                    evaluation, app_name=app_name
                )
            # Use json_format to parse dict to message to handle
            # camelCase/snake_case
            eval_message = types.Evaluation()
            # Parse into the underlying protobuf message
            json_format.ParseDict(
                evaluation, eval_message._pb, ignore_unknown_fields=True
            )
            evaluation = eval_message

        # If name is missing, try to find it by display name
        if not evaluation.name:
            existing_evals = self.list_evaluations(app_name)
            for existing in existing_evals:
                if existing.display_name == evaluation.display_name:
                    evaluation.name = existing.name
                    break

        # If still no name, it's a new evaluation, call create instead
        if not evaluation.name:
            print(
                f"Evaluation '{evaluation.display_name}' not found. "
                f"Creating it instead."
            )
            return self.create_evaluation(
                evaluation=evaluation, app_name=app_name
            )

        if replace:
            existing_name = evaluation.name
            self.delete_evaluation(name=existing_name, force=True)
            evaluation.name = ""
            return self.create_evaluation(
                evaluation=evaluation, app_name=app_name
            )

        print(f"Updating existing evaluation: {evaluation.name}")
        request = types.UpdateEvaluationRequest(evaluation=evaluation)
        try:
            return self.client.update_evaluation(request=request)
        except Exception as exc:
            err_text = str(exc).upper()
            if (
                "501" in err_text
                or "UNIMPLEMENTED" in err_text
                or "NOT IMPLEMENTED" in err_text
            ):
                existing_name = evaluation.name
                self.delete_evaluation(name=existing_name, force=True)
                evaluation.name = ""
                return self.create_evaluation(
                    evaluation=evaluation, app_name=app_name
                )
            raise

    def sync_evaluations(
        self,
        evaluations: list[types.Evaluation | dict[str, Any]],
        app_name: str | None = None,
        prune: bool = False,
        prune_tag: str | None = None,
        resolve_tools: bool = True,
        dry_run: bool = False,
    ) -> dict[str, list[str]]:
        """Synchronizes evaluations by display_name via delete+create.

        Resolves tool display names to server-minted tool resource IDs and
        optionally prunes stale remote evaluations whose display_name is not
        present in ``evaluations``.
        """
        app_name = app_name or self.app_name
        if not app_name:
            raise ValueError("app_name is required.")

        tools_reverse_map: dict[str, str] | None = None
        if resolve_tools and any(
            isinstance(ev, dict) and self._has_tool_expectations(ev)
            for ev in evaluations
        ):
            try:
                tools_reverse_map = Tools(
                    app_name=app_name, creds=self.creds
                ).get_tools_map(reverse=True)
            except Exception:  # noqa: BLE001
                tools_reverse_map = {}

        existing_list = list(self.list_evaluations(app_name) or [])
        existing_by_display: dict[str, list[str]] = {}
        for item in existing_list:
            if getattr(item, "display_name", None) and getattr(
                item, "name", None
            ):
                existing_by_display.setdefault(item.display_name, []).append(
                    item.name
                )

        pushed: list[str] = []
        suite_names: set[str] = set()

        for item in evaluations:
            if isinstance(item, dict):
                payload = dict(item)
                if resolve_tools:
                    self.resolve_evaluation_tool_references(
                        payload,
                        app_name=app_name,
                        tools_reverse_map=tools_reverse_map,
                    )
                dname = (
                    payload.get("displayName")
                    or payload.get("display_name")
                    or ""
                )
                payload.pop("name", None)
            else:
                payload = item
                dname = getattr(item, "display_name", "") or ""

            if dname:
                suite_names.add(dname)

            if dry_run:
                pushed.append(dname)
                continue

            for old_name in existing_by_display.get(dname, []):
                self.delete_evaluation(name=old_name, force=True)

            if not isinstance(payload, dict) and getattr(payload, "name", None):
                payload.name = ""

            created = self.create_evaluation(
                evaluation=payload, app_name=app_name
            )
            pushed.append(getattr(created, "display_name", "") or dname)

        pruned: list[str] = []
        if prune and not dry_run:
            for item in existing_list:
                dname = getattr(item, "display_name", "") or ""
                if dname in suite_names:
                    continue
                if prune_tag:
                    item_tags = list(getattr(item, "tags", []) or [])
                    if prune_tag not in item_tags:
                        continue
                self.delete_evaluation(name=item.name, force=True)
                pruned.append(dname or item.name)

        return {"pushed": pushed, "pruned": pruned}

    def delete_evaluation(self, name: str, force: bool = False) -> None:
        """Deletes an evaluation.

        Args:
            name: Full resource name of the evaluation.
            force: If True, deletes even if referenced by datasets.
        """
        request = types.DeleteEvaluationRequest(name=name, force=force)
        self.client.delete_evaluation(request=request)

    def run_evaluation(
        self,
        evaluations: str | list[str] | None = None,
        eval_type: str | None = None,
        app_name: str | None = None,
        modality: str = "text",
        run_count: int | None = None,
        golden_run_method: str = "STABLE",
        use_tool_fakes: bool | None = None,
    ) -> Any:
        """Runs an evaluation on the specified app.

        Args:
            evaluations: A single display name or a list of display names
                to run.
            eval_type: Run a specific type of evaluation. Must be one of:
                      'goldens', 'scenarios', or 'all'.
            app_name: Parent App ID. Defaults to self.app_name.
            modality: "text" (default) or "audio".
            run_count: Number of times to run the evaluation. Default is 1
                per golden, 5 per scenario.
            golden_run_method: Golden run replay strategy (STABLE or NAIVE).
            use_tool_fakes: If True, executes with mock/fake tool behaviour.
                If False, executes with real tool behaviour. Defaults to None.
        """
        app_name = app_name or self.app_name
        if not app_name:
            raise ValueError("app_name is required.")

        if not evaluations and not eval_type:
            raise ValueError(
                "Must provide either 'evaluations' (display names) or "
                "'eval_type' ('goldens'/'scenarios'/'all')."
            )

        resolved_names = set()
        need_map = bool(eval_type)
        if evaluations and not need_map:
            eval_list = (
                [evaluations] if isinstance(evaluations, str) else evaluations
            )
            if not all(
                isinstance(e, str)
                and e.startswith("projects/")
                and "/evaluations/" in e
                for e in eval_list
            ):
                need_map = True

        evals_map = self._get_or_load_evals_map(app_name) if need_map else {}

        # Handle explicit evaluation display names
        if evaluations:
            if isinstance(evaluations, str):
                evaluations = [evaluations]

            for display_name in evaluations:
                if (
                    display_name.startswith("projects/")
                    and "/evaluations/" in display_name
                ):
                    resolved_names.add(display_name)
                    continue

                # Check both goldens and scenarios for this name
                resource_name = evals_map.get("goldens", {}).get(
                    display_name
                ) or evals_map.get("scenarios", {}).get(display_name)

                if resource_name:
                    resolved_names.add(resource_name)
                else:
                    raise ValueError(
                        f"Evaluation display name not found: '{display_name}'"
                    )

        # Handle explicit evaluation types
        if eval_type:
            eval_type = eval_type.lower()
            if eval_type == "goldens":
                resolved_names.update(evals_map.get("goldens", {}).values())
            elif eval_type == "scenarios":
                resolved_names.update(evals_map.get("scenarios", {}).values())
            elif eval_type == "all":
                resolved_names.update(evals_map.get("goldens", {}).values())
                resolved_names.update(evals_map.get("scenarios", {}).values())
            else:
                raise ValueError(
                    f"Invalid eval_type: '{eval_type}'. Must be 'goldens', "
                    f"'scenarios', or 'all'."
                )

        if not resolved_names:
            raise ValueError(
                "No matching evaluation resource names found to run."
            )

        request = types.RunEvaluationRequest(
            app=app_name, evaluations=list(resolved_names)
        )

        if run_count:
            request.run_count = run_count

        if golden_run_method:
            method_enum = getattr(
                types.GoldenRunMethod, golden_run_method.upper(), None
            )
            if method_enum is not None:
                request.golden_run_method = method_enum
            else:
                raise ValueError(
                    f"Invalid golden_run_method: {golden_run_method}"
                )

        if modality.lower() == "audio":
            request.config.evaluation_channel = (
                types.EvaluationConfig.EvaluationChannel.AUDIO
            )

        if use_tool_fakes is True:
            request.config.tool_call_behaviour = (
                types.EvaluationToolCallBehaviour.FAKE
            )
        elif use_tool_fakes is False:
            request.config.tool_call_behaviour = (
                types.EvaluationToolCallBehaviour.REAL
            )

        return self.client.run_evaluation(request=request)

    def import_evaluations(
        self,
        app_name: str | None = None,
        gcs_uri: str | None = None,
        csv_content: bytes | None = None,
        conversations: list[str] | None = None,
        yaml_content: bytes | str | None = None,
        conflict_strategy: int | str = 0,
    ) -> Any:
        """Imports evaluations into the app.

        Args:
            app_name: Parent App ID. Defaults to self.app_name.
            gcs_uri: The GCS URI to import from (gs://...).
            csv_content: Raw bytes representing the csv file.
            conversations: A list of conversation resource names.
            yaml_content: YAML content string or bytes (for milestone or
                conversations import via REST).
            conflict_strategy: See
                types.ImportEvaluationsRequest.ImportOptions.ConflictResolutionStrategy
                               (0=UNSPECIFIED, 1=OVERWRITE, 2=SKIP, 3=DUPLICATE,
                               or string names like 'OVERWRITE').
        """
        app_name = app_name or self.app_name
        if not app_name:
            raise ValueError("app_name is required.")

        if yaml_content is not None:
            if isinstance(yaml_content, str):
                raw_bytes = yaml_content.encode("utf-8")
            else:
                raw_bytes = yaml_content
            b64_content = base64.b64encode(raw_bytes).decode("utf-8")

            strategy_str = None
            if isinstance(conflict_strategy, str):
                s_upper = conflict_strategy.upper()
                if s_upper in (
                    "OVERWRITE",
                    "SKIP",
                    "DUPLICATE",
                    "CONFLICT_RESOLUTION_STRATEGY_UNSPECIFIED",
                ):
                    strategy_str = s_upper
                elif s_upper == "UNSPECIFIED":
                    strategy_str = "CONFLICT_RESOLUTION_STRATEGY_UNSPECIFIED"
            elif isinstance(conflict_strategy, int):
                strat_map = {
                    0: "CONFLICT_RESOLUTION_STRATEGY_UNSPECIFIED",
                    1: "OVERWRITE",
                    2: "SKIP",
                    3: "DUPLICATE",
                }
                strategy_str = strat_map.get(conflict_strategy)

            body: dict[str, Any] = {"yamlContent": b64_content}
            if (
                strategy_str
                and strategy_str != "CONFLICT_RESOLUTION_STRATEGY_UNSPECIFIED"
            ):
                body["importOptions"] = {
                    "conflictResolutionStrategy": strategy_str
                }

            session = self._get_rest_session()
            base_url = self._get_rest_base_url()
            url = f"{base_url}/v1beta/{app_name.lstrip('/')}:importEvaluations"

            resp = session.post(
                url, json=body, headers=self._get_rest_headers()
            )
            resp.raise_for_status()
            op_data = resp.json()

            if (
                isinstance(op_data, dict)
                and "name" in op_data
                and not op_data.get("done", False)
            ):
                op_name = op_data["name"]
                poll_url = f"{base_url}/v1beta/{op_name.lstrip('/')}"
                poll_start = time.time()
                while not op_data.get("done", False):
                    if time.time() - poll_start > 300:
                        raise TimeoutError(
                            f"Import evaluations operation timed out: {op_name}"
                        )
                    time.sleep(2)
                    poll_resp = session.get(
                        poll_url, headers=self._get_rest_headers()
                    )
                    poll_resp.raise_for_status()
                    op_data = poll_resp.json()

            if isinstance(op_data, dict):
                if "error" in op_data:
                    raise RuntimeError(
                        f"Import evaluations failed: {op_data['error']}"
                    )
                resp_body = op_data.get("response")
                if isinstance(resp_body, dict):
                    err_msgs = (
                        resp_body.get("errorMessages")
                        or resp_body.get("error_messages")
                        or []
                    )
                    fail_cnt = (
                        resp_body.get("importFailureCount")
                        or resp_body.get("import_failure_count")
                        or 0
                    )
                    imported_evals = resp_body.get("evaluations") or []
                    if (err_msgs or fail_cnt) and not imported_evals:
                        msg = (
                            "; ".join(str(m) for m in err_msgs)
                            if err_msgs
                            else f"importFailureCount={fail_cnt}"
                        )
                        raise RuntimeError(f"Import evaluations failed: {msg}")

            # Invalidate cached evals
            self.evals_map = {}
            self._eval_search_index = {}
            return op_data

        request = types.ImportEvaluationsRequest(parent=app_name)

        if gcs_uri:
            request.gcs_uri = gcs_uri
        elif csv_content:
            request.csv_content = csv_content
        elif conversations:
            request.conversation_list = (
                types.ImportEvaluationsRequest.ConversationList(
                    conversations=conversations
                )
            )
        else:
            raise ValueError(
                "Must provide one of: gcs_uri, csv_content, conversations, "
                "or yaml_content."
            )

        if conflict_strategy:
            strategy_val = conflict_strategy
            if isinstance(strategy_val, str):
                strategy_val = getattr(
                    types.ImportEvaluationsRequest.ImportOptions.ConflictResolutionStrategy,
                    strategy_val.upper(),
                    0,
                )
            request.import_options = (
                types.ImportEvaluationsRequest.ImportOptions(
                    conflict_resolution_strategy=strategy_val
                )
            )

        return self.client.import_evaluations(request=request)

    def list_evaluation_expectations(
        self, app_name: str | None = None
    ) -> list[types.EvaluationExpectation]:
        """Lists all evaluation expectations in the given app.

        Args:
            app_name: Parent App ID. Defaults to self.app_name.
        """
        app_name = app_name or self.app_name
        if not app_name:
            raise ValueError("app_name is required.")

        request = types.ListEvaluationExpectationsRequest(parent=app_name)
        response = self.client.list_evaluation_expectations(request=request)
        return list(response)

    def get_evaluation_expectation(
        self, name: str
    ) -> types.EvaluationExpectation:
        """Gets details of the specified evaluation expectation.

        Args:
            name: Full resource name of the evaluation expectation.
        """
        request = types.GetEvaluationExpectationRequest(name=name)
        return self.client.get_evaluation_expectation(request=request)

    def create_evaluation_expectation(
        self,
        evaluation_expectation: types.EvaluationExpectation | dict[str, Any],
        app_name: str | None = None,
    ) -> types.EvaluationExpectation:
        """Creates an evaluation expectation.

        Args:
            evaluation_expectation: The EvaluationExpectation object or
                dict to create.
            app_name: Parent App ID. Defaults to self.app_name.
        """
        app_name = app_name or self.app_name
        if not app_name:
            raise ValueError("app_name is required.")

        if isinstance(evaluation_expectation, dict):
            evaluation_expectation = types.EvaluationExpectation(
                **evaluation_expectation
            )

        request = types.CreateEvaluationExpectationRequest(
            parent=app_name, evaluation_expectation=evaluation_expectation
        )
        return self.client.create_evaluation_expectation(request=request)

    def update_evaluation_expectation(
        self,
        evaluation_expectation: types.EvaluationExpectation,
        update_mask: field_mask_pb2.FieldMask | None = None,
    ) -> types.EvaluationExpectation:
        """Updates an evaluation expectation.

        Args:
            evaluation_expectation: The EvaluationExpectation to update.
            update_mask: Optional mask defining which fields to update.
        """
        request = types.UpdateEvaluationExpectationRequest(
            evaluation_expectation=evaluation_expectation,
            update_mask=update_mask,
        )
        return self.client.update_evaluation_expectation(request=request)

    def delete_evaluation_expectation(self, name: str) -> None:
        """Deletes an evaluation expectation.

        Args:
            name: Full resource name of the evaluation expectation.
        """
        request = types.DeleteEvaluationExpectationRequest(name=name)
        self.client.delete_evaluation_expectation(request=request)

    def get_evaluation_expectation_by_display_name(
        self, display_name: str, app_name: str | None = None
    ) -> types.EvaluationExpectation | None:
        """Gets an evaluation expectation by its display name.

        Args:
            display_name: The display name of the evaluation expectation.
            app_name: Parent App ID. Defaults to self.app_name.
        """
        app_name = app_name or self.app_name
        expectations = self.list_evaluation_expectations(app_name=app_name)
        for exp in expectations:
            if exp.display_name == display_name:
                return exp
        return None

    def find_or_create_evaluation_expectation(
        self, llm_prompt: str, display_name: str | None = None
    ) -> str:
        """Finds or creates an evaluation expectation from an LLM prompt.

        Args:
            llm_prompt: The prompt/criteria for the evaluation expectation.
            display_name: Optional display name. If not provided, a hash of the
                prompt is used.

        Returns:
            The full resource name of the evaluation expectation.

        Raises:
            ValueError: If an expectation with the same display name exists
                but with a different prompt.
        """
        if not display_name:
            # Generate a stable hash of the prompt for the display name
            display_name = (
                f"eval_exp_{hashlib.md5(llm_prompt.encode()).hexdigest()[:8]}"
            )

        existing_exp = self.get_evaluation_expectation_by_display_name(
            display_name=display_name
        )

        if existing_exp:
            if existing_exp.llm_criteria.prompt != llm_prompt:
                raise ValueError(
                    f"Evaluation expectation '{display_name}' already exists "
                    "with a different prompt."
                )
            return existing_exp.name

        # Create new expectation
        new_exp = types.EvaluationExpectation(
            display_name=display_name,
            llm_criteria=types.EvaluationExpectation.LlmCriteria(
                prompt=llm_prompt
            ),
        )
        created_exp = self.create_evaluation_expectation(
            evaluation_expectation=new_exp
        )
        return created_exp.name

    def get_evaluation_thresholds(
        self, app_name: str | None = None, print_console: bool = False
    ) -> dict[str, Any]:
        """Gets the evaluation metrics thresholds for the app.

        Args:
            app_name: Parent App ID. Defaults to self.app_name.
            print_console: If True, prints a formatted summary of the
                           settings to the console.

        Returns:
            A dictionary containing the evaluation metrics thresholds,
            with any enums resolved to their string representations.
        """
        app_name = app_name or self.app_name
        if not app_name:
            raise ValueError("app_name is required.")

        agent_client = AgentServiceClient(
            credentials=self.creds,
            client_options=self.client_options,
            client_info=self.client_info,
        )

        request = types.GetAppRequest(name=app_name)
        app_obj = agent_client.get_app(request=request)

        # Convert the app protobuf to a dictionary, forcing enums to strings
        app_dict = json_format.MessageToDict(
            app_obj._pb,
            preserving_proto_field_name=True,
            use_integers_for_enums=False,
        )

        thresholds = app_dict.get("evaluation_metrics_thresholds", {})

        if print_console:
            print("===== GLOBAL Settings =====")
            hallucination_behavior = thresholds.get(
                "hallucination_metric_behavior", "UNSPECIFIED"
            )
            print(f"Hallucinations: {hallucination_behavior}")

            print("\n===== GOLDEN Settings =====")
            golden_hallucination_behavior = thresholds.get(
                "golden_hallucination_metric_behavior", "UNSPECIFIED"
            )
            print(f"Hallucinations: {golden_hallucination_behavior}")

            golden = thresholds.get("golden_evaluation_metrics_thresholds", {})
            turn_level = golden.get("turn_level_metrics_thresholds", {})
            expectation_level = golden.get(
                "expectation_level_metrics_thresholds", {}
            )

            divisors = {
                "semantic_similarity_success_threshold": "/4",
                "overall_tool_invocation_correctness_threshold": "/1.0",
                "tool_invocation_parameter_correctness_threshold": "/1.0",
            }

            if turn_level:
                print("\n### Turn Level Metrics ###")
                for k, v in turn_level.items():
                    suffix = divisors.get(k, "")
                    print(f"- {k}: {v}{suffix}")
            if expectation_level:
                print("\n### Expectation Level Metrics ###")
                for k, v in expectation_level.items():
                    suffix = divisors.get(k, "")
                    print(f"- {k}: {v}{suffix}")

            print("\n===== SCENARIO Settings =====")
            scenario_hallucination_behavior = thresholds.get(
                "scenario_hallucination_metric_behavior", "UNSPECIFIED"
            )
            print(f"Hallucinations: {scenario_hallucination_behavior}")
            print("\n")

        return thresholds

    def bulk_export_evals(
        self,
        eval_type: str,
        output_dir: str,
        output_format: ExportFormat = ExportFormat.YAML,
    ) -> None:
        """Exports all evaluations of a specific type to a local directory.

        Args:
            eval_type: Type of evaluation ('goldens' or 'scenarios').
            output_dir: Local directory path to export files into.
            output_format: The format to export in (YAML or JSON).
        """

        print("Fetching evaluations map...")
        evals_map = self.get_evaluations_map(
            app_name=self.app_name, reverse=True
        )

        if eval_type not in ["goldens", "scenarios"]:
            raise ValueError(
                "eval_type must be either 'goldens' or 'scenarios'."
            )

        target_evals = evals_map.get(eval_type, {})

        if not target_evals:
            print(f"No {eval_type} found in the specified App.")
            return

        # Ensure the <output_dir>/evals directory exists
        evals_dir = os.path.join(output_dir, "evals")
        os.makedirs(evals_dir, exist_ok=True)

        print(
            f"Found {len(target_evals)} {eval_type}. "
            f"Starting export to {evals_dir}..."
        )

        success_count = 0
        for display_name, resource_id in target_evals.items():
            try:
                # Clean display name to make a safe filename
                safe_name = "".join(
                    c if c.isalnum() or c in ("_", "-") else "_"
                    for c in display_name
                )
                ext = "json" if output_format == ExportFormat.JSON else "yaml"
                file_path = os.path.join(evals_dir, f"{safe_name}.{ext}")

                # Export the eval with sideloading of evaluation expectations.
                self.export_evaluation(
                    resource_id,
                    output_format=output_format,
                    output_path=file_path,
                )

                print(f"✅ Exported: {safe_name}.{ext}")
                success_count += 1
            except Exception as e:
                print(f"❌ Failed to export '{display_name}': {e}")

        print(
            f"\nDone! Successfully exported "
            f"{success_count}/{len(target_evals)} {eval_type}."
        )
