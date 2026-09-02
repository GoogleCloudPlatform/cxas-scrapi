"""Insights Reconciler for Declarative Analysis Orchestration."""

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

import logging
from typing import Any

import yaml

import cxas_scrapi.utils.scorecard_template_manager as template_manager
from cxas_scrapi.utils.insights_utils import InsightsUtils
from cxas_scrapi.utils.metrics_extractor import MetricsExtractor


class InsightsReconciler:
    """Orchestrates Insights analysis declaratively based on a configuration file."""

    def __init__(self, insights_utils: InsightsUtils) -> None:
        self.utils = insights_utils
        self.metrics_extractor = MetricsExtractor(
            insights_client=insights_utils.scorecards_client
        )

    def diff(self, config_path: str) -> dict[str, Any]:
        """Calculates the diff between the desired configuration and the live GCP state without modifying resources."""
        logging.info("Calculating diff for Insights config: %s", config_path)
        with open(config_path) as f:
            config = yaml.safe_load(f)

        scorecards_plan = []
        scorecards = config.get("scorecards", [])

        for sc_config in scorecards:
            template_path = sc_config.get("template")
            if not template_path:
                continue

            scorecard_dict, questions = (
                template_manager.load_scorecard_template(template_path)
            )
            display_name = scorecard_dict.get("displayName", "scorecard")
            sc_id = (
                sc_config.get("scorecard_id")
                or "sc-" + display_name.lower().replace(" ", "-")[:20]
            )
            full_scorecard_name = (
                f"{self.utils.scorecards_client.parent}/qaScorecards/{sc_id}"
            )

            # Check existing status
            scorecard_exists = False
            remote_revision = None
            try:
                remote_sc = self.utils.scorecards_client.get_scorecard(
                    full_scorecard_name
                )
                scorecard_exists = bool(remote_sc)
                latest_rev = self.utils.scorecards_client.get_latest_revision(
                    full_scorecard_name
                )
                remote_revision = latest_rev.get("name")
            except Exception:
                scorecard_exists = False

            # Check rules diff
            rules_plan = []
            backfills_plan = []
            apply_to = sc_config.get("apply_to", [])

            for instruction in apply_to:
                if "rule_id" in instruction:
                    rule_id = instruction.get("rule_id")
                    full_rule_name = f"{self.utils.analysis_rules_client.parent}/analysisRules/{rule_id}"
                    rule_exists = False
                    try:
                        r_obj = (
                            self.utils.analysis_rules_client.get_analysis_rule(
                                full_rule_name
                            )
                        )
                        rule_exists = bool(r_obj)
                    except Exception:
                        rule_exists = False

                    rules_plan.append(
                        {
                            "rule_id": rule_id,
                            "action": "UPDATE" if rule_exists else "CREATE",
                            "filter": instruction.get("filter", ""),
                            "percentage": instruction.get("percentage", 100),
                        }
                    )

                elif "backfill" in instruction:
                    bf_filter = instruction.get("filter", "")
                    missing_count = 0
                    if remote_revision:
                        try:
                            missing_convos = self.metrics_extractor.get_missing_conversations(
                                filter_str=bf_filter,
                                target_scorecard=remote_revision,
                            )
                            missing_count = len(missing_convos)
                        except Exception as e:
                            logging.debug(
                                "Could not check missing conversations: %s", e
                            )

                    backfills_plan.append(
                        {
                            "filter": bf_filter,
                            "estimated_missing_conversations": missing_count,
                            "percentage": instruction.get("percentage", 100.0),
                        }
                    )

            scorecards_plan.append(
                {
                    "template": template_path,
                    "scorecard_id": sc_id,
                    "display_name": display_name,
                    "exists_in_gcp": scorecard_exists,
                    "question_count": len(questions),
                    "rules": rules_plan,
                    "backfills": backfills_plan,
                }
            )

        return {
            "config_path": config_path,
            "project_id": self.utils.project_id,
            "location": self.utils.location,
            "scorecards": scorecards_plan,
        }

    def apply(self, config_path: str, dry_run: bool = False) -> dict[str, Any]:
        """Parses the YAML configuration and reconciles live GCP state."""
        if dry_run:
            diff_result = self.diff(config_path)
            logging.info("Dry run complete: %s", diff_result)
            return {"status": "DRY_RUN", "diff": diff_result}

        logging.info("Applying Insights configuration from: %s", config_path)
        with open(config_path) as f:
            config = yaml.safe_load(f)

        scorecards = config.get("scorecards", [])
        reconciliation_summary = []

        for sc_config in scorecards:
            result = self._reconcile_scorecard(sc_config)
            reconciliation_summary.append(result)

        return {
            "status": "APPLIED",
            "config_path": config_path,
            "results": reconciliation_summary,
        }

    def _reconcile_scorecard(self, sc_config: dict[str, Any]) -> dict[str, Any]:
        """Syncs the scorecard and applies streaming rules and historical backfills."""
        template_path = sc_config.get("template")
        if not template_path:
            raise ValueError("Scorecard config missing 'template' path.")

        logging.info("Reconciling scorecard template: %s", template_path)
        scorecard_dict, questions = template_manager.load_scorecard_template(
            template_path
        )
        display_name = scorecard_dict.get("displayName", "sc_template")
        sc_id = (
            sc_config.get("scorecard_id")
            or "sc-" + display_name.lower().replace(" ", "-")[:20]
        )

        apply_to = sc_config.get("apply_to", [])
        deploy = sc_config.get("deploy", True)

        # 1. Import and sync scorecard questions non-destructively

        revision_name = self.utils.import_scorecard(
            scorecard_dict=scorecard_dict,
            questions=questions,
            target_scorecard_id=sc_id,
        )
        logging.info("Scorecard synced to revision: %s", revision_name)

        # 2. Deploy revision if requested
        if deploy:
            try:
                self.utils.scorecards_client.deploy_revision(revision_name)
                logging.info("Deployed scorecard revision: %s", revision_name)
            except Exception as e:
                logging.debug("Scorecard revision deploy notice: %s", e)

        # 3. Reconcile rules and backfills
        rules_applied = []
        backfills_applied = []

        for instruction in apply_to:
            if "rule_id" in instruction:
                rule_res = self._reconcile_rule(revision_name, instruction)
                rules_applied.append(rule_res)
            elif "backfill" in instruction:
                bf_res = self._reconcile_backfill(revision_name, instruction)
                backfills_applied.append(bf_res)

        return {
            "scorecard_id": sc_id,
            "revision_name": revision_name,
            "rules": rules_applied,
            "backfills": backfills_applied,
        }

    def _reconcile_rule(
        self, revision_name: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        """Creates or updates a streaming analysis rule."""
        rule_id = config.get("rule_id")
        filter_str = config.get("filter", "")
        percentage = config.get("percentage", 100) / 100.0

        logging.info("Setting up analysis rule: %s", rule_id)
        rule_dict = {
            "name": f"{self.utils.analysis_rules_client.parent}/analysisRules/{rule_id}",
            "displayName": config.get("display_name") or rule_id,
            "conversationFilter": filter_str,
            "annotatorSelector": {
                "runQaAnnotator": True,
                "qaConfig": {
                    "scorecardList": {"qaScorecardRevisions": [revision_name]}
                },
            },
            "analysisPercentage": percentage,
            "active": config.get("active", True),
        }

        try:
            created = self.utils.analysis_rules_client.create_analysis_rule(
                rule_dict
            )
            return {"rule_id": rule_id, "status": "CREATED", "result": created}
        except Exception as e:
            logging.debug(
                "Could not create rule %s (may exist): %s", rule_id, e
            )
            return {"rule_id": rule_id, "status": "EXISTS_OR_UPDATED"}

    def _reconcile_backfill(
        self, revision_name: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        """Finds historical conversation gaps and triggers chunked bulk analysis."""
        filter_str = config.get("filter", "")
        percentage = config.get("percentage", 100.0)

        logging.info("Checking coverage for backfill (filter: %s)", filter_str)
        missing_convos = self.metrics_extractor.get_missing_conversations(
            filter_str=filter_str, target_scorecard=revision_name
        )

        if not missing_convos:
            logging.info("Coverage is 100%. No backfill needed.")
            return {
                "filter": filter_str,
                "missing_count": 0,
                "status": "UP_TO_DATE",
            }

        logging.warning(
            "Found %d conversations missing this scorecard. Starting chunked backfill...",
            len(missing_convos),
        )

        # Chunk conversation names to avoid CEL filter length limits
        chunk_size = 50
        chunks_triggered = 0
        annotator_selector = {
            "runQaAnnotator": True,
            "qaConfig": {
                "scorecardList": {"qaScorecardRevisions": [revision_name]}
            },
        }

        for i in range(0, len(missing_convos), chunk_size):
            chunk = missing_convos[i : i + chunk_size]
            chunk_filter = " OR ".join([f'name="{name}"' for name in chunk])
            try:
                self.utils.scorecards_client.bulk_analyze_conversations(
                    filter_str=chunk_filter,
                    annotator_selector=annotator_selector,
                    analysis_percentage=percentage,
                )
                chunks_triggered += 1
            except Exception as e:
                logging.warning("Failed backfill chunk: %s", e)

        return {
            "filter": filter_str,
            "missing_count": len(missing_convos),
            "chunks_triggered": chunks_triggered,
            "status": "BACKFILL_TRIGGERED",
        }
