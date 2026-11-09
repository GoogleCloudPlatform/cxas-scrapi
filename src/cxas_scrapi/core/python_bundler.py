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


def _resolve_relative_base(
    module_name: str, level: int, *, is_package: bool
) -> list[str] | None:
    """Package parts that a `from <level dots> ...` import is relative to."""
    parts = module_name.split(".")
    pkg_parts = parts if is_package else parts[:-1]
    drop = level - 1
    if drop > len(pkg_parts):
        return None
    return pkg_parts[: len(pkg_parts) - drop]


def _get_module_dependencies(
    module_name: str, source: str, *, is_package: bool = False
) -> set[str]:
    """Candidate module names imported by `source` (unknown names are ignored).

    `from pkg import name` records `pkg` AND `pkg.name` (a submodule if it
    exists); relative imports are resolved against `module_name`, which is a
    package (`__init__.py`) when `is_package` is true.
    """
    deps: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return deps

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                deps.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if not node.module:
                    continue
                base = node.module.split(".")
            else:
                prefix = _resolve_relative_base(
                    module_name, node.level, is_package=is_package
                )
                if prefix is None:
                    continue
                base = [
                    *prefix,
                    *(node.module.split(".") if node.module else []),
                ]
            if base:
                deps.add(".".join(base))
            for alias in node.names:
                if alias.name != "*":
                    deps.add(".".join([*base, alias.name]))
    return deps


def _topo_sort_modules(
    modules: dict[str, str], packages: frozenset[str] | set[str] = frozenset()
) -> list[tuple[str, str]]:
    deps = {
        mod: _get_module_dependencies(mod, src, is_package=mod in packages)
        for mod, src in modules.items()
    }
    visited = set()
    temp = set()
    order = []

    def visit(n: str) -> None:
        if n in temp:
            return
        if n not in visited:
            temp.add(n)
            for d in sorted(deps.get(n, set())):
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


