"""Archive and zip extraction security utilities."""

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

import logging
import os
import stat
import zipfile

logger = logging.getLogger(__name__)

# Strict security boundaries for app archive extraction
MAX_FILE_SIZE = 1 * 1024 * 1024 * 1024  # 1 GB per file limit
MAX_TOTAL_SIZE = 5 * 1024 * 1024 * 1024  # 5 GB total cumulative limit
MAX_FILE_COUNT = 100_000  # Max total entries allowed


class ArchiveUtils:
    """Utilities for safely inspecting and extracting archive files."""

    @staticmethod
    def safe_extract_zip(zip_obj: zipfile.ZipFile, target_dir: str) -> None:
        """Safely extracts a ZipFile enforcing security constraints.

        Args:
            zip_obj: The opened ZipFile instance to extract.
            target_dir: Destination directory path.

        Raises:
            ValueError: If any entry violates path traversal, symlink, or size
                bounds.
        """
        target_dir_abs = os.path.abspath(target_dir)
        total_extracted_size = 0

        infolist = zip_obj.infolist()
        if len(infolist) > MAX_FILE_COUNT:
            raise ValueError(
                f"Archive contains too many entries: {len(infolist)} "
                f"(limit is {MAX_FILE_COUNT})"
            )

        for member in infolist:
            # 1. Zip Bomb checks (single file & cumulative totals)
            if member.file_size > MAX_FILE_SIZE:
                raise ValueError(
                    f"File '{member.filename}' exceeds max allowed "
                    f"uncompressed size ({member.file_size} > "
                    f"{MAX_FILE_SIZE} bytes)"
                )
            total_extracted_size += member.file_size
            if total_extracted_size > MAX_TOTAL_SIZE:
                raise ValueError(
                    "Archive total uncompressed size exceeds limit "
                    f"({total_extracted_size} > {MAX_TOTAL_SIZE} bytes)"
                )

            # 2. Zip Slip / Path Traversal protection
            member_path = os.path.abspath(
                os.path.join(target_dir_abs, member.filename)
            )
            try:
                common = os.path.commonpath([target_dir_abs, member_path])
            except ValueError as err:
                msg = (
                    "Cross-drive path traversal detected in "
                    f"'{member.filename}'"
                )
                raise ValueError(msg) from err

            if common != target_dir_abs:
                raise ValueError(
                    "Path traversal detected in archive entry: "
                    f"'{member.filename}'"
                )

            # 3. Symlink protection
            is_symlink = stat.S_ISLNK(member.external_attr >> 16)
            if is_symlink:
                raise ValueError(
                    "Symbolic link entries are not permitted in app "
                    f"archives: '{member.filename}'"
                )

            # 4. Extract safe member
            zip_obj.extract(member, target_dir_abs)
