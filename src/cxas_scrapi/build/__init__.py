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

"""Profile-Based Build package (``cxas build``).

The ``build`` package turns one shared ``core/`` app plus a set of
Pydantic-validated YAML manifests (profile identity, channel policy,
environment templates, locale content blocks) into one built CES app per
``profile x channel`` (where a profile can represent a brand, subsidiary,
region, business unit, or modality). The build pipeline is fully
deterministic — no LLM, no timestamps, and no runtime Python code is
generated.
"""

from cxas_scrapi.build.builder import (
    BuildError,
    BuildReport,
    build_all,
    build_app,
    merge_app_config,
)
from cxas_scrapi.build.bundle_io import (
    BuildGateError,
    DriftGateError,
    DriftReport,
    NormalizedResource,
    ResourceDrift,
)
from cxas_scrapi.build.drift import (
    check_drift,
    enforce_pre_push_gate,
    extract_normalized_resources,
    normalize_json_obj,
    write_lockfile,
)
from cxas_scrapi.build.manifests import (
    APP_CONFIG_FILENAMES,
    COMPOSITION_MODES,
    FIELD_ENV_APP,
    FORBIDDEN_APP_KEY_PAIRS,
    FORBIDDEN_APP_KEYS,
    AgentChannelOverride,
    AgentOverride,
    BrandOverrides,
    ChannelCapabilities,
    ChannelPolicy,
    CompositionMode,
    ContentBlockCatalog,
    EnvManifest,
    GlobalInstructionOverride,
    InstructionOverride,
    LineReplacement,
    ManifestError,
    Manifests,
    ModelPins,
    ProfileIdentity,
    ToolChannelOverride,
    ToolOverride,
    TtsVoice,
    load_manifests,
    validate_app_overlay_mapping,
)
from cxas_scrapi.build.overrides import (
    apply_brand_overrides,
    apply_instruction_override,
)

__all__ = [
    "APP_CONFIG_FILENAMES",
    "COMPOSITION_MODES",
    "FIELD_ENV_APP",
    "FORBIDDEN_APP_KEYS",
    "FORBIDDEN_APP_KEY_PAIRS",
    "AgentChannelOverride",
    "AgentOverride",
    "BrandOverrides",
    "BuildError",
    "BuildGateError",
    "BuildReport",
    "ChannelCapabilities",
    "ChannelPolicy",
    "CompositionMode",
    "ContentBlockCatalog",
    "DriftGateError",
    "DriftReport",
    "EnvManifest",
    "GlobalInstructionOverride",
    "InstructionOverride",
    "LineReplacement",
    "ManifestError",
    "Manifests",
    "ModelPins",
    "NormalizedResource",
    "ProfileIdentity",
    "ResourceDrift",
    "ToolChannelOverride",
    "ToolOverride",
    "TtsVoice",
    "apply_brand_overrides",
    "apply_instruction_override",
    "build_all",
    "build_app",
    "check_drift",
    "enforce_pre_push_gate",
    "extract_normalized_resources",
    "load_manifests",
    "merge_app_config",
    "normalize_json_obj",
    "validate_app_overlay_mapping",
    "write_lockfile",
]
