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

"""Bootstrap local eval files from an existing GECX agent.

Pulls platform goldens, auto-generates tool tests, syncs callbacks,
and creates a simulation skeleton. Run this once when starting to
debug an existing agent that has no local eval files.

Usage:
  python scripts/bootstrap-evals.py
  python scripts/bootstrap-evals.py --skip-goldens    # Skip platform golden export
  python scripts/bootstrap-evals.py --skip-tools      # Skip tool test generation
  python scripts/bootstrap-evals.py --skip-callbacks   # Skip callback sync
  python scripts/bootstrap-evals.py --dry-run          # Show what would be done
"""

import argparse
import os
import re
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

# Add scripts dir to path so we can import config
sys.path.insert(0, SCRIPTS_DIR)
from config import load_app_name, load_config, get_project_path

USER_AGENT_EXTENSION = "skill/cxas-agent-foundry/bootstrap-evals"


SIM_SKELETON = """\
# Simulation eval definitions -- local sims run via SCRAPI Sessions API.
# Fill in goals, success_criteria, and response_guide for each scenario.
# See references/eval-templates.md -> Simulation YAML Template for guidance.
#
# Tags are required -- the sim runner filters by --priority P0/P1/P2.
# End every success_criteria with what counts as success:
#   e.g., "Being transferred to a specialist counts as a successful outcome."

evals:
  - name: example_sim
    tags: [P0, HIGH]
    steps:
      - goal: "TODO: What the sim user wants to accomplish"
        success_criteria: "TODO: What counts as success"
        response_guide: "TODO: How the sim user should behave -- include auth details to provide when asked"
        max_turns: 12
    expectations:
      - "TODO: What the agent should do"
    session_parameters:
      account_id: "TODO"
      customer_id: "TODO"
"""


def _run(cmd, description, dry_run=False):
    """Run a subprocess with clear status output."""
    print(f"\n{'=' * 60}")
    print(f"  {description}")
    print(f"{'=' * 60}")
    print(f"  $ {' '.join(cmd)}")

    if dry_run:
        print("  [DRY RUN] Skipped.")
        return True

    result = subprocess.run(cmd, cwd=os.getcwd())
    if result.returncode != 0:
        print(f"\n  WARNING: {description} failed (exit code {result.returncode})")
        return False
    return True


def ensure_dirs():
    """Create the evals directory structure if it doesn't exist."""
    dirs = [
        get_project_path("evals", "goldens"),
        get_project_path("evals", "simulations"),
        get_project_path("evals", "tool_tests"),
        get_project_path("evals", "callback_tests", "agents"),
        get_project_path("evals", "callback_tests", "tests"),
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)


def _resolve_resource_paths(evals_dir, app_name):
    """Post-process exported YAML files to replace resource path UUIDs with display names.

    The SCRAPI export may leave agent transfers and tool references as raw
    resource paths (e.g., projects/.../agents/<uuid>) or as bare UUIDs
    (e.g., `action: <uuid>` in golden tool_calls). This walks evals_dir
    recursively and replaces both forms with display names.
    """
    # Full resource path -> display name
    resource_map = {}
    # Bare UUID -> display name
    uuid_map = {}

    def _record_uuid(resource_path, display_name):
        uuid = resource_path.split("/")[-1] if "/" in resource_path else resource_path
        if len(uuid) > 8 and "-" in uuid:
            uuid_map[uuid] = display_name

    try:
        from cxas_scrapi.core.agents import Agents
        agents_client = Agents(app_name=app_name, user_agent_extension=USER_AGENT_EXTENSION)
        agents_map = agents_client.get_agents_map(reverse=False)
        resource_map.update(agents_map)
        for resource_path, display_name in agents_map.items():
            _record_uuid(resource_path, display_name)
    except Exception as e:
        print(f"  WARNING: Could not build agent map: {e}")

    try:
        from cxas_scrapi.core.tools import Tools
        tools_client = Tools(app_name=app_name, user_agent_extension=USER_AGENT_EXTENSION)
        tools_map = tools_client.get_tools_map()
        # get_tools_map returns {resource_path: display_name}
        for resource_path, display_name in tools_map.items():
            resource_map[resource_path] = display_name
            _record_uuid(resource_path, display_name)
    except Exception as e:
        print(f"  WARNING: Could not build tool map: {e}")

    if not resource_map and not uuid_map:
        return

    resolved_count = 0
    for root, _dirs, files in os.walk(evals_dir):
        for fname in files:
            if not fname.endswith((".yaml", ".yml")):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, "r") as f:
                content = f.read()

            changed = False

            # Pass 1: replace known full resource paths
            for resource_path, display_name in resource_map.items():
                if resource_path in content:
                    content = content.replace(resource_path, display_name)
                    changed = True
                    resolved_count += 1

            # Pass 2: catch remaining full resource paths (e.g., system tools
            # not in the tools map) by stripping to the last segment
            remaining = re.findall(
                r'projects/[^/]+/locations/[^/]+/apps/[^/]+/(tools|agents)/([^\s\'",:}\]]+)',
                content,
            )
            for resource_type, name in remaining:
                pattern = re.compile(
                    r'projects/[^/]+/locations/[^/]+/apps/[^/]+/'
                    + resource_type + '/' + re.escape(name)
                )
                content = pattern.sub(name, content)
                changed = True
                resolved_count += 1

            # Pass 3: replace bare UUIDs (e.g. `action: <uuid>` in golden tool_calls)
            for uuid, display_name in uuid_map.items():
                if uuid in content:
                    content = content.replace(uuid, display_name)
                    changed = True
                    resolved_count += 1

            if changed:
                with open(fpath, "w") as f:
                    f.write(content)

    if resolved_count:
        print(f"  Resolved {resolved_count} resource path(s) to display names.")


