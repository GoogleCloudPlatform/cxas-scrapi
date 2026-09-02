"""Metrics Extractor for Insights Analysis."""

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

import pandas as pd

from cxas_scrapi.core.insights import Insights


class MetricsExtractor:
    """Extracts and flattens metrics from CCAI Insights analyses."""

    def __init__(self, insights_client: Insights) -> None:
        self.insights_client = insights_client

    def get_evaluation_results(
        self,
        filter_str: str | None = None,
        scorecard_names: list[str] | None = None,
    ) -> pd.DataFrame:
        """Fetches evaluation results for conversations using the 'latestAnalysis'

        field, which natively contains the latest merged results for all
        scorecards.
        """
        logging.info("Fetching conversations (view=FULL) to extract metrics...")
        conversations = self.insights_client.list_conversations(
            filter_str=filter_str, view="FULL"
        )

        all_results = []
        convo_count = len(conversations)
        scorecard_stats: dict[str, set[str]] = {}

        for convo in conversations:
            convo_name = convo.get("name", "")
            # Rely on the merged 'latestAnalysis' provided by the platform
            analysis = convo.get("latestAnalysis") or {}
            analysis_name = analysis.get("name")

            qa_results = (
                analysis.get("analysisResult", {})
                .get("callAnalysisMetadata", {})
                .get("qaScorecardResults", [])
            )

            # Also inspect top-level qaAnswers if present
            top_level_qa = convo.get("qaAnswers", [])
            if isinstance(top_level_qa, list):
                qa_results = list(qa_results) + top_level_qa

            for qa_res in qa_results:
                revision_name = qa_res.get("qaScorecardRevision") or qa_res.get(
                    "scorecardRevision"
                )
                if not revision_name:
                    continue
                base_scorecard = revision_name.split("/revisions/")[0]

                # Update stats
                if base_scorecard not in scorecard_stats:
                    scorecard_stats[base_scorecard] = set()
                scorecard_stats[base_scorecard].add(convo_name)

                # Filter by scorecard revision if requested
                if scorecard_names and (
                    base_scorecard not in scorecard_names
                    and revision_name not in scorecard_names
                ):
                    continue

                answers = (
                    qa_res.get("qaAnswers") or qa_res.get("qaQuestions") or []
                )
                for answer in answers:
                    ans_val_obj = answer.get("answerValue", {})

                    # Extract clean answer text
                    if isinstance(ans_val_obj, dict):
                        answer_text = (
                            ans_val_obj.get("strValue")
                            or ans_val_obj.get("key")
                            or str(ans_val_obj)
                        )
                        score = ans_val_obj.get("score")
                        potential_score = ans_val_obj.get("potentialScore")
                        normalized_score = ans_val_obj.get("normalizedScore")
                    else:
                        answer_text = str(ans_val_obj) if ans_val_obj else None
                        score = answer.get("score")
                        potential_score = answer.get("potentialScore")
                        normalized_score = answer.get("normalizedScore")

                    result_row = {
                        "conversation_name": convo_name,
                        "analysis_name": analysis_name,
                        "scorecard_revision": revision_name,
                        "question_name": answer.get("qaQuestion"),
                        "question_body": answer.get("questionBody"),
                        "answer_value": answer_text,
                        "score": score,
                        "potential_score": potential_score,
                        "normalized_score": normalized_score,
                        "tags": answer.get("tags", []),
                    }
                    all_results.append(result_row)

        # Diagnostics
        if convo_count > 0:
            requested_scorecards = set()
            if scorecard_names:
                for sn in scorecard_names:
                    requested_scorecards.add(sn.split("/revisions/")[0])

            # Check coverage for all scorecards found
            for sc, convos_with_sc in scorecard_stats.items():
                percentage = (len(convos_with_sc) / convo_count) * 100
                if percentage < 100:
                    logging.warning(
                        "Scorecard %s was applied to only %.2f%% of conversations (%d/%d).",
                        sc,
                        percentage,
                        len(convos_with_sc),
                        convo_count,
                    )
                else:
                    logging.info("Scorecard %s coverage: 100%%", sc)

            # Check for requested scorecards that were NOT found at all
            if requested_scorecards:
                missing = requested_scorecards - set(scorecard_stats.keys())
                for sc in missing:
                    logging.warning(
                        "Scorecard %s was applied to 0.00%% of conversations (0/%d).",
                        sc,
                        convo_count,
                    )

        df = pd.DataFrame(all_results)
        if df.empty:
            logging.warning(
                "No evaluation results found for the given criteria."
            )
            return pd.DataFrame(
                columns=[
                    "conversation_name",
                    "analysis_name",
                    "scorecard_revision",
                    "question_name",
                    "question_body",
                    "answer_value",
                    "score",
                    "potential_score",
                    "normalized_score",
                    "tags",
                ]
            )

        return df

    def get_missing_conversations(
        self, filter_str: str, target_scorecard: str
    ) -> list[str]:
        """Identifies conversations that match the filter but DO NOT have an

        analysis using the target scorecard in their 'latestAnalysis'.
        """
        conversations = self.insights_client.list_conversations(
            filter_str=filter_str, view="FULL"
        )
        missing_convos = []
        base_target_scorecard = target_scorecard.split(
            "/revisions/", maxsplit=1
        )[0]

        for convo in conversations:
            convo_name = convo.get("name", "")
            analysis = convo.get("latestAnalysis", {})

            qa_results = (
                analysis.get("analysisResult", {})
                .get("callAnalysisMetadata", {})
                .get("qaScorecardResults", [])
            )
            has_scorecard = False
            for qa_res in qa_results:
                revision_name = qa_res.get("qaScorecardRevision") or qa_res.get(
                    "scorecardRevision"
                )
                if not revision_name:
                    continue
                base_scorecard = revision_name.split("/revisions/")[0]
                if (
                    base_scorecard == base_target_scorecard
                    or revision_name == target_scorecard
                ):
                    has_scorecard = True
                    break

            if not has_scorecard:
                missing_convos.append(convo_name)

        return missing_convos
