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

from cxas_scrapi.build.blueprint_compiler import (
    BuildBundleResult,
    apply_runtime_policy_to_bundle,
    apply_tuning_overlay_to_bundle,
    compile_blueprint,
    format_build_table,
    package_bundle_zip,
)
from cxas_scrapi.build.blueprint_schemas import (
    AgentOverlaySpec,
    BlueprintCompilerError,
    BlueprintSpec,
    BrandSpec,
    CompilerError,
    DriftGateError,
    DriftReport,
    FirewallError,
    FirewallViolation,
    GuardrailSpec,
    KillSwitchSpec,
    ModalitySpec,
    ModelPoolsSpec,
    NormalizedResource,
    ResourceDrift,
    RootAgentSpec,
    RuntimePolicySpec,
    SegmentPolicySpec,
    SpecialistSpec,
    SurfacePolicySpec,
    ToolSpec,
    TuningOverlaySpec,
    VariablePropertySpec,
    VariableSpec,
    VoiceSpec,
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
from cxas_scrapi.build.firewall import FirewallProfile, run_firewall
from cxas_scrapi.build.manifests import (
    BrandIdentity,
    ChannelCapabilities,
    ChannelPolicy,
    ContentBlockCatalog,
    EnvManifest,
    Manifests,
    TtsVoice,
    load_manifests,
)

__all__ = [
    "AgentOverlaySpec",
    "BlueprintCompilerError",
    "BlueprintSpec",
    "BrandIdentity",
    "BrandSpec",
    "BuildBundleResult",
    "BuildError",
    "BuildReport",
    "ChannelCapabilities",
    "ChannelPolicy",
    "CompilerError",
    "ContentBlockCatalog",
    "DriftGateError",
    "DriftReport",
    "EnvManifest",
    "FirewallError",
    "FirewallProfile",
    "FirewallViolation",
    "GuardrailSpec",
    "KillSwitchSpec",
    "Manifests",
    "ModalitySpec",
    "ModelPoolsSpec",
    "NormalizedResource",
    "ResourceDrift",
    "RootAgentSpec",
    "RuntimePolicySpec",
    "SegmentPolicySpec",
    "SpecialistSpec",
    "SurfacePolicySpec",
    "ToolSpec",
    "TtsVoice",
    "TuningOverlaySpec",
    "VariablePropertySpec",
    "VariableSpec",
    "VoiceSpec",
    "apply_runtime_policy_to_bundle",
    "apply_tuning_overlay_to_bundle",
    "build_all",
    "build_app",
    "check_drift",
    "compile_blueprint",
    "enforce_pre_push_gate",
    "extract_normalized_resources",
    "format_build_table",
    "load_manifests",
    "normalize_json_obj",
    "package_bundle_zip",
    "run_firewall",
    "write_lockfile",
]
