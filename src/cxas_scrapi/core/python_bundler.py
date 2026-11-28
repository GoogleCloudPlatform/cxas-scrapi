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

"""Shared Python bundler and pythonEnvFiles.yaml synthesis."""

from __future__ import annotations

import ast
import hashlib
import io
import os
import re
import shutil
import tokenize
from dataclasses import dataclass
from pathlib import Path

import yaml

MAX_BUNDLE_BYTES_PER_FILE = 64 * 1024
MAX_TOTAL_ARCHIVE_BYTES = 32 * 1024 * 1024

BUNDLE_BEGIN_PREFIX = "# === BEGIN CXAS_SHARED_BUNDLE"
BUNDLE_END_PREFIX = "# === END CXAS_SHARED_BUNDLE"
BUNDLE_END_LINE = f"{BUNDLE_END_PREFIX} ==="
BUNDLE_BEGIN_RE = re.compile(
    rf"^{re.escape(BUNDLE_BEGIN_PREFIX)} \(sha256:([a-f0-9]{{16}})\) ===$"
)


class PythonBundlerError(Exception):
    pass


class BundleSizeError(PythonBundlerError):
    pass


class BundleDigestMismatchError(PythonBundlerError):
    pass


def _assert_safe_subpath(path: Path, root: Path, *, label: str) -> None:
    """Reject symlinks and paths resolving outside ``root``."""
    if path.is_symlink():
        raise PythonBundlerError(
            f"Symlinks are not permitted in {label}: {path}"
        )
    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise PythonBundlerError(
            f"Path escapes {label} root ({resolved_root}): {path}"
        )


def _discover_entrypoints(app_dir: Path) -> list[Path]:
    """Return sorted callback and Python tool entrypoint files in app_dir."""
    entrypoints: list[Path] = []
    seen: set[Path] = set()
    agents_dir = app_dir / "agents"
    if agents_dir.is_dir():
        for agent in sorted(agents_dir.iterdir()):
            if not agent.is_dir():
                continue
            for cb_dir in sorted(agent.glob("*_callbacks*")):
                if not cb_dir.is_dir():
                    continue
                direct = cb_dir / "python_code.py"
                if direct.is_file() and direct not in seen:
                    seen.add(direct)
                    entrypoints.append(direct)
                for cb in sorted(cb_dir.iterdir()):
                    if cb.is_dir():
                        py_file = cb / "python_code.py"
                        if py_file.is_file() and py_file not in seen:
                            seen.add(py_file)
                            entrypoints.append(py_file)

    tools_dir = app_dir / "tools"
    if tools_dir.is_dir():
        for tool in sorted(tools_dir.iterdir()):
            if tool.is_dir():
                py_file = tool / "python_function" / "python_code.py"
                if py_file.is_file() and py_file not in seen:
                    seen.add(py_file)
                    entrypoints.append(py_file)

    return entrypoints


