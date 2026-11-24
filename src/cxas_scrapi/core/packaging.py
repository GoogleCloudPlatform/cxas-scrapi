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

"""Deterministic ZIP packaging utilities for CXAS apps."""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from pathlib import Path

# Standard CES app archive roots plus pythonEnvFiles.
VALID_ARCHIVE_ROOTS: frozenset[str] = frozenset(
    {
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
)

_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
_EXCLUDED_DIR_NAMES = frozenset({"__pycache__"})
_EXCLUDED_FILE_NAMES = frozenset({".DS_Store"})


def _copy_filtered_tree(src: Path, dst: Path) -> None:
    """Copy a directory tree while skipping hidden entries and bytecode."""
    for root, dirs, files in os.walk(src):
        rel_root = Path(root).relative_to(src)
        dirs[:] = sorted(
            d
            for d in dirs
            if not d.startswith(".") and d not in _EXCLUDED_DIR_NAMES
        )
        for fname in sorted(files):
            if (
                fname.startswith(".")
                or fname in _EXCLUDED_FILE_NAMES
                or fname.endswith((".pyc", ".pyo"))
            ):
                continue
            src_fpath = Path(root) / fname
            dst_fpath = dst / rel_root / fname
            dst_fpath.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_fpath, dst_fpath)


def package_app_archive(
    app_dir: Path,
    output_zip: Path,
    env_file: Path | None = None,
    *,
    wrapper_dir: str | None = None,
    resolved_env_vars: list[str] | None = None,
) -> Path:
    """Creates a deterministic ZIP archive from ``app_dir``.

    Includes only directories and files matching ``VALID_ARCHIVE_ROOTS`` at the
    top level of ``app_dir``. Sorts entries and normalizes ZIP metadata so
    identical inputs produce byte-identical archives.

    If ``resolved_env_vars`` is a list, ``"$env_var"`` variable defaults and
    boolean environment overrides in the staged app config are resolved from
    the staged ``environment.json`` (after ``env_file`` is applied) and the
    resolved variable names are appended to it. Raises ``ValueError`` if a
    placeholder cannot be resolved.
    """
    app_dir = Path(app_dir).resolve()
    output_zip = Path(output_zip).resolve()

    if not app_dir.is_dir():
        raise FileNotFoundError(f"App directory not found: {app_dir}")

    if env_file is not None:
        env_file = Path(env_file).resolve()
        if not env_file.is_file():
            raise FileNotFoundError(f"Environment file not found: {env_file}")

    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)

        for root_name in sorted(VALID_ARCHIVE_ROOTS):
            src_path = app_dir / root_name
            if not src_path.exists():
                continue
            dst_path = temp_dir / root_name
            if src_path.is_dir():
                _copy_filtered_tree(src_path, dst_path)
            elif src_path.is_file():
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dst_path)

        if env_file is not None:
            dst_env = temp_dir / "environment.json"
            shutil.copy2(env_file, dst_env)

        if resolved_env_vars is not None:
            # Local import: cxas_scrapi.utils imports core.apps, avoiding
            # circular import chains during package initialization.
            from cxas_scrapi.utils.env_vars import (  # noqa: PLC0415
                resolve_app_env_variables_in_dir,
            )
            from cxas_scrapi.utils.tracing.app_config import (  # noqa: PLC0415
                resolve_env_booleans_in_dir,
            )

            resolved_env_vars.extend(resolve_app_env_variables_in_dir(temp_dir))
            resolve_env_booleans_in_dir(str(temp_dir))

        entries: list[tuple[str, Path]] = []
        for root, dirs, files in os.walk(temp_dir):
            dirs.sort()
            for fname in sorted(files):
                fpath = Path(root) / fname
                rel = fpath.relative_to(temp_dir)
                if wrapper_dir:
                    rel = Path(wrapper_dir) / rel
                arcname = rel.as_posix()
                entries.append((arcname, fpath))

        entries.sort(key=lambda item: item[0])
        output_zip.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(
            output_zip, "w", compression=zipfile.ZIP_DEFLATED
        ) as zf:
            for arcname, fpath in entries:
                info = zipfile.ZipInfo(filename=arcname, date_time=_ZIP_EPOCH)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                zf.writestr(info, fpath.read_bytes())

    return output_zip
