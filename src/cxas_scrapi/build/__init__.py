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

from cxas_scrapi.build.compiler import BuildError, BuildReport, build_app
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
    "BrandIdentity",
    "BuildError",
    "BuildReport",
    "ChannelCapabilities",
    "ChannelPolicy",
    "ContentBlockCatalog",
    "EnvManifest",
    "Manifests",
    "TtsVoice",
    "build_app",
    "load_manifests",
]