def synthesize_python_env_files(
    app_dir: Path, dest_dir: Path | None = None
) -> Path | None:
    """Synthesize pythonEnvFiles/content/ and pythonEnvFiles.yaml."""
    if dest_dir is None:
        dest_dir = app_dir

    python_lib = app_dir / "python_lib"
    if python_lib.is_symlink():
        raise PythonBundlerError(
            f"Symlinks are not permitted in python_lib: {python_lib}"
        )
    if not python_lib.is_dir():
        env_dir = app_dir / "pythonEnvFiles"
        env_content = env_dir / "content"
        content_lib = env_content / "python_lib"
        for candidate in (env_dir, env_content, content_lib):
            if candidate.is_symlink():
                raise PythonBundlerError(
                    f"Symlinks are not permitted in python_lib: {candidate}"
                )
        if not content_lib.is_dir():
            return None
        python_lib = content_lib

    _assert_safe_subpath(python_lib, app_dir, label="python_lib")

    python_env_dir = dest_dir / "pythonEnvFiles"
    content_dir = python_env_dir / "content"
    dest_lib = content_dir / "python_lib"

    if dest_lib.resolve() != python_lib.resolve():
        if dest_lib.is_symlink():
            raise PythonBundlerError(
                f"Symlinks are not permitted in pythonEnvFiles: {dest_lib}"
            )
        if dest_lib.exists():
            shutil.rmtree(dest_lib)
        dest_lib.mkdir(parents=True, exist_ok=True)
        for root, dirs, files in os.walk(python_lib):
            dirs[:] = sorted(
                d for d in dirs if d != "__pycache__" and not d.startswith(".")
            )
            for d in dirs:
                _assert_safe_subpath(
                    Path(root) / d, python_lib, label="python_lib"
                )
            for file in sorted(files):
                if file.endswith(".py") and not file.startswith("."):
                    src = Path(root) / file
                    _assert_safe_subpath(src, python_lib, label="python_lib")
                    rel = src.relative_to(python_lib)
                    dst = dest_lib / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)

    py_files: list[Path] = []
    for root, dirs, files in os.walk(dest_lib):
        dirs[:] = sorted(
            d for d in dirs if d != "__pycache__" and not d.startswith(".")
        )
        for d in dirs:
            _assert_safe_subpath(Path(root) / d, dest_lib, label="python_lib")
        for file in sorted(files):
            if file.endswith(".py") and not file.startswith("."):
                fpath = Path(root) / file
                _assert_safe_subpath(fpath, dest_lib, label="python_lib")
                py_files.append(fpath)
    py_files.sort(key=lambda p: p.relative_to(python_env_dir).as_posix())

    manifest_entries: list[dict[str, str]] = []
    used_ids: set[str] = set()
    for path in py_files:
        rel_content = path.relative_to(content_dir).as_posix()
        rel_env = path.relative_to(python_env_dir).as_posix()
        raw_id = (
            rel_content.replace("/", "-")
            .replace(".py", "")
            .replace("__init__", "init")
            .replace("_", "-")
        )
        clean_id = re.sub(r"[^a-zA-Z0-9-_]", "-", raw_id).strip("-_")
        if len(clean_id) < 5:
            clean_id = f"pyenv-{clean_id}"
        clean_id = clean_id[:36].rstrip("-_")
        if clean_id in used_ids:
            suffix = hashlib.sha256(rel_env.encode("utf-8")).hexdigest()[:6]
            clean_id = f"{clean_id[:29].rstrip('-_')}-{suffix}"
        used_ids.add(clean_id)
        manifest_entries.append(
            {
                "name": clean_id,
                "filePath": rel_env,
            }
        )

    manifest_entries.sort(key=lambda item: item["filePath"])
    python_env_dir.mkdir(parents=True, exist_ok=True)
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


def _is_type_checking_expr(expr: ast.expr) -> bool:
    return (isinstance(expr, ast.Name) and expr.id == "TYPE_CHECKING") or (
        isinstance(expr, ast.Attribute) and expr.attr == "TYPE_CHECKING"
    )


def _is_type_checking_guard(expr: ast.expr) -> bool:
    if _is_type_checking_expr(expr):
        return True
    if isinstance(expr, ast.BoolOp) and isinstance(expr.op, ast.And):
        return any(_is_type_checking_guard(v) for v in expr.values)
    return False


def _is_not_type_checking_expr(expr: ast.expr) -> bool:
    return (
        isinstance(expr, ast.UnaryOp)
        and isinstance(expr.op, ast.Not)
        and _is_type_checking_expr(expr.operand)
    )


def _get_module_dependencies(
    module_name: str, source: str, *, is_package: bool = False
) -> set[str]:
    """Candidate module names imported by `source` (unknown names are ignored).

    `from pkg import name` records `pkg` AND `pkg.name` (a submodule if it
    exists); relative imports are resolved against `module_name`, which is a
    package (`__init__.py`) when `is_package` is true. Imports guarded by
    `if TYPE_CHECKING:` (or `if typing.TYPE_CHECKING:`) are skipped so
    type-only imports are not bundled or executed at runtime.
    """
    deps: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return deps

    class _ImportVisitor(ast.NodeVisitor):
        def visit_If(self, node: ast.If) -> None:
            if _is_type_checking_guard(node.test):
                for stmt in node.orelse:
                    self.visit(stmt)
                return
            if _is_not_type_checking_expr(node.test):
                for stmt in node.body:
                    self.visit(stmt)
                return
            self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                deps.add(alias.name)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if node.level == 0:
                if not node.module:
                    return
                base = node.module.split(".")
            else:
                prefix = _resolve_relative_base(
                    module_name, node.level, is_package=is_package
                )
                if prefix is None:
                    return
                base = [
                    *prefix,
                    *(node.module.split(".") if node.module else []),
                ]
            if base:
                deps.add(".".join(base))
            for alias in node.names:
                if alias.name != "*":
                    deps.add(".".join([*base, alias.name]))

    _ImportVisitor().visit(tree)
    return deps


