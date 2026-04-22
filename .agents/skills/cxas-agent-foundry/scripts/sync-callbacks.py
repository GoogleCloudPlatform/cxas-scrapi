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

"""Sync callback code from GECX platform to local test directories.

Downloads python_code from each agent's callbacks and saves them locally,
then verifies/creates symlinks to corresponding test files.

Usage:
  python scripts/sync-callbacks.py                    # Sync all callbacks
  python scripts/sync-callbacks.py --agent root_agent # Sync only one agent
  python scripts/sync-callbacks.py --dry-run          # Show what would be synced
"""

import argparse
import json
import os
import sys
import yaml

from config import load_app_name, get_project_path


AGENTS_DIR = get_project_path("evals", "callback_tests", "agents")
TESTS_DIR = get_project_path("evals", "callback_tests", "tests")


def derive_callback_name(field_name):
    """Derive short callback name from field name.

    e.g. 'before_model_callbacks' -> 'before_model'
         'after_agent_callbacks'  -> 'after_agent'
    """
    if field_name.endswith("_callbacks"):
        return field_name[: -len("_callbacks")]
    return field_name


def sync_agent_callbacks(app_name, agent_name, dry_run=False):
    """Sync callbacks for a single agent. Returns (synced, tests_found, tests_missing)."""
    from cxas_scrapi.core.callbacks import Callbacks

    callbacks_client = Callbacks(app_name=app_name)
    try:
        cb_map = callbacks_client.list_callbacks(agent_name)
    except Exception as e:
        print(f"  Error: Failed to list callbacks for '{agent_name}': {e}")
        return 0, 0, 0

    synced = 0
    tests_found = 0
    tests_missing = 0

    if not cb_map:
        print(f"  No callbacks found for agent '{agent_name}'")
        return synced, tests_found, tests_missing

    for field_name, cb_list in cb_map.items():
        if not cb_list:
            continue

        callback_type = field_name  # e.g. 'before_model_callbacks'
        base_name = derive_callback_name(field_name)
        use_index = len(cb_list) > 1

        for idx, cb in enumerate(cb_list):
            python_code = getattr(cb, "python_code", None)
            if not python_code:
                continue

            # Determine callback_name
            if use_index:
                callback_name = f"{base_name}_{idx}"
            else:
                callback_name = base_name

            # Build paths
            agent_cb_dir = os.path.join(AGENTS_DIR, agent_name, callback_type, callback_name)
            code_path = os.path.join(agent_cb_dir, "python_code.py")
            test_src = os.path.join(TESTS_DIR, agent_name, callback_type, callback_name, "test.py")
            symlink_path = os.path.join(agent_cb_dir, "test.py")

            disabled = getattr(cb, "disabled", False)
            description = getattr(cb, "description", "")
            status = " (disabled)" if disabled else ""

            if dry_run:
                print(f"  [dry-run] Would write: {os.path.relpath(code_path)}{status}")
            else:
                os.makedirs(agent_cb_dir, exist_ok=True)
                with open(code_path, "w") as f:
                    f.write(python_code)
                print(f"  Wrote: {os.path.relpath(code_path)}{status}")

            synced += 1

            # Check for corresponding test and manage symlink
            if os.path.exists(test_src):
                tests_found += 1
                if dry_run:
                    print(f"  [dry-run] Would link: test.py -> {os.path.relpath(test_src)}")
                else:
                    # Create or update symlink
                    if os.path.islink(symlink_path):
                        current_target = os.readlink(symlink_path)
                        if current_target == test_src:
                            pass  # Already correct
                        else:
                            os.remove(symlink_path)
                            os.symlink(test_src, symlink_path)
                            print(f"  Updated symlink: test.py -> {os.path.relpath(test_src)}")
                    elif os.path.exists(symlink_path):
                        # Regular file exists where symlink should be -- skip
                        print(f"  WARNING: {os.path.relpath(symlink_path)} exists as a regular file, skipping symlink")
                    else:
                        os.symlink(test_src, symlink_path)
                        print(f"  Linked: test.py -> {os.path.relpath(test_src)}")
            else:
                tests_missing += 1
                print(f"  WARNING: No test found at {os.path.relpath(test_src)}")

    return synced, tests_found, tests_missing


def main():
    try:
        import cxas_scrapi
    except ImportError:
        print("Error: cxas-scrapi not installed. Activate venv (source .venv/bin/activate) and install cxas-scrapi first.")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Sync callback code from GECX platform to local test directories"
    )
    parser.add_argument(
        "--agent", default=None,
        help="Sync only this agent (by display_name)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be synced without writing files"
    )
    args = parser.parse_args()

    app_name = load_app_name()

    # List agents
    from cxas_scrapi.core.agents import Agents
    agents_client = Agents(app_name=app_name)
    try:
        agent_list = agents_client.list_agents()
    except Exception as e:
        print(f"Error: Failed to list agents: {e}")
        sys.exit(1)

    if not agent_list:
        print("No agents found in app.")
        return

    # Filter to a single agent if requested
    if args.agent:
        agent_list = [a for a in agent_list if getattr(a, "display_name", None) == args.agent]
        if not agent_list:
            print(f"Agent '{args.agent}' not found. Available agents:")
            all_agents = agents_client.list_agents()
            for a in all_agents:
                print(f"  - {getattr(a, 'display_name', '?')}")
            return

    total_synced = 0
    total_tests_found = 0
    total_tests_missing = 0

    for agent in agent_list:
        agent_name = getattr(agent, "display_name", None) or getattr(agent, "name", "unknown")
        print(f"\n--- {agent_name} ---")

        s, tf, tm = sync_agent_callbacks(app_name, agent_name, dry_run=args.dry_run)
        total_synced += s
        total_tests_found += tf
        total_tests_missing += tm

    # Summary
    print(f"\n{'=' * 50}")
    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}{total_synced} callbacks synced, "
          f"{total_tests_found} tests found, "
          f"{total_tests_missing} tests missing")


if __name__ == "__main__":
    main()
