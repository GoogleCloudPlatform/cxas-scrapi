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

"""Copies each Python tool's docstring into the description of its tool JSON.

The model reads the tool description, so keep the JSON in step with the code
after editing a docstring. If a Python tool's `<tool>.json` wrapper does not
exist yet, running without `--check` creates it automatically.

Example:
  python3 sync_tool_descriptions.py cxas_app/my-agent           # update files
  python3 sync_tool_descriptions.py cxas_app/my-agent --check   # only report
"""

import argparse
import ast
import json
import pathlib
import sys


def sync(
    app_dir: pathlib.Path | str,
    write: bool = True,
    *,
    check: bool = False,
) -> list[str]:
    """Syncs or checks tool JSON descriptions against Python docstrings.

    Args:
      app_dir: Path to the CXAS app directory.
      write: Whether to write updated or missing tool JSON files.
      check: Keyword alias for `write=False`.

    Returns:
      List of tool names that were updated/created (when `write=True`) or out
      of date / missing (when `write=False`).
    """
    if check:
        write = False
    stale, _ = _sync_with_details(pathlib.Path(app_dir), write=write)
    return stale


def _sync_with_details(
    app_path: pathlib.Path, write: bool = True
) -> tuple[list[str], list[str]]:
    """Returns `(stale_or_updated, skipped)` for `app_path`."""
    stale, skipped = [], []
    for code in sorted(app_path.glob("tools/*/python_function/python_code.py")):
        tool = code.parent.parent.name
        spec_path = code.parent.parent / f"{tool}.json"
        fn = next(
            (
                n
                for n in ast.parse(code.read_text(encoding="utf-8")).body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == tool
            ),
            None,
        )
        if fn is None:
            skipped.append(f"{tool} (no function named {tool})")
            continue
        docstring = ast.get_docstring(fn) or ""
        if not spec_path.exists():
            stale.append(tool)
            if write:
                spec = {
                    "name": tool,
                    "pythonFunction": {
                        "name": tool,
                        "pythonCode": (
                            f"tools/{tool}/python_function/python_code.py"
                        ),
                        "description": docstring,
                    },
                    "executionType": "SYNCHRONOUS",
                    "displayName": tool,
                }
                spec_path.write_text(
                    json.dumps(spec, indent=2) + "\n", encoding="utf-8"
                )
            continue
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        if spec.get("pythonFunction", {}).get("description") == docstring:
            continue
        stale.append(tool)
        if write:
            spec.setdefault("pythonFunction", {})["description"] = docstring
            spec_path.write_text(
                json.dumps(spec, indent=2) + "\n", encoding="utf-8"
            )
    return stale, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("app_dir")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report differences without writing files; exit 1 if any.",
    )
    args = parser.parse_args()

    stale, skipped = _sync_with_details(
        pathlib.Path(args.app_dir), write=not args.check
    )
    verb = "out of date" if args.check else "updated"
    print(
        f"{verb}: {', '.join(stale)}"
        if stale
        else "all tool descriptions match their docstrings"
    )
    if skipped:
        print(f"skipped: {', '.join(skipped)}")
    sys.exit(1 if (stale or skipped) and args.check else 0)


if __name__ == "__main__":
    main()
