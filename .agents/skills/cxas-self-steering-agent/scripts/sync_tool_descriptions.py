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
after editing a docstring.

Example:
  python3 sync_tool_descriptions.py cxas_app/my-agent           # update files
  python3 sync_tool_descriptions.py cxas_app/my-agent --check   # only report
"""

import argparse
import ast
import json
import pathlib
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("app_dir")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report differences without writing files; exit 1 if any.",
    )
    args = parser.parse_args()

    stale, skipped = [], []
    for code in sorted(
        pathlib.Path(args.app_dir).glob(
            "tools/*/python_function/python_code.py"
        )
    ):
        tool = code.parent.parent.name
        spec_path = code.parent.parent / f"{tool}.json"
        if not spec_path.exists():
            skipped.append(f"{tool} (no {tool}.json)")
            continue
        fn = next(
            (
                n
                for n in ast.parse(code.read_text(encoding="utf-8")).body
                if isinstance(n, ast.FunctionDef) and n.name == tool
            ),
            None,
        )
        if fn is None:
            skipped.append(f"{tool} (no function named {tool})")
            continue
        docstring = ast.get_docstring(fn) or ""
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        if spec.get("pythonFunction", {}).get("description") == docstring:
            continue
        stale.append(tool)
        if not args.check:
            spec.setdefault("pythonFunction", {})["description"] = docstring
            spec_path.write_text(
                json.dumps(spec, indent=2) + "\n", encoding="utf-8"
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
