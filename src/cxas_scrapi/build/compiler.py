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

"""Backwards-compatible re-export shim for ``cxas_scrapi.build.builder``."""

from cxas_scrapi.build.builder import (
    BuildError,
    BuildReport,
    _apply_append_rules_lines,
    _apply_brand_overrides,
    _apply_instruction_override,
    _apply_line_replacements_strict,
    _apply_lock_policy,
    _apply_model_pins,
    _build_identity_block,
    _pruned_reference_warnings,
    _set_model,
    build_all,
    build_app,
    deep_merge,
    validate_modalities,
)

__all__ = [
    "BuildError",
    "BuildReport",
    "_apply_append_rules_lines",
    "_apply_brand_overrides",
    "_apply_instruction_override",
    "_apply_line_replacements_strict",
    "_apply_lock_policy",
    "_apply_model_pins",
    "_build_identity_block",
    "_pruned_reference_warnings",
    "_set_model",
    "build_all",
    "build_app",
    "deep_merge",
    "validate_modalities",
]
