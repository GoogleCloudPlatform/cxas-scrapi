#!/usr/bin/env python3
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
import os
import sys
import zipfile
from typing import Any, Dict, List

# List of common system/default intents to ignore for steering logic
IGNORE_INTENTS = {
    "Default Welcome Intent",
    "Default Negative Intent",
}


def parse_dfcx_zip(zip_path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(zip_path):
        print(f"Error: Zip file not found at {zip_path}", file=sys.stderr)
        sys.exit(1)

    intents_data = []

    with zipfile.ZipFile(zip_path, "r") as z:
        # Step 1: Find all intent definition files
        intent_definition_files = []
        training_phrases_files = {}

        for name in z.namelist():
            parts = name.split("/")
            # Look for intents/<intent_dir>/<intent_dir>.json
            is_def = (
                len(parts) == 3
                and parts[0] == "intents"
                and parts[2].endswith(".json")
                and parts[2].replace(".json", "") == parts[1]
            )
            # Look for intents/<intent_dir>/trainingPhrases/<lang>.json
            is_tp = (
                len(parts) == 4
                and parts[0] == "intents"
                and parts[2] == "trainingPhrases"
                and parts[3].endswith(".json")
            )

            if is_def:
                intent_definition_files.append(name)
            elif is_tp:
                intent_dir = parts[1]
                training_phrases_files.setdefault(intent_dir, []).append(name)

        # Step 2: Parse each intent
        for def_file in intent_definition_files:
            intent_dir = def_file.split("/")[1]

            with z.open(def_file) as f:
                try:
                    intent_json = json.load(f)
                except Exception as e:
                    print(
                        f"Warning: Failed to parse {def_file}: {e}",
                        file=sys.stderr,
                    )
                    continue

                display_name = intent_json.get("displayName")
                if not display_name or display_name in IGNORE_INTENTS:
                    continue

                # Collect training phrases (usually just English 'en' or
                # first available)
                phrases = []
                tp_files = training_phrases_files.get(intent_dir, [])

                # Sort to prioritize English if available
                tp_files.sort(key=lambda x: 0 if "en" in x else 1)

                if tp_files:
                    # Parse the primary training phrase file
                    with z.open(tp_files[0]) as tpf:
                        try:
                            tp_json = json.load(tpf)
                            for tp in tp_json.get("trainingPhrases", []):
                                parts = [
                                    p.get("text", "")
                                    for p in tp.get("parts", [])
                                ]
                                phrase_text = "".join(parts).strip()
                                if phrase_text:
                                    phrases.append(phrase_text)
                        except Exception as e:
                            print(
                                "Warning: Failed to parse training phrases"
                                f" in {tp_files[0]}: {e}",
                                file=sys.stderr,
                            )

                # Keep a maximum of 5 representative training phrases to
                # keep prompt context clean
                sample_phrases = phrases[:5]

                intents_data.append(
                    {
                        "intent_name": display_name,
                        "description": intent_json.get("description", ""),
                        "sample_training_phrases": sample_phrases,
                        "total_phrases_count": len(phrases),
                    }
                )

    # Sort intents alphabetically by name
    intents_data.sort(key=lambda x: x["intent_name"])
    return intents_data


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Parse DFCX agent zip and print intent list for LLM context."
        )
    )
    parser.add_argument(
        "--zip-path",
        required=True,
        help="Path to the DFCX export .zip file.",
    )
    args = parser.parse_args()

    intents = parse_dfcx_zip(args.zip_path)
    # Output raw JSON to stdout so the agent can parse it
    print(json.dumps(intents, indent=2))


if __name__ == "__main__":
    main()
