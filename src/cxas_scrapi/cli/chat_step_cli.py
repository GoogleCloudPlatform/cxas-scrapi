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

"""Programmatic single-turn chat command with JSON output."""

import argparse
import json
import os
import sys
from typing import Any

from cxas_scrapi.core.programmatic_chat import ProgrammaticChatDriver


def _enrich_result(
    result: dict[str, Any],
    driver: ProgrammaticChatDriver,
    args: argparse.Namespace,
) -> None:
    if getattr(args, "with_slots", False):
        full_state = driver.get_full_state()
        result["slot_inspection"] = full_state.get("slot_inspection")

    if getattr(args, "with_log", None) is not None:
        result["sm_log"] = driver.get_sm_log(args.with_log)

    if getattr(args, "with_flow_context", False):
        result["flow_context"] = driver.get_flow_context()

    if getattr(args, "with_trace_report", None) is not None:
        result["trace_report"] = driver.get_trace_report(args.with_trace_report)

    if getattr(args, "with_turns", False):
        result["turns_summary"] = driver.get_turns_summary()

    if getattr(args, "bug", None) is not None:
        result["bug_report"] = driver.report_bug(args.bug)


def _build_inspect_result(
    driver: ProgrammaticChatDriver,
    args: argparse.Namespace,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "session_id": driver.session_id,
        "state": driver.get_full_state(),
        "inspect_only": True,
    }
    _enrich_result(result, driver, args)
    return result


def chat_step(args: argparse.Namespace) -> None:
    """Handle the `cxas chat-step` command."""
    inspect_only = getattr(args, "inspect_only", False)
    if not inspect_only and not args.message:
        print("Error: --message is required unless --inspect-only is set.", file=sys.stderr)
        sys.exit(1)

    session_id = None
    initial_turn_count = 0
    initial_variable_state = None

    if args.session_file and os.path.exists(args.session_file):
        try:
            with open(args.session_file) as f:
                session_data = json.load(f)
            session_id = session_data.get("session_id")
            initial_turn_count = session_data.get("turn_count", 0)
            initial_variable_state = session_data.get("variable_state")
        except (json.JSONDecodeError, OSError) as e:
            print(f"Failed to load session file: {e}", file=sys.stderr)
            sys.exit(1)

    if inspect_only and not session_id:
        print("Error: --inspect-only requires --session-file with existing session.", file=sys.stderr)
        sys.exit(1)

    try:
        driver = ProgrammaticChatDriver(
            app_name=args.app_name,
            channel=getattr(args, "channel", None),
            deployment_id=getattr(args, "deployment_id", None),
            include_trace=getattr(args, "with_trace", False),
            include_metrics=getattr(args, "with_metrics", False),
            session_id=session_id,
            initial_turn_count=initial_turn_count,
            initial_variable_state=initial_variable_state,
        )
    except Exception as e:
        print(f"Failed to create session: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        if inspect_only:
            result = _build_inspect_result(driver, args)
        else:
            result = driver.step(args.message)
            _enrich_result(result, driver, args)

        print(json.dumps(result, indent=2, default=str))

        if args.session_file and not inspect_only:
            session_state = {
                "session_id": driver.session_id,
                "turn_count": result["state"]["turn_count"],
                "app_name": args.app_name,
                "variable_state": driver._session._variable_state,
            }
            try:
                with open(args.session_file, "w") as f:
                    json.dump(session_state, f, indent=2, default=str)
            except OSError as e:
                print(f"Failed to save session file: {e}", file=sys.stderr)

    except Exception as e:
        print(f"Chat step failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        driver.close()


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the `chat-step` subcommand."""
    p = subparsers.add_parser(
        "chat-step",
        help="Single-turn programmatic chat with JSON output.",
        description=(
            "Send one message to a CES agent and receive a structured JSON response. "
            "Use --session-file for multi-turn conversations across invocations."
        ),
    )
    p.add_argument(
        "--app-name",
        required=True,
        help="Full CES app resource name.",
    )
    p.add_argument(
        "--message", "-m",
        default=None,
        help="User message to send.",
    )
    p.add_argument(
        "--session-file",
        default=None,
        help="Path to session state file for multi-turn conversations.",
    )
    p.add_argument(
        "--channel",
        default=None,
        help="Channel identifier (e.g., web, sms).",
    )
    p.add_argument(
        "--deployment-id",
        default=None,
        help="Deployment ID to target.",
    )
    p.add_argument(
        "--with-trace",
        action="store_true",
        default=False,
        help="Include normalized trace in output.",
    )
    p.add_argument(
        "--with-metrics",
        action="store_true",
        default=False,
        help="Include per-turn metrics in output.",
    )
    p.add_argument(
        "--with-slots",
        action="store_true",
        default=False,
        help="Include deep slot inspection in output.",
    )
    p.add_argument(
        "--with-log",
        nargs="?",
        const="INFO",
        default=None,
        metavar="LEVEL",
        help="Include SM event log (levels: debug, info, warn, error; default: info).",
    )
    p.add_argument(
        "--with-flow-context",
        action="store_true",
        default=False,
        help="Include multi-flow context in output.",
    )
    p.add_argument(
        "--with-trace-report",
        choices=["json", "md", "text", "html"],
        default=None,
        metavar="FMT",
        help="Include formatted trace report in output.",
    )
    p.add_argument(
        "--with-turns",
        action="store_true",
        default=False,
        help="Include conversation turn summaries in output.",
    )
    p.add_argument(
        "--bug",
        default=None,
        metavar="REASON",
        help="Report a bug for this conversation.",
    )
    p.add_argument(
        "--inspect-only",
        action="store_true",
        default=False,
        help="Inspect existing session state without sending a message.",
    )
    p.set_defaults(func=chat_step)
