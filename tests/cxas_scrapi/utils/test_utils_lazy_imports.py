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

import cxas_scrapi.utils as utils_mod

SUBMODULE_MAPPINGS = [
    ("changelog_utils", "ChangelogUtils"),
    ("eval_utils", "EvalUtils"),
    ("gcs_utils", "GCSUtils"),
    ("google_sheets_utils", "GoogleSheetsUtils"),
    ("rate_limiter", "RateLimiter"),
    ("secret_manager_utils", "SecretManagerUtils"),
]


@pytest.mark.parametrize("submod_name,cls_name", SUBMODULE_MAPPINGS)
def test_utils_submodule_and_class_lazy_imports(
    submod_name: str, cls_name: str
):
    """Assert submodules and classes resolve cleanly."""
    submod_val = getattr(utils_mod, submod_name)
    assert isinstance(submod_val, types.ModuleType)
    assert submod_val.__name__ == f"cxas_scrapi.utils.{submod_name}"

    cls_val = getattr(utils_mod, cls_name)
    assert isinstance(cls_val, type)
    assert cls_val is getattr(submod_val, cls_name)


def test_chained_submodule_attribute_access():
    """Assert chained submodule attribute access works."""
    import cxas_scrapi.utils  # noqa: PLC0415

    assert (
        cxas_scrapi.utils.eval_utils.COMBINED_REPORT_FILENAME
        == "combined_report.html"
    )
    assert (
        cxas_scrapi.utils.eval_utils.COMBINED_REPORT_JSON_FILENAME
        == "combined_report.json"
    )


def test_dir_uniqueness_sorting_and_sync():
    """Assert dir(utils) is deduplicated, sorted, and synced."""
    dir_keys = dir(utils_mod)
    assert len(dir_keys) == len(set(dir_keys))
    assert dir_keys == sorted(dir_keys)
    assert set(utils_mod.__all__) == set(utils_mod._DYNAMIC_IMPORTS.keys())
    assert set(utils_mod.__all__).issubset(set(dir_keys))


def test_constant_parity_across_modules():
    """Assert constant values match across modules."""
    import cxas_scrapi.cli.main as main_mod  # noqa: PLC0415
    import cxas_scrapi.utils.eval_utils as eval_utils_mod  # noqa: PLC0415
    import cxas_scrapi.utils.reporting as reporting_mod  # noqa: PLC0415

    assert (
        main_mod.COMBINED_REPORT_FILENAME
        == eval_utils_mod.COMBINED_REPORT_FILENAME
    )
    assert (
        main_mod.COMBINED_REPORT_JSON_FILENAME
        == eval_utils_mod.COMBINED_REPORT_JSON_FILENAME
    )
    assert (
        main_mod.COMBINED_REPORT_FILENAME
        == reporting_mod.eval_utils.COMBINED_REPORT_FILENAME
    )
    assert (
        main_mod.COMBINED_REPORT_JSON_FILENAME
        == reporting_mod.eval_utils.COMBINED_REPORT_JSON_FILENAME
    )


def test_non_existent_attribute_raises_error():
    """Assert non-existent attribute lookup raises AttributeError."""
    pattern = (
        r"module 'cxas_scrapi\.utils' has no attribute"
        r" 'non_existent_attribute_xyz'"
    )
    with pytest.raises(
        AttributeError,
        match=pattern,
    ):
        _ = utils_mod.non_existent_attribute_xyz
