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
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from cxas_scrapi.build.blueprint_schemas import (
    BlueprintCompilerError,
    DriftGateError,
    DriftReport,
    FirewallError,
    NormalizedResource,
    ResourceDrift,
    load_json_dict_strict,
    read_utf8_strict,
    write_canonical_json,
)
from cxas_scrapi.build.firewall import run_firewall
from cxas_scrapi.core.python_bundler import (
    PythonBundlerError,
    inspect_entrypoint_bundle,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from cxas_scrapi.build.firewall import FirewallProfile

VOLATILE_JSON_KEYS: frozenset[str] = frozenset(
    {"uuid", "uid", "etag", "createTime", "updateTime"}
)

DriftStatus = Literal["CLEAN", "DRIFT", "ORPHAN", "CONFLICT"]
ResolutionMode = Literal["accept-live", "force-ours"]


def normalize_json_obj(obj: Any, *, is_root: bool = True) -> Any:
    """Strips volatile CES control-plane envelope fields recursively."""
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
            cleaned[k] = normalize_json_obj(v, is_root=False)
        return cleaned
    if isinstance(obj, list):
        return [normalize_json_obj(item, is_root=False) for item in obj]
    return obj


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
    root = Path(app_dir).resolve()
    if not root.is_dir():
        raise BlueprintCompilerError(
            f"App export directory does not exist: {root}"
        )

    resources: dict[str, NormalizedResource] = {}

    app_json_path = root / "app.json"
    global_instr_path = root / "global_instruction.txt"
    if app_json_path.is_file() or global_instr_path.is_file():
        raw_app = (
            load_json_dict_strict(app_json_path, context="app.json")
            if app_json_path.is_file()
            else {}
        )
        norm_app = normalize_json_obj(raw_app, is_root=True)
        if global_instr_path.is_file():
            norm_app["_globalInstructionLines"] = (
                read_utf8_strict(
                    global_instr_path, context="global_instruction.txt"
                )
                .replace("\r\n", "\n")
                .splitlines()
            )
        resources["app"] = _make_normalized_resource("app", norm_app)

    for category in ("agents", "tools", "guardrails", "toolsets"):
        cat_dir = root / category
        if not cat_dir.is_dir():
            continue
        for entry in sorted(cat_dir.iterdir()):
            if entry.is_dir():
                dir_name = entry.name
                res_id = f"{category}/{dir_name}"
                primary_json = entry / f"{dir_name}.json"
                if not primary_json.is_file():
                    candidates = sorted(entry.glob("*.json"))
                    primary_json = candidates[0] if candidates else primary_json
                raw_obj = (
                    load_json_dict_strict(
                        primary_json, context=f"{res_id} JSON"
                    )
                    if primary_json.is_file()
                    else {}
                )
                norm_obj = normalize_json_obj(raw_obj, is_root=True)

                if category == "agents":
                    instr_file = entry / "instruction.txt"
                    if instr_file.is_file():
                        norm_obj["_instructionLines"] = (
                            read_utf8_strict(
                                instr_file, context=f"{res_id} instruction.txt"
                            )
                            .replace("\r\n", "\n")
                            .splitlines()
                        )

                if category in ("tools", "toolsets"):
                    oa_file = entry / "open_api_tool" / "open_api_schema.yaml"
                    if oa_file.is_file():
                        norm_obj["_openApiSchemaLines"] = (
                            read_utf8_strict(
                                oa_file, context=f"{res_id} OpenAPI schema"
                            )
                            .replace("\r\n", "\n")
                            .splitlines()
                        )

                py_files = sorted(entry.rglob("*.py"))
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
                            raise BlueprintCompilerError(str(exc)) from exc
                        extra_files[rel_posix] = clean_py.replace(
                            "\r\n", "\n"
                        ).splitlines()
                        if bundle_digest is not None:
                            bundle_digests[rel_posix] = bundle_digest
                    norm_obj["_extraFiles"] = extra_files
                    if bundle_digests:
                        norm_obj["_sharedBundleDigests"] = bundle_digests

                resources[res_id] = _make_normalized_resource(res_id, norm_obj)

            elif entry.is_file() and entry.suffix == ".json":
                res_id = f"{category}/{entry.stem}"
                raw_obj = load_json_dict_strict(entry, context=f"{res_id} JSON")
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
    bundle_dir = Path(app_dir).resolve()
    target_lock = (
        Path(lockfile_path).resolve()
        if lockfile_path is not None
        else bundle_dir.parent / f"{bundle_dir.name}.lock.json"
    )
    resources = extract_normalized_resources(bundle_dir)
    payload: dict[str, Any] = {
        "app_id": app_id if app_id is not None else bundle_dir.name,
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
    if lockfile_path is None or not lockfile_path.is_file():
        return {}
    data = load_json_dict_strict(lockfile_path, context="lockfile")
    raw_res = data.get("resources", {})
    if not isinstance(raw_res, dict):
        raise BlueprintCompilerError(
            f"Invalid lockfile {lockfile_path}: 'resources' must be an object."
        )
    loaded: dict[str, NormalizedResource] = {}
    for res_id, entry in raw_res.items():
        if not isinstance(entry, dict) or "sha256" not in entry:
            raise BlueprintCompilerError(
                f"Invalid lockfile {lockfile_path}: resource {res_id!r} "
                "must be an object containing 'sha256'."
            )
        norm_str = str(entry.get("normalized", ""))
        loaded[res_id] = NormalizedResource(
            resource_id=res_id,
            sha256=str(entry["sha256"]),
            normalized=norm_str,
            accepted_from_live=bool(entry.get("accepted_from_live", False)),
        )
    return loaded


def _resolve_app_bundle_dir(base_or_bundle: Path, app_id: str) -> Path:
    candidate = base_or_bundle / app_id
    if candidate.is_dir():
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
                if two_level.is_dir() and (
                    (two_level / "app.json").is_file()
                    or (two_level / "agents").is_dir()
                ):
                    return two_level
    if (base_or_bundle / "app.json").is_file() or (
        base_or_bundle / "agents"
    ).is_dir():
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
            merged.append(">>>>>>> blueprint")
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
            raise BlueprintCompilerError(
                f"Empty resource selector passed to {flag_name}."
            )
        if "/" in sel or sel == "app":
            if sel not in all_id_set:
                raise BlueprintCompilerError(
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
                raise BlueprintCompilerError(
                    f"Unknown bare-leaf selector {sel!r} for {flag_name}. "
                    f"Available resources: {sorted(all_id_set)}"
                )
            if len(matches) > 1:
                raise BlueprintCompilerError(
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
            raise BlueprintCompilerError(
                "Refusing to write shared module outside python_lib: "
                f"{mod_name!r}"
            )
        rel_str = (
            f"{mod_name.replace('.', '/')}/__init__.py"
            if mod_name in mod_packages
            else f"{mod_name.replace('.', '/')}.py"
        )
        rel_path = Path(rel_str)
        if ".." in rel_path.parts:
            raise BlueprintCompilerError(
                f"Invalid module path {rel_str!r} in shared bundle."
            )
        target_py = lib_parent / rel_path
        target_py.parent.mkdir(parents=True, exist_ok=True)
        with target_py.open("w", encoding="utf-8", newline="") as fh:
            fh.write(mod_src)


def _adopt_live_resource_into_ours(
    res_id: str, live_bundle: Path, ours_bundle: Path
) -> None:
    """Back-ports a normalized live resource into `ours_bundle`."""
    ours_bundle.mkdir(parents=True, exist_ok=True)
    if res_id == "app":
        live_app_json = live_bundle / "app.json"
        ours_app_json = ours_bundle / "app.json"
        if live_app_json.is_file():
            raw_app = load_json_dict_strict(
                live_app_json, context="live app.json"
            )
            norm_app = normalize_json_obj(raw_app, is_root=True)
            write_canonical_json(ours_app_json, norm_app)
        elif ours_app_json.exists():
            ours_app_json.unlink()

        live_global = live_bundle / "global_instruction.txt"
        ours_global = ours_bundle / "global_instruction.txt"
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
    ours_item_dir = ours_bundle / category / item_name
    ours_flat_json = ours_bundle / category / f"{item_name}.json"
    live_item_dir = live_bundle / category / item_name
    live_flat_json = live_bundle / category / f"{item_name}.json"

    if ours_item_dir.exists():
        shutil.rmtree(ours_item_dir)
    if ours_flat_json.exists():
        ours_flat_json.unlink()

    if live_item_dir.is_dir():
        ours_item_dir.mkdir(parents=True, exist_ok=True)
        for src_file in sorted(live_item_dir.rglob("*")):
            if not src_file.is_file():
                continue
            rel = src_file.relative_to(live_item_dir)
            dst_file = ours_item_dir / rel
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            if src_file.suffix == ".json":
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
                        raise BlueprintCompilerError(
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
            elif src_file.suffix == ".py":
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
                    raise BlueprintCompilerError(str(exc)) from exc
                dst_file.write_text(
                    clean_py.replace("\r\n", "\n"),
                    encoding="utf-8",
                )
                if parsed_modules:
                    _write_adopted_shared_modules(
                        ours_bundle, parsed_modules, mod_packages
                    )
            else:
                dst_file.write_text(
                    read_utf8_strict(
                        src_file, context=f"live {res_id} file"
                    ).replace("\r\n", "\n"),
                    encoding="utf-8",
                )
    elif live_flat_json.is_file():
        ours_item_dir.mkdir(parents=True, exist_ok=True)
        raw_obj = load_json_dict_strict(
            live_flat_json, context=f"live {res_id} JSON"
        )
        norm_obj = normalize_json_obj(raw_obj, is_root=True)
        write_canonical_json(ours_item_dir / f"{item_name}.json", norm_obj)


def _commit_live_git_mirror(
    mirror_dir: Path,
    app_id: str,
    live_resources: dict[str, NormalizedResource],
) -> str:
    mirror_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["GIT_CONFIG_NOSYSTEM"] = "1"

    def _git(*args: str) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(mirror_dir),
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
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
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()

    for res_id, norm_res in sorted(live_resources.items()):
        out_file = mirror_dir / f"{res_id}.json"
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(norm_res.normalized, encoding="utf-8")

    _git("add", "-A")
    _git("commit", "--allow-empty", "-m", f"snapshot live/{app_id}")
    return _git("rev-parse", "HEAD")


def check_drift(
    app_id: str,
    dist_dir: Path | str,
    live_dir: Path | str,
    *,
    lockfile_path: Path | str | None = None,
    lockfile: Path | str | None = None,
    blueprint_dir: Path | str | None = None,
    conflicts_dir: Path | str | None = None,
    accept_live: Sequence[str] = (),
    force_ours: Sequence[str] = (),
    git_mirror: Path | str | None = None,
    git_mirror_dir: Path | str | None = None,
    firewall_profile: FirewallProfile = "strict",
) -> DriftReport:
    """Runs per-resource 3-way (`base` vs. `ours` vs. `live`) drift check."""
    dist_root = Path(dist_dir).resolve()
    ours_bundle = _resolve_app_bundle_dir(dist_root, app_id)
    live_bundle = _resolve_app_bundle_dir(Path(live_dir).resolve(), app_id)

    eff_lock = lockfile_path if lockfile_path is not None else lockfile
    if eff_lock is not None:
        resolved_lock = Path(eff_lock).resolve()
        if not resolved_lock.is_file():
            raise BlueprintCompilerError(
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
            (cand for cand in lock_candidates if cand.is_file()),
            default_lock,
        )
    had_prior_lockfile = resolved_lock.is_file()

    eff_conflicts_dir = (
        Path(conflicts_dir).resolve()
        if conflicts_dir is not None
        else ours_bundle.parent / "conflicts" / app_id
    )

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
        raise BlueprintCompilerError(
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
                conflict_file = eff_conflicts_dir / f"{res_id}.diff"
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
            fw_violations = run_firewall(
                ours_bundle,
                blueprint_dir=blueprint_dir,
                profile=firewall_profile,
            )
            if fw_violations:
                if ours_bundle.exists():
                    shutil.rmtree(ours_bundle)
                if backup_bundle.is_dir():
                    shutil.copytree(backup_bundle, ours_bundle)
                summary = "; ".join(str(v) for v in fw_violations)
                raise FirewallError(
                    f"Refusing --accept-live for {app_id}: accepted live "
                    "state violates static firewall "
                    f"({len(fw_violations)} violation(s)): {summary}",
                    violations=fw_violations,
                )

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

    eff_mirror = git_mirror if git_mirror is not None else git_mirror_dir
    mirror_commit: str | None = None
    if eff_mirror is not None:
        mirror_commit = _commit_live_git_mirror(
            Path(eff_mirror).resolve(), app_id, live_map
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
    blueprint_dir: Path | str | None = None,
    conflicts_dir: Path | str | None = None,
    force_ours: Sequence[str] = (),
    run_static_firewall: bool = True,
    firewall_profile: FirewallProfile = "strict",
) -> DriftReport | None:
    """Enforces static firewall and optional 3-way drift gate prior to push."""
    if firewall_profile not in ("strict", "standard"):
        raise BlueprintCompilerError(
            f"Unsupported firewall profile {firewall_profile!r}; "
            "expected 'strict' or 'standard'."
        )
    raw_dir = Path(app_dir).resolve()
    eff_app_id = app_id or raw_dir.name
    bundle_dir = _resolve_app_bundle_dir(raw_dir, eff_app_id)

    if run_static_firewall:
        violations = run_firewall(
            bundle_dir,
            blueprint_dir=blueprint_dir,
            profile=firewall_profile,
        )
        if violations:
            summary = "; ".join(str(v) for v in violations)
            raise DriftGateError(
                f"Push refused for {eff_app_id}: {len(violations)} firewall "
                f"violation(s): {summary}",
                violations=violations,
            )

    if live_dir is not None:
        report = check_drift(
            eff_app_id,
            bundle_dir,
            live_dir,
            lockfile_path=lockfile_path,
            blueprint_dir=blueprint_dir,
            conflicts_dir=conflicts_dir,
            force_ours=force_ours,
            firewall_profile=firewall_profile,
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
