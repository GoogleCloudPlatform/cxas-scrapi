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

"""Declarative brand/channel identity compiler (``cxas build``).

The ``build`` package turns one shared ``core/`` app plus a set of
Pydantic-validated YAML manifests (brand identity, channel policy,
environment templates, locale content blocks) into one compiled CES app
per ``brand x channel``. The compiler is fully deterministic — no LLM, no
timestamps, no runtime Python is generated.
"""

from cxas_scrapi.build.backport import (
    BackportChange,
    BackportResult,
    BackportScope,
    BackportTarget,
    backport_live_to_source,
    format_backport_pr_body,
)
from cxas_scrapi.build.bundle_io import (
    BuildGateError,
    DriftGateError,
    DriftReport,
    NormalizedResource,
    ResourceDrift,
)
from cxas_scrapi.build.compiler import (
    BuildError,
    BuildReport,
    build_all,
    build_app,
)
from cxas_scrapi.build.drift import (
    check_drift,
    enforce_pre_push_gate,
    extract_normalized_resources,
    normalize_json_obj,
    write_lockfile,
)
from cxas_scrapi.build.manifests import (
    AgentChannelOverride,
    AgentOverride,
    BrandIdentity,
    BrandOverrides,
    ChannelCapabilities,
    ChannelPolicy,
    ContentBlockCatalog,
    EnvManifest,
    GlobalInstructionOverride,
    InstructionOverride,
    LineReplacement,
    ManifestError,
    Manifests,
    ModelPins,
    ToolChannelOverride,
    ToolOverride,
    TtsVoice,
    load_manifests,
)

__all__ = [
    "AgentChannelOverride",
    "AgentOverride",
    "BackportChange",
    "BackportResult",
    "BackportScope",
    "BackportTarget",
    "BrandIdentity",
    "BrandOverrides",
    "BuildError",
    "BuildGateError",
    "BuildReport",
    "ChannelCapabilities",
    "ChannelPolicy",
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
    "ResourceDrift",
    "ToolChannelOverride",
    "ToolOverride",
    "TtsVoice",
    "backport_live_to_source",
    "build_all",
    "build_app",
    "check_drift",
    "enforce_pre_push_gate",
    "extract_normalized_resources",
    "format_backport_pr_body",
    "load_manifests",
    "normalize_json_obj",
    "write_lockfile",
]
