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
import concurrent.futures
import json
import logging
import os
import random
import sys
from typing import Any, Dict, Optional

import yaml

from cxas_scrapi.core.conversation_history import ConversationHistory
from cxas_scrapi.core.insights import Insights

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

USER_AGENT_EXTENSION = "skill/cxas-loss-analysis/fetch-losses"


def ccai_to_cxas_dict(ccai_conv: Dict[str, Any]) -> Dict[str, Any]:
    """Converts a CCAI Insights conversation dict to CXAS-like format."""
    segments = ccai_conv.get("transcript", {}).get("transcriptSegments", [])
    turns = []
    for seg in segments:
        role = seg.get("segmentParticipant", {}).get("role", "UNKNOWN")
        text = seg.get("text", "")
        if not text:
            continue

        cxas_role = "user" if role in ("CUSTOMER", "END_USER") else "agent"
        turns.append(
            {"messages": [{"role": cxas_role, "chunks": [{"text": text}]}]}
        )
    return {"turns": turns}


def extract_transcript(
    client: Insights, conv_summary: Dict[str, Any]
) -> Optional[Dict[str, str]]:
    """Extracts conversation transcript and formats to YAML."""
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
        description=(
            "Fetch non-contained (loss) transcripts from "
            "CCAI Insights for agent analysis."
        )
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
        default=1000,
        help="Max raw conversations to inspect (default: 1000)",
    )
    parser.add_argument(
        "--loss-limit",
        type=int,
        default=100,
        help="Max loss transcripts to extract (default: 100)",
    )
    parser.add_argument(
        "--output-file",
        required=True,
        help="Output JSON file path to save the array of transcripts",
    )

    args = parser.parse_args()

    logger.info(
        "Initializing Insights client for project %s, location %s...",
        args.project_id,
        args.location,
    )
    insights_client = Insights(
        project_id=args.project_id,
        location=args.location,
        user_agent_extension=USER_AGENT_EXTENSION,
    )

    filter_arg = f'agent_id="{args.app_id}"'
    logger.info(
        f"Fetching recent conversations (target limit raw: {args.limit})..."
    )

    max_pages = (args.limit + 99) // 100
    conversations = insights_client.list_conversations(
        filter_str=filter_arg, page_size=100, max_pages=max_pages
    )

    if not conversations:
        logger.warning("No conversations returned from Insights API.")
        sys.exit(0)

    conversations = conversations[: args.limit]
    logger.info(f"Retrieved {len(conversations)} raw conversation summaries.")

    # Filter for non-contained conversations (losses)
    losses = []
    for c in conversations:
        contained = c.get("labels", {}).get("sessionContained")
        if contained != "true" and contained is not True:
            losses.append(c)

    total_losses = len(losses)
    logger.info(
        "Identified %d non-contained conversations (losses).", total_losses
    )

    if not losses:
        logger.warning("No non-contained conversations found for this app.")
        sys.exit(0)

    # Randomly sample losses
    if len(losses) > args.loss_limit:
        target_losses = random.sample(losses, args.loss_limit)
        logger.info(
            "Randomly sampled %d losses from %d total losses for extraction...",
            len(target_losses),
            len(losses),
        )
    else:
        target_losses = losses
        logger.info(
            "Selecting all %d available losses for extraction...",
            len(target_losses),
        )

    # Download detailed transcripts in parallel
    extracted_data = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(extract_transcript, insights_client, conv)
            for conv in target_losses
        ]
        for fut in concurrent.futures.as_completed(futures):
            res = fut.result()
            if res:
                extracted_data.append(res)

    logger.info(
        f"Successfully downloaded {len(extracted_data)} loss transcripts."
    )

    # Save to output JSON file and chunk transcripts
    output_dir = os.path.dirname(os.path.abspath(args.output_file))
    os.makedirs(output_dir, exist_ok=True)

    # Chunk size
    chunk_size = 10
    chunks = []

    for i in range(0, len(extracted_data), chunk_size):
        chunk_data = extracted_data[i : i + chunk_size]
        chunk_num = (i // chunk_size) + 1
        base_name = os.path.basename(args.output_file)
        name, ext = os.path.splitext(base_name)
        chunk_file_name = f"{name}_chunk_{chunk_num}{ext}"
        chunk_file_path = os.path.join(output_dir, chunk_file_name)

        logger.info(f"Writing chunk {chunk_num} to {chunk_file_path}...")
        with open(chunk_file_path, "w") as f:
            json.dump(chunk_data, f, indent=2)
        chunks.append(chunk_file_path)

    total_inspected = len(conversations)
    containment_rate = 0.0
    if total_inspected > 0:
        containment_rate = round(
            ((total_inspected - total_losses) / total_inspected) * 100, 2
        )

    output_payload = {
        "total_inspected": total_inspected,
        "total_losses": total_losses,
        "containment_rate": containment_rate,
        "chunks": chunks,
    }

    with open(args.output_file, "w") as f:
        json.dump(output_payload, f, indent=2)

    logger.info(f"Saved fetched transcripts metadata to {args.output_file}")


if __name__ == "__main__":
    main()
