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
)
from cxas_scrapi.build.manifests import (
    ChannelCapabilities,
    ChannelPolicy,
    ContentBlockCatalog,
    EnvManifest,
    ManifestError,
    Manifests,
    ProfileIdentity,
    TtsVoice,
    load_manifests,
)

__all__ = [
    "BuildError",
    "BuildReport",
    "ChannelCapabilities",
    "ChannelPolicy",
    "ContentBlockCatalog",
    "EnvManifest",
    "ManifestError",
    "Manifests",
    "ProfileIdentity",
    "TtsVoice",
    "build_all",
    "build_app",
    "load_manifests",
]
