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

"""`cxas redaction` — verify logging redaction against a policy."""

from __future__ import annotations

import argparse  # noqa: TC003
import pathlib
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cxas_scrapi.redaction import RedactionPolicy, RedactionReport


def _load_policy(path: str | None) -> tuple[RedactionPolicy, pathlib.Path]:
    from cxas_scrapi.redaction import find_policy_file, load_policy

    policy_path = find_policy_file(path)
    return load_policy(policy_path), policy_path


def _emit(report: RedactionReport, args: argparse.Namespace) -> None:
    from cxas_scrapi.redaction.report import (
        print_report,
        write_html,
        write_json,
    )

    print_report(report, max_findings=args.max_findings)
    if args.output_json:
        write_json(report, args.output_json)
        print(f"JSON report: {args.output_json}")
    if args.output_html:
        write_html(report, args.output_html)
        print(f"HTML report: {args.output_html}")
    if not report.passed and not args.no_fail:
        sys.exit(1)


def redaction_validate(args: argparse.Namespace) -> None:
    try:
        policy, path = _load_policy(args.policy)
    except Exception as e:
        print(f"Invalid redaction policy: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"{path}: OK ({len(policy.categories)} categories)")
    for c in policy.categories:
        sources = []
        if c.values:
            sources.append(f"{len(c.values)} values")
        if c.patterns:
            sources.append(f"{len(c.patterns)} patterns")
        if c.keys:
            sources.append(f"keys={','.join(c.keys)}")
        print(
            f"  {c.id:<28} {c.action.value:<16} match={c.match.value:<7} "
            f"{'; '.join(sources) or 'no values (never matched)'}"
        )


def redaction_replay(args: argparse.Namespace) -> None:
    from cxas_scrapi.redaction import RedactionReplay
    from cxas_scrapi.redaction.replay import load_conversation_file

    policy, _ = _load_policy(args.policy)
    if not (args.evaluation_run or args.conversation or args.file):
        print(
            "Provide --evaluation-run, --conversation or --file.",
            file=sys.stderr,
        )
        sys.exit(2)
    replay = RedactionReplay(
        policy,
        app_name=args.app_name,
        inspect_template=args.inspect_template,
        deidentify_template=args.deidentify_template,
        mode=args.mode,
    )
    report = None
    if args.evaluation_run:
        report = replay.replay_evaluation_run(
            args.evaluation_run, require=args.require
        )
    if args.conversation:
        r = replay.replay_conversation_ids(
            args.conversation, require=args.require
        )
        report = r if report is None else report.merge(r)
    for f in args.file or []:
        r = replay.replay_conversation(
            load_conversation_file(f), source=f, require=args.require
        )
        report = r if report is None else report.merge(r)
    _emit(report, args)


def redaction_live(args: argparse.Namespace) -> None:
    from cxas_scrapi.redaction import LiveRedactionCheck, load_live_script

    policy, _ = _load_policy(args.policy)
    conversations = load_live_script(args.script)
    if args.name:
        conversations = [c for c in conversations if c.name in args.name]
    check = LiveRedactionCheck(
        args.app_name,
        policy,
        deployment_id=args.deployment_id,
        persist_timeout_s=args.persist_timeout,
    )
    report = check.run_script(
        conversations,
        modality=args.modality,
        base_dir=pathlib.Path(args.script).parent,
    )
    _emit(report, args)


def redaction_variants(args: argparse.Namespace) -> None:
    from cxas_scrapi.redaction import spoken_variants

    for v in spoken_variants(args.value):
        print(v)


def _add_output_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--output-json", help="Write the full report as JSON.")
    p.add_argument("--output-html", help="Write a self-contained HTML report.")
    p.add_argument(
        "--max-findings",
        type=int,
        default=50,
        help="Findings to print in the terminal (default: 50).",
    )
    p.add_argument(
        "--no-fail",
        action="store_true",
        help="Exit 0 even when the policy thresholds are exceeded.",
    )


def _add_policy_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--policy",
        help=(
            "Path to the redaction policy YAML. Defaults to the nearest "
            "redaction_policy.yaml."
        ),
    )


def register(subparsers: argparse._SubParsersAction) -> None:
    """Adds the `redaction` command group to the top-level CLI."""
    parser = subparsers.add_parser(
        "redaction",
        help="Verify logging redaction against a redaction policy.",
        description=(
            "Checks what logging redaction (DLP templates configured in "
            "loggingSettings.redactionConfig) removes from and keeps in "
            "persisted conversations, against a declarative "
            "redaction_policy.yaml."
        ),
    )
    sub = parser.add_subparsers(
        title="redaction commands", dest="redaction_command", required=True
    )

    p_validate = sub.add_parser(
        "validate", help="Validate a redaction policy file."
    )
    p_validate.add_argument(
        "policy", nargs="?", help="Policy path (default: nearest file)."
    )
    p_validate.set_defaults(func=redaction_validate)

    p_replay = sub.add_parser(
        "replay",
        help=(
            "Replay raw eval/conversation transcripts through the app's DLP "
            "templates (offline, no agent calls)."
        ),
        description=(
            "Evaluation runs persist unredacted transcripts. `replay` sends "
            "them through the app's inspect/deidentify templates and checks "
            "the output against the policy."
        ),
    )
    p_replay.add_argument(
        "--app-name",
        help="App resource name (projects/.../locations/.../apps/...).",
    )
    _add_policy_arg(p_replay)
    p_replay.add_argument(
        "--evaluation-run", help="Evaluation run ID or resource name."
    )
    p_replay.add_argument(
        "--conversation",
        nargs="+",
        help="Conversation IDs or resource names to replay.",
    )
    p_replay.add_argument(
        "--file", nargs="+", help="Exported conversation JSON files."
    )
    p_replay.add_argument(
        "--mode",
        choices=["platform", "conversation", "key_aware"],
        default="platform",
        help=(
            "platform: each value on its own (default); conversation: "
            "utterances as one multi-turn dialog; key_aware: field names "
            "passed as context for structured values."
        ),
    )
    p_replay.add_argument(
        "--inspect-template",
        help="Override the inspect template (default: from the app).",
    )
    p_replay.add_argument(
        "--deidentify-template",
        help="Override the deidentify template (default: from the app).",
    )
    p_replay.add_argument(
        "--require",
        nargs="*",
        default=[],
        help="Categories that must appear at least once.",
    )
    _add_output_args(p_replay)
    p_replay.set_defaults(func=redaction_replay)

    p_live = sub.add_parser(
        "live",
        help=(
            "Run scripted sessions and compare live turns with the "
            "persisted, redacted conversation."
        ),
    )
    p_live.add_argument(
        "--app-name",
        required=True,
        help="App resource name (projects/.../locations/.../apps/...).",
    )
    _add_policy_arg(p_live)
    p_live.add_argument(
        "--script",
        required=True,
        help="YAML with `conversations: [{name, turns, variables}]`.",
    )
    p_live.add_argument(
        "--name", nargs="*", help="Only run these conversation names."
    )
    p_live.add_argument("--modality", choices=["text", "audio"], default="text")
    p_live.add_argument("--deployment-id", help="Deployment to target.")
    p_live.add_argument(
        "--persist-timeout",
        type=float,
        default=90.0,
        help="Seconds to wait for the conversation to be persisted.",
    )
    _add_output_args(p_live)
    p_live.set_defaults(func=redaction_live)

    p_variants = sub.add_parser(
        "variants",
        help="Print conversational renderings of a digit value.",
    )
    p_variants.add_argument("value")
    p_variants.set_defaults(func=redaction_variants)