def _bundle_digest(sorted_modules: list[tuple[str, str]]) -> str:
    hasher = hashlib.sha256()
    for m, src in sorted_modules:
        hasher.update(m.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(src.encode("utf-8"))
        hasher.update(b"\0")
    return hasher.hexdigest()[:16]


def _module_filename(module_name: str, *, is_package: bool) -> str:
    base = module_name.replace(".", "/")
    return f"{base}/__init__.py" if is_package else f"{base}.py"


def _render_bundle(
    sorted_modules: list[tuple[str, str]], packages: frozenset[str] | set[str]
) -> tuple[str, str]:
    """Returns `(digest, bundle_text)` for a topologically sorted module list.

    Single source of truth for the bundle format: `bundle_shared_python` emits
    it and `strip_shared_bundles` re-renders it to verify a pulled block.
    """
    digest = _bundle_digest(sorted_modules)
    bundle_lines = [
        f"# === BEGIN CXAS_SHARED_BUNDLE (sha256:{digest}) ===",
        "import sys as _cxas_sys, types as _cxas_types",
        f'_CXAS_DIGEST = "{digest}"',
    ]

    for m, src in sorted_modules:
        is_package = m in packages
        parts = m.split(".")
        package = m if is_package else ".".join(parts[:-1])
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

        bundle_lines.append(f'    _cxas_mod = _cxas_types.ModuleType("{m}")')
        bundle_lines.append("    _cxas_mod.__cxas_digest__ = _CXAS_DIGEST")
        bundle_lines.append(f'    _cxas_mod.__package__ = "{package}"')
        bundle_lines.append(f'    _cxas_sys.modules["{m}"] = _cxas_mod')

        filename = _module_filename(m, is_package=is_package)
        bundle_lines.append(
            f'    exec(compile({src!r}, "{filename}", "exec"), _cxas_mod.__dict__)'  # noqa: E501
        )

        if len(parts) > 1:
            bundle_lines.append(
                f'    setattr(_cxas_pkg, "{parts[-1]}", _cxas_mod)'
            )

    bundle_lines.append("# === END CXAS_SHARED_BUNDLE ===")
    return digest, "\n".join(bundle_lines) + "\n"


def _bundle_insert_offset(content: str) -> int:
    """Character offset after the leading `from __future__` imports (else 0)."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return 0
    last_future: ast.stmt | None = None
    for i, node in enumerate(tree.body):
        if (
            i == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            last_future = node
            continue
        break
    if last_future is None:
        return 0
    lines = content.splitlines(keepends=True)
    return sum(len(line) for line in lines[: last_future.end_lineno])


def bundle_shared_python(
    app_dir: Path,
    *,
    source_root: Path | None = None,
    max_bundle_bytes: int = MAX_BUNDLE_BYTES_PER_FILE,
    native_python_env: bool = False,
) -> dict[str, int]:
    """
    Tree-shaking AST marker bundler.

    Inserts one `CXAS_SHARED_BUNDLE` block per entrypoint without touching any
    other byte of the entrypoint, so `strip_shared_bundles` restores it exactly.
    Raises `PythonBundlerError` if a bundled shared module is not valid Python.
    """
    if source_root is None:
        source_root = app_dir

    search_paths = []
    if (source_root / "pythonEnvFiles" / "content").is_dir():
        search_paths.append(source_root / "pythonEnvFiles" / "content")
    if (source_root / "python_lib").is_dir():
        search_paths.append(source_root)

    available_modules: dict[str, str] = {}
    module_paths: dict[str, Path] = {}
    packages: set[str] = set()
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
                        packages.add(mod_name)
                    available_modules[mod_name] = path.read_text(
                        encoding="utf-8"
                    )
                    module_paths[mod_name] = path

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

    validated: set[str] = set()
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
        queue = sorted(deps)
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
                    curr, available_modules[curr], is_package=curr in packages
                )
                queue.extend(sorted(d for d in mod_deps if d not in closure))

        if not closure:
            ep.write_text(content, encoding="utf-8")
            results[str(ep)] = 0
            continue

        for m in sorted(closure - validated):
            try:
                ast.parse(available_modules[m], filename=str(module_paths[m]))
            except SyntaxError as exc:
                raise PythonBundlerError(
                    f"Shared module {m} ({module_paths[m]}) is not valid "
                    f"Python: {exc}"
                ) from exc
            validated.add(m)

        subset_modules = {m: available_modules[m] for m in closure}
        sorted_modules = _topo_sort_modules(subset_modules, packages)
        _digest, bundle_text = _render_bundle(sorted_modules, packages)

        if len(bundle_text.encode("utf-8")) > max_bundle_bytes:
            raise BundleSizeError(
                f"Bundle for {ep} exceeded {max_bundle_bytes} bytes"
            )

        offset = _bundle_insert_offset(content)
        head, tail = content[:offset], content[offset:]
        if head and not head.endswith("\n"):
            head += "\n"
        ep.write_text(head + bundle_text + tail, encoding="utf-8")
        results[str(ep)] = len(bundle_text.encode("utf-8"))

    return results


def _is_exec_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "exec"
    )


def _const_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _parse_bundled_modules(
    bundle_text: str, ep: Path
) -> tuple[dict[str, str], set[str], ast.Module]:
    """Extracts `({module: source}, packages, tree)` from a bundle block.

    Only `exec(compile(<str>, "<path>.py", "exec"), _cxas_mod.__dict__)` calls
    with string constants are accepted; anything else raises
    `BundleDigestMismatchError`. Parsing the AST rather than the text makes the
    extraction independent of the module source contents (`", "`, quotes, raw
    strings, backslashes, multi-line literals, unicode). The caller re-renders
    the block from the extracted modules and compares the full AST, so any
    statement outside this shape (Console edits, modified guards) is rejected.
    """
    try:
        tree = ast.parse(bundle_text)
    except SyntaxError:
        raise BundleDigestMismatchError(
            f"Unparsable shared bundle in {ep}"
        ) from None

    parsed_modules: dict[str, str] = {}
    packages: set[str] = set()
    for node in ast.walk(tree):
        if not _is_exec_call(node):
            continue
        ok = (
            len(node.args) == 2
            and not node.keywords
            and isinstance(node.args[0], ast.Call)
            and isinstance(node.args[0].func, ast.Name)
            and node.args[0].func.id == "compile"
            and len(node.args[0].args) == 3
            and not node.args[0].keywords
            and isinstance(node.args[1], ast.Attribute)
            and node.args[1].attr == "__dict__"
            and isinstance(node.args[1].value, ast.Name)
            and node.args[1].value.id == "_cxas_mod"
        )
        if ok:
            src_node, filename_node, mode_node = node.args[0].args
            src = _const_str(src_node)
            filename = _const_str(filename_node)
            mode = _const_str(mode_node)
            ok = (
                src is not None
                and filename is not None
                and filename.endswith(".py")
                and mode == "exec"
            )
        if not ok:
            raise BundleDigestMismatchError(f"Tampered source repr in {ep}")
        mod_name = filename.replace("/", ".")[:-3]
        if mod_name.endswith(".__init__"):
            mod_name = mod_name[:-9]
            packages.add(mod_name)
        if mod_name in parsed_modules:
            raise BundleDigestMismatchError(
                f"Duplicate bundled module {mod_name!r} in {ep}"
            )
        parsed_modules[mod_name] = src
    if not parsed_modules:
        raise BundleDigestMismatchError(f"Empty shared bundle in {ep}")
    return parsed_modules, packages, tree


def _local_module_source(local_source_dir: Path, module: str) -> str | None:
    """Local source of a bundled module (`python_lib.x` keyed from the root)."""
    rel = module.replace(".", "/")
    for root in (
        local_source_dir,
        local_source_dir / "pythonEnvFiles" / "content",
    ):
        for candidate in (root / f"{rel}.py", root / rel / "__init__.py"):
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8")
    return None


def strip_shared_bundles(
    app_dir: Path, *, local_source_dir: Path | None = None, force: bool = False
) -> list[Path]:
    """
    Digest-Checked Strip on Pull.

    Without `force`, an entrypoint must contain exactly one bundle block (and no
    stray markers) that is byte-for-byte what `bundle_shared_python` would emit
    for its embedded modules (CRLF-normalized; same digest, AST and text,
    including comments), and no embedded module may have drifted from
    `local_source_dir`; otherwise `BundleDigestMismatchError` is raised and the
    file is untouched. With `force`, every bundle block is removed unchecked.
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
        r"^(# === BEGIN CXAS_SHARED_BUNDLE \(sha256:([a-f0-9]{16})\) ===\r?\n.*?^# === END CXAS_SHARED_BUNDLE ===(?:\r?\n)?)",  # noqa: E501
        re.MULTILINE | re.DOTALL,
    )
    ANY_MARKER_REGEX = re.compile(
        r"^# === (?:BEGIN|END) CXAS_SHARED_BUNDLE\b", re.MULTILINE
    )

    stripped = []
    for ep in entrypoints:
        content = ep.read_text(encoding="utf-8")
        matches = list(MARKER_REGEX.finditer(content))
        if not matches:
            if not force and ANY_MARKER_REGEX.search(content):
                raise BundleDigestMismatchError(
                    f"Malformed shared bundle markers in {ep}"
                )
            continue

        if force:
            new_content = content
            for match in reversed(matches):
                new_content = (
                    new_content[: match.start()] + new_content[match.end() :]
                )
            ep.write_text(new_content, encoding="utf-8")
            stripped.append(ep)
            continue

        if len(matches) > 1:
            raise BundleDigestMismatchError(
                f"Block tampered in Console: {len(matches)} shared bundle "
                f"blocks in {ep} (expected 1)"
            )
        match = matches[0]
        outside = content[: match.start()] + content[match.end() :]
        if ANY_MARKER_REGEX.search(outside):
            raise BundleDigestMismatchError(
                f"Block tampered in Console: stray shared bundle marker in {ep}"
            )

        bundle_text = match.group(1)
        expected_digest = match.group(2)

        parsed_modules, packages, tree = _parse_bundled_modules(bundle_text, ep)
        sorted_modules = _topo_sort_modules(parsed_modules, packages)
        actual_digest, expected_text = _render_bundle(sorted_modules, packages)

        if actual_digest != expected_digest:
            raise BundleDigestMismatchError(
                f"Block tampered in Console: "
                f"{actual_digest} != {expected_digest}"
            )
        if ast.dump(ast.parse(expected_text)) != ast.dump(tree):
            raise BundleDigestMismatchError(
                f"Block tampered in Console: unexpected statements in {ep}"
            )
        # Byte-level check (CRLF-normalized): also catches comments and
        # whitespace edits that the AST comparison cannot see.
        if _normalize_block(bundle_text) != _normalize_block(expected_text):
            raise BundleDigestMismatchError(
                f"Block tampered in Console: unexpected content in {ep}"
            )

        for m, src in parsed_modules.items():
            local_src = _local_module_source(local_source_dir, m)
            if local_src is not None and local_src != src:
                raise BundleDigestMismatchError(
                    f"Drift between pulled block and local python_lib for "
                    f"{ep} ({m})"
                )

        new_content = content[: match.start()] + content[match.end() :]
        ep.write_text(new_content, encoding="utf-8")
        stripped.append(ep)

    return stripped


def _normalize_block(text: str) -> str:
    return text.replace("\r\n", "\n").strip("\r\n")
