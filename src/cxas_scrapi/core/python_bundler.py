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

"""
Shared Python bundler and pythonEnvFiles.yaml synthesis.
"""

import ast
import hashlib
import os
import re
import shutil
from pathlib import Path

import yaml

MAX_BUNDLE_BYTES_PER_FILE = 64 * 1024
MAX_TOTAL_ARCHIVE_BYTES = 32 * 1024 * 1024


class PythonBundlerError(Exception):
    pass


class BundleSizeError(PythonBundlerError):
    pass


class BundleDigestMismatchError(PythonBundlerError):
    pass


def synthesize_python_env_files(
    app_dir: Path, dest_dir: Path | None = None
) -> Path | None:
    """
    Synthesizes pythonEnvFiles/content/ and pythonEnvFiles.yaml from
    python_lib/.
    """
    if dest_dir is None:
        dest_dir = app_dir

    python_lib = app_dir / "python_lib"
    if not python_lib.is_dir():
        content_lib = app_dir / "pythonEnvFiles" / "content" / "python_lib"
        if not content_lib.is_dir():
            return None
        python_lib = content_lib

    python_env_dir = dest_dir / "pythonEnvFiles"
    content_dir = python_env_dir / "content"
    dest_lib = content_dir / "python_lib"

    if dest_lib.resolve() != python_lib.resolve():
        dest_lib.mkdir(parents=True, exist_ok=True)
        for root, dirs, files in os.walk(python_lib):
            dirs[:] = [
                d for d in dirs if d != "__pycache__" and not d.startswith(".")
            ]
            for file in files:
                if file.endswith(".py") and not file.startswith("."):
                    src = Path(root) / file
                    rel = src.relative_to(python_lib)
                    dst = dest_lib / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)

    manifest_entries = []
    for root, _dirs, files in os.walk(dest_lib):
        for file in sorted(files):
            if file.endswith(".py"):
                path = Path(root) / file
                rel_content = path.relative_to(content_dir)
                rel_env = path.relative_to(python_env_dir)
                raw_id = (
                    str(rel_content)
                    .replace(os.sep, "-")
                    .replace(".py", "")
                    .replace("__init__", "init")
                    .replace("_", "-")
                )
                clean_id = re.sub(r"[^a-zA-Z0-9-_]", "-", raw_id).strip("-_")
                if len(clean_id) < 5:
                    clean_id = f"pyenv-{clean_id}"
                clean_id = clean_id[:36]
                manifest_entries.append(
                    {
                        "name": clean_id,
                        "filePath": str(rel_env).replace(os.sep, "/"),
                    }
                )

    manifest_entries.sort(key=lambda item: item["filePath"])
    yaml_path = python_env_dir / "pythonEnvFiles.yaml"
    with yaml_path.open("w", encoding="utf-8") as f:
        yaml.dump(
            {"pythonEnvFiles": manifest_entries},
            f,
            default_flow_style=False,
            sort_keys=False,
        )

    return python_env_dir


def _get_module_dependencies(module_name: str, source: str) -> set[str]:
    deps = set()
    try:
        tree = ast.parse(source)
    except Exception:
        return frozenset()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                deps.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.level == 0:
                deps.add(node.module)
            else:
                parts = module_name.split(".")
                base = parts[: -node.level]
                if base:
                    deps.add(".".join([*base, node.module]))
    return deps


def _topo_sort_modules(modules: dict[str, str]) -> list[tuple[str, str]]:
    deps = {
        mod: _get_module_dependencies(mod, src) for mod, src in modules.items()
    }
    visited = set()
    temp = set()
    order = []

    def visit(n: str) -> None:
        if n in temp:
            return
        if n not in visited:
            temp.add(n)
            for d in deps.get(n, set()):
                if d in modules:
                    visit(d)
                parts = d.split(".")
                for i in range(1, len(parts)):
                    parent = ".".join(parts[:i])
                    if parent in modules:
                        visit(parent)

            parts = n.split(".")
            for i in range(1, len(parts)):
                parent = ".".join(parts[:i])
                if parent in modules:
                    visit(parent)

            temp.remove(n)
            visited.add(n)
            order.append(n)

    for mod in sorted(modules.keys()):
        if mod not in visited:
            visit(mod)

    return [(m, modules[m]) for m in order]


