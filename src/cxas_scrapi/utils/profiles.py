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

"""Shared runtime profile resolution and config overlay engine.

Profiles let developers and CI pipelines target different CXAS environments
(personal sandboxes, dev, qa, prod) without mutating tracked files in Git.

Discovery order:
1. Explicit file path (ends in .yml/.yaml/.json, contains a path separator,
   or exists on disk).
2. ``<project_or_repo_root>/.cxas/profiles/<name>.{yml,yaml,json}``
   (respecting ``GECX_PROJECT`` and ``.active-project`` so sibling projects
   never leak into each other).
3. ``CXAS_PROFILE`` environment variable when ``--profile`` is omitted.
"""

from __future__ import annotations

import copy
import difflib
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

ENV_PROFILE_VAR = "CXAS_PROFILE"
ENV_PROJECT_VAR = "GECX_PROJECT"
PROFILES_REL_DIR = Path(".cxas") / "profiles"
PROFILE_EXTENSIONS = (".yml", ".yaml", ".json")

ALLOWED_TOP_LEVEL_KEYS = frozenset(
    {
        "extends",
        "protected",
        "target",
        "app_dir",
        "env_file",
        "overrides",
        "description",
    }
)
ALLOWED_TARGET_KEYS = frozenset(
    {
        "project_id",
        "location",
        "app_id",
        "display_name",
        "deployment_id",
    }
)
ALLOWED_OVERRIDE_FILES = frozenset(
    {
        "app.json",
        "app.yaml",
        "gecx-config.json",
    }
)
GECX_TARGET_KEYS = frozenset(
    {
        "gcp_project_id",
        "location",
        "deployed_app_id",
    }
)
REQUIRED_APP_CONFIG_KEYS = frozenset(
    {
        "displayName",
        "rootAgent",
    }
)
SECRET_KEY_SUBSTRINGS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "passwd",
    "private_key",
    "credential",
)
# Keys that are safe despite containing a secret-like substring.
_SAFE_KEY_ALLOWLIST = frozenset(
    {
        "max_tokens",
        "max_output_tokens",
        "maxOutputTokens",
        "token_limit",
        "tokenLimit",
    }
)


class ProfileError(ValueError):
    """Base error raised when loading or validating a CXAS profile."""


class ProfileNotFoundError(ProfileError):
    """Raised when a requested profile name or file cannot be located."""


class ProfileCycleError(ProfileError):
    """Raised when profile ``extends`` inheritance contains a cycle."""


class ProtectedProfileError(ProfileError):
    """Raised when a local mutation targets a ``protected: true`` profile."""


@dataclass
class ProfileTarget:
    """Canonical CES target fields declared in a profile."""

    project_id: str | None = None
    location: str | None = None
    app_id: str | None = None
    display_name: str | None = None
    deployment_id: str | None = None

    @property
    def app_resource_name(self) -> str | None:
        """Return ``projects/.../locations/.../apps/...`` when resolvable."""
        if self.app_id and self.app_id.startswith("projects/"):
            return self.app_id
        if self.project_id and self.location and self.app_id:
            return (
                f"projects/{self.project_id}"
                f"/locations/{self.location}"
                f"/apps/{self.app_id}"
            )
        return None

    @property
    def target_label(self) -> str:
        """Human-readable ``<project>/<location>/<app>`` label."""
        proj = self.project_id or "<unset-project>"
        loc = self.location or "<unset-location>"
        app = self.app_id or self.display_name or "<new-app>"
        return f"{proj}/{loc}/{app}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize non-None target fields to a plain dict."""
        data: dict[str, Any] = {}
        for key in (
            "project_id",
            "location",
            "app_id",
            "display_name",
            "deployment_id",
        ):
            val = getattr(self, key)
            if val is not None:
                data[key] = val
        return data


@dataclass
class ResolvedProfile:
    """Fully resolved, inheritance-merged profile."""

    name: str
    source_path: Path
    project_root: Path
    source_kind: str = "cli"  # "cli" or "env"
    chain: list[Path] = field(default_factory=list)
    protected: bool = False
    target: ProfileTarget = field(default_factory=ProfileTarget)
    app_dir: Path | None = None
    env_file: Path | None = None
    overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    description: str | None = None

    @property
    def app_overrides(self) -> dict[str, Any]:
        """Return merged app config overrides (``app.json`` / ``app.yaml``)."""
        merged: dict[str, Any] = {}
        for filename in ("app.json", "app.yaml"):
            if filename in self.overrides:
                merged = deep_merge(
                    merged,
                    self.overrides[filename],
                    warn_on_list_replace=False,
                )
        return merged

    @property
    def gecx_config_overrides(self) -> dict[str, Any]:
        """Return ``overrides['gecx-config.json']`` dict (empty if absent)."""
        return copy.deepcopy(self.overrides.get("gecx-config.json", {}))

    def resolve_gecx_config(
        self,
        base_config: dict[str, Any] | None = None,
        *,
        warnings_out: list[str] | None = None,
    ) -> dict[str, Any]:
        """Merge ``gecx-config.json`` overrides and project ``target``."""
        merged = deep_merge(
            base_config or {},
            self.gecx_config_overrides,
            warnings_out=warnings_out,
        )
        if self.target.project_id is not None:
            merged["gcp_project_id"] = self.target.project_id
        if self.target.location is not None:
            merged["location"] = self.target.location
        if self.target.app_id is not None:
            merged["deployed_app_id"] = self.target.app_id
        if self.target.deployment_id is not None:
            merged["deployment_id"] = self.target.deployment_id
        if self.app_dir is not None:
            merged["app_dir"] = str(self.app_dir)
        return merged

    def format_banner(self) -> str:
        """Return mandatory 1-line banner printed before mutating CES."""
        return f"Profile: {self.name} -> {self.target.target_label}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize resolved profile for ``cxas profile show --json``."""
        return {
            "name": self.name,
            "source_path": str(self.source_path),
            "source_kind": self.source_kind,
            "chain": [str(p) for p in self.chain],
            "project_root": str(self.project_root),
            "protected": self.protected,
            "description": self.description,
            "target": self.target.to_dict(),
            "app_dir": str(self.app_dir) if self.app_dir else None,
            "env_file": str(self.env_file) if self.env_file else None,
            "overrides": copy.deepcopy(self.overrides),
            "warnings": list(self.warnings),
        }


