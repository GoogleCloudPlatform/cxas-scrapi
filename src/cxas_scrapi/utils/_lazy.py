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

"""Helper utility for AST-based single-source lazy import discovery."""

import ast
import importlib
import inspect
import pkgutil
import sys
from typing import Any


def _read_package_source(pkg_name: str, mod_name: str) -> str:
    """Read source code for a package module across pkgutil, file,
    and inspect fallbacks.
    """
    # 1. Try pkgutil (works in zip, PEX, PAR, package bundles)
    try:
        source_bytes = pkgutil.get_data(pkg_name, "__init__.py")
        if source_bytes:
            return source_bytes.decode("utf-8")
    except Exception:
        pass

    # 2. Try file system
    try:
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, "__file__") and mod.__file__:
            with open(mod.__file__, encoding="utf-8") as f:
                return f.read()
    except Exception:
        pass

    # 3. Try inspect
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
    """Parse AST and extract all import mappings inside
    if TYPE_CHECKING: blocks.
    """
    cache: dict[str, tuple[str, str | None]] = {}
    try:
        tree = ast.parse(source)
    except Exception as err:
        raise ImportError(f"Failed to parse AST in __init__.py: {err}") from err

    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue

        # Check if test condition is TYPE_CHECKING or _TYPE_CHECKING
        is_tc = False
        if isinstance(node.test, ast.Name) and node.test.id in (
            "TYPE_CHECKING",
            "_TYPE_CHECKING",
        ):
            is_tc = True
        elif isinstance(node.test, ast.Attribute) and node.test.attr in (
            "TYPE_CHECKING",
            "_TYPE_CHECKING",
        ):
            is_tc = True

        if not is_tc:
            continue

        for stmt in node.body:
            for sub_node in ast.walk(stmt):
                if isinstance(sub_node, ast.Import):
                    for alias in sub_node.names:
                        export_name = alias.asname or alias.name.split(".")[-1]
                        cache[export_name] = (alias.name, None)
                elif isinstance(sub_node, ast.ImportFrom):
                    if sub_node.level == 0:
                        base_mod = sub_node.module or ""
                    elif sub_node.module:
                        submod = sub_node.module.lstrip(".")
                        base_mod = f"{mod_name}.{submod}"
                    else:
                        base_mod = mod_name

                    for alias in sub_node.names:
                        export_name = alias.asname or alias.name
                        if base_mod in ("cxas_scrapi", mod_name):
                            cache[export_name] = (
                                f"{mod_name}.{alias.name}",
                                None,
                            )
                        else:
                            cache[export_name] = (base_mod, alias.name)

    return cache


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
