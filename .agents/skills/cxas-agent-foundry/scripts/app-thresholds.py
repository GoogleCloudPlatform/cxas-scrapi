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

"""View and update app-level golden eval scoring thresholds.

Usage:
  python scripts/app-thresholds.py show
  python scripts/app-thresholds.py set --similarity 2
  python scripts/app-thresholds.py set --tool-invocation 0.5
  python scripts/app-thresholds.py set --extra-tools allow
  python scripts/app-thresholds.py set --hallucination disabled
  python scripts/app-thresholds.py set --similarity 2 --extra-tools allow --hallucination disabled
"""

import argparse
import glob
import json
import os
import sys

from config import load_app_name, load_config, get_project_path


HALLUCINATION_VALUES = {"unspecified": 0, "disabled": 1, "enabled_strict": 2, "enabled": 2}
EXTRA_TOOL_VALUES = {"allow": 1, "deny": 2}


def get_app(project, location):
    """Get the Apps client."""
    from cxas_scrapi.core.apps import Apps
    return Apps(project_id=project, location=location)


def cmd_show(args):
    """Show current scoring thresholds."""
    app_name = load_app_name()

    from cxas_scrapi.core.evaluations import Evaluations
    try:
        client = Evaluations(app_name=app_name)
        client.get_evaluation_thresholds(print_console=True)
    except Exception as e:
        print(f"Error: Failed to get thresholds: {e}")
        sys.exit(1)


def cmd_set(args):
    """Update scoring thresholds."""
    from google.cloud.ces_v1beta import types
    from google.protobuf import field_mask_pb2

    app_name = load_app_name()
    parts = app_name.split("/")
    project, location = parts[1], parts[3]
    a = get_app(project, location)
    try:
        app = a.get_app(app_name=app_name)
    except Exception as e:
        print(f"Error: Failed to get app: {e}")
        sys.exit(1)

    changes = []

    if args.similarity is not None:
        val = args.similarity
        if val < 1 or val > 4:
            print("Error: --similarity must be 1-4")
            sys.exit(1)
        app.evaluation_metrics_thresholds.golden_evaluation_metrics_thresholds \
            .turn_level_metrics_thresholds.semantic_similarity_success_threshold = val
        changes.append(f"semantic_similarity → {val}")

    if args.tool_invocation is not None:
        val = args.tool_invocation
        if val < 0 or val > 1:
            print("Error: --tool-invocation must be 0.0-1.0")
            sys.exit(1)
        app.evaluation_metrics_thresholds.golden_evaluation_metrics_thresholds \
            .turn_level_metrics_thresholds.overall_tool_invocation_correctness_threshold = val
        changes.append(f"tool_invocation → {val}")

    if args.extra_tools is not None:
        val = EXTRA_TOOL_VALUES.get(args.extra_tools.lower())
        if val is None:
            print(f"Error: --extra-tools must be 'allow' or 'deny'")
            sys.exit(1)
        app.evaluation_metrics_thresholds.golden_evaluation_metrics_thresholds \
            .tool_matching_settings.extra_tool_call_behavior = val
        changes.append(f"extra_tool_call_behavior → {args.extra_tools.upper()}")

    if args.hallucination is not None:
        val = HALLUCINATION_VALUES.get(args.hallucination.lower())
        if val is None:
            print(f"Error: --hallucination must be 'disabled', 'enabled_strict', or 'unspecified'")
            sys.exit(1)
        app.evaluation_metrics_thresholds.golden_hallucination_metric_behavior = val
        changes.append(f"hallucination → {args.hallucination.upper()}")

    if not changes:
        print("No changes specified. Use --similarity, --tool-invocation, --extra-tools, or --hallucination.")
        sys.exit(1)

    mask = field_mask_pb2.FieldMask(paths=["evaluation_metrics_thresholds"])
    request = types.UpdateAppRequest(app=app, update_mask=mask)
    try:
        a.client.update_app(request=request)
    except Exception as e:
        print(f"Error: Failed to update app: {e}")
        sys.exit(1)

    print(f"Updated {len(changes)} threshold(s) on platform:")
    for c in changes:
        print(f"  {c}")

    # Sync to local app.json so the next `cxas push` doesn't overwrite
    _sync_thresholds_to_local(args)


def _find_local_app_json():
    """Find the local app.json file in the project's cxas_app directory."""
    config = load_config()
    app_dir = get_project_path(config.get("app_dir", "cxas_app/"))
    matches = glob.glob(os.path.join(app_dir, "*/app.json"))
    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        # Try to match by app name
        app_name = config.get("app_name", "")
        for m in matches:
            parent = os.path.basename(os.path.dirname(m))
            if parent == app_name:
                return m
        return matches[0]
    return None


def _sync_thresholds_to_local(args):
    """Update the local app.json evaluationMetricsThresholds to match what was just set on the platform."""
    app_json_path = _find_local_app_json()
    if not app_json_path:
        print("  (no local app.json found — skipping local sync)")
        return

    try:
        with open(app_json_path) as f:
            app_data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"  Warning: could not read {app_json_path}: {e}")
        return

    # Ensure the threshold structure exists
    thresholds = app_data.setdefault("evaluationMetricsThresholds", {})
    golden = thresholds.setdefault("goldenEvaluationMetricsThresholds", {})
    turn_level = golden.setdefault("turnLevelMetricsThresholds", {})
    tool_matching = golden.setdefault("toolMatchingSettings", {})

    changed = False

    if args.similarity is not None:
        turn_level["semanticSimilaritySuccessThreshold"] = args.similarity
        changed = True

    if args.tool_invocation is not None:
        turn_level["overallToolInvocationCorrectnessThreshold"] = args.tool_invocation
        changed = True

    if args.extra_tools is not None:
        tool_matching["extraToolCallBehavior"] = args.extra_tools.upper()
        changed = True

    if args.hallucination is not None:
        behavior_map = {"unspecified": "UNSPECIFIED", "disabled": "DISABLED", "enabled_strict": "ENABLED_STRICT", "enabled": "ENABLED"}
        thresholds["goldenHallucinationMetricBehavior"] = behavior_map.get(args.hallucination.lower(), args.hallucination.upper())
        changed = True

    if changed:
        with open(app_json_path, "w") as f:
            json.dump(app_data, f, indent=4)
            f.write("\n")
        rel_path = os.path.relpath(app_json_path)
        print(f"  Synced to local {rel_path}")


def main():
    try:
        import cxas_scrapi
    except ImportError:
        print("Error: cxas-scrapi not installed. Activate venv (source .venv/bin/activate) and install cxas-scrapi first.")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="View and update golden eval scoring thresholds"
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("show", help="Show current thresholds")

    p_set = sub.add_parser("set", help="Update thresholds")
    p_set.add_argument("--similarity", type=int, default=None,
                       help="Semantic similarity threshold (1-4). Lower = more lenient.")
    p_set.add_argument("--tool-invocation", type=float, default=None,
                       help="Tool invocation correctness threshold (0.0-1.0)")
    p_set.add_argument("--extra-tools", default=None,
                       help="Extra tool call behavior: 'allow' or 'deny'")
    p_set.add_argument("--hallucination", default=None,
                       help="Hallucination metric: 'disabled', 'enabled_strict', or 'unspecified'")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "show":
        cmd_show(args)
    elif args.command == "set":
        cmd_set(args)


if __name__ == "__main__":
    main()