def _topo_sort_modules(
    modules: dict[str, str], packages: frozenset[str] | set[str] = frozenset()
) -> list[tuple[str, str]]:
    deps = {
        mod: _get_module_dependencies(mod, src, is_package=mod in packages)
        for mod, src in modules.items()
    }
    visited: set[str] = set()
    temp: set[str] = set()
    order: list[str] = []

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


def _module_digest(module_name: str, source: str) -> str:
    hasher = hashlib.sha256()
    hasher.update(module_name.encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(source.encode("utf-8"))
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
        f"{BUNDLE_BEGIN_PREFIX} (sha256:{digest}) ===",
        "import sys as _cxas_sys, types as _cxas_types",
        f'_CXAS_DIGEST = "{digest}"',
        '_CXAS_KEEP = ("__name__", "__package__", "__loader__", "__spec__", "__path__", "__builtins__")',  # noqa: E501
    ]

    ancestor_packages: set[str] = set(packages)
    for m, _ in sorted_modules:
        parts = m.split(".")
        for i in range(1, len(parts)):
            ancestor_packages.add(".".join(parts[:i]))

    ordered_ancestors = sorted(
        ancestor_packages, key=lambda name: (name.count("."), name)
    )
    leaf_modules = [m for m, _ in sorted_modules if m not in ancestor_packages]
    all_stubs = [*ordered_ancestors, *leaf_modules]

    # Phase 1: Pre-register all package/namespace ancestors (with __path__=[])
    # and leaf modules without replacing existing module objects, enabling
    # namespace subpackages, circular imports, and pooled-sandbox sibling
    # preservation.
    for m in all_stubs:
        is_pkg = m in ancestor_packages
        parts = m.split(".")
        pkg_name = m if is_pkg else ".".join(parts[:-1])
        bundle_lines.append(f'_cxas_m = _cxas_sys.modules.get("{m}")')
        bundle_lines.append("if _cxas_m is None:")
        bundle_lines.append(f'    _cxas_m = _cxas_types.ModuleType("{m}")')
        bundle_lines.append(f'    _cxas_sys.modules["{m}"] = _cxas_m')
        if is_pkg:
            bundle_lines.append('if not hasattr(_cxas_m, "__path__"):')
            bundle_lines.append("    _cxas_m.__path__ = []")
        bundle_lines.append(f'_cxas_m.__package__ = "{pkg_name}"')
        if len(parts) > 1:
            parent = ".".join(parts[:-1])
            bundle_lines.append(
                f'setattr(_cxas_sys.modules["{parent}"], "{parts[-1]}", _cxas_m)'  # noqa: E501
            )

    # Phase 2: Execute each bundled module in topological order into its
    # existing namespace, clearing stale attributes when its digest changes
    # (preserving attached child submodules on packages).
    for m, src in sorted_modules:
        is_package = m in packages
        is_pkg = m in ancestor_packages
        mod_digest = _module_digest(m, src)
        filename = _module_filename(m, is_package=is_package)
        bundle_lines.append(
            f'if getattr(_cxas_sys.modules["{m}"], "__cxas_digest__", None) != "{mod_digest}":'  # noqa: E501
        )
        bundle_lines.append(f'    _cxas_mod = _cxas_sys.modules["{m}"]')
        bundle_lines.append("    for _cxas_k in list(_cxas_mod.__dict__):")
        if is_pkg:
            bundle_lines.append("        _cxas_v = _cxas_mod.__dict__[_cxas_k]")
            bundle_lines.append(
                f'        if _cxas_k not in _CXAS_KEEP and not (isinstance(_cxas_v, _cxas_types.ModuleType) and getattr(_cxas_v, "__name__", None) == "{m}." + _cxas_k):'  # noqa: E501
            )
            bundle_lines.append("            del _cxas_mod.__dict__[_cxas_k]")
        else:
            bundle_lines.append("        if _cxas_k not in _CXAS_KEEP:")
            bundle_lines.append("            del _cxas_mod.__dict__[_cxas_k]")
        bundle_lines.append("    _cxas_mod.__doc__ = None")
        bundle_lines.append(
            f'    exec(compile({src!r}, "{filename}", "exec"), _cxas_mod.__dict__)'  # noqa: E501
        )
        bundle_lines.append(f'    _cxas_mod.__cxas_digest__ = "{mod_digest}"')

    bundle_lines.append(BUNDLE_END_LINE)
    return digest, "\n".join(bundle_lines) + "\n"


