"""Argparse subcommand handlers for `cxas trace`.

The handlers are intentionally thin: they only build a `Traces` instance and
print results. All non-trivial logic lives in `core/traces.py` and
`utils/trace_*.py`. Output formatting uses Rich tables for the human-friendly
default (`list`, `stats`) and plain JSON / Markdown / text for everything else.
"""

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
import csv
import io
import json
import logging
import platform
import subprocess
import sys

from rich.console import Console
from rich.table import Table

from cxas_scrapi.core.traces import Traces

logger = logging.getLogger(__name__)
console = Console()


def add_trace_args(subparser: argparse.ArgumentParser) -> None:
    """Adds the common trace flags shared by every subcommand."""
    subparser.add_argument(
        "--app-name",
        required=True,
        help="The CXAS App ID (projects/.../locations/.../apps/...).",
    )
    subparser.add_argument(
        "--app-dir",
        default=".",
        help=(
            "Path to the pulled app directory (used to read app.json and "
            "environment.json). Defaults to current directory."
        ),
    )
    subparser.add_argument(
        "--env-file",
        help="Explicit path to an environment.json file.",
    )
    subparser.add_argument(
        "--environment",
        help=(
            "Named environment, resolved to <app-dir>/environment.<name>.json."
        ),
    )
    subparser.add_argument(
        "--config",
        help=(
            "Path to a trace.yaml config file. Defaults to "
            "./.cxas/trace.yaml or ~/.cxas/trace.yaml."
        ),
    )


def _build_traces(args: argparse.Namespace) -> Traces:
    return Traces(
        app_name=args.app_name,
        app_dir=getattr(args, "app_dir", "."),
        env_file=getattr(args, "env_file", None),
        environment=getattr(args, "environment", None),
        trace_config_path=getattr(args, "config", None),
    )


# --------------------------------- list -------------------------------------


