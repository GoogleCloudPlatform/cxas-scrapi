"""Tests for archive security utilities."""

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

import io
import typing
import zipfile

import pytest

from cxas_scrapi.utils import archive_utils
from cxas_scrapi.utils.archive_utils import ArchiveUtils


def test_safe_extract_zip_normal(tmp_path: typing.Any) -> None:
    """Verifies that normal files extract without issue."""
    target_dir = tmp_path / "extracted"
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as z:
        z.writestr("app.yaml", "name: My App")
        z.writestr("tools/search.json", '{"name": "search"}')

    zip_buffer.seek(0)
    with zipfile.ZipFile(zip_buffer, "r") as z:
        ArchiveUtils.safe_extract_zip(z, str(target_dir))

    assert (target_dir / "app.yaml").read_text() == "name: My App"
    assert (target_dir / "tools" / "search.json").read_text() == (
        '{"name": "search"}'
    )


def test_safe_extract_zip_nested_directories_allowed(
    tmp_path: typing.Any,
) -> None:
    """Verifies that legitimate deeply nested subfolders extract correctly."""
    target_dir = tmp_path / "extracted_nested"
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as z:
        z.writestr("flows/navigation/flow.yaml", "name: Navigation")
        z.writestr("flows/navigation/pages/home.json", '{"title": "Home"}')

    zip_buffer.seek(0)
    with zipfile.ZipFile(zip_buffer, "r") as z:
        ArchiveUtils.safe_extract_zip(z, str(target_dir))

    assert (
        target_dir / "flows" / "navigation" / "flow.yaml"
    ).read_text() == "name: Navigation"
    assert (
        target_dir / "flows" / "navigation" / "pages" / "home.json"
    ).read_text() == '{"title": "Home"}'


def test_safe_extract_zip_relative_traversal_rejected(
    tmp_path: typing.Any,
) -> None:
    """Verifies that Zip Slip attempts with ../ are rejected."""
    target_dir = tmp_path / "safe_dir"
    target_dir.mkdir()
    outside_file = tmp_path / "evil.txt"

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as z:
        z.writestr("../../evil.txt", "malicious content")

    zip_buffer.seek(0)
    with (
        zipfile.ZipFile(zip_buffer, "r") as z,
        pytest.raises(ValueError, match="Path traversal detected"),
    ):
        ArchiveUtils.safe_extract_zip(z, str(target_dir))

    assert not outside_file.exists()


def test_safe_extract_zip_absolute_path_rejected(
    tmp_path: typing.Any,
) -> None:
    """Verifies that absolute paths (e.g. /etc/passwd) are rejected."""
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    abs_file = tmp_path / "abs_evil.txt"

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as z:
        z.writestr(str(abs_file), "malicious content")

    zip_buffer.seek(0)
    with (
        zipfile.ZipFile(zip_buffer, "r") as z,
        pytest.raises(ValueError, match="Path traversal detected"),
    ):
        ArchiveUtils.safe_extract_zip(z, str(target_dir))

    assert not abs_file.exists()


def test_safe_extract_zip_max_file_size_exceeded(
    tmp_path: typing.Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies that single files exceeding MAX_FILE_SIZE raise ValueError."""
    monkeypatch.setattr(archive_utils, "MAX_FILE_SIZE", 100)
    target_dir = tmp_path / "extracted"
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as z:
        z.writestr("large.bin", b"x" * 200)

    zip_buffer.seek(0)
    with (
        zipfile.ZipFile(zip_buffer, "r") as z,
        pytest.raises(
            ValueError, match="exceeds max allowed uncompressed size"
        ),
    ):
        ArchiveUtils.safe_extract_zip(z, str(target_dir))


def test_safe_extract_zip_max_total_size_exceeded(
    tmp_path: typing.Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies that size exceeding MAX_TOTAL_SIZE raises ValueError."""
    monkeypatch.setattr(archive_utils, "MAX_TOTAL_SIZE", 100)
    target_dir = tmp_path / "extracted"
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as z:
        z.writestr("file1.bin", b"x" * 60)
        z.writestr("file2.bin", b"x" * 60)

    zip_buffer.seek(0)
    with (
        zipfile.ZipFile(zip_buffer, "r") as z,
        pytest.raises(
            ValueError,
            match="Archive total uncompressed size exceeds limit",
        ),
    ):
        ArchiveUtils.safe_extract_zip(z, str(target_dir))


def test_safe_extract_zip_max_file_count_exceeded(
    tmp_path: typing.Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies that entry count exceeding MAX_FILE_COUNT raises ValueError."""
    monkeypatch.setattr(archive_utils, "MAX_FILE_COUNT", 2)
    target_dir = tmp_path / "extracted"
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as z:
        z.writestr("f1.txt", "1")
        z.writestr("f2.txt", "2")
        z.writestr("f3.txt", "3")

    zip_buffer.seek(0)
    with (
        zipfile.ZipFile(zip_buffer, "r") as z,
        pytest.raises(ValueError, match="Archive contains too many entries"),
    ):
        ArchiveUtils.safe_extract_zip(z, str(target_dir))


def test_safe_extract_zip_symlink_rejected(tmp_path: typing.Any) -> None:
    """Verifies that symbolic links are rejected."""
    target_dir = tmp_path / "extracted"
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as z:
        zinfo = zipfile.ZipInfo("symlink_entry")
        # 0o120000 is S_IFLNK
        zinfo.external_attr = 0o120777 << 16
        z.writestr(zinfo, "/etc/passwd")

    zip_buffer.seek(0)
    with (
        zipfile.ZipFile(zip_buffer, "r") as z,
        pytest.raises(
            ValueError, match="Symbolic link entries are not permitted"
        ),
    ):
        ArchiveUtils.safe_extract_zip(z, str(target_dir))