def _split_for_bundle_insert(content: str) -> tuple[str, str]:
    """Split ``content`` into ``(head, tail)`` right after leading
    ``from __future__`` imports, splitting same-line semicolon statements.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return "", content
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
    if last_future is None or last_future.end_lineno is None:
        return "", content

    lines = content.splitlines(keepends=True)
    line_idx = last_future.end_lineno - 1
    before_lines = "".join(lines[:line_idx])
    stmt_line = lines[line_idx]

    end_col = last_future.end_col_offset
    if end_col is not None:
        stmt_bytes = stmt_line.encode("utf-8")
        prefix_part = stmt_bytes[:end_col].decode("utf-8")
        rest_part = stmt_bytes[end_col:].decode("utf-8")
        stripped_rest = rest_part.lstrip(" \t")
        if stripped_rest.startswith(";"):
            after_semi = stripped_rest[1:].lstrip(" \t")
            after_lines = "".join(lines[line_idx + 1 :])
            if after_semi and not after_semi.startswith(("\n", "\r", "#")):
                return before_lines + prefix_part + "\n", (
                    after_semi + after_lines
                )

    offset = sum(len(line) for line in lines[: last_future.end_lineno])
    head, tail = content[:offset], content[offset:]
    if head and not head.endswith("\n"):
        head += "\n"
    return head, tail


def _discover_shared_modules(
    source_root: Path,
) -> tuple[dict[str, str], dict[str, Path], set[str]]:
    """Discover shared Python modules under ``python_lib/`` safely."""
    search_paths: list[Path] = []
    env_dir = source_root / "pythonEnvFiles"
    env_content = env_dir / "content"
    env_plib = env_content / "python_lib"
    for candidate in (env_dir, env_content, env_plib):
        if candidate.is_symlink():
            raise PythonBundlerError(
                f"Symlinks are not permitted in python_lib: {candidate}"
            )
    if env_content.is_dir():
        search_paths.append(env_content)

    direct_plib = source_root / "python_lib"
    if direct_plib.is_symlink():
        raise PythonBundlerError(
            f"Symlinks are not permitted in python_lib: {direct_plib}"
        )
    if direct_plib.is_dir():
        search_paths.append(source_root)

    available_modules: dict[str, str] = {}
    module_paths: dict[str, Path] = {}
    packages: set[str] = set()
    for sp in search_paths:
        plib = sp / "python_lib"
        if plib.is_symlink():
            raise PythonBundlerError(
                f"Symlinks are not permitted in python_lib: {plib}"
            )
        if not plib.is_dir():
            continue
        _assert_safe_subpath(plib, source_root, label="python_lib")
        for root, dirs, files in os.walk(plib):
            dirs[:] = sorted(
                d for d in dirs if d != "__pycache__" and not d.startswith(".")
            )
            for d in dirs:
                _assert_safe_subpath(Path(root) / d, plib, label="python_lib")
            for file in sorted(files):
                if file.endswith(".py") and not file.startswith("."):
                    path = Path(root) / file
                    _assert_safe_subpath(path, plib, label="python_lib")
                    rel = path.relative_to(sp).as_posix()
                    mod_name = rel.replace("/", ".")[:-3]
                    if mod_name.endswith(".__init__"):
                        mod_name = mod_name[:-9]
                        packages.add(mod_name)
                    try:
                        available_modules[mod_name] = path.read_text(
                            encoding="utf-8"
                        )
                    except UnicodeDecodeError as exc:
                        raise PythonBundlerError(
                            f"Shared module {mod_name} ({path}) is not valid "
                            f"UTF-8: {exc}"
                        ) from exc
                    module_paths[mod_name] = path
    return available_modules, module_paths, packages


def _compute_module_closure(
    entrypoint_source: str,
    available_modules: dict[str, str],
    packages: frozenset[str] | set[str],
) -> set[str]:
    """Return transitive closure of shared modules imported by an entrypoint."""
    deps = _get_module_dependencies("entrypoint", entrypoint_source)
    closure: set[str] = set()
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
    return closure


def bundle_shared_python(
    app_dir: Path,
    *,
    source_root: Path | None = None,
    max_bundle_bytes: int = MAX_BUNDLE_BYTES_PER_FILE,
    native_python_env: bool = False,
) -> dict[str, int]:
    """Tree-shaking AST marker bundler.

    Inserts one `CXAS_SHARED_BUNDLE` block per entrypoint without touching any
    other byte of the entrypoint, so `strip_shared_bundles` restores it exactly.
    Raises `PythonBundlerError` if a bundled shared module or entrypoint is not
    valid UTF-8 Python or contains symlinks.
    """
    if source_root is None:
        source_root = app_dir

    available_modules, module_paths, packages = _discover_shared_modules(
        source_root
    )

    entrypoints = _discover_entrypoints(app_dir)

    validated: set[str] = set()
    results: dict[str, int] = {}

    for ep in entrypoints:
        _assert_safe_subpath(ep, app_dir, label="app_dir")
        try:
            original = ep.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise PythonBundlerError(
                f"Entrypoint {ep} is not valid UTF-8: {exc}"
            ) from exc
        content = _BUNDLE_MARKER_REGEX.sub("", original)

        if native_python_env:
            if content != original:
                ep.write_text(content, encoding="utf-8")
            results[str(ep)] = 0
            continue

        try:
            ast.parse(content, filename=str(ep))
        except SyntaxError as exc:
            raise PythonBundlerError(
                f"Entrypoint {ep} is not valid Python: {exc}"
            ) from exc

        closure = _compute_module_closure(content, available_modules, packages)

        if not closure:
            if content != original:
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

        head, tail = _split_for_bundle_insert(content)
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
        src: str | None = None
        filename: str | None = None
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
        if not ok or src is None or filename is None:
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
            if candidate.is_symlink():
                raise PythonBundlerError(
                    f"Symlinks are not permitted in python_lib: {candidate}"
                )
            if candidate.is_file():
                _assert_safe_subpath(
                    candidate, local_source_dir, label="python_lib"
                )
                try:
                    return candidate.read_text(encoding="utf-8")
                except UnicodeDecodeError as exc:
                    raise PythonBundlerError(
                        f"Shared module {module} ({candidate}) is not valid "
                        f"UTF-8: {exc}"
                    ) from exc
    return None


_MEMORY_ENTRYPOINT = Path("<memory>")
_BUNDLE_MARKER_REGEX = re.compile(
    rf"^({re.escape(BUNDLE_BEGIN_PREFIX)} \(sha256:([a-f0-9]{{16}})\) ===\r?\n.*?^{re.escape(BUNDLE_END_LINE)}(?:\r?\n)?)",  # noqa: E501
    re.MULTILINE | re.DOTALL,
)
_ANY_BUNDLE_MARKER_REGEX = re.compile(
    rf"^(?:{re.escape(BUNDLE_BEGIN_PREFIX)}|{re.escape(BUNDLE_END_PREFIX)})\b",
    re.MULTILINE,
)


def _strip_shared_bundle_impl(
    content: str,
    ep: Path,
    *,
    force: bool = False,
    local_source_dir: Path | None = None,
) -> tuple[str, bool, str | None, dict[str, str], set[str]]:
    matches = list(_BUNDLE_MARKER_REGEX.finditer(content))
    if not matches:
        if not force and _ANY_BUNDLE_MARKER_REGEX.search(content):
            raise BundleDigestMismatchError(
                f"Malformed shared bundle markers in {ep}"
            )
        return content, False, None, {}, set()

    if force:
        new_content = content
        for match in reversed(matches):
            new_content = (
                new_content[: match.start()] + new_content[match.end() :]
            )
        return new_content, True, None, {}, set()

    if len(matches) > 1:
        raise BundleDigestMismatchError(
            f"Block tampered in Console: {len(matches)} shared bundle "
            f"blocks in {ep} (expected 1)"
        )
    match = matches[0]
    outside = content[: match.start()] + content[match.end() :]
    if _ANY_BUNDLE_MARKER_REGEX.search(outside):
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
            f"Block tampered in Console: {actual_digest} != {expected_digest}"
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

    if local_source_dir is not None:
        for m, src in parsed_modules.items():
            local_src = _local_module_source(local_source_dir, m)
            if local_src is not None and local_src != src:
                raise BundleDigestMismatchError(
                    f"Drift between pulled block and local python_lib for "
                    f"{ep} ({m})"
                )

    new_content = content[: match.start()] + content[match.end() :]
    return new_content, True, actual_digest, parsed_modules, packages


def strip_shared_bundle_text(
    content: str,
    ep: Path | str = _MEMORY_ENTRYPOINT,
    *,
    force: bool = False,
) -> str:
    """Validates and strips a `CXAS_SHARED_BUNDLE` block without disk writes."""
    ep_path = ep if isinstance(ep, Path) else Path(ep)
    new_content, _had_bundle, _, _, _ = _strip_shared_bundle_impl(
        content, ep_path, force=force, local_source_dir=None
    )
    return new_content


def inspect_entrypoint_bundle(
    content: str,
    ep: Path | str = _MEMORY_ENTRYPOINT,
    *,
    app_dir: Path | None = None,
    force: bool = False,
) -> tuple[str, str | None, dict[str, str], set[str]]:
    """Inspects an entrypoint's embedded or tree-shaken python_lib closure."""
    ep_path = ep if isinstance(ep, Path) else Path(ep)
    new_content, had_bundle, digest, parsed_modules, packages = (
        _strip_shared_bundle_impl(
            content, ep_path, force=force, local_source_dir=None
        )
    )
    if had_bundle or app_dir is None:
        return new_content, digest, parsed_modules, packages

    try:
        ast.parse(content, filename=str(ep_path))
    except SyntaxError as exc:
        raise PythonBundlerError(
            f"Entrypoint {ep_path} is not valid Python: {exc}"
        ) from exc

    available_modules, module_paths, all_packages = _discover_shared_modules(
        Path(app_dir).resolve()
    )
    if not available_modules:
        return content, None, {}, set()

    closure = _compute_module_closure(content, available_modules, all_packages)
    if not closure:
        return content, None, {}, set()

    for m in sorted(closure):
        try:
            ast.parse(available_modules[m], filename=str(module_paths[m]))
        except SyntaxError as exc:
            raise PythonBundlerError(
                f"Shared module {m} ({module_paths[m]}) is not valid "
                f"Python: {exc}"
            ) from exc

    subset_modules = {m: available_modules[m] for m in closure}
    subset_packages = {m for m in closure if m in all_packages}
    sorted_modules = _topo_sort_modules(subset_modules, subset_packages)
    comp_digest, _bundle_text = _render_bundle(sorted_modules, subset_packages)
    return content, comp_digest, subset_modules, subset_packages