@dataclass
class ResolvedRuntimeConfig:
    """Unified runtime configuration shared across CLI commands & scripts."""

    project_root: Path
    profile: ResolvedProfile | None = None
    project_id: str | None = None
    location: str | None = None
    app_id: str | None = None
    app_name: str | None = None
    display_name: str | None = None
    deployment_id: str | None = None
    app_dir: str | None = None
    env_file: str | None = None
    modality: str | None = None
    model: str | None = None
    gecx_config: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ProfileSummary:
    """Lightweight metadata returned by ``list_profiles``."""

    name: str
    path: Path
    protected: bool = False
    target_label: str = ""
    extends: str | None = None
    error: str | None = None


def is_ci_environment() -> bool:
    """Return True when running inside a CI environment (``CI=true``/``1``)."""
    return os.environ.get("CI", "").strip().lower() in ("true", "1", "yes")


def _find_workspace_root(start_dir: Path) -> Path:
    """Walk up from ``start_dir`` looking for repo/workspace root markers."""
    current = start_dir.resolve()
    markers = (
        ".git",
        ".cxas",
        ".agents",
        ".claude",
        ".gemini",
        ".active-project",
    )
    for candidate in (current, *current.parents):
        if any((candidate / m).exists() for m in markers):
            return candidate
    return current


def resolve_project_root(
    start_dir: str | Path | None = None,
) -> tuple[Path, Path]:
    """Return ``(project_root, workspace_root)`` respecting active project.

    Active project precedence:
    1. ``GECX_PROJECT`` environment variable (``<workspace>/<GECX_PROJECT>``).
    2. ``<workspace>/.active-project`` pointer file.
    3. Nearest ancestor containing ``gecx-config.json`` up to ``workspace``.
    4. ``workspace_root`` itself.
    """
    base = Path(start_dir).resolve() if start_dir else Path.cwd().resolve()
    workspace = _find_workspace_root(base)

    # 1. GECX_PROJECT env var
    env_project = os.environ.get(ENV_PROJECT_VAR, "").strip()
    if env_project:
        candidate = (workspace / env_project).resolve()
        if candidate.is_dir():
            return candidate, workspace
        raise ProfileError(
            f"{ENV_PROJECT_VAR}='{env_project}' points to non-existent "
            f"directory: {candidate}"
        )

    # 2. .active-project pointer file at workspace root
    pointer = workspace / ".active-project"
    if pointer.is_file():
        active_name = pointer.read_text(encoding="utf-8").strip()
        if active_name:
            candidate = (workspace / active_name).resolve()
            if candidate.is_dir():
                return candidate, workspace

    # 3. Nearest ancestor with gecx-config.json (bounded by workspace)
    for candidate in (base, *base.parents):
        if (candidate / "gecx-config.json").is_file():
            return candidate, workspace
        if candidate == workspace:
            break

    return workspace, workspace


def _expand_path(raw: str, base_dir: Path) -> Path:
    """Expand ``~`` / ``${ENV_VAR}`` and resolve relative to ``base_dir``."""
    expanded = os.path.expanduser(os.path.expandvars(raw))
    path = Path(expanded)
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    else:
        path = path.resolve()
    return path


def _looks_like_explicit_path(profile_ref: str) -> bool:
    """Return True if ``profile_ref`` looks like a file path or filename."""
    return (
        profile_ref.endswith(PROFILE_EXTENSIONS)
        or "/" in profile_ref
        or "\\" in profile_ref
        or profile_ref.startswith((".", "~"))
    )


def _profile_search_dirs(
    project_root: Path, workspace_root: Path
) -> list[Path]:
    """Return ordered list of ``.cxas/profiles`` directories to search.

    Searches the active project's ``.cxas/profiles`` first, then the shared
    repo/workspace root ``.cxas/profiles`` (if distinct). Sibling project
    folders are never searched.
    """
    dirs: list[Path] = [project_root / PROFILES_REL_DIR]
    if workspace_root.resolve() != project_root.resolve():
        dirs.append(workspace_root / PROFILES_REL_DIR)
    return dirs