def _strip_expectations(evals_dir):
    """Remove `expectations` blocks from exported goldens.

    Platform expectations are autogenerated and rarely useful for local
    debugging — assertions get written via sims or by hand. Strips both
    top-level and per-conversation expectations.
    """
    import yaml
    stripped = 0
    for root, _dirs, files in os.walk(evals_dir):
        for fname in files:
            if not fname.endswith((".yaml", ".yml")):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath) as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                continue
            changed = False
            if "expectations" in data:
                del data["expectations"]
                changed = True
                stripped += 1
            for conv in data.get("conversations") or []:
                if isinstance(conv, dict) and "expectations" in conv:
                    del conv["expectations"]
                    changed = True
                    stripped += 1
            if changed:
                with open(fpath, "w") as f:
                    yaml.safe_dump(data, f, sort_keys=False)
    if stripped:
        print(f"  Stripped {stripped} expectation block(s) from exported goldens.")


def export_goldens(app_name, dry_run=False, keep_expectations=False):
    """Export platform golden evals to local YAML files."""
    output_dir = get_project_path("evals", "goldens")

    if dry_run:
        print(f"  Would export platform goldens to {output_dir}")
        return True

    try:
        from cxas_scrapi.core.evaluations import Evaluations, ExportFormat
        client = Evaluations(app_name=app_name, user_agent_extension=USER_AGENT_EXTENSION)
        evals_map = client.get_evaluations_map(reverse=True)
        goldens = evals_map.get("goldens", {})

        if not goldens:
            print("  No platform goldens found.")
            return True

        os.makedirs(output_dir, exist_ok=True)
        print(f"  Found {len(goldens)} platform golden(s). Exporting...")
        for display_name, resource_id in goldens.items():
            safe_name = "".join(
                c if c.isalnum() or c in ("_", "-") else "_" for c in display_name
            )
            file_path = os.path.join(output_dir, f"{safe_name}.yaml")
            try:
                client.export_evaluation(
                    resource_id,
                    output_format=ExportFormat.YAML,
                    output_path=file_path,
                )
                print(f"    OK  {safe_name}.yaml")
            except Exception as e:
                print(f"    ERR {display_name}: {e}")

        # Strip expectations BEFORE the text-level resource-path rewrites so
        # the YAML is parseable. (resolve does string replaces that don't
        # affect parseability, but stripping first keeps the two passes clean.)
        if not keep_expectations:
            _strip_expectations(output_dir)

        # Resolve agent/tool resource paths and bare UUIDs to display names
        _resolve_resource_paths(output_dir, app_name)

        print(f"  Exported to {output_dir}")
        return True
    except Exception as e:
        print(f"  WARNING: Failed to export goldens: {e}")
        return False


def generate_tool_tests(app_name, dry_run=False):
    """Auto-generate tool test YAML from tool schemas."""
    output_dir = get_project_path("evals", "tool_tests")

    if dry_run:
        print(f"  Would generate tool tests to {output_dir}")
        return True

    try:
        from cxas_scrapi.evals.tool_evals import ToolEvals
        tool_evals = ToolEvals(app_name=app_name, user_agent_extension=USER_AGENT_EXTENSION)
        tool_evals.generate_tool_tests(
            target_dir=output_dir,
            mine_tool_data=True,
            mine_conversations_limit=50,
        )
        print(f"  Generated tool tests to {output_dir}")
        return True
    except Exception as e:
        print(f"  WARNING: Failed to generate tool tests: {e}")
        return False


