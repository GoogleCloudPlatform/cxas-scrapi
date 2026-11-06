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

"""Utilities for synchronizing shared callbacks and instruction fragments."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SHARED_DIRECTIVE_RE = re.compile(r"^\s*#\s*@cxas-shared:\s*(.+?)\s*$")
INCLUDE_START_RE = re.compile(r"<!--\s*@cxas-include:\s*([^\n]+?)\s*-->")
INCLUDE_END_RE = re.compile(r"<!--\s*@cxas-end-include\s*-->")
INCLUDE_BLOCK_RE = re.compile(
    r"(<!--\s*@cxas-include:\s*([^\n]+?)\s*-->)"
    r"(.*?)"
    r"(<!--\s*@cxas-end-include\s*-->)",
    re.DOTALL,
)

MANIFEST_CANDIDATES: tuple[str, ...] = (
    "shared/manifest.yaml",
    "shared/manifest.yml",
    "cxas_shared.yaml",
    "cxas_shared.yml",
)


@dataclass
class SyncItemResult:
    """Result of synchronizing or checking a single target file."""

    target: str
    sources: list[str]
    mechanism: str
    status: str
    message: str = ""

    @property
    def source(self) -> str:
        """Primary source path for single-source targets."""
        return self.sources[0] if self.sources else ""

    def to_dict(self) -> dict[str, Any]:
        """Serializes the item result to a dictionary."""
        return {
            "target": self.target,
            "sources": list(self.sources),
            "mechanism": self.mechanism,
            "status": self.status,
            "message": self.message,
        }


@dataclass
class SyncReport:
    """Summary report returned by :func:`sync_shared`."""

    app_dir: str
    check_only: bool
    items: list[SyncItemResult] = field(default_factory=list)

    @property
    def updated(self) -> list[str]:
        """Relative paths of targets that were written/updated."""
        return [item.target for item in self.items if item.status == "updated"]

    @property
    def drifted(self) -> list[str]:
        """Relative paths of targets that are out of sync or missing."""
        return [
            item.target
            for item in self.items
            if item.status in ("drifted", "missing_target")
        ]

    @property
    def in_sync(self) -> bool:
        """True if all discovered shared targets are synchronized."""
        bad_statuses = {
            "drifted",
            "missing_target",
            "missing_source",
            "invalid_path",
        }
        return not any(item.status in bad_statuses for item in self.items)

    def to_dict(self) -> dict[str, Any]:
        """Serializes the full report to a dictionary."""
        return {
            "app_dir": self.app_dir,
            "check_only": self.check_only,
            "in_sync": self.in_sync,
            "updated": self.updated,
            "drifted": self.drifted,
            "items": [item.to_dict() for item in self.items],
        }


def _resolve_safe_rel_path(app_root: Path, rel_path_str: str) -> Path:
    """Resolves ``rel_path_str`` inside ``app_root`` with traversal guards.

    Raises:
        ValueError: If ``rel_path_str`` is empty, absolute, contains ``..``
            traversal segments, or resolves outside ``app_root``.
    """
    cleaned = rel_path_str.strip()
    if not cleaned:
        raise ValueError("Shared relative path cannot be empty.")

    raw_path = Path(cleaned)
    if (
        os.path.isabs(cleaned)
        or cleaned.startswith(("/", "\\"))
        or raw_path.is_absolute()
        or ".." in raw_path.parts
    ):
        raise ValueError(
            f"Invalid shared path '{rel_path_str}': path must be relative to "
            "app_dir and must not contain '..' traversal segments."
        )

    root_resolved = app_root.resolve()
    candidate = (root_resolved / raw_path).resolve()
    if candidate == root_resolved or not candidate.is_relative_to(
        root_resolved
    ):
        raise ValueError(
            f"Invalid shared path '{rel_path_str}': resolves outside app_dir."
        )
    return candidate


def _normalize_rel_posix(rel_path_str: str) -> str:
    """Returns a normalized POSIX relative path string."""
    return Path(rel_path_str.strip()).as_posix()


def _extract_callback_directive(text: str) -> str | None:
    """Extracts ``<rel_path>`` from ``# @cxas-shared:`` in top 5 lines."""
    lines = text.splitlines()
    for line in lines[:5]:
        match = SHARED_DIRECTIVE_RE.match(line)
        if match:
            return match.group(1).strip()
    return None


