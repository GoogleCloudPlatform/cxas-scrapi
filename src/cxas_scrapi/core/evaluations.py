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

import hashlib
import json
import os
from enum import Enum
from typing import Any, Dict, List, Optional, Union

import yaml
from google.cloud.ces_v1beta import (
    AgentServiceClient,
    EvaluationServiceClient,
    types,
)
from google.protobuf import field_mask_pb2, json_format

from cxas_scrapi.core.agents import Agents
from cxas_scrapi.core.common import Common
from cxas_scrapi.core.tools import Tools


class ExportFormat(Enum):
    YAML = "yaml"
    JSON = "json"


class Evaluations(Common):
    def __init__(self, app_name: str, env: str = "PROD", **kwargs):
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
            credentials=self.creds,
            client_options=self.client_options,
            client_info=self.client_info,
        )
        self.resource_type = "evaluations"
        self.evals_map: Dict[str, Dict[str, str]] = {}
        self._eval_search_index: Dict[str, str] = {}

    @staticmethod
    def parse_eval_to_yaml(filepath):
        """Parses a CXAS Evaluation textproto file into the target FDE
        YAML format."""
        with open(filepath, "r") as f:
            text = f.read()

        parsed = Common.parse_textproto(text)
        return Evaluations.eval_dict_to_yaml(parsed)

    @property
    def tools_map(self) -> Dict[str, str]:
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
        eval_dict, tools_map: Optional[Dict[str, str]] = None
    ):
        """Parses a CXAS Evaluation dictionary into the target FDE YAML
        format."""
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
    def process_export_operation(export_op) -> Optional[bytes]:
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
        self, app_name: Optional[str] = None
    ) -> List[types.Evaluation]:
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
    ) -> List[types.EvaluationResult]:
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
    ) -> List[types.EvaluationResult]:
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

        results = []
        for result_name in run_status.evaluation_results:
            results.append(self.client.get_evaluation_result(name=result_name))

        return results

    def build_search_index(
        self, app_name: Optional[str] = None, force: bool = False
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
        tools: Optional[List[str]] = None,
        variables: Optional[List[str]] = None,
        agents: Optional[List[str]] = None,
        rebuild_index: bool = False,
    ) -> List[str]:
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
        self, app_name: Optional[str] = None, reverse: bool = False
    ) -> Dict[str, Dict[str, str]]:
        """Creates a map of Evaluation full names to display names,
        grouped by type.

        Returns a dictionary with 'goldens' and 'scenarios' keys, each
        containing a sub-dictionary of the mappings.

        Args:
            app_name: Parent App ID. Defaults to self.app_name.
            reverse: If True, map display_name -> name.
        """
        app_name = app_name or self.app_name

        evaluations = self.list_evaluations(app_name)
        evaluations_dict: Dict[str, Dict[str, str]] = {
            "goldens": {},
            "scenarios": {},
        }

        for evaluation in evaluations:
            display_name = evaluation.display_name
            name = evaluation.name

            if display_name and name:
                target_dict = None
                # Check the oneof field or structure property to determine
                # the type
                if getattr(evaluation, "golden", None):
                    target_dict = evaluations_dict["goldens"]
                elif getattr(evaluation, "scenario", None):
                    target_dict = evaluations_dict["scenarios"]

                if target_dict is not None:
                    if reverse:
                        target_dict[display_name] = name
                    else:
                        target_dict[name] = display_name

        return evaluations_dict

    def _get_or_load_evals_map(
        self, app_name: Optional[str] = None
    ) -> Dict[str, Dict[str, str]]:
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
        output_path: Optional[str] = None,
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

        eval_obj = self.get_evaluation(evaluation_id=evaluation_id)
        # Convert the protobuf object to a python dictionary
        eval_dict = type(eval_obj).to_dict(eval_obj)

        out_dict = self.eval_dict_to_yaml(eval_dict, tools_map=self.tools_map)

        # Resolve expectation resource names to LLM prompts and optionally
        # sideload
        for conv in out_dict.get("conversations", []):
            if "expectations" not in conv:
                continue

            resolved_prompts = []
            for exp_ref in conv["expectations"]:
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

                                exp_content = {
                                    "displayName": exp_id,
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

            conv["expectations"] = resolved_prompts

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
        evaluation: Union[types.Evaluation, Dict[str, Any]],
        app_name: Optional[str] = None,
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

    def update_evaluation(
        self,
        evaluation: Union[types.Evaluation, Dict[str, Any]],
        app_name: Optional[str] = None,
    ) -> types.Evaluation:
        """Updates an evaluation. If it doesn't exist, it creates it.

        Args:
            evaluation: The Evaluation object or dict to update.
            app_name: Parent App ID. Defaults to self.app_name.
        """
        app_name = app_name or self.app_name
        if not app_name:
            raise ValueError("app_name is required.")

        # Convert dict to types.Evaluation if needed
        if isinstance(evaluation, dict):
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

        print(f"Updating existing evaluation: {evaluation.name}")
        request = types.UpdateEvaluationRequest(evaluation=evaluation)
        return self.client.update_evaluation(request=request)

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
        evaluations: Optional[Union[str, List[str]]] = None,
        eval_type: Optional[str] = None,
        app_name: Optional[str] = None,
        modality: str = "text",
        run_count: Optional[int] = None,
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
        evals_map = self._get_or_load_evals_map(app_name)

        # Handle explicit evaluation display names
        if evaluations:
            if isinstance(evaluations, str):
                evaluations = [evaluations]

            for display_name in evaluations:
                # Check both goldens and scenarios for this name
                resource_name = evals_map.get("goldens", {}).get(
                    display_name
                ) or evals_map.get("scenarios", {}).get(display_name)

                if resource_name:
                    resolved_names.add(resource_name)
                elif (
                    display_name.startswith("projects/")
                    and "/evaluations/" in display_name
                ):
                    resolved_names.add(display_name)
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

        if modality.lower() == "audio":
            request.config.evaluation_channel = (
                types.EvaluationConfig.EvaluationChannel.AUDIO
            )

        return self.client.run_evaluation(request=request)

    def import_evaluations(
        self,
        app_name: Optional[str] = None,
        gcs_uri: Optional[str] = None,
        csv_content: Optional[bytes] = None,
        conversations: Optional[List[str]] = None,
        conflict_strategy: int = 0,
    ) -> Any:
        """Imports evaluations into the app.

        Args:
            app_name: Parent App ID. Defaults to self.app_name.
            gcs_uri: The GCS URI to import from (gs://...).
            csv_content: Raw bytes representing the csv file.
            conversations: A list of conversation resource names.
            conflict_strategy: See
                types.ImportEvaluationsRequest.ImportOptions.ConflictResolutionStrategy
                               (0=UNSPECIFIED, 1=OVERWRITE, 2=SKIP, 3=DUPLICATE)
        """
        app_name = app_name or self.app_name
        if not app_name:
            raise ValueError("app_name is required.")

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
                "Must provide one of: gcs_uri, csv_content, or conversations."
            )

        if conflict_strategy:
            request.import_options = (
                types.ImportEvaluationsRequest.ImportOptions(
                    conflict_resolution_strategy=conflict_strategy
                )
            )

        return self.client.import_evaluations(request=request)

    def list_evaluation_expectations(
        self, app_name: Optional[str] = None
    ) -> List[types.EvaluationExpectation]:
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
        evaluation_expectation: Union[
            types.EvaluationExpectation, Dict[str, Any]
        ],
        app_name: Optional[str] = None,
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
        update_mask: Optional[field_mask_pb2.FieldMask] = None,
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
        self, display_name: str, app_name: Optional[str] = None
    ) -> Optional[types.EvaluationExpectation]:
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
        self, llm_prompt: str, display_name: Optional[str] = None
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
        self, app_name: Optional[str] = None, print_console: bool = False
    ) -> Dict[str, Any]:
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
