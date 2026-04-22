"""CLI subcommands for setting up CXAS components."""

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
import logging
import sys

from cxas_scrapi.utils.local.create_utils import CreateUtils

logger = logging.getLogger(__name__)


def handle_local_create(args: argparse.Namespace) -> None:
    """Handles the 'local create' command."""
    type_name = getattr(args, "create_local_command", None)
    print(f"Creating local {type_name} template: {args.name}")

    create_utils = CreateUtils()
    try:
        tool_type = getattr(args, "tool_type", None)
        add_to_agent = getattr(args, "add_to_agent", None)
        app_dir = getattr(args, "app_dir", ".")

        if type_name == "agent":
            path = create_utils.create_agent(
                display_name=args.name, app_dir=app_dir
            )
        elif type_name == "tool":
            path = create_utils.create_tool(
                display_name=args.name,
                app_dir=app_dir,
                tool_type=tool_type,
                add_to_agent=add_to_agent,
            )
        print(f"Successfully created local template at: {path}")
    except Exception as e:
        print(f"Failed to create local template: {e}")
        sys.exit(1)
