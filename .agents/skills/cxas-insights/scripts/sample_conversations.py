#!/usr/bin/env python3
"""CLI Script to extract sample conversations from live CCAI Insights for building golden test datasets."""

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
import sys

from cxas_scrapi.core.insights import Insights


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sample conversation transcripts from CCAI Insights to build golden test datasets."
    )
    parser.add_argument(
        "--project-id",
        required=True,
        help="Google Cloud Project ID.",
    )
    parser.add_argument(
        "--location",
        default="us-central1",
        help="Insights location (default: us-central1).",
    )
    parser.add_argument(
        "--filter",
        default="",
        help="Optional CEL filter string for conversations (e.g. create_time > '2026-01-01T00:00:00Z').",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of conversations to sample (default: 20).",
    )
    parser.add_argument(
        "--output",
        default="golden_conversations.json",
        help="Output JSON path to save sampled conversations (default: golden_conversations.json).",
    )

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    client = Insights(project_id=args.project_id, location=args.location)

    print(
        f"Fetching conversations from {client.parent} (filter='{args.filter}', limit={args.limit})..."
    )
    try:
        conversations = client.list_conversations(
            filter_str=args.filter if args.filter else None,
            view="FULL",
        )
    except Exception as e:
        print(f"Failed to fetch conversations: {e}", file=sys.stderr)
        sys.exit(1)

    sampled = conversations[: args.limit]
    print(f"Sampled {len(sampled)} conversations.")

    # Format into golden dataset structure
    goldens = []
    for c in sampled:
        convo_name = c.get("name", "")
        convo_id = (
            convo_name.split("/")[-1] if "/" in convo_name else convo_name
        )

        # Extract turns
        turns = []
        raw_turns = (
            c.get("transcript", {}).get("transcriptSegments", [])
            or c.get("conversationTurns", [])
            or c.get("turns", [])
        )

        for t in raw_turns:
            speaker = (
                t.get("segmentParticipant", {}).get("role")
                or t.get("speaker")
                or t.get("role", "UNKNOWN")
            )
            text = t.get("text") or t.get("content", "")
            turns.append({"speaker": speaker, "text": text})

        goldens.append(
            {
                "conversation_id": convo_id,
                "conversation_name": convo_name,
                "turns": turns,
                "expected_answers": {},  # Placeholder for human annotator to add expected labels
            }
        )

    try:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(goldens, f, indent=2)
        print(f"✅ Successfully wrote sampled dataset to: {args.output}")
    except Exception as e:
        print(f"Failed to write output: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