def strip_shared_bundles(
    app_dir: Path, *, local_source_dir: Path | None = None, force: bool = False
) -> list[Path]:
    """Digest-Checked Strip on Pull.

    Without `force`, an entrypoint must contain exactly one bundle block (and no
    stray markers) that is byte-for-byte what `bundle_shared_python` would emit
    for its embedded modules (CRLF-normalized; same digest, AST and text,
    including comments), and no embedded module may have drifted from
    `local_source_dir`; otherwise `BundleDigestMismatchError` is raised and the
    file is untouched. With `force`, every bundle block is removed unchecked.
    """
    if local_source_dir is None:
        local_source_dir = app_dir

    entrypoints = _discover_entrypoints(app_dir)

    stripped: list[Path] = []
    for ep in entrypoints:
        try:
            content = ep.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise PythonBundlerError(
                f"Entrypoint {ep} is not valid UTF-8: {exc}"
            ) from exc
        new_content, had_bundle, _, _, _ = _strip_shared_bundle_impl(
            content,
            ep,
            force=force,
            local_source_dir=local_source_dir,
        )
        if had_bundle:
            ep.write_text(new_content, encoding="utf-8")
            stripped.append(ep)

    return stripped


def _normalize_block(text: str) -> str:
    return text.replace("\r\n", "\n").strip("\r\n")


