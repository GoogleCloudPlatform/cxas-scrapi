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

"""Canonical lockfiles, 3-way CES drift/conflict engine, and pre-push gate."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal

from cxas_scrapi.build.bundle_io import (
    BuildGateError,
    DriftGateError,
    DriftReport,
    NormalizedResource,
    ResourceDrift,
    assert_safe_subpath,
    load_json_dict_strict,
    load_mapping_strict,
    read_utf8_strict,
    write_canonical_json,
    write_canonical_mapping,
)
from cxas_scrapi.core.python_bundler import (
    PythonBundlerError,
    inspect_entrypoint_bundle,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

VOLATILE_JSON_KEYS: frozenset[str] = frozenset(
    {"uuid", "uid", "etag", "createTime", "updateTime"}
)

# CES returns these reference lists sorted alphabetically on export, so their
# order carries no meaning across a CES round trip.
ORDER_INSENSITIVE_STRING_LIST_KEYS: frozenset[str] = frozenset(
    {"tools", "childAgents", "guardrails", "toolsets"}
)
# Lists of objects identified by their `name` that CES re-orders on export.
NAME_KEYED_LIST_KEYS: frozenset[str] = frozenset({"variableDeclarations"})
# app.json keys that describe the CES control plane rather than bundle content:
# `displayName` is chosen at push time (`cxas push --display-name`); `locked`
# is never present in a CES export (GetApp reports it, the export omits it) and
# is applied by `cxas push` via UpdateApp after import (the env manifest's
# `locked:` declares it in the built app.json).
APP_CONTROL_PLANE_KEYS: frozenset[str] = frozenset({"displayName", "locked"})
# Values CES adds server-side when a bundle does not set them. A key is
# ignored only when its normalized value equals the server default, so a real
# override (e.g. a shorter retention window) still reads as drift.
CES_SERVER_DEFAULT_APP_VALUES: dict[str, Any] = {
    "loggingSettings": {
        "conversationLoggingSettings": {"retentionWindow": "31536000s"}
    },
}
# CES replaces OpenAPI `servers[].url` with this placeholder on export and
# moves the concrete URL into environment.json (`tools.<name>.openApiTool.url`).
CES_ENV_VAR_PLACEHOLDER = "$env_var"
_OPENAPI_SERVER_URL_RE = re.compile(r"^(\s*-\s*url:\s*)\S.*$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_CONFIG_EXTS = (".json", ".yaml", ".yml")
_OPENAPI_SCHEMA_FILENAMES = frozenset(
    {"open_api_schema.yaml", "open_api_schema.yml"}
)

DriftStatus = Literal["CLEAN", "DRIFT", "ORPHAN", "CONFLICT"]
ResolutionMode = Literal["accept-live", "force-ours"]


def _paths_overlap(a: Path, b: Path) -> bool:
    """Return True if ``a`` and ``b`` are equal or nested inside each other."""
    ra, rb = a.resolve(), b.resolve()
    return ra == rb or ra in rb.parents or rb in ra.parents


def _find_config_file(
    directory: Path,
    stem: str,
    root: Path,
    *,
    fallback_any: bool = False,
    exclude_names: frozenset[str] = frozenset(),
    label: str = "app bundle",
) -> Path | None:
    """Locate ``<stem>.{json,yaml,yml}`` (or fallback config) safely."""
    for ext in _CONFIG_EXTS:
        cand = directory / f"{stem}{ext}"
        if cand.is_symlink():
            raise BuildGateError(
                f"Symlinks are not permitted in {label}: {cand}"
            )
        if cand.is_file():
            assert_safe_subpath(cand, root, label=label)
            return cand
    if fallback_any:
        for ext in _CONFIG_EXTS:
            for cand in sorted(directory.glob(f"*{ext}")):
                if cand.name in exclude_names:
                    continue
                if cand.is_symlink():
                    raise BuildGateError(
                        f"Symlinks are not permitted in {label}: {cand}"
                    )
                if cand.is_file():
                    assert_safe_subpath(cand, root, label=label)
                    return cand
    return None


def _validate_safe_identifier(value: str, *, label: str) -> str:
    """Validate an app_id or resource_id against traversal/flag injection."""
    if not isinstance(value, str) or not value.strip():
        raise BuildGateError(f"Invalid empty {label}.")
    if (
        "\\" in value
        or value.startswith(("/", "-"))
        or "\x00" in value
        or ".." in PurePosixPath(value).parts
    ):
        raise BuildGateError(
            f"Unsafe {label} {value!r}: path traversal or leading '-' is "
            "not permitted."
        )
    posix = PurePosixPath(value)
    if posix.is_absolute() or any(
        part in {"", ".", ".."} or (len(part) >= 2 and part[1] == ":")
        for part in posix.parts
    ):
        raise BuildGateError(
            f"Unsafe {label} {value!r}: must be a clean relative identifier."
        )
    return value


def _walk_safe_py_files(entry: Path, root: Path) -> list[Path]:
    """Return sorted `.py` files under ``entry``, rejecting any symlinks."""
    py_files: list[Path] = []
    for cur_root, dirs, files in os.walk(entry, followlinks=False):
        dirs[:] = sorted(
            d for d in dirs if d != "__pycache__" and not d.startswith(".")
        )
        for d in dirs:
            assert_safe_subpath(Path(cur_root) / d, root, label="app bundle")
        for fname in sorted(files):
            if fname.startswith("."):
                continue
            fpath = Path(cur_root) / fname
            assert_safe_subpath(fpath, root, label="app bundle")
            if fname.endswith(".py"):
                py_files.append(fpath)
    return sorted(py_files)


def _is_proto3_default(value: Any) -> bool:
    """True for values CES (proto3 JSON) omits on export: false/null/[]/{}."""
    if value is False or value is None:
        return True
    return isinstance(value, (list, dict)) and not value


def _sort_name_keyed(items: list[Any]) -> list[Any]:
    if all(
        isinstance(i, dict) and isinstance(i.get("name"), str) for i in items
    ):
        return sorted(items, key=lambda i: i["name"])
    return items


def normalize_json_obj(
    obj: Any, *, is_root: bool = True, _parent_key: str | None = None
) -> Any:
    """Canonicalizes a CES resource JSON so a CES round trip reads as CLEAN.

    Strips volatile control-plane envelope fields, drops proto3 default values
    that CES omits on export (`false`, `null`, `[]`, `{}`), sorts reference
    lists CES returns alphabetically, and orders name-keyed declarations.
    """
    if isinstance(obj, dict):
        has_display_name = "displayName" in obj
        cleaned: dict[str, Any] = {}
        for k, v in obj.items():
            if k in VOLATILE_JSON_KEYS:
                continue
            if k == "name":
                if is_root or has_display_name:
                    continue
                if (
                    isinstance(v, str)
                    and v.startswith("projects/")
                    and "/apps/" in v
                ):
                    continue
            norm_v = normalize_json_obj(v, is_root=False, _parent_key=k)
            if _is_proto3_default(norm_v):
                continue
            cleaned[k] = norm_v
        return cleaned
    if isinstance(obj, list):
        raw_items = (
            _sort_name_keyed(obj)
            if _parent_key in NAME_KEYED_LIST_KEYS
            else obj
        )
        items = [normalize_json_obj(item, is_root=False) for item in raw_items]
        if _parent_key in ORDER_INSENSITIVE_STRING_LIST_KEYS and all(
            isinstance(i, str) for i in items
        ):
            return sorted(items)
        return items
    return obj


def _normalize_app_obj(raw_app: dict[str, Any]) -> dict[str, Any]:
    norm_app = normalize_json_obj(raw_app, is_root=True)
    for key in APP_CONTROL_PLANE_KEYS:
        norm_app.pop(key, None)
    for key, default_val in CES_SERVER_DEFAULT_APP_VALUES.items():
        if key in norm_app and norm_app[key] == normalize_json_obj(
            default_val, is_root=False
        ):
            norm_app.pop(key)
    return norm_app


def _load_environment_tool_urls(root: Path) -> dict[str, str]:
    """Reads `tools.<name>.openApiTool.url` from environment.json."""
    env_path = root / "environment.json"
    if env_path.is_symlink():
        raise BuildGateError(
            f"Symlinks are not permitted in environment.json: {env_path}"
        )
    if not env_path.is_file():
        return {}
    assert_safe_subpath(env_path, root, label="app bundle")
    env = load_json_dict_strict(env_path, context="environment.json")
    tools = env.get("tools")
    urls: dict[str, str] = {}
    if isinstance(tools, dict):
        for t_name, t_cfg in tools.items():
            oa = t_cfg.get("openApiTool") if isinstance(t_cfg, dict) else None
            if isinstance(oa, dict) and isinstance(oa.get("url"), str):
                urls[str(t_name)] = oa["url"]
    return urls


def _canonical_openapi_schema_lines(text: str) -> list[str]:
    return [
        _OPENAPI_SERVER_URL_RE.sub(rf"\g<1>{CES_ENV_VAR_PLACEHOLDER}", line)
        for line in text.replace("\r\n", "\n").splitlines()
    ]


def _make_normalized_resource(
    resource_id: str, canonical_obj: dict[str, Any]
) -> NormalizedResource:
    normalized = (
        json.dumps(canonical_obj, indent=2, sort_keys=True, ensure_ascii=False)
        + "\n"
    )
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return NormalizedResource(
        resource_id=resource_id,
        sha256=digest,
        normalized=normalized,
    )


def extract_normalized_resources(
    app_dir: Path | str,
) -> dict[str, NormalizedResource]:
    """Extracts volatile-stripped canonical resources from an app export dir."""
    raw_root = Path(app_dir)
    if raw_root.is_symlink():
        raise BuildGateError(
            f"Symlinks are not permitted as app export directory: {raw_root}"
        )
    root = raw_root.resolve()
    if not root.is_dir():
        raise BuildGateError(f"App export directory does not exist: {root}")

    resources: dict[str, NormalizedResource] = {}

    app_cfg_path = _find_config_file(root, "app", root, label="app bundle")
    global_instr_path = root / "global_instruction.txt"
    if global_instr_path.is_symlink():
        raise BuildGateError(
            f"Symlinks are not permitted in app bundle: {global_instr_path}"
        )
    if app_cfg_path is not None or global_instr_path.is_file():
        if global_instr_path.is_file():
            assert_safe_subpath(global_instr_path, root, label="app bundle")
        raw_app = (
            load_mapping_strict(app_cfg_path, context=app_cfg_path.name)
            if app_cfg_path is not None
            else {}
        )
        norm_app = _normalize_app_obj(raw_app)
        if global_instr_path.is_file():
            norm_app["_globalInstructionLines"] = (
                read_utf8_strict(
                    global_instr_path, context="global_instruction.txt"
                )
                .replace("\r\n", "\n")
                .splitlines()
            )
        resources["app"] = _make_normalized_resource("app", norm_app)

    env_tool_urls = _load_environment_tool_urls(root)

    for category in ("agents", "tools", "guardrails", "toolsets"):
        cat_dir = root / category
        if cat_dir.is_symlink():
            raise BuildGateError(
                f"Symlinks are not permitted in app bundle: {cat_dir}"
            )
        if not cat_dir.is_dir():
            continue
        assert_safe_subpath(cat_dir, root, label="app bundle")
        for entry in sorted(cat_dir.iterdir()):
            assert_safe_subpath(entry, root, label="app bundle")
            if entry.is_dir():
                dir_name = entry.name
                res_id = f"{category}/{dir_name}"
                primary_cfg = _find_config_file(
                    entry,
                    dir_name,
                    root,
                    fallback_any=True,
                    exclude_names=_OPENAPI_SCHEMA_FILENAMES,
                    label=res_id,
                )
                raw_obj = (
                    load_mapping_strict(primary_cfg, context=f"{res_id} config")
                    if primary_cfg is not None
                    else {}
                )
                norm_obj = normalize_json_obj(raw_obj, is_root=True)

                if category == "agents":
                    instr_file = entry / "instruction.txt"
                    if instr_file.is_symlink() or instr_file.is_file():
                        assert_safe_subpath(instr_file, root, label=res_id)
                    if instr_file.is_file():
                        norm_obj["_instructionLines"] = (
                            read_utf8_strict(
                                instr_file, context=f"{res_id} instruction.txt"
                            )
                            .replace("\r\n", "\n")
                            .splitlines()
                        )

                if category in ("tools", "toolsets"):
                    oa_cfg = norm_obj.get("openApiTool")
                    env_url = env_tool_urls.get(
                        str(norm_obj.get("displayName", dir_name)),
                        env_tool_urls.get(dir_name),
                    )
                    if (
                        isinstance(oa_cfg, dict)
                        and "url" not in oa_cfg
                        and env_url is not None
                    ):
                        oa_cfg["url"] = env_url
                    oa_dir = entry / "open_api_tool"
                    oa_file = oa_dir / "open_api_schema.yaml"
                    if oa_dir.is_symlink():
                        raise BuildGateError(
                            f"Symlinks are not permitted in {res_id}: {oa_dir}"
                        )
                    if oa_file.is_symlink() or oa_file.is_file():
                        assert_safe_subpath(oa_file, root, label=res_id)
                    if oa_file.is_file():
                        oa_raw = read_utf8_strict(
                            oa_file, context=f"{res_id} OpenAPI schema"
                        )
                        if isinstance(oa_cfg, dict) and "url" in oa_cfg:
                            norm_obj["_openApiSchemaLines"] = (
                                _canonical_openapi_schema_lines(oa_raw)
                            )
                        else:
                            norm_obj["_openApiSchemaLines"] = oa_raw.replace(
                                "\r\n", "\n"
                            ).splitlines()

                py_files = _walk_safe_py_files(entry, root)
                if py_files:
                    extra_files: dict[str, list[str]] = {}
                    bundle_digests: dict[str, str] = {}
                    for p in py_files:
                        rel_posix = p.relative_to(entry).as_posix()
                        raw_py = read_utf8_strict(
                            p, context=f"{res_id} Python file"
                        )
                        try:
                            clean_py, bundle_digest, _, _ = (
                                inspect_entrypoint_bundle(
                                    raw_py, p, app_dir=root
                                )
                            )
                        except PythonBundlerError as exc:
                            raise BuildGateError(str(exc)) from exc
                        extra_files[rel_posix] = clean_py.replace(
                            "\r\n", "\n"
                        ).splitlines()
                        if bundle_digest is not None:
                            bundle_digests[rel_posix] = bundle_digest
                    norm_obj["_extraFiles"] = extra_files
                    if bundle_digests:
                        norm_obj["_sharedBundleDigests"] = bundle_digests

                resources[res_id] = _make_normalized_resource(res_id, norm_obj)

            elif entry.is_file() and entry.suffix.lower() in _CONFIG_EXTS:
                res_id = f"{category}/{entry.stem}"
                raw_obj = load_mapping_strict(entry, context=f"{res_id} config")
                norm_obj = normalize_json_obj(raw_obj, is_root=True)
                resources[res_id] = _make_normalized_resource(res_id, norm_obj)

    return dict(sorted(resources.items()))


def write_lockfile(
    app_dir: Path | str,
    lockfile_path: Path | str | None = None,
    *,
    app_id: str | None = None,
) -> Path:
    """Writes canonical normalized SHA-256 lockfile (`<app>.lock.json`)."""
    raw_dir = Path(app_dir)
    if raw_dir.is_symlink():
        raise BuildGateError(
            f"Symlinks are not permitted as bundle directory: {raw_dir}"
        )
    bundle_dir = raw_dir.resolve()
    if lockfile_path is not None:
        raw_lock = Path(lockfile_path)
        if raw_lock.is_symlink():
            raise BuildGateError(
                f"Symlinks are not permitted as lockfile path: {raw_lock}"
            )
        target_lock = raw_lock.resolve()
    else:
        target_lock = bundle_dir.parent / f"{bundle_dir.name}.lock.json"
        if target_lock.is_symlink():
            raise BuildGateError(
                f"Symlinks are not permitted as lockfile path: {target_lock}"
            )
    eff_app_id = app_id if app_id is not None else bundle_dir.name
    _validate_safe_identifier(eff_app_id, label="app_id")

    resources = extract_normalized_resources(bundle_dir)
    payload: dict[str, Any] = {
        "app_id": eff_app_id,
        "resources": {
            res_id: {
                "normalized": res.normalized,
                "sha256": res.sha256,
            }
            for res_id, res in sorted(resources.items())
        },
        "schema_version": "1.0",
    }
    write_canonical_json(target_lock, payload)
    return target_lock


def _load_lockfile_resources(
    lockfile_path: Path | None,
) -> dict[str, NormalizedResource]:
    if lockfile_path is None:
        return {}
    if lockfile_path.is_symlink():
        raise BuildGateError(
            f"Symlinks are not permitted as lockfile: {lockfile_path}"
        )
    if not lockfile_path.is_file():
        return {}
    data = load_json_dict_strict(lockfile_path, context="lockfile")
    if data.get("schema_version") != "1.0":
        raise BuildGateError(
            f"Unsupported or missing schema_version in lockfile "
            f"{lockfile_path}: expected '1.0', got "
            f"{data.get('schema_version')!r}."
        )
    if "resources" not in data or not isinstance(data["resources"], dict):
        raise BuildGateError(
            f"Invalid lockfile {lockfile_path}: 'resources' must be present "
            "and be a JSON object."
        )
    raw_res = data["resources"]
    loaded: dict[str, NormalizedResource] = {}
    for res_id, entry in raw_res.items():
        _validate_safe_identifier(str(res_id), label="lockfile resource_id")
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("sha256"), str)
            or not _SHA256_HEX_RE.fullmatch(entry["sha256"])
        ):
            raise BuildGateError(
                f"Invalid lockfile {lockfile_path}: resource {res_id!r} "
                "must be an object containing a 64-character lowercase hex "
                "'sha256'."
            )
        raw_norm = entry.get("normalized", "")
        if not isinstance(raw_norm, str):
            raise BuildGateError(
                f"Invalid lockfile {lockfile_path}: resource {res_id!r} "
                "'normalized' field must be a string."
            )
        loaded[res_id] = NormalizedResource(
            resource_id=res_id,
            sha256=entry["sha256"],
            normalized=raw_norm,
            accepted_from_live=bool(entry.get("accepted_from_live", False)),
        )
    return loaded


def _has_app_marker(directory: Path) -> bool:
    return (
        (directory / "app.json").is_file()
        or (directory / "app.yaml").is_file()
        or (directory / "app.yml").is_file()
        or (directory / "agents").is_dir()
    )


def _resolve_app_bundle_dir(base_or_bundle: Path, app_id: str) -> Path:
    _validate_safe_identifier(app_id, label="app_id")
    candidate = base_or_bundle / app_id
    if candidate.is_symlink():
        raise BuildGateError(
            f"Symlinks are not permitted as bundle directory: {candidate}"
        )
    if candidate.is_dir():
        assert_safe_subpath(candidate, base_or_bundle, label="bundle root")
        return candidate
    if "-" in app_id and "/" not in app_id:
        for idx, ch in enumerate(app_id):
            if ch != "-":
                continue
            brand_part = app_id[:idx]
            chan_part = app_id[idx + 1 :]
            if not brand_part or not chan_part:
                continue
            chan_variants = dict.fromkeys(
                (
                    chan_part,
                    chan_part.replace("-", "_"),
                    chan_part.lower(),
                    chan_part.lower().replace("-", "_"),
                )
            )
            for chan_cand in chan_variants:
                two_level = base_or_bundle / brand_part / chan_cand
                if two_level.is_symlink():
                    raise BuildGateError(
                        "Symlinks are not permitted as bundle directory: "
                        f"{two_level}"
                    )
                if two_level.is_dir() and _has_app_marker(two_level):
                    assert_safe_subpath(
                        two_level, base_or_bundle, label="bundle root"
                    )
                    return two_level
    if _has_app_marker(base_or_bundle):
        return base_or_bundle
    return candidate


def _two_way_conflict_lines(
    live_lines: Sequence[str],
    ours_lines: Sequence[str],
) -> tuple[list[str], int]:
    matcher = difflib.SequenceMatcher(
        None, list(live_lines), list(ours_lines), autojunk=False
    )
    merged: list[str] = []
    hunk_count = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            merged.extend(live_lines[i1:i2])
        else:
            merged.append("<<<<<<< live")
            merged.extend(live_lines[i1:i2])
            merged.append("=======")
            merged.extend(ours_lines[j1:j2])
            merged.append(">>>>>>> ours")
            hunk_count += 1
    return merged, hunk_count


def _render_conflict_markers(
    live_text: str,
    ours_text: str,
    base_text: str | None = None,
) -> str:
    """Renders base-aware 3-way conflict markers for edited line hunks."""
    live_lines = live_text.splitlines()
    ours_lines = ours_text.splitlines()

    if not base_text:
        two_way, _ = _two_way_conflict_lines(live_lines, ours_lines)
        return "\n".join(two_way) + "\n"

    base_lines = base_text.splitlines()
    live_edits: list[tuple[int, int, list[str]]] = [
        (b1, b2, live_lines[l1:l2])
        for tag, b1, b2, l1, l2 in difflib.SequenceMatcher(
            None, base_lines, live_lines, autojunk=False
        ).get_opcodes()
        if tag != "equal"
    ]
    ours_edits: list[tuple[int, int, list[str]]] = [
        (b1, b2, ours_lines[o1:o2])
        for tag, b1, b2, o1, o2 in difflib.SequenceMatcher(
            None, base_lines, ours_lines, autojunk=False
        ).get_opcodes()
        if tag != "equal"
    ]

    def _apply_edits(
        slice_start: int,
        slice_end: int,
        edits: Sequence[tuple[int, int, list[str]]],
    ) -> list[str]:
        out: list[str] = []
        cursor = slice_start
        for eb1, eb2, erepl in edits:
            out.extend(base_lines[cursor:eb1])
            out.extend(erepl)
            cursor = eb2
        out.extend(base_lines[cursor:slice_end])
        return out

    merged_lines: list[str] = []
    conflict_hunks = 0
    i = 0
    j = 0
    b_pos = 0

    while i < len(live_edits) or j < len(ours_edits):
        if j >= len(ours_edits) or (
            i < len(live_edits) and live_edits[i][0] <= ours_edits[j][0]
        ):
            c_start, c_end = live_edits[i][0], live_edits[i][1]
        else:
            c_start, c_end = ours_edits[j][0], ours_edits[j][1]

        merged_lines.extend(base_lines[b_pos:c_start])

        cluster_live: list[tuple[int, int, list[str]]] = []
        cluster_ours: list[tuple[int, int, list[str]]] = []
        progressed = True
        while progressed:
            progressed = False
            while i < len(live_edits):
                eb1, eb2, erepl = live_edits[i]
                if eb1 < c_end or (
                    eb1 == c_end
                    and (
                        (not cluster_live and not cluster_ours)
                        or eb1 == eb2
                        or c_start == c_end
                    )
                ):
                    cluster_live.append((eb1, eb2, erepl))
                    c_end = max(c_end, eb2)
                    i += 1
                    progressed = True
                else:
                    break
            while j < len(ours_edits):
                eb1, eb2, erepl = ours_edits[j]
                if eb1 < c_end or (
                    eb1 == c_end
                    and (
                        (not cluster_live and not cluster_ours)
                        or eb1 == eb2
                        or c_start == c_end
                    )
                ):
                    cluster_ours.append((eb1, eb2, erepl))
                    c_end = max(c_end, eb2)
                    j += 1
                    progressed = True
                else:
                    break

        if cluster_live and not cluster_ours:
            merged_lines.extend(_apply_edits(c_start, c_end, cluster_live))
        elif cluster_ours and not cluster_live:
            merged_lines.extend(_apply_edits(c_start, c_end, cluster_ours))
        else:
            l_chunk = _apply_edits(c_start, c_end, cluster_live)
            o_chunk = _apply_edits(c_start, c_end, cluster_ours)
            if l_chunk == o_chunk:
                merged_lines.extend(l_chunk)
            else:
                sub_lines, sub_hunks = _two_way_conflict_lines(l_chunk, o_chunk)
                merged_lines.extend(sub_lines)
                conflict_hunks += sub_hunks

        b_pos = c_end

    merged_lines.extend(base_lines[b_pos:])

    if conflict_hunks > 0:
        return "\n".join(merged_lines) + "\n"

    fallback_lines, _ = _two_way_conflict_lines(live_lines, ours_lines)
    return "\n".join(fallback_lines) + "\n"


def _resolve_resource_selectors(
    selectors: Sequence[str],
    all_ids: Sequence[str],
    flag_name: str,
) -> set[str]:
    all_id_set = set(all_ids)
    resolved: set[str] = set()
    for raw_sel in selectors:
        sel = raw_sel.strip()
        if not sel:
            raise BuildGateError(
                f"Empty resource selector passed to {flag_name}."
            )
        if "/" in sel or sel == "app":
            if sel not in all_id_set:
                raise BuildGateError(
                    f"Unknown resource selector {sel!r} for {flag_name}. "
                    f"Available resources: {sorted(all_id_set)}"
                )
            resolved.add(sel)
        else:
            matches = [
                rid
                for rid in all_ids
                if rid == sel or rid.rsplit("/", 1)[-1] == sel
            ]
            if not matches:
                raise BuildGateError(
                    f"Unknown bare-leaf selector {sel!r} for {flag_name}. "
                    f"Available resources: {sorted(all_id_set)}"
                )
            if len(matches) > 1:
                raise BuildGateError(
                    f"Ambiguous bare-leaf selector {sel!r} for {flag_name} "
                    f"matches multiple resources {matches}; specify full "
                    "'<category>/<name>'."
                )
            resolved.add(matches[0])
    return resolved


def _write_adopted_shared_modules(
    ours_bundle: Path,
    parsed_modules: dict[str, str],
    mod_packages: set[str],
) -> None:
    env_content_root = ours_bundle / "pythonEnvFiles" / "content"
    use_env_content = (env_content_root / "python_lib").is_dir() and not (
        ours_bundle / "python_lib"
    ).is_dir()
    lib_parent = env_content_root if use_env_content else ours_bundle
    for mod_name, mod_src in sorted(parsed_modules.items()):
        if mod_name != "python_lib" and not mod_name.startswith("python_lib."):
            raise BuildGateError(
                "Refusing to write shared module outside python_lib: "
                f"{mod_name!r}"
            )
        rel_str = (
            f"{mod_name.replace('.', '/')}/__init__.py"
            if mod_name in mod_packages
            else f"{mod_name.replace('.', '/')}.py"
        )
        rel_path = Path(rel_str)
        if ".." in rel_path.parts or rel_path.is_absolute():
            raise BuildGateError(
                f"Invalid module path {rel_str!r} in shared bundle."
            )
        target_py = lib_parent / rel_path
        assert_safe_subpath(target_py, lib_parent, label="python_lib")
        target_py.parent.mkdir(parents=True, exist_ok=True)
        with target_py.open("w", encoding="utf-8", newline="") as fh:
            fh.write(mod_src)


def _adopt_live_resource_into_ours(
    res_id: str, live_bundle: Path, ours_bundle: Path
) -> None:
    """Back-ports a normalized live resource into `ours_bundle`."""
    _validate_safe_identifier(res_id, label="resource_id")
    ours_bundle.mkdir(parents=True, exist_ok=True)
    if res_id == "app":
        live_app_cfg = _find_config_file(
            live_bundle, "app", live_bundle, label="live bundle"
        )
        ours_existing_ext: str | None = None
        ours_preserved_cp: dict[str, Any] = {}
        for ext in _CONFIG_EXTS:
            cand = ours_bundle / f"app{ext}"
            if cand.is_symlink():
                raise BuildGateError(
                    f"Symlinks are not permitted in ours bundle: {cand}"
                )
            if cand.is_file():
                assert_safe_subpath(cand, ours_bundle, label="ours bundle")
                if ours_existing_ext is None:
                    ours_existing_ext = ext
                    ours_raw_app = load_mapping_strict(
                        cand, context=f"ours {cand.name}"
                    )
                    for cp_key in APP_CONTROL_PLANE_KEYS:
                        if cp_key in ours_raw_app:
                            ours_preserved_cp[cp_key] = ours_raw_app[cp_key]
                cand.unlink()

        if live_app_cfg is not None:
            raw_app = load_mapping_strict(
                live_app_cfg, context=f"live {live_app_cfg.name}"
            )
            norm_app = normalize_json_obj(raw_app, is_root=True)
            for cp_key, cp_val in ours_preserved_cp.items():
                norm_app[cp_key] = cp_val
            target_ext = (
                ours_existing_ext
                if ours_existing_ext is not None
                else live_app_cfg.suffix.lower()
            )
            write_canonical_mapping(ours_bundle / f"app{target_ext}", norm_app)

        live_global = live_bundle / "global_instruction.txt"
        ours_global = ours_bundle / "global_instruction.txt"
        if live_global.is_symlink():
            raise BuildGateError(
                f"Symlinks are not permitted in live bundle: {live_global}"
            )
        if ours_global.is_symlink():
            raise BuildGateError(
                f"Symlinks are not permitted in ours bundle: {ours_global}"
            )
        if live_global.is_file():
            ours_global.parent.mkdir(parents=True, exist_ok=True)
            ours_global.write_text(
                read_utf8_strict(
                    live_global, context="live global_instruction.txt"
                ).replace("\r\n", "\n"),
                encoding="utf-8",
            )
        elif ours_global.exists():
            ours_global.unlink()
        return

    if "/" not in res_id:
        return

    category, item_name = res_id.split("/", 1)
    ours_cat_dir = ours_bundle / category
    live_cat_dir = live_bundle / category
    ours_item_dir = ours_cat_dir / item_name
    live_item_dir = live_cat_dir / item_name
    assert_safe_subpath(ours_item_dir, ours_bundle, label="ours bundle")

    if ours_item_dir.exists():
        shutil.rmtree(ours_item_dir)

    ours_flat_ext: str | None = None
    for ext in _CONFIG_EXTS:
        ours_flat = ours_cat_dir / f"{item_name}{ext}"
        assert_safe_subpath(ours_flat, ours_bundle, label="ours bundle")
        if ours_flat.is_file():
            if ours_flat_ext is None:
                ours_flat_ext = ext
            ours_flat.unlink()

    if live_item_dir.is_symlink():
        raise BuildGateError(
            f"Symlinks are not permitted in live bundle: {live_item_dir}"
        )
    if live_item_dir.is_dir():
        assert_safe_subpath(live_item_dir, live_bundle, label="live bundle")
        ours_item_dir.mkdir(parents=True, exist_ok=True)
        for cur_root, dirs, files in os.walk(live_item_dir, followlinks=False):
            dirs[:] = sorted(
                d for d in dirs if d != "__pycache__" and not d.startswith(".")
            )
            for d in dirs:
                assert_safe_subpath(
                    Path(cur_root) / d, live_bundle, label="live bundle"
                )
            for fname in sorted(files):
                if fname.startswith("."):
                    continue
                src_file = Path(cur_root) / fname
                assert_safe_subpath(src_file, live_bundle, label="live bundle")
                rel = src_file.relative_to(live_item_dir)
                dst_file = ours_item_dir / rel
                assert_safe_subpath(dst_file, ours_bundle, label="ours bundle")
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                suffix = src_file.suffix.lower()
                if suffix == ".json":
                    is_root_json = len(rel.parts) == 1
                    if is_root_json:
                        raw_obj: Any = load_json_dict_strict(
                            src_file, context=f"live {res_id} JSON"
                        )
                    else:
                        try:
                            raw_obj = json.loads(
                                read_utf8_strict(src_file, context="live JSON")
                            )
                        except ValueError as exc:
                            raise BuildGateError(
                                f"Corrupt JSON in {src_file}: {exc}"
                            ) from exc
                    norm_obj = normalize_json_obj(raw_obj, is_root=is_root_json)
                    if isinstance(norm_obj, dict):
                        write_canonical_json(dst_file, norm_obj)
                    else:
                        dst_file.write_text(
                            json.dumps(
                                norm_obj,
                                indent=2,
                                sort_keys=True,
                                ensure_ascii=False,
                            )
                            + "\n",
                            encoding="utf-8",
                        )
                elif (
                    len(rel.parts) == 1
                    and suffix in {".yaml", ".yml"}
                    and src_file.name not in _OPENAPI_SCHEMA_FILENAMES
                ):
                    raw_yaml = load_mapping_strict(
                        src_file, context=f"live {res_id} YAML"
                    )
                    norm_yaml = normalize_json_obj(raw_yaml, is_root=True)
                    write_canonical_mapping(dst_file, norm_yaml)
                elif suffix == ".py":
                    raw_py = read_utf8_strict(
                        src_file, context=f"live {res_id} Python file"
                    )
                    try:
                        clean_py, _, parsed_modules, mod_packages = (
                            inspect_entrypoint_bundle(
                                raw_py, src_file, app_dir=live_bundle
                            )
                        )
                    except PythonBundlerError as exc:
                        raise BuildGateError(str(exc)) from exc
                    dst_file.write_text(
                        clean_py.replace("\r\n", "\n"),
                        encoding="utf-8",
                    )
                    if parsed_modules:
                        _write_adopted_shared_modules(
                            ours_bundle, parsed_modules, mod_packages
                        )
                else:
                    try:
                        raw_bytes = src_file.read_bytes()
                    except OSError as exc:
                        raise BuildGateError(
                            f"Failed to read live {res_id} file "
                            f"{src_file}: {exc}"
                        ) from exc
                    try:
                        text = raw_bytes.decode("utf-8")
                    except UnicodeDecodeError:
                        dst_file.write_bytes(raw_bytes)
                    else:
                        dst_file.write_text(
                            text.replace("\r\n", "\n"),
                            encoding="utf-8",
                        )
    elif live_cat_dir.is_symlink():
        raise BuildGateError(
            f"Symlinks are not permitted in live bundle: {live_cat_dir}"
        )
    elif live_cat_dir.is_dir():
        live_flat_cfg = _find_config_file(
            live_cat_dir, item_name, live_bundle, label="live bundle"
        )
        if live_flat_cfg is not None:
            raw_obj = load_mapping_strict(
                live_flat_cfg, context=f"live {res_id} config"
            )
            norm_obj = normalize_json_obj(raw_obj, is_root=True)
            target_ext = (
                ours_flat_ext
                if ours_flat_ext is not None
                else live_flat_cfg.suffix.lower()
            )
            dst_flat = ours_cat_dir / f"{item_name}{target_ext}"
            assert_safe_subpath(dst_flat, ours_bundle, label="ours bundle")
            write_canonical_mapping(dst_flat, norm_obj)


def _validate_git_mirror_dir(mirror_dir: Path) -> None:
    """Refuse to overwrite dirty git repos or non-empty non-git directories."""
    if (mirror_dir / ".git").exists():
        env = dict(os.environ)
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        try:
            proc = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(mirror_dir),
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            raise BuildGateError(
                f"Failed to inspect git_mirror repository {mirror_dir}: "
                f"{stderr}"
            ) from exc
        if proc.stdout.strip():
            raise BuildGateError(
                "Refusing to write git_mirror into a dirty git repository "
                f"with uncommitted or untracked changes: {mirror_dir}"
            )
    elif mirror_dir.exists():
        if not mirror_dir.is_dir():
            raise BuildGateError(
                f"git_mirror path exists and is not a directory: {mirror_dir}"
            )
        if any(mirror_dir.iterdir()):
            raise BuildGateError(
                "Refusing to initialize git_mirror in a non-empty non-git "
                f"directory: {mirror_dir}"
            )


def _commit_live_git_mirror(
    mirror_dir: Path,
    app_id: str,
    live_resources: dict[str, NormalizedResource],
) -> str:
    _validate_safe_identifier(app_id, label="app_id")
    _validate_git_mirror_dir(mirror_dir)
    mirror_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["GIT_CONFIG_NOSYSTEM"] = "1"

    def _git(*args: str) -> str:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(mirror_dir),
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            raise BuildGateError(
                f"Git mirror command failed (`git {' '.join(args)}`): {stderr}"
            ) from exc
        return proc.stdout.strip()

    if not (mirror_dir / ".git").exists():
        _git("init")

    _git("config", "user.name", "cxas-drift-mirror")
    _git("config", "user.email", "noreply@example.invalid")
    _git("config", "commit.gpgsign", "false")

    branch_exists = (
        subprocess.run(
            [
                "git",
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/heads/live/{app_id}",
            ],
            cwd=str(mirror_dir),
            env=env,
            check=False,
        ).returncode
        == 0
    )
    if branch_exists:
        _git("checkout", "-f", f"live/{app_id}")
    else:
        _git("checkout", "--orphan", f"live/{app_id}")
        _git("rm", "-rf", "--ignore-unmatch", ".")

    for child in mirror_dir.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()

    for res_id, norm_res in sorted(live_resources.items()):
        _validate_safe_identifier(res_id, label="resource_id")
        out_file = mirror_dir / f"{res_id}.json"
        assert_safe_subpath(out_file, mirror_dir, label="git mirror")
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(norm_res.normalized, encoding="utf-8")

    _git("add", "-A")
    _git("commit", "--allow-empty", "-m", f"snapshot live/{app_id}")
    return _git("rev-parse", "HEAD")


def _restore_bundle_backup(backup_bundle: Path, ours_bundle: Path) -> None:
    """Restore ``ours_bundle`` from ``backup_bundle`` after a failed adopt."""
    if not backup_bundle.is_dir():
        return
    if ours_bundle.exists():
        shutil.rmtree(ours_bundle)
    shutil.copytree(backup_bundle, ours_bundle)


def check_drift(
    app_id: str,
    dist_dir: Path | str,
    live_dir: Path | str,
    *,
    lockfile_path: Path | str | None = None,
    lockfile: Path | str | None = None,
    conflicts_dir: Path | str | None = None,
    accept_live: Sequence[str] = (),
    force_ours: Sequence[str] = (),
    git_mirror: Path | str | None = None,
    git_mirror_dir: Path | str | None = None,
) -> DriftReport:
    """Runs per-resource 3-way (`base` vs. `ours` vs. `live`) drift check."""
    _validate_safe_identifier(app_id, label="app_id")
    raw_dist = Path(dist_dir)
    raw_live = Path(live_dir)
    if raw_dist.is_symlink():
        raise BuildGateError(
            f"Symlinks are not permitted as dist directory: {raw_dist}"
        )
    if raw_live.is_symlink():
        raise BuildGateError(
            f"Symlinks are not permitted as live directory: {raw_live}"
        )
    dist_root = raw_dist.resolve()
    ours_bundle = _resolve_app_bundle_dir(dist_root, app_id)
    live_bundle = _resolve_app_bundle_dir(raw_live.resolve(), app_id)

    eff_lock = lockfile_path if lockfile_path is not None else lockfile
    if eff_lock is not None:
        raw_lock = Path(eff_lock)
        if raw_lock.is_symlink():
            raise BuildGateError(
                f"Symlinks are not permitted as lockfile: {raw_lock}"
            )
        resolved_lock = raw_lock.resolve()
        if not resolved_lock.is_file():
            raise BuildGateError(
                f"Explicit lockfile does not exist: {resolved_lock}"
            )
    else:
        default_lock = ours_bundle.parent / f"{ours_bundle.name}.lock.json"
        lock_candidates = [
            default_lock,
            dist_root / f"{app_id}.lock.json",
            ours_bundle.parent / f"{app_id.replace('/', '-')}.lock.json",
        ]
        resolved_lock = next(
            (
                cand
                for cand in lock_candidates
                if cand.is_symlink() or cand.is_file()
            ),
            default_lock,
        )
        if resolved_lock.is_symlink():
            raise BuildGateError(
                f"Symlinks are not permitted as lockfile: {resolved_lock}"
            )
    had_prior_lockfile = resolved_lock.is_file()

    if conflicts_dir is not None:
        raw_conflicts = Path(conflicts_dir)
        if raw_conflicts.is_symlink():
            raise BuildGateError(
                f"Symlinks are not permitted as conflicts_dir: {raw_conflicts}"
            )
        eff_conflicts_dir = raw_conflicts.resolve()
    else:
        eff_conflicts_dir = (
            ours_bundle.parent / "conflicts" / app_id
        ).resolve()

    if _paths_overlap(eff_conflicts_dir, ours_bundle) or _paths_overlap(
        eff_conflicts_dir, live_bundle
    ):
        raise BuildGateError(
            f"conflicts_dir ({eff_conflicts_dir}) must not overlap with "
            "ours or live app bundle directories."
        )

    eff_mirror = git_mirror if git_mirror is not None else git_mirror_dir
    resolved_mirror: Path | None = None
    if eff_mirror is not None:
        raw_mirror = Path(eff_mirror)
        if raw_mirror.is_symlink():
            raise BuildGateError(
                f"Symlinks are not permitted as git_mirror: {raw_mirror}"
            )
        resolved_mirror = raw_mirror.resolve()
        if _paths_overlap(resolved_mirror, ours_bundle) or _paths_overlap(
            resolved_mirror, live_bundle
        ):
            raise BuildGateError(
                f"git_mirror ({resolved_mirror}) must not overlap with "
                "ours or live app bundle directories."
            )
        _validate_git_mirror_dir(resolved_mirror)

    base_map = _load_lockfile_resources(resolved_lock)
    ours_map = extract_normalized_resources(ours_bundle)
    live_map = extract_normalized_resources(live_bundle)

    all_ids = sorted(set(base_map) | set(ours_map) | set(live_map))
    accept_resolved = _resolve_resource_selectors(
        accept_live, all_ids, "--accept-live"
    )
    force_resolved = _resolve_resource_selectors(
        force_ours, all_ids, "--force-ours"
    )
    overlap = sorted(accept_resolved & force_resolved)
    if overlap:
        raise BuildGateError(
            f"Resource(s) {overlap} cannot specify both --accept-live and "
            "--force-ours."
        )

    drifts: list[ResourceDrift] = []
    accepted_ids: set[str] = set()
    lock_updated = False

    with tempfile.TemporaryDirectory() as tmp_backup_root:
        backup_bundle = Path(tmp_backup_root) / "ours_backup"
        if accept_resolved and ours_bundle.is_dir():
            shutil.copytree(ours_bundle, backup_bundle)

        try:
            for res_id in all_ids:
                in_base = res_id in base_map
                in_ours = res_id in ours_map
                in_live = res_id in live_map

                base_sha = base_map[res_id].sha256 if in_base else None
                ours_sha = ours_map[res_id].sha256 if in_ours else None
                live_sha = live_map[res_id].sha256 if in_live else None

                if (in_live and in_ours and live_sha == ours_sha) or (
                    not in_live and not in_ours
                ):
                    status: DriftStatus = "CLEAN"
                elif in_base and in_live and live_sha == base_sha:
                    if base_map[res_id].accepted_from_live and (
                        not in_ours or ours_sha != base_sha
                    ):
                        status = "DRIFT"
                    else:
                        status = "CLEAN"
                elif in_ours and not in_live and not in_base:
                    status = "CLEAN"
                elif (
                    in_base
                    and in_ours
                    and ours_sha == base_sha
                    and (not in_live or live_sha != base_sha)
                ):
                    status = "DRIFT"
                elif in_live and not in_ours and not in_base:
                    status = "ORPHAN"
                else:
                    status = "CONFLICT"

                conflict_file: Path | None = None
                if status == "CONFLICT":
                    _validate_safe_identifier(res_id, label="resource_id")
                    conflict_file = eff_conflicts_dir / f"{res_id}.diff"
                    assert_safe_subpath(
                        conflict_file, eff_conflicts_dir, label="conflicts_dir"
                    )
                    conflict_file.parent.mkdir(parents=True, exist_ok=True)
                    live_text = live_map[res_id].normalized if in_live else ""
                    ours_text = ours_map[res_id].normalized if in_ours else ""
                    base_text = base_map[res_id].normalized if in_base else None
                    conflict_file.write_text(
                        _render_conflict_markers(
                            live_text, ours_text, base_text=base_text
                        ),
                        encoding="utf-8",
                    )

                resolved_by: ResolutionMode | None = None
                if status != "CLEAN":
                    if res_id in accept_resolved:
                        resolved_by = "accept-live"
                        _adopt_live_resource_into_ours(
                            res_id, live_bundle, ours_bundle
                        )
                        if in_live:
                            accepted_res = NormalizedResource(
                                resource_id=res_id,
                                sha256=live_map[res_id].sha256,
                                normalized=live_map[res_id].normalized,
                                accepted_from_live=True,
                            )
                            ours_map[res_id] = accepted_res
                            base_map[res_id] = accepted_res
                        else:
                            ours_map.pop(res_id, None)
                            base_map.pop(res_id, None)
                        accepted_ids.add(res_id)
                        lock_updated = True
                    elif res_id in force_resolved:
                        resolved_by = "force-ours"

                drifts.append(
                    ResourceDrift(
                        resource_id=res_id,
                        status=status,
                        base_sha256=base_sha,
                        ours_sha256=ours_sha,
                        live_sha256=live_sha,
                        resolved_by=resolved_by,
                        conflict_path=conflict_file,
                    )
                )

            if lock_updated:
                refreshed_ours = extract_normalized_resources(ours_bundle)
                ours_map = dict(refreshed_ours)
                for acc_id in accepted_ids:
                    if acc_id in refreshed_ours:
                        accepted_res = NormalizedResource(
                            resource_id=acc_id,
                            sha256=refreshed_ours[acc_id].sha256,
                            normalized=refreshed_ours[acc_id].normalized,
                            accepted_from_live=True,
                        )
                        ours_map[acc_id] = accepted_res
                        base_map[acc_id] = accepted_res
                    else:
                        ours_map.pop(acc_id, None)
                        base_map.pop(acc_id, None)

                if not had_prior_lockfile:
                    for r_id, r_val in ours_map.items():
                        if r_id not in accepted_ids:
                            base_map.setdefault(r_id, r_val)
                payload: dict[str, Any] = {
                    "app_id": app_id,
                    "resources": {
                        r_id: (
                            {
                                "accepted_from_live": True,
                                "normalized": r.normalized,
                                "sha256": r.sha256,
                            }
                            if r.accepted_from_live
                            else {
                                "normalized": r.normalized,
                                "sha256": r.sha256,
                            }
                        )
                        for r_id, r in sorted(base_map.items())
                    },
                    "schema_version": "1.0",
                }
                write_canonical_json(resolved_lock, payload)
        except Exception:
            if accept_resolved and backup_bundle.is_dir():
                _restore_bundle_backup(backup_bundle, ours_bundle)
            raise

    mirror_commit: str | None = None
    if resolved_mirror is not None:
        mirror_commit = _commit_live_git_mirror(
            resolved_mirror, app_id, live_map
        )

    return DriftReport(
        app_id=app_id,
        resources=drifts,
        git_mirror_commit=mirror_commit,
    )


def enforce_pre_push_gate(
    app_dir: Path | str,
    *,
    app_id: str | None = None,
    live_dir: Path | str | None = None,
    lockfile_path: Path | str | None = None,
    conflicts_dir: Path | str | None = None,
    force_ours: Sequence[str] = (),
) -> DriftReport | None:
    """Enforces the optional 3-way drift gate prior to push."""
    raw_input = Path(app_dir)
    if raw_input.is_symlink():
        raise BuildGateError(
            f"Symlinks are not permitted as app directory: {raw_input}"
        )
    raw_dir = raw_input.resolve()
    eff_app_id = app_id or raw_dir.name
    bundle_dir = _resolve_app_bundle_dir(raw_dir, eff_app_id)

    if live_dir is not None:
        report = check_drift(
            eff_app_id,
            bundle_dir,
            live_dir,
            lockfile_path=lockfile_path,
            conflicts_dir=conflicts_dir,
            force_ours=force_ours,
        )
        if report.has_unresolved:
            dirty = [
                f"{r.resource_id}={r.status}"
                for r in report.resources
                if r.status != "CLEAN" and r.resolved_by is None
            ]
            raise DriftGateError(
                f"Push refused for {eff_app_id}: unresolved live "
                f"drift/conflict/orphan resources: {', '.join(dirty)}",
                drift_report=report,
            )
        return report

    return None


__all__ = [
    "DriftGateError",
    "DriftReport",
    "NormalizedResource",
    "ResourceDrift",
    "check_drift",
    "enforce_pre_push_gate",
    "extract_normalized_resources",
    "normalize_json_obj",
    "write_lockfile",
]
