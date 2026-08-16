#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0


"""Run converted DFCX test simulations against a deployed CXAS agent.

Usage:
    python run_simulations.py \
        --app-name projects/<project>/locations/<loc>/apps/<app-id> \
        --sim-dir <target_name>_evals/simulations \
        [--filter <glob>] \
        [--tags <tag1,tag2>] \
        [--limit <N>] \
        [--debug]

Examples:
    # Run all simulations
    python run_simulations.py \
        --app-name projects/my-proj/locations/us/apps/abc123 \
        --sim-dir tong_wf_dfcx_migration_evals/simulations

    # Run only tests tagged #happy_path, limit to 10
    python run_simulations.py \
        --app-name projects/my-proj/locations/us/apps/abc123 \
        --sim-dir tong_wf_dfcx_migration_evals/simulations \
        --tags '#happy_path' --limit 10

    # Run tests from a single agent file
    python run_simulations.py \
        --app-name projects/my-proj/locations/us/apps/abc123 \
        --sim-dir tong_wf_dfcx_migration_evals/simulations \
        --filter 'RootAgent*'
"""

import argparse  # noqa: E402
import fnmatch  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
import typing

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from cxas_scrapi.evals.turn_evals import TurnEvals  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args() -> typing.Any:
    parser = argparse.ArgumentParser(
        description="Run converted DFCX test simulations against a CXAS agent."
    )
    parser.add_argument(
        "--app-name",
        required=True,
        help="Full CXAS app resource name (projects/.../apps/...)",
    )
    parser.add_argument(
        "--sim-dir",
        required=True,
        help="Directory containing simulation YAML files",
    )
    parser.add_argument(
        "--filter",
        default="*.yaml",
        help="Glob pattern to filter YAML files (default: *.yaml)",
    )
    parser.add_argument(
        "--tags",
        default=None,
        help="Comma-separated tags to filter tests (e.g. '#happy_path,#bug')",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of tests to run",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path to write results CSV",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output for each turn",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    console = Console()

    if not os.path.isdir(args.sim_dir):
        console.print(f"[red]Directory not found: {args.sim_dir}[/]")
        sys.exit(1)

    # Discover YAML files
    yaml_files = sorted(
        f
        for f in os.listdir(args.sim_dir)
        if fnmatch.fnmatch(f, args.filter)
        and (f.endswith(".yaml") or f.endswith(".yml"))
    )
    if not yaml_files:
        msg = f"No YAML files matching '{args.filter}' in {args.sim_dir}"
        console.print(f"[yellow]{msg}[/]")
        sys.exit(0)

    console.print(
        f"Found {len(yaml_files)} simulation file(s): {', '.join(yaml_files)}"
    )

    # Initialize TurnEvals
    evals = TurnEvals(app_name=args.app_name)

    # Load tests
    all_tests = []
    for yf in yaml_files:
        path = os.path.join(args.sim_dir, yf)
        tests = evals.load_turn_test_cases_from_file(path)
        console.print(f"  Loaded {len(tests)} tests from {yf}")
        all_tests.extend(tests)

    if not all_tests:
        console.print("[yellow]No tests loaded.[/]")
        sys.exit(0)

    # Filter by tags
    if args.tags:
        tag_set = {t.strip() for t in args.tags.split(",")}
        all_tests = [t for t in all_tests if tag_set & set(t.tags)]
        console.print(f"After tag filter ({args.tags}): {len(all_tests)} tests")

    # Apply limit
    if args.limit and args.limit < len(all_tests):
        all_tests = all_tests[: args.limit]
        console.print(f"Limited to {args.limit} tests")

    if not all_tests:
        console.print("[yellow]No tests after filtering.[/]")
        sys.exit(0)

    n = len(all_tests)
    console.print(
        f"\n[bold]Running {n} simulations against {args.app_name}[/]\n"
    )

    # Run tests
    results_df = evals.run_turn_tests(all_tests, debug=args.debug)

    # Summary
    console.print()
    total = len(results_df)
    passed = len(results_df[results_df["status"] == "SUCCESS"])
    failed = len(results_df[results_df["status"] == "FAILURE"])
    skipped = len(results_df[results_df["status"] == "SKIPPED"])

    table = Table(title="Simulation Results Summary")
    table.add_column("Metric", style="bold")
    table.add_column("Count", justify="right")
    table.add_row("Total assertions", str(total))
    table.add_row("Passed", f"[green]{passed}[/]")
    table.add_row("Failed", f"[red]{failed}[/]")
    if skipped:
        table.add_row("Skipped", f"[yellow]{skipped}[/]")
    table.add_row(
        "Pass rate", f"{passed / total * 100:.1f}%" if total else "N/A"
    )
    console.print(table)

    # Show failures
    failures = results_df[results_df["status"] == "FAILURE"]
    if not failures.empty:
        console.print(f"\n[red bold]Failed assertions ({len(failures)}):[/]\n")
        for _, row in failures.head(20).iterrows():
            console.print(f"  [bold]{row['test_name']}[/] / {row['turn']}")
            console.print(f"    User: {row['user']}")
            console.print(f"    Error: {row['errors']}")
            if row.get("expected"):
                console.print(f"    Expected: {row['expected']}")
            if row.get("actual"):
                console.print(f"    Actual: {str(row['actual'])[:200]}")
            console.print()
        if len(failures) > 20:
            console.print(f"  ... and {len(failures) - 20} more failures")

    # Write CSV
    if args.output:
        results_df.to_csv(args.output, index=False)
        console.print(f"\nResults written to {args.output}")

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
