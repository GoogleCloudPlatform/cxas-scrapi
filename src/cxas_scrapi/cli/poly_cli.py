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

"""Argparse handlers for ``cxas poly``.

Drives the Polymorphism Engine from the command line: compile base agent
projects into channel-optimized variants, scaffold adapter cards, validate
and explain adapter cards, and show human/JSON diffs of what an adapter
changes.  All error paths render clean, rule-ID-formatted messages — never a
Python traceback.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console

from cxas_scrapi.poly.diagnostics import (
    ExplainedIssue,
    ValidationExplanationReport,
    build_validation_explanation_report,
)
from cxas_scrapi.poly.diffing import build_diff_report
from cxas_scrapi.poly.engine import (
    CompilationError,
    PolymorphismEngine,
)
from cxas_scrapi.poly.models import AdapterCard
from cxas_scrapi.poly.readiness import build_readiness_report
from cxas_scrapi.poly.scaffold import (
    DEFAULT_DISPLAY_NAME_TEMPLATE,
    ScaffoldOptions,
    build_scaffold_plan,
    split_channels,
    write_scaffold_plan,
)
from cxas_scrapi.poly.validators import (
    validate_adapter_card,
    validate_all_adapters,
)

console = Console()


def _counts(issues: List[dict]) -> Tuple[int, int]:
    """Return ``(errors, warnings)`` for a list of issue dicts."""
    errors = sum(1 for i in issues if i.get("severity") == "error")
    warnings = sum(1 for i in issues if i.get("severity") == "warning")
    return errors, warnings


def _print_issues(issues: List[dict]) -> int:
    """Print issue dicts; return the number of errors."""
    errors = 0
    for issue in sorted(
        issues, key=lambda i: (i.get("severity", ""), i.get("rule_id", ""))
    ):
        sev = issue.get("severity", "error")
        rule_id = issue.get("rule_id", "?")
        path = issue.get("path", "")
        msg = issue.get("message", "")
        if sev == "error":
            errors += 1
            color = "red"
            label = "ERROR"
        elif sev == "warning":
            color = "yellow"
            label = "WARN "
        else:
            color = "cyan"
            label = "INFO "
        console.print(f"  [{color}]{label}[/{color}] [{rule_id}] {path}: {msg}")
    return errors


def _load_engine(app_dir: str) -> PolymorphismEngine:
    engine = PolymorphismEngine(app_dir)
    engine.load_base_project()
    engine.load_adapter_cards()
    return engine


def _all_issues(engine: PolymorphismEngine, app_dir: str) -> List[dict]:
    """Parse errors plus validation issues for every loaded adapter."""
    cards = [c for c, _ in engine.adapter_cards]
    return list(engine.adapter_errors) + validate_all_adapters(cards, app_dir)


def _should_fail(errors: int, warnings: int, strict: bool) -> bool:
    """Return whether a command should exit non-zero for issue counts."""
    return bool(errors or (strict and warnings))


def _print_explanation_report(
    report: ValidationExplanationReport,
) -> None:
    """Render doctor/validate explanation output for humans."""
    if not report.issues:
        console.print(
            f"[green]All {report.cards} adapter card(s) valid.[/green]"
        )
        console.print("No doctor findings.")
        return

    console.print(
        f"Doctor checked {report.cards} adapter card(s): "
        f"{report.errors} error(s), {report.warnings} warning(s)\n"
    )
    for issue in report.issues:
        _print_explained_issue(issue)


def _print_explained_issue(issue: ExplainedIssue) -> None:
    """Render one enriched validation issue."""
    color = "red" if issue.severity == "error" else "yellow"
    label = "ERROR" if issue.severity == "error" else "WARN "
    console.print(
        f"[{color}]{label}[/{color}] [{issue.rule_id}] "
        f"{issue.adapter_path}"
    )
    if issue.field_path:
        console.print(f"  field: {issue.field_path}")
    if issue.related_paths:
        console.print(f"  look at: {', '.join(issue.related_paths)}")
    console.print(f"  what failed: {issue.message}")
    console.print(f"  why: {issue.why_it_failed}")
    console.print(f"  likely fix: {issue.likely_fix}\n")


def _print_diff_report(report: Dict[str, Any]) -> None:
    """Render the shared diff report in a compact reviewer-friendly format."""
    summary = report["summary"]
    console.print(
        f"[bold]Channel: {report['channel']}[/bold]   "
        f"(adapter: {report['adapter_path']})"
    )
    console.print(
        "Summary: "
        f"{summary['agents_touched']} agent(s), "
        f"{summary['instruction_diffs']} instruction diff(s), "
        f"{summary['tools_added']} tool add(s), "
        f"{summary['tools_removed']} tool remove(s), "
        f"{summary['callbacks_added']} callback(s), "
        f"{summary['evaluations_added']} eval merge(s)\n"
    )

    for agent in report["agents"]:
        console.print(
            f"[bold cyan]{agent['path']}[/bold cyan] "
            f"({agent['display_name']})"
        )
        for diff in agent["instruction_diffs"]:
            mode = diff["mode"]
            if mode == "replace_section":
                console.print(
                    "  instruction: "
                    f"~ replace_section <{diff['section_tag']}> "
                    f"({diff['path']})"
                )
            else:
                console.print(
                    f"  instruction: + {diff['line_count']} line(s) "
                    f"{mode} ({diff['path']})"
                )
            for line in diff["preview"]:
                console.print(f"    [green]+ {line}[/green]")
            if diff["line_count"] > len(diff["preview"]):
                console.print("    [green]+ ...[/green]")

        tools = agent["tools"]
        if tools["added"] or tools["removed"]:
            console.print(
                "  tools "
                f"({tools['before_count']} -> {tools['after_count']}):"
            )
            for tool in tools["added"]:
                console.print(f"    [green]+ {tool}[/green]")
            for tool in tools["removed"]:
                console.print(f"    [red]- {tool}[/red]")

        model = agent["model"]
        if model is not None:
            console.print(
                "  modelSettings.model: "
                f"{model['before'] or '(default)'} -> "
                f"[green]{model['after']}[/green]"
            )

        for callback in agent["callbacks_added"]:
            desc = callback["description"] or callback["python_code"]
            console.print(
                f"  callbacks: [green]+ {callback['type']}[/green] "
                f"({desc})"
            )

    if report["tool_definitions_added"]:
        console.print("\n[bold cyan]tools/[/bold cyan]")
        for tool in report["tool_definitions_added"]:
            console.print(
                f"  [green]+ {tool['display_name']}[/green] "
                f"({tool['tool_type']}, {tool['source_dir']})"
            )

    merges = report["evaluation_merges"]
    for label, names in (
        ("evaluations", merges["evaluations"]),
        ("evaluationExpectations", merges["evaluation_expectations"]),
        ("evaluationDatasets", merges["evaluation_datasets"]),
    ):
        if names:
            console.print(f"\n[bold cyan]{label}/[/bold cyan]")
            console.print(
                f"  [green]+ {len(names)} item(s):[/green] "
                f"{', '.join(names)}"
            )

    if report["gecx_config_overlay"]:
        console.print(
            "\n[bold cyan]gecx-config.json (channel config)[/bold cyan]"
        )
        for key, value in sorted(report["gecx_config_overlay"].items()):
            console.print(f"  [green]~ {key}: {value}[/green]")

    if report["deployment"]:
        console.print("\n[bold cyan]gecx-config.json (deployment)[/bold cyan]")
        for key, value in report["deployment"].items():
            console.print(f"  [green]+ {key}: {value}[/green]")


def _print_readiness_report(report: Dict[str, Any]) -> None:
    """Render a launch-readiness report for human review."""
    summary = report["summary"]
    console.print(
        "[bold]Poly readiness[/bold]   "
        f"{summary['ready']} ready, "
        f"{summary['attention']} attention, "
        f"{summary['blocked']} blocked"
    )
    console.print(
        f"Issues: {summary['errors']} error(s), "
        f"{summary['warnings']} warning(s)\n"
    )

    if report["adapter_errors"]:
        console.print("[bold red]Adapter parse/schema errors[/bold red]")
        _print_issues(report["adapter_errors"])
        console.print()

    for channel in report["channels"]:
        status = channel["status"]
        color = {
            "ready": "green",
            "attention": "yellow",
            "blocked": "red",
        }.get(status, "cyan")
        console.print(
            f"[bold {color}]{channel['channel']}[/bold {color}] "
            f"({status})   {channel['adapter_path']}"
        )
        if channel["issues"]:
            _print_issues(channel["issues"])
        diff = channel["diff_summary"]
        if diff:
            console.print(
                "  diff: "
                f"{diff['agents_touched']} agent(s), "
                f"{diff['instruction_diffs']} instruction diff(s), "
                f"{diff['tools_added']} tool add(s), "
                f"{diff['tools_removed']} tool remove(s), "
                f"{diff['callbacks_added']} callback(s)"
            )
        coverage = channel["eval_coverage"]["evaluations"]
        console.print(
            "  evals: "
            f"{coverage['base_count']} base, "
            f"{coverage['channel_count']} channel"
        )
        for warning in channel["coverage_warnings"]:
            console.print(f"  [yellow]WARN [/yellow] {warning['message']}")
        for step in channel["next_steps"]:
            console.print(f"  next: {step}")
        console.print()


def _prompt_channels() -> List[str]:
    """Ask for channels only when stdin is interactive."""
    if not sys.stdin.isatty():
        raise ValueError(
            "No --channel provided. Pass --channel for non-interactive use."
        )
    raw = input("Channel(s), comma-separated [chat]: ").strip() or "chat"
    return split_channels([raw])


# ── poly init ─────────────────────────────────────────────────────────────


def poly_init(args: argparse.Namespace) -> None:
    """Handle ``cxas poly init``."""
    app_dir = Path(getattr(args, "app_dir", ".")).resolve()
    try:
        channels = split_channels(getattr(args, "channel", []) or [])
        if not channels:
            channels = _prompt_channels()
        options = ScaffoldOptions(
            app_dir=app_dir,
            channels=channels,
            target_agent=getattr(args, "agent", None),
            display_name=getattr(args, "display_name", None),
            display_name_template=getattr(
                args,
                "display_name_template",
                DEFAULT_DISPLAY_NAME_TEMPLATE,
            ),
            deployment_target=getattr(args, "deployment_target", "auto"),
            modality=getattr(args, "modality", "auto"),
            include_eval=not getattr(args, "no_eval", False),
            tools=getattr(args, "with_tool", []) or [],
            callback_types=getattr(args, "with_callback", []) or [],
        )
        plan = build_scaffold_plan(options)
        written = write_scaffold_plan(
            plan,
            force=getattr(args, "force", False),
            dry_run=getattr(args, "dry_run", False),
        )
    except (FileNotFoundError, FileExistsError, ValueError) as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    action = "Would write" if getattr(args, "dry_run", False) else "Created"
    console.print(
        f"[green]{action}[/green] {len(written)} file(s) for "
        f"{len(plan.channels)} channel(s) targeting agent "
        f"'{plan.target_agent}'."
    )
    for path in written:
        console.print(f"  {path.relative_to(plan.app_dir)}")
    console.print("\nNext:")
    console.print(f"  cxas poly validate --app-dir {plan.app_dir}")
    for channel in plan.channels:
        console.print(
            f"  cxas poly diff {channel.channel} --app-dir {plan.app_dir}"
        )
    sys.exit(0)


# ── poly build ────────────────────────────────────────────────────────────


def poly_build(args: argparse.Namespace) -> None:
    """Handle ``cxas poly build``."""
    app_dir = str(Path(getattr(args, "app_dir", ".")).resolve())
    channel = getattr(args, "channel", "all")
    output_dir = Path(getattr(args, "output_dir", "./output"))
    force = getattr(args, "force", False)
    strict = getattr(args, "strict", False)

    try:
        engine = _load_engine(app_dir)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    if not engine.adapters and not engine.adapter_errors:
        console.print(
            f"[yellow]No adapter cards found in {app_dir}/adapters.[/yellow]"
        )
        sys.exit(1)

    # Decide which channels to build and gather issues to gate on.
    if channel == "all":
        issues = _all_issues(engine, app_dir)
        targets = list(engine.adapter_cards)
    else:
        if channel not in engine.adapters:
            console.print(
                f"[red]Error:[/red] no adapter for channel '{channel}'. "
                f"Available: {', '.join(sorted(engine.adapters)) or '(none)'}"
            )
            if engine.adapter_errors:
                _print_issues(engine.adapter_errors)
            sys.exit(1)
        card, path = engine.adapters[channel]
        targets = [(card, path)]
        issues = validate_adapter_card(card, app_dir)
        issues.extend(
            i
            for i in _all_issues(engine, app_dir)
            if i.get("rule_id") == "AD007"
            and f"'{channel}'" in i.get("message", "")
        )

    errors, warnings = _counts(issues)
    if issues:
        _print_issues(issues)
    if errors or (strict and warnings):
        console.print("\n[red]Build aborted: validation errors.[/red]")
        sys.exit(1)

    try:
        for card, path in targets:
            ch = card.metadata.channel
            compiled = engine.compile(card, path, validate=False)
            out = engine.write_output(
                compiled, str(output_dir / ch), force=force
            )
            console.print(f"[green]Compiled[/green] channel '{ch}' -> {out}")
    except CompilationError as e:
        console.print("[red]Compilation failed:[/red]")
        _print_issues(e.issues)
        sys.exit(1)
    except (FileExistsError, ValueError) as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    console.print(
        f"\n[green]Done.[/green] {len(targets)} channel(s) written to "
        f"{output_dir.resolve()}"
    )
    sys.exit(0)


# ── poly validate ───────────────────────────────────────────────────────────


def poly_validate(args: argparse.Namespace) -> None:
    """Handle ``cxas poly validate``."""
    app_dir = str(Path(getattr(args, "app_dir", ".")).resolve())
    fmt = getattr(args, "format", "text")
    strict = getattr(args, "strict", False)
    explain = getattr(args, "explain", False)

    try:
        engine = PolymorphismEngine(app_dir)
        engine.load_base_project()
        cards = engine.load_adapter_cards()
    except FileNotFoundError as e:
        if fmt == "json":
            print(json.dumps({"error": str(e), "issues": []}, indent=2))
        else:
            console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    if explain:
        report = build_validation_explanation_report(engine, app_dir)
        if fmt == "json":
            print(json.dumps(report.to_dict(), indent=2))
        else:
            _print_explanation_report(report)
        sys.exit(
            1 if _should_fail(report.errors, report.warnings, strict) else 0
        )

    issues = list(engine.adapter_errors) + validate_all_adapters(cards, app_dir)
    errors, warnings = _counts(issues)

    if fmt == "json":
        print(
            json.dumps(
                {
                    "app_dir": app_dir,
                    "cards": len(cards),
                    "errors": errors,
                    "warnings": warnings,
                    "issues": issues,
                },
                indent=2,
            )
        )
        sys.exit(1 if _should_fail(errors, warnings, strict) else 0)

    if not cards and not engine.adapter_errors:
        console.print(
            f"[yellow]No adapter cards found in {app_dir}/adapters.[/yellow]"
        )
        sys.exit(0)

    if not issues:
        console.print(f"[green]All {len(cards)} adapter card(s) valid.[/green]")
        sys.exit(0)

    console.print(f"Validated {len(cards)} adapter card(s) for {app_dir}:\n")
    _print_issues(issues)
    console.print(f"\n  {errors} error(s), {warnings} warning(s)")
    sys.exit(1 if _should_fail(errors, warnings, strict) else 0)


# ── poly doctor ────────────────────────────────────────────────────────────


def poly_doctor(args: argparse.Namespace) -> None:
    """Handle ``cxas poly doctor``."""
    app_dir = str(Path(getattr(args, "app_dir", ".")).resolve())
    fmt = getattr(args, "format", "text")
    strict = getattr(args, "strict", False)

    try:
        engine = PolymorphismEngine(app_dir)
        engine.load_base_project()
        engine.load_adapter_cards()
    except FileNotFoundError as e:
        if fmt == "json":
            print(json.dumps({"error": str(e), "issues": []}, indent=2))
        else:
            console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    report = build_validation_explanation_report(engine, app_dir)
    if fmt == "json":
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _print_explanation_report(report)

    sys.exit(1 if _should_fail(report.errors, report.warnings, strict) else 0)


# ── poly readiness ───────────────────────────────────────────────────────────


def poly_readiness(args: argparse.Namespace) -> None:
    """Handle ``cxas poly readiness``."""
    app_dir = str(Path(getattr(args, "app_dir", ".")).resolve())
    fmt = getattr(args, "format", "text")
    strict = getattr(args, "strict", False)

    try:
        engine = PolymorphismEngine(app_dir)
        engine.load_base_project()
        engine.load_adapter_cards()
    except FileNotFoundError as e:
        if fmt == "json":
            print(json.dumps({"error": str(e), "channels": []}, indent=2))
        else:
            console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    report = build_readiness_report(engine, app_dir)
    if fmt == "json":
        print(json.dumps(report, indent=2))
    else:
        _print_readiness_report(report)

    summary = report["summary"]
    sys.exit(
        1
        if _should_fail(summary["errors"], summary["warnings"], strict)
        else 0
    )


# ── poly diff ────────────────────────────────────────────────────────────────


def _find_card(
    engine: PolymorphismEngine, channel: str
) -> Optional[AdapterCard]:
    entry = engine.adapters.get(channel)
    return entry[0] if entry else None


def poly_diff(args: argparse.Namespace) -> None:
    """Handle ``cxas poly diff <channel>``."""
    app_dir = str(Path(getattr(args, "app_dir", ".")).resolve())
    channel = args.channel
    as_json = getattr(args, "json", False)

    try:
        engine = _load_engine(app_dir)
    except FileNotFoundError as e:
        if as_json:
            print(json.dumps({"error": str(e), "issues": []}, indent=2))
        else:
            console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    entry = engine.adapters.get(channel)
    if entry is None:
        message = (
            f"no adapter for channel '{channel}'. Available: "
            f"{', '.join(sorted(engine.adapters)) or '(none)'}"
        )
        if as_json:
            print(
                json.dumps(
                    {
                        "error": message,
                        "issues": engine.adapter_errors,
                    },
                    indent=2,
                )
            )
        else:
            console.print(f"[red]Error:[/red] {message}")
            if engine.adapter_errors:
                _print_issues(engine.adapter_errors)
        sys.exit(1)
    card, card_path = entry
    duplicate_issues = [
        i
        for i in _all_issues(engine, app_dir)
        if i.get("rule_id") == "AD007"
        and f"'{channel}'" in i.get("message", "")
    ]
    if duplicate_issues:
        if as_json:
            print(
                json.dumps(
                    {
                        "error": "duplicate adapter channel",
                        "issues": duplicate_issues,
                    },
                    indent=2,
                )
            )
        else:
            console.print("[red]Error:[/red] duplicate adapter channel:")
            _print_issues(duplicate_issues)
        sys.exit(1)

    try:
        compiled = engine.compile(card, card_path)
    except CompilationError as e:
        if as_json:
            print(
                json.dumps(
                    {
                        "error": "Compilation failed",
                        "issues": e.issues,
                    },
                    indent=2,
                )
            )
        else:
            console.print("[red]Compilation failed:[/red]")
            _print_issues(e.issues)
        sys.exit(1)

    report = build_diff_report(
        engine=engine,
        card=card,
        card_path=card_path,
        compiled=compiled,
    )
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        _print_diff_report(report)

    sys.exit(0)


# ── Registration ────────────────────────────────────────────────────────────


def register(subparsers: argparse._SubParsersAction) -> None:
    """Add the ``poly`` subcommand tree to the top-level CLI."""
    parser_poly = subparsers.add_parser(
        "poly",
        help=(
            "Polymorphism engine: compile a base agent project into "
            "channel-optimized variants."
        ),
    )
    poly_subparsers = parser_poly.add_subparsers(
        title="poly commands", dest="poly_command", required=True
    )

    # init
    p_init = poly_subparsers.add_parser(
        "init", help="Scaffold starter channel adapter cards."
    )
    p_init.add_argument(
        "--app-dir",
        default=".",
        help="Path to the agent project root (default: current directory).",
    )
    p_init.add_argument(
        "--channel",
        action="append",
        help=(
            "Channel id to scaffold. Repeat or comma-separate for multiple "
            "channels. If omitted in a TTY, prompts interactively."
        ),
    )
    p_init.add_argument(
        "--agent",
        help=(
            "Agent directory/displayName to target for starter deltas "
            "(default: app rootAgent)."
        ),
    )
    p_init.add_argument(
        "--display-name",
        help="Display name for a single scaffolded adapter.",
    )
    p_init.add_argument(
        "--display-name-template",
        default=DEFAULT_DISPLAY_NAME_TEMPLATE,
        help=(
            "Template for adapter display names; supports {app}, {channel}, "
            "{channel_title}, {channel_slug}."
        ),
    )
    p_init.add_argument(
        "--deployment-target",
        default="auto",
        help=(
            "Deployment channelType to scaffold: auto, none, or a supported "
            "enum such as WEB_UI, API, GOOGLE_TELEPHONY_PLATFORM."
        ),
    )
    p_init.add_argument(
        "--modality",
        default="auto",
        help=(
            "Deployment modality to scaffold: auto, none, CHAT_ONLY, "
            "VOICE_ONLY, CHAT_AND_VOICE, or CHAT_VOICE_AND_VIDEO."
        ),
    )
    p_init.add_argument(
        "--with-tool",
        action="append",
        help=(
            "Create a channel-only python tool and reference it from the "
            "adapter. May be repeated."
        ),
    )
    p_init.add_argument(
        "--with-callback",
        action="append",
        choices=(
            "before_model",
            "after_model",
            "before_tool",
            "after_tool",
            "before_agent",
            "after_agent",
        ),
        help="Create and reference a starter channel callback.",
    )
    p_init.add_argument(
        "--no-eval",
        action="store_true",
        help="Do not scaffold a starter channel eval directory.",
    )
    p_init.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned files without writing them.",
    )
    p_init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite scaffold files if they already exist.",
    )
    p_init.set_defaults(func=poly_init)

    # build
    p_build = poly_subparsers.add_parser(
        "build", help="Compile channel-optimized agent projects."
    )
    p_build.add_argument(
        "--app-dir",
        default=".",
        help="Path to the agent project root (default: current directory).",
    )
    p_build.add_argument(
        "--channel",
        default="all",
        help="Specific channel to compile, or 'all' (default: all).",
    )
    p_build.add_argument(
        "--output-dir",
        default="./output",
        help="Output directory (default: ./output).",
    )
    p_build.add_argument(
        "--force",
        action="store_true",
        help="Overwrite a non-empty output directory not created by poly.",
    )
    p_build.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors (abort the build).",
    )
    p_build.set_defaults(func=poly_build)

    # validate
    p_validate = poly_subparsers.add_parser(
        "validate", help="Validate adapter cards against the project."
    )
    p_validate.add_argument(
        "--app-dir",
        default=".",
        help="Path to the agent project root (default: current directory).",
    )
    p_validate.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text).",
    )
    p_validate.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any warnings are present.",
    )
    p_validate.add_argument(
        "--explain",
        action="store_true",
        help="Explain each validation issue with likely fixes.",
    )
    p_validate.set_defaults(func=poly_validate)

    # doctor
    p_doctor = poly_subparsers.add_parser(
        "doctor", help="Explain adapter validation issues and likely fixes."
    )
    p_doctor.add_argument(
        "--app-dir",
        default=".",
        help="Path to the agent project root (default: current directory).",
    )
    p_doctor.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text).",
    )
    p_doctor.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any warnings are present.",
    )
    p_doctor.set_defaults(func=poly_doctor)

    # readiness
    p_readiness = poly_subparsers.add_parser(
        "readiness",
        help=(
            "Summarize adapter validation, diffs, eval coverage, and build "
            "readiness."
        ),
    )
    p_readiness.add_argument(
        "--app-dir",
        default=".",
        help="Path to the agent project root (default: current directory).",
    )
    p_readiness.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text).",
    )
    p_readiness.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any warnings are present.",
    )
    p_readiness.set_defaults(func=poly_readiness)

    # diff
    p_diff = poly_subparsers.add_parser(
        "diff", help="Show what an adapter changes for a channel."
    )
    p_diff.add_argument("channel", help="The channel to diff.")
    p_diff.add_argument(
        "--app-dir",
        default=".",
        help="Path to the agent project root (default: current directory).",
    )
    p_diff.add_argument(
        "--json",
        action="store_true",
        help="Emit a stable machine-readable diff report.",
    )
    p_diff.set_defaults(func=poly_diff)
