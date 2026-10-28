"""Packages local files into a zip bundle for the CXAS AI assistant (Arc).

The bundle is pure transport: files are copied verbatim (no parsing or format
conversion) with their paths relative to the common root of the requested
inputs, plus a small `arc_bundle.json` manifest recording where the files came
from so Arc can map any changes it exports back to the same paths.

Size limits are enforced on the client so oversized bundles fail fast with a
breakdown of the largest directories instead of being rejected server-side.
"""

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

from __future__ import annotations

import collections
import dataclasses
import datetime
import io
import json
import os
import re
import time
import zipfile
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

MANIFEST_NAME = "arc_bundle.json"
BUNDLE_VERSION = 1

# The Arc server eagerly unpacks attached zips into the session workspace only
# up to 20 MiB, and caps inline attachments at the same size.
SERVER_MAX_ZIP_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_FILES = 10000

EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".jj",
        "__pycache__",
        "node_modules",
    }
)
# Build output trees (e.g. `bazel-bin`, `bazel-out`).
EXCLUDED_DIR_PREFIXES = ("bazel-",)
EXCLUDED_FILE_SUFFIXES = frozenset(
    {
        ".bin",
        ".flac",
        ".gif",
        ".gz",
        ".jar",
        ".jpeg",
        ".jpg",
        ".mov",
        ".mp3",
        ".mp4",
        ".ogg",
        ".par",
        ".pcm",
        ".png",
        ".pyc",
        ".so",
        ".tar",
        ".tgz",
        ".wav",
        ".zip",
    }
)

DEFAULT_UPLOAD_PREFIX = "agent-assist-uploads"
_BUNDLE_FILE_PREFIX = "arc_bundle"
_RESOURCE_MANAGER_URL = "https://cloudresourcemanager.googleapis.com/v3"
_RESOURCE_MANAGER_TIMEOUT_SECONDS = 30


class BundleError(ValueError):
    """Raised when a bundle cannot be built (bad paths or limits exceeded)."""


@dataclasses.dataclass(frozen=True)
class BundleLimits:
    """Client-side limits for a bundle."""

    max_zip_bytes: int = SERVER_MAX_ZIP_BYTES
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES
    max_files: int = DEFAULT_MAX_FILES


@dataclasses.dataclass
class BundleFile:
    """A single file selected for the bundle."""

    abs_path: str
    rel_path: str
    size: int


@dataclasses.dataclass
class Bundle:
    """A collected bundle, before or after it is zipped."""

    root: str
    inputs: list[str]
    files: list[BundleFile]
    excluded_count: int
    zip_bytes: bytes | None = None
    # Set when an existing .zip was attached as-is rather than built here.
    source_zip: str | None = None

    @property
    def uncompressed_bytes(self) -> int:
        """Total size of the bundled files before compression."""
        return sum(f.size for f in self.files)


def resolve_input_path(raw: str, working_dir: str | None = None) -> str:
    """Resolves an absolute or relative path to an existing absolute path."""
    working_dir = working_dir or os.getcwd()
    raw = raw.strip()
    resolved = raw if os.path.isabs(raw) else os.path.join(working_dir, raw)
    resolved = os.path.normpath(resolved)
    if not os.path.exists(resolved):
        raise BundleError(f"Attach path does not exist: {raw} ({resolved})")
    return resolved


def _is_excluded_dir(name: str) -> bool:
    return name in EXCLUDED_DIR_NAMES or name.startswith(EXCLUDED_DIR_PREFIXES)


