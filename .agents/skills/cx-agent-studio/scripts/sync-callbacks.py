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

"""Sync callback code into evals/callback_tests/agents/ and create test.py symlinks.

Two modes:
  - Default (post-push): pull each agent's callbacks from the GECX platform.
  - --from-local <app_dir>: copy callback python_code.py files from the local
    app dir. Use this during the initial build, before the app is pushed —
    sync-callbacks.py would otherwise have nothing to pull.

Either way, after copying python_code.py the script looks for a matching
test.py at evals/callback_tests/tests/<agent>/<callback_type>/<base>/test.py
and creates a symlink at evals/callback_tests/agents/.../test.py. SCRAPI's
test_all_callbacks_in_app_dir requires test.py and python_code.py in the same
directory — without the symlink, tests are silently skipped.

Usage:
  python scripts/sync-callbacks.py                              # Pull all callbacks from platform
  python scripts/sync-callbacks.py --agent root_agent           # Pull only one agent from platform
  python scripts/sync-callbacks.py --from-local <app_dir>       # Copy from local app dir (pre-push)
  python scripts/sync-callbacks.py --dry-run                    # Show what would be synced
"""

import argparse
import glob
import json
import os
import re
import shutil
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


CALLBACK_TYPES = (
    "before_agent_callbacks",
    "after_agent_callbacks",
    "before_model_callbacks",
    "after_model_callbacks",
    "before_tool_callbacks",
    "after_tool_callbacks",
)


def _ensure_symlink(test_src, symlink_path, dry_run=False):
    """Create or update the agents/.../test.py symlink → tests/.../test.py."""
    if not os.path.exists(test_src):
        print(f"  WARNING: No test found at {os.path.relpath(test_src)}")
        return False, True  # tests_found, tests_missing
    if dry_run:
        print(f"  [dry-run] Would link: test.py -> {os.path.relpath(test_src)}")
        return True, False
    if os.path.islink(symlink_path):
        if os.readlink(symlink_path) == test_src:
            return True, False
        os.remove(symlink_path)
        os.symlink(test_src, symlink_path)
        print(f"  Updated symlink: test.py -> {os.path.relpath(test_src)}")
    elif os.path.exists(symlink_path):
        print(f"  WARNING: {os.path.relpath(symlink_path)} exists as a regular file, skipping symlink")
    else:
        os.symlink(test_src, symlink_path)
        print(f"  Linked: test.py -> {os.path.relpath(test_src)}")
    return True, False


def sync_from_local(app_dir, agent_filter=None, dry_run=False):
    """Copy callback python_code.py files from a local app dir into evals/callback_tests/agents/.

    Mirrors the directory naming used by sync_agent_callbacks (platform mode):
      <type>_callbacks → strip _callbacks → base; append _<idx> if multiple.
    """
    if not os.path.isdir(app_dir):
        print(f"Error: --from-local app_dir not found: {app_dir}")
        sys.exit(1)

    agents_root = os.path.join(app_dir, "agents")
    if not os.path.isdir(agents_root):
        print(f"Error: no agents/ dir under {app_dir}")
        sys.exit(1)

    total_synced = 0
    total_tests_found = 0
    total_tests_missing = 0

    for agent_name in sorted(os.listdir(agents_root)):
        agent_path = os.path.join(agents_root, agent_name)
        if not os.path.isdir(agent_path):
            continue
        if agent_filter and agent_name != agent_filter:
            continue
        # Collect callbacks grouped by type so we can index multiples consistently.
        per_type = {}
        for cb_type in CALLBACK_TYPES:
            type_dir = os.path.join(agent_path, cb_type)
            if not os.path.isdir(type_dir):
                continue
            entries = []
            for sub in sorted(os.listdir(type_dir)):
                code = os.path.join(type_dir, sub, "python_code.py")
                if os.path.isfile(code):
                    entries.append((sub, code))
            if entries:
                per_type[cb_type] = entries

        if not per_type:
            continue

        print(f"\n--- {agent_name} ---")
        for cb_type, entries in per_type.items():
            base = cb_type[: -len("_callbacks")] if cb_type.endswith("_callbacks") else cb_type
            use_index = len(entries) > 1
            for idx, (local_subdir, code_src) in enumerate(entries):
                base_name = f"{base}_{idx}" if use_index else base
                agent_cb_dir = os.path.join(AGENTS_DIR, agent_name, cb_type, base_name)
                code_dst = os.path.join(agent_cb_dir, "python_code.py")
                test_src = os.path.join(TESTS_DIR, agent_name, cb_type, base_name, "test.py")
                symlink_path = os.path.join(agent_cb_dir, "test.py")

                if dry_run:
                    print(f"  [dry-run] Would copy: {os.path.relpath(code_src)} -> {os.path.relpath(code_dst)}")
                else:
                    os.makedirs(agent_cb_dir, exist_ok=True)
                    shutil.copyfile(code_src, code_dst)
                    print(f"  Copied: {os.path.relpath(code_dst)} (from {os.path.relpath(code_src)})")
                total_synced += 1

                tf, tm = _ensure_symlink(test_src, symlink_path, dry_run=dry_run)
                total_tests_found += int(tf)
                total_tests_missing += int(tm)

    print(f"\n{'=' * 50}")
    prefix = "[dry-run] " if dry_run else ""
    print(f"{prefix}{total_synced} callbacks synced from {app_dir}, "
          f"{total_tests_found} tests found, "
          f"{total_tests_missing} tests missing")


def main():
    parser = argparse.ArgumentParser(
        description="Sync callback code into evals/callback_tests/agents/ and create test.py symlinks."
    )
    parser.add_argument(
        "--agent", default=None,
        help="Sync only this agent (by display_name in platform mode, by directory name in --from-local mode)"
    )
    parser.add_argument(
        "--from-local", default=None, metavar="APP_DIR",
        help="Copy callback code from a local app dir (e.g., <project>/cxas_app/<App>) instead of pulling from the platform. Use during initial build, pre-push."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be synced without writing files"
    )
    args = parser.parse_args()

    if args.from_local:
        sync_from_local(args.from_local, agent_filter=args.agent, dry_run=args.dry_run)
        return

    try:
        import cxas_scrapi  # noqa: F401
    except ImportError:
        print("Error: cxas-scrapi not installed. Activate venv (source .venv/bin/activate) and install cxas-scrapi first.")
        sys.exit(1)

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