def _build_materialized_callback_content(
    source_content: str, norm_source_rel: str
) -> str:
    """Prepends ``# @cxas-shared: <norm_source_rel>`` if not already present."""
    header = f"# @cxas-shared: {norm_source_rel}"
    lines = source_content.splitlines(keepends=True)
    for idx, line in enumerate(lines[:5]):
        if SHARED_DIRECTIVE_RE.match(line):
            lines[idx] = f"{header}\n"
            return "".join(lines)
    return f"{header}\n{source_content}"


def _load_manifest_entries(app_root: Path) -> list[tuple[str, list[str]]]:
    """Loads ``(source_rel, [target_rel, ...])`` pairs from manifest."""
    manifest_path: Path | None = None
    for candidate in MANIFEST_CANDIDATES:
        path = app_root / candidate
        if path.is_file():
            manifest_path = path
            break

    if manifest_path is None:
        return []

    try:
        raw_data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        rel_manifest = manifest_path.relative_to(app_root).as_posix()
        raise ValueError(f"Malformed YAML in '{rel_manifest}': {exc}") from exc

    if not raw_data:
        return []

    entries: list[tuple[str, list[str]]] = []
    raw_list: list[Any] | None = None

    if isinstance(raw_data, list):
        raw_list = raw_data
    elif isinstance(raw_data, dict):
        for key in ("mappings", "callbacks", "files", "shared", "items"):
            if isinstance(raw_data.get(key), list):
                raw_list = raw_data[key]
                break
        if raw_list is None:
            for src_key, targets_val in raw_data.items():
                if isinstance(src_key, str) and isinstance(targets_val, list):
                    entries.append(
                        (src_key, [str(t) for t in targets_val if t])
                    )
            return entries

    if raw_list is not None:
        for entry in raw_list:
            if not isinstance(entry, dict):
                continue
            source = entry.get("source")
            targets = entry.get("targets")
            if isinstance(source, str) and isinstance(targets, list):
                entries.append((source, [str(t) for t in targets if t]))

    return entries


def _sync_callback_target(
    app_root: Path,
    source_rel: str,
    target_path: Path,
    target_rel: str,
    mechanism: str,
    check: bool,
) -> SyncItemResult:
    """Synchronizes or checks a single callback file from ``source_rel``."""
    source_path = _resolve_safe_rel_path(app_root, source_rel)
    norm_source_rel = _normalize_rel_posix(source_rel)

    if not source_path.is_file():
        raise FileNotFoundError(
            f"Shared source file '{norm_source_rel}' referenced by "
            f"'{target_rel}' does not exist."
        )

    source_content = source_path.read_text(encoding="utf-8")
    expected_content = _build_materialized_callback_content(
        source_content, norm_source_rel
    )

    is_link = target_path.is_symlink()
    if not target_path.exists() and not is_link:
        if check:
            return SyncItemResult(
                target=target_rel,
                sources=[norm_source_rel],
                mechanism=mechanism,
                status="missing_target",
                message=f"Target '{target_rel}' does not exist.",
            )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(expected_content, encoding="utf-8")
        return SyncItemResult(
            target=target_rel,
            sources=[norm_source_rel],
            mechanism=mechanism,
            status="updated",
            message=f"Created '{target_rel}' from '{norm_source_rel}'.",
        )

    current_content = (
        target_path.read_text(encoding="utf-8") if target_path.exists() else ""
    )
    if current_content == expected_content and not is_link:
        return SyncItemResult(
            target=target_rel,
            sources=[norm_source_rel],
            mechanism=mechanism,
            status="in_sync",
            message=f"'{target_rel}' is in sync with '{norm_source_rel}'.",
        )

    if check:
        return SyncItemResult(
            target=target_rel,
            sources=[norm_source_rel],
            mechanism=mechanism,
            status="drifted",
            message=(
                f"'{target_rel}' has drifted from canonical source "
                f"'{norm_source_rel}'."
            ),
        )

    if is_link:
        target_path.unlink()
    target_path.write_text(expected_content, encoding="utf-8")
    return SyncItemResult(
        target=target_rel,
        sources=[norm_source_rel],
        mechanism=mechanism,
        status="updated",
        message=f"Synchronized '{target_rel}' from '{norm_source_rel}'.",
    )


