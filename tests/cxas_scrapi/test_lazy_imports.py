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

import ast
import concurrent.futures
import inspect
import statistics
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import cxas_scrapi


@pytest.fixture(autouse=True)
def reset_lazy_import_state():
    """Reset symbol cache and purge resolved symbols from module __dict__."""
    cxas_scrapi._SYMBOL_CACHE = None
    for name in cxas_scrapi.__all__:
        cxas_scrapi.__dict__.pop(name, None)
    initial_keys = set(cxas_scrapi.__dict__.keys())

    yield

    cxas_scrapi._SYMBOL_CACHE = None
    added_keys = set(cxas_scrapi.__dict__.keys()) - initial_keys
    for key in added_keys:
        cxas_scrapi.__dict__.pop(key, None)
    for name in cxas_scrapi.__all__:
        cxas_scrapi.__dict__.pop(name, None)


def test_all_public_symbols_resolve():
    """Assert all 34 public SDK symbols resolve cleanly via __getattr__."""
    for name in cxas_scrapi.__all__:
        symbol = getattr(cxas_scrapi, name)
        assert symbol is not None
        assert name in cxas_scrapi.__dict__


def test_lazy_getattr_attribute_error():
    """Assert AttributeError is raised for non-existent symbols."""
    with pytest.raises(
        AttributeError, match="has no attribute 'NonExistentSymbol'"
    ):
        getattr(cxas_scrapi, "NonExistentSymbol")  # noqa: B009


def test_pep562_dir_completeness():
    """Assert dir() contains all __all__ symbols and dunders without leaking _ helpers."""  # noqa: E501
    res = dir(cxas_scrapi)
    for name in cxas_scrapi.__all__:
        assert name in res
    assert "__name__" in res
    assert "__doc__" in res
    assert "_discover_exports" not in res
    assert "_SYMBOL_CACHE" not in res
    assert "_ast" not in res


def test_3way_type_checking_all_ast_identity():
    """Assert 3-way identity between __all__, _discover_exports(), and independent AST parse (keys AND targets)."""  # noqa: E501
    assert len(cxas_scrapi.__all__) == len(set(cxas_scrapi.__all__))
    assert cxas_scrapi.__all__ == sorted(cxas_scrapi.__all__)

    exports = cxas_scrapi._discover_exports()
    assert set(cxas_scrapi.__all__) == set(exports.keys())

    # Independent AST parse of __init__.py verifying both keys and target module tuples  # noqa: E501
    source = inspect.getsource(cxas_scrapi)
    tree = ast.parse(source)
    independent_exports = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            is_tc = (
                isinstance(node.test, ast.Name)
                and node.test.id in ("TYPE_CHECKING", "_TYPE_CHECKING")
            ) or (
                isinstance(node.test, ast.Attribute)
                and node.test.attr in ("TYPE_CHECKING", "_TYPE_CHECKING")
            )
            if is_tc:
                for stmt in node.body:
                    for sub_node in ast.walk(stmt):
                        if isinstance(sub_node, ast.ImportFrom):
                            if sub_node.level == 0:
                                base_mod = sub_node.module or ""
                            elif sub_node.module:
                                base_mod = (
                                    f"cxas_scrapi.{sub_node.module.lstrip('.')}"
                                )
                            else:
                                base_mod = "cxas_scrapi"
                            for alias in sub_node.names:
                                export_name = alias.asname or alias.name
                                if base_mod == "cxas_scrapi":
                                    independent_exports[export_name] = (
                                        f"cxas_scrapi.{alias.name}",
                                        None,
                                    )
                                else:
                                    independent_exports[export_name] = (
                                        base_mod,
                                        alias.name,
                                    )
                        elif isinstance(sub_node, ast.Import):
                            for alias in sub_node.names:
                                export_name = (
                                    alias.asname or alias.name.split(".")[-1]
                                )
                                independent_exports[export_name] = (
                                    alias.name,
                                    None,
                                )

    assert exports == independent_exports


def test_multithreaded_cold_cache_initialization():
    """Assert thread-safe concurrent initialization of _SYMBOL_CACHE and __dir__ under barrier sync."""  # noqa: E501
    barrier = threading.Barrier(8)

    def resolve_symbol_and_dir(name):
        barrier.wait(timeout=5.0)
        _ = dir(cxas_scrapi)
        return getattr(cxas_scrapi, name)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(resolve_symbol_and_dir, name)
            for name in cxas_scrapi.__all__[:8]
        ]
        results = [f.result() for f in futures]
        assert len(results) == 8
        assert all(r is not None for r in results)


def test_cold_discovery_latency_benchmark():
    """Assert median cold discovery latency across 5 runs is strictly < 5ms with warmup pass."""  # noqa: E501
    # Warmup pass
    cxas_scrapi._SYMBOL_CACHE = None
    _ = cxas_scrapi._discover_exports()

    timings = []
    for _ in range(5):
        cxas_scrapi._SYMBOL_CACHE = None
        for name in cxas_scrapi.__all__:
            cxas_scrapi.__dict__.pop(name, None)
        t0 = time.perf_counter()
        _ = cxas_scrapi._discover_exports()
        t1 = time.perf_counter()
        timings.append((t1 - t0) * 1000)

    med_latency = statistics.median(timings)
    assert med_latency < 5.0, (
        f"Cold discovery latency too high: {med_latency:.2f}ms"
    )


def test_source_resolution_fallback_success():
    """Assert fallback to _inspect.getsource when _pkgutil.get_data returns None."""  # noqa: E501
    with patch("pkgutil.get_data", return_value=None):
        cxas_scrapi._SYMBOL_CACHE = None
        exports = cxas_scrapi._discover_exports()
        assert len(exports) == 34


