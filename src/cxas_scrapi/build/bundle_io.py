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

"""Shared types, errors and strict I/O helpers for lockfiles and drift."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import yaml

if TYPE_CHECKING:
    from pathlib import Path


class BuildGateError(ValueError):
    """Raised on invalid bundles, lockfiles, or drift/push gate inputs."""


class DriftGateError(BuildGateError):
    """Raised when the pre-push gate refuses promotion on unresolved drift."""

    def __init__(
        self,
        message: str,
        *,
        drift_report: DriftReport | None = None,
    ) -> None:
        super().__init__(message)
        self.drift_report: DriftReport | None = drift_report


@dataclass(frozen=True)
class NormalizedResource:
    """Canonical volatile-stripped representation of a single CES resource."""

    resource_id: str
    sha256: str
    normalized: str
    accepted_from_live: bool = False

    @property
    def res_id(self) -> str:
        return self.resource_id


@dataclass
class ResourceDrift:
    """3-way comparison outcome for a single logical CES resource."""

    resource_id: str
    status: Literal["CLEAN", "DRIFT", "ORPHAN", "CONFLICT"]
    base_sha256: str | None = None
    ours_sha256: str | None = None
    live_sha256: str | None = None
    resolved_by: Literal["accept-live", "force-ours"] | None = None
    conflict_path: Path | None = None

    @property
    def res_id(self) -> str:
        return self.resource_id

    @property
    def diff_path(self) -> Path | None:
        return self.conflict_path

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_sha256": self.base_sha256,
            "conflict_path": (
                str(self.conflict_path) if self.conflict_path else None
            ),
            "live_sha256": self.live_sha256,
            "ours_sha256": self.ours_sha256,
            "resolved_by": self.resolved_by,
            "resource_id": self.resource_id,
            "status": self.status,
        }


@dataclass
class DriftReport:
    """Aggregate 3-way drift, conflict, and orphan report for a built app."""

    app_id: str
    resources: list[ResourceDrift] = field(default_factory=list)
    git_mirror_commit: str | None = None

    @property
    def is_clean(self) -> bool:
        return all(r.status == "CLEAN" for r in self.resources)

    @property
    def has_unresolved(self) -> bool:
        return any(
            r.status != "CLEAN" and r.resolved_by is None
            for r in self.resources
        )

    @property
    def by_id(self) -> dict[str, ResourceDrift]:
        return {r.resource_id: r for r in self.resources}

    def to_dict(self) -> dict[str, Any]:
        return {
            "app_id": self.app_id,
            "git_mirror_commit": self.git_mirror_commit,
            "has_unresolved": self.has_unresolved,
            "is_clean": self.is_clean,
            "resources": [r.to_dict() for r in self.resources],
        }


def assert_safe_subpath(path: Path, root: Path, *, label: str) -> None:
    """Reject symlinks and paths resolving outside ``root``."""
    if path.is_symlink():
        raise BuildGateError(f"Symlinks are not permitted in {label}: {path}")
    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise BuildGateError(
            f"Path escapes {label} root ({resolved_root}): {path}"
        )


def read_utf8_strict(path: Path, *, context: str = "file") -> str:
    """Reads a UTF-8 file, raising `BuildGateError` on I/O or decode errors."""
    if path.is_symlink():
        raise BuildGateError(f"Symlinks are not permitted in {context}: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise BuildGateError(f"Failed to read {context} {path}: {exc}") from exc


def load_json_dict_strict(
    path: Path, *, context: str = "JSON file"
) -> dict[str, Any]:
    """Reads a JSON object (dict) from disk or raises `BuildGateError`."""
    raw_text = read_utf8_strict(path, context=context)
    try:
        data = json.loads(raw_text)
    except ValueError as exc:
        raise BuildGateError(
            f"Corrupt JSON in {context} {path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise BuildGateError(
            f"Expected JSON object at root of {context} {path}, "
            f"got {type(data).__name__}."
        )
    return data


def load_mapping_strict(
    path: Path, *, context: str = "config file"
) -> dict[str, Any]:
    """Reads a JSON or YAML mapping dict from disk or raises BuildGateError."""
    if path.suffix.lower() in {".yaml", ".yml"}:
        raw_text = read_utf8_strict(path, context=context)
        try:
            data = yaml.safe_load(raw_text)
        except yaml.YAMLError as exc:
            raise BuildGateError(
                f"Corrupt YAML in {context} {path}: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise BuildGateError(
                f"Expected mapping object at root of {context} {path}, "
                f"got {type(data).__name__}."
            )
        return data
    return load_json_dict_strict(path, context=context)


def write_canonical_json(path: Path, payload: dict[str, Any]) -> None:
    """Writes sorted, indented, newline-terminated UTF-8 JSON."""
    if path.is_symlink():
        raise BuildGateError(f"Refusing to write through symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )


def write_canonical_mapping(path: Path, payload: dict[str, Any]) -> None:
    """Writes canonical JSON or YAML depending on ``path.suffix``."""
    if path.suffix.lower() in {".yaml", ".yml"}:
        if path.is_symlink():
            raise BuildGateError(f"Refusing to write through symlink: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(payload, sort_keys=True, allow_unicode=True),
            encoding="utf-8",
        )
        return
    write_canonical_json(path, payload)


__all__ = [
    "BuildGateError",
    "DriftGateError",
    "DriftReport",
    "NormalizedResource",
    "ResourceDrift",
    "assert_safe_subpath",
    "load_json_dict_strict",
    "load_mapping_strict",
    "read_utf8_strict",
    "write_canonical_json",
    "write_canonical_mapping",
]