def _sync_instruction_file(
    app_root: Path, instruction_path: Path, check: bool
) -> SyncItemResult | None:
    """Synchronizes ``<!-- @cxas-include: ... -->`` blocks in a file."""
    target_rel = instruction_path.relative_to(app_root).as_posix()
    _resolve_safe_rel_path(app_root, target_rel)

    content = instruction_path.read_text(encoding="utf-8")
    start_matches = list(INCLUDE_START_RE.finditer(content))
    end_matches = list(INCLUDE_END_RE.finditer(content))
    if not start_matches and not end_matches:
        return None

    block_matches = list(INCLUDE_BLOCK_RE.finditer(content))

    if len(start_matches) != len(end_matches) or len(block_matches) != len(
        start_matches
    ):
        raise ValueError(
            f"Unclosed or orphan <!-- @cxas-include: ... --> / "
            f"<!-- @cxas-end-include --> marker in '{target_rel}': "
            "every include directive must have a matching "
            "<!-- @cxas-end-include --> marker."
        )

    for bmatch in block_matches:
        inner = bmatch.group(3)
        if INCLUDE_START_RE.search(inner) or INCLUDE_END_RE.search(inner):
            raise ValueError(
                f"Unclosed or nested <!-- @cxas-include: ... --> / "
                f"<!-- @cxas-end-include --> marker in '{target_rel}'."
            )

    sources_used: list[str] = []

    def _replace_block(match: re.Match[str]) -> str:
        raw_rel = match.group(2).strip()
        source_path = _resolve_safe_rel_path(app_root, raw_rel)
        norm_rel = _normalize_rel_posix(raw_rel)
        if not source_path.is_file():
            raise FileNotFoundError(
                f"Shared instruction fragment '{norm_rel}' referenced by "
                f"'{target_rel}' does not exist."
            )
        if norm_rel not in sources_used:
            sources_used.append(norm_rel)

        start_marker = f"<!-- @cxas-include: {norm_rel} -->"
        end_marker = "<!-- @cxas-end-include -->"
        fragment_body = source_path.read_text(encoding="utf-8").strip("\r\n")
        if fragment_body:
            return f"{start_marker}\n{fragment_body}\n{end_marker}"
        return f"{start_marker}\n{end_marker}"

    new_content = INCLUDE_BLOCK_RE.sub(_replace_block, content)
    if new_content == content and not instruction_path.is_symlink():
        return SyncItemResult(
            target=target_rel,
            sources=sources_used,
            mechanism="instruction_include",
            status="in_sync",
            message=f"'{target_rel}' include blocks are in sync.",
        )

    if check:
        return SyncItemResult(
            target=target_rel,
            sources=sources_used,
            mechanism="instruction_include",
            status="drifted",
            message=(
                f"'{target_rel}' include blocks have drifted from "
                f"{', '.join(sources_used)}."
            ),
        )

    if instruction_path.is_symlink():
        instruction_path.unlink()
    instruction_path.write_text(new_content, encoding="utf-8")
    return SyncItemResult(
        target=target_rel,
        sources=sources_used,
        mechanism="instruction_include",
        status="updated",
        message=(
            f"Synchronized '{target_rel}' include blocks from "
            f"{', '.join(sources_used)}."
        ),
    )