def bundle_shared_python(
    app_dir: Path,
    *,
    source_root: Path | None = None,
    max_bundle_bytes: int = MAX_BUNDLE_BYTES_PER_FILE,
    native_python_env: bool = False,
) -> dict[str, int]:
    """
    Tree-shaking AST marker bundler.
    """
    if source_root is None:
        source_root = app_dir

    search_paths = []
    if (source_root / "pythonEnvFiles" / "content").is_dir():
        search_paths.append(source_root / "pythonEnvFiles" / "content")
    if (source_root / "python_lib").is_dir():
        search_paths.append(source_root)

    available_modules = {}
    for sp in search_paths:
        plib = sp / "python_lib"
        if not plib.is_dir():
            continue
        for root, dirs, files in os.walk(plib):
            dirs[:] = [
                d for d in dirs if d != "__pycache__" and not d.startswith(".")
            ]
            for file in files:
                if file.endswith(".py") and not file.startswith("."):
                    path = Path(root) / file
                    rel = path.relative_to(sp)
                    mod_name = str(rel).replace(os.sep, ".")[:-3]
                    if mod_name.endswith(".__init__"):
                        mod_name = mod_name[:-9]
                    available_modules[mod_name] = path.read_text(
                        encoding="utf-8"
                    )

    entrypoints = []
    agents_dir = app_dir / "agents"
    if agents_dir.is_dir():
        for agent in agents_dir.iterdir():
            if not agent.is_dir():
                continue
            for cb_dir in sorted(agent.glob("*_callbacks")):
                if cb_dir.is_dir():
                    for cb in cb_dir.iterdir():
                        if cb.is_dir():
                            py_file = cb / "python_code.py"
                            if py_file.is_file():
                                entrypoints.append(py_file)

    tools_dir = app_dir / "tools"
    if tools_dir.is_dir():
        for tool in tools_dir.iterdir():
            if tool.is_dir():
                py_file = tool / "python_function" / "python_code.py"
                if py_file.is_file():
                    entrypoints.append(py_file)

    results = {}
    MARKER_BEGIN_REGEX = re.compile(
        r"^# === BEGIN CXAS_SHARED_BUNDLE \(sha256:[a-f0-9]{16}\) ===$.*?^# === END CXAS_SHARED_BUNDLE ===\n?",  # noqa: E501
        re.MULTILINE | re.DOTALL,
    )

    for ep in entrypoints:
        content = ep.read_text(encoding="utf-8")
        content = MARKER_BEGIN_REGEX.sub("", content)

        if native_python_env:
            ep.write_text(content, encoding="utf-8")
            results[str(ep)] = 0
            continue

        deps = _get_module_dependencies("entrypoint", content)
        closure = set()
        queue = list(deps)
        while queue:
            curr = queue.pop(0)
            if curr in available_modules and curr not in closure:
                closure.add(curr)
                parts = curr.split(".")
                for i in range(1, len(parts)):
                    p = ".".join(parts[:i])
                    if p in available_modules and p not in closure:
                        queue.append(p)
                mod_deps = _get_module_dependencies(
                    curr, available_modules[curr]
                )
                queue.extend(d for d in mod_deps if d not in closure)

        if not closure:
            ep.write_text(content, encoding="utf-8")
            results[str(ep)] = 0
            continue

        subset_modules = {m: available_modules[m] for m in closure}
        sorted_modules = _topo_sort_modules(subset_modules)

        hasher = hashlib.sha256()
        for m, src in sorted_modules:
            hasher.update(m.encode("utf-8"))
            hasher.update(b"\0")
            hasher.update(src.encode("utf-8"))
            hasher.update(b"\0")
        digest = hasher.hexdigest()[:16]

        bundle_lines = [
            f"# === BEGIN CXAS_SHARED_BUNDLE (sha256:{digest}) ===",
            "import sys as _cxas_sys, types as _cxas_types",
            f'_CXAS_DIGEST = "{digest}"',
        ]

        for m, src in sorted_modules:
            parts = m.split(".")
            bundle_lines.append(
                f'if getattr(_cxas_sys.modules.get("{m}"), "__cxas_digest__", None) != _CXAS_DIGEST:'  # noqa: E501
            )

            if len(parts) > 1:
                parent = ".".join(parts[:-1])
                bundle_lines.append(
                    f'    _cxas_pkg = _cxas_sys.modules.setdefault("{parent}", _cxas_types.ModuleType("{parent}"))'  # noqa: E501
                )
            else:
                bundle_lines.append("    _cxas_pkg = None")

            bundle_lines.append(
                f'    _cxas_mod = _cxas_types.ModuleType("{m}")'
            )
            bundle_lines.append("    _cxas_mod.__cxas_digest__ = _CXAS_DIGEST")
            bundle_lines.append(f'    _cxas_sys.modules["{m}"] = _cxas_mod')

            src_repr = repr(src)
            filename = m.replace(".", "/") + ".py"
            bundle_lines.append(
                f'    exec(compile({src_repr}, "{filename}", "exec"), _cxas_mod.__dict__)'  # noqa: E501
            )

            if len(parts) > 1:
                bundle_lines.append(
                    f'    setattr(_cxas_pkg, "{parts[-1]}", _cxas_mod)'
                )

        bundle_lines.append("# === END CXAS_SHARED_BUNDLE ===")
        bundle_text = "\n".join(bundle_lines) + "\n"

        if len(bundle_text.encode("utf-8")) > max_bundle_bytes:
            raise BundleSizeError(
                f"Bundle for {ep} exceeded {max_bundle_bytes} bytes"
            )

        lines = content.splitlines()
        insert_idx = 0
        for i, line in enumerate(lines):
            if line.startswith("from __future__ import"):
                insert_idx = i + 1
            elif (
                line.strip()
                and not line.startswith("from __future__ import")
                and not line.startswith("#")
            ):
                break

        new_content = (
            "\n".join(lines[:insert_idx])
            + ("\n" if insert_idx > 0 else "")
            + bundle_text
            + "\n".join(lines[insert_idx:])
        )
        new_content = re.sub(r"\n{3,}", "\n\n", new_content)
        ep.write_text(new_content, encoding="utf-8")
        results[str(ep)] = len(bundle_text.encode("utf-8"))

    return results