def sync_callbacks(dry_run=False):
    """Sync callback code from platform to local test directories."""
    python = sys.executable
    script = os.path.join(SCRIPTS_DIR, "sync-callbacks.py")
    cmd = [python, script]
    if dry_run:
        cmd.append("--dry-run")
    return _run(cmd, "Sync callback code from platform", dry_run=dry_run)


def create_sim_skeleton(dry_run=False):
    """Create a simulation YAML skeleton if none exists."""
    sim_dir = get_project_path("evals", "simulations")
    sim_file = os.path.join(sim_dir, "simulations.yaml")

    # Check if any sim YAML already exists
    if os.path.isdir(sim_dir):
        existing = [f for f in os.listdir(sim_dir) if f.endswith(".yaml") or f.endswith(".yml")]
    else:
        existing = []
    if existing:
        print(f"  Simulation files already exist: {', '.join(existing)}")
        return True

    if dry_run:
        print(f"  Would create skeleton at {sim_file}")
        return True

    with open(sim_file, "w") as f:
        f.write(SIM_SKELETON)
    print(f"  Created skeleton at {sim_file}")
    print("  --> Fill in goals, success_criteria, and response_guide manually.")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Bootstrap local eval files from an existing GECX agent."
    )
    parser.add_argument("--skip-goldens", action="store_true", help="Skip platform golden export")
    parser.add_argument("--skip-tools", action="store_true", help="Skip tool test generation")
    parser.add_argument("--skip-callbacks", action="store_true", help="Skip callback sync")
    parser.add_argument("--keep-expectations", action="store_true",
                        help="Preserve `expectations` blocks in exported goldens (stripped by default)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")

    args = parser.parse_args()

    # Verify environment
    if not args.dry_run:
        try:
            import cxas_scrapi  # noqa: F401
        except ImportError:
            print("Error: cxas-scrapi not installed. Activate venv first (source .venv/bin/activate).")
            sys.exit(1)

    app_name = load_app_name()
    project_dir = get_project_path()

    print(f"\nBootstrapping eval files for: {app_name}")
    print(f"Project directory: {project_dir}")
    print(f"{'=' * 60}")

    # Create directory structure
    print("\nCreating evals directory structure...")
    if not args.dry_run:
        ensure_dirs()
    print("  Done.")

    results = {}

    # Step 1: Export platform goldens
    if not args.skip_goldens:
        print(f"\n{'=' * 60}")
        print("  Step 1/4: Export platform goldens")
        print(f"{'=' * 60}")
        results["goldens"] = export_goldens(app_name, args.dry_run, args.keep_expectations)
    else:
        print("\n  Step 1/4: Export platform goldens -- SKIPPED")
        results["goldens"] = None

    # Step 2: Generate tool tests
    if not args.skip_tools:
        print(f"\n{'=' * 60}")
        print("  Step 2/4: Generate tool tests")
        print(f"{'=' * 60}")
        results["tool_tests"] = generate_tool_tests(app_name, args.dry_run)
    else:
        print("\n  Step 2/4: Generate tool tests -- SKIPPED")
        results["tool_tests"] = None

    # Step 3: Sync callbacks
    if not args.skip_callbacks:
        results["callbacks"] = sync_callbacks(args.dry_run)
    else:
        print("\n  Step 3/4: Sync callbacks -- SKIPPED")
        results["callbacks"] = None

    # Step 4: Create sim skeleton
    print(f"\n{'=' * 60}")
    print("  Step 4/4: Create simulation skeleton")
    print(f"{'=' * 60}")
    results["simulations"] = create_sim_skeleton(args.dry_run)

    # Summary
    print(f"\n{'=' * 60}")
    print("  Bootstrap complete. Summary:")
    print(f"{'=' * 60}")
    for step, success in results.items():
        if success is None:
            status = "SKIPPED"
        elif success:
            status = "OK"
        else:
            status = "FAILED"
        print(f"  {step}: {status}")

    print(f"\n  Still needs manual work:")
    print(f"  - Write simulation goals and success_criteria in evals/simulations/simulations.yaml")
    print(f"  - Write callback test assertions in evals/callback_tests/tests/")
    print(f"  - Review exported goldens for correctness")
    print(f"  - Generate TDD from agent code (copy template from assets/project-template/tdd.md)")
    print()


if __name__ == "__main__":
    main()