def find_profile_file(
    profile_ref: str,
    *,
    project_root: Path,
    workspace_root: Path,
    relative_to: Path | None = None,
) -> Path:
    """Locate a profile file by explicit path or logical name."""
    ref = profile_ref.strip()
    if not ref:
        raise ProfileNotFoundError("Profile name cannot be empty.")

    expanded = os.path.expanduser(os.path.expandvars(ref))
    direct = Path(expanded)

    # 1. Explicit file path or existing file on disk
    if direct.is_file():
        return direct.resolve()
    if relative_to is not None:
        rel_candidate = (relative_to / expanded).resolve()
        if rel_candidate.is_file():
            return rel_candidate
    proj_candidate = (project_root / expanded).resolve()
    if proj_candidate.is_file():
        return proj_candidate

    search_dirs = _profile_search_dirs(project_root, workspace_root)

    # If user passed a filename like "dev.yml", also check .cxas/profiles/
    if _looks_like_explicit_path(ref):
        for pdir in search_dirs:
            candidate = pdir / ref
            if candidate.is_file():
                return candidate.resolve()
        raise ProfileNotFoundError(f"Profile file not found: '{profile_ref}'.")

    # 2. Named profile inside <root>/.cxas/profiles/<name>.{yml,yaml,json}
    for pdir in search_dirs:
        for ext in PROFILE_EXTENSIONS:
            candidate = pdir / f"{ref}{ext}"
            if candidate.is_file():
                return candidate.resolve()

    searched = ", ".join(str(d) for d in search_dirs)
    raise ProfileNotFoundError(
        f"Profile '{profile_ref}' not found. Searched: {searched}"
    )


