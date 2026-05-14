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

import google.auth
from google.auth.transport.requests import Request
import requests

# Ensure standard output is logging friendly
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class InsightsSimpleClient:
    """Simple wrapper to fetch data from CCAI Insights."""

    def __init__(self, project_id: str, location: str):
        self.project_id = project_id
        self.location = location
        self.scopes = ["https://www.googleapis.com/auth/cloud-platform"]

        try:
            self.creds, _ = google.auth.default(scopes=self.scopes)
            self.creds.refresh(Request())
        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            sys.exit(1)

        base_endpoint = "contactcenterinsights.googleapis.com"
        if location != "global":
            self.base_url = f"https://{location}-{base_endpoint}/v1"
        else:
            self.base_url = f"https://{base_endpoint}/v1"

    def _get_headers(self) -> Dict[str, str]:
        if getattr(self.creds, "expired", False) or not getattr(
            self.creds, "token", None
        ):
            self.creds.refresh(Request())
        return {
            "Authorization": f"Bearer {self.creds.token}",
            "Content-Type": "application/json",
            "x-goog-user-project": self.project_id,
        }

    def get_conversations(
        self, parent: str, filter_str: Optional[str] = None, max_pages: int = 5
    ) -> List[Dict[str, Any]]:
        """Fetches recent conversations."""
        results = []
        url = f"{self.base_url}/{parent}/conversations"
        params = {"pageSize": 100}
        if filter_str:
            params["filter"] = filter_str
        page_token = None

        pages = 0

        while pages < max_pages:
            if page_token:
                params["pageToken"] = page_token
            res = requests.get(
                url, headers=self._get_headers(), params=params, timeout=60
            )
            res.raise_for_status()
            data = res.json()
            results.extend(data.get("conversations", []))
            page_token = data.get("nextPageToken")
            pages += 1
            if not page_token:
                break
        return results

    def get_conversation_details(self, name: str) -> Dict[str, Any]:
        """Fetches a single full conversation."""
        url = f"{self.base_url}/{name}"
        res = requests.get(url, headers=self._get_headers(), timeout=60)
        res.raise_for_status()
        return res.json()


def extract_transcript(
    client: InsightsSimpleClient, conv_summary: Dict[str, Any]
) -> Optional[Dict[str, str]]:
    """Processes a single conversation to extract its transcript."""
    conv_name = conv_summary.get("name")
    conv_id = conv_name.split("/")[-1]
    logger.info(f"Fetching detailed transcript for {conv_id}...")

    try:
        details = client.get_conversation_details(conv_name)
        segments = (
            details.get("transcript", {}).get("transcriptSegments", [])
        )

        if not segments:
            logger.warning(f"No transcript segments found for {conv_id}.")
            return None

        lines = []
        for seg in segments:
            role = seg.get("segmentParticipant", {}).get("role", "UNKNOWN")
            text = seg.get("text", "")
            if text:
                lines.append(f"{role}: {text}")

        transcript_str = "\n".join(lines)
        return {"conversation_id": conv_id, "transcript": transcript_str}

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
        "--limit", type=int, default=5, help="Max candidate transcripts to extract"
    )
    parser.add_argument(
        "--output-file",
        default="./candidate_transcripts.json",
        help="Output JSON file path containing extracted data array",
    )
    args = parser.parse_args()

    parent = f"projects/{args.project_id}/locations/{args.location}"
    logger.info(f"Initializing Insights client for {parent}...")
    insights_client = InsightsSimpleClient(args.project_id, args.location)

    filter_arg = f'agent_id="{args.app_id}"'
    logger.info(f"Fetching recent conversations with server filter: {filter_arg}...")
    conversations = insights_client.get_conversations(
        parent, filter_str=filter_arg, max_pages=10
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
        logger.warning("No matching contained conversations found for this app.")
        sys.exit(0)

    extracted_data = []
    for conv in target_convs:
        res = extract_transcript(insights_client, conv)
        if res:
            extracted_data.append(res)

    os.makedirs(os.path.dirname(os.path.abspath(args.output_file)), exist_ok=True)
    with open(args.output_file, "w") as f:
        json.dump(extracted_data, f, indent=2)

    logger.info(
        f"Extraction complete. Saved {len(extracted_data)} transcripts to {args.output_file}."
    )


if __name__ == "__main__":
    main()