@dataclass(frozen=True)
class SharedBundleInspection:
    """Result of inspecting an entrypoint for a ``CXAS_SHARED_BUNDLE``."""

    has_bundle: bool
    bundled_modules: frozenset[str] = frozenset()
    ancestor_packages: frozenset[str] = frozenset()
    error: str | None = None


def _extract_top_level_bundle_markers(content: str) -> list[tuple[int, str]]:
    """Return ``[(lineno, text), ...]`` for column-0 bundle marker comments.

    Uses Python's tokenizer so markers inside string literals or docstrings
    (``tokenize.STRING``) and indented comments (``col > 0``) are ignored.
    Falls back to column-0 line scanning if tokenization fails on malformed
    syntax.
    """
    markers: list[tuple[int, str]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(content).readline):
            if tok.type != tokenize.COMMENT or tok.start[1] != 0:
                continue
            text = tok.string.rstrip("\r\n")
            if text.startswith(BUNDLE_BEGIN_PREFIX) or text.startswith(
                BUNDLE_END_PREFIX
            ):
                markers.append((tok.start[0], text))
        return markers
    except (tokenize.TokenError, IndentationError):
        fallback: list[tuple[int, str]] = []
        for lineno, raw_line in enumerate(content.splitlines(), 1):
            if raw_line[:1].isspace():
                continue
            text = raw_line.rstrip("\r\n")
            if text.startswith(BUNDLE_BEGIN_PREFIX) or text.startswith(
                BUNDLE_END_PREFIX
            ):
                fallback.append((lineno, text))
        return fallback