def trace_list(args: argparse.Namespace) -> None:
    try:
        traces = _build_traces(args)
        rows = traces.list(
            time_filter=args.time_filter,
            source_filter=args.source,
            channel_filter=args.channel,
            limit=args.limit,
        )
    except Exception as e:
        print(f"Failed to list conversations: {e}", file=sys.stderr)
        sys.exit(1)

    fmt = args.format
    if fmt == "json":
        print(json.dumps(rows, indent=2, default=str))
        return
    if fmt == "csv":
        if not rows:
            return
        writer = csv.DictWriter(sys.stdout, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        return
    # default: table
    table = Table(title=f"Conversations ({len(rows)})")
    for col in (
        "id",
        "source",
        "channel",
        "start_time",
        "end_time",
        "ces_url",
    ):
        table.add_column(col)
    for r in rows:
        table.add_row(
            *(
                str(r.get(c) or "")
                for c in (
                    "id",
                    "source",
                    "channel",
                    "start_time",
                    "end_time",
                    "ces_url",
                )
            )
        )
    console.print(table)


# ---------------------------------- get -------------------------------------


def trace_get(args: argparse.Namespace) -> None:
    try:
        traces = _build_traces(args)
        out = traces.get_report(
            args.conversation_id,
            fmt=args.format,
            with_logs=args.with_logs,
            log_level=args.log_level,
            with_audio=args.with_audio,
            with_analysis=args.with_analysis,
            with_triage=args.with_triage,
        )
    except Exception as e:
        print(f"Failed to get trace: {e}", file=sys.stderr)
        sys.exit(1)

    if args.out:
        with open(args.out, "w") as f:
            f.write(out)
        print(f"Wrote {args.out}")
    else:
        print(out)


# ---------------------------------- logs ------------------------------------


def trace_logs(args: argparse.Namespace) -> None:
    try:
        traces = _build_traces(args)
        rows = traces.get_logs(args.conversation_id, level=args.level)
    except Exception as e:
        print(f"Failed to fetch logs: {e}", file=sys.stderr)
        sys.exit(1)

    if isinstance(rows, str):
        print(rows)
        return

    if args.format == "json":
        print(json.dumps(rows, indent=2, default=str))
        return

    for r in rows:
        print(f"{r.get('timestamp')}  {r.get('severity')}  {r.get('message')}")


# ---------------------------------- audio -----------------------------------


def trace_audio_download(args: argparse.Namespace) -> None:
    try:
        traces = _build_traces(args)
        path = traces.download_audio(args.conversation_id, dest_dir=args.out)
    except Exception as e:
        print(f"Audio download failed: {e}", file=sys.stderr)
        sys.exit(1)
    if path:
        print(f"Downloaded to: {path}")
    else:
        print("No audio URI could be resolved.", file=sys.stderr)
        sys.exit(2)


def trace_audio_analyze(args: argparse.Namespace) -> None:
    metrics = (
        [m.strip() for m in args.metric.split(",")] if args.metric else None
    )
    try:
        traces = _build_traces(args)
        results = traces.analyze_audio(args.conversation_id, metrics=metrics)
    except Exception as e:
        print(f"Audio analysis failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(results, indent=2, default=str))


# --------------------------------- triage -----------------------------------


def trace_triage(args: argparse.Namespace) -> None:
    metrics = (
        [m.strip() for m in args.metric.split(",")] if args.metric else None
    )
    try:
        traces = _build_traces(args)
        results = traces.triage(args.conversation_id, metrics=metrics)
    except Exception as e:
        print(f"Triage failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(results, indent=2, default=str))


# --------------------------------- replay -----------------------------------


def trace_replay(args: argparse.Namespace) -> None:
    try:
        traces = _build_traces(args)
        result = traces.replay(args.conversation_id, diff=args.diff)
    except Exception as e:
        print(f"Replay failed: {e}", file=sys.stderr)
        sys.exit(1)
    if args.format == "json":
        print(json.dumps(result, indent=2, default=str))
    elif "diff" in result:
        print("# Replay diff\n```diff")
        print(result["diff"] or "(no differences)")
        print("```")
    else:
        print(json.dumps(result, indent=2, default=str))


# ---------------------------------- stats -----------------------------------


def trace_stats(args: argparse.Namespace) -> None:
    try:
        traces = _build_traces(args)
        stats = traces.aggregate_stats(
            time_filter=args.time_filter,
            source_filter=args.source,
            channel_filter=args.channel,
            limit=args.limit,
        )
    except Exception as e:
        print(f"Stats failed: {e}", file=sys.stderr)
        sys.exit(1)

    if args.format == "json":
        out = json.dumps(stats, indent=2, default=str)
    else:
        out = _stats_to_markdown(stats)

    if args.out:
        with open(args.out, "w") as f:
            f.write(out)
        print(f"Wrote {args.out}")
    else:
        print(out)


def _stats_to_markdown(stats: dict) -> str:
    """Renders aggregate stats as Markdown with ASCII bar charts and units."""
    buf = io.StringIO()
    total = stats.get("total", 0)
    buf.write(f"# Trace stats — last {stats['time_filter']}\n\n")
    buf.write("## Summary\n\n")
    buf.write("| Metric | Value |\n|---|---|\n")
    buf.write(f"| Conversations analyzed | {total} |\n")
    sr = stats.get("success_rate_no_transfer")
    if sr is not None:
        buf.write(
            f"| Completed without escalation/transfer | "
            f"{sr:.1%} ({int(sr * total)} of {total}) |\n"
        )
    dur = stats.get("duration_seconds", {})

    def _fmt_sec(v):
        return "n/a" if v is None else f"{v:.2f} s"

    buf.write(
        f"| Conversation duration p50 / p95 / median | "
        f"{_fmt_sec(dur.get('p50'))} / "
        f"{_fmt_sec(dur.get('p95'))} / "
        f"{_fmt_sec(dur.get('median'))} |\n"
    )
    buf.write("\n")

    buf.write(
        "**Legend:** `LIVE` = deployed-agent traffic · `SIMULATOR` = "
        "build/preview · `EVAL` = evaluation runs · "
        "channel = derived from `Conversation.input_types`.\n\n"
    )

    buf.write("## Conversations by source\n\n")
    buf.write(
        _bar_block(
            stats.get("per_source", {}),
            unit="conversation",
            total=total,
        )
    )

    buf.write("\n## Conversations by channel\n\n")
    buf.write(
        _bar_block(
            stats.get("per_channel", {}),
            unit="conversation",
            total=total,
        )
    )

    buf.write("\n## Top tool calls\n\n")
    buf.write(
        "_Counts include every invocation across every conversation; "
        "a single conversation may invoke a tool multiple times._\n\n"
    )
    buf.write(
        _bar_block(
            dict(stats.get("top_tools", [])),
            unit="call",
        )
    )

    buf.write("\n## Top transfer targets\n\n")
    targets = dict(stats.get("top_transfer_targets", []))
    if targets:
        buf.write(_bar_block(targets, unit="transfer"))
    else:
        buf.write("_(no transfers in this window)_\n")
    return buf.getvalue()


def _bar_block(
    counts: dict,
    unit: str = "",
    total: int | None = None,
    width: int = 30,
) -> str:
    """Renders {label: count} as an ASCII bar block, scaled to the max value.

    Optionally shows percentage of `total` next to each bar.
    """
    if not counts:
        return "_(no data)_\n"
    items = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    peak = max(v for _, v in items) or 1
    label_w = min(40, max(len(str(k)) for k, _ in items))
    out: list[str] = ["```"]
    for label, n in items:
        bar = "█" * max(1, int(round(width * n / peak)))
        pct = ""
        if total:
            pct = f"  ({n / total:.0%})"
        unit_label = f" {unit}{'s' if n != 1 else ''}"
        out.append(f"{str(label).ljust(label_w)}  {bar}  {n}{unit_label}{pct}")
    out.append("```")
    return "\n".join(out) + "\n"


# --------------------------------- bundle -----------------------------------


def trace_bundle(args: argparse.Namespace) -> None:
    try:
        traces = _build_traces(args)
        path = traces.bundle(
            args.conversation_id,
            out_path=args.out,
            with_logs=not args.no_logs,
            with_audio=not args.no_audio,
            with_analysis=args.with_analysis,
            with_triage=args.with_triage,
        )
    except Exception as e:
        print(f"Bundle failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Wrote bundle to: {path}")


# ------------------------------- bug-report ---------------------------------


def trace_bug_report(args: argparse.Namespace) -> None:
    try:
        traces = _build_traces(args)
        info = traces.report_bug(
            args.conversation_id,
            reason=args.reason,
            severity=args.severity,
        )
    except Exception as e:
        print(f"Bug report failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(info, indent=2, default=str))


# --------------------------------- open ------------------------------------


def trace_open(args: argparse.Namespace) -> None:
    try:
        traces = _build_traces(args)
        url = traces.console_url(args.conversation_id)
    except Exception as e:
        print(f"Open failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(url)
    if platform.system() == "Darwin":
        try:
            subprocess.run(["open", url], check=False)
        except Exception:
            pass


# ----------------------------- argparse wiring ------------------------------


def register(subparsers: argparse._SubParsersAction) -> None:
    """Adds the `trace` subcommand tree to the top-level CLI."""
    parser_trace = subparsers.add_parser(
        "trace",
        help=(
            "Inspect, analyze, and report on individual conversations "
            "(deployed/build/eval)."
        ),
    )
    trace_subparsers = parser_trace.add_subparsers(
        title="trace commands", dest="trace_command", required=True
    )

    # list
    p_list = trace_subparsers.add_parser(
        "list", help="List conversations filtered by time/source/channel."
    )
    add_trace_args(p_list)
    p_list.add_argument("--time-filter", default="7d")
    p_list.add_argument(
        "--source",
        choices=["LIVE", "SIMULATOR", "EVAL"],
        help="Filter by conversation source.",
    )
    p_list.add_argument(
        "--channel",
        choices=["TEXT", "AUDIO", "MULTIMODAL", "OTHER"],
        help="Filter by input channel (client-side over input_types).",
    )
    p_list.add_argument("--limit", type=int)
    p_list.add_argument(
        "--format", choices=["table", "json", "csv"], default="table"
    )
    p_list.set_defaults(func=trace_list)

    # get
    p_get = trace_subparsers.add_parser(
        "get", help="Fetch a conversation and render a trace report."
    )
    add_trace_args(p_get)
    p_get.add_argument("conversation_id")
    p_get.add_argument(
        "--format",
        choices=["json", "md", "markdown", "text", "html"],
        default="md",
    )
    p_get.add_argument("--with-logs", action="store_true")
    p_get.add_argument(
        "--log-level",
        help="Cloud Logging severity threshold (default WARNING).",
    )
    p_get.add_argument("--with-audio", action="store_true")
    p_get.add_argument("--with-analysis", action="store_true")
    p_get.add_argument("--with-triage", action="store_true")
    p_get.add_argument("--out", help="File path to write the report to.")
    p_get.set_defaults(func=trace_get)

    # logs
    p_logs = trace_subparsers.add_parser(
        "logs", help="Fetch Cloud Logging entries for a conversation."
    )
    add_trace_args(p_logs)
    p_logs.add_argument("conversation_id")
    p_logs.add_argument(
        "--level",
        default="WARNING",
        help="Severity threshold (default WARNING).",
    )
    p_logs.add_argument("--format", choices=["text", "json"], default="text")
    p_logs.set_defaults(func=trace_logs)

    # audio
    p_audio = trace_subparsers.add_parser(
        "audio", help="Audio recording subcommands."
    )
    audio_subparsers = p_audio.add_subparsers(
        title="audio commands", dest="audio_command", required=True
    )

    p_audio_download = audio_subparsers.add_parser(
        "download", help="Download a conversation's audio recording."
    )
    add_trace_args(p_audio_download)
    p_audio_download.add_argument("conversation_id")
    p_audio_download.add_argument(
        "--out",
        help="Destination directory (default: ./.cxas/audio/).",
    )
    p_audio_download.set_defaults(func=trace_audio_download)

    p_audio_analyze = audio_subparsers.add_parser(
        "analyze",
        help="Run configured Gemini audio metrics over the recording.",
    )
    add_trace_args(p_audio_analyze)
    p_audio_analyze.add_argument("conversation_id")
    p_audio_analyze.add_argument(
        "--metric",
        help=(
            "Comma-separated metric names from trace.yaml gemini.audio_metrics."
        ),
    )
    p_audio_analyze.set_defaults(func=trace_audio_analyze)

    # triage
    p_triage = trace_subparsers.add_parser(
        "triage",
        help="Run configured Gemini text-only triage over the transcript.",
    )
    add_trace_args(p_triage)
    p_triage.add_argument("conversation_id")
    p_triage.add_argument(
        "--metric",
        help=(
            "Comma-separated metric names from trace.yaml "
            "gemini.triage_metrics."
        ),
    )
    p_triage.set_defaults(func=trace_triage)

    # replay
    p_replay = trace_subparsers.add_parser(
        "replay",
        help="Replay user inputs against the current agent and diff.",
    )
    add_trace_args(p_replay)
    p_replay.add_argument("conversation_id")
    p_replay.add_argument(
        "--no-diff",
        dest="diff",
        action="store_false",
        help="Skip the diff and only print the replayed turns.",
    )
    p_replay.add_argument("--format", choices=["md", "json"], default="md")
    p_replay.set_defaults(func=trace_replay, diff=True)

    # stats
    p_stats = trace_subparsers.add_parser(
        "stats", help="Aggregate stats over recent conversations."
    )
    add_trace_args(p_stats)
    p_stats.add_argument("--time-filter", default="7d")
    p_stats.add_argument("--source", choices=["LIVE", "SIMULATOR", "EVAL"])
    p_stats.add_argument(
        "--channel", choices=["TEXT", "AUDIO", "MULTIMODAL", "OTHER"]
    )
    p_stats.add_argument("--limit", type=int, default=200)
    p_stats.add_argument("--format", choices=["md", "json"], default="md")
    p_stats.add_argument("--out", help="Write stats to file instead of stdout.")
    p_stats.set_defaults(func=trace_stats)

    # bundle
    p_bundle = trace_subparsers.add_parser(
        "bundle",
        help="Zip transcript + logs + audio + report into a single archive.",
    )
    add_trace_args(p_bundle)
    p_bundle.add_argument("conversation_id")
    p_bundle.add_argument("--out", required=True, help="Output zip path.")
    p_bundle.add_argument("--no-logs", action="store_true")
    p_bundle.add_argument("--no-audio", action="store_true")
    p_bundle.add_argument("--with-analysis", action="store_true")
    p_bundle.add_argument("--with-triage", action="store_true")
    p_bundle.set_defaults(func=trace_bundle)

    # bug-report
    p_bug = trace_subparsers.add_parser(
        "bug-report",
        help=(
            "Flag a conversation as a platform bug; uploads bundle to the "
            "configured GCS bucket."
        ),
    )
    add_trace_args(p_bug)
    p_bug.add_argument("conversation_id")
    p_bug.add_argument("--reason", required=True)
    p_bug.add_argument(
        "--severity",
        choices=["low", "medium", "high"],
        default="medium",
    )
    p_bug.set_defaults(func=trace_bug_report)

    # open
    p_open = trace_subparsers.add_parser(
        "open", help="Print (and on macOS, open) the CES Console URL."
    )
    add_trace_args(p_open)
    p_open.add_argument("conversation_id")
    p_open.set_defaults(func=trace_open)
