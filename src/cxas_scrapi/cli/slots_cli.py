"""CLI subcommands for deep slot machine inspection."""

import argparse
import json
import sys
from typing import Any

from cxas_scrapi.core.traces import Traces
from cxas_scrapi.utils.slot_inspector import SlotInspector


def _build_traces(args: argparse.Namespace) -> Traces:
    """Construct a Traces instance from CLI args."""
    return Traces(app_name=args.app_name)


def slots_inspect(args: argparse.Namespace) -> None:
    """Handle `cxas slots inspect CONVERSATION_ID`."""
    try:
        traces = _build_traces(args)
        normalized = traces.get_normalized(args.conversation_id)
    except Exception as e:
        print(f"Failed to fetch trace: {e}", file=sys.stderr)
        sys.exit(1)

    result = SlotInspector.inspect_from_trace(
        normalized, turn=getattr(args, "at_turn", None)
    )

    category = getattr(args, "category", None)
    if category:
        if category in result.get("categories", {}):
            result = {
                "summary": result["summary"],
                "categories": {category: result["categories"][category]},
                "slot_dag": result["slot_dag"],
            }
        else:
            available = sorted(result.get("categories", {}).keys())
            print(
                f"Unknown category '{category}'. Available: {available}",
                file=sys.stderr,
            )
            sys.exit(1)

    print(json.dumps(result, indent=2, default=str))


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the `slots` subcommand with its sub-subcommands."""
    p = subparsers.add_parser(
        "slots",
        help="Deep slot machine inspection.",
        description="Inspect slot machine state from conversation traces.",
    )
    slots_sub = p.add_subparsers(
        title="slots commands", dest="slots_command", required=True
    )

    p_inspect = slots_sub.add_parser(
        "inspect",
        help="Inspect slots from a conversation trace.",
    )
    p_inspect.add_argument(
        "--app-name", required=True, help="Full CES app resource name."
    )
    p_inspect.add_argument(
        "conversation_id", help="Conversation ID to inspect."
    )
    p_inspect.add_argument(
        "--at-turn",
        type=int,
        default=None,
        help="Inspect slot state at a specific turn number.",
    )
    p_inspect.add_argument(
        "--category",
        default=None,
        help="Show only a specific category (e.g., core_data, configuration).",
    )
    p_inspect.set_defaults(func=slots_inspect)
