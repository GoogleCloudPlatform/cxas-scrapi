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

import os
import subprocess
import sys
import types

import pytest

import cxas_scrapi as pkg_mod

TOP_LEVEL_SUBMODULES = ["core", "evals", "migration", "utils"]

TOP_LEVEL_CLASSES = [
    ("Agents", "cxas_scrapi.core.agents"),
    ("Apps", "cxas_scrapi.core.apps"),
    ("BaseDFCXClient", "cxas_scrapi.migration.dfcx_exporter"),
    ("ConversationalAgentsAPI", "cxas_scrapi.migration.dfcx_exporter"),
    ("DFCXAgents", "cxas_scrapi.migration.dfcx_exporter"),
    ("EvalUtils", "cxas_scrapi.utils.eval_utils"),
]


@pytest.mark.parametrize("submod_name", TOP_LEVEL_SUBMODULES)
def test_top_level_submodule_lazy_imports(submod_name: str):
    """Assert top-level package submodules resolve as module objects."""
    submod_val = getattr(pkg_mod, submod_name)
    assert isinstance(submod_val, types.ModuleType)
    assert submod_val.__name__ == f"cxas_scrapi.{submod_name}"


@pytest.mark.parametrize("cls_name,expected_mod_path", TOP_LEVEL_CLASSES)
def test_top_level_class_lazy_imports(cls_name: str, expected_mod_path: str):
    """Assert top-level classes resolve as type objects."""
    cls_val = getattr(pkg_mod, cls_name)
    assert isinstance(cls_val, type)
    expected_mod = sys.modules[expected_mod_path]
    assert cls_val is getattr(expected_mod, cls_name)


def test_top_level_dir_uniqueness_sorting_and_sync():
    """Assert dir(cxas_scrapi) is deduplicated, sorted, and synced."""
    dir_keys = dir(pkg_mod)
    assert len(dir_keys) == len(set(dir_keys))
    assert dir_keys == sorted(dir_keys)
    assert set(pkg_mod.__all__) == set(pkg_mod._DYNAMIC_IMPORTS.keys())
    assert set(pkg_mod.__all__).issubset(set(dir_keys))


def test_package_non_existent_attribute_raises_error():
    """Assert non-existent attribute lookup on pkg_mod raises AttributeError."""
    pattern = r"module 'cxas_scrapi' has no attribute 'non_existent_pkg_xyz'"
    with pytest.raises(AttributeError, match=pattern):
        _ = pkg_mod.non_existent_pkg_xyz


def test_package_cold_start_latency():
    """Assert package import cold-start latency is < 200ms in subprocess."""
    script = (
        "import sys, time; "
        "t0 = time.perf_counter(); "
        "import cxas_scrapi; "
        "elapsed = time.perf_counter() - t0; "
        "assert elapsed < 0.200, f'Cold start took {elapsed*1000:.2f}ms'; "
        "print(f'{elapsed*1000:.2f}ms OK')"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    res = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"Cold start test failed: {res.stderr}"
    assert "OK" in res.stdout
