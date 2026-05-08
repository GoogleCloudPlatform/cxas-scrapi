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

"""Capture full conversation transcripts to build or verify golden evals.

Reads user turns from golden YAML files and replays them against the live agent,
capturing the full transcript including tool calls and agent transfers.

Usage:
  python scripts/capture-golden-transcripts.py --eval golden_auth_api_failure
  python scripts/capture-golden-transcripts.py --all
  python scripts/capture-golden-transcripts.py --all --channel audio
"""

import argparse
import json
import os
import sys
import uuid
import yaml
from typing import Any, Dict, List, Optional

from cxas_scrapi.core.sessions import Sessions
from config import load_app_name, get_project_path

GOLDENS_DIR = get_project_path("evals", "goldens")
TRANSCRIPTS_DIR = get_project_path("evals", "goldens", "transcripts")


def load_golden_scripts():
    """Load user turns and session parameters from golden YAML files."""
    scripts = {}
    for fname in sorted(os.listdir(GOLDENS_DIR)):
        if not fname.endswith(".yaml"):
            continue
        with open(os.path.join(GOLDENS_DIR, fname)) as f:
            data = yaml.safe_load(f)
        if not data or "conversations" not in data:
            continue
        common_params = data.get("common_session_parameters", {})
        for conv in data["conversations"]:
            name = conv["conversation"]
            params = dict(common_params)
            params.update(conv.get("session_parameters", {}))
            user_turns = [t["user"] for t in conv.get("turns", []) if "user" in t]
            scripts[name] = {"params": params, "turns": user_turns}
    return scripts


def parse_response_deduped(response) -> Dict[str, Any]:
    """Parse response, avoiding duplicate text from diagnostic info."""
    agent_texts = []
    tool_calls = []
    agent_transfer = None
    session_ended = False

    for output in response.outputs:
        if hasattr(output, "text") and output.text:
            agent_texts.append(output.text)

        tc_msg = getattr(output, "tool_calls", None)
        if tc_msg and hasattr(tc_msg, "tool_calls"):
            for tc in tc_msg.tool_calls:
                tool_name = getattr(tc, "display_name", "") or getattr(tc, "tool", "")
                args = Sessions._expand_pb_struct(tc.args) if hasattr(tc, "args") else {}
                tool_calls.append({"action": tool_name, "args": args})
                if "end_session" in (tool_name or ""):
                    session_ended = True

        diagnostic_info = getattr(output, "diagnostic_info", None)
        if diagnostic_info and hasattr(diagnostic_info, "messages"):
            for message in diagnostic_info.messages:
                for chunk in getattr(message, "chunks", []):
                    fc = getattr(chunk, "function_call", None)
                    if fc:
                        tc_name = getattr(fc, "name", "")
                        tc_args = Sessions._expand_pb_struct(fc.args) if hasattr(fc, "args") else {}
                        if tc_name and not any(t["action"] == tc_name for t in tool_calls):
                            tool_calls.append({"action": tc_name, "args": tc_args})
                            if "end_session" in tc_name:
                                session_ended = True

                    fr = getattr(chunk, "function_response", None)
                    if fr:
                        fr_name = getattr(fr, "name", "")
                        fr_resp = Sessions._expand_pb_struct(fr.response) if hasattr(fr, "response") else {}
                        tool_calls.append({"action": f"_response:{fr_name}", "args": {}, "response": fr_resp})

                actions = getattr(message, "actions", None)
                if actions and hasattr(actions, "transfer_to_agent") and actions.transfer_to_agent:
                    agent_transfer = actions.transfer_to_agent

    agent_text = " ".join(agent_texts).strip() if agent_texts else ""

    return {
        "agent_text": agent_text,
        "tool_calls": [t for t in tool_calls if not t["action"].startswith("_response:")],
        "tool_responses": [t for t in tool_calls if t["action"].startswith("_response:")],
        "agent_transfer": agent_transfer,
        "session_ended": session_ended,
    }


def capture(name: str, scripts: dict, app_name: str, channel: str = "text"):
    """Capture a single golden transcript."""
    config = scripts[name]
    sessions = Sessions(app_name)
    session_id = str(uuid.uuid4())
    params = config["params"]
    user_turns = config["turns"]

    transcript = []
    first_turn = True

    for user_text in user_turns:
        kwargs = {"session_id": session_id, "text": user_text}
        if channel == "audio":
            kwargs["modality"] = "audio"
        if first_turn and params:
            kwargs["variables"] = params
            first_turn = False
        else:
            first_turn = False

        try:
            response = sessions.run(**kwargs)
        except Exception as e:
            print(f"    ERROR: {e}")
            break

        parsed = parse_response_deduped(response)

        turn = {"user": user_text}
        if parsed["agent_text"]:
            turn["agent"] = parsed["agent_text"]
        if parsed["tool_calls"]:
            turn["tool_calls"] = parsed["tool_calls"]
        if parsed["agent_transfer"]:
            if "tool_calls" not in turn:
                turn["tool_calls"] = []
            turn["tool_calls"].append({"action": "transfer_to_agent", "agent": parsed["agent_transfer"]})

        transcript.append(turn)

        agent_short = parsed["agent_text"][:80] if parsed["agent_text"] else "(no text)"
        print(f"    [{len(transcript)}] User: {user_text[:60]}")
        print(f"        Agent: {agent_short}")
        for tc in parsed["tool_calls"]:
            print(f"        Tool: {tc['action'].split('/')[-1]}")
        if parsed["agent_transfer"]:
            print(f"        Transfer: {parsed['agent_transfer'].split('/')[-1]}")
        if parsed["session_ended"]:
            print(f"        [SESSION ENDED]")
            break

    return {"name": name, "session_parameters": params, "transcript": transcript}


def main():
    try:
        import cxas_scrapi  # noqa: F401
    except ImportError:
        print("Error: cxas-scrapi not installed. Activate venv (source .venv/bin/activate) and install cxas-scrapi first.")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Capture golden transcripts from live agent")
    parser.add_argument("--eval", action="append", default=None, help="Specific golden(s) to capture")
    parser.add_argument("--all", action="store_true", help="Capture all goldens")
    parser.add_argument("--channel", default="text", choices=["text", "audio"])
    args = parser.parse_args()

    scripts = load_golden_scripts()
    if not scripts:
        print("No golden YAML files found in evals/goldens/")
        return

    app_name = load_app_name()
    os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)

    names = list(scripts.keys())
    if args.eval:
        names = [n for n in names if n in args.eval]
    elif not args.all:
        print(f"Available goldens: {', '.join(sorted(scripts.keys()))}")
        parser.print_help()
        return

    print(f"Capturing {len(names)} golden transcript(s) ({args.channel} mode)...")
    print(f"App: {app_name}\n")

    results = []
    for name in names:
        print(f"--- {name} ---")
        result = capture(name, scripts, app_name, args.channel)
        results.append(result)
        out_path = os.path.join(TRANSCRIPTS_DIR, f"{name}.json")
        with open(out_path, "w") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"    Saved: {out_path}\n")

    print(f"Done. {len(results)} transcript(s) saved to {TRANSCRIPTS_DIR}")


if __name__ == "__main__":
    main()
