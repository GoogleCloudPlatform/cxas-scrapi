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

"""Validates all benchmark scenarios in the directory."""

import os
import pathlib
import sys
from collections.abc import Sequence

from absl import app

try:
    from skill_eval import scenario
except ImportError:
    sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
    from skill_eval import scenario


def main(argv: Sequence[str]) -> None:
    if len(argv) > 1:
        raise app.UsageError("Too many command-line arguments.")
    directory = pathlib.Path(__file__).resolve().parent

    has_errors = False

    for path in directory.iterdir():
        if path.suffix in (".yaml", ".yml"):
            try:
                scen = scenario.Scenario.from_file(str(path))
                for asset in scen.assets:
                    asset_path = scenario.get_asset_path(str(path), asset)
                    if not os.path.exists(asset_path):
                        raise ValueError(
                            f"Asset {asset} not found at {asset_path}"
                        )
                print(f"OK: {path.name}")
            except ValueError as e:
                print(f"FAIL: {path.name} - {e}", file=sys.stderr)
                has_errors = True

    if has_errors:
        print("Scenario validation failed.", file=sys.stderr)
        sys.exit(1)
    else:
        print("All scenarios are valid.")


if __name__ == "__main__":
    app.run(main)
