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

r"""Runs a scripted text conversation against a deployed CXAS app.

Prints the agent's reply to each customer message, then the command that shows
the stored conversation with tool calls, results and state changes. Uses
application default credentials (gcloud auth application-default login).

Example:
  python3 run_conversation.py \
    projects/my-project/locations/us/apps/0a1b2c3d-... \
    "Hi." "Where is my order 1002?" "That's all, thanks."
"""

import argparse
import uuid

from cxas_scrapi.core.sessions import Sessions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "app_name", help="projects/<project>/locations/<location>/apps/<app-id>"
    )
    parser.add_argument("turns", nargs="+", help="Customer messages, in order.")
    parser.add_argument(
        "--session-id",
        default=None,
        help="Session ID to use (defaults to a random one).",
    )
    args = parser.parse_args()

    session_id = args.session_id or f"test-{uuid.uuid4().hex[:8]}"
    sessions = Sessions(app_name=args.app_name)
    for i, text in enumerate(args.turns):
        response = sessions.run(session_id=session_id, text=text)
        replies, ended = [], False
        for output in getattr(response, "outputs", None) or []:
            if getattr(output, "text", None):
                replies.append(output.text)
            if getattr(output, "end_session", None):
                ended = True
        print(f"customer: {text}")
        print(f"agent:    {' | '.join(replies) or '(no reply)'}")
        if ended:
            print("[conversation ended]")
            if i < len(args.turns) - 1:
                print(f"skipped {len(args.turns) - 1 - i} remaining message(s)")
            break

    print(f"\nsession id: {session_id}")
    print(
        "To see tool calls, results and state changes (the stored conversation"
        " can take up to a minute to show up):"
    )
    print(
        f"  cxas trace get {session_id} --app-name {args.app_name}"
        " --format text"
    )


if __name__ == "__main__":
    main()
