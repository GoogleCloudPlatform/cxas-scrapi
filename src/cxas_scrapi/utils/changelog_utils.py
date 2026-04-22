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

import datetime
import functools
import json
from typing import Any, Dict, List

from cxas_scrapi.utils.gemini import GeminiGenerate


class ChangelogUtils:
    @staticmethod
    def _get_nested_val(data: Dict, path: List[str], default=None):
        """Safely gets a value from a nested dictionary."""
        if not isinstance(data, dict):
            return default
        try:
            value = functools.reduce(
                lambda d, key: d.get(key) if isinstance(d, dict) else None,
                path,
                data,
            )
            return value if value is not None else default
        except TypeError:
            return default

    @staticmethod
    def _extract_relevant_parts(resource: Dict, resource_type: str) -> Dict:
        """Extracts key fields from a resource object for comparison."""
        if not resource:
            return {}

        parts = {}
        if resource_type == "App":
            fields_to_extract = [
                "displayName",
                "globalInstruction",
                "audioProcessingConfig",
                "loggingSettings",
                "guardrails",
                "variableDeclarations",
                "defaultChannelProfile",
                "languageSettings",
            ]
        elif resource_type == "Agent":
            fields_to_extract = [
                "displayName",
                "description",
                "instruction",
                "tools",
                "childAgents",
                "beforeModelCallbacks",
                "afterModelCallbacks",
                "beforeToolCallbacks",
                "afterToolCallbacks",
                "toolsets",
            ]
        elif resource_type == "Tool":
            fields_to_extract = [
                "displayName",
                "description",
                "pythonFunction",
                "googleSearchTool",
                "openApiTool",
                "executionType",  # Add other tool types if needed
            ]
        elif resource_type == "Guardrail":
            fields_to_extract = [
                "displayName",
                "description",
                "enabled",
                "action",
                "modelSafety",
                "contentFilter",
                "llmPromptSecurity",
                "llmPolicy",
            ]
        elif resource_type == "Deployment":
            fields_to_extract = [
                "displayName",
                "description",
                "agentVersion",
                "state",  # Add other relevant fields
            ]
        else:  # Fallback for unknown types
            fields_to_extract = ["displayName", "description"]

        for field in fields_to_extract:
            value = resource.get(field)
            if value is not None:
                # Simplify complex fields like lists of
                # callbacks/variables/tools/guardrails
                if field in ["tools", "guardrails", "childAgents"]:
                    parts[field] = sorted(
                        [item.split("/")[-1] for item in value]
                    )  # Just IDs
                elif field in ["variableDeclarations"]:
                    parts[field] = sorted(
                        [v.get("name") for v in value]
                    )  # Just names
                elif field in [
                    "beforeModelCallbacks",
                    "afterModelCallbacks",
                    "beforeToolCallbacks",
                    "afterToolCallbacks",
                ]:
                    # Represent callbacks by their description or index if
                    # description missing
                    parts[field] = sorted(
                        [
                            cb.get("description", f"callback_{i}")
                            for i, cb in enumerate(value)
                        ]
                    )
                elif isinstance(value, (dict, list)):
                    # Convert other complex types to compact JSON string for
                    # the prompt
                    try:
                        parts[field] = json.dumps(
                            value, sort_keys=True, separators=(",", ":")
                        )
                    except TypeError:
                        parts[field] = str(
                            value
                        )  # Fallback if not JSON serializable
                else:
                    parts[field] = value
        return parts

    @staticmethod
    def _format_changelog_for_prompt(changelog: Dict[str, Any]) -> str:
        """Formats changelog info, providing Original/New snippets for
        Updates."""
        action = changelog.get("action")
        resource_type = changelog.get("resourceType")
        display_name = changelog.get("displayName", "N/A")
        changelog_description = changelog.get("description", "")

        if action == "Update":
            original_resource = changelog.get("originalResource")
            new_resource = changelog.get("newResource")

            if original_resource and new_resource:
                original_parts = ChangelogUtils._extract_relevant_parts(
                    original_resource, resource_type
                )
                new_parts = ChangelogUtils._extract_relevant_parts(
                    new_resource, resource_type
                )

                # Format as compact JSON strings for the prompt
                try:
                    original_str = json.dumps(
                        original_parts, sort_keys=True, separators=(",", ":")
                    )
                    new_str = json.dumps(
                        new_parts, sort_keys=True, separators=(",", ":")
                    )
                except TypeError:
                    # Fallback if parts aren't serializable (shouldn't happen
                    # often)
                    original_str = str(original_parts)
                    new_str = str(new_parts)

                # Only include Original/New if they are different
                if original_str != new_str:
                    return (
                        f"- Action: Update, ResourceType: {resource_type}, "
                        f"Name: '{display_name}'\n"
                        f"  Original: {original_str}\n"
                        f"  New: {new_str}"
                    )
                else:
                    # If resources are identical despite 'Update' action, treat
                    # as no-op or minor internal change
                    # Return a generic format, but indicate it was an update
                    # context
                    return (
                        f"- Action: Update (No detected change), "
                        f"ResourceType: {resource_type}, "
                        f"Name: '{display_name}', "
                        f"Description: '{changelog_description}'"
                    )

        # Fallback for Create, Delete, or Updates where Original/New are missing
        return (
            f"- Action: {action}, ResourceType: {resource_type}, "
            f"Name: '{display_name}', Description: '{changelog_description}'"
        )

    @staticmethod
    def get_changelogs_since_last_version(
        all_changelogs: List[Dict[str, Any]], versions: List[Any]
    ) -> List[Dict[str, Any]]:
        """Retrieves changelogs created after the most recent app version."""
        if not versions:
            print("No versions found. Returning all changelogs.")
            return all_changelogs
        try:
            valid_versions = [
                v for v in versions if isinstance(v, dict) and "createTime" in v
            ]
            if not valid_versions:
                print(
                    "No valid versions with createTime found. Returning all "
                    "changelogs."
                )
                return all_changelogs

            latest_version = max(
                valid_versions, key=lambda version: version["createTime"]
            )
            latest_version_timestamp_str = latest_version["createTime"]
            print(
                f"Latest version was created at: {latest_version_timestamp_str}"
            )
            latest_version_dt = datetime.datetime.fromisoformat(
                latest_version_timestamp_str.replace("Z", "+00:00")
            )
            recent_changelogs = [
                cl
                for cl in all_changelogs
                if isinstance(cl, dict)
                and "createTime" in cl
                and datetime.datetime.fromisoformat(
                    cl["createTime"].replace("Z", "+00:00")
                )
                > latest_version_dt
            ]
            return recent_changelogs
        except (ValueError, KeyError, TypeError) as e:
            print(
                f"Error processing version or changelog timestamps: {e}. "
                f"Returning all changelogs."
            )
            return all_changelogs

    @staticmethod
    def summarize_changelogs(
        vertex_client_or_project: Any,
        changelogs: List[Dict[str, Any]],
        project_id: str = None,
    ) -> str:
        """Summarizes each non-evaluation changelog into a simple, specific
        one-liner."""
        resource_types_to_exclude = [
            "Version",
            "AppVersion",
            "Evaluation",
            "EvaluationRun",
        ]
        filtered_changelogs = [
            cl
            for cl in changelogs
            if cl.get("resourceType") not in resource_types_to_exclude
        ]

        if not filtered_changelogs:
            return "No user-facing changes to summarize."

        # Format each changelog entry, potentially producing multi-line
        # strings for updates
        formatted_log_entries = [
            ChangelogUtils._format_changelog_for_prompt(cl)
            for cl in filtered_changelogs
        ]

        # Combine and number the entries for the prompt
        changelog_context = ""
        entry_number = 1
        for entry in formatted_log_entries:
            # Add numbering only to the start of each logical entry
            lines = entry.strip().split("\n")
            changelog_context += f"{entry_number}. {lines[0]}\n"
            if len(lines) > 1:
                changelog_context += (
                    "\n".join([f"   {line}" for line in lines[1:]]) + "\n"
                )  # Indent Original/New
            entry_number += 1

        if (
            not changelog_context.strip()
        ):  # Check if context became empty after formatting
            return "No user-facing changes to summarize."

        prompt = f"""
        You are an AI assistant that analyzes technical log entries,
        specifically focusing on 'Update' actions by comparing 'Original' and
        'New' configuration snippets. Your goal is to generate a concise,
        single-line summary describing the *exact* change that occurred for
        each entry.

        Rules:
        - **CRITICAL**: Provide one summary line for each numbered entry in
          the input. Maintain a 1-to-1 correspondence.
        - For 'Update' entries with 'Original' and 'New' snippets: Compare
          them carefully to identify the precise difference. Describe only
          that specific change (e.g., "Disabled barge-in", "Added variable
          'X'", "Updated agent instructions", "Added tool 'Y'").
        - For 'Create' or 'Delete' entries (or 'Update' entries without
          Original/New comparison data): Generate a summary based on the
          Action, ResourceType, Name, and Description provided (e.g.,
          "Created tool 'get_weather'", "Deleted agent 'old_agent'").
        - Be specific. Avoid generic phrases like "Updated settings" or
          "Changed configuration". State *what* was updated.
        - The final output must be a bulleted list, starting each line with
          '-'.

        Here is the raw changelog data to summarize:
        ---
        {changelog_context}
        ---

        Provide the specific one-line summary for each numbered entry:
        """

        try:
            # Handle if the user passes the vertex framework client or strings

            if isinstance(vertex_client_or_project, GeminiGenerate):
                response_text = vertex_client_or_project.generate(
                    prompt=prompt, model_name="gemini-2.5-flash"
                )
            elif hasattr(vertex_client_or_project, "models"):
                response = vertex_client_or_project.models.generate_content(
                    model="gemini-2.5-flash", contents=prompt
                )
                response_text = response.text
            else:
                cl = GeminiGenerate(
                    project_id=project_id,
                    location="us-central1",
                    model_name="gemini-2.5-flash",
                )
                response_text = cl.generate(prompt=prompt)

            # Basic post-processing to clean up potential numbering/extra
            # whitespace
            lines = response_text.strip().split("\n") if response_text else []
            cleaned_lines = [
                line.strip() for line in lines if line.strip().startswith("-")
            ]
            return "\n".join(cleaned_lines)
        except Exception as e:
            return f"An error occurred while generating the summary: {e}"
