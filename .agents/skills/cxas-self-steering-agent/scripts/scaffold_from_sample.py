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

r"""Copies the bundled sample agent into a new CXAS app directory.

Example:
  python3 scaffold_from_sample.py --target-dir cxas_app/my-agent \
    --app-id 0a1b2c3d-... --project-id my-gcp-project --agent-name Booking_Agent
"""

import argparse
import json
import pathlib
import shutil
from typing import Any

SAMPLE_AGENT = "Support_Agent"


def _write_json(path: pathlib.Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--target-dir",
        required=True,
        help="New app directory, e.g. cxas_app/my-agent.",
    )
    parser.add_argument(
        "--app-id",
        required=True,
        help="CES app ID to push to (from `cxas create` or the console).",
    )
    parser.add_argument(
        "--project-id",
        required=True,
        help="GCP project for the BigQuery export setting in environment.json.",
    )
    parser.add_argument(
        "--display-name",
        default=None,
        help="App display name (defaults to the target directory name).",
    )
    parser.add_argument(
        "--agent-name",
        default=SAMPLE_AGENT,
        help=f"Root agent name (defaults to {SAMPLE_AGENT}).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace the target directory if it already exists.",
    )
    args = parser.parse_args()

    sample = (
        pathlib.Path(__file__).resolve().parent.parent
        / "resources"
        / "sample-agent"
    )
    target = pathlib.Path(args.target_dir).resolve()
    if target.exists():
        if not args.overwrite:
            raise SystemExit(
                f"{target} already exists. Pass --overwrite to replace it."
            )
        shutil.rmtree(target)
    shutil.copytree(sample, target)

    app_path = target / "app.json"
    app = json.loads(app_path.read_text(encoding="utf-8"))
    app["name"] = args.app_id
    app["displayName"] = args.display_name or target.name
    app["rootAgent"] = args.agent_name
    _write_json(app_path, app)

    env_path = target / "environment.json"
    env = json.loads(env_path.read_text(encoding="utf-8"))
    env["app"]["loggingSettings"]["bigqueryExportSettings"]["project"] = (
        args.project_id
    )
    _write_json(env_path, env)

    if args.agent_name != SAMPLE_AGENT:
        agent_dir = target / "agents" / args.agent_name
        (target / "agents" / SAMPLE_AGENT).rename(agent_dir)
        old_json = agent_dir / f"{SAMPLE_AGENT}.json"
        raw = old_json.read_text(encoding="utf-8").replace(
            f"agents/{SAMPLE_AGENT}/", f"agents/{args.agent_name}/"
        )
        old_json.unlink()
        agent = json.loads(raw)
        agent["name"] = args.agent_name
        agent["displayName"] = args.agent_name
        _write_json(agent_dir / f"{args.agent_name}.json", agent)

    print(f"Created {target}")
    print(f"  app id:       {args.app_id}")
    print(f"  display name: {app['displayName']}")
    print(f"  root agent:   {args.agent_name}")
    print(
        "Next: rewrite the instruction, GREETING and agent description,"
        " update the router lists, replace the sample's order tools and"
        " variables, then run cxas lint, check_tool_visibility.py and"
        " run_locally.py before pushing."
    )


if __name__ == "__main__":
    main()
