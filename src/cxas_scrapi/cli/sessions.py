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

"""CLI command handlers for interactive sessions."""

from __future__ import annotations

import argparse
import sys

from cxas_scrapi.core.sessions import Sessions


def run_session(args: argparse.Namespace) -> None:
    """Handles the 'run-session' command."""
    try:
        session_client = Sessions(args.app_name)
        session_id = session_client.create_session_id()

        while True:
            try:
                user_input = input()
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input.strip():
                continue

            res = session_client.run(
                session_id=session_id,
                text=user_input,
                modality=args.modality,
                use_tool_fakes=args.use_tool_fakes,
            )
            session_client.parse_result(res)
    except Exception as e:
        print(f"Failed to run session: {e}")
        sys.exit(1)