def inspect_shared_bundle(content: str, ep: Path) -> SharedBundleInspection:
    """Validate an entrypoint's ``CXAS_SHARED_BUNDLE`` block, if present.

    Checks column-0 comment markers (ignoring strings/docstrings), strict
    ``(sha256:<16-hex>)`` header syntax, exact digest/AST/byte match against
    ``_render_bundle``, and returns the embedded module and ancestor package
    sets.
    """
    markers = _extract_top_level_bundle_markers(content)
    if not markers:
        return SharedBundleInspection(has_bundle=False)

    if len(markers) != 2:  # noqa: PLR2004
        return SharedBundleInspection(
            has_bundle=True,
            error=(
                "Malformed, truncated, or duplicate CXAS_SHARED_BUNDLE "
                "marker pair."
            ),
        )

    (begin_line, begin_text), (end_line, end_text) = markers
    begin_match = BUNDLE_BEGIN_RE.match(begin_text)
    if begin_match is None:
        return SharedBundleInspection(
            has_bundle=True,
            error=(
                "Malformed CXAS_SHARED_BUNDLE BEGIN marker (expected "
                "'# === BEGIN CXAS_SHARED_BUNDLE (sha256:<16-hex>) ===')."
            ),
        )
    if end_text != BUNDLE_END_LINE:
        return SharedBundleInspection(
            has_bundle=True,
            error="Malformed or truncated CXAS_SHARED_BUNDLE END marker.",
        )

    lines = content.splitlines(keepends=True)
    bundle_text = "".join(lines[begin_line - 1 : end_line])
    expected_digest = begin_match.group(1)

    try:
        parsed_modules, packages, tree = _parse_bundled_modules(bundle_text, ep)
    except BundleDigestMismatchError as exc:
        return SharedBundleInspection(
            has_bundle=True,
            error=f"Invalid CXAS_SHARED_BUNDLE block: {exc}",
        )

    sorted_modules = _topo_sort_modules(parsed_modules, packages)
    actual_digest, expected_text = _render_bundle(sorted_modules, packages)

    if actual_digest != expected_digest:
        return SharedBundleInspection(
            has_bundle=True,
            error=(
                "Tampered CXAS_SHARED_BUNDLE digest mismatch "
                f"(header={expected_digest}, computed={actual_digest})."
            ),
        )
    if ast.dump(ast.parse(expected_text)) != ast.dump(tree):
        return SharedBundleInspection(
            has_bundle=True,
            error="Tampered CXAS_SHARED_BUNDLE block: unexpected statements.",
        )
    if _normalize_block(bundle_text) != _normalize_block(expected_text):
        return SharedBundleInspection(
            has_bundle=True,
            error="Tampered CXAS_SHARED_BUNDLE block: unexpected content.",
        )

    ancestor_packages: set[str] = set(packages)
    for mod in parsed_modules:
        parts = mod.split(".")
        for i in range(1, len(parts)):
            ancestor_packages.add(".".join(parts[:i]))

    return SharedBundleInspection(
        has_bundle=True,
        bundled_modules=frozenset(parsed_modules),
        ancestor_packages=frozenset(ancestor_packages),
    )