def sync_shared(app_dir: str | Path, check: bool = False) -> SyncReport:
    """Materializes or checks shared callbacks and instruction fragments.

    Supports three deterministic mechanisms:
    1. Manifest mappings in ``shared/manifest.yaml`` or ``cxas_shared.yaml``.
    2. Inline ``# @cxas-shared: <rel_path>`` headers in ``agents/**/*.py``.
    3. ``<!-- @cxas-include: <rel_path> --> ... <!-- @cxas-end-include -->``
       markers in ``instruction.txt`` and ``global_instruction.txt``.

    Args:
        app_dir: Root directory of the CXAS app.
        check: If True, do not modify files on disk; only report drift.

    Returns:
        A :class:`SyncReport` detailing all synchronized or drifted targets.

    Raises:
        ValueError: If any shared path attempts to escape ``app_dir`` or an
            include block is malformed.
        FileNotFoundError: If a referenced canonical shared source file is
            missing.
    """
    app_root = Path(app_dir).resolve()
    report = SyncReport(app_dir=str(app_root), check_only=check)
    if not app_root.is_dir():
        return report

    processed_callback_targets: set[str] = set()
    manifest_target_sources: dict[str, str] = {}

    # 1. Process manifest entries (shared/manifest.yaml or cxas_shared.yaml)
    manifest_entries = _load_manifest_entries(app_root)
    for source_rel, target_rels in manifest_entries:
        norm_source_rel = _normalize_rel_posix(source_rel)
        for raw_target_rel in target_rels:
            _resolve_safe_rel_path(app_root, raw_target_rel)
            norm_target_rel = _normalize_rel_posix(raw_target_rel)
            if norm_target_rel in manifest_target_sources:
                prev_source = manifest_target_sources[norm_target_rel]
                if prev_source != norm_source_rel:
                    raise ValueError(
                        "Conflicting manifest mappings for target "
                        f"'{norm_target_rel}': '{prev_source}' vs "
                        f"'{norm_source_rel}'."
                    )
                continue
            manifest_target_sources[norm_target_rel] = norm_source_rel
            lexical_target_path = app_root / norm_target_rel
            item_result = _sync_callback_target(
                app_root=app_root,
                source_rel=source_rel,
                target_path=lexical_target_path,
                target_rel=norm_target_rel,
                mechanism="manifest",
                check=check,
            )
            report.items.append(item_result)
            processed_callback_targets.add(norm_target_rel)

    # 2. Scan agents/**/*.py for inline '# @cxas-shared: <rel_path>' headers
    agents_dir = app_root / "agents"
    if agents_dir.is_dir():
        for py_path in sorted(agents_dir.rglob("*.py")):
            if not py_path.is_file() and not py_path.is_symlink():
                continue
            norm_target_rel = py_path.relative_to(app_root).as_posix()
            _resolve_safe_rel_path(app_root, norm_target_rel)
            if norm_target_rel in processed_callback_targets:
                continue
            content = py_path.read_text(encoding="utf-8")
            directive_rel = _extract_callback_directive(content)
            if directive_rel is None:
                continue
            item_result = _sync_callback_target(
                app_root=app_root,
                source_rel=directive_rel,
                target_path=py_path,
                target_rel=norm_target_rel,
                mechanism="callback_directive",
                check=check,
            )
            report.items.append(item_result)
            processed_callback_targets.add(norm_target_rel)

    # 3. Scan global_instruction.txt and agents/**/instruction.txt for includes
    instruction_files: list[Path] = []
    global_instr = app_root / "global_instruction.txt"
    if global_instr.is_file() or global_instr.is_symlink():
        instruction_files.append(global_instr)
    if agents_dir.is_dir():
        instruction_files.extend(sorted(agents_dir.rglob("instruction.txt")))

    for instr_path in instruction_files:
        if not instr_path.is_file() and not instr_path.is_symlink():
            continue
        item_result = _sync_instruction_file(app_root, instr_path, check=check)
        if item_result is not None:
            report.items.append(item_result)

    return report
