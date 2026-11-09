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
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

from cxas_scrapi.core.python_bundler import (
    MAX_TOTAL_ARCHIVE_BYTES,
    BundleSizeError,
    bundle_shared_python,
    synthesize_python_env_files,
)

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
    app_dir: Path,
    output_zip: Path,
    env_file: Path | None = None,
    *,
    native_python_env: bool = False,
    wrapper_dir: str | None = None,
    resolved_env_vars: list[str] | None = None,
) -> Path:
    """Creates a deterministic ZIP archive from app_dir.

    Includes only directories and files matching VALID_ARCHIVE_ROOTS at the
    top level of app_dir. Sorts entries to ensure deterministic output.

    If `resolved_env_vars` is a list, `"$env_var"` variable defaults in the
    staged app config are resolved from the staged `environment.json` (after
    `env_file` is applied) and the resolved names are appended to it. Raises
    `ValueError` if a placeholder cannot be resolved.
    """
    app_dir = app_dir.resolve()

    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)

        # Stage all files to temp directory
        for root, dirs, files in os.walk(app_dir):
            rel_root = Path(root).relative_to(app_dir)
            if str(rel_root) == ".":
                dirs[:] = [
                    d
                    for d in dirs
                    if (d in VALID_ARCHIVE_ROOTS or d == "python_lib")
                    and not d.startswith(".")
                ]
                files[:] = [
                    f
                    for f in files
                    if (f in VALID_ARCHIVE_ROOTS or f == "python_lib")
                    and not f.startswith(".")
                ]
            else:
                dirs[:] = [d for d in dirs if not d.startswith(".")]

            for f in files:
                if not f.startswith(".") and f not in {
                    "__pycache__",
                    ".DS_Store",
                }:
                    src_fpath = Path(root) / f
                    dst_fpath = temp_dir / rel_root / f
                    dst_fpath.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_fpath, dst_fpath)

        if env_file:
            dst_env = temp_dir / "environment.json"
            shutil.copy2(env_file, dst_env)

        if resolved_env_vars is not None:
            # Local import: cxas_scrapi.utils imports core.apps, which imports
            # this module.
            from cxas_scrapi.utils.env_vars import (  # noqa: PLC0415
                resolve_app_env_variables_in_dir,
            )

            resolved_env_vars.extend(resolve_app_env_variables_in_dir(temp_dir))

        synthesize_python_env_files(temp_dir)

        bundle_shared_python(temp_dir, native_python_env=native_python_env)

        # Re-walk temp dir for packaging
        paths_to_add = []
        for root, dirs, files in os.walk(temp_dir):
            rel_root = Path(root).relative_to(temp_dir)
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
            for f in files:
                if not f.startswith(".") and f not in {
                    "__pycache__",
                    ".DS_Store",
                }:
                    paths_to_add.append(Path(root) / f)

        # Check total size
        total_size = sum(p.stat().st_size for p in paths_to_add)
        if total_size > MAX_TOTAL_ARCHIVE_BYTES:
            raise BundleSizeError(
                f"Total archive size {total_size} exceeds maximum {MAX_TOTAL_ARCHIVE_BYTES} bytes"  # noqa: E501
            )

        limit_time = time.mktime((1980, 1, 1, 0, 0, 0, 0, 0, 0))
        for root, dirs, files in os.walk(temp_dir):
            for name in files:
                path = os.path.join(root, name)
                if os.path.getmtime(path) < limit_time:
                    os.utime(path, None)
            for name in dirs:
                path = os.path.join(root, name)
                if os.path.getmtime(path) < limit_time:
                    os.utime(path, None)

        with zipfile.ZipFile(
            output_zip, "w", compression=zipfile.ZIP_DEFLATED
        ) as zf:
            for fpath in sorted(paths_to_add):
                arcname = fpath.relative_to(temp_dir)
                if wrapper_dir:
                    arcname = Path(wrapper_dir) / arcname
                zf.write(fpath, arcname=str(arcname).replace(os.sep, "/"))

    return output_zip