def strip_shared_bundles(
    app_dir: Path, *, local_source_dir: Path | None = None, force: bool = False
) -> list[Path]:
    """
    Digest-Checked Strip on Pull.
    """
    if local_source_dir is None:
        local_source_dir = app_dir

    entrypoints = []
    agents_dir = app_dir / "agents"
    if agents_dir.is_dir():
        for agent in agents_dir.iterdir():
            if not agent.is_dir():
                continue
            for cb_dir in sorted(agent.glob("*_callbacks")):
                if cb_dir.is_dir():
                    for cb in cb_dir.iterdir():
                        if cb.is_dir():
                            py_file = cb / "python_code.py"
                            if py_file.is_file():
                                entrypoints.append(py_file)

    tools_dir = app_dir / "tools"
    if tools_dir.is_dir():
        for tool in tools_dir.iterdir():
            if tool.is_dir():
                py_file = tool / "python_function" / "python_code.py"
                if py_file.is_file():
                    entrypoints.append(py_file)

    MARKER_REGEX = re.compile(
        r"^(# === BEGIN CXAS_SHARED_BUNDLE \(sha256:([a-f0-9]{16})\) ===\n.*?^# === END CXAS_SHARED_BUNDLE ===\n?)",  # noqa: E501
        re.MULTILINE | re.DOTALL,
    )

    stripped = []
    for ep in entrypoints:
        content = ep.read_text(encoding="utf-8")
        match = MARKER_REGEX.search(content)
        if not match:
            continue

        bundle_text = match.group(1)
        expected_digest = match.group(2)

        if force:
            new_content = content[: match.start()] + content[match.end() :]
            ep.write_text(new_content, encoding="utf-8")
            stripped.append(ep)
            continue

        parsed_modules = {}
        EXEC_REGEX = re.compile(
            r'exec\(compile\((r?[\'"].*?[\'"]), "(.*?)", "exec"\), _cxas_mod\.__dict__\)',  # noqa: E501
            re.DOTALL,
        )
        for exec_match in EXEC_REGEX.finditer(bundle_text):
            src_repr = exec_match.group(1)
            filename = exec_match.group(2)
            mod_name = filename.replace("/", ".")[:-3]
            if mod_name.endswith(".__init__"):
                mod_name = mod_name[:-9]
            try:
                src = ast.literal_eval(src_repr)
                parsed_modules[mod_name] = src
            except Exception:
                raise BundleDigestMismatchError(
                    f"Tampered source repr in {ep}"
                ) from None

        sorted_modules = _topo_sort_modules(parsed_modules)
        hasher = hashlib.sha256()
        for m, src in sorted_modules:
            hasher.update(m.encode("utf-8"))
            hasher.update(b"\0")
            hasher.update(src.encode("utf-8"))
            hasher.update(b"\0")
        actual_digest = hasher.hexdigest()[:16]

        if actual_digest != expected_digest:
            raise BundleDigestMismatchError(
                f"Block tampered in Console: "
                f"{actual_digest} != {expected_digest}"
            )

        local_drift = False
        plib = local_source_dir / "python_lib"
        if plib.is_dir():
            for m, src in parsed_modules.items():
                p = plib / (m.replace(".", "/") + ".py")
                if not p.is_file():
                    p = plib / m.replace(".", "/") / "__init__.py"
                if p.is_file():
                    local_src = p.read_text(encoding="utf-8")
                    if local_src != src:
                        local_drift = True
                        break

        if local_drift:
            raise BundleDigestMismatchError(
                f"Drift between pulled block and local python_lib for {ep}"
            )

        new_content = content[: match.start()] + content[match.end() :]
        ep.write_text(new_content, encoding="utf-8")
        stripped.append(ep)

    return stripped
