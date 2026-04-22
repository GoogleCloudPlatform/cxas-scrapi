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

"""Async Artifacts Builder for generating migration artifacts.

This module is currently not used in the main migration loop, and will be
modernized. We plan to add flow charts as inputs in the future.
"""

import io
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from cxas_scrapi.migration.prompts import Prompts

logger = logging.getLogger(__name__)


class CXASAsyncArtifactBuilder:
    """Handles Step 1: Production-grade asynchronous generation of migration
    artifacts.

    This class takes existing inputs (like tree view and BQ prod conversation
    data) and generates a series of artifacts that can be useful for downstream
    migration/agent building tasks.
    """

    def __init__(self, gemini_client: Any, determinism: float = 0.0):
        self.gemini = gemini_client
        self.determinism = determinism
        self.output_dir = str(
            Path(__file__).resolve().parents[3]
            / "examples"
            / "migration_artifacts"
        )
        os.makedirs(self.output_dir, exist_ok=True)

    @staticmethod
    def _get_determinism_instruction(determinism: float, doc_type: str) -> str:
        """Helper to get determinism instruction."""
        if doc_type == "requirements":
            if determinism < 0.3:
                return (
                    "STRICT ADHERENCE: Requirements must specify exact legacy "
                    "verbiage and behavior."
                )
            else:
                return (
                    "FLEXIBLE ADHERENCE: Requirements should focus on intent "
                    "and natural conversation."
                )
        elif doc_type == "test_cases":
            if determinism < 0.3:
                return (
                    "STRICT REGRESSION: Test cases must mirror the DFCX flow "
                    "exactly."
                )
            else:
                return (
                    "GENERATIVE FLOW: Test cases should simulate fluid human "
                    "conversation."
                )
        return ""

    def _save_artifact(
        self,
        flow_name: str,
        filename: str,
        content: Any,
        is_dataframe: bool = False,
    ):
        """Saves artifacts locally immediately after generation for easy
        inspection."""
        safe_flow_name = "".join(
            [
                char
                for char in flow_name
                if char.isalnum() or char in (" ", "_", "-")
            ]
        ).strip()
        flow_dir = os.path.join(self.output_dir, safe_flow_name)
        os.makedirs(flow_dir, exist_ok=True)
        file_path = os.path.join(flow_dir, filename)
        try:
            if is_dataframe and isinstance(content, pd.DataFrame):
                content.to_csv(file_path, index=False)
            else:
                with open(file_path, "w", encoding="utf-8") as f:
                    if isinstance(content, (dict, list)):
                        json.dump(content, f, indent=2)
                    else:
                        f.write(str(content))
        except Exception as e:
            logger.warning(f"    ⚠️ Failed to save {filename}: {e}")

    async def _run_step_1a_inventory(
        self, flow_name: str, tree_view: str, context_data: Dict[str, Any]
    ) -> str:
        """Runs Step 1A: Technical Inventory."""
        prompt_1a = Prompts.STEP_1A_INVENTORY["template"].format(
            flow_name=flow_name,
            tree_view=tree_view,
            context_json_str=json.dumps(context_data, indent=2),
        )
        inventory = await self.gemini.generate_async(
            prompt=prompt_1a, system_prompt=Prompts.STEP_1A_INVENTORY["system"]
        )
        self._save_artifact(flow_name, "1A_Inventory.md", inventory)
        return inventory

    async def _run_step_1b_business_logic(
        self,
        flow_name: str,
        inventory: str,
        tree_view: str,
        telemetry_summary: str,
    ) -> str:
        """Runs Step 1B: Business Logic."""
        prompt_1b = Prompts.STEP_1B_BUSINESS_LOGIC["template"].format(
            flow_name=flow_name,
            inventory_report=inventory,
            tree_view=tree_view,
            amplified_summary=telemetry_summary,
        )
        business_logic = await self.gemini.generate_async(
            prompt=prompt_1b,
            system_prompt=Prompts.STEP_1B_BUSINESS_LOGIC["system"],
        )
        self._save_artifact(flow_name, "1B_Business_Logic.md", business_logic)
        return business_logic

    async def _run_step_1c_requirements(
        self, flow_name: str, business_logic: str, tree_view: str
    ) -> pd.DataFrame:
        """Runs Step 1C: Requirements."""
        req_instruction = CXASAsyncArtifactBuilder._get_determinism_instruction(
            self.determinism, "requirements"
        )
        prompt_1c = Prompts.STEP_1C_REQS["template"].format(
            flow_name=flow_name,
            business_logic=business_logic,
            tree_view=tree_view,
            req_instruction=req_instruction,
        )
        reqs_raw = await self.gemini.generate_async(
            prompt=prompt_1c, system_prompt=Prompts.STEP_1C_REQS["system"]
        )

        df_reqs = pd.DataFrame()
        try:
            csv_str = reqs_raw.replace("```csv", "").replace("```", "").strip()
            df_reqs = pd.read_csv(io.StringIO(csv_str))
            df_reqs["Flow_Name"] = flow_name
            self._save_artifact(
                flow_name, "1C_Requirements.csv", df_reqs, is_dataframe=True
            )
        except Exception as e:
            logger.warning(
                f"[{flow_name}] ⚠️ Error parsing Requirements CSV: {e}"
            )

        return df_reqs

    async def _run_step_1d_tests(
        self,
        flow_name: str,
        inventory: str,
        tree_view: str,
        business_logic: str,
        df_reqs: pd.DataFrame,
    ) -> List[Dict[str, Any]]:
        """Runs Step 1D: Tests."""
        test_instruction = (
            CXASAsyncArtifactBuilder._get_determinism_instruction(
                self.determinism, "test_cases"
            )
        )
        reqs_context = (
            df_reqs.to_csv(index=False)
            if not df_reqs.empty
            else "No requirements generated."
        )

        prompt_1d = Prompts.STEP_1D_TESTS["template"].format(
            flow_name=flow_name,
            inventory_report=inventory,
            tree_view=tree_view,
            business_logic=business_logic,
            reqs_context=reqs_context,
            test_instruction=test_instruction,
        )
        tests_raw = await self.gemini.generate_async(
            prompt=prompt_1d, system_prompt=Prompts.STEP_1D_TESTS["system"]
        )

        parsed_tests = []
        try:
            json_str = (
                tests_raw.replace("```json", "").replace("```", "").strip()
            )
            parsed_tests = json.loads(json_str)
            self._save_artifact(flow_name, "1D_Test_Cases.json", parsed_tests)
        except Exception as e:
            logger.warning(
                f"[{flow_name}] ⚠️ Error parsing Test Cases JSON: {e}"
            )

        return parsed_tests

    async def run_step_1(
        self,
        flow_name: str,
        tree_view: str,
        context_data: Dict[str, Any],
        telemetry_summary: str,
    ) -> Dict[str, Any]:
        """Runs the full Step 1 analysis in sequence."""
        logger.info(
            f"[{flow_name}] Starting Async Step 1: Analysis & Logic "
            "Reconstruction"
        )
        artifacts = {"flow_name": flow_name}

        # --- 1A: Technical Inventory ---
        artifacts["inventory"] = await self._run_step_1a_inventory(
            flow_name, tree_view, context_data
        )
        logger.info(f"[{flow_name}] ✅ 1A: Inventory Complete")

        # --- 1B: Business Logic ---
        artifacts["business_logic"] = await self._run_step_1b_business_logic(
            flow_name, artifacts["inventory"], tree_view, telemetry_summary
        )
        logger.info(f"[{flow_name}] ✅ 1B: Logic Complete")

        # --- 1C: Requirements (CSV) ---
        artifacts["requirements"] = await self._run_step_1c_requirements(
            flow_name, artifacts["business_logic"], tree_view
        )
        logger.info(f"[{flow_name}] ✅ 1C: Requirements Complete")

        # --- 1D: Test Cases (JSON) ---
        artifacts["test_cases"] = await self._run_step_1d_tests(
            flow_name,
            artifacts["inventory"],
            tree_view,
            artifacts["business_logic"],
            artifacts["requirements"],
        )
        logger.info(f"[{flow_name}] ✅ 1D: Tests Complete")

        return artifacts
