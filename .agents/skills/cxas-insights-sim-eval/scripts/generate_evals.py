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

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

import yaml

from cxas_scrapi.core.conversation_history import ConversationHistory
from cxas_scrapi.core.insights import Insights

# Ensure standard output is logging friendly
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

USER_AGENT_EXTENSION = "skill/cxas-insights-sim-eval/generate-evals"


def ccai_to_cxas_dict(ccai_conv: Dict[str, Any]) -> Dict[str, Any]:
    """Converts a CCAI Insights conversation dict to a CXAS-like conversation dict."""
    segments = ccai_conv.get("transcript", {}).get("transcriptSegments", [])
    turns = []
    for seg in segments:
        role = seg.get("segmentParticipant", {}).get("role", "UNKNOWN")
        text = seg.get("text", "")
        if not text:
            continue

        # Map CCAI roles to CXAS roles
        cxas_role = "user" if role in ("CUSTOMER", "END_USER") else "agent"

        turns.append(
            {
                "messages": [
                    {"role": cxas_role, "chunks": [{"text": text}]}
                ]
            }
        )
    return {"turns": turns}


def extract_transcript(
    client: Insights, conv_summary: Dict[str, Any]
) -> Optional[Dict[str, str]]:
    """Processes a single conversation to extract its transcript and formats to YAML."""
    conv_name = conv_summary.get("name")
    conv_id = conv_name.split("/")[-1]
    logger.info(f"Fetching detailed transcript for {conv_id}...")

    try:
        details = client.get_conversation(conv_name)
        cxas_dict = ccai_to_cxas_dict(details)

        # Leverage ConversationHistory to format to FDE YAML structure
        yaml_dict = ConversationHistory.conversation_dict_to_yaml(cxas_dict)
        transcript_yaml = yaml.dump(
            yaml_dict, sort_keys=False, allow_unicode=True
        )

        return {"conversation_id": conv_id, "transcript": transcript_yaml}

    except Exception as e:
        logger.error(f"Failed extracting {conv_id}: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(
        description="Mine CCAI Insights candidate transcripts for evaluation generation."
    )
    parser.add_argument("--project-id", required=True, help="GCP Project ID")
    parser.add_argument(
        "--location", required=True, help="Insights Location (e.g. us)"
    )
    parser.add_argument(
        "--app-id",
        required=True,
        help="Target CXAS App ID to filter conversations for",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Max candidate transcripts to extract",
    )
    parser.add_argument(
        "--output-file",
        default="./candidate_transcripts.json",
        help="Output JSON file path containing extracted data array",
    )
    args = parser.parse_args()

    logger.info(
        f"Initializing Insights client for project {args.project_id}, location {args.location}..."
    )
    insights_client = Insights(
        project_id=args.project_id,
        location=args.location,
        user_agent_extension=USER_AGENT_EXTENSION,
    )

    filter_arg = f'agent_id="{args.app_id}"'
    logger.info(
        f"Fetching recent conversations with server filter: {filter_arg}..."
    )
    conversations = insights_client.list_conversations(
        filter_str=filter_arg, max_pages=10
    )

    if not conversations:
        logger.warning("No conversations returned from Insights API.")
        sys.exit(0)

    # Client-side filter for containment
    logger.info(
        f"Filtering {len(conversations)} agent conversations for sessionContained=true..."
    )
    filtered_convs = []
    for c in conversations:
        if (
            c.get("labels", {}).get("sessionContained") == "true"
            or c.get("labels", {}).get("sessionContained") is True
        ):
            filtered_convs.append(c)

    logger.info(f"Found {len(filtered_convs)} candidate conversations.")

    target_convs = filtered_convs[: args.limit]

    if not target_convs:
        logger.warning(
            "No matching contained conversations found for this app."
        )
        sys.exit(0)

    extracted_data = []
    for conv in target_convs:
        res = extract_transcript(insights_client, conv)
        if res:
            extracted_data.append(res)

    os.makedirs(
        os.path.dirname(os.path.abspath(args.output_file)), exist_ok=True
    )
    with open(args.output_file, "w") as f:
        json.dump(extracted_data, f, indent=2)

    logger.info(
        f"Extraction complete. Saved {len(extracted_data)} transcripts to {args.output_file}."
    )


if __name__ == "__main__":
    main()
