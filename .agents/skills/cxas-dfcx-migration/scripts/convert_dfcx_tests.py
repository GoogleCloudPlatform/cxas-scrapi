#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0


"""Convert DFCX test cases to CXAS TurnTestCase YAML files.

Can read test cases from either an IR bundle or a raw test case directory.

Usage:
    # From an IR bundle (has flow-to-agent mapping built in)
    python convert_dfcx_tests.py --ir-bundle <target>_ir.json

    # From a raw test case directory (needs explicit flow mapping)
    python convert_dfcx_tests.py \
        --source-dir /tmp/dfcx_compare/source/testCases \
        --flow-map flow_map.json

    # Override output directory
    python convert_dfcx_tests.py \
        --ir-bundle <target>_ir.json \
        --output-dir my_evals/simulations
"""

import argparse  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
import typing

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from cxas_scrapi.migration.data_models import (  # noqa: E402
    DFCXAgentIR,
    IRAgent,
    IRBundle,
    IRMetadata,
    MigrationIR,
)
from cxas_scrapi.migration.dfcx_test_converter import (  # noqa: E402
    DFCXTestConverter,  # noqa: E402
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args() -> typing.Any:
    parser = argparse.ArgumentParser(
        description="Convert DFCX test cases to CXAS simulation YAML files."
    )

    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--ir-bundle",
        help=(
            "Path to IR bundle JSON "
            "(contains source test cases and agent mapping)"
        ),
    )
    source.add_argument(
        "--source-dir",
        help="Path to directory containing DFCX test case JSON files",
    )

    parser.add_argument(
        "--flow-map",
        help=(
            "Path to JSON file mapping DFCX flow names to CXAS agent names. "
            "Required when using --source-dir. "
            'Format: {"Default Start Flow": "RootAgent", ...}'
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Output directory for YAML files "
            "(default: <target>_evals/simulations)"
        ),
    )
    return parser.parse_args()


def load_from_bundle(path: str) -> typing.Any:
    """Load test cases and build converter from an IR bundle."""
    bundle = IRBundle.load(path)
    source = bundle.source_agent_data
    ir = bundle.ir

    flow_map = None
    if bundle.grouping:
        flow_map = {
            flow: group
            for group, entry in bundle.grouping.items()
            for flow in entry.get("agents", [])
        }

    target_name = bundle.config.target_name
    return source, ir, flow_map, target_name


def load_from_directory(
    source_dir: str, flow_map_path: str | None
) -> typing.Any:
    """Load test cases from a directory of JSON files."""
    test_cases = []
    for filename in sorted(os.listdir(source_dir)):
        if filename.endswith(".json"):
            filepath = os.path.join(source_dir, filename)
            with open(filepath) as test_file:
                test_cases.append(json.load(test_file))

    if not test_cases:
        return None, None, None, None

    source = DFCXAgentIR(
        name="local",
        display_name="local",
        default_language_code="en",
        test_cases=test_cases,
    )

    flow_map = None
    if flow_map_path:
        with open(flow_map_path) as f:
            flow_map = json.load(f)

    # Build IR from flow map agent names, or from test case flow names
    if flow_map:
        agent_names = set(flow_map.values())
    else:
        agent_names = {
            tc.get("testConfig", {}).get("flow")
            for tc in test_cases
            if tc.get("testConfig", {}).get("flow")
        }

    ir = MigrationIR(
        metadata=IRMetadata(app_name="local"),
        agents={
            name: IRAgent(type="FLOW", display_name=name, instruction="")
            for name in agent_names
        },
    )

    return source, ir, flow_map, "converted"


def main() -> None:
    args = parse_args()
    console = Console()

    if args.source_dir and not args.flow_map:
        console.print(
            "[yellow]Warning: no --flow-map provided. "
            "Tests will be grouped by source flow name.[/]"
        )

    # Load
    if args.ir_bundle:
        console.print(f"Loading IR bundle: {args.ir_bundle}")
        source, ir, flow_map, target_name = load_from_bundle(args.ir_bundle)
    else:
        console.print(f"Loading test cases from: {args.source_dir}")
        source, ir, flow_map, target_name = load_from_directory(
            args.source_dir, args.flow_map
        )

    if source is None or not source.test_cases:
        console.print("[red]No test cases found.[/]")
        sys.exit(1)

    console.print(f"Found {len(source.test_cases)} DFCX test cases")

    # Convert
    converter = DFCXTestConverter(ir, flow_to_agent_map=flow_map)
    tests_by_agent, report = converter.convert_all(source)

    # Report
    table = Table(title="Conversion Report")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Source test cases", str(report["total_source_tests"]))
    table.add_row("Converted", f"[green]{report['converted']}[/]")
    table.add_row("Skipped", f"[yellow]{report['skipped']}[/]")
    table.add_row(
        "Behavioral assertions",
        str(report["behavioral_assertions"]),
    )
    table.add_row(
        "Fuzzy-match assertions",
        str(report["fuzzy_match_assertions"]),
    )
    table.add_row(
        "AGENT_TRANSFER assertions",
        str(report["agent_transfer_assertions"]),
    )
    table.add_row("DTMF-as-text turns", str(report["dtmf_as_text_count"]))
    console.print(table)

    if report.get("tests_per_agent"):
        agent_table = Table(title="Tests per Agent")
        agent_table.add_column("Agent")
        agent_table.add_column("Tests", justify="right")
        for agent, count in sorted(report["tests_per_agent"].items()):
            agent_table.add_row(agent, str(count))
        console.print(agent_table)

    # Write YAML
    out_dir = args.output_dir or f"{target_name}_evals/simulations"
    os.makedirs(out_dir, exist_ok=True)

    yamls = DFCXTestConverter.serialize_to_yaml(tests_by_agent)
    for agent_name, yaml_str in yamls.items():
        path = os.path.join(out_dir, f"{agent_name}.yaml")
        with open(path, "w") as f:
            f.write(yaml_str)
        console.print(f"  Wrote {path} ({len(yaml_str):,} bytes)")

    # Write report
    report_path = os.path.join(out_dir, "_conversion_report.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    console.print(f"  Wrote {report_path}")

    console.print(
        f"\n[bold green]Done.[/] Run simulations with:\n"
        f"  python run_simulations.py "
        f"--app-name projects/<project>/locations/<loc>/apps/<app-id> "
        f"--sim-dir {out_dir}"
    )


if __name__ == "__main__":
    main()
