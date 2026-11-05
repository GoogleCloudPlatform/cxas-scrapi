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

"""Packaging utilities for CXAS apps."""

import os
import zipfile
from pathlib import Path

# All standard roots plus pythonEnvFiles
VALID_ARCHIVE_ROOTS = {
    "app.yaml",
    "app.json",
    "global_instruction.txt",
    "environment.json",
    "agents",
    "tools",
    "examples",
    "guardrails",
    "toolsets",
    "evaluations",
    "evaluationDatasets",
    "evaluationExpectations",
    ".github/workflows",
    "pythonEnvFiles",
}


def package_app_archive(
    app_dir: Path, output_zip: Path, env_file: Path | None = None
) -> Path:
    """Creates a deterministic ZIP archive from app_dir.

    Includes only directories and files matching VALID_ARCHIVE_ROOTS at the
    top level of app_dir. Sorts entries to ensure deterministic output.
    """
    app_dir = app_dir.resolve()

    with zipfile.ZipFile(
        output_zip, "w", compression=zipfile.ZIP_DEFLATED
    ) as zf:
        if env_file:
            print(f"Included custom environment file: {env_file}")
            # Add env file at the root, regardless of original location
            zf.write(env_file, arcname="environment.json")

        # Collect paths to add
        paths_to_add = []
        for root, dirs, files in os.walk(app_dir):
            rel_root = Path(root).relative_to(app_dir)

            # Filter top level directories
            if str(rel_root) == ".":
                dirs[:] = [
                    d
                    for d in dirs
                    if d in VALID_ARCHIVE_ROOTS and not d.startswith(".")
                ]
                files[:] = [
                    f
                    for f in files
                    if f in VALID_ARCHIVE_ROOTS and not f.startswith(".")
                ]
            else:
                # Exclude hidden directories in subfolders
                dirs[:] = [d for d in dirs if not d.startswith(".")]

            for f in files:
                if not f.startswith(".") and f not in {
                    "__pycache__",
                    ".DS_Store",
                }:
                    paths_to_add.append(Path(root) / f)

        # Sort paths for deterministic ZIP
        for fpath in sorted(paths_to_add):
            arcname = fpath.relative_to(app_dir)
            if (env_file and fpath.resolve() == env_file.resolve()) or (
                env_file and str(arcname) == "environment.json"
            ):
                continue
            zf.write(fpath, arcname=arcname)

    return output_zip