def _is_excluded_file(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in EXCLUDED_FILE_SUFFIXES


def collect(
    raw_paths: Sequence[str],
    working_dir: str | None = None,
) -> Bundle:
    """Collects files under the given paths, applying default exclusions."""
    if not raw_paths:
        raise BundleError("No attach paths provided.")
    abs_inputs = [resolve_input_path(p, working_dir) for p in raw_paths]
    anchors = [
        p if os.path.isdir(p) else os.path.dirname(p) for p in abs_inputs
    ]
    root = os.path.commonpath(anchors)

    seen: set[str] = set()
    files: list[BundleFile] = []
    excluded = 0

    def _add(abs_path: str) -> None:
        nonlocal excluded
        if abs_path in seen:
            return
        seen.add(abs_path)
        if os.path.islink(abs_path) or _is_excluded_file(abs_path):
            excluded += 1
            return
        files.append(
            BundleFile(
                abs_path=abs_path,
                rel_path=os.path.relpath(abs_path, root).replace(os.sep, "/"),
                size=os.path.getsize(abs_path),
            )
        )

    for path in abs_inputs:
        if os.path.isfile(path):
            _add(path)
            continue
        for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
            kept = sorted(d for d in dirnames if not _is_excluded_dir(d))
            excluded += len(dirnames) - len(kept)
            dirnames[:] = kept
            for name in sorted(filenames):
                _add(os.path.join(dirpath, name))

    files.sort(key=lambda f: f.rel_path)
    return Bundle(
        root=root,
        inputs=[
            os.path.relpath(p, root).replace(os.sep, "/") for p in abs_inputs
        ],
        files=files,
        excluded_count=excluded,
    )


def _largest_dirs(bundle: Bundle, top_n: int = 5) -> list[str]:
    """Summarizes the heaviest entries one level below each input."""
    inputs = sorted(
        (p for p in bundle.inputs if p != "."), key=len, reverse=True
    )
    sizes: collections.Counter[str] = collections.Counter()
    for f in bundle.files:
        prefix = next((p for p in inputs if f.rel_path.startswith(p + "/")), "")
        rest = f.rel_path[len(prefix) + 1 :] if prefix else f.rel_path
        head = rest.split("/", 1)
        entry = head[0] + ("/" if len(head) > 1 else "")
        sizes[f"{prefix}/{entry}" if prefix else entry] += f.size
    return [
        f"  {name} = {_fmt_bytes(size)}"
        for name, size in sizes.most_common(top_n)
    ]


def _fmt_bytes(num: int) -> str:
    if num >= 1024 * 1024:
        return f"{num / (1024 * 1024):.1f} MiB"
    if num >= 1024:
        return f"{num / 1024:.1f} KiB"
    return f"{num} B"


def _limit_error(message: str, bundle: Bundle) -> BundleError:
    lines = [message, "Largest entries:", *_largest_dirs(bundle)]
    lines.append(
        "Attach narrower paths (e.g. only the app directory and the eval files"
        " the evals reference) and retry."
    )
    return BundleError("\n".join(lines))


def check_limits(bundle: Bundle, limits: BundleLimits) -> None:
    """Raises BundleError if file count or uncompressed size exceed limits."""
    if not bundle.files:
        raise BundleError(
            "No files to attach after applying default exclusions."
        )
    if len(bundle.files) > limits.max_files:
        raise _limit_error(
            f"Bundle has {len(bundle.files)} files (limit {limits.max_files}).",
            bundle,
        )
    if bundle.uncompressed_bytes > limits.max_uncompressed_bytes:
        raise _limit_error(
            f"Bundle is {_fmt_bytes(bundle.uncompressed_bytes)} uncompressed"
            f" (limit {_fmt_bytes(limits.max_uncompressed_bytes)}).",
            bundle,
        )


def build_manifest(bundle: Bundle) -> dict[str, object]:
    """Builds the provenance manifest stored at the root of the zip."""
    return {
        "version": BUNDLE_VERSION,
        "source": {"type": "local", "root": bundle.root},
        "inputs": bundle.inputs,
        "file_count": len(bundle.files),
        "uncompressed_bytes": bundle.uncompressed_bytes,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def write_zip(bundle: Bundle, limits: BundleLimits) -> bytes:
    """Zips the bundle with its manifest and enforces the compressed limit."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            MANIFEST_NAME, json.dumps(build_manifest(bundle), indent=2) + "\n"
        )
        for f in bundle.files:
            zf.write(f.abs_path, arcname=f.rel_path)
    data = buf.getvalue()
    if len(data) > limits.max_zip_bytes:
        raise _limit_error(
            f"Bundle zip is {_fmt_bytes(len(data))} (limit"
            f" {_fmt_bytes(limits.max_zip_bytes)}).",
            bundle,
        )
    bundle.zip_bytes = data
    return data


def _is_zip_input(abs_path: str) -> bool:
    return os.path.isfile(abs_path) and abs_path.lower().endswith(".zip")


def _prebuilt(abs_path: str, limits: BundleLimits) -> Bundle:
    """Wraps an existing .zip (e.g. a console export) as a bundle, unchanged."""
    try:
        with zipfile.ZipFile(abs_path) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
    except zipfile.BadZipFile as e:
        raise BundleError(f"Not a valid zip file: {abs_path}") from e
    bundle = Bundle(
        root=os.path.dirname(abs_path),
        inputs=[os.path.basename(abs_path)],
        files=[
            BundleFile(
                abs_path=f"{abs_path}!{i.filename}",
                rel_path=i.filename,
                size=i.file_size,
            )
            for i in infos
        ],
        excluded_count=0,
        source_zip=abs_path,
    )
    check_limits(bundle, limits)
    with open(abs_path, "rb") as f:
        data = f.read()
    if len(data) > limits.max_zip_bytes:
        raise _limit_error(
            f"Zip is {_fmt_bytes(len(data))} (limit"
            f" {_fmt_bytes(limits.max_zip_bytes)}).",
            bundle,
        )
    bundle.zip_bytes = data
    return bundle


def build(
    raw_paths: Sequence[str],
    limits: BundleLimits | None = None,
    working_dir: str | None = None,
) -> Bundle:
    """Collects, validates, and zips the given paths.

    A single `.zip` input is attached as-is (after validation and size checks)
    instead of being re-zipped; archives are otherwise excluded.

    Args:
        raw_paths: Files or directories (absolute or relative paths).
        limits: Client-side size and file-count limits.
        working_dir: Directory relative paths resolve against.

    Returns:
        The bundle, with `zip_bytes` set.

    Raises:
        BundleError: If a path is invalid or a limit is exceeded.
    """
    limits = limits or BundleLimits()
    if limits.max_zip_bytes > SERVER_MAX_ZIP_BYTES:
        raise BundleError(
            "max_zip_bytes cannot exceed the server limit"
            f" ({_fmt_bytes(SERVER_MAX_ZIP_BYTES)})."
        )
    if raw_paths:
        abs_inputs = [resolve_input_path(p, working_dir) for p in raw_paths]
        zips = [p for p in abs_inputs if _is_zip_input(p)]
        if zips and len(abs_inputs) > 1:
            raise BundleError(
                "A .zip must be attached on its own; attach either the zip or"
                f" the files/directories, not both: {', '.join(raw_paths)}"
            )
        if zips:
            return _prebuilt(zips[0], limits)
    bundle = collect(raw_paths, working_dir)
    check_limits(bundle, limits)
    write_zip(bundle, limits)
    return bundle


def format_report(
    bundle: Bundle, max_listed: int = 200, uploaded_uri: str | None = None
) -> str:
    """Renders a human-readable summary of the bundle contents."""
    lines = [
        "### [Attachment Bundle]",
        f"- **Root**: `{bundle.root}`",
        f"- **Inputs**: {', '.join(f'`{p}`' for p in bundle.inputs)}",
        f"- **Files**: {len(bundle.files)} (excluded: {bundle.excluded_count})",
        f"- **Uncompressed**: {_fmt_bytes(bundle.uncompressed_bytes)}",
    ]
    if bundle.zip_bytes is not None:
        lines.append(f"- **Zip**: {_fmt_bytes(len(bundle.zip_bytes))}")
    if uploaded_uri:
        lines.append(f"- **Uploaded**: `{uploaded_uri}`")
    lines.append("")
    lines.extend(
        f"  {f.rel_path} ({_fmt_bytes(f.size)})"
        for f in bundle.files[:max_listed]
    )
    if len(bundle.files) > max_listed:
        lines.append(f"  ... and {len(bundle.files) - max_listed} more")
    return "\n".join(lines)


def bundle_file_name(bundle: Bundle) -> str:
    """Returns a filesystem/object-safe file name for the zipped bundle."""
    if bundle.source_zip:
        base = os.path.basename(bundle.source_zip)
        return re.sub(r"[^a-zA-Z0-9._-]", "_", base)
    base = os.path.basename(bundle.root.rstrip(os.sep)) or "bundle"
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", base)
    return f"{_BUNDLE_FILE_PREFIX}_{safe}.zip"


def default_artifacts_bucket(project_id: str) -> str:
    """Returns the default artifacts bucket name for a project."""
    return f"{project_id}-agent-assist-artifacts"


def _split_gcs_uri(gcs_uri: str) -> tuple[str, str]:
    if not gcs_uri.startswith("gs://"):
        raise BundleError(f"Expected a gs:// URI, got: {gcs_uri}")
    bucket, _, object_name = gcs_uri[len("gs://") :].partition("/")
    if not bucket or not object_name:
        raise BundleError(f"Expected gs://bucket/object, got: {gcs_uri}")
    return bucket, object_name


def upload_bundle(
    local_path: str,
    bucket: str,
    *,
    credentials: Any,
    project: str | None = None,
    prefix: str = DEFAULT_UPLOAD_PREFIX,
) -> str:
    """Uploads a zipped bundle to Cloud Storage and returns its gs:// URI.

    Args:
        local_path: Path to the local zip file to upload.
        bucket: Destination bucket name (without `gs://`).
        credentials: Google credentials used for the storage client.
        project: Optional project used for client billing/quota.
        prefix: Object name prefix (folder) inside the bucket.

    Returns:
        The `gs://{bucket}/{prefix}/{epoch_ms}_{basename}` URI.
    """
    from google.cloud import storage  # noqa: PLC0415

    bucket = bucket.removeprefix("gs://").rstrip("/")
    object_name = (
        f"{prefix.strip('/')}/{int(time.time() * 1000)}_"
        f"{os.path.basename(local_path)}"
    )
    client = storage.Client(credentials=credentials, project=project)
    blob = client.bucket(bucket).blob(object_name)
    blob.upload_from_filename(local_path, content_type="application/zip")
    return f"gs://{bucket}/{object_name}"


def download_gcs_uri(gcs_uri: str, dest_dir: str, *, credentials: Any) -> str:
    """Downloads a gs:// object into dest_dir, returning the local path."""
    from google.cloud import storage  # noqa: PLC0415

    bucket, object_name = _split_gcs_uri(gcs_uri)
    os.makedirs(dest_dir, exist_ok=True)
    local_path = os.path.join(dest_dir, os.path.basename(object_name))
    client = storage.Client(credentials=credentials)
    client.bucket(bucket).blob(object_name).download_to_filename(local_path)
    return local_path


def resolve_project_id(project: str, *, credentials: Any) -> str:
    """Returns the project ID for a project ID or project number.

    Non-numeric input is assumed to already be a project ID and is returned
    unchanged. Numeric input is resolved through the Resource Manager API.

    Raises:
        RuntimeError: If the project number cannot be resolved.
    """
    project = project.strip()
    if not project.isdigit():
        return project
    from google.auth.transport.requests import (  # noqa: PLC0415
        AuthorizedSession,
    )

    url = f"{_RESOURCE_MANAGER_URL}/projects/{project}"
    try:
        session = AuthorizedSession(credentials)
        response = session.get(url, timeout=_RESOURCE_MANAGER_TIMEOUT_SECONDS)
        response.raise_for_status()
        project_id = response.json().get("projectId")
    except Exception as e:
        raise RuntimeError(
            f"Could not resolve project number {project} to a project ID"
            f" ({e}); pass --bucket explicitly."
        ) from e
    if not project_id:
        raise RuntimeError(
            f"Resource Manager returned no projectId for project {project};"
            " pass --bucket explicitly."
        )
    return str(project_id)
