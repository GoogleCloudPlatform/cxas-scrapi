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
from typing import Any

import yaml

from cxas_scrapi.core.conversation_history import ConversationHistory
from cxas_scrapi.core.insights import Insights

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

USER_AGENT_EXTENSION = "skill/cxas-loss-analysis/fetch-losses"


def ccai_to_cxas_dict(ccai_conv: dict[str, Any]) -> dict[str, Any]:
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


def extract_transcript(conv: dict[str, Any]) -> dict[str, str] | None:
    """Extracts conversation transcript and formats to YAML."""
    conv_name = conv.get("name")
    conv_id = conv_name.split("/")[-1]
    logger.info(f"Extracting transcript for {conv_id}...")

    try:
        cxas_dict = ccai_to_cxas_dict(conv)

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
        default=500,
        help="Max conversations to retrieve and process (default: 500)",
    )
    parser.add_argument(
        "--start-time",
        help=(
            "RFC 3339 timestamp for start of time period "
            "(e.g. 2026-05-20T00:00:00Z)"
        ),
    )
    parser.add_argument(
        "--end-time",
        help=(
            "RFC 3339 timestamp for end of time period "
            "(e.g. 2026-05-26T23:59:59Z)"
        ),
    )
    parser.add_argument(
        "--filter",
        help=(
            "Custom API filter string to append (overrides default loss filter)"
        ),
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

    filter_parts = [f'agent_id="{args.app_id}"']
    if args.filter:
        filter_parts.append(args.filter)
    else:
        filter_parts.append('-labels.sessionContained:"true"')

    if args.start_time:
        filter_parts.append(f'create_time >= "{args.start_time}"')
    if args.end_time:
        filter_parts.append(f'create_time <= "{args.end_time}"')
    filter_arg = " AND ".join(filter_parts)

    logger.info(
        "Fetching recent conversations with filter: %s (target limit: %d)...",
        filter_arg,
        args.limit,
    )

    max_pages = (args.limit + 99) // 100
    conversations = insights_client.list_conversations(
        filter_str=filter_arg, view="FULL", page_size=100, max_pages=max_pages
    )

    if not conversations:
        logger.warning("No conversations returned from Insights API.")
        sys.exit(0)

    conversations = conversations[: args.limit]
    logger.info(f"Retrieved {len(conversations)} raw conversation summaries.")

    # With server-side filtering, all retrieved conversations are target
    # conversations
    losses = conversations
    total_losses = len(losses)
    logger.info("Identified %d target conversations.", total_losses)

    if not losses:
        logger.warning("No conversations found matching the filter.")
        sys.exit(0)

    # Process transcripts
    extracted_data = []
    for conv in losses:
        res = extract_transcript(conv)
        if res:
            extracted_data.append(res)

    logger.info(
        f"Successfully processed {len(extracted_data)} loss transcripts."
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

    output_payload = {
        "total_losses": total_losses,
        "chunks": chunks,
    }

    with open(args.output_file, "w") as f:
        json.dump(output_payload, f, indent=2)

    logger.info(f"Saved fetched transcripts metadata to {args.output_file}")


if __name__ == "__main__":
    main()
