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

"""AST-based lazy import resolution engine using standard ast.NodeVisitor."""

import ast
import importlib
import inspect
import pkgutil
import sys
from typing import Any


class _TypeCheckingImportVisitor(ast.NodeVisitor):
    """AST visitor that extracts import mappings inside
    if TYPE_CHECKING: blocks.
    """

    def __init__(self, mod_name: str):
        self.mod_name = mod_name
        self.exports: dict[str, tuple[str, str | None]] = {}
        self._in_tc = False

    def visit_If(self, node: ast.If) -> None:
        if self._is_type_checking(node.test):
            old_state = self._in_tc
            self._in_tc = True
            for stmt in node.body:
                self.visit(stmt)
            self._in_tc = old_state

    def visit_Import(self, node: ast.Import) -> None:
        if not self._in_tc:
            return
        for alias in node.names:
            export_name = alias.asname or alias.name.split(".")[-1]
            self.exports[export_name] = (alias.name, None)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if not self._in_tc:
            return
        base_mod = self._resolve_base_module(node)
        for alias in node.names:
            export_name = alias.asname or alias.name
            if base_mod in ("cxas_scrapi", self.mod_name):
                self.exports[export_name] = (
                    f"{self.mod_name}.{alias.name}",
                    None,
                )
            else:
                self.exports[export_name] = (base_mod, alias.name)

    def _is_type_checking(self, test: ast.AST) -> bool:
        if isinstance(test, ast.Name) and test.id in (
            "TYPE_CHECKING",
            "_TYPE_CHECKING",
        ):
            return True
        if isinstance(test, ast.Attribute) and test.attr in (
            "TYPE_CHECKING",
            "_TYPE_CHECKING",
        ):
            return True
        return False

    def _resolve_base_module(self, node: ast.ImportFrom) -> str:
        if node.level == 0:
            return node.module or ""
        if node.module:
            submod = node.module.lstrip(".")
            return f"{self.mod_name}.{submod}"
        return self.mod_name


def _read_package_source(pkg_name: str, mod_name: str) -> str:
    """Read source code for a package module across pkgutil, file,
    and inspect fallbacks.
    """
    try:
        source_bytes = pkgutil.get_data(pkg_name, "__init__.py")
        if source_bytes:
            return source_bytes.decode("utf-8")
    except Exception:
        pass

    try:
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, "__file__") and mod.__file__:
            with open(mod.__file__, encoding="utf-8") as f:
                return f.read()
    except Exception:
        pass

    try:
        mod = sys.modules.get(mod_name)
        if mod:
            return inspect.getsource(mod)
    except Exception:
        pass

    raise ImportError("Unable to read package source for AST discovery")


def _extract_type_checking_imports(
    source: str, mod_name: str
) -> dict[str, tuple[str, str | None]]:
    """Parse AST using _TypeCheckingImportVisitor and return import mapping."""
    try:
        tree = ast.parse(source)
    except Exception as err:
        raise ImportError(f"Failed to parse AST in __init__.py: {err}") from err

    visitor = _TypeCheckingImportVisitor(mod_name)
    visitor.visit(tree)
    return visitor.exports


def lazy_import_attribute(
    name: str,
    exports: dict[str, tuple[str, str | None]],
    pkg_name: str,
    global_ns: dict[str, Any],
) -> Any:
    """Import and return attribute from export mapping,
    updating global_ns cache.
    """
    if name not in exports:
        raise AttributeError(f"module '{pkg_name}' has no attribute '{name}'")

    mod_path, target_attr = exports[name]
    if target_attr is None:
        val = importlib.import_module(mod_path)
    else:
        mod = importlib.import_module(mod_path)
        try:
            val = getattr(mod, target_attr)
        except AttributeError as err:
            if hasattr(mod, "__path__"):
                submod_path = f"{mod_path}.{target_attr}"
                try:
                    val = importlib.import_module(submod_path)
                except ModuleNotFoundError as mnf:
                    if mnf.name == submod_path:
                        raise err from None
                    raise
            else:
                raise

    global_ns[name] = val
    return val
