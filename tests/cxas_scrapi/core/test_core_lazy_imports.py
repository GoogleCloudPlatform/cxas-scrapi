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

import types

import pytest

import cxas_scrapi.core as core_mod

CORE_RESOURCE_MAPPINGS = [
    ("agents", "Agents"),
    ("apps", "Apps"),
    ("callbacks", "Callbacks"),
    ("changelogs", "Changelogs"),
    ("common", "Common"),
    ("conversation_history", "ConversationHistory"),
    ("deployments", "Deployments"),
    ("evaluations", "Evaluations"),
    ("guardrails", "Guardrails"),
    ("sessions", "Sessions"),
    ("tools", "Tools"),
    ("variables", "Variables"),
    ("versions", "Versions"),
]


@pytest.mark.parametrize("submod_name,cls_name", CORE_RESOURCE_MAPPINGS)
def test_core_submodule_and_class_lazy_imports(submod_name: str, cls_name: str):
    """Assert core submodules resolve with exact identity."""
    submod_val = getattr(core_mod, submod_name)
    assert isinstance(submod_val, types.ModuleType)
    assert submod_val.__name__ == f"cxas_scrapi.core.{submod_name}"

    cls_val = getattr(core_mod, cls_name)
    assert isinstance(cls_val, type)
    assert cls_val is getattr(submod_val, cls_name)


def test_core_dir_uniqueness_sorting_and_sync():
    """Assert dir(core) is deduplicated and sorted."""
    dir_keys = dir(core_mod)
    assert len(dir_keys) == len(set(dir_keys))
    assert dir_keys == sorted(dir_keys)
    assert set(core_mod.__all__) == set(core_mod._DYNAMIC_IMPORTS.keys())
    assert set(core_mod.__all__).issubset(set(dir_keys))


def test_core_non_existent_attribute_raises_error():
    """Assert non-existent attribute raises AttributeError."""
    pattern = (
        r"module 'cxas_scrapi\.core' has no attribute 'non_existent_core_xyz'"
    )
    with pytest.raises(AttributeError, match=pattern):
        _ = core_mod.non_existent_core_xyz