def _load_structured_file(path: Path) -> Any:
    """Parse a YAML or JSON file."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ProfileError(f"Invalid JSON in '{path}': {e}") from e
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ProfileError(f"Invalid YAML in '{path}': {e}") from e
    return {} if data is None else data


def _write_structured_file(path: Path, data: dict[str, Any]) -> None:
    """Write a dict back to JSON or YAML matching ``path.suffix``."""
    if path.suffix.lower() in (".yaml", ".yml"):
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    else:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _scan_secret_like_keys(
    data: Any,
    *,
    prefix: str,
    warnings_out: list[str],
) -> None:
    """Recursively warn on secret-like keys inside profile overrides."""
    if isinstance(data, dict):
        for key, value in data.items():
            key_str = str(key)
            key_path = f"{prefix}.{key_str}" if prefix else key_str
            lower = key_str.lower()
            if (
                key_str not in _SAFE_KEY_ALLOWLIST
                and lower not in _SAFE_KEY_ALLOWLIST
                and any(tok in lower for tok in SECRET_KEY_SUBSTRINGS)
                and value is not None
            ):
                msg = (
                    f"Potential secret key '{key_path}' found in profile "
                    "overrides. Prefer 'env_file' or Secret Manager instead "
                    "of committing secrets to profile files."
                )
                warnings_out.append(msg)
                logger.warning(msg)
            _scan_secret_like_keys(
                value, prefix=key_path, warnings_out=warnings_out
            )
    elif isinstance(data, list):
        for idx, item in enumerate(data):
            _scan_secret_like_keys(
                item, prefix=f"{prefix}[{idx}]", warnings_out=warnings_out
            )


def deep_merge(
    base: dict[str, Any],
    overlay: dict[str, Any],
    *,
    warn_on_list_replace: bool = True,
    warnings_out: list[str] | None = None,
    _path: str = "",
) -> dict[str, Any]:
    """Recursively merge ``overlay`` onto a deep copy of ``base``.

    Semantics:
    - ``dict`` + ``dict``: merge recursively.
    - ``None`` (YAML ``null``): delete key from the merged dict.
    - ``list``: replace entirely (emits a warning when replacing an existing
      list so users know lists are replaced rather than appended).
    - Scalars / mismatched types: replace.
    """
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        key_path = f"{_path}.{key}" if _path else str(key)
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(
                result[key],
                value,
                warn_on_list_replace=warn_on_list_replace,
                warnings_out=warnings_out,
                _path=key_path,
            )
        elif isinstance(value, dict):
            # Strip null tombstones when creating a fresh nested dict.
            result[key] = deep_merge(
                {},
                value,
                warn_on_list_replace=warn_on_list_replace,
                warnings_out=warnings_out,
                _path=key_path,
            )
        elif isinstance(value, list):
            if (
                warn_on_list_replace
                and key in result
                and isinstance(result[key], list)
            ):
                msg = (
                    f"Profile override replaces list at '{key_path}' "
                    "(lists are replaced wholesale, not appended)."
                )
                logger.warning(msg)
                if warnings_out is not None:
                    warnings_out.append(msg)
            result[key] = copy.deepcopy(value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _validate_raw_profile(
    raw: Any, path: Path, warnings_out: list[str]
) -> dict[str, Any]:
    """Validate structure, whitelists, and secret hygiene of a raw profile."""
    if not isinstance(raw, dict):
        raise ProfileError(
            f"Profile '{path}' must be a YAML/JSON mapping, got "
            f"{type(raw).__name__}."
        )

    unknown_top = sorted(set(raw.keys()) - ALLOWED_TOP_LEVEL_KEYS)
    if unknown_top:
        raise ProfileError(
            f"Profile '{path}' contains unsupported top-level key(s): "
            f"{', '.join(unknown_top)}. Allowed keys: "
            f"{', '.join(sorted(ALLOWED_TOP_LEVEL_KEYS))}."
        )

    if (
        "extends" in raw
        and raw["extends"] is not None
        and (not isinstance(raw["extends"], str) or not raw["extends"].strip())
    ):
        raise ProfileError(
            f"Profile '{path}': 'extends' must be a non-empty string."
        )

    if "protected" in raw and not isinstance(raw["protected"], bool):
        raise ProfileError(
            f"Profile '{path}': 'protected' must be a boolean (true/false)."
        )

    if "target" in raw and raw["target"] is not None:
        if not isinstance(raw["target"], dict):
            raise ProfileError(f"Profile '{path}': 'target' must be a mapping.")
        unknown_target = sorted(set(raw["target"].keys()) - ALLOWED_TARGET_KEYS)
        if unknown_target:
            raise ProfileError(
                f"Profile '{path}': unsupported key(s) in 'target': "
                f"{', '.join(unknown_target)}. Allowed: "
                f"{', '.join(sorted(ALLOWED_TARGET_KEYS))}."
            )
        for tk, tv in raw["target"].items():
            if tv is not None and not isinstance(tv, str):
                raise ProfileError(
                    f"Profile '{path}': 'target.{tk}' must be a string or null."
                )

    for path_field in ("app_dir", "env_file"):
        if (
            path_field in raw
            and raw[path_field] is not None
            and (
                not isinstance(raw[path_field], str)
                or not raw[path_field].strip()
            )
        ):
            raise ProfileError(
                f"Profile '{path}': '{path_field}' must be a non-empty "
                "string or null."
            )

    if "overrides" in raw and raw["overrides"] is not None:
        if not isinstance(raw["overrides"], dict):
            raise ProfileError(
                f"Profile '{path}': 'overrides' must be a mapping."
            )
        for filename, file_overlay in raw["overrides"].items():
            if filename not in ALLOWED_OVERRIDE_FILES:
                raise ProfileError(
                    f"Profile '{path}': override file '{filename}' is not "
                    f"allowed. Permitted files: "
                    f"{', '.join(sorted(ALLOWED_OVERRIDE_FILES))}."
                )
            if file_overlay is not None and not isinstance(file_overlay, dict):
                raise ProfileError(
                    f"Profile '{path}': overrides['{filename}'] must be a "
                    "mapping."
                )
            if filename == "gecx-config.json" and isinstance(
                file_overlay, dict
            ):
                dup_keys = sorted(set(file_overlay.keys()) & GECX_TARGET_KEYS)
                if dup_keys:
                    raise ProfileError(
                        f"Profile '{path}': do not set target key(s) "
                        f"{', '.join(dup_keys)} inside "
                        "overrides['gecx-config.json']; declare them under "
                        "'target:' (project_id, location, app_id) instead."
                    )
            if isinstance(file_overlay, dict):
                _scan_secret_like_keys(
                    file_overlay,
                    prefix=f"overrides.{filename}",
                    warnings_out=warnings_out,
                )

    return raw


def _profile_name_from_path(path: Path) -> str:
    """Derive a clean profile name from a file path."""
    name = path.name
    for ext in PROFILE_EXTENSIONS:
        if name.endswith(ext):
            return name[: -len(ext)]
    return path.stem


def _load_profile_chain(
    profile_ref: str,
    *,
    project_root: Path,
    workspace_root: Path,
    seen: list[Path],
    relative_to: Path | None = None,
) -> tuple[dict[str, Any], list[Path], list[str]]:
    """Recursively load a profile and its ``extends`` parent (cycle-checked)."""
    path = find_profile_file(
        profile_ref,
        project_root=project_root,
        workspace_root=workspace_root,
        relative_to=relative_to,
    )
    if path in seen:
        cycle_paths = [*seen[seen.index(path) :], path]
        cycle_names = [_profile_name_from_path(p) for p in cycle_paths]
        raise ProfileCycleError(
            f"Profile inheritance cycle detected: {' -> '.join(cycle_names)}"
        )

    warnings_out: list[str] = []
    raw = _validate_raw_profile(_load_structured_file(path), path, warnings_out)

    parent_ref = raw.get("extends")
    if not parent_ref:
        return raw, [path], warnings_out

    parent_merged, parent_chain, parent_warnings = _load_profile_chain(
        parent_ref,
        project_root=project_root,
        workspace_root=workspace_root,
        seen=[*seen, path],
        relative_to=path.parent,
    )

    merged: dict[str, Any] = copy.deepcopy(parent_merged)
    merged.pop("extends", None)

    if "protected" in raw:
        merged["protected"] = raw["protected"]
    if "description" in raw:
        merged["description"] = raw["description"]
    if "app_dir" in raw:
        merged["app_dir"] = raw["app_dir"]
    if "env_file" in raw:
        merged["env_file"] = raw["env_file"]

    if "target" in raw and raw["target"] is not None:
        merged["target"] = deep_merge(
            merged.get("target") or {},
            raw["target"],
            warn_on_list_replace=False,
        )

    if "overrides" in raw and raw["overrides"] is not None:
        merged_overrides: dict[str, dict[str, Any]] = copy.deepcopy(
            merged.get("overrides") or {}
        )
        for fname, foverlay in raw["overrides"].items():
            if foverlay is None:
                merged_overrides.pop(fname, None)
            else:
                merged_overrides[fname] = deep_merge(
                    merged_overrides.get(fname, {}),
                    foverlay,
                    warnings_out=warnings_out,
                    _path=f"overrides.{fname}",
                )
        merged["overrides"] = merged_overrides

    return merged, [*parent_chain, path], [*parent_warnings, *warnings_out]


def load_profile(
    profile_ref: str | None = None,
    *,
    start_dir: str | Path | None = None,
) -> ResolvedProfile | None:
    """Resolve and load a profile from CLI arg or ``CXAS_PROFILE`` env var.

    Returns ``None`` when neither ``profile_ref`` nor ``CXAS_PROFILE`` is set.
    """
    source_kind = "cli"
    effective_ref = profile_ref
    if not effective_ref:
        env_ref = os.environ.get(ENV_PROFILE_VAR, "").strip()
        if not env_ref:
            return None
        effective_ref = env_ref
        source_kind = "env"

    project_root, workspace_root = resolve_project_root(start_dir)
    merged, chain, warnings_out = _load_profile_chain(
        effective_ref,
        project_root=project_root,
        workspace_root=workspace_root,
        seen=[],
    )

    leaf_path = chain[-1]
    target_raw = merged.get("target") or {}
    target = ProfileTarget(
        project_id=target_raw.get("project_id"),
        location=target_raw.get("location"),
        app_id=target_raw.get("app_id"),
        display_name=target_raw.get("display_name"),
        deployment_id=target_raw.get("deployment_id"),
    )

    app_dir_raw = merged.get("app_dir")
    app_dir = (
        _expand_path(app_dir_raw, project_root)
        if isinstance(app_dir_raw, str) and app_dir_raw.strip()
        else None
    )

    env_file_raw = merged.get("env_file")
    env_file = (
        _expand_path(env_file_raw, project_root)
        if isinstance(env_file_raw, str) and env_file_raw.strip()
        else None
    )

    overrides: dict[str, dict[str, Any]] = {}
    for fname, fdata in (merged.get("overrides") or {}).items():
        if isinstance(fdata, dict):
            overrides[fname] = fdata

    return ResolvedProfile(
        name=_profile_name_from_path(leaf_path),
        source_path=leaf_path,
        project_root=project_root,
        source_kind=source_kind,
        chain=chain,
        protected=bool(merged.get("protected", False)),
        target=target,
        app_dir=app_dir,
        env_file=env_file,
        overrides=overrides,
        warnings=warnings_out,
        description=merged.get("description"),
    )


def list_profiles(start_dir: str | Path | None = None) -> list[ProfileSummary]:
    """List all profiles discovered for the active project / repo."""
    project_root, workspace_root = resolve_project_root(start_dir)
    seen_names: dict[str, Path] = {}
    for pdir in _profile_search_dirs(project_root, workspace_root):
        if not pdir.is_dir():
            continue
        for entry in sorted(pdir.iterdir()):
            if not entry.is_file():
                continue
            if not entry.name.endswith(PROFILE_EXTENSIONS):
                continue
            name = _profile_name_from_path(entry)
            if name not in seen_names:
                seen_names[name] = entry.resolve()

    summaries: list[ProfileSummary] = []
    for name in sorted(seen_names):
        path = seen_names[name]
        try:
            raw = _load_structured_file(path)
            extends = raw.get("extends") if isinstance(raw, dict) else None
            resolved = load_profile(str(path), start_dir=project_root)
            assert resolved is not None
            summaries.append(
                ProfileSummary(
                    name=name,
                    path=path,
                    protected=resolved.protected,
                    target_label=resolved.target.target_label,
                    extends=extends if isinstance(extends, str) else None,
                )
            )
        except ProfileError as e:
            summaries.append(ProfileSummary(name=name, path=path, error=str(e)))
    return summaries


def validate_profile(
    profile_ref: str,
    *,
    start_dir: str | Path | None = None,
) -> tuple[ResolvedProfile | None, list[str], list[str]]:
    """Validate a profile and return ``(resolved, errors, warnings)``."""
    errors: list[str] = []
    warnings_out: list[str] = []
    try:
        profile = load_profile(profile_ref, start_dir=start_dir)
    except ProfileError as e:
        return None, [str(e)], []

    if profile is None:
        return None, ["No profile specified."], []

    warnings_out.extend(profile.warnings)

    if profile.env_file is not None and not profile.env_file.is_file():
        warnings_out.append(
            f"Configured env_file does not exist on disk: {profile.env_file}"
        )
    if profile.app_dir is not None and not profile.app_dir.is_dir():
        warnings_out.append(
            f"Configured app_dir does not exist on disk: {profile.app_dir}"
        )

    # Validate app overrides against local app.json / app.yaml if present.
    app_dir = profile.app_dir or profile.project_root
    app_cfg = _find_app_config_file(app_dir)
    if app_cfg is not None and profile.app_overrides:
        try:
            base_data = _load_structured_file(app_cfg)
            if isinstance(base_data, dict):
                merge_app_config_data(
                    base_data,
                    profile.app_overrides,
                    warnings_out=warnings_out,
                )
        except ProfileError as e:
            errors.append(str(e))

    return profile, errors, warnings_out


def guard_protected_mutation(
    profile: ResolvedProfile | None,
    *,
    command: str,
    allow_protected: bool = False,
) -> None:
    """Print profile banner and block local mutations on ``protected: true``."""
    if profile is None:
        return

    print(profile.format_banner())
    if profile.protected and not allow_protected and not is_ci_environment():
        raise ProtectedProfileError(
            f"Refusing to run '{command}' against protected profile "
            f"'{profile.name}' ({profile.target.target_label}). "
            "Pass --allow-protected or run in CI (CI=true) to proceed."
        )


def guard_env_profile_target_conflict(
    profile: ResolvedProfile | None,
    *,
    explicit_to: str | None,
) -> None:
    """Fail fast if ``--to`` is passed with a ``CXAS_PROFILE`` env profile."""
    if profile is not None and profile.source_kind == "env" and explicit_to:
        raise ProfileError(
            f"Refusing to run with explicit --to '{explicit_to}' while "
            f"profile '{profile.name}' is active via {ENV_PROFILE_VAR}. "
            "Pass --profile explicitly or unset "
            f"{ENV_PROFILE_VAR} to avoid silently injecting profile overrides "
            "and env_file onto another app."
        )


def _find_app_config_file(app_dir: str | Path) -> Path | None:
    """Return ``app.json`` or ``app.yaml`` inside ``app_dir``, or None."""
    root = Path(app_dir)
    for name in ("app.json", "app.yaml"):
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def _validate_post_merge_app_config(
    base_inner: dict[str, Any],
    merged_inner: dict[str, Any],
    overlay_inner: dict[str, Any],
) -> None:
    """Ensure ``null`` overrides did not delete required app config keys."""
    for req_key in sorted(REQUIRED_APP_CONFIG_KEYS):
        if (
            req_key in overlay_inner
            and overlay_inner[req_key] is None
            and (req_key in base_inner or req_key not in merged_inner)
        ):
            raise ProfileError(
                f"Profile override cannot delete required app config key "
                f"'{req_key}' with null."
            )


def merge_app_config_data(
    base_data: dict[str, Any],
    app_overrides: dict[str, Any],
    *,
    warnings_out: list[str] | None = None,
) -> dict[str, Any]:
    """Pure in-memory merge of ``app_overrides`` onto ``base_data``.

    Transparently handles optional top-level ``"app"`` wrappers in either
    ``base_data`` or ``app_overrides``.
    """
    if not app_overrides:
        return copy.deepcopy(base_data)

    base_has_wrapper = isinstance(base_data.get("app"), dict)
    overlay_has_wrapper = isinstance(app_overrides.get("app"), dict)

    if base_has_wrapper and not overlay_has_wrapper:
        result = copy.deepcopy(base_data)
        merged_inner = deep_merge(
            base_data["app"],
            app_overrides,
            warnings_out=warnings_out,
            _path="app",
        )
        _validate_post_merge_app_config(
            base_data["app"], merged_inner, app_overrides
        )
        result["app"] = merged_inner
        return result

    if overlay_has_wrapper and not base_has_wrapper:
        unwrapped_overlay = app_overrides["app"]
        merged = deep_merge(
            base_data,
            unwrapped_overlay,
            warnings_out=warnings_out,
        )
        _validate_post_merge_app_config(base_data, merged, unwrapped_overlay)
        return merged

    merged = deep_merge(
        base_data,
        app_overrides,
        warnings_out=warnings_out,
    )
    if base_has_wrapper and overlay_has_wrapper:
        _validate_post_merge_app_config(
            base_data["app"], merged.get("app") or {}, app_overrides["app"]
        )
    else:
        _validate_post_merge_app_config(base_data, merged, app_overrides)
    return merged


def apply_app_overrides_in_dir(
    staged_dir: str | Path,
    profile: ResolvedProfile | None,
) -> tuple[Path | None, list[str]]:
    """Apply ``profile.app_overrides`` to staged ``app.json`` or ``app.yaml``.

    Intended to run inside ``_app_push``'s temp staging dir BEFORE
    ``resolve_app_env_variables_in_dir`` and ``resolve_env_booleans_in_dir``.
    """
    if profile is None or not profile.app_overrides:
        return None, []

    app_cfg = _find_app_config_file(staged_dir)
    if app_cfg is None:
        return None, []

    base_data = _load_structured_file(app_cfg)
    if not isinstance(base_data, dict):
        raise ProfileError(
            f"Expected mapping in '{app_cfg}', got {type(base_data).__name__}."
        )

    warnings_out: list[str] = []
    merged = merge_app_config_data(
        base_data, profile.app_overrides, warnings_out=warnings_out
    )
    _write_structured_file(app_cfg, merged)
    return app_cfg, warnings_out


def _serialize_for_diff(data: dict[str, Any], suffix: str) -> list[str]:
    if suffix.lower() in (".yaml", ".yml"):
        text = yaml.safe_dump(data, sort_keys=False)
    else:
        text = json.dumps(data, indent=2) + "\n"
    return text.splitlines(keepends=True)


def render_app_config_diff(
    app_dir: str | Path,
    profile: ResolvedProfile,
) -> str:
    """Return a unified diff of ``app.json``/``app.yaml`` with overrides."""
    app_cfg = _find_app_config_file(app_dir)
    if app_cfg is None or not profile.app_overrides:
        return ""
    base_data = _load_structured_file(app_cfg)
    if not isinstance(base_data, dict):
        return ""
    merged = merge_app_config_data(base_data, profile.app_overrides)
    before = _serialize_for_diff(base_data, app_cfg.suffix)
    after = _serialize_for_diff(merged, app_cfg.suffix)
    diff = list(
        difflib.unified_diff(
            before,
            after,
            fromfile=f"a/{app_cfg.name}",
            tofile=f"b/{app_cfg.name} (profile: {profile.name})",
        )
    )
    return "".join(diff)


def render_gecx_config_diff(
    base_config_path: Path | None,
    profile: ResolvedProfile,
) -> str:
    """Return a unified diff of ``gecx-config.json`` with profile applied."""
    base_data: dict[str, Any] = {}
    label = "gecx-config.json"
    if base_config_path is not None and base_config_path.is_file():
        loaded = _load_structured_file(base_config_path)
        if isinstance(loaded, dict):
            base_data = loaded
    merged = profile.resolve_gecx_config(base_data)
    before = _serialize_for_diff(base_data, ".json")
    after = _serialize_for_diff(merged, ".json")
    diff = list(
        difflib.unified_diff(
            before,
            after,
            fromfile=f"a/{label}",
            tofile=f"b/{label} (profile: {profile.name})",
        )
    )
    return "".join(diff)


# ---------------------------------------------------------------------------
# Safe `cxas pull --profile` preservation helpers
# ---------------------------------------------------------------------------

_MISSING = object()


def _extract_overridden_snapshot(
    base_dict: dict[str, Any],
    overlay: dict[str, Any],
) -> dict[str, Any]:
    """Capture base values at the exact paths touched by ``overlay``."""
    snap: dict[str, Any] = {}
    for key, oval in overlay.items():
        if isinstance(oval, dict) and isinstance(base_dict.get(key), dict):
            snap[key] = _extract_overridden_snapshot(base_dict[key], oval)
        elif key in base_dict:
            snap[key] = copy.deepcopy(base_dict[key])
        else:
            snap[key] = _MISSING
    return snap


def _restore_from_snapshot(
    pulled_dict: dict[str, Any],
    snap: dict[str, Any],
    overlay: dict[str, Any],
    *,
    prefix: str = "",
    restored_paths: list[str] | None = None,
) -> None:
    """Restore snapshotted base values onto ``pulled_dict`` in place."""
    if restored_paths is None:
        restored_paths = []
    for key, oval in overlay.items():
        if key not in snap:
            continue
        key_path = f"{prefix}.{key}" if prefix else str(key)
        sval = snap[key]
        if (
            isinstance(oval, dict)
            and isinstance(sval, dict)
            and isinstance(pulled_dict.get(key), dict)
        ):
            _restore_from_snapshot(
                pulled_dict[key],
                sval,
                oval,
                prefix=key_path,
                restored_paths=restored_paths,
            )
        elif sval is _MISSING:
            if key in pulled_dict:
                pulled_dict.pop(key, None)
                restored_paths.append(key_path)
        elif pulled_dict.get(key) != sval:
            pulled_dict[key] = copy.deepcopy(sval)
            restored_paths.append(key_path)


def snapshot_overridden_app_values(
    app_dir: str | Path,
    profile: ResolvedProfile | None,
) -> dict[str, Any]:
    """Snapshot tracked base values in ``app.json``/``app.yaml`` before pull."""
    if profile is None or not profile.app_overrides:
        return {}
    app_cfg = _find_app_config_file(app_dir)
    if app_cfg is None:
        return {}
    try:
        base_data = _load_structured_file(app_cfg)
    except (ProfileError, OSError):
        return {}
    if not isinstance(base_data, dict):
        return {}

    overrides = profile.app_overrides
    if isinstance(base_data.get("app"), dict) and not isinstance(
        overrides.get("app"), dict
    ):
        inner = _extract_overridden_snapshot(base_data["app"], overrides)
        return {"app": inner}
    if isinstance(overrides.get("app"), dict) and not isinstance(
        base_data.get("app"), dict
    ):
        return _extract_overridden_snapshot(base_data, overrides["app"])
    return _extract_overridden_snapshot(base_data, overrides)


def restore_overridden_app_values(
    app_dir: str | Path,
    snapshot: dict[str, Any],
    profile: ResolvedProfile | None,
) -> list[str]:
    """Restore base tracked values after ``cxas pull --profile`` extracts."""
    if not snapshot or profile is None or not profile.app_overrides:
        return []
    app_cfg = _find_app_config_file(app_dir)
    if app_cfg is None:
        return []
    try:
        pulled_data = _load_structured_file(app_cfg)
    except (ProfileError, OSError):
        return []
    if not isinstance(pulled_data, dict):
        return []

    overrides = profile.app_overrides
    restored_paths: list[str] = []

    if (
        isinstance(pulled_data.get("app"), dict)
        and isinstance(snapshot.get("app"), dict)
        and not isinstance(overrides.get("app"), dict)
    ):
        _restore_from_snapshot(
            pulled_data["app"],
            snapshot["app"],
            overrides,
            prefix="app",
            restored_paths=restored_paths,
        )
    elif isinstance(overrides.get("app"), dict) and not isinstance(
        pulled_data.get("app"), dict
    ):
        _restore_from_snapshot(
            pulled_data,
            snapshot,
            overrides["app"],
            restored_paths=restored_paths,
        )
    else:
        _restore_from_snapshot(
            pulled_data,
            snapshot,
            overrides,
            restored_paths=restored_paths,
        )

    if restored_paths:
        _write_structured_file(app_cfg, pulled_data)
    return restored_paths


# ---------------------------------------------------------------------------
# Shared runtime config resolver
# ---------------------------------------------------------------------------


def resolve_runtime_config(
    profile_name: str | None = None,
    *,
    start_dir: str | Path | None = None,
    base_gecx_config: dict[str, Any] | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> ResolvedRuntimeConfig:
    """Resolve runtime config from ``gecx-config.json``, profile, and CLI.

    Precedence (lowest -> highest):
    1. ``gecx-config.json`` at active project root (or ``base_gecx_config``).
    2. Profile (``--profile`` or ``CXAS_PROFILE``), including ``extends``.
    3. Explicit ``cli_overrides`` (non-None values only).
    """
    profile = load_profile(profile_name, start_dir=start_dir)
    if profile is not None:
        project_root = profile.project_root
    else:
        project_root, _ = resolve_project_root(start_dir)

    warnings_out: list[str] = list(profile.warnings) if profile else []

    if base_gecx_config is not None:
        gecx_cfg: dict[str, Any] = copy.deepcopy(base_gecx_config)
    else:
        gecx_path = project_root / "gecx-config.json"
        if gecx_path.is_file():
            try:
                loaded = _load_structured_file(gecx_path)
                gecx_cfg = loaded if isinstance(loaded, dict) else {}
            except (ProfileError, OSError):
                gecx_cfg = {}
        else:
            gecx_cfg = {}

    if profile is not None:
        gecx_cfg = profile.resolve_gecx_config(
            gecx_cfg, warnings_out=warnings_out
        )

    cli = cli_overrides or {}

    project_id = (
        cli.get("project_id")
        or (profile.target.project_id if profile else None)
        or gecx_cfg.get("gcp_project_id")
    )
    location = (
        cli.get("location")
        or (profile.target.location if profile else None)
        or gecx_cfg.get("location")
    )
    app_id = (
        cli.get("app_id")
        or (profile.target.app_id if profile else None)
        or gecx_cfg.get("deployed_app_id")
    )
    display_name = cli.get("display_name") or (
        profile.target.display_name if profile else None
    )
    deployment_id = (
        cli.get("deployment_id")
        or (profile.target.deployment_id if profile else None)
        or gecx_cfg.get("deployment_id")
    )

    app_dir: str | None = cli.get("app_dir")
    if not app_dir:
        if profile and profile.app_dir is not None:
            app_dir = str(profile.app_dir)
        elif gecx_cfg.get("app_dir"):
            app_dir = str(_expand_path(str(gecx_cfg["app_dir"]), project_root))

    env_file: str | None = cli.get("env_file")
    if not env_file and profile and profile.env_file is not None:
        env_file = str(profile.env_file)

    modality = cli.get("modality") or gecx_cfg.get("modality")
    model = cli.get("model") or gecx_cfg.get("model")

    # Derive full app resource name if possible.
    app_name = cli.get("app_name")
    if not app_name:
        if app_id and str(app_id).startswith("projects/"):
            app_name = str(app_id)
        elif project_id and location and app_id:
            app_name = (
                f"projects/{project_id}/locations/{location}/apps/{app_id}"
            )

    # Keep resolved gecx_cfg consistent with CLI overrides.
    if project_id is not None:
        gecx_cfg["gcp_project_id"] = project_id
    if location is not None:
        gecx_cfg["location"] = location
    if app_id is not None:
        gecx_cfg["deployed_app_id"] = app_id
    if deployment_id is not None:
        gecx_cfg["deployment_id"] = deployment_id
    if modality is not None:
        gecx_cfg["modality"] = modality
    if model is not None:
        gecx_cfg["model"] = model
    if app_dir is not None:
        gecx_cfg["app_dir"] = app_dir

    return ResolvedRuntimeConfig(
        project_root=project_root,
        profile=profile,
        project_id=project_id,
        location=location,
        app_id=app_id,
        app_name=app_name,
        display_name=display_name,
        deployment_id=deployment_id,
        app_dir=app_dir,
        env_file=env_file,
        modality=modality,
        model=model,
        gecx_config=gecx_cfg,
        warnings=warnings_out,
    )


def print_profile_warnings(warnings_list: list[str]) -> None:
    """Emit profile warnings to stderr."""
    for warning in warnings_list:
        print(f"Warning: {warning}", file=sys.stderr)
