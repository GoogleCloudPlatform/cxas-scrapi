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

"""GECX SCRAPI Workspace and Profile Resolution Engine.

ARCHITECTURAL INTENT:
This module decouples execution directories from GECX asset folders. It allows
stateless, concurrent orchestration of GECX project actions (evaluations,
compilation,
deployment) by deriving the target context entirely from directory-based
pointers.
This supports parallel multi-agent runner subdirectories in shared repositories.

KEY INVARIANTS:
1. Workspace Root: Resolved strictly by finding `.scrapi/active-project` (TOML).
   - Upward crawl from CWD stops at home (`Path.home()`) or root (`/`).
   - If no pointer directory is found, raises ValueError in downstream loaders.
2. Project Directory: Constructed by joining the resolved Workspace Root path
and the
   relative `base-dir` value read from `.scrapi/active-project`.
3. Configuration Keys: Standardized on kebab-case dashes (e.g. `gcp-project-id`,
   `deployed-app-id`, `app-dir`, `evals-dir`, `output-dir`) in configuration
   files.
   - Internal loading functions recursively transform all keys to snake_case
   underscores
     (e.g. `gcp_project_id`, `app_dir`) for pythonic execution.
   - Serialization functions map snake_case attributes back to kebab-case
   dashes.
4. Profile Overlay: Profiles defined in `gecx-profiles.toml` overlay base config
keys.
   - Active profile selection is stored in the `active-profile` key of
   `.scrapi/active-project`.
   - Modifying or unsetting the active profile invalidates the runtime
   configuration cache
     (`_workspace_config_cache = None`).

EXCEPTIONS HOOKS:
- Downstream tasks expecting resolved workspace/configs must call functions
decorated
  with `@require_workspace` to guarantee a resolved root exists before
  execution.
- File unlinking and I/O failures catch general Exceptions and log them to
`logger.debug`
  to preserve non-intrusive runtime execution.
"""

import functools
import json
import logging
import pathlib
import sys
import warnings
from collections.abc import Callable
from typing import Any

import toml

logger = logging.getLogger(__name__)

_project_dir = None
_active_project_cache = None
_workspace_root_cache = None
_workspace_config_cache = None


def _to_snake_case_keys(d: dict[str, Any]) -> dict[str, Any]:
    """Recursively convert all dictionary keys from kebab-case (dashes) to snake_case (underscores)."""
    res = {}
    for k, v in d.items():
        new_k = k.replace("-", "_")
        if isinstance(v, dict):
            res[new_k] = _to_snake_case_keys(v)
        else:
            res[new_k] = v
    return res


def _to_kebab_case_keys(d: dict[str, Any]) -> dict[str, Any]:
    """Recursively convert all dictionary keys from snake_case (underscores) to kebab-case (dashes)."""
    res = {}
    for k, v in d.items():
        new_k = k.replace("_", "-")
        if isinstance(v, dict):
            res[new_k] = _to_kebab_case_keys(v)
        else:
            res[new_k] = v
    return res


def _get_pointer_paths(directory: pathlib.Path) -> list[pathlib.Path]:
    """Return candidates for the pointer file in a given directory."""
    return [
        directory / ".scrapi" / "active-project",
        directory / ".active-project",
    ]


