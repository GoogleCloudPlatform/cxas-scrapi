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

import json
import logging
from datetime import datetime
from typing import Any, Dict, List

from cxas_scrapi.migration.prompts import Prompts
from cxas_scrapi.utils.gemini import GeminiGenerate

logger = logging.getLogger(__name__)


class DFCXMigrationReporter:
    """Generates a comprehensive, engineering-focused Markdown report of the \
migration process,
    augmented with CXAS agent details and generative AI content.
    """

    def __init__(self, gemini_client: GeminiGenerate):
        """Initializes the reporter.

        Args:
            gemini_client: An instance of GeminiGenerate for AI augmentation.
        """
        self.gemini_client = gemini_client

        # Dialogflow migration logs (retained from previous snippet)
        self.app_info: Dict[str, str] = {}
        self.variables: List[Dict[str, str]] = []
        self.tools: List[Dict[str, str]] = []
        self.agents: List[Dict[str, str]] = []
        self.dependencies: List[Dict[str, str]] = []
        self.examples: List[Dict[str, str]] = []
        self.actions: List[Dict[str, str]] = []
        self.transformations: List[Dict[str, str]] = []
        self.skipped: List[Dict[str, str]] = []

        # Augmented details
        self.generated_features: str = ""
        self.generated_description: str = ""

    def set_app_info(self, source_id: str, target_name: str, target_id: str):
        self.app_info = {
            "source": source_id,
            "target_name": target_name,
            "target_id": target_id,
        }

    def log_variable(
        self, original_name: str, sanitized_name: str, var_type: str
    ):
        self.variables.append(
            {
                "original": original_name,
                "sanitized": sanitized_name,
                "type": var_type,
            }
        )

    def log_tool(
        self,
        tool_type: str,
        original_name: str,
        new_id: str,
        ops: List[str] = None,
    ):
        entry = {"type": tool_type, "original": original_name, "new_id": new_id}
        if ops:
            entry["ops"] = ", ".join(ops)
        self.tools.append(entry)

    def log_agent(
        self,
        original_name: str,
        new_id: str,
        description: str = "",
        model: str = "",
    ):
        self.agents.append(
            {
                "original": original_name,
                "new_id": new_id,
                "description": description,
                "model": model,
            }
        )

    def log_agent_dependency(self, agent_name: str, dependency_name: str):
        self.dependencies.append(
            {"agent": agent_name, "dependency": dependency_name}
        )

    def log_example(self, agent_name: str, example_name: str):
        self.examples.append({"agent": agent_name, "example": example_name})

    def log_action(self, category: str, description: str):
        self.actions.append({"category": category, "description": description})

    def log_transformation(
        self, category: str, original: str, migrated: str, notes: str = ""
    ):
        self.transformations.append(
            {
                "category": category,
                "original": original,
                "migrated": migrated,
                "notes": notes,
            }
        )

    def log_skipped(self, category: str, name: str, reason: str):
        self.skipped.append(
            {"category": category, "name": name, "reason": reason}
        )

    async def generate_cxas_augmented_details(
        self, agent_config: Dict[str, Any]
    ):
        """Uses Gemini to generate user journeys and analyze instructions,
        tools, and callbacks based on the provided CXAS agent configuration.

        Args:
            agent_config: A dictionary containing the full configuration of
                the CXAS agent (instructions, tools, callbacks, etc.).
        """
        try:
            logger.info(
                "Generating augmented details and user journeys using Gemini."
            )

            # 1. Generate User Journeys and Analysis
            prompt = Prompts.REPORTER_JOURNEYS["template"].format(
                agent_config_json=json.dumps(agent_config, indent=2)
            )

            features_raw = await self.gemini_client.generate_async(
                prompt=prompt,
                system_prompt=Prompts.REPORTER_JOURNEYS["system"],
            )
            self.generated_features = (
                features_raw.strip() if features_raw else ""
            )

            # 2. Generate Description (still useful to have a short summary)
            desc_prompt = Prompts.REPORTER_DESCRIPTION["template"].format(
                agent_config_json=json.dumps(agent_config, indent=2)
            )

            desc_raw = await self.gemini_client.generate_async(
                prompt=desc_prompt,
                system_prompt=Prompts.REPORTER_DESCRIPTION["system"],
            )
            self.generated_description = desc_raw.strip() if desc_raw else ""

            logger.info(
                "Successfully generated augmented details and user journeys "
                "using Gemini."
            )

        except Exception as e:
            logger.error(f"Failed to generate augmented details: {e}")
            self.generated_description = "Error generating description."
            self.generated_features = (
                "Error generating user journeys and analysis."
            )

    def generate_markdown(self) -> str:
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        md = [
            "# Polysynth Migration Audit Report",
            f"**Generated:** `{timestamp}`\n",
            "## 📦 App Details",
            f"- **Source DFCX Agent:** `{self.app_info.get('source', 'N/A')}`",
            f"- **Target Polysynth App:** "
            f"`{self.app_info.get('target_name', 'N/A')}`",
            f"- **Target App ID:** `{self.app_info.get('target_id', 'N/A')}`\n",
        ]

        # Add Augmented Details if available
        if self.generated_description:
            md.extend(
                [
                    "## 🤖 AI-Augmented Analysis & User Journeys",
                    f"**Summary:** {self.generated_description}\n",
                    "### 🗺️ Detailed User Journeys & Component Analysis",
                    f"{self.generated_features}\n",
                ]
            )

        if self.skipped:
            md.extend(
                [
                    "## ⚠️ Skipped Resources (Action Required)",
                    "| Category | Resource Name | Reason |",
                    "|---|---|---|",
                ]
            )
            for s in self.skipped:
                md.append(
                    f"| `{s['category']}` | `{s['name']}` | {s['reason']} |"
                )
            md.append("\n")

        md.extend(
            [
                "## 🔠 App Variables Migrated",
                "| Original Name | Polysynth Name | Type |",
                "|---|---|---|",
            ]
        )
        for v in self.variables:
            md.append(
                f"| `{v['original']}` | `{v['sanitized']}` | `{v['type']}` |"
            )
        if not self.variables:
            md.append("| - | - | - |")

        md.extend(
            [
                "\n## 🛠️ Tools & Toolsets Migrated",
                "| Type | Original Name | Polysynth ID | Operations / Notes |",
                "|---|---|---|---|",
            ]
        )
        for t in self.tools:
            ops = t.get("ops", "-")
            md.append(
                f"| `{t['type']}` | `{t['original']}` | "
                f"`{t['new_id']}` | `{ops}` |"
            )
        if not self.tools:
            md.append("| - | - | - |")

        md.extend(
            [
                "\n## 🤖 Agents Migrated",
                "| Original Playbook/Flow | Polysynth Agent ID | "
                "Model | Generated Description |",
                "|---|---|---|---|",
            ]
        )
        for a in self.agents:
            desc = a["description"].replace("\n", " ").replace("|", "\\|")
            md.append(
                f"| `{a['original']}` | `{a['new_id']}` | "
                f"`{a['model']}` | {desc} |"
            )
        if not self.agents:
            md.append("| - | - | - | - |")

        md.extend(
            [
                "\n## 🔗 AST Code Block Dependencies",
                "| Agent | Injected Toolset Dependency |",
                "|---|---|",
            ]
        )
        for d in self.dependencies:
            md.append(f"| `{d['agent']}` | `{d['dependency']}` |")
        if not self.dependencies:
            md.append("| - | - |")

        md.extend(
            [
                "\n## 🔄 Instruction Rewrites & Transformations",
                "| Category | Original Reference | "
                "Migrated Reference | Notes |",
                "|---|---|---|---|",
            ]
        )
        for t in self.transformations:
            md.append(
                f"| `{t['category']}` | `{t['original']}` | "
                f"`{t['migrated']}` | {t['notes']} |"
            )
        if not self.transformations:
            md.append("| - | - | - | No notable transformations. |")

        md.extend(
            [
                "\n## ⚙️ System Actions & Linking",
                "| Category | Description |",
                "|---|---|",
            ]
        )
        for act in self.actions:
            md.append(f"| `{act['category']}` | {act['description']} |")

        md.extend(
            [
                "\n## 🛠️ Manual Steps Required",
                "The following items are not covered by this tool and must "
                "be migrated manually:",
                "1. **Examples:** If the source app has any examples, they "
                "need to be recreated in Polysynth.",
                "2. **Flows:** If the source app has any flows, they need "
                "to be manually transitioned or implemented.",
            ]
        )

        return "\n".join(md)

    def export_and_download(self, filename="migration_report.md"):
        md_content = self.generate_markdown()
        with open(filename, "w") as f:
            f.write(md_content)
        logger.info(f"\n✅ Migration report generated: {filename}")
        # Removed Colab specific download logic as it might not be running
        # in Colab