def test_source_resolution_fallback_exception():
    """Assert fallback to _inspect.getsource when _pkgutil.get_data raises OSError."""  # noqa: E501
    with patch("pkgutil.get_data", side_effect=OSError("Resource error")):
        cxas_scrapi._SYMBOL_CACHE = None
        exports = cxas_scrapi._discover_exports()
        assert len(exports) == 34


def test_source_unavailability_error():
    """Assert ImportError is raised when pkgutil, open, and inspect all fail to read source."""  # noqa: E501
    with (
        patch("pkgutil.get_data", return_value=None),
        patch("builtins.open", side_effect=OSError("No file")),
        patch("inspect.getsource", side_effect=OSError("No source")),
    ):
        cxas_scrapi._SYMBOL_CACHE = None
        with pytest.raises(
            ImportError, match="Unable to read package source for AST discovery"
        ):
            cxas_scrapi._discover_exports()


def test_ast_parse_failure_error():
    """Assert ImportError is raised when ast.parse fails."""
    with patch("ast.parse", side_effect=SyntaxError("Bad syntax")):
        cxas_scrapi._SYMBOL_CACHE = None
        with pytest.raises(
            ImportError, match=r"Failed to parse AST in __init__\.py"
        ):
            cxas_scrapi._discover_exports()


def test_ast_aliased_import_mapping():
    """Assert synthetic AST parsing correctly maps aliased imports (asname)."""
    snippet = """
if TYPE_CHECKING:
    from foo.bar import Baz as AliasedBaz
    import alpha.beta as AliasedBeta
"""
    with (
        patch("inspect.getsource", return_value=snippet),
        patch("pkgutil.get_data", return_value=None),
        patch("builtins.open", side_effect=OSError("No file")),
    ):
        cxas_scrapi._SYMBOL_CACHE = None
        exports = cxas_scrapi._discover_exports()
        assert exports["AliasedBaz"] == ("foo.bar", "Baz")
        assert exports["AliasedBeta"] == ("alpha.beta", None)


def test_submodule_fallback_success():
    """Assert submodule attribute fallback succeeds when getattr raises AttributeError but submod_path exists."""  # noqa: E501
    fake_mod = MagicMock(__path__=["/fake"])
    del fake_mod.SubMod  # Ensure getattr raises AttributeError

    fake_submod = MagicMock()

    def mock_import(path):
        if path == "cxas_scrapi.core.agents":
            return fake_mod
        if path == "cxas_scrapi.core.agents.SubMod":
            return fake_submod
        raise ModuleNotFoundError(f"No module named '{path}'", name=path)

    mock_exports = {"SubMod": ("cxas_scrapi.core.agents", "SubMod")}
    with patch.object(
        cxas_scrapi, "_discover_exports", return_value=mock_exports
    ):
        with patch("importlib.import_module", side_effect=mock_import):
            val = getattr(cxas_scrapi, "SubMod")  # noqa: B009
            assert val is fake_submod


def test_submodule_fallback_missing_submodule():
    """Assert AttributeError is re-raised when target submodule itself is missing (mnf.name == submod_path)."""  # noqa: E501
    fake_mod = MagicMock(__path__=["/fake"])
    del fake_mod.MissingSubMod

    def mock_import(path):
        if path == "cxas_scrapi.core.agents":
            return fake_mod
        if path == "cxas_scrapi.core.agents.MissingSubMod":
            raise ModuleNotFoundError(
                "No module named 'cxas_scrapi.core.agents.MissingSubMod'",
                name="cxas_scrapi.core.agents.MissingSubMod",
            )
        raise ModuleNotFoundError(f"No module named '{path}'", name=path)

    mock_exports = {
        "MissingSubMod": ("cxas_scrapi.core.agents", "MissingSubMod")
    }
    with patch.object(
        cxas_scrapi, "_discover_exports", return_value=mock_exports
    ):
        with patch("importlib.import_module", side_effect=mock_import):
            with pytest.raises(AttributeError):
                getattr(cxas_scrapi, "MissingSubMod")  # noqa: B009


def test_submodule_fallback_internal_import_error():
    """Assert internal ModuleNotFoundError (mnf.name != submod_path) is preserved and not masked as AttributeError."""  # noqa: E501
    fake_mod = MagicMock(__path__=["/fake"])
    del fake_mod.BrokenSubMod

    def mock_import(path):
        if path == "cxas_scrapi.core.agents":
            return fake_mod
        if path == "cxas_scrapi.core.agents.BrokenSubMod":
            raise ModuleNotFoundError(
                "No module named 'missing_dep'", name="missing_dep"
            )
        raise ModuleNotFoundError(f"No module named '{path}'", name=path)

    mock_exports = {"BrokenSubMod": ("cxas_scrapi.core.agents", "BrokenSubMod")}
    with patch.object(
        cxas_scrapi, "_discover_exports", return_value=mock_exports
    ):
        with patch("importlib.import_module", side_effect=mock_import):
            with pytest.raises(ModuleNotFoundError, match="missing_dep"):
                getattr(cxas_scrapi, "BrokenSubMod")  # noqa: B009


def test_submodule_fallback_non_package_module():
    """Assert AttributeError is re-raised when mod has no __path__ attribute."""
    fake_mod = object()  # Plain object without __path__

    mock_exports = {"NoPathMod": ("cxas_scrapi.core.agents", "NoPathMod")}
    with patch.object(
        cxas_scrapi, "_discover_exports", return_value=mock_exports
    ):
        with patch("importlib.import_module", return_value=fake_mod):
            with pytest.raises(AttributeError):
                getattr(cxas_scrapi, "NoPathMod")  # noqa: B009