def _migrate_pointer(dir_path: pathlib.Path) -> None:
    """Migrates legacy and JSON pointer files to TOML format."""
    legacy_file = dir_path / ".active-project"
    active_project_file = dir_path / ".scrapi" / "active-project"

    # 1. If legacy file exists, move its content to active-project in TOML format
    if legacy_file.is_file():
        try:
            with open(legacy_file, encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                new_dir = dir_path / ".scrapi"
                new_dir.mkdir(parents=True, exist_ok=True)

                data = {}
                try:
                    data = toml.loads(content)
                except Exception:
                    try:
                        data = json.loads(content)
                    except Exception:
                        workspace_root = find_workspace_root()
                        base = (
                            pathlib.Path(workspace_root)
                            if workspace_root
                            else dir_path
                        )
                        abs_path = (base / content).resolve()
                        data = {"base-dir": str(abs_path)}

                if isinstance(data, dict) and "base-dir" in data:
                    base_dir = data["base-dir"]
                    if not pathlib.Path(base_dir).is_absolute():
                        workspace_root = find_workspace_root()
                        base = (
                            pathlib.Path(workspace_root)
                            if workspace_root
                            else dir_path
                        )
                        data["base-dir"] = str((base / base_dir).resolve())

                with active_project_file.open("w", encoding="utf-8") as f:
                    toml.dump(data, f)
            legacy_file.unlink()
            warnings.warn(
                "Legacy pointer file '.active-project' is deprecated. Seamlessly"
                " migrated to '.scrapi/active-project'. Please commit the new"
                " '.scrapi/' folder.",
                category=UserWarning,
                stacklevel=2,
            )
        except Exception as e:
            logger.debug("Failed to migrate legacy pointer file: %s", e)

    # 2. If active-project file is JSON, migrate it to TOML in place
    if active_project_file.is_file():
        try:
            with active_project_file.open("r", encoding="utf-8") as f:
                content = f.read().strip()
            is_json = False
            try:
                json_data = json.loads(content)
                if isinstance(json_data, dict):
                    try:
                        toml_data = toml.loads(content)
                        if toml_data != json_data:
                            is_json = True
                    except Exception:
                        is_json = True
            except Exception:
                pass

            if is_json:
                with active_project_file.open("w", encoding="utf-8") as f:
                    toml.dump(json_data, f)
                warnings.warn(
                    "Pointer file '.scrapi/active-project' was in JSON format, which is"
                    " deprecated. Seamlessly migrated to TOML format.",
                    category=UserWarning,
                    stacklevel=2,
                )
        except Exception as e:
            logger.debug(
                "Failed to migrate pointer file in place to TOML: %s", e
            )


def _migrate_config_to_toml(project_dir: pathlib.Path) -> None:
    """Silently migrates gecx-config.json to gecx-config.toml."""
    json_file = project_dir / "gecx-config.json"
    toml_file = project_dir / "gecx-config.toml"
    if json_file.is_file() and not toml_file.exists():
        try:
            with open(json_file, encoding="utf-8") as f:
                data = json.load(f)

            if "default" in data or "default" in _to_snake_case_keys(data):
                toml_data = _to_kebab_case_keys(data)
            else:
                toml_data = {"default": _to_kebab_case_keys(data)}

            with open(toml_file, "w", encoding="utf-8") as f:
                toml.dump(toml_data, f)
            json_file.unlink()
            warnings.warn(
                "Legacy configuration file 'gecx-config.json' is deprecated."
                " Seamlessly migrated to 'gecx-config.toml'. Please commit the new"
                " TOML file.",
                category=UserWarning,
                stacklevel=2,
            )
        except Exception as e:
            logger.warning(
                "Failed to silently migrate gecx-config.json to gecx-config.toml: %s",
                e,
            )


def _parse_pointer_as_toml(content: str) -> str | None:
    """Attempts to parse pointer file content as TOML, returning base-dir if found."""
    try:
        data = toml.loads(content)
        if isinstance(data, dict) and "base-dir" in data:
            return data["base-dir"]
    except Exception:
        pass
    return None


def _parse_pointer_as_json(content: str) -> str | None:
    """Attempts to parse pointer file content as JSON, returning base-dir if found."""
    try:
        data = json.loads(content)
        if isinstance(data, dict) and "base-dir" in data:
            return data["base-dir"]
    except Exception:
        pass
    return None


def _read_pointer_file(dir_path: pathlib.Path) -> str | None:
    """Reads GECX project path from pointer files in the given directory."""
    for pointer in _get_pointer_paths(dir_path):
        if pointer.is_file():
            try:
                with open(pointer, encoding="utf-8") as f:
                    content = f.read().strip()
                if not content:
                    continue

                if base_dir := _parse_pointer_as_toml(content):
                    return base_dir

                if base_dir := _parse_pointer_as_json(content):
                    return base_dir

                # Fallback to flat text (relative path)
                return content
            except Exception as e:
                logger.debug("Failed to read pointer file %s: %s", pointer, e)
    return None


def find_workspace_root() -> str | None:
    """Find the workspace root by crawling up from cwd until a marker is found."""
    global _workspace_root_cache  # noqa: PLW0603
    if _workspace_root_cache:
        return _workspace_root_cache

    try:
        path = pathlib.Path.cwd().resolve()
    except Exception:
        return None

    home = pathlib.Path.home().resolve()

    while True:
        for pointer in _get_pointer_paths(path):
            if pointer.is_file():
                _workspace_root_cache = str(path)
                return _workspace_root_cache

        if path == home or path == pathlib.Path("/"):
            break

        parent = path.parent
        if parent == path:
            break
        path = parent

    return None


def find_project_dir() -> str | None:
    """Resolve the active project directory path without exiting on failure.

    Checks:
    1. Local pointer file in current working directory (CWD) (highest priority)
    2. Pointer file at the workspace root

    Returns:
        str | None: The absolute path to the active project directory, or None.
    """
    global _project_dir  # noqa: PLW0603
    if _project_dir:
        return _project_dir

    # Perform automatic migrations of legacy pointer files
    cwd_path = pathlib.Path.cwd()
    _migrate_pointer(cwd_path)

    workspace_root = find_workspace_root()
    if workspace_root:
        _migrate_pointer(pathlib.Path(workspace_root))

    # 1. Check local pointer file in CWD
    project_path_str = _read_pointer_file(cwd_path)
    if project_path_str:
        candidate = pathlib.Path(project_path_str)
        if not candidate.is_absolute():
            base = pathlib.Path(workspace_root) if workspace_root else cwd_path
            candidate = (base / candidate).resolve()
        else:
            candidate = candidate.resolve()
        _migrate_config_to_toml(candidate)
        if (candidate / "gecx-config.toml").exists() or (
            candidate / "gecx-config.json"
        ).exists():
            _project_dir = str(candidate)
            return _project_dir

    # 2. Check workspace root pointer file
    if workspace_root:
        project_path_str = _read_pointer_file(pathlib.Path(workspace_root))
        if project_path_str:
            candidate = pathlib.Path(project_path_str)
            if not candidate.is_absolute():
                candidate = (pathlib.Path(workspace_root) / candidate).resolve()
            else:
                candidate = candidate.resolve()
            _migrate_config_to_toml(candidate)
            if (candidate / "gecx-config.toml").exists() or (
                candidate / "gecx-config.json"
            ).exists():
                _project_dir = str(candidate)
                return _project_dir

    return None


def resolve_project_dir() -> str:
    """Find the absolute path to the active project directory.

    This function determines the active project directory by checking:
    1. Local pointer file in current working directory (CWD)
    2. Pointer file at the workspace root
    3. Git-style CWD upward traversal searching for gecx-config.json (primary)

    Returns:
        str: The absolute path to the validated active project directory.

    Raises:
        ValueError: If no project directory can be resolved.
    """
    res = find_project_dir()
    if res:
        return res

    raise ValueError(
        "No GECX project directory could be resolved.\nPlease set one using 'cxas"
        " workspace set [path-to-project-dir]',\nrun this command from within a"
        " GECX project directory containing gecx-config.json,\nor initialize a"
        " new project using 'cxas init'."
    )


def get_gecx_config_path() -> str:
    """Get the absolute path to the active project's configuration file."""
    project_dir = find_project_dir()
    base_dir = pathlib.Path(project_dir) if project_dir else pathlib.Path.cwd()
    toml_path = base_dir / "gecx-config.toml"
    if toml_path.exists():
        return str(toml_path)
    return str(base_dir / "gecx-config.json")


def get_workspace_path(*parts) -> str:
    """Join parts relative to the workspace root.

    A utility function to construct absolute file paths anchored to the root of
    the current SCRAPI workspace.

    Args:
        *parts: Variable length argument list of path components to join (e.g.,
          'data', 'file.txt').

    Returns:
        str: The absolute path constructed by joining the workspace root and the
            provided parts.

    Raises:
        SystemExit: If called when no SCRAPI workspace pointer can be found in the
            directory tree.
    """
    workspace_root = find_workspace_root()
    if not workspace_root:
        print("Error: No SCRAPI workspace found.")
        sys.exit(1)
    return str(pathlib.Path(workspace_root).joinpath(*parts))


def project_path(*parts) -> str:
    """Join parts relative to the active project directory.

    A utility function to construct absolute file paths anchored to the currently
    active GECX project directory (as resolved by `resolve_project_dir()`).

    Args:
        *parts: Variable length argument list of path components to join (e.g.,
          'evals', 'config.yaml').

    Returns:
        str: The absolute path constructed by joining the active project directory
            and the provided parts.
    """
    return str(pathlib.Path(resolve_project_dir()).joinpath(*parts))


_project_path = project_path


def callback_tests_path() -> str:
    """Get absolute path to callback tests directory."""
    config = load_workspace_config()
    evals_dir = config.get("evals_dir", "evals")
    return _project_path(evals_dir, "callback_tests")


def tool_tests_path() -> str:
    """Get absolute path to tool tests directory."""
    config = load_workspace_config()
    evals_dir = config.get("evals_dir", "evals")
    return _project_path(evals_dir, "tool_tests")


def goldens_path() -> str:
    """Get absolute path to goldens directory."""
    config = load_workspace_config()
    evals_dir = config.get("evals_dir", "evals")
    return _project_path(evals_dir, "goldens")


def simulations_path() -> str:
    """Get absolute path to simulations directory."""
    config = load_workspace_config()
    evals_dir = config.get("evals_dir", "evals")
    return _project_path(evals_dir, "simulations")


def _resolve_profile_overrides(
    config_data: dict[str, Any], profile_path: str
) -> dict[str, Any]:
    """Traverse and resolve cascading overrides for a dot-separated profile path."""
    profiles_section = config_data.get("profiles", {})
    if not profiles_section:
        raise ValueError("No [profiles] section defined in configuration file.")

    segments = profile_path.split(".")
    root_profile = segments[0].replace("-", "_")
    if root_profile not in profiles_section:
        raise ValueError(
            f"Profile '{segments[0]}' is not defined under [profiles]."
        )

    current = profiles_section
    overrides = {}

    for segment in segments:
        segment_key = segment.replace("-", "_")
        if not isinstance(current, dict) or segment_key not in current:
            raise ValueError(f"Profile path segment '{segment}' not found.")
        current = current[segment_key]

        if isinstance(current, dict):
            # Extract only flat configuration values, ignore nested sub-profiles
            segment_overrides = {
                k: v for k, v in current.items() if not isinstance(v, dict)
            }
            overrides.update(segment_overrides)

    return overrides


def load_workspace_config() -> dict[str, Any]:
    """Loads configuration and overlays the active profile overrides.

    Returns:
      dict[str, Any]: Resolved project configuration dictionary.

    Raises:
      ValueError: If GECX project directory or configuration is missing or
      invalid.
    """
    global _workspace_config_cache  # noqa: PLW0603
    if _workspace_config_cache is not None:
        return _workspace_config_cache

    project_dir = resolve_project_dir()  # Can raise ValueError

    _migrate_config_to_toml(pathlib.Path(project_dir))

    config_file_toml = pathlib.Path(project_dir) / "gecx-config.toml"
    config_file_json = pathlib.Path(project_dir) / "gecx-config.json"

    raw_config = {}
    if config_file_toml.exists():
        config_file = config_file_toml
        try:
            with open(config_file, encoding="utf-8") as f:
                raw_config = toml.load(f)
        except Exception as e:
            raise ValueError(f"Failed to parse {config_file}: {e}") from e
    elif config_file_json.exists():
        config_file = config_file_json
        try:
            with open(config_file, encoding="utf-8") as f:
                raw_config = json.load(f)
        except Exception as e:
            raise ValueError(f"Failed to parse {config_file}: {e}") from e
    else:
        raise ValueError(
            f"Neither gecx-config.toml nor gecx-config.json found in {project_dir}."
            " Run 'cxas init' to create a default config."
        )

    # Extract baseline config from 'default' section, fallback to raw flat config
    config = _to_snake_case_keys(raw_config.get("default", raw_config))

    profile_path = None
    workspace_root = find_workspace_root()
    if workspace_root:
        active_project_file = (
            pathlib.Path(workspace_root) / ".scrapi" / "active-project"
        )
        if active_project_file.is_file():
            try:
                with active_project_file.open("r", encoding="utf-8") as f:
                    profile_path = toml.load(f).get("active-profile")
            except Exception:
                pass

    if profile_path:
        try:
            overrides = _resolve_profile_overrides(
                _to_snake_case_keys(raw_config), profile_path
            )
            config.update(overrides)
        except ValueError as e:
            raise ValueError(f"Profile resolution error: {e}") from e
        except Exception as e:
            logger.warning(
                "Failed to resolve profile overrides for '%s': %s",
                profile_path,
                e,
            )

    project = config.get("gcp_project_id")
    app_id = config.get("deployed_app_id")
    if not project or not app_id:
        raise ValueError(
            f"{config_file.name} missing 'gcp_project_id' or 'deployed_app_id'. "
            "Update it with 'cxas workspace set'."
        )

    config.setdefault("location", "us")
    config.setdefault("app_dir", "app")
    config.setdefault("output_dir", ".scrapi-out")
    config["_project_dir"] = project_dir

    # ONLY cache the workspace configuration if it is fully resolved & valid
    if project and app_id:
        _workspace_config_cache = config

    return config


def get_output_dir() -> str:
    """Get the output directory path for the active project.

    This function determines where evaluation reports and generated artifacts
    should be saved. It reads the `output_dir` property from the active project's
    configuration (defaulting to '.scrapi-out'). If the configured path is
    absolute, it is returned directly; otherwise, it is resolved relative to the
    active project directory.

    Returns:
        str: The absolute path to the designated output directory.
    """
    config = load_workspace_config()
    out_dir = config.get("output_dir", ".scrapi-out")
    out_path = pathlib.Path(out_dir)
    if out_path.is_absolute():
        return str(out_path)
    return project_path(out_dir)


def app_name() -> str:
    """Load the full CX Agent Studio app name from config.

    This function constructs the standard fully-qualified resource name required
    by GCP APIs using the `gcp_project_id`, `location`, and `deployed_app_id`
    values from the active project's configuration.

    Returns:
        str: The fully-qualified app resource name in the format:
            `projects/<project_id>/locations/<location>/apps/<app_id>`
    """
    config = load_workspace_config()
    return (
        f"projects/{config['gcp_project_id']}"
        f"/locations/{config['location']}"
        f"/apps/{config['deployed_app_id']}"
    )


def create_default_config(target_dir: str) -> None:
    """Create a default gecx-config.toml file in the target directory if it doesn't exist."""
    config_path = pathlib.Path(target_dir) / "gecx-config.toml"
    if not config_path.exists():
        default_config = {
            "default": {
                "gcp-project-id": "YOUR_PROJECT_ID",
                "deployed-app-id": "YOUR_APP_ID",
                "location": "us",
                "app-dir": "app",
                "evals-dir": "evals",
                "output-dir": ".scrapi-out",
            }
        }
        with open(config_path, "w", encoding="utf-8") as f:
            toml.dump(default_config, f)

        print(f"Created default configuration: {config_path}")
    else:
        print(f"Configuration file already exists: {config_path}")


def update_workspace_config(updates: dict[str, Any]) -> tuple[bool, str]:
    """Update the active project's configuration file with the provided updates.

    Returns:
        tuple[bool, str]: (whether config was updated, path to config file)
    """
    project_dir = resolve_project_dir()
    config_file = pathlib.Path(project_dir) / "gecx-config.toml"
    if not config_file.exists():
        # Fallback to json if not migrated yet
        config_file = pathlib.Path(project_dir) / "gecx-config.json"
        if not config_file.exists():
            raise FileNotFoundError(
                f"Configuration file not found in {project_dir}."
            )

    # Read existing config
    if config_file.suffix == ".toml":
        with open(config_file, encoding="utf-8") as f:
            config = _to_snake_case_keys(toml.load(f))
    else:
        with open(config_file, encoding="utf-8") as f:
            config = json.load(f)

    # Check if config is structured with a default section, target modifications there
    target_section = config
    if "default" in config and isinstance(config["default"], dict):
        target_section = config["default"]

    updated = False
    for key, value in updates.items():
        if value is not None:
            target_section[key] = value
            updated = True

    if updated:
        # Always write back to gecx-config.toml in TOML format with kebab-case keys
        target_file = pathlib.Path(project_dir) / "gecx-config.toml"
        if "default" in config or "default" in _to_snake_case_keys(config):
            toml_data = _to_kebab_case_keys(config)
        else:
            toml_data = {"default": _to_kebab_case_keys(config)}

        with open(target_file, "w", encoding="utf-8") as f:
            toml.dump(toml_data, f)

        if config_file != target_file:
            config_file.unlink()  # delete old json
        config_file = target_file

    return updated, str(config_file)


def unset_active_project() -> bool:
    """Deletes active-project pointer files in CWD and workspace root.

    Returns:
        bool: True if at least one pointer file was found and deleted, False
        otherwise.
    """
    global _project_dir, _active_project_cache, _workspace_config_cache  # noqa: PLW0603
    deleted = False

    # Clear cached project directory/configurations
    _project_dir = None
    _active_project_cache = None
    _workspace_config_cache = None

    # 1. Check CWD
    cwd_path = pathlib.Path.cwd()
    for pointer in _get_pointer_paths(cwd_path):
        if pointer.is_file():
            try:
                pointer.unlink()
                deleted = True
            except Exception as e:
                logger.debug("Failed to delete pointer file %s: %s", pointer, e)

    # 2. Check Workspace Root
    workspace_root = find_workspace_root()
    if workspace_root:
        for pointer in _get_pointer_paths(pathlib.Path(workspace_root)):
            if pointer.is_file():
                try:
                    pointer.unlink()
                    deleted = True
                except Exception as e:
                    logger.debug(
                        "Failed to delete pointer file %s: %s", pointer, e
                    )

    return deleted


def require_workspace(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that raises ValueError if no workspace is active."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not find_workspace_root():
            raise ValueError(
                "No active workspace set. Profiles can only be managed within an"
                " active workspace."
            )
        return func(*args, **kwargs)

    return wrapper
